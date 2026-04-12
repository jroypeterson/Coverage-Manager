"""Enrich coverage CSV with comprehensive company identifiers.

Adds 16 new columns (ISIN, FIGI, CIK, Country, Currency, etc.) and renames
Sector -> Sector (JP), Subsector -> Subsector (JP). Safe to re-run (idempotent).
"""

import pandas as pd
import yfinance as yf
import requests
import time
import os
from datetime import datetime

from config import ALLOWED_SECTORS_JP, API_KEYS
from ticker_utils import (
    CSV_PATH, normalize_ticker, MANUAL_TICKER_MAP,
    EXCHANGE_TO_FIGI, EXCHANGE_TO_COUNTRY, COUNTRY_TO_ISO,
    COUNTRY_TO_ISIN_PREFIX,
    normalize_company_for_comparison, backup_csv,
)
from logging_utils import configure_logging, get_logger, log_exception
from providers.fmp_provider import fetch_profile as _fmp_fetch_profile


class EnrichError(Exception):
    """Raised when single-ticker enrichment cannot produce a viable universe row."""

logger = get_logger("enrich_identifiers")

# New columns in desired order
NEW_COLUMNS_ORDER = [
    "Ticker", "Exchange", "Exchange Code", "Exchange Full Name",
    "Listing Type", "Other Listings", "Year Listed",
    "ISIN", "FIGI", "Composite FIGI", "Share Class FIGI", "CIK",
    "Company Name", "Country (HQ)", "Country (Listing)", "Country (ISO)",
    "Currency", "Website",
    "YF Sector", "YF Industry", "Sector (JP)", "Subsector (JP)",
]



def cell_is_empty(val):
    """Check if a cell value is empty/missing."""
    if val is None or pd.isna(val):
        return True
    s = str(val).strip()
    return s == "" or s == "nan"


def validate_isin_for_row(isin, row, ticker=""):
    """Return `isin` if its 2-letter country prefix matches the row's listing
    country (or HQ as fallback), else `None`. Logs a warning on mismatch.

    yfinance occasionally returns a wrong-country ISIN for rebranded tickers
    — observed with the new "FI" ticker for Fiserv, which returned a Swiss
    ISIN after the FISV→FI rebrand. This guard rejects such mismatches so
    they don't silently land in the universe CSV.

    Behavior when the row has no country info or the country isn't in
    `COUNTRY_TO_ISIN_PREFIX`: no check is applied, the ISIN is accepted.
    """
    if not isin:
        return None
    s = str(isin).strip()
    if not s or s == "-" or "error" in s.lower():
        return None
    expected_prefix = None
    checked_country = ""
    for country_field in ("Country (Listing)", "Country (HQ)"):
        country = str(row.get(country_field, "") or "").strip()
        if country and country in COUNTRY_TO_ISIN_PREFIX:
            expected_prefix = COUNTRY_TO_ISIN_PREFIX[country]
            checked_country = country
            break
    isin_prefix = s[:2].upper()
    if expected_prefix and isin_prefix != expected_prefix:
        logger.warning(
            "ISIN mismatch for %s: got %s (prefix %s) but row is listed in "
            "%s (expected prefix %s) — rejecting",
            ticker or "?", s, isin_prefix, checked_country, expected_prefix,
        )
        return None
    return s


# ── Single-ticker enrichment ────────────────────────────────────────────────
# Used by `universe.watchlist.add(..., create_if_missing=True)` to build a
# universe row for a brand-new ticker without running the full 1091-row
# enrich pipeline. FMP's `/stable/profile` endpoint covers US names cleanly
# in one call; yfinance + OpenFIGI + SEC EDGAR fill in whatever FMP misses.

_UNIVERSE_ROW_COLUMNS = [
    "Ticker", "Exchange", "Exchange Code", "Exchange Full Name",
    "Listing Type", "Other Listings", "Year Listed",
    "ISIN", "FIGI", "Composite FIGI", "Share Class FIGI", "CIK",
    "Company Name", "Country (HQ)", "Country (Listing)", "Country (ISO)",
    "Currency", "Website",
    "YF Sector", "YF Industry", "Sector (JP)", "Subsector (JP)", "Core",
]

