"""The commercial-biopharma universe - a computed category, published for consumers.

JP: "I think we can compute what these names are but then give them a category
name in the coverage manager. I would want the downstream consumers to be updated
to use this list. that's what makes it useful."

⛑ THE RULE IS THE ONE HE WAS ALREADY SHOWN, NOT A NEW ONE. `PROJECT_IDEAS.md`
records it verbatim on 2026-09-07: "the 121-name commercial universe (revenue >
$1bn OR market cap >= $10bn)". A later plan proposed a revenue-only line at $250M
justified by an EV/Sales "cliff"; that was wrong twice. The statistical half:
median EV/Sales is mechanically a function of the bucket key, and 3.9x / 3.3x /
3.5x on n = 38 / 55 / 105 is inside sampling noise -- it locates nothing. The
substantive half: $250M ADDS exactly the band JP said to exclude ("a single small
product") -- Harmony at $959M revenue on a $2.5B cap, Supernus, Collegium,
Pacira, plus ~50 Indian and Japanese generics.

⛑ AND THE `or market cap` LEG IS NOT DECORATION. It covers his second case -- "a
single drug that is huge and allows them to become a platform" -- which revenue
alone cannot see: Arrowhead is $669M revenue on a $12.1B cap. Measured
2026-09-08: 13 names qualify on the cap leg alone.

WHY A NEW COLUMN, given JP ruled AGAINST one on board #223. That ruling was
against a REDUNDANT column -- `Core=Y` plus `Sector (JP)=Biopharma` already
expressed "core biopharma", so a second field would have been a copy that could
drift. Nothing today expresses commercial STAGE, so this is a new fact rather
than a second spelling of an existing one.

⛑ IT IS DELIBERATELY NOT A `Subsector (JP)` VALUE. That column answers "what IS
this company", is hand-owned, and is keyed on by literal string in at least
bond_credit, catalyst_watch, earnings_agent, post_earnings_movers,
sector_chart_pack, earnings_kpi and screens_equity -- none of which validate the
value set, so every one fails SILENTLY on an unexpected value. Commercial stage
is a computed, time-varying fact; folding it into a curated axis would churn a
hand-owned column weekly and move ~120 names out of every Biotech/Pharma cut in
the fleet. Two independent axes must not be read as one.

⛑ CACHE-ONLY BY DESIGN. `run_weekly_coverage.bat` runs `weekly-universe` --
exports, sigma push and git push -- BEFORE `cli.py performance`, the only lane
that fetches fundamentals. So this step reads the fundamentals cache and never
calls a vendor: putting a ~17-minute metered fetch ahead of the published
contract is the wrong trade, and a step that needs the network to publish a
contract fails when the network does. The cost is a figure up to a week old,
carried honestly as `as_of`.
"""
import glob
import json
import logging
import os

from providers.fx_provider import fetch_aggregate_fx, major_unit
from providers.valuation import derive_valuation, num
from ticker_utils import normalize_ticker

logger = logging.getLogger(__name__)

COLUMN = "Commercial Biopharma"
SECTOR = "Biopharma"

# The recorded definition. Both legs are config so the line is a one-value change.
MIN_REVENUE_USD_M = 1000.0
MIN_MKT_CAP_USD_M = 10000.0
RULE = ("revenue_ttm_usd_m >= %g or mkt_cap_usd_m >= %g"
        % (MIN_REVENUE_USD_M, MIN_MKT_CAP_USD_M))

# Curated subsectors that are commercial by definition, whatever the cache holds.
ALWAYS_COMMERCIAL_SUBSECTORS = frozenset({"Large Pharma"})

# ⛑ Refuse to rewrite the column when too little of the sector resolved. A wiped
# cache or a fresh clone would otherwise blank every flag and ship an EMPTY
# bucket to sigma-alert and the chart pack -- the "refuse to publish a partial
# book" rule this repo already applies to holdings and to the coverage workbook.
# A half-empty category is indistinguishable from a category where nothing
# qualifies, and only one of those is a reason to change what consumers see.
MIN_RESOLVED_FRACTION = 0.85

CACHE_GLOB = os.path.join("cache", "fundamentals", "yf2_*.json")


class RefusedPartialBook(RuntimeError):
    """Raised when too few rows resolved to safely rewrite the column."""


def load_cached_primitives(cache_glob=CACHE_GLOB):
    """`{yf_symbol: primitives}` from the fundamentals cache. No network."""
    out = {}
    for path in glob.glob(cache_glob):
        base = os.path.basename(path)
        sym = base[len("yf2_"):-len(".json")]
        try:
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
        except (OSError, ValueError):
            continue
        prim = ((blob.get("data") or {}).get("result") or {}).get("_valuation")
        if isinstance(prim, dict):
            out[sym] = prim
    return out


