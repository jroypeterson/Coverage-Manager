"""Companies that have confidentially submitted a draft registration statement.

**The gap.** A JOBS Act emerging-growth company may submit its S-1 to the SEC
confidentially and must only make it public 15 days before the roadshow. The
confidential submission is not searchable -- `s1_watch` cannot see it, and
neither can anything else in this project. But companies routinely *announce*
they have submitted, by press release, three to six months ahead. That
announcement is the earliest public signal a company intends to list, and it is
roughly ten weeks earlier than the public S-1 flip that `s1_watch` catches.

**Why this is a small ledger and not a lane.** There is no structured source, no
CIK, and no filing to key on. The agent adds entries from its weekly web search;
this module's job is only to keep the list honest and to close entries
automatically so it does not become a stale hand-maintained page.

**Provenance is the whole design, and it is enforced here rather than asked for
in prose.** Two things get called "confidentially filed" and they are not the
same claim:

    announcement  the company itself said so, in a press release or a filing.
                  A fact, with a source.
    report        a journalist said so, usually "according to people familiar
                  with the matter". A rumour, and it is sometimes wrong.

`SOURCE_KINDS` is a strict enum, `source_url` is required, and the two render in
separate tables under different headings. A rumour must never be able to sit in
the same list as a fact and inherit its authority -- that is the failure this
module exists to prevent, and it is exactly the failure the fee-table
"placeholder" marking exists to prevent one lane over.

**No size, ever.** There is no filing, so there is no fee table and no share
count. Any dollar figure attached to one of these came from a journalist. The
schema has no field for one.

**Closing.** An entry closes when `s1_watch` later sees a registrant whose name
matches -- the company flipped its S-1 and is now tracked by the real lane -- or
when it ages out. Name matching is fuzzy by nature, so a match is reported and
the entry is marked `flipped`, never silently deleted.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

WATCH_PATH = Path("data/confidential_watch.json")

#: Strict enum. An entry whose kind is not one of these is rejected on load --
#: "rumoured", "sources", "leak" and friends all mean `report`, and letting them
#: through would put a rumour one typo away from reading as a company statement.
SOURCE_ANNOUNCEMENT = "announcement"
SOURCE_REPORT = "report"
SOURCE_KINDS = (SOURCE_ANNOUNCEMENT, SOURCE_REPORT)

#: Lifecycle. Validated on load for the same reason `source_kind` is: both
#: `reconcile` and `render` act only on `open`, so ANY other value -- including
#: the empty string a missing field used to produce -- removes the company from
#: the report permanently and silently. That is the exact disappearance this
#: ledger exists to prevent.
STATUS_OPEN, STATUS_FLIPPED, STATUS_EXPIRED = "open", "flipped", "expired"
STATUSES = (STATUS_OPEN, STATUS_FLIPPED, STATUS_EXPIRED)

#: A confidential submission that has not flipped in this long is dead, withdrawn
#: or was never real. Longer than s1_watch's carry because the whole point is
#: that this signal arrives early: 3-6 months to the flip is normal, and a
#: company that slips a quarter has not stopped existing.
MAX_AGE_DAYS = 550

_NOISE = re.compile(r"\b(inc|corp|corporation|co|ltd|limited|plc|llc|lp|holdings?|"
                    r"group|company|technologies|technology|the)\b\.?", re.I)


@dataclass
class Entry:
    company: str
    source_kind: str
    source_url: str
    first_seen: str
    note: str = ""
    status: str = "open"          # open | flipped | expired
    flipped_to: str = ""          # the registrant name s1_watch matched

    @property
    def is_announcement(self) -> bool:
        return self.source_kind == SOURCE_ANNOUNCEMENT


def normalise(name: str) -> str:
    """Company name reduced to a comparable key.

    Deliberately crude. It exists to match "Acme Robotics, Inc." against "Acme
    Robotics Inc" -- not to be clever about "Acme" vs "Acme Holdings", which is a
    judgement a person should make.
    """
    out = _NOISE.sub(" ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", out)


# ------------------------------------------------------------------- storage


def load(path: Path) -> list[Entry]:
    """Read the watch list. Rejects malformed entries loudly rather than quietly.

    A dropped entry here is a company silently falling off the earliest signal
    the project has, so a bad row raises rather than being skipped.
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path.name} is unreadable ({exc})") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{path.name} must be a JSON array")
    out = []
    for n, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}[{n}] is not an object")
        missing = [k for k in ("company", "source_kind", "source_url", "first_seen")
                   if not str(row.get(k, "")).strip()]
        if missing:
            raise ValueError(f"{path.name}[{n}] ({row.get('company', '?')}) "
                             f"is missing {', '.join(missing)}")
        if row["source_kind"] not in SOURCE_KINDS:
            raise ValueError(
                f"{path.name}[{n}] ({row['company']}) has source_kind "
                f"{row['source_kind']!r}; must be one of {SOURCE_KINDS}. A "
                f"journalist's account is {SOURCE_REPORT!r}, not "
                f"{SOURCE_ANNOUNCEMENT!r}.")
        status = str(row.get("status", "") or STATUS_OPEN)
        if status not in STATUSES:
            raise ValueError(
                f"{path.name}[{n}] ({row['company']}) has status {status!r}; "
                f"must be one of {STATUSES}. An unrecognised status hides the "
                f"company from the report entirely.")
        # Optional fields fall back to the DATACLASS default, not to "".
        # `row.get(k, "")` for `status` silently produced "", which is not
        # "open", so an entry written without the field vanished forever.
        out.append(Entry(
            company=str(row["company"]).strip(),
            source_kind=row["source_kind"],
            source_url=str(row["source_url"]).strip(),
            first_seen=str(row["first_seen"]).strip(),
            note=str(row.get("note", "") or ""),
            status=status,
            flipped_to=str(row.get("flipped_to", "") or "")))
    return out