# FMP's exchange names → Coverage Manager's standard Exchange column values.
_FMP_EXCHANGE_NORMALIZE = {
    "NASDAQ Global Select": "NASDAQ",
    "NASDAQ Global Market": "NASDAQ",
    "NASDAQ Capital Market": "NASDAQ",
    "NASDAQ": "NASDAQ",
    "New York Stock Exchange": "NYSE",
    "New York Stock Exchange Arca": "NYSE Arca",
    "NYSE": "NYSE",
    "NYSE American": "NYSE American",
    "NYSEArca": "NYSE Arca",
    "AMEX": "NYSE American",
    "BATS": "BATS",
    "OTC": "OTC",
}


def validate_sector_jp(sector):
    """Raise `EnrichError` if `sector` is not in the user-curated taxonomy."""
    if not sector or not str(sector).strip():
        raise EnrichError(
            "sector_jp is required when creating a new universe row — "
            f"must be one of: {sorted(ALLOWED_SECTORS_JP)}"
        )
    if sector not in ALLOWED_SECTORS_JP:
        raise EnrichError(
            f"unknown Sector (JP): {sector!r}. Allowed values: "
            f"{sorted(ALLOWED_SECTORS_JP)}"
        )


def _fetch_fmp_profile(ticker):
    """Hit FMP `/stable/profile` for a single ticker. Returns dict or {}.

    Delegates to the shared fmp_provider.fetch_profile implementation.
    """
    key = API_KEYS.get("FMP_API_KEY", "")
    return _fmp_fetch_profile(ticker, key)


def _normalize_fmp_exchange(fmp_exchange_full, fmp_exchange_short):
    """Pick a Coverage-Manager-canonical exchange name from FMP's fields."""
    for candidate in (fmp_exchange_full, fmp_exchange_short):
        if not candidate:
            continue
        s = str(candidate).strip()
        if s in _FMP_EXCHANGE_NORMALIZE:
            return _FMP_EXCHANGE_NORMALIZE[s]
    # Fall back to short code if it already matches a known exchange name
    s = str(fmp_exchange_short or "").strip()
    if s and s in EXCHANGE_TO_COUNTRY:
        return s
    return ""


def _empty_row():
    return {c: "" for c in _UNIVERSE_ROW_COLUMNS}


# Reverse of COUNTRY_TO_ISIN_PREFIX, built lazily, for normalizing FMP's
# country field (which sometimes returns ISO 3166 alpha-2 codes like "US"
# instead of full names like "United States").
_ISIN_PREFIX_TO_COUNTRY = {v: k for k, v in COUNTRY_TO_ISIN_PREFIX.items()}


