"""Reporting-calendar enrichment — per-ticker fiscal-period → report-date map.

Publishes `exports/reporting_calendar.json`: for each ticker, the recent fiscal
`(year, quarter)` → report-date history plus the next expected report, so consumers
(transcripts fetch-gating, earnings_agent date verification) can answer
"has TICKER reported fiscal YYYYQ#?" offline.

DESIGN (locked v4 — see `REPORTING_CALENDAR_PLAN.md`). Three free load-bearing
sources + one cross-check:
  - SEC XBRL `companyconcept`  — US-filer fiscal-label AUTHORITY (period_end→fy/fp).
  - Finnhub `/calendar/earnings` — explicit fiscal anchor + announce/scheduled date.
  - API Ninjas earningscalendar  — the report-date history the count walks.
  - yfinance — cross-check only, NEVER in the labeling path.

`gating_eligible` (the consumer contract): for a US filer a quarter is `true` only
when the **SEC fiscal label and the Finnhub-anchored count agree** and the row is
otherwise clean. Non-US/ADR/foreign (no SEC us-gaap facts) and Q4 (10-K `fp=FY`,
not a 10-Q) default `false`. Consumers gate ONLY on `gating_eligible == true`;
anything else falls through to a normal fetch (zero-false-skip contract).

Network is isolated in the `fetch_*` helpers; the pure `build_record` /
`anchor_count` / `dedupe_sec_quarter_labels` functions take plain data so tests run
offline against frozen fixtures.
"""
from __future__ import annotations

import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from config import API_KEYS, CACHE_DIR
from logging_utils import get_logger

logger = get_logger("universe.reporting_calendar")

# ── constants ────────────────────────────────────────────────────────────────
SEC_BASE = "https://data.sec.gov"
FINNHUB_BASE = "https://finnhub.io/api/v1"
NINJAS_URL = "https://api.api-ninjas.com/v1/earningscalendar"
# Duration concepts reliably tagged across filers; first that returns wins.
SEC_CONCEPTS = [
    "EarningsPerShareDiluted", "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax", "NetIncomeLoss",
]
CACHE_DIR_RC = CACHE_DIR / "reporting_calendar"
CACHE_TTL_DAYS = 30
RECENT_QUARTERS_KEPT = 12
REPORT_LAG_MAX_DAYS = 95        # report lands within this many days after period-end
GAP_BREAK_DAYS = 140            # gap between consecutive reports that breaks the count
FINNHUB_FRESH_DAYS = 100        # next_expected must be within ~a quarter to be trusted
_EDGAR_UA = API_KEYS.get("EDGAR_IDENTITY") or "Coverage Manager jroypeterson@gmail.com"


