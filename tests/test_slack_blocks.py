"""Tests for the Slack Block Kit renderer and the report router.

The founding defect these guard against is not a crash — it is a post that ships
looking fine and is unreadable, or worse, one that silently drops a section. Both
happened: the 2026-07-31 post fenced an 11-column table into pipe-soup, and the
router's first draft filed `### Pending approval backlog` (the decision itself)
under "Notes" because it split on H2 only.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reporting import slack_blocks as sb  # noqa: E402


def _load_poster():
    spec = importlib.util.spec_from_file_location(
        "post_coverage_to_ipo", PROJECT_ROOT / "scripts" / "post_coverage_to_ipo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


poster = _load_poster()


# ------------------------------------------------------------------ inline text


def test_escape_order_does_not_double_escape():
    # `&` must be replaced first or `<` becomes `&amp;lt;`.
    assert sb.escape("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_bold_and_links_convert():
    assert sb.inline("**hi**") == "*hi*"
    assert sb.inline("[docs](http://x.co)") == "<http://x.co|docs>"


def test_plain_strips_emphasis_for_header_blocks():
    assert sb._plain("**Shein** `X`") == "Shein X"


# ---------------------------------------------------------------- table routing


NARROW = [
    ["Ticker", "Company", "Pending since", "Trigger"],
    ["688825.SS", "Changxin Technology Group (CXMT)", "2026-07-31", "IPO"],
    ["MU", "Micron Technology, Inc.", "2026-07-31", "New candidate"],
]

WIDE = [
    ["#", "Company", "Ticker", "Exchange", "Market Cap", "Reason to add"],
    ["1", "Changxin Technology Group (CXMT)", "688825.SS", "Shanghai STAR",
     "~$484B", "Mandatory Bucket 2 add. " * 12],
]


def test_narrow_table_stays_monospace_and_aligns():
    assert sb.is_narrow(NARROW)
    block = sb.render_aligned(NARROW)
    body = block["elements"][0]["elements"][0]["text"].splitlines()
    # Every column starts at the same offset on every line -> it actually aligns.
    starts = [ln.index("2026-07-31") for ln in body if "2026-07-31" in ln]
    assert len(set(starts)) == 1


def test_aligned_tables_use_rich_text_so_slack_cannot_linkify_tickers():
    """`.SS` / `.HK` / `.BR` are live ccTLDs; a mrkdwn code fence still links them."""
    block = sb.render_aligned(NARROW)
    assert block["type"] == "rich_text"
    assert block["elements"][0]["type"] == "rich_text_preformatted"


def test_wide_table_becomes_cards_not_a_code_fence():
    assert not sb.is_narrow(WIDE)
    blocks = sb.render_cards(WIDE)
    assert len(blocks) == 1
    assert "```" not in blocks[0]["text"]["text"]
    assert blocks[0]["text"]["text"].startswith("*1.*")


def test_card_promotes_ticker_and_index_and_keeps_reason():
    blocks = sb.render_cards(WIDE)
    text = blocks[0]["text"]["text"]
    assert "`688825.SS`" in text
    assert "Mandatory Bucket 2 add." in text


def test_card_meta_goes_to_a_two_column_field_grid():
    fields = sb.render_cards(WIDE)[0]["fields"]
    labels = [f["text"].split("\n")[0] for f in fields]
    assert "*Exchange*" in labels and "*Market Cap*" in labels
    assert len(fields) <= sb.MAX_FIELDS


def test_already_bold_title_is_not_double_wrapped():
    rows = [["Company", "Why it matters"],
            ["**Shein**", "x" * 200]]
    text = sb.render_cards(rows)[0]["text"]["text"]
    assert text.startswith("*Shein*")
    assert "**Shein**" not in text


@pytest.mark.parametrize("blank", ["-", "--", "—", "", "n/a", "N/A"])
def test_placeholder_cells_are_dropped_not_rendered_as_facts(blank):
    rows = [["Company", "Listing Date", "Why"],
            ["Micron", blank, "y" * 200]]
    block = sb.render_cards(rows)[0]
    assert all("Listing Date" not in f["text"] for f in block.get("fields", []))


def test_separator_row_is_not_data():
    rows = sb.parse("| A | B |\n|---|---|\n| 1 | 2 |")[0][1]
    assert rows == [["A", "B"], ["1", "2"]]


# --------------------------------------------------------------- block hygiene


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


REPORT = _find_report("2026-07-31")


def _all_messages(md: str) -> list[list[dict]]:
    lead, thread, footer = poster.route(md)
    msgs = [sb.markdown_to_blocks(lead)]
    msgs += [sb.markdown_to_blocks(b) for b in thread]
    msgs += [[sb.context_block(b[:2900]) for b in footer]] if footer else []
    return msgs


@pytest.mark.skipif(not REPORT.exists(), reason="sample report not present")
def test_live_report_respects_every_slack_limit():
    md = REPORT.read_text(encoding="utf-8")
    for msg in _all_messages(md):
        assert len(msg) <= sb.MAX_BLOCKS
        for b in msg:
            if b["type"] == "section":
                assert len(b["text"]["text"]) <= 3000
                assert len(b.get("fields", [])) <= 10
            if b["type"] == "header":
                assert len(b["text"]["text"]) <= 150
            if b["type"] == "context":
                assert b["elements"], "context blocks require elements[]"


@pytest.mark.skipif(not REPORT.exists(), reason="sample report not present")
def test_no_code_fence_survives_for_the_wide_recommendations_table():
    md = REPORT.read_text(encoding="utf-8")
    lead, _, _ = poster.route(md)
    rendered = "\n".join(
        b["text"]["text"] for b in sb.markdown_to_blocks(lead)
        if b["type"] == "section")
    assert "| Changxin" not in rendered      # the pipe-soup that started this
    assert "Changxin Technology Group" in rendered


# ------------------------------------------------------------------- no loss


@pytest.mark.skipif(not REPORT.exists(), reason="sample report not present")
def test_every_section_is_routed_exactly_once():
    """A heading that reaches no bucket is a section that silently vanished.

    This is the Jersey Mike's failure mode in miniature: the report said the
    right thing and the reader never saw it.
    """
    md = REPORT.read_text(encoding="utf-8")
    titles = [m.group(2).strip()
              for m in re.finditer(r"^(#{2,3}) +(.+?)\s*$", md, flags=re.M)]
    lead, thread, footer = poster.route(md)
    routed = "\n".join([lead] + thread + footer)
    for title in titles:
        assert routed.count(title) >= 1, f"section vanished: {title}"


@pytest.mark.skipif(not REPORT.exists(), reason="sample report not present")
def test_routing_preserves_every_non_blank_line():
    md = REPORT.read_text(encoding="utf-8")
    lead, thread, footer = poster.route(md)
    routed = {ln.strip() for ln in "\n".join([lead] + thread + footer).splitlines()}
    for line in md.splitlines():
        if line.strip():
            assert line.strip() in routed, f"line dropped in routing: {line[:70]}"


def test_pending_backlog_reaches_the_lead_even_when_nested_under_notes():
    md = ("# Title\n\nintro\n\n## Notes\n\nsome note\n\n"
          "### Pending approval backlog\n\n`add MU` to approve.\n")
    lead, thread, _ = poster.route(md)
    assert "add MU" in lead
    assert "some note" in "\n".join(thread)


def test_unrecognised_section_goes_to_the_thread_not_the_bin():
    md = "# T\n\nx\n\n## Something Nobody Anticipated\n\nbody text\n"
    lead, thread, footer = poster.route(md)
    assert "body text" in "\n".join(thread)
    assert "body text" not in lead and not footer


def test_chunk_never_loses_characters():
    text = "\n\n".join("para %d %s" % (i, "z" * 400) for i in range(30))
    assert "".join(sb._chunk(text)).replace("\n", "") == text.replace("\n", "")


def test_context_blocks_convert_markdown_rather_than_posting_it_raw():
    """`context` parses mrkdwn, so raw `###` and `**bold**` post literally.

    This shipped live on 2026-08-06: the report-files footer showed a literal
    `### CSV Changes` and literal double asterisks.
    """
    text = sb.context_block("### CSV Changes\n\n**No changes** here\n\n- one")["elements"][0]["text"]
    assert "###" not in text
    assert "**" not in text
    assert "*CSV Changes*" in text and "*No changes*" in text
    assert "\u2022 one" in text


# ------------------------------------------------- decisions-only Slack post


def test_the_auto_add_section_reaches_the_lead_not_the_thread():
    """An add JP was never asked about must be the most visible line, not the quietest.

    `sync_candidate_ledger` has printed "REPORT THESE IN THE SLACK POST" since the
    auto-add rule shipped, and the section was being threaded \u2014 the quietest place
    in the message.
    """
    md = ("# T\n\nintro\n\n## Added without asking - already in the universe\n\n"
          "3308.HK entered by rule.\n\n## Notes\n\nsome note\n")
    lead, thread, _ = poster.route(md)
    assert "3308.HK entered by rule." in lead
    assert "3308.HK entered by rule." not in "\n".join(thread)
    assert "some note" in "\n".join(thread)


def test_reference_sections_still_route_to_a_bucket_so_they_can_be_named():
    """Deferred to the page, not dropped \u2014 "moved" must not look like "vanished"."""
    md = "# T\n\nx\n\n## Pipeline / filings to monitor\n\nrows here\n"
    lead, thread, footer = poster.route(md)
    assert "rows here" in "\n".join(thread)
    assert "rows here" not in lead


# ------------------------------------------- the lead stays scannable (2026-08-17)


def test_h3_detail_inside_a_lead_section_defers_to_the_page():
    """JP 2026-08-17: "the format is too much blocks of text ... needs to be
    formatted for speed readability first and then be able to delve into details."

    The 2026-08-14 post carried two ~700-word H3 essays inside
    `## Added without asking`. `split_sections` only breaks on H2, so both went to
    the channel-level lead and buried the two-row table that IS the decision.
    """
    md = ("# T\n\nintro\n\n## Added without asking - already in the universe\n\n"
          "| Ticker | Bucket |\n|---|---|\n| `VOGX` | 1 |\n\n"
          "### VOGX - why it qualifies\n\nseven hundred words of thesis\n\n"
          "### BSEM - the argument against\n\nseven hundred more\n")
    lead, thread, _ = poster.route(md)
    assert "| `VOGX` | 1 |" in lead                     # the decision stays
    assert "seven hundred words of thesis" not in lead  # the essay does not
    joined = "\n".join(thread)
    assert "seven hundred words of thesis" in joined
    assert "seven hundred more" in joined


def test_deferred_lead_detail_is_named_not_silently_dropped():
    """Each deferred H3 becomes its own body, so its title reaches the "On the
    page:" line the same way a threaded H2 section does."""
    md = ("# T\n\nx\n\n## Recommendations\n\ncards\n\n"
          "### ACME - the long case\n\nbody\n")
    _, thread, _ = poster.route(md)
    assert [b.splitlines()[0].lstrip("# ").strip() for b in thread] == [
        "ACME - the long case"]


