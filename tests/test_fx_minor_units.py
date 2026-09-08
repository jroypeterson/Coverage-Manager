"""Minor-unit quote currencies, shared by every lane that converts aggregates.

⛑ THE DEFECT THIS PINS, and it happened TWICE. A venue can quote a PRICE in a
minor unit (pence, cents) while the same vendor reports that company's
AGGREGATES -- market cap, EV, net debt -- in the major one. Asking the vendor for
the minor code does not give a usable answer, and it fails in BOTH directions,
which is why this must be a table and not a per-caller guess. Measured against
the live cache 2026-09-08:

    ZAcUSD=X -> 0.000625   the CENTS rate. Applied to a whole-rand market cap
                           that is a silent 100x LOW -- Aspen Pharmacare
                           published at USD 43M against a real USD ~4,200M.
    GBpUSD=X -> 1.35547    the POUNDS rate, byte-identical to GBPUSD=X. The same
                           call that is 100x wrong for ZAc is correct for GBp,
                           and nothing in the response says which you got.

Fixed in `scripts/build_hc_coverage_xlsx.py` on 2026-09-07 and NOT carried across
to `reporting/generate.py`, which kept publishing the same 100x error in the
weekly performance report for another day. The rule now lives beside the vendor.
"""
import pytest

import providers.fx_provider as fx


def test_the_table_covers_both_known_minor_units():
    assert fx.MINOR_UNITS["GBp"] == ("GBP", 100.0)
    assert fx.MINOR_UNITS["ZAc"] == ("ZAR", 100.0)


def test_major_unit_maps_minor_to_major_and_passes_others_through():
    assert fx.major_unit("ZAc") == "ZAR"
    assert fx.major_unit("GBp") == "GBP"
    for c in ("USD", "EUR", "JPY", "ZAR", "GBP", "AUD", "CHF", "INR"):
        assert fx.major_unit(c) == c


def test_aggregate_fx_never_requests_a_minor_code(monkeypatch):
    """There is no usable ZAcUSD=X. Asking for it is how the 100x happened."""
    asked = {}

    def fake(codes):
        asked["codes"] = set(codes)
        return {c: 0.5 for c in codes}

    monkeypatch.setattr(fx, "fetch_fx_rates", fake)
    fx.fetch_aggregate_fx(["ZAc", "GBp", "EUR"])
    assert "ZAc" not in asked["codes"], "requested the cents rate for an aggregate"
    assert "GBp" not in asked["codes"]
    assert {"ZAR", "GBP", "EUR"} <= asked["codes"]


def test_aggregate_fx_returns_the_MAJOR_rate_under_the_minor_key(monkeypatch):
    """The caller looks up the row's raw quote currency. It must not be able to
    receive the minor rate by doing so -- that is the whole failure mode."""
    monkeypatch.setattr(fx, "fetch_fx_rates",
                        lambda codes: {c: (0.0625 if c == "ZAR" else 1.36) for c in codes})
    r = fx.fetch_aggregate_fx(["ZAc", "GBp"])
    assert r["ZAc"] == r["ZAR"] == 0.0625, "ZAc must resolve to the RAND rate"
    assert r["GBp"] == r["GBP"] == 1.36


def test_a_missing_major_rate_yields_no_key_rather_than_a_wrong_one(monkeypatch):
    """Absent is a true statement; a wrong unit is not. The caller blanks."""
    monkeypatch.setattr(fx, "fetch_fx_rates", lambda codes: {"USD": 1.0})
    r = fx.fetch_aggregate_fx(["ZAc"])
    assert "ZAc" not in r and "ZAR" not in r


