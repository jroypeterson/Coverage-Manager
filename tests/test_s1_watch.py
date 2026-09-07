"""Tests for the S-1 / F-1 IPO pipeline watch.

The lane's whole value is that it reports a filing weeks before anything else can
see it. Every failure mode below produces a *plausible, quiet, incomplete* answer
-- a short list that looks like a quiet week -- which is the one thing a
discovery lane must never do silently.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from universe import s1_watch as s1  # noqa: E402


def _hit(cik, name, ticker, adsh, filed, sic, form="S-1", ftype="S-1", fn="f.htm"):
    return {"_id": f"{adsh}:{fn}",
            "_source": {"ciks": [cik],
                        "display_names": [f"{name}  ({ticker})  (CIK {cik})"],
                        "adsh": adsh, "file_date": filed, "form": form,
                        "sics": [sic], "file_type": ftype}}


def _pager(by_form, page=10):
    """Mimic EDGAR FTS: ten hits per page, `total` on every page, per form."""
    def open_url(url):
        form = url.split("forms=")[1].split("&")[0].replace("%2F", "/")
        hits = by_form.get(form, [])
        off = int(url.split("from=")[1].split("&")[0]) if "from=" in url else 0
        return io.BytesIO(json.dumps(
            {"hits": {"total": {"value": len(hits)},
                      "hits": hits[off:off + page]}}).encode())
    return open_url


# ------------------------------------------------------------------ pagination


def test_reads_every_page_not_just_the_first():
    """One S-1 carries dozens of exhibits, so page one can be one company."""
    hits = [_hit(str(1000 + i), f"Co {i}", f"C{i}", f"a-{i}", "2026-09-01", "2834")
            for i in range(25)]
    res = s1.search_registrations("2026-08-20", "2026-09-03", ua="t",
                                  opener=_pager({"S-1": hits}), forms=("S-1",))
    assert res.status == "ok"
    assert len(res.filings) == 25


def test_each_form_is_searched_separately():
    """`forms=S-1` does not match `F-1`; a joined query that quietly returned
    only the first would look exactly like a week with no foreign issuers."""
    by_form = {"S-1": [_hit("1", "Domestic Inc", "DOM", "a", "2026-09-01", "2834")],
               "F-1": [_hit("2", "Foreign PLC", "FOR", "b", "2026-09-02", "3841",
                            form="F-1", ftype="F-1")]}
    res = s1.search_registrations("2026-08-20", "2026-09-03", ua="t",
                                  opener=_pager(by_form), forms=("S-1", "F-1"))
    assert {f.registrant for f in res.filings} == {"Domestic Inc", "Foreign PLC"}
    assert [f.is_foreign for f in res.filings if f.registrant == "Foreign PLC"] == [True]


def test_an_unreachable_form_is_inconclusive_not_a_quiet_week():
    def boom(url):
        if "F-1" in url:
            raise OSError("network down")
        return io.BytesIO(json.dumps({"hits": {"total": {"value": 0}, "hits": []}}).encode())

    res = s1.search_registrations("2026-08-20", "2026-09-03", ua="t",
                                  opener=boom, forms=("S-1", "F-1"))
    assert res.status == "inconclusive"
    assert "network down" in res.error


def test_amendments_collapse_onto_the_registrant_and_are_marked_a_refresh():
    """A refresh after months of silence is the news, not a second company."""
    by_form = {
        "S-1": [_hit("7", "Entrata Inc", "ENT", "a-1", "2026-05-02", "7372")],
        "S-1/A": [_hit("7", "Entrata Inc", "ENT", "a-2", "2026-09-02", "7372",
                       form="S-1/A", ftype="S-1/A")],
    }
    res = s1.search_registrations("2026-01-01", "2026-09-03", ua="t",
                                  opener=_pager(by_form), forms=("S-1", "S-1/A"))
    assert len(res.filings) == 1
    assert res.filings[0].filed == "2026-05-02"          # earliest kept
    assert res.filings[0].filing_kind == s1.FILING_UPDATE


# --------------------------------------------------------------- classification


def test_core_sector_is_relevant_at_any_size():
    r = s1.classify(s1.Registration(cik="1", registrant="Oura", ticker="",
                                    accession="a", filed="2026-09-02",
                                    form="S-1", sic="3841"))
    assert r.verdict == "relevant"
    assert "Bucket 1" in r.reason


def test_a_blank_check_shell_is_not_relevant():
    r = s1.classify(s1.Registration(cik="1", registrant="Acquisition Corp VII",
                                    ticker="", accession="a", filed="2026-09-02",
                                    form="S-1", sic="6770"))
    assert r.verdict == "not-relevant"
    assert "blank-check" in r.reason


def test_a_missing_sic_is_inconclusive_never_dropped():
    r = s1.classify(s1.Registration(cik="1", registrant="Mystery Co", ticker="",
                                    accession="a", filed="2026-09-02",
                                    form="S-1", sic=""))
    assert r.verdict == "inconclusive"


# ------------------------------------------------------------------ deal size


def test_placeholder_fee_table_values_are_flagged_not_printed_as_a_deal():
    val, placeholder = s1.extract_raise(
        "Proposed Maximum Aggregate Offering Price $ 100,000,000")
    assert val == 100_000_000.0 and placeholder is True
    r = s1.Registration(cik="1", registrant="X", ticker="", accession="a",
                        filed="2026-09-02", form="S-1", sic="2834",
                        raise_usd=val, raise_is_placeholder=placeholder)
    assert "placeholder" in r.raise_label()


def test_the_largest_fee_row_wins_not_the_first():
    """A multi-class fee table opens on a $1,000 stub row."""
    val, _ = s1.extract_raise(
        "maximum aggregate offering price $ 1,000 "
        "maximum aggregate offering price $ 5,200,000,000")
    assert val == 5_200_000_000.0


def test_absurd_values_are_rejected_rather_than_printed():
    assert s1.extract_raise("maximum aggregate offering price $ 12")[0] is None
    assert s1.extract_raise(
        "maximum aggregate offering price $ 900,000,000,000")[0] is None


def test_a_big_raise_promotes_an_uncovered_sector_to_inconclusive():
    """Bucket 2 is any sector at $25B+, so the SIC test is designed to miss it."""
    r = s1.classify(s1.Registration(cik="1", registrant="SB Energy", ticker="",
                                    accession="a", filed="2026-09-01",
                                    form="S-1", sic="4911"))
    assert r.verdict == "not-relevant"
    r.raise_usd, r.raise_is_placeholder = 5_000_000_000.0, False
    s1.apply_size_flag(r)
    assert r.verdict == "inconclusive"
    assert "Bucket 2" in r.reason


def test_a_big_raise_never_demotes_a_relevant_row():
    r = s1.classify(s1.Registration(cik="1", registrant="Oura", ticker="",
                                    accession="a", filed="2026-09-02",
                                    form="S-1", sic="3841"))
    r.raise_usd, r.raise_is_placeholder = 2_000_000_000.0, False
    s1.apply_size_flag(r)
    assert r.verdict == "relevant"


def test_a_placeholder_raise_does_not_promote_anything():
    r = s1.classify(s1.Registration(cik="1", registrant="Wella", ticker="",
                                    accession="a", filed="2026-08-31",
                                    form="S-1", sic="2844"))
    r.raise_usd, r.raise_is_placeholder = 100_000_000.0, True
    s1.apply_size_flag(r)
    assert r.verdict == "not-relevant"


# ---------------------------------------------------------------- carry forward


def _seen(cik, **kw):
    base = {"registrant": "Entrata Inc", "filed": "2026-05-02", "accession": "a",
            "verdict": "relevant", "ticker": "", "sic": "7372", "sector": "Tech",
            "form": "S-1", "filing_kind": s1.FILING_NEW, "raise_usd": None,
            "raise_is_placeholder": False, "withdrawn": False, "reason": "",
            "doc": "", "first_seen": "2026-05-02", "already_listed": False,
            "priced": False}
    base.update(kw)
    return {cik: base}


def test_a_prior_filing_outside_the_window_is_carried(tmp_path):
    """A 14-day window finds new filings; it does not describe the pipeline."""
    out = s1.carry_forward([], _seen("7"), root=tmp_path, today=date(2026, 9, 6))
    assert [r.registrant for r in out] == ["Entrata Inc"]


def test_a_withdrawn_registration_is_not_carried(tmp_path):
    out = s1.carry_forward([], _seen("7", withdrawn=True), root=tmp_path,
                           today=date(2026, 9, 6))
    assert out == []


def test_a_registration_that_priced_is_closed(tmp_path):
    """A 424B final prospectus is filed at pricing -- that is the close, not the
    ticker, which a registrant reserves in the S-1 while still pre-IPO."""
    out = s1.carry_forward([], _seen("7", priced=True), root=tmp_path,
                           today=date(2026, 9, 6))
    assert out == []


def test_a_follow_on_registration_is_never_carried_as_pipeline(tmp_path):
    out = s1.carry_forward([], _seen("7", already_listed=True), root=tmp_path,
                           today=date(2026, 9, 6))
    assert out == []


def test_a_reserved_ticker_alone_does_not_close_a_carried_entry(tmp_path):
    """Entrata carried `ENT` through its whole pre-IPO life."""
    out = s1.carry_forward([], _seen("7", ticker="ENT"), root=tmp_path,
                           today=date(2026, 9, 6))
    assert [r.registrant for r in out] == ["Entrata Inc"]


def test_a_stale_registration_ages_out(tmp_path):
    out = s1.carry_forward([], _seen("7", filed="2024-01-01"), root=tmp_path,
                           today=date(2026, 9, 6))
    assert out == []


def test_an_unreadable_universe_carries_rather_than_dropping(tmp_path):
    """Dropping on an IO error would silently empty the pipeline section."""
    out = s1.carry_forward([], _seen("7"), root=tmp_path, today=date(2026, 9, 6))
    assert len(out) == 1


# --------------------------------------------------------------------- report


def test_report_says_plainly_that_nothing_here_can_be_added():
    regs = [s1.classify(s1.Registration(cik="1", registrant="Oura", ticker="",
                                        accession="a", filed="2026-09-02",
                                        form="S-1", sic="3841"))]
    md = s1.render_report(regs, ("2026-08-23", "2026-09-06"), {"1"})
    assert "watch list, not a" in md
    assert "not a valuation" in md
    assert "(new)" in md


def test_report_is_ascii_for_the_scheduled_console():
    regs = [s1.classify(s1.Registration(cik="1", registrant="Sociéte Générale S.A.",
                                        ticker="", accession="a",
                                        filed="2026-09-02", form="F-1", sic="3841"))]
    md = s1.render_report(regs, ("2026-08-23", "2026-09-06"), set())
    md.encode("ascii")            # raises if the sanitizer missed anything


def test_inconclusive_rows_are_reported_never_hidden():
    regs = [s1.classify(s1.Registration(cik="9", registrant="Mystery Co",
                                        ticker="", accession="a",
                                        filed="2026-09-02", form="S-1", sic=""))]
    md = s1.render_report(regs, ("2026-08-23", "2026-09-06"), set())
    assert "Mystery Co" in md and "Inconclusive" in md


# ------------------------------------------ IPO candidate vs already trading


def _reg(listed=False, sic="2834", **kw):
    r = s1.Registration(cik=kw.get("cik", "1"),
                        registrant=kw.get("registrant", "Example Inc"),
                        ticker=kw.get("ticker", ""), accession="a",
                        filed=kw.get("filed", "2026-09-02"),
                        form="S-1", sic=sic, already_listed=listed)
    s1.classify(r)
    return r


def test_an_already_listed_registrant_is_a_follow_on_not_a_pipeline_entry():
    """Measured 2026-08-27..09-06: 26 rows classified relevant, 11 of them
    already-listed microcaps registering resale shares. A watch list that is 40%
    follow-ons is a list nobody reads."""
    md = s1.render_report([_reg(listed=True, registrant="FingerMotion, Inc."),
                           _reg(cik="2", registrant="Oura Inc.")],
                          ("2026-08-27", "2026-09-06"), set())
    pipeline = md.split("## Already trading")[0]
    assert "Oura Inc." in pipeline
    assert "FingerMotion" not in pipeline
    assert "FingerMotion" in md          # reported, never dropped
    assert "1 in-sector" in md and "1 follow-on / resale" in md


def test_a_big_raise_does_not_promote_a_company_that_already_trades():
    r = _reg(listed=True, sic="4911")
    r.raise_usd, r.raise_is_placeholder = 5_000_000_000.0, False
    s1.apply_size_flag(r)
    assert r.verdict == "not-relevant"


def test_check_status_reports_withdrawal_and_pricing():
    def opener(url):
        return io.BytesIO(json.dumps(
            {"tickers": ["ENT"],
             "filings": {"recent": {"form": ["S-1", "S-1/A", "424B4"]}}}).encode())

    assert s1.check_status("7", ua="t", opener=opener) == (False, True)


def test_check_status_treats_an_unreachable_sec_as_closing_nothing():
    def boom(url):
        raise OSError("down")

    assert s1.check_status("7", ua="t", opener=boom) == (False, False)


# --------------------------------------------- the reserved-ticker trap


def _subs(forms, tickers=()):
    def opener(url):
        return io.BytesIO(json.dumps(
            {"tickers": list(tickers),
             "filings": {"recent": {"form": list(forms)}}}).encode())
    return opener


def test_a_reserved_pre_ipo_ticker_is_not_treated_as_already_trading():
    """Syntiant reserved SYTN and Entrata reserved ENT while both were pre-IPO;
    SEC carries the reserved symbol. Testing on the ticker filed the two
    headline names of the 2026-09-06 Renaissance recap as 'already trading'."""
    assert s1.check_reporting_history(
        "7", ua="t", opener=_subs(["S-1", "S-1/A"], ["SYTN"])) is False


def test_a_company_that_files_10_ks_is_a_follow_on():
    assert s1.check_reporting_history(
        "7", ua="t", opener=_subs(["10-K", "10-Q", "S-1"], ["FNGR"])) is True


def test_an_unreachable_sec_leaves_the_row_in_the_pipeline():
    """Unknown is a candidate to look at: a stray follow-on in the table costs
    far less than a hidden IPO."""
    def boom(url):
        raise OSError("down")

    assert s1.check_reporting_history("7", ua="t", opener=boom) is None


# ============================================================================
# Fixes from the 2026-09-06 Fable structural review. Each of these was live.
# ============================================================================


def test_a_reserved_ticker_does_not_bury_a_name_in_the_not_relevant_tail():
    """THE regression. Tailored Brands (MENW) and Cumberland Farms (CMBY) both
    reserved pre-IPO symbols and both landed in the comma-separated tail under
    'registrants that already trade in uncovered sectors' -- false for both.
    Graybar Electric, a 10-K filer since the 1930s, sat in the pipeline instead
    because it has no ticker. The ticker test, reapplied one section lower."""
    tailored = _reg(cik="1", registrant="Tailored Brands, Inc.", sic="5600",
                    ticker="MENW", listed=False)
    graybar = _reg(cik="2", registrant="GRAYBAR ELECTRIC CO INC", sic="5063",
                   ticker="", listed=True)
    md = s1.render_report([tailored, graybar], ("2026-08-23", "2026-09-06"), set())
    coming = md.split("## Not relevant")[0]
    assert "Tailored Brands" in coming.split("Coming public")[1]
    assert "GRAYBAR" not in coming.split("Coming public")[1]


def test_shells_are_still_excluded_from_the_coming_public_section():
    shell = _reg(cik="3", registrant="Acquisition Corp VII", sic="6770")
    md = s1.render_report([shell], ("2026-08-23", "2026-09-06"), set())
    assert "## Coming public" not in md
    assert "Acquisition Corp VII" in md          # still reported


# ------------------------------------------------------- check_status recency


def _subs_dated(pairs, tickers=()):
    """pairs: [(form, filingDate)] in SEC's parallel-array shape."""
    def opener(url):
        return io.BytesIO(json.dumps({
            "tickers": list(tickers),
            "filings": {"recent": {"form": [f for f, _ in pairs],
                                   "filingDate": [d for _, d in pairs]}}}).encode())
    return opener


