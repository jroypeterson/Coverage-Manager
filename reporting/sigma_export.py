"""Export ticker metadata from the Coverage Manager CSV to the sigma-alert repo.

The sigma-alert screener loads `ticker_metadata.json` at startup so its Slack
alerts can show company names and sector tags, and so the 1σ alert tier can
filter on Healthcare Services / MedTech / PA tickers.

The Coverage Manager CSV is the canonical source for that data, so this module
generates the metadata file directly into the sibling sigma-alert clone, then
commits and pushes only the files it owns (the JSON payloads below, plus
`sources/sp500.txt` + `sources/sp500_names.json` since 2026-09-22 -- see
`build_sp500_mirror`). sigma-alert's CI does not (and
should not) try to regenerate the file — it has no access to the CSV.
"""

import json
import subprocess
from pathlib import Path

from logging_utils import get_logger
from universe import index_membership as _im
from universe.artifacts import build_universe_metadata

logger = get_logger("reporting.sigma_export")

# Sigma-alert clone is a sibling of Coverage Manager in the Dropbox folder.
SIGMA_ALERT_DIR = Path(__file__).resolve().parent.parent.parent / "sigma-alert"
METADATA_FILENAME = "ticker_metadata.json"
# Core watchlist pushed alongside ticker_metadata.json so the sigma
# screener can surface watchlist hits in its own section of the Slack
# digest. Schema mirrors exports/watchlist.json — ticker-keyed, with
# buy/target/notes joined against universe metadata.
#
# DEPRECATED 2026-05-03: kept for one cycle of back-compat while sigma-alert
# migrates to portfolio.json + researching.json. Will be removed in a
# follow-up after sigma-alert's screener consumes the new files.
CORE_WATCHLIST_FILENAME = "core_watchlist.json"
PORTFOLIO_FILENAME = "portfolio.json"
RESEARCHING_FILENAME = "researching.json"
# Passive-tracking list: names you follow for earnings/industry signal but
# have no intent to trade. Pushed for completeness; sigma-alert may render
# these in a separate Slack subcategory in the future.
FOLLOWING_FOR_INTEREST_FILENAME = "following_for_interest.json"
# Trigger-ready lists: thesis is done, waiting for an entry-price level. These
# are pushed to sigma-alert so the future price-target alerter (deferred —
# routes to the `#portfolio` Slack channel) can read the trigger levels.
READY_TO_BUY_FILENAME = "ready_to_buy.json"
READY_TO_SHORT_FILENAME = "ready_to_short.json"
# Sigma-alert's EOD screener writes this file when it finds watchlist tickers
# that are missing from ticker_metadata.json (or have a blank name). It's the
# only way the screener — running in CI with no access to the Coverage Manager
# CSV — can flag gaps for us to fix.
MISSING_METADATA_RELPATH = "cache/missing_metadata.json"

# Sector ETFs are not in the Coverage Manager universe but the sigma-alert
# watchlist includes them, so we hard-code their display info here.
#
# TODO (Stage 2 follow-up): move this list into the sigma-alert repo itself.
# The clean end state is that Coverage Manager publishes only generic
# `exports/universe_metadata.json`, sigma-alert reads that file directly, and
# sigma-alert applies its own ETF augmentation locally before consuming. That
# eliminates the need for sigma_export to know anything about sigma-alert's
# watchlist composition. Deferred from the current PR because moving the ETF
# list cross-repo also requires updating sigma-alert's read path and adds
# scope/risk to the GitHub Actions screener.
SECTOR_ETFS = {
    "XLE": ("Energy Select Sector SPDR", "ETF"),
    "XLB": ("Materials Select Sector SPDR", "ETF"),
    "XLU": ("Utilities Select Sector SPDR", "ETF"),
    "XLP": ("Consumer Staples Select Sector SPDR", "ETF"),
    "XLI": ("Industrial Select Sector SPDR", "ETF"),
    "XLRE": ("Real Estate Select Sector SPDR", "ETF"),
    "XLC": ("Communication Services Select Sector SPDR", "ETF"),
    "XLV": ("Health Care Select Sector SPDR", "ETF"),
    "XLK": ("Technology Select Sector SPDR", "ETF"),
    "XLY": ("Consumer Discretionary Select Sector SPDR", "ETF"),
    "XLF": ("Financial Select Sector SPDR", "ETF"),
    "XBI": ("SPDR S&P Biotech ETF", "ETF"),
    "SPYM": ("SPDR Portfolio S&P 500 ETF", "ETF"),
}


