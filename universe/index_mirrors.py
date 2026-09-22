"""Verify the fleet's OTHER copies of an index list against this repo's snapshot.

Board #354, and specifically the one residual its brief could not close.

## UPDATE 2026-09-22 -- the S&P 500 mirror is now WRITTEN by this repo

JP approved retiring sigma-alert's own collector ("retire sigma-alert/sources/sp500.txt").
`reporting/sigma_export.build_sp500_mirror` now writes `sources/sp500.txt` and
`sources/sp500_names.json` from the sp500 snapshot, in the same push as
`ticker_metadata.json`, and sigma-alert's monthly `refresh-sp500.yml` is manual-only.
The committed files remain the transport (CI still cannot read the gitignored snapshot);
only tickers and names cross, exactly what sigma-alert already published from its own
scrape. This module keeps running as the post-write check: a `drifted` result now means
the writer REFUSED (see its guards) or has not pushed yet. The history below is kept
because its constraints still bind the writer.

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

It does not write, fix or normalise the mirror -- it reports. A mirror that has drifted is
a fact for a human; the correct repair depends on which side is wrong, and this module cannot
know that.

It makes **no network call by default**. `fetch=True` is the one exception and it does exactly
one thing: refresh the remote-tracking ref so the comparison is against what the consumer's CI
would clone *now* rather than whenever this machine last fetched. The weekly step passes it
because that step is already networked; everything else keeps the cheap property.

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
    # The branch the consumer's CI actually clones. DECLARED, never inferred from the
    # local checkout's current upstream -- see `_remote_ref`.
    ci_remote: str = "origin"
    ci_branch: str = "master"

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
        why=("CM-written since 2026-09-22 (sigma_export.build_sp500_mirror); sigma-alert "
             "runs only in GitHub Actions and cannot read the gitignored snapshot, and "
             "sync_watchlist.py builds watchlist.txt from it"),
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
                                     # | mirror_implausible | mirror_unverifiable
                                     # | reference_unusable
    detail: str = ""
    mirror_count: int | None = None
    snapshot_count: int | None = None
    only_in_mirror: list[str] = field(default_factory=list)
    only_in_snapshot: list[str] = field(default_factory=list)
    # Which bytes were actually compared, and whether CI is reading them yet.
    compared: str = "pushed"         # "pushed" -- the ref CI clones
    publish_lag: str = ""            # non-empty => a local fix CI does not have
    ref_caveat: str = ""             # non-empty => how current the compared ref itself is

    @property
    def is_problem(self) -> bool:
        """Everything that is not a verified match, including 'could not check'.

        `reference_unusable` counts as a problem on purpose. The check going blind is a
        thing to fix, not a quiet pass -- that distinction is the whole point of the
        module's docstring.

        A publish lag is a problem too, even when the lists agree: the point of this
        module is whether the bytes the CI-hosted screener READS still match, and an
        uncommitted or unpushed fix is one CI does not have.

        ⛑ `ref_caveat` is deliberately NOT a problem. It records how current the compared
        ref is, and on the `fetch=False` library default it is set on EVERY result -- so
        counting it would make `is_problem` permanently true, which is the fleet's own
        `a-flag-that-is-always-true`. Both production callers fetch (pinned by a test), so
        the caveat is empty where it would matter.
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
            "ref_caveat": self.ref_caveat,
        }


def _kill_tree(proc: "subprocess.Popen") -> None:
    """Kill git AND the transport helpers it spawned.

    ⛑ `subprocess.run(timeout=...)` kills only the immediate `git.exe`. On Windows a
    `git fetch` launches `git-remote-https` (or `ssh`), which keeps the captured pipe
    handles open, so the subsequent blocking read can hang **past** the timeout that was
    supposed to bound it -- the weekly build stalled by the diagnostic it does not gate
    on. An interrupted fetch can also leave `.git` lock files that break Coverage
    Manager's own `sigma_export` fetch+rebase later in the same build. (Codex round 3.)
    """
    import os
    try:
        proc.kill()
    except OSError:
        pass
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
    else:                                     # pragma: no cover - POSIX path
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except (OSError, AttributeError):
            pass


