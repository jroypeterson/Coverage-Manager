"""Tests for the symbol-alias store (`universe/aliases.py`) and the committed file.

Two halves, and the second is the one that earns its keep:

  1. **Unit tests on synthetic files** — every validation rule gets a test that
     fires it, because a validator with no failing case is a comment.
  2. **Tests against the REAL `data/ticker_aliases.json`** — the committed file
     must satisfy its own rules and agree with the live universe. A store that
     only passes on fixtures is the "green test that names a thing that is
     missing" the fleet keeps rediscovering.

Nothing here touches the network. The FI/FISV facts asserted below were measured
on 2026-08-27 and are recorded with their sources in the data file itself.
"""

import json

import pandas as pd
import pytest

from universe.aliases import (
    ALIASES_PATH,
    MIN_SOURCES,
    SCHEMA_VERSION,
    AliasError,
    all_symbols,
    check_universe,
    load_aliases,
    published_payload,
    to_canonical,
    vendor_symbol,
)

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

GOOD_ENTRY = {
    "canonical": "FI",
    "aliases": ["FISV"],
    "company_name": "Fiserv Inc.",
    "cik": "798354",
    "isin": "US3377381088",
    "composite_figi": "BBG000BJKPG0",
    "verified": "2026-08-27",
    "vendor_symbols": {"yfinance": "FISV", "finra": "FI"},
    "sources": ["OpenFIGI 2026-08-27", "Nasdaq Trader directory 2026-08-26"],
}


def write(tmp_path, entries, **top):
    payload = {"schema_version": SCHEMA_VERSION, "entries": entries}
    payload.update(top)
    path = tmp_path / "ticker_aliases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def entry(**overrides):
    e = json.loads(json.dumps(GOOD_ENTRY))
    for key, value in overrides.items():
        if value is _DROP:
            e.pop(key, None)
        else:
            e[key] = value
    return e


class _Drop:
    pass


_DROP = _Drop()


def universe_df(rows):
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# loading and resolution
# --------------------------------------------------------------------------

def test_missing_file_is_an_empty_index_not_an_error(tmp_path):
    """No known splits is the normal state, not a broken install."""
    idx = load_aliases(tmp_path / "does_not_exist.json")
    assert idx["entries"] == []
    assert to_canonical("FISV", idx) == "FISV"


def test_malformed_file_raises_rather_than_resolving_to_nothing(tmp_path):
    """A silently-ignored alias map reads exactly like a working one."""
    path = tmp_path / "ticker_aliases.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(AliasError, match="unreadable"):
        load_aliases(path)


def test_wrong_schema_version_raises(tmp_path):
    path = write(tmp_path, [entry()])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AliasError, match="schema_version"):
        load_aliases(path)


def test_alias_resolves_to_canonical_and_is_case_insensitive(tmp_path):
    idx = load_aliases(write(tmp_path, [entry()]))
    assert to_canonical("FISV", idx) == "FI"
    assert to_canonical("  fisv  ", idx) == "FI"
    assert to_canonical("FI", idx) == "FI"


def test_unaliased_symbol_passes_through(tmp_path):
    """Callers route everything through it, so passthrough must be exact."""
    idx = load_aliases(write(tmp_path, [entry()]))
    assert to_canonical("ABT", idx) == "ABT"
    assert vendor_symbol("ABT", "yfinance", idx) == "ABT"
    assert all_symbols("ABT", idx) == {"ABT"}


def test_vendor_symbol_routes_opposite_directions_for_the_same_issuer(tmp_path):
    """The whole reason vendor_symbols is a map: yfinance and FINRA disagree."""
    idx = load_aliases(write(tmp_path, [entry()]))
    assert vendor_symbol("FI", "yfinance", idx) == "FISV"
    assert vendor_symbol("FI", "finra", idx) == "FI"


def test_vendor_symbol_accepts_an_alias_as_input(tmp_path):
    """A consumer holding the broker's symbol should not have to canonicalize first."""
    idx = load_aliases(write(tmp_path, [entry()]))
    assert vendor_symbol("FISV", "yfinance", idx) == "FISV"
    assert vendor_symbol("FISV", "finra", idx) == "FI"


def test_undeclared_vendor_falls_back_to_THE_CALLERS_symbol(tmp_path):
    """Not to the canonical: that would assert this vendor wants the universe
    spelling, which nothing in the store records, and can turn a symbol the caller
    had working into one the vendor does not have (Codex round 2, on a consumer).
    """
    idx = load_aliases(write(tmp_path, [entry()]))
    assert vendor_symbol("FI", "fmp", idx) == "FI"
    assert vendor_symbol("FISV", "fmp", idx) == "FISV"


