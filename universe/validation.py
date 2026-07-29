"""Coverage universe CSV validation.

Checks for schema issues, duplicates, and data quality problems.
Returns errors (hard failures) and warnings (informational).
"""

import statistics

import pandas as pd

from config import CSV_PATH, REQUIRED_COLUMNS, EXPECTED_COLUMNS, ALLOWED_SECTORS_JP
from ticker_utils import COUNTRY_TO_ISIN_PREFIXES, normalize_company_for_comparison
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


def validate_country_prefix_coverage(df):
    """Warn on any populated country value with no ISIN-prefix mapping.

    An incomplete `COUNTRY_TO_ISIN_PREFIXES` map does not fail anything — the
    prefix guard in `enrich.validate_isin_for_row` simply skips rows whose
    country it does not know, which is the exact silent-no-op class this
    workspace keeps getting burned by. Codex found the map missing Ireland,
    Netherlands, Israel, Singapore and the whole offshore incorporation set
    (2026-07-28) — 10 live country values, ~48 rows the guard silently never
    validated. The map was completed the same day; THIS check exists so the
    next gap is visible instead of invisible.

    Two things a warning here can mean, both wanting a human:
      - a genuinely new country entered the universe -> add it to
        `ticker_utils.COUNTRY_TO_ISO2` (the prefix map derives from it);
      - the value is not a country name at all -> fix the row (live case:
        `MICC` carries `Country (HQ) = "NL"`, an alpha-2 code where a name
        belongs).

    A warning, never an error, on purpose: it must not gate the weekly build
    (matches `validate_case_only_ticker_collisions`).
    """
    warnings = []
    unmapped = {}
    for col in ("Country (HQ)", "Country (Listing)"):
        if col not in df.columns:
            continue
        for v in df[col].fillna("").astype(str):
            s = v.strip()
            if s and s not in COUNTRY_TO_ISIN_PREFIXES:
                unmapped[s] = unmapped.get(s, 0) + 1
    if unmapped:
        detail = ", ".join(f"{k!r} ({n} row(s))" for k, n in sorted(unmapped.items()))
        warnings.append(
            f"{len(unmapped)} country value(s) with no ISIN-prefix mapping - the "
            f"ISIN prefix guard silently skips these rows; add the country to "
            f"ticker_utils.COUNTRY_TO_ISO2 or fix the value: {detail}")
    return warnings


def validate_listing_date_agreement(df, max_year_gap=1):
    """Warn when `Year Listed` disagrees with a verified `IPO Date`.

    **This is the re-listing detector.** `Year Listed` is populated from FMP's
    `ipoDate`, which is a fact about a *brand*, not about the security currently
    trading. When a company is taken private or acquired and later spun back out,
    FMP keeps reporting the original listing while a new registrant (new CIK) is
    what actually trades. `IPO Date` comes from Renaissance and describes the
    current offering, so a large gap between the two is the tell.

    Live case (2026-07-28): **SNDK** read `Year Listed` 1995 — SanDisk's original
    IPO — while the security trading under that ticker is the February 2025 spin
    out of Western Digital, CIK 2023554, a different registrant entirely. Wrong
    under either definition anyone queries: an IPO-cohort screen for 2025 misses
    it, and a "public since 1995" screen wrongly includes a security that did not
    exist. Same class: Kyndryl, Solventum, GE Vernova.

    **The convention this enforces:** `Year Listed`, `IPO Date`, `Est Lockup 90d`
    and `Est Lockup 180d` all describe **the listing currently trading**, never the
    issuer's earliest ever. Prior listings belong in `ipo_tracker`'s event registry
    (5,459 events back to 2016, with `deal_type`), joined on CIK/ticker — not
    duplicated here.

    A warning, never an error: a one-year gap is routine (a December offer date
    against a January first-trade year), and this must not gate the weekly build.
    """
    warnings = []
    if not {"Ticker", "Year Listed", "IPO Date"} <= set(df.columns):
        return warnings

    mismatches = []
    for _, row in df.iterrows():
        raw_year = str(row.get("Year Listed") or "").strip()
        raw_ipo = str(row.get("IPO Date") or "").strip()
        if not raw_year or not raw_ipo or len(raw_ipo) < 4:
            continue                      # nothing to compare is not a finding
        try:
            listed = int(raw_year)
            offered = int(raw_ipo[:4])
        except ValueError:
            continue                      # unparseable is not a disagreement
        if abs(listed - offered) > max_year_gap:
            mismatches.append(
                f"{str(row.get('Ticker') or '').strip()} "
                f"(Year Listed {listed} vs IPO Date {raw_ipo})")

    if mismatches:
        warnings.append(
            f"{len(mismatches)} row(s) where Year Listed disagrees with the "
            f"verified IPO Date by more than {max_year_gap}y - likely a re-listing "
            f"(spin-off/re-IPO) where Year Listed still reflects the ORIGINAL "
            f"offering; both columns must describe the listing currently trading: "
            f"{mismatches[:10]}")
    return warnings