def _git(repo_root: Path, *args: str, timeout: int = 30) -> tuple[int, str]:
    """Run one git command in `repo_root`. Never raises; returns (code, stdout).

    ⛑ `GIT_TERMINAL_PROMPT=0` and an empty credential helper are not optional. This runs
    unattended under Windows Task Scheduler, where a git command that decides to ask for
    a username blocks for ever on a console nobody is watching -- a diagnostic that hangs
    the weekly build it is specified not to gate.

    ⛑ The timeout kills the whole PROCESS TREE, not just git. See `_kill_tree`.
    """
    import os
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="",
               GCM_INTERACTIVE="never", GIT_LFS_SKIP_SMUDGE="1")
    flags = {}
    if os.name == "nt":
        flags["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:                                     # pragma: no cover - POSIX path
        flags["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            ["git", "-C", str(repo_root), "-c", "credential.helper=", "--no-pager", *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", env=env, **flags)
    except (OSError, ValueError):
        return 1, ""
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            out = ""
        return 1, out or ""
    except (OSError, ValueError):
        _kill_tree(proc)
        return 1, ""
    return proc.returncode, out or ""


def _remote_ref(mirror: "Mirror") -> str:
    """The fully qualified remote-tracking ref whose content CI clones.

    ⛑ Built from the mirror's DECLARED `ci_remote`/`ci_branch`, never from `@{u}`.
    `@{u}` follows whatever branch the sibling checkout happens to be on: leave
    `sigma-alert` parked on a feature branch and the check would compare that branch's
    copy and report `ok` while CI kept reading a divergent `master`. `--abbrev-ref` also
    drops the namespace, so a local tag named like the branch can make the name
    ambiguous. (Codex round 3.)
    """
    return f"refs/remotes/{mirror.ci_remote}/{mirror.ci_branch}"


def _published_text(repo_root: Path, mirror: "Mirror", path_in_repo: str,
                    *, fetch: bool = False) -> tuple[str | None, str, str]:
    """The mirror's PUSHED content, plus a note for anything CI does not have yet.

    ⛑ **Neither the worktree nor local `HEAD` is what the consumer reads.** `sigma-alert`
    runs only in GitHub Actions, which clones the **pushed branch**. Two rounds of review
    walked this in:

      * Round 1 caught it comparing the **working tree** -- a local regeneration never
        committed would certify agreement while CI served the stale copy. That was the
        live state of `sources/sp500_names.json` when this check first ran.
      * Round 2 caught the fix itself comparing local **HEAD**, which is the same class one
        step out: `sigma-alert`'s own monthly Action pushes to the remote, so this clone's
        HEAD and its remote-tracking ref both go stale with **no local action at all**, and
        the check would certify old bytes. A detached HEAD or a checkout with no upstream
        made it worse -- `@{u}` failed, the failure was ignored, and an unpushed HEAD was
        certified indefinitely.

      * Round 3 caught four more in round 2's own surface: `@{u}` follows whatever
        branch the sibling is parked on rather than the branch CI clones; a bare
        `git fetch origin` returns 0 after the branch is deleted upstream and leaves the
        stale ref in place; a **failed** requested fetch still let that stale ref reach
        the comparison, where a chance match reported `ok`; and the subprocess timeout
        killed only `git.exe`, not the transport helper holding its pipes.

    So the comparison is against a **declared, fully qualified remote-tracking ref**,
    refreshed by an **exact refspec**, and any checkout or fetch that cannot establish it
    is reported `mirror_unverifiable` rather than passing.

    `fetch=True` refreshes that ref first; the weekly caller does, because it is already a
    networked step. The default is False so `check()` keeps its no-network property, and
    the returned note then says the ref was not refreshed this run.
    """
    ref = _remote_ref(mirror)
    code, _ = _git(repo_root, "rev-parse", "--verify", "--quiet", ref)
    if code != 0:
        return None, "", (f"{ref} does not exist in this checkout -- cannot tell which "
                          f"bytes CI has (no remote, wrong branch, or not a git checkout)")

    caveat = ""
    if fetch:
        # ⛑ Fetch the EXACT branch, not the whole remote. A bare `git fetch origin`
        # returns 0 after the branch is renamed or deleted upstream, leaving the stale
        # remote-tracking ref in place -- and the comparison would then certify a blob
        # CI can no longer clone. Naming the refspec makes a deleted branch an error.
        refspec = f"+refs/heads/{mirror.ci_branch}:{ref}"
        code, _ = _git(repo_root, "fetch", "--quiet", mirror.ci_remote, refspec,
                       timeout=90)
        if code != 0:
            # ⛑ A REQUESTED refresh that failed is not a caveat, it is a refusal. The
            # stale ref must not reach the comparison: if it happens to match while the
            # real remote has moved, `status` would be `ok`, `is_problem` False (it
            # ignores `ref_caveat`), the weekly step silent and the CLI exit 0.
            return None, "", (
                f"could not fetch {mirror.ci_remote}/{mirror.ci_branch} -- refusing to "
                f"compare against a ref that is known to be unrefreshed (branch renamed "
                f"or deleted upstream, network, or auth)")
    else:
        caveat = f"{ref} was not refreshed this run and may be behind the real remote"

    code, out = _git(repo_root, "show", f"{ref}:{path_in_repo}")
    if code != 0:
        return None, "", f"not present at {ref} (never pushed, or deleted upstream)"

    lag = []
    code, dirty = _git(repo_root, "status", "--porcelain", "--", path_in_repo)
    if code == 0 and dirty.strip():
        lag.append("uncommitted local changes CI will not see")

    code, diff = _git(repo_root, "diff", "--name-only", f"{ref}..HEAD", "--", path_in_repo)
    if code == 0 and diff.strip():
        lag.append(f"committed locally but not pushed to {mirror.ci_remote}/"
                   f"{mirror.ci_branch}")
    return out, "; ".join(lag), caveat


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
          fleet_root: Path | None = None, fetch: bool = False) -> MirrorResult:
    """Compare one mirror against this repo's snapshot. No writes, ever.

    No network either unless `fetch=True`, which only refreshes the remote-tracking
    ref. The weekly caller passes it; the default keeps the cheap property that makes
    this runnable anywhere.
    """
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

    # ⛑ Compare the PUSHED bytes -- the ref the CI-hosted consumer clones. There is
    # deliberately NO fallback to the working tree or to local HEAD: both were tried and
    # both certified stale bytes as agreement (see `_published_text`). A checkout where
    # the pushed state cannot be established reports that it cannot be established.
    text, lag, caveat = _published_text(root / mirror.repo, mirror,
                                        mirror.path_in_repo, fetch=fetch)
    if text is None:
        res.status = "mirror_unverifiable" if path.exists() else "mirror_absent"
        res.detail = (f"{mirror.repo_relative}: {caveat}"
                      if res.status == "mirror_unverifiable"
                      else f"{mirror.repo_relative} is not on disk and {caveat}")
        return res
    res.publish_lag, res.ref_caveat = lag, caveat

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
    for note in (res.publish_lag, res.ref_caveat):
        if note:
            res.detail = f"{res.detail} - {note}"
    return res


