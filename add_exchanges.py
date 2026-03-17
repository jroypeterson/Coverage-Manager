import pandas as pd
import yfinance as yf
from datetime import date
import warnings
import time

from ticker_utils import CSV_PATH, get_exchange_from_suffix, normalize_exchange

warnings.filterwarnings("ignore")


def lookup_us_exchanges(tickers):
    """Look up exchanges for US tickers via yfinance."""
    results = {}
    batch_size = 50
    total = len(tickers)
    total_batches = (total + batch_size - 1) // batch_size

    for i in range(0, total, batch_size):
        batch = tickers[i:i + batch_size]
        batch_num = i // batch_size + 1
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} tickers)...")
        for t in batch:
            try:
                info = yf.Ticker(t).fast_info
                exchange = getattr(info, "exchange", None)
                if exchange:
                    results[t] = normalize_exchange(exchange)
            except Exception:
                pass
        time.sleep(0.5)  # Brief pause between batches

    return results


def main():
    print("Reading CSV...")
    df = pd.read_csv(CSV_PATH)
    print(f"Total rows: {len(df)}")

    # Determine which tickers need yfinance lookup
    exchanges = []
    us_tickers_to_lookup = []
    us_ticker_indices = []

    for idx, row in df.iterrows():
        ticker = str(row.get("Ticker", "")).strip()
        if not ticker or ticker == "#N/A":
            exchanges.append("")
            continue

        # Try suffix-based mapping first
        exch = get_exchange_from_suffix(ticker)
        if exch:
            exchanges.append(exch)
        else:
            # Assume US ticker, needs lookup
            exchanges.append(None)  # placeholder
            us_tickers_to_lookup.append(ticker)
            us_ticker_indices.append(idx)

    print(f"Exchange determined from suffix: {sum(1 for e in exchanges if e and e is not None)}")
    print(f"US tickers to look up: {len(us_tickers_to_lookup)}")
    print("Looking up US exchanges via yfinance...")

    us_results = lookup_us_exchanges(us_tickers_to_lookup)
    print(f"Successfully resolved: {len(us_results)} exchanges")

    # Fill in US results
    for ticker, idx in zip(us_tickers_to_lookup, us_ticker_indices):
        exchanges[idx] = us_results.get(ticker, "")

    # Insert Exchange column after Ticker
    df.insert(1, "Exchange", exchanges)

    # Save
    df.to_csv(CSV_PATH, index=False)
    print(f"Saved updated CSV with Exchange column: {CSV_PATH}")
    print("Done!")


if __name__ == "__main__":
    main()
