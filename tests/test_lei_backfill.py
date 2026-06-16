"""Tests for the LEI (GLEIF) backfill — offline via an injected fetcher."""

import pandas as pd

from universe import lei_backfill as L


def _write(tmp_path, rows):
    cols = ["Ticker", "ISIN", "CIK", "Company Name"]
    p = tmp_path / "u.csv"
    pd.DataFrame(rows, columns=cols).to_csv(p, index=False)
    return p


def test_adds_lei_column_after_cik_and_fills(tmp_path):
    csv = _write(tmp_path, [
        ["AAPL", "US0378331005", "320193", "Apple Inc"],
        ["NOISIN", "", "111", "No ISIN Co"],
    ])
    fake = lambda isin, use_cache: ("HWUPKR0MPOU8FGXBT394", "Apple Inc.") if isin else ("", "")
    res = L.backfill(csv_path=csv, _fetcher=fake)
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    assert list(df.columns).index("LEI") == list(df.columns).index("CIK") + 1
    assert df[df["Ticker"] == "AAPL"]["LEI"].iloc[0] == "HWUPKR0MPOU8FGXBT394"
    assert df[df["Ticker"] == "NOISIN"]["LEI"].iloc[0] == ""   # no ISIN -> skipped
    assert res["filled"] == 1 and res["with_isin"] == 1


def test_skips_rows_that_already_have_lei(tmp_path):
    csv = _write(tmp_path, [["AAPL", "US0378331005", "320193", "Apple Inc"]])
    # pre-populate LEI
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    df = L._ensure_lei_column(df)
    df.at[0, "LEI"] = "EXISTING_LEI"
    df.to_csv(csv, index=False)

    def _boom(isin, use_cache):
        raise AssertionError("should not look up a row that already has an LEI")
    res = L.backfill(csv_path=csv, _fetcher=_boom)
    assert res["attempted"] == 0 and res["had_lei"] == 1


def test_limit_caps_lookups(tmp_path):
    csv = _write(tmp_path, [
        ["A", "ISIN_A", "1", "A Co"],
        ["B", "ISIN_B", "2", "B Co"],
        ["C", "ISIN_C", "3", "C Co"],
    ])
    calls = []
    fake = lambda isin, use_cache: (calls.append(isin) or ("LEI_" + isin, "n"))
    res = L.backfill(csv_path=csv, _fetcher=fake, limit=2)
    assert res["attempted"] == 2 and len(calls) == 2


def test_blank_isin_fetch_returns_empty():
    assert L.fetch_lei("", use_cache=False) == ("", "")
