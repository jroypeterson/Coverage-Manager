"""Find rows whose OWN fields disagree about which company they describe.

The `Secare SS` row carried Swedencare's ticker, website, listing year and vendor sector
under the company name "Sectra AB", with `Core=Y` and a blank ISIN. **The weekly identity
check ran over all 1,341 rows on 2026-08-08 and cleared it**, because that check asks a
vendor whether the ticker is still alive -- and it is; it just belongs to a different
company. Nothing looked at whether the row is internally consistent.

This does. Network-free, warning-level, no mutations: it reads the universe and reports.

The signature is a row where a HAND-entered field (Company Name, Sector/Subsector (JP))
and a VENDOR-derived field (Website, YF Sector/Industry) describe different companies --
which happens when a ticker is wrong and enrichment faithfully populates the row from
whoever really owns that ticker.

Usage:  python scripts/identity_consistency_sweep.py [--all]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

CM = Path(__file__).resolve().parent.parent
CSV = CM / "data" / "coverage_universe_tickers.csv"

# Corporate boilerplate identifies nobody -- matching on it would clear any row.
_GENERIC = {
    "inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "holding",
    "holdings", "group", "the", "sa", "nv", "ag", "se", "as", "ab", "spa", "kgaa", "asa",
    "oyj", "publ", "therapeutics", "pharmaceuticals", "pharmaceutical", "pharma", "biosciences",
    "bioscience", "sciences", "science", "labs", "laboratories", "laboratory", "technologies",
    "technology", "systems", "solutions", "international", "industries", "medical", "health",
    "healthcare", "and", "of", "participacoes", "biopharma", "bio", "life", "global",
    "worldwide", "partners", "enterprises", "ventures", "capital", "trust", "reit",
}
# Hosts that never encode the issuer's identity.
_HOST_NOISE = {"www", "com", "net", "org", "co", "uk", "de", "fr", "se", "dk", "ch", "it",
               "nl", "jp", "cn", "br", "in", "au", "ca", "no", "fi", "es", "be", "at", "ir",
               "investors", "investor", "group", "global", "corp", "inc", "holdings"}


def tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (name or "").lower())
            if len(w) > 3 and w not in _GENERIC}


def host_tokens(url: str) -> set[str]:
    m = re.sub(r"^https?://", "", (url or "").strip().lower()).split("/")[0]
    return {w for w in re.split(r"[.\-]", m) if len(w) > 3 and w not in _HOST_NOISE}


def _subseq(short: str, long: str) -> bool:
    """Are `short`'s letters present in `long`, in order?"""
    it = iter(long)
    return all(ch in it for ch in short)


def _explains(name_toks: set[str], host_toks: set[str]) -> bool:
    """Can the domain be explained by the company name (or vice versa)?

    Abbreviation domains are the norm, not an anomaly: vrtx.com for Vertex, pahc.com for
    Phibro Animal Health Corp, fico.com for Fair Isaac Corp, atecspine.com for Alphatec.
    A naive "do they share a token" test flags every one of them, which buries the real
    signal -- the first run produced 14 Core-row hits and ALL were domains of this kind.
    So a domain is accepted when its letters appear in order somewhere in the name, or the
    name's do in the domain. `swedencare` is not a subsequence of `sectra` in any order,
    which is what made that row visible.
    """
    flat_n = "".join(sorted(name_toks))
    flat_h = "".join(sorted(host_toks))
    for n in name_toks:
        for h in host_toks:
            if n in h or h in n or _subseq(h, n) or _subseq(n, h):
                return True
    return _subseq(flat_h, flat_n) or _subseq(flat_n, flat_h)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="report every finding, not just Core=Y rows")
    args = ap.parse_args()

    rows = list(csv.DictReader(CSV.open(newline="", encoding="utf-8-sig")))
    name_vs_site, blank_isin_core, no_site = [], [], []

    for r in rows:
        tk = (r.get("Ticker") or "").strip()
        name = (r.get("Company Name") or "").strip()
        site = (r.get("Website") or "").strip()
        isin = (r.get("ISIN") or "").strip()
        core = (r.get("Core") or "").strip().upper() == "Y"

        if site:
            nt, ht = tokens(name), host_tokens(site)
            # Only a CONFLICT counts: both sides have something distinctive to say and
            # they share nothing. A domain that simply omits the name (abbreviations,
            # holding-company sites) says nothing either way and must not be flagged.
            if nt and ht and not (nt & ht):
                # Substring both ways catches "swedencare" vs "sweden care" style splits
                # and abbreviations embedded in a longer host.
                if not _explains(nt, ht):
                    name_vs_site.append((tk, name, site, core))
        elif core:
            no_site.append((tk, name))

        # A row is unresolvable only when it has NO durable identifier. ISIN alone
        # over-counts: for US names CIK is the key that every downstream system uses,
        # and 112 "blank ISIN" hits collapsed to a far smaller set once CIK and FIGI
        # were counted as identifiers too.
        cik = (r.get("CIK") or "").strip()
        figi = (r.get("FIGI") or "").strip() or (r.get("Composite FIGI") or "").strip()
        if core and not (isin or cik or figi):
            blank_isin_core.append((tk, name, (r.get("Exchange") or "").strip()))

    def show(title, items, fmt, limit=None):
        print(f"\n{title}: {len(items)}")
        for it in (items[:limit] if limit else items):
            print("   " + fmt(it))
        if limit and len(items) > limit:
            print(f"   ... and {len(items) - limit} more")

    print(f"swept {len(rows)} rows")
    flagged = [x for x in name_vs_site if args.all or x[3]]
    show("NAME vs WEBSITE conflict (the Secare signature)", flagged,
         lambda x: f"{x[0]:<11} {x[1][:30]:<30} {x[2][:44]}"
                   + ("   [Core]" if x[3] else ""))
    show("Core=Y with NO durable identifier (no ISIN, CIK or FIGI)", blank_isin_core,
         lambda x: f"{x[0]:<11} {x[1][:30]:<30} {x[2]}", limit=25)
    show("Core=Y with NO website (cannot be cross-checked)", no_site,
         lambda x: f"{x[0]:<11} {x[1][:30]}", limit=15)
    return 0


if __name__ == "__main__":
    sys.exit(main())