def test_an_old_withdrawal_from_a_previous_attempt_does_not_close_this_one():
    """Withdrawing a 2021-22 registration and re-filing is the dominant pattern
    in this cohort. Without a date test the entry vanished the week after it
    left the search window, then reappeared when it next amended."""
    opener = _subs_dated([("RW", "2022-06-01"), ("S-1", "2026-05-02")])
    assert s1.check_status("7", ua="t", opener=opener,
                           since="2026-05-02") == (False, False)


def test_a_withdrawal_after_this_registration_does_close_it():
    opener = _subs_dated([("S-1", "2026-05-02"), ("RW", "2026-08-01")])
    assert s1.check_status("7", ua="t", opener=opener,
                           since="2026-05-02") == (True, False)


def test_a_stale_424b3_resale_supplement_does_not_read_as_priced():
    opener = _subs_dated([("424B3", "2023-01-05"), ("S-1", "2026-05-02")])
    assert s1.check_status("7", ua="t", opener=opener,
                           since="2026-05-02")[1] is False


def test_a_424b4_after_filing_reads_as_priced():
    opener = _subs_dated([("S-1", "2026-05-02"), ("424B4", "2026-09-01")])
    assert s1.check_status("7", ua="t", opener=opener,
                           since="2026-05-02")[1] is True


