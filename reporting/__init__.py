"""Reporting — performance calculations, Excel/HTML output, and email."""

from reporting.calcs import (
    PERIOD_COLS, ANNUAL_COLS, ANNUAL_YEARS, RETURN_COLS,
    FUND_COLS, VAL_COLS, FUND_PCT_COLS,
    compute_returns, build_result_row,
    get_color, get_html_color,
    format_mkt_cap, format_price,
)
