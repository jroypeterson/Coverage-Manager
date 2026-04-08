"""Email delivery and report archiving."""

import os
import glob
import shutil
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from logging_utils import get_logger

logger = get_logger("perf_email")


REPORT_ARCHIVE_PATTERNS = [
    "coverage_performance_*.xlsx",
    "coverage_performance_*.html",
    "coverage_consolidated_*.html",
    "coverage_biopharma_*.html",
    "coverage_hc_svcs_medtech_*.html",
    "coverage_pa_other_*.html",
    "coverage_sp500_non_hc_*.html",
    "coverage_sp500_*.html",
]


def archive_files(source_dir, archive_dir, today_str, patterns, prune_days=60):
    """Move files matching any of `patterns` from source_dir to archive_dir.

    Files whose basename contains `today_str` are left in place. Files in
    `archive_dir` older than `prune_days` are deleted to keep the archive bounded.

    Args:
        source_dir: Directory to scan for matching files.
        archive_dir: Destination directory for moved files.
        today_str: Date string; files containing this in the basename are skipped.
        patterns: Iterable of glob patterns (basename globs, joined to source_dir).
        prune_days: Delete files in archive_dir older than this many days.

    Returns:
        dict with keys 'moved' (count of files moved) and 'pruned' (count deleted).
    """
    os.makedirs(archive_dir, exist_ok=True)
    moved = 0
    for pattern in patterns:
        for f in glob.glob(os.path.join(str(source_dir), pattern)):
            if today_str in os.path.basename(f):
                continue
            dest = os.path.join(str(archive_dir), os.path.basename(f))
            shutil.move(f, dest)
            moved += 1
    if moved:
        logger.info("Archived %s old file(s) to: %s", moved, archive_dir)

    pruned = 0
    if prune_days and prune_days > 0:
        cutoff = time.time() - prune_days * 86400
        for f in glob.glob(os.path.join(str(archive_dir), "*")):
            if os.path.isfile(f) and os.path.getmtime(f) < cutoff:
                os.remove(f)
                pruned += 1
        if pruned:
            logger.info("Deleted %s archived file(s) older than %s days", pruned, prune_days)

    return {"moved": moved, "pruned": pruned}


def archive_old_files(reports_dir, old_reports_dir, today_str):
    """Backwards-compat wrapper: archive performance reports using the standard patterns.

    New code should call `archive_files` directly with explicit patterns.
    """
    return archive_files(reports_dir, old_reports_dir, today_str, REPORT_ARCHIVE_PATTERNS)


def send_email_report(gmail_addr, gmail_pass, html_paths, today_str):
    """Send HTML reports as email attachments via Gmail SMTP.

    Accepts a single path or list of paths.
    """
    if isinstance(html_paths, (str, os.PathLike)):
        html_paths = [html_paths]

    msg = MIMEMultipart()
    msg["From"] = gmail_addr
    msg["To"] = gmail_addr
    msg["Subject"] = f"Coverage Performance Reports — {today_str}"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg.attach(MIMEText(f"{len(html_paths)} report(s) attached, generated {timestamp}.", "plain"))

    for html_path in html_paths:
        with open(html_path, "rb") as f:
            part = MIMEBase("text", "html")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={os.path.basename(html_path)}",
            )
            msg.attach(part)

    last_err = None
    for attempt in range(3):
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
                server.starttls()
                server.login(gmail_addr, gmail_pass)
                server.send_message(msg)
            logger.info("Emailed %s report(s) to %s", len(html_paths), gmail_addr)
            return
        except smtplib.SMTPAuthenticationError as e:
            logger.error("Gmail authentication failed — check GMAIL_APP_PASSWORD: %s", e)
            raise
        except (smtplib.SMTPException, OSError) as e:
            last_err = e
            if attempt < 2:
                wait = 5 * (attempt + 1)
                logger.warning("Email send failed (attempt %d/3): %s — retrying in %ds", attempt + 1, e, wait)
                time.sleep(wait)
            else:
                logger.error("Email send failed after 3 attempts: %s", e)
                raise
