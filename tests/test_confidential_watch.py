"""Tests for the confidential-submission watch list.

The list holds the earliest signal the project has and the least certain one at
the same time. Every test here defends the same boundary: a journalist's rumour
must never be able to acquire the authority of a company's own statement.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from universe import confidential_watch as cw  # noqa: E402


def _row(company="Acme Robotics", kind=cw.SOURCE_ANNOUNCEMENT,
         first_seen="2026-09-01", **kw):
    base = {"company": company, "source_kind": kind,
            "source_url": "https://example.com/pr", "first_seen": first_seen,
            "note": "MedTech", "status": "open", "flipped_to": ""}
    base.update(kw)
    return base


def _write(tmp_path, rows):
    p = tmp_path / "confidential_watch.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    return p


# ------------------------------------------------------------------ schema


def test_a_missing_file_is_an_empty_list_not_an_error(tmp_path):
    assert cw.load(tmp_path / "nope.json") == []


def test_an_unknown_source_kind_is_rejected_by_name(tmp_path):
    """"rumoured", "sources", "leak" all mean `report`; letting them through puts
    a rumour one typo away from reading as a company statement."""
    p = _write(tmp_path, [_row(kind="rumoured")])
    with pytest.raises(ValueError) as err:
        cw.load(p)
    assert "source_kind" in str(err.value)
    assert "report" in str(err.value)


def test_a_missing_source_url_is_rejected(tmp_path):
    """An unsourced claim is not evidence of anything."""
    p = _write(tmp_path, [_row(source_url="")])
    with pytest.raises(ValueError) as err:
        cw.load(p)
    assert "source_url" in str(err.value)


def test_the_offending_company_is_named_in_the_error(tmp_path):
    p = _write(tmp_path, [_row(), _row(company="Broken Co", first_seen="")])
    with pytest.raises(ValueError) as err:
        cw.load(p)
    assert "Broken Co" in str(err.value)


def test_a_malformed_file_raises_rather_than_silently_emptying(tmp_path):
    p = tmp_path / "confidential_watch.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        cw.load(p)


def test_a_json_object_is_not_a_watch_list(tmp_path):
    p = tmp_path / "confidential_watch.json"
    p.write_text('{"company": "Acme"}', encoding="utf-8")
    with pytest.raises(ValueError):
        cw.load(p)


def test_a_valid_list_round_trips(tmp_path):
    p = _write(tmp_path, [_row()])
    entries = cw.load(p)
    cw.save(p, entries)
    assert [e.company for e in cw.load(p)] == ["Acme Robotics"]


# ------------------------------------------------------------------ render


def test_announcements_and_reports_never_share_a_table():
    entries = [cw.Entry(**_row(company="Said So")),
               cw.Entry(**_row(company="Heard So", kind=cw.SOURCE_REPORT))]
    md = cw.render(entries)
    said, heard = md.split("### Press reports only")
    assert "Said So" in said and "Heard So" not in said
    assert "Heard So" in heard


def test_the_rumour_table_is_labelled_unconfirmed():
    md = cw.render([cw.Entry(**_row(kind=cw.SOURCE_REPORT))])
    assert "UNCONFIRMED" in md
    assert "rumour" in md


def test_the_section_says_out_loud_that_it_carries_no_sizes():
    md = cw.render([cw.Entry(**_row())])
    assert "No sizes" in md


def test_closed_entries_do_not_render():
    entries = [cw.Entry(**_row(status="flipped")),
               cw.Entry(**_row(company="Gone", status="expired"))]
    assert cw.render(entries) == ""


def test_an_empty_list_renders_nothing():
    assert cw.render([]) == ""


# --------------------------------------------------------------- reconcile


def test_an_entry_closes_when_its_company_files_publicly():
    """Otherwise the same company sits in two sections of one report."""
    entries = [cw.Entry(**_row(company="Acme Robotics"))]
    entries, notes = cw.reconcile(entries, ["Acme Robotics, Inc."],
                                  today=date(2026, 9, 20))
    assert entries[0].status == "flipped"
    assert entries[0].flipped_to == "Acme Robotics, Inc."
    assert "flipped" in notes[0]


def test_corporate_suffixes_do_not_defeat_the_match():
    entries = [cw.Entry(**_row(company="Acme Robotics Holdings Inc."))]
    entries, _ = cw.reconcile(entries, ["ACME ROBOTICS HOLDINGS, INC"],
                              today=date(2026, 9, 20))
    assert entries[0].status == "flipped"


def test_a_different_company_does_not_close_an_entry():
    entries = [cw.Entry(**_row(company="Acme Robotics"))]
    entries, notes = cw.reconcile(entries, ["Beta Industries"],
                                  today=date(2026, 9, 20))
    assert entries[0].status == "open" and notes == []


def test_a_stale_entry_expires_and_says_how_long_it_waited():
    entries = [cw.Entry(**_row(first_seen="2024-01-01"))]
    entries, notes = cw.reconcile(entries, [], today=date(2026, 9, 20))
    assert entries[0].status == "expired"
    assert "days with no public filing" in notes[0]


def test_reconcile_never_reopens_a_closed_entry():
    entries = [cw.Entry(**_row(status="flipped", flipped_to="Acme, Inc."))]
    entries, notes = cw.reconcile(entries, ["Acme Robotics, Inc."],
                                  today=date(2026, 9, 20))
    assert entries[0].status == "flipped" and notes == []


def test_a_bad_first_seen_does_not_crash_the_run():
    entries = [cw.Entry(**_row(first_seen="not-a-date"))]
    entries, _ = cw.reconcile(entries, [], today=date(2026, 9, 20))
    assert entries[0].status == "open"


def test_the_shipped_watch_list_is_valid():
    """The real file must load: a malformed one stops the lane."""
    p = PROJECT_ROOT / cw.WATCH_PATH
    if not p.exists():
        pytest.skip("no watch list in this checkout")
    entries = cw.load(p)
    assert all(e.source_kind in cw.SOURCE_KINDS for e in entries)
    assert all(e.source_url for e in entries)


# --------------------------------------------- status validation (Codex round 4)


def test_a_missing_status_defaults_to_open_not_to_empty(tmp_path):
    """`row.get(k, "")` replaced the dataclass default with "", which is not
    "open" -- and both reconcile() and render() act only on "open", so an entry
    written without the field disappeared from the report permanently."""
    row = {"company": "Acme", "source_kind": cw.SOURCE_REPORT,
           "source_url": "https://x", "first_seen": "2026-09-01"}
    p = _write(tmp_path, [row])
    assert cw.load(p)[0].status == cw.STATUS_OPEN
    assert "Acme" in cw.render(cw.load(p))


def test_an_unrecognised_status_is_rejected_by_name(tmp_path):
    p = _write(tmp_path, [_row(status="closed")])
    with pytest.raises(ValueError) as err:
        cw.load(p)
    assert "status" in str(err.value) and "hides the company" in str(err.value)


def test_every_legal_status_loads():
    for s in cw.STATUSES:
        assert cw.Entry(**_row(status=s)).status == s


def test_a_null_note_does_not_become_the_string_none(tmp_path):
    row = _row()
    row["note"] = None
    p = _write(tmp_path, [row])
    assert cw.load(p)[0].note == ""
