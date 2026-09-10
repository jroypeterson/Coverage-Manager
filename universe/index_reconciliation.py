"""How much of the coverage universe sits inside each index, and what moved. Board #354.

The last piece of the row: *"a universe-vs-index reconciliation line in the Friday
report."* `index_membership.py` collects the lists; this reads them back against
`data/coverage_universe_tickers.csv` and produces one line per index for the weekly
Slack post:

    S&P 500 121 of 503 (as of 2026-09-08) · no change since 2026-09-01

**It answers a question nothing else in the fleet answers:** how much of what JP
covers is index-visible, and which covered names entered or left an index. A name
leaving the Russell 2000 is a real event for a small-cap holding — it is forced
selling by every fund tracking it — and until now nothing watched for it.

Nothing here writes. It reads two artifacts and returns a dict.

## ⛑ THE JOIN IS RESTRICTED TO US-LISTED ROWS, AND THAT IS NOT TIDINESS

The index files carry a bare ticker. The coverage universe carries 217 non-US
listings. A bare ticker join across both is wrong, and measurably so — measured
2026-09-10 against the live files:

| Ticker | Coverage universe | Index member |
|---|---|---|
| `CSL` | CSL Ltd (ASX, Australia) | Carlisle Companies (R1000, R3000) |
| `UCB` | UCB SA (Euronext Brussels) | United Community Banks (R2000, R3000) |

Two of JP's biopharma names would have been reported as Russell constituents.
Restricting the universe side to `Country (Listing) == United States` removes both
and costs nothing, because every index collected here is a US-listed fund's US
holdings. `eafe` is excluded from this reconciliation entirely for the same reason
in a stronger form: its tickers are LOCAL exchange lines (`ROP` is Roche, not
Roper), so it needs a real `(ticker, exchange)` resolver, not a filter.

## ⛑ "SINCE LAST WEEK" WOULD BE A LIE FOR THE RUSSELL LANE

Vanguard publishes MONTH-END holdings, so `r1000_latest.json` carries the same
`as_of` for four or five consecutive weekly runs. A line reading "0 entered, 0 left
since last week" every week and then jumping would look like a broken diff rather
than a monthly cadence.

So the comparison is against the **previous distinct dated snapshot**, and the line
NAMES the date it compared to. When there is only one snapshot the line says so
rather than reporting a change of zero — ⛑ *an absent baseline is not a finding of
no change*, and the archive is young enough (first snapshots 2026-09-08) that this
is the normal case for now, not an edge case.
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import config

log = logging.getLogger(__name__)

MEMBERSHIP_DIR = config.DATA_DIR / "index_membership"

# Order is the reading order of the line, largest-cap index first.
#
# ⛑ `eafe` IS ABSENT ON PURPOSE. Its `ticker` column is a local exchange line and
# the file carries no ISIN, so it cannot be joined to the universe on a ticker at
# all — see the module docstring. Adding it here would silently marry Roche to
# Roper Technologies. It needs a (ticker, exchange) resolver first.
INDICES = ["sp500", "r1000", "r2000", "r3000"]

US_LISTING = "United States"


def _universe_us_tickers(csv_path: Path | None = None) -> dict[str, str]:
    """`{TICKER: company name}` for US-LISTED coverage rows only.

    See the module docstring on why the filter is load-bearing rather than tidy.
    """
    path = Path(csv_path or config.CSV_PATH)
    out: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("Country (Listing)") or "").strip() != US_LISTING:
                continue
            t = (row.get("Ticker") or "").strip().upper()
            if t:
                out[t] = (row.get("Company Name") or t).strip()
    return out


def _snapshots(key: str) -> list[tuple[str, Path]]:
    """`[(as_of, path)]` for one index's DATED snapshots, newest first.

    `<key>_latest.json` is deliberately skipped: it is a copy of the newest dated
    file, and counting it would make the newest snapshot its own predecessor and
    report every week as "no change".
    """
    out = []
    for p in MEMBERSHIP_DIR.glob(f"{key}_*.json"):
        stamp = p.stem[len(key) + 1:]
        if stamp == "latest" or len(stamp) != 10:
            continue
        out.append((stamp, p))
    return sorted(out, reverse=True)


def _members(path: Path) -> set[str]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("index_reconciliation: unreadable snapshot %s (%s)", path, e)
        return set()
    return {(h.get("ticker") or "").strip().upper()
            for h in doc.get("holdings") or []} - {""}


def reconcile_one(key: str, universe: dict[str, str]) -> dict | None:
    """One index's row, or None when nothing has ever been snapshotted for it."""
    snaps = _snapshots(key)
    if not snaps:
        return None
    as_of, path = snaps[0]
    members = _members(path)
    if not members:
        return None

    covered = sorted(universe.keys() & members)
    row = {
        "key": key,
        "as_of": as_of,
        "index_count": len(members),
        "covered": len(covered),
        "covered_tickers": covered,
        # None, not an empty list: "we have no earlier snapshot" and "nothing
        # changed" are different facts and must not render the same way.
        "prev_as_of": None,
        "entered": None,
        "left": None,
    }

    if len(snaps) > 1:
        prev_as_of, prev_path = snaps[1]
        prev = _members(prev_path)
        if prev:
            prev_covered = universe.keys() & prev
            row["prev_as_of"] = prev_as_of
            row["entered"] = sorted(set(covered) - prev_covered)
            row["left"] = sorted(prev_covered - set(covered))
    return row


