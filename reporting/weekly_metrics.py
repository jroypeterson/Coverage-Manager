"""Before / after / year-to-date counters for the weekly Slack lead.

**Why it exists.** JP, 2026-09-06: the Slack post should open with a table of
before-and-after and year-to-date metrics, *"visually offset from when you
actually add new stocks"*. The two things were reading as one thing -- a report
that says "Universe 1,347 -> 1,352" in a header line and then lists five names
underneath makes the movement and the names look like the same claim, and there
was nowhere to see whether five was a normal week.

**Every number is derived, never asserted.** Each metric names its own source and
falls back explicitly; a metric that cannot be computed renders as `n/a` rather
than as a zero, because a confident zero here reads as "nothing happened this
week" and that is the one lie this table must not tell.

**"Year to date" is honest about its own start.** `candidate_ledger.csv` opens on
2026-06-19 -- it does not go back to January. Labelling June-to-date as "YTD"
would understate the year by five months while looking authoritative, so the
column header carries the actual start date, and `coverage_note` says so in
words when the ledger starts after January 1.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEDGER = PROJECT_ROOT / "data" / "candidate_ledger.csv"
UNIVERSE = PROJECT_ROOT / "data" / "coverage_universe_tickers.csv"
RUN_LOG = PROJECT_ROOT / "run_log.csv"

# `**Universe** 1,347 → 1,352` in the report's own header line. Any dash, and an
# optional arrow, because the header's punctuation has changed twice.
_UNIVERSE_RE = re.compile(
    r"\*\*Universe\*\*\s*([\d,]+)\s*(?:->|→|—|–|-)\s*([\d,]+)")
# `— 1,352 rows`, the older header shape, gives the AFTER figure only.
_ROWS_RE = re.compile(r"[—–-]\s*([\d,]+)\s*rows")


@dataclass
class Metrics:
    universe_before: int | None = None
    universe_after: int | None = None
    added_week: int | None = None
    pending_week: int | None = None
    declined_week: int | None = None
    added_ytd: int | None = None
    proposed_ytd: int | None = None
    declined_ytd: int | None = None
    pending_now: int | None = None
    universe_added_ytd: int | None = None
    universe_ytd_start: str = ""
    ytd_start: str = ""
    weeks_ytd: int = 0          # distinct run DAYS; diagnostics only, not a rate
    ytd_weeks: int = 0          # calendar weeks from ytd_start -- the rate's base
    top_triggers: list[tuple[str, int]] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def universe_delta(self) -> int | None:
        if self.universe_before is None or self.universe_after is None:
            return None
        return self.universe_after - self.universe_before

    @property
    def coverage_note(self) -> str:
        """Says out loud when 'year to date' does not mean January."""
        if not self.ytd_start:
            return ""
        if self.ytd_start[5:] == "01-01":
            return ""
        return (f"Ledger-to-date counts start {self.ytd_start}, when the "
                f"candidate ledger opened -- not January 1.")


def _int(text: str) -> int | None:
    try:
        return int(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _read_csv(path: Path) -> list[dict]:
    """`[]` for a missing file; raises when it exists but cannot be read.

    Same reasoning as `added_names._read_csv`: an unreadable ledger that reads
    as an empty one turns every to-date counter into a confident zero.
    """
    if not path.exists():
        return []
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    except OSError as exc:
        raise OSError(f"{path.name}: {exc}") from exc


# ------------------------------------------------------------------- universe


def universe_row_count(path: Path = UNIVERSE) -> int | None:
    rows = _read_csv(path)
    return len(rows) or None


def _backups(root: Path) -> list[tuple[str, Path]]:
    """[(YYYYMMDD, path)] for dated universe backups, oldest first."""
    out: list[tuple[str, Path]] = []
    backups = root / "data" / "backups"
    if not backups.is_dir():
        return out
    for p in backups.glob("coverage_universe_tickers_*.csv"):
        m = re.search(r"_(\d{8})_", p.name)
        if m:
            out.append((m.group(1), p))
    return sorted(out)


def _prior_backup_count(root: Path, report_date: str) -> int | None:
    """Row count of the most recent universe backup taken BEFORE the report date.

    Used only when the report header does not carry the before/after pair itself.
    """
    stamp = report_date.replace("-", "")
    prior = [b for b in _backups(root) if b[0] < stamp]
    return universe_row_count(prior[-1][1]) if prior else None


def _earliest_backup_this_year(root: Path, year: str) -> tuple[str, int] | None:
    """-> (YYYY-MM-DD, row count) of the oldest backup in `year`.

    The universe row count is NOT taken from `run_log.tickers_added`. Measured
    2026-09-06: that column reads 0 on all 25 weekly runs of the year while the
    ledger records 42 approvals over the same span -- it tracks a discovery-step
    counter that the auto-add path does not increment. A metric sourced from it
    would print a confident `+0` next to a table of names that were, visibly,
    added. Backups are the physical record of the file itself.
    """
    for stamp, path in _backups(root):
        if stamp[:4] != year:
            continue
        n = universe_row_count(path)
        if n:
            return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}", n
    return None


# --------------------------------------------------------------------- ledger


def _in_window(value: str, start: str, end: str, *, inclusive: bool = True) -> bool:
    """`start` is EXCLUSIVE when inclusive=False.

    The weekly window matters: a review window of 08-28 -> 09-04 shares its start
    date with the previous report's end date, so an inclusive test counts every
    name decided on 08-28 in both weeks. That produced "6 added" against a report
    whose own decisions section said five.
    """
    if not value:
        return False
    v = value[:10]
    return (start <= v <= end) if inclusive else (start < v <= end)


def collect(report_md: str = "", *, report_date: str = "",
            window_start: str = "", root: Path = PROJECT_ROOT) -> Metrics:
    """Build the metric set for one weekly report.

    `report_md` is optional: without it the universe pair falls back to the CSV
    plus the most recent prior backup. `window_start` defaults to seven days
    before `report_date`, matching the report's own review window.
    """
    m = Metrics()

    # --- universe before / after -------------------------------------------
    hit = _UNIVERSE_RE.search(report_md or "")
    if hit:
        m.universe_before, m.universe_after = _int(hit.group(1)), _int(hit.group(2))
        m.sources["universe"] = "report header"
    else:
        m.universe_after = universe_row_count(root / "data" / "coverage_universe_tickers.csv")
        rows_hit = _ROWS_RE.search(report_md or "")
        if m.universe_after is None and rows_hit:
            m.universe_after = _int(rows_hit.group(1))
        if report_date:
            m.universe_before = _prior_backup_count(root, report_date)
        m.sources["universe"] = "universe CSV + prior backup"

    # --- window -------------------------------------------------------------
    end = report_date or date.today().isoformat()
    if not window_start:
        try:
            window_start = date.fromordinal(
                date.fromisoformat(end).toordinal() - 7).isoformat()
        except ValueError:
            window_start = end
    start_of_year = f"{end[:4]}-01-01"

    # --- ledger -------------------------------------------------------------
    ledger = _read_csv(root / "data" / "candidate_ledger.csv")
    if ledger:
        m.sources["ledger"] = "candidate_ledger.csv"
        first = min((r.get("first_proposed") or "")[:10]
                    for r in ledger if r.get("first_proposed"))
        m.ytd_start = max(first, start_of_year) if first else start_of_year

        def decided(r: dict) -> str:
            return (r.get("decision_date") or r.get("first_proposed") or "")[:10]

        week = [r for r in ledger
                if _in_window(decided(r), window_start, end, inclusive=False)]
        m.added_week = sum(1 for r in week if r.get("status") == "approved")
        m.declined_week = sum(1 for r in week if r.get("status") == "declined")
        m.pending_week = sum(1 for r in week if r.get("status") == "pending")

        ytd = [r for r in ledger if _in_window(decided(r), m.ytd_start, end)]
        m.added_ytd = sum(1 for r in ytd if r.get("status") == "approved")
        m.declined_ytd = sum(1 for r in ytd if r.get("status") == "declined")
        m.proposed_ytd = len(ytd)
        m.pending_now = sum(1 for r in ledger if r.get("status") == "pending")

        try:
            span = (date.fromisoformat(end) - date.fromisoformat(m.ytd_start)).days
            m.ytd_weeks = max(1, round(span / 7))
        except ValueError:
            m.ytd_weeks = 0

        counts: dict[str, int] = {}
        for r in ytd:
            if r.get("status") != "approved":
                continue
            # "Russell 1000 addition" and "Russell 2000 addition" are the same
            # rule; collapsing them keeps the table three rows instead of six.
            trig = (r.get("trigger") or "unknown").strip()
            trig = re.sub(r"^Russell.*addition$", "Russell addition", trig)
            trig = re.sub(r"\s*\(.*\)$", "", trig)
            counts[trig] = counts.get(trig, 0) + 1
        m.top_triggers = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:4]

    # --- universe growth over the year, from the backups themselves ----------
    oldest = _earliest_backup_this_year(root, end[:4])
    if oldest and m.universe_after is not None:
        m.universe_ytd_start, base = oldest
        m.universe_added_ytd = m.universe_after - base
        m.sources["universe_ytd"] = f"backup {m.universe_ytd_start}"

    # --- run log: cadence only ----------------------------------------------
    runs = _read_csv(root / "run_log.csv")
    if runs:
        ytd_runs = [r for r in runs
                    if _in_window((r.get("timestamp") or "")[:10], start_of_year, end)]
        # Distinct run DAYS, so "5 this week" can be read against a rate.
        m.weeks_ytd = len({(r.get("timestamp") or "")[:10] for r in ytd_runs})
        m.sources["runs"] = "run_log.csv"

    return m


# --------------------------------------------------------------------- render


def _cell(value: int | None, *, sign: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+,}" if sign else f"{value:,}"


def as_rows(m: Metrics) -> list[list[str]]:
    """-> markdown-table rows (header first) for `slack_blocks.render_aligned`.

    Four columns, short labels: this is read on a phone, and `is_narrow` will
    refuse anything wider than the monospace block can align.
    """
    ytd_label = f"Since {m.ytd_start[5:]}" if m.ytd_start else "To date"
    rows = [["Metric", "Before", "After", ytd_label]]
    # The universe row has its own start date: backups reach back further than
    # the ledger does, and silently borrowing the ledger's label would date the
    # figure five months wrong in the direction that flatters it.
    uni_ytd = _cell(m.universe_added_ytd, sign=True)
    if m.universe_added_ytd is not None and m.universe_ytd_start[5:] != m.ytd_start[5:]:
        uni_ytd += f" fr {m.universe_ytd_start[5:]}"
    rows.append(["Universe rows",
                 _cell(m.universe_before),
                 _cell(m.universe_after),
                 uni_ytd])
    rows.append(["Names added",
                 "-",
                 _cell(m.added_week),
                 _cell(m.added_ytd)])
    rows.append(["Awaiting you",
                 "-",
                 _cell(m.pending_now),
                 _cell(m.proposed_ytd) + " proposed"])
    rows.append(["Declined",
                 "-",
                 _cell(m.declined_week),
                 _cell(m.declined_ytd)])
    return rows


def summary_line(m: Metrics) -> str:
    """One-sentence framing under the table. Never states a number it lacks."""
    bits = []
    d = m.universe_delta
    if d is not None and m.universe_after is not None:
        bits.append(f"Universe {m.universe_before:,} -> {m.universe_after:,} ({d:+,})")
    # Calendar weeks between the ledger's own start and this report -- NOT the
    # run-log's day count. Dividing approvals-since-06-19 by run-days-since-Jan-1
    # printed "1.7/week" against a true 3.8/week: two different periods, and the
    # denominator counted mid-week re-runs (08-06/07/08/09 are one week, four
    # rows). A rate is a ratio of two spans and both have to be the same span.
    if m.added_ytd is not None and m.ytd_weeks:
        rate = m.added_ytd / m.ytd_weeks
        bits.append(f"{m.added_ytd:,} added over {m.ytd_weeks} weeks "
                    f"({rate:.1f}/week)")
    if m.top_triggers:
        bits.append("mostly " + ", ".join(f"{k} {v}" for k, v in m.top_triggers))
    return " · ".join(bits)
