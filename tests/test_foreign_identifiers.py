"""Tests for universe/foreign_identifiers.py — no live network."""

import pytest

from universe import foreign_identifiers as fi

# ── parsing ──────────────────────────────────────────────────────────────────

HOLDINGS = b"""iShares Core MSCI Total International Stock ETF
Fund Holdings as of,"Jul 27, 2026"
Inception Date,"Oct 18, 2012"

Ticker,Name,Type,Sector,Asset Class,Market Value,Weight (%),Location,Exchange,Currency,Market Currency
"4503","ASTELLAS PHARMA INC","EQUITY","Health Care","Equity","1","0.1","Japan","Tokyo Stock Exchange","USD","JPY"
"1801","INNOVENT BIOLOGICS INC","EQUITY","Health Care","Equity","1","0.1","China","Hong Kong Exchanges","USD","HKD"
"1801","TAISEI CORP","EQUITY","Industrials","Equity","1","0.1","Japan","Tokyo Stock Exchange","USD","JPY"
"XXXX","SOME CASH THING","CASH","-","Cash","1","0.1","-","-","USD","USD"
"""

NPORT = b"""<edgarSubmission>
<invstOrSec><name>ASTELLAS PHARMA INC</name><lei>529900IB708DY2HBBB35</lei>
<identifiers><isin value="JP3942400007"/></identifiers><invCountry>JP</invCountry></invstOrSec>
<invstOrSec><name>INNOVENT BIOLOGICS INC</name><lei>N/A</lei>
<identifiers><isin value="KYG4818G1010"/></identifiers><invCountry>KY</invCountry></invstOrSec>
<invstOrSec><name>TAISEI CORP</name><lei>3538001ZWQ4T2RJVGT44</lei>
<identifiers><isin value="JP3443600006"/></identifiers><invCountry>JP</invCountry></invstOrSec>
</edgarSubmission>"""


def test_parse_holdings_skips_preamble_and_non_equity():
    rows = fi.parse_holdings(HOLDINGS)
    assert len(rows) == 3
    assert {r["Ticker"] for r in rows} == {"4503", "1801"}


def test_parse_holdings_rejects_an_html_error_page():
    """A dead or redirected iShares URL returns HTML with HTTP 200 — that must
    never read as an empty portfolio."""
    with pytest.raises(ValueError, match="no 'Ticker,Name,' header"):
        fi.parse_holdings(b"<!DOCTYPE html><html><body>Not found</body></html>")


def test_parse_nport_extracts_isin_lei_country():
    m = fi.parse_nport(NPORT)
    assert m[fi._norm_key("ASTELLAS PHARMA INC")] == (
        "JP3942400007", "529900IB708DY2HBBB35", "JP")


def test_parse_nport_treats_na_lei_as_absent():
    """'N/A' is the filing's way of saying no LEI; storing it would look like one."""
    assert fi.parse_nport(NPORT)[fi._norm_key("INNOVENT BIOLOGICS INC")][1] == ""


def test_parse_nport_rejects_a_document_with_no_holdings():
    with pytest.raises(ValueError, match="no <invstOrSec>"):
        fi.parse_nport(b"<edgarSubmission><genInfo/></edgarSubmission>")


# ── map building ─────────────────────────────────────────────────────────────


@pytest.fixture
def mapping(monkeypatch):
    def fake(url, cache_name, *, ua, use_cache=True):
        return NPORT if cache_name.startswith("nport") else HOLDINGS

    monkeypatch.setattr(fi, "_cached_get", fake)
    monkeypatch.setattr(fi, "latest_nport_url", lambda c, s, use_cache=True: "u")
    return fi.build_map([("TestFund", "1", "2", "S1")])


def test_map_is_keyed_on_ticker_and_country(mapping):
    """`1801` is Innovent in Hong Kong AND Taisei in Tokyo. Keyed on ticker
    alone one silently overwrites the other."""
    assert mapping[("1801", "China")]["isin"] == "KYG4818G1010"
    assert mapping[("1801", "Japan")]["isin"] == "JP3443600006"
    assert mapping[("4503", "Japan")]["isin"] == "JP3942400007"


def test_map_records_failed_sources_without_discarding_others(monkeypatch):
    calls = {"n": 0}

    def flaky(url, cache_name, *, ua, use_cache=True):
        if cache_name.endswith("_BAD.csv"):
            raise RuntimeError("dead url")
        return NPORT if cache_name.startswith("nport") else HOLDINGS

    monkeypatch.setattr(fi, "_cached_get", flaky)
    monkeypatch.setattr(fi, "latest_nport_url", lambda c, s, use_cache=True: "u")
    res = fi.BackfillResult()
    m = fi.build_map([("Dead", "_BAD", "2", "S1"), ("Good", "1", "2", "S2")], result=res)
    assert m                      # the working fund still contributed
    assert len(res.funds_failed) == 1 and len(res.funds_ok) == 1


