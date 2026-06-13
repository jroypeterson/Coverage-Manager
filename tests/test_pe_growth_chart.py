"""Tests for the P/E vs forward-2yr-EPS-growth scatter: the pure growth helper
and the (side-effect-only) chart renderer."""

from datetime import date

import pytest

from reporting.calcs import forward_2yr_eps_growth_pct
from reporting import charts


def _rec(d, eps):
    return {"date": d, "epsAvg": eps}


class TestForward2yrEpsGrowth:
    def test_two_year_cagr_from_current_fy(self):
        # FY0=2026 (10.0), FY+2=2028 (14.4) → (14.4/10)^0.5 - 1 = 0.2 = 20%/yr.
        recs = [
            _rec("2028-12-31", 14.4),
            _rec("2027-12-31", 12.0),
            _rec("2026-12-31", 10.0),
            _rec("2025-12-31", 8.0),  # historical — ignored
        ]
        g = forward_2yr_eps_growth_pct(recs, date(2026, 6, 13))
        assert g == pytest.approx(20.0, rel=1e-6)

    def test_off_calendar_fiscal_year(self):
        # FYE May. Today 2026-06-13 → first forward FY-end is 2027-05-31.
        recs = [
            _rec("2027-05-31", 10.0),
            _rec("2028-05-31", 11.0),
            _rec("2029-05-31", 12.1),  # FY+2 → (12.1/10)^0.5-1 = 10%/yr
        ]
        g = forward_2yr_eps_growth_pct(recs, date(2026, 6, 13))
        assert g == pytest.approx(10.0, rel=1e-6)

    def test_none_when_fewer_than_three_forward_years(self):
        recs = [_rec("2026-12-31", 10.0), _rec("2027-12-31", 12.0)]
        assert forward_2yr_eps_growth_pct(recs, date(2026, 6, 13)) is None

    def test_none_when_eps_non_positive(self):
        recs = [
            _rec("2026-12-31", -1.0),  # loss-making FY0 → CAGR undefined
            _rec("2027-12-31", 0.5),
            _rec("2028-12-31", 1.0),
        ]
        assert forward_2yr_eps_growth_pct(recs, date(2026, 6, 13)) is None

    def test_ignores_unparseable_or_missing(self):
        recs = [
            _rec("not-a-date", 10.0),
            _rec("2026-12-31", None),
            _rec("2026-12-31", 10.0),
            _rec("2027-12-31", 11.0),
            _rec("2028-12-31", 12.1),
        ]
        g = forward_2yr_eps_growth_pct(recs, date(2026, 6, 13))
        assert g == pytest.approx(10.0, rel=1e-6)

    def test_empty_records(self):
        assert forward_2yr_eps_growth_pct([], date(2026, 6, 13)) is None
        assert forward_2yr_eps_growth_pct(None, date(2026, 6, 13)) is None


class TestRenderScatter:
    def test_writes_png_and_counts_points(self, tmp_path):
        out = tmp_path / "scatter.png"
        rows = [
            {"ticker": "ISRG", "pe": 65.0, "growth": 14.0, "sector": "MedTech", "mkt_cap": 200e9},
            {"ticker": "UNH", "pe": 18.0, "growth": 9.0, "sector": "Healthcare Services", "mkt_cap": 450e9},
            {"ticker": "X", "pe": None, "growth": 5.0, "sector": "Tech", "mkt_cap": 1e9},  # dropped
        ]
        n = charts.render_pe_growth_scatter(rows, str(out))
        assert n == 2
        assert out.exists() and out.stat().st_size > 0

    def test_no_points_writes_nothing(self, tmp_path):
        out = tmp_path / "empty.png"
        n = charts.render_pe_growth_scatter(
            [{"ticker": "X", "pe": None, "growth": None}], str(out))
        assert n == 0
        assert not out.exists()
