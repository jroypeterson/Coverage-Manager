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
- `weekly_build.py` — Weekly pipeline orchestrator (validate → archive → discovery → performance → email → sigma-export → slack)
- `weekly_coverage_prompt.md` — Weekly coverage discovery prompt (run by scheduled task)
- `config.py` — All paths, API keys, segment definitions
- `data/coverage_universe_tickers.csv` — Master coverage universe
- `providers/` — External data sources (yfinance, Finnhub, FMP, AlphaVantage, FX)
- `reporting/` — Report generation (Excel, HTML, email, Slack, sigma_export)
- `universe/` — CSV validation, enrichment, cleanup
- `discovery/` — Candidate discovery pipeline
- `reports/` — Generated reports (gitignored)
- `reports/samples/` — Sample/preview reports
- `cache/` — Cached API data (gitignored)

## Sibling projects
- `../sigma-alert/` — GitHub Actions stock screener that consumes `ticker_metadata.json` from Coverage Manager. The weekly-build `sigma-export` step writes that file directly into the sigma-alert clone and pushes only that single file. See `reporting/sigma_export.py`.

## Key conventions
- Sector classification uses `Sector (JP)` and `Subsector (JP)` columns (user-defined taxonomy)
- Market cap, EV, and Net Debt are converted to USD at report time
- Price stays in local currency
- Performance reports are emailed and posted to Slack `#stock-price-alerts` via `SLACK_WEBHOOK_URL` in `.env`
- AlphaVantage is wired as a third fundamentals fallback after yfinance and FMP
- `--refresh` flag bypasses cache and refetches all data; threaded through `generate.main()` to all providers
- The weekly scheduled task runs via `C:\Users\jroyp\run_weekly_coverage.bat` every Friday at 8am

## Testing
Run `python -m pytest tests/ -q` before committing. All tests must pass.
