"""Tests for the blank-CIK re-probe (`universe/cik_backfill.py`).

The module exists because a CIK is not a static property of a company — it is a
fact about whether that company has *registered with the SEC yet*, which
changes. SpaceX sat in the portfolio with a blank CIK, invisible to every
CIK-keyed lane, until an independent cross-check found 40 Form 3s from its board
(2026-07-25).

Because this module WRITES THE WHOLE UNIVERSE CSV BACK, most of what is pinned
here is about not damaging it.
"""
from __future__ import annotations

import pandas as pd
import pytest

from universe import cik_backfill


@pytest.fixture
def universe_csv(tmp_path, monkeypatch):
    """A tiny universe CSV wired in as the module's target."""
    path = tmp_path / "coverage_universe_tickers.csv"

    def _write(rows):
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
        monkeypatch.setattr(cik_backfill, "CSV_PATH", path)
        return path

    return _write


def _rows(*specs):
    return [{"Ticker": t, "Company Name": n, "CIK": c, "Country (HQ)": country}
            for t, n, c, country in specs]


# --- the core contract -----------------------------------------------------


def test_fills_a_blank_cik(universe_csv, monkeypatch):
    path = universe_csv(_rows(("SPCX", "Space Exploration Technologies", "", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map", lambda: {"SPCX": ("1181412", "Space Exploration Technologies Corp")})

    result = cik_backfill.main()

    assert result["filled"] == 1 and result["fetched_ok"] is True
    assert pd.read_csv(path, dtype=str)["CIK"].iloc[0] == "1181412"


def test_never_overwrites_an_existing_cik(universe_csv, monkeypatch):
    """A populated CIK that disagrees means the TICKER moved — that is
    `ticker_change_check`'s finding to surface, not this module's to silently
    'fix'. A CIK is stable across a ticker change; that invariant is load-bearing."""
    path = universe_csv(_rows(("AAPL", "Apple Inc", "320193", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map",
                        lambda: {"AAPL": ("99999999", "Apple Inc")})

    result = cik_backfill.main()

    assert result["filled"] == 0
    assert pd.read_csv(path, dtype=str)["CIK"].iloc[0] == "320193"


def test_dry_run_reports_without_writing(universe_csv, monkeypatch):
    path = universe_csv(_rows(("SPCX", "Space Exploration Technologies", "", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map", lambda: {"SPCX": ("1181412", "Space Exploration Technologies Corp")})

    result = cik_backfill.main(dry_run=True)

    assert result["filled"] == 1
    assert pd.read_csv(path, dtype=str, keep_default_na=False)["CIK"].iloc[0] == ""


# --- failure must not look like success ------------------------------------


def test_sec_failure_changes_nothing_and_says_so(universe_csv, monkeypatch):
    """An empty SEC map must never read as 'no ticker resolves'. That would look
    like a clean run while quietly reopening the exact gap this step closes."""
    path = universe_csv(_rows(("SPCX", "SpaceX", "", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map", lambda: {})

    result = cik_backfill.main()

    assert result["fetched_ok"] is False
    assert result["filled"] == 0
    assert result["still_blank"] == 1
    assert pd.read_csv(path, dtype=str, keep_default_na=False)["CIK"].iloc[0] == ""


# --- CSV safety: this module rewrites the whole file -----------------------


def test_literal_na_values_survive_the_round_trip(universe_csv, monkeypatch):
    """A bare `pd.read_csv` maps "NA"/"N/A"/"NULL" to NaN, and writing the file
    back would persist that as an empty cell — destroying a curated field while
    'successfully' filling a CIK. Hence `read_universe_csv`."""
    path = universe_csv(_rows(
        ("SPCX", "SpaceX", "", "NA"),          # Namibia, not "not available"
        ("XYZ", "N/A Holdings", "12345", "NULL"),
    ))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map", lambda: {"SPCX": ("1181412", "Space Exploration Technologies Corp")})

    cik_backfill.main()

    out = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert list(out["Country (HQ)"]) == ["NA", "NULL"]
    assert out["Company Name"].iloc[1] == "N/A Holdings"


def test_existing_ciks_do_not_gain_a_float_suffix(universe_csv, monkeypatch):
    """The `.0` corruption: a bare read infers a blank-containing int column as
    float64, and `1125376.0` breaks every SEC lookup that consumes it."""
    path = universe_csv(_rows(
        ("AAPL", "Apple Inc", "320193", "US"),
        ("SPCX", "Space Exploration Technologies", "", "US"),
    ))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map", lambda: {"SPCX": ("1181412", "Space Exploration Technologies Corp")})

    cik_backfill.main()

    ciks = pd.read_csv(path, dtype=str, keep_default_na=False)["CIK"]
    assert list(ciks) == ["320193", "1181412"]
    assert not any("." in c for c in ciks)


# --- identity: a ticker string is not a company -----------------------------


def test_refuses_to_bind_a_row_whose_company_name_disagrees(universe_csv,
                                                            monkeypatch, caplog):
    """The recycled-ticker case. Tickers move between issuers -- that is the
    entire premise of the sibling delisted_check -- and the rows this module
    targets (private names under provisional symbols) are the most exposed.
    Writing another company's CIK would make insider_ownership and
    earnings_agent silently pull the wrong filings."""
    path = universe_csv(_rows(("ABCD", "Some Private Biotech Inc", "", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map",
                        lambda: {"ABCD": ("999", "Unrelated Mining Corporation")})

    with caplog.at_level("WARNING"):
        result = cik_backfill.main()

    assert result["filled"] == 0
    assert result["rejected_name_mismatch"] == 1
    assert pd.read_csv(path, dtype=str, keep_default_na=False)["CIK"].iloc[0] == ""
    assert "SKIPPED ABCD" in caplog.text, "a refusal must never be silent"


def test_a_name_shorthand_is_skipped_not_guessed(universe_csv, monkeypatch):
    """Deliberate, documented false NEGATIVE. A universe row reading "SpaceX"
    against SEC's "Space Exploration Technologies Corp" is skipped with a
    warning rather than filled. That is the intended trade: a warned skip is
    visible and a human resolves it in seconds, whereas a wrong CIK looks like
    data and propagates to seven downstream projects."""
    universe_csv(_rows(("SPCX", "SpaceX", "", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map",
                        lambda: {"SPCX": ("1181412",
                                          "Space Exploration Technologies Corp")})

    result = cik_backfill.main()

    assert result["filled"] == 0 and result["rejected_name_mismatch"] == 1


def test_a_blank_company_name_cannot_disconfirm(universe_csv, monkeypatch):
    """No recorded name means no basis to reject -- the gate must not become a
    blanket refusal for sparsely-populated rows."""
    path = universe_csv(_rows(("SPCX", "", "", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map",
                        lambda: {"SPCX": ("1181412", "Space Exploration Technologies Corp")})

    assert cik_backfill.main()["filled"] == 1
    assert pd.read_csv(path, dtype=str)["CIK"].iloc[0] == "1181412"


def test_foreign_symbols_skip_the_normalized_fallback(universe_csv, monkeypatch):
    """`4503.T` normalizes to `4503T`, which could collide with an unrelated US
    symbol. build_norm_index only rules out collisions among SEC's OWN symbols;
    it cannot stop a universe-side foreign ticker landing on a real US one."""
    path = universe_csv(_rows(("4503.T", "Astellas Pharma Inc", "", "JP")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map",
                        lambda: {"4503T": ("777", "Astellas Pharma Inc")})

    result = cik_backfill.main()

    assert result["filled"] == 0
    assert pd.read_csv(path, dtype=str, keep_default_na=False)["CIK"].iloc[0] == ""


# --- share-class separator normalization -----------------------------------


def test_share_class_separator_is_matched_across_forms(universe_csv, monkeypatch):
    """SEC writes `BRK-B`; the universe commonly carries `BRK.B`. Exact string
    matching leaves a registered issuer blank and CIK-keyed lanes skip it."""
    path = universe_csv(_rows(("BRK.B", "Berkshire Hathaway Inc", "", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map", lambda: {"BRK-B": ("1067983", "Berkshire Hathaway Inc")})

    result = cik_backfill.main()

    assert result["filled"] == 1
    assert pd.read_csv(path, dtype=str)["CIK"].iloc[0] == "1067983"


def test_ambiguous_normalized_symbols_are_not_guessed():
    """Two SEC symbols collapsing to one key means we cannot tell which issuer a
    row meant. A blank CIK is visibly missing; a WRONG one silently pulls another
    company's filings — so ambiguity must leave the row alone."""
    index = cik_backfill.build_norm_index({"AB-C": ("111", "A"), "AB.C": ("222", "B"),
                                           "SPCX": ("1181412", "SpaceX")})
    assert "ABC" not in index
    assert index["SPCX"] == "1181412"


def test_exact_match_wins_over_normalized(universe_csv, monkeypatch):
    path = universe_csv(_rows(("ABC", "Exact Co", "", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map",
                        lambda: {"ABC": ("111", "Exact Co"), "A.B.C": ("222", "Exact Co")})

    cik_backfill.main()

    assert pd.read_csv(path, dtype=str)["CIK"].iloc[0] == "111"


# --- operational hygiene ---------------------------------------------------


def test_log_lines_are_ascii(universe_csv, monkeypatch, caplog):
    """The scheduled task redirects stdout to a cp1252 log; one non-ASCII
    character raises UnicodeEncodeError and kills the run before its heartbeat."""
    universe_csv(_rows(("SPCX", "Space Exploration Technologies Corp.", "", "US")))
    monkeypatch.setattr(cik_backfill, "fetch_sec_cik_map", lambda: {"SPCX": ("1181412", "Space Exploration Technologies Corp")})

    with caplog.at_level("INFO"):
        cik_backfill.main()

    "\n".join(r.getMessage() for r in caplog.records).encode("ascii")


def test_sec_fetch_is_not_a_second_implementation():
    """The SEC bulk file is downloaded once, by the module that already caches
    it -- two uncoordinated fetchers meant weekly steps 4a and 4b pulled the
    same ~1 MB back-to-back and only one survived a brief SEC outage."""
    import inspect

    src = inspect.getsource(cik_backfill)
    assert "load_sec_cik_map" in src
    assert "sec.gov" not in src, "the URL should live in exactly one module"
