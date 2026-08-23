"""Positions and research — names the user owns, is researching, or is
ready to act on.

Replaces the older `universe/watchlist.py` module. The source file
`data/positions_and_researching.csv` records every ticker with a personal
trading-state relationship (held in portfolio, actively researching,
or trigger-ready for entry on either side). It is NOT mixed into
`data/coverage_universe_tickers.csv` because the universe is a shared
canonical artifact consumed by sibling projects; position state is personal.

Schema (`data/positions_and_researching.csv`):
    Ticker, Position, Position Date, Buy Price, Sell Price,
    First Buy Date, Average Cost, Shares, Notes

Position values:
    "Portfolio"              — names you own (any size, full or starter)
    "Researching"            — names you're building a thesis to buy but
                                don't yet hold (active thesis work)
    "Following for Interest" — names you track for earnings / industry
                                signal but have no intent to trade
                                (passive tracking; no thesis work)
    "Ready to Buy"           — long thesis complete; waiting for the entry
                                trigger (typically a price level on Buy Price)
    "Ready to Short"         — short thesis complete; waiting for the entry
                                trigger (typically a price level on Sell Price,
                                since short entry is at the high and cover
                                is at the low)

Rules:
  - Every ticker must exist in the coverage universe (strict subset).
  - Position must be one of the five enum values (case-sensitive).
  - Buy Price semantics:
      * Portfolio              — historical/avg entry reference (often blank)
      * Researching            — forward entry target
      * Following for Interest — informational only (typically blank)
      * Ready to Buy           — entry trigger (the level you're waiting on)
      * Ready to Short         — cover/exit target (low side of the short)
  - Sell Price semantics:
      * Portfolio              — exit target (sell-side trigger)
      * Researching            — exit target once held
      * Following for Interest — informational only (typically blank)
      * Ready to Buy           — exit target once held
      * Ready to Short         — short-entry trigger (the level you're waiting on)
  - First Buy Date / Average Cost / Shares will eventually be auto-populated
    from broker integration (IBKR / Fidelity); empty today.
  - Notes is free-form.

This module provides the pure data layer (load / validate / add / remove /
save). Artifact publishing lives in `weekly_universe._step_export_positions`,
and the weekly report lives in `reporting/positions_report.py` (TBD — for
now `watchlist_report.py` continues to serve via the back-compat view).
"""

import csv
from datetime import date
from pathlib import Path

from config import CSV_PATH, DATA_DIR
from logging_utils import get_logger

logger = get_logger("universe.positions")

POSITIONS_PATH = DATA_DIR / "positions_and_researching.csv"
POSITIONS_COLUMNS = [
    "Ticker", "Position Date",
    "Buy Price", "Sell Price",
    "First Buy Date", "Average Cost", "Shares",
    "Notes",
    # DERIVED from portfolio_daily's broker feed -- see universe/held.py. Never
    # typed by a human, and never authored through Notion: the state flags below
    # are intent, these are fact. `Held Until` + `Previously Held` keep the history
    # of a sold name beside it, so no intent column has to encode it (which would
    # change where the name flows -- see held.py's module docstring).
    "Held", "Held As Of", "Previously Held", "Held Until",
    # INTENT, one Y/N flag per state (2026-08-23, JP: "I want coverage manager to
    # have columns for following for Interest and Researching").
    #
    # These replaced the single `Position` value, and the reason is not cosmetic:
    #   1. One column forced a HELD name to carry an intent it does not have. The
    #      first cut of the Held work stored AAPL as Position="Researching", which
    #      is simply false -- he owns it, he is not researching it.
    #   2. The states are not mutually exclusive in life. A name can be a bellwether
    #      you read every quarter AND one you are actively underwriting; JPM is the
    #      worked example (Following for Interest for the consumer commentary,
    #      Researching if he starts a thesis). One column cannot say both.
    #
    # `Position` is still WRITTEN as the last column -- see `published_position` --
    # so anything reading it keeps working. It is a DERIVED MIRROR, not a store:
    # write the flags, never `Position`.
    "Researching", "Following for Interest", "Ready to Buy", "Ready to Short",
    "Position",
]

