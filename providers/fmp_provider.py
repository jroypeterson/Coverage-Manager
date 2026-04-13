"""Financial Modeling Prep (FMP) API provider — prices and fundamentals."""

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from cache import cache_get, cache_set
from reporting.calcs import FUND_COLS, VAL_COLS
from logging_utils import get_logger, log_exception, retry_on_failure

logger = get_logger("providers.fmp")

# Cache TTL
FUND_CACHE_TTL_HOURS = 24.0

# ── Rate limiter ────────────────────────────────────────────────────────────

class _RateLimiter:
    """Token-bucket rate limiter for FMP API (300 calls/min)."""

    def __init__(self, calls_per_minute=300):
        self._interval = 60.0 / calls_per_minute
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_call = time.monotonic()


_rate_limiter = _RateLimiter(calls_per_minute=300)

# ── Low-level request ───────────────────────────────────────────────────────

@retry_on_failure(max_retries=2, base_delay=1.0, logger_name="providers.fmp")
def _fmp_request(url):
    """Make an FMP API request with retry on transient failures."""
    _rate_limiter.wait()
    resp = requests.get(url, timeout=10)
    if resp.status_code == 429:
        raise Exception("FMP rate limited (429)")
    if resp.status_code == 402:
        logger.warning("FMP 402 (payment required) — endpoint may be gated")
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


# ── Price history (existing) ────────────────────────────────────────────────

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


# ── Profile ─────────────────────────────────────────────────────────────────

def fetch_profile(ticker, api_key):
    """Fetch FMP /stable/profile for a ticker. Returns dict or {}.

    Shared by enrich.py (universe enrichment) and fundamentals fetching.
    """
    if not api_key:
        return {}
    try:
        url = f"https://financialmodelingprep.com/stable/profile?symbol={ticker}&apikey={api_key}"
        data = _fmp_request(url)
        if not data:
            return {}
        return data[0] if isinstance(data, list) else data
    except Exception as e:
        log_exception(logger, f"FMP profile lookup failed for {ticker}", e)
        return {}


# ── Fundamentals (progressive endpoint strategy) ────────────────────────────

def _fetch_ratios(ticker, api_key):
    """Fetch FMP /stable/ratios-ttm for a ticker. Returns dict or {}."""
    try:
        url = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={ticker}&apikey={api_key}"
        data = _fmp_request(url)
        if not data:
            return {}
        return data[0] if isinstance(data, list) else data
    except Exception as e:
        log_exception(logger, f"FMP ratios lookup failed for {ticker}", e)
        return {}


def _fetch_key_metrics(ticker, api_key):
    """Fetch FMP /stable/key-metrics-ttm for a ticker. Returns dict or {}."""
    try:
        url = f"https://financialmodelingprep.com/stable/key-metrics-ttm?symbol={ticker}&apikey={api_key}"
        data = _fmp_request(url)
        if not data:
            return {}
        return data[0] if isinstance(data, list) else data
    except Exception as e:
        log_exception(logger, f"FMP key-metrics lookup failed for {ticker}", e)
        return {}


def _fetch_financial_growth(ticker, api_key):
    """Fetch FMP /stable/financial-growth for a ticker. Returns dict or {}."""
    try:
        url = f"https://financialmodelingprep.com/stable/financial-growth?symbol={ticker}&period=annual&limit=1&apikey={api_key}"
        data = _fmp_request(url)
        if not data:
            return {}
        return data[0] if isinstance(data, list) else data
    except Exception as e:
        log_exception(logger, f"FMP financial-growth lookup failed for {ticker}", e)
        return {}


