"""Tests for the blank-CIK name resolver (report-only detector).

Built 2026-07-30 after a Codex review and a Fable review of the plan. The two
design decisions those reviews forced are both pinned here: the verdict split
(a foreign primary listing must NOT be called a rename) and the ledger routing
(an already-adjudicated blank must not be re-raised as a fresh finding).
"""
import pytest

from universe import cik_name_resolver as R


def _df(rows):
    import pandas as pd
    base = {"Ticker": "", "Company Name": "", "CIK": "",
            "Country (Listing)": "United States"}
    return pd.DataFrame([{**base, **r} for r in rows])


def _map(**entries):
    """{TICKER: (cik, title)} — the shape cik_backfill.fetch_sec_cik_map returns."""
    return dict(entries)


def _no_ledger():
    """An EMPTY but structurally valid ledger.

    `ledger=None` means "auto-load the real one", not "no ledger" — so a test
    using None against a ticker that IS adjudicated (CSL, MED, MOVE) silently
    exercises the ledger path instead of the one under test. Use this when the
    ledger is not what is being tested."""
    return {"entries": [], "removals": [], "renames": [], "held": []}


# ── the verdict split (Codex) ───────────────────────────────────────────────

def test_a_us_listed_row_with_a_different_sec_ticker_is_the_valuable_class():
    res = R.resolve(
        df=_df([{"Ticker": "CYBN", "Company Name": "Cybin Inc"}]),
        cik_map=_map(HELP=("1833141", "CYBIN INC.")), ledger=None)
    f = res.by_verdict(R.STALE_US_LISTING)
    assert len(f) == 1 and f[0].ticker == "CYBN"
    assert "HELP" in f[0].sec_tickers


def test_a_foreign_primary_listing_is_NOT_called_a_rename():
    """The correctness bug the first review caught. SEC knowing a US ADR for a
    foreign issuer is a DIFFERENT LINE. Six live rows would have been mislabelled
    — Shionogi (SGIOF), Fresenius (FSNUY), CUV.AX, TLX.AX, NGEN.V, CSL."""
    res = R.resolve(
        df=_df([{"Ticker": "4507.T", "Company Name": "Shionogi & Co., Ltd.",
                 "Country (Listing)": "Japan"}]),
        cik_map=_map(SGIOF=("1000001", "Shionogi & Co Ltd")), ledger=None)
    assert res.by_verdict(R.STALE_US_LISTING) == []
    other = res.by_verdict(R.SEC_REGISTERED_OTHER_LINE)
    assert len(other) == 1
    assert "not a rename" in other[0].detail.lower()


def test_the_low_severity_class_is_excluded_from_needs_review():
    """`sec_registered_other_line` is informational — alarming on it every week
    for the same 5 permanent rows is how a report gets ignored."""
    res = R.resolve(
        df=_df([{"Ticker": "FRE", "Company Name": "Fresenius SE & Co KGaA",
                 "Country (Listing)": "Germany"}]),
        cik_map=_map(FSNUY=("1000002", "Fresenius SE & Co KGaA")), ledger=None)
    assert res.by_verdict(R.SEC_REGISTERED_OTHER_LINE)
    assert res.needs_review == []


# ── ledger routing (Codex) ──────────────────────────────────────────────────

def test_an_adjudicated_blank_is_not_re_raised_as_a_fresh_finding():
    """MED/MOVE/CSL have a blank CIK recorded as VERIFIED. Without this the
    resolver would fight its own repo's provenance ledger every week."""
    from universe.provenance import load_ledger

    res = R.resolve(
        df=_df([{"Ticker": "MED", "Company Name": "Medartis Holding AG"}]),
        cik_map=_map(MED=("910329", "MEDIFAST INC")), ledger=load_ledger())
    lc = res.by_verdict(R.LEDGER_CONFLICT)
    assert len(lc) == 1
    assert "row-verified" in lc[0].detail
    assert res.by_verdict(R.STALE_US_LISTING) == []


