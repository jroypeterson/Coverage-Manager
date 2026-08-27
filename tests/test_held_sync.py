"""Tests for the broker-derived `Held` column (`universe/held.py`).

The ordering here is deliberate: the GUARDS come first, because the failure this
module exists to prevent is not "the sync is wrong about one name" -- it is "the
sync reads an absent or broken feed as *everything was sold* and empties the book
in one run, and every downstream repo faithfully acts on it."

Every guard test asserts the positions file is **byte-identical** afterwards, not
merely that a function raised. An abort that still rewrote the file would be no
protection at all, and only the bytes prove it did not. Same assertion the
sibling `portfolio_daily/tests/test_fail_closed.py` uses, for the same reason.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from universe import held as held_mod
from universe import positions as pos


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _feed_payload(tickers=("AAPL", "MSFT"), age=1.0, schema=1):
    return {
        "schema": schema,
        "generated_at": "2026-08-23T10:00:00-04:00",
        "brokers": [{"broker": "IBKR", "as_of": "2026-08-22", "age_days": age}],
        "stalest_age_days": age,
        "held": [
            {"ticker": t, "shares": 10.5, "avg_cost": 100.25, "brokers": ["IBKR"]}
            for t in tickers
        ],
    }


def _write_feed(tmp_path, payload):
    p = tmp_path / "held.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _entries(spec):
    """spec: {ticker: (position, held)}"""
    out = []
    for t, (p, h) in spec.items():
        e = {"Ticker": t, "Position": p, "Position Date": "", "Buy Price": None,
             "Sell Price": None, "First Buy Date": "", "Average Cost": None,
             "Shares": None, "Notes": "", "Held": h, "Held As Of": "",
             "Previously Held": "", "Held Until": ""}
        # Intent lives in the flags since 2026-08-23; mirror the spec's Position
        # onto the matching flag so these fixtures describe a real row.
        for f in pos.STATE_FLAGS:
            e[f] = "Y" if f == p else ""
        out.append(e)
    return out


@pytest.fixture()
def book(tmp_path):
    """A written positions CSV plus a snapshot of its exact bytes."""
    path = tmp_path / "positions.csv"
    pos.save(_entries({
        "AAPL": ("Researching", "Y"),
        "MSFT": ("Researching", "Y"),
        "ZZZZ": ("Researching", ""),
    }), path)
    return path, path.read_bytes()


# ---------------------------------------------------------------------------
# GUARDS -- each must abort AND leave the file byte-identical
# ---------------------------------------------------------------------------

def _assert_untouched(book):
    path, original = book
    assert path.read_bytes() == original, (
        "the positions file changed on an aborted sync -- an abort that still "
        "writes is not a guard"
    )


def test_missing_feed_aborts_and_writes_nothing(book, tmp_path):
    with pytest.raises(held_mod.HeldFeedError, match="not found"):
        held_mod.load_feed(tmp_path / "does_not_exist.json")
    _assert_untouched(book)


def test_unreadable_feed_aborts(book, tmp_path):
    p = tmp_path / "held.json"
    p.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(held_mod.HeldFeedError, match="unreadable"):
        held_mod.load_feed(p)
    _assert_untouched(book)


def test_unknown_schema_aborts(book, tmp_path):
    p = _write_feed(tmp_path, _feed_payload(schema=99))
    with pytest.raises(held_mod.HeldFeedError, match="schema"):
        held_mod.load_feed(p)
    _assert_untouched(book)


def test_stale_feed_aborts(book, tmp_path):
    p = _write_feed(tmp_path, _feed_payload(age=held_mod.HELD_STALE_MAX_DAYS + 0.1))
    with pytest.raises(held_mod.HeldFeedError, match="old"):
        held_mod.load_feed(p)
    _assert_untouched(book)


def test_a_feed_at_exactly_the_limit_is_still_accepted(tmp_path):
    """The boundary is <=, not <. A feed refused ON the limit would make the
    threshold effectively one day tighter than it is documented to be."""
    p = _write_feed(tmp_path, _feed_payload(age=held_mod.HELD_STALE_MAX_DAYS))
    assert len(held_mod.load_feed(p).rows) == 2


def test_empty_feed_aborts_rather_than_selling_everything(book, tmp_path):
    """THE headline failure. Zero holdings is far likelier to be a broken
    publisher than a liquidated account, and treating it as the latter is
    unrecoverable."""
    p = _write_feed(tmp_path, _feed_payload(tickers=()))
    with pytest.raises(held_mod.HeldFeedError, match="zero holdings"):
        held_mod.load_feed(p)
    _assert_untouched(book)


def test_mass_demotion_is_blocked_and_names_the_casualties(tmp_path):
    """The guard that catches a feed which is present, fresh, well-formed and WRONG."""
    entries = _entries({f"T{i}": ("Researching", "Y")
                        for i in range(held_mod.MAX_DEMOTIONS_PER_RUN + 3)})
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("T0",))))
    plan = held_mod.plan_sync(entries, feed)
    assert plan.is_blocked
    assert "T5" in plan.blocked_reason
    assert "nothing written" in " ".join(plan.summary_lines())


def test_a_blocked_plan_cannot_reach_disk_by_any_path(tmp_path):
    """Belt and braces: `apply_plan` refuses a blocked plan even if a caller
    forgets to check `is_blocked`."""
    entries = _entries({f"T{i}": ("Researching", "Y")
                        for i in range(held_mod.MAX_DEMOTIONS_PER_RUN + 3)})
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("T0",))))
    plan = held_mod.plan_sync(entries, feed)
    with pytest.raises(held_mod.HeldFeedError, match="blocked"):
        held_mod.apply_plan(entries, feed, plan)


# ---------------------------------------------------------------------------
# behaviour
# ---------------------------------------------------------------------------

def test_a_sale_lands_on_Researching_and_records_its_history(tmp_path):
    entries = _entries({"AAPL": ("Researching", "Y"), "SOLD": ("Researching", "Y")})
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL",))))
    plan = held_mod.plan_sync(entries, feed)
    out = {e["Ticker"]: e for e in held_mod.apply_plan(entries, feed, plan,
                                                      today=date(2026, 8, 23))}
    sold = out["SOLD"]
    assert sold["Held"] == "N"
    assert sold["Previously Held"] == "Y"
    assert sold["Held Until"] == "2026-08-23"
    # JP 2026-08-24: a sold name lands on Following for Interest -- you are not
    # building a thesis on something you have sold, you are interested in what it
    # says. Asserted on the FLAG, because `Position` is only a derived mirror.
    assert sold["Following for Interest"] == "Y"
    assert sold["Researching"] == "", "the old landing state must be cleared"


def test_a_row_that_is_not_held_carries_no_shares_or_cost(tmp_path):
    """ROIV kept '3400 shares @ $5.00' for 19 days after the account was
    liquidated. Figures are facts about a HOLDING; a non-held row must not
    carry them, whatever wrote them."""
    entries = _entries({"GONE": ("Researching", "")})
    entries[0]["Shares"] = 3400.0
    entries[0]["Average Cost"] = 5.0
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL",))))
    plan = held_mod.plan_sync(entries, feed)
    out = held_mod.apply_plan(entries, feed, plan)[0]
    assert out["Shares"] is None and out["Average Cost"] is None


def test_a_held_ticker_missing_from_the_universe_is_reported_not_dropped(tmp_path):
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL", "GHOST"))))
    plan = held_mod.plan_sync(_entries({"AAPL": ("Researching", "Y")}),
                              feed, universe_tickers=["AAPL"])
    assert plan.not_in_universe == ["GHOST"]
    assert "GHOST" in " ".join(plan.summary_lines())


def test_an_unmigrated_file_promotes_rather_than_mass_demoting(tmp_path):
    """A pre-2026-08-23 CSV has no `Held` column at all, so every row reads "".
    That must mean 'not yet known', never 'not held' -- otherwise the first run
    on an old file would look like a total liquidation."""
    entries = _entries({"AAPL": ("Researching", ""), "MSFT": ("Researching", "")})
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload()))
    plan = held_mod.plan_sync(entries, feed)
    assert plan.demotions == []
    assert sorted(plan.promotions) == ["AAPL", "MSFT"]


def test_legacy_Portfolio_rows_absent_from_the_feed_are_marked_previously_held(tmp_path):
    """On the first run nothing carries Held=Y, so `plan_sync` sees no demotion.
    The old `Position == "Portfolio"` is the evidence that the name WAS owned."""
    entries = _entries({"AAPL": ("Portfolio", ""), "ROIV": ("Portfolio", "")})
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL",))))
    out, migrated, already_sold = held_mod.migrate_legacy_portfolio(entries, feed)
    assert migrated == ["AAPL", "ROIV"]
    assert already_sold == ["ROIV"]
    roiv = {e["Ticker"]: e for e in out}["ROIV"]
    assert roiv["Previously Held"] == "Y"
    # The sale date is NOT inferred from the feed's as-of: we know it is gone by
    # then, not the day it went. ROIV sold 2026-08-03 and RPD/U 2026-08-21 -- the
    # feed cannot tell them apart, so it must not claim to.
    assert roiv["Held Until"] == ""


def test_the_legacy_migration_is_inert_on_a_second_run(tmp_path):
    entries = _entries({"AAPL": ("Researching", "Y")})
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL",))))
    _, migrated, already_sold = held_mod.migrate_legacy_portfolio(entries, feed)
    assert migrated == [] and already_sold == []


def test_Portfolio_is_no_longer_an_authorable_Position(tmp_path):
    """Ownership cannot be typed. If this ever passes again, the whole change has
    been undone."""
    assert "Portfolio" not in pos.ALLOWED_POSITION_VALUES
    assert "Portfolio" not in pos.POSITION_VALUES_ORDERED


def test_fractional_shares_survive_a_round_trip(tmp_path):
    """`_parse_int` used to do int(float(x)) and would publish 452 shares for a
    452.656 holding -- a wrong number that looks entirely plausible."""
    path = tmp_path / "p.csv"
    e = _entries({"FMS": ("Researching", "Y")})
    e[0]["Shares"] = 452.656
    pos.save(e, path)
    assert pos.load(path)[0]["Shares"] == pytest.approx(452.656)


def test_the_symbol_alias_maps_the_broker_symbol_onto_the_universe_symbol(tmp_path):
    """Without it, the first run reports that JP sold Fiserv: the feed says FISV,
    the universe says FI, and nothing joins them. Stopgap for board #345."""
    p = _write_feed(tmp_path, _feed_payload(tickers=("FISV",)))
    feed = held_mod.load_feed(p)
    assert "FI" in feed.rows and "FISV" not in feed.rows
    assert feed.aliased == ["FISV->FI"]


