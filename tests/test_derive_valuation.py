"""EV computed from single-currency primitives, never from the vendor's own.

⛑ THE DEFECT. Yahoo's `enterpriseValue` is `marketCap` (QUOTE currency) plus
`totalDebt - totalCash` (REPORTING currency), summed as if one unit. Measured
2026-09-08: `(EV - marketCap) / (totalDebt - totalCash)` = 1.000 TAK, 1.004 NVO,
0.990 LLY, 1.053 CYH. For a US row the currencies match and it is harmless; for
an ADR it is a number in no unit at all, and Takeda published at USD 5.1tn.

⛑ AND WHY THE OBVIOUS FIX IS WRONG. Tagging EV with `financialCurrency` and
converting gives Takeda ~USD 33bn against a true ~USD 90bn: a PLAUSIBLE wrong
number, worse than an absurd one because absurd numbers get noticed.

Every payload here is RECORDED from the live vendor (tests/fixtures/), not
invented, and no test makes a network call -- a live differential test would be
either skipped in CI or flaky, and would burn the Yahoo budget the build needs.
"""
import json
import os

import pytest

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "fixtures", "yf_valuation_payloads.json")
with open(_FIX, encoding="utf-8") as _fh:
    PAYLOADS = json.load(_fh)

# Spot rates from the FX cache on the day the payloads were recorded, so the
# arithmetic is deterministic and the assertions do not drift with the market.
FX = {"USD": 1.0, "JPY": 0.006502331234514713, "DKK": 0.15550141036510468,
      "EUR": 1.1626555919647217, "ZAR": 0.06254061311483383,
      "GBP": 1.3554726839065552}


def _load():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hcb", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scripts", "build_hc_coverage_xlsx.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


b = _load()


# ── the differential test: an ADR must agree with its own primary listing ─────

@pytest.mark.parametrize("adr,primary,tol", [
    ("TAK", "4502.T", 0.03),
    ("NVO", "NOVO-B.CO", 0.05),   # NVO carries ~DKK 23bn beyond net debt
    ("ASML", "ASML.AS", 0.03),
])
def test_the_adr_and_its_primary_listing_agree_on_ev_sales(adr, primary, tol):
    """⛑ THE PIN, and it needs no second vendor. The ADR quotes in USD and
    reports in the home currency -- the exact mismatch that breaks the vendor's
    EV. The primary listing quotes AND reports in the home currency, so its own
    numbers are internally consistent. If the computation is right the two must
    land on the same multiple; if it converts on the wrong rate they diverge by
    exactly that rate."""
    a = b.derive_valuation(PAYLOADS[adr], FX)
    p = b.derive_valuation(PAYLOADS[primary], FX)
    assert a["ev_sales"] is not None and p["ev_sales"] is not None
    rel = abs(a["ev_sales"] - p["ev_sales"]) / p["ev_sales"]
    assert rel < tol, (
        "%s computes EV/Sales %.2f but its own primary line %s computes %.2f "
        "(%.0f%% apart) -- the currencies are being handled differently"
        % (adr, a["ev_sales"], primary, p["ev_sales"], rel * 100))


def test_the_adr_and_primary_agree_on_ev_itself_in_usd():
    a = b.derive_valuation(PAYLOADS["TAK"], FX)
    p = b.derive_valuation(PAYLOADS["4502.T"], FX)
    rel = abs(a["ev_usd_m"] - p["ev_usd_m"]) / p["ev_usd_m"]
    assert rel < 0.05, ("TAK EV %.0f vs 4502.T %.0f USD $M"
                        % (a["ev_usd_m"], p["ev_usd_m"]))


# ── the vendor's own value is never used ─────────────────────────────────────

