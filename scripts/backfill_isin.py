"""One-off ISIN backfill for rows with cleared ISIN fields.

Targets the 15 rows whose ISINs were cleared in the May 2026 hygiene pass
(5 biotech rows with overwritten Company Name + 10 sweep rows with foreign
or malformed ISINs). For each, fetch the FMP profile and apply the same
validate_isin_for_row guard the live enrichment path uses.

Usage:
    python scripts/backfill_isin.py [--dry-run]

Re-runnable: skips rows whose ISIN is already populated, and skips writing
when FMP returns nothing or its ISIN fails the country-prefix guard.
"""
import argparse
import csv
import sys
from pathlib import Path

# Add repo root to sys.path so the standalone script can import project code
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config import API_KEYS, CSV_PATH  # noqa: E402
from logging_utils import configure_logging, get_logger  # noqa: E402
from providers.fmp_provider import fetch_profile as fmp_fetch_profile  # noqa: E402
from universe.enrich import validate_isin_for_row  # noqa: E402

logger = get_logger("backfill_isin")

TARGETS = (
    "ADAP", "FGEN", "LIAN", "MNK", "ZOM",            # original 5
    "DAE", "IMA", "NBP", "IMCR", "ACH",              # sweep
    "RPRX", "SPOT", "WMT", "JAN", "RBLX",
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would change without writing the CSV.")
    args = ap.parse_args()

    configure_logging()

    fmp_key = API_KEYS.get("FMP_API_KEY", "")
    if not fmp_key:
        logger.error("FMP_API_KEY missing — aborting")
        return 2

    csv_path = Path(CSV_PATH)
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    targets_set = set(TARGETS)
    summary = []

    for row in rows:
        ticker = (row.get("Ticker") or "").strip()
        if ticker not in targets_set:
            continue
        existing = (row.get("ISIN") or "").strip()
        if existing:
            summary.append((ticker, "skip (already set)", existing))
            continue

        profile = fmp_fetch_profile(ticker, fmp_key)
        if not profile:
            summary.append((ticker, "fmp returned nothing", ""))
            continue

        candidate = str(profile.get("isin", "") or "").strip()
        if not candidate:
            summary.append((ticker, "no isin from fmp", ""))
            continue

        checked = validate_isin_for_row(candidate, row, ticker=ticker)
        if not checked:
            summary.append((ticker, f"REJECTED (prefix mismatch): {candidate}", ""))
            continue

        row["ISIN"] = checked
        summary.append((ticker, "ok", checked))

    if not args.dry_run:
        # Write without BOM — the file is BOM-less in git, and downstream
        # readers (e.g., universe/artifacts.py) open with plain utf-8 and
        # would otherwise see "﻿Ticker" as the first header.
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    print(f"\n{'TICKER':<8} {'RESULT':<40} ISIN")
    print("-" * 72)
    for tkr, result, isin in summary:
        print(f"{tkr:<8} {result:<40} {isin}")

    if args.dry_run:
        print("\n(dry run — CSV not written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
