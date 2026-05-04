"""Tests for universe.positions — the new positions_and_researching module."""

import csv

import pytest

from universe import positions as pos


@pytest.fixture
def fake_universe(tmp_path):
    path = tmp_path / "coverage_universe_tickers.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["Ticker", "Company Name", "Sector (JP)", "Subsector (JP)", "Currency", "Exchange"]
        )
        writer.writeheader()
        writer.writerow({"Ticker": "INSM", "Company Name": "Insmed", "Sector (JP)": "Biopharma", "Subsector (JP)": "", "Currency": "USD", "Exchange": "NASDAQ"})
        writer.writerow({"Ticker": "ISRG", "Company Name": "Intuitive Surgical", "Sector (JP)": "MedTech", "Subsector (JP)": "", "Currency": "USD", "Exchange": "NASDAQ"})
        writer.writerow({"Ticker": "WELL", "Company Name": "Welltower", "Sector (JP)": "Healthcare Services", "Subsector (JP)": "", "Currency": "USD", "Exchange": "NYSE"})
    return path


@pytest.fixture
def pos_path(tmp_path):
    return tmp_path / "positions_and_researching.csv"


# ── add / load roundtrip ────────────────────────────────────────────────────


def test_add_portfolio_roundtrip(fake_universe, pos_path):
    pos.add("INSM", position="Portfolio", sell_price=75.0, notes="core long",
            path=pos_path, universe_csv_path=fake_universe, today="2026-04-11")
    entries = pos.load(pos_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["Ticker"] == "INSM"
    assert e["Position"] == "Portfolio"
    assert e["Sell Price"] == 75.0
    assert e["Buy Price"] is None
    assert e["Position Date"] == "2026-04-11"
    assert e["Notes"] == "core long"


def test_add_researching_roundtrip(fake_universe, pos_path):
    pos.add("INSM", position="Researching", buy_price=30.0,
            path=pos_path, universe_csv_path=fake_universe, today="2026-04-11")
    entries = pos.load(pos_path)
    assert entries[0]["Position"] == "Researching"
    assert entries[0]["Buy Price"] == 30.0


def test_add_with_broker_fields(fake_universe, pos_path):
    pos.add("INSM", position="Portfolio",
            first_buy_date="2026-01-15", average_cost=42.5, shares=100,
            path=pos_path, universe_csv_path=fake_universe)
    e = pos.load(pos_path)[0]
    assert e["First Buy Date"] == "2026-01-15"
    assert e["Average Cost"] == 42.5
    assert e["Shares"] == 100


# ── validation ──────────────────────────────────────────────────────────────


def test_add_rejects_invalid_position(fake_universe, pos_path):
    with pytest.raises(pos.PositionsError, match="position must be one of"):
        pos.add("INSM", position="Watching", path=pos_path, universe_csv_path=fake_universe)


def test_add_rejects_non_universe_ticker(fake_universe, pos_path):
    with pytest.raises(pos.PositionsError, match="not in the coverage universe"):
        pos.add("AAPL", position="Portfolio", path=pos_path, universe_csv_path=fake_universe)


def test_add_rejects_sell_not_above_buy(fake_universe, pos_path):
    with pytest.raises(pos.PositionsError, match="above buy price"):
        pos.add("INSM", position="Researching", buy_price=100, sell_price=50,
                path=pos_path, universe_csv_path=fake_universe)


def test_validate_flags_invalid_position(fake_universe, pos_path):
    entries = [{
        "Ticker": "INSM", "Position": "Watching",  # invalid
        "Position Date": "2026-04-11", "Buy Price": None, "Sell Price": None,
        "First Buy Date": "", "Average Cost": None, "Shares": None, "Notes": "",
    }]
    errors, _ = pos.validate(entries, universe_csv_path=fake_universe)
    assert any("Position must be one of" in e for e in errors)


def test_validate_flags_missing_from_universe(fake_universe, pos_path):
    entries = [{
        "Ticker": "ZZZ", "Position": "Portfolio",
        "Position Date": "2026-04-11", "Buy Price": None, "Sell Price": None,
        "First Buy Date": "", "Average Cost": None, "Shares": None, "Notes": "",
    }]
    errors, _ = pos.validate(entries, universe_csv_path=fake_universe)
    assert any("not in the coverage universe" in e for e in errors)


def test_validate_warns_on_sell_at_or_below_buy(fake_universe, pos_path):
    entries = [{
        "Ticker": "INSM", "Position": "Researching",
        "Position Date": "2026-04-11", "Buy Price": 100.0, "Sell Price": 90.0,
        "First Buy Date": "", "Average Cost": None, "Shares": None, "Notes": "",
    }]
    _, warnings = pos.validate(entries, universe_csv_path=fake_universe)
    assert any("not above buy price" in w for w in warnings)


# ── update / remove ─────────────────────────────────────────────────────────


def test_add_updates_existing_entry(fake_universe, pos_path):
    pos.add("INSM", position="Portfolio", sell_price=75.0,
            path=pos_path, universe_csv_path=fake_universe, today="2026-04-11")
    pos.add("INSM", position="Portfolio", sell_price=80.0, notes="raised target",
            path=pos_path, universe_csv_path=fake_universe, today="2026-04-12")
    entries = pos.load(pos_path)
    assert len(entries) == 1
    assert entries[0]["Sell Price"] == 80.0
    assert entries[0]["Notes"] == "raised target"
    # Position Date stays at the original add date
    assert entries[0]["Position Date"] == "2026-04-11"


def test_remove(fake_universe, pos_path):
    pos.add("INSM", position="Portfolio", path=pos_path, universe_csv_path=fake_universe)
    pos.add("ISRG", position="Researching", path=pos_path, universe_csv_path=fake_universe)
    assert pos.remove("INSM", path=pos_path) is True
    entries = pos.load(pos_path)
    assert len(entries) == 1
    assert entries[0]["Ticker"] == "ISRG"
    # Removing a non-existent ticker returns False
    assert pos.remove("AAPL", path=pos_path) is False


def test_load_missing_file_returns_empty(tmp_path):
    assert pos.load(tmp_path / "nope.csv") == []


def test_save_sorts_by_ticker(fake_universe, pos_path):
    pos.add("WELL", position="Researching", path=pos_path, universe_csv_path=fake_universe)
    pos.add("INSM", position="Portfolio", path=pos_path, universe_csv_path=fake_universe)
    pos.add("ISRG", position="Portfolio", path=pos_path, universe_csv_path=fake_universe)
    with open(pos_path) as f:
        rows = list(csv.DictReader(f))
    assert [r["Ticker"] for r in rows] == ["INSM", "ISRG", "WELL"]


# ── filter_by_position ──────────────────────────────────────────────────────


def test_filter_by_position(fake_universe, pos_path):
    pos.add("INSM", position="Portfolio", path=pos_path, universe_csv_path=fake_universe)
    pos.add("ISRG", position="Portfolio", path=pos_path, universe_csv_path=fake_universe)
    pos.add("WELL", position="Researching", path=pos_path, universe_csv_path=fake_universe)
    entries = pos.load(pos_path)
    portfolio = pos.filter_by_position(entries, "Portfolio")
    researching = pos.filter_by_position(entries, "Researching")
    assert {e["Ticker"] for e in portfolio} == {"INSM", "ISRG"}
    assert {e["Ticker"] for e in researching} == {"WELL"}


# ── new-ticker auto-enrichment escape hatch (mirrors test_watchlist) ────────


def test_add_create_if_missing_appends_universe_row(fake_universe, pos_path):
    """create_if_missing=True should auto-enrich a new ticker before adding."""
    fake_enrich_row = {
        "Ticker": "NEWCO",
        "Exchange": "NASDAQ",
        "Company Name": "NewCo, Inc.",
        "Sector (JP)": "Tech",
        "Subsector (JP)": "",
        "Currency": "USD",
    }

    from unittest.mock import patch
    with patch("universe.enrich.enrich_single_ticker", return_value=fake_enrich_row) as mock_enrich:
        pos.add("NEWCO", position="Researching",
                path=pos_path, universe_csv_path=fake_universe,
                create_if_missing=True, sector_jp="Tech")
    mock_enrich.assert_called_once_with("NEWCO", sector_jp="Tech", exchange_hint=None)
    # NEWCO should now be in the universe CSV
    with open(fake_universe) as f:
        rows = list(csv.DictReader(f))
    newco_row = next((r for r in rows if r["Ticker"] == "NEWCO"), None)
    assert newco_row is not None
    assert newco_row["Sector (JP)"] == "Tech"
    # And on the positions file
    entries = pos.load(pos_path)
    assert any(e["Ticker"] == "NEWCO" for e in entries)


def test_add_create_if_missing_without_sector_errors(fake_universe, pos_path):
    with pytest.raises(pos.PositionsError, match="sector_jp.*required"):
        pos.add("NEWCO", position="Researching",
                path=pos_path, universe_csv_path=fake_universe,
                create_if_missing=True)


def test_add_dry_run_does_not_write(fake_universe, pos_path):
    result = pos.add("INSM", position="Portfolio", sell_price=75.0,
                     path=pos_path, universe_csv_path=fake_universe,
                     dry_run=True)
    assert "positions_entry" in result
    assert pos.load(pos_path) == []  # nothing on disk
