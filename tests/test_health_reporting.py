"""Tests for the v1 health-reporting helpers.

Covers the workspace-wide HEALTH_REPORTING.md v1 contract for Coverage Manager:

  - reporting.slack.format_health_v1_message — message body rendering
  - reporting.slack.post_health_v1            — Slack post with §4.7 fallback
  - weekly_build._build_health_payload        — status mapping (ok/partial/error)
  - weekly_build.main try/finally             — heartbeat fires even on exception
"""

import json
from collections import OrderedDict
from datetime import datetime, timezone

import pytest

import weekly_build
from reporting import slack as slack_mod


# ── format_health_v1_message ─────────────────────────────────────────────────


def _base_payload(**overrides):
    payload = {
        "project": "Coverage Manager",
        "status": "ok",
        "cycle": "2026-05-03 weekly",
        "attempt": "1",
        "start_time_utc": "2026-05-03 08:14",
        "end_time_utc": "2026-05-03 08:42",
        "next_expected": "Fri 2026-05-10",
        "counters": ["1094 tickers in universe", "9/9 steps ok"],
        "artifacts": [
            {"path": "exports/universe.csv", "bytes": 412300},
        ],
        "warnings": [],
        "errors": [],
        "run_link": None,
        "tag": "health/v1",
    }
    payload.update(overrides)
    return payload


def test_format_health_v1_ok_includes_required_lines():
    msg = slack_mod.format_health_v1_message(_base_payload())
    assert ":white_check_mark:" in msg
    assert "*Coverage Manager — ok*" in msg
    assert "health/v1" in msg
    assert "cycle: 2026-05-03 weekly" in msg
    assert "attempt: 1" in msg
    assert "2026-05-03 08:14 → 08:42 UTC" in msg
    assert "next expected: Fri 2026-05-10" in msg
    assert "1094 tickers in universe" in msg
    assert "exports/universe.csv" in msg
    # No error block on a clean run
    assert "*Error:*" not in msg


def test_format_health_v1_partial_uses_warning_icon():
    msg = slack_mod.format_health_v1_message(_base_payload(status="partial"))
    assert ":warning:" in msg
    assert "*Coverage Manager — partial*" in msg


def test_format_health_v1_error_renders_error_block():
    payload = _base_payload(
        status="error",
        errors=["performance: failed: ConnectionError",
                "email: blocked: validation failed"],
    )
    msg = slack_mod.format_health_v1_message(payload)
    assert ":x:" in msg
    assert "*Error:*" in msg
    assert "```" in msg
    assert "performance: failed: ConnectionError" in msg
    assert "email: blocked: validation failed" in msg


def test_format_health_v1_rejects_unknown_status():
    with pytest.raises(ValueError, match="status must be"):
        slack_mod.format_health_v1_message(_base_payload(status="weird"))


def test_format_health_v1_warnings_only_render_when_present():
    msg_no_warn = slack_mod.format_health_v1_message(_base_payload())
    assert "*Warnings:*" not in msg_no_warn

    msg_with_warn = slack_mod.format_health_v1_message(
        _base_payload(warnings=["fell back to yfinance"])
    )
    assert "*Warnings:*" in msg_with_warn
    assert "fell back to yfinance" in msg_with_warn


# ── post_health_v1 (§4.7 fallback) ───────────────────────────────────────────


def test_post_health_v1_writes_fallback_when_no_webhook(tmp_path):
    fallback = tmp_path / "health" / "last_run.json"
    payload = _base_payload()

    result = slack_mod.post_health_v1(None, payload, fallback)

    assert result["posted"] is False
    assert "no webhook" in result["reason"].lower()
    assert fallback.exists()
    saved = json.loads(fallback.read_text(encoding="utf-8"))
    assert saved["project"] == "Coverage Manager"
    assert saved["status"] == "ok"
    assert saved["tag"] == "health/v1"


def test_post_health_v1_writes_fallback_on_network_error(tmp_path, monkeypatch):
    """If urlopen raises URLError, payload is still preserved locally."""
    import urllib.error

    fallback = tmp_path / ".health" / "last_run.json"

    def boom(*a, **kw):
        raise urllib.error.URLError("simulated network failure")

    monkeypatch.setattr(slack_mod.urllib.request, "urlopen", boom)

    result = slack_mod.post_health_v1(
        "https://hooks.slack.test/services/x", _base_payload(), fallback
    )

    assert result["posted"] is False
    assert "network error" in result["reason"]
    assert fallback.exists()


