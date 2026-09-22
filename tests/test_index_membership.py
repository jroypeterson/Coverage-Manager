"""Tests for universe/index_membership.py — the EAFE constituent+weight archive.

The CSV fragments below are shaped from the REAL iShares response captured
2026-09-09 (product 239623): a 9-line preamble, then a `Ticker,` header, then
rows whose `Asset Class` is mostly but not entirely `Equity`.
"""
import json
from datetime import date

import pytest

from universe import index_membership as im

REAL_PREAMBLE = '''iShares MSCI EAFE ETF
Fund Holdings as of,"Sep 04, 2026"
Inception Date,"Aug 14, 2001"
Shares Outstanding,"1,000,000.00"
Stock,"-"
Bond,"-"
Cash,"-"
Other,"-"

'''

HEADER = ("Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,"
          "Quantity,Price,Location,Exchange,Currency,FX Rate,Market Currency,Accrual Date\n")


def _rows(n=3):
    """⛑ The weights SUM TO ~100, as a real fund's do. They used to be `{i}.5`, so a
    500-row fixture summed to 125,500% -- harmless while nothing read the total, and
    misleading the moment a guard did (the weight-total rule, Codex round 10)."""
    each = round(100.0 / n, 4)
    out = []
    for i in range(n):
        out.append(f'"T{i}","COMPANY {i}","Health Care","Equity","1,000.00","{each}",'
                   f'"1,000.00","10.00","100.00","Japan","Tokyo","USD","1.0","JPY","-"\n')
    return "".join(out)


def _csv(n=3, extras=""):
    return REAL_PREAMBLE + HEADER + _rows(n) + extras


# --- parsing -----------------------------------------------------------------

def test_parses_as_of_and_equity_rows():
    as_of, rows = im.parse_holdings(_csv(3))
    assert as_of == "2026-09-04"
    assert [r["ticker"] for r in rows] == ["T0", "T1", "T2"]
    assert rows[0]["weight_pct"] == round(100 / 3, 4)
    assert rows[0]["location"] == "Japan"
    assert rows[0]["market_currency"] == "JPY"


def test_non_equity_rows_are_excluded():
    """Cash, futures and FX lines carry weight but are not constituents. Counting
    them would inflate the list and put a `-` ticker in a membership record."""
    extras = ('"USD","US DOLLAR","Cash","Cash","500.00","0.5","500.00","500.00",'
              '"1.00","United States","-","USD","1.0","USD","-"\n'
              '"-","MSCI EAFE FUTURE","-","Futures","900.00","0.9","900.00","5.00",'
              '"180.00","-","-","USD","1.0","USD","-"\n')
    _, rows = im.parse_holdings(_csv(2, extras))
    assert [r["ticker"] for r in rows] == ["T0", "T1"]


def test_html_app_shell_raises_even_though_it_would_arrive_as_http_200():
    """The documented iShares failure: the `.ajax` route serves the page shell with
    a 200 and `content-type: text/csv`. Accepting it writes an empty index."""
    with pytest.raises(im.IndexMembershipError, match="HTML page"):
        im.parse_holdings("<!DOCTYPE html>\n<html lang='en'><head><title>iShares</title>")


def test_missing_as_of_raises_rather_than_stamping_today():
    """An invented as-of makes every snapshot look current, which destroys the only
    thing the archive is for."""
    no_date = _csv(3).replace('Fund Holdings as of,"Sep 04, 2026"\n', "")
    with pytest.raises(im.IndexMembershipError, match="Fund Holdings as of"):
        im.parse_holdings(no_date)


def test_missing_header_row_raises():
    with pytest.raises(im.IndexMembershipError, match="Ticker"):
        im.parse_holdings(REAL_PREAMBLE + "some,other,shape\n1,2,3\n")


# --- refresh -----------------------------------------------------------------

@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "OUT_DIR", tmp_path / "index_membership")
    return tmp_path / "index_membership"


def _serve(monkeypatch, text):
    monkeypatch.setattr(im, "_fetch_csv", lambda pid, timeout=60: text)


def _fail(monkeypatch, msg="boom"):
    def _raise(pid, timeout=60):
        raise im.IndexMembershipError(msg)
    monkeypatch.setattr(im, "_fetch_csv", _raise)


def test_refresh_writes_dated_and_latest(out_dir, monkeypatch):
    _serve(monkeypatch, _csv(500))
    r = im.refresh("eafe")
    assert r["status"] == "ok"
    assert r["count"] == 500
    assert (out_dir / "eafe_2026-09-04.json").exists()
    doc = json.loads((out_dir / "eafe_latest.json").read_text(encoding="utf-8"))
    assert doc["index"] == "MSCI EAFE"
    assert doc["as_of"] == "2026-09-04"
    assert len(doc["holdings"]) == 500


def test_a_dated_snapshot_is_never_rewritten(out_dir, monkeypatch):
    """The fund republishes the same as-of for days. The archive's value is that a
    past file says what it said at the time, so a re-run must not edit it."""
    _serve(monkeypatch, _csv(500))
    im.refresh("eafe")
    dated = out_dir / "eafe_2026-09-04.json"
    first = dated.read_bytes()

    _serve(monkeypatch, _csv(501))          # same as_of, different content
    r = im.refresh("eafe")
    assert r["written"] is None
    assert dated.read_bytes() == first
    # latest DOES move — it is the current view, not the archive.
    assert json.loads((out_dir / "eafe_latest.json").read_text(encoding="utf-8"))["count"] == 501


def test_a_short_list_is_refused_not_written(out_dir, monkeypatch):
    """Half an index is worse than none: it looks usable."""
    _serve(monkeypatch, _csv(10))
    with pytest.raises(im.IndexMembershipError, match="credibility floor"):
        im.refresh("eafe")
    assert not (out_dir / "eafe_latest.json").exists()


def test_fetch_failure_with_no_cache_raises(out_dir, monkeypatch):
    """No fallback and no fetch is not a quiet week — it is an outage."""
    _fail(monkeypatch)
    with pytest.raises(im.IndexMembershipError):
        im.refresh("eafe")


def test_fetch_failure_falls_back_to_cache_and_reports_age(out_dir, monkeypatch):
    _serve(monkeypatch, _csv(500))
    im.refresh("eafe")

    _fail(monkeypatch, "endpoint gone")
    r = im.refresh("eafe", today=date(2026, 9, 20))
    assert r["status"] == "stale"
    assert r["count"] == 500
    assert r["age_days"] == 16
    assert "endpoint gone" in r["error"]


def test_a_cache_older_than_stale_days_is_reported_unfit(out_dir, monkeypatch):
    """Serving a months-old membership list silently is the failure this avoids."""
    _serve(monkeypatch, _csv(500))
    im.refresh("eafe")

    _fail(monkeypatch)
    r = im.refresh("eafe", today=date(2026, 12, 1))
    assert r["status"] == "stale_unfit"
    assert r["age_days"] > im.STALE_DAYS


def test_every_snapshot_states_that_the_fund_is_not_the_index(out_dir, monkeypatch):
    """EFA is sampled. A consumer reading only the JSON must still be told, or the
    caveat lives in a docstring nobody downstream reads."""
    _serve(monkeypatch, _csv(500))
    im.refresh("eafe")
    doc = json.loads((out_dir / "eafe_latest.json").read_text(encoding="utf-8"))
    blob = " ".join(doc["caveats"]).lower()
    assert "sampled" in blob
    assert "isin" in blob          # the local-ticker join trap
    assert "exports" in blob       # the licensing rule


def test_the_weekly_step_fails_only_on_an_unfit_snapshot(out_dir, monkeypatch):
    """A stale-but-usable cache must not fail the build that publishes the universe;
    an unfit one must not pass silently."""
    import weekly_universe

    monkeypatch.setattr(im, "refresh_all",
                        lambda: [{"key": "eafe", "status": "stale", "as_of": "2026-09-04",
                                  "count": 658, "age_days": 20, "error": "x", "written": None}])
    assert weekly_universe._step_index_membership()["results"][0]["status"] == "stale"

    monkeypatch.setattr(im, "refresh_all",
                        lambda: [{"key": "eafe", "status": "stale_unfit", "as_of": "2026-05-01",
                                  "count": 658, "age_days": 131, "error": "x", "written": None}])
    with pytest.raises(RuntimeError, match="unfit"):
        weekly_universe._step_index_membership()


# --- the multi-source extension (2026-09-09) ---------------------------------

def test_the_vanguard_url_lowercases_the_etf():
    """⛑ THE REGRESSION THAT FROZE THE RUSSELL LISTS FOR ~12 DAYS.

    `/api/VONE/...` answers 301 to a human page that serves HTML with HTTP 200, so the
    JSON decode fails on every retry; `/api/vone/...` returns the data. This asserts the
    property, not the string, so a future URL change cannot quietly re-uppercase it.
    """
    captured = []

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"size": 1, "asOfDate": "2026-07-31T00:00:00-04:00",
                               "fund": {"entity": []}}).encode()

    import urllib.request as _u

    def _fake(req, timeout=None):
        captured.append(req.full_url)
        return _Resp()

    import pytest as _p
    with _p.MonkeyPatch.context() as m:
        m.setattr(_u, "urlopen", _fake)
        with pytest.raises(im.IndexMembershipError):    # no holdings -> refuses
            im._fetch_vanguard("VONE")
    assert captured, "no request was made"
    assert "/api/vone/" in captured[0]
    assert "/api/VONE/" not in captured[0]


