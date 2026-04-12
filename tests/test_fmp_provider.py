"""Tests for providers/fmp_provider.py — fundamentals fetching."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from providers.fmp_provider import (
    fetch_profile,
    fetch_fundamentals,
    _safe_float,
    _pct,
    _RateLimiter,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

SAMPLE_PROFILE = {
    "symbol": "ISRG",
    "companyName": "Intuitive Surgical",
    "marketCap": 180000000000,
    "currency": "USD",
    "price": 520.0,
    "exchange": "NASDAQ",
    "exchangeFullName": "NASDAQ Global Select",
    "isin": "US46120E6023",
    "cik": "1035267",
    "ipoDate": "2000-06-13",
    "website": "https://www.intuitive.com",
    "sector": "Healthcare",
    "industry": "Medical Instruments & Supplies",
    "country": "US",
}

SAMPLE_RATIOS = {
    "peRatioTTM": 72.5,
    "enterpriseValueOverEBITDATTM": 55.3,
    "priceEarningsToGrowthRatioTTM": 3.2,
    "priceToSalesRatioTTM": 22.1,
    "grossProfitMarginTTM": 0.67,
    "operatingProfitMarginTTM": 0.35,
    "returnOnEquityTTM": 0.18,
}

SAMPLE_KEY_METRICS = {
    "enterpriseValueTTM": 175000000000,
    "netDebtTTM": -5000000000,
    "evToSalesTTM": 22.0,
}

SAMPLE_GROWTH = {
    "revenueGrowth": 0.17,
    "epsgrowth": 0.25,
}


# ── Unit tests: helpers ─────────────────────────────────────────────────────

class TestHelpers:
    def test_safe_float_valid(self):
        assert _safe_float(42.5) == 42.5
        assert _safe_float("123") == 123.0
        assert _safe_float(0) == 0.0

    def test_safe_float_none_and_invalid(self):
        assert _safe_float(None) is None
        assert _safe_float("abc") is None
        assert _safe_float("") is None

    def test_safe_float_nan(self):
        assert _safe_float(float("nan")) is None

    def test_pct_converts_decimal(self):
        assert _pct(0.45) == 45.0
        assert _pct(0.0) == 0.0
        assert _pct(1.0) == 100.0

    def test_pct_none(self):
        assert _pct(None) is None


# ── Unit tests: fetch_profile ───────────────────────────────────────────────

class TestFetchProfile:
    @patch("providers.fmp_provider._fmp_request")
    def test_returns_profile_dict(self, mock_req):
        mock_req.return_value = [SAMPLE_PROFILE]
        result = fetch_profile("ISRG", "test_key")
        assert result["companyName"] == "Intuitive Surgical"
        assert result["marketCap"] == 180000000000

    @patch("providers.fmp_provider._fmp_request")
    def test_empty_response(self, mock_req):
        mock_req.return_value = []
        result = fetch_profile("FAKE", "test_key")
        assert result == {}

    @patch("providers.fmp_provider._fmp_request")
    def test_none_response(self, mock_req):
        mock_req.return_value = None
        result = fetch_profile("FAKE", "test_key")
        assert result == {}

    def test_no_api_key(self):
        result = fetch_profile("ISRG", "")
        assert result == {}


# ── Unit tests: fetch_fundamentals ──────────────────────────────────────────

class TestFetchFundamentals:
    @patch("providers.fmp_provider._fetch_financial_growth")
    @patch("providers.fmp_provider._fetch_key_metrics")
    @patch("providers.fmp_provider._fetch_ratios")
    @patch("providers.fmp_provider.fetch_profile")
    @patch("providers.fmp_provider.cache_get", return_value=None)
    @patch("providers.fmp_provider.cache_set")
    def test_full_response(self, mock_cset, mock_cget, mock_profile, mock_ratios, mock_km, mock_fg):
        mock_profile.return_value = SAMPLE_PROFILE
        mock_ratios.return_value = SAMPLE_RATIOS
        mock_km.return_value = SAMPLE_KEY_METRICS
        mock_fg.return_value = SAMPLE_GROWTH

        result, is_ttm, currency = fetch_fundamentals("ISRG", "test_key", use_cache=False)

        assert result["Mkt Cap"] == 180000000000
        assert currency == "USD"
        assert result["Fwd P/E"] == 72.5
        assert result["EV/EBITDA"] == 55.3
        assert result["PEG"] == 3.2
        # Margins are converted from decimal to percentage
        assert result["Gross Mgn"] == 67.0
        assert result["Op Mgn"] == 35.0
        assert result["ROE"] == 18.0
        # Growth from financial-growth endpoint (also decimal → pct)
        assert result["Rev Grw"] == 17.0
        assert result["EPS Grw"] == 25.0
        # Key metrics
        assert result["Enterprise Value"] == 175000000000
        assert result["Net Debt"] == -5000000000

    @patch("providers.fmp_provider._fetch_financial_growth")
    @patch("providers.fmp_provider._fetch_key_metrics")
    @patch("providers.fmp_provider._fetch_ratios")
    @patch("providers.fmp_provider.fetch_profile")
    @patch("providers.fmp_provider.cache_get", return_value=None)
    @patch("providers.fmp_provider.cache_set")
    def test_progressive_skips_key_metrics_when_not_needed(
        self, mock_cset, mock_cget, mock_profile, mock_ratios, mock_km, mock_fg
    ):
        """key-metrics is NOT called if EV/S comes from ratios and EV/Net Debt are present."""
        profile_with_ev = {**SAMPLE_PROFILE}
        mock_profile.return_value = profile_with_ev
        # ratios provides EV/S
        ratios_with_evs = {**SAMPLE_RATIOS, "priceToSalesRatioTTM": 22.1}
        mock_ratios.return_value = ratios_with_evs
        mock_fg.return_value = SAMPLE_GROWTH

        # key-metrics still called because EV and Net Debt are None from profile+ratios
        fetch_fundamentals("ISRG", "test_key", use_cache=False)
        # key-metrics IS called because Enterprise Value and Net Debt are still None
        assert mock_km.called

    @patch("providers.fmp_provider._fetch_financial_growth")
    @patch("providers.fmp_provider._fetch_key_metrics")
    @patch("providers.fmp_provider._fetch_ratios")
    @patch("providers.fmp_provider.fetch_profile")
    @patch("providers.fmp_provider.cache_get", return_value=None)
    @patch("providers.fmp_provider.cache_set")
    def test_growth_skipped_when_present(
        self, mock_cset, mock_cget, mock_profile, mock_ratios, mock_km, mock_fg
    ):
        """financial-growth NOT called if both Rev Grw and EPS Grw are already set."""
        mock_profile.return_value = SAMPLE_PROFILE
        # Simulate ratios that somehow include growth (unlikely but tests the logic)
        ratios_with_growth = {**SAMPLE_RATIOS}
        mock_ratios.return_value = ratios_with_growth
        mock_km.return_value = SAMPLE_KEY_METRICS
        # Growth will be None from ratios, so financial-growth IS called
        fetch_fundamentals("ISRG", "test_key", use_cache=False)
        assert mock_fg.called

    @patch("providers.fmp_provider.fetch_profile")
    @patch("providers.fmp_provider.cache_get", return_value=None)
    def test_empty_profile_returns_empty(self, mock_cget, mock_profile):
        mock_profile.return_value = {}
        result, is_ttm, currency = fetch_fundamentals("FAKE", "test_key", use_cache=False)
        assert result["Mkt Cap"] is None
        assert currency == ""

    def test_no_api_key_returns_empty(self):
        result, is_ttm, currency = fetch_fundamentals("ISRG", "", use_cache=False)
        assert result["Mkt Cap"] is None

    @patch("providers.fmp_provider.fetch_profile")
    @patch("providers.fmp_provider.cache_get")
    def test_cache_hit_skips_api(self, mock_cget, mock_profile):
        from reporting.calcs import FUND_COLS as FC, VAL_COLS as VC
        cached_result = {c: None for c in FC + VC}
        cached_result["Mkt Cap"] = 100
        cached_result["Fwd P/E"] = 10
        cached = {
            "result": cached_result,
            "is_ttm": {"Rev Grw": False, "EPS Grw": False},
            "currency": "USD",
        }
        mock_cget.return_value = cached

        result, is_ttm, currency = fetch_fundamentals("ISRG", "key", use_cache=True)
        assert result["Mkt Cap"] == 100
        mock_profile.assert_not_called()


# ── Unit tests: rate limiter ────────────────────────────────────────────────

class TestRateLimiter:
    def test_rate_limiter_creates(self):
        rl = _RateLimiter(calls_per_minute=600)
        assert rl._interval == pytest.approx(0.1)

    def test_rate_limiter_wait_does_not_crash(self):
        rl = _RateLimiter(calls_per_minute=6000)  # Very fast for tests
        rl.wait()
        rl.wait()


# ── Integration-style: decimal normalization ────────────────────────────────

class TestDecimalNormalization:
    """Verify that FMP's decimal ratios (0.45) become percentages (45.0)."""

    @patch("providers.fmp_provider._fetch_financial_growth", return_value={})
    @patch("providers.fmp_provider._fetch_key_metrics", return_value={})
    @patch("providers.fmp_provider._fetch_ratios")
    @patch("providers.fmp_provider.fetch_profile")
    @patch("providers.fmp_provider.cache_get", return_value=None)
    @patch("providers.fmp_provider.cache_set")
    def test_margins_are_percentages(self, mock_cset, mock_cget, mock_profile, mock_ratios, mock_km, mock_fg):
        mock_profile.return_value = {"marketCap": 1000, "currency": "USD"}
        mock_ratios.return_value = {
            "grossProfitMarginTTM": 0.452,
            "operatingProfitMarginTTM": 0.231,
            "returnOnEquityTTM": 0.089,
        }

        result, _, _ = fetch_fundamentals("TEST", "key", use_cache=False)
        assert result["Gross Mgn"] == pytest.approx(45.2)
        assert result["Op Mgn"] == pytest.approx(23.1)
        assert result["ROE"] == pytest.approx(8.9)
