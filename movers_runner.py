"""Movers report orchestration — the connective tissue between the
performance snapshot, the movers module, and the delivery surfaces (file,
email, Slack).

Used by both ``cli.py movers`` (ad-hoc invocation) and ``weekly_report.py``
(automated run after the performance step).
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from config import (
    API_KEYS,
    CACHE_DIR,
    REPORTS_DIR,
    TODAY,
    MOVERS_ABS_THRESHOLD_PCT,
    MOVERS_ZSCORE_THRESHOLD,
    MOVERS_MIN_PEER_COUNT,
    MOVERS_MAX_FLAGGED,
    MOVERS_LLM_MODEL,
)
from logging_utils import get_logger
from reporting import movers
from reporting.slack import send_slack_notification

logger = get_logger("movers_runner")


def _load_perf_snapshot(snapshot_date: str) -> pd.DataFrame | None:
    """Load the perf snapshot pickle written by reporting/generate.py.

    Returns None if the snapshot doesn't exist — caller decides whether to
    error or fall through to a price-only fallback.
    """
    path = CACHE_DIR / "perf" / f"perf_df_{snapshot_date}.pkl"
    if not path.exists():
        logger.warning("No perf snapshot at %s", path)
        return None
    try:
        df = pd.read_pickle(path)
        logger.info("Loaded perf snapshot: %s (%d rows)", path, len(df))
        return df
    except Exception as e:
        logger.warning("Failed to load perf snapshot %s: %s", path, e)
        return None


def run(
    snapshot_date: str | None = None,
    skip_news: bool = False,
    skip_slack: bool = False,
) -> dict:
    """Generate the movers report and write artifacts to ``reports/``.

    Args:
        snapshot_date: Date string (YYYY-MM-DD) for the perf snapshot to
            load. Defaults to TODAY.
        skip_news: Skip Finnhub news + Anthropic summary enrichment.
        skip_slack: Skip the Slack post even if a webhook is configured.

    Returns dict with: count, html_path, md_path, slack_posted, error.
    """
    snap_date = snapshot_date or TODAY
    result = {
        "count": 0,
        "html_path": None,
        "md_path": None,
        "slack_posted": False,
        "error": None,
    }

    df = _load_perf_snapshot(snap_date)
    if df is None or df.empty:
        msg = f"No perf snapshot for {snap_date}; run `cli.py performance` first."
        logger.warning(msg)
        result["error"] = msg
        return result

    finnhub_key = "" if skip_news else (API_KEYS.get("FINNHUB_API_KEY") or "")
    anthropic_key = "" if skip_news else (API_KEYS.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", ""))
    if not skip_news and not anthropic_key:
        logger.info("ANTHROPIC_API_KEY not set; movers report will skip LLM 'why' summaries")

    bundle = movers.run(
        perf_df=df,
        today=snap_date,
        finnhub_key=finnhub_key,
        anthropic_key=anthropic_key,
        abs_threshold_pct=MOVERS_ABS_THRESHOLD_PCT,
        z_threshold=MOVERS_ZSCORE_THRESHOLD,
        min_peer_count=MOVERS_MIN_PEER_COUNT,
        max_flagged=MOVERS_MAX_FLAGGED,
        llm_model=MOVERS_LLM_MODEL,
    )
    result["count"] = bundle["count"]

    html_path = Path(REPORTS_DIR) / f"coverage_movers_{snap_date}.html"
    md_path = Path(REPORTS_DIR) / f"coverage_movers_{snap_date}.md"
    html_path.write_text(bundle["html"], encoding="utf-8")
    md_path.write_text(bundle["md"], encoding="utf-8")
    result["html_path"] = str(html_path)
    result["md_path"] = str(md_path)
    logger.info("Wrote %s and %s", html_path, md_path)

    # Slack — non-fatal if it fails.
    if not skip_slack:
        webhook = API_KEYS.get("SLACK_WEBHOOK_URL") or os.environ.get("SLACK_WEBHOOK_URL", "")
        if webhook:
            try:
                ok = send_slack_notification(webhook, bundle["slack"])
                result["slack_posted"] = bool(ok)
            except Exception as e:
                logger.warning("Slack post failed: %s", e)
        else:
            logger.info("Skipping Slack post — no SLACK_WEBHOOK_URL set")

    return result


def run_movers_cli(
    snapshot_date: str | None = None,
    skip_news: bool = False,
    skip_slack: bool = False,
) -> int:
    """CLI entry point. Returns exit code (0 success, 1 failure)."""
    res = run(snapshot_date=snapshot_date, skip_news=skip_news, skip_slack=skip_slack)
    if res["error"]:
        print(f"Error: {res['error']}")
        return 1
    print(
        f"Movers report — {res['count']} flagged ticker(s)\n"
        f"  HTML: {res['html_path']}\n"
        f"  MD:   {res['md_path']}\n"
        f"  Slack posted: {res['slack_posted']}"
    )
    return 0
