"""Valuation arithmetic shared by every lane that reads a vendor payload.

⛑ THIS MODULE EXISTS BECAUSE THE RULE LIVED BESIDE ONE CONSUMER. `MINOR_UNITS`
was defined in `scripts/build_hc_coverage_xlsx.py`, so when the coverage books
were fixed on 2026-09-07 the weekly performance report -- a different lane
reading the same vendor -- kept publishing Aspen Pharmacare 100x low for another
day. `derive_valuation` was about to repeat that exactly: written for the
workbook, with `reporting/generate.py` still deriving `Net Debt = EV - marketCap`
on mixed units beside it. A rule that describes a VENDOR belongs beside the
vendor, not beside one of its consumers.

So this is the one implementation. `scripts/build_hc_coverage_xlsx.py` and
`reporting/generate.py` both import from here; neither owns a copy.

THE DEFECT IT ENCODES. Yahoo's `enterpriseValue` is `marketCap` (in the QUOTE
currency) plus `totalDebt - totalCash` (in the REPORTING currency), summed as if
they shared a unit. Measured 2026-09-08: `(EV - marketCap) / (totalDebt -
totalCash)` is 1.000 TAK, 1.004 NVO, 0.990 LLY, 1.053 CYH. For a US row the two
currencies match and the sum is harmless; for an ADR it is a number in no unit
at all, and Takeda published at USD 5.1 trillion.
"""
import math

from providers.fx_provider import MINOR_UNITS, major_unit  # noqa: F401  (re-export)


def num(x):
    """Vendor value -> float, or None for anything that is not a real number.

    ⛑ REJECTS INFINITY, NOT JUST NaN (Codex, High, 2026-09-08). This was
    `None if f != f else f`, which is a NaN test only, so +/-inf passed straight
    through. That mattered because `size_bucket()` is derived from the raw value
    BEFORE any downstream scrub: `marketCap=inf` published a BLANK market-cap
    cell next to `Size = LC`, and -- worse -- it never counted toward the
    partial-book guard's >5% missing-cap threshold, because the guard tests the
    converted cell rather than the input.

    Validating at the boundary is the fix: reject the value where it enters, so
    Size, the ratios, the sort keys and the summary totals all inherit one
    decision instead of each re-deriving from a contaminated input.
    """
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def positive_multiple(raw):
    """A valuation multiple, or None if it is not positive.

    Same rule as `forward_pe` and for the same reason: a negative EV/EBITDA is a
    loss-making denominator showing through the ratio, not a cheap company, and it
    sorts straight to the top of any "cheapest names" ranking.
    """
    v = num(raw)
    return v if (v is not None and v > 0) else None


def _usable_rate(rate):
    """A rate you can actually convert with: a positive, finite number.

    Present-but-garbage is the case that bites. `fx.get(...) is not None` is true
    for 0.0, for a negative, and for NaN, and each one produces a different
    wrong answer rather than a blank.
    """
    if rate is None:
        return False
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return False
    return math.isfinite(r) and r > 0


