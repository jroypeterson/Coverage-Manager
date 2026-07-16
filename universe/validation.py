"""Coverage universe CSV validation.

Checks for schema issues, duplicates, and data quality problems.
Returns errors (hard failures) and warnings (informational).
"""

import pandas as pd

from config import CSV_PATH, REQUIRED_COLUMNS, EXPECTED_COLUMNS, ALLOWED_SECTORS_JP
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


def validate_case_only_ticker_collisions(df):
    """Warn on tickers that collide ONLY by case, e.g. 'VCEL' and 'VCEl'.

    Returns warnings (not errors). A case-only collision is almost always a
    data-entry typo that silently duplicates a company: `validate_no_duplicate_
    tickers` above uses an exact match and so misses it, and the metadata
    builder's later-row-wins then hides one spelling — the exact way the
    VCEL/VCEl duplicate lived in the universe unnoticed.

    Deliberately narrower than the exchange-suffix collisions the metadata
    builder tracks as `normalization_collisions`: those legitimate dual-listings
    ('ROG' + 'ROG.SW' -> ROG) differ as raw strings, so they never group
    together under `.upper()` and are never flagged here. This makes the check
    false-positive-free on real dual-listings.

    A warning, not an error, on purpose: it must not gate the weekly build, and
    a genuinely mixed-case ticker (rare, e.g. a Bloomberg-style line) shouldn't
    hard-fail — a human dedups at the source.
    """
    warnings = []
    if "Ticker" not in df.columns:
        return warnings
    groups = {}
    for t in df["Ticker"].dropna().astype(str):
        s = t.strip()
        if s:
            groups.setdefault(s.upper(), set()).add(s)
    collisions = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
    if collisions:
        examples = list(collisions.values())[:10]
        warnings.append(
            f"{len(collisions)} case-only ticker collision(s) (likely typos; "
            f"dedup at the source): {examples}")
    return warnings


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


def validate_sector_taxonomy(df):
    """Check that every Sector (JP) value is in ALLOWED_SECTORS_JP. Returns errors.

    Catches stale taxonomy values (e.g. the retired "PA" sector) from slipping
    back in via copy/paste of old CSV rows.
    """
    errors = []
    if "Sector (JP)" not in df.columns:
        return errors
    values = df["Sector (JP)"].fillna("").astype(str).str.strip()
    present = values[values != ""]
    stale = sorted(set(present[~present.isin(ALLOWED_SECTORS_JP)]))
    if stale:
        errors.append(f"Unknown Sector (JP) value(s) (not in taxonomy): {stale}")
    return errors


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
    errors.extend(validate_sector_taxonomy(df))

    warnings.extend(validate_case_only_ticker_collisions(df))
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
