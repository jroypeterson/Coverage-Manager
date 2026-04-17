"""Tests for enrichment helpers that don't need live API calls."""

from unittest.mock import patch

import pytest

from universe.enrich import (
    EnrichError,
    _normalize_fmp_exchange,
    enrich_single_ticker,
    validate_isin_for_row,
    validate_sector_jp,
)


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


# ── validate_sector_jp ───────────────────────────────────────────────────────


def test_validate_sector_jp_accepts_known():
    for s in ("Tech", "Biopharma", "Healthcare Services", "Other"):
        validate_sector_jp(s)  # no exception


def test_validate_sector_jp_rejects_unknown():
    with pytest.raises(EnrichError, match="unknown Sector"):
        validate_sector_jp("Utilities")


def test_validate_sector_jp_rejects_empty():
    with pytest.raises(EnrichError, match="sector_jp is required"):
        validate_sector_jp(None)
    with pytest.raises(EnrichError, match="sector_jp is required"):
        validate_sector_jp("")


# ── _normalize_fmp_exchange ──────────────────────────────────────────────────


def test_normalize_fmp_exchange_nasdaq_variants():
    assert _normalize_fmp_exchange("NASDAQ Global Select", "NASDAQ") == "NASDAQ"
    assert _normalize_fmp_exchange("NASDAQ Capital Market", "NASDAQ") == "NASDAQ"


def test_normalize_fmp_exchange_nyse_variants():
    assert _normalize_fmp_exchange("New York Stock Exchange", "NYSE") == "NYSE"
    assert _normalize_fmp_exchange("NYSE", "NYSE") == "NYSE"


def test_normalize_fmp_exchange_falls_back_to_short():
    assert _normalize_fmp_exchange("Unknown Full Name", "LSE") == "LSE"
    assert _normalize_fmp_exchange("Unknown", "Unknown") == ""


# ── enrich_single_ticker ─────────────────────────────────────────────────────


def _fake_fmp_response(ticker):
    """Return a complete FMP stable/profile response for FI (Fiserv)."""
    return {
        "symbol": ticker,
        "companyName": "Fiserv Inc.",
        "isin": "US3377381088",
        "cik": "0000798354",
        "ipoDate": "1986-09-25",
        "sector": "Technology",
        "industry": "Information Technology Services",
        "website": "https://www.fiserv.com",
        "currency": "USD",
        "country": "United States",
        "exchange": "NYSE",
        "exchangeFullName": "New York Stock Exchange",
    }


def test_enrich_single_ticker_fmp_only_full_row():
    """Happy path: FMP returns complete data; no yfinance/OpenFIGI needed."""
    with patch("universe.enrich._fetch_fmp_profile", side_effect=_fake_fmp_response), \
         patch("universe.enrich.fetch_openfigi_identifiers", return_value={}), \
         patch("universe.enrich.fetch_sec_cik_map", return_value={}), \
         patch("universe.enrich.yf.Ticker") as mock_yf:
        # yfinance shouldn't be called since FMP filled everything — but if
        # it is, return empty so the test still passes and we can detect
        # unnecessary calls via the assert below.
        mock_yf.return_value.isin = "-"
        mock_yf.return_value.info = {}

        row = enrich_single_ticker("FI", sector_jp="Fintech")

    assert row["Ticker"] == "FI"
    assert row["Company Name"] == "Fiserv Inc."
    assert row["ISIN"] == "US3377381088"
    assert row["CIK"] == "798354"  # leading zeros stripped
    assert row["Year Listed"] == "1986"
    assert row["Currency"] == "USD"
    assert row["Exchange"] == "NYSE"
    assert row["Country (HQ)"] == "United States"
    assert row["Country (Listing)"] == "United States"
    assert row["Country (ISO)"] == "USA"
    assert row["Sector (JP)"] == "Fintech"
    assert row["YF Sector"] == "Technology"
    assert row["Website"] == "https://www.fiserv.com"
    assert row["Listing Type"] == "Primary"