# --------------------------------------------------------------- carry scope


def test_an_out_of_sector_registrant_is_carried_not_dropped(tmp_path):
    """SB Energy: not-relevant by SIC, the largest deal in the window, and it
    fell out of the report the week it left the 14-day search window. Bucket 2
    is the case the sector test cannot find AND the case carry did not cover."""
    seen = _seen("7", registrant="SB Energy, Inc.", sic="4911",
                 verdict="not-relevant")
    out = s1.carry_forward([], seen, root=tmp_path, today=date(2026, 9, 20))
    assert [r.registrant for r in out] == ["SB Energy, Inc."]
    assert out[0].verdict == "not-relevant"      # verdict survives the round trip


def test_an_inconclusive_registrant_is_carried(tmp_path):
    seen = _seen("7", registrant="Mystery Co", sic="", verdict="inconclusive")
    out = s1.carry_forward([], seen, root=tmp_path, today=date(2026, 9, 20))
    assert [r.verdict for r in out] == ["inconclusive"]


def test_a_carried_shell_is_not_resurrected(tmp_path):
    seen = _seen("7", registrant="Acquisition Corp", sic="6770",
                 verdict="not-relevant")
    assert s1.carry_forward([], seen, root=tmp_path, today=date(2026, 9, 20)) == []


