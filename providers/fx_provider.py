"""FX rate provider — fetches USD exchange rates via yfinance, cached daily."""

import yfinance as yf

from cache import cache_get, cache_set
from logging_utils import get_logger

logger = get_logger("providers.fx")

CACHE_TTL_HOURS = 12

# ── minor-unit quote currencies ───────────────────────────────────────────────
# {minor: (major, minor_per_major)}. A venue can QUOTE in a minor unit while the
# same vendor reports that company's AGGREGATES (market cap, EV, net debt) in the
# major one -- so price and market cap need DIFFERENT rates off the same pair.
#
# ⛑ ASKING THE VENDOR FOR THE MINOR CODE DOES NOT GIVE A USABLE ANSWER, and it
# fails in BOTH directions, which is why this has to be a table rather than a
# per-caller guess. Measured against the live cache 2026-09-08:
#     ZAcUSD=X -> 0.000625   the CENTS rate. Applied to a market cap Yahoo
#                            reports in whole rand, that is a silent 100x LOW --
#                            Aspen Pharmacare published at USD 43M against a
#                            real USD 4,217M.
#     GBpUSD=X -> 1.35547    the POUNDS rate, byte-identical to GBPUSD=X. So the
#                            same call that is 100x wrong for ZAc is correct for
#                            GBp. Nothing about the response says which you got.
#
# `major_unit()` is therefore the single rule every AGGREGATE conversion must go
# through. Prices stay in the quoted unit and are never converted here.
MINOR_UNITS = {
    "GBp": ("GBP", 100.0),   # LSE, pence
    "ZAc": ("ZAR", 100.0),   # JSE, cents
}


def major_unit(ccy):
    """The currency an AGGREGATE value is reported in, for a given quote code."""
    return MINOR_UNITS.get(ccy, (ccy, 1))[0]


def fetch_aggregate_fx(currencies):
    """`{code: rate}` safe for converting MARKET CAP / EV / NET DEBT.

    Requests the MAJOR unit for every minor-unit code and returns the rate under
    BOTH keys, so a caller that looks up the raw quote currency cannot silently
    get the minor rate. Use this, not `fetch_fx_rates`, for company aggregates.
    """
    wanted = {c for c in currencies if c and c != "USD"}
    majors = {major_unit(c) for c in wanted}
    rates = fetch_fx_rates(majors)
    out = dict(rates)
    for c in wanted:
        m = major_unit(c)
        if m in rates:
            out[c] = rates[m]          # deliberately the MAJOR rate
    return out


def fetch_fx_rates(currencies):
    """Fetch exchange rates to USD for a set of currency codes.

    Returns dict mapping currency code -> rate (1 unit of currency = rate USD).
    USD maps to 1.0. Unknown/failed currencies are omitted.
    """
    rates = {"USD": 1.0}
    to_fetch = {c for c in currencies if c and c != "USD"}
    if not to_fetch:
        return rates

    # Check cache first
    uncached = []
    for c in to_fetch:
        cached = cache_get("fx", c, CACHE_TTL_HOURS)
        if cached is not None:
            rates[c] = cached
        else:
            uncached.append(c)

    if not uncached:
        logger.info("FX rates: all %d from cache", len(to_fetch))
        return rates

    # Fetch via yfinance (e.g. JPYUSD=X)
    symbols = [f"{c}USD=X" for c in uncached]
    logger.info("Fetching FX rates for %d currencies: %s", len(uncached), ", ".join(uncached))

    for c, sym in zip(uncached, symbols):
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="5d")
            if hist is not None and not hist.empty:
                rate = float(hist["Close"].iloc[-1])
                rates[c] = rate
                cache_set("fx", c, rate)
            else:
                logger.warning("No FX data for %s", c)
        except Exception as e:
            logger.warning("FX fetch failed for %s: %s", c, e)

    logger.info("FX rates resolved: %d/%d", len(rates) - 1, len(to_fetch))
    return rates
