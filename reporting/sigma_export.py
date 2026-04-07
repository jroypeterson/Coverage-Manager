"""Export ticker metadata from the Coverage Manager CSV to the sigma-alert repo.

The sigma-alert screener loads `ticker_metadata.json` at startup so its Slack
alerts can show company names and sector tags, and so the 1σ alert tier can
filter on Healthcare Services / MedTech / PA tickers.

The Coverage Manager CSV is the canonical source for that data, so this module
generates the metadata file directly into the sibling sigma-alert clone, then
commits and pushes only that single file. sigma-alert's CI does not (and
should not) try to regenerate the file — it has no access to the CSV.
"""

import csv
import json
import subprocess
from pathlib import Path

from logging_utils import get_logger

logger = get_logger("reporting.sigma_export")

# Sigma-alert clone is a sibling of Coverage Manager in the Dropbox folder.
SIGMA_ALERT_DIR = Path(__file__).resolve().parent.parent.parent / "sigma-alert"
METADATA_FILENAME = "ticker_metadata.json"

# Sector ETFs are not in the Coverage Manager universe but the sigma-alert
# watchlist includes them, so we hard-code their display info here.
SECTOR_ETFS = {
    "XLE": ("Energy Select Sector SPDR", "ETF"),
    "XLB": ("Materials Select Sector SPDR", "ETF"),
    "XLU": ("Utilities Select Sector SPDR", "ETF"),
    "XLP": ("Consumer Staples Select Sector SPDR", "ETF"),
    "XLI": ("Industrial Select Sector SPDR", "ETF"),
    "XLRE": ("Real Estate Select Sector SPDR", "ETF"),
    "XLC": ("Communication Services Select Sector SPDR", "ETF"),
    "XLV": ("Health Care Select Sector SPDR", "ETF"),
    "XLK": ("Technology Select Sector SPDR", "ETF"),
    "XLY": ("Consumer Discretionary Select Sector SPDR", "ETF"),
    "XLF": ("Financial Select Sector SPDR", "ETF"),
    "XBI": ("SPDR S&P Biotech ETF", "ETF"),
    "SPYM": ("SPDR Portfolio S&P 500 ETF", "ETF"),
}


def build_metadata(csv_path):
    """Read the coverage CSV and return a {TICKER: {name, sector, subsector}} dict."""
    metadata = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("Ticker", "").strip().upper()
            if not ticker or ticker == "#N/A":
                continue
            # Strip exchange suffix to match the format the screener uses
            # ("ROG SW" -> "ROG", "FRE.DE" -> "FRE")
            plain = ticker.split()[0] if " " in ticker else ticker
            plain = plain.split(".")[0] if "." in plain else plain
            metadata[plain] = {
                "name": row.get("Company Name", "").strip(),
                "sector": row.get("Sector (JP)", "").strip(),
                "subsector": row.get("Subsector (JP)", "").strip(),
            }

    for ticker, (name, sector) in SECTOR_ETFS.items():
        if ticker not in metadata:
            metadata[ticker] = {"name": name, "sector": sector, "subsector": ""}

    return metadata


def _git(cwd, *args):
    """Run a git command in cwd, returning (stdout, returncode). Errors are logged, not raised."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip(), result.returncode


def export_and_push(csv_path, target_dir=SIGMA_ALERT_DIR, push=True):
    """Build metadata, write it into target_dir, and commit/push only that file.

    Returns a dict describing what happened. Raises only on unrecoverable errors;
    expected outcomes (no changes, no remote, etc.) are reported in the dict.
    """
    if not target_dir.exists():
        return {"status": "skipped", "reason": f"sigma-alert clone not found at {target_dir}"}

    if not (target_dir / ".git").exists():
        return {"status": "skipped", "reason": f"{target_dir} is not a git repo"}

    metadata = build_metadata(csv_path)
    target_path = target_dir / METADATA_FILENAME

    new_content = json.dumps(metadata, indent=2) + "\n"
    if target_path.exists() and target_path.read_text(encoding="utf-8") == new_content:
        return {"status": "unchanged", "tickers": len(metadata)}

    target_path.write_text(new_content, encoding="utf-8")
    logger.info("Wrote %d ticker entries to %s", len(metadata), target_path)

    # Stage only the metadata file — leave any other unstaged work in sigma-alert alone.
    _, rc = _git(target_dir, "add", METADATA_FILENAME)
    if rc != 0:
        return {"status": "failed", "reason": "git add failed", "tickers": len(metadata)}

    # Was anything actually staged? (handles the rare case where file content
    # changed but git normalizes line endings to match HEAD)
    _, rc = _git(target_dir, "diff", "--cached", "--quiet", "--", METADATA_FILENAME)
    if rc == 0:
        return {"status": "unchanged", "tickers": len(metadata)}

    _, rc = _git(target_dir, "commit", "-m", "Sync ticker metadata from Coverage Manager")
    if rc != 0:
        return {"status": "failed", "reason": "git commit failed", "tickers": len(metadata)}

    if not push:
        return {"status": "committed", "tickers": len(metadata)}

    _, rc = _git(target_dir, "push", "origin", "HEAD")
    if rc != 0:
        return {
            "status": "committed_not_pushed",
            "reason": "git push failed — commit is local",
            "tickers": len(metadata),
        }

    return {"status": "pushed", "tickers": len(metadata)}
