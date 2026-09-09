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
    out = []
    for i in range(n):
        out.append(f'"T{i}","COMPANY {i}","Health Care","Equity","1,000.00","{i + 1}.5",'
                   f'"1,000.00","10.00","100.00","Japan","Tokyo","USD","1.0","JPY","-"\n')
    return "".join(out)


def _csv(n=3, extras=""):
    return REAL_PREAMBLE + HEADER + _rows(n) + extras


# --- parsing -----------------------------------------------------------------

def test_parses_as_of_and_equity_rows():
    as_of, rows = im.parse_holdings(_csv(3))
    assert as_of == "2026-09-04"
    assert [r["ticker"] for r in rows] == ["T0", "T1", "T2"]
    assert rows[0]["weight_pct"] == 1.5
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
    said so would let today's scrape claim to be a membership record for today."""
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
                                         "weight_pct": 0.1, "location": "", "exchange": "",
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
