# Plan & Goals — Coverage Manager Reporting-Calendar Enrichment (workstream #8)

**Status:** REVISED v4 — 3rd review incorporated; **design locked, A0/A0.1 gate
PASSED** (2026-06-02). Algorithm validated 225/225 vs independent SEC labels (§14).
SEC-into-A1 decision made (US-filer `gating_eligible` = SEC↔Finnhub agreement).
**A1 (build the export) is unblocked pending the user's go.**
**Author:** Claude (session 2026-06-02). **Owning project:** Coverage Manager.
**Reviewers:** a second code-aware AI (review folded in) + the user.

> **Reviewer ask (v1):** Does this plan correctly identify the problem, choose a
> sound design, respect the published-export contract and its schema-pinned
> consumers, and sequence the work so each step is reviewable and reversible?

### Changelog — v4 (3rd review: SEC-into-A1 decision locked)
Reviewer verdict: "mostly go, but decide whether SEC XBRL is built into A1 —
recommendation yes." **Decided yes.** Changes:
1. **SEC XBRL is now a production source in A1** (§3 table, §3a, §7). The label
   rule (§3/§4): **US filers → `gating_eligible=true` only when SEC fiscal label
   and the Finnhub-anchored count agree**; non-US/ADR/foreign → `false` unless
   another validated source; **Q4 (10-K `fp=FY`) → `false`** until separately
   validated. Removes the Finnhub-sole-anchor false-skip risk.
2. **Stale schema fixed** (§4): `sources` = `dates`(api_ninjas) / `anchor`(finnhub)
   / `sec_label`(sec_xbrl); yfinance moved to `cross_checks`. Rows carry
   `label_sources` + `sec_finnhub_agree`; record carries `filer_type`.
3. **No live calls in CI** (§10, §14): provider responses captured to **frozen
   fixtures**, replayed offline; the live `experiments/a0_*` scripts are manual
   diagnostics only.

### Changelog — v2 (review incorporated)
The second reviewer's verdict was **"go, but only if fiscal-label validation
becomes a blocking first step"** — the subtle failure mode is mislabeling, and a
false skip is worse than a wasted AV call. Changes made:
1. **Mapper validation promoted from open question to a blocking `Phase A0`
   spike** (no export until it passes a labelled fixture set). §5, §7.
2. **Row-level `gating_eligible` flag** added to the schema; consumers gate ONLY
   on `gating_eligible == true`. Low-confidence / source-conflict / missing-FYE /
   non-monotonic rows are published but `gating_eligible=false`. §4, §5.
3. **API Ninjas / yfinance demoted from "authoritative" to "useful, validate
   per-ticker"**; per-ticker source freshness + error fields added. §3, §4.
4. **Pinned export test corrected:** `tests/test_export_artifacts.py:73` asserts
   `len(result["artifacts"]) == 4` for `_step_export_artifacts`. A *separate*
   reporting-calendar step keeps it green, but manifest registration + a
   both-files manifest assertion are now explicit test requirements. §6, §10.
5. **First scope confirmed Positions∪Core; status file confirmed separate**
   (`reporting_calendar_status.json`, not inline `_meta`). §8, §4.
6. Phases restructured to **A0 (validation spike) → A1 (export) → B → C**. §7.

### Changelog — v3 (2nd review + A0/A0.1 results)
2nd reviewer held before A1: A0 had validated mostly against Finnhub (≈circular)
and hadn't met its own "vs AV/SEC" bar. Actions:
1. **Ran A0.1 with independent SEC XBRL labels** → production algorithm (Finnhub
   anchor + count) scores **225/225, zero mismatches** across 6 hard fixtures (§14).
   Gate passed.
2. **Source hierarchy finalized** (§3): API Ninjas = date sequence, Finnhub =
   fiscal anchor (with freshness/corroboration conditions), **yfinance removed from
   the labeling path** (cross-check only). Updated §5 and §7-A1 to match.
3. **Softened the false-skip claim** (§4): "minimizes false skips by gating only on
   corroborated labels" (a wrong corroborated `next_expected` can still over-skip).
4. **Added edge-case handling** (§5): missing quarters, duplicate same-quarter
   events, changed fiscal year, ticker changes → each → `gating_eligible=false`.
5. **Fixed section order** (A0/A0.1 results = §14, References = §15).
6. **Added §3a — SEC XBRL as a production corroboration source** (not just A0.1
   ground truth): free `companyconcept` HTTP gives authoritative `period_end →
   fiscal (year,quarter)` + filing date for US filers; raises a design option to
   require SEC↔Finnhub label agreement for `gating_eligible`.
7. **Probed the EdgarTools hosted API** (`api.edgar.tools/v1`, Bearer key, user on
   **Pro 500/day**): headless-capable (unlike the MCP). Quarterly EXISTS on Pro
   (`?period_type=quarterly`) but is **unusable as the calendar label** — off-by-one
   fiscal year vs SEC/AV (labels by calendar-year-of-period-end), only 2 periods
   deep, calendar-rounded period-ends. Raw `data.sec.gov` `companyconcept` remains
   the production quarterly source; EdgarTools Pro is best for annual fundamentals /
   entity / filings / 13F. §3a updated.

---

## 1. Goals of this workstream

### 1.1 Primary goal
Publish a **canonical, per-ticker reporting-calendar** as a new Coverage Manager
export (`exports/reporting_calendar.json`) that answers, for any covered ticker:

