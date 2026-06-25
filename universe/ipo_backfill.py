"""Backfill verified IPO offer dates from Renaissance Capital, keyed by CIK/ticker.

Adds three **immutable** columns just after ``Year Listed``:
  - ``IPO Date``        — the verified offer date, ISO ``YYYY-MM-DD``
  - ``Est Lockup 90d``  — offer date + 90 days (the API has no lockup field)
  - ``Est Lockup 180d`` — offer date + 180 days

These are the raw facts; the date-relative routing signal (``ipo_age_days`` /
``ipo_age_bucket``) is computed on read by ``providers.renaissance_ipo.ipo_age``,
never stored (it would go stale between weekly runs).

Source: Renaissance Capital's FREE endpoint — a metered single-company verifier
(120 calls/MONTH, see ``providers/renaissance_ipo.py``). Only rows with a blank
``IPO Date`` are looked up; results (including authoritative "no IPO on record"
answers) are cached forever, so reruns are nearly free and only chase still-blank
rows. The backfill stops cleanly when the monthly budget is exhausted.

Scope/use: this is a *supplement* to CM's existing identity plumbing — verify the
offer date for SMID HC names where yfinance/FMP report the first-trade or
listing-transfer date rather than the offer. Prefer running it on a small batch
(``--limit``) or after a name is freshly discovered, not as a market-wide sweep.

Non-gating: writes the CSV, never raises on a single lookup failure. Degrades
loudly (logs a warning) if ``RENAISSANCE_API_KEY`` is unset.

CLI: ``python cli.py ipo-backfill [--no-cache] [--limit N]``
"""

import time

from config import API_KEYS, CSV_PATH
from logging_utils import get_logger
from providers import renaissance_ipo
from ticker_utils import read_universe_csv

logger = get_logger("ipo_backfill")

IPO_DATE_COL = "IPO Date"
LOCKUP_90_COL = "Est Lockup 90d"
LOCKUP_180_COL = "Est Lockup 180d"
_NEW_COLS = [IPO_DATE_COL, LOCKUP_90_COL, LOCKUP_180_COL]


def _ensure_ipo_columns(df):
    """Add the IPO columns (just after 'Year Listed') if missing. Returns the df."""
    if IPO_DATE_COL in df.columns:
        return df
    anchor = "Year Listed"
    idx = (df.columns.get_loc(anchor) + 1) if anchor in df.columns else len(df.columns)
    for offset, col in enumerate(_NEW_COLS):
        df.insert(idx + offset, col, "")
    return df


def _default_fetcher(api_key, use_cache):
    """Build a (ticker, cik) -> result-dict|None fetcher bound to the API key."""
    def _fetch(ticker, cik):
        return renaissance_ipo.fetch_ipo_date(ticker, api_key, cik=cik, use_cache=use_cache)
    return _fetch


def _year_sort_key(year_str):
    """Year Listed -> int for most-recent-first ordering; blanks/garbage sort last."""
    try:
        return int(str(year_str).strip()[:4])
    except (ValueError, TypeError):
        return -1


