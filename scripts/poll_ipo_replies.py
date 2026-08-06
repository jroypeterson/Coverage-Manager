"""Apply JP's `add` / `decline` replies in #ipo-spinoffs-newissues to the ledger.

**Why this exists.** Every weekly report has ended with *"To act, reply in this
thread: `add MU`, `decline MU`, or `add all`"* — and nothing read that thread.
`approve_candidates.py`'s own docstring said so: *"The Slack reply-poller will call
exactly this; today it is driven by hand."* The result on 2026-07-31 was three
recommendations sitting `pending` with an instruction that could not work. The
sibling lane (`watchlist_harmonizer`) has had `WatchlistHarmonizerReplies` polling
#coverage three times a day since 2026-07-14; this is the same shape for this lane.

Design rules, each earned elsewhere in the fleet:

- **Gated to the pending ledger.** A reply naming a ticker that is not `pending`
  is reported, never acted on. The poller can only ever resolve a decision the
  report actually asked for.
- **Idempotent by message timestamp**, not by outcome. A processed `ts` is
  recorded whether it succeeded or failed, so a transient enrichment failure does
  not re-fire the approval on the next pass — position is never the key.
- **Only the approver.** Messages from anyone else, and from the bot itself, are
  ignored; otherwise the bot's own "3 pending: MU" summary reads as an instruction.
- **Republish, or the approval is invisible.** Writing the universe CSV changes
  nothing outside this repo until `exports/` regenerates. That runs here, and its
  outcome is reported — an approval that silently fails to reach the seven
  downstream consumers is the same class of failure as the report nobody saw.
- **Answer in the thread.** JP gets a reply saying what landed and what did not,
  so a decision never disappears into a state file.

Usage:
    python scripts/poll_ipo_replies.py                # apply + republish
    python scripts/poll_ipo_replies.py --dry-run      # parse and report only
    python scripts/poll_ipo_replies.py --no-publish   # apply, skip the republish
    python scripts/poll_ipo_replies.py --since 2026-08-01   # re-scan from a date
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from universe import candidate_ledger as cl  # noqa: E402

SLACK_API = "https://slack.com/api/"
STATE_PATH = PROJECT_ROOT / "data" / "ipo_reply_state.json"
DEFAULT_APPROVER = "U0ALRRASV6X"          # Jason Peterson
HISTORY_LIMIT = 40                        # ~3 months of weekly posts

ADD_ALL = re.compile(r"^\s*add\s+all\s*$", re.I)
DECIDE = re.compile(r"^\s*(add|approve|decline|reject|skip)\s+([A-Za-z0-9._-]{1,15})\s*$",
                    re.I)
APPROVE_WORDS = {"add", "approve"}


class PollError(RuntimeError):
    pass


# --------------------------------------------------------------------------- io


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name, "")
    if val:
        return val
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    if default:
        return default
    raise PollError(f"{name} not set (checked env and {env_path})")


def _api(method: str, params: dict, token: str, *, post: bool = False) -> dict:
    if post:
        req = urllib.request.Request(
            SLACK_API + method, data=json.dumps(params).encode("utf-8"),
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json; charset=utf-8"})
    else:
        req = urllib.request.Request(
            SLACK_API + method + "?" + urllib.parse.urlencode(params),
            headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    if not body.get("ok"):
        raise PollError(f"{method} failed: {body.get('error')}")
    return body


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("applied_ts", [])
                return data
        except (ValueError, OSError):
            # A corrupt state file must not silently re-apply every past reply.
            raise PollError(f"unreadable state file: {STATE_PATH}")
    return {"applied_ts": [], "last_scan": ""}


def save_state(state: dict) -> None:
    state["applied_ts"] = sorted(set(state["applied_ts"]))[-500:]
    state["last_scan"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


# ---------------------------------------------------------------------- parsing


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s and not s.startswith(">"):
            return s
    return ""


def parse_reply(text: str) -> tuple[str, str] | None:
    """-> ('approve'|'decline', TICKER) or ('approve','ALL'), else None."""
    line = first_line(text)
    if not line:
        return None
    # Strip Slack's leading @-mention so "@ClaudeBot add MU" works.
    line = re.sub(r"^<@[A-Z0-9]+>\s*", "", line).strip()
    if ADD_ALL.match(line):
        return ("approve", "ALL")
    m = DECIDE.match(line)
    if not m:
        return None
    verb, ticker = m.group(1).lower(), m.group(2).upper()
    return ("approve" if verb in APPROVE_WORDS else "decline", ticker)


def collect_replies(token: str, channel: str, approver: str,
                    oldest: str = "") -> list[dict]:
    """Every approver message in the channel, top-level and in threads."""
    params = {"channel": channel, "limit": HISTORY_LIMIT}
    if oldest:
        params["oldest"] = oldest
    history = _api("conversations.history", params, token).get("messages", [])

    found: list[dict] = []
    seen_threads: set[str] = set()
    for msg in history:
        # JP replies at top level as often as in-thread; the harmonizer poller
        # reads both for exactly this reason.
        if msg.get("user") == approver and msg.get("text"):
            found.append({"ts": msg["ts"], "text": msg["text"],
                          "thread_ts": msg.get("thread_ts") or msg["ts"]})
        parent = msg.get("thread_ts") or msg["ts"]
        if msg.get("reply_count") and parent not in seen_threads:
            seen_threads.add(parent)
            replies = _api("conversations.replies",
                           {"channel": channel, "ts": parent, "limit": 200},
                           token).get("messages", [])
            for r in replies:
                if r.get("user") == approver and r.get("text") and r["ts"] != parent:
                    found.append({"ts": r["ts"], "text": r["text"],
                                  "thread_ts": parent})
    # Oldest first: decisions are applied in the order JP made them.
    uniq = {m["ts"]: m for m in found}
    return [uniq[k] for k in sorted(uniq)]


# --------------------------------------------------------------------- applying


def _spec_for(row: dict) -> str:
    """Build approve_candidates' TICKER:Sector:Subsector:Exchange from the ledger."""
    return ":".join([
        str(row.get("ticker", "")).strip(),
        str(row.get("sector", "")).strip(),
        str(row.get("subsector", "")).strip(),
        str(row.get("exchange", "")).strip(),
    ])


