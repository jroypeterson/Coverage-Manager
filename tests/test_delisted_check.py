"""Tests for the delisted/recycled ticker check, focused on the
price-recency hardening (catches clean acquisitions that keep `.info` stale)."""

from datetime import date

import pandas as pd

from universe.delisted_check import (
    _classify,
    _price_is_stale,
    _probe_recent_price,
    PRICE_STALE_DAYS,
)

TODAY = date(2026, 6, 13)


def _fresh(days_ago):
    return (date.fromordinal(TODAY.toordinal() - days_ago)).isoformat()


# ── _price_is_stale ─────────────────────────────────────────────────────────

def test_price_stale_when_missing():
    assert _price_is_stale("", today=TODAY) is True
    assert _price_is_stale(None, today=TODAY) is True


def test_price_stale_when_bad_format():
    assert _price_is_stale("not-a-date", today=TODAY) is True


def test_price_fresh_within_window():
    assert _price_is_stale(_fresh(2), today=TODAY) is False
    assert _price_is_stale(_fresh(PRICE_STALE_DAYS), today=TODAY) is False


def test_price_stale_beyond_window():
    assert _price_is_stale(_fresh(PRICE_STALE_DAYS + 1), today=TODAY) is True
    assert _price_is_stale(_fresh(90), today=TODAY) is True


# ── _probe_recent_price (direct, with a fake yfinance Ticker) ───────────────

class _FakeTicker:
    def __init__(self, df=None, raises=False):
        self._df = df
        self._raises = raises
        self.called_with = None

    def history(self, **kwargs):
        self.called_with = kwargs
        if self._raises:
            raise RuntimeError("429 Too Many Requests")
        return self._df


def _hist(dates, tz="America/New_York"):
    idx = pd.to_datetime(dates).tz_localize(tz)
    return pd.DataFrame({"Open": [1.0] * len(dates), "Close": [2.0] * len(dates)}, index=idx)


def test_probe_returns_last_bar_date_tz_aware():
    t = _FakeTicker(_hist(["2026-06-10", "2026-06-11", "2026-06-12"]))
    ran, last = _probe_recent_price(t)
    assert ran is True
    assert last == "2026-06-12"  # exchange-local trading date, not UTC-shifted
    # essential: raise_errors must be passed so 429s don't masquerade as dead feeds
    assert t.called_with.get("raise_errors") is True


def test_probe_empty_frame_is_dead_feed():
    ran, last = _probe_recent_price(_FakeTicker(pd.DataFrame()))
    assert ran is True and last == ""


def test_probe_all_nan_close_is_dead_feed():
    idx = pd.to_datetime(["2026-06-12"]).tz_localize("UTC")
    df = pd.DataFrame({"Close": [float("nan")]}, index=idx)
    ran, last = _probe_recent_price(_FakeTicker(df))
    assert ran is True and last == ""


def test_probe_exception_marks_not_run():
    ran, last = _probe_recent_price(_FakeTicker(raises=True))
    assert ran is False and last == ""


# ── _classify: price-recency rule (reads the frozen price_stale decision) ────

def _row(name="Exact Sciences Corporation"):
    return {"Company Name": name, "Sector (JP)": "MedTech", "Subsector (JP)": "Diagnostics"}


def _identity(name="Exact Sciences Corporation", quote="EQUITY",
              last="", probe_ran=True, stale=False):
    return {
        "quoteType": quote,
        "longName": name,
        "shortName": name,
        "last_close_date": last,
        "price_probe_ran": probe_ran,
        "price_stale": stale,
    }


def test_clean_acquisition_flagged_despite_stale_info():
    # Yahoo keeps longName populated post-delisting; price feed dead → price_stale.
    flagged, reason = _classify(_row(), _identity(last="", stale=True))
    assert flagged is True
    assert "no recent price data" in reason


def test_old_last_bar_flagged():
    flagged, reason = _classify(_row(), _identity(last=_fresh(90), stale=True))
    assert flagged is True
    assert "no recent price data" in reason and "last bar=" in reason


def test_live_ticker_not_flagged():
    flagged, reason = _classify(_row(), _identity(last=_fresh(1), stale=False))
    assert flagged is False, reason


def test_transient_probe_failure_does_not_trigger_price_flag():
    # probe didn't run (network blip) → price_stale is False → not flagged on price;
    # falls through to identity rules, which pass for a matching name.
    flagged, reason = _classify(_row(), _identity(last="", probe_ran=False, stale=False))
    assert flagged is False, reason


def test_no_identity_still_flagged():
    flagged, reason = _classify(_row(), {})
    assert flagged is True
    assert "no yfinance data" in reason


def test_non_equity_recycle_flagged():
    flagged, reason = _classify(_row(), _identity(name="Some ETF", quote="ETF",
                                                  last=_fresh(1), stale=False))
    assert flagged is True
    assert "non-equity" in reason


def test_name_mismatch_flagged_when_price_fresh():
    flagged, reason = _classify(
        _row(), _identity(name="Completely Different Issuer Holdings",
                          last=_fresh(1), stale=False))
    assert flagged is True
    assert "mismatch" in reason
