"""Universe-side weekly orchestrator.

Owns the universe management half of the weekly pipeline:
validate -> archive -> discovery -> export-artifacts -> sigma-export.

Produces a versioned, published artifact contract under `exports/` that other
projects in this workspace consume (forensic_triage, biotech_triage,
screens_equity/quantitative_screens, 13F analyzer). See `exports/manifest.json` and
`exports/universe_status.json` for the contract.

Returns a standardized result dict; see `_make_result` for the shape.
"""

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config import CSV_PATH, DATA_DIR, OLD_REPORTS_DIR, REPORTS_DIR, SCRIPT_DIR, TODAY
from logging_utils import get_logger
from pipeline_utils import collect_non_successes, run_step

logger = get_logger("weekly_universe")

EXPORTS_DIR = SCRIPT_DIR / "exports"
EXPORTS_SCHEMA_VERSION = 4
# The reporting-calendar export versions independently of the universe/positions
# schemas (decoupled so calendar changes never force a bump on pinned consumers).
REPORTING_CALENDAR_SCHEMA_VERSION = 1
# Same reasoning for the alias map: it is re-exported from universe/aliases.py so
# there is one definition of the version, and a consumer pinning it is unaffected
# by a universe schema bump.
from universe.aliases import SCHEMA_VERSION as ALIASES_SCHEMA_VERSION  # noqa: E402

UNIVERSE_ARCHIVE_PATTERNS = [
    "weekly_coverage_universe_additions_*.md",
    "company_backgrounds_*.md",
    "delisted_check_*.md",
    "delisted_check_*.csv",
    "ticker_change_check_*.md",
    "ticker_change_check_*.csv",
    "cik_name_resolution_*.md",
    "cik_name_resolution_*.csv",
]


# ── Steps ────────────────────────────────────────────────────────────────────


def _step_validate():
    """Run CSV validation. Returns a dict with rows/errors/warnings/passed."""
    import pandas as pd

    from universe import validation

    df = pd.read_csv(CSV_PATH)
    errors, warnings = validation.run_all_validations(df)

    for w in warnings:
        logger.info("  WARN: %s", w)
    for e in errors:
        logger.warning("  ERROR: %s", e)

    return {
        "rows": len(df),
        "errors": errors,
        "warnings": warnings,
        "passed": len(errors) == 0,
    }


def _step_archive_universe():
    """Archive prior dated universe-side outputs (discovery md files)."""
    from reporting.email import archive_files

    # 90 days (JP 2026-07-28), up from the shared 60-day default. These are the
    # analytical outputs - weekly recommendations + company backgrounds - not
    # regenerable renders, and `reports/` is gitignored, so a delete here is
    # permanent. 90 covers a full quarter of recommendations; JP explicitly did not
    # want indefinite retention. Pruning is scoped to these patterns only, so this
    # can no longer reach another caller's artifacts (see archive_files' docstring).
    return archive_files(REPORTS_DIR, OLD_REPORTS_DIR, TODAY,
                         UNIVERSE_ARCHIVE_PATTERNS, prune_days=90)


def _step_discovery(dry_run=False):
    """Run the discovery candidate pipeline. Mirrors the original logic from weekly_build."""
    from discovery.candidates import (
        commit_staged_candidates,
        stage_candidates,
        validate_discovery_output,
        write_discovery_input,
    )

    input_path = write_discovery_input()
    logger.info("  Discovery input written to %s", input_path)

    output_path = DATA_DIR / f"discovery_output_{TODAY}.json"
    if not output_path.exists():
        logger.info("  No discovery output found at %s", output_path)
        logger.info("  Run the weekly coverage prompt in Claude, save output as:")
        logger.info("    %s", output_path)
        return {"status": "awaiting output", "input_written": str(input_path)}

    valid, errors = validate_discovery_output(output_path)
    for e in errors:
        logger.warning("  Validation: %s", e)
    logger.info("  %d valid candidates, %d validation errors", len(valid), len(errors))

    if not valid:
        return {"status": "no valid candidates", "errors": len(errors)}

    staging_path = stage_candidates(valid)
    logger.info("  Staged to %s", staging_path)
    logger.info("  Review the staging file, set approved=true for candidates to add")

    if not dry_run:
        pre_approved = [c for c in valid if c.get("approved")]
        if pre_approved:
            commit_path = DATA_DIR / f"approved_candidates_{TODAY}.csv"
            stage_candidates(pre_approved, commit_path)
            added = commit_staged_candidates(commit_path)
            logger.info("  Committed %d pre-approved candidates", added)
            return {"status": "committed", "added": added, "total_valid": len(valid)}

    return {"status": "staged", "valid": len(valid), "staging_path": str(staging_path)}


def _find_last_discovery_run():
    """Return the date string of the most recent discovery_output_*.json file, or None."""
    candidates = sorted(DATA_DIR.glob("discovery_output_*.json"))
    if not candidates:
        return None
    # Filename pattern: discovery_output_YYYY-MM-DD.json
    name = candidates[-1].stem  # discovery_output_YYYY-MM-DD
    return name.replace("discovery_output_", "") or None


