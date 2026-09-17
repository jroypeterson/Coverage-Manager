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
    """A fleet root whose sibling repo is a real git checkout.

    Production compares the COMMITTED blob. A bare directory would exercise only the
    worktree fallback and prove nothing about the path that actually runs.
    """
    repo = tmp_path / "sigma-alert"
    (repo / "sources").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
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


def _commit(root):
    repo = root / "sigma-alert"
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "x", "--allow-empty")


def _write_lines(root, tickers, *, name="sp500.txt", commit=True, body=None):
    p = root / "sigma-alert" / "sources" / name
    p.write_text(body if body is not None
                 else "# a comment\n\n" + "\n".join(tickers) + "\n", encoding="utf-8")
    if commit:
        _commit(root)
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
    assert r.compared == "committed"
    assert r.mirror_count == 503 and r.snapshot_count == 503


def test_comments_blank_lines_and_case_do_not_count_as_drift(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    body = "\n".join(t.lower() for t in SNAP_TICKERS)
    _write_lines(root, [], body=f"# header\n#\n\n{body}\n\n")
    assert mir.check(mirror, today=TODAY, fleet_root=root).status == "ok"


# --- it must compare the bytes CI reads, not the working tree --------------------------

def test_an_uncommitted_fix_does_not_certify_agreement(root, mirror, monkeypatch):
    """Codex's P1, reproduced: the exact live state when this module first ran.

    The committed copy is stale and the worktree has been repaired but not committed.
    A worktree comparison says `ok` while CI is still serving the stale list.
    """
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS[:-10] + [f"OLD{i}" for i in range(10)])  # committed
    _write_lines(root, SNAP_TICKERS, commit=False)                           # repaired
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.compared == "committed"
    assert r.status == "drifted", "the committed copy is what CI reads"
    assert r.is_problem


def test_a_committed_but_unstaged_edit_is_flagged_even_when_the_lists_agree(
        root, mirror, monkeypatch):
    """Agreement plus a publish lag is still a problem: CI does not have the bytes."""
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS)
    (root / "sigma-alert" / "sources" / "sp500.txt").write_text(
        "# touched, not committed\n" + "\n".join(SNAP_TICKERS) + "\n", encoding="utf-8")
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.status == "ok"
    assert r.publish_lag and "uncommitted" in r.publish_lag
    assert r.is_problem, "a publish lag must not read as verified"


def test_a_non_git_checkout_falls_back_and_says_so(root, mirror, monkeypatch, tmp_path):
    """Falling back to the worktree is allowed; doing it SILENTLY is not."""
    plain = tmp_path / "plain"
    (plain / "sigma-alert" / "sources").mkdir(parents=True)
    (plain / "sigma-alert" / "sources" / "sp500.txt").write_text(
        "\n".join(SNAP_TICKERS) + "\n", encoding="utf-8")
    _patch_snapshot(monkeypatch, _snapshot())
    r = mir.check(mirror, today=TODAY, fleet_root=plain)
    assert r.compared == "worktree"
    assert r.publish_lag
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
    assert mir.check(mirror, today=TODAY, fleet_root=root).status == "ok"


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
    _write_lines(root, SNAP_TICKERS)              # commit BEFORE git is broken

    def _boom(*a, **k):
        raise OSError("git is not on PATH")
    monkeypatch.setattr(mir.subprocess, "run", _boom)
    _patch_snapshot(monkeypatch, _snapshot())
    r = mir.check(mirror, today=TODAY, fleet_root=root)
    assert r.compared == "worktree"
    assert r.status == "ok" and r.is_problem      # fell back, and said so


def test_every_registered_mirror_names_an_index_we_actually_collect():
    """A mirror keyed to an index this repo does not snapshot could never be verified.

    It would sit at `reference_unusable` for ever and read as a standing alarm nobody
    can clear -- the `a-flag-that-is-always-true` shape.
    """
    known = set(mir.im.SOURCES)
    for m in mir.MIRRORS:
        assert m.index_key in known, f"{m.name} points at unknown index {m.index_key!r}"


def test_every_registered_mirror_declares_why_it_exists():
    """`why` is load-bearing: it is what stops the next reader deleting the file.

    The brief already told one reader to retire sigma-alert/sources/sp500.txt, which
    would have taken the screener offline.
    """
    for m in mir.MIRRORS:
        assert m.why and len(m.why) > 20, f"{m.name} has no usable rationale"


def test_summarise_covers_every_result(root, mirror, monkeypatch):
    _patch_snapshot(monkeypatch, _snapshot())
    _write_lines(root, SNAP_TICKERS)
    results = [mir.check(mirror, today=TODAY, fleet_root=root)]
    assert mirror.name in mir.summarise(results)
