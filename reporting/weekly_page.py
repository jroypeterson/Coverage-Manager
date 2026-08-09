"""Render the weekly coverage-additions report as a published HTML page.

**Why this exists.** The report was delivered as a Slack lead message plus eleven
thread replies. JP, 2026-08-08, looking at that thread: *"the way you have all of
the replies threaded is kind of confusing since there are so many questions... I
think having a published clickable html page that refreshes weekly that is nicely
formatted would be better and more readable and I can still reply in the slack
channel."*

The diagnosis underneath the complaint is that **the thread was routed by section,
not by whether a reply was needed.** Of the eleven replies exactly one asked JP for
anything; the other ten were reference material — a twelve-row pipeline table, two
listing-lane reports, ten exclusions and four company briefings — posted as if they
were questions. So the split is now: Slack carries the decisions, this page carries
everything, and one link joins them.

**Where it publishes, and why not Netlify or an artifact.** `docs/` on the public
`Coverage-Manager` repo, served by GitHub Pages at
`https://jroypeterson.github.io/Coverage-Manager/`. JP offered a Claude artifact or
Netlify and said he did not care about public; this beats both on the criterion he
actually gave ("clickable from my mobile") because it is a plain URL with no login,
and it beats both operationally because `run_weekly_coverage.bat` already runs
`git add -A`, `git commit` and `git push` with **exit-code gating on each**. Writing
a file into `docs/` therefore inherits a publish path that is already hardened and
already turns the scheduled task red when it fails. Netlify would add a token, a
deploy step and a new failure mode; a Claude artifact cannot be published by a
headless session at all.

**The decision strip is read from the LEDGER, not from the report.** A report is a
snapshot of what was true when it was written; `candidate_ledger.csv` is what is
true now. Rendering the report's own "3 pending" line onto a page that refreshes
weekly would show a queue that has already been decided — which is exactly the
confusion this page exists to remove. So the strip re-reads the ledger at render
time and shows live status, and a page rebuilt after JP replies shows his replies.

**Nothing is dropped.** Sections are rendered in report order and an unrecognised
one renders as-is, mirroring `post_coverage_to_ipo`'s rule that an unrecognised
section is threaded, never dropped. The parser is `slack_blocks.parse` — one
markdown parser for this report family, already covered by its own tests, rather
than a second one that could disagree with it.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reporting import slack_blocks

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
PAGES_URL = "https://jroypeterson.github.io/Coverage-Manager/"

# Report titles whose content is a decision. Everything else is reference.
_DECISION_TITLES = ("recommendations", "added without asking")

# `Sector (JP)` is a free-text column; a ticker is the one token worth linking.
_TICKER_RE = re.compile(r"`([A-Za-z0-9][A-Za-z0-9.\-]{0,14})`")


# --------------------------------------------------------------------- inline


def _inline(text: str) -> str:
    """Markdown inline -> HTML. Escapes first, so no input can inject markup."""
    out = html.escape(text, quote=False)
    # Code spans before emphasis: a ticker like `PNAQ.U` must not be re-parsed.
    holds: list[str] = []

    def _hold(m: re.Match) -> str:
        holds.append(m.group(1))
        return f"\x00{len(holds) - 1}\x00"

    out = re.sub(r"`([^`]+)`", _hold, out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                 r'<a href="\2" rel="noopener">\1</a>', out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", out)
    for i, held in enumerate(holds):
        out = out.replace(f"\x00{i}\x00", f'<span class="tk">{held}</span>')
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


# ---------------------------------------------------------------- ledger state


@dataclass(frozen=True)
class Decision:
    ticker: str
    company: str
    status: str            # pending / approved / declined / expired
    source: str
    sector: str
    market_cap: str
    reason: str


_STATE_LABEL = {
    "pending":  ("open",   "Awaiting your call"),
    "approved": ("added",  "In the universe"),
    "declined": ("closed", "Declined"),
    "expired":  ("closed", "Expired unanswered"),
}


def _cap(raw: str) -> str:
    """Ledger market_cap is a number or a hand-written '~$5.3B'. Both render."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        value = float(raw)
    except ValueError:
        return raw
    if value >= 1e12:
        return f"~${value / 1e12:,.2f}T"
    if value >= 1e9:
        return f"~${value / 1e9:,.1f}B"
    return f"~${value / 1e6:,.0f}M"


