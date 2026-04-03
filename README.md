# Coverage Manager

Script-driven tooling for maintaining a coverage universe CSV and generating performance reports.

## What It Does

- Cleans and deduplicates the ticker universe
- Normalizes and enriches identifiers from external data sources
- Generates Excel and HTML performance reports
- Optionally emails the HTML report when Gmail credentials are present in `.env`

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

Create a `.env` file in this folder if you want API-backed enrichment or email delivery:

```env
FINNHUB_API_KEY=...
FMP_API_KEY=...
GMAIL_ADDRESS=...
GMAIL_APP_PASSWORD=...
```

## Usage

Run everything from this directory. The typical workflow order is:

```bash
python3 cli.py cleanup            # 1. Clean and deduplicate tickers
python3 cli.py enrich             # 2. Enrich with external identifiers
python3 cli.py add-exchanges      # 3. Populate Exchange column via yfinance
python3 cli.py performance        # 4. Generate full performance reports
python3 cli.py performance --sample  # (or generate a quick sample preview)
```

Add `--verbose` for more detailed logs:

```bash
python3 cli.py --verbose performance --sample
```

On Windows you can also double-click `run_weekly_coverage.bat` to run the `performance` command directly without activating the virtual environment manually.

### Command details

| Command | Description |
|---------|-------------|
| `cleanup` | Removes duplicates, normalizes ticker formatting, and flags conflicts in the coverage CSV. |
| `enrich` | Adds identifier columns (ISIN, FIGI, CIK, etc.) from Finnhub and FMP APIs. |
| `add-exchanges` | Resolves exchange names — first by ticker suffix, then via yfinance lookup for US tickers. |
| `performance` | Fetches price history and fundamentals, then produces Excel and HTML reports. |
| `performance --sample` | Generates a reduced preview using a small subset of tickers. |

## Output

### `cleanup` / `enrich`

These commands modify `coverage_universe_tickers.csv` in place. A timestamped backup is saved to `backups/` before each run. `cleanup` also writes `cleanup_conflicts_YYYYMMDD.csv` if duplicate conflicts are found.

### `performance`

Writes the following files to `reports/`:

- `coverage_performance_YYYY-MM-DD.xlsx` — full Excel workbook
- `coverage_performance_YYYY-MM-DD.html` — consolidated HTML report
- `coverage_biopharma_YYYY-MM-DD.html`
- `coverage_hc_svcs_medtech_YYYY-MM-DD.html`
- `coverage_pa_other_YYYY-MM-DD.html`
- `coverage_sp500_non_hc_YYYY-MM-DD.html`

Each report contains price returns over multiple periods (1D, 1W, QTD, YTD, 1Y, 3Y, 5Y, 10Y, plus calendar-year annual returns) and fundamental metrics (Fwd P/E, EV/EBITDA, EV/S, PEG, margins, ROE, revenue and EPS growth). Tickers are segmented by `Sector (JP)` / `Subsector (JP)` into Biopharma, HC Services & MedTech, PA & Other, and a separate Non-HC S&P 500 benchmark tab.

Previous reports are archived to `reports/old reports/` automatically.

### `performance --sample`

Writes `sample_preview.xlsx` and `sample_*.html` files to `reports/` for a quick preview.

## CSV Columns

`coverage_universe_tickers.csv` is the central data file. Key columns include:

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

## File Structure

```
Coverage Manager/
├── cli.py                         # CLI entry point
├── cleanup_tickers.py             # Dedup and normalization
├── enrich_identifiers.py          # Identifier enrichment (Finnhub/FMP)
├── add_exchanges.py               # Exchange column population
├── generate_performance.py        # Performance report generation
├── sample_html.py                 # Standalone sample HTML preview
├── ticker_utils.py                # Shared ticker helpers and constants
├── logging_utils.py               # Logging configuration
├── coverage_universe_tickers.csv  # Central ticker universe
├── requirements.txt               # Python dependencies
├── run_weekly_coverage.bat        # Windows shortcut for performance run
├── .env                           # API keys and Gmail credentials (not committed)
├── backups/                       # Timestamped CSV backups
└── reports/                       # Generated reports
    └── old reports/               # Archived previous runs
```

## Current Project Shape

This repository is still script-first rather than a packaged Python application. The CLI is a thin wrapper over the existing scripts so behavior stays stable while the project becomes easier to run and maintain.
