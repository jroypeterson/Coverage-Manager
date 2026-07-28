"""Renaissance Capital IPO date — a metered single-company *verifier*.

Renaissance Capital (the IPO-calendar / IPO-ETF people) exposes a FREE endpoint
that returns the offer date for one company:

    GET https://api.renaissancecapital.com/free/CompanyIpoDate?TickerSymbol=RDDT
    GET https://api.renaissancecapital.com/free/CompanyIpoDate?CIK=1713445
    -> {"tickerSymbol": "RDDT", "companyName": "Reddit", "offerDate": "3/20/2024"}

Auth is an Azure API Management subscription key sent as the
``Ocp-Apim-Subscription-Key`` header. The FREE tier is **120 calls per MONTH**
(not per day) and returns no remaining-count header, so this module tracks spend
itself in a small usage file and refuses to exceed ``MONTHLY_CALL_CAP``.

Design (see CLAUDE.md "IPO offer-date" + workspace memory reference_renaissance_ipo_api):
- An IPO offer date is **immutable**, so results (including authoritative "no
  data" 404s) are cached effectively forever and a resolved ticker is never
  re-fetched. The 120/month is therefore a one-time-per-ticker spend.
- This is a *verifier*, not a feed: it confirms an offer date for a name CM is
  actually going to watch (yfinance/FMP often report the first-trade or
  listing-transfer date for SMID names, not the offer). The value is IPO *age*
  as a routing signal — use ``ipo_age()`` / ``lockup_dates()`` to derive that.
- Never raises on a single lookup failure (transient errors return None, uncached
  so the next run retries). It DOES raise ``RenaissanceBudgetError`` when the
  monthly cap would be exceeded — a loud stop, never a silent skip.
"""

import time
from datetime import date, datetime, timedelta, timezone

import requests

from cache import cache_get, cache_set
from logging_utils import get_logger, log_exception, retry_on_failure

logger = get_logger("providers.renaissance_ipo")

BASE_URL = "https://api.renaissancecapital.com/free/CompanyIpoDate"
IPO_CACHE_NS = "ipo_renaissance"
# IPO dates are immutable -> cache effectively forever (100 years).
IPO_CACHE_TTL_HOURS = 24.0 * 365 * 100
REQUEST_SPACING_SEC = 0.5          # polite; quota (not rate) is the real limit
REQUEST_TIMEOUT_SEC = 20

# Free tier is 120 calls/MONTH. Stop a little short so ad-hoc verifier calls and
# fresh-discovery lookups always have headroom over a backfill that drains the budget.
MONTHLY_CALL_CAP = 115

# Usage counter lives alongside the cache, keyed by calendar month "YYYY-MM".
# The leading underscore keeps it out of the per-ticker cache keyspace.
_USAGE_KEY = "_usage"


class RenaissanceBudgetError(RuntimeError):
    """Raised when a network call would exceed the monthly free-tier cap."""


# ── monthly usage counter ─────────────────────────────────────────────────────

def _current_month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load_usage():
    # max_age huge so the usage file never "expires"; it's state, not a cache.
    data = cache_get(IPO_CACHE_NS, _USAGE_KEY, IPO_CACHE_TTL_HOURS)
    return data if isinstance(data, dict) else {}


def calls_this_month():
    """How many *counted* (authenticated) calls have been made this month."""
    return int(_load_usage().get(_current_month(), 0))


def calls_remaining():
    """Calls left before hitting MONTHLY_CALL_CAP (never negative)."""
    return max(0, MONTHLY_CALL_CAP - calls_this_month())


def _record_call():
    usage = _load_usage()
    month = _current_month()
    usage[month] = int(usage.get(month, 0)) + 1
    cache_set(IPO_CACHE_NS, _USAGE_KEY, usage)


# ── date helpers (pure) ────────────────────────────────────────────────────────

def _parse_offer_date(raw):
    """Parse Renaissance's 'M/D/YYYY' (or ISO) into a date, or None."""
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # last resort: lenient split on M/D/YYYY with non-padded parts
    try:
        m, d, y = (int(x) for x in s.split("/"))
        return date(y, m, d)
    except Exception:  # noqa: BLE001
        return None


def lockup_dates(offer_date):
    """Return (est_lockup_90d, est_lockup_180d) as ISO strings from an offer date.

    The API has NO lockup field — these are the conventional 90/180-day estimates
    derived from the offer date. Accepts an ISO string or a date.
    """
    if isinstance(offer_date, str):
        offer_date = _parse_offer_date(offer_date)
    if not offer_date:
        return "", ""
    return (
        (offer_date + timedelta(days=90)).isoformat(),
        (offer_date + timedelta(days=180)).isoformat(),
    )


def ipo_age(offer_date, as_of=None):
    """Return (age_days, age_bucket) for an offer date (ISO string or date).

    Bucket is the routing signal: '<30d', '30-90d', '90-180d', '180-365d',
    '1-2y', '>2y'. Returns (None, '') when the offer date is missing/unparseable.
    Date-relative, so compute on read — never store the bucket as an immutable field.
    """
    if isinstance(offer_date, str):
        offer_date = _parse_offer_date(offer_date)
    if not offer_date:
        return None, ""
    as_of = as_of or date.today()
    age = (as_of - offer_date).days
    if age < 30:
        bucket = "<30d"
    elif age < 90:
        bucket = "30-90d"
    elif age < 180:
        bucket = "90-180d"
    elif age < 365:
        bucket = "180-365d"
    elif age < 730:
        bucket = "1-2y"
    else:
        bucket = ">2y"
    return age, bucket


