"""Consume OWNERSHIP truth from portfolio_daily; derive the `Held` column.

JP, 2026-08-22: *"Coverage manager should own what stocks I want to follow but I
don't want it to own the record for what I own. I want it to consume something
else if it needs to know what I own or am researching or ready to buy."*

## What changed, conceptually

`Position` used to do two jobs: record a broker FACT (do I own this) and record a
JUDGEMENT (what do I intend). Two owners for one column, so neither was
authoritative and drift was structurally invisible — nothing in this repo ever
read a broker. Measured 2026-08-22, that cost: CM published 33 holdings while the
brokers held 30. ROIV had been liquidated on 2026-08-03 and was still publishing
as a holding **19 days later**, flowing into catalyst_watch, analyst-days,
sigma-alert, earnings_agent and the insider tearsheet.

Now the axes are separate:

  * `Position` — INTENT. Authored by JP through Notion. Coverage Manager owns the
    follow-list, which is what it is good at.
  * `Held` — FACT. Derived here from `portfolio_daily/exports/held.json`, which is
    published from the broker CSVs. Never typed by a human.

`portfolio.json` is then derived from `Held`, so its shape is unchanged and no
consumer needed editing.

## Why a sale lands on `Following for Interest`

JP's requirement (2026-08-22): a sold name stays in coverage and keeps getting
flagged for earnings and movers; it just stops claiming to be owned.

It landed on `Researching` for the first two days, and that was a CONSTRAINT
talking, not a judgement: `catalyst_watch`, `analyst-days` and `insider_ownership`
read only `portfolio.json` + `researching.json`, so any other state silently
dropped the name from three lanes.

JP corrected it on 2026-08-24 -- "RPD and U should default to Following for
Interest once I sell a stock" -- and he is right on the meaning. Once you have
sold something you are not building a thesis on it; you are interested in what it
says. That is his own definition of the state: "I would be interested in
particular in what they are saying on their earnings calls." `Researching`
described the plumbing, not the position.

**What it costs, stated because it is a real trade.** A sold name still reaches
transcripts, earnings_agent, sigma-alert and analyst-days (all four read every
state, the last since 2026-08-23) and NO LONGER reaches `catalyst_watch` or
`insider_ownership`, which still read the two files only. That is defensible --
forward catalysts and insider buying are questions about a position you might
take, not about a bellwether you read -- but it is a consequence, not a free
change. If either lane should carry these names, widen THAT lane rather than
mislabelling the position to sneak them in, which is what the first two days did.

The history goes into its own columns -- `Previously Held` and `Held Until` --
where it is preserved without deciding where the name flows.

## The guards are the point of this module

A sync that reads "no broker data" as "sold everything" would empty the book in one
run, and every downstream repo would faithfully act on it. So every abort below
changes NOTHING on disk, and the caller reports why:

  1. feed missing / unreadable / unknown schema version
  2. feed staler than `HELD_STALE_MAX_DAYS`
  3. feed contains zero holdings
  4. more names would leave `Held` in one run than `MAX_DEMOTIONS_PER_RUN`

(4) is the one that catches the failures the other three do not: a feed that is
present, fresh, well-formed and WRONG. A real day sells one or two names; thirty
means the publisher broke, and the circuit breaker turns a silent catastrophe into
a loud refusal.

A held ticker that is absent from the coverage universe is NOT fatal — it is
reported every run and never silently dropped. Today that fires on FISV, which is
the FI/FISV symbol split (board row #345): this sync doubles as that bug's detector.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from config import DATA_DIR
from logging_utils import get_logger

logger = get_logger("universe.held")

# portfolio_daily publishes this. Read cross-repo by local path, deliberately: it
# is a published artifact with a schema and an as_of, not a reach into that
# project's private `data/` directory.
DEFAULT_FEED_PATH = (
    Path(__file__).resolve().parent.parent.parent / "portfolio_daily" / "exports" / "held.json"
)

ACCEPTED_FEED_SCHEMAS = frozenset({1})

# Matches portfolio_daily.config.POSITIONS_STALE_WARN_DAYS. Its CSVs are refreshed
# by INTERACTIVE jobs (the IBKR connector and the Fidelity browser export cannot be
# reached headlessly), so the feed legitimately ages between sessions. Ten days is
# "stale enough that acting on it is guesswork", not "stale enough to be unusual".
HELD_STALE_MAX_DAYS = 10.0

# The circuit breaker. Chosen against the real book: 30 holdings, and the largest
# single-day change on record is the 2026-08-03 E*Trade liquidation (1 name) and
# the 2026-08-21 pair (2). Five leaves generous headroom for a genuine rebalance
# while still catching any failure that empties or halves the feed.
MAX_DEMOTIONS_PER_RUN = 5

# Where a sold name lands. It was `Researching` for two days because three
# consumers could not see anything else -- a constraint masquerading as a
# taxonomy. JP 2026-08-24: "RPD and U should default to Following for Interest
# once I sell a stock."
DEMOTION_POSITION = "Following for Interest"

# BROKER SYMBOL -> COVERAGE-UNIVERSE SYMBOL.
#
# This was a hardcoded one-entry dict until 2026-08-27, carrying a comment saying
# it was a stopgap for board row #345 and must not be grown. #345 landed, so the
# map now comes from `data/ticker_aliases.json` via `universe.aliases` -- the same
# store every other consumer reads, anchored to the identifiers that did not change
# (CIK 798354 / ISIN US3377381088 / composite FIGI BBG000BJKPG0 for Fiserv).
#
# Why it still exists at all: without a join, the very FIRST run of this sync
# reports that JP sold Fiserv, because the feed says FISV and the universe says FI
# and nothing connects them. A false SALE is exactly the error this module was
# written to eliminate, so shipping it as a side effect would be self-defeating.
#
# Loaded once at import, deliberately: this module runs as a single short-lived
# sync, and re-reading the file per row would let the map change mid-run -- which
# is how a half-aliased book gets written.
class AliasStoreUnavailable(Exception):
    """The alias store could not be read. Callers MUST abort without writing."""


#: Share counts are floats and brokers round differently (DRIP fractions, ADR
#: distributions), so the false-sale match below is a near-equality, not `==`.
#: Same tolerance `lots.py` uses for its own reconciliation.
SHARE_MATCH_TOLERANCE = 0.5

#: Largest group of unjoined holdings the split check will try to combine. One
#: issuer under two or three spellings is the real case; beyond that this stops
#: being a guard and starts being a subset-sum solver on operator data.
MAX_SPLIT_SUBSET = 3

#: ...and how many unjoined holdings may be considered at all. Capping only the
#: subset size still leaves O(n^3) in the number of candidates, which took this
#: repo's suite from 30s to 888s. Above this, single-symbol matching still runs
#: and the combination search is skipped WITH A WARNING.
MAX_SPLIT_CANDIDATES = 12


def _finite(value) -> bool:
    """A usable number: not None, not NaN, not infinite.

    ⛑ `float("nan")` and `float("inf")` survive every `float(...)` coercion in
    this module, and they do not merely produce odd output — they DISABLE the
    guards silently, because every comparison against NaN is False. A feed row
    with `shares: NaN` sailed through `load_feed`, `abs(NaN - lost)` never matched
    the split heuristic, and a real position was written out as sold; a
    `stalest_age_days: NaN` cleared the freshness check the same way, since
    `NaN > limit` is False. Both found by review, both on a path whose entire job
    is to refuse untrustworthy input.
    """
    if value is None:
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def _subset_summing_to(candidates: dict, target: float) -> list[str]:
    """Names of any subset of `candidates` whose shares sum to `target`. [] if none.

    Single-row matching was evadable by arithmetic — a 30-share position arriving
    as unjoined 10 + 20 matched neither and the demotion went through as a sale.

    ⛑ BOUNDED ON BOTH AXES, and the second bound was learned the hard way: the
    first version capped only the subset SIZE at 3, which is still O(n^3) in the
    number of unjoined holdings and took the test suite from 30 seconds to
    **888**. A guard that can hang the lane it protects is an outage with good
    intentions — the third time this feature produced one. Above
    `MAX_SPLIT_CANDIDATES` the combination search is skipped and SAID SO, never
    silently: single-row matching still runs, so the common case is unaffected
    and only the arithmetic-evasion case degrades.
    """
    from itertools import combinations

    names = sorted(candidates)
    for name in names:                       # single row: always, and cheap
        if abs(candidates[name] - target) <= SHARE_MATCH_TOLERANCE:
            return [name]

    if len(names) > MAX_SPLIT_CANDIDATES:
        logger.warning(
            "split check: %d unjoined holdings exceeds the %d-name cap, so only "
            "single-symbol matches were tested; a position split across several "
            "unjoined symbols would not be detected this run",
            len(names), MAX_SPLIT_CANDIDATES)
        return []

    for size in range(2, min(len(names), MAX_SPLIT_SUBSET) + 1):
        for combo in combinations(names, size):
            total = sum(candidates[n] for n in combo)
            if abs(total - target) <= SHARE_MATCH_TOLERANCE:
                return list(combo)
    return []


def _shares_of(entry) -> float | None:
    """A position row's share count as a float, or None when it is not recorded."""
    raw = str(entry.get("Shares", "") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _load_symbol_aliases() -> dict[str, str]:
    """Broker/vendor symbol -> universe ticker, from the published alias store.

    ⛑ AN UNREADABLE STORE RAISES. It must not degrade to an empty map, and the
    first version of this function did exactly that on the strength of a comment
    claiming `MAX_DEMOTIONS_PER_RUN` would abort the run. **It does not** — that
    guard fires above FIVE demotions and a single unjoined holding is one.
    Reproduced 2026-08-27 against these files: with the store corrupted, the feed's
    `FISV` fails to join the universe's `FI`, `plan_sync` returns
    `demotions=['FI']` with `blocked_reason=None`, and `apply_plan` writes
    `Held=N`, clears Shares and Average Cost, and stamps `Held Until` — a
    fabricated sale of a real position, which is the exact error this module was
    written to eliminate. The comment was a prose claim with nothing enforcing it.

    A MISSING file still yields an empty map, because that is a real and different
    fact: a fleet with no known symbol splits has no store, and every ticker then
    joins by its own name, which is the behaviour that predates the store. The
    danger is only in *losing* a mapping that existed, and the publish side guards
    that separately (`weekly_universe` refuses to overwrite a non-empty published
    alias export with an empty one).
    """
    try:
        from universe.aliases import AliasError, load_aliases
    except ImportError:
        return {}
    try:
        return {alias: entry["canonical"]
                for alias, entry in load_aliases()["by_alias"].items()}
    except AliasError as exc:
        raise AliasStoreUnavailable(
            f"ticker alias store unreadable ({exc}) - refusing to sync ownership. "
            f"Without it a split holding does not join its universe row, reads as a "
            f"sale, and is written as one: a single demotion is well under "
            f"MAX_DEMOTIONS_PER_RUN, so nothing downstream would stop it."
        ) from exc


#: Resolved at import so the map cannot change mid-run and write a half-aliased
#: book. An unreadable store therefore fails the import of any caller, which is
#: the intended blast radius: this module's whole job is to decide what is owned.
SYMBOL_ALIASES = _load_symbol_aliases()



# Named here rather than imported from `positions` to keep this module free of a
# circular import at load time; a test pins the two lists together so they cannot
# drift.
STATE_FLAGS_FOR_DEMOTION = ["Ready to Buy", "Ready to Short",
                            "Researching", "Following for Interest"]


class HeldFeedError(Exception):
    """The feed cannot be trusted. Callers MUST abort without writing."""


@dataclass
class HeldRow:
    ticker: str
    shares: float
    avg_cost: float | None
    brokers: list[str]


@dataclass
class HeldFeed:
    schema: int
    generated_at: str
    stalest_age_days: float | None
    rows: dict[str, HeldRow]           # keyed by UPPERCASE ticker

    @property
    def as_of(self) -> str:
        """Oldest broker as_of in the feed — the date this book is honest about."""
        return min((b for b in self._broker_dates if b), default="")

    _broker_dates: list[str] = field(default_factory=list)
    aliased: list[str] = field(default_factory=list)


@dataclass
class SyncPlan:
    """What the sync WOULD do. Pure data, so `--dry-run` and the real run share it."""
    promotions: list[str] = field(default_factory=list)     # not held -> held
    demotions: list[str] = field(default_factory=list)      # held -> not held
    refreshed: list[str] = field(default_factory=list)      # still held, figures updated
    not_in_universe: list[str] = field(default_factory=list)
    #: Demotions held back because the join could not be trusted this run. They are
    #: NOT applied and NOT a block -- see the withhold rule in `plan_sync`.
    withheld_demotions: list[str] = field(default_factory=list)
    #: Rows whose figures were NOT refreshed because they would have lost shares to
    #: a holding that did not join. The row keeps its prior, trusted figures.
    withheld_refreshes: list[str] = field(default_factory=list)
    withheld_reason: str = ""
    #: True when the operator passed --accept-partial-join and the withholds were
    #: deliberately released. Recorded so the run output says a human decided.
    accepted_partial_join: bool = False
    migrated_legacy: list[str] = field(default_factory=list)
    already_sold: list[str] = field(default_factory=list)
    feed_as_of: str = ""
    blocked_reason: str | None = None

    @property
    def is_blocked(self) -> bool:
        return self.blocked_reason is not None

    def summary_lines(self) -> list[str]:
        if self.is_blocked:
            return [f"BLOCKED — {self.blocked_reason}", "nothing written"]
        out = [
            f"feed as-of {self.feed_as_of}",
            f"promote to held : {len(self.promotions)}"
            + (f"  {', '.join(self.promotions)}" if self.promotions else ""),
            f"demote from held: {len(self.demotions)}"
            + (f"  {', '.join(self.demotions)}" if self.demotions else ""),
            f"refreshed       : {len(self.refreshed)}",
        ]
        if self.withheld_demotions or self.withheld_refreshes:
            out.append(
                f"WITHHELD         : {len(self.withheld_demotions)} demotion(s)"
                + (f" ({', '.join(self.withheld_demotions)})" if self.withheld_demotions else "")
                + f", {len(self.withheld_refreshes)} figure update(s)"
                + (f" ({', '.join(self.withheld_refreshes)})" if self.withheld_refreshes else "")
                + " - the join could not be trusted this run"
            )
        if self.migrated_legacy:
            out.append(
                f"one-time migration of the retired 'Portfolio' Position value: "
                f"{len(self.migrated_legacy)} row(s) -> {DEMOTION_POSITION}"
            )
        if self.already_sold:
            out.append(
                f"WAS Portfolio but absent from the broker feed -- recorded as "
                f"previously held, sale date UNKNOWN (not inferred): "
                f"{', '.join(self.already_sold)}"
            )
        if self.not_in_universe:
            out.append(
                f"HELD BUT NOT IN UNIVERSE: {', '.join(self.not_in_universe)} "
                f"— reported, never dropped; add to the universe or fix the symbol"
            )
        return out


def load_feed(path=None) -> HeldFeed:
    """Read and VALIDATE the ownership feed. Raises `HeldFeedError` on anything
    that would make acting on it a guess."""
    p = Path(DEFAULT_FEED_PATH if path is None else path)
    if not p.exists():
        raise HeldFeedError(
            f"ownership feed not found at {p} — run `python -m scripts.export_held` "
            f"in portfolio_daily (it also runs at the end of the daily digest)"
        )
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        raise HeldFeedError(f"ownership feed at {p} is unreadable: {exc}") from exc

    schema = payload.get("schema")
    if schema not in ACCEPTED_FEED_SCHEMAS:
        raise HeldFeedError(
            f"ownership feed schema {schema!r} not in accepted {sorted(ACCEPTED_FEED_SCHEMAS)} "
            f"— the publisher changed shape; update this consumer deliberately"
        )

    age = payload.get("stalest_age_days")
    if age is None:
        raise HeldFeedError("ownership feed carries no stalest_age_days — cannot judge freshness")
    if not _finite(age):
        # `NaN > limit` is False, so a NaN age reported the feed as FRESH.
        raise HeldFeedError(
            f"ownership feed carries a non-finite stalest_age_days ({age!r}) — it cannot "
            f"be judged fresh or stale, and comparing against it silently reports FRESH")
    if float(age) > HELD_STALE_MAX_DAYS:
        raise HeldFeedError(
            f"ownership feed is {float(age):.1f}d old (limit {HELD_STALE_MAX_DAYS:.0f}d) — "
            f"refresh the brokers first; acting on it would guess at sales"
        )

    raw_rows = payload.get("held") or []
    if not raw_rows:
        raise HeldFeedError(
            "ownership feed lists zero holdings — refusing to treat an empty book as "
            "'everything was sold'"
        )

    rows: dict[str, HeldRow] = {}
    aliased: list[str] = []
    merged: list[str] = []
    for r in raw_rows:
        t = str(r.get("ticker", "")).strip().upper()
        if not t:
            continue
        if t in SYMBOL_ALIASES:
            aliased.append(f"{t}->{SYMBOL_ALIASES[t]}")
            t = SYMBOL_ALIASES[t]
        if not _finite(r.get("shares") or 0.0):
            raise HeldFeedError(
                f"ownership feed row {t} carries a non-finite share count "
                f"({r.get('shares')!r}) — every comparison against it is False, so it "
                f"would DISABLE the guards below rather than trip them")
        if r.get("avg_cost") is not None and not _finite(r["avg_cost"]):
            raise HeldFeedError(
                f"ownership feed row {t} carries a non-finite average cost "
                f"({r.get('avg_cost')!r})")
        shares = float(r.get("shares") or 0.0)
        avg_cost = None if r.get("avg_cost") is None else float(r["avg_cost"])
        brokers = list(r.get("brokers") or [])
        prior = rows.get(t)
        if prior is None:
            rows[t] = HeldRow(ticker=t, shares=shares, avg_cost=avg_cost, brokers=brokers)
            continue
        # TWO FEED ROWS CAN NORMALIZE ONTO ONE TICKER, AND ASSIGNING WOULD EAT ONE.
        # The same issuer held at two brokers under two spellings (Fidelity `FI`,
        # IBKR `FISV`) arrives as two rows; `rows[t] = ...` kept whichever came
        # last, silently dropping the other broker's shares and cost basis, and
        # the survivor's broker list then understated where the position is held.
        # Merge instead: shares add, brokers union, and the cost basis is
        # SHARE-WEIGHTED because two lots at different prices have one blended
        # basis and picking either lot's price would misstate P&L on both.
        total = prior.shares + shares
        if prior.avg_cost is None or avg_cost is None:
            # A blend needs both sides. One missing basis makes the blend
            # unknowable, and inventing it from the half we have is worse than
            # admitting we do not know it.
            merged_cost = None
        elif total > 0:
            merged_cost = (prior.avg_cost * prior.shares + avg_cost * shares) / total
        else:
            merged_cost = None
        rows[t] = HeldRow(
            ticker=t,
            shares=total,
            avg_cost=merged_cost,
            brokers=sorted(set(prior.brokers) | set(brokers)),
        )
        merged.append(t)

    # ⛑ THE EMPTY-FEED GUARD CHECKED THE RAW LIST, NOT WHAT SURVIVED PARSING.
    #
    # Rows with a blank ticker are skipped above, so a feed of `[{"ticker": "",
    # ...}]` is non-empty going in and empty coming out — it sails past the
    # "refusing to treat an empty book as everything-was-sold" check and marks the
    # WHOLE book sold, with two demotions comfortably under the circuit breaker
    # (Codex round 4). One blank row among good ones fabricates one sale the same
    # way. The guard has to be applied to the rows that will actually be compared.
    skipped = len(raw_rows) - len(rows) - len(merged)
    if not rows:
        raise HeldFeedError(
            f"ownership feed carried {len(raw_rows)} row(s) but none had a usable "
            f"ticker — refusing to treat that as 'everything was sold'")
    if skipped > 0:
        raise HeldFeedError(
            f"ownership feed has {skipped} row(s) with no ticker among {len(raw_rows)} "
            f"— each one is a holding this sync cannot see, and an unseen holding "
            f"reads as a sale. Fix the publisher; nothing has been written")

    feed = HeldFeed(
        schema=int(schema),
        generated_at=str(payload.get("generated_at", "")),
        stalest_age_days=float(age),
        rows=rows,
    )
    feed._broker_dates = [str(b.get("as_of") or "") for b in (payload.get("brokers") or [])]
    feed.aliased = aliased
    if merged:
        logger.warning("held feed: %s arrived under more than one symbol and were "
                       "MERGED (shares summed, brokers unioned, cost basis "
                       "share-weighted): %s", len(set(merged)), ", ".join(sorted(set(merged))))
    if aliased:
        # Never silent: an alias is a claim that two symbols are one company, and a
        # wrong one would merge two issuers' holdings without a trace.
        logger.warning("held feed symbol alias applied: %s (see SYMBOL_ALIASES / board #345)",
                       ", ".join(aliased))
    return feed


LEGACY_POSITION = "Portfolio"


def migrate_legacy_portfolio(entries, feed: "HeldFeed"):
    """One-time: move rows still carrying the retired `Position == "Portfolio"`.

    Returns `(new_entries, migrated_tickers)`. Idempotent by construction -- after
    the first run no row can hold that value, because it is no longer in
    `ALLOWED_POSITION_VALUES`, so this is inert on every subsequent call. Folded
    into `sync-held` rather than given its own command so there is nothing for a
    human to remember to run first, and nothing that half-migrates if they forget.

    They land on `Researching`, which is the same place a SALE lands a name. That
    is deliberate and makes the sale path almost a no-op on this column: a held
    name's intent is already the state it will occupy once it is sold, so selling
    it changes the FACT (`Held`) without disturbing the JUDGEMENT. `researching.json`
    still excludes held names (see weekly_universe), so nothing appears in two
    exported lists at once and no consumer sees a membership change from this.

    ## A legacy `Portfolio` row absent from the feed was ALREADY sold

    On the first run nothing carries `Held == "Y"` yet, so `plan_sync` cannot see a
    demotion -- those rows would simply fail to be promoted and land on `Held = "N"`
    looking as though they had never been owned. That would lose the history for
    exactly the three names that motivated this work (ROIV, RPD, U). So the
    migration marks them `Previously Held` here, where the old value still carries
    the evidence: `Position == "Portfolio"` MEANT held, so a legacy row missing from
    the broker feed is a sale that already happened.

    `Held Until` is deliberately left EMPTY for them. We know the name is gone by the
    feed's as-of; we do not know the day it sold -- ROIV was 2026-08-03 and RPD/U
    2026-08-21, and the feed cannot distinguish them. Stamping the as-of would write
    a sale date that every downstream reader would take as fact. They are returned by
    name instead, so the operator can fill the real dates if they matter.
    """
    from universe import positions as _pos

    migrated = []
    already_sold = []
    out = []
    for e in entries:
        e = dict(e)
        # Keyed on the FLAGS being empty, never on `Position`. Since 2026-08-23
        # `Position` is a DERIVED MIRROR that `save()` recomputes -- and it prints
        # "Portfolio" for every held row -- so a Position-keyed check re-fires on
        # all 30 holdings every single run and reports a migration it did not do.
        # The genuine legacy shape is a row with no intent flag at all; `load()`
        # upgrades anything that still has the old column, so in practice this is
        # now inert, which is exactly what a completed migration should be.
        is_legacy = (
            not any(_pos.has_state(e, f) for f in _pos.STATE_FLAGS)
            and not _pos.is_held(e)
            and (e.get("Position") or "").strip() == LEGACY_POSITION
        )
        if is_legacy:
            ticker = e["Ticker"].strip().upper()
            e[DEMOTION_POSITION] = "Y"
            migrated.append(ticker)
            if ticker not in feed.rows:
                e["Previously Held"] = "Y"
                e["Held"] = "N"
                e["Held Until"] = ""      # unknown on purpose -- see docstring
                already_sold.append(ticker)
        out.append(e)
    return out, sorted(migrated), sorted(already_sold)


def plan_sync(entries, feed: HeldFeed, universe_tickers=None, accept_partial_join: bool = False) -> SyncPlan:
    """Compute the change WITHOUT touching disk.

    `entries` is `positions.load()` output. Pure function so the dry-run and the
    real run cannot disagree about what is about to happen.
    """
    plan = SyncPlan(feed_as_of=feed.as_of)
    by_ticker = {e["Ticker"].strip().upper(): e for e in entries}

    if universe_tickers is not None:
        known = {t.strip().upper() for t in universe_tickers}
        plan.not_in_universe = sorted(t for t in feed.rows if t not in known)

    for ticker, entry in sorted(by_ticker.items()):
        was_held = (entry.get("Held") or "").strip().upper() == "Y"
        now_held = ticker in feed.rows
        if now_held and not was_held:
            plan.promotions.append(ticker)
        elif was_held and not now_held:
            plan.demotions.append(ticker)
        elif now_held and was_held:
            plan.refreshed.append(ticker)

    # ⛑ AN UNJOINED FEED ROW ALONGSIDE A DEMOTION IS THE FALSE-SALE SIGNATURE.
    #
    # This guard is at the point of HARM, and that is the whole point: it does not
    # care WHY a symbol failed to join. Codex round 2 showed the alias-store
    # guards could not cover the case — `_load_symbol_aliases` raises on an
    # UNREADABLE store, but a MISSING one legitimately yields an empty map, and a
    # store whose entry contradicts the universe is never checked here at all
    # (`held.py` reads `data/ticker_aliases.json` directly, so the publish-side
    # guard is irrelevant to it). Both roads lead here: the feed's `FISV` does not
    # join the universe's `FI`, `FI` is planned as a demotion, one demotion is far
    # under MAX_DEMOTIONS_PER_RUN, and a real position is written out as sold.
    #
    # `not_in_universe` is only populated when the caller passes `universe_tickers`
    # — without it we cannot see the signature and do not pretend to.
    # ⛑ THE MATCH IS ON SHARE COUNT, NOT ON CO-OCCURRENCE.
    #
    # v1 of this guard blocked whenever ANY demotion coincided with ANY unjoined
    # feed holding, which is the shape of a fabricated sale and also the shape of
    # an ordinary week: sell a covered name, buy an uncovered one, and the whole
    # sync aborted while the sold name kept publishing as owned (Codex round 3).
    # That is "a guard can become the outage", and it contradicted this module's
    # own documented contract that an uncovered holding is NOT fatal.
    #
    # The discriminator is cheap and strong: a fabricated sale is ONE position
    # seen twice, so the unjoined feed row carries the SAME share count the
    # position record already holds. A genuine rebalance has no such coincidence.
    # A demoted row with no recorded share count cannot be matched either way, so
    # it is reported and allowed through rather than blocking on ignorance.
    # ⛑ THE RULE IS "SHARES LOST == SHARES UNJOINED", AND A DEMOTION IS ONLY ITS
    #    EXTREME CASE. v1 of this guard looked at demotions alone, so a PARTIAL
    #    split slipped through untouched: a 30-share position held at two brokers
    #    under two spellings, with the alias store gone, arrives as `FI: 10` plus
    #    an unjoined `FISV: 20`. That is `refreshed`, not a demotion — unblocked,
    #    and `apply_plan` overwrote 30 shares with 10 and one broker's cost basis
    #    with the other's (Codex round 4). Same defect, two thirds of the way
    #    along. Generalising to "recorded minus new" covers both, and a demotion
    #    falls out as new == 0.
    # ⛑ RUN THE ALIAS STORE'S OWN VALIDATOR, DO NOT RE-IMPLEMENT ONE OF ITS RULES.
    #
    # Four adversarial rounds over this feature found three separate roads to a
    # wrong ownership record, and all three were the SAME structural fault: a
    # check that existed on the PUBLISH side and not on the read side, because
    # this module reads `data/ticker_aliases.json` directly and the export
    # pipeline never runs for it.
    #
    # The first fix here was a hand-rolled version of `check_universe`'s fatal
    # rule (an alias that is also a covered row — resolve through it and two
    # separately-covered companies merge into one holding). That closed the
    # instance and left the class open: a hand copy of one of three rules drifts
    # from the original the first time the original changes. Calling the real
    # validator closes all three and cannot drift.
    #
    # A ticker list is all this function is given, so only the rules that key on
    # tickers can fire; the identity-anchor rule needs columns we do not have and
    # skips itself on blanks, which is its documented behaviour, not a silent hole.
    # ⛑ VALIDATE THE MAP THAT WAS ACTUALLY APPLIED, AND NEVER DISCARD A HAZARD.
    #
    # Two defects lived in the first version of this block, both found by review:
    #
    #   * it RELOADED the store from disk while `load_feed` had already normalised
    #     the feed with the import-time `SYMBOL_ALIASES`. Change the file in
    #     between — Dropbox sync, a concurrent edit — and it validated bytes that
    #     never touched the data, which is "the reviewed thing is not the
    #     published thing" with a validator playing the reviewer;
    #   * its `except` set `problems = []`, so a hazard `merge_hazards` had
    #     ALREADY confirmed was erased when the advisory call raised afterwards.
    #     A fatal, once found, is never unfound.
    #
    # The advisory `check_universe` pass was also dropped rather than kept. It was
    # handed a frame carrying only `Ticker`, so its CIK/ISIN/FIGI rules skip on
    # blanks every single time — a three-rule validator of which one rule could
    # ever fire, logging as though all three had. A check that cannot match is
    # worse than no check, because it reads like coverage.
    if universe_tickers:
        # The store's OWN rule, applied to the exact map `load_feed` used. Not a
        # fresh read (which would validate bytes that never touched the data) and
        # not a hand copy (which would drift from the original). Both were live
        # defects here before this line.
        try:
            from universe.aliases import merge_hazards_for_map

            hazards = merge_hazards_for_map(SYMBOL_ALIASES, universe_tickers)
        except ImportError:
            hazards = []
        if hazards:
            plan.blocked_reason = (
                "the alias map applied to this feed would resolve a covered company "
                "onto another: " + "; ".join(hazards[:4])
                + (f" ... +{len(hazards) - 4} more" if len(hazards) > 4 else "")
                + ". Fix data/ticker_aliases.json or the universe row, then re-run."
            )
            return plan

    # ⛑ A DEMOTION IS THE ONLY DESTRUCTIVE OPERATION HERE, SO IT IS THE ONLY ONE
    #    WITHHELD WHEN THE JOIN CANNOT BE TRUSTED.
    #
    # This replaces a share-count heuristic that matched "shares lost == shares
    # unjoined" and blocked the whole run on a hit. Fable disproved its premise:
    # **a symbol change and a share-count change CO-OCCUR at a corporate action**,
    # which is precisely the moment no alias entry exists yet. Reproduced against
    # these files -- a 3-share DRIP (100 -> 103) made the numbers unequal, the
    # match missed, and a fabricated sale of the real position was WRITTEN:
    # Held=N, Shares cleared, Held Until stamped. Splits, conversions and an added
    # buy all do the same. A guard whose premise fails exactly when it is needed
    # is not a guard.
    #
    # The replacement asserts nothing about identity. It says only: while some
    # feed holding did not join, or while a promotion looks like the other half of
    # a demotion, WE CANNOT TELL a sale from a re-spelling -- so promotions and
    # refreshes still apply and the demotions are held back and named. That is
    # strictly safer than the heuristic (it cannot fabricate a sale at all) and
    # strictly cheaper than the round-3 version (it does not block the run, so an
    # uncovered holding never stops an ordinary rebalance). The cost is a `Held`
    # that stays stale for a run, reported every time and exiting non-zero -- the
    # trade this module's own docstring already makes: never read absence as sold.
    withhold_reasons = []
    if plan.demotions and plan.not_in_universe:
        withhold_reasons.append(
            f"{len(plan.not_in_universe)} feed holding(s) did not join the universe "
            f"({', '.join(plan.not_in_universe[:5])})")

    # The same doubt without any unjoined row: a promotion carrying a demotion's
    # share count is the shape of ONE position moving between two covered rows --
    # the case where the universe holds two rows for one issuer and no alias links
    # them. Zero unjoined holdings, so the check above cannot see it.
    if plan.demotions and plan.promotions:
        # NO SHARE COMPARISON HERE. The previous version required the promoted row
        # to carry the SAME count as the demoted one -- the exact premise this
        # redesign was written to delete, left gating the covered-row path. Codex
        # round 6: a corporate action producing covered NEW at 103 against OLD at
        # 100 slipped straight through and OLD was stamped sold, because the
        # counts differ at precisely the event that renames a symbol.
        #
        # Without an identity anchor we genuinely cannot tell "sold X, bought Y"
        # from "X became Y", so we do not pretend to: any demotion coinciding with
        # any promotion is deferred. That over-defers a real rotation week by one
        # run, which is why `--accept-partial-join` exists as the release.
        withhold_reasons.append(
            f"{len(plan.promotions)} name(s) joined Held in the same run "
            f"({', '.join(plan.promotions[:5])}) -- with no identity anchor on the "
            f"feed, a rename is indistinguishable from a sale plus a purchase")

    # A REFRESH CAN LOSE SHARES TO AN UNJOINED SYMBOL TOO, and that is corruption
    # rather than a fabricated sale: the position survives with the WRONG count.
    # 30 shares held across two brokers under two spellings arrive as FI:10 plus an
    # unjoined FISV:20, and writing 10 silently discards two thirds of the holding
    # and one broker's cost basis. Same doubt, same answer -- hold the figures back
    # rather than write a number we cannot trust.
    if plan.not_in_universe:
        for ticker in list(plan.refreshed):
            recorded = _shares_of(by_ticker.get(ticker, {}))
            now = feed.rows[ticker].shares if ticker in feed.rows else None
            if recorded is None or now is None or not _finite(now):
                continue
            # ANY difference, not just a decrease. Requiring `recorded > now` kept
            # the same share-direction assumption the redesign rejects: with a
            # joined leg at 35 against a stored 30 and an unjoined 20, the refresh
            # was allowed and wrote 35, discarding 20 shares and their basis
            # (Codex round 6). An unjoined holding could belong to ANY covered row,
            # so any figure that moves while one exists is a figure we cannot trust.
            if abs(recorded - now) > SHARE_MATCH_TOLERANCE:
                plan.refreshed.remove(ticker)
                plan.withheld_refreshes.append(ticker)
                withhold_reasons.append(
                    f"{ticker} would drop {recorded - now:g} share(s) while "
                    f"{len(plan.not_in_universe)} holding(s) did not join")

    # ⛑ THE RELEASE. Without one, a single persistently-uncovered holding defers
    # every real sale FOREVER, and exit 2 is a repeated warning, not a mechanism --
    # which recreates the exact stale-held failure this module exists to prevent
    # (ROIV published as held for 19 days). Codex round 6. The operator who has
    # looked at the named holdings and decided they are unrelated passes
    # `--accept-partial-join`; the reasons are still printed, so the decision is
    # recorded in the run output rather than made silently by a default.
    if withhold_reasons and accept_partial_join:
        logger.warning(
            "accept-partial-join: applying %d demotion(s) and %d figure update(s) "
            "the join could not vouch for, on operator instruction: %s",
            len(plan.demotions), len(plan.refreshed), "; ".join(withhold_reasons))
        plan.accepted_partial_join = True
        withhold_reasons = []

    if withhold_reasons:
        plan.withheld_demotions = list(plan.demotions)
        plan.demotions = []
        plan.withheld_reason = (
            f"{len(plan.withheld_demotions)} demotion(s) and "
            f"{len(plan.withheld_refreshes)} figure update(s) WITHHELD "
            f"({', '.join(plan.withheld_demotions)}) because the join cannot be "
            f"trusted this run: " + "; ".join(withhold_reasons)
            + ". Everything else was applied. Nothing was marked sold and no figure was "
            "overwritten with a number that could not be trusted -- "
            "a sale that is real will apply on the next run once the holding is "
            "covered or its symbols are recorded in data/ticker_aliases.json."
        )
        logger.warning("%s", plan.withheld_reason)

    if len(plan.demotions) > MAX_DEMOTIONS_PER_RUN:
        plan.blocked_reason = (
            f"{len(plan.demotions)} names would leave Held in one run "
            f"(limit {MAX_DEMOTIONS_PER_RUN}): {', '.join(plan.demotions)}. "
            f"That is more than a rebalance — check the publisher before overriding."
        )
    return plan


def apply_plan(entries, feed: HeldFeed, plan: SyncPlan, today=None):
    """Return NEW entries with the derived columns written. Never mutates input.

    Raises if the plan is blocked — a blocked plan must not reach disk by any path.
    """
    if plan.is_blocked:
        raise HeldFeedError(f"refusing to apply a blocked plan: {plan.blocked_reason}")

    stamp = (today or date.today()).isoformat()
    out = []
    for e in entries:
        e = dict(e)
        ticker = e["Ticker"].strip().upper()
        if ticker in plan.withheld_refreshes or ticker in plan.withheld_demotions:
            # UNTOUCHED -- and this covers BOTH withheld sets, which the first
            # version did not. A withheld demotion has no feed row and was removed
            # from `plan.demotions`, so it fell through to the generic
            # not-in-feed branch and had Shares and Average Cost CLEARED: the row
            # stayed Held=Y with blank figures. I fixed this exact erasure for
            # refreshes and shipped the identical bug for demotions in the same
            # commit, while its message claimed "a withheld row is left
            # untouched". Codex round 6 caught it.
            out.append(e)
            continue
        row = feed.rows.get(ticker)
        if row is not None:
            e["Held"] = "Y"
            e["Held As Of"] = plan.feed_as_of
            e["Shares"] = row.shares
            e["Average Cost"] = row.avg_cost
            e["Held Until"] = ""
        elif ticker in plan.demotions:
            # The name left the brokers. Its ROUTING key falls to Researching so it
            # stays in every lane that reads portfolio.json + researching.json; the
            # fact that it was owned is preserved beside it rather than in Position.
            e["Held"] = "N"
            e["Held As Of"] = plan.feed_as_of
            e["Previously Held"] = "Y"
            e["Held Until"] = stamp
            # Set the FLAG. Writing `Position` alone is a dead write that `save()`
            # recomputes from the flags -- precisely what made the legacy migration
            # look like it was working when it was not.
            for _f in STATE_FLAGS_FOR_DEMOTION:
                e[_f] = "Y" if _f == DEMOTION_POSITION else ""
            e["Shares"] = None
            e["Average Cost"] = None
        else:
            # Not held, by any route. Share count and average cost are facts about a
            # HOLDING, so a row that is not held must not carry them -- ROIV kept
            # "3400 shares @ $5.00" from its hand-entered days after the account was
            # liquidated, which is exactly the shape of stale figure a consumer reads
            # as current. Stated as an invariant here rather than only on the
            # demotion path, so it self-heals whatever wrote them.
            e["Held"] = e.get("Held") or "N"
            e["Shares"] = None
            e["Average Cost"] = None
        out.append(e)
    return out
