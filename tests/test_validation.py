"""Tests for validation.py — CSV schema and data quality checks."""

import pandas as pd
import pytest

from universe.validation import (
    validate_required_columns,
    validate_no_orphaned_columns,
    validate_no_blank_tickers,
    validate_no_duplicate_tickers,
    validate_case_only_ticker_collisions,
    validate_duplicate_companies,
    validate_exchange_populated,
    run_all_validations,
)


def _make_df(data=None, columns=None):
    if data is None:
        data = {
            "Ticker": ["AAPL", "MSFT", "GOOG"],
            "Company Name": ["Apple Inc", "Microsoft Corp", "Alphabet Inc"],
            "Sector (JP)": ["Tech", "Tech", "Tech"],
            "Exchange": ["NASDAQ", "NASDAQ", "NASDAQ"],
        }
    return pd.DataFrame(data, columns=columns)


class TestRequiredColumns:
    def test_all_present(self):
        assert validate_required_columns(_make_df()) == []

    def test_missing_ticker(self):
        df = _make_df({"Company Name": ["A"], "Sector (JP)": ["B"]})
        errors = validate_required_columns(df)
        assert len(errors) == 1
        assert "Ticker" in errors[0]

    def test_missing_multiple(self):
        df = pd.DataFrame({"Foo": [1]})
        errors = validate_required_columns(df)
        assert len(errors) == 3


class TestOrphanedColumns:
    def test_clean(self):
        assert validate_no_orphaned_columns(_make_df()) == []

    def test_unnamed(self):
        df = _make_df()
        df["Unnamed: 0"] = [1, 2, 3]
        errors = validate_no_orphaned_columns(df)
        assert len(errors) == 1


class TestBlankTickers:
    def test_clean(self):
        assert validate_no_blank_tickers(_make_df()) == []

    def test_blank(self):
        df = _make_df({"Ticker": ["AAPL", "", "GOOG"], "Company Name": ["A", "B", "C"], "Sector (JP)": ["X", "Y", "Z"]})
        errors = validate_no_blank_tickers(df)
        assert len(errors) == 1


class TestDuplicateTickers:
    def test_clean(self):
        assert validate_no_duplicate_tickers(_make_df()) == []

    def test_dupes(self):
        df = _make_df({"Ticker": ["AAPL", "AAPL", "GOOG"], "Company Name": ["A", "B", "C"], "Sector (JP)": ["X", "Y", "Z"]})
        errors = validate_no_duplicate_tickers(df)
        assert len(errors) == 1
        assert "AAPL" in errors[0]


class TestCaseOnlyTickerCollisions:
    def test_clean(self):
        assert validate_case_only_ticker_collisions(_make_df()) == []

    def test_case_only_collision_flagged(self):
        # The VCEL/VCEl case: exact-match dup check misses this; this one catches it.
        df = _make_df({
            "Ticker": ["VCEL", "VCEl", "GOOG"],
            "Company Name": ["Vericel", "Vericel Corp", "Google"],
            "Sector (JP)": ["MedTech", "MedTech", "Tech"],
        })
        warnings = validate_case_only_ticker_collisions(df)
        assert len(warnings) == 1
        assert "VCEL" in warnings[0] and "VCEl" in warnings[0]
        # And it is NOT reported by the exact-match duplicate check.
        assert validate_no_duplicate_tickers(df) == []

    def test_suffix_collision_not_flagged(self):
        # Legitimate exchange dual-listing (ROG + ROG.SW) differs as raw strings
        # -> must NOT be flagged as a case-only collision.
        df = _make_df({
            "Ticker": ["ROG", "ROG.SW", "GOOG"],
            "Company Name": ["Roche", "Roche", "Google"],
            "Sector (JP)": ["Biopharma", "Biopharma", "Tech"],
        })
        assert validate_case_only_ticker_collisions(df) == []


class TestDuplicateCompanies:
    def test_clean(self):
        assert validate_duplicate_companies(_make_df()) == []

    def test_dupes_normalized(self):
        df = _make_df({
            "Ticker": ["A", "B", "C"],
            "Company Name": ["Apple Inc", "Apple Inc.", "Google LLC"],
            "Sector (JP)": ["X", "Y", "Z"],
        })
        warnings = validate_duplicate_companies(df)
        assert len(warnings) == 1
        assert "apple" in warnings[0].lower()


class TestRunAllValidations:
    def test_clean_df(self):
        errors, warnings = run_all_validations(_make_df())
        assert len(errors) == 0

    def test_errors_and_warnings(self):
        df = _make_df({
            "Ticker": ["AAPL", "AAPL"],
            "Company Name": ["Apple", "Apple"],
            "Sector (JP)": ["Tech", "Tech"],
        })
        errors, warnings = run_all_validations(df)
        assert len(errors) >= 1  # duplicate ticker
        assert len(warnings) >= 1  # duplicate company + missing exchange
