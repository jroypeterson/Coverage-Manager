"""Tests for the coverage-candidate ledger."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from universe import candidate_ledger as cl  # noqa: E402


def _cand(ticker="CSQR", company="Csquare, Inc.", **kw):
    base = {"ticker": ticker, "company": company, "exchange": "NYSE",
            "sector": "Tech", "subsector": "Data Center", "trigger": "IPO"}
    base.update(kw)
    return base


class TestUpsert:
    def test_new_candidate_is_added_as_pending(self):
        rows = []
        res = cl.upsert(rows, [_cand()], today=date(2026, 7, 28))
        assert res == {"added": 1, "refreshed": 0, "skipped_decided": 0}
        assert rows[0]["status"] == "pending"
        assert rows[0]["first_proposed"] == "2026-07-28"
        assert rows[0]["pending_since"] == "2026-07-28"

    def test_reproposal_bumps_last_seen_but_not_the_expiry_clock(self):
        """A name re-listed every week must still age out — that is the point."""
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 6, 19))
        cl.upsert(rows, [_cand()], today=date(2026, 7, 28))
        assert len(rows) == 1
        assert rows[0]["first_proposed"] == "2026-06-19"
        assert rows[0]["pending_since"] == "2026-06-19"   # NOT reset
        assert rows[0]["last_seen"] == "2026-07-28"

    def test_decided_rows_are_never_reopened(self):
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 6, 19))
        cl.decide(rows, "CSQR", "declined", today=date(2026, 6, 20))
        res = cl.upsert(rows, [_cand()], today=date(2026, 7, 28))
        assert res["skipped_decided"] == 1
        assert rows[0]["status"] == "declined"
        assert rows[0]["last_seen"] == "2026-06-19"       # untouched

    def test_pending_since_override_gives_an_imported_backlog_a_fresh_clock(self):
        rows = []
        cl.upsert(rows, [_cand(first_proposed="2026-06-19")],
                  today=date(2026, 7, 28), pending_since=date(2026, 7, 28))
        assert rows[0]["first_proposed"] == "2026-06-19"  # history preserved
        assert rows[0]["pending_since"] == "2026-07-28"   # clock restarted

    def test_ticker_is_normalised_and_matched_case_insensitively(self):
        rows = []
        cl.upsert(rows, [_cand(ticker="csqr")], today=date(2026, 7, 28))
        assert rows[0]["ticker"] == "CSQR"
        cl.upsert(rows, [_cand(ticker="CsQr")], today=date(2026, 7, 29))
        assert len(rows) == 1

    def test_company_name_conflict_is_flagged_not_overwritten(self):
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 6, 19))
        cl.upsert(rows, [_cand(company="Something Else Corp")],
                  today=date(2026, 7, 28))
        assert rows[0]["company"] == "Csquare, Inc."
        assert "name conflict" in rows[0]["notes"]

    def test_candidate_without_a_ticker_raises(self):
        with pytest.raises(cl.LedgerError):
            cl.upsert([], [_cand(ticker="")], today=date(2026, 7, 28))


class TestExpiry:
    def test_pending_past_the_cutoff_expires(self):
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 5, 1))
        expired = cl.expire_stale(rows, today=date(2026, 7, 28))
        assert [r["ticker"] for r in expired] == ["CSQR"]
        assert rows[0]["status"] == "expired"
        assert rows[0]["decision_date"] == "2026-07-28"

    def test_pending_inside_the_window_survives(self):
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 7, 1))
        assert cl.expire_stale(rows, today=date(2026, 7, 28)) == []
        assert rows[0]["status"] == "pending"

    def test_boundary_is_exclusive_at_exactly_max_age(self):
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 5, 29))   # exactly 60 days
        assert cl.expire_stale(rows, today=date(2026, 7, 28)) == []

    def test_already_decided_rows_are_not_expired(self):
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 1, 1))
        cl.decide(rows, "CSQR", "approved", today=date(2026, 1, 2))
        assert cl.expire_stale(rows, today=date(2026, 7, 28)) == []
        assert rows[0]["status"] == "approved"

    def test_unparseable_date_is_warned_not_expired(self):
        """An unreadable date is not evidence that something is stale."""
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 1, 1))
        rows[0]["pending_since"] = "not-a-date"
        assert cl.expire_stale(rows, today=date(2026, 7, 28)) == []
        assert rows[0]["status"] == "pending"


class TestDecideAndRevive:
    def test_decide_records_date_and_source(self):
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 7, 28))
        cl.decide(rows, "CSQR", "approved", today=date(2026, 7, 29),
                  source="slack-thread")
        assert rows[0]["status"] == "approved"
        assert rows[0]["decision_date"] == "2026-07-29"
        assert rows[0]["decision_source"] == "slack-thread"

    def test_revive_restores_pending_with_a_fresh_clock(self):
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 5, 1))
        cl.expire_stale(rows, today=date(2026, 7, 28))
        cl.revive(rows, "CSQR", today=date(2026, 7, 28))
        assert rows[0]["status"] == "pending"
        assert rows[0]["pending_since"] == "2026-07-28"
        assert rows[0]["decision_date"] == ""
        assert cl.expire_stale(rows, today=date(2026, 7, 28)) == []

    def test_unknown_status_raises(self):
        rows = []
        cl.upsert(rows, [_cand()], today=date(2026, 7, 28))
        with pytest.raises(cl.LedgerError):
            cl.decide(rows, "CSQR", "maybe", today=date(2026, 7, 28))

    def test_decide_on_missing_ticker_raises(self):
        with pytest.raises(cl.LedgerError):
            cl.decide([], "NOPE", "approved", today=date(2026, 7, 28))


class TestRoundTrip:
    def test_save_load_preserves_rows(self, tmp_path):
        rows = []
        cl.upsert(rows, [_cand(), _cand(ticker="STDN", company="Standard Nuclear")],
                  today=date(2026, 7, 28))
        p = tmp_path / "ledger.csv"
        cl.save(rows, p)
        back = cl.load(p)
        assert {r["ticker"] for r in back} == {"CSQR", "STDN"}
        assert all(r["status"] == "pending" for r in back)

    def test_written_file_has_no_bom(self, tmp_path):
        """A BOM would blank the join key for any plain-utf-8 reader."""
        p = tmp_path / "ledger.csv"
        cl.save([], p)
        assert not p.read_bytes().startswith(b"\xef\xbb\xbf")
        assert p.read_bytes().startswith(b"ticker,")

    def test_load_tolerates_a_bom(self, tmp_path):
        p = tmp_path / "ledger.csv"
        cl.save([], p)
        p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
        assert cl.load(p) == []

    def test_missing_file_is_an_empty_ledger(self, tmp_path):
        assert cl.load(tmp_path / "nope.csv") == []

    def test_unknown_status_on_load_raises(self, tmp_path):
        p = tmp_path / "ledger.csv"
        p.write_text("ticker,status\nCSQR,banana\n", encoding="utf-8")
        with pytest.raises(cl.LedgerError):
            cl.load(p)

    def test_save_leaves_no_tmp_file_behind(self, tmp_path):
        p = tmp_path / "ledger.csv"
        cl.save([_cand(first_proposed="2026-07-28", status="pending")], p)
        assert list(tmp_path.glob("*.tmp")) == []
