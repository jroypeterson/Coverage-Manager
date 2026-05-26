"""Universe-side weekly orchestrator.

Owns the universe management half of the weekly pipeline:
validate -> archive -> discovery -> export-artifacts -> sigma-export.

Produces a versioned, published artifact contract under `exports/` that other
projects in this workspace consume (forensic_triage, biotech_triage,
idea_generation, 13F analyzer). See `exports/manifest.json` and
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
EXPORTS_SCHEMA_VERSION = 3

UNIVERSE_ARCHIVE_PATTERNS = [
    "weekly_coverage_universe_additions_*.md",
    "company_backgrounds_*.md",
    "delisted_check_*.md",
    "delisted_check_*.csv",
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

    return archive_files(REPORTS_DIR, OLD_REPORTS_DIR, TODAY, UNIVERSE_ARCHIVE_PATTERNS)


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

    Produces four files described in `exports/manifest.json`:
      - universe.csv              — snapshot of the coverage universe CSV
      - universe_metadata.json    — {ticker: {name, sector, subsector}} dict
      - universe_status.json      — versioned status + validation contract
      - manifest.json             — directory of files in this exports/ folder

    `validation_result` is the dict returned by `_step_validate` and feeds the
    status file's validation_passed / errors / warnings fields.
    """
    from universe.artifacts import build_universe_metadata_with_stats

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Snapshot the CSV.
    universe_csv_path = EXPORTS_DIR / "universe.csv"
    shutil.copyfile(CSV_PATH, universe_csv_path)

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
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
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
    with open(pos_csv_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=pos_csv_fieldnames, extrasaction="ignore")
        writer.writeheader()
        for e in sorted(pos_entries, key=lambda x: x["Ticker"].upper()):
            t = e["Ticker"]
            row = dict(universe_rows.get(t, {}))
            row["Ticker"] = t
            for col in pos_unique_cols:
                v = e.get(col)
                row[col] = "" if v is None else v
            writer.writerow(row)

    # ── NEW: portfolio.json + researching.json ──────────────────────────────
    def _build_position_json(entries_subset):
        out = {}
        for e in entries_subset:
            t = e["Ticker"]
            meta_key = t.split()[0].split(".")[0].upper()
            meta = metadata.get(meta_key, {})
            row = universe_rows.get(t, {})
            entry = {
                "position": e.get("Position", ""),
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

    portfolio_entries = pos.filter_by_position(pos_entries, "Portfolio")
    researching_entries = pos.filter_by_position(pos_entries, "Researching")
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
    with open(legacy_csv_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=legacy_csv_fieldnames, extrasaction="ignore")
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


# Back-compat alias — `weekly_build` and tests reference the old name.
_step_export_watchlist = _step_export_positions


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
        "missing_data": result["missing_data"],
        "report": paths["md_path"],
    }


def _step_universe_delta_slack(baseline):
    """Post a weekly before/delta/after universe summary to Slack #coverage.

    Reads pre-mutation snapshots from the baseline SHA captured at the top of
    main(), reads post-mutation snapshots from the working tree, computes the
    delta, and posts to #coverage. On any failure (no webhook, network, etc.)
    the full delta is written to .coverage/last_universe_delta.json plus a
    timestamped copy for history. Non-gating — the universe CSV update is the
    real product; the Slack post is reporting on it.
    """
    from config import API_KEYS
    from reporting.universe_delta import (
        compute_universe_delta,
        load_universe_snapshot,
        load_positions_snapshot,
        post_universe_delta,
    )

    head_sha = baseline.get("head_sha") if baseline else None

    before_universe = load_universe_snapshot(commit_sha=head_sha, baseline=baseline) if head_sha else None
    after_universe = load_universe_snapshot(commit_sha=None)
    before_positions = load_positions_snapshot(commit_sha=head_sha, baseline=baseline) if head_sha else None
    after_positions = load_positions_snapshot(commit_sha=None)

    delisted_path = DATA_DIR / "delisted_tickers.csv"
    delisted_df = None
    if delisted_path.exists():
        import pandas as pd
        delisted_df = pd.read_csv(delisted_path, encoding="utf-8-sig")

    delta = compute_universe_delta(
        before_universe_df=before_universe,
        after_universe_df=after_universe,
        before_positions_df=before_positions,
        after_positions_df=after_positions,
        delisted_df=delisted_df,
        baseline_sha=head_sha,
        baseline_date=(baseline or {}).get("head_date"),
    )

    webhook = API_KEYS.get("SLACK_WEBHOOK_COVERAGE")
    result = post_universe_delta(webhook, delta)

    return {
        "posted": result["posted"],
        "reason": result["reason"],
        "added": len(delta["added"]),
        "removed": len(delta["removed"]),
        "modified": len({m["ticker"] for m in delta["modified"]}),
        "position_changes": len(delta["position_changes"]),
        "before_total": delta["before_stats"]["total"],
        "after_total": delta["after_stats"]["total"],
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
                logger.warning("No baseline SHA available — Slack delta will mark baseline as unavailable")
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
            steps["delisted_check"] = (
                f"{dc_result['flagged']} flagged of {dc_result['checked']} "
                f"(missing data: {dc_result['missing_data']})"
            )
            if dc_result["flagged"]:
                logger.warning(
                    "  %d ticker(s) flagged — review %s",
                    dc_result["flagged"], dc_result["report"],
                )
        else:
            steps["delisted_check"] = status

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

    # Post-step: Weekly universe delta -> Slack #coverage
    # Runs AFTER discovery/delisted_check/exports/sigma_export so the diff
    # captures every change in the universe state this run, and the totals
    # quoted in the Slack post match what downstream consumers will read.
    if dry_run:
        logger.info("[post] Universe delta Slack... SKIPPED (dry run)")
        steps["universe_delta_slack"] = "skipped (dry run)"
    else:
        logger.info("[post] Posting universe delta to Slack #coverage...")
        status, ud_result = run_step("universe_delta_slack", _step_universe_delta_slack, baseline)
        if ud_result:
            if ud_result["posted"]:
                steps["universe_delta_slack"] = (
                    f"posted (+{ud_result['added']}/-{ud_result['removed']}, "
                    f"{ud_result['modified']} modified, {ud_result['position_changes']} pos)"
                )
            else:
                steps["universe_delta_slack"] = f"skipped: {ud_result['reason']}"
        else:
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
