"""Financial Modeling Prep (FMP) API provider — fallback price source."""

import pandas as pd
import requests

from logging_utils import get_logger, log_exception, retry_on_failure

logger = get_logger("providers.fmp")


@retry_on_failure(max_retries=2, base_delay=1.0, logger_name="providers.fmp")
def _fmp_request(url):
    """Make an FMP API request with retry on transient failures."""
    resp = requests.get(url, timeout=10)
    if resp.status_code == 429:
        raise Exception(f"FMP rate limited (429)")
    if resp.status_code != 200:
        return None
    return resp.json()


def fetch_historical_prices(ticker, api_key):
    """Get historical prices from FMP API as fallback (US tickers only).

    Returns a pandas Series indexed by date, or None on failure.
    """
    try:
        url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={ticker}&apikey={api_key}"
        data = _fmp_request(url)
        if not data or not isinstance(data, list):
            return None
        df = pd.DataFrame(data)
        if "date" not in df.columns or "close" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        series = df["close"].dropna()
        return series if len(series) > 0 else None
    except Exception as e:
        log_exception(logger, f"FMP historical lookup failed for {ticker}", e)
        return None