# ------------------------------------------------------------- quiet failures


def test_zero_s1_filings_is_inconclusive_not_a_quiet_week():
    """The floor is dozens a fortnight in any market. Zero means the query shape
    changed, and '0 distinct registrants' printed without comment is
    indistinguishable from a working run."""
    def empty(url):
        return io.BytesIO(json.dumps(
            {"hits": {"total": {"value": 0}, "hits": []}}).encode())

    res = s1.search_registrations("2026-08-23", "2026-09-06", ua="t",
                                  opener=empty, forms=("S-1",))
    assert res.status == "inconclusive"
    assert "implausible" in res.error


def test_a_corrupt_ledger_refuses_to_run_rather_than_overwriting_history(tmp_path):
    """An empty ledger means carry yields nothing, everything looks new, and
    save_seen then REPLACES the unreadable file with one window's rows. This
    repo lives in Dropbox; a conflicted copy is a question of when."""
    p = tmp_path / "s1_seen.json"
    p.write_text("{not json", encoding="utf-8")
    import pytest as _pytest
    with _pytest.raises(s1.LedgerCorrupt):
        s1.load_seen(p)


def test_a_missing_ledger_is_a_legitimate_first_run(tmp_path):
    assert s1.load_seen(tmp_path / "nope.json") == {}


