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


def test_an_unjoined_feed_holding_WITHHOLDS_the_demotion(tmp_path):
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
        # WITHHELD, not blocked. Fable disproved the blocking version's premise:
        # a symbol change and a share-count change CO-OCCUR at a corporate action,
        # so "shares lost == shares unjoined" misses exactly when it is needed --
        # a 3-share DRIP was enough to write a fabricated sale.
        assert plan.demotions == []
        assert plan.withheld_demotions == ["FI"]
        assert plan.blocked_reason is None, "withholding must not block the run"
        out = held_mod.apply_plan(entries, feed, plan, today=date(2026, 8, 27))
        row = next(e for e in out if e["Ticker"] == "FI")
        assert row["Held"] == "Y", "the position must survive untouched"
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


def test_an_ORDINARY_REBALANCE_still_runs_though_its_demotion_waits(tmp_path):
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
        assert plan.not_in_universe == ["TSLA"]
        # The run is NOT blocked -- that was the round-3 over-guard. The sale is
        # merely deferred one run, and named, because an uncovered holding means
        # we cannot tell a sale from a re-spelling.
        assert plan.blocked_reason is None
        assert plan.withheld_demotions == ["AAPL"]
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


def test_a_PARTIAL_alias_failure_WITHHOLDS_THE_FIGURES(tmp_path):
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
        assert plan.demotions == [] and plan.refreshed == []
        assert plan.withheld_refreshes == ["FI"]
        out = held_mod.apply_plan(entries, feed, plan, today=date(2026, 8, 27))
        row = next(e for e in out if e["Ticker"] == "FI")
        # Not a fabricated sale -- corruption: the position survives with the WRONG
        # count. Writing 10 would discard two thirds of the holding and one
        # broker's cost basis.
        # The fixture sets no Average Cost, so the assertion is on what it DOES set:
        # the share count survives untouched rather than being overwritten with 10.
        assert row["Shares"] == "30"
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


