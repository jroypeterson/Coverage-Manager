"""Coverage performance report generator — orchestrator.

Coordinates data fetching, return calculations, and report generation.
Heavy lifting is delegated to perf_calcs, perf_data, perf_excel, perf_html, perf_email.
"""

import pandas as pd
import openpyxl
import warnings
import sys
import os

from config import (
    CSV_PATH, REPORTS_DIR, OLD_REPORTS_DIR, SAMPLE_REPORTS_DIR, API_KEYS, TODAY,
    BIOPHARMA_VALUES, HC_SERVICES_MEDTECH_VALUES, SECTOR_SEGMENTS, SAMPLE_TICKERS,
    SEGMENT_ETFS,
)
from ticker_utils import normalize_ticker, MANUAL_TICKER_MAP
from logging_utils import configure_logging, get_logger

from reporting.calcs import (
    ANNUAL_YEARS, PERIOD_COLS, ANNUAL_COLS, RETURN_COLS,
    FUND_COLS, VAL_COLS,
    compute_returns, build_result_row,
)
from providers.wikipedia_provider import fetch_sp500_tickers
from providers.fmp_provider import fetch_historical_prices as try_fmp_historical
from providers.finnhub_provider import fetch_metrics as fetch_finnhub_metrics
from providers.finnhub_provider import fetch_parallel as fetch_finnhub_parallel
from providers.yfinance_provider import (
    fetch_fundamentals, fetch_fundamentals_parallel, batch_download_prices,
)
from reporting.excel import write_excel_sheet
from reporting.html import write_html_report, build_ticker_health_data
from reporting.email import archive_old_files, send_email_report
from providers.fx_provider import fetch_fx_rates

warnings.filterwarnings("ignore")
logger = get_logger("generate_performance")

# ── Output paths ─────────────────────────────────────────────────────────────

OUTPUT_XLSX = REPORTS_DIR / f"coverage_performance_{TODAY}.xlsx"
OUTPUT_HTML = REPORTS_DIR / f"coverage_performance_{TODAY}.html"

# ── Helper functions ───────────────────────────────────────────────────────


def classify_sector_group(row):
    """Classify a coverage row into a sector group based on Sector (JP) / Subsector (JP)."""
    sector = str(row.get("Sector (JP)", "")).strip()
    subsector = str(row.get("Subsector (JP)", "")).strip()
    if sector in BIOPHARMA_VALUES or subsector in BIOPHARMA_VALUES:
        return "Biopharma"
    if sector in HC_SERVICES_MEDTECH_VALUES or subsector in HC_SERVICES_MEDTECH_VALUES:
        return "HC Svcs & MedTech"
    return "PA & Other"


def _split_into_segments(result_df):
    """Split result_df into segment DataFrames keyed by tab name."""
    segments = {"Consolidated": result_df}
    biopharma_mask = result_df.apply(lambda r: classify_sector_group(r) == "Biopharma", axis=1)
    hc_svcs_mask = result_df.apply(lambda r: classify_sector_group(r) == "HC Svcs & MedTech", axis=1)
    pa_other_mask = ~biopharma_mask & ~hc_svcs_mask
    segments["Biopharma"] = result_df[biopharma_mask].reset_index(drop=True)
    segments["HC Svcs & MedTech"] = result_df[hc_svcs_mask].reset_index(drop=True)
    segments["PA & Other"] = result_df[pa_other_mask].reset_index(drop=True)
    return segments


# ── Main ───────────────────────────────────────────────────────────────────

