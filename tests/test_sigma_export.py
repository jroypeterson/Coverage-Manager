"""Tests for the sigma-alert specific metadata builder.

The generic universe artifact builder lives in `universe/artifacts.py` and is
covered by `test_export_artifacts.py`. This file covers the sigma-alert path,
which composes the generic builder with hardcoded sector ETFs that the
sigma-alert watchlist needs but the coverage universe does not contain.
"""

import csv

import pytest

from reporting.sigma_export import SECTOR_ETFS, build_sigma_metadata
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
