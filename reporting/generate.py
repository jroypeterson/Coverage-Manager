"""Coverage performance report generator — orchestrator.

Coordinates data fetching, return calculations, and report generation.
Heavy lifting is delegated to perf_calcs, perf_data, perf_excel, perf_html, perf_email.
"""

import json
import os
import re
import sys
import time
from datetime import datetime

import openpyxl
import pandas as pd
import warnings

from config import (
    CSV_PATH, REPORTS_DIR, OLD_REPORTS_DIR, SAMPLE_REPORTS_DIR, API_KEYS, TODAY,
    BIOPHARMA_VALUES, HC_SERVICES_MEDTECH_VALUES, SECTOR_SEGMENTS, SAMPLE_TICKERS,
    SEGMENT_ETFS,
)
from ticker_utils import normalize_ticker
from logging_utils import configure_logging, get_logger

from reporting.calcs import (
    FUND_COLS, VAL_COLS, HIST_COLS, HIST_STATUS_COL,
    compute_returns, build_result_row, forward_2yr_eps_growth_pct,
)
from reporting.history_stats import stats_from_series
from providers.wikipedia_provider import fetch_sp500_tickers
from providers.fmp_provider import fetch_historical_prices as try_fmp_historical
from providers.fmp_history import fetch_history_parallel, STATUS_NOT_ATTEMPTED
from providers.fmp_estimates import fetch_estimates_parallel
from providers.finnhub_provider import fetch_parallel as fetch_finnhub_parallel
from providers.yfinance_provider import batch_download_prices
from providers.provider_chain import fetch_all_fundamentals
from reporting.excel import write_excel_sheet
from reporting.html import write_html_report, build_ticker_health_data
from reporting.email import archive_old_files, send_email_report
from providers.fx_provider import fetch_aggregate_fx, fetch_fx_rates, major_unit
from providers.valuation import _usable_rate, derive_valuation

warnings.filterwarnings("ignore")
logger = get_logger("generate_performance")

# ── Output paths ─────────────────────────────────────────────────────────────

OUTPUT_XLSX = REPORTS_DIR / f"coverage_performance_{TODAY}.xlsx"
OUTPUT_HTML = REPORTS_DIR / f"coverage_performance_{TODAY}.html"
OUTPUT_PE_GROWTH_PNG = REPORTS_DIR / f"coverage_pe_vs_growth_{TODAY}.png"

# ── Helper functions ───────────────────────────────────────────────────────



USD_AGGREGATE_FIELDS = ["Mkt Cap", "Enterprise Value", "Net Debt"]


EV_DERIVED_FIELDS = ["Enterprise Value", "Net Debt", "EV/EBITDA", "EV/S",
                     "Revenue (TTM)"]


def _blank_ev_fields(fund):
    """Blank every field derived from the vendor's EV. Never a fallback.

    ⛑ THE GATE IS MISSING-PROOF, NOT RATIO-ANOMALY. Novo's vendor EV was 37%
    high while sitting INSIDE any sane EV/market-cap band; CYH is correct while
    sitting well outside one. A ratio screen flags the innocent and passes the
    guilty, so the only honest question is "are this field's own inputs present
    and convertible". Blank the FIELDS, never the row -- Mkt Cap, Price and the
    returns are proven independently of EV.
    """
    for f in EV_DERIVED_FIELDS:
        fund[f] = None


