"""Centralized configuration for the Coverage Manager.

All paths, API keys, segment definitions, and shared constants live here.
Other modules import from config rather than defining their own.
"""

from datetime import date
from pathlib import Path

from dotenv import dotenv_values

# ── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CSV_PATH = DATA_DIR / "coverage_universe_tickers.csv"
REPORTS_DIR = SCRIPT_DIR / "reports"
BACKUPS_DIR = SCRIPT_DIR / "backups"
OLD_REPORTS_DIR = REPORTS_DIR / "old reports"
SAMPLE_REPORTS_DIR = REPORTS_DIR / "samples"
CACHE_DIR = SCRIPT_DIR / "cache"

# ── API keys (.env) ──────────────────────────────────────────────────────────

API_KEYS = dotenv_values(SCRIPT_DIR / ".env")

# ── Date ─────────────────────────────────────────────────────────────────────

TODAY = date.today().strftime("%Y-%m-%d")

# ── Sector report segments ───────────────────────────────────────────────────

BIOPHARMA_VALUES = {"Biopharma"}
HC_SERVICES_MEDTECH_VALUES = {"Healthcare Services", "MedTech"}

SECTOR_SEGMENTS = [
    # (tab_name, html_suffix, title)
    ("Consolidated", "consolidated", "Coverage Universe Performance — Consolidated"),
    ("Biopharma", "biopharma", "Coverage Universe Performance — Biopharma"),
    ("HC Svcs & MedTech", "hc_svcs_medtech", "Coverage Universe Performance — HC Services & MedTech"),
    ("PA & Other", "pa_other", "Coverage Universe Performance — PA & Other"),
    ("S&P 500", "sp500", "S&P 500 Performance"),
]

# ── ETF benchmarks per report segment ────────────────────────────────────

SEGMENT_ETFS = {
    "Biopharma": [
        ("XLV", "Health Care Select Sector SPDR"),
        ("XBI", "SPDR S&P Biotech ETF"),
    ],
    "HC Svcs & MedTech": [
        ("XLV", "Health Care Select Sector SPDR"),
        ("XBI", "SPDR S&P Biotech ETF"),
    ],
    "S&P 500": [
        ("XLE", "Energy Select Sector SPDR"),
        ("XLB", "Materials Select Sector SPDR"),
        ("XLU", "Utilities Select Sector SPDR"),
        ("XLP", "Consumer Staples Select Sector SPDR"),
        ("XLI", "Industrial Select Sector SPDR"),
        ("XLRE", "Real Estate Select Sector SPDR"),
        ("XLC", "Communication Services Select Sector SPDR"),
        ("XLV", "Health Care Select Sector SPDR"),
        ("XLK", "Technology Select Sector SPDR"),
        ("XLY", "Consumer Discretionary Select Sector SPDR"),
        ("XLF", "Financial Select Sector SPDR"),
        ("XBI", "SPDR S&P Biotech ETF"),
        ("SPYM", "SPDR Portfolio S&P 500 ETF"),
    ],
}

# ── Sample mode ──────────────────────────────────────────────────────────────

SAMPLE_TICKERS = ["ISRG", "BLLN", "HTFL", "JAN", "WELL", "NTRA"]

# ── Required CSV columns ─────────────────────────────────────────────────────

REQUIRED_COLUMNS = ["Ticker", "Company Name", "Sector (JP)"]
EXPECTED_COLUMNS = ["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"]
