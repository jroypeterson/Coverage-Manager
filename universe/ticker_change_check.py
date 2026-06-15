"""Discover ticker changes (renames) and SEC deregistrations in the universe.

Companion to `delisted_check.py`. Where `delisted_check` answers *"is this
ticker dead?"* (via a yfinance price-recency probe), this module answers the
question that one can't: *"and what symbol does SEC now have for this company?"*
— so a covered name whose ticker changed can be **remapped** to the new symbol
rather than just removed.

Discovery path — SEC EDGAR's `company_tickers.json` (the same bulk file
`enrich.py` uses). A company's **CIK is stable across a ticker change**; only the
symbol moves. So:

  * Build the reverse map  CIK -> {current ticker(s), company title}.
  * For each universe row carrying a CIK, look up that CIK.
  * SEC's ticker for the CIK differs from the universe ticker -> a **mismatch**
    (candidate ticker change). Report old + SEC's symbol(s) + SEC title.
  * CIK absent from the file entirely -> likely **deregistered**.

Why a review list, not an auto-applied fix: SEC's structured ticker data can
*lag* a real-world rebrand (it still lists the retired `FISV` long after Fiserv
moved to `FI`, on BOTH the bulk file and the per-CIK submissions endpoint), and
yfinance can't disambiguate either (Yahoo aliases the retired symbol to the live
one). There is no automated authority that reliably says which symbol is current
— so the check **surfaces the mismatch with full context and lets a human decide
direction** (a glance at the SEC title + former-names settles it). For the handful
of mismatches a week, that's fast and 100% accurate; auto-classification on an
unreliable signal would just manufacture false confidence.

A best-effort `formerNames` lookup (SEC per-CIK submissions, only for the few
mismatch candidates) flags entities that legally renamed — a strong "this is a
real change" signal (e.g. GALAPAGOS NV -> Lakefront Biotherapeutics, GLPG ->
LKFT). Empty former-names with a matching title leans toward SEC-file lag.

Scope / precision:
  * Only rows with a non-blank CIK are considered.
  * Mismatch detection is gated to **plain US-style symbols** (`ABT`, `BRK.B`) so
    a cross-listed row tracking the foreign line (e.g. `DIA.MI`) isn't flagged as
    "changed to the US ADR."

Non-gating: writes a report, never raises, never blocks downstream.

Output:
  - `reports/ticker_change_check_{date}.csv` — flagged rows
  - `reports/ticker_change_check_{date}.md`  — human-readable summary
"""

import csv
import re
from datetime import date

import pandas as pd
import requests

from cache import cache_get, cache_set
from config import API_KEYS, CSV_PATH, REPORTS_DIR
from logging_utils import get_logger, log_exception

logger = get_logger("ticker_change_check")

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# SEC requires a descriptive User-Agent. Reuse the workspace EDGAR identity from
# .env (same key reporting_calendar.py uses) with a safe fallback.
_EDGAR_UA = API_KEYS.get("EDGAR_IDENTITY") or "Coverage Manager jroypeterson@gmail.com"

# The SEC bulk ticker file changes slowly; a 24h cache keeps the weekly run from
# re-pulling ~1 MB and is fresh enough for a weekly cadence.
SEC_CACHE_NS = "sec_company_tickers"
SEC_CACHE_KEY = "all"
SEC_CACHE_TTL_HOURS = 24.0

# A "plain" US-style symbol: 1-5 letters with an optional single-letter share
# class (BRK.B / BF-B). Excludes foreign-suffixed tickers (DIA.MI, ROG.SW,
# 000100.KS) whose universe row tracks a non-US line.
_PLAIN_US_SYMBOL = re.compile(r"^[A-Z]{1,5}([.\-/][A-Z])?$")

# Strip share-class / exchange separators for symbol equality so "BRK.B" matches
# SEC's "BRK-B".
_SEP = re.compile(r"[.\-/ ]")


def _norm_symbol(t):
    return _SEP.sub("", str(t or "").strip().upper())