def test_the_validated_map_is_the_one_that_was_APPLIED(tmp_path, monkeypatch):
    """Codex round 5: the check re-LOADED the store while `load_feed` had already
    normalised the feed with the import-time map.

    Change the file in between -- Dropbox sync, a concurrent edit -- and it
    validated bytes that never touched the data, then allowed the very swap it
    exists to stop. The map passed to the validator must be `SYMBOL_ALIASES`.
    """
    import universe.aliases as aliases_mod

    seen = {}
    real = aliases_mod.merge_hazards_for_map

    def spy(alias_map, universe_tickers, names=None):
        seen["map"] = dict(alias_map)
        return real(alias_map, universe_tickers, names)

    monkeypatch.setattr(aliases_mod, "merge_hazards_for_map", spy)
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update({"ZZZ": "AAPL"})
        feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL",))))
        held_mod.plan_sync(_entries({"AAPL": ("Portfolio", "Y")}), feed,
                           universe_tickers=["AAPL"])
        assert seen.get("map") == {"ZZZ": "AAPL"}, (
            "the validator must see the map load_feed applied, not a fresh file read")
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_a_confirmed_hazard_is_never_erased_by_a_later_failure(tmp_path, monkeypatch):
    """Codex round 5: the `except` around the validators set `problems = []`.

    So a hazard `merge_hazards` had ALREADY confirmed was discarded when a later
    advisory call raised, and the plan proceeded to rewrite ownership onto the
    wrong company. A fatal, once found, is never unfound -- which is why the
    hazard check now stands alone rather than sharing a try block with anything.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update({"FISV": "FI"})
        feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("FISV",))))
        entries = _entries({"FISV": ("Portfolio", "Y"), "FI": ("Researching", "")})
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["FI", "FISV"])
        assert plan.blocked_reason, "the confirmed merge hazard must survive"
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


# ── Codex round 5 (2026-08-27) ──────────────────────────────────────────────

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_share_count_is_REFUSED(tmp_path, bad):
    """NaN does not merely produce odd output -- it DISABLES the guards.

    Every comparison against NaN is False, so a row with `shares: NaN` sailed
    through load_feed, never matched the split heuristic, and let a real position
    be written out as sold.
    """
    payload = _feed_payload(tickers=())
    payload["held"] = [{"ticker": "NEWCO", "shares": bad, "avg_cost": 1.0, "brokers": ["IBKR"]}]
    with pytest.raises(held_mod.HeldFeedError, match="non-finite share count"):
        held_mod.load_feed(_write_feed(tmp_path, payload))


def test_a_non_finite_feed_age_is_REFUSED(tmp_path):
    """`NaN > limit` is False, so a NaN age reported the feed as FRESH."""
    payload = _feed_payload(tickers=("AAPL",))
    payload["stalest_age_days"] = float("nan")
    with pytest.raises(held_mod.HeldFeedError, match="non-finite stalest_age_days"):
        held_mod.load_feed(_write_feed(tmp_path, payload))


def test_a_split_across_TWO_unjoined_symbols_is_still_caught(tmp_path):
    """Now covered by the withhold rule rather than by subset arithmetic -- which
    is the point: the rule no longer depends on the share counts lining up."""
    """Single-row matching was evadable by arithmetic: a 30-share position
    arriving as unjoined 10 + 20 matched neither and went through as a sale."""
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        payload = _feed_payload(tickers=())
        payload["held"] = [
            {"ticker": "FISV", "shares": 10.0, "avg_cost": 50.0, "brokers": ["IBKR"]},
            {"ticker": "FISVA", "shares": 20.0, "avg_cost": 50.0, "brokers": ["IBKR"]},
        ]
        feed = held_mod.load_feed(_write_feed(tmp_path, payload))
        entries = _entries({"FI": ("Portfolio", "Y")})
        entries[0]["Shares"] = "30"
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["FI"])
        assert plan.demotions == []
        assert plan.withheld_demotions == ["FI"]
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_the_subset_search_is_capped_and_SAYS_SO(caplog):
    """Capping only the subset SIZE left O(n^3) in the candidate count and took
    this suite from 30s to 888s -- a guard that can hang the lane it protects.

    Above the cap, single-symbol matching still runs and the skip is logged; it is
    never silently narrowed.
    """
    many = {f"T{i}": float(i + 1) for i in range(held_mod.MAX_SPLIT_CANDIDATES + 5)}
    with caplog.at_level("WARNING"):
        assert held_mod._subset_summing_to(many, 999999.0) == []
    assert any("exceeds the" in r.message for r in caplog.records)
    # ...and a single-symbol match above the cap still works
    assert held_mod._subset_summing_to(many, 3.0) == ["T2"]


# ── Fable review (2026-08-27) ────────────────────────────────────────────────

def test_a_DRIP_sized_share_drift_no_longer_fabricates_a_sale(tmp_path):
    """The finding that retired the share-count heuristic.

    Its premise was "one position seen twice carries the SAME count". Fable's
    counter: a symbol change and a share-count change CO-OCCUR at a corporate
    action -- which is exactly when no alias entry exists yet. A 3-share DRIP made
    the numbers unequal, the match missed, and Held=N / Shares cleared / Held Until
    stamped was WRITTEN for a real position.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        payload = _feed_payload(tickers=())
        payload["held"] = [{"ticker": "FISV", "shares": 103.0, "avg_cost": 50.0,
                            "brokers": ["IBKR"]}]
        feed = held_mod.load_feed(_write_feed(tmp_path, payload))
        entries = _entries({"FI": ("Portfolio", "Y")})
        entries[0]["Shares"] = "100"          # 100 -> 103: a dividend reinvestment
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["FI"])
        assert plan.withheld_demotions == ["FI"]
        out = held_mod.apply_plan(entries, feed, plan, today=date(2026, 8, 27))
        row = next(e for e in out if e["Ticker"] == "FI")
        assert row["Held"] == "Y" and not row["Held Until"]
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_a_promotion_carrying_a_demotions_shares_withholds_it(tmp_path):
    """Fable: the universe can hold TWO rows for one issuer with no alias linking
    them (discovery auto-adds `FISV` while `FI` is covered).

    Then the feed's FISV joins a real row, so there are ZERO unjoined holdings --
    the other rule cannot see it -- and FI is demoted, promoting FISV. Exit 0, no
    warning, a real position marked sold.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        payload = _feed_payload(tickers=())
        payload["held"] = [{"ticker": "FISV", "shares": 100.0, "avg_cost": 50.0,
                            "brokers": ["IBKR"]}]
        feed = held_mod.load_feed(_write_feed(tmp_path, payload))
        entries = _entries({"FI": ("Portfolio", "Y"), "FISV": ("Researching", "")})
        entries[0]["Shares"] = "100"
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["FI", "FISV"])
        assert plan.not_in_universe == [], "the other rule is blind here by construction"
        assert plan.promotions == ["FISV"]
        assert plan.withheld_demotions == ["FI"]
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_an_ordinary_week_is_never_withheld(tmp_path):
    """The fourth over-guard check: nothing unjoined, no twin promotion, so a real
    sale applies immediately."""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(tickers=("AAPL",))))
    entries = _entries({"AAPL": ("Portfolio", "Y"), "MRNA": ("Portfolio", "Y")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers=["AAPL", "MRNA"])
    assert plan.demotions == ["MRNA"]
    assert plan.withheld_demotions == [] and plan.withheld_refreshes == []


# ── Codex round 6 (2026-08-27) ──────────────────────────────────────────────

def test_a_withheld_DEMOTION_keeps_its_figures(tmp_path):
    """I fixed this erasure for withheld REFRESHES and shipped the identical bug
    for withheld DEMOTIONS in the same commit -- whose message claimed "a withheld
    row is left untouched".

    A withheld demotion has no feed row and was removed from `plan.demotions`, so
    it fell through to the generic not-in-feed branch: Held stayed Y, Shares and
    Average Cost went blank.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        payload = _feed_payload(tickers=())
        payload["held"] = [{"ticker": "NEW", "shares": 100.0, "avg_cost": 50.0,
                            "brokers": ["IBKR"]}]
        feed = held_mod.load_feed(_write_feed(tmp_path, payload))
        entries = _entries({"OLD": ("Portfolio", "Y")})
        entries[0]["Shares"] = "100"
        entries[0]["Average Cost"] = "42"
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["OLD"])
        assert plan.withheld_demotions == ["OLD"]
        out = held_mod.apply_plan(entries, feed, plan, today=date(2026, 8, 27))
        row = next(e for e in out if e["Ticker"] == "OLD")
        assert row["Shares"] == "100" and row["Average Cost"] == "42"
        assert row["Held"] == "Y"
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_a_COVERED_rename_with_unequal_shares_is_withheld(tmp_path):
    """The disproved share-equality premise was still gating the covered-row path.

    A corporate action producing covered NEW at 103 against OLD at 100 slipped
    through the twin check and OLD was stamped sold -- the counts differ at exactly
    the event that renames a symbol, which is the whole reason the heuristic died.
    """
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        payload = _feed_payload(tickers=())
        payload["held"] = [{"ticker": "NEW", "shares": 103.0, "avg_cost": 50.0,
                            "brokers": ["IBKR"]}]
        feed = held_mod.load_feed(_write_feed(tmp_path, payload))
        entries = _entries({"OLD": ("Portfolio", "Y"), "NEW": ("Researching", "")})
        entries[0]["Shares"] = "100"
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["OLD", "NEW"])
        assert plan.not_in_universe == [], "no unjoined row -- the other rule is blind"
        assert plan.demotions == [] and plan.withheld_demotions == ["OLD"]
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_a_refresh_that_GROWS_is_withheld_too(tmp_path):
    """Requiring `recorded > now` kept the same share-direction assumption the
    redesign rejects: a joined leg at 35 against a stored 30 with an unjoined 20
    wrote 35, discarding 20 shares and their basis."""
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        payload = _feed_payload(tickers=())
        payload["held"] = [
            {"ticker": "OLD", "shares": 35.0, "avg_cost": 50.0, "brokers": ["Fid"]},
            {"ticker": "NEWX", "shares": 20.0, "avg_cost": 60.0, "brokers": ["IBKR"]},
        ]
        feed = held_mod.load_feed(_write_feed(tmp_path, payload))
        entries = _entries({"OLD": ("Portfolio", "Y")})
        entries[0]["Shares"] = "30"
        plan = held_mod.plan_sync(entries, feed, universe_tickers=["OLD"])
        assert plan.withheld_refreshes == ["OLD"]
        out = held_mod.apply_plan(entries, feed, plan, today=date(2026, 8, 27))
        assert next(e for e in out if e["Ticker"] == "OLD")["Shares"] == "30"
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


