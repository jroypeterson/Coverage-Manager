"""Tests for the published weekly page (`reporting/weekly_page.py`).

The paragraph tests are the load-bearing ones: the first version shipped a page
where every hard-wrapped source line became its own `<p>`, which shredded each
lede and split `**bold**` across two elements so it rendered as literal
asterisks. That was found by looking at the published page, not by the markup
validator -- the HTML was perfectly well-formed and completely wrong.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from reporting import weekly_page as wp

VOID = {"meta", "br", "hr", "img", "input", "link", "col", "source"}


def _balanced(html_text: str) -> tuple[list[str], list[str]]:
    class P(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.stack: list[str] = []
            self.bad: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag not in VOID:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack:
                self.bad.append(f"extra </{tag}>")
            elif self.stack[-1] == tag:
                self.stack.pop()
            else:
                self.bad.append(f"</{tag}> closes <{self.stack[-1]}>")

    p = P()
    p.feed(html_text)
    return p.stack, p.bad


# ------------------------------------------------------------------ paragraphs


def test_soft_line_breaks_join_into_one_paragraph():
    """The live bug: a hard-wrapped lede became twelve one-line paragraphs."""
    md = "The headline is a name last week's report\nshould have caught, and it\nmatters.\n"
    out = wp._render_para(md)
    assert out.count("<p>") == 1
    assert "report should have caught, and it matters." in out


def test_bold_spanning_a_wrapped_line_still_renders():
    """`**Zhongji Innolight listed in\\nHong Kong**` rendered as literal asterisks."""
    md = "The headline is **Zhongji Innolight listed in\nHong Kong on 2026-07-30**, raised US$6.81B."
    out = wp._render_para(md)
    assert "<strong>Zhongji Innolight listed in Hong Kong on 2026-07-30</strong>" in out
    assert "**" not in out


def test_a_blank_line_does_start_a_new_paragraph():
    out = wp._render_para("First para here.\n\nSecond para here.\n")
    assert out.count("<p>") == 2


def test_bullets_render_as_a_list_and_wrapped_items_rejoin():
    md = ("- **6 symbol mismatches** (`APM`, `FI`) -- SEC carries a stale\n"
          "  symbol; these are format disagreements.\n"
          "- **15 inconclusive** for want of a CIK.\n")
    out = wp._render_para(md)
    assert out.count("<li>") == 2
    assert "stale symbol; these are format disagreements." in out


def test_prose_and_bullets_in_one_buffer_both_survive():
    out = wp._render_para("Intro line.\n\n- one\n- two\n\nOutro line.\n")
    assert out.count("<p>") == 2
    assert out.count("<li>") == 2


# ---------------------------------------------------------------------- inline


def test_html_in_the_report_is_escaped_not_injected():
    out = wp._inline('a <script>alert("x")</script> b')
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_a_code_span_is_not_re_parsed_for_emphasis():
    """`PNAQ.U` and `**` inside backticks must survive verbatim."""
    out = wp._inline("reply `add PNAQ.U` or `a**b`")
    assert '<span class="tk">add PNAQ.U</span>' in out
    assert '<span class="tk">a**b</span>' in out


def test_links_render_and_keep_their_href():
    out = wp._inline("see [the repo](https://example.com/x)")
    assert '<a href="https://example.com/x" rel="noopener">the repo</a>' in out


# ---------------------------------------------------------------------- tables


def test_table_renders_with_a_header_row():
    html_text = wp._render_table([["Ticker", "Cap"], ["FN", "~$19.5B"]])
    assert "<th>Ticker</th>" in html_text
    assert "<td>FN</td>" in html_text


def test_a_short_row_is_padded_never_truncated():
    """Dropping a cell to match the header would silently drop data."""
    html_text = wp._render_table([["A", "B", "C"], ["only-one"]])
    assert html_text.count("<td>") == 3


def test_a_long_row_keeps_every_cell():
    html_text = wp._render_table([["A", "B"], ["1", "2", "3"]])
    assert html_text.count("<td>") == 3


# --------------------------------------------------------------------- markup


def _sample_md() -> str:
    return (
        "# Weekly Coverage Universe Additions -- 2026-08-07\n\n"
        "**Review window:** 2026-07-28 -> 2026-08-07 (10 days)\n"
        "**Universe checked against:** `data/coverage_universe_tickers.csv` --- 1,328 rows\n\n"
        "## Recommendations\n\n"
        "| # | Company | Ticker |\n|---|---|---|\n| 1 | Fabrinet | `FN` |\n\n"
        "## Notes\n\n- a note that wraps\n  onto a second line\n"
    )


def test_render_produces_balanced_markup():
    page = wp.render(_sample_md(), report_date="2026-08-07", decisions=[])
    stack, bad = _balanced(page)
    assert stack == []
    assert bad == []


def test_render_carries_the_reports_own_header_facts():
    page = wp.render(_sample_md(), report_date="2026-08-07", decisions=[])
    assert "2026-07-28" in page
    assert "1,328 rows" in page


@pytest.mark.parametrize("dash", ["—", "–", "-"])
def test_the_row_count_survives_whichever_dash_the_report_used(dash):
    """The dedup deletes this line from the body, so a missed match loses it entirely."""
    md = (f"**Universe checked against:** `x.csv` {dash} 1,328 rows\n\n"
          "## Notes\n\nBody.\n")
    page = wp.render(md, report_date="2026-08-07", decisions=[])
    assert "1,328 rows" in page


def test_the_h1_is_not_duplicated_into_the_body():
    """The masthead already carries the title; a second <h1> would be a dupe."""
    page = wp.render(_sample_md(), report_date="2026-08-07", decisions=[])
    assert page.count("<h1>") == 1


def test_every_section_heading_becomes_a_nav_anchor():
    _, nav = wp.render_body(_sample_md())
    assert [t for _, t in nav] == ["Recommendations", "Notes"]


def test_an_unrecognised_section_is_rendered_not_dropped():
    md = "## Something Nobody Planned For\n\nBody text here.\n"
    body, nav = wp.render_body(md)
    assert "Body text here." in body
    assert nav == [("something-nobody-planned-for", "Something Nobody Planned For")]


def test_body_background_is_painted_from_a_token():
    """A transparent body silently borrows the host page's theme."""
    assert "background:var(--paper)" in wp._CSS


