"""Reporting-side weekly orchestrator.

Owns the performance reporting half of the weekly pipeline:
validate (read-only) -> archive -> performance -> email.

Validation here is informational only — gating is the wrapper's job
(see weekly_build.py). Direct CLI invocation will surface validation
status in the returned result but will not refuse to generate reports.
"""

import glob
import os

from config import API_KEYS, OLD_REPORTS_DIR, REPORTS_DIR, TODAY
from logging_utils import get_logger
from pipeline_utils import collect_non_successes, run_step

logger = get_logger("weekly_report")

REPORT_ARCHIVE_PATTERNS = [
    "coverage_performance_*.xlsx",
    "coverage_performance_*.html",
    "coverage_consolidated_*.html",
    "coverage_biopharma_*.html",
    "coverage_hc_svcs_medtech_*.html",
    "coverage_following_non_hc_*.html",
    "coverage_pa_other_*.html",  # legacy — schema v1 era
    "coverage_other_*.html",     # legacy — pre-rename "Other" segment files
    "coverage_sp500_non_hc_*.html",
    "coverage_sp500_*.html",
    "coverage_movers_*.html",
    "coverage_movers_*.md",
]


# ── Steps ────────────────────────────────────────────────────────────────────


def _step_validate_readonly():
    """Run CSV validation as a read-only informational check.

    The result is reported in the returned dict so the wrapper (or a human
    reading the logs) can see whether the universe is in good shape, but this
    step never raises and never blocks downstream steps. Gating decisions live
    in the weekly_build wrapper.
    """
    import pandas as pd

    from config import CSV_PATH
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


def _step_archive_reports():
    """Archive prior dated performance reports."""
    from reporting.email import archive_files

    return archive_files(REPORTS_DIR, OLD_REPORTS_DIR, TODAY, REPORT_ARCHIVE_PATTERNS)


def _step_performance():
    """Generate the Excel and HTML performance reports.

    Runs with skip_email=True so the orchestrator owns delivery and can
    bundle the movers HTML into a single email.
    """
    from reporting import generate

    generate.main(sample_mode=False, skip_email=True)
    return {}


def _step_movers():
    """Generate the movers report and post a Slack summary."""
    from movers_runner import run as run_movers

    result = run_movers(snapshot_date=TODAY, skip_news=False, skip_slack=False)
    if result.get("error"):
        return {"status": "skipped", "reason": result["error"]}
    return {
        "flagged": result["count"],
        "html": result["html_path"],
        "slack_posted": result["slack_posted"],
    }


def _step_email():
    """Send performance reports via email. Includes the movers HTML when present."""
    from reporting.email import send_email_report

    gmail_addr = API_KEYS.get("GMAIL_ADDRESS")
    gmail_pass = API_KEYS.get("GMAIL_APP_PASSWORD")
    if not gmail_addr or not gmail_pass:
        return {"status": "skipped", "reason": "no credentials"}

    # Include both the existing coverage_*.html files and the movers HTML;
    # movers is matched by the same coverage_*_{TODAY}.html glob now.
    html_files = glob.glob(os.path.join(REPORTS_DIR, f"coverage_*_{TODAY}.html"))
    if not html_files:
        return {"status": "skipped", "reason": "no HTML files found"}

    send_email_report(gmail_addr, gmail_pass, html_files, TODAY)
    return {"sent": len(html_files)}


# ── Result helper ────────────────────────────────────────────────────────────


def _make_result(steps, validation_passed):
    """Build the standardized orchestrator result shape."""
    return {
        "command": "weekly-report",
        "date": TODAY,
        "validation_passed": validation_passed,
        "steps": steps,
        "artifacts": [],
        "non_successes": collect_non_successes(steps),
    }


# ── Main ─────────────────────────────────────────────────────────────────────


def main(skip_email=False, dry_run=False, log_audit=True):
    """Run the reporting-side weekly pipeline.

    Args:
        skip_email: Skip the email send step.
        dry_run: Validate and report only — no mutations or external sends.
        log_audit: Whether to write a row to run_log.csv. The wrapper passes
            this through; direct CLI invocation defaults to True.

    Returns the standardized result dict (see `_make_result`). Note: this
    orchestrator deliberately does NOT take a `force` parameter — gating on
    validation failures lives in the weekly_build wrapper, not here.
    """
    logger.info("=" * 60)
    logger.info("Weekly Report -- %s", TODAY)
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN -- no mutations will be made")

    steps = {}
    validation_passed = False

    # Step 1: Read-only validation (informational)
    logger.info("[1/4] Validating coverage universe (read-only)...")
    status, validation_result = run_step("validate", _step_validate_readonly)
    steps["validate"] = status
    if validation_result:
        logger.info(
            "  %d rows, %d errors, %d warnings",
            validation_result["rows"],
            len(validation_result["errors"]),
            len(validation_result["warnings"]),
        )
        validation_passed = validation_result["passed"]

    # Step 2: Archive prior reports
    logger.info("[2/4] Archiving prior performance reports...")
    if dry_run:
        steps["archive"] = "skipped (dry run)"
    else:
        status, _ = run_step("archive", _step_archive_reports)
        steps["archive"] = status

    # Step 3: Performance reports
    if dry_run:
        logger.info("[3/5] Performance reports... SKIPPED (dry run)")
        steps["performance"] = "skipped (dry run)"
    else:
        logger.info("[3/5] Generating performance reports...")
        status, _ = run_step("performance", _step_performance)
        steps["performance"] = status

    # Step 4: Movers report (flag extreme weekly movers + Slack summary)
    if dry_run:
        logger.info("[4/5] Movers report... SKIPPED (dry run)")
        steps["movers"] = "skipped (dry run)"
    elif steps.get("performance", "").startswith("failed"):
        logger.info("[4/5] Movers report... SKIPPED (performance step failed)")
        steps["movers"] = "skipped: performance failed"
    else:
        logger.info("[4/5] Generating movers report...")
        status, _ = run_step("movers", _step_movers)
        steps["movers"] = status

    # Step 5: Email
    # Reporting transport flag — when EMAIL_ENABLED=False the email path is a
    # no-op regardless of caller. Slack (#coverage) carries the weekly update
    # for now; flip the flag in config.py to re-enable email.
    import config
    if skip_email:
        logger.info("[5/5] Email... SKIPPED")
        steps["email"] = "skipped"
    elif dry_run:
        logger.info("[5/5] Email... SKIPPED (dry run)")
        steps["email"] = "skipped (dry run)"
    elif not config.EMAIL_ENABLED:
        logger.info("[5/5] Email... SKIPPED (EMAIL_ENABLED=False)")
        steps["email"] = "skipped: EMAIL_ENABLED=False"
    else:
        logger.info("[5/5] Sending email...")
        status, _ = run_step("email", _step_email)
        steps["email"] = status

    # Summary
    logger.info("")
    logger.info("-- Weekly Report Summary --")
    for step_name, status in steps.items():
        logger.info("%-20s %s", step_name, status)

    non_successes = collect_non_successes(steps)
    if non_successes:
        logger.warning(
            "Weekly report completed with %d non-success(es): %s",
            len(non_successes),
            non_successes,
        )
    else:
        logger.info("Weekly report completed successfully")

    # Audit log
    if log_audit and not dry_run:
        try:
            from audit import log_run

            notes = "email skipped" if skip_email else ""
            log_run("weekly-report", steps, notes=notes)
        except Exception as e:
            logger.warning("Failed to write audit log: %s", e)

    return _make_result(steps, validation_passed)