def load_decisions(thread_ts: str = "", *, rows=None) -> list[Decision]:
    """Live ledger state: everything on this report's thread, plus ALL pending.

    Pending rows are included regardless of which report proposed them, because a
    name still awaiting a reply is the page's whole reason to exist and it must
    not fall off when the next week's report is published.
    """
    from universe import candidate_ledger as cl

    rows = cl.load() if rows is None else rows
    picked = []
    for r in rows:
        on_thread = thread_ts and str(r.get("slack_thread_ts", "")).strip() == thread_ts
        if on_thread or r.get("status") == "pending":
            picked.append(Decision(
                ticker=str(r.get("ticker", "")).strip(),
                company=str(r.get("company", "")).strip(),
                status=str(r.get("status", "")).strip(),
                source=str(r.get("decision_source", "")).strip(),
                sector="/".join(x for x in (str(r.get("sector", "")).strip(),
                                            str(r.get("subsector", "")).strip()) if x),
                market_cap=_cap(str(r.get("market_cap", ""))),
                reason=str(r.get("reason", "")).strip(),
            ))
    # Open items first, then in-universe, then closed; alphabetical inside each.
    order = {"pending": 0, "approved": 1, "declined": 2, "expired": 3}
    return sorted(picked, key=lambda d: (order.get(d.status, 9), d.ticker))


def _render_decisions(decisions: list[Decision]) -> str:
    if not decisions:
        return ""
    open_n = sum(1 for d in decisions if d.status == "pending")
    lede = (
        f"<strong>{open_n} name{'s' if open_n != 1 else ''} awaiting your call.</strong> "
        "Reply in <span class=\"tk\">#ipo-spinoffs-newissues</span> with "
        "<span class=\"tk\">add TICKER</span>, <span class=\"tk\">decline TICKER</span> "
        "or <span class=\"tk\">add all</span> &mdash; top-level or in the thread, either works."
        if open_n else
        "<strong>Nothing is waiting on you.</strong> Every candidate below has been decided. "
        "Reply <span class=\"tk\">decline TICKER</span> in "
        "<span class=\"tk\">#ipo-spinoffs-newissues</span> to reverse any of them."
    )

    def _label(d: Decision) -> tuple[str, str]:
        tone, label = _STATE_LABEL.get(d.status, ("closed", d.status or "unknown"))
        if d.status == "approved" and d.source.startswith("auto-add"):
            label = "Added by rule &mdash; you were not asked"
        return tone, label

    # An open item gets the full case; a settled one gets a line. Rendering all
    # thirteen as full cards reproduces, on the page, exactly the wall of text
    # this page exists to replace -- and the reasons are near-identical when a
    # batch is approved together, so the repetition reads as padding.
    cards = []
    for d in (x for x in decisions if x.status == "pending"):
        tone, label = _label(d)
        facts = " ".join(f"<span>{html.escape(x)}</span>"
                         for x in (d.market_cap, d.sector) if x)
        cards.append(
            f'<article class="row s-{tone}">'
            f'<div class="row-top"><span class="row-tk">{html.escape(d.ticker)}</span>'
            f'<span class="row-co">{html.escape(d.company)}</span>'
            f'<span class="chip c-{tone}">{label}</span></div>'
            f'<div class="row-facts">{facts}</div>'
            f'<p class="row-why">{_inline(d.reason)}</p></article>'
        )

    settled = [d for d in decisions if d.status != "pending"]
    settled_html = ""
    if settled:
        lines = []
        for d in settled:
            tone, label = _label(d)
            meta = " &middot; ".join(html.escape(x) for x in
                                     (d.market_cap, d.sector) if x)
            lines.append(
                f'<li class="s-{tone}">'
                f'<span class="row-tk">{html.escape(d.ticker)}</span>'
                f'<span class="settled-co">{html.escape(d.company)}</span>'
                f'<span class="settled-meta">{meta}</span>'
                f'<span class="chip c-{tone}">{label}</span></li>'
            )
        heading = "Settled" if cards else "Decided"
        settled_html = (f"<h3>{heading} &mdash; {len(settled)} name"
                        f"{'s' if len(settled) != 1 else ''}</h3>"
                        f'<ul class="settled">{"".join(lines)}</ul>')

    replies = ""
    if open_n:
        chips = "".join(
            f'<button class="copy" type="button">add {html.escape(d.ticker)}</button>'
            for d in decisions if d.status == "pending"
        )
        replies = (f'<div class="replies">{chips}'
                   '<button class="copy" type="button">add all</button></div>')

    rows_html = f'<div class="rows">{"".join(cards)}</div>' if cards else ""
    return (
        '<section id="decisions"><h2>Decisions</h2>'
        f'<p class="sec-lede">{lede}</p>'
        f"{rows_html}{replies}{settled_html}</section>"
    )