def build_sigma_metadata(csv_path):
    """Build the sigma-alert ticker metadata: generic universe + sigma-only ETFs.

    The sigma-alert watchlist includes sector ETFs that are not part of the
    coverage universe. This function composes the generic universe metadata
    with those ETFs so the screener has display info for the full watchlist.

    Generic exports under `exports/` must NOT use this function — they should
    call `universe.artifacts.build_universe_metadata` directly so consumer-
    specific tickers don't leak into the published artifact contract.
    """
    metadata = build_universe_metadata(csv_path)
    for ticker, (name, sector) in SECTOR_ETFS.items():
        if ticker not in metadata:
            metadata[ticker] = {
                "name": name,
                "sector": sector,
                "subsector": "",
                "sub_subsector": "",
            }
    return metadata


def build_core_watchlist_payload(csv_path):
    """DEPRECATED — back-compat wrapper. Returns the union of portfolio +
    researching, in the legacy watchlist shape.

    Use `build_portfolio_payload` and `build_researching_payload` for new code.
    Will be removed once sigma-alert's screener migrates to the new files.
    """
    from universe import watchlist as wl  # back-compat shim

    entries = wl.load(wl.WATCHLIST_PATH)
    metadata = build_universe_metadata(csv_path)
    out = {}
    for e in entries:
        t = e["Ticker"]
        meta_key = t.split()[0].split(".")[0].upper()
        meta = metadata.get(meta_key, {})
        out[t] = {
            "date_added": e.get("Date Added", ""),
            "notes": e.get("Notes", ""),
            "name": meta.get("name", ""),
            "sector": meta.get("sector", ""),
            "subsector": meta.get("subsector", ""),
            "sub_subsector": meta.get("sub_subsector", ""),
        }
    return out


def _select(entries, position_value):
    """Pick the rows for one exported list.

    `Portfolio` is no longer a `Position` value -- ownership is DERIVED into the
    `Held` column from the brokers (2026-08-23, see `universe/held.py`). This
    function is the single place that knows it, so every sigma-alert payload and
    `weekly_universe`'s exports agree by construction rather than by both being
    remembered. `Researching` excludes held names to keep the exported lists
    mutually exclusive, exactly as they were when Position was one value.
    """
    from universe import positions

    if position_value == "Portfolio":
        return [e for e in entries if (e.get("Held") or "").strip().upper() == "Y"]
    rows = positions.filter_by_position(entries, position_value)
    if position_value == "Researching":
        rows = [e for e in rows if (e.get("Held") or "").strip().upper() != "Y"]
    return rows


def _build_position_payload(csv_path, position_value):
    """Return the {ticker: {...}} dict for one Position state.

    Filters `data/positions_and_researching.csv` by Position == position_value
    and joins with universe metadata. Used for both portfolio.json and
    researching.json pushed to sigma-alert.
    """
    from universe import positions

    entries = positions.load(positions.POSITIONS_PATH)
    filtered = _select(entries, position_value)
    metadata = build_universe_metadata(csv_path)
    out = {}
    for e in filtered:
        t = e["Ticker"]
        meta_key = t.split()[0].split(".")[0].upper()
        meta = metadata.get(meta_key, {})
        out[t] = {
            "position": positions.published_position(e),
            "position_date": e.get("Position Date", ""),
            "first_buy_date": e.get("First Buy Date", ""),
            "average_cost": e.get("Average Cost"),
            "shares": e.get("Shares"),
            "notes": e.get("Notes", ""),
            "name": meta.get("name", ""),
            "sector": meta.get("sector", ""),
            "subsector": meta.get("subsector", ""),
            "sub_subsector": meta.get("sub_subsector", ""),
            "core": meta.get("core", ""),
        }
    return out


def build_portfolio_payload(csv_path):
    """{ticker: {...}} for HELD rows (derived from the broker feed, not from
    Position). Pushed to sigma-alert. Shape unchanged -- see `_select`."""
    return _build_position_payload(csv_path, "Portfolio")