- What is its **fiscal-year-end** (and is it calendar-aligned)?
- For a given **fiscal `(year, quarter)`**, **has it reported yet, and on what
  date?** (the availability question)
- What is its typical **report cadence** and **lag** (period-end → call date)?

This is the **authoritative source** the workspace currently lacks: a mapping from
a company's *fiscal* quarter (how AlphaVantage and SEC filings are keyed) to the
*calendar* report date (how earnings calendars are keyed). Report-date cadence
alone cannot recover this — AAPL (Sep FYE) and ABBV (Dec FYE) report in the same
calendar months — so a fiscal-year-end signal is required (proven by the existing
POC, `experiments/poc_reporting_calendar.py`).

### 1.2 Secondary goals (consumers — may land in follow-up steps)
- **transcripts**: replace/augment the free Finnhub pre-fetch gate (shipped
  2026-06-02, commit `7c22c4f`, `src/transcripts/precheck.py`) with this calendar.
  The gate today is correct but limited: Finnhub free has **no history** and **no
  foreign-numeric coverage**, so it can only skip the not-yet-reported *newest*
  quarter. A calendar with history + ADR/foreign coverage lets transcripts (a)
  gate *every* candidate quarter, not just the newest, and (b) stop wasting AV
  calls on foreign names it currently can't pre-check.
- **earnings_agent**: use the report dates as a **date-verification anchor**,
  complementing its EDGAR auto-correction (the recurring ICLR/foreign-filer
  phantom-date problem; see DEPENDENCIES.md and earnings_agent memory).

### 1.3 Explicit non-goals
- **Not** a real-time/event-driven earnings calendar. Weekly refresh cadence,
  matching the existing `weekly_universe` pipeline. Intraday "who reports today"
  is out of scope.
- **Not** a replacement for `transcripts/unsupported.json` or the Sunday
  `retry-unsupported` stopgap in this step — those are *superseded later*, only
  once transcripts is wired to consume the calendar (a deliberate, separate step
  so we never remove a guard before its replacement is proven).
- **Not** consumer-specific. Per CM's export contract (CLAUDE.md, "Exports —
  published artifact contract"), the file describes the universe generically;
  consumer transforms live in the consumer.
- **Not** a forward-estimates/EPS-surprise product. We persist `eps_actual` only
  as a free, incidental "this quarter reported" corroboration signal.

### 1.4 Definition of done (acceptance criteria)
0. **(Phase A0 gate — blocking)** The date→fiscal-`(year,quarter)` mapper matches
   AV/SEC ground-truth labels with **zero** mismatches on the labelled fixture set
   (§5: AAPL, MSFT, NKE, COST, WMT, ABBV, KO, NVO + ≥1 weird ADR/foreign-style
   ticker). No export code is written until this passes.
1. `exports/reporting_calendar.json` + `exports/reporting_calendar_status.json`
   are produced by `weekly_universe` and **both** listed in `exports/manifest.json`,
   **without** bumping any existing schema-pinned status file
   (`universe_status.json` v3, `positions_status.json` v3).
2. Every published quarter row carries an explicit `gating_eligible` boolean.
   Consumers gate ONLY on `gating_eligible == true`; everything else falls through
   to a normal fetch. Disagreements/low-confidence surface as warnings, never
   silent.
3. Where Finnhub *does* return an explicit `(year, quarter)` for a name's next
   event, our mapper's label for that event **agrees** (built-in cross-check).
4. Per-ticker source freshness + error fields are populated for **all four**
   sources — SEC XBRL (covered/not, fetch error), Finnhub (anchor present/stale),
   API Ninjas (rows/empty/error), and the yfinance cross-check — so any
   missing/stale/erroring source is visible, never silently dropped.
5. Tests pass (`python -m pytest tests/ -q`) — including the updated
   `test_export_artifacts.py` expectations — DEPENDENCIES.md updated, pushed.
6. A consumer can answer "has TICKER reported fiscal YYYYQ#, and is that answer
   gating-eligible?" from the export alone, offline, for US + ADR names
   (foreign-numeric coverage gap documented).

---

## 2. Background / why now

`transcripts` daily-fetch burns a scarce free AV budget (25/day) walking
`(ticker, fiscal-Y, fiscal-Q)` newest-quarter-first; not-yet-reported cells come
back empty and consume a call. The 2026-06-02 spike shipped a free Finnhub gate
that skips the *newest* not-yet-reported quarter (~65–73% of candidates), but
Finnhub free can't see history or foreign names. The same fiscal↔calendar mapping
is independently needed by `earnings_agent` for date verification. CM is the right
owner because it already publishes the universe contract these projects consume,
and the POC for this enrichment already lives in `experiments/`.

The context for this plan is fresh from having just built the transcripts gate, so
the consumer requirements are concrete and known.

---

## 3. Design overview

Per ticker, combine two **free** sources:

**None of these sources is treated as authoritative — each is corroborated.** The
reviewer's point: yfinance `.info` can be stale or missing, and API Ninjas dates
can include estimate dates, restatements, or shifted dates. The export therefore
carries per-ticker **source freshness + error** fields, and any internal
disagreement downgrades a row to `gating_eligible=false` (§4).

