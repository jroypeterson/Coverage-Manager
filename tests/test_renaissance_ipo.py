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


# ── status distinction: "no IPO on record" vs "we were throttled" ──────────────
#
# Regression for 2026-07-28: a burst of HTTP 429s was reported to the operator as
# "no IPO on record: 9" when only 3 were real. Nothing was mis-cached, but a
# summary that under-reports its own ignorance means a name silently never gets
# an offer date. Same class as delisted_check's found/clean/inconclusive split.

def test_404_is_no_data_and_is_cached():
    with patch.object(R, "_request", return_value=_FakeResp(404)), \
         patch.object(R, "_record_call"), \
         patch.object(R, "cache_get", return_value=None), \
         patch.object(R, "cache_set") as cset:
        status, payload = R.fetch_ipo_date_ex("DEAD", "key")
    assert status == R.STATUS_NO_DATA
    assert payload is None
    assert cset.called, "an authoritative 404 must be cached so it is never re-hit"


def test_429_is_inconclusive_and_is_never_cached_or_counted():
    with patch.object(R, "_request", return_value=_FakeResp(429)), \
         patch.object(R, "_record_call") as rec, \
         patch.object(R, "cache_get", return_value=None), \
         patch.object(R, "cache_set") as cset:
        status, payload = R.fetch_ipo_date_ex("SKHY", "key")
    assert status == R.STATUS_INCONCLUSIVE
    assert payload is None
    assert not cset.called, "a throttled lookup must not be cached"
    assert not rec.called, "a throttled lookup must not burn quota"


def test_transport_exception_is_inconclusive():
    with patch.object(R, "_request", side_effect=OSError("dns")), \
         patch.object(R, "cache_get", return_value=None), \
         patch.object(R, "cache_set") as cset:
        status, _ = R.fetch_ipo_date_ex("BSP", "key")
    assert status == R.STATUS_INCONCLUSIVE
    assert not cset.called


def test_missing_api_key_is_inconclusive_not_no_data():
    status, _ = R.fetch_ipo_date_ex("BSP", "")
    assert status == R.STATUS_INCONCLUSIVE


def test_200_without_offer_date_is_a_real_answer():
    """The API knows the company and has no offer date -> that IS no_data."""
    with patch.object(R, "_request",
                      return_value=_FakeResp(200, {"companyName": "X", "offerDate": ""})), \
         patch.object(R, "_record_call"), \
         patch.object(R, "cache_get", return_value=None), \
         patch.object(R, "cache_set"):
        status, payload = R.fetch_ipo_date_ex("X", "key")
    assert status == R.STATUS_NO_DATA
    assert payload is None


def test_cached_empty_replays_as_no_data_not_inconclusive():
    empty = {"ticker": "D", "company_name": "", "offer_date": None}
    with patch.object(R, "cache_get", return_value=empty):
        assert R.fetch_ipo_date_ex("D", "key")[0] == R.STATUS_NO_DATA


def test_legacy_wrapper_still_returns_payload_or_none():
    with patch.object(R, "_request",
                      return_value=_FakeResp(200, {"companyName": "SK hynix",
                                                   "offerDate": "3/20/2024"})), \
         patch.object(R, "_record_call"), \
         patch.object(R, "cache_get", return_value=None), \
         patch.object(R, "cache_set"):
        assert R.fetch_ipo_date("SKHY", "key")["offer_date"] == "2024-03-20"
    with patch.object(R, "_request", return_value=_FakeResp(429)), \
         patch.object(R, "cache_get", return_value=None):
        assert R.fetch_ipo_date("SKHY", "key") is None
