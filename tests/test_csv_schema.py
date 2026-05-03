"""Tests for CSV schema expectations across pipeline stages."""

import pandas as pd
from config import CSV_PATH


def test_csv_exists():
    assert CSV_PATH.exists(), f"Coverage CSV not found at {CSV_PATH}"


def test_csv_has_expected_columns():
    df = pd.read_csv(CSV_PATH, nrows=0)
    expected = [
        "Ticker", "Exchange", "Company Name",
        "Sector (JP)", "Subsector (JP)",
    ]
    for col in expected:
        assert col in df.columns, f"Missing expected column: {col}"


def test_csv_no_unnamed_columns():
    df = pd.read_csv(CSV_PATH, nrows=0)
    unnamed = [c for c in df.columns if c.startswith("Unnamed:")]
    assert len(unnamed) == 0, f"Found orphaned columns: {unnamed}"


def test_csv_no_blank_tickers():
    df = pd.read_csv(CSV_PATH)
    blank_tickers = df[df["Ticker"].isna() | (df["Ticker"].astype(str).str.strip() == "")]
    assert len(blank_tickers) == 0, f"Found {len(blank_tickers)} rows with blank Ticker"


def test_csv_has_country_iso_column():
    """Country (ISO) should exist in the CSV after enrichment pipeline fix."""
    df = pd.read_csv(CSV_PATH, nrows=0)
    assert "Country (ISO)" in df.columns, "Missing Country (ISO) column"