def test_the_alias_map_comes_from_the_published_store_not_a_hardcoded_dict():
    """#345 landed: this was a hardcoded {"FISV": "FI"} pinned at one entry.

    The pin was the right guard for a stopgap and the wrong one afterwards -- it
    would now fail the moment a SECOND genuine split is recorded, which is exactly
    the case the store exists to serve. What must stay true is that this module
    reads the shared store rather than growing a private map of its own, so the
    assertion moved to the source of the mapping.
    """
    from universe.aliases import load_aliases

    published = {alias: e["canonical"] for alias, e in load_aliases()["by_alias"].items()}
    assert held_mod.SYMBOL_ALIASES == published
    assert held_mod.SYMBOL_ALIASES.get("FISV") == "FI"


def test_an_unreadable_alias_store_RAISES_rather_than_degrading(tmp_path, monkeypatch):
    """This test asserted the OPPOSITE for a few hours, and the assertion was wrong.

    It claimed degrading to an empty map was safe "because `MAX_DEMOTIONS_PER_RUN`
    aborts". It does not: that guard fires above FIVE demotions and a single
    unjoined holding is one. The next test reproduces what actually happened.
    """
    bad = tmp_path / "ticker_aliases.json"
    bad.write_text("{not json", encoding="utf-8")
    import universe.aliases as aliases_mod

    monkeypatch.setattr(aliases_mod, "ALIASES_PATH", bad)
    with pytest.raises(held_mod.AliasStoreUnavailable):
        held_mod._load_symbol_aliases()


