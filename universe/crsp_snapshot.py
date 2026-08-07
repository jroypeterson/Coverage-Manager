"""CRSP / Morningstar US Total Market — quarterly constituent snapshot.

WHY THIS EXISTS
---------------
CRSP publishes the full constituent list of the US Total Market index — 3,477
names with weights — as a free CSV. It is the closest thing to an authoritative
answer to *"what is the complete set of US-listed operating companies?"*, which
is the question `delisted_check` and `ticker_change_check` both approximate from
other angles (yfinance price feeds, SEC filings).

The catch that makes this a scheduled job rather than a one-off download:
**CRSP overwrites the file each quarter.** There is no archive. A quarter that
is not captured while it is live is gone permanently, and the entire value of
the dataset is in the *delta* — a name leaving the index between two quarters is
a delisting, acquisition, or a fall out of the investable universe. One snapshot
is a list; two snapshots are a signal.

Provenance note: CRSP was acquired by Morningstar (closed 2026-02-02) and the
index is being renamed the Morningstar US Total Market Index (FS00009VTK) as of
late July 2026. `crsp.org` began redirecting to `indexes.morningstar.com` on
2026-07-28. The download URLs below are expected to move; when they break, this
module must fail loudly rather than silently keep the last snapshot (see
`SnapshotResult.status`).

**Verified live 2026-07-28**: the *website* redirects (`https://www.crsp.org/`
-> `https://indexes.morningstar.com/morningstar-market-indexes`, 301) but the two
data files below do NOT — both return HTTP 200, `text/csv`, with **zero**
redirects, straight from the same nginx/Pantheon origin. The levels file was
modified that same morning (`Last-Modified: Tue, 28 Jul 2026 02:13:08 GMT`, last
row `2026-07-27`), so the feed is still being maintained behind the redirect.
A redirecting landing page is therefore not evidence that the data path moved,
and the two must be checked separately.

WHAT IT IS NOT
--------------
Not a delisting detector for the coverage universe. CRSP excludes
foreign-domiciled issuers, ADRs, and OTC names, so ~234 of Coverage Manager's
US-listed rows (argenx, ASML, Ascendis, Alcon, Amarin, …) are legitimately
absent. Absence from CRSP means "not a US-domiciled exchange-listed operating
company", which overlaps with delisting but is not the same fact. See
`reconcile_universe` for the distinction, which it reports rather than collapses.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import config

log = logging.getLogger(__name__)

# ── Sources ──────────────────────────────────────────────────────────────────

CONSTITUENTS_URL = (
    "https://www.crsp.org/wp-content/uploads/quarterly-index-constituents/"
    "crsp_quarterly_constituents.csv"
)
LEVELS_URL = (
    "https://www.crsp.org/wp-content/uploads/daily-index-levels/"
    "crspmi_daily_index_levels.csv"
)

CRSP_DIR = config.DATA_DIR / "crsp"

# The total-market list is keyed under the PRICE-return ticker in the
# constituents file (CRSPTM1), not the total-return ticker (CRSPTMT) that names
# the index everywhere else. Constituents are identical for both variants, so
# CRSP only publishes one. Filtering on CRSPTMT returns an empty set silently.
TOTAL_MARKET_KEY = "CRSPTM1"

# Sector indexes. These partition CRSP's *Core Cap* universe (1,713 names), not
# the total market — micro-caps carry no sector label at all. Coverage is
# therefore ~49% by count. Do not present a sector breakdown as covering the
# whole index.
SECTOR_INDEXES = {
    "CRSPCD1": "Consumer Discretionary",
    "CRSPCS1": "Consumer Staples",
    "CRSPEN1": "Energy",
    "CRSPFN1": "Financials",
    "CRSPHC1": "Healthcare",
    "CRSPID1": "Industrials",
    "CRSPIT1": "Technology",
    "CRSPMT1": "Materials",
    "CRSPRE1": "Real Estate & REITs",
    "CRSPTE1": "Media & Communications",
    "CRSPUT1": "Utilities",
}

# The disjoint size ladder is Mega / Mid / Small / Micro. Verified against the
# 2026-03-31 file: 173 + 288 + 1,306 + 1,817 = 3,584 memberships over 3,477
# names, the 107-name excess being CRSP's "packeting" migration rule, which
# parks a name in two adjacent tiers while it moves between them.
#
# `CRSPLC1 "Large Cap"` is deliberately NOT here. It is a COMPOSITE, not a tier:
# Mega ∪ Mid, exactly (173 + 288 − 18 straddles = 443). Including it labelled
# every mid-cap "Large" and — because Mega is a strict subset of it — erased
# Mega from the index entirely depending on row order. The same applies to the
# other composites in the file (`CRSPXM1` Core Cap, `CRSPMS1` Small/Micro,
# `CRSPSM1` Small/Mid, `CRSPXE1` ex-Mega): useful as index series, useless as
# per-name labels.
SIZE_INDEXES = {
    "CRSPME1": "Mega",
    "CRSPMI1": "Mid",
    "CRSPSC1": "Small",
    "CRSPMC1": "Micro",
}
SIZE_PRECEDENCE = ["Mega", "Mid", "Small", "Micro"]

# Style is recorded as the AXIS only — Growth / Value / both — not as a
# "Mega Growth"-style box. CRSP assigns style separately within each size band
# and the boxes are not nested: Mega Growth is *not* a subset of Large Growth.
# Picking one box per name therefore has no principled answer, and last-write
# -wins produced labels like NVDA = "Large Growth" while Mega Growth existed.
#
# "both" is a real state, not a bug: CRSP splits 134 names across the growth and
# value boxes with partial weights in each. The axis is well defined for 1,713
# names — the same Core Cap set the sector indexes cover; the 1,764 micro-caps
# have no style, same as they have no sector.
# Base size-band style cuts only. The composite style indexes (`CRSPSMG1`
# Small/Mid Growth, `CRSPXMG1` Core Growth, …) re-cut the same names and
# disagree with the base cuts often enough to inflate "both" from 134 to 292 —
# the same reason composite *size* indexes are excluded above.
GROWTH_INDEXES = {"CRSPMEG1", "CRSPLCG1", "CRSPMIG1", "CRSPSCG1"}
VALUE_INDEXES = {"CRSPMEV1", "CRSPLCV1", "CRSPMIV1", "CRSPSCV1"}

# A healthy total-market file. Sized to catch a truncated download or an HTML
# error page served with a 200, not to police normal index turnover.
MIN_EXPECTED_CONSTITUENTS = 2_500
MAX_EXPECTED_CONSTITUENTS = 6_000
WEIGHT_SUM_TOLERANCE = 0.02

USER_AGENT = "CoverageManager/1.0 (research; contact via repo owner)"
TIMEOUT_SECONDS = 300

# Where to look when the paths do move. Kept ASCII: this string is printed to a
# cp1252 Windows console by the scheduled task, and a non-ASCII character in the
# *error* path would raise UnicodeEncodeError at the exact moment the job is
# trying to report why it failed.
MORNINGSTAR_INDEX_PAGE = (
    "https://indexes.morningstar.com/indexes/details/"
    "morningstar-us-total-market-FS00009VTK"
)


# ── Failure classification ───────────────────────────────────────────────────
#
# "The download failed" is two different operational facts wearing one message,
# and they want opposite responses. A moved URL needs a human to find the new
# path — every retry from here to the heat death of the universe returns the
# same 404. A transient network error needs nothing but the next scheduled run.
# Reporting them identically means the reader has to open the traceback to learn
# which one happened, and on a weekly job whose whole purpose is that a missed
# quarter is unrecoverable, that lag is the expensive part.

MOVED = "moved"            # the server answered, but not with the file
TRANSIENT = "transient"    # the server could not be reached
CONTENT = "content"        # a file arrived; it is not the file we asked for
UNKNOWN = "unknown"


class SourceMoved(RuntimeError):
    """The URL resolved and answered — with something that is not the data file.

    Raised for a 4xx and, importantly, for the *200-with-HTML* case: a CDN whose
    path has been retired commonly serves the site's landing page rather than a
    404, and a landing page parses as a one-column CSV. Catching it here rather
    than in `verify_total_market` keeps the diagnosis ("the URL moved") attached
    to the evidence (the final URL after redirects and the content type).
    """


class TransientDownload(RuntimeError):
    """Every attempt failed to reach the server. The URL is probably fine."""

    def __init__(self, message: str, cause: BaseException | None = None):
        super().__init__(message)
        self.cause = cause


def classify_download_failure(exc: BaseException) -> str:
    """Map a download exception to one of MOVED / TRANSIENT / CONTENT / UNKNOWN."""
    if isinstance(exc, SourceMoved):
        return MOVED
    if isinstance(exc, TransientDownload):
        return TRANSIENT
    # HTTPError subclasses URLError, so it must be tested first.
    if isinstance(exc, urllib.error.HTTPError):
        return MOVED if exc.code < 500 else TRANSIENT
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return TRANSIENT
    return UNKNOWN


def failure_guidance(kind: str) -> str:
    """One ASCII line telling the reader what this failure requires of them."""
    if kind == MOVED:
        return (
            "URL MOVED: the server answered but did not return the data file. "
            "The CRSP -> Morningstar site migration began 2026-07-28 and is the "
            f"likely cause; find the new download path at {MORNINGSTAR_INDEX_PAGE} "
            "and update CONSTITUENTS_URL / LEVELS_URL. Re-running will NOT help."
        )
    if kind == TRANSIENT:
        return (
            "TRANSIENT NETWORK: the server could not be reached after every "
            "retry, so nothing was learned about the URL itself. Re-run. If it "
            "repeats on the next scheduled run, re-classify it as a moved URL "
            f"and check {MORNINGSTAR_INDEX_PAGE}."
        )
    if kind == CONTENT:
        return (
            "SOURCE CHANGED: the URL returned a file, but not the file this job "
            "expects (schema, index key, or row count is wrong). Inspect the "
            "staged download before assuming the path moved."
        )
    return (
        "UNKNOWN FAILURE: could not classify this as a moved URL or a network "
        "problem. Read the error text before re-running."
    )


# ── Results ──────────────────────────────────────────────────────────────────


@dataclass
class SnapshotResult:
    """Outcome of one snapshot attempt.

    `status` is one of `ok` / `unchanged` / `failed`, mirroring the operational
    status semantics in CLAUDE.md. There is deliberately no "partial": a
    snapshot that could not be verified is not written at all, because a
    half-written quarter is worse than a missing one — the missing quarter is
    visibly missing.
    """

    status: str
    failure_kind: str | None = None
    trade_date: str | None = None
    constituent_count: int = 0
    path: Path | None = None
    levels_path: Path | None = None
    levels_archive: Path | None = None
    sector_labelled: int = 0
    added: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    # `(old_ticker, new_ticker, company)`. These tickers ALSO appear in
    # `added`/`dropped`, which stay the raw ticker-keyed diff — the report nets
    # them out for presentation. See `detect_ticker_changes`.
    renames: list[tuple[str, str, str]] = field(default_factory=list)
    prior_trade_date: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "unchanged")

    # `added`/`dropped` stay the raw ticker-keyed diff (that is what the archive
    # says happened). Everything operator-facing reports the NET, and both the
    # console line and the report read these same two properties — computing the
    # net in two places is how a console/report disagreement gets shipped.
    @property
    def net_added(self) -> list[str]:
        renamed = {new for _, new, _ in self.renames}
        return [t for t in self.added if t not in renamed]

    @property
    def net_dropped(self) -> list[str]:
        renamed = {old for old, _, _ in self.renames}
        return [t for t in self.dropped if t not in renamed]


# ── Download ─────────────────────────────────────────────────────────────────


def _download(url: str, dest: Path, *, attempts: int = 4) -> None:
    """Fetch `url` to `dest` atomically, retrying transient network failures.

    Writes to a sibling temp file and replaces on success, so a failed or
    truncated transfer can never leave a half-file where the caller expects a
    complete one. (A plain open-then-write truncates the existing file first,
    which turns a network blip into data loss.)

    The retry exists for the scheduled path specifically: the task runs with
    `StartWhenAvailable`, which fires as soon as the machine wakes — routinely
    before DNS is up. A first-attempt `URLError` there means "the laptop just
    woke", not "CRSP is gone", and this job only gets one shot a week.

    A `SourceMoved` is never retried: it is an *answer*, and repeating a question
    that has already been answered only delays the report of the answer.
    """
    import time

    last: BaseException | None = None
    for i in range(attempts):
        try:
            return _download_once(url, dest)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # An HTTP status is a real answer from a reachable server; only
            # retry it for 5xx, where the server is telling us to come back.
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise
            last = exc
            if i < attempts - 1:
                wait = 5 * (2 ** i)
                log.warning("download failed (%s); retrying in %ds", exc, wait)
                time.sleep(wait)
    raise TransientDownload(
        f"{url}: {attempts} attempts failed; last error: {last}", last
    )


# A retired CDN path very often serves the site's landing page with HTTP 200
# rather than a 404. That page parses as a CSV with one useless column, so the
# 200 has to be checked, not trusted.
_HTML_MARKERS = (b"<!doctype", b"<html", b"<?xml", b"<head")


def _looks_like_html(head: bytes, content_type: str) -> bool:
    if "html" in content_type.lower():
        return True
    return head.lstrip()[:64].lower().startswith(_HTML_MARKERS)


def _download_once(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".download")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status} from {url}")
                final_url = resp.geturl()
                content_type = resp.headers.get("Content-Type", "")
                if final_url != url:
                    # Not fatal on its own — a 301 to a renamed CSV is a perfectly
                    # good outcome — but it is the first visible sign of the
                    # migration, so it must never pass silently.
                    log.warning(
                        "download redirected: %s -> %s (content-type %s)",
                        url, final_url, content_type or "unset",
                    )
                head = resp.read(1 << 20)
                if _looks_like_html(head, content_type):
                    raise SourceMoved(
                        f"{url} answered HTTP 200 with an HTML page "
                        f"(final URL {final_url}, content-type "
                        f"{content_type or 'unset'}) -- the data path has moved"
                    )
                while head:
                    out.write(head)
                    head = resp.read(1 << 20)
        if tmp.stat().st_size == 0:
            raise RuntimeError(f"empty response from {url}")
        _replace_with_retry(tmp, dest)
    except BaseException:
        _remove_quietly(tmp)
        raise


# ── Parse + verify ───────────────────────────────────────────────────────────


def parse_constituents(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{path.name}: no rows")
    required = {"TradeDate", "Index Ticker", "Index Name", "Ticker", "Company", "Weight"}
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)} — schema changed?")
    return rows


def verify_total_market(rows: list[dict]) -> tuple[str, list[dict], list[str]]:
    """Return `(trade_date, total_market_rows, warnings)`; raise if unusable.

    The checks here exist because the failure mode of a dead CDN URL is an HTML
    error page served with HTTP 200, which parses as a CSV with one useless
    column. A row count and a weight sum are cheap and catch that immediately.
    """
    tm = [r for r in rows if r["Index Ticker"].strip() == TOTAL_MARKET_KEY]
    if not tm:
        seen = sorted({r["Index Ticker"] for r in rows})[:10]
        raise ValueError(
            f"no {TOTAL_MARKET_KEY} rows in constituents file; saw index tickers {seen}"
        )

    dates = {r["TradeDate"].strip() for r in tm}
    if len(dates) != 1:
        raise ValueError(f"expected one TradeDate, got {sorted(dates)}")
    trade_date = _iso_date(dates.pop())

    n = len(tm)
    if not MIN_EXPECTED_CONSTITUENTS <= n <= MAX_EXPECTED_CONSTITUENTS:
        raise ValueError(
            f"total-market constituent count {n} outside sane range "
            f"[{MIN_EXPECTED_CONSTITUENTS}, {MAX_EXPECTED_CONSTITUENTS}] — bad download?"
        )

    warnings: list[str] = []
    wsum = sum(float(r["Weight"]) for r in tm if r["Weight"].strip())
    if abs(wsum - 1.0) > WEIGHT_SUM_TOLERANCE:
        warnings.append(f"total-market weights sum to {wsum:.4f}, expected ~1.0")

    dupes = n - len({r["Ticker"].strip().upper() for r in tm})
    if dupes:
        warnings.append(f"{dupes} duplicate ticker(s) in the total-market list")

    return trade_date, tm, warnings


def _iso_date(raw: str) -> str:
    """CRSP writes `MM/DD/YYYY`. Normalise; pass through anything already ISO."""
    raw = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if not m:
        raise ValueError(f"unrecognised TradeDate format: {raw!r}")
    mm, dd, yyyy = m.groups()
    return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"


# ── Derived views ────────────────────────────────────────────────────────────


def build_classification(rows: list[dict]) -> dict[str, dict]:
    """Map ticker → {sector, size, style} from index membership.

    CRSP publishes no sector column; sector is recovered from *which* sector
    index a name appears in. Names absent from all eleven (every micro-cap) get
    `sector: None` — which must stay distinguishable from "no sector exists",
    hence None rather than a fallback string.
    """
    out: dict[str, dict] = {}
    flags: dict[str, set[str]] = {}
    for r in rows:
        idx = r["Index Ticker"].strip()
        tkr = r["Ticker"].strip().upper()
        if not tkr:
            continue
        entry = out.setdefault(tkr, {"sector": None, "size": None, "style": None})
        flags.setdefault(tkr, set())
        if idx in SECTOR_INDEXES:
            entry["sector"] = SECTOR_INDEXES[idx]
        elif idx in SIZE_INDEXES:
            entry["size"] = _more_specific_size(entry["size"], SIZE_INDEXES[idx])
        elif idx in GROWTH_INDEXES:
            flags[tkr].add("Growth")
        elif idx in VALUE_INDEXES:
            flags[tkr].add("Value")

    for tkr, seen in flags.items():
        if seen:
            out[tkr]["style"] = "Growth+Value" if len(seen) == 2 else seen.pop()
    return out


def _more_specific_size(current: str | None, candidate: str) -> str:
    """Resolve overlapping size-tier membership by precedence (Mega wins)."""
    if current is None:
        return candidate
    return min(current, candidate, key=SIZE_PRECEDENCE.index)


def diff_constituents(prior: list[dict], current: list[dict]) -> tuple[list[str], list[str]]:
    """`(added, dropped)` tickers between two total-market snapshots."""
    p = {r["Ticker"].strip().upper() for r in prior}
    c = {r["Ticker"].strip().upper() for r in current}
    return sorted(c - p), sorted(p - c)


def detect_ticker_changes(
    prior: list[dict],
    current: list[dict],
    added: list[str],
    dropped: list[str],
) -> list[tuple[str, str, str]]:
    """Pair a dropped ticker with the added ticker carrying the SAME company name.

    A ticker change appears in a ticker-keyed diff as a drop **and** an add, and
    the report tells the reader a drop means "a delisting, acquisition, or a
    fall below the investable threshold". For a rename all three readings are
    wrong, and the mistake points the wrong way — it invents a corporate action
    on a company that merely changed symbol.

    Measured on the first delta this job ever computed (2026-03-31 → 2026-06-30):
    5 of 95 "drops" were renames, including `BK` → `BNY` (Bank of New York
    Mellon) and `SATS` → `ECHO` (EchoStar).

    The company name is the stable key here, the way a CIK is for
    `ticker_change_check` — CRSP carries no identifier, so the name is what
    there is. Matching is therefore **exact** (case- and whitespace-normalised)
    and strictly **1:1**: a name held by two dropped or two added tickers is
    ambiguous — share classes do this routinely — and an ambiguous pair is left
    in the plain lists rather than resolved by a guess. A blank name matches
    nothing, since two absent names are not evidence of a shared identity.

    **Do NOT relax this to fuzzy matching.** It was measured on this very
    quarter, and `_name_similarity` is token-COVER based, so a shared
    distinctive token scores 1.00 (the trap `form10_watch` documents). At a
    0.85 threshold the 2026-03-31 → 2026-06-30 delta yields four candidates and
    **three are false**:

    | Pair | Truth |
    |---|---|
    | `KFS`→`KWY` Kingsway Finl Svcs → Kingsway Corp | real rename, MISSED here |
    | `FDP`→`DMC` Fresh Del Monte Produce → Del Monte Corp | different companies |
    | `USEG`→`EFOI` US Energy → Energy Focus | shares only "ENERGY" |
    | `HOTH`→`FTH` Hoth Therapeutics → Faeth Therapeutics | shares only "THERAPEUTICS" |

    A false positive here is the worst outcome the module can produce: it
    **hides a real delisting inside a rename**, which is the opposite of what
    this report exists to surface. A false negative merely leaves the name in
    the drop list, where it was before. So the miss on `KFS` is deliberate.

    A rename where the COMPANY NAME ALSO CHANGED is out of scope by
    construction — nothing in the CRSP file can link those two rows. The
    sibling modules answer that properly from SEC data, and are where such a
    case should be resolved: `ticker_change_check` (CIK is stable across a
    ticker change) and `symbol_directory` (Form 15 adjudication).
    """
    def _norm(s: str) -> str:
        return " ".join((s or "").split()).upper()

    dropped_set, added_set = set(dropped), set(added)

    def _index(rows: list[dict], keep: set[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in rows:
            tkr = r.get("Ticker", "").strip().upper()
            if tkr not in keep:
                continue
            name = _norm(r.get("Company", ""))
            if not name:
                continue
            if tkr not in out.setdefault(name, []):
                out[name].append(tkr)
        return out

    old_by_name = _index(prior, dropped_set)
    new_by_name = _index(current, added_set)

    changes = []
    for name, olds in old_by_name.items():
        news = new_by_name.get(name, [])
        if len(olds) == 1 and len(news) == 1:
            changes.append((olds[0], news[0], name))
    return sorted(changes)


def find_prior_snapshot(trade_date: str, snapshot_dir: Path = CRSP_DIR) -> Path | None:
    """Most recent snapshot strictly older than `trade_date`."""
    if not snapshot_dir.exists():
        return None
    others = sorted(
        p for p in snapshot_dir.glob("constituents_*.csv")
        if p.stem.removeprefix("constituents_") < trade_date
    )
    return others[-1] if others else None


# ── Main entry point ─────────────────────────────────────────────────────────


def snapshot(
    *,
    snapshot_dir: Path = CRSP_DIR,
    force: bool = False,
    skip_levels: bool = False,
    archive_levels: bool = False,
    dry_run: bool = False,
) -> SnapshotResult:
    """Download, verify, and archive the current quarterly constituent file.

    Returns `unchanged` when the live file's TradeDate is already archived —
    the normal outcome for three of every four monthly runs, since CRSP posts a
    new quarter roughly a month after each rebalance.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    staging = snapshot_dir / "_staging_constituents.csv"

    try:
        _download(CONSTITUENTS_URL, staging)
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        kind = classify_download_failure(exc)
        return SnapshotResult(
            status="failed",
            failure_kind=kind,
            errors=[
                f"constituents download failed [{kind}]: {exc}. "
                f"{failure_guidance(kind)}"
            ],
        )

    try:
        rows = parse_constituents(staging)
        trade_date, tm_rows, warnings = verify_total_market(rows)
    except (ValueError, KeyError) as exc:
        _remove_quietly(staging)
        return SnapshotResult(
            status="failed",
            failure_kind=CONTENT,
            errors=[
                f"verification failed [{CONTENT}]: {exc}. {failure_guidance(CONTENT)}"
            ],
        )

    dest = snapshot_dir / f"constituents_{trade_date}.csv"
    already = dest.exists()

    result = SnapshotResult(
        status="unchanged" if (already and not force) else "ok",
        trade_date=trade_date,
        constituent_count=len(tm_rows),
        path=dest,
        warnings=warnings,
    )

    classification = build_classification(rows)
    result.sector_labelled = sum(
        1 for r in tm_rows
        if classification.get(r["Ticker"].strip().upper(), {}).get("sector")
    )

    prior_path = find_prior_snapshot(trade_date, snapshot_dir)
    if prior_path:
        result.prior_trade_date = prior_path.stem.removeprefix("constituents_")
        prior_rows = [
            r for r in parse_constituents(prior_path)
            if r["Index Ticker"].strip() == TOTAL_MARKET_KEY
        ]
        result.added, result.dropped = diff_constituents(prior_rows, tm_rows)
        result.renames = detect_ticker_changes(
            prior_rows, tm_rows, result.added, result.dropped
        )

    if dry_run:
        _remove_quietly(staging)
        result.status = "skipped (dry run)"
        return result

    if already and not force:
        _remove_quietly(staging)
        log.info("CRSP snapshot for %s already archived; nothing to do", trade_date)
    else:
        _replace_with_retry(staging, dest)
        _write_json(
            snapshot_dir / f"classification_{trade_date}.json",
            {
                "trade_date": trade_date,
                "source": CONSTITUENTS_URL,
                "note": (
                    "sector is derived from CRSP sector-index membership and covers "
                    "Core Cap only; micro-caps have sector=null by construction"
                ),
                "tickers": classification,
            },
        )
        log.info("archived CRSP snapshot %s (%d names)", trade_date, len(tm_rows))

    if not skip_levels:
        levels = snapshot_dir / "index_levels.csv"
        try:
            _download(LEVELS_URL, levels)
            result.levels_path = levels
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            # Non-fatal: levels are cumulative from inception, so the previous
            # copy stays valid and only loses recent days. The constituent
            # archive is the irreplaceable half of this job. Still classified —
            # a moved levels URL is a standing task, a blip is not.
            kind = classify_download_failure(exc)
            result.warnings.append(
                f"levels download failed [{kind}] (prior copy retained): {exc}. "
                f"{failure_guidance(kind)}"
            )
        else:
            # Archive a dated, compressed copy when a new quarter lands (or on
            # demand). The working file is refreshed in place every run, which is
            # fine while the source lives — the series is cumulative from
            # inception, so each download is a superset of the last. It stops
            # being fine the moment CRSP restates history or the URL dies
            # mid-migration: an in-place refresh has already overwritten the only
            # copy by then. Quarterly (not weekly) because 52 near-identical
            # 2.8 MB snapshots a year is hoarding, not provenance.
            if archive_levels or (result.status == "ok" and not already):
                try:
                    result.levels_archive = _archive_levels(levels, snapshot_dir)
                except (OSError, ValueError) as exc:
                    result.warnings.append(f"levels archive failed: {exc}")

    return result