def _mkt_cap_usd_m(prim, fx):
    mc = num(prim.get("marketCap"))
    rate = fx.get(major_unit((prim.get("currency") or "").strip()))
    if mc is None or rate is None or mc <= 0:
        return None
    return mc * rate / 1e6


def classify_row(row, prim, fx, fmp=None):
    """`(status, revenue_usd_m, mkt_cap_usd_m)` for one universe row.

    status is `commercial` / `below_line` / `unknown`. `unknown` means we could
    not measure it -- never that the company has no product. Those are different
    claims and the second is much stronger.
    """
    if (row.get("Subsector (JP)") or "").strip() in ALWAYS_COMMERCIAL_SUBSECTORS:
        # ⛑ Roche and Bayer had NO cache entry at all on 2026-09-08, and JCR
        # (4552.T) is a $270M/$419M row. A purely computed rule would silently
        # drop them from a list titled "commercial biopharma". The curated
        # judgement outranks the measurement it exists to encode.
        rev = mcap = None
        if prim:
            rev = derive_valuation(prim, fx)["revenue_usd_m"]
            mcap = _mkt_cap_usd_m(prim, fx)
        return "commercial", rev, mcap

    if not prim:
        return "unknown", None, None

    rev = derive_valuation(prim, fx)["revenue_usd_m"]
    mcap = _mkt_cap_usd_m(prim, fx)
    # ⛑ SECOND SOURCE, FOR ZERO-VS-ABSENCE ONLY. yfinance reports a null for
    # both "no product" and "no figure". FMP resolves which, and 91% of the time
    # the answer is a corroborated ZERO. It is NEVER used as a magnitude: it is a
    # fiscal-year figure in its own reported currency against yfinance's TTM, and
    # merging two periods under one heading is the defect class this repo just
    # spent two days removing. A POSITIVE FMP figure therefore leaves the row
    # `unknown` and is surfaced for a human, rather than silently substituted.
    if rev is None and fmp is not None:
        if fmp.get("status") == "zero":
            rev = 0.0

    if rev is None and mcap is None:
        return "unknown", None, None
    if (rev is not None and rev >= MIN_REVENUE_USD_M) or \
       (mcap is not None and mcap >= MIN_MKT_CAP_USD_M):
        return "commercial", rev, mcap
    # ⛑ A row can fail the revenue leg on IGNORANCE while failing the cap leg on
    # FACT. Only say "below the line" when the unmeasured leg could not have
    # rescued it -- otherwise an absent revenue reads as a measured shortfall.
    if rev is None:
        return "unknown", rev, mcap
    return "below_line", rev, mcap


def _revenue_source(prim, fmp):
    """Which vendor produced the revenue figure, so the export shows its work."""
    if prim and prim.get("totalRevenue") is not None:
        return "yfinance_ttm"
    if fmp is not None and fmp.get("status") == "zero":
        return "fmp_corroborated_zero"
    if fmp is not None and fmp.get("status") == "positive":
        return "fmp_positive_not_used"
    return None


def classify(rows, primitives=None, fx=None, fmp_revenue=None):
    """Classify every Biopharma row. Returns `(by_ticker, summary)`.

    `fmp_revenue` is `{ticker: fetch_revenue(...) result}`, used only to turn a
    yfinance null into a corroborated zero. Absent, the classification still
    works -- more rows simply stay `unknown`, which is the honest degradation.
    """
    primitives = load_cached_primitives() if primitives is None else primitives
    bio = [r for r in rows if (r.get("Sector (JP)") or "").strip() == SECTOR]
    if fx is None:
        wanted = set()
        for p in primitives.values():
            wanted.add((p.get("currency") or "").strip())
            wanted.add((p.get("financialCurrency") or "").strip())
        wanted = {c for c in wanted if c and c != "USD"}
        fx = fetch_aggregate_fx(wanted) if wanted else {"USD": 1.0}

    by_ticker = {}
    for r in bio:
        t = r["Ticker"]
        prim = (primitives.get(normalize_ticker(t, r.get("Exchange", "")))
                or primitives.get(t))
        status, rev, mcap = classify_row(r, prim, fx, (fmp_revenue or {}).get(t))
        by_ticker[t] = {
            "status": status,
            "revenue_ttm_usd_m": None if rev is None else round(rev, 1),
            "mkt_cap_usd_m": None if mcap is None else round(mcap, 1),
            "subsector": (r.get("Subsector (JP)") or "").strip(),
            "revenue_source": _revenue_source(prim, (fmp_revenue or {}).get(t)),
        }

    counts = {}
    for v in by_ticker.values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    resolved = len(bio) - counts.get("unknown", 0)
    summary = {
        "rule": RULE,
        "biopharma_rows": len(bio),
        "commercial": counts.get("commercial", 0),
        "below_line": counts.get("below_line", 0),
        "unknown": counts.get("unknown", 0),
        "resolved_fraction": (resolved / len(bio)) if bio else 0.0,
    }
    return by_ticker, summary


