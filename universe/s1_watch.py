"""Discover US IPO candidates from S-1 / F-1 registrations, before they price.

**The gap this closes.** Every IPO source this project has is *offering-shaped*:
the Finnhub IPO calendar wants terms, the symbol-directory diff wants a listed
symbol, the Russell lane wants an index membership. All three need a security
that already trades, or is about to within days. A company that has filed to go
public but has not set terms has no ticker and no market cap, so it is invisible
to all of them.

Measured 2026-09-06 against Renaissance Capital's weekly filing recap. That week
named eight new or refreshed registrants -- SB Energy, Accelevation, Syntiant,
Oura, Wella, Tailored Brands, Cumberland Farms, Entrata. **Not one appeared in
any Coverage Manager artifact**, and two of them (Oura, a wearable + subscription
health company; Syntiant, an edge-AI chip designer) are squarely Bucket 1. The
filing-to-pricing gap is typically four to eight weeks, so the whole pipeline was
arriving as a surprise on the day it became untradeable-as-news.

**S-1 vs F-1.** S-1 is the domestic registration statement; F-1 is the same thing
for a foreign private issuer. Both are searched, because Bucket 2 is explicitly
global and the largest deals of the last two years (Shein among them) are F-1s.

**This lane never adds anything.** An S-1 filer has no market cap -- there is no
price and often no share count -- so it cannot be tested against Bucket 2's $25B
bar or Bucket 3's $10B bar, and `auto_add` must never see it. Everything here is
a *watch* entry. That separation is the point: the report's adds are decisions,
this is the pipeline behind them.

**Proposed maximum aggregate offering price is reported, and it is not a
valuation.** It is the number the registrant pays SEC fees on. On a first filing
it is very often a placeholder -- $100,000,000 is the convention regardless of
the real deal size -- so `is_placeholder` marks the round numbers and the report
says "fee-table placeholder" rather than printing a fake $100M deal. Even when
real it is the *raise*, not the market cap: a $5B raise at a 15% float implies a
~$33B company. The report says raise, never cap, and the size test it feeds is a
soft "big enough to check" flag, never a bucket verdict.

**Three states**, matching `form10_watch`: `relevant` / `not-relevant` /
`inconclusive`. A registrant whose SIC is missing or unmapped is inconclusive and
is reported, never silently dropped -- most of the ~1,200 annual S-1 filings are
shells, blank-check vehicles and tiny resale registrations, and the ones this
module cannot classify are exactly where a missed IPO would hide.

**Carry-forward, and how an entry closes.** A 14-day window finds new *filings*;
it does not describe the *pipeline*. Entrata filed in May 2026 and revived in
September -- a two-week window reports it in neither month. An entry stays open
until one of three things happens: its ticker turns up in the universe (it priced
and was added), the registrant files an `RW` (registration withdrawn), or it ages
past `CARRY_MAX_AGE_DAYS`. A withdrawal is checked for, not assumed from silence.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from universe import confidential_watch
from universe.form10_watch import (
    CORE_SECTORS, _ascii, _get, sector_for_sic,
)

logger = logging.getLogger(__name__)

FTS_URL = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_DIR = "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh}/"
SEEN_PATH = Path("data/s1_seen.json")

# Initial registrations and their amendments. Amendments matter: Renaissance's
# "four more on file refreshed their papers" is four S-1/A filings, and a refresh
# after months of silence is the single best public tell that a deal is being
# marketed. They are searched, then classified as `update` when the registrant is
# already known -- see `FILING_NEW` / `FILING_UPDATE`.
FORMS = ("S-1", "S-1/A", "F-1", "F-1/A")

FILING_NEW = "new filing"
FILING_UPDATE = "refiled / amended"

# De-SPAC registrations. An S-4 registers a stock-for-stock merger; when the
# FILER is a blank-check vehicle, that merger is a company coming public.
#
# Deliberately a LINE IN THIS REPORT AND NOT A LANE. Measured 2026-06-06..09-06:
# 131 distinct S-4/F-4 registrants, of which 6 had a blank-check filer -- about
# 24 a year. The other 125 are ordinary M&A, which other lanes already handle.
# Two things make a full lane the wrong shape at that volume:
#   - The filer's SIC is 6770, so the TARGET's sector -- the only thing that
#     decides a bucket -- is not in the metadata. Routing would need the S-4
#     parsed to identify and resolve the target, which is exactly the fragile
#     parent-resolution `form10_watch` refuses to gate on.
#   - De-SPACs do not clear Bucket 2 ($25B) or Bucket 3 ($10B). They are Bucket 1
#     only, and Bucket 1 at any size runs into the standing instruction not to
#     recommend sub-scale names.
# And the post-close ticker is caught anyway by the symbol-directory diff. So
# this buys LEAD TIME on a handful of names a quarter, at the cost of one extra
# search: the names are listed, no sector is claimed, and JP decides by name.
DESPAC_FORMS = ("S-4", "F-4")
BLANK_CHECK_SIC = {"6770", "6199"}

# A registration statement this old without pricing is dead in all but name.
# Deliberately longer than form10_watch's 540: an IPO window can close for a year
# and reopen (Entrata filed May 2026, revived September 2026), and a withdrawal
# is detected explicitly by `RW`, so age is the backstop and not the main test.
CARRY_MAX_AGE_DAYS = 400

# Fee-table conventions. A registrant that has not set terms writes a round
# number purely to compute the filing fee. Treating these as deal sizes produced
# a "$100M IPO" line for a company that raised $5B, which is worse than no
# number at all, so they are marked and printed as placeholders.
_PLACEHOLDER_VALUES = {1_000_000.0, 10_000_000.0, 20_000_000.0, 25_000_000.0,
                       50_000_000.0, 75_000_000.0, 100_000_000.0,
                       150_000_000.0, 200_000_000.0, 250_000_000.0,
                       300_000_000.0, 500_000_000.0,
                       # $1B is a fee-table convention too, and it is exactly
                       # BUCKET2_CHECK_RAISE -- omitting it meant the single
                       # most common large placeholder was the one value that
                       # could fire a Bucket 2 flag on no evidence at all.
                       750_000_000.0, 1_000_000_000.0}

# Soft flag only. A raise at or above this is large enough that the resulting
# market cap could plausibly clear Bucket 2's $25B bar at a normal float, so the
# report asks for a look. It is NOT a bucket verdict -- see the module docstring.
BUCKET2_CHECK_RAISE = 1_000_000_000.0

# SIC codes that are almost always a shell, a blank-check vehicle or a fund.
# These dominate the raw S-1 count and carry no information for this lane.
_SHELL_SIC = {"6770", "6199", "6221", "6726", "6798"}


@dataclass
class Registration:
    cik: str
    registrant: str
    ticker: str
    accession: str
    filed: str
    form: str
    sic: str
    sector: str = ""
    verdict: str = "inconclusive"
    reason: str = ""
    doc: str = ""
    doc_rank: int = 9
    # The NEWEST filing for this registrant, which is a different document from
    # the earliest one `accession`/`doc`/`filed` describe. Both are needed and
    # they answer different questions: `filed` is how long this has been in the
    # pipeline, `latest_*` is where the current numbers are. Conflating them
    # made the accession-change size refresh unfireable -- dedup keeps the
    # earliest accession, so it never changed, so the terms-setting S-1/A was
    # the one document never read.
    latest_accession: str = ""
    latest_doc: str = ""
    latest_filed: str = ""
    filing_kind: str = FILING_NEW
    raise_usd: float | None = None
    raise_is_placeholder: bool = False
    withdrawn: bool = False
    first_seen: str = ""

    @property
    def is_foreign(self) -> bool:
        return self.form.upper().startswith("F-")

    # True when the registrant is ALREADY a public reporting company, so this
    # filing is a resale or shelf registration rather than a company coming
    # public. Set by `check_reporting_history`, never inferred from the ticker --
    # see that function for why the ticker is the wrong test.
    already_listed: bool = False
    #: False when the SEC reporting-history check could not be made. The row is
    #: kept in the pipeline (over-inclusion is the safe direction), but the fact
    #: that it is UNVERIFIED has to survive into the carry ledger -- otherwise
    #: the filing leaves the search window, carry-forward rebuilds it checking
    #: only withdrawal and pricing, and it reports as verified and green for up
    #: to CARRY_MAX_AGE_DAYS.
    reporting_verified: bool = True

    def raise_label(self) -> str:
        """Human-readable proposed raise. Never presented as a market cap."""
        if self.raise_usd is None:
            return "not stated"
        if self.raise_is_placeholder:
            if self.raise_usd >= 1e9:
                return f"${self.raise_usd/1e9:,.1f}B (fee-table placeholder)"
            return f"${self.raise_usd/1e6:,.0f}M (fee-table placeholder)"
        if self.raise_usd >= 1e9:
            return f"~${self.raise_usd/1e9:,.1f}B proposed raise"
        # The floor in `extract_raise` is $100k, so values under $1M are real and
        # reachable -- and `,.0f` rendered every one of them as "~$0M proposed
        # raise", which is a confidently wrong number rather than a small one.
        if self.raise_usd >= 1e6:
            return f"~${self.raise_usd/1e6:,.1f}M proposed raise"
        return f"~${self.raise_usd/1e3:,.0f}K proposed raise"


@dataclass
class WatchResult:
    status: str                      # ok | inconclusive
    filings: list[Registration] = field(default_factory=list)
    error: str = ""
    window: tuple[str, str] = ("", "")


# ------------------------------------------------------------------- discovery


def search_registrations(start: str, end: str, *, ua: str, opener=None,
                         forms: tuple[str, ...] = FORMS) -> WatchResult:
    """EDGAR full-text search for S-1 / F-1 registrations in a date window.

    One search per form type rather than a comma-joined `forms` parameter: FTS
    treats `S-1` and `S-1/A` as distinct types, and a joined query that silently
    matched only the first would look exactly like a quiet week. Paging is
    identical to `form10_watch.search_form10` and for the same reason -- FTS
    returns ten *documents* per page, and one S-1 carries dozens of exhibits, so
    reading page one samples boilerplate rather than filings.
    """
    opener = opener or (lambda u: _get(u, ua, accept="application/json"))
    by_cik: dict[str, Registration] = {}
    newest: dict[str, tuple[str, int, str, str]] = {}   # cik -> (filed, rank, adsh, doc)
    totals: dict[str, int] = {}

    for form in forms:
        hits: list[dict] = []
        offset, total = 0, None
        while True:
            params = urllib.parse.urlencode(
                {"q": '""', "forms": form, "startdt": start, "enddt": end,
                 "from": offset})
            try:
                data = json.loads(opener(f"{FTS_URL}?{params}").read())
            except Exception as exc:               # noqa: BLE001
                # One unreachable form type makes the whole window unreliable:
                # reporting the other three as if they were the pipeline is the
                # "quiet week" lie this lane exists to prevent.
                return WatchResult("inconclusive",
                                   error=f"{type(exc).__name__}: {exc} "
                                         f"(form {form}, offset {offset})",
                                   window=(start, end))
            page = data.get("hits", {}).get("hits", [])
            if total is None:
                total = (data.get("hits", {}).get("total") or {}).get("value", 0)
                totals[form] = total
            hits.extend(page)
            offset += len(page)
            if not page or offset >= min(total or 0, 9990):
                break

        for hit in hits:
            src = hit.get("_source", {})
            ciks = src.get("ciks") or []
            if not ciks:
                continue
            cik = str(ciks[0]).lstrip("0")
            display = (src.get("display_names") or [""])[0]
            name = re.sub(r"\s*\(.*", "", display).strip()
            tick = ""
            m = re.search(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s*\(CIK", display)
            if m:
                tick = m.group(1)
            r = Registration(
                cik=cik, registrant=name, ticker=tick,
                accession=src.get("adsh") or "", filed=src.get("file_date") or "",
                form=src.get("form") or form,
                sic=(src.get("sics") or [""])[0] or "",
                doc=(hit.get("_id") or "").split(":")[-1])
            # Prefer the primary registration document over its exhibits: the
            # cover page and the fee table live there, and an EX-23.1 auditor
            # consent has neither.
            ftype = (src.get("file_type") or "").upper()
            r.doc_rank = (0 if ftype in ("S-1", "S-1/A", "F-1", "F-1/A")
                          else (1 if ftype.startswith("EX-FILING") else 2))
            # Newest filing per registrant, tracked independently of the
            # earliest one. Sort key is (date asc, doc_rank asc), so the newest
            # date wins and the best document within that date wins.
            best = newest.get(cik)
            if best is None or (r.filed, -r.doc_rank) > (best[0], -best[1]):
                newest[cik] = (r.filed, r.doc_rank, r.accession, r.doc)

            prev = by_cik.get(cik)
            if prev is None or (r.filed, r.doc_rank) < (prev.filed, prev.doc_rank):
                # Keep the earliest filing per registrant, but carry forward the
                # fact that an amendment exists -- a refresh is the news.
                if prev is not None and prev.form.endswith("/A"):
                    r.filing_kind = FILING_UPDATE
                by_cik[cik] = r
            elif r.form.endswith("/A") and not prev.form.endswith("/A"):
                prev.filing_kind = FILING_UPDATE

    for cik, r in by_cik.items():
        if r.form.endswith("/A"):
            r.filing_kind = FILING_UPDATE
        filed, _rank, adsh, doc = newest.get(cik, (r.filed, r.doc_rank,
                                                   r.accession, r.doc))
        r.latest_filed, r.latest_accession, r.latest_doc = filed, adsh, doc

    # A plain S-1 is the highest-volume registration form there is; the floor in
    # any market, dead or not, is dozens a fortnight. Zero means the query shape
    # changed or the endpoint answered 200 with an empty body -- NOT a quiet
    # week. Reported as inconclusive, because "0 distinct registrants" printed
    # without comment is the exact quiet-incomplete answer this lane exists to
    # avoid, and it is indistinguishable from a working run at a glance.
    if "S-1" in forms and totals.get("S-1", 0) == 0:
        return WatchResult(
            "inconclusive",
            error=("EDGAR full-text search returned 0 S-1 filings for "
                   f"{start}..{end} -- implausible; treating as unavailable "
                   "rather than as a quiet week"),
            window=(start, end))

    return WatchResult("ok",
                       filings=sorted(by_cik.values(),
                                      key=lambda x: (x.filed, x.registrant)),
                       window=(start, end))


# --------------------------------------------------------------- classification


def classify(r: Registration) -> Registration:
    """Assign sector and a three-state verdict from the registrant's own SIC.

    Deliberately the same SIC map as `form10_watch` (imported, not copied) so the
    two forward lanes cannot drift into disagreeing about what "core" means.
    """
    r.sector = sector_for_sic(r.sic)
    if r.sector in CORE_SECTORS:
        r.verdict = "relevant"
        r.reason = (f"Bucket 1 - core sector ({r.sector}, SIC {r.sic}); "
                    f"relevant at any size once it prices")
    elif r.sector:
        r.verdict = "relevant"
        r.reason = (f"Bucket 1 adjacency ({r.sector}, SIC {r.sic}); "
                    f"judge on relevance to the sheet when terms are set")
    elif r.sic in _SHELL_SIC:
        r.verdict = "not-relevant"
        r.reason = f"SIC {r.sic} - blank-check / shell / fund vehicle"
    elif not r.sic:
        r.verdict = "inconclusive"
        r.reason = "no SIC on the filing - cannot classify, review by hand"
    else:
        r.verdict = "not-relevant"
        r.reason = f"SIC {r.sic} is outside the covered sectors"
    return r


def apply_size_flag(r: Registration) -> Registration:
    """Raise a soft Bucket 2 flag on a very large proposed raise.

    Runs AFTER `classify`, and can only ever *promote* a not-relevant row to
    inconclusive -- never demote a relevant one. Bucket 2 is any sector at $25B+,
    so a $5B raise from a company whose SIC this module does not cover is exactly
    the case the sector test is designed to miss. It is reported as a question,
    not an answer, because a raise is not a market cap.
    """
    if r.raise_usd is None or r.raise_is_placeholder:
        return r
    if r.raise_usd < BUCKET2_CHECK_RAISE:
        return r
    if r.already_listed:
        # A large raise by a company that already trades is a follow-on. Its cap
        # is knowable today from the tape, so it is a Bucket 4 question for the
        # normal screen, not a pre-IPO one for this lane to speculate about.
        return r
    if r.verdict == "not-relevant":
        r.verdict = "inconclusive"
        r.reason = (f"outside the covered sectors (SIC {r.sic or '-'}), but a "
                    f"{r.raise_label()} could imply a Bucket 2 (>=$25B) company "
                    f"at a normal float - check the cap when terms are set")
    else:
        r.reason += f" | large deal: {r.raise_label()}"
    return r


# ------------------------------------------------------------ offering size


# The structured fee tag, and the only reliable source. Since SEC's 2022
# fee-tagging rule every registration statement files an `EX-FILING FEES`
# exhibit with an XBRL sidecar, and the amount is one element:
#   <ffd:MaxAggtOfferingPric ... unitRef="USD">100000000.00</ffd:MaxAggtOfferingPric>
_FEE_XBRL = re.compile(r"MaxAggtOfferingPric[^>]*>\s*([\d,]+(?:\.\d+)?)\s*<", re.I)

# Prose fallbacks, for pre-2022 filings and foreign issuers that still print the
# table on the cover. NOT used on the rendered fee exhibit: its header row names
# every column before any value, so "the phrase then the next number" matches the
# wrong cell. The XBRL tag exists precisely to make this guesswork unnecessary.
_FEE_PROSE = [
    re.compile(r"aggregate\s+offering\s+price\s+of\s+up\s+to\s+\$\s*"
               r"([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"maximum\s+aggregate\s+offering\s+price[^$]{0,80}?\$\s*"
               r"([\d,]+(?:\.\d+)?)", re.I | re.S),
]


def extract_raise(text: str) -> tuple[float | None, bool]:
    """-> (proposed maximum aggregate offering price in USD, is_placeholder).

    The XBRL tag is tried first and, when present, wins outright: it is the
    filer's own tagged number, so falling back to prose after finding it could
    only replace a fact with a guess.

    Within a source, returns the LARGEST match rather than the first. A fee table
    lists a row per security class, and the first row on a multi-class filing is
    routinely a $1,000 stub. Values are sanity-bounded: under $100k is a fee
    artefact, over $500B is a parse error (a stray share count read as dollars),
    and both are safer as "not stated" than as a wrong number in a table JP reads
    to decide what to look at.
    """
    def _best(pats) -> float | None:
        best: float | None = None
        for pat in pats:
            for m in pat.finditer(text):
                try:
                    val = float(m.group(1).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                if not (100_000.0 <= val <= 500_000_000_000.0):
                    continue
                if best is None or val > best:
                    best = val
        return best

    val = _best([_FEE_XBRL])
    if val is None:
        val = _best(_FEE_PROSE)
    if val is None:
        return None, False
    return val, val in _PLACEHOLDER_VALUES


_FEE_DOC_RE = re.compile(r"(fee|ex-?fil)", re.I)


def fetch_registration_cover(cik: str, accession: str, filename: str, *,
                             ua: str, opener=None) -> str:
    """Text of the filing's fee table, falling back to the primary document.

    **The fee table is not on the cover any more.** Before SEC's 2022 fee-tagging
    rule the registration-fee table was printed on the S-1 cover page, which is
    what the first version of this function assumed. It is now filed as its own
    `EX-FILING FEES` exhibit, and reading the cover returned "not stated" for
    every registrant in the 2026-08-23..09-06 window -- 104 filings, zero sizes,
    and a `Proposed raise` column that was decoration.

    So the filing's `index.json` is read first and the fee exhibit picked out of
    it by name. The primary document remains the fallback, because pre-2022
    filings and some foreign issuers still print the table on the cover.

    Bounded at 400KB: an S-1 runs to several megabytes, and this runs over every
    relevant registrant on a Friday-morning scheduled job.
    """
    if not (cik and accession):
        return ""
    adsh = accession.replace("-", "")
    base = ARCHIVE_DIR.format(cik=cik, adsh=adsh)
    opener = opener or (lambda u: _get(u, ua, timeout=30))

    def _read(url: str) -> str:
        try:
            raw = opener(url).read(400_000)
        except Exception as exc:                   # noqa: BLE001
            logger.debug("document unavailable %s (%s)", url, exc)
            return ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return raw

    names: list[str] = []
    idx = _read(base + "index.json")
    if idx:
        try:
            items = [str(i.get("name", ""))
                     for i in json.loads(idx).get("directory", {}).get("item", [])]
        except ValueError:
            items = []
        fee = [n for n in items
               if _FEE_DOC_RE.search(n) and n.lower().endswith((".htm", ".html", ".xml"))]
        # XBRL sidecar first: it carries the tagged amount. The rendered .htm is
        # only useful for its prose, and its header row lists every column name
        # before any value, so parsing it is guesswork the sidecar removes.
        names = [n for n in fee if n.lower().endswith(".xml")] + \
                [n for n in fee if not n.lower().endswith(".xml")]

    for name in names + ([filename] if filename else []):
        text = _read(base + name)
        if not text:
            continue
        if extract_raise(text)[0] is not None:
            return text
    return ""


# ------------------------------------------------------------------ withdrawal


_REPORTING_FORMS = {"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A", "20-F/A"}


def check_reporting_history(cik: str, *, ua: str, opener=None) -> bool | None:
    """True when the registrant already files periodic reports -> follow-on.

    **This separates an IPO candidate from noise, and the obvious test fails.**
    The first cut used "does EDGAR show a ticker": an S-1 from a company with a
    symbol must be a resale. It is wrong, and wrong on exactly the names this
    lane was built to catch. Syntiant reserved `SYTN` and Entrata reserved `ENT`
    in their registration statements, SEC carries the reserved symbol, and both
    -- the two headline pre-IPO names of the 2026-09-06 Renaissance recap --
    were filed under "already trading".

    SEC's `exchanges` field is no better: `form10_watch` documents that a
    registrant certified for listing already shows its destination venue while
    still being pre-listing.

    What actually separates them is periodic reporting. A company that trades
    files 10-Ks and 10-Qs; a company that has only registered has none. That is
    a fact about the past, so a reserved ticker and a certified exchange cannot
    forge it.

    Returns None when SEC is unreachable -- the caller keeps the row in the
    pipeline, because an unknown registrant is a candidate to look at, and the
    cost of a stray follow-on in the table is far below the cost of hiding an IPO.
    """
    opener = opener or (lambda u: _get(u, ua, timeout=25))
    try:
        data = json.loads(opener(SUBMISSIONS.format(cik=cik.zfill(10))).read())
    except Exception:                              # noqa: BLE001
        return None
    forms = (data.get("filings", {}).get("recent", {}) or {}).get("form", [])
    return any(str(f).upper() in _REPORTING_FORMS for f in forms)


def check_status(cik: str, *, ua: str, opener=None,
                 since: str = "") -> tuple[bool, bool]:
    """-> (withdrawn, priced) for a carried registrant, from SEC submissions.

    **`since` is the registrant's own filing date and it is not optional in
    practice.** Without it, ANY historical `RW`/`AW` or `424B*` closes the
    entry -- and withdrawing a 2021-22 registration and re-filing in 2025-26 is
    the dominant pattern in this IPO cohort. Such a company carries an old `RW`
    forever, so the week after it left the search window it was marked withdrawn
    and vanished, then un-vanished when it next amended: a name flickering in and
    out of the pipeline for reasons that have nothing to do with its deal. A
    stale `424B3` resale supplement from a prior shelf read as "priced" the same
    way.

    **Withdrawn** is an `RW`/`AW` on file, checked explicitly rather than
    inferred from silence. A registration that goes quiet for six months is
    usually a delayed deal, not a dead one, and dropping it on a guess is how
    Entrata's May filing would have vanished before its September revival.

    **Priced** is a `424B` final prospectus, which is filed at pricing. This
    closes the other end of the carry: without it an entry sits in the pipeline
    until it ages out 400 days later, because the search window that would
    refresh it has long since moved past its filing date.

    Deliberately NOT the ticker. A registrant reserves its symbol in the S-1 --
    Syntiant had `SYTN` and Entrata `ENT` while both were still pre-IPO -- so a
    ticker says nothing about whether shares have priced.

    One request per carried name, once a week, for the handful that are carried.
    """
    opener = opener or (lambda u: _get(u, ua, timeout=25))
    try:
        data = json.loads(opener(SUBMISSIONS.format(cik=cik.zfill(10))).read())
    except Exception:                              # noqa: BLE001
        return False, False                        # unknown closes nothing
    recent = (data.get("filings", {}).get("recent", {}) or {})
    forms = [str(f).upper() for f in recent.get("form", [])]
    dates = [str(d) for d in recent.get("filingDate", [])]
    withdrawn = priced = False
    for n, form in enumerate(forms):
        when = dates[n] if n < len(dates) else ""
        if since and when and when < since:
            continue                               # predates this registration
        if form in ("RW", "AW"):
            withdrawn = True
        elif form.startswith("424B"):
            priced = True
    return withdrawn, priced


# --------------------------------------------------------------------- report


class LedgerCorrupt(RuntimeError):
    """The seen-ledger exists but could not be parsed."""


class LedgerNotSaved(RuntimeError):
    """The seen-ledger could not be written."""


def save_seen(path: Path, seen: dict) -> None:
    """Write the carry ledger, or RAISE.

    `form10_watch.save_seen` logs a warning and returns on OSError. Inherited
    here that is a silent loss of the only state this lane accumulates: the run
    still returns `ok`, the step is green, and next week every carried entry is
    gone with nothing in the summary to say so. A Dropbox folder makes the
    failure realistic -- the temp-file rename loses to the sync client often
    enough to matter.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        raise LedgerNotSaved(f"could not write {path.name} ({exc}); carry-forward "
                             f"state for {len(seen)} registrant(s) is lost") from exc


