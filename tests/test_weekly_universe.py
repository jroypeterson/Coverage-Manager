"""Smoke tests for the weekly_universe orchestrator."""

import csv
from pathlib import Path

import pytest

import weekly_universe


@pytest.fixture
def fixture_csv(tmp_path):
    """Write a small valid coverage CSV and return its path."""
    csv_path = tmp_path / "coverage_universe_tickers.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Ticker": "AAPL",
                "Exchange": "NASDAQ",
                "Company Name": "Apple Inc",
                "Sector (JP)": "Tech",
                "Subsector (JP)": "Hardware",
            }
        )
        writer.writerow(
            {
                "Ticker": "MRNA",
                "Exchange": "NASDAQ",
                "Company Name": "Moderna Inc",
                "Sector (JP)": "Biopharma",
                "Subsector (JP)": "Biotech",
            }
        )
    return csv_path


def test_main_dry_run_skip_discovery_returns_standardized_shape(monkeypatch, fixture_csv):
    """A dry-run universe call should return the standardized result dict shape
    with validation_passed=True for a clean CSV."""
    monkeypatch.setattr(weekly_universe, "CSV_PATH", fixture_csv)

    result = weekly_universe.main(skip_discovery=True, dry_run=True, log_audit=False)

    # Standardized shape
    assert result["command"] == "weekly-universe"
    assert "date" in result
    assert "validation_passed" in result
    assert "steps" in result
    assert "artifacts" in result
    assert "non_successes" in result

    # Clean fixture should pass validation
    assert result["validation_passed"] is True

    # All steps appear in the steps dict
    assert set(result["steps"].keys()) == {
        "validate",
        "archive",
        "discovery",
        "delisted_check",
        "cik_backfill",
        "ticker_change_check",
        "crosscheck_foreign",
        "verify_isin_issuers",   # [4d/6], wired 2026-07-29
        "resolve_cik_by_name",   # [4e/6], wired 2026-07-30
        "export_artifacts",
        "export_watchlist",
        "export_reporting_calendar",
        "sigma_export",
        "universe_delta_slack",
    }
    assert result["steps"]["validate"] == "ok"
    assert result["steps"]["discovery"] == "skipped"
    # Dry run skips mutation steps
    assert "skipped" in result["steps"]["archive"]
    assert "skipped" in result["steps"]["delisted_check"]
    assert "skipped" in result["steps"]["ticker_change_check"]
    assert "skipped" in result["steps"]["crosscheck_foreign"]
    assert "skipped" in result["steps"]["export_artifacts"]
    assert "skipped" in result["steps"]["export_watchlist"]
    assert "skipped" in result["steps"]["sigma_export"]
    assert "skipped" in result["steps"]["universe_delta_slack"]

    assert result["non_successes"] == []


def test_main_validation_failure_sets_validation_passed_false(monkeypatch, tmp_path):
    """A CSV with duplicate tickers should fail validation and set
    validation_passed=False, but the orchestrator should still return cleanly."""
    bad_csv = tmp_path / "bad.csv"
    with open(bad_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"],
        )
        writer.writeheader()
        # Duplicate ticker — hard validation error
        writer.writerow({"Ticker": "AAPL", "Exchange": "NASDAQ", "Company Name": "A", "Sector (JP)": "Tech", "Subsector (JP)": ""})
        writer.writerow({"Ticker": "AAPL", "Exchange": "NASDAQ", "Company Name": "B", "Sector (JP)": "Tech", "Subsector (JP)": ""})

    monkeypatch.setattr(weekly_universe, "CSV_PATH", bad_csv)

    result = weekly_universe.main(skip_discovery=True, dry_run=True, log_audit=False)

    assert result["validation_passed"] is False
    # validate step itself completed (it's the rules that failed, not the step)
    assert result["steps"]["validate"] == "ok"


# --- degraded/failed steps must reach the health heartbeat ------------------
#
# `collect_non_successes` recognises ONLY the "failed:"/"blocked:" prefixes, so
# a step that reports trouble in prose posts a green heartbeat. That is how a
# delisted check which resolved half the universe, and a CIK backfill that never
# reached SEC, both reported :white_check_mark: to #status-reports.


def test_degraded_delisted_check_is_a_non_success():
    from pipeline_utils import collect_non_successes

    steps = {"delisted_check": "failed: degraded - 0 flagged of 1093",
             "export_artifacts": "ok"}
    assert collect_non_successes(steps) == ["delisted_check"]


