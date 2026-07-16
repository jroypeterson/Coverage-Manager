# Coverage Manager — Project Instructions

## Git sync
After making code changes, always commit and push to GitHub (`origin master`) before ending the conversation. Also push after completing a significant change or when it has been a while since the last push — don't let unpushed work accumulate. Use descriptive commit messages.

## "Let's finish" workflow
When the user says "let's finish", "we're done", "wrap up", or anything similar that signals the end of a working session, perform this checklist before responding:

1. **Save memory** — write any new feedback, project, user, or reference memories from the session that aren't already captured. Update or remove stale entries.
2. **Update documentation** — refresh `README.md`, `CLAUDE.md`, and any other relevant doc files for the changes made during the session. Don't let docs drift behind the code.
3. **Run tests** — `python -m pytest tests/ -q` must pass before pushing.
4. **Commit and push** — stage relevant files, write a descriptive commit message, push to `origin master`. Include the sibling `sigma-alert` repo if its files were touched in this session.
5. **Surface anything skipped** — if there are unrelated uncommitted changes, surface them and ask before touching them. Never silently commit or revert work the user didn't explicitly authorize.

## Project structure
- `cli.py` — CLI entry point
- `weekly_build.py` — Wrapper that runs `weekly_universe` then `weekly_report` and posts a combined Slack summary
- `weekly_universe.py` — Universe-side orchestrator (validate → archive → discovery → export-artifacts → sigma-export)
- `weekly_report.py` — Reporting-side orchestrator (validate read-only → archive → performance → movers → email)
- `movers_runner.py` — Movers report orchestration (loads perf snapshot, calls `reporting.movers`, writes HTML/MD, posts Slack). Used by `cli.py movers` and `weekly_report._step_movers`
- `pipeline_utils.py` — Shared `run_step` / `collect_failures` helpers used by all three orchestrators
- `weekly_coverage_prompt.md` — Weekly coverage discovery prompt (run by scheduled task)
- `config.py` — All paths, API keys, segment definitions
- `data/coverage_universe_tickers.csv` — Master coverage universe
- `data/positions_and_researching.csv` — Positions and research list (subset of universe). Replaces `data/watchlist.csv` (deleted 2026-05-03). Schema: `Ticker, Position, Position Date, Buy Price, Sell Price, First Buy Date, Average Cost, Shares, Notes`. `Position` is one of:
  - `Portfolio` — you own this (full or starter)
  - `Researching` — building a thesis to buy; not yet held (active thesis work)
  - `Following for Interest` — passive earnings/signal tracking; no intent to trade
  - `Ready to Buy` — long thesis complete; waiting for the entry trigger (typically a price level on Buy Price)
  - `Ready to Short` — short thesis complete; waiting for the entry trigger (typically a price level on Sell Price, since short entry is at the high and cover is at the low)

  Managed via `universe/positions.py` and the `positions` CLI subcommand. Published to `exports/positions_and_researching.csv`, `exports/portfolio.json`, `exports/researching.json`, `exports/following_for_interest.json`, `exports/ready_to_buy.json`, `exports/ready_to_short.json` (and back-compat `exports/watchlist*.{csv,json}` for one cycle — these only include `Portfolio ∪ Researching` to preserve the historical contract).
- `data/delisted_tickers.csv` — Hand-managed archive of tickers that have been acquired/de-listed. Captures last-known sector + market cap so the data isn't lost when a row is removed from the active universe. Append manually after confirming a `delisted_check` flag is real. Schema: `Ticker, Company Name, Sector (JP), Subsector (JP), Sub-subsector (JP), Country (HQ), Exchange, ISIN, Currency, Last Mkt Cap (USD), Last Price, Last Data Date, Delisted Date, Reason, Notes, Date Recorded`. Supersedes the legacy `reports/delisted_tickers.xlsx` (which is gitignored and was migrated into this CSV on 2026-04-27).
- `providers/` — External data sources (yfinance, Finnhub, FMP, AlphaVantage, FX). `providers/fmp_history.py` is a separate FMP-only fetcher for 5-year P/E and EV/S history used by the Phase 1 historical valuation enrichment (see "Historical valuation columns" below).
- `reporting/` — Report generation (Excel, HTML, email, Slack, sigma_export). `reporting/history_stats.py` holds None-safe avg/stdev/min/max/vs-avg helpers for the Phase 1 history columns.
- `universe/` — CSV validation, enrichment, cleanup
- `discovery/` — Candidate discovery pipeline
- `exports/` — **Published artifact contract for downstream projects (committed to git)**
- `reports/` — Generated reports (gitignored)
- `reports/samples/` — Sample/preview reports
- `cache/` — Cached API data (gitignored). Namespaces: `prices/`, `fundamentals/`, `fx/`, `news/`, `perf/`, `identity/`, and `key_metrics_history/` (30-day TTL — annual P/E + EV/S series for the Phase 1 historical valuation columns)

## Exports — published artifact contract

`exports/` is the versioned, committed interface that other projects in this workspace consume (forensic_triage, biotech_triage, idea_generation, 13F analyzer, sigma-alert via a separate path). **Files are committed to git** so consumers get history, reproducibility, and rollback. Downstream projects should read these files directly rather than importing Coverage Manager code or hitting fundamentals providers themselves.

**These artifacts are generic and canonical** — they describe the coverage universe and nothing else. Consumer-specific transforms (e.g. sigma-alert sector ETF augmentation) belong in the consumer, not here. If you find yourself wanting to add tickers to `universe_metadata.json` that aren't in `data/coverage_universe_tickers.csv`, that's a sign the transform belongs downstream.