def test_accept_partial_join_is_the_RELEASE_from_an_indefinite_defer(tmp_path):
    """Without it a single persistently-uncovered holding defers every real sale
    forever, and exit 2 is a repeated warning rather than a mechanism -- recreating
    the stale-held failure this module exists to prevent."""
    monkeypatched = dict(held_mod.SYMBOL_ALIASES)
    try:
        held_mod.SYMBOL_ALIASES.clear()
        payload = _feed_payload(tickers=())
        payload["held"] = [{"ticker": "UNLISTED", "shares": 5.0, "avg_cost": 1.0,
                            "brokers": ["IBKR"]}]
        feed = held_mod.load_feed(_write_feed(tmp_path, payload))
        entries = _entries({"AAPL": ("Portfolio", "Y")})
        entries[0]["Shares"] = "10"

        held_back = held_mod.plan_sync(entries, feed, universe_tickers=["AAPL"])
        assert held_back.withheld_demotions == ["AAPL"]

        released = held_mod.plan_sync(entries, feed, universe_tickers=["AAPL"],
                                      accept_partial_join=True)
        assert released.demotions == ["AAPL"]
        assert released.withheld_demotions == []
        assert released.accepted_partial_join is True
    finally:
        held_mod.SYMBOL_ALIASES.clear()
        held_mod.SYMBOL_ALIASES.update(monkeypatched)


# ---------------------------------------------------------------------------
# Board #347 — a broker holding with no positions row
#
# `plan_sync` iterates the EXISTING entries, so a feed holding with no row was never
# promoted. `not_in_universe` does not catch it either: that check compares against
# the UNIVERSE, and a freshly bought name IS in the universe. The gap between the two
# checks is exactly a new purchase.
#
# Reproduced from the row verbatim: positions holds AAPL; the feed carries AAPL and a
# newly bought MSFT; the universe carries both. Originally `promotions=[]`,
# `not_in_universe=[]`, `blocked_reason=None`, and the fleet published a book missing
# a real position on a green run. 2026-09-15..25 it was reported and exited 2.
#
# JP 2026-09-26: "once you see it's in my portfolio shouldn't you just update it
# automatically" -> "yes". So the row is now CREATED and promoted, named per ticker.
# ---------------------------------------------------------------------------

