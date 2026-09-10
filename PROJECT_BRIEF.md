# Project Brief — read this first (for reviewers, human or AI)

> **Last reconciled against the repo: 2026-09-10** (board #319). Check drift with
> `python ../scripts/audit_project_briefs.py --repo "Coverage Manager"` — it counts
> commits landed since this file was last touched that actually changed behaviour,
> excluding CI/artifact writes. Reconcile when that number gets large; every figure
> below was re-measured, not carried forward.

This file exists so a reviewer can (1) judge how close the project is to its
intended goal and (2) understand the key design decisions **before** giving
feedback. For mechanics — CLI surface, file layout, the `exports/` artifact
contract, provider chain, column definitions — see `README.md` and `CLAUDE.md`;
this brief does not re-describe how it works.

> When reviewing, weigh findings against the **success criteria** and the
> **non-goals / accepted tradeoffs** below. Several "obvious improvements" (import
> CM into consumers, compute fundamentals downstream, real-time delta, FMP-first
> default) were considered and deliberately declined. Say so if you think a
> declined option is actually worth it, but engage with the stated rationale.

---

## 1. Intended goal (the "why")

Coverage Manager is the **workspace data hub**: the single source of truth for a
solo, part-time, healthcare-focused investor's coverage universe (**1,354 rows**,
verified 2026-09-10 — `exports/universe_status.json` `row_count` is the authority,
not this sentence), its user-defined `Sector (JP)` / `Subsector (JP)` taxonomy, and
the personal trading-state layer (`Portfolio` / `Researching` /
`Following for Interest` / `Ready to Buy` / `Ready to Short`) maintained on top of it.

Its job is twofold:

1. **Maintain and grow the universe** — clean, dedup, validate, enrich
   identifiers, discover new candidate tickers (via a Claude-run discovery
   prompt with human sign-off), and probe for delisted/recycled names.
2. **Publish a versioned, generic artifact contract** under `exports/` (**schema
   v4**) that downstream sibling projects consume — so they read CM's canonical
   universe + positions instead of each re-hitting metered fundamentals APIs or
   re-inventing the taxonomy.

**Two framing changes landed after this brief's previous version, and a reviewer
holding the old model will misread the repo:**

- **CM publishes derived *classifications*, not only the hand-maintained taxonomy.**
  `Commercial Biopharma` (`exports/commercial_biopharma.json`, its own
  `schema_version 1`) is computed from a stated rule —
  `revenue_ttm_usd_m >= 1000 or mkt_cap_usd_m >= 10000` — and carried into
  `universe_metadata.json` as a `commercial` field. It is a category **CM decides**,
  so the rule and its `unknown` bucket are review surface in a way a hand-typed
  column is not.
- **There are now TWO coverage books from one code path**, not one.
  `scripts/build_hc_coverage_xlsx.py` drives a `BOOKS` table: the healthcare Core
  book, and `AA_NonCore Coverage` (Core=Y names the Core book does not hold). Every
  differing string is data in that table — a third book belongs in `BOOKS`, never in
  a fork of the builder.

**Measured 2026-08-15 — 13 sibling projects import CM's exports by path**
(`13F Analyzer`, `company-research-agent`, `earnings_kpi`, `exec_interviews`,
`focus_today`, `forensic_triage`, `insider_ownership`, `notion_watchlist`,
`post_earnings_movers`, `quality_companies`, `sa-monitor`, `sector_chart_pack`,
`transcripts`), plus `sigma-alert` via the separate push path and
`analyst-days` / `catalyst_watch` / `screens_equity` via the raw CSV and
`reports/`. The long-standing "~9 consumers" figure in this brief was an
undercount; treat `DEPENDENCIES.md` as the register, not this paragraph.

On top of that it generates the weekly performance reports (Excel + segmented
HTML) and the Slack feeds the user actually reads: an After/Before/Delta universe
summary to `#coverage`, a movers digest to `#stock-price-alerts`, and a health
heartbeat to `#status-reports`. Success = downstream projects can trust
`exports/` is fresh, valid, and schema-stable, and the user never has to wonder
what changed in the universe week-to-week.

