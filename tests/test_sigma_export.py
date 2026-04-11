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
    """The payload must include buy/target/notes plus name/sector from the universe."""
    from universe import watchlist as wl

    wl_csv = tmp_path / "watchlist.csv"
    monkeypatch.setattr(wl, "WATCHLIST_PATH", wl_csv)
    wl.add(
        "AAPL", buy_price=150, target_price=220, notes="core long",
        path=wl_csv, universe_csv_path=fixture_csv, today="2026-04-11",
    )

    payload = build_core_watchlist_payload(fixture_csv)
    assert set(payload.keys()) == {"AAPL"}
    entry = payload["AAPL"]
    assert entry["buy_price"] == 150
    assert entry["target_price"] == 220
    assert entry["notes"] == "core long"
    assert entry["name"] == "Apple Inc"
    assert entry["sector"] == "Tech"
    assert entry["subsector"] == "Hardware"


def test_export_and_push_writes_both_files(monkeypatch, tmp_path, fixture_csv):
    """export_and_push must write both ticker_metadata.json and core_watchlist.json
    into the target dir. Git operations are stubbed."""
    from universe import watchlist as wl

    wl_csv = tmp_path / "watchlist.csv"
    monkeypatch.setattr(wl, "WATCHLIST_PATH", wl_csv)
    wl.add(
        "MRNA", buy_price=40, target_price=100,
        path=wl_csv, universe_csv_path=fixture_csv, today="2026-04-11",
    )

    target_dir = tmp_path / "sigma-alert"
    target_dir.mkdir()
    (target_dir / ".git").mkdir()

    calls = []

    def fake_git(cwd, *args):
        calls.append(args)
        # `git diff --cached --quiet` returns 1 when there are staged changes.
        # Return 1 here so export_and_push proceeds past the unchanged-check.
        if args[:3] == ("diff", "--cached", "--quiet"):
            return "", 1
        return "", 0

    monkeypatch.setattr(sigma_export, "_git", fake_git)

    result = export_and_push(fixture_csv, target_dir=target_dir, push=False)

    assert (target_dir / "ticker_metadata.json").exists()
    assert (target_dir / CORE_WATCHLIST_FILENAME).exists()

    payload = json.loads((target_dir / CORE_WATCHLIST_FILENAME).read_text())
    assert "MRNA" in payload
    assert payload["MRNA"]["buy_price"] == 40
    assert payload["MRNA"]["sector"] == "Biopharma"

    # Confirm both files were `git add`-ed
    add_calls = [c for c in calls if c and c[0] == "add"]
    added_files = {c[1] for c in add_calls}
    assert added_files == {"ticker_metadata.json", CORE_WATCHLIST_FILENAME}

    assert result["status"] == "committed"
    assert result["watchlist_entries"] == 1
