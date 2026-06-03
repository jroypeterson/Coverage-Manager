"""Phase A0 (BLOCKING) — date -> fiscal (year, quarter) mapper validation spike.

Per REPORTING_CALENDAR_PLAN.md: prove the mapper reproduces ground-truth fiscal
labels with ZERO mismatches BEFORE any export code is written. No export, no CM
mutation — this only reads free sources and prints a pass/fail table.

Ground truth (all AV-free):
  - AAPL: AlphaVantage's OWN labels from the transcripts cache (gold) — report
    dates 2026-01-29 -> 2026Q1, 2026-04-30 -> 2026Q2.
  - All fixture tickers: Finnhub's EXPLICIT fiscal (year, quarter)+date for the
    next/recent event (free, provider-sourced, spans diverse FYEs).

The mapper is fed a KNOWN-correct FYE month so we isolate mapper-logic
correctness from yfinance-FYE reliability (reported separately as a data-quality
signal for Phase A1).

Run: python experiments/a0_mapper_validation.py
"""
from __future__ import annotations

import datetime
import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]            # Coverage Manager/
TX_ENV = ROOT.parent / "transcripts" / ".env"


def _key(name: str) -> str:
    for envp in (ROOT / ".env", TX_ENV):
        if envp.exists():
            for line in envp.read_text(encoding="utf-8").splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    return ""


FINNHUB = _key("FINNHUB_API_KEY")
NINJAS = _key("API_NINJAS_KEY")

# Well-established fiscal-year-end MONTH per fixture ticker (the "known correct"
# FYE used to isolate mapper logic). COST = Sunday nearest Aug 31 (52/53-week);
# its FYE month is effectively 8/early-9 — included precisely as the hard case.
FIXTURE_FYE = {
    "AAPL": 9,   # last Sat of Sep
    "MSFT": 6,
    "NKE": 5,
    "COST": 9,   # 52/53-week, ~early Sep  (edge case)
    "WMT": 1,    # FYE Jan 31  (Jan-FYE edge case)
    "ABBV": 12,
    "KO": 12,
    "NVO": 12,   # ADR
}


# --------------------------------------------------------------------------- mapper
#
# v1 (estimate period_end from report_date by snapping to calendar-month quarter
# boundaries) FAILED on COST — a 4-4-5 / 16-week-Q4 retail calendar whose quarters
# end in Nov/Feb/May/early-Sep, not on calendar quarter-ends.
#
# v2 = ANCHOR + COUNT (reviewer's suggested approach):
#   1. Anchor on a REAL period-end (yfinance `mostRecentQuarter`) -> its absolute
#      fiscal (year, quarter) via the FYE formula (correct for any calendar,
#      because a real period-end already sits on the true quarter boundary).
#   2. Tie that anchor to its report date (the earliest report on/after the
#      period-end) in the ordered API Ninjas report-date list.
#   3. COUNT backward/forward over the report-date list, rolling year at Q4->Q1.
# No per-report period-end estimation, so 4-4-5 calendars no longer break it.


def period_end_to_fiscal(period_end: datetime.date, fye_month: int) -> tuple[int, int]:
    """Real quarter-end date + FYE month -> fiscal (year, quarter).

    Robust to 52/53-week calendars: a 52/53-week quarter can end a few days into
    the "wrong" calendar month (e.g. KO Q1 ends 2026-04-03, not Mar 31). So we
    SNAP the actual period-end to the nearest *ideal* calendar quarter-end month
    for this FYE before reading off the quarter, instead of trusting the raw
    month. This fixes the KO/PEP/COST 52/53-week off-by-one.
    """
    ideal_months = sorted({((fye_month + 3 * k - 1) % 12) + 1 for k in range(4)})
    best = None
    for yr in (period_end.year - 1, period_end.year, period_end.year + 1):
        for im in ideal_months:
            cand = _month_end(yr, im)
            if best is None or abs((cand - period_end).days) < abs((best - period_end).days):
                best = cand
    pe = best
    fq = ((pe.month - fye_month - 1) % 12) // 3 + 1
    fy = pe.year if pe.month <= fye_month else pe.year + 1
    return fy, fq


