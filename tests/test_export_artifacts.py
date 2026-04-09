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

    # Result advertises four artifacts and the right ticker count.
    # Generic export contract: ticker_count must equal CSV row count exactly,
    # with no consumer-specific augmentation (no sigma-alert ETFs, etc.).
    assert len(result["artifacts"]) == 4
    assert result["ticker_count"] == 2


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
        "normalization_collisions",
        "collision_examples",
        "validation_passed",
        "validation_errors",
        "validation_warnings",
        "last_discovery_run",
    }
    assert required_fields.issubset(status.keys())
    assert status["schema_version"] == 1
    assert status["validation_passed"] is True
    assert status["row_count"] == 2
    # Generic contract: for a fixture without ticker normalization collisions,
    # ticker_count == row_count. The general invariant is `ticker_count +
    # normalization_collisions == row_count` (no consumer-specific augmentation
    # ever increases ticker_count above row_count - collisions).
    assert status["normalization_collisions"] == 0
    assert status["ticker_count"] == status["row_count"]
    assert status["collision_examples"] == []
    assert status["validation_warnings"] == ["test warning"]


def test_metadata_matches_generic_builder(monkeypatch, tmp_path, fixture_csv):
    """universe_metadata.json must exactly match the generic CSV-derived data
    from `universe.artifacts.build_universe_metadata` — no extra keys, no
    consumer-specific augmentation."""
    from universe.artifacts import build_universe_metadata

    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    weekly_universe._step_export_artifacts(
        {"rows": 2, "errors": [], "warnings": [], "passed": True}
    )

    on_disk = json.loads((exports_dir / "universe_metadata.json").read_text(encoding="utf-8"))
    expected = build_universe_metadata(fixture_csv)
    assert on_disk == expected
    # Lock in: must be exactly the CSV tickers, nothing else.
    assert set(on_disk.keys()) == {"AAPL", "MRNA"}


def test_metadata_excludes_sigma_alert_etfs(monkeypatch, tmp_path, fixture_csv):
    """Regression guard: the sigma-alert sector ETFs (XLE, XBI, etc.) must
    NOT appear in the generic universe_metadata.json. They live only in the
    sigma-alert-specific path (`reporting/sigma_export.build_sigma_metadata`)."""
    from reporting.sigma_export import SECTOR_ETFS

    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    weekly_universe._step_export_artifacts(
        {"rows": 2, "errors": [], "warnings": [], "passed": True}
    )

    on_disk = json.loads((exports_dir / "universe_metadata.json").read_text(encoding="utf-8"))
    leaked = set(on_disk.keys()) & set(SECTOR_ETFS.keys())
    assert leaked == set(), (
        f"Sigma-alert ETFs leaked into generic universe_metadata.json: {leaked}. "
        "Generic exports must not contain consumer-specific tickers."
    )


def test_normalization_collisions_are_surfaced(monkeypatch, tmp_path):
    """When two CSV rows normalize to the same ticker (e.g. 'ROG SW' and
    'ROG.DE' both → 'ROG'), the later row wins, ticker_count drops below
    row_count, and the status file reports the collision count + examples."""
    csv_path = tmp_path / "collision.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"],
        )
        writer.writeheader()
        writer.writerow({"Ticker": "ROG SW", "Exchange": "SIX", "Company Name": "Roche Swiss", "Sector (JP)": "Biopharma", "Subsector (JP)": ""})
        writer.writerow({"Ticker": "ROG.DE", "Exchange": "XETRA", "Company Name": "Roche Germany", "Sector (JP)": "Biopharma", "Subsector (JP)": ""})
        writer.writerow({"Ticker": "AAPL", "Exchange": "NASDAQ", "Company Name": "Apple Inc", "Sector (JP)": "Tech", "Subsector (JP)": ""})

    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", csv_path)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    weekly_universe._step_export_artifacts(
        {"rows": 3, "errors": [], "warnings": [], "passed": True}
    )

    status = json.loads((exports_dir / "universe_status.json").read_text(encoding="utf-8"))
    assert status["row_count"] == 3
    assert status["ticker_count"] == 2  # ROG collapses, AAPL standalone
    assert status["normalization_collisions"] == 1
    assert "ROG" in status["collision_examples"]


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
