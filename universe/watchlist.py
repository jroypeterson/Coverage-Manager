"""Personal watchlist — a subset of the coverage universe with buy/target prices.

The watchlist is a separate, hand-edited CSV that records tickers the user owns
or is watching for a buy opportunity. It is NOT mixed into
`data/coverage_universe_tickers.csv` because the universe is a shared canonical
artifact consumed by several downstream projects; position state is personal.

Rules:
  - Every watchlist ticker must exist in the coverage universe (strict subset).
    New coverage additions go through discovery, not the watchlist.
  - Prices are in the ticker's local currency (matches universe row).
  - The file is intended to be readable/editable by hand.

This module provides the pure data layer (load / validate / add / remove /
save). Artifact publishing lives in `weekly_universe._step_export_watchlist`,
and the weekly report lives in `reporting/watchlist_report.py`.
"""

import csv
from datetime import date
from pathlib import Path

from config import CSV_PATH, DATA_DIR
from logging_utils import get_logger

logger = get_logger("universe.watchlist")

WATCHLIST_PATH = DATA_DIR / "watchlist.csv"
WATCHLIST_COLUMNS = ["Ticker", "Buy Price", "Target Price", "Date Added", "Notes"]


class WatchlistError(Exception):
    """Raised for watchlist validation or I/O errors."""


REQUIRED_METADATA_FIELDS = ("Company Name", "Sector (JP)", "Currency", "Exchange")


def _load_universe_rows(universe_csv_path=CSV_PATH):
    """Return a {ticker: row_dict} map for the coverage universe CSV."""
    rows = {}
    with open(universe_csv_path, newline="", encoding="utf-8") as f:
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
        raise WatchlistError(f"{field} must be a number, got {raw!r}") from e
    if val <= 0:
        raise WatchlistError(f"{field} must be positive, got {val}")
    return val


def load(path=WATCHLIST_PATH):
    """Read the watchlist CSV and return a list of dicts.

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
                "Buy Price": _parse_price(row.get("Buy Price"), "Buy Price"),
                "Target Price": _parse_price(row.get("Target Price"), "Target Price"),
                "Date Added": (row.get("Date Added") or "").strip(),
                "Notes": (row.get("Notes") or "").strip(),
            })
    return entries


def save(entries, path=WATCHLIST_PATH):
    """Write entries back to the watchlist CSV, sorted by ticker."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_entries = sorted(entries, key=lambda e: e["Ticker"].upper())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=WATCHLIST_COLUMNS)
        writer.writeheader()
        for e in sorted_entries:
            writer.writerow({
                "Ticker": e["Ticker"],
                "Buy Price": "" if e.get("Buy Price") is None else e["Buy Price"],
                "Target Price": "" if e.get("Target Price") is None else e["Target Price"],
                "Date Added": e.get("Date Added", ""),
                "Notes": e.get("Notes", ""),
            })


def validate(entries, universe_csv_path=CSV_PATH):
    """Check that entries are a strict subset of the coverage universe and
    that each ticker's universe row has the metadata fields the downstream
    report and sigma integration need (Company Name, Sector, Currency, Exchange).

    Returns (errors, warnings) — lists of strings. `errors` is non-empty when
    the watchlist cannot be used as-is.
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
        tgt = e.get("Target Price")
        if buy is not None and tgt is not None and tgt <= buy:
            warnings.append(
                f"{t}: target price ({tgt}) is not above buy price ({buy})"
            )
    return errors, warnings


def add(ticker, buy_price=None, target_price=None, notes="", path=WATCHLIST_PATH,
        universe_csv_path=CSV_PATH, today=None):
    """Add a ticker to the watchlist (or update its fields if already present).

    Enforces subset-of-universe and price sanity before writing. Returns the
    updated entry dict.
    """
    ticker = (ticker or "").strip()
    if not ticker:
        raise WatchlistError("ticker is required")

    universe = _load_universe_tickers(universe_csv_path)
    if ticker not in universe:
        raise WatchlistError(
            f"{ticker} is not in the coverage universe — add it via discovery first"
        )

    buy = _parse_price(buy_price, "Buy Price") if buy_price not in (None, "") else None
    tgt = _parse_price(target_price, "Target Price") if target_price not in (None, "") else None
    if buy is not None and tgt is not None and tgt <= buy:
        raise WatchlistError(
            f"target price ({tgt}) must be above buy price ({buy})"
        )

    entries = load(path)
    existing = next((e for e in entries if e["Ticker"] == ticker), None)
    if existing:
        existing["Buy Price"] = buy if buy is not None else existing.get("Buy Price")
        existing["Target Price"] = tgt if tgt is not None else existing.get("Target Price")
        if notes:
            existing["Notes"] = notes
        entry = existing
    else:
        entry = {
            "Ticker": ticker,
            "Buy Price": buy,
            "Target Price": tgt,
            "Date Added": (today or date.today().isoformat()),
            "Notes": notes or "",
        }
        entries.append(entry)

    save(entries, path)
    return entry


def remove(ticker, path=WATCHLIST_PATH):
    """Remove a ticker from the watchlist. Returns True if removed, False if not found."""
    ticker = (ticker or "").strip()
    entries = load(path)
    new_entries = [e for e in entries if e["Ticker"] != ticker]
    if len(new_entries) == len(entries):
        return False
    save(new_entries, path)
    return True