def test_a_json_array_is_corrupt_not_an_empty_ledger(tmp_path):
    p = tmp_path / "s1_seen.json"
    p.write_text("[]", encoding="utf-8")
    import pytest as _pytest
    with _pytest.raises(s1.LedgerCorrupt):
        s1.load_seen(p)


# ------------------------------------------------------------------ fee table


def test_a_one_billion_fee_value_is_a_placeholder_not_a_bucket_2_signal():
    """$1B is a fee-table convention AND exactly BUCKET2_CHECK_RAISE, so it was
    the one value that could fire a Bucket 2 flag on no evidence at all."""
    assert 1_000_000_000.0 in s1._PLACEHOLDER_VALUES
    r = _reg(sic="4911")
    r.raise_usd, r.raise_is_placeholder = s1.extract_raise(
        "<ffd:MaxAggtOfferingPric unitRef='USD'>1000000000.00</ffd:MaxAggtOfferingPric>")
    s1.apply_size_flag(r)
    assert r.verdict == "not-relevant"


# ============================================================================
# Fixes from the 2026-09-06 Codex review.
# ============================================================================


def test_the_newest_accession_is_tracked_alongside_the_earliest():
    """Dedup keeps the EARLIEST filing, so `accession` never changes for the life
    of an entry -- and the size refresh compared against it, so the refresh could
    never fire and the terms-setting S-1/A was the one document never read."""
    by_form = {
        "S-1": [_hit("7", "Entrata Inc", "ENT", "orig-1", "2026-05-02", "7372")],
        "S-1/A": [_hit("7", "Entrata Inc", "ENT", "amend-2", "2026-09-02", "7372",
                       form="S-1/A", ftype="S-1/A")],
    }
    res = s1.search_registrations("2026-01-01", "2026-09-06", ua="t",
                                  opener=_pager(by_form), forms=("S-1", "S-1/A"))
    r = res.filings[0]
    assert (r.accession, r.filed) == ("orig-1", "2026-05-02")      # pipeline age
    assert (r.latest_accession, r.latest_filed) == ("amend-2", "2026-09-02")


