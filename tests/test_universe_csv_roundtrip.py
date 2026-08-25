"""Regression tests for CIK / Year-Listed float-ification on universe-CSV round-trips.

A bare ``pd.read_csv`` infers integer ID columns that contain blank cells (CIK,
Year Listed) as float64, so a load -> full-rewrite round-trip persists ``1125376.0``
/ ``2007.0``. A ``.0`` CIK breaks SEC/EDGAR lookups and corrupts the published
``exports/universe.csv``. ``ticker_utils.read_universe_csv`` (dtype=str,
keep_default_na=False) is the fix; every full-file writer must use it.
"""

import csv as _csv

import pandas as pd

from ticker_utils import read_universe_csv


def _write_universe(path):
    """A universe CSV whose CIK / Year Listed columns contain blanks — the exact
    shape that makes a bare ``read_csv`` infer float64."""
    df = pd.DataFrame({
        "Ticker": ["AAPL", "FOO", "BAR"],
        "Company Name": ["Apple", "Foo", "Bar"],
        "CIK": ["320193", "", "1551152"],
        "Year Listed": ["1980", "2007", ""],
    })
    df.to_csv(path, index=False)


def test_read_universe_csv_preserves_integer_ids(tmp_path):
    p = tmp_path / "u.csv"
    _write_universe(p)
    df = read_universe_csv(p)
    assert list(df["CIK"]) == ["320193", "", "1551152"]
    assert list(df["Year Listed"]) == ["1980", "2007", ""]
    # Must not be inferred as a numeric/float column (the root cause).
    assert not pd.api.types.is_float_dtype(df["CIK"])
    assert not pd.api.types.is_numeric_dtype(df["Year Listed"])
    assert not df["CIK"].str.endswith(".0").any()


def test_round_trip_is_byte_stable(tmp_path):
    p = tmp_path / "u.csv"
    _write_universe(p)
    before = p.read_text(encoding="utf-8")
    df = read_universe_csv(p)
    df.to_csv(p, index=False)
    after = p.read_text(encoding="utf-8")
    assert before == after


def test_bare_read_csv_would_corrupt_cik(tmp_path):
    """Documents the bug the fix prevents: the bare read path DOES float-ify."""
    p = tmp_path / "u.csv"
    _write_universe(p)
    bad = pd.read_csv(p)            # bare → CIK becomes float64
    bad.to_csv(p, index=False)
    reread = read_universe_csv(p)
    assert reread["CIK"].str.endswith(".0").any()   # the corruption read_universe_csv avoids