# ── HTTP helpers ─────────────────────────────────────────────────────────────
def _get_json(url: str, headers: dict, timeout: int = 30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_cik_map() -> dict[str, str]:
    """{TICKER: zero-padded CIK} from SEC. Cached weekly under the RC cache dir."""
    cache = CACHE_DIR_RC / "_cik_map.json"
    if cache.exists() and _age_days(cache) < 7:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        j = _get_json(f"{SEC_BASE.replace('data.', 'www.')}/files/company_tickers.json",
                      {"User-Agent": _EDGAR_UA})
    except Exception as e:
        logger.warning("CIK map fetch failed: %s", e)
        return {}
    out = {str(r["ticker"]).upper(): str(r["cik_str"]).zfill(10) for r in j.values()}
    CACHE_DIR_RC.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def fetch_sec_labels(cik: str) -> dict[str, list]:
    """{period_end_iso: [fy, quarter]} from SEC XBRL for a US (us-gaap) filer.

    The dedupe (keep true ~13-week quarter durations, earliest `filed` per
    period-end, Q1–Q3 only) strips the prior-year COMPARATIVE re-reports that
    otherwise carry the wrong filing's fy/fp. Q4/annual is intentionally excluded
    (10-K `fp=FY`), so Q4 rows downstream get no SEC label → `gating_eligible=false`.
    """
    for concept in SEC_CONCEPTS:
        url = f"{SEC_BASE}/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
        try:
            j = _get_json(url, {"User-Agent": _EDGAR_UA})
        except urllib.error.HTTPError:
            continue
        except Exception as e:
            logger.debug("SEC %s/%s error: %s", cik, concept, e)
            continue
        labels = dedupe_sec_quarter_labels(j.get("units", {}))
        if labels:
            return labels
    return {}


def fetch_finnhub_events(symbol: str, today: datetime.date) -> list[dict]:
    token = API_KEYS.get("FINNHUB_API_KEY")
    if not token:
        return []
    frm = (today - datetime.timedelta(days=140)).isoformat()
    to = (today + datetime.timedelta(days=210)).isoformat()
    qp = urllib.parse.urlencode({"from": frm, "to": to, "symbol": symbol, "token": token})
    try:
        j = _get_json(f"{FINNHUB_BASE}/calendar/earnings?{qp}", {})
    except Exception:
        return []
    return [{"date": e["date"], "year": e["year"], "quarter": e["quarter"]}
            for e in (j.get("earningsCalendar") or [])
            if e.get("date") and e.get("year") and e.get("quarter")]


def fetch_ninjas_rows(symbol: str) -> list[dict]:
    key = API_KEYS.get("API_NINJAS_KEY")
    if not key:
        return []
    req = urllib.request.Request(f"{NINJAS_URL}?ticker={urllib.parse.quote(symbol)}",
                                 headers={"X-Api-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.load(r)
    except Exception:
        return []
    rows = []
    for e in data:
        if e.get("date"):
            eps = e.get("actual_eps")
            rows.append({"date": e["date"], "eps_actual": eps if isinstance(eps, (int, float)) else None})
    rows.sort(key=lambda x: x["date"])
    return rows


# ── pure logic ───────────────────────────────────────────────────────────────
def dedupe_sec_quarter_labels(units: dict) -> dict[str, list]:
    """SEC companyconcept `units` → {period_end: [fy, quarter]} (Q1–Q3, original filing)."""
    cand: dict[str, tuple[int, int, str]] = {}  # end -> (fy, q, filed)
    for rows in units.values():
        for r in rows:
            fp, fy, end, start = r.get("fp"), r.get("fy"), r.get("end"), r.get("start")
            form, filed = r.get("form"), r.get("filed")
            if not (fp and fy and end and start and filed) or form not in ("10-Q", "10-Q/A"):
                continue
            q = {"Q1": 1, "Q2": 2, "Q3": 3}.get(fp)
            if q is None:
                continue
            dur = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
            if not (80 <= dur <= 100):
                continue
            if end not in cand or filed < cand[end][2]:
                cand[end] = (int(fy), q, filed)
    return {end: [fy, q] for end, (fy, q, _f) in cand.items()}


def _advance(y: int, q: int, by: int) -> tuple[int, int]:
    idx = (y * 4 + (q - 1)) + by
    return idx // 4, idx % 4 + 1


def anchor_count(report_dates: list[str], anchor: dict | None) -> dict[str, list]:
    """{report_date: [fy, q]} by anchoring on Finnhub's explicit label and counting
    over the ordered report-date list (immune to 4-4-5 / 52-53-week calendars)."""
    if not anchor:
        return {}
    dates = sorted(set(report_dates) | {anchor["date"]})
    if anchor["date"] not in dates:
        return {}
    ai = dates.index(anchor["date"])
    out: dict[str, list] = {}
    y, q = anchor["year"], anchor["quarter"]
    prev = None
    for i in range(ai, -1, -1):              # backward incl. anchor
        d = datetime.date.fromisoformat(dates[i])
        if prev is not None and (prev - d).days > GAP_BREAK_DAYS:
            break                            # missing quarter → stop counting
        out[dates[i]] = [y, q]
        prev = d
        y, q = _advance(y, q, -1)
    y, q = _advance(anchor["year"], anchor["quarter"], 1)
    prev = datetime.date.fromisoformat(anchor["date"])
    for i in range(ai + 1, len(dates)):      # forward
        d = datetime.date.fromisoformat(dates[i])
        if (d - prev).days > GAP_BREAK_DAYS:
            break
        out[dates[i]] = [y, q]
        prev = d
        y, q = _advance(y, q, 1)
    return out


def _fye_month_from(period_end_iso: str, quarter: int) -> int:
    """Infer the fiscal-year-end month from one labeled quarter. Snaps the
    period-end to the nearest calendar quarter month (3/6/9/12) first so 52/53-week
    calendars whose quarter-ends spill into the next month (e.g. KO Q1 → Apr-3)
    don't shift the inferred FYE by a month."""
    m = datetime.date.fromisoformat(period_end_iso).month
    qmonth = min((3, 6, 9, 12), key=lambda c: min((m - c) % 12, (c - m) % 12))
    return ((qmonth - 3 * quarter - 1) % 12) + 1


def _sec_label_for(report_date: str, sec_labels: dict[str, list]) -> tuple[list | None, str | None]:
    """SEC (fy,q) for the quarter this report covers = the period-end most recent
    BEFORE the report within the lag window. Returns (label, period_end)."""
    rd = datetime.date.fromisoformat(report_date)
    best = None
    for end in sec_labels:
        days = (rd - datetime.date.fromisoformat(end)).days
        if 0 <= days <= REPORT_LAG_MAX_DAYS and (best is None or end > best):
            best = end
    return (sec_labels[best], best) if best else (None, None)


def build_record(ticker: str, *, sec_labels: dict[str, list], finnhub_events: list[dict],
                 ninjas_rows: list[dict], today: datetime.date,
                 sources_meta: dict | None = None) -> dict:
    """Pure builder for one ticker's calendar record (the testable core)."""
    filer_type = "us_gaap" if sec_labels else "non_us_or_unknown"
    ninjas_dates = [r["date"] for r in ninjas_rows]
    eps_by_date = {r["date"]: r.get("eps_actual") for r in ninjas_rows}
    today_iso = today.isoformat()

    past = sorted([e for e in finnhub_events if e["date"] <= today_iso], key=lambda e: e["date"])
    future = sorted([e for e in finnhub_events if e["date"] > today_iso], key=lambda e: e["date"])
    anchor = past[-1] if past else (future[0] if future else None)
    fh_count = anchor_count(ninjas_dates, anchor)

    rows = []
    fye_month = None
    lags = []
    for rd in sorted([d for d in ninjas_dates if d <= today_iso], reverse=True):
        sec_lbl, period_end = _sec_label_for(rd, sec_labels)
        fh_lbl = fh_count.get(rd)
        label = sec_lbl or fh_lbl
        if label is None:
            continue
        eps = eps_by_date.get(rd)
        sec_fh_agree = bool(sec_lbl and fh_lbl and sec_lbl == fh_lbl)
        label_sources = ([s for s, v in (("sec_xbrl", sec_lbl), ("finnhub", fh_lbl)) if v])
        # US-filer gate: SEC and Finnhub must agree, the quarter actually reported,
        # and it's a 10-Q quarter (sec_lbl present ⇒ Q1–Q3, never Q4/FY).
        gating = bool(filer_type == "us_gaap" and sec_fh_agree and eps is not None)
        rows.append({
            "fiscal_year": label[0], "fiscal_quarter": label[1],
            "report_date": rd, "period_end": period_end, "eps_actual": eps,
            "label_sources": label_sources, "sec_finnhub_agree": sec_fh_agree,
            "confidence": "high" if gating else "low", "gating_eligible": gating,
        })
        if fye_month is None and period_end:
            fye_month = _fye_month_from(period_end, label[1])
        if period_end:
            lags.append((datetime.date.fromisoformat(rd) - datetime.date.fromisoformat(period_end)).days)

    rows = rows[:RECENT_QUARTERS_KEPT]
    monotonic = _is_monotonic(rows)
    # "Finnhub-trusted" gates whether a not-yet-filed next_expected (which SEC can't
    # corroborate) may be gating-eligible. Scope the check to the RECENT window (the
    # kept quarters), NOT all ~50 quarters of history: a single ancient SEC
    # restatement or stale API-Ninjas date should not permanently disqualify a name
    # whose current labeling is clean. Trust = enough recent SEC↔Finnhub agreements
    # and no recent disagreement.
    recent_both = [r for r in rows if set(r["label_sources"]) >= {"sec_xbrl", "finnhub"}]
    recent_agree = sum(1 for r in recent_both if r["sec_finnhub_agree"])
    recent_disagree = len(recent_both) - recent_agree
    finnhub_trusted = (
        filer_type == "us_gaap" and recent_agree >= 2 and recent_disagree == 0
    )

    next_expected = None
    if future:
        ev = future[0]
        fresh = (datetime.date.fromisoformat(ev["date"]) - today).days <= FINNHUB_FRESH_DAYS
        next_expected = {
            "fiscal_year": ev["year"], "fiscal_quarter": ev["quarter"],
            "report_date": ev["date"], "source": "finnhub",
            "gating_eligible": bool(finnhub_trusted and fresh and monotonic),
        }

    record = {
        "fye_month": fye_month,
        "calendar_aligned": (fye_month == 12) if fye_month else None,
        "filer_type": filer_type,
        "typical_lag_days": (sorted(lags)[len(lags) // 2] if lags else None),
        "report_cadence_months": sorted({int(r["report_date"][5:7]) for r in rows}) or None,
        "monotonic": monotonic,
        "last_report_date": rows[0]["report_date"] if rows else None,
        "sources": sources_meta or {},
        "cross_checks": {},
        "recent_quarters": rows,
        "next_expected": next_expected,
    }
    return record


def _is_monotonic(rows: list[dict]) -> bool:
    keys = [r["fiscal_year"] * 4 + r["fiscal_quarter"] for r in rows]  # newest-first
    return all(keys[i] > keys[i + 1] for i in range(len(keys) - 1))


# ── orchestrator ─────────────────────────────────────────────────────────────
def _age_days(path) -> float:
    return (time.time() - path.stat().st_mtime) / 86400.0


def build_reporting_calendar(tickers, *, today=None, cik_map=None,
                             use_cache=True, finnhub_spacing=1.1) -> tuple[dict, dict]:
    """Build the full calendar dict for `tickers`. Returns (calendar, status_meta).

    Per-ticker raw pulls are cached (30-day TTL) under cache/reporting_calendar/.
    Network failures degrade per-ticker (row present, gating_eligible=false) — never
    crash the weekly run.
    """
    today = today or datetime.date.today()
    cik_map = cik_map if cik_map is not None else fetch_cik_map()
    CACHE_DIR_RC.mkdir(parents=True, exist_ok=True)
    calendar = {}
    covered = gating_n = uncovered = errors = 0
    last_finnhub = [0.0]

    for tk in tickers:
        tk = tk.upper()
        cache = CACHE_DIR_RC / f"{tk}.json"
        raw = None
        if use_cache and cache.exists() and _age_days(cache) < CACHE_TTL_DAYS:
            try:
                raw = json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                raw = None
        if raw is None:
            cik = cik_map.get(tk)
            sec_labels = fetch_sec_labels(cik) if cik else {}
            # polite Finnhub spacing (free 60/min)
            if finnhub_spacing:
                gap = time.monotonic() - last_finnhub[0]
                if gap < finnhub_spacing:
                    time.sleep(finnhub_spacing - gap)
            finnhub_events = fetch_finnhub_events(tk, today)
            last_finnhub[0] = time.monotonic()
            ninjas_rows = fetch_ninjas_rows(tk)
            raw = {
                "fetched_at": today.isoformat(), "cik": cik,
                "sec_labels": sec_labels, "finnhub_events": finnhub_events,
                "ninjas_rows": ninjas_rows,
            }
            try:
                cache.write_text(json.dumps(raw), encoding="utf-8")
            except Exception as e:
                logger.debug("cache write failed for %s: %s", tk, e)

        sources_meta = {
            "dates": {"provider": "api_ninjas", "fetched_at": raw["fetched_at"],
                      "n_rows": len(raw["ninjas_rows"]), "error": None},
            "anchor": {"provider": "finnhub", "fetched_at": raw["fetched_at"],
                       "n_events": len(raw["finnhub_events"]), "error": None},
            "sec_label": {"provider": "sec_xbrl", "fetched_at": raw["fetched_at"],
                          "covered": bool(raw["sec_labels"]), "error": None},
        }
        rec = build_record(
            tk, sec_labels=raw["sec_labels"], finnhub_events=raw["finnhub_events"],
            ninjas_rows=raw["ninjas_rows"], today=today, sources_meta=sources_meta,
        )
        calendar[tk] = rec
        if rec["filer_type"] == "us_gaap":
            covered += 1
        else:
            uncovered += 1
        if any(q["gating_eligible"] for q in rec["recent_quarters"]) or \
                (rec["next_expected"] or {}).get("gating_eligible"):
            gating_n += 1

    status = {
        "ticker_count": len(calendar),
        "us_filer_count": covered,
        "non_us_or_uncovered_count": uncovered,
        "gating_eligible_count": gating_n,
        "generated_for": today.isoformat(),
    }
    return calendar, status
