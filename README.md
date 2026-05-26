# Coverage Manager

Script-driven tooling for maintaining a coverage universe CSV, discovering new tickers, and generating weekly artifacts + Slack updates summarizing what changed.

## What It Does

- Maintains the master coverage universe at `data/coverage_universe_tickers.csv`
- Cleans, deduplicates, validates, and enriches identifiers
- Discovers new candidate tickers via an external Claude prompt and a staging workflow
- **Publishes a versioned, generic artifact contract under `exports/`** (schema v3) for downstream projects (forensic_triage, biotech_triage, idea_generation, 13F analyzer, sigma-alert, earnings_agent, analyst-days, sa-monitor, catalyst_watch) to consume
- Generates Excel and HTML performance reports segmented by `Sector (JP)` / `Subsector (JP)`
- **Posts a weekly After/Before/Delta summary to Slack `#coverage`** (`SLACK_WEBHOOK_COVERAGE`). The Friday email transport is currently off (`EMAIL_ENABLED = False` in `config.py`); revisit 2026-06-29.
- Posts a movers summary to `#stock-price-alerts` and a workspace-standard health heartbeat to `#status-reports`
- Orchestrates the whole thing as a `weekly-build` pipeline scheduled for Friday 8am, with separable `weekly-universe` and `weekly-report` subcommands for finer control
- Tracks positions and research candidates in `data/positions_and_researching.csv` across five `Position` states (Portfolio / Researching / Following for Interest / Ready to Buy / Ready to Short); per-state JSON artifacts published to `exports/` for sigma-alert + earnings_agent consumption

## Prerequisites

- Python 3.8+

## Setup

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows:**

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in this folder for API-backed enrichment and Slack delivery:

```env
FINNHUB_API_KEY=...
FMP_API_KEY=...
ALPHAVANTAGE_API_KEY=...
EDGAR_IDENTITY="Your Name your@email"
# Slack — three independent channels, three independent webhooks
SLACK_WEBHOOK_URL=...                   # #stock-price-alerts (movers + legacy summary)
SLACK_WEBHOOK_COVERAGE=...              # #coverage (weekly After/Before/Delta)
SLACK_WEBHOOK_STATUS_REPORTS=...        # #status-reports (workspace health heartbeat)
# Email — currently disabled via EMAIL_ENABLED=False in config.py
GMAIL_ADDRESS=...
GMAIL_APP_PASSWORD=...
```

Alpha Vantage is used as a fallback fundamentals provider when Finnhub/FMP do not return data. Slack posts are non-gating: if a webhook is unset, the project-specific channel post is skipped or written to a local fallback file (the `#coverage` post writes `.coverage/last_universe_delta.json`).

**Email transport (currently off).** `config.EMAIL_ENABLED = False` disables the weekly performance-report email; the `#coverage` Slack post replaces it. Each reporting transport (email, Slack `#coverage`, Slack `#stock-price-alerts`, Slack `#status-reports`) is enabled/disabled independently. Flip `EMAIL_ENABLED = True` to re-enable email — no other code changes required. Revisit comment: `# REVISIT EMAIL REPORTING: 2026-06-29`.

## Usage

Run everything from this directory:

```bash
python cli.py weekly-build              # Full Friday pipeline (universe + report wrapper)
python cli.py weekly-universe           # Just the universe-side pipeline (no perf reports)
python cli.py weekly-report             # Just the reporting-side pipeline
python cli.py performance               # Just generate reports
python cli.py performance --sample      # Quick preview using a small subset
python cli.py performance --refresh     # Bypass cache, refetch all data
python cli.py cross-check               # Cross-check overlapping fields across providers
python cli.py cross-check --sample      # Quick validation run on the sample set
python cli.py validate                  # Validate the coverage CSV
python cli.py cleanup                   # Dedup and normalize tickers
python cli.py enrich                    # Identifier enrichment (Finnhub/FMP)
python cli.py add-exchanges             # Populate Exchange column via yfinance
python cli.py cache-clear               # Clear all cached external data
python cli.py cache-clear --namespace fundamentals
python cli.py positions add TICKER --position Portfolio --sell 75 --notes "..."                 # Held position
python cli.py positions add TICKER --position Researching --buy 30 --notes "..."                # Building thesis to buy
python cli.py positions add TICKER --position "Following for Interest" --notes "..."            # Passive earnings tracking; no intent to trade
python cli.py positions add TICKER --position "Ready to Buy" --buy 25 --notes "..."             # Long thesis done; waiting for entry trigger
python cli.py positions add TICKER --position "Ready to Short" --sell 600 --notes "..."         # Short thesis done; waiting for entry trigger
python cli.py positions remove TICKER   # Remove from positions file
python cli.py positions list            # Print all positions (all five states)
python cli.py positions validate        # Validate (subset + Position enum + universe metadata)
python cli.py watchlist-report          # Generate the Monday positions performance report (legacy name; covers Portfolio + Researching)
```

