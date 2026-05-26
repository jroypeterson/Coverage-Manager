"""Smoke tests for the weekly_report orchestrator."""

import csv

import pytest

import weekly_report


@pytest.fixture
def fixture_csv(tmp_path):
    csv_path = tmp_path / "coverage_universe_tickers.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Ticker": "AAPL",
                "Exchange": "NASDAQ",
                "Company Name": "Apple Inc",
                "Sector (JP)": "Tech",
                "Subsector (JP)": "Hardware",
            }
        )
    return csv_path


def test_main_dry_run_returns_standardized_shape(monkeypatch, fixture_csv):
    """A dry-run report call should return the standardized result dict shape."""
    # weekly_report._step_validate_readonly does `from config import CSV_PATH`
    # at call time, so we need to patch config.CSV_PATH directly.
    import config

    monkeypatch.setattr(config, "CSV_PATH", fixture_csv)

    result = weekly_report.main(dry_run=True, log_audit=False)

    assert result["command"] == "weekly-report"
    assert "date" in result
    assert "validation_passed" in result
    assert "steps" in result
    assert "artifacts" in result
    assert "non_successes" in result

    assert result["validation_passed"] is True
    assert set(result["steps"].keys()) == {"validate", "archive", "performance", "movers", "email"}
    assert result["steps"]["validate"] == "ok"
    # Dry run skips everything else
    assert "skipped" in result["steps"]["archive"]
    assert "skipped" in result["steps"]["performance"]
    assert "skipped" in result["steps"]["movers"]
    assert "skipped" in result["steps"]["email"]
    assert result["non_successes"] == []


def test_main_no_force_parameter():
    """The plan explicitly says weekly_report.main does NOT take a force parameter —
    gating belongs in the wrapper. Lock this in via signature inspection."""
    import inspect

    sig = inspect.signature(weekly_report.main)
    assert "force" not in sig.parameters


def test_email_step_skipped_when_email_disabled(monkeypatch, fixture_csv):
    """When config.EMAIL_ENABLED=False the email step must be skipped without
    invoking _step_email — the kill-switch is a normal operational flag, not a
    temporary state. Flip the flag back to re-enable; no other code changes."""
    import config

    monkeypatch.setattr(config, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)

    # Fail loudly if _step_email is invoked despite the flag being off
    def boom():
        raise AssertionError("_step_email must not run when EMAIL_ENABLED=False")
    monkeypatch.setattr(weekly_report, "_step_email", boom)

    # Run with dry_run=False so the email branch is actually exercised; patch
    # the upstream report-producing steps to no-op so we don't need real data.
    monkeypatch.setattr(weekly_report, "_step_archive_reports", lambda: {"moved": 0, "pruned": 0})
    monkeypatch.setattr(weekly_report, "_step_performance", lambda: {"ticker_count": 1, "fund_count": 1})
    monkeypatch.setattr(weekly_report, "_step_movers", lambda: {
        "flagged": 0, "html": None, "slack_posted": False,
    })

    result = weekly_report.main(dry_run=False, log_audit=False)

    assert "EMAIL_ENABLED=False" in result["steps"]["email"]
    assert result["non_successes"] == []


def test_email_step_skipped_when_email_enabled_but_skip_email_flag(monkeypatch, fixture_csv):
    """skip_email=True overrides regardless of EMAIL_ENABLED."""
    import config

    monkeypatch.setattr(config, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)

    monkeypatch.setattr(weekly_report, "_step_archive_reports", lambda: {"moved": 0, "pruned": 0})
    monkeypatch.setattr(weekly_report, "_step_performance", lambda: {"ticker_count": 1, "fund_count": 1})
    monkeypatch.setattr(weekly_report, "_step_movers", lambda: {
        "flagged": 0, "html": None, "slack_posted": False,
    })

    result = weekly_report.main(skip_email=True, dry_run=False, log_audit=False)
    assert result["steps"]["email"] == "skipped"
