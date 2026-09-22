"""Tests for the sigma-alert specific metadata builder.

The generic universe artifact builder lives in `universe/artifacts.py` and is
covered by `test_export_artifacts.py`. This file covers the sigma-alert path,
which composes the generic builder with hardcoded sector ETFs that the
sigma-alert watchlist needs but the coverage universe does not contain.
"""

import csv
import json
import subprocess

import pytest

def _mark_held(path, *tickers):
    """Mark rows held the way PRODUCTION does -- by writing the derived column.

    Deliberately not a `held=` kwarg on `pos.add`: ownership must not be
    authorable through the human-facing path, which is the entire point of the
    2026-08-23 change. Tests earn the state the same way the sync does.
    """
    from universe import positions as _pos

    entries = _pos.load(path)
    for e in entries:
        if e["Ticker"] in tickers:
            e["Held"] = "Y"
            e["Held As Of"] = "2026-08-22"
    _pos.save(entries, path)


from reporting import sigma_export
from reporting.sigma_export import (
    CORE_WATCHLIST_FILENAME,
    SECTOR_ETFS,
    build_core_watchlist_payload,
    build_sigma_metadata,
    export_and_push,
)
from universe.artifacts import build_universe_metadata


@pytest.fixture(autouse=True)
def _no_real_sp500_snapshot(monkeypatch):
    """Hermetic: export_and_push must not read CM's real sp500 snapshot here.
    The S&P 500 path is covered by tests/test_sigma_sp500_mirror.py."""
    monkeypatch.setattr(sigma_export, "_load_sp500_snapshot", lambda: None)


@pytest.fixture
def fixture_csv(tmp_path):
    csv_path = tmp_path / "coverage_universe_tickers.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Ticker": "AAPL",
                "Exchange": "NASDAQ",
                "Company Name": "Apple Inc",
                "Sector (JP)": "Tech",
                "Subsector (JP)": "Hardware",
            }
        )
        writer.writerow(
            {
                "Ticker": "MRNA",
                "Exchange": "NASDAQ",
                "Company Name": "Moderna Inc",
                "Sector (JP)": "Biopharma",
                "Subsector (JP)": "Biotech",
            }
        )
    return csv_path


def test_sigma_metadata_includes_all_etfs(fixture_csv):
    """build_sigma_metadata must augment the generic universe with every ETF
    in SECTOR_ETFS — that's the whole reason it exists."""
    metadata = build_sigma_metadata(fixture_csv)
    for etf_ticker in SECTOR_ETFS:
        assert etf_ticker in metadata, f"Missing sigma-alert ETF {etf_ticker}"


def test_sigma_metadata_is_superset_of_generic(fixture_csv):
    """build_sigma_metadata must contain everything build_universe_metadata
    contains, plus exactly the SECTOR_ETFS."""
    generic = build_universe_metadata(fixture_csv)
    sigma = build_sigma_metadata(fixture_csv)

    # Every CSV ticker is preserved
    for ticker, info in generic.items():
        assert sigma[ticker] == info

    # The only additions are the sigma-alert ETFs
    additions = set(sigma.keys()) - set(generic.keys())
    assert additions == set(SECTOR_ETFS.keys())


def test_sigma_metadata_does_not_overwrite_existing_csv_ticker(tmp_path):
    """If the CSV happens to contain a ticker symbol that collides with an
    ETF (e.g., the universe lists XBI for some reason), the CSV row wins —
    sigma augmentation must not clobber it."""
    csv_path = tmp_path / "collision.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Exchange", "Company Name", "Sector (JP)", "Subsector (JP)"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Ticker": "XBI",
                "Exchange": "NYSEARCA",
                "Company Name": "XBI From Coverage CSV",
                "Sector (JP)": "Biopharma",
                "Subsector (JP)": "ETF",
            }
        )

    sigma = build_sigma_metadata(csv_path)
    # CSV value, not the hardcoded ETF tuple
    assert sigma["XBI"]["name"] == "XBI From Coverage CSV"
    assert sigma["XBI"]["sector"] == "Biopharma"


# ── Core watchlist export ──────────────────────────────────────────────