def test_a_single_filing_sets_latest_to_itself():
    by_form = {"S-1": [_hit("7", "Solo Inc", "", "only-1", "2026-09-02", "7372")]}
    res = s1.search_registrations("2026-09-01", "2026-09-06", ua="t",
                                  opener=_pager(by_form), forms=("S-1",))
    r = res.filings[0]
    assert r.latest_accession == "only-1" and r.latest_filed == "2026-09-02"


# --------------------------------------------------------------- state saving


def test_a_failed_state_write_raises_rather_than_warning(tmp_path):
    """form10's save_seen logs and returns; inherited here that silently loses
    the only state this lane accumulates while the step stays green."""
    import pytest as _pytest
    # A FILE where the parent directory should be. Portable: Windows ignores
    # chmod on directories, so a permission bit proves nothing here.
    blocker = tmp_path / "sub"
    blocker.write_text("not a directory", encoding="utf-8")
    with _pytest.raises(s1.LedgerNotSaved) as err:
        s1.save_seen(blocker / "s1_seen.json", {"1": {}, "2": {}})
    assert "carry-forward state for 2 registrant(s) is lost" in str(err.value)


def test_a_successful_state_write_round_trips(tmp_path):
    p = tmp_path / "s1_seen.json"
    s1.save_seen(p, {"7": {"registrant": "X"}})
    assert s1.load_seen(p) == {"7": {"registrant": "X"}}


# ------------------------------------------------- one definition of the group


def test_is_coming_public_covers_the_out_of_sector_bucket_2_group():
    """Three call sites had three notions of 'worth surfacing', so a week whose
    only discoveries were out-of-sector operating registrants rendered a
    populated report, logged nothing, and exited 0."""
    sb_energy = _reg(cik="1", registrant="SB Energy", sic="4911")
    assert sb_energy.verdict == "not-relevant"
    assert s1.is_coming_public(sb_energy) is True


