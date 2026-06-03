"""Phase A0.1 (BLOCKING) — validate the PRODUCTION mapper against INDEPENDENT
SEC ground truth (not Finnhub-vs-Finnhub).

Reviewer's hold condition: A0 validated mostly against Finnhub, which is also the
proposed anchor — partly circular. A0.1 gets authoritative fiscal labels from SEC
XBRL company-facts `(end, fy, fp)` for the hard fixtures and checks whether the
production algorithm

    Finnhub explicit fiscal (year,quarter)  =anchor=>  count over API Ninjas dates

reproduces SEC's labels with ZERO mismatches. No AlphaVantage calls.

SEC fp convention: Q1/Q2/Q3 from 10-Q; FY (annual 10-K) -> fiscal quarter 4.
Foreign/ADR filers (20-F/IFRS, e.g. NVO) have no us-gaap companyconcept -> they
can't be SEC-validated here; reported as 'no-SEC' (a documented coverage gap).

Run: python experiments/a0_1_sec_validation.py
"""
from __future__ import annotations

import datetime
import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TX_ENV = ROOT.parent / "transcripts" / ".env"
UA = "Jason Peterson jroypeterson@gmail.com"   # SEC requires a UA
FIXTURES = ["AAPL", "MSFT", "NKE", "COST", "WMT", "KO", "NVO"]
CONCEPTS = ["EarningsPerShareDiluted", "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax", "NetIncomeLoss"]


def _key(name: str) -> str:
    for envp in (ROOT / ".env", TX_ENV):
        if envp.exists():
            for line in envp.read_text(encoding="utf-8").splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    return ""


FINNHUB = _key("FINNHUB_API_KEY")
NINJAS = _key("API_NINJAS_KEY")


def _get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# ----------------------------------------------------------------- SEC ground truth

def cik_map() -> dict[str, str]:
    j = _get("https://www.sec.gov/files/company_tickers.json")
    out = {}
    for row in j.values():
        out[row["ticker"].upper()] = str(row["cik_str"]).zfill(10)
    return out


def sec_truth(ticker: str, cik: str) -> dict[str, tuple[int, int]]:
    """Return {period_end_iso: (fiscal_year, fiscal_quarter)} from SEC XBRL.

    The hard part (A0.1 v1 got this wrong): companyconcept re-reports each period
    many times — as prior-year COMPARATIVES in later filings, carrying THAT
    filing's fy/fp, not the period's own. To get the as-originally-reported fiscal
    label we (a) keep only true ~13-week quarter durations (excludes YTD and annual
    FY rows that share an end date), (b) restrict to fp in Q1/Q2/Q3, and (c) dedupe
    each period-end to its EARLIEST `filed` row (the original 10-Q, not a later
    comparative). Q4/annual is intentionally out of scope for this validation.
    """
    for concept in CONCEPTS:
        url = (f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}"
               f"/us-gaap/{concept}.json")
        try:
            j = _get(url)
        except Exception:
            continue
        # candidate[end] = (fy, q, filed)  — keep earliest filed
        cand: dict[str, tuple[int, int, str]] = {}
        for unit_rows in j.get("units", {}).values():
            for r in unit_rows:
                fp, fy, end, start = r.get("fp"), r.get("fy"), r.get("end"), r.get("start")
                form, filed = r.get("form"), r.get("filed")
                if not (fp and fy and end and start and filed):
                    continue
                if form not in ("10-Q", "10-Q/A"):
                    continue
                q = {"Q1": 1, "Q2": 2, "Q3": 3}.get(fp)
                if q is None:
                    continue
                dur = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
                if not (80 <= dur <= 100):       # true single quarter only
                    continue
                if end not in cand or filed < cand[end][2]:
                    cand[end] = (int(fy), q, filed)
        if cand:
            return {end: (fy, q) for end, (fy, q, _f) in cand.items()}
    return {}


# ------------------------------------------------------------ free provider sources