def _universe_csv(tmp_path, tickers):
    """A throwaway universe carrying the metadata `positions.validate` requires."""
    import csv
    uni = tmp_path / "universe.csv"
    with open(uni, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Ticker", "Company Name", "Sector (JP)",
                                          "Subsector (JP)", "Currency", "Exchange"])
        w.writeheader()
        for t in tickers:
            w.writerow({"Ticker": t, "Company Name": t, "Sector (JP)": "Tech",
                        "Subsector (JP)": "", "Currency": "USD", "Exchange": "NASDAQ"})
    return uni


def test_a_held_covered_name_with_no_positions_row_is_AUTO_ADDED(tmp_path):
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "MSFT"))))
    entries = _entries({"AAPL": ("Portfolio", "Y")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers={"AAPL", "MSFT"})

    assert plan.auto_added == ["MSFT"]
    assert plan.held_without_row == []          # created, so not also "reported"
    # the pre-existing lists are untouched by it
    assert plan.promotions == []
    assert plan.refreshed == ["AAPL"]
    assert plan.not_in_universe == []
    assert plan.blocked_reason is None


def test_the_auto_added_row_ENDS_HELD_with_the_feeds_figures_and_no_intent(tmp_path):
    """The MSFT repro, applied: a row is created and the ordinary promotion sets Held.
    No intent flag -- the shape of every held row in the real book -- so its published
    `Position` is `Portfolio`, and its provenance is written where a reader of the file
    will see it."""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "MSFT"))))
    entries = _entries({"AAPL": ("Portfolio", "Y")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers={"AAPL", "MSFT"})
    out = held_mod.apply_plan(entries, feed, plan, today=date(2026, 9, 26))

    msft = next(e for e in out if e["Ticker"] == "MSFT")
    assert msft["Held"] == "Y"
    assert msft["Held As Of"] == "2026-08-22"
    assert msft["Shares"] == 10.5 and msft["Average Cost"] == 100.25
    assert msft["Position Date"] == "2026-09-26"
    assert not any(pos.has_state(msft, f) for f in pos.STATE_FLAGS)
    assert pos.published_position(msft) == "Portfolio"
    assert "auto-added by sync-held 2026-09-26" in msft["Notes"]
    assert "IBKR" in msft["Notes"]
    assert entries == _entries({"AAPL": ("Portfolio", "Y")})    # input not mutated

    # Round-trips through the real file and passes the real validator.
    path = tmp_path / "positions.csv"
    pos.save(out, path)
    reloaded = {e["Ticker"]: e for e in pos.load(path)}
    assert reloaded["MSFT"]["Held"] == "Y"
    assert reloaded["MSFT"]["Position"] == "Portfolio"
    errors, _ = pos.validate(list(reloaded.values()),
                             universe_csv_path=_universe_csv(tmp_path, ("AAPL", "MSFT")))
    assert errors == []

    # Idempotent: the next run sees an ordinary held row and creates nothing.
    again = held_mod.plan_sync(list(reloaded.values()), feed,
                               universe_tickers={"AAPL", "MSFT"})
    assert again.auto_added == [] and sorted(again.refreshed) == ["AAPL", "MSFT"]


def test_auto_add_leaves_every_OTHER_plan_output_and_row_identical(tmp_path):
    """Adding a covered rowless holding to the feed must change nothing but the new
    row: same promotions, refreshes, demotions and withholds, and every pre-existing
    row written byte-for-byte as it would be without the purchase."""
    entries = _entries({"AAPL": ("Portfolio", "Y"), "SGRY": ("Researching", ""),
                        "ZZZZ": ("Researching", "")})
    uni = {"AAPL", "SGRY", "ZZZZ", "MSFT"}
    base_feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "SGRY"))))
    base = held_mod.plan_sync(entries, base_feed, universe_tickers=uni)
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "SGRY", "MSFT"))))
    plan = held_mod.plan_sync(entries, feed, universe_tickers=uni)

    assert plan.auto_added == ["MSFT"] and base.auto_added == []
    for name in ("promotions", "demotions", "refreshed", "not_in_universe",
                 "held_without_row", "withheld_demotions", "withheld_refreshes",
                 "withheld_reason", "blocked_reason", "feed_as_of"):
        assert getattr(plan, name) == getattr(base, name), name

    today = date(2026, 9, 26)
    with_new = held_mod.apply_plan(entries, feed, plan, today=today)
    without = held_mod.apply_plan(entries, base_feed, base, today=today)
    assert [e for e in with_new if e["Ticker"] != "MSFT"] == without