def republish() -> tuple[bool, str]:
    """Regenerate exports/ so the approval reaches the downstream consumers."""
    cmd = [sys.executable, "cli.py", "weekly-universe", "--skip-discovery"]
    try:
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True,
                              text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"republish could not start: {exc}"
    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-3:]
        return False, f"republish exited {proc.returncode}: {' | '.join(tail)}"
    return True, "exports republished"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="poll_ipo_replies")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-publish", action="store_true",
                    help="apply decisions but skip the exports republish")
    ap.add_argument("--since", default="",
                    help="re-scan from YYYY-MM-DD (ignores the applied cursor)")
    a = ap.parse_args(argv)

    token = _env("SLACK_BOT_TOKEN")
    channel = _env("SLACK_IPO_CHANNEL_ID")
    approver = _env("SLACK_APPROVER_USER_ID", DEFAULT_APPROVER)

    state = load_state()
    applied = set() if a.since else set(state["applied_ts"])
    oldest = ""
    if a.since:
        oldest = str(datetime.strptime(a.since, "%Y-%m-%d")
                     .replace(tzinfo=timezone.utc).timestamp())

    messages = [m for m in collect_replies(token, channel, approver, oldest)
                if m["ts"] not in applied]
    if not messages:
        # Stamp `last_scan` even on a silent run. Most runs find nothing, and the
        # fleet's artifact-freshness lane reads this file's mtime — a poller that
        # only touches state when it acts looks identical to one that has stopped.
        save_state(state)
        print("no new approver replies")
        return 0

    rows = cl.load()
    pending = {str(r["ticker"]).strip().upper() for r in cl.pending(rows)}

    approvals: list[str] = []
    declines: list[str] = []
    ignored: list[str] = []
    handled: list[dict] = []

    for msg in messages:
        parsed = parse_reply(msg["text"])
        if not parsed:
            continue                      # ordinary conversation, not a command
        action, ticker = parsed
        handled.append(msg)
        if ticker == "ALL":
            approvals.extend(sorted(pending - set(approvals)))
            continue
        if ticker not in pending:
            ignored.append(f"{ticker} ({action}) - not pending in the ledger")
            continue
        (approvals if action == "approve" else declines).append(ticker)

    if not handled:
        print("no command replies found")
        save_state(state)
        return 0

    approvals = [t for t in dict.fromkeys(approvals) if t not in declines]
    declines = list(dict.fromkeys(declines))
    print(f"approve={approvals} decline={declines} ignored={len(ignored)}")

    if a.dry_run:
        for line in ignored:
            print("  IGNORED", line)
        return 0

    results: list[str] = []

    if declines:
        for ticker in declines:
            cl.decide(rows, ticker, "declined", today=date.today(),
                      source="slack-reply")
        cl.save(rows)
        results.append(f":no_entry: declined: {', '.join(declines)}")

    approved_ok: list[str] = []
    if approvals:
        rows = cl.load()                  # re-read: decline() just rewrote it
        specs = []
        for ticker in approvals:
            row = cl.by_ticker(rows, ticker)
            if row is None:
                ignored.append(f"{ticker} - vanished from the ledger")
                continue
            if not str(row.get("sector", "")).strip():
                ignored.append(f"{ticker} - ledger row has no sector; stays pending")
                continue
            specs.append(_spec_for(row))
        if specs:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "approve_candidates", PROJECT_ROOT / "scripts" / "approve_candidates.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            rc = mod.main(["--source", "slack-reply"] +
                          [arg for s in specs for arg in ("--add", s)])
            after = {str(r["ticker"]).strip().upper()
                     for r in cl.pending(cl.load())}
            approved_ok = [t for t in approvals if t not in after]
            stuck = [t for t in approvals if t in after]
            if approved_ok:
                results.append(f":white_check_mark: added to the universe: "
                               f"{', '.join(approved_ok)}")
            if stuck:
                results.append(f":warning: still pending (enrichment refused a "
                               f"half-filled row): {', '.join(stuck)}")
            print(f"approve_candidates rc={rc}")

    if approved_ok and not a.no_publish:
        ok, note = republish()
        results.append((":white_check_mark: " if ok else ":x: ") + note)

    if ignored:
        results.append(":grey_question: ignored: " + "; ".join(ignored))

    thread_ts = handled[-1]["thread_ts"]
    _api("chat.postMessage",
         {"channel": channel, "thread_ts": thread_ts,
          "text": "Candidate ledger updated",
          "blocks": [{"type": "section", "text": {"type": "mrkdwn",
                      "text": "*Candidate ledger updated*\n" + "\n".join(results)}}]},
         token, post=True)

    state["applied_ts"] = list(applied | {m["ts"] for m in handled})
    save_state(state)
    print("\n".join(results).encode("ascii", "replace").decode("ascii"))
    return 0 if not ignored else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PollError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
