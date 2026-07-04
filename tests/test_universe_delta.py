"""Tests for the weekly universe delta module.

Covers:
  - capture_baseline_shas captured before mutation (orchestration-level)
  - compute_universe_delta: additions, removals (with delisted lookup),
    modifications (grouped per ticker in formatter), position changes,
    baseline caveat threading
  - format_universe_delta_slack: grouping, capping, empty-week, caveat
  - _split_into_section_blocks: 3000-char limit
  - post_universe_delta: fallback files (timestamped + last) on no-webhook + network errors
  - 2-tier baseline: load_baseline_* prefers snapshot file, falls back to git
  - write_delta_json + write_run_snapshot: end-of-run persistence
"""

import json
import urllib.error
from io import StringIO

import pandas as pd
import pytest

from reporting import universe_delta as ud


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _universe_df(rows):
    """Build a coverage-universe-shaped DataFrame from row dicts."""
    cols = [
        "Ticker", "Company Name", "Sector (JP)", "Subsector (JP)",
        "Sub-subsector (JP)", "Country (HQ)", "Core", "ISIN",
    ]
    return pd.DataFrame([{c: r.get(c, "") for c in cols} for r in rows])


def _positions_df(rows):
    return pd.DataFrame([{"Ticker": r["Ticker"], "Position": r["Position"]} for r in rows])


def _delisted_df(rows):
    cols = ["Ticker", "Company Name", "Reason", "Notes"]
    return pd.DataFrame([{c: r.get(c, "") for c in cols} for r in rows])


# ── compute_universe_delta ───────────────────────────────────────────────────


def test_compute_delta_pure_addition():
    before = _universe_df([
        {"Ticker": "AAPL", "Company Name": "Apple Inc", "Sector (JP)": "Tech"},
    ])
    after = _universe_df([
        {"Ticker": "AAPL", "Company Name": "Apple Inc", "Sector (JP)": "Tech"},
        {"Ticker": "NEWA", "Company Name": "Newco A", "Sector (JP)": "Biopharma",
         "Subsector (JP)": "Biotech"},
    ])

    delta = ud.compute_universe_delta(before, after)

    assert len(delta["added"]) == 1
    assert delta["added"][0]["ticker"] == "NEWA"
    assert delta["added"][0]["sector"] == "Biopharma"
    assert delta["added"][0]["subsector"] == "Biotech"
    assert delta["removed"] == []
    assert delta["modified"] == []
    assert delta["before_stats"]["total"] == 1
    assert delta["after_stats"]["total"] == 2


def test_compute_delta_removal_with_delisted_lookup():
    """A removed ticker that appears in delisted_tickers.csv carries its Reason."""
    before = _universe_df([
        {"Ticker": "AAPL", "Company Name": "Apple Inc"},
        {"Ticker": "ADVM", "Company Name": "Adverum Biotechnologies"},
        {"Ticker": "FOO",  "Company Name": "Foo Corp"},
    ])
    after = _universe_df([
        {"Ticker": "AAPL", "Company Name": "Apple Inc"},
    ])
    delisted = _delisted_df([
        {"Ticker": "ADVM", "Reason": "Acquired", "Notes": "Acquired by Eli Lilly for $3.56 + CVR"},
    ])

    delta = ud.compute_universe_delta(before, after, delisted_df=delisted)

    removed_by_ticker = {r["ticker"]: r for r in delta["removed"]}
    assert "ADVM" in removed_by_ticker
    assert "Acquired by Eli Lilly" in removed_by_ticker["ADVM"]["reason"]
    # FOO is not in the delisted archive — falls back to "manual removal"
    assert removed_by_ticker["FOO"]["reason"] == "manual removal"


