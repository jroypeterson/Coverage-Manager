"""Read the published exports back the way consumers do, and refuse to ship a broken one.

Coverage Manager validated its *source* data and then published artifacts nobody re-read.
On 2026-07-25 a UTF-8 BOM reached the source CSV; the exporter read fieldnames as plain
utf-8, so `DictWriter(extrasaction="ignore")` silently dropped the `Ticker` column it was
writing. The result shipped with `validation_passed: true` and cost, simultaneously:

  exports/positions_and_researching.csv   84 of 84 rows with a blank join key
  exports/watchlist.csv                   66 of 66 blank
  exports/universe.csv                    BOM-prefixed header, so earnings_agent,
                                          post_earnings_movers and analyst-days each
                                          recovered 0 of 1,086 tickers while reporting ok

Validation that never reads the artifact is not validation. This module re-opens each
published CSV **with the encoding consumers actually use** (plain utf-8 — the strict case),
asserts the join key is present and populated, and cross-checks the row count against the
status file that claims to describe it.

It is deliberately dumb and deliberately last: no knowledge of how the exports were built,
so it cannot share a bug with the writer.
"""
from __future__ import annotations

import csv
from pathlib import Path


class ExportAcceptanceError(RuntimeError):
    """A published artifact is unreadable or has lost its join key."""


# (filename, join column, status file, status field holding the expected row count)
CHECKS: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("universe.csv", "Ticker", "universe_status.json", "row_count"),
    ("positions_and_researching.csv", "Ticker", "positions_status.json", "entry_count"),
    ("watchlist.csv", "Ticker", None, None),
)


def check_exports(exports_dir: Path, *, strict: bool = True) -> list[str]:
    """Return a list of problems. Raises when `strict` and anything is wrong."""
    import json

    problems: list[str] = []
    for fname, key, status_file, count_field in CHECKS:
        p = Path(exports_dir) / fname
        if not p.exists():
            continue                      # optional/deprecated artifacts may be absent

        head = p.read_bytes()[:3]
        if head == b"\xef\xbb\xbf":
            problems.append(
                f"{fname}: starts with a UTF-8 BOM. A consumer reading plain utf-8 sees "
                f"'\\ufeff{key}' and silently recovers nothing.")

        # Read it the STRICT way, exactly as the least-tolerant consumer does.
        with p.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        if key not in fields:
            problems.append(f"{fname}: no '{key}' column when read as plain utf-8 "
                            f"(header is {fields[:3]}...)")
            continue
        blank = sum(1 for r in rows if not (r.get(key) or "").strip())
        if blank:
            problems.append(f"{fname}: {blank} of {len(rows)} rows have a blank '{key}' - "
                            f"every consumer joining on it gets nothing")

        if status_file and count_field:
            sp = Path(exports_dir) / status_file
            if sp.exists():
                try:
                    expected = json.loads(sp.read_text(encoding="utf-8")).get(count_field)
                except (OSError, ValueError):
                    expected = None
                if isinstance(expected, int) and expected != len(rows):
                    problems.append(
                        f"{fname}: {len(rows)} rows but {status_file}.{count_field} "
                        f"claims {expected}")

    if problems and strict:
        raise ExportAcceptanceError(
            "published exports failed acceptance:\n  - " + "\n  - ".join(problems))
    return problems
