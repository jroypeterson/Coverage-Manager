"""FMP TTM cash-return metrics — FCF yield, ROIC and CFO margin for the whole universe.

Distinct from `fmp_provider.py`, which is the TTM/profile fundamentals chain, because that
chain is `yf_first`: when yfinance answers with a market cap and the row looks complete, the
FMP leg is never called at all. These three columns must exist for EVERY row or the screens
downstream silently run over whoever yfinance happened to miss, so they get their own step —
the same shape as `fmp_history.py`, which is here for the same reason.

Endpoint: `/stable/key-metrics-ttm?symbol=X` — ONE call carries all three numbers.
JP, 2026-09-22, asked for a mid-teens FCF-yield screen; board `#430` asks for negative
FCF/CFO with ROIC above 15% and `#361` for ROIC quintiles. Fetching them separately would
have made three callers of one endpoint on three cadences — three chances to disagree about
one company on one day.

⛑ EVERY STORED FIELD IS A RATIO, AND THAT IS NOT A STYLE CHOICE. Measured 2026-09-22, FMP's
`marketCap` and `investedCapitalTTM` are in the LISTING'S OWN CURRENCY for cross-listings:
Takeda's is 150.3x Coverage Manager's USD figure (yen), Novo's 6.55x (kroner), GSK's 0.72x
(sterling). An absolute field would be a local-currency number in a report whose money
columns are all USD, and a "$10bn" gate over it is the pence trap this fleet has already
paid for twice. A ratio has the same currency top and bottom, so it is unit-free and safe.
`mcap_usd` in the perf report remains the only market cap anything may threshold on.

CFO margin is DERIVED, not fetched: `evToSalesTTM / evToOperatingCashFlowTTM` is
(EV/Sales)·(OCF/EV) = OCF/Sales. Validated against the quarterly cash-flow and income
statements on 8 names, including three with negative operating cash flow (MRNA -47.2%,
SRPT -4.6%, BEAM -230.3%): exact to the printed digits on 7, the 8th being the vendor's own
disagreement documented below. That saves a second endpoint for the `#430` leg entirely.

Status semantics — "no silent failures", the same four words `fmp_history` uses:
  "ok"            → at least one of the three numbers came back usable
  "no_data"       → FMP answered and had nothing for this ticker. A RECORDED FACT, cached on
                    a shorter TTL, visibly different from "never tried". 225 of 1,359
                    coverage rows are in this state and 80% of them are foreign lines.
  "gated"         → HTTP 402: the endpoint is not on this plan. Not an error, not retried.
  "error"         → the fetch raised or the API misbehaved. NEVER cached, so the next run
                    retries it.
  "not_attempted" → no cache entry and the caller ran cache-only. No call was made.

⛑ THE VENDOR DISAGREES WITH ITSELF ON SOME NAMES, SO A HEADLINE NUMBER IS CORROBORATED
BEFORE IT CAN DRIVE A SCREEN. Measured 2026-09-22 across 8 names: FMP's
`freeCashFlowYieldTTM` equals its own four quarterly cash-flow statements over its own market
cap to the printed digit on 7 of them — and on SANOFI it reads 18.45% against 13.18% from its
statements, a 1.40x gap. SNY clears a 15% bar on the vendor's figure and fails it on the
vendor's own filings. So every name that would PASS a mid-teens bar gets one extra call and a
`Cash Flow Status` of `ok` or `unreconciled`; names nowhere near the bar are `not_checked`,
which is cheap and honest rather than a blanket claim of verification.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from cache import cache_get, cache_set
from logging_utils import get_logger, log_exception
from providers.fmp_provider import _fmp_request, _safe_float, FMP_GATED_STATUS

logger = get_logger("providers.fmp_quality")

QUALITY_CACHE_NAMESPACE = "quality_metrics"
# 6 days: the perf report is weekly, so this refreshes on every run rather than serving a
# figure from the previous one. Market cap sits in the FCF-yield denominator and moves daily,
# so a long TTL would quietly widen the as-of gap the report's own date claims.
QUALITY_CACHE_TTL_HOURS = 144.0
QUALITY_NO_DATA_TTL_HOURS = 168.0
# ⛑ BUMPED TO 2 WHEN `stmt_as_of` LANDED. A v1 entry was corroborated but carries NO
# statement date, and the consumer's staleness gate passes anything it cannot date — so 130
# cached rows would have published as corroborated-and-undatable, which is exactly the
# `a-check-that-silently-matches-nothing` shape (Codex r13). An old-schema entry is a cache
# MISS and is refetched, the same rule `fmp_history` uses for its own v1 payloads.
QUALITY_SCHEMA_VERSION = 2

STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_ERROR = "error"
STATUS_GATED = "gated"
STATUS_NOT_ATTEMPTED = "not_attempted"

CHECK_OK = "ok"
CHECK_UNRECONCILED = "unreconciled"
CHECK_NO_STATEMENTS = "no_statements"
CHECK_NOT_CHECKED = "not_checked"

# A name at or above this on either leg is a screen candidate and gets corroborated.
# It is deliberately BELOW the screens' own 15% bar: a name at 14.6% that the vendor has
# overstated would otherwise reach the bar next week with no check ever having run.
CHECK_TRIGGER = 0.12
# How far the vendor's own statements may sit from its headline before the row is flagged.
# SNY's live gap is 40%; the seven names that agree sit under 1%.
CHECK_TOLERANCE = 0.20


def _empty(status):
    return {
        "schema": QUALITY_SCHEMA_VERSION,
        "status": status,
        "fcf_yield": None,
        "roic": None,
        "cfo_margin": None,
        "ic_nonpositive": False,
        "check": CHECK_NOT_CHECKED,
        "own_fcf_yield": None,
        # The newest quarterly statement behind the corroboration, so a consumer can refuse
        # a figure computed off an obsolete fiscal period. Only populated where we actually
        # read statements: `key-metrics-ttm` carries NO period date at all (probed live
        # 2026-09-22 -- 43 fields, not one of them a date), so for a row we never
        # corroborated there is nothing honest to publish here.
        "stmt_as_of": None,
    }


def _is_error_payload(data):
    return isinstance(data, dict) and any(
        k in data for k in ("Error Message", "error", "message"))


def _fetch_key_metrics_ttm(ticker, api_key):
    """(row, errored, gated) for /stable/key-metrics-ttm."""
    try:
        url = (f"https://financialmodelingprep.com/stable/key-metrics-ttm"
               f"?symbol={ticker}&apikey={api_key}")
        data, code = _fmp_request(url, want_status=True)
        if data is None and code == FMP_GATED_STATUS:
            return {}, False, True
        if data is None:
            logger.warning("FMP key-metrics-ttm gave no payload for %s (HTTP %s) — retryable "
                           "error, not 'no data'", ticker, code)
            return {}, True, False
        if _is_error_payload(data):
            return {}, True, False
        if isinstance(data, list):
            return (data[0] if data else {}), False, False
        if isinstance(data, dict):
            return data, False, False
        return {}, True, False
    except Exception as e:
        log_exception(logger, f"FMP key-metrics-ttm failed for {ticker}", e)
        return {}, True, False


def _fetch_quarterly_fcf(ticker, api_key, limit=4):
    """(sum of the last `limit` quarterly free cash flows, newest statement date, errored).

    The sum is in the STATEMENT's currency, which is the same currency as the `marketCap`
    in the key-metrics payload it is compared against — measured on SNY, both EUR. The
    comparison is therefore currency-safe without touching an FX table.
    """
    try:
        url = (f"https://financialmodelingprep.com/stable/cash-flow-statement"
               f"?symbol={ticker}&period=quarter&limit={limit}&apikey={api_key}")
        data, code = _fmp_request(url, want_status=True)
        if not isinstance(data, list) or not data:
            return None, None, (data is None and code != FMP_GATED_STATUS)
        vals = [_safe_float(r.get("freeCashFlow")) for r in data]
        if any(v is None for v in vals) or len(vals) < limit:
            # A partial year is not a TTM. Corroborating against one would manufacture a
            # disagreement out of the missing quarters.
            return None, None, False
        dates = sorted(str(r.get("date") or "")[:10] for r in data)
        return sum(vals), (dates[-1] or None), False
    except Exception as e:
        log_exception(logger, f"FMP quarterly cash-flow failed for {ticker}", e)
        return None, None, True


def _corroborate(ticker, api_key, payload, market_cap):
    """Set `check`/`own_fcf_yield` on a candidate row. Mutates and returns `payload`."""
    own_fcf, stmt_as_of, errored = _fetch_quarterly_fcf(ticker, api_key)
    payload["stmt_as_of"] = stmt_as_of
    if own_fcf is None or not market_cap:
        payload["check"] = CHECK_NO_STATEMENTS if not errored else CHECK_NOT_CHECKED
        return payload
    own_yield = own_fcf / market_cap
    payload["own_fcf_yield"] = own_yield
    vendor = payload.get("fcf_yield")
    if vendor is None or vendor == 0:
        payload["check"] = CHECK_NO_STATEMENTS
        return payload
    # A RATIO, not a difference in points: the same 2-point gap means something different at
    # 3% and at 30%, and this bar has to hold across a universe spanning both.
    payload["check"] = (CHECK_OK if abs(own_yield / vendor - 1.0) <= CHECK_TOLERANCE
                        else CHECK_UNRECONCILED)
    if payload["check"] == CHECK_UNRECONCILED:
        logger.warning("%s: FMP headline FCF yield %.1f%% vs %.1f%% from its own quarterly "
                       "statements — flagged unreconciled", ticker,
                       vendor * 100, own_yield * 100)
    return payload


def fetch_quality(ticker, api_key, use_cache=True, cache_only=False):
    """TTM FCF yield, ROIC and CFO margin for one ticker. Never raises."""
    key = ticker.upper()
    if use_cache:
        cached = cache_get(QUALITY_CACHE_NAMESPACE, key, QUALITY_CACHE_TTL_HOURS)
        if isinstance(cached, dict) and cached.get("schema") == QUALITY_SCHEMA_VERSION:
            return cached
    if cache_only or not api_key:
        return _empty(STATUS_NOT_ATTEMPTED)

    row, errored, gated = _fetch_key_metrics_ttm(ticker, api_key)
    if gated:
        out = _empty(STATUS_GATED)
        cache_set(QUALITY_CACHE_NAMESPACE, key, out)
        return out
    if errored:
        return _empty(STATUS_ERROR)          # never cached — retry next run
    if not row:
        out = _empty(STATUS_NO_DATA)
        cache_set(QUALITY_CACHE_NAMESPACE, key, out)
        return out

    out = _empty(STATUS_NO_DATA)
    fcf_yield = _safe_float(row.get("freeCashFlowYieldTTM"))
    # ⛑ An exact 0.0 is FMP's "no data", not a company whose free cash flow is precisely
    # zero. `sp500_valuation` learned this on `epsAvg == 0` for a profitable insurer; 12
    # names in the fleet's existing FCF cache sit at exactly 0.0.
    out["fcf_yield"] = None if fcf_yield == 0 else fcf_yield

    roic = _safe_float(row.get("returnOnInvestedCapitalTTM"))
    invested = _safe_float(row.get("investedCapitalTTM"))
    if invested is not None and invested <= 0:
        # ⛑ A return on NEGATIVE invested capital is not a high return — the sign of the
        # denominator flips a loss into a flattering percentage. The guard lives HERE, at the
        # one place the number is made, so no consumer can reach the unguarded value.
        out["ic_nonpositive"] = True
        out["roic"] = None
    else:
        out["roic"] = roic

    ev_ocf = _safe_float(row.get("evToOperatingCashFlowTTM"))
    ev_sales = _safe_float(row.get("evToSalesTTM"))
    if ev_ocf not in (None, 0) and ev_sales is not None:
        out["cfo_margin"] = ev_sales / ev_ocf

    if any(out[f] is not None for f in ("fcf_yield", "roic", "cfo_margin")):
        out["status"] = STATUS_OK

    market_cap = _safe_float(row.get("marketCap"))
    candidate = ((out["fcf_yield"] is not None and abs(out["fcf_yield"]) >= CHECK_TRIGGER)
                 or (out["roic"] is not None and out["roic"] >= CHECK_TRIGGER))
    if candidate:
        out = _corroborate(ticker, api_key, out, market_cap)

    cache_set(QUALITY_CACHE_NAMESPACE, key, out)
    return out


def fetch_quality_parallel(tickers, api_key, max_workers=10, use_cache=True,
                           cache_only=False, progress_every=50):
    """{ticker: payload} for many tickers. A failure is recorded, never raised."""
    out = {}
    if not tickers:
        return out

    def _one(t):
        return t, fetch_quality(t, api_key, use_cache=use_cache, cache_only=cache_only)

    completed, total = 0, len(tickers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_one, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                t, payload = future.result()
                out[t] = payload
            except Exception as e:
                t = futures[future]
                log_exception(logger, f"FMP quality failed for {t}", e)
                out[t] = _empty(STATUS_ERROR)
            completed += 1
            if progress_every and completed % progress_every == 0:
                logger.info("FMP quality %s/%s...", completed, total)
    return out


def quality_columns_from_payload(payload):
    """Payload → the report's column dict. Percentages, because every neighbouring
    percentage column in this report (`ROE (TTM)`, `Gross Mgn (TTM)`) is one."""
    p = payload or _empty(STATUS_NOT_ATTEMPTED)

    def _pct(v):
        return None if v is None else v * 100.0

    return {
        "FCF Yield": _pct(p.get("fcf_yield")),
        "ROIC": _pct(p.get("roic")),
        "CFO Margin": _pct(p.get("cfo_margin")),
        "Cash Flow Status": _status_cell(p),
        "Cash Flow As Of": p.get("stmt_as_of"),
    }


def _status_cell(p):
    """One cell a reader can act on: why a number is missing, or why it is suspect.

    `ok` alone would be ambiguous between "checked and agreed" and "fetched fine, never
    checked", which is the distinction the corroboration exists to draw.
    """
    status = p.get("status", STATUS_NOT_ATTEMPTED)
    if status != STATUS_OK:
        return status
    if p.get("check") == CHECK_UNRECONCILED:
        return CHECK_UNRECONCILED
    if p.get("check") == CHECK_OK:
        return "ok (corroborated)"
    # ⛑ "WE COULD NOT CHECK" IS NOT "WE CHECKED". A candidate whose statements were
    # unavailable, or whose corroboration call failed, used to collapse into a plain `ok`
    # cell — and screens_equity's mid-teens screen rejects only `unreconciled`, so an
    # unverified figure published under a section promising corroboration (Codex r7).
    fcf = p.get("fcf_yield")
    candidate = fcf is not None and abs(fcf) >= CHECK_TRIGGER
    if candidate and p.get("check") == CHECK_NO_STATEMENTS:
        return "ok (not corroborated: no statements)"
    if candidate and p.get("check") == CHECK_NOT_CHECKED:
        return "ok (not corroborated)"
    if p.get("ic_nonpositive"):
        return "ok (ROIC withheld: invested capital <= 0)"
    return STATUS_OK
