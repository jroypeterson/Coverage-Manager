"""Weekly build wrapper.

Thin orchestrator that calls weekly_universe.main() and weekly_report.main()
in sequence, gates the report side on validation results, sends the combined
Slack summary, and writes a parent `weekly-build` row to the audit log.

The actual step logic lives in `weekly_universe.py` and `weekly_report.py`.
This file exists to preserve the existing CLI surface (`cli.py weekly-build`)
and the Friday scheduled task (`run_weekly_coverage.bat`) without changes.

Also posts the workspace-standard health heartbeat to `#status-reports` per
HEALTH_REPORTING.md v1. The heartbeat is `try/finally`-guaranteed: even an
uncaught exception in the orchestration produces an `error` heartbeat before
the exception propagates.
"""

import json
import os
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import weekly_report
import weekly_universe
from config import API_KEYS, SCRIPT_DIR, TODAY
from logging_utils import get_logger
from pipeline_utils import collect_non_successes

logger = get_logger("weekly_build")

PROJECT_NAME = "Coverage Manager"
HEALTH_DIR = SCRIPT_DIR / ".health"
HEALTH_FALLBACK_PATH = HEALTH_DIR / "last_run.json"
UNIVERSE_CSV = SCRIPT_DIR / "data" / "coverage_universe_tickers.csv"
DELTA_JSON = SCRIPT_DIR / ".coverage" / "last_universe_delta.json"

# Abnormal-counts rule (HEALTH_REPORTING.md §4.2): a hard-broken universe is
# already caught by validation → `error`, but a *valid-yet-shrunken* CSV (e.g.
# rows silently dropped by a bad edit that still passes validation) would
# heartbeat `ok`. A week-over-week drop at/above this fraction downgrades to
# `partial` with a warning. Normal weeks move by a handful of names; a
# deliberate mass-prune tripping this once is fine (verify-and-move-on).
UNIVERSE_DROP_THRESHOLD_PCT = 5.0


def _universe_drop_warning(before_total, after_total,
                           threshold_pct=UNIVERSE_DROP_THRESHOLD_PCT):
    """Warning string when the universe shrank materially this run, else None.
    Pure — totals injected for tests. NOTE: `after_total == 0` is the WORST
    drop (universe wiped), not missing data — only `None` means missing
    (Codex round-2 High: a truthiness check made a zero-row wipe skip the
    very warning this exists for)."""
    if before_total is None or after_total is None:
        return None  # first run / missing stats — nothing to compare
    if before_total <= 0 or after_total >= before_total:
        return None
    drop_pct = (before_total - after_total) / before_total * 100.0
    if drop_pct >= threshold_pct:
        return (f"universe shrank {before_total:,} → {after_total:,} tickers "
                f"(-{drop_pct:.1f}% week-over-week) — verify deliberate")
    return None


