"""Cash-return metrics (FCF yield / ROIC / CFO margin) — providers/fmp_quality.py.

Every fixture value below was captured live from FMP on 2026-09-22, including the Sanofi
disagreement that motivated the corroboration step.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers import fmp_quality as q

# MRK, live 2026-09-22. evToSales/evToOperatingCashFlow = 6.36/21.14 = 30.1% CFO margin,
# which equals the sum of its four quarterly statements over the same period exactly.
MRK_ROW = {
    "freeCashFlowYieldTTM": 0.0428, "returnOnInvestedCapitalTTM": 0.0562,
    "investedCapitalTTM": 81245000000.0, "evToOperatingCashFlowTTM": 21.14,
    "evToSalesTTM": 6.36, "marketCap": 250000000000.0,
}


def _patch(monkeypatch, row, fcf_sum=None, calls=None):
    monkeypatch.setattr(q, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(q, "cache_set", lambda *a, **k: (calls or {}).setdefault("set", []).append(a))
    monkeypatch.setattr(q, "_fetch_key_metrics_ttm", lambda t, k: (row, False, False))
    monkeypatch.setattr(q, "_fetch_quarterly_fcf", lambda t, k, limit=4: (fcf_sum, False))


def test_the_three_numbers_come_off_one_payload(monkeypatch):
    _patch(monkeypatch, MRK_ROW)
    p = q.fetch_quality("MRK", "key")
    assert p["status"] == q.STATUS_OK
    assert p["fcf_yield"] == pytest.approx(0.0428)
    assert p["roic"] == pytest.approx(0.0562)
    assert p["cfo_margin"] == pytest.approx(6.36 / 21.14)


def test_cfo_margin_keeps_the_SIGN_of_operating_cash_flow(monkeypatch):
    """MRNA lives at evToOperatingCashFlow -68.45 / evToSales 32.32 = -47.2%, and the whole
    point of board #430 is finding names that BURN cash. A sign lost here loses the screen."""
    _patch(monkeypatch, {**MRK_ROW, "evToOperatingCashFlowTTM": -68.45, "evToSalesTTM": 32.32})
    assert q.fetch_quality("MRNA", "key")["cfo_margin"] == pytest.approx(-0.4722, abs=1e-4)


def test_an_exact_zero_yield_is_MISSING_not_zero(monkeypatch):
    """FMP encodes no-data as a literal 0 elsewhere (`sp500_valuation`'s epsAvg). A screen
    for negative FCF must not read 12 no-data names as 'cash flow is exactly zero'."""
    _patch(monkeypatch, {**MRK_ROW, "freeCashFlowYieldTTM": 0.0})
    assert q.fetch_quality("X", "key")["fcf_yield"] is None


def test_roic_is_WITHHELD_when_invested_capital_is_not_positive(monkeypatch):
    """A negative denominator turns a loss into a flattering percentage. The guard lives at
    the one place the number is made, so no consumer can reach the unguarded value."""
    _patch(monkeypatch, {**MRK_ROW, "investedCapitalTTM": -500.0,
                         "returnOnInvestedCapitalTTM": 0.42})
    p = q.fetch_quality("X", "key")
    assert p["roic"] is None and p["ic_nonpositive"] is True
    assert "invested capital" in q._status_cell(p)


def test_a_candidate_whose_statements_DISAGREE_is_flagged(monkeypatch):
    """Sanofi, live: 18.45% headline against 13.18% from its own four quarterly statements."""
    row = {**MRK_ROW, "freeCashFlowYieldTTM": 0.1845, "marketCap": 89312634000.0}
    _patch(monkeypatch, row, fcf_sum=0.1318 * 89312634000.0)
    p = q.fetch_quality("SNY", "key")
    assert p["check"] == q.CHECK_UNRECONCILED
    assert q.quality_columns_from_payload(p)["Cash Flow Status"] == "unreconciled"