# The intent flags, in the precedence order used to collapse them into the single
# legacy `Position` scalar. Precedence runs most-committed first: a trigger-ready
# thesis is a stronger statement than active research, which is stronger than
# passive interest.
STATE_FLAGS = ["Ready to Buy", "Ready to Short", "Researching", "Following for Interest"]
# "Portfolio" LEFT this set on 2026-08-23 and that is the whole point of the change:
# ownership is a broker fact derived into the `Held` column, not an intent someone
# types. A row can no longer CLAIM to be owned. `portfolio.json` is still exported --
# it is now built from Held == "Y" (weekly_universe), so every consumer's contract is
# byte-identical and none needed editing.
ALLOWED_POSITION_VALUES = {
    "Researching", "Following for Interest",
    "Ready to Buy", "Ready to Short",
}

# Ordered list used wherever stable iteration order matters (CLI summary,
# manifest, exports). Mirrors the five states above.
POSITION_VALUES_ORDERED = [
    "Researching", "Following for Interest",
    "Ready to Buy", "Ready to Short",
]


class PositionsError(Exception):
    """Raised for positions validation or I/O errors."""


REQUIRED_METADATA_FIELDS = ("Company Name", "Sector (JP)", "Currency", "Exchange")


def _load_universe_rows(universe_csv_path=CSV_PATH):
    """Return a {ticker: row_dict} map for the coverage universe CSV."""
    rows = {}
    # utf-8-sig tolerates an accidental BOM on the source CSV.
    with open(universe_csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("Ticker") or "").strip()
            if t and t != "#N/A":
                rows[t] = row
    return rows


def _load_universe_tickers(universe_csv_path=CSV_PATH):
    """Return the set of tickers present in the coverage universe CSV (exact, not normalized)."""
    return set(_load_universe_rows(universe_csv_path).keys())


def _parse_price(raw, field):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        val = float(str(raw).strip())
    except ValueError as e:
        raise PositionsError(f"{field} must be a number, got {raw!r}") from e
    if val <= 0:
        raise PositionsError(f"{field} must be positive, got {val}")
    return val


def _parse_shares(raw, field):
    """Share counts are FRACTIONAL and must not be rounded.

    This was `_parse_int` doing `int(float(x))`, which silently TRUNCATED: the real
    book carries FMS 452.656, PACS 277.893, CI 40.532, AAPL 13.036. While the column
    was reserved-and-blank that cost nothing; the moment it is filled from the broker
    feed it would publish 452 shares for a 452.656 holding -- a wrong number that
    looks entirely plausible. Non-negative rather than strictly positive (unlike
    `_parse_price`), because zero shares is expressible even though the feed omits
    such rows.
    """
    if raw is None or str(raw).strip() == "":
        return None
    try:
        val = float(str(raw).strip())
    except ValueError as e:
        raise PositionsError(f"{field} must be a number, got {raw!r}") from e
    if val < 0:
        raise PositionsError(f"{field} must be non-negative, got {val}")
    return val