def test_post_health_v1_success_does_not_write_fallback(tmp_path, monkeypatch):
    """On a 200 response, no fallback file should be written."""
    fallback = tmp_path / ".health" / "last_run.json"

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        slack_mod.urllib.request, "urlopen", lambda req, timeout=15: FakeResp()
    )

    result = slack_mod.post_health_v1(
        "https://hooks.slack.test/services/x", _base_payload(), fallback
    )

    assert result["posted"] is True
    assert not fallback.exists()


def test_post_health_v1_sends_block_kit_payload(tmp_path, monkeypatch):
    """The wire payload must include `blocks` (Block Kit) so mrkdwn renders.

    Regression test for the smoke-test bug where the earnings-agent webhook
    was rendering :white_check_mark: and *bold* as literal text because we
    sent only `{"text": ...}`. Section blocks with type=mrkdwn always render.
    """
    captured = {}
    fallback = tmp_path / ".health" / "last_run.json"

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=15):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    monkeypatch.setattr(slack_mod.urllib.request, "urlopen", fake_urlopen)

    slack_mod.post_health_v1(
        "https://hooks.slack.test/services/x", _base_payload(), fallback
    )

    body = captured["body"]
    assert "blocks" in body, "must send Block Kit blocks for reliable rendering"
    assert "text" in body, "must include text fallback for notifications"
    # All blocks must be mrkdwn sections, not plain_text — that's what makes
    # *bold* and :emoji: render in apps that don't auto-parse text fields.
    assert all(b["type"] == "section" for b in body["blocks"])
    assert all(b["text"]["type"] == "mrkdwn" for b in body["blocks"])
    # The full message content is preserved across the block(s)
    rebuilt = "".join(b["text"]["text"] for b in body["blocks"])
    assert rebuilt == body["text"]


def test_build_health_v1_blocks_splits_when_over_3000_chars():
    """A single section's text is capped at 3000 chars by Slack. Long error
    blocks must split into multiple sections rather than being truncated."""
    long_line = "x" * 200 + "\n"
    huge_message = long_line * 20  # ~4000 chars
    blocks = slack_mod._build_health_v1_blocks(huge_message)
    assert len(blocks) >= 2
    for b in blocks:
        assert len(b["text"]["text"]) <= slack_mod._SLACK_SECTION_TEXT_MAX
    rebuilt = "".join(b["text"]["text"] for b in blocks)
    assert rebuilt == huge_message


# ── _build_health_payload — status mapping ───────────────────────────────────


def _ts(hour, minute):
    return datetime(2026, 5, 3, hour, minute, tzinfo=timezone.utc)


def test_build_payload_ok_status():
    steps = OrderedDict([
        ("validate", "ok"),
        ("archive", "ok"),
        ("performance", "ok"),
    ])
    payload = weekly_build._build_health_payload(
        steps, ["exports/universe.csv"], validation_passed=True,
        start_dt=_ts(8, 14), end_dt=_ts(8, 42),
    )
    assert payload["status"] == "ok"
    assert payload["tag"] == "health/v1"
    assert payload["cycle"].endswith(" weekly")
    assert payload["attempt"] == "1"


def test_build_payload_partial_when_validation_passed_with_non_successes():
    """Universe valid, but some report-side step blocked/failed → partial."""
    steps = OrderedDict([
        ("validate", "ok"),
        ("archive", "ok"),
        ("performance", "failed: provider timeout"),
        ("email", "blocked: previous step failed"),
    ])
    payload = weekly_build._build_health_payload(
        steps, [], validation_passed=True,
        start_dt=_ts(8, 14), end_dt=_ts(8, 42),
    )
    assert payload["status"] == "partial"
    assert any("performance: failed" in e for e in payload["errors"])
    assert any("email: blocked" in e for e in payload["errors"])