def test_takeda_ev_is_neither_the_vendor_value_nor_the_converted_one():
    """Three numbers, only one right. Vendor EV ~5.1e12 (no unit); that same
    value read as JPY ~USD 33bn (plausible, wrong); computed ~USD 90bn."""
    v = b.derive_valuation(PAYLOADS["TAK"], FX)
    vendor_m = PAYLOADS["TAK"]["enterpriseValue"] / 1e6
    as_jpy_m = PAYLOADS["TAK"]["enterpriseValue"] * FX["JPY"] / 1e6
    assert v["ev_usd_m"] < vendor_m / 10, "published the raw mixed-unit vendor EV"
    assert abs(v["ev_usd_m"] - as_jpy_m) / as_jpy_m > 0.5, (
        "computed %.0f is close to the JPY-converted vendor EV %.0f -- that is "
        "the plausible-wrong-number failure" % (v["ev_usd_m"], as_jpy_m))
    assert 60_000 < v["ev_usd_m"] < 130_000, v["ev_usd_m"]


def test_the_garbage_class_is_repaired_even_when_currencies_match():
    """argenx quotes AND reports in USD, so no currency fix touches it -- and its
    vendor EV was still 25x its market cap. Computing from components is what
    repairs it."""
    p = PAYLOADS["ARGX"]
    assert p["currency"] == p["financialCurrency"] == "USD"
    v = b.derive_valuation(p, FX)
    mc_m = p["marketCap"] / 1e6
    assert v["ev_usd_m"] < mc_m * 1.5, (
        "ARGX EV %.0f against a market cap of %.0f" % (v["ev_usd_m"], mc_m))
    assert v["ev_sales"] is not None and v["ev_sales"] < 25, v["ev_sales"]


def test_a_us_row_is_essentially_unchanged():
    """CYH is legitimately levered -- EV many times market cap -- and correct.
    The fix must not 'repair' it. This is the plausible-wrong-value guard: a
    ratio screen flags CYH and passes Novo, which is why the gate is
    missing-proof rather than anomaly."""
    v = b.derive_valuation(PAYLOADS["CYH"], FX)
    vendor_m = PAYLOADS["CYH"]["enterpriseValue"] / 1e6
    assert abs(v["ev_usd_m"] - vendor_m) / vendor_m < 0.10


def test_a_minor_unit_quote_with_a_major_unit_report():
    """APN.JO quotes ZAc and reports ZAR -- both paths at once. Market cap must
    take the RAND rate via major_unit, net debt the ZAR rate directly."""
    p = PAYLOADS["APN.JO"]
    assert p["currency"] == "ZAc" and p["financialCurrency"] == "ZAR"
    v = b.derive_valuation(p, FX)
    assert v["ev_usd_m"] is not None
    mc_usd_m = p["marketCap"] * FX["ZAR"] / 1e6
    assert 2_000 < mc_usd_m < 8_000, mc_usd_m          # a ~USD 4bn company
    assert v["ev_usd_m"] > mc_usd_m * 0.5, v["ev_usd_m"]
    assert v["reporting_ccy"] == "ZAR"


# ── blank on missing proof, never a fallback ─────────────────────────────────

@pytest.mark.parametrize("drop,reason", [
    ("financialCurrency", "no financialCurrency"),
    ("currency", "no quote currency"),
    ("marketCap", "no marketCap"),
    ("totalDebt", "no totalDebt/totalCash"),
    ("totalCash", "no totalDebt/totalCash"),
])
def test_a_missing_input_blanks_ev_and_names_the_reason(drop, reason):
    p = dict(PAYLOADS["TAK"])
    p[drop] = None
    v = b.derive_valuation(p, FX)
    assert v["ev_usd_m"] is None
    assert v["ev_sales"] is None and v["ev_ebitda"] is None
    assert v["reason"] == reason


def test_a_missing_reporting_rate_blanks_rather_than_using_the_quote_rate():
    """⛑ The tempting fallback, and the original defect in one line. TAK quotes
    USD; if the JPY rate is absent, using the quote rate would add yen to dollars
    and produce a confident wrong number."""
    v = b.derive_valuation(PAYLOADS["TAK"], {"USD": 1.0})
    assert v["ev_usd_m"] is None
    assert v["reason"] == "no FX for JPY"