def _month_end(year: int, month: int) -> datetime.date:
    nxt = datetime.date(year + (month == 12), (month % 12) + 1, 1)
    return nxt - datetime.timedelta(days=1)


def map_ticker(report_dates: list[str], fye_month: int,
               most_recent_qend: datetime.date) -> dict[str, tuple[int, int, str]]:
    """Label every report date with (fiscal_year, fiscal_quarter, confidence)."""
    dates = sorted(set(report_dates))
    if not dates or most_recent_qend is None:
        return {}
    anchor_fy, anchor_fq = period_end_to_fiscal(most_recent_qend, fye_month)
    # The report for the anchor quarter is the earliest report on/after its end.
    after = [d for d in dates if datetime.date.fromisoformat(d) >= most_recent_qend]
    anchor_date = after[0] if after else dates[-1]
    ai = dates.index(anchor_date)

    labels: dict[str, tuple[int, int, str]] = {}
    # Backward (and the anchor itself).
    y, q = anchor_fy, anchor_fq
    prev = None
    for i in range(ai, -1, -1):
        d = datetime.date.fromisoformat(dates[i])
        conf = "high"
        if prev is not None and (prev - d).days > 140:
            conf = "low"  # gap suggests a missing quarter -> chain unreliable here
        labels[dates[i]] = (y, q, conf)
        prev = d
        q -= 1
        if q < 1:
            q, y = 4, y - 1
    # Forward.
    y, q = anchor_fy, anchor_fq
    q += 1
    if q > 4:
        q, y = 1, y + 1
    prev = datetime.date.fromisoformat(anchor_date)
    for i in range(ai + 1, len(dates)):
        d = datetime.date.fromisoformat(dates[i])
        conf = "high" if (d - prev).days <= 140 else "low"
        labels[dates[i]] = (y, q, conf)
        prev = d
        q += 1
        if q > 4:
            q, y = 1, y + 1
    return labels