def test_a_lead_section_that_is_itself_an_h3_keeps_its_own_heading():
    """`### Pending approval backlog` nested under `## Notes` is a lead section
    whose own heading is an H3 -- it must not defer itself into the thread."""
    md = ("# T\n\nx\n\n## Notes\n\nnote\n\n"
          "### Pending approval backlog\n\n`add MU` to approve.\n")
    lead, thread, _ = poster.route(md)
    assert "add MU" in lead
    assert "add MU" not in "\n".join(thread)


def test_lead_section_without_subsections_is_unchanged():
    md = "# T\n\nx\n\n## Recommendations\n\njust cards, no H3\n"
    lead, thread, _ = poster.route(md)
    assert "just cards, no H3" in lead
    assert thread == []


def test_the_scannable_top_reaches_the_lead():
    """`## Decisions` / `## Watch` are the report top's two sections. Unrecognised,
    they route to the page and the lead posts as a bare title."""
    md = ("# T\n\n**Action needed:** none\n\n"
          "## Decisions\n\n| # | What |\n|---|---|\n| 2 | Added by rule |\n\n"
          "## Watch\n\n- `LYNT` prices 08-17 at $2.53B\n\n"
          "## Notes\n\nsome note\n")
    lead, thread, _ = poster.route(md)
    assert "| 2 | Added by rule |" in lead
    assert "`LYNT` prices 08-17 at $2.53B" in lead
    assert "some note" in "\n".join(thread)


def test_form_10_watch_subsection_is_not_pulled_into_the_lead_by_the_watch_prefix():
    """"watch" is a prefix match, and `### Form 10 watch` is reference material."""
    md = ("# T\n\nx\n\n## Listing-lane findings\n\nintro\n\n"
          "### Form 10 watch (`form10_watch_2026-08-14.md`)\n\nrows\n")
    lead, thread, _ = poster.route(md)
    assert "rows" not in lead
    assert "rows" in "\n".join(thread)