def test_is_coming_public_excludes_shells_and_follow_ons():
    assert s1.is_coming_public(_reg(sic="6770")) is False           # shell
    assert s1.is_coming_public(_reg(listed=True, sic="2834")) is False


# ------------------------------------------------------------ raise formatting


def test_a_sub_million_raise_is_not_rendered_as_zero():
    """The extract floor is $100k, so these are real and reachable -- and
    `,.0f` printed every one of them as '~$0M proposed raise'."""
    r = s1.Registration(cik="1", registrant="X", ticker="", accession="a",
                        filed="2026-09-02", form="S-1", sic="2834",
                        raise_usd=450_000.0, raise_is_placeholder=False)
    assert r.raise_label() == "~$450K proposed raise"


def test_a_few_million_raise_keeps_a_decimal():
    r = s1.Registration(cik="1", registrant="X", ticker="", accession="a",
                        filed="2026-09-02", form="S-1", sic="2834",
                        raise_usd=15_400_000.0, raise_is_placeholder=False)
    assert r.raise_label() == "~$15.4M proposed raise"


def test_a_billion_scale_placeholder_says_billions_not_thousands_of_millions():
    r = s1.Registration(cik="1", registrant="X", ticker="", accession="a",
                        filed="2026-09-02", form="S-1", sic="2834",
                        raise_usd=1_000_000_000.0, raise_is_placeholder=True)
    assert r.raise_label() == "$1.0B (fee-table placeholder)"


# ============================================================================
# Second Codex round: partial fixes completed.
# ============================================================================


def test_an_unverified_row_carries_its_unverified_state(tmp_path):
    """A failed SEC check was persisted as plain already_listed=False, so once
    the filing left the search window carry-forward rebuilt it checking only
    withdrawal and pricing -- and it read as verified and green for 400 days."""
    seen = _seen("7", reporting_verified=False)
    out = s1.carry_forward([], seen, root=tmp_path, today=date(2026, 9, 6))
    assert out[0].reporting_verified is False


def test_a_verified_row_stays_verified_through_the_ledger(tmp_path):
    out = s1.carry_forward([], _seen("7"), root=tmp_path, today=date(2026, 9, 6))
    assert out[0].reporting_verified is True


def test_a_legacy_ledger_entry_defaults_to_verified(tmp_path):
    """Entries written before the field existed must not all become unverified."""
    seen = _seen("7")
    seen["7"].pop("reporting_verified", None)
    out = s1.carry_forward([], seen, root=tmp_path, today=date(2026, 9, 6))
    assert out[0].reporting_verified is True


# --------------------------------------------------- report partition parity


def test_an_already_listed_row_with_an_unmapped_sic_is_a_follow_on():
    """It was excluded from is_coming_public but still rendered under
    'Inconclusive - could not classify', i.e. presented as a possible IPO when
    the lane already knew it was a follow-on."""
    r = _reg(listed=True, sic="")
    assert r.verdict == "inconclusive"
    assert s1.is_coming_public(r) is False
    md = s1.render_report([r], ("2026-08-23", "2026-09-06"), set())
    assert "Already trading" in md
    assert "Inconclusive" not in md


def test_every_row_lands_in_exactly_one_report_section():
    """The partition must be total: a row that falls through every section is a
    registrant the report silently never mentions."""
    rows = [_reg(cik="1", sic="2834"),                       # relevant
            _reg(cik="2", sic="", listed=False),             # inconclusive
            _reg(cik="3", sic="4911"),                       # out-of-sector
            _reg(cik="4", sic="2834", listed=True),          # follow-on
            _reg(cik="5", sic="6770")]                       # shell
    md = s1.render_report(rows, ("2026-08-23", "2026-09-06"), set())
    counts = {
        "pipeline": md.count("## Pipeline"),
        "coming": md.count("## Coming public"),
        "followon": md.count("## Already trading"),
        "inconclusive": md.count("## Inconclusive"),
        "rest": md.count("## Not relevant"),
    }
    assert all(v == 1 for v in counts.values()), counts
    header = md.splitlines()[2]
    assert "1 in-sector" in header and "1 follow-on / resale" in header


