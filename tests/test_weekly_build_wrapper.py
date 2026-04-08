"""Integration tests for the weekly_build wrapper.

The wrapper's responsibilities are: ordering, gating on validation+force,
merging results, and audit logging. These tests stub out the universe and
report sub-orchestrators so we can exercise the wrapper logic without
touching real CSVs, providers, or external services.
"""

from collections import OrderedDict

import pytest

import weekly_build


def _make_universe_result(validation_passed=True, steps=None):
    return {
        "command": "weekly-universe",
        "date": "2026-04-08",
        "validation_passed": validation_passed,
        "steps": OrderedDict(steps or [
            ("validate", "ok"),
            ("archive", "ok"),
            ("discovery", "skipped"),
            ("export_artifacts", "ok"),
            ("sigma_export", "unchanged (411 tickers)"),
        ]),
        "artifacts": ["exports/universe.csv", "exports/universe_metadata.json"],
        "failures": [],
    }


def _make_report_result():
    return {
        "command": "weekly-report",
        "date": "2026-04-08",
        "validation_passed": True,
        "steps": OrderedDict([
            ("validate", "ok"),
            ("archive", "ok"),
            ("performance", "ok"),
            ("email", "ok"),
        ]),
        "artifacts": [],
        "failures": [],
    }


@pytest.fixture
def stub_orchestrators(monkeypatch):
    """Replace weekly_universe.main and weekly_report.main with call recorders."""
    calls = {"universe": [], "report": []}

    def fake_universe(**kwargs):
        calls["universe"].append(kwargs)
        return _make_universe_result(validation_passed=kwargs.get("_validation", True))

    def fake_report(**kwargs):
        calls["report"].append(kwargs)
        return _make_report_result()

    # Stub Slack so we don't try to send during tests
    def fake_slack(*a, **kw):
        return True

    monkeypatch.setattr(weekly_build.weekly_universe, "main", fake_universe)
    monkeypatch.setattr(weekly_build.weekly_report, "main", fake_report)
    # Empty API_KEYS so the slack branch is "no webhook" (skipped) — no network call.
    monkeypatch.setattr(weekly_build, "API_KEYS", {})
    return calls


# ── Gating logic ─────────────────────────────────────────────────────────────


def test_gate_report_normal():
    run, reason = weekly_build._gate_report(skip_performance=False, validation_passed=True, force=False)
    assert run is True
    assert reason is None


def test_gate_report_skip_performance():
    run, reason = weekly_build._gate_report(skip_performance=True, validation_passed=True, force=False)
    assert run is False
    assert reason == "skipped"


def test_gate_report_validation_failed_no_force():
    run, reason = weekly_build._gate_report(skip_performance=False, validation_passed=False, force=False)
    assert run is False
    assert reason == "blocked: validation failed"


def test_gate_report_force_overrides_validation_failure():
    run, reason = weekly_build._gate_report(skip_performance=False, validation_passed=False, force=True)
    assert run is True
    assert reason is None


def test_gate_report_skip_performance_dominates_force():
    """skip_performance is a hard skip; force should not override it."""
    run, reason = weekly_build._gate_report(skip_performance=True, validation_passed=False, force=True)
    assert run is False
    assert reason == "skipped"


# ── Wrapper integration ──────────────────────────────────────────────────────


def test_happy_path_runs_universe_then_report(stub_orchestrators):
    result = weekly_build.main(skip_discovery=True, dry_run=True)

    assert len(stub_orchestrators["universe"]) == 1
    assert len(stub_orchestrators["report"]) == 1
    # Universe was called with skip_discovery=True; report was called with skip_email default
    assert stub_orchestrators["universe"][0]["skip_discovery"] is True
    assert stub_orchestrators["report"][0]["skip_email"] is False

    # Combined result includes both halves
    assert "validate" in result["steps"]
    assert "performance" in result["steps"]
    assert "sigma_export" in result["steps"]
    assert result["validation_passed"] is True
    assert result["failures"] == []


def test_validation_failure_blocks_report_side(monkeypatch, stub_orchestrators):
    def fake_universe(**kwargs):
        stub_orchestrators["universe"].append(kwargs)
        return _make_universe_result(validation_passed=False)

    monkeypatch.setattr(weekly_build.weekly_universe, "main", fake_universe)

    result = weekly_build.main(skip_discovery=True, dry_run=True)

    # Report side was NOT called
    assert len(stub_orchestrators["report"]) == 0
    # Report-side steps appear in the combined dict with the blocked reason
    assert result["steps"]["performance"] == "blocked: validation failed"
    assert result["steps"]["email"] == "blocked: validation failed"
    assert result["validation_passed"] is False


def test_force_overrides_validation_failure(monkeypatch, stub_orchestrators):
    def fake_universe(**kwargs):
        stub_orchestrators["universe"].append(kwargs)
        return _make_universe_result(validation_passed=False)

    monkeypatch.setattr(weekly_build.weekly_universe, "main", fake_universe)

    result = weekly_build.main(skip_discovery=True, dry_run=True, force=True)

    # Report side WAS called despite failed validation
    assert len(stub_orchestrators["report"]) == 1
    assert result["steps"]["performance"] == "ok"


def test_skip_performance_skips_report_side_without_blocking(stub_orchestrators):
    result = weekly_build.main(skip_discovery=True, skip_performance=True, dry_run=True)

    assert len(stub_orchestrators["report"]) == 0
    assert result["steps"]["performance"] == "skipped"
    # Validation still passed (universe ran cleanly)
    assert result["validation_passed"] is True