Add `--verbose` for debug logs:

```bash
python cli.py --verbose performance --sample
```

### Weekly pipeline architecture

The weekly pipeline is split into two independently-runnable orchestrators with a thin wrapper that runs both. This decoupling lets the universe management half run without dragging the reporting half along — useful for downstream projects that consume the universe but don't need performance reports.

**`weekly-universe`** — universe management half (validate → archive → discovery → export-artifacts → sigma-export):

1. **validate** — schema/data checks on the coverage CSV; produces `validation_passed` boolean
2. **archive** — moves prior dated discovery markdown files into `reports/old reports/`
3. **discovery** — writes a discovery input JSON, then looks for `data/discovery_output_<DATE>.json` produced by running `weekly_coverage_prompt.md` in Claude. Validated candidates are staged; rows with `approved=true` are auto-committed
4. **export-artifacts** — writes the published artifact contract under `exports/` (see Exports section below)
5. **sigma-export** — writes `ticker_metadata.json` into the sibling `../sigma-alert/` clone and commits/pushes only that file. The screener loads it at startup so Slack alerts can show company names and sector tags. See `reporting/sigma_export.py`

**`weekly-report`** — reporting half (validate read-only → archive → performance → movers → email):

1. **validate** — read-only validation, informational only; gating belongs in the wrapper
2. **archive** — moves prior dated performance reports into `reports/old reports/`
3. **performance** — generates Excel + HTML reports
4. **movers** — flags extreme weekly movers and posts a top-N summary to `#stock-price-alerts`
5. **email** — sends HTML reports via Gmail. **Currently disabled** (`EMAIL_ENABLED = False` in `config.py`); the step reports `skipped: EMAIL_ENABLED=False`. Revisit 2026-06-29.

**Universe delta -> Slack `#coverage`** — post-step inside `weekly-universe`, runs after `discovery`, `delisted_check`, `export_artifacts`, `export_watchlist`, and `sigma_export`. Posts one Block Kit message to `#coverage` summarizing **After (current state) → Before (last-run context) → Delta (Added / Removed / Modified / Position changes)**. Baseline is a 2-tier snapshot mechanism (`.coverage/last_run_*.csv` preferred; git HEAD fallback with a dirty-tree caveat). Slack failures raise so the health heartbeat reports `partial`. See `CLAUDE.md` "Weekly universe delta -> Slack #coverage" for the full contract.

**`weekly-build`** — wrapper that runs `weekly-universe`, gates `weekly-report` on `validation_passed` (overridable with `--force`), merges results, posts a Slack summary to `#stock-price-alerts`, and writes a parent audit row. This is the entry point used by the Friday scheduled task; the CLI surface and `run_weekly_coverage.bat` are unchanged.

Flags: `--skip-discovery`, `--skip-performance`, `--skip-email`, `--dry-run`, `--force`.

The Windows scheduled task runs `C:\Users\jroyp\run_weekly_coverage.bat` every Friday at 8am.

**Operational status semantics.** Step statuses fall into three buckets:

- **Success**: `"ok"`, `"unchanged"`, deliberate skips (`"skipped"`, `"skipped (dry run)"`)
- **Failed**: `"failed: <reason>"` — the step raised an exception
- **Blocked**: `"blocked: <reason>"` — the step was prevented from running by a gating decision (e.g. validation failed and `--force` was not passed). **Blocked is non-success** — a blocked report run produced no report. Both failed and blocked appear in `run_log.csv`'s `steps_failed` column and in the wrapper's `non_successes` list. Slack uses `:x:` for failed and `:no_entry:` for blocked.

Use `pipeline_utils.collect_non_successes(steps)` for any rollup logic — never reverse-engineer the success state from the steps dict directly.

### Exports — published artifact contract

`exports/` is a versioned, **committed** interface for downstream projects. Files are committed to git so consumers get history, reproducibility, and rollback. The contract is **strictly generic**: artifacts describe the coverage universe and nothing else. Consumer-specific transforms belong in the consumer.