def test_an_empty_alias_map_fabricates_a_SALE_of_a_real_position(tmp_path):
    """The defect behind the fix above, pinned so the degrade-quietly option cannot
    look attractive again.

    With no alias joining the feed's `FISV` to the universe's `FI`, the row is
    planned as a demotion, the guard does NOT block one demotion, and `apply_plan`
    writes Held=N, clears Shares and Average Cost, and stamps a sale date.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("FISV",))))
        entries = _entries({"FI": ("Portfolio", "Y")})
        plan = held_mod.plan_sync(entries, feed)
        assert plan.demotions == ["FI"]
        assert plan.blocked_reason is None, (
            "one demotion is well under MAX_DEMOTIONS_PER_RUN -- this is exactly why "
            "an unreadable alias store must raise instead of degrading")
        out = held_mod.apply_plan(entries, feed, plan, today=date(2026, 8, 27))
        row = next(e for e in out if e["Ticker"] == "FI")
        assert row["Held"] == "N" and row["Held Until"] == "2026-08-27"
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_two_feed_rows_that_normalize_onto_one_ticker_are_MERGED(tmp_path):
    """Assigning would have eaten one broker's shares entirely.

    The same issuer held at two brokers under two spellings arrives as two rows;
    `rows[t] = ...` kept whichever came last. Shares add, brokers union, and the
    cost basis is share-weighted -- two lots at different prices have one blended
    basis, and picking either lot's price misstates P&L on both.
    """
    payload = _feed_payload(tickers=())
    payload["held"] = [
        {"ticker": "FI", "shares": 10.0, "avg_cost": 50.0, "brokers": ["fidelity"]},
        {"ticker": "FISV", "shares": 20.0, "avg_cost": 60.0, "brokers": ["ibkr"]},
    ]
    row = held_mod.load_feed(_write_feed(tmp_path, payload)).rows["FI"]
    assert row.shares == 30.0
    assert row.avg_cost == pytest.approx((10 * 50 + 20 * 60) / 30)
    assert row.brokers == ["fidelity", "ibkr"]


def test_a_merge_with_one_unknown_cost_basis_reports_UNKNOWN_not_a_half_blend(tmp_path):
    """A blend needs both sides; inventing one from the half we have is worse than
    admitting we do not know it."""
    payload = _feed_payload(tickers=())
    payload["held"] = [
        {"ticker": "FI", "shares": 10.0, "avg_cost": None, "brokers": ["fidelity"]},
        {"ticker": "FISV", "shares": 20.0, "avg_cost": 60.0, "brokers": ["ibkr"]},
    ]
    row = held_mod.load_feed(_write_feed(tmp_path, payload)).rows["FI"]
    assert row.shares == 30.0
    assert row.avg_cost is None


def test_the_completed_migration_stays_inert_on_a_held_book(tmp_path):
    """Regression, found by running the sync twice on 2026-08-23.

    `save()` writes `Position` as a DERIVED MIRROR and it reads "Portfolio" for
    every held row -- so a migration keyed on that string re-fires on all 30
    holdings every run and reports work it did not do. The flags are the store;
    the mirror is not evidence of anything.
    """
    entries = _entries({"AAPL": ("Portfolio", "Y")})   # mirror says Portfolio, held
    entries[0]["Researching"] = ""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL",))))
    out, migrated, already_sold = held_mod.migrate_legacy_portfolio(entries, feed)
    assert migrated == [] and already_sold == []
    assert out[0]["Following for Interest"] == "", "a held row carries no landing flag"


def test_a_genuinely_legacy_row_still_migrates(tmp_path):
    """The flip side: a row with NO intent flag whose old Position said Portfolio,
    and which the brokers do not hold, is a sale that already happened."""
    entries = _entries({"ROIV": ("Portfolio", "")})
    for f in ("Researching", "Following for Interest", "Ready to Buy", "Ready to Short"):
        entries[0][f] = ""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL",))))
    out, migrated, already_sold = held_mod.migrate_legacy_portfolio(entries, feed)
    assert migrated == ["ROIV"] and already_sold == ["ROIV"]
    assert out[0]["Following for Interest"] == "Y"
    assert out[0]["Previously Held"] == "Y"


def test_the_demotion_flag_list_matches_positions():
    """`held.py` names the state flags locally to dodge a circular import. If the
    two lists drift, a demotion clears the wrong ones and leaves the name in two
    states at once -- silently, because both files still import."""
    assert set(held_mod.STATE_FLAGS_FOR_DEMOTION) == set(pos.STATE_FLAGS)


def test_the_landing_state_is_one_of_the_real_states():
    assert held_mod.DEMOTION_POSITION in pos.STATE_FLAGS


def test_an_unjoined_feed_holding_alongside_a_demotion_BLOCKS(tmp_path):
    """Codex round 2, Critical: the alias-store guards could not cover this.

    `_load_symbol_aliases` raises on an UNREADABLE store, but a MISSING one
    legitimately yields an empty map, and an entry contradicting the universe is
    never checked here at all -- this module reads data/ticker_aliases.json
    directly, so the publish-side guard is irrelevant to it. Both roads end in the
    same fabricated sale, so the guard belongs at the point of HARM: a feed row
    that failed to join, in the same run as a demotion, is that shape.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()          # a MISSING store, not a broken one
        feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("FISV",))))
        entries = _entries({"FI": ("Portfolio", "Y")})
        # One position seen twice carries the SAME share count on both sides --
        # that equality is the discriminator, not mere co-occurrence.
        entries[0]["Shares"] = "10.5"
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["FI"])
        assert plan.demotions == ["FI"]
        assert plan.blocked_reason and "fabricated sale" in plan.blocked_reason
        with pytest.raises(Exception):
            held_mod.apply_plan(entries, feed, plan, today=date(2026, 8, 27))
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_a_demotion_with_every_feed_row_joined_is_NOT_blocked(tmp_path):
    """The guard must not become the outage: an ordinary sale still applies."""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL",))))
    entries = _entries({"AAPL": ("Portfolio", "Y"), "MRNA": ("Portfolio", "Y")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers=["AAPL", "MRNA"])
    assert plan.demotions == ["MRNA"]
    assert plan.blocked_reason is None


def test_the_guard_is_inert_without_a_universe_to_check_against(tmp_path):
    """`not_in_universe` is only populated when the caller passes the universe;
    without it we cannot see the signature and must not pretend to."""
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("FISV",))))
        plan = held_mod.plan_sync(_entries({"FI": ("Portfolio", "Y")}), feed)
        assert plan.not_in_universe == []
        assert plan.blocked_reason is None
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_an_ORDINARY_REBALANCE_is_not_blocked_by_the_false_sale_guard(tmp_path):
    """Codex round 3: v1 of that guard blocked on mere CO-OCCURRENCE.

    Sell a covered name and buy an uncovered one in the same week -- an ordinary
    week -- and the whole sync aborted while the sold name kept publishing as
    owned. That is "a guard can become the outage", and it contradicted this
    module's own contract that an uncovered holding is not fatal. The share count
    is the discriminator: one position seen twice matches, two unrelated trades
    do not.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("TSLA",))))
        entries = _entries({"AAPL": ("Portfolio", "Y")})
        entries[0]["Shares"] = "4"          # nothing like the feed's 10.5
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["AAPL"])
        assert plan.demotions == ["AAPL"]
        assert plan.not_in_universe == ["TSLA"]
        assert plan.blocked_reason is None
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_a_demotion_with_no_recorded_share_count_is_allowed_through(tmp_path):
    """Blocking on ignorance is how a guard becomes permanent."""
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("TSLA",))))
        entries = _entries({"AAPL": ("Portfolio", "Y")})     # Shares stays None
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["AAPL"])
        assert plan.blocked_reason is None
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


# ── Codex round 4 (2026-08-27) ──────────────────────────────────────────────

def test_an_alias_whose_source_is_ALSO_a_covered_row_blocks(tmp_path):
    """`aliases.check_universe` calls this its fatal case and the EXPORT path drops
    such an entry -- but this module reads data/ticker_aliases.json directly, so
    nothing checked it here. Repro: FISV -> FI with BOTH in the universe, FISV
    held and FI not. The feed normalises to FI, the plan promotes FI and demotes
    FISV, reports no unjoined holding, and publishes the wrong company as owned --
    silently, because every other guard sees a tidy one-in-one-out swap.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update({"FISV": "FI"})
        feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("FISV",))))
        entries = _entries({"FISV": ("Portfolio", "Y"), "FI": ("Researching", "")})
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["FI", "FISV"])
        assert plan.blocked_reason and "merge two separately-covered companies" in plan.blocked_reason
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_a_feed_of_blank_tickers_is_REFUSED_not_read_as_everything_sold(tmp_path):
    """The empty-feed guard checked the RAW list; blank rows are skipped after it.

    So `held=[{"ticker": ""}]` was non-empty going in, empty coming out, sailed
    past "refusing to treat an empty book as everything-was-sold", and marked the
    whole book sold with two demotions comfortably under the circuit breaker.
    """
    payload = _feed_payload(tickers=())
    payload["held"] = [{"ticker": "", "shares": 1.0, "avg_cost": 1.0, "brokers": ["IBKR"]}]
    with pytest.raises(held_mod.HeldFeedError, match="none had a usable ticker"):
        held_mod.load_feed(_write_feed(tmp_path, payload))


