"""Tests for the US symbol-directory watchdog.

The defect class this guards against is a watchdog that stops watching quietly:
a failed download reported as a quiet week, a first run reported as 7,500 new
listings, or a foreign line reported as delisted because it was never going to
be in a US exchange file in the first place.
"""
from __future__ import annotations

import io
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from universe import symbol_directory as sd  # noqa: E402

NASDAQ = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|"
    "Round Lot Size|ETF|NextShares\n"
    "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
    "SICK|Sick Co - Common Stock|Q|N|D|100|N|N\n"
    "ZVZZT|NASDAQ TEST STOCK|G|Y|N|100|N|N\n"
    "QQQ|Invesco QQQ Trust|Q|N|N|100|Y|N\n"
    "File Creation Time: 0806202611:01|||||||\n"
)
OTHER = (
    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|"
    "Test Issue|NASDAQ Symbol\n"
    "BRK.B|Berkshire Hathaway Inc. New Common Stock|N|BRK.B|N|40|N|BRK.B\n"
    "SPY|SPDR S&P 500|P|SPY|Y|100|N|SPY\n"
    "File Creation Time: 0806202611:01||||||\n"
)


def _opener(nasdaq=NASDAQ, other=OTHER, fail=None):
    def open_url(url):
        if fail and fail in url:
            raise OSError("connection reset")
        body = nasdaq if "nasdaqlisted" in url else other
        return io.BytesIO(body.encode("utf-8"))
    return open_url


# ---------------------------------------------------------------------- parse


def test_etfs_and_test_issues_are_dropped():
    syms, _ = sd.parse_directory(NASDAQ, sd.FILES["nasdaqlisted.txt"])
    assert set(syms) == {"AAPL", "SICK"}


def test_footer_timestamp_is_parsed_not_treated_as_a_row():
    syms, created = sd.parse_directory(NASDAQ, sd.FILES["nasdaqlisted.txt"])
    assert "FILE CREATION TIME: 0806202611:01" not in syms
    assert created == datetime(2026, 8, 6, 11, 1, tzinfo=timezone.utc)


def test_exchange_codes_map_and_unknown_codes_pass_through_raw():
    other = OTHER.replace("|N|BRK.B|", "|WAT|BRK.B|")
    syms, _ = sd.parse_directory(other, sd.FILES["otherlisted.txt"])
    assert syms["BRK.B"].exchange == "WAT"      # never guessed
    syms2, _ = sd.parse_directory(OTHER, sd.FILES["otherlisted.txt"])
    assert syms2["BRK.B"].exchange == "NYSE"


def test_dot_notation_matches_the_universe_convention():
    syms, _ = sd.parse_directory(OTHER, sd.FILES["otherlisted.txt"])
    assert "BRK.B" in syms


# ---------------------------------------------------------------------- fetch


def test_a_failed_download_is_inconclusive_never_an_empty_week():
    """The most dangerous possible bug here: reporting silence as 'no changes'."""
    res = sd.fetch_all(opener=_opener(fail="otherlisted"))
    assert res.status == "inconclusive"
    assert res.symbols == {}
    assert "otherlisted" in res.error


def test_a_file_that_parses_to_zero_rows_is_inconclusive():
    res = sd.fetch_all(opener=_opener(nasdaq="Symbol|Security Name\n"))
    assert res.status == "inconclusive"


def test_partial_failure_never_yields_a_partial_directory():
    """A half directory makes every symbol of the missing venue look delisted."""
    res = sd.fetch_all(opener=_opener(fail="nasdaqlisted"))
    assert res.status == "inconclusive" and not res.symbols


# ------------------------------------------------------------------ reconcile


UNIVERSE = [
    {"Ticker": "AAPL", "Exchange": "NASDAQ"},
    {"Ticker": "SICK", "Exchange": "NASDAQ"},
    {"Ticker": "GONE", "Exchange": "NYSE"},
    {"Ticker": "ROG.SW", "Exchange": "SIX"},        # foreign — must be ignored
    {"Ticker": "4519", "Exchange": "TSE"},          # foreign — must be ignored
]


