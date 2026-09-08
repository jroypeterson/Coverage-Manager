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


def test_the_weekly_report_uses_the_aggregate_helper_and_blanks_on_a_dead_rate():
    """⛑ The lane that did NOT inherit the 2026-09-07 fix. Structural, and
    labelled as such: it reads the source of the conversion block."""
    import inspect
    import reporting.generate as g
    src = inspect.getsource(g)
    i = src.index("# Convert Mkt Cap, EV, Net Debt to USD")
    block = src[i:i + 2600]
    assert "fetch_aggregate_fx(" in block, \
        "generate.py still converts aggregates on the raw quote-currency rate"
    assert "fund[field] = None" in block, \
        "a missing rate must BLANK the field, never leave the raw value under a USD heading"
