"""Detect delisted, acquired, or recycled tickers in the coverage universe.

For each ticker in the universe CSV, fetch a lightweight identity probe from
yfinance (`quoteType`, `longName`, `shortName`) and compare against the
universe-recorded `Company Name`. A meaningful mismatch suggests the ticker
has been recycled (e.g. an operating company was acquired/de-listed and the
symbol is now used by an ETF or another issuer).

Output:
  - `reports/delisted_check_{date}.csv`  — flagged rows with reason
  - `reports/delisted_check_{date}.md`   — human-readable summary

Flagged tickers stay in the universe — this is a non-gating warning. The user
moves them to `data/delisted_tickers.csv` manually after confirming.
"""

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from difflib import SequenceMatcher

import pandas as pd

from cache import cache_get, cache_set
from config import CSV_PATH, REPORTS_DIR
from logging_utils import get_logger, log_exception
from ticker_utils import normalize_company_for_comparison, normalize_ticker

logger = get_logger("delisted_check")

IDENTITY_CACHE_NS = "identity"
IDENTITY_CACHE_TTL_HOURS = 24.0 * 7  # weekly refresh is enough

# quoteType values from yfinance that should never appear in the equity universe
NON_EQUITY_QUOTE_TYPES = {"ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY"}

# Below this normalized-name similarity score, flag as a likely mismatch
NAME_SIMILARITY_THRESHOLD = 0.55


def _fetch_identity(yf_ticker, use_cache=True):
    """Fetch quoteType + longName/shortName for a single ticker from yfinance.

    Returns dict with {quoteType, longName, shortName} or {} on failure.
    """
    if use_cache:
        cached = cache_get(IDENTITY_CACHE_NS, yf_ticker, IDENTITY_CACHE_TTL_HOURS)
        if cached is not None:
            return cached

    try:
        import yfinance as yf

        info = yf.Ticker(yf_ticker).info or {}
        identity = {
            "quoteType": info.get("quoteType") or "",
            "longName": info.get("longName") or "",
            "shortName": info.get("shortName") or "",
        }
        if use_cache:
            cache_set(IDENTITY_CACHE_NS, yf_ticker, identity)
        return identity
    except Exception as e:
        log_exception(logger, f"Identity lookup failed for {yf_ticker}", e)
        return {}


def _name_similarity(recorded_name, yf_long, yf_short):
    """Best similarity ratio between recorded name and yfinance long/short names.

    Both are first normalized (drop Inc/Corp/etc.) so corp-suffix differences
    don't trigger false positives.
    """
    recorded = normalize_company_for_comparison(recorded_name)
    if not recorded:
        return 1.0  # no recorded name to compare against; don't flag

    best = 0.0
    for yf_name in (yf_long, yf_short):
        if not yf_name:
            continue
        candidate = normalize_company_for_comparison(yf_name)
        if not candidate:
            continue
        score = SequenceMatcher(None, recorded, candidate).ratio()
        # Substring match gets a floor of 0.85 so e.g.
        # "premier" vs "premier inc holdings" doesn't get penalized.
        if recorded in candidate or candidate in recorded:
            score = max(score, 0.85)
        best = max(best, score)
    return best


def _classify(row, identity):
    """Return (flagged: bool, reason: str) for a single ticker.

    No identity data => 'no yfinance data' (likely delisted).
    Non-equity quoteType => 'recycled to {ETF|MUTUALFUND|...}'.
    Low name similarity => 'name mismatch (recorded vs yfinance)'.
    Otherwise unflagged.
    """
    quote_type = (identity.get("quoteType") or "").upper()
    long_name = identity.get("longName") or ""
    short_name = identity.get("shortName") or ""
    recorded_name = str(row.get("Company Name", "") or "")

    if not identity or (not quote_type and not long_name and not short_name):
        return True, "no yfinance data (likely delisted)"

    if quote_type in NON_EQUITY_QUOTE_TYPES:
        return True, f"ticker recycled to non-equity instrument ({quote_type})"

    if quote_type and quote_type not in {"EQUITY", "ADR", ""}:
        # Surface any other unexpected types but don't hard-flag
        pass

    score = _name_similarity(recorded_name, long_name, short_name)
    if score < NAME_SIMILARITY_THRESHOLD:
        return True, (
            f"company name mismatch (similarity={score:.2f}); recorded="
            f"{recorded_name!r}, yfinance={long_name or short_name!r}"
        )

    return False, ""


