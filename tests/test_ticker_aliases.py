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


def test_undeclared_vendor_falls_back_to_canonical(tmp_path):
    idx = load_aliases(write(tmp_path, [entry()]))
    assert vendor_symbol("FI", "fmp", idx) == "FI"


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