def check_all(*, today: date | None = None, fleet_root: Path | None = None,
              fetch: bool = False) -> list[MirrorResult]:
    return [check(m, today=today, fleet_root=fleet_root, fetch=fetch)
            for m in MIRRORS]


# --- IVV vs Wikipedia: a second-SOURCE line, NON-GATING (2026-09-22) -----------------
#
# Since 2026-09-22 the sp500 snapshot comes from iShares IVV holdings. CM still refreshes
# its Wikipedia cache (`cache/constituents/sp500.json`) for its own use, so every week there
# are two independent sources for one list. Their difference is a fact worth one line, and
# ONLY a line: on a reconstitution week they SHOULD differ until Wikipedia and CM's 7-day
# cache of it catch up, so this is never a `problem`, never flips the step to `failed:`, and
# never touches the CLI's exit code.

_XC_SHOW = 10


def compare_sources(ivv: set[str], wiki: set[str], *, ivv_as_of: str | None,
                    wiki_as_of: str | None) -> dict:
    """Symmetric difference of two ticker sets, plus one ASCII report line."""
    only_ivv, only_wiki = sorted(ivv - wiki), sorted(wiki - ivv)

    def _names(xs):
        more = f", +{len(xs) - _XC_SHOW} more" if len(xs) > _XC_SHOW else ""
        return ", ".join(xs[:_XC_SHOW]) + more

    dates = f"IVV as_of {ivv_as_of}, Wikipedia cache {wiki_as_of}"
    if only_ivv or only_wiki:
        status = "differs"
        body = (f"{len(only_ivv)} only in IVV ({_names(only_ivv)}); "
                f"{len(only_wiki)} only in Wikipedia ({_names(only_wiki)})")
    else:
        status = "agree"
        body = f"{len(ivv)}/{len(wiki)} identical"
    line = f"IVV vs Wikipedia: {status} - {body} [{dates}; non-gating]"
    return {"status": status, "only_in_ivv": only_ivv, "only_in_wikipedia": only_wiki,
            "line": line.encode("ascii", "replace").decode("ascii")}