def test_all_symbols_returns_both_live_strings(tmp_path):
    idx = load_aliases(write(tmp_path, [entry()]))
    assert all_symbols("FI", idx) == {"FI", "FISV"}
    assert all_symbols("FISV", idx) == {"FI", "FISV"}


# --------------------------------------------------------------------------
# validation rules — one test per rule, each firing it
# --------------------------------------------------------------------------

def test_canonical_declared_twice_is_rejected(tmp_path):
    with pytest.raises(AliasError, match="declared twice"):
        load_aliases(write(tmp_path, [entry(), entry(aliases=["FISERV"])]))


def test_alias_claimed_by_two_entries_is_rejected(tmp_path):
    """Order-dependent resolution is the bug where a join returns another company."""
    other = entry(canonical="ZZZ", aliases=["FISV"])
    with pytest.raises(AliasError, match="already claimed"):
        load_aliases(write(tmp_path, [entry(), other]))


def test_alias_that_is_another_entrys_canonical_is_rejected_in_either_order(tmp_path):
    """A chain FI -> FISV -> X has no defined answer; refuse it, don't pick one."""
    with pytest.raises(AliasError):
        load_aliases(write(tmp_path, [entry(), entry(canonical="FISV", aliases=["FSRV"])]))
    with pytest.raises(AliasError):
        load_aliases(write(tmp_path, [entry(canonical="FISV", aliases=["FSRV"]), entry()]))


def test_canonical_listed_as_its_own_alias_is_rejected(tmp_path):
    with pytest.raises(AliasError, match="its own alias"):
        load_aliases(write(tmp_path, [entry(aliases=["FI", "FISV"])]))


def test_duplicate_alias_within_one_entry_is_rejected(tmp_path):
    with pytest.raises(AliasError, match="duplicate alias"):
        load_aliases(write(tmp_path, [entry(aliases=["FISV", "FISV"])]))


def test_empty_alias_list_is_rejected(tmp_path):
    with pytest.raises(AliasError, match="non-empty"):
        load_aliases(write(tmp_path, [entry(aliases=[])]))


def test_entry_with_no_identity_anchor_is_rejected(tmp_path):
    """An alias with no CIK/ISIN/FIGI is a guess wearing a schema."""
    with pytest.raises(AliasError, match="identity anchor"):
        load_aliases(write(tmp_path, [entry(cik=_DROP, isin=_DROP, composite_figi=_DROP)]))


def test_one_identity_anchor_is_enough(tmp_path):
    idx = load_aliases(write(tmp_path, [entry(isin=_DROP, composite_figi=_DROP)]))
    assert to_canonical("FISV", idx) == "FI"


def test_single_source_is_rejected(tmp_path):
    """One source is how a vendor's own lag becomes a fleet-wide fact."""
    with pytest.raises(AliasError, match=f">= {MIN_SOURCES}"):
        load_aliases(write(tmp_path, [entry(sources=["only OpenFIGI"])]))


def test_missing_verified_date_is_rejected(tmp_path):
    with pytest.raises(AliasError, match="verified"):
        load_aliases(write(tmp_path, [entry(verified=_DROP)]))


def test_unknown_vendor_key_is_rejected(tmp_path):
    """A typo'd vendor would fall back to canonical and reintroduce the 404."""
    with pytest.raises(AliasError, match="unknown vendor"):
        load_aliases(write(tmp_path, [entry(vendor_symbols={"yahoo": "FISV"})]))


def test_vendor_routed_to_an_undeclared_symbol_is_rejected(tmp_path):
    with pytest.raises(AliasError, match="neither the canonical"):
        load_aliases(write(tmp_path, [entry(vendor_symbols={"yfinance": "FSRV"})]))


# --------------------------------------------------------------------------
# check_universe — staleness against the live universe
# --------------------------------------------------------------------------

def test_check_universe_clean_when_row_matches(tmp_path):
    idx = load_aliases(write(tmp_path, [entry()]))
    df = universe_df([{"Ticker": "FI", "Company Name": "Fiserv Inc.", "CIK": "798354",
                       "ISIN": "US3377381088", "Composite FIGI": "BBG000BJKPG0"}])
    assert check_universe(df, idx) == []