def test_build_core_watchlist_payload_joins_universe_metadata(
    monkeypatch, tmp_path, fixture_csv,
):
    """The legacy payload (back-compat shim) must include buy/target/notes
    plus name/sector from the universe."""
    from universe import positions as pos

    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    from universe import watchlist as wl
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)
    pos.add(
        "AAPL", position="Researching", notes="core long",
        path=pos_csv, universe_csv_path=fixture_csv, today="2026-04-11",
    )

    _mark_held(pos_csv, "AAPL")

    payload = build_core_watchlist_payload(fixture_csv)
    assert set(payload.keys()) == {"AAPL"}
    entry = payload["AAPL"]
    # Sell Price -> Target Price in the legacy shape
    assert entry["notes"] == "core long"
    assert entry["name"] == "Apple Inc"
    assert entry["sector"] == "Tech"
    assert entry["subsector"] == "Hardware"


def test_build_portfolio_payload_filters_to_portfolio_rows(
    monkeypatch, tmp_path, fixture_csv,
):
    """build_portfolio_payload includes HELD rows -- ownership is derived from
    the broker feed, not from a typed Position. The published `position` field
    still reads "Portfolio" so the artifact contract is unchanged."""
    from universe import positions as pos
    from reporting.sigma_export import build_portfolio_payload, build_researching_payload

    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    pos.add("AAPL", position="Researching", path=pos_csv, universe_csv_path=fixture_csv)
    pos.add("MRNA", position="Researching", path=pos_csv, universe_csv_path=fixture_csv)
    _mark_held(pos_csv, "AAPL")

    portfolio = build_portfolio_payload(fixture_csv)
    researching = build_researching_payload(fixture_csv)
    assert set(portfolio.keys()) == {"AAPL"}
    assert set(researching.keys()) == {"MRNA"}
    assert portfolio["AAPL"]["position"] == "Portfolio"
    assert researching["MRNA"]["position"] == "Researching"


def test_export_and_push_writes_all_seven_files(monkeypatch, tmp_path, fixture_csv):
    """export_and_push must write ticker_metadata.json + core_watchlist.json
    + portfolio.json + researching.json + following_for_interest.json +
    ready_to_buy.json + ready_to_short.json. Git operations are stubbed."""
    from universe import positions as pos

    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    from universe import watchlist as wl
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)
    pos.add("MRNA", position="Researching", path=pos_csv, universe_csv_path=fixture_csv, today="2026-04-11")
    pos.add("AAPL", position="Ready to Buy", path=pos_csv, universe_csv_path=fixture_csv, today="2026-05-08")
    _mark_held(pos_csv, "MRNA")

    target_dir = tmp_path / "sigma-alert"
    target_dir.mkdir()
    (target_dir / ".git").mkdir()

    calls = []

    def fake_git(cwd, *args):
        calls.append(args)
        if args[:3] == ("diff", "--cached", "--quiet"):
            return "", 1
        return "", 0

    monkeypatch.setattr(sigma_export, "_git", fake_git)

    result = export_and_push(fixture_csv, target_dir=target_dir, push=False)

    assert (target_dir / "ticker_metadata.json").exists()
    assert (target_dir / CORE_WATCHLIST_FILENAME).exists()
    assert (target_dir / "portfolio.json").exists()
    assert (target_dir / "researching.json").exists()
    assert (target_dir / "following_for_interest.json").exists()
    assert (target_dir / "ready_to_buy.json").exists()
    assert (target_dir / "ready_to_short.json").exists()

    portfolio_payload = json.loads((target_dir / "portfolio.json").read_text())
    assert "MRNA" in portfolio_payload
    assert portfolio_payload["MRNA"]["position"] == "Portfolio"
    assert portfolio_payload["MRNA"]["sector"] == "Biopharma"

    researching_payload = json.loads((target_dir / "researching.json").read_text())
    # MRNA's stored intent is `Researching`, but it is HELD -- and the exported
    # lists stay mutually exclusive, exactly as they were when Position was a
    # single value. A held name appears in portfolio.json only.
    assert researching_payload == {}

    following_payload = json.loads((target_dir / "following_for_interest.json").read_text())
    assert following_payload == {}  # no Following-for-Interest rows

    rtb_payload = json.loads((target_dir / "ready_to_buy.json").read_text())
    assert "AAPL" in rtb_payload
    assert rtb_payload["AAPL"]["position"] == "Ready to Buy"

    rts_payload = json.loads((target_dir / "ready_to_short.json").read_text())
    assert rts_payload == {}  # no Ready-to-Short rows

    # Confirm all seven files were `git add`-ed
    add_calls = [c for c in calls if c and c[0] == "add"]
    added_files = {c[1] for c in add_calls}
    assert added_files == {
        "ticker_metadata.json", CORE_WATCHLIST_FILENAME,
        "portfolio.json", "researching.json",
        "following_for_interest.json",
        "ready_to_buy.json", "ready_to_short.json",
    }

    assert result["status"] == "committed"
    assert result["watchlist_entries"] == 1
    assert result["portfolio_entries"] == 1
    assert result["researching_entries"] == 0
    assert result["following_for_interest_entries"] == 0
    assert result["ready_to_buy_entries"] == 1
    assert result["ready_to_short_entries"] == 0


