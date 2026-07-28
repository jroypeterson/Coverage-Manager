"""Tests for universe/foreign_crosscheck.py — no live network."""

import pytest

from universe import foreign_crosscheck as fc


def _map(**over):
    base = {
        ("4503", "Japan"): {
            "isin": "JP3942400007", "lei": "529900IB708DY2HBBB35", "inv_country": "JP",
            "name": "ASTELLAS PHARMA INC", "exchange": "Tokyo Stock Exchange",
            "currency": "JPY", "source": "TestFund",
        },
        ("1801", "China"): {
            "isin": "KYG4818G1010", "lei": "", "inv_country": "KY",
            "name": "INNOVENT BIOLOGICS INC", "exchange": "HKEX",
            "currency": "HKD", "source": "TestFund",
        },
    }
    base.update(over)
    return base


def _row(ticker, name, hq, listing="", isin="", lei="", ccy="", iso=""):
    return {"Ticker": ticker, "Company Name": name, "Country (HQ)": hq,
            "Country (Listing)": listing or hq, "Country (ISO)": iso,
            "ISIN": isin, "LEI": lei, "Currency": ccy}


def _run(rows, mapping=None):
    r = fc.CrosscheckResult()
    fc.crosscheck(rows, mapping or _map(), r)
    return r


# ── matching ─────────────────────────────────────────────────────────────────


def test_clean_row_produces_no_findings():
    r = _run([_row("4503.T", "Astellas Pharma Inc.", "Japan",
                   isin="JP3942400007", lei="529900IB708DY2HBBB35", ccy="JPY")])
    assert r.matched == 1
    assert r.conflicts == []


def test_us_rows_are_out_of_scope():
    r = _run([_row("AAPL", "Apple Inc", "United States", isin="US0378331005")])
    assert r.checked == 0 and r.matched == 0


def test_unmatched_row_is_counted_not_reported():
    """Not being held by these funds is an absence of evidence, not a finding."""
    r = _run([_row("9999.T", "Some Unheld Co", "Japan")])
    assert r.unmatched == 1 and r.conflicts == []


def test_falls_back_to_ticker_and_country_when_no_isin():
    r = _run([_row("4503.T", "Astellas Pharma Inc.", "Japan", ccy="JPY")])
    assert r.matched == 1


def test_ticker_fallback_respects_the_exchange_guard():
    """`1801` is Innovent in China and could be another issuer elsewhere."""
    r = _run([_row("1801.SW", "Some Swiss Co", "Switzerland")])
    assert r.matched == 0 and r.unmatched == 1


# ── conflicts ────────────────────────────────────────────────────────────────


def test_isin_conflict_is_reported():
    r = _run([_row("4503.T", "Astellas Pharma Inc.", "Japan", isin="JP0000000000", ccy="JPY")])
    # No ISIN index hit, so it matches on ticker+country and still compares.
    kinds = [c.kind for c in r.conflicts]
    assert "isin-conflict" in kinds


def test_wrong_country_isin_gets_the_stronger_note():
    """An Indian ISIN on a Korean row is not a close call — and is exactly what
    validate_isin_for_row exists to block."""
    r = _run([_row("4503.T", "Astellas Pharma Inc.", "Japan",
                   isin="INE156M01017", ccy="JPY")])
    c = next(c for c in r.conflicts if c.kind == "isin-conflict")
    assert "NEITHER" in c.note


def test_matching_prefix_gets_the_milder_note():
    r = _run([_row("4503.T", "Astellas Pharma Inc.", "Japan",
                   isin="JP0000000000", ccy="JPY")])
    c = next(c for c in r.conflicts if c.kind == "isin-conflict")
    assert "NEITHER" not in c.note


def test_lei_conflict_is_reported():
    r = _run([_row("4503.T", "Astellas Pharma Inc.", "Japan",
                   isin="JP3942400007", lei="0000000000000000ZZZZ", ccy="JPY")])
    assert [c.kind for c in r.conflicts] == ["lei-conflict"]


def test_absent_lei_on_either_side_is_not_a_conflict():
    """A comparison that cannot be made has no result."""
    r = _run([_row("4503.T", "Astellas Pharma Inc.", "Japan", isin="JP3942400007", ccy="JPY")])
    assert r.conflicts == []
    r2 = _run([_row("1801.HK", "Innovent Biologics, Inc.", "China",
                    isin="KYG4818G1010", lei="SOMELEI0000000000000", ccy="HKD")])
    assert [c.kind for c in r2.conflicts] == []      # source LEI is blank


