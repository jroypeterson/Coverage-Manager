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
import re
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
    "eafe":  {"kind": "ishares",  "pid": "239623", "etf": "EFA", "floor": 400,
              "index": "MSCI EAFE", "fund": "iShares MSCI EAFE ETF (EFA)"},
    # ⛑ RUSSELL MOVED FROM VANGUARD TO iSHARES ON 2026-09-24 (JP; Fable-gated). Measured
    # that day: VTWO served pages from two different month-ends in one response and a
    # VTHR holding had no ticker, so both lanes refused every fetch and every consumer
    # had held 2026-07-31 membership for eight weeks. iShares IWB/IWM publish DAILY
    # through the same path as EFA and IVV: IWB 1,022 and IWM 1,976 listed equities as
    # of 2026-09-23, overlap 0.
    "r1000": {"kind": "ishares", "pid": "239707", "etf": "IWB", "floor": 800,
              "post": "russell_ishares",
              "index": "Russell 1000", "fund": "iShares Russell 1000 ETF (IWB)"},
    "r2000": {"kind": "ishares", "pid": "239710", "etf": "IWM", "floor": 1500,
              "post": "russell_ishares",
              "index": "Russell 2000", "fund": "iShares Russell 2000 ETF (IWM)"},
    # ⛑ DERIVED, NOT IWV. The Russell 3000 IS the Russell 1000 plus the Russell 2000,
    # and iShares' own Russell 3000 fund (IWV) is SAMPLED: 2,598 names against ~2,966,
    # dropping ~370 micro-caps. Built by `refresh_all` from the r1000 and r2000 results
    # of the SAME run, never from `latest` files (two stale caches would agree on a date
    # trivially). Carries no weights: fund weights are fund-relative, so two funds'
    # would sum to ~200%, and no consumer reads a Russell weight (measured).
    "r3000": {"kind": "derived", "from": ("r1000", "r2000"), "etf": "IWB+IWM",
              "floor": 2400, "index": "Russell 3000",
              "fund": "derived: iShares IWB + IWM holdings"},
    # `post` names an S&P-500-only row transform (see `_POST`). Rollback: kind
    # "cm_cache" with no pid/post restores the Wikipedia-scrape source exactly.
    "sp500": {"kind": "ishares", "pid": "239726", "etf": "IVV", "floor": 450,
              "post": "sp500_ivv",
              "index": "S&P 500", "fund": "iShares Core S&P 500 ETF (IVV)"},
}

