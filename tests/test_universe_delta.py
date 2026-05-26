"""Tests for the weekly universe delta module.

Covers:
  - capture_baseline_shas captured before mutation (orchestration-level)
  - compute_universe_delta: additions, removals (with delisted lookup),
    modifications (grouped per ticker in formatter), position changes
  - format_universe_delta_slack: grouping, capping, empty-week
  - _split_into_section_blocks: 3000-char limit
  - post_universe_delta: fallback files (timestamped + last) on no-webhook + network errors
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
