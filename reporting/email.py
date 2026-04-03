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


def archive_old_files(reports_dir, old_reports_dir, today_str):
    """Move prior dated performance files to old reports folder."""
    os.makedirs(old_reports_dir, exist_ok=True)
    patterns = [
        os.path.join(reports_dir, "coverage_performance_*.xlsx"),
        os.path.join(reports_dir, "coverage_performance_*.html"),
        os.path.join(reports_dir, "coverage_consolidated_*.html"),
        os.path.join(reports_dir, "coverage_biopharma_*.html"),
        os.path.join(reports_dir, "coverage_hc_svcs_medtech_*.html"),
        os.path.join(reports_dir, "coverage_pa_other_*.html"),
        os.path.join(reports_dir, "coverage_sp500_non_hc_*.html"),
        os.path.join(reports_dir, "coverage_sp500_*.html"),
    ]
    moved = 0
    for pattern in patterns:
        for f in glob.glob(pattern):
            if today_str in os.path.basename(f):
                continue
            dest = os.path.join(old_reports_dir, os.path.basename(f))
            shutil.move(f, dest)
            moved += 1
    if moved:
        logger.info("Archived %s old file(s) to: %s", moved, old_reports_dir)

    # Delete archived files older than 60 days
    deleted = 0
    cutoff = time.time() - 60 * 86400
    for f in glob.glob(os.path.join(old_reports_dir, "*")):
        if os.path.isfile(f) and os.path.getmtime(f) < cutoff:
            os.remove(f)
            deleted += 1
    if deleted:
        logger.info("Deleted %s archived file(s) older than 60 days", deleted)


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

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_addr, gmail_pass)
        server.send_message(msg)
    logger.info("Emailed %s report(s) to %s", len(html_paths), gmail_addr)
