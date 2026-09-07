"""The week's added names, each with a short plain-English business summary.

**Why.** JP, 2026-09-06: *"each stock that you added ... I want you to have a
summary of what the business does in the slack channel."* The lead message named
five tickers and their buckets and said nothing about what any of them sold. The
full briefings answer that, but they moved to the published page on 2026-08-09
and run to two thousand words each -- correct for reference, useless for the
thirty seconds in which someone decides whether a name is worth opening.

**Source order, best first.** Each falls back only when the one above is absent,
and the resolved source travels with the summary so a thin week is visible as a
thin week rather than passing for a written one:

1. `company_backgrounds_<date>.md` -> the opening of `#### 1. Business
   Description`. Written for this exact purpose and in the house voice.
2. `candidate_ledger.csv` -> the `notes` column. Compact and third-person, but
   written to justify the add rather than to describe the business.
3. Nothing. Renders as an explicit "no briefing written this week", which is a
   real defect worth seeing, not a blank to be smoothed over.

**Names come from the ledger, not from the report's prose.** The ledger is the
decision record; the report is one week's description of it. Parsing the report's
own tables to find out what the report added is circular, and it broke once
already when a table gained a column.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUMMARY_MAX = 240          # ~two lines on a phone
# Split after terminal punctuation, OR after the closing `**` of a bolded
# sentence. Without the second alternative SSMR's opener -- "**This is a
# pre-revenue company...**" -- was one 900-char "sentence" and the 240-char cut
# landed mid-clause. Two lookbehinds rather than `\*{0,2}` inside one, because
# consuming the `**` as part of the separator drops it: the sentence then ends
# with an unmatched `**` and Slack renders literal asterisks.
_SENTENCE = re.compile(r"(?<=[.!?])\s+|(?<=[.!?]\*\*)\s+")


@dataclass
class Added:
    ticker: str
    company: str
    market_cap: str = ""
    sector: str = ""
    trigger: str = ""
    summary: str = ""
    source: str = "none"

    def label(self) -> str:
        bits = [b for b in (_cap(self.market_cap), self.sector, self.trigger) if b]
        return " · ".join(bits)


def _cap(raw: str) -> str:
    """Market cap for a phone line.

    The ledger column is mixed by construction -- rows written by hand carry
    `~$22.8B`, rows written by `auto_add` carry a bare float straight from the
    provider. Printing the float gave `26400000000` in the lead message, which is
    the number nobody can read at a glance and the reason this exists.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        val = float(text.replace(",", "").replace("$", "").replace("~", ""))
    except ValueError:
        return text                      # already formatted -- leave it alone
    if val >= 1e12:
        return f"~${val/1e12:,.2f}T"
    if val >= 1e9:
        return f"~${val/1e9:,.1f}B"
    if val >= 1e6:
        return f"~${val/1e6:,.0f}M"
    return text


class LedgerUnreadable(RuntimeError):
    """The candidate ledger exists but could not be read."""


def _read_csv(path: Path) -> list[dict]:
    """Raises when the file EXISTS but cannot be read.

    Returning `[]` there is indistinguishable from "nothing was added this
    week", so an unreadable ledger silently produced a Slack post with no
    business summaries and an exit code of 0 -- the section just was not there,
    and nothing said why.
    """
    if not path.exists():
        return []
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    except OSError as exc:
        raise LedgerUnreadable(f"{path.name}: {exc}") from exc


# ------------------------------------------------------------------ briefings


def _briefing_sections(md: str) -> dict[str, str]:
    """-> {upper-cased ticker or company: section text} for each '### ...' block."""
    out: dict[str, str] = {}
    for m in re.finditer(r"^### (.+?)$", md, flags=re.M):
        nxt = md.find("\n### ", m.end())
        body = md[m.start():nxt if nxt != -1 else len(md)]
        head = m.group(1)
        tick = re.search(r"\(([^)]+)\)", head)
        if tick:
            out[tick.group(1).strip().upper()] = body
        name = re.sub(r"\s*\(.*", "", head)
        name = re.sub(r"\s*[—-]\s*Quick Background\s*$", "", name).strip()
        if name:
            out[name.upper()] = body
    return out