def check_universe(csv_path=None, max_workers=10, use_cache=True):
    """Run the delisted/recycled check across the full universe CSV.

    Returns dict with keys:
      - flagged: list of dicts with ticker/recorded_name/quoteType/yf_name/reason
      - checked: total tickers checked
      - missing_data: count of tickers yfinance returned nothing for
    """
    csv_path = csv_path or CSV_PATH
    df = pd.read_csv(csv_path)

    rows = df.to_dict(orient="records")
    logger.info("Probing yfinance identity for %d tickers...", len(rows))

    pairs = []
    for row in rows:
        yf_t = normalize_ticker(
            row.get("Ticker", ""),
            company_name=row.get("Company Name", ""),
            exchange=row.get("Exchange", ""),
        )
        if not yf_t:
            continue
        pairs.append((row, yf_t))

    identities = {}

    def _fetch_one(yf_t):
        return yf_t, _fetch_identity(yf_t, use_cache=use_cache)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_fetch_one, yf_t) for _, yf_t in pairs]
        for i, fut in enumerate(as_completed(futures), start=1):
            yf_t, identity = fut.result()
            identities[yf_t] = identity
            if i % 100 == 0:
                logger.info("  progress: %d/%d", i, len(pairs))

    flagged = []
    missing_data = 0
    for row, yf_t in pairs:
        identity = identities.get(yf_t, {})
        if not identity or (
            not identity.get("quoteType")
            and not identity.get("longName")
            and not identity.get("shortName")
        ):
            missing_data += 1
        is_flagged, reason = _classify(row, identity)
        if is_flagged:
            flagged.append({
                "ticker": row.get("Ticker", ""),
                "yf_ticker": yf_t,
                "recorded_name": row.get("Company Name", ""),
                "yf_long_name": identity.get("longName", ""),
                "yf_short_name": identity.get("shortName", ""),
                "quote_type": identity.get("quoteType", ""),
                "sector_jp": row.get("Sector (JP)", ""),
                "subsector_jp": row.get("Subsector (JP)", ""),
                "reason": reason,
            })

    flagged.sort(key=lambda r: r["ticker"])

    return {
        "checked": len(pairs),
        "flagged": flagged,
        "missing_data": missing_data,
    }


def write_report(result, reports_dir=None, run_date=None):
    """Write CSV + markdown reports for flagged tickers.

    Returns dict of {csv_path, md_path}.
    """
    reports_dir = reports_dir or REPORTS_DIR
    run_date = run_date or date.today().strftime("%Y-%m-%d")
    reports_dir.mkdir(parents=True, exist_ok=True)

    csv_path = reports_dir / f"delisted_check_{run_date}.csv"
    md_path = reports_dir / f"delisted_check_{run_date}.md"

    fieldnames = [
        "ticker", "yf_ticker", "recorded_name", "yf_long_name", "yf_short_name",
        "quote_type", "sector_jp", "subsector_jp", "reason",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in result["flagged"]:
            writer.writerow(row)

    lines = []
    lines.append(f"# Delisted / recycled ticker check — {run_date}")
    lines.append("")
    lines.append(f"- Checked: {result['checked']} tickers")
    lines.append(f"- Flagged: {len(result['flagged'])}")
    lines.append(f"- No yfinance data: {result['missing_data']}")
    lines.append("")
    if result["flagged"]:
        lines.append("| Ticker | Recorded Name | yfinance Name | quoteType | Reason |")
        lines.append("|--------|---------------|---------------|-----------|--------|")
        for row in result["flagged"]:
            yf_name = row["yf_long_name"] or row["yf_short_name"]
            lines.append(
                f"| {row['ticker']} | {row['recorded_name']} | {yf_name} | "
                f"{row['quote_type']} | {row['reason']} |"
            )
    else:
        lines.append("_No flagged tickers — universe identity matches yfinance._")
    lines.append("")
    lines.append(
        "Review flagged rows. To mark a ticker as delisted/acquired, "
        "remove it from `data/coverage_universe_tickers.csv` and append "
        "an entry to `data/delisted_tickers.csv` with last-known sector "
        "and market cap data."
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return {"csv_path": str(csv_path), "md_path": str(md_path)}


def main(use_cache=True):
    """CLI entry point: run the check and write the report."""
    result = check_universe(use_cache=use_cache)
    paths = write_report(result)
    logger.info(
        "Delisted check: %d/%d flagged (missing data: %d)",
        len(result["flagged"]), result["checked"], result["missing_data"],
    )
    logger.info("  CSV: %s", paths["csv_path"])
    logger.info("  MD:  %s", paths["md_path"])
    if result["flagged"]:
        for row in result["flagged"][:20]:
            logger.warning(
                "  FLAG %s (recorded=%r, yf=%r, qt=%s): %s",
                row["ticker"], row["recorded_name"],
                row["yf_long_name"] or row["yf_short_name"],
                row["quote_type"], row["reason"],
            )
        if len(result["flagged"]) > 20:
            logger.warning("  ... and %d more (see report)", len(result["flagged"]) - 20)
    return result