def load_seen(path: Path) -> dict:
    """The carry ledger. Raises `LedgerCorrupt` rather than returning empty.

    `form10_watch.load_seen` warns and returns `{}` on unreadable JSON. Inherited
    here that is destructive rather than merely lossy: an empty ledger means
    carry-forward yields nothing, every filing looks new, and `save_seen` then
    REPLACES the unreadable file with only this window's rows -- so a single bad
    read permanently deletes the pipeline history the lane exists to accumulate.

    This repo lives in a Dropbox folder. A JSON file written once a week by a
    scheduled job on one machine and synced to others is exactly the file that
    acquires a conflicted copy or a half-written body, so this is a question of
    when, not whether. A missing file is still a legitimate first run.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LedgerCorrupt(f"{path.name} is unreadable ({exc}); refusing to "
                            f"overwrite it with a partial ledger") from exc
    if not isinstance(data, dict):
        raise LedgerCorrupt(f"{path.name} is not a JSON object")
    return data


def is_coming_public(r: Registration) -> bool:
    """One definition, used by the report, the weekly step and the exit code.

    Three call sites had three notions of "worth surfacing", so a week whose only
    discoveries were out-of-sector operating registrants -- the Bucket 2 group --
    rendered a populated report, logged nothing, and exited 0.
    """
    if r.already_listed or r.sic in _SHELL_SIC:
        return False
    return r.verdict in ("relevant", "inconclusive", "not-relevant")


def partition(regs: list[Registration]) -> dict[str, list[Registration]]:
    """The one five-way split, used by the report, its header and the weekly step.

    Every row lands in exactly one bucket and the buckets sum to `len(regs)` --
    asserted below, because a partition that is total in the BODY but not in the
    COUNTS prints a header whose categories do not add up to its own total, and
    a reader who notices has no way to tell which section is hiding a row.

    - `pipeline`  in-sector, coming public. The table JP reads.
    - `other`     out-of-sector, coming public. The Bucket 2 candidates: a sector
                  test cannot decide these, only a price can.
    - `inconclusive` coming public, unclassifiable.
    - `followon`  already reporting AND in-sector or unclassifiable -- the ones
                  that would otherwise have looked like candidates. An
                  already-listed registrant in an uncovered sector is a resale
                  shelf by a company this universe does not follow; naming all of
                  those took the section from 17 rows to 41 and made "scan this
                  for a covered name" impossible.
    - `rest`      shells, blank-check vehicles, and out-of-sector follow-ons.
    """
    coming = [r for r in regs if is_coming_public(r)]
    followon = [r for r in regs
                if r.already_listed and r.sic not in _SHELL_SIC
                and r.verdict in ("relevant", "inconclusive")]
    claimed = {id(r) for r in coming} | {id(r) for r in followon}
    out = {
        "pipeline": [r for r in coming if r.verdict == "relevant"],
        "other": [r for r in coming if r.verdict == "not-relevant"],
        "inconclusive": [r for r in coming if r.verdict == "inconclusive"],
        "followon": followon,
        "rest": [r for r in regs if id(r) not in claimed],
    }
    assert sum(len(v) for v in out.values()) == len(regs), (
        "partition is not total: "
        + ", ".join(f"{k}={len(v)}" for k, v in out.items())
        + f" against {len(regs)} registrant(s)")
    return out


def search_despacs(start: str, end: str, *, ua: str, opener=None) -> list[Registration]:
    """S-4 / F-4 registrations whose FILER is a blank-check vehicle.

    No sector is claimed and no size is read: the filer is the SPAC, so its SIC
    describes the shell rather than the business coming public. Returns names and
    dates for a human to judge. Never raises -- a de-SPAC line is a bonus, and
    losing it must not cost the report it rides on.
    """
    try:
        res = search_registrations(start, end, ua=ua, opener=opener,
                                   forms=DESPAC_FORMS)
    except Exception as exc:                       # noqa: BLE001
        logger.warning("de-SPAC search failed (%s); the line is omitted", exc)
        return []
    if res.status != "ok":
        logger.warning("de-SPAC search unavailable: %s", res.error)
        return []
    return [r for r in res.filings if r.sic in BLANK_CHECK_SIC]


def render_report(regs: list[Registration], window: tuple[str, str],
                  fresh: set[str], carried: set[str] | None = None,
                  despacs: list[Registration] | None = None,
                  confidential_md: str = "") -> str:
    despacs = list(despacs or [])
    parts = partition(regs)
    rel, other = parts["pipeline"], parts["other"]
    inc, followon, non = parts["inconclusive"], parts["followon"], parts["rest"]
    out = [f"# S-1 / F-1 IPO pipeline watch - {window[0]} to {window[1]}", "",
           f"**{len(regs)} distinct registrants** "
           f"({len(fresh)} new since the last run) - "
           f"{len(rel)} in-sector | {len(other)} out-of-sector "
           f"| {len(inc)} inconclusive | {len(followon)} follow-on / resale "
           f"| {len(non)} not relevant "
           f"({len(rel) + len(other) + len(inc)} coming public in total"
           + (f", plus {len(despacs)} de-SPAC" if despacs else "") + ")",
           "",
           "An S-1 (domestic) or F-1 (foreign private issuer) is the earliest "
           "public, structured signal that a company intends to go public - "
           "typically four to eight weeks before terms are set. None of these "
           "can be added: a registrant that has not priced has no market cap, "
           "so no bucket test can run on it yet. **This is a watch list, not a "
           "recommendation list.**", ""]

    def _mark(r: Registration) -> str:
        return " (new)" if r.cik in fresh else (
            " (still open)" if carried and r.cik in carried else "")

    if rel:
        out += ["## Pipeline - companies coming public", "",
                "Registrants that do not yet file periodic reports (no 10-K or "
                "10-Q on file): these are IPO candidates. A reserved ticker in "
                "the registration statement does not make a company public.",
                "",
                "| Filed | Kind | Registrant | SIC | Sector | Proposed raise | Why |",
                "|---|---|---|---|---|---|---|"]
        for r in rel:
            wd = " [WITHDRAWN]" if r.withdrawn else ""
            out.append(f"| {r.filed} | {r.filing_kind} "
                       f"| {r.registrant[:38]}{_mark(r)}{wd} "
                       f"| {r.sic or '-'} | {r.sector or '-'} "
                       f"| {r.raise_label()} | {r.reason} |")
        out += ["", "**Proposed raise is not a valuation.** It is the number the "
                "registrant pays SEC fees on - often a round placeholder on a "
                "first filing, and even when real it is the money raised, not "
                "the company's market cap.", ""]

    if followon:
        out += [f"## Already trading - follow-on / resale ({len(followon)})", "",
                "These registrants already file 10-Ks and 10-Qs, so the filing "
                "registers shares for resale or tops up a shelf - it is not a "
                "company coming public. Listed for completeness; nothing here "
                "belongs in the pipeline.", "",
                "| Filed | Registrant | Ticker | Sector |", "|---|---|---|---|"]
        out += [f"| {r.filed} | {r.registrant[:44]}{_mark(r)} | `{r.ticker}` "
                f"| {r.sector or '-'} |" for r in followon] + [""]

    if inc:
        out += ["## Inconclusive - could not classify", "",
                "Reported rather than dropped: an unclassifiable registrant is "
                "exactly where a missed IPO would hide.", "",
                "| Filed | Registrant | SIC | Proposed raise | Why |",
                "|---|---|---|---|---|"]
        out += [f"| {r.filed} | {r.registrant[:44]} | {r.sic or '-'} "
                f"| {r.raise_label()} | {r.reason} |" for r in inc] + [""]

    # Bucket 2 is any sector at $25B+, and a company that has not priced cannot
    # be sized -- so a sector test is the wrong instrument for finding it, and
    # burying an out-of-sector registrant in a 70-name tail is how SB Energy
    # (SIC 4911, a reported $5bn+ raise) stays invisible until it lists.
    # Anything that looks like an operating company coming public gets a line.
    #
    # `already_listed`, NOT `ticker`. The ticker test is wrong here for exactly
    # the reason `check_reporting_history` documents, and it was live in this
    # line: Tailored Brands (`MENW`) and Cumberland Farms (`CMBY`) both reserved
    # pre-IPO symbols, so both were filed under "registrants that already
    # trade" -- false for both -- while Graybar Electric, a 10-K filer since the
    # 1930s whose S-1 is its annual employee stock offer, sat in the pipeline
    # because it has no ticker. That is the trap this module was built to avoid,
    # reapplied one section lower.
    # `coming` already excludes shells and follow-ons, so the out-of-sector
    # group is simply its not-relevant members. `non` is now everything the
    # partition above did not claim: shells, and anything unclassifiable that is
    # neither coming public nor a follow-on.
    rest = non
    if other:
        out += [f"## Coming public, outside the covered sectors ({len(other)})", "",
                "Not a sector match, and **not sized** -- no bucket test can run "
                "before pricing. Listed anyway because Bucket 2 is sector-"
                "agnostic at $25B+, and a name buried in the tail below is a "
                "name nobody checks when terms are set.", "",
                "| Filed | Registrant | SIC | Proposed raise |", "|---|---|---|---|"]
        out += [f"| {r.filed} | {r.registrant[:44]}{_mark(r)} | {r.sic or '-'} "
                f"| {r.raise_label()} |" for r in other] + [""]

    if confidential_md:
        # Placed ABOVE the de-SPAC line and below the filed pipeline: it is the
        # earliest signal in the report and the least certain, and both facts
        # need to be visible at once.
        out += [confidential_md.rstrip(), ""]

    if despacs:
        out += [f"## De-SPAC registrations ({len(despacs)})", "",
                "S-4 / F-4 filings whose **filer is a blank-check vehicle** -- a "
                "private company coming public by merger. **No sector is claimed "
                "here**: the filer is the SPAC, so its SIC describes the shell, "
                "not the business. Judge these by name; the post-close ticker is "
                "caught by the symbol-directory diff either way, so this is lead "
                "time, not a safety net.", "",
                "| Filed | Filer (SPAC) | Form |", "|---|---|---|"]
        out += [f"| {r.filed} | {r.registrant[:52]} | {r.form} |"
                for r in despacs] + [""]

    if rest:
        out += [f"## Not relevant ({len(rest)})", "",
                "Shells, blank-check vehicles, funds, and registrants that "
                "already trade in uncovered sectors.", "",
                ", ".join(f"{r.registrant[:30]} (SIC {r.sic or '-'})"
                          for r in rest[:60]), ""]
        if len(rest) > 60:
            out += [f"_...and {len(rest) - 60} more._", ""]

    return _ascii("\n".join(out))


# ----------------------------------------------------------------- carry / run


def carry_forward(current: list[Registration], seen: dict, *, root: Path,
                  today: date) -> list[Registration]:
    """Still-open pipeline registrations from prior runs, as Registration objects.

    Closed when the registrant priced (`424B`), withdrew (`RW`/`AW`), turned out
    to be an existing reporting company, or aged past CARRY_MAX_AGE_DAYS.

    **Every verdict that can reach the report is carried, not just `relevant`.**
    Carrying `relevant` alone looked right and defeated the point: a 14-day
    window does not describe the pipeline, and the rows it fails to describe are
    exactly the ones a sector test cannot classify. SB Energy (`not-relevant`,
    SIC 4911, the largest deal in the window) and every no-SIC `inconclusive` row
    dropped out the week they left the window and only came back if they
    amended. Bucket 2 is the case the sector test cannot find AND the case carry
    did not cover.

    `root` is unused today and kept in the signature because the caller passes
    the project root to every lane function.
    """
    have = {r.cik for r in current}
    out: list[Registration] = []
    for cik, e in seen.items():
        if cik in have:
            continue
        verdict = e.get("verdict") or ""
        if verdict not in ("relevant", "inconclusive", "not-relevant"):
            continue
        if e.get("withdrawn") or e.get("priced"):
            continue
        if e.get("already_listed"):
            # It was a follow-on / resale registration by an existing reporting
            # company, so it was never a pipeline entry to begin with.
            continue
        if verdict == "not-relevant" and (e.get("sic") or "") in _SHELL_SIC:
            continue                # a shell is not a pipeline entry either
        filed = e.get("filed") or ""
        try:
            age = (today - date.fromisoformat(filed)).days
        except ValueError:
            age = 0
        if age > CARRY_MAX_AGE_DAYS:
            continue
        out.append(Registration(
            cik=cik, registrant=e.get("registrant", ""), ticker=e.get("ticker", ""),
            accession=e.get("accession", ""), filed=filed,
            form=e.get("form", "S-1"), sic=e.get("sic", ""),
            sector=e.get("sector", ""), verdict=verdict,
            reason=e.get("reason", ""), doc=e.get("doc", ""),
            filing_kind=e.get("filing_kind", FILING_NEW),
            reporting_verified=bool(e.get("reporting_verified", True)),
            raise_usd=e.get("raise_usd"),
            raise_is_placeholder=bool(e.get("raise_is_placeholder")),
            first_seen=e.get("first_seen", "")))
    return sorted(out, key=lambda r: r.filed)


def run(root: Path, *, ua: str, days: int = 14, today: date | None = None,
        dry_run: bool = False, fetch_sizes: bool = True,
        max_size_fetches: int = 60):
    """-> (status, registrations, report, diag). status: ok | inconclusive.

    `diag` carries what the caller needs to decide whether the run was DEGRADED
    as opposed to failed: `reporting_unavailable` (SEC checks that could not be
    made, so those rows may be follow-ons wrongly shown as IPO candidates) and
    `state_saved`. Both were previously log lines only, which meant a run where
    SEC throttled most checks was indistinguishable from a clean one.
    """
    today = today or date.today()
    diag = {"reporting_unavailable": 0, "state_saved": True, "despacs": 0}
    status = "ok"
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()

    res = search_registrations(start, end, ua=ua)
    if res.status != "ok":
        logger.error("S-1/F-1 search unavailable: %s", res.error)
        return "inconclusive", [], "", diag

    seen_path = root / SEEN_PATH
    try:
        seen = load_seen(seen_path)
    except LedgerCorrupt as exc:
        # Inconclusive, not empty: a run that proceeds here would report a full
        # pipeline as "all new" and then destroy the history behind it.
        logger.error("S-1 carry ledger unusable: %s", exc)
        return "inconclusive", [], "", diag
    fresh = {r.cik for r in res.filings if r.cik not in seen}

    for r in res.filings:
        classify(r)

    # Follow-on vs coming-public, for EVERY row a reader will see -- including
    # not-relevant ones. Restricting this to relevant/inconclusive was the whole
    # defect: 33 of 70 not-relevant rows carried a ticker and none were checked,
    # so the out-of-sector section fell back to the ticker test and buried
    # Tailored Brands and Cumberland Farms. Shells are skipped because the SIC
    # already settles them and they are the bulk of the count.
    #
    # ~55 SEC submissions requests a week. Cached in `seen` only when True: a
    # company that has filed a 10-K always will have, but a pre-IPO registrant
    # can become a reporting company later, so False must be re-checked.
    unavailable = 0
    for r in res.filings:
        if r.sic in _SHELL_SIC:
            continue
        if seen.get(r.cik, {}).get("already_listed"):
            r.already_listed = True
            continue
        listed = check_reporting_history(r.cik, ua=ua)
        if listed is None:
            unavailable += 1                 # SEC down -> keep in pipeline
            r.reporting_verified = False
        r.already_listed = bool(listed)
    # NOTE: `unavailable` is finalised into `diag` AFTER the carry-forward block
    # below, which retries unverified carried rows and can add to it. Recording
    # it here would drop every retry failure -- the exact class of failure this
    # counter exists to surface.

    # Size is fetched for rows a reader will see, when there is a reason to
    # believe the number CHANGED.
    #
    # "New rows only" made `apply_size_flag`'s Bucket 2 path dead code. The real
    # sequence is a placeholder on the initial S-1 and the true number on the
    # S-1/A that sets terms -- so the one filing that carries the answer was the
    # one never fetched, and SB Energy would have read "$100M (fee-table
    # placeholder)" until it listed. A row is now re-read when its accession has
    # moved on and the number we hold is missing or a placeholder, which is
    # precisely the terms-setting amendment and nothing else.
    def _needs_size(r: Registration) -> bool:
        prev = seen.get(r.cik)
        if prev is None:
            return True                       # never seen
        # `latest_accession`, NOT `accession`. Dedup keeps the EARLIEST filing,
        # so `accession` is fixed for the life of the entry and comparing it
        # could never be true -- the refresh this function exists to trigger was
        # unreachable, and the terms-setting S-1/A stayed the one document never
        # read. Verified: an initial + amendment pair in one window resolved to
        # the original accession every time.
        if prev.get("latest_accession") != (r.latest_accession or r.accession):
            return prev.get("raise_usd") is None or prev.get("raise_is_placeholder")
        return False

    if fetch_sizes:
        # Priority order, because `max_size_fetches` truncates: the pipeline
        # table first, then the unclassifiable rows, then out-of-sector
        # registrants that still look like operating companies coming public --
        # the Bucket 2 group, which is the one a sector test cannot find.
        def _rank(r: Registration) -> int:
            if r.verdict == "relevant" and not r.already_listed:
                return 0
            if r.verdict == "inconclusive":
                return 1
            if (r.verdict == "not-relevant" and not r.already_listed
                    and r.sic not in _SHELL_SIC):
                return 2
            return 9

        want = sorted((r for r in res.filings if _needs_size(r) and _rank(r) < 9),
                      key=_rank)
        for r in want[:max_size_fetches]:
            # Read the NEWEST filing: that is where terms appear.
            text = fetch_registration_cover(
                r.cik, r.latest_accession or r.accession,
                r.latest_doc or r.doc, ua=ua)
            if text:
                r.raise_usd, r.raise_is_placeholder = extract_raise(text)
            apply_size_flag(r)
        # A not-relevant row can be promoted by size alone, so the flag pass has
        # to see the rows whose size came from a previous run's cache too.
        for r in res.filings:
            if r.raise_usd is None and r.cik in seen:
                prev = seen[r.cik]
                r.raise_usd = prev.get("raise_usd")
                r.raise_is_placeholder = bool(prev.get("raise_is_placeholder"))
                apply_size_flag(r)

    carried = carry_forward(res.filings, seen, root=root, today=today)
    priced_ciks: set[str] = set()
    if not dry_run and carried:
        # A carried entry is only worth carrying if it is still live AND still
        # pre-pricing. Checked once a week for the handful of carried names,
        # never for the full search result. Both outcomes are written back to
        # `seen`, so a closed entry costs one request in total, not one a week.
        for r in carried:
            r.withdrawn, priced = check_status(r.cik, ua=ua, since=r.filed)
            entry = seen.setdefault(r.cik, {})
            entry["withdrawn"] = r.withdrawn
            entry["priced"] = priced
            if priced:
                priced_ciks.add(r.cik)
            # A carried row whose reporting-history check never succeeded is
            # still unverified. Retry it here -- otherwise the row silently
            # becomes "verified" the moment it leaves the search window, and
            # stays that way for up to CARRY_MAX_AGE_DAYS.
            if not r.reporting_verified:
                listed = check_reporting_history(r.cik, ua=ua)
                if listed is None:
                    unavailable += 1
                else:
                    r.reporting_verified = True
                    r.already_listed = bool(listed)
                    entry["reporting_verified"] = True
                    entry["already_listed"] = r.already_listed
    carried = [r for r in carried
               if not (r.withdrawn or r.cik in priced_ciks)]

    diag["reporting_unavailable"] = unavailable
    if unavailable:
        # Silent degradation here is indistinguishable from a clean run except
        # that follow-ons appear as IPO candidates. Say the number out loud, and
        # hand it to the caller so the weekly step can go amber on it.
        logger.warning("%d reporting-history check(s) unavailable; those rows are "
                       "unverified and may be follow-ons", unavailable)

    all_regs = res.filings + carried
    # NOT gated on `fetch_sizes`. That flag means "skip the fee-table fetches",
    # and de-SPAC output has no size by construction, so tying them made
    # `--no-sizes` silently disable a discovery source it has nothing to do with.
    despacs = search_despacs(start, end, ua=ua)
    diag["despacs"] = len(despacs)

    # Confidential submissions: names the agent added from its web sweep, which
    # no lane can see. Reconciled against what this run actually found, so an
    # entry closes the week its company flips its S-1 instead of sitting in two
    # sections of the same report.
    conf_md, conf_notes = "", []
    try:
        conf_path = root / confidential_watch.WATCH_PATH
        entries = confidential_watch.load(conf_path)
        entries, conf_notes = confidential_watch.reconcile(
            entries, [r.registrant for r in all_regs], today=today)
        conf_md = confidential_watch.render(entries)
        diag["confidential_open"] = sum(1 for e in entries if e.status == "open")
        if not dry_run and entries:
            confidential_watch.save(conf_path, entries)
    except ValueError as exc:
        # A malformed watch list is a defect worth seeing, not a reason to lose
        # the report -- but it must not pass silently either.
        logger.error("confidential watch list unusable: %s", exc)
        diag["confidential_error"] = str(exc)
    for note in conf_notes:
        logger.warning("  CONFIDENTIAL: %s", note)

    report = render_report(all_regs, (start, end), fresh,
                           carried={c.cik for c in carried}, despacs=despacs,
                           confidential_md=conf_md)

    if not dry_run:
        for r in res.filings:
            prior = seen.get(r.cik, {})
            seen[r.cik] = {
                "registrant": r.registrant, "filed": r.filed,
                "accession": r.accession, "verdict": r.verdict,
                "ticker": r.ticker, "sic": r.sic, "sector": r.sector,
                "form": r.form, "filing_kind": r.filing_kind,
                "already_listed": r.already_listed,
                "reporting_verified": r.reporting_verified,
                "latest_accession": r.latest_accession or r.accession,
                "latest_filed": r.latest_filed or r.filed,
                "raise_usd": r.raise_usd,
                "raise_is_placeholder": r.raise_is_placeholder,
                "withdrawn": r.withdrawn,
                "reason": r.reason, "doc": r.doc,
                "first_seen": prior.get("first_seen", today.isoformat()),
            }
        # Report FIRST: it is the deliverable, and it must survive a state-write
        # failure rather than being lost alongside it.
        rp = root / "reports" / f"s1_watch_{end}.md"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(report, encoding="utf-8")
        try:
            save_seen(seen_path, seen)
        except LedgerNotSaved as exc:
            logger.error("S-1 carry ledger not saved: %s", exc)
            diag["state_saved"] = False

    return status, all_regs, report, diag
