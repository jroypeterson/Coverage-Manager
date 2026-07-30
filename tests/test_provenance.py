"""The provenance ledger, and the ONE test that replaces per-incident pins.

Before this, each identity fix minted its own dated test file full of literal
cell values. That has two costs an architectural review named precisely:

  1. A legitimate corporate action breaks the test, and the fix is to edit the
     assertion — which trains exactly the habit that lets wrong data live.
  2. Every future fix session mints another file.

`data/identity_provenance.json` holds the adjudicated facts as DATA. A stale
entry is corrected by editing that file with new evidence and a new date, which
is auditable; editing an assertion is not.
"""
import json

import pytest

from ticker_utils import read_universe_csv
from universe import provenance as P


@pytest.fixture(scope="module")
def ledger():
    return P.load_ledger()


# ── THE test ────────────────────────────────────────────────────────────────

def test_the_universe_agrees_with_every_verified_cell(ledger):
    """One assertion covering every adjudicated identity fact.

    Replaces ~25 literal pins across three dated files. It also derives the
    generalised `ZEN` rule — a superseded identifier must not reappear on ANY row
    — from the ledger, instead of a hand-maintained set that someone must
    remember to extend.
    """
    problems = P.check_universe(read_universe_csv(), ledger)
    assert problems == [], "\n".join(problems)


# ── schema is enforced, not assumed ─────────────────────────────────────────

def test_the_shipped_ledger_is_valid_and_non_trivial(ledger):
    assert len(ledger["entries"]) >= 30
    assert ledger["removals"] and ledger["renames"]
    # `held` is deliberately NOT required to be non-empty. An empty held list is
    # the GOOD state -- it means every known conflict has been settled, which
    # became true on 2026-07-30 when the last three (2715.HK, CPH, MDLA) were
    # resolved. A test that required a permanent backlog would have to be
    # weakened every time the backlog was cleared.
    assert isinstance(ledger["held"], list)


def _write(tmp_path, obj):
    p = tmp_path / "led.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _entry(**over):
    e = {"ticker": "AAA", "field": "ISIN", "value": "US0000000000",
         "verified": "2026-07-30", "sources": ["one", "two"]}
    e.update(over)
    return e


def test_one_source_needs_a_written_reason(tmp_path):
    """The repo's bar for overwriting an identifier is two independent sources.
    One is allowed — a FIGI has only OpenFIGI — but it must be argued, not
    slipped in."""
    bad = {"schema_version": 1, "entries": [_entry(sources=["only one"])]}
    with pytest.raises(P.LedgerError, match="single_source_reason"):
        P.load_ledger(_write(tmp_path, bad))
    ok = {"schema_version": 1, "entries": [
        _entry(sources=["only one"], single_source_reason="FIGIs have one registry")]}
    assert len(P.load_ledger(_write(tmp_path, ok))["entries"]) == 1


def test_two_entries_for_one_cell_are_refused(tmp_path):
    """Ambiguity about 'the verified value' would let one silently shadow the
    other."""
    bad = {"schema_version": 1, "entries": [_entry(), _entry(value="US1111111111")]}
    with pytest.raises(P.LedgerError, match="duplicate entry"):
        P.load_ledger(_write(tmp_path, bad))


def test_a_cell_cannot_be_both_verified_and_held(tmp_path):
    bad = {"schema_version": 1, "entries": [_entry()],
           "held": [{"ticker": "AAA", "field": "ISIN", "value": "X",
                     "reviewed": "2026-07-30", "why_unresolved": "n/a"}]}
    with pytest.raises(P.LedgerError, match="settled and unresolved"):
        P.load_ledger(_write(tmp_path, bad))


def test_an_untracked_field_is_refused(tmp_path):
    """Catches a typo (`ISNI`) that would otherwise pin nothing while looking
    like it pinned something."""
    bad = {"schema_version": 1, "entries": [_entry(field="ISNI")]}
    with pytest.raises(P.LedgerError, match="TRACKED_FIELDS"):
        P.load_ledger(_write(tmp_path, bad))


def test_delisted_must_be_a_real_boolean(tmp_path):
    """`ZEN`/`ALBT` were scope removals; `ICAD` was a genuine delisting. A string
    "false" would read as truthy and collapse the distinction."""
    bad = {"schema_version": 1, "entries": [],
           "removals": [{"ticker": "ZZZ", "verified": "2026-07-30",
                         "reason": "r", "delisted": "false"}]}
    with pytest.raises(P.LedgerError, match="real\n?\\s*boolean|real boolean"):
        P.load_ledger(_write(tmp_path, bad))


