"""Append-only ledger of every coverage candidate ever proposed.

**Why this exists.** Until now "pending" was prose: each Friday's report re-derived the
backlog by having an LLM read the previous week's report. That drifted exactly the way
you would expect — by 2026-07-28, 15 names had accumulated across five reports with no
record of when each was first proposed, and no way to distinguish a name JP passed on
from one that was never actually put to him. Nothing had been added to the universe CSV
since at least 6/19.

This is the same lesson as the fleet triage board (`PROJECT_IDEAS.md` #183): a queue
whose state lives in prose an LLM re-reads is not a queue. Candidates are data.

**Two dates, deliberately.** `first_proposed` is history — when the name was first
recommended, never rewritten. `pending_since` is the expiry clock. They differ when a
backlog is imported (the 15 names above were proposed weeks ago but were never actually
asked, so expiring them on their original date would decline them by accident) and when
a candidate is revived. Expiry always reads `pending_since`.

**Decided rows are immutable.** `upsert` only ever touches `pending` rows. A re-proposal
of a name JP already declined does not silently reopen it — that is what `revive` is for.
"""
from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_ledger.csv"
EXPIRY_DAYS = 60          # JP 2026-07-28: pending 60 days -> declined, revivable

STATUSES = ("pending", "approved", "declined", "expired")

FIELDS = [
    "ticker", "company", "exchange", "market_cap", "sector", "subsector",
    "trigger", "first_proposed", "pending_since", "last_seen", "status",
    "decision_date", "decision_source", "slack_thread_ts", "reason", "notes",
]


class LedgerError(RuntimeError):
    pass


# ------------------------------------------------------------------------ I/O


def load(path: Path | str | None = None) -> list[dict]:
    """Read the ledger. Missing file is an empty ledger, not an error."""
    p = Path(path) if path else LEDGER_PATH
    if not p.exists():
        return []
    # Tolerant in, strict out: accept a BOM if some editor added one, never write one.
    with open(p, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r.get("status") not in STATUSES:
            raise LedgerError(
                f"{p.name}: {r.get('ticker')!r} has unknown status "
                f"{r.get('status')!r} (expected one of {STATUSES})")
    return rows


def save(rows: list[dict], path: Path | str | None = None) -> Path:
    """Write the ledger as plain UTF-8, no BOM.

    A BOM here would be read as part of the first field name by any plain-utf-8
    consumer and silently blank the join key — the failure that cost the universe
    exports on 2026-07-25 (see CLAUDE.md, "Exports are BOM-free").
    """
    p = Path(path) if path else LEDGER_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    # Write-then-replace: a failed encode must not leave a truncated ledger behind.
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r.get("first_proposed", ""),
                                             r.get("ticker", ""))):
            w.writerow({k: r.get(k, "") for k in FIELDS})
    tmp.replace(p)
    return p


# -------------------------------------------------------------------- queries


def by_ticker(rows: list[dict], ticker: str) -> dict | None:
    t = (ticker or "").strip().upper()
    for r in rows:
        if (r.get("ticker") or "").strip().upper() == t:
            return r
    return None


def pending(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("status") == "pending"]


# ------------------------------------------------------------------ mutations


