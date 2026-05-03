"""Finnhub /company-news provider.

Fetches recent company news headlines for a single ticker via Finnhub's free
tier endpoint. Used by the movers report to provide a "why" context for
flagged tickers.

Rate limit: free tier is 60 calls/min — fine for the typical movers run
(<= MOVERS_MAX_FLAGGED tickers per week).
"""

from datetime import date, timedelta

import requests

from cache import cache_get, cache_set
from logging_utils import get_logger, log_exception

logger = get_logger("providers.finnhub_news")

# Cache TTL: 24 hours. News changes throughout the day but for the weekly
# movers run we only care that headlines are roughly today's.
CACHE_TTL_HOURS = 24.0


def fetch_company_news(ticker, api_key, days_back=7, max_items=10, use_cache=True):
    """Fetch recent company-news items for a single ticker.

    Args:
        ticker: Finnhub-compatible symbol (US-style — e.g. "AAPL", "PS").
        api_key: Finnhub API key.
        days_back: How many calendar days back to query (default 7).
        max_items: Cap on items returned (default 10) — Finnhub returns most-
            recent first, so this keeps us focused on what likely caused the
            move.
        use_cache: Set False to bypass the 24h cache (e.g. for an ad-hoc rerun).

    Returns a list of dicts with keys: ``date`` (ISO), ``headline``, ``source``,
    ``summary`` (may be empty), ``url``. Empty list on any failure — this is a
    non-critical enrichment so callers should tolerate misses silently.
    """
    cache_key = f"finnhub_news_{ticker}_{days_back}d"
    if use_cache:
        cached = cache_get("news", cache_key, CACHE_TTL_HOURS)
        if cached is not None:
            return cached[:max_items]

    today = date.today()
    from_date = (today - timedelta(days=days_back)).isoformat()
    to_date = today.isoformat()

    try:
        url = (
            "https://finnhub.io/api/v1/company-news"
            f"?symbol={ticker}&from={from_date}&to={to_date}&token={api_key}"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            logger.debug("Finnhub news for %s returned %d", ticker, resp.status_code)
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []

        # Normalize and trim. Finnhub returns datetime as Unix epoch seconds.
        items = []
        for entry in data:
            ts = entry.get("datetime")
            if ts:
                iso_date = date.fromtimestamp(ts).isoformat()
            else:
                iso_date = ""
            items.append({
                "date": iso_date,
                "headline": entry.get("headline", "") or "",
                "source": entry.get("source", "") or "",
                "summary": entry.get("summary", "") or "",
                "url": entry.get("url", "") or "",
            })

        # Sort newest first (Finnhub usually returns this order, but make it
        # explicit) and trim duplicates by headline.
        items.sort(key=lambda x: x["date"], reverse=True)
        seen_headlines = set()
        deduped = []
        for it in items:
            h = it["headline"].strip().lower()
            if h and h not in seen_headlines:
                seen_headlines.add(h)
                deduped.append(it)

        if use_cache:
            cache_set("news", cache_key, deduped)
        return deduped[:max_items]

    except Exception as e:
        log_exception(logger, f"Finnhub news lookup failed for {ticker}", e)
        return []
