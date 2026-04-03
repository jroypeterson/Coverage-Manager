"""S&P 500 constituent list from Wikipedia with caching."""

import pandas as pd

from cache import cache_get, cache_set
from logging_utils import get_logger

logger = get_logger("providers.wikipedia")

# Cache TTL: 7 days for constituent lists
CACHE_TTL_HOURS = 7 * 24.0


def fetch_sp500_tickers(use_cache=True):
    """Fetch current S&P 500 constituent tickers from Wikipedia.

    Returns (tickers, sp500_info) where sp500_info maps ticker to
    {Company Name, GICS Sector, GICS Sub-Industry}.
    Checks cache first (7-day TTL). Falls back gracefully on failure.
    """
    if use_cache:
        cached = cache_get("constituents", "sp500", CACHE_TTL_HOURS)
        if cached is not None:
            tickers = cached.get("tickers", [])
            sp500_info = cached.get("info", {})
            logger.info("Loaded %s S&P 500 tickers from cache", len(tickers))
            return tickers, sp500_info

    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(
            url,
            header=0,
            storage_options={"User-Agent": "CoverageManager/1.0 (investment research tool)"},
        )
        df = tables[0]
        tickers = []
        for sym in df["Symbol"]:
            t = str(sym).strip().replace(".", "-")
            tickers.append(t)
        logger.info("Fetched %s S&P 500 tickers from Wikipedia", len(tickers))
        sp500_info = {}
        for _, r in df.iterrows():
            t = str(r["Symbol"]).strip().replace(".", "-")
            sp500_info[t] = {
                "Company Name": str(r.get("Security", "")).strip(),
                "GICS Sector": str(r.get("GICS Sector", "")).strip(),
                "GICS Sub-Industry": str(r.get("GICS Sub-Industry", "")).strip(),
            }
        if use_cache and tickers:
            cache_set("constituents", "sp500", {"tickers": tickers, "info": sp500_info})
        return tickers, sp500_info
    except Exception as e:
        logger.warning("Failed to fetch S&P 500 list from Wikipedia: %s", e)
        logger.warning("S&P 500 benchmark tab will be skipped this run")
        return [], {}
