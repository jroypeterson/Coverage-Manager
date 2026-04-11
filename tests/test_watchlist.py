"""Tests for the watchlist module."""

import csv

import pytest

from universe import watchlist as wl


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
def wl_path(tmp_path):
    return tmp_path / "watchlist.csv"


def test_add_and_load_roundtrip(fake_universe, wl_path):
    wl.add("INSM", buy_price=30.0, target_price=75.0, notes="core long",
           path=wl_path, universe_csv_path=fake_universe, today="2026-04-11")
    entries = wl.load(wl_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["Ticker"] == "INSM"
    assert e["Buy Price"] == 30.0
    assert e["Target Price"] == 75.0
    assert e["Date Added"] == "2026-04-11"
    assert e["Notes"] == "core long"


def test_add_rejects_non_universe_ticker(fake_universe, wl_path):
    with pytest.raises(wl.WatchlistError, match="not in the coverage universe"):
        wl.add("AAPL", buy_price=100, target_price=200,
               path=wl_path, universe_csv_path=fake_universe)


def test_add_rejects_target_not_above_buy(fake_universe, wl_path):
    with pytest.raises(wl.WatchlistError, match="above buy price"):
        wl.add("INSM", buy_price=50, target_price=40,
               path=wl_path, universe_csv_path=fake_universe)


def test_add_updates_existing_entry(fake_universe, wl_path):
    wl.add("ISRG", buy_price=300, target_price=500,
           path=wl_path, universe_csv_path=fake_universe, today="2026-04-01")
    wl.add("ISRG", buy_price=320, target_price=520, notes="raised",
           path=wl_path, universe_csv_path=fake_universe, today="2026-04-11")
    entries = wl.load(wl_path)
    assert len(entries) == 1
    assert entries[0]["Buy Price"] == 320
    assert entries[0]["Target Price"] == 520
    assert entries[0]["Notes"] == "raised"
    # Date Added should persist from the first add
    assert entries[0]["Date Added"] == "2026-04-01"


def test_remove(fake_universe, wl_path):
    wl.add("INSM", buy_price=30, target_price=75,
           path=wl_path, universe_csv_path=fake_universe)
    wl.add("ISRG", buy_price=300, target_price=500,
           path=wl_path, universe_csv_path=fake_universe)
    assert wl.remove("INSM", path=wl_path) is True
    entries = wl.load(wl_path)
    assert [e["Ticker"] for e in entries] == ["ISRG"]
    assert wl.remove("NOPE", path=wl_path) is False


def test_validate_flags_missing_from_universe(fake_universe, wl_path):
    # Write a bad entry directly so add() doesn't reject it
    with open(wl_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=wl.WATCHLIST_COLUMNS)
        writer.writeheader()
        writer.writerow({"Ticker": "NOTREAL", "Buy Price": "10", "Target Price": "20",
                         "Date Added": "2026-04-11", "Notes": ""})
        writer.writerow({"Ticker": "INSM", "Buy Price": "30", "Target Price": "25",
                         "Date Added": "2026-04-11", "Notes": ""})
    entries = wl.load(wl_path)
    errors, warnings = wl.validate(entries, universe_csv_path=fake_universe)
    assert any("NOTREAL" in e for e in errors)
    assert any("INSM" in w and "target" in w for w in warnings)


def test_save_sorts_by_ticker(fake_universe, wl_path):
    wl.add("WELL", buy_price=80, target_price=120,
           path=wl_path, universe_csv_path=fake_universe)
    wl.add("INSM", buy_price=30, target_price=75,
           path=wl_path, universe_csv_path=fake_universe)
    wl.add("ISRG", buy_price=300, target_price=500,
           path=wl_path, universe_csv_path=fake_universe)
    entries = wl.load(wl_path)
    assert [e["Ticker"] for e in entries] == ["INSM", "ISRG", "WELL"]


def test_load_missing_file_returns_empty(tmp_path):
    assert wl.load(tmp_path / "does_not_exist.csv") == []


def test_validate_flags_missing_universe_metadata(tmp_path, wl_path):
    """A watchlist ticker whose universe row is missing Company Name / Sector /
    Currency / Exchange should fail validation — downstream report + sigma
    integration rely on all four."""
    bad_universe = tmp_path / "bad_universe.csv"
    with open(bad_universe, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["Ticker", "Company Name", "Sector (JP)", "Subsector (JP)", "Currency", "Exchange"]
        )
        writer.writeheader()
        # INSM is fine
        writer.writerow({"Ticker": "INSM", "Company Name": "Insmed", "Sector (JP)": "Biopharma", "Subsector (JP)": "", "Currency": "USD", "Exchange": "NASDAQ"})
        # HALF_BAKED is missing Currency and Exchange
        writer.writerow({"Ticker": "HALF_BAKED", "Company Name": "Half Co", "Sector (JP)": "Biopharma", "Subsector (JP)": "", "Currency": "", "Exchange": ""})
        # NO_SECTOR is missing Sector (JP)
        writer.writerow({"Ticker": "NO_SECTOR", "Company Name": "NoSec Co", "Sector (JP)": "", "Subsector (JP)": "", "Currency": "USD", "Exchange": "NASDAQ"})

    # Write watchlist directly so add() doesn't reject via the same check
    with open(wl_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=wl.WATCHLIST_COLUMNS)
        writer.writeheader()
        for t in ("INSM", "HALF_BAKED", "NO_SECTOR"):
            writer.writerow({"Ticker": t, "Buy Price": "10", "Target Price": "20",
                             "Date Added": "2026-04-11", "Notes": ""})

    entries = wl.load(wl_path)
    errors, _ = wl.validate(entries, universe_csv_path=bad_universe)

    # INSM should be clean
    assert not any("INSM" in e for e in errors)
    # HALF_BAKED should be flagged for both Currency and Exchange
    half_errors = [e for e in errors if "HALF_BAKED" in e]
    assert len(half_errors) == 1
    assert "Currency" in half_errors[0]
    assert "Exchange" in half_errors[0]
    # NO_SECTOR should be flagged for Sector (JP)
    sector_errors = [e for e in errors if "NO_SECTOR" in e]
    assert len(sector_errors) == 1
    assert "Sector (JP)" in sector_errors[0]
