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
    # Nordic B-shares. The universe carries Bloomberg-style symbols ("COLOB DC")
    # and the space-suffix rule yields "COLOB.CO", but Yahoo writes the share
    # class with a hyphen: "COLO-B.CO". No Exchange value can express that, so
    # these are mapped by company name. Both previously returned a MUTUALFUND
    # quoteType with no name, i.e. no usable fundamentals at all.
    "Coloplast A/S": "COLO-B.CO",
    "Getinge AB": "GETI-B.ST",
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

# Country full name → ISO 3166-1 alpha-2 code. This is the COUNTRY's identity
# code, 1:1 by construction — used to normalize vendor country fields (FMP
# sometimes returns "US" for "United States") and to compare against the
# alpha-2 incorporation codes in SEC N-PORT filings. It is NOT the set of ISIN
# prefixes a country's issuers may use; that is a different fact and lives in
# COUNTRY_TO_ISIN_PREFIXES below. Conflating the two is how Jersey issuers
# using GB-prefixed ISINs would break either map.
#
# Completed 2026-07-28 (Codex R3): previously missing Ireland, Netherlands,
# Israel, Singapore, the offshore incorporation set (Cayman/Bermuda/BVI/
# Jersey/Guernsey/IoM/Panama), Austria, Hungary, Iceland, Indonesia and
# Estonia — every one either appears in the live universe's Country columns
# or its prefix appears in a stored ISIN. `validate_country_prefix_coverage`
# (universe/validation.py) warns when the live CSV outgrows this map again.
COUNTRY_TO_ISO2 = {
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
    # Added 2026-07-28 — present in the live universe's country columns:
    "Austria": "AT", "Hungary": "HU", "Iceland": "IS", "Indonesia": "ID",
    "Ireland": "IE", "Israel": "IL", "Netherlands": "NL", "Singapore": "SG",
    "Cayman Islands": "KY",
    # Added 2026-08-06 — arrived with the exhaustive-biopharma batch's
    # enrichment. Both are US-listed with a foreign HQ (COSM, BGMS), which is
    # exactly the shape the prefix guard exists for, and an unmapped country
    # makes that guard skip the row SILENTLY rather than fail it.
    "Greece": "GR", "Malaysia": "MY",
    # Added 2026-07-28 — offshore incorporation countries whose prefixes
    # appear in stored ISINs (or, for Panama/BVI/IoM, in the standard
    # offshore set the crosscheck's incorporation notes report):
    "Bermuda": "BM", "British Virgin Islands": "VG", "Jersey": "JE",
    "Guernsey": "GG", "Isle of Man": "IM", "Panama": "PA", "Estonia": "EE",
}

# Extra ISIN prefixes a country's issuers legitimately use BEYOND the
# country's own ISO code. The known case is the Crown dependencies:
# Channel-Islands/Isle-of-Man issuers commonly issue under GB as well as
# their own JE/GG/IM. The reverse is deliberately NOT loosened — United
# Kingdom stays {GB}, because accepting JE/GG/IM on every UK row would
# weaken the guard for the whole UK book; a Guernsey-incorporated UK
# company (OKYO's shape) is the `Country (Incorporation)` question, blocked
# pending JP's taxonomy decision. Euroclear "XS"/"EU" prefixes are debt-
# market shapes; no equity in the live universe carries one, so they are
# deliberately not accepted anywhere.
_EXTRA_ISIN_PREFIXES = {
    "Jersey": frozenset({"GB"}),
    "Guernsey": frozenset({"GB"}),
    "Isle of Man": frozenset({"GB"}),
}

# Country full name → frozenset of acceptable ISIN prefixes. Used by enrich
# to sanity-check ISINs against the row's countries — yfinance occasionally
# returns a wrong-country ISIN for rebranded or recycled tickers (e.g. "FI"
# returned a Swiss ISIN for Fiserv after the FISV→FI rebrand). Set-valued
# because a country can legitimately map to more than one prefix (see
# _EXTRA_ISIN_PREFIXES); derived from COUNTRY_TO_ISO2 so the two maps
# cannot drift apart.
COUNTRY_TO_ISIN_PREFIXES = {
    country: frozenset({iso2}) | _EXTRA_ISIN_PREFIXES.get(country, frozenset())
    for country, iso2 in COUNTRY_TO_ISO2.items()
}

# ── Functions ───────────────────────────────────────────────────────────────


# Structural shape of an ISIN (ISO 6166): 2-letter country/agency code,
# 9 alphanumerics (the NSIN), 1 check digit.
_ISIN_SHAPE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