def _real_clone(tmp_path):
    """A sigma-alert clone with a real bare origin, so push/rebase actually run."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(bare)],
                   check=True, capture_output=True)
    repo = tmp_path / "sigma-alert"
    repo.mkdir()
    for args in (["init", "-q", "-b", "master"], ["config", "user.email", "t@t.t"],
                 ["config", "user.name", "t"], ["remote", "add", "origin", str(bare)],
                 ["commit", "-q", "--allow-empty", "-m", "root"],
                 ["push", "-q", "-u", "origin", "master"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return repo, bare


def test_a_failed_push_is_retried_on_the_NEXT_run_even_when_nothing_changed(
        monkeypatch, tmp_path, fixture_csv):
    """A transient push failure left the commit local. The next run rebased, found the
    worktree already carrying the new bytes, reported `unchanged` and never pushed --
    so origin, and every sigma-alert CI job cloning it, served the old list for ever."""
    from universe import positions as pos

    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    from universe import watchlist as wl
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)

    repo, bare = _real_clone(tmp_path)
    real_git = sigma_export._git

    def no_push(cwd, *args):
        if args and args[0] == "push":
            return "", 1
        return real_git(cwd, *args)

    monkeypatch.setattr(sigma_export, "_git", no_push)
    first = export_and_push(fixture_csv, target_dir=repo, push=True)
    assert first["status"] == "committed_not_pushed"
    assert subprocess.run(["git", "-C", str(bare), "show", "master:ticker_metadata.json"],
                          capture_output=True).returncode != 0

    monkeypatch.setattr(sigma_export, "_git", real_git)
    second = export_and_push(fixture_csv, target_dir=repo, push=True)
    assert second["status"] == "pushed", second
    assert subprocess.run(["git", "-C", str(bare), "show", "master:ticker_metadata.json"],
                          capture_output=True).returncode == 0


def test_an_up_to_date_clone_still_reports_unchanged(monkeypatch, tmp_path, fixture_csv):
    """The catch-up push must not fire on an ordinary quiet week."""
    from universe import positions as pos

    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    from universe import watchlist as wl
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)

    repo, _ = _real_clone(tmp_path)
    assert export_and_push(fixture_csv, target_dir=repo, push=True)["status"] == "pushed"
    assert export_and_push(fixture_csv, target_dir=repo, push=True)["status"] == "unchanged"


def _positions_stub(monkeypatch, tmp_path):
    from universe import positions as pos
    from universe import watchlist as wl

    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)


def test_a_FOREIGN_local_commit_is_never_pushed_by_the_exporter(
        monkeypatch, tmp_path, fixture_csv):
    """The round-4 recovery treated every commit in origin/<branch>..HEAD as its own,
    so an unrelated WIP commit sitting in the sibling clone would be rebased and pushed
    to origin/master by a SCHEDULED job. This exporter publishes its own files; it must
    refuse and name the foreign commit rather than ship someone's unfinished work."""
    _positions_stub(monkeypatch, tmp_path)
    repo, bare = _real_clone(tmp_path)
    assert export_and_push(fixture_csv, target_dir=repo, push=True)["status"] == "pushed"

    (repo / "notes.txt").write_text("half-written thought\n", encoding="utf-8")
    for args in (["add", "notes.txt"], ["commit", "-q", "-m", "WIP: do not ship"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    res = export_and_push(fixture_csv, target_dir=repo, push=True)
    assert res["status"] == "failed", res
    assert "WIP: do not ship" in res["reason"]
    assert subprocess.run(["git", "-C", str(bare), "show", "master:notes.txt"],
                          capture_output=True).returncode != 0
    head, _ = sigma_export._git(repo, "rev-parse", "HEAD")
    remote, _ = sigma_export._git(repo, "rev-parse", "origin/master")
    assert head.strip() != remote.strip()      # the clone is left exactly as it was


def test_a_commit_touching_a_foreign_path_alongside_ours_is_refused(
        monkeypatch, tmp_path, fixture_csv):
    """Both tests must hold: OUR message marker AND only our files. A commit wearing
    the right subject while carrying someone else's file is not ours."""
    _positions_stub(monkeypatch, tmp_path)
    repo, bare = _real_clone(tmp_path)
    assert export_and_push(fixture_csv, target_dir=repo, push=True)["status"] == "pushed"

    (repo / "secrets.env").write_text("TOKEN=x\n", encoding="utf-8")
    (repo / "portfolio.json").write_text("{}\n", encoding="utf-8")
    for args in (["add", "secrets.env", "portfolio.json"],
                 ["commit", "-q", "-m", sigma_export.EXPORT_COMMIT_MESSAGE
                  + "\n\n" + sigma_export.EXPORT_COMMIT_MARKER]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    res = export_and_push(fixture_csv, target_dir=repo, push=True)
    assert res["status"] == "failed", res
    assert "secrets.env" in res["reason"]
    assert subprocess.run(["git", "-C", str(bare), "show", "master:secrets.env"],
                          capture_output=True).returncode != 0


def test_a_foreign_commit_is_refused_BEFORE_the_rebase_rewrites_it(
        monkeypatch, tmp_path, fixture_csv):
    """The check ran after the rebase, so a scheduled run replayed someone's WIP
    commit onto the new remote tip -- new SHA, signature dropped, topology gone --
    and then reported that it had left the clone untouched."""
    _positions_stub(monkeypatch, tmp_path)
    repo, bare = _real_clone(tmp_path)
    assert export_and_push(fixture_csv, target_dir=repo, push=True)["status"] == "pushed"

    # someone else advances origin, so a rebase would certainly rewrite local history
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True,
                   capture_output=True)
    (other / "upstream.txt").write_text("from CI\n", encoding="utf-8")
    for args in (["config", "user.email", "t@t.t"], ["config", "user.name", "t"],
                 ["add", "upstream.txt"], ["commit", "-q", "-m", "CI cache update"],
                 ["push", "-q", "origin", "master"]):
        subprocess.run(["git", "-C", str(other), *args], check=True, capture_output=True)

    (repo / "notes.txt").write_text("half-written\n", encoding="utf-8")
    for args in (["add", "notes.txt"], ["commit", "-q", "-m", "WIP: do not ship"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    before, _ = sigma_export._git(repo, "rev-parse", "HEAD")

    res = export_and_push(fixture_csv, target_dir=repo, push=True)
    assert res["status"] == "failed" and "WIP: do not ship" in res["reason"]
    after, _ = sigma_export._git(repo, "rev-parse", "HEAD")
    assert after.strip() == before.strip(), "the foreign commit was rewritten anyway"


def test_a_clone_parked_on_a_FEATURE_BRANCH_is_refused_not_published_sideways(
        monkeypatch, tmp_path, fixture_csv):
    """Publication followed whatever branch the sibling checkout happened to be on,
    while every sigma-alert job clones origin/master: parked on a feature branch the
    exporter rebased, pushed HEAD, reported `pushed`, and origin/master:sources/sp500.txt
    never moved. The lane must name the active branch and stop."""
    _positions_stub(monkeypatch, tmp_path)
    repo, bare = _real_clone(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "wip"],
                   check=True, capture_output=True)

    res = export_and_push(fixture_csv, target_dir=repo, push=True)
    assert res["status"] == "failed", res
    assert "wip" in res["reason"] and sigma_export.PUBLISH_BRANCH in res["reason"]
    assert not (repo / "ticker_metadata.json").exists()   # nothing written either
    assert subprocess.run(["git", "-C", str(bare), "show", "master:ticker_metadata.json"],
                          capture_output=True).returncode != 0


def test_the_push_names_the_publishing_branch_not_HEAD(monkeypatch, tmp_path, fixture_csv):
    """An explicit refspec is what makes the destination a fact rather than a
    consequence of the local checkout."""
    _positions_stub(monkeypatch, tmp_path)
    repo, bare = _real_clone(tmp_path)

    seen = []
    real_git = sigma_export._git

    def spy(cwd, *args):
        seen.append(args)
        return real_git(cwd, *args)

    monkeypatch.setattr(sigma_export, "_git", spy)
    assert export_and_push(fixture_csv, target_dir=repo, push=True)["status"] == "pushed"

    pushes = [a for a in seen if a and a[0] == "push"]
    assert pushes, seen
    assert all("HEAD" not in " ".join(a) or ":" in " ".join(a) for a in pushes), pushes
    assert any(sigma_export.PUBLISH_BRANCH in " ".join(a) for a in pushes), pushes
    head, _ = real_git(repo, "rev-parse", "HEAD")
    remote = subprocess.run(["git", "-C", str(bare), "rev-parse", "master"],
                            capture_output=True, text=True).stdout
    assert head.strip() == remote.strip()
