"""Shared ticker/exchange mappings, normalization functions, and path constants.

Used by generate_performance.py, cleanup_tickers.py, enrich_identifiers.py,
and add_exchanges.py to avoid duplicating logic.
"""

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── Path constants (imported from config, re-exported for backward compat) ──

from config import SCRIPT_DIR, CSV_PATH, REPORTS_DIR, BACKUPS_DIR

# ── Manual yfinance ticker mappings ─────────────────────────────────────────

MANUAL_TICKER_MAP = {
    # Japanese tickers (name-based in CSV → numeric yfinance format)
    "Olympus": "7733.T",
    "Shimadzu": "7701.T",
    "Sysmex": "6869.T",
    "Terumo": "4543.T",
    "Hoya": "7741.T",
    "Nihon Kohden": "6849.T",
    "Asahi Intecc": "7747.T",
    "Fukuda Denshi": "6960.T",
    "Japan Lifeline": "7575.T",
    "Nakanishi": "7716.T",
    "NGK Insulators": "5333.T",
    "Horiba": "6856.T",
    "Keyence": "6861.T",
    "Murata": "6981.T",
    "Renesas": "6723.T",
    "Rohm": "6963.T",
    "TDK": "6762.T",
    "Advantest": "6857.T",
    "Disco": "6146.T",
    "Lasertec": "6920.T",
    "Screen Holdings": "7735.T",
    "Tokyo Electron": "8035.T",
    # Brazilian tickers
    "RDOR3": "RDOR3.SA",
    "HAPV3": "HAPV3.SA",
    "FLRY3": "FLRY3.SA",
    "QUAL3": "QUAL3.SA",
    "HYPE3": "HYPE3.SA",
    "PNVL3": "PNVL3.SA",
    "DASA3": "DASA3.SA",
    "MATD3": "MATD3.SA",
    "ONCO3": "ONCO3.SA",
    # European tickers that may need explicit suffix
    "BAYN": "BAYN.DE",
    "LONN": "LONN.SW",
    "SIKA": "SIKA.SW",
    "VAR1": "VAR1.DE",
}

# ── Exchange → yfinance suffix (reverse of SUFFIX_TO_EXCHANGE) ─────────────

EXCHANGE_TO_YF_SUFFIX = {
    "TSE": ".T", "TWSE": ".TW", "HKEX": ".HK", "SSE": ".SS", "SZSE": ".SZ",
    "NSE": ".NS", "KRX": ".KS", "KOSDAQ": ".KQ",
    "OMX Stockholm": ".ST", "OMX Copenhagen": ".CO", "OMX Helsinki": ".HE",
    "Oslo Bors": ".OL", "SIX": ".SW", "XETRA": ".DE", "Frankfurt": ".F",
    "Euronext Paris": ".PA", "Euronext Brussels": ".BR",
    "Borsa Italiana": ".MI", "BME Madrid": ".MC",
    "LSE": ".L", "ASX": ".AX", "NZX": ".NZ", "JSE": ".JO",
    "Tadawul": ".SR", "ADX": ".AE",
    "TSX": ".TO", "TSXV": ".V", "WSE": ".WA", "BMV": ".MX",
    "B3": ".SA", "IDX": ".JK",
}

# US exchanges — no suffix needed for yfinance
_US_EXCHANGES = {
    "NYSE", "NASDAQ", "NYSE American", "NYSE Arca", "OTC", "BATS",
    "OQB", "OQX", "PCX",
}

# ── Exchange suffix mappings ────────────────────────────────────────────────

# Dot-suffix → exchange name (e.g. ".T" → "TSE")
SUFFIX_TO_EXCHANGE = {
    ".T": "TSE", ".TW": "TWSE", ".HK": "HKEX", ".SS": "SSE", ".SZ": "SZSE",
    ".NS": "NSE", ".KS": "KRX", ".KQ": "KOSDAQ", ".ST": "OMX Stockholm",
    ".CO": "OMX Copenhagen", ".HE": "OMX Helsinki", ".OL": "Oslo Bors",
    ".SW": "SIX", ".DE": "XETRA", ".F": "Frankfurt", ".PA": "Euronext Paris",
    ".BR": "Euronext Brussels", ".MI": "Borsa Italiana", ".MC": "BME Madrid",
    ".L": "LSE", ".AX": "ASX", ".NZ": "NZX", ".JO": "JSE", ".SR": "Tadawul",
    ".AE": "ADX", ".TO": "TSX", ".V": "TSXV", ".WA": "WSE", ".MX": "BMV",
    ".SA": "B3",
}

# Space-separated suffix → exchange name (e.g. "GETIB SS" → "OMX Stockholm")
SPACE_SUFFIX_TO_EXCHANGE = {
    "SW": "SIX", "CH": "SIX", "sW": "SIX",
    "DC": "OMX Copenhagen", "SS": "OMX Stockholm", "ST": "OMX Stockholm",
    "DE": "XETRA", "GY": "XETRA", "FP": "Euronext Paris", "FR": "Euronext Paris",
    "LN": "LSE", "GB": "LSE", "AU": "ASX", "Au": "ASX", "IM": "Borsa Italiana",
    "HK": "HKEX",
}