def test_dark_tokens_exist_for_both_the_media_query_and_the_stamp():
    assert '@media(prefers-color-scheme:dark){:root:not([data-theme="light"])' in wp._CSS
    assert ':root[data-theme="dark"]' in wp._CSS


# ------------------------------------------------------------------- decisions


def _dec(ticker, status, **kw):
    return wp.Decision(ticker=ticker, company=kw.get("company", ticker + " Inc."),
                       status=status, source=kw.get("source", ""),
                       sector=kw.get("sector", "Tech"),
                       market_cap=kw.get("market_cap", "~$5.0B"),
                       reason=kw.get("reason", "because."))


def test_an_open_item_gets_a_full_card_and_a_copyable_reply():
    out = wp._render_decisions([_dec("FN", "pending")])
    assert 'class="row s-open"' in out
    assert ">add FN<" in out
    assert ">add all<" in out


def test_a_settled_item_is_compressed_to_a_line_not_a_card():
    """Thirteen full cards reproduced, on the page, the wall it exists to replace."""
    out = wp._render_decisions([_dec("FN", "approved")])
    assert 'class="row s-added"' not in out
    assert '<ul class="settled">' in out


def test_an_auto_add_is_labelled_as_never_having_been_asked():
    out = wp._render_decisions([_dec("3308.HK", "approved", source="auto-add (bucket rule)")])
    assert "you were not asked" in out


def test_no_reply_chips_when_nothing_is_open():
    out = wp._render_decisions([_dec("FN", "approved")])
    assert "add all" not in out
    assert "Nothing is waiting on you" in out


def test_decision_reasons_are_escaped():
    out = wp._render_decisions([_dec("X", "pending", reason="<img onerror=1>")])
    assert "<img" not in out


def test_load_decisions_keeps_every_pending_row_whatever_its_thread():
    """A name awaiting a reply must not fall off when next week publishes."""
    rows = [
        {"ticker": "OLD", "company": "Old Co", "status": "pending",
         "slack_thread_ts": "111", "sector": "Tech", "subsector": "", "market_cap": "",
         "decision_source": "", "reason": ""},
        {"ticker": "NEW", "company": "New Co", "status": "approved",
         "slack_thread_ts": "222", "sector": "Tech", "subsector": "", "market_cap": "",
         "decision_source": "", "reason": ""},
        {"ticker": "GONE", "company": "Gone Co", "status": "approved",
         "slack_thread_ts": "999", "sector": "Tech", "subsector": "", "market_cap": "",
         "decision_source": "", "reason": ""},
    ]
    got = {d.ticker for d in wp.load_decisions("222", rows=rows)}
    assert got == {"OLD", "NEW"}          # GONE is another week's settled row


