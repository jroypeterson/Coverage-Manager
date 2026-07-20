# Project Brief — read this first (for reviewers, human or AI)

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
solo, part-time, healthcare-focused investor's ~1,095-ticker coverage universe,
its user-defined `Sector (JP)` / `Subsector (JP)` taxonomy, and the personal
trading-state layer (`Portfolio` / `Researching` / `Following for Interest` /
`Ready to Buy` / `Ready to Short`) maintained on top of it.

Its job is twofold:

1. **Maintain and grow the universe** — clean, dedup, validate, enrich
   identifiers, discover new candidate tickers (via a Claude-run discovery
   prompt with human sign-off), and probe for delisted/recycled names.
2. **Publish a versioned, generic artifact contract** under `exports/` (schema
   v3) that ~9 downstream sibling projects consume (forensic_triage,
   biotech_triage, screens_equity/quantitative_screens, 13F analyzer, sigma-alert, earnings_agent,
   analyst-days, sa-monitor, catalyst_watch) — so they read CM's canonical
   universe + positions instead of each re-hitting metered fundamentals APIs or
   re-inventing the taxonomy.

On top of that it generates the weekly performance reports (Excel + segmented
HTML) and the Slack feeds the user actually reads: an After/Before/Delta universe
summary to `#coverage`, a movers digest to `#stock-price-alerts`, and a health
heartbeat to `#status-reports`. Success = downstream projects can trust
`exports/` is fresh, valid, and schema-stable, and the user never has to wonder
what changed in the universe week-to-week.

