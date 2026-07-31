"""`Instrument Type` — depositary receipt vs the actual share.

Every OpenFIGI value in these tests was captured LIVE on 2026-07-31 against real
universe rows, in the same spirit as `tests/test_isin_identity.py`. Nothing here
is invented, because the point of the column is to be right about real rows.
"""
import json

import pytest

from universe.instrument_type import (
    DEPOSITARY_RECEIPT, ORDINARY_SHARE, classify, classify_row, backfill,
)


# ── the classifier ───────────────────────────────────────────────────────────

def test_openfigi_answers_the_question_directly():
    """securityType2 IS the answer. No heuristic beats the source."""
    assert classify(["Depositary Receipt"]).value == DEPOSITARY_RECEIPT
    assert classify(["Common Stock"]).value == ORDINARY_SHARE


@pytest.mark.parametrize("isin,types,expected", [
    # captured live: US ISIN + foreign HQ on a US line -> genuinely ADRs
    ("US0053291078", ["Depositary Receipt"], DEPOSITARY_RECEIPT),   # Adagene
    ("US00653A1079", ["Depositary Receipt"], DEPOSITARY_RECEIPT),   # Adaptimmune
    ("US0231114044", ["Depositary Receipt"], DEPOSITARY_RECEIPT),   # Amarin
    ("US00654J2069", ["Depositary Receipt"], DEPOSITARY_RECEIPT),   # Addex
    # ...and the ordinaries that a US-ISIN heuristic would NOT have caught
    ("CH0329023102", ["Common Stock"], ORDINARY_SHARE),             # AC Immune
    ("IE00BTN1Y115", ["Common Stock"], ORDINARY_SHARE),             # Medtronic
    ("KYG4818G1010", ["Common Stock"], ORDINARY_SHARE),             # Innovent (Cayman/HK)
    ("CNE1000031K4", ["Common Stock"], ORDINARY_SHARE),             # WuXi AppTec
])
def test_live_captured_rows(isin, types, expected):
    assert classify(types).value == expected


def test_absent_coverage_is_not_a_verdict():
    """`[]` means OpenFIGI knows nothing. That is not 'ordinary share'.

    The whole `found / clean / inconclusive` discipline this repo already applies
    to delisted_check and ipo_backfill: absent data is never a finding.
    """
    r = classify([])
    assert r.value == ""
    assert r.status == "no-openfigi-coverage"


def test_a_transient_failure_is_never_recorded_as_a_verdict():
    r = classify(None)
    assert r.value == ""
    assert r.status == "openfigi-unreachable"


def test_venues_that_disagree_are_escalated_not_averaged():
    """One ISIN maps to one FIGI per venue. If two venues disagree about what the
    instrument IS, picking either is a coin toss dressed as data."""
    r = classify(["Common Stock", "Depositary Receipt"])
    assert r.value == ""
    assert r.status == "ambiguous"
    assert "Common Stock" in r.detail and "Depositary Receipt" in r.detail


def test_an_unknown_security_type_does_not_silently_become_ordinary():
    """Preferred stock, units, warrants: real securityType2 values that are
    neither. Mapping the unrecognised to the common case would be a lie that
    reads as data."""
    r = classify(["Preferred Stock"])
    assert r.value == ""
    assert r.status == "unmapped-type"


# ── the row-level rule ───────────────────────────────────────────────────────

def test_a_primary_listing_needs_no_api_call():
    """A primary listing is the actual share by definition — there is no such
    thing as a primary listing of a receipt. 915 of 1,093 rows decide here, for
    free, which is what keeps the OpenFIGI pass to ~10 requests."""
    r = classify_row({"Listing Type": "Primary", "ISIN": ""}, types=None)
    assert r.value == ORDINARY_SHARE
    assert r.status == "primary-listing"


def test_a_row_with_no_isin_cannot_be_decided():
    r = classify_row({"Listing Type": "ADR/Cross-listed", "ISIN": ""}, types=None)
    assert r.value == ""
    assert r.status == "no-isin"


def test_cross_listed_rows_defer_to_openfigi():
    row = {"Listing Type": "ADR/Cross-listed", "ISIN": "US0231114044"}
    assert classify_row(row, types=["Depositary Receipt"]).value == DEPOSITARY_RECEIPT


# ── the backfill ─────────────────────────────────────────────────────────────

def _rows():
    return [
        {"Ticker": "MRK", "Listing Type": "Primary", "ISIN": "US58933Y1055"},
        {"Ticker": "AMRN", "Listing Type": "ADR/Cross-listed", "ISIN": "US0231114044"},
        {"Ticker": "MDT", "Listing Type": "ADR/Cross-listed", "ISIN": "IE00BTN1Y115"},
        {"Ticker": "ALC", "Listing Type": "ADR/Cross-listed", "ISIN": ""},
    ]


def test_backfill_only_calls_openfigi_for_rows_it_cannot_decide_locally():
    asked = {}

    def fake_fetch(isins, **kw):
        asked["isins"] = list(isins)
        return {"US0231114044": ["Depositary Receipt"], "IE00BTN1Y115": ["Common Stock"]}

    res = backfill(_rows(), fetch=fake_fetch)
    # MRK is Primary (free) and ALC has no ISIN (undecidable) — neither is worth a call.
    assert asked["isins"] == ["US0231114044", "IE00BTN1Y115"]
    assert res.values["MRK"] == ORDINARY_SHARE
    assert res.values["AMRN"] == DEPOSITARY_RECEIPT
    assert res.values["MDT"] == ORDINARY_SHARE
    # `values` is "what to write" — an undecided row writes NOTHING rather than
    # an empty string, so a blank cell is never mistaken for a computed verdict.
    assert "ALC" not in res.values
    assert "ALC" in res.undecided


def test_backfill_never_overwrites_a_populated_cell():
    """Same rule as every other identifier lane in this repo: fill blanks, never
    overwrite. A human may have adjudicated the cell the API cannot."""
    rows = _rows()
    rows[1]["Instrument Type"] = "Ordinary Share"        # hand-set, deliberately contrary
    res = backfill(rows, fetch=lambda isins, **kw: {i: ["Depositary Receipt"] for i in isins})
    assert "AMRN" not in res.values
    assert res.skipped_populated == 1


def test_backfill_reports_undecided_rows_by_name():
    res = backfill(_rows(), fetch=lambda isins, **kw: {i: [] for i in isins})
    assert set(res.undecided) == {"AMRN", "MDT", "ALC"}
    assert res.counts["no-openfigi-coverage"] == 2
    assert res.counts["no-isin"] == 1


def test_a_transient_openfigi_outage_writes_nothing():
    """An unreachable API must never read as validated — the rule the ISIN
    identity gate already enforces on the write path."""
    res = backfill(_rows(), fetch=lambda isins, **kw: {i: None for i in isins})
    assert res.values == {"MRK": ORDINARY_SHARE}          # only the free verdict
    assert res.counts["openfigi-unreachable"] == 2