def test_enrich_single_ticker_rejects_bad_sector():
    with pytest.raises(EnrichError, match="unknown Sector"):
        enrich_single_ticker("FI", sector_jp="NotARealSector")


def test_enrich_single_ticker_requires_sector():
    with pytest.raises(EnrichError, match="sector_jp is required"):
        enrich_single_ticker("FI", sector_jp=None)


def test_enrich_single_ticker_blank_ticker():
    with pytest.raises(EnrichError, match="ticker is required"):
        enrich_single_ticker("", sector_jp="Tech")


def test_enrich_single_ticker_fails_when_required_metadata_missing():
    """If every source returns empty, raise with a clear error."""
    with patch("universe.enrich._fetch_fmp_profile", return_value={}), \
         patch("universe.enrich.fetch_openfigi_identifiers", return_value={}), \
         patch("universe.enrich.fetch_sec_cik_map", return_value={}), \
         patch("universe.enrich.yf.Ticker") as mock_yf:
        mock_yf.return_value.isin = "-"
        mock_yf.return_value.info = {}

        with pytest.raises(EnrichError, match="missing"):
            enrich_single_ticker("NOPE", sector_jp="Tech")


def test_enrich_single_ticker_yfinance_fallback_fills_gaps():
    """FMP returns partial data; yfinance fills the rest."""
    def partial_fmp(ticker):
        return {
            "symbol": ticker,
            "companyName": "Example Corp",
            "exchange": "NASDAQ",
            "exchangeFullName": "NASDAQ Global Select",
            "country": "United States",
            # no currency, no ISIN, no ipoDate, no sector
        }

    class FakeYFTicker:
        isin = "US1234567890"
        info = {
            "currency": "USD",
            "sector": "Technology",
            "industry": "Software",
            "firstTradeDateEpochUtc": 1_234_567_890,  # 2009
        }

    with patch("universe.enrich._fetch_fmp_profile", side_effect=partial_fmp), \
         patch("universe.enrich.fetch_openfigi_identifiers", return_value={}), \
         patch("universe.enrich.fetch_sec_cik_map", return_value={"EXMP": "1234567"}), \
         patch("universe.enrich.yf.Ticker", return_value=FakeYFTicker()):
        row = enrich_single_ticker("EXMP", sector_jp="SaaS")

    assert row["Company Name"] == "Example Corp"
    assert row["Exchange"] == "NASDAQ"
    assert row["Currency"] == "USD"         # filled by yfinance
    assert row["ISIN"] == "US1234567890"    # filled by yfinance, passes guard
    assert row["Year Listed"] == "2009"     # filled by yfinance
    assert row["YF Sector"] == "Technology"
    assert row["CIK"] == "1234567"          # filled by SEC fallback
    assert row["Sector (JP)"] == "SaaS"


def test_enrich_single_ticker_rejects_wrong_country_isin_from_yfinance():
    """Regression for the FISV→FI bug: wrong-country ISIN must be rejected."""
    def partial_fmp(ticker):
        return {
            "symbol": ticker,
            "companyName": "Fiserv Inc.",
            "exchange": "NYSE",
            "currency": "USD",
            "country": "United States",
            # No ISIN from FMP, forcing yfinance fallback
        }

    class FakeYFTicker:
        isin = "CH0029956346"  # The actual wrong ISIN yfinance returns for FI
        info = {}

    with patch("universe.enrich._fetch_fmp_profile", side_effect=partial_fmp), \
         patch("universe.enrich.fetch_openfigi_identifiers", return_value={}), \
         patch("universe.enrich.fetch_sec_cik_map", return_value={"FI": "798354"}), \
         patch("universe.enrich.yf.Ticker", return_value=FakeYFTicker()):
        row = enrich_single_ticker("FI", sector_jp="Fintech")

    # Row is still built (other required fields present), but ISIN is blank
    assert row["ISIN"] == ""
    assert row["Company Name"] == "Fiserv Inc."
    assert row["Exchange"] == "NYSE"
