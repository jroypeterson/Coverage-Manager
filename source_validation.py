"""Cross-check overlapping market/fundamental values across providers."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
from statistics import median

import pandas as pd

from config import API_KEYS, CSV_PATH, REPORTS_DIR, SAMPLE_TICKERS, TODAY
from logging_utils import configure_logging, get_logger, log_exception
from providers.finnhub_provider import fetch_metrics
from providers.fmp_provider import fetch_fundamentals as fetch_fmp_fundamentals
from providers.yfinance_provider import fetch_fundamentals as fetch_yf_fundamentals
from ticker_utils import normalize_ticker

logger = get_logger("source_validation")

OUTPUT_CSV = REPORTS_DIR / f"source_crosscheck_{TODAY}.csv"
OUTPUT_JSON = REPORTS_DIR / f"source_crosscheck_{TODAY}.json"

FIELD_RULES = {
    "Price": {"mode": "relative_pct", "threshold": 5.0},
    "Mkt Cap": {"mode": "relative_pct", "threshold": 20.0},
    "Enterprise Value": {"mode": "relative_pct", "threshold": 20.0},
    "Net Debt": {"mode": "relative_pct", "threshold": 30.0},
    "Fwd P/E": {"mode": "relative_pct", "threshold": 25.0},
    "EV/EBITDA": {"mode": "relative_pct", "threshold": 25.0},
    "EV/S": {"mode": "relative_pct", "threshold": 25.0},
    "Gross Mgn": {"mode": "absolute", "threshold": 10.0},
    "Op Mgn": {"mode": "absolute", "threshold": 10.0},
    "ROE": {"mode": "absolute", "threshold": 12.0},
    "Rev Grw": {"mode": "absolute", "threshold": 15.0},
    "EPS Grw": {"mode": "absolute", "threshold": 15.0},
    "PEG": {"mode": "relative_pct", "threshold": 35.0},
}
MONETARY_FIELDS = {"Price", "Mkt Cap", "Enterprise Value", "Net Debt"}


def _prepare_universe_rows(df, sample_mode=False):
    """Normalize and deduplicate the universe rows for a validation run."""
    working = df.copy()
    working["Ticker"] = working["Ticker"].fillna("").astype(str).str.strip()
    valid_mask = (working["Ticker"] != "") & (working["Ticker"] != "#N/A")
    if sample_mode:
        sample_set = {t.upper() for t in SAMPLE_TICKERS}
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


def _finnhub_snapshot(metrics):
    """Map Finnhub metrics into the shared field names."""
    if not metrics:
        return {}
    snapshot = {}
    mapping = {
        "Rev Grw": "revenueGrowthTTMYoy",
        "EPS Grw": "epsGrowthTTMYoy",
        "PEG": "pegTTM",
    }
    for output_field, input_field in mapping.items():
        value = metrics.get(input_field)
        if value is not None:
            snapshot[output_field] = value
    return snapshot


def _relative_delta_pct(values):
    """Return relative delta percent for a sequence of numeric values."""
    if len(values) < 2:
        return None
    baseline = median(abs(v) for v in values)
    if baseline == 0:
        return None
    return abs(max(values) - min(values)) / baseline * 100.0


def compare_field(field, source_values, source_currencies=None):
    """Compare a single field across providers."""
    populated = {
        source: float(value)
        for source, value in source_values.items()
        if value is not None
    }
    if len(populated) < 2:
        return None

    if field in MONETARY_FIELDS and source_currencies:
        comparable_currencies = {
            currency
            for source, currency in source_currencies.items()
            if source in populated and currency
        }
        if len(comparable_currencies) > 1:
            return None

    min_source = min(populated, key=populated.get)
    max_source = max(populated, key=populated.get)
    min_value = populated[min_source]
    max_value = populated[max_source]
    abs_delta = abs(max_value - min_value)
    rel_delta_pct = _relative_delta_pct(list(populated.values()))

    rule = FIELD_RULES[field]
    if rule["mode"] == "absolute":
        flagged = abs_delta >= rule["threshold"]
    else:
        flagged = rel_delta_pct is not None and rel_delta_pct >= rule["threshold"]

    return {
        "field": field,
        "sources_compared": ",".join(sorted(populated)),
        "min_source": min_source,
        "min_value": round(min_value, 4),
        "max_source": max_source,
        "max_value": round(max_value, 4),
        "abs_delta": round(abs_delta, 4),
        "rel_delta_pct": None if rel_delta_pct is None else round(rel_delta_pct, 2),
        "threshold_mode": rule["mode"],
        "threshold": rule["threshold"],
        "flagged": flagged,
    }


def _collect_ticker_snapshots(row, use_cache=True):
    """Fetch comparable values for a single ticker from each provider."""
    ticker = row["Ticker"]
    yf_ticker = row["_yf_ticker"]
    if not yf_ticker:
        return {"ticker": ticker, "company": row.get("Company Name", ""), "comparisons": [], "skipped": "no_yf_ticker"}

    snapshots = {}

    try:
        yf_data, _, yf_currency = fetch_yf_fundamentals(yf_ticker, finnhub_metrics=None, use_cache=use_cache)
        if any(v is not None for v in yf_data.values()):
            snapshots["yfinance"] = {"values": yf_data, "currency": yf_currency or ""}
    except Exception as exc:
        log_exception(logger, f"yfinance cross-check failed for {ticker}", exc)

    fmp_key = API_KEYS.get("FMP_API_KEY", "")
    if fmp_key:
        try:
            fmp_data, _, fmp_currency = fetch_fmp_fundamentals(ticker, fmp_key, use_cache=use_cache)
            if any(v is not None for v in fmp_data.values()):
                snapshots["fmp"] = {"values": fmp_data, "currency": fmp_currency or ""}
        except Exception as exc:
            log_exception(logger, f"FMP cross-check failed for {ticker}", exc)

    finnhub_key = API_KEYS.get("FINNHUB_API_KEY", "")
    if finnhub_key and "." not in yf_ticker:
        try:
            finnhub_metrics = fetch_metrics(yf_ticker, finnhub_key, use_cache=use_cache)
            finnhub_values = _finnhub_snapshot(finnhub_metrics)
            if finnhub_values:
                snapshots["finnhub"] = {"values": finnhub_values, "currency": ""}
        except Exception as exc:
            log_exception(logger, f"Finnhub cross-check failed for {ticker}", exc)

    comparisons = []
    for field in FIELD_RULES:
        comparison = compare_field(
            field,
            {
                source: payload["values"].get(field)
                for source, payload in snapshots.items()
            },
            {
                source: payload["currency"]
                for source, payload in snapshots.items()
            },
        )
        if comparison is None:
            continue
        comparison["ticker"] = ticker
        comparison["company"] = row.get("Company Name", "")
        comparison["yf_ticker"] = yf_ticker
        comparison["currencies"] = ",".join(
            sorted({payload["currency"] for payload in snapshots.values() if payload["currency"]})
        )
        comparisons.append(comparison)

    return {
        "ticker": ticker,
        "company": row.get("Company Name", ""),
        "comparisons": comparisons,
        "sources": sorted(snapshots),
    }


def run_crosscheck(sample_mode=False, refresh=False, max_workers=8):
    """Run provider cross-checks for the current universe."""
    configure_logging()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    use_cache = not refresh

    df = pd.read_csv(CSV_PATH)
    df_unique = _prepare_universe_rows(df, sample_mode=sample_mode)
    rows = df_unique.to_dict("records")
    logger.info("Cross-checking %d tickers across providers", len(rows))

    ticker_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_collect_ticker_snapshots, row, use_cache): row["Ticker"] for row in rows}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                ticker_results.append(future.result())
            except Exception as exc:
                log_exception(logger, f"Cross-check worker failed for {ticker}", exc)

    comparison_rows = []
    for result in ticker_results:
        comparison_rows.extend(result.get("comparisons", []))
    comparison_rows.sort(key=lambda row: (not row["flagged"], row["ticker"], row["field"]))

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticker", "company", "yf_ticker", "field", "sources_compared",
                "min_source", "min_value", "max_source", "max_value",
                "abs_delta", "rel_delta_pct", "threshold_mode", "threshold",
                "currencies", "flagged",
            ],
        )
        writer.writeheader()
        writer.writerows(comparison_rows)

    summary = {
        "date": TODAY,
        "sample_mode": sample_mode,
        "refresh": refresh,
        "tickers_checked": len(rows),
        "comparisons": len(comparison_rows),
        "flagged": sum(1 for row in comparison_rows if row["flagged"]),
        "output_csv": str(OUTPUT_CSV),
        "output_json": str(OUTPUT_JSON),
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "rows": comparison_rows}, handle, indent=2)

    logger.info(
        "Cross-check complete: %d comparisons, %d flagged",
        summary["comparisons"],
        summary["flagged"],
    )
    return summary


def main(sample_mode=False, refresh=False):
    """CLI entry point."""
    summary = run_crosscheck(sample_mode=sample_mode, refresh=refresh)
    print(
        f"Cross-check complete: {summary['comparisons']} comparisons, "
        f"{summary['flagged']} flagged, CSV={summary['output_csv']}"
    )


if __name__ == "__main__":
    main()