def _current():
    res = sd.fetch_all(opener=_opener())
    return res.symbols


def test_foreign_lines_are_never_flagged_as_missing():
    rec = sd.reconcile(_current(), None, UNIVERSE, None)
    assert "ROG.SW" not in rec.universe_missing
    assert "4519" not in rec.universe_missing
    assert rec.checked_us_rows == 3


def test_a_us_row_absent_from_the_directory_is_flagged():
    rec = sd.reconcile(_current(), None, UNIVERSE, None)
    assert rec.universe_missing == ["GONE"]


def test_financial_status_flags_are_surfaced():
    rec = sd.reconcile(_current(), None, UNIVERSE, None)
    assert rec.universe_deficient == [("SICK", "D")]


def test_first_run_reports_a_baseline_not_thousands_of_new_listings():
    rec = sd.reconcile(_current(), None, UNIVERSE, None)
    assert rec.added == [] and rec.removed == []
    report = sd.render_report(rec, sd.FetchResult("ok"), date(2026, 8, 6), 2)
    assert "Baseline established" in report


def test_diff_reports_additions_and_removals_against_a_prior_snapshot():
    prior = dict(_current())
    prior["OLD"] = sd.Symbol("OLD", "Old Co", "NYSE", False, False)
    del prior["AAPL"]
    rec = sd.reconcile(_current(), prior, UNIVERSE, date(2026, 7, 30))
    assert [s.symbol for s in rec.added] == ["AAPL"]
    assert [s.symbol for s in rec.removed] == ["OLD"]


def test_a_covered_name_removed_this_period_is_called_out_separately():
    prior = dict(_current())
    prior["GONE"] = sd.Symbol("GONE", "Gone Inc", "NYSE", False, False)
    rec = sd.reconcile(_current(), prior, UNIVERSE, date(2026, 7, 30))
    assert rec.universe_removed == ["GONE"]


# ----------------------------------------------------------------- adjudicate


def _sec(payload):
    import json
    def open_url(url):
        return io.BytesIO(json.dumps(payload).encode("utf-8"))
    return open_url


def test_form_15_confirms_a_delisting():
    v = sd.confirm_absence(["NUVL"], {"NUVL": "1783036"}, sleep=lambda: None,
                           opener=_sec({"tickers": ["NUVL"], "filings": {"recent": {
                               "form": ["15-12G"], "filingDate": ["2026-07-27"]}}}))
    assert v[0].status == "delisted" and "15-12G" in v[0].detail


def test_no_registered_ticker_confirms_a_delisting():
    v = sd.confirm_absence(["DAY"], {"DAY": "1725057"}, sleep=lambda: None,
                           opener=_sec({"tickers": [], "filings": {"recent": {
                               "form": ["4"], "filingDate": ["2026-05-15"]}}}))
    assert v[0].status == "delisted"


def test_a_still_registered_ticker_is_reported_as_a_symbol_mismatch():
    v = sd.confirm_absence(["FI"], {"FI": "798354"}, sleep=lambda: None,
                           opener=_sec({"tickers": ["FISV"], "filings": {"recent": {
                               "form": ["10-Q"], "filingDate": ["2026-07-20"]}}}))
    assert v[0].status == "listed" and "FISV" in v[0].detail


def test_a_row_with_no_cik_is_inconclusive_never_delisted():
    """Deleting a live company from the universe is the unrecoverable mistake."""
    v = sd.confirm_absence(["AFMD"], {"AFMD": ""}, sleep=lambda: None)
    assert v[0].status == "inconclusive" and "no CIK" in v[0].detail


def test_an_unreachable_endpoint_is_inconclusive_never_delisted():
    def boom(url):
        raise OSError("timeout")
    v = sd.confirm_absence(["ACLX"], {"ACLX": "1786205"}, opener=boom,
                           sleep=lambda: None)
    assert v[0].status == "inconclusive"
