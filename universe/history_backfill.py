"""Resumable full-universe backfill of the FMP historical-valuation cache.

Why this exists
---------------
The Phase 1 historical-valuation columns (`P/E 5Y …`, `EV/S 5Y …`) were scoped to
the ~77 names carrying a personal trading state, because `reporting/generate.py`
fetched them inline during the weekly performance run and a full-universe fetch
inside that run was not something we wanted to pay for. The consequence was that
the chart pack's "valuation vs history" views were thin and skewed to the handful
of Tech/Consumer/Industrials names that happened to be in the positions file —
the ~1,000-name healthcare universe was absent entirely.

This module decouples *fetching* from *reporting*:

- **This backfill** populates `cache/key_metrics_history/` for the whole universe.
  It is safe to run weekly (JP: "it's fine if the cache is only updated 1x a week").
- **The report** reads that cache (`cache_only=True` for non-position names) and
  therefore adds zero API calls and zero runtime for the widened coverage.

Call budget
-----------
3 FMP calls per ticker (annual ratios + annual key-metrics + ratios-ttm) — the
same 3 calls that already served the 5Y columns. The **10-year window is free**:
FMP Starter returns 15 annual rows, so the wider window is the same request with
a higher `limit`, and the 5Y stats are the first 5 elements of that same series.

Full cold universe ≈ 1,095 × 3 = ~3,285 calls. The FMP client rate-limits itself
to 300 calls/min, so a full cold pass is ~11 minutes of wall clock. Warm passes
are far cheaper — annual fundamentals are cached 30 days, so a weekly run only
chases the ~1/4 of names whose cache aged out plus anything new.

Resumability
------------
The per-ticker cache file **is** the resume state — there is no separate cursor
to corrupt. Every run skips any ticker that already has a fresh, current-schema
cache entry, so a pass that dies at name 600 does not restart from zero; the next
pass simply finds 600 fewer names to do. `--limit N` bounds a run deliberately
(the intended way to test or to spread a cold backfill over several days).

No silent failures
------------------
Three outcomes are recorded distinctly, never collapsed into a blank or a zero:
  ok            → data present
  no_data       → FMP answered and has nothing for this ticker (cached 7d, so it
                  is a recorded fact rather than an untried name)
  error         → the call failed (NOT cached — retried next run)
plus `not_attempted` for anything this backfill has never reached. A run summary
is written to `cache/key_metrics_history/_backfill_state.json` and the error /
no-data ticker lists are logged, so a degrading provider is visible rather than
quietly producing empty columns.

CLI: `python cli.py history-backfill [--limit N] [--tickers A,B,C] [--refresh]`
"""

import json
import time
from datetime import datetime, timezone

from config import CACHE_DIR, API_KEYS, CSV_PATH
from logging_utils import get_logger
from providers.fmp_history import (
    HISTORY_CACHE_NAMESPACE,
    STATUS_OK,
    STATUS_NO_DATA,
    STATUS_ERROR,
    fetch_history_parallel,
    is_cached,
)
from ticker_utils import read_universe_csv

logger = get_logger("history_backfill")

STATE_PATH = CACHE_DIR / HISTORY_CACHE_NAMESPACE / "_backfill_state.json"

# 3 FMP calls per ticker: annual ratios + annual key-metrics + ratios-ttm.
CALLS_PER_TICKER = 3


def load_universe_tickers():
    """Return the sorted list of unique, non-blank tickers in the coverage universe."""
    df = read_universe_csv(CSV_PATH)
    raw = df["Ticker"].fillna("").astype(str).str.strip()
    seen = []
    seen_set = set()
    for t in raw:
        if not t or t == "#N/A" or t in seen_set:
            continue
        seen_set.add(t)
        seen.append(t)
    return sorted(seen)


def select_pending(tickers, use_cache=True):
    """Filter `tickers` down to those still needing a fetch.

    This is the resume step: anything with a fresh, current-schema cache entry is
    already done and is skipped.
    """
    if not use_cache:
        return list(tickers)
    return [t for t in tickers if not is_cached(t)]


def write_state(summary):
    """Persist the run summary so a partial/failed backfill is visible after the fact."""
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write backfill state to %s: %s", STATE_PATH, e)


def main(limit=None, tickers=None, use_cache=True, max_workers=10):
    """Run the backfill. Returns the summary dict (also written to STATE_PATH).

    Args:
        limit: only fetch the first N pending tickers (bounds a run).
        tickers: explicit list/CSV-string of tickers instead of the full universe.
        use_cache: False forces a refetch of everything in scope.
        max_workers: parallel fetch width. The FMP client rate-limits globally at
            300 calls/min regardless, so this only controls concurrency.
    """
    api_key = API_KEYS.get("FMP_API_KEY")
    if not api_key:
        # Loud, not silent: without a key this would otherwise produce a
        # full universe of blank columns that look like real "no data".
        raise RuntimeError(
            "FMP_API_KEY is not set — refusing to run the history backfill. "
            "Every ticker would be recorded as unavailable."
        )

    if isinstance(tickers, str):
        tickers = [t.strip() for t in tickers.split(",") if t.strip()]

    universe = list(tickers) if tickers else load_universe_tickers()
    pending = select_pending(universe, use_cache=use_cache)
    already_done = len(universe) - len(pending)

    if limit is not None and limit >= 0:
        pending = pending[:limit]

    est_calls = len(pending) * CALLS_PER_TICKER
    logger.info(
        "History backfill: %s in scope, %s already cached (resumed), %s to fetch "
        "(~%s FMP calls, ~%.1f min at 300/min)",
        len(universe), already_done, len(pending), est_calls, est_calls / 300.0,
    )

    t0 = time.monotonic()
    results = {}
    if pending:
        results = fetch_history_parallel(
            pending, api_key, max_workers=max_workers, use_cache=use_cache,
        )
    elapsed = time.monotonic() - t0

    ok = sorted(t for t, h in results.items() if h.get("status") == STATUS_OK)
    no_data = sorted(t for t, h in results.items() if h.get("status") == STATUS_NO_DATA)
    errored = sorted(t for t, h in results.items() if h.get("status") == STATUS_ERROR)

    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(universe),
        "already_cached_at_start": already_done,
        "attempted": len(pending),
        "ok": len(ok),
        "no_data": len(no_data),
        "error": len(errored),
        "estimated_fmp_calls": est_calls,
        "elapsed_sec": round(elapsed, 1),
        "no_data_tickers": no_data,
        "error_tickers": errored,
        "remaining_after_run": max(0, len(universe) - already_done - len(ok) - len(no_data)),
    }
    write_state(summary)

    logger.info(
        "History backfill done in %.1fs — ok=%s no_data=%s error=%s; %s ticker(s) still pending",
        elapsed, len(ok), len(no_data), len(errored), summary["remaining_after_run"],
    )
    if no_data:
        logger.info("No FMP history available for: %s", ", ".join(no_data[:40]))
    if errored:
        # Errors are transient by definition (never cached) — surface them loudly
        # so a systematically failing provider isn't mistaken for missing data.
        logger.warning(
            "History fetch ERRORED for %s ticker(s) (will retry next run): %s",
            len(errored), ", ".join(errored[:40]),
        )

    return summary
