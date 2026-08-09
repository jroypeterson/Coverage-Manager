"""Tests for the auto-add rules.

The asymmetry these guard: wrongly queueing costs one Slack reply; wrongly
auto-adding puts a row into the fleet's most-depended-on artifact without anyone
being asked. Every ambiguous case must therefore fall to `queue`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from universe import auto_add  # noqa: E402


def c(**kw):
    base = {"company": "X Inc.", "ticker": "X", "exchange": "NASDAQ",
            "sector": "Tech", "trigger": "IPO", "market_cap": 1e9}
    base.update(kw)
    return base


EMPTY = {"in_universe": set(), "removed": set()}


# ------------------------------------------------------------------- bucket 2


def test_a_25b_ipo_auto_adds_regardless_of_sector():
    """JP's rule is explicit: 'ALL IPOs globally >= $25B regardless of sector'."""
    d = auto_add.decide(c(market_cap=484e9, sector="Tech"), **EMPTY)
    assert d.auto and d.bucket == 2


def test_just_under_25b_queues():
    assert not auto_add.decide(c(market_cap=24.9e9), **EMPTY).auto


def test_exactly_25b_auto_adds():
    """The rule says '>= $25 billion'."""
    assert auto_add.decide(c(market_cap=25e9), **EMPTY).auto


# ------------------------------------------------------------------- bucket 3


@pytest.mark.parametrize("trigger", ["Spin-off", "Carve-out"])
def test_a_spin_off_above_10b_auto_adds(trigger):
    d = auto_add.decide(c(trigger=trigger, market_cap=15e9), **EMPTY)
    assert d.auto and d.bucket == 3


def test_a_spin_off_at_exactly_10b_queues():
    """The rule says 'over $10 billion', not at."""
    assert not auto_add.decide(c(trigger="Spin-off", market_cap=10e9), **EMPTY).auto


def test_a_small_spin_off_queues():
    assert not auto_add.decide(c(trigger="Spin-off", market_cap=3e9), **EMPTY).auto


# --------------------------------------------------- buckets that must QUEUE


def test_a_core_sector_microcap_now_auto_adds():
    """Bucket 1 has NO size floor and JP ruled it auto-adds (2026-08-09).

    This test previously asserted the opposite, and was right to: the bucket was
    undecided and an undecided rule defaults to the status quo. The ruling was
    made on measured evidence -- the lane had proposed 6 Bucket 1 names and JP
    approved 6, and had overridden an exclusion to add BLSM at $465M.
    """
    d = auto_add.decide(c(sector="Biopharma", market_cap=40e6), **EMPTY)
    assert d.auto
    assert d.bucket == 1


def test_a_russell_addition_in_band_now_auto_adds():
    d = auto_add.decide(c(trigger="Russell addition", market_cap=5e9), **EMPTY)
    assert d.auto
    assert d.bucket == 5


def test_a_russell_addition_outside_the_band_still_queues():
    """Bucket 5 is written as $2-20B. SNDK came through as a Russell addition at
    ~$29B and is exactly this case -- above the band, so a human still sees it."""
    assert not auto_add.decide(c(trigger="Russell addition", market_cap=29e9), **EMPTY).auto
    assert not auto_add.decide(c(trigger="Russell addition", market_cap=1e9), **EMPTY).auto


def test_a_TECH_listing_still_queues_however_relevant_it_looks():
    """Bucket 1's prose says "adjacent technology RELEVANT TO MY UNIVERSE".

    Relevance is the judgement the queue exists to collect, and a sector string
    cannot carry it. Gating Bucket 1 on `Tech` would auto-add every technology
    IPO at any size -- so Tech is deliberately NOT in CORE_SECTORS.
    """
    d = auto_add.decide(c(sector="Tech", trigger="IPO", market_cap=3e9), **EMPTY)
    assert not d.auto


def test_a_core_sector_candidate_with_no_cap_still_refuses():
    """Bucket 1 has no size floor, so the cap gate now stands in for "does this
    actually trade yet" -- an unseparated SpinCo has a core SIC and no shares."""
    d = auto_add.decide(c(sector="Biopharma", trigger="Spin-off", market_cap=None), **EMPTY)
    assert not d.auto


def test_a_large_core_sector_ipo_reports_the_MANDATORY_rule_not_the_permissive_one():
    """A $30B biopharma IPO satisfies both 1 and 2. The Slack post should name
    the rule that makes it mandatory."""
    d = auto_add.decide(c(sector="Biopharma", trigger="IPO", market_cap=30e9), **EMPTY)
    assert d.bucket == 2


def test_a_strategic_new_candidate_queues():
    """Bucket 4 is explicitly a judgement call in the rules themselves."""
    d = auto_add.decide(c(trigger="New candidate", market_cap=8e9), **EMPTY)
    assert not d.auto


def test_a_new_candidate_stays_queued_even_when_enormous():
    """Bucket 2 is about IPOs and direct listings. MU at ~$988B surfaced as a
    'New candidate' -- a coverage gap, not a listing event -- and JP was asked."""
    d = auto_add.decide(c(trigger="New candidate", market_cap=988e9), **EMPTY)
    assert not d.auto


# ----------------------------------------------------------------- refusals


def test_no_market_cap_never_auto_adds():
    """Both auto buckets are size-gated; None is unknown, not qualifying."""
    for cap in (None, "", 0, -1, "abc"):
        assert not auto_add.decide(c(market_cap=cap), **EMPTY).auto


