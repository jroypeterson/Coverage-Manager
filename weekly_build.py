"""Weekly build wrapper.

Thin orchestrator that calls weekly_universe.main() and weekly_report.main()
in sequence, gates the report side on validation results, sends the combined
Slack summary, and writes a parent `weekly-build` row to the audit log.

The actual step logic lives in `weekly_universe.py` and `weekly_report.py`.
This file exists to preserve the existing CLI surface (`cli.py weekly-build`)
and the Friday scheduled task (`run_weekly_coverage.bat`) without changes.
"""

from collections import OrderedDict

import weekly_report
import weekly_universe
from config import API_KEYS, TODAY
from logging_utils import get_logger
from pipeline_utils import collect_non_successes

logger = get_logger("weekly_build")


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


def main(skip_discovery=False, skip_performance=False, skip_email=False, dry_run=False, force=False):
    """Run the full weekly build pipeline (universe + report)."""
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
    combined_steps = OrderedDict()
    for k, v in universe_result["steps"].items():
        combined_steps[k] = v
    for k, v in report_result["steps"].items():
        # Avoid clobbering universe's "validate" with report's "validate".
        if k == "validate":
            continue
        combined_steps[k] = v

    combined_artifacts = list(universe_result["artifacts"]) + list(report_result["artifacts"])

    # 4. Slack summary
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