def check_floor(summary, min_fraction=MIN_RESOLVED_FRACTION):
    """Raise unless enough of the sector resolved to trust a rewrite."""
    if summary["biopharma_rows"] == 0:
        raise RefusedPartialBook(
            "no Biopharma rows; refusing to rewrite %s" % COLUMN)
    if summary["resolved_fraction"] < min_fraction:
        raise RefusedPartialBook(
            "only %.1f%% of %d Biopharma rows resolved (floor %.0f%%). A wiped "
            "fundamentals cache looks exactly like a sector where nothing "
            "qualifies; refusing to rewrite %s and ship an empty bucket."
            % (100 * summary["resolved_fraction"], summary["biopharma_rows"],
               100 * min_fraction, COLUMN))


def backfill_fmp_revenue(rows, api_key, primitives=None, fx=None,
                         max_workers=8, use_cache=True, limit=None):
    """Populate the FMP revenue cache for Biopharma rows yfinance cannot resolve.

    Run on demand (like `backfill-lei` / `ipo-backfill`), NOT inside
    `weekly-universe` -- that step must stay cache-only, because it publishes the
    contract before anything is allowed to touch the network. Returns a summary.
    """
    from concurrent.futures import ThreadPoolExecutor
    from providers.fmp_revenue import fetch_revenue

    primitives = load_cached_primitives() if primitives is None else primitives
    if fx is None:
        fx = {"USD": 1.0}
    todo = []
    for r in rows:
        if (r.get("Sector (JP)") or "").strip() != SECTOR:
            continue
        t = r["Ticker"]
        prim = (primitives.get(normalize_ticker(t, r.get("Exchange", "")))
                or primitives.get(t))
        if prim and prim.get("totalRevenue") is not None:
            continue                       # yfinance already answered
        todo.append(t)
    if limit:
        todo = todo[:limit]

    out = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for t, res in zip(todo, ex.map(
                lambda x: fetch_revenue(x, api_key, use_cache=use_cache), todo)):
            out[t] = res
    counts = {}
    for v in out.values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    logger.info("FMP revenue backfill: %d asked, %s", len(todo), counts)
    return out, counts


def load_fmp_revenue(rows, primitives=None):
    """Read whatever the FMP revenue cache already holds. No network."""
    from providers.fmp_revenue import fetch_revenue
    primitives = load_cached_primitives() if primitives is None else primitives
    out = {}
    for r in rows:
        if (r.get("Sector (JP)") or "").strip() != SECTOR:
            continue
        t = r["Ticker"]
        prim = (primitives.get(normalize_ticker(t, r.get("Exchange", "")))
                or primitives.get(t))
        if prim and prim.get("totalRevenue") is not None:
            continue
        res = fetch_revenue(t, api_key=None, use_cache=True)   # cache-only
        if res["status"] != "no_data":
            out[t] = res
    return out


def published_payload(by_ticker, summary, as_of):
    """The `exports/commercial_biopharma.json` document.

    Carries the RULE STRING, not just the membership: a consumer looking at 123
    names must be able to see what produced them without reading this module, and
    a changed line must be visible as a changed document rather than as a list
    that quietly grew.
    """
    return {
        "schema_version": 1,
        "generated_at": as_of,
        "as_of": as_of,
        "rule": summary["rule"],
        "source": ("yfinance TTM revenue and market cap from the fundamentals "
                   "cache; FMP used ONLY to tell a corroborated zero from an "
                   "absence, never as a magnitude"),
        "counts": {k: summary[k] for k in
                   ("biopharma_rows", "commercial", "below_line", "unknown")},
        "resolved_fraction": round(summary["resolved_fraction"], 4),
        "tickers": sorted(t for t, v in by_ticker.items()
                          if v["status"] == "commercial"),
        "detail": {t: v for t, v in sorted(by_ticker.items())},
    }


def apply_to_frame(df, by_ticker):
    """Write the Y/blank column onto the universe frame. Returns (set, cleared).

    ⛑ `Y` / blank, not a three-state string. Every consumer in this fleet already
    parses `Core` as `== "Y"`, so this reuses an idiom rather than minting one.
    And the three-state detail would be actively WRONG on the CSV: "below_line"
    is not "pre-commercial" -- Harmony at $959M revenue is neither. The nuance
    lives in the JSON, where a reader can see the figure that produced it.
    """
    if COLUMN not in df.columns:
        df[COLUMN] = ""
    set_n = cleared = 0
    for i in df.index:
        t = df.at[i, "Ticker"]
        rec = by_ticker.get(t)
        if rec is None:
            continue                      # not Biopharma: leave whatever is there
        want = "Y" if rec["status"] == "commercial" else ""
        cur = (df.at[i, COLUMN] or "").strip()
        if cur != want:
            df.at[i, COLUMN] = want
            if want:
                set_n += 1
            else:
                cleared += 1
    return set_n, cleared
