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
#
# ALBT and FGEN were BOTH resolved later the same day, and neither by touching an
# ISIN -- which was the point of holding them. JP identified both immediately:
# ALBT "used to be a clinical company but then changed" (removed from the
# universe, see test_row_defects), FGEN is now Kyntra Bio trading as KYNB
# (remapped). See test_fgen_became_kynb below.
HELD = {
    # CBIO was resolved 2026-07-30 -- see test_cbio_resolved_by_the_merger_cusip.
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


def test_fgen_became_kynb_and_kept_its_isin(universe_rows):
    """FibroGen renamed to Kyntra Bio and the ticker went FGEN -> KYNB.

    This is the vindication of holding it rather than "correcting" the ISIN.
    OpenFIGI returned KYNTRA BIO INC for the stored `US31572Q8814`, which read as
    a wrong-issuer conflict; it was actually the register being AHEAD of the row.
    The ISIN survived the rename intact (OpenFIGI still maps it to KYNTRA BIO
    INC, Common Stock), so the fix was a ticker+name+CIK remap and NOT an
    identifier change. Had it been "corrected", a valid identifier would have
    been overwritten.

    The CIK is the load-bearing part: the row had NO CIK, which is exactly why
    `check-ticker-changes` never saw this rename -- see
    test_the_cik_blind_spot_that_hid_this_rename.
    """
    assert "FGEN" not in universe_rows, "the old ticker must be gone, not duplicated"
    row = universe_rows["KYNB"]
    assert row["Company Name"].strip() == "Kyntra Bio, Inc."
    assert row["ISIN"].strip() == "US31572Q8814", "the ISIN survived the rename"
    assert row["CIK"].strip() == "921299"
    assert row["LEI"].strip() == "549300Q914ULWWY95822", "LEIs persist through renames"
    assert row["Sector (JP)"].strip() == "Biopharma"


def test_the_cik_blind_spot_that_hid_this_rename():
    """Why FGEN->KYNB went undetected, stated as a test so it is not re-learned.

    Two mechanisms should have caught it and both failed, circularly:

      - `check-ticker-changes` is keyed on CIK. FGEN's CIK was BLANK, so the row
        was invisible to it.
      - `backfill-cik` fills a blank CIK by looking the TICKER up in SEC's map.
        But a renamed company's OLD ticker is no longer IN that map, so the CIK
        can never be filled -- confirmed live: `backfill-cik --dry-run` reported
        "would fill 0" with 238 blanks remaining.

    So a row with a changed ticker AND a blank CIK is undetectable by both. The
    escape hatch is resolving by COMPANY NAME instead, which found three further
    hidden renames (RENB->LNAI, THAR->CNTN, ZOM->ZOMDF) that remain open.
    """
    from ticker_utils import read_universe_csv
    df = read_universe_csv()
    blank = [r["Ticker"] for _, r in df.iterrows() if not str(r.get("CIK", "")).strip()]
    # Not an assertion about the exact number -- it will move as rows are fixed.
    # The point is that the blind spot is LARGE, so the name-based resolver is
    # worth building rather than treating FGEN as a one-off.
    assert len(blank) > 100, (
        f"only {len(blank)} blank CIKs -- if this has genuinely been fixed, "
        f"delete this test and say so in the commit")
    assert "KYNB" not in blank, "the row this test exists for must have its CIK"


def test_every_replacement_is_absent_from_the_delisted_ledger():
    """Cheap cross-check: none of the new values collides with a quarantined row,
    which would mean we just re-pointed a live row at a dead identifier."""
    with open(config.DATA_DIR / "delisted_tickers.csv",
              encoding="utf-8-sig", newline="") as fh:
        ledger_isins = {r["ISIN"].strip() for r in csv.DictReader(fh) if r["ISIN"].strip()}
    new = {r for _w, r, _n in CORRECTED.values()}
    assert not (new & ledger_isins), f"collision with quarantine: {new & ledger_isins}"


def test_cbio_resolved_by_the_merger_cusip(universe_rows):
    """CBIO came off the held list on 2026-07-30 with THREE confirmations, not one.

    It was held because `US38000Q2012` had GLEIF but no OpenFIGI coverage, and one
    source is not two. A web search settled it: the GlycoMimetics/Crescent reverse
    merger (closed 2025-06-16) was preceded by a **1-for-100 reverse split**, and
    the merger release names the post-split CUSIP as **38000Q201**. That composes
    to exactly one valid ISIN -- `US38000Q2012` -- so:

      1. GLEIF ties US38000Q2012 to Crescent Biopharma's LEI 549300TZ84FFU2J2J459
      2. the merger release's CUSIP 38000Q201 + the ISO 6166 check digit yields
         US38000Q2012 uniquely
      3. the check digit passes

    And it explains the stored value rather than just overruling it: the old
    `US38000Q1022` embeds CUSIP `38000Q102`, the **pre-split** line -- which is
    precisely why OpenFIGI still resolves it to GLYCOMIMETICS. Nothing was wrong
    with that mapping; the row was one corporate action behind.

    The CIK is unchanged (1253689) because the registrant survived the merger --
    the same reason FGEN kept its LEI through the Kyntra rename.
    """
    row = universe_rows["CBIO"]
    assert row["ISIN"].strip() == "US38000Q2012"
    assert row["ISIN"].strip() != "US38000Q1022", "that is the pre-reverse-split line"
    assert isin_check_digit_ok(row["ISIN"].strip())
    assert row["CIK"].strip() == "1253689"


def test_the_pre_split_cbio_isin_is_gone_from_every_row(universe_rows):
    cols = ("ISIN", "LEI", "FIGI", "Composite FIGI", "Share Class FIGI")
    hits = [(t, c) for t, r in universe_rows.items()
            for c in cols if c in r and str(r[c]).strip() == "US38000Q1022"]
    assert hits == [], f"the pre-split ISIN reappeared: {hits}"
