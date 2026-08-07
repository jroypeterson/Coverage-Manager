"""Discover US spin-offs from SEC Form 10 registrations, before they list.

**The gap this closes.** Coverage Manager's inclusion rules name spin-offs twice
— Bucket 1 (core sector, *any* size) and Bucket 3 (>$10B, any sector) — but
every discovery source is offering-shaped: the Finnhub IPO calendar, the Gmail
IPO-summary mail, the Russell reconstitution. A spin-off distributes shares; it
has no offering, so the calendar structurally cannot see one. Measured
2026-08-06: of 18 candidates the lane had ever proposed, exactly **one** was a
spin-off, and it was proposed two months after its Form 10, at listing. Honeywell
Aerospace filed 10-12B on 2026-05-14 and had never been mentioned in any report.

**Form 10-12B** is the registration a company files to distribute a subsidiary's
shares onto a US exchange — typically one to three months before separation. It
is the earliest public, structured, free signal that a spin-off is coming.

**Routing is on the registrant's own SIC code, not on parent resolution.** The
brief assumed the parent had to be identified before the sector could be known,
and treated that as the hard problem. It is not: EDGAR's full-text search returns
the registrant's SIC on every hit, and a SpinCo is classified under the business
it actually operates. Parent resolution still runs — a reader needs to know that
"Honeywell Aerospace Inc." is Honeywell's aerospace arm — but it is *context*,
not a gate, so an unresolved parent downgrades the entry rather than dropping it.

**Market cap does not exist pre-separation and is not invented.** No shares
trade; the information statement gives a distribution ratio, not a valuation.
Bucket 1 needs no cap at all. Bucket 3's $10B test is therefore reported as
`size unknown` until a price exists, and the entry sits in the report's
"Pipeline / filings to monitor" section, which is exactly what that section is
for.

**Three states.** Every filing resolves to `relevant` / `not-relevant` /
`inconclusive`. A registrant whose SIC is missing or unmapped is inconclusive and
is reported, never silently dropped — most of the ~350 annual 10-12B filings are
trusts, shells and blank-check vehicles, and the ones this module cannot classify
are exactly where a missed spin-off would hide.
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

logger = logging.getLogger(__name__)

FTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh}/"
SEEN_PATH = Path("data/form10_seen.json")

# SIC ranges that map to JP's coverage sectors. A SpinCo is classified under the
# business it operates, so this is the registrant's own sector, not the parent's.
SIC_SECTORS = {
    "Biopharma": {"2833", "2834", "2836"},
    "MedTech": {"3826", "3827", "3841", "3842", "3843", "3844", "3845", "3851"},
    "Healthcare Services": {"8000", "8011", "8049", "8050", "8051", "8060",
                            "8062", "8071", "8082", "8090", "8093", "8093"},
    "Life Science Tools": {"8731"},
    "Tech": {"3674", "3672", "3661", "3663", "3669", "3670", "3571", "3572",
             "3576", "3577", "3578", "7370", "7371", "7372", "7373", "7374",
             "7379", "7389"},
}
CORE_SECTORS = {"Biopharma", "MedTech", "Healthcare Services",
                "Life Science Tools"}

# Parent-naming language on the first page of a Form 10 information statement.
# Each capture must END at a corporate designator. Without that anchor the
# non-greedy group happily runs into the surrounding prose: "a wholly owned
# subsidiary of Honeywell that will hold the assets" captured the whole clause,
# which then resolved -- confidently and wrongly -- to Williams Companies.
# LONGEST FIRST. Regex alternation is first-match-wins, so listing `Corp`
# before `Corporation` truncates "FedEx Corporation" to "FedEx Corp".
_SUFFIX = (r"(?:Incorporated|Corporation|Technologies|International|Holdings|"
           r"Holding|Company|Limited|Group|Inc\.|Inc|Corp\.|Corp|Co\.|Ltd\.|"
           r"Ltd|plc|PLC|LLC|L\.P\.|LP|N\.V\.|NV|S\.A\.|SA|AG|SE)")
_NAME = r"([A-Z][\w&.,'\-]*(?:\s+[A-Z][\w&.,'\-]*){0,5}\s+%s)" % _SUFFIX

# Ordered most-specific first. "separation from X" and "spin-off from X" name
# the parent unambiguously; "a wholly owned subsidiary of X" does not, because a
# Form 10 also uses it to describe the SpinCo's OWN subsidiaries -- FedEx
# Freight's information statement says "a wholly owned subsidiary of FedEx
# Freight Holding Company", i.e. itself, 200 characters before it says
# "separation from FedEx Corporation".
PARENT_PATTERNS = [
    re.compile(r"separation from\s+" + _NAME),
    re.compile(r"spin[\s-]off from\s+" + _NAME),
    re.compile(r"distribution by\s+" + _NAME),
    re.compile(r"wholly[\s-]owned subsidiary of\s+" + _NAME),
    re.compile(r"(?:stock|share)holders of\s+" + _NAME),
]

NOISE = re.compile(r"^(the|this|our|its|a|an|such|record|common|class)\b", re.I)


# A 10-12B registers securities for listing on a national exchange. That covers
# TWO different events, and calling both "spin-off" mislabels half of them:
#   - a SpinCo separating from a parent (FedEx Freight, Honeywell Aerospace)
#   - an existing OTC company UPLISTING to Nasdaq/NYSE (BSEM, 2026-08-06)
# Both are Bucket 1 new listings, so the catch is right either way. The tell is
# whether the registrant already trades: SEC `exchanges` reads ["OTC"] and there
# is no parent to resolve.
LISTING_SPINOFF = "spin-off"
LISTING_UPLIST = "uplisting"
LISTING_UNKNOWN = "new registration"


@dataclass
class Filing:
    cik: str
    registrant: str
    ticker: str
    accession: str
    filed: str
    form: str
    sic: str
    sector: str = ""
    parent: str = ""
    parent_cik: str = ""
    verdict: str = "inconclusive"
    reason: str = ""
    doc: str = ""
    doc_rank: int = 9
    parent_ticker: str = ""
    parent_cap: float | None = None
    listing_kind: str = LISTING_UNKNOWN


@dataclass
class WatchResult:
    status: str                      # ok | inconclusive
    filings: list[Filing] = field(default_factory=list)
    error: str = ""
    window: tuple[str, str] = ("", "")


def _get(url: str, ua: str, timeout: int = 30, accept: str = ""):
    headers = {"User-Agent": ua}
    if accept:
        headers["Accept"] = accept
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=timeout)


def search_form10(start: str, end: str, *, ua: str, opener=None,
                  form: str = "10-12B") -> WatchResult:
    """EDGAR full-text search for Form 10 registrations in a date window.

    FTS returns one hit per *document*, so a single filing with twelve exhibits
    yields twelve hits. Collapsed to one entry per registrant CIK, keeping the
    earliest filing — an `/A` amendment of something already seen is not news.
    """
    opener = opener or (lambda u: _get(u, ua, accept="application/json"))

    # FTS returns TEN hits per page. Reading only the first page does not sample
    # filings -- it samples DOCUMENTS, and a single Form 10 carries a dozen
    # exhibits, so one page can be one company's boilerplate. Measured
    # 2026-08-06: a 120-day window reported 157 total and returned 10, which
    # silently produced "10 distinct registrants" and picked EX-4.3 as
    # Honeywell's information statement because the EX-99.1 was on page 2.
    hits = []
    offset, total = 0, None
    while True:
        params = urllib.parse.urlencode(
            {"q": '""', "forms": form, "startdt": start, "enddt": end,
             "from": offset})
        try:
            data = json.loads(opener(f"{FTS_URL}?{params}").read())
        except Exception as exc:                   # noqa: BLE001
            return WatchResult("inconclusive",
                               error=f"{type(exc).__name__}: {exc} (offset {offset})",
                               window=(start, end))
        page = data.get("hits", {}).get("hits", [])
        if total is None:
            total = (data.get("hits", {}).get("total") or {}).get("value", 0)
        hits.extend(page)
        offset += len(page)
        if not page or offset >= min(total or 0, 9990):
            break

    by_cik: dict[str, Filing] = {}
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
        filed = src.get("file_date") or ""
        f = Filing(cik=cik, registrant=name, ticker=tick,
                   accession=src.get("adsh") or "", filed=filed,
                   form=src.get("form") or form,
                   sic=(src.get("sics") or [""])[0] or "",
                   doc=(hit.get("_id") or "").split(":")[-1])
        # Keep the EARLIEST filing per registrant, but prefer the EX-99.1
        # information statement as its document -- that is the exhibit naming
        # the parent. Taking whichever hit arrived last picked EX-99.2 for ADI
        # Global, a 2.4KB stub with no parent language in it at all.
        ftype = (src.get("file_type") or "").upper()
        f.doc_rank = 0 if ftype == "EX-99.1" else (1 if ftype.startswith("10-12B") else 2)
        prev = by_cik.get(cik)
        if prev is None or (f.filed, f.doc_rank) < (prev.filed, prev.doc_rank):
            by_cik[cik] = f
    return WatchResult("ok", filings=sorted(by_cik.values(),
                                            key=lambda x: (x.filed, x.registrant)),
                       window=(start, end))


def sector_for_sic(sic: str) -> str:
    for sector, codes in SIC_SECTORS.items():
        if sic in codes:
            return sector
    return ""


def classify(f: Filing) -> Filing:
    """Assign sector and a three-state verdict from the registrant's own SIC."""
    f.sector = sector_for_sic(f.sic)
    if f.sector in CORE_SECTORS:
        f.verdict = "relevant"
        f.reason = (f"Bucket 1 — core sector ({f.sector}, SIC {f.sic}); "
                    f"relevant at any size")
    elif f.sector:
        f.verdict = "relevant"
        f.reason = (f"Adjacent sector ({f.sector}, SIC {f.sic}); Bucket 3 if it "
                    f"separates above $10B — size unknown until it trades")
    elif not f.sic:
        f.verdict = "inconclusive"
        f.reason = "no SIC on the filing — cannot classify, review by hand"
    else:
        f.verdict = "not-relevant"
        f.reason = f"SIC {f.sic} is outside the covered sectors"
    return f


