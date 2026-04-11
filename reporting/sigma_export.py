"""Export ticker metadata from the Coverage Manager CSV to the sigma-alert repo.

The sigma-alert screener loads `ticker_metadata.json` at startup so its Slack
alerts can show company names and sector tags, and so the 1σ alert tier can
filter on Healthcare Services / MedTech / PA tickers.

The Coverage Manager CSV is the canonical source for that data, so this module
generates the metadata file directly into the sibling sigma-alert clone, then
commits and pushes only that single file. sigma-alert's CI does not (and
should not) try to regenerate the file — it has no access to the CSV.
"""

import json
import subprocess
from pathlib import Path

from logging_utils import get_logger
from universe.artifacts import build_universe_metadata

logger = get_logger("reporting.sigma_export")

# Sigma-alert clone is a sibling of Coverage Manager in the Dropbox folder.
SIGMA_ALERT_DIR = Path(__file__).resolve().parent.parent.parent / "sigma-alert"
METADATA_FILENAME = "ticker_metadata.json"
# Core watchlist pushed alongside ticker_metadata.json so the sigma
# screener can surface watchlist hits in its own section of the Slack
# digest. Schema mirrors exports/watchlist.json — ticker-keyed, with
# buy/target/notes joined against universe metadata.
CORE_WATCHLIST_FILENAME = "core_watchlist.json"
# Sigma-alert's EOD screener writes this file when it finds watchlist tickers
# that are missing from ticker_metadata.json (or have a blank name). It's the
# only way the screener — running in CI with no access to the Coverage Manager
# CSV — can flag gaps for us to fix.
MISSING_METADATA_RELPATH = "cache/missing_metadata.json"

# Sector ETFs are not in the Coverage Manager universe but the sigma-alert
# watchlist includes them, so we hard-code their display info here.
#
# TODO (Stage 2 follow-up): move this list into the sigma-alert repo itself.
# The clean end state is that Coverage Manager publishes only generic
# `exports/universe_metadata.json`, sigma-alert reads that file directly, and
# sigma-alert applies its own ETF augmentation locally before consuming. That
# eliminates the need for sigma_export to know anything about sigma-alert's
# watchlist composition. Deferred from the current PR because moving the ETF
# list cross-repo also requires updating sigma-alert's read path and adds
# scope/risk to the GitHub Actions screener.
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


def build_sigma_metadata(csv_path):
    """Build the sigma-alert ticker metadata: generic universe + sigma-only ETFs.

    The sigma-alert watchlist includes sector ETFs that are not part of the
    coverage universe. This function composes the generic universe metadata
    with those ETFs so the screener has display info for the full watchlist.

    Generic exports under `exports/` must NOT use this function — they should
    call `universe.artifacts.build_universe_metadata` directly so consumer-
    specific tickers don't leak into the published artifact contract.
    """
    metadata = build_universe_metadata(csv_path)
    for ticker, (name, sector) in SECTOR_ETFS.items():
        if ticker not in metadata:
            metadata[ticker] = {"name": name, "sector": sector, "subsector": ""}
    return metadata


def build_core_watchlist_payload(csv_path):
    """Return the {ticker: {...}} dict to write into sigma-alert.

    Uses the same join logic as `weekly_universe._step_export_watchlist` so the
    two files stay in sync: each entry has buy/target/notes from the personal
    watchlist CSV plus name/sector/subsector from the universe CSV.
    """
    from universe import watchlist as wl

    entries = wl.load(wl.WATCHLIST_PATH)
    metadata = build_universe_metadata(csv_path)
    out = {}
    for e in entries:
        t = e["Ticker"]
        meta_key = t.split()[0].split(".")[0].upper()
        meta = metadata.get(meta_key, {})
        out[t] = {
            "buy_price": e.get("Buy Price"),
            "target_price": e.get("Target Price"),
            "date_added": e.get("Date Added", ""),
            "notes": e.get("Notes", ""),
            "name": meta.get("name", ""),
            "sector": meta.get("sector", ""),
            "subsector": meta.get("subsector", ""),
        }
    return out


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

    metadata = build_sigma_metadata(csv_path)
    watchlist_payload = build_core_watchlist_payload(csv_path)

    files = {
        METADATA_FILENAME: json.dumps(metadata, indent=2) + "\n",
        CORE_WATCHLIST_FILENAME: json.dumps(watchlist_payload, indent=2) + "\n",
    }

    def _result(**fields):
        out = {"tickers": len(metadata), "watchlist_entries": len(watchlist_payload)}
        if flagged_tickers:
            out["missing_metadata"] = flagged_tickers
        out.update(fields)
        return out

    # Write files that actually changed; stage all tracked files so we pick up
    # anything the previous run left in an inconsistent state.
    changed_any = False
    for name, content in files.items():
        path = target_dir / name
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.write_text(content, encoding="utf-8")
        logger.info("Wrote %s (%d bytes)", path, len(content))
        changed_any = True

    if not changed_any:
        return _result(status="unchanged")

    for name in files:
        _, rc = _git(target_dir, "add", name)
        if rc != 0:
            return _result(status="failed", reason=f"git add {name} failed")

    # Was anything actually staged? (handles the rare case where file content
    # changed but git normalizes line endings to match HEAD)
    _, rc = _git(target_dir, "diff", "--cached", "--quiet", "--", *files.keys())
    if rc == 0:
        return _result(status="unchanged")

    _, rc = _git(
        target_dir, "commit", "-m",
        "Sync ticker metadata + core watchlist from Coverage Manager",
    )
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