def _normalize_country_name(raw):
    """Return a full country name for `raw`, which may be an ISO alpha-2
    code (FMP sometimes returns "US" instead of "United States") or the
    full name already. Unknown values pass through unchanged."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if len(s) == 2 and s.upper() in _ISIN_PREFIX_TO_COUNTRY:
        return _ISIN_PREFIX_TO_COUNTRY[s.upper()]
    return s


def enrich_single_ticker(ticker, sector_jp, exchange_hint=None):
    """Build a full universe-CSV row for a brand-new ticker.

    Contract:
      - Validates `sector_jp` against the ALLOWED_SECTORS_JP taxonomy.
      - Primary source: FMP `/stable/profile` (ISIN, CIK, IPO year, sector,
        industry, website, exchange, currency, country, company name).
      - Fallback: yfinance `Ticker.info` + `Ticker.isin` for anything FMP
        left blank. Uses `exchange_hint` to normalize the yfinance symbol
        when the ticker has a regional suffix.
      - FIGI fields come from OpenFIGI (`fetch_openfigi_identifiers` on a
        single-row DataFrame).
      - CIK fallback: SEC EDGAR bulk map (for when FMP omits CIK on non-US
        names or when the SEC map lags a ticker rebrand).
      - Runs `validate_isin_for_row` so a wrong-country ISIN from yfinance
        never lands in the row.

    Raises `EnrichError` when the result is missing any of the
    watchlist-required metadata fields: Company Name, Sector (JP), Currency,
    Exchange. `Sector (JP)` comes from the caller; the other three must come
    from the data sources.

    Returns a dict keyed by the universe CSV's column names, suitable for
    appending to `data/coverage_universe_tickers.csv` via csv.DictWriter.
    """
    validate_sector_jp(sector_jp)

    ticker = str(ticker or "").strip()
    if not ticker:
        raise EnrichError("ticker is required")

    row = _empty_row()
    row["Ticker"] = ticker
    row["Sector (JP)"] = sector_jp
    row["Listing Type"] = "Primary"

    sources_used = []

    # ── 1. FMP /stable/profile ───────────────────────────────────────────
    fmp = _fetch_fmp_profile(ticker)
    if fmp:
        sources_used.append("fmp")
        row["Company Name"] = str(fmp.get("companyName", "") or "").strip()
        row["ISIN"] = str(fmp.get("isin", "") or "").strip()
        cik = str(fmp.get("cik", "") or "").strip().lstrip("0")
        if cik:
            row["CIK"] = cik
        ipo = str(fmp.get("ipoDate", "") or "").strip()
        if ipo and len(ipo) >= 4:
            row["Year Listed"] = ipo[:4]
        row["Website"] = str(fmp.get("website", "") or "").strip()
        row["YF Sector"] = str(fmp.get("sector", "") or "").strip()
        row["YF Industry"] = str(fmp.get("industry", "") or "").strip()
        row["Currency"] = str(fmp.get("currency", "") or "").strip()
        row["Country (HQ)"] = _normalize_country_name(fmp.get("country", ""))
        exch = _normalize_fmp_exchange(
            fmp.get("exchangeFullName"), fmp.get("exchange")
        )
        if exch:
            row["Exchange"] = exch

    # Exchange hint override (non-US cases where FMP is weak)
    if exchange_hint:
        row["Exchange"] = exchange_hint

    # ── 2. Derive country (Listing)/(ISO) from exchange ──────────────────
    if row["Exchange"] and not row["Country (Listing)"]:
        row["Country (Listing)"] = EXCHANGE_TO_COUNTRY.get(row["Exchange"], "")
    if row["Country (HQ)"] and not row["Country (Listing)"]:
        row["Country (Listing)"] = row["Country (HQ)"]
    if row["Country (Listing)"]:
        row["Country (ISO)"] = COUNTRY_TO_ISO.get(row["Country (Listing)"], "")

    # ── 3. yfinance fallback for empty fields ────────────────────────────
    needs_yf = not all(row[c] for c in ("Company Name", "Currency", "ISIN", "Year Listed"))
    if needs_yf:
        try:
            yf_ticker = normalize_ticker(
                ticker,
                company_name=row.get("Company Name", ""),
                exchange=row.get("Exchange", ""),
            )
            if yf_ticker:
                yt = yf.Ticker(yf_ticker)
                sources_used.append("yfinance")

                if not row["ISIN"]:
                    try:
                        candidate_isin = yt.isin
                        checked = validate_isin_for_row(candidate_isin, row, ticker=ticker)
                        if checked:
                            row["ISIN"] = checked
                    except Exception as e:
                        log_exception(logger, f"yfinance ISIN failed for {ticker}", e)

                try:
                    info = yt.info or {}
                except Exception as e:
                    log_exception(logger, f"yfinance info failed for {ticker}", e)
                    info = {}

                if info:
                    if not row["Company Name"]:
                        row["Company Name"] = str(info.get("longName", "") or info.get("shortName", "")).strip()
                    if not row["Exchange Code"]:
                        row["Exchange Code"] = str(info.get("exchange", "") or "").strip()
                    if not row["Exchange Full Name"]:
                        row["Exchange Full Name"] = str(info.get("fullExchangeName", "") or "").strip()
                    if not row["Currency"]:
                        row["Currency"] = str(info.get("currency", "") or "").strip()
                    if not row["Country (HQ)"]:
                        row["Country (HQ)"] = str(info.get("country", "") or "").strip()
                    if not row["Website"]:
                        row["Website"] = str(info.get("website", "") or "").strip()
                    if not row["YF Sector"]:
                        row["YF Sector"] = str(info.get("sector", "") or "").strip()
                    if not row["YF Industry"]:
                        row["YF Industry"] = str(info.get("industry", "") or "").strip()
                    if not row["Year Listed"]:
                        first_trade_ms = info.get("firstTradeDateEpochUtc") or info.get("firstTradeDateMilliseconds")
                        if first_trade_ms:
                            if first_trade_ms > 1e12:
                                first_trade_ms = first_trade_ms / 1000
                            year = datetime.utcfromtimestamp(first_trade_ms).year
                            if 1900 < year <= datetime.now().year:
                                row["Year Listed"] = str(year)
        except Exception as e:
            log_exception(logger, f"yfinance fallback failed for {ticker}", e)

    # Re-derive country fields if yfinance filled Country (HQ)
    if row["Country (HQ)"] and not row["Country (Listing)"]:
        row["Country (Listing)"] = row["Country (HQ)"]
    if row["Country (Listing)"] and not row["Country (ISO)"]:
        row["Country (ISO)"] = COUNTRY_TO_ISO.get(row["Country (Listing)"], "")

    # ── 4. OpenFIGI for FIGI fields ──────────────────────────────────────
    if not row["FIGI"] or not row["Composite FIGI"]:
        try:
            mini_df = pd.DataFrame([{
                "Ticker": ticker,
                "Company Name": row["Company Name"],
                "Exchange": row["Exchange"],
            }])
            figi_map = fetch_openfigi_identifiers(mini_df)
            figi_data = figi_map.get(ticker, {})
            if figi_data:
                sources_used.append("openfigi")
                for key in ("FIGI", "Composite FIGI", "Share Class FIGI"):
                    if not row[key] and figi_data.get(key):
                        row[key] = figi_data[key]
        except Exception as e:
            log_exception(logger, f"OpenFIGI lookup failed for {ticker}", e)

    # ── 5. SEC EDGAR CIK fallback ────────────────────────────────────────
    if not row["CIK"]:
        try:
            cik_map = fetch_sec_cik_map()
            cik = cik_map.get(ticker.upper(), "")
            if cik:
                row["CIK"] = cik
                sources_used.append("sec")
        except Exception as e:
            log_exception(logger, f"SEC CIK lookup failed for {ticker}", e)

    logger.info(
        "enrich_single_ticker(%s): sources=%s",
        ticker, ",".join(sources_used) or "none",
    )

    # ── 6. Validate required watchlist metadata fields ───────────────────
    required = ("Company Name", "Exchange", "Currency", "Sector (JP)")
    missing = [f for f in required if not row[f]]
    if missing:
        raise EnrichError(
            f"could not resolve required metadata for {ticker}: missing "
            f"{', '.join(missing)}. Sources tried: {sources_used or ['none']}. "
            f"Check the ticker spelling or pass --exchange for non-US names."
        )

    return row


def fetch_yfinance_identifiers(df):
    """Fetch identifiers from yfinance for all tickers.

    Returns dict of {original_ticker: {field: value, ...}}.
    """
    results = {}
    total = len(df)

    for i, (_, row) in enumerate(df.iterrows()):
        orig_ticker = str(row["Ticker"]).strip()
        company = str(row.get("Company Name", "")).strip()
        exchange = str(row.get("Exchange", "")).strip()
        yf_ticker = normalize_ticker(orig_ticker, company, exchange)

        if not yf_ticker:
            continue

        if i > 0 and i % 50 == 0:
            logger.info("yfinance: %s/%s...", i, total)

        data = {}
        try:
            t = yf.Ticker(yf_ticker)

            # ISIN — sanity-checked against the row's listing country.
            try:
                checked = validate_isin_for_row(t.isin, row, ticker=orig_ticker)
                if checked:
                    data["ISIN"] = checked
            except Exception as e:
                log_exception(logger, f"ISIN lookup failed for {orig_ticker}", e)

            # Info dict
            try:
                info = t.info
                if info:
                    data["Exchange Code"] = info.get("exchange", "")
                    data["Exchange Full Name"] = info.get("fullExchangeName", "")
                    data["Currency"] = info.get("currency", "")
                    data["Country (HQ)"] = info.get("country", "")
                    data["Website"] = info.get("website", "")
                    data["YF Sector"] = info.get("sector", "")
                    data["YF Industry"] = info.get("industry", "")

                    # Year Listed from firstTradeDateMilliseconds
                    first_trade_ms = info.get("firstTradeDateEpochUtc") or info.get("firstTradeDateMilliseconds")
                    if first_trade_ms:
                        # firstTradeDateEpochUtc is in seconds, firstTradeDateMilliseconds in ms
                        if first_trade_ms > 1e12:
                            first_trade_ms = first_trade_ms / 1000
                        year = datetime.utcfromtimestamp(first_trade_ms).year
                        if 1900 < year <= datetime.now().year:
                            data["Year Listed"] = str(year)
            except Exception as e:
                log_exception(logger, f"Info lookup failed for {orig_ticker}", e)

        except Exception as e:
            log_exception(logger, f"Ticker lookup failed for {orig_ticker}", e)

        results[orig_ticker] = data
        time.sleep(0.05)  # Light rate limiting

    return results


def fetch_openfigi_identifiers(df):
    """Fetch FIGI identifiers from OpenFIGI API.

    Returns dict of {original_ticker: {figi, composite_figi, share_class_figi}}.
    """
    results = {}

    # Build request items
    items = []
    for _, row in df.iterrows():
        orig_ticker = str(row["Ticker"]).strip()
        exchange = str(row.get("Exchange", "")).strip()
        company = str(row.get("Company Name", "")).strip()

        yf_ticker = normalize_ticker(orig_ticker, company, exchange)
        if not yf_ticker:
            continue

        # Extract the base symbol for OpenFIGI
        # For tickers like "7733.T", use "7733"; for "ABBV", use "ABBV"
        base_symbol = yf_ticker.split(".")[0] if "." in yf_ticker else yf_ticker

        figi_exch = EXCHANGE_TO_FIGI.get(exchange, "")

        item = {"idType": "TICKER", "idValue": base_symbol}
        if figi_exch:
            item["exchCode"] = figi_exch
        items.append((orig_ticker, item))

    # Free tier (no API key) allows max 10 items per request
    batch_size = 10
    total_batches = (len(items) + batch_size - 1) // batch_size
    url = "https://api.openfigi.com/v3/mapping"
    headers = {"Content-Type": "application/json"}

    for batch_idx in range(0, len(items), batch_size):
        batch = items[batch_idx:batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1
        logger.info("OpenFIGI batch %s/%s (%s items)...", batch_num, total_batches, len(batch))

        payload = [item for _, item in batch]
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                response_data = resp.json()
                for j, (orig_ticker, _) in enumerate(batch):
                    if j < len(response_data):
                        entry = response_data[j]
                        if "data" in entry and entry["data"]:
                            d = entry["data"][0]
                            results[orig_ticker] = {
                                "FIGI": d.get("figi", ""),
                                "Composite FIGI": d.get("compositeFIGI", ""),
                                "Share Class FIGI": d.get("shareClassFIGI", ""),
                            }
            elif resp.status_code == 429:
                logger.warning("OpenFIGI rate limited, waiting 10s...")
                time.sleep(10)
                # Retry this batch
                try:
                    resp = requests.post(url, json=payload, headers=headers, timeout=30)
                    if resp.status_code == 200:
                        response_data = resp.json()
                        for j, (orig_ticker, _) in enumerate(batch):
                            if j < len(response_data):
                                entry = response_data[j]
                                if "data" in entry and entry["data"]:
                                    d = entry["data"][0]
                                    results[orig_ticker] = {
                                        "FIGI": d.get("figi", ""),
                                        "Composite FIGI": d.get("compositeFIGI", ""),
                                        "Share Class FIGI": d.get("shareClassFIGI", ""),
                                    }
                except Exception as e:
                    log_exception(logger, "OpenFIGI retry failed", e)
            else:
                logger.warning("OpenFIGI error: HTTP %s", resp.status_code)
        except Exception as e:
            log_exception(logger, "OpenFIGI request error", e)

        # Rate limiting: 25 req/min without API key
        if batch_num < total_batches:
            time.sleep(3)

    return results


def fetch_sec_cik_map():
    """Download SEC EDGAR bulk ticker->CIK mapping.

    Returns dict of {TICKER: cik_number_string}.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": "CoverageManager/1.0 (coverage-research@example.com)"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.warning("SEC EDGAR error: HTTP %s", resp.status_code)
            return {}
        data = resp.json()
        cik_map = {}
        for entry in data.values():
            ticker = str(entry.get("ticker", "")).strip().upper()
            cik = entry.get("cik_str")
            if ticker and cik:
                cik_map[ticker] = str(cik)
        logger.info("SEC EDGAR: loaded %s ticker->CIK mappings", len(cik_map))
        return cik_map
    except Exception as e:
        log_exception(logger, "SEC EDGAR error", e)
        return {}