# ------------------------------------------------------------------ rules panel

_RULES_HTML = """
<section id="rules"><h2>The rules &mdash; what adds itself, what waits for you</h2>
<p class="sec-lede">Two of the five inclusion buckets are size-gated formalities, so the lane
applies them without asking. The other three are judgement calls and always queue. An undecided
rule defaults to the status quo: wrongly queueing costs one Slack reply, wrongly auto-adding puts
a row in the fleet's most-depended-on artifact.</p>
<div class="tw"><table>
<thead><tr><th>Bucket</th><th>Rule</th><th class="num">Threshold</th><th>Behaviour</th><th>Why</th></tr></thead>
<tbody>
<tr><td class="co">2</td><td>Any IPO or direct listing, <strong>any sector</strong></td>
<td class="num">&ge; $25B</td><td class="tight b-auto">AUTO</td>
<td>Mandatory by rule &mdash; queueing it just puts a step between the rule and the outcome.</td></tr>
<tr><td class="co">3</td><td>Spin-off, carve-out, separation</td>
<td class="num">&gt; $10B</td><td class="tight b-auto">AUTO</td>
<td>Same. Applies at separation, never before &mdash; no shares trade, so there is no cap to gate on.</td></tr>
<tr><td class="co">1</td><td>Core-sector IPO / listing / spin-off &mdash; HC services, MedTech, tools, diagnostics, HCIT, adjacent tech</td>
<td class="num">any size</td><td class="tight b-queue">QUEUES</td>
<td>No size floor at all, so auto-adding would sweep in every microcap biotech that lists.</td></tr>
<tr><td class="co">4</td><td>Strategically relevant new candidate</td>
<td class="num">$2&ndash;20B</td><td class="tight b-queue">QUEUES</td>
<td>&ldquo;Strategically relevant&rdquo; is explicitly a judgement call. Not mechanisable.</td></tr>
<tr><td class="co">5</td><td>Russell 1000 / 2000 first-time addition, any sector</td>
<td class="num">$2&ndash;20B</td><td class="tight b-queue">QUEUES</td>
<td>~40 a quarter, mostly unfamiliar names.</td></tr>
</tbody></table></div>
<h3>Three refusals that override everything above</h3>
<ul class="guards">
<li><b>No market cap means no auto-add, ever.</b> Both auto buckets are size-gated, and unknown is
not qualifying. This matters most for spin-offs &mdash; a Form 10 candidate legitimately has no cap
until it trades.</li>
<li><b>A ticker on the provenance removals list is never auto-added.</b> A vendor has no idea a row
was deliberately removed and will keep re-proposing it. An FMP biopharma screen re-proposed
<span class="tk">ALBT</span> eight days after it was removed for leaving healthcare.</li>
<li><b>Auto means unasked, not unvalidated.</b> Every auto-add routes through the same enrichment
gate as a manual one, which refuses a half-filled row and says why.</li>
</ul>
<h3>What happens to your reply</h3>
<p class="prose-narrow">A poller reads <span class="tk">#ipo-spinoffs-newissues</span> at 09:20,
13:20 and 18:20 ET. It reads <strong>both</strong> top-level channel messages and thread replies
&mdash; where you post makes no difference. It only ever acts on a ticker already sitting
<span class="tk">pending</span> in the ledger, it ignores everyone but you, and it is idempotent by
message timestamp so a transient failure cannot re-fire an approval. It answers in the thread with
what landed and what did not, then republishes <span class="tk">exports/</span> so the change
reaches the downstream consumers.</p>
</section>
"""


# ----------------------------------------------------------------- report body


def _render_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    ths = "".join(f"<th>{_inline(c)}</th>" for c in head)
    trs = []
    for r in body:
        # A short row is padded, never truncated: dropping a cell would drop data.
        cells = list(r) + [""] * (len(head) - len(r))
        tds = "".join(f"<td>{_inline(c)}</td>" for c in cells)
        trs.append(f"<tr>{tds}</tr>")
    return (f'<div class="tw"><table><thead><tr>{ths}</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')


