"""The ISIN identity audit is on the weekly cadence (2026-07-29)."""
import weekly_universe as wu


def test_step_status_reports_counted_classes_not_a_boolean():
    s = wu._isin_identity_step_status(
        {"checked": 792, "ok": 758, "conflicts": [{}] * 4, "inconclusive": [{}] * 30})
    assert s.startswith("failed:")
    assert "4 conflict(s)" in s and "30 inconclusive" in s and "758 ok" in s
    assert "yes" not in s.lower()


def test_a_clean_audit_is_ok():
    s = wu._isin_identity_step_status(
        {"checked": 792, "ok": 792, "conflicts": [], "inconclusive": []})
    assert s.startswith("ok") and "failed" not in s


def test_inconclusive_only_is_ok_but_says_so():
    s = wu._isin_identity_step_status(
        {"checked": 792, "ok": 762, "conflicts": [], "inconclusive": [{}] * 30})
    assert s.startswith("ok") and "inconclusive" in s


def test_an_audit_that_learned_NOTHING_is_not_clean():
    """All-inconclusive (OpenFIGI unreachable) must never read as agreement —
    the delisted_check rule, applied here."""
    s = wu._isin_identity_step_status(
        {"checked": 792, "ok": 0, "conflicts": [], "inconclusive": [{}] * 792})
    assert s.startswith("failed:") and "learned nothing" in s


def test_status_string_is_ascii_and_carries_no_company_names():
    """It reaches a cp1252 console mid-run; a non-ASCII company name has killed
    this fleet's runs twice."""
    s = wu._isin_identity_step_status(
        {"checked": 5, "ok": 4,
         "conflicts": [{"ticker": "DIA.MI", "company": "DiaSorin S.p.A."}],
         "inconclusive": []})
    s.encode("ascii", "strict")
    assert "DiaSorin" not in s and "DIA.MI" not in s


def test_the_step_is_actually_wired_into_the_weekly():
    """A step function nobody calls is dead code, and the whole point was moving
    this off run-on-demand."""
    import inspect
    src = inspect.getsource(wu.main)
    assert "_step_verify_isin_issuers" in src
    assert "verify_isin_issuers" in src