def _recompute_ev_from_primitives(all_fundamentals, all_currencies, fx, skip=()):
    """Replace EV / Net Debt / EV-multiples with values computed from primitives.

    Returns (computed, blanked). `skip` is the set of tickers the caller already
    blanked for having no usable quote rate.

    ⛑ `skip` IS NOT BOOKKEEPING. Found by this module's own test: a row whose
    `Mkt Cap` had just been blanked for a dead rate got its EV recomputed anyway,
    because `derive_valuation` reads the quote currency off the row's OWN
    `_valuation` payload while the blanking decision was made from
    `all_currencies`. Two sources of truth for one fact, and the row published an
    ENTERPRISE VALUE WITH NO MARKET CAP -- internally impossible, and the kind of
    thing a reader trusts because the number itself looks fine.

    ⛑ WHY THIS OVERWRITES RATHER THAN FILLS A GAP. `yfinance_provider` still
    populates `Enterprise Value` and `Net Debt` from the vendor, because
    `provider_chain._is_success` counts EV as a quality field and `_merge_partial`
    fills any None from the NEXT provider -- so leaving them empty upstream would
    have pulled FMP's identically mixed-unit EV in behind it and reintroduced the
    defect through the fallback. Making one site the authority is what closes
    that door; a row with no primitives is blanked here rather than trusted.
    """
    computed, blanked = 0, 0
    skip = set(skip)
    for yf_t, fund in all_fundamentals.items():
        if yf_t in skip:
            continue          # already blanked; its market cap is unpublishable
        prim = fund.get("_valuation")
        if not isinstance(prim, dict):
            # No primitives: an FMP/AlphaVantage row, or a cache entry predating
            # them. We cannot show that its EV is in any single currency, so we
            # do not publish it. Absent is a true statement; a mixed unit is not.
            _blank_ev_fields(fund)
            blanked += 1
            continue
        val = derive_valuation(prim, fx)
        if val["ev_usd_m"] is None:
            _blank_ev_fields(fund)
            blanked += 1
            continue
        ev_usd = val["ev_usd_m"] * 1e6
        fund["Enterprise Value"] = ev_usd
        # Net debt is the reporting-currency leg, converted on the REPORTING
        # rate -- the whole point. It comes back FROM `derive_valuation` rather
        # than being re-derived here: re-deriving was a second implementation of
        # a leg already proven, and it skipped `num()`, so a vendor string
        # `"5.4e12"` raised TypeError and aborted the run (Fable, 2026-09-08).
        fund["Net Debt"] = val["net_debt_usd"]
        fund["EV/S"] = val["ev_sales"]
        fund["EV/EBITDA"] = val["ev_ebitda"]
        # ⛑ Revenue in USD, from the primitive rather than derived as
        # `EV / (EV/Sales)`. The derived form inherits any error in EITHER input
        # and returns nothing at all for a row with no EV/Sales -- which is every
        # pre-revenue biotech, i.e. exactly the population a commercial-stage
        # classification has to separate. ZERO is a real answer here and is kept
        # distinct from None.
        fund["Revenue (TTM)"] = (val["revenue_usd_m"] * 1e6
                                 if val["revenue_usd_m"] is not None else None)
        computed += 1

    # ⛑ `_valuation` IS TRANSPORT, AND IT MUST NOT REACH THE ROW. `calcs.
    # build_result_row` does `row.update(fund)`, so every key here becomes a
    # DataFrame column -- and this one holds a dict, which openpyxl cannot write.
    # That is the shape of a crash AFTER the report is half-built, the same way
    # an `inf` market cap once killed the coverage build after it had already
    # installed the workbook. Consumed here, dropped here, for every row
    # including the skipped and blanked ones.
    for fund in all_fundamentals.values():
        fund.pop("_valuation", None)
    return computed, blanked


