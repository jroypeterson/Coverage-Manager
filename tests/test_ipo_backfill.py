"""Tests for the IPO-date backfill — offline via an injected fetcher."""

import pandas as pd

from universe import ipo_backfill as B


def _write(tmp_path, rows):
    cols = ["Ticker", "Year Listed", "CIK", "Company Name"]
    p = tmp_path / "u.csv"
    pd.DataFrame(rows, columns=cols).to_csv(p, index=False)
    return p


def test_adds_columns_after_year_listed_and_fills(tmp_path):
    csv = _write(tmp_path, [
        ["RDDT", "2024", "1713445", "Reddit"],
        ["AAPL", "1980", "320193", "Apple Inc"],
    ])

    def fake(ticker, cik):
        if ticker == "RDDT":
            return {"ticker": "RDDT", "company_name": "Reddit", "offer_date": "2024-03-20"}
        return None  # no IPO on record

    res = B.backfill(csv_path=csv, _fetcher=fake)
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)

    # columns land right after Year Listed, in order
    yi = list(df.columns).index("Year Listed")
    assert list(df.columns)[yi + 1: yi + 4] == ["IPO Date", "Est Lockup 90d", "Est Lockup 180d"]

    row = df[df["Ticker"] == "RDDT"].iloc[0]
    assert row["IPO Date"] == "2024-03-20"
    assert row["Est Lockup 90d"] == "2024-06-18"   # +90d
    assert row["Est Lockup 180d"] == "2024-09-16"  # +180d
    assert df[df["Ticker"] == "AAPL"].iloc[0]["IPO Date"] == ""  # no-data stays blank

    assert res["filled"] == 1 and res["no_data"] == 1 and res["attempted"] == 2


def test_skips_rows_that_already_have_ipo_date(tmp_path):
    csv = _write(tmp_path, [["RDDT", "2024", "1713445", "Reddit"]])
    df = B.read_universe_csv(csv)
    df = B._ensure_ipo_columns(df)
    df.at[0, "IPO Date"] = "2024-03-20"
    df.to_csv(csv, index=False)

    def _boom(ticker, cik):
        raise AssertionError("should not look up a row that already has an IPO Date")

    res = B.backfill(csv_path=csv, _fetcher=_boom)
    assert res["attempted"] == 0 and res["missing_before"] == 0


def test_limit_caps_lookups(tmp_path):
    csv = _write(tmp_path, [
        ["A", "2024", "1", "A Co"],
        ["B", "2024", "2", "B Co"],
        ["C", "2024", "3", "C Co"],
    ])
    calls = []

    def fake(ticker, cik):
        calls.append(ticker)
        return {"ticker": ticker, "company_name": ticker, "offer_date": "2024-01-01"}

    res = B.backfill(csv_path=csv, _fetcher=fake, limit=2)
    assert res["attempted"] == 2 and len(calls) == 2


def test_prefers_cik_when_present(tmp_path):
    csv = _write(tmp_path, [["RDDT", "2024", "1713445", "Reddit"]])
    seen = {}

    def fake(ticker, cik):
        seen["ticker"], seen["cik"] = ticker, cik
        return {"ticker": ticker, "company_name": "Reddit", "offer_date": "2024-03-20"}

    B.backfill(csv_path=csv, _fetcher=fake)
    assert seen == {"ticker": "RDDT", "cik": "1713445"}


def test_us_only_skips_rows_without_cik(tmp_path):
    csv = _write(tmp_path, [
        ["000100.KS", "2010", "", "Korean Co"],   # no CIK -> foreign -> skipped
        ["RDDT", "2024", "1713445", "Reddit"],
    ])
    looked_up = []

    def fake(ticker, cik):
        looked_up.append(ticker)
        return {"ticker": ticker, "company_name": ticker, "offer_date": "2024-03-20"}

    res = B.backfill(csv_path=csv, _fetcher=fake)   # us_only=True default
    assert looked_up == ["RDDT"]                    # foreign no-CIK row never attempted
    assert res["candidates"] == 1