def test_check_universe_flags_canonical_missing_from_the_universe(tmp_path):
    idx = load_aliases(write(tmp_path, [entry()]))
    df = universe_df([{"Ticker": "ABT", "Company Name": "Abbott", "CIK": "1800",
                       "ISIN": "", "Composite FIGI": ""}])
    problems = check_universe(df, idx)
    assert len(problems) == 1
    assert "not in the universe" in problems[0]


def test_check_universe_flags_an_alias_that_is_its_own_covered_row(tmp_path):
    """The fatal one: resolving would merge two separately-covered companies."""
    idx = load_aliases(write(tmp_path, [entry()]))
    df = universe_df([
        {"Ticker": "FI", "Company Name": "Fiserv Inc.", "CIK": "798354",
         "ISIN": "US3377381088", "Composite FIGI": "BBG000BJKPG0"},
        {"Ticker": "FISV", "Company Name": "Some Other Issuer", "CIK": "999",
         "ISIN": "", "Composite FIGI": ""},
    ])
    problems = check_universe(df, idx)
    assert any("merge two separately-covered companies" in p for p in problems)


def test_check_universe_flags_a_moved_identity_anchor(tmp_path):
    """If the invariant moved, 'same issuer' is no longer evidenced."""
    idx = load_aliases(write(tmp_path, [entry()]))
    df = universe_df([{"Ticker": "FI", "Company Name": "Fiserv Inc.", "CIK": "111111",
                       "ISIN": "US3377381088", "Composite FIGI": "BBG000BJKPG0"}])
    problems = check_universe(df, idx)
    assert any("identity anchor moved" in p for p in problems)


def test_check_universe_ignores_a_blank_cell_on_either_side(tmp_path):
    """A blank is 'not recorded', not 'recorded as different'."""
    idx = load_aliases(write(tmp_path, [entry()]))
    df = universe_df([{"Ticker": "FI", "Company Name": "Fiserv Inc.", "CIK": "798354",
                       "ISIN": "", "Composite FIGI": None}])
    assert check_universe(df, idx) == []


# --------------------------------------------------------------------------
# the published payload
# --------------------------------------------------------------------------

def test_published_payload_carries_both_resolution_directions(tmp_path):
    payload = published_payload(load_aliases(write(tmp_path, [entry()])))
    assert payload["alias_to_canonical"] == {"FISV": "FI"}
    assert payload["vendor_symbols"]["FI"]["yfinance"] == "FISV"
    assert payload["vendor_symbols"]["FI"]["finra"] == "FI"
    assert payload["entries"][0]["sources"]


def test_published_payload_omits_the_canonical_from_alias_to_canonical(tmp_path):
    """Consumers do `map.get(sym, sym)`; a FI->FI row is noise, not a fact."""
    payload = published_payload(load_aliases(write(tmp_path, [entry()])))
    assert "FI" not in payload["alias_to_canonical"]


# --------------------------------------------------------------------------
# the REAL committed file
# --------------------------------------------------------------------------

def test_committed_file_loads_and_satisfies_its_own_rules():
    idx = load_aliases()
    assert idx["entries"], "the committed alias file should not be empty"


def test_committed_file_agrees_with_the_live_universe():
    import config
    df = pd.read_csv(config.CSV_PATH, dtype=str, encoding="utf-8-sig")
    assert check_universe(df, load_aliases()) == []


def test_committed_file_carries_the_fiserv_split():
    """The case that motivated the store, pinned so a regeneration cannot drop it."""
    idx = load_aliases()
    assert to_canonical("FISV", idx) == "FI"
    assert vendor_symbol("FI", "yfinance", idx) == "FISV"
    assert vendor_symbol("FI", "finra", idx) == "FI"


def test_committed_file_routes_no_vendor_to_a_symbol_no_source_supports():
    """Every vendor_symbols value must appear in at least one source line.

    The evidence and the routing are stored side by side precisely so they can be
    checked against each other; without this, a hand-edit could point yfinance at
    a symbol the sources never mention and every other test would stay green.
    """
    for entry_ in load_aliases()["entries"]:
        blob = " ".join(entry_["sources"])
        for vendor, symbol in entry_["vendor_symbols"].items():
            assert symbol in blob, (
                f"{entry_['canonical']}: {vendor} is routed to {symbol}, which no "
                f"source line mentions")


def test_committed_file_is_utf8_and_ends_with_a_newline():
    text = ALIASES_PATH.read_text(encoding="utf-8")
    assert text.endswith("\n")
    json.loads(text)


# --------------------------------------------------------------------------
# the export step and its acceptance check
# --------------------------------------------------------------------------