def _render_para(text: str) -> str:
    """A markdown paragraph buffer may hold a bullet list, prose, or both."""
    out: list[str] = []
    items: list[str] = []

    def flush_items() -> None:
        if items:
            lis = "".join(f"<li>{_inline(x)}</li>" for x in items)
            out.append(f'<ul class="notes">{lis}</ul>')
            items.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^[-*+]\s+(.*)", line)
        if m:
            items.append(m.group(1))
            continue
        if items and raw.startswith(("  ", "\t")):
            items[-1] += " " + line          # continuation of the bullet above
            continue
        flush_items()
        out.append(f"<p>{_inline(line)}</p>")
    flush_items()
    return "".join(out)


def render_body(md: str) -> tuple[str, list[tuple[str, str]]]:
    """Report markdown -> (html, [(anchor, title)]) for the section nav."""
    nodes = slack_blocks.parse(md)
    parts: list[str] = []
    nav: list[tuple[str, str]] = []
    open_section = False

    for kind, payload in nodes:
        if kind == "heading":
            level, title = payload
            if level == 1:
                continue                     # the masthead already carries it
            if level == 2:
                if open_section:
                    parts.append("</section>")
                anchor = _slug(title)
                nav.append((anchor, re.sub(r"[*`]", "", title)))
                parts.append(f'<section id="{anchor}"><h2>{_inline(title)}</h2>')
                open_section = True
                continue
            parts.append(f"<h3>{_inline(title)}</h3>")
        elif kind == "table":
            parts.append(_render_table(payload))
        elif kind == "code":
            body = "\n".join(payload.splitlines()[1:-1])
            parts.append(f'<div class="tw"><pre>{html.escape(body)}</pre></div>')
        elif kind == "rule":
            continue                         # sections already separate visually
        else:
            parts.append(_render_para(payload))

    if open_section:
        parts.append("</section>")
    return "".join(parts), nav


# ------------------------------------------------------------------ whole page


def _meta(md: str) -> dict:
    """Pull the report's own header facts rather than recomputing them."""
    out = {}
    m = re.search(r"\*\*Review window:\*\*\s*(.+)", md)
    if m:
        out["window"] = re.sub(r"[*`]", "", m.group(1)).strip()
    m = re.search(r"—\s*([\d,]+)\s*rows", md)
    if m:
        out["rows"] = m.group(1)
    return out


def render(md: str, *, report_date: str, decisions: list[Decision],
           generated: str = "") -> str:
    body, nav = render_body(md)
    meta = _meta(md)
    open_n = sum(1 for d in decisions if d.status == "pending")

    tiles = [
        ("is-open" if open_n else "", open_n, "Awaiting you"),
        ("is-added", sum(1 for d in decisions if d.status == "approved"), "In the universe"),
        ("", sum(1 for d in decisions if d.status in ("declined", "expired")), "Declined"),
    ]
    tile_html = "".join(
        f'<div class="tile {cls}"><span class="n">{n}</span><span class="l">{label}</span></div>'
        for cls, n, label in tiles
    )

    nav_html = "".join(
        f'<a href="#{a}">{html.escape(t)}</a>' for a, t in
        [("decisions", "Decisions"), ("rules", "The rules")] + nav
    )

    meta_html = "".join([
        f'<span>Report <b>{html.escape(report_date)}</b></span>',
        f'<span>Window <b>{html.escape(meta["window"])}</b></span>' if meta.get("window") else "",
        f'<span>Universe <b>{html.escape(meta["rows"])} rows</b></span>' if meta.get("rows") else "",
        f'<span>Built <b>{html.escape(generated)}</b></span>' if generated else "",
    ])

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>Coverage Universe Additions — {html.escape(report_date)}</title>"
        f"<style>{_CSS}</style></head><body><div class=\"shell\">"
        '<header class="mast">'
        '<p class="eyebrow">Agentic Investing &middot; Coverage Manager &middot; discovery lane</p>'
        "<h1>Weekly Coverage Universe Additions</h1>"
        f'<div class="mast-meta">{meta_html}</div>'
        f'<nav class="secnav">{nav_html}</nav>'
        "</header>"
        f'<div class="tiles">{tile_html}</div>'
        f"{_render_decisions(decisions)}"
        f"{_RULES_HTML}"
        f"{body}"
        '<footer><span>Coverage Manager &middot; discovery lane</span>'
        '<span><a href="archive.html">Past weeks</a></span>'
        '<span><a href="https://github.com/jroypeterson/Coverage-Manager">Repo</a></span>'
        "</footer></div>"
        f"<script>{_JS}</script></body></html>"
    )


