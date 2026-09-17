"""Verify the fleet's OTHER copies of an index list against this repo's snapshot.

Board #354, and specifically the one residual its brief could not close.

## Why this exists instead of the thing the brief asked for

`plans/index_membership_brief.md` step 4 ends: *"Retire `sigma-alert/sources/sp500.txt`
after one compatibility cycle."* Four consumers were migrated onto this repo's snapshot in
2026-09-15 and that line was the last one left. **It is not executable, and the reason is a
constraint the same brief established.** Measured 2026-09-16:

  * `sigma-alert` has no local runtime at all -- every one of its seven jobs runs in GitHub
    Actions (`sigma-open/midday/close`, `sync-watchlist`, `refresh-sp500`, the watchdog and
    the weekly skip report). A CI checkout sees only that repo.
  * This repo's snapshots are **gitignored on purpose** (`.gitignore:37 data/index_membership/`)
    under the licensing decision the brief settled: *"Redistribution -- the real concern.
    Solved by gitignoring. Never in `exports/`."*

So the committed text file is the only way a CI-hosted screener can know the S&P 500, and
publishing our snapshot to give it another way is exactly what the licensing call forbids.
Retiring the mirror would take `sigma-alert` offline. The brief's step 4 is withdrawn, with
this module as the replacement.

## What the duplication actually costs, and what does not

Two collectors scrape the same public Wikipedia page -- `providers/wikipedia_provider.py`
here, `scripts/refresh_sp500.py` there. That is not the defect. A CI job is *more* reliable
for a CI-hosted repo than a weekly local one: if this machine is off for a month, a
CM-written mirror goes stale and nothing in `sigma-alert` can refresh it, whereas its own
Action cannot miss. Redundant collection of a free public page is cheap redundancy.

**The defect is that nothing would ever notice them disagreeing.** Two lists, two jobs, two
cadences (this repo weekly, `refresh_sp500.py` monthly), and no reader of either is told when
they diverge. `screens_equity` measured 503/503 agreement on 2026-09-16 -- a fact with a
shelf life, and nothing was watching it. So: detect, do not unify.

## What this module does NOT do

It does not write, fix or normalise the mirror, and it makes no network call. It reports.
A mirror that has drifted is a fact for a human -- the correct repair depends on which side
is wrong, and this module cannot know that.

## The failure it is built to avoid in itself

`feedback_a_check_cannot_expect_what_it_measures`. A comparison whose reference is missing,
stale or short must report **"cannot verify"**, never "clean". A silent pass on an absent
snapshot is worse than no check, because it retires the worry. Every non-comparison outcome
here is its own status and none of them is `ok`.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import config
from universe import index_membership as im

# The fleet's known second copies of a list this repo also collects.
#
# `repo_relative` is resolved against the FLEET ROOT (this repo's parent), which is how
# every other cross-repo read in the fleet is spelled. A mirror that is absent is reported
# absent -- it is not evidence of agreement, and it is not an error either: a fresh checkout
# legitimately has none.
@dataclass(frozen=True)
class Mirror:
    name: str            # how a human refers to it
    repo: str            # the sibling repo's directory name under the fleet root
    path_in_repo: str    # the file's path INSIDE that repo, as git spells it
    index_key: str       # the key in this repo's index_membership snapshots
    fmt: str             # "lines" (one ticker per line, # comments) or "names_json"
    why: str             # why this copy exists and may not simply be deleted

    @property
    def repo_relative(self) -> str:
        return f"{self.repo}/{self.path_in_repo}"


MIRRORS: tuple[Mirror, ...] = (
    Mirror(
        name="sigma-alert S&P 500 watchlist source",
        repo="sigma-alert",
        path_in_repo="sources/sp500.txt",
        index_key="sp500",
        fmt="lines",
        why=("sigma-alert runs only in GitHub Actions and cannot read this repo's "
             "gitignored snapshot; sync_watchlist.py builds watchlist.txt from it"),
    ),
    Mirror(
        name="sigma-alert S&P 500 name fallback",
        repo="sigma-alert",
        path_in_repo="sources/sp500_names.json",
        index_key="sp500",
        fmt="names_json",
        why=("sigma_screener.load_sp500_names() fills company names for the ~390 S&P "
             "names Coverage Manager holds no metadata for"),
    ),
)

FLEET_ROOT = Path(config.__file__).resolve().parent.parent

# A mirror below this fraction of the snapshot's size is treated as broken rather than
# merely divergent. A half-written monthly refresh should not read as "391 departures".
MIN_MIRROR_FRACTION = 0.80


@dataclass
class MirrorResult:
    name: str
    path: str
    index_key: str
    status: str                      # ok | drifted | mirror_absent | mirror_unreadable
                                     # | mirror_implausible | reference_unusable
    detail: str = ""
    mirror_count: int | None = None
    snapshot_count: int | None = None
    only_in_mirror: list[str] = field(default_factory=list)
    only_in_snapshot: list[str] = field(default_factory=list)
    # Which bytes were actually compared, and whether CI is reading them yet.
    compared: str = "worktree"       # "committed" | "worktree"
    publish_lag: str = ""            # non-empty => the compared copy is not what CI has

    @property
    def is_problem(self) -> bool:
        """Everything that is not a verified match, including 'could not check'.

        `reference_unusable` counts as a problem on purpose. The check going blind is a
        thing to fix, not a quiet pass -- that distinction is the whole point of the
        module's docstring.

        A publish lag is a problem too, even when the lists agree: the point of this
        module is whether the bytes the CI-hosted screener READS still match, and an
        uncommitted or unpushed fix is one CI does not have.
        """
        return self.status != "ok" or bool(self.publish_lag)

    def as_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path, "index_key": self.index_key,
            "status": self.status, "detail": self.detail,
            "mirror_count": self.mirror_count, "snapshot_count": self.snapshot_count,
            "only_in_mirror": self.only_in_mirror,
            "only_in_snapshot": self.only_in_snapshot,
            "compared": self.compared, "publish_lag": self.publish_lag,
        }


def _git(repo_root: Path, *args: str) -> tuple[int, str]:
    """Run one git command in `repo_root`. Never raises; returns (code, stdout)."""
    try:
        p = subprocess.run(["git", "-C", str(repo_root), *args],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, p.stdout


def _published_text(repo_root: Path, path_in_repo: str) -> tuple[str | None, str]:
    """The mirror's COMMITTED content, plus a note when CI does not have it yet.

    ⛑ **The worktree file is NOT what the consumer reads.** `sigma-alert` runs only in
    GitHub Actions, which clones the pushed branch -- so the bytes that matter are the
    committed (and pushed) ones. Comparing the working tree lets a local regeneration
    that was never committed certify agreement while CI keeps using the stale copy.
    That is not hypothetical: it was the live state of `sources/sp500_names.json` at the
    moment this check first ran, and Codex caught the module reading the wrong bytes.

    Returns `(None, reason)` when git cannot answer, so the caller can fall back to the
    worktree and SAY that is what it compared.
    """
    code, out = _git(repo_root, "show", f"HEAD:{path_in_repo}")
    if code != 0:
        return None, "not committed at HEAD (or not a git checkout)"

    lag = []
    code, dirty = _git(repo_root, "status", "--porcelain", "--", path_in_repo)
    if code == 0 and dirty.strip():
        lag.append("uncommitted local changes CI will not see")

    code, upstream = _git(repo_root, "rev-parse", "--abbrev-ref",
                          "--symbolic-full-name", "@{u}")
    if code == 0 and upstream.strip():
        code, diff = _git(repo_root, "diff", "--name-only",
                          f"{upstream.strip()}..HEAD", "--", path_in_repo)
        if code == 0 and diff.strip():
            lag.append(f"committed but not pushed to {upstream.strip()}")
    return out, "; ".join(lag)


def _parse_mirror(text: str, fmt: str) -> set[str]:
    """Tickers held by a mirror's contents. Raises ValueError for the caller to catch."""
    if fmt == "lines":
        return {ln.strip().upper() for ln in text.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")}
    if fmt == "names_json":
        doc = json.loads(text)
        if not isinstance(doc, dict):
            raise ValueError(f"expected a JSON object, got {type(doc).__name__}")
        return {str(t).upper() for t in doc}
    raise ValueError(f"unknown mirror format {fmt!r}")


def _snapshot_tickers(key: str, today: date | None = None) -> tuple[set[str] | None, str]:
    """(tickers, reason-if-unusable) for this repo's snapshot of `key`.

    Returns `None` for anything that is not a trustworthy reference. The staleness limit
    is READ from the snapshot (`stale_days`), never hardcoded here -- the fleet has already
    paid once for a local copy of that number drifting from CM's.
    """
    try:
        doc = im.load_latest(key)
    except (OSError, ValueError) as exc:          # Dropbox lock, truncated write
        return None, f"snapshot unreadable ({type(exc).__name__}: {exc})"
    if not doc:
        return None, "no snapshot on disk"

    holdings = doc.get("holdings")
    if not isinstance(holdings, list) or not holdings:
        return None, "snapshot carries no holdings"
    tickers = {str(h["ticker"]).upper() for h in holdings
               if isinstance(h, dict) and h.get("ticker")}
    if not tickers:
        return None, "snapshot holdings carry no tickers"

    # ⛑ THE REFERENCE MUST CLEAR THE INDEX'S OWN CREDIBILITY FLOOR, not merely be
    # non-empty. `MIN_MIRROR_FRACTION` is a fraction OF THE REFERENCE, so a truncated
    # snapshot silently rescales the plausibility test with it -- two equally broken
    # 400-name files would have certified `ok` against a 503-name index. That is the
    # `a-check-cannot-expect-what-it-measures` shape, and `index_membership.SOURCES`
    # already publishes the floor, so it is read from there rather than restated.
    floor = (im.SOURCES.get(key) or {}).get("floor")
    if isinstance(floor, int) and len(tickers) < floor:
        return None, (f"snapshot holds only {len(tickers)} tickers, under the "
                      f"{key} credibility floor of {floor}")

    age = im.snapshot_age_days(doc, today=today)
    if age is None:
        return None, "snapshot carries no usable as_of date"
    limit = doc.get("stale_days")
    if not isinstance(limit, int) or limit <= 0:
        limit = im.stale_days_for(key)
    if age > limit:
        return None, f"snapshot as_of {doc.get('as_of')} is {age}d old (limit {limit}d)"
    return tickers, ""


def check(mirror: Mirror, *, today: date | None = None,
          fleet_root: Path | None = None) -> MirrorResult:
    """Compare one mirror against this repo's snapshot. No network, no writes."""
    root = fleet_root or FLEET_ROOT
    path = root / mirror.repo_relative
    res = MirrorResult(name=mirror.name, path=str(path), index_key=mirror.index_key,
                       status="ok")

    ref, why = _snapshot_tickers(mirror.index_key, today=today)
    if ref is None:
        res.status = "reference_unusable"
        res.detail = f"cannot verify {mirror.index_key}: {why}"
        return res
    res.snapshot_count = len(ref)

    # Prefer the COMMITTED bytes -- they are what the CI-hosted consumer clones. Fall
    # back to the worktree only when git cannot answer, and record which was compared.
    text, lag = _published_text(root / mirror.repo, mirror.path_in_repo)
    if text is not None:
        res.compared, res.publish_lag = "committed", lag
    else:
        if not path.exists():
            res.status = "mirror_absent"
            res.detail = f"{mirror.repo_relative} is not on disk (not checked out?)"
            return res
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            res.status = "mirror_unreadable"
            res.detail = (f"{mirror.repo_relative} unreadable "
                          f"({type(exc).__name__}: {exc})")
            return res
        res.compared = "worktree"
        res.publish_lag = f"compared the working tree: {lag}"

    try:
        got = _parse_mirror(text, mirror.fmt)
    except (ValueError, KeyError, TypeError) as exc:
        res.status = "mirror_unreadable"
        res.detail = f"{mirror.repo_relative} unreadable ({type(exc).__name__}: {exc})"
        return res
    res.mirror_count = len(got)

    if len(got) < len(ref) * MIN_MIRROR_FRACTION:
        res.status = "mirror_implausible"
        res.detail = (f"{len(got)} tickers against a {len(ref)}-name snapshot "
                      f"(under {MIN_MIRROR_FRACTION:.0%}) - reporting this as a broken "
                      f"file, not as {len(ref) - len(got)} departures")
        return res

    res.only_in_mirror = sorted(got - ref)
    res.only_in_snapshot = sorted(ref - got)
    if res.only_in_mirror or res.only_in_snapshot:
        res.status = "drifted"
        res.detail = (f"{len(got)} vs {len(ref)}: "
                      f"{len(res.only_in_mirror)} only in the mirror, "
                      f"{len(res.only_in_snapshot)} only in the snapshot")
    else:
        res.detail = f"{len(got)}/{len(ref)} identical ({res.compared})"
    if res.publish_lag:
        res.detail = f"{res.detail} - {res.publish_lag}"
    return res


def check_all(*, today: date | None = None,
              fleet_root: Path | None = None) -> list[MirrorResult]:
    return [check(m, today=today, fleet_root=fleet_root) for m in MIRRORS]


def summarise(results: list[MirrorResult]) -> str:
    """One line per mirror, for the weekly step's report string."""
    if not results:
        return "no mirrors registered"
    return "; ".join(f"{r.name}: {r.status} ({r.detail})" for r in results)


def main(argv: list[str] | None = None) -> int:
    """Manual entry point: `python -m universe.index_mirrors`.

    Exits 1 when any mirror is not a verified match, so it is usable as a check. The
    WEEKLY step deliberately does not gate on it -- see `_step_index_mirrors`.
    """
    results = check_all()
    for r in results:
        mark = "OK  " if r.status == "ok" else "WARN"
        print(f"[{mark}] {r.name}")
        print(f"       {r.path}")
        print(f"       {r.status}: {r.detail}")
        if r.only_in_mirror:
            print(f"       only in mirror   ({len(r.only_in_mirror)}): "
                  f"{', '.join(r.only_in_mirror[:20])}"
                  f"{' ...' if len(r.only_in_mirror) > 20 else ''}")
        if r.only_in_snapshot:
            print(f"       only in snapshot ({len(r.only_in_snapshot)}): "
                  f"{', '.join(r.only_in_snapshot[:20])}"
                  f"{' ...' if len(r.only_in_snapshot) > 20 else ''}")
    bad = [r for r in results if r.is_problem]
    print(f"\n{len(results) - len(bad)}/{len(results)} mirrors verified identical")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