def _business_line(section: str) -> str:
    """First one-to-two sentences of the section's Business Description.

    The briefings open the description with a single short thesis sentence and
    then expand over several paragraphs; taking the first paragraph whole put 900
    characters into a Slack lead that is capped at 2,600 for the entire message.
    """
    m = re.search(r"####\s*\d*\.?\s*Business Description\s*\n+(.+?)(?=\n####|\Z)",
                  section, flags=re.S | re.I)
    if not m:
        return ""
    # Paragraph breaks are collapsed before sentence-splitting. The briefings
    # sometimes open on a one-line thesis that IS its own paragraph -- "We make
    # the metal that has to not fail." -- and stopping at the blank line shipped
    # that alone as the whole description.
    para = " ".join(m.group(1).split())
    if not para:
        return ""
    parts = [p.strip() for p in _SENTENCE.split(para) if p.strip()]
    if not parts:
        return ""
    out = parts[0]
    # A very short opener is a hook, not a description -- take the next sentence
    # too rather than shipping "The interesting part is not the clothes."
    # Only when the pair still FITS: pulling in a 200-char second sentence and
    # letting the 240-char cut chop it produced "...survive launch loads, high-g"
    # for AADX, which reads as a truncation bug rather than a summary.
    if len(out) < 90 and len(parts) > 1 and len(out) + len(parts[1]) + 1 <= SUMMARY_MAX:
        out = f"{out} {parts[1]}"
    if len(out) > SUMMARY_MAX:
        # Prefer the last whole sentence that fits over a mid-clause ellipsis.
        whole = [p for p in parts if len(p) <= SUMMARY_MAX]
        out = whole[0] if whole else (
            out[:SUMMARY_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "...")
    return out


def _ledger_line(notes: str) -> str:
    text = " ".join((notes or "").split())
    if not text:
        return ""
    if len(text) > SUMMARY_MAX:
        text = text[:SUMMARY_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return text


# ---------------------------------------------------------------------- collect


def collect(report_date: str, *, window_start: str = "",
            root: Path = PROJECT_ROOT, briefings_md: str = "") -> list[Added]:
    """The names approved in this report's window, with a summary for each."""
    end = report_date
    if not window_start:
        try:
            window_start = date.fromordinal(
                date.fromisoformat(end).toordinal() - 7).isoformat()
        except ValueError:
            window_start = end

    sections = _briefing_sections(briefings_md) if briefings_md else {}
    out: list[Added] = []
    for r in _read_csv(root / "data" / "candidate_ledger.csv"):
        if (r.get("status") or "") != "approved":
            continue
        when = (r.get("decision_date") or r.get("first_proposed") or "")[:10]
        # Exclusive start: the window's first day belongs to the prior report.
        if not (when and window_start < when <= end):
            continue
        a = Added(ticker=(r.get("ticker") or "").strip(),
                  company=(r.get("company") or "").strip(),
                  market_cap=(r.get("market_cap") or "").strip(),
                  sector=(r.get("sector") or "").strip(),
                  trigger=(r.get("trigger") or "").strip())
        sec = sections.get(a.ticker.upper()) or sections.get(a.company.upper())
        if sec:
            line = _business_line(sec)
            if line:
                a.summary, a.source = line, "briefing"
        if not a.summary:
            line = _ledger_line(r.get("notes", ""))
            if line:
                a.summary, a.source = line, "ledger"
        if not a.summary:
            a.summary = "_No briefing written this week._"
            a.source = "none"
        out.append(a)
    return sorted(out, key=lambda x: x.ticker)


def as_markdown(added: list[Added]) -> str:
    """Slack-bound markdown: one short block per name, business first."""
    if not added:
        return ""
    lines = [f"## Added this week — what each one does ({len(added)})", ""]
    for a in added:
        head = f"**{a.company}**" + (f" `{a.ticker}`" if a.ticker else "")
        label = a.label()
        lines.append(head + (f"  \n{label}" if label else ""))
        # "What it does:" is not decoration. The briefings are written in the
        # first person ("We sell clothes on the internet"), which works on the
        # page where five sections establish the device, but in one line of a
        # bot-authored Slack message "We" reads as either a quote from the
        # company (it is not) or as this firm. The prefix names the sentence as
        # a description and removes both readings.
        lines.append(f"_What it does:_ {a.summary}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
