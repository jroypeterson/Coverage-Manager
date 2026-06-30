"""AlphaVantage API provider — fundamentals fallback for tickers missing yfinance data."""

import requests

from cache import cache_get, cache_set
from logging_utils import get_logger, log_exception, retry_on_failure
from providers.api_ledger import get_ledger

logger = get_logger("providers.alphavantage")

CACHE_TTL_HOURS = 24.0


@retry_on_failure(max_retries=2, base_delay=12.0, logger_name="providers.alphavantage")
def _av_request(url, ticker="?"):
    """Make an AlphaVantage API request with retry.

    AlphaVantage free tier: 25 requests/day, so retries use longer backoff.

    Reserves against the shared cross-project ledger PER ACTUAL request (inside the
    retried function) so the count matches real HTTP spend: `@retry_on_failure`
    can fire this up to 3x, and recording only once would silently overspend the
    25-call window shared with the `transcripts` project (Codex review 2026-06-29).
    A blocked reserve raises (caught upstream → AV skipped); fetch_fundamentals
    pre-checks read-only so an exhausted window short-circuits without spinning the
    retry/backoff loop.
    """
    led = get_ledger()
    if led is not None:
        res = led.reserve("alphavantage", "personal_free",
                          project="coverage-manager", operation=f"OVERVIEW {ticker}")
        if not res.allowed and not res.degraded:
            raise Exception(f"AlphaVantage shared quota exhausted: {res.detail}")
    resp = requests.get(url, timeout=15)
    data = resp.json()
    if "Note" in data or "Information" in data:
        raise Exception("AlphaVantage rate limited")
    return data


def fetch_fundamentals(ticker, api_key, use_cache=True):
    """Fetch company fundamentals from AlphaVantage OVERVIEW endpoint.

    Returns dict matching FUND_COLS/VAL_COLS keys, or empty dict on failure.
    """
    cache_key = f"av_{ticker}"
    if use_cache:
        cached = cache_get("fundamentals", cache_key, CACHE_TTL_HOURS)
        if cached is not None:
            return cached

    # Pre-check (read-only) the shared cross-project rolling-24h AV ledger BEFORE
    # entering the retry loop. The same free key is used by the `transcripts`
    # project's daily 7am backfill; without coordination the two silently starve
    # each other's 25/day (the 2026-06-29 zero-transcript incident). AV is only a
    # last-resort fundamentals fallback here, so when the shared budget is gone we
    # skip cleanly (return {}) and let the provider chain proceed without it —
    # short-circuiting here also avoids spinning _av_request's 2x12s backoff on an
    # exhausted window. The actual per-call recording happens inside _av_request.
    # The ledger never hard-fails: an unavailable ledger degrades → normal AV call.
    led = get_ledger()
    if led is not None:
        pre = led.check("alphavantage", "personal_free")
        if not pre.allowed and not pre.degraded:
            logger.warning(
                "AlphaVantage shared quota exhausted (rolling 24h, shared with "
                "transcripts) — skipping AV fallback for %s. %s", ticker, pre.detail)
            return {}

    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=OVERVIEW&symbol={ticker}&apikey={api_key}"
        )
        data = _av_request(url, ticker)
        if not data or "Symbol" not in data:
            return {}

        result = {}

        mc = data.get("MarketCapitalization")
        result["Mkt Cap"] = float(mc) if mc and mc != "None" else None

        ev = data.get("EnterpriseValue") or data.get("EVToEBITDA")
        # AV doesn't provide EV directly in all cases
        result["Enterprise Value"] = None
        result["Net Debt"] = None

        fpe = data.get("ForwardPE")
        result["Fwd P/E"] = float(fpe) if fpe and fpe != "None" and fpe != "0" else None

        ebitda = data.get("EBITDA")
        ev_raw = data.get("EVToEBITDA")
        if ev_raw and ev_raw != "None" and ev_raw != "-" and ebitda and ebitda != "None":
            result["EV/EBITDA"] = float(ev_raw)
        else:
            result["EV/EBITDA"] = None

        evr = data.get("EVToRevenue")
        result["EV/S"] = float(evr) if evr and evr != "None" and evr != "-" else None

        peg = data.get("PEGRatio")
        result["PEG"] = float(peg) if peg and peg != "None" and peg != "0" else None

        gm = data.get("GrossProfitTTM")
        rev = data.get("RevenueTTM")
        if gm and rev and gm != "None" and rev != "None":
            try:
                result["Gross Mgn"] = (float(gm) / float(rev)) * 100
            except (ValueError, ZeroDivisionError):
                result["Gross Mgn"] = None
        else:
            result["Gross Mgn"] = None

        om = data.get("OperatingMarginTTM")
        result["Op Mgn"] = float(om) * 100 if om and om != "None" and om != "0" else None

        roe = data.get("ReturnOnEquityTTM")
        result["ROE"] = float(roe) * 100 if roe and roe != "None" and roe != "0" else None

        rev_grw = data.get("QuarterlyRevenueGrowthYOY")
        result["Rev Grw"] = float(rev_grw) * 100 if rev_grw and rev_grw != "None" else None

        eps_grw = data.get("QuarterlyEarningsGrowthYOY")
        result["EPS Grw"] = float(eps_grw) * 100 if eps_grw and eps_grw != "None" else None

        result["Price"] = None  # AV OVERVIEW doesn't provide current price
        result["% 52Wk Hi"] = None

        if use_cache and result.get("Mkt Cap") is not None:
            cache_set("fundamentals", cache_key, result)

        return result

    except Exception as e:
        log_exception(logger, f"AlphaVantage lookup failed for {ticker}", e)
        return {}
