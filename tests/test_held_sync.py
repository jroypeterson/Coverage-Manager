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


def test_an_unreadable_alias_store_yields_an_empty_map_not_a_guess(tmp_path, monkeypatch):
    """Degrading to "no aliases" is safe ONLY because the demotion guard aborts.

    An unjoined holding reads as a sale, and `MAX_DEMOTIONS_PER_RUN` refuses to
    write one. Inventing an alias instead would merge two issuers with no trace.
    """
    bad = tmp_path / "ticker_aliases.json"
    bad.write_text("{not json", encoding="utf-8")
    import universe.aliases as aliases_mod

    monkeypatch.setattr(aliases_mod, "ALIASES_PATH", bad)
    assert held_mod._load_symbol_aliases() == {}


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
