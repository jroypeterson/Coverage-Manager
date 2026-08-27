"""Symbol aliases: one issuer, several live ticker strings, and which one each vendor answers to.

## Why this exists

A ticker is a label, not an identity, and for a handful of issuers the fleet's
sources do not agree on the label **right now** — not "one is stale and will
catch up", but a standing disagreement with no automated authority to settle it.

Fiserv is the worked example and the reason this module exists. Measured
2026-08-27, every one of these is current:

| Source                                    | Says  |
|-------------------------------------------|-------|
| `data/coverage_universe_tickers.csv`      | `FI`  |
| API Ninjas insider feed (CIK 798354)      | `FI`  |
| OpenFIGI, keyed on the row's own ISIN     | `FISV`|
| Nasdaq Trader exchange directory (12/12 snapshots) | `FISV`|
| SEC `company_tickers.json` (CIK 798354)   | `FISV`|
| yfinance (`FI` → HTTP 404)                | `FISV`|
| IBKR position feed                        | `FISV`|

The identity did **not** change: CIK `798354`, ISIN `US3377381088`, composite
FIGI `BBG000BJKPG0` are identical on both sides and sit unused in the export
already. Only the label moved, and only for some sources.

## What this is NOT

**Not a vote on which symbol is "correct".** `ticker_change_check` deliberately
refuses to auto-classify direction because no source reliably says which symbol
is current, and picking a winner here would just relocate that guess. This module
records that two strings are **one issuer**, anchored to the identifiers that did
not change, plus **which string to hand each vendor** — which is the only thing
consumers actually need.

**Not a place to fix a data-entry error.** If a row's ticker is simply wrong and
nothing outside Coverage Manager ever used it, correct the row and record it in
`identity_provenance.json`. An alias entry asserts that *both* strings are live
in the wild; `require_alias_is_not_a_universe_ticker` and the two-source bar
below exist so this cannot quietly become a dumping ground for typos.

**Not fuzzy.** Curated and committed, never matched at runtime — the fleet's
standing rule after `SHL` resolved to Siemens AG and `PHM` to the wrong
homebuilder.

## What it fixes

Two published surfaces were corrupted by the split, silently and in opposite
directions:

  * `insider_ownership` asked yfinance for `FI` and got nothing, so every Fiserv
    insider stake on the standing tearsheet rendered as a share count with no
    dollar value.
  * `portfolio_daily` could not join the broker's `FISV` to Coverage Manager's
    `FI`, so the public portfolio page invented a sector bucket named
    `Unclassified` holding exactly one name.

Both are joins, and both are fixed by resolving through this file rather than by
rewriting either side's documents — the repair belongs in the consumer.

## Using it

    from universe.aliases import load_aliases, to_canonical, vendor_symbol

    idx = load_aliases()
    to_canonical("FISV", idx)              # -> "FI"     (broker → coverage)
    vendor_symbol("FI", "yfinance", idx)   # -> "FISV"   (coverage → vendor)
    vendor_symbol("FI", "api_ninjas", idx) # -> "FI"     (declared, unchanged)
    vendor_symbol("ABT", "yfinance", idx)  # -> "ABT"    (not aliased, passthrough)

Consumers outside this repo read the published `exports/ticker_aliases.json`
instead of importing this module; the shape is identical.
"""
from __future__ import annotations

import json
from pathlib import Path

import config
from logging_utils import get_logger

logger = get_logger(__name__)

ALIASES_PATH = Path(config.DATA_DIR) / "ticker_aliases.json"
SCHEMA_VERSION = 1

#: Identity fields an entry may pin, mapped to the universe column they must
#: agree with. At least one is required: an alias with no identity anchor is a
#: guess wearing a schema, and this whole file exists because the *identity* is
#: the thing that did not change.
IDENTITY_FIELDS = {
    "cik": "CIK",
    "isin": "ISIN",
    "composite_figi": "Composite FIGI",
}

#: Vendors a consumer may ask about. A closed set on purpose — a typo'd vendor
#: key would silently fall back to the canonical symbol and reintroduce exactly
#: the 404 this module exists to stop.
KNOWN_VENDORS = frozenset({
    "yfinance", "openfigi", "sec_company_tickers", "nasdaq_directory",
    "finra", "fmp", "alphavantage", "api_ninjas", "ibkr", "fidelity",
    "fiscal_ai", "finnhub",
})