def test_unreachable_sec_map_is_a_non_success():
    from pipeline_utils import collect_non_successes

    steps = {"cik_backfill": "failed: SEC map unavailable - no rows changed",
             "export_artifacts": "ok"}
    assert collect_non_successes(steps) == ["cik_backfill"]


def test_an_ordinary_run_stays_a_success():
    """The alarm must not be permanently lit -- flags and inconclusive rows are
    the check's normal output, not a failure of the run."""
    from pipeline_utils import collect_non_successes

    steps = {"delisted_check": "3 flagged of 1093 (11 inconclusive, missing data: 4)",
             "cik_backfill": "filled 0 blank CIK(s); 241 blank before this step",
             "export_artifacts": "ok"}
    assert collect_non_successes(steps) == []


# --- crosscheck-foreign as a weekly step (Fable, 2026-07-28) -----------------
#
# The seven wrong ISINs survived four months because every identity check was
# run-on-demand. The step is NON-GATING: a conflict must not fail the build or
# block exports, but it must reach the health heartbeat as `partial` — which
# `collect_non_successes` only does for a "failed:"-prefixed status. Counts are
# reported as COUNTED CLASSES, never a boolean: "4 listing-mismatch,
# 0 isin-conflict" is actionable; "conflicts: yes" is not.


def _cf_result(by_kind=None, conflicts=0, ok=True, matched=72, checked=350, notes=7):
    return {"status": "conflicts" if conflicts else ("ok" if ok else "failed"),
            "ok": ok, "checked": checked, "matched": matched,
            "conflicts": conflicts, "by_kind": by_kind or {},
            "incorporation_notes": notes, "report": "reports/x.md"}


def test_step_crosscheck_foreign_returns_counted_classes(monkeypatch, tmp_path):
    from universe import foreign_crosscheck as fc

    r = fc.CrosscheckResult(status="conflicts", checked=350, matched=72)
    for t in ("AZN", "FER", "MDA", "2359.HK"):
        r.conflicts.append(fc.Finding(
            kind="listing-mismatch", ticker=t, company="c", field="Currency",
            universe_value="USD", source_value="GBP", source="s"))
    r.incorporation_notes.append(fc.Finding(
        kind="incorporation", ticker="1801.HK", company="Innovent",
        field="Country", universe_value="CN", source_value="KY", source="s"))
    monkeypatch.setattr(fc, "main", lambda **k: r)
    monkeypatch.setattr(fc, "write_report", lambda res: tmp_path / "x.md")

    out = weekly_universe._step_crosscheck_foreign()
    assert out["conflicts"] == 4
    assert out["by_kind"] == {"listing-mismatch": 4}
    assert out["incorporation_notes"] == 1
    assert out["matched"] == 72 and out["checked"] == 350


def test_crosscheck_status_with_conflicts_is_failed_and_counts_every_class():
    status = weekly_universe._crosscheck_step_status(
        _cf_result(by_kind={"listing-mismatch": 4}, conflicts=4))
    assert status.startswith("failed:")
    assert "4 listing-mismatch" in status
    assert "0 isin-conflict" in status
    assert "0 lei-conflict" in status
    assert "0 name-divergence" in status
    assert "7 incorporation note(s)" in status
    assert status.isascii()


def test_crosscheck_conflicts_reach_the_heartbeat_as_partial():
    from pipeline_utils import collect_non_successes

    steps = {"crosscheck_foreign": weekly_universe._crosscheck_step_status(
        _cf_result(by_kind={"listing-mismatch": 4}, conflicts=4)),
        "export_artifacts": "ok"}
    assert collect_non_successes(steps) == ["crosscheck_foreign"]


def test_crosscheck_clean_run_is_a_success_with_counts_still_visible():
    from pipeline_utils import collect_non_successes

    status = weekly_universe._crosscheck_step_status(_cf_result())
    assert not status.startswith("failed:")
    assert "0 isin-conflict" in status
    assert collect_non_successes({"crosscheck_foreign": status}) == []


def test_crosscheck_every_source_failed_is_a_non_success_not_silence():
    """A run that learned nothing must not report agreement."""
    from pipeline_utils import collect_non_successes

    status = weekly_universe._crosscheck_step_status(_cf_result(ok=False))
    assert status.startswith("failed:")
    assert "source" in status
    assert collect_non_successes({"crosscheck_foreign": status}) == ["crosscheck_foreign"]
