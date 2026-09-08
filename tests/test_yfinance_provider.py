"""Tests for providers/yfinance_provider.py — fundamentals fetching."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.yfinance_provider import fetch_fundamentals


class TestCurrencyPrecedence:
    """`currency` is the QUOTE currency, which is what `Mkt Cap` and `Price` need.

    ⛑ THIS DOCSTRING USED TO READ "Mkt Cap / EV / Net Debt are price-derived, so
    USD conversion must use the QUOTE currency." A GREEN test file asserting a
    claim that is one-third false. It is right about `Mkt Cap`; EV is `Mkt Cap`
    (quote) plus net debt (reporting) summed as if one unit, and `Net Debt` is
    purely reporting. Corrected 2026-09-08 rather than patched around -- the
    tests below were always testing the right thing (currency PRECEDENCE for the
    cap), it was the sentence explaining them that generalised to two fields it
    did not describe.
    """

    @patch("providers.yfinance_provider.cache_set")
    @patch("providers.yfinance_provider.cache_get", return_value=None)
    @patch("providers.yfinance_provider._fetch_ticker_info")
    def test_quote_currency_wins_over_reporting_currency(self, mock_info, mock_cget, mock_cset):
        # NVO-like ADR: quotes in USD but reports financials in DKK. The old
        # precedence returned "DKK" (mis-converting the USD-quoted cap).
        mock_info.return_value = {
            "marketCap": 219_000_000_000,
            "currency": "USD",
            "financialCurrency": "DKK",
            "currentPrice": 120.0,
        }
        _, _, currency = fetch_fundamentals("NVO", use_cache=False)
        assert currency == "USD"

    @patch("providers.yfinance_provider.cache_set")
    @patch("providers.yfinance_provider.cache_get", return_value=None)
    @patch("providers.yfinance_provider._fetch_ticker_info")
    def test_falls_back_to_financial_currency_when_quote_absent(self, mock_info, mock_cget, mock_cset):
        mock_info.return_value = {
            "marketCap": 1000,
            "financialCurrency": "EUR",
            "currentPrice": 10.0,
        }
        _, _, currency = fetch_fundamentals("XYZ", use_cache=False)
        assert currency == "EUR"

    @patch("providers.yfinance_provider.cache_set")
    @patch("providers.yfinance_provider.cache_get", return_value=None)
    @patch("providers.yfinance_provider._fetch_ticker_info")
    def test_empty_when_neither_present(self, mock_info, mock_cget, mock_cset):
        mock_info.return_value = {"marketCap": 1000, "currentPrice": 10.0}
        _, _, currency = fetch_fundamentals("ZZZ", use_cache=False)
        assert currency == ""