# ── resolution guards ────────────────────────────────────────────────────────


def _row(ticker, name, hq, isin="", lei=""):
    return {"Ticker": ticker, "Company Name": name, "Country (HQ)": hq,
            "ISIN": isin, "LEI": lei}


def test_resolves_a_clean_match(mapping):
    r = fi.BackfillResult()
    p = fi.resolve([_row("4503.T", "Astellas Pharma Inc.", "Japan")], mapping, r)
    assert len(p) == 1
    assert p[0].isin == "JP3942400007"
    assert p[0].writes_isin and p[0].writes_lei


def test_exchange_suffix_guard_blocks_a_cross_market_ticker_collision(mapping):
    """The bug this module was built around: `1801.HK` keyed on ticker alone
    resolves to a Japanese ISIN."""
    r = fi.BackfillResult()
    p = fi.resolve([_row("1801.HK", "Innovent Biologics, Inc.", "China")], mapping, r)
    assert len(p) == 1
    assert p[0].isin == "KYG4818G1010"      # Cayman, correct — NOT JP3443600006


def test_country_mismatch_is_rejected_and_reported(mapping):
    """`2359.HK` exists in the fund as a Taiwanese line; it must not be used."""
    r = fi.BackfillResult()
    p = fi.resolve([_row("1801.SW", "Some Swiss Co", "Switzerland")], mapping, r)
    assert p == []
    assert r.rejected_country and r.rejected_country[0][0] == "1801.SW"


def test_name_disagreement_is_rejected(mapping):
    """Ticker and country can match while the companies do not."""
    r = fi.BackfillResult()
    p = fi.resolve([_row("4503.T", "Completely Different Mining Corp", "Japan")], mapping, r)
    assert p == []
    assert r.rejected_name and r.rejected_name[0][0] == "4503.T"


def test_existing_values_are_never_overwritten(mapping):
    r = fi.BackfillResult()
    p = fi.resolve([_row("4503.T", "Astellas Pharma Inc.", "Japan",
                         isin="JP0000000000", lei="EXISTINGLEI000000000")], mapping, r)
    assert p == []


def test_fills_only_the_missing_half(mapping):
    r = fi.BackfillResult()
    p = fi.resolve([_row("4503.T", "Astellas Pharma Inc.", "Japan", isin="JP3942400007")],
                   mapping, r)
    assert len(p) == 1
    assert p[0].writes_lei and not p[0].writes_isin


def test_us_rows_are_out_of_scope(mapping):
    """Sources are ex-US funds; counting US rows reported 599 'unresolvable'
    against a real foreign figure of ~60."""
    r = fi.BackfillResult()
    fi.resolve([_row("AAPL", "Apple Inc", "United States")], mapping, r)
    assert r.no_suffix == 0 and r.candidates == 0


def test_foreign_row_without_a_suffix_is_counted_not_guessed(mapping):
    r = fi.BackfillResult()
    p = fi.resolve([_row("207940", "Samsung Biologics Co Ltd", "South Korea")], mapping, r)
    assert p == [] and r.no_suffix == 1


def test_unknown_suffix_is_skipped(mapping):
    r = fi.BackfillResult()
    p = fi.resolve([_row("4503.ZZ", "Astellas Pharma Inc.", "Japan")], mapping, r)
    assert p == [] and r.candidates == 0


# ── incorporation vs HQ ──────────────────────────────────────────────────────


def test_incorporation_divergence_is_flagged_not_rejected():
    """Cayman incorporation of a China-operating issuer is ordinary and the ISIN
    is still right — enrich's country-prefix guard would wrongly reject it."""
    assert fi._hq_prefix_diverges({"Country (HQ)": "China"}, "KYG4818G1010") is True
    assert fi._hq_prefix_diverges({"Country (HQ)": "Japan"}, "JP3942400007") is False
    assert fi._hq_prefix_diverges({"Country (HQ)": ""}, "KYG4818G1010") is False


# ── report ───────────────────────────────────────────────────────────────────


def test_report_names_every_failed_source(tmp_path):
    r = fi.BackfillResult(status="ok")
    r.funds_failed.append("IEMG: dead url")
    p = fi.write_report(r, reports_dir=tmp_path, today="2026-07-28")
    assert "source FAILED: IEMG" in p.read_text(encoding="utf-8")


def test_main_fails_loudly_when_no_source_answers(monkeypatch):
    monkeypatch.setattr(fi, "build_map", lambda **k: {})
    res = fi.main(dry_run=True)
    assert res.status == "failed"
    assert res.errors and "no fund data" in res.errors[0]