def _safe_float(val):
    """Convert a value to float, returning None if not possible."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # reject NaN
    except (ValueError, TypeError):
        return None


def _pct(val):
    """Convert decimal ratio (0.45) to percentage (45.0). None-safe."""
    f = _safe_float(val)
    return f * 100 if f is not None else None


def fetch_fundamentals(ticker, api_key, use_cache=True):
    """Fetch fundamentals from FMP using progressive endpoint strategy.

    Progressive calls:
      1. profile (always) — Mkt Cap, currency
      2. ratios-ttm (always) — P/E, EV/EBITDA, PEG, margins
      3. key-metrics-ttm (only if EV/Net Debt/EV/S/ROE still missing)

    financial-growth is skipped (402 on Starter tier; Finnhub covers growth).

    Returns (result, is_ttm, currency) matching yfinance_provider contract.
    """
    result = {col: None for col in FUND_COLS + VAL_COLS}
    is_ttm = {"Rev Grw": False, "EPS Grw": False}
    currency = ""

    cache_key = f"fmp_{ticker}"
    if use_cache:
        cached = cache_get("fundamentals", cache_key, FUND_CACHE_TTL_HOURS)
        if cached is not None:
            return cached.get("result", result), cached.get("is_ttm", is_ttm), cached.get("currency", currency)
    # Note: even with use_cache=False (--refresh), we still WRITE to cache below
    # so that later steps (e.g. S&P 500) can benefit from warm cache.

    if not api_key:
        return result, is_ttm, currency

    # Call 1: profile (always)
    profile = fetch_profile(ticker, api_key)
    if not profile:
        return result, is_ttm, currency

    result["Mkt Cap"] = _safe_float(profile.get("marketCap"))
    currency = str(profile.get("currency") or "")
    result["Price"] = _safe_float(profile.get("price"))

    # Call 2: ratios-ttm (always)
    ratios = _fetch_ratios(ticker, api_key)
    if ratios:
        # FMP ratios-ttm uses priceToEarningsRatioTTM (trailing P/E — best available)
        result["Fwd P/E"] = _safe_float(ratios.get("priceToEarningsRatioTTM"))
        result["EV/EBITDA"] = _safe_float(ratios.get("enterpriseValueMultipleTTM"))
        result["PEG"] = _safe_float(ratios.get("priceToEarningsGrowthRatioTTM"))
        result["EV/S"] = _safe_float(ratios.get("priceToSalesRatioTTM"))
        result["Gross Mgn"] = _pct(ratios.get("grossProfitMarginTTM"))
        result["Op Mgn"] = _pct(ratios.get("operatingProfitMarginTTM"))
        # ROE not in ratios-ttm — comes from key-metrics-ttm below

    # Call 3: key-metrics-ttm (EV, Net Debt, EV/S, ROE live here)
    needs_key_metrics = (
        result["Enterprise Value"] is None
        or result["Net Debt"] is None
        or result["EV/S"] is None
        or result["ROE"] is None
    )
    if needs_key_metrics:
        km = _fetch_key_metrics(ticker, api_key)
        if km:
            if result["Enterprise Value"] is None:
                result["Enterprise Value"] = _safe_float(km.get("enterpriseValueTTM") or km.get("enterpriseValue"))
            if result["Net Debt"] is None:
                # netDebtToEBITDATTM exists but we need absolute Net Debt
                # Derive from EV - Mkt Cap if not directly available
                pass  # handled below via EV - Mkt Cap derivation
            if result["EV/S"] is None:
                evs = _safe_float(km.get("evToSalesTTM") or km.get("evToSales"))
                if evs is not None:
                    result["EV/S"] = evs
            if result["ROE"] is None:
                result["ROE"] = _pct(km.get("returnOnEquityTTM"))

    # Derive Net Debt from EV - Mkt Cap if still missing
    if result["Net Debt"] is None and result["Enterprise Value"] is not None and result["Mkt Cap"] is not None:
        result["Net Debt"] = result["Enterprise Value"] - result["Mkt Cap"]

    # financial-growth endpoint is skipped — it returns 402 on FMP Starter tier,
    # and Finnhub TTM overlay covers Rev Grw / EPS Grw anyway.

    # Always cache results (even on --refresh) so later pipeline steps benefit
    if result.get("Mkt Cap") is not None:
        cache_set("fundamentals", cache_key, {
            "result": result.copy(),
            "is_ttm": is_ttm.copy(),
            "currency": currency,
        })

    return result, is_ttm, currency


def fetch_fundamentals_parallel(tickers, api_key, max_workers=10, use_cache=True):
    """Fetch FMP fundamentals for multiple tickers in parallel.

    Returns (all_fundamentals, all_is_ttm, all_currencies) dicts keyed by ticker.
    """
    all_fundamentals = {}
    all_is_ttm = {}
    all_currencies = {}

    def _fetch_one(t):
        return t, fetch_fundamentals(t, api_key, use_cache=use_cache)

    completed = 0
    total = len(tickers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                t, (fund_result, is_ttm, currency) = future.result()
                all_fundamentals[t] = fund_result
                all_is_ttm[t] = is_ttm
                all_currencies[t] = currency
            except Exception as e:
                t = futures[future]
                log_exception(logger, f"FMP fundamentals failed for {t}", e)
                all_fundamentals[t] = {col: None for col in FUND_COLS + VAL_COLS}
                all_is_ttm[t] = {"Rev Grw": False, "EPS Grw": False}
                all_currencies[t] = ""
            completed += 1
            if completed % 100 == 0:
                logger.info("FMP fundamentals %s/%s...", completed, total)

    return all_fundamentals, all_is_ttm, all_currencies
