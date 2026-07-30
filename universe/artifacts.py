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
    """Strip exchange suffixes (e.g. 'ROG SW' -> 'ROG', 'FRE.DE' -> 'FRE').

    RETAINED FOR THE CASE-COLLISION CHECK ONLY. As of schema v4 the published
    metadata is keyed by the RAW ticker — see `build_universe_metadata_with_stats`
    for why. Do not reintroduce this into the key path.
    """
    ticker = (raw or "").strip().upper()
    if not ticker or ticker == "#N/A":
        return None
    plain = ticker.split()[0] if " " in ticker else ticker
    plain = plain.split(".")[0] if "." in plain else plain
    return plain or None


def _publish_key(raw):
    """The published metadata key: the ticker EXACTLY as the universe CSV holds it.

    Schema v4 (2026-07-30). Previously the key was suffix-stripped, and that
    silently destroyed data and broke joins:

      - **It lost a company.** `ROG` (Rogers Corporation, `Core=Y`) and `ROG.SW`
        (Roche) both normalized to `ROG`, later-row-wins, so the published
        metadata said `ROG` was Roche and Rogers Corporation had no entry at all.
        The exporter had been reporting `normalization_collisions: 1` on every
        run for months.
      - **It broke the obvious join for 183 of 1,096 rows.** `exports/universe.csv`
        carries `Ticker = DIA.MI`, but the metadata key was `DIA`, so any consumer
        doing `metadata[row["Ticker"]]` missed every suffixed row. `transcripts`
        iterates these keys AS tickers (`load_all_universe`) and `focus_today`
        keys its own map by them while positions use the raw ticker — both were
        being handed a symbol that is not the one the universe uses.

    The raw ticker is already unique (`validate_no_duplicate_tickers` is an ERROR-
    level check), so keying by it makes collisions structurally impossible rather
    than merely counted.

    The one consumer that RELIED on stripping is `sigma-alert`, which built
    `to_metadata_key()` / `foreign_collision_bases()` to compensate; it is updated
    in the same release.
    """
    ticker = (raw or "").strip()
    if not ticker or ticker.upper() == "#N/A":
        return None
    return ticker


def build_universe_metadata(csv_path):
    """Read the coverage CSV and return a `{TICKER: {name, sector, subsector, sub_subsector}}` dict.

    This is the **generic** builder: no ETFs, no consumer-specific augmentation.
    Keyed by the RAW ticker as of schema v4, so the map is exactly 1:1 with the
    CSV's rows and `metadata[row["Ticker"]]` is the correct join. See
    `_publish_key` for what changed and why.

    Args:
        csv_path: Path to a coverage universe CSV (must have columns
            'Ticker', 'Company Name', 'Sector (JP)', 'Subsector (JP)',
            'Sub-subsector (JP)', 'Core').

    Returns:
        Dict keyed by the RAW ticker (``DIA.MI`` stays ``DIA.MI``). Each value
        has fields: name, sector, subsector, sub_subsector, core. The `core`
        field is the raw value of the `Core` column ("Y" for analytically-
        covered names, blank otherwise).
    """
    metadata, _ = build_universe_metadata_with_stats(csv_path)
    return metadata


def build_universe_metadata_with_stats(csv_path):
    """Like `build_universe_metadata` but also returns a stats dict.

    The stats dict has:
      - rows_seen: total CSV rows processed (including skipped/blank)
      - rows_kept: rows that produced a metadata entry
      - normalization_collisions: rows whose published key collided with an
        earlier row's. Always 0 under schema v4 (raw tickers are unique); the
        field is kept so the status file's shape is unchanged, and a non-zero
        value now means a DUPLICATE ROW reached the exporter.
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
            plain = _publish_key(row.get("Ticker", ""))
            if not plain:
                continue
            if plain in metadata:
                # Structurally impossible now (raw tickers are unique, enforced by
                # validate_no_duplicate_tickers at ERROR level), but counted rather
                # than assumed: if it ever fires, a duplicate row reached the
                # exporter and one company is being silently dropped again.
                collisions += 1
                if len(collision_examples) < 10:
                    collision_examples.append(plain)
            metadata[plain] = {
                "name": row.get("Company Name", "").strip(),
                "sector": row.get("Sector (JP)", "").strip(),
                "subsector": row.get("Subsector (JP)", "").strip(),
                "sub_subsector": row.get("Sub-subsector (JP)", "").strip(),
                "core": row.get("Core", "").strip(),
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