def test_build_payload_error_when_validation_failed():
    """validation_passed=False means the universe is broken → status=error."""
    steps = OrderedDict([
        ("validate", "ok"),  # the step ran, but validation_passed flag is False
        ("performance", "blocked: validation failed"),
    ])
    payload = weekly_build._build_health_payload(
        steps, [], validation_passed=False,
        start_dt=_ts(8, 14), end_dt=_ts(8, 21),
    )
    assert payload["status"] == "error"


def test_build_payload_error_when_uncaught_exception():
    payload = weekly_build._build_health_payload(
        OrderedDict(), [], validation_passed=False,
        start_dt=_ts(8, 14), end_dt=_ts(8, 14),
        exception=RuntimeError("boom"),
    )
    assert payload["status"] == "error"
    assert any("RuntimeError" in e for e in payload["errors"])
    assert any("crashed: RuntimeError" in c for c in payload["counters"])


def test_build_payload_required_fields_present():
    payload = weekly_build._build_health_payload(
        OrderedDict([("validate", "ok")]), [], validation_passed=True,
        start_dt=_ts(8, 14), end_dt=_ts(8, 42),
    )
    required = {
        "project", "status", "cycle", "attempt",
        "start_time_utc", "end_time_utc", "next_expected",
        "counters", "artifacts", "warnings", "errors", "tag",
    }
    assert required.issubset(payload.keys())


def test_next_friday_label_skips_today_when_already_friday():
    # Friday 2026-05-08 → next is 2026-05-15
    fri = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    assert weekly_build._next_friday_label(fri) == "Fri 2026-05-15"


def test_next_friday_label_finds_upcoming():
    # Monday 2026-05-04 → upcoming Friday is 2026-05-08
    mon = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    assert weekly_build._next_friday_label(mon) == "Fri 2026-05-08"


# ── try/finally heartbeat guarantee ──────────────────────────────────────────


def test_uncaught_exception_still_emits_error_heartbeat(monkeypatch):
    """If weekly_universe.main raises, weekly_build.main should still emit a
    heartbeat (with status=error) before re-raising."""
    posted = {}

    def fake_universe(**kwargs):
        raise RuntimeError("simulated universe crash")

    def capture_emit(payload, dry_run):
        posted["payload"] = payload
        posted["dry_run"] = dry_run
        return {"posted": False, "reason": "captured"}

    monkeypatch.setattr(weekly_build.weekly_universe, "main", fake_universe)
    monkeypatch.setattr(weekly_build, "_emit_health_heartbeat", capture_emit)

    # Use dry_run=False so the emit path is exercised; capture_emit does the gating.
    with pytest.raises(RuntimeError, match="simulated universe crash"):
        weekly_build.main(skip_discovery=True, dry_run=False)

    assert posted, "heartbeat was not emitted when main() crashed"
    assert posted["payload"]["status"] == "error"
    assert any("RuntimeError" in e for e in posted["payload"]["errors"])


def test_dry_run_skips_heartbeat_post(monkeypatch):
    """In dry_run mode the heartbeat is constructed but not posted."""
    captured = {}

    def fake_universe(**kwargs):
        return {
            "command": "weekly-universe",
            "date": "2026-05-03",
            "validation_passed": True,
            "steps": OrderedDict([("validate", "ok"), ("archive", "ok")]),
            "artifacts": [],
            "non_successes": [],
        }

    def fake_report(**kwargs):
        return {
            "command": "weekly-report",
            "date": "2026-05-03",
            "validation_passed": True,
            "steps": OrderedDict([("validate", "ok"), ("performance", "ok")]),
            "artifacts": [],
            "non_successes": [],
        }

    def fake_post_health_v1(*a, **kw):
        captured["called"] = True
        return {"posted": True, "reason": None}

    monkeypatch.setattr(weekly_build.weekly_universe, "main", fake_universe)
    monkeypatch.setattr(weekly_build.weekly_report, "main", fake_report)
    monkeypatch.setattr(weekly_build, "API_KEYS", {})
    monkeypatch.setattr(slack_mod, "post_health_v1", fake_post_health_v1)

    weekly_build.main(skip_discovery=True, dry_run=True)

    assert "called" not in captured, "heartbeat should NOT be posted on dry runs"
