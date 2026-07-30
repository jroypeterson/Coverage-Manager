"""Three hidden renames + the venue/currency contamination (2026-07-29, JP-approved).

Two separate defect families, both rooted in the same thing: **a bare ticker is
not an identity.** A foreign row keyed on an unsuffixed symbol gets enriched
against whatever US security shares that symbol.

  Renames  RENB->LNAI, THAR->CNTN, ZOM->ZOMDF. Found by resolving CIK-blank rows
           by COMPANY NAME, because the CIK-keyed detector cannot see them (see
           test_isin_corrections_2026_07_29.test_the_cik_blind_spot_that_hid_this_rename).

  Venue    GXI/SDZ/TUB claimed NASDAQ; MED/MOVE/UCB claimed USD. All six also
           carried `Country (Listing) = United States` and a US
           Exchange Code / Exchange Full Name. MED/MOVE/UCB additionally carried
           the US namesake's CIK **and** FIGIs -- the worst of it, because a wrong
           CIK misroutes every EDGAR-keyed lane to a different company.

Every replacement value matches the convention already used by correctly
populated peers in this file (SIX -> EBS/Swiss/CHE, XETRA -> GER/XETRA/DEU,
Euronext Brussels -> BRU/Brussels/BEL, OTC -> PNK/'OTC Markets OTCPK'), rather
than being invented here.
"""
import pytest

from ticker_utils import read_universe_csv


@pytest.fixture(scope="module")
def rows():
    df = read_universe_csv()
    return {r["Ticker"]: r for _, r in df.iterrows()}


# ── the three renames ───────────────────────────────────────────────────────

RENAMED = {
    # new: (old, cik, name, exchange)
    "LNAI":  ("RENB", "1527728", "Lunai Bioworks Inc.", "NASDAQ"),
    "CNTN":  ("THAR", "1861657", "Canton Strategic Holdings Inc", "NASDAQ"),
    "ZOMDF": ("ZOM",  "1684144", "Zomedica Corp.", "OTC"),
}


@pytest.mark.parametrize("new", sorted(RENAMED))
def test_renamed_row_carries_the_new_ticker_and_a_cik(new, rows):
    old, cik, name, exch = RENAMED[new]
    assert old not in rows, f"{old} must be gone, not duplicated alongside {new}"
    r = rows[new]
    assert r["Company Name"].strip() == name
    assert r["Exchange"].strip() == exch
    # The CIK is the point: its absence is what hid every one of these renames.
    assert r["CIK"].strip() == cik, (
        f"{new} needs its CIK or check-ticker-changes goes blind again")


def test_zomedica_moved_to_OTC_it_did_not_merely_rename(rows):
    """ZOM is the odd one of the three and must not be filed as a plain rename.

    The company NAME did not change -- EDGAR still reports 'Zomedica Corp.'. What
    changed is the venue: EDGAR reports exchange=OTC and `ZOMDF` is a 5-letter
    OTC-style symbol. A move off an exchange to OTC is materially different from
    a rebrand, so the row records the venue rather than silently keeping NASDAQ.
    """
    r = rows["ZOMDF"]
    assert r["Exchange"].strip() == "OTC"
    assert r["Exchange Code"].strip() == "PNK"
    assert r["Exchange Full Name"].strip() == "OTC Markets OTCPK"


def test_canton_no_longer_advertises_tharimmunes_website(rows):
    """The row stopped describing Tharimmune, so a tharimmune.com URL on it is
    misinformation. Cleared rather than guessed -- a blank is honest."""
    assert "tharimmune" not in rows["CNTN"].get("Website", "").lower()


# ── the venue / currency contamination ──────────────────────────────────────

VENUE = {
    "GXI":  ("XETRA",             "GER", "XETRA",             "Germany",     "DEU", "EUR"),
    "SDZ":  ("SIX",               "EBS", "Swiss",             "Switzerland", "CHE", "CHF"),
    "TUB":  ("Euronext Brussels", "BRU", "Brussels",          "Belgium",     "BEL", "EUR"),
    "MED":  ("SIX",               "EBS", "Swiss",             "Switzerland", "CHE", "CHF"),
    "MOVE": ("SIX",               "EBS", "Swiss",             "Switzerland", "CHE", "CHF"),
    "UCB":  ("Euronext Brussels", "BRU", "Brussels",          "Belgium",     "BEL", "EUR"),
}