def test_listing_mismatch_describes_conflation_not_a_wrong_currency():
    """AZN: ticker on NYQ in USD carrying the London ordinary's ISIN. Both facts
    are right; the row mixes two securities."""
    m = _map()
    m[("AZN", "United Kingdom")] = {
        "isin": "GB0009895292", "lei": "", "inv_country": "GB",
        "name": "ASTRAZENECA PLC", "exchange": "London Stock Exchange",
        "currency": "GBP", "source": "TestFund",
    }
    r = _run([_row("AZN", "AstraZeneca PLC", "United Kingdom", listing="United States",
                   isin="GB0009895292", ccy="USD")], m)
    c = next(c for c in r.conflicts if c.kind == "listing-mismatch")
    assert "conflation" in c.note and "GBP" in c.note


def test_name_divergence_keeps_both_readings_open():
    """ZEN matched an Indian company by ISIN — a rename is not the only cause."""
    r = _run([_row("4503.T", "Completely Different Mining Corp", "Japan",
                   isin="JP3942400007", lei="529900IB708DY2HBBB35", ccy="JPY")])
    c = next(c for c in r.conflicts if c.kind == "name-divergence")
    assert "rename" in c.note and "another company" in c.note


def test_name_divergence_suppresses_the_vaguer_listing_finding():
    """ZEN matched an Indian company. Reporting that as a 'listing conflation'
    would send a reader hunting for an Indian listing of a Canadian company."""
    m = _map()
    m[("ZEN", "India")] = {
        "isin": "INE251B01027", "lei": "", "inv_country": "IN",
        "name": "ZEN TECHNOLOGIES LTD", "exchange": "NSE India",
        "currency": "INR", "source": "TestFund",
    }
    r = _run([_row("ZEN", "Zentek Ltd", "Canada", isin="INE251B01027", ccy="CAD")], m)
    kinds = [c.kind for c in r.conflicts]
    assert "name-divergence" in kinds
    assert "listing-mismatch" not in kinds


def test_cosmetic_name_difference_is_not_a_finding():
    r = _run([_row("4503.T", "Astellas Pharma, Inc.", "Japan",
                   isin="JP3942400007", lei="529900IB708DY2HBBB35", ccy="JPY")])
    assert [c.kind for c in r.conflicts] == []


# ── expected divergences that must NOT be errors ─────────────────────────────


def test_incorporation_difference_is_a_note_not_a_conflict():
    r = _run([_row("1801.HK", "Innovent Biologics, Inc.", "China",
                   isin="KYG4818G1010", ccy="HKD")])
    assert r.conflicts == []
    assert [n.ticker for n in r.incorporation_notes] == ["1801.HK"]


def test_alpha3_vs_alpha2_is_never_reported():
    """Every one of the first 18 hand-found 'country mismatches' was this."""
    assert fc._iso3_to_iso2("GBR") == "GB"
    assert fc._iso3_to_iso2("JPN") == "JP"
    r = _run([_row("4503.T", "Astellas Pharma Inc.", "Japan", isin="JP3942400007",
                   lei="529900IB708DY2HBBB35", ccy="JPY", iso="JPN")])
    assert r.conflicts == [] and r.incorporation_notes == []


def test_prefix_matches_any_country_mirrors_the_enrich_rule():
    row = _row("X", "n", "Switzerland", listing="United States")
    assert fc._prefix_matches_any_country(row, "CH0012032048") is True   # HQ
    assert fc._prefix_matches_any_country(row, "US0378331005") is True   # listing
    assert fc._prefix_matches_any_country(row, "INE156M01017") is False


# ── run-level behaviour ──────────────────────────────────────────────────────


def test_main_fails_loudly_when_no_source_answers(monkeypatch):
    monkeypatch.setattr(fc, "build_map", lambda **k: {})
    res = fc.main()
    assert res.status == "failed" and not res.ok


def test_report_states_agreement_explicitly(tmp_path):
    """A silent report and a clean one must not look the same."""
    p = fc.write_report(fc.CrosscheckResult(status="ok", checked=10, matched=8),
                        reports_dir=tmp_path, today="2026-07-28")
    assert "_None. Every matched row agrees" in p.read_text(encoding="utf-8")


def test_report_names_failed_sources(tmp_path):
    r = fc.CrosscheckResult()
    r.funds_failed.append("IEMG: dead url")
    p = fc.write_report(r, reports_dir=tmp_path, today="2026-07-28")
    assert "source FAILED: IEMG" in p.read_text(encoding="utf-8")