def render_archive(dates: list[str]) -> str:
    items = "".join(
        f'<li><a href="weekly/{html.escape(d)}.html">{html.escape(d)}</a></li>'
        for d in sorted(dates, reverse=True)
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Coverage Universe Additions — archive</title>"
        f"<style>{_CSS}</style></head><body><div class=\"shell\">"
        '<header class="mast"><p class="eyebrow">Agentic Investing &middot; Coverage Manager</p>'
        "<h1>Weekly reports</h1></header>"
        f'<section><h2>Archive</h2><ul class="archive">{items}</ul>'
        '<p class="prose-narrow"><a href="index.html">&larr; Latest report</a></p></section>'
        "</div></body></html>"
    )


_REPORT_GLOB = "weekly_coverage_universe_additions_*.md"


def find_report(report_date: str = "") -> tuple[Path, str]:
    """Locate a report by date, or the newest one. Searches the archive too.

    `reports/` is gitignored and the weekly run archives into `reports/old reports/`,
    so a report written on Friday has usually MOVED by the time a later run wants to
    re-render it. Looking in only one directory would silently render nothing.
    """
    from config import REPORTS_DIR  # noqa: PLC0415 - config imports pandas

    roots = [Path(REPORTS_DIR), Path(REPORTS_DIR) / "old reports"]
    found: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for p in root.glob(_REPORT_GLOB):
            stamp = p.stem.rsplit("_", 1)[-1]
            # Live `reports/` wins over the archive for the same date: a re-run
            # writes there, and the archived copy is the older of the two.
            if stamp not in found or root == roots[0]:
                found[stamp] = p
    if not found:
        raise FileNotFoundError(
            f"no {_REPORT_GLOB} in {roots[0]} or {roots[1]}")
    if report_date:
        if report_date not in found:
            raise FileNotFoundError(
                f"no report for {report_date}; have {', '.join(sorted(found))}")
        return found[report_date], report_date
    newest = max(found)
    return found[newest], newest


def thread_ts_for(report_date: str) -> str:
    """The Slack thread this report posted to, per the ledger's own provenance."""
    from universe import candidate_ledger as cl

    seen: dict[str, int] = {}
    for r in cl.load():
        if str(r.get("first_proposed", "")).strip() != report_date:
            continue
        ts = str(r.get("slack_thread_ts", "")).strip()
        if ts:
            seen[ts] = seen.get(ts, 0) + 1
    return max(seen, key=seen.get) if seen else ""


def publish(md: str, *, report_date: str, thread_ts: str = "",
            docs_dir: Path | None = None, generated: str = "") -> dict:
    """Write index.html + weekly/<date>.html + archive.html. Returns what changed."""
    docs = Path(docs_dir) if docs_dir else DOCS_DIR
    (docs / "weekly").mkdir(parents=True, exist_ok=True)

    decisions = load_decisions(thread_ts)
    page = render(md, report_date=report_date, decisions=decisions,
                  generated=generated or date.today().isoformat())

    dated = docs / "weekly" / f"{report_date}.html"
    # utf-8 + newline="" so the bytes are identical on every platform; a page that
    # differs only by line ending would show as changed in `git add -A` every week.
    for target in (dated, docs / "index.html"):
        target.write_text(page, encoding="utf-8", newline="\n")

    archived = sorted(p.stem for p in (docs / "weekly").glob("*.html"))
    (docs / "archive.html").write_text(render_archive(archived),
                                       encoding="utf-8", newline="\n")

    return {"url": PAGES_URL, "report_date": report_date,
            "open": sum(1 for d in decisions if d.status == "pending"),
            "decisions": len(decisions), "archived": len(archived),
            "bytes": len(page.encode("utf-8"))}


