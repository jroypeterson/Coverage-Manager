import pandas as pd
import yfinance as yf
from datetime import date
import warnings
import time

from ticker_utils import CSV_PATH, get_exchange_from_suffix, normalize_exchange
from logging_utils import configure_logging, get_logger, log_exception

warnings.filterwarnings("ignore")
logger = get_logger("add_exchanges")


def lookup_us_exchanges(tickers):
    """Look up exchanges for US tickers via yfinance."""
    results = {}
    batch_size = 50
    total = len(tickers)
    total_batches = (total + batch_size - 1) // batch_size

    for i in range(0, total, batch_size):
        batch = tickers[i:i + batch_size]
        batch_num = i // batch_size + 1
        logger.info("Batch %s/%s (%s tickers)...", batch_num, total_batches, len(batch))
        for t in batch:
            try:
                info = yf.Ticker(t).fast_info
                exchange = getattr(info, "exchange", None)
                if exchange:
                    results[t] = normalize_exchange(exchange)
            except Exception as e:
                log_exception(logger, f"Exchange lookup failed for {t}", e)
        time.sleep(0.5)  # Brief pause between batches

    return results


def main():
    configure_logging()
    logger.info("Reading CSV...")
    df = pd.read_csv(CSV_PATH)
    logger.info("Total rows: %s", len(df))

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

    logger.info("Exchange determined from suffix: %s", sum(1 for e in exchanges if e and e is not None))
    logger.info("US tickers to look up: %s", len(us_tickers_to_lookup))
    logger.info("Looking up US exchanges via yfinance...")

    us_results = lookup_us_exchanges(us_tickers_to_lookup)
    logger.info("Successfully resolved: %s exchanges", len(us_results))

    # Fill in US results
    for ticker, idx in zip(us_tickers_to_lookup, us_ticker_indices):
        exchanges[idx] = us_results.get(ticker, "")

    # Insert or update Exchange column
    if "Exchange" in df.columns:
        df["Exchange"] = exchanges
    else:
        df.insert(1, "Exchange", exchanges)

    # Save
    df.to_csv(CSV_PATH, index=False)
    logger.info("Saved updated CSV with Exchange column: %s", CSV_PATH)
    logger.info("Done!")


if __name__ == "__main__":
    main()
