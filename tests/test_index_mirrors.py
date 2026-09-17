"""Tests for `universe/index_mirrors.py` (board #354).

Two properties matter most here, and both are negatives.

**A comparison whose reference is missing, stale or short must report "cannot verify",
never "clean"** -- `feedback_a_check_cannot_expect_what_it_measures`, and the fleet has
shipped that shape inside the fix for it before.

**The bytes compared must be the ones the CONSUMER reads.** `sigma-alert` runs only in
GitHub Actions, which clones the pushed branch, so the working-tree file is not the
artifact. Codex caught the first version reading it anyway, and demonstrated the defect
against live state: an uncommitted regeneration matched 503/503 in the worktree while
`HEAD` -- the copy CI actually had -- differed by ten names in each direction.

Every test builds its own fleet root under tmp_path, with a REAL git checkout for the
sibling repo, so none of them touches the real sigma-alert clone or the real snapshots.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date

import pytest

from universe import index_mirrors as mir


SNAP_TICKERS = [f"T{i:03d}" for i in range(503)]
TODAY = date(2026, 9, 16)
LINES_BODY = "\n".join(SNAP_TICKERS) + "\n"


def _snapshot(tickers=None, *, as_of="2026-09-08", stale_days=45, holdings=None):
    if holdings is None:
        holdings = [{"ticker": t, "name": f"Co {t}"} for t in (tickers or SNAP_TICKERS)]
    return {"schema_version": 3, "key": "sp500", "index": "S&P 500",
            "kind": "cm_cache", "as_of": as_of, "stale_days": stale_days,
            "count": len(holdings), "holdings": holdings}


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def root(tmp_path):
    """A fleet root whose sibling repo is a real checkout WITH AN UPSTREAM.

    Production compares the blob at the remote-tracking ref, because that is what the
    consumer's CI clones. A checkout with no upstream is a legitimate state and gets its
    own test -- but the ordinary fixture must have one, or every test here would exercise
    the unverifiable branch and prove nothing about the path that actually runs.
    """
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(bare)],
                   check=True, capture_output=True)
    repo = tmp_path / "sigma-alert"
    (repo / "sources").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)],
                   check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "commit", "-q", "--allow-empty", "-m", "root")
    _git(repo, "push", "-q", "-u", "origin", "master")
    return tmp_path


@pytest.fixture
def mirror():
    return mir.Mirror(name="test mirror", repo="sigma-alert",
                      path_in_repo="sources/sp500.txt",
                      index_key="sp500", fmt="lines", why="test")


@pytest.fixture
def names_mirror():
    return mir.Mirror(name="names", repo="sigma-alert",
                      path_in_repo="sources/sp500_names.json",
                      index_key="sp500", fmt="names_json", why="test")


def _commit(root, *, push=True):
    """Commit, and by default PUSH -- the pushed ref is what production compares."""
    repo = root / "sigma-alert"
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "x", "--allow-empty")
    if push:
        _git(repo, "push", "-q", "origin", "master")


def _write_lines(root, tickers, *, name="sp500.txt", commit=True, body=None,
                 push=True):
    p = root / "sigma-alert" / "sources" / name
    p.write_text(body if body is not None
                 else "# a comment\n\n" + "\n".join(tickers) + "\n", encoding="utf-8")
    if commit:
        _commit(root, push=push)
    return p


def _patch_snapshot(monkeypatch, doc):
    monkeypatch.setattr(mir.im, "load_latest", lambda key: doc)


# --- the happy path, so the negatives below mean something ----------------------------

def test_identical_lists_report_ok(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS)
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "ok"
    assert not r.is_problem
    assert r.compared == "pushed"
    assert r.mirror_count == 503 and r.snapshot_count == 503


def test_comments_blank_lines_and_case_do_not_count_as_drift(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    body = "\n".join(t.lower() for t in SNAP_TICKERS)
    _write_lines(root, [], body=f"# header\n#\n\n{body}\n\n")
    assert mir.check(mirror, today=TODAY, fleet_root=root).status == "ok"


# --- it must compare the bytes CI reads, not the working tree --------------------------

def test_an_uncommitted_fix_does_not_certify_agreement(root, mirror, monkeypatch):
    """Codex round 1, reproduced: the exact live state when this module first ran.

    The pushed copy is stale and the worktree has been repaired but not committed.
    A worktree comparison says `ok` while CI is still serving the stale list.
    """
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS[:-10] + [f"OLD{i}" for i in range(10)])  # pushed
    _write_lines(root, SNAP_TICKERS, commit=False)                           # repaired
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "drifted", "the pushed copy is what CI reads"
    assert r.is_problem


def test_a_committed_but_unpushed_fix_does_not_certify_agreement(root, mirror, monkeypatch):
    """Codex round 2: the same class one step out, and it was INSIDE round 1's fix.

    Comparing local HEAD instead of the pushed ref certifies a repair CI has never seen.
    sigma-alert's own monthly Action also advances that remote with no local action at
    all, so this clone goes stale by itself.
    """
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS[:-10] + [f"OLD{i}" for i in range(10)])   # pushed
    _write_lines(root, SNAP_TICKERS, push=False)                              # local only
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "drifted", "an unpushed commit is not what CI reads"
    assert "not pushed" in r.publish_lag
    assert r.is_problem


def test_no_upstream_is_unverifiable_not_ok(root, mirror, monkeypatch, tmp_path):
    """A checkout with no upstream cannot say what CI has, so it must not say `ok`.

    Round 2 caught this too: `@{u}` failed, the failure was ignored, and an unpushed
    HEAD was certified indefinitely.
    """
    plain = tmp_path / "noupstream"
    repo = plain / "sigma-alert"
    (repo / "sources").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)],
                   check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "sources" / "sp500.txt").write_text(LINES_BODY, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "x")
    _patch_snapshot(monkeypatch, _snapshot())
    r = mir.check(mirror, today=TODAY, fleet_root=plain)
    assert r.status == "mirror_unverifiable"
    assert r.is_problem


def test_an_unstaged_edit_is_flagged_even_when_the_lists_agree(root, mirror,
                                                              monkeypatch):
    """Agreement plus a publish lag is still a problem: CI does not have the bytes."""
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS)
    (root / "sigma-alert" / "sources" / "sp500.txt").write_text(
        "# touched, not committed\n" + "\n".join(SNAP_TICKERS) + "\n", encoding="utf-8")
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "ok"
    assert r.publish_lag and "uncommitted" in r.publish_lag
    assert r.is_problem, "a publish lag must not read as verified"


def test_a_non_git_directory_is_unverifiable_not_ok(root, mirror, monkeypatch, tmp_path):
    """There is deliberately NO worktree fallback: both fallbacks certified stale bytes."""
    plain = tmp_path / "plain"
    (plain / "sigma-alert" / "sources").mkdir(parents=True)
    (plain / "sigma-alert" / "sources" / "sp500.txt").write_text(LINES_BODY,
                                                                 encoding="utf-8")
    _patch_snapshot(monkeypatch, _snapshot())
    r = mir.check(mirror, today=TODAY, fleet_root=plain)
    assert r.status == "mirror_unverifiable"
    assert r.is_problem


# --- drift, which is the thing it is for ----------------------------------------------

def test_drift_is_reported_with_both_directions(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS[:-2] + ["ZZZA", "ZZZB"])
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "drifted"
    assert r.only_in_mirror == ["ZZZA", "ZZZB"]
    assert r.only_in_snapshot == SNAP_TICKERS[-2:]
    assert r.is_problem


def test_a_single_name_difference_is_not_rounded_away(root, mirror, monkeypatch):
    """The real defect this module found was 10 names in 503. One must also fire."""
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS[:-1] + ["NEWNAME"])
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "drifted"
    assert r.only_in_mirror == ["NEWNAME"]


# --- the negatives: none of these may report ok ---------------------------------------

def test_missing_snapshot_cannot_verify_rather_than_pass(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, None)
    _write_lines(root, SNAP_TICKERS)
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "reference_unusable"
    assert r.is_problem
    assert "no snapshot" in r.detail


def test_stale_snapshot_cannot_verify(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot(as_of="2026-01-01", stale_days=45))
    _write_lines(root, SNAP_TICKERS)
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "reference_unusable"
    assert "old" in r.detail


def test_stale_limit_is_read_from_the_snapshot_not_hardcoded(root, mirror, monkeypatch):
    """A 200-day-old snapshot that DECLARES stale_days 365 is usable.

    The fleet has already paid once for a local copy of this number drifting from CM's
    (the Russell 120d vs 45d case). Pinned so a future edit cannot reintroduce a
    hardcoded limit without failing.
    """
    _patch_snapshot(monkeypatch, _snapshot(as_of="2026-03-01", stale_days=365))
    _write_lines(root, SNAP_TICKERS)
    assert mir.check(mirror, today=TODAY, fleet_root=root).status == "ok"


def test_a_reference_under_the_index_floor_cannot_verify(root, mirror, monkeypatch):
    """Codex's other P1, and it is the module's own stated failure mode.

    `MIN_MIRROR_FRACTION` is a fraction OF THE REFERENCE, so a truncated snapshot
    rescales the plausibility test along with it -- two equally broken 420-name files
    would certify `ok` against a 503-name index. The floor comes from
    `index_membership.SOURCES`, so it cannot drift from the collector's own rule.
    """
    short = SNAP_TICKERS[:420]                    # under sp500's floor of 450
    _patch_snapshot(monkeypatch, _snapshot(tickers=short))
    _write_lines(root, short)                     # mirror agrees perfectly
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "reference_unusable"
    assert "floor" in r.detail
    assert r.is_problem


def test_a_reference_just_above_the_floor_is_still_usable(root, mirror, monkeypatch):
    """The floor must not become the outage -- 460 names is short but credible."""
    ok_size = SNAP_TICKERS[:460]
    _patch_snapshot(monkeypatch, _snapshot(tickers=ok_size))
    _write_lines(root, ok_size)
    assert mir.check(mirror, today=TODAY, fleet_root=root).status == "ok"


def test_snapshot_with_no_as_of_cannot_verify(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot(as_of=""))
    _write_lines(root, SNAP_TICKERS)
    assert mir.check(mirror, today=TODAY,
                     fleet_root=root).status == "reference_unusable"


def test_snapshot_with_no_holdings_cannot_verify(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot(holdings=[]))
    _write_lines(root, SNAP_TICKERS)
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "reference_unusable"
    assert "no holdings" in r.detail


def test_unreadable_snapshot_cannot_verify(root, mirror, monkeypatch):
    """A Dropbox lock is WinError 5 -- the most common IO failure in this workspace.

    It must land as `reference_unusable`, not escape the module. A sibling reader in
    screens_equity had exactly this bug (OSError missing from a catch tuple) and it
    killed the daily lane.
    """
    def _boom(key):
        raise OSError(5, "Access is denied")
    monkeypatch.setattr(mir.im, "load_latest", _boom)
    _write_lines(root, SNAP_TICKERS)
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "reference_unusable"
    assert "unreadable" in r.detail


def test_absent_mirror_is_not_agreement(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "mirror_absent"
    assert r.is_problem


def test_a_mirror_deleted_from_the_repo_is_absent_not_empty(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS)
    (root / "sigma-alert" / "sources" / "sp500.txt").unlink()
    _commit(root)
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "mirror_absent"


def test_half_written_mirror_reports_broken_not_departures(root, mirror, monkeypatch):
    """A truncated refresh must not render as 300 index departures.

    This is the difference between "the file is broken" and "the index changed", and
    only the first is true. Reporting the second would send a reader hunting a
    corporate-action story that does not exist.
    """
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS[:200])
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "mirror_implausible"
    assert r.only_in_snapshot == []           # deliberately NOT reported as departures


def test_an_empty_mirror_is_implausible_not_total_drift(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, [], body="# nothing\n")
    assert mir.check(mirror, today=TODAY,
                     fleet_root=root).status == "mirror_implausible"


def test_a_mirror_just_above_the_floor_is_compared_normally(root, mirror, monkeypatch):
    """The plausibility floor must not swallow a genuine large-but-credible drift."""
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS[:450])
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "drifted"
    assert len(r.only_in_snapshot) == 53


# --- the names_json format -------------------------------------------------------------

def test_names_json_mirror_compares_its_keys(root, names_mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    p = root / "sigma-alert" / "sources" / "sp500_names.json"
    p.write_text(json.dumps({t: f"Co {t}" for t in SNAP_TICKERS}), encoding="utf-8")
    _commit(root)
    assert mir.check(names_mirror, today=TODAY, fleet_root=root).status == "ok"


def test_names_json_that_is_a_list_is_unreadable_not_empty(root, names_mirror,
                                                           monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    p = root / "sigma-alert" / "sources" / "sp500_names.json"
    p.write_text(json.dumps(SNAP_TICKERS), encoding="utf-8")
    _commit(root)
    assert mir.check(names_mirror, today=TODAY,
                     fleet_root=root).status == "mirror_unreadable"


def test_names_json_that_is_not_json_is_unreadable(root, names_mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    p = root / "sigma-alert" / "sources" / "sp500_names.json"
    p.write_text("{not json", encoding="utf-8")
    _commit(root)
    assert mir.check(names_mirror, today=TODAY,
                     fleet_root=root).status == "mirror_unreadable"


# --- module-level properties ------------------------------------------------------------

def test_check_makes_no_network_call(root, mirror, monkeypatch):
    """The cheapness is the reason this can run weekly. Pin it."""
    import urllib.request

    def _forbidden(*a, **k):
        raise AssertionError("index_mirrors must never make a network call")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    monkeypatch.setattr(mir.im, "collect", _forbidden)
    monkeypatch.setattr(mir.im, "refresh", _forbidden)
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS)
    # fetch defaults to False -- the ONE network call this module can make is opt-in.
    assert mir.check(mirror, today=TODAY, fleet_root=root).status == "ok"


def test_fetch_is_opt_in_and_off_by_default(root, mirror, monkeypatch):
    """The library default must make no network call.

    ⛑ This test was VACUOUS for one commit. It spied on `subprocess.run`, and when
    `_git` switched to `subprocess.Popen` for the process-tree kill, the spy stopped
    observing anything -- `calls` was always empty, so the assertion passed however the
    default behaved. `a-check-that-silently-matches-nothing`, caught by Codex, and the
    third time tonight one of my own tests went stale inside my own fix. It now spies on
    `_git`, which is the seam the behaviour actually crosses and does not move when the
    subprocess API underneath it does.
    """
    calls = []
    real = mir._git

    def _spy(repo_root, *args, **kw):
        calls.append(args)
        return real(repo_root, *args, **kw)

    _write_lines(root, SNAP_TICKERS)
    _patch_snapshot(monkeypatch, _snapshot())
    monkeypatch.setattr(mir, "_git", _spy)
    mir.check(mirror, today=TODAY, fleet_root=root)
    assert calls, "the spy must actually observe _git, or this test proves nothing"
    assert not any(a and a[0] == "fetch" for a in calls), (
        f"the library default must make no network call; saw {calls}")



def test_check_writes_nothing(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS)
    src = root / "sigma-alert" / "sources"
    before = {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()}
    mir.check(mirror, today=TODAY, fleet_root=root)
    after = {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()}
    assert before == after


def test_a_git_failure_never_escapes(root, mirror, monkeypatch):
    """A diagnostic that crashes is an outage. The weekly step calls this."""
    _write_lines(root, SNAP_TICKERS)          # commit+push BEFORE git is broken

    def _boom(*a, **k):
        raise OSError("git is not on PATH")
    monkeypatch.setattr(mir.subprocess, "Popen", _boom)
    _patch_snapshot(monkeypatch, _snapshot())
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "mirror_unverifiable"      # could not ask, so did not claim
    assert r.is_problem


def test_git_is_invoked_non_interactively(root, mirror, monkeypatch):
    """Unattended under Task Scheduler, a git credential prompt blocks for ever.

    The weekly build must not be hangable by a diagnostic it does not gate on.
    """
    seen = {}
    real = mir.subprocess.Popen

    def _spy(cmd, **kw):
        seen.setdefault("env", kw.get("env") or {})
        seen.setdefault("stdin", kw.get("stdin"))
        seen.setdefault("cmd", cmd)
        return real(cmd, **kw)

    _write_lines(root, SNAP_TICKERS)          # fixture git runs BEFORE the spy
    _patch_snapshot(monkeypatch, _snapshot())
    monkeypatch.setattr(mir.subprocess, "Popen", _spy)
    mir.check(mirror, today=TODAY, fleet_root=root)
    assert seen["env"].get("GIT_TERMINAL_PROMPT") == "0"
    assert seen["stdin"] is mir.subprocess.DEVNULL
    assert "credential.helper=" in seen["cmd"]


def test_a_hung_git_kills_the_whole_process_tree(root, mirror, monkeypatch):
    """Codex round 3: `run(timeout=)` kills git.exe and not its transport helper.

    On Windows `git fetch` spawns `git-remote-https`, which holds the captured pipes --
    so the read after the timeout can block PAST the bound that was supposed to stop it,
    hanging the weekly build. The kill must reach the tree.
    """
    killed = []
    monkeypatch.setattr(mir, "_kill_tree", lambda proc: killed.append(proc))

    class _Hanging:
        pid = 4242
        returncode = None

        def __init__(self, *a, **k):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise mir.subprocess.TimeoutExpired(cmd="git", timeout=timeout or 0)
            return ("", "")

    monkeypatch.setattr(mir.subprocess, "Popen", _Hanging)
    code, out = mir._git(root, "fetch", timeout=1)
    assert code == 1 and out == ""
    assert killed, "the timeout must kill the process tree, not just git"


def test_a_failed_requested_fetch_refuses_to_compare(root, mirror, monkeypatch):
    """Codex round 3, and it is the module's own founding rule.

    With `fetch=True` the caller asked for a current ref. If that refresh fails, the
    stale ref must NOT reach the comparison: a chance match would report `ok`, and
    `is_problem` ignores `ref_caveat`, so the weekly step would be silent and the CLI
    would exit 0 while the real remote had moved.
    """
    _write_lines(root, SNAP_TICKERS)
    _patch_snapshot(monkeypatch, _snapshot())
    real = mir._git

    def _fail_fetch(repo_root, *args, **kw):
        if args and args[0] == "fetch":
            return 1, ""
        return real(repo_root, *args, **kw)

    monkeypatch.setattr(mir, "_git", _fail_fetch)
    r = mir.check(mirror, today=TODAY, fleet_root=root, fetch=True)
    assert r.status == "mirror_unverifiable", (
        "a refresh that was requested and failed is a refusal, not a caveat")
    assert r.is_problem


def test_the_fetch_names_the_exact_branch_not_just_the_remote(root, mirror, monkeypatch):
    """Codex round 3: a bare `git fetch origin` returns 0 after the branch is deleted
    upstream and leaves the stale remote-tracking ref in place, so the comparison would
    certify a blob CI can no longer clone. Naming the refspec makes that an error.
    """
    _write_lines(root, SNAP_TICKERS)
    _patch_snapshot(monkeypatch, _snapshot())
    seen = []
    real = mir._git

    def _spy(repo_root, *args, **kw):
        if args and args[0] == "fetch":
            seen.append(args)
        return real(repo_root, *args, **kw)

    monkeypatch.setattr(mir, "_git", _spy)
    mir.check(mirror, today=TODAY, fleet_root=root, fetch=True)
    assert seen, "fetch=True must actually fetch"
    assert any("refs/heads/" in a and mirror.ci_branch in a
               for args in seen for a in args if isinstance(a, str)), (
        f"the fetch must name an explicit refspec for {mirror.ci_branch}; got {seen}")


def test_the_compared_ref_is_declared_not_taken_from_the_local_upstream(root, monkeypatch):
    """Codex round 3: `@{u}` follows whatever branch the sibling is parked on.

    Park the sibling on a feature branch whose copy matches, while the branch CI clones
    still holds the stale one. The old `@{u}` resolution reported `ok`; the declared ref
    must report the drift.
    """
    m = mir.Mirror(name="test mirror", repo="sigma-alert",
                   path_in_repo="sources/sp500.txt", index_key="sp500",
                   fmt="lines", why="test", ci_branch="master")
    _patch_snapshot(monkeypatch, _snapshot())
    repo = root / "sigma-alert"
    _write_lines(root, SNAP_TICKERS[:-10] + [f"OLD{i}" for i in range(10)])  # on master
    _git(repo, "checkout", "-q", "-b", "feature")
    _write_lines(root, SNAP_TICKERS, commit=False)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feature fix")
    _git(repo, "push", "-q", "-u", "origin", "feature")

    r = mir.check(m, today=TODAY, fleet_root=root)
    assert r.status == "drifted", (
        "the declared CI branch is master; the feature branch's copy must not certify it")


def test_every_mirror_declares_the_branch_its_consumer_clones():
    for m in mir.MIRRORS:
        assert m.ci_remote and m.ci_branch, f"{m.name} does not declare its CI branch"


def test_both_production_callers_fetch_before_comparing(monkeypatch):
    """`is_problem` ignores `ref_caveat` ONLY because production refreshes the ref.

    That reasoning is written into `MirrorResult.is_problem`'s docstring as "pinned by a
    test", and until this existed it was not: a mutation making the CLI stop fetching
    left all 33 tests green. A comment claiming a guard exists, with no guard, is the
    `a-green-test-can-name-a-thing-that-is-missing` shape.

    ⛑ The FIRST version of this test asserted `"check_all(fetch=" in source`, which the
    mutant `check_all(fetch=False)` also satisfies -- it survived a second time. Assert
    the VALUE that reaches the callee, never the shape of the call.
    """
    from pathlib import Path as _P

    seen = []
    monkeypatch.setattr(mir, "check_all", lambda **kw: seen.append(kw.get("fetch")) or [])
    mir.main([])
    mir.main(["--no-fetch"])
    assert seen == [True, False], (
        f"CLI must fetch by default and opt out with --no-fetch; got {seen}")

    weekly = (_P(mir.__file__).resolve().parent.parent / "weekly_universe.py").read_text(
        encoding="utf-8")
    step = weekly.split("def _step_index_mirrors")[1].split("\ndef ")[0]
    assert "check_all(fetch=True)" in step, (
        "the weekly step must refresh the remote-tracking ref before comparing")
