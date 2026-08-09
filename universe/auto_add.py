"""Decide which discovery candidates enter the universe without being asked.

**The problem this solves.** Every candidate the weekly lane finds goes into
`candidate_ledger.csv` as `pending`, and nothing reaches the universe until JP
replies `add TICKER`. For most buckets that is right — they are judgement calls.
For two of them it is a formality: his own inclusion rules say Bucket 2 ("include
ALL IPOs globally >= $25B **regardless of sector**") and Bucket 3 ("spin-offs,
direct listings, carve-outs, major separations if market cap > $10B") are
mandatory adds. Queueing a mandatory add just puts a step between the rule and
the outcome.

**What auto-adds, and what does not** (JP 2026-08-06, extended by his ruling 2026-08-09):

| Bucket | Rule | Behaviour |
|---|---|---|
| 2 | any IPO / direct listing >= $25B, any sector | **auto** — mandatory by rule |
| 3 | spin-off / carve-out / separation > $10B | **auto** — mandatory by rule |
| 1 | **core-sector** listing, ANY size | **auto** (2026-08-09) — but core is the *sector column*, see below |
| 5 | Russell first-time addition, $2–20B | **auto** (2026-08-09) |
| 4 | $2–20B "strategically relevant" | queue — explicitly a judgement call, and the only bucket left |

**Why 1 and 5 changed, and what the decision was actually based on.** They were held
back as "undecided, so default to the status quo". Measured across the whole
candidate ledger before putting it to JP: **31 candidates ever proposed, 29
approved, 2 declined.** Of the names these two buckets would have auto-added,
**Bucket 1 was 6-for-6 and Bucket 5 was 4-for-4** — and both declines (`DPC`
Industrials $7.1B, `EROC` Energy $3.6B) fall in *neither* bucket, so the change
would have produced **zero** wrong adds across the lane's entire history. JP had
also overridden the report *to add* `BLSM` ($465M) after it was excluded on the
sub-$1B biotech bar, i.e. he was already more inclusive than the queue was.

**Bucket 1 is gated on the SECTOR COLUMN, not on the bucket's prose.** The written
rule lists "healthcare services, MedTech, tools, diagnostics, HCIT, tech-enabled
healthcare, **adjacent technology relevant to my universe**, semis/instrumentation
relevant to the existing sheet". The last two are judgements, not predicates — a
`Sector (JP)` string cannot carry "relevant to my universe", and auto-adding on
`Tech` would sweep in every technology IPO at any size, which is emphatically not
what Bucket 1 means. So the mechanical gate is the unambiguously-core sectors
(`CORE_SECTORS`); a `Tech` core-adjacent name still queues, which is exactly where
the human judgement belongs. Verified against the ledger: all six historical
Bucket 1 names are `Biopharma` and all six are captured.

`New candidate` never auto-adds under any bucket — that trigger marks a
coverage-gap proposal (MU, FN, the optical complex), which is pure judgement by
construction.

**Three refusals, each of which would otherwise be a silent bad add:**

- **No market cap means NO auto-add, ever** — including for Bucket 1, which has
  no size floor. The gate now does two jobs: size-gating buckets 2/3/5, and
  standing in for *"does this actually trade yet"* on bucket 1. Before separation
  a spin-off has no shares and no cap, so a Form 10 pipeline entry must wait until
  it does; without this refusal, adding Bucket 1 would start auto-adding
  unseparated SpinCos on the strength of their SIC code alone.
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
BUCKET5_MIN_CAP = 2e9       # "Russell additions roughly $2B-$20B"
BUCKET5_MAX_CAP = 20e9

# `trigger` is a strict enum in discovery_output_schema.json.
BUCKET2_TRIGGERS = {"IPO", "Direct listing"}
BUCKET3_TRIGGERS = {"Spin-off", "Carve-out", "Direct listing"}
BUCKET1_TRIGGERS = {"IPO", "Direct listing", "Spin-off", "Carve-out"}
BUCKET5_TRIGGERS = {"Russell addition"}

# Bucket 1's mechanical gate. Deliberately NOT `Tech`: the written rule says
# "adjacent technology RELEVANT TO MY UNIVERSE", and relevance is the judgement
# the queue exists to collect. A Tech core-adjacent name still queues.
CORE_SECTORS = {"biopharma", "medtech", "healthcare services", "life science tools"}

AUTO_BUCKETS = {1, 2, 3, 5}


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

    Deliberately only recognises the AUTO buckets. Bucket 4 is not "not
    classified" — it is decided-to-queue, and conflating "no auto bucket" with
    "not relevant" is how a queue silently becomes a bin.

    Order matters: the size buckets are tested FIRST so a $30B biopharma IPO is
    reported as Bucket 2 rather than Bucket 1. Both would auto-add it, but the
    reason printed in the Slack post should name the rule that makes it
    *mandatory*, not the one that merely permits it.
    """
    trigger = str(candidate.get("trigger") or "").strip()
    sector = str(candidate.get("sector") or "").strip().lower()
    cap = _cap(candidate)
    if cap is None:
        return None
    if trigger in BUCKET2_TRIGGERS and cap >= BUCKET2_MIN_CAP:
        return 2
    if trigger in BUCKET3_TRIGGERS and cap > BUCKET3_MIN_CAP:
        return 3
    if trigger in BUCKET1_TRIGGERS and sector in CORE_SECTORS:
        return 1
    if trigger in BUCKET5_TRIGGERS and BUCKET5_MIN_CAP <= cap <= BUCKET5_MAX_CAP:
        return 5
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
        # Absent and unreadable are different facts and want different fixes: one
        # waits for the company to trade, the other is a malformed value someone
        # has to correct. Reporting both as "no market cap" sent a reader looking
        # for a listing date when the row actually said `~$3.7B` (PBLS, KARD --
        # hand-written before discovery_output_schema.json required a number).
        raw = str(candidate.get("market_cap") or "").strip()
        if raw:
            return Decision(ticker, False, None,
                            f"market cap {raw!r} is not machine-readable - the "
                            "schema requires a number, so this is never inferred "
                            "from prose to justify an unasked write")
        return Decision(ticker, False, None,
                        "no market cap - the auto buckets are size-gated, and for "
                        "bucket 1 the cap also stands in for 'does it trade yet' "
                        "(a pre-separation spin-off has none until it does)")

    bucket = classify_bucket(candidate)
    if bucket == 2:
        return Decision(ticker, True, 2,
                        f"Bucket 2 - {candidate.get('trigger')} at "
                        f"${cap / 1e9:,.1f}B, mandatory at >= $25B regardless of sector")
    if bucket == 3:
        return Decision(ticker, True, 3,
                        f"Bucket 3 - {candidate.get('trigger')} at "
                        f"${cap / 1e9:,.1f}B, mandatory above $10B")
    if bucket == 1:
        return Decision(ticker, True, 1,
                        f"Bucket 1 - {candidate.get('sector')} "
                        f"{candidate.get('trigger')} at ${cap / 1e9:,.2f}B, core "
                        f"sector at any size (JP's ruling 2026-08-09)")
    if bucket == 5:
        return Decision(ticker, True, 5,
                        f"Bucket 5 - Russell addition at ${cap / 1e9:,.1f}B, "
                        f"inside the $2-20B band (JP's ruling 2026-08-09)")
    return Decision(ticker, False, None,
                    f"queued - {candidate.get('trigger') or 'no trigger'} in "
                    f"{candidate.get('sector') or 'no sector'} at "
                    f"${cap / 1e9:,.1f}B does not meet an auto bucket "
                    f"(Bucket 4 queues by decision, not by omission)")


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
