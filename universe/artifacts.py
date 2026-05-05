"""Generic universe artifact builders.

This module owns the canonical, consumer-agnostic transformation from the
coverage universe CSV into the structured artifacts published under
`exports/`. Anything that injects consumer-specific tickers (e.g. sigma-alert
sector ETFs) does NOT belong here — it belongs in the consumer's own export
module (see `reporting/sigma_export.py`) or in the consumer project itself.

The contract: `build_universe_metadata(csv_path)` returns a dict whose keys
are exactly the tickers found in the CSV, with no additions, no removals,
and no consumer-specific augmentation.
"""

import csv

from logging_utils import get_logger

logger = get_logger("universe.artifacts")


def _normalize_ticker(raw):
    """Strip exchange suffixes (e.g. 'ROG SW' -> 'ROG', 'FRE.DE' -> 'FRE')."""
    ticker = (raw or "").strip().upper()
    if not ticker or ticker == "#N/A":
        return None
    plain = ticker.split()[0] if " " in ticker else ticker
    plain = plain.split(".")[0] if "." in plain else plain
    return plain or None


def build_universe_metadata(csv_path):
    """Read the coverage CSV and return a `{TICKER: {name, sector, subsector, sub_subsector}}` dict.

    This is the **generic** builder: no ETFs, no consumer-specific augmentation.
    Every key in the returned dict corresponds to one or more rows in the source
    CSV; multiple rows can collapse to a single key when their tickers normalize
    to the same root (e.g. ``ROG SW`` and ``ROG.DE`` both become ``ROG``).
    Use `build_universe_metadata_with_stats` if you need the collision count.

    Args:
        csv_path: Path to a coverage universe CSV (must have columns
            'Ticker', 'Company Name', 'Sector (JP)', 'Subsector (JP)').

    Returns:
        Dict keyed by normalized ticker (exchange suffix stripped).
    """
    metadata, _ = build_universe_metadata_with_stats(csv_path)
    return metadata


def build_universe_metadata_with_stats(csv_path):
    """Like `build_universe_metadata` but also returns a stats dict.

    The stats dict has:
      - rows_seen: total CSV rows processed (including skipped/blank)
      - rows_kept: rows that produced a metadata entry
      - normalization_collisions: rows whose normalized ticker collided with
        an earlier row's (the later row wins, the earlier is overwritten)
      - collision_examples: up to 10 sample collided ticker keys for debugging
    """
    metadata = {}
    rows_seen = 0
    rows_kept = 0
    collisions = 0
    collision_examples = []

    # utf-8-sig tolerates an accidental BOM on the source CSV; without it,
    # a BOM would prefix the first header (﻿Ticker) and silently
    # produce empty metadata for every row.
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows_seen += 1
            plain = _normalize_ticker(row.get("Ticker", ""))
            if not plain:
                continue
            if plain in metadata:
                collisions += 1
                if len(collision_examples) < 10:
                    collision_examples.append(plain)
            metadata[plain] = {
                "name": row.get("Company Name", "").strip(),
                "sector": row.get("Sector (JP)", "").strip(),
                "subsector": row.get("Subsector (JP)", "").strip(),
                "sub_subsector": row.get("Sub-subsector (JP)", "").strip(),
            }
            rows_kept += 1

    if collisions:
        logger.warning(
            "build_universe_metadata: %d ticker normalization collision(s); examples: %s",
            collisions,
            collision_examples,
        )

    stats = {
        "rows_seen": rows_seen,
        "rows_kept": rows_kept,
        "normalization_collisions": collisions,
        "collision_examples": collision_examples,
    }
    return metadata, stats
