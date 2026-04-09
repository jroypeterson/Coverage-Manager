"""Append-only audit trail for the Coverage Manager.

Provides two logs:
- run_log.csv: records each command invocation and its outcome
- change_log.csv: records every mutation to the coverage universe CSV
"""

import csv
import os
from datetime import datetime

from config import SCRIPT_DIR
from logging_utils import get_logger

logger = get_logger("audit")

RUN_LOG_PATH = SCRIPT_DIR / "run_log.csv"
CHANGE_LOG_PATH = SCRIPT_DIR / "change_log.csv"

RUN_LOG_FIELDS = ["timestamp", "command", "steps_run", "steps_ok", "steps_failed", "tickers_added", "notes"]
CHANGE_LOG_FIELDS = ["timestamp", "action", "ticker", "company", "sector", "source"]


def _ensure_header(path, fields):
    """Write CSV header if file doesn't exist yet."""
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()


def log_run(command, steps, tickers_added=0, notes=""):
    """Log a command run to run_log.csv.

    Args:
        command: The CLI command that was run (e.g., "weekly-build", "performance")
        steps: dict of {step_name: status_string}
        tickers_added: Number of tickers added to the universe
        notes: Any additional notes

    The `steps_failed` column captures any non-success status — both
    `failed: ...` (exception) and `blocked: ...` (gated). The status string
    itself preserves the distinction so debugging stays unambiguous; the
    column exists for monitoring/rollup queries that just need to know
    "did this run fully succeed?".
    """
    _ensure_header(RUN_LOG_PATH, RUN_LOG_FIELDS)

    steps_run = ";".join(steps.keys())
    steps_ok = ";".join(k for k, v in steps.items() if v == "ok")
    steps_failed = ";".join(
        k
        for k, v in steps.items()
        if isinstance(v, str) and (v.startswith("failed") or v.startswith("blocked"))
    )

    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "command": command,
        "steps_run": steps_run,
        "steps_ok": steps_ok,
        "steps_failed": steps_failed,
        "tickers_added": tickers_added,
        "notes": notes,
    }

    with open(RUN_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RUN_LOG_FIELDS)
        writer.writerow(row)

    logger.info("Run logged: %s (%d steps ok, %d failed)", command, len(steps_ok.split(";")) if steps_ok else 0, len(steps_failed.split(";")) if steps_failed else 0)


def log_change(action, ticker, company="", sector="", source=""):
    """Log a universe mutation to change_log.csv.

    Args:
        action: "add", "remove", "update"
        ticker: The ticker symbol
        company: Company name
        sector: Sector classification
        source: What triggered the change (e.g., "discovery-2026-03-28", "cleanup-dedup")
    """
    _ensure_header(CHANGE_LOG_PATH, CHANGE_LOG_FIELDS)

    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "ticker": ticker,
        "company": company,
        "sector": sector,
        "source": source,
    }

    with open(CHANGE_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CHANGE_LOG_FIELDS)
        writer.writerow(row)


def log_changes_batch(action, tickers_info, source=""):
    """Log multiple universe mutations at once.

    Args:
        action: "add", "remove", "update"
        tickers_info: list of dicts with at least "ticker" key, optionally "company", "sector"
        source: What triggered the changes
    """
    _ensure_header(CHANGE_LOG_PATH, CHANGE_LOG_FIELDS)

    with open(CHANGE_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CHANGE_LOG_FIELDS)
        for info in tickers_info:
            writer.writerow({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "action": action,
                "ticker": info.get("ticker", ""),
                "company": info.get("company", ""),
                "sector": info.get("sector", ""),
                "source": source,
            })