| File | Purpose |
|------|---------|
| `exports/universe.csv` | Snapshot of `data/coverage_universe_tickers.csv` |
| `exports/universe_metadata.json` | `{TICKER: {name, sector, subsector, sub_subsector, core}}` derived only from CSV rows |
| `exports/universe_status.json` | Versioned status + validation contract; **always read `schema_version` first** |
| `exports/positions_and_researching.csv` | Positions+researching list joined with universe metadata — every universe column followed by `Position`, `Position Date`, `Buy Price`, `Sell Price`, `First Buy Date`, `Average Cost`, `Shares`, `Notes`. |
| `exports/portfolio.json` | `{TICKER: {...}}` for `Position == "Portfolio"` rows only (names you own). |
| `exports/researching.json` | Same shape, `Position == "Researching"` rows only (active thesis work). |
| `exports/following_for_interest.json` | Same shape, `Position == "Following for Interest"` rows only (passive tracking). |
| `exports/ready_to_buy.json` | Same shape, `Position == "Ready to Buy"` rows only (long thesis done; waiting for entry). |
| `exports/ready_to_short.json` | Same shape, `Position == "Ready to Short"` rows only (short thesis done; waiting for entry). |
| `exports/positions_status.json` | Versioned status + validation contract for positions (separate schema). |
| `exports/watchlist.{csv,json,_status.json}` | **DEPRECATED** back-compat (one cycle), filtered to `Portfolio ∪ Researching` only. New code should use the five state-specific JSONs. |
| `exports/manifest.json` | Directory of files in `exports/` with their purpose |

`universe_status.json` (schema **v3**) includes `row_count`, `ticker_count`, `normalization_collisions`, `collision_examples`, `validation_passed`, `validation_errors`, `validation_warnings`, and `last_discovery_run`. v3 added the `core` field to per-ticker metadata. Invariant: `ticker_count + normalization_collisions == row_count`.

Read pattern for downstream projects:

```python
import json
from pathlib import Path

CM_EXPORTS = Path("../Coverage Manager/exports")
status = json.loads((CM_EXPORTS / "universe_status.json").read_text())
assert status["schema_version"] == 3, "Coverage Manager exports schema changed"
if not status["validation_passed"]:
    raise RuntimeError(f"Universe failed validation: {status['validation_errors']}")
metadata = json.loads((CM_EXPORTS / "universe_metadata.json").read_text())
```

The sigma-alert-specific `ticker_metadata.json` (in the sibling sigma-alert clone) is a **separate** artifact produced by `reporting/sigma_export.build_sigma_metadata`, which composes the generic builder with hardcoded sector ETFs. Don't conflate the two.

### Command details

| Command | Description |
|---------|-------------|
| `weekly-build` | Wrapper that runs `weekly-universe` then `weekly-report`, gates the report on validation, posts a combined Slack summary. |
| `weekly-universe` | Universe-side pipeline only: validate, archive, discovery, export-artifacts, sigma-export. |
| `weekly-report` | Reporting-side pipeline only: validate (read-only), archive, performance, email. |
| `performance` | Fetches price history and fundamentals, then produces Excel and HTML reports. |
| `performance --sample` | Generates a reduced preview using a small subset of tickers. |
| `performance --refresh` | Bypass cache and refetch all data. |
| `cross-check` | Runs a separate source validation pass across yfinance, FMP, and Finnhub, then flags large discrepancies to dated CSV/JSON outputs in `reports/`. |
| `validate` | Runs schema and data validation against `data/coverage_universe_tickers.csv`. |
| `cleanup` | Removes duplicates, normalizes ticker formatting, flags conflicts. |
| `enrich` | Adds identifier columns (ISIN, FIGI, CIK, etc.) from Finnhub and FMP. |
| `add-exchanges` | Resolves exchange names by suffix or yfinance lookup. |
| `cache-clear` | Clears cached API data; optional `--namespace` to clear one bucket. |
| `watchlist add/remove/list/validate` | Manage `data/watchlist.csv` — the core watchlist of tickers you own or are watching. `add` and `validate` enforce subset-of-universe and require non-empty `Company Name`, `Sector (JP)`, `Currency`, `Exchange` on the universe row. |
| `watchlist-report` | Generate the weekly Monday core watchlist report (HTML + Excel + email + Slack). |

## Output

### `performance`

Writes the following files to `reports/`:

- `coverage_performance_YYYY-MM-DD.xlsx` — full Excel workbook
- `coverage_biopharma_YYYY-MM-DD.html`
- `coverage_hc_svcs_medtech_YYYY-MM-DD.html`
- `coverage_other_YYYY-MM-DD.html`
- `coverage_sp500_non_hc_YYYY-MM-DD.html`

Each report contains price returns over multiple periods (1D, 1W, QTD, YTD, 1Y, 3Y, 5Y, 10Y, plus calendar-year annual returns) and fundamental metrics (Fwd P/E, EV/EBITDA, EV/S, PEG, margins, ROE, revenue and EPS growth). Market cap, EV, and Net Debt are converted to USD; price stays in local currency. ETF benchmark rows are included alongside ticker rows. Tickers are segmented by `Sector (JP)` / `Subsector (JP)` into Biopharma, HC Services & MedTech, Other, and a Non-HC S&P 500 benchmark tab.

Previous reports are archived to `reports/old reports/` automatically.

Runtime notes:

- The default fundamentals priority is now `yf_first` in `config.py`. This keeps normal report runs faster because yfinance is typically one call per ticker, while FMP often fans out into multiple endpoint calls.
- The S&P 500 benchmark tab is now built in price-only mode for speed. It still computes benchmark returns, but it does not run a second full fundamentals pass for the S&P 500 universe.
- `PROVIDER_PRIORITY=fmp_first` is still available when you explicitly want to bias toward FMP for a comparison run.

### `performance --sample`

Writes sample HTML files into `reports/samples/` for quick preview.

### `cross-check`

Writes the following files to `reports/`:

- `source_crosscheck_YYYY-MM-DD.csv`
- `source_crosscheck_YYYY-MM-DD.json`

This command is a separate data-validation pass, not part of report generation. It fetches overlapping fields from yfinance, FMP, and Finnhub where available, computes discrepancies, and flags large differences using per-field thresholds. Monetary fields are skipped when providers report different currencies so the output focuses on real disagreements rather than unit mismatches.

### `cleanup` / `enrich` / `add-exchanges`

These commands modify `data/coverage_universe_tickers.csv` in place. A timestamped backup is saved to `backups/` before each run. `cleanup` writes `cleanup_conflicts_YYYYMMDD.csv` if duplicate conflicts are found.

## CSV Columns

`data/coverage_universe_tickers.csv` is the central data file. Key columns include:

| Column | Description |
|--------|-------------|
| `Ticker` | Symbol with exchange suffix for non-US tickers (e.g. `000100.KS`) |
| `Exchange` / `Exchange Code` / `Exchange Full Name` | Exchange identifiers |
| `Company Name` | Company name (from yfinance) |
| `ISIN`, `FIGI`, `Composite FIGI`, `Share Class FIGI`, `CIK` | Security identifiers added by `enrich` |
| `Country (HQ)`, `Country (Listing)`, `Country (ISO)` | Country fields |
| `Currency` | Trading currency |
| `YF Sector` / `YF Industry` | Yahoo Finance classification |
| `Sector (JP)` / `Subsector (JP)` | Custom classification used for report segmentation |
| `Core` | Marks core coverage tickers |

## File Structure

```
Coverage Manager/
├── cli.py                       # CLI entry point
├── weekly_build.py              # Wrapper: runs weekly_universe + weekly_report
├── weekly_universe.py           # Universe-side orchestrator
├── weekly_report.py             # Reporting-side orchestrator
├── pipeline_utils.py            # run_step / collect_non_successes helpers
├── weekly_coverage_prompt.md    # Discovery prompt run in Claude
├── config.py                    # Paths, API keys, segment definitions
├── cache.py                     # Disk cache for external API responses
├── audit.py                     # Run-log audit trail
├── ticker_utils.py              # Shared ticker helpers
├── logging_utils.py             # Logging configuration
├── providers/                   # yfinance, Finnhub, FMP, Alpha Vantage, FX
├── reporting/                   # Excel, HTML, email, slack, sigma_export
├── universe/                    # validation, cleanup, enrich, add-exchanges, artifacts
├── discovery/                   # Candidate discovery and staging
├── tests/                       # pytest suite
├── data/
│   ├── coverage_universe_tickers.csv
│   ├── positions_and_researching.csv  # Positions + research candidates (5 Position states)
│   └── delisted_tickers.csv     # Archive: acquired / delisted / recycled rows
├── exports/                     # Published artifact contract (committed)
│   ├── universe.csv
│   ├── universe_metadata.json   # schema v3 (adds `core` field)
│   ├── universe_status.json
│   ├── positions_and_researching.csv
│   ├── portfolio.json
│   ├── researching.json
│   ├── following_for_interest.json
│   ├── ready_to_buy.json
│   ├── ready_to_short.json
│   ├── positions_status.json
│   ├── watchlist.{csv,json,_status.json}  # DEPRECATED back-compat
│   └── manifest.json
├── backups/                     # Timestamped CSV backups
├── cache/                       # Cached API data (gitignored)
├── .coverage/                   # Snapshot files + delta JSON (gitignored)
│   ├── last_run_universe.csv    # End of previous run; baseline for next run's diff
│   ├── last_run_positions.csv
│   ├── last_universe_delta.json # Most recent delta payload (stable filename)
│   └── universe_delta_YYYY-MM-DD.json  # Historical delta payloads
├── .health/                     # Health heartbeat fallback (gitignored)
├── reports/                     # Generated reports (gitignored)
│   ├── old reports/             # Archived previous runs
│   └── samples/                 # Sample previews
├── run_weekly_coverage.bat      # Windows scheduled-task entry point (Fri 8am)
├── run_watchlist_monday.bat     # Windows scheduled-task entry point (Mon 8am)
├── requirements.txt
└── .env                         # API keys (not committed)
```

## Testing

```bash
python -m pytest tests/ -q
```

All tests must pass before committing.
