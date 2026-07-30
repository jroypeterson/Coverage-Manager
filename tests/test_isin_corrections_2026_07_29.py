"""Pins for the 14 wrong-issuer ISIN corrections applied 2026-07-29 (#249).

Every replacement cleared the same three-part gate before it was written, per the
protocol set by the first seven corrections in `03812d5`:

  1. ISO 6166 check digit passes.
  2. OpenFIGI resolves the ISIN to an issuer name matching the row's Company
     Name, AND shows it trading under the row's OWN ticker — so a bond, a
     warrant, a second line or a foreign listing cannot be swapped in.
  3. GLEIF independently ties the ISIN to that issuer's LEI.

Six of the twenty conflicts were deliberately NOT applied. They are asserted
here too, as holds, because "we looked and could not settle it" is a state worth
protecting from a future pass that mistakes silence for cleanliness.
"""
import csv

import pytest

import config
from ticker_utils import isin_check_digit_ok, read_universe_csv

# ticker -> (wrong, right, who the wrong ISIN actually belonged to)
CORRECTED = {
    "CROX":    ("FR0000050395", "US2270461096", "CROSSWOOD"),
    "DIA.MI":  ("US78467X1090", "IT0003492391", "SPDR DJIA TRUST - an ETF"),
    "GALD.SW": ("INE243E01010", "CH1335392721", "GALADA FINANCE LTD"),
    "GNFT":    ("JP3264860002", "FR0004163111", "GIFT HOLDINGS INC"),
    "GXI":     ("CNE0000019K1", "DE000A0LD6E6", "GUIZHOU GUIHANG AUTOMOTIVE-A"),
    "MED":     ("US58470H1014", "CH0386200239", "MEDIFAST INC"),
    "MOVE":    ("US62459M3051", "CH0468525222", "CORVEX INC"),
    "OPT":     ("CNE100005XZ3", "GB00BRSCY602", "OPT MACHINE VISION TECH CO-A"),
    "SDZ":     ("DE000A3CM708", "CH1243598427", "SDM SE"),
    "SOON":    ("SG1W36938981", "CH0012549785", "SOON LIAN HOLDINGS LTD"),
    "BOI.PA":  ("AU000000WNR4", "FR0000061129", "WINGARA AG LTD"),
    "TUB":     ("ES0132945017", "BE0003823409", "TUBACEX SA"),
    "UCB":     ("US90984P3038", "BE0003739530", "UNITED COMMUNITY BANKS/GA"),
    # EVO is the one that had to be the ADR, not the ordinary — see its own test.
    "EVO":     ("DE000A161234", "US30050E1055", "EKOTECHNIKA AG"),
}

# Conflicts left in place, with the reason each could not be settled.
HELD = {
    "ALBT": "US05344R3021",   # ISIN is RIGHT; the stored NAME is stale
    "FGEN": "US31572Q8814",   # ISIN is RIGHT; the stored NAME is stale
    "CBIO": "US38000Q1022",   # GLEIF-only candidate, no OpenFIGI coverage
    "CPH":  "CH0001624714",   # no candidate cleared both sources
    "MDLA": "SE0008937411",   # Indonesian; no candidate found at all
    "2715.HK": "EE0000000453",  # no GLEIF record; H-share status unclear
}


@pytest.fixture(scope="module")
def universe_rows():
    df = read_universe_csv()
    return {r["Ticker"]: r for _, r in df.iterrows()}


@pytest.mark.parametrize("ticker", sorted(CORRECTED))
def test_corrected_isin_is_what_the_universe_carries(ticker, universe_rows):
    wrong, right, _who = CORRECTED[ticker]
    stored = universe_rows[ticker]["ISIN"].strip()
    assert stored == right, f"{ticker} ISIN regressed to {stored}"
    assert stored != wrong
    assert isin_check_digit_ok(stored)


def test_no_replaced_identifier_survives_anywhere_in_the_universe(universe_rows):
    """A wrong ISIN is worse than a blank one, because it looks like data. None of
    the fourteen replaced values may reappear on ANY row, not merely on its own —
    the generalized `ZEN` lesson."""
    removed = {w for w, _r, _n in CORRECTED.values()}
    cols = ("ISIN", "LEI", "FIGI", "Composite FIGI", "Share Class FIGI")
    hits = [(t, c, row[c].strip()) for t, row in universe_rows.items()
            for c in cols if c in row and str(row[c]).strip() in removed]
    assert hits == [], f"a replaced identifier reappeared: {hits}"


def test_evo_carries_the_ADR_isin_because_the_row_is_the_ADR_line(universe_rows):
    """The trap this row nearly walked into, and the reason #250 exists.

    GLEIF lists BOTH of Evotec's lines under one LEI: the German ordinary
    DE0005664809 and the sponsored ADR US30050E1055. This row is
    `Listing Type = ADR/Cross-listed`, NASDAQ, USD, ticker EVO — so the ordinary's
    ISIN would have been the wrong INSTRUMENT while looking perfectly valid
    (right issuer, right check digit, real security). What caught it was requiring
    OpenFIGI to show the ISIN trading under the row's own ticker: nothing under
    DE0005664809 trades as `EVO`, while US30050E1055 is `EVO` on UN/US/UW and is
    typed `ADR`.
    """
    row = universe_rows["EVO"]
    assert row["ISIN"].strip() == "US30050E1055"
    assert row["ISIN"].strip() != "DE0005664809", "that is the German ordinary"
    assert "ADR" in str(row.get("Listing Type", "")), (
        "if this row stops being the ADR line, its ISIN must be revisited")


@pytest.mark.parametrize("ticker", sorted(HELD))
def test_held_conflicts_were_not_silently_changed(ticker, universe_rows):
    """These six stayed as they were ON PURPOSE. Asserting the hold means a later
    pass cannot quietly 'fix' one without confronting why it was left."""
    assert universe_rows[ticker]["ISIN"].strip() == HELD[ticker]


def test_albt_and_fgen_are_name_problems_not_isin_problems(universe_rows):
    """Both were filed as "probable renames — the NAME side may be stale", and
    that is exactly what the evidence says: GLEIF lists each row's STORED ISIN
    under the correct issuer's own LEI, while OpenFIGI returns a different name
    for it (CHANGE AGENTS CORP / KYNTRA BIO INC). So the identifier is right and
    the name is behind — the opposite of the other twelve. Changing the ISIN here
    would have introduced an error, not removed one."""
    assert universe_rows["ALBT"]["ISIN"].strip() == "US05344R3021"
    assert universe_rows["FGEN"]["ISIN"].strip() == "US31572Q8814"


def test_every_replacement_is_absent_from_the_delisted_ledger():
    """Cheap cross-check: none of the new values collides with a quarantined row,
    which would mean we just re-pointed a live row at a dead identifier."""
    with open(config.DATA_DIR / "delisted_tickers.csv",
              encoding="utf-8-sig", newline="") as fh:
        ledger_isins = {r["ISIN"].strip() for r in csv.DictReader(fh) if r["ISIN"].strip()}
    new = {r for _w, r, _n in CORRECTED.values()}
    assert not (new & ledger_isins), f"collision with quarantine: {new & ledger_isins}"
