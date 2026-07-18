"""Tests for perf_calcs pure calculation and formatting functions."""

from datetime import date

import pandas as pd
import numpy as np

from reporting.calcs import (
    calc_annual_return, calc_period_return, calc_1d_return, calc_1w_return,
    calc_qtd_return, calc_ytd_return,
    get_color, get_html_color,
    format_mkt_cap, format_price,
    compute_returns, build_result_row,
    RETURN_COLS,
)


def _make_series(prices, start="2024-01-02", freq="B"):
    """Create a price Series with business day index."""
    idx = pd.bdate_range(start=start, periods=len(prices), freq=freq)
    return pd.Series(prices, index=idx)


class TestCalcAnnualReturn:
    def test_normal_year(self):
        # Prior-year close 100 -> year-end 110 = 10% (measured from prior close,
        # NOT the first in-year bar of 105). Fails under the old first-bar basis.
        idx = pd.DatetimeIndex(
            [pd.Timestamp(2023, 12, 29)]
            + list(pd.bdate_range(start="2024-01-02", periods=51))
        )
        prices = [100.0] + [105.0] * 50 + [110.0]
        s = pd.Series(prices, index=idx)
        result = calc_annual_return(s, 2024)
        assert abs(result - 10.0) < 0.1

    def test_measures_from_prior_year_close(self):
        # Prior-year close (Dec 29) anchors the return; the Jan-open gap counts.
        idx = pd.DatetimeIndex([
            pd.Timestamp(2023, 12, 29),  # prior-year close
            pd.Timestamp(2024, 1, 2),    # first in-year bar (gapped up)
            pd.Timestamp(2024, 6, 1),
            pd.Timestamp(2024, 12, 31),  # year-end close
        ])
        s = pd.Series([100.0, 103.0, 108.0, 110.0], index=idx)
        # New basis: 110 / 100 - 1 = 10%. Old first-bar basis: 110 / 103 - 1 ≈ 6.8%.
        assert abs(calc_annual_return(s, 2024) - 10.0) < 0.01

    def test_no_prior_year_bar_returns_none(self):
        # IPO within the year: nothing to anchor on -> None.
        s = _make_series([100.0] + [110.0] * 5, start="2024-01-02")
        assert calc_annual_return(s, 2024) is None

    def test_insufficient_data(self):
        s = _make_series([100.0], start="2024-06-01")
        assert calc_annual_return(s, 2024) is None

    def test_no_data_for_year(self):
        s = _make_series([100.0, 110.0], start="2023-01-02")
        assert calc_annual_return(s, 2025) is None


class TestCalcQtdReturn:
    def test_measures_from_prior_quarter_close(self):
        today = date.today()
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        q_start = pd.Timestamp(today.year, q_start_month, 1)
        idx = pd.DatetimeIndex([
            q_start - pd.Timedelta(days=3),  # prior-quarter close
            q_start + pd.Timedelta(days=1),  # first in-quarter bar (gapped up)
            q_start + pd.Timedelta(days=2),  # latest bar
        ])
        s = pd.Series([100.0, 108.0, 110.0], index=idx)
        # New basis: 110 / 100 - 1 = 10%. Old first-bar basis: 110 / 108 - 1 ≈ 1.85%.
        assert abs(calc_qtd_return(s) - 10.0) < 0.01

    def test_no_prior_quarter_bar_returns_none(self):
        today = date.today()
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        q_start = pd.Timestamp(today.year, q_start_month, 1)
        s = pd.Series([108.0, 110.0], index=pd.DatetimeIndex([
            q_start + pd.Timedelta(days=1),
            q_start + pd.Timedelta(days=2),
        ]))
        assert calc_qtd_return(s) is None


class TestCalcYtdReturn:
    def test_measures_from_prior_year_close(self):
        today = date.today()
        idx = pd.DatetimeIndex([
            pd.Timestamp(today.year - 1, 12, 29),  # prior-year close
            pd.Timestamp(today.year, 1, 2),        # first in-year bar (gapped up)
            pd.Timestamp(today.year, 1, 3),        # latest bar
        ])
        s = pd.Series([100.0, 108.0, 110.0], index=idx)
        # New basis: 110 / 100 - 1 = 10%. Old first-bar basis: 110 / 108 - 1 ≈ 1.85%.
        assert abs(calc_ytd_return(s) - 10.0) < 0.01

    def test_no_prior_year_bar_returns_none(self):
        today = date.today()
        s = pd.Series([108.0, 110.0], index=pd.DatetimeIndex([
            pd.Timestamp(today.year, 1, 2),
            pd.Timestamp(today.year, 1, 3),
        ]))
        assert calc_ytd_return(s) is None


