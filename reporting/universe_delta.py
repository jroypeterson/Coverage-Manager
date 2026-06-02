"""Weekly universe delta — before/after snapshot, Slack post to #coverage.

Renders the universe state at the start of the weekly run, the deltas applied
during the run (additions, removals, sector reclassifications, position state
transitions, ISIN fills), and the final state — into a single Block Kit Slack
message posted to #coverage. Replaces the prior weekly performance-report email.

Baseline strategy (2-tier):

  1. PREFERRED: end-of-previous-run snapshot files at
       .coverage/last_run_universe.csv
       .coverage/last_run_positions.csv
     written unconditionally at the end of each post-step, regardless of
     whether the Slack post itself succeeded. This is the source of truth for
     "what the universe looked like last time we ran." Independent of git, so
     uncommitted manual edits between weekly runs are correctly reported in
     the next week's delta.

  2. FALLBACK: git HEAD at run start. Used only on the very first run after
     this snapshot mechanism shipped, or if the snapshot file was deleted.
     When this path runs, `capture_baseline_shas()` also detects whether the
     working tree was dirty for the universe/positions CSVs and threads a
     caveat into the message so the user knows the diff may include
     pre-existing local edits.

Pure functions (`compute_universe_delta`, `format_universe_delta_slack`) take
DataFrames in and return data structures / strings — tests don't need git or
real files.
"""

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd

from config import CSV_PATH, SCRIPT_DIR, TODAY
from logging_utils import get_logger
from reporting.slack import urlopen_with_retry
from universe.positions import POSITIONS_PATH

logger = get_logger("reporting.universe_delta")

COVERAGE_CHANNEL = "#coverage"
FALLBACK_DIR = SCRIPT_DIR / ".coverage"
SNAPSHOT_UNIVERSE_PATH = FALLBACK_DIR / "last_run_universe.csv"
SNAPSHOT_POSITIONS_PATH = FALLBACK_DIR / "last_run_positions.csv"

# Only column changes in this set produce "Modified" rows. CIK, FIGI variants,
# Exchange Code, Currency are operational hygiene fields and would create noise
# in a weekly digest. ISIN is handled separately (blank → non-blank only).
TRACKED_MODIFIED_FIELDS = (
    "Sector (JP)",
    "Subsector (JP)",
    "Sub-subsector (JP)",
    "Core",
    "Country (HQ)",
)

TOP_SECTORS_TO_SHOW = 5
MAX_POSITION_CHANGES = 20
_SLACK_SECTION_TEXT_MAX = 3000

_POSITION_ORDER = [
    "Portfolio", "Researching", "Following for Interest",
    "Ready to Buy", "Ready to Short",
]


# ── Git helpers ──────────────────────────────────────────────────────────────


