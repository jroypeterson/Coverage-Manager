"""Tests for the SEC-CIK ticker-change / deregistration discovery check."""

import pandas as pd

from universe import ticker_change_check as tcc


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_norm_symbol_strips_class_separators():
    assert tcc._norm_symbol("BRK.B") == "BRKB"
    assert tcc._norm_symbol("brk-b") == "BRKB"
    assert tcc._norm_symbol(" abt ") == "ABT"
    assert tcc._norm_symbol("") == ""


def test_coerce_cik_handles_blank_float_and_padded():
    assert tcc._coerce_cik("1800") == 1800
    assert tcc._coerce_cik("0001800") == 1800   # zero-padded
    assert tcc._coerce_cik("1800.0") == 1800    # pandas float coercion
    assert tcc._coerce_cik("") is None
    assert tcc._coerce_cik("nan") is None
    assert tcc._coerce_cik(None) is None


# ── check_ticker_changes ─────────────────────────────────────────────────────

def _write_universe(tmp_path, rows):
    cols = ["Ticker", "CIK", "Company Name", "Sector (JP)", "Subsector (JP)"]
    df = pd.DataFrame(rows, columns=cols)
    p = tmp_path / "universe.csv"
    df.to_csv(p, index=False)
    return p


# Fake SEC bulk map: CIK -> {tickers, title}.
_FAKE_SEC = {
    1287865: {"tickers": ["MPT"], "title": "MEDICAL PROPERTIES TRUST INC"},  # was MPW
    731766:  {"tickers": ["UNH"], "title": "UNITEDHEALTH GROUP INC"},        # unchanged
    1652044: {"tickers": ["GOOGL", "GOOG"], "title": "ALPHABET INC"},        # share classes
    # CIK 9999999 intentionally absent -> deregistered
}

# submissions stubs: never hit the network in tests.
_SUBS_EMPTY = lambda cik: {"former_names": [], "tickers": [], "last_form": "", "last_date": ""}
_SUBS_DELISTED = lambda cik: {"former_names": [], "tickers": [], "last_form": "15-12G",
                              "last_date": "2026-03-27"}
_SUBS_ACTIVE = lambda cik: {"former_names": [], "tickers": ["CFLT"], "last_form": "10-Q",
                            "last_date": "2026-05-01"}


def _patch_sec(monkeypatch, cik_map=_FAKE_SEC, ok=True):
    monkeypatch.setattr(tcc, "load_sec_cik_map", lambda use_cache=True: (cik_map, ok))


