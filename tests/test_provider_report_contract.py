"""The contract BETWEEN the provider and the report — the seam nothing tested.

⛑ FOUND BY REVIEW (Fable, High, 2026-09-08), and it is this repo's own recorded
failure mode: A GREEN SUITE IS NOT EVIDENCE. Two one-line mutations left all
~1,648 tests passing and blanked EV for the ENTIRE universe in production:

  1. delete the `_valuation` block in `providers/yfinance_provider.py`
  2. revert the cache namespace `yf2_` back to `yf_`

Both survived because every test in `test_report_ev_from_primitives.py`
hand-builds its own `_valuation` dict, and nothing anywhere referenced `yf2_`.
The two halves were each tested and the JOIN between them was not -- so the
report would have silently published a blank EV column behind a single log line.
Unlike the coverage workbook, this lane has NO abort threshold, so nothing else
would have caught it.

These tests run against the real provider function with a mocked vendor payload.
No network.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.yfinance_provider import fetch_fundamentals

# The exact keys `providers.valuation.derive_valuation` reads. If the provider
# stops supplying one, EV silently becomes unprovable for every row.
REQUIRED = ["currency", "financialCurrency", "marketCap",
            "totalDebt", "totalCash", "totalRevenue", "ebitda"]

TAK_INFO = {
    "currency": "USD", "financialCurrency": "JPY",
    "marketCap": 58.4e9, "totalDebt": 5.4e12, "totalCash": 0.42e12,
    "totalRevenue": 4.6e12, "ebitda": 1.24e12,
    "enterpriseValue": 58.4e9 + 5.4e12 - 0.42e12,
    "currentPrice": 15.0,
}


@patch("providers.yfinance_provider.cache_set")
@patch("providers.yfinance_provider.cache_get", return_value=None)
@patch("providers.yfinance_provider._fetch_ticker_info", return_value=dict(TAK_INFO))
def test_the_provider_supplies_the_primitives_the_report_needs(mi, mg, ms):
    """⛑ MUTANT 1. Deleting the `_valuation` block passed every other test."""
    result, _, _ = fetch_fundamentals("TAK", use_cache=False)
    prim = result.get("_valuation")
    assert isinstance(prim, dict), (
        "the provider stopped supplying `_valuation`; EV is now unprovable for "
        "EVERY row and the report has no abort threshold to notice")
    missing = [k for k in REQUIRED if k not in prim]
    assert not missing, "`_valuation` is missing %s" % missing
    assert prim["financialCurrency"] == "JPY"
    assert prim["currency"] == "USD"


@patch("providers.yfinance_provider.cache_set")
@patch("providers.yfinance_provider.cache_get", return_value=None)
@patch("providers.yfinance_provider._fetch_ticker_info", return_value=dict(TAK_INFO))
def test_the_provider_output_feeds_the_report_end_to_end(mi, mg, ms):
    """The JOIN itself: the provider's real output, through the report's real
    conversion function, producing a correct published EV. Neither side is
    hand-built. This is the test whose absence let both mutants live."""
    import reporting.generate as g
    result, _, currency = fetch_fundamentals("TAK", use_cache=False)
    funds = {"TAK": result}
    fx = {"USD": 1.0, "JPY": 0.006502331234514713}
    g._convert_aggregates_to_usd(funds, {"TAK": currency}, fx=fx)
    ev = funds["TAK"]["Enterprise Value"]
    assert ev is not None, "the provider/report join blanked a provable row"
    assert 60e9 < ev < 130e9, "EV %.4g is not a ~USD 90bn company" % ev
    assert 2.0 < funds["TAK"]["EV/S"] < 4.5
    assert funds["TAK"]["Net Debt"] < 40e9, "net debt is still yen-scale"


@patch("providers.yfinance_provider.cache_set")
@patch("providers.yfinance_provider.cache_get")
@patch("providers.yfinance_provider._fetch_ticker_info", return_value=dict(TAK_INFO))
def test_the_cache_namespace_is_read_under_the_key_it_is_written_under(mi, mget, mset):
    """⛑ MUTANT 2. Reverting `yf2_` -> `yf_` blanked EV for 24h off entries that
    predate `_valuation`, and NO test mentioned either key. Pin the namespace to
    the one the writer uses rather than to a literal, so a future bump moves both
    ends together and only a MISMATCH fails."""
    mget.return_value = None
    fetch_fundamentals("TAK", use_cache=True)
    read_key = mget.call_args[0][1]
    write_key = mset.call_args[0][1]
    assert read_key == write_key, (
        "reads %r but writes %r -- every lookup misses" % (read_key, write_key))
    assert read_key.endswith("TAK")
    assert read_key != "yf_TAK", (
        "back on the pre-`_valuation` namespace; entries there carry no "
        "primitives and the report reads that as unprovable")


@patch("providers.yfinance_provider.cache_set")
@patch("providers.yfinance_provider.cache_get", return_value=None)
@patch("providers.yfinance_provider._fetch_ticker_info")
def test_a_cached_payload_without_primitives_blanks_rather_than_guesses(mi, mg, ms):
    """A row reaching the report with no primitives -- an FMP fallback, or an old
    cache shape -- must blank, never inherit the vendor's mixed-unit EV."""
    import reporting.generate as g
    funds = {"OLD": {"Mkt Cap": 1e9, "Enterprise Value": 9.9e12, "Net Debt": 9.9e12,
                     "EV/S": 7.0, "EV/EBITDA": 3.0}}
    g._convert_aggregates_to_usd(funds, {"OLD": "USD"}, fx={"USD": 1.0})
    assert funds["OLD"]["Enterprise Value"] is None
    assert funds["OLD"]["Mkt Cap"] == 1e9