def build_researching_payload(csv_path):
    """{ticker: {...}} for Position=='Researching' rows. Pushed to sigma-alert."""
    return _build_position_payload(csv_path, "Researching")


def build_following_for_interest_payload(csv_path):
    """{ticker: {...}} for Position=='Following for Interest' rows.
    Pushed to sigma-alert for passive-tracking display purposes."""
    return _build_position_payload(csv_path, "Following for Interest")


def build_ready_to_buy_payload(csv_path):
    """{ticker: {...}} for Position=='Ready to Buy' rows. Pushed to sigma-alert."""
    return _build_position_payload(csv_path, "Ready to Buy")


def build_ready_to_short_payload(csv_path):
    """{ticker: {...}} for Position=='Ready to Short' rows. Pushed to sigma-alert."""
    return _build_position_payload(csv_path, "Ready to Short")


def read_missing_metadata_flag(target_dir=SIGMA_ALERT_DIR):
    """Read the sigma-alert flag file listing tickers missing from metadata.

    Returns the parsed dict ({"updated": ISO8601, "tickers": {TICKER: reason}})
    or None if the file doesn't exist or can't be read. The screener writes
    this file on EOD runs whenever it finds watchlist tickers that are missing
    from ticker_metadata.json (or have a blank `name` field).
    """
    flag_path = target_dir / MISSING_METADATA_RELPATH
    if not flag_path.exists():
        return None
    try:
        with open(flag_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read sigma-alert missing-metadata flag: %s", e)
        return None


def _git(cwd, *args):
    """Run a git command in cwd, returning (stdout, returncode). Errors are logged, not raised."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip(), result.returncode


# --- S&P 500 membership (board #354; JP 2026-09-22: "retire sigma-alert/sources/sp500.txt")
#
# sigma-alert's S&P 500 list used to be collected by its own monthly Wikipedia scrape
# (`scripts/refresh_sp500.py`), in parallel with CM's `wikipedia_provider`. CM now owns it:
# this step writes both files from CM's `index_membership` sp500 snapshot, in the same
# commit as ticker_metadata.json. sigma-alert's CI still cannot read the gitignored
# snapshot, so the committed text file remains the transport -- it is simply CM-written.
#
# ONLY tickers and names cross the boundary (the same two fields sigma-alert already
# published from its own scrape). GICS fields stay in the gitignored snapshot.
#
# ⛑ NEVER OVERWRITE A GOOD LIST WITH A BAD ONE. Every refusal leaves both files untouched,
# lets the rest of the export proceed, and is reported as `failed:` in the weekly step.
SP500_RELPATH = "sources/sp500.txt"
SP500_NAMES_RELPATH = "sources/sp500_names.json"

# ⛑ THE ONLY PATHS THIS EXPORTER OWNS, and the marker it stamps on its own commits.
# The round-4 catch-up push treated EVERY commit in `origin/<branch>..HEAD` as a
# stranded export, so one unrelated WIP commit sitting in the sibling clone would be
# rebased and pushed to origin/master by a SCHEDULED job -- publishing someone's
# unfinished work, from a lane nobody is watching at the time. Ownership is now two
# tests, both required: OUR message, and ONLY our files.
# ⛑ THE BRANCH SIGMA-ALERT'S CONSUMERS ACTUALLY READ, declared rather than inferred.
# Every sigma-alert job runs in GitHub Actions and clones `master`; the exporter used
# whatever branch the sibling checkout happened to be on, so a clone parked on a
# feature branch was rebased onto that branch, pushed there, and reported `pushed` and
# `sp500 changed` while `origin/master:sources/sp500.txt` never moved. Same reasoning
# as `index_mirrors.Mirror.ci_branch`, which declares it for the read side.
PUBLISH_BRANCH = "master"

EXPORT_COMMIT_MESSAGE = "Sync ticker metadata + position lists from Coverage Manager"
EXPORT_COMMIT_MARKER = "CM-Sigma-Export: v1"
# ⛑ ONE AUTHORITY FOR THE BAND. These used to be a second copy of the numbers the
# collector applies; the collector had a LOOSER rule (the generic 450 floor), so a
# truncated or transitional list was written to the snapshot and only refused here --
# after every other consumer of `sp500_latest.json` had already read it.
SP500_MIN_COUNT = _im.SP500_MIN_COUNT
SP500_MAX_COUNT = _im.SP500_MAX_COUNT
# Named WITHOUT a URL (2026-09-22): the list now comes from IVV fund holdings via CM's
# index_membership, and the iShares URL must never reach this public repo.
SP500_SOURCE_LABEL = "Coverage Manager index membership (S&P 500; fund holdings disclosure)"
_SP500_UPDATED_PREFIX = "# Last updated:"


def _load_sp500_snapshot():
    """CM's latest S&P 500 snapshot doc, or None. Seam for tests."""
    from universe import index_membership as im

    return im.load_latest("sp500")


def _mirror_tickers(text):
    return {ln.strip().upper() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")}


def mirror_update_values(text):
    """Every `# Last updated:` value in the file, raw, in order. Used to say WHY a
    mirror is undatable -- the refusal has to name the values a human will look for."""
    return [ln[len(_SP500_UPDATED_PREFIX):].strip()
            for ln in text.splitlines() if ln.startswith(_SP500_UPDATED_PREFIX)]


def _mirror_updated(text):
    """The mirror's own `# Last updated:` date as ISO, or None if it is not a date.

    ⛑ IT MUST PARSE, NOT MERELY BE NON-EMPTY. This returned the first 10 characters of
    whatever followed the label, and the caller then COMPARED THEM LEXICALLY: a file
    stamped `0000-00-00` sorts below every real ISO date, so a fresh-but-older snapshot
    counted as strictly newer and rolled the public list backward -- the exact revert
    the guard exists to stop, surviving the round that was supposed to close it.
    """
    from datetime import datetime as _dt

    # ⛑ A SECOND HEADER IS NOT AN ANNOTATION, IT IS AN AMBIGUITY. This returned the
    # FIRST value and never looked further, so a file carrying both `2026-09-01` and a
    # corrective `2026-09-30` let a 2026-09-08 snapshot count as strictly newer and
    # overwrite the public list with the OLDER basket.
    found = mirror_update_values(text)
    if len(found) != 1:
        return None
    for ln in text.splitlines():
        if ln.startswith(_SP500_UPDATED_PREFIX):
            # ⛑ THE WHOLE VALUE, NOT ITS FIRST TEN CHARACTERS. Slicing first made an
            # annotated header parse as its PREFIX -- `2026-09-01 (corrected
            # 2026-09-30)` read as 2026-09-01, so a snapshot from 2026-09-08 counted
            # as strictly newer and overwrote the public mirror with the OLDER basket.
            # Trailing content means we do not know what the file is dated.
            raw = ln[len(_SP500_UPDATED_PREFIX):].strip()
            try:
                return _dt.strptime(raw, "%Y-%m-%d").date().isoformat()
            except ValueError:
                return None
    return None


def render_sp500_txt(tickers, as_of):
    """Byte format of sigma-alert's `refresh_sp500.write_sp500`, exactly (LF; git
    normalises the worktree's line endings)."""
    header = [
        "# S&P 500 Constituents",
        f"# Last updated: {as_of}",
        "# Check for reconstitution updates quarterly (March, June, September, December)",
        f"# Source: {SP500_SOURCE_LABEL}",
    ]
    return "\n".join(header + sorted(set(tickers))) + "\n"


def render_sp500_names(mapping):
    """Byte format of sigma-alert's `refresh_sp500.write_sp500_names`, exactly."""
    return json.dumps(dict(sorted(mapping.items())), indent=2) + "\n"


def build_sp500_mirror(target_dir=SIGMA_ALERT_DIR, today=None, doc=None):
    """Decide what, if anything, to write to sigma-alert's two S&P 500 files. No writes.

    Returns {"status": "changed"|"unchanged"|"refused", "reason", "as_of", "count",
    "files": {relpath: content}} -- `files` holds only the files whose CONTENT changed.
    A header-date-only difference is not a change, so an unchanged list never churns.

    The snapshot is loaded once and the same object is validated and rendered.
    """
    from universe import index_membership as im

    def refused(reason, **kw):
        return {"status": "refused", "reason": reason, "files": {}, **kw}

    if doc is None:
        try:
            doc = _load_sp500_snapshot()
        except Exception as e:  # a diagnostic seam must not crash the export
            return refused(f"snapshot unreadable ({type(e).__name__}: {e})")
    if not isinstance(doc, dict):
        return refused("no S&P 500 snapshot on disk")

    holdings = doc.get("holdings")
    if not isinstance(holdings, list) or not holdings:
        return refused("snapshot carries no holdings")
    pairs = {}
    for h in holdings:
        t = str((h or {}).get("ticker") or "").strip().upper() if isinstance(h, dict) else ""
        if not t:
            return refused("snapshot has a holding with no ticker")
        if t in pairs:
            return refused(f"snapshot lists {t} twice")
        pairs[t] = str(h.get("name") or "").strip()

    as_of = doc.get("as_of")
    count = len(pairs)
    if not SP500_MIN_COUNT <= count <= SP500_MAX_COUNT:
        return refused(f"implausible count {count} (outside {SP500_MIN_COUNT}-{SP500_MAX_COUNT})",
                       as_of=as_of, count=count)

    # Staleness: the SAME rule index_membership/index_mirrors apply -- the limit travels
    # on the snapshot (`stale_days`), falling back to the module's per-kind table.
    age = im.snapshot_age_days(doc, today=today)
    limit = doc.get("stale_days")
    if not isinstance(limit, int) or limit <= 0:
        limit = im.stale_days_for("sp500")
    if age is None:
        return refused("snapshot carries no usable as_of date", count=count)
    # ⛑ THE SAME TOLERANCE THE COLLECTOR APPLIES, not a second rule with a different
    # sign convention. `age` is today - as_of, so a snapshot one day ahead is -1 --
    # which `index_membership.refresh` accepts (a fund stamps its own trade date). This
    # refused it, so a snapshot the archive had already accepted could never reach
    # sigma-alert and the public list simply stopped updating, with no path back but
    # waiting for the date to catch up.
    if age < -_im.FUTURE_TOLERANCE_DAYS:
        return refused(f"snapshot as_of {as_of} is in the future "
                       f"(beyond the {_im.FUTURE_TOLERANCE_DAYS}d tolerance)",
                       as_of=as_of, count=count)
    if age > limit:
        return refused(f"snapshot as_of {as_of} is {age}d old (limit {limit}d)",
                       as_of=as_of, count=count)

    txt_path = Path(target_dir) / SP500_RELPATH
    names_path = Path(target_dir) / SP500_NAMES_RELPATH
    try:
        cur_txt = txt_path.read_text(encoding="utf-8") if txt_path.exists() else None
        cur_names = (json.loads(names_path.read_text(encoding="utf-8"))
                     if names_path.exists() else None)
    except (OSError, ValueError) as e:
        return refused(f"current sigma-alert S&P 500 files unreadable ({e})",
                       as_of=as_of, count=count)

    # Blank names are skipped, as refresh_sp500 did. A name EQUAL to its ticker is kept:
    # IBM, MSCI and Uber really are named that (measured 2026-09-22).
    names = {t: n for t, n in pairs.items() if n}

    files = {}
    if cur_txt is None or _mirror_tickers(cur_txt) != set(pairs):
        files[SP500_RELPATH] = render_sp500_txt(pairs, as_of)
    if cur_names != names:
        files[SP500_NAMES_RELPATH] = render_sp500_names(names)

    # ⛑ ONLY A STRICTLY NEWER OBSERVATION MAY CHANGE THE LIST. CM's snapshot is taken
    # before the weekly performance run refreshes its 7-day Wikipedia cache, so it lags;
    # measured 2026-09-22, a snapshot observed on the SAME date as sigma-alert's list
    # (2026-09-18) still lacked that week's reconstitution (BE/ILMN/P in, BLDR/TAP/TTD
    # out). Writing it would have silently reverted the index change. So: a difference
    # from a same-day or older observation is refused, and the list waits for CM to
    # catch up. Agreement is never refused -- that is simply `unchanged`.
    cur_updated = _mirror_updated(cur_txt) if cur_txt else None
    # ⛑ AN UNKNOWN DATE IS NOT A DATE OLDER THAN OURS. With no parsable
    # `# Last updated:` line the comparison below was simply skipped, so the guard
    # failed OPEN and an older snapshot rolled the public list backward -- the exact
    # revert (BLDR/TAP/TTD returning) it exists to prevent. A list we cannot date is a
    # list we must not overwrite; agreement is still `unchanged`, so a quiet week is
    # unaffected and only a CHANGE is blocked.
    if files and cur_txt is not None and not cur_updated:
        found = mirror_update_values(cur_txt)
        detail = (f"found {len(found)}: {', '.join(repr(v) for v in found)}"
                  if found else "no `# Last updated:` line at all")
        return refused("the sigma-alert list carries no single parsable "
                       f"`# Last updated:` date ({detail}), so it cannot be dated - "
                       "refusing to overwrite it with a snapshot that may be older",
                       as_of=as_of, count=count)
    if files and cur_updated and as_of and as_of <= cur_updated:
        return refused(f"CM snapshot as_of {as_of} is not newer than the sigma-alert list "
                       f"dated {cur_updated} but disagrees with it - refusing to overwrite",
                       as_of=as_of, count=count)
    return {"status": "changed" if files else "unchanged", "reason": "",
            "as_of": as_of, "count": count, "names": len(names), "files": files}


def _owned_paths():
    return {METADATA_FILENAME, CORE_WATCHLIST_FILENAME, PORTFOLIO_FILENAME,
            RESEARCHING_FILENAME, FOLLOWING_FOR_INTEREST_FILENAME,
            READY_TO_BUY_FILENAME, READY_TO_SHORT_FILENAME,
            SP500_RELPATH, SP500_NAMES_RELPATH}


def classify_unpushed(target_dir, branch):
    """`(ours, foreign, error)` for the commits `origin/<branch>` does not have.

    ⛑ ASKED ON EVERY RUN, NOT ONLY WHEN BYTES CHANGED. A transient push failure left
    the commit local; the next run rebased, found the worktree already carrying the new
    bytes, reported `unchanged` and never pushed again -- so origin, the only thing
    sigma-alert's GitHub Actions ever clone, served the old list indefinitely.

    ⛑ AND A COMMIT IS OURS ONLY IF BOTH TESTS PASS: our message (the marker, or the
    subject we have always written, for commits predating the marker) AND a changed-path
    set inside `_owned_paths()`. A subject can be copied and a path set can be
    coincidental; together they are what "this exporter wrote it" means. `git push`
    cannot publish a subset of a linear history, so a foreign commit means the whole
    push is refused and the clone is left exactly as it was.
    """
    out, rc = _git(target_dir, "rev-list", f"origin/{branch}..HEAD")
    if rc != 0:
        return [], [], f"could not list commits ahead of origin/{branch}"
    shas = [ln.strip() for ln in out.splitlines() if ln.strip()]
    owned, ours, foreign = _owned_paths(), [], []
    for sha in reversed(shas):
        msg, rc_msg = _git(target_dir, "show", "-s", "--format=%s%n%b", sha)
        paths, rc_paths = _git(target_dir, "show", "--name-only", "--format=", sha)
        if rc_msg != 0 or rc_paths != 0:
            return [], [], f"could not read local commit {sha[:8]}"
        subject = (msg.splitlines() or [""])[0].strip()
        changed = [p.strip() for p in paths.splitlines() if p.strip()]
        outside = sorted(p for p in changed if p not in owned)
        if (EXPORT_COMMIT_MARKER in msg or subject.startswith(EXPORT_COMMIT_MESSAGE)) \
                and not outside:
            ours.append(sha)
        else:
            foreign.append((sha[:8], subject, outside))
    return ours, foreign, ""


def _foreign_commit_reason(foreign, branch):
    sha, subject, outside = foreign[0]
    extra = f" touching {', '.join(outside)}" if outside else ""
    return (f"{len(foreign)} local commit(s) in the sigma-alert clone are not this "
            f"exporter's - refusing to push them to origin/{branch} and leaving the "
            f"clone untouched (first: {sha} \"{subject}\"{extra})")


def export_and_push(csv_path, target_dir=SIGMA_ALERT_DIR, push=True, today=None):
    """Build metadata, write it into target_dir, and commit/push only that file.

    Returns a dict describing what happened. Raises only on unrecoverable errors;
    expected outcomes (no changes, no remote, etc.) are reported in the dict.
    """
    if not target_dir.exists():
        return {"status": "skipped", "reason": f"sigma-alert clone not found at {target_dir}"}

    if not (target_dir / ".git").exists():
        return {"status": "skipped", "reason": f"{target_dir} is not a git repo"}

    # Sync local clone with remote before writing. sigma-alert's GitHub Actions
    # cron jobs commit cache updates to origin/master after each market close,
    # so without rebasing first, our push is rejected as non-fast-forward and
    # the export silently stalls — the original cause of the 2026-04-07 →
    # 2026-04-29 core_watchlist drift.
    branch = None
    if push:
        active, rc = _git(target_dir, "rev-parse", "--abbrev-ref", "HEAD")
        if rc != 0:
            return {"status": "failed", "reason": "could not determine current branch in sigma-alert clone"}
        active = active.strip()
        # ⛑ REFUSE, DO NOT CHECK OUT. A checkout would move someone else's work out
        # from under them in a repo this lane does not own; naming the branch costs one
        # command from the operator and cannot lose anything.
        if active != PUBLISH_BRANCH:
            return {"status": "failed",
                    "reason": f"the sigma-alert clone is on branch {active!r}, not the "
                              f"publishing branch {PUBLISH_BRANCH!r} that its GitHub "
                              f"Actions clone - refusing to publish sideways "
                              f"(git -C <clone> checkout {PUBLISH_BRANCH})"}
        branch = PUBLISH_BRANCH
        _, rc = _git(target_dir, "fetch", "origin", branch)
        if rc != 0:
            return {"status": "failed", "reason": f"git fetch origin {branch} failed in sigma-alert clone"}
        # ⛑ CLASSIFY BEFORE ANY HISTORY-MUTATING COMMAND, AND BEFORE A SINGLE FILE IS
        # WRITTEN. The fetch above only moves a remote-tracking ref; the REBASE rewrites
        # local history, so running this check after it meant a scheduled job replayed
        # someone's WIP commit onto the new remote tip — new SHA, signature dropped,
        # merge topology flattened — and then reported that it had left the clone
        # untouched. A push also publishes the whole branch, so a foreign commit
        # contaminates the changed-bytes path exactly as it does the catch-up path.
        _, foreign, err = classify_unpushed(target_dir, branch)
        if err:
            return {"status": "failed", "reason": err}
        if foreign:
            return {"status": "failed", "reason": _foreign_commit_reason(foreign, branch)}

        _, rc = _git(target_dir, "rebase", f"origin/{branch}")
        if rc != 0:
            _git(target_dir, "rebase", "--abort")
            return {"status": "failed", "reason": "pre-export rebase failed (sigma-alert working tree dirty or conflict)"}

    # Surface any tickers sigma-alert flagged as missing metadata. We log the
    # warning whether or not the export below ends up changing the file —
    # the operator needs to see the gaps so they can fix the source CSV.
    flag = read_missing_metadata_flag(target_dir)
    flagged_tickers = {}
    if flag and flag.get("tickers"):
        flagged_tickers = flag["tickers"]
        logger.warning(
            "sigma-alert flagged %d ticker(s) missing metadata (as of %s): %s",
            len(flagged_tickers),
            flag.get("updated", "unknown"),
            sorted(flagged_tickers),
        )

    metadata = build_sigma_metadata(csv_path)
    watchlist_payload = build_core_watchlist_payload(csv_path)  # back-compat (one cycle)
    portfolio_payload = build_portfolio_payload(csv_path)
    researching_payload = build_researching_payload(csv_path)
    following_payload = build_following_for_interest_payload(csv_path)
    ready_to_buy_payload = build_ready_to_buy_payload(csv_path)
    ready_to_short_payload = build_ready_to_short_payload(csv_path)

    files = {
        METADATA_FILENAME: json.dumps(metadata, indent=2) + "\n",
        CORE_WATCHLIST_FILENAME: json.dumps(watchlist_payload, indent=2) + "\n",
        PORTFOLIO_FILENAME: json.dumps(portfolio_payload, indent=2) + "\n",
        RESEARCHING_FILENAME: json.dumps(researching_payload, indent=2) + "\n",
        FOLLOWING_FOR_INTEREST_FILENAME: json.dumps(following_payload, indent=2) + "\n",
        READY_TO_BUY_FILENAME: json.dumps(ready_to_buy_payload, indent=2) + "\n",
        READY_TO_SHORT_FILENAME: json.dumps(ready_to_short_payload, indent=2) + "\n",
    }

    # S&P 500 membership (CM-owned since 2026-09-22). Only CHANGED content is added, and
    # a refusal adds nothing -- the existing files stay exactly as they are.
    sp500 = build_sp500_mirror(target_dir, today=today)
    files.update(sp500["files"])
    sp500_summary = {k: v for k, v in sp500.items() if k != "files"}
    if sp500["status"] == "refused":
        logger.warning("S&P 500 list NOT written to sigma-alert: %s", sp500["reason"])

    def _result(**fields):
        out = {
            "tickers": len(metadata),
            "watchlist_entries": len(watchlist_payload),
            "portfolio_entries": len(portfolio_payload),
            "researching_entries": len(researching_payload),
            "following_for_interest_entries": len(following_payload),
            "ready_to_buy_entries": len(ready_to_buy_payload),
            "ready_to_short_entries": len(ready_to_short_payload),
            "sp500": sp500_summary,
        }
        if flagged_tickers:
            out["missing_metadata"] = flagged_tickers
        out.update(fields)
        return out

    def _nothing_changed_this_run():
        """`unchanged` -- unless an EARLIER run left a commit origin never received."""
        if not push or not branch:
            return _result(status="unchanged")
        ours, foreign, err = classify_unpushed(target_dir, branch)
        if err:
            return _result(status="failed", reason=err)
        if foreign:
            return _result(status="failed",
                           reason=_foreign_commit_reason(foreign, branch))
        if not ours:
            return _result(status="unchanged")
        _, rc = _git(target_dir, "push", "origin", f"HEAD:refs/heads/{branch}")
        if rc != 0:
            return _result(status="committed_not_pushed",
                           reason=f"{len(ours)} commit(s) from an earlier run are still "
                                  f"local and git push failed again")
        logger.info("Pushed %d commit(s) left local by an earlier run", len(ours))
        return _result(status="pushed",
                       reason=f"pushed {len(ours)} commit(s) left local by an earlier run")

    # Write files that actually changed; stage all tracked files so we pick up
    # anything the previous run left in an inconsistent state.
    changed_any = False
    for name, content in files.items():
        path = target_dir / name
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.write_text(content, encoding="utf-8")
        logger.info("Wrote %s (%d bytes)", path, len(content))
        changed_any = True

    if not changed_any:
        return _nothing_changed_this_run()

    for name in files:
        _, rc = _git(target_dir, "add", name)
        if rc != 0:
            return _result(status="failed", reason=f"git add {name} failed")

    # Was anything actually staged? (handles the rare case where file content
    # changed but git normalizes line endings to match HEAD)
    _, rc = _git(target_dir, "diff", "--cached", "--quiet", "--", *files.keys())
    if rc == 0:
        return _nothing_changed_this_run()

    message = EXPORT_COMMIT_MESSAGE
    if sp500["files"]:
        message += " (+ S&P 500 list)"
    # The marker is what a later run reads to know this commit is its own.
    message += "\n\n" + EXPORT_COMMIT_MARKER
    _, rc = _git(target_dir, "commit", "-m", message)
    if rc != 0:
        return _result(status="failed", reason="git commit failed")

    if not push:
        return _result(status="committed")

    # An EXPLICIT refspec: the destination is then a fact, not a consequence of which
    # branch this checkout happens to have active.
    _, rc = _git(target_dir, "push", "origin", f"HEAD:refs/heads/{branch}")
    if rc != 0:
        return _result(
            status="committed_not_pushed",
            reason="git push failed — commit is local",
        )

    return _result(status="pushed")


if __name__ == "__main__":
    # Read-only preview: `python -m reporting.sigma_export` shows what the S&P 500 step
    # WOULD write into the sibling sigma-alert clone. Writes nothing, runs no git.
    res = build_sp500_mirror()
    print({k: v for k, v in res.items() if k != "files"})
    for rel in res["files"]:
        print(f"  would write {rel}")