def test_detects_ticker_mismatch(monkeypatch, tmp_path):
    _patch_sec(monkeypatch)
    csv = _write_universe(tmp_path, [
        ["MPW", "1287865", "Medical Properties Trust", "Healthcare Services", "HC Real Estate"],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False, submissions_fetcher=_SUBS_EMPTY)
    assert res["sec_fetched_ok"] is True
    assert len(res["changes"]) == 1
    r = res["changes"][0]
    assert r["ticker"] == "MPW"
    assert r["sec_tickers"] == "MPT"
    assert "MEDICAL PROPERTIES" in r["sec_title"]
    assert r["entity_renamed"] is False
    assert res["deregistered"] == []


def test_entity_renamed_flag_from_former_names(monkeypatch, tmp_path):
    """A non-empty SEC formerNames sets entity_renamed (strong real-rename tell)."""
    _patch_sec(monkeypatch)
    csv = _write_universe(tmp_path, [
        ["MPW", "1287865", "Medical Properties Trust", "Healthcare Services", ""],
    ])
    res = tcc.check_ticker_changes(
        csv_path=csv, use_cache=False,
        submissions_fetcher=lambda cik: {"former_names": ["OLDCO INC"], "tickers": [],
                                         "last_form": "", "last_date": ""})
    assert res["changes"][0]["entity_renamed"] is True
    assert res["changes"][0]["former_names"] == "OLDCO INC"


def test_no_flag_when_ticker_matches(monkeypatch, tmp_path):
    _patch_sec(monkeypatch)
    csv = _write_universe(tmp_path, [
        ["UNH", "731766", "UnitedHealth Group Inc", "Healthcare Services", "Managed Care"],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False, submissions_fetcher=_SUBS_EMPTY)
    assert res["changes"] == []
    assert res["deregistered"] == []


def test_share_class_member_not_flagged(monkeypatch, tmp_path):
    """A row tracking GOOG must not flag just because SEC lists GOOGL first."""
    _patch_sec(monkeypatch)
    csv = _write_universe(tmp_path, [
        ["GOOG", "1652044", "Alphabet Inc", "Tech", ""],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False, submissions_fetcher=_SUBS_EMPTY)
    assert res["changes"] == []


def test_deregistered_confirmed_when_submissions_has_no_ticker(monkeypatch, tmp_path):
    _patch_sec(monkeypatch)
    csv = _write_universe(tmp_path, [
        ["ZZZZ", "9999999", "Gone Corp", "Biopharma", ""],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False, submissions_fetcher=_SUBS_DELISTED)
    assert len(res["deregistered"]) == 1
    assert res["deregistered"][0]["ticker"] == "ZZZZ"
    assert res["deregistered"][0]["last_form"] == "15-12G"
    assert res["changes"] == []
    assert res["active_omissions"] == 0


def test_form15_overrides_lagging_ticker(monkeypatch, tmp_path):
    """A filed Form 15 deregisters even when submissions `tickers` still lists
    the symbol (the field lags Form 15) — the SEMR/EHAB/ONTF post-acquisition case."""
    _patch_sec(monkeypatch)
    csv = _write_universe(tmp_path, [
        ["SEMR", "1831840", "Semrush Holdings", "SaaS", ""],
    ])
    res = tcc.check_ticker_changes(
        csv_path=csv, use_cache=False,
        submissions_fetcher=lambda cik: {"former_names": [], "tickers": ["SEMR"],
                                         "last_form": "15-12B", "last_date": "2026-05-08"})
    assert len(res["deregistered"]) == 1
    assert res["active_omissions"] == 0


def test_bulk_omission_dropped_when_submissions_still_active(monkeypatch, tmp_path):
    """CIK absent from the bulk file but submissions shows a live ticker -> active
    bulk-omission, NOT a deregistration (the ACLX/ATAI false-positive class)."""
    _patch_sec(monkeypatch)
    csv = _write_universe(tmp_path, [
        ["ACLX", "1786205", "Arcellx Inc", "Biopharma", ""],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False, submissions_fetcher=_SUBS_ACTIVE)
    assert res["deregistered"] == []
    assert res["active_omissions"] == 1


def test_blank_cik_skipped(monkeypatch, tmp_path):
    _patch_sec(monkeypatch)
    csv = _write_universe(tmp_path, [
        ["000100.KS", "", "Yuhan Corporation", "Biopharma", ""],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False, submissions_fetcher=_SUBS_EMPTY)
    assert res["checked"] == 0
    assert res["changes"] == [] and res["deregistered"] == []


def test_foreign_suffixed_ticker_not_flagged(monkeypatch, tmp_path):
    """A cross-listed row (DIA.MI) whose CIK SEC maps to a US ADR must NOT be
    flagged as a change — only plain US-style symbols are mismatch-eligible."""
    monkeypatch.setattr(tcc, "load_sec_cik_map",
                        lambda use_cache=True: ({111: {"tickers": ["DIA"], "title": "SOME ADR"}}, True))
    csv = _write_universe(tmp_path, [
        ["DIA.MI", "111", "Some Cross-Listed Co", "Tech", ""],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False, submissions_fetcher=_SUBS_EMPTY)
    assert res["changes"] == []         # foreign-suffixed -> mismatch check skipped
    assert res["deregistered"] == []    # CIK present, so not deregistered either
    assert res["checked"] == 1


def test_sec_unavailable_sets_flag(monkeypatch, tmp_path):
    _patch_sec(monkeypatch, cik_map={}, ok=False)
    csv = _write_universe(tmp_path, [
        ["MPW", "1287865", "Medical Properties Trust", "Healthcare Services", ""],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False, submissions_fetcher=_SUBS_EMPTY)
    assert res["sec_fetched_ok"] is False
    assert res["changes"] == [] and res["deregistered"] == []
    assert res["checked"] == 0


# ── write_report ─────────────────────────────────────────────────────────────

def test_write_report_emits_files_and_sec_symbol(tmp_path):
    res = {
        "checked": 1, "sec_cik_count": 5, "sec_fetched_ok": True,
        "changes": [{"ticker": "MPW", "sec_tickers": "MPT", "cik": 1287865,
                     "recorded_name": "Medical Properties Trust",
                     "sec_title": "MEDICAL PROPERTIES TRUST INC",
                     "former_names": "", "entity_renamed": False,
                     "sector_jp": "Healthcare Services", "subsector_jp": ""}],
        "deregistered": [],
    }
    tcc.write_report(res, reports_dir=tmp_path, run_date="2026-06-15")
    md = (tmp_path / "ticker_change_check_2026-06-15.md").read_text(encoding="utf-8")
    assert "MPW" in md and "MPT" in md
    csv_text = (tmp_path / "ticker_change_check_2026-06-15.csv").read_text(encoding="utf-8")
    assert "change,MPW,MPT" in csv_text


def test_write_report_sec_unavailable_note(tmp_path):
    res = {"checked": 0, "sec_cik_count": 0, "sec_fetched_ok": False,
           "changes": [], "deregistered": []}
    tcc.write_report(res, reports_dir=tmp_path, run_date="2026-06-15")
    md = (tmp_path / "ticker_change_check_2026-06-15.md").read_text(encoding="utf-8")
    assert "unavailable" in md.lower()


# ── settled symbol splits (board #345) ───────────────────────────────────────
#
# A mismatch the alias store already adjudicated is not a review item. Before
# this split, Fiserv's FI/FISV was re-reported every week under guidance that
# said "leave as-is" -- a permanently-raised flag, which trains the reader to
# skim the exact section the next real rename lands in.

_FISERV_SEC = {798354: {"tickers": ["FISV"], "title": "FISERV INC"}}


def _alias_index(monkeypatch, tmp_path, entries):
    """Point the module's alias lookup at a synthetic store."""
    import json

    import universe.aliases as aliases_mod

    path = tmp_path / "ticker_aliases.json"
    path.write_text(json.dumps({"schema_version": aliases_mod.SCHEMA_VERSION,
                                "entries": entries}), encoding="utf-8")
    monkeypatch.setattr(aliases_mod, "ALIASES_PATH", path)


_FISERV_ALIAS = [{
    "canonical": "FI", "aliases": ["FISV"], "cik": "798354",
    "verified": "2026-08-27",
    "sources": ["OpenFIGI 2026-08-27", "Nasdaq Trader 2026-08-26"],
}]


def test_a_mismatch_covered_by_the_alias_store_is_settled_not_a_change(monkeypatch, tmp_path):
    _patch_sec(monkeypatch, _FISERV_SEC)
    _alias_index(monkeypatch, tmp_path, _FISERV_ALIAS)
    csv = _write_universe(tmp_path, [["FI", "798354", "Fiserv Inc.", "Financials", ""]])

    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False,
                                   submissions_fetcher=_SUBS_EMPTY)
    assert res["changes"] == []
    assert [r["ticker"] for r in res["settled"]] == ["FI"]
    assert res["settled"][0]["sec_tickers"] == "FISV"


def test_an_uncovered_mismatch_is_still_a_change(monkeypatch, tmp_path):
    """The store must not blanket-silence the section it lives in."""
    _patch_sec(monkeypatch)
    _alias_index(monkeypatch, tmp_path, _FISERV_ALIAS)
    csv = _write_universe(tmp_path, [
        ["MPW", "1287865", "Medical Properties Trust", "Healthcare Services", ""],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False,
                                   submissions_fetcher=_SUBS_EMPTY)
    assert [r["ticker"] for r in res["changes"]] == ["MPW"]
    assert res["settled"] == []


def test_an_alias_for_a_DIFFERENT_symbol_does_not_settle_this_mismatch(monkeypatch, tmp_path):
    """Settling keys on the pair, not on the row merely having any alias entry."""
    _patch_sec(monkeypatch, {798354: {"tickers": ["ZZZZ"], "title": "FISERV INC"}})
    _alias_index(monkeypatch, tmp_path, _FISERV_ALIAS)
    csv = _write_universe(tmp_path, [["FI", "798354", "Fiserv Inc.", "Financials", ""]])

    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False,
                                   submissions_fetcher=_SUBS_EMPTY)
    assert [r["ticker"] for r in res["changes"]] == ["FI"]
    assert res["settled"] == []


def test_an_unreadable_alias_store_leaves_the_mismatch_as_a_change(monkeypatch, tmp_path):
    """Degrade toward NOISE, never toward silence: a store we cannot read must
    not be able to suppress a finding."""
    import universe.aliases as aliases_mod

    bad = tmp_path / "ticker_aliases.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(aliases_mod, "ALIASES_PATH", bad)
    _patch_sec(monkeypatch, _FISERV_SEC)
    csv = _write_universe(tmp_path, [["FI", "798354", "Fiserv Inc.", "Financials", ""]])

    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False,
                                   submissions_fetcher=_SUBS_EMPTY)
    assert [r["ticker"] for r in res["changes"]] == ["FI"]
    assert res["settled"] == []


def test_the_report_separates_settled_from_review_and_names_the_store(monkeypatch, tmp_path):
    _patch_sec(monkeypatch, _FISERV_SEC)
    _alias_index(monkeypatch, tmp_path, _FISERV_ALIAS)
    csv = _write_universe(tmp_path, [["FI", "798354", "Fiserv Inc.", "Financials", ""]])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False,
                                   submissions_fetcher=_SUBS_EMPTY)

    paths = tcc.write_report(res, reports_dir=tmp_path, run_date="2026-08-27")
    md = (tmp_path / "ticker_change_check_2026-08-27.md").read_text(encoding="utf-8")
    assert "Settled symbol splits" in md
    assert "ticker_aliases.json" in md
    assert "Ticker mismatches — review & remap" not in md
    csv_text = (tmp_path / "ticker_change_check_2026-08-27.csv").read_text(encoding="utf-8")
    assert "settled,FI,FISV" in csv_text
    assert paths["md_path"]


def test_the_report_no_longer_teaches_readers_to_ignore_the_fiserv_pair(monkeypatch, tmp_path):
    """The guidance string named FISV/FI as its worked example of 'leave as-is'.

    That one sentence is why a live defect was dismissed weekly for months, so it
    is pinned out rather than merely deleted.
    """
    _patch_sec(monkeypatch)
    _alias_index(monkeypatch, tmp_path, [])
    csv = _write_universe(tmp_path, [
        ["MPW", "1287865", "Medical Properties Trust", "Healthcare Services", ""],
    ])
    res = tcc.check_ticker_changes(csv_path=csv, use_cache=False,
                                   submissions_fetcher=_SUBS_EMPTY)
    tcc.write_report(res, reports_dir=tmp_path, run_date="2026-08-27")
    md = (tmp_path / "ticker_change_check_2026-08-27.md").read_text(encoding="utf-8")
    # The guidance still discusses "leave as-is" as one of three verdicts, which
    # is correct. What must never come back is naming this pair as the worked
    # example of it -- that sentence is what made the dismissal automatic.
    assert "FISV" not in md and "`FI`" not in md
    assert "ticker_aliases.json" in md
