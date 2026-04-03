"""One-time cleanup script for coverage_universe_tickers.csv.

Deduplicates rows, normalizes exchanges, fills missing company names/exchanges,
and tests price availability. Safe to re-run (idempotent).
"""

import pandas as pd
import yfinance as yf
from datetime import datetime
import re
import time
import os

from universe.ticker_utils import (
    CSV_PATH, SUFFIX_TO_EXCHANGE, SPACE_SUFFIX_TO_EXCHANGE,
    EXCHANGE_NORMALIZE, get_exchange_from_suffix, normalize_exchange,
    normalize_company_for_comparison, backup_csv,
)
from logging_utils import configure_logging, get_logger, log_exception

logger = get_logger("cleanup_tickers")




def pick_best_row(group):
    """Pick the row (as Series) with the most non-empty fields."""
    def filled_count(row):
        return sum(1 for v in row if pd.notna(v) and str(v).strip() != "")
    best_idx = max(group.index, key=lambda idx: filled_count(group.loc[idx]))
    return group.loc[best_idx]


def load_and_dedup(df):
    """Deduplicate rows by Ticker, flagging conflicts where company names differ."""
    clean_rows = []
    conflict_rows = []

    for ticker, group in df.groupby("Ticker"):
        if len(group) == 1:
            clean_rows.append(group.iloc[0])
            continue

        # Check if company names are the same (after normalization)
        names = group["Company Name"].fillna("").astype(str).str.strip()
        normalized = names.apply(normalize_company_for_comparison)
        unique_names = set(n for n in normalized if n)

        if len(unique_names) <= 1:
            # True duplicate — keep the most complete row
            clean_rows.append(pick_best_row(group))
        else:
            # Conflict — different company names for same ticker
            for _, row in group.iterrows():
                conflict_rows.append(row)
            # Still keep the best row in clean output
            clean_rows.append(pick_best_row(group))

    clean_df = pd.DataFrame(clean_rows).reset_index(drop=True)
    conflicts_df = pd.DataFrame(conflict_rows).reset_index(drop=True) if conflict_rows else None
    return clean_df, conflicts_df



def fill_missing_exchanges(df):
    """Fill empty Exchange values using suffix mapping then yfinance lookup."""
    changes = {}
    needs_lookup = []

    for idx, row in df.iterrows():
        exchange = str(row.get("Exchange", "")).strip()
        if exchange and exchange != "nan":
            # Normalize existing exchange values
            norm = normalize_exchange(exchange)
            if norm != exchange:
                df.at[idx, "Exchange"] = norm
                changes[row["Ticker"]] = f"{exchange} -> {norm} (normalized)"
            continue

        ticker = str(row.get("Ticker", "")).strip()
        if not ticker or ticker == "#N/A":
            continue

        # Try suffix-based mapping
        exch = get_exchange_from_suffix(ticker)
        if exch:
            df.at[idx, "Exchange"] = exch
            changes[ticker] = f"-> {exch} (suffix)"
        else:
            needs_lookup.append((idx, ticker))

    # Batch yfinance lookup for remaining
    if needs_lookup:
        logger.info("Looking up %s exchanges via yfinance...", len(needs_lookup))
        for idx, ticker in needs_lookup:
            try:
                info = yf.Ticker(ticker).fast_info
                raw_ex = getattr(info, "exchange", None)
                if raw_ex:
                    norm = normalize_exchange(raw_ex)
                    df.at[idx, "Exchange"] = norm
                    changes[ticker] = f"-> {norm} (yfinance)"
            except Exception as e:
                log_exception(logger, f"Exchange fill failed for {ticker}", e)
            time.sleep(0.1)

    return df, changes


def fill_missing_company_names(df):
    """Fill empty Company Name values using yfinance info."""
    changes = {}
    missing = []

    for idx, row in df.iterrows():
        name = str(row.get("Company Name", "")).strip()
        if name and name != "nan":
            continue
        ticker = str(row.get("Ticker", "")).strip()
        if not ticker or ticker == "#N/A":
            continue
        missing.append((idx, ticker))

    if not missing:
        return df, changes

    logger.info("Looking up %s company names via yfinance...", len(missing))
    for idx, ticker in missing:
        try:
            info = yf.Ticker(ticker).info
            long_name = info.get("longName") or info.get("shortName")
            if long_name:
                df.at[idx, "Company Name"] = long_name
                changes[ticker] = long_name
        except Exception as e:
            log_exception(logger, f"Company name lookup failed for {ticker}", e)
        time.sleep(0.1)

    return df, changes