def save(path: Path, entries: list[Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([asdict(e) for e in entries], indent=1,
                              sort_keys=True), encoding="utf-8")
    tmp.replace(path)


# -------------------------------------------------------------------- close


def reconcile(entries: list[Entry], registrants: list[str], *,
              today: date | None = None) -> tuple[list[Entry], list[str]]:
    """-> (entries, notes). Closes anything that flipped or aged out.

    `registrants` are the names `s1_watch` saw this run. A confidential entry
    whose company now appears as a real registrant has flipped: the public lane
    tracks it from here, and leaving it in both places would double-count the
    same company in the same report.
    """
    today = today or date.today()
    by_key = {normalise(n): n for n in registrants if n}
    notes: list[str] = []
    for e in entries:
        if e.status != "open":
            continue
        hit = by_key.get(normalise(e.company))
        if hit:
            e.status, e.flipped_to = "flipped", hit
            notes.append(f"{e.company} flipped its S-1 (now tracked as {hit})")
            continue
        try:
            age = (today - date.fromisoformat(e.first_seen[:10])).days
        except ValueError:
            age = 0
        if age > MAX_AGE_DAYS:
            e.status = "expired"
            notes.append(f"{e.company} expired after {age} days with no public "
                         f"filing")
    return entries, notes


# ------------------------------------------------------------------- render


def render(entries: list[Entry]) -> str:
    """Markdown section. Announcements and reports NEVER share a table."""
    open_rows = [e for e in entries if e.status == "open"]
    if not open_rows:
        return ""
    said = [e for e in open_rows if e.is_announcement]
    heard = [e for e in open_rows if not e.is_announcement]

    out = [f"## Confidential submissions ({len(open_rows)})", "",
           "Companies that have submitted a draft registration statement "
           "**confidentially**. Not searchable on EDGAR, so no lane can see "
           "these -- they are here because someone said so. Typically three to "
           "six months ahead of the public S-1 flip. **No sizes**: there is no "
           "public filing, so any figure would be a journalist's.", ""]

    if said:
        out += ["### Confirmed by the company", "",
                "The company said so itself. A fact, with a source.", "",
                "| Company | Since | Source | Note |", "|---|---|---|---|"]
        out += [f"| {e.company[:44]} | {e.first_seen[:10]} "
                f"| [link]({e.source_url}) | {e.note[:80]} |" for e in said] + [""]

    if heard:
        out += ["### Press reports only - UNCONFIRMED", "",
                "A journalist reported it, usually from unnamed sources. This is "
                "a rumour: it is sometimes wrong, and it must not be treated as "
                "a filing.", "",
                "| Company | Since | Source | Note |", "|---|---|---|---|"]
        out += [f"| {e.company[:44]} | {e.first_seen[:10]} "
                f"| [link]({e.source_url}) | {e.note[:80]} |" for e in heard] + [""]

    return "\n".join(out)
