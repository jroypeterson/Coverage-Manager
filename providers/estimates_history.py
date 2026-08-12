"""Point-in-time archive of analyst EPS estimates.

**A snapshot is not a record.** `cache/analyst_estimates/` holds one overwritten
blob per ticker with a `_cached_at` stamp, so it always answers "what does the
street forecast *now*" and can never answer "what did the street forecast *then*".
That second question is the one a valuation band needs, and it is unanswerable
retroactively -- FMP Starter sells no point-in-time consensus, and nothing else in
the fleet stores one. Every week that passes without recording the estimate is a
week of forward-P/E history that cannot be reconstructed at any price.

So this module appends, never overwrites: one JSON line per (ticker, observation
date) under `data/estimates_history/<TICKER>.jsonl`. It cannot backfill -- the
series starts the day it is switched on -- which is precisely why it should not
wait for a consumer to exist.

**Cadence is the cache TTL, not the run schedule.** `fetch_estimates` returns
early on a cache hit, so an observation is recorded only when a real fetch
happens -- roughly every 30 days per ticker (`ESTIMATES_CACHE_TTL_HOURS`), not
weekly. That is deliberate and it is enough: annual EPS estimates move slowly,
and JP's own `SPY vs DGX PE.xlsx` samples forward P/E **monthly**, so a ~monthly
series matches the granularity of the artifact this is meant to reproduce. If a
denser series is ever wanted, shorten the TTL -- do not add a second fetch path.

**Deliberately NOT gitignore-exempt.** `data/estimates_history/` is ignored for
the same reason `data/crsp/` is: this repo is PUBLIC and the rows are licensed
vendor data. The archive is the point of the lane, so back it up outside git --
the directory lives in Dropbox, which is the current backup.

Non-gating by construction: `record_observation` swallows its own errors. An
archive is a side effect of fetching estimates, and it must never be able to
break the fetch that feeds the live report.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from config import SCRIPT_DIR
from logging_utils import get_logger, log_exception

logger = get_logger("providers.estimates_history")

HISTORY_DIR = Path(SCRIPT_DIR) / "data" / "estimates_history"

#: Bumped when the line shape changes, so a reader can tell eras apart rather
#: than guessing from which keys happen to be present.
SCHEMA_VERSION = 1


def _path(ticker: str) -> Path:
    safe = "".join(c for c in ticker if c.isalnum() or c in "._-").upper()
    return HISTORY_DIR / f"{safe}.jsonl"


def observation_dates(ticker: str) -> set[str]:
    """Every observation date already recorded for `ticker`.

    A malformed line is skipped rather than raising: one bad append (a kill
    mid-write) must not make the whole series unreadable, and the cost of
    misreading it is one duplicate observation, not a lost one.
    """
    p = _path(ticker)
    if not p.exists():
        return set()
    out: set[str] = set()
    try:
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("observed"):
                    out.add(str(row["observed"]))
    except OSError as e:
        log_exception(logger, f"estimates history unreadable for {ticker}", e)
    return out


def record_observation(ticker, rows, observed=None) -> bool:
    """Append today's estimate curve for `ticker`. Returns True if a line was written.

    Idempotent per calendar day: a second call on the same date is a no-op, so
    re-running the weekly build (or a manual `--refresh`) cannot stack duplicate
    observations onto one date and skew any series built from the file later.

    `rows` is the same `[{"date", "epsAvg"}]` list the cache stores. An empty or
    all-null curve is NOT recorded -- "we asked and the vendor had nothing" is a
    fact about the vendor, and writing it as an observation would put a hole in a
    series whose whole value is that its points are real.
    """
    try:
        if not ticker or not rows:
            return False
        usable = [r for r in rows
                  if isinstance(r, dict) and r.get("epsAvg") is not None]
        if not usable:
            return False

        observed = observed or date.today().isoformat()
        if observed in observation_dates(ticker):
            return False

        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        line = {
            "schema_version": SCHEMA_VERSION,
            "observed": observed,
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ticker": str(ticker).upper(),
            "rows": [{"date": r.get("date"), "epsAvg": r.get("epsAvg")} for r in rows],
        }
        with _path(ticker).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
        return True
    except Exception as e:  # noqa: BLE001 - an archive must never break the fetch
        log_exception(logger, f"failed to archive estimates for {ticker}", e)
        return False


def load_observations(ticker: str) -> list[dict]:
    """Every recorded observation for `ticker`, oldest-first. Malformed lines skipped."""
    p = _path(ticker)
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("observed"):
                    out.append(row)
    except OSError as e:
        log_exception(logger, f"estimates history unreadable for {ticker}", e)
        return []
    out.sort(key=lambda r: str(r.get("observed", "")))
    return out