def backfill(csv_path=None, use_cache=True, limit=None, us_only=True,
             min_year=None, _fetcher=None):
    """Fill IPO Date + lockup columns for rows with a blank IPO Date.

    The free source covers **US IPOs only** and the API's reliable key is the CIK,
    so by default (`us_only=True`) only rows WITH a CIK are attempted — foreign
    names without a CIK would always 404 and waste the tiny monthly quota. Rows are
    processed **most-recently-listed first** (by `Year Listed`) so a limited budget
    hits the highest-value recent IPOs before old mega-caps. `min_year` skips rows
    listed before a given year (e.g. 2024 for "last ~2 years").

    ``_fetcher``: injectable ``(ticker, cik) -> {"offer_date": iso, ...}|None`` for
    tests (bypasses HTTP, the budget guard, and the inter-call sleep).

    Returns dict: {total, missing_before, candidates, attempted, filled, no_data,
    still_missing, budget_exhausted, calls_this_month, monthly_cap}.
    """
    csv_path = csv_path or CSV_PATH

    if _fetcher is None:
        api_key = API_KEYS.get("RENAISSANCE_API_KEY")
        if not api_key:
            logger.warning(
                "RENAISSANCE_API_KEY not set — IPO backfill is a no-op. "
                "Add it to .env (see AUTHENTICATIONS.md)."
            )
            return {
                "total": 0, "missing_before": 0, "candidates": 0, "attempted": 0,
                "filled": 0, "no_data": 0, "still_missing": 0, "budget_exhausted": False,
                "calls_this_month": renaissance_ipo.calls_this_month(),
                "monthly_cap": renaissance_ipo.MONTHLY_CALL_CAP,
            }
        fetch = _default_fetcher(api_key, use_cache)
    else:
        fetch = _fetcher

    df = read_universe_csv(csv_path)
    df = _ensure_ipo_columns(df)

    missing = df[IPO_DATE_COL].str.strip() == ""
    eligible = missing.copy()
    if us_only and "CIK" in df.columns:
        eligible &= df["CIK"].str.strip() != ""      # US filers only (foreign always 404)
    if min_year is not None and "Year Listed" in df.columns:
        eligible &= df["Year Listed"].map(_year_sort_key) >= int(min_year)

    # Most-recently-listed first so a limited monthly budget hits recent IPOs first.
    candidate_idx = df.index[eligible].tolist()
    if "Year Listed" in df.columns:
        candidate_idx.sort(key=lambda i: _year_sort_key(df.at[i, "Year Listed"]), reverse=True)
    todo_idx = candidate_idx[:limit] if limit is not None else candidate_idx

    logger.info(
        "IPO backfill: %d blank-IPO-Date row(s); %d eligible (us_only=%s, min_year=%s); "
        "looking up %d.",
        int(missing.sum()), len(candidate_idx), us_only, min_year, len(todo_idx),
    )

    filled = no_data = attempted = 0
    budget_exhausted = False
    for n, i in enumerate(todo_idx, start=1):
        ticker = (df.at[i, "Ticker"] or "").strip()
        cik = (df.at[i, "CIK"] or "").strip() if "CIK" in df.columns else ""
        try:
            res = fetch(ticker, cik or None)
        except renaissance_ipo.RenaissanceBudgetError as e:
            logger.warning("Stopping IPO backfill: %s", e)
            budget_exhausted = True
            break
        attempted += 1
        if res and res.get("offer_date"):
            offer = res["offer_date"]
            d90, d180 = renaissance_ipo.lockup_dates(offer)
            df.at[i, IPO_DATE_COL] = offer
            df.at[i, LOCKUP_90_COL] = d90
            df.at[i, LOCKUP_180_COL] = d180
            filled += 1
        else:
            no_data += 1
        if n % 25 == 0:
            logger.info("  progress: %d/%d (%d filled)", n, len(todo_idx), filled)
        if _fetcher is None:
            time.sleep(renaissance_ipo.REQUEST_SPACING_SEC)

    df.to_csv(csv_path, index=False)
    still_missing = int((df[IPO_DATE_COL].str.strip() == "").sum())
    return {
        "total": len(df),
        "missing_before": int(missing.sum()),
        "candidates": len(candidate_idx),
        "attempted": attempted,
        "filled": filled,
        "no_data": no_data,
        "still_missing": still_missing,
        "budget_exhausted": budget_exhausted,
        "calls_this_month": renaissance_ipo.calls_this_month(),
        "monthly_cap": renaissance_ipo.MONTHLY_CALL_CAP,
    }


def main(use_cache=True, limit=None, us_only=True, min_year=None):
    result = backfill(use_cache=use_cache, limit=limit, us_only=us_only, min_year=min_year)
    logger.info(
        "IPO backfill done: filled %d (no IPO on record: %d) of %d attempted; "
        "%d row(s) still blank. Calls this month: %d/%d.%s",
        result["filled"], result["no_data"], result["attempted"],
        result["still_missing"], result["calls_this_month"], result["monthly_cap"],
        " BUDGET EXHAUSTED — rerun next month to continue." if result["budget_exhausted"] else "",
    )
    return result
