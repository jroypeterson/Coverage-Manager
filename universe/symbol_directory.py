"""Weekly snapshot + diff of the US exchange symbol directories.

**What it is.** Nasdaq Trader publishes two free, unauthenticated, pipe-delimited
files listing every security on every US venue — `nasdaqlisted.txt` (Nasdaq) and
`otherlisted.txt` (NYSE `N`, NYSE Arca `P`, NYSE American `A`, Cboe BZX `Z`,
IEX `V`). Together ~13,100 rows, of which ~7,500 are operating companies once
ETFs and test issues are dropped.

**Why it earns its place.** This is the exchanges' own record, not a vendor's
curation, and one weekly diff does four jobs the fleet currently pays for
separately or not at all:

1. **New-listing discovery.** A symbol appearing between two snapshots is a new
   listing — IPO, spin-off, direct listing or uplisting — whether or not the
   Finnhub IPO calendar caught it. Spin-offs in particular have no offering, so
   the calendar structurally cannot see them (the Jersey Mike's / HONA class).
2. **In/out tracking for the coverage universe.** JP's 2026-08-06 decision was to
   put all biopharma in the universe rather than keep a separate registry, on the
   basis that this diff would track names falling in and out. This is that.
3. **A second source for `delisted_check`.** Its docs say the `no_data` bucket
   "needs a second source"; a name absent from the exchange's own directory is
   that source, and it is a *positive* signal rather than an inference from
   Yahoo's silence.
4. **Financial-status warnings.** `nasdaqlisted.txt` carries a `Financial Status`
   field (`D` = deficient / delinquent / bankrupt) that nothing in the fleet reads.

**Snapshot, because the source keeps no archive** — same reasoning as the CRSP
job. Nasdaq overwrites these files continuously; a week not captured is a diff
that can never be computed.

**Three states, never two.** A fetch that fails is `inconclusive`, never "no
changes" — reporting a failed download as a quiet week is precisely how a
watchdog stops watching. The first run establishes a baseline and reports
`baseline`, not 7,500 new listings.
"""
from __future__ import annotations

import csv
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_URL = "https://www.nasdaqtrader.com/dynamic/symdir/"
USER_AGENT = "coverage-manager (jroypeterson@gmail.com)"

# Exchange single-letter codes used by otherlisted.txt. An unmapped code is
# passed through raw rather than guessed — a wrong venue label is worse than an
# unfamiliar one.
EXCHANGE_CODES = {
    "N": "NYSE", "P": "NYSE Arca", "A": "NYSE American",
    "Z": "Cboe BZX", "V": "IEX",
}

# Which CM `Exchange` values mean "this row trades on a US venue", and so may
# legitimately be compared against these files. A foreign line (SIX, HKEX, NSE)
# is absent from the directory by definition and must never be read as delisted.
US_EXCHANGES = {"NASDAQ", "NYSE", "NYSE AMERICAN", "NYSE ARCA", "AMEX",
                "NYSE MKT", "CBOE", "BATS", "IEX"}

# Nasdaq's Financial Status codes. These are DISTINCT states, not synonyms --
# rendering them all as "deficient / delinquent / bankrupt" would tell a reader
# that a late filer and a bankruptcy are the same fact.
FINANCIAL_STATUS = {
    "D": "Deficient (failed a listing requirement)",
    "E": "Delinquent (late SEC filing)",
    "Q": "Bankrupt",
    "G": "Deficient and Bankrupt",
    "H": "Deficient and Delinquent",
    "J": "Delinquent and Bankrupt",
    "K": "Deficient, Delinquent and Bankrupt",
    "N": "Normal",
}

FOOTER_RE = re.compile(r"^File Creation Time:\s*(\d{8})(\d{2}):(\d{2})", re.I)

FILES = {
    "nasdaqlisted.txt": {"symbol_col": "Symbol", "exchange": "NASDAQ"},
    "otherlisted.txt": {"symbol_col": "ACT Symbol", "exchange_col": "Exchange"},
}

SNAPSHOT_DIR = Path("data/symbol_directory")


@dataclass
class Symbol:
    symbol: str
    name: str
    exchange: str
    etf: bool
    test_issue: bool
    financial_status: str = ""


@dataclass
class FetchResult:
    """`status` is ok | inconclusive. Never a bare bool — a failed download and
    an empty week are different facts and must not share a representation."""
    status: str
    symbols: dict[str, Symbol] = field(default_factory=dict)
    created_at: datetime | None = None
    error: str = ""


