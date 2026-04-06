"""Slack webhook integration for posting pipeline notifications."""

import json
import urllib.request
import urllib.error

from logging_utils import get_logger

logger = get_logger("reporting.slack")

SLACK_CHANNEL = "#all-jp-personal-hub"


def send_slack_notification(webhook_url, message):
    """Post a message to Slack via incoming webhook.

    Returns True on success, False on failure.
    """
    payload = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                logger.info("Slack notification sent to %s", SLACK_CHANNEL)
                return True
            logger.warning("Slack returned status %s", resp.status)
            return False
    except urllib.error.URLError as e:
        logger.warning("Slack notification failed: %s", e)
        return False


def format_weekly_build_summary(today_str, steps):
    """Format a weekly build summary for Slack.

    Args:
        today_str: Date string (YYYY-MM-DD).
        steps: Dict of step_name -> status from weekly build.
    """
    lines = [f"*Weekly Coverage Build — {today_str}*", ""]
    for step_name, status in steps.items():
        if status == "ok":
            icon = ":white_check_mark:"
        elif isinstance(status, str) and status.startswith("failed"):
            icon = ":x:"
        elif status in ("skipped", "blocked"):
            icon = ":fast_forward:"
        else:
            icon = ":large_blue_circle:"
        lines.append(f"{icon}  {step_name}: {status}")

    failed = [k for k, v in steps.items() if isinstance(v, str) and v.startswith("failed")]
    lines.append("")
    if failed:
        lines.append(f":warning: {len(failed)} step(s) failed: {', '.join(failed)}")
    else:
        lines.append(":rocket: All steps completed successfully")

    return "\n".join(lines)


def format_performance_summary(today_str, ticker_count, fund_count):
    """Format a performance report summary for Slack.

    Args:
        today_str: Date string (YYYY-MM-DD).
        ticker_count: Total tickers processed.
        fund_count: Tickers with fundamental data.
    """
    lines = [
        f"*Coverage Performance Reports — {today_str}*",
        "",
        f"Tickers processed: {ticker_count}",
        f"Fundamentals loaded: {fund_count}/{ticker_count}",
        "",
        "Reports emailed and saved to Dropbox.",
    ]
    return "\n".join(lines)