def test_commit_staged_candidates_preserves_existing_cik(tmp_path, monkeypatch):
    """The weekly vector: committing an approved candidate must not float-ify the
    existing rows' CIK / Year Listed."""
    from discovery import candidates

    monkeypatch.setattr(candidates, "log_change", lambda **kw: None)

    uni = tmp_path / "u.csv"
    _write_universe(uni)

    staging = tmp_path / "staging.csv"
    fields = ["approved", "ticker", "company", "exchange", "market_cap", "sector", "subsector"]
    with open(staging, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({"approved": "true", "ticker": "NEWCO", "company": "New Co",
                    "exchange": "NASDAQ", "market_cap": "", "sector": "Tech", "subsector": ""})

    added = candidates.commit_staged_candidates(str(staging), csv_path=str(uni))
    assert added == 1

    out = read_universe_csv(uni)
    # Existing CIKs preserved as integer-strings (no .0).
    assert not out["CIK"].str.endswith(".0").any()
    assert "320193" in set(out["CIK"])
    # New row appended with a blank CIK (enrich fills it later).
    assert "NEWCO" in set(out["Ticker"])
    new_cik = out.loc[out["Ticker"] == "NEWCO", "CIK"].iloc[0]
    assert new_cik == ""


# --- writes go through the shared helper, or the encoding drifts again -------
#
# ONE canonical form for the universe CSV, and as of 2026-07-27 that form is
# UTF-8 **without** a BOM.
#
# The original ratchet (2026-07-16) picked BOM-ful because that was what the files
# happened to contain. The goal -- one form, no drift -- was right; the form was
# not. On 2026-07-25 a BOM reached the master, `_step_export_artifacts` read the
# header as plain utf-8, and `"﻿Ticker" != "Ticker"` meant DictWriter dropped
# the join key from every published row: 84 blank position rows, 66 blank
# watchlist rows, and a universe.csv from which earnings_agent,
# post_earnings_movers and analyst-days each recovered 0 of 1,086 tickers -- all
# reported as a clean run.
#
# BOM-free is the safe direction: a reader using utf-8-sig handles a BOM-free file
# correctly, but a reader using plain utf-8 does NOT handle a BOM, and most
# consumers use plain utf-8. For a file ~20 sibling projects join on, the encoding
# must be the one that survives the least careful reader.

import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: Modules that write the whole universe CSV back.
_UNIVERSE_WRITERS = [
    "universe/add_exchanges.py",
    "universe/cleanup.py",
    "universe/enrich.py",
    "universe/lei_backfill.py",
    "universe/ipo_backfill.py",
    "universe/cik_backfill.py",
    "discovery/candidates.py",
]

#: `to_csv` calls that legitimately write something OTHER than the universe.
_ALLOWED_OTHER_TARGETS = ("conflict_path",)


def test_no_universe_writer_calls_to_csv_directly():
    offenders = []
    for rel in _UNIVERSE_WRITERS:
        src = (_REPO / rel).read_text(encoding="utf-8")
        for match in re.finditer(r"\.to_csv\(\s*([A-Za-z_][A-Za-z_0-9]*)", src):
            target = match.group(1)
            if target not in _ALLOWED_OTHER_TARGETS:
                line = src[: match.start()].count("\n") + 1
                offenders.append(f"{rel}:{line} -> .to_csv({target}, ...)")
    assert not offenders, (
        "these write the universe CSV without write_universe_csv(), which "
        "strips the canonical BOM: " + "; ".join(offenders)
    )


def test_write_universe_csv_is_bom_free():
    import pandas as pd

    from ticker_utils import read_universe_csv, write_universe_csv

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "u.csv"
        write_universe_csv(pd.DataFrame([{"Ticker": "AAPL", "CIK": "320193"}]), p)
        assert p.read_bytes()[:3] != b"\xef\xbb\xbf", (
            "the universe CSV must be BOM-free: a consumer reading plain utf-8 sees "
            "'﻿Ticker' and silently joins on nothing"
        )
        assert read_universe_csv(p)["Ticker"].iloc[0] == "AAPL"


def test_published_exports_are_readable_by_a_plain_utf8_consumer():
    """The ratchet the original was missing.

    It pinned the SOURCE encoding and said nothing about the published artifact —
    which is where the contract actually lives, and where the 2026-07-25 breakage
    landed. Reads each export the way the least careful consumer does.
    """
    import csv as _csv

    exports = _REPO / "exports"
    for fname, key in (("universe.csv", "Ticker"),
                       ("positions_and_researching.csv", "Ticker"),
                       ("watchlist.csv", "Ticker")):
        p = exports / fname
        if not p.exists():
            continue
        assert p.read_bytes()[:3] != b"\xef\xbb\xbf", f"{fname} starts with a BOM"
        with p.open(encoding="utf-8", newline="") as fh:
            rows = list(_csv.DictReader(fh))
        assert rows, f"{fname} has no rows"
        blank = sum(1 for r in rows if not (r.get(key) or "").strip())
        assert not blank, f"{fname}: {blank}/{len(rows)} rows have a blank {key}"


# --- foreign lines must not resolve to a US namesake -------------------------
#
# Five coverage rows carried a bare US ticker for a foreign company, so every
# consumer resolving them got a DIFFERENT COMPANY's fundamentals: CSL Ltd
# (Australian biotech, A$55.7bn) was pulling Carlisle Companies at $13.4bn, and
# UCB SA (Belgian pharma, EUR 46.8bn) was pulling United Community Banks at
# $4.3bn. Their Country/Exchange columns had been auto-enriched FROM the wrong
# symbol, so the bad data looked self-consistent. Found 2026-07-27 via the
# delisted check's name-mismatch rule.

_FOREIGN_SYMBOL_EXPECTATIONS = {
    "CSL": "CSL.AX",            # not Carlisle Companies (NYSE: CSL)
    "UCB": "UCB.BR",            # not United Community Banks (NYSE: UCB)
    "IPN": "IPN.PA",            # not the SPDR S&P Intl Industrial ETF
    "MED": "MED.SW",            # not Medifast (NYSE: MED)
    "MOVE": "MOVE.SW",          # not Corvex (NASDAQ: MOVE)
    "ZEN": "ZEN.V",             # TSX-V, not TSX (.TO returns garbage)
    "COLOB DC": "COLO-B.CO",    # Yahoo hyphenates the B share class
    "GETIB SS": "GETI-B.ST",
}


def test_foreign_rows_resolve_to_their_own_listing():
    from ticker_utils import normalize_ticker, read_universe_csv

    df = read_universe_csv()
    wrong = []
    for ticker, expected in _FOREIGN_SYMBOL_EXPECTATIONS.items():
        row = df[df["Ticker"] == ticker]
        if row.empty:
            continue  # retired from coverage; nothing to protect
        row = row.iloc[0]
        got = normalize_ticker(row["Ticker"], company_name=row["Company Name"],
                               exchange=row["Exchange"])
        if got != expected:
            wrong.append(f"{ticker}: {got} (expected {expected})")
    assert not wrong, (
        "these resolve to a US namesake and would pull another company's "
        "fundamentals into the report and every export: " + "; ".join(wrong)
    )


# --- ADR boilerplate must not read as a different company -------------------


def test_adr_wrapper_names_match_their_issuer():
    """The universe records the INSTRUMENT, providers record the ISSUER. That
    scored ~0.5 and produced a standing weekly "name mismatch" flag for 22 rows
    -- noise in the one list that has to stay readable."""
    from difflib import SequenceMatcher

    from ticker_utils import normalize_company_for_comparison as norm

    pairs = [
        ("Can Fite Biopharma ADR Representing 300 Ord Shs", "Can-Fite BioPharma Ltd."),
        ("Inventiva ADR Representing Ord Shs", "Inventiva S.A."),
        ("Centessa Pharmaceuticals PLC - ADR", "Centessa Pharmaceuticals plc"),
        ("Grifols SA - ADR ADR Class B", "Grifols, S.A."),
    ]
    for recorded, provider in pairs:
        a, b = norm(recorded), norm(provider)
        score = 0.85 if (a in b or b in a) else SequenceMatcher(None, a, b).ratio()
        assert score >= 0.55, f"{recorded!r} vs {provider!r} scored {score:.2f}"


def test_adr_stripping_respects_word_boundaries():
    """"Cadrenal", "Madrigal" and "Adrian" all contain "adr"; truncating them
    would turn a real company into a stub and could match the wrong issuer."""
    from ticker_utils import normalize_company_for_comparison as norm

    assert norm("Cadrenal Therapeutics Inc") == "cadrenal therapeutics"
    assert "madrigal" in norm("Madrigal Pharmaceuticals")
    assert "adrian" in norm("Padres Adrian Corp")


def test_adr_stripping_adds_no_duplicate_company_collisions():
    """Guards the live file: this normalizer feeds the duplicate-company
    validation, so a broader strip must not manufacture false warnings."""
    import collections

    from ticker_utils import normalize_company_for_comparison as norm
    from ticker_utils import read_universe_csv

    names = list(read_universe_csv()["Company Name"])
    counts = collections.Counter(norm(n) for n in names if str(n).strip())
    dupes = {k: v for k, v in counts.items() if v > 1 and k}
    # Was `{"shimadzu": 2}` until 2026-08-25. That was not a normalizer artefact
    # but a real double-count: `SHMZF` (OTC) and `7701.T` (TSE) were the same
    # issuer on two lines, so Shimadzu's market cap landed twice in every
    # MedTech aggregate. The SHMZF row was deleted; the primary Tokyo line kept
    # it. Accepting the collision here is what let it sit unnoticed -- if this
    # fires again, check whether the pair is a genuine cross-listing before
    # widening the expectation.
    assert dupes == {}, (
        f"unexpected normalized-name collisions: {dupes}"
    )
