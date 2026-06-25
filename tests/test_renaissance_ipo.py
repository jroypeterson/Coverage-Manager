"""Tests for the Renaissance Capital IPO-date verifier — offline (no HTTP)."""

from datetime import date
from unittest.mock import patch

import pytest

from providers import renaissance_ipo as R


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


# ── pure helpers ───────────────────────────────────────────────────────────────

def test_parse_offer_date_formats():
    assert R._parse_offer_date("3/20/2024") == date(2024, 3, 20)
    assert R._parse_offer_date("03/20/2024") == date(2024, 3, 20)
    assert R._parse_offer_date("2024-03-20") == date(2024, 3, 20)
    assert R._parse_offer_date("") is None
    assert R._parse_offer_date("garbage") is None


def test_lockup_dates():
    assert R.lockup_dates("2024-03-20") == ("2024-06-18", "2024-09-16")
    assert R.lockup_dates("") == ("", "")


def test_ipo_age_buckets():
    as_of = date(2024, 4, 1)
    assert R.ipo_age("2024-03-20", as_of=as_of) == (12, "<30d")
    assert R.ipo_age("2024-01-01", as_of=as_of)[1] == "90-180d"
    assert R.ipo_age("2022-01-01", as_of=as_of)[1] == ">2y"
    assert R.ipo_age("", as_of=as_of) == (None, "")


# ── network path (mocked) ────────────────────────────────────────────────────────

@patch("providers.renaissance_ipo.cache_set")
@patch("providers.renaissance_ipo.cache_get", return_value=None)
@patch("providers.renaissance_ipo.calls_this_month", return_value=0)
@patch("providers.renaissance_ipo._record_call")
@patch("providers.renaissance_ipo._request")
def test_fetch_200_parses_and_records_and_caches(mreq, mrec, mcnt, mget, mset):
    mreq.return_value = _FakeResp(200, {
        "tickerSymbol": "RDDT", "companyName": "Reddit", "offerDate": "3/20/2024",
    })
    res = R.fetch_ipo_date("RDDT", api_key="k", cik="1713445")
    assert res == {"ticker": "RDDT", "company_name": "Reddit", "offer_date": "2024-03-20"}
    mrec.assert_called_once()                       # a 200 counts against quota
    mset.assert_called_once()                       # and is cached
    # CIK preferred over ticker in the query
    assert mreq.call_args.args[0] == {"CIK": "1713445"}


@patch("providers.renaissance_ipo.cache_set")
@patch("providers.renaissance_ipo.cache_get", return_value=None)
@patch("providers.renaissance_ipo.calls_this_month", return_value=0)
@patch("providers.renaissance_ipo._record_call")
@patch("providers.renaissance_ipo._request")
def test_fetch_404_returns_none_but_caches_empty(mreq, mrec, mcnt, mget, mset):
    mreq.return_value = _FakeResp(404)
    res = R.fetch_ipo_date("ZZZZ", api_key="k")
    assert res is None
    mrec.assert_called_once()                       # 404 is authenticated -> counts
    # authoritative "no IPO" is cached so it's never re-hit
    cached = mset.call_args.args[2]
    assert cached["offer_date"] is None


@patch("providers.renaissance_ipo.cache_set")
@patch("providers.renaissance_ipo.cache_get", return_value=None)
@patch("providers.renaissance_ipo.calls_this_month", return_value=0)
@patch("providers.renaissance_ipo._record_call")
@patch("providers.renaissance_ipo._request")
def test_fetch_transient_5xx_returns_none_uncached(mreq, mrec, mcnt, mget, mset):
    mreq.return_value = _FakeResp(503)
    assert R.fetch_ipo_date("RDDT", api_key="k") is None
    mrec.assert_not_called()                        # don't burn quota on a transient
    mset.assert_not_called()                        # don't cache a transient


@patch("providers.renaissance_ipo._request")
@patch("providers.renaissance_ipo.cache_get")
def test_cache_hit_short_circuits_network(mget, mreq):
    mget.return_value = {"ticker": "RDDT", "company_name": "Reddit", "offer_date": "2024-03-20"}
    res = R.fetch_ipo_date("RDDT", api_key="k")
    assert res["offer_date"] == "2024-03-20"
    mreq.assert_not_called()


@patch("providers.renaissance_ipo._request")
@patch("providers.renaissance_ipo.cache_get", return_value=None)
@patch("providers.renaissance_ipo.calls_this_month", return_value=R.MONTHLY_CALL_CAP)
def test_budget_cap_raises_before_network(mcnt, mget, mreq):
    with pytest.raises(R.RenaissanceBudgetError):
        R.fetch_ipo_date("RDDT", api_key="k")
    mreq.assert_not_called()


def test_no_api_key_returns_none():
    assert R.fetch_ipo_date("RDDT", api_key="") is None


def test_blank_ticker_returns_none():
    assert R.fetch_ipo_date("", api_key="k") is None