def test_compute_delta_modified_grouped_by_ticker_in_formatter():
    """Data model is flat (one row per ticker × field). Formatter groups by ticker."""
    before = _universe_df([
        {"Ticker": "FOO", "Company Name": "Foo Corp", "Sector (JP)": "Other",
         "Subsector (JP)": "", "Core": ""},
    ])
    after = _universe_df([
        {"Ticker": "FOO", "Company Name": "Foo Corp", "Sector (JP)": "Biopharma",
         "Subsector (JP)": "Oncology", "Core": "Y"},
    ])

    delta = ud.compute_universe_delta(before, after)

    # Flat data: 3 changes for FOO (Sector, Subsector, Core)
    foo_changes = [m for m in delta["modified"] if m["ticker"] == "FOO"]
    assert len(foo_changes) == 3
    fields = {m["field"] for m in foo_changes}
    assert fields == {"Sector (JP)", "Subsector (JP)", "Core"}

    # Formatter output groups them onto one line per ticker
    msg = ud.format_universe_delta_slack(delta)
    foo_lines = [line for line in msg.splitlines() if line.startswith("• `FOO`")]
    assert len(foo_lines) == 1
    # All three field changes appear on that single line
    assert "Sector (JP):" in foo_lines[0]
    assert "Subsector (JP):" in foo_lines[0]
    assert "Core:" in foo_lines[0]
    # Semicolons join the multiple field changes
    assert ";" in foo_lines[0]


def test_compute_delta_isin_only_blank_to_nonblank():
    """ISIN counts as Modified only when transitioning blank → non-blank."""
    before = _universe_df([
        {"Ticker": "AAA", "ISIN": ""},
        {"Ticker": "BBB", "ISIN": "US1234567890"},
    ])
    after = _universe_df([
        {"Ticker": "AAA", "ISIN": "US9999999999"},  # blank → non-blank: counts
        {"Ticker": "BBB", "ISIN": "US0000000000"},  # non-blank → different: does NOT count
    ])

    delta = ud.compute_universe_delta(before, after)
    isin_changes = [m for m in delta["modified"] if m["field"] == "ISIN"]
    assert len(isin_changes) == 1
    assert isin_changes[0]["ticker"] == "AAA"


def test_compute_delta_position_changes_bounded():
    """Many position changes are bounded in the formatter, not the data model."""
    n = 25
    before_rows = [{"Ticker": f"T{i:02d}", "Position": "Researching"} for i in range(n)]
    after_rows = [{"Ticker": f"T{i:02d}", "Position": "Portfolio"} for i in range(n)]
    universe_rows = [{"Ticker": f"T{i:02d}"} for i in range(n)]

    before_pos = _positions_df(before_rows)
    after_pos = _positions_df(after_rows)
    universe = _universe_df(universe_rows)

    delta = ud.compute_universe_delta(
        universe, universe,
        before_positions_df=before_pos, after_positions_df=after_pos,
    )
    # Data model carries all 25
    assert len(delta["position_changes"]) == n

    # Formatter caps at MAX_POSITION_CHANGES with an overflow indicator
    msg = ud.format_universe_delta_slack(delta)
    pos_section = msg.split("*Position changes")[1].split("\n\n")[0]
    bullet_lines = [l for l in pos_section.splitlines() if l.startswith("• ")]
    assert len(bullet_lines) == ud.MAX_POSITION_CHANGES
    assert f"+{n - ud.MAX_POSITION_CHANGES} more" in msg


def test_compute_delta_empty_week_renders_no_changes():
    """No additions/removals/mods/position changes still produces a valid post."""
    df = _universe_df([{"Ticker": "AAPL", "Company Name": "Apple Inc"}])
    delta = ud.compute_universe_delta(df, df)
    msg = ud.format_universe_delta_slack(delta)
    assert "_No changes this week._" in msg
    assert "*Before*" in msg
    assert "*After*" in msg


def test_format_delta_section_leads_the_post():
    """JP 2026-07-04: week-over-week diffs come FIRST, before After/Before."""
    before = _universe_df([{"Ticker": "AAPL", "Company Name": "Apple Inc"}])
    after = _universe_df([
        {"Ticker": "AAPL", "Company Name": "Apple Inc"},
        {"Ticker": "NEWA", "Company Name": "Newco A"},
    ])
    delta = ud.compute_universe_delta(before, after)
    msg = ud.format_universe_delta_slack(delta)
    assert msg.index("*Week over week*") < msg.index("*After*")
    assert msg.index("*After*") < msg.index("*Before*")


def test_format_empty_week_no_changes_leads_the_post():
    """The explicit no-changes line sits up top, not buried after state blocks."""
    df = _universe_df([{"Ticker": "AAPL", "Company Name": "Apple Inc"}])
    delta = ud.compute_universe_delta(df, df)
    msg = ud.format_universe_delta_slack(delta)
    assert msg.index("_No changes this week._") < msg.index("*After*")