Files (regenerated by `weekly_universe`'s export-artifacts step):

- `exports/universe.csv` — Snapshot of `data/coverage_universe_tickers.csv`
- `exports/universe_metadata.json` — `{TICKER: {name, sector, subsector, sub_subsector, core}}` derived only from CSV rows; no consumer-specific augmentation. `core` is the raw value of the `Core` column ("Y" for analytically-covered names, blank otherwise)
- `exports/universe_status.json` — Versioned status + validation contract; **always read `schema_version` first**
- `exports/positions_and_researching.csv` — Positions+researching list joined with the full universe row: every coverage column followed by `Position`, `Position Date`, `Buy Price`, `Sell Price`, `First Buy Date`, `Average Cost`, `Shares`, `Notes`. Source `data/positions_and_researching.csv` is the editable source; the join happens at export time. All four `Position` states appear in this CSV.
- `exports/portfolio.json` — `{TICKER: {...}}` for `Position == "Portfolio"` rows only (names you own). Each entry has the position fields (`position`, `position_date`, `buy_price`, `sell_price`, `first_buy_date`, `average_cost`, `shares`, `notes`), `name`/`sector`/`subsector`/`sub_subsector`, and every raw universe column.
- `exports/researching.json` — Same shape as `portfolio.json` but for `Position == "Researching"` rows only (names you're building a thesis on).
- `exports/following_for_interest.json` — Same shape as `portfolio.json` but for `Position == "Following for Interest"` rows only (passive earnings/signal tracking; no intent to trade). `buy_price` / `sell_price` are informational and typically blank.
- `exports/ready_to_buy.json` — Same shape as `portfolio.json` but for `Position == "Ready to Buy"` rows only (long thesis complete; waiting for the entry trigger). `buy_price` is typically the entry trigger level.
- `exports/ready_to_short.json` — Same shape as `portfolio.json` but for `Position == "Ready to Short"` rows only (short thesis complete; waiting for the entry trigger). `sell_price` is typically the short-entry trigger level (entry-on-the-high, cover-on-the-low).
- `exports/positions_status.json` — Versioned status + validation contract for the positions file. Includes `entry_count`, `portfolio_count`, `researching_count`, `following_for_interest_count`, `ready_to_buy_count`, `ready_to_short_count`.
- `exports/watchlist.csv` — **DEPRECATED** back-compat (one cycle): legacy 5-col schema (Buy Price, Target Price, Date Added, Notes) derived from positions_and_researching.csv, **filtered to `Portfolio ∪ Researching` only** (preserves the historical contract — `Ready to Buy` / `Ready to Short` rows do not appear here). `Sell Price` is mapped to `Target Price`. Use the new state-specific JSON files for new code.
- `exports/watchlist.json` — **DEPRECATED** back-compat (one cycle): legacy JSON shape derived from positions_and_researching.csv, **filtered to `Portfolio ∪ Researching` only**. Use `portfolio.json` + `researching.json` + `following_for_interest.json` + `ready_to_buy.json` + `ready_to_short.json` for new code.
- `exports/watchlist_status.json` — **DEPRECATED** back-compat (one cycle): mirrors `positions_status.json` with the legacy shape.
- `exports/reporting_calendar.json` — **(NEW 2026-06-02)** Per-ticker fiscal `(year, quarter)` → report-date map for **Positions ∪ Core**. Built by `universe/reporting_calendar.py` (step `_step_export_reporting_calendar`). Each `recent_quarters` row + `next_expected` carries **`gating_eligible`**: for **US filers** `true` only when the **SEC XBRL fiscal label and the Finnhub-anchored count agree**; **non-US/ADR/foreign** (no us-gaap facts) and **Q4** (10-K `fp=FY`) default `false`. Sources: SEC `companyconcept` (fiscal-label authority, comparative-deduped), Finnhub `/calendar/earnings` (anchor + announce date), API Ninjas earningscalendar (report-date history); yfinance is cross-check only. **Consumers (transcripts fetch-gating, earnings_agent date verification) MUST gate only on `gating_eligible == true`** — anything else (incl. `null`/foreign/Q4) falls through to a normal fetch (zero-false-skip contract). Own `schema_version` (1), independent of the universe/positions schemas. See `REPORTING_CALENDAR_PLAN.md`.
- `exports/reporting_calendar_status.json` — Versioned status for the reporting calendar (`schema_version`, ticker/us-filer/gating-eligible counts). Read `schema_version` first.
- `exports/manifest.json` — Directory of files in `exports/` with their purpose

`universe_status.json` schema (v3) — required fields:

```json
{
  "schema_version": 3,
  "dataset_version": "2026-04-17",
  "generated_at": "2026-04-17T13:05:22Z",
  "source_path": "data/coverage_universe_tickers.csv",
  "row_count": 1094,
  "ticker_count": 1092,
  "normalization_collisions": 2,
  "collision_examples": ["ROG", "VCEL"],
  "validation_passed": true,
  "validation_errors": [],
  "validation_warnings": ["..."],
  "last_discovery_run": "2026-04-17"
}
```

**Note (2026-05-03):** Report segment renamed `"Other"` → `"Following: Non-HC"` in `config.py` `SECTOR_SEGMENTS`; HTML filename suffix `other` → `following_non_hc`. This is **not** a schema change — only the report-output label changed to remove the segment-vs-sector name clash. Archive glob keeps `coverage_other_*.html` for back-compat cleanup of pre-rename files.

**Sector (JP) taxonomy expansion (2026-05-03):** Split the residual catch-all `"Other"` (46 names) and merged `"Fintech"` (3 names) into seven explicit sectors. `ALLOWED_SECTORS_JP` now includes:

| Sector | Count | Status |
|---|---:|---|
| Biopharma | 705 | -1 (Nipro reclassified to MedTech 2026-05-03) |
| MedTech | 142 | +3 (AVTR + PACB from old Life Science Tools sector + Nipro from Biopharma) |
| Healthcare Services | 106 | unchanged |
| SaaS | 56 | unchanged |
| Tech | 52 | expanded (absorbed AAPL/MSFT/NVDA/NFLX/SPOT/AMZN/TSLA/etc. from Other) |
| Financials | 12 | NEW (JPM/V/MA/KKR/HOOD/COIN/AFRM/MCO/SPGI from Other + BRO/FI/PAYP from Fintech) |
| Industrials | 11 | NEW (CAT/CNI/CP/FDX/UPS/CPRT/ARXS/MDA/VLTO/FER/ULS) |
| Consumer | 6 | NEW (WMT/LULU/CROX/CVNA/FIVE/ACVA) |
| Energy | 3 | NEW (BE/XE/TPL) |
| Materials | 1 | NEW (LIN) |
| Real Estate | 1 | NEW (CIGI) |
| Life Science Tools | 0 | DISSOLVED — folded into MedTech / Subsector="Life Science Tools" |
| Fintech | 0 | retained in ALLOWED for back-compat; merged into Financials |
| Other | 0 | retained in ALLOWED as residual; all rows reassigned |

**MedTech subsector consolidation (2026-05-03):** Reduced ~30 messy subsectors to 16 clean ones:

| Subsector | Count | Notes |
|---|---:|---|
| Life Science Tools | 23 | Lab tools, sequencing platforms, CDMOs, bioprocessing — Sub-subsector tags preserve detail (Lab Products, Genomics, Bioprocessing, CDMO, Analytical Instruments) |
| Diagnostics | 23 | absorbed `NextGen Sequencing` (4) and `Diagnostics / AI` (1) as Sub-subsector tags |
| Hospital Supply & Equipment | 20 | merged 3 prior labels (Hospital Supply, Hospital Capex Ex/Pumps/Supplies, HC Cap Equipment); Olympus added as Endoscopy sub-sub |
| Orthopedics | 14 | merged Spine/Ortho + Ortho; SYK added |
| Cardiovascular | 9 | renamed from Cardio; absorbed Peripheral; added Terumo, EW, BSX |
| Diversified MedTech | 9 | absorbed Other, Diversified, LC MedTech (the LC names individually reclassified to ABT, JNJ, etc.); Nipro added |
| Ophthalmology | 9 | spelling fix (Opthomalogy → Ophthalmology); absorbed Contact Lenses / Surgical |
| Packaging | 7 | unchanged |
| Diabetes | 5 | merged Diabetes Technology + Diabetes |
| Dental, Hearing Aid | 5 each | unchanged |
| Sleep, Urology | 4 each | unchanged |
| Surgery | 2 | added ISRG with Sub-subsector="Surgical Robotics" |
| Aesthetics | 2 | unchanged |
| Radiopharmaceuticals | 1 | unchanged |

Fintech and Other are kept in `ALLOWED_SECTORS_JP` so legacy callers (e.g. `watchlist add --sector=Fintech`) don't reject the value, but no rows currently use them. Schema version unchanged (still v2) — the change is additive in `universe_metadata.json` (consumers that pass through the sector value see new strings); only consumers that pin to specific values would notice.

**`Core` column semantics:** The `Core` column on `data/coverage_universe_tickers.csv` flags tickers the user analytically covers (loosely or tightly) — names with a working model or formed view. Distinct from `data/positions_and_researching.csv` which records personal trading state (held in portfolio, or actively researching). Three downstream sibling projects depend on the `Core` flag: `forensic_triage` (call-budget gate for triage runs), `analyst-days/src/universe.py:load_core_watchlist`, and `earnings_agent/coverage.py` — all filter `Core == "Y"` to scope their work to deeper-coverage names. Do not drop the column.

**Three lists summary (post-2026-05-03):**

| List | Where | What it represents |
|---|---|---|
| Coverage Universe | `data/coverage_universe_tickers.csv` (1,095 rows) | Every ticker tracked. Source of truth for sector taxonomy. |
| Core Coverage flag | `Core` column on the universe CSV (~263 names) | Names you cover analytically. Used by 3 sibling projects. |
| Positions and Research | `data/positions_and_researching.csv` | Names with personal trading state — `Portfolio` (held), `Researching` (active thesis work), `Following for Interest` (passive tracking; no intent to trade), `Ready to Buy` (long thesis done, waiting for entry trigger), or `Ready to Short` (short thesis done, waiting for entry trigger). |

**Schema v3 changes (2026-05-06):**
- `universe_metadata.json` entries now include `core` ("Y" for analytically-covered names, blank otherwise). Additive change; consumers ignoring unknown fields are unaffected. Downstream consumers that currently grep `Core == "Y"` from the raw CSV (forensic_triage, analyst-days, earnings_agent) can read the JSON instead — refactor deferred until they're touched.

**Schema v2 changes (2026-04-17):**
- `universe_metadata.json` entries now include `sub_subsector` (empty string when unset); same for `watchlist.json` legacy flat keys.
- `Sector (JP)` taxonomy change: `"PA"` retired (collapsed into `"Other"`); `"Healthcare Real Estate"` retired (collapsed into `"Healthcare Services"` with `Subsector (JP)="Healthcare Real Estate"`).
- Subsector normalizations: `Post-acute` → `Post-Acute`, `HCIT` → `HIT`, `Value-Based Care` → `VBC`, `Life Sci - Software` → `Life Science Software`.
- New source column `Sub-subsector (JP)` for finer-grain classifications (e.g. `Senior Housing REIT` under HC Real Estate).
- Report segment renamed: `"PA & Other"` → `"Other"`; HTML suffix `pa_other` → `other`.

Downstream consumers should bump their `assert status["schema_version"] == N` check accordingly; metadata is additive so reads of `name`/`sector`/`subsector` continue to work unchanged.

Field semantics:
- `row_count` — number of rows in the source CSV
- `ticker_count` — number of unique normalized tickers in `universe_metadata.json`
- `normalization_collisions` — number of CSV rows whose normalized ticker collided with an earlier row's (e.g. `ROG SW` and `ROG.DE` both normalize to `ROG`); the later row wins
- Invariant: `ticker_count + normalization_collisions == row_count`. If consumer-specific tickers were leaking in, `ticker_count` would exceed `row_count - normalization_collisions`.
- `validation_passed` — explicit boolean; do NOT reverse-engineer this from the errors list

Read pattern for downstream projects:

```python
import json
from pathlib import Path

CM_EXPORTS = Path("../Coverage Manager/exports")
status = json.loads((CM_EXPORTS / "universe_status.json").read_text())
assert status["schema_version"] == 3, "Coverage Manager exports schema changed"  # bumped 2026-05-06: added 'core'
if not status["validation_passed"]:
    raise RuntimeError(f"Universe failed validation: {status['validation_errors']}")
metadata = json.loads((CM_EXPORTS / "universe_metadata.json").read_text())
```

The sigma-alert-specific `ticker_metadata.json` (in the sibling sigma-alert clone) is a **separate** artifact produced by `reporting/sigma_export.build_sigma_metadata` for sigma-alert's GitHub Actions runs. It composes the generic `build_universe_metadata` with hardcoded sector ETFs that the sigma-alert watchlist needs. Don't conflate the two — `exports/universe_metadata.json` is the generic contract; `ticker_metadata.json` is sigma-alert's checked-in input.

**Stage 2 follow-up (deferred):** the sigma-alert ETF list should eventually move into the sigma-alert repo itself, with sigma-alert reading `Coverage Manager/exports/universe_metadata.json` directly and applying its own augmentation. That eliminates the cross-repo coupling and lets Coverage Manager publish only generic artifacts. Tracked as a TODO comment in `reporting/sigma_export.py`.

## Operational status semantics

Step statuses fall into three buckets:

- **Success**: `"ok"`, `"unchanged"`, or any deliberate operator skip (`"skipped"`, `"skipped (dry run)"`, `"skipped: <reason>"`)
- **Failed**: status starts with `"failed:"` — the step raised an exception. Recorded in `run_log.csv` `steps_failed` column.
- **Blocked**: status starts with `"blocked:"` — the step was prevented from running by a gating decision (e.g. `weekly-build` gating `weekly_report` on `validation_passed=False` without `--force`). **Blocked is non-success.** A blocked report run produced no report; that's operationally identical to a failure for monitoring purposes. Recorded in `run_log.csv` `steps_failed` column alongside failed steps; the prefix in the status string preserves the distinction for debugging. Slack icons differ: `:x:` for failed, `:no_entry:` for blocked.

The wrapper logs `"completed successfully"` only when **zero** steps are non-success (failed or blocked). Use `pipeline_utils.collect_non_successes(steps)` for any rollup logic.

## Sibling projects
- `../sigma-alert/` — GitHub Actions stock screener that consumes `ticker_metadata.json`, `portfolio.json`, `researching.json`, `following_for_interest.json`, `ready_to_buy.json`, `ready_to_short.json`, and `core_watchlist.json` (deprecated, one cycle) from Coverage Manager. The weekly-build `sigma-export` step writes all seven files directly into the sigma-alert clone and pushes them in a single commit. See `reporting/sigma_export.py`. The two `ready_to_*` files are pushed in advance of the deferred price-target alerter — they carry the trigger price levels the alerter will need.
  - **On-demand refresh**: `python cli.py sigma-export` pushes the four files immediately without running the full universe pipeline. Use this after a taxonomy / Sector (JP) / Core flag change so sigma-alert isn't stuck on a stale snapshot until the next Friday cron. `--no-push` commits locally only.
  - **Auto-rebase before push** (added 2026-04-29): sigma-alert's CI cron jobs commit cache updates to its `origin/master`, so `sigma_export.export_and_push` does `git fetch origin <branch>` + `git rebase origin/<branch>` on the local clone before writing files. If the rebase fails (uncommitted local edits in the sigma-alert clone, or a merge conflict on a tracked file), the step returns `failed:` and `weekly_universe` flags it as `:x:` in the Slack run summary. Do not commit local edits inside the sigma-alert clone unless they are intentional — the next sigma_export will refuse to run. The historical failure that motivated this is documented in memory `project_sigma_alert_core_watchlist_missing.md`.

## Provider architecture (fundamentals)

Fundamentals fetching uses a **provider chain** (`providers/provider_chain.py`) that coordinates fallback and field-level merging:

```
PROVIDER_PRIORITY (config.py, env-overridable)
├── "yf_first"  (DEFAULT) → yfinance → FMP → AlphaVantage
└── "fmp_first"           → FMP → yfinance → AlphaVantage
```

- **yfinance** (`providers/yfinance_provider.py`): Single `Ticker.info` call per ticker. This is now the default primary because it is materially faster on full-universe runs.
- **FMP** (`providers/fmp_provider.py`): Progressive endpoint strategy — profile + ratios-ttm (2 calls always), key-metrics-ttm only if EV/Net Debt/EV/S/ROE still missing. `financial-growth` is skipped (402 on Starter tier). Rate limited at 300 calls/min. Used as fallback by default, or as primary only when you explicitly set `PROVIDER_PRIORITY=fmp_first`.
- **AlphaVantage** (`providers/alphavantage_provider.py`): OVERVIEW endpoint, last-resort fallback only.
- **Finnhub** (`providers/finnhub_provider.py`): TTM overlay for Rev Grw, EPS Grw, and PEG for US tickers. Free tier (60 req/min), so cold-cache refreshes can still be slow.

**Success rule**: Mkt Cap present AND at least one of (EV, Fwd P/E, EV/EBITDA, EV/S, Gross Mgn, Op Mgn, ROE, Rev Grw, EPS Grw). If primary returns partial, fields are merged from secondary without overwriting.

**Why the default changed**: the refactor had drifted into an expensive path where ordinary report runs effectively paid the FMP multi-endpoint fan-out across the whole universe. `yf_first` keeps the normal report path faster while preserving FMP as fallback and as an explicit comparison mode.

**Prices are NOT affected** — yfinance `batch_download_prices` remains primary for prices, with FMP historical as fallback for missing US tickers. `% 52Wk Hi` stays derived from price history.

**S&P 500 benchmark tab**: `reporting/generate.py` now builds the S&P 500 benchmark in price-only mode for speed. It still computes benchmark returns, but it does not do a second full fundamentals pull for the entire S&P 500 universe. Do not reintroduce benchmark fundamentals into the default report path unless you want a materially slower run.

**Timing log**: Each run appends step timings to `reports/performance_timing.jsonl` (JSONL, one entry per run).

**To force FMP-first for a comparison run**: Set env `PROVIDER_PRIORITY=fmp_first`. No code deleted — existing providers are still present as fallbacks.

## Delisted / recycled ticker check

`python cli.py check-delisted` probes yfinance for each universe ticker (via a lightweight `Ticker.info` pull, results cached for 7 days under `cache/identity/`) and flags rows that look delisted, acquired, or recycled to a non-equity instrument.

Flag rules (evaluated in this order):
- yfinance returns nothing (`.info` empty) → likely delisted
- **no recent price data** → likely delisted/renamed (or an extended halt). A price-recency probe pulls ~1mo of daily bars; if the most recent bar is older than `PRICE_STALE_DAYS` (10) the price feed is treated as dead. This is the reliable tell for a **clean acquisition / take-private**: Yahoo keeps the stale `.info` metadata (longName etc.) populated for months, so the `.info`-empty and name-similarity rules miss these — but the price feed goes empty immediately. Added 2026-06-13 after EXAS (Abbott), HOLX (Blackstone/TPG), and the MPW→MPT / GMRE→XRN rebrands lingered in the universe for months. Robustness: the probe uses `history(raise_errors=True)` so a transient 429/network error becomes a *skipped* probe (counted as `price_probe_failures`, surfaced in the report) rather than a false "delisted" flag; the stale/not-stale decision is **frozen at probe time** into the cached `price_stale` field so a cached `last_close_date` can't "age into" staleness within the 7-day identity-cache TTL.
- `quoteType` is `ETF`, `MUTUALFUND`, `INDEX`, `CURRENCY`, or `CRYPTOCURRENCY` → ticker has been recycled
- Normalized fuzzy similarity between the universe `Company Name` and yfinance `longName`/`shortName` falls below 0.55 → ticker may have been recycled to a different issuer

Outputs (in `reports/`, archived weekly):
- `delisted_check_YYYY-MM-DD.csv` — flagged rows with reason
- `delisted_check_YYYY-MM-DD.md` — human-readable summary

The check is **non-gating** — it never blocks the report or the published artifacts. After confirming a flag is real, the user manually:
1. Removes the row from `data/coverage_universe_tickers.csv`
2. Appends an entry to `data/delisted_tickers.csv` with the last-known sector + market cap (the `Last Mkt Cap (USD)` / `Last Price` can be pulled from the most recent `cache/fundamentals/yf_<TICKER>.json` before clearing it)

The check runs as step `[4/6]` of `weekly-universe`. CLI exit code is `2` when at least one flag is raised.

## Ticker-change / deregistration discovery

`python cli.py check-ticker-changes` is the **companion** to `check-delisted`. Where the delisted check answers *"is this ticker dead?"* (yfinance price feed), this answers *"what symbol does SEC now have for this company?"* — so a renamed name can be **remapped** to the new symbol instead of just removed (the MPW→MPT / GMRE→XRN case).

**Discovery path:** SEC EDGAR's bulk `company_tickers.json` (same file `enrich.py` uses). A company's **CIK is stable across a ticker change** — only the symbol moves. The module builds the reverse map `CIK → {current ticker(s), title}` and, for each universe row with a CIK:
- SEC's ticker for that CIK differs from the universe ticker → a **mismatch** (candidate change), reported with SEC's symbol(s) + title.
- CIK absent from the bulk file → a **deregistration candidate**, then **confirmed** against the authoritative per-CIK submissions endpoint (the bulk file omits ~many active names, so absence alone is too noisy): flagged only when submissions has **no live ticker** OR the last filing is a **Form 15** (`15-12B/12G/15D` = filed deregistration, which the `tickers` field lags by weeks). A bulk-absent CIK that submissions confirms is still active is dropped (counted as `active_omissions`). First live run: 14 confirmed delistings (CFLT→IBM, APLS→Biogen, FOLD→BioMarin, SEMR→Adobe, …) with 4 active bulk-omissions correctly dropped.

**Why it's a review list, not an auto-fix:** SEC's structured ticker data can *lag* a real-world rebrand — it still lists the retired `FISV` long after Fiserv moved to `FI`, on **both** the bulk file and the per-CIK submissions endpoint — and yfinance can't disambiguate either (Yahoo aliases the retired symbol to the live one). There is no automated authority that reliably says which symbol is current, so the check surfaces the mismatch with full context and a human decides direction. A best-effort per-CIK **`formerNames`** lookup (SEC submissions, only for the few mismatch candidates) flags entities that legally renamed — a strong "real change" tell (e.g. `GALAPAGOS NV → Lakefront`, GLPG→LKFT). A matching SEC title with empty former-names leans toward SEC-file lag (leave the row as-is).

**Scope:** only rows with a CIK; mismatch detection gated to plain US-style symbols (`ABT`, `BRK.B`) so a cross-listed row tracking the foreign line (`DIA.MI`) isn't flagged as "changed to the US ADR." The SEC bulk map is cached 24h (`cache/sec_company_tickers/`).

Outputs (in `reports/`, archived weekly): `ticker_change_check_YYYY-MM-DD.{csv,md}`. Non-gating. Runs as step `[4b/6]` of `weekly-universe` (right after `delisted_check`). CLI exit code is `2` when any mismatch or deregistration is flagged. Module: `universe/ticker_change_check.py`; tests: `tests/test_ticker_change_check.py`.

## LEI (Legal Entity Identifier) backfill

`python cli.py backfill-lei [--no-cache] [--limit N]` fills the universe's **`LEI`**
column (just after `CIK`) from **GLEIF**'s free API (`api.gleif.org`, no key),
keyed by **ISIN**. The LEI (ISO 17442) is the official cross-provider *entity*
identifier — complements the ISIN/FIGI *security* IDs already carried — so the
ticker list can be joined to any LEI-keyed regulator/provider dataset.

- Only rows with an ISIN and a blank LEI are looked up; results (including
  authoritative "no LEI" answers) cached 90 days under `cache/lei/` → reruns are
  cheap and only chase still-missing rows. Foreign names (no CIK) still get an LEI
  here since ISIN is global.
- **Coverage ceiling ~46%** (337/731 ISIN rows as of 2026-06-16): GLEIF's
  ISIN→LEI *mapping* is issuer-contributed and incomplete even for US names (the
  entities all have LEIs; the ISIN link just isn't published). A confirmed rerun
  showed the misses are real gaps, not rate-limit transients. **To lift coverage**
  (deferred): add a GLEIF entity-*name* search fallback for the misses (carries
  name-match false-positive risk, so gate it carefully).
- Non-gating, additive: writes the CSV column (also surfaced in `exports/universe.csv`
  via the snapshot); `universe_metadata.json` is unchanged (no schema bump).
  Module `universe/lei_backfill.py`; tests `tests/test_lei_backfill.py`. Not yet
  wired into the weekly pipeline — run on demand (or add as a weekly step later).

## IPO date backfill (Renaissance Capital)

`python cli.py ipo-backfill [--no-cache] [--limit N] [--min-year YYYY] [--include-foreign]`
fills three **immutable** columns just after `Year Listed` — **`IPO Date`** (verified
offer date, ISO), **`Est Lockup 90d`**, **`Est Lockup 180d`** (offer + 90/180d; the API
has no lockup field) — from **Renaissance Capital**'s free IPO endpoint
(`api.renaissancecapital.com/free/CompanyIpoDate`, header `Ocp-Apim-Subscription-Key`,
key `RENAISSANCE_API_KEY` in `.env`).

- **Why, not just the date:** yfinance/FMP often report the *first-trade* or
  *listing-transfer* date for SMID names, not the offer — this is the clean verifier
  for the recent SMID HC IPOs that matter. The routing signal is IPO **age**, computed
  on read by `providers.renaissance_ipo.ipo_age(offer_date)` → `(age_days, bucket)`
  (`<30d/30-90d/90-180d/180-365d/1-2y/>2y`); never stored (it's date-relative). Lockup
  dates + IPO date are immutable so they live in the CSV.
- **Hard quota guard:** the FREE tier is **120 calls/MONTH** (no remaining-count header),
  so `providers/renaissance_ipo.py` tracks spend in `cache/ipo_renaissance/_usage.json`
  keyed by month and **raises `RenaissanceBudgetError` at `MONTHLY_CALL_CAP` (115)** —
  the backfill stops cleanly and reports `budget_exhausted`. IPO dates are immutable, so
  results (incl. authoritative 404 "no IPO on record") are cached ~forever under
  `cache/ipo_renaissance/<TICKER>.json` and a resolved/known-empty ticker is never re-hit.
- **Targeting (avoids wasting the tiny quota):** by default only rows **with a CIK**
  (US filers — Renaissance is US-IPO-only; foreign no-CIK rows always 404) are looked up,
  **most-recently-listed first**. `--min-year 2024` restricts to the last ~2 years;
  `--include-foreign` lifts the CIK requirement (rarely useful). Always use `--limit` to
  cap a run. Prefers the `?CIK=` query (the API's reliable key) over `?TickerSymbol=`.
- Non-gating, additive: writes the CSV column (surfaced in `exports/universe.csv` via the
  snapshot on the next weekly run); `universe_metadata.json` is unchanged (**no schema
  bump**). Degrades loudly (logs a warning, no-ops) if `RENAISSANCE_API_KEY` is unset.
  Module `universe/ipo_backfill.py` + `providers/renaissance_ipo.py`; tests
  `tests/test_ipo_backfill.py` + `tests/test_renaissance_ipo.py`. **Not wired into the
  weekly pipeline** — run on demand (like `backfill-lei`); wiring it as a weekly step
  (+ a fresh-discovery hook) is the obvious next increment.

## Historical valuation columns (Phase 1)

The weekly performance report includes 13 trailing-valuation columns appended after the existing FUND_COLS, populated only for the **Phase 1 universe** = every name with a personal trading-state relationship: `Position ∈ {Portfolio, Researching, Following for Interest, Ready to Buy, Ready to Short}` from `data/positions_and_researching.csv` (read from `exports/portfolio.json` + `exports/researching.json` + `exports/following_for_interest.json` + `exports/ready_to_buy.json` + `exports/ready_to_short.json` at report time). Trigger-ready states are included because they already carry a completed thesis; Following-for-Interest is included because earnings-season context benefits from the same historical-valuation columns. Tickers outside Phase 1 render as `N/A`.

### Columns (in order)

| Column | Source | Format |
|---|---|---|
| P/E (TTM) | FMP `/stable/ratios-ttm` `priceToEarningsRatioTTM` | float, 1dp |
| P/E 5Y Avg | mean of FMP `/stable/ratios?period=annual&limit=5` `priceToEarningsRatio` | float, 1dp |
| P/E 5Y +1σ | avg + sample stdev (n-1) | float, 1dp |
| P/E 5Y -1σ | avg − sample stdev | float, 1dp |
| P/E 5Y Min / Max | min/max of the 5Y series | float, 1dp |
| P/E vs 5Y Avg | (TTM − avg) / avg × 100 | percent; **red = premium, green = discount** (inverted vs return colors) |
| EV/S 5Y Avg / +1σ / -1σ / Min / Max | from FMP `/stable/key-metrics?period=annual` `evToSales` | float, 1dp |
| EV/S vs 5Y Avg | (existing TTM `EV/S` column − avg) / avg × 100 | percent; same red/green semantics |

### Why a new "P/E (TTM)" column

The pre-existing "Fwd P/E" column is **inconsistent** across providers:
- yfinance puts `forwardPE` (NTM, forward) → label "Fwd P/E (NTM)" is correct
- FMP puts `priceToEarningsRatioTTM` (trailing) → label is wrong for FMP-sourced rows

Comparing forward to a 5-year trailing average is apples-to-oranges, so the Phase 1 feature adds a separate "P/E (TTM)" column populated **always from FMP** regardless of which provider was primary. EV/S TTM is consistent across providers (yfinance `enterpriseToRevenue` and FMP `priceToSalesRatioTTM` are both trailing), so no new EV/S TTM column was needed.

### Caching

- Namespace: `cache/key_metrics_history/<TICKER>.json`
- TTL: 30 days (annual fundamentals change slowly; weekly re-fetches would be wasteful)
- Schema: `{pe_ttm, pe_history[5], evs_history[5], record_dates[5]}` — most-recent-first, padded with None to length 5

### Performance

Cold cache: ~3 FMP calls per Phase 1 ticker (annual ratios + annual key-metrics + ratios-ttm). Phase 1 has ~50–100 tickers, so ~150–300 calls at 300/min = 30–60 sec one-time, then 30-day-cached.

### Phase 2 (deferred)

- HTML report rendering (`reporting/html.py` iterates `FUND_COLS`, doesn't include `HIST_COLS`)
- Expand to full universe / Core flag once formatting is validated

## P/E vs forward-2yr-EPS-growth scatter (Phase 1)

The performance run renders a scatter of **P/E (TTM)** (y) vs **annualized forward 2-year EPS growth** (x) for the Phase 1 set (positions ∪ research), written to `reports/coverage_pe_vs_growth_<date>.png` and attached to the performance email. Built as `run_step("pe_growth_chart")` in `reporting/generate.py` after `result_df`.

- **Y-axis = P/E (TTM)** — reuses the FMP-sourced `P/E (TTM)` HIST column (currency-consistent), NOT the provider-inconsistent `Fwd P/E`.
- **X-axis = forward 2yr EPS-growth CAGR** — new data source `providers/fmp_estimates.py` (`/stable/analyst-estimates?period=annual`, verified on the FMP **Starter** tier 2026-06-13; legacy `/api/v3` 403s). Phase-1-scoped, 30-day cache (`cache/analyst_estimates/`), parallel fetch — same scope/cadence rationale as `fmp_history.py`. The CAGR math is the pure, unit-tested `reporting/calcs.forward_2yr_eps_growth_pct(records, today)`: FY0 = first estimate with fiscal year-end ≥ today, FY+2 = two fiscal years later, `(eps_FY+2/eps_FY0)**0.5 - 1`; returns None for <3 forward years or non-positive EPS (CAGR through zero/negative is undefined — those names just drop off the chart).
- **Rendering** is thin/side-effect-only in `reporting/charts.py` (matplotlib Agg, added to `requirements.txt`): dots sized by market cap, colored by Sector (JP), labeled with ticker, median guide-lines marking the cheap/expensive × low/high-growth quadrants.
- **S&P 500 is intentionally excluded** — the benchmark tab is built price-only (no fundamentals, no P/E) to keep the run fast; a 500-name fundamentals pull is the expensive path the architecture avoids. Portfolio/Phase-1 only.
- Internal report artifact only — does **not** touch the `exports/` contract. Tests: `tests/test_pe_growth_chart.py`.

## Movers report

`python cli.py movers` flags tickers in the coverage universe with extreme weekly performance and pulls a "why" summary for each. The report consumes the performance snapshot pickle written by `cli.py performance` (under `cache/perf/perf_df_<date>.pkl`) — it does **not** re-fetch prices.

### Flagging rule

A ticker is flagged if **either** condition fires:
- `|1W return| >= MOVERS_ABS_THRESHOLD_PCT` (default 10.0%), or
- `|z-score of 1W vs Sector (JP) cohort| >= MOVERS_ZSCORE_THRESHOLD` (default 2.0), provided the cohort has at least `MOVERS_MIN_PEER_COUNT` (default 5) peers — smaller cohorts skip the z-score and only the absolute threshold applies.

Flagged tickers are sorted by `|1W|` descending and capped at `MOVERS_MAX_FLAGGED` (default 30) before enrichment.

### Enrichment

For each flagged ticker:
1. **Finnhub `/company-news`** is queried for the past 7 days (free tier; cached 24h under `cache/news/`).
2. **Anthropic Claude Haiku 4.5** writes a 2-3 line "why" summary from the headlines via `providers/anthropic_summary.py`. The system prompt has a `cache_control` breakpoint so it caches across calls in a single run. Falls back to a headline list if `ANTHROPIC_API_KEY` is missing or the API errors.

### Outputs

- `reports/coverage_movers_<date>.html` — Table view with company, sector, 1W move, z-score, trigger, why, and headlines drilldown.
- `reports/coverage_movers_<date>.md` — Same content as markdown.
- Slack post to `SLACK_WEBHOOK_URL` (`#stock-price-alerts`) with top-10 movers.

### Wiring

- **Standalone:** `python cli.py movers` (also accepts `--date`, `--no-news`, `--no-slack`).
- **Weekly pipeline:** `weekly_report._step_movers()` runs after `_step_performance()` and before `_step_email()`. The email step picks up `coverage_movers_<date>.html` automatically via the existing glob — both the perf reports and the movers HTML go in one email.
- **Standalone perf no longer auto-emails when called from the orchestrator:** `generate.main()` accepts `skip_email=True`; `weekly_report` passes it so the orchestrator owns email delivery and the movers HTML is included.

### Configuration

Tunable via env or `config.py`:

| Variable                       | Default                | Purpose                                      |
|--------------------------------|------------------------|----------------------------------------------|
| `MOVERS_ABS_THRESHOLD_PCT`     | `10.0`                 | Absolute % threshold                         |
| `MOVERS_ZSCORE_THRESHOLD`      | `2.0`                  | Sector-cohort z-score threshold              |
| `MOVERS_MIN_PEER_COUNT`        | `5`                    | Minimum cohort size to compute z-score       |
| `MOVERS_MAX_FLAGGED`           | `30`                   | Cap on flagged tickers (LLM call budget)     |
| `MOVERS_LLM_MODEL`             | `claude-haiku-4-5`     | Anthropic model for "why" summaries          |
| `ANTHROPIC_API_KEY`            | (unset)                | Required for "why" summaries; degrades cleanly if absent |
| `FINNHUB_API_KEY`              | (existing)             | Reused for `/company-news`                   |

## Thematic baskets

`python cli.py baskets` builds a thematic-basket returns table (JP's 2026-07-08 ask) into
`reports/thematic_baskets_<perf-date>.md`. Reads the **latest** `cache/perf/perf_df_<date>.pkl`
(same snapshot the movers report uses — no re-fetch); each row already carries `Mkt Cap`,
`Sector (JP)`, and per-period returns (`1W`→WTD, `QTD`, `YTD`, calendar-year `2025`). For each
basket it reports member count + **equal-weighted** and **market-cap-weighted** returns per
period. Module: `reporting/thematic_baskets.py`; tests: `tests/test_thematic_baskets.py`.

**Basket membership is a curated judgment call** (the scoping JP invited) — edit the `BASKETS`
dict in `reporting/thematic_baskets.py`. v1 baskets: AI Trade, GLP-1 Winners/Losers, Obesity,
Alzheimer's, MRD, Oncology. Themes span sectors so they're explicit ticker lists, not a
Sector/Subsector filter. Intended names outside the coverage universe are kept in the lists and
reported as "not in universe" so gaps are visible (e.g. much of the AI trade is outside CM's
HC-focused coverage — a candidate for watchlist adds). Additive/manual — NOT wired into the
weekly pipeline and does not touch the `exports/` contract; output goes to gitignored `reports/`.

## Source cross-check workflow

Use `python cli.py cross-check` to run a separate source-validation pass without generating reports. This exists because "is the report producible?" and "do the providers agree?" are different questions.

- Entry point: `source_validation.py`
- CLI: `python cli.py cross-check` or `python cli.py cross-check --sample`
- Outputs:
  - `reports/source_crosscheck_YYYY-MM-DD.csv`
  - `reports/source_crosscheck_YYYY-MM-DD.json`

What it does:

- Deduplicates and normalizes the coverage universe once, using the same ticker normalization rules as reporting
- Pulls overlapping fields from `yfinance`, `FMP`, and `Finnhub` where available
- Computes either relative deltas or absolute deltas depending on the field
- Flags large disagreements using per-field thresholds

Important comparison rules:

- Monetary fields (`Price`, `Mkt Cap`, `Enterprise Value`, `Net Debt`) are not compared across mismatched currencies. That is intentional to avoid false positives from provider unit differences.
- Finnhub is mainly used for overlapping growth and PEG fields.
- The cross-check is diagnostic only; it does not gate report generation.

## Key conventions
- Sector classification uses `Sector (JP)` and `Subsector (JP)` columns (user-defined taxonomy)
- Market cap, EV, and Net Debt are converted to USD at report time
- Price stays in local currency
- Performance reports are emailed and posted to Slack `#stock-price-alerts` via `SLACK_WEBHOOK_URL` in `.env`
- `--refresh` flag bypasses cache reads and refetches from APIs. Avoid it on full runs unless you really need it; provider latency, especially Finnhub on cold cache, is still the main runtime cost
- The weekly scheduled task runs via `C:\Users\jroyp\run_weekly_coverage.bat` every Friday at 8am (uses `--dangerously-skip-permissions` for unattended execution). **2026-06-29 hardening:** the bat runs a **deterministic exports-publish backstop** (`"%PYTHON%" cli.py weekly-universe --skip-discovery`) UNCONDITIONALLY after the headless claude session, because on 2026-06-26 the headless `claude -p` session backgrounded the build and exited (no re-invocation in `-p` mode), leaving `exports/manifest.json` 10 days stale while the task showed rc=0. The backstop guarantees exports regenerate regardless of what the agent did; `weekly_coverage_prompt.md` also carries a CRITICAL rule forbidding backgrounding the build. The backstop, `git commit`, and `git push` each capture their exit code and `goto` a fail-label (`endlocal & exit /b <rc>`) so a failed publish/commit/push turns the task **RED** instead of green-but-stale (Codex-reviewed 2026-06-29). **2026-07-02:** added a second **deterministic performance-report backstop** — after the exports publish + git push the bat runs `"%PYTHON%" cli.py performance` UNCONDITIONALLY, because the full consolidated coverage report (`coverage_consolidated_*.html` + per-segment HTML + xlsx) is produced ONLY by the reporting-side performance step, which **no scheduled task ran** — so it silently went stale after 2026-05-29 while the Monday `WatchlistMondayReport` kept the positions-only `watchlist_report_*.html` fresh. It runs *after* git push so a transient provider hiccup never blocks the critical exports contract; `reports/` is gitignored so nothing is committed; `EMAIL_ENABLED=False` so no email is sent (`cli.py performance` honors the flag). It captures `PERF_RC` and `goto perffail` (RED) on failure so it can't silently drift again. Runtime ~17min (Finnhub cold-cache 62s rate-limit pauses dominate). Note: `weekly_coverage_prompt.md` line 230 still names a dead `generate_performance.py` (harmless now that the bat backstop covers it — fix on next prompt edit). Keep that bat **CRLF + ASCII + goto-style (no paren blocks)**.
- Performance report emails include weekly coverage additions summary + attached files list when `weekly_coverage_universe_additions_{date}.md` exists in `reports/`

## Weekly universe delta -> Slack #coverage

Each `weekly-universe` (and therefore `weekly-build`) run posts a single message to Slack `#coverage` summarizing what changed in the coverage universe this week. **Section order (changed 2026-07-04 per JP): header → optional caveat → Week over week (Added / Removed / Modified / Position changes — the diffs LEAD the post; an empty week renders an explicit `*Week over week:* _No changes this week._` line up top) → After (current state) → Before (last-run context) → Year to date.** Diffs-first is deliberate — the WoW changes are the reason to read the post; the state blocks are context.

- **Year-to-date block**: aggregates the timestamped `.coverage/universe_delta_YYYY-MM-DD.json` files for the current calendar year (`load_ytd_delta_history` + pure `compute_ytd_summary`): summed adds/removes/modified-tickers/position-changes, plus net ticker drift (earliest run's before-total → latest run's after-total). Best-effort: a YTD failure logs a warning and the post ships without the block; omitted entirely when there's no history yet. Note the history only reaches back to 2026-05-29 (when the delta mechanism shipped), so "since" shows the first available run of the year until 2027. Same-day reruns overwrite their dated file, so YTD reflects the last run of each date.

- **Webhook**: `SLACK_WEBHOOK_COVERAGE`, resolved via `os.environ.get(...) or API_KEYS.get(...)` (real env var first, then `.env`). Mirrors the health-heartbeat pattern.
- **Baseline strategy (2-tier)**:
  1. **Snapshot files** (preferred): `.coverage/last_run_universe.csv` + `.coverage/last_run_positions.csv`, written at the end of every post-step *regardless of Slack outcome*. Next week's baseline reads from these. Independent of git — manual uncommitted edits between weekly runs are correctly captured in the next week's delta.
  2. **Git HEAD** (bootstrap fallback): only used when the snapshot files are missing (first run after this mechanism shipped, or snapshots manually deleted). When the git fallback is taken AND the working tree was dirty at run start, a caveat appears at the top of the Slack message so the user knows pre-existing local edits may appear in the diff.
- **Sequencing**: the post-step runs **after** `discovery`, `delisted_check`, `export_artifacts`, `export_watchlist`, and `sigma_export` so the diff captures every change made during the run and the totals quoted in the Slack post match what downstream consumers will read from `exports/`.
- **Lifecycle inside the post-step** (in order):
  1. Compute delta from baseline vs working tree.
  2. Write delta JSON to `.coverage/{last_universe_delta.json, universe_delta_YYYY-MM-DD.json}` — ALWAYS, regardless of Slack outcome. The position-change overflow message ("see fallback file") relies on this file always existing.
  3. Post to Slack `#coverage`.
  4. Write the run snapshot to `.coverage/last_run_*.csv` — ALWAYS. Next week's baseline must reflect this run's actual end state, independent of Slack success.
  5. **Send the [ClaudeFin] email alert (added 2026-07-06)** — a short WoW-counts + YTD summary emailed to JP via the shared `_shared/email_alert` helper (`reporting/email_alert_client.py` shim; subject `[ClaudeFin] Coverage Manager — Weekly universe delta — <date>`; pure formatter `format_universe_delta_email`). ADDITIVE to the Slack post and sent even when Slack failed (independent channel). This is **NOT** the old `EMAIL_ENABLED` full-report email — that stays flag-disabled (see "Email transport" below). Convention: root `CONVENTIONS.md` "Email alerts ([ClaudeFin])".
  6. If the Slack post and/or the email alert failed, raise `RuntimeError` (reasons joined). `pipeline_utils.run_step` records `failed: ...`, `collect_non_successes` catches it, and the health heartbeat reports `partial`. Non-gating — the universe CSV update is the real product.
- **Modified-field filter**: only changes in `Sector (JP)`, `Subsector (JP)`, `Sub-subsector (JP)`, `Core`, `Country (HQ)`, and ISIN (blank → non-blank only) appear in the "Modified" section. CIK / FIGI / Exchange Code / Currency are operational hygiene and excluded by design.
- **Position changes**: enumerated by ticker, bounded at 20 entries with an overflow indicator; full list is always in the fallback JSON.
- **Empty week**: still posts, with `_No changes this week._` between the After and Delta sections.

Module: `reporting/universe_delta.py`. Tests: `tests/test_universe_delta.py`.

## Email transport (currently OFF)

`config.EMAIL_ENABLED = False` disables the weekly performance-report email; the Slack #coverage post replaces it. Email is **not** deleted — flip `EMAIL_ENABLED = True` in `config.py` to re-enable, no other code changes required. **Distinct from this flag:** the weekly universe-delta step's short `[ClaudeFin]` alert email (see the delta section above) always sends — it's the fleet-wide alert convention, not the full-report transport, and re-enabling/disabling `EMAIL_ENABLED` does not affect it. Each reporting transport (email, Slack #coverage, Slack #stock-price-alerts movers, #status-reports health) is enabled/disabled independently. Revisit date: 2026-06-29 (comment in `config.py`).

`EMAIL_ENABLED` is honored by **both** the orchestrator (`weekly_report` / `weekly_build`) **and** the standalone `cli.py performance` command. The standalone path gates via `reporting/generate.email_skip_reason()` (added 2026-05-29). Before that fix, `cli.py performance` emailed unconditionally whenever Gmail creds were set — bypassing the flag — which caused surprise/duplicate sends. Each `cli.py performance` run still produces at most one email; re-running it for the same date with `EMAIL_ENABLED=True` sends again (see memory `project_perf_command_emails_regardless_of_flag`).

## Health reporting

Coverage Manager posts a v1 health heartbeat to Slack `#status-reports` at the end of every `weekly-build` run, per the workspace contract in `../HEALTH_REPORTING.md`. The heartbeat is **additional to** (not a replacement for) the existing project-specific Slack post that goes to `#stock-price-alerts`.

- **Cadence**: weekly, Friday 8am local (Windows Task Scheduler running `run_weekly_coverage.bat`).
- **Webhook**: read from env var `SLACK_WEBHOOK_STATUS_REPORTS`, falling back to a key of the same name in `.env`. If unset, the post is skipped and the payload is written to `.health/last_run.json` instead.
- **Status mapping** (per HEALTH_REPORTING.md §4.2):
  - Uncaught exception or `validation_passed=False` → `error` (universe broken; no usable downstream artifacts)
  - Universe valid, some report-side step `failed:` or `blocked:` → `partial` (universe usable; report didn't fully ship)
  - Clean run → `ok`
- **Try/finally guarantee**: the heartbeat fires even if `weekly_universe.main` or `weekly_report.main` raises an uncaught exception. The original exception still propagates after the heartbeat is emitted.
- **Reruns**: the spec uses `cycle` + `attempt`. For a manual rerun, set env var `HEALTH_ATTEMPT="2 (manual rerun after timeout)"` (or similar) before invoking `cli.py weekly-build`. Default is `"1"`.
- **Standalone runs of `weekly-universe` or `weekly-report` do NOT emit a heartbeat in v1** — only the combined `weekly-build` wrapper does. The Friday cron uses `weekly-build`, so this is fine. If standalone runs become regular, lift the heartbeat into the sub-orchestrators.
- **Dry runs do not post**: `--dry-run` skips both the project-specific Slack post and the health heartbeat.

Implementation: `reporting/slack.py` (`format_health_v1_message`, `post_health_v1`) + `weekly_build.py` (`_build_health_payload`, `_emit_health_heartbeat`, try/finally in `main`). Tests: `tests/test_health_reporting.py`.

## Universe CSV I/O — float-safe loader (2026-06-20)

Any code that reads `data/coverage_universe_tickers.csv` **and writes the whole file back**
MUST load it via `ticker_utils.read_universe_csv()` (`pd.read_csv(path, dtype=str,
keep_default_na=False)`), never a bare `pd.read_csv`. A bare read infers integer ID columns that
contain blank cells — `CIK` and `Year Listed` — as float64 (`1125376` → `1125376.0`), and the
subsequent `df.to_csv` persists the `.0` suffix. A `.0` CIK breaks the SEC/EDGAR lookups that
consume the column (`ticker_change_check`, `enrich`) and corrupts the **published**
`exports/universe.csv`. The full-file pandas writers all use the safe loader now:
`universe/add_exchanges.py`, `universe/cleanup.py`, `universe/enrich.py` (main), and
`discovery/candidates.py:commit_staged_candidates` (the weekly vector — float-ifies on any week
that commits an approved candidate); `lei_backfill.py` was already safe. Read-only readers
(`validation.py`, `weekly_universe._step_validate`, reporting/*) may stay bare. `_step_export_artifacts`
copies the master to exports via `shutil.copyfile` (faithful), so protecting the source writers
protects the export. Regression: `tests/test_universe_csv_roundtrip.py`. See `feedback_published_artifacts`.

## Case-only ticker collisions (validation warning, 2026-07-16)

The universe can carry two rows that collide only by CASE — e.g. `VCEL` + `VCEl` (a
data-entry typo that silently duplicates a company). `validate_no_duplicate_tickers` uses an
EXACT match and MISSES these, and `build_universe_metadata`'s later-row-wins then hides one
spelling — exactly how the Vericel dup lived unnoticed until the `notion_watchlist` sync (which
does a case-insensitive universe join) surfaced it. `validate_case_only_ticker_collisions`
(`universe/validation.py`, in `run_all_validations`) groups tickers by `.upper()` and **warns**
(never errors — must not gate the weekly build) on any group with 2+ distinct spellings. It is
deliberately narrower than the `normalization_collisions` the metadata builder tracks: legitimate
exchange dual-listings (`ROG` + `ROG.SW`) differ as raw strings, never group together under
`.upper()`, and are never flagged — so the check is false-positive-free on real dual-listings.
When a case-only warning appears in `universe_status.json`'s `validation_warnings`, dedup the two
rows at the source (keep the correctly-cased ticker; merge in the curated fields). VCEL/VCEl was
merged 2026-07-16 (commit `e1b0859`). Tests: `tests/test_validation.py`.

## Testing
Run `python -m pytest tests/ -q` before committing. All tests must pass.
