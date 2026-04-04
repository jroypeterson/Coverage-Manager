# Coverage Manager — Project Instructions

## Git sync
After making code changes, always commit and push to GitHub (`origin master`) before ending the conversation. Use descriptive commit messages.

## Project structure
- `cli.py` — CLI entry point
- `weekly_build.py` — Weekly pipeline orchestrator
- `weekly_coverage_prompt.md` — Weekly coverage discovery prompt (run by scheduled task)
- `config.py` — All paths, API keys, segment definitions
- `data/coverage_universe_tickers.csv` — Master coverage universe
- `providers/` — External data sources (yfinance, Finnhub, FMP, FX)
- `reporting/` — Report generation (Excel, HTML, email)
- `universe/` — CSV validation, enrichment, cleanup
- `discovery/` — Candidate discovery pipeline
- `reports/` — Generated reports (gitignored)
- `reports/samples/` — Sample/preview reports
- `cache/` — Cached API data (gitignored)

## Key conventions
- Sector classification uses `Sector (JP)` and `Subsector (JP)` columns (user-defined taxonomy)
- Market cap, EV, and Net Debt are converted to USD at report time
- Price stays in local currency
- Performance reports are emailed and also posted to Slack `#all-jp-personal-hub`
- The weekly scheduled task runs via `C:\Users\jroyp\run_weekly_coverage.bat` every Friday at 8am

## Testing
Run `python -m pytest tests/ -q` before committing. All tests must pass.