@pytest.mark.parametrize("ticker", sorted(VENUE))
def test_row_trades_where_and_in_what_its_isin_says(ticker, rows):
    exch, code, full, listing, iso, ccy = VENUE[ticker]
    r = rows[ticker]
    assert r["Exchange"].strip() == exch
    assert r["Exchange Code"].strip() == code
    assert r["Exchange Full Name"].strip() == full
    assert r["Country (Listing)"].strip() == listing
    assert r["Country (ISO)"].strip() == iso
    assert r["Currency"].strip() == ccy


@pytest.mark.parametrize("ticker", sorted(VENUE))
def test_no_venue_row_still_claims_a_US_listing(ticker, rows):
    """The single shared symptom across all six: `Country (Listing)` said United
    States for a company that has never listed there."""
    assert rows[ticker]["Country (Listing)"].strip() != "United States"


# ── the CIK/FIGI half, which was the dangerous half ─────────────────────────

NAMESAKE_CIKS = {"910329": "MEDIFAST INC", "1734750": "Corvex, Inc.",
                 "857855": "UNITED COMMUNITY BANKS INC"}
NAMESAKE_FIGIS = {"BBG000BWBW76", "BBG001SD45H4",   # Medifast
                  "BBG00X0V8H84", "BBG00X0V8H93",   # Corvex
                  "BBG000BL7GB5", "BBG001S9SSJ5"}   # United Community Banks

CORRECT = {
    "MED":  ("",        "BBG00K68NZ35", "BBG00K68NZ35", "BBG00K68NZB6"),
    "MOVE": ("",        "BBG00NPRXKK9", "BBG00NPRXKK9", "BBG00NPRXKS1"),
    "UCB":  ("1290640", "BBG000BD8CR4", "BBG000BD8BK3", "BBG001S6LBX9"),
}


@pytest.mark.parametrize("ticker", sorted(CORRECT))
def test_cik_and_figis_identify_the_right_issuer(ticker, rows):
    """A wrong CURRENCY is cosmetic; a wrong CIK actively misroutes every
    EDGAR-keyed lane to a different company's filings. `MED` was pointing at
    Medifast's CIK, `MOVE` at Corvex's, `UCB` at United Community Banks'.

    Medartis and Medacta have NO EDGAR record, so blank is the correct value --
    not another CIK. UCB S.A. genuinely has one (1290640) as a foreign private
    issuer, which is why it is the only non-blank of the three.
    """
    cik, figi, comp, sc = CORRECT[ticker]
    r = rows[ticker]
    assert r["CIK"].strip() == cik
    assert r["FIGI"].strip() == figi
    assert r["Composite FIGI"].strip() == comp
    assert r["Share Class FIGI"].strip() == sc


def test_no_namesake_identifier_survives_anywhere(rows):
    """Generalised `ZEN` rule: a removed wrong identifier must not be sitting on
    some OTHER row either."""
    figi_cols = ("FIGI", "Composite FIGI", "Share Class FIGI")
    hits = [(t, c, r[c].strip()) for t, r in rows.items()
            for c in figi_cols if r[c].strip() in NAMESAKE_FIGIS]
    assert hits == [], f"a namesake FIGI reappeared: {hits}"
    cik_hits = [(t, r["CIK"].strip()) for t, r in rows.items()
                if r["CIK"].strip() in NAMESAKE_CIKS and t in CORRECT]
    assert cik_hits == [], f"a namesake CIK reappeared: {cik_hits}"


def test_the_root_cause_is_recorded_not_just_the_symptoms(rows):
    """⚑ OPEN, deliberately not fixed here: all six rows are keyed on a BARE
    foreign ticker, which is exactly why a US namesake's data landed on them.

    This file's own convention already supports suffixes -- `GALD.SW`, `BOI.PA`,
    `DIA.MI`, `2715.HK` all carry one -- and `MED.SW` / `MOVE.SW` / `UCB.BR`
    would stop the recurrence at the source. Re-keying was NOT done unilaterally
    because a ticker is the published join key ~20 sibling repos match on.

    This test documents the exposure rather than asserting the fix: it fails the
    day someone adds a suffix, which is the moment to delete it and update
    DEPENDENCIES.md.
    """
    for t in ("GXI", "SDZ", "TUB", "MED", "MOVE", "UCB"):
        assert "." not in t and t in rows, (
            f"{t} appears to have been re-keyed -- if a suffix was added, update "
            f"the consumers and delete this test")