def load(path=POSITIONS_PATH):
    """Read the positions CSV and return a list of dicts.

    Returns an empty list if the file does not exist. Does NOT validate
    against the universe — use `validate()` for that.
    """
    path = Path(path)
    if not path.exists():
        return []
    entries = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = (row.get("Ticker") or "").strip()
            if not ticker:
                continue
            entry = {
                "Ticker": ticker,
                "Position Date": (row.get("Position Date") or "").strip(),
                "Buy Price": _parse_price(row.get("Buy Price"), "Buy Price"),
                "Sell Price": _parse_price(row.get("Sell Price"), "Sell Price"),
                "First Buy Date": (row.get("First Buy Date") or "").strip(),
                "Average Cost": _parse_price(row.get("Average Cost"), "Average Cost"),
                "Shares": _parse_shares(row.get("Shares"), "Shares"),
                "Notes": (row.get("Notes") or "").strip(),
                # Derived columns. Absent in a pre-2026-08-23 file, which reads as
                # "" -- NOT as "N". A missing key is not a value: `held.plan_sync`
                # treats only an explicit "Y" as held, so an unmigrated file
                # promotes rather than mass-demoting on the first run.
                "Held": (row.get("Held") or "").strip(),
                "Held As Of": (row.get("Held As Of") or "").strip(),
                "Previously Held": (row.get("Previously Held") or "").strip(),
                "Held Until": (row.get("Held Until") or "").strip(),
            }
            legacy = (row.get("Position") or "").strip()
            has_any_flag = any((row.get(f) or "").strip() for f in STATE_FLAGS)
            for flag in STATE_FLAGS:
                v = (row.get(flag) or "").strip().upper()
                if not has_any_flag and legacy == flag:
                    # UPGRADE-ON-READ from a pre-flags file. Only when NO flag column
                    # carries anything -- otherwise a row deliberately cleared of a
                    # flag would have it silently restored from the stale `Position`
                    # mirror on every load, and the mirror would quietly become the
                    # store again.
                    v = "Y"
                entry[flag] = v
            # Carry the derived mirror in memory too, so `filter_by_position` and
            # every caller that reads `entry["Position"]` keeps working against a
            # value that is always consistent with the flags. Writing it here (not
            # copying the file's cell) is what stops a stale mirror being believed.
            entry["Position"] = published_position(entry)
            entries.append(entry)
    return entries


def is_held(entry):
    """True when the BROKERS report this name as held. The only ownership test."""
    return (entry.get("Held") or "").strip().upper() == "Y"


def has_state(entry, flag):
    """True when this row carries the named intent flag."""
    return (entry.get(flag) or "").strip().upper() == "Y"


def published_position(entry):
    """Collapse ownership + the intent flags into the single legacy `Position`.

    The stored truth is now one flag per state plus the derived `Held` column, but
    the published artifacts must not move: ~8 sibling repos read `position`, and
    `earnings_agent` subgroups on this very field. So this is the ONE place that
    decides what a row is called, and every export goes through it -- portfolio.json
    (weekly_universe), the sigma-alert payloads (reporting/sigma_export), and the
    joined positions CSV. Three copies of the rule would be three chances for them
    to disagree about what the fleet owns.

    Ownership wins outright: if the brokers say you hold it, that is what it is,
    whatever you also intend. Below that, `STATE_FLAGS` order applies -- and a name
    carrying two flags is real (a bellwether you have started underwriting), which
    is exactly why the flags are the store and this scalar is only a projection.
    """
    if is_held(entry):
        return "Portfolio"
    for flag in STATE_FLAGS:
        if has_state(entry, flag):
            return flag
    return ""


def filter_by_position(entries, position):
    """Return entries where Position matches (e.g. "Portfolio" or "Researching")."""
    return [e for e in entries if e.get("Position") == position]


