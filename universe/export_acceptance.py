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

Three contracts govern every line below, and only the last is about a wrong answer:

  1. **`check_exports(dir, strict=False)` must NEVER raise.** That is the mode the weekly
     pipeline calls, and a diagnostic that crashes is an outage. Every read here is of a
     file some other process wrote, on a Dropbox-synced directory: it can be locked,
     permission-denied, a directory, not UTF-8, not CSV, not JSON, or nested past the
     recursion limit. All of those are FINDINGS.
  2. **A payload of the wrong shape is a recorded problem naming the shape** — never a
     crash, and never a silent skip.
  3. **"I could not check" must never read as "I checked and it is fine."** Inconclusive
     is not clean.

Round 3 (Codex adversarial, 2026-07-29) is the reason those are stated as rules rather
than as three more patches. The five defects fixed in rounds 1-2 were each fixed only on
the artifact class that *exposed* them — the position-state JSONs — while the identical
bug classes stayed live on the CSV and status-file siblings: a status file could crash
the run (`null.get(...)`), disappear, arrive BOM'd, or carry an unusable count, and each
was a crash or a silent pass. Reading one artifact class is now one code path
(`_read_csv_strict` / `_read_json_artifact` / `_read_status` / `_expected_count`), so the
next fix cannot land on one sibling and miss four.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import NamedTuple

_BOM = b"\xef\xbb\xbf"


class ExportAcceptanceError(RuntimeError):
    """A published artifact is unreadable, empty, inconsistent, or has lost its join key."""


class CsvCheck(NamedTuple):
    """A published CSV, its join column, and the status file that describes it.

    `min_rows=1` marks artifacts that can never be legitimately empty: a
    header-only universe.csv has no BOM and no blank keys — it is simply EMPTY,
    and every consumer joining on it gets nothing.

    `required` is a SEPARATE fact from `min_rows`: "may not be empty" and "may
    not be absent" are different contracts, and inferring one from the other is
    how an absent universe.csv slipped through every check below (Codex round 1,
    2026-07-28).

    There is deliberately no `status_required` field. If the artifact is
    published at all, the status file that claims to describe it is part of the
    same contract — every consumer's documented read pattern *starts* by
    asserting on it — so a present artifact with an absent status file is a
    finding, and an absent optional artifact takes its status file with it.
    """
    name: str
    key: str
    status_file: str | None
    count_field: str | None
    min_rows: int
    required: bool


class JsonCountCheck(NamedTuple):
    """A published JSON artifact whose entry count a status file claims to know."""
    name: str
    status_file: str
    count_field: str
    required: bool


# watchlist.csv is a deprecated filtered subset (Portfolio u Researching), so it
# may legitimately be both empty and absent.
CHECKS: tuple[CsvCheck, ...] = (
    CsvCheck("universe.csv", "Ticker", "universe_status.json", "row_count", 1, True),
    CsvCheck("positions_and_researching.csv", "Ticker", "positions_status.json",
             "entry_count", 1, True),
    CsvCheck("watchlist.csv", "Ticker", "watchlist_status.json", "entry_count", 0, False),
)