def test_ledger_routing_carries_the_original_evidence():
    """A reader must be able to see WHY it was adjudicated without opening
    another file, or they will re-investigate it."""
    from universe.provenance import load_ledger
    res = R.resolve(df=_df([{"Ticker": "MED", "Company Name": "Medartis Holding AG"}]),
                    cik_map=_map(), ledger=load_ledger())
    assert "MEDIFAST" in res.by_verdict(R.LEDGER_CONFLICT)[0].detail


def test_a_missing_ledger_does_not_abort_the_sweep():
    res = R.resolve(df=_df([{"Ticker": "AAA", "Company Name": "Alpha Therapeutics"}]),
                    cik_map=_map(), ledger=None)
    assert res.checked == 1


# ── ambiguity, counted the right way (Fable) ────────────────────────────────

def test_ambiguity_is_counted_over_distinct_CIKs_not_map_entries():
    """Share classes give ONE issuer several tickers under the same title.
    Counting entries would false-ambiguate `BRK-A`/`BRK-B`."""
    idx = R.build_name_index(_map(**{"BRK-A": ("1067983", "BERKSHIRE HATHAWAY INC"),
                                     "BRK-B": ("1067983", "BERKSHIRE HATHAWAY INC")}))
    key = next(iter(idx))
    assert len(idx[key]) == 1, "one CIK, two tickers -- not ambiguous"
    assert sorted(idx[key]["1067983"][1]) == ["BRK-A", "BRK-B"]


def test_two_distinct_ciks_sharing_a_name_ARE_ambiguous():
    res = R.resolve(
        df=_df([{"Ticker": "XYZ", "Company Name": "Acme Therapeutics"}]),
        cik_map=_map(AAA=("111", "Acme Therapeutics"), BBB=("222", "Acme Therapeutics")),
        ledger=None)
    amb = res.by_verdict(R.AMBIGUOUS_NAME)
    assert len(amb) == 1 and "111" in amb[0].detail and "222" in amb[0].detail
    assert res.needs_review, "ambiguity needs a human"


# ── guards ──────────────────────────────────────────────────────────────────

def test_a_short_name_is_not_evidence():
    """`norm("CSL Limited")` is `csl` — three characters, and many registrants
    share three letters."""
    res = R.resolve(df=_df([{"Ticker": "CSL", "Company Name": "CSL Ltd"}]),
                    cik_map=_map(CSLLY=("111", "CSL Ltd")), ledger=_no_ledger())
    assert res.by_verdict(R.SHORT_NAME_SUPPRESSED)
    assert res.by_verdict(R.STALE_US_LISTING) == []


def test_rows_that_already_have_a_cik_are_skipped_entirely():
    res = R.resolve(df=_df([{"Ticker": "AAPL", "Company Name": "Apple Inc",
                             "CIK": "320193"}]),
                    cik_map=_map(AAPL=("320193", "Apple Inc.")), ledger=None)
    assert res.checked == 0


def test_no_match_is_the_expected_state_and_not_review_worthy():
    """~220 foreign non-registrants land here every run. Alarming on them would
    bury the 4 findings that matter."""
    res = R.resolve(df=_df([{"Ticker": "4519", "Company Name": "Chugai Pharmaceutical",
                             "Country (Listing)": "Japan"}]),
                    cik_map=_map(), ledger=None)
    assert res.by_verdict(R.NO_MATCH)
    assert res.needs_review == []


# ── it never writes ─────────────────────────────────────────────────────────

def test_the_module_has_no_write_path_at_all():
    """A deliberate conclusion, not an omission: of 236 blank-CIK rows only three
    have their ticker in SEC's map, and those three are exactly the
    ledger-verified blanks. So a name match can only ever resolve to a DIFFERENT
    ticker — there is nothing to write that `cik_backfill` has not written."""
    import inspect
    src = inspect.getsource(R)
    for forbidden in ("write_universe_csv", "to_csv", "df.at[", "--apply"):
        assert forbidden not in src, f"a write path appeared: {forbidden}"


