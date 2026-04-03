"""Coverage universe CSV validation.

Checks for schema issues, duplicates, and data quality problems.
Returns errors (hard failures) and warnings (informational).
"""

import pandas as pd

from config import CSV_PATH, REQUIRED_COLUMNS, EXPECTED_COLUMNS
from ticker_utils import normalize_company_for_comparison
from logging_utils import get_logger

logger = get_logger("validation")


def validate_required_columns(df):
    """Check that required columns exist. Returns list of error strings."""
    errors = []
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            errors.append(f"Missing required column: '{col}'")
    return errors


def validate_no_orphaned_columns(df):
    """Check for Unnamed columns (artifacts of bad CSV reads). Returns errors."""
    errors = []
    unnamed = [c for c in df.columns if str(c).startswith("Unnamed")]
    if unnamed:
        errors.append(f"Orphaned columns found: {unnamed}")
    return errors


def validate_no_blank_tickers(df):
    """Check that no Ticker values are blank/NA. Returns errors."""
    errors = []
    if "Ticker" not in df.columns:
        return errors
    blank_count = df["Ticker"].isna().sum() + (df["Ticker"].astype(str).str.strip() == "").sum()
    if blank_count > 0:
        errors.append(f"{blank_count} blank/NA Ticker value(s) found")
    return errors


def validate_no_duplicate_tickers(df):
    """Check for duplicate tickers. Returns errors."""
    errors = []
    if "Ticker" not in df.columns:
        return errors
    dupes = df["Ticker"].dropna()
    dupes = dupes[dupes.astype(str).str.strip() != ""]
    dupe_counts = dupes.value_counts()
    dupe_tickers = dupe_counts[dupe_counts > 1]
    if len(dupe_tickers) > 0:
        examples = list(dupe_tickers.index[:10])
        errors.append(f"{len(dupe_tickers)} duplicate ticker(s): {examples}")
    return errors


def validate_duplicate_companies(df):
    """Check for possible duplicate companies by exact normalized name match.

    Returns warnings (not errors) since company naming can be messy.
    """
    warnings = []
    if "Company Name" not in df.columns:
        return warnings
    names = df["Company Name"].dropna().astype(str)
    normalized = names.apply(normalize_company_for_comparison)
    normalized = normalized[normalized.str.strip() != ""]
    dupe_counts = normalized.value_counts()
    dupe_names = dupe_counts[dupe_counts > 1]
    if len(dupe_names) > 0:
        examples = list(dupe_names.index[:10])
        warnings.append(f"{len(dupe_names)} possible duplicate company name(s) (normalized): {examples}")
    return warnings


def validate_exchange_populated(df):
    """Check that Exchange column is populated. Returns warnings."""
    warnings = []
    if "Exchange" not in df.columns:
        warnings.append("Exchange column missing entirely")
        return warnings
    empty_count = df["Exchange"].isna().sum() + (df["Exchange"].astype(str).str.strip() == "").sum()
    if empty_count > 0:
        warnings.append(f"{empty_count} ticker(s) missing Exchange value")
    return warnings


def validate_subsector_populated(df):
    """Check that Subsector column is populated. Returns warnings."""
    warnings = []
    if "Subsector (JP)" not in df.columns:
        return warnings
    empty_count = df["Subsector (JP)"].isna().sum() + (df["Subsector (JP)"].astype(str).str.strip() == "").sum()
    if empty_count > 0:
        warnings.append(f"{empty_count} ticker(s) missing Subsector (JP) value")
    return warnings


def run_all_validations(df):
    """Run all validators. Returns (errors, warnings) as lists of strings."""
    errors = []
    warnings = []

    errors.extend(validate_required_columns(df))
    errors.extend(validate_no_orphaned_columns(df))
    errors.extend(validate_no_blank_tickers(df))
    errors.extend(validate_no_duplicate_tickers(df))

    warnings.extend(validate_duplicate_companies(df))
    warnings.extend(validate_exchange_populated(df))
    warnings.extend(validate_subsector_populated(df))

    return errors, warnings


def main():
    """CLI entry point for validation."""
    logger.info("Validating %s", CSV_PATH)

    df = pd.read_csv(CSV_PATH)
    total_rows = len(df)
    logger.info("Loaded %d rows", total_rows)

    errors, warnings_list = run_all_validations(df)

    if warnings_list:
        print(f"\n  WARNINGS ({len(warnings_list)}):")
        for w in warnings_list:
            print(f"    - {w}")

    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    - {e}")
        print(f"\n  Validation FAILED: {len(errors)} error(s), {len(warnings_list)} warning(s) in {total_rows} rows")
        return 1
    else:
        print(f"\n  Validation PASSED: 0 errors, {len(warnings_list)} warning(s) in {total_rows} rows")
        return 0