def test_a_candidate_whose_statements_AGREE_says_corroborated(monkeypatch):
    row = {**MRK_ROW, "freeCashFlowYieldTTM": 0.1707, "marketCap": 1000.0}
    _patch(monkeypatch, row, fcf_sum=0.1693 * 1000.0)     # FMS, a 1% gap
    p = q.fetch_quality("FMS", "key")
    assert p["check"] == q.CHECK_OK
    assert q.quality_columns_from_payload(p)["Cash Flow Status"] == "ok (corroborated)"


def test_a_name_far_from_the_bar_is_not_checked_and_does_not_CLAIM_to_be(monkeypatch):
    """One extra call per candidate is cheap; one per universe row is not. The cell must not
    say 'corroborated' for a name nothing corroborated."""
    called = []
    _patch(monkeypatch, MRK_ROW)
    monkeypatch.setattr(q, "_fetch_quarterly_fcf",
                        lambda *a, **k: (called.append(1), (1.0, False))[1])
    p = q.fetch_quality("MRK", "key")
    assert called == [] and p["check"] == q.CHECK_NOT_CHECKED
    assert q.quality_columns_from_payload(p)["Cash Flow Status"] == "ok"


def test_a_partial_year_of_statements_cannot_corroborate(monkeypatch):
    """Three quarters summed against a TTM headline manufactures a disagreement."""
    monkeypatch.setattr(q, "_fmp_request", lambda url, want_status=False: (
        [{"freeCashFlow": 1.0}, {"freeCashFlow": 2.0}], 200))
    total, errored = q._fetch_quarterly_fcf("X", "key")
    assert total is None and errored is False


def test_an_error_is_NEVER_cached_but_no_data_is(monkeypatch):
    """A transient failure frozen into the cache is a column blank for its whole TTL."""
    writes = []
    monkeypatch.setattr(q, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(q, "cache_set", lambda *a, **k: writes.append(a[1]))
    monkeypatch.setattr(q, "_fetch_key_metrics_ttm", lambda t, k: ({}, True, False))
    assert q.fetch_quality("X", "key")["status"] == q.STATUS_ERROR
    assert writes == []
    monkeypatch.setattr(q, "_fetch_key_metrics_ttm", lambda t, k: ({}, False, False))
    assert q.fetch_quality("X", "key")["status"] == q.STATUS_NO_DATA
    assert writes == ["X"]


def test_a_gated_endpoint_is_its_own_state(monkeypatch):
    monkeypatch.setattr(q, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(q, "cache_set", lambda *a, **k: None)
    monkeypatch.setattr(q, "_fetch_key_metrics_ttm", lambda t, k: ({}, False, True))
    assert q.fetch_quality("X", "key")["status"] == q.STATUS_GATED


def test_no_key_and_cache_only_make_no_call(monkeypatch):
    monkeypatch.setattr(q, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(q, "_fetch_key_metrics_ttm",
                        lambda t, k: pytest.fail("must not call the vendor"))
    assert q.fetch_quality("X", "")["status"] == q.STATUS_NOT_ATTEMPTED
    assert q.fetch_quality("X", "key", cache_only=True)["status"] == q.STATUS_NOT_ATTEMPTED


def test_an_absent_payload_renders_not_attempted_not_a_blank():
    cols = q.quality_columns_from_payload(None)
    assert cols["Cash Flow Status"] == q.STATUS_NOT_ATTEMPTED
    assert cols["FCF Yield"] is None and cols["ROIC"] is None


def test_columns_are_PERCENTAGES_matching_their_neighbours(monkeypatch):
    """`ROE (TTM)` and `Gross Mgn (TTM)` are percentages in this report. A fraction under a
    heading whose neighbours are percentages reads as 0%."""
    _patch(monkeypatch, MRK_ROW)
    cols = q.quality_columns_from_payload(q.fetch_quality("MRK", "key"))
    assert cols["FCF Yield"] == pytest.approx(4.28, abs=0.01)
    assert cols["ROIC"] == pytest.approx(5.62, abs=0.01)


def test_the_report_column_names_match_the_calcs_constant():
    """A key this module emits that `QUALITY_COLS` does not list becomes an unwritten column;
    the reverse becomes a column of None. Neither raises."""
    from reporting.calcs import QUALITY_COLS
    assert set(q.quality_columns_from_payload(None)) == set(QUALITY_COLS)
