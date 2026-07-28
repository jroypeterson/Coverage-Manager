"""Approve ledger candidates: enrich, append to the universe CSV, mark decided.

This is the write half of the approval loop (COVERAGE_APPROVAL_PLAN.md S3). The
Slack reply-poller will call exactly this; today it is driven by hand.

**Approval means a fully populated row, not a stub.** JP 2026-07-28: "approval just
means add to coverage manager, which means I want you to get all the appropriate
metadata to track the name." So each add runs the real enrichment chain and
**refuses to write a half-filled row** — a blank CIK makes the name invisible to
insider_ownership and earnings_agent, and a wrong one is worse. A failed enrichment
leaves the candidate `pending` and reports why.

Not done here, deliberately — run after this, once, for the whole batch:
    python cli.py ipo-backfill --min-year 2026      # verified offer date + lockups
    python cli.py weekly-universe --skip-discovery  # republish exports + sigma_export

That second command is what makes the names visible to the seven downstream
consumers and to sigma-alert. Adding to the CSV without it changes nothing outside
this repo until Friday.

Usage:
    python scripts/approve_candidates.py --add SKHY:Tech:"Semiconductors / Memory"
    python scripts/approve_candidates.py --add PBLS:Biopharma:Biotech --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import CSV_PATH  # noqa: E402
from ticker_utils import read_universe_csv, write_universe_csv  # noqa: E402
from universe import candidate_ledger as cl  # noqa: E402
from universe.enrich import EnrichError, enrich_single_ticker  # noqa: E402


def parse_add(spec: str) -> tuple[str, str, str, str]:
    """'TICKER:Sector[:Subsector[:Exchange]]' -> (ticker, sector, subsector, exchange).

    The per-name exchange matters for foreign lines: `normalize_ticker` only appends
    a Yahoo suffix when `Exchange` is non-US, so a wrong or missing exchange yields a
    bare symbol that resolves to whoever owns it in the US — the CSL/UCB/Ipsen class
    of bug (see CLAUDE.md). A batch-wide flag cannot express `2475.HK -> HKEX` and
    `MWH -> NASDAQ` in the same run.
    """
    parts = spec.split(":")
    if len(parts) < 2:
        raise SystemExit(f"--add needs TICKER:Sector[:Subsector[:Exchange]], got {spec!r}")
    ticker, sector = parts[0].strip(), parts[1].strip()
    subsector = parts[2].strip() if len(parts) > 2 else ""
    exchange = parts[3].strip() if len(parts) > 3 else ""
    return ticker, sector, subsector, exchange


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="approve_candidates")
    ap.add_argument("--add", action="append", required=True,
                    metavar="TICKER:Sector[:Subsector]")
    ap.add_argument("--source", default="manual",
                    help="recorded on the ledger row, e.g. 'slack-thread'")
    ap.add_argument("--exchange-hint", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    today = date.today()
    rows = cl.load()
    df = read_universe_csv(CSV_PATH)
    existing = {str(t).strip().upper() for t in df["Ticker"] if str(t).strip()}

    enriched, failed = [], []
    for spec in a.add:
        ticker, sector, subsector, exchange = parse_add(spec)

        if ticker.upper() in existing:
            print(f"  SKIP {ticker}: already in the universe CSV")
            failed.append((ticker, "already present"))
            continue
        if cl.by_ticker(rows, ticker) is None:
            print(f"  SKIP {ticker}: not in the candidate ledger - add it there first")
            failed.append((ticker, "not in ledger"))
            continue

        try:
            row = enrich_single_ticker(ticker, sector,
                                       exchange_hint=exchange or a.exchange_hint)
        except EnrichError as exc:
            # Loud, and the candidate stays pending. Never append a stub.
            print(f"  FAIL {ticker}: enrichment incomplete - {exc}", file=sys.stderr)
            failed.append((ticker, str(exc)))
            continue

        if subsector:
            row["Subsector (JP)"] = subsector
        row["Core"] = ""          # JP sets Core himself (decision 2, 2026-07-28)
        enriched.append((ticker, row))
        print(f"  OK   {ticker}: {row.get('Company Name')} | {row.get('Exchange')} | "
              f"{row.get('Currency')} | CIK={row.get('CIK') or '-'} | "
              f"ISIN={row.get('ISIN') or '-'} | listed {row.get('Year Listed') or '-'}")

    if not enriched:
        print("nothing to write")
        return 1 if failed else 0

    if a.dry_run:
        print(f"\n(dry run - {len(enriched)} row(s) NOT written)")
        return 0

    import pandas as pd
    add_df = pd.DataFrame([r for _, r in enriched])
    # Reindex to the canonical column order; a column the enricher didn't set
    # becomes "" rather than NaN, which would round-trip as the string "nan".
    add_df = add_df.reindex(columns=list(df.columns), fill_value="").fillna("")
    combined = pd.concat([df, add_df], ignore_index=True)
    write_universe_csv(combined, CSV_PATH)     # float-safe writer, never bare to_csv
    print(f"\nwrote {len(enriched)} row(s) to {Path(CSV_PATH).name} "
          f"({len(df)} -> {len(combined)})")

    for ticker, _ in enriched:
        cl.decide(rows, ticker, "approved", today=today, source=a.source)
    cl.save(rows)
    print(f"ledger: {len(enriched)} approved, {len(cl.pending(rows))} still pending")

    print("\nNEXT (required, or nothing outside this repo changes):")
    print("  python cli.py ipo-backfill --min-year <year>   # no --tickers flag exists")
    print("  python cli.py weekly-universe --skip-discovery")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
