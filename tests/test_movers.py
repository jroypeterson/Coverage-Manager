"""Tests for the movers report — threshold + z-score logic and rendering."""

import pandas as pd
import pytest

from reporting import movers


def _make_df(rows):
    """Build a DataFrame matching the perf-snapshot schema with the columns
    movers actually reads (Ticker, Company Name, Sector (JP), Subsector (JP),
    1W). All other perf columns are unused by movers."""
    return pd.DataFrame(rows)


# ── compute_flags ──────────────────────────────────────────────────────────


def test_empty_df_returns_empty():
    df = pd.DataFrame()
    out = movers.compute_flags(df, abs_threshold_pct=10.0, z_threshold=2.0, min_peer_count=5)
    assert out.empty


def test_no_1w_column_returns_empty():
    df = pd.DataFrame([{"Ticker": "A", "Sector (JP)": "Tech"}])
    out = movers.compute_flags(df, abs_threshold_pct=10.0, z_threshold=2.0, min_peer_count=5)
    assert out.empty


def test_abs_threshold_flags_extreme_movers():
    df = _make_df([
        {"Ticker": "BIG_UP",   "Company Name": "Big Up",   "Sector (JP)": "Tech",  "1W": 25.0},
        {"Ticker": "BIG_DOWN", "Company Name": "Big Down", "Sector (JP)": "Tech",  "1W": -20.0},
        {"Ticker": "SMALL",    "Company Name": "Small",    "Sector (JP)": "Tech",  "1W": 2.5},
        {"Ticker": "ZERO",     "Company Name": "Zero",     "Sector (JP)": "Tech",  "1W": 0.0},
    ])
    out = movers.compute_flags(df, abs_threshold_pct=10.0, z_threshold=2.0, min_peer_count=5)
    tickers = set(out["Ticker"].tolist())
    assert tickers == {"BIG_UP", "BIG_DOWN"}
    # Sorted by |move| descending
    assert out.iloc[0]["Ticker"] == "BIG_UP"


def test_z_score_flags_relative_outliers_when_cohort_large_enough():
    # Sector cohort with 6 members; one is a clear outlier vs the others.
    rows = [
        {"Ticker": f"T{i}", "Company Name": f"C{i}", "Sector (JP)": "Tech", "1W": 1.0 + i * 0.1}
        for i in range(5)
    ]
    rows.append({"Ticker": "OUTLIER", "Company Name": "Outlier", "Sector (JP)": "Tech", "1W": 8.0})
    df = _make_df(rows)
    # abs threshold high so only z can fire
    out = movers.compute_flags(df, abs_threshold_pct=50.0, z_threshold=1.5, min_peer_count=5)
    assert "OUTLIER" in out["Ticker"].tolist()
    assert out[out["Ticker"] == "OUTLIER"]["_z_flag"].iloc[0]


def test_small_cohort_skips_z_score():
    # Only 2 members in the sector — z-score should be skipped, only abs fires.
    df = _make_df([
        {"Ticker": "A", "Company Name": "A", "Sector (JP)": "Niche", "1W": 5.0},
        {"Ticker": "B", "Company Name": "B", "Sector (JP)": "Niche", "1W": -3.0},
    ])
    out = movers.compute_flags(df, abs_threshold_pct=4.0, z_threshold=1.0, min_peer_count=5)
    # A passes abs (5 >= 4); B does not (3 < 4). Neither triggers z (cohort too small).
    assert "A" in out["Ticker"].tolist()
    assert "B" not in out["Ticker"].tolist()
    # And z_score is NaN for everyone
    assert out["_z_score"].isna().all()


def test_neither_threshold_means_no_flags():
    df = _make_df([
        {"Ticker": f"T{i}", "Company Name": f"C{i}", "Sector (JP)": "Tech", "1W": 1.0 + i * 0.1}
        for i in range(8)
    ])
    out = movers.compute_flags(df, abs_threshold_pct=10.0, z_threshold=2.0, min_peer_count=5)
    assert out.empty


