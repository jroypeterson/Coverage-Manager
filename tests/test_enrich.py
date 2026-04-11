"""Tests for enrichment helpers that don't need live API calls."""

from universe.enrich import validate_isin_for_row


def test_isin_country_match_accepted():
    row = {"Country (Listing)": "United States", "Country (HQ)": "United States"}
    assert validate_isin_for_row("US3377381088", row, "FI") == "US3377381088"


def test_isin_wrong_country_rejected():
    # The actual bug: yfinance returned a Swiss ISIN for US-listed Fiserv
    row = {"Country (Listing)": "United States", "Country (HQ)": "United States"}
    assert validate_isin_for_row("CH0029956346", row, "FI") is None


def test_isin_hq_fallback_when_listing_blank():
    row = {"Country (Listing)": "", "Country (HQ)": "Switzerland"}
    assert validate_isin_for_row("CH0012032048", row, "ROG") == "CH0012032048"
    assert validate_isin_for_row("US1234567890", row, "ROG") is None


def test_isin_unknown_country_passes_through():
    # If we don't have a mapping for the row's country, accept the ISIN
    # rather than blocking enrichment — the goal is to catch *known* mismatches,
    # not to gate every row on the map being complete.
    row = {"Country (Listing)": "Liechtenstein", "Country (HQ)": "Liechtenstein"}
    assert validate_isin_for_row("LI0377700028", row, "VPBN") == "LI0377700028"


def test_isin_blank_row_passes_through():
    # New rows where Country hasn't been populated yet — can't check, accept.
    row = {"Country (Listing)": "", "Country (HQ)": ""}
    assert validate_isin_for_row("US3377381088", row, "FI") == "US3377381088"


def test_isin_sentinel_values_rejected():
    row = {"Country (Listing)": "United States"}
    assert validate_isin_for_row("", row) is None
    assert validate_isin_for_row("-", row) is None
    assert validate_isin_for_row("error: not found", row) is None
    assert validate_isin_for_row(None, row) is None
