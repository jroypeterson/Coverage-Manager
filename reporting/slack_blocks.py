"""Render the weekly coverage report's markdown as Slack Block Kit.

**Why this module exists.** `post_coverage_to_ipo` used to wrap every markdown
table in a ``` fence, on the theory that monospacing preserves the columns. It
does not. The recommendations table is **11 columns wide and its last column is a
paragraph**; Slack wraps a code block at roughly 90 characters with no horizontal
scroll, so the 2026-07-31 post rendered as pipe-soup — column headers on one line,
a company name three lines below the row it belongs to, and `**bold**` showing as
literal asterisks because the table path skipped inline conversion entirely.

The fix is to stop pretending Slack has tables. It has two things that read well:
short aligned monospace, and prose. So each table is routed by shape:

| Shape | Rendering | Why |
|---|---|---|
| narrow (<=5 cols, <=34-char cells, <=80 total) | aligned monospace block | genuinely tabular; columns line up and stay lined up |
| anything wider | one **card** per row | a row whose last cell is a paragraph is a record, not a row |

A card promotes the row's identifying column to a bold headline, collapses the
short columns into a single middot-separated meta line, and lets the long columns
be what they already are — prose. Nothing is dropped; the same cells appear, in
the same order, in a shape Slack can lay out.

`markdown_to_blocks` never returns more than `MAX_BLOCKS` blocks or a section over
`MRKDWN_LIMIT` characters, because both are hard Slack API limits that fail the
whole `chat.postMessage` call rather than degrading — and a failed post makes the
week's report invisible, which is the failure this whole lane was built to end.
"""
from __future__ import annotations

import re

# Slack hard limits, with headroom. Exceeding any of these fails the API call.
MRKDWN_LIMIT = 2900        # section text cap is 3,000
HEADER_LIMIT = 145         # header plain_text cap is 150
MAX_BLOCKS = 45            # blocks-per-message cap is 50

# Table-shape thresholds. Tuned against the live 2026-07-31 report: the pending
# backlog table (4 cols / 72 chars) stays tabular, the recommendations table
# (11 cols, a 300-char reason cell) becomes cards.
NARROW_MAX_COLS = 6
NARROW_MAX_CELL = 36
NARROW_MAX_WIDTH = 88

# Column-role detection for card rendering.
INDEX_HEADERS = {"#", "no", "no.", "rank", "n"}
TICKER_HEADERS = {"ticker", "symbol"}
META_MAX = 40              # a cell this short is a fact, not a paragraph


# ------------------------------------------------------------------ inline text


def escape(text: str) -> str:
    """Escape the three characters Slack's mrkdwn parser reserves.

    Order matters: `&` first, or the ampersands introduced by the `<`/`>`
    replacements get double-escaped into `&amp;lt;`.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(text: str) -> str:
    """Markdown inline -> Slack mrkdwn. Assumes `text` is not yet escaped."""
    text = escape(text)
    # Links first: they are the only construct that reintroduces < >.
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"<\2|\1>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)      # **bold** -> *bold*
    text = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"*\1*", text)
    return text


def _plain(text: str) -> str:
    """Strip markdown emphasis for `header` blocks, which take plain_text only."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    return text.strip()


# ---------------------------------------------------------------------- parsing


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and len(s) > 1


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-+:?", c.strip()) for c in cells)


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse(md: str) -> list[tuple[str, object]]:
    """Markdown -> [(kind, payload)] where kind is heading/para/table/rule/code.

    Deliberately small: this parses the shapes the weekly report actually uses.
    Anything unrecognised falls through to `para` and is rendered as text — the
    module never drops input it does not understand.
    """
    nodes: list[tuple[str, object]] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            text = "\n".join(buf).strip()
            if text:
                nodes.append(("para", text))
            buf.clear()

    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):                    # passthrough code fence
            flush()
            fence = [line]
            i += 1
            while i < len(lines):
                fence.append(lines[i])
                if lines[i].strip().startswith("```"):
                    i += 1
                    break
                i += 1
            nodes.append(("code", "\n".join(fence)))
            continue

        if _is_table_row(stripped):
            flush()
            rows: list[list[str]] = []
            while i < len(lines) and _is_table_row(lines[i]):
                cells = _split_row(lines[i])
                if not _is_separator_row(cells):
                    rows.append(cells)
                i += 1
            if rows:
                nodes.append(("table", rows))
            continue

        m = re.fullmatch(r"(#{1,6})\s+(.*)", stripped)
        if m:
            flush()
            nodes.append(("heading", (len(m.group(1)), m.group(2).strip())))
            i += 1
            continue

        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush()
            nodes.append(("rule", None))
            i += 1
            continue

        buf.append(line)
        i += 1

    flush()
    return nodes


# ------------------------------------------------------------------ table shape


