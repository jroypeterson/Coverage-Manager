"""Tests for the delisted/recycled ticker check, focused on the
price-recency hardening (catches clean acquisitions that keep `.info` stale)."""

from datetime import date

import pandas as pd
import pytest

from universe.delisted_check import (
    DEGRADED_FAILURE_RATE,
    PRICE_FAILED,
    PRICE_NO_DATA,
    PRICE_OK,
    PRICE_STALE_DAYS,
    VERDICT_CLEAN,
    VERDICT_FLAGGED,
    VERDICT_INCONCLUSIVE,
    _classify,
    _price_is_stale,
    _probe_recent_price,
    write_report,
)

TODAY = date(2026, 6, 13)


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """Exercise the backoff logic without paying its wall-clock cost."""
    import universe.delisted_check as dc

    slept = []
    clock = [1000.0]

    def _fake_sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds  # a no-op sleep against a real clock busy-spins

    monkeypatch.setattr(dc, "_sleep", _fake_sleep)
    monkeypatch.setattr(dc, "_now", lambda: clock[0])
    dc._THROTTLE.reset()
    yield slept
    dc._THROTTLE.reset()


def _fresh(days_ago):
    return (date.fromordinal(TODAY.toordinal() - days_ago)).isoformat()


# ── _price_is_stale ─────────────────────────────────────────────────────────

def test_price_stale_when_missing():
    assert _price_is_stale("", today=TODAY) is True
    assert _price_is_stale(None, today=TODAY) is True


def test_price_stale_when_bad_format():
    assert _price_is_stale("not-a-date", today=TODAY) is True


def test_price_fresh_within_window():
    assert _price_is_stale(_fresh(2), today=TODAY) is False
    assert _price_is_stale(_fresh(PRICE_STALE_DAYS), today=TODAY) is False


def test_price_stale_beyond_window():
    assert _price_is_stale(_fresh(PRICE_STALE_DAYS + 1), today=TODAY) is True
    assert _price_is_stale(_fresh(90), today=TODAY) is True


# ── _probe_recent_price (direct, with a fake yfinance Ticker) ───────────────

class _FakeTicker:
    def __init__(self, df=None, raises=False):
        self._df = df
        self._raises = raises
        self.called_with = None

    def history(self, **kwargs):
        self.called_with = kwargs
        if self._raises:
            raise RuntimeError("429 Too Many Requests")
        return self._df


def _hist(dates, tz="America/New_York"):
    idx = pd.to_datetime(dates).tz_localize(tz)
    return pd.DataFrame({"Open": [1.0] * len(dates), "Close": [2.0] * len(dates)}, index=idx)


def test_probe_returns_last_bar_date_tz_aware():
    t = _FakeTicker(_hist(["2026-06-10", "2026-06-11", "2026-06-12"]))
    ran, last = _probe_recent_price(t)
    assert ran == PRICE_OK
    assert last == "2026-06-12"  # exchange-local trading date, not UTC-shifted
    # essential: raise_errors must be passed so 429s don't masquerade as dead feeds
    assert t.called_with.get("raise_errors") is True


def test_probe_empty_frame_is_anomalous_not_authoritative():
    """With `raise_errors=True`, yfinance signals a missing feed by RAISING, so
    a silent empty frame means the error path is not behaving as assumed.

    This matters because `raise_errors` is already deprecated in yfinance 1.4.1.
    If it ever becomes accepted-but-ignored, every throttled response lands here
    as an empty frame -- and calling that `no_data` would cache it for a week,
    exclude it from the degraded rate, and post a green heartbeat for a run that
    checked nothing. As a transport failure it is loud, uncached, and degrades
    the run. Either way it is never a flag."""
    ran, last = _probe_recent_price(_FakeTicker(pd.DataFrame()))
    assert ran == PRICE_FAILED and last == ""


def test_probe_all_nan_close_is_not_a_dead_feed():
    idx = pd.to_datetime(["2026-06-12"]).tz_localize("UTC")
    df = pd.DataFrame({"Close": [float("nan")]}, index=idx)
    ran, last = _probe_recent_price(_FakeTicker(df))
    assert ran == PRICE_FAILED and last == ""


