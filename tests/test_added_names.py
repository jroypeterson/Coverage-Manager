"""Tests for the per-name business summaries in the Slack lead.

The lead used to name five tickers and their buckets and say nothing about what
any of them sold. These pin the ways a summary could come out empty, wrong-length
or silently missing -- a blank line under a company name reads as "no business
here" rather than "nobody wrote one".
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reporting import added_names as an  # noqa: E402

LEDGER_HEAD = ("ticker,company,exchange,market_cap,sector,subsector,trigger,"
               "first_proposed,pending_since,last_seen,status,decision_date,"
               "decision_source,slack_thread_ts,reason,notes\n")


def _root(tmp_path, rows):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "candidate_ledger.csv").write_text(
        LEDGER_HEAD + rows, encoding="utf-8")
    return tmp_path


def _row(ticker, company, status="approved", decided="2026-09-02",
         cap="2000000000", notes=""):
    # The company field is quoted: real names carry commas ("Liftoff Mobile,
    # Inc."), and an unquoted one silently shifts every later column.
    return (f'{ticker},"{company}",Nasdaq,{cap},Tech,Software,IPO,{decided},,,'
            f'{status},{decided},auto,,,"{notes}"\n')


BRIEF = """# Company Background Briefings

### LIFTOFF MOBILE, INC. (LFTO) — Quick Background
**Sector / Industry:** Tech

#### 1. Business Description

We are the toll booth between mobile apps that want users and mobile apps that
want revenue, and we take a cut of everything that crosses.

A second paragraph that should not appear in a Slack lead.

#### 2. Financial Snapshot
Numbers here.
"""


# ------------------------------------------------------------------- sourcing


def test_the_briefing_is_preferred_over_the_ledger_note(tmp_path):
    root = _root(tmp_path, _row("LFTO", "Liftoff Mobile, Inc.", notes="a ledger note"))
    out = an.collect("2026-09-04", root=root, briefings_md=BRIEF)
    assert out[0].source == "briefing"
    assert out[0].summary.startswith("We are the toll booth")


def test_only_the_first_paragraph_of_the_description_is_used(tmp_path):
    root = _root(tmp_path, _row("LFTO", "Liftoff Mobile, Inc."))
    out = an.collect("2026-09-04", root=root, briefings_md=BRIEF)
    assert "second paragraph" not in out[0].summary
    assert "Financial Snapshot" not in out[0].summary


def test_the_ledger_note_is_the_fallback_when_there_is_no_briefing(tmp_path):
    root = _root(tmp_path, _row("XYZ", "Example Corp", notes="Makes widgets."))
    out = an.collect("2026-09-04", root=root, briefings_md=BRIEF)
    assert out[0].source == "ledger" and out[0].summary == "Makes widgets."


def test_a_name_with_neither_says_so_rather_than_rendering_blank(tmp_path):
    root = _root(tmp_path, _row("XYZ", "Example Corp"))
    out = an.collect("2026-09-04", root=root, briefings_md="")
    assert out[0].source == "none"
    assert "No briefing" in out[0].summary


def test_a_briefing_is_matched_by_company_name_when_the_ticker_differs(tmp_path):
    """Foreign tickers in the ledger (`0625.HK`) do not always match the
    briefing heading's parenthesised symbol."""
    brief = BRIEF.replace("(LFTO)", "(LFTO.XX)")
    root = _root(tmp_path, _row("LFTO", "LIFTOFF MOBILE, INC."))
    out = an.collect("2026-09-04", root=root, briefings_md=brief)
    assert out[0].source == "briefing"


# -------------------------------------------------------------------- shaping


def test_a_one_line_hook_pulls_in_the_next_sentence(tmp_path):
    """'We make the metal that has to not fail.' is a hook, not a description,
    and it is its own paragraph -- so the paragraph break cannot end the take."""
    brief = ("### APPLIED (AADX) — Quick Background\n\n"
             "#### 1. Business Description\n\n"
             "We make the metal that has to not fail.\n\n"
             "We design and manufacture structures for space launch.\n")
    root = _root(tmp_path, _row("AADX", "Applied"))
    out = an.collect("2026-09-04", root=root, briefings_md=brief)
    assert "space launch" in out[0].summary


def test_a_long_summary_is_truncated_on_a_word_boundary(tmp_path):
    brief = ("### LONG (LNG) — Quick Background\n\n#### 1. Business Description\n\n"
             + "word " * 200 + "\n")
    root = _root(tmp_path, _row("LNG", "Long Co"))
    out = an.collect("2026-09-04", root=root, briefings_md=brief)
    assert len(out[0].summary) <= an.SUMMARY_MAX + 3
    assert out[0].summary.endswith("...")


