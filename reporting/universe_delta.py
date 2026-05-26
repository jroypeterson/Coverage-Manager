"""Weekly universe delta — before/after snapshot, Slack post to #coverage.

Renders the universe state at the start of the weekly run, the deltas applied
during the run (additions, removals, sector reclassifications, position state
transitions, ISIN fills), and the final state — into a single Block Kit Slack
message posted to #coverage. Replaces the prior weekly performance-report email.

Baseline strategy: the orchestrator calls `capture_baseline_shas()` at the
top of `weekly_universe.main()`, before any mutation step runs. That SHA is
then threaded into the post-step, where `load_universe_snapshot(sha)` uses
`git show <sha>:<rel>` to read the pre-mutation file content. Working tree is
read by passing `commit_sha=None`. This is calendar-independent and survives
manual mid-week commits.

Pure functions (`compute_universe_delta`, `format_universe_delta_slack`) take
DataFrames in and return data structures / strings — tests don't need git.
"""

import json
import subprocess
import urllib.error
import urllib.request
from collections import defaultdict
from io import StringIO
from pathlib import Path

import pandas as pd

from config import CSV_PATH, SCRIPT_DIR, TODAY
from logging_utils import get_logger
from universe.positions import POSITIONS_PATH

logger = get_logger("reporting.universe_delta")

COVERAGE_CHANNEL = "#coverage"
FALLBACK_DIR = SCRIPT_DIR / ".coverage"

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


def capture_baseline_shas():
    """Capture the current git HEAD SHA at run start, before any mutation.

    Called from the top of `weekly_universe.main()`. Both the universe and
    positions CSVs are tracked under the same commit, so one SHA is enough.

    Returns a dict with the SHA, the resolved commit date (for display), and
    the standard rel-paths used for later diffing.
    """
    sha = _git_head_sha()
    return {
        "head_sha": sha,
        "head_date": _git_commit_date(sha),
        "universe_rel": "data/coverage_universe_tickers.csv",
        "positions_rel": "data/positions_and_researching.csv",
    }


# ── Snapshot loading ─────────────────────────────────────────────────────────


def _read_csv_text(text):
    if text is None:
        return None
    return pd.read_csv(StringIO(text))


def load_universe_snapshot(commit_sha=None, baseline=None):
    """Load the universe CSV from working tree (commit_sha=None) or a commit.

    Returns None if the file wasn't tracked at the given commit (e.g. very
    first weekly run with no prior history).
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
):
    """Compute a flat delta structure between two universe snapshots.

    Pure — no I/O, no git. Tests pass fixture DataFrames directly.

    Returns a dict with: added, removed, modified (flat: one row per
    ticker × field), position_changes, before_stats, after_stats,
    before_position_counts, after_position_counts, baseline_sha,
    baseline_date, today.
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

    Section order (top-down): header → After → Before → Delta sections.
    After leads because the current state is what the reader cares about first;
    Before is for context; the per-ticker delta sections follow for drill-down.
    The wire-payload helper `_split_into_section_blocks` chunks it if needed.
    """
    baseline_sha = delta.get("baseline_sha")
    baseline_date = delta.get("baseline_date")
    sha_disp = baseline_sha[:7] if baseline_sha else None
    today = delta.get("today", "")

    parts = [f":open_file_folder: *Coverage Universe — Weekly Update — {today}*"]

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

    # 2) Before — last-week context next.
    if baseline_sha:
        before_header = f" (committed state @ {sha_disp}, {baseline_date or 'previous commit'})"
    else:
        before_header = " (baseline unavailable — no prior commit found)"
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
        with urllib.request.urlopen(req, timeout=15) as resp:
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
