"""The wrong-company bug class, fourth pass — 2026-08-26.

History, because the shape matters more than any one row:

  efe28e0  2026-07-27  five rows pulling another company's fundamentals
                       (CSL, UCB, Ipsen, Medartis, Medacta). Repaired by
                       correcting Exchange/Country/Currency.
  13f8d01  2026-07-29  `validate_venue_consistency` added; four more found.
                       The FMP profile and SEC title payloads were gated.
  087c274  2026-08-25  ASX (ASE Technology under MedTech/Sleep), ROG (Rogers
                       Corporation where Roche was meant), SHMZF double-count.
  this     2026-08-26  the three mechanisms all of the above left live.

MED and MOVE appear in the FIRST list and were still resolving to Medifast and
Corvex on 2026-08-26 — measured, at USD 135M and USD 256M against CHF 1.10bn and
CHF 2.54bn. The July fix corrected the metadata and left the ticker bare, which
made the rows internally consistent and therefore INVISIBLE to the venue
validator, while every consumer keying on `Ticker` still got the namesake. A fix
that restores the property a check tests, without fixing the defect, silences the
check.

These tests pin the three code paths that made that possible.
"""
import pandas as pd
import pytest

from universe.enrich import _payload_names_match, payload_is_for_this_row
from universe.validation import validate_bare_foreign_tickers


# ── the matcher ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, vendor", [
    ("bioMerieux SA", "bioMérieux S.A."),      # accent
    ("Daetwyler Holding AG", "Dätwyler Holding AG"),  # umlaut -> ae
    ("Muenchener Rueck", "Münchener Rück"),
])
def test_the_same_company_spelled_two_ways_still_matches(stored, vendor):
    """Measured 2026-08-26: these were the ONLY false positives in a 135-row
    identity sweep, and both were the tokenizer turning an accented character
    into a space. `bioMérieux` became {biom, rieux} while `bioMerieux` became
    {biomerieux} — no shared token, so the gate rejected the company's own
    payload. German umlauts need ae/oe/ue, not bare a/o/u: NFKD alone folds
    `Dätwyler` to `Datwyler`, which still does not match the stored
    `Daetwyler`."""
    assert _payload_names_match(stored, vendor) is True


@pytest.mark.parametrize("stored, vendor", [
    ("Medartis Holding AG", "Medifast, Inc."),
    ("Medacta Group SA", "Corvex, Inc."),
    ("CSL Ltd", "Carlisle Companies Incorporated"),
    ("UCB SA", "United Community Banks, Inc."),
    ("Zentek Ltd", "ZEN TECHNOLOGIES LTD"),
])
def test_the_real_collisions_are_still_rejected(stored, vendor):
    """Accent folding must not buy tolerance anywhere else. Every pair here is a
    row that actually shipped wrong."""
    assert _payload_names_match(stored, vendor) is False


def test_a_blank_name_is_unknown_not_agreement():
    """A brand-new row has no Company Name yet, so the comparison cannot be made
    and enrichment must still be allowed to fill it. `None` means unknown."""
    assert _payload_names_match("", "Anything Inc") is None
    assert payload_is_for_this_row("", "Anything Inc", "NEW", "yfinance") is True


# ── the yfinance payload gate ────────────────────────────────────────────────

def test_the_yfinance_payload_is_gated_on_both_write_paths():
    """The gate existed for FMP and SEC and NOT for yfinance, which is the vendor
    every measured wrong-company row came through.

    Source-level, deliberately: both call sites sit inside a live network branch,
    and the property worth pinning is 'no yfinance payload reaches a write without
    passing the gate' rather than any single call's behaviour."""
    import inspect

    from universe import enrich

    for fn in (enrich.enrich_single_ticker, enrich.fetch_yfinance_identifiers):
        src = inspect.getsource(fn)
        assert "t.info" in src or "yt.info" in src, (
            "%s no longer reads a yfinance info payload; re-point this test"
            % fn.__name__)
        assert "payload_is_for_this_row" in src, (
            "%s writes fields from a yfinance payload without identity-gating it. "
            "That is the hole ASX/ROG/MED/MOVE came through." % fn.__name__)


# ── AlphaVantage ─────────────────────────────────────────────────────────────