def _parse_footer(line: str) -> datetime | None:
    m = FOOTER_RE.match(line.strip())
    if not m:
        return None
    try:
        stamp = datetime.strptime(m.group(1), "%m%d%Y")
        return stamp.replace(hour=int(m.group(2)), minute=int(m.group(3)),
                             tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_directory(text: str, spec: dict) -> tuple[dict[str, Symbol], datetime | None]:
    """Parse one pipe-delimited directory file. Drops ETFs and test issues."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}, None
    created = None
    if FOOTER_RE.match(lines[-1].strip()):
        created = _parse_footer(lines.pop().strip())

    reader = csv.DictReader(lines, delimiter="|")
    out: dict[str, Symbol] = {}
    for row in reader:
        sym = (row.get(spec["symbol_col"]) or "").strip().upper()
        if not sym:
            continue
        if (row.get("Test Issue") or "").strip().upper() == "Y":
            continue
        if (row.get("ETF") or "").strip().upper() == "Y":
            continue
        if "exchange" in spec:
            exch = spec["exchange"]
        else:
            code = (row.get(spec["exchange_col"]) or "").strip().upper()
            exch = EXCHANGE_CODES.get(code, code)
        out[sym] = Symbol(
            symbol=sym,
            name=(row.get("Security Name") or "").strip(),
            exchange=exch,
            etf=False,
            test_issue=False,
            financial_status=(row.get("Financial Status") or "").strip().upper(),
        )
    return out, created


def fetch_all(*, opener=None, timeout: int = 30) -> FetchResult:
    """Download and parse both files.

    A failure on EITHER file yields `inconclusive` for the whole run. A partial
    directory would make every symbol from the missing venue look delisted —
    the single most dangerous false positive this module can produce.
    """
    opener = opener or (lambda url: urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
        timeout=timeout))

    symbols: dict[str, Symbol] = {}
    newest: datetime | None = None
    for fname, spec in FILES.items():
        try:
            raw = opener(BASE_URL + fname).read()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return FetchResult(status="inconclusive",
                               error=f"{fname}: {type(exc).__name__}: {exc}")
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        parsed, created = parse_directory(text, spec)
        if not parsed:
            return FetchResult(status="inconclusive",
                               error=f"{fname}: parsed to zero rows")
        symbols.update(parsed)
        if created and (newest is None or created > newest):
            newest = created
    return FetchResult(status="ok", symbols=symbols, created_at=newest)


# ------------------------------------------------------------------- snapshots


def snapshot_path(root: Path, when: date) -> Path:
    return root / SNAPSHOT_DIR / f"symbols_{when.isoformat()}.csv"


def write_snapshot(path: Path, symbols: dict[str, Symbol]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "name", "exchange", "financial_status"])
        for s in sorted(symbols.values(), key=lambda x: x.symbol):
            w.writerow([s.symbol, s.name, s.exchange, s.financial_status])
    tmp.replace(path)
    return path


def read_snapshot(path: Path) -> dict[str, Symbol]:
    out: dict[str, Symbol] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sym = (row.get("symbol") or "").strip().upper()
            if sym:
                out[sym] = Symbol(symbol=sym, name=row.get("name", ""),
                                  exchange=row.get("exchange", ""),
                                  etf=False, test_issue=False,
                                  financial_status=row.get("financial_status", ""))
    return out


def latest_prior_snapshot(root: Path, before: date) -> tuple[date, Path] | None:
    folder = root / SNAPSHOT_DIR
    if not folder.is_dir():
        return None
    best: tuple[date, Path] | None = None
    for p in folder.glob("symbols_*.csv"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
        if not m:
            continue
        try:
            when = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if when < before and (best is None or when > best[0]):
            best = (when, p)
    return best


# ------------------------------------------------------------------ reconcile


@dataclass
class Reconciliation:
    added: list[Symbol]
    removed: list[Symbol]
    universe_removed: list[str]           # our names that vanished this period
    universe_missing: list[str]           # US rows absent from the directory
    universe_deficient: list[tuple[str, str]]   # (ticker, financial_status)
    prior_date: date | None
    checked_us_rows: int


def reconcile(current: dict[str, Symbol], prior: dict[str, Symbol] | None,
              universe_rows: list[dict], prior_date: date | None) -> Reconciliation:
    """Diff two snapshots and cross-reference the coverage universe.

    `universe_rows` are dicts with at least `Ticker` and `Exchange`. Only rows on
    a US venue are compared — a foreign line is absent from these files by
    definition, and flagging it would be an artefact of the question, not a fact
    about the company.
    """
    added, removed = [], []
    if prior is not None:
        added = [current[s] for s in sorted(set(current) - set(prior))]
        removed = [prior[s] for s in sorted(set(prior) - set(current))]

    us_rows = [r for r in universe_rows
               if (r.get("Exchange") or "").strip().upper() in US_EXCHANGES]
    us_tickers = {(r.get("Ticker") or "").strip().upper() for r in us_rows}
    us_tickers.discard("")

    removed_syms = {s.symbol for s in removed}
    universe_removed = sorted(us_tickers & removed_syms)
    universe_missing = sorted(t for t in us_tickers if t not in current)
    universe_deficient = sorted(
        (t, current[t].financial_status) for t in us_tickers
        if t in current and current[t].financial_status not in ("", "N")
    )
    return Reconciliation(
        added=added, removed=removed,
        universe_removed=universe_removed,
        universe_missing=universe_missing,
        universe_deficient=universe_deficient,
        prior_date=prior_date,
        checked_us_rows=len(us_tickers),
    )


# --------------------------------------------------------------------- report


def render_report(rec: Reconciliation, fetched: FetchResult, when: date,
                  total: int, verdicts: list | None = None) -> str:
    out = [f"# US symbol-directory watch — {when.isoformat()}", ""]
    stamp = fetched.created_at.strftime("%Y-%m-%d %H:%M UTC") if fetched.created_at else "unknown"
    out += [f"**Directory rows (operating companies, ETFs and test issues dropped):** {total:,}",
            f"**Source file creation time:** {stamp}", ""]

    if rec.prior_date is None:
        out += ["## Baseline established", "",
                "First snapshot — there is nothing to diff against. Next run "
                "reports real additions and removals.", ""]
    else:
        out += [f"## Listing changes since {rec.prior_date.isoformat()}", "",
                f"- **{len(rec.added)}** new listings",
                f"- **{len(rec.removed)}** removed", ""]
        if rec.added:
            out += ["### New listings", "",
                    "| Symbol | Name | Exchange |", "|---|---|---|"]
            out += [f"| `{s.symbol}` | {s.name[:70]} | {s.exchange} |"
                    for s in rec.added[:120]]
            if len(rec.added) > 120:
                out.append(f"| … | _{len(rec.added) - 120} more, see the snapshot_ | |")
            out.append("")
        if rec.removed:
            out += ["### Removed", "", "| Symbol | Name | Exchange |", "|---|---|---|"]
            out += [f"| `{s.symbol}` | {s.name[:70]} | {s.exchange} |"
                    for s in rec.removed[:120]]
            if len(rec.removed) > 120:
                out.append(f"| … | _{len(rec.removed) - 120} more_ | |")
            out.append("")

    out += [f"## Coverage universe cross-check ({rec.checked_us_rows} US-listed rows)", ""]
    if rec.universe_removed:
        out += ["### :rotating_light: Covered names removed from the exchange this period", ""]
        out += [f"- `{t}`" for t in rec.universe_removed] + [""]
    if rec.universe_missing:
        out += ["### Covered US rows absent from the directory", ""]
        if verdicts:
            by = {"delisted": [], "listed": [], "inconclusive": []}
            for v in verdicts:
                by.get(v.status, by["inconclusive"]).append(v)
            out += [f"**{len(by['delisted'])} confirmed delisted · "
                    f"{len(by['listed'])} symbol mismatch · "
                    f"{len(by['inconclusive'])} inconclusive**", "",
                    "Absence from the directory is a candidate; SEC's per-CIK "
                    "submissions endpoint is the authority that turns it into a "
                    "verdict. Inconclusive is never folded into delisted.", "",
                    "| Ticker | Verdict | Evidence |", "|---|---|---|"]
            for status in ("delisted", "listed", "inconclusive"):
                for v in by[status]:
                    out.append(f"| `{v.ticker}` | {status} | {v.detail} |")
            out.append("")
            if by["delisted"]:
                out += ["**Action:** confirm, then remove each row from "
                        "`data/coverage_universe_tickers.csv` and append it to "
                        "`data/delisted_tickers.csv` with its last-known data.", ""]
            if by["inconclusive"]:
                noc = [v.ticker for v in by["inconclusive"]
                       if "no CIK" in v.detail]
                if noc:
                    out += [f"**{len(noc)} could not be adjudicated for want of a "
                            f"CIK** — run `python cli.py backfill-cik` and re-run "
                            f"this check.", ""]
        else:
            out += ["A delisting, an uplisting to a venue not in these files, or "
                    "a symbol-format mismatch. Not a verdict on its own — pair "
                    "with `check-delisted` and `check-ticker-changes`.", ""]
            out += [f"- `{t}`" for t in rec.universe_missing] + [""]
    if rec.universe_deficient:
        out += ["### Nasdaq financial-status flags on covered names", "",
                "Nasdaq's own field, not an inference. A code here means the "
                "exchange has an open issue with the listing.", "",
                "| Ticker | Code | Meaning |", "|---|---|---|"]
        out += [f"| `{t}` | {c} | {FINANCIAL_STATUS.get(c, 'unmapped code')} |"
                for t, c in rec.universe_deficient] + [""]
    if not (rec.universe_removed or rec.universe_missing or rec.universe_deficient):
        out += ["No covered name is missing or flagged. ", ""]
    return "\n".join(out)


def run(root: Path, *, today: date | None = None, opener=None,
        dry_run: bool = False, confirm: bool = True,
        identity: str = "") -> tuple[str, Reconciliation | None, str]:
    """-> (status, reconciliation, report_text). status: ok | inconclusive."""
    today = today or date.today()
    fetched = fetch_all(opener=opener)
    if fetched.status != "ok":
        # Never write a snapshot from a failed fetch, and never report silence.
        logger.error("symbol directory unavailable: %s", fetched.error)
        return "inconclusive", None, ""

    prior_entry = latest_prior_snapshot(root, today)
    prior = read_snapshot(prior_entry[1]) if prior_entry else None
    prior_date = prior_entry[0] if prior_entry else None

    universe_rows: list[dict] = []
    uni = root / "data" / "coverage_universe_tickers.csv"
    if uni.exists():
        with open(uni, newline="", encoding="utf-8-sig") as f:
            universe_rows = list(csv.DictReader(f))

    rec = reconcile(fetched.symbols, prior, universe_rows, prior_date)

    verdicts = None
    if rec.universe_missing and confirm:
        ciks = {(r.get("Ticker") or "").strip().upper(): (r.get("CIK") or "")
                for r in universe_rows}
        verdicts = confirm_absence(rec.universe_missing, ciks,
                                   identity=identity or USER_AGENT)
    report = render_report(rec, fetched, today, len(fetched.symbols), verdicts)
    if not dry_run:
        write_snapshot(snapshot_path(root, today), fetched.symbols)
        rp = root / "reports" / f"symbol_directory_{today.isoformat()}.md"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(report, encoding="utf-8")
    return "ok", rec, report


# ------------------------------------------------- SEC confirmation of absence

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FORM_15 = ("15-12B", "15-12G", "15-15D")


@dataclass
class Verdict:
    ticker: str
    status: str          # delisted | listed | inconclusive
    detail: str


def confirm_absence(tickers: list[str], cik_by_ticker: dict[str, str], *,
                    opener=None, identity: str = USER_AGENT,
                    sleep=None) -> list[Verdict]:
    """Turn "absent from the exchange directory" into a verdict, or admit it can't.

    Absence is a *candidate*, not a finding — a symbol-format mismatch looks
    identical to a delisting. SEC's per-CIK submissions endpoint is the
    authority `ticker_change_check` already trusts: no live ticker, or a filed
    Form 15 (deregistration), confirms it.

    Three states. A row with no CIK, or an endpoint that will not answer, is
    `inconclusive` — never folded into `delisted`. Getting this wrong deletes a
    live company from the universe, which no downstream check would catch.
    """
    import time
    import json as _json
    opener = opener or (lambda url: urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": identity}), timeout=25))
    sleep = sleep if sleep is not None else (lambda: time.sleep(0.15))

    out: list[Verdict] = []
    for ticker in tickers:
        cik = (cik_by_ticker.get(ticker) or "").strip()
        if not cik:
            out.append(Verdict(ticker, "inconclusive", "no CIK in the universe row"))
            continue
        try:
            raw = opener(SUBMISSIONS_URL.format(cik=cik.zfill(10))).read()
            data = _json.loads(raw)
        except Exception as exc:                    # noqa: BLE001
            out.append(Verdict(ticker, "inconclusive",
                               f"submissions unreachable: {type(exc).__name__}"))
            continue
        live = [t for t in (data.get("tickers") or []) if t]
        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        form15 = next(((f, d) for f, d in zip(forms, dates) if f in FORM_15), None)

        if form15:
            out.append(Verdict(ticker, "delisted",
                               f"Form {form15[0]} filed {form15[1]}"))
        elif not live:
            out.append(Verdict(ticker, "delisted",
                               "SEC submissions shows no registered ticker"))
        else:
            out.append(Verdict(ticker, "listed",
                               f"SEC still lists {','.join(live)} — likely a "
                               f"symbol-format mismatch, not a delisting"))
        sleep()
    return out