def _convert_aggregates_to_usd(all_fundamentals, all_currencies, fx=None):
    """Convert company AGGREGATES to USD in place. Returns (converted, blanked).

    ⛑ AGGREGATES USE THE MAJOR UNIT (2026-09-08). This used
    `fetch_fx_rates(currency)` on the raw quote code, and Yahoo answers a minor
    code with a rate that is right for the PRICE and wrong for the AGGREGATES:
    `ZAcUSD=X` returns the CENTS rate (0.000625 in the live cache), so a
    Johannesburg market cap -- which Yahoo reports in whole rand -- was converted
    at 1/100. **Aspen Pharmacare's R65.6bn published as USD 43M in this report**,
    the same defect fixed in the coverage books on 2026-09-07 and not carried
    across to this lane. `fetch_aggregate_fx` requests the major unit and returns
    it under both keys, so looking up the raw quote currency cannot return the
    minor rate.

    ⛑ ONLY AN EXPLICIT "USD" BYPASSES CONVERSION (Codex, High). This read
    `all_currencies.get(yf_t, "USD")` and skipped on a falsy value, so a MISSING
    or EMPTY currency took the same path as a genuine US row and published the
    raw aggregate under a USD heading. That state is reachable --
    `provider_chain._is_success` documents currency as required and never checks
    it -- so a Japanese payload with `Mkt Cap = 4e12` and `currency = ""` would
    read as USD 4,000B and skew every cap-weighted basket built from it.

    ⛑ AN UNUSABLE RATE BLANKS THE FIELD. This was `continue`, which left a
    local-currency figure under a column headed USD -- a number that reads as
    converted and is not. Absent is a true statement; a wrong unit is not.

    `fx` is injectable for tests only; production passes None and fetches.
    """
    if fx is None:
        # ⛑ THE UNION OF BOTH CURRENCY SETS, not just the quote currencies.
        # `all_currencies` holds what each row QUOTES in. EV and Net Debt also
        # need the rate for what it REPORTS in, and for an ADR those differ by
        # definition -- Takeda quotes USD and reports JPY. Requesting only the
        # quote set left `fx` with no JPY, so `derive_valuation` could not prove
        # the reporting leg and BLANKED EV for the whole ADR book.
        #
        # It failed safe rather than publishing a wrong number, which is the
        # design working -- but silently blanking every ADR's EV is still a
        # functional outage, and it only looked fine in testing because the
        # verification passed `fx` in explicitly and so never exercised this
        # branch. Same trap as `test_the_production_path_fetches_AGGREGATE_rates`
        # in tests/test_fx_minor_units.py, which exists for exactly this reason.
        #
        # It would ALSO have partly worked by luck: JPY and EUR are quoted by
        # other rows in the universe, so those ADRs would resolve while a
        # reporting currency no row happens to quote would not. Correct by
        # caller coincidence is not correct.
        wanted = {c for c in all_currencies.values() if c and c != "USD"}
        wanted |= {(f.get("_valuation") or {}).get("financialCurrency")
                   for f in all_fundamentals.values()}
        wanted = {c for c in wanted if c and c != "USD"}
        fx = fetch_aggregate_fx(wanted) if wanted else {"USD": 1.0}

    converted, unconvertible = 0, []
    for yf_t, fund in all_fundamentals.items():
        currency = all_currencies.get(yf_t)
        if currency == "USD":
            continue
        # ⛑ LOOK UP THE MAJOR UNIT, not the raw quote code. `fetch_aggregate_fx`
        # already aliases the major rate under the minor key, so this is
        # belt-and-braces -- but without it the correctness of this function
        # depends on WHICH dict the caller handed it, which is caller convention
        # rather than a property. A test that passes a plain
        # `{"ZAc": <cents rate>}` proves the difference: it converted Aspen at
        # 1/100 until this line read `major_unit(currency)`.
        # ⛑ `_usable_rate`, NOT `is None` (Fable, Medium, 2026-09-08). Codex
        # round 3 fixed this class inside `derive_valuation` and it stayed live
        # in this loop, which converts `Mkt Cap`. `fetch_fx_rates` does a bare
        # `float(hist["Close"].iloc[-1])` with no finiteness check and caches the
        # result for 12h, so 0.0 and NaN are both reachable: a 0.0 rate published
        # `Mkt Cap = 0.0` under a USD heading, and NaN put a NaN into the
        # DataFrame and on into openpyxl. A rate must be usable, not merely present.
        rate = fx.get(major_unit(currency)) if currency else None
        if not _usable_rate(rate):
            for field in USD_AGGREGATE_FIELDS:
                if fund.get(field) is not None:
                    fund[field] = None
            _blank_ev_fields(fund)
            unconvertible.append((yf_t, currency or "<no currency>"))
            continue
        for field in USD_AGGREGATE_FIELDS:
            val = fund.get(field)
            if val is not None:
                fund[field] = val * rate
        converted += 1

    # ⛑ EV AND NET DEBT ARE RECOMPUTED, NOT CONVERTED -- for EVERY row, USD ones
    # included. The loop above is correct for `Mkt Cap` and only for `Mkt Cap`:
    # it applies ONE rate to three fields, and two of them are not in that
    # currency. See `providers/valuation.derive_valuation` for the measurement.
    # A US row is included because the vendor's EV is broken there too for a
    # separate reason -- argenx quotes AND reports USD and its EV was 25x its
    # market cap -- so "same currency" is not the same claim as "correct".
    ev_ok, ev_blanked = _recompute_ev_from_primitives(
        all_fundamentals, all_currencies, fx, skip={t for t, _ in unconvertible})

    if converted:
        logger.info("Converted Mkt Cap/EV/Net Debt to USD for %d non-USD tickers",
                    converted)
    if unconvertible:
        logger.warning(
            "No usable currency/FX rate for %d ticker(s); Mkt Cap/EV/Net Debt "
            "BLANKED rather than published unconverted: %s",
            len(unconvertible),
            ", ".join("%s(%s)" % (t, c) for t, c in sorted(unconvertible)[:15]))
    logger.info("EV/Net Debt computed from primitives for %d ticker(s)", ev_ok)
    if ev_blanked:
        logger.warning(
            "EV/Net Debt/EV-multiples BLANKED for %d ticker(s) whose inputs "
            "could not be proven (no fallback to the vendor's mixed-unit EV)",
            ev_blanked)
    return converted, len(unconvertible)

def _load_phase1_tickers():
    """Read all five-state position tickers from exports.

    Phase 1 of the historical-valuation feature enriches every name with a
    personal trading-state relationship: Portfolio, Researching, Following
    for Interest, Ready to Buy, Ready to Short. Following-for-Interest names
    are included because earnings-season context benefits from the same
    historical-valuation columns the other states rely on.

    Returns an empty set if the export files are missing — the caller treats
    that as "skip enrichment" and leaves the columns blank.
    """
    from pathlib import Path
    exports_dir = Path(__file__).resolve().parent.parent / "exports"
    tickers = set()
    for fname in (
        "portfolio.json",
        "researching.json",
        "following_for_interest.json",
        "ready_to_buy.json",
        "ready_to_short.json",
    ):
        path = exports_dir / fname
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tickers.update(data.keys())
        except Exception as e:
            logger.warning("Failed to read %s for history universe: %s", fname, e)
    return tickers