def test_alphavantage_is_never_asked_about_a_stripped_symbol():
    """`av_symbol = ticker.split(".")[0]` turned MED.SW into MED and merged
    MEDIFAST's market cap, margins and growth into a Medartis row with no
    currency guard.

    This one is worse than the others: it needs no bad data in the universe at
    all. The row's ticker is CORRECT, and the chain corrupts it on the way out,
    so no amount of repairing the universe prevents it."""
    import inspect

    from providers import provider_chain

    # Comments stripped first. The fix carries a comment quoting the old
    # expression verbatim so the next reader knows what it protects against, and
    # a naive substring check matches that explanation and fails on the fix.
    src = inspect.getsource(provider_chain.fetch_fundamentals_with_fallback)
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert 'split(".")[0]' not in code, (
        "the AlphaVantage fallback is stripping the exchange suffix again; that "
        "asks a US-symbol service about a different issuer")


def test_alphavantage_fallback_skips_suffixed_tickers(monkeypatch):
    """Functional half of the above: a suffixed symbol must reach no AV call."""
    from providers import provider_chain

    called = []
    monkeypatch.setattr(provider_chain, "av_fetch",
                        lambda sym, key, use_cache=True: called.append(sym) or {})
    monkeypatch.setattr(provider_chain, "yf_fetch", lambda *a, **k: ({}, {}, ""))
    monkeypatch.setattr(provider_chain, "fmp_fetch", lambda *a, **k: ({}, {}, ""))
    provider_chain.fetch_fundamentals_with_fallback(
        "MED.SW", use_cache=False, _fmp_api_key="x", _av_api_key="y")
    assert called == [], (
        "AlphaVantage was queried for a suffixed foreign symbol: %r" % called)


# ── the cross-check ──────────────────────────────────────────────────────────

def test_the_cross_check_asks_both_vendors_about_the_same_symbol():
    """`_collect_ticker_snapshots` gave yfinance the normalized symbol and FMP the
    RAW one, so for every bare foreign row it compared two different companies and
    reported the gap as provider disagreement. A tool built to find discrepancies
    was manufacturing them."""
    import inspect

    import source_validation

    src = inspect.getsource(source_validation._collect_ticker_snapshots)
    assert "fetch_fmp_fundamentals(yf_ticker" in src, (
        "FMP is being handed a symbol other than the normalized one that yfinance "
        "gets; the two vendors are then describing different issuers")


# ── the offline invariant ────────────────────────────────────────────────────

def _df(rows):
    return pd.DataFrame(rows)


def test_a_bare_foreign_ticker_is_flagged():
    warnings = validate_bare_foreign_tickers(_df([
        {"Ticker": "MED", "Company Name": "Medartis Holding AG",
         "Country (Listing)": "Switzerland", "Exchange": "SIX"},
    ]))
    assert len(warnings) == 1 and "MED" in warnings[0]


def test_an_adr_is_not_flagged():
    """The reason this keys on `Country (Listing)` and not `Country (HQ)`: an ADR
    is a foreign issuer with a US listing and a legitimately bare symbol. A
    domicile filter flags 156 rows, most of them TEVA-shaped; a listing filter
    flags 25 and every historical incident is in them."""
    assert validate_bare_foreign_tickers(_df([
        {"Ticker": "TEVA", "Company Name": "Teva Pharmaceutical Industries Ltd",
         "Country (Listing)": "United States", "Exchange": "NYSE"},
    ])) == []


def test_a_suffixed_foreign_ticker_is_not_flagged():
    assert validate_bare_foreign_tickers(_df([
        {"Ticker": "ROG.SW", "Company Name": "Roche",
         "Country (Listing)": "Switzerland", "Exchange": "SIX"},
        {"Ticker": "SHL GY", "Company Name": "Siemens Healthineers AG",
         "Country (Listing)": "Germany", "Exchange": "XETRA"},
    ])) == []


def test_the_live_universe_flags_the_names_that_have_already_cost_a_repair():
    """Not a fixed count — the universe moves. But every name that has previously
    been repaired for exactly this reason must still be visible in the warning,
    because their tickers are still bare and the exposure is unchanged for any
    downstream consumer resolving `Ticker` raw."""
    from ticker_utils import read_universe_csv

    warnings = validate_bare_foreign_tickers(read_universe_csv())
    assert warnings, "the bare-foreign-ticker invariant matched nothing at all"
    text = warnings[0]
    for ticker in ("MED", "MOVE", "CSL", "UCB", "IPN"):
        assert ticker in text, "%s dropped out of the bare-ticker warning" % ticker