# ── YTD summary ──────────────────────────────────────────────────────────────


def _ytd_payload(added=0, removed=0, modified_tickers=0, position_changes=0,
                 before_total=100, after_total=100):
    return {
        "added": [{"ticker": f"A{i}"} for i in range(added)],
        "removed": [{"ticker": f"R{i}"} for i in range(removed)],
        "modified": [{"ticker": f"M{i}", "field": "Core", "old": "", "new": "Y"}
                     for i in range(modified_tickers)],
        "position_changes": [{"ticker": f"P{i}"} for i in range(position_changes)],
        "before_stats": {"total": before_total},
        "after_stats": {"total": after_total},
    }


def test_compute_ytd_summary_aggregates_across_runs():
    payloads = [
        ("2026-01-09", _ytd_payload(added=2, removed=1, before_total=100, after_total=101)),
        ("2026-02-13", _ytd_payload(added=0, removed=0, before_total=101, after_total=101)),
        ("2026-07-03", _ytd_payload(added=3, removed=2, modified_tickers=4,
                                    position_changes=1, before_total=101, after_total=102)),
    ]
    ytd = ud.compute_ytd_summary(payloads)
    assert ytd["added"] == 5
    assert ytd["removed"] == 3
    assert ytd["modified_tickers"] == 4
    assert ytd["position_changes"] == 1
    assert ytd["start_total"] == 100
    assert ytd["end_total"] == 102
    assert ytd["net"] == 2
    assert ytd["first_date"] == "2026-01-09"
    assert ytd["runs"] == 3


def test_compute_ytd_summary_returns_none_on_empty_history():
    assert ud.compute_ytd_summary([]) is None