def test_a_STALE_plan_cannot_mint_a_second_row(tmp_path):
    """`apply_plan` re-checks presence: a plan computed before someone added the row
    (or applied twice) must not write two rows for one ticker -- `positions.validate`
    calls a duplicate an ERROR, and every export would then carry the name twice."""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "MSFT"))))
    plan = held_mod.plan_sync(_entries({"AAPL": ("Portfolio", "Y")}), feed,
                              universe_tickers={"AAPL", "MSFT"})
    assert plan.auto_added == ["MSFT"]
    now = _entries({"AAPL": ("Portfolio", "Y"), "MSFT": ("Researching", "")})
    out = held_mod.apply_plan(now, feed, plan, today=date(2026, 9, 26))
    assert sorted(e["Ticker"] for e in out) == ["AAPL", "MSFT"]


def test_a_name_outside_the_universe_is_still_REPORTED_not_created(tmp_path):
    """Unchanged behaviour: CM owns the follow-list, so a holding it does not cover
    is named, not added -- it needs a sector and a human decision."""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "ZZZZ"))))
    entries = _entries({"AAPL": ("Portfolio", "Y")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers={"AAPL"})
    assert plan.not_in_universe == ["ZZZZ"]
    assert plan.auto_added == [] and plan.held_without_row == []
    out = held_mod.apply_plan(entries, feed, plan, today=date(2026, 9, 26))
    assert [e["Ticker"] for e in out] == ["AAPL"]
    assert any("HELD BUT NOT IN UNIVERSE" in line and "ZZZZ" in line
               for line in plan.summary_lines())


def test_no_universe_passed_means_no_claim_and_no_row(tmp_path):
    """Without `universe_tickers` we cannot tell covered from uncovered, so we neither
    report nor create -- the same discipline `not_in_universe` already follows."""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "MSFT"))))
    plan = held_mod.plan_sync(_entries({"AAPL": ("Portfolio", "Y")}), feed)
    assert plan.auto_added == [] and plan.held_without_row == []
    assert plan.not_in_universe == []


def test_an_existing_row_is_promoted_not_auto_added(tmp_path):
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "MSFT"))))
    entries = _entries({"AAPL": ("Portfolio", "Y"), "MSFT": ("Researching", "")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers={"AAPL", "MSFT"})
    assert plan.auto_added == []
    assert plan.promotions == ["MSFT"]


def test_the_auto_added_row_takes_the_UNIVERSE_spelling(tmp_path):
    """The feed is upper-cased; the universe is the authority on spelling, and
    `positions.validate` joins verbatim, so the created row must use the universe's."""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "BRK.B"))))
    plan = held_mod.plan_sync(_entries({"AAPL": ("Portfolio", "Y")}), feed,
                              universe_tickers={"AAPL", "brk.b"})
    assert plan.auto_added == ["brk.b"]
    out = held_mod.apply_plan(_entries({"AAPL": ("Portfolio", "Y")}), feed, plan,
                              today=date(2026, 9, 26))
    row = next(e for e in out if e["Ticker"] == "brk.b")
    assert row["Held"] == "Y"


def test_an_auto_add_coinciding_with_a_demotion_WITHHOLDS_it(tmp_path):
    """A covered name joining Held while another leaves is the shape of a rename. A
    promoted row already triggers the withhold; an auto-added one must too, or a
    covered rename OLD -> NEW would stamp OLD sold while creating NEW beside it."""
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "NEW"))))
    entries = _entries({"AAPL": ("Portfolio", "Y"), "OLD": ("Portfolio", "Y")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers={"AAPL", "OLD", "NEW"})
    assert plan.auto_added == ["NEW"]
    assert plan.demotions == [] and plan.withheld_demotions == ["OLD"]
    assert "NEW" in plan.withheld_reason


def test_a_withheld_mass_demotion_still_TRIPS_the_circuit_breaker(tmp_path):
    """A fresh, well-formed, WRONG feed carrying one covered new name and none of the
    real holdings: the auto-add is a promotion, so the demotions are withheld -- and a
    breaker that counted only the un-withheld list saw zero and let the run write
    (Codex, #347 round 2). Every name the feed says left Held must count."""
    n = held_mod.MAX_DEMOTIONS_PER_RUN + 1
    old = [f"O{i:02d}" for i in range(n)]
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("NEW",))))
    entries = _entries({t: ("Portfolio", "Y") for t in old})
    plan = held_mod.plan_sync(entries, feed, universe_tickers={"NEW", *old})
    assert plan.is_blocked
    assert f"{n} names would leave Held" in plan.blocked_reason
    with pytest.raises(held_mod.HeldFeedError):
        held_mod.apply_plan(entries, feed, plan)

    # The same with an EXISTING row promoted instead of an auto-add (pre-existing path).
    entries2 = entries + _entries({"NEW": ("Researching", "")})
    assert held_mod.plan_sync(entries2, feed, universe_tickers={"NEW", *old}).is_blocked


