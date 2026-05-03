"""Pure calculation and formatting functions for performance reports.

No side effects or I/O — all functions are deterministic given their inputs.
"""

from datetime import date

import pandas as pd

# ── Return period constants ────────────────────────────────────────────────

ANNUAL_YEARS = list(range(date.today().year - 1, 2018, -1))  # e.g. [2025, 2024, ..., 2019]
PERIOD_COLS = ["1D", "1W", "QTD", "YTD", "1Y", "3Y", "5Y", "10Y"]
ANNUAL_COLS = [str(y) for y in ANNUAL_YEARS]
RETURN_COLS = PERIOD_COLS + ANNUAL_COLS

# ── Fundamental column constants ──────────────────────────────────────────

FUND_COLS = ["Fwd P/E", "EV/EBITDA", "EV/S", "PEG", "Gross Mgn", "Op Mgn", "ROE", "Rev Grw", "EPS Grw"]
VAL_COLS = ["Mkt Cap", "Enterprise Value", "Net Debt", "Price", "% 52Wk Hi"]
FUND_PCT_COLS = {"Gross Mgn", "Op Mgn", "ROE", "Rev Grw", "EPS Grw"}
FUND_RATIO_COLS = {"Fwd P/E", "EV/EBITDA", "EV/S", "PEG"}
FUND_MONEY_COLS = {"Mkt Cap", "Enterprise Value", "Net Debt"}

FUND_DISPLAY_NAMES = {
    "Mkt Cap": "Mkt Cap (USD $B)",
    "Enterprise Value": "EV (USD $B)",
    "Net Debt": "Net Debt (USD $B)",
    "Price": "Price",
    "% 52Wk Hi": "% 52Wk Hi",
    "Fwd P/E": "Fwd P/E (NTM)",
    "EV/EBITDA": "EV/EBITDA (TTM)",
    "EV/S": "EV/S (TTM)",
    "PEG": "PEG",
    "Gross Mgn": "Gross Mgn (TTM)",
    "Op Mgn": "Op Mgn (TTM)",
    "ROE": "ROE (TTM)",
    "Rev Grw": "Rev Grw (TTM YoY)",
    "EPS Grw": "EPS Grw (TTM YoY)",
}

# ── Currency formatting ───────────────────────────────────────────────────

CURRENCY_SYMBOLS = {
    "JPY": "¥", "CNY": "¥", "KRW": "₩", "GBP": "£", "EUR": "€",
    "INR": "₹", "CHF": "CHF ", "HKD": "HK$", "TWD": "NT$", "AUD": "A$",
    "CAD": "C$", "NZD": "NZ$", "SEK": "SEK ", "NOK": "NOK ", "DKK": "DKK ",
    "ILS": "₪", "ZAR": "R", "BRL": "R$", "MXN": "MX$", "SAR": "SAR ",
    "AED": "AED ", "PLN": "PLN ", "SGD": "S$",
}


def _currency_prefix(currency):
    """Return display prefix for a currency code, or the code itself + space."""
    if not currency or currency == "USD":
        return ""
    return CURRENCY_SYMBOLS.get(currency, f"{currency} ")


def format_mkt_cap(value, currency=""):
    """Format value in USD billions. Values are pre-converted to USD."""
    if value is None or pd.isna(value):
        return "N/A"
    v = float(value)
    sign = "-" if v < 0 else ""
    av = abs(v)
    billions = av / 1e9
    if billions >= 100:
        return f"{sign}{billions:,.1f}"
    elif billions >= 10:
        return f"{sign}{billions:.1f}"
    elif billions >= 1:
        return f"{sign}{billions:.2f}"
    elif billions >= 0.01:
        return f"{sign}{billions:.2f}"
    elif av > 0:
        return f"{sign}{billions:.3f}"
    else:
        return "0.00"


def format_price(value, currency=""):
    """Format share price. USD: bare number. Foreign: currency symbol prefix."""
    if value is None or pd.isna(value):
        return "N/A"
    v = float(value)
    is_foreign = currency and currency != "USD"
    prefix = _currency_prefix(currency) if is_foreign else ""
    if v >= 1000:
        return f"{prefix}{v:,.0f}"
    return f"{prefix}{v:.2f}"


# ── Return calculations ──────────────────────────────────────────────────

def calc_annual_return(hist, year):
    """Calculate total return for a given calendar year."""
    year_data = hist[hist.index.year == year]
    if len(year_data) < 2:
        return None
    first = year_data.iloc[0]
    last = year_data.iloc[-1]
    return (last / first - 1) * 100


