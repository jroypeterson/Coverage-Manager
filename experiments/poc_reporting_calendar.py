"""POC: reporting-calendar enrichment (fiscal-vs-calendar breakdown).

Prototype for a future Coverage Manager enrichment that publishes per-ticker
reporting conventions (fiscal-year-end, calendar-aligned flag, cadence, typical
report lag) as a shared export, consumed by transcripts (fetch-gating + call_date)
and earnings_agent (date-verification anchor).

Sources:
  - API Ninjas earnings calendar (FREE): per-quarter report DATE + actual EPS/rev,
    ~50 quarters back to ~2014, covers US names + ADRs. The report-date source.
  - yfinance .info: authoritative fiscal-year-end (the calendar offset).

Insight: report-date cadence ALONE does not reveal fiscal-year-end — AAPL (Sep FYE)
and ABBV (Dec FYE) report in the same months. The FYE signal is required to know
which fiscal quarter a given call belongs to.

Run: set API_NINJAS_KEY (or it reads transcripts/.env), then
  python experiments/poc_reporting_calendar.py
This is a parked prototype — NOT wired into the weekly CM build.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]  # Coverage Manager/
MON = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _api_ninjas_key() -> str:
    if os.environ.get("API_NINJAS_KEY"):
        return os.environ["API_NINJAS_KEY"]
    for envp in (ROOT / ".env", ROOT.parent / "transcripts" / ".env"):
        if envp.exists():
            for line in envp.read_text(encoding="utf-8").splitlines():
                if line.startswith("API_NINJAS_KEY="):
                    return line.split("=", 1)[1].strip()
    return ""


KEY = _api_ninjas_key()
H = {"X-Api-Key": KEY}


def ninjas_report_dates(tk: str) -> list[str]:
    try:
        r = requests.get("https://api.api-ninjas.com/v1/earningscalendar",
                         headers=H, params={"ticker": tk}, timeout=30)
        if r.status_code != 200:
            return []
        return sorted(e["date"] for e in r.json() if e.get("date"))
    except Exception:
        return []


def yf_fiscal(tk: str):
    """Return (fiscal_year_end_date, most_recent_quarter_end) or (None, None)."""
    try:
        import yfinance as yf
        info = yf.Ticker(tk).info
        ts, mq = info.get("lastFiscalYearEnd"), info.get("mostRecentQuarter")
        fye = datetime.datetime.utcfromtimestamp(ts).date() if ts else None
        mrq = datetime.datetime.utcfromtimestamp(mq).date() if mq else None
        return fye, mrq
    except Exception:
        return None, None


def fiscal_q1_window(fye_month: int) -> str:
    """Fiscal Q1 covers the 3 months after fiscal-year end."""
    s = fye_month % 12 + 1
    e = (fye_month + 2) % 12 + 1
    return f"{MON[s]}-{MON[e]}"


def load_portfolio() -> list[str]:
    p = ROOT / "exports" / "portfolio.json"
    return sorted(json.loads(p.read_text(encoding="utf-8")).keys())


def main() -> None:
    if not KEY:
        print("No API_NINJAS_KEY found (env or transcripts/.env). Aborting.")
        return
    pf = load_portfolio()
    rows = []
    for tk in pf:
        dates = ninjas_report_dates(tk)
        fye, mrq = yf_fiscal(tk)
        last_report = dates[-1] if dates else "-"
        rmonths = sorted({int(d[5:7]) for d in dates[-8:]}) if dates else []
        cadence = "/".join(MON[m] for m in rmonths) if rmonths else "-"
        lag = "-"
        if mrq and dates:
            after = [datetime.date.fromisoformat(d) for d in dates
                     if datetime.date.fromisoformat(d) >= mrq]
            if after:
                lag = f"{(min(after) - mrq).days}d"
        if fye:
            cal = "calendar" if fye.month == 12 else f"FYE {MON[fye.month]}"
        else:
            cal = "?"
        rows.append({
            "tk": tk, "fye": (f"{MON[fye.month]} {fye.day}" if fye else "?"),
            "cal": cal, "last": last_report, "n": len(dates), "cadence": cadence,
            "lag": lag, "q1": (fiscal_q1_window(fye.month) if fye else "-"),
            "fye_month": fye.month if fye else 13,
        })

    print(f"\nFISCAL vs CALENDAR -- {len(pf)} Portfolio names  (API Ninjas dates + yfinance FYE)\n")
    h = (f"{'TICK':<6}{'FISCAL YR END':<14}{'CLASS':<11}{'FISCAL Q1':<11}"
         f"{'REPORTS IN':<22}{'LAST RPT':<12}{'#':>3}{'LAG':>6}")
    print(h); print("-" * len(h))
    for r in sorted(rows, key=lambda r: (r["fye_month"], r["tk"])):
        print(f"{r['tk']:<6}{r['fye']:<14}{r['cal']:<11}{r['q1']:<11}"
              f"{r['cadence']:<22}{r['last']:<12}{r['n']:>3}{r['lag']:>6}")

    cal_n = sum(1 for r in rows if r["cal"] == "calendar")
    off = [r for r in rows if r["cal"] not in ("calendar", "?")]
    unk = [r for r in rows if r["cal"] == "?"]
    print(f"\nSUMMARY: {cal_n}/{len(pf)} calendar-fiscal (Dec year-end).")
    if off:
        print("Off-calendar fiscal years:")
        for r in sorted(off, key=lambda r: r["fye_month"]):
            print(f"  {r['tk']:<6} FYE {r['fye']:<10} -> fiscal Q1 = {r['q1']}")
    if unk:
        print("No FYE from yfinance: " + ", ".join(r["tk"] for r in unk))


if __name__ == "__main__":
    main()
