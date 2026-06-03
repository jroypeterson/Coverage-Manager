"""Tests for the reporting-calendar enrichment.

OFFLINE ONLY — no live SEC/Finnhub/API-Ninjas calls. The `build_record` /
`anchor_count` / `dedupe_sec_quarter_labels` pure functions are exercised against
**frozen fixtures** captured under tests/fixtures/reporting_calendar/ (raw provider
pulls). The live `experiments/a0_*` scripts remain manual diagnostics.
"""

import datetime
import json
from pathlib import Path

import pytest

import weekly_universe
from universe import reporting_calendar as rc

TODAY = datetime.date(2026, 6, 2)
FIXDIR = Path(__file__).parent / "fixtures" / "reporting_calendar"


def _fixture(tk):
    return json.loads((FIXDIR / f"{tk}.json").read_text(encoding="utf-8"))


def _record(tk):
    raw = _fixture(tk)
    return rc.build_record(
        tk, sec_labels=raw["sec_labels"], finnhub_events=raw["finnhub_events"],
        ninjas_rows=raw["ninjas_rows"], today=TODAY,
    )


# ── pure logic ───────────────────────────────────────────────────────────────
def test_anchor_count_forward_back_and_year_rollover():
    dates = ["2025-07-29", "2025-10-29", "2026-01-28", "2026-04-30"]
    anchor = {"date": "2026-01-28", "year": 2026, "quarter": 1}
    out = rc.anchor_count(dates, anchor)
    assert out["2026-01-28"] == [2026, 1]
    assert out["2025-10-29"] == [2025, 4]   # back across the Q1->Q4 year boundary
    assert out["2025-07-29"] == [2025, 3]
    assert out["2026-04-30"] == [2026, 2]   # forward


def test_anchor_count_breaks_on_large_gap():
    dates = ["2024-01-01", "2026-04-30", "2026-07-30"]  # huge gap before the anchor
    anchor = {"date": "2026-04-30", "year": 2026, "quarter": 2}
    out = rc.anchor_count(dates, anchor)
    assert "2024-01-01" not in out          # gap > 140d breaks the count
    assert out["2026-07-30"] == [2026, 3]


def test_anchor_count_no_anchor_returns_empty():
    assert rc.anchor_count(["2026-04-30"], None) == {}


def test_dedupe_sec_keeps_quarters_drops_comparatives_and_annual():
    units = {"USD/shares": [
        # original Q1 10-Q (~91d) filed first
        {"start": "2026-01-01", "end": "2026-03-31", "fy": 2026, "fp": "Q1",
         "form": "10-Q", "filed": "2026-04-20"},
        # same period re-reported as a comparative in a LATER filing (wrong fy) — must be dropped
        {"start": "2026-01-01", "end": "2026-03-31", "fy": 2027, "fp": "Q1",
         "form": "10-Q", "filed": "2027-04-20"},
        # annual / YTD duration (~365d) — must be dropped
        {"start": "2025-01-01", "end": "2025-12-31", "fy": 2025, "fp": "FY",
         "form": "10-K", "filed": "2026-02-01"},
    ]}
    out = rc.dedupe_sec_quarter_labels(units)
    assert out == {"2026-03-31": [2026, 1]}   # earliest-filed quarter only


def test_fye_month_handles_52_53_week_spill():
    # AAPL Q1 ends late Dec -> Sep FYE
    assert rc._fye_month_from("2025-12-27", 1) == 9
    # KO Q1 ends 2026-04-03 (April spill) -> snaps to Mar -> Dec FYE (not Jan)
    assert rc._fye_month_from("2026-04-03", 1) == 12


# ── build_record gating contract (synthetic) ─────────────────────────────────
def _ninjas(*pairs):
    return [{"date": d, "eps_actual": e} for d, e in pairs]


def test_us_filer_gates_true_when_sec_and_finnhub_agree():
    rec = rc.build_record(
        "AAA", sec_labels={"2026-03-31": [2026, 1]},
        finnhub_events=[{"date": "2026-04-28", "year": 2026, "quarter": 1}],
        ninjas_rows=_ninjas(("2026-04-28", 1.23)), today=TODAY)
    q = rec["recent_quarters"][0]
    assert q["sec_finnhub_agree"] and q["gating_eligible"]
    assert q["label_sources"] == ["sec_xbrl", "finnhub"]


def test_us_filer_gates_false_when_sec_and_finnhub_disagree():
    rec = rc.build_record(
        "AAA", sec_labels={"2026-03-31": [2026, 1]},
        finnhub_events=[{"date": "2026-04-28", "year": 2026, "quarter": 2}],  # disagree
        ninjas_rows=_ninjas(("2026-04-28", 1.23)), today=TODAY)
    q = rec["recent_quarters"][0]
    assert not q["sec_finnhub_agree"] and not q["gating_eligible"]


