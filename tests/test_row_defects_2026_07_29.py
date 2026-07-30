"""VALUE PINS MOVED TO THE PROVENANCE LEDGER (2026-07-30).
Literal cell assertions now live in `data/identity_provenance.json` and are
checked by the single `test_the_universe_agrees_with_every_verified_cell`.
What remains here is what a ledger cannot express: mechanisms, quarantine
content, and the narrative of why a decision went the way it did.

Pins for the three row defects JP decided on 2026-07-29 (#251, #252).

All three are *row* fixes, not code fixes, so nothing but a test keeps them
fixed — the next enrichment pass or a restored backup can silently undo any of
them. Each assertion below names both the wrong value and the right one, so a
regression says what happened rather than just failing.

  #252  CSU   ISIN `NET000CLBR01` -> `CA21037X1006`
              The only ISO 6166 check-digit failure in 794 checked rows.
  #252  MICC  Country (HQ) `"NL"` -> `"Netherlands"`
              An alpha-2 code where a country NAME belongs. Fixed on the ROW,
              deliberately not by adding "NL" to COUNTRY_TO_ISO2 — the map is
              keyed by name, and teaching it a code would paper over the defect
              on every future row that carries one.
  #251  ICAD  removed from the universe, quarantined to delisted_tickers.csv
"""
import csv

import pytest

import config
from ticker_utils import isin_check_digit_ok, read_universe_csv


@pytest.fixture(scope="module")
def universe_rows():
    df = read_universe_csv()
    return {r["Ticker"]: r for _, r in df.iterrows()}


@pytest.fixture(scope="module")
def delisted_ledger():
    with open(config.DATA_DIR / "delisted_tickers.csv",
              encoding="utf-8-sig", newline="") as fh:
        return {r["Ticker"]: r for r in csv.DictReader(fh)}


# ── #252 CSU ────────────────────────────────────────────────────────────────


def test_the_old_csu_value_still_fails_the_check_digit():
    """Guards the premise, not just the outcome: if `isin_check_digit_ok` ever
    started accepting `NET000CLBR01`, the validator that found this would go
    blind and the test above would be pinning a value nothing detects."""
    assert not isin_check_digit_ok("NET000CLBR01")


# ── #252 MICC ───────────────────────────────────────────────────────────────


def test_country_prefix_coverage_is_quiet(universe_rows):
    """The acceptance test the board specified for MICC: the fix is right when
    `validate_country_prefix_coverage` goes SILENT, because that warning is what
    tells us the ISIN prefix guard is silently skipping rows. It warned
    "'NL' (1 row(s))" before this fix.
    """
    from universe.validation import validate_country_prefix_coverage
    assert validate_country_prefix_coverage(read_universe_csv()) == []


# ── #251 ICAD ───────────────────────────────────────────────────────────────

def test_icad_removed_and_quarantined(universe_rows, delisted_ledger):
    """ICAD was three companies in one row, and the one it was FOR is gone.

    Origin: iCAD, Inc. (NASDAQ: ICAD), the US AI breast-imaging company — which
    is where `Sector (JP) = Biopharma` came from. RadNet acquired it through
    DeepHealth (closed 2025-07-17, 8-K 0001193805-25-001039) and it deregistered
    by Form 15-12G on 2025-07-28. The freed ticker then pulled in Icade SA (the
    French REIT trading as ICAD in Paris) as the name, and AU000000ICI5 — iCandy
    Interactive Ltd, ASX: ICI — as the ISIN.

    Note this differs from how the item was originally filed, which described
    four wrong fields including a CIK: there was never a CIK on the row.
    """
    assert "ICAD" not in universe_rows
    assert "ICAD" in delisted_ledger, "removal must be quarantined, not a hard delete"
    e = delisted_ledger["ICAD"]
    assert e["Company Name"] == "iCAD, Inc."       # the row's actual subject
    assert e["Country (HQ)"] == "United States"
    assert "RadNet" in e["Reason"]
    assert "15-12G" in e["Reason"]


def test_icad_quarantine_records_a_blank_isin_not_a_guess(delisted_ledger):
    """ZEN precedent: a wrong identifier is worse than a blank one. OpenFIGI has
    no live mapping for the deregistered US line, so the cell stays empty and
    the Notes say why — rather than inheriting either contaminant."""
    e = delisted_ledger["ICAD"]
    assert e["ISIN"].strip() == ""
    assert "BLANK" in e["Notes"] or "blank" in e["Notes"]


def test_icad_notes_name_all_three_companies_and_icades_live_status(delisted_ledger):
    """A reader a year from now must be able to tell removal-for-contamination
    from delisting, and must not re-add Icade SA by inheriting this ticker."""
    notes = delisted_ledger["ICAD"]["Notes"]
    for who in ("iCAD, Inc.", "Icade SA", "ICANDY INTERACTIVE"):
        assert who in notes, f"{who} missing from the quarantine note"
    assert "still listed" in notes          # Icade SA is alive; say so explicitly
    assert "FR0000035081" in notes          # Icade's real ISIN, for a deliberate re-add
    assert "Reversible" in notes


def test_neither_icad_contaminant_survives_anywhere_in_the_universe(universe_rows):
    """The `ZEN` lesson generalized: a removed wrong identifier must not be
    sitting on some OTHER row. AU000000ICI5 is iCandy's; FR0000035081 is
    Icade's and belongs to no row in this universe today."""
    cols = ("ISIN", "LEI", "FIGI", "Composite FIGI", "Share Class FIGI")
    banned = {"AU000000ICI5", "FR0000035081"}
    hits = [(t, c, row[c].strip()) for t, row in universe_rows.items()
            for c in cols if c in row and str(row[c]).strip() in banned]
    assert hits == [], f"removed identifier reappeared: {hits}"


def test_albt_removed_as_a_SCOPE_change_not_a_delisting(universe_rows, delisted_ledger):
    """ALBT left the universe because the COMPANY left healthcare, not because it
    stopped trading — and the ledger has to say which, or a future reader will
    assume delisted.

    JP, 2026-07-29: "You can get rid of ALBT. It used to be a clinical company
    but then changed." Confirmed three independent ways: SEC's ticker map now
    titles CIK 1630212 "Change Agents Corporation." (was Avalon GloboCare Corp),
    OpenFIGI resolves US05344R3021 to CHANGE AGENTS CORP, and EDGAR classifies
    the registrant as `technology`. It is still filing (8-K accepted 2026-07-29)
    and still listed — very much alive.
    """
    assert "ALBT" not in universe_rows
    assert "ALBT" in delisted_ledger, "removal must be quarantined, not a hard delete"
    e = delisted_ledger["ALBT"]
    assert e["Delisted Date"].strip() == "", "it is NOT delisted; leave the date blank"
    # Assert the CLAIM, not one phrasing of it: the note must state somewhere that
    # this is not a delisting, and must say the company is still trading.
    notes = e["Notes"]
    assert ("NOT a delisting" in notes) or ("NOT delisted" in notes), notes[:200]
    assert "ALIVE" in notes or "still filing" in notes, notes[:200]
    assert "Change Agents" in e["Reason"] or "Change Agents" in notes
    # Its identifiers were CORRECT — this was never an identifier defect.
    assert e["ISIN"].strip() == "US05344R3021"