def load_sec_cik_map(use_cache=True):
    """Return (cik_map, fetched_ok).

    `cik_map`: {cik_int: {"tickers": [sym, ...], "title": str}} from SEC's
    `company_tickers.json` (a CIK can carry several tickers — share classes).
    `fetched_ok`: False when the download failed AND no cache was available, so
    the caller can say "couldn't check" rather than a misleading "0 changes".
    """
    if use_cache:
        cached = cache_get(SEC_CACHE_NS, SEC_CACHE_KEY, SEC_CACHE_TTL_HOURS)
        if cached is not None:
            return {int(k): v for k, v in cached.items()}, True

    try:
        resp = requests.get(SEC_TICKERS_URL, headers={"User-Agent": _EDGAR_UA}, timeout=20)
        if resp.status_code != 200:
            logger.warning("SEC company_tickers.json HTTP %s", resp.status_code)
            return {}, False
        data = resp.json()
    except Exception as e:
        log_exception(logger, "SEC company_tickers.json fetch failed", e)
        return {}, False

    cik_map = {}
    for entry in data.values():
        cik = entry.get("cik_str")
        ticker = str(entry.get("ticker", "")).strip().upper()
        title = str(entry.get("title", "")).strip()
        if cik is None or not ticker:
            continue
        cik = int(cik)
        slot = cik_map.setdefault(cik, {"tickers": [], "title": title})
        if ticker not in slot["tickers"]:
            slot["tickers"].append(ticker)
        if not slot["title"] and title:
            slot["title"] = title

    if use_cache and cik_map:
        cache_set(SEC_CACHE_NS, SEC_CACHE_KEY, {str(k): v for k, v in cik_map.items()})
    logger.info("SEC: loaded ticker map for %d CIKs", len(cik_map))
    return cik_map, True


def _default_former_names(cik):
    """Best-effort SEC per-CIK submissions lookup -> list of former company names.

    Authoritative signal that an entity legally renamed (a strong "real ticker
    change" tell). Only called for the few mismatch candidates, so the extra SEC
    calls are negligible. Returns [] on any error (best-effort, never raises).
    """
    try:
        url = SEC_SUBMISSIONS_URL.format(cik=int(cik))
        resp = requests.get(url, headers={"User-Agent": _EDGAR_UA}, timeout=20)
        if resp.status_code != 200:
            return []
        former = resp.json().get("formerNames", []) or []
        return [str(f.get("name", "")).strip() for f in former if f.get("name")]
    except Exception:
        return []


def _coerce_cik(raw):
    """Universe CIK -> int, or None if blank/non-numeric."""
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.split(".")[0]  # tolerate "1800.0" (pandas float coercion)
    try:
        return int(s)
    except ValueError:
        return None


def check_ticker_changes(csv_path=None, use_cache=True, former_names_fetcher=None):
    """Scan the universe for ticker mismatches (candidate renames) + SEC
    deregistrations.

    `former_names_fetcher`: injectable callable(cik)->[names] for the best-effort
    reorg signal; defaults to a SEC-submissions lookup. Pass a stub in tests.

    Returns dict:
      - checked:        rows with a CIK that were examined
      - sec_cik_count:  size of the SEC CIK map
      - sec_fetched_ok: whether SEC data was available (False => unreliable)
      - changes:        mismatch candidates for human review — each
                        {ticker, sec_tickers, cik, recorded_name, sec_title,
                         former_names, entity_renamed (bool), sector_jp, subsector_jp}
      - deregistered:   list of {ticker, cik, recorded_name, sector_jp, subsector_jp}
    """
    csv_path = csv_path or CSV_PATH
    df = pd.read_csv(csv_path, dtype=str).fillna("")

    cik_map, fetched_ok = load_sec_cik_map(use_cache=use_cache)
    if not fetched_ok:
        return {
            "checked": 0, "sec_cik_count": 0, "sec_fetched_ok": False,
            "changes": [], "deregistered": [],
        }

    fetch_former = former_names_fetcher or _default_former_names
    changes, deregistered = [], []
    checked = 0
    for row in df.to_dict(orient="records"):
        cik = _coerce_cik(row.get("CIK"))
        if cik is None:
            continue
        ticker = str(row.get("Ticker", "")).strip()
        if not ticker:
            continue
        checked += 1

        entry = cik_map.get(cik)
        if entry is None:
            # SEC has no current ticker for this CIK -> likely deregistered. A
            # very recent listing not yet indexed can also land here, so the
            # report frames it as "verify".
            deregistered.append({
                "ticker": ticker, "cik": cik,
                "recorded_name": row.get("Company Name", ""),
                "sector_jp": row.get("Sector (JP)", ""),
                "subsector_jp": row.get("Subsector (JP)", ""),
            })
            continue

        # Mismatch detection only for plain US-style symbols (see docstring).
        if not _PLAIN_US_SYMBOL.match(ticker.upper()):
            continue
        sec_norm = {_norm_symbol(t) for t in entry["tickers"]}
        if _norm_symbol(ticker) not in sec_norm:
            former = fetch_former(cik)
            changes.append({
                "ticker": ticker,
                "sec_tickers": ", ".join(entry["tickers"]),
                "cik": cik,
                "recorded_name": row.get("Company Name", ""),
                "sec_title": entry.get("title", ""),
                "former_names": "; ".join(former),
                "entity_renamed": bool(former),
                "sector_jp": row.get("Sector (JP)", ""),
                "subsector_jp": row.get("Subsector (JP)", ""),
            })

    changes.sort(key=lambda r: r["ticker"])
    deregistered.sort(key=lambda r: r["ticker"])
    return {
        "checked": checked,
        "sec_cik_count": len(cik_map),
        "sec_fetched_ok": True,
        "changes": changes,
        "deregistered": deregistered,
    }