def upsert(rows: list[dict], candidates: list[dict], *, today: date,
           pending_since: date | None = None) -> dict:
    """Merge this week's candidates in. Returns {added, refreshed, skipped_decided}.

    New name  -> appended as `pending`.
    Still pending -> `last_seen` bumped; the expiry clock is NOT reset (a name
        re-listed every week for 60 days is exactly what expiry is for).
    Already decided -> left completely alone.

    `pending_since` overrides the expiry-clock start for this batch — used when
    importing a historical backlog that was never actually put to JP.
    """
    iso = today.isoformat()
    clock = (pending_since or today).isoformat()
    added = refreshed = skipped = 0

    for c in candidates:
        ticker = (c.get("ticker") or "").strip().upper()
        if not ticker:
            raise LedgerError(f"candidate with no ticker: {c!r}")
        existing = by_ticker(rows, ticker)

        if existing is None:
            rows.append({
                "ticker": ticker,
                "company": c.get("company", ""),
                "exchange": c.get("exchange", ""),
                "market_cap": c.get("market_cap", ""),
                "sector": c.get("sector", ""),
                "subsector": c.get("subsector", ""),
                "trigger": c.get("trigger", ""),
                "first_proposed": c.get("first_proposed") or iso,
                "pending_since": clock,
                "last_seen": iso,
                "status": "pending",
                "decision_date": "", "decision_source": "",
                "slack_thread_ts": c.get("slack_thread_ts", ""),
                "reason": c.get("reason", ""),
                "notes": c.get("notes", ""),
            })
            added += 1
            continue

        if existing["status"] != "pending":
            skipped += 1
            continue

        # A recycled ticker pointing at a different issuer is the premise of the
        # sibling delisted_check. Surface it rather than overwriting the company.
        old_co = (existing.get("company") or "").strip().lower()
        new_co = (c.get("company") or "").strip().lower()
        if old_co and new_co and old_co != new_co:
            logger.warning(
                "%s: ledger holds %r but candidate says %r - company name changed "
                "or ticker recycled; keeping the ledger's and flagging",
                ticker, existing.get("company"), c.get("company"))
            existing["notes"] = (existing.get("notes", "") +
                                 f" [name conflict {iso}: {c.get('company')}]").strip()

        existing["last_seen"] = iso
        if c.get("slack_thread_ts"):
            existing["slack_thread_ts"] = c["slack_thread_ts"]
        refreshed += 1

    return {"added": added, "refreshed": refreshed, "skipped_decided": skipped}


def decide(rows: list[dict], ticker: str, status: str, *, today: date,
           source: str = "", notes: str = "") -> dict:
    """Record a decision. Returns the updated row."""
    if status not in STATUSES:
        raise LedgerError(f"unknown status {status!r} (expected one of {STATUSES})")
    row = by_ticker(rows, ticker)
    if row is None:
        raise LedgerError(f"{ticker}: not in the ledger")
    row["status"] = status
    row["decision_date"] = today.isoformat()
    row["decision_source"] = source
    if notes:
        row["notes"] = (row.get("notes", "") + " " + notes).strip()
    return row


def revive(rows: list[dict], ticker: str, *, today: date, source: str = "") -> dict:
    """Return a declined/expired candidate to `pending` with a fresh clock."""
    row = by_ticker(rows, ticker)
    if row is None:
        raise LedgerError(f"{ticker}: not in the ledger")
    row["status"] = "pending"
    row["pending_since"] = today.isoformat()
    row["decision_date"] = ""
    row["decision_source"] = source
    return row


def expire_stale(rows: list[dict], *, today: date,
                 max_age_days: int = EXPIRY_DAYS) -> list[dict]:
    """Expire pending candidates older than `max_age_days`. Returns expired rows.

    Ledger-only: never touches the universe CSV, so this is safe unattended.
    A row with an unparseable/blank `pending_since` is left alone and warned about —
    an unreadable date is not evidence that something is stale.
    """
    cutoff = today - timedelta(days=max_age_days)
    out = []
    for r in pending(rows):
        raw = (r.get("pending_since") or "").strip()
        try:
            since = date.fromisoformat(raw)
        except ValueError:
            logger.warning("%s: unparseable pending_since %r - not expiring",
                           r.get("ticker"), raw)
            continue
        if since < cutoff:
            r["status"] = "expired"
            r["decision_date"] = today.isoformat()
            r["decision_source"] = f"auto-expiry after {max_age_days}d"
            out.append(r)
    return out


# ----------------------------------------------------------------- manual CLI


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="candidate_ledger")
    ap.add_argument("action", choices=["list", "pending", "expire"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    rows = load()
    if a.action in ("list", "pending"):
        sel = pending(rows) if a.action == "pending" else rows
        for r in sel:
            print(f"{r['ticker']:<10} {r['status']:<9} "
                  f"first={r['first_proposed']} clock={r['pending_since']}  "
                  f"{r['company'][:44]}")
        print(f"\n{len(sel)} row(s); {len(pending(rows))} pending of {len(rows)} total")
        return 0

    expired = expire_stale(rows, today=date.today())
    for r in expired:
        print(f"EXPIRED {r['ticker']} (pending since {r['pending_since']})")
    if expired and not a.dry_run:
        save(rows)
    print(f"{len(expired)} expired{' (dry run, not saved)' if a.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
