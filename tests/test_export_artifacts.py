"""Tests for the universe export artifact contract.

These tests pin the schema version and file shape so downstream consumers can
rely on a stable contract. If you bump schema_version, update the test.
"""

import csv
import json

import pytest

import weekly_universe


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


def test_export_step_writes_all_four_artifacts(monkeypatch, tmp_path, fixture_csv):
    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    validation_result = {
        "rows": 2,
        "errors": [],
        "warnings": ["test warning"],
        "passed": True,
    }

    result = weekly_universe._step_export_artifacts(validation_result)

    # Four files exist
    assert (exports_dir / "universe.csv").exists()
    assert (exports_dir / "universe_metadata.json").exists()
    assert (exports_dir / "universe_status.json").exists()
    assert (exports_dir / "manifest.json").exists()

    # Result advertises four artifacts and the right ticker count
    assert len(result["artifacts"]) == 4
    # ticker_count includes the SECTOR_ETFS hardcoded in build_metadata; the
    # CSV-derived count should be at least our 2 fixture tickers.
    assert result["ticker_count"] >= 2


def test_status_file_schema(monkeypatch, tmp_path, fixture_csv):
    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    validation_result = {
        "rows": 2,
        "errors": [],
        "warnings": ["test warning"],
        "passed": True,
    }
    weekly_universe._step_export_artifacts(validation_result)

    status = json.loads((exports_dir / "universe_status.json").read_text(encoding="utf-8"))

    # Required fields per the documented contract
    required_fields = {
        "schema_version",
        "dataset_version",
        "generated_at",
        "source_path",
        "row_count",
        "ticker_count",
        "validation_passed",
        "validation_errors",
        "validation_warnings",
        "last_discovery_run",
    }
    assert required_fields.issubset(status.keys())
    assert status["schema_version"] == 1
    assert status["validation_passed"] is True
    assert status["row_count"] == 2
    assert status["validation_warnings"] == ["test warning"]


def test_metadata_round_trip(monkeypatch, tmp_path, fixture_csv):
    """universe_metadata.json should match what build_metadata() produces from the same CSV."""
    from reporting.sigma_export import build_metadata

    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    weekly_universe._step_export_artifacts(
        {"rows": 2, "errors": [], "warnings": [], "passed": True}
    )

    on_disk = json.loads((exports_dir / "universe_metadata.json").read_text(encoding="utf-8"))
    expected = build_metadata(fixture_csv)
    assert on_disk == expected


def test_manifest_lists_all_files(monkeypatch, tmp_path, fixture_csv):
    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    weekly_universe._step_export_artifacts(
        {"rows": 2, "errors": [], "warnings": [], "passed": True}
    )

    manifest = json.loads((exports_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    listed_names = {f["name"] for f in manifest["files"]}
    assert listed_names == {
        "universe.csv",
        "universe_metadata.json",
        "universe_status.json",
        "manifest.json",
    }