def _hist_columns_from_payload(payload):
    """Convert a fetch_history result dict into the history columns.

    Returns a dict with keys matching `HIST_COLS` (5Y block, 10Y block, and the
    `History Status` marker). Any field that can't be computed (insufficient
    data) is set to None — the Excel writer renders those as N/A. Nothing is
    ever defaulted to 0: a 0 in a P/E-min column would silently corrupt every
    downstream valuation screen.

    The 5Y and 10Y windows are two slices of the SAME annual series, so the 10Y
    columns add no API cost.
    """
    pe_ttm = payload.get("pe_ttm")
    pe_history = payload.get("pe_history") or []
    evs_history = payload.get("evs_history") or []

    pe_5 = stats_from_series(pe_history[:5], current=pe_ttm)
    pe_10 = stats_from_series(pe_history[:10], current=pe_ttm)
    # For EV/S vs-avg the "current" comparison uses the existing EV/S (TTM)
    # column populated by the provider chain, which isn't in scope here — pass
    # current=None and let the row-injection step below fill vs_avg_pct.
    evs_5 = stats_from_series(evs_history[:5], current=None)
    evs_10 = stats_from_series(evs_history[:10], current=None)

    return {
        "P/E (TTM)": pe_ttm,
        "P/E 5Y Avg": pe_5["avg"],
        "P/E 5Y +1σ": pe_5["plus_1sd"],
        "P/E 5Y -1σ": pe_5["minus_1sd"],
        "P/E 5Y Min": pe_5["min"],
        "P/E 5Y Max": pe_5["max"],
        "P/E vs 5Y Avg": pe_5["vs_avg_pct"],
        "EV/S 5Y Avg": evs_5["avg"],
        "EV/S 5Y +1σ": evs_5["plus_1sd"],
        "EV/S 5Y -1σ": evs_5["minus_1sd"],
        "EV/S 5Y Min": evs_5["min"],
        "EV/S 5Y Max": evs_5["max"],
        "EV/S vs 5Y Avg": None,  # filled later once TTM EV/S is in scope
        "P/E 10Y Avg": pe_10["avg"],
        "P/E 10Y +1σ": pe_10["plus_1sd"],
        "P/E 10Y -1σ": pe_10["minus_1sd"],
        "P/E 10Y Min": pe_10["min"],
        "P/E 10Y Max": pe_10["max"],
        "P/E vs 10Y Avg": pe_10["vs_avg_pct"],
        "EV/S 10Y Avg": evs_10["avg"],
        "EV/S 10Y +1σ": evs_10["plus_1sd"],
        "EV/S 10Y -1σ": evs_10["minus_1sd"],
        "EV/S 10Y Min": evs_10["min"],
        "EV/S 10Y Max": evs_10["max"],
        "EV/S vs 10Y Avg": None,  # filled later once TTM EV/S is in scope
        HIST_STATUS_COL: payload.get("status") or STATUS_NOT_ATTEMPTED,
    }


def _evs_vs_avg_pct(current_evs, evs_history, years=5):
    """Compute (current TTM EV/S - N-year avg) / N-year avg * 100. None-safe."""
    stats = stats_from_series((evs_history or [])[:years], current=current_evs)
    return stats["vs_avg_pct"]


def classify_sector_group(row):
    """Classify a coverage row into a sector group based on Sector (JP) / Subsector (JP)."""
    sector = str(row.get("Sector (JP)", "")).strip()
    subsector = str(row.get("Subsector (JP)", "")).strip()
    if sector in BIOPHARMA_VALUES or subsector in BIOPHARMA_VALUES:
        return "Biopharma"
    if sector in HC_SERVICES_MEDTECH_VALUES or subsector in HC_SERVICES_MEDTECH_VALUES:
        return "HC Svcs & MedTech"
    return "Following: Non-HC"


def _split_into_segments(result_df):
    """Split result_df into segment DataFrames keyed by tab name."""
    segments = {"Consolidated": result_df}
    sector = result_df.get("Sector (JP)", pd.Series(dtype="object")).fillna("").astype(str).str.strip()
    subsector = result_df.get("Subsector (JP)", pd.Series(dtype="object")).fillna("").astype(str).str.strip()
    biopharma_mask = sector.isin(BIOPHARMA_VALUES) | subsector.isin(BIOPHARMA_VALUES)
    hc_svcs_mask = sector.isin(HC_SERVICES_MEDTECH_VALUES) | subsector.isin(HC_SERVICES_MEDTECH_VALUES)
    non_hc_mask = ~biopharma_mask & ~hc_svcs_mask
    segments["Biopharma"] = result_df[biopharma_mask].reset_index(drop=True)
    segments["HC Svcs & MedTech"] = result_df[hc_svcs_mask].reset_index(drop=True)
    segments["Following: Non-HC"] = result_df[non_hc_mask].reset_index(drop=True)
    return segments