def detect_listing_type_and_other_listings(df, yf_data):
    """Determine Listing Type and Other Listings for each ticker.

    Listing Type: "Primary" if company domicile matches exchange country,
                  "ADR/Cross-listed" if mismatch.
    Other Listings: other tickers in the CSV for the same company.
    """
    listing_types = {}
    other_listings = {}

    # Build normalized company name -> list of tickers
    name_to_tickers = {}
    for _, row in df.iterrows():
        ticker = str(row["Ticker"]).strip()
        company = str(row.get("Company Name", "")).strip()
        if not company or company == "nan":
            continue
        norm_name = normalize_company_for_comparison(company)
        if norm_name:
            name_to_tickers.setdefault(norm_name, []).append(ticker)

    for _, row in df.iterrows():
        ticker = str(row["Ticker"]).strip()
        exchange = str(row.get("Exchange", "")).strip()

        # Listing Type
        yf_info = yf_data.get(ticker, {})
        country_hq = yf_info.get("Country (HQ)", "")
        country_listing = EXCHANGE_TO_COUNTRY.get(exchange, "")

        if country_hq and country_listing:
            if country_hq.lower() == country_listing.lower():
                listing_types[ticker] = "Primary"
            else:
                listing_types[ticker] = "ADR/Cross-listed"
        else:
            listing_types[ticker] = ""

        # Other Listings - find other tickers with same normalized company name
        company = str(row.get("Company Name", "")).strip()
        if company and company != "nan":
            norm_name = normalize_company_for_comparison(company)
            siblings = name_to_tickers.get(norm_name, [])
            others = [t for t in siblings if t != ticker]
            if others:
                other_listings[ticker] = ", ".join(others)
            elif listing_types.get(ticker) == "ADR/Cross-listed" and country_hq:
                other_listings[ticker] = f"Primary listing likely in {country_hq}"
            else:
                other_listings[ticker] = ""
        else:
            other_listings[ticker] = ""

    return listing_types, other_listings


