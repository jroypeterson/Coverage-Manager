"""Identity provenance: which cells a human verified, when, and on what evidence.

## Why this exists

Two days of identity work produced nine hand-repaired rows and ~25 test
assertions pinning literal cell values. An architectural review named the real
problem: **the system could not tell a verified cell from an unverified one**, so
every vendor-vs-row disagreement escalated to a human because there was no prior
on which side was stale. And both directions genuinely occur:

  - `FGEN` — the stored ISIN was RIGHT; the register had moved on (Kyntra rename).
  - `CBIO` — the stored ISIN was RIGHT FOR ITS TIME; a 1-for-100 reverse split
    minted a new CUSIP and the row was one corporate action behind.
  - `MED`  — the stored NAME was right and every other cell was another company's.

With no provenance, those three are indistinguishable at the moment an audit
fires. With it, they are three different verdicts.

The adjudications were previously stored as commit prose plus literal pin tests —
"a provenance database implemented in pytest". This is that database, made real.

## What it is NOT

Not a second source of truth for the universe. `data/coverage_universe_tickers.csv`
remains authoritative. This records *what was checked and how*, so:

  1. one generic test can assert the universe still agrees with every verified
     cell — replacing per-incident literal pins that break on a legitimate
     corporate action and train the habit of editing tests to match data;
  2. an audit can auto-triage its own conflicts (`triage`), so only genuine
     judgment calls reach a human.

A stale ledger entry is corrected by editing a DATA file with new evidence and a
new date — an auditable act — rather than by editing an assertion.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import config
from logging_utils import get_logger

logger = get_logger(__name__)

LEDGER_PATH = Path(config.DATA_DIR) / "identity_provenance.json"
SCHEMA_VERSION = 1

#: Fields whose provenance is worth recording. Deliberately the IDENTITY surface
#: — the cells that say *which company and which listing this row is* — not
#: market data, which changes constantly and is not verified by hand.
TRACKED_FIELDS = frozenset({
    "Company Name", "ISIN", "LEI", "CIK",
    "FIGI", "Composite FIGI", "Share Class FIGI",
    "Exchange", "Exchange Code", "Exchange Full Name",
    "Country (HQ)", "Country (Listing)", "Country (ISO)",
    "Currency", "Website", "Listing Type",
})

# Triage verdicts
ROW_VERIFIED = "row-verified"        # cell was human-verified; suspect the vendor
ROW_UNVERIFIED = "row-unverified"    # cell is unverified import stock; suspect the row
ROW_SUPERSEDED = "row-superseded"    # vendor is offering a value we already rejected
ROW_HELD = "row-held"                # already investigated and could not be settled


class LedgerError(ValueError):
    """The ledger is malformed. Raised loudly — a silently-skipped entry is a
    silently-unpinned fact."""


def _key(ticker: str, field: str) -> tuple[str, str]:
    return (str(ticker).strip().upper(), str(field).strip())


def _cell(value) -> str:
    """Normalize a cell to the string the ledger stores, tolerating the loader.

    `run_all_validations` is called with whatever DataFrame the caller loaded,
    and `cli validate` uses a bare `pd.read_csv` (permitted for read-only
    readers per CLAUDE.md). That read infers integer ID columns containing
    blanks as float64, so a correct CSV presents as `921299.0` and a blank as
    `nan`. Both are artifacts of the READER, not differences in the data — the
    same file read via `read_universe_csv` compares clean.

    So exactly two pandas artifacts are normalized, and nothing else: `nan` to
    empty, and a trailing `.0` on an otherwise all-digit value. Anything broader
    would risk coercing a genuine difference into agreement, which is the whole
    failure this check exists to catch.
    """
    s = str(value).strip()
    if s.lower() in ("nan", "none", "<na>"):
        return ""
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def load_ledger(path=None) -> dict:
    """Parse and VALIDATE. Every rule here makes a degraded ledger impossible
    rather than merely unlikely."""
    p = Path(path) if path else LEDGER_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise LedgerError(f"no provenance ledger at {p}") from e
    except ValueError as e:
        raise LedgerError(f"{p} is not valid JSON: {e}") from e

    if raw.get("schema_version") != SCHEMA_VERSION:
        raise LedgerError(
            f"{p}: schema_version {raw.get('schema_version')!r}, expected "
            f"{SCHEMA_VERSION} — refusing to read a ledger written by another shape")

    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise LedgerError(f"{p}: 'entries' must be a list")

    seen: set[tuple[str, str]] = set()
    for i, e in enumerate(entries):
        where = f"{p} entries[{i}]"
        if not isinstance(e, dict):
            raise LedgerError(f"{where}: not an object")
        for req in ("ticker", "field", "value", "verified", "sources"):
            if req not in e:
                raise LedgerError(f"{where}: missing {req!r}")
        field = str(e["field"]).strip()
        if field not in TRACKED_FIELDS:
            raise LedgerError(
                f"{where}: field {field!r} is not in TRACKED_FIELDS — add it there "
                f"deliberately, or fix the typo")
        k = _key(e["ticker"], field)
        if k in seen:
            # Two entries for one cell would make "the verified value" ambiguous
            # and let one silently shadow the other.
            raise LedgerError(f"{where}: duplicate entry for {k[0]} / {k[1]}")
        seen.add(k)
        srcs = e["sources"]
        if not isinstance(srcs, list) or not srcs or not all(
                isinstance(s, str) and s.strip() for s in srcs):
            raise LedgerError(f"{where}: 'sources' must be a non-empty list of strings")
        if len(srcs) < 2 and not e.get("single_source_reason"):
            # The repo's own bar for overwriting an identifier is TWO independent
            # sources. One source is allowed, but it must be argued in writing.
            raise LedgerError(
                f"{where} ({k[0]}/{k[1]}): one source and no 'single_source_reason'. "
                f"Overwriting an identifier on one source needs a stated reason.")
        try:
            date.fromisoformat(str(e["verified"]))
        except ValueError as exc:
            raise LedgerError(f"{where}: 'verified' must be ISO YYYY-MM-DD") from exc

    removals = raw.get("removals", [])
    if not isinstance(removals, list):
        raise LedgerError(f"{p}: 'removals' must be a list")
    for i, r in enumerate(removals):
        for req in ("ticker", "verified", "reason", "delisted"):
            if req not in r:
                raise LedgerError(f"{p} removals[{i}]: missing {req!r}")
        if not isinstance(r["delisted"], bool):
            # The ZEN/ICAD/ALBT distinction: removed-for-scope vs actually gone.
            # A string here would let "false" read as truthy.
            raise LedgerError(
                f"{p} removals[{i}] ({r['ticker']}): 'delisted' must be a real "
                f"boolean — scope removal and delisting are different facts")

    renames = raw.get("renames", [])
    for i, r in enumerate(renames):
        for req in ("from", "to", "verified", "evidence"):
            if req not in r:
                raise LedgerError(f"{p} renames[{i}]: missing {req!r}")

    # `held` is provenance too, and the kind most easily lost: "we looked and
    # could not settle it" is a real, expensive finding. Without it, the next
    # audit re-derives the same dead end, and a later pass can mistake the
    # silence for cleanliness.
    held = raw.get("held", [])
    for i, h in enumerate(held):
        for req in ("ticker", "field", "value", "reviewed", "why_unresolved"):
            if req not in h:
                raise LedgerError(f"{p} held[{i}]: missing {req!r}")
        k = _key(h["ticker"], h["field"])
        if k in seen:
            raise LedgerError(
                f"{p} held[{i}]: {k[0]}/{k[1]} is also a verified entry — a cell "
                f"cannot be both settled and unresolved")

    return {"entries": entries, "removals": removals, "renames": renames,
            "held": held, "generated_note": raw.get("generated_note", "")}


def index_entries(ledger: dict) -> dict[tuple[str, str], dict]:
    return {_key(e["ticker"], e["field"]): e for e in ledger["entries"]}


def verified_value(ledger: dict, ticker: str, field: str):
    """The human-verified value for a cell, or None if it was never verified."""
    e = index_entries(ledger).get(_key(ticker, field))
    return e["value"] if e else None


def check_universe(df, ledger=None) -> list[str]:
    """Every verified cell must still be what the universe carries.

    This is the ONE check that replaces per-incident literal pin tests. Drift is
    reported, never auto-corrected: if a corporate action genuinely moved a
    value, the ledger entry is updated with new evidence and a new date — an
    auditable act — rather than an assertion being edited to match.
    """
    ledger = ledger or load_ledger()
    rows = {str(r["Ticker"]).strip().upper(): r for _, r in df.iterrows()}
    problems = []

    for e in ledger["entries"]:
        tkr, field = _key(e["ticker"], e["field"])
        row = rows.get(tkr)
        if row is None:
            problems.append(
                f"{tkr}: verified {field} on a row that is no longer in the "
                f"universe (was it removed without updating the ledger?)")
            continue
        if field not in row:
            problems.append(f"{tkr}: column {field!r} no longer exists")
            continue
        actual = _cell(row[field])
        if actual != _cell(e["value"]):
            problems.append(
                f"{tkr} {field}: universe has {actual!r}, ledger verified "
                f"{str(e['value'])!r} on {e['verified']}")

    for r in ledger["removals"]:
        if str(r["ticker"]).strip().upper() in rows:
            problems.append(
                f"{r['ticker']}: removed on {r['verified']} but is back in the "
                f"universe")

    for r in ledger.get("renames", []):
        old, new = str(r["from"]).strip().upper(), str(r["to"]).strip().upper()
        if old in rows:
            problems.append(
                f"{old}: renamed to {new} on {r['verified']} but the OLD ticker is "
                f"still in the universe")
        if new not in rows:
            problems.append(
                f"{new}: renamed from {old} on {r['verified']} but the new ticker "
                f"is not in the universe")

    for h in ledger.get("held", []):
        tkr, field = _key(h["ticker"], h["field"])
        row = rows.get(tkr)
        if row is None or field not in row:
            continue
        actual = _cell(row[field])
        if actual != _cell(h["value"]):
            # Not necessarily wrong — someone may have settled it. But it must be
            # a deliberate act: move the entry to `entries` with evidence.
            problems.append(
                f"{tkr} {field}: held-unresolved value was {h['value']!r} but the "
                f"universe now has {actual!r} — if this was settled, move it to "
                f"'entries' with its sources")

    # A superseded value must not reappear ANYWHERE — the generalised ZEN rule,
    # now derived from the ledger instead of hand-maintained in a test.
    superseded = {str(e["supersedes"]).strip(): (e["ticker"], e["field"])
                  for e in ledger["entries"] if e.get("supersedes")}
    if superseded:
        id_cols = [c for c in ("ISIN", "LEI", "FIGI", "Composite FIGI",
                               "Share Class FIGI", "CIK") if c in df.columns]
        for tkr, row in rows.items():
            for c in id_cols:
                v = _cell(row[c])
                if v and v in superseded:
                    orig = superseded[v]
                    problems.append(
                        f"{tkr} {c}: carries {v!r}, which was superseded on "
                        f"{orig[0]}/{orig[1]} — a rejected identifier must not "
                        f"reappear on any row")
    return problems


def triage(ledger, ticker: str, field: str, vendor_value: str = "") -> tuple[str, str]:
    """Given an audit disagreement, say which side to suspect.

    Returns `(verdict, explanation)`. This is what turns an audit from "here are
    N conflicts, go look at each" into "here are the N that need you".
    """
    e = index_entries(ledger).get(_key(ticker, field))
    v = str(vendor_value or "").strip()
    if e is None:
        for h in ledger.get("held", []):
            if _key(h["ticker"], h["field"]) == _key(ticker, field):
                return (ROW_HELD,
                        f"{ticker} {field} was investigated on {h['reviewed']} and "
                        f"could NOT be settled: {h['why_unresolved']} Re-raising it "
                        f"adds nothing until new evidence exists.")
    if e and e.get("supersedes") and v and v == str(e["supersedes"]).strip():
        return (ROW_SUPERSEDED,
                f"the vendor is offering {v!r}, which was explicitly rejected for "
                f"{ticker} on {e['verified']}. Nothing to do — this is the audit "
                f"re-finding a decision already made.")
    if e:
        return (ROW_VERIFIED,
                f"{ticker} {field} was verified on {e['verified']} against "
                f"{len(e['sources'])} source(s): {'; '.join(e['sources'])}. Suspect "
                f"the VENDOR first — a rename or corporate action it has and we "
                f"do not (the FGEN and CBIO shape).")
    return (ROW_UNVERIFIED,
            f"{ticker} {field} has never been human-verified — it is most likely "
            f"unchecked bulk-import stock. Suspect the ROW first.")
