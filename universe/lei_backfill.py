"""Backfill a Legal Entity Identifier (LEI) column from GLEIF, keyed by ISIN.

The universe already carries the cross-provider *security* identifiers (ISIN +
FIGI). This adds the official cross-provider *entity* identifier — the **LEI**
(ISO 17442, the global regulatory standard) — so the ticker list can be joined
to any data-provider / regulator dataset that keys on LEI.

Source: GLEIF's free API (`api.gleif.org`, no key) — `filter[isin]=<ISIN>`
returns the LEI record(s) for a security. LEI rarely changes, so results
(including authoritative "no LEI" answers) are cached 90 days; reruns only fetch
rows still missing an LEI, making the weekly pass cheap after the first fill.

Scope: only rows that have an ISIN and a blank LEI. Foreign names without a CIK
still get an LEI here (ISIN is global) — that's the point. Non-gating: writes the
CSV, never raises on a single lookup failure.

CLI: `python cli.py backfill-lei [--no-cache] [--limit N]`
"""

import time
from datetime import date

import pandas as pd
import requests

from cache import cache_get, cache_set
from config import CSV_PATH
from logging_utils import get_logger, log_exception

logger = get_logger("lei_backfill")

GLEIF_URL = "https://api.gleif.org/api/v1/lei-records?filter[isin]={isin}"
_HEADERS = {"Accept": "application/vnd.api+json"}
LEI_CACHE_NS = "lei_by_isin"
LEI_CACHE_TTL_HOURS = 24.0 * 90          # LEI is stable; 90-day cache
REQUEST_SPACING_SEC = 0.3                # ~3 req/s — polite to GLEIF's free API


def fetch_lei(isin, use_cache=True):
    """Return (lei, legal_name) for an ISIN via GLEIF, or ("", "") if none.

    A confirmed "no LEI" (HTTP 200, empty data) IS cached (it's an authoritative
    answer); a transient error (non-200 / exception) is NOT cached so the next
    run retries it.
    """
    isin = (isin or "").strip()
    if not isin:
        return "", ""
    if use_cache:
        cached = cache_get(LEI_CACHE_NS, isin, LEI_CACHE_TTL_HOURS)
        if cached is not None:
            return cached.get("lei", ""), cached.get("name", "")
    for attempt in range(3):
        try:
            r = requests.get(GLEIF_URL.format(isin=isin), headers=_HEADERS, timeout=20)
            if r.status_code == 429:           # rate-limited — back off + retry
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                return "", ""                  # transient — don't cache
            data = r.json().get("data", []) or []
            if not data:
                if use_cache:
                    cache_set(LEI_CACHE_NS, isin, {"lei": "", "name": ""})
                return "", ""
            rec = data[0]
            lei = rec.get("id", "") or ""
            name = (rec.get("attributes", {}).get("entity", {})
                    .get("legalName", {}).get("name", "") or "")
            if use_cache:
                cache_set(LEI_CACHE_NS, isin, {"lei": lei, "name": name})
            return lei, name
        except Exception as e:  # noqa: BLE001 — surface, keep going
            log_exception(logger, f"GLEIF lookup failed for {isin}", e)
            return "", ""
    return "", ""


def _ensure_lei_column(df):
    """Add an LEI column (just after CIK) if missing. Returns the df."""
    if "LEI" in df.columns:
        return df
    idx = (df.columns.get_loc("CIK") + 1) if "CIK" in df.columns else len(df.columns)
    df.insert(idx, "LEI", "")
    return df


def backfill(csv_path=None, use_cache=True, limit=None, _fetcher=None):
    """Fill the LEI column for rows that have an ISIN but no LEI.

    `_fetcher`: injectable (isin, use_cache)->(lei, name) for tests.
    Returns dict: {total, with_isin, had_lei, attempted, filled, still_missing}.
    """
    csv_path = csv_path or CSV_PATH
    fetch = _fetcher or fetch_lei
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    df = _ensure_lei_column(df)

    have_isin = df["ISIN"].str.strip() != ""
    have_lei = df["LEI"].str.strip() != ""
    todo_idx = df.index[have_isin & ~have_lei].tolist()
    if limit is not None:
        todo_idx = todo_idx[:limit]

    logger.info("LEI backfill: %d row(s) with ISIN+no-LEI to look up (of %d with ISIN)",
                len(todo_idx), int(have_isin.sum()))

    filled = 0
    for n, i in enumerate(todo_idx, start=1):
        lei, _name = fetch(df.at[i, "ISIN"].strip(), use_cache)
        if lei:
            df.at[i, "LEI"] = lei
            filled += 1
        if n % 100 == 0:
            logger.info("  progress: %d/%d (%d filled)", n, len(todo_idx), filled)
        if _fetcher is None:
            time.sleep(REQUEST_SPACING_SEC)

    df.to_csv(csv_path, index=False)
    result = {
        "total": len(df),
        "with_isin": int(have_isin.sum()),
        "had_lei": int(have_lei.sum()),
        "attempted": len(todo_idx),
        "filled": filled,
        "still_missing": int((df["ISIN"].str.strip() != "").sum()
                             - (df["LEI"].str.strip() != "").sum()),
    }
    return result


def main(use_cache=True, limit=None):
    result = backfill(use_cache=use_cache, limit=limit)
    logger.info(
        "LEI backfill done: filled %d/%d attempted; %d/%d ISIN rows now have an LEI "
        "(%d still missing)",
        result["filled"], result["attempted"],
        result["with_isin"] - result["still_missing"], result["with_isin"],
        result["still_missing"],
    )
    return result