def save(entries, path=POSITIONS_PATH):
    """Write entries back to the positions CSV, sorted by ticker."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_entries = sorted(entries, key=lambda e: e["Ticker"].upper())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=POSITIONS_COLUMNS)
        writer.writeheader()
        for e in sorted_entries:
            writer.writerow({
                "Ticker": e["Ticker"],
                "Position Date": e.get("Position Date", ""),
                "Buy Price": "" if e.get("Buy Price") is None else e["Buy Price"],
                "Sell Price": "" if e.get("Sell Price") is None else e["Sell Price"],
                "First Buy Date": e.get("First Buy Date", ""),
                "Average Cost": "" if e.get("Average Cost") is None else e["Average Cost"],
                "Shares": "" if e.get("Shares") is None else e["Shares"],
                "Notes": e.get("Notes", ""),
                "Held": e.get("Held", ""),
                "Held As Of": e.get("Held As Of", ""),
                "Previously Held": e.get("Previously Held", ""),
                "Held Until": e.get("Held Until", ""),
                **{f: (e.get(f) or "") for f in STATE_FLAGS},
                # DERIVED MIRROR, written so anything still reading `Position`
                # keeps working. Never read it back as the store -- `load` only
                # trusts it when no flag column exists at all.
                "Position": published_position(e),
            })


def validate(entries, universe_csv_path=CSV_PATH):
    """Check that entries are a strict subset of the coverage universe, every
    Position value is valid, and each ticker's universe row has the metadata
    fields downstream consumers need.

    Returns (errors, warnings) — lists of strings. `errors` is non-empty when
    the file cannot be used as-is.
    """
    errors = []
    warnings = []
    universe_rows = _load_universe_rows(universe_csv_path)

    seen = set()
    for e in entries:
        t = e["Ticker"]
        if t in seen:
            errors.append(f"duplicate ticker: {t}")
            continue
        seen.add(t)

        # Validate the FLAGS, which are the store. `Position` is a derived mirror
        # and is legitimately empty on a held row that carries no intent -- which
        # is the normal shape for something you simply own.
        for flag in STATE_FLAGS:
            v = (e.get(flag) or "").strip().upper()
            if v not in ("", "Y", "N"):
                errors.append(f"{t}: {flag} must be Y, N or blank, got {e.get(flag)!r}")
        if not is_held(e) and not any(has_state(e, f) for f in STATE_FLAGS):
            # A row that is neither held nor carries any intent is a row with no
            # reason to be in this file. Reported rather than dropped: it is far
            # likelier to be a flag someone cleared by mistake than a deliberate
            # blank, and silently removing coverage is the unrecoverable direction.
            errors.append(
                f"{t}: not held and carries no intent flag "
                f"({', '.join(STATE_FLAGS)}) - it has no reason to be in this file"
            )

        row = universe_rows.get(t)
        if row is None:
            errors.append(
                f"{t} is not in the coverage universe — add it via discovery first"
            )
        else:
            missing = [
                f for f in REQUIRED_METADATA_FIELDS
                if not (row.get(f) or "").strip()
            ]
            if missing:
                errors.append(
                    f"{t}: missing universe metadata for {', '.join(missing)} — "
                    f"fix the universe CSV row before using this ticker"
                )

        buy = e.get("Buy Price")
        sell = e.get("Sell Price")
        if buy is not None and sell is not None and sell <= buy:
            warnings.append(
                f"{t}: sell price ({sell}) is not above buy price ({buy})"
            )
    return errors, warnings


def _append_universe_row(row, universe_csv_path=CSV_PATH):
    """Append a fully-formed row dict to the coverage universe CSV.

    Mirrors `universe.watchlist._append_universe_row` for new-ticker creation
    via `add(..., create_if_missing=True)`.
    """
    path = Path(universe_csv_path)
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        existing_rows = list(reader)

    new_row = {col: row.get(col, "") for col in fieldnames}
    for col in row:
        if col not in fieldnames:
            fieldnames.append(col)
            new_row[col] = row[col]

    existing_rows.append(new_row)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)


def add(ticker, position, buy_price=None, sell_price=None, notes="",
        first_buy_date="", average_cost=None, shares=None,
        path=POSITIONS_PATH, universe_csv_path=CSV_PATH, today=None,
        create_if_missing=False, sector_jp=None, exchange_hint=None,
        dry_run=False):
    """Add a ticker to the positions file (or update its fields if already present).

    `position` must be one of "Portfolio" or "Researching" (case-sensitive).

    Same new-ticker escape hatch as `universe.watchlist.add` —
    `create_if_missing=True` auto-enriches a new universe row when the
    ticker isn't already covered.
    """
    ticker = (ticker or "").strip()
    if not ticker:
        raise PositionsError("ticker is required")

    if position not in ALLOWED_POSITION_VALUES:
        raise PositionsError(
            f"position must be one of {sorted(ALLOWED_POSITION_VALUES)}, "
            f"got {position!r}"
        )

    universe_rows = _load_universe_rows(universe_csv_path)
    universe_row_created = None

    if ticker not in universe_rows:
        if not create_if_missing:
            raise PositionsError(
                f"{ticker} is not in the coverage universe — add it via "
                f"discovery first, or re-run with create_if_missing=True "
                f"(CLI: --sector <Sector>) to auto-enrich"
            )
        if not sector_jp:
            raise PositionsError(
                f"{ticker} is not in the coverage universe and no sector_jp "
                f"was provided — sector is required when creating a new "
                f"universe row (it's user-curated, no API can fill it)"
            )
        from universe.enrich import enrich_single_ticker, EnrichError
        try:
            universe_row_created = enrich_single_ticker(
                ticker, sector_jp=sector_jp, exchange_hint=exchange_hint
            )
        except EnrichError as e:
            raise PositionsError(f"could not enrich {ticker}: {e}") from e

    buy = _parse_price(buy_price, "Buy Price") if buy_price not in (None, "") else None
    sell = _parse_price(sell_price, "Sell Price") if sell_price not in (None, "") else None
    avg_cost = _parse_price(average_cost, "Average Cost") if average_cost not in (None, "") else None
    n_shares = _parse_shares(shares, "Shares") if shares not in (None, "") else None

    if buy is not None and sell is not None and sell <= buy:
        raise PositionsError(
            f"sell price ({sell}) must be above buy price ({buy})"
        )

    entries = load(path)
    existing = next((e for e in entries if e["Ticker"] == ticker), None)
    if existing:
        # `position` names ONE state; setting it makes that flag true and clears
        # the others. `add` has always been "put this ticker in this state", and
        # keeping that meaning is what stops a caller half-migrating a row. To
        # carry two states at once (a bellwether you have started underwriting),
        # use `set_state`, which touches one flag and leaves the rest alone.
        for _f in STATE_FLAGS:
            existing[_f] = "Y" if _f == position else ""
        if buy is not None:
            existing["Buy Price"] = buy
        if sell is not None:
            existing["Sell Price"] = sell
        if first_buy_date:
            existing["First Buy Date"] = first_buy_date
        if avg_cost is not None:
            existing["Average Cost"] = avg_cost
        if n_shares is not None:
            existing["Shares"] = n_shares
        if notes:
            existing["Notes"] = notes
        entry = existing
    else:
        entry = {
            "Ticker": ticker,
            "Position Date": (today or date.today().isoformat()),
            "Buy Price": buy,
            "Sell Price": sell,
            "First Buy Date": first_buy_date or "",
            "Average Cost": avg_cost,
            "Shares": n_shares,
            "Notes": notes or "",
            **{_f: ("Y" if _f == position else "") for _f in STATE_FLAGS},
        }
        entries.append(entry)

    if dry_run:
        return {
            "positions_entry": entry,
            "universe_row": universe_row_created,
            "would_create_universe_row": universe_row_created is not None,
        }

    if universe_row_created is not None:
        _append_universe_row(universe_row_created, universe_csv_path=universe_csv_path)
    save(entries, path)
    return entry


def set_state(ticker, flag, on=True, path=POSITIONS_PATH):
    """Turn ONE intent flag on or off, leaving every other state untouched.

    This is the surface a "add JPM to following for interest" instruction lands
    on, and it is deliberately not `add`: `add` means *put this ticker in this
    state*, which clears the others. The states genuinely co-occur -- a bellwether
    you have started underwriting is both Following for Interest and Researching --
    and an instruction about one of them must not silently drop the other.

    Returns (entry, changed). `changed` is False when the flag already had that
    value, so a caller can stay quiet rather than announcing a no-op.
    """
    if flag not in STATE_FLAGS:
        raise PositionsError(f"unknown state {flag!r}; expected one of {STATE_FLAGS}")
    ticker = ticker.strip().upper()
    entries = load(path)
    entry = next((e for e in entries if e["Ticker"].strip().upper() == ticker), None)
    if entry is None:
        raise PositionsError(
            f"{ticker} is not in the positions file - add it first "
            f"(it must also be in the coverage universe)"
        )
    want = "Y" if on else ""
    if (entry.get(flag) or "") == want:
        return entry, False
    entry[flag] = want
    save(entries, path)
    return entry, True


def remove(ticker, path=POSITIONS_PATH):
    """Remove a ticker from the positions file. Returns True if removed, False if not found."""
    ticker = (ticker or "").strip()
    entries = load(path)
    new_entries = [e for e in entries if e["Ticker"] != ticker]
    if len(new_entries) == len(entries):
        return False
    save(new_entries, path)
    return True
