"""`Instrument Type` — is this row a depositary receipt, or the actual share?

`Listing Type` and `Instrument Type` are two different facts and one column
cannot carry both without lying about one (JP, 2026-07-29):

    Listing Type      "is this the home listing, or a secondary one?"
    Instrument Type   "is this a depositary receipt, or the actual share?"

Medtronic is a **secondary** listing of the **actual ordinary share**.
AstraZeneca's NYSE line is a **secondary** listing of a **receipt**. Both read
`ADR/Cross-listed` today, which is why the ADR validation rule (Rule A) cannot
be written: tightening a row's ISIN to its listing country's prefix would reject
**84 legitimate rows** — `ALKS` (IE), `CRSP` (CH), `BLCO` (CA), `CP` (CA),
`MDT`, `ICLR` — because an interlisted ordinary correctly carries its foreign
ISIN. This column is what tells those apart.

## The source is OpenFIGI, not a prefix heuristic

The plan of record was mechanical: *"if the ISIN prefix equals the listing
country it is the ordinary trading abroad; if it is US-prefixed on a US line it
is a receipt."* Measured against the live universe, that rule decides 65 of the
138 cross-listed rows correctly and then breaks in two places:

- **ISIN follows incorporation, not listing.** 12 rows are Cayman-incorporated
  China operators listed in Hong Kong (`KYG…` on HKEX — Innovent, WuXi
  Biologics, Hansoh, Akeso). Their ISIN matches neither the listing country nor
  the HQ country, and every one is an ordinary share.
- **A US ISIN on a US line is genuinely ambiguous.** 33 rows sit there, and the
  bucket contains both real ADRs *and* US-incorporated companies that merely
  operate abroad. The prefix cannot separate them.

OpenFIGI already answers the question outright. `securityType2` returns
literally ``"Depositary Receipt"`` or ``"Common Stock"``, and CM already trusts
OpenFIGI as the authority for the ISIN→issuer identity gate — so this adds a
field to an existing call rather than a new dependency. Verified live
2026-07-31 on eight real rows: Adagene / Adaptimmune / Amarin / Addex all
returned `Depositary Receipt`; AC Immune / Medtronic / Innovent / WuXi AppTec
all returned `Common Stock`.

## Three states, never two

Same discipline as `delisted_check`, `ipo_backfill` and the ISIN identity gate:
an answer we could not obtain is **not** a verdict. `no-openfigi-coverage`,
`openfigi-unreachable`, `ambiguous` and `unmapped-type` all leave the cell
**blank** and are reported by name. A blank cell is visibly missing and gets
refilled next run; a wrong one looks like data forever.

`ambiguous` deserves its own note: one ISIN maps to one FIGI per venue, so a
disagreement between venues is a real signal about a messy row — resolving it by
majority vote would be a coin toss dressed as data.

## What this column does NOT do

It does not describe the venue. AZN's row is a NYQ/USD line carrying
`GB0009895292`, the London ordinary — so this column reads `Ordinary Share`
while the ticker actually trades as a receipt. That disagreement is already a
finding (`crosscheck-foreign`'s `listing-mismatch`, standing on AZN/FER/MDA/
2359.HK). This column makes it machine-readable instead of prose; it does not
paper over it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

COLUMN = "Instrument Type"
AFTER_COLUMN = "Listing Type"          # where it belongs in the CSV

DEPOSITARY_RECEIPT = "Depositary Receipt"
ORDINARY_SHARE = "Ordinary Share"

# OpenFIGI `securityType2` -> our value. Deliberately a small, explicit map: an
# unrecognised type is REPORTED, never folded into the common case.
_TYPE_MAP = {
    "depositary receipt": DEPOSITARY_RECEIPT,
    "common stock": ORDINARY_SHARE,
}


@dataclass(frozen=True)
class Verdict:
    value: str          # "" when undecided -- the cell stays blank
    status: str         # ok | primary-listing | no-isin | no-openfigi-coverage
                        # | openfigi-unreachable | ambiguous | unmapped-type
    detail: str = ""


def classify(types) -> Verdict:
    """Map OpenFIGI `securityType2` values to an instrument type.

    `types` follows the fetcher's three-state contract: a list of strings,
    `[]` for "OpenFIGI has no coverage", or `None` for a transient failure.
    """
    if types is None:
        return Verdict("", "openfigi-unreachable")
    if not types:
        return Verdict("", "no-openfigi-coverage")

    mapped = {_TYPE_MAP.get(t.strip().lower()) for t in types if t and t.strip()}
    if None in mapped:
        unknown = sorted(t for t in types if t.strip().lower() not in _TYPE_MAP)
        return Verdict("", "unmapped-type", ", ".join(unknown))
    if len(mapped) > 1:
        return Verdict("", "ambiguous", ", ".join(sorted(types)))
    return Verdict(mapped.pop(), "ok")


def classify_row(row, types) -> Verdict:
    """Decide one universe row, using OpenFIGI only where it is actually needed."""
    listing_type = str(row.get("Listing Type", "") or "").strip()
    # A primary listing is the actual share by definition -- there is no such
    # thing as a primary listing of a receipt. 915 of 1,093 rows land here and
    # cost nothing, which is what keeps a full pass to ~10 requests.
    if listing_type.lower() == "primary":
        return Verdict(ORDINARY_SHARE, "primary-listing")
    if not str(row.get("ISIN", "") or "").strip():
        return Verdict("", "no-isin")
    return classify(types)


@dataclass
class BackfillResult:
    values: dict = field(default_factory=dict)        # ticker -> value to write
    counts: dict = field(default_factory=dict)        # status -> n
    undecided: list = field(default_factory=list)     # tickers left blank
    skipped_populated: int = 0

    def summary(self) -> str:
        parts = [f"{k}={v}" for k, v in sorted(self.counts.items())]
        return (f"{len(self.values)} decided, {len(self.undecided)} undecided, "
                f"{self.skipped_populated} already populated ({', '.join(parts)})")


def needs_lookup(rows):
    """The ISINs actually worth an API call: cross-listed, blank cell, has ISIN."""
    out = []
    for row in rows:
        if str(row.get(COLUMN, "") or "").strip():
            continue
        if str(row.get("Listing Type", "") or "").strip().lower() == "primary":
            continue
        isin = str(row.get("ISIN", "") or "").strip()
        if isin and isin not in out:
            out.append(isin)
    return out


def backfill(rows, *, fetch=None, use_cache=True) -> BackfillResult:
    """Compute `Instrument Type` for every row that does not already have one.

    Fills blanks only. An existing value is never overwritten -- the same rule
    every identifier lane here follows, and it matters more than usual for this
    column because a human may have adjudicated exactly the rows OpenFIGI could
    not.
    """
    if fetch is None:
        from universe.isin_identity import fetch_isin_security_types as fetch

    res = BackfillResult()
    wanted = needs_lookup(rows)
    types_by_isin = fetch(wanted, use_cache=use_cache) if wanted else {}

    for row in rows:
        ticker = str(row.get("Ticker", "") or "").strip()
        if str(row.get(COLUMN, "") or "").strip():
            res.skipped_populated += 1
            continue
        isin = str(row.get("ISIN", "") or "").strip()
        # `.get` would default a never-fetched ISIN to None, which the classifier
        # reads as "unreachable". Only ISINs we actually asked about have an answer.
        verdict = classify_row(row, types_by_isin.get(isin) if isin in types_by_isin else None)
        res.counts[verdict.status] = res.counts.get(verdict.status, 0) + 1
        if verdict.value:
            res.values[ticker] = verdict.value
        else:
            res.undecided.append(ticker)
            if verdict.detail:
                logger.warning("%s: %s (%s)", ticker, verdict.status, verdict.detail)
    return res


# ── CSV layer ────────────────────────────────────────────────────────────────

def ensure_column(df):
    """Add `Instrument Type` immediately after `Listing Type` if missing."""
    if COLUMN in df.columns:
        return df
    idx = (df.columns.get_loc(AFTER_COLUMN) + 1) if AFTER_COLUMN in df.columns \
        else len(df.columns)
    df.insert(idx, COLUMN, "")
    return df


def run(csv_path=None, *, use_cache=True, dry_run=False, fetch=None):
    """Populate the column on the universe CSV. Returns the BackfillResult.

    Reads via `read_universe_csv` (dtype=str) rather than a bare `pd.read_csv`:
    this writes the WHOLE file back, and a bare read turns blank-containing
    integer columns (`CIK`, `Year Listed`) into floats -- `1125376` becomes
    `1125376.0`, which breaks every SEC lookup downstream and corrupts the
    published export. See `tests/test_universe_csv_roundtrip.py`.
    """
    import config
    from ticker_utils import read_universe_csv, write_universe_csv

    csv_path = Path(csv_path) if csv_path else Path(config.CSV_PATH)
    df = read_universe_csv(csv_path)
    df = ensure_column(df)

    rows = df.to_dict("records")
    res = backfill(rows, fetch=fetch, use_cache=use_cache)

    if not dry_run and res.values:
        by_ticker = {str(t).strip(): v for t, v in res.values.items()}
        for i in df.index:
            v = by_ticker.get(str(df.at[i, "Ticker"]).strip())
            if v:
                df.at[i, COLUMN] = v
        write_universe_csv(df, csv_path)
    return res


def main(use_cache=True, dry_run=False):
    res = run(use_cache=use_cache, dry_run=dry_run)
    logger.info("Instrument Type: %s%s", res.summary(),
                " [DRY RUN - nothing written]" if dry_run else "")
    if res.undecided:
        logger.warning("undecided (cell left blank): %s",
                       ", ".join(res.undecided[:30])
                       + (f" ... +{len(res.undecided) - 30} more"
                          if len(res.undecided) > 30 else ""))
    return res