def test_metadata_incomplete_names_are_reported_not_created(tmp_path):
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "MSFT", "NVDA"))))
    plan = held_mod.plan_sync(_entries({"AAPL": ("Portfolio", "Y")}), feed,
                              universe_tickers={"AAPL", "MSFT", "NVDA"},
                              metadata_incomplete={"NVDA"})
    assert plan.auto_added == ["MSFT"]
    assert plan.held_without_row == ["NVDA"]


def test_a_REPORTED_rowless_name_still_withholds_a_rename_and_a_split(tmp_path):
    """A covered rowless name this run does NOT create (here: incomplete universe
    metadata) did not join the book, so it can be the other half of a rename or a
    split. It must withhold a coinciding demotion and a moving refresh exactly as an
    uncovered holding does (Codex, #347 round 3)."""
    # rename: OLD held, feed now says NEW (covered, rowless, not creatable)
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", "NEW"))))
    entries = _entries({"AAPL": ("Portfolio", "Y"), "OLD": ("Portfolio", "Y")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers={"AAPL", "OLD", "NEW"},
                              metadata_incomplete={"NEW"})
    assert plan.held_without_row == ["NEW"] and plan.auto_added == []
    assert plan.demotions == [] and plan.withheld_demotions == ["OLD"]

    # split: OLD 100 recorded, feed OLD 80 + NEW 20
    payload = _feed_payload(("OLD", "NEW"))
    payload["held"][0]["shares"] = 80.0
    feed2 = held_mod.load_feed(_write_feed(tmp_path, payload))
    entries2 = _entries({"OLD": ("Portfolio", "Y")})
    entries2[0]["Shares"] = 100.0
    plan2 = held_mod.plan_sync(entries2, feed2, universe_tickers={"OLD", "NEW"},
                               metadata_incomplete={"NEW"})
    assert plan2.withheld_refreshes == ["OLD"] and plan2.refreshed == []

    # ...and the operator's release really releases the figures (Codex, #347 r4):
    # before, `--accept-partial-join` left them in `withheld_refreshes`, which
    # `apply_plan` skips, so the stale 100 stood for ever and the run exited 2.
    released = held_mod.plan_sync(entries2, feed2, universe_tickers={"OLD", "NEW"},
                                  metadata_incomplete={"NEW"}, accept_partial_join=True)
    assert released.withheld_refreshes == [] and released.refreshed == ["OLD"]
    out = held_mod.apply_plan(entries2, feed2, released, today=date(2026, 9, 26))
    assert out[0]["Shares"] == 80.0


def test_a_NEGATIVE_share_count_aborts_before_writing(tmp_path):
    """`positions.load` rejects a negative share count, so writing one leaves a book
    nothing can read; the publisher never emits one, so it is a broken feed
    (Codex, #347 round 6)."""
    payload = _feed_payload(("AAPL", "MSFT"))
    payload["held"][1]["shares"] = -5.0
    with pytest.raises(held_mod.HeldFeedError, match="nothing has been written"):
        held_mod.load_feed(_write_feed(tmp_path, payload))


@pytest.mark.parametrize("cost", [0.0, -1.0])
def test_a_ZERO_basis_is_unknown_basis_and_the_book_stays_readable(tmp_path, cost):
    """portfolio_daily publishes avg_cost=0.0 for a holding with no known basis. That
    must not abort the sync (a guard becoming the outage, Codex #347 round 7), and it
    must not be written as 0 either (`positions.load` would then refuse the whole
    book). It is recorded as unknown, end to end through a real save and load."""
    payload = _feed_payload(("AAPL", "MSFT"))
    payload["held"][1]["avg_cost"] = cost
    feed = held_mod.load_feed(_write_feed(tmp_path, payload))
    assert feed.rows["MSFT"].avg_cost is None
    entries = _entries({"AAPL": ("Portfolio", "Y")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers={"AAPL", "MSFT"})
    path = tmp_path / "positions.csv"
    pos.save(held_mod.apply_plan(entries, feed, plan, today=date(2026, 9, 26)), path)
    msft = next(e for e in pos.load(path) if e["Ticker"] == "MSFT")
    assert msft["Held"] == "Y" and msft["Average Cost"] is None


def test_more_than_the_cap_are_REPORTED_and_none_created(tmp_path):
    """A feed carrying a dozen covered names the book never held is a publisher
    fault, not a week of purchases. Creating the first N would be an arbitrary partial
    book, so none are created and all fall back to the report."""
    n = held_mod.MAX_AUTO_ADDS_PER_RUN + 1
    new = [f"N{i:02d}" for i in range(n)]
    feed = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", *new))))
    entries = _entries({"AAPL": ("Portfolio", "Y")})
    plan = held_mod.plan_sync(entries, feed, universe_tickers={"AAPL", *new})
    assert plan.auto_added == []
    assert plan.held_without_row == sorted(new)
    out = held_mod.apply_plan(entries, feed, plan, today=date(2026, 9, 26))
    assert [e["Ticker"] for e in out] == ["AAPL"]
    assert any("HELD, NO ROW" in line for line in plan.summary_lines())

    # An incomplete-metadata name still counts toward the cap: cap-many creatable
    # names plus one that is not must create NOTHING (Codex, #347 round 5).
    at_cap = new[:held_mod.MAX_AUTO_ADDS_PER_RUN]
    plan3 = held_mod.plan_sync(entries, feed, universe_tickers={"AAPL", *new},
                               metadata_incomplete={new[-1]})
    assert plan3.auto_added == [] and plan3.held_without_row == sorted(new)

    # ...and exactly at the cap, all are created.
    at_cap = new[:held_mod.MAX_AUTO_ADDS_PER_RUN]
    feed2 = held_mod.load_feed(_write_feed(tmp_path, _feed_payload(("AAPL", *at_cap))))
    plan2 = held_mod.plan_sync(entries, feed2, universe_tickers={"AAPL", *at_cap})
    assert plan2.auto_added == sorted(at_cap) and plan2.held_without_row == []


def _run_cli(monkeypatch, capsys, argv):
    """Run `cli.py` exactly as the shell does -- through its `__main__` block -- and
    return (exit code, stdout). Going through `__main__` is the point: the return
    value of `main()` is not the exit code unless that block passes it on, and for
    months it did not."""
    import runpy

    import logging_utils

    cli_path = Path(__file__).resolve().parent.parent / "cli.py"
    monkeypatch.setattr(sys, "argv", [str(cli_path)] + argv)
    # `configure_logging` does `basicConfig(force=True)` onto the CURRENT stdout,
    # which here is capsys's temporary stream: it would strip pytest's handlers and
    # leave the root logger writing to a closed file for every later test.
    monkeypatch.setattr(logging_utils, "configure_logging", lambda **_: None)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(cli_path), run_name="__main__")
    code = exc.value.code
    return (0 if code is None else code), capsys.readouterr().out


def _cli_book(tmp_path, monkeypatch, feed_tickers, universe, incomplete=()):
    book_path = tmp_path / "positions.csv"
    pos.save(_entries({"AAPL": ("Researching", "Y")}), book_path)
    feed_path = _write_feed(tmp_path, _feed_payload(feed_tickers))
    monkeypatch.setattr(pos, "POSITIONS_PATH", book_path)
    rows = {t: {"Ticker": t, "Company Name": t, "Sector (JP)": "Tech",
                "Currency": "USD", "Exchange": "" if t in incomplete else "NASDAQ"}
            for t in universe}
    monkeypatch.setattr(pos, "_load_universe_rows", lambda *a, **k: dict(rows))
    return book_path, feed_path


def test_a_universe_row_MISSING_METADATA_is_reported_not_auto_added(tmp_path, monkeypatch, capsys):
    """`catalyst_watch` gates the whole positions export on `validation_passed`, and
    `positions.validate` errors on a row whose universe entry lacks Company Name /
    Sector / Currency / Exchange. Auto-adding one would drop every name from that
    lane, so it falls back to the report (Codex, #347 round 2)."""
    book_path, feed_path = _cli_book(tmp_path, monkeypatch, ("AAPL", "MSFT", "NVDA"),
                                     {"AAPL", "MSFT", "NVDA"}, incomplete={"NVDA"})
    code, out = _run_cli(monkeypatch, capsys,
                         ["positions", "sync-held", "--feed", str(feed_path)])
    assert code == 2
    assert "auto-added MSFT - held at IBKR" in out
    assert "auto-added NVDA" not in out
    assert "HELD, NO ROW" in out and "NVDA" in out and "lacks Company Name" in out
    assert [e["Ticker"] for e in pos.load(book_path)] == ["AAPL", "MSFT"]


def test_sync_held_AUTO_ADDS_names_it_and_exits_0(tmp_path, monkeypatch, capsys):
    """End to end through the real CLI, on temp files only: the MSFT repro ends with
    MSFT in `Held`, a per-ticker line saying so, and a clean exit."""
    real_positions = pos.POSITIONS_PATH
    real_bytes = real_positions.read_bytes() if real_positions.exists() else None
    book_path, feed_path = _cli_book(tmp_path, monkeypatch, ("AAPL", "MSFT"),
                                     {"AAPL", "MSFT"})

    code, out = _run_cli(monkeypatch, capsys,
                         ["positions", "sync-held", "--feed", str(feed_path)])

    assert code == 0, out
    assert "auto-added MSFT - held at IBKR, no coverage row existed" in out
    assert "auto-add row" in out and "HELD, NO ROW" not in out
    held = {e["Ticker"]: e["Held"] for e in pos.load(book_path)}
    assert held == {"AAPL": "Y", "MSFT": "Y"}

    # A second run is ordinary: nothing created, nothing announced.
    code2, out2 = _run_cli(monkeypatch, capsys,
                           ["positions", "sync-held", "--feed", str(feed_path)])
    assert code2 == 0 and "auto-add" not in out2
    assert [e["Ticker"] for e in pos.load(book_path)] == ["AAPL", "MSFT"]

    # Assert the negative: the production book was never the file written.
    if real_bytes is not None:
        assert real_positions.read_bytes() == real_bytes


def test_dry_run_SHOWS_the_auto_add_and_writes_NOTHING(tmp_path, monkeypatch, capsys):
    book_path, feed_path = _cli_book(tmp_path, monkeypatch, ("AAPL", "MSFT"),
                                     {"AAPL", "MSFT"})
    before = book_path.read_bytes()
    code, out = _run_cli(monkeypatch, capsys,
                         ["positions", "sync-held", "--dry-run", "--feed", str(feed_path)])
    assert code == 0
    assert "would auto-add MSFT - held at IBKR, no coverage row existed" in out
    assert "[dry run] nothing written" in out
    assert "auto-added MSFT" not in out
    assert book_path.read_bytes() == before


def test_sync_held_not_in_universe_is_UNCHANGED_exit_2_no_row(tmp_path, monkeypatch, capsys):
    book_path, feed_path = _cli_book(tmp_path, monkeypatch, ("AAPL", "ZZZZ"), {"AAPL"})
    code, out = _run_cli(monkeypatch, capsys,
                         ["positions", "sync-held", "--feed", str(feed_path)])
    assert code == 2
    assert "HELD BUT NOT IN UNIVERSE" in out and "ZZZZ" in out
    assert "auto-add" not in out
    assert [e["Ticker"] for e in pos.load(book_path)] == ["AAPL"]


def test_over_the_cap_the_cli_EXITS_2_and_its_remedy_parses(tmp_path, monkeypatch, capsys):
    """Over `MAX_AUTO_ADDS_PER_RUN` nothing is created and the old warning returns.
    Its remedy said `python cli.py pos add <TICKER> ...` once -- a subcommand that
    does not exist. Parse what is actually printed with the real parser, so the next
    rename of a subcommand breaks this test, not JP's paste."""
    import re
    import importlib.util
    import shlex

    new = [f"N{i:02d}" for i in range(held_mod.MAX_AUTO_ADDS_PER_RUN + 1)]
    book_path, feed_path = _cli_book(tmp_path, monkeypatch, ("AAPL", *new),
                                     {"AAPL", *new})
    code, out = _run_cli(monkeypatch, capsys,
                         ["positions", "sync-held", "--feed", str(feed_path)])
    assert code == 2
    assert "HELD, NO ROW" in out and "board #347" in out
    assert "auto-added N" not in out
    assert [e["Ticker"] for e in pos.load(book_path)] == ["AAPL"]

    m = re.search(r"`python cli\.py ([^`]+)`, <state> one of: ([^)]+)\)", out)
    assert m, f"no pasteable command + state list in the warning:\n{out}"
    template, states = m.group(1), [s.strip() for s in m.group(2).split(",")]
    assert states, "the warning named no valid state"

    spec = importlib.util.spec_from_file_location(
        "_cli_for_parse", Path(__file__).resolve().parent.parent / "cli.py")
    cli_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli_mod)

    # A throwaway universe carrying a SPACED ticker, so `positions.add` gets past the
    # membership check and its POSITION validation is what is under test.
    import csv
    uni = tmp_path / "universe_spaced.csv"
    with open(uni, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Ticker", "Company Name", "Sector (JP)",
                                          "Subsector (JP)", "Currency", "Exchange"])
        w.writeheader()
        w.writerow({"Ticker": "AMP IM", "Company Name": "Amplifon", "Sector (JP)": "MedTech",
                    "Subsector (JP)": "", "Currency": "EUR", "Exchange": "Borsa Italiana"})

    monkeypatch.undo()     # `pos.add` below must read the real (temp) universe file
    for state in states:
        argv = shlex.split(template.replace("<TICKER>", "AMP IM").replace("<state>", state))
        args = cli_mod.build_parser().parse_args(argv)      # SystemExit(2) if invalid
        assert (args.command, args.pos_command, args.ticker, args.position) == (
            "positions", "add", "AMP IM", state)
        # Parsing is not enough: the parser accepts `Portfolio`, which the handler
        # rejects. Run each named state through the function the CLI calls.
        target = tmp_path / f"add_{state.replace(' ', '_')}.csv"
        pos.add(args.ticker, position=args.position, path=target, universe_csv_path=uni)
        assert [e["Ticker"] for e in pos.load(target)] == ["AMP IM"]
