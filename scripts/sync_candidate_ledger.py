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
from universe import auto_add, candidate_ledger as cl  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sync_candidate_ledger")
    ap.add_argument("--date", required=True, help="discovery run date, YYYY-MM-DD")
    ap.add_argument("--thread-ts", default="",
                    help="#ipo-spinoffs-newissues thread ts for this week's post, recorded on new rows")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-auto-add", action="store_true",
                    help="Queue every candidate, including Buckets 2 and 3.")
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
        if not a.no_auto_add:
            # Report the auto-add PLAN without writing. An auto-add is the one
            # thing here that happens without JP being asked, so it must be
            # inspectable before it fires, not only after.
            from config import CSV_PATH
            from ticker_utils import read_universe_csv
            from universe.provenance import removed_tickers

            df = read_universe_csv(CSV_PATH)
            in_universe = {str(t).strip().upper() for t in df["Ticker"] if str(t).strip()}
            auto, queued = auto_add.plan(valid, in_universe=in_universe,
                                         removed=removed_tickers())
            print(f"auto-add plan: {len(auto)} would be added by rule, "
                  f"{len(queued)} queued")
            for d in auto:
                print(f"  WOULD AUTO-ADD {d.ticker}: {d.reason}")
            for d in queued:
                print(f"  queue {d.ticker}: {d.reason}")
        return 0

    path = cl.save(rows)
    print(f"wrote {path.name}: {len(cl.pending(rows))} pending of {len(rows)} total")

    if not a.no_auto_add:
        rc = _auto_add(valid)
        if rc:
            return rc
    return 0


def _auto_add(candidates: list[dict]) -> int:
    """Add the mandatory-by-rule candidates without asking (JP 2026-08-06).

    Buckets 2 (>= $25B IPO, any sector) and 3 (spin-off/carve-out > $10B) are
    mandatory adds under JP's own inclusion rules, so queueing them puts a step
    between the rule and the outcome. Everything else -- including Buckets 1 and
    5, which are undecided -- keeps queueing, because an undecided rule must
    default to the STATUS QUO and the costs are asymmetric: wrongly queueing is
    one Slack reply, wrongly auto-adding is a row in the fleet's
    most-depended-on artifact.
    """
    import importlib.util

    from config import CSV_PATH
    from ticker_utils import read_universe_csv
    from universe.provenance import removed_tickers

    df = read_universe_csv(CSV_PATH)
    in_universe = {str(t).strip().upper() for t in df["Ticker"] if str(t).strip()}
    auto, queued = auto_add.plan(candidates, in_universe=in_universe,
                                 removed=removed_tickers())
    if not auto:
        print(f"auto-add: none qualified ({len(queued)} queued for approval)")
        return 0

    # Route through approve_candidates so the SAME enrichment gate applies --
    # it refuses a half-filled row, so an auto-add that cannot be enriched stays
    # pending and says why. Auto means unasked, not unvalidated.
    ledger = {str(r["ticker"]).strip().upper(): r for r in cl.load()}
    specs = []
    for d in auto:
        row = ledger.get(d.ticker.upper())
        if row is None or not str(row.get("sector", "")).strip():
            print(f"  SKIP {d.ticker}: no ledger row or no sector - stays pending",
                  file=sys.stderr)
            continue
        specs.append(":".join([row["ticker"], row.get("sector", ""),
                               row.get("subsector", ""), row.get("exchange", "")]))
    if not specs:
        return 0

    spec_file = PROJECT_ROOT / "scripts" / "approve_candidates.py"
    mod_spec = importlib.util.spec_from_file_location("approve_candidates", spec_file)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    rc = mod.main(["--source", "auto-add (bucket rule)"]
                  + [arg for s in specs for arg in ("--add", s)])

    still = {str(r["ticker"]).strip().upper() for r in cl.pending(cl.load())}
    added = [d for d in auto if d.ticker.upper() not in still]
    stuck = [d for d in auto if d.ticker.upper() in still]
    print()
    print(f"AUTO-ADDED {len(added)} by rule (no approval needed):")
    for d in added:
        print(f"  {d.ticker}: {d.reason}")
    for d in stuck:
        print(f"  STILL PENDING {d.ticker}: enrichment refused a half-filled row",
              file=sys.stderr)
    print("  -> REPORT THESE IN THE SLACK POST. An add JP was never asked about "
          "must be the most visible line in the report, not the quietest.")
    return rc if stuck else 0


if __name__ == "__main__":
    raise SystemExit(main())