def test_a_raw_provider_float_is_formatted_not_printed(tmp_path):
    """`auto_add` writes a bare float; printing it gave `26400000000` in a lead."""
    root = _root(tmp_path, _row("SHE", "Shein", cap="26400000000"))
    out = an.collect("2026-09-04", root=root, briefings_md="")
    assert "~$26.4B" in out[0].label()


def test_an_already_formatted_cap_is_left_alone(tmp_path):
    root = _root(tmp_path, _row("BSP", "Bending Spoons", cap="~$22.8B"))
    out = an.collect("2026-09-04", root=root, briefings_md="")
    assert "~$22.8B" in out[0].label()


# ------------------------------------------------------------------ selection


def test_only_approved_names_in_the_window_are_included(tmp_path):
    root = _root(tmp_path,
                 _row("AAA", "Approved In Window")
                 + _row("BBB", "Pending", status="pending")
                 + _row("CCC", "Approved Last Week", decided="2026-08-20")
                 + _row("DDD", "Boundary Day", decided="2026-08-28"))
    out = an.collect("2026-09-04", root=root, briefings_md="")
    assert [a.company for a in out] == ["Approved In Window"]


def test_no_adds_renders_nothing_at_all():
    assert an.as_markdown([]) == ""


def test_the_markdown_leads_with_the_business_not_the_bucket(tmp_path):
    root = _root(tmp_path, _row("LFTO", "Liftoff Mobile, Inc."))
    md = an.as_markdown(an.collect("2026-09-04", root=root, briefings_md=BRIEF))
    assert "what each one does" in md
    assert md.index("toll booth") > md.index("Liftoff Mobile")


# ------------------------------------------- shaping fixes from the 09-06 review


def test_a_bolded_opening_sentence_splits_at_its_full_stop(tmp_path):
    """SSMR's briefing opens '**This is a pre-revenue company...**' and the
    closing ** sat between the full stop and the space, so the whole 900-char
    paragraph was one 'sentence' and the 240-char cut landed mid-clause."""
    brief = ("### SUNSHINE (SSMR) — Quick Background\n\n"
             "#### 1. Business Description\n\n"
             "**This is a pre-revenue company, and that is the first thing to "
             "say about it.** We own the Sunshine Mine in Idaho, historically "
             "one of the highest-grade primary silver deposits in the world, "
             "and idle for decades, and we intend to restart it in 2028 after "
             "a capital programme that is not yet fully funded.\n")
    root = _root(tmp_path, _row("SSMR", "Sunshine Silver"))
    out = an.collect("2026-09-04", root=root, briefings_md=brief)
    assert out[0].summary.endswith("say about it.**")
    assert not out[0].summary.endswith("...")


def test_a_long_second_sentence_is_not_pulled_in_only_to_be_chopped(tmp_path):
    """AADX rendered '...survive launch loads, high-g' -- the <90-char hook rule
    pulled a 200-char sentence in and the cut did the work, which reads as a
    truncation bug rather than a summary."""
    brief = ("### APPLIED (AADX) — Quick Background\n\n"
             "#### 1. Business Description\n\n"
             "We make the metal that has to not fail.\n\n"
             + "We design and engineer structures for space launch " * 6 + "\n")
    root = _root(tmp_path, _row("AADX", "Applied Aerospace"))
    out = an.collect("2026-09-04", root=root, briefings_md=brief)
    assert out[0].summary == "We make the metal that has to not fail."


def test_every_summary_is_labelled_as_a_description(tmp_path):
    """First person under a bold company name reads as a quote from the company
    or as this firm; neither is true in a bot-authored Slack message."""
    root = _root(tmp_path, _row("LFTO", "Liftoff Mobile, Inc."))
    md = an.as_markdown(an.collect("2026-09-04", root=root, briefings_md=BRIEF))
    assert "_What it does:_ We are the toll booth" in md


# ------------------------------------------------ degraded, not empty (Codex)


def test_an_unreadable_ledger_raises_rather_than_reading_as_no_adds(tmp_path):
    """Returning [] is indistinguishable from 'nothing was added this week', so
    an unreadable ledger silently produced a post with no summaries and exit 0."""
    import pytest as _pytest
    (tmp_path / "data").mkdir()
    # A directory where the CSV should be: open() raises on every platform.
    (tmp_path / "data" / "candidate_ledger.csv").mkdir()
    with _pytest.raises(an.LedgerUnreadable):
        an.collect("2026-09-04", root=tmp_path)


def test_a_missing_ledger_is_still_simply_no_adds(tmp_path):
    assert an.collect("2026-09-04", root=tmp_path) == []