def calc_period_return(hist, days):
    """Calculate return over last N calendar days from latest data point."""
    if hist.empty:
        return None
    end = hist.iloc[-1]
    target_date = hist.index[-1] - pd.Timedelta(days=days)
    earlier = hist[hist.index <= target_date]
    if earlier.empty:
        return None
    start = earlier.iloc[-1]
    return (end / start - 1) * 100


def calc_1d_return(hist):
    """Calculate 1-day return (last close vs prior close)."""
    if len(hist) < 2:
        return None
    return (hist.iloc[-1] / hist.iloc[-2] - 1) * 100


def calc_1w_return(hist):
    """Calculate 1-week return (last 5 trading days)."""
    if len(hist) < 6:
        return None
    return (hist.iloc[-1] / hist.iloc[-6] - 1) * 100


def calc_qtd_return(hist):
    """Calculate quarter-to-date return."""
    today = date.today()
    q_start_month = ((today.month - 1) // 3) * 3 + 1
    q_start = pd.Timestamp(today.year, q_start_month, 1)
    qtr_data = hist[hist.index >= q_start]
    if len(qtr_data) < 2:
        return None
    return (qtr_data.iloc[-1] / qtr_data.iloc[0] - 1) * 100


def calc_ytd_return(hist):
    """Calculate YTD return."""
    today = date.today()
    year_data = hist[hist.index.year == today.year]
    if len(year_data) < 2:
        return None
    first = year_data.iloc[0]
    last = year_data.iloc[-1]
    return (last / first - 1) * 100


# ── Color formatting ─────────────────────────────────────────────────────

def get_color(value, is_negative_col=False):
    """Return RGB hex color for a return value. Red for negative, green for positive, white for zero/NA."""
    if value is None or pd.isna(value):
        return "FFFFFF"
    val = float(value)
    if val == 0:
        return "FFFFFF"
    if val > 0:
        intensity = min(abs(val) / 100, 1.0)
        r = int(255 - intensity * 155)
        g = int(255 - intensity * 30)
        b = int(255 - intensity * 155)
        return f"{r:02X}{g:02X}{b:02X}"
    else:
        intensity = min(abs(val) / 100, 1.0)
        r = int(255 - intensity * 30)
        g = int(255 - intensity * 155)
        b = int(255 - intensity * 155)
        return f"{r:02X}{g:02X}{b:02X}"


def get_html_color(value):
    """Return CSS background-color for HTML."""
    hex_color = get_color(value)
    return f"#{hex_color}"


# ── Composite helpers ─────────────────────────────────────────────────────

def calc_pct_of_52wk_high(hist):
    """Calculate current price as a percentage of 52-week high."""
    if hist is None or len(hist) < 2:
        return None
    one_year_ago = hist.index[-1] - pd.Timedelta(days=365)
    year_data = hist[hist.index >= one_year_ago]
    if year_data.empty:
        return None
    high = year_data.max()
    if high <= 0:
        return None
    return (year_data.iloc[-1] / high) * 100


def compute_returns(hist):
    """Compute all return columns from a price history Series."""
    if hist is None or (hasattr(hist, '__len__') and len(hist) == 0):
        return {col: None for col in RETURN_COLS}
    returns = {}
    for year in ANNUAL_YEARS:
        returns[str(year)] = calc_annual_return(hist, year)
    returns["1D"] = calc_1d_return(hist)
    returns["1W"] = calc_1w_return(hist)
    returns["QTD"] = calc_qtd_return(hist)
    returns["YTD"] = calc_ytd_return(hist)
    returns["1Y"] = calc_period_return(hist, 365)
    returns["3Y"] = calc_period_return(hist, 365 * 3)
    returns["5Y"] = calc_period_return(hist, 365 * 5)
    returns["10Y"] = calc_period_return(hist, 365 * 10)
    returns["% 52Wk Hi"] = calc_pct_of_52wk_high(hist)
    return returns


def build_result_row(ticker, company, sector, subsector, yf_sector, yf_industry,
                     country_iso, exchange, returns, fund, is_ttm, currency):
    """Build a standardized result row dict."""
    def _clean(val):
        return val if val != "nan" else ""

    row = {
        "Ticker": ticker,
        "Company Name": _clean(company),
        "Sector (JP)": _clean(sector),
        "Subsector (JP)": _clean(subsector),
        "YF Sector": _clean(yf_sector),
        "YF Industry": _clean(yf_industry),
        "Country (ISO)": _clean(country_iso),
        "Exchange": _clean(exchange),
        "_is_ttm_rev": is_ttm["Rev Grw"],
        "_is_ttm_eps": is_ttm["EPS Grw"],
        "_currency": currency,
    }
    row.update(fund)
    row.update(returns)  # returns last so price-derived fields (% 52Wk Hi) aren't overwritten by fund nulls
    return row
