"""Tests for the weekly Slack metrics table.

The table exists so JP can tell "the universe moved five rows" apart from "here
are five companies". Every test below pins a way it could quietly state a number
that is wrong rather than saying it does not know one -- which in a counters
table is indistinguishable from the truth.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reporting import slack_blocks, weekly_metrics as wm  # noqa: E402

LEDGER_HEAD = ("ticker,company,exchange,market_cap,sector,subsector,trigger,"
               "first_proposed,pending_since,last_seen,status,decision_date,"
               "decision_source,slack_thread_ts,reason,notes\n")


def _root(tmp_path, ledger_rows="", runs="", universe=1352, backups=None):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "candidate_ledger.csv").write_text(
        LEDGER_HEAD + ledger_rows, encoding="utf-8")
    (tmp_path / "data" / "coverage_universe_tickers.csv").write_text(
        "Ticker,Company\n" + "".join(f"T{i},C{i}\n" for i in range(universe)),
        encoding="utf-8")
    if runs:
        (tmp_path / "run_log.csv").write_text(
            "timestamp,command,steps_run,steps_ok,steps_failed,tickers_added,notes\n"
            + runs, encoding="utf-8")
    if backups:
        (tmp_path / "data" / "backups").mkdir(exist_ok=True)
        for stamp, n in backups:
            (tmp_path / "data" / "backups" /
             f"coverage_universe_tickers_{stamp}_120000.csv").write_text(
                "Ticker,Company\n" + "".join(f"T{i},C{i}\n" for i in range(n)),
                encoding="utf-8")
    return tmp_path


def _row(ticker, status, decided, trigger="IPO"):
    return (f"{ticker},{ticker} Inc,Nasdaq,2000000000,Tech,Software,{trigger},"
            f"{decided},,,{status},{decided},auto,,,notes for {ticker}\n")


# ------------------------------------------------------------------- universe


def test_before_and_after_come_from_the_reports_own_header():
    m = wm.collect("**Universe** 1,347 → 1,352", report_date="2026-09-04")
    assert (m.universe_before, m.universe_after, m.universe_delta) == (1347, 1352, 5)
    assert m.sources["universe"] == "report header"


def test_an_arrow_free_header_still_parses():
    """The header's punctuation has changed twice; the regex must not care."""
    m = wm.collect("**Universe** 1,347 - 1,352", report_date="2026-09-04")
    assert (m.universe_before, m.universe_after) == (1347, 1352)


def test_a_missing_header_falls_back_to_the_csv_and_a_backup(tmp_path):
    root = _root(tmp_path, universe=1352, backups=[("20260828", 1347)])
    m = wm.collect("", report_date="2026-09-04", root=root)
    assert (m.universe_before, m.universe_after) == (1347, 1352)


def test_an_uncomputable_metric_renders_na_not_zero():
    """A confident zero here reads as 'nothing happened this week'."""
    m = wm.Metrics()
    rows = wm.as_rows(m)
    assert all("n/a" in " ".join(r) for r in rows[1:])
    assert "0" not in rows[1][1]


# --------------------------------------------------------------------- window


def test_the_window_start_is_exclusive(tmp_path):
    """08-28 is the previous report's end date; counting it twice reported six
    adds against a report whose own decisions section said five."""
    root = _root(tmp_path, ledger_rows=_row("AAA", "approved", "2026-08-28")
                 + _row("BBB", "approved", "2026-09-02"))
    m = wm.collect("", report_date="2026-09-04", root=root)
    assert m.added_week == 1


# ----------------------------------------------------------------- ledger ytd


def test_ytd_is_labelled_with_the_date_the_ledger_actually_opens(tmp_path):
    """June-to-date presented as 'YTD' understates the year by five months."""
    root = _root(tmp_path, ledger_rows=_row("AAA", "approved", "2026-06-19"))
    m = wm.collect("", report_date="2026-09-04", root=root)
    assert m.ytd_start == "2026-06-19"
    assert "not January 1" in m.coverage_note
    assert wm.as_rows(m)[0][3] == "Since 06-19"


def test_a_ledger_that_does_reach_january_says_nothing_extra(tmp_path):
    root = _root(tmp_path, ledger_rows=_row("AAA", "approved", "2026-01-01"))
    m = wm.collect("", report_date="2026-09-04", root=root)
    assert m.coverage_note == ""