def enrich_dataframe(df, yf_data, figi_data, cik_map, listing_types, other_listings):
    """Add all new columns to the dataframe and reorder."""
    # Rename Sector -> Sector (JP), Subsector -> Subsector (JP)
    rename_map = {}
    if "Sector" in df.columns and "Sector (JP)" not in df.columns:
        rename_map["Sector"] = "Sector (JP)"
    if "Subsector" in df.columns and "Subsector (JP)" not in df.columns:
        rename_map["Subsector"] = "Subsector (JP)"
    if rename_map:
        df = df.rename(columns=rename_map)

    # Initialize new columns if they don't exist, and ensure string dtype
    new_cols = [
        "Exchange Code", "Exchange Full Name", "Listing Type", "Other Listings",
        "Year Listed", "ISIN", "FIGI", "Composite FIGI", "Share Class FIGI", "CIK",
        "Country (HQ)", "Country (Listing)", "Country (ISO)", "Currency", "Website",
        "YF Sector", "YF Industry",
    ]
    for col in new_cols:
        if col not in df.columns:
            df[col] = ""
        # Ensure column is object dtype so we can assign strings freely
        df[col] = df[col].astype(object)

    # Populate data (idempotent — only fill empty cells)
    for idx, row in df.iterrows():
        ticker = str(row["Ticker"]).strip()
        exchange = str(row.get("Exchange", "")).strip()
        yf_info = yf_data.get(ticker, {})
        figi_info = figi_data.get(ticker, {})

        # yfinance fields
        yf_fields = [
            "Exchange Code", "Exchange Full Name", "Currency",
            "Country (HQ)", "Website", "YF Sector", "YF Industry", "ISIN",
        ]
        for field in yf_fields:
            if cell_is_empty(row.get(field)):
                val = yf_info.get(field, "")
                if val and str(val).strip():
                    df.at[idx, field] = val

        # Year Listed
        if cell_is_empty(row.get("Year Listed")):
            year = yf_info.get("Year Listed")
            if year:
                df.at[idx, "Year Listed"] = year

        # FIGI fields
        for field in ["FIGI", "Composite FIGI", "Share Class FIGI"]:
            if cell_is_empty(row.get(field)):
                val = figi_info.get(field, "")
                if val:
                    df.at[idx, field] = val

        # CIK (US tickers only)
        if cell_is_empty(row.get("CIK")):
            cik = cik_map.get(ticker.upper())
            if cik:
                df.at[idx, "CIK"] = cik

        # Country (Listing) from exchange mapping
        if cell_is_empty(row.get("Country (Listing)")):
            country = EXCHANGE_TO_COUNTRY.get(exchange, "")
            if country:
                df.at[idx, "Country (Listing)"] = country

        # Country (ISO) from Country (Listing)
        if cell_is_empty(row.get("Country (ISO)")):
            country_listing = df.at[idx, "Country (Listing)"]
            if not cell_is_empty(country_listing):
                iso = COUNTRY_TO_ISO.get(str(country_listing).strip(), "")
                if iso:
                    df.at[idx, "Country (ISO)"] = iso

        # Listing Type
        if cell_is_empty(row.get("Listing Type")):
            lt = listing_types.get(ticker, "")
            if lt:
                df.at[idx, "Listing Type"] = lt

        # Other Listings
        if cell_is_empty(row.get("Other Listings")):
            ol = other_listings.get(ticker, "")
            if ol:
                df.at[idx, "Other Listings"] = ol

    # Reorder columns
    final_cols = [c for c in NEW_COLUMNS_ORDER if c in df.columns]
    # Add any extra columns not in our order (shouldn't happen, but safety)
    for c in df.columns:
        if c not in final_cols:
            final_cols.append(c)
    df = df[final_cols]

    return df