## 2. Success criteria — and current status

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Single source of truth for the coverage universe + its taxonomy | ✅ Done | `data/coverage_universe_tickers.csv` (**1,354 rows**, `Core=Y` = **345**, verified 2026-09-10); `Sector (JP)`/`Subsector (JP)` taxonomy expanded + consolidated 2026-05-03. Grew 1,086 → 1,328 on 2026-08-06 on JP's expansion decision. Both counts are observations — `exports/universe_status.json` and a `Core` filter over the CSV are the authority |
| 2 | Published, versioned, **generic** artifact contract downstream projects consume | ✅ Done | `exports/` committed to git; **schema v4**; the invariant is now simply **`ticker_count == row_count`** — v4 keys on the RAW ticker, so collisions are structurally impossible rather than counted. `normalization_collisions` remains in the status file (shape unchanged) and is always `0`; a non-zero value now means a **duplicate row** reached the exporter |
| 3 | Schema is stable + explicitly versioned (consumers pin a version) | ✅ Done | `schema_version` field; documented read-pattern is **`assert status["schema_version"] in (3, 4)`**. Consumers were widened to `{3, 4}` rather than moved to `{4}` **on purpose** — a consumer must work on either side of CM republishing, so a deploy-order dependency never exists. `DEPENDENCIES.md` tracks consumers |
| 4 | Fundamentals fetched cheaply with graceful fallback | ✅ Done | Provider chain yfinance → FMP → Alpha Vantage (default `yf_first` for speed); Finnhub TTM overlay; AV last-resort. `tests/test_provider_chain.py`, `test_fmp_provider.py` |
| 5 | New tickers require explicit human sign-off | ✅ Done | Discovery stages candidates; only `approved=true` rows auto-commit (`discovery/`, `tests/test_discovery.py`) |
| 6 | Universe edits surfaced weekly so the user knows what changed | ✅ Done | After/Before/Delta Block Kit post to `#coverage`; 2-tier baseline (snapshot files preferred, git HEAD fallback w/ dirty-tree caveat); `reporting/universe_delta.py`, `tests/test_universe_delta.py` |
| 7 | Weekly performance reports (returns + fundamentals, segmented) | ✅ Done | Excel + 4 segmented HTML tabs; multi-period returns; USD-converted mkt cap/EV; `tests/test_perf_calcs.py`, `test_excel.py` |
| 8 | Personal trading-state layer with 5 states, published per-state | ✅ Done | `data/positions_and_researching.csv` → `portfolio.json` + 4 sibling JSONs; `tests/test_positions.py` |
| 9 | No silent failures — visible alarm on partial/failed runs | ✅ Done | 3-bucket status semantics (success/failed/blocked); `pipeline_utils.collect_non_successes`; `health/v1` heartbeat to `#status-reports`; `tests/test_health_reporting.py`, `test_weekly_build_wrapper.py` |
| 10 | Runs unattended weekly | ✅ Done | Windows Task Scheduler, Fri 08:00 ET, `run_weekly_coverage.bat`; `weekly-build` wrapper with try/finally heartbeat guarantee |
| 11 | Delisted/recycled tickers caught before they rot the universe | 🟡 Partial | **Four independent lanes now, not one** — `delisted_check` (yfinance price/identity, three-outcome `flagged`/`clean`/**`inconclusive`** + process-wide rate-limit backoff), `ticker_change_check` (SEC CIK→symbol, catches a *rename* so a row can be remapped rather than removed), `symbol_directory` (weekly Nasdaq Trader snapshot diff, adjudicated against SEC per-CIK submissions), and `crsp_snapshot`'s universe reconciliation. All **non-gating**; removal + archival to `data/delisted_tickers.csv` is still a **manual** confirm-then-edit step, which is the remaining gap |
| 11b | Identity is verified, not assumed — the right ISIN/LEI/CIK for the right issuer | ✅ Done | Shipped after this brief was last written and is now the largest single area of the repo. `verify-isin-issuers` (OpenFIGI ISIN→issuer-name identity check, on the **enrich write path** *and* as an on-demand audit), `crosscheck-foreign` (iShares holdings ⋈ SEC N-PORT, weekly step), `backfill-foreign-ids`, `instrument-type` (depositary receipt vs ordinary share), ISO 6166 check-digit + country-prefix gates. **First full run found 7 wrong ISINs identifying entirely different companies** (all corrected 2026-07-28, JP-approved) and 21 further conflicts awaiting a call |
| 11c | Universe growth is discovered, not only hand-added | ✅ Done | `form10-watch` (weekly `10-12B` spin-off registrations — a spin-off has **no offering**, so the IPO calendar is structurally blind to it) + `symbol-directory` + the discovery lane's candidate ledger with Slack approval (`poll_ipo_replies`, scheduled 3×/day) |
| 12 | Historical valuation context (P/E, EV/S vs 5Y **and 10Y**) across the universe | 🟡 Partial | Full-universe expansion shipped 2026-07-19: 26 HIST_COLS (5Y + 10Y + `History Status`) in the **Excel**/pickle output; `cli.py history-backfill` populates the cache resumably for the whole universe, the report reads it cache-only so runtime is unchanged. Still deferred: HTML rendering (`reporting/html.py` doesn't iterate `HIST_COLS`), weekly-pipeline wiring, and the negative-P/E-mean decision |
| 13 | Reporting-calendar artifact (fiscal-quarter → report-date map) | 🟡 Partial | `exports/reporting_calendar.json` shipped (schema v1, own version) with `gating_eligible` zero-false-skip contract; US-filer-only gating (foreign/Q4 default `false` by design) |
| 14 | Weekly performance email delivery | ⬜ Not yet (disabled) | `EMAIL_ENABLED = False` in `config.py` (verified 2026-08-15); intentionally off, replaced by `#coverage` Slack post. **The 2026-06-29 revisit date has passed unactioned** — the decision is open, not settled. Honored by both orchestrator and standalone `cli.py performance` |
| 15 | sigma-alert ETF augmentation lives in the consumer (no cross-repo coupling) | ⬜ Not yet | Deferred "Stage 2": `reporting/sigma_export.py` still composes generic builder with hardcoded sector ETFs and pushes into the sibling clone; TODO tracked in-code. Still true 2026-08-15 |
| 16 | The weekly report is readable where JP actually reads it | ✅ Done | **The weekly report is a published web page** — <https://jroypeterson.github.io/Coverage-Manager/> (`reporting/weekly_page.py`, step `weekly_page`, served by GitHub Pages off `master`). JP 2026-08-08 on an eleven-reply Slack thread: *"having a published clickable html page that refreshes weekly … would be better and more readable"*. Slack now carries only the decisions |
| 17 | A published artifact is verified after writing, not only before | ✅ Done | Step `check_published_exports` (`universe/export_acceptance.py`) re-opens every published CSV **with the encoding the least careful consumer uses**, asserts the join key is present and populated, and cross-checks counts against the status file claiming to describe them. Built after a BOM silently blanked the `Ticker` column in every export while `validation_passed` said `true` — validation ran on the *source* and nothing read the *artifact* back |
| 18 | Money is single-currency by construction — no mixed-unit figure is ever published | ✅ Done | Shipped after this brief was last written. Minor-unit currencies are a `MINOR_UNITS` table, not a GBp special case (ZAc mapped after two JSE rows published ~100x low: Aspen Pharmacare USD 43M → 4,217M). EV and its multiples are computed from **single-currency primitives**; the vendor's mixed-unit `enterpriseValue` is no longer published (TAK 5.1tn → 91bn). `derive_valuation` lives in `providers/valuation.py` so the report lane and the export lane cannot diverge. `num()` rejects infinity at the boundary, not just NaN |
| 19 | A derived `Commercial Biopharma` category, published and consumed | ✅ Done | `exports/commercial_biopharma.json` (`schema_version 1`, rule stated *in the artifact*), plus a `commercial` field in `universe_metadata.json`; wired into `weekly-universe` step 4i and `cli.py backfill-commercial-revenue`. Consumed by `sigma-alert` (digest subcategory) and `sector_chart_pack` (valuation scatter). The export names its own `unknown` bucket rather than silently folding it below the line |
| 20 | Index membership tracked with **history**, without infecting the universe | 🟡 Partial | `universe/index_membership.py` + `index_reconciliation.py` (board #354): MSCI EAFE, Russell 1000/2000/3000, S&P 500, dated weekly snapshots so history accumulates. Found and fixed a silent Vanguard outage that had served a frozen membership list for 12 days behind a working fallback. **Deliberately not in `exports/` and not rows in the universe** — see §4. Remaining: four consumers still read per-project caches as if they were a contract (the migration is filed, not done) |

**Overall: the core hub goal is met and live.** The universe, the published
contract, the weekly Slack feeds, the published web page, and the unattended
schedule all work and are tested — `ls tests/test_*.py | wc -l` is the authority on
the count, and this brief no longer carries one. It said `27`, then `58`; both were
true when written and wrong within a month, which is the whole argument for pointing
at the command instead. Open items are deliberate
deferrals (Phase 2 history rendering, email re-enable, sigma-export decoupling,
wake-race hardening) and one manual-step gap (delisted removal), not missing
core function.

**What changed in the previous reconciliation (2026-07-19 → 2026-08-15, 84
substantive commits).** The whole identity-verification surface (§11b) and the
published page (§16) did not exist; the universe grew by 254 names; the export
contract went v3 → v4.

**What changed in this one (2026-08-15 → 2026-09-10, 56 substantive commits).**
Four things, and the first two change the *shape* of the project rather than a
number: CM now publishes a **derived classification** it computes itself
(Commercial Biopharma, §19) and drives a **second coverage book** from the same
builder (§1); currency and enterprise value were wrong in a plausible direction and
were fixed structurally (§18); and index membership is tracked with history on its
own path (§20). Auto-add lost Bucket 5 — a declined ticker is now never auto-added
under any bucket (§3.13).

## 3. Key design decisions (and why)

1. **`exports/` is a strictly generic, committed contract — not a grab-bag.**
   Artifacts describe the coverage universe and nothing else; consumer-specific
   transforms belong in the consumer. Files are committed to git (not gitignored)
   so consumers get history/reproducibility/rollback. The **`ticker_count ==
   row_count`** invariant is a guard: if a consumer's tickers leaked into
   `universe_metadata.json`, the count would break.
2. **`yf_first` is the default fundamentals priority, not FMP.** The FMP-primary
   refactor had drifted into paying FMP's multi-endpoint fan-out across the whole
   universe on every ordinary report run. yfinance is one `Ticker.info` call per
   ticker and materially faster; FMP is kept as fallback and as an explicit
   `PROVIDER_PRIORITY=fmp_first` comparison mode. No providers were deleted.
3. **S&P 500 benchmark tab is price-only.** It computes benchmark returns but
   skips a second full fundamentals pass over the whole S&P 500 — a large,
   deliberate runtime win. Reintroducing benchmark fundamentals would materially
   slow the default path.
4. **Separate "P/E (TTM)" column sourced *always* from FMP.** The existing
   "Fwd P/E" column is provider-inconsistent (yfinance = forward/NTM, FMP =
   trailing/TTM), so comparing it to a 5Y trailing average is apples-to-oranges.
   Phase 1 adds a clean always-FMP TTM column; EV/S TTM is consistent across
   providers so no new column was needed there.
5. **Three distinct lists, not one.** Coverage Universe (everything tracked) vs.
   the `Core` flag (**345** analytically-covered names, verified 2026-09-10; 3 sibling projects gate on
   it) vs. `positions_and_researching.csv` (personal trading state). Conflating
   them would break downstream gating; the `Core` column must not be dropped.
6. **3-bucket operational status (success / failed / blocked).** "Blocked" (a
   gating decision prevented a step) is treated as non-success distinct from
   "failed" (an exception) — a blocked report still produced no report. All
   rollups must use `pipeline_utils.collect_non_successes`, never reverse-engineer
   success from the steps dict.
7. **2-tier delta baseline with a dirty-tree caveat.** Snapshot files
   (`.coverage/last_run_*.csv`) are preferred over git HEAD so manual uncommitted
   edits between weekly runs are still captured; git fallback only on first run,
   and it warns in the Slack post when the tree was dirty.
8. **Pipeline split into `weekly-universe` + `weekly-report` under a thin
   `weekly-build` wrapper.** Lets the universe half (which produces the contract
   downstream projects need) run without dragging the slower reporting half along.
9. **`reporting_calendar` gating is zero-false-skip.** Only US filers with SEC
   XBRL label ↔ Finnhub count agreement get `gating_eligible=true`; everything
   ambiguous (foreign/ADR/Q4/null) defaults `false` so consumers fall through to
   a normal fetch rather than wrongly skipping.
10. **`universe_metadata.json` is keyed by the RAW ticker (schema v4,
    2026-07-30).** The key used to be suffix-stripped (`ROG SW` → `ROG`,
    `DIA.MI` → `DIA`), which did two silent things. It **deleted a company from
    the published contract** — `ROG` (Rogers Corporation, `Core=Y`) and `ROG.SW`
    (Roche) both normalized to `ROG`, later-row-wins meant the export said `ROG`
    was Roche, and Rogers Corporation had **no entry at all** while the exporter
    logged `normalization_collisions: 1` every run for months and it was read
    past every time. And it **broke the obvious join for 183 of 1,096 rows**:
    `universe.csv` carries `Ticker = DIA.MI` while the metadata key was `DIA`, so
    any consumer doing `metadata[row["Ticker"]]` missed every suffixed row.
    `_normalize_ticker` is retained for the case-collision validator **only**;
    `tests/test_metadata_raw_keys_v4.py` guards the key path by inspecting the
    source.
11. **A published artifact is read back before it ships.** See criterion 17. The
    general rule this encodes: *validating the source is not validating the
    artifact*, and the check must share no code with the writer so it cannot
    share a bug with it.
12. **Every identity lane has three outcomes, never two.** `flagged` / `clean` /
    **`inconclusive`** in `delisted_check`; `ok` / `no_data` / `inconclusive` in
    `ipo_backfill`; `ok` / `conflict` / `inconclusive` in `isin_identity`. The
    founding case: on 2026-07-25 `delisted_check` reported 58 flags while `ACLX`
    traded at **$115.07 on NASDAQ with 13.2M volume** — Yahoo was throttling, and
    throttling was being recorded as death. A lookup that failed and a company
    that died return the same empty response, and only one of them is a finding.
13. **A declined ticker is never auto-added, under any bucket.** Bucket 5 was
    demoted from auto-add to queue on 2026-09-06. The 2026-08-09 ruling that
    created it rested on a measured claim that `DPC` and `EROC` "fall in neither
    bucket"; the first Russell list under the new rule put **both** in band, and
    three of the four names Bucket 5 did auto-add had been screened out on the
    merits twice each. The general form: **a rule justified by a measurement is
    only as good as re-running that measurement**, and this one was never re-run.
14. **A currency rule belongs beside the VENDOR, not beside a consumer.**
    `MINOR_UNITS` first lived inside `scripts/build_hc_coverage_xlsx.py`, so when
    the books were fixed on 2026-09-07 the *performance report* — a different lane
    reading the same vendor — kept publishing Aspen Pharmacare 100x low for another
    day. `derive_valuation` was about to repeat it exactly, so it lives in
    `providers/valuation.py` and both lanes import it; **neither owns a copy.**
15. **A plausible wrong number is worse than an absurd one.** The first proposed EV
    fix — tag the vendor's `enterpriseValue` with `financialCurrency` and convert —
    puts Takeda at ~USD 33bn against a true ~USD 91bn. The absurd USD 5.1tn it
    replaced gets noticed; 33bn gets used. Hence EV is computed from primitives
    that each carry one known currency, and a missing input **blanks the field,
    never the row**.

## 4. Non-goals / accepted tradeoffs

- **Not a real-time system.** Batch, weekly (Fri 08:00) or on-demand via CLI.
  The user's machine must be on; accepted.
- **Delisted removal is intentionally manual.** The `check-delisted` probe is
  non-gating and only *flags*; the user confirms each flag and edits the CSV +
  archives to `delisted_tickers.csv` by hand. Auto-removal was declined to avoid
  eating real rows on a false positive.
- **`exports/` carries no consumer-specific data.** sigma-alert's sector-ETF
  augmentation, forensic_triage's call budgets, etc. live in the consumers. If
  you want to add a non-universe ticker to the metadata, the transform belongs
  downstream.
- **Email is deliberately off**, not broken — the `#coverage` Slack post replaces
  it. Flipping `EMAIL_ENABLED = True` re-enables with no other code change.
- **`financial-growth` FMP endpoint is skipped** (402 on Starter tier); growth
  fields come from the Finnhub TTM overlay instead.
- **History columns now cover the full universe** (expansion 2026-07-19; the
  universe keeps growing, so a cold-cache tail is always expected), but
  the *fetching* is a separate on-demand command (`history-backfill`), not part of
  the weekly pipeline — the report itself reads the cache only, so a cold cache
  shows `not_attempted` rather than blocking or slowing the run.
- **Index membership must NOT enter the universe or `exports/`.** JP, 2026-09-09:
  *"I don't want index membership to infect the coverage manager."* Snapshots are
  reference data on their own path (`data/index_membership/`, gitignored, same
  licensing posture as `data/crsp/`) — never rows in
  `data/coverage_universe_tickers.csv`, never a published export. A consumer that
  wants membership reads the snapshots directly.
- **The four remaining index-membership consumers are deliberately unmigrated.**
  `post_earnings_movers`, `forensic_triage` and `screens_equity/surprise_screens`
  still read their own caches. Starting the dated archive is the half that cannot
  be bought back later; repointing consumers can be done any week and risks
  breaking working lanes. Ordering, not neglect — do not file it as a gap.
- **Public-repo privacy exposure is out of scope here.** The full book is
  committed to public repos; that is a known, separately-tracked workspace
  decision, not something this project re-litigates.

## 5. Known gaps / candidate next steps (feedback welcome here)

- **Phase 2 historical valuation (deferred):** HTML report doesn't render the 13
  HIST_COLS (`reporting/html.py` iterates `FUND_COLS` only); expansion to the
  full universe / Core flag is pending formatting validation.
- **sigma-export cross-repo coupling (deferred "Stage 2"):** the hardcoded sector
  ETF list should move into the sigma-alert repo, with sigma-alert reading
  `exports/universe_metadata.json` directly. TODO tracked in
  `reporting/sigma_export.py`. The current design writes + pushes into the sibling
  clone and is sensitive to local edits there (auto-rebase guards CI races but a
  dirty clone makes the step `failed:`).
- **Wake-time network race:** the Friday scheduled run can fire before DNS is up,
  causing provider/Slack calls to fail. A `_urlopen_retry`-style backoff (used by
  scheduled_jobs_monitor) would harden it.
- **Downstream `Core` consumers still grep the raw CSV** instead of reading the
  `core` field now in `universe_metadata.json`; the refactor was deferred
  until those projects are next touched.
- **21 ISIN identity conflicts are open and awaiting JP's call** (first full run
  2026-07-28: 794 checked → 742 ok, 21 conflicts, 31 inconclusive). None were
  auto-applied, same protocol as the seven that were corrected. Two classes worth
  separating before acting: plain wrong-issuer ISINs (`BOI.PA`→WINGARA AG,
  `DIA.MI`→the SPDR DJIA Trust) versus **multi-field contamination** where the
  name, CIK and ISIN describe different companies and the row must be fixed as a
  **set**, not one cell (`MED`, `MOVE`, `UCB`, `ICAD`). `ticker_change_check`
  cannot catch the latter — the wrong CIK maps back to the same ticker, so the
  contamination is self-consistent.
- **4 standing `listing-mismatch` rows** (`AZN`, `FER`, `MDA`, `2359.HK`) hold the
  weekly heartbeat at `partial` **deliberately**, pending JP's ruling on the
  ADR-vs-ordinary `Listing Type` taxonomy question.
- **The negative-P/E-mean decision is still open** — raw FMP annual P/E goes
  negative in loss years (`LLY` FY2017 −444.5, `CAT` FY2016 −843.1), so
  `P/E 10Y Avg` computes to −2.2 for LLY. Winsorize, drop non-positive years, or
  switch to a median. Until then downstream screens should prefer EV/S.
- **`watchlist*` exports are deprecated back-compat** (one cycle) — `Ready to Buy`
  / `Ready to Short` rows don't appear there; new consumers must use the 5
  state-specific JSONs.
- **Email re-enable decision** is pending the 2026-06-29 revisit.

Most useful feedback: (a) whether the `exports/` contract is genuinely sufficient
and stable for the consumers `DEPENDENCIES.md` registers, or whether something
consumer-specific is
leaking in; (b) correctness of the universe-delta baseline/snapshot logic;
(c) whether the manual delisted-removal step is an acceptable tradeoff or worth
automating; (d) which deferred item (Phase 2 history vs. sigma-export decoupling
vs. wake-race hardening) to do first.

## 6. How to evaluate

- **Mechanics, CLI surface, exports schema, provider chain:** `README.md` +
  `CLAUDE.md` (detailed).
- **Entry points:** `cli.py` (all subcommands); `weekly_build.py` (Friday
  wrapper, the scheduled entry); `weekly_universe.py` / `weekly_report.py`
  (independently-runnable halves); `run_weekly_coverage.bat` (Task Scheduler).
- **Core logic to scrutinize:**
  - Published contract: `universe/` (artifacts, validation, reporting_calendar)
    + `exports/` output.
  - Provider fallback/merge: `providers/provider_chain.py`,
    `providers/fmp_provider.py`, `providers/yfinance_provider.py`.
  - Pipeline status correctness: `pipeline_utils.py` (`run_step`,
    `collect_non_successes`) + the three orchestrators.
  - Universe delta: `reporting/universe_delta.py`.
  - Cross-repo push: `reporting/sigma_export.py`.
- **Tests:** `python -m pytest tests/ -q` — the suite is the authority on its own
  size; this brief deliberately no longer names a count (do not need
  network/API access; providers are mocked). Notable coverage:
  `test_weekly_build_wrapper.py`, `test_weekly_universe.py`,
  `test_universe_delta.py`, `test_export_artifacts.py`, `test_provider_chain.py`,
  `test_reporting_calendar.py`, `test_health_reporting.py`, `test_positions.py`,
  and the identity/acceptance suites added since 2026-07-19:
  `test_isin_identity.py` (82 — every name pair captured live from OpenFIGI
  against real universe rows, not invented), `test_foreign_crosscheck.py`,
  `test_export_acceptance.py`, `test_weekly_page.py` (50),
  `test_symbol_directory.py` (18), `test_form10_watch.py` (23),
  `test_instrument_type.py` (16), `test_crsp_snapshot.py` (68),
  `test_metadata_raw_keys_v4.py`.
- ⚠️ **`reporting/weekly_page.py` is the one module you must not review by
  reading alone.** It shipped three bugs the markup validator passed — the HTML
  was well-formed and completely wrong each time (one `<p>` per *source* line
  shredded every hard-wrapped lede and split `**bold**` across elements; an
  11-column table rendered four words per line down a 90px column; a card
  headline picked the *peers* list over the company name because the widest
  short cell is longer for `WMT, LULU, CROX, FIVE, CVNA` than for
  `Jersey Mike's Subs Inc.`). **Render it and look at it.**
- **Repo:** GitHub `jroypeterson/Coverage-Manager`, branch `master`. `exports/`
  is committed on purpose — do not gitignore it.

## 7. Architecture map

*CM is the workspace's primary data producer — §"Integration points" is the load-bearing part.*

### Tech stack
Python 3.8+, script-driven (no framework). `pandas`, `yfinance`, `openpyxl` (Excel),
`matplotlib` (Agg), `requests`/`lxml`, `anthropic` (Haiku 4.5 movers "why"), `python-dotenv`,
`pytest`. **No DB** — CSV masters in `data/`, disk-cached provider JSON in `cache/`, committed
JSON/CSV contract in `exports/`, gitignored Excel/HTML/PNG in `reports/`, snapshot/delta JSON in
`.coverage/`, health fallback in `.health/`.

### Module map
- `cli.py` — argparse entry point; dispatches every subcommand.
- `weekly_build.py` — Friday wrapper: runs `weekly_universe` then gates `weekly_report` on
  `validation_passed`; posts `#stock-price-alerts` summary + `#status-reports` health (try/finally).
- `weekly_universe.py` / `weekly_report.py` — the universe-side and report-side orchestrators.
- `pipeline_utils.py` — shared `run_step` / `collect_non_successes` three-bucket step status.
- `config.py` — paths, `.env` keys, `PROVIDER_PRIORITY`, segments/ETFs, movers thresholds.
- `providers/` — data adapters; `provider_chain.py` owns the fundamentals fallback/merge chain.
- `reporting/` — Excel/HTML/Slack/email + `sigma_export.py`, `universe_delta.py`, `movers.py`, `charts.py`.
- `universe/` — CSV lifecycle: validation, cleanup, enrich, positions, reporting_calendar,
  delisted/ticker-change checks, lei/ipo backfill, export-artifacts. **Plus, added since
  2026-07-19:** `isin_identity.py` (ISIN→issuer name via OpenFIGI), `foreign_crosscheck.py`
  + `foreign_identifiers.py` (iShares ⋈ SEC N-PORT), `symbol_directory.py` (Nasdaq Trader
  diff), `form10_watch.py` (spin-off registrations), `instrument_type.py` (receipt vs
  ordinary share), `export_acceptance.py` (read the artifact back), `crsp_snapshot.py`,
  `cik_backfill.py`. **Added since 2026-08-15:** `commercial_biopharma.py` (the derived
  category, §19), `index_membership.py` + `index_reconciliation.py` (§20, on their own
  path — nothing here writes the universe), `aliases.py` (one issuer, several live ticker
  strings), `s1_watch.py` / `confidential_watch.py` (the IPO pipeline before a listing
  exists).
- `providers/valuation.py` — **the one implementation** of `derive_valuation` / `num` /
  `_usable_rate` / `positive_multiple`. Both the coverage workbook and the weekly
  performance report import it; neither owns a copy. See §3.14 for why it lives beside
  the vendor.
- `reporting/weekly_page.py` — the published GitHub Pages weekly report ·
  `reporting/slack_blocks.py` (markdown → Block Kit; **Slack has no table primitive, stop
  trying to make one**) · `reporting/pipeline_reversals.py` (a company an earlier report
  committed to adding that a later one excludes without acknowledgement).
- `providers/estimates_history.py` — appends a point-in-time analyst-estimate observation
  per `(ticker, date)`. **A snapshot is not a record:** `cache/analyst_estimates/` always
  answers *"what does the street forecast now"* and can never answer *"what did it forecast
  then"* — which is unanswerable retroactively, so every unrecorded week is gone.
- `discovery/` — candidate discovery + staging. `cache.py` / `audit.py` / `ticker_utils.py` — infra.
- `data/coverage_universe_tickers.csv` (source of truth) · `positions_and_researching.csv`
  (5 Position states) · `delisted_tickers.csv`. Row counts live in
  `exports/universe_status.json` and `exports/positions_status.json`, not here —
  both numbers were stale within a month of every previous reconciliation.

### Data flow
Sources (yfinance/FMP/Finnhub/AlphaVantage/SEC EDGAR/GLEIF/Renaissance/API Ninjas) → `providers/`
(chained, `cache/`-backed) → `universe/` validates+enriches `data/*.csv` → **two sinks:**
(a) `reporting/` builds `reports/` Excel+HTML+PNG (gitignored, emailed when `EMAIL_ENABLED`);
(b) export-artifacts writes the committed `exports/` **schema-v4** contract siblings read. Slack fans
to 3 channels (`#coverage` delta · `#stock-price-alerts` movers · `#status-reports` health);
`sigma-export` pushes metadata straight into the sibling `../sigma-alert/` git clone.

### Configuration & secrets
`.env` keys: `FINNHUB_API_KEY`, `FMP_API_KEY`, `ALPHAVANTAGE_API_KEY`, `ANTHROPIC_API_KEY`,
`EDGAR_IDENTITY`, `RENAISSANCE_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `SLACK_WEBHOOK_URL`,
`SLACK_WEBHOOK_COVERAGE`, `SLACK_WEBHOOK_STATUS_REPORTS`. Env-overridable tunables in `config.py`:
`PROVIDER_PRIORITY` (`yf_first` default | `fmp_first`), `MOVERS_*`, `MOVERS_LLM_MODEL`, `HEALTH_ATTEMPT`.

### Build / run / schedule
Entry: `cli.py`. Primary: `python cli.py weekly-build`. Split: `weekly-universe` / `weekly-report`.
Manual/preview: `cli.py performance --sample`, `cli.py cross-check --sample`, `cli.py validate`.
**Schedule: Windows Task Scheduler** (not GH Actions): `run_weekly_coverage.bat`
(`WeeklyCoverageBuilder`, Fri 08:00 ET, headless `claude -p`) + `run_watchlist_monday.bat`
(`WatchlistMondayReport`, Mon 08:00). Both `.bat` live at `C:\Users\jroyp\` — keep **CRLF + ASCII +
goto-style**.

### Error handling & observability
Health v1 → `#status-reports` at end of every `weekly-build` (`error`/`partial`/`ok`; try/finally;
`.health/last_run.json` fallback). Three-bucket step status (Success/`failed:`/`blocked:`);
`collect_non_successes` is the canonical rollup; report gated on `validation_passed` (`--force`
override). **`.bat` publish backstop:** after the headless agent, the bat runs `weekly-universe
--skip-discovery` then `performance` UNCONDITIONALLY (guards against a backgrounded build leaving
`exports/` stale), each capturing rc + `goto` fail-label so a bad publish/commit/push turns the task
RED not green-stale. Audit: `run_log.csv`, `reports/performance_timing.jsonl`, `.coverage/…delta*.json`.

### Testing
`python -m pytest tests/ -q` (mocked providers, no network) — must pass before committing. Scope/
notable files in §6.

### Integration points (cross-project) — the load-bearing section
**Publishes (`exports/`, schema v4 — consumers `assert schema_version in (3, 4)`):**
- `universe.csv` / `universe_metadata.json` / `universe_status.json` — the coverage universe +
  `{name,sector,subsector,sub_subsector,core}`. Consumed by earnings_agent, sa-monitor, transcripts,
  forensic_triage, exec_interviews, insider_ownership, earnings_kpi, focus_today, catalyst_watch, …
- 5 Position-state files `portfolio/researching/following_for_interest/ready_to_buy/ready_to_short.json`
  + `positions_and_researching.csv` + `positions_status.json` — consumed by sigma-alert, earnings_agent,
  transcripts, catalyst_watch, analyst-days, exec_interviews, insider_ownership, sector_chart_pack, …
- `reporting_calendar.json` (+`_status`, own `schema_version==1`, `gating_eligible` zero-false-skip
  contract) — transcripts precheck (LIVE), earnings_agent (planned), earnings_kpi.
- `watchlist.{csv,json,_status}` — **DEPRECATED** back-compat (Portfolio∪Researching); analyst-days only.
- `manifest.json` — directory. **Pushed directly into `../sigma-alert/`** (not `exports/`) by
  sigma-export: `ticker_metadata.json` + the 5 state files + deprecated `core_watchlist.json`, one commit.
- Non-`exports/` couplings: `data/coverage_universe_tickers.csv` `Core` column → forensic_triage /
  analyst-days / earnings_agent; `reports/coverage_performance_<date>.xlsx` → screens_equity/quantitative_screens;
  `cache/prices/*` → screens_equity/quantitative_screens, portfolio_daily, sector_chart_pack; `cache/perf/perf_df_*.pkl`
  → sector_chart_pack.

**Consumes (reverse channel):** notion_watchlist WRITES `data/positions_and_researching.csv` (only
downstream that writes CM data; runs as a non-gating pre-step of `WeeklyCoverageBuilder`);
sigma-alert's `missing_metadata.json` feedback; `_shared/api_rate_ledger` (AV) + `_shared/email_alert`.

✅ **The "known drift" this section used to warn about is RESOLVED — verified 2026-08-15.**
It read: *"sa-monitor `build_universe.py:27` still asserts `schema_version == 2` — needs a
bump to 3."* All three facts are now wrong: the file is at
`sa-monitor/scripts/build_universe.py` (not the repo root), the pin is on line 41, and it
reads `_ACCEPTED_CM_SCHEMA = frozenset({3, 4})`. The warning is kept here as a **corrected**
entry rather than deleted, because a stale hazard warning is worse than none — it sends a
reviewer to fix a non-problem and implies a live cross-repo break that does not exist.
The standing rule is unchanged and is the real content: **any schema change here — grep
siblings and patch in the same session**, and widen the pin to a set rather than moving it,
so no consumer depends on deploy order.

### Performance / Security
Runtime dominated by Finnhub cold-cache 60s rate-limit pauses (~17min full run); `yf_first` keeps the
normal path fast; S&P 500 benchmark is price-only by design (no 500-name fundamentals pull). Book is
private but **already committed to two public repos** (CM + sigma-alert) since 2026-05-03 — a
deliberately deprioritized pre-existing leak, not this project's to fix.
