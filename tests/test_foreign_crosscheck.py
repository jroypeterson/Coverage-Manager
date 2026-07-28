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


# ── the 2026-07-28 corrections (JP-approved) ─────────────────────────────────
# `crosscheck-foreign` found seven universe rows carrying an ISIN that belongs
# to a *different company*. JP approved applying all seven on 2026-07-28. These
# tests pin the corrected values and the rule that should have blocked the
# originals, so a future enrichment run cannot quietly reintroduce them.
#
# Each replacement was verified against OpenFIGI (Bloomberg) and/or GLEIF —
# sources independent of the SEC N-PORT join that proposed them.

# ticker -> (wrong value that was removed, corrected value, what the wrong one
#            actually identifies)
CORRECTED_ISINS = {
    "000100.KS": ("INE156M01017", "KR7000100008", "Yuranus Infrastructure (India)"),
    "9926.HK": ("INE087A01019", "KYG0146B1032", "Kesoram Industries (India)"),
    "ZEN": ("INE251B01027", "CA98942X1024", "Zen Technologies (India)"),
    "7741.T": ("DE0005297204", "JP3837800006", "Homag Group AG (Germany)"),
    "FAGR.BR": ("CZ0008461209", "BE0003874915", "Fagron a.s. (Czechia)"),
    "6446.TW": ("US7169722037", "TW0006446008", "PharmaEssentia GDR (Luxembourg)"),
    "8086.T": ("JP3750800009", "JP3673600007", "NMS Holdings (Japan)"),
}

CORRECTED_LEIS = {
    "7741.T": ("5299009ROBNLE4G0RK14", "353800X4VR3BHEUCJB42"),   # Homag -> Hoya
    "FAGR.BR": ("3157004AQG2TA4ZS7Y94", "549300TRKRUFK2RRG779"),  # a.s. -> Fagron NV
}

# ZEN carried Zen Technologies' Bloomberg identifiers too, not just its ISIN.
CORRECTED_ZEN_FIGIS = {
    "FIGI": ("BBG000BTLY23", "BBG0018QK5P0"),
    "Composite FIGI": ("BBG000BTLY23", "BBG0018QK5L4"),
    "Share Class FIGI": ("BBG001SF6TG6", "BBG001TFBYY8"),
}

# The row's country as `validate_isin_for_row` sees it. Akeso is the documented
# exception: China-HQ'd, Hong-Kong-listed, Cayman-incorporated, so its correct
# ISIN legitimately matches neither country field.
_ROW_COUNTRIES = {
    "000100.KS": ("South Korea", "South Korea"),
    "9926.HK": ("China", "Hong Kong"),
    "ZEN": ("Canada", "Canada"),
    "7741.T": ("Japan", "Japan"),
    "FAGR.BR": ("Belgium", "Belgium"),
    "6446.TW": ("Taiwan", "Taiwan"),
    "8086.T": ("Japan", "Japan"),
}
_INCORPORATION_EXCEPTIONS = {"9926.HK"}


def _isin_check_digit_ok(isin):
    """ISO 6166 mod-10 (Luhn). Arithmetic only — no vendor, no network."""
    digits = "".join(str(int(c, 36)) if c.isalpha() else c for c in isin.upper())
    total, double = 0, True
    for ch in reversed(digits[:-1]):
        d = int(ch) * (2 if double else 1)
        total += d - 9 if d > 9 else d
        double = not double
    return (10 - total % 10) % 10 == int(digits[-1])


@pytest.fixture(scope="module")
def universe_rows():
    from ticker_utils import read_universe_csv

    df = read_universe_csv()
    return {r["Ticker"]: r for _, r in df.iterrows()}


@pytest.mark.parametrize("ticker", sorted(CORRECTED_ISINS))
def test_corrected_isin_is_what_the_universe_carries(ticker, universe_rows):
    wrong, right, _ = CORRECTED_ISINS[ticker]
    stored = universe_rows[ticker]["ISIN"].strip()
    assert stored == right, f"{ticker} ISIN regressed to {stored}"
    assert stored != wrong


@pytest.mark.parametrize("ticker", sorted(CORRECTED_LEIS))
def test_corrected_lei_is_what_the_universe_carries(ticker, universe_rows):
    wrong, right = CORRECTED_LEIS[ticker]
    stored = universe_rows[ticker]["LEI"].strip()
    assert stored == right, f"{ticker} LEI regressed to {stored}"
    assert stored != wrong


