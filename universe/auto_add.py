"""Decide which discovery candidates enter the universe without being asked.

**The problem this solves.** Every candidate the weekly lane finds goes into
`candidate_ledger.csv` as `pending`, and nothing reaches the universe until JP
replies `add TICKER`. For most buckets that is right — they are judgement calls.
For two of them it is a formality: his own inclusion rules say Bucket 2 ("include
ALL IPOs globally >= $25B **regardless of sector**") and Bucket 3 ("spin-offs,
direct listings, carve-outs, major separations if market cap > $10B") are
mandatory adds. Queueing a mandatory add just puts a step between the rule and
the outcome.

**What auto-adds, and what does not** (JP 2026-08-06):

| Bucket | Rule | Behaviour |
|---|---|---|
| 2 | any IPO / direct listing >= $25B, any sector | **auto** — mandatory by rule |
| 3 | spin-off / carve-out / separation > $10B | **auto** — mandatory by rule |
| 1 | core sector, ANY size | queue — no size floor, so it would auto-add microcaps |
| 4 | $2–20B "strategically relevant" | queue — explicitly a judgement call |
| 5 | Russell first-time additions | queue — ~40/quarter of mostly unfamiliar names |

Buckets 1 and 5 are undecided and therefore **queue**, which is exactly today's
behaviour. An undecided rule must default to the status quo, never to the new
thing: the cost of wrongly queueing is one Slack reply, and the cost of wrongly
auto-adding is a row in the fleet's most-depended-on artifact.

**Three refusals, each of which would otherwise be a silent bad add:**

- **No market cap means NO auto-add, ever.** Both auto buckets are size-gated,
  and `None` is unknown, not qualifying. This matters most for spin-offs: before
  separation no shares trade, so a Form 10 candidate legitimately has no cap and
  must wait until it does.
- **A ticker on the provenance removals list is never auto-added.** A vendor has
  no idea a row was removed for scope reasons and will keep proposing it — an FMP
  biopharma screen re-proposed ALBT eight days after it was removed for leaving
  healthcare.
- **Already in the universe means skip**, not re-add.

Enrichment still gates the write: `approve_candidates` refuses a half-filled
row, so an auto-add that cannot be enriched stays `pending` and says why. Auto
does not mean unvalidated — it means unasked.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Thresholds are JP's own inclusion rules, not tuned numbers. Changing one is a
# change to the rules, not to this module.
BUCKET2_MIN_CAP = 25e9      # "any IPO globally with market cap >= $25 billion"
BUCKET3_MIN_CAP = 10e9      # "spin-offs / direct listings / carve-outs over $10 billion"

# `trigger` is a strict enum in discovery_output_schema.json.
BUCKET2_TRIGGERS = {"IPO", "Direct listing"}
BUCKET3_TRIGGERS = {"Spin-off", "Carve-out", "Direct listing"}

AUTO_BUCKETS = {2, 3}


@dataclass
class Decision:
    ticker: str
    auto: bool
    bucket: int | None
    reason: str


def _cap(candidate: dict) -> float | None:
    raw = candidate.get("market_cap")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def classify_bucket(candidate: dict) -> int | None:
    """Which AUTO bucket a candidate qualifies for, or None.

    Deliberately only recognises the two auto buckets. Buckets 1/4/5 are not
    "not classified" — they are decided-to-queue, and conflating "no auto bucket"
    with "not relevant" is how a queue silently becomes a bin.
    """
    trigger = str(candidate.get("trigger") or "").strip()
    cap = _cap(candidate)
    if cap is None:
        return None
    if trigger in BUCKET2_TRIGGERS and cap >= BUCKET2_MIN_CAP:
        return 2
    if trigger in BUCKET3_TRIGGERS and cap > BUCKET3_MIN_CAP:
        return 3
    return None


def decide(candidate: dict, *, in_universe: set[str], removed: set[str]) -> Decision:
    """One candidate -> auto-add or queue, with the reason either way."""
    ticker = str(candidate.get("ticker") or "").strip()
    upper = ticker.upper()

    if not ticker:
        return Decision(ticker, False, None, "no ticker on the candidate")
    if upper in removed:
        return Decision(ticker, False, None,
                        "on the provenance removals list - deliberately removed "
                        "before; a vendor screen will keep re-proposing it")
    if upper in in_universe:
        return Decision(ticker, False, None, "already in the universe")

    cap = _cap(candidate)
    if cap is None:
        return Decision(ticker, False, None,
                        "no market cap - both auto buckets are size-gated and "
                        "unknown is not qualifying (a pre-separation spin-off "
                        "has no cap until it trades)")

    bucket = classify_bucket(candidate)
    if bucket == 2:
        return Decision(ticker, True, 2,
                        f"Bucket 2 - {candidate.get('trigger')} at "
                        f"${cap / 1e9:,.1f}B, mandatory at >= $25B regardless of sector")
    if bucket == 3:
        return Decision(ticker, True, 3,
                        f"Bucket 3 - {candidate.get('trigger')} at "
                        f"${cap / 1e9:,.1f}B, mandatory above $10B")
    return Decision(ticker, False, None,
                    f"queued - {candidate.get('trigger') or 'no trigger'} at "
                    f"${cap / 1e9:,.1f}B does not meet an auto bucket "
                    f"(1/4/5 queue by decision, not by omission)")


def plan(candidates: list[dict], *, in_universe: set[str],
         removed: set[str]) -> tuple[list[Decision], list[Decision]]:
    """-> (auto, queued). Every candidate appears in exactly one list."""
    decisions = [decide(c, in_universe=in_universe, removed=removed)
                 for c in candidates]
    auto = [d for d in decisions if d.auto]
    queued = [d for d in decisions if not d.auto]
    for d in auto:
        logger.warning("AUTO-ADD %s: %s", d.ticker, d.reason)
    return auto, queued
