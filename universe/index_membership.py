"""Index membership snapshots — MSCI EAFE today, board #354's lane.

JP, 2026-09-09: *"I just want the EAFE list and weights tracked."* That is the whole
scope of this module today: collect the constituent list and its weights weekly, and
write a DATED snapshot so history accumulates. Nothing consumes it yet, and nothing here
migrates the S&P 500 or Russell collectors — that is the rest of #354 and it stays there.

## Why dated snapshots are the point

`sector_chart_pack/russell.py` has fetched Russell 1000/2000/3000 weekly for months into
a gitignored cache that is OVERWRITTEN each run. The lists work; the history does not
exist. FTSE sells Russell history and MSCI publishes none free, so membership history is
something you either start accumulating or buy later — and you cannot buy today's. This
module therefore writes `eafe_<as_of>.json` alongside `latest.json`, and the dated file
is never overwritten once written.

⛑ **RECONSTRUCTING PAST MEMBERSHIP FROM TODAY'S LIST IS SURVIVORSHIP BIAS BY
CONSTRUCTION.** A name that left the index is exactly the name a backtest needs and the
one a current list cannot contain. The archive is the only honest source, which is why
starting it is worth more than the code in this file.

## Licensing

Same house rule as `data/crsp/` and `data/estimates_history/`: kept on disk, **never
pushed and never published into `exports/`**. `data/index_membership/` is gitignored.
Fund holdings are the fund's own SEC-mandated disclosure; redistribution is the concern,
and not redistributing solves it.

## Two failure modes this endpoint actually has

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
   `diagnostics/russell_membership_nport_vs_capband_2026-08-18.md`.

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

# key -> (iShares product id, index label, fund label, a floor below which the list is
# not credible). The floor is a REFUSAL, not a warning: a short list means the endpoint
# changed shape, and half an index is worse than none because it looks usable.
FUNDS: dict[str, tuple[str, str, str, int]] = {
    "eafe": ("239623", "MSCI EAFE", "iShares MSCI EAFE ETF (EFA)", 400),
}

# Past this, the cached snapshot is reported as unfit rather than quietly used.
STALE_DAYS = 45


class IndexMembershipError(RuntimeError):
    """Fetch or parse failed in a way that must not resolve to an empty index."""


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

    def _num(v):
        try:
            return float(str(v).replace(",", "").strip() or "nan")
        except ValueError:
            return None

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
    pid, index_label, fund_label, floor = FUNDS[key]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = today or date.today()

    try:
        as_of, rows = parse_holdings(_fetch_csv(pid))
    except IndexMembershipError as e:
        cached = load_latest(key)
        age = snapshot_age_days(cached, today)
        if cached is None:
            raise
        unfit = age is None or age > STALE_DAYS
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

    total_weight = sum(r["weight_pct"] or 0.0 for r in rows)
    doc = {
        "schema_version": 1,
        "key": key,
        "index": index_label,
        "fund": fund_label,
        "as_of": as_of,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": HOLDINGS_URL.format(pid=pid),
        # Stated on every snapshot so a consumer cannot mistake the fund for the index.
        "caveats": [
            "EFA is a sampled fund, not the index — treat as a proxy for MSCI EAFE "
            "membership, never as a membership record.",
            "`ticker` is a LOCAL exchange ticker and there is no ISIN in this file. "
            "Join on (ticker, exchange) or by name; a bare ticker join is wrong.",
            "Licensed for internal use only — never publish into exports/ or push.",
        ],
        "count": len(rows),
        "equity_weight_pct": round(total_weight, 4),
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
    return [refresh(k, today=today) for k in FUNDS]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for r in refresh_all():
        # ASCII only: this prints to a cp1252 console under Task Scheduler, where a
        # middot in the DATA is what breaks the run, not the code around it.
        tail = f" | wrote {r['written']}" if r["written"] else " | dated snapshot already on disk"
        print(f"{r['key']}: {r['status']} | as_of {r['as_of']} | "
              f"{r['count']} holdings{tail}")