def print_summary(df, yf_data, figi_data, cik_map):
    """Print enrichment summary."""
    total = len(df)

    def count_filled(col):
        return sum(1 for _, row in df.iterrows() if not cell_is_empty(row.get(col)))

    print("\n" + "=" * 60)
    print("ENRICHMENT SUMMARY")
    print("=" * 60)
    print(f"Total tickers: {total}")
    print(f"\nColumn fill rates:")
    check_cols = [
        "Exchange Code", "Exchange Full Name", "Listing Type", "Year Listed",
        "ISIN", "FIGI", "Composite FIGI", "Share Class FIGI", "CIK",
        "Country (HQ)", "Country (Listing)", "Currency", "Website",
        "YF Sector", "YF Industry",
    ]
    for col in check_cols:
        if col in df.columns:
            filled = count_filled(col)
            pct = filled / total * 100 if total > 0 else 0
            print(f"  {col:25s}: {filled:4d}/{total} ({pct:.0f}%)")

    print(f"\nAPI results:")
    print(f"  yfinance: {len(yf_data)} tickers returned data")
    print(f"  OpenFIGI: {len(figi_data)} tickers matched")
    print(f"  SEC CIK:  {len(cik_map)} total mappings loaded")
    print("=" * 60)


def main():
    configure_logging()
    print("=" * 60)
    print("Coverage Universe Identifier Enrichment")
    print("=" * 60)

    # Step 1: Backup
    print("\n1. Creating backup...")
    backup_path = backup_csv(CSV_PATH)
    print(f"   Backup: {backup_path}")

    # Step 2: Load CSV
    print("\n2. Loading CSV...")
    df = pd.read_csv(CSV_PATH)
    print(f"   {len(df)} rows, columns: {list(df.columns)}")

    # Step 3: Fetch SEC CIK (single bulk download, fast)
    print("\n3. Fetching SEC CIK mappings...")
    cik_map = fetch_sec_cik_map()

    # Step 4: Fetch yfinance identifiers
    print(f"\n4. Fetching yfinance identifiers for {len(df)} tickers...")
    yf_data = fetch_yfinance_identifiers(df)
    print(f"   yfinance returned data for {len(yf_data)} tickers")

    # Step 5: Fetch OpenFIGI identifiers
    print(f"\n5. Fetching OpenFIGI identifiers...")
    figi_data = fetch_openfigi_identifiers(df)
    print(f"   OpenFIGI matched {len(figi_data)} tickers")

    # Step 6: Detect listing types
    print("\n6. Detecting listing types and cross-listings...")
    listing_types, other_listings = detect_listing_type_and_other_listings(df, yf_data)

    # Step 7: Enrich dataframe
    print("\n7. Enriching dataframe...")
    df = enrich_dataframe(df, yf_data, figi_data, cik_map, listing_types, other_listings)

    # Step 8: Save
    print("\n8. Saving enriched CSV...")
    df.to_csv(CSV_PATH, index=False)
    print(f"   Saved: {CSV_PATH}")

    # Summary
    print_summary(df, yf_data, figi_data, cik_map)


if __name__ == "__main__":
    main()