def main(sample_mode=False):
    global OUTPUT_XLSX, OUTPUT_HTML
    configure_logging()
    os.makedirs(REPORTS_DIR, exist_ok=True)

    if sample_mode:
        logger.info("=== SAMPLE PREVIEW MODE ===")
        os.makedirs(SAMPLE_REPORTS_DIR, exist_ok=True)
        OUTPUT_XLSX = SAMPLE_REPORTS_DIR / "sample_preview.xlsx"
        OUTPUT_HTML = SAMPLE_REPORTS_DIR / "sample_preview.html"
        sample_set = {t.upper() for t in SAMPLE_TICKERS}
    else:
        archive_old_files(REPORTS_DIR, OLD_REPORTS_DIR, TODAY)

    logger.info("Reading coverage CSV...")
    df = pd.read_csv(CSV_PATH)

    # Deduplicate tickers
    seen = set()
    unique_rows = []
    for _, row in df.iterrows():
        ticker = str(row.get("Ticker", "")).strip()
        if ticker and ticker != "#N/A" and ticker not in seen:
            if sample_mode and ticker.upper() not in sample_set:
                continue
            seen.add(ticker)
            unique_rows.append(row)
    df_unique = pd.DataFrame(unique_rows).reset_index(drop=True)
    logger.info("Found %s unique tickers", len(df_unique))

    # Normalize tickers for yfinance
    yf_tickers = []
    ticker_map = {}  # yf_ticker -> original_ticker
    for _, row in df_unique.iterrows():
        orig = str(row["Ticker"]).strip()
        company = str(row.get("Company Name", "")).strip()
        yf_t = normalize_ticker(orig, company)
        if yf_t:
            yf_tickers.append(yf_t)
            ticker_map[yf_t] = orig

    logger.info("Downloading data for %s tickers (this may take several minutes)...", len(yf_tickers))
    all_results = batch_download_prices(yf_tickers)

    # FMP fallback for missing US tickers
    fmp_key = API_KEYS.get("FMP_API_KEY")
    if not fmp_key:
        logger.warning("FMP_API_KEY not set — skipping FMP fallback for missing tickers")
    if fmp_key:
        missing = [t for t in yf_tickers if t not in all_results and "." not in t]
        if missing:
            logger.info("Trying FMP API fallback for %s missing US tickers...", len(missing))
            fmp_found = 0
            for t in missing:
                series = try_fmp_historical(t, fmp_key)
                if series is not None:
                    all_results[t] = series
                    fmp_found += 1
            logger.info("FMP resolved %s additional tickers", fmp_found)

    logger.info("Total tickers with data: %s", len(all_results))

    # Fetch Finnhub metrics for US tickers (no dot in symbol = likely US)
    finnhub_key = API_KEYS.get("FINNHUB_API_KEY")
    finnhub_data = {}
    if finnhub_key:
        us_yf_tickers = [t for t in yf_tickers if "." not in t]
        logger.info("Fetching Finnhub fundamentals for %s US tickers...", len(us_yf_tickers))
        finnhub_data = fetch_finnhub_parallel(us_yf_tickers, finnhub_key)
        logger.info("Finnhub returned data for %s tickers", len(finnhub_data))

    # Fetch yfinance fundamentals for all tickers (parallel)
    logger.info("Fetching yfinance fundamentals for %s tickers...", len(yf_tickers))
    all_fundamentals, all_is_ttm, all_currencies = fetch_fundamentals_parallel(
        yf_tickers, finnhub_data, max_workers=10
    )
    fund_count = sum(1 for v in all_fundamentals.values() if v.get("Mkt Cap") is not None)
    logger.info("Fundamentals loaded for %s tickers", fund_count)

    # Convert Mkt Cap, EV, Net Debt to USD
    unique_currencies = {c for c in all_currencies.values() if c and c != "USD"}
    fx_rates = fetch_fx_rates(unique_currencies) if unique_currencies else {"USD": 1.0}
    usd_convert_fields = ["Mkt Cap", "Enterprise Value", "Net Debt"]
    converted = 0
    for yf_t, fund in all_fundamentals.items():
        currency = all_currencies.get(yf_t, "USD")
        if not currency or currency == "USD":
            continue
        rate = fx_rates.get(currency)
        if rate is None:
            continue
        for field in usd_convert_fields:
            val = fund.get(field)
            if val is not None:
                fund[field] = val * rate
        converted += 1
    if converted:
        logger.info("Converted Mkt Cap/EV/Net Debt to USD for %d non-USD tickers", converted)

    # Build ticker health data (no extra API calls)
    health_data = build_ticker_health_data(df_unique, yf_tickers, ticker_map, all_results, all_fundamentals)
    logger.info("Ticker health: %s issues found", health_data["total_issues"])

    # Calculate returns
    results = []
    for _, row in df_unique.iterrows():
        orig_ticker = str(row["Ticker"]).strip()
        company = str(row.get("Company Name", "")).strip()
        yf_t = normalize_ticker(orig_ticker, company)

        hist = all_results.get(yf_t) if yf_t else None
        returns = compute_returns(hist)
        fund = all_fundamentals.get(yf_t, {col: None for col in FUND_COLS + VAL_COLS})
        is_ttm = all_is_ttm.get(yf_t, {"Rev Grw": False, "EPS Grw": False})

        result_row = build_result_row(
            ticker=orig_ticker,
            company=company,
            sector=str(row.get("Sector (JP)", row.get("Sector", ""))).strip(),
            subsector=str(row.get("Subsector (JP)", row.get("Subsector", ""))).strip(),
            yf_sector=str(row.get("YF Sector", "")).strip(),
            yf_industry=str(row.get("YF Industry", "")).strip(),
            country_iso=str(row.get("Country (ISO)", "")).strip(),
            exchange=str(row.get("Exchange", "")).strip(),
            returns=returns, fund=fund, is_ttm=is_ttm,
            currency=all_currencies.get(yf_t, ""),
            core=str(row.get("Core", "")).strip(),
        )
        results.append(result_row)

    result_df = pd.DataFrame(results)
    info_cols = ["Ticker", "Company Name"] + VAL_COLS + ["Sector (JP)", "Subsector (JP)", "Core", "YF Sector", "YF Industry", "Country (ISO)", "Exchange"]

    # ── Step tracking ────────────────────────────────────────────────────────
    step_results = {}

    def run_step(name, fn, *args, **kwargs):
        """Run a pipeline step, catching and logging failures."""
        try:
            result = fn(*args, **kwargs)
            step_results[name] = "ok"
            return result
        except Exception as e:
            logger.warning("Step '%s' failed: %s", name, e)
            step_results[name] = f"failed: {e}"
            return None

    # ============ S&P 500 ============
    sp500_result_df = None
    if not sample_mode:
        def _fetch_sp500():
            nonlocal sp500_result_df
            logger.info("Fetching S&P 500 constituents...")
            sp500_tickers, sp500_info = fetch_sp500_tickers()

            sp500_all = [(t, sp500_info.get(t, {})) for t in sp500_tickers]

            logger.info("S&P 500 tickers: %s", len(sp500_all))
            if not sp500_all:
                return

            sp500_yf_tickers = [t for t, _ in sp500_all]
            sp500_results_data = batch_download_prices(sp500_yf_tickers)

            logger.info("Fetching fundamentals for %s S&P 500 tickers...", len(sp500_yf_tickers))
            sp500_finnhub = {}
            if finnhub_key:
                sp500_finnhub = fetch_finnhub_parallel(sp500_yf_tickers, finnhub_key)
            sp500_fundamentals, sp500_is_ttm, sp500_currencies = fetch_fundamentals_parallel(
                sp500_yf_tickers, sp500_finnhub, max_workers=10
            )

            sp500_rows = []
            for t, info_entry in sp500_all:
                returns = compute_returns(sp500_results_data.get(t))
                fund = sp500_fundamentals.get(t, {col: None for col in FUND_COLS + VAL_COLS})
                is_ttm = sp500_is_ttm.get(t, {"Rev Grw": False, "EPS Grw": False})
                row_data = build_result_row(
                    ticker=t,
                    company=info_entry.get("Company Name", ""),
                    sector=info_entry.get("GICS Sector", ""),
                    subsector=info_entry.get("GICS Sub-Industry", ""),
                    yf_sector=info_entry.get("GICS Sector", ""),
                    yf_industry=info_entry.get("GICS Sub-Industry", ""),
                    country_iso="USA", exchange="",
                    returns=returns, fund=fund, is_ttm=is_ttm,
                    currency=sp500_currencies.get(t, ""),
                )
                sp500_rows.append(row_data)
            sp500_result_df = pd.DataFrame(sp500_rows)
            logger.info("S&P 500 report: %s tickers", len(sp500_result_df))

        run_step("sp500", _fetch_sp500)
    else:
        logger.info("Skipping S&P 500 report (sample mode)")
        step_results["sp500"] = "skipped"

    # ============ ETF BENCHMARKS ============
    # Collect all unique ETF tickers needed across segments
    all_etf_tickers = list({t for etfs in SEGMENT_ETFS.values() for t, _ in etfs})
    etf_row_cache = {}  # ticker -> result row dict
    if all_etf_tickers and not sample_mode:
        logger.info("Fetching ETF benchmark data for %s tickers...", len(all_etf_tickers))
        etf_prices = batch_download_prices(all_etf_tickers)
        for etf_ticker, etf_name in {t: n for etfs in SEGMENT_ETFS.values() for t, n in etfs}.items():
            hist = etf_prices.get(etf_ticker)
            returns = compute_returns(hist)
            etf_row = build_result_row(
                ticker=etf_ticker, company=etf_name,
                sector="ETF", subsector="", yf_sector="", yf_industry="",
                country_iso="USA", exchange="",
                returns=returns,
                fund={col: None for col in FUND_COLS + VAL_COLS},
                is_ttm={"Rev Grw": False, "EPS Grw": False},
                currency="USD",
            )
            etf_row["_is_etf"] = True
            etf_row_cache[etf_ticker] = etf_row

    # ============ EXCEL OUTPUT ============
    segment_dfs = _split_into_segments(result_df)

    # Append ETF benchmark rows to coverage segments
    if etf_row_cache:
        for seg_name in list(segment_dfs.keys()):
            etf_list = SEGMENT_ETFS.get(seg_name, [])
            if etf_list:
                etf_rows = [etf_row_cache[t] for t, _ in etf_list if t in etf_row_cache]
                if etf_rows:
                    segment_dfs[seg_name] = pd.concat(
                        [segment_dfs[seg_name], pd.DataFrame(etf_rows)],
                        ignore_index=True,
                    )

    def _generate_excel():
        logger.info("Generating Excel file...")
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for tab_name, _html_suffix, _title in SECTOR_SEGMENTS:
            if tab_name == "Non-HC S&P 500":
                continue
            seg_df = segment_dfs.get(tab_name, pd.DataFrame())
            if seg_df.empty:
                logger.info("Skipping empty Excel tab: %s", tab_name)
                continue
            write_excel_sheet(wb, tab_name, seg_df, info_cols)
        if sp500_result_df is not None and not sp500_result_df.empty:
            sp500_with_etfs = sp500_result_df
            sp500_etf_list = SEGMENT_ETFS.get("S&P 500", [])
            if sp500_etf_list and etf_row_cache:
                etf_rows = [etf_row_cache[t] for t, _ in sp500_etf_list if t in etf_row_cache]
                if etf_rows:
                    sp500_with_etfs = pd.concat(
                        [sp500_result_df, pd.DataFrame(etf_rows)], ignore_index=True,
                    )
            write_excel_sheet(wb, "S&P 500", sp500_with_etfs, info_cols)
        wb.save(OUTPUT_XLSX)
        logger.info("Saved: %s", OUTPUT_XLSX)

    run_step("excel", _generate_excel)

    # ============ HTML OUTPUT ============
    html_paths = []

    def _generate_html():
        logger.info("Generating HTML reports...")
        for tab_name, html_suffix, report_title in SECTOR_SEGMENTS:
            if tab_name == "S&P 500":
                seg_df = sp500_result_df if sp500_result_df is not None else pd.DataFrame()
                sp500_etf_list = SEGMENT_ETFS.get("S&P 500", [])
                if not seg_df.empty and sp500_etf_list and etf_row_cache:
                    etf_rows = [etf_row_cache[t] for t, _ in sp500_etf_list if t in etf_row_cache]
                    if etf_rows:
                        seg_df = pd.concat([seg_df, pd.DataFrame(etf_rows)], ignore_index=True)
            else:
                seg_df = segment_dfs.get(tab_name, pd.DataFrame())
            if seg_df.empty:
                logger.info("Skipping empty HTML report: %s", tab_name)
                continue
            if sample_mode:
                html_path = SAMPLE_REPORTS_DIR / f"sample_{html_suffix}.html"
            else:
                html_path = REPORTS_DIR / f"coverage_{html_suffix}_{TODAY}.html"
            seg_health = health_data if tab_name == "Consolidated" else None
            write_html_report(seg_df, html_path, report_title, seg_health)
            html_paths.append(html_path)

    run_step("html", _generate_html)

    # ============ EMAIL REPORT ============
    if not sample_mode:
        gmail_addr = API_KEYS.get("GMAIL_ADDRESS")
        gmail_pass = API_KEYS.get("GMAIL_APP_PASSWORD")
        if gmail_addr and gmail_pass and html_paths:
            def _send_email():
                logger.info("Emailing %s HTML report(s)...", len(html_paths))
                send_email_report(gmail_addr, gmail_pass, html_paths, TODAY)
            run_step("email", _send_email)
        else:
            logger.info("Skipping email (GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set in .env)")
            step_results["email"] = "skipped"
    else:
        logger.info("Skipping email (sample mode)")
        step_results["email"] = "skipped"

    # ============ SUMMARY ============
    logger.info("-- Pipeline Summary --")
    for step_name, status in step_results.items():
        logger.info("  %-15s %s", step_name, status)
    logger.info("Done!")


if __name__ == "__main__":
    main(sample_mode="--sample" in sys.argv)
