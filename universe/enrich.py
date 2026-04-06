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

from ticker_utils import (
    CSV_PATH, normalize_ticker, MANUAL_TICKER_MAP,
    EXCHANGE_TO_FIGI, EXCHANGE_TO_COUNTRY, COUNTRY_TO_ISO,
    normalize_company_for_comparison, backup_csv,
)
from logging_utils import configure_logging, get_logger, log_exception

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

            # ISIN
            try:
                isin = t.isin
                if isin and isin != "-" and "error" not in str(isin).lower():
                    data["ISIN"] = isin
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