## 2. Success criteria — and current status

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Single source of truth for the coverage universe + its taxonomy | ✅ Done | `data/coverage_universe_tickers.csv` (~1,095 rows); `Sector (JP)`/`Subsector (JP)` taxonomy expanded + consolidated 2026-05-03 |
| 2 | Published, versioned, **generic** artifact contract downstream projects consume | ✅ Done | `exports/` committed to git; schema v3; `universe_status.json` invariant `ticker_count + normalization_collisions == row_count` guards against consumer-specific leakage |
| 3 | Schema is stable + explicitly versioned (consumers pin a version) | ✅ Done | `schema_version` field; documented read-pattern with `assert == 3`; additive changes only (v2→v3 added `core`); `DEPENDENCIES.md` tracks consumers |
| 4 | Fundamentals fetched cheaply with graceful fallback | ✅ Done | Provider chain yfinance → FMP → Alpha Vantage (default `yf_first` for speed); Finnhub TTM overlay; AV last-resort. `tests/test_provider_chain.py`, `test_fmp_provider.py` |
| 5 | New tickers require explicit human sign-off | ✅ Done | Discovery stages candidates; only `approved=true` rows auto-commit (`discovery/`, `tests/test_discovery.py`) |
| 6 | Universe edits surfaced weekly so the user knows what changed | ✅ Done | After/Before/Delta Block Kit post to `#coverage`; 2-tier baseline (snapshot files preferred, git HEAD fallback w/ dirty-tree caveat); `reporting/universe_delta.py`, `tests/test_universe_delta.py` |
| 7 | Weekly performance reports (returns + fundamentals, segmented) | ✅ Done | Excel + 4 segmented HTML tabs; multi-period returns; USD-converted mkt cap/EV; `tests/test_perf_calcs.py`, `test_excel.py` |
| 8 | Personal trading-state layer with 5 states, published per-state | ✅ Done | `data/positions_and_researching.csv` → `portfolio.json` + 4 sibling JSONs; `tests/test_positions.py` |
| 9 | No silent failures — visible alarm on partial/failed runs | ✅ Done | 3-bucket status semantics (success/failed/blocked); `pipeline_utils.collect_non_successes`; `health/v1` heartbeat to `#status-reports`; `tests/test_health_reporting.py`, `test_weekly_build_wrapper.py` |
| 10 | Runs unattended weekly | ✅ Done | Windows Task Scheduler, Fri 08:00 ET, `run_weekly_coverage.bat`; `weekly-build` wrapper with try/finally heartbeat guarantee |
| 11 | Delisted/recycled tickers caught before they rot the universe | 🟡 Partial | `check-delisted` probe (step [4/6]) flags but is **non-gating**; removal + archival to `data/delisted_tickers.csv` is a **manual** confirm-then-edit step |
| 12 | Historical valuation context (P/E, EV/S vs 5Y **and 10Y**) across the universe | 🟡 Partial | Full-universe expansion shipped 2026-07-19: 26 HIST_COLS (5Y + 10Y + `History Status`) in the **Excel**/pickle output; `cli.py history-backfill` populates the cache resumably for all ~1,095 names, the report reads it cache-only so runtime is unchanged. Still deferred: HTML rendering (`reporting/html.py` doesn't iterate `HIST_COLS`), weekly-pipeline wiring, and the negative-P/E-mean decision |
| 13 | Reporting-calendar artifact (fiscal-quarter → report-date map) | 🟡 Partial | `exports/reporting_calendar.json` shipped (schema v1, own version) with `gating_eligible` zero-false-skip contract; US-filer-only gating (foreign/Q4 default `false` by design) |
| 14 | Weekly performance email delivery | ⬜ Not yet (disabled) | `EMAIL_ENABLED = False` in `config.py`; intentionally off, replaced by `#coverage` Slack post. Revisit 2026-06-29. Honored by both orchestrator and standalone `cli.py performance` |
| 15 | sigma-alert ETF augmentation lives in the consumer (no cross-repo coupling) | ⬜ Not yet | Deferred "Stage 2": `reporting/sigma_export.py` still composes generic builder with hardcoded sector ETFs and pushes into the sibling clone; TODO tracked in-code |

**Overall: the core hub goal is met and live.** The universe, the published
contract, the weekly Slack feeds, and the unattended schedule all work and are
tested (27 test files). Open items are deliberate deferrals (Phase 2 history,
email re-enable, sigma-export decoupling) and one manual-step gap (delisted
removal), not missing core function.

## 3. Key design decisions (and why)

1. **`exports/` is a strictly generic, committed contract — not a grab-bag.**
   Artifacts describe the coverage universe and nothing else; consumer-specific
   transforms belong in the consumer. Files are committed to git (not gitignored)
   so consumers get history/reproducibility/rollback. The `ticker_count +
   normalization_collisions == row_count` invariant is a guard: if a consumer's
   tickers leaked into `universe_metadata.json`, the count would break.
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
   the `Core` flag (~263 analytically-covered names; 3 sibling projects gate on
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
- **History columns now cover the full ~1,095-ticker universe** (2026-07-19), but
  the *fetching* is a separate on-demand command (`history-backfill`), not part of
  the weekly pipeline — the report itself reads the cache only, so a cold cache
  shows `not_attempted` rather than blocking or slowing the run.
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
  `core` field now in `universe_metadata.json` (v3); the refactor was deferred
  until those projects are next touched.
- **`watchlist*` exports are deprecated back-compat** (one cycle) — `Ready to Buy`
  / `Ready to Short` rows don't appear there; new consumers must use the 5
  state-specific JSONs.
- **Email re-enable decision** is pending the 2026-06-29 revisit.

Most useful feedback: (a) whether the `exports/` contract is genuinely sufficient
and stable for the ~9 consumers, or whether something consumer-specific is
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
- **Tests:** `python -m pytest tests/ -q` — 27 test files (do not need
  network/API access; providers are mocked). Notable coverage:
  `test_weekly_build_wrapper.py`, `test_weekly_universe.py`,
  `test_universe_delta.py`, `test_export_artifacts.py`, `test_provider_chain.py`,
  `test_reporting_calendar.py`, `test_health_reporting.py`, `test_positions.py`.
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
  delisted/ticker-change checks, lei/ipo backfill, export-artifacts.
- `discovery/` — candidate discovery + staging. `cache.py` / `audit.py` / `ticker_utils.py` — infra.
- `data/coverage_universe_tickers.csv` (~1,095 rows, source of truth) · `positions_and_researching.csv`
  (5 Position states) · `delisted_tickers.csv`.

### Data flow
Sources (yfinance/FMP/Finnhub/AlphaVantage/SEC EDGAR/GLEIF/Renaissance/API Ninjas) → `providers/`
(chained, `cache/`-backed) → `universe/` validates+enriches `data/*.csv` → **two sinks:**
(a) `reporting/` builds `reports/` Excel+HTML+PNG (gitignored, emailed when `EMAIL_ENABLED`);
(b) export-artifacts writes the committed `exports/` **schema-v3** contract siblings read. Slack fans
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
**Publishes (`exports/`, schema v3 — consumers `assert schema_version == 3`):**
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

⚠️ **Known drift (DEPENDENCIES.md):** sa-monitor `build_universe.py:27` still asserts
`schema_version == 2` — needs a bump to 3. Any schema change here: grep siblings + patch same session.

### Performance / Security
Runtime dominated by Finnhub cold-cache 60s rate-limit pauses (~17min full run); `yf_first` keeps the
normal path fast; S&P 500 benchmark is price-only by design (no 500-name fundamentals pull). Book is
private but **already committed to two public repos** (CM + sigma-alert) since 2026-05-03 — a
deliberately deprioritized pre-existing leak, not this project's to fix.
