"""Post the weekly coverage-additions report to Slack #ipo-spinoffs-newissues.

Why this is a script and not prompt instructions: the weekly run is a headless
`claude -p` session, and hand-rolling Slack payloads there has already failed twice —
once by posting to a channel with no webhook (the report landed in #stock-price-alerts
and went unread), once by reporting "emailed" for a draft that is never sent. Chunking
long briefings under Slack's block limits is exactly the kind of thing that silently
truncates when an agent eyeballs it. So the agent writes markdown; this posts it.

**Layout (rewritten 2026-08-05).** The report used to go out as one enormous message
with every markdown table fenced in a ``` block. JP's screenshot of the 07-31 post is
the argument against it: an 11-column table wrapped into unreadable pipe-soup, and the
decision — *which names am I being asked to approve* — was buried a dozen screens below
the fold. Rendering is now `reporting/slack_blocks`, and the report is split:

    channel   lead message: title, this week's framing, the Recommendations cards,
              the pending-approval backlog, and how to reply
    thread    every other section, in report order, then one briefing per company,
              then a compact footer of generated files

**Nothing is dropped.** Sections are routed by H2 title, and any title this script does
not recognise goes to the thread rather than being skipped — `test_every_section_is_routed`
pins that, because a silent omission here is indistinguishable from the report never
having mentioned the name at all. That is the exact failure that lost Jersey Mike's.

The threading is the point. JP should be able to decide from the thread alone —
reply `add CSQR` in place — without opening the Dropbox folder to find the write-up.

Usage:
    python scripts/post_coverage_to_ipo.py --date 2026-07-24
    python scripts/post_coverage_to_ipo.py --date 2026-07-24 --dry-run
    python scripts/post_coverage_to_ipo.py --date 2026-07-24 --dry-run --preview
    python scripts/post_coverage_to_ipo.py --date 2026-07-24 --thread-ts 1785262763.598659
        (attach briefings to an existing summary post instead of posting a new one)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reporting.pipeline_reversals import (  # noqa: E402
    SECTION_EXCLUDED, find_reversals, load_prior_reports,
)
from reporting.slack_blocks import (  # noqa: E402
    MAX_BLOCKS, context_block, markdown_to_blocks,
)

SLACK_API = "https://slack.com/api/"

# H2 sections that belong in the channel-level lead message. Matched as a
# case-folded prefix so a week titled "Recommendations (3)" still routes right.
LEAD_SECTION_PREFIXES = ("recommendations", "pending approval")

# Sections that are pure run-metadata: rendered as a small grey footer at the end
# of the thread rather than as a full section, because nobody decides from them.
FOOTER_SECTION_PREFIXES = ("report files", "csv changes")


class PostError(RuntimeError):
    pass


# --------------------------------------------------------------------------- env


def _env(name: str) -> str:
    val = os.environ.get(name, "")
    if val:
        return val
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise PostError(f"{name} not set (checked env and {env_path})")


# ----------------------------------------------------------------- section split


def _matches(title: str, prefixes: tuple[str, ...]) -> bool:
    low = title.strip().lower()
    return any(low.startswith(p) for p in prefixes)


def split_sections(md: str) -> tuple[str, list[tuple[str, str]]]:
    """-> (preamble, [(title, body_including_heading)]).

    Splits on every H2, **and** on any H3 whose title matches a routing prefix.
    The second half is not defensive padding: in the live 2026-07-31 report
    `### Pending approval backlog` — the list of names JP is being asked to decide,
    and the only place the reply syntax appears — is nested under `## Notes`. A
    strict H2 split filed the decision itself under "Notes", four messages deep in
    the thread. Heading depth is a formatting choice the report-writing agent makes
    week to week; what a section *is* must not depend on it.
    """
    heads = list(re.finditer(r"^(#{2,3}) +(.+?)\s*$", md, flags=re.M))
    bounds = [m for m in heads
              if len(m.group(1)) == 2
              or _matches(m.group(2), LEAD_SECTION_PREFIXES + FOOTER_SECTION_PREFIXES)]
    if not bounds:
        return md.strip(), []
    preamble = md[: bounds[0].start()].strip()
    sections = []
    for n, m in enumerate(bounds):
        end = bounds[n + 1].start() if n + 1 < len(bounds) else len(md)
        sections.append((m.group(2).strip(), md[m.start():end].strip()))
    return preamble, sections


def route(md: str) -> tuple[str, list[str], list[str]]:
    """Split the report into (lead_markdown, thread_bodies, footer_bodies).

    Every H2 section lands in exactly one bucket. Unrecognised titles go to the
    thread — never dropped.
    """
    preamble, sections = split_sections(md)
    lead_parts = [preamble] if preamble else []
    thread, footer = [], []
    for title, body in sections:
        if _matches(title, LEAD_SECTION_PREFIXES):
            lead_parts.append(body)
        elif _matches(title, FOOTER_SECTION_PREFIXES):
            footer.append(body)
        else:
            thread.append(body)
    return "\n\n".join(lead_parts), thread, footer


def split_briefings(md: str) -> list[tuple[str, str]]:
    """-> [(heading, body)] for each '### <COMPANY> — Quick Background' section."""
    out = []
    for m in re.finditer(r"^### (.+?)$", md, flags=re.M):
        start = m.start()
        nxt = md.find("\n### ", m.end())
        section = md[start:nxt if nxt != -1 else len(md)]
        heading = re.sub(r"\s*[—-]\s*Quick Background\s*$", "", m.group(1)).strip()
        out.append((heading, section.strip()))
    return out


# ---------------------------------------------------------------------- posting


def _api(method: str, payload: dict, token: str) -> dict:
    req = urllib.request.Request(
        SLACK_API + method,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    if not body.get("ok"):
        # Loud, never silent: a failed post makes the whole report invisible.
        raise PostError(f"{method} failed: {body.get('error')}")
    return body


def _preview(blocks: list[dict]) -> str:
    """Flatten blocks to text so a dry run can be eyeballed without posting."""
    out = []
    for b in blocks:
        if b["type"] == "header":
            out.append("=== " + b["text"]["text"] + " ===")
        elif b["type"] == "divider":
            out.append("-" * 60)
        elif b["type"] == "context":
            out.append("[ " + b["elements"][0]["text"] + " ]")
        elif b["type"] == "rich_text":
            out.append("\n".join(e2.get("text", "")
                                 for e1 in b["elements"]
                                 for e2 in e1.get("elements", [])))
        else:
            piece = b["text"]["text"]
            if b.get("fields"):
                grid = [f.get("text", "").replace("\n", ": ") for f in b["fields"]]
                # Two columns, the way Slack lays fields out.
                pairs = [grid[i:i + 2] for i in range(0, len(grid), 2)]
                piece += "\n" + "\n".join(
                    "   " + "".join(c.ljust(42) for c in p).rstrip() for p in pairs)
            out.append(piece)
    return "\n\n".join(out)


def post(blocks: list[dict], *, token: str, channel: str, fallback: str,
         thread_ts: str | None = None, dry_run: bool = False,
         preview: bool = False) -> str:
    if len(blocks) > MAX_BLOCKS:
        raise PostError(f"{len(blocks)} blocks exceeds the {MAX_BLOCKS} cap")
    if dry_run:
        print(f"  [dry-run] {len(blocks)} block(s)"
              f"{' (threaded)' if thread_ts else ''} - {fallback[:60]}")
        if preview:
            print(_preview(blocks).encode("ascii", "replace").decode("ascii"))
            print()
        return "dry-run"
    payload = {"channel": channel, "blocks": blocks, "text": fallback,
               "unfurl_links": False, "unfurl_media": False}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return _api("chat.postMessage", payload, token)["ts"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="post_coverage_to_ipo")
    ap.add_argument("--date", required=True, help="report date, YYYY-MM-DD")
    ap.add_argument("--reports-dir", default=None)
    ap.add_argument("--thread-ts", default=None,
                    help="attach briefings to an existing summary post")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--preview", action="store_true",
                    help="with --dry-run, print the rendered text")
    ap.add_argument("--channel", default=None,
                    help="override SLACK_IPO_CHANNEL_ID (for a test post)")
    ap.add_argument("--no-reversal-check", action="store_true",
                    help="skip the promised-then-excluded cross-check")
    a = ap.parse_args(argv)

    reports = Path(a.reports_dir) if a.reports_dir else PROJECT_ROOT / "reports"
    summary_p = reports / f"weekly_coverage_universe_additions_{a.date}.md"
    briefs_p = reports / f"company_backgrounds_{a.date}.md"
    # The weekly archive step sweeps dated files into "old reports/" — look there too
    # rather than failing on a file that exists one directory over.
    for p in (summary_p, briefs_p):
        if not p.exists():
            alt = reports / "old reports" / p.name
            if alt.exists():
                if p is summary_p:
                    summary_p = alt
                else:
                    briefs_p = alt

    if not summary_p.exists():
        raise PostError(f"missing summary report: {summary_p}")

    token = "" if a.dry_run else _env("SLACK_BOT_TOKEN")
    channel = a.channel or ("dry-run" if a.dry_run else _env("SLACK_IPO_CHANNEL_ID"))

    summary_md = summary_p.read_text(encoding="utf-8")
    lead_md, thread_bodies, footer_bodies = route(summary_md)

    thread_ts = a.thread_ts
    if not thread_ts:
        # The full report is a page now; the lead carries the decisions and a link.
        # JP 2026-08-08 on the eleven-reply thread: "kind of confusing since there
        # are so many questions". Of those eleven replies exactly one asked him for
        # anything -- the rest were reference material shaped like questions.
        from reporting.weekly_page import PAGES_URL

        lead_blocks = markdown_to_blocks(lead_md) + [context_block(
            f":page_facing_up: *Full report, formatted and clickable:* {PAGES_URL}\n"
            "Pipeline, listing-lane findings, exclusions, company briefings and the "
            "auto-add rules all live there. Reply here to decide -- top-level or in "
            "thread, either is read.", convert=False)]
        thread_ts = post(lead_blocks, token=token, channel=channel,
                         fallback=f"Weekly Coverage Universe Additions - {a.date}",
                         dry_run=a.dry_run, preview=a.preview)
        print(f"lead posted: ts={thread_ts}")

    # A name the report promised to add and then quietly excluded is the failure
    # that lost Jersey Mike's. Surface it beside the exclusion, not in a log file.
    reversals = []
    if not a.no_reversal_check:
        reversals = find_reversals(
            summary_md, load_prior_reports(reports, a.date))
        for r in reversals:
            print(f"  REVERSAL: {r.company[:40]} (promised {r.prior_date})"
                  .encode("ascii", "replace").decode("ascii"))
    warn_blocks = [context_block(
        f":warning: *{len(reversals)} unexplained reversal(s)* - this report "
        f"excludes a name an earlier report committed to adding:")] + [
        context_block(r.as_line()) for r in reversals] if reversals else []

    attached = False
    for body in thread_bodies:
        title = body.splitlines()[0].lstrip("# ").strip()
        blocks = markdown_to_blocks(body)
        if warn_blocks and SECTION_EXCLUDED.search(title):
            blocks = blocks + warn_blocks
            attached = True
        post(blocks, token=token, channel=channel,
             thread_ts=thread_ts, fallback=title,
             dry_run=a.dry_run, preview=a.preview)
        print(f"  threaded section: {title[:60]}")

    if warn_blocks and not attached:
        # No exclusions section this week, but a reversal still happened. Posting
        # it standalone rather than dropping it - the whole point is that a
        # retracted promise must not be able to disappear.
        post(warn_blocks, token=token, channel=channel, thread_ts=thread_ts,
             fallback="Unexplained reversal", dry_run=a.dry_run,
             preview=a.preview)
        print("  threaded reversal warning (no exclusions section)")

    briefings: list[tuple[str, str]] = []
    if briefs_p.exists():
        briefings = split_briefings(briefs_p.read_text(encoding="utf-8"))
        if not briefings:
            print(f"WARNING: {briefs_p} parsed to zero briefings", file=sys.stderr)
    else:
        print(f"WARNING: no briefings file at {briefs_p} - report posted without "
              f"write-ups; JP cannot decide from the thread alone", file=sys.stderr)

    for heading, body in briefings:
        post(markdown_to_blocks(body), token=token, channel=channel,
             thread_ts=thread_ts, fallback=heading,
             dry_run=a.dry_run, preview=a.preview)
        print(f"  threaded briefing: {heading[:60]}")

    if footer_bodies:
        blocks = [context_block(b[:2900]) for b in footer_bodies]
        post(blocks, token=token, channel=channel, thread_ts=thread_ts,
             fallback="Report files", dry_run=a.dry_run, preview=a.preview)
        print("  threaded footer")

    print(f"done - {len(thread_bodies)} section(s), {len(briefings)} briefing(s) "
          f"in thread {thread_ts}")
    return 0 if briefings else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PostError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