def _step_export_artifacts(validation_result):
    """Write the published universe artifacts to the `exports/` directory.

    Produces five files described in `exports/manifest.json`:
      - universe.csv              — snapshot of the coverage universe CSV
      - universe_metadata.json    — {ticker: {name, sector, subsector}} dict
      - universe_status.json      — versioned status + validation contract
      - ticker_aliases.json       — symbol splits + per-vendor symbols
      - manifest.json             — directory of files in this exports/ folder

    `validation_result` is the dict returned by `_step_validate` and feeds the
    status file's validation_passed / errors / warnings fields.
    """
    from universe.artifacts import build_universe_metadata_with_stats

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Snapshot the CSV — transcoded, NOT raw-copied.
    #    The source carries a UTF-8 BOM (ticker_utils writes it with utf-8-sig). A raw copy
    #    propagated that BOM into the published artifact, and every consumer that opens it
    #    as plain utf-8 — earnings_agent, post_earnings_movers, analyst-days — then read
    #    "﻿Ticker" as the first field and recovered ZERO of 1,086 tickers while
    #    reporting success. Exports are the contract; they are published BOM-free.
    universe_csv_path = EXPORTS_DIR / "universe.csv"
    universe_csv_path.write_text(
        CSV_PATH.read_text(encoding="utf-8-sig") if hasattr(CSV_PATH, "read_text")
        else Path(CSV_PATH).read_text(encoding="utf-8-sig"),
        encoding="utf-8", newline="")

    # 2. Build the structured metadata dict (ticker -> {name, sector, subsector}).
    #    Generic builder only — no consumer-specific augmentation. Sigma-alert
    #    ETF injection lives in `reporting/sigma_export.build_sigma_metadata`.
    metadata, build_stats = build_universe_metadata_with_stats(CSV_PATH)
    metadata_path = EXPORTS_DIR / "universe_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    # 3. Status / contract file.
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        source_path = str(Path(CSV_PATH).relative_to(SCRIPT_DIR)).replace("\\", "/")
    except ValueError:
        source_path = str(CSV_PATH).replace("\\", "/")
    status = {
        "schema_version": EXPORTS_SCHEMA_VERSION,
        "dataset_version": TODAY,
        "generated_at": generated_at,
        "source_path": source_path,
        "row_count": validation_result["rows"],
        "ticker_count": len(metadata),
        "normalization_collisions": build_stats["normalization_collisions"],
        "collision_examples": build_stats["collision_examples"],
        "validation_passed": validation_result["passed"],
        "validation_errors": list(validation_result["errors"]),
        "validation_warnings": list(validation_result["warnings"]),
        "last_discovery_run": _find_last_discovery_run(),
    }
    status_path = EXPORTS_DIR / "universe_status.json"
    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    # 3b. Symbol aliases — issuers the fleet's sources spell differently.
    #     Published as its own artifact rather than folded into universe_metadata
    #     because the consumers are DIFFERENT: metadata answers "what sector is
    #     this", aliases answer "what do I call this when I ask yfinance". A
    #     consumer needing one rarely needs the other, and folding them would
    #     have bumped a schema six projects pin.
    from universe.aliases import published_payload as _alias_payload

    aliases_path = EXPORTS_DIR / "ticker_aliases.json"
    # The universe is passed so an entry that CONTRADICTS it is excluded rather
    # than published; see `published_payload`.
    import pandas as _pd

    alias_payload = _alias_payload(
        df=_pd.read_csv(CSV_PATH, dtype=str, encoding="utf-8-sig").fillna(""))

    # ⛑ NEVER OVERWRITE A NON-EMPTY PUBLISHED MAP WITH AN EMPTY ONE.
    #
    # `load_aliases` treats a MISSING source file as a legitimate empty store, and
    # that is right: a fleet with no known symbol splits has no store. But this is
    # a load-with-fallback feeding a write-everything publish, which is the fleet's
    # own documented data-loss shape. `data/ticker_aliases.json` lives in Dropbox;
    # delete it, or catch it mid-sync, and this step would cheerfully republish an
    # empty contract over a working one — silently un-joining every consumer, with
    # a green run to show for it. An empty result is only believable when the
    # published file was already empty.
    # `excluded_count` separates "the curated source vanished" (refuse, keep the
    # good published file) from "every entry was correctly EXCLUDED for
    # contradicting the universe" (publish the empty map — it is the right
    # answer). Without the distinction this guard raised on a correct exclusion
    # and left a STALE export beside a universe.csv, metadata and status file this
    # same function had already rewritten — an internally contradictory artifact
    # set, which is worse than either outcome it was choosing between.
    if (not alias_payload["alias_to_canonical"]
            and not alias_payload.get("excluded_count")
            and aliases_path.exists()):
        try:
            existing = json.loads(aliases_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if isinstance(existing, dict) and existing.get("alias_to_canonical"):
            raise RuntimeError(
                f"refusing to publish an EMPTY ticker_aliases.json over one carrying "
                f"{len(existing['alias_to_canonical'])} alias(es) — the curated source "
                f"data/ticker_aliases.json is missing or empty. Restore it, or delete "
                f"the published file deliberately if the store really is empty now.")

    # Not part of the consumer contract — it describes THIS build, not the
    # mapping — so it is used above and dropped before writing.
    alias_payload.pop("excluded_count", None)
    aliases_path.write_text(json.dumps(alias_payload, indent=2) + "\n", encoding="utf-8")

    # 4. Manifest — describes the contract for downstream consumers.
    manifest = {
        "schema_version": EXPORTS_SCHEMA_VERSION,
        "generated_at": generated_at,
        "description": (
            "Coverage Manager published universe artifacts. Downstream projects "
            "should read these files instead of importing Coverage Manager code "
            "or hitting fundamentals providers directly. Files are committed to "
            "git so consumers get history, reproducibility, and rollback."
        ),
        "files": [
            {
                "name": "universe.csv",
                "purpose": "Canonical coverage universe ticker list (snapshot of data/coverage_universe_tickers.csv)",
                "format": "csv",
            },
            {
                "name": "universe_metadata.json",
                "purpose": (
                    "Generic structured metadata keyed by ticker: "
                    "{name, sector, subsector, sub_subsector}. Contains only "
                    "tickers from the source CSV — no consumer-specific augmentation."
                ),
                "format": "json",
            },
            {
                "name": "universe_status.json",
                "purpose": "Versioned status + validation contract (read schema_version before consuming)",
                "format": "json",
                "schema_version": EXPORTS_SCHEMA_VERSION,
            },
            {
                "name": "ticker_aliases.json",
                "purpose": (
                    "Issuers whose ticker string differs between sources, anchored "
                    "to the identifiers that did NOT change (CIK / ISIN / composite "
                    "FIGI). `alias_to_canonical` maps any known symbol to the "
                    "universe ticker — use it before joining a broker or vendor "
                    "feed to universe.csv. `vendor_symbols` gives the symbol to "
                    "send a named vendor: Fiserv is FISV at yfinance and FI at "
                    "FINRA, so there is no single 'correct' string to rewrite to. "
                    "Read its own schema_version; usually empty."
                ),
                "format": "json",
                "schema_version": ALIASES_SCHEMA_VERSION,
            },
            {
                "name": "positions_and_researching.csv",
                "purpose": (
                    "Positions and researching list joined with universe "
                    "metadata — all coverage universe columns plus Position, "
                    "Position Date, Buy Price, Sell Price, First Buy Date, "
                    "Average Cost, Shares, Notes appended at the end."
                ),
                "format": "csv",
            },
            {
                "name": "portfolio.json",
                "purpose": (
                    "Position == 'Portfolio' rows only (names you own). "
                    "{ticker: {position, position_date, buy_price, sell_price, "
                    "first_buy_date, average_cost, shares, notes, name, "
                    "sector, subsector, sub_subsector, <all universe columns...>}}."
                ),
                "format": "json",
            },
            {
                "name": "researching.json",
                "purpose": (
                    "Position == 'Researching' rows only (names you're "
                    "building a thesis on). Same shape as portfolio.json."
                ),
                "format": "json",
            },
            {
                "name": "following_for_interest.json",
                "purpose": (
                    "Position == 'Following for Interest' rows only "
                    "(passive earnings/signal tracking; no intent to "
                    "trade). Same shape as portfolio.json."
                ),
                "format": "json",
            },
            {
                "name": "ready_to_buy.json",
                "purpose": (
                    "Position == 'Ready to Buy' rows only (long thesis "
                    "complete; waiting for entry trigger). Same shape as "
                    "portfolio.json."
                ),
                "format": "json",
            },
            {
                "name": "ready_to_short.json",
                "purpose": (
                    "Position == 'Ready to Short' rows only (short thesis "
                    "complete; waiting for entry trigger). Same shape as "
                    "portfolio.json."
                ),
                "format": "json",
            },
            {
                "name": "positions_status.json",
                "purpose": "Versioned status + validation contract for positions (read schema_version first).",
                "format": "json",
                "schema_version": EXPORTS_SCHEMA_VERSION,
            },
            {
                "name": "watchlist.csv",
                "purpose": (
                    "DEPRECATED back-compat (one cycle): legacy watchlist "
                    "shape derived from positions_and_researching.csv. "
                    "Sell Price is mapped to Target Price. Use "
                    "positions_and_researching.csv for new code."
                ),
                "format": "csv",
            },
            {
                "name": "watchlist.json",
                "purpose": (
                    "DEPRECATED back-compat (one cycle): legacy watchlist "
                    "JSON shape derived from positions_and_researching.csv. "
                    "Use portfolio.json + researching.json for new code."
                ),
                "format": "json",
            },
            {
                "name": "watchlist_status.json",
                "purpose": "DEPRECATED back-compat (one cycle): mirrors positions_status.json with the legacy shape.",
                "format": "json",
                "schema_version": EXPORTS_SCHEMA_VERSION,
            },
            {
                "name": "reporting_calendar.json",
                "purpose": (
                    "Per-ticker fiscal (year, quarter) -> report-date map for "
                    "Positions union Core. Each recent_quarters row + next_expected "
                    "carries gating_eligible: for US filers true only when the SEC "
                    "XBRL fiscal label and the Finnhub-anchored count agree (foreign/"
                    "ADR and Q4 default false). Consumers (transcripts fetch-gating, "
                    "earnings_agent date verification) gate ONLY on gating_eligible."
                ),
                "format": "json",
                "schema_version": REPORTING_CALENDAR_SCHEMA_VERSION,
            },
            {
                "name": "reporting_calendar_status.json",
                "purpose": "Versioned status for the reporting calendar (read schema_version first).",
                "format": "json",
                "schema_version": REPORTING_CALENDAR_SCHEMA_VERSION,
            },
            {
                "name": "manifest.json",
                "purpose": "This file — directory of published artifacts",
                "format": "json",
            },
        ],
    }
    manifest_path = EXPORTS_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    def _rel(p):
        try:
            return str(Path(p).relative_to(SCRIPT_DIR)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    return {
        "artifacts": [
            _rel(universe_csv_path),
            _rel(metadata_path),
            _rel(status_path),
            _rel(aliases_path),
            _rel(manifest_path),
        ],
        "ticker_count": len(metadata),
    }


def _step_export_positions():
    """Publish the positions+researching list as standalone artifacts under `exports/`.

    Writes the new (canonical) artifacts:
      - positions_and_researching.csv  — full join: every universe column
                                          followed by Position-related fields
      - portfolio.json                  — {ticker: {...}} for Position=Portfolio
                                          rows only (rich legacy keys + raw
                                          universe columns)
      - researching.json                — {ticker: {...}} for Position=Researching
                                          rows only
      - following_for_interest.json     — {ticker: {...}} for Position=
                                          'Following for Interest' rows
                                          (passive tracking; no intent to trade)
      - ready_to_buy.json               — {ticker: {...}} for Position=
                                          'Ready to Buy' rows (long thesis
                                          complete; waiting for entry trigger)
      - ready_to_short.json             — {ticker: {...}} for Position=
                                          'Ready to Short' rows (short thesis
                                          complete; waiting for entry trigger)
      - positions_status.json           — versioned status + validation contract

    And keeps writing the legacy back-compat artifacts for one cycle so
    sibling consumers (sigma-alert, earnings_agent, analyst-days) continue
    working until they migrate:
      - watchlist.csv                   — derived from positions; legacy 5-col
                                          schema (Sell Price -> Target Price)
      - watchlist.json                  — same shape as before; auto-derived
      - watchlist_status.json           — same shape as before

    Source of truth is `data/positions_and_researching.csv`. The legacy
    `data/watchlist.csv` source file was deleted in Phase B (2026-05-03);
    the legacy exports are built from positions and dropped in a follow-up
    once consumers migrate.
    """
    from universe import positions as pos
    from universe import watchlist as wl  # back-compat shim
    from universe.artifacts import build_universe_metadata

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Source: positions module reads data/positions_and_researching.csv
    pos_entries = pos.load(pos.POSITIONS_PATH)
    pos_errors, pos_warnings = pos.validate(pos_entries, universe_csv_path=CSV_PATH)
    for w in pos_warnings:
        logger.info("  positions WARN: %s", w)
    for e in pos_errors:
        logger.warning("  positions ERROR: %s", e)

    # Read universe rows + header so the export mirrors whatever columns the
    # coverage universe currently carries (auto-tracks schema changes there).
    universe_rows = pos._load_universe_rows(CSV_PATH)
    # utf-8-sig, NOT utf-8. `ticker_utils.write_universe_csv` writes the source with a
    # BOM, so a plain read makes the first fieldname "\ufeffTicker". Every row below
    # then sets row["Ticker"], which DictWriter(extrasaction="ignore") silently drops --
    # publishing 84 position rows and 66 watchlist rows with a blank join key, and
    # (via the raw copy of universe.csv) costing earnings_agent and post_earnings_movers
    # all 1,086 universe tickers. `universe/artifacts.py:71` already had this right.
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        universe_fieldnames = list(csv.DictReader(f).fieldnames or [])

    metadata = build_universe_metadata(CSV_PATH)

    def _rel(p):
        try:
            return str(Path(p).relative_to(SCRIPT_DIR)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    # ── NEW: positions_and_researching.csv ──────────────────────────────────
    pos_unique_cols = [c for c in pos.POSITIONS_COLUMNS if c != "Ticker"]
    pos_csv_fieldnames = universe_fieldnames + [
        c for c in pos_unique_cols if c not in universe_fieldnames
    ]
    pos_csv_out = EXPORTS_DIR / "positions_and_researching.csv"
    # Exports are written BOM-FREE so a consumer reading plain utf-8 -- which most
    # of them do -- gets a usable header. extrasaction is left at its strict default:
    # "ignore" is what turned a header/row key mismatch into silent data loss.
    with open(pos_csv_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=pos_csv_fieldnames)
        writer.writeheader()
        for e in sorted(pos_entries, key=lambda x: x["Ticker"].upper()):
            t = e["Ticker"]
            row = dict(universe_rows.get(t, {}))
            row["Ticker"] = t
            for col in pos_unique_cols:
                v = e.get(col)
                row[col] = "" if v is None else v
            # The joined CSV must agree with portfolio.json about what is owned --
            # earnings_agent reads BOTH. One rule, one function.
            row["Position"] = pos.published_position(e)
            writer.writerow(row)

    # ── NEW: portfolio.json + researching.json ──────────────────────────────
    def _build_position_json(entries_subset):
        # A HELD row publishes `position: "Portfolio"` even though its stored `Position`
        # is now an intent value. The published field keeps the meaning it has always had
        # -- consumers read it (earnings_agent subgroups on it) and the whole promise of
        # this change was that the export contract does not move. What changed is WHO
        # decides ownership, not how the artifact expresses it.
        out = {}
        for e in entries_subset:
            t = e["Ticker"]
            meta_key = t.split()[0].split(".")[0].upper()
            meta = metadata.get(meta_key, {})
            row = universe_rows.get(t, {})
            entry = {
                "position": pos.published_position(e),
                "position_date": e.get("Position Date", ""),
                "buy_price": e.get("Buy Price"),
                "sell_price": e.get("Sell Price"),
                "first_buy_date": e.get("First Buy Date", ""),
                "average_cost": e.get("Average Cost"),
                "shares": e.get("Shares"),
                "notes": e.get("Notes", ""),
                "name": meta.get("name", ""),
                "sector": meta.get("sector", ""),
                "subsector": meta.get("subsector", ""),
                "sub_subsector": meta.get("sub_subsector", ""),
                "core": meta.get("core", ""),
            }
            for col in universe_fieldnames:
                if col == "Ticker":
                    continue
                entry[col] = row.get(col, "")
            out[t] = entry
        return out

    # OWNERSHIP IS DERIVED, NOT AUTHORED (2026-08-23). `portfolio.json` used to be
    # `filter_by_position(..., "Portfolio")` -- a value a human typed. It is now the
    # rows the BROKERS report as held (universe/held.py fills `Held` from
    # portfolio_daily's feed). The emitted JSON shape is byte-identical, so no schema
    # bump and no consumer edit: catalyst_watch pins _ACCEPTED_CM_SCHEMA={3,4} and
    # would hard-fail on an unannounced bump.
    portfolio_entries = [e for e in pos_entries if (e.get("Held") or "").strip().upper() == "Y"]
    # Held names are EXCLUDED from researching.json so the exported lists stay mutually
    # exclusive exactly as they were when Position was a single value. Without this a
    # held name would appear in both files (its intent is `Researching` while it is
    # owned), which is a membership change no consumer asked for.
    researching_entries = [
        e for e in pos.filter_by_position(pos_entries, "Researching")
        if (e.get("Held") or "").strip().upper() != "Y"
    ]
    following_entries = pos.filter_by_position(pos_entries, "Following for Interest")
    ready_to_buy_entries = pos.filter_by_position(pos_entries, "Ready to Buy")
    ready_to_short_entries = pos.filter_by_position(pos_entries, "Ready to Short")
    portfolio_json_out = EXPORTS_DIR / "portfolio.json"
    researching_json_out = EXPORTS_DIR / "researching.json"
    following_json_out = EXPORTS_DIR / "following_for_interest.json"
    ready_to_buy_json_out = EXPORTS_DIR / "ready_to_buy.json"
    ready_to_short_json_out = EXPORTS_DIR / "ready_to_short.json"
    portfolio_json_out.write_text(
        json.dumps(_build_position_json(portfolio_entries), indent=2) + "\n",
        encoding="utf-8",
    )
    researching_json_out.write_text(
        json.dumps(_build_position_json(researching_entries), indent=2) + "\n",
        encoding="utf-8",
    )
    following_json_out.write_text(
        json.dumps(_build_position_json(following_entries), indent=2) + "\n",
        encoding="utf-8",
    )
    ready_to_buy_json_out.write_text(
        json.dumps(_build_position_json(ready_to_buy_entries), indent=2) + "\n",
        encoding="utf-8",
    )
    ready_to_short_json_out.write_text(
        json.dumps(_build_position_json(ready_to_short_entries), indent=2) + "\n",
        encoding="utf-8",
    )

    # ── NEW: positions_status.json ──────────────────────────────────────────
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        source_path = str(pos.POSITIONS_PATH.relative_to(SCRIPT_DIR)).replace("\\", "/")
    except ValueError:
        source_path = str(pos.POSITIONS_PATH).replace("\\", "/")
    pos_status = {
        "schema_version": EXPORTS_SCHEMA_VERSION,
        "dataset_version": TODAY,
        "generated_at": generated_at,
        "source_path": source_path,
        "entry_count": len(pos_entries),
        "portfolio_count": len(portfolio_entries),
        "researching_count": len(researching_entries),
        "following_for_interest_count": len(following_entries),
        "ready_to_buy_count": len(ready_to_buy_entries),
        "ready_to_short_count": len(ready_to_short_entries),
        "validation_passed": len(pos_errors) == 0,
        "validation_errors": list(pos_errors),
        "validation_warnings": list(pos_warnings),
    }
    pos_status_out = EXPORTS_DIR / "positions_status.json"
    pos_status_out.write_text(json.dumps(pos_status, indent=2) + "\n", encoding="utf-8")

    # ── BACK-COMPAT (one cycle): watchlist.csv / .json / _status.json ───────
    # Derived from positions via the universe.watchlist shim, which projects
    # the new schema down to the legacy 5-col shape (Sell Price -> Target).
    legacy_entries = wl.load(wl.WATCHLIST_PATH)  # via shim
    legacy_errors, legacy_warnings = wl.validate(legacy_entries, universe_csv_path=CSV_PATH)
    legacy_unique_cols = [c for c in wl.WATCHLIST_COLUMNS if c != "Ticker"]
    legacy_csv_fieldnames = universe_fieldnames + [
        c for c in legacy_unique_cols if c not in universe_fieldnames
    ]
    legacy_csv_out = EXPORTS_DIR / "watchlist.csv"
    # Exports are written BOM-FREE so a consumer reading plain utf-8 -- which most
    # of them do -- gets a usable header. extrasaction is left at its strict default:
    # "ignore" is what turned a header/row key mismatch into silent data loss.
    with open(legacy_csv_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=legacy_csv_fieldnames)
        writer.writeheader()
        for e in sorted(legacy_entries, key=lambda x: x["Ticker"].upper()):
            t = e["Ticker"]
            row = dict(universe_rows.get(t, {}))
            row["Ticker"] = t
            row["Buy Price"] = "" if e.get("Buy Price") is None else e["Buy Price"]
            row["Target Price"] = "" if e.get("Target Price") is None else e["Target Price"]
            row["Date Added"] = e.get("Date Added", "")
            row["Notes"] = e.get("Notes", "")
            writer.writerow(row)
    legacy_joined = {}
    for e in legacy_entries:
        t = e["Ticker"]
        meta_key = t.split()[0].split(".")[0].upper()
        meta = metadata.get(meta_key, {})
        row = universe_rows.get(t, {})
        entry = {
            "buy_price": e.get("Buy Price"),
            "target_price": e.get("Target Price"),
            "date_added": e.get("Date Added", ""),
            "notes": e.get("Notes", ""),
            "name": meta.get("name", ""),
            "sector": meta.get("sector", ""),
            "subsector": meta.get("subsector", ""),
        }
        for col in universe_fieldnames:
            if col == "Ticker":
                continue
            entry[col] = row.get(col, "")
        legacy_joined[t] = entry
    legacy_json_out = EXPORTS_DIR / "watchlist.json"
    legacy_json_out.write_text(json.dumps(legacy_joined, indent=2) + "\n", encoding="utf-8")
    legacy_status = {
        "schema_version": EXPORTS_SCHEMA_VERSION,
        "dataset_version": TODAY,
        "generated_at": generated_at,
        "source_path": source_path,  # points at positions_and_researching now
        "entry_count": len(legacy_entries),
        "validation_passed": len(legacy_errors) == 0,
        "validation_errors": list(legacy_errors),
        "validation_warnings": list(legacy_warnings),
    }
    legacy_status_out = EXPORTS_DIR / "watchlist_status.json"
    legacy_status_out.write_text(json.dumps(legacy_status, indent=2) + "\n", encoding="utf-8")

    return {
        "artifacts": [
            _rel(pos_csv_out),
            _rel(portfolio_json_out),
            _rel(researching_json_out),
            _rel(following_json_out),
            _rel(ready_to_buy_json_out),
            _rel(ready_to_short_json_out),
            _rel(pos_status_out),
            _rel(legacy_csv_out),
            _rel(legacy_json_out),
            _rel(legacy_status_out),
        ],
        "entry_count": len(pos_entries),
        "portfolio_count": len(portfolio_entries),
        "researching_count": len(researching_entries),
        "following_for_interest_count": len(following_entries),
        "ready_to_buy_count": len(ready_to_buy_entries),
        "ready_to_short_count": len(ready_to_short_entries),
        "validation_passed": len(pos_errors) == 0,
    }


def _step_check_published_exports():
    """Re-read the published artifacts the way consumers do. Runs LAST, on purpose.

    Everything above validates source data and then writes. This reads the written file
    back and asserts the join key survived -- the check whose absence let a BOM ship
    84 blank-Ticker position rows, 66 blank watchlist rows, and a universe.csv from which
    three downstream projects recovered zero of 1,086 tickers, all under
    `validation_passed: true`.
    """
    from universe.export_acceptance import check_exports
    problems = check_exports(EXPORTS_DIR, strict=False)
    for msg in problems:
        logger.error("  EXPORT ACCEPTANCE: %s", msg)
    return {"checked": True, "problems": problems, "passed": not problems}


# Back-compat alias — `weekly_build` and tests reference the old name.
_step_export_watchlist = _step_export_positions


def _positions_union_core():
    """Resolve the reporting-calendar universe = Positions ∪ Core, read from the
    just-written exports (universe_metadata core=='Y' + the five Position files)."""
    tickers = set()
    meta_path = EXPORTS_DIR / "universe_metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            tickers |= {t for t, v in meta.items() if (v or {}).get("core") == "Y"}
        except Exception as e:
            logger.warning("reporting_calendar: could not read universe_metadata.json: %s", e)
    for fn in ("portfolio.json", "researching.json", "following_for_interest.json",
               "ready_to_buy.json", "ready_to_short.json"):
        p = EXPORTS_DIR / fn
        if p.exists():
            try:
                tickers |= set(json.loads(p.read_text(encoding="utf-8")).keys())
            except Exception as e:
                logger.warning("reporting_calendar: could not read %s: %s", fn, e)
    return sorted(t for t in tickers if t)


def _step_export_reporting_calendar():
    """Write exports/reporting_calendar.json + reporting_calendar_status.json.

    Per-ticker fiscal (year,quarter) → report-date map for Positions ∪ Core, with
    the SEC↔Finnhub `gating_eligible` contract (see universe/reporting_calendar.py).
    Non-gating: a failure here is `failed:`-tagged by run_step and surfaces as a
    `partial` health heartbeat; the universe + other exports still ship.
    """
    from universe.reporting_calendar import build_reporting_calendar

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    tickers = _positions_union_core()
    calendar, status_meta = build_reporting_calendar(tickers)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cal_path = EXPORTS_DIR / "reporting_calendar.json"
    cal_path.write_text(json.dumps(calendar, indent=2) + "\n", encoding="utf-8")

    status = {
        "schema_version": REPORTING_CALENDAR_SCHEMA_VERSION,
        "dataset_version": TODAY,
        "generated_at": generated_at,
        "universe": "positions_union_core",
        **status_meta,
    }
    status_path = EXPORTS_DIR / "reporting_calendar_status.json"
    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    def _rel(p):
        try:
            return str(Path(p).relative_to(SCRIPT_DIR)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    return {
        "artifacts": [_rel(cal_path), _rel(status_path)],
        "ticker_count": status_meta["ticker_count"],
        "gating_eligible_count": status_meta["gating_eligible_count"],
    }


def _step_sigma_export():
    """Push ticker metadata to the sigma-alert clone (unchanged from prior weekly_build)."""
    from reporting.sigma_export import export_and_push

    return export_and_push(CSV_PATH)


def _step_delisted_check():
    """Probe yfinance identity for each universe ticker and flag mismatches.

    Non-gating: only writes a report; never raises and never blocks downstream.
    """
    from universe import delisted_check

    result = delisted_check.check_universe()
    paths = delisted_check.write_report(result)
    return {
        "checked": result["checked"],
        "flagged": len(result["flagged"]),
        # Tickers the run could not resolve. Kept separate from `flagged` all
        # the way out to the run summary: a lookup that failed is not a
        # delisting, and merging the two is what made this check untrustworthy.
        "inconclusive": len(result.get("inconclusive", [])),
        "degraded": bool(result.get("degraded")),
        "missing_data": result["missing_data"],
        "report": paths["md_path"],
    }



def _step_symbol_directory():
    """Weekly snapshot + diff of the US exchange symbol directories.

    Non-gating. Its value is the DIFF, so it must run on the weekly cadence and
    commit its snapshot -- Nasdaq keeps no archive, and a week not captured is a
    diff that can never be computed.
    """
    from pathlib import Path as _P
    from universe import symbol_directory as sd
    from universe.ticker_change_check import _EDGAR_UA as _ua

    status, rec, _ = sd.run(_P(__file__).resolve().parent, identity=_ua)
    if status != "ok" or rec is None:
        # INCONCLUSIVE, never "no changes" -- a failed download reported as a
        # quiet week is how a watchdog stops watching.
        raise RuntimeError("symbol directory unavailable (inconclusive, not clean)")
    return {
        "added": len(rec.added),
        "removed": len(rec.removed),
        "universe_removed": rec.universe_removed,
        "universe_missing": len(rec.universe_missing),
        "deficient": len(rec.universe_deficient),
        "checked": rec.checked_us_rows,
    }


def _step_form10_watch():
    """Form 10-12B registrations -- spin-offs and uplistings, before they list.

    Non-gating. Feeds the report's forward section, which is the only place a
    pre-listing signal can usefully land.
    """
    from pathlib import Path as _P
    from universe import form10_watch as f10
    from universe.ticker_change_check import _EDGAR_UA as _ua

    from config import API_KEYS as _keys

    status, filings, _ = f10.run(_P(__file__).resolve().parent, ua=_ua,
                                 api_key=_keys.get("FMP_API_KEY", ""), days=14)
    if status != "ok":
        raise RuntimeError("Form 10 search unavailable (inconclusive, not clean)")
    rel = [f for f in filings if f.verdict == "relevant"]
    return {
        "registrants": len(filings),
        "relevant": [f"{f.registrant} ({f.listing_kind}"
                     + (f", parent {f.parent}" if f.parent else "") + ")"
                     for f in rel],
        "inconclusive": len([f for f in filings if f.verdict == "inconclusive"]),
    }

def _step_ticker_change_check():
    """Discover ticker changes (renames) + SEC deregistrations via the stable
    CIK->ticker map. Companion to _step_delisted_check (price-feed based).

    Non-gating: only writes a report; never raises and never blocks downstream.
    """
    from universe import ticker_change_check

    result = ticker_change_check.check_ticker_changes()
    paths = ticker_change_check.write_report(result)
    return {
        "checked": result["checked"],
        "changes": len(result["changes"]),
        "settled": len(result.get("settled", [])),
        "deregistered": len(result["deregistered"]),
        "sec_fetched_ok": result["sec_fetched_ok"],
        "report": paths["md_path"],
    }


def _step_resolve_cik_by_name():
    """Find blank-CIK rows whose COMPANY NAME matches an SEC registrant filing
    under a DIFFERENT ticker.

    Report-only. Closes the circular blind spot where `backfill-cik` (keyed on
    the current ticker) and `check-ticker-changes` (keyed on the CIK) both miss a
    renamed row -- `FGEN` and `CYBN` were both found this way, by hand, after the
    fact.
    """
    from universe import cik_name_resolver as rcn

    result, report = rcn.main()
    return {"checked": result.checked, "report": str(report),
            "fetched_ok": result.fetched_ok,
            "stale_us": len(result.by_verdict(rcn.STALE_US_LISTING)),
            "ambiguous": len(result.by_verdict(rcn.AMBIGUOUS_NAME)),
            "other_line": len(result.by_verdict(rcn.SEC_REGISTERED_OTHER_LINE)),
            "needs_review": len(result.needs_review)}


def _cik_resolver_step_status(r):
    """Counted classes, ASCII-only, no company names -- this string reaches a
    cp1252 console mid-run."""
    if not r.get("fetched_ok", True):
        return "failed: SEC map unavailable - resolver learned nothing"
    tail = (f"{r['checked']} blank-CIK row(s) checked; {r['stale_us']} stale US "
            f"listing(s), {r['ambiguous']} ambiguous, {r['other_line']} other-line")
    return f"failed: review needed - {tail}" if r["needs_review"] else f"ok - {tail}"


def _step_verify_isin_issuers():
    """Audit every stored ISIN against the issuer name OpenFIGI maps it to.

    Wired into the weekly 2026-07-29, for the same reason `crosscheck-foreign`
    was the day before: this check existed and was run-on-demand, so its findings
    only surfaced when somebody thought to look. The first full pass found 21
    conflicts that had been live since the 2026-04-03 bulk import — four months.
    The stock of already-stored identifiers is exactly what no write-path guard
    can reach, so the audit has to run on a cadence or the backlog silently
    re-accumulates.

    Read-only and NON-GATING. Cache-warm in the normal case (deterministic
    OpenFIGI answers are cached), so the weekly cost is small.
    """
    from universe import isin_identity

    return isin_identity.main()


def _isin_identity_step_status(result):
    """Counted classes, never a boolean — `4 conflict(s)` is actionable,
    `conflicts: yes` is not. ASCII-only and no company names: this string reaches
    a cp1252 console and the universe is global.
    """
    checked = result.get("checked", 0)
    conflicts = len(result.get("conflicts") or [])
    inconclusive = len(result.get("inconclusive") or [])
    ok = result.get("ok", 0)
    tail = (f"{ok} ok / {conflicts} conflict(s) / {inconclusive} inconclusive "
            f"of {checked} ISIN-bearing row(s)")
    if checked and ok == 0:
        # Learned nothing: must not report as clean (the delisted_check rule).
        return f"failed: audit learned nothing - {tail}"
    if conflicts:
        return f"failed: {tail}"
    return f"ok - {tail}" if not inconclusive else f"ok (with inconclusive) - {tail}"


def _step_crosscheck_foreign():
    """Cross-check foreign-row identity metadata against SEC N-PORT filings.

    Read-only and NON-GATING (Fable, 2026-07-28): the seven wrong ISINs that
    survived four months did so because every identity check was run-on-demand;
    this puts `crosscheck-foreign` on the weekly cadence. A conflict must never
    fail the build or block exports — `_crosscheck_step_status` marks the step
    `failed:` so the health heartbeat reads `partial`, and the run summary
    carries the per-class counts (a counted class, never a boolean).
    """
    from universe import foreign_crosscheck

    result = foreign_crosscheck.main()
    report = foreign_crosscheck.write_report(result)
    by_kind = {}
    for c in result.conflicts:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    return {
        "status": result.status,
        "ok": result.ok,
        "checked": result.checked,
        "matched": result.matched,
        "conflicts": len(result.conflicts),
        "by_kind": by_kind,
        "incorporation_notes": len(result.incorporation_notes),
        "report": str(report),
    }


# The four conflict classes, in severity order. Zero-count classes are still
# printed: "4 listing-mismatch, 0 isin-conflict" is actionable, "conflicts:
# yes" is not — and an absent count is indistinguishable from an unchecked one.
_CROSSCHECK_KINDS = ("isin-conflict", "lei-conflict", "name-divergence",
                     "listing-mismatch")


def _crosscheck_step_status(cf_result):
    """Render the crosscheck step's status string (ASCII-only by construction —
    counts and fixed labels; no company names, whose non-ASCII characters have
    twice killed a cp1252 console mid-run)."""
    if not cf_result.get("ok"):
        return "failed: every source failed - nothing checked"
    kinds = cf_result.get("by_kind", {})
    counts = ", ".join(f"{kinds.get(k, 0)} {k}" for k in _CROSSCHECK_KINDS)
    summary = (
        f"{counts}; {cf_result.get('incorporation_notes', 0)} incorporation note(s) "
        f"({cf_result.get('matched', 0)} matched of {cf_result.get('checked', 0)} "
        f"foreign rows)")
    if cf_result.get("conflicts"):
        return f"failed: {cf_result['conflicts']} conflict(s) - {summary}"
    return summary


def _step_cik_backfill():
    """Fill blank CIKs from SEC's bulk map before anything reads the exports.

    A CIK is a fact about whether a company has REGISTERED YET, not a static
    property, so blanks must be re-probed on a schedule. Without this, a name
    that registers after its row was enriched keeps a blank CIK forever and
    every CIK-keyed lane silently skips it — which is precisely how SpaceX went
    unscreened by insider_ownership while sitting in the portfolio (2026-07-25).

    Runs BEFORE the export steps so a newly-resolved CIK reaches consumers in
    the same run. Non-gating: a failed SEC fetch changes nothing and is
    reported.
    """
    from universe import cik_backfill

    return cik_backfill.main()


def _step_weekly_page():
    """Render the newest weekly report into `docs/` for GitHub Pages.

    Returns `{"skipped": reason}` when there is no report to render, so the step
    summary can say *why* nothing was published rather than reporting a bare `ok`
    over an untouched page.
    """
    from reporting import weekly_page as wp

    try:
        path, stamp = wp.find_report()
    except FileNotFoundError as exc:
        return {"skipped": str(exc)}
    briefs = wp.find_briefings(stamp)
    result = wp.publish(path.read_text(encoding="utf-8", errors="replace"),
                        report_date=stamp, thread_ts=wp.thread_ts_for(stamp),
                        briefings_md=briefs.read_text(encoding="utf-8", errors="replace")
                        if briefs else "")
    logger.info("[page] %s -> docs/ (briefings: %s) (%s)", path.name,
                briefs.name if briefs else "none", result["url"])
    return result


def _step_universe_delta_slack(baseline):
    """Post a weekly before/delta/after universe summary to Slack #coverage.

    Baseline tiers (universe_delta.load_baseline_*):
      1. `.coverage/last_run_*.csv` — end-of-previous-run snapshot (preferred).
      2. git HEAD captured at run start — bootstrap fallback. If git baseline
         is used AND the working tree was dirty at run start, a caveat appears
         in the Slack message header so the user knows the diff may include
         pre-existing local edits.

    Lifecycle in order:
      1. Compute delta from baseline vs working tree.
      2. Write delta JSON to .coverage/ (ALWAYS, regardless of Slack outcome).
      3. Post to Slack #coverage.
      4. Write run snapshot to .coverage/ (ALWAYS — represents this run's end
         state; becomes next week's baseline regardless of Slack success).
      5. Send the [ClaudeFin] email alert (additive channel, independent of
         Slack outcome; never the old EMAIL_ENABLED full-report email).
      6. Raise on Slack and/or email failure so the step status becomes
         `failed: ...` and `collect_non_successes` flags `#status-reports`
         as `partial`. Non-gating either way.
    """
    import os
    import pandas as pd

    from config import API_KEYS
    from reporting import email_alert_client
    from reporting.universe_delta import (
        SNAPSHOT_UNIVERSE_PATH,
        compute_universe_delta,
        compute_ytd_summary,
        format_universe_delta_email,
        load_baseline_universe,
        load_baseline_positions,
        load_universe_snapshot,
        load_positions_snapshot,
        load_ytd_delta_history,
        post_universe_delta,
        snapshot_mtime_date,
        write_delta_json,
        write_run_snapshot,
    )

    head_sha = (baseline or {}).get("head_sha")
    head_date = (baseline or {}).get("head_date")
    dirty_paths = (baseline or {}).get("dirty_paths", []) or []

    # Tier 1 vs Tier 2 detection: presence of snapshot file decides.
    using_snapshot = SNAPSHOT_UNIVERSE_PATH.exists()
    if using_snapshot:
        baseline_source = "snapshot"
        snap_date = snapshot_mtime_date(SNAPSHOT_UNIVERSE_PATH) or "previous run"
        baseline_label = f"end of previous run · {snap_date}"
        baseline_caveat = None
    elif head_sha:
        baseline_source = "git"
        baseline_label = f"commit @ {head_sha[:7]}, {head_date or 'previous commit'} (no snapshot found — bootstrap fallback)"
        baseline_caveat = (
            "Working tree was dirty at run start — pre-existing local edits "
            "may appear in this delta. Future runs will use snapshot baselines."
        ) if dirty_paths else None
    else:
        baseline_source = "none"
        baseline_label = None
        baseline_caveat = None

    before_universe = load_baseline_universe(commit_sha=head_sha, baseline=baseline)
    before_positions = load_baseline_positions(commit_sha=head_sha, baseline=baseline)
    after_universe = load_universe_snapshot(commit_sha=None)
    after_positions = load_positions_snapshot(commit_sha=None)

    delisted_path = DATA_DIR / "delisted_tickers.csv"
    delisted_df = None
    if delisted_path.exists():
        delisted_df = pd.read_csv(delisted_path, encoding="utf-8-sig")

    delta = compute_universe_delta(
        before_universe_df=before_universe,
        after_universe_df=after_universe,
        before_positions_df=before_positions,
        after_positions_df=after_positions,
        delisted_df=delisted_df,
        baseline_sha=head_sha,
        baseline_date=head_date,
        baseline_source=baseline_source,
        baseline_label=baseline_label,
        baseline_caveat=baseline_caveat,
    )

    # ALWAYS persist the delta JSON before posting. Position-change overflow
    # ("see fallback file") relies on this — the file must exist whether or
    # not the Slack post succeeds. Writing first also means the YTD summary
    # below includes this run.
    write_delta_json(delta)

    # Year-to-date block: aggregate this year's persisted delta files.
    # Best-effort — a YTD failure must never block the weekly post.
    try:
        ytd = compute_ytd_summary(load_ytd_delta_history())
    except Exception as e:
        logger.warning("YTD delta summary failed (posting without it): %s", e)
        ytd = None

    # Webhook resolution: real OS env first, then .env via API_KEYS. Mirrors
    # the health-heartbeat pattern.
    webhook = os.environ.get("SLACK_WEBHOOK_COVERAGE") or API_KEYS.get("SLACK_WEBHOOK_COVERAGE")
    post_result = post_universe_delta(webhook, delta, ytd=ytd)

    # ALWAYS write the run snapshot — Slack success/failure is orthogonal to
    # what the universe state actually is. Next week's baseline must reflect
    # this run's actual end state.
    write_run_snapshot()

    # [ClaudeFin] email alert — ADDITIVE to the Slack post (root CONVENTIONS.md
    # "Email alerts ([ClaudeFin])"), sent even when Slack failed (independent
    # channel). send_alert never raises; a False is folded into this step's
    # failure below, exactly like the Slack path. This is NOT the old
    # EMAIL_ENABLED full-report email — that stays flag-disabled.
    email_subject, email_body = format_universe_delta_email(delta, ytd=ytd)
    email_sent = email_alert_client.send_alert(
        "Coverage Manager", email_subject, email_body)

    failures = []
    if not post_result["posted"]:
        failures.append(f"Slack post failed: {post_result['reason']}")
    if not email_sent:
        failures.append("[ClaudeFin] email alert failed (see log)")
    if failures:
        # Raise so pipeline_utils.run_step marks this step `failed: ...` and
        # collect_non_successes flags the health heartbeat as `partial`.
        # Non-gating — the universe CSV + exports + sigma push already ran.
        raise RuntimeError("; ".join(failures))

    return {
        "posted": True,
        "email_alert": True,
        "reason": None,
        "added": len(delta["added"]),
        "removed": len(delta["removed"]),
        "modified": len({m["ticker"] for m in delta["modified"]}),
        "position_changes": len(delta["position_changes"]),
        "before_total": delta["before_stats"]["total"],
        "after_total": delta["after_stats"]["total"],
        "baseline_source": baseline_source,
    }


# ── Result helper ────────────────────────────────────────────────────────────


def _make_result(steps, validation_passed, artifacts):
    """Build the standardized orchestrator result shape."""
    return {
        "command": "weekly-universe",
        "date": TODAY,
        "validation_passed": validation_passed,
        "steps": steps,
        "artifacts": artifacts,
        "non_successes": collect_non_successes(steps),
    }


# ── Main ─────────────────────────────────────────────────────────────────────


def main(skip_discovery=False, dry_run=False, force=False, log_audit=True):
    """Run the universe-side weekly pipeline.

    Args:
        skip_discovery: Skip the discovery step (used by the Friday scheduled task).
        dry_run: Validate and report only — no mutations to disk or remote.
        force: Continue past validation errors (informational here; the wrapper
            uses validation_passed in the returned dict to gate the report side).
        log_audit: Whether to write a row to run_log.csv. The wrapper passes
            this through; direct CLI invocation defaults to True.

    Returns the standardized result dict (see `_make_result`).
    """
    logger.info("=" * 60)
    logger.info("Weekly Universe -- %s", TODAY)
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN -- no mutations will be made")

    steps = {}
    artifacts = []
    validation_passed = False

    # Step 0: Capture baseline git SHA BEFORE any mutation step runs.
    # The post-step (universe_delta_slack) diffs this committed state against
    # the working tree at the end of the run to produce the #coverage post.
    # Calendar-independent and survives manual mid-week commits.
    baseline = None
    if not dry_run:
        try:
            from reporting.universe_delta import capture_baseline_shas
            baseline = capture_baseline_shas()
            if baseline.get("head_sha"):
                logger.info(
                    "Baseline universe SHA: %s (%s)",
                    baseline["head_sha"][:7],
                    baseline.get("head_date") or "unknown date",
                )
            else:
                logger.warning("No baseline SHA available - Slack delta will mark baseline as unavailable")
        except Exception as e:
            logger.warning("Failed to capture baseline SHA: %s", e)

    # Step 1: Validate
    logger.info("[1/6] Validating coverage universe...")
    status, validation_result = run_step("validate", _step_validate)
    steps["validate"] = status
    if validation_result:
        logger.info(
            "  %d rows, %d errors, %d warnings",
            validation_result["rows"],
            len(validation_result["errors"]),
            len(validation_result["warnings"]),
        )
        validation_passed = validation_result["passed"]
        if not validation_passed:
            logger.warning("  Validation errors found")
    else:
        # Validation step itself failed (CSV unreadable, etc.) — treat as not passed
        # and synthesize a minimal result for the export step so it can still emit
        # a status file documenting the failure.
        validation_result = {
            "rows": 0,
            "errors": [f"validate step crashed: {steps['validate']}"],
            "warnings": [],
            "passed": False,
        }

    # Step 2: Archive universe outputs
    logger.info("[2/6] Archiving prior universe outputs...")
    if dry_run:
        steps["archive"] = "skipped (dry run)"
    else:
        status, _ = run_step("archive", _step_archive_universe)
        steps["archive"] = status

    # Step 3: Discovery
    if skip_discovery:
        logger.info("[3/6] Discovery... SKIPPED")
        steps["discovery"] = "skipped"
    else:
        logger.info("[3/6] Discovery...")
        status, result = run_step("discovery", _step_discovery, dry_run=dry_run)
        steps["discovery"] = status
        if result:
            logger.info("  Discovery: %s", result.get("status", "unknown"))

    # Step 4: Delisted / recycled ticker check
    if dry_run:
        logger.info("[4/6] Delisted check... SKIPPED (dry run)")
        steps["delisted_check"] = "skipped (dry run)"
    else:
        logger.info("[4/6] Checking universe for delisted/recycled tickers...")
        status, dc_result = run_step("delisted_check", _step_delisted_check)
        if dc_result:
            summary = (
                f"{dc_result['flagged']} flagged of {dc_result['checked']} "
                f"({dc_result.get('inconclusive', 0)} inconclusive, "
                f"missing data: {dc_result['missing_data']})"
            )
            # A degraded run must reach the health heartbeat, and
            # `collect_non_successes` only recognises the "failed:"/"blocked:"
            # prefixes -- an unprefixed "[DEGRADED]" note reads as success and
            # posts a green :white_check_mark: for a run that checked half the
            # universe. The CLI already exits 2 here; the scheduled path must
            # agree with it rather than quietly disagreeing.
            steps["delisted_check"] = (
                f"failed: degraded - {summary}" if dc_result.get("degraded")
                else summary
            )
            if dc_result["flagged"]:
                logger.warning(
                    "  %d ticker(s) flagged - review %s",
                    dc_result["flagged"], dc_result["report"],
                )
            if dc_result.get("degraded"):
                logger.warning(
                    "  DEGRADED: high lookup-failure rate (Yahoo throttling), "
                    "so this week's flags are provisional - see %s",
                    dc_result["report"],
                )
        else:
            steps["delisted_check"] = status

    # Step 4a: Backfill blank CIKs from SEC's bulk map. Placed before the export
    # steps so a company that has newly registered becomes visible to
    # CIK-keyed consumers (insider_ownership, earnings_agent) in this same run.
    if dry_run:
        logger.info("[4a/6] CIK backfill... SKIPPED (dry run)")
        steps["cik_backfill"] = "skipped (dry run)"
    else:
        logger.info("[4a/6] Backfilling blank CIKs from SEC EDGAR...")
        status, cb_result = run_step("cik_backfill", _step_cik_backfill)
        if cb_result:
            if not cb_result["fetched_ok"]:
                # "failed:" so it reaches the heartbeat -- see the delisted_check
                # note above. A silent pass here means the blanks were never
                # re-probed, i.e. the exact gap this step exists to close stayed
                # open while the run reported ok.
                steps["cik_backfill"] = "failed: SEC map unavailable - no rows changed"
                logger.warning("  SEC map unavailable; blank CIKs NOT re-probed")
            else:
                steps["cik_backfill"] = (
                    f"{cb_result['filled']} filled, "
                    f"{cb_result['still_blank']} still blank"
                    # NOT "(non-US)": most are, but asserting it here explains
                    # away a count that can also contain real US misses.
                    + (f", {cb_result['rejected_name_mismatch']} skipped on "
                       f"name mismatch"
                       if cb_result.get("rejected_name_mismatch") else "")
                )
                # The module already logs each filled row; repeating them here
                # doubled every line, at WARNING, for a SUCCESS event -- which
                # pollutes any warn-level monitoring.
        else:
            steps["cik_backfill"] = status

    # Step 4b: Ticker-change / deregistration discovery (SEC CIK->ticker map).
    # Complements delisted_check: it surfaces the NEW symbol for a renamed
    # company so the row can be remapped rather than just removed. Non-gating.
    if dry_run:
        logger.info("[4b/6] Ticker-change check... SKIPPED (dry run)")
        steps["ticker_change_check"] = "skipped (dry run)"
    else:
        logger.info("[4b/6] Checking universe for ticker changes / deregistrations...")
        status, tc_result = run_step("ticker_change_check", _step_ticker_change_check)

        if tc_result:
            if not tc_result["sec_fetched_ok"]:
                steps["ticker_change_check"] = "SEC data unavailable — not checked"
            else:
                # `settled` is reported but never warned on: it counts splits a
                # human already adjudicated into the alias store, and folding it
                # into the mismatch count is what kept Fiserv lit for months.
                settled = tc_result.get("settled", 0)
                steps["ticker_change_check"] = (
                    f"{tc_result['changes']} mismatch, {tc_result['deregistered']} "
                    f"deregistered of {tc_result['checked']}"
                    + (f" ({settled} settled split(s))" if settled else "")
                )
                if tc_result["changes"] or tc_result["deregistered"]:
                    logger.warning(
                        "  %d ticker-mismatch + %d deregistration candidate(s) — see %s",
                        tc_result["changes"], tc_result["deregistered"], tc_result["report"],
                    )
        else:
            steps["ticker_change_check"] = status

    # Step 4f: US exchange symbol-directory snapshot + diff. Wired weekly
    # 2026-08-06 alongside the Form 10 watch. Both were built as standalone
    # scheduled tasks that wrote reports/*.md -- and nothing read them, so two
    # correct lanes reached nobody. The DIFF is the product here: Nasdaq keeps
    # no archive, so a missed week is a diff that can never be computed.
    if dry_run:
        logger.info("[4f/6] US symbol-directory... SKIPPED (dry run)")
        steps["symbol_directory"] = "skipped (dry run)"
    else:
        logger.info("[4f/6] US symbol-directory snapshot + diff...")
        status, sd_result = run_step("symbol_directory", _step_symbol_directory)
        if sd_result:
            steps["symbol_directory"] = (
                f"{sd_result['added']} new listings, {sd_result['removed']} removed; "
                f"{len(sd_result['universe_removed'])} covered name(s) removed, "
                f"{sd_result['universe_missing']} absent, "
                f"{sd_result['deficient']} financial-status flag(s) "
                f"of {sd_result['checked']} US rows"
            )
            if sd_result["universe_removed"]:
                logger.warning(
                    "  COVERED NAMES REMOVED FROM THE EXCHANGE: %s",
                    ", ".join(sd_result["universe_removed"]),
                )
        else:
            steps["symbol_directory"] = status

    # Step 4g: Form 10-12B watch -- spin-offs and OTC uplistings, 1-3 months
    # BEFORE they list. A spin-off has no offering, so the Finnhub IPO calendar
    # is structurally blind to it; this is the only forward signal for that
    # class. Feeds the report's "Pipeline / filings to monitor" section.
    if dry_run:
        logger.info("[4g/6] Form 10 watch... SKIPPED (dry run)")
        steps["form10_watch"] = "skipped (dry run)"
    else:
        logger.info("[4g/6] Form 10 spin-off / uplisting watch...")
        status, f10_result = run_step("form10_watch", _step_form10_watch)
        if f10_result:
            steps["form10_watch"] = (
                f"{len(f10_result['relevant'])} relevant, "
                f"{f10_result['inconclusive']} inconclusive "
                f"of {f10_result['registrants']} registrants"
            )
            for name in f10_result["relevant"]:
                logger.warning("  FORWARD LISTING: %s", name)
        else:
            steps["form10_watch"] = status

    # Step 4c: Foreign metadata cross-check vs SEC N-PORT (read-only). Wired
    # weekly on 2026-07-28 (Fable): the seven wrong ISINs corrected that day
    # survived four months because every identity check was run-on-demand.
    # NON-GATING — a conflict marks the step `failed:` (so the heartbeat reads
    # `partial` and the run summary carries per-class counts) but never blocks
    # the exports; the fix is always a human decision on the CSV.
    if dry_run:
        logger.info("[4c/6] Foreign crosscheck... SKIPPED (dry run)")
        steps["crosscheck_foreign"] = "skipped (dry run)"
    else:
        logger.info("[4c/6] Cross-checking foreign rows against SEC N-PORT...")
        status, cf_result = run_step("crosscheck_foreign", _step_crosscheck_foreign)
        if cf_result:
            steps["crosscheck_foreign"] = _crosscheck_step_status(cf_result)
            if cf_result["conflicts"]:
                logger.warning(
                    "  %d conflict(s) between the universe and filed documents - "
                    "see %s", cf_result["conflicts"], cf_result["report"],
                )
        else:
            steps["crosscheck_foreign"] = status

    # Step [4e/6]: blank-CIK resolution by company name
    if dry_run:
        logger.info("[4e/6] Blank-CIK name resolution... SKIPPED (dry run)")
        steps["resolve_cik_by_name"] = "skipped (dry run)"
    else:
        logger.info("[4e/6] Resolving blank CIKs by company name...")
        status, rc_res = run_step("resolve_cik_by_name", _step_resolve_cik_by_name)
        steps["resolve_cik_by_name"] = (
            _cik_resolver_step_status(rc_res) if rc_res else status)

    # Step [4d/6]: ISIN -> issuer-name identity audit
    if dry_run:
        logger.info("[4d/6] ISIN issuer identity audit... SKIPPED (dry run)")
        steps["verify_isin_issuers"] = "skipped (dry run)"
    else:
        logger.info("[4d/6] Auditing stored ISINs against their issuer names...")
        status, ii_result = run_step("verify_isin_issuers", _step_verify_isin_issuers)
        steps["verify_isin_issuers"] = (
            _isin_identity_step_status(ii_result) if ii_result else status)

    # Step 5: Export artifacts (the new published contract)
    if dry_run:
        logger.info("[5/6] Export artifacts... SKIPPED (dry run)")
        steps["export_artifacts"] = "skipped (dry run)"
    else:
        logger.info("[5/6] Writing published artifacts to exports/...")
        status, export_result = run_step(
            "export_artifacts", _step_export_artifacts, validation_result
        )
        steps["export_artifacts"] = status
        if export_result:
            artifacts.extend(export_result["artifacts"])
            logger.info(
                "  Wrote %d artifacts (%d tickers)",
                len(export_result["artifacts"]),
                export_result["ticker_count"],
            )

    # Step 5b: Watchlist artifact export
    if dry_run:
        logger.info("[5b/6] Export watchlist... SKIPPED (dry run)")
        steps["export_watchlist"] = "skipped (dry run)"
    else:
        logger.info("[5b/6] Writing watchlist artifact to exports/...")
        status, wl_result = run_step("export_watchlist", _step_export_watchlist)
        steps["export_watchlist"] = status
        if wl_result:
            artifacts.extend(wl_result["artifacts"])
            logger.info(
                "  Wrote watchlist (%d entries, validation_passed=%s)",
                wl_result["entry_count"],
                wl_result["validation_passed"],
            )

    # Step 5c: Reporting-calendar export (fiscal-period -> report-date map).
    # Non-gating; runs after the universe + positions exports exist (it reads them
    # to resolve Positions ∪ Core) and before sigma_export.
    if dry_run:
        logger.info("[5c/6] Export reporting-calendar... SKIPPED (dry run)")
        steps["export_reporting_calendar"] = "skipped (dry run)"
    else:
        logger.info("[5c/6] Writing reporting-calendar artifact to exports/...")
        status, rc_result = run_step("export_reporting_calendar", _step_export_reporting_calendar)
        steps["export_reporting_calendar"] = status
        if rc_result:
            artifacts.extend(rc_result["artifacts"])
            logger.info(
                "  Wrote reporting-calendar (%d tickers, %d gating-eligible)",
                rc_result["ticker_count"], rc_result["gating_eligible_count"],
            )

    # Step 5d: acceptance — re-read the published artifacts the way consumers do.
    # Deliberately AFTER every writer and BEFORE sigma_export, so a broken contract is
    # reported before it is propagated into the sibling repo. Non-gating by design (the
    # universe CSV update is still the real product) but it turns a silent, green,
    # zero-ticker export into a visible failure in the run summary and heartbeat.
    if not dry_run:
        logger.info("[5d/6] Re-reading published exports as a consumer would...")
        status, acc = run_step("check_published_exports", _step_check_published_exports)
        steps["check_published_exports"] = status
        if acc and not acc["passed"]:
            steps["check_published_exports"] = (
                f"failed: {len(acc['problems'])} export(s) unreadable or missing a join key")

    # Step 6: Sigma-alert metadata export
    if dry_run:
        logger.info("[6/6] Sigma export... SKIPPED (dry run)")
        steps["sigma_export"] = "skipped (dry run)"
    else:
        logger.info("[6/6] Exporting ticker metadata to sigma-alert...")
        status, result = run_step("sigma_export", _step_sigma_export)
        if result:
            outcome = result.get("status", "unknown")
            tickers = result.get("tickers", 0)
            reason = result.get("reason", "")
            missing = result.get("missing_metadata") or {}
            if outcome == "skipped":
                steps["sigma_export"] = f"skipped: {reason}"
            elif outcome in ("pushed", "committed", "unchanged"):
                detail = f"{outcome} ({tickers} tickers)"
                if reason:
                    detail = f"{detail} — {reason}"
                if missing:
                    detail = f"{detail} | sigma-alert flagged {len(missing)} missing: {sorted(missing)}"
                steps["sigma_export"] = detail
            elif outcome == "committed_not_pushed":
                steps["sigma_export"] = f"failed: {reason} (commit is local in sigma-alert clone)"
            elif outcome == "failed":
                steps["sigma_export"] = f"failed: {reason}"
            else:
                steps["sigma_export"] = status
        else:
            steps["sigma_export"] = status

    # Render the published weekly page into docs/ for GitHub Pages.
    #
    # Deliberately NOT gated on anything above: the page is a rendering of a report
    # that already exists on disk, so it can and should refresh even on a run where
    # a lane failed — a stale page is exactly what JP asked to stop reading.
    # `run_weekly_coverage.bat` does `git add -A` + commit + push with exit-code
    # gating right after this, so writing the file IS the publish.
    #
    # A missing report is `skipped`, not `failed`: the page is downstream of the
    # discovery session, and a week the session did not run is a week with nothing
    # new to render, not a broken pipeline.
    if dry_run:
        logger.info("[page] Weekly page... SKIPPED (dry run)")
        steps["weekly_page"] = "skipped (dry run)"
    else:
        logger.info("[page] Rendering the published weekly page...")
        status, page_result = run_step("weekly_page", _step_weekly_page)
        if page_result is None:
            steps["weekly_page"] = status
        elif page_result.get("skipped"):
            steps["weekly_page"] = f"skipped: {page_result['skipped']}"
        else:
            steps["weekly_page"] = (
                f"ok ({page_result['report_date']}, {page_result['open']} awaiting a "
                f"reply, {page_result['archived']} week(s) archived)"
            )

    # Post-step: Weekly universe delta -> Slack #coverage
    # Runs AFTER discovery/delisted_check/exports/sigma_export so the diff
    # captures every change in the universe state this run, and the totals
    # quoted in the Slack post match what downstream consumers will read.
    # On Slack-post failure the step raises so run_step records `failed: ...`,
    # which collect_non_successes catches and the health heartbeat reports as
    # `partial`. Non-gating — the universe CSV update is the real product.
    if dry_run:
        logger.info("[post] Universe delta Slack... SKIPPED (dry run)")
        steps["universe_delta_slack"] = "skipped (dry run)"
    else:
        logger.info("[post] Posting universe delta to Slack #coverage...")
        status, ud_result = run_step("universe_delta_slack", _step_universe_delta_slack, baseline)
        if ud_result:
            # Success path — ud_result is only returned on posted=True
            steps["universe_delta_slack"] = (
                f"posted [{ud_result['baseline_source']}] "
                f"(+{ud_result['added']}/-{ud_result['removed']}, "
                f"{ud_result['modified']} modified, {ud_result['position_changes']} pos)"
            )
        else:
            # Step raised — status starts with "failed: ..."
            steps["universe_delta_slack"] = status

    # Summary
    logger.info("")
    logger.info("-- Weekly Universe Summary --")
    for step_name, status in steps.items():
        logger.info("%-20s %s", step_name, status)

    non_successes = collect_non_successes(steps)
    if non_successes:
        logger.warning(
            "Weekly universe completed with %d non-success(es): %s",
            len(non_successes),
            non_successes,
        )
    else:
        logger.info("Weekly universe completed successfully")

    # Audit log
    if log_audit and not dry_run:
        try:
            from audit import log_run

            notes = "discovery skipped" if skip_discovery else ""
            log_run("weekly-universe", steps, notes=notes)
        except Exception as e:
            logger.warning("Failed to write audit log: %s", e)

    return _make_result(steps, validation_passed, artifacts)
