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
            fieldnames=["Ticker", "Exchange", "Company Name", "Sector (JP)",
                        "Subsector (JP)", "Currency", "Core"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Ticker": "AAPL",
                "Exchange": "NASDAQ",
                "Company Name": "Apple Inc",
                "Sector (JP)": "Tech",
                "Subsector (JP)": "Hardware",
                "Currency": "USD",
                "Core": "Y",
            }
        )
        writer.writerow(
            {
                "Ticker": "MRNA",
                "Exchange": "NASDAQ",
                "Company Name": "Moderna Inc",
                "Sector (JP)": "Biopharma",
                "Subsector (JP)": "Biotech",
                "Currency": "USD",
                "Core": "",
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
    assert status["schema_version"] == 3
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


def test_metadata_includes_core_field(monkeypatch, tmp_path, fixture_csv):
    """Schema v3: universe_metadata.json entries must include the `core` field
    so downstream consumers (sigma-alert 1σ filter, forensic_triage, etc.) can
    read it without falling back to the raw CSV."""
    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    weekly_universe._step_export_artifacts(
        {"rows": 2, "errors": [], "warnings": [], "passed": True}
    )

    metadata = json.loads((exports_dir / "universe_metadata.json").read_text(encoding="utf-8"))
    assert metadata["AAPL"]["core"] == "Y"
    assert metadata["MRNA"]["core"] == ""


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


def test_positions_export_writes_artifacts(monkeypatch, tmp_path, fixture_csv):
    """The positions export step writes the new portfolio.json + researching.json
    + ready_to_buy.json + ready_to_short.json + positions_and_researching.csv
    + positions_status.json, plus back-compat watchlist.csv/json/status.json
    (one cycle)."""
    from universe import positions as pos

    pos_csv = tmp_path / "positions_and_researching.csv"
    pos.add(
        "AAPL", position="Portfolio", sell_price=220, notes="core long",
        path=pos_csv, universe_csv_path=fixture_csv, today="2026-04-11",
    )
    pos.add(
        "MRNA", position="Researching", buy_price=40, notes="watching",
        path=pos_csv, universe_csv_path=fixture_csv, today="2026-04-12",
    )

    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    # The shim's WATCHLIST_PATH points to POSITIONS_PATH, so we need to refresh it
    from universe import watchlist as wl
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)

    result = weekly_universe._step_export_positions()

    # New canonical artifacts
    assert (exports_dir / "positions_and_researching.csv").exists()
    assert (exports_dir / "portfolio.json").exists()
    assert (exports_dir / "researching.json").exists()
    assert (exports_dir / "following_for_interest.json").exists()
    assert (exports_dir / "ready_to_buy.json").exists()
    assert (exports_dir / "ready_to_short.json").exists()
    assert (exports_dir / "positions_status.json").exists()

    # Legacy back-compat artifacts
    assert (exports_dir / "watchlist.csv").exists()
    assert (exports_dir / "watchlist.json").exists()
    assert (exports_dir / "watchlist_status.json").exists()

    assert result["entry_count"] == 2
    assert result["portfolio_count"] == 1
    assert result["researching_count"] == 1
    assert result["following_for_interest_count"] == 0
    assert result["ready_to_buy_count"] == 0
    assert result["ready_to_short_count"] == 0
    assert result["validation_passed"] is True

    # portfolio.json: Portfolio rows only
    portfolio = json.loads((exports_dir / "portfolio.json").read_text(encoding="utf-8"))
    assert "AAPL" in portfolio
    assert "MRNA" not in portfolio
    assert portfolio["AAPL"]["position"] == "Portfolio"
    assert portfolio["AAPL"]["sell_price"] == 220
    assert portfolio["AAPL"]["name"] == "Apple Inc"

    # researching.json: Researching rows only
    researching = json.loads((exports_dir / "researching.json").read_text(encoding="utf-8"))
    assert "MRNA" in researching
    assert "AAPL" not in researching
    assert researching["MRNA"]["position"] == "Researching"
    assert researching["MRNA"]["buy_price"] == 40

    # Empty trigger-ready and following files in this fixture
    assert json.loads((exports_dir / "following_for_interest.json").read_text(encoding="utf-8")) == {}
    assert json.loads((exports_dir / "ready_to_buy.json").read_text(encoding="utf-8")) == {}
    assert json.loads((exports_dir / "ready_to_short.json").read_text(encoding="utf-8")) == {}

    # CSV: universe cols + position cols
    with open(exports_dir / "positions_and_researching.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        rows = list(reader)
    assert header[0] == "Ticker"
    assert header[-8:] == ["Position", "Position Date", "Buy Price", "Sell Price",
                            "First Buy Date", "Average Cost", "Shares", "Notes"]
    assert len(rows) == 2
    aapl_row = next(r for r in rows if r["Ticker"] == "AAPL")
    assert aapl_row["Position"] == "Portfolio"
    assert aapl_row["Sell Price"] == "220.0"

    # Status file
    status = json.loads((exports_dir / "positions_status.json").read_text(encoding="utf-8"))
    assert status["schema_version"] == 3
    assert status["entry_count"] == 2
    assert status["portfolio_count"] == 1
    assert status["researching_count"] == 1
    assert status["following_for_interest_count"] == 0
    assert status["ready_to_buy_count"] == 0
    assert status["ready_to_short_count"] == 0
    assert status["validation_passed"] is True

    # Legacy back-compat: watchlist.json should have BOTH entries (union)
    # with Sell Price mapped to Target Price
    legacy = json.loads((exports_dir / "watchlist.json").read_text(encoding="utf-8"))
    assert "AAPL" in legacy and "MRNA" in legacy
    assert legacy["AAPL"]["target_price"] == 220  # was Sell Price


def test_positions_export_routes_following_for_interest(monkeypatch, tmp_path, fixture_csv):
    """Following for Interest rows must land in following_for_interest.json
    and not pollute portfolio.json / researching.json / the ready-to-* files."""
    from universe import positions as pos

    pos_csv = tmp_path / "positions_and_researching.csv"
    pos.add(
        "MRNA", position="Following for Interest", notes="track earnings",
        path=pos_csv, universe_csv_path=fixture_csv, today="2026-05-10",
    )

    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    from universe import watchlist as wl
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)

    result = weekly_universe._step_export_positions()
    assert result["following_for_interest_count"] == 1
    assert result["portfolio_count"] == 0
    assert result["researching_count"] == 0

    following = json.loads((exports_dir / "following_for_interest.json").read_text(encoding="utf-8"))
    assert "MRNA" in following
    assert following["MRNA"]["position"] == "Following for Interest"

    # Cross-check: doesn't leak elsewhere.
    assert json.loads((exports_dir / "portfolio.json").read_text(encoding="utf-8")) == {}
    assert json.loads((exports_dir / "researching.json").read_text(encoding="utf-8")) == {}
    assert json.loads((exports_dir / "ready_to_buy.json").read_text(encoding="utf-8")) == {}
    assert json.loads((exports_dir / "ready_to_short.json").read_text(encoding="utf-8")) == {}

    # Legacy back-compat: must NOT include Following-for-Interest rows.
    legacy = json.loads((exports_dir / "watchlist.json").read_text(encoding="utf-8"))
    assert legacy == {}


def test_positions_export_routes_ready_states(monkeypatch, tmp_path, fixture_csv):
    """Ready to Buy and Ready to Short rows must land in their own JSON files
    and not pollute portfolio.json / researching.json."""
    from universe import positions as pos

    pos_csv = tmp_path / "positions_and_researching.csv"
    # AAPL → Ready to Buy with a buy-trigger level; MRNA → Ready to Short
    # with a sell-trigger level (short entry is at the high).
    pos.add(
        "AAPL", position="Ready to Buy", buy_price=180, notes="enter on dip",
        path=pos_csv, universe_csv_path=fixture_csv, today="2026-05-08",
    )
    pos.add(
        "MRNA", position="Ready to Short", sell_price=120, notes="short the bounce",
        path=pos_csv, universe_csv_path=fixture_csv, today="2026-05-08",
    )

    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    from universe import watchlist as wl
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)

    result = weekly_universe._step_export_positions()
    assert result["portfolio_count"] == 0
    assert result["researching_count"] == 0
    assert result["ready_to_buy_count"] == 1
    assert result["ready_to_short_count"] == 1

    rtb = json.loads((exports_dir / "ready_to_buy.json").read_text(encoding="utf-8"))
    rts = json.loads((exports_dir / "ready_to_short.json").read_text(encoding="utf-8"))
    assert "AAPL" in rtb and rtb["AAPL"]["buy_price"] == 180
    assert "MRNA" in rts and rts["MRNA"]["sell_price"] == 120

    # Cross-check: rtb/rts entries do NOT leak into portfolio/researching.
    portfolio = json.loads((exports_dir / "portfolio.json").read_text(encoding="utf-8"))
    researching = json.loads((exports_dir / "researching.json").read_text(encoding="utf-8"))
    assert portfolio == {}
    assert researching == {}


def test_manifest_lists_all_files(monkeypatch, tmp_path, fixture_csv):
    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    weekly_universe._step_export_artifacts(
        {"rows": 2, "errors": [], "warnings": [], "passed": True}
    )

    manifest = json.loads((exports_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 3
    listed_names = {f["name"] for f in manifest["files"]}
    assert listed_names == {
        "universe.csv",
        "universe_metadata.json",
        "universe_status.json",
        "positions_and_researching.csv",
        "portfolio.json",
        "researching.json",
        "following_for_interest.json",
        "ready_to_buy.json",
        "ready_to_short.json",
        "positions_status.json",
        "watchlist.csv",
        "watchlist.json",
        "watchlist_status.json",
        "reporting_calendar.json",
        "reporting_calendar_status.json",
        "manifest.json",
    }