def finnhub_anchor(sym: str) -> dict | None:
    if not FINNHUB:
        return None
    today = datetime.date(2026, 6, 2)
    frm = (today - datetime.timedelta(days=130)).isoformat()
    to = (today + datetime.timedelta(days=210)).isoformat()
    qp = urllib.parse.urlencode({"from": frm, "to": to, "symbol": sym, "token": FINNHUB})
    try:
        j = _get(f"https://finnhub.io/api/v1/calendar/earnings?{qp}", headers={})
    except Exception:
        return None
    evs = [e for e in (j.get("earningsCalendar") or [])
           if e.get("date") and e.get("year") and e.get("quarter")]
    if not evs:
        return None
    # Prefer the most recent PAST event (closest anchor); else the soonest future.
    today_iso = today.isoformat()
    past = sorted([e for e in evs if e["date"] <= today_iso], key=lambda e: e["date"])
    fut = sorted([e for e in evs if e["date"] > today_iso], key=lambda e: e["date"])
    return past[-1] if past else fut[0]


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


# --------------------------------------------------- production mapper: anchor+count

def map_finnhub_anchored(sym: str) -> dict[str, tuple[int, int]]:
    """{report_date: (fy, q)} via Finnhub fiscal anchor + count over API Ninjas."""
    anchor = finnhub_anchor(sym)
    if not anchor:
        return {}
    dates = sorted(set(ninjas_dates(sym)) | {anchor["date"]})
    if anchor["date"] not in dates:
        return {}
    ai = dates.index(anchor["date"])
    labels: dict[str, tuple[int, int]] = {}
    y, q = anchor["year"], anchor["quarter"]
    for i in range(ai, -1, -1):                 # backward incl. anchor
        labels[dates[i]] = (y, q)
        q -= 1
        if q < 1:
            q, y = 4, y - 1
    y, q = anchor["year"], anchor["quarter"]
    q += 1
    if q > 4:
        q, y = 1, y + 1
    for i in range(ai + 1, len(dates)):         # forward
        labels[dates[i]] = (y, q)
        q += 1
        if q > 4:
            q, y = 1, y + 1
    return labels


def main() -> None:
    print("PHASE A0.1 — production mapper (Finnhub anchor + count) vs INDEPENDENT SEC truth")
    print(f"Finnhub:{bool(FINNHUB)} Ninjas:{bool(NINJAS)}\n")
    cm = cik_map()
    total = passed = 0
    detail_fail = []
    for tk in FIXTURES:
        cik = cm.get(tk)
        truth = sec_truth(tk, cik) if cik else {}
        mapped = map_finnhub_anchored(tk)
        if not truth:
            print(f"{tk:5} no-SEC (foreign/IFRS or concept miss) — anchor={('y' if mapped else 'n')}")
            continue
        if not mapped:
            print(f"{tk:5} no Finnhub anchor / API Ninjas — cannot map")
            continue
        # Match each report_date to the SEC period_end most recent BEFORE it (<=95d).
        truth_ends = sorted(truth)
        tk_total = tk_pass = 0
        misses = []
        for rd in sorted(mapped):
            rdate = datetime.date.fromisoformat(rd)
            cand = [e for e in truth_ends
                    if 0 <= (rdate - datetime.date.fromisoformat(e)).days <= 95]
            if not cand:
                continue
            pe = cand[-1]
            exp = truth[pe]
            got = mapped[rd]
            tk_total += 1
            total += 1
            if got == exp:
                tk_pass += 1
                passed += 1
            else:
                misses.append(f"{rd} (end {pe}): got {got[0]}Q{got[1]} vs SEC {exp[0]}Q{exp[1]}")
        flag = "PASS" if tk_pass == tk_total and tk_total else ("FAIL" if tk_total else "n/a")
        print(f"{tk:5} {tk_pass}/{tk_total} {flag}")
        detail_fail += [f"   {tk}: {m}" for m in misses]

    if detail_fail:
        print("\nMISMATCHES:")
        print("\n".join(detail_fail))
    print(f"\n=== A0.1 RESULT: {passed}/{total} report dates match INDEPENDENT SEC labels ===")
    print("GATE:", "PASS — A1 is a go" if total and passed == total
          else "FAIL/INCOMPLETE — resolve before A1")


if __name__ == "__main__":
    main()