def test_one_blank_row_among_good_ones_is_also_refused(tmp_path):
    """An unseen holding reads as a sale; one blank row fabricates one sale."""
    payload = _feed_payload(tickers=())
    payload["held"] = [
        {"ticker": "AAPL", "shares": 10.0, "avg_cost": 1.0, "brokers": ["IBKR"]},
        {"ticker": "", "shares": 1.0, "avg_cost": 1.0, "brokers": ["IBKR"]},
    ]
    with pytest.raises(held_mod.HeldFeedError, match="no ticker"):
        held_mod.load_feed(_write_feed(tmp_path, payload))


def test_a_PARTIAL_alias_failure_blocks_even_with_no_demotion(tmp_path):
    """The guard's rule is "shares lost == shares unjoined"; a demotion is only
    its extreme case.

    A 30-share position held at two brokers under two spellings, alias store
    gone, arrives as FI:10 plus an unjoined FISV:20. That is `refreshed`, not a
    demotion -- previously unblocked, and apply_plan overwrote 30 shares with 10
    and one broker's cost basis with the other's.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        payload = _feed_payload(tickers=())
        payload["held"] = [
            {"ticker": "FI", "shares": 10.0, "avg_cost": 50.0, "brokers": ["Fidelity"]},
            {"ticker": "FISV", "shares": 20.0, "avg_cost": 60.0, "brokers": ["IBKR"]},
        ]
        feed = held_mod.load_feed(_write_feed(tmp_path, payload))
        entries = _entries({"FI": ("Portfolio", "Y")})
        entries[0]["Shares"] = "30"
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["FI"])
        assert plan.demotions == [] and plan.refreshed == ["FI"]
        assert plan.blocked_reason and "loses 20 share(s)" in plan.blocked_reason
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_a_position_that_merely_SHRANK_is_not_blocked(tmp_path):
    """Selling part of a position is ordinary; only a match against an unjoined
    holding's exact share count is the split signature."""
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        payload = _feed_payload(tickers=())
        payload["held"] = [
            {"ticker": "FI", "shares": 10.0, "avg_cost": 50.0, "brokers": ["Fidelity"]},
            {"ticker": "TSLA", "shares": 7.0, "avg_cost": 400.0, "brokers": ["IBKR"]},
        ]
        feed = held_mod.load_feed(_write_feed(tmp_path, payload))
        entries = _entries({"FI": ("Portfolio", "Y")})
        entries[0]["Shares"] = "30"          # sold 20, unjoined TSLA holds 7
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["FI"])
        assert plan.not_in_universe == ["TSLA"]
        assert plan.blocked_reason is None
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)