# ── network ────────────────────────────────────────────────────────────────────

@retry_on_failure(max_retries=2, base_delay=2.0, logger_name="providers.renaissance_ipo")
def _request(params, api_key):
    """One HTTP GET. Returns the requests.Response (retried on exception)."""
    return requests.get(
        BASE_URL,
        params=params,
        headers={"Ocp-Apim-Subscription-Key": api_key},
        timeout=REQUEST_TIMEOUT_SEC,
    )


#: Lookup outcomes. `no_data` is an ANSWER (Renaissance has no IPO on record for
#: this name); `inconclusive` means we never got one — throttled, offline, no key.
#: Collapsing the two is the "absent data is not a finding" trap the sibling
#: delisted_check was hardened against on 2026-07-25: on 2026-07-28 a burst of
#: HTTP 429s was reported to the operator as "no IPO on record: 9" when only 3
#: of those were real. The cache was always right — it never stored a 429 — but
#: the summary was not, and a summary that under-reports its own ignorance is how
#: a name silently never gets an offer date.
STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_INCONCLUSIVE = "inconclusive"


def fetch_ipo_date_ex(ticker, api_key, cik=None, use_cache=True):
    """Return ``(status, payload)`` — the honest form of :func:`fetch_ipo_date`.

    ``status`` is one of ``STATUS_OK`` / ``STATUS_NO_DATA`` / ``STATUS_INCONCLUSIVE``.
    ``payload`` is the result dict when ``ok``, else ``None``.

    Prefers the CIK query when a CIK is supplied (the API's most reliable key).
    Results are cached by **ticker** effectively forever, INCLUDING an authoritative
    404, so a resolved-or-known-empty ticker is never re-hit. An ``inconclusive``
    result is never cached and never counted against quota, so the next run retries.

    Raises RenaissanceBudgetError if a network call would exceed the monthly cap.
    """
    ticker = (ticker or "").strip()
    if not ticker:
        return STATUS_INCONCLUSIVE, None
    if not api_key:
        logger.warning("RENAISSANCE_API_KEY not set — skipping IPO-date lookup for %s", ticker)
        return STATUS_INCONCLUSIVE, None

    if use_cache:
        cached = cache_get(IPO_CACHE_NS, ticker, IPO_CACHE_TTL_HOURS)
        if cached is not None:
            if cached.get("offer_date"):
                return STATUS_OK, cached
            return STATUS_NO_DATA, None

    if calls_this_month() >= MONTHLY_CALL_CAP:
        raise RenaissanceBudgetError(
            f"Renaissance free-tier monthly cap reached "
            f"({MONTHLY_CALL_CAP} calls) — refusing to look up {ticker}"
        )

    cik = (str(cik).strip() if cik is not None else "")
    params = {"CIK": cik} if cik else {"TickerSymbol": ticker}

    try:
        resp = _request(params, api_key)
    except Exception as e:  # noqa: BLE001 — transient; surface and keep going, don't cache
        log_exception(logger, f"Renaissance IPO lookup failed for {ticker}", e)
        return STATUS_INCONCLUSIVE, None

    # 404 = authenticated "no IPO on record" -> counts against quota AND is cached.
    if resp.status_code == 404:
        _record_call()
        empty = {"ticker": ticker, "company_name": "", "offer_date": None}
        if use_cache:
            cache_set(IPO_CACHE_NS, ticker, empty)
        return STATUS_NO_DATA, None

    if resp.status_code != 200:
        # 401/403/429/5xx — we learned nothing: don't count, don't cache, don't
        # let it masquerade as an answer.
        logger.warning("Renaissance IPO lookup for %s returned HTTP %s", ticker, resp.status_code)
        return STATUS_INCONCLUSIVE, None

    _record_call()
    try:
        body = resp.json()
    except ValueError:
        logger.warning("Renaissance IPO lookup for %s returned non-JSON 200", ticker)
        return STATUS_INCONCLUSIVE, None

    offer = _parse_offer_date(body.get("offerDate"))
    result = {
        "ticker": ticker,
        "company_name": body.get("companyName", "") or "",
        "offer_date": offer.isoformat() if offer else None,
    }
    if use_cache:
        cache_set(IPO_CACHE_NS, ticker, result)
    # A 200 whose offerDate is absent/unparseable IS an answer: the API knows the
    # company and has no offer date for it.
    return (STATUS_OK, result) if result["offer_date"] else (STATUS_NO_DATA, None)


def fetch_ipo_date(ticker, api_key, cik=None, use_cache=True):
    """Return {"ticker", "company_name", "offer_date"} for a name, or None.

    Back-compat wrapper over :func:`fetch_ipo_date_ex`. Prefer ``_ex`` in new code —
    this form cannot distinguish "no IPO on record" from "we were throttled".
    """
    _status, payload = fetch_ipo_date_ex(ticker, api_key, cik=cik, use_cache=use_cache)
    return payload
