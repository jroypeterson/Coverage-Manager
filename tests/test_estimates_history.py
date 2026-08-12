"""Point-in-time estimates archive.

The whole value of this file is that its points are REAL and DATED. Every test
here defends one of those two properties.
"""

import json

import pytest

from providers import estimates_history as EH

ROWS = [
    {"date": "2027-09-27", "epsAvg": 9.59},
    {"date": "2026-09-27", "epsAvg": 8.80},
]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(EH, "HISTORY_DIR", tmp_path / "estimates_history")


def test_an_observation_is_appended_with_its_date():
    assert EH.record_observation("AAPL", ROWS, observed="2026-08-12") is True
    obs = EH.load_observations("AAPL")
    assert len(obs) == 1
    assert obs[0]["observed"] == "2026-08-12"
    assert obs[0]["ticker"] == "AAPL"
    assert obs[0]["rows"] == ROWS
    assert obs[0]["schema_version"] == EH.SCHEMA_VERSION


def test_a_second_call_the_same_day_is_a_no_op():
    """Re-running the weekly build must not stack duplicates onto one date.

    Two observations sharing a date would silently double-weight that day in any
    series built from the file later.
    """
    assert EH.record_observation("AAPL", ROWS, observed="2026-08-12") is True
    assert EH.record_observation("AAPL", ROWS, observed="2026-08-12") is False
    assert len(EH.load_observations("AAPL")) == 1


def test_successive_dates_accumulate_oldest_first():
    for d in ("2026-08-12", "2026-08-19", "2026-08-05"):
        EH.record_observation("AAPL", ROWS, observed=d)
    assert [o["observed"] for o in EH.load_observations("AAPL")] == [
        "2026-08-05", "2026-08-12", "2026-08-19"]


def test_an_empty_or_all_null_curve_is_NOT_recorded():
    """"The vendor had nothing" is a fact about the vendor, not an observation.

    Writing it would put a hole in a series whose entire premise is that every
    point is a real reading.
    """
    assert EH.record_observation("NADA", []) is False
    assert EH.record_observation("NADA", [{"date": "2027-01-01", "epsAvg": None}]) is False
    assert EH.load_observations("NADA") == []


def test_a_corrupt_line_does_not_destroy_the_series():
    """One bad append (a kill mid-write) must cost one observation, not all of them."""
    EH.record_observation("AAPL", ROWS, observed="2026-08-12")
    path = EH._path("AAPL")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    EH.record_observation("AAPL", ROWS, observed="2026-08-19")

    obs = EH.load_observations("AAPL")
    assert [o["observed"] for o in obs] == ["2026-08-12", "2026-08-19"]


def test_archiving_never_raises_into_the_caller():
    """An archive is a side effect of fetching. It must never break the fetch."""
    assert EH.record_observation(None, ROWS) is False
    assert EH.record_observation("AAPL", None) is False
    assert EH.record_observation("AAPL", "not-a-list") is False


def test_a_slash_in_a_ticker_cannot_escape_the_archive_directory():
    EH.record_observation("../../etc/passwd", ROWS, observed="2026-08-12")
    written = list(EH.HISTORY_DIR.glob("*.jsonl"))
    assert len(written) == 1
    assert written[0].parent == EH.HISTORY_DIR


def test_the_fetch_path_records_an_observation(monkeypatch, tmp_path):
    """The archive is wired into fetch_estimates, not merely importable."""
    from providers import fmp_estimates as FE

    monkeypatch.setattr(FE, "_fmp_request", lambda url: [
        {"date": "2027-09-27", "epsAvg": 9.59, "extra": "ignored"},
    ])
    monkeypatch.setattr(FE, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(FE, "cache_set", lambda *a, **k: None)

    rows = FE.fetch_estimates("AAPL", api_key="x", use_cache=False)
    assert rows == [{"date": "2027-09-27", "epsAvg": 9.59}]

    obs = EH.load_observations("AAPL")
    assert len(obs) == 1
    assert obs[0]["rows"] == [{"date": "2027-09-27", "epsAvg": 9.59}]


def test_the_archive_is_valid_jsonl():
    """One JSON object per line -- readable by anything, including a shell one-liner."""
    EH.record_observation("AAPL", ROWS, observed="2026-08-12")
    EH.record_observation("AAPL", ROWS, observed="2026-08-19")
    text = EH._path("AAPL").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        assert isinstance(json.loads(ln), dict)
