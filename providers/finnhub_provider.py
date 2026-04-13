"""Finnhub API provider — fundamental metrics with rate limiting and caching."""

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from cache import cache_get, cache_set
from logging_utils import get_logger, log_exception

logger = get_logger("providers.finnhub")

# Cache TTL: 24 hours for fundamental metrics
CACHE_TTL_HOURS = 24.0


def fetch_metrics(ticker, api_key, max_retries=3, use_cache=True):
    """Fetch fundamental metrics from Finnhub for a single ticker.

    Checks cache first (24h TTL). Retries on 429 with exponential backoff.
    Returns dict of metrics, or empty dict on failure.
    """
    if use_cache:
        cached = cache_get("fundamentals", f"finnhub_{ticker}", CACHE_TTL_HOURS)
        if cached is not None:
            return cached

    for attempt in range(max_retries + 1):
        try:
            url = f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={api_key}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 429:
                if attempt < max_retries:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning("Finnhub rate limited for %s, retry %d in %.1fs", ticker, attempt + 1, backoff)
                    time.sleep(backoff)
                    continue
                else:
                    logger.warning("Finnhub rate limited for %s after %d retries, skipping", ticker, max_retries)
                    return {}
            if resp.status_code != 200:
                return {}
            data = resp.json()
            metrics = data.get("metric", {})
            if metrics and use_cache:
                cache_set("fundamentals", f"finnhub_{ticker}", metrics)
            return metrics
        except Exception as e:
            log_exception(logger, f"Finnhub metrics lookup failed for {ticker}", e)
            return {}
    return {}


def fetch_parallel(tickers, api_key, max_workers=10, batch_size=55, batch_pause=62, use_cache=True):
    """Fetch Finnhub metrics in parallel, respecting the 60 req/min free tier limit.

    Checks cache first for each ticker. Only fetches uncached tickers from API.
    Processes in batches of 55 with a 62-second pause between batches.
    Returns dict of {ticker: metrics}.
    """
    results = {}
    total = len(tickers)

    # Check cache first — only fetch uncached tickers from API
    uncached = []
    if use_cache:
        for t in tickers:
            cached = cache_get("fundamentals", f"finnhub_{t}", CACHE_TTL_HOURS)
            if cached is not None:
                results[t] = cached
            else:
                uncached.append(t)
        if results:
            logger.info("Finnhub: %d/%d tickers served from cache", len(results), total)
    else:
        uncached = list(tickers)

    if not uncached:
        logger.info("Finnhub: all %d tickers served from cache", total)
        return results

    num_batches = (len(uncached) + batch_size - 1) // batch_size

    for batch_idx, batch_start in enumerate(range(0, len(uncached), batch_size)):
        batch = uncached[batch_start:batch_start + batch_size]
        if batch_start > 0:
            logger.info("Finnhub batch %d/%d (%d/%d uncached tickers, pausing %ds for rate limit)...",
                        batch_idx + 1, num_batches, batch_start, len(uncached), batch_pause)
            time.sleep(batch_pause)
        else:
            logger.info("Finnhub batch 1/%d (%d uncached tickers)...", num_batches, len(batch))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_metrics, t, api_key, use_cache=use_cache): t for t in batch}
            for future in as_completed(futures):
                t = futures[future]
                try:
                    metrics = future.result()
                    if metrics:
                        results[t] = metrics
                except Exception as e:
                    logger.warning("Finnhub fetch failed for %s: %s", t, e)

    logger.info("Finnhub: fetched metrics for %d/%d tickers", len(results), total)
    return results
