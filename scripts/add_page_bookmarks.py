"""Pin the published weekly page as a Slack channel bookmark.

JP 2026-08-09: *"also add this as a bookmark to coverage manager adn ipo channel"*.
A channel bookmark sits in the header bar, so the page is one tap away on mobile
without hunting for the week's post — which is the point of publishing it.

Idempotent by title: `bookmarks.list` is checked first and an existing bookmark is
`bookmarks.edit`-ed rather than added again, so re-running never leaves two.
Needs `bookmarks:read` + `bookmarks:write`, both already on ClaudeBot.

    python scripts/add_page_bookmarks.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reporting.weekly_page import PAGES_URL  # noqa: E402
from scripts.post_coverage_to_ipo import PostError, _env  # noqa: E402

SLACK_API = "https://slack.com/api/"
TITLE = "Weekly coverage report"
EMOJI = ":page_facing_up:"

# Both channels Coverage Manager posts to. #coverage carries the weekly universe
# delta, #ipo-spinoffs-newissues carries the additions report and the approvals.
CHANNELS = {
    "C0BKLC32EHK": "#ipo-spinoffs-newissues",
    "C0B4MQKH189": "#coverage",
}


def _api(method: str, params: dict, token: str, *, post: bool = False) -> dict:
    if post:
        req = urllib.request.Request(
            SLACK_API + method, data=json.dumps(params).encode("utf-8"),
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json; charset=utf-8"})
    else:
        req = urllib.request.Request(
            SLACK_API + method + "?" + urllib.parse.urlencode(params),
            headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    if not body.get("ok"):
        raise PostError(f"{method} failed: {body.get('error')}")
    return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="add_page_bookmarks")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--url", default=PAGES_URL)
    a = ap.parse_args(argv)

    token = _env("SLACK_BOT_TOKEN")
    failures = []

    for channel_id, name in CHANNELS.items():
        try:
            existing = _api("bookmarks.list", {"channel_id": channel_id},
                            token).get("bookmarks", [])
            match = next((b for b in existing if b.get("title") == TITLE), None)

            if a.dry_run:
                print(f"[dry-run] {name}: would "
                      f"{'edit' if match else 'add'} {TITLE!r} -> {a.url}")
                continue

            if match:
                _api("bookmarks.edit",
                     {"channel_id": channel_id, "bookmark_id": match["id"],
                      "title": TITLE, "link": a.url}, token, post=True)
                print(f"{name}: updated {TITLE!r} -> {a.url}")
            else:
                _api("bookmarks.add",
                     {"channel_id": channel_id, "title": TITLE, "type": "link",
                      "link": a.url, "emoji": EMOJI}, token, post=True)
                print(f"{name}: added {TITLE!r} -> {a.url}")
        except PostError as exc:
            # One channel failing must not stop the other; report both outcomes.
            print(f"{name}: FAILED - {exc}", file=sys.stderr)
            failures.append(name)

    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PostError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