# JSON artifact -> status file + field that claims its entry count.
#
# reporting_calendar.json is checked but NOT required, and the split is
# deliberate. Every `required=True` artifact here shares one property: its
# absence makes a consumer silently serve WRONG data from a stale copy.
# transcripts' documented contract for the calendar is the opposite —
# zero-false-skip — so an absent calendar degrades to a normal fetch: correct,
# merely expensive. What it must never do is *disagree with its status file*
# while present, which is what the count cross-check catches. Flip `required`
# to True if the owner rules that a publish without a calendar is broken.
JSON_COUNT_CHECKS: tuple[JsonCountCheck, ...] = (
    JsonCountCheck("universe_metadata.json", "universe_status.json", "ticker_count", True),
    JsonCountCheck("reporting_calendar.json", "reporting_calendar_status.json",
                   "ticker_count", False),
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


def _ascii(text: str) -> str:
    """Escape anything a cp1252 console cannot print.

    Sanitize the DATA, not just the format string: this universe is global
    (`R0DE`, `Tickér`, Japanese and Nordic company names all reach these
    messages through fieldnames, tickers and OS error text), and a
    UnicodeEncodeError would kill the run at the exact moment it is trying to
    report why the publish is broken.
    """
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _dedupe(problems: list[str]) -> list[str]:
    """Drop exact repeats, preserving order.

    universe_status.json is the status file for BOTH universe.csv and
    universe_metadata.json, so one corrupt status file is discovered twice.
    Identical strings carry identical information; reporting them twice only
    makes the operator wonder what the difference is.
    """
    seen: set[str] = set()
    out: list[str] = []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _read_csv_strict(p: Path, problems: list[str]) -> tuple[list[str], list[dict]] | None:
    """Open a published CSV exactly as the least-tolerant consumer does.

    Returns (fieldnames, rows), or None after recording the problem. An
    unreadable file is a FINDING, not a crash — the acceptance step must report
    WHICH artifact is broken, not die on the first bad byte.
    """
    # Sniff three bytes, not the whole file: universe.csv is multi-MB and
    # `read_bytes()[:3]` loaded all of it to look at the header.
    try:
        with p.open("rb") as fh:
            head = fh.read(3)
    except OSError as e:
        # `exists()` is true for a locked file, a permission-denied file and a
        # directory alike; only the open tells them apart. exports/ lives in
        # Dropbox, which briefly locks files mid-sync, so this is the likeliest
        # way the weekly pipeline meets an unreadable artifact.
        problems.append(
            f"{p.name}: could not be opened ({type(e).__name__}: {e}) - the "
            f"artifact exists but was NOT checked, so nothing here says whether "
            f"it is publishable")
        return None
    if head == _BOM:
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
    except csv.Error as e:
        # A single cell over csv's 131,072-char field limit (one bloated Notes
        # value) aborts `list(reader)`. Every consumer using stock csv hits the
        # same wall, so this is a publish defect, not a checker limitation -
        # raising our own field limit would only hide it.
        problems.append(
            f"{p.name}: unreadable as CSV ({e}) - a consumer using stock csv "
            f"fails on this file the same way")
        return None
    except OSError as e:
        problems.append(
            f"{p.name}: could not be read ({type(e).__name__}: {e}) - the "
            f"artifact was NOT checked")
        return None
    return fields, rows


def _read_json_artifact(p: Path, problems: list[str]) -> tuple[bool, object]:
    """Parse a published JSON artifact the way a consumer does. -> (ok, payload).

    Consumers do `json.loads(path.read_text())`, and json rejects a BOM
    outright, so a BOM here is a finding in its own right — the founding
    incident was a BOM. It is reported and then decoded with utf-8-sig anyway,
    so one bad byte does not additionally silence every count check this file
    feeds.
    """
    try:
        raw = p.read_bytes()
    except OSError as e:
        problems.append(
            f"{p.name}: could not be read ({type(e).__name__}: {e}) - the "
            f"artifact exists but was NOT checked")
        return False, None

    encoding = "utf-8"
    if raw[:3] == _BOM:
        problems.append(
            f"{p.name}: starts with a UTF-8 BOM. Consumers read this file with "
            f"json.loads(path.read_text()), which rejects the BOM outright - so "
            f"every consumer of it fails, and every check keyed on it was skipped.")
        encoding = "utf-8-sig"

    try:
        return True, json.loads(raw.decode(encoding))
    except (UnicodeDecodeError, ValueError) as e:
        problems.append(f"{p.name}: unreadable as JSON ({e})")
    except RecursionError:
        # Neither OSError nor ValueError, so it escaped every json.loads site.
        problems.append(
            f"{p.name}: nested too deeply for json to parse - a consumer "
            f"calling json.loads on this file dies with RecursionError")
    return False, None


def _read_status(sp: Path, problems: list[str]) -> dict | None:
    """A status payload must be a JSON OBJECT. Anything else is a finding.

    `null` is what a truncated or failed atomic write leaves behind, and
    `.get(count_field)` on it raised AttributeError — the crash the isinstance
    guard on the *state* files exists to prevent, still live on the *status*
    files.
    """
    ok, payload = _read_json_artifact(sp, problems)
    if not ok:
        return None
    if not isinstance(payload, dict):
        problems.append(
            f"{sp.name}: expected a JSON object, got {type(payload).__name__} - "
            f"a truncated or failed atomic write looks exactly like this, and "
            f"every consumer's first line is status['schema_version']")
        return None
    return payload


def _expected_count(sp: Path, count_field: str, problems: list[str]) -> int | None:
    """The row count this status file CLAIMS, or None after recording why not.

    Every non-answer below used to be a silent skip: absent file, BOM, corrupt
    JSON, wrong shape, missing field, non-numeric field. A status file that
    cannot be read is not a status file that agrees with you.
    """
    if not sp.exists():
        problems.append(
            f"{sp.name}: MISSING from the published exports - every consumer's "
            f"read pattern STARTS by asserting on this file, and the row-count "
            f"cross-check it exists for could not be made")
        return None
    status = _read_status(sp, problems)
    if status is None:
        return None
    if count_field not in status:
        problems.append(
            f"{sp.name}: no '{count_field}' field - the row-count cross-check "
            f"silently did not run")
        return None
    value = status[count_field]
    # `True` is an int in Python, so a boolean has to be rejected explicitly or
    # it would compare against a row count as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(
            f"{sp.name}: '{count_field}' is {type(value).__name__} "
            f"({value!r}), not a row count - the cross-check silently did not run")
        return None
    return value


