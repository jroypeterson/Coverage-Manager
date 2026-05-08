"""FMP historical valuation — annual P/E and EV/Sales for 5-year averages.

Distinct from `fmp_provider.py` (TTM/profile fundamentals) because:
- This runs only for a small Phase 1 universe (Portfolio ∪ Researching)
- Always uses FMP regardless of provider chain primary (yfinance lacks clean
  historical multiples; AV's free tier is too rate-limited for time-series)
- Has its own cache namespace + 30-day TTL — annual fundamentals change slowly,
  re-fetching weekly would be wasted bandwidth

Endpoints used:
- /stable/ratios?symbol=X&period=annual&limit=5  → 5 years of peRatio
- /stable/key-metrics?symbol=X&period=annual&limit=5  → 5 years of evToSales
- /stable/ratios-ttm?symbol=X  → live TTM peRatio for "current" comparison

The TTM call is duplicative of `_fetch_ratios` in `fmp_provider.py` for tickers
where FMP is the primary, but isolating it here keeps Phase 1 self-contained
without coupling to the provider-chain flow.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from cache import cache_get, cache_set
from logging_utils import get_logger, log_exception
from providers.fmp_provider import _fmp_request, _safe_float

logger = get_logger("providers.fmp_history")

HISTORY_CACHE_NAMESPACE = "key_metrics_history"
HISTORY_CACHE_TTL_HOURS = 720.0  # 30 days


def _fetch_ratios_annual(ticker, api_key, limit=5):
    """Fetch FMP /stable/ratios?period=annual&limit=N. Returns list (most-recent-first) or []."""
    try:
        url = (
            f"https://financialmodelingprep.com/stable/ratios"
            f"?symbol={ticker}&period=annual&limit={limit}&apikey={api_key}"
        )
        data = _fmp_request(url)
        if not data or not isinstance(data, list):
            return []
        return data
    except Exception as e:
        log_exception(logger, f"FMP annual ratios lookup failed for {ticker}", e)
        return []


def _fetch_key_metrics_annual(ticker, api_key, limit=5):
    """Fetch FMP /stable/key-metrics?period=annual&limit=N. Returns list (most-recent-first) or []."""
    try:
        url = (
            f"https://financialmodelingprep.com/stable/key-metrics"
            f"?symbol={ticker}&period=annual&limit={limit}&apikey={api_key}"
        )
        data = _fmp_request(url)
        if not data or not isinstance(data, list):
            return []
        return data
    except Exception as e:
        log_exception(logger, f"FMP annual key-metrics lookup failed for {ticker}", e)
        return []


def _fetch_ratios_ttm_live(ticker, api_key):
    """Fetch FMP /stable/ratios-ttm for the current TTM P/E. Returns dict or {}."""
    try:
        url = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={ticker}&apikey={api_key}"
        data = _fmp_request(url)
        if not data:
            return {}
        return data[0] if isinstance(data, list) else data
    except Exception as e:
        log_exception(logger, f"FMP ratios-ttm lookup failed for {ticker}", e)
        return {}


def fetch_history(ticker, api_key, use_cache=True):
    """Fetch 5-year history of P/E and EV/Sales plus current TTM P/E for a ticker.

    Returns a dict:
        {
            "pe_ttm": float | None,          # live TTM P/E from ratios-ttm
            "pe_history": [float | None] * 5, # most-recent-first annual peRatio
            "evs_history": [float | None] * 5,# most-recent-first annual evToSales
            "record_dates": [str] * 5,       # ISO yyyy-mm-dd, most-recent-first
        }
    Lists are padded with None to length 5 if fewer years are available.
    """
    if not api_key:
        return _empty_history()

    if use_cache:
        cached = cache_get(HISTORY_CACHE_NAMESPACE, ticker, HISTORY_CACHE_TTL_HOURS)
        if cached is not None:
            return cached

    ratios_annual = _fetch_ratios_annual(ticker, api_key, limit=5)
    key_metrics_annual = _fetch_key_metrics_annual(ticker, api_key, limit=5)
    ratios_ttm = _fetch_ratios_ttm_live(ticker, api_key)

    pe_ttm = _safe_float(ratios_ttm.get("priceToEarningsRatioTTM")) if ratios_ttm else None

    pe_history = _pad([_safe_float(r.get("priceToEarningsRatio")) for r in ratios_annual], 5)
    record_dates = _pad([str(r.get("date") or "") for r in ratios_annual], 5, fill="")

    # FMP annual key-metrics uses field name `evToSales` (no TTM suffix on the dated row)
    evs_history = _pad([_safe_float(r.get("evToSales")) for r in key_metrics_annual], 5)

    result = {
        "pe_ttm": pe_ttm,
        "pe_history": pe_history,
        "evs_history": evs_history,
        "record_dates": record_dates,
    }

    # Always cache when we got at least one usable data point — protects against
    # transient empties caching as failures.
    if pe_ttm is not None or any(v is not None for v in pe_history) or any(v is not None for v in evs_history):
        cache_set(HISTORY_CACHE_NAMESPACE, ticker, result)

    return result


def fetch_history_parallel(tickers, api_key, max_workers=10, use_cache=True):
    """Fetch FMP history for multiple tickers in parallel. Returns dict keyed by ticker."""
    out = {}
    if not tickers:
        return out

    def _fetch_one(t):
        return t, fetch_history(t, api_key, use_cache=use_cache)

    completed = 0
    total = len(tickers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                t, h = future.result()
                out[t] = h
            except Exception as e:
                t = futures[future]
                log_exception(logger, f"FMP history failed for {t}", e)
                out[t] = _empty_history()
            completed += 1
            if completed % 25 == 0:
                logger.info("FMP history %s/%s...", completed, total)

    return out


def _empty_history():
    return {
        "pe_ttm": None,
        "pe_history": [None] * 5,
        "evs_history": [None] * 5,
        "record_dates": [""] * 5,
    }


def _pad(values, length, fill=None):
    """Pad a list with `fill` to reach `length`, or truncate if longer."""
    if len(values) >= length:
        return values[:length]
    return list(values) + [fill] * (length - len(values))
