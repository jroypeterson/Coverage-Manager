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
| S&P 500 | Two writers (CM's `wikipedia_provider` and `sigma-alert/sources/sp500.txt`), neither accumulating history | **Half** — history yes; retiring the second copy is consumer migration (done 2026-09-22, `sigma_export.build_sp500_mirror`) |
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

⛑ **ONE BREACH OF THAT RULE IS IN THIS PUBLIC REPO'S HISTORY, AND JP RULED IT STAYS.**
Commit `b46885f` (2026-09-22) carried `tests/fixtures/ivv_holdings_2026-09-21_trimmed.csv`
with REAL IVV market values, weights and quantities for ~20 names. It was meant to be
squashed before pushing; another session pushed `master` first and carried it along. The
next commit replaced those figures with synthetic ones, so only the history holds them.
JP, asked: *"i don't care if people see publicly data that is available publicly"* — the
file is a public iShares download, 20 of 503 rows, one day old. **Do NOT rewrite history
or force-push this repo to remove it.** The rule above still governs new commits.

## Three sources, and none of them is the index vendor

| Kind | Indices | Source |
|---|---|---|
| `ishares` | MSCI EAFE | `latest-holdings.csv` for EFA — the route `foreign_identifiers.py` already uses |
| `vanguard` | Russell 1000 / 2000 / 3000 | VONE / VTWO / VTHR holdings JSON, paginated at 500 |
| `ishares` | S&P 500 | `latest-holdings.csv` for **IVV** (product 239726) — since 2026-09-22 |
| `cm_cache` | (none; rollback only) | CM's own `cache/constituents/sp500.json` (Wikipedia via `providers/wikipedia_provider.py`) |

## S&P 500 comes from IVV holdings, not the Wikipedia scrape (JP 2026-09-22, Fable-gated)

The scrape had three defects: no weights; no effective date (`as_of_kind` could only be
`observed`); and the weekly build snapshotted CM's cache BEFORE the weekly performance run
refreshed it (18 Sep 2026: snapshot 09:36, cache 10:09), so the list lagged a week and
missed the September reconstitution (BE, ILMN, P in; BLDR, TAP, TTD out). IVV is a full
replication fund that states its own as-of date daily.

Measured on the live file, 2026-09-22 (`Fund Holdings as of Sep 21, 2026`), and pinned by
`tests/fixtures/ivv_holdings_2026-09-21_trimmed.csv`:

* 508 lines: 504 `Equity`, plus `Money Market` (XTSLA), `Cash` (USD), `Cash Collateral
  and Margins` (SGAFT) and `Futures` (ESZ6). Only `Asset Class == Equity` is kept.
* ⛑ **One equity line is not a constituent**: HOLX, $28k at $0.01 on exchange
  `NO MARKET (E.G. UNLISTED)` — a post-deal residual the index had already dropped. Lines
  on that exchange are dropped (S&P 500 only), which leaves 503 = the index's count.
* ⛑ **Share classes are written with a SPACE**: `BRK B`, `BF B`. GOOG/GOOGL, FOX/FOXA and
  NWS/NWSA are distinct plain tickers. `normalise_us_ticker` maps space and dot to the
  dash every cm_cache snapshot used (`BRK-B`), so the switch week's reconciliation shows
  no phantom departures. **S&P 500 only**: EFA's `NOVO B` is a Copenhagen local ticker
  and must stay raw.
* ⛑ **IVV's sector strings are NOT the GICS set** (`Communication`, not `Communication
  Services`) and its names are `BERKSHIRE HATHAWAY INC CLASS B` style. So `name`,
  `sector` and `sub_industry` are JOINED from the Wikipedia cache by normalised ticker,
  and IVV's are only the fallback for a name the cache does not have yet (a new entrant,
  in the week before Wikipedia catches up); `name_source` says which. An unreadable
  cache RAISES rather than falling back for all 503 — that would rewrite every name in
  sigma-alert's public `sp500_names.json` in one week.
* Weight, market value and exchange stay on the snapshot, which is gitignored. The public
  sigma-alert mirror writes tickers and names ONLY (pinned by a test in
  `tests/test_sigma_sp500_mirror.py`).

`universe/index_mirrors.source_crosscheck` compares IVV against the Wikipedia cache every
week as a NON-GATING line: they should differ on a reconstitution week and agree after.

**Documented fallback source (not built):** SSGA's daily SPY holdings xlsx
(`https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx`),
(URL recalled, NOT fetched or verified) -- another full-replication S&P 500 fund with
its own as-of date. If IVV's endpoint goes for good, that is the swap — the `cm_cache` kind is the immediate rollback meanwhile.

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
from datetime import date, datetime, timedelta
from pathlib import Path

import config

log = logging.getLogger(__name__)

OUT_DIR = config.DATA_DIR / "index_membership"

HOLDINGS_URL = "https://www.ishares.com/us/products/{pid}/x/latest-holdings.csv"

# Columns whose ABSENCE would make a filter or a field silently vanish rather than fail.
REQUIRED_COLUMNS = ("Ticker", "Name", "Sector", "Asset Class", "Weight (%)", "Exchange")

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
    # `post` names an S&P-500-only row transform (see `_POST`). Rollback: kind
    # "cm_cache" with no pid/post restores the Wikipedia-scrape source exactly.
    "sp500": {"kind": "ishares", "pid": "239726", "floor": 450, "post": "sp500_ivv",
              "index": "S&P 500", "fund": "iShares Core S&P 500 ETF (IVV)"},
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
# ⛑ AN OVERRIDE IS KEYED ON (key, KIND), NEVER ON THE KEY ALONE. These sentences
# describe the SOURCE, so they must follow the source actually used for the run: keyed
# on "sp500" alone, the documented rollback to `cm_cache` (change the SOURCES entry,
# nothing else) would keep stamping every snapshot "full-replication IVV holdings,
# fund-stated date" onto a Wikipedia scrape, and the archive would be permanently
# mislabelled with no error anywhere. The per-kind table below is the fallback.
CAVEATS_BY_KEY: dict[tuple[str, str], list[str]] = {
    ("sp500", "ishares"): [
        "iShares Core S&P 500 ETF (IVV) holdings - a full-replication fund, not S&P's own "
        "constituent file. `as_of` is the fund's stated holdings date (`source`).",
        "Non-equity lines (cash, money market, collateral, futures) and residual lines "
        "on 'NO MARKET (E.G. UNLISTED)' are dropped.",
        "Share classes normalised to the dash form (IVV `BRK B` -> BRK-B).",
        "`name`/`sector`/`sub_industry` are joined from CM's Wikipedia cache; "
        "`name_source: ivv` marks a row where IVV's own name/sector was the fallback.",
    ],
}


def caveats_for(key: str, kind: str) -> list[str]:
    """The caveats for the source this run actually used. See `CAVEATS_BY_KEY`."""
    return CAVEATS_BY_KEY.get((key, kind)) or CAVEATS[kind]
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
# A fund stamps its holdings in its own calendar; one day covers the timezone gap and
# nothing else. See the future-as_of guard in `refresh`.
FUTURE_TOLERANCE_DAYS = 1
STALE_DAYS_BY_KIND = {"vanguard": 120}           # month-end publishing + annual recon


def stale_days_for(key: str) -> int:
    return STALE_DAYS_BY_KIND.get(SOURCES[key]["kind"], STALE_DAYS)


def is_future_as_of(as_of: str | None, today: date) -> bool:
    """Is this as-of date further ahead than the timezone tolerance allows?

    ⛑ ONE TEST, USED ON BOTH PATHS. The first version guarded only a FRESHLY FETCHED
    date, so a future-dated snapshot already on disk sailed through the fetch-FAILURE
    path: its age is NEGATIVE, a negative is never greater than `STALE_DAYS`, and it
    was therefore served as an ordinary `stale` fallback and read as consumable —
    indefinitely, because the same file then blocks every correct fetch behind it.
    A future date is not a fresh snapshot and not a usable fallback; it is a corrupt
    one, whichever path produced it.
    """
    d = _as_date(as_of)
    return d is not None and d > today + timedelta(days=FUTURE_TOLERANCE_DAYS)


class IndexMembershipError(RuntimeError):
    """Fetch or parse failed in a way that must not resolve to an empty index."""


def _as_date(value: str | None) -> date | None:
    """A real calendar date from an ISO string, or None. Never a substring."""
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_as_of(raw: str | None, source: str) -> str:
    """An ISO date from a source's own stamp, or raise. Used by EVERY source kind.

    ⛑ A DATE-SHAPED STRING IS NOT A DATE, AND THESE WERE COMPARED LEXICALLY. Vanguard's
    `asOfDate` was merely SLICED to ten characters, so `2026-09-00T00:00:00-04:00`
    passed the future check, passed the older-than check, and archived
    `r1000_2026-09-00.json` as latest with `age_days` None — a snapshot that no
    staleness rule can ever measure and that blocks nothing behind it. Parse where the
    value is read, refuse what does not parse, and compare dates rather than strings.
    """
    d = _as_date(raw)
    if d is None:
        raise IndexMembershipError(
            f"{source}: as-of {raw!r} is not a calendar date — refusing to stamp a "
            f"snapshot with a value no staleness or ordering check can read")
    return d.isoformat()


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
    import http.client

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8-sig", "replace")
    except http.client.IncompleteRead as e:
        # ⛑ NOT an OSError, so this escaped the clause below as an unhandled exception
        # mid-build. It is also the transport's ONLY truncation signal here: both
        # endpoints answer `Transfer-Encoding: chunked` with no Content-Length.
        raise IndexMembershipError(
            f"truncated response for product {pid}: the chunked body ended early "
            f"({len(e.partial)} bytes read)") from e
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as e:
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

    # ⛑ A CUT INSIDE THE FINAL QUOTED FIELD LEAVES EVERY EARLIER RECORD FULL-WIDTH, so
    # the per-row field-count guard never fires and 496 constituents clear the 495-510
    # band with seven names silently missing. Measured on the live files 2026-09-22
    # (IVV 83,277 bytes, EFA 115,972): both end `...\n\n`, i.e. a newline after the
    # last record — and neither endpoint sends a Content-Length (both answer
    # `Transfer-Encoding: chunked`), so this is the only in-file evidence there is.
    #
    # 🔻 RESIDUAL, STATED: a cut landing exactly on a record boundary is invisible here
    # — the file carries no holdings count to reconcile against. The count band and the
    # weekly IVV-vs-Wikipedia line in `index_mirrors` are what cover that.
    if not text.endswith("\n"):
        raise IndexMembershipError(
            "the response does not end with a newline — it was truncated mid-record "
            "(a cut inside a quoted field leaves the rows before it well-formed, so "
            "nothing else in this parser can see it)")

    lines = text.splitlines()
    header_idx = next((i for i, l in enumerate(lines)
                       if l.startswith("Ticker,")), None)
    if header_idx is None:
        raise IndexMembershipError("no 'Ticker,' header row — file shape changed")

    # ⛑ REQUIRE THE COLUMNS THE FILTERS READ, because `row.get(col, "")` makes a
    # renamed or dropped column fail OPEN: with `Exchange` gone, the
    # NO MARKET (E.G. UNLISTED) test never matches and the HOLX-style residual
    # publishes as a constituent; with `Asset Class` gone, cash and futures lines do.
    # A guard that silently stops applying is worse than no guard.
    # ⛑ THE CHECK AND THE PARSE READ THE SAME NORMALISED HEADER. The first version
    # stripped the names for the check and then handed the RAW line to DictReader, so a
    # header of `Exchange ` passed validation while every parsed exchange came back
    # blank -- the NO MARKET filter matched nothing, HOLX rode through, and 504 rows
    # still sat inside the 495-510 band. A validator that inspects a different value
    # from the one the code uses is not a validator.
    rows_iter = csv.reader(io.StringIO("\n".join(lines[header_idx:])))
    header = [h.strip() for h in next(rows_iter)]
    # ⛑ `dict(zip(...))` KEEPS THE LAST VALUE FOR A REPEATED NAME. A second, blank
    # `Exchange` column therefore passed the required-column check AND the row-width
    # check while blanking "NO MARKET (E.G. UNLISTED)" for every row: HOLX survived and
    # a 504-row basket cleared the count band and the join floor. Which column wins is
    # not a judgement this parser can make, so a duplicate name is refused.
    dupes = sorted({h for h in header if header.count(h) > 1})
    if dupes:
        raise IndexMembershipError(
            f"holdings header names the same column twice ({', '.join(dupes)}) — "
            f"a duplicate silently overwrites the first value, so the filters would "
            f"read whichever copy came last")
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise IndexMembershipError(
            f"holdings file is missing required column(s) {', '.join(missing)} — "
            f"the filters that read them would fail open (header: {', '.join(header)})")

    # ⛑ TWO AS-OF HEADERS IS NOT AN ANSWER. The parser took the FIRST and never looked
    # further, so a response carrying `Sep 25, 2026` followed by a corrective
    # `Sep 18, 2026` -- with the OLDER basket beneath it -- was stamped Sep 25, sailed
    # past the strictly-newer guard, and published older membership as current. Same
    # rule the sigma-alert mirror applies to its own `# Last updated:` line.
    stamps = [l.split(",", 1)[1].strip().strip('"') if "," in l else ""
              for l in lines[:header_idx] if l.strip().startswith("Fund Holdings as of")]
    if len(stamps) > 1:
        raise IndexMembershipError(
            "the file carries more than one 'Fund Holdings as of' header "
            f"({', '.join(repr(s) for s in stamps)}) — it cannot be dated, and the "
            f"first one is not evidence of which basket is below it")

    as_of = ""
    for l in lines[:header_idx]:
        if l.strip().startswith("Fund Holdings as of"):
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
    for raw in rows_iter:
        if not any(f.strip() for f in raw):
            continue                      # a blank line is not a truncated record
        # ⛑ VALIDATING THE HEADER IS NOT VALIDATING THE ROWS. `zip` stops at the
        # shorter side, so a response truncated mid-record left HOLX carrying a Ticker
        # and `Asset Class=Equity` with NO Exchange -- the unlisted filter saw "" and
        # kept it, and 504 rows still cleared the count band and the join floor. A
        # truncated CSV must not parse as a valid basket.
        if len(raw) != len(header):
            raise IndexMembershipError(
                f"holdings row {len(rows) + 1} has {len(raw)} field(s) against a "
                f"{len(header)}-column header (first cell {raw[0] if raw else ''!r}) — "
                f"the response is truncated or the shape changed; refusing to parse "
                f"fields that would silently read as empty")
        r = dict(zip(header, raw))
        if (r.get("Asset Class") or "").strip() != "Equity":
            continue                      # cash, futures and collateral carry `-`
        t = (r.get("Ticker") or "").strip()
        # ⛑ AN EQUITY ROW WITH NO SYMBOL IS NOT A ROW TO DROP. Dropping it removed the
        # holding before any downstream guard could see it: an otherwise complete file
        # with AAPL's ticker blank yields a 502-member basket that clears the count
        # band and the join floor, reports success, and publishes the S&P 500 without
        # Apple -- and the mirror's own no-blank-ticker check never sees a row that is
        # already gone. Measured 2026-09-22: neither IVV nor EFA carries a single
        # Equity row with a blank or `-` ticker, so this shape is always a defect.
        if not t or t == "-":
            raise IndexMembershipError(
                f"an Equity holding has no ticker (name {r.get('Name', '')!r}) — "
                f"dropping it would silently shrink the index by one real constituent")
        # ⛑ AN EXCLUSION PREDICATE NEEDS A POPULATED COLUMN. Keep the `Exchange`
        # header and blank its values and the "NO MARKET (E.G. UNLISTED)" test matches
        # NOTHING — the fleet's a-flag-that-is-always-FALSE shape — so the HOLX-style
        # residual rides through and a 504-row basket clears the count band and the
        # join floor. Measured 2026-09-22: 0 of 504 IVV and 0 of 658 EFA equity rows
        # carry a blank exchange, so "every constituent names its venue" is the rule,
        # not a rate heuristic that would itself need calibrating.
        if not (r.get("Exchange") or "").strip():
            raise IndexMembershipError(
                f"equity holding {t} has a blank Exchange — the unlisted-residual "
                f"filter would match nothing and silently keep non-constituents")
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


def normalise_us_ticker(ticker: str) -> str:
    """A US share-class ticker in the fleet's dash form, from IVV's SPACE form.

    IVV writes `BRK B` / `BF B` (measured 2026-09-22); Vanguard and Wikipedia a dot.
    Every run of whitespace and dots becomes one dash. S&P 500 only -- an EFA ticker
    like `NOVO B` is a local exchange code and is never passed through this.
    """
    import re

    return re.sub(r"[\s.]+", "-", (ticker or "").strip().upper())


UNLISTED_EXCHANGE = "NO MARKET (E.G. UNLISTED)"

# ⛑ THE S&P 500 COUNT BAND IS THE PUBLIC MIRROR'S BAND, CHECKED AT COLLECT TIME.
# The generic `floor` (450) is a half-parse test, not a membership test: a CSV
# truncated at 495 equities, or a transitional basket of 506 (503 current plus three
# outgoing names the fund has not sold yet), both cleared it and replaced the snapshot
# every consumer reads. Only `sigma_export.build_sp500_mirror` applied a real band, and
# it sits one repo downstream and guards one file. One authority, imported there.
#
# 🔻 RESIDUAL, STATED RATHER THAN GUARDED: 504-510 is still accepted, so a fund caught
# mid-reconstitution can publish a handful of non-members for one run and self-corrects
# on the next. Narrowing it to exactly 503 would refuse a real index change (the S&P 500
# has carried 503 lines for years only because of the dual-class names), and the obvious
# discriminator -- absent from the Wikipedia list AND tiny weight -- fails on exactly the
# week it is needed, because a NEW entrant is also absent from a week-old Wikipedia list.
# `index_mirrors.source_crosscheck` names every such difference instead.
SP500_MIN_COUNT = 495
SP500_MAX_COUNT = 510

# A Wikipedia join below this share means the cache is present but not usable for names
# (empty `info`, or a renamed schema), and every row would fall back to IVV's own
# "BERKSHIRE HATHAWAY INC CLASS B" style -- rewriting the public sp500_names.json
# wholesale. Measured 2026-09-22: 500 of 503 joined, the 3 misses being that week's
# entrants. 0.90 leaves room for a full reconstitution (~25 names) and still refuses a
# schema break, which lands near zero.
SP500_MIN_JOIN_RATE = 0.90

# IVV's sector labels differ from GICS in exactly this one (measured: the other ten of
# IVV's eleven equity sector strings equal the Wikipedia cache's GICS set).
IVV_SECTOR_TO_GICS = {"Communication": "Communication Services"}


def _sp500_ivv_rows(rows: list[dict]) -> list[dict]:
    """IVV equity rows -> S&P 500 membership rows. See the docstring's IVV section.

    Drops unlisted residuals, normalises share classes, and joins name/sector/
    sub_industry from the Wikipedia cache (IVV's only as the fallback).
    """
    _, info = _read_cm_sp500()
    out, seen = [], {}
    for r in rows:
        if (r.get("exchange") or "").strip().upper() == UNLISTED_EXCHANGE:
            continue
        t = normalise_us_ticker(r["ticker"])
        if t in seen:
            raise IndexMembershipError(
                f"IVV lines {seen[t]!r} and {r['ticker']!r} both normalise to {t} - "
                f"refusing to guess which is the constituent")
        seen[t] = r["ticker"]
        w = info.get(t) or {}
        wname = (w.get("Company Name") or "").strip()
        wsector = (w.get("GICS Sector") or "").strip()
        ivv_sector = IVV_SECTOR_TO_GICS.get(r.get("sector") or "", r.get("sector") or "")
        out.append({**r,
                    "ticker": t,
                    "name": wname or r.get("name") or t,
                    "sector": wsector or ivv_sector,
                    "sub_industry": (w.get("GICS Sub-Industry") or "").strip(),
                    "name_source": "wikipedia" if wname else "ivv",
                    # transport for the per-field join check below; popped before return
                    "_sector_source": "wikipedia" if wsector else "ivv"})


    # ⛑ THE THRESHOLD COVERS EVERY FIELD THE JOIN PROMISES, not just the one that
    # motivated it. Counting company NAMES alone meant a cache that renamed
    # `GICS Sub-Industry` to `GICS Sub Industry` reported 503/503 joined and wrote a
    # snapshot with every sub_industry blank -- a check that measures one of the three
    # things it certifies is a check that passes while two of them are broken.
    for field, joined in (
            ("name", sum(1 for r in out if r["name_source"] == "wikipedia")),
            ("sector", sum(1 for r in out if r["_sector_source"] == "wikipedia")),
            ("sub_industry", sum(1 for r in out if r["sub_industry"]))):
        if joined < SP500_MIN_JOIN_RATE * len(out):
            raise IndexMembershipError(
                f"sp500: the Wikipedia cache supplied `{field}` for only {joined} of "
                f"{len(out)} holdings (floor {SP500_MIN_JOIN_RATE:.0%}) — refusing to "
                f"publish a snapshot whose joined fields are mostly missing (a renamed "
                f"cache key looks exactly like this)")
    for r in out:
        r.pop("_sector_source", None)
    return out


_POST = {"sp500_ivv": _sp500_ivv_rows}


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
        if not as_of and d.get("asOfDate"):
            as_of = parse_as_of(d.get("asOfDate"), etf)
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


def _read_cm_sp500_doc() -> tuple[str, list[str], dict]:
    """`(cached_at_date, raw_tickers, raw_info)` from CM's Wikipedia cache. Raises."""
    try:
        doc = json.loads(SP500_CACHE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise IndexMembershipError(f"S&P 500 cache unreadable at {SP500_CACHE}: {e}") from e
    data = doc.get("data") or {}
    tickers = data.get("tickers") or []
    if not tickers:
        raise IndexMembershipError("S&P 500 cache holds no tickers")
    return str(doc.get("_cached_at") or "")[:10], tickers, data.get("info") or {}


def _read_cm_sp500() -> tuple[str, dict]:
    """`(cached_at_date, {dash_ticker: info})` -- the join table for IVV enrichment."""
    stamp, tickers, info = _read_cm_sp500_doc()
    return stamp, {normalise_us_ticker(t): (info.get(t) or {}) for t in tickers}


def _load_cm_sp500() -> tuple[str, list[dict]]:
    """CM's own S&P 500 constituent cache, read-only.

    ⛑ THE DATE IS AN OBSERVATION, NOT A SOURCE AS-OF. A scraped list states no effective
    date, so the only honest stamp is `_cached_at` — when we looked. The caller records
    `as_of_kind='observed'` so a consumer cannot mistake it for the index provider's own.
    """
    stamp, tickers, info = _read_cm_sp500_doc()
    if len(stamp) != 10:
        raise IndexMembershipError(
            "S&P 500 cache has no usable `_cached_at` — refusing to stamp a snapshot "
            "with today's date instead")
    stamp = parse_as_of(stamp, "S&P 500 cache `_cached_at`")

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


# Kinds whose whole point is that they carry weights. `cm_cache` is deliberately not
# here: a scraped constituent list is not a weighted index and publishes None.
WEIGHTED_KINDS = frozenset({"ishares", "vanguard"})
MIN_WEIGHTED_ROW_RATE = 0.90
# Live totals 2026-09-22: IVV 99.92, EFA 99.48, VONE 99.80, VTWO 97.44, VTHR 97.97.
WEIGHT_TOTAL_RANGE = (90.0, 105.0)


def check_weights(key: str, kind: str, rows: list[dict]) -> None:
    """Refuse a weighted source that came back without usable weights.

    ⛑ WEIGHTS ARE HALF THE REASON THIS ARCHIVE EXISTS AND CANNOT BE BACKFILLED. A
    structurally perfect 503-row response whose `Weight (%)` cells are all `-` passed
    every gate: `_num` returned None for each, `equity_weight_pct` was None, `refresh`
    reported `ok`, and that date was archived weightless — permanently, because a dated
    file is written once. A snapshot that cannot answer the question it was taken for
    is a failure, so this raises and the weekly step reports the lane degraded.
    """
    if kind not in WEIGHTED_KINDS or not rows:
        return
    weighted = [r["weight_pct"] for r in rows if r.get("weight_pct") is not None]
    if len(weighted) < MIN_WEIGHTED_ROW_RATE * len(rows):
        raise IndexMembershipError(
            f"{key}: only {len(weighted)} of {len(rows)} holdings carry a usable "
            f"weight (floor {MIN_WEIGHTED_ROW_RATE:.0%}) — refusing to archive a "
            f"weightless snapshot of a weighted fund; this date cannot be re-fetched")
    total = round(sum(weighted), 4)
    low, high = WEIGHT_TOTAL_RANGE
    if not low <= total <= high:
        raise IndexMembershipError(
            f"{key}: the weights sum to {total}% of the fund, outside {low}-{high}% — "
            f"refusing to archive a basket that does not describe the whole fund")


def check_sp500_count(key: str, rows: list[dict]) -> list[dict]:
    """The S&P 500 count band, applied to the KEY rather than to one source kind.

    ⛑ IT USED TO LIVE INSIDE THE IVV TRANSFORM, which the documented `cm_cache`
    ROLLBACK returns before — so the rollback, the one moment the band is most needed,
    wrote a 450-member `sp500_latest.json` as `ok` under the generic floor. A rule
    about what the S&P 500 IS cannot be attached to where today's copy comes from.
    """
    if key != "sp500":
        return rows
    if not SP500_MIN_COUNT <= len(rows) <= SP500_MAX_COUNT:
        raise IndexMembershipError(
            f"sp500: {len(rows)} constituents is outside {SP500_MIN_COUNT}-"
            f"{SP500_MAX_COUNT} — refusing to replace the list with a truncated file "
            f"or a transitional basket")
    return rows


def collect(key: str) -> tuple[str, str, list[dict]]:
    """`(as_of, as_of_kind, rows)` for one index. Dispatches on the source kind."""
    src = SOURCES[key]
    kind = src["kind"]
    if kind == "ishares":
        as_of, rows = parse_holdings(_fetch_csv(src["pid"]))
        if src.get("post"):
            rows = _POST[src["post"]](rows)
        return as_of, "source", check_sp500_count(key, rows)
    if kind == "vanguard":
        as_of, rows = _fetch_vanguard(src["etf"])
        return as_of, "source", rows
    if kind == "cm_cache":
        as_of, rows = _load_cm_sp500()
        return as_of, "observed", check_sp500_count(key, rows)
    raise IndexMembershipError(f"unknown source kind {kind!r} for {key!r}")


def _source_label(key: str) -> str:
    src = SOURCES[key]
    if src["kind"] == "ishares":
        return HOLDINGS_URL.format(pid=src["pid"])
    if src["kind"] == "vanguard":
        return VANGUARD_API.format(etf=src["etf"], start=1, count=VANGUARD_PAGE)
    return str(SP500_CACHE)


def _write_snapshot_bytes(path: Path, text: str) -> None:
    """Write and flush to disk. The seam a test replaces to simulate a partial write."""
    import os

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def _snapshot_is_usable(path: Path, key: str, count: int | None = None) -> bool:
    """Does this file hold a COMPLETE snapshot of `key`?

    ⛑ Completeness is INTERNAL consistency — it parses, it is this index, and it holds
    as many rows as it claims. It is deliberately NOT agreement with what today's fetch
    returned: a dated file records what that day said, and the fund legitimately
    republishes the same as-of with a slightly different basket. `count` is passed only
    where the caller is verifying a write it has just made.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not (isinstance(doc, dict) and doc.get("key") == key
            and isinstance(doc.get("holdings"), list)
            and isinstance(doc.get("count"), int)
            and len(doc["holdings"]) == doc["count"]):
        return False
    return count is None or doc["count"] == count


def write_snapshot(path: Path, doc: dict) -> None:
    """Write a snapshot durably, then prove it by READING IT BACK.

    ⛑ `os.replace` NEEDS DELETE RIGHTS ON THE TARGET AND DROPBOX DENIES THEM
    (`WinError 5`, measured on this machine 2026-08-21) while permitting an in-place
    write — so the rename fails *after* the temp is written and the stale file survives
    beside a `.new` nothing will ever read. Hence: temp → fsync → try `replace` with a
    short backoff → fall back to writing THROUGH the target, keeping the temp until
    that returns so a crash on the non-atomic path is still recoverable.

    ⛑ AND THE WRITE IS NOT "DONE" UNTIL IT PARSES BACK. An interrupted first write left
    a truncated dated file; the next valid run saw only that the PATH EXISTED, skipped
    it, and returned `ok` — so the sole historical snapshot for that date stayed corrupt
    for ever. Existence is not evidence.
    """
    import os
    import time

    text = json.dumps(doc, indent=1)
    tmp = path.with_name(path.name + ".new")
    _write_snapshot_bytes(tmp, text)
    for attempt in range(4):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if attempt == 3:
                # Dropbox holds the target open; writing through still works.
                log.warning("index_membership: rename refused for %s (Dropbox lock) — "
                            "writing in place", path.name)
                _write_snapshot_bytes(path, text)
                break
            time.sleep(0.25 * (attempt + 1))
        except OSError as e:
            raise IndexMembershipError(f"could not write {path}: {e}") from e
    try:
        tmp.unlink(missing_ok=True)
    except OSError:                       # pragma: no cover - a locked temp is junk
        pass
    if not _snapshot_is_usable(path, doc.get("key"), doc.get("count")):
        raise IndexMembershipError(
            f"{path.name} could not be read back as the snapshot just written — "
            f"refusing to report a half-written file as an archived date")


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
        if is_future_as_of(cached.get("as_of"), today):
            msg = (f"{key}: refresh failed ({e}) and the cached snapshot as_of "
                   f"{cached.get('as_of')} is in the FUTURE against {today.isoformat()} "
                   f"— a future-dated snapshot is a corrupt file, not a fallback")
            log.warning("index_membership[%s]: %s", key, msg)
            return {"key": key, "status": "source_future", "as_of": cached.get("as_of"),
                    "count": cached.get("count"), "age_days": age, "error": msg,
                    "written": None}
        unfit = age is None or age > stale_days_for(key)
        log.warning("index_membership[%s]: refresh failed (%s); serving cached "
                    "snapshot as_of=%s age=%sd%s",
                    key, e, cached.get("as_of"), age,
                    " — REPORTED UNFIT, past STALE_DAYS" if unfit else "")
        return {"key": key, "status": "stale_unfit" if unfit else "stale",
                "as_of": cached.get("as_of"), "count": cached.get("count"),
                "age_days": age, "error": str(e), "written": None}

    # ⛑ A FUTURE AS-OF IS REFUSED BEFORE ANYTHING IS WRITTEN, AND IT IS THE WEDGE
    # CASE. Written once, it becomes the floor the older-than check below compares
    # against, so every subsequent CORRECT file is refused as `source_older` and the
    # lane stays stuck until a human deletes the snapshot; `build_sp500_mirror` refuses
    # a future as_of independently, so the public list would stop updating too. One
    # day of tolerance, because a fund stamps its own trade date (US Eastern for
    # iShares) and a run either side of midnight local is not a corrupt file. Applies
    # to EVERY index -- EAFE and the Russell lanes had the same hole with no mirror
    # behind them to catch it.
    if is_future_as_of(as_of, today):
        msg = (f"{key}: fetched as_of {as_of} is in the FUTURE against {today.isoformat()} "
               f"(tolerance {FUTURE_TOLERANCE_DAYS}d) — refusing to write a snapshot "
               f"that would become an unbeatable floor for every later fetch")
        log.warning("index_membership[%s]: %s", key, msg)
        return {"key": key, "status": "source_future", "as_of": as_of,
                "count": len(rows), "age_days": snapshot_age_days({"as_of": as_of}, today),
                "error": msg, "written": None}

    # ⛑ A VALID BUT OLDER RESPONSE MUST NOT MOVE `<key>_latest.json` BACKWARD.
    # iShares can serve a previous day's file (CDN edge, or a republish), and every
    # snapshot consumer reads `latest` — only the public mirror had a strictly-newer
    # rule, and it guards one file in one repo. Dated files stay immutable either way,
    # and nothing is written: the run reports what it saw and the next fetch fixes it.
    previous = load_latest(key)
    prev_as_of = (previous or {}).get("as_of")
    prev_date, this_date = _as_date(prev_as_of), _as_date(as_of)
    if prev_date and this_date and this_date < prev_date:
        msg = (f"{key}: fetched as_of {as_of} is OLDER than the snapshot on disk "
               f"({prev_as_of}) — refusing to move latest backward")
        log.warning("index_membership[%s]: %s", key, msg)
        return {"key": key, "status": "source_older", "as_of": as_of, "count": len(rows),
                "age_days": snapshot_age_days({"as_of": as_of}, today), "error": msg,
                "written": None}

    check_weights(key, src["kind"], rows)

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
        "caveats": caveats_for(key, src["kind"]) + [_LICENCE],
        "count": len(rows),
        # None, not 0.0, where the source publishes no weights: a zero would read as an
        # index whose constituents carry no weight, which is a claim rather than a gap.
        "equity_weight_pct": round(sum(weighted), 4) if weighted else None,
        "holdings": rows,
    }

    dated = _snapshot_path(key, as_of)
    # ⛑ A DATED SNAPSHOT IS WRITTEN ONCE — BUT IMMUTABILITY PROTECTS A GOOD FILE, NOT A
    # PATH. The fund republishes the same as-of for days, so rewriting a complete file
    # would silently change what the archive says a past day said. An UNUSABLE one
    # (interrupted write, truncated, wrong key) is not a record of anything, and
    # skipping it on `exists()` alone left the only snapshot for that date corrupt for
    # ever. So: rewrite exactly when the file cannot be read back as this snapshot.
    written = None
    if not _snapshot_is_usable(dated, key):
        write_snapshot(dated, doc)
        written = str(dated)
    write_snapshot(_latest_path(key), doc)

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
