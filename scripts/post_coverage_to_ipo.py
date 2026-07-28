"""Post the weekly coverage-additions summary to #ipo, with each company's full
briefing as a threaded reply.

Why this is a script and not prompt instructions: the weekly run is a headless
`claude -p` session, and hand-rolling Slack payloads there has already failed twice —
once by posting to a channel with no webhook (the report landed in #stock-price-alerts
and went unread), once by reporting "emailed" for a draft that is never sent. Chunking
long briefings under Slack's 3,000-char block limit is exactly the kind of thing that
silently truncates when an agent eyeballs it. So the agent writes markdown; this posts it.

The threading is the point. JP should be able to decide from the thread alone —
reply `add CSQR` in place — without opening the Dropbox folder to find the write-up.

Usage:
    python scripts/post_coverage_to_ipo.py --date 2026-07-24
    python scripts/post_coverage_to_ipo.py --date 2026-07-24 --dry-run
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
BLOCK_LIMIT = 2900          # Slack's section-text cap is 3,000; leave headroom.
SLACK_API = "https://slack.com/api/"


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


# ------------------------------------------------------------------ md -> slack


def md_to_mrkdwn(text: str) -> str:
    """Convert the report's markdown to Slack mrkdwn.

    Markdown tables are wrapped in a code fence rather than converted — Slack has no
    table primitive, and a fenced block at least keeps the columns aligned. Losing the
    financial-snapshot tables would defeat the purpose of putting the briefing in the
    thread at all.
    """
    out: list[str] = []
    table: list[str] = []

    def flush_table() -> None:
        if not table:
            return
        # Drop the |---|---| separator row; it is noise once monospaced.
        rows = [r for r in table if not re.fullmatch(r"\s*\|[\s|:-]+\|\s*", r)]
        out.append("```\n" + "\n".join(rows) + "\n```")
        table.clear()

    for line in text.splitlines():
        if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            table.append(line.strip())
            continue
        flush_table()
        line = re.sub(r"^#{1,6}\s+(.*)$", r"*\1*", line)      # headings -> bold
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)         # **bold** -> *bold*
        line = re.sub(r"^\s*[-*]\s+", "• ", line)              # bullets
        out.append(line)
    flush_table()

    body = "\n".join(out)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def chunk(text: str, limit: int = BLOCK_LIMIT) -> list[str]:
    """Split on paragraph boundaries, never inside a code fence."""
    parts, cur, in_fence = [], "", False
    for para in text.split("\n\n"):
        fences = para.count("```")
        candidate = (cur + "\n\n" + para) if cur else para
        if len(candidate) <= limit or in_fence:
            cur = candidate
        else:
            if cur:
                parts.append(cur)
            cur = para
            while len(cur) > limit:            # a single oversized paragraph
                parts.append(cur[:limit])
                cur = cur[limit:]
        if fences % 2:
            in_fence = not in_fence
    if cur:
        parts.append(cur)
    return parts


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


def post(text: str, *, token: str, channel: str, thread_ts: str | None = None,
         fallback: str, dry_run: bool = False) -> str:
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": c}}
              for c in chunk(text)]
    if dry_run:
        print(f"  [dry-run] {len(blocks)} block(s), {len(text)} chars"
              f"{' (threaded)' if thread_ts else ''}")
        return "dry-run"
    payload = {"channel": channel, "blocks": blocks, "text": fallback,
               "unfurl_links": False}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return _api("chat.postMessage", payload, token)["ts"]


# ---------------------------------------------------------------------- parsing


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="post_coverage_to_ipo")
    ap.add_argument("--date", required=True, help="report date, YYYY-MM-DD")
    ap.add_argument("--reports-dir", default=None)
    ap.add_argument("--thread-ts", default=None,
                    help="attach briefings to an existing summary post")
    ap.add_argument("--dry-run", action="store_true")
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

    token = _env("SLACK_BOT_TOKEN")
    channel = _env("SLACK_IPO_CHANNEL_ID")

    thread_ts = a.thread_ts
    if not thread_ts:
        summary = md_to_mrkdwn(summary_p.read_text(encoding="utf-8"))
        thread_ts = post(summary, token=token, channel=channel,
                         fallback=f"Weekly Coverage Universe Additions - {a.date}",
                         dry_run=a.dry_run)
        print(f"summary posted: ts={thread_ts}")

    if not briefs_p.exists():
        print(f"WARNING: no briefings file at {briefs_p} - summary posted without "
              f"write-ups; JP cannot decide from the thread alone", file=sys.stderr)
        return 1

    briefings = split_briefings(briefs_p.read_text(encoding="utf-8"))
    if not briefings:
        print(f"WARNING: {briefs_p} parsed to zero briefings", file=sys.stderr)
        return 1

    for heading, body in briefings:
        post(md_to_mrkdwn(body), token=token, channel=channel, thread_ts=thread_ts,
             fallback=heading, dry_run=a.dry_run)
        print(f"  threaded briefing: {heading}")

    print(f"done - {len(briefings)} briefing(s) in thread {thread_ts}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PostError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