# Raw yfinance exchange codes → clean display names
EXCHANGE_NORMALIZE = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ", "NASDAQ": "NASDAQ",
    "NYQ": "NYSE", "NYS": "NYSE", "NYSE": "NYSE",
    "ASE": "NYSE American", "AMX": "NYSE American", "AMEX": "NYSE American",
    "PNK": "OTC", "OTC": "OTC",
    "BTS": "BATS", "BATS": "BATS",
    "PCX": "NYSE Arca",
}

# ── FIGI / Country mappings ─────────────────────────────────────────────────

# Our Exchange name → OpenFIGI exchCode
EXCHANGE_TO_FIGI = {
    "NASDAQ": "US", "NYSE": "US", "NYSE American": "US", "NYSE Arca": "US",
    "OTC": "US", "BATS": "US",
    "LSE": "LN",
    "XETRA": "GY", "Frankfurt": "GY",
    "Euronext Paris": "FP", "Euronext Brussels": "BB",
    "SIX": "SW",
    "Borsa Italiana": "IM",
    "BME Madrid": "SM",
    "TSE": "JP",
    "HKEX": "HK",
    "KRX": "KS", "KOSDAQ": "KS",
    "TWSE": "TT",
    "SSE": "CH", "SZSE": "CZ",
    "ASX": "AU",
    "NZX": "NZ",
    "TSX": "CN", "TSXV": "CV",
    "B3": "BS",
    "NSE": "IN",
    "OMX Stockholm": "SS", "OMX Copenhagen": "DC", "OMX Helsinki": "FH",
    "Oslo Bors": "NO",
    "WSE": "WA",
    "BMV": "MX",
    "JSE": "SJ",
    "Tadawul": "AB",
    "ADX": "DH",
}

# Exchange → Country (Listing)
EXCHANGE_TO_COUNTRY = {
    "NASDAQ": "United States", "NYSE": "United States", "NYSE American": "United States",
    "NYSE Arca": "United States", "OTC": "United States", "BATS": "United States",
    "LSE": "United Kingdom",
    "XETRA": "Germany", "Frankfurt": "Germany",
    "Euronext Paris": "France", "Euronext Brussels": "Belgium",
    "SIX": "Switzerland",
    "Borsa Italiana": "Italy",
    "BME Madrid": "Spain",
    "TSE": "Japan",
    "HKEX": "Hong Kong",
    "KRX": "South Korea", "KOSDAQ": "South Korea",
    "TWSE": "Taiwan",
    "SSE": "China", "SZSE": "China",
    "ASX": "Australia",
    "NZX": "New Zealand",
    "TSX": "Canada", "TSXV": "Canada",
    "B3": "Brazil",
    "NSE": "India",
    "OMX Stockholm": "Sweden", "OMX Copenhagen": "Denmark", "OMX Helsinki": "Finland",
    "Oslo Bors": "Norway",
    "WSE": "Poland",
    "BMV": "Mexico",
    "JSE": "South Africa",
    "Tadawul": "Saudi Arabia",
    "ADX": "United Arab Emirates",
}

# Country full name → ISO 3166-1 alpha-3
COUNTRY_TO_ISO = {
    "United States": "USA", "United Kingdom": "GBR", "Germany": "DEU",
    "France": "FRA", "Belgium": "BEL", "Switzerland": "CHE",
    "Italy": "ITA", "Spain": "ESP", "Japan": "JPN",
    "Hong Kong": "HKG", "South Korea": "KOR", "Taiwan": "TWN",
    "China": "CHN", "Australia": "AUS", "New Zealand": "NZL",
    "Canada": "CAN", "Brazil": "BRA", "India": "IND",
    "Sweden": "SWE", "Denmark": "DNK", "Finland": "FIN",
    "Norway": "NOR", "Poland": "POL", "Mexico": "MEX",
    "South Africa": "ZAF", "Saudi Arabia": "SAU",
    "United Arab Emirates": "ARE", "Luxembourg": "LUX",
}

# Country full name → ISO 3166-1 alpha-2 (the prefix an ISIN uses).
# Used by enrich to sanity-check ISINs against the row's listing country —
# yfinance occasionally returns a wrong-country ISIN for rebranded tickers
# (e.g. "FI" returned a Swiss ISIN for Fiserv after the FISV→FI rebrand).
COUNTRY_TO_ISIN_PREFIX = {
    "United States": "US", "United Kingdom": "GB", "Germany": "DE",
    "France": "FR", "Belgium": "BE", "Switzerland": "CH",
    "Italy": "IT", "Spain": "ES", "Japan": "JP",
    "Hong Kong": "HK", "South Korea": "KR", "Taiwan": "TW",
    "China": "CN", "Australia": "AU", "New Zealand": "NZ",
    "Canada": "CA", "Brazil": "BR", "India": "IN",
    "Sweden": "SE", "Denmark": "DK", "Finland": "FI",
    "Norway": "NO", "Poland": "PL", "Mexico": "MX",
    "South Africa": "ZA", "Saudi Arabia": "SA",
    "United Arab Emirates": "AE", "Luxembourg": "LU",
}

