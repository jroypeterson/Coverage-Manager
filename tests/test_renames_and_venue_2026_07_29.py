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


def test_these_rows_are_internally_consistent_however_they_are_keyed():
    """The invariant, replacing an inverted sentinel.

    The first version of this test asserted these tickers had NO suffix, framed
    as "documenting the exposure". An architectural review pointed out it was
    backwards: it would FAIL the day someone finally re-keyed the rows to
    `MED.SW` etc. and fixed the root cause. A test that punishes the fix is worse
    than no test.

    What actually needs pinning is the invariant, not the key: whatever these
    rows are called, their Exchange, Country (Listing) and Currency must agree.
    That is now enforced for the whole file by
    `validate_venue_consistency`, so this asserts the general property and stops
    caring about the ticker string.
    """
    from ticker_utils import read_universe_csv
    from universe.validation import validate_venue_consistency

    warnings = validate_venue_consistency(read_universe_csv())
    blob = " ".join(warnings)
    for t in ("GXI", "SDZ", "TUB", "MED", "MOVE", "UCB"):
        assert f"{t} (" not in blob, (
            f"{t} regressed to an inconsistent venue/currency:\n" + blob[:600])


# ── the validator that should have found all six in one pass ────────────────

def test_venue_validator_catches_the_prefix_of_tonights_own_defects():
    """Regression harness for the mechanism, not the rows.

    `validate_venue_consistency` was added after two reviewers observed that six
    rows had been corrected ONE AT A TIME by hand when a single offline pass over
    data already in the file would have surfaced all of them. Prove it actually
    does, by running it against a synthetic row shaped like the defect.
    """
    import pandas as pd
    from universe.validation import validate_venue_consistency

    bad = pd.DataFrame([{
        "Ticker": "ZZZ", "Exchange": "SIX", "Country (Listing)": "United States",
        "Currency": "USD", "Listing Type": "Primary",
    }])
    warnings = validate_venue_consistency(bad)
    blob = " ".join(warnings)
    assert "ZZZ" in blob
    assert "Switzerland" in blob                      # names the real venue country
    assert any("Currency" in w for w in warnings)     # and the currency half


def test_venue_validator_does_not_flag_a_legitimate_cross_listing():
    """`EVO` is a correct NASDAQ/USD row for a German issuer. A validator that
    flagged every ADR would be turned off within a week."""
    import pandas as pd
    from universe.validation import validate_venue_consistency

    adr = pd.DataFrame([{
        "Ticker": "EVO", "Exchange": "NASDAQ", "Country (Listing)": "United States",
        "Currency": "USD", "Listing Type": "ADR/Cross-listed",
    }])
    assert validate_venue_consistency(adr) == []


def test_venue_validator_accepts_minor_currency_units():
    """The LSE quotes in pence and the JSE in cents. Flagging `GBp`/`ZAc` would
    plant permanent false positives in a warning, which is how validators die."""
    import pandas as pd
    from universe.validation import validate_venue_consistency

    minor = pd.DataFrame([
        {"Ticker": "AGY.L", "Exchange": "LSE", "Country (Listing)": "United Kingdom",
         "Currency": "GBp", "Listing Type": ""},
        {"Ticker": "APN.JO", "Exchange": "JSE", "Country (Listing)": "South Africa",
         "Currency": "ZAc", "Listing Type": ""},
    ])
    assert validate_venue_consistency(minor) == []


def test_venue_validator_stays_silent_on_data_it_cannot_judge():
    """An unmapped exchange or a blank cell is absent information, not a defect --
    the found/clean/inconclusive discipline applied to a validator."""
    import pandas as pd
    from universe.validation import validate_venue_consistency

    unknown = pd.DataFrame([
        {"Ticker": "AAA", "Exchange": "Some New Bourse",
         "Country (Listing)": "Ruritania", "Currency": "XYZ", "Listing Type": ""},
        {"Ticker": "BBB", "Exchange": "", "Country (Listing)": "",
         "Currency": "", "Listing Type": ""},
    ])
    assert validate_venue_consistency(unknown) == []
