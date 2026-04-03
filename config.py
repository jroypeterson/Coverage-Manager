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
    ("Non-HC S&P 500", "sp500_non_hc", "Non-Healthcare S&P 500 Performance"),
]

# ── Sample mode ──────────────────────────────────────────────────────────────

SAMPLE_TICKERS = ["ISRG", "BLLN", "HTFL", "JAN", "WELL", "NTRA"]

# ── Required CSV columns ─────────────────────────────────────────────────────

REQUIRED_COLUMNS = ["Ticker", "Company Name", "Sector (JP)"]
EXPECTED_COLUMNS = ["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"]
