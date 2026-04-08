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
# Sigma-alert's EOD screener writes this file when it finds watchlist tickers
# that are missing from ticker_metadata.json (or have a blank name). It's the
# only way the screener — running in CI with no access to the Coverage Manager
# CSV — can flag gaps for us to fix.
MISSING_METADATA_RELPATH = "cache/missing_metadata.json"

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


def read_missing_metadata_flag(target_dir=SIGMA_ALERT_DIR):
    """Read the sigma-alert flag file listing tickers missing from metadata.

    Returns the parsed dict ({"updated": ISO8601, "tickers": {TICKER: reason}})
    or None if the file doesn't exist or can't be read. The screener writes
    this file on EOD runs whenever it finds watchlist tickers that are missing
    from ticker_metadata.json (or have a blank `name` field).
    """
    flag_path = target_dir / MISSING_METADATA_RELPATH
    if not flag_path.exists():
        return None
    try:
        with open(flag_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read sigma-alert missing-metadata flag: %s", e)
        return None


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

    # Surface any tickers sigma-alert flagged as missing metadata. We log the
    # warning whether or not the export below ends up changing the file —
    # the operator needs to see the gaps so they can fix the source CSV.
    flag = read_missing_metadata_flag(target_dir)
    flagged_tickers = {}
    if flag and flag.get("tickers"):
        flagged_tickers = flag["tickers"]
        logger.warning(
            "sigma-alert flagged %d ticker(s) missing metadata (as of %s): %s",
            len(flagged_tickers),
            flag.get("updated", "unknown"),
            sorted(flagged_tickers),
        )

    metadata = build_metadata(csv_path)
    target_path = target_dir / METADATA_FILENAME

    def _result(**fields):
        out = {"tickers": len(metadata)}
        if flagged_tickers:
            out["missing_metadata"] = flagged_tickers
        out.update(fields)
        return out

    new_content = json.dumps(metadata, indent=2) + "\n"
    if target_path.exists() and target_path.read_text(encoding="utf-8") == new_content:
        return _result(status="unchanged")

    target_path.write_text(new_content, encoding="utf-8")
    logger.info("Wrote %d ticker entries to %s", len(metadata), target_path)

    # Stage only the metadata file — leave any other unstaged work in sigma-alert alone.
    _, rc = _git(target_dir, "add", METADATA_FILENAME)
    if rc != 0:
        return _result(status="failed", reason="git add failed")

    # Was anything actually staged? (handles the rare case where file content
    # changed but git normalizes line endings to match HEAD)
    _, rc = _git(target_dir, "diff", "--cached", "--quiet", "--", METADATA_FILENAME)
    if rc == 0:
        return _result(status="unchanged")

    _, rc = _git(target_dir, "commit", "-m", "Sync ticker metadata from Coverage Manager")
    if rc != 0:
        return _result(status="failed", reason="git commit failed")

    if not push:
        return _result(status="committed")

    _, rc = _git(target_dir, "push", "origin", "HEAD")
    if rc != 0:
        return _result(
            status="committed_not_pushed",
            reason="git push failed — commit is local",
        )

    return _result(status="pushed")