# ── the Mkt Cap rate guard (Fable, Medium) ──────────────────────────────────

@pytest.mark.parametrize("bad", [0.0, float("nan"), float("inf"), -1.0])
def test_an_unusable_rate_blanks_market_cap_rather_than_publishing_it(bad):
    """⛑ The Mkt Cap loop kept `is None` after Codex round 3 replaced it with
    `_usable_rate` inside the helper. `fetch_fx_rates` does a bare
    `float(hist["Close"].iloc[-1])` with no finiteness check and caches it 12h,
    so 0.0 and NaN are both reachable: 0.0 published `Mkt Cap = 0.0` under a USD
    heading, and NaN carried a NaN into the DataFrame and on into openpyxl."""
    import reporting.generate as g
    funds = {"JP": {"Mkt Cap": 4e12, "Enterprise Value": 4e12, "Net Debt": 0.0,
                    "EV/S": 1.0, "EV/EBITDA": 1.0}}
    g._convert_aggregates_to_usd(funds, {"JP": "JPY"}, fx={"USD": 1.0, "JPY": bad})
    assert funds["JP"]["Mkt Cap"] is None, (
        "published Mkt Cap %r off an unusable rate %r" % (funds["JP"]["Mkt Cap"], bad))


# ── the second implementation that is now gone (Fable, Low #4) ──────────────

def test_a_string_debt_does_not_crash_the_whole_report():
    """⛑ `num()` accepts the vendor string `"5.4e12"`, so `derive_valuation`
    proved the row -- and the caller then re-derived net debt from the RAW
    payload and raised TypeError on `debt - cash`, aborting the entire run. A
    second implementation of an already-proven value. It is now returned, not
    recomputed."""
    import reporting.generate as g
    row = {"Mkt Cap": 58.4e9, "Enterprise Value": 1.0, "Net Debt": 1.0,
           "EV/S": 1.0, "EV/EBITDA": 1.0,
           "_valuation": {"currency": "USD", "financialCurrency": "JPY",
                          "marketCap": 58.4e9, "totalDebt": "5.4e12",
                          "totalCash": "0.42e12", "totalRevenue": 4.6e12,
                          "ebitda": 1.24e12}}
    funds = {"TAK": row}
    g._convert_aggregates_to_usd(funds, {"TAK": "USD"},
                                 fx={"USD": 1.0, "JPY": 0.006502331234514713})
    assert funds["TAK"]["Enterprise Value"] is not None
    assert 60e9 < funds["TAK"]["Enterprise Value"] < 130e9
