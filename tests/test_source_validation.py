"""Tests for source_validation.py."""

import pandas as pd

from source_validation import compare_field, _prepare_universe_rows


def test_compare_field_flags_large_relative_difference():
    result = compare_field(
        "Fwd P/E",
        {"yfinance": 10.0, "fmp": 16.0},
    )

    assert result is not None
    assert result["flagged"] is True
    assert result["threshold_mode"] == "relative_pct"


def test_compare_field_uses_absolute_threshold_for_percent_fields():
    result = compare_field(
        "Gross Mgn",
        {"yfinance": 55.0, "fmp": 61.0},
    )

    assert result is not None
    assert result["flagged"] is False
    assert result["threshold_mode"] == "absolute"


def test_compare_field_skips_monetary_mismatched_currencies():
    result = compare_field(
        "Price",
        {"yfinance": 100.0, "fmp": 130.0},
        {"yfinance": "USD", "fmp": "JPY"},
    )

    assert result is None


def test_prepare_universe_rows_deduplicates_and_normalizes():
    df = pd.DataFrame(
        {
            "Ticker": [" AAPL ", "AAPL", "#N/A", ""],
            "Company Name": ["Apple Inc", "Apple Duplicate", "Bad", "Blank"],
            "Exchange": ["NASDAQ", "NASDAQ", "NYSE", "NYSE"],
        }
    )

    result = _prepare_universe_rows(df)

    assert result["Ticker"].tolist() == ["AAPL"]
    assert result["_yf_ticker"].tolist() == ["AAPL"]
