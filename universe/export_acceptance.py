"""Read the published exports back the way consumers do, and refuse to ship a broken one.

Coverage Manager validated its *source* data and then published artifacts nobody re-read.
On 2026-07-25 a UTF-8 BOM reached the source CSV; the exporter read fieldnames as plain
utf-8, so `DictWriter(extrasaction="ignore")` silently dropped the `Ticker` column it was
writing. The result shipped with `validation_passed: true` and cost, simultaneously:

  exports/positions_and_researching.csv   84 of 84 rows with a blank join key
  exports/watchlist.csv                   66 of 66 blank (undetected until an audit)
  exports/universe.csv                    BOM-prefixed header, so earnings_agent,
                                          post_earnings_movers and analyst-days each
                                          recovered 0 of 1,086 tickers while reporting ok

Validation that never reads the artifact is not validation. This module re-opens each
published CSV **with the encoding consumers actually use** (plain utf-8 — the strict case),
asserts the join key is present and populated, cross-checks the row count against the
status file that claims to describe it, and (since 2026-07-28, Codex R5) asserts the
artifacts are mutually consistent: positions/watchlist rows join back to universe.csv,
the five per-state position JSONs partition the positions CSV, and metadata counts agree
with their status files. An empty or undecodable artifact fails loudly instead of
passing vacuously.

It is deliberately dumb and deliberately last: no knowledge of how the exports were built,
so it cannot share a bug with the writer. Keep it that way — stdlib csv/json only, no
imports from the writer's modules.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


class ExportAcceptanceError(RuntimeError):
    """A published artifact is unreadable, empty, inconsistent, or has lost its join key."""


# (filename, join column, status file, status field holding the expected row
#  count, minimum row count, REQUIRED). `min_rows=1` marks artifacts that can
#  never be legitimately empty: a header-only universe.csv has no BOM and no
#  blank keys — it is simply EMPTY, and every consumer joining on it gets
#  nothing. `required` is a SEPARATE fact from `min_rows`: "may not be empty"
#  and "may not be absent" are different contracts, and inferring one from the
#  other is how an absent universe.csv slipped through every check below
#  (Codex round 1, 2026-07-28).
#
#  watchlist.csv is a deprecated filtered subset (Portfolio ∪ Researching), so
#  it may legitimately be both empty and absent.
CHECKS: tuple[tuple[str, str, str | None, str | None, int, bool], ...] = (
    ("universe.csv", "Ticker", "universe_status.json", "row_count", 1, True),
    ("positions_and_researching.csv", "Ticker", "positions_status.json",
     "entry_count", 1, True),
    ("watchlist.csv", "Ticker", "watchlist_status.json", "entry_count", 0, False),
)

# JSON artifact -> status file + field that claims its entry count.
JSON_COUNT_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("universe_metadata.json", "universe_status.json", "ticker_count"),
)

# The five per-state position files. Together they partition
# positions_and_researching.csv: same tickers, no extras, counts summing to its
# row count.
POSITION_STATE_FILES: tuple[str, ...] = (
    "portfolio.json",
    "researching.json",
    "following_for_interest.json",
    "ready_to_buy.json",
    "ready_to_short.json",
)


def _read_csv_strict(p: Path, problems: list[str]) -> tuple[list[str], list[dict]] | None:
    """Open a published CSV exactly as the least-tolerant consumer does.

    Returns (fieldnames, rows), or None after recording the problem. A decode
    failure is a finding, not a crash — the acceptance step must report WHICH
    artifact is broken, not die on the first bad byte.
    """
    head = p.read_bytes()[:3]
    if head == b"\xef\xbb\xbf":
        problems.append(
            f"{p.name}: starts with a UTF-8 BOM. A consumer reading plain utf-8 sees "
            f"'\\ufeff...' as its first header cell and silently recovers nothing.")

    try:
        with p.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
    except UnicodeDecodeError as e:
        problems.append(
            f"{p.name}: not valid UTF-8 (byte {e.object[e.start:e.start + 1]!r} at "
            f"offset {e.start}) - a plain-utf-8 consumer crashes on this file")
        return None
    return fields, rows


def check_exports(exports_dir: Path, *, strict: bool = True) -> list[str]:
    """Return a list of problems. Raises when `strict` and anything is wrong."""
    exports_dir = Path(exports_dir)
    problems: list[str] = []

    # Ticker sets collected for the cross-artifact checks below. `None` means
    # the artifact is absent — a comparison that cannot be made has no result.
    csv_tickers: dict[str, set[str] | None] = {}

    for fname, key, status_file, count_field, min_rows, required in CHECKS:
        p = exports_dir / fname
        if not p.exists():
            csv_tickers[fname] = None
            if required:
                # An absent required artifact is the worst state, not a neutral
                # one: there is nothing to misread, so consumers silently keep
                # using whatever stale copy is already on their disk. Skipping
                # quietly here also skipped every downstream check.
                problems.append(
                    f"{fname}: MISSING from the published exports - consumers will "
                    f"silently read a stale copy, and every check that depends on "
                    f"this artifact was skipped")
            continue                      # optional/deprecated artifacts may be absent

        parsed = _read_csv_strict(p, problems)
        if parsed is None:
            csv_tickers[fname] = None
            continue
        fields, rows = parsed

        if key not in fields:
            problems.append(f"{fname}: no '{key}' column when read as plain utf-8 "
                            f"(header is {fields[:3]}...)")
            csv_tickers[fname] = None
            continue
        if len(rows) < min_rows:
            problems.append(
                f"{fname}: {len(rows)} rows - the artifact is empty, so every "
                f"consumer joining on it gets nothing")
        blank = sum(1 for r in rows if not (r.get(key) or "").strip())
        if blank:
            problems.append(f"{fname}: {blank} of {len(rows)} rows have a blank '{key}' - "
                            f"every consumer joining on it gets nothing")
        csv_tickers[fname] = {(r.get(key) or "").strip() for r in rows} - {""}

        if status_file and count_field:
            sp = exports_dir / status_file
            if sp.exists():
                try:
                    expected = json.loads(sp.read_text(encoding="utf-8")).get(count_field)
                except (OSError, ValueError):
                    expected = None
                if isinstance(expected, int) and expected != len(rows):
                    problems.append(
                        f"{fname}: {len(rows)} rows but {status_file}.{count_field} "
                        f"claims {expected}")

    # ── JSON entry counts vs their status files ──────────────────────────────
    for fname, status_file, count_field in JSON_COUNT_CHECKS:
        p = exports_dir / fname
        sp = exports_dir / status_file
        if not (p.exists() and sp.exists()):
            continue
        try:
            entries = json.loads(p.read_text(encoding="utf-8"))
            expected = json.loads(sp.read_text(encoding="utf-8")).get(count_field)
        except (OSError, ValueError) as e:
            problems.append(f"{fname}: unreadable as JSON ({e})")
            continue
        if isinstance(expected, int) and expected != len(entries):
            problems.append(
                f"{fname}: {len(entries)} entries but {status_file}.{count_field} "
                f"claims {expected}")

    # ── cross-artifact consistency (Codex R5) ────────────────────────────────
    # Every positions/watchlist row must join back to universe.csv — a ticker
    # that does not is a hollow row carrying blank universe columns.
    universe_tickers = csv_tickers.get("universe.csv")
    if universe_tickers is not None:
        for fname in ("positions_and_researching.csv", "watchlist.csv"):
            tickers = csv_tickers.get(fname)
            if tickers is None:
                continue
            orphans = sorted(tickers - universe_tickers)
            if orphans:
                problems.append(
                    f"{fname}: {len(orphans)} ticker(s) not in universe.csv - the "
                    f"joined universe columns for them are blank: {orphans[:10]}")

    # The five per-state JSONs partition positions_and_researching.csv.
    pos_tickers = csv_tickers.get("positions_and_researching.csv")
    if pos_tickers is not None:
        # A partition must be checked AS a partition. The previous version
        # compared a SUM of counts against the row count, which two different
        # breakages can satisfy by coincidence: put one ticker in two states and
        # drop another, and the total still matches while a name has silently
        # vanished (Codex round 1, 2026-07-28). Set equality plus pairwise
        # disjointness cannot be fooled that way, and it can name the offenders.
        state_sets: dict[str, set[str]] = {}
        seen_in: dict[str, list[str]] = {}
        for fname in POSITION_STATE_FILES:
            p = exports_dir / fname
            if not p.exists():
                # Never let "I could not check" read as "I checked and it is
                # fine" - inconclusive is not clean.
                problems.append(
                    f"{fname}: MISSING - the position-state partition cannot be "
                    f"verified, so a lost or duplicated position would go unseen")
                continue
            try:
                entries = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                problems.append(f"{fname}: unreadable as JSON ({e})")
                continue
            # Only a dict (the production shape, {TICKER: {...}}) or a list of
            # tickers is meaningful. Iterating anything else either RAISES -
            # breaking the non-gating contract, turning a diagnostic into an
            # outage - or silently succeeds on nonsense: a bare JSON string
            # iterates into individual CHARACTERS and yields a plausible-looking
            # ticker set. Both were live before this guard.
            if not isinstance(entries, (dict, list)):
                problems.append(
                    f"{fname}: expected a JSON object or array of tickers, got "
                    f"{type(entries).__name__} - the position-state partition "
                    f"cannot be verified from this shape")
                continue
            tickers = {str(t).strip() for t in entries if isinstance(t, str)} - {""}
            non_str = [t for t in entries if not isinstance(t, str)]
            if non_str:
                problems.append(
                    f"{fname}: {len(non_str)} entry key(s) are not strings "
                    f"(first is {type(non_str[0]).__name__}) - these cannot be "
                    f"tickers, so the partition check would silently under-count")
            state_sets[fname] = tickers
            for t in tickers:
                seen_in.setdefault(t, []).append(fname)
            extras = sorted(tickers - pos_tickers)
            if extras:
                problems.append(
                    f"{fname}: {len(extras)} ticker(s) not in "
                    f"positions_and_researching.csv: {extras[:10]}")

        # Only assert the partition when every state file was actually read;
        # otherwise the gaps above are the finding.
        if len(state_sets) == len(POSITION_STATE_FILES):
            union = set().union(*state_sets.values()) if state_sets else set()
            missing = sorted(pos_tickers - union)
            if missing:
                problems.append(
                    f"{len(missing)} ticker(s) in positions_and_researching.csv are "
                    f"missing from every position-state JSON - they exist in the CSV "
                    f"but no state file claims them: {missing[:10]}")
            duped = sorted(t for t, files in seen_in.items() if len(files) > 1)
            if duped:
                detail = "; ".join(
                    f"{t} in {len(seen_in[t])} position states "
                    f"({', '.join(sorted(seen_in[t]))})" for t in duped[:5])
                problems.append(
                    f"{len(duped)} ticker(s) appear in more than one position-state "
                    f"JSON - the states must be mutually exclusive: {detail}")

    if problems and strict:
        raise ExportAcceptanceError(
            "published exports failed acceptance:\n  - " + "\n  - ".join(problems))
    return problems