def test_price_availability(df):
    """Test which tickers have no recent price data (potential delistings)."""
    from ticker_utils import normalize_ticker, MANUAL_TICKER_MAP

    no_data = []
    tickers = []
    ticker_to_orig = {}

    for _, row in df.iterrows():
        orig = str(row["Ticker"]).strip()
        company = str(row.get("Company Name", "")).strip()
        if not orig or orig == "#N/A":
            continue
        yf_t = normalize_ticker(orig, company)
        if yf_t:
            tickers.append(yf_t)
            ticker_to_orig[yf_t] = orig

    logger.info("Testing price availability for %s tickers...", len(tickers))
    batch_size = 20  # Smaller batches — yfinance silently returns NaN in large batches
    has_data = set()

    def download_batch(batch):
        """Download a batch and return set of tickers with data."""
        found = set()
        try:
            data = yf.download(batch, period="5d", progress=False, threads=True)
            if data.empty:
                return found
            close = data["Close"]
            if isinstance(close, pd.Series):
                if close.dropna().any():
                    found.add(batch[0])
            else:
                for t in batch:
                    if t in close.columns and close[t].dropna().any():
                        found.add(t)
        except Exception as e:
            log_exception(logger, f"Batch price availability lookup failed for {batch}", e)
        return found

    # First pass
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(tickers) + batch_size - 1) // batch_size
        logger.info("Batch %s/%s...", batch_num, total_batches)
        has_data.update(download_batch(batch))

    # Retry pass — individually check tickers that failed
    # (yfinance batch downloads can silently drop valid tickers or error on bad ones)
    missing_tickers = [t for t in tickers if t not in has_data]
    if missing_tickers:
        logger.info("Retrying %s tickers individually...", len(missing_tickers))
        for i, t in enumerate(missing_tickers):
            if i > 0 and i % 50 == 0:
                logger.info("%s/%s...", i, len(missing_tickers))
            try:
                data = yf.download(t, period="5d", progress=False)
                if not data.empty:
                    close = data["Close"]
                    if close.dropna().any():
                        has_data.add(t)
            except Exception as e:
                log_exception(logger, f"Single ticker price availability lookup failed for {t}", e)

    for yf_t in tickers:
        if yf_t not in has_data:
            no_data.append(ticker_to_orig.get(yf_t, yf_t))

    return no_data


def print_summary(backup_path, orig_count, clean_count, dupes_removed,
                  conflicts_df, exchange_changes, name_changes, no_data_tickers):
    """Print cleanup summary to console."""
    print("\n" + "=" * 60)
    print("CLEANUP SUMMARY")
    print("=" * 60)
    print(f"Backup saved to: {backup_path}")
    print(f"Original rows: {orig_count}")
    print(f"Clean rows: {clean_count}")
    print(f"Duplicates removed: {dupes_removed}")

    if conflicts_df is not None:
        print(f"Conflicts flagged for manual review: {len(conflicts_df)} rows")

    print(f"\nExchange changes: {len(exchange_changes)}")
    for ticker, change in list(exchange_changes.items())[:10]:
        print(f"  {ticker}: {change}")
    if len(exchange_changes) > 10:
        print(f"  ... and {len(exchange_changes) - 10} more")

    print(f"\nCompany names filled: {len(name_changes)}")
    for ticker, name in list(name_changes.items())[:10]:
        print(f"  {ticker}: {name}")
    if len(name_changes) > 10:
        print(f"  ... and {len(name_changes) - 10} more")

    print(f"\nTickers with NO price data (potential delistings): {len(no_data_tickers)}")
    for t in no_data_tickers:
        print(f"  {t}")

    print("=" * 60)


def main():
    configure_logging()
    print("=" * 60)
    print("Coverage Universe Ticker Cleanup")
    print("=" * 60)

    # Step 1: Backup
    print("\n1. Creating backup...")
    backup_path = backup_csv(CSV_PATH)
    print(f"   Backup: {backup_path}")

    # Step 2: Load and dedup
    print("\n2. Deduplicating tickers...")
    df = pd.read_csv(CSV_PATH)

    # Drop orphaned unnamed columns (e.g. from trailing commas)
    unnamed = [c for c in df.columns if c.startswith("Unnamed:")]
    if unnamed:
        df = df.drop(columns=unnamed)
        logger.info("Dropped orphaned columns: %s", unnamed)
    orig_count = len(df)
    df, conflicts_df = load_and_dedup(df)
    dupes_removed = orig_count - len(df)
    print(f"   {orig_count} rows -> {len(df)} rows ({dupes_removed} duplicates removed)")

    if conflicts_df is not None:
        conflict_path = os.path.join(
            os.path.dirname(CSV_PATH),
            f"cleanup_conflicts_{datetime.now().strftime('%Y%m%d')}.csv"
        )
        conflicts_df.to_csv(conflict_path, index=False)
        print(f"   Conflicts saved to: {conflict_path}")

    # Step 3: Normalize existing exchanges
    print("\n3. Normalizing and filling exchanges...")
    df, exchange_changes = fill_missing_exchanges(df)
    print(f"   {len(exchange_changes)} exchange changes")

    # Step 4: Fill missing company names
    print("\n4. Filling missing company names...")
    df, name_changes = fill_missing_company_names(df)
    print(f"   {len(name_changes)} names filled")

    # Step 5: Test price availability
    print("\n5. Testing price availability...")
    no_data_tickers = test_price_availability(df)

    # Step 6: Save cleaned CSV
    print("\n6. Saving cleaned CSV...")
    df.to_csv(CSV_PATH, index=False)
    print(f"   Saved: {CSV_PATH}")

    # Summary
    print_summary(backup_path, orig_count, len(df), dupes_removed,
                  conflicts_df, exchange_changes, name_changes, no_data_tickers)


if __name__ == "__main__":
    main()
