"""Weekly build orchestrator.

Runs the full weekly coverage workflow as a series of independently-failable steps:
validate -> archive -> enrich -> performance -> email -> slack.

Each step reports its own status. The full pipeline completes even if individual
steps fail (except for fatal errors in the data pipeline).
"""

import os
import sys
from datetime import datetime

from config import CSV_PATH, REPORTS_DIR, OLD_REPORTS_DIR, API_KEYS, TODAY
from logging_utils import get_logger

logger = get_logger("weekly_build")


def run_step(name, fn, *args, **kwargs):
    """Run a pipeline step, catching and logging failures.

    Returns (status_string, result_or_none).
    """
    try:
        result = fn(*args, **kwargs)
        return "ok", result
    except Exception as e:
        logger.warning("Step '%s' failed: %s", name, e)
        return f"failed: {e}", None


def _step_validate():
    """Run CSV validation."""
    from universe import validation
    import pandas as pd

    df = pd.read_csv(CSV_PATH)
    errors, warnings = validation.run_all_validations(df)

    for w in warnings:
        logger.info("  WARN: %s", w)
    for e in errors:
        logger.warning("  ERROR: %s", e)

    return {
        "rows": len(df),
        "errors": len(errors),
        "warnings": len(warnings),
    }


def _step_archive():
    """Archive old dated reports."""
    from reporting.email import archive_old_files

    os.makedirs(OLD_REPORTS_DIR, exist_ok=True)

    # Archive performance reports
    archive_old_files(REPORTS_DIR, OLD_REPORTS_DIR, TODAY)

    # Also archive old weekly additions and company backgrounds
    import glob
    import shutil

    moved = 0
    for pattern in [
        os.path.join(REPORTS_DIR, "weekly_coverage_universe_additions_*.md"),
        os.path.join(REPORTS_DIR, "company_backgrounds_*.md"),
    ]:
        for f in glob.glob(pattern):
            if TODAY in os.path.basename(f):
                continue
            dest = os.path.join(OLD_REPORTS_DIR, os.path.basename(f))
            shutil.move(f, dest)
            moved += 1

    return {"moved": moved}


def _step_enrich():
    """Run enrichment on any new/unenriched tickers."""
    from universe import enrich

    enrich.main()
    return {}


def _step_performance():
    """Generate performance reports."""
    from reporting import generate

    generate.main(sample_mode=False)
    return {}


def _step_email():
    """Send performance reports via email."""
    import glob
    from reporting.email import send_email_report

    gmail_addr = API_KEYS.get("GMAIL_ADDRESS")
    gmail_pass = API_KEYS.get("GMAIL_APP_PASSWORD")
    if not gmail_addr or not gmail_pass:
        return {"status": "skipped", "reason": "no credentials"}

    html_files = glob.glob(os.path.join(REPORTS_DIR, f"coverage_*_{TODAY}.html"))
    if not html_files:
        return {"status": "skipped", "reason": "no HTML files found"}

    send_email_report(gmail_addr, gmail_pass, html_files, TODAY)
    return {"sent": len(html_files)}


