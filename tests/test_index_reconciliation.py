"""Coverage-vs-index reconciliation — the join guard and the honest baseline.

Board #354's last piece. Two things here are not cosmetic: the join is restricted
to US-listed rows because a bare ticker is not an identity, and the comparison
names the date it compared against because "since last week" is false for the
Russell lane four weeks in five.
"""
import json

import pytest

from universe import index_reconciliation as ir


UNIVERSE_HEADER = "Ticker,Company Name,Country (Listing)\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ir, "MEMBERSHIP_DIR", tmp_path / "membership")
    (tmp_path / "membership").mkdir()
    return tmp_path


def _universe(env, rows):
    p = env / "universe.csv"
    p.write_text(UNIVERSE_HEADER + "".join(f"{t},{n},{c}\n" for t, n, c in rows),
                 encoding="utf-8")
    return p


def _snapshot(env, key, as_of, tickers, *, latest=False):
    doc = {"schema_version": 3, "key": key, "index": key.upper(), "as_of": as_of,
           "count": len(tickers),
           "holdings": [{"ticker": t, "name": t} for t in tickers]}
    name = f"{key}_latest.json" if latest else f"{key}_{as_of}.json"
    (ir.MEMBERSHIP_DIR / name).write_text(json.dumps(doc), encoding="utf-8")


# --- ⛑ the identity guard ----------------------------------------------------

def test_a_foreign_listed_row_is_never_joined_on_its_bare_ticker(env):
    """⛑ MEASURED ON THE LIVE FILES, 2026-09-10, NOT INVENTED.

    `CSL` is CSL Ltd on the ASX in the coverage universe and Carlisle Companies
    in the Russell 1000. `UCB` is UCB SA in Brussels and United Community Banks
    in the Russell 2000. A bare ticker join reported both as index constituents;
    restricting the universe side to US listings removes both and costs nothing,
    because every index collected here is a US-listed fund's US holdings.
    """
    csv_path = _universe(env, [
        ("CSL", "CSL Ltd", "Australia"),
        ("UCB", "UCB SA", "Belgium"),
        ("MRK", "Merck", "United States"),
    ])
    _snapshot(env, "r1000", "2026-07-31", ["CSL", "UCB", "MRK"])
    rows = ir.reconcile(csv_path)
    assert len(rows) == 1
    assert rows[0]["covered_tickers"] == ["MRK"]
    assert rows[0]["universe_us"] == 1


def test_eafe_is_excluded_from_the_reconciliation_entirely(env):
    """⛑ ITS TICKERS ARE LOCAL EXCHANGE LINES AND IT CARRIES NO ISIN — `ROP` is
    Roche, not Roper. A US-listing filter cannot fix that; it needs a real
    (ticker, exchange) resolver, so EAFE is not in scope here at all."""
    csv_path = _universe(env, [("ROP", "Roper Technologies", "United States")])
    _snapshot(env, "eafe", "2026-09-08", ["ROP"])
    assert "eafe" not in ir.INDICES
    assert ir.reconcile(csv_path) == []


# --- ⛑ the baseline ----------------------------------------------------------

def test_one_snapshot_reports_NO_PRIOR_rather_than_no_change(env):
    """⛑ AN ABSENT BASELINE IS NOT A FINDING OF NO CHANGE. The archive started
    2026-09-08, so this is the normal case for now, not an edge case."""
    csv_path = _universe(env, [("MRK", "Merck", "United States")])
    _snapshot(env, "sp500", "2026-09-08", ["MRK"])
    row = ir.reconcile(csv_path)[0]
    assert row["prev_as_of"] is None
    assert row["entered"] is None and row["left"] is None
    assert "first snapshot" in ir.format_slack([row])
    assert "first snapshot" in ir.format_email([row])


def test_the_comparison_names_its_date_rather_than_claiming_last_week(env):
    """⛑ VANGUARD PUBLISHES MONTH-END HOLDINGS, so `r1000_latest` carries the
    same as_of for four or five consecutive weekly runs. "since last week" would
    be false on most of them and the line would look like a broken diff."""
    csv_path = _universe(env, [("AAA", "A", "United States"),
                               ("BBB", "B", "United States")])
    _snapshot(env, "r2000", "2026-06-30", ["AAA"])
    _snapshot(env, "r2000", "2026-07-31", ["BBB"])
    row = ir.reconcile(csv_path)[0]
    assert row["as_of"] == "2026-07-31" and row["prev_as_of"] == "2026-06-30"
    assert row["entered"] == ["BBB"] and row["left"] == ["AAA"]
    line = ir.format_slack([row])
    assert "since 2026-06-30" in line and "last week" not in line