@pytest.mark.parametrize("raw,want", [
    ("2.9500", 2.95),        # Vanguard sends percentWeight as a STRING
    ("1,234.5", 1234.5),
    ("3.1%", 3.1),
    (2.5, 2.5),
    ("", None),              # never NaN — NaN poisons any total it joins
    ("n/a", None),
    (None, None),
    (float("nan"), None),
    (float("inf"), None),
])
def test_num_returns_a_finite_float_or_nothing(raw, want):
    got = im._num(raw)
    assert got == want
    assert got is None or isinstance(got, float)


def test_stale_days_is_per_source_because_the_cadences_differ():
    """A flat 45-day rule would mark the Russell lane unfit on an ordinary week —
    Vanguard publishes month-end holdings and Russell reconstitutes annually."""
    assert im.stale_days_for("r1000") == 120
    assert im.stale_days_for("eafe") == im.STALE_DAYS == 45
    assert im.stale_days_for("sp500") == 45


def test_sp500_comes_from_cm_cache_as_an_OBSERVATION_not_a_source_date(out_dir, monkeypatch, tmp_path):
    """A scraped list states no effective date. Stamping it as though the index provider
    said so would let today's scrape claim to be a membership record for today.

    Since 2026-09-22 sp500's source is IVV; the `cm_cache` kind is kept as the rollback
    path (and its reader is IVV's name/sector enrichment), so it is pinned here by
    pointing the sp500 key back at it."""
    monkeypatch.setitem(im.SOURCES, "sp500", dict(im.SOURCES["sp500"], kind="cm_cache"))
    cache = tmp_path / "sp500.json"
    cache.write_text(json.dumps({
        "_cached_at": "2026-09-08T19:01:55.529799+00:00",
        "data": {"tickers": [f"T{i}" for i in range(500)] + ["BRK.B"],
                 "info": {"T0": {"Company Name": "Zero Inc",
                                 "GICS Sector": "Industrials",
                                 "GICS Sub-Industry": "Conglomerates"}}}}), encoding="utf-8")
    monkeypatch.setattr(im, "SP500_CACHE", cache)

    r = im.refresh("sp500")
    assert r["status"] == "ok"
    doc = json.loads((out_dir / "sp500_2026-09-08.json").read_text(encoding="utf-8"))
    assert doc["as_of"] == "2026-09-08"
    assert doc["as_of_kind"] == "observed"
    assert doc["holdings"][0]["sector"] == "Industrials"
    # Share classes normalise to the fleet's dash, same as the Vanguard leg.
    assert doc["holdings"][-1]["ticker"] == "BRK-B"
    # ⛑ NO INVENTED WEIGHTS. A constituent list is not a weighted index.
    assert all(h["weight_pct"] is None for h in doc["holdings"])
    assert doc["equity_weight_pct"] is None


def test_an_sp500_cache_with_no_stamp_is_refused(out_dir, monkeypatch, tmp_path):
    cache = tmp_path / "sp500.json"
    cache.write_text(json.dumps({"data": {"tickers": ["A"], "info": {}}}), encoding="utf-8")
    monkeypatch.setattr(im, "SP500_CACHE", cache)
    with pytest.raises(im.IndexMembershipError, match="_cached_at"):
        im._load_cm_sp500()


def test_one_index_failing_does_not_skip_the_others(out_dir, monkeypatch):
    """⛑ The first version raised out of the loop, so a Vanguard outage took the EAFE and
    S&P snapshots with it — and a week not captured cannot be recaptured."""
    def _collect(key):
        if key.startswith("r"):
            raise im.IndexMembershipError("vanguard down")
        return "2026-09-08", "source", [{"ticker": f"T{i}", "name": "x", "sector": "",
                                         "weight_pct": 0.2, "location": "", "exchange": "",
                                         "market_currency": "", "market_value_usd": None}
                                        for i in range(500)]
    monkeypatch.setattr(im, "collect", _collect)

    results = {r["key"]: r["status"] for r in im.refresh_all()}
    assert results["eafe"] == "ok"
    assert results["sp500"] == "ok"
    assert results["r1000"] == results["r2000"] == results["r3000"] == "failed"


def test_the_weekly_step_fails_on_a_failed_source_too(out_dir, monkeypatch):
    """`failed` and `stale_unfit` are different states but the same operator signal:
    this lane did not learn what it was asked to learn."""
    import weekly_universe

    monkeypatch.setattr(im, "refresh_all",
                        lambda: [{"key": "r1000", "status": "failed", "as_of": None,
                                  "count": None, "age_days": None, "error": "x",
                                  "written": None}])
    with pytest.raises(RuntimeError, match="degraded"):
        weekly_universe._step_index_membership()


# --- the threshold travels on the artifact (board #354, consumer migration) ---

def test_a_snapshot_states_its_own_staleness_threshold(out_dir, monkeypatch):
    """⛑ THE CONSUMER THAT APPLIES THIS LIVES IN ANOTHER REPO.

    `sector_chart_pack` reads these JSON files, not this module, so a threshold
    kept only in Python here has to be copied there — and a copied number is a
    number that drifts. Before this field, `russell.py` carried a flat 120 and
    this module a per-kind table, and the straight swap between them would have
    tightened the Russell gate to 45 days silently. The snapshot carrying its own
    `stale_days` is what makes that impossible rather than merely noticed.
    """
    _serve(monkeypatch, _csv(500))
    im.refresh("eafe")
    doc = json.loads((out_dir / "eafe_latest.json").read_text(encoding="utf-8"))
    assert doc["schema_version"] == 3
    assert doc["kind"] == "ishares"
    assert doc["stale_days"] == im.stale_days_for("eafe") == 45


def test_every_source_writes_the_threshold_its_own_kind_earns(out_dir, monkeypatch):
    """Not one number on every file: the Russell lane's 120 is the whole point."""
    def _collect(key):
        # sp500 gets a plausible S&P 500 count: the band binds every path now.
        n = 503 if key == "sp500" else 3000
        return "2026-09-08", "source", [{"ticker": f"T{i}", "name": "x", "sector": "",
                                         "weight_pct": round(100 / n, 4),
                                         "location": "", "exchange": "",
                                         "market_currency": "", "market_value_usd": None}
                                        for i in range(n)]
    monkeypatch.setattr(im, "collect", _collect)
    im.refresh_all()
    got = {k: json.loads((out_dir / f"{k}_latest.json").read_text(encoding="utf-8"))["stale_days"]
           for k in im.SOURCES}
    assert got == {"eafe": 45, "sp500": 45, "r1000": 120, "r2000": 120, "r3000": 120}


# --- Vanguard guards inherited from sector_chart_pack/russell.py -------------
#
# ⛑ THESE TWO MOVED HERE WHEN `russell.py` WAS RETIRED (board #354). They were
# the only tests anywhere pinning the page size and the refuse-a-partial-list
# rule, and the module docstring calls both load-bearing. Deleting the file that
# held them would have left two documented invariants with zero tests — a green
# suite that names a thing that is missing.

def _vanguard_page(n, start, size, as_of="2026-07-31"):
    return {"size": size, "asOfDate": as_of + "T00:00:00-04:00",
            "fund": {"entity": [{"ticker": f"T{i}", "longName": f"Name {i}",
                                 "percentWeight": f"{round(100.0 / max(size, 1), 4)}"}
                                for i in range(start, start + n)]}}


class _JsonResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self): return self

    def __exit__(self, *a): return False

    def read(self): return json.dumps(self._payload).encode()


def test_vanguard_pagination_never_widens_past_500(monkeypatch):
    """⛑ `count=5000` returns everything in one call but serves an OLDER snapshot
    (2026-06-30 against 2026-07-31, measured 2026-08-18). Freshness is the whole
    reason this source was chosen over SEC N-PORT, so it always paginates."""
    import time
    import urllib.request as _u

    seen = []

    def _fake(req, timeout=None):
        seen.append(req.full_url)
        start = int(req.full_url.split("start=")[1].split("&")[0])
        return _JsonResp(_vanguard_page(min(500, 1200 - start + 1), start, 1200))

    monkeypatch.setattr(_u, "urlopen", _fake)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    as_of, rows = im._fetch_vanguard("VTWO")
    assert im.VANGUARD_PAGE == 500
    assert all("count=500" in u for u in seen)
    assert len(seen) >= 3                    # 1200 names cannot arrive in one page
    assert len(rows) == 1200 and as_of == "2026-07-31"