def test_export_step_publishes_the_real_alias_content(monkeypatch, tmp_path):
    """The export must carry the committed entries, not just an empty envelope.

    Asserting only that the file EXISTS is the "green test that names a thing
    that is missing" — a step writing `{"entries": []}` every week would pass it
    while every consumer silently lost the join.
    """
    import weekly_universe

    exports = tmp_path / "exports"
    csv_path = tmp_path / "universe.csv"
    csv_path.write_text(
        "Ticker,Company Name,Sector (JP),Subsector (JP),Sub-subsector (JP),Core\n"
        "FI,Fiserv Inc.,Financials,,,\n", encoding="utf-8")
    monkeypatch.setattr(weekly_universe, "CSV_PATH", csv_path)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports)

    weekly_universe._step_export_artifacts(
        {"rows": 1, "errors": [], "warnings": [], "passed": True})

    payload = json.loads((exports / "ticker_aliases.json").read_text(encoding="utf-8"))
    assert payload["alias_to_canonical"].get("FISV") == "FI"
    assert payload["vendor_symbols"]["FI"]["yfinance"] == "FISV"


def test_acceptance_flags_a_map_that_contradicts_its_own_entries(tmp_path):
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "ZZZ"},
        "entries": [{"canonical": "FI", "aliases": ["FISV"]}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("disagrees with entries" in p for p in problems)


def test_acceptance_flags_a_vendor_routed_to_an_undeclared_symbol(tmp_path):
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "FI"},
        "entries": [{"canonical": "FI", "aliases": ["FISV"],
                     "vendor_symbols": {"yfinance": "FSRV"}}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("neither its canonical symbol nor a declared alias" in p for p in problems)


def test_acceptance_flags_a_symbol_that_is_both_canonical_and_alias(tmp_path):
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "FI", "FI": "ZZZ"},
        "entries": [{"canonical": "FI", "aliases": ["FISV"]},
                    {"canonical": "ZZZ", "aliases": ["FI"]}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("order-dependent" in p for p in problems)


def test_acceptance_treats_an_absent_alias_export_as_fine(tmp_path):
    """`map.get(sym, sym)` degrades to today's behaviour; absent is not wrong."""
    from universe.export_acceptance import _check_ticker_aliases
    assert _check_ticker_aliases(tmp_path) == []


def test_acceptance_does_not_raise_on_a_malformed_alias_export(tmp_path):
    """check_exports(strict=False) is what the weekly pipeline calls; it must never crash."""
    from pathlib import Path

    from universe.export_acceptance import check_exports

    (tmp_path / "ticker_aliases.json").write_text("[1, 2, 3]", encoding="utf-8")
    problems = check_exports(Path(tmp_path), strict=False)
    assert any("ticker_aliases.json" in p for p in problems)


# --------------------------------------------------------------------------
# Codex round 1 (2026-08-27) — three defects in the publish path
# --------------------------------------------------------------------------
#
# All three share a shape: `check_universe` and the per-entry validator both
# reported correctly, and the PUBLISH step ignored them. A validator whose finding
# does not reach the artifact is a comment.

def test_a_universe_contradicting_entry_is_EXCLUDED_from_the_export(tmp_path):
    """Warning and publishing anyway is worse than not having the store.

    Rename the canonical row and the published map sends a ticker that IS in the
    universe to one that is not -- a working join broken by the thing meant to fix
    joins. Excluding it degrades that name to passthrough, which is safe.
    """
    idx = load_aliases(write(tmp_path, [entry()]))
    df = universe_df([{"Ticker": "FISV", "Company Name": "Fiserv Inc.", "CIK": "798354",
                       "ISIN": "US3377381088", "Composite FIGI": "BBG000BJKPG0"}])
    payload = published_payload(idx, df=df)
    assert payload["alias_to_canonical"] == {}
    assert payload["entries"] == []


def test_a_clean_entry_still_publishes_when_the_universe_is_passed(tmp_path):
    """The exclusion must not be a blanket silence."""
    idx = load_aliases(write(tmp_path, [entry()]))
    df = universe_df([{"Ticker": "FI", "Company Name": "Fiserv Inc.", "CIK": "798354",
                       "ISIN": "US3377381088", "Composite FIGI": "BBG000BJKPG0"}])
    assert published_payload(idx, df=df)["alias_to_canonical"] == {"FISV": "FI"}


def test_the_export_step_refuses_to_publish_an_empty_map_over_a_full_one(monkeypatch, tmp_path):
    """load-with-fallback + save-everything = data loss, on a Dropbox-synced source.

    A MISSING `data/ticker_aliases.json` is a legitimate empty store, so the loader
    is right to allow it -- but republishing `{}` over a working contract would
    silently un-join every consumer with a green run to show for it.
    """
    import weekly_universe

    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1, "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {}, "entries": []}), encoding="utf-8")

    csv_path = tmp_path / "universe.csv"
    csv_path.write_text("Ticker,Company Name,Sector (JP),Subsector (JP),Sub-subsector (JP),Core\n"
                        "FI,Fiserv Inc.,Financials,,,\n", encoding="utf-8")

    import universe.aliases as aliases_mod
    monkeypatch.setattr(aliases_mod, "ALIASES_PATH", tmp_path / "does_not_exist.json")
    monkeypatch.setattr(weekly_universe, "CSV_PATH", csv_path)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports)

    with pytest.raises(RuntimeError, match="refusing to publish an EMPTY"):
        weekly_universe._step_export_artifacts(
            {"rows": 1, "errors": [], "warnings": [], "passed": True})
    # and the good file is untouched
    kept = json.loads((exports / "ticker_aliases.json").read_text(encoding="utf-8"))
    assert kept["alias_to_canonical"] == {"FISV": "FI"}