def test_the_partition_is_total_and_its_counts_sum():
    """The body partition was total but the HEADER omitted the out-of-sector
    bucket, so a five-row report printed '5 distinct' above four counts that
    summed to four."""
    rows = [_reg(cik="1", sic="2834"), _reg(cik="2", sic=""),
            _reg(cik="3", sic="4911"), _reg(cik="4", sic="2834", listed=True),
            _reg(cik="5", sic="6770")]
    parts = s1.partition(rows)
    assert sum(len(v) for v in parts.values()) == len(rows)
    md = s1.render_report(rows, ("2026-08-23", "2026-09-06"), set())
    header = md.splitlines()[2]
    import re as _re
    counted = sum(int(n) for n in _re.findall(r"(\d+) (?:in-sector|out-of-sector"
                                              r"|inconclusive|follow-on|not relevant)",
                                              header))
    assert counted == len(rows), header


def test_the_partition_assertion_fires_if_a_bucket_is_ever_lost():
    rows = [_reg(cik=str(i), sic=s) for i, s in
            enumerate(["2834", "", "4911", "6770"])]
    parts = s1.partition(rows)
    assert sum(len(v) for v in parts.values()) == 4
    assert set(parts) == {"pipeline", "other", "inconclusive", "followon", "rest"}


def test_an_empty_run_partitions_cleanly():
    assert sum(len(v) for v in s1.partition([]).values()) == 0


# ============================================================================
# De-SPAC line and confidential section (2026-09-06).
# ============================================================================


def test_only_blank_check_filers_count_as_de_spacs():
    """An S-4 is filed for every stock-for-stock merger. Measured over three
    months: 131 registrants, 6 with a blank-check filer. The other 125 are
    ordinary M&A that other lanes already handle."""
    by_form = {
        "S-4": [_hit("1", "McKinley Acquisition Corp", "", "a", "2026-08-12", "6770"),
                _hit("2", "Pfizer Inc", "PFE", "b", "2026-08-13", "2834")],
    }
    out = s1.search_despacs("2026-08-01", "2026-09-06", ua="t",
                            opener=_pager(by_form))
    assert [r.registrant for r in out] == ["McKinley Acquisition Corp"]


def test_the_de_spac_section_claims_no_sector():
    """The filer is the SPAC, so its SIC describes the shell, not the business
    coming public -- and claiming a sector from it would be a false routing."""
    spac = s1.Registration(cik="1", registrant="McKinley Acquisition Corp",
                           ticker="", accession="a", filed="2026-08-12",
                           form="S-4", sic="6770")
    md = s1.render_report([], ("2026-08-01", "2026-09-06"), set(), despacs=[spac])
    assert "De-SPAC registrations (1)" in md
    assert "No sector is claimed" in md
    assert "McKinley" in md


def test_a_failed_de_spac_search_costs_only_the_de_spac_line():
    """A bonus line must never take down the report it rides on."""
    def boom(url):
        raise OSError("down")

    assert s1.search_despacs("2026-08-01", "2026-09-06", ua="t", opener=boom) == []


def test_the_confidential_section_is_embedded_when_present():
    md = s1.render_report([], ("2026-08-23", "2026-09-06"), set(),
                          confidential_md="## Confidential submissions (1)\n\nbody")
    assert "Confidential submissions (1)" in md


def test_no_confidential_entries_adds_no_section():
    md = s1.render_report([], ("2026-08-23", "2026-09-06"), set())
    assert "Confidential submissions" not in md


def test_de_spac_discovery_is_not_disabled_by_no_sizes():
    """`--no-sizes` means "skip the fee-table fetches". De-SPAC output has no
    size by construction, so gating it on that flag silently switched off a
    discovery source the flag has nothing to do with."""
    import inspect
    src = inspect.getsource(s1.run)
    assert "search_despacs(start, end, ua=ua)" in src
    assert "search_despacs(start, end, ua=ua) if fetch_sizes" not in src