def yf_fiscal(ticker: str) -> tuple[int | None, datetime.date | None]:
    """(fye_month, most_recent_quarter_end) from yfinance .info. None on failure."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        fye_ts, mrq_ts = info.get("lastFiscalYearEnd"), info.get("mostRecentQuarter")
        fye_m = datetime.datetime.utcfromtimestamp(fye_ts).month if fye_ts else None
        mrq = datetime.datetime.utcfromtimestamp(mrq_ts).date() if mrq_ts else None
        return fye_m, mrq
    except Exception:
        return None, None


# --------------------------------------------------------------------- ground truth

def finnhub_labels(sym: str) -> list[dict]:
    """Free Finnhub calendar events (date + explicit fiscal year/quarter) in a
    window spanning recent past .. near future."""
    if not FINNHUB:
        return []
    today = datetime.date(2026, 6, 2)
    frm = (today - datetime.timedelta(days=130)).isoformat()
    to = (today + datetime.timedelta(days=210)).isoformat()
    qp = urllib.parse.urlencode({"from": frm, "to": to, "symbol": sym, "token": FINNHUB})
    try:
        with urllib.request.urlopen(f"https://finnhub.io/api/v1/calendar/earnings?{qp}", timeout=20) as r:
            j = json.load(r)
    except Exception:
        return []
    out = []
    for e in j.get("earningsCalendar") or []:
        if e.get("date") and e.get("year") and e.get("quarter"):
            out.append({"date": e["date"], "year": e["year"], "quarter": e["quarter"]})
    return out


def ninjas_dates(sym: str) -> list[str]:
    if not NINJAS:
        return []
    req = urllib.request.Request("https://api.api-ninjas.com/v1/earningscalendar?ticker=" + sym,
                                 headers={"X-Api-Key": NINJAS})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return sorted(e["date"] for e in json.load(r) if e.get("date"))
    except Exception:
        return []


# AAPL gold labels from AlphaVantage's own keys in the transcripts cache.
AV_GOLD = {"AAPL": [("2026-01-29", 2026, 1), ("2026-04-30", 2026, 2)]}


def main() -> None:
    print("PHASE A0 v2 (anchor+count) — date -> fiscal (year, quarter) validation")
    print(f"Finnhub key: {bool(FINNHUB)} · API Ninjas key: {bool(NINJAS)}")
    print("Anchor: yfinance mostRecentQuarter (real period-end). "
          "Validation: Finnhub (independent) + AAPL AV-cache (gold).\n")

    total = passed = 0
    rows = []
    fye_notes = []
    for tk, fye in FIXTURE_FYE.items():
        nd = ninjas_dates(tk)
        yf_fye, mrq = yf_fiscal(tk)
        if yf_fye is not None and yf_fye != fye:
            fye_notes.append(f"{tk}: known FYE month={fye}, yfinance says {yf_fye}")
        if mrq is None:
            rows.append((tk, fye, "(no yfinance mostRecentQuarter)", "", "", "**SKIP**", "", ""))
            continue
        labels = map_ticker(nd, fye, mrq)  # anchored on the REAL period-end

        # Ground truth = AAPL AV-cache (gold, independent) + Finnhub (independent provider).
        truth: list[tuple[str, int, int, str]] = [(d, y, q, "AV-cache") for d, y, q in AV_GOLD.get(tk, [])]
        for ev in finnhub_labels(tk):
            if not any(t[0] == ev["date"] for t in truth):
                truth.append((ev["date"], ev["year"], ev["quarter"], "Finnhub"))

        for d, exp_y, exp_q, src in sorted(truth):
            got = labels.get(d)
            if got is None:
                # Finnhub's *future* event isn't in API Ninjas history; extrapolate
                # one step beyond the latest labelled report for the check.
                got = _extrapolate(labels, d)
            if got is None:
                rows.append((tk, fye, d, f"{exp_y}Q{exp_q}", "n/a", "**MISS**", "", src))
                total += 1
                continue
            fy, fq, conf = got
            ok = (fy == exp_y and fq == exp_q)
            total += 1
            passed += ok
            rows.append((tk, fye, d, f"{exp_y}Q{exp_q}", f"{fy}Q{fq}",
                         "PASS" if ok else "**FAIL**", conf, src))

    print(f"{'TKR':<5}{'FYE':>3} {'REPORT DATE':<12}{'TRUTH':<8}{'MAPPED':<8}{'RESULT':<9}{'CONF':<6}{'SRC'}")
    print("-" * 70)
    for r in rows:
        print(f"{r[0]:<5}{r[1]:>3} {r[2]:<12}{r[3]:<8}{r[4]:<8}{r[5]:<9}{r[6]:<6}{r[7]}")

    print("\nMONOTONICITY over API Ninjas history (anchor+count, last 8 each):")
    mono_fail = 0
    for tk, fye in FIXTURE_FYE.items():
        _, mrq = yf_fiscal(tk)
        if mrq is None:
            print(f"  {tk:<5} (no yfinance anchor)"); continue
        labels = map_ticker(ninjas_dates(tk), fye, mrq)
        seq_dates = sorted(labels)[-8:]
        seq = [(labels[d][0], labels[d][1]) for d in seq_dates]
        keys = [y * 4 + q for (y, q) in seq]
        ok = all(keys[i] < keys[i + 1] for i in range(len(keys) - 1))
        mono_fail += (not ok and len(keys) > 1)
        print(f"  {tk:<5} {'OK ' if ok else 'BAD'}  " + " ".join(f"{y}Q{q}" for y, q in seq))

    if fye_notes:
        print("\nyfinance FYE vs known (A1 data-quality signal):")
        for n in fye_notes:
            print("  " + n)

    print(f"\n=== A0 RESULT: {passed}/{total} ground-truth labels correct · "
          f"monotonicity failures: {mono_fail} ===")
    print("GATE:", "PASS — proceed to A1" if passed == total and mono_fail == 0
          else "FAIL — fix mapper before any export code")


def _extrapolate(labels: dict[str, tuple[int, int, str]], future_date: str):
    """Label a future (not-yet-in-history) date by stepping one quarter past the
    latest labelled report. Used only to check Finnhub's *next* event."""
    if not labels:
        return None
    last = max(labels)
    if future_date <= last:
        return None
    y, q, _ = labels[last]
    q += 1
    if q > 4:
        q, y = 1, y + 1
    return (y, q, "high")


if __name__ == "__main__":
    main()
