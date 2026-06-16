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