def main(skip_discovery=False, skip_performance=False, skip_email=False, dry_run=False, force=False):
    """Run the full weekly build pipeline."""
    logger.info("=" * 60)
    logger.info("Weekly Build -- %s", TODAY)
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN -- no mutations will be made")

    steps = {}

    # Step 1: Validate
    logger.info("[1/6] Validating coverage universe...")
    status, result = run_step("validate", _step_validate)
    steps["validate"] = status
    validation_ok = True
    if result:
        logger.info("  %d rows, %d errors, %d warnings", result["rows"], result["errors"], result["warnings"])
        if result["errors"] > 0:
            validation_ok = False
            logger.warning("  Validation errors found — downstream steps will be blocked")

    # Step 2: Archive old reports
    logger.info("[2/6] Archiving old reports...")
    if not dry_run:
        status, result = run_step("archive", _step_archive)
        steps["archive"] = status
    else:
        steps["archive"] = "skipped (dry run)"

    # Step 3: Discovery
    if skip_discovery:
        logger.info("[3/6] Discovery... SKIPPED")
        steps["discovery"] = "skipped"
    else:
        logger.info("[3/6] Discovery...")

        def _step_discovery():
            from discovery.candidates import (
                write_discovery_input, validate_discovery_output,
                stage_candidates, commit_staged_candidates,
            )
            from config import DATA_DIR

            # Write input JSON for Claude
            input_path = write_discovery_input()
            logger.info("  Discovery input written to %s", input_path)

            # Check for output JSON (populated externally by Claude)
            output_path = DATA_DIR / f"discovery_output_{TODAY}.json"
            if not output_path.exists():
                logger.info("  No discovery output found at %s", output_path)
                logger.info("  Run the weekly coverage prompt in Claude, save output as:")
                logger.info("    %s", output_path)
                return {"status": "awaiting output", "input_written": str(input_path)}

            # Validate candidates
            valid, errors = validate_discovery_output(output_path)
            for e in errors:
                logger.warning("  Validation: %s", e)
            logger.info("  %d valid candidates, %d validation errors", len(valid), len(errors))

            if not valid:
                return {"status": "no valid candidates", "errors": len(errors)}

            # Stage candidates
            staging_path = stage_candidates(valid)
            logger.info("  Staged to %s", staging_path)
            logger.info("  Review the staging file, set approved=true for candidates to add")

            if not dry_run:
                # Auto-commit pre-approved candidates (approved=true in output JSON)
                pre_approved = [c for c in valid if c.get("approved")]
                if pre_approved:
                    # Write a separate commit file so the full staging file is preserved
                    commit_path = DATA_DIR / f"approved_candidates_{TODAY}.csv"
                    stage_candidates(pre_approved, commit_path)
                    added = commit_staged_candidates(commit_path)
                    logger.info("  Committed %d pre-approved candidates", added)
                    return {"status": "committed", "added": added, "total_valid": len(valid)}

            return {"status": "staged", "valid": len(valid), "staging_path": str(staging_path)}

        status, result = run_step("discovery", _step_discovery)
        steps["discovery"] = status
        if result:
            logger.info("  Discovery: %s", result.get("status", "unknown"))

    # Block downstream steps if validation found hard errors (unless --force)
    blocked = not validation_ok and not force
    if blocked:
        logger.warning("Blocking downstream steps due to validation errors (use --force to override)")

    # Step 4: Performance reports
    if blocked:
        logger.info("[4/6] Performance reports... BLOCKED (validation errors)")
        steps["performance"] = "blocked"
    elif skip_performance:
        logger.info("[4/6] Performance reports... SKIPPED")
        steps["performance"] = "skipped"
    elif dry_run:
        logger.info("[4/6] Performance reports... SKIPPED (dry run)")
        steps["performance"] = "skipped (dry run)"
    else:
        logger.info("[4/6] Generating performance reports...")
        status, _ = run_step("performance", _step_performance)
        steps["performance"] = status

    # Step 5: Email
    if blocked:
        logger.info("[5/6] Email... BLOCKED (validation errors)")
        steps["email"] = "blocked"
    elif skip_email:
        logger.info("[5/6] Email... SKIPPED")
        steps["email"] = "skipped"
    elif dry_run:
        logger.info("[5/6] Email... SKIPPED (dry run)")
        steps["email"] = "skipped (dry run)"
    else:
        logger.info("[5/6] Sending email...")
        status, _ = run_step("email", _step_email)
        steps["email"] = status

    # Step 6: Slack notification
    slack_url = API_KEYS.get("SLACK_WEBHOOK_URL")
    if not slack_url:
        logger.info("[6/6] Slack... SKIPPED (no SLACK_WEBHOOK_URL in .env)")
        steps["slack"] = "skipped"
    elif dry_run:
        logger.info("[6/6] Slack... SKIPPED (dry run)")
        steps["slack"] = "skipped (dry run)"
    else:
        logger.info("[6/6] Sending Slack notification...")
        from reporting.slack import send_slack_notification, format_weekly_build_summary
        message = format_weekly_build_summary(TODAY, steps)
        ok = send_slack_notification(slack_url, message)
        steps["slack"] = "ok" if ok else "failed: webhook error"

    # Summary
    logger.info("")
    logger.info("-- Weekly Build Summary --")
    logger.info("%-20s %s", "Step", "Status")
    logger.info("-" * 50)
    for step_name, status in steps.items():
        logger.info("%-20s %s", step_name, status)

    failed = [k for k, v in steps.items() if isinstance(v, str) and v.startswith("failed")]
    if failed:
        logger.warning("Weekly build completed with %d failure(s): %s", len(failed), failed)
    else:
        logger.info("Weekly build completed successfully")

    # Log the run to audit trail
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
            log_run("weekly-build", steps, notes="; ".join(notes_parts))
        except Exception as e:
            logger.warning("Failed to write audit log: %s", e)

    return steps
