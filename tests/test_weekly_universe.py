"""Smoke tests for the weekly_universe orchestrator."""

import csv
from pathlib import Path

import pytest

import weekly_universe


@pytest.fixture
def fixture_csv(tmp_path):
    """Write a small valid coverage CSV and return its path."""
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
        writer.writerow(
            {
                "Ticker": "MRNA",
                "Exchange": "NASDAQ",
                "Company Name": "Moderna Inc",
                "Sector (JP)": "Biopharma",
                "Subsector (JP)": "Biotech",
            }
        )
    return csv_path


def test_main_dry_run_skip_discovery_returns_standardized_shape(monkeypatch, fixture_csv):
    """A dry-run universe call should return the standardized result dict shape
    with validation_passed=True for a clean CSV."""
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)

    result = weekly_universe.main(skip_discovery=True, dry_run=True, log_audit=False)

    # Standardized shape
    assert result["command"] == "weekly-universe"
    assert "date" in result
    assert "validation_passed" in result
    assert "steps" in result
    assert "artifacts" in result
    assert "non_successes" in result

    # Clean fixture should pass validation
    assert result["validation_passed"] is True

    # All steps appear in the steps dict
    assert set(result["steps"].keys()) == {
        "validate",
        "archive",
        "discovery",
        "export_artifacts",
        "export_watchlist",
        "sigma_export",
    }
    assert result["steps"]["validate"] == "ok"
    assert result["steps"]["discovery"] == "skipped"
    # Dry run skips mutation steps
    assert "skipped" in result["steps"]["archive"]
    assert "skipped" in result["steps"]["export_artifacts"]
    assert "skipped" in result["steps"]["export_watchlist"]
    assert "skipped" in result["steps"]["sigma_export"]

    assert result["non_successes"] == []


def test_main_validation_failure_sets_validation_passed_false(monkeypatch, tmp_path):
    """A CSV with duplicate tickers should fail validation and set
    validation_passed=False, but the orchestrator should still return cleanly."""
    bad_csv = tmp_path / "bad.csv"
    with open(bad_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"],
        )
        writer.writeheader()
        # Duplicate ticker — hard validation error
        writer.writerow({"Ticker": "AAPL", "Exchange": "NASDAQ", "Company Name": "A", "Sector (JP)": "Tech", "Subsector (JP)": ""})
        writer.writerow({"Ticker": "AAPL", "Exchange": "NASDAQ", "Company Name": "B", "Sector (JP)": "Tech", "Subsector (JP)": ""})

    monkeypatch.setattr(weekly_universe, "CSV_PATH", bad_csv)

    result = weekly_universe.main(skip_discovery=True, dry_run=True, log_audit=False)

    assert result["validation_passed"] is False
    # validate step itself completed (it's the rules that failed, not the step)
    assert result["steps"]["validate"] == "ok"
