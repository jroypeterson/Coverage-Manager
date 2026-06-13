"""Detect delisted, acquired, or recycled tickers in the coverage universe.

For each ticker in the universe CSV, fetch a lightweight identity probe from
yfinance (`quoteType`, `longName`, `shortName`) and compare against the
universe-recorded `Company Name`. A meaningful mismatch suggests the ticker
has been recycled (e.g. an operating company was acquired/de-listed and the
symbol is now used by an ETF or another issuer).

In addition to the `.info` identity probe, a **price-recency probe** checks
whether yfinance still serves recent daily bars for the ticker. This is the
reliable tell for a clean acquisition/take-private: Yahoo keeps the stale
`.info` metadata (longName etc.) populated for months after a name stops
trading, so the identity probe alone misses these — but the price feed goes
empty immediately. A ticker with a populated `.info` and zero recent bars is
flagged `no recent price data (likely delisted/renamed)`. This is what would
have caught EXAS (Abbott, 2026-03), HOLX (Blackstone/TPG, 2026-04), and the
MPW→MPT / GMRE→XRN rebrands instead of letting them linger in the universe.

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

# v2: added price-recency fields (last_close_date / price_probe_ran / price_stale).
# Bumping the namespace ignores pre-v2 entries so the price probe activates on
# the next run instead of waiting up to a TTL for old caches to roll over.
IDENTITY_CACHE_NS = "identity_v2"
IDENTITY_CACHE_TTL_HOURS = 24.0 * 7  # weekly refresh is enough

# quoteType values from yfinance that should never appear in the equity universe
NON_EQUITY_QUOTE_TYPES = {"ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY"}

# Below this normalized-name similarity score, flag as a likely mismatch
NAME_SIMILARITY_THRESHOLD = 0.55

# A live ticker always has a daily bar within the last few trading sessions.
# If yfinance serves no bar within this many calendar days, treat the price
# feed as dead (acquired / taken private / renamed). 10 days clears a normal
# long weekend / holiday cluster with margin while still catching a stop in
# trading promptly on the weekly cadence.
PRICE_STALE_DAYS = 10


def _probe_recent_price(yf_obj):
    """Return (probe_ran: bool, last_close_date: str|"") for a yfinance Ticker.

    Pulls ~1 month of daily bars and reports the most recent bar date. A clean
    acquisition/take-private leaves `.info` stale but kills the price feed, so
    an empty/old result here is the reliable delisted signal.

    `probe_ran` is False ONLY when the history pull itself raised (transient
    network / rate-limit error) so the caller can avoid false-flagging on an
    infrastructure blip vs. a genuinely dead feed (empty result with no error).
    `raise_errors=True` is essential: yfinance otherwise swallows 429s/network
    errors and returns an empty frame, which would masquerade as a dead feed.
    """
    try:
        hist = yf_obj.history(period="1mo", auto_adjust=True, raise_errors=True)
    except Exception:
        return False, ""  # transient — do NOT treat as delisted
    try:
        if hist is None or hist.empty or "Close" not in hist:
            return True, ""  # ran cleanly, genuinely no bars → dead feed
        closes = hist["Close"].dropna()
        if closes.empty:
            return True, ""
        return True, closes.index[-1].date().isoformat()
    except Exception:
        return True, ""


def _fetch_identity(yf_ticker, use_cache=True):
    """Fetch identity + price-recency probe for a single ticker from yfinance.

    Returns dict with {quoteType, longName, shortName, last_close_date,
    price_probe_ran, price_stale} or {} on a total failure.

    Staleness is decided HERE, at probe time, and frozen into `price_stale` —
    not recomputed at classify time. The identity cache (7-day TTL) would
    otherwise let a cached `last_close_date` "age into" staleness and falsely
    flag a live ticker that was fresh when probed.
    """
    if use_cache:
        cached = cache_get(IDENTITY_CACHE_NS, yf_ticker, IDENTITY_CACHE_TTL_HOURS)
        if cached is not None:
            return cached

    try:
        import yfinance as yf

        yf_obj = yf.Ticker(yf_ticker)
        info = yf_obj.info or {}
        price_probe_ran, last_close_date = _probe_recent_price(yf_obj)
        identity = {
            "quoteType": info.get("quoteType") or "",
            "longName": info.get("longName") or "",
            "shortName": info.get("shortName") or "",
            "last_close_date": last_close_date,
            "price_probe_ran": price_probe_ran,
            # frozen-at-probe-time decision (avoids the cache-aging trap)
            "price_stale": bool(price_probe_ran and _price_is_stale(last_close_date)),
        }
        # Do NOT cache a transient probe failure — let the next run retry it
        # rather than disabling the price check for this ticker for a full TTL.
        if use_cache and price_probe_ran:
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


def _price_is_stale(last_close_date, today=None):
    """True when last_close_date is older than PRICE_STALE_DAYS (or missing)."""
    if not last_close_date:
        return True
    try:
        last = date.fromisoformat(last_close_date)
    except (ValueError, TypeError):
        return True
    today = today or date.today()
    return (today - last).days > PRICE_STALE_DAYS


def _classify(row, identity):
    """Return (flagged: bool, reason: str) for a single ticker.

    No identity data        => 'no yfinance data' (likely delisted).
    Price feed gone stale    => 'no recent price data' (likely delisted/renamed).
    Non-equity quoteType     => 'recycled to {ETF|MUTUALFUND|...}'.
    Low name similarity      => 'name mismatch (recorded vs yfinance)'.
    Otherwise unflagged.

    The price-recency rule sits above the name-similarity rule because a clean
    acquisition keeps `.info` (and thus the name match) intact for months; the
    dead price feed is the earlier, more reliable signal. `price_stale` is the
    decision frozen at probe time (see `_fetch_identity`), so a stale read is
    never an artifact of cache age.
    """
    quote_type = (identity.get("quoteType") or "").upper()
    long_name = identity.get("longName") or ""
    short_name = identity.get("shortName") or ""
    recorded_name = str(row.get("Company Name", "") or "")

    if not identity or (not quote_type and not long_name and not short_name):
        return True, "no yfinance data (likely delisted)"

    if identity.get("price_stale"):
        last_seen = identity.get("last_close_date") or "never"
        return True, (
            f"no recent price data (likely delisted/renamed, or extended halt); "
            f"last bar={last_seen}"
        )

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


def check_universe(csv_path=None, max_workers=6, use_cache=True):
    """Run the delisted/recycled check across the full universe CSV.

    Returns dict with keys:
      - flagged: list of dicts with ticker/recorded_name/quoteType/yf_name/reason
      - checked: total tickers checked
      - missing_data: count of tickers yfinance returned nothing for
      - price_probe_failures: count of tickers whose price probe raised (the
        price-recency rule was skipped for them; a high count means a Yahoo
        rate-limit/outage, not a delisting wave)

    max_workers defaults to 6 (down from 10): the run now pulls 1mo of history
    per ticker in addition to `.info`, and Yahoo rate-limits bursty traffic —
    a 429 storm would degrade the probe (failures are skipped, not false-flagged).
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
    price_probe_failures = 0
    for row, yf_t in pairs:
        identity = identities.get(yf_t, {})
        if not identity or (
            not identity.get("quoteType")
            and not identity.get("longName")
            and not identity.get("shortName")
        ):
            missing_data += 1
        # identity present but the price probe raised → rule skipped for it
        if identity and not identity.get("price_probe_ran", True):
            price_probe_failures += 1
        is_flagged, reason = _classify(row, identity)
        if is_flagged:
            flagged.append({
                "ticker": row.get("Ticker", ""),
                "yf_ticker": yf_t,
                "recorded_name": row.get("Company Name", ""),
                "yf_long_name": identity.get("longName", ""),
                "yf_short_name": identity.get("shortName", ""),
                "quote_type": identity.get("quoteType", ""),
                "last_close_date": identity.get("last_close_date", ""),
                "sector_jp": row.get("Sector (JP)", ""),
                "subsector_jp": row.get("Subsector (JP)", ""),
                "reason": reason,
            })

    flagged.sort(key=lambda r: r["ticker"])

    return {
        "checked": len(pairs),
        "flagged": flagged,
        "missing_data": missing_data,
        "price_probe_failures": price_probe_failures,
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
        "quote_type", "last_close_date", "sector_jp", "subsector_jp", "reason",
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
    probe_fail = result.get("price_probe_failures", 0)
    if probe_fail:
        lines.append(
            f"- :warning: Price probe failed (rule skipped): {probe_fail} "
            f"— transient Yahoo errors, not delistings; re-run if high"
        )
    lines.append("")
    if result["flagged"]:
        lines.append("| Ticker | Recorded Name | yfinance Name | quoteType | Last Bar | Reason |")
        lines.append("|--------|---------------|---------------|-----------|----------|--------|")
        for row in result["flagged"]:
            yf_name = row["yf_long_name"] or row["yf_short_name"]
            last_bar = row.get("last_close_date") or "—"
            lines.append(
                f"| {row['ticker']} | {row['recorded_name']} | {yf_name} | "
                f"{row['quote_type']} | {last_bar} | {row['reason']} |"
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
        "Delisted check: %d/%d flagged (missing data: %d, price-probe failures: %d)",
        len(result["flagged"]), result["checked"], result["missing_data"],
        result.get("price_probe_failures", 0),
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