# ⛑ ROLLBACK TO VANGUARD IS NOT A ONE-LINE CHANGE, and that is stated here rather than
# discovered during an incident. Swap these rows back into SOURCES and the Vanguard
# as_of (month-end, published weeks late) is OLDER than the iShares dated files already
# on disk: `refresh` refuses it as `source_older`, and `_sync_latest_with_archive`
# repoints `latest` at the iShares file every run. The rollback cannot write until
# Vanguard's as_of passes the last iShares date - typically 4-8 weeks.
ROLLBACK_SOURCES: dict[str, dict] = {
    "r1000": {"kind": "vanguard", "etf": "VONE", "floor": 800,
              "index": "Russell 1000", "fund": "Vanguard Russell 1000 ETF (VONE)"},
    "r2000": {"kind": "vanguard", "etf": "VTWO", "floor": 1500,
              "index": "Russell 2000", "fund": "Vanguard Russell 2000 ETF (VTWO)"},
    "r3000": {"kind": "vanguard", "etf": "VTHR", "floor": 2400,
              "index": "Russell 3000", "fund": "Vanguard Russell 3000 ETF (VTHR)"},
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
_RUSSELL_ISHARES = [
    "iShares holdings - a full-replication fund, not FTSE Russell's constituent list "
    "(FTSE sells membership; this is the closest free proxy). `as_of` is the fund's "
    "stated holdings date (`source`).",
    "Non-equity lines and residual lines on 'NO MARKET (E.G. UNLISTED)' (CVRs, vesting "
    "rights, post-deal stubs) are dropped and listed under `excluded`.",
    "Share classes normalised to the dash form (`BRK B` -> BRK-B).",
    "The Russell includes a few non-US-domiciled US-listed companies; `location` says which.",
]
CAVEATS_BY_KEY: dict[tuple[str, str], list[str]] = {
    ("r1000", "ishares"): _RUSSELL_ISHARES,
    ("r2000", "ishares"): _RUSSELL_ISHARES,
    ("r3000", "derived"): [
        "DERIVED: the union of the r1000 (IWB) and r2000 (IWM) snapshots of the same run "
        "- the Russell 3000 is by definition the two combined. iShares' own Russell 3000 "
        "fund (IWV) is sampled and is deliberately not used.",
        "`weight_pct` is None on every holding: the two funds' weights are relative to "
        "different funds and cannot be combined without inventing an index weight.",
        "`derived_from` names the inputs and their as_of; `overlap` counts names held by "
        "both (reconstitution weeks).",
    ],
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


# ⛑ ONE CONSTANT WAS DOING TWO JOBS (Codex round 14, 2026-09-24). `STALE_DAYS` answers
# "may a consumer still USE this cache" — and was also silently deciding "is the ARCHIVE
# missing observations", which it cannot: a Vanguard fetch could fail every week for 119
# days, report plain `stale`, keep the weekly step green, and lose every month-end the
# fund published and then overwrote. This is the second job's own number: past it, a
# failed fetch means history is being lost, even though the cache is still servable.
# Vanguard: a month-end plus ~10 days of publishing lag. iShares publishes daily, so a
# failure spanning more than a weekly cycle is a missed week that cannot be recaptured.
ARCHIVE_GAP_DAYS = 6
ARCHIVE_GAP_DAYS_BY_KIND = {"vanguard": 40}


def archive_gap_days_for(key: str) -> int:
    return ARCHIVE_GAP_DAYS_BY_KIND.get(SOURCES[key]["kind"], ARCHIVE_GAP_DAYS)


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


# Residual lines on the no-market venue may carry up to this much of the fund in total.
# Above it they are not residue: the basket would not describe the index.
MAX_EXCLUDED_WEIGHT_PCT = 0.25


def parse_holdings(text: str) -> tuple[str, list[dict]]:
    """`(as_of, rows)`; see `parse_holdings_ex` for the lines it drops."""
    as_of, rows, _ = parse_holdings_ex(text)
    return as_of, rows


def parse_holdings_ex(text: str) -> tuple[str, list[dict], list[dict]]:
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

    rows, excluded = [], []
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
        asset_class = cell(r, "Asset Class")
        t = cell(r, "Ticker")
        if not asset_class:
            # ⛑ AN UNCLASSIFIABLE ROW IS NOT A NON-EQUITY ROW. `!= "Equity"` sent a
            # blank straight to `continue`, which is a silent deletion of whatever the
            # row was; only an EMPTY row may be skipped.
            if t or cell(r, "Name"):
                raise IndexMembershipError(
                    f"holding {t or cell(r, 'Name')!r} has no Asset Class — refusing "
                    f"to classify it as non-equity and drop it")
            continue
        if asset_class != "Equity":
            continue                      # cash, futures and collateral carry `-`
        # ⛑ A NO-MARKET LINE IS DROPPED BEFORE THE TICKER CHECK, AND LISTED (Fable,
        # 2026-09-24). IWM and IWV carry `ARCELLX INC CVR` and two `OMNIAB ... VESTING
        # Prvt` lines: Equity, no ticker, 0.00% weight, venue NO MARKET — contingent
        # value and vesting rights, not constituents. The blank-ticker guard below fired
        # on them first and took both lanes down. The exclusion lived only in the S&P
        # transform, so EFA had none at all; it is ONE rule here now. It keys on the
        # venue alone — not "and weight 0", because a residual at 0.01% would then raise
        # and a take-private stub would become the outage. The summed weight is capped
        # instead, so a basket that is mostly "no market" is still refused.
        if cell(r, "Exchange").upper() == UNLISTED_EXCHANGE:
            excluded.append({"ticker": t, "name": cell(r, "Name"),
                             "weight_pct": _num(cell(r, "Weight (%)"))})
            continue
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
        if not cell(r, "Exchange"):
            raise IndexMembershipError(
                f"equity holding {t} has a blank Exchange — the unlisted-residual "
                f"filter would match nothing and silently keep non-constituents")
        rows.append({
            "ticker": t,
            "name": cell(r, "Name"),
            "sector": cell(r, "Sector"),
            "weight_pct": _num(cell(r, "Weight (%)")),
            "location": cell(r, "Location"),
            "exchange": cell(r, "Exchange"),
            "market_currency": cell(r, "Market Currency"),
            "market_value_usd": _num(cell(r, "Market Value")),
        })
    # ⛑ AN UNREADABLE WEIGHT IS NOT A ZERO WEIGHT (Codex round 16). `or 0.0` let a
    # no-market line whose weight read `-` slip under the cap below whatever it really
    # held. Every such line seen live reads "0.00", so refusing costs nothing today.
    unknown = [e["name"] for e in excluded if e["weight_pct"] is None]
    if unknown:
        raise IndexMembershipError(
            f"no-market line(s) {unknown!r} carry an unreadable weight — cannot show they "
            f"are residue rather than a real share of the fund")
    dropped = round(sum(e["weight_pct"] for e in excluded), 4)
    if dropped > MAX_EXCLUDED_WEIGHT_PCT:
        raise IndexMembershipError(
            f"{len(excluded)} no-market line(s) carry {dropped}% of the fund (cap "
            f"{MAX_EXCLUDED_WEIGHT_PCT}%) — that is not residue; the basket would not "
            f"describe the index")
    return as_of, rows, excluded


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

# ⛑ THE VENDOR WRITES "MISSING" SEVERAL WAYS, AND EACH READER GUESSED SEPARATELY. A
# blank `Asset Class` read as "not Equity" and the row was DROPPED (blank MMM's cell
# and 3M silently leaves the S&P 500 while every count still looks right); an
# `Exchange` of `-` read as a populated venue, so the unlisted-residual filter never
# fired for it. Together: 503 rows, ~100% of weight, passing joins, HOLX in and MMM
# out. One sentinel set, read through one helper, at every site.
MISSING_CELLS = frozenset({"", "-", "--", "N/A", "NA", "NULL", "NONE"})


def cell(row: dict, name: str) -> str:
    """A holdings cell, with every vendor sentinel normalised to the empty string."""
    v = str(row.get(name) or "").strip()
    return "" if v.upper() in MISSING_CELLS else v

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


def _russell_ishares_rows(rows: list[dict]) -> list[dict]:
    """IWB/IWM equity rows -> Russell membership rows: dash share classes, and refuse
    two lines that normalise to one ticker. No Wikipedia join: that cache is S&P-only,
    and its join floor would refuse every Russell basket."""
    out, seen = [], {}
    for r in rows:
        t = normalise_us_ticker(r["ticker"])
        if t in seen:
            raise IndexMembershipError(
                f"lines {seen[t]!r} and {r['ticker']!r} both normalise to {t} - "
                f"refusing to guess which is the constituent")
        seen[t] = r["ticker"]
        out.append({**r, "ticker": t})
    return out


def _sp500_ivv_rows(rows: list[dict]) -> list[dict]:
    """IVV equity rows -> S&P 500 membership rows. See the docstring's IVV section.

    Drops unlisted residuals, normalises share classes, and joins name/sector/
    sub_industry from the Wikipedia cache (IVV's only as the fallback).
    """
    _, info = _read_cm_sp500()
    out, seen = [], {}
    for r in rows:
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


_POST = {"sp500_ivv": _sp500_ivv_rows, "russell_ishares": _russell_ishares_rows}


VANGUARD_READ_ATTEMPTS = 2        # pairs of full reads before giving up


def _fetch_vanguard(etf: str, *, page: int = VANGUARD_PAGE,
                    retries: int = VANGUARD_RETRIES) -> tuple[str, list[dict]]:
    """Read the fund TWICE and accept it only when both full reads agree.

    ⛑ PAGINATION IS ONLY SAFE WHILE THE THING BEING PAGINATED STANDS STILL, AND
    AGREEING PAGE HEADERS DO NOT PROVE IT DID (Codex round 14, 2026-09-24). Every page
    had to state the same `asOfDate` and `size`, but Vanguard can republish the SAME
    date at the SAME size with different members. A republish between page 1 and
    page 2 (drop T0, add T1000) produced T0..T499 + T501..T1000 — an outgoing name
    kept, an entrant added, T500 missing: neither version of the fund, 1,000 unique
    rows, 100% of weight, so every count and weight gate passed and it was archived.
    Nothing inside one read can detect that; a second read can, because a hybrid is a
    transient and the fund itself is not. Cost: one more pass over ~2-6 small pages.
    """
    reads = []
    for _ in range(VANGUARD_READ_ATTEMPTS):
        a = _fetch_vanguard_once(etf, page=page, retries=retries)
        b = _fetch_vanguard_once(etf, page=page, retries=retries)
        if _vanguard_signature(a) == _vanguard_signature(b):
            return b
        reads.append((a[0], b[0]))
        log.warning("index_membership[%s]: two consecutive reads disagreed (the fund "
                    "republished mid-fetch) — reading again", etf)
    raise IndexMembershipError(
        f"{etf}: {VANGUARD_READ_ATTEMPTS} pairs of consecutive full reads did not agree "
        f"(as_of pairs {reads}) — the fund is changing under the fetch, so there is no "
        f"stable snapshot to archive")


def _vanguard_signature(read: tuple[str, list[dict]]) -> tuple:
    as_of, rows = read
    return as_of, tuple(sorted((r["ticker"], r["weight_pct"]) for r in rows))


def _fetch_vanguard_once(etf: str, *, page: int = VANGUARD_PAGE,
                         retries: int = VANGUARD_RETRIES) -> tuple[str, list[dict]]:
    """Paginate one Vanguard ETF's equity holdings — ONE read; see `_fetch_vanguard`.

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
        # ⛑ EVERY PAGE MUST DESCRIBE THE SAME SNAPSHOT. `size` and `asOfDate` were read
        # from the FIRST page only, so a fetch straddling Vanguard's monthly republish
        # combined an Aug 31 page with a Sep 30 page into one snapshot stamped Aug 31 —
        # half its membership from September, and nothing on the artifact to tell a
        # later reader. Pagination is only safe while the thing being paginated stands
        # still, so a page that disagrees is refused rather than merged.
        # ⛑ ABSENT IS NOT AGREEMENT. The rule compared only when a later page PROVIDED
        # the field, so a second page carrying neither `asOfDate` nor `size` joined an
        # August snapshot even when it came from the September republish — the exact
        # merge the rule exists to stop, walking through the hole in the rule.
        if not d.get("asOfDate"):
            raise IndexMembershipError(
                f"{etf}: page at start={start} states no asOfDate — a page that cannot "
                f"be matched to the others must not be merged into their snapshot")
        if d.get("size") is None:
            raise IndexMembershipError(
                f"{etf}: page at start={start} states no size — a page that cannot be "
                f"matched to the others must not be merged into their snapshot")
        page_as_of = parse_as_of(d["asOfDate"], etf)
        if as_of is None:
            as_of = page_as_of
        elif page_as_of != as_of:
            raise IndexMembershipError(
                f"{etf}: page at start={start} is as of {page_as_of} but the first page "
                f"was {as_of} — the fund republished mid-fetch; refusing to merge two "
                f"snapshots into one")
        page_size = d["size"]
        if size is None:
            size = page_size
        elif page_size != size:
            raise IndexMembershipError(
                f"{etf}: page at start={start} reports size {page_size} against {size} "
                f"on the first page — one fund cannot have two sizes mid-fetch")

        # ⛑ A VALID HTTP-200 BODY OF THE WRONG SHAPE IS OUR ERROR, NOT PYTHON'S.
        # `{"fund": ["maintenance"]}` raised a raw AttributeError, which `refresh_all`
        # did not catch — so a maintenance page at r1000 aborted the whole lane.
        fund = d.get("fund")
        if fund is not None and not isinstance(fund, dict):
            raise IndexMembershipError(
                f"{etf}: page at start={start} has `fund` as {type(fund).__name__}, "
                f"not an object — the endpoint served something that is not holdings")
        ents = ((fund or {}).get("entity")) or []
        if not isinstance(ents, list):
            raise IndexMembershipError(
                f"{etf}: page at start={start} has `fund.entity` as "
                f"{type(ents).__name__}, not a list")
        if not ents:
            break
        for e in ents:
            if not isinstance(e, dict):
                raise IndexMembershipError(
                    f"{etf}: a holding on the page at start={start} is "
                    f"{type(e).__name__}, not an object")
            # ⛑ THE SAME SENTINEL READER THE iSHARES PATH USES. A blank ticker was
            # silently DROPPED here (899 of 900 archived at ~99.9% of weight, over the
            # floor, nothing reported) and `-` or `N/A` became a literal constituent
            # that every later check read as populated.
            raw_ticker = cell(e, "ticker")
            if not raw_ticker:
                raise IndexMembershipError(
                    f"{etf}: a holding on the page at start={start} has no ticker "
                    f"(name {cell(e, 'longName') or '?'}) — dropping it would shrink "
                    f"the index by a real constituent")
            t = normalise_share_class(raw_ticker)
            out.append({"ticker": t, "name": cell(e, "longName") or t,
                        "sector": cell(e, "sector"),
                        "weight_pct": _num(cell(e, "percentWeight")),
                        "location": "", "exchange": "", "market_currency": "",
                        "market_value_usd": None})
        start += len(ents)
        if size and start > size:
            break
        time.sleep(VANGUARD_PAUSE)
    # ⛑ AN EMPTY PAGE IS NOT EVIDENCE THAT THE FETCH IS COMPLETE. The loop broke on
    # one without asking whether it had reached the advertised `size`: five 500-row
    # VTHR pages then an empty one returned 2,500 holdings, cleared the 2,400 floor at
    # ~95% of weight, and archived an index missing 500 constituents as `ok`.
    #
    # 🔻 ONE-SIDED BY CHOICE: this refuses a SHORT fetch. Whether `size` can legitimately
    # exceed the rows served (a non-stock line, a same-day correction) could not be
    # measured — on 2026-09-22 the endpoint served non-JSON for VONE and VTHR through
    # four retries each — so a strict equality could take the lane down over a vendor
    # convention nobody has verified. A surplus is logged instead.
    if size is not None and len(out) < size:
        raise IndexMembershipError(
            f"{etf}: collected {len(out)} holdings against an advertised size of "
            f"{size} — the fetch stopped short; refusing to archive a partial index")
    if size is not None and len(out) > size:
        log.warning("index_membership[%s]: %d holdings against an advertised size of "
                    "%d — serving the larger list", etf, len(out), size)
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


_EXCLUDED: dict[str, list[dict]] = {}


def collect(key: str) -> tuple[str, str, list[dict]]:
    """`(as_of, as_of_kind, rows)` for one index. Dispatches on the source kind."""
    src = SOURCES[key]
    kind = src["kind"]
    if kind == "ishares":
        as_of, rows, excluded = parse_holdings_ex(_fetch_csv(src["pid"]))
        if src.get("post"):
            rows = _POST[src["post"]](rows)
        # `collect` keeps its three-value contract (callers and tests unpack it); the
        # dropped no-market lines travel beside it for `refresh` to put on the snapshot.
        _EXCLUDED[key] = excluded
        return as_of, "source", check_sp500_count(key, rows)
    if kind == "vanguard":
        as_of, rows = _fetch_vanguard(src["etf"])
        return as_of, "source", rows
    if kind == "cm_cache":
        as_of, rows = _load_cm_sp500()
        return as_of, "observed", check_sp500_count(key, rows)
    if kind == "derived":
        # Reached only when `refresh_all` could not build it from this run's inputs;
        # the fallback path then serves the last good derived snapshot, if any.
        raise IndexMembershipError(
            f"{key}: derived from {', '.join(src['from'])}, which did not all refresh "
            f"`ok` in this run - not rebuilding it from older files")
    raise IndexMembershipError(f"unknown source kind {kind!r} for {key!r}")


def _derive_union(key: str, inputs: list[dict]) -> tuple[str, str, list[dict], list]:
    """Union of the input snapshots (just written by this run). Raises on disagreement."""
    dates = {d["as_of"] for d in inputs}
    if len(dates) != 1:
        raise IndexMembershipError(
            f"{key}: inputs are as of {sorted(dates)} - a union of two different days "
            f"is not a snapshot of either")
    seen, rows, overlap = set(), [], 0
    for d in inputs:
        for h in d["holdings"]:
            if h["ticker"] in seen:
                overlap += 1
                continue
            seen.add(h["ticker"])
            rows.append({**h, "weight_pct": None, "from": d["key"]})
    if overlap > 0.01 * len(rows):
        raise IndexMembershipError(
            f"{key}: {overlap} names are held by more than one input (cap 1% of "
            f"{len(rows)}) - the funds are not partitioning the index")
    meta = {"derived_from": [{"key": d["key"], "as_of": d["as_of"]} for d in inputs],
            "overlap": overlap}
    return dates.pop(), "source", rows, meta


def _source_label(key: str) -> str:
    src = SOURCES[key]
    if src["kind"] == "ishares":
        return HOLDINGS_URL.format(pid=src["pid"])
    if src["kind"] == "vanguard":
        return VANGUARD_API.format(etf=src["etf"], start=1, count=VANGUARD_PAGE)
    if src["kind"] == "derived":
        return "union of " + " + ".join(src["from"])
    return str(SP500_CACHE)


def _write_snapshot_bytes(path: Path, text: str) -> None:
    """Write and flush to disk. The seam a test replaces to simulate a partial write."""
    import os

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def snapshot_problem(key: str, doc: dict) -> str:
    """Why this snapshot is not a usable record of `key`, or "" if it is.

    ⛑ AN INVARIANT APPLIED ON ONE PATH IS APPLIED NOWHERE. Round 10 refused a
    weightless response at FETCH time; the cached-fallback path and the dated-file
    check knew nothing about it, so a weightless ishares snapshot plus an HTML-with-200
    fetch reported `stale`, kept the weekly step green, and left the weighted archive
    unusable — and the weightless DATED file still counted as a valid immutable record,
    so a later good fetch for the same as_of repaired `latest` and never the archive.
    Same shape as the round-3 future-date gap, so the rules live in ONE function that
    every acceptance path calls.

    Applied to a doc, not to a fetch: the parse-time rules (HTML shell, truncation,
    duplicate or missing columns, duplicate as-of headers) describe a CSV and cannot be
    restated here; what a stored snapshot can still be checked for is its shape, its
    tickers, the S&P 500 count band and the weights a weighted kind must carry.
    """
    # ⛑ WHICH INDEX IS THIS? Nothing asked, so a current, weighted, 503-row EAFE
    # document stored as `sp500_latest.json` served as a stale S&P 500 fallback — and
    # `build_sp500_mirror` would have rendered it into the PUBLIC sources/sp500.txt and
    # sp500_names.json. Every other property was impeccable; it was simply another
    # index. The filename is not evidence, the document must say so itself.
    stored_key = doc.get("key")
    if stored_key != key:
        return (f"the document describes {stored_key!r}, not {key!r} — a snapshot of "
                f"another index is not a fallback for this one")
    holdings = doc.get("holdings")
    if not isinstance(holdings, list) or not holdings:
        return "snapshot carries no holdings"
    if not isinstance(doc.get("count"), int) or doc["count"] != len(holdings):
        return (f"count {doc.get('count')!r} does not match the "
                f"{len(holdings)} holdings stored")
    blank = [h for h in holdings
             if not isinstance(h, dict) or not str(h.get("ticker") or "").strip()]
    if blank:
        return f"{len(blank)} holding(s) carry no ticker"
    # ⛑ THE FLOOR IS A FACT ABOUT THE INDEX, SO IT BINDS EVERY COPY OF IT. It lived on
    # the fresh-fetch path alone, so a recent cached EAFE doc holding ONE ticker at 100%
    # served as a fallback and the weekly step stayed green, and a truncated dated file
    # counted as an immutable record. (A test written in round 11 asserted exactly that
    # wrong result; it was fixed with this change, not worked around.)
    floor = (SOURCES.get(key) or {}).get("floor")
    if isinstance(floor, int) and len(holdings) < floor:
        return (f"{len(holdings)} holdings is below the {key} credibility floor "
                f"of {floor}")
    kind = doc.get("kind") or (SOURCES.get(key) or {}).get("kind") or ""
    try:
        check_sp500_count(key, holdings)
        check_weights(key, kind, holdings)
    except IndexMembershipError as e:
        return str(e)
    return ""


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
    if not isinstance(doc, dict) or doc.get("key") != key:
        return False
    if snapshot_problem(key, doc):
        return False
    return count is None or doc["count"] == count


def _dated_is_usable(path: Path, key: str, as_of: str) -> bool:
    """A dated artifact is valid only if it IS the snapshot that date's name claims.

    ⛑ NOTHING BOUND THE DOCUMENT TO THE DATE IN ITS FILENAME, so a complete Sep 14 doc
    stored as `sp500_2026-09-21.json` passed every check: the good Sep 21 fetch
    reported `ok` with `written=None` and the Sep 21 archive held Sep 14 membership
    permanently — the one file in this system that cannot be re-fetched.
    """
    if not _snapshot_is_usable(path, key):
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("as_of") == as_of
    except (OSError, ValueError):          # pragma: no cover - re-read of a good file
        return False


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
    # ⛑ THE VERIFICATION COMPARES THE BYTES, NOT A SHAPE. `key` and `count` are
    # satisfied by any document of the same shape — including the one ALREADY on disk,
    # which is exactly the case a failed rename leaves behind.
    try:
        landed = path.read_text(encoding="utf-8")
    except OSError as e:
        raise IndexMembershipError(f"{path.name} could not be read back: {e}") from e
    if landed != text:
        raise IndexMembershipError(
            f"{path.name} could not be read back as the snapshot just written "
            f"({len(landed)} bytes on disk against {len(text)} written) — refusing to "
            f"report a half-written or stale file as an archived date")


def _snapshot_path(key: str, as_of: str) -> Path:
    return OUT_DIR / f"{key}_{as_of}.json"


def _latest_path(key: str) -> Path:
    return OUT_DIR / f"{key}_latest.json"


# Exactly `<key>_YYYY-MM-DD.json`. Anchored so `<key>_latest.json` and a Dropbox
# `<key>_<date> (X's conflicted copy <date>).json` can never be read as a snapshot:
# a conflicted copy is whatever another machine last held, and trusting one would
# let sync inject membership into the archive's view of itself.
_DATED_NAME = re.compile(r"^(?P<key>[a-z0-9]+)_(?P<d>\d{4}-\d{2}-\d{2})\.json$")


def newest_usable_dated(key: str, today: date | None = None) -> dict | None:
    """The newest DATED snapshot of `key` that is complete and not future-dated.

    ⛑ `latest` IS A COPY, AND A COPY CAN FALL BEHIND ITS SOURCE (Codex round 14). The
    dated write and the latest write are two operations: a run killed between them, or
    Dropbox restoring an older `latest`, left a valid newer snapshot on disk that
    nothing consulted — the fallback served the older list, and the monotonicity guard
    judged a fetch only against `latest`, so a response older than the archive moved
    `latest` behind it. Both now take the newer of the two.
    """
    today = today or date.today()
    if not OUT_DIR.exists():
        return None
    dated = []
    for p in OUT_DIR.glob(f"{key}_*.json"):
        m = _DATED_NAME.match(p.name)
        if m and m["key"] == key:
            dated.append((m["d"], p))
    for as_of, p in sorted(dated, reverse=True):
        if is_future_as_of(as_of, today) or not _dated_is_usable(p, key, as_of):
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):       # pragma: no cover - just validated
            continue
    return None


def _newer(a: dict | None, b: dict | None) -> dict | None:
    """The document with the later `as_of`; `a` wins ties and unparseable dates."""
    if b is None:
        return a
    if a is None:
        return b
    da, db = _as_date(a.get("as_of")), _as_date(b.get("as_of"))
    return b if (da is None or (db is not None and db > da)) else a


def _repair_latest(key: str, doc: dict, was: dict | None) -> None:
    """Point `latest` at a newer dated snapshot it had fallen behind. Best-effort:
    a failure is logged, never raised — this is a repair, not the run's product."""
    try:
        write_snapshot(_latest_path(key), doc)
        log.warning("index_membership[%s]: latest (as_of %s) had fallen behind the "
                    "archive — repointed to the dated snapshot as_of %s",
                    key, (was or {}).get("as_of"), doc.get("as_of"))
    except Exception as e:                    # noqa: BLE001 - see the docstring
        log.error("index_membership[%s]: could not repair latest: %s", key, e)


# Sources whose `ticker` is a LOCAL exchange code, so a ticker alone is not an identity
# (the EAFE caveat). Measured live 2026-09-24: 657 EFA lines, 653 ticker strings — SAN is
# Santander (Madrid) AND Sanofi (Paris); RIO, IAG and 1928 likewise.
LOCAL_TICKER_KEYS = frozenset({"eafe"})


def _member_set(key: str, doc: dict) -> frozenset:
    """Membership identities for the republish log (Codex round 16). `TICKER@EXCHANGE`
    for a local-ticker source, so one of two companies sharing a code can be seen to
    leave; the bare ticker elsewhere, so a US venue move is not logged as a change."""
    holdings = doc.get("holdings") or []
    if key in LOCAL_TICKER_KEYS:
        return frozenset(f"{h.get('ticker')}@{h.get('exchange')}" for h in holdings)
    return frozenset(h.get("ticker") for h in holdings)


def _republish_log_path(key: str) -> Path:
    return OUT_DIR / f"{key}_republish_log.jsonl"


def _log_has_date(key: str, as_of: str) -> bool:
    return _last_logged(key, as_of) is not None


def _last_logged(key: str, as_of: str) -> frozenset | None:
    """The membership of the most recent log line for `as_of`, or None."""
    previous = None
    try:
        with _republish_log_path(key).open(encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue                 # a torn line must not block the log
                if isinstance(entry, dict) and entry.get("as_of") == as_of:
                    previous = frozenset(entry.get("members") or entry.get("tickers") or [])
    except FileNotFoundError:
        pass
    return previous


def _append_line(path: Path, line: str) -> None:
    """Append one JSON line durably.

    ⛑ A TORN LAST LINE IS CLOSED BEFORE APPENDING (Codex round 16). A crash can leave a
    final fragment with no newline; appending straight onto it fused two observations
    into one unparseable line and lost both. And the append is fsync'd like every
    snapshot write: `latest` was durable while the log tail was not, so a power cut
    could keep the new basket and lose the only record of the correction.
    """
    import os
    needs_newline = False
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            if f.tell():
                f.seek(-1, 2)
                needs_newline = f.read(1) != b"\n"
    except FileNotFoundError:
        pass
    with path.open("a", encoding="utf-8") as f:
        if needs_newline:
            f.write("\n")
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def _log_republish_if_changed(key: str, as_of: str, base: Path, doc: dict) -> Path | None:
    """Append a line when a same-as_of republish changes MEMBERSHIP. Never rewrites.

    ⛑ WHY A LOG AND NOT REVISION FILES (JP, 2026-09-24, after Codex rounds 14-15). A fund
    can republish the same as_of with different members; only `latest` took the new
    basket, so the correction was lost when the date moved on. Round 14 recorded it as
    `<key>_<as_of>_rev<N>.json` files, and round 15 found four defects in that chain —
    numbering, `supersedes` pointers going false under a rewritten base, and every reader
    of dated files having to learn which version stood for a date. This keeps the
    evidence and drops the chain: the dated base file stays the ONE snapshot every reader
    uses (the first observation, unchanged semantics), and this append-only JSON-lines
    file records what changed afterwards.

    Each line is self-contained — `as_of`, `observed_at`, `count`, the FULL `tickers`
    list, and `added`/`removed` against the previous observation of that date — so a
    line stays true even if the base file is later found corrupt and rewritten, and the
    membership at any observation can be read off one line without replaying the rest.
    Compared with the MOST RECENT observation of the date (the last line for it, else
    the base), so A -> B -> A records both changes. Weight-only changes are not logged.
    `.jsonl` never matches a dated-snapshot pattern, so no snapshot reader sees it.

    🔻 ACCEPTED, NOT GUARDED (JP, 2026-09-24, Codex round 16): there is no lock. Two runs
    writing the same key at once could interleave lines, and diff one against the base
    rather than the other's line. Exactly one scheduled job writes this directory weekly,
    and a lock file would add a failure mode (a stale lock stopping the archive) to
    remove one that needs two concurrent runs. Revisit if a second writer is ever added.
    """
    path = _republish_log_path(key)
    previous = _last_logged(key, as_of)
    if previous is None:
        try:
            previous = _member_set(key, json.loads(base.read_text(encoding="utf-8")))
        except (OSError, ValueError):        # pragma: no cover - base just validated
            return None
    new = _member_set(key, doc)
    if new == previous:
        return None
    # `members` is the identity set compared (TICKER@EXCHANGE for a local-ticker
    # source); `count` is the number of holdings, which a bare-ticker set undercounted.
    entry = {"key": key, "as_of": as_of, "observed_at": doc.get("fetched_at"),
             "count": len(doc.get("holdings") or []), "added": sorted(new - previous),
             "removed": sorted(previous - new), "members": sorted(new)}
    _append_line(path, json.dumps(entry))
    log.warning("index_membership[%s]: as_of %s republished with different membership "
                "(+%d/-%d) — logged to %s", key, as_of, len(entry["added"]),
                len(entry["removed"]), path.name)
    return path


def _sync_latest_with_archive(key: str, today: date) -> None:
    """Repoint `latest` at the newest usable dated snapshot when it is missing, broken,
    or older than it. A FUTURE-dated `latest` is left alone: it is a corrupt file the
    future guard reports by name, and replacing it here would hide that report."""
    latest = load_latest(key)
    if latest is not None and is_future_as_of(latest.get("as_of"), today):
        return
    dated = newest_usable_dated(key, today)
    if dated is None:
        return
    if latest is None or snapshot_problem(key, latest) or _newer(latest, dated) is dated:
        _repair_latest(key, dated, latest)


def _fetch_state_path(key: str) -> Path:
    return OUT_DIR / f"{key}_fetch_state.json"


def _record_fetch(key: str, fetched_at: str) -> None:
    """Best-effort: the snapshot has already landed; a lost clock only risks a false gap."""
    try:
        write_snapshot(_fetch_state_path(key), {"key": key, "last_ok_fetch": fetched_at})
    except Exception as e:                    # noqa: BLE001
        log.error("index_membership[%s]: could not record the fetch time: %s", key, e)


def last_ok_fetch(key: str, doc: dict | None) -> str | None:
    """The most recent successful fetch we can prove: the fetch-state file or the doc.

    ⛑ A SNAPSHOT'S `fetched_at` IS WHEN THAT SNAPSHOT WAS FIRST FETCHED (Codex round 15).
    The archive keeps a date's first fetch time; later successful fetches of the same
    date only refreshed `latest`. So a `latest` restored from the archive carried a
    weeks-old time and reported an archive gap the day after a successful fetch. The
    clock is now its own small file, and the newer of the two readings wins.
    """
    stamps = [(doc or {}).get("fetched_at")]
    try:
        stamps.append(json.loads(_fetch_state_path(key).read_text(encoding="utf-8"))
                      .get("last_ok_fetch"))
    except (OSError, ValueError, AttributeError):
        pass
    parsed = []
    for s in stamps:
        try:
            parsed.append((datetime.fromisoformat(s), s))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return None
    try:
        return max(parsed)[1]
    except TypeError:                         # naive vs aware: compare as dates
        return max(parsed, key=lambda x: x[0].date())[1]


def days_since_fetch(doc: dict | None, today: date) -> int | None:
    """Days since `doc` was last successfully FETCHED -- the process-ran clock.

    ⛑ NOT the data's `as_of` age. Vanguard's August month-end had still not been
    published by 2026-09-22 (`r2000_latest` rewritten that day, still as-of 07-31), so
    an as_of clock would call an honest vendor lag an archive gap. What loses history is
    OUR fetches failing across a publication, and `fetched_at` is when one last worked.
    """
    raw = (doc or {}).get("fetched_at")
    if not raw:
        return None
    try:
        return (today - datetime.fromisoformat(raw).date()).days
    except (TypeError, ValueError):
        return None


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


def refresh(key: str = "eafe", *, today: date | None = None,
            collected: tuple | None = None, sink: dict | None = None) -> dict:
    """Fetch, validate, and write a dated snapshot. Returns a status dict.

    Never raises on a fetch failure when a cached snapshot exists — it falls back and
    reports the age, because dropping the list is worse than serving a known-old one.
    It DOES raise when there is no fallback: an empty index must not be a valid result.
    """
    src = SOURCES[key]
    floor = src["floor"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    # Before anything else, so EVERY exit below -- fetch failure, source_older,
    # source_future, cache_unusable, ok -- starts from a `latest` no older than the
    # archive. A repair made on one exit path only is how this module has shipped the
    # same hole twice (Fable, round-14 plan review).
    _sync_latest_with_archive(key, today)

    try:
        _EXCLUDED.pop(key, None)            # never carry a previous run's list forward
        res = collected if collected is not None else collect(key)
        as_of, as_of_kind, rows = res[:3]
        extra = res[3] if len(res) > 3 else _EXCLUDED.pop(key, None)
    except IndexMembershipError as e:
        cached = load_latest(key)            # already synced with the archive above
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
        problem = snapshot_problem(key, cached)
        if problem:
            msg = (f"{key}: refresh failed ({e}) and the cached snapshot as_of "
                   f"{cached.get('as_of')} is not a usable record — {problem}")
            log.warning("index_membership[%s]: %s", key, msg)
            return {"key": key, "status": "cache_unusable", "as_of": cached.get("as_of"),
                    "count": cached.get("count"), "age_days": age, "error": msg,
                    "written": None}
        unfit = age is None or age > stale_days_for(key)
        since = days_since_fetch({"fetched_at": last_ok_fetch(key, cached)}, today)
        gap = not unfit and (since if since is not None else age) > archive_gap_days_for(key)
        log.warning("index_membership[%s]: refresh failed (%s); serving cached "
                    "snapshot as_of=%s age=%sd%s",
                    key, e, cached.get("as_of"), age,
                    " — REPORTED UNFIT, past STALE_DAYS" if unfit
                    else " — ARCHIVE GAP: observations are being lost" if gap else "")
        return {"key": key,
                "status": "stale_unfit" if unfit else "stale_archive_gap" if gap else "stale",
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
    # ⛑ THE GUARD BECOMING THE OUTAGE. The future check ran on the EXCEPTION path only,
    # so after a SUCCESSFUL fetch a corrupt `latest.as_of = 2026-12-31` made a valid
    # 2026-09-21 snapshot `source_older` and nothing was written — every good fetch
    # blocked until December or a manual delete, by the guard that exists to keep the
    # archive correct. A baseline is only a baseline while it is itself usable: a
    # future-dated or otherwise broken `latest` is the thing this fetch should FIX.
    previous = load_latest(key)
    if previous is not None and (is_future_as_of(previous.get("as_of"), today)
                                 or snapshot_problem(key, previous)):
        log.warning("index_membership[%s]: the stored latest (as_of %s) is not a usable "
                    "baseline — this fetch replaces it", key, previous.get("as_of"))
        previous = None
    # The baseline is the newer of `latest` and the newest usable dated snapshot --
    # judged against `latest` alone, a restored-older `latest` let a response older
    # than the archive return `ok` and move `latest` behind it (Codex round 14).
    previous = _newer(previous, newest_usable_dated(key, today))
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
        # The fund's own ticker, so a consumer can label the source without a table
        # of its own (sector_chart_pack hardcoded "VONE" in its banner).
        "etf": src.get("etf"),
        "holdings": rows,
    }
    if isinstance(extra, list):                     # ishares: dropped no-market lines
        doc["excluded"] = extra
    elif isinstance(extra, dict):                   # derived: provenance
        doc.update(extra)

    dated = _snapshot_path(key, as_of)
    # ⛑ A DATED SNAPSHOT IS WRITTEN ONCE — BUT IMMUTABILITY PROTECTS A GOOD FILE, NOT A
    # PATH. The fund republishes the same as-of for days, so rewriting a complete file
    # would silently change what the archive says a past day said. An UNUSABLE one
    # (interrupted write, truncated, wrong key) is not a record of anything, and
    # skipping it on `exists()` alone left the only snapshot for that date corrupt for
    # ever. So: rewrite exactly when the file cannot be read back as this snapshot.
    written = None
    if not _dated_is_usable(dated, key, as_of):
        write_snapshot(dated, doc)
        written = str(dated)
        # ⛑ If the log already describes this date, the rewrite is an OBSERVATION it must
        # record (Codex round 16): otherwise the next one is compared with the last logged
        # basket, and a C -> B move after the rewrite is lost.
        if _log_has_date(key, as_of):
            _log_republish_if_changed(key, as_of, dated, doc)
    else:
        logged = _log_republish_if_changed(key, as_of, dated, doc)
        if logged is not None:
            written = str(logged)
    write_snapshot(_latest_path(key), doc)
    _record_fetch(key, doc["fetched_at"])
    if sink is not None:
        sink[key] = doc                     # the doc THIS call wrote, for a derived key

    return {"key": key, "status": "ok", "as_of": as_of, "count": len(rows),
            "age_days": snapshot_age_days(doc, today), "error": None,
            "written": written}


def refresh_all(*, today: date | None = None) -> list[dict]:
    """Every index, independently.

    ⛑ ONE INDEX FAILING MUST NOT SKIP THE REST, AND "FAILING" MEANS ANY EXCEPTION. The
    first version raised out of the loop, so a Vanguard outage took the EAFE and S&P
    snapshots with it — and the whole point of this lane is that a week not captured
    cannot be recaptured. The second version caught only `IndexMembershipError`, which
    is the same outage one step in: a malformed page raised `AttributeError` at r1000
    and everything after it was skipped. A failure with no cached fallback becomes a
    `failed` row carrying the exception's class name, never an exception.
    """
    out, written = [], {}
    for k in SOURCES:
        try:
            src = SOURCES[k]
            if src["kind"] == "derived":
                # ⛑ From the docs THIS run wrote, held in memory — never a re-read of
                # `latest` (Codex round 16): another run or a Dropbox sync can replace a
                # file between the input's `ok` and the derivation, and `derived_from`
                # would then describe a union that never happened.
                if all(i in written for i in src["from"]):
                    inputs = [written[i] for i in src["from"]]
                    out.append(refresh(k, today=today, sink=written,
                                       collected=_derive_union(k, inputs)))
                    continue
            out.append(refresh(k, today=today, sink=written))
        except Exception as e:                       # noqa: BLE001 - see the docstring
            # ⛑ ANY exception, not only ours. The loop caught `IndexMembershipError`
            # alone, so one raw `AttributeError` from a malformed Vanguard page took
            # r2000, r3000, eafe and sp500 down with it — the per-index independence
            # this function exists for, undone by an error TYPE. The class name travels
            # in the message so an unexpected failure is still diagnosable.
            log.error("index_membership[%s]: %s: %s", k, type(e).__name__, e)
            out.append({"key": k, "status": "failed", "as_of": None, "count": None,
                        "age_days": None, "error": f"{type(e).__name__}: {e}",
                        "written": None})
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for r in refresh_all():
        # ASCII only: this prints to a cp1252 console under Task Scheduler, where a
        # middot in the DATA is what breaks the run, not the code around it.
        tail = f" | wrote {r['written']}" if r["written"] else " | dated snapshot already on disk"
        print(f"{r['key']}: {r['status']} | as_of {r['as_of']} | "
              f"{r['count']} holdings{tail}")
