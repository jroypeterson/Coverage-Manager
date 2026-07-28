"""Recover ISIN + LEI for foreign-listed universe rows from fund holdings.

WHY THIS EXISTS
---------------
200 of the universe's 349 foreign-HQ rows carry **no ISIN at all**, and that gap
is the binding constraint on every identifier-keyed cross-check: a reconciliation
against any authoritative source can only reach the 43% of foreign rows that have
something to join on. `enrich.py` already tries yfinance `Ticker.isin` and FMP,
so these 200 are the residual those two cannot resolve — mostly local lines on
Tokyo, Shenzhen, Korea, India, and the Swiss/Nordic exchanges (Astellas, Shionogi,
Celltrion, Innovent, CSPC …). A different kind of source is needed, not a retry.

THE SOURCE
----------
Two public files describing the *same* portfolio of a broad international ETF:

* **iShares holdings CSV** — local exchange ticker, company name, `Location`
  (country), `Exchange`, `Market Currency`. Daily. No ISIN.
* **SEC N-PORT (NPORT-P)** — the same fund's holdings as a legally filed report:
  company name, **ISIN**, **LEI**, `invCountry`. Verified 100% coverage on all
  three fields across 4,198 IXUS holdings. Quarterly. No ticker.

Neither alone is enough; joined on company name within one fund, they yield
`(local ticker, country) -> {ISIN, LEI, incorporation country, exchange,
currency}` from an SEC filing.

WHY THE JOIN IS KEYED ON (TICKER, COUNTRY) AND NOT TICKER
---------------------------------------------------------
A bare local ticker is **not** an identity — that is what the exchange suffix is
for. Keyed on ticker alone, `1801.HK` (Innovent Biologics, Hong Kong) resolves to
`JP3443600006`, a *Japanese* ISIN, because `1801` is also a Tokyo issuer's code.
Measured during development, not hypothesised. Keyed on `(ticker, location)`
there are zero collisions across 2,638 keys and Innovent resolves correctly to
`KYG4818G1010`.

Three further guards, because a wrong identifier is far worse than a missing one
(a blank ISIN is visibly missing; a wrong one looks like data and silently
mis-joins every downstream consumer):

1. The universe ticker must carry an exchange suffix that maps to a known country.
2. The fund's `Location` must be one of that suffix's expected countries.
3. The universe `Company Name` and the fund's holding name must actually agree
   (token-based, prefix-aware — the same comparison `crsp_snapshot` uses, for the
   same reason: providers abbreviate and reorder names).

INCORPORATION IS NOT DOMICILE
-----------------------------
The recovered ISIN's country prefix frequently disagrees with the row's
`Country (HQ)` — and is still correct. Innovent is China-HQ'd and Cayman-
incorporated (`KY`); WuXi Biologics likewise. `enrich.validate_isin_for_row`
would reject those, and rightly so for its own inputs: it guards against yfinance
returning a *wrong* ISIN for a rebranded ticker. These come from an SEC filing
with the issuer's own LEI attached, so the guard is not applied — instead every
prefix-vs-HQ divergence is reported explicitly, so an unexpected one is seen
rather than silently written or silently dropped.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config
from universe.crsp_snapshot import _name_similarity

log = logging.getLogger(__name__)

CACHE_DIR = config.CACHE_DIR / "foreign_ids"
CACHE_TTL_DAYS = 7

# The iShares URL slug is ignored — only the product id resolves the fund — so a
# fund is fully identified here by (product id, SEC CIK, SEC series id).
FUNDS: list[tuple[str, str, str, str]] = [
    # label, iShares product id, SEC CIK, SEC series id
    ("IXUS (Core MSCI Total International)", "244048", "1100663", "S000038931"),
    ("IEMG (Core MSCI Emerging Markets)", "244050", "930667", "S000038923"),
]

HOLDINGS_URL = "https://www.ishares.com/us/products/{pid}/x/latest-holdings.csv"
EDGAR_ATOM = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series}"
              "&type=NPORT-P&dateb=&owner=include&count=3&output=atom")

# iShares 403s a non-browser agent (the same CDN behaviour the comments tracker
# hit). SEC, conversely, requires a contact string and rate-limits hard.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SEC_UA = os.environ.get("EDGAR_IDENTITY", "ClaudeFin research contact@example.com")
SEC_MIN_INTERVAL = 0.25

# Yahoo-style exchange suffix -> the countries the fund files may label it with.
# Hong Kong listings of mainland issuers are labelled either way, so both are
# accepted; anything not listed here is simply not resolvable by this module.
SUFFIX_COUNTRIES: dict[str, set[str]] = {
    "T": {"Japan"}, "HK": {"China", "Hong Kong"}, "SZ": {"China"}, "SS": {"China"},
    "KS": {"Korea (South)"}, "KQ": {"Korea (South)"},
    "NS": {"India"}, "BO": {"India"},
    "SW": {"Switzerland"}, "PA": {"France"}, "DE": {"Germany"}, "F": {"Germany"},
    "L": {"United Kingdom"}, "AX": {"Australia"}, "CO": {"Denmark"},
    "MC": {"Spain"}, "ST": {"Sweden"}, "TO": {"Canada"}, "V": {"Canada"},
    "MI": {"Italy"}, "AS": {"Netherlands"}, "BR": {"Belgium"}, "OL": {"Norway"},
    "HE": {"Finland"}, "VI": {"Austria"}, "LS": {"Portugal"},
    "TW": {"Taiwan"}, "TWO": {"Taiwan"}, "SI": {"Singapore"},
    "NZ": {"New Zealand"}, "TA": {"Israel"}, "IR": {"Ireland"},
}

NAME_MATCH_THRESHOLD = 0.60

US_HQ_VALUES = {"", "US", "USA", "U.S.", "UNITED STATES", "UNITED STATES OF AMERICA"}

_NAME_NOISE = {
    "THE", "INC", "CORP", "CORPORATION", "CO", "LTD", "LIMITED", "PLC", "SA",
    "NV", "AG", "AB", "ASA", "SE", "HOLDING", "HOLDINGS", "GROUP", "CLASS",
    "A", "B", "C", "LP", "KGAA", "COMPANY", "AS", "OYJ", "SPA",
}


@dataclass
class Proposal:
    ticker: str
    company: str
    isin: str
    lei: str
    fund_country: str
    inv_country: str
    exchange: str
    currency: str
    similarity: float
    source: str
    writes_isin: bool = False
    writes_lei: bool = False


@dataclass
class BackfillResult:
    status: str = "ok"
    funds_ok: list[str] = field(default_factory=list)
    funds_failed: list[str] = field(default_factory=list)
    map_size: int = 0
    candidates: int = 0
    proposals: list[Proposal] = field(default_factory=list)
    rejected_name: list[tuple] = field(default_factory=list)
    rejected_country: list[tuple] = field(default_factory=list)
    no_suffix: int = 0
    isin_written: int = 0
    lei_written: int = 0
    prefix_divergences: list[Proposal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── fetch + cache ────────────────────────────────────────────────────────────


def _cache_path(name: str) -> Path:
    return CACHE_DIR / name


def _fresh(p: Path) -> bool:
    if not p.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    return age < timedelta(days=CACHE_TTL_DAYS)


def _get(url: str, *, ua: str, attempts: int = 3) -> bytes:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise
            last = exc
            if i < attempts - 1:
                time.sleep(3 * (2 ** i))
    raise RuntimeError(f"{url}: {attempts} attempts failed; last error: {last}")


def _cached_get(url: str, cache_name: str, *, ua: str, use_cache: bool = True) -> bytes:
    p = _cache_path(cache_name)
    if use_cache and _fresh(p):
        return p.read_bytes()
    data = _get(url, ua=ua)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        Path(tmp).replace(p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return data


# ── parse ────────────────────────────────────────────────────────────────────


def parse_holdings(raw: bytes) -> list[dict]:
    """Rows from an iShares holdings CSV (preamble skipped, equities only).

    The file opens with ~8 lines of fund metadata before the real header, and a
    dead or redirected URL returns an HTML page with HTTP 200 — so a missing
    header row is treated as a failure, never as an empty portfolio.
    """
    text = raw.decode("utf-8-sig", errors="replace").splitlines()
    hdr = next((i for i, line in enumerate(text) if line.startswith("Ticker,Name,")), None)
    if hdr is None:
        raise ValueError("no 'Ticker,Name,' header found — HTML error page or schema change?")
    rows = list(csv.DictReader(io.StringIO("\n".join(text[hdr:]))))
    return [r for r in rows if (r.get("Asset Class") or "").strip() == "Equity"]


def parse_nport(raw: bytes) -> dict[str, tuple[str, str, str]]:
    """{normalised name: (isin, lei, invCountry)} from an NPORT-P primary_doc."""
    x = raw.decode("utf-8", errors="replace")
    out: dict[str, tuple[str, str, str]] = {}
    for rec in re.findall(r"<invstOrSec>.*?</invstOrSec>", x, re.S):
        name = re.search(r"<name>([^<]*)</name>", rec)
        isin = re.search(r'<isin value="([^"]*)"', rec)
        lei = re.search(r"<lei>([^<]*)</lei>", rec)
        ctry = re.search(r"<invCountry>([^<]*)</invCountry>", rec)
        if not (name and isin and isin.group(1).strip()):
            continue
        key = _norm_key(name.group(1))
        if not key:
            continue
        lei_v = (lei.group(1).strip() if lei else "")
        out[key] = (isin.group(1).strip(),
                    "" if lei_v in ("", "N/A") else lei_v,
                    ctry.group(1).strip() if ctry else "")
    if not out:
        raise ValueError("no <invstOrSec> records with an ISIN — wrong document or schema change?")
    return out


def _norm_key(s: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper())
    return " ".join(w for w in s.split() if w and w not in _NAME_NOISE)


def latest_nport_url(cik: str, series: str, *, use_cache: bool = True) -> str:
    atom = _cached_get(EDGAR_ATOM.format(series=series), f"atom_{series}.xml",
                       ua=SEC_UA, use_cache=use_cache)
    time.sleep(SEC_MIN_INTERVAL)
    hrefs = re.findall(r"<filing-href>([^<]*)</filing-href>", atom.decode("utf-8", "replace"))
    if not hrefs:
        raise ValueError(f"no NPORT-P filings listed for series {series}")
    acc_dir = hrefs[0].rsplit("/", 1)[0]
    return f"{acc_dir}/primary_doc.xml"


# ── build the map ────────────────────────────────────────────────────────────


def build_map(funds=FUNDS, *, use_cache: bool = True,
              result: BackfillResult | None = None) -> dict[tuple[str, str], dict]:
    """`{(local ticker, country): {isin, lei, ...}}` unioned across `funds`.

    A fund that fails to download is recorded and skipped — one dead URL must not
    discard the other funds' recoveries — but a run where *every* fund failed is
    a failed run, not an empty answer.
    """
    out: dict[tuple[str, str], dict] = {}
    for label, pid, cik, series in funds:
        try:
            holdings = parse_holdings(_cached_get(
                HOLDINGS_URL.format(pid=pid), f"holdings_{pid}.csv",
                ua=BROWSER_UA, use_cache=use_cache))
            nport = parse_nport(_cached_get(
                latest_nport_url(cik, series, use_cache=use_cache),
                f"nport_{series}.xml", ua=SEC_UA, use_cache=use_cache))
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
            log.warning("fund %s unavailable: %s", label, exc)
            if result is not None:
                result.funds_failed.append(f"{label}: {exc}")
            continue

        added = 0
        for h in holdings:
            key = _norm_key(h.get("Name", ""))
            hit = nport.get(key)
            if not hit:
                continue
            tkr = (h.get("Ticker") or "").strip().upper()
            loc = (h.get("Location") or "").strip()
            if not tkr or not loc:
                continue
            out.setdefault((tkr, loc), {
                "isin": hit[0], "lei": hit[1], "inv_country": hit[2],
                "name": (h.get("Name") or "").strip(),
                "exchange": (h.get("Exchange") or "").strip(),
                "currency": (h.get("Market Currency") or "").strip(),
                "source": label,
            })
            added += 1
        log.info("fund %s: %d holdings, %d joined", label, len(holdings), added)
        if result is not None:
            result.funds_ok.append(f"{label} ({added} joined)")
    return out


# ── resolve against the universe ─────────────────────────────────────────────


def resolve(rows: list[dict], mapping: dict[tuple[str, str], dict],
            result: BackfillResult) -> list[Proposal]:
    """Propose ISIN/LEI fills for rows that can be matched with all guards passed."""
    proposals: list[Proposal] = []
    for row in rows:
        ticker = (row.get("Ticker") or "").strip()
        have_isin = bool((row.get("ISIN") or "").strip())
        have_lei = bool((row.get("LEI") or "").strip())
        if not ticker or (have_isin and have_lei):
            continue
        # Scope is foreign lines. A US row missing an LEI is a real gap but not
        # this module's — its sources are ex-US funds, and counting those rows
        # here reported 599 "unresolvable" when the foreign figure is ~60.
        if (row.get("Country (HQ)") or "").strip().upper() in US_HQ_VALUES:
            continue

        if "." not in ticker:
            result.no_suffix += 1
            continue
        local, suffix = ticker.rsplit(".", 1)
        expected = SUFFIX_COUNTRIES.get(suffix.upper())
        if not expected:
            continue

        result.candidates += 1
        local = local.upper()
        found = [(loc, v) for (t, loc), v in mapping.items() if t == local]
        if not found:
            continue
        ok_country = [(loc, v) for loc, v in found if loc in expected]
        if not ok_country:
            result.rejected_country.append(
                (ticker, (row.get("Company Name") or "")[:36], found[0][0], sorted(expected)))
            continue

        loc, hit = ok_country[0]
        cm_name = (row.get("Company Name") or "").strip()
        sim = _name_similarity(cm_name, hit["name"]) if cm_name else None
        if sim is None or sim < NAME_MATCH_THRESHOLD:
            result.rejected_name.append(
                (ticker, cm_name[:36], hit["name"][:36], round(sim, 2) if sim is not None else None))
            continue

        p = Proposal(
            ticker=ticker, company=cm_name, isin=hit["isin"], lei=hit["lei"],
            fund_country=loc, inv_country=hit["inv_country"], exchange=hit["exchange"],
            currency=hit["currency"], similarity=round(sim, 3), source=hit["source"],
            writes_isin=not have_isin and bool(hit["isin"]),
            writes_lei=not have_lei and bool(hit["lei"]),
        )
        if p.writes_isin or p.writes_lei:
            proposals.append(p)
    return proposals


def _hq_prefix_diverges(row: dict, isin: str) -> bool:
    """True when the ISIN's country prefix disagrees with the row's HQ country.

    Not a rejection — Cayman/Bermuda/Irish incorporation of a China- or US-
    operating issuer is ordinary and the ISIN is still right. Reported so an
    unexpected divergence is seen rather than silently written.
    """
    from ticker_utils import COUNTRY_TO_ISIN_PREFIX

    hq = (row.get("Country (HQ)") or "").strip()
    want = COUNTRY_TO_ISIN_PREFIX.get(hq)
    return bool(want and isin[:2].upper() != want)


# ── main ─────────────────────────────────────────────────────────────────────


def main(*, dry_run: bool = False, use_cache: bool = True,
         limit: int | None = None) -> BackfillResult:
    from ticker_utils import backup_csv, read_universe_csv, write_universe_csv

    result = BackfillResult()
    mapping = build_map(use_cache=use_cache, result=result)
    result.map_size = len(mapping)
    if not mapping:
        result.status = "failed"
        result.errors.append(
            "no fund data available — every source failed; "
            "check the iShares product ids and SEC series ids")
        return result

    df = read_universe_csv()
    for col in ("ISIN", "LEI"):
        if col not in df.columns:
            result.status = "failed"
            result.errors.append(f"universe CSV has no {col} column")
            return result

    rows = df.to_dict("records")
    proposals = resolve(rows, mapping, result)
    if limit is not None:
        proposals = proposals[:limit]
    result.proposals = proposals

    by_ticker = {p.ticker: p for p in proposals}
    for p in proposals:
        row = next((r for r in rows if (r.get("Ticker") or "").strip() == p.ticker), None)
        if row and p.writes_isin and _hq_prefix_diverges(row, p.isin):
            result.prefix_divergences.append(p)

    if dry_run:
        result.status = "skipped (dry run)"
        return result

    if not proposals:
        result.status = "unchanged"
        return result

    backup_csv(str(config.CSV_PATH))
    isin_col, lei_col = df.columns.get_loc("ISIN"), df.columns.get_loc("LEI")
    for i, tkr in enumerate(df["Ticker"].astype(str).str.strip()):
        p = by_ticker.get(tkr)
        if not p:
            continue
        if p.writes_isin:
            df.iat[i, isin_col] = p.isin
            result.isin_written += 1
        if p.writes_lei:
            df.iat[i, lei_col] = p.lei
            result.lei_written += 1
    write_universe_csv(df)
    log.info("wrote %d ISIN and %d LEI values", result.isin_written, result.lei_written)
    return result


def write_report(result: BackfillResult, *, reports_dir: Path | None = None,
                 today: str | None = None) -> Path:
    reports_dir = reports_dir or config.REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    today = today or date.today().isoformat()
    path = reports_dir / f"foreign_identifiers_{today}.md"

    L = [f"# Foreign identifier backfill — {today}", "",
         f"**Status:** `{result.status}`", ""]
    for f in result.funds_ok:
        L.append(f"- source OK: {f}")
    for f in result.funds_failed:
        L.append(f"- ⚠️ source FAILED: {f}")
    for e in result.errors:
        L.append(f"- ❌ {e}")
    L += ["",
          f"- `(ticker, country)` keys built: **{result.map_size:,}**",
          f"- universe rows eligible (foreign suffix, missing an id): **{result.candidates:,}**",
          f"- proposals passing every guard: **{len(result.proposals):,}**",
          f"- ISIN written: **{result.isin_written}** · LEI written: **{result.lei_written}**",
          f"- foreign rows with no exchange suffix (not resolvable here): {result.no_suffix}",
          ""]

    if result.proposals:
        L += ["## Filled", "",
              "| Ticker | Company | ISIN | LEI | Country | Exch | Sim |",
              "|---|---|---|---|---|---|---:|"]
        for p in result.proposals:
            L.append(f"| `{p.ticker}` | {p.company} | `{p.isin}` | "
                     f"{'`' + p.lei + '`' if p.lei else '—'} | {p.fund_country} | "
                     f"{p.exchange} | {p.similarity:.2f} |")
        L.append("")

    if result.prefix_divergences:
        L += ["## Incorporation differs from HQ — expected, not errors", "",
              "The ISIN's country prefix disagrees with `Country (HQ)`. Cayman, Bermuda "
              "and Irish incorporation of a China- or US-operating issuer is ordinary; "
              "these came from an SEC filing with the issuer's own LEI attached. Listed "
              "so an *unexpected* one is seen.", "",
              "| Ticker | Company | ISIN | invCountry |", "|---|---|---|---|"]
        for p in result.prefix_divergences:
            L.append(f"| `{p.ticker}` | {p.company} | `{p.isin}` | {p.inv_country} |")
        L.append("")

    if result.rejected_name:
        L += ["## Rejected — name disagreement", "",
              "Ticker and country matched but the companies do not. A wrong identifier "
              "is worse than a missing one.", "",
              "| Ticker | Coverage Manager | Fund holding | Sim |", "|---|---|---|---:|"]
        for t, a, b, s in result.rejected_name:
            L.append(f"| `{t}` | {a} | {b} | {s if s is not None else '—'} |")
        L.append("")

    if result.rejected_country:
        L += ["## Rejected — exchange/country disagreement", "",
              "The local ticker exists in the fund but on a different exchange. A bare "
              "local ticker is not an identity: keyed on ticker alone, `1801.HK` "
              "(Innovent, Hong Kong) resolves to a Japanese ISIN.", "",
              "| Ticker | Company | Found in | Expected |", "|---|---|---|---|"]
        for t, c, got, want in result.rejected_country:
            L.append(f"| `{t}` | {c} | {got} | {', '.join(want)} |")
        L.append("")

    path.write_text("\n".join(L), encoding="utf-8")
    return path
