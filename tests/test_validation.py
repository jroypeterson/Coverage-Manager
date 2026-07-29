"""Tests for validation.py — CSV schema and data quality checks."""

import pandas as pd
import pytest

from universe.validation import (
    validate_required_columns,
    validate_no_orphaned_columns,
    validate_no_blank_tickers,
    validate_no_duplicate_tickers,
    validate_case_only_ticker_collisions,
    validate_country_prefix_coverage,
    validate_duplicate_companies,
    validate_exchange_populated,
    validate_listing_date_agreement,
    validate_relisting_cik_cohort,
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


# ── country → ISIN-prefix coverage (Codex R3, 2026-07-28) ─────────────────────
#
# An incomplete COUNTRY_TO_ISIN_PREFIXES map means the ISIN prefix guard
# silently does nothing for rows in the unmapped country - the exact
# silent-no-op class this workspace keeps getting burned by. This validator
# makes the NEXT gap visible instead of invisible.

class TestCountryPrefixCoverage:
    def test_mapped_countries_are_clean(self):
        df = _make_df({
            "Ticker": ["A", "B"],
            "Company Name": ["A Co", "B Co"],
            "Sector (JP)": ["Tech", "Tech"],
            "Country (HQ)": ["United States", "Ireland"],
            "Country (Listing)": ["United States", "United States"],
        })
        assert validate_country_prefix_coverage(df) == []

    def test_unmapped_country_warns_with_value_and_row_count(self):
        df = _make_df({
            "Ticker": ["A", "B", "C"],
            "Company Name": ["A Co", "B Co", "C Co"],
            "Sector (JP)": ["Tech", "Tech", "Tech"],
            "Country (HQ)": ["Ruritania", "Ruritania", "United States"],
            "Country (Listing)": ["United States", "", "United States"],
        })
        warnings = validate_country_prefix_coverage(df)
        assert len(warnings) == 1
        assert "Ruritania" in warnings[0]
        assert "2 row(s)" in warnings[0]

    def test_the_live_nl_defect_class_is_flagged(self):
        """An alpha-2 code stored where a country NAME belongs (MICC carries
        HQ='NL'). Not a mapping to add - a value for a human to fix - but it
        must be VISIBLE."""
        df = _make_df({
            "Ticker": ["MICC"],
            "Company Name": ["The Magnum Ice Cream Company N.V."],
            "Sector (JP)": ["Consumer"],
            "Country (HQ)": ["NL"],
            "Country (Listing)": ["United States"],
        })
        warnings = validate_country_prefix_coverage(df)
        assert len(warnings) == 1 and "'NL'" in warnings[0]

    def test_blank_is_not_a_finding(self):
        df = _make_df({
            "Ticker": ["A"],
            "Company Name": ["A Co"],
            "Sector (JP)": ["Tech"],
            "Country (HQ)": [""],
            "Country (Listing)": [""],
        })
        assert validate_country_prefix_coverage(df) == []

    def test_missing_columns_is_not_a_finding(self):
        assert validate_country_prefix_coverage(_make_df()) == []

    def test_is_a_warning_in_run_all_validations_never_an_error(self):
        """Wired like validate_case_only_ticker_collisions: it must not gate
        the weekly build."""
        df = _make_df({
            "Ticker": ["A"],
            "Company Name": ["A Co"],
            "Sector (JP)": ["Tech"],
            "Exchange": ["NASDAQ"],
            "Subsector (JP)": ["X"],
            "Country (HQ)": ["Ruritania"],
            "Country (Listing)": ["Ruritania"],
        })
        errors, warnings = run_all_validations(df)
        assert not any("Ruritania" in e for e in errors)
        assert any("Ruritania" in w for w in warnings)


# ── re-listing detectors (2026-07-28) ─────────────────────────────────────────
#
# SNDK read Year Listed 1995 (SanDisk's original IPO) while the security trading
# under that ticker is the Feb-2025 spin out of Western Digital, CIK 2023554 - a
# different registrant. Two detectors because they catch different halves: the
# IPO-Date one catches re-IPOs that had an actual offering; the CIK-cohort one
# catches spin-offs, which have no offering for Renaissance to record and so slip
# straight past the first check.

def _cohort_df(extra_rows=None):
    """A CIK/Year-ordered universe wide enough for a two-sided window."""
    rows = []
    for i in range(80):
        rows.append({"Ticker": f"T{i:03d}", "CIK": str(1_500_000 + i * 8_000),
                     "Year Listed": str(2005 + i // 4), "Exchange": "NASDAQ",
                     "IPO Date": ""})
    rows.extend(extra_rows or [])
    return pd.DataFrame(rows)


class TestListingDateAgreement:
    def test_large_gap_between_year_listed_and_ipo_date_warns(self):
        df = pd.DataFrame([{"Ticker": "XYZ", "Year Listed": "1995",
                            "IPO Date": "2025-02-10"}])
        w = validate_listing_date_agreement(df)
        assert len(w) == 1 and "XYZ" in w[0]

    def test_one_year_gap_is_routine_and_does_not_warn(self):
        """A December offer date against a January first-trade year."""
        df = pd.DataFrame([{"Ticker": "XYZ", "Year Listed": "2026",
                            "IPO Date": "2025-12-30"}])
        assert validate_listing_date_agreement(df) == []

    def test_blank_ipo_date_is_not_a_finding(self):
        df = pd.DataFrame([{"Ticker": "SNDK", "Year Listed": "1995", "IPO Date": ""}])
        assert validate_listing_date_agreement(df) == []

    def test_unparseable_values_are_skipped(self):
        df = pd.DataFrame([{"Ticker": "XYZ", "Year Listed": "n/a",
                            "IPO Date": "garbage"}])
        assert validate_listing_date_agreement(df) == []


class TestRelistingCikCohort:
    def test_old_year_with_a_modern_cik_warns(self):
        df = _cohort_df([{"Ticker": "SNDK", "CIK": "1900000", "Year Listed": "1995",
                          "Exchange": "NASDAQ", "IPO Date": ""}])
        w = validate_relisting_cik_cohort(df)
        assert len(w) == 1 and "SNDK" in w[0]

    def test_a_consistent_row_does_not_warn(self):
        df = _cohort_df([{"Ticker": "OK1", "CIK": "1900000", "Year Listed": "2024",
                          "Exchange": "NASDAQ", "IPO Date": ""}])
        assert validate_relisting_cik_cohort(df) == []

    def test_foreign_lines_are_excluded(self):
        """An ADR's home-market listing legitimately predates its SEC registration."""
        df = _cohort_df([{"Ticker": "4503.T", "CIK": "1900000", "Year Listed": "1949",
                          "Exchange": "TSE", "IPO Date": ""}])
        assert validate_relisting_cik_cohort(df) == []

    def test_rows_at_the_edge_of_the_cik_ordering_are_skipped(self):
        """One-sided neighbourhoods pull the median hard; BRO flagged purely on this."""
        df = _cohort_df([{"Ticker": "LOWEST", "CIK": "1000", "Year Listed": "1981",
                          "Exchange": "NYSE", "IPO Date": ""}])
        assert validate_relisting_cik_cohort(df) == []

    def test_too_few_rows_to_calibrate_is_not_a_finding(self):
        df = pd.DataFrame([{"Ticker": "A", "CIK": "2000000", "Year Listed": "1990",
                            "Exchange": "NYSE", "IPO Date": ""}])
        assert validate_relisting_cik_cohort(df) == []

    def test_blank_cik_rows_are_ignored(self):
        df = _cohort_df([{"Ticker": "NOCIK", "CIK": "", "Year Listed": "1990",
                          "Exchange": "NYSE", "IPO Date": ""}])
        assert validate_relisting_cik_cohort(df) == []