def test_a_pre_separation_spin_off_waits_for_a_price():
    """A Form 10 candidate has no cap because no shares trade yet. It must queue
    until one exists, not auto-add on a guess."""
    d = auto_add.decide(c(trigger="Spin-off", market_cap=None), **EMPTY)
    assert not d.auto and "no market cap" in d.reason


def test_a_previously_removed_ticker_is_never_auto_added():
    """FMP re-proposed ALBT eight days after it was removed for leaving
    healthcare. A vendor cannot know a row was removed for scope."""
    d = auto_add.decide(c(ticker="ALBT", market_cap=30e9),
                        in_universe=set(), removed={"ALBT"})
    assert not d.auto and "removals list" in d.reason


def test_a_ticker_already_in_the_universe_is_skipped():
    d = auto_add.decide(c(ticker="LLY", market_cap=1e12),
                        in_universe={"LLY"}, removed=set())
    assert not d.auto and "already in the universe" in d.reason


def test_a_blank_ticker_never_auto_adds():
    assert not auto_add.decide(c(ticker=""), **EMPTY).auto


# --------------------------------------------------------------------- plan


def test_plan_partitions_every_candidate_exactly_once():
    cands = [c(ticker="A", market_cap=30e9),
             c(ticker="B", market_cap=1e9),
             c(ticker="C", trigger="Spin-off", market_cap=None)]
    auto, queued = auto_add.plan(cands, in_universe=set(), removed=set())
    assert [d.ticker for d in auto] == ["A"]
    assert sorted(d.ticker for d in queued) == ["B", "C"]
    assert len(auto) + len(queued) == len(cands)


def test_every_queued_decision_carries_a_reason():
    """A queue with no stated reason becomes a bin nobody audits."""
    cands = [c(ticker="A", market_cap=1e9), c(ticker="B", market_cap=None)]
    _, queued = auto_add.plan(cands, in_universe=set(), removed=set())
    assert all(d.reason for d in queued)


def test_every_reason_is_ascii_for_the_cp1252_console():
    """These strings are printed by sync_candidate_ledger under Task Scheduler,
    whose console is cp1252 -- a stray em-dash kills the run at the moment it is
    reporting what it did. The script's own comments already say so."""
    cases = [c(market_cap=30e9), c(market_cap=1e9), c(market_cap=None),
             c(trigger="Spin-off", market_cap=15e9), c(ticker="")]
    for cand in cases:
        auto_add.decide(cand, **EMPTY).reason.encode("ascii")
    auto_add.decide(c(ticker="Z"), in_universe=set(), removed={"Z"}).reason.encode("ascii")
    auto_add.decide(c(ticker="Y"), in_universe={"Y"}, removed=set()).reason.encode("ascii")


# ------------------------------------------- replay against the real ledger


def test_the_new_rules_would_have_auto_added_nothing_JP_declined():
    """The evidence the 2026-08-09 ruling was actually made on.

    Buckets 1 and 5 were opened up because, replayed over every candidate the
    lane has ever proposed, they auto-add only names JP approved. If a future
    change to CORE_SECTORS or the Bucket 5 band would have swept in `DPC` or
    `EROC` -- the only two he ever declined -- that is a regression against the
    reasoning, not just against a number.
    """
    from universe import candidate_ledger as cl

    rows = cl.load()
    declined = {r["ticker"].upper() for r in rows if r["status"] == "declined"}
    assert declined, "ledger has no declines; this test would pass vacuously"

    for row in rows:
        d = auto_add.decide(dict(row), in_universe=set(), removed=set())
        if d.auto:
            assert row["ticker"].upper() not in declined, (
                f"{row['ticker']} was DECLINED by JP but now auto-adds: {d.reason}")


def test_every_core_sector_name_with_a_READABLE_cap_is_captured_by_bucket_1():
    """The historical Bucket 1 names are all Biopharma; none should slip through.

    Scoped to rows whose `market_cap` is machine-readable. Two July rows (`PBLS`,
    `KARD`) store it as prose -- `~$3.7B` -- because they predate
    `discovery_output_schema.json` requiring a number. The live path cannot
    produce those, and auto_add deliberately refuses rather than parsing prose to
    justify an unasked write; the sibling test below pins that it says so honestly
    instead of claiming the cap is missing.
    """
    from universe import candidate_ledger as cl

    missed, unreadable = [], []
    for row in cl.load():
        if str(row.get("sector", "")).strip().lower() not in auto_add.CORE_SECTORS:
            continue
        if str(row.get("trigger", "")).strip() not in auto_add.BUCKET1_TRIGGERS:
            continue          # 'New candidate' is a judgement call by construction
        d = auto_add.decide(dict(row), in_universe=set(), removed=set())
        if d.auto:
            continue
        (unreadable if "not machine-readable" in d.reason else missed).append(
            (row["ticker"], d.reason))
    assert not missed, f"core-sector listings not captured: {missed}"
    # Named, not hidden: if this list grows, the ledger is accreting bad values.
    assert {t for t, _ in unreadable} <= {"PBLS", "KARD"}, unreadable


def test_a_prose_market_cap_refuses_and_SAYS_it_was_unreadable():
    """'no market cap' and 'I could not read the market cap' are different facts.

    One waits for the company to trade; the other is a malformed value someone has
    to fix. Reporting both the same way sent a reader hunting for a listing date on
    a row that plainly said `~$3.7B`.
    """
    d = auto_add.decide(c(sector="Biopharma", market_cap="~$3.7B"), **EMPTY)
    assert not d.auto
    assert "not machine-readable" in d.reason
    assert "~$3.7B" in d.reason
