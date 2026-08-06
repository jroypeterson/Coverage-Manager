"""Fold a week's discovery output into the candidate ledger, and expire stale rows.

This is the bridge that was missing. `weekly_universe._step_discovery` has always read
`data/discovery_output_<date>.json`, but the weekly prompt was never told to write one —
so zero such files ever existed, `discovery/candidates.py` sat unused, and the backlog
lived only in prose. This script is what the weekly run calls once it has written that
file.

Run order matters: upsert first, then expire. A candidate re-proposed this week has its
`last_seen` bumped but NOT its expiry clock, so a name that has been listed every Friday
for two months still ages out — which is the point of the clock.

Usage:
    python scripts/sync_candidate_ledger.py --date 2026-08-07
    python scripts/sync_candidate_ledger.py --date 2026-08-07 --dry-run
    python scripts/sync_candidate_ledger.py --date 2026-08-07 --thread-ts 178...
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from discovery.candidates import validate_discovery_output  # noqa: E402
from universe import candidate_ledger as cl  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sync_candidate_ledger")
    ap.add_argument("--date", required=True, help="discovery run date, YYYY-MM-DD")
    ap.add_argument("--thread-ts", default="",
                    help="#ipo-spinoffs-newissues thread ts for this week's post, recorded on new rows")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    today = date.fromisoformat(a.date)
    out = PROJECT_ROOT / "data" / f"discovery_output_{a.date}.json"

    valid, errors = validate_discovery_output(out)
    for e in errors:
        # Not silent: a candidate rejected as a dupe is normal, a malformed one is not.
        print(f"  validation: {e}", file=sys.stderr)
    if not valid:
        print(f"no valid candidates in {out.name} "
              f"({len(errors)} validation message(s))", file=sys.stderr)
        # A quiet week is legitimate; a missing file is not.
        return 0 if out.exists() else 1

    if a.thread_ts:
        for c in valid:
            c.setdefault("slack_thread_ts", a.thread_ts)

    rows = cl.load()
    res = cl.upsert(rows, valid, today=today)
    expired = cl.expire_stale(rows, today=today)

    print(f"ledger: +{res['added']} new, {res['refreshed']} refreshed, "
          f"{res['skipped_decided']} already decided")
    for r in expired:
        # ASCII only in console output: this runs under a cp1252 console in the
        # scheduled task, where a stray em-dash kills the run (see CLAUDE.md).
        print(f"  EXPIRED {r['ticker']} ({r['company'][:40]}) - "
              f"pending since {r['pending_since']}")
    if expired:
        print(f"  -> report these in the #ipo-spinoffs-newissues post: reply `revive TICKER` to restore")

    if a.dry_run:
        print("(dry run - ledger not written)")
        return 0

    path = cl.save(rows)
    print(f"wrote {path.name}: {len(cl.pending(rows))} pending of {len(rows)} total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