def _git_run(args, timeout=10):
    """Run a git command from SCRIPT_DIR. Returns stdout (str) or None."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        logger.debug("git %s failed: %s", " ".join(args), e)
    return None


def _git_head_sha():
    out = _git_run(["rev-parse", "HEAD"])
    return out.strip() if out else None


def _git_commit_date(commit_sha):
    """Return the short ISO date (YYYY-MM-DD) of a commit, or None."""
    if not commit_sha:
        return None
    out = _git_run(["show", "-s", "--format=%cs", commit_sha])
    return out.strip() if out else None


def _git_show(commit_sha, rel_path):
    """Read file contents at a commit. Returns str or None."""
    if not commit_sha:
        return None
    return _git_run(["show", f"{commit_sha}:{rel_path}"])


def _git_dirty_paths(rel_paths):
    """Return the subset of `rel_paths` that show uncommitted changes.

    Uses `git status --porcelain -- <paths>`. Empty list when clean or when
    git is unavailable.
    """
    if not rel_paths:
        return []
    args = ["status", "--porcelain", "--"] + list(rel_paths)
    out = _git_run(args)
    if not out:
        return []
    dirty = []
    for line in out.splitlines():
        # Porcelain format: "XY path" — first two chars are status codes
        if len(line) > 3:
            path = line[3:].strip().strip('"')
            dirty.append(path)
    return dirty


def capture_baseline_shas():
    """Capture the current git HEAD SHA + dirty-path state at run start.

    Called from the top of `weekly_universe.main()`, BEFORE any mutation step.
    The SHA is used as a fallback baseline when the snapshot files in
    `.coverage/` are missing (first run after this mechanism shipped).
    `dirty_paths` flags whether the universe/positions CSVs have uncommitted
    changes — only relevant when the git fallback is taken, where pre-existing
    dirty edits would otherwise appear as "this run's" deltas.
    """
    sha = _git_head_sha()
    rel_paths = [
        "data/coverage_universe_tickers.csv",
        "data/positions_and_researching.csv",
    ]
    dirty = _git_dirty_paths(rel_paths)
    return {
        "head_sha": sha,
        "head_date": _git_commit_date(sha),
        "universe_rel": rel_paths[0],
        "positions_rel": rel_paths[1],
        "dirty_paths": dirty,
    }


# ── Snapshot loading ─────────────────────────────────────────────────────────


def _read_csv_text(text):
    if text is None:
        return None
    return pd.read_csv(StringIO(text))


def load_universe_snapshot(commit_sha=None, baseline=None):
    """Load the universe CSV from working tree (commit_sha=None) or a commit.

    Used for working-tree reads in the post-step. Baseline reads should go
    through `load_baseline_universe` instead (which prefers snapshot files).

    Returns None if the file wasn't tracked at the given commit.
    """
    if commit_sha is None:
        return pd.read_csv(CSV_PATH, encoding="utf-8-sig")
    rel = (baseline or {}).get("universe_rel", "data/coverage_universe_tickers.csv")
    text = _git_show(commit_sha, rel)
    return _read_csv_text(text)


def load_positions_snapshot(commit_sha=None, baseline=None):
    """Same shape as load_universe_snapshot for positions_and_researching.csv."""
    if commit_sha is None:
        if not POSITIONS_PATH.exists():
            return None
        return pd.read_csv(POSITIONS_PATH, encoding="utf-8-sig")
    rel = (baseline or {}).get("positions_rel", "data/positions_and_researching.csv")
    text = _git_show(commit_sha, rel)
    return _read_csv_text(text)


def load_baseline_universe(snapshot_path=None, commit_sha=None, baseline=None):
    """Load the baseline universe DataFrame for the diff.

    Tier 1: end-of-previous-run snapshot file (preferred).
    Tier 2: git HEAD at run start (bootstrap fallback).
    Returns None if neither is available — caller renders "baseline unavailable".
    """
    snapshot_path = Path(snapshot_path or SNAPSHOT_UNIVERSE_PATH)
    if snapshot_path.exists():
        return pd.read_csv(snapshot_path, encoding="utf-8-sig")
    if commit_sha:
        return load_universe_snapshot(commit_sha=commit_sha, baseline=baseline)
    return None


def load_baseline_positions(snapshot_path=None, commit_sha=None, baseline=None):
    """Same shape as load_baseline_universe for positions_and_researching.csv."""
    snapshot_path = Path(snapshot_path or SNAPSHOT_POSITIONS_PATH)
    if snapshot_path.exists():
        return pd.read_csv(snapshot_path, encoding="utf-8-sig")
    if commit_sha:
        return load_positions_snapshot(commit_sha=commit_sha, baseline=baseline)
    return None


def write_run_snapshot(fallback_dir=None):
    """Snapshot the current working tree to `.coverage/last_run_*.csv`.

    Called at the end of `_step_universe_delta_slack`, AFTER the Slack post
    attempt. The snapshot represents "end of this run's state" and becomes
    next week's baseline. Written even if the Slack post failed — Slack
    success/failure is orthogonal to what the universe state actually is.

    Returns the list of snapshot file paths written.
    """
    fallback_dir = Path(fallback_dir or FALLBACK_DIR)
    fallback_dir.mkdir(parents=True, exist_ok=True)
    written = []
    universe_out = fallback_dir / "last_run_universe.csv"
    shutil.copyfile(CSV_PATH, universe_out)
    written.append(universe_out)
    if POSITIONS_PATH.exists():
        positions_out = fallback_dir / "last_run_positions.csv"
        shutil.copyfile(POSITIONS_PATH, positions_out)
        written.append(positions_out)
    return written


def write_delta_json(delta, fallback_dir=None, reason=None):
    """Always write the delta payload to .coverage/, regardless of Slack outcome.

    Two files:
      - universe_delta_{TODAY}.json   (timestamped, historical)
      - last_universe_delta.json      (stable, always overwritten)

    Both files include `reason` (None on the normal pre-post write; set to a
    failure description on the post-failure overwrite from `post_universe_delta`).
    """
    fallback_dir = Path(fallback_dir or FALLBACK_DIR)
    fallback_dir.mkdir(parents=True, exist_ok=True)
    payload = {"reason": reason, "today": delta.get("today"), "delta": delta}
    text = json.dumps(payload, indent=2, default=str)
    timestamped = fallback_dir / f"universe_delta_{delta.get('today', TODAY)}.json"
    stable = fallback_dir / "last_universe_delta.json"
    timestamped.write_text(text, encoding="utf-8")
    stable.write_text(text, encoding="utf-8")
    return [timestamped, stable]


def snapshot_mtime_date(snapshot_path=None):
    """Return the YYYY-MM-DD mtime of the snapshot file, or None if absent."""
    snapshot_path = Path(snapshot_path or SNAPSHOT_UNIVERSE_PATH)
    if not snapshot_path.exists():
        return None
    return datetime.fromtimestamp(
        snapshot_path.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%d")


# ── Diff computation ─────────────────────────────────────────────────────────


def _stats_for_universe(df):
    if df is None or df.empty:
        return {"total": 0, "core_y": 0, "sector_counts": {}}
    total = len(df)
    core_y = 0
    if "Core" in df.columns:
        core_y = int((df["Core"].fillna("").astype(str).str.upper() == "Y").sum())
    sector_counts = {}
    if "Sector (JP)" in df.columns:
        sector_counts = {k: int(v) for k, v in df["Sector (JP)"].fillna("").value_counts().items()}
    return {"total": total, "core_y": core_y, "sector_counts": sector_counts}


def _stats_for_positions(df):
    if df is None or df.empty or "Position" not in df.columns:
        return {}
    return {k: int(v) for k, v in df["Position"].fillna("").value_counts().items()}


def _delisted_reason_for(ticker, delisted_df):
    if delisted_df is None or delisted_df.empty or "Ticker" not in delisted_df.columns:
        return None
    matches = delisted_df[delisted_df["Ticker"].astype(str).str.upper() == ticker.upper()]
    if matches.empty:
        return None
    row = matches.iloc[0]
    reason = str(row.get("Reason", "") or "").strip()
    notes = str(row.get("Notes", "") or "").strip()
    if reason and notes:
        return f"{reason} — {notes}"
    return reason or notes or None


def _row_to_dict(df, ticker):
    matches = df[df["Ticker"].astype(str) == ticker]
    if matches.empty:
        return {}
    return {k: ("" if pd.isna(v) else v) for k, v in matches.iloc[0].to_dict().items()}


def compute_universe_delta(
    before_universe_df,
    after_universe_df,
    before_positions_df=None,
    after_positions_df=None,
    delisted_df=None,
    baseline_sha=None,
    baseline_date=None,
    baseline_source=None,
    baseline_label=None,
    baseline_caveat=None,
):
    """Compute a flat delta structure between two universe snapshots.

    Pure — no I/O, no git. Tests pass fixture DataFrames directly.

    Args:
        baseline_source: "snapshot" | "git" | "none" — which tier produced
            the before-state. Drives the Before-block header in the formatter.
        baseline_label: Human-readable baseline descriptor (e.g.,
            "end of previous run · 2026-05-22" or "commit @ abc1234, 2026-05-22").
        baseline_caveat: Optional warning string shown right after the header
            in the Slack message (e.g., "working tree was dirty at run start").

    Returns a dict with: added, removed, modified (flat: one row per
    ticker × field), position_changes, before_stats, after_stats,
    before_position_counts, after_position_counts, baseline_sha,
    baseline_date, baseline_source, baseline_label, baseline_caveat, today.
    """
    if after_universe_df is None:
        after_universe_df = pd.DataFrame(columns=["Ticker"])
    if before_universe_df is None:
        before_universe_df = pd.DataFrame(columns=["Ticker"])

    before_tickers = set(before_universe_df.get("Ticker", pd.Series(dtype=str)).astype(str))
    after_tickers = set(after_universe_df.get("Ticker", pd.Series(dtype=str)).astype(str))

    added_tickers = sorted(after_tickers - before_tickers)
    removed_tickers = sorted(before_tickers - after_tickers)

    added = []
    for t in added_tickers:
        row = _row_to_dict(after_universe_df, t)
        added.append({
            "ticker": t,
            "name": row.get("Company Name", ""),
            "sector": row.get("Sector (JP)", ""),
            "subsector": row.get("Subsector (JP)", ""),
            "sub_subsector": row.get("Sub-subsector (JP)", ""),
            "country_hq": row.get("Country (HQ)", ""),
            "core": row.get("Core", ""),
        })

    removed = []
    for t in removed_tickers:
        row = _row_to_dict(before_universe_df, t)
        removed.append({
            "ticker": t,
            "name": row.get("Company Name", ""),
            "sector": row.get("Sector (JP)", ""),
            "subsector": row.get("Subsector (JP)", ""),
            "reason": _delisted_reason_for(t, delisted_df) or "manual removal",
        })

    modified = []
    common = sorted(before_tickers & after_tickers)
    for t in common:
        before_row = _row_to_dict(before_universe_df, t)
        after_row = _row_to_dict(after_universe_df, t)
        for field in TRACKED_MODIFIED_FIELDS:
            old_val = str(before_row.get(field, "") or "").strip()
            new_val = str(after_row.get(field, "") or "").strip()
            if old_val != new_val:
                modified.append({"ticker": t, "field": field, "old": old_val, "new": new_val})
        old_isin = str(before_row.get("ISIN", "") or "").strip()
        new_isin = str(after_row.get("ISIN", "") or "").strip()
        if not old_isin and new_isin:
            modified.append({"ticker": t, "field": "ISIN", "old": "", "new": new_isin})

    position_changes = []
    if before_positions_df is not None and after_positions_df is not None:
        before_pos, after_pos = {}, {}
        if "Ticker" in before_positions_df.columns and "Position" in before_positions_df.columns:
            for _, r in before_positions_df.iterrows():
                t = str(r["Ticker"] or "").strip()
                if t:
                    before_pos[t] = str(r["Position"] or "").strip()
        if "Ticker" in after_positions_df.columns and "Position" in after_positions_df.columns:
            for _, r in after_positions_df.iterrows():
                t = str(r["Ticker"] or "").strip()
                if t:
                    after_pos[t] = str(r["Position"] or "").strip()
        for t in sorted(set(before_pos) | set(after_pos)):
            b = before_pos.get(t, "")
            a = after_pos.get(t, "")
            if b != a and (b or a):
                position_changes.append({
                    "ticker": t,
                    "before_state": b or "(none)",
                    "after_state": a or "(removed)",
                })

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "position_changes": position_changes,
        "before_stats": _stats_for_universe(before_universe_df),
        "after_stats": _stats_for_universe(after_universe_df),
        "before_position_counts": _stats_for_positions(before_positions_df),
        "after_position_counts": _stats_for_positions(after_positions_df),
        "baseline_sha": baseline_sha,
        "baseline_date": baseline_date,
        "baseline_source": baseline_source,
        "baseline_label": baseline_label,
        "baseline_caveat": baseline_caveat,
        "today": TODAY,
    }


# ── Slack formatting ─────────────────────────────────────────────────────────


def _format_field_change(field, old, new):
    old_disp = '""' if old == "" else old
    new_disp = '""' if new == "" else new
    return f"{field}: {old_disp} → {new_disp}"


def _group_modified_by_ticker(modified):
    grouped = defaultdict(list)
    for m in modified:
        grouped[m["ticker"]].append((m["field"], m["old"], m["new"]))
    return grouped


def _format_stats_block(label, header_suffix, universe_stats, position_counts,
                       baseline_universe_stats=None, baseline_position_counts=None):
    lines = [f"*{label}*{header_suffix}"]
    total = universe_stats.get("total", 0)
    lines.append(f"• Total tickers: {total:,}")

    core_y = universe_stats.get("core_y", 0)
    core_delta = ""
    if baseline_universe_stats is not None:
        diff = core_y - baseline_universe_stats.get("core_y", 0)
        if diff != 0:
            core_delta = f" ({diff:+d})"
    lines.append(f"• Core=\"Y\": {core_y}{core_delta}")

    sector_counts = universe_stats.get("sector_counts", {})
    top = sorted(sector_counts.items(), key=lambda kv: -kv[1])[:TOP_SECTORS_TO_SHOW]
    if top:
        before_counts = (baseline_universe_stats or {}).get("sector_counts", {})
        sector_strs = []
        for sector, count in top:
            if baseline_universe_stats is not None:
                diff = count - before_counts.get(sector, 0)
                sector_strs.append(f"{sector} {count} ({diff:+d})" if diff != 0 else f"{sector} {count}")
            else:
                sector_strs.append(f"{sector} {count}")
        lines.append(f"• Sector (JP) top {len(top)}: " + " · ".join(sector_strs))

    if position_counts:
        position_strs = []
        for state in _POSITION_ORDER:
            if state not in position_counts and (not baseline_position_counts or state not in baseline_position_counts):
                continue
            count = position_counts.get(state, 0)
            if baseline_position_counts is not None:
                diff = count - baseline_position_counts.get(state, 0)
                position_strs.append(f"{state} {count} ({diff:+d})" if diff != 0 else f"{state} {count}")
            else:
                position_strs.append(f"{state} {count}")
        if position_strs:
            lines.append("• Position states: " + " · ".join(position_strs))

    return "\n".join(lines)


def format_universe_delta_slack(delta):
    """Render the full delta as Slack mrkdwn (returned as a single string).

    Section order (top-down): header → caveat (if any) → After → Before → Delta.
    Current-state-first chosen for glanceability; Before is context; Delta is
    drill-down. See CLAUDE.md "Weekly universe delta -> Slack #coverage" for
    rationale.

    The wire-payload helper `_split_into_section_blocks` chunks it if needed.
    """
    baseline_source = delta.get("baseline_source")
    baseline_label = delta.get("baseline_label")
    baseline_caveat = delta.get("baseline_caveat")
    today = delta.get("today", "")

    parts = [f":open_file_folder: *Coverage Universe — Weekly Update — {today}*"]

    # Caveat (only when present) — e.g. dirty working tree at run start.
    if baseline_caveat:
        parts.append(f":warning: _{baseline_caveat}_")

    # 1) After — current state up top.
    after_total = delta["after_stats"].get("total", 0)
    parts.append(
        _format_stats_block(
            "After", f" ({after_total:,} tickers)",
            delta["after_stats"], delta["after_position_counts"],
            baseline_universe_stats=delta["before_stats"],
            baseline_position_counts=delta["before_position_counts"],
        )
    )

    # 2) Before — last-run context next.
    if baseline_source == "none" or (baseline_source is None and baseline_label is None):
        before_header = " (baseline unavailable — first run after snapshot mechanism shipped, or snapshot missing and no git history)"
    elif baseline_label:
        before_header = f" ({baseline_label})"
    else:
        # legacy / tests passing only baseline_sha + baseline_date
        baseline_sha = delta.get("baseline_sha")
        baseline_date = delta.get("baseline_date")
        if baseline_sha:
            before_header = f" (committed state @ {baseline_sha[:7]}, {baseline_date or 'previous commit'})"
        else:
            before_header = ""
    parts.append(
        _format_stats_block(
            "Before", before_header,
            delta["before_stats"], delta["before_position_counts"],
        )
    )

    # 3) Delta sections — the per-ticker drill-down.
    added = delta["added"]
    removed = delta["removed"]
    modified = delta["modified"]
    position_changes = delta["position_changes"]
    modified_grouped = _group_modified_by_ticker(modified)

    parts.append(
        f"*Delta* (+{len(added)} added · −{len(removed)} removed · {len(modified_grouped)} modified)"
    )

    if added:
        lines = [f"*Added ({len(added)})*"]
        for a in added:
            sector_bits = [s for s in (a.get("sector", ""), a.get("subsector", "")) if s]
            suffix = (" — " + " / ".join(sector_bits)) if sector_bits else ""
            lines.append(f"• `{a['ticker']}`  {a['name']}{suffix}")
        parts.append("\n".join(lines))

    if removed:
        lines = [f"*Removed ({len(removed)})*"]
        for r in removed:
            lines.append(f"• `{r['ticker']}`  {r['name']} — {r['reason']}")
        parts.append("\n".join(lines))

    if modified_grouped:
        lines = [f"*Modified ({len(modified_grouped)})*"]
        for ticker, changes in modified_grouped.items():
            change_str = "; ".join(_format_field_change(f, o, n) for f, o, n in changes)
            lines.append(f"• `{ticker}`  {change_str}")
        parts.append("\n".join(lines))

    if position_changes:
        total_pc = len(position_changes)
        shown = position_changes[:MAX_POSITION_CHANGES]
        lines = [f"*Position changes ({total_pc})*"]
        for pc in shown:
            lines.append(f"• `{pc['ticker']}`  {pc['before_state']} → {pc['after_state']}")
        if total_pc > MAX_POSITION_CHANGES:
            lines.append(f"_+{total_pc - MAX_POSITION_CHANGES} more — see fallback file_")
        parts.append("\n".join(lines))

    if not (added or removed or modified or position_changes):
        parts.append("_No changes this week._")

    return "\n\n".join(parts)


def _split_into_section_blocks(message):
    if len(message) <= _SLACK_SECTION_TEXT_MAX:
        return [{"type": "section", "text": {"type": "mrkdwn", "text": message}}]
    blocks = []
    current, current_len = [], 0
    for line in message.splitlines(keepends=True):
        if current_len + len(line) > _SLACK_SECTION_TEXT_MAX and current:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "".join(current)}})
            current, current_len = [line], len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "".join(current)}})
    return blocks


# ── Post + fallback ─────────────────────────────────────────────────────────


def post_universe_delta(webhook_url, delta, fallback_dir=None):
    """Post the universe delta to Slack #coverage with fallback on failure.

    On any non-success path, writes TWO files to `fallback_dir`:
      - universe_delta_{TODAY}.json  (timestamped, historical)
      - last_universe_delta.json     (stable, always overwritten)

    Returns {"posted": True/False, "reason": str | None}. Never raises.
    """
    fallback_dir = Path(fallback_dir or FALLBACK_DIR)
    message = format_universe_delta_slack(delta)
    body = {
        "blocks": _split_into_section_blocks(message),
        "text": message,
    }

    def _write_fallback(reason):
        try:
            fallback_dir.mkdir(parents=True, exist_ok=True)
            payload = {"reason": reason, "today": delta.get("today"), "delta": delta}
            timestamped = fallback_dir / f"universe_delta_{delta.get('today', TODAY)}.json"
            stable = fallback_dir / "last_universe_delta.json"
            text = json.dumps(payload, indent=2, default=str)
            timestamped.write_text(text, encoding="utf-8")
            stable.write_text(text, encoding="utf-8")
        except Exception as fe:
            logger.error("Universe delta fallback write failed: %s", fe)
        logger.warning("Universe delta post failed (%s); message follows:\n%s", reason, message)

    if not webhook_url:
        _write_fallback("no SLACK_WEBHOOK_COVERAGE configured")
        return {"posted": False, "reason": "no webhook configured"}

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen_with_retry(req, timeout=15, label="Universe delta") as resp:
            if resp.status == 200:
                logger.info("Universe delta posted to %s", COVERAGE_CHANNEL)
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