def reconcile(csv_path: Path | None = None) -> list[dict]:
    """One row per index that has a snapshot. Never raises."""
    try:
        universe = _universe_us_tickers(csv_path)
    except (OSError, ValueError) as e:
        log.warning("index_reconciliation: universe unreadable (%s)", e)
        return []
    out = []
    for key in INDICES:
        try:
            row = reconcile_one(key, universe)
        except Exception as e:                                  # noqa: BLE001
            log.warning("index_reconciliation[%s]: %s", key, e)
            continue
        if row:
            row["universe_us"] = len(universe)
            out.append(row)
    return out


_LABELS = {"sp500": "S&P 500", "r1000": "Russell 1000",
           "r2000": "Russell 2000", "r3000": "Russell 3000"}


def format_slack(rows: list[dict]) -> str:
    """The block for the weekly #coverage post. Empty string when there is nothing.

    ⛑ NAMES THE COMPARISON DATE RATHER THAN SAYING "SINCE LAST WEEK". Vanguard
    publishes month-end holdings, so the Russell snapshots repeat an `as_of` for
    four or five consecutive runs; "since last week" would be false on most of them.
    """
    if not rows:
        return ""
    lines = [f"*Coverage in the indices* (of {rows[0]['universe_us']} US-listed rows)"]
    for r in rows:
        label = _LABELS.get(r["key"], r["key"])
        bit = (f"• *{label}* {r['covered']} of {r['index_count']} "
               f"(membership as of {r['as_of']})")
        if r["prev_as_of"] is None:
            # ⛑ An absent baseline is not a finding of no change.
            bit += " — first snapshot, no prior to compare"
        elif not r["entered"] and not r["left"]:
            bit += f" — no change since {r['prev_as_of']}"
        else:
            moves = []
            if r["entered"]:
                moves.append("entered " + ", ".join(f"`{t}`" for t in r["entered"]))
            if r["left"]:
                moves.append("left " + ", ".join(f"`{t}`" for t in r["left"]))
            bit += f" — since {r['prev_as_of']}: " + " · ".join(moves)
        lines.append(bit)
    return "\n".join(lines)


def format_email(rows: list[dict]) -> str:
    """Plain-text twin of `format_slack` — the email alert carries no mrkdwn."""
    if not rows:
        return ""
    lines = [f"Coverage in the indices (of {rows[0]['universe_us']} US-listed rows):"]
    for r in rows:
        label = _LABELS.get(r["key"], r["key"])
        bit = f"  - {label}: {r['covered']} of {r['index_count']} (as of {r['as_of']})"
        if r["prev_as_of"] is None:
            bit += "; first snapshot, no prior to compare"
        elif not r["entered"] and not r["left"]:
            bit += f"; no change since {r['prev_as_of']}"
        else:
            moves = []
            if r["entered"]:
                moves.append("entered " + ", ".join(r["entered"]))
            if r["left"]:
                moves.append("left " + ", ".join(r["left"]))
            bit += f"; since {r['prev_as_of']}: " + "; ".join(moves)
        lines.append(bit)
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # ASCII only: this prints to a cp1252 console under Task Scheduler.
    print(format_email(reconcile()))