def test_russell_triggers_collapse_to_one_row(tmp_path):
    root = _root(tmp_path,
                 ledger_rows=_row("A", "approved", "2026-07-01", "Russell 1000 addition")
                 + _row("B", "approved", "2026-07-02", "Russell 2000 addition")
                 + _row("C", "approved", "2026-07-03", "Russell addition"))
    m = wm.collect("", report_date="2026-09-04", root=root)
    assert dict(m.top_triggers) == {"Russell addition": 3}


# ------------------------------------------------------ universe growth source


def test_universe_growth_is_measured_from_backups_not_the_run_log(tmp_path):
    """`run_log.tickers_added` reads 0 on every 2026 run while the ledger records
    42 approvals -- it tracks a counter the auto-add path never increments, and
    sourcing from it printed `+0` beside a visible list of added names."""
    runs = "".join(f"2026-0{i}-01T08:00:00,weekly-universe,a,a,,0,\n"
                   for i in range(1, 9))
    root = _root(tmp_path, universe=1352, runs=runs,
                 backups=[("20260411", 1091), ("20260828", 1347)])
    m = wm.collect("", report_date="2026-09-04", root=root)
    assert m.universe_added_ytd == 261
    assert m.universe_ytd_start == "2026-04-11"


def test_the_universe_row_carries_its_own_start_date_when_it_differs(tmp_path):
    root = _root(tmp_path, universe=1352,
                 ledger_rows=_row("AAA", "approved", "2026-06-19"),
                 backups=[("20260411", 1091)])
    m = wm.collect("", report_date="2026-09-04", root=root)
    assert "fr 04-11" in wm.as_rows(m)[1][3]


# --------------------------------------------------------------------- render


def test_the_table_is_narrow_enough_for_slack_to_align_it(tmp_path):
    root = _root(tmp_path, universe=1352,
                 ledger_rows=_row("AAA", "approved", "2026-09-02"),
                 backups=[("20260411", 1091)])
    m = wm.collect("**Universe** 1,347 → 1,352", report_date="2026-09-04", root=root)
    assert slack_blocks.is_narrow(wm.as_rows(m))


def test_summary_line_states_no_number_it_does_not_have():
    assert wm.summary_line(wm.Metrics()) == ""


# ------------------------------------------- the run-rate, from the 09-06 review


def test_the_rate_divides_by_calendar_weeks_not_run_days(tmp_path):
    """It printed '42 added over 25 weekly runs (1.7/week)' against a true
    3.8/week: numerator counted approvals since the ledger start, denominator
    counted distinct run DAYS since Jan 1 -- including mid-week re-runs, so
    08-06/07/08/09 scored four. A rate needs both spans to be the same span."""
    rows = "".join(_row(f"T{i}", "approved", "2026-07-0%d" % (i + 1))
                   for i in range(4))
    runs = "".join(f"2026-0{m}-0{d}T08:00:00,weekly-universe,a,a,,0,\n"
                   for m in (2, 3, 4) for d in (1, 2, 3))
    root = _root(tmp_path, ledger_rows=rows, runs=runs)
    m = wm.collect("", report_date="2026-08-01", root=root)
    # ledger opens 2026-07-01, report 2026-08-01 -> ~4 calendar weeks
    assert m.ytd_weeks == 4
    line = wm.summary_line(m)
    assert "over 4 weeks" in line
    assert "1.0/week" in line
    assert "runs" not in line


def test_run_days_are_still_recorded_but_no_longer_drive_the_rate(tmp_path):
    runs = "".join(f"2026-0{m}-01T08:00:00,weekly-universe,a,a,,0,\n"
                   for m in (2, 3, 4))
    root = _root(tmp_path, ledger_rows=_row("A", "approved", "2026-07-01"),
                 runs=runs)
    m = wm.collect("", report_date="2026-08-01", root=root)
    assert m.weeks_ytd == 3          # diagnostics
    assert m.ytd_weeks == 4          # the rate's base


def test_a_ledger_start_equal_to_the_report_date_cannot_divide_by_zero(tmp_path):
    root = _root(tmp_path, ledger_rows=_row("A", "approved", "2026-09-04"))
    m = wm.collect("", report_date="2026-09-04", root=root)
    assert m.ytd_weeks == 1
    wm.summary_line(m)               # must not raise
