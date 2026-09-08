"""The weekly performance report computes EV, not converts it.

⛑ WHY THIS FILE EXISTS. The coverage workbook was fixed on 2026-09-08 and this
lane was not, so for one day the two published surfaces disagreed about Novo
Nordisk while reading the same vendor. That is the same shape as the Aspen
100x bug the day before: `MINOR_UNITS` was fixed beside one consumer and the
other kept publishing wrong. The rule now lives in `providers/valuation.py` and
BOTH lanes import it -- these tests drive THIS lane's real conversion block, not
the shared helper, because a test of the helper proves nothing about the caller.

Every test here is behavioural: it calls `_convert_aggregates_to_usd`, the
function production calls, with an injected `fx`. No network.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FX = {"USD": 1.0, "JPY": 0.006502331234514713, "DKK": 0.15550141036510468,
      "ZAR": 0.06254061311483383, "GBP": 1.3554726839065552}


def _g():
    import reporting.generate as g
    return g


def _tak_row():
    """A Takeda-shaped ADR: quotes USD, reports JPY. Figures are the recorded
    live payload rounded; the vendor EV is the mixed-unit one it really served."""
    mc = 58.4e9
    debt, cash = 5.4e12, 0.42e12
    return {
        "Mkt Cap": mc,
        # what the vendor actually hands over: quote-currency cap + yen net debt
        "Enterprise Value": mc + (debt - cash),
        "Net Debt": debt - cash,
        "EV/S": 1.3,          # the vendor's own broken multiple
        "EV/EBITDA": 4.8,
        "_valuation": {
            "currency": "USD", "financialCurrency": "JPY",
            "marketCap": mc, "totalDebt": debt, "totalCash": cash,
            "totalRevenue": 4.6e12, "ebitda": 1.24e12,
        },
    }


def test_the_adr_ev_is_recomputed_not_converted():
    """⛑ THE BUG. The vendor EV here is ~5.0e12 -- yen added to dollars. The old
    code multiplied that by the USD rate (1.0) and published it verbatim, so the
    report carried Takeda at roughly USD 5 TRILLION."""
    g = _g()
    funds = {"TAK": _tak_row()}
    vendor_ev = funds["TAK"]["Enterprise Value"]
    g._convert_aggregates_to_usd(funds, {"TAK": "USD"}, fx=FX)
    ev = funds["TAK"]["Enterprise Value"]
    assert ev is not None
    assert ev < vendor_ev / 10, (
        "published the vendor's mixed-unit EV: %.3e" % ev)
    assert 60e9 < ev < 130e9, "EV %.3e is not a ~USD 90bn company" % ev


def test_net_debt_is_converted_on_the_REPORTING_rate():
    """⛑ `ev - mc` returned the YEN leg and the code labelled it USD. Novo's
    DKK 95bn published as USD 95bn is the same defect in another currency."""
    g = _g()
    funds = {"TAK": _tak_row()}
    g._convert_aggregates_to_usd(funds, {"TAK": "USD"}, fx=FX)
    nd = funds["TAK"]["Net Debt"]
    expected = (5.4e12 - 0.42e12) * FX["JPY"]
    assert abs(nd - expected) / expected < 1e-9, (
        "Net Debt %.3e != yen net debt at the JPY rate %.3e -- it was published "
        "in the reporting currency under a USD heading" % (nd, expected))
    assert nd < 40e9, "net debt is still yen-scale"


def test_the_ev_multiples_are_replaced_too():
    """A multiple built on a mixed-unit numerator is as wrong as the numerator.
    Leaving `EV/S` alone while fixing `EV` would publish two numbers that cannot
    both be true of the same company."""
    g = _g()
    funds = {"TAK": _tak_row()}
    g._convert_aggregates_to_usd(funds, {"TAK": "USD"}, fx=FX)
    assert funds["TAK"]["EV/S"] != 1.3, "kept the vendor's EV/S"
    assert 2.0 < funds["TAK"]["EV/S"] < 4.5, funds["TAK"]["EV/S"]
    assert 8.0 < funds["TAK"]["EV/EBITDA"] < 16.0, funds["TAK"]["EV/EBITDA"]


def test_a_us_row_is_recomputed_and_lands_in_the_same_place():
    """Same currency both sides, so the vendor EV happens to be consistent --
    recomputing must agree with it rather than 'repair' it."""
    g = _g()
    mc, debt, cash = 400e9, 60e9, 20e9
    funds = {"US": {
        "Mkt Cap": mc, "Enterprise Value": mc + debt - cash, "Net Debt": debt - cash,
        "EV/S": 5.0, "EV/EBITDA": 12.0,
        "_valuation": {"currency": "USD", "financialCurrency": "USD",
                       "marketCap": mc, "totalDebt": debt, "totalCash": cash,
                       "totalRevenue": 80e9, "ebitda": 30e9},
    }}
    g._convert_aggregates_to_usd(funds, {"US": "USD"}, fx=FX)
    assert abs(funds["US"]["Enterprise Value"] - 440e9) < 1e6
    assert abs(funds["US"]["Net Debt"] - 40e9) < 1e6


def test_a_row_with_no_primitives_is_BLANKED_not_trusted():
    """⛑ THE FALLBACK DOOR. `provider_chain._merge_partial` fills any None from
    the next provider, and FMP derives Net Debt by the same `EV - Mkt Cap`
    subtraction -- so a row that reaches here without primitives may be carrying
    FMP's identically mixed-unit EV. It is not publishable, and there is no
    ratio test that could tell: Novo's was 37% wrong and looked normal."""
    g = _g()
    funds = {"FMP": {"Mkt Cap": 1e9, "Enterprise Value": 9.9e12,
                     "Net Debt": 9.9e12, "EV/S": 7.0, "EV/EBITDA": 3.0}}
    g._convert_aggregates_to_usd(funds, {"FMP": "USD"}, fx=FX)
    for f in ("Enterprise Value", "Net Debt", "EV/S", "EV/EBITDA"):
        assert funds["FMP"][f] is None, "%s survived with no primitives" % f
    assert funds["FMP"]["Mkt Cap"] == 1e9, "Mkt Cap is proven independently"


