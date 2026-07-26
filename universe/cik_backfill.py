"""Backfill blank CIKs from SEC EDGAR's bulk ticker map, every weekly run.

**Why this exists.** A CIK is not a static fact about a company — it is a fact
about whether that company has *registered with the SEC yet*. `enrich.py`
resolves CIKs when a row is first enriched, and nothing re-checked the blanks
afterwards. So a name that registers LATER keeps a blank CIK forever, and every
downstream lane keyed on CIK silently skips it.

That is not hypothetical. On 2026-07-25 an independent insider cross-check
found SpaceX (SPCX) filing 40 Form 3s from its entire board — it had registered,
was held in the portfolio, and `insider_ownership` had been skipping it because
its CIK was blank. Re-probing the blanks found **16** such rows, including
Cerebras, Fervo Energy, Quantinuum, HawkEye 360, Lumexa (the subject of an open
earnings-agent alert) and GMR Solutions and Mobia Medical (two of sigma-alert's
chronic skips).

**Why it is cheap.** SEC's `company_tickers.json` is one free unauthenticated
download covering every registrant, and only blank rows are looked up in it —
there is no per-ticker request. The step therefore costs one HTTP GET per weekly
run regardless of universe size.

**Why it only fills blanks.** An existing CIK is never overwritten. A CIK is
stable across a ticker change (that invariant is what `ticker_change_check`
relies on), so a disagreement between a populated CIK and the map means the
TICKER moved, not the CIK — which is that module's job to surface, not this
one's to silently "fix".

CLI: `python cli.py backfill-cik [--dry-run]`
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from config import CSV_PATH
from logging_utils import get_logger
from ticker_utils import (
    backup_csv,
    normalize_company_for_comparison,
    read_universe_csv,
    write_universe_csv,
)
from ticker_utils import normalize_symbol_for_matching as _norm_symbol

logger = get_logger(__name__)

# The SEC download itself (URL, User-Agent, 24h cache, fetched-ok contract) lives
# in `ticker_change_check.load_sec_cik_map` -- see `fetch_sec_cik_map` below.


#: Below this normalized-name similarity, refuse to bind a row to a CIK. Mirrors
#: `delisted_check.NAME_SIMILARITY_THRESHOLD`, which solves the same problem
#: (has this ticker been recycled to a different issuer?) from the other side.
NAME_MATCH_THRESHOLD = 0.55

#: Only plain US-style symbols take the separator-insensitive fallback. Foreign
#: lines dominate the blank-CIK population (`4503.T`, `000100.KS`, `1093.HK`),
#: and stripping their separators produces keys like `4503T` that could collide
#: with an unrelated SEC symbol. `build_norm_index` can only rule out
#: collisions among SEC's own symbols -- it cannot stop a universe-side foreign
#: ticker from normalizing onto a real US one. Those rows are US-registrant
#: candidates only via an exact match, which needs no normalization anyway.
#: Same gate `ticker_change_check` applies for the same reason.
_PLAIN_US_SYMBOL = re.compile(r"^[A-Z]{1,5}([.\-/][A-Z])?$")


def build_norm_index(cik_map: dict[str, tuple[str, str]]) -> dict[str, str]:
    """`{normalized_symbol: cik}` for symbols whose normalized form is unique.

    SEC writes share classes with a hyphen (`BRK-B`) where the universe often
    carries a dot (`BRK.B`), so exact string matching leaves a registered issuer
    blank and invisible to every CIK-keyed lane. Normalizing strips the
    separator — but only unambiguous mappings are kept: if two different SEC
    symbols normalize to the same string we cannot tell which issuer a universe
    row meant, and guessing would write a WRONG CIK, which is far worse than
    leaving it blank (a blank is visibly missing; a wrong CIK silently pulls
    another company's filings).
    """
    seen: dict[str, set[str]] = {}
    for ticker, (cik, _title) in cik_map.items():
        seen.setdefault(_norm_symbol(ticker), set()).add(cik)
    return {norm: next(iter(ciks)) for norm, ciks in seen.items() if len(ciks) == 1}


def fetch_sec_cik_map() -> dict[str, tuple[str, str]]:
    """`{TICKER: (cik, sec_title)}` from SEC's bulk file. Empty on any failure.

    Delegates to `ticker_change_check.load_sec_cik_map`, which downloads the
    same ~1 MB file this module needs and caches it for 24h. Fetching it
    separately meant weekly steps 4a and 4b pulled it back-to-back, and only one
    of them survived a brief SEC outage on cache. Sharing also carries the SEC
    **title** through, which `_name_matches` needs.
    """
    from universe.ticker_change_check import load_sec_cik_map  # noqa: PLC0415

    cik_map, fetched_ok = load_sec_cik_map()
    if not fetched_ok:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for cik, entry in cik_map.items():
        title = str(entry.get("title") or "")
        for ticker in entry.get("tickers") or []:
            t = str(ticker).strip().upper()
            if t:
                out[t] = (str(cik), title)
    logger.info("SEC EDGAR: %s ticker->CIK mappings", len(out))
    return out


def _name_matches(recorded_name: str, sec_title: str) -> bool:
    """Does the universe row plausibly describe the same company SEC does?

    A ticker string alone is NOT identity. Tickers get recycled between issuers
    -- that is the entire premise of the sibling `delisted_check` module -- and
    the rows this backfill targets are the most exposed of all: privately-held
    names carried under provisional symbols (SPCX, Cerebras, Fervo, Quantinuum)
    assigned before any listing existed. If such a symbol is, or later becomes,
    some other registrant's ticker, matching on the string alone writes THAT
    company's CIK into the master CSV, and the CIK-keyed lanes downstream
    (insider_ownership, earnings_agent) then silently pull the wrong company's
    filings. Writing a wrong CIK is far worse than leaving a blank one: a blank
    is visibly missing, a wrong one looks like data.

    Deliberately permissive -- the goal is to catch "this is a different
    company", not to adjudicate naming style. An unavailable name on either side
    cannot disconfirm anything, so it passes.
    """
    if not recorded_name or not sec_title:
        return True
    a = normalize_company_for_comparison(recorded_name)
    b = normalize_company_for_comparison(sec_title)
    if not a or not b:
        return True
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= NAME_MATCH_THRESHOLD


def main(dry_run: bool = False) -> dict:
    # MUST be read_universe_csv: this function writes the WHOLE file back, and a
    # bare read maps literal tokens like "NA"/"N/A"/"NULL" to NaN, which a
    # subsequent write would persist as an empty cell -- silently destroying a
    # curated field while "successfully" filling a CIK. See CONVENTIONS in
    # CLAUDE.md, "Universe CSV I/O -- float-safe loader".
    df = read_universe_csv(CSV_PATH)
    if "CIK" not in df.columns or "Ticker" not in df.columns:
        logger.warning("CIK backfill: CSV lacks Ticker/CIK columns; skipping")
        return {"filled": 0, "still_blank": 0, "fetched_ok": False}

    cik_map = fetch_sec_cik_map()
    if not cik_map:
        # Fail loud, change nothing. A blank map must never be read as
        # "no ticker resolves", which would look like a clean run.
        logger.warning("CIK backfill: SEC map unavailable - no rows changed")
        return {"filled": 0, "still_blank": int((df["CIK"].str.strip() == "").sum()),
                "fetched_ok": False}

    norm_index = build_norm_index(cik_map)
    blank = df["CIK"].str.strip() == ""
    blank_before = int(blank.sum())
    filled: list[tuple[str, str, str]] = []
    rejected: list[tuple[str, str, str]] = []
    for idx in df.index[blank]:
        t = str(df.at[idx, "Ticker"]).strip().upper()
        recorded = str(df.at[idx, "Company Name"])
        hit = cik_map.get(t)
        if hit:
            cik, sec_title = hit
        elif _PLAIN_US_SYMBOL.match(t):
            # Separator-insensitive fallback (universe "BRK.B" vs SEC "BRK-B"),
            # US-style symbols only -- see _PLAIN_US_SYMBOL.
            cik = norm_index.get(_norm_symbol(t))
            sec_title = next((title for sym, (c, title) in cik_map.items()
                              if c == cik), "") if cik else ""
        else:
            continue
        if not cik:
            continue
        if not _name_matches(recorded, sec_title):
            # Warn and skip, never write. A ticker match with a company-name
            # disagreement is the recycled-symbol case, and guessing here writes
            # another company's filings into seven downstream projects.
            rejected.append((t, cik, sec_title[:40]))
            continue
        df.at[idx, "CIK"] = cik
        filled.append((t, cik, recorded[:40]))

    if filled and not dry_run:
        backup_csv(CSV_PATH)
        write_universe_csv(df, CSV_PATH)

    # Report the CURRENT state, not the hypothetical post-fill one: in dry-run
    # the whole point is what the file looks like now.
    still_blank = blank_before - (0 if dry_run else len(filled))
    verb = "would fill" if dry_run else "filled"
    logger.info("CIK backfill: %s %s blank CIK(s); %s blank before this step "
                "(most are non-US registrants, but not all - review "
                "periodically)", verb, len(filled), blank_before)
    for t, cik, name in filled:
        logger.info("  NEW CIK %s -> %s (%s) - now visible to CIK-keyed lanes",
                    t, cik, name)
    for t, cik, sec_title in rejected:
        logger.warning("  SKIPPED %s: SEC CIK %s is '%s', which does not match "
                       "the universe company name - possible recycled ticker",
                       t, cik, sec_title)
    return {"filled": len(filled), "still_blank": still_blank,
            "rejected_name_mismatch": len(rejected),
            "fetched_ok": True, "rows": filled}
