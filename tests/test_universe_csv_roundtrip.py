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
# The committed master AND exports/universe.csv both carry a UTF-8 BOM, so that
# is the canonical form. Six modules used pandas' default UTF-8 and silently
# stripped it, so the file's encoding depended on which step happened to write
# last -- producing spurious whole-file first-line diffs and flipping the header
# cell a stdlib-`csv` consumer sees between "Ticker" and "﻿Ticker".
# Centralizing the READ was done in 2026-06-20 for the same class of reason;
# the write was left uncentralized and drifted. This test is the ratchet.

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


def test_write_universe_csv_round_trips_the_bom():
    import pandas as pd

    from ticker_utils import read_universe_csv, write_universe_csv

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "u.csv"
        write_universe_csv(pd.DataFrame([{"Ticker": "AAPL", "CIK": "320193"}]), p)
        assert p.read_bytes()[:3] == b"\xef\xbb\xbf", "canonical BOM must survive"
        assert read_universe_csv(p)["Ticker"].iloc[0] == "AAPL", (
            "and the BOM must not leak into the first column name"
        )