def isin_check_digit_ok(isin):
    """True iff `isin` is a structurally valid ISIN with a correct ISO 6166
    check digit. Arithmetic only — no vendor, no network, deterministic.

    Promoted from tests/test_foreign_crosscheck.py on 2026-07-28 so the enrich
    write path can reject a malformed ISIN before any network check, and so a
    single implementation exists (two copies of a checksum is how they drift).

    Input normalization: case-folded, all whitespace stripped — a hand-edited
    cell with stray spaces or lower case still carries the same value, so it
    is judged on the value.

    Contract for everything else: **False.** Blank/None, wrong length,
    non-alphanumeric characters, a digit where the country code belongs, a
    letter where the check digit belongs — all False, never an exception and
    never "unknown". Callers treat False as "do not store this"; a malformed
    value that cannot be checksummed is exactly a value that must not be
    stored (the live case: `CSU` carried `NET000CLBR01`, which is not
    structurally an ISIN at all).
    """
    s = "".join(str(isin or "").split()).upper()
    if not _ISIN_SHAPE.fullmatch(s):
        return False
    # ISO 6166 mod-10 (Luhn): letters expand to two digits via base-36, then
    # double every other digit from the right of the stem.
    digits = "".join(str(int(c, 36)) if c.isalpha() else c for c in s)
    total, double = 0, True
    for ch in reversed(digits[:-1]):
        d = int(ch) * (2 if double else 1)
        total += d - 9 if d > 9 else d
        double = not double
    return (10 - total % 10) % 10 == int(digits[-1])


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


# ADR / depositary-receipt boilerplate. The universe records the INSTRUMENT
# ("Can Fite Biopharma ADR Representing 300 Ord Shs") where data providers record
# the ISSUER ("Can-Fite BioPharma Ltd."), which scored ~0.5 and produced a
# standing "company name mismatch" flag every week for 22 rows. Word boundaries
# are load-bearing: "Cadrenal", "Madrigal" and "Adrian" all contain "adr".
_ADR_BOILERPLATE = re.compile(
    r"\s*[-,]?\s*\b(sponsored\s+)?adrs?\b.*$"      # "- ADR", "ADR Class B", ...
    r"|\s*representing\s+.*$"                       # "Representing 300 Ord Shs"
    r"|\s*\b(ordinary|ord)\s+(shares|shs)\b.*$",
    re.I,
)


def normalize_company_for_comparison(name):
    """Strip corporate suffixes and ADR boilerplate for fuzzy name matching.

    Verified across the full universe (2026-07-27): stripping the ADR wrapper
    introduces ZERO new normalized-name collisions, so it cannot manufacture a
    false duplicate-company warning in `validate_no_duplicate_companies`.
    """
    if not name or pd.isna(name):
        return ""
    s = _ADR_BOILERPLATE.sub("", str(name).strip())
    s = s.strip(" -,").lower()
    # Drop periods BEFORE stripping legal forms, so dotted spellings reduce to
    # the same token as undotted ones. Without this, "N.V." survives as the two
    # tokens "n v" and "Cosmo Pharmaceuticals N.V." vs "Cosmo N.V." scored 0.53
    # -- a standing weekly mismatch flag between a company and itself. Likewise
    # "S.A." vs "SA". Only periods: "/" is left alone so "Genmab A/S" is
    # untouched, and collapsing it would risk merging genuinely distinct names.
    s = s.replace(".", "")
    s = re.sub(r'\b(inc|corp|corporation|plc|ltd|limited|holdings|co|company|group|se|ag|sa|nv)\b', '', s)
    s = re.sub(r'[,\s]+', ' ', s).strip()
    return s


# Share-class / exchange separators, stripped for symbol EQUALITY comparisons so
# a universe "BRK.B" matches SEC's "BRK-B". Deliberately NOT part of
# `normalize_ticker` (which produces a yfinance-callable symbol) — this form is
# a matching key only and must never be used to make a request.
_SYMBOL_SEPARATORS = re.compile(r"[.\-/ ]")


def normalize_symbol_for_matching(ticker):
    """Separator-insensitive symbol key ("BRK.B" and "BRK-B" -> "BRKB").

    Shared by `ticker_change_check` (SEC symbol comparison) and `cik_backfill`
    (SEC CIK lookup); both compare against SEC's bulk `company_tickers.json`,
    which writes share classes with a hyphen while the universe CSV commonly
    carries a dot.
    """
    return _SYMBOL_SEPARATORS.sub("", str(ticker or "").strip().upper())


def write_universe_csv(df, path=CSV_PATH):
    """Write the coverage-universe CSV back in its canonical encoding.

    Pairs with `read_universe_csv`. The committed master AND the published
    `exports/universe.csv` snapshot both carry a UTF-8 BOM, so that is the
    canonical form; a writer using pandas' default UTF-8 silently strips it,
    producing a spurious whole-file first-line diff and flipping the header cell
    a stdlib-`csv` consumer sees between ``Ticker`` and ``﻿Ticker``.
    Reads were centralized here in 2026-06-20 for the same class of reason;
    writes were not, and drifted.

    All seven whole-file writers use this as of 2026-07-26 (`add_exchanges`,
    `cleanup`, `enrich`, `lei_backfill`, `ipo_backfill`, `cik_backfill`,
    `discovery/candidates`). `tests/test_universe_csv_roundtrip.py` fails if any
    of them regresses to a bare `df.to_csv` -- the six-way drift is what made
    the file's encoding depend on which step happened to write last.
    """
    # utf-8 WITHOUT the BOM. utf-8-sig here put a BOM on the master CSV on 2026-07-25;
    # the export step then read fieldnames as plain utf-8, so "﻿Ticker" != "Ticker"
    # and DictWriter silently dropped the join key from every published row. Readers
    # using utf-8-sig handle a BOM-free file fine; the reverse is not true, so BOM-free
    # is the safe direction for a file ~20 sibling projects consume.
    df.to_csv(path, index=False, encoding="utf-8")
