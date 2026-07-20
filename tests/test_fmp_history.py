"""Tests for providers/fmp_history.py — historical valuation fetching."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.fmp_history import (
    HISTORY_SCHEMA_VERSION,
    HISTORY_YEARS,
    STATUS_ERROR,
    STATUS_NO_DATA,
    STATUS_NOT_ATTEMPTED,
    STATUS_OK,
    fetch_history,
    is_cached,
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


def _cache_payload(**overrides):
    payload = {
        "status": STATUS_OK,
        "pe_ttm": 72.5,
        "pe_history": _pad([65.3, 62.1, 58.9, 55.0, 71.2], HISTORY_YEARS),
        "evs_history": _pad([18.2, 17.5, 16.8, 15.9, 14.0], HISTORY_YEARS),
        "record_dates": _pad(["2025-12-31"], HISTORY_YEARS, fill=""),
        "fetched_at": "2026-07-19T00:00:00+00:00",
        "schema_version": HISTORY_SCHEMA_VERSION,
    }
    payload.update(overrides)
    return payload


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
        rows, errored = _fetch_ratios_annual("ISRG", "key")
        assert len(rows) == 5
        assert rows[0]["priceToEarningsRatio"] == 65.3
        assert errored is False

    @patch("providers.fmp_history._fmp_request")
    def test_handles_empty_response(self, mock_req):
        mock_req.return_value = []
        assert _fetch_ratios_annual("XYZ", "key") == ([], False)

    @patch("providers.fmp_history._fmp_request")
    def test_handles_none_response(self, mock_req):
        """None means _fmp_request swallowed a 402/non-200 — a provider
        failure, so it must be RETRYABLE, not cached as no-data.
        (This assertion was inverted before 2026-07-20 and codified the bug.)"""
        mock_req.return_value = None
        assert _fetch_ratios_annual("XYZ", "key") == ([], True)

    @patch("providers.fmp_history._fmp_request")
    def test_handles_non_list_response(self, mock_req):
        """An FMP error payload is a failure, not an empty result."""
        mock_req.return_value = {"error": "bad"}
        assert _fetch_ratios_annual("XYZ", "key") == ([], True)

    @patch("providers.fmp_history._fmp_request")
    def test_exception_is_reported_as_errored(self, mock_req):
        """An exception must be distinguishable from a legitimate empty answer."""
        mock_req.side_effect = RuntimeError("connection reset")
        rows, errored = _fetch_key_metrics_annual("XYZ", "key")
        assert rows == []
        assert errored is True


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

        assert result["status"] == STATUS_OK
        assert result["pe_ttm"] == 72.5
        assert result["pe_history"][:5] == [65.3, 62.1, 58.9, None, 71.2]
        assert result["evs_history"][:5] == [18.2, 17.5, 16.8, 15.9, 14.0]
        assert result["record_dates"][0] == "2025-12-31"

    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history._fmp_request")
    def test_series_are_ten_years_long(self, mock_req, _set, _get):
        """The stored series must span the 10Y window, not the legacy 5."""
        mock_req.side_effect = [
            SAMPLE_RATIOS_ANNUAL,
            SAMPLE_KEY_METRICS_ANNUAL,
            [SAMPLE_RATIOS_TTM],
        ]
        result = fetch_history("ISRG", "key", use_cache=True)
        assert HISTORY_YEARS == 10
        for key in ("pe_history", "evs_history", "record_dates"):
            assert len(result[key]) == HISTORY_YEARS

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

        assert result["pe_history"] == _pad([65.3, 62.1], HISTORY_YEARS)
        assert result["evs_history"] == _pad([18.2, 17.5], HISTORY_YEARS)
        # record_dates pads with empty string, not None
        assert result["record_dates"] == _pad(["2025-12-31", "2024-12-31"], HISTORY_YEARS, fill="")

    def test_no_api_key_returns_not_attempted(self):
        result = fetch_history("ISRG", api_key=None, use_cache=False)
        assert result["status"] == STATUS_NOT_ATTEMPTED
        assert result["pe_ttm"] is None
        assert result["pe_history"] == [None] * HISTORY_YEARS
        assert result["evs_history"] == [None] * HISTORY_YEARS

    @patch("providers.fmp_history._fmp_request")
    def test_cache_only_never_calls_the_api(self, mock_req):
        """cache_only is what the report uses — it must not spend a single call."""
        with patch("providers.fmp_history.cache_get", return_value=None):
            result = fetch_history("ISRG", "key", use_cache=True, cache_only=True)
        assert result["status"] == STATUS_NOT_ATTEMPTED
        mock_req.assert_not_called()

    @patch("providers.fmp_history.cache_get")
    def test_uses_cache(self, mock_get):
        mock_get.return_value = _cache_payload()
        result = fetch_history("ISRG", "key", use_cache=True)
        assert result["pe_ttm"] == 72.5
        # _fmp_request should not have been called
        mock_get.assert_called_once()

    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history._fmp_request")
    @patch("providers.fmp_history.cache_get")
    def test_stale_schema_cache_entry_is_refetched(self, mock_get, mock_req, _set):
        """A v1 (5-element, status-less) entry must not be parsed as current."""
        mock_get.return_value = {
            "pe_ttm": 10.0,
            "pe_history": [10.0] * 5,
            "evs_history": [1.0] * 5,
            "record_dates": [""] * 5,
        }
        mock_req.side_effect = [
            SAMPLE_RATIOS_ANNUAL,
            SAMPLE_KEY_METRICS_ANNUAL,
            [SAMPLE_RATIOS_TTM],
        ]
        result = fetch_history("ISRG", "key", use_cache=True)
        assert result["schema_version"] == HISTORY_SCHEMA_VERSION
        assert len(result["pe_history"]) == HISTORY_YEARS
        assert mock_req.called

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
    def test_empty_answer_is_recorded_as_no_data(self, mock_req, mock_set, _get):
        """FMP answering 'nothing here' is a FACT and must be cached as such."""
        # All three calls answer with a genuine empty list. (The third used to
        # be None, which now correctly reads as a provider error, not no-data.)
        mock_req.side_effect = [[], [], []]
        result = fetch_history("XYZ", "key", use_cache=True)
        assert result["status"] == STATUS_NO_DATA
        mock_set.assert_called_once()

    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history._fmp_request")
    def test_transient_error_is_not_cached(self, mock_req, mock_set, _get):
        """Caching an error would silently freeze a blank column in place."""
        mock_req.side_effect = RuntimeError("503 from FMP")
        result = fetch_history("XYZ", "key", use_cache=True)
        assert result["status"] == STATUS_ERROR
        mock_set.assert_not_called()

    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history._fmp_request")
    def test_failure_never_yields_zero(self, mock_req, _set, _get):
        """A 0 in a P/E-min column would corrupt every downstream screen."""
        mock_req.side_effect = RuntimeError("boom")
        result = fetch_history("XYZ", "key", use_cache=True)
        assert result["pe_ttm"] is None
        assert all(v is None for v in result["pe_history"])
        assert all(v is None for v in result["evs_history"])


class TestIsCached:
    @patch("providers.fmp_history.cache_get")
    def test_true_for_current_schema_ok_entry(self, mock_get):
        mock_get.return_value = _cache_payload()
        assert is_cached("ISRG") is True

    @patch("providers.fmp_history.cache_get")
    def test_false_for_missing_entry(self, mock_get):
        mock_get.return_value = None
        assert is_cached("ISRG") is False

    @patch("providers.fmp_history.cache_get")
    def test_false_for_stale_schema(self, mock_get):
        mock_get.return_value = {"pe_ttm": 1.0, "pe_history": [1.0] * 5}
        assert is_cached("ISRG") is False


class TestProviderFailureIsNotNoData:
    """A 402/non-200 or an FMP error payload must stay RETRYABLE. Treating it as
    `no_data` cached a provider outage as authoritative "this ticker has no
    history" for 7 days, and history-backfill then skipped straight past it —
    silent, and self-perpetuating across the universe (codex 2026-07-20)."""

    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history._fmp_request", return_value=None)
    def test_402_or_non_200_is_error_and_never_cached(self, _req, _get, mock_set):
        result = fetch_history("AAPL", "key", use_cache=True)
        assert result["status"] == STATUS_ERROR
        mock_set.assert_not_called()

    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history._fmp_request",
           return_value={"Error Message": "Invalid API KEY"})
    def test_error_payload_is_error_and_never_cached(self, _req, _get, mock_set):
        result = fetch_history("AAPL", "bad-key", use_cache=True)
        assert result["status"] == STATUS_ERROR
        mock_set.assert_not_called()

    @patch("providers.fmp_history.cache_set")
    @patch("providers.fmp_history.cache_get", return_value=None)
    @patch("providers.fmp_history._fmp_request", return_value=[])
    def test_genuine_empty_list_is_no_data_and_IS_cached(self, _req, _get, mock_set):
        """An empty list means FMP answered and the company has no rows — a
        real fact, worth caching so we stop asking."""
        result = fetch_history("NEWCO", "key", use_cache=True)
        assert result["status"] == STATUS_NO_DATA
        mock_set.assert_called_once()

    def test_unwrap_helper_classifies_each_shape(self):
        from providers.fmp_history import _unwrap_list_response
        assert _unwrap_list_response([{"a": 1}], "X", "t") == ([{"a": 1}], False)
        assert _unwrap_list_response([], "X", "t") == ([], False)
        assert _unwrap_list_response(None, "X", "t") == ([], True)
        assert _unwrap_list_response({"Error Message": "x"}, "X", "t") == ([], True)
        assert _unwrap_list_response("garbage", "X", "t") == ([], True)

    @patch("providers.fmp_history._fmp_request", return_value=None)
    def test_ttm_402_is_also_an_error(self, _req):
        from providers.fmp_history import _fetch_ratios_ttm_live
        assert _fetch_ratios_ttm_live("AAPL", "key") == ({}, True)

    @patch("providers.fmp_history._fmp_request", return_value=[])
    def test_ttm_empty_list_is_not_an_error(self, _req):
        from providers.fmp_history import _fetch_ratios_ttm_live
        assert _fetch_ratios_ttm_live("AAPL", "key") == ({}, False)
