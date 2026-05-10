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
#
# DEPRECATED 2026-05-03: kept for one cycle of back-compat while sigma-alert
# migrates to portfolio.json + researching.json. Will be removed in a
# follow-up after sigma-alert's screener consumes the new files.
CORE_WATCHLIST_FILENAME = "core_watchlist.json"
PORTFOLIO_FILENAME = "portfolio.json"
RESEARCHING_FILENAME = "researching.json"
# Passive-tracking list: names you follow for earnings/industry signal but
# have no intent to trade. Pushed for completeness; sigma-alert may render
# these in a separate Slack subcategory in the future.
FOLLOWING_FOR_INTEREST_FILENAME = "following_for_interest.json"
# Trigger-ready lists: thesis is done, waiting for an entry-price level. These
# are pushed to sigma-alert so the future price-target alerter (deferred —
# routes to the `#portfolio` Slack channel) can read the trigger levels.
READY_TO_BUY_FILENAME = "ready_to_buy.json"
READY_TO_SHORT_FILENAME = "ready_to_short.json"
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
            metadata[ticker] = {
                "name": name,
                "sector": sector,
                "subsector": "",
                "sub_subsector": "",
            }
    return metadata


def build_core_watchlist_payload(csv_path):
    """DEPRECATED — back-compat wrapper. Returns the union of portfolio +
    researching, in the legacy watchlist shape (Sell Price -> Target Price).

    Use `build_portfolio_payload` and `build_researching_payload` for new code.
    Will be removed once sigma-alert's screener migrates to the new files.
    """
    from universe import watchlist as wl  # back-compat shim

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
            "sub_subsector": meta.get("sub_subsector", ""),
        }
    return out


def _build_position_payload(csv_path, position_value):
    """Return the {ticker: {...}} dict for one Position state.

    Filters `data/positions_and_researching.csv` by Position == position_value
    and joins with universe metadata. Used for both portfolio.json and
    researching.json pushed to sigma-alert.
    """
    from universe import positions

    entries = positions.load(positions.POSITIONS_PATH)
    filtered = positions.filter_by_position(entries, position_value)
    metadata = build_universe_metadata(csv_path)
    out = {}
    for e in filtered:
        t = e["Ticker"]
        meta_key = t.split()[0].split(".")[0].upper()
        meta = metadata.get(meta_key, {})
        out[t] = {
            "position": e.get("Position", ""),
            "position_date": e.get("Position Date", ""),
            "buy_price": e.get("Buy Price"),
            "sell_price": e.get("Sell Price"),
            "first_buy_date": e.get("First Buy Date", ""),
            "average_cost": e.get("Average Cost"),
            "shares": e.get("Shares"),
            "notes": e.get("Notes", ""),
            "name": meta.get("name", ""),
            "sector": meta.get("sector", ""),
            "subsector": meta.get("subsector", ""),
            "sub_subsector": meta.get("sub_subsector", ""),
            "core": meta.get("core", ""),
        }
    return out


def build_portfolio_payload(csv_path):
    """{ticker: {...}} for Position=='Portfolio' rows. Pushed to sigma-alert."""
    return _build_position_payload(csv_path, "Portfolio")


def build_researching_payload(csv_path):
    """{ticker: {...}} for Position=='Researching' rows. Pushed to sigma-alert."""
    return _build_position_payload(csv_path, "Researching")


def build_following_for_interest_payload(csv_path):
    """{ticker: {...}} for Position=='Following for Interest' rows.
    Pushed to sigma-alert for passive-tracking display purposes."""
    return _build_position_payload(csv_path, "Following for Interest")


def build_ready_to_buy_payload(csv_path):
    """{ticker: {...}} for Position=='Ready to Buy' rows. Pushed to sigma-alert."""
    return _build_position_payload(csv_path, "Ready to Buy")


def build_ready_to_short_payload(csv_path):
    """{ticker: {...}} for Position=='Ready to Short' rows. Pushed to sigma-alert."""
    return _build_position_payload(csv_path, "Ready to Short")


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

    # Sync local clone with remote before writing. sigma-alert's GitHub Actions
    # cron jobs commit cache updates to origin/master after each market close,
    # so without rebasing first, our push is rejected as non-fast-forward and
    # the export silently stalls — the original cause of the 2026-04-07 →
    # 2026-04-29 core_watchlist drift.
    if push:
        branch, rc = _git(target_dir, "rev-parse", "--abbrev-ref", "HEAD")
        if rc != 0:
            return {"status": "failed", "reason": "could not determine current branch in sigma-alert clone"}
        _, rc = _git(target_dir, "fetch", "origin", branch)
        if rc != 0:
            return {"status": "failed", "reason": f"git fetch origin {branch} failed in sigma-alert clone"}
        _, rc = _git(target_dir, "rebase", f"origin/{branch}")
        if rc != 0:
            _git(target_dir, "rebase", "--abort")
            return {"status": "failed", "reason": "pre-export rebase failed (sigma-alert working tree dirty or conflict)"}

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
    watchlist_payload = build_core_watchlist_payload(csv_path)  # back-compat (one cycle)
    portfolio_payload = build_portfolio_payload(csv_path)
    researching_payload = build_researching_payload(csv_path)
    following_payload = build_following_for_interest_payload(csv_path)
    ready_to_buy_payload = build_ready_to_buy_payload(csv_path)
    ready_to_short_payload = build_ready_to_short_payload(csv_path)

    files = {
        METADATA_FILENAME: json.dumps(metadata, indent=2) + "\n",
        CORE_WATCHLIST_FILENAME: json.dumps(watchlist_payload, indent=2) + "\n",
        PORTFOLIO_FILENAME: json.dumps(portfolio_payload, indent=2) + "\n",
        RESEARCHING_FILENAME: json.dumps(researching_payload, indent=2) + "\n",
        FOLLOWING_FOR_INTEREST_FILENAME: json.dumps(following_payload, indent=2) + "\n",
        READY_TO_BUY_FILENAME: json.dumps(ready_to_buy_payload, indent=2) + "\n",
        READY_TO_SHORT_FILENAME: json.dumps(ready_to_short_payload, indent=2) + "\n",
    }

    def _result(**fields):
        out = {
            "tickers": len(metadata),
            "watchlist_entries": len(watchlist_payload),
            "portfolio_entries": len(portfolio_payload),
            "researching_entries": len(researching_payload),
            "following_for_interest_entries": len(following_payload),
            "ready_to_buy_entries": len(ready_to_buy_payload),
            "ready_to_short_entries": len(ready_to_short_payload),
        }
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
        "Sync ticker metadata + position lists from Coverage Manager",
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
