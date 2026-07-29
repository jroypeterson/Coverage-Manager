"""Audit foreign-row metadata against SEC N-PORT + fund holdings.

The **companion** to `foreign_identifiers.py`: that module *fills* missing ISIN
and LEI from broad international ETF holdings joined to the funds' SEC N-PORT
filings; this one takes the same joined source and asks whether the metadata the
universe already carries **agrees with it**. Same sources, same guards, opposite
question — mirroring the `delisted_check` / `ticker_change_check` pairing.

WHY THIS IS WORTH RUNNING
-------------------------
N-PORT is a legally filed document carrying the issuer's own LEI, ISIN and
country of investment. Where the universe disagrees with it, one of the two is
wrong, and until now nothing in the fleet could tell. It became worth building
only after the identifier backfill: the check can only reach rows that have
something to join on, and foreign ISIN coverage went 43% -> 59% on 2026-07-28.

WHAT IT DOES *NOT* TREAT AS A FINDING
-------------------------------------
Three classes of apparent disagreement are expected and are reported separately
(or not at all), because a check that cries wolf on them is one nobody reads:

* **Code format.** The universe stores ISO **alpha-3** (`GBR`); N-PORT uses
  alpha-2 (`GB`). Every one of the first 18 "country mismatches" found by hand
  was this. Normalised before comparison, never reported.
* **Incorporation vs headquarters.** `invCountry` is where the security is
  *incorporated*; `Country (HQ)` is where the company *operates*. Innovent is
  `CN` HQ and `KY` incorporated; Legend Biotech is US-HQ'd and `KY`
  incorporated. Both fields are right. Reported only in an explicitly labelled
  section, never as an error.
* **Hong Kong listings of mainland issuers**, which the funds label either
  `China` or `Hong Kong` depending on the vehicle.

The findings that remain are the ones with teeth: an **ISIN or LEI that
disagrees** with the filing (one of them is wrong), a **currency mismatch**, and
a **name divergence on a row whose ISIN matches** — which, because an ISIN
survives a rename, is the tell that the company was renamed and the universe
still carries the old name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import config
from universe.crsp_snapshot import _name_similarity
from universe.foreign_identifiers import (
    SUFFIX_COUNTRIES,
    US_HQ_VALUES,
    BackfillResult,
    build_map,
)

log = logging.getLogger(__name__)

# Below this, an ISIN-anchored pair is treated as a probable rename rather than
# cosmetic drift. Deliberately looser than the backfill's matching gate: there a
# weak name blocks a write, here it only raises a question.
NAME_DIVERGENCE_THRESHOLD = 0.60


@dataclass
class Finding:
    kind: str
    ticker: str
    company: str
    field: str
    universe_value: str
    source_value: str
    source: str
    note: str = ""


@dataclass
class CrosscheckResult:
    status: str = "ok"
    checked: int = 0
    matched: int = 0
    map_size: int = 0
    funds_ok: list[str] = field(default_factory=list)
    funds_failed: list[str] = field(default_factory=list)
    conflicts: list[Finding] = field(default_factory=list)
    incorporation_notes: list[Finding] = field(default_factory=list)
    unmatched: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != "failed"


def _iso2(country_name: str) -> str:
    """Country NAME -> ISO alpha-2, using the mapping enrich already relies on."""
    from ticker_utils import COUNTRY_TO_ISO2

    return COUNTRY_TO_ISO2.get((country_name or "").strip(), "")


def _iso3_to_iso2(code: str) -> str:
    from ticker_utils import COUNTRY_TO_ISO, COUNTRY_TO_ISO2

    code = (code or "").strip().upper()
    if len(code) == 2:
        return code
    for name, iso3 in COUNTRY_TO_ISO.items():
        if iso3 == code:
            return COUNTRY_TO_ISO2.get(name, "")
    return ""


def _prefix_matches_any_country(row: dict, isin: str) -> bool:
    """True if the ISIN's 2-letter prefix matches HQ or listing country.

    Mirrors `enrich.validate_isin_for_row`'s accept rule (set-valued since
    2026-07-28 — a country can accept more than one prefix), so a False here
    means the stored value would be rejected by the live enrichment path today.
    """
    from ticker_utils import COUNTRY_TO_ISIN_PREFIXES

    prefix = (isin or "")[:2].upper()
    for fld in ("Country (HQ)", "Country (Listing)"):
        want = COUNTRY_TO_ISIN_PREFIXES.get((row.get(fld) or "").strip())
        if want and prefix in want:
            return True
    return False


def _index_by_isin(mapping: dict) -> dict[str, dict]:
    out = {}
    for (tkr, loc), v in mapping.items():
        if v.get("isin"):
            out.setdefault(v["isin"], dict(v, ticker=tkr, location=loc))
    return out


def crosscheck(rows: list[dict], mapping: dict,
               result: CrosscheckResult) -> None:
    """Populate `result` with disagreements between `rows` and the fund sources.

    Rows are matched ISIN-first (an exact identifier beats any heuristic), then
    by `(local ticker, country)` — the same key the backfill uses, and for the
    same reason: a bare local ticker collides across exchanges.
    """
    by_isin = _index_by_isin(mapping)

    for row in rows:
        ticker = (row.get("Ticker") or "").strip()
        if not ticker:
            continue
        if (row.get("Country (HQ)") or "").strip().upper() in US_HQ_VALUES:
            continue
        result.checked += 1

        cm_isin = (row.get("ISIN") or "").strip().upper()
        hit = by_isin.get(cm_isin) if cm_isin else None
        matched_on = "ISIN"

        if hit is None:
            if "." not in ticker:
                result.unmatched += 1
                continue
            local, suffix = ticker.rsplit(".", 1)
            expected = SUFFIX_COUNTRIES.get(suffix.upper())
            if not expected:
                result.unmatched += 1
                continue
            cands = [(loc, v) for (t, loc), v in mapping.items()
                     if t == local.upper() and loc in expected]
            if not cands:
                result.unmatched += 1
                continue
            hit = dict(cands[0][1], ticker=local.upper(), location=cands[0][0])
            matched_on = "ticker+country"

        result.matched += 1
        company = (row.get("Company Name") or "").strip()

        def add(kind, fld, uni, src, note=""):
            result.conflicts.append(Finding(
                kind=kind, ticker=ticker, company=company, field=fld,
                universe_value=uni, source_value=src,
                source=f"{hit['source']} ({matched_on})", note=note))

        # --- identifier conflicts: one side is simply wrong -------------------
        src_isin = (hit.get("isin") or "").strip().upper()
        if cm_isin and src_isin and cm_isin != src_isin:
            # When the stored ISIN's country prefix matches neither the HQ nor
            # the listing country, it is not a close call — and it is exactly
            # what `enrich.validate_isin_for_row` exists to block, so such a row
            # entered before that guard or by a path that bypasses it.
            note = "an ISIN is a security's identity; two different values cannot both be right"
            if not _prefix_matches_any_country(row, cm_isin):
                note = ("stored prefix matches NEITHER Country (HQ) NOR Country (Listing) - "
                        "validate_isin_for_row should have blocked this, so it predates the "
                        "guard or was written by a path that skips it")
            add("isin-conflict", "ISIN", cm_isin, src_isin, note)

        cm_lei = (row.get("LEI") or "").strip().upper()
        src_lei = (hit.get("lei") or "").strip().upper()
        if cm_lei and src_lei and cm_lei != src_lei:
            add("lei-conflict", "LEI", cm_lei, src_lei,
                "the filing carries the issuer's own LEI")

        # Names are compared first because the answer changes what a currency
        # difference MEANS: same company, two listings (conflation) vs a
        # different company altogether (a wrong stored ISIN).
        name_sim = (_name_similarity(company, hit["name"])
                    if company and hit.get("name") else None)
        names_diverge = name_sim is not None and name_sim < NAME_DIVERGENCE_THRESHOLD

        # --- listing conflation -----------------------------------------------
        # A currency disagreement on an ISIN-matched row does not mean the
        # currency is wrong: it means the row's ISIN identifies a DIFFERENT
        # LISTING than its ticker, exchange and currency do. `AZN` is the live
        # case - ticker AZN on NYQ in USD (the US ADR) carrying GB0009895292,
        # the London ordinary. Both facts are individually right and the row
        # mixes two securities, so any ISIN-keyed join silently returns London
        # for a row that is tracking New York.
        # Suppressed when the names diverge: then the currency difference is a
        # symptom of the wrong company being matched, and the name-divergence
        # row below is the accurate diagnosis. `ZEN` (Zentek, Canada, CAD)
        # matched ZEN TECHNOLOGIES (India, INR) - calling that a "listing
        # conflation" would send a reader looking for an Indian listing of a
        # Canadian company that does not exist.
        cm_ccy = (row.get("Currency") or "").strip().upper()
        src_ccy = (hit.get("currency") or "").strip().upper()
        if cm_ccy and src_ccy and cm_ccy != src_ccy and not names_diverge:
            add("listing-mismatch", "Currency", cm_ccy, src_ccy,
                f"row trades in {cm_ccy} but its ISIN is the {src_ccy} line "
                f"({hit.get('exchange', '?')}) - ADR/ordinary or H-share/A-share conflation")

        # --- name divergence on an ISIN-anchored row --------------------------
        # An ISIN survives a rename, so names diverging under a matching ISIN is
        # normally the rename itself - but NOT always: `ZEN` (Zentek, Canada)
        # matched ZEN TECHNOLOGIES (India) because the stored ISIN is the Indian
        # company's. Both readings must stay on the table.
        if matched_on == "ISIN" and names_diverge:
            add("name-divergence", "Company Name", company, hit["name"],
                f"similarity {name_sim:.2f}; ISIN matches so this is either a rename the "
                f"universe has not caught, or the stored ISIN belongs to another company")

        # --- incorporation vs HQ: reported, never an error --------------------
        inv = (hit.get("inv_country") or "").strip().upper()
        hq2 = _iso2((row.get("Country (HQ)") or "").strip()) or \
            _iso3_to_iso2(row.get("Country (ISO)", ""))
        if inv and hq2 and inv != hq2:
            result.incorporation_notes.append(Finding(
                kind="incorporation", ticker=ticker, company=company,
                field="Country", universe_value=hq2, source_value=inv,
                source=hit["source"],
                note="SEC invCountry is where the security is incorporated, "
                     "not where the company operates"))


def main(*, use_cache: bool = True) -> CrosscheckResult:
    from ticker_utils import read_universe_csv

    result = CrosscheckResult()
    fetch = BackfillResult()
    mapping = build_map(use_cache=use_cache, result=fetch)
    result.funds_ok, result.funds_failed = fetch.funds_ok, fetch.funds_failed
    result.map_size = len(mapping)
    if not mapping:
        result.status = "failed"
        result.errors.append("no fund data available - every source failed")
        return result

    crosscheck(read_universe_csv().to_dict("records"), mapping, result)
    if result.conflicts:
        result.status = "conflicts"
    return result


def write_report(result: CrosscheckResult, *, reports_dir: Path | None = None,
                 today: str | None = None) -> Path:
    reports_dir = reports_dir or config.REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    today = today or date.today().isoformat()
    path = reports_dir / f"foreign_crosscheck_{today}.md"

    L = [f"# Foreign metadata cross-check vs SEC N-PORT - {today}", "",
         f"**Status:** `{result.status}`", ""]
    for f in result.funds_ok:
        L.append(f"- source OK: {f}")
    for f in result.funds_failed:
        L.append(f"- WARNING source FAILED: {f}")
    for e in result.errors:
        L.append(f"- ERROR {e}")
    L += ["",
          f"- foreign rows checked: **{result.checked:,}**",
          f"- matched to a fund holding: **{result.matched:,}** "
          f"(unmatched: {result.unmatched:,} - not held by these funds, or no key to join on)",
          f"- **conflicts: {len(result.conflicts)}**",
          f"- incorporation-vs-HQ notes (not errors): {len(result.incorporation_notes)}",
          ""]

    if result.conflicts:
        order = {"isin-conflict": 0, "lei-conflict": 1, "listing-mismatch": 2,
                 "name-divergence": 3}
        L += ["## Conflicts", "",
              "The universe and a legally filed document disagree. One of them is wrong.",
              "",
              "| Kind | Ticker | Company | Field | Coverage Manager | SEC / fund | Note |",
              "|---|---|---|---|---|---|---|"]
        for f in sorted(result.conflicts, key=lambda f: (order.get(f.kind, 9), f.ticker)):
            L.append(f"| `{f.kind}` | `{f.ticker}` | {f.company} | {f.field} | "
                     f"`{f.universe_value}` | `{f.source_value}` | {f.note} |")
        L.append("")
    else:
        L += ["## Conflicts", "",
              "_None. Every matched row agrees with its filing._", ""]

    if result.incorporation_notes:
        L += ["## Incorporation differs from HQ - expected, not errors", "",
              "`invCountry` is where the security is **incorporated**; `Country (HQ)` is "
              "where the company **operates**. Cayman, Bermuda, Jersey and Irish "
              "incorporation of a China- or US-operating issuer is ordinary. Listed so an "
              "*unexpected* one is visible - do not 'fix' these.", "",
              "| Ticker | Company | HQ | Incorporated |", "|---|---|---|---|"]
        for f in sorted(result.incorporation_notes, key=lambda f: f.ticker):
            L.append(f"| `{f.ticker}` | {f.company} | {f.universe_value} | "
                     f"{f.source_value} |")
        L.append("")

    path.write_text("\n".join(L), encoding="utf-8")
    return path