def test_open_items_sort_ahead_of_settled_ones():
    rows = [
        {"ticker": "ZZZ", "company": "Z", "status": "pending", "slack_thread_ts": "1",
         "sector": "", "subsector": "", "market_cap": "", "decision_source": "", "reason": ""},
        {"ticker": "AAA", "company": "A", "status": "approved", "slack_thread_ts": "1",
         "sector": "", "subsector": "", "market_cap": "", "decision_source": "", "reason": ""},
    ]
    assert [d.ticker for d in wp.load_decisions("1", rows=rows)] == ["ZZZ", "AAA"]


@pytest.mark.parametrize("raw,expected", [
    ("167000000000", "~$167.0B"),
    ("465000000", "~$465M"),
    ("3280000000000", "~$3.28T"),
    ("~$5.3B", "~$5.3B"),          # hand-written values pass through
    ("", ""),
])
def test_market_cap_renders_from_either_a_number_or_prose(raw, expected):
    assert wp._cap(raw) == expected


# ---------------------------------------------------------------------- files


def test_publish_writes_index_dated_copy_and_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "load_decisions", lambda ts, rows=None: [])
    result = wp.publish(_sample_md(), report_date="2026-08-07", docs_dir=tmp_path)
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "weekly" / "2026-08-07.html").exists()
    assert (tmp_path / "archive.html").exists()
    assert result["archived"] == 1
    # index and the dated copy are the same page, so a link never 404s on content
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == \
           (tmp_path / "weekly" / "2026-08-07.html").read_text(encoding="utf-8")


def test_publish_writes_lf_so_the_page_is_not_dirty_every_week(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "load_decisions", lambda ts, rows=None: [])
    wp.publish(_sample_md(), report_date="2026-08-07", docs_dir=tmp_path)
    assert b"\r\n" not in (tmp_path / "index.html").read_bytes()


def test_archive_lists_newest_first():
    out = wp.render_archive(["2026-07-31", "2026-08-07", "2026-07-24"])
    order = re.findall(r"weekly/(\d{4}-\d{2}-\d{2})\.html", out)
    assert order == ["2026-08-07", "2026-07-31", "2026-07-24"]


def test_find_report_prefers_the_live_copy_over_the_archived_one(tmp_path, monkeypatch):
    """The weekly run archives, so the same date can exist in both directories."""
    live, old = tmp_path, tmp_path / "old reports"
    old.mkdir()
    name = "weekly_coverage_universe_additions_2026-08-07.md"
    (live / name).write_text("live", encoding="utf-8")
    (old / name).write_text("archived", encoding="utf-8")
    monkeypatch.setattr("config.REPORTS_DIR", tmp_path)
    path, stamp = wp.find_report("2026-08-07")
    assert stamp == "2026-08-07"
    assert path.read_text(encoding="utf-8") == "live"


