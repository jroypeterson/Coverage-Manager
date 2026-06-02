"""Slack webhook integration for posting pipeline notifications.

Two surfaces:
  - `send_slack_notification` / `format_weekly_build_summary` / `format_performance_summary`
    post project-specific output to `#stock-price-alerts` (the existing channel).
  - `post_health_v1` / `format_health_v1_message` post the standardized health
    heartbeat to `#status-reports` per the workspace HEALTH_REPORTING.md v1 spec.
    The heartbeat is *additional* to project-specific output, not a replacement.
"""

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

from logging_utils import get_logger

logger = get_logger("reporting.slack")

# Network-blip retry. A Windows scheduled task that missed its trigger (laptop
# asleep) and catches up on wake via StartWhenAvailable can fire the instant the
# machine wakes — before WiFi/DNS is up — so the first Slack POST dies with
# `getaddrinfo failed` / URLError. Retry-with-backoff rides through it; DNS-on-
# wake typically clears within 10-30s. See workspace CONVENTIONS §3 and the
# scheduled_jobs_monitor reference impl. (2026-06-01.)
_RETRY_BACKOFF = (5, 15, 30)  # seconds to wait BEFORE retry attempts 2..N


def _retry_sleep(seconds):
    # Skip the wall-clock wait under pytest (which sets PYTEST_CURRENT_TEST) so
    # the network-error tests don't sleep through the full backoff.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    time.sleep(seconds)


def urlopen_with_retry(req, *, timeout=15, attempts=4, label="slack post"):
    """``urllib.request.urlopen`` with backoff retry on transient network errors.

    Re-raises the last ``URLError`` if every attempt fails, so existing callers
    keep their fallback/return-False behavior on a genuine outage — this only
    adds resilience to momentary blips (notably DNS-not-ready on wake). Calls
    ``urllib.request.urlopen`` by name so test monkeypatches still intercept it.
    """
    last = None
    for i in range(attempts):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.URLError as e:
            last = e
            if i < attempts - 1:
                delay = _RETRY_BACKOFF[min(i, len(_RETRY_BACKOFF) - 1)]
                logger.warning("%s attempt %d/%d failed (%s); retrying in %ds",
                               label, i + 1, attempts, e, delay)
                _retry_sleep(delay)
    raise last

SLACK_CHANNEL = "#stock-price-alerts"
HEALTH_CHANNEL = "#status-reports"
HEALTH_TAG = "health/v1"

_STATUS_ICON = {
    "ok": ":white_check_mark:",
    "partial": ":warning:",
    "error": ":x:",
}


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
        with urlopen_with_retry(req, timeout=15, label="Slack notification") as resp:
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
        elif isinstance(status, str) and status.startswith("blocked"):
            # Distinct from a deliberate skip — a blocked step is non-success.
            icon = ":no_entry:"
        elif status == "skipped" or (isinstance(status, str) and status.startswith("skipped")):
            icon = ":fast_forward:"
        else:
            icon = ":large_blue_circle:"
        lines.append(f"{icon}  {step_name}: {status}")

    non_success = [
        k
        for k, v in steps.items()
        if isinstance(v, str) and (v.startswith("failed") or v.startswith("blocked"))
    ]
    lines.append("")
    if non_success:
        lines.append(f":warning: {len(non_success)} step(s) non-success: {', '.join(non_success)}")
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


# ── Health heartbeat (v1 spec, workspace-wide contract) ──────────────────────
# See HEALTH_REPORTING.md at the workspace root for the full contract. Helpers
# below implement the v1 message format and the §4.7 fallback (write payload
# to a local file when the Slack POST fails).