def _prepare_universe_rows(df, sample_mode=False, sample_set=None):
    """Normalize and deduplicate the coverage universe once per run."""
    working = df.copy()
    working["Ticker"] = working["Ticker"].fillna("").astype(str).str.strip()
    valid_mask = (working["Ticker"] != "") & (working["Ticker"] != "#N/A")
    if sample_mode and sample_set is not None:
        valid_mask &= working["Ticker"].str.upper().isin(sample_set)
    working = working.loc[valid_mask].drop_duplicates(subset=["Ticker"], keep="first").reset_index(drop=True)

    yf_tickers = []
    for row in working[["Ticker", "Company Name", "Exchange"]].fillna("").to_dict("records"):
        yf_tickers.append(
            normalize_ticker(
                str(row.get("Ticker", "")).strip(),
                str(row.get("Company Name", "")).strip(),
                str(row.get("Exchange", "")).strip(),
            )
        )
    working["_yf_ticker"] = yf_tickers
    return working


def _build_additions_summary(additions_path):
    """Parse the weekly additions markdown and return a plain-text summary.

    Extracts the recommendations table and company summaries section to
    include in the performance email body.
    """
    text = additions_path.read_text(encoding="utf-8")
    lines = []
    lines.append("=== Weekly Coverage Universe Additions ===\n")

    # Extract each row from the recommendations table (lines starting with | N |)
    for m in re.finditer(
        r"^\|\s*(\d+)\s*\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|",
        text, re.MULTILINE,
    ):
        num = m.group(1).strip()
        company = m.group(2).strip().strip("*")
        ticker = m.group(3).strip()
        trigger = m.group(9).strip()
        reason = m.group(11).strip()
        lines.append(f"{num}. {ticker} ({company}) — {trigger} — {reason}")

    # Extract company summaries
    summary_match = re.search(
        r"## Company Summaries\s*\n(.*?)(?=\n---|\n## |\Z)", text, re.DOTALL
    )
    if summary_match:
        lines.append("\n--- Company Summaries ---\n")
        lines.append(summary_match.group(1).strip())

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────

def _fmt_duration(seconds):
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def email_skip_reason(sample_mode, skip_email, email_enabled):
    """Return why the email step should be skipped, or None to send.

    Centralizes the gating so the standalone `cli.py performance` path honors
    the same rules as the orchestrator: sample runs and orchestrator-owned
    delivery (skip_email) are skipped, and EMAIL_ENABLED is the master switch.
    """
    if sample_mode:
        return "sample mode"
    if skip_email:
        return "skip_email"
    if not email_enabled:
        return "EMAIL_ENABLED=False"
    return None