def _column_widths(rows: list[list[str]]) -> list[int]:
    ncols = max(len(r) for r in rows)
    widths = [0] * ncols
    for row in rows:
        for j, cell in enumerate(row):
            widths[j] = max(widths[j], len(_plain(cell)))
    return widths


def is_narrow(rows: list[list[str]]) -> bool:
    """True when the table is small enough that monospace columns still line up."""
    if not rows:
        return False
    widths = _column_widths(rows)
    if len(widths) > NARROW_MAX_COLS:
        return False
    if max(widths) > NARROW_MAX_CELL:
        return False
    total = sum(widths) + 2 * (len(widths) - 1)
    return total <= NARROW_MAX_WIDTH


def preformatted(text: str) -> dict:
    """A monospace block that Slack will NOT linkify.

    Measured 2026-08-05: Slack auto-links anything that looks like a domain, and
    several exchange suffixes are live ccTLDs — `.SS` is South Sudan, `.HK` Hong
    Kong, `.BR` Brazil, `.PA` Panama. So `688825.SS` came back as
    `<http://688825.SS|688825.SS>` **even inside backticks and inside a ``` fence**,
    and `parse: "none"` does not suppress it for `blocks` (both tested live).
    `rich_text` is the one block type Slack leaves literal — verified: even a bare
    `2475.HK` in a rich_text element stays a `text` element, never a `link`.

    Used for the aligned tables, where a blue underlined ticker inside a column of
    data is the most jarring. Card headlines stay `section` blocks because the
    two-column `fields` grid has no rich_text equivalent, so a ccTLD-suffixed
    foreign ticker still renders as a dead link there. That is a deliberate,
    documented trade: density over a cosmetic blemish on the minority of rows.
    """
    return {"type": "rich_text", "elements": [
        {"type": "rich_text_preformatted",
         "elements": [{"type": "text", "text": text}]}]}


def render_aligned(rows: list[list[str]]) -> dict:
    """Pad a narrow table into an aligned monospace block.

    The source markdown is not padded (`| 1 | Changxin |`), so fencing it raw
    produced ragged pipes even when the table *was* narrow enough to fit.
    """
    widths = _column_widths(rows)

    def line(cells: list[str]) -> str:
        padded = [_plain(c).ljust(widths[j]) for j, c in enumerate(cells)]
        padded += [" " * widths[j] for j in range(len(cells), len(widths))]
        return "  ".join(padded).rstrip()

    out = [line(rows[0]), "  ".join("-" * w for w in widths)]
    out += [line(r) for r in rows[1:]]
    return preformatted("\n".join(out))


# Cells that carry no information. A markdown table has to put *something* in
# every cell, so "this does not apply" is written as a dash — and rendering that
# as `Listing Date: —` is noise dressed as a fact.
EMPTY_CELLS = {"", "-", "--", "---", "—", "–", "n/a", "na", "none"}

MAX_FIELDS = 10            # Slack caps section.fields at 10


def _is_empty(value: str) -> bool:
    return value.strip().lower() in EMPTY_CELLS


def render_cards(rows: list[list[str]]) -> list[dict]:
    """One card per body row: bold headline, prose, then a two-column fact grid.

    Slack's `section.fields` renders up to ten `*Label*\\nvalue` pairs as a tidy
    two-column grid. That is the right home for the short columns — putting them
    on one middot-joined line produced a 200-character wall on the recommendations
    table, which is the same unreadability the code fence caused, just narrower.

    Column roles are derived from the data rather than hardcoded, so this works on
    the recommendations table, the forward-pipeline table, the exclusions table
    and the financial-history tables in the briefings with no per-table config.
    """
    if len(rows) < 2:
        return []
    header, body = rows[0], rows[1:]
    ncols = max(len(r) for r in rows)

    def cell(row: list[str], j: int) -> str:
        return row[j].strip() if j < len(row) else ""

    def head_of(j: int) -> str:
        return header[j].strip() if j < len(header) else ""

    lowered = [h.strip().lower() for h in header]
    idx_col = 0 if lowered and lowered[0] in INDEX_HEADERS else None
    tkr_col = next((j for j, h in enumerate(lowered) if h in TICKER_HEADERS), None)

    # Title = first column that is neither the index nor the ticker.
    title_col = next((j for j in range(ncols) if j not in (idx_col, tkr_col)), 0)

    # A column is "meta" when every one of its values is short.
    meta_cols, prose_cols = [], []
    for j in range(ncols):
        if j in (idx_col, tkr_col, title_col):
            continue
        longest = max((len(cell(r, j)) for r in body), default=0)
        (meta_cols if longest <= META_MAX else prose_cols).append(j)

    blocks: list[dict] = []
    for row in body:
        # _plain, not inline: the source often bolds the title cell already, and
        # wrapping `*Shein*` in another pair yields `**Shein**`, which Slack
        # renders as literal asterisks.
        title = _plain(cell(row, title_col)) or _plain(cell(row, tkr_col or 0)) or "-"
        head = f"*{escape(title)}*"
        if idx_col is not None and not _is_empty(cell(row, idx_col)):
            head = f"*{escape(_plain(cell(row, idx_col)))}.*  " + head
        if tkr_col is not None and not _is_empty(cell(row, tkr_col)):
            head += f"   `{escape(_plain(cell(row, tkr_col)))}`"

        parts = [head]
        for j in prose_cols:
            val = cell(row, j)
            if _is_empty(val):
                continue
            # Label the prose only when there is more than one prose column;
            # a lone "Reason to add" column does not need announcing.
            parts.append(f"*{inline(head_of(j))}:* {inline(val)}"
                         if len(prose_cols) > 1 else inline(val))

        fields = [
            {"type": "mrkdwn",
             "text": f"*{inline(head_of(j))}*\n{inline(cell(row, j))}"[:1900]}
            for j in meta_cols if not _is_empty(cell(row, j))
        ]

        chunks = _chunk("\n".join(parts))
        if not chunks:
            chunks = [head]
        first: dict = {"type": "section",
                       "text": {"type": "mrkdwn", "text": chunks[0]}}
        if fields:
            first["fields"] = fields[:MAX_FIELDS]
        blocks.append(first)
        blocks.extend(_section(c) for c in chunks[1:])
    return blocks


