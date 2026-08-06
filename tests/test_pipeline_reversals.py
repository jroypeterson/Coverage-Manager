"""Tests for the promised-then-excluded cross-check.

The live case is pinned deliberately: a check built for one incident that cannot
find that incident is worse than no check, because it reports clean.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reporting import pipeline_reversals as pr  # noqa: E402

REPORTS = PROJECT_ROOT / "reports"


def _find_report(date: str) -> Path:
    """Resolve a dated report from either location.

    The weekly `archive` step sweeps dated reports into `reports/old reports/`,
    so pinning the live directory made all six corpus-backed tests skip the
    moment a weekly run archived -- silently, which is the exact
    "absent data is not a finding" failure these tests exist to guard against.
    `load_prior_reports` already searched both; its tests did not.
    """
    name = f"weekly_coverage_universe_additions_{date}.md"
    for folder in (REPORTS, REPORTS / "old reports"):
        p = folder / name
        if p.exists():
            return p
    return REPORTS / name


CUR = _find_report("2026-07-31")


# ------------------------------------------------------------------- extraction


def test_commitment_recognised_in_a_bullet_not_only_a_table():
    """The 07-24 report kept its forward book as bullets under `## Notes`."""
    md = ("## Notes\n\n"
          "  - **Jersey Mike's Subs (JMKE)** - roadshow launched 7/20; "
          "43.5M shares at $21-25. Would be a Consumer add at pricing.\n")
    assert "jmke" in pr.promises(md)


def test_commitment_recognised_in_a_table_row():
    md = ("## Pipeline / filings to monitor\n\n"
          "| Company | Ticker | Why |\n|---|---|---|\n"
          "| **Shein** | (HK) | **Will be a mandatory Bucket 2 add "
          "the moment it prices.** |\n")
    assert pr.promises(md)


def test_mere_monitoring_is_not_a_promise():
    """Or every SPAC in the forward book would flag every single week."""
    md = ("## Pipeline\n\n| Company | Ticker | Why |\n|---|---|---|\n"
          "| Churchill Capital XIII | XIIIU | Tracked only if it announces "
          "a relevant target. |\n")
    assert pr.promises(md) == {}


def test_exclusions_are_section_scoped_not_document_wide():
    md = ("## Recommendations\n\n| Company | Ticker | Why |\n|---|---|---|\n"
          "| Acme | ACME | a great add |\n")
    assert pr.exclusions(md) == {}


# --------------------------------------------------------------------- matching


PRIOR = ("## Notes\n\n- **Jersey Mike's Subs (JMKE)** - prices 7/30. "
         "Would be a Consumer add at pricing.\n")


def test_reversal_is_found_when_the_exclusion_is_silent():
    cur = ("## Considered and excluded\n\n| Company | Ticker | Why not |\n"
           "|---|---|---|\n| Jersey Mike's Subs | JMKE | Restaurant "
           "franchisor - not universe-relevant. |\n")
    found = pr.find_reversals(cur, [("2026-07-24", PRIOR)])
    assert [r.key for r in found] == ["jmke"]
    assert found[0].prior_date == "2026-07-24"


@pytest.mark.parametrize("phrase", [
    "This reverses the 07-24 call",
    "we previously said it would be an add; that was wrong",
    "superseded by the sector review",
    "no longer a candidate after the terms changed",
])
def test_an_acknowledged_reversal_is_not_a_finding(phrase):
    cur = ("## Considered and excluded\n\n| Company | Ticker | Why not |\n"
           "|---|---|---|\n| Jersey Mike's Subs | JMKE | Restaurant "
           f"franchisor. {phrase}. |\n")
    assert pr.find_reversals(cur, [("2026-07-24", PRIOR)]) == []


def test_citing_the_prior_report_date_is_not_an_acknowledgement():
    """The real 07-31 row did exactly this, and it is what JP missed.

    Naming the earlier report is not the same as saying the earlier call has been
    withdrawn — a reader sees a tidy cross-reference, not a changed verdict.
    """
    cur = ("## Considered and excluded\n\n| Company | Ticker | Why not |\n"
           "|---|---|---|\n| Jersey Mike's Subs | JMKE | Restaurant franchisor "
           "- not universe-relevant. Flagged in the 2026-07-24 report; "
           "closing the loop so it stops recurring. |\n")
    assert pr.find_reversals(cur, [("2026-07-24", PRIOR)])


def test_a_promise_never_excluded_is_not_a_finding():
    cur = "## Considered and excluded\n\n| Company | Ticker | Why not |\n"
    assert pr.find_reversals(cur, [("2026-07-24", PRIOR)]) == []


# ------------------------------------------------------------------- live corpus


@pytest.mark.skipif(not CUR.exists(), reason="sample report not present")
def test_finds_the_live_jersey_mikes_reversal():
    md = CUR.read_text(encoding="utf-8")
    found = pr.find_reversals(md, pr.load_prior_reports(REPORTS, "2026-07-31"))
    assert [r.company for r in found] == ["Jersey Mike's Subs"]
    assert found[0].prior_date == "2026-07-24"


@pytest.mark.skipif(not CUR.exists(), reason="sample report not present")
def test_corpus_false_positive_rate_stays_at_zero():
    """Run over every archived report. One finding total, and it is the real one.

    An over-eager check is the same class of defect as a missing one: it trains
    the reader to skip the warning.
    """
    seen = {}
    for folder in (REPORTS, REPORTS / "old reports"):
        if folder.is_dir():
            for path in folder.glob(pr.REPORT_GLOB):
                m = pr.DATE_RE.search(path.name)
                if m:
                    seen.setdefault(m.group(1), path)
    assert len(seen) >= 5, "corpus too small for this test to mean anything"
    total = []
    for report_date, path in seen.items():
        md = path.read_text(encoding="utf-8", errors="replace")
        total += pr.find_reversals(md, pr.load_prior_reports(REPORTS, report_date))
    assert [r.company for r in total] == ["Jersey Mike's Subs"]


def test_load_prior_reports_searches_the_archive_folder_too():
    """The weekly archive step moves dated reports into `old reports/`."""
    found = pr.load_prior_reports(REPORTS, "2026-07-31")
    assert any(d == "2026-07-24" for d, _ in found), (
        "07-24 lives in 'old reports/'; missing it makes the check silently blind")


def test_the_quoted_promise_is_the_committing_sentence_not_the_line_head():
    """Jersey Mike's ran 190 chars of deal terms before the promise.

    Trimming from the front cut off the only words that make it a reversal,
    leaving a warning whose own evidence was missing.
    """
    line = ("- **Jersey Mike's Subs (JMKE)** - roadshow launched 7/20; 43.5M shares "
            "at $21-25, implying $6.7-7.9B valuation; ~68% secondary. Expected to "
            "price ~week of 7/27. Would be a Consumer add at pricing.")
    assert pr._commitment_sentence(line) == "Would be a Consumer add at pricing."


def test_a_quoted_exclusion_drops_its_table_pipes():
    row = "| Jersey Mike's Subs | JMKE | Restaurant franchisor - not relevant. |"
    assert pr._trim(row) == "Restaurant franchisor - not relevant."