def test_an_unreachable_sec_map_reports_learning_nothing():
    """A run that could not check must never read as clean.

    Note `fetched_ok=False` is explicit: an EMPTY supplied map is a legitimate
    input meaning "everything is no_match", and only a failed FETCH is a
    non-result. Conflating the two made this bail-out fire on ordinary input."""
    res = R.resolve(df=_df([{"Ticker": "AAA", "Company Name": "Alpha Therapeutics"}]),
                    cik_map={}, ledger=None, fetched_ok=False)
    assert res.fetched_ok is False
    assert res.checked == 0
    assert "NOTHING was checked" in R.format_report(res)


# ── report ──────────────────────────────────────────────────────────────────

def test_report_warns_that_a_Y_or_F_symbol_is_probably_a_delisting():
    """Verified live: ADAP -> SEC `ADAPY`, exchange OTC. Reading that as a rename
    would file a delisting as a rebrand — the `ZOM` -> `ZOMDF` lesson."""
    res = R.resolve(df=_df([{"Ticker": "ADAP", "Company Name": "Adaptimmune Therapeutics PLC"}]),
                    cik_map=_map(ADAPY=("1621227", "Adaptimmune Therapeutics PLC")),
                    ledger=None)
    md = R.format_report(res)
    assert "ADAPY" in md
    assert "OTC" in md and "DELISTED" in md.upper()


def test_report_renders_every_section_even_when_empty():
    """An absent section reads as 'not checked'; an empty one reads as 'checked,
    nothing found'."""
    md = R.format_report(R.ResolverResult(checked=0))
    for heading in ("Stale US listing", "Ambiguous name", "Already adjudicated",
                    "SEC-registered other line", "No SEC entity"):
        assert heading in md


def test_an_empty_map_is_no_match_not_a_failed_run():
    """The distinction the `fetched_ok` parameter exists for."""
    res = R.resolve(df=_df([{"Ticker": "AAA", "Company Name": "Alpha Therapeutics"}]),
                    cik_map={}, ledger=None)
    assert res.fetched_ok is True
    assert res.checked == 1
    assert res.by_verdict(R.NO_MATCH)


# ── weekly integration ──────────────────────────────────────────────────────

def test_step_status_reports_counted_classes_and_is_ascii():
    """Reaches a cp1252 console mid-run; the universe is global, so no company
    names in the status string."""
    import weekly_universe as wu
    s = wu._cik_resolver_step_status(
        {"checked": 235, "stale_us": 4, "ambiguous": 0, "other_line": 5,
         "needs_review": 4, "fetched_ok": True})
    assert s.startswith("failed: review needed")
    assert "4 stale US listing" in s and "235" in s
    s.encode("ascii", "strict")


def test_a_clean_resolver_run_is_ok():
    import weekly_universe as wu
    s = wu._cik_resolver_step_status(
        {"checked": 235, "stale_us": 0, "ambiguous": 0, "other_line": 5,
         "needs_review": 0, "fetched_ok": True})
    assert s.startswith("ok") and "failed" not in s


def test_an_unreachable_sec_map_is_not_clean_in_the_weekly_either():
    import weekly_universe as wu
    s = wu._cik_resolver_step_status({"checked": 0, "fetched_ok": False})
    assert s.startswith("failed:") and "learned nothing" in s


def test_the_step_is_wired_and_its_report_is_archived():
    """A step nobody calls is dead code; a report nobody archives grows forever."""
    import inspect

    import weekly_universe as wu
    assert "_step_resolve_cik_by_name" in inspect.getsource(wu.main)
    assert "cik_name_resolution_*.md" in wu.UNIVERSE_ARCHIVE_PATTERNS
