"""Structured I/O for candidate company discovery.

Provides JSON schema validation, deduplication against the universe,
staging before CSV mutation, and commit logic.
"""

import csv
import json
from datetime import date
from pathlib import Path

import pandas as pd

from config import CSV_PATH, DATA_DIR, TODAY
from ticker_utils import normalize_company_for_comparison
from audit import log_change
from logging_utils import get_logger

logger = get_logger("discovery.candidates")

# ── JSON Schema ──────────────────────────────────────────────────────────────

VALID_TRIGGERS = {"IPO", "Direct listing", "Spin-off", "Carve-out", "New candidate", "Russell addition"}

CANDIDATE_REQUIRED_FIELDS = {"company", "ticker", "exchange", "sector", "trigger"}

CANDIDATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["date", "candidates"],
    "properties": {
        "date": {"type": "string", "format": "date"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": list(CANDIDATE_REQUIRED_FIELDS),
                "properties": {
                    "company": {"type": "string"},
                    "ticker": {"type": "string"},
                    "exchange": {"type": "string"},
                    "market_cap": {"type": ["number", "null"]},
                    "sector": {"type": "string"},
                    "subsector": {"type": ["string", "null"]},
                    "listing_date": {"type": ["string", "null"]},
                    "trigger": {"enum": list(VALID_TRIGGERS)},
                    "peers": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "business_summary": {"type": "string"},
                    "approved": {"type": "boolean", "default": False},
                },
            },
        },
    },
}


# ── Input: write universe summary for Claude ─────────────────────────────────

def write_discovery_input(csv_path=None, output_path=None):
    """Write a JSON summary of the current universe for Claude's dedup checks.

    Output contains all tickers, company names, and sectors.
    """
    csv_path = Path(csv_path) if csv_path else CSV_PATH
    output_path = Path(output_path) if output_path else (DATA_DIR / f"discovery_input_{TODAY}.json")

    df = pd.read_csv(csv_path)

    tickers = df["Ticker"].dropna().astype(str).str.strip().tolist()
    companies = df["Company Name"].dropna().astype(str).str.strip().tolist()

    # Sector breakdown
    sector_col = "Sector (JP)" if "Sector (JP)" in df.columns else "Sector"
    sector_counts = df[sector_col].value_counts().to_dict() if sector_col in df.columns else {}

    summary = {
        "date": TODAY,
        "total_tickers": len(tickers),
        "tickers": tickers,
        "companies": companies,
        "sector_breakdown": {str(k): int(v) for k, v in sector_counts.items()},
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    logger.info("Discovery input written: %s (%d tickers)", output_path, len(tickers))
    return output_path


# ── Output: validate and read Claude's candidate list ────────────────────────

def validate_discovery_output(path):
    """Validate a discovery output JSON file.

    Returns (valid_candidates, errors) where valid_candidates is a list of
    candidate dicts that passed validation, and errors is a list of error strings.
    """
    errors = []

    if not Path(path).exists():
        return [], [f"Discovery output file not found: {path}"]

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [], [f"Invalid JSON in discovery output: {e}"]

    if not isinstance(data, dict):
        return [], ["Discovery output must be a JSON object"]

    if "candidates" not in data:
        return [], ["Discovery output missing 'candidates' key"]

    candidates = data["candidates"]
    if not isinstance(candidates, list):
        return [], ["'candidates' must be an array"]

    # Load current universe for dedup
    df = pd.read_csv(CSV_PATH)
    existing_tickers = set(df["Ticker"].dropna().astype(str).str.strip().str.upper())
    existing_names = set(
        df["Company Name"].dropna().astype(str).apply(normalize_company_for_comparison)
    )

    valid = []
    for i, c in enumerate(candidates):
        prefix = f"Candidate {i + 1}"

        # Check required fields
        missing = CANDIDATE_REQUIRED_FIELDS - set(c.keys())
        if missing:
            errors.append(f"{prefix}: missing required fields {missing}")
            continue

        ticker = str(c["ticker"]).strip().upper()
        company = str(c["company"]).strip()
        trigger = c.get("trigger", "")

        # Validate trigger
        if trigger not in VALID_TRIGGERS:
            errors.append(f"{prefix} ({ticker}): invalid trigger '{trigger}', must be one of {VALID_TRIGGERS}")
            continue

        # Check for duplicates against universe
        if ticker in existing_tickers:
            errors.append(f"{prefix} ({ticker}): already in coverage universe")
            continue

        normalized_name = normalize_company_for_comparison(company)
        if normalized_name and normalized_name in existing_names:
            errors.append(f"{prefix} ({ticker}): company name '{company}' matches existing entry (normalized: '{normalized_name}')")
            continue

        valid.append(c)

    return valid, errors


# ── Staging: write candidates to review file before CSV mutation ─────────────

def stage_candidates(candidates, staging_path=None):
    """Write validated candidates to a staging CSV for review.

    Returns the staging path.
    """
    staging_path = Path(staging_path) if staging_path else (DATA_DIR / f"staged_candidates_{TODAY}.csv")

    fields = [
        "approved", "ticker", "company", "exchange", "market_cap",
        "sector", "subsector", "listing_date", "trigger", "peers", "reason",
    ]

    staging_path.parent.mkdir(parents=True, exist_ok=True)
    with open(staging_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for c in candidates:
            row = {k: c.get(k, "") for k in fields}
            row["approved"] = str(c.get("approved", False)).lower()
            if isinstance(row.get("peers"), list):
                row["peers"] = "; ".join(row["peers"])
            writer.writerow(row)

    logger.info("Staged %d candidates to %s", len(candidates), staging_path)
    return staging_path


def read_staged_candidates(staging_path):
    """Read the staging CSV and return list of candidate dicts.

    Only returns candidates where approved == 'true'.
    """
    if not Path(staging_path).exists():
        return []

    approved = []
    with open(staging_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("approved", "").strip().lower() == "true":
                approved.append(row)

    return approved


# ── Commit: append staged candidates to the canonical CSV ────────────────────

def commit_staged_candidates(staging_path, csv_path=None):
    """Append approved staged candidates to the canonical CSV.

    Logs each addition to the change log. Returns count of tickers added.
    """
    csv_path = csv_path or CSV_PATH
    approved = read_staged_candidates(staging_path)

    if not approved:
        logger.info("No approved candidates to commit")
        return 0

    df = pd.read_csv(csv_path)
    existing_tickers = set(df["Ticker"].dropna().astype(str).str.strip().str.upper())

    added = 0
    rows_to_add = []
    for c in approved:
        ticker = str(c.get("ticker", "")).strip()
        if not ticker or ticker.upper() in existing_tickers:
            logger.warning("Skipping %s (already in universe or blank)", ticker)
            continue

        new_row = {
            "Ticker": ticker,
            "Exchange": c.get("exchange", ""),
            "Company Name": c.get("company", ""),
            "Sector (JP)": c.get("sector", ""),
            "Subsector (JP)": c.get("subsector", ""),
        }
        rows_to_add.append(new_row)
        existing_tickers.add(ticker.upper())

        log_change(
            action="add",
            ticker=ticker,
            company=c.get("company", ""),
            sector=c.get("sector", ""),
            source=f"discovery-{TODAY}",
        )
        added += 1

    if rows_to_add:
        new_df = pd.DataFrame(rows_to_add)
        combined = pd.concat([df, new_df], ignore_index=True)
        combined.to_csv(csv_path, index=False)
        logger.info("Committed %d new tickers to %s", added, csv_path)

    return added