def _check_ticker_aliases(exports_dir: Path) -> list[str]:
    """`ticker_aliases.json` must be internally consistent and honour its schema.

    This artifact needs its own check rather than a `JsonCountCheck` because it
    has no status file and its correct count is legitimately **zero** — a fleet
    with no known symbol splits publishes an empty map. So "is it empty?" is not
    the question; "does it contradict itself?" is. Three ways it can, and each
    would silently drop a join at a consumer:

      * `alias_to_canonical` and `entries` disagree, so a consumer using the fast
        map resolves differently from one reading the evidence;
      * an alias also appears as a canonical, making resolution order-dependent;
      * a `vendor_symbols` value that is neither a canonical nor a declared alias,
        which sends a vendor a string nothing in the file supports.

    Absent is NOT a finding: the file is new, and a consumer's contract is
    `map.get(sym, sym)` — an absent map degrades to today's behaviour rather than
    to wrong data. That is the same reasoning that leaves `reporting_calendar.json`
    unrequired.
    """
    p = exports_dir / "ticker_aliases.json"
    if not p.exists():
        return []

    problems: list[str] = []
    ok, payload = _read_json_artifact(p, problems)
    if not ok:
        return problems
    if not isinstance(payload, dict):
        problems.append(
            f"ticker_aliases.json: expected a JSON object, got "
            f"{type(payload).__name__} - no consumer can resolve a symbol from this")
        return problems

    amap = payload.get("alias_to_canonical")
    entries = payload.get("entries")
    if not isinstance(amap, dict) or not isinstance(entries, list):
        problems.append(
            "ticker_aliases.json: needs an 'alias_to_canonical' object and an "
            "'entries' array - a consumer reading either one alone would silently "
            "resolve nothing")
        return problems

    from_entries: dict[str, str] = {}
    canonicals: set[str] = set()
    for e in entries:
        if not isinstance(e, dict):
            problems.append("ticker_aliases.json: an entry is not an object")
            continue
        canonical = str(e.get("canonical") or "").strip().upper()
        aliases = e.get("aliases")
        if not canonical or not isinstance(aliases, list):
            problems.append(
                f"ticker_aliases.json: entry {canonical or '<blank>'} is missing a "
                f"canonical symbol or an aliases list")
            continue
        canonicals.add(canonical)
        declared = {canonical}
        for a in aliases:
            alias = str(a).strip().upper()
            declared.add(alias)
            from_entries[alias] = canonical
        vendors = e.get("vendor_symbols")
        if isinstance(vendors, dict):
            for vendor, symbol in vendors.items():
                if str(symbol).strip().upper() not in declared:
                    problems.append(
                        f"ticker_aliases.json: {canonical} routes {vendor} to "
                        f"{symbol}, which is neither its canonical symbol nor a "
                        f"declared alias")

    normalized = {str(k).strip().upper(): str(v).strip().upper() for k, v in amap.items()}
    if normalized != from_entries:
        only_map = sorted(set(normalized) - set(from_entries))
        only_entries = sorted(set(from_entries) - set(normalized))
        mismatched = sorted(k for k in set(normalized) & set(from_entries)
                            if normalized[k] != from_entries[k])
        problems.append(
            f"ticker_aliases.json: alias_to_canonical disagrees with entries - "
            f"only in map: {only_map[:5]}; only in entries: {only_entries[:5]}; "
            f"resolving differently: {mismatched[:5]}")

    both = sorted(set(normalized) & canonicals)
    if both:
        problems.append(
            f"ticker_aliases.json: {len(both)} symbol(s) are both a canonical and "
            f"an alias, so resolution is order-dependent: {both[:5]}")

    return problems