def test_processes_most_recent_first(tmp_path):
    csv = _write(tmp_path, [
        ["OLD", "2001", "1", "Old Co"],
        ["NEW", "2025", "2", "New Co"],
        ["MID", "2018", "3", "Mid Co"],
    ])
    order = []

    def fake(ticker, cik):
        order.append(ticker)
        return None

    B.backfill(csv_path=csv, _fetcher=fake)
    assert order == ["NEW", "MID", "OLD"]           # Year Listed descending


def test_min_year_filters_old_rows(tmp_path):
    csv = _write(tmp_path, [
        ["OLD", "2019", "1", "Old Co"],
        ["NEW", "2024", "2", "New Co"],
    ])
    looked_up = []

    def fake(ticker, cik):
        looked_up.append(ticker)
        return None

    res = B.backfill(csv_path=csv, _fetcher=fake, min_year=2024)
    assert looked_up == ["NEW"] and res["candidates"] == 1


def test_include_foreign_attempts_no_cik_rows(tmp_path):
    csv = _write(tmp_path, [["FOO.L", "2024", "", "Foreign Co"]])
    looked_up = []

    def fake(ticker, cik):
        looked_up.append((ticker, cik))
        return None

    B.backfill(csv_path=csv, _fetcher=fake, us_only=False)
    assert looked_up == [("FOO.L", None)]           # attempted; cik None when blank


def test_stops_on_budget_exhaustion(tmp_path):
    from providers.renaissance_ipo import RenaissanceBudgetError

    csv = _write(tmp_path, [
        ["A", "2024", "1", "A Co"],
        ["B", "2024", "2", "B Co"],
    ])

    def fake(ticker, cik):
        raise RenaissanceBudgetError("cap reached")

    res = B.backfill(csv_path=csv, _fetcher=fake)
    assert res["budget_exhausted"] is True
    assert res["filled"] == 0 and res["attempted"] == 0


# ── inconclusive is counted separately from no_data ───────────────────────────
#
# Regression for 2026-07-28: six HTTP 429s were folded into "no IPO on record",
# so a throttled run reported as a complete one.

from providers import renaissance_ipo as R  # noqa: E402


def test_inconclusive_is_not_counted_as_no_data(tmp_path):
    csv = _write(tmp_path, [
        ["AAA", "2026", "1", "Filled Co"],
        ["BBB", "2026", "2", "Genuinely No IPO Co"],
        ["CCC", "2026", "3", "Throttled Co"],
    ])

    def fake(ticker, cik):
        if ticker == "AAA":
            return R.STATUS_OK, {"ticker": "AAA", "company_name": "Filled Co",
                                 "offer_date": "2026-03-20"}
        if ticker == "BBB":
            return R.STATUS_NO_DATA, None
        return R.STATUS_INCONCLUSIVE, None

    res = B.backfill(csv_path=csv, _fetcher=fake)
    assert res["filled"] == 1
    assert res["no_data"] == 1
    assert res["inconclusive"] == 1
    assert res["attempted"] == 3


def test_throttled_row_is_left_blank_for_the_next_run(tmp_path):
    csv = _write(tmp_path, [["CCC", "2026", "3", "Throttled Co"]])
    res = B.backfill(csv_path=csv,
                     _fetcher=lambda t, c: (R.STATUS_INCONCLUSIVE, None))
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    assert df.loc[0, "IPO Date"] == ""
    assert res["still_missing"] == 1
    assert res["no_data"] == 0


def test_legacy_bare_payload_fetcher_still_works(tmp_path):
    """Injected fetchers predating the (status, payload) pair must keep working."""
    csv = _write(tmp_path, [
        ["AAA", "2026", "1", "Filled Co"],
        ["BBB", "2026", "2", "No IPO Co"],
    ])

    def legacy(ticker, cik):
        if ticker == "AAA":
            return {"ticker": "AAA", "company_name": "Filled Co",
                    "offer_date": "2026-03-20"}
        return None

    res = B.backfill(csv_path=csv, _fetcher=legacy)
    assert res["filled"] == 1
    assert res["no_data"] == 1
    assert res["inconclusive"] == 0