def validate_relisting_cik_cohort(df, neighbours=25, max_year_gap=15):
    """Warn when a row's CIK implies a far newer registrant than `Year Listed`.

    The companion to `validate_listing_date_agreement`, and the one that actually
    catches **spin-offs**. That check needs a verified `IPO Date`, which a spin
    never has — there was no offering for Renaissance to record — so SNDK, the case
    that motivated both, slipped straight through it.

    The signal here needs no API call: **a CIK is assigned when a registrant first
    files with the SEC, so a registrant cannot predate its own CIK.** CIKs are
    issued in ascending order, so a row claiming a 1995 listing while carrying a
    CIK typical of 2025 registrants is not a 1995 security. SNDK reads `Year Listed`
    1995 (SanDisk's original IPO) against CIK 2023554 — the February 2025 spin out
    of Western Digital, a different registrant entirely.

    Implemented as a local-neighbourhood outlier test rather than a hardcoded
    year->CIK table: rows are sorted by CIK and each is compared against the median
    `Year Listed` of its `neighbours` closest CIKs on **both** sides. Self-calibrating
    as the universe grows, and nothing to maintain.

    Two deliberate exclusions, both to keep it false-positive-free:

    - **US exchanges only.** A foreign issuer listed on its home market in 1985 and
      registered with the SEC decades later for an ADR has a legitimately old
      `Year Listed` against a recent CIK. That is not a re-listing.
    - **Two-sided windows only.** Rows within `neighbours` of either end of the
      CIK ordering are skipped: their neighbourhood is one-sided and the median is
      pulled hard in one direction. Without this, BRO (Brown & Brown, the lowest CIK
      in the file) flags purely as an edge artefact.

    Measured on the live universe 2026-07-28: **1 flag (SNDK), 0 false positives
    across 667 US-listed rows** at gaps of both 15 and 20 years.

    A warning, never an error — the fix is a human deciding what `Year Listed`
    should say, and it must not gate the weekly build.
    """
    warnings = []
    if not {"Ticker", "CIK", "Year Listed", "Exchange"} <= set(df.columns):
        return warnings

    us_exchanges = {"NASDAQ", "NYSE", "NYSE AMERICAN", "NYSEAMERICAN", "AMEX",
                    "NYSE ARCA"}
    rows = []
    for _, r in df.iterrows():
        cik = str(r.get("CIK") or "").strip()
        year = str(r.get("Year Listed") or "").strip()
        exch = str(r.get("Exchange") or "").strip().upper()
        if cik.isdigit() and year.isdigit() and exch in us_exchanges:
            rows.append((int(cik), int(year), str(r.get("Ticker") or "").strip()))
    rows.sort()

    if len(rows) < neighbours * 2 + 2:
        return warnings                    # too few rows to calibrate against

    flagged = []
    for i, (cik, year, ticker) in enumerate(rows):
        if i < neighbours or i >= len(rows) - neighbours:
            continue                       # one-sided neighbourhood: skip
        window = [rows[j][1] for j in range(i - neighbours, i + neighbours + 1)
                  if j != i]
        median_year = statistics.median(window)
        if year < median_year - max_year_gap:
            flagged.append(f"{ticker} (Year Listed {year}, but CIK {cik} sits among "
                           f"registrants listing ~{int(median_year)})")

    if flagged:
        warnings.append(
            f"{len(flagged)} row(s) whose CIK implies a much newer registrant than "
            f"Year Listed - likely a spin-off or re-IPO where Year Listed still "
            f"reflects the ORIGINAL listing rather than the security now trading: "
            f"{flagged[:10]}")
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
    warnings.extend(validate_country_prefix_coverage(df))
    warnings.extend(validate_duplicate_companies(df))
    warnings.extend(validate_exchange_populated(df))
    warnings.extend(validate_subsector_populated(df))
    warnings.extend(validate_listing_date_agreement(df))
    warnings.extend(validate_relisting_cik_cohort(df))

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