# ── Functions ───────────────────────────────────────────────────────────────


def read_universe_csv(path=CSV_PATH):
    """Load the coverage-universe CSV with every column as a string and blanks as "".

    Any module that reads the universe CSV and writes the WHOLE file back must use
    this loader. A bare ``pd.read_csv`` infers integer ID columns that contain blank
    cells (notably ``CIK`` and ``Year Listed``) as float64 (``1125376`` -> ``1125376.0``),
    and the subsequent full ``df.to_csv`` then persists the ``.0`` suffix. A ``.0`` CIK
    breaks the SEC/EDGAR lookups that consume the column and corrupts the published
    ``exports/universe.csv`` snapshot. ``dtype=str`` + ``keep_default_na=False`` keeps
    every value verbatim so a load->save round-trip is byte-stable. Mirrors the existing
    safe reads in ``lei_backfill.py`` and ``ticker_change_check.py``.
    """
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def backup_csv(path):
    """Create a timestamped backup of the CSV in the backups subfolder."""
    backup_dir = os.path.join(os.path.dirname(path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    basename = os.path.splitext(os.path.basename(path))[0]
    backup_path = os.path.join(backup_dir, f"{basename}_{ts}.csv")
    shutil.copy2(path, backup_path)
    return backup_path


def normalize_ticker(ticker, company_name="", exchange=""):
    """Normalize a ticker string to yfinance format.

    Handles manual mappings, space-separated exchange suffixes,
    colon-separated formats, and Exchange column fallback.
    """
    t = str(ticker).strip()
    if t in ("#N/A", "", "nan"):
        return None
    # Check manual mapping by ticker first, then by company name
    if t in MANUAL_TICKER_MAP:
        return MANUAL_TICKER_MAP[t]
    cn = str(company_name).strip()
    if cn and cn != "nan" and cn in MANUAL_TICKER_MAP:
        return MANUAL_TICKER_MAP[cn]
    # Handle space-separated exchange suffixes
    suffix_map = {
        "SW": "SW", "CH": "SW", "sW": "SW",
        "DC": "CO", "SS": "ST", "ST": "ST",
        "DE": "DE", "GY": "DE",
        "FP": "PA", "FR": "PA",
        "LN": "L", "GB": "L",
        "AU": "AX", "Au": "AX",
        "IM": "MI",
        "HK": "HK",
    }
    parts = t.split()
    if len(parts) == 2:
        sym, exch = parts
        if exch in suffix_map:
            return f"{sym}.{suffix_map[exch]}"
        return f"{sym}.{exch}"
    # Already has a dot (e.g., ROG.SW, 4519.T)
    if "." in t or ":" in t:
        return t.replace(":", ".")
    # Plain ticker — use Exchange column to add yfinance suffix
    ex = str(exchange).strip() if exchange else ""
    if ex and ex != "nan" and ex not in _US_EXCHANGES:
        yf_suffix = EXCHANGE_TO_YF_SUFFIX.get(ex)
        if yf_suffix:
            return f"{t}{yf_suffix}"
    return t


def get_exchange_from_suffix(ticker):
    """Determine exchange name from a ticker's suffix (dot or space-separated)."""
    t = str(ticker).strip()
    # Check space-separated suffix (e.g., "GETIB SS", "AMP IM")
    parts = t.split()
    if len(parts) == 2:
        _, suffix = parts
        if suffix in SPACE_SUFFIX_TO_EXCHANGE:
            return SPACE_SUFFIX_TO_EXCHANGE[suffix]
    # Check dot suffix (e.g., 4519.T, BIOCON.NS) — longest match first
    for suffix, exchange in sorted(SUFFIX_TO_EXCHANGE.items(), key=lambda x: -len(x[0])):
        if t.endswith(suffix):
            return exchange
    return None


def normalize_exchange(exchange_val):
    """Normalize raw exchange strings (e.g. 'NMS') to clean names (e.g. 'NASDAQ')."""
    if not exchange_val or pd.isna(exchange_val):
        return ""
    ex = str(exchange_val).strip().upper()
    for key, normalized in EXCHANGE_NORMALIZE.items():
        if key == ex:
            return normalized
    return str(exchange_val).strip()


def normalize_company_for_comparison(name):
    """Strip suffixes like Inc, Corp, PLC, Ltd, Holdings, Co for fuzzy matching."""
    if not name or pd.isna(name):
        return ""
    s = str(name).strip().lower()
    s = re.sub(r'\b(inc|corp|corporation|plc|ltd|limited|holdings|co|company|group|se|ag|sa|nv)\b', '', s)
    s = re.sub(r'[.,\s]+', ' ', s).strip()
    return s
