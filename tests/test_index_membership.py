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
