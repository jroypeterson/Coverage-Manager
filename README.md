# Coverage Manager

Script-driven tooling for maintaining a coverage universe CSV, discovering new tickers, and generating weekly performance reports that are emailed and posted to Slack.

## What It Does

- Maintains the master coverage universe at `data/coverage_universe_tickers.csv`
- Cleans, deduplicates, validates, and enriches identifiers
- Discovers new candidate tickers via an external Claude prompt and a staging workflow
- Generates Excel and HTML performance reports segmented by `Sector (JP)` / `Subsector (JP)`
- Emails reports via Gmail and posts a summary to Slack `#all-jp-personal-hub`
- Orchestrates the whole thing as a `weekly-build` pipeline scheduled for Friday 8am

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

Create a `.env` file in this folder for API-backed enrichment, email, and Slack delivery:

```env
FINNHUB_API_KEY=...
FMP_API_KEY=...
ALPHAVANTAGE_API_KEY=...
GMAIL_ADDRESS=...
GMAIL_APP_PASSWORD=...
SLACK_WEBHOOK_URL=...
```

Alpha Vantage is used as a fallback fundamentals provider when Finnhub/FMP do not return data. Slack posting is skipped silently if `SLACK_WEBHOOK_URL` is not set.

## Usage

Run everything from this directory:

```bash
python cli.py weekly-build              # Full Friday pipeline
python cli.py performance               # Just generate reports
python cli.py performance --sample      # Quick preview using a small subset
python cli.py performance --refresh     # Bypass cache, refetch all data
python cli.py validate                  # Validate the coverage CSV
python cli.py cleanup                   # Dedup and normalize tickers
python cli.py enrich                    # Identifier enrichment (Finnhub/FMP)
python cli.py add-exchanges             # Populate Exchange column via yfinance
python cli.py cache-clear               # Clear all cached external data
python cli.py cache-clear --namespace fundamentals
```

Add `--verbose` for debug logs:

```bash
python cli.py --verbose performance --sample
```

### Weekly build

`python cli.py weekly-build` runs:

1. **validate** — schema/data checks on the coverage CSV; hard errors block downstream steps unless `--force`
2. **archive** — moves prior dated reports into `reports/old reports/`
3. **discovery** — writes a discovery input JSON, then looks for `data/discovery_output_<DATE>.json` produced by running `weekly_coverage_prompt.md` in Claude. Validated candidates are staged; rows with `approved=true` are auto-committed to the universe CSV
4. **performance** — generates Excel + HTML reports
5. **email** — sends HTML reports via Gmail
6. **slack** — posts a build summary to the webhook URL

Flags: `--skip-discovery`, `--skip-performance`, `--skip-email`, `--dry-run`, `--force`.

The Windows scheduled task runs `C:\Users\jroyp\run_weekly_coverage.bat` every Friday at 8am.

### Command details

| Command | Description |
|---------|-------------|
| `weekly-build` | Runs the full pipeline (validate → archive → discovery → performance → email → slack). |
| `performance` | Fetches price history and fundamentals, then produces Excel and HTML reports. |
| `performance --sample` | Generates a reduced preview using a small subset of tickers. |
| `performance --refresh` | Bypass cache and refetch all data. |
| `validate` | Runs schema and data validation against `data/coverage_universe_tickers.csv`. |
| `cleanup` | Removes duplicates, normalizes ticker formatting, flags conflicts. |
| `enrich` | Adds identifier columns (ISIN, FIGI, CIK, etc.) from Finnhub and FMP. |
| `add-exchanges` | Resolves exchange names by suffix or yfinance lookup. |
| `cache-clear` | Clears cached API data; optional `--namespace` to clear one bucket. |

## Output

### `performance`

Writes the following files to `reports/`:

- `coverage_performance_YYYY-MM-DD.xlsx` — full Excel workbook
- `coverage_biopharma_YYYY-MM-DD.html`
- `coverage_hc_svcs_medtech_YYYY-MM-DD.html`
- `coverage_pa_other_YYYY-MM-DD.html`
- `coverage_sp500_non_hc_YYYY-MM-DD.html`

Each report contains price returns over multiple periods (1D, 1W, QTD, YTD, 1Y, 3Y, 5Y, 10Y, plus calendar-year annual returns) and fundamental metrics (Fwd P/E, EV/EBITDA, EV/S, PEG, margins, ROE, revenue and EPS growth). Market cap, EV, and Net Debt are converted to USD; price stays in local currency. ETF benchmark rows are included alongside ticker rows. Tickers are segmented by `Sector (JP)` / `Subsector (JP)` into Biopharma, HC Services & MedTech, PA & Other, and a Non-HC S&P 500 benchmark tab.

Previous reports are archived to `reports/old reports/` automatically.

### `performance --sample`

Writes sample HTML files into `reports/samples/` for quick preview.

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
├── weekly_build.py              # Weekly pipeline orchestrator
├── weekly_coverage_prompt.md    # Discovery prompt run in Claude
├── config.py                    # Paths, API keys, segment definitions
├── cache.py                     # Disk cache for external API responses
├── audit.py                     # Run-log audit trail
├── perf_data.py                 # Price and fundamentals fetching
├── ticker_utils.py              # Shared ticker helpers
├── logging_utils.py             # Logging configuration
├── providers/                   # yfinance, Finnhub, FMP, Alpha Vantage, FX
├── reporting/                   # Excel, HTML, email, slack
├── universe/                    # validation, cleanup, enrich, add-exchanges
├── discovery/                   # Candidate discovery and staging
├── tests/                       # pytest suite
├── data/
│   └── coverage_universe_tickers.csv
├── backups/                     # Timestamped CSV backups
├── cache/                       # Cached API data (gitignored)
├── reports/                     # Generated reports (gitignored)
│   ├── old reports/             # Archived previous runs
│   └── samples/                 # Sample previews
├── run_weekly_coverage.bat      # Windows scheduled-task entry point
├── requirements.txt
└── .env                         # API keys (not committed)
```

## Testing

```bash
python -m pytest tests/ -q
```

All tests must pass before committing.