def test_probe_exception_marks_not_run():
    ran, last = _probe_recent_price(_FakeTicker(raises=True))
    assert ran == PRICE_FAILED and last == ""


# ── rate-limit backoff ──────────────────────────────────────────────────────
#
# The 2026-07-26 cold run lost 518/1,093 price probes and 488 `.info` calls to
# "Too Many Requests". The verdict logic handled that honestly (502 inconclusive
# rather than 502 false flags) but the run still learned almost nothing, so the
# throughput itself had to be fixed.


class _FlakyTicker:
    """Rate-limits the first `fail_times` calls, then succeeds."""

    def __init__(self, fail_times, df):
        self.fail_times = fail_times
        self._df = df
        self.calls = 0

    def history(self, **_kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("Too Many Requests. Rate limited. Try after a while.")
        return self._df


def test_rate_limit_detection():
    from universe.delisted_check import _is_rate_limited

    assert _is_rate_limited(RuntimeError("Too Many Requests. Rate limited."))
    assert _is_rate_limited(RuntimeError("HTTP 429"))
    assert not _is_rate_limited(RuntimeError("404 Not Found")), (
        "a 404 is a real answer about a real symbol -- retrying it would burn "
        "the budget the throttled names need"
    )


def test_probe_retries_through_rate_limiting(_no_real_sleeping):
    t = _FlakyTicker(2, _hist(["2026-06-12"]))
    ran, last = _probe_recent_price(t)
    assert ran == PRICE_OK and last == "2026-06-12", (
        "a rate-limited probe that later succeeds must not be recorded as a "
        "failed lookup"
    )
    assert t.calls == 3
    assert _no_real_sleeping, "backoff should have slept between attempts"


def test_probe_gives_up_after_the_attempt_budget():
    from universe.delisted_check import RETRY_ATTEMPTS

    t = _FlakyTicker(99, None)
    ran, _last = _probe_recent_price(t)
    assert ran == PRICE_FAILED
    assert t.calls == RETRY_ATTEMPTS


def test_a_404_is_not_retried():
    """Only rate limiting is transient; a dead symbol answers immediately."""

    class _NotFound(_FakeTicker):
        def history(self, **kwargs):
            self.called_with = kwargs
            self.calls = getattr(self, "calls", 0) + 1
            raise RuntimeError("404 Not Found")

    nf = _NotFound()
    ran, _last = _probe_recent_price(nf)
    assert ran == PRICE_FAILED
    assert nf.calls == 1, "a real answer must not be retried"


def test_yfinance_no_data_is_its_own_outcome_not_a_failure():
    """yfinance raises "possibly delisted; no price data found" for symbols that
    trade perfectly well (ACLX, verified 2026-07-26). It is an ANSWER, so it is
    not retried -- and it is not EVIDENCE, so it is not a failure either."""

    class _NoData(_FakeTicker):
        def history(self, **kwargs):
            self.calls = getattr(self, "calls", 0) + 1
            raise RuntimeError(
                "$ACLX: possibly delisted; no price data found  (period=1y)")

    nd = _NoData()
    ran, _last = _probe_recent_price(nd)
    assert ran == PRICE_NO_DATA
    assert nd.calls == 1


def test_throttle_escalates_then_relaxes():
    from universe.delisted_check import _THROTTLE

    _THROTTLE.reset()
    first = _THROTTLE.trip()
    second = _THROTTLE.trip()
    assert second > first, "backoff must escalate while Yahoo keeps refusing"
    assert _THROTTLE.trips == 2
    _THROTTLE.relax()
    _THROTTLE.relax()
    third = _THROTTLE.trip()
    assert third <= second, "a run that recovers must speed back up"
    _THROTTLE.reset()
    assert _THROTTLE.trips == 0


def test_throttle_pause_is_shared_across_threads(_no_real_sleeping):
    """One thread's 429 must pause the others -- private backoff expires into a
    server the other workers never stopped hammering."""
    import threading

    from universe.delisted_check import _THROTTLE

    _THROTTLE.reset()
    _THROTTLE.trip()
    done = []
    t = threading.Thread(target=lambda: (_THROTTLE.wait(), done.append(True)))
    t.start()
    t.join(timeout=5)
    assert done == [True]
    assert _no_real_sleeping, "the second thread waited on the shared cooldown"
    _THROTTLE.reset()


# ── _classify: price-recency rule (reads the frozen price_stale decision) ────

def _row(name="Exact Sciences Corporation"):
    return {"Company Name": name, "Sector (JP)": "MedTech", "Subsector (JP)": "Diagnostics"}


def _identity(name="Exact Sciences Corporation", quote="EQUITY",
              last="", probe_ran=True, stale=False, info_ok=True):
    return {
        "quoteType": quote,
        "longName": name,
        "shortName": name,
        "last_close_date": last,
        "info_ok": info_ok,
        "price_probe_ran": probe_ran,
        "price_stale": stale,
    }


def test_clean_acquisition_flagged_despite_stale_info():
    # Yahoo keeps longName populated post-delisting; price feed dead → price_stale.
    verdict, reason = _classify(_row(), _identity(last="", stale=True))
    assert verdict == VERDICT_FLAGGED
    assert "no recent price data" in reason


def test_old_last_bar_flagged():
    verdict, reason = _classify(_row(), _identity(last=_fresh(90), stale=True))
    assert verdict == VERDICT_FLAGGED
    assert "no recent price data" in reason and "last bar=" in reason


def test_live_ticker_not_flagged():
    verdict, reason = _classify(_row(), _identity(last=_fresh(1), stale=False))
    assert verdict == VERDICT_CLEAN, reason


def test_transient_probe_failure_does_not_trigger_price_flag():
    # probe didn't run (network blip) → price_stale is False → never FLAGGED.
    verdict, reason = _classify(_row(), _identity(last="", probe_ran=False, stale=False))
    assert verdict != VERDICT_FLAGGED, reason


def test_matching_identity_with_a_failed_price_probe_is_inconclusive():
    """Not clean. A clean acquisition keeps `.info` — and therefore the name
    match — intact for months, so the price feed is the ONLY signal that finds
    it. With the probe failed, the check that mattered never ran."""
    verdict, reason = _classify(_row(), _identity(last="", probe_ran=False, stale=False))
    assert verdict == VERDICT_INCONCLUSIVE
    assert "was not checked" in reason


def test_metadata_only_findings_survive_a_failed_price_probe():
    """The inconclusive downgrade must not swallow rules that never needed the
    price feed — a recycled quoteType and a name mismatch are still findings."""
    recycled = _classify(_row(), _identity(name="Some ETF", quote="ETF",
                                           probe_ran=False, stale=False))
    assert recycled[0] == VERDICT_FLAGGED and "non-equity" in recycled[1]

    renamed = _classify(_row(), _identity(name="Completely Different Issuer Holdings",
                                          probe_ran=False, stale=False))
    assert renamed[0] == VERDICT_FLAGGED and "mismatch" in renamed[1]


def test_non_equity_recycle_flagged():
    verdict, reason = _classify(_row(), _identity(name="Some ETF", quote="ETF",
                                                  last=_fresh(1), stale=False))
    assert verdict == VERDICT_FLAGGED
    assert "non-equity" in reason


def test_name_mismatch_flagged_when_price_fresh():
    verdict, reason = _classify(
        _row(), _identity(name="Completely Different Issuer Holdings",
                          last=_fresh(1), stale=False))
    assert verdict == VERDICT_FLAGGED
    assert "mismatch" in reason


# ── the ACLX regression: a failed lookup is not a delisting ─────────────────
#
# The 2026-07-25 run reported 58 flags. An independent quote showed ACLX
# (Arcellx) trading at $115.07 on NASDAQ with 13.2M shares of volume, and the
# same run logged 53 price-probe failures out of 1,093 names. Yahoo was
# throttling; the check was writing throttling down as death. Every test below
# pins the distinction the old two-state return could not express.


def test_total_lookup_failure_is_inconclusive_not_delisted():
    """The headline bug. `{}` (nothing answered) used to mean 'likely delisted'."""
    verdict, reason = _classify(_row(), {})
    assert verdict == VERDICT_INCONCLUSIVE, (
        "a lookup that failed outright is the ABSENCE of evidence -- reporting "
        "it as a delisting is what made this check untrustworthy"
    )
    assert "no evidence" in reason


def test_both_probes_failed_is_inconclusive():
    ident = _identity(name="", quote="", info_ok=False, probe_ran=False)
    verdict, _reason = _classify(_row(), ident)
    assert verdict == VERDICT_INCONCLUSIVE


def test_live_price_feed_overrides_empty_metadata():
    """A ticker that demonstrably TRADES is not delisted, whatever `.info` says.

    This is the ACLX shape exactly: throttled metadata, healthy price feed.
    """
    ident = _identity(name="", quote="", info_ok=False,
                      last=_fresh(1), stale=False)
    verdict, reason = _classify(_row(), ident)
    assert verdict == VERDICT_CLEAN, reason
    assert "price feed is live" in reason


def test_empty_metadata_that_actually_answered_still_defers_to_live_price():
    ident = _identity(name="", quote="", info_ok=True, last=_fresh(1), stale=False)
    verdict, reason = _classify(_row(), ident)
    assert verdict == VERDICT_CLEAN, reason


def test_empty_metadata_and_dead_price_feed_is_a_real_flag():
    """Both independent signals agree -- this one SHOULD flag."""
    ident = _identity(name="", quote="", info_ok=True, last="", stale=True)
    verdict, reason = _classify(_row(), ident)
    assert verdict == VERDICT_FLAGGED
    assert "likely delisted" in reason


def test_quote_type_without_a_name_is_not_a_name_mismatch():
    """Caught live: ACLX returned a quoteType but empty long/shortName, and the
    similarity rule scored that 0.00 and flagged a 'mismatch' against `''`."""
    ident = _identity(name="", quote="EQUITY", last=_fresh(1), stale=False)
    verdict, reason = _classify(_row(), ident)
    assert verdict == VERDICT_CLEAN, reason
    assert "no company name to compare" in reason
    assert "mismatch" not in reason


def test_name_similarity_is_none_when_nothing_to_compare():
    from universe.delisted_check import _name_similarity

    assert _name_similarity("Arcellx Inc", "", "") is None, (
        "an impossible comparison has no score -- 0.0 would read as "
        "'completely different names'"
    )
    assert _name_similarity("", "Arcellx Inc", "") is None


def test_empty_metadata_with_failed_price_probe_is_inconclusive():
    ident = _identity(name="", quote="", info_ok=True, probe_ran=False, stale=False)
    verdict, _reason = _classify(_row(), ident)
    assert verdict == VERDICT_INCONCLUSIVE


# ── check_universe: bucketing and the degraded-run guard ────────────────────


def _fake_universe(monkeypatch, rows, identities):
    """Run check_universe against canned identities, no network."""
    import universe.delisted_check as dc

    monkeypatch.setattr(dc.pd, "read_csv", lambda *a, **k: pd.DataFrame(rows))
    monkeypatch.setattr(dc, "_fetch_identity",
                        lambda yf_t, use_cache=True: identities[yf_t])
    return dc.check_universe(csv_path="ignored", max_workers=1)


def _urow(ticker, name="Some Company Inc"):
    return {"Ticker": ticker, "Company Name": name, "Exchange": "NASDAQ",
            "Sector (JP)": "Biopharma", "Subsector (JP)": ""}


def test_inconclusive_never_enters_the_flagged_list(monkeypatch):
    result = _fake_universe(
        monkeypatch,
        [_urow("AAA"), _urow("BBB")],
        {"AAA": {}, "BBB": _identity(name="Some Company Inc", last=_fresh(1))},
    )
    assert [r["ticker"] for r in result["flagged"]] == []
    assert [r["ticker"] for r in result["inconclusive"]] == ["AAA"]


def test_live_price_with_missing_metadata_is_reported_but_not_flagged(monkeypatch):
    """Never silent: it stays out of `flagged` but is still surfaced."""
    result = _fake_universe(
        monkeypatch, [_urow("ACLX", "Arcellx Inc")],
        {"ACLX": _identity(name="", quote="", info_ok=False, last=_fresh(1))},
    )
    assert result["flagged"] == []
    assert result["inconclusive"] == []
    assert [r["ticker"] for r in result["metadata_gaps"]] == ["ACLX"]


def test_degraded_counts_the_union_of_failed_probes(monkeypatch):
    """max(price_fails, info_fails) understates the damage: the probes fail
    independently, so disjoint failures affect more tickers than either count."""
    ids = {}
    for i in range(20):
        if i == 0:      # price-only failure
            ids[f"T{i}"] = _identity(name="Some Company Inc", probe_ran=False)
        elif i == 1:    # metadata-only failure, price fine
            ids[f"T{i}"] = _identity(name="", quote="", info_ok=False,
                                     last=_fresh(1))
        else:
            ids[f"T{i}"] = _identity(name="Some Company Inc", last=_fresh(1))
    result = _fake_universe(monkeypatch, [_urow(f"T{i}") for i in range(20)], ids)

    assert result["price_probe_failures"] == 1
    assert result["info_failures"] == 1
    assert result["tickers_with_a_failed_probe"] == 2, "the union, not the max"
    # 2/20 = 10% > 2%; max() would have seen 1/20 = 5%... still over, so pin the
    # count itself rather than relying on the threshold to expose the bug.


def test_high_failure_rate_marks_the_run_degraded(monkeypatch):
    result = _fake_universe(
        monkeypatch, [_urow(f"T{i}") for i in range(10)],
        {f"T{i}": ({} if i < 2 else _identity(name="Some Company Inc",
                                              last=_fresh(1)))
         for i in range(10)},
    )
    assert 2 / 10 > DEGRADED_FAILURE_RATE
    assert result["degraded"] is True


def test_clean_run_is_not_degraded(monkeypatch):
    result = _fake_universe(
        monkeypatch, [_urow(f"T{i}") for i in range(10)],
        {f"T{i}": _identity(name="Some Company Inc", last=_fresh(1))
         for i in range(10)},
    )
    assert result["degraded"] is False
    assert result["flagged"] == [] and result["inconclusive"] == []


# ── report rendering ───────────────────────────────────────────────────────


def _minimal_result(**over):
    base = {"checked": 3, "flagged": [], "inconclusive": [], "metadata_gaps": [],
            "missing_data": 0, "price_probe_failures": 0, "info_failures": 0,
            "degraded": False}
    base.update(over)
    return base


def _entry(ticker, reason):
    return {"ticker": ticker, "yf_ticker": ticker, "recorded_name": "Co",
            "yf_long_name": "", "yf_short_name": "", "quote_type": "",
            "last_close_date": "", "sector_jp": "", "subsector_jp": "",
            "reason": reason}


def test_report_separates_inconclusive_from_flagged(tmp_path):
    paths = write_report(
        _minimal_result(flagged=[_entry("DEAD", "no recent price data")],
                        inconclusive=[_entry("ACLX", "lookup failed")]),
        reports_dir=tmp_path, run_date="2026-07-25")
    md = (tmp_path / "delisted_check_2026-07-25.md").read_text(encoding="utf-8")
    assert "Inconclusive" in md and "ACLX" in md
    # the CSV must carry the verdict so a reader of it alone can't conflate them
    csv_text = (tmp_path / "delisted_check_2026-07-25.csv").read_text(encoding="utf-8")
    assert csv_text.splitlines()[0].startswith("verdict,")
    assert f"{VERDICT_INCONCLUSIVE},ACLX" in csv_text
    assert f"{VERDICT_FLAGGED},DEAD" in csv_text
    assert paths["md_path"].endswith(".md")


def test_degraded_run_says_so_in_the_report(tmp_path):
    write_report(_minimal_result(degraded=True, price_probe_failures=53),
                 reports_dir=tmp_path, run_date="2026-07-25")
    md = (tmp_path / "delisted_check_2026-07-25.md").read_text(encoding="utf-8")
    assert "degraded" in md.lower()
    assert "provisional" in md.lower(), (
        "a throttled run's flags must not read as findings"
    )


def test_missing_identity_excludes_throttled_lookups(monkeypatch):
    """`missing_data` must mean "Yahoo has no metadata for this symbol", not
    "Yahoo would not talk to us" -- otherwise the ambiguity this module was
    rewritten to remove survives inside a counter and inflates under throttling,
    double-counting against the transport-failure line."""
    result = _fake_universe(
        monkeypatch,
        [_urow("ANSWERED"), _urow("THROTTLED"), _urow("FINE")],
        {
            # answered, and genuinely has nothing
            "ANSWERED": _identity(name="", quote="", info_ok=True, last=_fresh(1)),
            # never answered
            "THROTTLED": _identity(name="", quote="", info_ok=False, probe_ran=False),
            "FINE": _identity(name="Some Company Inc", last=_fresh(1)),
        },
    )
    assert result["missing_data"] == 1, "only the ticker that actually answered"
    assert result["info_failures"] == 1


def test_a_universe_wide_no_data_answer_is_implausible(monkeypatch):
    """`no_data` is excluded from the transport-failure rate on purpose, so a
    systemic fault that manifests AS no_data -- a Yahoo API change, a broken
    endpoint -- would otherwise report zero flags behind a green heartbeat.
    Past a threshold the answer describes the RUN, not the universe."""
    ids = {f"T{i}": _identity(name="Some Company Inc", info_ok=True,
                              probe_ran=False, stale=False)
           for i in range(10)}
    for v in ids.values():
        v["price_status"] = PRICE_NO_DATA
    result = _fake_universe(monkeypatch, [_urow(f"T{i}") for i in range(10)], ids)

    assert result["price_no_data"] == 10
    assert result["tickers_with_a_failed_probe"] == 0, (
        "no_data must stay out of the transport rate, or degraded pins on"
    )
    assert result["no_data_implausible"] is True
    assert result["degraded"] is True, "the backstop must still fire"


def test_a_normal_no_data_population_does_not_degrade_the_run(monkeypatch):
    """A few dozen symbols genuinely have no yfinance history. That is the
    baseline, and it must not hold the alarm on."""
    ids = {}
    for i in range(100):
        ident = _identity(name="Some Company Inc", last=_fresh(1))
        if i < 3:
            ident = _identity(name="Some Company Inc", info_ok=True,
                              probe_ran=False, stale=False)
            ident["price_status"] = PRICE_NO_DATA
        ids[f"T{i}"] = ident
    result = _fake_universe(monkeypatch, [_urow(f"T{i}") for i in range(100)], ids)

    assert result["price_no_data"] == 3
    assert result["no_data_implausible"] is False
    assert result["degraded"] is False


def test_no_console_log_line_carries_non_ascii():
    """A non-ASCII character in a LOGGED string raises UnicodeEncodeError under
    the scheduled task's cp1252-redirected console and kills the run BEFORE its
    heartbeat posts -- so the failure is invisible. Markdown written to a UTF-8
    report file is fine and is not checked here; only console output is.

    Found two live instances on 2026-07-27, both in failure paths (a missing
    baseline SHA, and SEC being unavailable) -- i.e. they would have fired
    exactly when something was already going wrong.
    """
    import pathlib
    import re

    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for rel in ("universe/delisted_check.py", "universe/cik_backfill.py",
                "universe/ticker_change_check.py", "weekly_universe.py",
                "weekly_build.py", "cli.py"):
        for n, line in enumerate((repo / rel).read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(logger\.\w+|print)\(", line) and any(ord(c) > 127 for c in line):
                offenders.append(f"{rel}:{n}")
    assert not offenders, (
        "non-ASCII on a console line; a cp1252 scheduled run dies here before "
        "its heartbeat: " + ", ".join(offenders)
    )