def test_either_threshold_triggers_flag():
    rows = [
        {"Ticker": f"PEER{i}", "Company Name": f"P{i}", "Sector (JP)": "Tech", "1W": 1.0 + i * 0.1}
        for i in range(6)
    ]
    rows.append({"Ticker": "ABS_ONLY", "Company Name": "Abs Only", "Sector (JP)": "Lonely", "1W": 12.0})
    rows.append({"Ticker": "Z_ONLY",   "Company Name": "Z Only",   "Sector (JP)": "Tech",   "1W": 3.5})
    df = _make_df(rows)
    out = movers.compute_flags(df, abs_threshold_pct=10.0, z_threshold=1.5, min_peer_count=5)
    flagged = set(out["Ticker"].tolist())
    assert "ABS_ONLY" in flagged   # passes abs (12 >= 10), small-cohort skips z
    assert "Z_ONLY" in flagged     # passes z (~3+ stdev above peers)


def test_missing_1w_rows_are_excluded():
    df = _make_df([
        {"Ticker": "A", "Company Name": "A", "Sector (JP)": "Tech", "1W": 12.0},
        {"Ticker": "B", "Company Name": "B", "Sector (JP)": "Tech", "1W": None},
    ])
    out = movers.compute_flags(df, abs_threshold_pct=10.0, z_threshold=2.0, min_peer_count=5)
    assert "A" in out["Ticker"].tolist()
    assert "B" not in out["Ticker"].tolist()


# ── cap_flagged ────────────────────────────────────────────────────────────


def test_cap_flagged_takes_top_n_by_abs_move():
    rows = [
        {"Ticker": f"T{i}", "Company Name": f"C{i}", "Sector (JP)": "Tech", "1W": 11.0 + i}
        for i in range(10)
    ]
    df = _make_df(rows)
    flagged = movers.compute_flags(df, abs_threshold_pct=10.0, z_threshold=999.0, min_peer_count=5)
    capped = movers.cap_flagged(flagged, max_flagged=3)
    assert len(capped) == 3
    # compute_flags returns sorted desc by |1W|, so cap keeps the top 3
    assert capped["Ticker"].tolist() == ["T9", "T8", "T7"]


# ── render_html / render_markdown / format_slack_summary ──────────────────


def test_render_empty_messages():
    empty = pd.DataFrame()
    html = movers.render_html(empty, today="2026-05-02", abs_threshold=10.0, z_threshold=2.0)
    assert "No tickers crossed the threshold" in html
    md = movers.render_markdown(empty, today="2026-05-02", abs_threshold=10.0, z_threshold=2.0)
    assert "No tickers crossed the threshold" in md
    slack = movers.format_slack_summary(empty, today="2026-05-02", abs_threshold=10.0, z_threshold=2.0)
    assert "Quiet week" in slack


def test_render_includes_ticker_and_move():
    df = _make_df([
        {"Ticker": "AAPL", "Company Name": "Apple Inc.", "Sector (JP)": "Other", "Subsector (JP)": "", "1W": 12.5},
    ])
    flagged = movers.compute_flags(df, abs_threshold_pct=10.0, z_threshold=2.0, min_peer_count=5)
    flagged["_news"] = [[]]
    flagged["_why"] = [""]
    html = movers.render_html(flagged, today="2026-05-02", abs_threshold=10.0, z_threshold=2.0)
    assert "AAPL" in html
    assert "+12.5%" in html
    md = movers.render_markdown(flagged, today="2026-05-02", abs_threshold=10.0, z_threshold=2.0)
    assert "AAPL" in md
    assert "+12.5%" in md
    slack = movers.format_slack_summary(flagged, today="2026-05-02", abs_threshold=10.0, z_threshold=2.0)
    assert "AAPL" in slack


def test_render_markdown_escapes_pipes():
    df = _make_df([
        {"Ticker": "X", "Company Name": "Has | pipe", "Sector (JP)": "Tech", "Subsector (JP)": "", "1W": 15.0},
    ])
    flagged = movers.compute_flags(df, abs_threshold_pct=10.0, z_threshold=2.0, min_peer_count=5)
    flagged["_news"] = [[]]
    flagged["_why"] = ["why with | pipe"]
    md = movers.render_markdown(flagged, today="2026-05-02", abs_threshold=10.0, z_threshold=2.0)
    # Pipe in cell content escaped
    assert "Has \\| pipe" in md
    assert "why with \\| pipe" in md
