"""FX rate provider — fetches USD exchange rates via yfinance, cached daily."""

import yfinance as yf

from cache import cache_get, cache_set
from logging_utils import get_logger

logger = get_logger("providers.fx")

CACHE_TTL_HOURS = 12


def fetch_fx_rates(currencies):
    """Fetch exchange rates to USD for a set of currency codes.

    Returns dict mapping currency code -> rate (1 unit of currency = rate USD).
    USD maps to 1.0. Unknown/failed currencies are omitted.
    """
    rates = {"USD": 1.0}
    to_fetch = {c for c in currencies if c and c != "USD"}
    if not to_fetch:
        return rates

    # Check cache first
    uncached = []
    for c in to_fetch:
        cached = cache_get("fx", c, CACHE_TTL_HOURS)
        if cached is not None:
            rates[c] = cached
        else:
            uncached.append(c)

    if not uncached:
        logger.info("FX rates: all %d from cache", len(to_fetch))
        return rates

    # Fetch via yfinance (e.g. JPYUSD=X)
    symbols = [f"{c}USD=X" for c in uncached]
    logger.info("Fetching FX rates for %d currencies: %s", len(uncached), ", ".join(uncached))

    for c, sym in zip(uncached, symbols):
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="5d")
            if hist is not None and not hist.empty:
                rate = float(hist["Close"].iloc[-1])
                rates[c] = rate
                cache_set("fx", c, rate)
            else:
                logger.warning("No FX data for %s", c)
        except Exception as e:
            logger.warning("FX fetch failed for %s: %s", c, e)

    logger.info("FX rates resolved: %d/%d", len(rates) - 1, len(to_fetch))
    return rates
