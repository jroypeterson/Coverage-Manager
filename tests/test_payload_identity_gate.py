"""The whole vendor payload is identity-gated, not just its ISIN.

Why this exists (2026-07-29). The ISIN write path has been identity-gated since
`81ada8d`, but every other field from the SAME response landed unchecked, so a
lookup that resolved to the wrong company was only half-rejected: its ISIN was
refused while its CIK, website, venue and currency were written. Nine rows were
repaired by hand over two days, all one shape:

    MED   <- MEDIFAST INC            (CIK 910329, medifastinc.com)
    MOVE  <- Corvex, Inc.            (CIK 1734750, movano.com)
    UCB   <- UNITED COMMUNITY BANKS  (CIK 857855, ucbi.com, "Banks - Regional")
    CSL   <- CARLISLE COMPANIES      (CIK 790051, FIGI BBG000BGGBT8, carlisle.com)

The mechanism: `_fetch_fmp_profile` gets the RAW ticker while every yfinance call
goes through `normalize_ticker`, which appends an exchange suffix. So for a bare
foreign symbol FMP answers about the US namesake -- and its payload overwrites
`Exchange`, which `normalize_ticker` keys off, so the next run's yfinance call
goes bare too. Self-reinforcing, and all four columns end up agreeing, which is
why it was invisible for four months.
"""
import pytest

from universe.enrich import (PAYLOAD_NAME_THRESHOLD, _payload_names_match,
                             payload_is_for_this_row)


# ── the comparison ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("expected,vendor", [
    ("Medartis Holding AG", "Medartis AG"),
    ("Medacta Group SA", "Medacta International"),
    ("UCB SA", "UCB S.A."),
    ("CSL Ltd", "CSL Limited"),
    ("bioMerieux SA", "BIOMERIEUX"),
    ("Gerresheimer AG", "Gerresheimer AG"),
    ("Financiere de Tubize SA", "FINANCIERE DE TUBIZE"),
])
def test_the_same_company_matches_across_legal_form_and_case(expected, vendor):
    assert _payload_names_match(expected, vendor) is True


@pytest.mark.parametrize("expected,vendor", [
    ("Medartis Holding AG", "MEDIFAST INC"),
    ("Medacta Group SA", "Corvex, Inc."),
    ("UCB SA", "UNITED COMMUNITY BANKS INC"),
    ("CSL Ltd", "CARLISLE COMPANIES INC"),
    ("Ipsen SA", "SPDR Dow Jones Industrial Average ETF Trust"),
    ("Boiron SA", "WINGARA AG LTD"),
    ("DiaSorin S.p.A.", "SPDR DJIA TRUST"),
])
def test_the_real_namesake_pairs_are_all_rejected(expected, vendor):
    """Every pair here was found live on a universe row."""
    assert _payload_names_match(expected, vendor) is False


@pytest.mark.parametrize("expected,vendor", [
    ("", "MEDIFAST INC"),
    ("Medartis Holding AG", ""),
    (None, None),
])
def test_an_impossible_comparison_returns_None_not_False(expected, vendor):
    """A comparison that cannot be made has no result -- the found/clean/
    inconclusive rule this repo applies everywhere else. Returning False would
    reject every payload on a fresh add (where the row has no name yet);
    returning True would wave the wrong company through."""
    assert _payload_names_match(expected, vendor) is None


# ── the gate ────────────────────────────────────────────────────────────────

def test_gate_discards_a_namesake_payload():
    assert payload_is_for_this_row(
        "Medartis Holding AG", "MEDIFAST INC", "MED", "FMP profile") is False


def test_gate_admits_the_right_company():
    assert payload_is_for_this_row(
        "Medartis Holding AG", "Medartis AG", "MED", "FMP profile") is True


def test_gate_admits_when_it_CANNOT_tell():
    """Unknown must not block enrichment: a brand-new row legitimately has no
    name to compare against, and refusing every such payload would make adding a
    ticker impossible. The gate's job is catching a KNOWN mismatch, and the
    caller decides whether to supply a hint at all."""
    assert payload_is_for_this_row("", "MEDIFAST INC", "MED", "FMP profile") is True
    assert payload_is_for_this_row("Medartis", "", "MED", "FMP profile") is True


def test_gate_warns_on_rejection(caplog):
    """A silent skip is the failure mode this repo keeps getting burned by, so
    the rejection must be loud AND name both companies -- a reader must be able
    to tell a namesake collision from a rename without re-running anything."""
    import logging
    with caplog.at_level(logging.WARNING):
        payload_is_for_this_row("CSL Ltd", "CARLISLE COMPANIES INC", "CSL", "FMP profile")
    msg = caplog.text
    assert "CSL" in msg
    assert "CARLISLE" in msg.upper()
    assert "DISCARDING" in msg.upper()


def test_this_gate_is_STRICTER_than_the_cik_one_on_purpose():
    """It guards a worse failure — `cik_backfill` binds one CIK, this admits an
    entire payload — and 0.55 is provably too loose for the motivating case:
    difflib scores Medartis vs Medifast ~0.62."""
    import difflib

    from universe.cik_backfill import NAME_MATCH_THRESHOLD
    assert PAYLOAD_NAME_THRESHOLD > NAME_MATCH_THRESHOLD
    naive = difflib.SequenceMatcher(None, "medartis", "medifast").ratio()
    assert naive > NAME_MATCH_THRESHOLD, (
        f"the premise changed: naive similarity is now {naive:.2f}")


