"""Flag names the report promised to add, then quietly excluded.

**The incident.** The 2026-07-24 report put Jersey Mike's Subs in *Pipeline /
filings to monitor* with full terms and an explicit forward commitment — *"Would be
a Consumer add at pricing."* The 2026-07-31 report moved it to *Considered and
excluded* as *"Restaurant franchisor - not universe-relevant."* Both rows are
defensible in isolation. Together they are a reversal, and the second row never
says the first one existed as a commitment — it cites the prior report's date while
presenting the new verdict as if it had always been the verdict.

JP noticed the name never came through. The discovery lane had in fact found it four
days before pricing; what failed was that a *promise* was retracted with no notice,
in a table nobody reads, one week after the promise was made.

**Why this is a script and not a rule in the prompt.** A docstring is not a
mechanism. The instruction "explain reversals" cannot be verified, does not fire,
and is exactly the kind of prose the weekly agent is free to satisfy by citing a
date. This runs over the actual reports and either finds a reversal or does not.

**What counts as a reversal:** a prior report's pipeline entry containing a forward
*commitment* (not mere monitoring), whose company reappears in a later report's
exclusions, where the exclusion text carries no reversal language. A pipeline row
that only said "tracked if it announces a target" is not a promise and its later
exclusion is not a reversal — that distinction is the whole point, or every SPAC in
the forward book would flag every week.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Phrases that turn a monitoring note into a promise. Every one of these is drawn
# from real pipeline rows in the 2026-06/07 reports.
COMMITMENT = re.compile(
    r"would be (a|an) .{0,40}\badd\b"
    r"|will be (a|an) .{0,40}\badd\b"
    r"|mandatory .{0,30}\badd\b"
    r"|\badd (at|on) pricing\b"
    r"|bucket \d+ (event|add)\b"
    r"|add (it|the name)? ?when it prices",
    re.I | re.S)

# Language that shows the later report knows it is changing its own call.
ACKNOWLEDGED = re.compile(
    r"revers|contradict|supersed|previously (said|flagged as)"
    r"|earlier (call|report) (said|was)|changed (our|the) (call|view|mind)"
    r"|on reflection|revis(ed|ing) (the|our) (call|view)"
    r"|no longer|retract|walk(ing)? back|correcting the",
    re.I)

SECTION_PIPELINE = re.compile(r"pipeline|filings to monitor|forward book", re.I)
SECTION_EXCLUDED = re.compile(r"considered and excluded|excluded|not added", re.I)

REPORT_GLOB = "weekly_coverage_universe_additions_*.md"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class Reversal:
    key: str
    company: str
    prior_date: str
    promise: str
    exclusion: str

    def as_line(self) -> str:
        return (f"*{self.company}* - the {self.prior_date} report said "
                f"“{_trim(self.promise)}”, and this week's exclusion "
                f"(“{_trim(self.exclusion)}”) does not say what changed.")


def _trim(text: str, limit: int = 150) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"[*`]", "", text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _norm(value: str) -> str:
    """Normalise a company/ticker cell to a comparison key."""
    value = re.sub(r"[*`_]", "", value or "").strip().lower()
    value = re.sub(r"\(.*?\)", " ", value)
    value = re.sub(r"\b(inc|corp|corporation|ltd|limited|plc|s\.?a\.?|nv|ag|"
                   r"co|company|group|holdings|therapeutics|the)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


TICKER_IN_TEXT = re.compile(r"[(`]([A-Z]{1,6}(?:\.[A-Z]{1,3})?|\d{4,6}\.[A-Z]{2})[)`]")
BOLD = re.compile(r"\*\*(.+?)\*\*")


def _identify(line: str) -> tuple[str, str] | None:
    """-> (company, ticker) for a table row or a bullet, else None.

    The forward book has lived in **both** shapes: a `Pipeline / filings to
    monitor` table (2026-07-31) and a bullet list nested under `## Notes`
    (2026-07-24 — the report that promised Jersey Mike's). A parser that
    understood only tables found nothing on the very report this check exists
    for, and reported clean. Layout is the agent's weekly choice; identity is not.
    """
    s = line.strip()
    if s.startswith("|") and s.endswith("|"):
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2 or all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            return None
        company = re.sub(r"[*`]", "", cells[0]).strip()
        ticker = re.sub(r"[*`]", "", cells[1]).strip()
        if not company or company.lower() in {"company", "#", ""}:
            return None
        return company, (ticker if 0 < len(ticker) <= 12 else "")

    bold = BOLD.search(s)
    tick = TICKER_IN_TEXT.search(s)
    if not bold and not tick:
        return None
    company = re.sub(r"\s*[(`].*", "", bold.group(1)).strip() if bold else ""
    ticker = tick.group(1) if tick else ""
    if not company and not ticker:
        return None
    return company or ticker, ticker


def _key(company: str, ticker: str) -> str:
    """Prefer the ticker, but fall back when it normalises to nothing.

    A pre-listing name has no symbol yet and the report writes a placeholder —
    Shein's ticker cell is literally `(HK)`, which `_norm` strips to "" because it
    drops parenthesised text. Keying on that dropped the row entirely, so the one
    company in the forward book with an explicit "will be a mandatory add" promise
    was invisible to the check.
    """
    normalised = _norm(ticker) if ticker else ""
    return normalised or _norm(company)


def promises(md: str) -> dict[str, tuple[str, str]]:
    """Every line anywhere in the report that commits to adding a named company.

    Scanned line-wise across the whole document rather than within a named
    section, because a commitment phrase is self-identifying ("would be a Consumer
    add at pricing") while the heading above it is not stable week to week.
    """
    out: dict[str, tuple[str, str]] = {}
    for line in md.splitlines():
        if not COMMITMENT.search(line):
            continue
        ident = _identify(line)
        if not ident:
            continue
        company, ticker = ident
        out[_key(company, ticker)] = (company, line.strip())
    return {k: v for k, v in out.items() if k}


def exclusions(md: str) -> dict[str, tuple[str, str]]:
    """Entries under a "considered and excluded" heading, table rows or bullets.

    Section-scoped, unlike `promises`: an exclusion reason is ordinary prose with
    nothing to distinguish it from any other sentence about a company, so the
    heading is the only thing that marks it as a verdict.
    """
    out: dict[str, tuple[str, str]] = {}
    for section in re.split(r"^#{2,4} +", md, flags=re.M)[1:]:
        title = section.splitlines()[0] if section else ""
        if not SECTION_EXCLUDED.search(title):
            continue
        for line in section.splitlines()[1:]:
            ident = _identify(line)
            if not ident:
                continue
            company, ticker = ident
            key = _key(company, ticker)
            if key:
                out[key] = (company, line.strip())
    return out


def find_reversals(current_md: str,
                   prior: list[tuple[str, str]]) -> list[Reversal]:
    """`prior` is [(report_date, markdown)], any order. Newest promise wins."""
    excluded = exclusions(current_md)
    if not excluded:
        return []
    found: dict[str, Reversal] = {}
    for report_date, md in sorted(prior):
        for key, (company, text) in promises(md).items():
            if key not in excluded:
                continue
            exc_company, exc_text = excluded[key]
            if ACKNOWLEDGED.search(exc_text):
                found.pop(key, None)
                continue
            found[key] = Reversal(key=key, company=exc_company or company,
                                  prior_date=report_date, promise=text,
                                  exclusion=exc_text)
    return sorted(found.values(), key=lambda r: r.company.lower())


def load_prior_reports(reports_dir: Path, current_date: str,
                       limit: int = 8) -> list[tuple[str, str]]:
    """The `limit` most recent reports strictly older than `current_date`.

    Looks in `reports/` and `reports/old reports/` because the weekly archive step
    sweeps dated files into the latter — searching only one of them would make the
    check quietly find nothing, which is the failure mode it exists to prevent.
    """
    seen: dict[str, Path] = {}
    for folder in (reports_dir, reports_dir / "old reports"):
        if not folder.is_dir():
            continue
        for path in folder.glob(REPORT_GLOB):
            m = DATE_RE.search(path.name)
            if m and m.group(1) < current_date:
                seen.setdefault(m.group(1), path)
    out = []
    for report_date in sorted(seen, reverse=True)[:limit]:
        try:
            out.append((report_date,
                        seen[report_date].read_text(encoding="utf-8",
                                                    errors="replace")))
        except OSError:
            continue          # an unreadable archive is not a reversal finding
    return out