def _levels_last_date(path: Path) -> str:
    """Latest Date in the levels CSV — the file's own as-of, not today's date.

    Named by content rather than download date so a re-run, a retry, or a stale
    upstream file cannot mint a second archive claiming to be newer data.
    """
    latest = ""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            d = (row.get("Date") or "").strip()
            if d > latest:
                latest = d
    if not latest:
        raise ValueError(f"{path.name}: no dates found")
    return _iso_date(latest)


def _archive_levels(levels: Path, snapshot_dir: Path) -> Path | None:
    """Write `archive/index_levels_<lastdate>.csv.gz`. Returns the path, or None
    if that date is already archived."""
    import gzip
    import shutil

    archive_dir = snapshot_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"index_levels_{_levels_last_date(levels)}.csv.gz"
    if dest.exists():
        return None
    fd, tmp_name = tempfile.mkstemp(dir=str(archive_dir), suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.close(fd)
        with levels.open("rb") as src, gzip.open(tmp, "wb", compresslevel=6) as out:
            shutil.copyfileobj(src, out)
        _replace_with_retry(tmp, dest)
    except BaseException:
        _remove_quietly(tmp)
        raise
    log.info("archived levels -> %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
    return dest


def _replace_with_retry(src: Path, dest: Path, *, attempts: int = 6) -> None:
    """`src.replace(dest)`, retrying a transient Windows share lock.

    Same cause as `_remove_quietly` — Dropbox opens newly written files to hash
    them — but the opposite severity. A failed *delete* leaves harmless litter;
    a failed *replace* means the quarter was never archived, which is the one
    outcome this whole job exists to prevent. So retry harder, then raise.
    """
    import time

    for i in range(attempts):
        try:
            src.replace(dest)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.3 * (i + 1))


def _remove_quietly(path: Path, *, attempts: int = 5) -> None:
    """Delete `path`, tolerating a transient Windows lock.

    `data/` lives inside a Dropbox-synced tree, and Dropbox opens newly written
    files to hash them. That briefly holds a share lock, and `unlink` then raises
    `PermissionError: [WinError 32]` — which took down a whole run whose real
    work had already succeeded. A leftover staging file is harmless (the next run
    overwrites it and nothing else reads it), so failing to delete it must never
    be fatal.
    """
    import time

    for i in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if i == attempts - 1:
                log.warning("could not remove %s (locked); leaving it in place", path.name)
                return
            time.sleep(0.2 * (i + 1))


def _write_json(path: Path, payload: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        _replace_with_retry(tmp, path)
    except BaseException:
        _remove_quietly(tmp)
        raise


# ── Coverage-universe reconciliation ─────────────────────────────────────────


@dataclass
class ReconcileResult:
    matched: int = 0
    absent: list[dict] = field(default_factory=list)
    name_mismatches: list[dict] = field(default_factory=list)
    symbol_collisions: list[dict] = field(default_factory=list)
    checked: int = 0


US_HQ_VALUES = {"", "US", "USA", "U.S.", "UNITED STATES", "UNITED STATES OF AMERICA"}

# Plain US-style symbols only (`ABT`, `BRK.B`). A row already carrying an
# exchange suffix (`ROG.SW`, `4503.T`) cannot be confused with a US listing by
# any consumer, so it is out of scope — the same gate `ticker_change_check`
# applies for the same reason.
_US_STYLE_SYMBOL = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


_NAME_NOISE = {
    "COM", "COMMON", "STOCK", "INC", "CORP", "CORPORATION", "CO", "COS",
    "COMPANY", "COMPANIES", "LTD", "LIMITED", "PLC", "SA", "NV", "AG", "AB",
    "ASA", "SE", "HLDGS", "HOLDING", "HOLDINGS", "GROUP", "GRP", "THE", "CL",
    "CLASS", "A", "B", "C", "AND", "OF", "NEW", "ADR",
}


def _name_tokens(s: str) -> list[str]:
    s = re.sub(r"[.,&\-/()']", " ", s.upper())
    return [t for t in s.split() if t and t not in _NAME_NOISE]


def _name_similarity(a: str, b: str) -> float | None:
    """Order-insensitive similarity between two company names.

    CRSP writes names surname-first and heavily abbreviated — `Eli Lilly And Co`
    is `LILLY ELI & CO COM`, `Henry Schein` is `SCHEIN HENRY INC`. A plain
    sequence ratio scores those around 0.5 and floods the report with cosmetic
    differences, burying the findings that matter. So compare on *tokens*:
    word order stops counting, and a shared distinctive token is enough.

    Returns None when either side has no comparable token — a comparison that
    cannot be made has no result, and must not be reported as a disagreement
    (the same rule `delisted_check._name_similarity` follows).
    """
    import difflib

    sa, sb = set(_name_tokens(a)), set(_name_tokens(b))
    if not sa or not sb:
        return None
    # Prefix-aware, because CRSP truncates words rather than dropping them:
    # PHARMACEUTICALS -> PHARMA, INTERNATIONAL -> INTER, LABORATORIES -> LABS.
    # Exact-token overlap alone scores Kiniksa-vs-Kiniksa at 0.56 and buries it
    # among the real collisions.
    hits = sum(1 for x in sa if any(_token_match(x, y) for y in sb))
    overlap = hits / min(len(sa), len(sb))
    ordered = difflib.SequenceMatcher(
        None, " ".join(sorted(sa)), " ".join(sorted(sb))
    ).ratio()
    return max(overlap, ordered)


def _token_match(x: str, y: str) -> bool:
    if x == y:
        return True
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    return len(short) >= 4 and long.startswith(short)


def reconcile_universe(
    tm_rows: list[dict],
    universe_rows: list[dict],
    *,
    similarity_threshold: float = 0.55,
    foreign_hq_threshold: float = 0.70,
) -> ReconcileResult:
    """Compare the coverage universe against the CRSP total-market list.

    Two findings, deliberately kept apart:

    * **absent** — a universe ticker CRSP does not carry. This is *mostly a
      domicile fact, not a delisting*: CRSP excludes foreign issuers, ADRs, and
      OTC names, so argenx / ASML / Ascendis are all correctly absent. Treat as
      a cross-check on the cross-listing map, never as a delisting flag.
    * **name mismatch** — the ticker exists in CRSP but names a different
      company, judged by fuzzy name comparison.
    * **symbol collision** — a *foreign-HQ* row whose plain US-style symbol
      matches a US-domiciled CRSP constituent. This is the structural version of
      the same finding and it does not depend on the names looking different:
      CRSP carries US-domiciled issuers only, so a Belgian or Australian company
      sharing a symbol with a CRSP name means that symbol resolves to somebody
      else on any US feed. It catches cases fuzzy matching misses — `MED` is
      Swiss Medartis here and US Medifast on the NYSE, two names close enough in
      spelling (0.63) to slip a similarity threshold while being entirely
      different companies.

    The two overlap but neither contains the other, so they are reported apart.
    """
    by_ticker = {r["Ticker"].strip().upper(): r for r in tm_rows}
    out = ReconcileResult()

    for row in universe_rows:
        tkr = (row.get("Ticker") or "").strip().upper()
        if not tkr:
            continue
        out.checked += 1
        crsp = by_ticker.get(tkr)
        if crsp is None:
            out.absent.append({
                "ticker": tkr,
                "company": (row.get("Company Name") or "").strip(),
                "exchange": (row.get("Exchange Code") or "").strip(),
                "country": (row.get("Country (HQ)") or "").strip(),
            })
            continue
        out.matched += 1
        cm_name = (row.get("Company Name") or "").strip()
        hq = (row.get("Country (HQ)") or "").strip()
        if not cm_name:
            continue
        sim = _name_similarity(cm_name, crsp["Company"])

        # Foreign HQ is a *prior*, not a finding on its own: Irish and UK
        # inversions (Medtronic, Linde, Jazz, Perrigo) are foreign-domiciled and
        # legitimately in CRSP under matching names. Flagging on domicile alone
        # produced 12 rows of which 9 were those. What it does justify is a
        # stricter name test — a foreign row whose name only half-matches is far
        # likelier to be a symbol collision than a US row scoring the same.
        foreign = hq.upper() not in US_HQ_VALUES
        if (
            foreign
            and sim is not None
            and sim < foreign_hq_threshold
            and _US_STYLE_SYMBOL.match(tkr)
        ):
            out.symbol_collisions.append({
                "ticker": tkr,
                "cm_name": cm_name,
                "crsp_name": crsp["Company"].strip(),
                "country": hq,
                "similarity": round(sim, 3),
                "exchange": (row.get("Exchange Code") or "").strip(),
            })

        if sim is not None and sim < similarity_threshold:
            out.name_mismatches.append({
                "ticker": tkr,
                "cm_name": cm_name,
                "crsp_name": crsp["Company"].strip(),
                "similarity": round(sim, 3),
                "country": (row.get("Country (HQ)") or "").strip(),
            })

    out.name_mismatches.sort(key=lambda d: d["similarity"])
    out.symbol_collisions.sort(key=lambda d: d["ticker"])
    return out


# ── Report ───────────────────────────────────────────────────────────────────


def write_report(
    result: SnapshotResult,
    recon: ReconcileResult | None,
    *,
    reports_dir: Path | None = None,
    today: str | None = None,
) -> Path:
    reports_dir = reports_dir or config.REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    today = today or date.today().strftime("%Y-%m-%d")
    path = reports_dir / f"crsp_snapshot_{today}.md"
    path.write_text(render_report(result, recon, today=today), encoding="utf-8")
    return path


def render_report(
    result: SnapshotResult,
    recon: ReconcileResult | None = None,
    *,
    today: str | None = None,
) -> str:
    """Pure markdown renderer.

    Split out of `write_report` so the delta presentation can be tested without
    a filesystem — the ticker-change section below is presentation logic, and
    getting it wrong is exactly the class of error that ships silently.
    """
    today = today or date.today().strftime("%Y-%m-%d")

    L = [f"# CRSP US Total Market snapshot — {today}", ""]
    L.append(f"**Status:** `{result.status}`")
    if result.failure_kind:
        L.append(f"**Failure kind:** `{result.failure_kind}` — {failure_guidance(result.failure_kind)}")
    if result.trade_date:
        L.append(f"**Index TradeDate:** {result.trade_date}")
        L.append(f"**Constituents:** {result.constituent_count:,}")
        pct = (result.sector_labelled / result.constituent_count * 100) if result.constituent_count else 0
        L.append(
            f"**Sector-labelled:** {result.sector_labelled:,} ({pct:.1f}%) — "
            "sector indexes cover Core Cap only; micro-caps are unlabelled by construction"
        )
    L.append("")

    for e in result.errors:
        L.append(f"- ❌ {e}")
    for w in result.warnings:
        L.append(f"- ⚠️ {w}")
    if result.errors or result.warnings:
        L.append("")

    if result.prior_trade_date:
        net_dropped = result.net_dropped
        net_added = result.net_added

        L += [
            f"## Delta vs {result.prior_trade_date}",
            "",
            f"- **Added:** {len(net_added)}",
            f"- **Dropped:** {len(net_dropped)}",
        ]
        if result.renames:
            L.append(f"- **Ticker changes:** {len(result.renames)}")
        L += [
            "",
            "A dropped name is a delisting, acquisition, or a fall below the "
            "investable threshold — it does not say which. Ticker changes are "
            "counted separately below and are **none of those things**.",
            "",
        ]
        if result.renames:
            L += [
                "### Ticker changes",
                "",
                "Same company, new symbol — a ticker-keyed diff sees this as a drop "
                "plus an add. Paired here on an exact company-name match, 1:1 only.",
                "",
                "A rename where the **company name also changed** is not detectable "
                "from this file and is NOT counted here — it stays in the dropped "
                "list. `ticker_change_check` and `symbol_directory` resolve that "
                "class from SEC data.",
                "",
                "| Was | Now | Company |",
                "|---|---|---|",
            ]
            L += [f"| `{old}` | `{new}` | {name} |" for old, new, name in result.renames]
            L.append("")
        if net_dropped:
            L += ["**Dropped:**", "", "```", ", ".join(net_dropped), "```", ""]
        if net_added:
            L += ["**Added:**", "", "```", ", ".join(net_added), "```", ""]
    else:
        L += [
            "## Delta",
            "",
            "_No prior snapshot to compare against. Deltas begin at the next quarter._",
            "",
        ]

    if recon:
        L += [
            "## Coverage-universe reconciliation",
            "",
            f"- Universe rows checked: **{recon.checked:,}**",
            f"- Present in CRSP total market: **{recon.matched:,}**",
            f"- Absent from CRSP: **{len(recon.absent):,}** "
            "(mostly foreign issuers / ADRs / OTC — CRSP is US-domiciled only; "
            "**not** a delisting signal)",
            f"- Ticker present but names a different company: **{len(recon.name_mismatches):,}**",
            f"- Foreign-HQ rows whose symbol collides with a US listing: "
            f"**{len(recon.symbol_collisions):,}**",
            "",
        ]
        if recon.symbol_collisions:
            L += [
                "### Symbol collisions — foreign HQ, US-listed symbol",
                "",
                "CRSP carries US-domiciled issuers only. These rows name a foreign "
                "company under a plain symbol that belongs to a different, "
                "US-domiciled company — so any US price or fundamentals lookup on "
                "the bare symbol returns the wrong issuer.",
                "",
                "| Ticker | Coverage Manager | US listing (CRSP) | Sim | HQ | Exch |",
                "|---|---|---|---:|---|---|",
            ]
            for m in recon.symbol_collisions:
                L.append(
                    f'| `{m["ticker"]}` | {m["cm_name"]} | {m["crsp_name"]} '
                    f'| {m["similarity"]:.2f} | {m["country"]} | {m["exchange"]} |'
                )
            L.append("")
        if recon.name_mismatches:
            L += [
                "### Name mismatches — review these",
                "",
                "A US price lookup on these symbols may resolve to the CRSP company, "
                "not yours.",
                "",
                "| Ticker | Coverage Manager | CRSP (US listing) | Sim | HQ |",
                "|---|---|---|---:|---|",
            ]
            for m in recon.name_mismatches:
                L.append(
                    f'| `{m["ticker"]}` | {m["cm_name"]} | {m["crsp_name"]} '
                    f'| {m["similarity"]:.2f} | {m["country"]} |'
                )
            L.append("")

    return "\n".join(L)