def test_a_minor_unit_row_uses_the_major_rate_for_both_legs():
    """Aspen: quotes ZAc, reports ZAR. The cap is in whole rand despite the
    cents quote, and net debt is in rand -- so both take the ZAR rate."""
    g = _g()
    mc, debt, cash = 69.4e9, 30e9, 8e9
    funds = {"APN.JO": {
        "Mkt Cap": mc, "Enterprise Value": mc + debt - cash, "Net Debt": debt - cash,
        "EV/S": 1.0, "EV/EBITDA": 5.0,
        "_valuation": {"currency": "ZAc", "financialCurrency": "ZAR",
                       "marketCap": mc, "totalDebt": debt, "totalCash": cash,
                       "totalRevenue": 40e9, "ebitda": 8e9},
    }}
    g._convert_aggregates_to_usd(funds, {"APN.JO": "ZAc"}, fx=FX)
    ev = funds["APN.JO"]["Enterprise Value"]
    assert 4e9 < ev < 8e9, "EV %.3e -- a 100x either way is the ZAc trap" % ev


def test_an_unprovable_input_blanks_ev_but_keeps_market_cap():
    """Blank the FIELD, never the row."""
    g = _g()
    row = _tak_row()
    row["_valuation"]["totalDebt"] = None
    funds = {"TAK": row}
    g._convert_aggregates_to_usd(funds, {"TAK": "USD"}, fx=FX)
    assert funds["TAK"]["Enterprise Value"] is None
    assert funds["TAK"]["Net Debt"] is None
    assert funds["TAK"]["Mkt Cap"] == 58.4e9


def test_a_dead_quote_rate_still_blanks_everything_including_ev():
    """The pre-existing unconvertible path blanked the three aggregates. It must
    also blank the EV multiples, or a row publishes a multiple with no EV."""
    g = _g()
    funds = {"TAK": _tak_row()}
    g._convert_aggregates_to_usd(funds, {"TAK": "XXX"}, fx=FX)
    for f in ("Mkt Cap", "Enterprise Value", "Net Debt", "EV/S", "EV/EBITDA"):
        assert funds["TAK"][f] is None, "%s survived a dead rate" % f


def test_the_primitives_never_reach_the_report_row():
    """⛑ `calcs.build_result_row` does `row.update(fund)`, so every key in this
    dict becomes a DataFrame column. `_valuation` holds a DICT, which openpyxl
    cannot write -- a crash partway through building the report, after the
    expensive fetching is done. It is transport: consumed at the conversion site
    and dropped there, on every path."""
    g = _g()
    funds = {
        "TAK": _tak_row(),                                  # computed
        "FMP": {"Mkt Cap": 1e9, "Enterprise Value": 2e9},   # no primitives
        "DEAD": _tak_row(),                                 # unconvertible
    }
    g._convert_aggregates_to_usd(
        funds, {"TAK": "USD", "FMP": "USD", "DEAD": "XXX"}, fx=FX)
    for t, fund in funds.items():
        assert "_valuation" not in fund, "%s leaked the primitives into the row" % t


def test_no_row_carries_a_non_scalar_value_after_conversion():
    """The general form of the rule above: whatever this function leaves behind
    has to be writable to a spreadsheet cell."""
    g = _g()
    funds = {"TAK": _tak_row()}
    g._convert_aggregates_to_usd(funds, {"TAK": "USD"}, fx=FX)
    for k, v in funds["TAK"].items():
        assert v is None or isinstance(v, (int, float, str)), (
            "column %r would reach openpyxl as %s" % (k, type(v).__name__))