def test_an_empty_map_over_an_already_empty_one_is_fine(monkeypatch, tmp_path):
    """The guard must not block the legitimate no-splits state."""
    import weekly_universe

    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1, "alias_to_canonical": {}, "vendor_symbols": {},
        "entries": []}), encoding="utf-8")
    csv_path = tmp_path / "universe.csv"
    csv_path.write_text("Ticker,Company Name,Sector (JP),Subsector (JP),Sub-subsector (JP),Core\n"
                        "FI,Fiserv Inc.,Financials,,,\n", encoding="utf-8")

    import universe.aliases as aliases_mod
    monkeypatch.setattr(aliases_mod, "ALIASES_PATH", tmp_path / "does_not_exist.json")
    monkeypatch.setattr(weekly_universe, "CSV_PATH", csv_path)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports)

    weekly_universe._step_export_artifacts(
        {"rows": 1, "errors": [], "warnings": [], "passed": True})
    assert json.loads((exports / "ticker_aliases.json").read_text(encoding="utf-8"))[
        "alias_to_canonical"] == {}


def test_acceptance_flags_a_top_level_vendor_map_that_contradicts_its_entry(tmp_path):
    """The top-level map is what consumers READ; the per-entry check never saw it."""
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {"FI": {"yfinance": "FI"}},          # <- entry says FISV
        "entries": [{"canonical": "FI", "aliases": ["FISV"],
                     "vendor_symbols": {"yfinance": "FISV"}}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("the routing map and its own evidence disagree" in p for p in problems)


def test_acceptance_flags_a_top_level_vendor_symbol_nothing_declares(tmp_path):
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {"FI": {"yfinance": "FSRV"}},
        "entries": [{"canonical": "FI", "aliases": ["FISV"]}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("neither FI nor a declared alias" in p for p in problems)


def test_acceptance_flags_a_top_level_vendor_map_for_an_unknown_canonical(tmp_path):
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {"ZZZ": {"yfinance": "ZZZ"}},
        "entries": [{"canonical": "FI", "aliases": ["FISV"]}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("not a canonical symbol in entries" in p for p in problems)


# --------------------------------------------------------------------------
# Codex round 2 (2026-08-27) — three defects, two of them IN round 1's fixes
# --------------------------------------------------------------------------

def test_an_excluded_entry_publishes_an_EMPTY_map_rather_than_raising(monkeypatch, tmp_path):
    """Round 1's empty-publish guard misread a CORRECT exclusion as source loss.

    Rename the canonical row: `published_payload` rightly drops the entry and
    returns {}, the guard read that as "the curated file vanished", raised, and
    left the STALE export beside a universe.csv / metadata / status this same
    function had already rewritten — an internally contradictory artifact set,
    worse than either outcome it was choosing between.
    """
    import weekly_universe

    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1, "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {}, "entries": []}), encoding="utf-8")

    csv_path = tmp_path / "universe.csv"
    csv_path.write_text("Ticker,Company Name,Sector (JP),Subsector (JP),Sub-subsector (JP),Core\n"
                        "FISV,Fiserv Inc.,Financials,,,\n", encoding="utf-8")   # renamed!

    src = tmp_path / "ticker_aliases.json"
    src.write_text(json.dumps({"schema_version": 1, "entries": [entry()]}), encoding="utf-8")
    import universe.aliases as aliases_mod
    monkeypatch.setattr(aliases_mod, "ALIASES_PATH", src)
    monkeypatch.setattr(weekly_universe, "CSV_PATH", csv_path)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports)

    weekly_universe._step_export_artifacts(
        {"rows": 1, "errors": [], "warnings": [], "passed": True})
    published = json.loads((exports / "ticker_aliases.json").read_text(encoding="utf-8"))
    assert published["alias_to_canonical"] == {}
    assert "excluded_count" not in published, "build-local, not part of the contract"


def test_acceptance_flags_an_entry_whose_vendor_map_never_reached_the_top_level(tmp_path):
    """A one-way loop over a container that can be EMPTY executes zero times.

    An entry declaring `yfinance: FISV` beside `vendor_symbols: {}` returned no
    problems, so consumers ask for the canonical, get the documented 404, and the
    gate reports green.
    """
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {},
        "entries": [{"canonical": "FI", "aliases": ["FISV"],
                     "vendor_symbols": {"yfinance": "FISV"}}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("has no entry for it" in p for p in problems)


def test_acceptance_flags_a_top_level_map_missing_ONE_declared_vendor(tmp_path):
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {"FI": {"yfinance": "FISV"}},
        "entries": [{"canonical": "FI", "aliases": ["FISV"],
                     "vendor_symbols": {"yfinance": "FISV", "finra": "FI"}}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("is missing ['finra']" in p for p in problems)


def test_acceptance_flags_an_EXTRA_route_the_entry_does_not_declare(tmp_path):
    """Codex round 3: "bidirectional" was a claim before it was true.

    v1 compared only keys MISSING from the top-level map, and the per-vendor loop
    defaults an undeclared vendor's expected value to itself -- so an extra route
    was hidden twice over and consumers would send a symbol no evidence supports.
    """
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {"FI": {"yfinance": "FISV", "finra": "FISV"}},
        "entries": [{"canonical": "FI", "aliases": ["FISV"],
                     "vendor_symbols": {"yfinance": "FISV"}}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("which the entry does not declare" in p for p in problems)


def test_the_empty_map_refusal_happens_BEFORE_any_artifact_is_written(monkeypatch, tmp_path):
    """Codex round 3: the guard fired after universe.csv, metadata and status were
    already overwritten, leaving the NEW universe beside the STALE alias map --
    consumers resolving a live ticker onto one that no longer exists. A refusal
    halfway through a multi-file publish turns one bad artifact into an
    inconsistent SET.
    """
    import weekly_universe

    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1, "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {}, "entries": []}), encoding="utf-8")
    (exports / "universe.csv").write_text("Ticker\nOLD\n", encoding="utf-8")

    csv_path = tmp_path / "universe.csv"
    csv_path.write_text("Ticker,Company Name,Sector (JP),Subsector (JP),Sub-subsector (JP),Core\n"
                        "FI,Fiserv Inc.,Financials,,,\n", encoding="utf-8")

    import universe.aliases as aliases_mod
    monkeypatch.setattr(aliases_mod, "ALIASES_PATH", tmp_path / "gone.json")
    monkeypatch.setattr(weekly_universe, "CSV_PATH", csv_path)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports)

    with pytest.raises(RuntimeError, match="NOTHING has been written"):
        weekly_universe._step_export_artifacts(
            {"rows": 1, "errors": [], "warnings": [], "passed": True})

    assert (exports / "universe.csv").read_text(encoding="utf-8") == "Ticker\nOLD\n", \
        "the refusal must leave every artifact untouched, not just the alias map"
    assert not (exports / "universe_status.json").exists()


def test_acceptance_flags_an_extra_route_when_the_entry_declares_NONE(tmp_path):
    """Codex round 4: filtering empty declarations out of the comparison dropped it
    entirely for exactly the entries where an extra route is least supported.

    An empty declaration is not a declaration to skip -- the same mistake as
    iterating a container that can be empty, one level up.
    """
    from universe.export_acceptance import _check_ticker_aliases

    (tmp_path / "ticker_aliases.json").write_text(json.dumps({
        "schema_version": 1,
        "alias_to_canonical": {"FISV": "FI"},
        "vendor_symbols": {"FI": {"yfinance": "FISV"}},
        "entries": [{"canonical": "FI", "aliases": ["FISV"], "vendor_symbols": {}}],
    }), encoding="utf-8")
    problems = _check_ticker_aliases(tmp_path)
    assert any("does not declare" in p for p in problems)