_CSS = """
:root{--paper:#f4f5f3;--surface:#fcfcfb;--surface-2:#eef0ec;--ink:#191c1a;--ink-soft:#3d4441;
--muted:#5f6864;--faint:#8b938f;--rule:#dcdfdb;--rule-soft:#e7e9e5;--open:#b0561a;
--open-bg:#fbf0e6;--added:#2f6b4f;--added-bg:#e8f1ec;--closed:#6b736f;--closed-bg:#eceeea;
--serif:Georgia,"Iowan Old Style","Times New Roman",serif;
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
--mono:ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace;
--shell:1140px;--prose:68ch}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#131614;--surface:#1b1f1d;
--surface-2:#232825;--ink:#e9ece9;--ink-soft:#c6ccc8;--muted:#98a09b;--faint:#6e7772;--rule:#2d322f;
--rule-soft:#262b28;--open:#e59254;--open-bg:#35251a;--added:#63b28c;--added-bg:#16291f;
--closed:#8d9591;--closed-bg:#222724}}
:root[data-theme="dark"]{--paper:#131614;--surface:#1b1f1d;--surface-2:#232825;--ink:#e9ece9;
--ink-soft:#c6ccc8;--muted:#98a09b;--faint:#6e7772;--rule:#2d322f;--rule-soft:#262b28;
--open:#e59254;--open-bg:#35251a;--added:#63b28c;--added-bg:#16291f;--closed:#8d9591;--closed-bg:#222724}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:16px;
line-height:1.55;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
.shell{max-width:var(--shell);margin:0 auto;padding:0 28px 88px}
@media(max-width:640px){.shell{padding:0 16px 56px}}
a{color:inherit;text-underline-offset:2px;text-decoration-color:var(--faint)}
a:focus-visible,button:focus-visible{outline:2px solid var(--open);outline-offset:2px;border-radius:2px}
code,.tk{font-family:var(--mono);font-size:.86em;letter-spacing:-.01em}
.tk{background:var(--surface-2);border:1px solid var(--rule);border-radius:3px;padding:1px 5px;white-space:nowrap}
.mast{border-bottom:2px solid var(--ink);padding:44px 0 18px}
.eyebrow{font-size:11px;font-weight:650;letter-spacing:.13em;text-transform:uppercase;color:var(--muted);margin:0 0 12px}
h1{font-family:var(--serif);font-size:clamp(30px,4.4vw,46px);line-height:1.08;font-weight:600;
letter-spacing:-.015em;margin:0 0 14px;text-wrap:balance}
.mast-meta{display:flex;flex-wrap:wrap;gap:6px 22px;font-size:13px;color:var(--muted);font-family:var(--mono)}
.mast-meta b{color:var(--ink-soft);font-weight:600}
.secnav{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:16px;font-size:12px}
.secnav a{color:var(--muted);text-decoration:none;border-bottom:1px solid transparent;padding-bottom:1px}
.secnav a:hover{color:var(--open);border-bottom-color:var(--open)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:1px;
background:var(--rule);border:1px solid var(--rule);border-top:none;margin-bottom:52px}
.tile{background:var(--surface);padding:16px 18px 15px}
.tile .n{font-family:var(--mono);font-size:30px;font-weight:600;line-height:1;letter-spacing:-.02em;display:block;margin-bottom:7px}
.tile .l{font-size:11px;letter-spacing:.075em;text-transform:uppercase;color:var(--muted);font-weight:600}
.tile.is-open .n{color:var(--open)}
.tile.is-added .n{color:var(--added)}
section{margin-bottom:62px;scroll-margin-top:20px}
h2{font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
margin:0 0 6px;padding-bottom:10px;border-bottom:1px solid var(--rule)}
.sec-lede{font-family:var(--serif);font-size:19px;line-height:1.5;max-width:var(--prose);
margin:18px 0 26px;color:var(--ink-soft);text-wrap:pretty}
h3{font-size:15px;font-weight:650;letter-spacing:-.005em;margin:34px 0 10px}
p{max-width:var(--prose);margin:0 0 14px;text-wrap:pretty}
section>p{margin-top:14px}
.prose-narrow{max-width:var(--prose)}
.rows{display:flex;flex-direction:column;gap:12px}
.row{background:var(--surface);border:1px solid var(--rule);border-left:3px solid var(--closed);
border-radius:4px;padding:16px 18px 15px}
.row.s-open{border-left-color:var(--open)}
.row.s-added{border-left-color:var(--added)}
.row-top{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 12px;margin-bottom:4px}
.row-tk{font-family:var(--mono);font-size:15px;font-weight:650;letter-spacing:-.01em}
.row-co{font-size:15px;font-weight:600}
.chip{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:3px 8px;
border-radius:100px;white-space:nowrap;background:var(--closed-bg);color:var(--closed)}
.chip.c-open{background:var(--open-bg);color:var(--open)}
.chip.c-added{background:var(--added-bg);color:var(--added)}
.row-facts{display:flex;flex-wrap:wrap;gap:3px 18px;font-family:var(--mono);font-size:12px;
color:var(--muted);margin:8px 0 10px}
.row-why{font-size:14.5px;line-height:1.55;color:var(--ink-soft);max-width:82ch;margin:0}
.row-why strong{color:var(--ink);font-weight:650}
.settled{list-style:none;padding:0;margin:14px 0 0;display:flex;flex-direction:column;gap:1px;
background:var(--rule);border:1px solid var(--rule);border-radius:4px;overflow:hidden}
.settled li{background:var(--surface);padding:10px 14px;display:flex;flex-wrap:wrap;
align-items:baseline;gap:6px 12px;border-left:3px solid var(--closed)}
.settled li.s-added{border-left-color:var(--added)}
.settled li.s-open{border-left-color:var(--open)}
.settled .settled-co{font-size:14px;font-weight:600;color:var(--ink)}
.settled .settled-meta{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-left:auto}
.settled .chip{flex:0 0 auto}
@media(max-width:640px){.settled .settled-meta{margin-left:0}}
.replies{display:flex;flex-wrap:wrap;gap:7px;margin:18px 0 0}
.copy{font-family:var(--mono);font-size:12.5px;background:var(--surface);color:var(--ink-soft);
border:1px solid var(--rule);border-radius:4px;padding:6px 11px;cursor:pointer;
display:inline-flex;align-items:center;gap:7px;transition:border-color .12s,color .12s}
.copy:hover{border-color:var(--open);color:var(--open)}
.copy::after{content:"copy";font-size:9px;letter-spacing:.08em;text-transform:uppercase;
color:var(--faint);font-family:var(--sans);font-weight:700}
.copy.done{border-color:var(--added);color:var(--added)}
.copy.done::after{content:"copied";color:var(--added)}
@media(prefers-reduced-motion:reduce){.copy{transition:none}}
.tw{overflow-x:auto;border:1px solid var(--rule);border-radius:4px;background:var(--surface);margin:0 0 16px}
table{border-collapse:collapse;width:100%;font-size:13.5px;min-width:620px}
th{text-align:left;font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
color:var(--muted);padding:11px 14px;border-bottom:1px solid var(--rule);background:var(--surface-2);white-space:nowrap}
td{padding:11px 14px;border-bottom:1px solid var(--rule-soft);vertical-align:top;color:var(--ink-soft)}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-family:var(--mono);white-space:nowrap}
td.co{color:var(--ink);font-weight:600;white-space:nowrap}
td.tight{white-space:nowrap}
.b-auto{color:var(--added);font-weight:700}
.b-queue{color:var(--open);font-weight:700}
pre{margin:0;padding:14px;overflow-x:auto;font-family:var(--mono);font-size:12.5px;
line-height:1.5;color:var(--ink-soft)}
.guards,.notes{list-style:none;padding:0;margin:18px 0 0;display:flex;flex-direction:column;gap:11px}
.guards li,.notes li{padding-left:15px;border-left:2px solid var(--rule);font-size:14.5px;
color:var(--ink-soft);max-width:84ch}
.guards b,.notes b{color:var(--ink);font-weight:650}
.archive{list-style:none;padding:0;margin:18px 0 0;display:flex;flex-direction:column;gap:9px;font-family:var(--mono)}
footer{border-top:1px solid var(--rule);padding-top:20px;font-size:12.5px;color:var(--faint);
font-family:var(--mono);display:flex;flex-wrap:wrap;gap:6px 24px}
"""

_JS = """
document.querySelectorAll(".copy").forEach(function(b){b.addEventListener("click",function(){
var t=b.textContent.trim();var d=function(){b.classList.add("done");
setTimeout(function(){b.classList.remove("done")},1400)};
if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(d,function(){})}
else{var a=document.createElement("textarea");a.value=t;document.body.appendChild(a);a.select();
try{document.execCommand("copy");d()}catch(e){}document.body.removeChild(a)}})});
"""