def source_crosscheck() -> dict:
    """Compare the IVV-derived sp500 snapshot with CM's Wikipedia cache. Never raises.

    Reads two local files; no network, no writes. A snapshot that is not IVV-derived
    (e.g. after a rollback to `cm_cache`) is reported `unavailable`, because comparing a
    Wikipedia-derived list against Wikipedia would be agreement by construction.
    """
    def unavailable(why):
        return {"status": "unavailable", "only_in_ivv": [], "only_in_wikipedia": [],
                "line": f"IVV vs Wikipedia: unavailable - {why} [non-gating]"}

    try:
        doc = im.load_latest("sp500")
        if not isinstance(doc, dict):
            return unavailable("no sp500 snapshot on disk")
        if doc.get("kind") != "ishares":
            return unavailable(f"sp500 snapshot kind is {doc.get('kind')!r}, not IVV")
        ivv = {str(h.get("ticker")).upper() for h in doc.get("holdings") or []
               if isinstance(h, dict) and h.get("ticker")}
        if not ivv:
            return unavailable("sp500 snapshot carries no tickers")
        stamp, rows = im._load_cm_sp500()
        wiki = {str(r["ticker"]).upper() for r in rows if r.get("ticker")}
    except Exception as exc:                          # noqa: BLE001 - a line, not a crash
        return unavailable(f"{type(exc).__name__}: {exc}")
    return compare_sources(ivv, wiki, ivv_as_of=doc.get("as_of"), wiki_as_of=stamp)


def summarise(results: list[MirrorResult]) -> str:
    """One line per mirror, for the weekly step's report string."""
    if not results:
        return "no mirrors registered"
    return "; ".join(f"{r.name}: {r.status} ({r.detail})" for r in results)


def main(argv: list[str] | None = None) -> int:
    """Manual entry point: `python -m universe.index_mirrors [--no-fetch]`.

    Exits 1 when any mirror is not a verified match, so it is usable as a check. The
    WEEKLY step deliberately does not gate on it -- see `_step_index_mirrors`.
    """
    # ⛑ The CLI FETCHES BY DEFAULT, and `--no-fetch` opts out. The other way round made
    # every ordinary manual run report "the ref was not refreshed, this may be stale" and
    # exit 1 -- true, useless, and `a-flag-that-is-always-true`: a warning on the common
    # path is one the reader stops seeing. The library default stays False so importing
    # this module still costs no network.
    results = check_all(fetch="--no-fetch" not in (argv or []))
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
    # Informational only -- deliberately NOT part of the exit code (see compare_sources).
    print(source_crosscheck()["line"])
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
