"""DEPRECATED back-compat shim — use `universe.positions` instead.

This module previously owned `data/watchlist.csv` (5 cols: Ticker, Buy Price,
Target Price, Date Added, Notes). On 2026-05-03 the source file was renamed
and split into `data/positions_and_researching.csv` with a richer 9-column
schema and an explicit `Position` column.

This shim keeps **read-only** legacy callers working for one cycle. It loads
the new file via `universe.positions` and returns watchlist-shape dicts:
    - `Buy Price` carried over verbatim
    - `Sell Price` → `Target Price` (semantic mapping; the old "Target Price"
      was effectively the sell target for held names)
    - `Position Date` → `Date Added`
    - `Notes` carried over
    - New columns (`Position`, `First Buy Date`, `Average Cost`, `Shares`)
      are dropped from the legacy shape

Mutating helpers (`add`, `remove`, `save`, `_append_universe_row`) are removed.
Use `universe.positions` and `cli.py positions` for those.

This module will be deleted in a follow-up cleanup once `weekly_universe`,
`reporting/sigma_export`, and `reporting/watchlist_report` have all been
migrated to import `universe.positions` directly.
"""

from pathlib import Path

from logging_utils import get_logger
from universe import positions

logger = get_logger("universe.watchlist")

# Path that read-only callers may inspect. Points at the new source file.
WATCHLIST_PATH = positions.POSITIONS_PATH
WATCHLIST_COLUMNS = ["Ticker", "Buy Price", "Target Price", "Date Added", "Notes"]


# Re-export for callers that catch this exception type.
WatchlistError = positions.PositionsError


def _to_watchlist_shape(entry):
    """Project a positions-shape dict to the legacy watchlist shape."""
    return {
        "Ticker": entry["Ticker"],
        "Buy Price": entry.get("Buy Price"),
        "Target Price": entry.get("Sell Price"),  # semantic mapping
        "Date Added": entry.get("Position Date", ""),
        "Notes": entry.get("Notes", ""),
    }


def load(path=None):
    """Load positions and return them as legacy watchlist-shape dicts.

    The `path` argument is accepted for back-compat but ignored — the shim
    always reads from `positions.POSITIONS_PATH`. Callers that pass a path
    are likely doing test fixtures; those should migrate to using
    `universe.positions.load(path)` directly.
    """
    if path is not None and Path(path) != positions.POSITIONS_PATH:
        logger.warning(
            "watchlist.load(path=%s) ignored — shim reads from %s",
            path, positions.POSITIONS_PATH,
        )
    raw = positions.load(positions.POSITIONS_PATH)
    return [_to_watchlist_shape(e) for e in raw]


def validate(entries, universe_csv_path=None):
    """Read-only validation of legacy watchlist-shape entries.

    For tests that build synthetic legacy-shape entries, we can't round-trip
    through the positions module without a Position field. We therefore
    validate only the universe-membership invariant here, which is the
    minimal check the legacy callers actually need.
    """
    from config import CSV_PATH
    universe_csv_path = universe_csv_path or CSV_PATH
    universe_rows = positions._load_universe_rows(universe_csv_path)
    errors = []
    warnings = []
    seen = set()
    for e in entries:
        t = e["Ticker"]
        if t in seen:
            errors.append(f"duplicate ticker: {t}")
            continue
        seen.add(t)
        if t not in universe_rows:
            errors.append(
                f"{t} is not in the coverage universe — add it via discovery first"
            )
        else:
            row = universe_rows[t]
            missing = [
                f for f in positions.REQUIRED_METADATA_FIELDS
                if not (row.get(f) or "").strip()
            ]
            if missing:
                errors.append(
                    f"{t}: missing universe metadata for {', '.join(missing)} — "
                    f"fix the universe CSV row before using this ticker"
                )
        buy = e.get("Buy Price")
        tgt = e.get("Target Price")
        if buy is not None and tgt is not None and tgt <= buy:
            warnings.append(
                f"{t}: target price ({tgt}) is not above buy price ({buy})"
            )
    return errors, warnings


def _load_universe_rows(universe_csv_path=None):
    """Pass-through to positions._load_universe_rows for legacy callers."""
    from config import CSV_PATH
    return positions._load_universe_rows(universe_csv_path or CSV_PATH)


def _load_universe_tickers(universe_csv_path=None):
    """Pass-through for legacy callers."""
    from config import CSV_PATH
    return positions._load_universe_tickers(universe_csv_path or CSV_PATH)
