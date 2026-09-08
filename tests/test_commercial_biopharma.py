"""The computed commercial-biopharma category.

Fixture-driven, no network, no cache dependency: every test builds its own
primitives so the assertions cannot drift with the market or with what happens
to be on disk.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import universe.commercial_biopharma as cb
from providers.fmp_revenue import classify_statement

FX = {"USD": 1.0, "JPY": 0.006502331234514713, "CHF": 1.25}


def _row(t, sub="Biotech", sector="Biopharma", exch="NASDAQ"):
    return {"Ticker": t, "Sector (JP)": sector, "Subsector (JP)": sub,
            "Exchange": exch, "Company Name": t}


def _prim(rev=None, cap=None, ccy="USD", rccy="USD"):
    return {"currency": ccy, "financialCurrency": rccy, "marketCap": cap,
            "totalDebt": 0.0, "totalCash": 0.0, "totalRevenue": rev,
            "ebitda": None}


# ── the rule ────────────────────────────────────────────────────────────────

def test_the_rule_is_the_one_jp_was_shown_not_a_new_one():
    """⛑ PROJECT_IDEAS.md records it verbatim on 2026-09-07: "the 121-name
    commercial universe (revenue > $1bn OR market cap >= $10bn)". A plan later
    proposed a revenue-only $250M line, which would have silently replaced a
    definition he had already been shown AND added the exact band he said to
    exclude. The constants are the contract; a change to them is a change to
    what he asked for."""
    assert cb.MIN_REVENUE_USD_M == 1000.0
    assert cb.MIN_MKT_CAP_USD_M == 10000.0
    assert "1000" in cb.RULE and "10000" in cb.RULE


def test_revenue_leg_alone_qualifies():
    s, _, _ = cb.classify_row(_row("BIG"), _prim(rev=2e9, cap=3e9), FX)
    assert s == "commercial"


def test_market_cap_leg_alone_qualifies():
    """⛑ NOT DECORATION. This is JP's second case -- "a single drug that is huge
    and allows them to become a platform" -- which revenue alone cannot see.
    Arrowhead: $669M revenue on a $12.1B cap. 16 names qualify this way."""
    s, _, _ = cb.classify_row(_row("PLAT"), _prim(rev=669e6, cap=12.1e9), FX)
    assert s == "commercial"


def test_a_name_below_both_legs_is_below_line():
    s, _, _ = cb.classify_row(_row("SMALL"), _prim(rev=200e6, cap=2e9), FX)
    assert s == "below_line"


def test_the_line_is_applied_in_usd_not_the_reporting_currency():
    """A JPY reporter with 4.6tn yen revenue is a ~$30bn company, not a
    4,600,000-unit one. Getting this wrong would sweep in every foreign row."""
    s, rev, _ = cb.classify_row(
        _row("TAK"), _prim(rev=4.6e12, cap=58.4e9, ccy="USD", rccy="JPY"), FX)
    assert s == "commercial"
    assert 20_000 < rev < 45_000, rev


# ── unknown is not a verdict ────────────────────────────────────────────────

def test_no_primitives_at_all_is_unknown_never_below_line():
    """"We could not measure it" and "it has no product" are different claims."""
    s, rev, cap = cb.classify_row(_row("GHOST"), None, FX)
    assert (s, rev, cap) == ("unknown", None, None)


def test_absent_revenue_with_a_small_cap_is_unknown_not_below_line():
    """⛑ The subtle one. The cap leg FAILED on fact, but the revenue leg failed
    on IGNORANCE -- and a small cap does not imply small revenue (Organon:
    $6.1B revenue on a $3.6B cap). Calling this below_line would publish a
    measured shortfall we never measured."""
    s, _, _ = cb.classify_row(_row("Q"), _prim(rev=None, cap=2e9), FX)
    assert s == "unknown"


def test_a_corroborated_fmp_zero_resolves_the_row_to_below_line():
    fmp = {"status": "zero", "revenue": 0.0}
    s, rev, _ = cb.classify_row(_row("PRECOM"), _prim(rev=None, cap=500e6), FX, fmp)
    assert s == "below_line" and rev == 0.0


def test_an_fmp_POSITIVE_figure_is_never_substituted_as_a_magnitude():
    """⛑ FMP here is a completed FISCAL YEAR in its own reported currency;
    yfinance is TTM. Merging them under one heading is the two-periods-one-column
    defect this repo just spent two days removing. A positive FMP figure leaves
    the row `unknown` and is surfaced for a human instead."""
    fmp = {"status": "positive", "revenue": 5e9, "currency": "EUR"}
    s, rev, _ = cb.classify_row(_row("POS"), _prim(rev=None, cap=1e9), FX, fmp)
    assert s == "unknown", "an FMP magnitude was used to classify"
    assert rev is None


# ── the corroboration, tested against the PLAUSIBLE wrong value ─────────────

def test_an_all_zero_statement_is_not_believed_as_a_zero():
    """⛑ FMP fills absent line items with 0, so an all-zero statement is a blank
    wearing a number. A real pre-revenue biotech still SPENDS. Tested against
    the plausible wrong value, not against garbage -- a corroboration check that
    only rejects nonsense passes the error it exists to catch."""
    assert classify_statement(
        {"revenue": 0, "researchAndDevelopmentExpenses": 48e6,
         "netIncome": -52e6})[0] == "zero"
    assert classify_statement(
        {"revenue": 0, "researchAndDevelopmentExpenses": 0,
         "operatingExpenses": 0, "netIncome": 0})[0] == "empty_statement"
    assert classify_statement({"researchAndDevelopmentExpenses": 10})[0] == "no_data"
    assert classify_statement({"revenue": 5.1e9})[0] == "positive"


# ── the curated override ───────────────────────────────────────────────────

def test_large_pharma_is_commercial_even_with_no_data_at_all():
    """⛑ MEASURED, NOT HYPOTHETICAL: Roche (ROG.SW) had NO fundamentals cache
    entry on 2026-09-08 -- yfinance 404s the symbol. A purely computed rule
    dropped Roche from a list titled "commercial biopharma". The curated
    judgement outranks the measurement it exists to encode."""
    s, rev, cap = cb.classify_row(_row("ROG.SW", sub="Large Pharma"), None, FX)
    assert s == "commercial"
    assert rev is None and cap is None, "must not invent figures to justify it"


def test_large_pharma_below_the_line_on_figures_is_still_commercial():
    """JCR (4552.T) is $270M revenue on a $419M cap and is Large Pharma."""
    s, _, _ = cb.classify_row(
        _row("4552.T", sub="Large Pharma"), _prim(rev=270e6, cap=419e6), FX)
    assert s == "commercial"


# ── the floor guard ────────────────────────────────────────────────────────

def test_the_floor_guard_refuses_a_mostly_unresolved_sector():
    """⛑ A wiped cache looks EXACTLY like a sector where nothing qualifies. One
    of those is a reason to change what consumers see; the other is an outage.
    Refuse rather than ship an empty bucket to sigma-alert and the chart pack."""
    rows = [_row("T%d" % i) for i in range(100)]
    by, summary = cb.classify(rows, primitives={}, fx=FX)
    assert summary["unknown"] == 100
    with pytest.raises(cb.RefusedPartialBook):
        cb.check_floor(summary)


def test_the_floor_guard_passes_a_healthy_sector():
    """A guard that can never pass is an outage with good intentions."""
    rows = [_row("T%d" % i) for i in range(100)]
    prims = {"T%d" % i: _prim(rev=5e6, cap=50e6) for i in range(100)}
    _, summary = cb.classify(rows, primitives=prims, fx=FX)
    cb.check_floor(summary)
    assert summary["below_line"] == 100


def test_an_empty_sector_is_refused_rather_than_reported_as_clean():
    _, summary = cb.classify([], primitives={}, fx=FX)
    with pytest.raises(cb.RefusedPartialBook):
        cb.check_floor(summary)


# ── the published artifact ─────────────────────────────────────────────────

def test_the_export_carries_the_RULE_not_just_the_membership():
    """A consumer looking at 123 names must be able to see what produced them
    without reading this module, and a changed line must show up as a changed
    document rather than as a list that quietly grew."""
    rows = [_row("A"), _row("B")]
    prims = {"A": _prim(rev=2e9, cap=3e9), "B": _prim(rev=1e6, cap=1e7)}
    by, summary = cb.classify(rows, primitives=prims, fx=FX)
    pay = cb.published_payload(by, summary, "2026-09-08")
    assert pay["schema_version"] == 1
    assert pay["rule"] == cb.RULE
    assert pay["tickers"] == ["A"]
    assert pay["detail"]["B"]["status"] == "below_line"
    assert pay["counts"]["biopharma_rows"] == 2


def test_only_biopharma_rows_are_touched():
    """The column must not silently clear a value on a MedTech row."""
    import pandas as pd
    rows = [_row("BIO"), _row("MED", sector="MedTech")]
    prims = {"BIO": _prim(rev=2e9, cap=3e9)}
    by, _ = cb.classify(rows, primitives=prims, fx=FX)
    df = pd.DataFrame([{"Ticker": "BIO", cb.COLUMN: ""},
                       {"Ticker": "MED", cb.COLUMN: "KEEPME"}])
    cb.apply_to_frame(df, by)
    assert df.loc[0, cb.COLUMN] == "Y"
    assert df.loc[1, cb.COLUMN] == "KEEPME", "a non-Biopharma row was rewritten"


def test_the_column_is_Y_or_blank_and_never_a_status_string():
    """Every consumer in this fleet parses `Core` as `== "Y"`; this reuses that
    idiom. And "below_line" on the CSV would be actively wrong as a label --
    Harmony at $959M revenue is not "pre-commercial"."""
    import pandas as pd
    rows = [_row("A"), _row("B")]
    prims = {"A": _prim(rev=2e9, cap=3e9), "B": _prim(rev=1e6, cap=1e7)}
    by, _ = cb.classify(rows, primitives=prims, fx=FX)
    df = pd.DataFrame([{"Ticker": "A"}, {"Ticker": "B"}])
    cb.apply_to_frame(df, by)
    assert set(df[cb.COLUMN]) <= {"Y", ""}


# ── the OR has to mean OR in every branch (found by executing, not reading) ──

def test_the_revenue_leg_works_without_a_market_cap():
    """⛑ The published rule is "revenue >= $1bn OR cap >= $10bn". The first
    implementation routed revenue through `derive_valuation`, which is built for
    ENTERPRISE VALUE and returns early when `marketCap` is missing -- silently
    ANDing the revenue leg with cap-availability. A $5bn-revenue row with no cap
    classified `unknown`. The rule string this module PUBLISHES has to be the
    rule it applies."""
    s, rev, cap = cb.classify_row(_row("R"), _prim(rev=5e9, cap=None), FX)
    assert s == "commercial", "the revenue leg was disabled by a missing cap"
    assert rev == 5000.0 and cap is None


def test_the_market_cap_leg_works_without_a_revenue():
    s, rev, cap = cb.classify_row(_row("C"), _prim(rev=None, cap=12e9), FX)
    assert s == "commercial" and rev is None and cap == 12000.0


@pytest.mark.parametrize("rev,cap,expected", [
    (5e8,  2e9,  "below_line"),   # both measured, both fail
    (5e8,  None, "unknown"),      # cap could have rescued it
    (None, 2e9,  "unknown"),      # revenue could have rescued it
    (None, None, "unknown"),
])
def test_below_line_requires_BOTH_legs_measured(rev, cap, expected):
    """⛑ SYMMETRIC, and both directions are real. A small cap does not imply
    small revenue (Organon: $6.1bn revenue on a $3.6bn cap); a modest revenue
    does not imply a small cap (Revolution Medicines: no meaningful revenue,
    $44.9bn cap). Calling either a shortfall publishes a measurement never made.
    The second case returned `below_line` until an edge-case probe caught it."""
    s, _, _ = cb.classify_row(_row("X"), _prim(rev=rev, cap=cap), FX)
    assert s == expected


def test_a_negative_revenue_is_not_a_revenue_figure():
    """A contra-revenue restatement is not a measurement of revenue."""
    s, rev, _ = cb.classify_row(_row("N"), _prim(rev=-1e9, cap=2e9), FX)
    assert rev is None and s == "unknown"


def test_revenue_is_converted_on_the_reporting_rate_without_a_cap():
    """The independent path must still get the currency right."""
    _, rev, _ = cb.classify_row(
        _row("J"), _prim(rev=4.6e12, cap=None, rccy="JPY"), FX)
    assert 20_000 < rev < 45_000, rev