def write_report(result, reports_dir=None, run_date=None):
    """Write CSV + markdown reports. Returns {csv_path, md_path}."""
    reports_dir = reports_dir or REPORTS_DIR
    run_date = run_date or date.today().strftime("%Y-%m-%d")
    reports_dir.mkdir(parents=True, exist_ok=True)

    csv_path = reports_dir / f"ticker_change_check_{run_date}.csv"
    md_path = reports_dir / f"ticker_change_check_{run_date}.md"

    fieldnames = ["type", "ticker", "sec_tickers", "cik", "recorded_name",
                  "sec_title", "former_names", "entity_renamed",
                  "sector_jp", "subsector_jp"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in result["changes"]:
            writer.writerow({"type": "change", **{k: r.get(k, "") for k in fieldnames if k != "type"}})
        for r in result["deregistered"]:
            writer.writerow({"type": "deregistered", **{k: r.get(k, "") for k in fieldnames if k != "type"}})

    lines = [f"# Ticker-change / deregistration check — {run_date}", ""]
    if not result["sec_fetched_ok"]:
        lines.append(":x: **SEC `company_tickers.json` unavailable this run — "
                     "no ticker-change check performed.** Re-run when SEC is reachable.")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"csv_path": str(csv_path), "md_path": str(md_path)}

    lines.append(f"- Checked: {result['checked']} universe rows with a CIK "
                 f"(against {result['sec_cik_count']} SEC CIKs)")
    lines.append(f"- Ticker mismatches (candidate changes — review): {len(result['changes'])}")
    lines.append(f"- Deregistered (CIK absent from SEC ticker file): {len(result['deregistered'])}")
    lines.append("")

    if result["changes"]:
        lines.append("## :arrows_counterclockwise: Ticker mismatches — review & remap")
        lines.append("")
        lines.append("_SEC lists a different symbol than the universe for the same CIK. **Check direction "
                     "before changing anything:** SEC's structured data can lag a rebrand (it still shows the "
                     "retired symbol while your ticker is current, e.g. `FISV` vs your `FI` — leave as-is), so "
                     "a mismatch is not automatically a stale row. A non-empty **Former Names** is a strong "
                     "tell of a real entity rename (remap then). When remapping, update `Ticker` (and "
                     "identifiers) on the row in `data/coverage_universe_tickers.csv` to SEC's symbol._")
        lines.append("")
        lines.append("| Your Ticker | SEC Symbol(s) | CIK | Recorded Name | SEC Title | Former Names | Sector |")
        lines.append("|-------------|---------------|-----|---------------|-----------|--------------|--------|")
        for r in result["changes"]:
            former = r.get("former_names") or "—"
            lines.append(
                f"| {r['ticker']} | **{r['sec_tickers']}** | {r['cik']} | "
                f"{r['recorded_name']} | {r['sec_title']} | {former} | {r['sector_jp']} |"
            )
        lines.append("")

    if result["deregistered"]:
        lines.append("## :warning: Possible deregistrations — verify (CIK no longer in SEC ticker file)")
        lines.append("")
        lines.append("_A very recently-listed name not yet indexed by SEC can also appear here — "
                     "cross-check `delisted_check` (a name flagged by both is high-confidence gone)._")
        lines.append("")
        lines.append("| Ticker | CIK | Recorded Name | Sector |")
        lines.append("|--------|-----|---------------|--------|")
        for r in result["deregistered"]:
            lines.append(f"| {r['ticker']} | {r['cik']} | {r['recorded_name']} | {r['sector_jp']} |")
        lines.append("")

    if not result["changes"] and not result["deregistered"]:
        lines.append("_No ticker mismatches or deregistrations — every CIK's universe "
                     "ticker matches SEC's current symbol._")
        lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"csv_path": str(csv_path), "md_path": str(md_path)}


def main(use_cache=True):
    """CLI entry point: run the check and write the report."""
    result = check_ticker_changes(use_cache=use_cache)
    paths = write_report(result)
    if not result["sec_fetched_ok"]:
        logger.warning("Ticker-change check: SEC data unavailable — no check performed.")
        return result
    logger.info(
        "Ticker-change check: %d mismatch(es), %d deregistered (of %d CIK rows)",
        len(result["changes"]), len(result["deregistered"]), result["checked"],
    )
    for r in result["changes"]:
        tell = " [entity renamed]" if r["entity_renamed"] else ""
        logger.warning("  MISMATCH %s vs SEC %s (CIK %s, %s)%s",
                       r["ticker"], r["sec_tickers"], r["cik"], r["sec_title"], tell)
    logger.info("  CSV: %s", paths["csv_path"])
    logger.info("  MD:  %s", paths["md_path"])
    return result
