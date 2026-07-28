"""Archive/prune scoping — regression for the 2026-07-28 data loss.

Four callers share `reports/old reports/` with a 60-day prune default. Pruning used
to glob the whole archive, so any one of them deleted every other one's artifacts.
That destroyed all weekly coverage-recommendation reports and company backgrounds
older than 60 days, permanently (reports/ is gitignored).
"""
import os
import time

from reporting.email import archive_files

PERF = ["coverage_performance_*.xlsx"]
UNIVERSE = ["weekly_coverage_universe_additions_*.md", "company_backgrounds_*.md"]


def _age(path, days):
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_prune_never_touches_another_callers_artifacts(tmp_path):
    src, arch = tmp_path / "reports", tmp_path / "old"
    src.mkdir(); arch.mkdir()
    victim = arch / "weekly_coverage_universe_additions_2026-04-17.md"
    victim.write_text("irreplaceable analysis", encoding="utf-8")
    _age(victim, 120)

    # A performance run prunes its own patterns only.
    archive_files(src, arch, "2026-07-28", PERF, prune_days=60)
    assert victim.exists(), "a performance run must not delete coverage reports"


def test_prune_still_removes_the_callers_own_stale_files(tmp_path):
    src, arch = tmp_path / "reports", tmp_path / "old"
    src.mkdir(); arch.mkdir()
    stale = arch / "coverage_performance_2026-01-02.xlsx"
    stale.write_text("regenerable", encoding="utf-8")
    _age(stale, 120)

    res = archive_files(src, arch, "2026-07-28", PERF, prune_days=60)
    assert res["pruned"] == 1
    assert not stale.exists()


def test_prune_days_zero_retains_everything(tmp_path):
    src, arch = tmp_path / "reports", tmp_path / "old"
    src.mkdir(); arch.mkdir()
    old = arch / "company_backgrounds_2026-01-02.md"
    old.write_text("keep me", encoding="utf-8")
    _age(old, 400)

    res = archive_files(src, arch, "2026-07-28", UNIVERSE, prune_days=0)
    assert res["pruned"] == 0
    assert old.exists()


def test_todays_file_is_not_archived(tmp_path):
    src, arch = tmp_path / "reports", tmp_path / "old"
    src.mkdir(); arch.mkdir()
    today = src / "weekly_coverage_universe_additions_2026-07-28.md"
    today.write_text("current", encoding="utf-8")
    prior = src / "weekly_coverage_universe_additions_2026-07-24.md"
    prior.write_text("prior", encoding="utf-8")

    res = archive_files(src, arch, "2026-07-28", UNIVERSE, prune_days=0)
    assert res["moved"] == 1
    assert today.exists(), "the current run's report stays in reports/"
    assert (arch / prior.name).exists()