# -------------------------------------------------------------------- rendering


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _header(text: str) -> dict:
    return {"type": "header",
            "text": {"type": "plain_text", "text": text[:HEADER_LIMIT],
                     "emoji": True}}


def _chunk(text: str, limit: int = MRKDWN_LIMIT) -> list[str]:
    """Split on paragraph boundaries, then lines, then hard-cut. Never empty."""
    if len(text) <= limit:
        return [text] if text.strip() else []
    parts, cur = [], ""
    for para in text.split("\n\n"):
        candidate = (cur + "\n\n" + para) if cur else para
        if len(candidate) <= limit:
            cur = candidate
            continue
        if cur:
            parts.append(cur)
        cur = ""
        if len(para) <= limit:
            cur = para
            continue
        for ln in para.split("\n"):          # oversized paragraph -> by line
            cand2 = (cur + "\n" + ln) if cur else ln
            if len(cand2) <= limit:
                cur = cand2
            else:
                if cur:
                    parts.append(cur)
                cur = ln
                while len(cur) > limit:      # a single oversized line
                    parts.append(cur[:limit])
                    cur = cur[limit:]
    if cur:
        parts.append(cur)
    return [p for p in parts if p.strip()]


def markdown_to_blocks(md: str) -> list[dict]:
    """Render markdown to Block Kit, capped at MAX_BLOCKS."""
    blocks: list[dict] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        for part in _chunk("\n".join(buf).strip()):
            blocks.append(_section(part))
        buf.clear()

    for kind, payload in parse(md):
        if kind == "heading":
            level, text = payload            # type: ignore[misc]
            if level <= 2:
                flush()
                blocks.append(_header(_plain(text)))
            else:
                buf.append(f"*{inline(text)}*")
        elif kind == "rule":
            flush()
            blocks.append({"type": "divider"})
        elif kind == "code":
            flush()
            body = "\n".join(
                ln for ln in str(payload).splitlines()
                if not ln.strip().startswith("```"))
            if body.strip():
                blocks.append(preformatted(body))
        elif kind == "table":
            rows = payload                   # type: ignore[assignment]
            if is_narrow(rows):              # type: ignore[arg-type]
                flush()
                blocks.append(render_aligned(rows))  # type: ignore[arg-type]
            else:
                flush()
                blocks.extend(render_cards(rows))  # type: ignore[arg-type]
        else:
            buf.append(inline_paragraph(str(payload)))

    flush()

    if len(blocks) > MAX_BLOCKS:
        kept = blocks[: MAX_BLOCKS - 1]
        kept.append({"type": "context", "elements": [{
            "type": "mrkdwn",
            "text": (f":warning: {len(blocks) - MAX_BLOCKS + 1} more block(s) "
                     f"omitted - Slack caps a message at 50. Full text is in "
                     f"the report file."),
        }]})
        return kept
    return blocks


def inline_paragraph(text: str) -> str:
    """Convert a paragraph, turning markdown bullets into Slack bullets."""
    out = []
    for ln in text.split("\n"):
        bullet = re.match(r"^(\s*)[-*]\s+(.*)$", ln)
        if bullet:
            indent = "    " * (len(bullet.group(1)) // 2)
            out.append(f"{indent}• {inline(bullet.group(2))}")
        else:
            out.append(inline(ln))
    return "\n".join(out)


def context_block(text: str) -> dict:
    """A small grey footer. `elements` is required - a bare `text` key 400s."""
    return {"type": "context",
            "elements": [{"type": "mrkdwn", "text": text[:MRKDWN_LIMIT]}]}