@pytest.mark.parametrize("column", sorted(CORRECTED_ZEN_FIGIS))
def test_zen_carries_zenteks_figis_not_zen_technologies(column, universe_rows):
    """Correcting only the ISIN would have left the row still lying: ZEN's three
    Bloomberg IDs were the Indian company's as well."""
    wrong, right = CORRECTED_ZEN_FIGIS[column]
    stored = universe_rows["ZEN"][column].strip()
    assert stored == right, f"ZEN {column} regressed to {stored}"
    assert stored != wrong


def test_no_removed_identifier_survives_anywhere_in_the_universe(universe_rows):
    """A wrong ISIN is worse than a blank one — it looks like data. None of the
    twelve removed values may reappear on ANY row, not just its own."""
    removed = ({w for w, _, _ in CORRECTED_ISINS.values()}
               | {w for w, _ in CORRECTED_LEIS.values()}
               | {w for w, _ in CORRECTED_ZEN_FIGIS.values()})
    cols = ("ISIN", "LEI", "FIGI", "Composite FIGI", "Share Class FIGI")
    hits = [(t, c, row[c].strip()) for t, row in universe_rows.items()
            for c in cols if row[c].strip() in removed]
    assert hits == [], f"removed identifiers reappeared: {hits}"


@pytest.mark.parametrize("ticker", sorted(CORRECTED_ISINS))
def test_every_corrected_isin_passes_its_own_check_digit(ticker):
    """Independent of every source that proposed it: writing a *second* wrong
    identifier over a first is the worst outcome, so verify the arithmetic."""
    _, right, _ = CORRECTED_ISINS[ticker]
    assert _isin_check_digit_ok(right), f"{right} fails ISO 6166 mod-10"


@pytest.mark.parametrize("ticker", sorted(CORRECTED_ISINS))
def test_corrected_isin_prefix_matches_the_rows_actual_market(ticker):
    """The rule the crosscheck reports on: an ISIN's prefix must match the
    security's market. Akeso is the one documented exception — Cayman
    incorporation of a China-operating, HK-listed issuer (see CLAUDE.md
    'Incorporation is not domicile')."""
    _, right, _ = CORRECTED_ISINS[ticker]
    hq, listing = _ROW_COUNTRIES[ticker]
    row = _row(ticker, "n", hq, listing=listing)
    matches = fc._prefix_matches_any_country(row, right)
    if ticker in _INCORPORATION_EXCEPTIONS:
        assert not matches and right[:2] == "KY"
    else:
        assert matches, f"{ticker}: {right} matches neither {hq} nor {listing}"


# ── the guard that should have blocked these ─────────────────────────────────


@pytest.mark.parametrize("ticker", sorted(set(CORRECTED_ISINS) - {"8086.T"}))
def test_enrich_guard_rejects_every_wrong_country_isin_we_removed(ticker):
    """Six of the seven have a prefix matching neither country field, which is
    exactly what `validate_isin_for_row` exists to reject — so they predate the
    guard (universe CSV imported 2026-04-03, guard landed 2026-04-11) rather
    than slipping past it."""
    from universe.enrich import validate_isin_for_row

    wrong, _, _ = CORRECTED_ISINS[ticker]
    hq, listing = _ROW_COUNTRIES[ticker]
    row = {"Country (HQ)": hq, "Country (Listing)": listing}
    assert validate_isin_for_row(wrong, row, ticker) is None


def test_guard_does_NOT_catch_a_same_country_wrong_issuer_isin():
    """Known, live limitation — pinned so it is not mistaken for coverage.

    Nipro's wrong ISIN `JP3750800009` is NMS Holdings', also Japanese, so the
    prefix rule accepts it. yfinance still returns that value for `8086.T`
    today. A prefix check is a country check, not an identity check; closing
    this needs a name/identity cross-check (or an ISIN check digit + issuer
    lookup), not a tighter prefix.
    """
    from universe.enrich import validate_isin_for_row

    wrong, right, _ = CORRECTED_ISINS["8086.T"]
    row = {"Country (HQ)": "Japan", "Country (Listing)": "Japan"}
    assert validate_isin_for_row(wrong, row, "8086.T") == wrong
    assert validate_isin_for_row(right, row, "8086.T") == right
    assert _isin_check_digit_ok(wrong)  # and it is a well-formed ISIN, too