def test_find_report_reaches_into_the_archive_when_that_is_all_there_is(tmp_path, monkeypatch):
    old = tmp_path / "old reports"
    old.mkdir()
    (old / "weekly_coverage_universe_additions_2026-07-31.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr("config.REPORTS_DIR", tmp_path)
    _, stamp = wp.find_report()
    assert stamp == "2026-07-31"


def test_find_report_names_what_it_has_when_the_date_is_missing(tmp_path, monkeypatch):
    (tmp_path / "weekly_coverage_universe_additions_2026-07-31.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr("config.REPORTS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="2026-07-31"):
        wp.find_report("2026-08-07")


def test_the_published_page_is_balanced_on_the_real_report():
    """Run against whatever report is actually on disk, not a fixture."""
    try:
        path, stamp = wp.find_report()
    except FileNotFoundError:
        pytest.skip("no weekly report on disk")
    page = wp.render(path.read_text(encoding="utf-8", errors="replace"),
                     report_date=stamp, decisions=[])
    stack, bad = _balanced(page)
    assert stack == []
    assert bad == []
    assert "**" not in re.sub(r"<style>.*?</style>", "", page, flags=re.S)


# ------------------------------------------------------------------ dedup + step


def test_the_reports_header_block_is_not_repeated_in_the_body():
    """The masthead already shows the window and row count."""
    md = ("**Review window:** 2026-07-28 -> 2026-08-07\n"
          "**Universe checked against:** x --- 1,328 rows\n\n"
          "## Recommendations\n\nBody.\n")
    body, _ = wp.render_body(md)
    assert "Review window" not in body
    assert "Body." in body


def test_the_same_phrase_inside_a_section_is_kept():
    """Only the copy BEFORE the first H2 is a duplicate of the masthead."""
    md = "## Notes\n\n**Review window:** was extended this week.\n"
    body, _ = wp.render_body(md)
    assert "was extended this week." in body


def test_weekly_page_step_reports_why_it_published_nothing(tmp_path, monkeypatch):
    import weekly_universe as wu
    monkeypatch.setattr("config.REPORTS_DIR", tmp_path)
    result = wu._step_weekly_page()
    assert "skipped" in result


# ------------------------------------------------------------- wide tables


def test_a_narrow_table_stays_a_grid():
    out = wp._render_table([["Ticker", "Cap"], ["FN", "~$19.5B"]])
    assert "<table>" in out


def test_a_wide_table_becomes_cards_not_a_squeezed_grid():
    """11 columns with a paragraph in the last renders 4 words per line as a grid."""
    head = ["#", "Company", "Ticker", "Exchange", "Market Cap", "Sector",
            "Subsector", "Listing Date", "Trigger", "Peers", "Reason to add"]
    row = ["1", "Jersey Mike's Subs Inc.", "JMKE", "NYSE", "~$5.3B", "Consumer",
           "Restaurants", "2026-07-30", "IPO", "WMT, LULU", "A " + "long " * 40]
    out = wp._render_table([head, row])
    assert "<table>" not in out
    assert '<h4>' in out
    assert "Jersey Mike's Subs Inc." in out


def test_a_card_puts_the_long_prose_cell_in_prose_not_a_field():
    head = ["Company", "Ticker", "Exchange", "Sector", "Subsector", "Trigger", "Why"]
    row = ["Fabrinet", "FN", "NYSE", "Tech", "Optical", "New candidate", "B " + "word " * 40]
    out = wp._render_table([head, row])
    assert '<p class="row-why">' in out
    assert "Optical" in out


def test_a_card_drops_placeholder_dashes_but_keeps_real_values():
    head = ["Company", "Ticker", "Exchange", "Listing Date", "Sector", "Subsector", "Why"]
    row = ["Fabrinet", "FN", "NYSE", "-", "Tech", "Optical", "C " + "word " * 40]
    out = wp._render_table([head, row])
    assert "Listing Date" not in out
    assert "NYSE" in out


def test_no_cell_is_lost_when_a_wide_row_becomes_a_card():
    head = ["Company", "Ticker", "Exchange", "Sector", "Subsector", "Trigger", "Peers"]
    row = ["Fabrinet", "FN", "NYSE", "Tech", "Optical", "New candidate", "TSM, NVDA"]
    out = wp._render_table([head, row])
    for value in row:
        assert value in out


def test_the_card_headline_is_the_company_not_the_peers_list():
    """Live bug: "widest short cell" picked the peer list over the company name."""
    head = ["#", "Company", "Ticker", "Exchange", "Market Cap", "Sector",
            "Subsector", "Listing Date", "Trigger", "Peers in sheet", "Reason to add"]
    row = ["1", "Jersey Mike's Subs Inc.", "JMKE", "NYSE", "~$5.3B", "Consumer",
           "Restaurants", "2026-07-30", "IPO", "WMT, LULU, CROX, FIVE, CVNA",
           "D " + "word " * 40]
    out = wp._render_table([head, row])
    assert "<h4>Jersey Mike&#x27;s Subs Inc.</h4>" in out or            "<h4>Jersey Mike's Subs Inc.</h4>" in out


def test_the_headline_falls_back_when_no_column_names_the_record():
    head = ["A", "B", "C", "D", "E", "F", "G"]
    row = ["1", "A much longer descriptive label", "x", "y", "z", "w", "v"]
    out = wp._render_table([head, row])
    assert "<h4>A much longer descriptive label</h4>" in out
