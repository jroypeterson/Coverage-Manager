"""Tests for providers/fmp_history.py — historical valuation fetching."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.fmp_history import (
    fetch_history,
    _fetch_ratios_annual,
    _fetch_key_metrics_annual,
    _pad,
)


SAMPLE_RATIOS_ANNUAL = [
    {"date": "2025-12-31", "priceToEarningsRatio": 65.3, "evToSales": 18.2},
    {"date": "2024-12-31", "priceToEarningsRatio": 62.1, "evToSales": 17.5},
    {"date": "2023-12-31", "priceToEarningsRatio": 58.9, "evToSales": 16.8},
    {"date": "2022-12-31", "priceToEarningsRatio": None, "evToSales": 15.9},
    {"date": "2021-12-31", "priceToEarningsRatio": 71.2, "evToSales": 14.0},
]

SAMPLE_KEY_METRICS_ANNUAL = [
    {"date": "2025-12-31", "evToSales": 18.2},
    {"date": "2024-12-31", "evToSales": 17.5},
    {"date": "2023-12-31", "evToSales": 16.8},
    {"date": "2022-12-31", "evToSales": 15.9},
    {"date": "2021-12-31", "evToSales": 14.0},
]

SAMPLE_RATIOS_TTM = {
    "priceToEarningsRatioTTM": 72.5,
    "priceToSalesRatioTTM": 22.1,
}


class TestPad:
    def test_pads_to_length(self):
        assert _pad([1, 2], 5) == [1, 2, None, None, None]

    def test_truncates_longer(self):
        assert _pad([1, 2, 3, 4, 5, 6], 3) == [1, 2, 3]

    def test_exact_length(self):
        assert _pad([1, 2, 3], 3) == [1, 2, 3]

    def test_empty(self):
        assert _pad([], 3) == [None, None, None]

    def test_custom_fill(self):
        assert _pad(["a"], 3, fill="") == ["a", "", ""]


class TestFetchRatiosAnnual:
    @patch("providers.fmp_history._fmp_request")
    def test_returns_list(self, mock_req):
        mock_req.return_value = SAMPLE_RATIOS_ANNUAL
        result = _fetch_ratios_annual("ISRG", "key")
        assert len(result) == 5
        assert result[0]["priceToEarningsRatio"] == 65.3

    @patch("providers.fmp_history._fmp_request")
    def test_handles_empty_response(self, mock_req):
        mock_req.return_value = []
        result = _fetch_ratios_annual("XYZ", "key")
        assert result == []

    @patch("providers.fmp_history._fmp_request")
    def test_handles_none_response(self, mock_req):
        mock_req.return_value = None
        result = _fetch_ratios_annual("XYZ", "key")
        assert result == []

    @patch("providers.fmp_history._fmp_request")
    def test_handles_non_list_response(self, mock_req):
        mock_req.return_value = {"error": "bad"}
        result = _fetch_ratios_annual("XYZ", "key")
        assert result == []


class TestFetchHistory:
    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history._fmp_request")
    def test_combines_endpoints(self, mock_req, _set, _get):
        # _fmp_request is called 3 times: ratios annual, key-metrics annual, ratios-ttm
        mock_req.side_effect = [
            SAMPLE_RATIOS_ANNUAL,
            SAMPLE_KEY_METRICS_ANNUAL,
            [SAMPLE_RATIOS_TTM],
        ]
        result = fetch_history("ISRG", "key", use_cache=True)

        assert result["pe_ttm"] == 72.5
        assert result["pe_history"] == [65.3, 62.1, 58.9, None, 71.2]
        assert result["evs_history"] == [18.2, 17.5, 16.8, 15.9, 14.0]
        assert result["record_dates"][0] == "2025-12-31"
        assert len(result["record_dates"]) == 5

    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history._fmp_request")
    def test_pads_short_history(self, mock_req, _set, _get):
        # Newer ticker — only 2 years available
        mock_req.side_effect = [
            SAMPLE_RATIOS_ANNUAL[:2],
            SAMPLE_KEY_METRICS_ANNUAL[:2],
            [SAMPLE_RATIOS_TTM],
        ]
        result = fetch_history("NEWCO", "key", use_cache=True)

        assert result["pe_history"] == [65.3, 62.1, None, None, None]
        assert result["evs_history"] == [18.2, 17.5, None, None, None]
        # record_dates pads with empty string, not None
        assert result["record_dates"] == ["2025-12-31", "2024-12-31", "", "", ""]

    def test_no_api_key_returns_empty(self):
        result = fetch_history("ISRG", api_key=None, use_cache=False)
        assert result["pe_ttm"] is None
        assert result["pe_history"] == [None] * 5
        assert result["evs_history"] == [None] * 5

    @patch("providers.fmp_history.cache_get")
    def test_uses_cache(self, mock_get):
        mock_get.return_value = {
            "pe_ttm": 72.5,
            "pe_history": [65.3, 62.1, 58.9, 55.0, 71.2],
            "evs_history": [18.2, 17.5, 16.8, 15.9, 14.0],
            "record_dates": ["2025-12-31"] + [""] * 4,
        }
        result = fetch_history("ISRG", "key", use_cache=True)
        assert result["pe_ttm"] == 72.5
        # _fmp_request should not have been called
        mock_get.assert_called_once()

    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history._fmp_request")
    def test_caches_when_data_present(self, mock_req, mock_set, _get):
        mock_req.side_effect = [
            SAMPLE_RATIOS_ANNUAL,
            SAMPLE_KEY_METRICS_ANNUAL,
            [SAMPLE_RATIOS_TTM],
        ]
        fetch_history("ISRG", "key", use_cache=True)
        mock_set.assert_called_once()

    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history._fmp_request")
    def test_does_not_cache_empty_results(self, mock_req, mock_set, _get):
        mock_req.side_effect = [[], [], None]
        fetch_history("XYZ", "key", use_cache=True)
        mock_set.assert_not_called()
