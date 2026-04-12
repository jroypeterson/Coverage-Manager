"""Provider chain — thin coordinator for fundamentals fallback and merging.

Responsibilities:
  - Per-ticker fallback coordination (primary → secondary → AV)
  - Field-level merging of partial results
  - Fallback rate logging by exchange

Does NOT own parsing (that stays in each provider) or orchestration
(that stays in generate.py).
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import PROVIDER_PRIORITY, API_KEYS
from reporting.calcs import FUND_COLS, VAL_COLS
from logging_utils import get_logger, log_exception
from providers.fmp_provider import fetch_fundamentals as fmp_fetch
from providers.yfinance_provider import fetch_fundamentals as yf_fetch
from providers.alphavantage_provider import fetch_fundamentals as av_fetch

logger = get_logger("providers.chain")


def _is_success(result):
    """Multi-field success rule: Mkt Cap + currency + at least one valuation/quality field."""
    if result.get("Mkt Cap") is None:
        return False
    quality_fields = [
        "Enterprise Value", "Fwd P/E", "EV/EBITDA", "EV/S",
        "Gross Mgn", "Op Mgn", "ROE", "Rev Grw", "EPS Grw",
    ]
    return any(result.get(f) is not None for f in quality_fields)


def _merge_partial(base, overlay):
    """Merge overlay fields into base where base has None values.

    Does not overwrite existing non-None values.
    """
    for key, val in overlay.items():
        if val is not None and base.get(key) is None:
            base[key] = val
    return base


def fetch_fundamentals_with_fallback(
    ticker,
    finnhub_metrics=None,
    use_cache=True,
    priority=None,
    *,
    _fmp_api_key=None,
    _av_api_key=None,
):
    """Fetch fundamentals for a single ticker with fallback chain.

    Args:
        ticker: The ticker symbol (yfinance-normalized for yf, raw for FMP).
        finnhub_metrics: Optional Finnhub metrics dict for TTM overlay.
        use_cache: Whether to use cache.
        priority: Override PROVIDER_PRIORITY ("fmp_first" or "yf_first").
        _fmp_api_key: FMP API key (defaults to API_KEYS).
        _av_api_key: AV API key (defaults to API_KEYS).

    Returns:
        (result, is_ttm, currency, provider_used) where provider_used is
        "fmp", "yfinance", "alphavantage", or "none".
    """
    effective_priority = priority or PROVIDER_PRIORITY
    fmp_key = _fmp_api_key if _fmp_api_key is not None else API_KEYS.get("FMP_API_KEY", "")
    av_key = _av_api_key if _av_api_key is not None else API_KEYS.get("ALPHAVANTAGE_API_KEY", "")

    result = {col: None for col in FUND_COLS + VAL_COLS}
    is_ttm = {"Rev Grw": False, "EPS Grw": False}
    currency = ""
    provider_used = "none"

    if effective_priority == "fmp_first":
        providers = [
            ("fmp", lambda: fmp_fetch(ticker, fmp_key, use_cache=use_cache)),
            ("yfinance", lambda: yf_fetch(ticker, finnhub_metrics=None, use_cache=use_cache)),
        ]
    else:
        providers = [
            ("yfinance", lambda: yf_fetch(ticker, finnhub_metrics=None, use_cache=use_cache)),
            ("fmp", lambda: fmp_fetch(ticker, fmp_key, use_cache=use_cache)),
        ]

    for name, fetcher in providers:
        try:
            res, ttm, cur = fetcher()
        except Exception as e:
            log_exception(logger, f"{name} fundamentals failed for {ticker}", e)
            continue

        if provider_used == "none":
            # First provider to return anything with Mkt Cap
            if res.get("Mkt Cap") is not None:
                result = res
                is_ttm = ttm
                currency = cur
                provider_used = name
                if _is_success(res):
                    break
            # else: complete miss, try next
        else:
            # We already have partial from primary — merge secondary into it
            if res.get("Mkt Cap") is not None:
                _merge_partial(result, res)
                # Don't overwrite currency from primary
            break  # We've tried both, done

    # If still not successful after both, no further merge needed here
    # (AV fallback below handles the final attempt)

    # AlphaVantage as final fallback if still not successful
    if not _is_success(result) and av_key:
        try:
            av_symbol = ticker.split(".")[0] if "." in ticker else ticker
            av_data = av_fetch(av_symbol, av_key, use_cache=use_cache)
            if av_data and av_data.get("Mkt Cap") is not None:
                _merge_partial(result, av_data)
                if provider_used == "none":
                    provider_used = "alphavantage"
        except Exception as e:
            log_exception(logger, f"AlphaVantage fallback failed for {ticker}", e)

    # Finnhub TTM overlay always wins for growth + PEG
    if finnhub_metrics:
        fh_rev = finnhub_metrics.get("revenueGrowthTTMYoy")
        if fh_rev is not None:
            result["Rev Grw"] = fh_rev
            is_ttm["Rev Grw"] = True
        fh_eps = finnhub_metrics.get("epsGrowthTTMYoy")
        if fh_eps is not None:
            result["EPS Grw"] = fh_eps
            is_ttm["EPS Grw"] = True
        fh_peg = finnhub_metrics.get("pegTTM")
        if fh_peg is not None:
            result["PEG"] = fh_peg

    return result, is_ttm, currency, provider_used


def fetch_all_fundamentals(
    tickers,
    finnhub_data=None,
    max_workers=10,
    use_cache=True,
    priority=None,
):
    """Fetch fundamentals for all tickers with fallback chain (parallel).

    Returns (all_fundamentals, all_is_ttm, all_currencies) matching the
    contract expected by generate.py.

    Also logs fallback rates by exchange suffix for monitoring.
    """
    if finnhub_data is None:
        finnhub_data = {}

    all_fundamentals = {}
    all_is_ttm = {}
    all_currencies = {}
    provider_counts = defaultdict(int)
    exchange_fallbacks = defaultdict(lambda: defaultdict(int))

    def _fetch_one(t):
        fh = finnhub_data.get(t)
        return t, fetch_fundamentals_with_fallback(
            t, finnhub_metrics=fh, use_cache=use_cache, priority=priority
        )

    completed = 0
    total = len(tickers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                t, (fund_result, is_ttm, currency, provider_used) = future.result()
            except Exception as e:
                t = futures[future]
                log_exception(logger, f"Provider chain failed for {t}", e)
                fund_result = {col: None for col in FUND_COLS + VAL_COLS}
                is_ttm = {"Rev Grw": False, "EPS Grw": False}
                currency = ""
                provider_used = "none"

            all_fundamentals[t] = fund_result
            all_is_ttm[t] = is_ttm
            all_currencies[t] = currency
            provider_counts[provider_used] += 1

            # Track fallback by exchange suffix (for international monitoring)
            suffix = t.split(".")[-1] if "." in t else "US"
            exchange_fallbacks[suffix][provider_used] += 1

            completed += 1
            if completed % 100 == 0:
                logger.info("Fundamentals %s/%s...", completed, total)

    # Log summary
    logger.info(
        "Provider chain complete: %s",
        ", ".join(f"{k}={v}" for k, v in sorted(provider_counts.items())),
    )
    for suffix, counts in sorted(exchange_fallbacks.items()):
        if suffix != "US":
            logger.info(
                "  Exchange .%s: %s",
                suffix,
                ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
            )

    return all_fundamentals, all_is_ttm, all_currencies
