"""One-off: stage the five Coatue slide-16 'sellers of the shortage' names.

Source: Coatue Public Markets Update (May 2026), slide 16 — the only names on that
slide absent from the coverage universe. Sector/subsector chosen to match existing
labels rather than invent new ones, except `Storage / HDD` (WDC/STX are spinning
disk, not memory — filing them under `Semiconductors / Memory` alongside MU/SNDK
would be wrong).

Run once, then:
    python scripts/approve_candidates.py --add ... --dry-run
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from universe import candidate_ledger as cl  # noqa: E402

CANDIDATES = [
    {
        "ticker": "AVGO", "company": "Broadcom Inc.", "exchange": "NASDAQ",
        "sector": "Tech", "subsector": "Semiconductors / Connectivity",
        "trigger": "Coverage gap",
        "reason": "Coatue May-2026 slide 16 'sellers of products in shortage'. "
                  "Custom AI ASICs + networking silicon; the largest seller-side "
                  "name absent from coverage.",
    },
    {
        "ticker": "GEV", "company": "GE Vernova Inc.", "exchange": "NYSE",
        "sector": "Industrials", "subsector": "Electrical Equipment",
        "trigger": "Coverage gap",
        "reason": "Coatue May-2026 slide 16. Gas turbines + grid electrification - "
                  "the power side of the AI datacenter shortage. 2024 spin from GE.",
    },
    {
        "ticker": "005930.KS", "company": "Samsung Electronics Co., Ltd.",
        "exchange": "KRX",
        "sector": "Tech", "subsector": "Semiconductors / Memory",
        "trigger": "Coverage gap",
        "reason": "Coatue May-2026 slide 16. DRAM/NAND alongside MU and SKHY, "
                  "both already covered. NOT Samsung Biologics (207940), which is "
                  "the existing 'Samsung' row in the universe.",
    },
    {
        "ticker": "WDC", "company": "Western Digital Corporation", "exchange": "NASDAQ",
        "sector": "Tech", "subsector": "Storage / HDD",
        "trigger": "Coverage gap",
        "reason": "Coatue May-2026 slide 16. Post-SanDisk-spin HDD pure-play; "
                  "SNDK (the flash half) is already covered.",
    },
    {
        "ticker": "STX", "company": "Seagate Technology Holdings plc",
        "exchange": "NASDAQ",
        "sector": "Tech", "subsector": "Storage / HDD",
        "trigger": "Coverage gap",
        "reason": "Coatue May-2026 slide 16. HDD duopoly with WDC; nearline "
                  "capacity is a named datacenter shortage.",
    },
]


def main() -> int:
    rows = cl.load()
    before = len(rows)
    result = cl.upsert(rows, CANDIDATES, today=date.today())
    cl.save(rows)
    print("ledger rows {} -> {}".format(before, len(rows)))
    print("upsert:", result)
    for c in CANDIDATES:
        r = cl.by_ticker(rows, c["ticker"])
        print("  {:12} {:34} status={}".format(
            r["ticker"], r["company"][:34], r["status"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
