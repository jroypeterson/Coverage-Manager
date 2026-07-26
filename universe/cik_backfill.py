"""Backfill blank CIKs from SEC EDGAR's bulk ticker map, every weekly run.

**Why this exists.** A CIK is not a static fact about a company — it is a fact
about whether that company has *registered with the SEC yet*. `enrich.py`
resolves CIKs when a row is first enriched, and nothing re-checked the blanks
afterwards. So a name that registers LATER keeps a blank CIK forever, and every
downstream lane keyed on CIK silently skips it.

That is not hypothetical. On 2026-07-25 an independent insider cross-check
found SpaceX (SPCX) filing 40 Form 3s from its entire board — it had registered,
was held in the portfolio, and `insider_ownership` had been skipping it because
its CIK was blank. Re-probing the blanks found **16** such rows, including
Cerebras, Fervo Energy, Quantinuum, HawkEye 360, Lumexa (the subject of an open
earnings-agent alert) and GMR Solutions and Mobia Medical (two of sigma-alert's
chronic skips).

**Why it is cheap.** SEC's `company_tickers.json` is one free unauthenticated
download covering every registrant, and only blank rows are looked up in it —
there is no per-ticker request. The step therefore costs one HTTP GET per weekly
run regardless of universe size.

**Why it only fills blanks.** An existing CIK is never overwritten. A CIK is
stable across a ticker change (that invariant is what `ticker_change_check`
relies on), so a disagreement between a populated CIK and the map means the
TICKER moved, not the CIK — which is that module's job to surface, not this
one's to silently "fix".

CLI: `python cli.py backfill-cik [--dry-run]`
"""
from __future__ import annotations

import json

import pandas as pd
import requests

from config import CSV_PATH
from logging_utils import get_logger, log_exception

logger = get_logger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
#: SEC requires a real contact in the User-Agent or it serves 403/garbage.
SEC_USER_AGENT = "Coverage Manager (jroypeterson@gmail.com)"


def fetch_sec_cik_map() -> dict[str, str]:
    """`{TICKER: cik}` from SEC's bulk file. Empty dict on any failure."""
    try:
        resp = requests.get(SEC_TICKERS_URL,
                            headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
        if resp.status_code != 200:
            logger.warning("SEC company_tickers.json HTTP %s", resp.status_code)
            return {}
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        log_exception(logger, "SEC company_tickers.json fetch failed", e)
        return {}
    out: dict[str, str] = {}
    for entry in data.values():
        t = str(entry.get("ticker", "")).strip().upper()
        cik = entry.get("cik_str")
        if t and cik:
            out[t] = str(cik)
    logger.info("SEC EDGAR: %s ticker->CIK mappings", len(out))
    return out


def main(dry_run: bool = False) -> dict:
    df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    if "CIK" not in df.columns or "Ticker" not in df.columns:
        logger.warning("CIK backfill: CSV lacks Ticker/CIK columns; skipping")
        return {"filled": 0, "still_blank": 0, "fetched_ok": False}

    cik_map = fetch_sec_cik_map()
    if not cik_map:
        # Fail loud, change nothing. A blank map must never be read as
        # "no ticker resolves", which would look like a clean run.
        logger.warning("CIK backfill: SEC map unavailable - no rows changed")
        return {"filled": 0, "still_blank": int((df["CIK"].str.strip() == "").sum()),
                "fetched_ok": False}

    blank = df["CIK"].str.strip() == ""
    filled: list[tuple[str, str, str]] = []
    for idx in df.index[blank]:
        t = str(df.at[idx, "Ticker"]).strip().upper()
        cik = cik_map.get(t)
        if cik:
            df.at[idx, "CIK"] = cik
            filled.append((t, cik, str(df.at[idx, "Company Name"])[:40]))

    if filled and not dry_run:
        df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

    still_blank = int((df["CIK"].str.strip() == "").sum())
    verb = "would fill" if dry_run else "filled"
    logger.info("CIK backfill: %s %s blank CIK(s); %s still blank "
                "(expected - non-US registrants)", verb, len(filled), still_blank)
    for t, cik, name in filled:
        logger.warning("  NEW CIK %s -> %s (%s) - now visible to CIK-keyed lanes",
                       t, cik, name)
    return {"filled": len(filled), "still_blank": still_blank,
            "fetched_ok": True, "rows": filled}