def test_gates_false_when_eps_actual_missing_scheduled_not_reported():
    rec = rc.build_record(
        "AAA", sec_labels={"2026-03-31": [2026, 1]},
        finnhub_events=[{"date": "2026-04-28", "year": 2026, "quarter": 1}],
        ninjas_rows=_ninjas(("2026-04-28", None)), today=TODAY)
    assert not rec["recent_quarters"][0]["gating_eligible"]


def test_foreign_filer_all_rows_gate_false():
    rec = rc.build_record(
        "FOR", sec_labels={},   # no us-gaap facts
        finnhub_events=[{"date": "2026-05-06", "year": 2026, "quarter": 1}],
        ninjas_rows=_ninjas(("2026-05-06", 2.0)), today=TODAY)
    assert rec["filer_type"] == "non_us_or_unknown"
    assert all(not q["gating_eligible"] for q in rec["recent_quarters"])
    if rec["next_expected"]:
        assert not rec["next_expected"]["gating_eligible"]


# ── build_record against frozen real fixtures ────────────────────────────────
def test_aapl_fixture_us_filer_sep_fye_gates():
    rec = _record("AAPL")
    assert rec["filer_type"] == "us_gaap"
    assert rec["fye_month"] == 9 and rec["calendar_aligned"] is False
    assert rec["monotonic"]
    gated = [q for q in rec["recent_quarters"] if q["gating_eligible"]]
    assert gated and all(q["sec_finnhub_agree"] for q in gated)
    assert rec["next_expected"]["gating_eligible"] is True   # finnhub-trusted + fresh


def test_ko_fixture_dec_fye_despite_april_quarter_spill():
    rec = _record("KO")
    assert rec["fye_month"] == 12 and rec["calendar_aligned"] is True


def test_nvo_fixture_foreign_never_gates():
    rec = _record("NVO")
    assert rec["filer_type"] == "non_us_or_unknown"
    assert all(not q["gating_eligible"] for q in rec["recent_quarters"])
    assert rec["next_expected"]["gating_eligible"] is False


def test_insm_clinical_microcap_works():
    rec = _record("INSM")
    assert rec["filer_type"] == "us_gaap" and rec["fye_month"] == 12
    assert any(q["gating_eligible"] for q in rec["recent_quarters"])


# ── export step + manifest registration ──────────────────────────────────────
def test_export_step_writes_both_files(monkeypatch, tmp_path):
    exports_dir = tmp_path / "exports"
    exports_dir.mkdir()
    # Stub the universe-resolution inputs the step reads.
    (exports_dir / "universe_metadata.json").write_text(
        json.dumps({"AAPL": {"core": "Y"}, "ZZZZ": {"core": ""}}), encoding="utf-8")
    (exports_dir / "portfolio.json").write_text(json.dumps({"KO": {}}), encoding="utf-8")
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    captured = {}

    def fake_build(tickers, **kw):
        captured["tickers"] = list(tickers)
        return ({tk: {"filer_type": "us_gaap", "recent_quarters": []} for tk in tickers},
                {"ticker_count": len(tickers), "us_filer_count": len(tickers),
                 "non_us_or_uncovered_count": 0, "gating_eligible_count": 1,
                 "generated_for": "2026-06-02"})

    monkeypatch.setattr("universe.reporting_calendar.build_reporting_calendar", fake_build)

    result = weekly_universe._step_export_reporting_calendar()

    assert (exports_dir / "reporting_calendar.json").exists()
    status = json.loads((exports_dir / "reporting_calendar_status.json").read_text())
    assert status["schema_version"] == weekly_universe.REPORTING_CALENDAR_SCHEMA_VERSION
    assert status["universe"] == "positions_union_core"
    assert sorted(captured["tickers"]) == ["AAPL", "KO"]   # Positions ∪ Core
    assert len(result["artifacts"]) == 2


def test_manifest_lists_reporting_calendar_files(monkeypatch, tmp_path):
    import csv as _csv
    csv_path = tmp_path / "u.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=["Ticker", "Company Name", "Sector (JP)", "Core"])
        w.writeheader()
        w.writerow({"Ticker": "AAPL", "Company Name": "Apple", "Sector (JP)": "Tech", "Core": "Y"})
    exports_dir = tmp_path / "exports"
    monkeypatch.setattr(weekly_universe, "CSV_PATH", csv_path)
    monkeypatch.setattr(weekly_universe, "EXPORTS_DIR", exports_dir)

    result = weekly_universe._step_export_artifacts(
        {"rows": 1, "errors": [], "warnings": [], "passed": True})

    manifest = json.loads((exports_dir / "manifest.json").read_text())
    names = {f["name"] for f in manifest["files"]}
    assert "reporting_calendar.json" in names
    assert "reporting_calendar_status.json" in names
    # The universe-artifacts step still advertises exactly its own four files.
    assert len(result["artifacts"]) == 4
