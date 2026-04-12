"""Tests for providers/provider_chain.py — fallback coordination and merging."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from reporting.calcs import FUND_COLS, VAL_COLS
from providers.provider_chain import (
    _is_success,
    _merge_partial,
    fetch_fundamentals_with_fallback,
    fetch_all_fundamentals,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _empty_result():
    return {col: None for col in FUND_COLS + VAL_COLS}


def _full_result(**overrides):
    r = _empty_result()
    r.update({"Mkt Cap": 1e9, "Fwd P/E": 25.0, "Gross Mgn": 65.0, "Currency": "USD"})
    r.update(overrides)
    return r


def _partial_result(**overrides):
    """Mkt Cap only — passes Mkt Cap check but not multi-field success."""
    r = _empty_result()
    r.update({"Mkt Cap": 1e9})
    r.update(overrides)
    return r


# ── Unit tests: _is_success ─────────────────────────────────────────────────

class TestIsSuccess:
    def test_full_result_is_success(self):
        assert _is_success(_full_result()) is True

    def test_mkt_cap_only_is_not_success(self):
        r = _empty_result()
        r["Mkt Cap"] = 1e9
        assert _is_success(r) is False

    def test_no_mkt_cap_is_not_success(self):
        r = _empty_result()
        r["Fwd P/E"] = 25.0
        assert _is_success(r) is False

    def test_mkt_cap_plus_one_quality_is_success(self):
        r = _empty_result()
        r["Mkt Cap"] = 1e9
        r["ROE"] = 15.0
        assert _is_success(r) is True


# ── Unit tests: _merge_partial ──────────────────────────────────────────────

class TestMergePartial:
    def test_fills_none_slots(self):
        base = {"Mkt Cap": 1e9, "Fwd P/E": None, "ROE": 15.0}
        overlay = {"Mkt Cap": 2e9, "Fwd P/E": 25.0, "EV/EBITDA": 12.0}
        result = _merge_partial(base, overlay)
        # Mkt Cap not overwritten (already non-None)
        assert result["Mkt Cap"] == 1e9
        # Fwd P/E filled from overlay
        assert result["Fwd P/E"] == 25.0
        # ROE preserved
        assert result["ROE"] == 15.0
        # EV/EBITDA added
        assert result["EV/EBITDA"] == 12.0

    def test_overlay_none_does_not_clobber(self):
        base = {"Mkt Cap": 1e9}
        overlay = {"Mkt Cap": None}
        result = _merge_partial(base, overlay)
        assert result["Mkt Cap"] == 1e9


# ── Unit tests: fetch_fundamentals_with_fallback ────────────────────────────

class TestFetchWithFallback:
    """Test the fallback chain logic with mocked providers."""

    @patch("providers.provider_chain.av_fetch")
    @patch("providers.provider_chain.yf_fetch")
    @patch("providers.provider_chain.fmp_fetch")
    def test_fmp_first_success_skips_yfinance(self, mock_fmp, mock_yf, mock_av):
        """When FMP succeeds, yfinance and AV are not called."""
        mock_fmp.return_value = (_full_result(), {"Rev Grw": False, "EPS Grw": False}, "USD")

        result, is_ttm, currency, provider = fetch_fundamentals_with_fallback(
            "ISRG", priority="fmp_first", _fmp_api_key="k", _av_api_key=""
        )
        assert provider == "fmp"
        assert result["Mkt Cap"] == 1e9
        mock_yf.assert_not_called()
        mock_av.assert_not_called()

    @patch("providers.provider_chain.av_fetch")
    @patch("providers.provider_chain.yf_fetch")
    @patch("providers.provider_chain.fmp_fetch")
    def test_fmp_failure_falls_back_to_yfinance(self, mock_fmp, mock_yf, mock_av):
        """When FMP returns empty, yfinance is tried."""
        mock_fmp.return_value = (_empty_result(), {"Rev Grw": False, "EPS Grw": False}, "")
        mock_yf.return_value = (_full_result(), {"Rev Grw": False, "EPS Grw": False}, "USD")

        result, is_ttm, currency, provider = fetch_fundamentals_with_fallback(
            "ISRG", priority="fmp_first", _fmp_api_key="k", _av_api_key=""
        )
        assert provider == "yfinance"
        assert result["Mkt Cap"] == 1e9

    @patch("providers.provider_chain.av_fetch")
    @patch("providers.provider_chain.yf_fetch")
    @patch("providers.provider_chain.fmp_fetch")
    def test_both_fail_falls_back_to_av(self, mock_fmp, mock_yf, mock_av):
        """When both FMP and yfinance fail, AlphaVantage is tried."""
        mock_fmp.return_value = (_empty_result(), {"Rev Grw": False, "EPS Grw": False}, "")
        mock_yf.return_value = (_empty_result(), {"Rev Grw": False, "EPS Grw": False}, "")
        mock_av.return_value = {"Mkt Cap": 5e8, "Fwd P/E": 20.0, "ROE": 10.0}

        result, is_ttm, currency, provider = fetch_fundamentals_with_fallback(
            "ISRG", priority="fmp_first", _fmp_api_key="k", _av_api_key="avk"
        )
        assert provider == "alphavantage"
        assert result["Mkt Cap"] == 5e8

    @patch("providers.provider_chain.av_fetch")
    @patch("providers.provider_chain.yf_fetch")
    @patch("providers.provider_chain.fmp_fetch")
    def test_partial_fmp_merged_with_yfinance(self, mock_fmp, mock_yf, mock_av):
        """FMP partial (Mkt Cap only) + yfinance partial = merged result."""
        # FMP has Mkt Cap only — NOT success (needs Mkt Cap + quality field)
        fmp_partial = _partial_result()  # Mkt Cap only
        yf_partial = _partial_result(**{"Fwd P/E": 25.0, "ROE": 18.0})

        mock_fmp.return_value = (fmp_partial, {"Rev Grw": False, "EPS Grw": False}, "USD")
        mock_yf.return_value = (yf_partial, {"Rev Grw": False, "EPS Grw": False}, "USD")

        result, is_ttm, currency, provider = fetch_fundamentals_with_fallback(
            "ISRG", priority="fmp_first", _fmp_api_key="k", _av_api_key=""
        )
        # FMP was primary (Mkt Cap only, not success) — yfinance merged in
        assert result["Mkt Cap"] == 1e9
        assert result["Fwd P/E"] == 25.0
        assert result["ROE"] == 18.0
        assert provider == "fmp"

    @patch("providers.provider_chain.av_fetch")
    @patch("providers.provider_chain.yf_fetch")
    @patch("providers.provider_chain.fmp_fetch")
    def test_yf_first_priority(self, mock_fmp, mock_yf, mock_av):
        """yf_first priority tries yfinance before FMP."""
        mock_yf.return_value = (_full_result(), {"Rev Grw": False, "EPS Grw": False}, "USD")

        result, _, _, provider = fetch_fundamentals_with_fallback(
            "ISRG", priority="yf_first", _fmp_api_key="k", _av_api_key=""
        )
        assert provider == "yfinance"
        mock_fmp.assert_not_called()

    @patch("providers.provider_chain.av_fetch")
    @patch("providers.provider_chain.yf_fetch")
    @patch("providers.provider_chain.fmp_fetch")
    def test_finnhub_override_always_wins(self, mock_fmp, mock_yf, mock_av):
        """Finnhub TTM data overrides growth fields regardless of provider."""
        fmp_result = _full_result(**{"Rev Grw": 10.0, "EPS Grw": 12.0, "PEG": 2.5})
        mock_fmp.return_value = (fmp_result, {"Rev Grw": False, "EPS Grw": False}, "USD")

        finnhub = {"revenueGrowthTTMYoy": 22.5, "epsGrowthTTMYoy": 30.0, "pegTTM": 1.8}

        result, is_ttm, _, provider = fetch_fundamentals_with_fallback(
            "ISRG", finnhub_metrics=finnhub, priority="fmp_first",
            _fmp_api_key="k", _av_api_key=""
        )
        assert result["Rev Grw"] == 22.5
        assert result["EPS Grw"] == 30.0
        assert result["PEG"] == 1.8
        assert is_ttm["Rev Grw"] is True
        assert is_ttm["EPS Grw"] is True

    @patch("providers.provider_chain.av_fetch")
    @patch("providers.provider_chain.yf_fetch")
    @patch("providers.provider_chain.fmp_fetch")
    def test_currency_preserved_through_merge(self, mock_fmp, mock_yf, mock_av):
        """Currency from primary provider is preserved even after merge."""
        fmp_partial = _partial_result()
        mock_fmp.return_value = (fmp_partial, {"Rev Grw": False, "EPS Grw": False}, "GBP")
        mock_yf.return_value = (_full_result(), {"Rev Grw": False, "EPS Grw": False}, "USD")

        result, _, currency, _ = fetch_fundamentals_with_fallback(
            "AZN.L", priority="fmp_first", _fmp_api_key="k", _av_api_key=""
        )
        # FMP was the primary source, its currency is preserved
        assert currency == "GBP"

    @patch("providers.provider_chain.av_fetch")
    @patch("providers.provider_chain.yf_fetch")
    @patch("providers.provider_chain.fmp_fetch")
    def test_output_shape_matches_contract(self, mock_fmp, mock_yf, mock_av):
        """Result dict has all expected FUND_COLS + VAL_COLS keys."""
        mock_fmp.return_value = (_full_result(), {"Rev Grw": False, "EPS Grw": False}, "USD")

        result, is_ttm, currency, _ = fetch_fundamentals_with_fallback(
            "ISRG", priority="fmp_first", _fmp_api_key="k", _av_api_key=""
        )
        for col in FUND_COLS + VAL_COLS:
            assert col in result, f"Missing key: {col}"
        assert "Rev Grw" in is_ttm
        assert "EPS Grw" in is_ttm


# ── Unit tests: fetch_all_fundamentals ──────────────────────────────────────

class TestFetchAllFundamentals:
    @patch("providers.provider_chain.fetch_fundamentals_with_fallback")
    def test_returns_three_dicts(self, mock_fetch):
        mock_fetch.return_value = (
            _full_result(), {"Rev Grw": False, "EPS Grw": False}, "USD", "fmp"
        )

        all_fund, all_ttm, all_cur = fetch_all_fundamentals(
            ["ISRG", "ABBV"], priority="fmp_first"
        )
        assert "ISRG" in all_fund
        assert "ABBV" in all_fund
        assert all_cur["ISRG"] == "USD"
        assert len(all_fund) == 2

    @patch("providers.provider_chain.fetch_fundamentals_with_fallback")
    def test_handles_exception_gracefully(self, mock_fetch):
        mock_fetch.side_effect = Exception("network error")

        all_fund, all_ttm, all_cur = fetch_all_fundamentals(
            ["ISRG"], priority="fmp_first"
        )
        # Should not raise; ticker gets empty result
        assert "ISRG" in all_fund
        assert all_fund["ISRG"]["Mkt Cap"] is None


# ── Config flag test ────────────────────────────────────────────────────────

class TestConfigFlag:
    @patch("providers.provider_chain.av_fetch")
    @patch("providers.provider_chain.yf_fetch")
    @patch("providers.provider_chain.fmp_fetch")
    def test_priority_param_overrides_config(self, mock_fmp, mock_yf, mock_av):
        """The priority parameter overrides the global PROVIDER_PRIORITY."""
        mock_fmp.return_value = (_full_result(), {"Rev Grw": False, "EPS Grw": False}, "USD")
        mock_yf.return_value = (_full_result(), {"Rev Grw": False, "EPS Grw": False}, "USD")

        # Even if config says yf_first, passing fmp_first should use FMP
        result, _, _, provider = fetch_fundamentals_with_fallback(
            "ISRG", priority="fmp_first", _fmp_api_key="k", _av_api_key=""
        )
        assert provider == "fmp"
        mock_yf.assert_not_called()
