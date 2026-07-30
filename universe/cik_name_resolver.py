"""Resolve blank-CIK rows by COMPANY NAME, to see what ticker-keyed lookups cannot.

## The blind spot this exists for

Two mechanisms should catch a renamed ticker and they fail as a pair, circularly:

  - `cik_backfill` fills a blank CIK by looking the row's **current ticker** up in
    SEC's bulk map. A renamed company's OLD ticker is no longer in that map, so
    the CIK can never be filled. Measured 2026-07-29: fills **0 of 236**.
  - `ticker_change_check` detects a rename by comparing SEC's ticker for the row's
    **CIK**. A blank CIK makes the row invisible to it.

So a row whose ticker changed AND whose CIK is blank is unreachable by both.
`FGEN` (FibroGen -> Kyntra Bio) was the proof, and `CYBN` (Cybin -> `HELP`) was
found by this module's own simulation before it was written.

## It is a DETECTOR, not a filler

There is no write path, and that is a deliberate conclusion rather than a
simplification. Of 236 blank-CIK rows only **three** have their ticker in SEC's
map -- `CSL`, `MED`, `MOVE` -- and those are exactly the three whose blank is
recorded as VERIFIED in the provenance ledger, because the SEC entity behind that
ticker is a *different company* (790051 Carlisle, 910329 Medifast, 1734750
Corvex). `cik_backfill`'s name gate correctly refused them, and that refusal is
*why* they are blank. The other 233 are absent from the map entirely.

Therefore a name match on a blank-CIK row can only ever resolve to a **different**
ticker -- there is nothing to write that `cik_backfill` has not already written.
What is left is a finding for a human, because a ticker is the published join key
~20 sibling repos match on.

It writes no provenance either: the ledger's contract is *which cells a human
verified*, at a bar of two independent sources. An automated fill has one.

## Why the verdicts are split the way they are

An exact-name lookup matches a foreign primary listing to the SAME issuer's US
ADR -- a different LINE, not a rename. Verified against live data: six rows would
be mislabelled, including `4507.T` Shionogi (SEC: `SGIOF`), `FRE` Fresenius
(`FSNUY`) and `CUV.AX`. `ticker_change_check` already carries a guard for this
class; this module keys the same idea off `Country (Listing)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from logging_utils import get_logger
from ticker_utils import normalize_company_for_comparison, read_universe_csv

logger = get_logger(__name__)

# ── verdicts ────────────────────────────────────────────────────────────────
STALE_US_LISTING = "stale_us_listing"                # the valuable class
SEC_REGISTERED_OTHER_LINE = "sec_registered_other_line"
LEDGER_CONFLICT = "ledger_conflict"
AMBIGUOUS_NAME = "ambiguous_name"
SHORT_NAME_SUPPRESSED = "short_name_suppressed"
NO_MATCH = "no_match"

#: A normalized name shorter than this is not evidence. `norm("CSL Limited")` is
#: `"csl"` -- three characters, and there are many three-letter registrants.
MIN_NAME_CHARS = 4


@dataclass
class Finding:
    ticker: str
    company: str
    verdict: str
    sec_cik: str = ""
    sec_title: str = ""
    sec_tickers: tuple = ()
    detail: str = ""


@dataclass
class ResolverResult:
    checked: int = 0
    findings: list = field(default_factory=list)
    fetched_ok: bool = True

    def by_verdict(self, verdict):
        return [f for f in self.findings if f.verdict == verdict]

    @property
    def needs_review(self):
        """Classes that want a human. `no_match` and `sec_registered_other_line`
        deliberately do NOT -- the first is the expected state for ~200 foreign
        non-registrants, and the second is informational."""
        return (self.by_verdict(STALE_US_LISTING)
                + self.by_verdict(AMBIGUOUS_NAME)
                + self.by_verdict(LEDGER_CONFLICT))


def build_name_index(cik_map):
    """`{normalized_name: {cik: (title, [tickers])}}` from `{TICKER: (cik, title)}`.

    Keyed by CIK inside, NOT by map entry: share classes give ONE issuer several
    tickers under the same title, and counting entries would false-ambiguate them
    (`BRK-A`/`BRK-B` is one company, not an ambiguous name).
    """
    index: dict[str, dict[str, tuple]] = {}
    for ticker, pair in (cik_map or {}).items():
        if not isinstance(pair, (tuple, list)) or len(pair) < 2:
            continue
        cik, title = str(pair[0]), str(pair[1])
        key = normalize_company_for_comparison(title)
        if not key:
            continue
        bucket = index.setdefault(key, {})
        if cik in bucket:
            bucket[cik][1].append(ticker)
        else:
            bucket[cik] = (title, [ticker])
    return index


def _is_us_listed(row) -> bool:
    listing = str(row.get("Country (Listing)", "") or "").strip()
    return listing == "United States"


def resolve(df=None, cik_map=None, ledger=None, tickers=None, limit=None,
            fetched_ok=None):
    """Report-only sweep over blank-CIK rows. Never writes.

    `ledger=None` means **auto-load the real ledger**, not "no ledger". Pass a
    structurally valid empty ledger to disable the routing.

    `fetched_ok` separates *"the SEC map could not be fetched"* from *"the map is
    empty"*. `fetch_sec_cik_map` returns `{}` on failure, so when this function
    fetches for itself an empty map does mean failure — and a run that learned
    nothing must never report a clean universe. But a CALLER that supplies a map
    knows whether its fetch worked, and an empty supplied map is a legitimate
    input (every row is then genuinely `no_match`). Conflating the two made an
    unreachable-SEC bail-out fire on ordinary no-match input.
    """
    from universe.cik_backfill import fetch_sec_cik_map

    if df is None:
        df = read_universe_csv()
    if cik_map is None:
        cik_map = fetch_sec_cik_map()
        if fetched_ok is None:
            fetched_ok = bool(cik_map)
    elif fetched_ok is None:
        fetched_ok = True

    result = ResolverResult(fetched_ok=bool(fetched_ok))
    if not fetched_ok:
        logger.warning("SEC map unavailable - resolver learned nothing this run")
        return result

    if ledger is None:
        try:
            from universe.provenance import load_ledger
            ledger = load_ledger()
        except Exception as e:  # noqa: BLE001 - a missing ledger must not abort
            logger.warning("provenance ledger unreadable (%s); ledger routing off", e)
            ledger = None

    index = build_name_index(cik_map)
    want = {t.strip().upper() for t in tickers} if tickers else None

    for _, row in df.iterrows():
        ticker = str(row.get("Ticker", "") or "").strip()
        if not ticker or str(row.get("CIK", "") or "").strip():
            continue
        if want and ticker.upper() not in want:
            continue
        if limit is not None and result.checked >= limit:
            break
        result.checked += 1
        company = str(row.get("Company Name", "") or "").strip()

        # Ledger first: a blank this repo has already ADJUDICATED must not be
        # re-raised as a fresh finding every week.
        if ledger is not None:
            try:
                from universe.provenance import ROW_UNVERIFIED, triage
                verdict, why = triage(ledger, ticker, "CIK")
                if verdict != ROW_UNVERIFIED:
                    result.findings.append(Finding(
                        ticker=ticker, company=company, verdict=LEDGER_CONFLICT,
                        detail=f"{verdict}: {why}"))
                    continue
            except Exception as e:  # noqa: BLE001
                logger.warning("triage failed for %s: %s", ticker, e)

        key = normalize_company_for_comparison(company)
        if not key or len(key) < MIN_NAME_CHARS:
            result.findings.append(Finding(
                ticker=ticker, company=company, verdict=SHORT_NAME_SUPPRESSED,
                detail=f"normalized name {key!r} is under {MIN_NAME_CHARS} chars - "
                       f"too short to be evidence"))
            continue

        bucket = index.get(key)
        if not bucket:
            result.findings.append(Finding(
                ticker=ticker, company=company, verdict=NO_MATCH,
                detail="no SEC entity with this exact normalized name"))
            continue
        if len(bucket) > 1:
            result.findings.append(Finding(
                ticker=ticker, company=company, verdict=AMBIGUOUS_NAME,
                detail=f"{len(bucket)} distinct CIKs share this name: "
                       + ", ".join(sorted(bucket))))
            continue

        cik, (title, sec_tickers) = next(iter(bucket.items()))
        same = ticker.upper() in {t.upper() for t in sec_tickers}
        if same:
            # Structurally near-impossible: cik_backfill would have filled it.
            # Recorded rather than assumed -- if it fires, 4a is not running.
            result.findings.append(Finding(
                ticker=ticker, company=company, verdict=LEDGER_CONFLICT,
                sec_cik=cik, sec_title=title, sec_tickers=tuple(sec_tickers),
                detail="SEC ticker MATCHES this row, yet the CIK is blank - "
                       "cik_backfill should already have filled it; check step 4a"))
            continue

        verdict = STALE_US_LISTING if _is_us_listed(row) else SEC_REGISTERED_OTHER_LINE
        detail = (f"SEC has this company under {', '.join(sorted(sec_tickers))}"
                  if verdict == STALE_US_LISTING else
                  f"SEC knows a US line ({', '.join(sorted(sec_tickers))}) for this "
                  f"issuer; the row tracks its non-US primary listing, so this is a "
                  f"DIFFERENT LINE, not a rename")
        result.findings.append(Finding(
            ticker=ticker, company=company, verdict=verdict, sec_cik=cik,
            sec_title=title, sec_tickers=tuple(sec_tickers), detail=detail))

    return result


def format_report(result, run_date=None) -> str:
    from datetime import date
    run_date = run_date or date.today()
    L = [f"# Blank-CIK resolution by company name - {run_date.isoformat()}", ""]
    if not result.fetched_ok:
        L += ["**The SEC map could not be fetched, so NOTHING was checked.** This is "
              "not a clean result.", ""]
        return "\n".join(L) + "\n"
    L += [f"Checked **{result.checked}** rows with a blank CIK.", "",
          "This lane is **report-only by design** - see the module docstring. A name "
          "match on a blank-CIK row can only resolve to a *different* ticker, and a "
          "ticker is the published join key ~20 sibling repos match on, so every "
          "finding is a human decision rather than a fill.", ""]

    sections = [
        (STALE_US_LISTING, "Stale US listing - THE ONE THAT MATTERS",
         "A US-listed row whose company SEC now files under a different ticker. "
         "This is the class `check-ticker-changes` structurally cannot see.\n\n"
         "**Read the new symbol before assuming a rename.** A 5-letter SEC ticker "
         "ending `Y` or `F` is usually an OTC line, which means the company was "
         "DELISTED from its exchange rather than rebranded - verified live for "
         "`ADAP` (Adaptimmune, SEC `ADAPY`, exchange OTC). That is the `ZOM` -> "
         "`ZOMDF` shape, and it is material in a different way: the row should "
         "record the venue move, and `delisted_check` is the sibling lane. A "
         "same-length alphabetic change (`CYBN` -> `HELP`) is the true rename."),
        (AMBIGUOUS_NAME, "Ambiguous name", "More than one distinct CIK shares this name. Never actioned automatically."),
        (LEDGER_CONFLICT, "Already adjudicated", "The provenance ledger has a verdict for this cell. Re-review only if new evidence exists."),
        (SEC_REGISTERED_OTHER_LINE, "SEC-registered other line (low severity)",
         "The row tracks a non-US primary listing and SEC knows a US line for the "
         "same issuer. NOT a rename. Filling the CIK here is legitimate after "
         "review - `UCB` carries CIK 1290640 on a Belgian-primary row by "
         "deliberate ledger entry."),
        (SHORT_NAME_SUPPRESSED, "Name too short to match on", "Suppressed deliberately."),
        (NO_MATCH, "No SEC entity (expected for foreign non-registrants)",
         "For a **US-HQ** row this is worth a look - it may mean the registrant "
         "deregistered, which is `delisted_check`'s territory."),
    ]
    for verdict, heading, blurb in sections:
        rows = result.by_verdict(verdict)
        L += [f"## {heading} ({len(rows)})", "", blurb, ""]
        if not rows:
            L += ["_none_", ""]
            continue
        L += ["| Ticker | Company | SEC title | SEC ticker(s) | CIK | Detail |",
              "|---|---|---|---|---|---|"]
        for f in sorted(rows, key=lambda x: x.ticker):
            L.append(f"| `{f.ticker}` | {f.company} | {f.sec_title} | "
                     f"{', '.join(f.sec_tickers)} | {f.sec_cik} | {f.detail} |")
        L.append("")
    return "\n".join(L) + "\n"


def write_report(result, reports_dir=None, run_date=None):
    from datetime import date
    from pathlib import Path

    import config
    reports_dir = Path(reports_dir) if reports_dir else config.REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = run_date or date.today()
    path = reports_dir / f"cik_name_resolution_{run_date.isoformat()}.md"
    path.write_text(format_report(result, run_date), encoding="utf-8")
    return path


def main(*, tickers=None, limit=None):
    result = resolve(tickers=tickers, limit=limit)
    report = write_report(result)
    return result, report
