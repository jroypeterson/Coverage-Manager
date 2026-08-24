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
# This map exists because a ticker is not an identity, and it is deliberately as
# small as the evidence supports -- one entry, not a general aliasing layer.
#
# Fiserv is ONE issuer (CIK 798354, ISIN US3377381088, FIGI BBG000BJKQW0) trading
# under two live symbols across the fleet: the coverage universe carries `FI` (the
# 2025 corporate action), while IBKR and yfinance both serve `FISV` -- and yfinance
# returns HTTP 404 for `FI`. Without this entry the very FIRST run of this sync
# reports that JP sold Fiserv, because the feed says FISV and the universe says FI
# and nothing joins them. A false SALE is exactly the error this module was written
# to eliminate, so shipping it as a side effect would be self-defeating.
#
# THIS IS A STOPGAP, NOT THE FIX. The real repair is board row #345: join on the
# identity that did NOT change (CIK / ISIN / FIGI are identical across both symbols
# and are already sitting in exports/universe.csv, unused for this). When #345 lands,
# delete this map -- do not grow it. A second entry here is the signal that the
# stopgap has become the architecture.
SYMBOL_ALIASES = {
    "FISV": "FI",
}



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
    for r in raw_rows:
        t = str(r.get("ticker", "")).strip().upper()
        if not t:
            continue
        if t in SYMBOL_ALIASES:
            aliased.append(f"{t}->{SYMBOL_ALIASES[t]}")
            t = SYMBOL_ALIASES[t]
        rows[t] = HeldRow(
            ticker=t,
            shares=float(r.get("shares") or 0.0),
            avg_cost=(None if r.get("avg_cost") is None else float(r["avg_cost"])),
            brokers=list(r.get("brokers") or []),
        )

    feed = HeldFeed(
        schema=int(schema),
        generated_at=str(payload.get("generated_at", "")),
        stalest_age_days=float(age),
        rows=rows,
    )
    feed._broker_dates = [str(b.get("as_of") or "") for b in (payload.get("brokers") or [])]
    feed.aliased = aliased
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


def plan_sync(entries, feed: HeldFeed, universe_tickers=None) -> SyncPlan:
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