def test_the_weekly_report_converts_a_minor_unit_on_the_MAJOR_rate():
    """⛑ BEHAVIOURAL, not textual (Codex, 2026-09-08). The first version of this
    file had six tests and **five would still have passed with the weekly-report
    fix reverted** -- they exercised the helper in isolation while the lane that
    was actually publishing Aspen at USD 43M went unasserted. The sixth matched
    source text, which passes if `fetch_aggregate_fx` is called and its result
    ignored.

    This drives the real conversion block with a JSE-shaped row and asserts the
    published number."""
    import reporting.generate as g
    funds = {"APN.JO": {"Mkt Cap": 65.6e9, "Enterprise Value": 70.0e9,
                        "Net Debt": 4.4e9, "Price": 15352.0}}
    ccy = {"APN.JO": "ZAc"}
    g._convert_aggregates_to_usd(funds, ccy,
                                 fx={"ZAR": 0.0625, "ZAc": 0.000625, "USD": 1.0})
    mc = funds["APN.JO"]["Mkt Cap"]
    assert mc == pytest.approx(65.6e9 * 0.0625), (
        "market cap must convert on the RAND rate; got %.3e, which is the cents "
        "rate and the exact 100x that published Aspen at USD 43M" % mc)
    assert funds["APN.JO"]["Price"] == 15352.0, "price must stay in the quoted unit"


def test_the_weekly_report_blanks_rather_than_publishing_an_unconverted_value():
    """A missing rate must not leave a local-currency number under a USD heading."""
    import reporting.generate as g
    funds = {"X.XX": {"Mkt Cap": 1.0e9, "Enterprise Value": 2.0e9, "Net Debt": 3.0e8}}
    g._convert_aggregates_to_usd(funds, {"X.XX": "XYZ"}, fx={"USD": 1.0})
    assert funds["X.XX"]["Mkt Cap"] is None
    assert funds["X.XX"]["Enterprise Value"] is None
    assert funds["X.XX"]["Net Debt"] is None


def test_an_unknown_currency_is_not_treated_as_USD():
    """⛑ Codex, High. `all_currencies.get(t, "USD")` plus a falsy skip meant a
    MISSING or EMPTY currency took the US path and published raw. A Japanese
    payload with `Mkt Cap = 4e12` and `currency = ""` would have read as
    USD 4,000B and skewed every cap-weighted basket downstream."""
    import reporting.generate as g
    for bad in ("", None):
        funds = {"T": {"Mkt Cap": 4.0e12, "Enterprise Value": None, "Net Debt": None}}
        g._convert_aggregates_to_usd(funds, {"T": bad} if bad is not None else {},
                                     fx={"USD": 1.0})
        assert funds["T"]["Mkt Cap"] is None, (
            "an unknown currency (%r) published a raw aggregate as USD" % bad)


def test_a_real_usd_row_is_untouched():
    import reporting.generate as g
    funds = {"LLY": {"Mkt Cap": 1.0e12, "Enterprise Value": 1.1e12, "Net Debt": 1.0e10}}
    g._convert_aggregates_to_usd(funds, {"LLY": "USD"}, fx={"USD": 1.0})
    assert funds["LLY"]["Mkt Cap"] == 1.0e12


def test_the_production_path_fetches_AGGREGATE_rates(monkeypatch):
    """⛑ Found by mutation testing, 2026-09-08. Reverting this lane from
    `fetch_aggregate_fx` to `fetch_fx_rates` passed all 1,604 tests, because
    every other test here injects `fx=` and never exercises the fetch.

    It is not a wrong number -- the `major_unit()` lookup means a raw
    `fetch_fx_rates` result has no "ZAR" key, so the row BLANKS. But every
    Johannesburg and London row would blank silently instead of converting,
    which is a real degradation nothing else would report."""
    import reporting.generate as g
    called = {}

    def spy_aggregate(codes):
        called["aggregate"] = set(codes)
        return {"ZAR": 0.0625, "ZAc": 0.0625, "USD": 1.0}

    def boom(codes):
        called["raw"] = set(codes)
        return {c: 0.000625 for c in codes}

    monkeypatch.setattr(g, "fetch_aggregate_fx", spy_aggregate)
    monkeypatch.setattr(g, "fetch_fx_rates", boom)

    funds = {"APN.JO": {"Mkt Cap": 65.6e9, "Enterprise Value": None, "Net Debt": None}}
    g._convert_aggregates_to_usd(funds, {"APN.JO": "ZAc"})   # fx=None -> production path

    assert "aggregate" in called,         "the weekly report must fetch AGGREGATE rates, not raw quote-code rates"
    assert "raw" not in called,         "fetch_fx_rates was used for aggregates; that is the 100x path"
    assert funds["APN.JO"]["Mkt Cap"] == pytest.approx(65.6e9 * 0.0625)