# ------------------------------------------------------- parent + size proxy


def _strip_html(html: str) -> str:
    txt = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&")
              .replace("&#8217;", "'").replace("&rsquo;", "'")
              .replace("&#8212;", "-").replace("&mdash;", "-"))
    return re.sub(r"\s+", " ", txt)


def extract_parent(text: str, registrant: str = "") -> str:
    """Pull the parent company's name out of an information statement.

    Candidates are gathered from every pattern in priority order and the first
    that is NOT the registrant itself wins. Returning the first regex hit
    outright picked "FedEx Freight Holding Company" -- the filer -- as its own
    parent, which then failed to resolve and silently cost a Bucket 3 spin-off
    out of a ~$60B parent.
    """
    import difflib

    def _n(t):
        t = re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())
        t = re.sub(r"(inc|corp|corporation|co|company|holdings?|ltd|the)", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    head = text[:60000]
    for pat in PARENT_PATTERNS:
        for m in pat.finditer(head):
            cand = re.sub(r"\s+", " ", m.group(1)).strip(" ,.;:")
            if len(cand) < 4:
                continue
            if registrant:
                # Whole-string closeness, NOT `_name_similarity`: that metric is
                # token-cover based, so "FedEx Corp" scores as the registrant
                # "FedEx Freight Holding Company" on the shared token and the
                # filter ate the correct parent.
                if difflib.SequenceMatcher(None, _n(cand), _n(registrant)).ratio() >= 0.85:
                    continue           # the filer describing itself
            return cand
    return ""


def resolve_company(name: str, sec_map: dict[str, dict], *,
                    threshold: float = 0.80,
                    margin: float = 0.05) -> tuple[str, str, str]:
    """Name -> (cik, ticker, matched_title), or ('','','') if unconvincing.

    The executable pin on the extraction. Two guards, both earned live on
    2026-08-06 at the repo's usual 0.55 threshold:

    - **0.80, not 0.55.** "Ceridian HCM Holding" matched *Meridian Corp* at 0.55.
      That threshold is calibrated for comparing a stored company name against a
      vendor's spelling of the SAME company; here the input is a regex capture
      from prose and the search space is 10,000 registrants, so the prior that
      any match is correct is far weaker.
    - **A margin over the runner-up.** Two registrants scoring within 0.05 of
      each other means the name does not identify one company, and returning the
      arbitrary winner would be a coin toss presented as a fact.

    A wrong parent is worse than no parent: it would put a real company's name
    on someone else's spin-off in a report JP acts on.
    """
    import difflib
    from universe.crsp_snapshot import _name_similarity

    def _norm(t: str) -> str:
        t = re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())
        t = re.sub(r"(inc|corp|corporation|co|company|holdings?|ltd|limited|"
                   r"plc|llc|lp|nv|sa|ag|se|the)", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    if not name:
        return "", "", ""
    target = _norm(name)
    best_by_cik: dict[str, tuple[float, float, str, str]] = {}
    for entry in sec_map.values():
        title = entry.get("title") or ""
        score = _name_similarity(name, title)
        if score is None or score < threshold:
            continue
        # `_name_similarity` is token-cover based, so a SUBSET scores 1.00 --
        # "Honeywell International Inc" ties with "Inter & Co, Inc.". Tie-break
        # on whole-string closeness, which containment cannot fake.
        ratio = difflib.SequenceMatcher(None, target, _norm(title)).ratio()
        cik = str(entry.get("cik_str") or "")
        cur = best_by_cik.get(cik)
        if cur is None or (score, ratio) > (cur[0], cur[1]):
            best_by_cik[cik] = (score, ratio, entry.get("ticker") or "", title)

    if not best_by_cik:
        return "", "", ""
    ranked = sorted(((v[1], v[0], k, v[2], v[3]) for k, v in best_by_cik.items()),
                    reverse=True)
    best = ranked[0]
    # Ambiguity is only ambiguity across DIFFERENT companies; SEC lists several
    # rows per CIK for multiple share classes and those are not a conflict.
    if len(ranked) > 1 and (best[0] - ranked[1][0]) < margin:
        logger.warning("parent %r is ambiguous: %r vs %r", name, best[4], ranked[1][4])
        return "", "", ""
    if best[0] < 0.60:
        return "", "", ""
    return best[2], best[3], best[4]


def fetch_information_statement(cik: str, accession: str, filename: str, *,
                                ua: str, opener=None) -> str:
    opener = opener or (lambda u: _get(u, ua))
    url = ARCHIVE.format(cik=cik, adsh=accession.replace("-", "")) + filename
    try:
        raw = opener(url).read()
    except Exception:                              # noqa: BLE001
        return ""
    html = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    return _strip_html(html)


def parent_market_cap(ticker: str, api_key: str, *, opener=None) -> float | None:
    """Parent market cap, the only available size proxy before separation.

    Bucket 3 is sector-agnostic and size-gated at $10B, so a spin-off out of a
    core sector still qualifies on size alone — which is precisely the case the
    SIC router cannot see. Honeywell Aerospace is SIC 3724 (aircraft engines),
    outside every covered sector, and is a mandatory add anyway because its
    parent is a ~$130B company separating a major segment.

    Returns None on any failure. None means "unknown", and the caller must
    render it as unknown rather than as small.
    """
    if not ticker or not api_key:
        return None
    url = ("https://financialmodelingprep.com/stable/profile?symbol=%s&apikey=%s"
           % (urllib.parse.quote(ticker), api_key))
    opener = opener or (lambda u: urllib.request.urlopen(u, timeout=25))
    try:
        data = json.loads(opener(url).read())
    except Exception:                              # noqa: BLE001
        return None
    if isinstance(data, list) and data:
        cap = data[0].get("marketCap")
        return float(cap) if cap else None
    return None


# Parent size above which a separated segment is presumed capable of clearing
# Bucket 3's $10B bar. Deliberately not $10B: a $12B parent does not spin off a
# $10B segment. $40B is the smallest parent for which "a major segment could
# plausibly be worth $10B+" is a defensible default, and the entry says
# "presumed" rather than asserting a valuation nobody can know yet.
BUCKET3_PARENT_FLOOR = 40e9


def apply_size_proxy(f: Filing) -> Filing:
    """Upgrade a sector-irrelevant filing to Bucket 3 on the parent's size."""
    if f.verdict == "relevant" or f.parent_cap is None:
        return f
    if f.parent_cap >= BUCKET3_PARENT_FLOOR:
        f.verdict = "relevant"
        f.reason = (f"Bucket 3 candidate on size — parent {f.parent} "
                    f"(${f.parent_cap/1e9:,.0f}B) is separating a segment; the "
                    f"SpinCo's own cap is unknown until it trades, so this is a "
                    f"pipeline entry, not an add")
    return f


# ------------------------------------------------------------------- runner


def load_seen(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("form10 seen-ledger unreadable — treating as empty")
    return {}


def save_seen(path: Path, seen: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.warning("form10 seen-ledger not written (%s) — the next run may "
                       "re-report these filings", e)



_ASCII_MAP = {"—": "-", "–": "-", "’": "'", "‘": "'",
              "“": '"', "”": '"', "·": "|", "…": "...",
              " ": " ", "≥": ">=", "≤": "<="}


def _ascii(text: str) -> str:
    """Sanitize at the EXIT, not per string.

    This report is printed by a scheduled task to a cp1252 console. Chasing
    individual em-dashes through a dozen f-strings is how one gets missed, and
    the miss raises UnicodeEncodeError at the exact moment the job is trying to
    report what it found. `export_acceptance` sanitizes the same way and for the
    same reason. Company names arrive from EDGAR and are global.
    """
    for bad, good in _ASCII_MAP.items():
        text = text.replace(bad, good)
    return text.encode("ascii", "replace").decode("ascii")


def render_report(filings: list[Filing], window: tuple[str, str],
                  fresh: set[str], carried: set[str] | None = None) -> str:
    rel = [f for f in filings if f.verdict == "relevant"]
    inc = [f for f in filings if f.verdict == "inconclusive"]
    non = [f for f in filings if f.verdict == "not-relevant"]
    out = [f"# Form 10 spin-off watch - {window[0]} to {window[1]}", "",
           f"**{len(filings)} distinct registrants** "
           f"({len(fresh)} new since the last run) - "
           f"{len(rel)} relevant | {len(inc)} inconclusive | {len(non)} not relevant",
           "",
           "A Form 10-12B registers securities for listing on a US national "
           "exchange. That covers two events, both of them Bucket 1 new "
           "listings: a **spin-off** separating from a parent (named in the "
           "information statement), and an **uplisting** of a company that "
           "already trades OTC. Neither has an offering, so the IPO calendar "
           "is structurally blind to both.", ""]
    if rel:
        out += ["## For the pipeline", "",
                "| Filed | Kind | Registrant | Ticker | SIC | Parent | Why |",
                "|---|---|---|---|---|---|---|"]
        for f in rel:
            par = f.parent or "_unresolved_"
            if f.parent_cap:
                par += f" (${f.parent_cap/1e9:,.0f}B)"
            # ASCII only: this string reaches a cp1252 console on the
            # scheduled run, and an emoji there raises UnicodeEncodeError at the
            # exact moment the job is trying to report what it found.
            mark = " (new)" if f.cik in fresh else (
                " (still open)" if carried and f.cik in carried else "")
            out.append(f"| {f.filed} | {f.listing_kind} "
                       f"| {f.registrant[:38]}{mark} "
                       f"| `{f.ticker or '-'}` | {f.sic} | {par} | {f.reason} |")
        out += ["", "**Sizes are unknown by construction.** No shares trade before "
                "separation, so these are pipeline entries to monitor, not adds.", ""]
    if inc:
        out += ["## Inconclusive - could not classify", "",
                "Reported rather than dropped: an unclassifiable registrant is "
                "exactly where a missed spin-off would hide.", "",
                "| Filed | Registrant | SIC | Why |", "|---|---|---|---|"]
        out += [f"| {f.filed} | {f.registrant[:44]} | {f.sic or '-'} | {f.reason} |"
                for f in inc] + [""]
    if non:
        out += [f"## Not relevant ({len(non)})", "",
                ", ".join(f"{f.registrant[:30]} (SIC {f.sic})" for f in non), ""]
    return _ascii("\n".join(out))


def run(root: Path, *, ua: str, api_key: str = "", days: int = 10,
        today: date | None = None, dry_run: bool = False,
        resolve_parents: bool = True) -> tuple[str, list[Filing], str]:
    """-> (status, filings, report). status: ok | inconclusive."""
    today = today or date.today()
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()

    res = search_form10(start, end, ua=ua)
    if res.status != "ok":
        logger.error("Form 10 search unavailable: %s", res.error)
        return "inconclusive", [], ""

    seen_path = root / SEEN_PATH
    seen = load_seen(seen_path)
    fresh = {f.cik for f in res.filings if f.cik not in seen}

    sec_map = {}
    if resolve_parents:
        try:
            sec_map = json.loads(_get("https://www.sec.gov/files/company_tickers.json",
                                      ua, timeout=40).read())
        except Exception as exc:                   # noqa: BLE001
            logger.warning("SEC ticker map unavailable (%s) — parents will be "
                           "reported as unresolved, never guessed", exc)

    for f in res.filings:
        classify(f)
        if resolve_parents and sec_map:
            text = fetch_information_statement(f.cik, f.accession, f.doc, ua=ua)
            raw = extract_parent(text, f.registrant)
            cik, tick, title = resolve_company(raw, sec_map)
            if cik:
                f.parent, f.parent_cik, f.parent_ticker = title, cik, tick
                f.parent_cap = parent_market_cap(tick, api_key)
                apply_size_proxy(f)
        if resolve_parents:
            classify_listing_kind(f, ua=ua)

    report = render_report(res.filings, (start, end), fresh)
    # CARRY FORWARD. A 14-day search window finds new FILINGS; it does not
    # describe the PIPELINE. FedEx Freight filed 2026-01-16 and Honeywell
    # Aerospace 2026-03-03 -- both still unlisted, both squarely "filings to
    # monitor" -- and a 14-day window reports neither, forever. An item stays
    # open until its ticker turns up in the universe (it listed and was added)
    # or it ages out.
    carried = carry_forward(res.filings, seen, root=root, today=today)
    all_relevant = res.filings + carried

    report = render_report(all_relevant, (start, end), fresh, carried={c.cik for c in carried})
    if not dry_run:
        for f in res.filings:
            prior = seen.get(f.cik, {})
            seen[f.cik] = {
                "registrant": f.registrant, "filed": f.filed,
                "accession": f.accession, "verdict": f.verdict,
                "ticker": f.ticker, "sic": f.sic, "sector": f.sector,
                "parent": f.parent, "listing_kind": f.listing_kind,
                "reason": f.reason, "doc": f.doc,
                "first_seen": prior.get("first_seen", today.isoformat()),
            }
        save_seen(seen_path, seen)
        rp = root / "reports" / f"form10_watch_{end}.md"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(report, encoding="utf-8")
    return "ok", all_relevant, report


CARRY_MAX_AGE_DAYS = 540        # ~18 months; a Form 10 older than that is dead


def carry_forward(current: list[Filing], seen: dict, *, root: Path,
                  today: date) -> list[Filing]:
    """Still-open relevant filings from prior runs, as Filing objects.

    Closed when the registrant's ticker is in the universe -- it listed and was
    added, so it is coverage now, not pipeline. Also closed past
    CARRY_MAX_AGE_DAYS so a withdrawn registration cannot haunt the report
    forever (BSEM's own 2024 attempt was withdrawn by an `RW` in 2025).
    """
    import csv as _csv

    have = {f.cik for f in current}
    universe: set[str] = set()
    uni = root / "data" / "coverage_universe_tickers.csv"
    if uni.exists():
        try:
            with open(uni, newline="", encoding="utf-8-sig") as fh:
                universe = {(r.get("Ticker") or "").strip().upper()
                            for r in _csv.DictReader(fh)}
        except OSError:
            universe = set()      # unreadable universe: carry, do not drop

    out: list[Filing] = []
    for cik, e in seen.items():
        if cik in have or e.get("verdict") != "relevant":
            continue
        tick = (e.get("ticker") or "").strip().upper()
        if tick and tick in universe:
            continue              # it listed and was added
        filed = e.get("filed") or ""
        try:
            age = (today - date.fromisoformat(filed)).days
        except ValueError:
            age = 0
        if age > CARRY_MAX_AGE_DAYS:
            continue
        out.append(Filing(
            cik=cik, registrant=e.get("registrant", ""), ticker=e.get("ticker", ""),
            accession=e.get("accession", ""), filed=filed, form="10-12B",
            sic=e.get("sic", ""), sector=e.get("sector", ""),
            parent=e.get("parent", ""), verdict="relevant",
            reason=e.get("reason", ""), doc=e.get("doc", ""),
            listing_kind=e.get("listing_kind", LISTING_UNKNOWN)))
    return sorted(out, key=lambda f: f.filed)
    return "ok", res.filings, report


def classify_listing_kind(f: Filing, *, ua: str, opener=None) -> Filing:
    """Spin-off vs uplisting. Call AFTER parent resolution -- it uses the result.

    The obvious discriminator does not work: SEC's `exchanges` is non-empty for
    BOTH, because a SpinCo that has been certified for listing already shows its
    destination venue. Honeywell Aerospace reads ["Nasdaq"] with ticker HONA
    while being a textbook spin-off.

    What actually separates them:
      - a resolved PARENT in the information statement -> spin-off. Nothing
        else produces one, and it is the definition of the event.
      - `OTC` among SEC's exchanges -> uplisting. The registrant already trades
        over the counter and is moving venue (BSEM: OTC since 2016, Form Ds,
        10-12B attempts since 2024, CERT filed 2026-08-06).

    Unknown otherwise, and unknown is reported as unknown. Both kinds are Bucket
    1 new listings, so this is descriptive -- it changes what the report SAYS,
    never what it routes.
    """
    import json as _json
    if f.parent:
        f.listing_kind = LISTING_SPINOFF
        return f
    opener = opener or (lambda u: _get(u, ua, timeout=25))
    try:
        data = _json.loads(opener(
            f"https://data.sec.gov/submissions/CIK{f.cik.zfill(10)}.json").read())
    except Exception:                              # noqa: BLE001
        f.listing_kind = LISTING_UNKNOWN
        return f
    exchanges = [str(e).upper() for e in (data.get("exchanges") or []) if e]
    forms = set((data.get("filings", {}).get("recent", {}) or {}).get("form", []))
    if any("OTC" in e for e in exchanges):
        f.listing_kind = LISTING_UPLIST
        f.reason += (" | UPLISTING, not a spin-off: SEC already lists this "
                     "registrant OTC"
                     + (" and a CERT (exchange listing certification) is filed"
                        if "CERT" in forms else ""))
    else:
        f.listing_kind = LISTING_UNKNOWN
        f.reason += (" | listing kind unresolved: no parent named in the "
                     "information statement and SEC shows no OTC quotation")
    return f