class TestCalcPeriodReturn:
    def test_normal(self):
        prices = list(range(100, 200))
        s = _make_series(prices)
        result = calc_period_return(s, 30)
        assert result is not None
        assert isinstance(result, float)

    def test_empty_hist(self):
        s = pd.Series([], dtype=float)
        assert calc_period_return(s, 30) is None


class TestCalc1dReturn:
    def test_positive(self):
        s = _make_series([100.0, 105.0])
        assert abs(calc_1d_return(s) - 5.0) < 0.01

    def test_negative(self):
        s = _make_series([100.0, 95.0])
        assert abs(calc_1d_return(s) - (-5.0)) < 0.01

    def test_insufficient(self):
        s = _make_series([100.0])
        assert calc_1d_return(s) is None


class TestCalc1wReturn:
    def test_normal(self):
        s = _make_series([100.0, 101.0, 102.0, 103.0, 104.0, 110.0])
        result = calc_1w_return(s)
        assert abs(result - 10.0) < 0.01

    def test_insufficient(self):
        s = _make_series([100.0, 101.0, 102.0])
        assert calc_1w_return(s) is None


class TestGetColor:
    def test_positive_returns_green(self):
        color = get_color(50.0)
        assert color != "FFFFFF"
        # Green channel should be high
        g = int(color[2:4], 16)
        assert g > 200

    def test_negative_returns_red(self):
        color = get_color(-50.0)
        assert color != "FFFFFF"
        # Red channel should be high
        r = int(color[0:2], 16)
        assert r > 200

    def test_zero_returns_white(self):
        assert get_color(0) == "FFFFFF"

    def test_none_returns_white(self):
        assert get_color(None) == "FFFFFF"

    def test_nan_returns_white(self):
        assert get_color(float("nan")) == "FFFFFF"


class TestGetHtmlColor:
    def test_format(self):
        result = get_html_color(10.0)
        assert result.startswith("#")
        assert len(result) == 7


class TestFormatMktCap:
    def test_usd_billions(self):
        result = format_mkt_cap(150e9)
        assert "150" in result

    def test_usd_small(self):
        result = format_mkt_cap(500e6)
        assert "0.5" in result

    def test_values_are_usd(self):
        # Values are pre-converted to USD; currency param is ignored for mkt cap
        result = format_mkt_cap(5e12, "JPY")
        assert "5,000.0" in result

    def test_none(self):
        assert format_mkt_cap(None) == "N/A"

    def test_negative(self):
        result = format_mkt_cap(-1e9)
        assert "-" in result


class TestFormatPrice:
    def test_usd(self):
        assert format_price(150.5) == "150.50"

    def test_large_price(self):
        result = format_price(5000.0)
        assert "5,000" in result

    def test_foreign(self):
        result = format_price(15000.0, "JPY")
        assert "¥" in result

    def test_none(self):
        assert format_price(None) == "N/A"


class TestComputeReturns:
    def test_none_hist(self):
        result = compute_returns(None)
        assert all(v is None for v in result.values())
        assert set(result.keys()) == set(RETURN_COLS)

    def test_empty_hist(self):
        s = pd.Series([], dtype=float)
        result = compute_returns(s)
        assert all(v is None for v in result.values())


class TestBuildResultRow:
    def test_basic(self):
        returns = {col: None for col in RETURN_COLS}
        fund = {"Fwd P/E": 25.0}
        is_ttm = {"Rev Grw": True, "EPS Grw": False}
        row = build_result_row(
            "AAPL", "Apple Inc.", "Tech", "HW", "Technology", "Consumer Electronics",
            "USA", "NASDAQ", returns, fund, is_ttm, "USD"
        )
        assert row["Ticker"] == "AAPL"
        assert row["Company Name"] == "Apple Inc."
        assert row["_currency"] == "USD"
        assert row["_is_ttm_rev"] is True

    def test_nan_cleaning(self):
        returns = {col: None for col in RETURN_COLS}
        fund = {}
        is_ttm = {"Rev Grw": False, "EPS Grw": False}
        row = build_result_row(
            "X", "nan", "nan", "nan", "nan", "nan", "nan", "nan",
            returns, fund, is_ttm, ""
        )
        assert row["Company Name"] == ""
        assert row["Sector (JP)"] == ""
