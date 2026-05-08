"""Tests for reporting/history_stats.py — pure stats helpers."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reporting.history_stats import stats_from_series


class TestStatsFromSeries:
    def test_full_series(self):
        s = stats_from_series([10.0, 12.0, 14.0, 16.0, 18.0], current=20.0)
        assert s["avg"] == 14.0
        assert s["min"] == 10.0
        assert s["max"] == 18.0
        # sample stdev = sqrt(40/4) = sqrt(10) ≈ 3.162
        assert math.isclose(s["plus_1sd"], 14.0 + math.sqrt(10), rel_tol=1e-6)
        assert math.isclose(s["minus_1sd"], 14.0 - math.sqrt(10), rel_tol=1e-6)
        # vs_avg_pct = (20 - 14) / 14 * 100 ≈ 42.857
        assert math.isclose(s["vs_avg_pct"], (20.0 - 14.0) / 14.0 * 100.0, rel_tol=1e-6)

    def test_drops_none_and_nan(self):
        s = stats_from_series([10.0, None, float("nan"), 14.0, 18.0], current=15.0)
        assert s["avg"] == 14.0
        assert s["min"] == 10.0
        assert s["max"] == 18.0

    def test_drops_non_numeric_strings(self):
        s = stats_from_series([10.0, "bad", 14.0, "", None], current=12.0)
        assert s["avg"] == 12.0
        assert s["min"] == 10.0
        assert s["max"] == 14.0

    def test_single_value_no_stdev(self):
        s = stats_from_series([15.0], current=20.0)
        assert s["avg"] == 15.0
        assert s["min"] == 15.0
        assert s["max"] == 15.0
        # stdev requires n>=2
        assert s["plus_1sd"] is None
        assert s["minus_1sd"] is None
        # vs_avg_pct still computable
        assert math.isclose(s["vs_avg_pct"], (20.0 - 15.0) / 15.0 * 100.0, rel_tol=1e-6)

    def test_empty_series(self):
        s = stats_from_series([], current=10.0)
        assert s["avg"] is None
        assert s["min"] is None
        assert s["max"] is None
        assert s["plus_1sd"] is None
        assert s["minus_1sd"] is None
        assert s["vs_avg_pct"] is None

    def test_all_none_series(self):
        s = stats_from_series([None, None, None], current=10.0)
        assert s["avg"] is None
        assert s["vs_avg_pct"] is None

    def test_no_current(self):
        s = stats_from_series([10.0, 20.0, 30.0])
        assert s["avg"] == 20.0
        assert s["vs_avg_pct"] is None

    def test_current_below_avg_returns_negative_pct(self):
        s = stats_from_series([10.0, 20.0, 30.0], current=10.0)
        # vs_avg_pct = (10 - 20) / 20 * 100 = -50
        assert math.isclose(s["vs_avg_pct"], -50.0)

    def test_zero_avg_returns_none_vs(self):
        s = stats_from_series([-1.0, 0.0, 1.0], current=5.0)
        # avg is 0 → vs_avg_pct undefined (would be div by zero)
        assert s["avg"] == 0.0
        assert s["vs_avg_pct"] is None