def _format_size(n_bytes):
    """Return a short human-readable byte size (e.g. '412 KB', '1.4 MB')."""
    if n_bytes is None:
        return None
    n = float(n_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1024


def format_health_v1_message(payload):
    """Render a v1 health payload as a Slack mrkdwn message body.

    Required payload keys: project, status, cycle, attempt, start_time_utc,
    end_time_utc, next_expected, counters (list of strings).
    Optional: artifacts (list of dicts with path / rows / bytes), warnings (list),
    errors (list of strings), run_link, tag.

    The format mirrors HEALTH_REPORTING.md §7. Status drives the emoji.
    """
    status = payload["status"]
    if status not in _STATUS_ICON:
        raise ValueError(f"status must be one of {list(_STATUS_ICON)}, got {status!r}")
    icon = _STATUS_ICON[status]
    tag = payload.get("tag", HEALTH_TAG)

    start = payload["start_time_utc"]
    end = payload["end_time_utc"]
    # If end falls on the same calendar day as start, strip the redundant
    # "YYYY-MM-DD " prefix off the end time. Matches HEALTH_REPORTING.md §7.
    if " " in start and " " in end:
        start_date_prefix = start.split(" ", 1)[0] + " "
        if end.startswith(start_date_prefix):
            end = end[len(start_date_prefix):]
    duration = payload.get("duration")
    duration_str = f" ({duration})" if duration else ""

    lines = [
        f"{icon} *{payload['project']} — {status}*  ·  {tag}",
        f"cycle: {payload['cycle']}  ·  attempt: {payload['attempt']}",
        f"{start} → {end} UTC{duration_str}",
        f"next expected: {payload['next_expected']}",
        "",
    ]

    counters = payload.get("counters") or []
    if counters:
        lines.append(f"*Counters:* {' · '.join(counters)}")

    artifacts = payload.get("artifacts") or []
    if artifacts:
        lines.append("*Artifacts:*")
        for a in artifacts:
            parts = [a["path"]]
            extras = []
            if a.get("rows") is not None:
                extras.append(f"{a['rows']} rows")
            size_str = _format_size(a.get("bytes")) if a.get("bytes") is not None else None
            if size_str:
                extras.append(size_str)
            if extras:
                parts.append(f"({', '.join(extras)})")
            lines.append(f"  • {' '.join(parts)}")

    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("*Warnings:*")
        for w in warnings:
            lines.append(f"  • {w}")

    errors = payload.get("errors") or []
    if errors and status != "ok":
        lines.append("")
        lines.append("*Error:*")
        lines.append("```")
        for e in errors:
            lines.append(e)
        lines.append("```")

    run_link = payload.get("run_link")
    if run_link:
        lines.append("")
        lines.append(f"<{run_link}|run log>")

    return "\n".join(lines)


_SLACK_SECTION_TEXT_MAX = 3000  # Slack hard limit on section.text.text length


def _build_health_v1_blocks(message):
    """Wrap the rendered mrkdwn message in Slack Block Kit blocks.

    Plain `{"text": ...}` payloads render correctly only on webhooks whose
    Slack-app config has mrkdwn parsing enabled. The earnings-agent app
    webhook used for #status-reports treats `text` as literal, so emoji
    shortcodes and `*bold*` show as raw characters. Block Kit `section`
    blocks with `type: "mrkdwn"` always render formatting and emoji
    shortcodes regardless of app config — so we send blocks for the rich
    payload and keep `text` only as a fallback for notifications and old
    clients that can't render blocks.

    A single section's text is capped at 3000 chars. If we exceed it,
    split into multiple section blocks at line boundaries.
    """
    blocks = []
    if len(message) <= _SLACK_SECTION_TEXT_MAX:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": message}})
        return blocks

    chunks = []
    current = []
    current_len = 0
    for line in message.splitlines(keepends=True):
        if current_len + len(line) > _SLACK_SECTION_TEXT_MAX and current:
            chunks.append("".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        chunks.append("".join(current))

    for chunk in chunks:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    return blocks


def post_health_v1(webhook_url, payload, fallback_path):
    """Post a v1 health heartbeat to Slack with §4.7 fallback semantics.

    Sends a Block Kit payload so mrkdwn (`*bold*`, `:emoji:`) renders
    consistently regardless of the destination webhook's mrkdwn-parsing
    config. Includes a plain `text` field as a fallback for notification
    previews and clients that can't render blocks.

    On any non-success path (no webhook configured, network error, non-200
    response), this function:
      - writes the full payload as JSON to ``fallback_path``
      - logs the full payload at WARNING so the scheduler stdout/log captures it
      - returns a dict ``{"posted": False, "reason": "..."}``

    On success, returns ``{"posted": True, "reason": None}``.

    The caller is responsible for any "fail loudly" behavior (non-zero exit,
    raised exception). This function does not raise — it returns a status dict
    so the caller can decide whether a Slack-only failure should fail the run.

    Args:
        webhook_url: Slack incoming webhook URL for #status-reports. If falsy,
            the post is skipped and the fallback file is written.
        payload: dict matching the v1 contract (see format_health_v1_message).
        fallback_path: Path-like target for the JSON fallback file.
    """
    fallback_path = Path(fallback_path)
    message = format_health_v1_message(payload)
    body = {
        "blocks": _build_health_v1_blocks(message),
        "text": message,  # fallback for notifications + old clients
    }

    def _write_fallback(reason):
        try:
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            fallback_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as fe:
            logger.error("Health fallback file write failed: %s", fe)
        logger.warning("Health post failed (%s); payload follows:\n%s", reason, message)

    if not webhook_url:
        _write_fallback("no SLACK_WEBHOOK_STATUS_REPORTS configured")
        return {"posted": False, "reason": "no webhook configured"}

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen_with_retry(req, timeout=15, label="Health heartbeat") as resp:
            if resp.status == 200:
                logger.info("Health heartbeat posted to %s", HEALTH_CHANNEL)
                return {"posted": True, "reason": None}
            reason = f"slack returned status {resp.status}"
            _write_fallback(reason)
            return {"posted": False, "reason": reason}
    except urllib.error.URLError as e:
        reason = f"network error: {e}"
        _write_fallback(reason)
        return {"posted": False, "reason": reason}
    except Exception as e:
        reason = f"unexpected error: {e}"
        _write_fallback(reason)
        return {"posted": False, "reason": reason}
