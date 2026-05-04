"""Tests for the sigma-alert specific metadata builder.

The generic universe artifact builder lives in `universe/artifacts.py` and is
covered by `test_export_artifacts.py`. This file covers the sigma-alert path,
which composes the generic builder with hardcoded sector ETFs that the
sigma-alert watchlist needs but the coverage universe does not contain.
"""

import csv
import json

import pytest

from reporting import sigma_export
from reporting.sigma_export import (
    CORE_WATCHLIST_FILENAME,
    SECTOR_ETFS,
    build_core_watchlist_payload,
    build_sigma_metadata,
    export_and_push,
)
from universe.artifacts import build_universe_metadata


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


def test_sigma_metadata_includes_all_etfs(fixture_csv):
    """build_sigma_metadata must augment the generic universe with every ETF
    in SECTOR_ETFS — that's the whole reason it exists."""
    metadata = build_sigma_metadata(fixture_csv)
    for etf_ticker in SECTOR_ETFS:
        assert etf_ticker in metadata, f"Missing sigma-alert ETF {etf_ticker}"


def test_sigma_metadata_is_superset_of_generic(fixture_csv):
    """build_sigma_metadata must contain everything build_universe_metadata
    contains, plus exactly the SECTOR_ETFS."""
    generic = build_universe_metadata(fixture_csv)
    sigma = build_sigma_metadata(fixture_csv)

    # Every CSV ticker is preserved
    for ticker, info in generic.items():
        assert sigma[ticker] == info

    # The only additions are the sigma-alert ETFs
    additions = set(sigma.keys()) - set(generic.keys())
    assert additions == set(SECTOR_ETFS.keys())


def test_sigma_metadata_does_not_overwrite_existing_csv_ticker(tmp_path):
    """If the CSV happens to contain a ticker symbol that collides with an
    ETF (e.g., the universe lists XBI for some reason), the CSV row wins —
    sigma augmentation must not clobber it."""
    csv_path = tmp_path / "collision.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Ticker": "XBI",
                "Exchange": "NYSEARCA",
                "Company Name": "XBI From Coverage CSV",
                "Sector (JP)": "Biopharma",
                "Subsector (JP)": "ETF",
            }
        )

    sigma = build_sigma_metadata(csv_path)
    # CSV value, not the hardcoded ETF tuple
    assert sigma["XBI"]["name"] == "XBI From Coverage CSV"
    assert sigma["XBI"]["sector"] == "Biopharma"


# ── Core watchlist export ──────────────────────────────────────────────


def test_build_core_watchlist_payload_joins_universe_metadata(
    monkeypatch, tmp_path, fixture_csv,
):
    """The legacy payload (back-compat shim) must include buy/target/notes
    plus name/sector from the universe."""
    from universe import positions as pos

    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    from universe import watchlist as wl
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)
    pos.add(
        "AAPL", position="Portfolio", sell_price=220, notes="core long",
        path=pos_csv, universe_csv_path=fixture_csv, today="2026-04-11",
    )

    payload = build_core_watchlist_payload(fixture_csv)
    assert set(payload.keys()) == {"AAPL"}
    entry = payload["AAPL"]
    # Sell Price -> Target Price in the legacy shape
    assert entry["target_price"] == 220
    assert entry["notes"] == "core long"
    assert entry["name"] == "Apple Inc"
    assert entry["sector"] == "Tech"
    assert entry["subsector"] == "Hardware"


def test_build_portfolio_payload_filters_to_portfolio_rows(
    monkeypatch, tmp_path, fixture_csv,
):
    """build_portfolio_payload only includes Position == 'Portfolio'."""
    from universe import positions as pos
    from reporting.sigma_export import build_portfolio_payload, build_researching_payload

    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    pos.add("AAPL", position="Portfolio", sell_price=220,
            path=pos_csv, universe_csv_path=fixture_csv)
    pos.add("MRNA", position="Researching", buy_price=40,
            path=pos_csv, universe_csv_path=fixture_csv)

    portfolio = build_portfolio_payload(fixture_csv)
    researching = build_researching_payload(fixture_csv)
    assert set(portfolio.keys()) == {"AAPL"}
    assert set(researching.keys()) == {"MRNA"}
    assert portfolio["AAPL"]["position"] == "Portfolio"
    assert portfolio["AAPL"]["sell_price"] == 220
    assert researching["MRNA"]["position"] == "Researching"
    assert researching["MRNA"]["buy_price"] == 40


def test_export_and_push_writes_all_four_files(monkeypatch, tmp_path, fixture_csv):
    """export_and_push must write ticker_metadata.json + core_watchlist.json
    + portfolio.json + researching.json. Git operations are stubbed."""
    from universe import positions as pos

    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    from universe import watchlist as wl
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)
    pos.add("MRNA", position="Portfolio", sell_price=100,
            path=pos_csv, universe_csv_path=fixture_csv, today="2026-04-11")

    target_dir = tmp_path / "sigma-alert"
    target_dir.mkdir()
    (target_dir / ".git").mkdir()

    calls = []

    def fake_git(cwd, *args):
        calls.append(args)
        if args[:3] == ("diff", "--cached", "--quiet"):
            return "", 1
        return "", 0

    monkeypatch.setattr(sigma_export, "_git", fake_git)

    result = export_and_push(fixture_csv, target_dir=target_dir, push=False)

    assert (target_dir / "ticker_metadata.json").exists()
    assert (target_dir / CORE_WATCHLIST_FILENAME).exists()
    assert (target_dir / "portfolio.json").exists()
    assert (target_dir / "researching.json").exists()

    portfolio_payload = json.loads((target_dir / "portfolio.json").read_text())
    assert "MRNA" in portfolio_payload
    assert portfolio_payload["MRNA"]["position"] == "Portfolio"
    assert portfolio_payload["MRNA"]["sell_price"] == 100
    assert portfolio_payload["MRNA"]["sector"] == "Biopharma"

    researching_payload = json.loads((target_dir / "researching.json").read_text())
    assert researching_payload == {}  # no Researching rows in this test

    # Confirm all four files were `git add`-ed
    add_calls = [c for c in calls if c and c[0] == "add"]
    added_files = {c[1] for c in add_calls}
    assert added_files == {
        "ticker_metadata.json", CORE_WATCHLIST_FILENAME,
        "portfolio.json", "researching.json",
    }

    assert result["status"] == "committed"
    assert result["watchlist_entries"] == 1
    assert result["portfolio_entries"] == 1
    assert result["researching_entries"] == 0