def test_load_ytd_delta_history_filters_to_current_year_and_skips_bad_files(tmp_path):
    good = {"reason": None, "delta": _ytd_payload(added=1)}
    (tmp_path / "universe_delta_2026-01-09.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "universe_delta_2026-03-06.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "universe_delta_2025-12-19.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "universe_delta_2026-02-13.json").write_text("{not json", encoding="utf-8")
    payloads = ud.load_ytd_delta_history(fallback_dir=tmp_path, today="2026-07-04")
    dates = [d for d, _ in payloads]
    assert dates == ["2026-01-09", "2026-03-06"]  # 2025 excluded, bad file skipped
    assert all(len(p["added"]) == 1 for _, p in payloads)


def test_format_includes_ytd_block_when_provided():
    df = _universe_df([{"Ticker": "AAPL", "Company Name": "Apple Inc"}])
    delta = ud.compute_universe_delta(df, df)
    ytd = ud.compute_ytd_summary([
        ("2026-01-09", _ytd_payload(added=2, removed=1, before_total=100, after_total=101)),
    ])
    msg = ud.format_universe_delta_slack(delta, ytd=ytd)
    assert "*Year to date* (since 2026-01-09 · 1 run)" in msg
    assert "+2 added · −1 removed · net +1 tickers (100 → 101)" in msg
    # YTD renders after the state blocks
    assert msg.index("*Year to date*") > msg.index("*Before*")


def test_format_omits_ytd_block_when_none():
    df = _universe_df([{"Ticker": "AAPL", "Company Name": "Apple Inc"}])
    delta = ud.compute_universe_delta(df, df)
    msg = ud.format_universe_delta_slack(delta, ytd=None)
    assert "*Year to date*" not in msg


# ── _split_into_section_blocks ───────────────────────────────────────────────


def test_split_into_section_blocks_respects_3000_char_limit():
    long_line = "x" * 200 + "\n"
    huge = long_line * 20  # ~4000 chars
    blocks = ud._split_into_section_blocks(huge)
    assert len(blocks) >= 2
    for b in blocks:
        assert b["type"] == "section"
        assert b["text"]["type"] == "mrkdwn"
        assert len(b["text"]["text"]) <= ud._SLACK_SECTION_TEXT_MAX
    rebuilt = "".join(b["text"]["text"] for b in blocks)
    assert rebuilt == huge


# ── post_universe_delta — fallback files (timestamped + stable) ──────────────


def _minimal_delta(today="2026-05-29"):
    """Smallest valid delta payload for post_universe_delta exercising."""
    return {
        "added": [],
        "removed": [],
        "modified": [],
        "position_changes": [],
        "before_stats": {"total": 1, "core_y": 0, "sector_counts": {}},
        "after_stats": {"total": 1, "core_y": 0, "sector_counts": {}},
        "before_position_counts": {},
        "after_position_counts": {},
        "baseline_sha": "abc1234567",
        "baseline_date": "2026-05-22",
        "today": today,
    }


def test_post_universe_delta_no_webhook_writes_both_fallback_files(tmp_path):
    delta = _minimal_delta(today="2026-05-29")
    result = ud.post_universe_delta(None, delta, fallback_dir=tmp_path)

    assert result["posted"] is False
    assert "no webhook" in result["reason"].lower()
    timestamped = tmp_path / "universe_delta_2026-05-29.json"
    stable = tmp_path / "last_universe_delta.json"
    assert timestamped.exists()
    assert stable.exists()

    payload_ts = json.loads(timestamped.read_text(encoding="utf-8"))
    payload_st = json.loads(stable.read_text(encoding="utf-8"))
    # Both files carry the same content this run
    assert payload_ts["today"] == "2026-05-29"
    assert payload_st["today"] == "2026-05-29"
    assert payload_ts["delta"]["baseline_sha"] == "abc1234567"


def test_post_universe_delta_network_failure_writes_both_fallback_files(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.URLError("simulated network failure")

    monkeypatch.setattr(ud.urllib.request, "urlopen", boom)

    delta = _minimal_delta(today="2026-05-29")
    result = ud.post_universe_delta(
        "https://hooks.slack.test/services/x", delta, fallback_dir=tmp_path,
    )

    assert result["posted"] is False
    assert "network error" in result["reason"]
    assert (tmp_path / "universe_delta_2026-05-29.json").exists()
    assert (tmp_path / "last_universe_delta.json").exists()


def test_post_universe_delta_success_sends_block_kit_payload(tmp_path, monkeypatch):
    """Successful post: Block Kit body with mrkdwn sections + text fallback."""
    captured = {}

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=15):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    monkeypatch.setattr(ud.urllib.request, "urlopen", fake_urlopen)

    delta = _minimal_delta()
    result = ud.post_universe_delta(
        "https://hooks.slack.test/services/x", delta, fallback_dir=tmp_path,
    )

    assert result["posted"] is True
    body = captured["body"]
    assert "blocks" in body
    assert "text" in body
    assert all(b["type"] == "section" for b in body["blocks"])
    assert all(b["text"]["type"] == "mrkdwn" for b in body["blocks"])
    # No fallback files on success
    assert not (tmp_path / "last_universe_delta.json").exists()


# ── Orchestration: baseline SHA captured BEFORE mutation ─────────────────────


def test_baseline_sha_captured_before_mutation(monkeypatch):
    """weekly_universe.main captures the git HEAD SHA at step 0, before any
    mutation step. The captured SHA must reflect pre-discovery state regardless
    of what HEAD does later in the run.

    This guards the core design invariant from the planning review: capture
    at start, diff at end, never depend on calendar arithmetic.
    """
    import weekly_universe

    call_order = []

    def fake_capture():
        call_order.append("capture")
        return {
            "head_sha": "deadbeef1234567",
            "head_date": "2026-05-22",
            "universe_rel": "data/coverage_universe_tickers.csv",
            "positions_rel": "data/positions_and_researching.csv",
        }

    # Patch every step that would normally mutate state, recording call order.
    def fake_step_validate():
        call_order.append("validate")
        return {"rows": 1, "errors": [], "warnings": [], "passed": True}

    def fake_step_archive_universe():
        call_order.append("archive")
        return {"moved": 0, "pruned": 0}

    def fake_step_discovery(dry_run=False):
        call_order.append("discovery")
        return {"status": "no valid candidates", "errors": 0}

    def fake_step_delisted_check():
        call_order.append("delisted_check")
        return {"checked": 1, "flagged": 0, "missing_data": 0, "report": "x"}

    def fake_step_export_artifacts(validation_result):
        call_order.append("export_artifacts")
        return {"artifacts": [], "ticker_count": 1}

    def fake_step_export_positions():
        call_order.append("export_positions")
        return {
            "artifacts": [], "entry_count": 0, "portfolio_count": 0,
            "researching_count": 0, "following_for_interest_count": 0,
            "ready_to_buy_count": 0, "ready_to_short_count": 0,
            "validation_passed": True,
        }

    def fake_step_export_reporting_calendar():
        call_order.append("export_reporting_calendar")
        return {"artifacts": [], "ticker_count": 1, "gating_eligible_count": 0}

    def fake_step_sigma_export():
        call_order.append("sigma_export")
        return {"status": "unchanged", "tickers": 1}

    captured_baseline = {}

    def fake_step_delta_slack(baseline):
        call_order.append("universe_delta_slack")
        captured_baseline["baseline"] = baseline
        return {
            "posted": True, "reason": None, "added": 0, "removed": 0,
            "modified": 0, "position_changes": 0, "before_total": 1, "after_total": 1,
            "baseline_source": "snapshot",
        }

    from reporting import universe_delta as ud_mod
    monkeypatch.setattr(ud_mod, "capture_baseline_shas", fake_capture)
    monkeypatch.setattr(weekly_universe, "_step_validate", fake_step_validate)
    monkeypatch.setattr(weekly_universe, "_step_archive_universe", fake_step_archive_universe)
    monkeypatch.setattr(weekly_universe, "_step_discovery", fake_step_discovery)
    monkeypatch.setattr(weekly_universe, "_step_delisted_check", fake_step_delisted_check)
    monkeypatch.setattr(weekly_universe, "_step_export_artifacts", fake_step_export_artifacts)
    monkeypatch.setattr(weekly_universe, "_step_export_positions", fake_step_export_positions)
    monkeypatch.setattr(weekly_universe, "_step_export_watchlist", fake_step_export_positions)
    monkeypatch.setattr(weekly_universe, "_step_export_reporting_calendar", fake_step_export_reporting_calendar)
    monkeypatch.setattr(weekly_universe, "_step_sigma_export", fake_step_sigma_export)
    monkeypatch.setattr(weekly_universe, "_step_universe_delta_slack", fake_step_delta_slack)

    result = weekly_universe.main(skip_discovery=False, dry_run=False, log_audit=False)

    # Baseline capture happens before ANY mutation step
    assert call_order[0] == "capture"
    # And the post-step receives that baseline
    assert captured_baseline["baseline"]["head_sha"] == "deadbeef1234567"
    # The new step is in the result
    assert "universe_delta_slack" in result["steps"]
    # And it runs LAST (after sigma_export)
    assert call_order[-1] == "universe_delta_slack"
    sigma_idx = call_order.index("sigma_export")
    delta_idx = call_order.index("universe_delta_slack")
    assert delta_idx > sigma_idx


# ── 2-tier baseline: snapshot preferred, git fallback ────────────────────────


def test_capture_baseline_shas_includes_dirty_paths(monkeypatch):
    """capture_baseline_shas runs `git status --porcelain` and includes any
    dirty universe/positions paths in the returned dict. Only matters when the
    git baseline is used (snapshot absent), but the detection is unconditional."""

    def fake_git_run(args, timeout=10):
        if args[:2] == ["rev-parse", "HEAD"]:
            return "abc1234567890\n"
        if args[:2] == ["show", "-s"]:
            return "2026-05-22\n"
        if args[0] == "status":
            return " M data/coverage_universe_tickers.csv\n M data/positions_and_researching.csv\n"
        return None

    monkeypatch.setattr(ud, "_git_run", fake_git_run)
    baseline = ud.capture_baseline_shas()

    assert baseline["head_sha"] == "abc1234567890"
    assert baseline["head_date"] == "2026-05-22"
    assert "data/coverage_universe_tickers.csv" in baseline["dirty_paths"]
    assert "data/positions_and_researching.csv" in baseline["dirty_paths"]


def test_capture_baseline_shas_no_dirty_paths_when_clean(monkeypatch):
    def fake_git_run(args, timeout=10):
        if args[:2] == ["rev-parse", "HEAD"]:
            return "abc1234567890\n"
        if args[:2] == ["show", "-s"]:
            return "2026-05-22\n"
        if args[0] == "status":
            return ""  # clean
        return None

    monkeypatch.setattr(ud, "_git_run", fake_git_run)
    baseline = ud.capture_baseline_shas()
    assert baseline["dirty_paths"] == []


def test_load_baseline_universe_prefers_snapshot_over_git(tmp_path, monkeypatch):
    """When `.coverage/last_run_universe.csv` exists, it wins over any git SHA."""
    snapshot = tmp_path / "last_run_universe.csv"
    _universe_df([
        {"Ticker": "FROM_SNAPSHOT", "Company Name": "From Snapshot"},
    ]).to_csv(snapshot, index=False)

    # Make git_show return a different value so we know which path was taken
    def fake_git_show(commit_sha, rel_path):
        return "Ticker,Company Name\nFROM_GIT,From Git\n"
    monkeypatch.setattr(ud, "_git_show", fake_git_show)

    df = ud.load_baseline_universe(snapshot_path=snapshot, commit_sha="deadbeef")
    assert list(df["Ticker"]) == ["FROM_SNAPSHOT"]


def test_load_baseline_universe_falls_back_to_git_when_no_snapshot(tmp_path, monkeypatch):
    """No snapshot → git is consulted via load_universe_snapshot/git_show."""
    nonexistent = tmp_path / "nope.csv"

    def fake_git_show(commit_sha, rel_path):
        return "Ticker,Company Name\nFROM_GIT,From Git\n"
    monkeypatch.setattr(ud, "_git_show", fake_git_show)

    df = ud.load_baseline_universe(snapshot_path=nonexistent, commit_sha="deadbeef")
    assert list(df["Ticker"]) == ["FROM_GIT"]


def test_load_baseline_universe_returns_none_when_neither_available(tmp_path):
    """No snapshot, no commit_sha → returns None for the baseline-unavailable path."""
    nonexistent = tmp_path / "nope.csv"
    assert ud.load_baseline_universe(snapshot_path=nonexistent, commit_sha=None) is None


# ── Caveat threading: dirty git baseline shows :warning: in message ──────────


def test_compute_delta_carries_caveat_through_to_format(monkeypatch):
    """The dirty-tree caveat string survives compute → format and renders as a
    :warning: line directly under the header."""
    before = _universe_df([{"Ticker": "AAPL", "Company Name": "Apple"}])
    after = _universe_df([{"Ticker": "AAPL", "Company Name": "Apple"}])

    delta = ud.compute_universe_delta(
        before, after,
        baseline_source="git",
        baseline_label="commit @ abc1234, 2026-05-22 (no snapshot found — bootstrap fallback)",
        baseline_caveat="Working tree was dirty at run start — pre-existing local edits may appear in this delta.",
    )

    assert delta["baseline_caveat"]
    msg = ud.format_universe_delta_slack(delta)
    assert ":warning:" in msg
    assert "dirty at run start" in msg


def test_format_no_caveat_when_absent():
    """No caveat → no :warning: line."""
    before = _universe_df([{"Ticker": "AAPL"}])
    after = _universe_df([{"Ticker": "AAPL"}])
    delta = ud.compute_universe_delta(before, after, baseline_source="snapshot")
    msg = ud.format_universe_delta_slack(delta)
    assert ":warning:" not in msg


def test_format_uses_baseline_label_when_provided():
    """baseline_label is rendered verbatim in the Before-block header."""
    before = _universe_df([{"Ticker": "AAPL"}])
    after = _universe_df([{"Ticker": "AAPL"}])
    delta = ud.compute_universe_delta(
        before, after,
        baseline_source="snapshot",
        baseline_label="end of previous run · 2026-05-22",
    )
    msg = ud.format_universe_delta_slack(delta)
    assert "end of previous run · 2026-05-22" in msg


def test_format_renders_baseline_unavailable_when_source_none():
    """baseline_source='none' produces the bootstrap-not-yet message."""
    df = _universe_df([{"Ticker": "AAPL"}])
    delta = ud.compute_universe_delta(df, df, baseline_source="none")
    msg = ud.format_universe_delta_slack(delta)
    assert "baseline unavailable" in msg


# ── write_delta_json / write_run_snapshot ────────────────────────────────────


def test_write_delta_json_writes_both_files_unconditionally(tmp_path):
    """write_delta_json is now the canonical place — always called before Slack
    post fires, not only on failure. Both timestamped + stable files written."""
    delta = _minimal_delta(today="2026-05-29")
    paths = ud.write_delta_json(delta, fallback_dir=tmp_path)

    timestamped = tmp_path / "universe_delta_2026-05-29.json"
    stable = tmp_path / "last_universe_delta.json"
    assert timestamped.exists()
    assert stable.exists()
    assert set(paths) == {timestamped, stable}

    payload = json.loads(stable.read_text(encoding="utf-8"))
    assert payload["today"] == "2026-05-29"
    assert payload["reason"] is None
    assert payload["delta"]["baseline_sha"] == "abc1234567"


def test_write_delta_json_reason_field_appears_when_set(tmp_path):
    delta = _minimal_delta()
    ud.write_delta_json(delta, fallback_dir=tmp_path, reason="slack returned 500")
    payload = json.loads((tmp_path / "last_universe_delta.json").read_text(encoding="utf-8"))
    assert payload["reason"] == "slack returned 500"


def test_write_run_snapshot_copies_working_tree_to_fallback_dir(tmp_path, monkeypatch):
    """write_run_snapshot copies both CSVs (when present) to .coverage/."""
    # Fake working-tree files
    fake_universe = tmp_path / "data" / "coverage_universe_tickers.csv"
    fake_positions = tmp_path / "data" / "positions_and_researching.csv"
    fake_universe.parent.mkdir(parents=True)
    fake_universe.write_text("Ticker,Company Name\nAAPL,Apple\n", encoding="utf-8")
    fake_positions.write_text("Ticker,Position\nAAPL,Portfolio\n", encoding="utf-8")

    monkeypatch.setattr(ud, "CSV_PATH", fake_universe)
    monkeypatch.setattr(ud, "POSITIONS_PATH", fake_positions)

    out_dir = tmp_path / ".coverage"
    written = ud.write_run_snapshot(fallback_dir=out_dir)

    universe_out = out_dir / "last_run_universe.csv"
    positions_out = out_dir / "last_run_positions.csv"
    assert universe_out.exists()
    assert positions_out.exists()
    assert "AAPL,Apple" in universe_out.read_text(encoding="utf-8")
    assert "AAPL,Portfolio" in positions_out.read_text(encoding="utf-8")
    assert set(written) == {universe_out, positions_out}


def test_write_run_snapshot_skips_positions_when_absent(tmp_path, monkeypatch):
    fake_universe = tmp_path / "data" / "coverage_universe_tickers.csv"
    fake_universe.parent.mkdir(parents=True)
    fake_universe.write_text("Ticker,Company Name\nAAPL,Apple\n", encoding="utf-8")

    nonexistent_positions = tmp_path / "data" / "missing.csv"
    monkeypatch.setattr(ud, "CSV_PATH", fake_universe)
    monkeypatch.setattr(ud, "POSITIONS_PATH", nonexistent_positions)

    out_dir = tmp_path / ".coverage"
    written = ud.write_run_snapshot(fallback_dir=out_dir)

    assert (out_dir / "last_run_universe.csv").exists()
    assert not (out_dir / "last_run_positions.csv").exists()
    assert len(written) == 1


def test_snapshot_mtime_date_returns_iso_string(tmp_path):
    snap = tmp_path / "last_run_universe.csv"
    snap.write_text("Ticker\nX\n", encoding="utf-8")
    date_str = ud.snapshot_mtime_date(snapshot_path=snap)
    # YYYY-MM-DD shape
    assert len(date_str) == 10
    assert date_str.count("-") == 2


def test_snapshot_mtime_date_returns_none_when_absent(tmp_path):
    assert ud.snapshot_mtime_date(snapshot_path=tmp_path / "nope.csv") is None


# ── Step-level: Slack failure raises so health goes partial ─────────────────


def test_step_raises_runtime_error_on_slack_post_failure(monkeypatch, tmp_path):
    """When post_universe_delta returns posted=False, _step_universe_delta_slack
    must raise so pipeline_utils.run_step records `failed: ...` and
    collect_non_successes flags the health heartbeat as partial."""
    import weekly_universe

    # Build a working-tree universe CSV the step can read
    fake_universe = tmp_path / "coverage_universe_tickers.csv"
    _universe_df([{"Ticker": "AAPL", "Company Name": "Apple"}]).to_csv(fake_universe, index=False)
    fake_positions = tmp_path / "positions_and_researching.csv"
    fake_positions.write_text("Ticker,Position\nAAPL,Portfolio\n", encoding="utf-8")
    fake_coverage_dir = tmp_path / ".coverage"

    # Patch the module-level paths the step + universe_delta use
    monkeypatch.setattr(ud, "CSV_PATH", fake_universe)
    monkeypatch.setattr(ud, "POSITIONS_PATH", fake_positions)
    monkeypatch.setattr(ud, "FALLBACK_DIR", fake_coverage_dir)
    monkeypatch.setattr(ud, "SNAPSHOT_UNIVERSE_PATH", fake_coverage_dir / "last_run_universe.csv")
    monkeypatch.setattr(ud, "SNAPSHOT_POSITIONS_PATH", fake_coverage_dir / "last_run_positions.csv")
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fake_universe)
    monkeypatch.setattr(weekly_universe, "DATA_DIR", tmp_path)

    # Force Slack post to fail
    monkeypatch.setattr(
        ud, "post_universe_delta",
        lambda webhook, delta, fallback_dir=None, ytd=None: {"posted": False, "reason": "slack returned 500"},
    )

    baseline = {
        "head_sha": None, "head_date": None,
        "universe_rel": "data/coverage_universe_tickers.csv",
        "positions_rel": "data/positions_and_researching.csv",
        "dirty_paths": [],
    }

    with pytest.raises(RuntimeError, match="Slack post failed"):
        weekly_universe._step_universe_delta_slack(baseline)

    # Both side-effects must still have happened before the raise
    assert (fake_coverage_dir / "last_universe_delta.json").exists()
    assert (fake_coverage_dir / "last_run_universe.csv").exists()


def test_step_uses_os_environ_first_then_api_keys(monkeypatch, tmp_path):
    """Webhook is resolved via os.environ first; .env API_KEYS is the fallback."""
    import os
    import weekly_universe

    fake_universe = tmp_path / "coverage_universe_tickers.csv"
    _universe_df([{"Ticker": "AAPL", "Company Name": "Apple"}]).to_csv(fake_universe, index=False)
    fake_positions = tmp_path / "positions_and_researching.csv"
    fake_positions.write_text("Ticker,Position\nAAPL,Portfolio\n", encoding="utf-8")
    fake_coverage_dir = tmp_path / ".coverage"

    monkeypatch.setattr(ud, "CSV_PATH", fake_universe)
    monkeypatch.setattr(ud, "POSITIONS_PATH", fake_positions)
    monkeypatch.setattr(ud, "FALLBACK_DIR", fake_coverage_dir)
    monkeypatch.setattr(ud, "SNAPSHOT_UNIVERSE_PATH", fake_coverage_dir / "last_run_universe.csv")
    monkeypatch.setattr(ud, "SNAPSHOT_POSITIONS_PATH", fake_coverage_dir / "last_run_positions.csv")
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fake_universe)
    monkeypatch.setattr(weekly_universe, "DATA_DIR", tmp_path)

    captured = {}
    def fake_post(webhook, delta, fallback_dir=None, ytd=None):
        captured["webhook"] = webhook
        return {"posted": True, "reason": None}
    monkeypatch.setattr(ud, "post_universe_delta", fake_post)

    monkeypatch.setenv("SLACK_WEBHOOK_COVERAGE", "FROM_OS_ENV")
    import config
    monkeypatch.setitem(config.API_KEYS, "SLACK_WEBHOOK_COVERAGE", "FROM_DOTENV")

    baseline = {
        "head_sha": None, "head_date": None,
        "universe_rel": "data/coverage_universe_tickers.csv",
        "positions_rel": "data/positions_and_researching.csv",
        "dirty_paths": [],
    }
    weekly_universe._step_universe_delta_slack(baseline)

    assert captured["webhook"] == "FROM_OS_ENV"

    # And with no OS env, falls back to .env
    monkeypatch.delenv("SLACK_WEBHOOK_COVERAGE")
    weekly_universe._step_universe_delta_slack(baseline)
    assert captured["webhook"] == "FROM_DOTENV"