def test_a_wrong_schema_version_is_refused(tmp_path):
    with pytest.raises(P.LedgerError, match="schema_version"):
        P.load_ledger(_write(tmp_path, {"schema_version": 99, "entries": []}))


@pytest.mark.parametrize("bad,match", [
    ({"schema_version": 1, "entries": "nope"}, "must be a list"),
    ({"schema_version": 1, "entries": [{"ticker": "A"}]}, "missing"),
    ({"schema_version": 1, "entries": [_entry(verified="July 30")]}, "ISO"),
])
def test_malformed_ledgers_raise_loudly(tmp_path, bad, match):
    with pytest.raises(P.LedgerError, match=match):
        P.load_ledger(_write(tmp_path, bad))


# ── drift detection actually detects ────────────────────────────────────────

def test_drift_is_reported_naming_both_values(ledger):
    import pandas as pd
    df = pd.DataFrame([{"Ticker": "CBIO", "ISIN": "US38000Q1022"}])
    problems = P.check_universe(df, {**ledger, "removals": [], "renames": [],
                                     "held": [],
                                     "entries": [e for e in ledger["entries"]
                                                 if e["ticker"] == "CBIO"]})
    assert any("US38000Q1022" in p and "US38000Q2012" in p for p in problems), problems


def test_a_removed_row_coming_back_is_reported(ledger):
    import pandas as pd
    df = pd.DataFrame([{"Ticker": "ICAD", "ISIN": ""}])
    problems = P.check_universe(df, {**ledger, "entries": [], "renames": [],
                                     "held": []})
    assert any("ICAD" in p and "back in the universe" in p for p in problems)


def test_an_old_ticker_surviving_a_rename_is_reported(ledger):
    import pandas as pd
    df = pd.DataFrame([{"Ticker": "FGEN", "ISIN": ""}, {"Ticker": "KYNB", "ISIN": ""}])
    problems = P.check_universe(df, {**ledger, "entries": [], "removals": [],
                                     "held": [],
                                     "renames": [r for r in ledger["renames"]
                                                 if r["from"] == "FGEN"]})
    assert any("OLD ticker is still" in p for p in problems), problems


# ── triage: the point of recording provenance at all ────────────────────────

def test_triage_verified_cell_says_suspect_the_vendor(ledger):
    """`KYNB` is the canonical case: the audit called its ISIN a conflict and the
    ISIN was RIGHT — the register had moved on. Auto-correcting would have
    overwritten a valid identifier."""
    verdict, why = P.triage(ledger, "KYNB", "ISIN", "KYNTRA BIO INC")
    assert verdict == P.ROW_VERIFIED
    assert "VENDOR" in why


def test_triage_unverified_cell_says_suspect_the_row(ledger):
    verdict, why = P.triage(ledger, "AAPL", "ISIN", "whatever")
    assert verdict == P.ROW_UNVERIFIED
    assert "ROW" in why


def test_triage_recognises_a_value_we_already_rejected(ledger):
    """The audit re-offering `US38000Q1022` for CBIO is it re-finding a decision
    already made — that must not reach a human a second time."""
    verdict, why = P.triage(ledger, "CBIO", "ISIN", "US38000Q1022")
    assert verdict == P.ROW_SUPERSEDED
    assert "already made" in why


def test_triage_recognises_a_held_dead_end():
    """'We looked and could not settle it' is expensive knowledge. Without it the
    next audit re-derives the same dead end.

    Uses a SYNTHETIC ledger rather than a live held row. The original version
    keyed off `2715.HK`, which was genuinely held until 2026-07-30 -- so
    resolving it broke a test of the triage MECHANISM, which has nothing to do
    with whether anything is currently held. Coupling a mechanism test to live
    data state is what made clearing the backlog look like a regression."""
    synthetic = {"entries": [], "removals": [], "renames": [],
                 "held": [{"ticker": "ZZZ", "field": "ISIN", "value": "XX0000000000",
                           "reviewed": "2026-07-30",
                           "why_unresolved": "neither source produced a candidate."}]}
    verdict, why = P.triage(synthetic, "ZZZ", "ISIN", "SOMEONE ELSE")
    assert verdict == P.ROW_HELD
    assert "could NOT be settled" in why