def main(sample_mode=False, refresh=False, skip_email=False):
    """Generate performance reports.

    Args:
        sample_mode: Sample-mode preview run.
        refresh: Bypass cache.
        skip_email: Suppress the internal email send (set by weekly_report
            so the orchestrator can own email attachments and avoid duplicates).
    """
    global OUTPUT_XLSX, OUTPUT_HTML
    configure_logging()
    os.makedirs(REPORTS_DIR, exist_ok=True)
    use_cache = not refresh
    pipeline_start = time.monotonic()
    step_timings = []  # list of (step_name, duration_seconds, detail)

    if refresh:
        logger.info("Cache bypass enabled (--refresh)")

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

    df_unique = _prepare_universe_rows(df, sample_mode=sample_mode, sample_set=sample_set if sample_mode else None)
    logger.info("Found %s unique tickers", len(df_unique))

    # Normalize tickers for yfinance
    yf_tickers = [t for t in df_unique["_yf_ticker"].tolist() if t]
    ticker_map = {
        row["_yf_ticker"]: row["Ticker"]
        for row in df_unique[["Ticker", "_yf_ticker"]].to_dict("records")
        if row["_yf_ticker"]
    }

    logger.info("Downloading data for %s tickers (this may take several minutes)...", len(yf_tickers))
    t0 = time.monotonic()
    all_results = batch_download_prices(yf_tickers, use_cache=use_cache)

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

    step_timings.append(("prices", time.monotonic() - t0, f"{len(all_results)}/{len(yf_tickers)} tickers"))
    logger.info("Total tickers with data: %s", len(all_results))

    # Fetch Finnhub metrics for US tickers (no dot in symbol = likely US)
    finnhub_key = API_KEYS.get("FINNHUB_API_KEY")
    finnhub_data = {}
    t0 = time.monotonic()
    if finnhub_key:
        us_yf_tickers = [t for t in yf_tickers if "." not in t]
        logger.info("Fetching Finnhub fundamentals for %s US tickers...", len(us_yf_tickers))
        finnhub_data = fetch_finnhub_parallel(us_yf_tickers, finnhub_key)
        logger.info("Finnhub returned data for %s tickers", len(finnhub_data))
    step_timings.append(("finnhub", time.monotonic() - t0, f"{len(finnhub_data)} tickers"))

    # Fetch fundamentals via provider chain (handles FMP/yfinance/AV fallback)
    logger.info("Fetching fundamentals for %s tickers via provider chain...", len(yf_tickers))
    t0 = time.monotonic()
    all_fundamentals, all_is_ttm, all_currencies = fetch_all_fundamentals(
        yf_tickers, finnhub_data=finnhub_data, max_workers=10, use_cache=use_cache
    )
    fund_count = sum(1 for v in all_fundamentals.values() if v.get("Mkt Cap") is not None)
    step_timings.append(("fundamentals", time.monotonic() - t0, f"{fund_count}/{len(yf_tickers)} tickers"))
    logger.info("Fundamentals loaded for %s tickers", fund_count)

    # Convert Mkt Cap, EV, Net Debt to USD. Extracted so it can be driven
    # directly by tests -- the first version of this fix was pinned only by
    # helper unit tests and a source-text assertion, and Codex showed that five
    # of the six would still have passed with this lane reverted.
    _convert_aggregates_to_usd(all_fundamentals, all_currencies)

    # Build ticker health data (no extra API calls)
    health_data = build_ticker_health_data(df_unique, yf_tickers, ticker_map, all_results, all_fundamentals)
    logger.info("Ticker health: %s issues found", health_data["total_issues"])

    # Fetch 5-year P/E and EV/S history for Phase 1 universe (all five
    # position states: Portfolio ∪ Researching ∪ Following for Interest ∪
    # Ready to Buy ∪ Ready to Short).
    # Cached 30 days; cold-cache cost is ~3 FMP calls per ticker × ~50–100 tickers = trivial.
    # Runs in sample mode too — useful for verifying the new columns format correctly
    # when a sample ticker overlaps with Phase 1.
    phase1_universe = _load_phase1_tickers()
    history_data = {}
    fmp_key_for_history = API_KEYS.get("FMP_API_KEY")
    if fmp_key_for_history:
        t0 = time.monotonic()
        all_universe_tickers = set(df_unique["Ticker"].dropna().astype(str).str.strip()) - {""}
        # Two-speed scope (widened to the full universe 2026-07-19):
        #  - Position names are fetched LIVE, exactly as before, so the set JP
        #    looks at every week is never stale.
        #  - Every OTHER universe name is read cache-only — zero API calls — so
        #    the report's runtime never depends on a cold full-universe fetch.
        #    `cli.py history-backfill` is what populates that cache (weekly,
        #    resumable). Names it hasn't reached yet come back
        #    status="not_attempted" and render as N/A, never as 0.
        cache_only_tickers = sorted(all_universe_tickers - set(phase1_universe))
        if phase1_universe:
            logger.info("Fetching history LIVE for %s position tickers...", len(phase1_universe))
            history_data = fetch_history_parallel(
                sorted(phase1_universe), fmp_key_for_history,
                max_workers=10, use_cache=use_cache,
            )
        if cache_only_tickers:
            logger.info("Reading cached history for %s non-position tickers...", len(cache_only_tickers))
            history_data.update(fetch_history_parallel(
                cache_only_tickers, fmp_key_for_history,
                max_workers=10, use_cache=True, cache_only=True, progress_every=0,
            ))
        covered = sum(1 for h in history_data.values() if h.get("status") == "ok")
        unattempted = sum(1 for h in history_data.values() if h.get("status") == STATUS_NOT_ATTEMPTED)
        step_timings.append((
            "history", time.monotonic() - t0,
            f"{covered}/{len(history_data)} tickers ({unattempted} not yet backfilled)",
        ))
        logger.info(
            "History: %s/%s tickers populated (%s never attempted — run `cli.py history-backfill`)",
            covered, len(history_data), unattempted,
        )
    else:
        logger.warning("Skipping history enrichment — FMP_API_KEY not set")

    # Forward annual EPS estimates for the same Phase 1 universe — drives the
    # P/E vs forward-2yr-growth scatter. FMP-only, 30-day cached (same scope +
    # cadence rationale as the 5Y history above).
    estimates_data = {}
    if phase1_universe:
        fmp_key_for_est = API_KEYS.get("FMP_API_KEY")
        if fmp_key_for_est:
            t0 = time.monotonic()
            logger.info("Fetching forward EPS estimates for %s Phase 1 tickers...", len(phase1_universe))
            estimates_data = fetch_estimates_parallel(
                sorted(phase1_universe), fmp_key_for_est,
                max_workers=10, use_cache=use_cache,
            )
            est_covered = sum(1 for rows in estimates_data.values() if rows)
            step_timings.append(("estimates", time.monotonic() - t0, f"{est_covered}/{len(phase1_universe)} tickers"))
            logger.info("Estimates loaded for %s/%s Phase 1 tickers", est_covered, len(phase1_universe))
        else:
            logger.info("Skipping forward-estimates fetch — FMP_API_KEY not set")

    # Calculate returns
    results = []
    for row in df_unique.to_dict("records"):
        orig_ticker = row["Ticker"]
        company = str(row.get("Company Name", "")).strip()
        exchange = str(row.get("Exchange", "")).strip()
        yf_t = row.get("_yf_ticker")
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

        # Phase 1 historical valuation enrichment. Tickers outside the universe
        # get explicit None for every HIST_COLS key so DataFrame construction
        # doesn't drop the columns when no row has values.
        hist_payload = history_data.get(orig_ticker)
        if hist_payload:
            hist_cols = _hist_columns_from_payload(hist_payload)
            # Fill EV/S vs 5Y/10Y Avg from the live TTM EV/S in `fund`
            current_evs = fund.get("EV/S")
            evs_hist = hist_payload.get("evs_history") or []
            hist_cols["EV/S vs 5Y Avg"] = _evs_vs_avg_pct(current_evs, evs_hist, years=5)
            hist_cols["EV/S vs 10Y Avg"] = _evs_vs_avg_pct(current_evs, evs_hist, years=10)
            result_row.update(hist_cols)
        else:
            # No payload at all — mark explicitly as never attempted rather than
            # leaving an ambiguous blank.
            result_row.update({col: None for col in HIST_COLS})
            result_row[HIST_STATUS_COL] = STATUS_NOT_ATTEMPTED

        results.append(result_row)

    result_df = pd.DataFrame(results)
    info_cols = ["Ticker", "Company Name"] + VAL_COLS + ["Sector (JP)", "Subsector (JP)", "Core", "YF Sector", "YF Industry", "Country (ISO)", "Exchange"]

    # Persist the coverage perf snapshot so downstream steps (e.g. movers report)
    # can read it without re-running the price/fundamentals pipeline. Pickle
    # keeps the float types intact; the file lives under cache/ so it's
    # gitignored. Sample-mode runs are skipped because they're partial.
    if not sample_mode:
        from config import CACHE_DIR
        perf_snapshot_dir = CACHE_DIR / "perf"
        perf_snapshot_dir.mkdir(parents=True, exist_ok=True)
        perf_snapshot_path = perf_snapshot_dir / f"perf_df_{TODAY}.pkl"
        try:
            result_df.to_pickle(perf_snapshot_path)
            logger.info("Saved perf snapshot: %s", perf_snapshot_path)
        except Exception as e:
            logger.warning("Failed to save perf snapshot: %s", e)

    # ── Step tracking ────────────────────────────────────────────────────────
    step_results = {}

    def run_step(name, fn, *args, **kwargs):
        """Run a pipeline step, catching and logging failures."""
        t0 = time.monotonic()
        try:
            result = fn(*args, **kwargs)
            step_results[name] = "ok"
            step_timings.append((name, time.monotonic() - t0, "ok"))
            return result
        except Exception as e:
            logger.warning("Step '%s' failed: %s", name, e)
            step_results[name] = f"failed: {e}"
            step_timings.append((name, time.monotonic() - t0, f"failed: {e}"))
            return None

    # ============ P/E vs FORWARD-2YR-GROWTH SCATTER (Phase 1) ============
    # P/E (TTM, FMP-consistent) vs annualized forward 2-year EPS-growth, for the
    # positions/research set. S&P 500 is intentionally excluded — that tab is
    # built price-only (no fundamentals) to keep the run fast, so it has no P/E.
    def _generate_pe_growth_chart():
        from reporting.charts import render_pe_growth_scatter
        today = datetime.now().date()
        phase1 = phase1_universe or set()
        rows = []
        for rec in result_df.to_dict("records"):
            t = rec.get("Ticker")
            if t not in phase1:
                continue
            pe = rec.get("P/E (TTM)")
            growth = forward_2yr_eps_growth_pct(estimates_data.get(t), today)
            if pe is None or growth is None:
                continue
            rows.append({
                "ticker": t, "pe": pe, "growth": growth,
                "sector": rec.get("Sector (JP)"), "mkt_cap": rec.get("Mkt Cap"),
            })
        n = render_pe_growth_scatter(rows, OUTPUT_PE_GROWTH_PNG)
        logger.info("P/E-vs-growth scatter plotted %s/%s Phase 1 names", n, len(phase1))

    run_step("pe_growth_chart", _generate_pe_growth_chart)

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

            logger.info("Building S&P 500 benchmark tab in price-only mode for speed")

            sp500_rows = []
            for t, info_entry in sp500_all:
                returns = compute_returns(sp500_results_data.get(t))
                row_data = build_result_row(
                    ticker=t,
                    company=info_entry.get("Company Name", ""),
                    sector=info_entry.get("GICS Sector", ""),
                    subsector=info_entry.get("GICS Sub-Industry", ""),
                    yf_sector=info_entry.get("GICS Sector", ""),
                    yf_industry=info_entry.get("GICS Sub-Industry", ""),
                    country_iso="USA", exchange="",
                    returns=returns,
                    fund={col: None for col in FUND_COLS + VAL_COLS},
                    is_ttm={"Rev Grw": False, "EPS Grw": False},
                    currency="USD",
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
    # EMAIL_ENABLED is the master transport switch (config.py). Honor it here so
    # the standalone `cli.py performance` command behaves the same as the
    # orchestrator paths — otherwise a manual run emails even when the flag is
    # off. Referenced via the module so tests can monkeypatch config.EMAIL_ENABLED.
    import config
    skip_reason = email_skip_reason(sample_mode, skip_email, config.EMAIL_ENABLED)
    if skip_reason is not None:
        logger.info("Skipping email (%s)", skip_reason)
        step_results["email"] = "skipped" if skip_reason in ("sample mode", "skip_email") else f"skipped: {skip_reason}"
    else:
        gmail_addr = API_KEYS.get("GMAIL_ADDRESS")
        gmail_pass = API_KEYS.get("GMAIL_APP_PASSWORD")
        if gmail_addr and gmail_pass and html_paths:
            def _send_email():
                # Look for weekly additions report to attach and summarize
                additions_pattern = REPORTS_DIR / f"weekly_coverage_universe_additions_{TODAY}.md"
                extra_attachments = []
                body_lines = []
                if additions_pattern.exists():
                    extra_attachments.append(additions_pattern)
                    body_lines.append(_build_additions_summary(additions_pattern))
                # Attach the P/E-vs-growth scatter PNG if it rendered this run.
                if OUTPUT_PE_GROWTH_PNG.exists():
                    extra_attachments.append(OUTPUT_PE_GROWTH_PNG)
                # Pick up the movers HTML if it was generated this run.
                movers_html = REPORTS_DIR / f"coverage_movers_{TODAY}.html"
                if movers_html.exists() and movers_html not in html_paths:
                    html_paths.append(movers_html)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                body_lines.append(f"Generated {timestamp}.\n")
                body_lines.append("--- Attached Files ---")
                for p in list(extra_attachments) + [str(p) for p in html_paths]:
                    body_lines.append(f"  - {os.path.basename(str(p))}")
                body_text = "\n".join(body_lines)
                logger.info("Emailing %s HTML report(s) + %s extra attachment(s)...",
                            len(html_paths), len(extra_attachments))
                send_email_report(gmail_addr, gmail_pass, html_paths, TODAY,
                                  extra_attachments=extra_attachments,
                                  body_text=body_text)
            run_step("email", _send_email)
        else:
            logger.info("Skipping email (GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set in .env)")
            step_results["email"] = "skipped"

    # ============ SUMMARY ============
    total_duration = time.monotonic() - pipeline_start
    logger.info("-- Pipeline Summary --")
    for step_name, status in step_results.items():
        logger.info("  %-15s %s", step_name, status)

    logger.info("-- Step Timings --")
    for step_name, duration, detail in step_timings:
        logger.info("  %-20s %10s  %s", step_name, _fmt_duration(duration), detail)
    logger.info("  %-20s %10s", "TOTAL", _fmt_duration(total_duration))

    # Write timing log to reports/
    timing_log_path = REPORTS_DIR / "performance_timing.jsonl"
    timing_entry = {
        "timestamp": datetime.now().isoformat(),
        "date": TODAY,
        "sample_mode": sample_mode,
        "refresh": refresh,
        "total_seconds": round(total_duration, 1),
        "total_formatted": _fmt_duration(total_duration),
        "steps": [
            {"step": name, "seconds": round(dur, 1), "detail": detail}
            for name, dur, detail in step_timings
        ],
    }
    try:
        with open(timing_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(timing_entry) + "\n")
        logger.info("Timing log appended to %s", timing_log_path)
    except Exception as e:
        logger.warning("Failed to write timing log: %s", e)

    logger.info("Done!")


if __name__ == "__main__":
    main(sample_mode="--sample" in sys.argv)