def check_exports(exports_dir: Path, *, strict: bool = True) -> list[str]:
    """Return a list of problems. Raises when `strict` and anything is wrong.

    `strict=False` is what the weekly pipeline calls and it MUST NOT raise.
    """
    exports_dir = Path(exports_dir)
    problems: list[str] = []

    # Ticker sets collected for the cross-artifact checks below. `None` means
    # the artifact is absent — a comparison that cannot be made has no result.
    csv_tickers: dict[str, set[str] | None] = {}

    for chk in CHECKS:
        p = exports_dir / chk.name
        if not p.exists():
            csv_tickers[chk.name] = None
            if chk.required:
                # An absent required artifact is the worst state, not a neutral
                # one: there is nothing to misread, so consumers silently keep
                # using whatever stale copy is already on their disk. Skipping
                # quietly here also skipped every downstream check.
                problems.append(
                    f"{chk.name}: MISSING from the published exports - consumers will "
                    f"silently read a stale copy, and every check that depends on "
                    f"this artifact was skipped")
            continue                      # optional/deprecated artifacts may be absent

        parsed = _read_csv_strict(p, problems)
        if parsed is None:
            csv_tickers[chk.name] = None
            continue
        fields, rows = parsed

        if chk.key not in fields:
            problems.append(f"{chk.name}: no '{chk.key}' column when read as plain utf-8 "
                            f"(header is {fields[:3]}...)")
            csv_tickers[chk.name] = None
            continue
        if len(rows) < chk.min_rows:
            problems.append(
                f"{chk.name}: {len(rows)} rows - the artifact is empty, so every "
                f"consumer joining on it gets nothing")
        blank = sum(1 for r in rows if not (r.get(chk.key) or "").strip())
        if blank:
            problems.append(
                f"{chk.name}: {blank} of {len(rows)} rows have a blank '{chk.key}' - "
                f"every consumer joining on it gets nothing")
        csv_tickers[chk.name] = {(r.get(chk.key) or "").strip() for r in rows} - {""}

        if chk.status_file and chk.count_field:
            expected = _expected_count(
                exports_dir / chk.status_file, chk.count_field, problems)
            if expected is not None and expected != len(rows):
                problems.append(
                    f"{chk.name}: {len(rows)} rows but {chk.status_file}.{chk.count_field} "
                    f"claims {expected}")

    # ── JSON entry counts vs their status files ──────────────────────────────
    for chk in JSON_COUNT_CHECKS:
        p = exports_dir / chk.name
        if not p.exists():
            if chk.required:
                problems.append(
                    f"{chk.name}: MISSING from the published exports - three "
                    f"siblings key their whole run on this artifact and will "
                    f"silently read a stale copy")
            continue
        # Parsed on its own, NOT alongside the status file: sharing one `try`
        # meant a corrupt universe_status.json was reported as
        # "universe_metadata.json: unreadable as JSON", sending the operator to
        # the wrong file.
        ok, entries = _read_json_artifact(p, problems)
        if not ok:
            continue
        if not isinstance(entries, (dict, list)):
            # `len()` on a number RAISES; on a bare JSON string it silently
            # measures the CHARACTER count against a ticker count - the same
            # character-iteration cousin the position-state guard exists for.
            problems.append(
                f"{chk.name}: expected a JSON object or array, got "
                f"{type(entries).__name__} - the {chk.count_field} cross-check "
                f"cannot be made from this shape")
            continue
        expected = _expected_count(
            exports_dir / chk.status_file, chk.count_field, problems)
        if expected is not None and expected != len(entries):
            problems.append(
                f"{chk.name}: {len(entries)} entries but {chk.status_file}.{chk.count_field} "
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
            ok, entries = _read_json_artifact(p, problems)
            if not ok:
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

        # Only assert the partition when every state file was actually read: a
        # ticker "missing from every state file" would otherwise be a FALSE
        # POSITIVE, since it may well be sitting in the file we could not parse.
        #
        # But staying silent about that is finding #2 in a new costume, which is
        # exactly what an adversarial re-test found: with one state file corrupt
        # AND a ticker genuinely unaccounted for, the only problem reported was
        # "unreadable as JSON" - the partition simply went unverified and nothing
        # said so. Suppressing a false positive is right; suppressing the fact
        # that the check did not run is not. Inconclusive is not clean.
        unread = [f for f in POSITION_STATE_FILES if f not in state_sets]
        if unread:
            problems.append(
                f"position-state partition NOT VERIFIED - {len(unread)} of "
                f"{len(POSITION_STATE_FILES)} state file(s) could not be read "
                f"({', '.join(unread)}), so a lost or double-assigned position "
                f"would go unseen. Fix those file(s) and re-run acceptance.")

        # The DUPLICATE half needs no such gate, and gating it was wrong: a
        # ticker found in two files that BOTH parsed is double-assigned
        # whatever the unreadable file contains, so there is zero
        # false-positive risk. The module already accepts this reasoning for
        # the per-file extras-vs-CSV check above, which runs per readable file.
        # Report what can be proved; stay silent only about what cannot be known.
        duped = sorted(t for t, files in seen_in.items() if len(files) > 1)
        if duped:
            detail = "; ".join(
                f"{t} in {len(seen_in[t])} position states "
                f"({', '.join(sorted(seen_in[t]))})" for t in duped[:5])
            problems.append(
                f"{len(duped)} ticker(s) appear in more than one position-state "
                f"JSON - the states must be mutually exclusive: {detail}")

        if len(state_sets) == len(POSITION_STATE_FILES):
            union = set().union(*state_sets.values()) if state_sets else set()
            missing = sorted(pos_tickers - union)
            if missing:
                problems.append(
                    f"{len(missing)} ticker(s) in positions_and_researching.csv are "
                    f"missing from every position-state JSON - they exist in the CSV "
                    f"but no state file claims them: {missing[:10]}")

    problems.extend(_check_ticker_aliases(exports_dir))

    problems = [_ascii(p) for p in _dedupe(problems)]
    if problems and strict:
        raise ExportAcceptanceError(
            "published exports failed acceptance:\n  - " + "\n  - ".join(problems))
    return problems