def test_character_similarity_alone_would_have_passed_the_bad_pair():
    """Guards the DESIGN, not just the outcome. If someone simplifies this back
    to a difflib ratio, this test says why that fails."""
    import difflib
    for a, b in (("medartis", "medifast"), ("csl", "carlisle")):
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        # The pair a naive gate would wave through...
        if ratio >= 0.55:
            # ...must still be rejected by the real gate.
            assert _payload_names_match(a, b) is False, (a, b, ratio)


def test_a_short_name_is_not_matched_by_mere_substring():
    """`ucb` is a literal substring of `glucberry`. Subset-of-tokens is the test,
    never raw string containment."""
    assert _payload_names_match("UCB SA", "Glucberry Inc") is False


# ── the write path actually uses it ─────────────────────────────────────────

def test_enrich_single_ticker_accepts_a_company_hint():
    """Signature guard: if the parameter is renamed or dropped, the callers that
    thread it (scripts/approve_candidates.py) silently stop gating."""
    import inspect

    from universe.enrich import enrich_single_ticker
    params = inspect.signature(enrich_single_ticker).parameters
    assert "company_hint" in params
    assert params["company_hint"].default is None, "must stay backward-compatible"


def test_the_fmp_payload_is_discarded_wholesale_not_field_by_field(monkeypatch):
    """The actual regression: with a hint that disagrees with FMP, NONE of the
    payload's fields may reach the row -- not the CIK, not the website, not the
    currency, not the exchange. Half-rejecting is what produced the nine bad rows.
    """
    from universe import enrich

    monkeypatch.setattr(enrich, "_fetch_fmp_profile", lambda t: {
        "companyName": "MEDIFAST INC", "cik": "910329",
        "website": "https://medifastinc.com", "currency": "USD",
        "country": "US", "exchange": "NYSE", "exchangeFullName": "New York Stock Exchange",
        "sector": "Consumer Cyclical", "industry": "Personal Services",
        "isin": "US58470H1014", "ipoDate": "1993-01-01",
    })
    # Neutralise every other network source so only the FMP decision is exercised.
    monkeypatch.setattr(enrich, "fetch_openfigi_identifiers", lambda df: {})
    monkeypatch.setattr(enrich, "fetch_sec_cik_map", lambda: {})
    monkeypatch.setattr(enrich, "_fetch_yfinance_single",
                        lambda *a, **k: {}, raising=False)

    try:
        row = enrich.enrich_single_ticker(
            "MED", "MedTech", exchange_hint="SIX",
            company_hint="Medartis Holding AG")
    except enrich.EnrichError:
        # Refusing to build the row at all is an acceptable outcome -- what must
        # NOT happen is a row carrying Medifast's identity.
        return

    for field, poison in (("CIK", "910329"), ("Website", "medifastinc"),
                          ("ISIN", "US58470H1014")):
        assert poison not in str(row.get(field, "")), (
            f"{field} kept Medifast's value: {row.get(field)!r}")
    assert "MEDIFAST" not in str(row.get("Company Name", "")).upper()


# ── the BULK path gate (all ~1,096 rows, not just new adds) ─────────────────

def _bulk_row(ticker, name, cik_blank=True):
    import pandas as pd
    return pd.DataFrame([{
        "Ticker": ticker, "Company Name": name, "CIK": "" if cik_blank else "999",
        "Exchange": "ASX", "Country (Listing)": "", "Country (ISO)": "",
        "Listing Type": "", "Other Listings": "",
    }])


def test_bulk_cik_write_rejects_a_namesake_title():
    """`enrich_dataframe` bound a CIK on a BARE TICKER MATCH across the whole
    universe. CSL Ltd would take Carlisle Companies' CIK — which is exactly the
    state found on the live row (790051)."""
    from universe.enrich import enrich_dataframe

    df = _bulk_row("CSL", "CSL Ltd")
    out = enrich_dataframe(
        df, yf_data={}, figi_data={},
        cik_map={"CSL": ("790051", "CARLISLE COMPANIES INC")},
        listing_types={}, other_listings={})
    assert str(out.iloc[0]["CIK"]).strip() == "", (
        "Carlisle's CIK was written onto CSL Ltd")


def test_bulk_cik_write_admits_the_right_company():
    from universe.enrich import enrich_dataframe

    df = _bulk_row("CRSP", "CRISPR Therapeutics AG")
    out = enrich_dataframe(
        df, yf_data={}, figi_data={},
        cik_map={"CRSP": ("1674416", "CRISPR Therapeutics AG")},
        listing_types={}, other_listings={})
    assert str(out.iloc[0]["CIK"]).strip() == "1674416"


def test_bulk_cik_write_still_works_with_an_untitled_map():
    """Back-compat: the legacy {TICKER: cik} shape must keep working, ungated,
    rather than silently writing nothing."""
    from universe.enrich import enrich_dataframe

    df = _bulk_row("CRSP", "CRISPR Therapeutics AG")
    out = enrich_dataframe(
        df, yf_data={}, figi_data={}, cik_map={"CRSP": "1674416"},
        listing_types={}, other_listings={})
    assert str(out.iloc[0]["CIK"]).strip() == "1674416"


def test_the_bulk_loader_supplies_titles_so_the_gate_can_fire():
    """A guard that cannot fire is decoration. `enrich.main` must load the TITLED
    map, or `enrich_dataframe`'s gate degrades to 'write it anyway' with nothing
    said."""
    import inspect

    from universe import enrich
    src = inspect.getsource(enrich.main)
    assert "cik_backfill import fetch_sec_cik_map" in src, (
        "main() no longer loads the titled SEC map — the bulk identity gate is "
        "now inert")