def test_a_vanguard_page_that_never_decodes_raises_rather_than_shrinking_the_index(monkeypatch):
    """The endpoint intermittently answers HTTP 200 with the HTML app shell. Returning
    the pages that did decode would quietly publish a Russell 2000 of 500 names."""
    import time
    import urllib.request as _u

    calls = {"n": 0}

    def _fake(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _JsonResp(_vanguard_page(500, 1, 1200))
        raise ValueError("Expecting value: line 1 column 1 (char 0)")   # HTML, not JSON

    monkeypatch.setattr(_u, "urlopen", _fake)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    with pytest.raises(im.IndexMembershipError, match="undecodable"):
        im._fetch_vanguard("VTWO")
    assert calls["n"] == 1 + im.VANGUARD_RETRIES     # it retried before giving up


# --- S&P 500 from iShares IVV holdings (JP 2026-09-22, Fable-gated) -----------
#
# The fixture is a TRIMMED copy of the live IVV file fetched 2026-09-22 (product
# 239726, "Fund Holdings as of Sep 21, 2026"): the real preamble and header, every
# share-class line the S&P 500 has, the September reconstitution's three entrants,
# the four non-equity lines, and HOLX -- a post-deal residual the fund still carries
# at $0.01 on "NO MARKET (E.G. UNLISTED)" although the index dropped it.

from pathlib import Path as _Path

IVV_FIXTURE = _Path(__file__).parent / "fixtures" / "ivv_holdings_2026-09-21_trimmed.csv"


def _ivv_text():
    return IVV_FIXTURE.read_text(encoding="utf-8")


WIKI_TICKERS = ["NVDA", "AAPL", "MMM", "GOOGL", "GOOG", "BRK.B", "BF.B", "FOXA",
                "FOX", "NWSA", "NWS", "CBOE", "BLDR", "TAP", "TTD"]
WIKI_INFO = {
    "MMM": {"Company Name": "3M", "GICS Sector": "Industrials",
            "GICS Sub-Industry": "Industrial Conglomerates"},
    "BRK.B": {"Company Name": "Berkshire Hathaway", "GICS Sector": "Financials",
              "GICS Sub-Industry": "Multi-Sector Holdings"},
    "GOOGL": {"Company Name": "Alphabet Inc. (Class A)",
              "GICS Sector": "Communication Services",
              "GICS Sub-Industry": "Interactive Media & Services"},
}
# The fixture carries 15 members; the S&P 500 must land inside 495-510, so every
# collect-level test pads to a plausible index rather than exercising a 15-name one.
PAD_N = 488


def _pad_tickers(n=PAD_N):
    return [f"Z{i:03d}" for i in range(n)]


# The trimmed fixture's own equity rows carry ~0.9% between them; the pad rows take
# the rest, so a padded basket sums to ~100% exactly as the live file does.
FIXTURE_WEIGHT_PCT = 0.9


def _pad_rows(n=PAD_N):
    each = round((100.0 - FIXTURE_WEIGHT_PCT) / n, 4)
    return "".join(
        f'"{t}","PAD {t}","Industrials","Equity","1.00","{each}","1.00","1.00",'
        f'"1.00","United States","NYSE","USD","1.00","USD","-"\n'
        for t in _pad_tickers(n))


def _wiki_cache(tmp_path, monkeypatch, tickers=None, info=None):
    """A Wikipedia-shaped CM cache: pre-reconstitution, so no BE/ILMN/P."""
    tickers = WIKI_TICKERS if tickers is None else tickers
    info = dict(WIKI_INFO) if info is None else info
    cache = tmp_path / "sp500_wiki.json"
    cache.write_text(json.dumps({"_cached_at": "2026-09-18T14:09:24+00:00",
                                 "data": {"tickers": tickers, "info": info}}),
                     encoding="utf-8")
    monkeypatch.setattr(im, "SP500_CACHE", cache)
    return cache


def _serve_ivv(monkeypatch, tmp_path, text=None, pad=PAD_N, info_covered=None):
    """Serve the fixture padded to a plausible index, with a matching Wikipedia cache."""
    pads = _pad_tickers(pad)
    covered = pads if info_covered is None else pads[:info_covered]
    info = dict(WIKI_INFO)
    info.update({t: {"Company Name": f"Pad {t} Inc", "GICS Sector": "Industrials",
                     "GICS Sub-Industry": "Widgets"} for t in covered})
    _wiki_cache(tmp_path, monkeypatch, tickers=WIKI_TICKERS + pads, info=info)
    _serve(monkeypatch, (text if text is not None else _ivv_text()) + _pad_rows(pad))


def _ivv(monkeypatch, tmp_path, text=None, pad=PAD_N, info_covered=None):
    _serve_ivv(monkeypatch, tmp_path, text=text, pad=pad, info_covered=info_covered)
    return im.collect("sp500")


def _rename_header(column, replacement):
    lines = _ivv_text().splitlines(keepends=True)
    i = next(i for i, l in enumerate(lines) if l.startswith("Ticker,"))
    lines[i] = lines[i].replace(column, replacement)
    return "".join(lines)


def test_sp500_source_is_ivv_via_the_ishares_path():
    src = im.SOURCES["sp500"]
    assert src["kind"] == "ishares"
    assert src["pid"] == "239726"
    assert src["floor"] == 450
    assert im.stale_days_for("sp500") == im.STALE_DAYS == 45


def test_ivv_share_classes_normalise_to_the_cm_cache_dash_form(monkeypatch, tmp_path):
    """IVV writes `BRK B` / `BF B` with a SPACE. The cm_cache snapshots say `BRK-B`, and
    any other spelling makes the switch week's diff show phantom departures."""
    _, _, rows = _ivv(monkeypatch, tmp_path)
    got = {r["ticker"] for r in rows}
    assert {"BRK-B", "BF-B", "GOOG", "GOOGL", "FOX", "FOXA", "NWS", "NWSA"} <= got
    assert not [t for t in got if " " in t or "." in t]
    assert {"BE", "ILMN", "P"} <= got          # proves these rows came from IVV


def test_ivv_non_equity_and_unlisted_residual_lines_are_dropped(monkeypatch, tmp_path):
    _, _, rows = _ivv(monkeypatch, tmp_path)
    got = {r["ticker"] for r in rows}
    assert not got & {"XTSLA", "USD", "SGAFT", "ESZ6"}      # money market, cash, futures
    assert "HOLX" not in got                                # $0.01 post-deal residual
    assert {"BE", "ILMN", "P"} <= got and not got & {"BLDR", "TAP", "TTD"}
    assert len(rows) == 503


def test_ivv_as_of_is_the_funds_own_date_recorded_as_source(monkeypatch, tmp_path):
    as_of, kind, _ = _ivv(monkeypatch, tmp_path)
    assert (as_of, kind) == ("2026-09-21", "source")


def test_ivv_names_and_sectors_come_from_the_wikipedia_cache(monkeypatch, tmp_path):
    """Otherwise sp500_names.json flips to 'BERKSHIRE HATHAWAY INC CLASS B' style names,
    and IVV's 'Communication' is not the GICS 'Communication Services'."""
    _, _, rows = _ivv(monkeypatch, tmp_path)
    by = {r["ticker"]: r for r in rows}
    assert by["BRK-B"]["name"] == "Berkshire Hathaway"
    assert by["BRK-B"]["sub_industry"] == "Multi-Sector Holdings"
    assert by["GOOGL"]["sector"] == "Communication Services"
    assert by["MMM"]["name"] == "3M"
    assert by["MMM"]["name_source"] == "wikipedia"
    # the fund's weights and values stay on the (gitignored) snapshot
    assert by["MMM"]["weight_pct"] == 0.06
    assert by["MMM"]["exchange"] == "NYSE"


def test_ivv_name_is_only_the_fallback_for_a_name_wikipedia_lacks(monkeypatch, tmp_path):
    _, _, rows = _ivv(monkeypatch, tmp_path)
    be = {r["ticker"]: r for r in rows}["BE"]
    assert be["name"] == "BLOOM ENERGY CLASS A"
    assert be["name_source"] == "ivv"
    assert be["sector"] == "Industrials"
    assert be["sub_industry"] == ""
    fox = {r["ticker"]: r for r in rows}["FOX"]
    # in the Wikipedia ticker list but with no info row: IVV's sector, mapped to GICS
    assert fox["sector"] == "Communication Services"
    assert (fox["name"], fox["name_source"]) == ("FOX CLASS B", "ivv")


def test_ivv_with_an_unreadable_wikipedia_cache_raises(monkeypatch, tmp_path):
    """Falling back to IVV names for all 503 would rewrite every sigma-alert name."""
    _serve(monkeypatch, _ivv_text())
    monkeypatch.setattr(im, "SP500_CACHE", tmp_path / "missing.json")
    with pytest.raises(im.IndexMembershipError, match="S&P 500 cache"):
        im.collect("sp500")


def test_ivv_html_shell_still_raises(monkeypatch, tmp_path):
    with pytest.raises(im.IndexMembershipError, match="HTML"):
        _ivv(monkeypatch, tmp_path, text="<!DOCTYPE html><html>app shell</html>")


def test_two_ivv_lines_normalising_to_one_ticker_raise(monkeypatch, tmp_path):
    text = _ivv_text()
    dup = next(l for l in text.splitlines() if l.startswith('"BRK B"'))
    with pytest.raises(im.IndexMembershipError, match="BRK-B"):
        _ivv(monkeypatch, tmp_path, text=text + dup.replace('"BRK B"', '"BRK.B"') + "\n")


def test_eafe_local_tickers_stay_raw(monkeypatch):
    """The normalisation is S&P 500 only: EFA's `NOVO B` is a Copenhagen local ticker."""
    extras = ('"NOVO B","NOVO NORDISK CLASS B","Health Care","Equity","1,000.00","1.0",'
              '"1,000.00","10.00","100.00","Denmark","Omx Nordic Exchange Copenhagen A/S",'
              '"DKK","1.0","DKK","-"\n')
    _serve(monkeypatch, _csv(2, extras))
    _, _, rows = im.collect("eafe")
    assert "NOVO B" in {r["ticker"] for r in rows}


def test_ivv_snapshot_caveats_describe_ivv_not_efa(out_dir, monkeypatch, tmp_path):
    _serve_ivv(monkeypatch, tmp_path)
    r = im.refresh("sp500")
    assert r["status"] == "ok" and r["as_of"] == "2026-09-21" and r["count"] == 503
    doc = json.loads((out_dir / "sp500_2026-09-21.json").read_text(encoding="utf-8"))
    assert doc["as_of_kind"] == "source" and doc["kind"] == "ishares"
    blob = " ".join(doc["caveats"]).lower()
    assert "ivv" in blob and "sampled" not in blob and "exports" in blob


# --- Codex round 1: four guards on the IVV path -------------------------------

def test_the_sp500_collect_band_is_the_PUBLIC_MIRRORS_band():
    """One band, not two. The 450 floor let a 495-truncated file replace a
    503-member list, and a transitional 506 publish non-members; the mirror's
    495-510 was the only real gate and it sits one repo downstream."""
    from reporting import sigma_export as se

    assert (im.SP500_MIN_COUNT, im.SP500_MAX_COUNT) == (495, 510)
    assert (se.SP500_MIN_COUNT, se.SP500_MAX_COUNT) == (495, 510)


def test_a_truncated_ivv_file_is_refused_at_collect(monkeypatch, tmp_path):
    with pytest.raises(im.IndexMembershipError, match="494.*495-510"):
        _ivv(monkeypatch, tmp_path, pad=479)            # 15 + 479 = 494


def test_an_over_long_ivv_file_is_refused_at_collect(monkeypatch, tmp_path):
    with pytest.raises(im.IndexMembershipError, match="511.*495-510"):
        _ivv(monkeypatch, tmp_path, pad=496)            # 15 + 496 = 511


def test_a_transitional_basket_inside_the_band_is_still_accepted(monkeypatch, tmp_path):
    """The stated residual exposure: a fund still holding an outgoing name reads
    504-510 and passes, then self-corrects on the next run."""
    _, _, rows = _ivv(monkeypatch, tmp_path, pad=491)   # 506
    assert len(rows) == 506


def test_a_renamed_exchange_column_raises_rather_than_failing_the_filter_open(
        monkeypatch, tmp_path):
    """`.get("Exchange", "")` made the NO MARKET filter fail OPEN: rename the
    column and HOLX publishes as a member with nothing reported."""
    with pytest.raises(im.IndexMembershipError, match="Exchange"):
        _ivv(monkeypatch, tmp_path, text=_rename_header("Exchange,", "Venue,"))


def test_a_renamed_asset_class_column_raises(monkeypatch, tmp_path):
    with pytest.raises(im.IndexMembershipError, match="Asset Class"):
        _ivv(monkeypatch, tmp_path, text=_rename_header("Asset Class,", "AssetClass,"))


def test_an_empty_wikipedia_info_map_is_refused_not_used_as_a_fallback(
        monkeypatch, tmp_path):
    """Tickers but no `info` (or a renamed schema) would give IVV-style names to all
    503 and rewrite the public sp500_names.json wholesale."""
    with pytest.raises(im.IndexMembershipError, match=r"3 of 503"):
        _ivv(monkeypatch, tmp_path, info_covered=0)


def test_a_join_rate_below_the_floor_is_refused_and_one_above_it_passes(
        monkeypatch, tmp_path):
    # 452 pads + the 3 real info rows = 455 of 503 = 90.5%
    _, _, rows = _ivv(monkeypatch, tmp_path, info_covered=452)
    assert sum(1 for r in rows if r["name_source"] == "wikipedia") == 455
    # 449 pads + 3 = 452 of 503 = 89.9%
    with pytest.raises(im.IndexMembershipError, match=r"452 of 503"):
        _ivv(monkeypatch, tmp_path, info_covered=449)


def test_an_older_as_of_never_moves_latest_backward(out_dir, monkeypatch, tmp_path):
    """Only the public mirror refused an older list; every snapshot consumer reads
    `sp500_latest.json` and would have been moved back a week."""
    _serve_ivv(monkeypatch, tmp_path)
    assert im.refresh("sp500")["status"] == "ok"
    latest = out_dir / "sp500_latest.json"
    before = latest.read_bytes()

    _serve_ivv(monkeypatch, tmp_path,
               text=_ivv_text().replace("Sep 21, 2026", "Sep 14, 2026"))
    r = im.refresh("sp500")
    assert r["status"] == "source_older"
    assert r["as_of"] == "2026-09-14" and r["written"] is None
    assert "2026-09-21" in r["error"]
    assert latest.read_bytes() == before
    assert not (out_dir / "sp500_2026-09-14.json").exists()


def test_the_same_as_of_is_not_a_regression(out_dir, monkeypatch, tmp_path):
    """A fund republishes one as-of for days; refusing that would be an outage."""
    _serve_ivv(monkeypatch, tmp_path)
    im.refresh("sp500")
    _serve_ivv(monkeypatch, tmp_path)
    assert im.refresh("sp500")["status"] == "ok"


def test_the_weekly_step_fails_when_a_source_went_backwards(out_dir, monkeypatch):
    import weekly_universe

    monkeypatch.setattr(im, "refresh_all",
                        lambda: [{"key": "sp500", "status": "source_older",
                                  "as_of": "2026-09-14", "count": 503, "age_days": 8,
                                  "error": "older than 2026-09-21", "written": None}])
    with pytest.raises(RuntimeError, match="degraded"):
        weekly_universe._step_index_membership()


# --- Codex round 2: the monotonicity guard was one-sided ----------------------

def test_a_future_as_of_is_refused_before_anything_is_written(out_dir, monkeypatch, tmp_path):
    """A future date is the WEDGE case, not merely a wrong one: written once, it
    becomes the floor the older-than check compares against, so every subsequent
    (correct) file is refused as `source_older` and the lane stays stuck until a
    human deletes the file. The public mirror refuses a future as_of too, so the
    S&P 500 list would simply stop updating."""
    _serve_ivv(monkeypatch, tmp_path,
               text=_ivv_text().replace("Sep 21, 2026", "Sep 30, 2026"))
    r = im.refresh("sp500", today=date(2026, 9, 22))
    assert r["status"] == "source_future"
    assert r["as_of"] == "2026-09-30" and r["written"] is None
    assert not (out_dir / "sp500_latest.json").exists()
    assert not (out_dir / "sp500_2026-09-30.json").exists()


def test_one_day_ahead_is_tolerated_because_the_fund_dates_in_its_own_timezone(
        out_dir, monkeypatch, tmp_path):
    """iShares stamps a US-Eastern trade date; a run either side of midnight local
    must not read that as a corrupt file. One day, and no more."""
    _serve_ivv(monkeypatch, tmp_path,
               text=_ivv_text().replace("Sep 21, 2026", "Sep 23, 2026"))
    assert im.refresh("sp500", today=date(2026, 9, 22))["status"] == "ok"


def test_the_future_guard_covers_every_index_not_just_sp500(out_dir, monkeypatch):
    """EAFE and the Russell lanes had the same hole and no mirror behind them."""
    _serve(monkeypatch, _csv(500).replace("Sep 04, 2026", "Dec 04, 2027"))
    r = im.refresh("eafe", today=date(2026, 9, 22))
    assert r["status"] == "source_future" and r["written"] is None
    assert not (out_dir / "eafe_latest.json").exists()


def test_the_weekly_step_fails_on_a_future_dated_source(out_dir, monkeypatch):
    import weekly_universe

    monkeypatch.setattr(im, "refresh_all",
                        lambda: [{"key": "eafe", "status": "source_future",
                                  "as_of": "2027-12-04", "count": 500, "age_days": -438,
                                  "error": "dated in the future", "written": None}])
    with pytest.raises(RuntimeError, match="degraded"):
        weekly_universe._step_index_membership()


# --- Codex round 3 -------------------------------------------------------------

def test_a_future_dated_CACHED_snapshot_is_not_a_usable_fallback(out_dir, monkeypatch):
    """The round-2 guard sat on the SUCCESS path only. A snapshot already on disk with
    a future as_of plus a failing fetch computes a NEGATIVE age, which is never greater
    than STALE_DAYS, so it classified as an ordinary `stale` fallback and the weekly
    step read it as consumable — the wedge, serving the corrupt file indefinitely."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eafe_latest.json").write_text(json.dumps(
        {"key": "eafe", "as_of": "2027-12-04", "count": 500, "stale_days": 45,
         "holdings": [{"ticker": "T0"}]}), encoding="utf-8")
    _fail(monkeypatch)

    r = im.refresh("eafe", today=date(2026, 9, 22))
    assert r["status"] == "source_future"
    assert r["as_of"] == "2027-12-04" and r["written"] is None
    assert "fallback" in r["error"] or "future" in r["error"]


def test_a_cached_snapshot_inside_the_tolerance_is_still_a_fallback(out_dir, monkeypatch):
    """The guard must not become the outage: one day ahead is the tolerated case."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _cached(out_dir, key="eafe", as_of="2026-09-23")      # 500 holdings, weighted
    _fail(monkeypatch)
    assert im.refresh("eafe", today=date(2026, 9, 22))["status"] == "stale"


def test_a_rollback_to_the_wikipedia_cache_restores_ITS_caveats(
        out_dir, monkeypatch, tmp_path):
    """The caveats describe the SOURCE, so they follow the kind actually used. Keyed on
    `sp500` alone, the documented rollback to `cm_cache` would keep telling every
    consumer the file was full-replication IVV holdings with a fund-stated date."""
    cache = tmp_path / "sp500.json"
    cache.write_text(json.dumps({
        "_cached_at": "2026-09-18T14:09:24+00:00",
        "data": {"tickers": [f"T{i}" for i in range(500)],
                 "info": {"T0": {"Company Name": "Zero Inc"}}}}), encoding="utf-8")
    monkeypatch.setattr(im, "SP500_CACHE", cache)
    monkeypatch.setitem(im.SOURCES, "sp500",
                        {"kind": "cm_cache", "floor": 450, "index": "S&P 500",
                         "fund": "Wikipedia constituent list"})

    r = im.refresh("sp500", today=date(2026, 9, 22))
    assert r["status"] == "ok"
    doc = json.loads((out_dir / "sp500_latest.json").read_text(encoding="utf-8"))
    assert doc["as_of_kind"] == "observed" and doc["kind"] == "cm_cache"
    blob = " ".join(doc["caveats"]).lower()
    assert "wikipedia" in blob and "observed" in blob
    assert "ivv" not in blob and "no market" not in blob


# --- Codex round 4 -------------------------------------------------------------

def test_a_header_with_stray_whitespace_still_filters(monkeypatch, tmp_path):
    """The required-column check STRIPPED the header names while DictReader did not,
    so `Exchange ` passed validation and then every parsed exchange was blank: the
    NO MARKET filter matched nothing, HOLX rode through, and 504 rows still sat inside
    the 495-510 band. Validation and parsing must read the SAME normalised header."""
    got = {r["ticker"] for r in _ivv(
        monkeypatch, tmp_path,
        text=_rename_header("Location,Exchange,", "Location , Exchange ,"))[2]}
    assert "HOLX" not in got
    assert {"BE", "ILMN", "P"} <= got


def test_stray_whitespace_around_asset_class_still_drops_cash_and_futures(
        monkeypatch, tmp_path):
    rows = _ivv(monkeypatch, tmp_path,
                text=_rename_header("Asset Class,", " Asset Class ,"))[2]
    got = {r["ticker"] for r in rows}
    assert not got & {"XTSLA", "USD", "SGAFT", "ESZ6"}
    assert len(rows) == 503


# --- Codex round 5 -------------------------------------------------------------

def test_a_row_shorter_than_the_header_raises_rather_than_blanking_fields(
        monkeypatch, tmp_path):
    """Validating the HEADER is not validating the ROWS. A response truncated
    mid-record left HOLX with a Ticker and `Asset Class=Equity` but no Exchange, so
    the unlisted filter saw "" and kept it -- and 504 rows still cleared the count
    band and the join floor, publishing a non-member."""
    text = _ivv_text()
    short = '"HOLX","HOLOGIC INC","Health Care","Equity","1.00"\n'
    text = text.replace(
        next(l + "\n" for l in text.splitlines() if l.startswith('"HOLX"')), short)
    with pytest.raises(im.IndexMembershipError, match="5 field"):
        _ivv(monkeypatch, tmp_path, text=text)


def test_a_row_longer_than_the_header_raises_too(monkeypatch, tmp_path):
    text = _ivv_text()
    line = next(l for l in text.splitlines() if l.startswith('"MMM"'))
    with pytest.raises(im.IndexMembershipError, match="field"):
        _ivv(monkeypatch, tmp_path, text=text.replace(line, line + ',"EXTRA"'))


def test_blank_lines_are_still_ignored(monkeypatch, tmp_path):
    rows = _ivv(monkeypatch, tmp_path, text=_ivv_text() + "\n\n")[2]
    assert len(rows) == 503


# --- Codex round 6 -------------------------------------------------------------

def test_a_body_cut_inside_the_last_record_raises(monkeypatch, tmp_path):
    """A cut inside the final QUOTED field yields full-width records for everything
    before it, so the field-count guard never fires: 496 constituents would clear the
    495-510 band with seven names silently missing. Measured on the live files
    2026-09-22 (IVV 83,277 bytes, EFA 115,972): both end with a newline after the last
    record, so a body that does not is a body that was cut."""
    with pytest.raises(im.IndexMembershipError, match="truncat"):
        im.parse_holdings(_ivv_text()[:-30])


def test_a_body_with_no_terminating_newline_raises_for_every_fund(monkeypatch):
    with pytest.raises(im.IndexMembershipError, match="truncat"):
        im.parse_holdings(_csv(3).rstrip("\n"))


def test_a_complete_body_is_accepted_with_or_without_a_trailing_blank_line():
    """The live files end `...\n\n`; one newline is equally complete."""
    assert im.parse_holdings(_csv(3))[1]
    assert im.parse_holdings(_csv(3) + "\n")[1]


def test_an_incomplete_chunked_read_is_reported_not_crashed(monkeypatch):
    """http.client.IncompleteRead is NOT an OSError, so a truncated chunked response
    escaped the fetch's own except clause as an unhandled exception mid-build. Both
    endpoints answer `Transfer-Encoding: chunked` with no Content-Length (measured
    2026-09-22), so this is the transport's only truncation signal."""
    import http.client
    import urllib.request as _u

    def _boom(*a, **k):
        raise http.client.IncompleteRead(b"partial")

    monkeypatch.setattr(_u, "urlopen", _boom)
    with pytest.raises(im.IndexMembershipError, match="truncat"):
        im._fetch_csv("239726")


def test_a_vanguard_as_of_that_is_not_a_calendar_date_raises(monkeypatch):
    """Dates were compared as STRINGS and Vanguard's value was only sliced, so
    `2026-09-00T00:00:00-04:00` passed the future check, passed the older-than check,
    and archived `r1000_2026-09-00.json` as latest with age_days None."""
    import time
    import urllib.request as _u

    def _fake(req, timeout=60):
        return _JsonResp(_vanguard_page(600, 1, 600, as_of="2026-09-00"))

    monkeypatch.setattr(_u, "urlopen", _fake)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    with pytest.raises(im.IndexMembershipError, match="2026-09-00"):
        im._fetch_vanguard("VONE")


def test_a_cm_cache_stamp_that_is_not_a_calendar_date_raises(monkeypatch, tmp_path):
    cache = tmp_path / "sp500.json"
    cache.write_text(json.dumps({"_cached_at": "2026-09-00T19:01:55+00:00",
                                 "data": {"tickers": ["A"], "info": {}}}),
                     encoding="utf-8")
    monkeypatch.setattr(im, "SP500_CACHE", cache)
    with pytest.raises(im.IndexMembershipError, match="2026-09-00"):
        im._load_cm_sp500()


def test_the_future_and_older_checks_compare_PARSED_dates(out_dir, monkeypatch, tmp_path):
    """A string comparison reads `2026-9-21` as older than `2026-09-08`."""
    assert im.is_future_as_of("2026-09-30", date(2026, 9, 22)) is True
    assert im.is_future_as_of("2026-09-23", date(2026, 9, 22)) is False
    with pytest.raises(im.IndexMembershipError, match="2026-09-31"):
        im.parse_as_of("2026-09-31", "test")


# --- Codex round 7 -------------------------------------------------------------

def test_a_duplicate_column_name_is_refused(monkeypatch, tmp_path):
    """`dict(zip(...))` keeps the LAST value, so a second (blank) `Exchange` column
    passes both the required-column check and the row-width check while blanking
    "NO MARKET (E.G. UNLISTED)": HOLX survives and a 504-row basket clears the band
    and the join floor. A header that names one field twice is not a header we can
    read."""
    with pytest.raises(im.IndexMembershipError, match="Exchange"):
        _ivv(monkeypatch, tmp_path, text=_rename_header("Location,", "Exchange,"))


def test_the_duplicate_check_names_every_repeat(monkeypatch, tmp_path):
    with pytest.raises(im.IndexMembershipError, match="duplicate"):
        _ivv(monkeypatch, tmp_path, text=_rename_header("Name,", "Ticker,"))


# --- Codex round 8 -------------------------------------------------------------

@pytest.mark.parametrize("cell", ["", "-"])
def test_an_equity_row_with_no_ticker_raises_instead_of_being_dropped(
        monkeypatch, tmp_path, cell):
    """The row was silently DISCARDED, so the mirror's no-blank-ticker guard never saw
    it: an otherwise complete file with AAPL's ticker blank yields a 502-member basket
    that clears the band and the join, reports success, and publishes without Apple.
    Measured 2026-09-22: neither IVV nor EFA has a single Equity row with a blank or
    `-` ticker, so this is never a normal shape."""
    line = next(l for l in _ivv_text().splitlines() if l.startswith('"AAPL"'))
    text = _ivv_text().replace(line, line.replace('"AAPL"', f'"{cell}"', 1))
    with pytest.raises(im.IndexMembershipError, match="ticker"):
        _ivv(monkeypatch, tmp_path, text=text)


def test_a_non_equity_row_with_no_ticker_is_still_just_skipped(monkeypatch, tmp_path):
    """The cash and futures lines legitimately carry `-`; only a CONSTITUENT must
    have a symbol, or the guard becomes the outage."""
    rows = _ivv(monkeypatch, tmp_path)[2]
    assert len(rows) == 503


def _wiki_missing_field(tmp_path, monkeypatch, drop):
    """A cache whose entries omit one GICS field for every padded name."""
    pads = _pad_tickers()
    info = {}
    for t in pads:
        entry = {"Company Name": f"Pad {t} Inc", "GICS Sector": "Industrials",
                 "GICS Sub-Industry": "Widgets"}
        entry.pop(drop)
        info[t] = entry
    _wiki_cache(tmp_path, monkeypatch, tickers=WIKI_TICKERS + pads, info=info)
    _serve(monkeypatch, _ivv_text() + _pad_rows())


def test_the_join_threshold_covers_every_field_the_join_PROMISES(monkeypatch, tmp_path):
    """It counted company NAMES only, so renaming `GICS Sub-Industry` to
    `GICS Sub Industry` in the cache reported 503/503 joined and wrote a snapshot with
    every sub_industry blank. Each field the join claims to provide is checked."""
    _wiki_missing_field(tmp_path, monkeypatch, "GICS Sub-Industry")
    with pytest.raises(im.IndexMembershipError, match="sub_industry"):
        im.collect("sp500")


def test_a_missing_sector_field_is_named_too(monkeypatch, tmp_path):
    _wiki_missing_field(tmp_path, monkeypatch, "GICS Sector")
    with pytest.raises(im.IndexMembershipError, match="sector"):
        im.collect("sp500")


# --- Codex round 9 -------------------------------------------------------------

def test_two_fund_holdings_as_of_headers_refuse_and_name_both(monkeypatch, tmp_path):
    """The parser took the FIRST as-of line and never looked further, so a response
    carrying `Sep 25, 2026` followed by a corrective `Sep 18, 2026` -- with the OLDER
    basket beneath it -- was stamped Sep 25, sailed past the strictly-newer guard, and
    published older membership as current. Same rule as the mirror's header."""
    text = _ivv_text().replace(
        'Fund Holdings as of,"Sep 21, 2026"',
        'Fund Holdings as of,"Sep 25, 2026"\nFund Holdings as of,"Sep 18, 2026"')
    with pytest.raises(im.IndexMembershipError, match="Sep 25, 2026"):
        _ivv(monkeypatch, tmp_path, text=text)


def test_the_count_BAND_applies_to_the_sp500_key_under_ANY_source_kind(
        out_dir, monkeypatch, tmp_path):
    """The band lived inside the IVV transform, which the documented cm_cache rollback
    returns before -- so the rollback, the moment the band is most needed, wrote a
    450-member sp500_latest.json as `ok` under the generic 450 floor."""
    cache = tmp_path / "sp500.json"
    cache.write_text(json.dumps({
        "_cached_at": "2026-09-18T14:09:24+00:00",
        "data": {"tickers": [f"T{i}" for i in range(450)],
                 "info": {f"T{i}": {"Company Name": f"Co {i}",
                                    "GICS Sector": "Industrials",
                                    "GICS Sub-Industry": "Widgets"} for i in range(450)}}}),
        encoding="utf-8")
    monkeypatch.setattr(im, "SP500_CACHE", cache)
    monkeypatch.setitem(im.SOURCES, "sp500",
                        {"kind": "cm_cache", "floor": 450, "index": "S&P 500",
                         "fund": "Wikipedia constituent list"})

    with pytest.raises(im.IndexMembershipError, match="450.*495-510"):
        im.collect("sp500")
    assert not (out_dir / "sp500_latest.json").exists()


def test_the_rollback_still_works_at_a_plausible_count(out_dir, monkeypatch, tmp_path):
    """The band must not become the outage: a real Wikipedia list still publishes."""
    cache = tmp_path / "sp500.json"
    cache.write_text(json.dumps({
        "_cached_at": "2026-09-18T14:09:24+00:00",
        "data": {"tickers": [f"T{i}" for i in range(503)],
                 "info": {f"T{i}": {"Company Name": f"Co {i}",
                                    "GICS Sector": "Industrials",
                                    "GICS Sub-Industry": "Widgets"} for i in range(503)}}}),
        encoding="utf-8")
    monkeypatch.setattr(im, "SP500_CACHE", cache)
    monkeypatch.setitem(im.SOURCES, "sp500",
                        {"kind": "cm_cache", "floor": 450, "index": "S&P 500",
                         "fund": "Wikipedia constituent list"})
    r = im.refresh("sp500", today=date(2026, 9, 22))
    assert r["status"] == "ok" and r["count"] == 503


# --- Codex round 10: what a real weekly run hits --------------------------------

def test_a_blank_exchange_on_an_equity_row_refuses(monkeypatch, tmp_path):
    """If iShares keeps the Exchange column but BLANKS it, the
    "NO MARKET (E.G. UNLISTED)" test matches nothing -- the fleet's
    a-flag-that-is-always-FALSE shape -- so HOLX rides through and 504 rows clear the
    band and the join. Measured 2026-09-22: 0 of 504 IVV and 0 of 658 EFA equity rows
    carry a blank exchange, so every constituent having one is the real rule."""
    line = next(l for l in _ivv_text().splitlines() if l.startswith('"MMM"'))
    with pytest.raises(im.IndexMembershipError, match="[Ee]xchange"):
        _ivv(monkeypatch, tmp_path, text=_ivv_text().replace(
            line, line.replace('"NYSE"', '""')))


def test_a_weightless_but_structurally_valid_response_fails_the_lane(
        out_dir, monkeypatch, tmp_path):
    """Weights are half the reason this archive exists and cannot be backfilled. A
    503-row file whose `Weight (%)` cells are all `-` passed every gate: _num returned
    None for each, equity_weight_pct was None, and that date was archived WITHOUT
    weights -- permanently, since a dated file is written once."""
    import csv as _csvmod
    import io as _io

    lines = (_ivv_text() + _pad_rows()).splitlines()
    h = next(i for i, l in enumerate(lines) if l.startswith("Ticker,"))
    buf = _io.StringIO()
    w = _csvmod.writer(buf, quoting=_csvmod.QUOTE_ALL, lineterminator="\n")
    for raw in _csvmod.reader(_io.StringIO("\n".join(lines[h + 1:]))):
        if not raw:
            continue
        raw[5] = "-"                       # every Weight (%) cell unusable
        w.writerow(raw)
    text = "\n".join(lines[:h + 1]) + "\n" + buf.getvalue()
    _serve_ivv(monkeypatch, tmp_path)          # wires the Wikipedia cache
    _serve(monkeypatch, text)                  # ... then the weightless body
    with pytest.raises(im.IndexMembershipError, match="weight"):
        im.refresh("sp500", today=date(2026, 9, 22))
    assert not (out_dir / "sp500_latest.json").exists()


def test_a_weight_total_far_from_100_refuses(out_dir, monkeypatch, tmp_path):
    """Live totals 2026-09-22: IVV 99.92, EFA 99.48, Russell 97.44-99.80. A basket
    summing to a third of the fund is a different fact, not a rounding difference."""
    _serve_ivv(monkeypatch, tmp_path)
    real = im.parse_holdings

    def third(text):
        as_of, rows = real(text)
        for r in rows:
            if r["weight_pct"] is not None:
                r["weight_pct"] = r["weight_pct"] / 3
        return as_of, rows

    monkeypatch.setattr(im, "parse_holdings", third)
    with pytest.raises(im.IndexMembershipError, match="weight"):
        im.refresh("sp500", today=date(2026, 9, 22))


def test_the_weight_rule_exempts_a_source_that_publishes_none(out_dir, monkeypatch, tmp_path):
    """⛑ The cm_cache rollback carries NO weights BY DESIGN (a constituent list is not
    a weighted index). The guard must not become the outage for it."""
    cache = tmp_path / "sp500.json"
    cache.write_text(json.dumps({
        "_cached_at": "2026-09-18T14:09:24+00:00",
        "data": {"tickers": [f"T{i}" for i in range(503)],
                 "info": {f"T{i}": {"Company Name": f"Co {i}", "GICS Sector": "Industrials",
                                    "GICS Sub-Industry": "Widgets"} for i in range(503)}}}),
        encoding="utf-8")
    monkeypatch.setattr(im, "SP500_CACHE", cache)
    monkeypatch.setitem(im.SOURCES, "sp500",
                        {"kind": "cm_cache", "floor": 450, "index": "S&P 500",
                         "fund": "Wikipedia constituent list"})
    assert im.refresh("sp500", today=date(2026, 9, 22))["status"] == "ok"


def test_a_corrupt_dated_snapshot_is_REWRITTEN_not_treated_as_written(
        out_dir, monkeypatch, tmp_path):
    """An interrupted first write leaves a truncated file; the next valid run saw only
    that the PATH EXISTS, skipped it, wrote latest and returned ok -- so the sole
    historical snapshot for that date stayed corrupt for ever. Immutability protects a
    GOOD file, not a byte count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dated = out_dir / "sp500_2026-09-21.json"
    dated.write_text('{"key": "sp500", "count": 503, "holdi', encoding="utf-8")

    _serve_ivv(monkeypatch, tmp_path)
    r = im.refresh("sp500", today=date(2026, 9, 22))
    assert r["status"] == "ok" and r["written"] == str(dated)
    doc = json.loads(dated.read_text(encoding="utf-8"))
    assert doc["key"] == "sp500" and doc["count"] == 503 and len(doc["holdings"]) == 503


def test_a_GOOD_dated_snapshot_is_still_never_rewritten(out_dir, monkeypatch, tmp_path):
    """The archive's whole value is that a past file says what it said at the time."""
    _serve_ivv(monkeypatch, tmp_path)
    im.refresh("sp500", today=date(2026, 9, 22))
    dated = out_dir / "sp500_2026-09-21.json"
    before = dated.read_bytes()
    dated.write_bytes(before.replace(b'"count": 503', b'"count": 503 '))  # same doc, new bytes
    marker = dated.read_bytes()

    _serve_ivv(monkeypatch, tmp_path)
    r = im.refresh("sp500", today=date(2026, 9, 22))
    assert r["written"] is None
    assert dated.read_bytes() == marker


def test_a_dropbox_locked_rename_still_lands_the_file(out_dir, monkeypatch, tmp_path):
    """⛑ `os.replace` needs DELETE on the target and Dropbox denies it (WinError 5),
    while an in-place write still succeeds -- measured on this machine 2026-08-21. The
    fallback must leave no half-written file that a later run would trust."""
    import os as _os

    def _denied(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(_os, "replace", _denied)
    _serve_ivv(monkeypatch, tmp_path)
    r = im.refresh("sp500", today=date(2026, 9, 22))
    assert r["status"] == "ok"
    doc = json.loads((out_dir / "sp500_latest.json").read_text(encoding="utf-8"))
    assert doc["count"] == 503 and len(doc["holdings"]) == 503
    assert not list(out_dir.glob("*.new"))          # no orphan temp left behind


def test_a_write_that_cannot_be_read_back_raises(out_dir, monkeypatch, tmp_path):
    """Validate by RE-READING: a write that reports success and produces an unreadable
    file must not be reported as an archived snapshot."""
    _serve_ivv(monkeypatch, tmp_path)
    real_write = im._write_snapshot_bytes

    def _truncating(path, text):
        real_write(path, text[: len(text) // 2])

    monkeypatch.setattr(im, "_write_snapshot_bytes", _truncating)
    with pytest.raises(im.IndexMembershipError, match="read back"):
        im.refresh("sp500", today=date(2026, 9, 22))


def test_a_basket_where_a_FIFTH_of_the_rows_carry_no_weight_refuses(
        out_dir, monkeypatch, tmp_path):
    """The total rule alone cannot see this: rescale the rows that DO carry a weight
    and the fund still sums to ~100 while a fifth of the archive's rows are weightless
    -- and those rows are exactly the ones a later analysis would silently drop."""
    import csv as _csvmod
    import io as _io

    lines = (_ivv_text() + _pad_rows()).splitlines()
    h = next(i for i, l in enumerate(lines) if l.startswith("Ticker,"))
    rows = [r for r in _csvmod.reader(_io.StringIO("\n".join(lines[h + 1:]))) if r]
    blank = [i for i, r in enumerate(rows) if i % 5 == 0]
    each = round(100.0 / (len(rows) - len(blank)), 4)
    buf = _io.StringIO()
    w = _csvmod.writer(buf, quoting=_csvmod.QUOTE_ALL, lineterminator="\n")
    for i, r in enumerate(rows):
        r[5] = "-" if i in blank else str(each)
        w.writerow(r)
    text = "\n".join(lines[:h + 1]) + "\n" + buf.getvalue()

    _serve_ivv(monkeypatch, tmp_path)          # wires the Wikipedia cache
    _serve(monkeypatch, text)
    with pytest.raises(im.IndexMembershipError, match="carry a usable weight"):
        im.refresh("sp500", today=date(2026, 9, 22))


# --- Codex round 11: an invariant applied on ONE path is applied nowhere --------

def _cached(out_dir, key="eafe", *, weights=True, kind="ishares", n=500,
            as_of="2026-09-20", ticker_blank=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    holdings = [{"ticker": f"T{i}", "name": f"Co {i}", "sector": "Health Care",
                 "sub_industry": "Widgets", "name_source": "wikipedia",
                 "weight_pct": round(100.0 / n, 4) if weights else None}
                for i in range(n)]
    if ticker_blank:
        holdings[0]["ticker"] = ""
    (out_dir / f"{key}_latest.json").write_text(json.dumps(
        {"key": key, "kind": kind, "as_of": as_of, "count": n, "stale_days": 45,
         "holdings": holdings}), encoding="utf-8")


def test_a_WEIGHTLESS_cached_snapshot_is_not_a_usable_fallback(out_dir, monkeypatch):
    """Round 10's weight rule ran only on a fresh fetch. A one-day-old ishares snapshot
    with 503 valid tickers and every weight null, plus IVV answering HTML-with-200,
    reported `stale`, kept the weekly step green, and left the WEIGHTED archive
    unusable -- the same one-path shape as the round-3 future-date gap."""
    _cached(out_dir, weights=False)
    _fail(monkeypatch)
    r = im.refresh("eafe", today=date(2026, 9, 22))
    assert r["status"] == "cache_unusable"
    assert "weight" in r["error"]


def test_a_weighted_cached_snapshot_is_still_a_fallback(out_dir, monkeypatch):
    """The guard must not become the outage: a real cached snapshot still serves."""
    _cached(out_dir, weights=True)
    _fail(monkeypatch)
    assert im.refresh("eafe", today=date(2026, 9, 22))["status"] == "stale"


def test_the_cache_rule_exempts_cm_cache_which_publishes_no_weights(
        out_dir, monkeypatch, tmp_path):
    """A scraped constituent list is not a weighted index -- exempt on EVERY path."""
    monkeypatch.setitem(im.SOURCES, "sp500",
                        {"kind": "cm_cache", "floor": 450, "index": "S&P 500",
                         "fund": "Wikipedia constituent list"})
    _cached(out_dir, key="sp500", weights=False, kind="cm_cache", n=503)
    monkeypatch.setattr(im, "SP500_CACHE", tmp_path / "missing.json")
    assert im.refresh("sp500", today=date(2026, 9, 22))["status"] == "stale"


def test_a_cached_snapshot_with_a_blank_ticker_is_not_usable(out_dir, monkeypatch):
    _cached(out_dir, ticker_blank=True)
    _fail(monkeypatch)
    assert im.refresh("eafe", today=date(2026, 9, 22))["status"] == "cache_unusable"


def test_a_cached_sp500_outside_the_count_band_is_not_usable(out_dir, monkeypatch):
    """The band is a fact about the index, so it binds a cached copy too."""
    _cached(out_dir, key="sp500", n=450, kind="ishares")
    _fail(monkeypatch)
    assert im.refresh("sp500", today=date(2026, 9, 22))["status"] == "cache_unusable"


def test_the_weekly_step_fails_on_an_unusable_cache(out_dir, monkeypatch):
    import weekly_universe

    monkeypatch.setattr(im, "refresh_all",
                        lambda: [{"key": "eafe", "status": "cache_unusable",
                                  "as_of": "2026-09-20", "count": 500, "age_days": 2,
                                  "error": "no usable weights", "written": None}])
    with pytest.raises(RuntimeError, match="degraded"):
        weekly_universe._step_index_membership()


def test_a_weightless_DATED_file_is_rewritten_when_a_good_fetch_arrives(
        out_dir, monkeypatch, tmp_path):
    """`_snapshot_is_usable` checked only key and row count, so the weightless dated
    file for that as_of counted as a valid immutable record: `latest` was repaired and
    the ARCHIVE -- the thing that cannot be re-fetched -- never was."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dated = out_dir / "sp500_2026-09-21.json"
    dated.write_text(json.dumps(
        {"key": "sp500", "kind": "ishares", "as_of": "2026-09-21", "count": 503,
         "stale_days": 45,
         "holdings": [{"ticker": f"T{i}", "name": f"Co {i}", "sector": "Health Care",
                       "sub_industry": "Widgets", "name_source": "wikipedia",
                       "weight_pct": None} for i in range(503)]}), encoding="utf-8")

    _serve_ivv(monkeypatch, tmp_path)
    r = im.refresh("sp500", today=date(2026, 9, 22))
    assert r["status"] == "ok" and r["written"] == str(dated)
    doc = json.loads(dated.read_text(encoding="utf-8"))
    assert sum(1 for h in doc["holdings"] if h["weight_pct"] is not None) == 503


# --- Codex round 12 (A): floors on every path, sentinels, a dated file's own date ---

def test_a_cached_doc_UNDER_ITS_INDEX_FLOOR_is_not_a_usable_fallback(out_dir, monkeypatch):
    """`snapshot_problem` carried only the S&P 500 band, so the EAFE and Russell floors
    existed on the fresh-fetch path alone: a recent cached EAFE doc holding ONE ticker
    at 100% returned `stale` and the weekly step stayed green."""
    _cached(out_dir, key="eafe", n=1)
    _fail(monkeypatch)
    r = im.refresh("eafe", today=date(2026, 9, 22))
    assert r["status"] == "cache_unusable"
    assert "floor" in r["error"]


def test_a_truncated_dated_file_under_the_floor_is_rewritten(out_dir, monkeypatch):
    """Same rule, other path: an under-floor dated file is not an immutable record."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dated = out_dir / "eafe_2026-09-04.json"
    dated.write_text(json.dumps(
        {"key": "eafe", "kind": "ishares", "as_of": "2026-09-04", "count": 2,
         "holdings": [{"ticker": "T0", "weight_pct": 50.0},
                      {"ticker": "T1", "weight_pct": 50.0}]}), encoding="utf-8")
    _serve(monkeypatch, _csv(500))
    r = im.refresh("eafe", today=date(2026, 9, 22))
    assert r["status"] == "ok" and r["written"] == str(dated)
    assert json.loads(dated.read_text(encoding="utf-8"))["count"] == 500


def test_a_sentinel_exchange_is_MISSING_not_a_venue(monkeypatch, tmp_path):
    """`Exchange="-"` counted as a populated venue, so the unlisted filter never fired
    for it: pair it with a blanked Asset Class elsewhere and you get 503 rows, ~100%
    weights and passing joins -- with HOLX in and MMM out."""
    line = next(l for l in _ivv_text().splitlines() if l.startswith('"HOLX"'))
    text = _ivv_text().replace(line, line.replace(
        '"NO MARKET (E.G. UNLISTED)"', '"-"'))
    with pytest.raises(im.IndexMembershipError, match="[Ee]xchange"):
        _ivv(monkeypatch, tmp_path, text=text)


@pytest.mark.parametrize("cell", ["", "-", "N/A"])
def test_a_missing_asset_class_on_a_real_row_refuses(monkeypatch, tmp_path, cell):
    """A blank read as `not Equity`, so the row was DROPPED: blank MMM's asset class
    and 3M silently leaves the S&P 500 while every count still looks right."""
    line = next(l for l in _ivv_text().splitlines() if l.startswith('"MMM"'))
    text = _ivv_text().replace(line, line.replace('"Equity"', f'"{cell}"'))
    with pytest.raises(im.IndexMembershipError, match="[Aa]sset [Cc]lass"):
        _ivv(monkeypatch, tmp_path, text=text)


def test_a_dated_file_holding_ANOTHER_DATE_is_not_a_valid_snapshot(
        out_dir, monkeypatch, tmp_path):
    """Nothing bound the document to the date in its filename, so a complete Sep 14 doc
    stored as `sp500_2026-09-21.json` counted as usable: the good Sep 21 fetch returned
    ok with written=None and the Sep 21 archive held Sep 14 membership for ever."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dated = out_dir / "sp500_2026-09-21.json"
    _cached(out_dir, key="sp500", n=503, as_of="2026-09-14")
    dated.write_text((out_dir / "sp500_latest.json").read_text(encoding="utf-8"),
                     encoding="utf-8")
    (out_dir / "sp500_latest.json").unlink()

    _serve_ivv(monkeypatch, tmp_path)
    r = im.refresh("sp500", today=date(2026, 9, 22))
    assert r["status"] == "ok" and r["written"] == str(dated)
    assert json.loads(dated.read_text(encoding="utf-8"))["as_of"] == "2026-09-21"


def test_the_write_back_check_proves_the_BYTES_just_written(out_dir, monkeypatch, tmp_path):
    """Key and count are satisfied by any document of the same shape -- including the
    one already on disk. The verification compares what is there with what was sent."""
    _serve_ivv(monkeypatch, tmp_path)
    real_write = im._write_snapshot_bytes

    def _writes_something_else(path, text):
        doc = json.loads(text)
        doc["holdings"][0]["ticker"] = "SWAPPED"
        real_write(path, json.dumps(doc, indent=1))

    monkeypatch.setattr(im, "_write_snapshot_bytes", _writes_something_else)
    with pytest.raises(im.IndexMembershipError, match="read back"):
        im.refresh("sp500", today=date(2026, 9, 22))


# --- Codex round 12 (B): pagination across a rebalance, and per-index isolation ---

def _paged(monkeypatch, pages):
    """Serve `pages` in order to `_fetch_vanguard`, one per request."""
    import time
    import urllib.request as _u

    seen = iter(pages)

    def _fake(req, timeout=60):
        return _JsonResp(next(seen))

    monkeypatch.setattr(_u, "urlopen", _fake)
    monkeypatch.setattr(time, "sleep", lambda *_: None)


def test_pages_dated_DIFFERENTLY_are_not_one_snapshot(monkeypatch):
    """`asOfDate` was read from the FIRST page only, so a fetch straddling a monthly
    republish combined an Aug 31 page with a Sep 30 page into one snapshot stamped
    Aug 31 -- half its membership from September, and no way to tell later."""
    _paged(monkeypatch, [_vanguard_page(500, 1, 1000, as_of="2026-08-31"),
                         _vanguard_page(500, 501, 1000, as_of="2026-09-30")])
    with pytest.raises(im.IndexMembershipError, match="2026-09-30"):
        im._fetch_vanguard("VONE")


def test_pages_reporting_a_DIFFERENT_SIZE_are_not_one_snapshot(monkeypatch):
    """The same republish shows up as a changed `size`; one fund cannot have two."""
    _paged(monkeypatch, [_vanguard_page(500, 1, 1000, as_of="2026-08-31"),
                         _vanguard_page(500, 501, 1200, as_of="2026-08-31")])
    with pytest.raises(im.IndexMembershipError, match="size"):
        im._fetch_vanguard("VONE")


def test_consistent_pages_still_paginate(monkeypatch):
    _paged(monkeypatch, [_vanguard_page(500, 1, 1000, as_of="2026-08-31"),
                         _vanguard_page(500, 501, 1000, as_of="2026-08-31"),
                         _vanguard_page(0, 1001, 1000, as_of="2026-08-31")])
    as_of, rows = im._fetch_vanguard("VONE")
    assert as_of == "2026-08-31" and len(rows) == 1000


@pytest.mark.parametrize("payload", [
    {"fund": ["maintenance"]},
    {"fund": {"entity": "maintenance"}},
    {"fund": {"entity": [["VONE", 1]]}},
    {"fund": None, "size": 1000},
])
def test_an_unexpected_json_SHAPE_is_an_IndexMembershipError(monkeypatch, payload):
    """A valid HTTP-200 body of the wrong shape raised a raw AttributeError/TypeError,
    which `refresh_all` does not catch."""
    _paged(monkeypatch, [payload])
    with pytest.raises(im.IndexMembershipError):
        im._fetch_vanguard("VONE")


def test_refresh_all_isolates_ANY_exception_not_just_ours(out_dir, monkeypatch):
    """⛑ The loop caught IndexMembershipError only, so one raw exception at r1000 took
    r2000, r3000, eafe and sp500 down with it -- the module's stated per-index
    independence, undone by an error type. A week not captured cannot be recaptured."""
    def _collect(key):
        if key == "r1000":
            raise AttributeError("'list' object has no attribute 'get'")
        n = 503 if key == "sp500" else im.SOURCES[key]["floor"] + 50
        return "2026-09-08", "source", [
            {"ticker": f"T{i}", "name": "x", "sector": "", "weight_pct": round(100 / n, 4),
             "location": "", "exchange": "NYSE", "market_currency": "",
             "market_value_usd": None} for i in range(n)]

    monkeypatch.setattr(im, "collect", _collect)
    results = {r["key"]: r["status"] for r in im.refresh_all(today=date(2026, 9, 22))}
    assert results["r1000"] == "failed"
    assert results["eafe"] == results["sp500"] == results["r2000"] == "ok"


def test_an_isolated_failure_still_names_its_error(out_dir, monkeypatch):
    def _collect(key):
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(im, "collect", _collect)
    rows = im.refresh_all(today=date(2026, 9, 22))
    assert all(r["status"] == "failed" for r in rows)
    assert all("ZeroDivisionError" in (r["error"] or "") for r in rows)


# --- Codex round 13 (A): the Vanguard path --------------------------------------

def test_an_early_empty_page_does_not_end_a_short_fetch(monkeypatch):
    """Pagination stopped on an empty page without asking whether it had REACHED the
    advertised size: five 500-row VTHR pages then an empty one returned 2,500
    holdings, cleared the 2,400 floor at ~95% of weight, and archived a snapshot
    missing 500 constituents as `ok`."""
    pages = [_vanguard_page(500, 1 + 500 * i, 3000, as_of="2026-08-31") for i in range(5)]
    pages.append(_vanguard_page(0, 2501, 3000, as_of="2026-08-31"))
    _paged(monkeypatch, pages)
    with pytest.raises(im.IndexMembershipError, match="2500.*3000|3000.*2500"):
        im._fetch_vanguard("VTHR")


@pytest.mark.parametrize("drop", ["asOfDate", "size"])
def test_a_page_that_states_no_metadata_cannot_be_matched_to_the_others(monkeypatch, drop):
    """The agreement rule only compared when a later page PROVIDED the field, so a
    second page carrying neither was accepted into an August snapshot even when it came
    from the September republish. Absent is not agreement."""
    second = _vanguard_page(500, 501, 1000, as_of="2026-08-31")
    second.pop(drop)
    _paged(monkeypatch, [_vanguard_page(500, 1, 1000, as_of="2026-08-31"), second])
    with pytest.raises(im.IndexMembershipError, match=drop):
        im._fetch_vanguard("VONE")


def test_a_vanguard_holding_with_no_ticker_raises_instead_of_vanishing(monkeypatch):
    """The Vanguard reader silently dropped a row with no ticker -- 899 of 900
    archived at ~99.9% of weight, over the floor, with nothing reported."""
    page = _vanguard_page(900, 1, 900, as_of="2026-08-31")
    page["fund"]["entity"][7]["ticker"] = ""
    _paged(monkeypatch, [page])
    with pytest.raises(im.IndexMembershipError, match="ticker"):
        im._fetch_vanguard("VONE")


@pytest.mark.parametrize("cell", ["-", "N/A"])
def test_a_vanguard_sentinel_ticker_is_missing_not_a_constituent(monkeypatch, cell):
    """`-` became a literal ticker that every later check read as populated."""
    page = _vanguard_page(900, 1, 900, as_of="2026-08-31")
    page["fund"]["entity"][3]["ticker"] = cell
    _paged(monkeypatch, [page])
    with pytest.raises(im.IndexMembershipError, match="ticker"):
        im._fetch_vanguard("VONE")