def _load_universe_drop_warning():
    """Read this run's delta JSON (written by the weekly-universe delta step)
    and return the drop warning, or None. Best-effort: a missing/unreadable
    delta file (first run, or a run that failed before the delta step) skips
    the check rather than fabricating a warning."""
    try:
        data = json.loads(DELTA_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # The delta payload nests the stats under "delta" ({reason, today, delta:
    # {before_stats, after_stats, ...}}); accept a flat shape too so a future
    # writer refactor degrades to no-check instead of a crash. (Codex round-1
    # High: the flat-only read meant real drops would never have warned.)
    stats_root = data.get("delta") if isinstance(data.get("delta"), dict) else data
    before = (stats_root.get("before_stats") or {}).get("total")
    after = (stats_root.get("after_stats") or {}).get("total")
    return _universe_drop_warning(before, after)


def _gate_report(skip_performance, validation_passed, force):
    """Decide whether to run the report side. Returns (run_report, blocked_reason).

    `force` and `validation_passed` are kept as separate concepts: we never
    collapse them into a single boolean passed downstream.
    """
    if skip_performance:
        return False, "skipped"
    if not validation_passed and not force:
        return False, "blocked: validation failed"
    return True, None


def _next_friday_label(now=None):
    """Return a 'Fri YYYY-MM-DD' label for the next Friday relative to `now`."""
    now = now or datetime.now(timezone.utc)
    days_ahead = (4 - now.weekday()) % 7  # Monday=0 ... Friday=4
    if days_ahead == 0:
        days_ahead = 7
    target = now + timedelta(days=days_ahead)
    return f"Fri {target.strftime('%Y-%m-%d')}"


def _count_universe_rows():
    """Return the row count of the coverage universe CSV (excluding header)."""
    if not UNIVERSE_CSV.exists():
        return None
    try:
        with open(UNIVERSE_CSV, "r", encoding="utf-8") as f:
            return sum(1 for _ in f) - 1
    except Exception as e:
        logger.warning("Could not count universe rows for health payload: %s", e)
        return None


def _resolve_artifact_size(rel_path):
    """Return file size in bytes for an artifact path (relative to SCRIPT_DIR)."""
    p = Path(rel_path)
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    try:
        return p.stat().st_size if p.exists() else None
    except Exception:
        return None


def _build_health_payload(combined_steps, combined_artifacts, validation_passed,
                          start_dt, end_dt, exception=None):
    """Translate weekly_build state into a v1 health payload (dict).

    Status mapping (per HEALTH_REPORTING.md §4.2):
      - exception, OR validation_passed=False → "error"
        (universe broken means no usable downstream artifacts)
      - non_successes present but validation passed → "partial"
        (universe usable, some report-side steps blocked/failed)
      - clean run → "ok"
    """
    if exception is not None or not validation_passed:
        status = "error"
    else:
        non_successes = collect_non_successes(combined_steps)
        status = "partial" if non_successes else "ok"

    warnings = []
    drop_warning = _load_universe_drop_warning()
    if drop_warning:
        warnings.append(drop_warning)
        if status == "ok":
            status = "partial"

    cycle = f"{TODAY} weekly"
    attempt = os.environ.get("HEALTH_ATTEMPT", "1")

    counters = []
    n_tickers = _count_universe_rows()
    if n_tickers is not None:
        counters.append(f"{n_tickers} tickers in universe")
    n_ok = sum(1 for v in combined_steps.values() if v == "ok")
    n_total = len(combined_steps)
    if n_total:
        counters.append(f"{n_ok}/{n_total} steps ok")
    if exception is not None:
        counters.append(f"crashed: {type(exception).__name__}")

    artifact_entries = []
    for path_str in (combined_artifacts or []):
        size = _resolve_artifact_size(path_str)
        artifact_entries.append({"path": path_str, "bytes": size})

    errors = []
    if exception is not None:
        errors.append(f"Uncaught exception: {type(exception).__name__}: {exception}")
    for k, v in combined_steps.items():
        if isinstance(v, str) and (v.startswith("failed") or v.startswith("blocked")):
            errors.append(f"{k}: {v}")

    duration_seconds = max(0, int((end_dt - start_dt).total_seconds()))
    if duration_seconds < 60:
        duration_str = f"{duration_seconds}s"
    elif duration_seconds < 3600:
        duration_str = f"{duration_seconds // 60}m"
    else:
        h, rem = divmod(duration_seconds, 3600)
        duration_str = f"{h}h{rem // 60}m"

    return {
        "project": PROJECT_NAME,
        "status": status,
        "cycle": cycle,
        "attempt": attempt,
        "start_time_utc": start_dt.strftime("%Y-%m-%d %H:%M"),
        "end_time_utc": end_dt.strftime("%Y-%m-%d %H:%M"),
        "duration": duration_str,
        "next_expected": _next_friday_label(end_dt),
        "counters": counters,
        "artifacts": artifact_entries,
        "warnings": warnings,
        "errors": errors,
        "run_link": None,
        "tag": "health/v1",
    }


def _emit_health_heartbeat(payload, dry_run):
    """Send the v1 heartbeat to #status-reports unless this is a dry run.

    Reads `SLACK_WEBHOOK_STATUS_REPORTS` from env first, then `.env` via API_KEYS.
    Falls back to writing the payload locally on any post failure (§4.7).
    """
    if dry_run:
        logger.info("Health heartbeat... SKIPPED (dry run)")
        return {"posted": False, "reason": "dry run"}
    from reporting.slack import post_health_v1

    webhook = os.environ.get("SLACK_WEBHOOK_STATUS_REPORTS") or API_KEYS.get(
        "SLACK_WEBHOOK_STATUS_REPORTS"
    )
    return post_health_v1(webhook, payload, HEALTH_FALLBACK_PATH)


def main(skip_discovery=False, skip_performance=False, skip_email=False, dry_run=False, force=False):
    """Run the full weekly build pipeline (universe + report).

    Always emits a v1 health heartbeat to `#status-reports` (gated on
    `dry_run=False`) — including on uncaught exceptions, via try/finally.
    """
    start_dt = datetime.now(timezone.utc)
    combined_steps = OrderedDict()
    combined_artifacts = []
    validation_passed = False
    non_successes = []
    universe_result = None
    report_result = None
    blocked_reason = None
    exception_for_health = None

    try:
        logger.info("=" * 60)
        logger.info("Weekly Build -- %s", TODAY)
        logger.info("=" * 60)
        if dry_run:
            logger.info("DRY RUN -- no mutations will be made")

        # 1. Run universe side. Sub-orchestrators write their own audit rows.
        universe_result = weekly_universe.main(
            skip_discovery=skip_discovery,
            dry_run=dry_run,
            force=force,
            log_audit=not dry_run,
        )
        validation_passed = universe_result["validation_passed"]

        # 2. Decide whether to run report side — explicit, no overloaded booleans.
        run_report, blocked_reason = _gate_report(skip_performance, validation_passed, force)

        if run_report:
            report_result = weekly_report.main(
                skip_email=skip_email,
                dry_run=dry_run,
                log_audit=not dry_run,
            )
        else:
            logger.info("Report side: %s", blocked_reason)
            report_result = {
                "command": "weekly-report",
                "date": TODAY,
                "validation_passed": validation_passed,
                "steps": OrderedDict(
                    [
                        ("validate", blocked_reason),
                        ("archive", blocked_reason),
                        ("performance", blocked_reason),
                        ("email", blocked_reason),
                    ]
                ),
                "artifacts": [],
                "non_successes": [],
            }

        # 3. Merge into a combined steps dict in execution order.
        for k, v in universe_result["steps"].items():
            combined_steps[k] = v
        for k, v in report_result["steps"].items():
            # Avoid clobbering universe's "validate" with report's "validate".
            if k == "validate":
                continue
            combined_steps[k] = v

        combined_artifacts = list(universe_result["artifacts"]) + list(report_result["artifacts"])

        # 4. Slack summary (project-specific output to #stock-price-alerts)
        slack_url = API_KEYS.get("SLACK_WEBHOOK_URL")
        if not slack_url:
            logger.info("Slack... SKIPPED (no SLACK_WEBHOOK_URL in .env)")
            slack_status = "skipped"
        elif dry_run:
            logger.info("Slack... SKIPPED (dry run)")
            slack_status = "skipped (dry run)"
        else:
            from reporting.slack import format_weekly_build_summary, send_slack_notification

            message = format_weekly_build_summary(TODAY, combined_steps)
            ok = send_slack_notification(slack_url, message)
            slack_status = "ok" if ok else "failed: webhook error"
        combined_steps["slack"] = slack_status

        # 5. Summary log
        logger.info("")
        logger.info("-- Weekly Build Summary --")
        logger.info("%-20s %s", "Step", "Status")
        logger.info("-" * 50)
        for step_name, status in combined_steps.items():
            logger.info("%-20s %s", step_name, status)

        # Non-success covers both "failed:" (exception) and "blocked:" (gated).
        # A blocked report run is operationally non-successful — the report didn't
        # ship, even though no exception was raised — and must surface as such in
        # logs and audit trails.
        non_successes = collect_non_successes(combined_steps)
        if non_successes:
            logger.warning(
                "Weekly build completed with %d non-success(es): %s",
                len(non_successes),
                non_successes,
            )
        else:
            logger.info("Weekly build completed successfully")

        # 6. Parent audit row — preserves historical continuity for queries on command="weekly-build".
        if not dry_run:
            try:
                from audit import log_run

                notes_parts = []
                if skip_discovery:
                    notes_parts.append("discovery skipped")
                if skip_performance:
                    notes_parts.append("performance skipped")
                if skip_email:
                    notes_parts.append("email skipped")
                if blocked_reason and not skip_performance:
                    notes_parts.append(blocked_reason)
                log_run("weekly-build", combined_steps, notes="; ".join(notes_parts))
            except Exception as e:
                logger.warning("Failed to write parent audit log: %s", e)

        return {
            "command": "weekly-build",
            "date": TODAY,
            "validation_passed": validation_passed,
            "steps": combined_steps,
            "artifacts": combined_artifacts,
            "non_successes": non_successes,
            "universe": universe_result,
            "report": report_result,
        }
    except Exception as e:
        exception_for_health = e
        raise
    finally:
        # Always emit the v1 health heartbeat. Errors here must not mask the
        # original exception (if any), so the heartbeat post is itself wrapped.
        try:
            end_dt = datetime.now(timezone.utc)
            payload = _build_health_payload(
                combined_steps,
                combined_artifacts,
                validation_passed,
                start_dt,
                end_dt,
                exception=exception_for_health,
            )
            _emit_health_heartbeat(payload, dry_run)
        except Exception as health_err:
            logger.error("Health heartbeat reporting itself failed: %s", health_err)
