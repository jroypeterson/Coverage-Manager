"""yfinance provider — price downloads and fundamental data with caching."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd
import yfinance as yf

from cache import cache_get, cache_set
from reporting.calcs import FUND_COLS, VAL_COLS
from logging_utils import get_logger, log_exception, retry_on_failure

logger = get_logger("providers.yfinance")

# Cache TTLs
PRICE_CACHE_TTL_HOURS = 18.0  # Same-day reruns only (expires overnight)
FUND_CACHE_TTL_HOURS = 24.0   # Fundamentals refresh daily


def batch_download_prices(tickers, start="2015-01-01", batch_size=50, use_cache=True):
    """Download historical close prices from yfinance in batches.

    Checks same-day cache first. Only downloads uncached tickers.
    Returns dict of {ticker: pd.Series} for tickers with data.
    """
    all_results = {}
    today = date.today().isoformat()

    # Check cache for same-day price data
    uncached = []
    if use_cache:
        for t in tickers:
            cached = cache_get("prices", f"{t}_{today}", PRICE_CACHE_TTL_HOURS)
            if cached is not None:
                try:
                    series = pd.Series(cached["values"], index=pd.to_datetime(cached["index"]), name=t)
                    all_results[t] = series
                except Exception:
                    uncached.append(t)
            else:
                uncached.append(t)
        if all_results:
            logger.info("Loaded price data for %d/%d tickers from cache", len(all_results), len(tickers))
    else:
        uncached = list(tickers)

    if not uncached:
        logger.info("All %d tickers served from price cache", len(tickers))
        return all_results

    total_batches = (len(uncached) + batch_size - 1) // batch_size

    newly_fetched = {}
    for i in range(0, len(uncached), batch_size):
        batch = uncached[i:i + batch_size]
        batch_num = i // batch_size + 1
        logger.info("Batch %s/%s (%s tickers)...", batch_num, total_batches, len(batch))
        try:
            data = yf.download(
                batch,
                start=start,
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if data.empty:
                continue
            close = data["Close"]
            if isinstance(close, pd.Series):
                t = batch[0]
                series = close.dropna()
                all_results[t] = series
                newly_fetched[t] = series
            else:
                for t in batch:
                    if t in close.columns:
                        series = close[t].dropna()
                        if len(series) > 0:
                            all_results[t] = series
                            newly_fetched[t] = series
        except Exception as e:
            logger.warning("Error in batch %s: %s", batch_num, e)
            continue

    # Cache newly fetched price data for same-day reruns
    if use_cache and newly_fetched:
        for t, series in newly_fetched.items():
            try:
                cache_set("prices", f"{t}_{today}", {
                    "index": [d.isoformat() for d in series.index],
                    "values": series.tolist(),
                })
            except Exception:
                pass  # Don't let cache errors break the pipeline

    logger.info("Price data: %d from cache, %d newly fetched, %d total",
                len(all_results) - len(newly_fetched), len(newly_fetched), len(all_results))
    return all_results


@retry_on_failure(max_retries=2, base_delay=1.0, logger_name="providers.yfinance")
def _fetch_ticker_info(yf_ticker):
    """Fetch yfinance Ticker.info with retry on transient failures."""
    return yf.Ticker(yf_ticker).info


def fetch_fundamentals(yf_ticker, finnhub_metrics=None, use_cache=True):
    """Fetch fundamental data from yfinance, enriched with Finnhub for US tickers.

    Returns (result, is_ttm, currency) where is_ttm tracks whether Finnhub TTM YoY
    data was used for Rev Grw and EPS Grw.
    """
    result = {col: None for col in FUND_COLS + VAL_COLS}
    is_ttm = {"Rev Grw": False, "EPS Grw": False}
    currency = ""

    # Check cache for yfinance fundamentals (before Finnhub enrichment)
    cache_key = f"yf_{yf_ticker}"
    if use_cache:
        cached = cache_get("fundamentals", cache_key, FUND_CACHE_TTL_HOURS)
        if cached is not None:
            result = cached.get("result", result)
            currency = cached.get("currency", currency)
            # Still apply Finnhub enrichment on top of cached yfinance data
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
            return result, is_ttm, currency

    try:
        info = _fetch_ticker_info(yf_ticker)
        if not info:
            return result, is_ttm, currency

        result["Mkt Cap"] = info.get("marketCap")
        result["Enterprise Value"] = info.get("enterpriseValue")
        result["Price"] = info.get("currentPrice") or info.get("regularMarketPrice")

        ev = info.get("enterpriseValue")
        mc = info.get("marketCap")
        if ev is not None and mc is not None:
            result["Net Debt"] = ev - mc
        else:
            result["Net Debt"] = None

        result["Fwd P/E"] = info.get("forwardPE")
        result["EV/EBITDA"] = info.get("enterpriseToEbitda")
        result["EV/S"] = info.get("enterpriseToRevenue")

        # Mkt Cap / EV / Net Debt are price-derived, so they are in the QUOTE
        # currency (`currency`), not the reporting currency (`financialCurrency`).
        # For foreign lines / ADRs where the two differ (e.g. NVO quotes USD but
        # reports DKK) the reporting currency would mis-convert to USD. Quote
        # currency first. NOTE: currency is cached alongside fundamentals, so a
        # re-cache (`--refresh`, or 24h TTL expiry) is needed for existing rows.
        currency = info.get("currency") or info.get("financialCurrency") or ""

        gm = info.get("grossMargins")
        result["Gross Mgn"] = gm * 100 if gm is not None else None
        om = info.get("operatingMargins")
        result["Op Mgn"] = om * 100 if om is not None else None
        roe = info.get("returnOnEquity")
        result["ROE"] = roe * 100 if roe is not None else None

        rev_grw = info.get("revenueGrowth")
        result["Rev Grw"] = rev_grw * 100 if rev_grw is not None else None
        eps_grw = info.get("earningsGrowth")
        result["EPS Grw"] = eps_grw * 100 if eps_grw is not None else None

        result["PEG"] = info.get("pegRatio")

        # Cache yfinance data before Finnhub enrichment
        if use_cache and result.get("Mkt Cap") is not None:
            cache_set("fundamentals", cache_key, {"result": result.copy(), "currency": currency})
    except Exception as e:
        log_exception(logger, f"Fundamental lookup failed for {yf_ticker}", e)

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

    return result, is_ttm, currency


def fetch_fundamentals_parallel(yf_tickers, finnhub_data=None, max_workers=10, use_cache=True):
    """Fetch fundamentals for a list of tickers in parallel.

    Returns (all_fundamentals, all_is_ttm, all_currencies) dicts keyed by yf_ticker.
    """
    if finnhub_data is None:
        finnhub_data = {}

    all_fundamentals = {}
    all_is_ttm = {}
    all_currencies = {}
    total = len(yf_tickers)

    def _fetch_one(yf_t):
        fh = finnhub_data.get(yf_t)
        return yf_t, fetch_fundamentals(yf_t, finnhub_metrics=fh, use_cache=use_cache)

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in yf_tickers}
        for future in as_completed(futures):
            yf_t, (fund_result, is_ttm, currency) = future.result()
            all_fundamentals[yf_t] = fund_result
            all_is_ttm[yf_t] = is_ttm
            all_currencies[yf_t] = currency
            completed += 1
            if completed % 100 == 0:
                logger.info("Fundamentals %s/%s...", completed, total)

    return all_fundamentals, all_is_ttm, all_currencies
