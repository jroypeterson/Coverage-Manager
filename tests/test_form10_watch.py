"""Tests for the Form 10 spin-off watch.

Every test here is a bug that was live during development on 2026-08-06. The
module looked like it worked at each stage, and each stage was wrong in a way
that produced a plausible, quiet, incomplete answer — which is the failure mode
a discovery lane cannot afford.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from universe import form10_watch as f10  # noqa: E402


def _hit(cik, name, ticker, adsh, filed, sic, ftype, fn):
    return {"_id": f"{adsh}:{fn}",
            "_source": {"ciks": [cik], "display_names": [f"{name}  ({ticker})  (CIK {cik})"],
                        "adsh": adsh, "file_date": filed, "form": "10-12B",
                        "sics": [sic], "file_type": ftype}}


def _pager(hits, page=10):
    """Mimic EDGAR FTS: ten hits per page, `total` reported on every page."""
    def open_url(url):
        off = 0
        if "from=" in url:
            off = int(url.split("from=")[1].split("&")[0])
        chunk = hits[off:off + page]
        return io.BytesIO(json.dumps(
            {"hits": {"total": {"value": len(hits)}, "hits": chunk}}).encode())
    return open_url


# ------------------------------------------------------------------ pagination


def test_all_pages_are_read_not_just_the_first():
    """FTS returns 10 hits/page and one filing carries a dozen exhibits, so one
    page can be a single company's boilerplate. Reading page 1 only reported
    "10 distinct registrants" for a window that held far more."""
    hits = [_hit(f"{1000+i}", f"Co {i} Inc.", f"T{i}", f"a-{i}", "2026-05-01",
                 "2834", "EX-99.1", f"x{i}.htm") for i in range(25)]
    res = f10.search_form10("2026-01-01", "2026-08-06", ua="ua",
                            opener=_pager(hits))
    assert res.status == "ok"
    assert len(res.filings) == 25


def test_many_documents_collapse_to_one_filing_per_registrant():
    hits = [_hit("999", "Spin Inc.", "SPN", "a-1", "2026-05-01", "2834",
                 t, f"{t}.htm")
            for t in ("EX-4.3", "EX-21.1", "EX-99.1", "10-12B")]
    res = f10.search_form10("2026-01-01", "2026-08-06", ua="ua",
                            opener=_pager(hits))
    assert len(res.filings) == 1


def test_the_information_statement_exhibit_is_the_one_selected():
    """EX-99.1 carries the parent language; EX-4.3 is an indenture."""
    hits = [_hit("999", "Spin Inc.", "SPN", "a-1", "2026-05-01", "2834",
                 t, f"{t}.htm")
            for t in ("EX-4.3", "EX-21.1", "EX-99.1")]
    res = f10.search_form10("2026-01-01", "2026-08-06", ua="ua",
                            opener=_pager(hits))
    assert res.filings[0].doc == "EX-99.1.htm"


def test_a_failed_search_is_inconclusive_never_an_empty_window():
    def boom(url):
        raise OSError("connection reset")
    res = f10.search_form10("2026-01-01", "2026-08-06", ua="ua", opener=boom)
    assert res.status == "inconclusive" and res.filings == []


# -------------------------------------------------------------------- routing


def _f(**kw):
    base = dict(cik="1", registrant="X Inc.", ticker="X", accession="a",
                filed="2026-05-01", form="10-12B", sic="2834")
    base.update(kw)
    return f10.Filing(**base)


@pytest.mark.parametrize("sic,sector", [("2834", "Biopharma"), ("2836", "Biopharma"),
                                        ("3841", "MedTech"), ("8011", "Healthcare Services"),
                                        ("8731", "Life Science Tools")])
def test_core_sector_sics_are_bucket_1_at_any_size(sic, sector):
    f = f10.classify(_f(sic=sic))
    assert f.verdict == "relevant" and f.sector == sector


def test_a_missing_sic_is_inconclusive_not_irrelevant():
    """An unclassifiable registrant is exactly where a missed spin-off hides."""
    f = f10.classify(_f(sic=""))
    assert f.verdict == "inconclusive"


def test_an_unmapped_sic_is_not_relevant_rather_than_inconclusive():
    f = f10.classify(_f(sic="6021"))       # state commercial bank
    assert f.verdict == "not-relevant"


def test_a_large_parent_promotes_a_non_core_sector_to_bucket_3():
    """Honeywell Aerospace is SIC 3724 — outside every covered sector — and is a
    mandatory Bucket 3 add anyway, because Bucket 3 is size-gated and
    sector-agnostic. Routing on SIC alone could never see it."""
    f = f10.classify(_f(sic="3724"))
    assert f.verdict == "not-relevant"
    f.parent, f.parent_cap = "HONEYWELL INTERNATIONAL INC", 77e9
    f10.apply_size_proxy(f)
    assert f.verdict == "relevant" and "Bucket 3" in f.reason


def test_a_small_parent_does_not_promote():
    f = f10.classify(_f(sic="5072"))
    f.parent, f.parent_cap = "RESIDEO TECHNOLOGIES, INC.", 3e9
    f10.apply_size_proxy(f)
    assert f.verdict == "not-relevant"


def test_an_unknown_parent_cap_never_promotes():
    """None means unknown and must not be rendered — or treated — as small."""
    f = f10.classify(_f(sic="3724"))
    f.parent_cap = None
    f10.apply_size_proxy(f)
    assert f.verdict == "not-relevant"


# ------------------------------------------------------- parent extraction


FEDEX = ("... a wholly owned subsidiary of FedEx Freight Holding Company, Inc. "
         "following the Spin-Off ... our separation from FedEx Corporation. "
         "We are the largest North American less-than-truckload carrier.")


def test_the_registrant_is_not_extracted_as_its_own_parent():
    """FedEx Freight's statement names ITSELF as a subsidiary 200 characters
    before it names FedEx Corporation, and the first-match-wins version picked
    the filer — silently costing a spin-off out of a ~$75B parent."""
    got = f10.extract_parent(FEDEX, "FedEx Freight Holding Company, Inc.")
    assert got == "FedEx Corporation"


def test_a_capture_stops_at_a_corporate_suffix():
    text = "a wholly owned subsidiary of Honeywell International Inc that will hold the assets"
    assert f10.extract_parent(text, "Honeywell Aerospace Inc.") == "Honeywell International Inc"


def test_no_parent_language_yields_empty_not_a_guess():
    assert f10.extract_parent("This document contains no such language.", "X") == ""


# ------------------------------------------------------- parent resolution


SEC_MAP = {
    "0": {"cik_str": 773840, "ticker": "HON", "title": "HONEYWELL INTERNATIONAL INC"},
    "1": {"cik_str": 1000, "ticker": "INTR", "title": "Inter & Co, Inc."},
    "2": {"cik_str": 1740332, "ticker": "REZI", "title": "RESIDEO TECHNOLOGIES, INC."},
    "3": {"cik_str": 1740332, "ticker": "REZI-B", "title": "RESIDEO TECHNOLOGIES, INC."},
    "4": {"cik_str": 2000, "ticker": "BCE", "title": "BCE INC"},
    "5": {"cik_str": 2001, "ticker": "CAE", "title": "CAE INC"},
}


def test_a_containment_match_does_not_beat_the_real_company():
    """`_name_similarity` is token-cover based, so "Inter & Co, Inc." ties with
    "Honeywell International Inc" at 1.00. Whole-string closeness breaks it."""
    cik, tick, title = f10.resolve_company("Honeywell International Inc", SEC_MAP)
    assert tick == "HON"


def test_multiple_share_classes_of_one_company_are_not_ambiguity():
    cik, tick, title = f10.resolve_company("Resideo Technologies, Inc.", SEC_MAP)
    assert cik == "1740332"


def test_two_different_companies_scoring_alike_is_refused():
    """A coin toss presented as a fact is worse than an unresolved parent."""
    assert f10.resolve_company("CE Holdings", SEC_MAP) == ("", "", "")


def test_an_unresolvable_name_fails_closed():
    assert f10.resolve_company("Complete Garbage Xyzzy Inc.", SEC_MAP) == ("", "", "")
    assert f10.resolve_company("", SEC_MAP) == ("", "", "")


# ------------------------------------------------------------------- report


def test_the_report_is_ascii_so_a_cp1252_console_survives_it():
    """An emoji in this string raised UnicodeEncodeError on the scheduled run —
    at the exact moment the job was trying to report what it had found."""
    f = f10.classify(_f(sic="2834", registrant="Atrium Therapeutics, Inc."))
    report = f10.render_report([f], ("2026-01-01", "2026-08-06"), {"1"})
    report.encode("ascii")      # must not raise


def test_inconclusive_filings_appear_in_the_report():
    f = f10.classify(_f(sic="", registrant="Mystery Corp"))
    report = f10.render_report([f], ("2026-01-01", "2026-08-06"), set())
    assert "Mystery Corp" in report and "Inconclusive" in report