**Source hierarchy (finalized after A0/A0.1, SEC added v4):** three load-bearing
free sources (SEC XBRL fiscal-label authority, Finnhub anchor + announce date, API
Ninjas date sequence) + one optional cross-check (yfinance). yfinance is **out of
the labeling path** (A0 found it stale/wrong); Finnhub is **the fiscal anchor**
(A0.1: its labels match
SEC 225/225).

| Role | Source | Notes (verified 2026-06-02) | Failure modes to guard |
|---|---|---|---|
| **Fiscal-label authority** (US filers) — `period_end → (fy, fp)` | **SEC XBRL** `companyconcept` (direct `data.sec.gov` HTTP) | **DECIDED v4 (reviewer): SEC is the fiscal-label authority where available.** Free, no key, no cap; A0.1-validated 225/225. Quarterly fp from 10-Q; comparative-dedup required (§3a). | foreign/IFRS → no us-gaap facts → no SEC label (those rows default `gating_eligible=false`); Q4 from 10-K `fp=FY` held to `false` until validated |
| **Earnings-calendar anchor + announce date** — explicit `(year, quarter)`+date | **Finnhub** `/calendar/earnings` | Provider-asserted fiscal label + the *announce/scheduled date* SEC lacks. Confirms/extends the SEC label and supplies the date the consumer actually keys on. | missing / stale / far-future-only / no matching API Ninjas date → `gating_eligible=false` |
| **Date sequence** (history ~50 quarters) + `eps_actual` | **API Ninjas** `/v1/earningscalendar?ticker=` | Free `date`/`actual_eps`/`actual_revenue`. The report-date history the count walks. Covers US + ADRs; **NOT foreign-numeric**. | estimate/future dates, restatements, missing/duplicate quarters → dedupe per ~90-day window; `eps_actual != null` = "actually reported"; >140-day gap breaks the count (§5) |
| **Optional cross-check only** | **yfinance** `.info` FYE / `mostRecentQuarter` | **NOT authoritative, NOT in the labeling path.** A0 found COST `mostRecentQuarter` 5y stale + FYE month wrong. | used only to flag data-quality disagreements; never sets the label |

**The label decision (DECIDED v4):** for **US filers**, `gating_eligible=true` only
when the **SEC fiscal label and the Finnhub-anchored count agree**. SEC supplies the
authoritative `(fy, q)`; Finnhub/API Ninjas supply the announce-date sequence the
consumer keys on. **Non-US / ADR / foreign-numeric** (no SEC us-gaap facts): rows are
published but default `gating_eligible=false` unless another validated source
exists. **Q4** (10-K `fp=FY`): `gating_eligible=false` until Q4/FY labeling is
separately validated. yfinance stays a QA cross-check only.

### 3a. SEC XBRL — a stronger label/corroboration source for US filers (added v3)

A0.1 used SEC XBRL as independent ground truth; it can also be a *production*
corroboration source. Demonstrated 2026-06-02 (AAPL): the free
`data.sec.gov/api/xbrl/companyconcept/CIK…/us-gaap/{tag}.json` endpoint returns,
per period, `value + unit + start/end + fiscal (fy, fp) + filed date` for every
tagged concept. This gives, **authoritatively and free for US (us-gaap) filers**:

