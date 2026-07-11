"""Tests for thematic basket returns (pure logic; synthetic frame, no pickle)."""
from __future__ import annotations

import math

import pandas as pd

from reporting import thematic_baskets as tb


def _frame():
    return pd.DataFrame({
        "Ticker": ["AAA", "BBB", "CCC"],
        "Mkt Cap": [100.0, 300.0, 200.0],
        "1W": [1.0, 2.0, 3.0],
        "QTD": [5.0, 5.0, 5.0],
        "YTD": [10.0, 20.0, float("nan")],
        "2025": [0.0, 0.0, 0.0],
    })


def test_weighted_equal_and_cap():
    r = pd.Series([10.0, 20.0])
    c = pd.Series([100.0, 300.0])
    ew, cw = tb._weighted(r, c)
    assert ew == 15.0
    # (10*100 + 20*300) / 400 = 17.5
    assert cw == 17.5


def test_weighted_nan_safe():
    r = pd.Series([10.0, float("nan")])
    c = pd.Series([100.0, 200.0])
    ew, cw = tb._weighted(r, c)
    assert ew == 10.0        # NaN return dropped
    assert cw == 10.0        # only the valued name counts


def test_weighted_all_nan_returns_nan():
    ew, cw = tb._weighted(pd.Series([float("nan")]), pd.Series([100.0]))
    assert math.isnan(ew) and math.isnan(cw)


def test_compute_baskets_membership_and_periods(monkeypatch):
    monkeypatch.setattr(tb, "BASKETS", {"Test": ["AAA", "BBB", "ZZZ"]})
    recs = tb.compute_baskets(_frame())
    assert len(recs) == 1
    rec = recs[0]
    assert rec["present"] == ["AAA", "BBB"]
    assert rec["missing"] == ["ZZZ"]     # ZZZ not in the frame
    assert rec["n"] == 2
    # WTD equal-weighted over AAA(1) + BBB(2) = 1.5
    ew, cw = rec["periods"]["WTD"]
    assert ew == 1.5
    # cap-weighted: (1*100 + 2*300)/400 = 1.75
    assert cw == 1.75


def test_render_markdown_has_table_and_membership(monkeypatch):
    monkeypatch.setattr(tb, "BASKETS", {"Test": ["AAA", "ZZZ"]})
    md = tb.render_markdown(tb.compute_baskets(_frame()), "2026-07-10")
    assert "# Thematic stock baskets" in md
    assert "| Test |" in md
    assert "WTD (EW / CW)" in md
    assert "not in universe: ZZZ" in md


def test_baskets_are_nonempty_ticker_lists():
    # Guard the shipped v1 definitions: every basket is a non-empty list of strings.
    for name, tickers in tb.BASKETS.items():
        assert tickers and all(isinstance(t, str) and t for t in tickers), name
