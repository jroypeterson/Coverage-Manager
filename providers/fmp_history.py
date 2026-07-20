"""FMP historical valuation — annual P/E and EV/Sales for 5-year and 10-year windows.

Distinct from `fmp_provider.py` (TTM/profile fundamentals) because:
- It has its own cache namespace + long TTL — annual fundamentals change slowly,
  re-fetching weekly would be wasted bandwidth
- Always uses FMP regardless of provider chain primary (yfinance lacks clean
  historical multiples; AV's free tier is too rate-limited for time-series)

Endpoints used:
- /stable/ratios?symbol=X&period=annual&limit=10  → 10 years of priceToEarningsRatio
- /stable/key-metrics?symbol=X&period=annual&limit=10  → 10 years of evToSales
- /stable/ratios-ttm?symbol=X  → live TTM P/E for the "current" comparison

**Depth note (probed live 2026-07-19):** the FMP *Starter* tier returns at least
15 annual rows from `ratios` and `key-metrics`. The older workspace note that
Starter is "5yr annual only" is wrong for these two endpoints. That means the
10-year window costs **zero extra API calls** — it is the same request with a
higher `limit`, and the 5-year window is just the first 5 elements of the same
series. Do not add a second round of calls for the 10Y columns.

Status semantics — "no silent failures" (see CLAUDE.md):
  "ok"            → at least one usable historical/TTM value was returned
  "no_data"       → FMP answered but had nothing usable for this ticker. This is a
                    RECORDED FACT and is cached (shorter TTL) so it is visibly
                    distinct from "never tried".
  "error"         → the fetch raised / the API misbehaved. NEVER cached, so the
                    next run retries it.
  "not_attempted" → no cache entry and the caller ran in cache_only mode. No API
                    call was made and nothing is known about this ticker.

In every non-"ok" case the numeric fields are None — never 0. A 0 in a P/E-min
column would silently corrupt every downstream valuation screen.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from cache import cache_get, cache_set
from logging_utils import get_logger, log_exception
from providers.fmp_provider import _fmp_request, _safe_float

logger = get_logger("providers.fmp_history")

HISTORY_CACHE_NAMESPACE = "key_metrics_history"
HISTORY_CACHE_TTL_HOURS = 720.0  # 30 days — closed fiscal years don't change
HISTORY_NO_DATA_TTL_HOURS = 168.0  # 7 days — retry "no data" names sooner than good ones

# Length of the annual series we request and store. The 5Y stats are computed
# from the first 5 elements of the same series, so widening this does NOT cost
# extra API calls.
HISTORY_YEARS = 10

# Bump when the cached payload shape changes. Entries written by an older schema
# are ignored on read (treated as a cache miss) rather than silently mis-parsed —
# v1 stored 5-element series and had no `status` field.
HISTORY_SCHEMA_VERSION = 2

STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_ERROR = "error"
STATUS_NOT_ATTEMPTED = "not_attempted"


# FMP signals an error with a JSON object rather than an HTTP status, e.g.
# {"Error Message": "Invalid API KEY..."}. That matters most on ratios-ttm,
# which LEGITIMATELY returns a dict, so type alone can't separate an error
# payload from a real one — the key has to be checked.
_FMP_ERROR_KEYS = ("Error Message", "error", "errorMessage")


def _is_fmp_error_payload(data) -> bool:
    return isinstance(data, dict) and any(k in data for k in _FMP_ERROR_KEYS)


def _unwrap_list_response(data, ticker, what):
    """Split an FMP list-endpoint response into (rows, errored).

    ONLY an actual list means the call succeeded — an empty list is a genuine
    "this company has no rows", which is a fact worth caching. Everything else
    is a provider failure and must be retryable:

      None  -> `_fmp_request` swallowed a 402 or a non-200 (gated endpoint,
               bad key, outage). Foreign lines like ROG.SW / 4543.T land here.
      dict  -> an FMP error payload, e.g. {"Error Message": "Invalid API KEY"}.

    Conflating those with no-data (codex 2026-07-20) meant an outage or an
    expired key got cached as authoritative "no history for this ticker" for 7
    days, and `history-backfill` would then skip right past it. Silent, and
    self-perpetuating across the whole universe.
    """
    if isinstance(data, list):
        return data, False
    if data is None:
        logger.warning(
            "FMP %s returned no payload for %s (402/non-200) — treating as a "
            "retryable error, not as 'no data'", what, ticker)
    else:
        logger.warning(
            "FMP %s returned %s for %s, expected a list — treating as a "
            "retryable error, not as 'no data'", what, type(data).__name__, ticker)
    return [], True


def _fetch_ratios_annual(ticker, api_key, limit=HISTORY_YEARS):
    """Fetch FMP /stable/ratios?period=annual&limit=N.

    Returns (rows, errored). `rows` is most-recent-first (possibly empty);
    `errored` distinguishes "the call blew up" from "the call worked and the
    company simply has no rows".
    """
    try:
        url = (
            f"https://financialmodelingprep.com/stable/ratios"
            f"?symbol={ticker}&period=annual&limit={limit}&apikey={api_key}"
        )
        return _unwrap_list_response(_fmp_request(url), ticker, "annual ratios")
    except Exception as e:
        log_exception(logger, f"FMP annual ratios lookup failed for {ticker}", e)
        return [], True


def _fetch_key_metrics_annual(ticker, api_key, limit=HISTORY_YEARS):
    """Fetch FMP /stable/key-metrics?period=annual&limit=N. Returns (rows, errored)."""
    try:
        url = (
            f"https://financialmodelingprep.com/stable/key-metrics"
            f"?symbol={ticker}&period=annual&limit={limit}&apikey={api_key}"
        )
        return _unwrap_list_response(_fmp_request(url), ticker, "annual key-metrics")
    except Exception as e:
        log_exception(logger, f"FMP annual key-metrics lookup failed for {ticker}", e)
        return [], True


def _fetch_ratios_ttm_live(ticker, api_key):
    """Fetch FMP /stable/ratios-ttm for the current TTM P/E. Returns (dict, errored)."""
    try:
        url = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={ticker}&apikey={api_key}"
        data = _fmp_request(url)
        # Same distinction as _unwrap_list_response: None is a 402/non-200 and
        # must stay retryable. An empty list IS a valid "no TTM row".
        if data is None:
            logger.warning(
                "FMP ratios-ttm returned no payload for %s (402/non-200) — "
                "treating as a retryable error, not as 'no data'", ticker)
            return {}, True
        if _is_fmp_error_payload(data):
            # ratios-ttm legitimately returns a dict, so an error payload is
            # only distinguishable by its keys (codex 2026-07-20). Without this
            # an invalid key or a gated response was cached as "no history".
            logger.warning(
                "FMP ratios-ttm returned an error payload for %s (%s) — "
                "treating as a retryable error, not as 'no data'",
                ticker, next((data[k] for k in _FMP_ERROR_KEYS if k in data), "?"))
            return {}, True
        if isinstance(data, list):
            return (data[0] if data else {}), False
        if isinstance(data, dict):
            return data, False
        return {}, True
    except Exception as e:
        log_exception(logger, f"FMP ratios-ttm lookup failed for {ticker}", e)
        return {}, True


def fetch_history(ticker, api_key, use_cache=True, cache_only=False):
    """Fetch up-to-10-year history of P/E and EV/Sales plus current TTM P/E.

    Args:
        ticker: symbol to fetch.
        api_key: FMP key. Falsy → returns a `not_attempted` payload (no call).
        use_cache: read from the on-disk cache before hitting the API.
        cache_only: never make an API call. A cache miss returns a
            `not_attempted` payload. This is what the weekly performance report
            uses for non-position names, so report runtime never depends on a
            cold full-universe fetch.

    Returns a dict:
        {
            "status": "ok" | "no_data" | "error" | "not_attempted",
            "pe_ttm": float | None,
            "pe_history":  [float | None] * HISTORY_YEARS,  # most-recent-first
            "evs_history": [float | None] * HISTORY_YEARS,
            "record_dates": [str] * HISTORY_YEARS,          # ISO yyyy-mm-dd
            "fetched_at": ISO-8601 str | None,
            "schema_version": int,
        }
    Lists are padded with None to HISTORY_YEARS. Numeric fields are None (never
    0) whenever the value is unknown.
    """
    if use_cache:
        cached = cache_get(HISTORY_CACHE_NAMESPACE, ticker, HISTORY_CACHE_TTL_HOURS)
        if _cache_entry_usable(cached):
            # A cached "no_data" verdict expires sooner than a cached "ok" one.
            if cached.get("status") == STATUS_NO_DATA:
                fresh = cache_get(HISTORY_CACHE_NAMESPACE, ticker, HISTORY_NO_DATA_TTL_HOURS)
                if _cache_entry_usable(fresh):
                    return fresh
            else:
                return cached

    if cache_only or not api_key:
        return _empty_history(STATUS_NOT_ATTEMPTED)

    ratios_annual, ratios_err = _fetch_ratios_annual(ticker, api_key)
    key_metrics_annual, km_err = _fetch_key_metrics_annual(ticker, api_key)
    ratios_ttm, ttm_err = _fetch_ratios_ttm_live(ticker, api_key)

    pe_ttm = _safe_float(ratios_ttm.get("priceToEarningsRatioTTM")) if ratios_ttm else None
    pe_history = _pad([_safe_float(r.get("priceToEarningsRatio")) for r in ratios_annual], HISTORY_YEARS)
    record_dates = _pad([str(r.get("date") or "") for r in ratios_annual], HISTORY_YEARS, fill="")
    # FMP annual key-metrics uses field name `evToSales` (no TTM suffix on the dated row)
    evs_history = _pad([_safe_float(r.get("evToSales")) for r in key_metrics_annual], HISTORY_YEARS)

    has_data = (
        pe_ttm is not None
        or any(v is not None for v in pe_history)
        or any(v is not None for v in evs_history)
    )

    # ANY failed source makes the record incomplete, so errors are checked
    # BEFORE has_data (codex 2026-07-20). Previously has_data won: a failed
    # annual-ratios call plus a successful TTM call cached status=ok with
    # pe_history all None, and history-backfill then skipped that ticker as
    # "already cached" for 30 days — a transient blip frozen into a permanently
    # blank valuation history. Retrying next run costs one call; caching a
    # partial answer as authoritative costs a month of wrong columns.
    if ratios_err or km_err or ttm_err:
        failed = ", ".join(n for n, e in (
            ("annual ratios", ratios_err),
            ("annual key-metrics", km_err),
            ("ratios-ttm", ttm_err)) if e)
        logger.warning(
            "FMP history for %s is incomplete (%s failed) — not caching, so the "
            "next run retries rather than freezing a partial record", ticker, failed)
        status = STATUS_ERROR
    elif has_data:
        status = STATUS_OK
    else:
        status = STATUS_NO_DATA

    result = {
        "status": status,
        "pe_ttm": pe_ttm,
        "pe_history": pe_history,
        "evs_history": evs_history,
        "record_dates": record_dates,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": HISTORY_SCHEMA_VERSION,
    }

    # Cache successes AND authoritative "no data" answers (so a name we've tried
    # is visibly distinct from one we haven't). Never cache errors — those must
    # be retried, and caching one would silently freeze a transient failure into
    # a permanent blank column.
    if status in (STATUS_OK, STATUS_NO_DATA):
        cache_set(HISTORY_CACHE_NAMESPACE, ticker, result)
    else:
        logger.warning("FMP history unavailable for %s (status=error) — will retry next run", ticker)

    return result


def _cache_entry_usable(cached):
    """True when a cache read returned a payload written by the current schema."""
    return isinstance(cached, dict) and cached.get("schema_version") == HISTORY_SCHEMA_VERSION


def fetch_history_parallel(tickers, api_key, max_workers=10, use_cache=True, cache_only=False,
                           progress_every=25):
    """Fetch FMP history for multiple tickers in parallel. Returns dict keyed by ticker."""
    out = {}
    if not tickers:
        return out

    def _fetch_one(t):
        return t, fetch_history(t, api_key, use_cache=use_cache, cache_only=cache_only)

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
                out[t] = _empty_history(STATUS_ERROR)
            completed += 1
            if progress_every and completed % progress_every == 0:
                logger.info("FMP history %s/%s...", completed, total)

    return out


def is_cached(ticker):
    """True when `ticker` already has a fresh, current-schema cache entry.

    This is the resume primitive: a backfill skips any ticker for which this is
    true, so a run that dies at name 600 does not restart from zero.
    """
    cached = cache_get(HISTORY_CACHE_NAMESPACE, ticker, HISTORY_CACHE_TTL_HOURS)
    if not _cache_entry_usable(cached):
        return False
    if cached.get("status") == STATUS_NO_DATA:
        return _cache_entry_usable(
            cache_get(HISTORY_CACHE_NAMESPACE, ticker, HISTORY_NO_DATA_TTL_HOURS)
        )
    return True


def _empty_history(status=STATUS_NOT_ATTEMPTED):
    return {
        "status": status,
        "pe_ttm": None,
        "pe_history": [None] * HISTORY_YEARS,
        "evs_history": [None] * HISTORY_YEARS,
        "record_dates": [""] * HISTORY_YEARS,
        "fetched_at": None,
        "schema_version": HISTORY_SCHEMA_VERSION,
    }


def _pad(values, length, fill=None):
    """Pad a list with `fill` to reach `length`, or truncate if longer."""
    if len(values) >= length:
        return values[:length]
    return list(values) + [fill] * (length - len(values))