- the `period_end → fiscal (year, quarter)` label (exactly the calendar's crux), and
- the 10-Q/10-K **filing date** (a date anchor, though see the caveat).

**DECIDED v4 (reviewer's call):** SEC is elevated from "validation-only" to the
**production fiscal-label authority** for US filers — SEC supplies the authoritative
fiscal label, API Ninjas/Finnhub supply the **announcement date**, and
`gating_eligible=true` (US filers) **requires SEC↔Finnhub label agreement**. This
directly removes the Finnhub-sole-anchor false-skip risk. Caveats baked into the
gating rules: (a) the **10-Q filing date ≠ the earnings announcement/call date**
(call precedes filing ~0–30 days), so SEC anchors the *label*, the calendars anchor
the *date*; (b) **Q4** (10-K `fp=FY`) → `gating_eligible=false` until separately
validated; (c) **foreign/IFRS** (20-F/6-K, e.g. NVO) → no us-gaap facts → default
`gating_eligible=false`.

**Three access paths (probed live 2026-06-02 with the EdgarTools API key):**
- **Direct `data.sec.gov` HTTP** (free, no key, User-Agent only, **no daily cap**) —
  the **production source for the quarterly fiscal label**: `companyconcept` gives
  per-quarter `(period_end, fy, fp)` directly. Must dedupe comparative re-reports
  (A0.1: ~13-week durations, earliest `filed`). Headless ✓.
- **EdgarTools hosted API** `https://api.edgar.tools/v1` (Bearer key in
  `EDGARTOOLS_API_KEY`; user is **Pro = 500 calls/day, 2 keys**; free=100, Analyst
  =5000) — **headless ✓** (Bearer, so unlike the MCP it *can* run in the cron).
  40+ endpoints; confirmed: `/companies/{cik}/income-statement|balance-sheet|
  cash-flow` (clean, normalized, `standard_concept`), `/metrics`, `/filings`
  (accession + filing_date + acceptance_datetime + sec_url), `/search`,
  `/companies/{cik}`. **Quarterly EXISTS** (Pro feature, `?period_type=quarterly`)
  **but is NOT usable as the calendar's fiscal-label source** — probed 2026-06-02:
  (1) **off-by-one fiscal year vs SEC/AV** — it labels the year as the *calendar
  year of the period-end*, not the fiscal year (AAPL Dec-2025 quarter →
  "Q1 2025" vs SEC/AV "Q1 FY2026"); wrong for ~half the quarters of every mid-year-
  FYE name → would map to the wrong AV `YYYYQ#` key; (2) returns **only 2 periods**
  (current + YoY), no depth params honored; (3) **calendar-rounded period-ends**
  (`2025-12-31` vs true fiscal `2025-12-27`). **Best fit:** clean *annual*
  fundamentals (no comparative-dedup), entity / filings / 13F / adviser metadata
  for the separate KPI-collector idea + headless verification — NOT the per-quarter
  label.
- **EdgarTools MCP** (`financial_trends`/`statements`/`snapshot`; user is **Pro**) —
  richest/pre-aggregated, but a **claude.ai connector = interactive-only**, so
  dev-time verification only, not the cron.

**Net for the calendar:** raw `data.sec.gov` `companyconcept` stays the production
quarterly-label corroboration source; the EdgarTools API/MCP are convenient for
annual fundamentals + verification, not for the per-quarter label layer.

*Out of scope but noted:* the same XBRL endpoints expose the financials themselves
(Revenue, EPS, OperatingIncomeLoss, NetIncomeLoss, …) free + authoritative for US
filers — a foundation for the separate **Earnings KPI collector** idea (triage #34),
not for this calendar.

---

## 4. Proposed export schema — `exports/reporting_calendar.json`

Ticker-keyed, same access pattern as `universe_metadata.json`. **Additive** new
file — see §6 for why this does not touch existing schema pins.

```json
{
  "AAPL": {
    "fye_month": 9,
    "fye_day": 27,
    "calendar_aligned": false,
    "report_cadence_months": [1, 4, 7, 10],
    "typical_lag_days": 30,
    "filer_type": "us_gaap",
    "sources": {
      "dates":     {"provider": "api_ninjas", "fetched_at": "2026-06-02", "n_rows": 50, "error": null},
      "anchor":    {"provider": "finnhub",    "fetched_at": "2026-06-02", "error": null},
      "sec_label": {"provider": "sec_xbrl",   "fetched_at": "2026-06-02", "covered": true, "error": null}
    },
    "cross_checks": {
      "yfinance": {"fye_month": 9, "agrees": true, "stale": false}
    },
    "monotonic": true,
    "last_report_date": "2026-04-30",
    "recent_quarters": [
      {"fiscal_year": 2026, "fiscal_quarter": 2, "report_date": "2026-04-30",
       "period_end": "2026-03-28", "eps_actual": 2.01,
       "label_sources": ["sec_xbrl", "finnhub"], "sec_finnhub_agree": true,
       "confidence": "high", "gating_eligible": true},
      {"fiscal_year": 2026, "fiscal_quarter": 1, "report_date": "2026-01-29",
       "period_end": "2025-12-27", "eps_actual": 2.85,
       "label_sources": ["sec_xbrl", "finnhub"], "sec_finnhub_agree": true,
       "confidence": "high", "gating_eligible": true}
    ],
    "next_expected": {"fiscal_year": 2026, "fiscal_quarter": 3,
                      "report_date": "2026-07-29", "source": "finnhub",
                      "gating_eligible": true}
  }
}
```

`gating_eligible` is the explicit consumer contract. For a **US filer** it is `true`
only when **SEC's fiscal label and the Finnhub-anchored count agree** (`sec_finnhub_
agree == true`) AND the row is otherwise clean. It is forced to **`false`** when any
of: SEC and Finnhub disagree, `confidence != "high"`, the history is **non-monotonic**,
`report_date` is past but `eps_actual` is null (scheduled/estimate, not reported),
the quarter is **Q4** (10-K `fp=FY`, pending separate validation), or the filer is
**non-US / ADR / foreign-numeric** (`filer_type != "us_gaap"`, no SEC label) and no
other validated source corroborates. Consumers never re-derive eligibility — they
read it. yfinance appears only under `cross_checks`, never as a label source.

**Separate status file** `reporting_calendar_status.json` (matches the repo's
existing `*_status.json` convention — resolves v1 §13 Q4): `schema_version` (start
at **1**, independent of universe/positions schemas), `generated_at`,
`ticker_count`, `covered_count` (≥1 report date), `gating_eligible_count`,
`uncovered_tickers` (foreign-numeric etc.), `source_error_tickers`,
`source_versions`.

**Consumer read pattern (the availability question):**
```python
cal = json.loads((CM_EXPORTS / "reporting_calendar.json").read_text())
def has_reported(tk, y, q, today):
    rec = cal.get(tk.upper())
    if not rec: return None  # unknown -> caller falls through (no false skip)
    for rq in rec["recent_quarters"]:
        if rq["fiscal_year"] == y and rq["fiscal_quarter"] == q:
            if not rq["gating_eligible"]: return None   # not trustworthy -> fetch
            return rq["report_date"] <= today           # True=reported -> can fetch
    nx = rec.get("next_expected") or {}
    if nx.get("fiscal_year") == y and nx.get("fiscal_quarter") == q:
        return None if not nx.get("gating_eligible") else False  # scheduled -> skip
    return None  # not in window -> unknown -> fall through
```
**Three-valued** return (True / False / None=unknown). Consumers treat both
`None` AND any `gating_eligible=false` row as "attempt the fetch". The design
**minimizes false skips by gating only on corroborated labels** — but note (per
reviewer) this is not an absolute guarantee: a wrong-but-`gating_eligible=true`
*next_expected* label could still cause a false skip. That is exactly why
`next_expected` is held to the strictest eligibility bar (§5: fresh Finnhub anchor
corroborated by the API Ninjas sequence), and why the residual cost is bounded:
the only way to over-skip is a corroborated label that is still wrong.

---

## 5. The crux: date → fiscal `(year, quarter)` mapper

This is the highest-risk component — **the project, not an implementation
detail** (reviewer). It must be its own pure, unit-tested function, and **Phase A0
validates it against ground-truth labels before any export code is written.**

**Production algorithm — VALIDATED in A0/A0.1 (anchor + count, NOT period-end
estimation).** A0 proved the original "estimate period-end from the report date and
snap to calendar quarter boundaries" approach fails on 4-4-5 / 52-53-week calendars
(COST, KO). The validated algorithm needs **no** per-report period-end math:

1. **Anchor** on Finnhub's explicit, provider-asserted fiscal `(year, quarter)` for
   the ticker's nearest event (most-recent past, else soonest future), with its
   report `date`.
2. **Count** over the ordered API Ninjas report-date list: assign the anchor's
   `(fy, q)` to its matching date, then walk outward incrementing/decrementing the
   quarter and rolling the year at the Q4↔Q1 boundary. The fiscal calendar shape is
   irrelevant — counting is immune to 4-4-5 / 52-53-week quirks.

**Anchor eligibility conditions (reviewer):** Finnhub is the anchor **only when**
(a) it returns an explicit `(year, quarter)`+date, (b) that anchor date matches a
nearby API Ninjas report date (corroboration), and (c) the anchor is fresh (a
recent past event, or a future event within ~one quarter). If Finnhub is missing,
stale, only gives a far-future event, or the API Ninjas sequence doesn't
corroborate → the ticker is **published but `gating_eligible=false`**.

**yfinance is NOT in the labeling path** — A0 found it unreliable (COST
`mostRecentQuarter` 5 years stale; FYE month wrong). It is retained only as an
optional cross-check / data-quality signal. The period-end→fiscal formula (with
52/53-week nearest-boundary snapping) survives only as a **diagnostic** path, not
the load-bearing labeler.

**Edge cases the anchor+count must handle (reviewer) — each sets
`gating_eligible=false` for the affected rows rather than guessing:**
- **Missing quarter** in the API Ninjas history → a >~140-day gap between
  consecutive reports breaks the count; rows past the gap (away from the anchor)
  are not gating-eligible.
- **Duplicate same-quarter events** (restatement, amended date, two rows one
  quarter) → dedupe to one per ~90-day window before counting; flag if ambiguous.
- **Changed fiscal year** (a company shifts its FYE — rare but real) → detect via a
  quarter-length anomaly / non-monotonic labels and stop the count at the change.
- **Ticker changes / re-used symbols** → anchor and history must be for the same
  entity; a name/CIK mismatch in the source → not gating-eligible.

**Built-in correctness checks:** monotonicity of the produced `(year,quarter)`
sequence; and the SEC↔Finnhub agreement check. **CI uses frozen fixtures only** —
the A0.1 validation logic runs in pytest against **cached/captured** SEC/Finnhub/API
Ninjas responses (committed test fixtures), never live network. The live scripts
(`experiments/a0_*_validation.py`) stay as **manual diagnostics**, not a CI
requirement (reviewer's point #2 — no live API calls in normal CI).

---

## 6. Schema safety / blast radius

CM `exports/` is consumed by ~7 projects; two **hard-assert** schema versions
(`catalyst_watch` asserts `positions_status.json.schema_version == 3`;
`analyst-days` asserts `watchlist_status.json` v2/3). Mitigation:

- The new export is a **brand-new file**. It does **not** modify
  `universe_metadata.json`, `universe_status.json`, `positions_status.json`, or
  any existing artifact. No existing consumer reads it, so no existing pin breaks.
- `reporting_calendar` gets its **own** `schema_version` starting at 1, decoupled
  from the universe/positions schemas, so future calendar changes never force a
  bump on unrelated pinned files.
- Update DEPENDENCIES.md with a new "produces / consumed-by" row.

**Verified test interaction (reviewer point #4):**
`tests/test_export_artifacts.py:73` asserts `len(result["artifacts"]) == 4` — but
that is scoped to **`_step_export_artifacts`'s own** four universe artifacts
(`universe.csv`, `universe_metadata.json`, `universe_status.json`, `manifest.json`).
Because the reporting-calendar is a **separate step** (`_step_export_reporting_
calendar`), that assertion stays green untouched. The real care item is the shared
`exports/manifest.json`: the new step must **merge** its two files into the
manifest's `artifacts` list (read-modify-write, not overwrite), and a new test must
assert the manifest contains BOTH `reporting_calendar.json` and
`reporting_calendar_status.json` after a full export run. Sequence the new step so
it runs after `manifest.json` exists. (Confirmed there is no exhaustive
"exact file set" assertion over `exports/` elsewhere in the CM test suite.)

---

## 7. Implementation plan (phased, each independently reviewable/reversible)

**Phase A0 — mapper validation spike ONLY, NO export (blocking gate):**
0. Build the §5 ground-truth fixture (AAPL/MSFT/NKE/COST/WMT/ABBV/KO/NVO + a weird
   ADR/foreign) from AV/SEC labels. Implement the pure date→fiscal-`(year,quarter)`
   mapper + the Finnhub cross-check. **Stop and report** the pass/fail table. If
   any label is wrong, iterate the mapper (or escalate the convention question)
   **before** writing one line of export code. This is the project's make-or-break
   step; treat its correctness bar as stricter than normal.

**Phase A1 — CM enrichment + export (only after A0 passes):**
1. `universe/reporting_calendar.py` — pure builder: `build_reporting_calendar(
   tickers) -> dict`, emitting per-row `confidence` + `gating_eligible` and
   per-ticker source freshness/error. Composes **API Ninjas (date sequence) +
   Finnhub (anchor + announce date) + SEC XBRL `companyconcept` (US-filer fiscal-
   label authority) + the A0 anchor+count mapper**, and sets `gating_eligible`
   per the §3/§4 rule (**US: SEC↔Finnhub must agree**; foreign/Q4 → `false`).
   yfinance only as an optional QA cross-check. No export I/O here (mirrors
   `universe/artifacts.py`).
2. `providers/` additions: a thin API Ninjas earnings-calendar client + an SEC
   XBRL `companyconcept` client with the A0.1 comparative-dedup (both cached);
   reuse the existing Finnhub provider; yfinance optional. Cache namespace
   `cache/reporting_calendar/<TICKER>.json`, TTL ~30 days (slow-moving), with a
   forced refresh near a name's `next_expected` date.
3. `weekly_universe._step_export_reporting_calendar(...)` — writes
   `exports/reporting_calendar.json` + `exports/reporting_calendar_status.json`,
   then **read-modify-write merges** both into `exports/manifest.json`. Insert into
   `main()` after `manifest.json` is written (after `_step_export_artifacts` /
   `_step_export_positions`), before `_step_sigma_export`. Use
   `pipeline_utils.run_step` so a failure is `failed:`-tagged and rolls into the
   health heartbeat (non-gating — universe still ships).
4. Tests: `tests/test_reporting_calendar.py` (mapper, gating_eligible logic, source
   freshness/error, degradation) + update `tests/test_export_artifacts.py` and add
   a manifest-includes-both-files assertion (§10). Offline (mock providers).
5. Docs (CLAUDE.md exports section + README), DEPENDENCIES.md, push CM.

**Phase B — transcripts consumes it (separate PR/session):**
6. transcripts `precheck.py`: add a calendar-backed gate that reads
   `reporting_calendar.json` and **only acts on `gating_eligible == true` rows**
   (history + ADR + every quarter); falls back to the existing Finnhub forward-gate
   when the calendar lacks the name or the row isn't gating-eligible. Preserve the
   three-valued/zero-false-skip contract. Update tests + the `precheck` measurement
   command. Only after this proves out do we revisit retiring the
   `unsupported.json`/Sunday-retry stopgaps.

**Phase C — earnings_agent date anchor (optional, only if it pays off):**
7. Wire the calendar into earnings_agent as a verification anchor **only if** a
   measurement shows it demonstrably reduces phantom-date errors over the existing
   EDGAR auto-correction. Otherwise drop it. Not required for A/B value.

Recommended first deliverable: **A0 → A1** (A0 first, hard stop on its result).
Phase B delivers the end-to-end transcripts win. The user will choose
A-only vs A+B-in-one-session at kickoff (A0 is non-optional either way).

---

## 8. Universe scope & API budget

- API Ninjas free tier: 1 request/ticker (history returned in one call), cached
  ~30 days. **Open question §13 Q2:** confirm the free-tier monthly request cap
  and any per-minute rate limit before sizing the universe. Verified working, not
  yet rate-measured.
- Proposed initial scope: **Positions ∪ Core** (~262–300 names) — the set every
  consumer actually gates on — not the full ~700 `max` universe on day one.
  Expand to full universe once the request budget is confirmed and the mapper is
  validated. The export schema is identical either way; scope is just the input
  ticker list.
- yfinance FYE pulls are also 1/ticker, cached; reuse CM's existing yfinance
  provider + cache discipline.

---

## 9. Caching & refresh

- `cache/reporting_calendar/<TICKER>.json`, TTL ~30 days. Annual FYE never moves;
  report dates only change when a new quarter lands.
- Targeted refresh: if `today >= next_expected.report_date − N days`, refetch that
  ticker so a just-reported quarter appears promptly (so transcripts can fetch it
  the next morning rather than waiting up to 30 days).
- All upstream calls are free; no metered-API exposure (respects the workspace
  "don't burn metered APIs" rule — API Ninjas/yfinance/Finnhub are all free here;
  AV is *not* called by this enrichment at all).

---

## 10. Testing strategy

**CI is offline-only (reviewer point #2): no live SEC/Finnhub/API-Ninjas calls in
normal pytest.** All provider responses are **captured to committed fixtures** and
replayed; the live `experiments/a0_*_validation.py` scripts remain **manual
diagnostics** (run on demand to re-capture fixtures or spot-check), never a CI gate.

- **(A0)** Pure mapper unit tests against the hardcoded AV/SEC ground-truth
  fixture (§5) — zero mismatches is the gate.
- **SEC↔Finnhub agreement test** against **frozen** SEC + Finnhub fixtures (the
  A0.1 logic, replayed offline) — asserts the US-filer `gating_eligible` rule.
- Monotonicity property test over each ticker's date history → sets `monotonic` +
  `gating_eligible`.
- **`gating_eligible` logic tests:** SEC↔Finnhub disagreement, low-confidence,
  non-monotonic history, past-date-with-null-`eps_actual`, Q4/FY, and non-US/foreign
  filer each force `false`.
- Export-shape + status-schema test.
- **Manifest-registration test:** after a full export run, `exports/manifest.json`
  lists BOTH `reporting_calendar.json` and `reporting_calendar_status.json`
  (reviewer point #4). Plus the existing `test_export_artifacts.py:73`
  `== 4` assertion still passes (separate step doesn't touch it).
- Degradation tests: API Ninjas empty (foreign-numeric) → ticker present with
  `recent_quarters: []`, `uncovered` recorded in status, all rows
  `gating_eligible=false`, never a crash; yfinance FYE missing → `stale/error`
  recorded, rows not gating-eligible.
- `weekly_universe` step wired through `run_step` so a failure is visible
  (health heartbeat `partial`), never silent (workspace "no silent failures").

---

## 11. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Mapper mislabels a non-calendar FY**, causing a consumer to skip an available transcript | Med | Pure, heavily-tested mapper; Finnhub cross-check; `confidence` flag; consumers treat low-confidence + unknown as "fetch" (zero false skip preserved) |
| **SEC↔Finnhub fiscal-label disagreement** (the two production label sources diverge for a US filer) | Med | That disagreement is exactly the `gating_eligible=false` trigger (§3/§4) — a divergent label is never gated on; A0.1 showed 225/225 agreement on the fixture set, so disagreements are the rare exception, surfaced not trusted |
| Foreign-numeric names (Tokyo etc.) uncovered by all free sources | High (known) | Documented gap; those names simply return `unknown` → consumers fall through to a normal fetch (status quo, no regression) |
| API Ninjas free-tier cap exceeded on full universe | Low/Med | Start at Positions∪Core; confirm cap (§13 Q2); 30-day cache; targeted refresh only |
| Touching CM's published contract breaks a pinned consumer | Low | New file only; no existing schema bumped; reviewer confirms no exhaustive-file-set assertions (§6) |
| Scope creep into a real-time calendar / estimates product | Med | §1.3 non-goals; weekly cadence; actuals-only |

---

## 12. Why this supersedes the stopgaps (longer-term)
Once Phase B ships, transcripts can gate **every** candidate quarter (not just the
newest) for US+ADR names using real history, and `unsupported.json` +
`retry-unsupported` become redundant for covered names (kept for foreign-numeric
until a source covers them). This is the "fuller fix" the transcripts CLAUDE.md and
the 2026-06-02 gate commit both point at.

---

## 13. Open questions

**Resolved by the v1 review (2026-06-02):**
- ~~Status placement~~ → separate `reporting_calendar_status.json` (matches repo
  convention).
- ~~Scope first cut~~ → Positions∪Core for v1; full universe after limits measured.
- ~~Fiscal-year convention as open question~~ → promoted to the **blocking A0
  gate**: encode AV's convention from the ground-truth fixture; don't assume.
- ~~Consumers infer eligibility from confidence~~ → explicit `gating_eligible`.
- ~~Manifest test risk~~ → separate step keeps `==4` green; add both-files
  manifest assertion.

**Resolved by A0/A0.1 (2026-06-02):**
- ~~Mapper approach~~ → **anchor+count settled, 225/225 vs SEC** (§14). Period-end
  snapping is diagnostic-only.
- ~~Anchor source~~ → **Finnhub** (with §5 freshness/corroboration conditions);
  yfinance out of the labeling path.
- ~~AAPL-only gold too thin~~ → broadened to 6 SEC-validated names.

**Still open — at A1 kickoff (not blocking the algorithm); see §14 "Remaining open":**
1. **API Ninjas free-tier limits** before scaling past Positions∪Core.
2. **Q4 / annual labeling** (A0.1 validated Q1–Q3; Q4 from 10-K/FY needs its own
   check or stays `gating_eligible=false`).
3. **earnings_agent (Phase C)** — only if it beats EDGAR auto-correction.
4. **Anything still wrong** in the data-source claims, schema, blast-radius, or
   sequencing.

---

## 14. Phase A0 + A0.1 results — GATE PASSED (ran 2026-06-02)

*(A0 spike: `experiments/a0_mapper_validation.py`; A0.1 SEC validation:
`experiments/a0_1_sec_validation.py`.)*

### A0 — mapper design spike (`a0_mapper_validation.py`)

Validation spike executed. Ground truth: AAPL's AlphaVantage labels from the
transcripts cache (independent gold: 2026-01-29→2026Q1, 2026-04-30→2026Q2) +
Finnhub's explicit fiscal `(year,quarter)` for the next/recent event of 8 fixture
tickers spanning Sep/Jun/May/early-Sep/Jan/Dec FYEs (incl. COST 4-4-5, WMT Jan-FYE,
NVO ADR). **No AV calls spent.**

**Result: 11/12 labels correct, 0 monotonicity failures.** The mapper evolved
through two design fixes the spike *surfaced empirically*:
1. **v1 (estimate period-end from report date, snap to calendar quarter months)
   FAILED on COST** — its 4-4-5 / 16-week-Q4 calendar ends quarters in
   Nov/Feb/May/Aug, not on calendar quarter-ends. → Replaced with **anchor+count**
   (anchor on a real period-end, count over the ordered report-date list).
2. **anchor+count then FAILED on KO** — a 52/53-week calendar whose Q1 ends
   2026-04-03 (spills into April), which a raw month-based read labels Q2. →
   Added **nearest-ideal-quarter-end snapping** (±days) in `period_end_to_fiscal`.
   KO then passed.

**The one remaining failure (COST) is an ANCHOR DATA-QUALITY problem, not a
mapping-logic problem:** yfinance returned COST `mostRecentQuarter = 2021-05-09`
(**5 years stale**) and `lastFiscalYearEnd` month **8** vs the true ~9. Counting
from a stale anchor lands wrong. KO's anchor was also off (April-spill), and these
together are **hard evidence that yfinance is too unreliable to be the anchor** —
exactly the reviewer's "useful, not authoritative" point.

### A0.1 — independent SEC validation (ran 2026-06-02, `experiments/a0_1_sec_validation.py`)

The 2nd reviewer correctly noted A0 validated mostly against Finnhub (the proposed
anchor) — partly circular — and held before A1 pending independent AV/SEC labels.
A0.1 addresses that: ground truth = **SEC XBRL company-facts** `(period_end → fy,
fp)` (authoritative, free, no AV calls), validating the **production algorithm
(Finnhub anchor + count over API Ninjas dates)**.

**Result: 225/225 report dates correct across 6 hard fixtures, ZERO mismatches.**

| Ticker | FYE | quarters checked | result |
|---|---|---|---|
| AAPL | Sep | 38 | 38/38 |
| MSFT | Jun | 38 | 38/38 |
| NKE | May | 38 | 38/38 |
| COST | early-Sep (4-4-5) | 37 | 37/37 |
| WMT | Jan | 37 | 37/37 |
| KO | Dec | 37 | 37/37 |
| NVO | Dec (ADR/IFRS) | — | no us-gaap facts → can't SEC-validate (documented foreign gap) |

Meta-finding: getting *clean* SEC labels is itself non-trivial — the first harness
scored 29/299 because `companyconcept` re-reports each period as prior-year
**comparatives** in later filings (carrying that filing's fy/fp). Fix: keep only
true ~13-week quarter durations, dedupe each period-end to its **earliest** filing,
Q1–Q3 only. This dedupe logic is unit-tested against **frozen SEC fixtures** in CI;
the live harness stays a manual diagnostic (§10).

### Conclusion — A0/A0.1 gate PASSED → A1 is a go
- Algorithm settled: **Finnhub fiscal anchor + count over API Ninjas dates**, no
  per-report period-end math; immune to 4-4-5 / 52-53-week calendars; validated to
  225/225 vs SEC.
- Reviewer answers folded into the plan body: (a) Finnhub-as-anchor adopted **with
  the freshness/corroboration conditions** in §5; (b) independent gold broadened
  from AAPL-only to 6 SEC-validated names; (c) period-end snapping confirmed
  **secondary/diagnostic only** (Finnhub gives the label directly).
- Source hierarchy finalized in §3; yfinance out of the labeling path.

**Remaining open (for kickoff, not blocking the algorithm):**
0. ~~SEC XBRL as primary corroboration?~~ → **DECIDED v4 (3rd review): YES, built
   into A1.** US-filer `gating_eligible` requires SEC↔Finnhub label agreement; one
   extra free `companyconcept` call/ticker + comparative-dedup. §3/§4/§7.
1. **API Ninjas free-tier limits** — confirm monthly cap / rate before scaling past
   Positions∪Core to full universe.
2. **Q4 / annual labeling** — A0.1 validated Q1–Q3 (clean 10-Q durations); confirm
   AV/transcripts Q4 keying (10-K/FY) maps as expected, or hold Q4 to
   `gating_eligible=false` until separately validated.
3. **Foreign-numeric + ADR/IFRS gap** — neither API Ninjas nor (us-gaap) SEC covers
   them; these stay `gating_eligible=false` (consumers fall through), as today.
4. **earnings_agent (Phase C)** — only if it measurably beats EDGAR auto-correction.

## 15. References (for the reviewer to verify)
- POC: `Coverage Manager/experiments/poc_reporting_calendar.py`
- CM export contract: `Coverage Manager/CLAUDE.md` → "Exports — published artifact contract"
- Export step: `Coverage Manager/weekly_universe.py` → `_step_export_artifacts`, `_step_export_positions`, `main()`
- Metadata builder pattern: `Coverage Manager/universe/artifacts.py`
- transcripts gate (the consumer + the limits this closes): `transcripts/src/transcripts/precheck.py`, `transcripts/CLAUDE.md` → "Pre-fetch availability gate", commit `7c22c4f`
- Dependency map: `Claude Folder/DEPENDENCIES.md`
- Finnhub fiscal `(year,quarter)` semantics (verified): transcripts `providers/finnhub_dates.py`
