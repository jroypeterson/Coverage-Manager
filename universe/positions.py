"""Positions and research — names the user owns or is actively researching.

Replaces the older `universe/watchlist.py` module. The source file
`data/positions_and_researching.csv` records every ticker with a personal
trading-state relationship (held in portfolio, or actively researching with
intent to buy). It is NOT mixed into `data/coverage_universe_tickers.csv`
because the universe is a shared canonical artifact consumed by sibling
projects; position state is personal.

Schema (`data/positions_and_researching.csv`):
    Ticker, Position, Position Date, Buy Price, Sell Price,
    First Buy Date, Average Cost, Shares, Notes

Position values:
    "Portfolio"   — names you own (any size, full or starter)
    "Researching" — names you're building a thesis to buy but don't yet hold

Rules:
  - Every ticker must exist in the coverage universe (strict subset).
  - Position must be one of the two enum values (case-sensitive).
  - Buy Price (entry target) is most useful for Researching rows.
  - Sell Price (exit target) is most useful for Portfolio rows.
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
    "Ticker", "Position", "Position Date",
    "Buy Price", "Sell Price",
    "First Buy Date", "Average Cost", "Shares",
    "Notes",
]
ALLOWED_POSITION_VALUES = {"Portfolio", "Researching"}


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


def _parse_int(raw, field):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        val = int(float(str(raw).strip()))
    except ValueError as e:
        raise PositionsError(f"{field} must be an integer, got {raw!r}") from e
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
            entries.append({
                "Ticker": ticker,
                "Position": (row.get("Position") or "").strip(),
                "Position Date": (row.get("Position Date") or "").strip(),
                "Buy Price": _parse_price(row.get("Buy Price"), "Buy Price"),
                "Sell Price": _parse_price(row.get("Sell Price"), "Sell Price"),
                "First Buy Date": (row.get("First Buy Date") or "").strip(),
                "Average Cost": _parse_price(row.get("Average Cost"), "Average Cost"),
                "Shares": _parse_int(row.get("Shares"), "Shares"),
                "Notes": (row.get("Notes") or "").strip(),
            })
    return entries


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
                "Position": e.get("Position", ""),
                "Position Date": e.get("Position Date", ""),
                "Buy Price": "" if e.get("Buy Price") is None else e["Buy Price"],
                "Sell Price": "" if e.get("Sell Price") is None else e["Sell Price"],
                "First Buy Date": e.get("First Buy Date", ""),
                "Average Cost": "" if e.get("Average Cost") is None else e["Average Cost"],
                "Shares": "" if e.get("Shares") is None else e["Shares"],
                "Notes": e.get("Notes", ""),
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

        pos = e.get("Position", "")
        if pos not in ALLOWED_POSITION_VALUES:
            errors.append(
                f"{t}: Position must be one of {sorted(ALLOWED_POSITION_VALUES)}, "
                f"got {pos!r}"
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
    n_shares = _parse_int(shares, "Shares") if shares not in (None, "") else None

    if buy is not None and sell is not None and sell <= buy:
        raise PositionsError(
            f"sell price ({sell}) must be above buy price ({buy})"
        )

    entries = load(path)
    existing = next((e for e in entries if e["Ticker"] == ticker), None)
    if existing:
        existing["Position"] = position
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
            "Position": position,
            "Position Date": (today or date.today().isoformat()),
            "Buy Price": buy,
            "Sell Price": sell,
            "First Buy Date": first_buy_date or "",
            "Average Cost": avg_cost,
            "Shares": n_shares,
            "Notes": notes or "",
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


def remove(ticker, path=POSITIONS_PATH):
    """Remove a ticker from the positions file. Returns True if removed, False if not found."""
    ticker = (ticker or "").strip()
    entries = load(path)
    new_entries = [e for e in entries if e["Ticker"] != ticker]
    if len(new_entries) == len(entries):
        return False
    save(new_entries, path)
    return True
