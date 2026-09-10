"""Index membership snapshots — MSCI EAFE, Russell 1000/2000/3000, S&P 500. Board #354.

JP, 2026-09-09: *"I just want the EAFE list and weights tracked"*, then *"lets fix the
Russell and S&P500 issues if they are issues."* They were, and this is the fix.

Collect each index's constituent list and weights weekly, and write a DATED snapshot so
history accumulates. **Nothing here touches the coverage universe.** JP, same day: *"I
don't want index membership to infect the coverage manager."* Index membership is
reference data published on its own path, never rows in
`data/coverage_universe_tickers.csv` and never in `exports/`.

## The issues this fixes, and the one it does not

| Index | The issue | Fixed here? |
|---|---|---|
| Russell 1000/2000/3000 | `sector_chart_pack/russell.py` has fetched them weekly for months into a cache that is **overwritten**. The lists work; no history exists. | **Yes** — dated snapshots |
| S&P 500 | Two writers (CM's `wikipedia_provider` and `sigma-alert/sources/sp500.txt`), neither accumulating history | **Half** — history yes; retiring the second copy is consumer migration |
| All | Four consumers read a per-project cache as if it were a contract | **No** — deliberately out of scope, see below |

⛑ **THE CONSUMER MIGRATION IS DELIBERATELY NOT DONE HERE, AND THE ORDER IS THE POINT.**
`sector_chart_pack`, `post_earnings_movers`, `forensic_triage` and
`screens_equity/surprise_screens` read the existing caches today and still do. Starting
the archive is the half that **cannot be bought back later**; repointing four consumers
can be done any week and risks breaking working lanes. So history starts now and the
migration is filed. The transient cost is named rather than hidden: the Vanguard fetch
below duplicates `sector_chart_pack/russell.py` until that module is retired.

## Why dated snapshots are the point

FTSE sells Russell history and MSCI publishes none free, so membership history is
something you either start accumulating or buy later — and you cannot buy today's. This
module writes `<key>_<as_of>.json` alongside `<key>_latest.json`, and the dated file is
never overwritten once written.

⛑ **RECONSTRUCTING PAST MEMBERSHIP FROM TODAY'S LIST IS SURVIVORSHIP BIAS BY
CONSTRUCTION.** A name that left the index is exactly the name a backtest needs and the
one a current list cannot contain. The archive is the only honest source, which is why
starting it is worth more than the code in this file.

## Licensing

Same house rule as `data/crsp/` and `data/estimates_history/`: kept on disk, **never
pushed and never published into `exports/`**. `data/index_membership/` is gitignored.
Fund holdings are the fund's own SEC-mandated disclosure; redistribution is the concern,
and not redistributing solves it.

## Three sources, and none of them is the index vendor

| Kind | Indices | Source |
|---|---|---|
| `ishares` | MSCI EAFE | `latest-holdings.csv` for EFA — the route `foreign_identifiers.py` already uses |
| `vanguard` | Russell 1000 / 2000 / 3000 | VONE / VTWO / VTHR holdings JSON, paginated at 500 |
| `cm_cache` | S&P 500 | CM's own `cache/constituents/sp500.json` (Wikipedia via `providers/wikipedia_provider.py`) |

⛑ **`as_of_kind` DISTINGUISHES A SOURCE DATE FROM AN OBSERVATION DATE, AND THEY ARE NOT
THE SAME FACT.** A fund states the date its holdings are as of (`source`). A scraped
constituent list states nothing, so the only honest date is when we looked (`observed`).
Collapsing the two would let a scrape taken today claim to be a membership record for
today — which it is, only in the weak sense that nobody has noticed a change yet.

⛑ **DO NOT RAISE THE VANGUARD PAGE SIZE.** `count=5000` serves an OLDER snapshot than
paginating at 500 (measured 2026-08-18: 2026-06-30 against 2026-07-31). Freshness is the
whole reason that source was chosen over SEC N-PORT, so it always paginates.

## Failure modes these endpoints actually have

1. ⛑ **HTTP 200 WITH THE HTML APP SHELL.** iShares serves its product pages through
   JavaScript, and the `.ajax?fileType=csv` route returns 1.4 MB of HTML with a 200 and
   `content-type: text/csv` (measured for EFA, 2026-09-09). The `/x/latest-holdings.csv`
   route used here returns a real CSV — but a response that does not parse as one is
   RAISED on, never accepted as an empty fund. Silently writing a zero-member index is
   how a screen ends up reporting that nothing is in EAFE.
2. **The endpoint is undocumented and can vanish.** A failed fetch falls back to the last
   good snapshot and reports its age; past `STALE_DAYS` the cache is reported unfit
   rather than silently used. Recovery path if it goes for good: SEC N-PORT for the same
   fund (CIK 1100663), the recipe proven in
   the WORKSPACE ROOT's `diagnostics/russell_membership_nport_vs_capband_2026-08-18.md`
   -- not this repo's `diagnostics/`, which exists and does not hold it.

## What this list is and is not

⛑ **EFA IS A SAMPLED FUND, NOT THE INDEX.** 658 equities against MSCI EAFE's ~700
constituents (measured 2026-09-04). Good for "the large and mid caps of developed
ex-US"; not a membership record, and it must never be described as one.

⛑ **THE `Ticker` COLUMN IS A LOCAL EXCHANGE TICKER AND THE FILE CARRIES NO ISIN.**
`ROP` here is Roche, not Roper Technologies; `ASML` is the Euronext line, not the Nasdaq
one. Anything joining this to coverage must key on `(ticker, exchange)` or resolve by
name — a bare ticker join marries the wrong companies with no error. Nothing in this
module joins; it records what the fund published, and the resolver is the consumer's
problem to solve deliberately.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

import config

log = logging.getLogger(__name__)

OUT_DIR = config.DATA_DIR / "index_membership"

HOLDINGS_URL = "https://www.ishares.com/us/products/{pid}/x/latest-holdings.csv"

# iShares 403s a non-browser agent — the same CDN behaviour `foreign_identifiers.py`
# and the comments tracker both hit.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

VANGUARD_API = ("https://investor.vanguard.com/investment-products/etfs/profile/api/"
                "{etf}/portfolio-holding/stock?start={start}&count={count}")
VANGUARD_UA = ("CoverageManager/1.0 (personal investment research; "
               "jroypeterson@gmail.com)")
VANGUARD_PAGE = 500          # see the docstring: a bigger page serves an OLDER snapshot
VANGUARD_PAUSE = 1.5
VANGUARD_RETRIES = 4

# The S&P 500 list CM already refreshes for itself. Read, never written here.
SP500_CACHE = config.CACHE_DIR / "constituents" / "sp500.json"

# `floor` is a REFUSAL, not a warning: a short list means the source changed shape, and
# half an index is worse than none because it looks usable.
SOURCES: dict[str, dict] = {
    "eafe":  {"kind": "ishares",  "pid": "239623", "floor": 400,
              "index": "MSCI EAFE", "fund": "iShares MSCI EAFE ETF (EFA)"},
    "r1000": {"kind": "vanguard", "etf": "VONE", "floor": 800,
              "index": "Russell 1000", "fund": "Vanguard Russell 1000 ETF (VONE)"},
    "r2000": {"kind": "vanguard", "etf": "VTWO", "floor": 1500,
              "index": "Russell 2000", "fund": "Vanguard Russell 2000 ETF (VTWO)"},
    "r3000": {"kind": "vanguard", "etf": "VTHR", "floor": 2400,
              "index": "Russell 3000", "fund": "Vanguard Russell 3000 ETF (VTHR)"},
    "sp500": {"kind": "cm_cache", "floor": 450,
              "index": "S&P 500",
              "fund": "Wikipedia constituent list (CM providers/wikipedia_provider.py)"},
}

# Per-source caveats, written into every snapshot rather than left in this docstring —
# a consumer reads the JSON, not the module that produced it.
CAVEATS: dict[str, list[str]] = {
    "ishares": [
        "EFA is a sampled fund, not the index - treat as a proxy for MSCI EAFE "
        "membership, never as a membership record.",
        "`ticker` is a LOCAL exchange ticker and there is no ISIN in this file. "
        "Join on (ticker, exchange) or by name; a bare ticker join is wrong.",
    ],
    "vanguard": [
        "A Vanguard ETF's holdings are a REPLICATION of the Russell index, not FTSE's "
        "constituent list. FTSE sells membership; this is the closest free proxy.",
        "Share classes are normalised from Vanguard's dot to the fleet's dash "
        "(MOG.A -> MOG-A). Without it every dual-class name silently drops out.",
    ],
    "cm_cache": [
        "Scraped from Wikipedia by CM's own provider - `as_of_kind` is `observed`, "
        "the date we looked, NOT a date the index provider stated.",
    ],
}
_LICENCE = ("Licensed for internal use only - never publish into exports/ or push.")

# Back-compat: the first version of this module exposed `FUNDS`. Nothing outside it read
# that, but the weekly step and the tests import by name, so keep one authority.
FUNDS = SOURCES

# Past this, a cached snapshot is reported as unfit rather than quietly used.
#
# ⛑ THE THRESHOLD IS PER SOURCE, BECAUSE THE PUBLISHING CADENCES DIFFER BY MONTHS.
# iShares publishes holdings DAILY, so an EFA list a fortnight old means the fetch has
# been failing. Vanguard publishes MONTH-END holdings and Russell reconstitutes annually,
# so a list stamped five weeks ago is normal and healthy — one flat 45-day rule would
# have marked the Russell lane unfit on an ordinary week, which is how a guard becomes
# the outage it was added to prevent.
STALE_DAYS = 45                                  # default, and the iShares/CM-cache case
STALE_DAYS_BY_KIND = {"vanguard": 120}           # month-end publishing + annual recon


def stale_days_for(key: str) -> int:
    return STALE_DAYS_BY_KIND.get(SOURCES[key]["kind"], STALE_DAYS)


class IndexMembershipError(RuntimeError):
    """Fetch or parse failed in a way that must not resolve to an empty index."""


def _num(v) -> float | None:
    """A finite float, or None. Never NaN and never a string.

    ⛑ BOTH HALVES OF THIS ARE LOAD-BEARING. Vanguard returns `percentWeight` as a
    STRING, so summing the column raised `TypeError: int + str`; and a bare
    `float(x or "nan")` yields NaN for an empty cell, which then poisons any total it is
    added to while comparing False against every threshold. An unparseable weight is an
    absent weight.
    """
    import math

    if v is None:
        return None
    try:
        f = float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fetch_csv(pid: str, timeout: int = 60) -> str:
    req = urllib.request.Request(HOLDINGS_URL.format(pid=pid),
                                 headers={"User-Agent": BROWSER_UA,
                                          "Accept": "text/csv,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8-sig", "replace")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise IndexMembershipError(f"fetch failed for product {pid}: {e}") from e


def parse_holdings(text: str) -> tuple[str, list[dict]]:
    """`(as_of_iso, rows)` from an iShares latest-holdings CSV.

    ⛑ RAISES on the HTML app shell. `text/csv` in the response header is not evidence
    that the body is a CSV — see this module's docstring. The check is structural: a real
    file has a `Ticker,` header row and a `Fund Holdings as of` line, and an HTML shell
    has neither.
    """
    if "<html" in text[:2000].lower() or "<!doctype" in text[:2000].lower():
        raise IndexMembershipError(
            "response is an HTML page, not a CSV — the endpoint served the app shell "
            "(this returns HTTP 200, so the status code proves nothing)")

    lines = text.splitlines()
    header_idx = next((i for i, l in enumerate(lines)
                       if l.startswith("Ticker,")), None)
    if header_idx is None:
        raise IndexMembershipError("no 'Ticker,' header row — file shape changed")

    as_of = ""
    for l in lines[:header_idx]:
        if l.startswith("Fund Holdings as of"):
            raw = l.split(",", 1)[1].strip().strip('"') if "," in l else ""
            for fmt in ("%b %d, %Y", "%d-%b-%Y", "%Y-%m-%d"):
                try:
                    as_of = datetime.strptime(raw, fmt).date().isoformat()
                    break
                except ValueError:
                    continue
            break
    if not as_of:
        # ⛑ NO SILENT SUBSTITUTION OF TODAY. The as-of date is the single most
        # load-bearing field in a membership snapshot — it is what makes the archive an
        # archive rather than a pile of files — and inventing it would make every
        # snapshot look current.
        raise IndexMembershipError(
            "no parsable 'Fund Holdings as of' date — refusing to stamp a snapshot with "
            "today's date instead")

    rows = []
    for r in csv.DictReader(io.StringIO("\n".join(lines[header_idx:]))):
        if (r.get("Asset Class") or "").strip() != "Equity":
            continue
        t = (r.get("Ticker") or "").strip()
        if not t or t == "-":
            continue
        rows.append({
            "ticker": t,
            "name": (r.get("Name") or "").strip(),
            "sector": (r.get("Sector") or "").strip(),
            "weight_pct": _num(r.get("Weight (%)")),
            "location": (r.get("Location") or "").strip(),
            "exchange": (r.get("Exchange") or "").strip(),
            "market_currency": (r.get("Market Currency") or "").strip(),
            "market_value_usd": _num(r.get("Market Value")),
        })
    return as_of, rows


def normalise_share_class(ticker: str) -> str:
    """Vanguard writes share classes with a dot (`MOG.A`); FMP and CM use a dash.

    Measured on a 500-row page by `sector_chart_pack/russell.py`: 500/500 resolve against
    the FMP US universe after the swap and 499/500 before it. One character, and without
    it every dual-class name silently drops out.
    """
    return (ticker or "").strip().upper().replace(".", "-")


def _fetch_vanguard(etf: str, *, page: int = VANGUARD_PAGE,
                    retries: int = VANGUARD_RETRIES) -> tuple[str, list[dict]]:
    """Paginate one Vanguard ETF's equity holdings.

    ⛑ RAISES rather than returning a partial list. A short list quietly shrinks an index,
    and the floor check downstream would then be measuring a truncated fetch rather than
    a changed source. The endpoint also intermittently returns the HTML app shell with
    HTTP 200 — that lands here as a JSON decode error and is retried, never read as an
    empty fund.
    """
    import time

    start, size, out, as_of = 1, None, [], None
    while True:
        # ⛑ THE ETF SEGMENT MUST BE LOWERCASE. Measured 2026-09-09: `/api/VONE/...`
        # answers **301** to the human page `/profile/vone/portfolio-holding/stock`,
        # which serves 56 KB of HTML with HTTP 200 — so urllib follows the redirect and
        # the JSON decode fails, while a browser-following client would see a "working"
        # page. `/api/vone/...` returns the JSON. This is exactly why
        # `sector_chart_pack/russell.py` has been serving a frozen 2026-07-31 list: it
        # interpolates the uppercase ticker, its fetch has been failing since Vanguard
        # made the path case-sensitive, and its last-good fallback hid that correctly.
        url = VANGUARD_API.format(etf=etf.lower(), start=start, count=page)
        req = urllib.request.Request(url, headers={"User-Agent": VANGUARD_UA})
        d = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    d = json.loads(r.read().decode("utf-8"))
                break
            except Exception as e:                       # noqa: BLE001
                if attempt == retries - 1:
                    raise IndexMembershipError(
                        f"{etf}: page at start={start} undecodable after {retries} "
                        f"attempts ({type(e).__name__}: {e})") from e
                time.sleep(2.0 * (attempt + 1))
        if not isinstance(d, dict):
            raise IndexMembershipError(f"{etf}: page at start={start} is not a JSON object")
        size = d.get("size") if size is None else size
        as_of = as_of or (d.get("asOfDate") or "")[:10]
        ents = ((d.get("fund") or {}).get("entity")) or []
        if not ents:
            break
        for e in ents:
            t = normalise_share_class(e.get("ticker") or "")
            if t:
                out.append({"ticker": t, "name": e.get("longName") or t,
                            "sector": (e.get("sector") or "").strip(),
                            "weight_pct": _num(e.get("percentWeight")),
                            "location": "", "exchange": "", "market_currency": "",
                            "market_value_usd": None})
        start += len(ents)
        if size and start > size:
            break
        time.sleep(VANGUARD_PAUSE)
    if not out:
        raise IndexMembershipError(f"{etf}: no holdings returned")
    if not as_of:
        raise IndexMembershipError(
            f"{etf}: no asOfDate in the response — refusing to stamp a snapshot with "
            f"today's date instead")
    return as_of, out


def _load_cm_sp500() -> tuple[str, list[dict]]:
    """CM's own S&P 500 constituent cache, read-only.

    ⛑ THE DATE IS AN OBSERVATION, NOT A SOURCE AS-OF. A scraped list states no effective
    date, so the only honest stamp is `_cached_at` — when we looked. The caller records
    `as_of_kind='observed'` so a consumer cannot mistake it for the index provider's own.
    """
    try:
        doc = json.loads(SP500_CACHE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise IndexMembershipError(f"S&P 500 cache unreadable at {SP500_CACHE}: {e}") from e

    data = doc.get("data") or {}
    tickers = data.get("tickers") or []
    info = data.get("info") or {}
    stamp = str(doc.get("_cached_at") or "")[:10]
    if not tickers:
        raise IndexMembershipError("S&P 500 cache holds no tickers")
    if len(stamp) != 10:
        raise IndexMembershipError(
            "S&P 500 cache has no usable `_cached_at` — refusing to stamp a snapshot "
            "with today's date instead")

    rows = []
    for t in tickers:
        d = info.get(t) or {}
        rows.append({"ticker": normalise_share_class(t),
                     "name": (d.get("Company Name") or t).strip(),
                     "sector": (d.get("GICS Sector") or "").strip(),
                     "sub_industry": (d.get("GICS Sub-Industry") or "").strip(),
                     # ⛑ NO WEIGHTS. A constituent list is not a weighted index, and
                     # inventing equal weights would be a fabricated figure.
                     "weight_pct": None,
                     "location": "", "exchange": "", "market_currency": "",
                     "market_value_usd": None})
    return stamp, rows


def collect(key: str) -> tuple[str, str, list[dict]]:
    """`(as_of, as_of_kind, rows)` for one index. Dispatches on the source kind."""
    src = SOURCES[key]
    kind = src["kind"]
    if kind == "ishares":
        as_of, rows = parse_holdings(_fetch_csv(src["pid"]))
        return as_of, "source", rows
    if kind == "vanguard":
        as_of, rows = _fetch_vanguard(src["etf"])
        return as_of, "source", rows
    if kind == "cm_cache":
        as_of, rows = _load_cm_sp500()
        return as_of, "observed", rows
    raise IndexMembershipError(f"unknown source kind {kind!r} for {key!r}")


def _source_label(key: str) -> str:
    src = SOURCES[key]
    if src["kind"] == "ishares":
        return HOLDINGS_URL.format(pid=src["pid"])
    if src["kind"] == "vanguard":
        return VANGUARD_API.format(etf=src["etf"], start=1, count=VANGUARD_PAGE)
    return str(SP500_CACHE)


def _snapshot_path(key: str, as_of: str) -> Path:
    return OUT_DIR / f"{key}_{as_of}.json"


def _latest_path(key: str) -> Path:
    return OUT_DIR / f"{key}_latest.json"


def load_latest(key: str) -> dict | None:
    p = _latest_path(key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def snapshot_age_days(doc: dict | None, today: date | None = None) -> int | None:
    if not doc or not doc.get("as_of"):
        return None
    try:
        d = datetime.strptime(doc["as_of"], "%Y-%m-%d").date()
    except ValueError:
        return None
    return ((today or date.today()) - d).days


def refresh(key: str = "eafe", *, today: date | None = None) -> dict:
    """Fetch, validate, and write a dated snapshot. Returns a status dict.

    Never raises on a fetch failure when a cached snapshot exists — it falls back and
    reports the age, because dropping the list is worse than serving a known-old one.
    It DOES raise when there is no fallback: an empty index must not be a valid result.
    """
    src = SOURCES[key]
    floor = src["floor"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = today or date.today()

    try:
        as_of, as_of_kind, rows = collect(key)
    except IndexMembershipError as e:
        cached = load_latest(key)
        age = snapshot_age_days(cached, today)
        if cached is None:
            raise
        unfit = age is None or age > stale_days_for(key)
        log.warning("index_membership[%s]: refresh failed (%s); serving cached "
                    "snapshot as_of=%s age=%sd%s",
                    key, e, cached.get("as_of"), age,
                    " — REPORTED UNFIT, past STALE_DAYS" if unfit else "")
        return {"key": key, "status": "stale_unfit" if unfit else "stale",
                "as_of": cached.get("as_of"), "count": cached.get("count"),
                "age_days": age, "error": str(e), "written": None}

    if len(rows) < floor:
        raise IndexMembershipError(
            f"{key}: {len(rows)} holdings is below the credibility floor of {floor} — "
            f"refusing to write a snapshot of a half-parsed index")

    weighted = [r["weight_pct"] for r in rows if r.get("weight_pct") is not None]
    doc = {
        "schema_version": 3,
        "key": key,
        "index": src["index"],
        "fund": src["fund"],
        # ⛑ THE STALENESS THRESHOLD TRAVELS ON THE SNAPSHOT, because the consumer
        # that has to apply it lives in ANOTHER REPO and reads the JSON, not this
        # module. `sector_chart_pack` previously carried its own flat 120 and this
        # module its own per-kind table; two copies of a number that must agree is
        # how the two lanes silently disagreed about what "stale" meant. Stated on
        # the artifact, there is one authority and a cross-repo reader cannot drift
        # from it. Schema 2 files predate this — a reader must have a fallback.
        "kind": src["kind"],
        "stale_days": stale_days_for(key),
        "as_of": as_of,
        # ⛑ A SOURCE DATE AND AN OBSERVATION DATE ARE DIFFERENT FACTS. See the docstring.
        "as_of_kind": as_of_kind,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": _source_label(key),
        # Stated on every snapshot so a consumer cannot mistake the proxy for the index.
        "caveats": CAVEATS[src["kind"]] + [_LICENCE],
        "count": len(rows),
        # None, not 0.0, where the source publishes no weights: a zero would read as an
        # index whose constituents carry no weight, which is a claim rather than a gap.
        "equity_weight_pct": round(sum(weighted), 4) if weighted else None,
        "holdings": rows,
    }

    dated = _snapshot_path(key, as_of)
    # ⛑ A DATED SNAPSHOT IS WRITTEN ONCE. The fund republishes the same as-of for days;
    # rewriting it would silently change a file the archive treats as immutable, and the
    # whole value of the archive is that a past file says what it said at the time.
    written = None
    if not dated.exists():
        dated.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        written = str(dated)
    _latest_path(key).write_text(json.dumps(doc, indent=1), encoding="utf-8")

    return {"key": key, "status": "ok", "as_of": as_of, "count": len(rows),
            "age_days": snapshot_age_days(doc, today), "error": None,
            "written": written}


def refresh_all(*, today: date | None = None) -> list[dict]:
    """Every index, independently.

    ⛑ ONE INDEX FAILING MUST NOT SKIP THE REST. The first version raised out of the
    loop, so a Vanguard outage took the EAFE and S&P snapshots with it — and the whole
    point of this lane is that a week not captured cannot be recaptured. A failure with
    no cached fallback becomes a `failed` row, not an exception.
    """
    out = []
    for k in SOURCES:
        try:
            out.append(refresh(k, today=today))
        except IndexMembershipError as e:
            log.error("index_membership[%s]: %s", k, e)
            out.append({"key": k, "status": "failed", "as_of": None, "count": None,
                        "age_days": None, "error": str(e), "written": None})
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for r in refresh_all():
        # ASCII only: this prints to a cp1252 console under Task Scheduler, where a
        # middot in the DATA is what breaks the run, not the code around it.
        tail = f" | wrote {r['written']}" if r["written"] else " | dated snapshot already on disk"
        print(f"{r['key']}: {r['status']} | as_of {r['as_of']} | "
              f"{r['count']} holdings{tail}")