def test_every_triage_verdict_is_actionable_prose(ledger):
    """A verdict a reader cannot act on is a verdict that gets ignored."""
    for tkr, field in (("KYNB", "ISIN"), ("AAPL", "ISIN"), ("CPH", "ISIN")):
        _v, why = P.triage(ledger, tkr, field, "x")
        assert len(why) > 40 and why.strip().endswith((".", "!"))


# ── the facts that MUST be in the ledger, not just somewhere ────────────────

@pytest.mark.parametrize("ticker,field,value", [
    ("CBIO", "ISIN", "US38000Q2012"),
    ("EVO", "ISIN", "US30050E1055"),
    ("CSU", "ISIN", "CA21037X1006"),
    ("KYNB", "CIK", "921299"),
    ("MED", "CIK", ""),           # blank IS the verified value
    ("CSL", "Currency", "AUD"),
    ("MICC", "Country (HQ)", "Netherlands"),
])
def test_the_hard_won_facts_are_recorded(ledger, ticker, field, value):
    """Spot-check that the migration from literal pins did not silently drop a
    fact. Each of these cost real verification work."""
    assert P.verified_value(ledger, ticker, field) == value


def test_blank_is_a_legitimate_verified_value(ledger):
    """`MED`/`MOVE` have NO EDGAR record, so blank is the correct CIK — not
    another CIK. A ledger that could not express "verified empty" would leave the
    most dangerous cells unpinned."""
    for t in ("MED", "MOVE"):
        assert P.verified_value(ledger, t, "CIK") == ""


# ── it runs on the weekly, not only in tests ────────────────────────────────

def test_the_ledger_check_is_in_run_all_validations():
    """A ledger checked only by pytest protects the test suite, not the data. It
    has to run where the universe is actually built."""
    import inspect

    from universe import validation
    src = inspect.getsource(validation.run_all_validations)
    assert "validate_against_provenance_ledger" in src


def test_validator_is_quiet_on_the_live_universe():
    from universe.validation import validate_against_provenance_ledger
    assert validate_against_provenance_ledger(read_universe_csv()) == []


def test_validator_reports_that_it_COULD_NOT_check(monkeypatch):
    """'I could not check' must never read as 'I checked and it is fine' — the
    rule this repo has paid for repeatedly."""
    from universe import validation

    def boom():
        raise P.LedgerError("ledger is corrupt")

    monkeypatch.setattr("universe.provenance.load_ledger", boom)
    out = validation.validate_against_provenance_ledger(read_universe_csv())
    assert len(out) == 1
    assert "NOT checked" in out[0] and "corrupt" in out[0]


def test_validator_warns_rather_than_errors_on_drift():
    """Drift is ambiguous — a regression OR a real corporate action — so it must
    surface for a human, not gate the build."""
    import pandas as pd

    from universe.validation import run_all_validations
    df = pd.DataFrame([{c: "" for c in read_universe_csv().columns}])
    df.loc[0, "Ticker"] = "CBIO"
    df.loc[0, "ISIN"] = "US38000Q1022"          # the superseded value
    errors, warnings = run_all_validations(df)
    assert not any("provenance" in e or "verified" in e for e in errors)
    assert any("verified" in w for w in warnings)


def test_comparison_tolerates_the_bare_read_float_artifacts():
    """`cli validate` uses a bare pd.read_csv, which renders a correct CIK as
    `921299.0` and a blank as `nan`. Those are READER artifacts, not data
    differences — the same file via read_universe_csv compares clean. Exactly two
    are normalized; nothing broader, or a genuine difference could be coerced
    into agreement."""
    assert P._cell("921299.0") == "921299"
    assert P._cell("nan") == ""
    assert P._cell(float("nan")) == ""
    # NOT normalized: these are real differences.
    assert P._cell("US38000Q2012") == "US38000Q2012"
    assert P._cell("1.5") == "1.5"
    assert P._cell("ABC.0") == "ABC.0"


def test_cli_validate_path_is_quiet_too():
    """Regression: the ledger check must be clean on the DataFrame the weekly
    build actually passes it, not only on a read_universe_csv one."""
    import pandas as pd

    import config
    from universe.validation import validate_against_provenance_ledger
    bare = pd.read_csv(config.CSV_PATH)      # the reader cli validate uses
    assert validate_against_provenance_ledger(bare) == []
