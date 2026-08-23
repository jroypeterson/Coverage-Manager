"""Weekly print of what JP is Researching and Following for Interest -> #portfolio.

JP, 2026-08-23: *"I want to be able to just tell you what to update via the
portfolio channel. Maybe 1x a week you print my current researching and following
for interest sheet?"*

## Why a printout rather than a link

The two states are maintained from two surfaces now -- the Notion board and
instructions in the channel -- and neither of them is where he reads. A weekly
post is the cheapest thing that makes the state visible without opening anything,
and it doubles as the heartbeat: if it stops arriving, the lists have stopped
being maintained, which a bookmark cannot tell you.

## What it shows, and what it deliberately does not

`Following for Interest` gets its own section with its own explanation, because
its meaning is not self-evident from the name and he had to explain it: *"I would
be interested in particular in what they are saying on their earnings calls...
other companies are like bellwethers in their category."* A reader six months from
now (including him) should not have to reconstruct that from a bare ticker list.

Held names are NOT listed. They are ownership, they already have a daily digest
and two published pages, and repeating them here would bury the two lists this
post exists for.

Run: `python scripts/post_watchlist_to_portfolio.py [--dry-run]`
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from universe import positions as pos                              # noqa: E402

SECTIONS = [
    ("Researching", "building a thesis; not yet held"),
    ("Following for Interest",
     "not a candidate to buy - names whose earnings calls are worth reading. "
     "Bellwethers who set the tone for a category, or who speak early and broadly "
     "about the consumer."),
]


def build_blocks(entries, today):
    """Block Kit for the weekly print. Pure, so it is testable without a webhook."""
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": "Watchlist · weekly", "emoji": True}},
    ]
    total = 0
    for flag, gloss in SECTIONS:
        names = sorted(e["Ticker"] for e in entries
                       if pos.has_state(e, flag) and not pos.is_held(e))
        total += len(names)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*{flag}* — {len(names)}\n_{gloss}_\n"
                             + (" · ".join(f"`{n}`" for n in names) if names
                                else "_none_")},
        })

    held = sum(1 for e in entries if pos.is_held(e))
    blocks.append({
        "type": "context",
        # context REQUIRES elements[]; a bare `text` key renders NOTHING and Slack
        # does not complain about it.
        "elements": [{
            "type": "mrkdwn",
            "text": (f"{held} held (not listed here - see the daily digest) · "
                     f"{today} · reply here to change one, or edit the Notion board"),
        }],
    })
    fallback = f"Watchlist weekly - {total} name(s) across {len(SECTIONS)} states"
    return blocks, fallback


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Render and print; post nothing.")
    args = ap.parse_args()

    entries = pos.load(pos.POSITIONS_PATH)
    blocks, fallback = build_blocks(entries, date.today().isoformat())

    if args.dry_run:
        print(json.dumps(blocks, indent=2))
        return 0

    hook = (os.environ.get("SLACK_WEBHOOK_PORTFOLIO") or "").strip()
    if not hook:
        # Loud, and non-zero: a weekly post that silently never happens is
        # indistinguishable from a week with nothing to say.
        print("SLACK_WEBHOOK_PORTFOLIO unset - nothing posted")
        return 2

    payload = json.dumps({"text": fallback, "blocks": blocks}).encode("utf-8")
    req = urllib.request.Request(
        hook, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        ok = 200 <= getattr(resp, "status", 200) < 300
    print("posted watchlist weekly to #portfolio" if ok else "post failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
