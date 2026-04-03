"""API and data-fetching functions for performance reports.

This module is a backward-compatibility shim. All functions have been
extracted into provider modules under providers/. Import from there for
new code; this file re-exports for existing callers.
"""

# Re-export from providers for backward compat
from providers.wikipedia_provider import fetch_sp500_tickers
from providers.fmp_provider import fetch_historical_prices as try_fmp_historical
from providers.finnhub_provider import fetch_metrics as fetch_finnhub_metrics
from providers.finnhub_provider import fetch_parallel as fetch_finnhub_parallel
from providers.yfinance_provider import (
    fetch_fundamentals,
    fetch_fundamentals_parallel,
    batch_download_prices,
)

__all__ = [
    "fetch_sp500_tickers",
    "try_fmp_historical",
    "fetch_finnhub_metrics",
    "fetch_finnhub_parallel",
    "fetch_fundamentals",
    "fetch_fundamentals_parallel",
    "batch_download_prices",
]