#: Two independent sources to assert a split, the same bar `identity_provenance`
#: sets for overwriting an identifier. One source is how a vendor's own lag gets
#: promoted to a fleet-wide fact.
MIN_SOURCES = 2


class AliasError(ValueError):
    """The alias file is malformed, or an entry contradicts the universe."""


def _sym(value) -> str:
    return str(value or "").strip().upper()


def _cell(value) -> str:
    """Normalize a universe cell for comparison; NaN and blanks collapse to ''."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def load_aliases(path=None) -> dict:
    """Read and structurally validate the alias file.

    Returns an index:

        {"schema_version": int,
         "entries": [entry, ...],
         "by_alias": {ALIAS: entry},        # every alias, canonical excluded
         "by_canonical": {CANON: entry}}

    A missing file is not an error — it is the normal state for a fleet with no
    known splits — and yields an empty index. A file that exists but is
    malformed raises: a silently-ignored alias map reads exactly like a working
    one right up until a join drops a holding.
    """
    p = Path(path) if path else ALIASES_PATH
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "entries": [],
                "by_alias": {}, "by_canonical": {}}

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AliasError(f"{p.name}: unreadable ({exc})") from exc

    if not isinstance(raw, dict):
        raise AliasError(f"{p.name}: top level must be an object")

    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise AliasError(
            f"{p.name}: schema_version {version!r}, this code speaks {SCHEMA_VERSION}")

    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise AliasError(f"{p.name}: 'entries' must be a list")

    by_alias: dict[str, dict] = {}
    by_canonical: dict[str, dict] = {}

    for i, entry in enumerate(entries):
        where = f"{p.name} entry {i}"
        if not isinstance(entry, dict):
            raise AliasError(f"{where}: must be an object")

        canonical = _sym(entry.get("canonical"))
        if not canonical:
            raise AliasError(f"{where}: 'canonical' is required")
        if canonical in by_canonical:
            raise AliasError(f"{where}: canonical {canonical} declared twice")

        aliases = entry.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            raise AliasError(f"{where} ({canonical}): 'aliases' must be a non-empty list")
        aliases = [_sym(a) for a in aliases]
        if canonical in aliases:
            raise AliasError(
                f"{where} ({canonical}): canonical symbol is listed as its own alias")
        if len(set(aliases)) != len(aliases):
            raise AliasError(f"{where} ({canonical}): duplicate alias in the list")

        # An alias may not be another entry's canonical, and no alias may be
        # claimed twice. Either would make resolution order-dependent — the
        # class of bug where a join silently returns a DIFFERENT company.
        for alias in aliases:
            if alias in by_alias:
                raise AliasError(
                    f"{where}: alias {alias} is already claimed by "
                    f"{by_alias[alias]['canonical']}")
            if alias in by_canonical:
                raise AliasError(
                    f"{where}: alias {alias} is another entry's canonical symbol")

        identity = {k: _cell(entry.get(k)) for k in IDENTITY_FIELDS}
        if not any(identity.values()):
            raise AliasError(
                f"{where} ({canonical}): needs at least one of "
                f"{sorted(IDENTITY_FIELDS)} — an alias with no identity anchor is a guess")

        sources = entry.get("sources")
        if not isinstance(sources, list) or len(sources) < MIN_SOURCES:
            raise AliasError(
                f"{where} ({canonical}): needs >= {MIN_SOURCES} independent sources, "
                f"got {len(sources) if isinstance(sources, list) else 0}")

        if not _cell(entry.get("verified")):
            raise AliasError(f"{where} ({canonical}): 'verified' date is required")

        vendors = entry.get("vendor_symbols") or {}
        if not isinstance(vendors, dict):
            raise AliasError(f"{where} ({canonical}): 'vendor_symbols' must be an object")
        allowed = {canonical, *aliases}
        for vendor, symbol in vendors.items():
            if vendor not in KNOWN_VENDORS:
                raise AliasError(
                    f"{where} ({canonical}): unknown vendor {vendor!r} — add it to "
                    f"KNOWN_VENDORS so a typo cannot silently fall back to {canonical}")
            if _sym(symbol) not in allowed:
                raise AliasError(
                    f"{where} ({canonical}): vendor {vendor} routed to {symbol!r}, "
                    f"which is neither the canonical symbol nor a declared alias")

        normalized = dict(entry)
        normalized["canonical"] = canonical
        normalized["aliases"] = aliases
        normalized["vendor_symbols"] = {v: _sym(s) for v, s in vendors.items()}

        by_canonical[canonical] = normalized
        for alias in aliases:
            by_alias[alias] = normalized

    # Deferred to the end so the message can name the offender in either order:
    # an entry whose canonical is a LATER entry's alias is just as ambiguous.
    for canonical in by_canonical:
        if canonical in by_alias:
            raise AliasError(
                f"{p.name}: {canonical} is both a canonical symbol and "
                f"{by_alias[canonical]['canonical']}'s alias")

    return {"schema_version": version,
            "entries": [by_canonical[c] for c in by_canonical],
            "by_alias": by_alias,
            "by_canonical": by_canonical}


def to_canonical(symbol: str, index=None) -> str:
    """Map any known symbol for an issuer to the Coverage Manager ticker.

    Passthrough for anything unaliased, so a caller can route every symbol
    through it unconditionally. Case- and whitespace-insensitive; the value
    returned is always the canonical string as the universe spells it.
    """
    idx = index if index is not None else load_aliases()
    sym = _sym(symbol)
    entry = idx["by_alias"].get(sym)
    return entry["canonical"] if entry else sym


def vendor_symbol(ticker: str, vendor: str, index=None) -> str:
    """The symbol to send `vendor` when you mean `ticker`.

    `ticker` may be the canonical symbol or any alias — both name the same
    issuer, and a caller holding a broker symbol should not have to canonicalize
    first.

    ⛑ **An unrouted vendor gets the CALLER'S OWN symbol, never the canonical.**
    Returning the canonical asserts "this vendor wants the universe spelling",
    which the store does not record and cannot support, and it is strictly worse
    than doing nothing: it can turn a symbol the caller had working into one the
    vendor does not have. Passthrough is the floor. A consumer hit exactly this
    (`portfolio_daily`, Codex round 2, 2026-08-27) — a malformed vendor map left
    the alias half intact, `FISV` canonicalised to `FI`, and a holding silently
    vanished from a published page.
    """
    idx = index if index is not None else load_aliases()
    sym = _sym(ticker)
    entry = idx["by_canonical"].get(sym) or idx["by_alias"].get(sym)
    if not entry:
        return sym
    return entry["vendor_symbols"].get(vendor, sym)


def all_symbols(ticker: str, index=None) -> set[str]:
    """Every string in the wild for this issuer, canonical included.

    For the membership tests that read `if t in some_vendor_frame` — a set
    intersection is the honest question when two live symbols exist.
    """
    idx = index if index is not None else load_aliases()
    sym = _sym(ticker)
    entry = idx["by_canonical"].get(sym) or idx["by_alias"].get(sym)
    if not entry:
        return {sym}
    return {entry["canonical"], *entry["aliases"]}


def check_universe(df, index=None) -> list[str]:
    """Assert every entry still agrees with the universe. Returns problem strings.

    Three ways an entry goes stale, and each is a different failure:

      1. **Canonical is not in the universe** — the row was renamed or dropped
         and the entry now maps a live broker symbol onto nothing.
      2. **An alias IS in the universe as its own row** — the fatal one. Two
         separately-covered companies would collapse into one, which is the
         `ROG`/`ROG.SW` accident with a config file behind it.
      3. **A pinned identifier disagrees with the row** — the anchor moved, so
         the claim "same issuer" is no longer evidenced by the thing that was
         supposed to be invariant.

    Non-gating by design, like `provenance.check_universe`: it reports, the
    weekly run surfaces it, a human adjudicates. Blocking the whole export on a
    stale alias would take the universe down over a cosmetic disagreement.
    """
    idx = index if index is not None else load_aliases()
    if not idx["entries"]:
        return []

    rows = {}
    for _, row in df.iterrows():
        ticker = _sym(row.get("Ticker"))
        if ticker:
            rows[ticker] = row

    problems: list[str] = []
    for entry in idx["entries"]:
        canonical = entry["canonical"]
        row = rows.get(canonical)
        if row is None:
            problems.append(
                f"alias entry {canonical}: canonical ticker is not in the universe "
                f"(aliases {', '.join(entry['aliases'])} now resolve to nothing)")
            continue

        for alias in entry["aliases"]:
            if alias in rows:
                problems.append(
                    f"alias entry {canonical}: alias {alias} is ALSO a universe row "
                    f"({_cell(rows[alias].get('Company Name'))}) — resolving through this "
                    f"entry would merge two separately-covered companies")

        for field, column in IDENTITY_FIELDS.items():
            pinned = _cell(entry.get(field))
            actual = _cell(row.get(column))
            if pinned and actual and pinned.upper() != actual.upper():
                problems.append(
                    f"alias entry {canonical}: pinned {field} {pinned} != universe "
                    f"{column} {actual} — the identity anchor moved, so 'same issuer' "
                    f"is no longer evidenced")

    return problems


def published_payload(index=None, df=None) -> dict:
    """The `exports/ticker_aliases.json` body.

    Consumers get resolution maps they can use without reimplementing the walk:
    `alias_to_canonical` for the inbound join, `vendor_symbols` for the outbound
    call. `entries` carries the evidence so a consumer can show its work.

    ⛑ **Pass `df` (the universe) and a contradicted entry is EXCLUDED, loudly.**
    Without it, `check_universe` only ever warned while this function published the
    entry anyway — and the dangerous case is not a cosmetic disagreement. Rename the
    canonical row `FI` to `FISV` without updating the store and the published map
    says `FISV -> FI`, so every consumer takes a ticker that IS in the universe and
    resolves it to one that is not: a working join turned into a broken one by the
    thing meant to fix joins. Excluding it degrades that name to passthrough, which
    is the pre-store behaviour and safe. `df=None` skips the check and is for
    callers that genuinely have no universe to check against (tests).
    """
    idx = index if index is not None else load_aliases()
    if df is not None:
        problems = check_universe(df, idx)
        if problems:
            bad = {p.split(":", 1)[0].replace("alias entry ", "").strip()
                   for p in problems if p.startswith("alias entry ")}
            kept = [e for e in idx["entries"] if e["canonical"] not in bad]
            logger.warning(
                "ticker aliases: EXCLUDING %d entry(ies) that contradict the universe "
                "from the published export (%s) - they would resolve a live universe "
                "ticker onto one that is not there. Fix data/ticker_aliases.json; "
                "problems: %s", len(idx["entries"]) - len(kept), ", ".join(sorted(bad)),
                "; ".join(problems[:4]))
            excluded = len(idx["entries"]) - len(kept)
            idx = {"schema_version": idx["schema_version"],
                   "entries": kept,
                   "by_canonical": {e["canonical"]: e for e in kept},
                   "by_alias": {a: e for e in kept for a in e["aliases"]}}
    payload_excluded = locals().get("excluded", 0)
    return {
        "schema_version": idx["schema_version"],
        "description": (
            "Issuers whose ticker string differs between sources, anchored to the "
            "identifiers that did NOT change (CIK / ISIN / composite FIGI). "
            "`alias_to_canonical` maps any known symbol to the coverage universe "
            "ticker; `vendor_symbols` gives the symbol to send a named vendor. "
            "This file does not assert which symbol is 'current' — no source "
            "reliably says, which is why the join is aliased rather than rewritten."
        ),
        "alias_to_canonical": {
            alias: entry["canonical"]
            for alias, entry in sorted(idx["by_alias"].items())
        },
        "vendor_symbols": {
            entry["canonical"]: entry["vendor_symbols"]
            for entry in idx["entries"] if entry["vendor_symbols"]
        },
        "entries": idx["entries"],
        # How many entries this build DROPPED for contradicting the universe.
        # The publish guard needs it to tell "the curated source vanished" (refuse,
        # keep the good published file) from "every entry was correctly excluded"
        # (publish the empty map — it is the right answer). Without the
        # distinction the guard raised on a correct exclusion and left a STALE
        # export beside a freshly-rewritten universe.csv (Codex round 2).
        "excluded_count": payload_excluded,
    }