def derive_valuation(payload, fx):
    """`{ev_usd_m, ev_sales, ev_ebitda, reporting_ccy, reason}` for one row.

    ⛑ THE VENDOR'S `enterpriseValue` IS NOT IN ANY CURRENCY, so it is never
    published. Measured 2026-09-08 across the live payloads: Yahoo's EV is
    `marketCap` (in the QUOTE currency) plus `totalDebt - totalCash` (in the
    REPORTING currency), summed as if they shared a unit --
    `(EV - marketCap) / (totalDebt - totalCash)` is 1.000 for TAK, 1.004 for NVO,
    0.990 for LLY, 1.053 for CYH. For a US row the two currencies are the same and
    the sum is harmless; for an ADR it is a number in no unit at all.

    ⛑ THAT IS WHY TAGGING EV WITH `financialCurrency` AND CONVERTING IS WRONG,
    and it was the first fix proposed. It produces Takeda at USD 33.3bn against a
    true ~USD 91.8bn: a PLAUSIBLE wrong number, which is worse than the absurd
    USD 5.1tn it replaces, because absurd numbers get noticed and plausible ones
    get used.

    So: trust only primitives that each carry ONE known currency, and compute.

        EV      = marketCap x fx(quote)  +  (totalDebt - totalCash) x fx(reporting)
        EV/S    = EV / (totalRevenue x fx(reporting))
        EV/EBITDA = EV / (ebitda x fx(reporting))

    This also repairs a class that has nothing to do with currency: ASML and
    argenx have entirely sane components and only the vendor's DERIVED EV is
    broken (argenx quote and reporting are both USD and its EV was still 25x its
    market cap). Computed: TAK EV 5,134,819 -> 91,798; NVO EV/S 0.9 -> 4.3;
    ASML 1035.7 -> 15.9; ARGX 302.9 -> 11.1; CYH unchanged at 0.9, correctly.

    ⛑ NO FALLBACK TO THE VENDOR EV WHEN A COMPONENT IS MISSING. That would
    reintroduce exactly the undetectable garbage class, and it cannot be screened
    out afterwards: Novo's published EV is 37% high while sitting INSIDE any
    sane EV/market-cap band, and CYH is correct while sitting outside it. A ratio
    test flags the innocent and passes the guilty, so the only honest gate is
    "are this field's own inputs present and convertible" -- `reason` says which
    one was not.

    Known limit, stated because the number should not imply more precision than
    it has: component EV omits minority interest and preferred stock, so it reads
    ~0.5% below Yahoo's own primary-listing EV for TAK and ~2% for NVO. That is
    far inside the error of any spot-FX conversion.
    """
    out = {"ev_usd_m": None, "ev_sales": None, "ev_ebitda": None,
           "reporting_ccy": None, "reason": None}

    quote = (payload.get("currency") or "").strip()
    report = (payload.get("financialCurrency") or "").strip()
    out["reporting_ccy"] = report or None

    mc = num(payload.get("marketCap"))
    debt = num(payload.get("totalDebt"))
    cash = num(payload.get("totalCash"))

    if mc is None or mc <= 0:
        # ⛑ `mc == 0` is NOT a small company, it is a vendor blank wearing a
        # number (Codex, 2026-09-08). It sails past an `is None` check and makes
        # EV collapse to net debt alone: Takeda came out at USD 33,003M -- which
        # is, to the dollar, the same plausible-wrong figure the rejected
        # financialCurrency fix produced. A wrong EV that looks reasonable is the
        # one failure mode this whole function exists to prevent.
        out["reason"] = "no marketCap"; return out
    if not quote:
        out["reason"] = "no quote currency"; return out
    if not report:
        # Absent for some rows in `.info`. That is a blank, never a fallback to
        # the quote currency -- assuming they match is the original defect.
        out["reason"] = "no financialCurrency"; return out
    if debt is None or cash is None:
        out["reason"] = "no totalDebt/totalCash"; return out

    # ⛑ A RATE MUST BE POSITIVE AND FINITE, not merely present (Codex,
    # 2026-09-08). `fetch_aggregate_fx` can hand back 0.0 or a negative for a
    # dead pair, and all three of those failed differently and badly:
    #   0.0 on the reporting leg -> ZeroDivisionError in the EV/Sales
    #     denominator. A CRASH, and it beat the `dead_fx` guard to the punch --
    #     that guard runs AFTER this loop, so it could never report the thing it
    #     exists to report. Ordering made a guard unreachable.
    #   negative on the reporting leg -> a confident USD 25,470M for Takeda with
    #     `reason: None`, i.e. published as though proven.
    #   0.0 on the quote leg -> a near-zero EV, also with no reason.
    # `is None` was the wrong test. "Can I actually convert with this?" is the
    # right one, and it is asked of both legs before either is used.
    q_rate = fx.get(major_unit(quote))
    r_rate = fx.get(major_unit(report))
    if not _usable_rate(q_rate):
        out["reason"] = "no FX for %s" % quote; return out
    if not _usable_rate(r_rate):
        out["reason"] = "no FX for %s" % report; return out

    ev_usd = mc * q_rate + (debt - cash) * r_rate
    if not math.isfinite(ev_usd):
        out["reason"] = "non-finite EV"; return out
    out["ev_usd_m"] = ev_usd / 1e6

    # Belt and braces on the denominator itself. `r_rate` is positive and both
    # figures are checked positive above, so the product should be too -- but a
    # tiny revenue times a tiny rate can underflow to exactly 0.0, and this is a
    # division. Guarding the RESULT (as positive_multiple does) is too late; the
    # exception is raised before it ever sees a value.
    rev = num(payload.get("totalRevenue"))
    if rev is not None and rev > 0 and rev * r_rate > 0:
        out["ev_sales"] = positive_multiple(ev_usd / (rev * r_rate))
    ebitda = num(payload.get("ebitda"))
    if ebitda is not None and ebitda > 0 and ebitda * r_rate > 0:
        out["ev_ebitda"] = positive_multiple(ev_usd / (ebitda * r_rate))
    return out
