"""FMP analyst estimates — forward annual EPS for the P/E vs growth scatter.

Separate from `fmp_provider.py` (TTM/profile) and `fmp_history.py` (trailing
5-year multiples) because:
- It runs only for the small Phase 1 universe (the positions/research set).
- It always uses FMP regardless of provider-chain primary (yfinance's forward
  EPS is single-year only; FMP gives a multi-year forward estimate curve).
- It has its own cache namespace + 30-day TTL — analyst annual estimates change
  slowly, so weekly re-fetches would be wasted bandwidth.

Endpoint: /stable/analyst-estimates?symbol=X&period=annual  → forward annual
estimates with `epsAvg` per fiscal year (verified available on the FMP Starter
tier 2026-06-13; the legacy /api/v3 path 403s).
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from cache import cache_get, cache_set
from logging_utils import get_logger, log_exception
from providers.fmp_provider import _fmp_request

logger = get_logger("providers.fmp_estimates")

ESTIMATES_CACHE_NAMESPACE = "analyst_estimates"
ESTIMATES_CACHE_TTL_HOURS = 720.0  # 30 days


def fetch_estimates(ticker, api_key, use_cache=True):
    """Fetch forward annual EPS estimates for a ticker.

    Returns a list of `{"date": iso, "epsAvg": float|None}` rows (FMP's raw
    order, newest-first), or [] on failure/no-key. Cached 30 days.
    """
    if not api_key:
        return []

    if use_cache:
        cached = cache_get(ESTIMATES_CACHE_NAMESPACE, ticker, ESTIMATES_CACHE_TTL_HOURS)
        if cached is not None:
            return cached

    try:
        url = (
            f"https://financialmodelingprep.com/stable/analyst-estimates"
            f"?symbol={ticker}&period=annual&apikey={api_key}"
        )
        data = _fmp_request(url)
    except Exception as e:
        log_exception(logger, f"FMP analyst-estimates lookup failed for {ticker}", e)
        return []

    if not data or not isinstance(data, list):
        return []

    rows = [{"date": r.get("date"), "epsAvg": r.get("epsAvg")} for r in data]
    if any(r["epsAvg"] is not None for r in rows):
        cache_set(ESTIMATES_CACHE_NAMESPACE, ticker, rows)
    return rows


def fetch_estimates_parallel(tickers, api_key, max_workers=10, use_cache=True):
    """Fetch FMP estimates for multiple tickers in parallel. Returns dict keyed by ticker."""
    out = {}
    if not tickers:
        return out

    def _fetch_one(t):
        return t, fetch_estimates(t, api_key, use_cache=use_cache)

    completed = 0
    total = len(tickers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                t, rows = future.result()
                out[t] = rows
            except Exception as e:
                t = futures[future]
                log_exception(logger, f"FMP estimates failed for {t}", e)
                out[t] = []
            completed += 1
            if completed % 25 == 0:
                logger.info("FMP estimates %s/%s...", completed, total)

    return out
