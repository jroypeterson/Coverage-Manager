"""FMP revenue, used ONLY to tell a real zero from an absence.

⛑ THE PROBLEM THIS SOLVES. yfinance returns `totalRevenue: null` both for a
company with no product and for a company it simply has no figure for. Measured
2026-09-08 across the 331 Biopharma rows where yfinance is null: FMP answers 264
(80%), and 240 of those report revenue of EXACTLY ZERO. So the common case for a
yfinance null in this sector is a vendor representing zero as absent.

That distinction is the whole classification. "We could not measure it" and "it
has no product" are different claims and the second is far stronger -- publishing
the second when only the first is true would label ~240 real pre-revenue biotechs
on evidence we do not have.

⛑ IT IS NOT USED AS A MAGNITUDE, DELIBERATELY. yfinance is TTM; FMP here is the
last completed fiscal year, and it carries its own `reportedCurrency`. Mixing the
two as if they were one series would put two different periods, and possibly two
different currencies, under one heading -- the exact class of defect the EV work
in this repo spent two days removing. So a positive FMP figure is recorded and
SHOWN, never silently substituted: those rows stay `unknown` for a human to look
at once.

⛑ AND AN FMP ZERO IS CORROBORATED BEFORE IT IS BELIEVED. FMP fills absent line
items with 0, so an all-zero statement is a blank wearing a number. A genuine
pre-revenue biotech still spends: it has R&D or operating expenses above zero on
the SAME statement. Requiring that is what stops "a missing key is not a blank
value" from reappearing here -- and the check is written against the PLAUSIBLE
wrong value (an empty statement), not against garbage.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request

from cache import cache_get, cache_set

logger = logging.getLogger(__name__)

CACHE_NS = "revenue_fmp"
CACHE_TTL_HOURS = 24.0 * 30          # a completed fiscal year does not move
ENDPOINT = "https://financialmodelingprep.com/stable/income-statement"

# Fields that must not ALL be zero for a reported zero revenue to be believed.
# A pre-revenue biotech spends money; a company that reported nothing at all did
# not file an all-zero income statement.
_LIFE_SIGNS = ("researchAndDevelopmentExpenses", "operatingExpenses",
               "costOfRevenue", "generalAndAdministrativeExpenses",
               "totalOperatingExpenses", "netIncome")


def classify_statement(rec):
    """`(status, revenue, currency, period)` for one FMP income statement.

    status: `positive` / `zero` / `empty_statement` / `no_data`.
    `empty_statement` is the corroboration failing -- a zero we do not believe.
    """
    if not isinstance(rec, dict):
        return "no_data", None, None, None
    rev = rec.get("revenue")
    ccy = rec.get("reportedCurrency")
    per = rec.get("date")
    if not isinstance(rev, (int, float)):
        return "no_data", None, ccy, per
    if rev > 0:
        return "positive", float(rev), ccy, per
    # revenue == 0 (or negative, which is a contra-revenue restatement): believe
    # it only if the company shows other signs of life on the same statement.
    for f in _LIFE_SIGNS:
        v = rec.get(f)
        if isinstance(v, (int, float)) and v != 0:
            return "zero", 0.0, ccy, per
    return "empty_statement", None, ccy, per


def fetch_revenue(ticker, api_key, use_cache=True, timeout=25):
    """`{status, revenue, currency, period}` for one ticker. Cached."""
    key = "rev_%s" % ticker
    if use_cache:
        hit = cache_get(CACHE_NS, key, CACHE_TTL_HOURS)
        if hit is not None:
            return hit
    if not api_key:
        return {"status": "no_data", "revenue": None, "currency": None,
                "period": None}
    url = "%s?symbol=%s&limit=1&apikey=%s" % (ENDPOINT, ticker, api_key)
    rec = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as fh:
                payload = json.load(fh)
            rec = payload[0] if isinstance(payload, list) and payload else {}
            break
        except (urllib.error.URLError, ValueError, OSError, TimeoutError):
            if attempt == 2:
                # ⛑ A transport failure is NOT an answer and is never cached --
                # freezing it would turn one bad minute into 30 days of "this
                # company has no revenue on record".
                return {"status": "no_data", "revenue": None, "currency": None,
                        "period": None}
            time.sleep(1.5 * (attempt + 1))
    status, rev, ccy, per = classify_statement(rec)
    out = {"status": status, "revenue": rev, "currency": ccy, "period": per}
    if status in ("positive", "zero", "empty_statement"):
        cache_set(CACHE_NS, key, out)      # a real answer, including a disbelieved zero
    return out