def test_it_never_falls_back_to_the_vendor_ev():
    """Even with a perfectly good vendor EV sitting in the payload."""
    p = dict(PAYLOADS["TAK"])
    p["totalDebt"] = None
    assert p["enterpriseValue"]
    v = b.derive_valuation(p, FX)
    assert v["ev_usd_m"] is None, "fell back to the vendor's mixed-unit EV"


def test_the_reporting_currency_is_reported_even_when_ev_is_blank():
    """`Rpt Ccy` answers 'what currency am I looking at', a separate question
    from whether EV could be computed."""
    p = dict(PAYLOADS["TAK"])
    p["totalDebt"] = None
    v = b.derive_valuation(p, FX)
    assert v["ev_usd_m"] is None and v["reporting_ccy"] == "JPY"


def test_a_non_positive_denominator_blanks_only_the_multiple():
    p = dict(PAYLOADS["TAK"])
    p["ebitda"] = -1.0
    v = b.derive_valuation(p, FX)
    assert v["ev_usd_m"] is not None
    assert v["ev_ebitda"] is None
    assert v["ev_sales"] is not None


# ── the published schema ─────────────────────────────────────────────────────

def test_rpt_ccy_is_appended_never_inserted():
    """The Google Sheet is one =IMPORTDATA cell addressing columns by LETTER.
    Inserting silently re-points every formula to its right."""
    assert b.COLS[-1] == "Rpt Ccy"
    assert b.COLS[-4:-1] == ["Listing", "Exchange", "Country (HQ)"]
    assert "Rpt Ccy" in b.PUBLIC_COLS


# ── the minor-unit quote rule, MEASURED rather than inferred ─────────────────

@pytest.mark.parametrize("t", ["HIK.L", "ONT.L", "APN.JO"])
def test_market_cap_is_in_the_MAJOR_unit_even_when_the_quote_is_minor(t):
    """⛑ The one inference the fix rested on, now a measurement.

    `major_unit(quote)` is applied to `marketCap`, which assumes the vendor
    reports the CAP in pounds/rand while quoting the PRICE in pence/cents. If
    that is backwards the error is a silent 100x -- the same magnitude as the
    Aspen bug this whole line of work started from.

    Measured live 2026-09-08 over every GBp row in the universe (AGY, AVCT,
    CVSG, FUM, HIK, ONT, OXB): `price x sharesOutstanding / marketCap` is
    exactly 100.00 for 7 of 7. So price is minor, cap is major. Pinned on
    recorded payloads so it cannot drift back to an assumption.
    """
    p = PAYLOADS[t]
    assert p["currency"] in ("GBp", "ZAc"), "fixture is no longer a minor-unit quote"
    ratio = (p["regularMarketPrice"] * p["sharesOutstanding"]) / p["marketCap"]
    assert abs(ratio - 100.0) < 0.5, (
        "%s: price x shares / marketCap = %.2f. If this is ~1.0 the cap is in the "
        "MINOR unit and major_unit() must not be applied to the quote rate." % (t, ratio))


def test_a_minor_unit_quote_with_a_FOREIGN_reporting_currency():
    """HIK.L is the third shape: quotes GBp, reports USD. Both legs differ AND
    one is a minor unit, so a single-rate conversion is wrong twice over."""
    p = PAYLOADS["HIK.L"]
    assert p["currency"] == "GBp" and p["financialCurrency"] == "USD"
    v = b.derive_valuation(p, FX)
    assert v["reporting_ccy"] == "USD"
    mc_usd_m = p["marketCap"] * FX["GBP"] / 1e6
    assert 3_500 < mc_usd_m < 5_500, mc_usd_m          # a ~USD 4.5bn company
    # net debt is already USD: it must NOT be scaled by the GBP rate
    net_debt_m = (p["totalDebt"] - p["totalCash"]) / 1e6
    assert abs(v["ev_usd_m"] - (mc_usd_m + net_debt_m)) < 1.0, (
        "EV %.0f != mktcap %.0f + USD net debt %.0f -- the reporting leg was "
        "converted on the quote rate" % (v["ev_usd_m"], mc_usd_m, net_debt_m))