def test_latest_is_not_counted_as_its_own_predecessor(env):
    """`<key>_latest.json` is a copy of the newest dated file. Counting it would
    make every week report "no change" against itself."""
    csv_path = _universe(env, [("AAA", "A", "United States")])
    _snapshot(env, "sp500", "2026-09-08", ["AAA"])
    _snapshot(env, "sp500", "2026-09-08", ["AAA"], latest=True)
    row = ir.reconcile(csv_path)[0]
    assert row["prev_as_of"] is None       # one DATED snapshot, so no baseline


def test_no_movement_between_two_snapshots_says_so(env):
    csv_path = _universe(env, [("AAA", "A", "United States")])
    _snapshot(env, "sp500", "2026-09-01", ["AAA"])
    _snapshot(env, "sp500", "2026-09-08", ["AAA"])
    row = ir.reconcile(csv_path)[0]
    assert row["entered"] == [] and row["left"] == []
    assert "no change since 2026-09-01" in ir.format_slack([row])


# --- degradation -------------------------------------------------------------

def test_an_empty_archive_yields_no_block_rather_than_an_error(env):
    csv_path = _universe(env, [("MRK", "Merck", "United States")])
    assert ir.reconcile(csv_path) == []
    assert ir.format_slack([]) == ""
    assert ir.format_email([]) == ""


def test_a_corrupt_snapshot_drops_that_index_and_keeps_the_others(env):
    csv_path = _universe(env, [("AAA", "A", "United States")])
    (ir.MEMBERSHIP_DIR / "sp500_2026-09-08.json").write_text("{oops",
                                                             encoding="utf-8")
    _snapshot(env, "r1000", "2026-07-31", ["AAA"])
    keys = [r["key"] for r in ir.reconcile(csv_path)]
    assert keys == ["r1000"]


def test_a_missing_universe_csv_yields_nothing_rather_than_raising(env):
    _snapshot(env, "sp500", "2026-09-08", ["AAA"])
    assert ir.reconcile(env / "does_not_exist.csv") == []


def test_the_weekly_step_posts_without_the_block_when_reconciliation_fails(monkeypatch):
    """⛑ THE STEP'S PRODUCT IS THE DELTA POST. A membership archive that is
    absent, unreadable or brand new must never cost the weekly universe post."""
    from reporting.universe_delta import format_universe_delta_slack
    delta = {"added": [], "removed": [], "modified": [], "position_changes": [],
             "today": "2026-09-11", "baseline_source": "snapshot",
             "baseline_label": "end of previous run", "baseline_caveat": None,
             "before_stats": {"total": 1354, "core_y": 300, "sector_counts": {}},
             "after_stats": {"total": 1354, "core_y": 300, "sector_counts": {}},
             "before_position_counts": {}, "after_position_counts": {}}
    msg = format_universe_delta_slack(delta, index_rows=None)
    assert "Coverage in the indices" not in msg
    assert "Week over week" in msg


def test_the_block_renders_into_the_slack_post_when_rows_exist():
    from reporting.universe_delta import format_universe_delta_slack
    delta = {"added": [], "removed": [], "modified": [], "position_changes": [],
             "today": "2026-09-11", "baseline_source": "snapshot",
             "baseline_label": "end of previous run", "baseline_caveat": None,
             "before_stats": {"total": 1354, "core_y": 300, "sector_counts": {}},
             "after_stats": {"total": 1354, "core_y": 300, "sector_counts": {}},
             "before_position_counts": {}, "after_position_counts": {}}
    rows = [{"key": "sp500", "as_of": "2026-09-08", "index_count": 503,
             "covered": 121, "covered_tickers": [], "prev_as_of": "2026-09-01",
             "entered": ["MRK"], "left": [], "universe_us": 1137}]
    msg = format_universe_delta_slack(delta, index_rows=rows)
    assert "Coverage in the indices" in msg
    assert "121 of 503" in msg and "entered" in msg
    # ⛑ It sits with the week-over-week block, not down in the state blocks: a
    # covered name leaving the Russell 2000 is forced selling, i.e. news.
    assert msg.index("Coverage in the indices") < msg.index("*After*")


def test_the_email_block_is_ascii_because_the_console_prints_it():
    """The module's `__main__` prints this to a cp1252 console under Task
    Scheduler, where a middot in the DATA is what breaks the run."""
    rows = [{"key": "r2000", "as_of": "2026-07-31", "index_count": 1986,
             "covered": 436, "covered_tickers": [], "prev_as_of": "2026-06-30",
             "entered": ["AAA"], "left": ["BBB"], "universe_us": 1137}]
    ir.format_email(rows).encode("ascii")
