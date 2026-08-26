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
- `scripts/post_coverage_to_ipo.py` — Posts the weekly additions report to Slack **#ipo-spinoffs-newissues** (`C0BKLC32EHK`; no webhook, so `chat.postMessage` with `SLACK_BOT_TOKEN` + `SLACK_IPO_CHANNEL_ID`). **As of 2026-08-09 the lead is the WHOLE Slack post**: framing, the auto-add report (`Added without asking` — moved into the lead because `sync_candidate_ledger` has always printed *"REPORT THESE IN THE SLACK POST… must be the most visible line in the report, not the quietest"*, and threading it made it exactly the quietest), Recommendations, and the pending-approval backlog. Every reference section — pipeline, listing-lane findings, exclusions, company briefings, files footer — is on the **published page** instead, and the lead **names the deferred titles** so *"it moved"* never looks like *"it vanished"*. `route()` still buckets every section, so `test_every_section_is_routed_exactly_once` is unchanged; the caller simply does not post two of the buckets. `--thread-reference` restores the old behaviour. The ONE piece of reference that stays in Slack is the unexplained-reversal warning — that is the failure that lost Jersey Mike's for two weeks, and it must never be able to disappear. Manual: `python scripts/post_coverage_to_ipo.py --date YYYY-MM-DD [--dry-run] [--preview] [--thread-ts TS] [--channel C…] [--no-reversal-check] [--thread-reference]`. Replaces hand-rolled payloads in the headless session, which twice delivered the report somewhere unread — see `COVERAGE_APPROVAL_PLAN.md`.

  **2026-08-17 — the lead is a scannable strip, and that is enforced in code.** JP on the 08-14 post: *"the format is too much blocks of text - needs to be better delineated or sectioned with bullets or tables. It needs to be formatted for speed readability first and then be able to delve into details. Don't add qualitative commentary like 'light week' be terse."* Three changes. (1) **`split_lead_detail`** — every `###` nested *inside* a lead section defers to the page. `split_sections` only ever broke on H2, so the 08-14 `## Added without asking` carried two ~700-word H3 essays (`### VOGX — why it qualifies…`, `### BSEM — the argument against`) straight into the channel message, burying the two-row table that *is* the decision. Detail was never the problem; detail **in the lead** was. (2) **`decisions` and `watch` joined `LEAD_SECTION_PREFIXES`** — the report top is now written as those two H2s (see `weekly_coverage_prompt.md` → "Report top"), and without the prefixes a correctly-written top routes to the page and the lead posts as a bare title. `watch` is a whole-prefix match, so `### Form 10 watch (…)` under Listing-lane findings is untouched — pinned by a test. (3) **`LEAD_SOFT_LIMIT`** (2,600 chars) prints a warning, never truncates: a silently cut lead would hide an auto-add, which is the one thing this post exists to make unmissable. Nothing is dropped by any of it — the page renders the whole report and the deferred titles are still named in the lead's link block. **The `[Agentic Investing]` title tag is a Gmail-draft filter prefix, NOT a project reference** (`agentic_trading` is unrelated and does not touch this lane); it reached Slack only because this prompt used to require the report H1 to match the email subject. Decoupled — the H1 is untagged, and `weekly_page.py`'s masthead eyebrow (which hardcoded it in two places) reads `Coverage Manager · discovery lane`. The Gmail draft moved to the fleet convention the same day: **`[ClaudeFin] Coverage Manager — Weekly Coverage Universe Additions — <date>`** (`CONVENTIONS.md` §5), which the 2026-07-13 fleet audit had left as the last opt-out. It is hand-built via IMAP, not through `_shared/email_alert`, so the prompt writes the whole subject. Also deleted `scripts/_tmp_slack_post.py` — a tracked one-off with a year-old report body hardcoded that posted to `SLACK_WEBHOOK_URL`, i.e. **#stock-price-alerts**, the exact mis-delivery `post_coverage_to_ipo.py` exists to prevent. The Gmail-draft one-offs (`_make_draft*.py`, `_tmp_gmail_draft.py`) stay. **`--update-ts TS` rewrites an already-posted lead in place** via `chat.update` and returns before the briefings loop — the thread they hang under is unchanged, so re-posting them would double every company write-up. Used on 2026-08-17 to rewrite the live 08-14 post (`1786711699.833639`). When you use it, **re-render the page too** (`cli.py weekly-page --date …`): the deferred sections have to exist where the lead says they are.
- **The weekly report is a published web page: <https://jroypeterson.github.io/Coverage-Manager/>** — `reporting/weekly_page.py`, rendered by `cli.py weekly-page` and by the `weekly_page` step of `weekly-universe`, written into `docs/` and served by GitHub Pages off `master`. Bookmarked in Slack **#ipo-spinoffs-newissues** and **#coverage** (`scripts/add_page_bookmarks.py`, idempotent by title). `index.html` is always the newest week; `weekly/<date>.html` is the archive and `archive.html` the index.

  **Why a page.** JP 2026-08-08, looking at an eleven-reply thread: *"the way you have all of the replies threaded is kind of confusing since there are so many questions… I think having a published clickable html page that refreshes weekly that is nicely formatted would be better and more readable and I can still reply in the slack channel."* The diagnosis under it: the thread was routed **by section, not by whether a reply was needed** — of eleven replies exactly one asked him for anything. Slack now carries only the decisions; the page carries everything.

  **Why Pages, not Netlify or a Claude artifact** (he offered both, and said he did not care about public). It wins on his stated criterion — *"clickable from my mobile"* — because it is a plain URL with no login. It wins operationally because `run_weekly_coverage.bat` already does `git add -A` → `commit` → `push` **with exit-code gating on each**, so writing a file into `docs/` inherits a publish path that is already hardened and already turns the task red when it fails. Netlify would add a token, a deploy step and a new failure mode; an artifact cannot be published by a headless session at all.

  **Design rules that are load-bearing.** The decision strip reads the **ledger, not the report** — a report is a snapshot of what was true when written, and rendering its own "3 pending" line onto a weekly-refreshing page would show a queue already decided. **Every** `pending` row appears regardless of which week proposed it, so a name awaiting a reply cannot fall off when the next report publishes. An open item gets a full card and a settled one gets a line (thirteen cards reproduced, on the page, the wall it exists to replace). Wide tables reuse `slack_blocks.is_narrow` rather than a second shape rule, so page and Slack agree on "too wide for a table". The parser is `slack_blocks.parse` — one markdown parser for this report family, no new dependency. `find_report` searches `reports/` **and** `reports/old reports/`, because the weekly run archives and a report has usually moved by the time a later run re-renders it. The step is **not gated on any lane above it**: it renders a report already on disk, so it should refresh even on a run where something else failed — a stale page is what JP asked to stop reading. A missing report is `skipped` with the reason, never `failed`.

  **Three bugs it shipped with, all found by rendering and looking, none by the markup validator** (the HTML was well-formed and completely wrong every time): one `<p>` per *source* line shredded every hard-wrapped lede and split `**bold**` across two elements so it rendered as literal asterisks; the 11-column recommendations table rendered four words per line down a 90px column; and the card headline picked the *peers* list over the company name because "widest short cell" is longer for `WMT, LULU, CROX, FIVE, CVNA` than for `Jersey Mike's Subs Inc.` Tests: `tests/test_weekly_page.py` (50). **Render it and look at it before believing a change to this module.**

- `reporting/slack_blocks.py` — markdown → Block Kit. **Slack has no table primitive; stop trying to make one.** The prior approach fenced every markdown table in a ``` block, which turned the 11-column recommendations table (last column: a paragraph) into unreadable pipe-soup — JP's 2026-08-05 screenshot. Tables now route by shape: **narrow** (≤6 cols, ≤36-char cells, ≤88 total) → aligned monospace; **anything wider** → one card per row, bold headline + prose + a two-column `section.fields` grid. Aligned tables emit `rich_text_preformatted`, not a fence, because **Slack auto-links anything domain-shaped and several exchange suffixes are live ccTLDs** (`.SS` South Sudan, `.HK`, `.BR`, `.PA`) — `688825.SS` came back as `<http://688825.SS|…>` *inside backticks and inside a fence*, and `parse:"none"` does not suppress it for `blocks` (both measured live 2026-08-05). `rich_text` is the one block type Slack leaves literal. Card headlines stay `section` blocks because `fields` has no rich_text equivalent, so a ccTLD-suffixed ticker is still a dead link there — a documented trade of density over a cosmetic blemish. Tests: `tests/test_slack_blocks.py` (24).
- `reporting/pipeline_reversals.py` — flags a company an **earlier report committed to adding** that a later report puts under *Considered and excluded* with no acknowledgement. Built from the Jersey Mike's case: 07-24 said "Would be a Consumer add at pricing", 07-31 said "not universe-relevant" and cited the 07-24 report — **citing a date is not withdrawing a promise**, and that distinction is the check. Commitment phrases are matched **line-wise across the whole prior report**, not within a named section, because the forward book has lived in both a `Pipeline` table (07-31) and a bullet list under `## Notes` (07-24) — a table-only parser found nothing on the very report the check exists for. Exclusions stay section-scoped (an exclusion reason is ordinary prose; only the heading marks it a verdict). Calibrated on the live corpus: **9 reports, 1 finding, 0 false positives**, pinned by `test_corpus_false_positive_rate_stays_at_zero`. Tests: `tests/test_pipeline_reversals.py` (13).
- `scripts/poll_ipo_replies.py` — applies JP's `add TICKER` / `decline TICKER` / `add all` replies (thread **and** top-level) to `data/candidate_ledger.csv`, shells `approve_candidates.py` for approvals, republishes `exports/`, and answers in-thread. Gated to `pending` ledger rows, gated to the approver user (`SLACK_APPROVER_USER_ID`, default JP), idempotent by message `ts` in `data/ipo_reply_state.json` — recorded on failure too, so a transient enrichment error cannot re-fire an approval. **Until 2026-08-05 nothing read the thread**, so every weekly post's "reply `add MU`" was a dead end and three names sat pending. Scheduled as `CoverageManager-IpoReplyPoll` (09:20/13:20/18:20, `run_poll_ipo_replies.bat`). Manual: `python scripts/poll_ipo_replies.py [--dry-run] [--no-publish] [--since YYYY-MM-DD]`.
- `scripts/build_hc_coverage_xlsx.py` — **JP's `AA_Core Coverage` workbook**, at
  `Dropbox\Companies_Stocks_Sectors_Ratings\Coverage\AA_Core Coverage.xlsx`, plus the flat CSV that
  feeds a Google Sheet. Reads `exports/universe.csv` filtered to `Sector (JP)` in
  {Healthcare Services, MedTech} (239 rows), prices each row, and joins JP's
  ratings. Run by the Friday `WeeklyCoverageBuilder` (see below); manual:
  `python scripts/build_hc_coverage_xlsx.py [--out-dir DIR] [--no-archive]`.

  **ONE current file, previous versions in `archive/`.** A stable filename is the
  point: it can be bookmarked and the Google Sheet keeps one identity. `PRIOR_STEMS`
  carries a one-time migration so the pre-rename workbook does not sit at the top
  level looking current.

  **It runs immediately AFTER `cli.py performance`, and that ordering is the design.**
  The calendar-year return columns (`2019`..`2025`, `YTD`) are read from the
  snapshot that step just wrote, so returns and prices share ONE as-of date. Run it
  anywhere else and the workbook footnotes a snapshot up to a week older than its
  own prices — which reads as one moment and is not. JP declined recomputing the
  returns ("just use a footnote"), so the ordering is what makes the footnote
  honest. A snapshot older than `SNAPSHOT_MAX_AGE_DAYS` (10) leaves the columns
  BLANK rather than stale.

  ⛑ **`docs/hc_coverage.csv` is PUBLIC and is a DIFFERENT SCHEMA.** GitHub Pages
  serves it and the Google Sheet reads it through a single `=IMPORTDATA()`. The CSV
  writer serialises whatever is in `COLS`, so **anything added to `COLS` is
  published unless it is also added to `PRIVATE_ONLY`** — which is how joining JP's
  ratings would have published them (Codex, Critical, 2026-08-26). `Rating` is in
  the local xlsx/csv only. **Never rename that path**: nothing here can rewrite the
  Sheet's `IMPORTDATA` cell, so renaming the endpoint silently empties the Sheet.

  ⛑ **Symbols go through `ticker_utils.normalize_ticker`, FX through
  `providers/fx_provider`.** Both were reimplemented privately first and both were
  mistakes: `normalize_ticker` already handled 21 of 22 aliases including the
  `MED`/`MOVE` collisions, and the private FX fetcher cost a whole build — the 239
  ticker lookups exhausted the Yahoo budget and every FX call after them was
  throttled, so the run aborted holding every price and no rate to convert with. FX
  is fetched BEFORE the ticker sweep.

  **It refuses to publish a partial book.** A rate-limited response is
  indistinguishable from a company with no market cap, so it aborts on any dead FX
  rate or >5% of rows missing one, saving to a temp path before archiving so a
  failed write cannot leave the folder with the old file archived and no current
  file. The first run shipped 105 of 239 blank — USD 3,089bn against a true ~4,950bn
  — with nothing worse than a warning line.

  **Exit codes: 0 ok, 3 = the xlsx is open in Excel, anything else = a real
  failure.** 3 is separate because the weekly task calls this and JP having the file
  open on a Friday morning is not a broken pipeline; a red that fires because
  someone was reading the output trains you to ignore reds. The handler covers the
  archive MOVE as well as the install COPY — Excel takes a sharing lock, so the
  error lands on `shutil.move` first. A read-only attribute does NOT reproduce it
  (move succeeds on those), which is why the first attempt to test this passed and
  proved nothing.

  **Ratings** live in `Companies_Stocks_Sectors_Ratings\Ratings_CoreCoverage.xlsx`,
  seeded with the `Core=Y` rows (310 of 1,346, every sector). **The ordinary build
  NEVER writes that file** — `--sync-ratings` does, and only that. `Rating`/`Notes`
  are human-owned and no code path writes them. ⛑ **A changed `Company Name` is
  deliberately NOT refreshed**, which looks like a bug and is the safety mechanism:
  a rating attaches by ticker only while the stored name still agrees with the
  universe, so refreshing it would make the two agree by construction and the check
  could never fire — how `ZEN` kept Zendesk's classification after the ticker became
  Zentek. Such a row is flagged `REVIEW - issuer may have changed` and the rating
  stops attaching. JP's rule: *"Ticker is an identity but it can be fuzzy so you
  need to check and verify with me if its too ambiguous."*

  **Columns, and the provenance line.** `Rating` sits immediately after
  `Company Name` (JP, 2026-08-26); the old `Ramp Effort` column and the
  legacy-sheet matching behind it are gone. Every surface carries a `LAST UPDATED`
  line naming the build time, the Friday cadence, the sources and the column
  rules — the xlsx as its subtitle, and the CSV as a **preamble row before the
  header** (row 1 note, row 2 blank, row 3 header). The CSV preamble looks odd but
  is the only route to the Google Sheet: that Sheet is a single `=IMPORTDATA()`
  cell and nothing here can write to any other cell of it, so anything the reader
  should see has to travel inside the data.

  Tests: `tests/test_hc_coverage_builder.py` (31).

- `config.py` — All paths, API keys, segment definitions
- `data/coverage_universe_tickers.csv` — Master coverage universe
- `data/positions_and_researching.csv` — Positions and research list (subset of universe). Replaces `data/watchlist.csv` (deleted 2026-05-03). Schema: `Ticker, Position, Position Date, Buy Price, Sell Price, First Buy Date, Average Cost, Shares, Notes, Held, Held As Of, Previously Held, Held Until`.

  ### ⛑ OWNERSHIP IS DERIVED, NOT AUTHORED (2026-08-23)

  JP: *"Coverage manager should own what stocks I want to follow but I don't want it to own the record for what I own. I want it to consume something else if it needs to know what I own."*

  **`Portfolio` is NOT a `Position` value any more.** `Position` records INTENT (a judgement you author through Notion); the new **`Held`** column records the FACT, derived from the brokers by `universe/held.py` and never typed. Run it with `python cli.py positions sync-held [--dry-run]`.

  **Why:** one column doing both jobs had two owners, so neither was authoritative and drift was structurally invisible — nothing here ever read a broker. Measured 2026-08-22: CM published **33** holdings against **30** actually held. ROIV was liquidated 2026-08-03 and still publishing as a holding **19 days later**, into catalyst_watch, analyst-days, sigma-alert, earnings_agent and the insider tearsheet.

  **The feed** is `portfolio_daily/exports/held.json` (published by `scripts/export_held.py` at the end of its daily run) — a versioned artifact with a per-broker `as_of`, deliberately not a reach into that project's private `data/`. It **refuses to publish a partial book**, because a half book is indistinguishable from one where half the names were sold.

  **The guards abort and write NOTHING** on: a missing/unreadable/wrong-schema feed; a feed older than `HELD_STALE_MAX_DAYS` (10); zero holdings; or more than `MAX_DEMOTIONS_PER_RUN` (5) names leaving `Held` in one run. That last one catches the case the others cannot — a feed that is present, fresh, well-formed and WRONG. Every guard has a test asserting the CSV is **byte-identical** afterwards.

  **A sale lands on `Following for Interest`** (JP 2026-08-24: *"RPD and U should default to Following for Interest once I sell a stock"*), and the history goes in `Previously Held` / `Held Until` rather than into the routing key. It was `Researching` for two days, which was a CONSTRAINT talking rather than a judgement — `catalyst_watch`, `analyst-days` and `insider_ownership` read only `portfolio.json` + `researching.json`, so nothing else was visible to them. ⛑ **The trade, stated because it is real:** a sold name still reaches transcripts, earnings_agent, sigma-alert and analyst-days (all read every state; the last since 2026-08-23) and NO LONGER reaches `catalyst_watch` or `insider_ownership`. Defensible — forward catalysts and insider buying are questions about a position you might take, not a bellwether you read — but if either lane should carry these names, widen THAT lane rather than mislabelling the position to sneak them in.

  **The export contract did NOT move.** `portfolio.json` is now the rows where `Held == "Y"`, and a held row still publishes `position: "Portfolio"` — see `positions.published_position()`, which is the single rule used by all three export sites (portfolio.json, the sigma-alert payloads, and the joined CSV). No schema bump, no consumer edit; `catalyst_watch` pins `_ACCEPTED_CM_SCHEMA={3,4}` and would hard-fail on an unannounced bump. The four `Held*` columns are APPENDED to the joined CSV — additive, and every consumer reads by name.

  ⛑ **`SYMBOL_ALIASES` in `held.py` is a one-entry stopgap, not an aliasing layer.** Fiserv is one issuer under two live symbols (`FI` here, `FISV` at the brokers and on yfinance, which 404s `FI`). Without it the first sync reports that JP sold Fiserv. The real fix is board row **#345** — join on the identity that did not change (CIK/ISIN/FIGI are identical across both symbols and already in `exports/universe.csv`). A second entry means the stopgap became the architecture; a test pins the count at one.

  ⛑ **`Shares` is a FLOAT.** `_parse_int` did `int(float(x))` and would publish 452 shares for a 452.656 holding. Fractional holdings are the norm here (FMS 452.656, PACS 277.893, CI 40.532).

  `Position` is one of:
  - `Researching` — building a thesis to buy; not yet held (active thesis work). **Also where a sold name lands.**
  - `Following for Interest` — passive earnings/signal tracking; no intent to trade
  - `Ready to Buy` — long thesis complete; waiting for the entry trigger (typically a price level on Buy Price)
  - `Ready to Short` — short thesis complete; waiting for the entry trigger (typically a price level on Sell Price, since short entry is at the high and cover is at the low)

  Managed via `universe/positions.py` and the `positions` CLI subcommand. Published to `exports/positions_and_researching.csv`, `exports/portfolio.json`, `exports/researching.json`, `exports/following_for_interest.json`, `exports/ready_to_buy.json`, `exports/ready_to_short.json` (and back-compat `exports/watchlist*.{csv,json}` for one cycle — these only include `Portfolio ∪ Researching` to preserve the historical contract).
- `data/delisted_tickers.csv` — Hand-managed archive of tickers that have been acquired/de-listed. Captures last-known sector + market cap so the data isn't lost when a row is removed from the active universe. Append manually after confirming a `delisted_check` flag is real. Schema: `Ticker, Company Name, Sector (JP), Subsector (JP), Sub-subsector (JP), Country (HQ), Exchange, ISIN, Currency, Last Mkt Cap (USD), Last Price, Last Data Date, Delisted Date, Reason, Notes, Date Recorded`. Supersedes the legacy `reports/delisted_tickers.xlsx` (which is gitignored and was migrated into this CSV on 2026-04-27).
- `providers/` — External data sources (yfinance, Finnhub, FMP, AlphaVantage, FX). `providers/fmp_history.py` is a separate FMP-only fetcher for 5-year and 10-year P/E and EV/S history used by the historical valuation enrichment (see "Historical valuation columns" below).
- `reporting/` — Report generation (Excel, HTML, email, Slack, sigma_export). `reporting/history_stats.py` holds None-safe avg/stdev/min/max/vs-avg helpers for the Phase 1 history columns.
- `universe/` — CSV validation, enrichment, cleanup
- `discovery/` — Candidate discovery pipeline
- `exports/` — **Published artifact contract for downstream projects (committed to git)**
- `reports/` — Generated reports (gitignored)
- `reports/samples/` — Sample/preview reports
- `cache/` — Cached API data (gitignored). Namespaces: `prices/`, `fundamentals/`, `fx/`, `news/`, `perf/`, `identity/`, and `key_metrics_history/` (30-day TTL, schema v2 — 10-year annual P/E + EV/S series backing both the 5Y and 10Y historical valuation columns; populated for the full universe by `cli.py history-backfill`)

## Exports — published artifact contract

`exports/` is the versioned, committed interface that other projects in this workspace consume (forensic_triage, biotech_triage, screens_equity/quantitative_screens, 13F analyzer, sigma-alert via a separate path). **Files are committed to git** so consumers get history, reproducibility, and rollback. Downstream projects should read these files directly rather than importing Coverage Manager code or hitting fundamentals providers themselves.

**These artifacts are generic and canonical** — they describe the coverage universe and nothing else. Consumer-specific transforms (e.g. sigma-alert sector ETF augmentation) belong in the consumer, not here. If you find yourself wanting to add tickers to `universe_metadata.json` that aren't in `data/coverage_universe_tickers.csv`, that's a sign the transform belongs downstream.

Files (regenerated by `weekly_universe`'s export-artifacts step):

- `exports/universe.csv` — Snapshot of `data/coverage_universe_tickers.csv`
- `exports/universe_metadata.json` — `{TICKER: {name, sector, subsector, sub_subsector, core}}` derived only from CSV rows; no consumer-specific augmentation. `core` is the raw value of the `Core` column ("Y" for analytically-covered names, blank otherwise). **Keyed by the RAW ticker as of schema v4 (2026-07-30)** — `DIA.MI` is published as `DIA.MI`, so the map is exactly 1:1 with the CSV and `metadata[row["Ticker"]]` is the correct join.
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

**Schema v4 changes (2026-07-30) — `universe_metadata.json` keys are now the RAW ticker.**

Previously the key was suffix-stripped (`_normalize_ticker`: `ROG SW` → `ROG`,
`DIA.MI` → `DIA`). That silently did two things:

1. **It deleted a company from the published contract.** `ROG` (Rogers
   Corporation, `Core=Y`) and `ROG.SW` (Roche) both normalized to `ROG`, and
   later-row-wins meant the export said `ROG` was Roche while Rogers Corporation
   had **no entry at all**. The exporter logged `normalization_collisions: 1` on
   every run for months and it was read past every time.
2. **It broke the obvious join for 183 of 1,096 rows.** `exports/universe.csv`
   carries `Ticker = DIA.MI`; the metadata key was `DIA`. Any consumer doing
   `metadata[row["Ticker"]]` missed every suffixed row. `transcripts` iterates
   these keys **as tickers** (`load_all_universe`) and `focus_today` keys its own
   map by them while positions use the raw ticker — both were handed a symbol
   the universe does not use.

The raw ticker is already unique (`validate_no_duplicate_tickers` is an
ERROR-level check), so collisions are now structurally impossible rather than
counted. `normalization_collisions` stays in the status file (shape unchanged)
and is always `0`; a non-zero value now means a **duplicate row** reached the
exporter. The invariant is simply `ticker_count == row_count`.

`_normalize_ticker` is retained for the case-collision validator ONLY. Do not
reintroduce it into the key path — `tests/test_metadata_raw_keys_v4.py` guards
that by inspecting the source.

**Cross-repo:** `sigma-alert` was the one consumer that *relied* on the
stripping — it had built `to_metadata_key()` / `foreign_collision_bases()` /
`disambiguate_collision_metadata()` to compensate. It now uses a new
`lookup_metadata()` that tries the raw key and falls back to the stripped base,
so it works on **either** side of CM republishing rather than depending on deploy
order. Consumer schema pins were widened to `{3, 4}` (not moved to `{4}`) for the
same reason: analyst-days, screens_equity, catalyst_watch, sa-monitor,
exec_interviews, insider_ownership.

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
assert status["schema_version"] in (3, 4), "Coverage Manager exports schema changed"  # v4 2026-07-30: raw-ticker keys
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

- `universe/form10_watch.py` — **weekly Form 10 spin-off discovery** (`cli.py form10-watch`, and step `[4g/6]` of `weekly-universe`). A `10-12B` registers a subsidiary's shares for distribution onto a US exchange 1–3 months before separation. **A spin-off has no offering, so the Finnhub IPO calendar cannot see one** — of 18 candidates the lane had ever proposed, exactly one was a spin-off, proposed two months late at listing. Routes Bucket 1 on the registrant's **own SIC** (a SpinCo is classified under the business it operates) and Bucket 3 on the **parent's market cap as a size proxy** — Bucket 3 is size-gated and sector-agnostic, so SIC routing alone is blind to it (Honeywell Aerospace is SIC 3724, aircraft engines). **Market cap is never invented**: no shares trade pre-separation, so entries are `size unknown` pipeline items. Parent resolution is regex → CIK-resolution pin (0.80 + runner-up margin, CIK-deduped, whole-string tie-break); unresolved downgrades, never guesses. Three states incl. `inconclusive` for a missing SIC — an unclassifiable registrant is where a miss hides. Seen-ledger `data/form10_seen.json` doubles as an **open-items ledger**: a 14-day search window finds new FILINGS but does not describe the PIPELINE, so still-unlisted relevant filings are **carried forward** every week marked `(still open)` until their ticker appears in the universe (it listed and was added) or they age out at 540 days. Without it FedEx Freight (filed 2026-01-16) and Honeywell Aerospace (2026-03-03) would never appear in a weekly report again. Report ASCII-sanitized at the exit. **Gotchas, all found live and all of which returned plausible wrong answers:** EDGAR FTS **paginates at 10 hits per page** and returns one hit per *document*, so page 1 alone samples one company's exhibits (reported 10 registrants for a window holding 18); the per-registrant collapse must prefer **EX-99.1** (the information statement) or it picks an indenture; the parent capture must end at a corporate designator **and** reject the registrant itself (FedEx Freight names itself as a subsidiary before naming FedEx Corporation); `_name_similarity` is token-cover based so a subset scores 1.00 ("Honeywell International Inc" ties "Inter & Co, Inc."). Tests: `tests/test_form10_watch.py` (23).

- `universe/symbol_directory.py` — **weekly US symbol-directory watch** (`cli.py symbol-directory`, and step `[4f/6]` of `weekly-universe`). Snapshots the two free Nasdaq Trader files (`nasdaqlisted.txt` + `otherlisted.txt`, covering Nasdaq/NYSE/Arca/American/Cboe/IEX — ~7,500 operating companies after dropping ETFs and test issues) and diffs against the prior snapshot. **Nasdaq keeps no archive**, so snapshots are committed to `data/symbol_directory/` — a missed week is a diff that can never be computed. **Absence from the directory is a candidate, not a verdict:** each covered US row that is missing gets adjudicated against SEC's per-CIK submissions endpoint into `delisted` (a filed Form 15-12B/12G/15D, or no registered ticker) / `listed` (a symbol-format mismatch — `FI` vs SEC's stale `FISV`, `SGMO` vs `SGMOQ`) / **`inconclusive`** (no CIK on the row, or the endpoint would not answer). Inconclusive is NEVER folded into delisted — deleting a live company from the universe is the one unrecoverable mistake here. Foreign lines are excluded by `Exchange` before comparison; they are absent from a US file by definition and flagging them would be an artefact of the question. Also surfaces Nasdaq's `Financial Status` field (D/E/Q/G/H/J/K — distinct states, mapped, not conflated), which nothing else in the fleet reads. Exit 2 on any covered name missing or removed. First live run 2026-08-06: 863 US rows checked, 31 absent → **10 confirmed delisted by Form 15** (ACLX, CCRN, CPRX, DAY, KZR, LYRA, NOTV, NUVL, PRTC, XOMA), 6 symbol mismatches, 15 inconclusive for want of a CIK. Tests: `tests/test_symbol_directory.py` (18).

## Delisted / recycled ticker check

`python cli.py check-delisted` probes yfinance for each universe ticker (via a lightweight `Ticker.info` pull, results cached for 7 days under `cache/identity/`) and flags rows that look delisted, acquired, or recycled to a non-equity instrument.

**Three outcomes, not two (2026-07-25).** Every ticker resolves to `flagged`,
`clean`, or **`inconclusive`** — the last meaning *we could not find out*. This is
the module's central distinction: a throttled lookup and a dead company return
the same empty response, and only one of them is a delisting. The 2026-07-25 run
reported **58 flags**, but `ACLX` (Arcellx) was trading at **$115.07 on NASDAQ
with 13.2M shares of volume**; that same run logged 53 price-probe failures out of
1,093 names. Yahoo was throttling, and throttling was being recorded as death.
Inconclusive rows are reported in their own section and **never enter the flagged
list**. Two guards enforce it:

- **`.info` and the price probe are fetched independently.** A `.info` exception
  used to abort the whole lookup and return `{}`, discarding a price probe that
  would have succeeded — and `{}` classified as "likely delisted". `info_ok`
  records whether the metadata call actually answered, so an
  empty-because-throttled `.info` is never read as empty-because-gone. A ticker
  with a **live price feed is never flagged**, whatever `.info` says; it is
  reported under "trading, but missing vendor identity metadata".
- **A comparison that can't be made has no result.** `_name_similarity` returns
  `None` (not `0.0`) when either side has no name — `ACLX` returned a `quoteType`
  with empty `longName`/`shortName` and was flagged `name mismatch
  (similarity=0.00), yfinance=''`, i.e. a disagreement with an empty string.

**"No price data" is an answer, not evidence (2026-07-26).** yfinance raises
`YFPricesMissingError` — *"possibly delisted; no price data found"* — for symbols
that trade perfectly well. Verified live: **`ACLX` raises it on both a 1mo and a
1y window while quoting $115.07 on NASDAQ with 13.2M volume.** So the probe has
three outcomes (`PRICE_OK` / `PRICE_NO_DATA` / `PRICE_FAILED`) and **only a stale
last bar ever supports a flag** — real bars, ending too long ago. An empty
result never flags, whatever Yahoo calls it.

Two consequences worth knowing:
- **`PROBE_PERIOD` is `1y`, not `1mo`.** Bars are only visible inside the
  requested window, so a name acquired three months ago returns *nothing* on a
  1mo pull and is invisible; on 1y its final bars still show and it flags
  correctly. Verified: ASRT (Zydus, delisted 2026-06-16) returns 3 bars on 1mo
  and 9 on 1y, both ending 2026-06-29. Same one request either way.
- **`no_data` is excluded from the `degraded` calculation** and *is* cached. It
  is a stable property of a few dozen symbols, so counting it as a run failure
  would hold the degraded flag permanently on — and an alarm that is always lit
  is one the reader learns to ignore, which would defeat wiring it into the
  heartbeat at all. `degraded` counts **transport** failures only. These names
  are reported in their own bucket: re-running will not resolve them, they need
  a second source.

**Rate-limit backoff (2026-07-26).** The first full cold run after the above fix
lost **518 of 1,093 price probes and 488 `.info` calls** to "Too Many Requests" —
502 names came back `inconclusive`. The verdict logic handled that honestly
(under the old code they would have been 502 false delistings) but the run still
learned almost nothing, so throughput had to be fixed too. `_Throttle` is a
**process-wide** backoff gate: per-thread retry is useless here because the other
workers keep hammering Yahoo, so one thread's 429 pauses **all** of them.
Exponential with jitter (2s → 60s cap, 4 attempts), decaying on success, so the
run finds a sustainable rate instead of relying on a constant guessed up front.
`max_workers` default dropped 6 → 4. **A cold pass is slow by design** — on a
weekly job, verdict correctness beats wall clock. Only rate-limit errors are
retried; a 404 is a real answer and retrying it would burn the budget the
throttled names need. `rate_limit_trips` is reported. Note yfinance raises
`"possibly delisted; no price data found"` for names that demonstrably trade
(ADAP at $0.0485, AFMD at $0.1815 on 2026-07-25) — that is a *failed probe*, so
it yields `inconclusive`, never a flag.

**Run-level `degraded` flag**: when over `DEGRADED_FAILURE_RATE` (2%) of lookups
fail, the run is marked degraded in the report, the `weekly-universe` step
summary, and the CLI exit code — because a throttled run's *flags* are also less
trustworthy, and the reader needs to see that rather than infer it. Cache
namespace bumped to `identity_v3` (a v2 entry has no `info_ok`, which would
downgrade a genuine flag to `inconclusive` for a full TTL).

Flag rules (evaluated in this order):
- both probes failed → **`inconclusive`**, never a flag
- `.info` empty but price feed live → **clean** (vendor metadata gap, reported)
- `.info` empty AND price feed dead → likely delisted (both signals agree)
- **no recent price data** → likely delisted/renamed (or an extended halt). A price-recency probe pulls ~1mo of daily bars; if the most recent bar is older than `PRICE_STALE_DAYS` (10) the price feed is treated as dead. This is the reliable tell for a **clean acquisition / take-private**: Yahoo keeps the stale `.info` metadata (longName etc.) populated for months, so the `.info`-empty and name-similarity rules miss these — but the price feed goes empty immediately. Added 2026-06-13 after EXAS (Abbott), HOLX (Blackstone/TPG), and the MPW→MPT / GMRE→XRN rebrands lingered in the universe for months. Robustness: the probe uses `history(raise_errors=True)` so a transient 429/network error becomes a *skipped* probe (counted as `price_probe_failures`, surfaced in the report) rather than a false "delisted" flag; the stale/not-stale decision is **frozen at probe time** into the cached `price_stale` field so a cached `last_close_date` can't "age into" staleness within the 7-day identity-cache TTL.
- `quoteType` is `ETF`, `MUTUALFUND`, `INDEX`, `CURRENCY`, or `CRYPTOCURRENCY` → ticker has been recycled
- Normalized fuzzy similarity between the universe `Company Name` and yfinance `longName`/`shortName` falls below 0.55 → ticker may have been recycled to a different issuer

Outputs (in `reports/`, archived weekly):
- `delisted_check_YYYY-MM-DD.csv` — one row per non-clean ticker, with a leading
  **`verdict`** column (`flagged` / `inconclusive` / `clean`) so a reader of the
  CSV alone cannot mistake an unresolved lookup for a delisting candidate
- `delisted_check_YYYY-MM-DD.md` — human-readable summary, with flagged,
  inconclusive, and metadata-gap rows in separate sections

The check is **non-gating** — it never blocks the report or the published artifacts. After confirming a flag is real, the user manually:
1. Removes the row from `data/coverage_universe_tickers.csv`
2. Appends an entry to `data/delisted_tickers.csv` with the last-known sector + market cap (the `Last Mkt Cap (USD)` / `Last Price` can be pulled from the most recent `cache/fundamentals/yf_<TICKER>.json` before clearing it)

The check runs as step `[4/6]` of `weekly-universe`. CLI exit code is `2` when at
least one flag is raised **or the run was degraded** — a run that failed to learn
what it was asked to learn must not exit `0` and report its silence as a clean
universe. Module: `universe/delisted_check.py`; tests: `tests/test_delisted_check.py`.

## Ticker-change / deregistration discovery

`python cli.py check-ticker-changes` is the **companion** to `check-delisted`. Where the delisted check answers *"is this ticker dead?"* (yfinance price feed), this answers *"what symbol does SEC now have for this company?"* — so a renamed name can be **remapped** to the new symbol instead of just removed (the MPW→MPT / GMRE→XRN case).

**Discovery path:** SEC EDGAR's bulk `company_tickers.json` (same file `enrich.py` uses). A company's **CIK is stable across a ticker change** — only the symbol moves. The module builds the reverse map `CIK → {current ticker(s), title}` and, for each universe row with a CIK:
- SEC's ticker for that CIK differs from the universe ticker → a **mismatch** (candidate change), reported with SEC's symbol(s) + title.
- CIK absent from the bulk file → a **deregistration candidate**, then **confirmed** against the authoritative per-CIK submissions endpoint (the bulk file omits ~many active names, so absence alone is too noisy): flagged only when submissions has **no live ticker** OR the last filing is a **Form 15** (`15-12B/12G/15D` = filed deregistration, which the `tickers` field lags by weeks). A bulk-absent CIK that submissions confirms is still active is dropped (counted as `active_omissions`). First live run: 14 confirmed delistings (CFLT→IBM, APLS→Biogen, FOLD→BioMarin, SEMR→Adobe, …) with 4 active bulk-omissions correctly dropped.

**Why it's a review list, not an auto-fix:** SEC's structured ticker data can *lag* a real-world rebrand — it still lists the retired `FISV` long after Fiserv moved to `FI`, on **both** the bulk file and the per-CIK submissions endpoint — and yfinance can't disambiguate either (Yahoo aliases the retired symbol to the live one). There is no automated authority that reliably says which symbol is current, so the check surfaces the mismatch with full context and a human decides direction. A best-effort per-CIK **`formerNames`** lookup (SEC submissions, only for the few mismatch candidates) flags entities that legally renamed — a strong "real change" tell (e.g. `GALAPAGOS NV → Lakefront`, GLPG→LKFT). A matching SEC title with empty former-names leans toward SEC-file lag (leave the row as-is).

**Scope:** only rows with a CIK; mismatch detection gated to plain US-style symbols (`ABT`, `BRK.B`) so a cross-listed row tracking the foreign line (`DIA.MI`) isn't flagged as "changed to the US ADR." The SEC bulk map is cached 24h (`cache/sec_company_tickers/`).

Outputs (in `reports/`, archived weekly): `ticker_change_check_YYYY-MM-DD.{csv,md}`. Non-gating. Runs as step `[4b/6]` of `weekly-universe` (right after `delisted_check`). CLI exit code is `2` when any mismatch or deregistration is flagged. Module: `universe/ticker_change_check.py`; tests: `tests/test_ticker_change_check.py`.

## Blank-CIK re-probe (weekly step `[4a/6]`)

`python cli.py backfill-cik [--dry-run]` fills blank `CIK` cells from SEC's bulk
ticker map. It exists because **a CIK is not a static property of a company — it
is a fact about whether that company has registered with the SEC *yet*, which
changes.** `enrich.py` resolves CIKs when a row is first enriched and nothing
re-checked the blanks, so a name that registered *later* kept a blank CIK forever
and every CIK-keyed lane silently skipped it. That is not hypothetical: on
2026-07-25 an independent cross-check found SpaceX filing 40 Form 3s while
`insider_ownership` had been skipping it for want of a CIK. Re-probing found 16
such rows (Cerebras, Fervo, Quantinuum, HawkEye 360, Lumexa…).

Runs as step `[4a/6]`, deliberately **before** the export steps, so a newly
registered company reaches CIK-keyed consumers in the *same* run. Costs one HTTP
GET per week regardless of universe size. **Only fills blanks** — an existing CIK
is never overwritten, because a CIK is stable across a ticker change (the
invariant `ticker_change_check` relies on), so a populated-CIK disagreement means
the *ticker* moved, which is that module's finding to surface.

**A ticker string is not an identity.** The fill is gated on a name-similarity
check between the universe `Company Name` and SEC's `title` (threshold 0.55,
mirroring `delisted_check`); a disagreement is **warned and skipped, never
written**. Tickers get recycled between issuers — the premise of the sibling
`delisted_check` — and the rows this module targets are the most exposed of all:
private names under provisional symbols (SPCX, Cerebras, Fervo, Quantinuum)
assigned before any listing existed. Writing another registrant's CIK would make
`insider_ownership` and `earnings_agent` silently pull the wrong company's
filings; a blank CIK is visibly missing, a wrong one looks like data. The gate
yields a deliberate false negative — a row reading "SpaceX" against SEC's "Space
Exploration Technologies Corp" is skipped — which is the intended trade, since a
warned skip is resolved by a human in seconds.

Separator-insensitive matching (universe `BRK.B` vs SEC `BRK-B`) applies **only
to plain US-style symbols**, and only for unambiguous normalized keys. Foreign
lines dominate the blank-CIK population (`4503.T`, `000100.KS`) and stripping
their separators yields keys that could collide with an unrelated US symbol.

The SEC bulk file comes from `ticker_change_check.load_sec_cik_map` (24h cache,
`fetched_ok` contract) rather than a second downloader — otherwise weekly steps
4a and 4b pull the same ~1 MB back-to-back and only one survives a brief SEC
outage on cache. A failed fetch changes nothing and reports
`failed: SEC map unavailable`; CLI exits `2`. Module `universe/cik_backfill.py`;
tests `tests/test_cik_backfill.py`.

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

## Listing dates describe the CURRENT listing (convention, 2026-07-28)

**`Year Listed`, `IPO Date`, `Est Lockup 90d` and `Est Lockup 180d` all describe the listing
that is trading today — never the issuer's earliest listing ever.**

This had never been written down, and the default source disagrees with it: `Year Listed` comes
from FMP's `ipoDate`, which is a fact about a **brand**, not about the security now trading. When
a company is acquired and later spun back out, FMP keeps reporting the original listing while a
**new registrant with a new CIK** is what actually trades.

Live case: **SNDK** read `Year Listed` **1995** — SanDisk's original IPO — while the security is
the **February 2025** spin out of Western Digital, **CIK 2023554**. That is wrong under either
definition anyone queries: an IPO-cohort screen for 2025 misses it, and a "public since 1995"
screen wrongly includes a security that did not exist. Corrected to 2025. Same class: Kyndryl,
Solventum, GE Vernova.

**Prior listings are NOT stored here.** `ipo_tracker/data/ipo_registry.json` already holds 5,459
listing events back to 2016 with `deal_type` (`ipo` / `spac_ipo` / `de_spac` / `direct_listing`).
That is the event store; join to it on CIK/ticker. Duplicating the history into this CSV would
mean two things to keep in sync and guaranteed drift.

**Two validators enforce it** (both warnings, never gating), because they catch different halves:

| Check | Catches | Signal |
|---|---|---|
| `validate_listing_date_agreement` | **re-IPOs** — a relisting that had an actual offering | `Year Listed` vs a Renaissance-verified `IPO Date`, >1y apart |
| `validate_relisting_cik_cohort` | **spin-offs** — no offering exists, so the check above is blind | a CIK typical of far newer registrants than `Year Listed` claims |

The second needs no API call: **a CIK is assigned at first SEC registration, so a registrant
cannot predate its own CIK.** It is a local-neighbourhood outlier test (rows sorted by CIK,
compared against the median `Year Listed` of the 25 nearest CIKs on **both** sides), so it
self-calibrates as the universe grows. US exchanges only — an ADR's home-market listing
legitimately predates its SEC registration — and rows at either end of the CIK ordering are
skipped, since a one-sided neighbourhood pulls the median hard (BRO flagged purely on that).
Measured on the live universe: **1 flag (SNDK), 0 false positives across 667 US-listed rows.**

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
- **Three outcomes, not two (2026-07-28).** `providers.renaissance_ipo.fetch_ipo_date_ex`
  returns `(status, payload)` with status `ok` / `no_data` / **`inconclusive`**. A 404 is an
  *answer* ("no IPO on record") — counted against quota and cached forever. A 429/5xx/transport
  error or a missing key is **not an answer**: never counted, never cached, retried next run.
  The bare `fetch_ipo_date` wrapper remains for back-compat but collapses the two and should
  not be used in new code. `backfill()` now reports `inconclusive` separately and logs a warning
  when it is non-zero. **Why:** on 2026-07-28 a burst of 429s was summarised as "no IPO on
  record: 9" when only 3 were real; the cache was correct throughout (nothing was poisoned) but
  the operator-facing count was not, and a rerun 40 seconds later filled 6 of the 9. Same class
  as `delisted_check`'s found/clean/inconclusive split. Tests: `tests/test_renaissance_ipo.py`,
  `tests/test_ipo_backfill.py`.
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

## Foreign identifier backfill (ISIN + LEI from fund holdings)

`python cli.py backfill-foreign-ids [--dry-run] [--no-cache] [--limit N]`
recovers **ISIN and LEI for foreign-listed rows** that `enrich.py` cannot resolve.
Before it ran, **200 of the 350 foreign-HQ rows had no ISIN at all** — the binding
constraint on every identifier-keyed cross-check, since a reconciliation can only
reach rows that have something to join on. First live run (2026-07-28) wrote
**55 ISIN + 43 LEI**, taking foreign ISIN coverage 43% → **59%** and universe LEI
334 → **377**.

### Why two sources, joined

Neither public file is sufficient alone; both describe the *same* portfolio of a
broad international ETF:

| Source | Gives | Missing |
|---|---|---|
| iShares holdings CSV (`/us/products/<id>/x/latest-holdings.csv`) | local exchange ticker, `Location`, `Exchange`, `Market Currency`, daily | **no ISIN** |
| SEC **N-PORT** (`NPORT-P` `primary_doc.xml`) | **ISIN, LEI, `invCountry`** — verified 100% coverage on all three across 4,198 IXUS holdings | **no ticker** (0.5%) |

Joined on normalised company name within one fund, they yield
`(local ticker, country) → {ISIN, LEI, incorporation country, exchange, currency}`
**from an SEC filing**. Funds are identified by `(iShares product id, SEC CIK, SEC
series id)` — the URL *slug is ignored*, only the product id resolves the fund.
Currently IXUS + IEMG; adding funds is a one-line change and raises coverage.

### The guards, and why each exists

1. **`(ticker, country)`, never ticker alone.** A bare local ticker is not an
   identity — that is what the exchange suffix is for. Keyed on ticker alone,
   `1801.HK` (Innovent, Hong Kong) resolves to `JP3443600006`, a **Japanese**
   ISIN, because `1801` is also a Tokyo issuer's code. Measured, not
   hypothesised. Keyed properly: zero collisions across 3,147 keys, and Innovent
   resolves to `KYG4818G1010`.
2. **Suffix → expected country.** `SUFFIX_COUNTRIES` maps `.T`→Japan, `.HK`→
   {China, Hong Kong}, etc. A fund row from the wrong country is rejected and
   reported — this caught `2359.HK` (WuXi AppTech) matching a *Taiwanese* `2359`.
3. **Name agreement** (token-based, prefix-aware — reuses
   `crsp_snapshot._name_similarity`). Ticker and country can both match while the
   companies do not.
4. **Never overwrites.** An existing ISIN or LEI is left alone; the two halves
   fill independently.

A wrong identifier is far worse than a missing one: a blank ISIN is visibly
missing, a wrong one looks like data and silently mis-joins every consumer.

### Incorporation is not domicile — do not "fix" this

The recovered ISIN's country prefix often disagrees with `Country (HQ)` **and is
still correct**: Innovent is China-HQ'd, Cayman-incorporated (`KYG…`); likewise
WuXi Biologics, Hansoh. `enrich.validate_isin_for_row` would reject those, and is
right to for *its* inputs — it guards against yfinance returning a wrong ISIN for
a rebranded ticker. These come from an SEC filing carrying the issuer's own LEI,
so that heuristic is deliberately **not** applied; instead every prefix-vs-HQ
divergence is listed in the report so an *unexpected* one is seen rather than
silently written or silently dropped.

Sources cached 7 days under `cache/foreign_ids/`. iShares 403s a non-browser
user-agent (same CDN behaviour the comments tracker hit); SEC needs
`EDGAR_IDENTITY`. Non-gating, additive; not wired into the weekly pipeline — run
on demand like `backfill-lei`. Exit 2 when **every** source failed (a run that
learned nothing must not report a clean universe). Report:
`reports/foreign_identifiers_<date>.md`. Module
`universe/foreign_identifiers.py`; tests `tests/test_foreign_identifiers.py` (19).

**Known remaining gap:** ~60 foreign rows carry no exchange suffix (`207940`,
`SUNPHARMA`), so there is nothing to resolve the market from and they are counted,
not guessed.

## Foreign metadata cross-check (`crosscheck-foreign`)

`python cli.py crosscheck-foreign [--no-cache]` is the **read-only companion** to
`backfill-foreign-ids`: same sources (iShares holdings ⋈ SEC N-PORT), same guards,
opposite question — where does the metadata the universe *already carries*
disagree with a legally filed document? Mirrors the `delisted_check` /
`ticker_change_check` pairing. It only became worth building after the backfill,
since the check can reach only rows with something to join on.

First live run (2026-07-28): **350 foreign rows checked, 73 matched, 13
conflicts.** Matches ISIN-first (an exact identifier beats any heuristic), then
falls back to `(local ticker, country)`.

### It found seven wrong ISINs — all seven CORRECTED 2026-07-28 (JP-approved)

Every stored value below identified a **different company**. JP reviewed and
approved applying all seven; the corrections are in the universe CSV and the
published `exports/`. Each replacement was verified against **OpenFIGI**
(Bloomberg) and **GLEIF** — sources independent of the N-PORT join that proposed
it — plus its ISO 6166 check digit.

| Ticker | Company | Stored (wrong) — what it actually was | Corrected |
|---|---|---|---|
| `000100.KS` | Yuhan (South Korea) | `INE156M01017` — Yuranus Infrastructure (**India**) | `KR7000100008` |
| `9926.HK` | Akeso (China) | `INE087A01019` — Kesoram Industries (**India**) | `KYG0146B1032` |
| `ZEN` | Zentek (Canada) | `INE251B01027` — Zen Technologies (**India**) | `CA98942X1024` |
| `7741.T` | Hoya (Japan) | `DE0005297204` — Homag Group AG (**Germany**) | `JP3837800006` |
| `FAGR.BR` | Fagron NV (Belgium) | `CZ0008461209` — Fagron a.s. (**Czechia**) | `BE0003874915` |
| `6446.TW` | PharmaEssentia (Taiwan) | `US7169722037` — its **GDR** line, not the TWSE ordinary | `TW0006446008` |
| `8086.T` | Nipro (Japan) | `JP3750800009` — NMS Holdings (**Japan**) | `JP3673600007` |

Two rows carried the wrong company's **LEI** as a matched pair with the wrong
ISIN, and both were corrected too: `7741.T` `5299009ROBNLE4G0RK14` (GLEIF: *Homag
Group AG, DE*) → `353800X4VR3BHEUCJB42` (*HOYA, JP*); `FAGR.BR`
`3157004AQG2TA4ZS7Y94` (*FAGRON a.s., CZ*) → `549300TRKRUFK2RRG779` (*Fagron,
BE*). Nipro's LEI was already right — only its ISIN was another issuer's.

**`ZEN` needed more than an ISIN — and was then REMOVED entirely (JP,
2026-07-28: "remove it. it was an old company").** Correcting only the
identifier would have left a row that still lied: its **three Bloomberg FIGIs
were Zen Technologies' too** (`BBG000BTLY23` resolves to `ZEN TECHNOLOGIES LTD`
on OpenFIGI); they were fixed as a set the same day. But the row's stale
`Sector (JP) = SaaS` exposed the real story: the row was created for
**Zendesk** (NYSE ticker `ZEN` until its 2022 take-private) and later repointed
by ticker to **Zentek Ltd** — a TSXV graphene/nanomaterials IP company
(zentek.com: Albany graphite, ZenGUARD coatings, aptamer biosciences; also
Nasdaq `ZTEK`) that was never a deliberate pick and fits neither SaaS nor the
HC-focused universe. The earliest record in this repo (pre-enrichment backup,
2026-03-16) already reads `ZEN,,Zentek Ltd,SaaS,` — name refreshed, sector
never revisited: the "a ticker is not an identity" failure mode end to end.
At deletion time the row described **Zentek** (Company Name `Zentek Ltd`, ISIN
`CA98942X1024`, Exchange `TSXV`, Canada/CAD — all verified Zentek's own), not
Zendesk. Removal is **reversible**: the full entry lives in
`data/delisted_tickers.csv` (Reason: "Removed from universe - Zendesk-era
artifact row"; Notes flag it is NOT delisted — Zentek still trades). Pinned by
`test_zen_removed_and_quarantined`. Exports republished the same day
(delta: +0/−1).

### Why the guard did not block them — and the hole (CLOSED 2026-07-28)

All seven arrived with the **initial universe CSV import** (`3ac4425`,
2026-04-03); `enrich.validate_isin_for_row` landed eight days later (`ce3cf91`,
2026-04-11). So no live path bypasses the guard — they predate it, and the bulk
enrich only fills **blank** cells, so nothing ever revisited them.

**The writer is yfinance's `.isin` property, and it is still wrong today.**
Verified live 2026-07-28 — `yf.Ticker(...).isin` returns the *exact* stored wrong
value for `000100.KS` (`INE156M01017`), `9926.HK` (`INE087A01019`), `8086.T`
(`JP3750800009`) and `6446.TW` (`US7169722037`), and yet another Indian ISIN
(`INE510H01015`) for `7741.T`. Yahoo resolves a bare local code (`000100`,
`9926`, `8086`) against the wrong market, and India's `INE…` space dominates the
collisions — which is why "three Indian ISINs" was **one** bad source, not three
slips.

**A prefix check is a country check, not an identity check.** Six of the seven
are caught by the prefix guard today. `8086.T` is **not**: `JP3750800009` is NMS
Holdings', also Japanese, so the prefix matches and the guard accepts it — and
yfinance still serves it. That same-country wrong-issuer hole is **closed as of
2026-07-28** by the ISIN → issuer-name identity check
(`universe/isin_identity.py`, section below): the enrich write path now drops
any ISIN whose OpenFIGI issuer name does not match the row's `Company Name`.
`test_guard_does_NOT_catch_a_same_country_wrong_issuer_isin` still pins the
prefix layer's limitation (that function alone remains a country check);
`tests/test_isin_identity.py` pins the closure at the write path.

**Post-fix acceptance:** `crosscheck-foreign` returns **4 conflicts, all
`listing-mismatch`** (`AZN`, `FER`, `MDA`, `2359.HK`) — the ADR-vs-ordinary
convention question, scoped separately. Zero `isin-conflict`, zero
`lei-conflict`, zero `name-divergence`. `matched` moves 73 → 72 because `ZEN` was
only ever matching through the *Indian* company's ISIN; Zentek is a TSXV
micro-cap that IXUS/IEMG do not hold, so falling out of the matched set is the
correct outcome. Regression tests: `tests/test_foreign_crosscheck.py`
(the `CORRECTED_ISINS` block).

### Three finding classes, deliberately kept apart

- **`isin-conflict` / `lei-conflict`** — one side is simply wrong.
- **`listing-mismatch`** — the row's ISIN identifies a *different listing* than
  its ticker, exchange and currency do. `AZN` is the live case: ticker on NYQ in
  USD (the US ADR) carrying `GB0009895292`, the London ordinary. Both facts are
  individually right; the row mixes two securities, so any ISIN-keyed join
  silently returns London for a row tracking New York. Same for `FER`, `MDA`,
  and `2359.HK` (HK line, Shanghai A-share ISIN).
- **`name-divergence`** — an ISIN survives a rename, so diverging names under a
  matching ISIN normally *is* the rename. Not always: `ZEN` (Zentek, Canada)
  matched `ZEN TECHNOLOGIES` (India) because the stored ISIN **was** the Indian
  company's — the reading this class kept open turned out to be the right one,
  and the row was corrected 2026-07-28 (above). The note keeps both readings
  open, and this finding **suppresses**
  the `listing-mismatch` for the same row — a currency difference between two
  *different companies* is a symptom, and calling it a listing conflation would
  send a reader hunting for an Indian listing of a Canadian company.

### What is deliberately NOT a finding

- **ISO alpha-3 vs alpha-2.** The universe stores `GBR`, N-PORT uses `GB`. Every
  one of the first 18 hand-found "country mismatches" was this. Normalised
  before comparison, never reported.
- **Incorporation vs headquarters.** `invCountry` is where the security is
  *incorporated*, `Country (HQ)` where the company *operates*. Innovent is `CN`
  HQ / `KY` incorporated; Legend Biotech is US-HQ'd / `KY`. Both right. Reported
  in a separate labelled section (7 rows) — **do not "fix" these**.
- **Unmatched rows** (277). Not being held by these two funds is an absence of
  evidence, not evidence of a problem. Counted, never flagged.

Exit code `2` on any conflict **or** on a run where every source failed — a run
that learned nothing must not report agreement. Report:
`reports/foreign_crosscheck_<date>.md`. Module `universe/foreign_crosscheck.py`;
tests `tests/test_foreign_crosscheck.py`. Non-gating and read-only.

**Wired into `weekly-universe` as step `[4c/6]` (2026-07-28).** The seven wrong
ISINs survived four months because every identity check was run-on-demand; the
crosscheck now runs on the weekly cadence. Still NON-GATING: a conflict never
fails the build or blocks exports, but the step status is `failed:`-prefixed so
`collect_non_successes` catches it and the health heartbeat reads `partial`.
The run summary reports **counted classes, never a boolean** — e.g.
`failed: 4 conflict(s) - 0 isin-conflict, 0 lei-conflict, 0 name-divergence,
4 listing-mismatch; 6 incorporation note(s) (72 matched of 349 foreign rows)` —
because "4 listing-mismatch" is actionable and "conflicts: yes" is not. The
status string is counts + fixed ASCII labels only (no company names — non-ASCII
names have twice killed the cp1252 console mid-run). Step logic:
`weekly_universe._step_crosscheck_foreign` + `_crosscheck_step_status`; the fund
sources are cached 7 days, so the weekly step is usually cache-warm. Until JP
rules on the ADR/ordinary `Listing Type` taxonomy question, the 4 standing
`listing-mismatch` rows (`AZN`, `FER`, `MDA`, `2359.HK`) will keep the weekly
heartbeat at `partial` — that visibility is deliberate.

## ISIN structural + prefix validation (write path, offline)

`enrich.validate_isin_for_row` runs **two offline gates, in order, before any network
check** (the OpenFIGI identity check is downstream of it):

1. **ISO 6166 check digit** — `ticker_utils.isin_check_digit_ok(isin)`, promoted
   2026-07-28 from a test-local helper in `tests/test_foreign_crosscheck.py` (which now
   imports it — one implementation, on purpose). Arithmetic only: no vendor, no network,
   deterministic. Input is case-folded and whitespace-stripped; **everything malformed
   returns False** (wrong length, non-alphanumeric, digit in the country code, letter in
   the check digit, blank/None) — the caller treats False as "do not store", and a value
   that cannot be checksummed is exactly a value that must not be stored. Catches typos in
   a column humans hand-edit, and structurally-bogus vendor values, on rows whose countries
   are blank or unmapped — where the prefix rule cannot help. Live sweep 2026-07-28:
   **794 ISIN-bearing rows, exactly 1 failure — `CSU` carrying `NET000CLBR01`** (flagged
   by the same day's identity audit; awaiting JP, not auto-corrected).
2. **Country prefix** — the ISIN's 2-letter prefix must be in the acceptable-prefix set of
   `Country (HQ)` **or** `Country (Listing)`.

The prefix map was restructured 2026-07-28 (Codex R3 — it was missing Ireland,
Netherlands, Israel, Singapore, the offshore set and more: 10 live country values / 48
rows the guard silently never validated). Two maps in `ticker_utils`, deliberately
distinct facts:

- **`COUNTRY_TO_ISO2`** — country name → its ISO 3166-1 alpha-2 identity code, 1:1.
  Used for normalizing vendor country fields and comparing against N-PORT `invCountry`.
- **`COUNTRY_TO_ISIN_PREFIXES`** — country name → **frozenset** of acceptable ISIN
  prefixes, derived from `COUNTRY_TO_ISO2` plus `_EXTRA_ISIN_PREFIXES` so the two cannot
  drift. Set-valued because Channel-Islands/IoM issuers legitimately use `GB` as well as
  `JE`/`GG`/`IM`. The reverse is NOT loosened: United Kingdom stays `{GB}` — accepting
  JE/GG/IM on every UK row would weaken the whole UK book, and a Guernsey-incorporated
  UK company (OKYO's shape) is the `Country (Incorporation)` question, blocked pending
  JP. Euroclear `XS`/`EU` prefixes are debt-market shapes and deliberately not accepted.
  The old string-valued `COUNTRY_TO_ISIN_PREFIX` is **gone**; nothing outside this repo
  imported it.

**`validate_country_prefix_coverage`** (`universe/validation.py`, in
`run_all_validations`, warning-only like the case-collision check) flags any populated
country value with no prefix mapping, so the next map gap is visible instead of a silent
no-op. Live: 1 warning — `MICC` carries `Country (HQ) = "NL"`, an alpha-2 code where a
country name belongs (a row defect for JP to fix, not a mapping to add).

## `Instrument Type` — depositary receipt vs the actual share (2026-07-31)

`python cli.py instrument-type [--dry-run] [--no-cache]` fills the **`Instrument Type`**
column, inserted immediately after `Listing Type`. They are **two different facts** and one
column cannot carry both without lying about one (JP's call, 2026-07-29):

| Column | Answers |
|---|---|
| `Listing Type` | is this the **home** listing, or a secondary one? |
| `Instrument Type` | is this a **depositary receipt**, or the **actual share**? |

Medtronic is a *secondary* listing of the *actual ordinary share*; AstraZeneca's NYSE line is
a *secondary* listing of a *receipt*. Both read `ADR/Cross-listed`, which is exactly why
**Rule A could not be written**: tightening a row's ISIN to its listing country's prefix would
reject **84 legitimate rows** (`ALKS` IE, `CRSP` CH, `BLCO` CA, `CP` CA, `MDT`, `ICLR`) because
an interlisted ordinary correctly carries its foreign ISIN. **This column is what unblocks it** —
verified on the live universe: all six named false-rejections classify `Ordinary Share`, while
`ARGX`/`ASML`/`GSK`/`NVO`/`TEVA`/`SNY` classify `Depositary Receipt`.

**The source is OpenFIGI `securityType2`, not the ISIN-prefix heuristic in the plan.** The
planned rule — *"ISIN prefix equals the listing country ⇒ ordinary; US-prefixed on a US line ⇒
receipt"* — was measured against the 138 cross-listed rows and decides only 65 of them, then
breaks twice: **ISIN follows incorporation, not listing** (12 Cayman-incorporated China
operators on HKEX carry `KYG…`, matching neither country, and all are ordinaries), and **a US
ISIN on a US line is genuinely ambiguous** (33 rows, containing both real ADRs and
US-incorporated foreign operators). OpenFIGI returns literally `"Depositary Receipt"` or
`"Common Stock"`, and CM already trusts it as the authority for the ISIN→issuer identity gate,
so this adds a field to an existing call rather than a new dependency.

**Live result (2026-07-31): 1,093 rows → 1,002 `Ordinary Share`, 26 `Depositary Receipt`,
65 blank.** Blanks are `no-isin` (57) and `no-openfigi-coverage` (8) — reported by name, never
guessed. The 57 no-ISIN rows are the **same population as the `#220` residual**, so closing the
foreign-ISIN coverage gap closes most of these for free.

**Cost is ~10 requests, not 80.** A *primary* listing is the actual share by definition — there
is no such thing as a primary listing of a receipt — so 915 of 1,093 rows decide locally for
free and only cross-listed rows with an ISIN cost a call.

**Three states, never two** (same discipline as `delisted_check` / `ipo_backfill`): `ok`,
plus `no-isin` / `no-openfigi-coverage` / `openfigi-unreachable` / `ambiguous` / `unmapped-type`,
all of which leave the cell **blank**. `ambiguous` means the FIGIs for one ISIN disagree across
venues — resolving that by majority vote would be a coin toss dressed as data. An unrecognised
`securityType2` (preferred stock, units, warrants) is **reported, never folded into the common
case**. Fills blanks only; never overwrites, because a human may have adjudicated exactly the
rows OpenFIGI could not.

**What it deliberately does NOT do:** it describes the **stored ISIN's** instrument, not the
venue. `AZN` is a NYQ/USD line carrying `GB0009895292` (the London ordinary), so it reads
`Ordinary Share` while the ticker actually trades as a receipt. That disagreement is already a
finding — `crosscheck-foreign`'s standing `listing-mismatch` on `AZN`/`FER`/`MDA`/`2359.HK` —
and this column makes it machine-readable rather than papering over it.

Additive, **no schema bump** (the `LEI` / `IPO Date` precedent); surfaced in `exports/universe.csv`
via the snapshot. Verified no consumer couples to column count or position. Exit code `2` when any
row is left undecided. Module `universe/instrument_type.py`; the OpenFIGI fetch gained
`fetch_isin_security_types` alongside `fetch_isin_names` (shared cache; entries predating the
`types` field count as a **miss**, so an older cache cannot answer a newer question with silence).
Tests `tests/test_instrument_type.py` (16) — every OpenFIGI value in them was captured live.

## ISIN → issuer-name identity check (`verify-isin-issuers`)

`python cli.py verify-isin-issuers [--no-cache] [--sample N] [--tickers ...]`
asks OpenFIGI **who each stored ISIN belongs to** and compares that name to the
row's `Company Name`. This is the closure of the same-country wrong-issuer hole
above: `validate_isin_for_row` is a country check; this is the identity check.
Module `universe/isin_identity.py`; tests `tests/test_isin_identity.py` (82 —
every name pair in them was captured live from OpenFIGI against real universe
rows on 2026-07-28, not invented).

**Where it runs, and why.** Two places, deliberately:

1. **The enrich write path** (`enrich_single_ticker` + the bulk
   `fetch_yfinance_identifiers`) — this is the guard. Any ISIN that survives
   the prefix check is identity-checked before it can land in a row, and any
   non-`ok` verdict (**conflict OR inconclusive**) drops the write with a
   loud warning. An unreachable API must never read as validated; a blank
   cell is refilled by the next run, a wrong value looks like data forever.
   Cost control: the bulk path calls OpenFIGI **only for rows whose ISIN
   cell is blank** — filled cells are never written by `enrich_dataframe`,
   so checking them would burn an API call per row for nothing.
2. **The on-demand audit** (`verify-isin-issuers`) — read-only sweep of every
   ISIN-bearing row, exit 2 on any conflict or on a run that learned nothing
   (all-inconclusive), mirroring `crosscheck-foreign`. Not wired into the
   weekly pipeline yet (same posture as `backfill-lei`).

Write-path-only would leave the stock of already-stored ISINs unexamined;
audit-only would leave the front door open. The write path is the part that
must never be skipped; the audit is cheap because of the cache.

**Three states, never two:** `ok` / `conflict` / `inconclusive`
(`openfigi-unreachable`, `no-openfigi-coverage`, `no-company-name`).
Inconclusive is reported as NOT clean, in its own section.

**Matching** (see module docstring for the full design): token cover with
legal-form/share-class stripping, truncation-tolerant prefix equality
(OpenFIGI truncates names at ~28 chars — "SUN PHARMACEUTICAL INDUS";
minimum 4-char stem so "zen" can never match "zentek"), vowel-less
abbreviation matching ("Lbrtrs" ~ "Laboratories" — a real stored name), and a
0.85 difflib floor. **Not a finding by design:** same issuer on a different
line — PharmaEssentia's GDR and Alphabet's Canadian CDR both pass; that class
is `crosscheck-foreign`'s `listing-mismatch`.

**Rate limits / cache (observed live 2026-07-28):** keyless OpenFIGI returns
`ratelimit-policy: 25;w=60` (25 req/min), 10 mapping jobs per request; a full
794-ISIN pass is ~80 requests ≈ 4 minutes. Deterministic outcomes (names and
explicit "No identifier found") are cached in
`cache/openfigi_isin_names.json`; transient failures are never cached.

### First full-universe run (2026-07-28): 794 checked → 742 ok, 21 conflicts, 31 inconclusive

**None of these were auto-applied** — same protocol as the first seven: they
await JP's call. Evidence classes (SEC bulk-map cross-checked where noted):

- **Wrong-issuer ISIN, same disease as the corrected seven** (recycled/collided
  tickers; several also predate the prefix guard and are cross-country):
  `BOI.PA` Boiron→WINGARA AG (AU), `CPH` Cipher Pharmaceuticals→CPH Chemie &
  Papier (CH), `CROX` Crocs→CROSSWOOD (FR), `EVO` Evotec→EKOTECHNIKA (DE — real
  Evotec ISIN `DE0005664809` verified live), `GALD.SW` Galderma→GALADA FINANCE
  (IN), `GNFT` Genfit→GIFT HOLDINGS (JP), `GXI` Gerresheimer→GUIZHOU GUIHANG
  (CN), `MDLA` →MALARASEN AB (SE), `OPT` Optima Health→OPT MACHINE VISION (CN),
  `SDZ` Sandoz→SDM SE (DE), `SOON` Sonova→SOON LIAN (SG), `TUB` Financière de
  Tubize→TUBACEX (ES), `2715.HK` Estun→ELKOP ESTONIA (EE), `DIA.MI`
  DiaSorin→**the DIA ETF** (SPDR DJIA Trust).
- **Multi-field contamination (the ZEN pattern — fix as a SET, not one cell):**
  `MED` (name Medartis/SIX but ISIN+CIK 910329+medifastinc.com = **Medifast**),
  `MOVE` (name Medacta/SIX but ISIN+CIK 1734750+movano.com = **Corvex, ex-
  Movano**), `UCB` (name UCB SA/Brussels but ISIN+CIK 857855+ucbi.com+"Banks -
  Regional" = **United Community Banks**), `ICAD` (chimera: name "Icade SA",
  Euronext Paris, Sector **Biopharma**, ISIN = iCandy Interactive **AU** —
  which company did JP even mean?). Note `ticker_change_check` cannot catch
  these: the wrong CIK maps back to the same ticker, so the contamination is
  self-consistent.
- **Probable renames (the NAME side may be the stale one — confirm first):**
  `ALBT` (ISIN is in Avalon GloboCare's own CUSIP space `05344R`; OpenFIGI
  says CHANGE AGENTS CORP, SEC bulk map still says Avalon GloboCare),
  `FGEN` (ISIN in FibroGen's own `31572Q` space; OpenFIGI says KYNTRA BIO;
  `FGEN` is GONE from SEC's ticker map), `CBIO` (universe name Crescent
  Biopharma is CURRENT per SEC CIK 1253689; the stored ISIN is the
  pre-merger GlycoMimetics line — universe ahead of OpenFIGI on the name,
  behind on the ISIN; PXMD-precedent).
- **Inconclusive, 31** — mostly US micro-caps OpenFIGI has no ISIN mapping
  for (several look like post-reverse-split ISINs). Three are also
  prefix-implausible and deserve suspicion despite the inconclusive verdict:
  `BAVA.CO` Bavarian Nordic carrying `SGXZ32918005` (real BN ISIN
  `DK0015998017` verified live), `SAAS` Microlise (LSE) carrying
  `AU0000297590`, `CSU` Constellation Software carrying `NET000CLBR01`
  (not even a structurally valid ISIN).

Report: `reports/isin_identity_<date>.md`. The 24→21 delta from the first
pass to the committed one is three matcher fixes on real rows (`TEVA`/`TSM`
sponsored-ADR line names, `CRL` vowel-dropped stored name), pinned in tests.

## CRSP / Morningstar US Total Market snapshot

`python cli.py crsp-snapshot [--force] [--skip-levels] [--dry-run] [--no-reconcile]`
archives the **full constituent list of the CRSP US Total Market index** — 3,477
names with weights as of 2026-03-31 — plus the **daily PR + TR index levels for
all 76 CRSP indexes back to 2011-03-31**. Both are free CSVs from crsp.org.

**Why it is scheduled and not a one-off:** CRSP **overwrites** the constituents
file each quarter and keeps no archive. A quarter not captured while it is live
is gone permanently, and the value of the dataset is in the *delta* — a name
leaving the index is a delisting, acquisition, or a fall out of the investable
universe. One snapshot is a list; two are a signal. The job runs **weekly**
(`CrspQuarterlySnapshot`, Mondays 08:00, `run_crsp_snapshot.bat`) rather than
quarterly because CRSP posts a new quarter roughly a month after each rebalance
and the exact date drifts; a weekly poll costs one 2 MB download and returns
`unchanged` until a new `TradeDate` appears.

**Provenance / expected breakage:** Morningstar acquired CRSP (closed
2026-02-02) and the index is being renamed the **Morningstar US Total Market
Index (FS00009VTK)** as of late July 2026; `crsp.org` began redirecting to
`indexes.morningstar.com` on 2026-07-28. **The download URLs are expected to
move.** The module fails loudly (exit 2) rather than silently keeping the last
snapshot — a 4xx is never retried, precisely so a moved path surfaces instead of
burning the run.

**Checked live 2026-07-28 — the data URLs did NOT move.** The *website* redirects
(`https://www.crsp.org/` → `https://indexes.morningstar.com/morningstar-market-indexes`,
301) but the two CSVs do not:

| URL | Status | Redirects | Content-Type | Last-Modified |
|---|---|---:|---|---|
| `…/quarterly-index-constituents/crsp_quarterly_constituents.csv` | 200 | 0 | `text/csv` | Sat, 02 May 2026 01:13:02 GMT |
| `…/daily-index-levels/crspmi_daily_index_levels.csv` | 200 | 0 | `text/csv` | Tue, 28 Jul 2026 02:13:08 GMT |

Both still serve from the same nginx/Pantheon origin, and the levels file was
refreshed the morning of the check (last row `2026-07-27`) — so the feed is
being maintained *behind* the redirect. **A redirecting landing page is not
evidence that the data path moved**; check the two separately. A full live run
into a scratch directory returned `ok` / 3,477 constituents / 1,713
sector-labelled — i.e. the published figures are unchanged post-migration. No
fetch-path code was changed as a result.

### The failure message says which kind of failure it was

"The download failed" was two operational facts wearing one message, and they
want opposite responses: a **moved URL** needs a human today (every retry
returns the same 404 forever), a **transient network** error needs nothing but
the next scheduled run. Reporting them identically forced the reader into the
traceback to find out which — costly on a weekly job whose entire premise is
that a missed quarter is unrecoverable.

`classify_download_failure` now labels every failure and
`SnapshotResult.failure_kind` carries it into the report, the console, and the
`[kind]`-tagged error string:

| Kind | Trigger | What it asks of the reader |
|---|---|---|
| `moved` | 4xx, **or HTTP 200 whose body/content-type is HTML** | find the new path on `indexes.morningstar.com`, update the two constants. Re-running will not help. |
| `transient` | 5xx, `URLError`, timeout — after all 4 attempts | re-run. If it repeats next week, re-classify as moved. |
| `content` | a file arrived, but the schema / index key / row count is wrong | inspect the staged download; the URL itself is working. |
| `unknown` | anything else | read the error text before re-running. |

Two details are load-bearing:

- **A retired CDN path usually answers 200 with the site's landing page, not a
  404** — and that page parses as a one-column CSV. `_download_once` now sniffs
  the first chunk plus the `Content-Type` and raises `SourceMoved` before a byte
  is written, so the diagnosis stays attached to its evidence (final URL after
  redirects + content type) instead of resurfacing later as a baffling schema
  error. A redirect that *does* return CSV is fine and is logged as a warning —
  it is the first visible sign of the migration and must not pass silently.
- **`SourceMoved` is never retried.** It is an *answer*; re-asking an answered
  question only delays reporting the answer.

Guidance strings are **ASCII-only and pinned by a test** (`.encode("ascii")`).
The old message contained a `→`, which the scheduled task's cp1252 console
cannot encode — it would have raised `UnicodeEncodeError` at the exact moment
the job was trying to report why it failed.

### Two gotchas that will bite

1. **The total-market list is keyed under `CRSPTM1`, not `CRSPTMT`.** Constituents
   are identical for the price- and total-return variants, so CRSP publishes only
   one — under the *price*-return ticker, while `CRSPTMT` names the index
   everywhere else. Filtering on `CRSPTMT` returns an empty set with no error.
2. **Sector labels cover 49% of the index, and the missing half is not random.**
   CRSP publishes no sector column; sector is recovered from which of the eleven
   sector indexes a name appears in, and those partition **Core Cap** (1,713
   names), not the total market. All 1,764 unlabelled names are micro-caps.
   `build_classification` returns `sector: None` for them — never a fallback
   string, so "no sector published" stays distinguishable from a real value.
   By **weight** the picture is the opposite: the unlabelled micro tail is
   **1.2% of the index**, so sector work on a weighted basis is ~99% covered.
3. **`CRSPLC1 "Large Cap"` is a composite, not a size tier.** It is Mega ∪ Mid
   exactly (173 + 288 − 18 packeting straddles = 443), and Mega is a strict
   subset of it. Treating the five size indexes as a ladder labelled every
   mid-cap "Large" and erased Mega from the index entirely, depending on row
   order. The disjoint ladder is **Mega / Mid / Small / Micro** (173 / 270 /
   1,270 / 1,764 after resolving the 107 packeting straddles to the larger
   tier). Same trap for the other composites: `CRSPXM1` Core Cap, `CRSPMS1`
   Small/Micro, `CRSPSM1` Small/Mid, `CRSPXE1` ex-Mega — fine as index series,
   wrong as per-name labels.
4. **Style is recorded as an axis, not a box.** The style indexes are *not*
   nested (Mega Growth is not a subset of Large Growth), because CRSP assigns
   style separately within each size band — so no single "Mega Growth"-style box
   is the right label for a name, and last-write-wins gave NVDA `Large Growth`
   while Mega Growth existed. `classification.style` is `Growth` (578) /
   `Value` (1,001) / `Growth+Value` (134) / `None` (1,764 micro-caps).
   `Growth+Value` is a real state: CRSP splits those names across both boxes
   with partial weight in each.

### Outputs

- `data/crsp/constituents_<TradeDate>.csv` — the archived quarter (**gitignored**)
- `data/crsp/classification_<TradeDate>.json` — ticker → `{sector, size, style}`
  derived from index membership
- `data/crsp/index_levels.csv` — daily PR + TR levels, all 76 indexes, refreshed
  in place (cumulative from inception, so each download is a superset of the last)
- `data/crsp/archive/index_levels_<lastdate>.csv.gz` — dated compressed copies,
  2.8 MB each (21% of raw). Written when a **new quarter** lands, or on demand
  with `--archive-levels`. In-place refresh is fine while the source lives; it
  stops being fine the moment CRSP restates history or the URL dies mid-migration,
  because by then the refresh has already overwritten the only copy. Quarterly,
  not weekly — 52 near-identical snapshots a year is hoarding, not provenance.
  Named by the file's own latest `Date`, not the download date, so a re-run or a
  stale upstream file cannot mint a second archive claiming to be newer data.
- `reports/crsp_snapshot_<today>.md` — delta vs the prior quarter + universe
  reconciliation

**`data/crsp/` is gitignored and this repo is PUBLIC.** CRSP licenses these free
downloads for informational, non-commercial use with no redistribution;
committing a full constituent list with weights to a public remote would be
republishing it. The archive is the point of the job, so it must survive on disk
— back it up outside git, not by tracking it here.

### Universe reconciliation — two findings, deliberately separate

`reconcile_universe` compares `coverage_universe_tickers.csv` against the CRSP
list and reports two things that must not be collapsed:

- **Absent from CRSP (437 rows)** — *mostly a domicile fact, not a delisting.*
  CRSP carries US-domiciled exchange-listed operating companies only, so argenx,
  ASML, Ascendis, Alcon, and Amarin are all correctly absent. Useful as a
  cross-check on the cross-listing map; **never** as a delisting flag.
- **Symbol collisions (3 rows)** — a foreign-HQ row whose plain US-style symbol
  belongs to a *different*, US-domiciled company, so any US price or fundamentals
  lookup on the bare symbol returns the wrong issuer. Live findings: `UCB`
  (Belgian UCB SA vs United Community Banks), `CSL` (Australian CSL Ltd vs
  Carlisle Companies), `MED` (Swiss Medartis vs Medifast).

Foreign HQ is a **prior, not a finding**. Flagging on domicile alone produced 12
rows, 9 of which were Irish/UK inversions (Medtronic, Linde, Jazz, Perrigo,
Atlassian) that are foreign-domiciled and legitimately in CRSP under matching
names. What domicile justifies is a *stricter name threshold* (0.70 vs 0.55),
because a foreign row whose name only half-matches is far likelier to be a
collision than a US row scoring the same.

Name comparison is **token-based and prefix-aware**, not a sequence ratio. CRSP
writes names surname-first and truncates words — `Eli Lilly And Co` is `LILLY ELI
& CO COM`, `Henry Schein` is `SCHEIN HENRY INC`, `Pharmaceuticals` becomes
`PHARMA`, `International` becomes `INTER`. A plain `SequenceMatcher` scored those
around 0.5 and produced 9 cosmetic flags that buried the 3 real ones.
`_name_similarity` returns `None` when either side has no comparable token — a
comparison that cannot be made has no result (the same rule `delisted_check`
follows).

Module `universe/crsp_snapshot.py`; tests `tests/test_crsp_snapshot.py` (68).
Non-gating; not part of `weekly-universe`.

## Historical valuation columns (Phase 2 — full universe, 5Y + 10Y)

The weekly performance report appends **26 trailing-valuation columns** after the existing FUND_COLS: the original 13 five-year columns, 12 new **ten-year** columns, and a `History Status` marker. As of **2026-07-19** these are populated for the **full coverage universe** (~1,095 names), not just the positions set.

### Why it was only ~77 names before

Nothing was capping it — no quota guard, no filter bug, no partial backfill. `reporting/generate.py:_load_phase1_tickers()` deliberately scoped the fetch to the union of the five position-state export files (77 tickers on 2026-07-19; 76 had cache entries), because the fetch ran **inline during the weekly performance run** and a full-universe fetch inside that run wasn't something we wanted to pay for. The skew to Tech/Consumer/Industrials was simply the sector mix of the positions file, and it left the ~1,000-name HC universe absent from every downstream "valuation vs history" view.

### The fix: decouple fetching from reporting

- **`python cli.py history-backfill`** (`universe/history_backfill.py`) populates the cache for the whole universe. Resumable; safe to run weekly.
- **The report** (`reporting/generate.py`) fetches position names **live** (unchanged behaviour) and reads every other universe name **`cache_only=True`** — zero API calls, zero added runtime. Names the backfill hasn't reached yet come back `not_attempted` and render `N/A`, never `0`.

So widening coverage costs the *report* nothing; the cost lives in a separate weekly command.

### Cost — and why 10Y is free

3 FMP calls per uncached ticker (annual `ratios` + annual `key-metrics` + `ratios-ttm`) — the **same 3 calls** that already served the 5Y columns. Full cold universe ≈ 1,095 × 3 = **~3,285 calls**, ~11 min at the client's self-imposed 300/min. Warm weekly passes are far cheaper (30-day TTL, so only aged-out + new names).

**The 10-year window costs ZERO extra calls.** Probed live 2026-07-19: FMP **Starter** returns **15** annual rows from `ratios` and `key-metrics`. The older `reference_fmp_starter_tier` note that Starter is "5yr annual only" is **wrong for these two endpoints**. The 10Y window is the same request with a higher `limit`, and the 5Y stats are the first 5 elements of that same series. Do not add a second round of calls for the 10Y columns.

### Resumability

The per-ticker cache file **is** the resume state — there is no separate cursor to corrupt. Every run skips tickers with a fresh, current-schema entry (`fmp_history.is_cached`), so a pass that dies at name 600 doesn't restart from zero. `--limit N` bounds a run (the intended way to test, or to spread a cold backfill over days). A run summary lands in `cache/key_metrics_history/_backfill_state.json`.

### No silent failures — missing vs unattempted

`History Status` records **why** a row is blank. All numeric fields are `None` (rendered `N/A`) in every non-`ok` case — never `0`, because a `0` in a P/E-min column would corrupt every downstream valuation screen.

| Status | Meaning | Cached? |
|---|---|---|
| `ok` | data present, and **every** endpoint succeeded | yes, 30d |
| `no_data` | FMP answered and has nothing for this ticker — a recorded fact | yes, 7d (retried sooner) |
| `gated` | FMP returned **402** on all three endpoints — our plan can't see this symbol | yes, 30d |
| `error` | a call failed transiently, or only *some* endpoints succeeded | **no** — retried next run; logged as a warning |
| `not_attempted` | backfill has never reached this name | n/a |

Three distinctions here are load-bearing (all three were Codex findings on 2026-07-20, and each had already shipped as a live bug):

1. **A provider failure is not `no_data`.** `_fmp_request` returns `None` on a 402/non-200 and FMP signals some errors with a JSON object (`{"Error Message": ...}`) rather than an HTTP status. Both used to report `errored=False`, so an outage or an expired key got cached as authoritative "this ticker has no history" — and `history-backfill` then skipped it. Only an actual `list` counts as success; an empty list is still a real fact and still cached. `ratios-ttm` legitimately returns a dict, so there an error payload is detectable **only by its keys** (`_is_fmp_error_payload`).

2. **A partial success is not `ok`.** Errors are evaluated *before* `has_data`. Previously a failed annual-ratios call plus a working `ratios-ttm` produced `status=ok` with `pe_history` all `None`, cached for 30 days, and skipped by the backfill as "already cached" — a transient blip frozen into a permanently blank valuation history.

3. **`gated` is not `error`.** A 402 is a *permanent* plan limit, not a transient failure. ~170 of 1,095 names are foreign lines (`ROG.SW`, `4543.T`) that 402 on all three endpoints — verified live. Classing them `error` would mean never caching them and re-issuing ~510 calls every single run, none of which can ever succeed; classing them `no_data` would claim the company has no history when really we just can't see it. So `gated` is cached (30d, tier access rarely changes) and named honestly.

Caching an `error` is specifically avoided: it would freeze a transient failure into a permanent-looking blank column.

### Known data caveat — negative/charge-hit fiscal years

The series are raw FMP annual P/E, which goes **negative or enormous** in loss or one-off-charge years. Verified real (not bugs), each landing on a known event: `MRK` FY2023 P/E **778.7** (Prometheus IPR&D charge crushed EPS), `LLY` FY2017 **-444.5** (Tax Cuts & Jobs Act charge), `CAT` FY2016 **-843.1** (mining-downturn net loss), `VRTX` FY2024 **-193.6** (Alpine IPR&D charge).

**Consequence:** an arithmetic mean over such a series is not a usable "average valuation" — e.g. `LLY` P/E 10Y Avg computes to **-2.2** and `CAT` to **-56.3**. This affects the **pre-existing** 5Y columns identically, so it was NOT changed here (that would silently alter numbers downstream already consumes). Open decision for JP: winsorize, drop non-positive P/E years, or switch to a median. Until then, downstream screens should prefer **EV/S** (always positive, unaffected) or filter on `P/E 5Y Min > 0`.

### Columns (in order)

Existing 13 (unchanged — **never rename or reorder**, downstream reads by name):

| Column | Source | Format |
|---|---|---|
| P/E (TTM) | FMP `/stable/ratios-ttm` `priceToEarningsRatioTTM` | float, 1dp |
| P/E 5Y Avg | mean of the first 5 elements of FMP `/stable/ratios?period=annual` `priceToEarningsRatio` | float, 1dp |
| P/E 5Y +1σ | avg + sample stdev (n-1) | float, 1dp |
| P/E 5Y -1σ | avg − sample stdev | float, 1dp |
| P/E 5Y Min / Max | min/max of the 5Y series | float, 1dp |
| P/E vs 5Y Avg | (TTM − avg) / avg × 100 | percent; **red = premium, green = discount** (inverted vs return colors) |
| EV/S 5Y Avg / +1σ / -1σ / Min / Max | from FMP `/stable/key-metrics?period=annual` `evToSales` | float, 1dp |
| EV/S vs 5Y Avg | (existing TTM `EV/S` column − avg) / avg × 100 | percent; same red/green semantics |

New 10Y block (appended 2026-07-19; same series, elements 1–10):

| Column | Source | Format |
|---|---|---|
| P/E 10Y Avg / +1σ / -1σ / Min / Max | same annual `priceToEarningsRatio` series, 10 elements | float, 1dp |
| P/E vs 10Y Avg | (TTM − 10Y avg) / 10Y avg × 100 | percent; red = premium, green = discount |
| EV/S 10Y Avg / +1σ / -1σ / Min / Max | same annual `evToSales` series, 10 elements | float, 1dp |
| EV/S vs 10Y Avg | (TTM `EV/S` − 10Y avg) / 10Y avg × 100 | percent; same semantics |
| History Status | `ok` / `no_data` / `error` / `not_attempted` | string |

### Why a new "P/E (TTM)" column

The pre-existing "Fwd P/E" column is **inconsistent** across providers:
- yfinance puts `forwardPE` (NTM, forward) → label "Fwd P/E (NTM)" is correct
- FMP puts `priceToEarningsRatioTTM` (trailing) → label is wrong for FMP-sourced rows

Comparing forward to a 5-year trailing average is apples-to-oranges, so the Phase 1 feature adds a separate "P/E (TTM)" column populated **always from FMP** regardless of which provider was primary. EV/S TTM is consistent across providers (yfinance `enterpriseToRevenue` and FMP `priceToSalesRatioTTM` are both trailing), so no new EV/S TTM column was needed.

### Caching

- Namespace: `cache/key_metrics_history/<TICKER>.json` (+ `_backfill_state.json` run summary)
- TTL: 30 days for `ok`, 7 days for `no_data`; `error` is never cached
- Schema **v2**: `{status, pe_ttm, pe_history[10], evs_history[10], record_dates[10], fetched_at, schema_version}` — most-recent-first, padded with None to length 10
- **v1 entries (5-element, no `status`) are treated as a cache miss and refetched** — `_cache_entry_usable` checks `schema_version`, so an old payload can never be silently mis-parsed as a 10Y series

### Running it

```bash
python cli.py history-backfill                      # full universe, resumable
python cli.py history-backfill --limit 200          # spread a cold backfill over days
python cli.py history-backfill --tickers MRK,PFE    # targeted
python cli.py history-backfill --refresh            # bypass cache (expensive)
```

Not yet wired into the weekly pipeline — run on demand (like `backfill-lei` / `ipo-backfill`). Wiring it as a weekly `weekly-universe` step is the obvious next increment; JP has confirmed a 1×/week cadence is sufficient.

### Still deferred

- HTML report rendering (`reporting/html.py` iterates `FUND_COLS`, doesn't include `HIST_COLS`) — the columns are Excel/pickle-only
- Wiring `history-backfill` into the weekly pipeline
- The negative-P/E-mean decision above

## P/E vs forward-2yr-EPS-growth scatter (Phase 1)

The performance run renders a scatter of **P/E (TTM)** (y) vs **annualized forward 2-year EPS growth** (x) for the Phase 1 set (positions ∪ research), written to `reports/coverage_pe_vs_growth_<date>.png` and attached to the performance email. Built as `run_step("pe_growth_chart")` in `reporting/generate.py` after `result_df`.

- **Y-axis = P/E (TTM)** — reuses the FMP-sourced `P/E (TTM)` HIST column (currency-consistent), NOT the provider-inconsistent `Fwd P/E`.
- **X-axis = forward 2yr EPS-growth CAGR** — new data source `providers/fmp_estimates.py` (`/stable/analyst-estimates?period=annual`, verified on the FMP **Starter** tier 2026-06-13; legacy `/api/v3` 403s). Phase-1-scoped, 30-day cache (`cache/analyst_estimates/`), parallel fetch — same scope/cadence rationale as `fmp_history.py`. The CAGR math is the pure, unit-tested `reporting/calcs.forward_2yr_eps_growth_pct(records, today)`: FY0 = first estimate with fiscal year-end ≥ today, FY+2 = two fiscal years later, `(eps_FY+2/eps_FY0)**0.5 - 1`; returns None for <3 forward years or non-positive EPS (CAGR through zero/negative is undefined — those names just drop off the chart).
- **Rendering** is thin/side-effect-only in `reporting/charts.py` (matplotlib Agg, added to `requirements.txt`): dots sized by market cap, colored by Sector (JP), labeled with ticker, median guide-lines marking the cheap/expensive × low/high-growth quadrants.
- **S&P 500 is intentionally excluded** — the benchmark tab is built price-only (no fundamentals, no P/E) to keep the run fast; a 500-name fundamentals pull is the expensive path the architecture avoids. Portfolio/Phase-1 only.
- Internal report artifact only — does **not** touch the `exports/` contract. Tests: `tests/test_pe_growth_chart.py`.

## Point-in-time estimates archive (2026-08-12)

**`cache/analyst_estimates/` is a snapshot, and a snapshot is not a record.** It holds one
overwritten blob per ticker with a `_cached_at` stamp, so it always answers *"what does the
street forecast now"* and can never answer *"what did the street forecast then"*. The second
question is the one a forward-P/E history needs, it is **unanswerable retroactively** (FMP
Starter sells no point-in-time consensus and nothing else in the fleet stored one), and every
week not recorded is a week of history nobody can buy back.

`providers/estimates_history.py` appends alongside the cache: one JSON line per
`(ticker, observation date)` in **`data/estimates_history/<TICKER>.jsonl`**. Wired into
`fetch_estimates` immediately after `cache_set`, and **non-gating by construction** —
`record_observation` swallows its own errors, because an archive is a side effect of fetching
and must never break the fetch that feeds the live report.

Four properties worth knowing before touching it:

- **Cadence is the cache TTL (~30 days), not the run schedule.** `fetch_estimates` returns
  early on a cache hit, so observations land roughly monthly per ticker. That is deliberate:
  annual EPS estimates move slowly, and JP's own `SPY vs DGX PE.xlsx` samples forward P/E
  **monthly**, so the granularity already matches the artifact this exists to reproduce. Want
  denser? Shorten the TTL — do not add a second fetch path.
- **Idempotent per calendar day.** A re-run cannot stack two observations onto one date and
  silently double-weight it in any series built later.
- **An empty or all-null curve is NOT recorded.** *"We asked and the vendor had nothing"* is a
  fact about the vendor; writing it as an observation would put a hole in a series whose whole
  premise is that its points are real readings.
- **`data/estimates_history/` is gitignored, same reasoning as `data/crsp/`.** THIS REPO IS
  PUBLIC and these are licensed FMP rows, so they must not be republished — but the archive is
  irreplaceable, so it must survive locally. Dropbox is the backup. Do **not** "fix" this by
  tracking it.

Consumer context: deep_dd's C4 valuation module needs a multiple-history band, and until this
series has depth its only honest bases are trailing P/E (no look-ahead bias) or a
perfect-foresight forward P/E (rejected — its error correlates with outcomes). See
`deep_dd/BUILD_PLAN_C1-C5.md` §8 Q3. Tests: `tests/test_estimates_history.py` (9).

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

## Foreign lines that collide with a US namesake (found 2026-07-27)

A universe row for a foreign company carrying a **bare US-style ticker** silently
pulls a *different company's* fundamentals into the report and every export.
`normalize_ticker` only appends an exchange suffix when `Exchange` is non-US, so
a wrong `Exchange` value yields a bare symbol that Yahoo happily resolves to
whoever owns it in the US:

| Row | Was pulling | Actual |
|---|---|---|
| CSL Ltd (Australia) | Carlisle Companies, $13.4bn | **CSL.AX**, A$55.7bn |
| UCB SA (Belgium) | United Community Banks, $4.3bn | **UCB.BR**, EUR 46.8bn |
| Ipsen SA (France) | an SPDR ETF | **IPN.PA**, EUR 13.1bn |
| Medartis (Switzerland) | Medifast, $119M | **MED.SW**, CHF 1.08bn |
| Medacta (Switzerland) | Corvex, $351M | **MOVE.SW**, CHF 2.64bn |

**The failure is self-concealing**: `Country (HQ)` and `Exchange` had been
auto-enriched *from the wrong symbol*, so all four columns agreed with each
other and looked clean (CSL Ltd read "United States / NYSE"). Fixing `Exchange`
is therefore the fix — the suffix map already had every needed entry.

Two cases `Exchange` cannot express go in `MANUAL_TICKER_MAP` instead: Yahoo
hyphenates Nordic B-shares (`COLOB DC` -> **`COLO-B.CO`**, not `COLOB.CO`;
`GETIB SS` -> **`GETI-B.ST`**), which had been returning a `MUTUALFUND`
quoteType with no usable fundamentals at all.

Caught by `delisted_check`'s name-mismatch rule, which is exactly what it is for.
Pinned by `test_foreign_rows_resolve_to_their_own_listing`. **When adding a
non-US name, verify `normalize_ticker` returns a suffixed symbol** — a bare one
for a foreign company is the tell. Poisoned `cache/fundamentals/yf_<T>.json`
entries must be deleted when fixing one, or the wrong data is reused.

**`enrich` is a SCHEDULED lane as of 2026-08-06** (`CoverageManager-EnrichWeekly`,
Sun 03:00) and is deliberately NOT a step of `weekly-universe`. The weekly pipeline
runs `cik_backfill` but not `enrich`, which was invisible while the universe was
static — and became a real gap the moment JP's decision made it *grow* (1,086 →
1,328 on 2026-08-06): the 242 new rows got CIKs automatically on Friday, while
ISIN, Country (HQ) and Currency only filled when a human remembered. It is off the
Friday critical path on purpose: it fetches all ~1,330 tickers from yfinance,
took ~1.5h at 30s-per-ticker timeouts, and Friday's build is the publish contract
that has already been killed mid-run once. Sunday 03:00 cannot collide with the
Friday build or the reply-poller, which write the same CSV.

## Two known data facts that look like bugs (2026-08-06)

**`PBLS` market cap is wrong at the VENDOR, not in this repo.** FMP returns
~$0.12B for Parabilis Medicines against a price of $34.66 and
`sharesOutstanding: null` — i.e. ~3.5M shares for a company whose IPO raised
$670M upsized. The candidate ledger recorded ~$3.7B at IPO, which is the
credible figure. **It is not verifiable from SEC**: PBLS IPO'd 2026-06 and has
not filed a 10-Q, so `dei:EntityCommonStockSharesOutstanding` 404s. The CIK
(1657677) IS correct — checked against SEC submissions, which returns
"Parabilis Medicines, Inc.", ticker PBLS, Nasdaq. Nothing in the universe CSV
stores market cap, so there is nothing here to fix; the consequence is that
PBLS sorts near the BOTTOM of every cap-ranked screen and chart until FMP's
share count is right or the first 10-Q lands. Do not "correct" it by hand —
recheck after the first quarterly filing.

**`KRC` under `Healthcare Services / Healthcare Real Estate` is a judgement
call, not a misclassification.** FMP classifies Kilroy Realty as `REIT - Office`
and on that alone it looks wrong. It is in the universe on its life-science lab
exposure — the same rationale that puts Alexandria (`ARE`) there, and ARE is
classified identically. Reclassifying KRC without also revisiting ARE would make
the taxonomy less consistent, not more. Left as-is deliberately; if the HC-REIT
sub-universe is ever revisited, revisit both together.

## Key conventions
- Sector classification uses `Sector (JP)` and `Subsector (JP)` columns (user-defined taxonomy)
- Market cap, EV, and Net Debt are converted to USD at report time
- Price stays in local currency
- Performance reports are emailed and posted to Slack `#stock-price-alerts` via `SLACK_WEBHOOK_URL` in `.env`
- `--refresh` flag bypasses cache reads and refetches from APIs. Avoid it on full runs unless you really need it; provider latency, especially Finnhub on cold cache, is still the main runtime cost
- The weekly scheduled task runs via `C:\Users\jroyp\run_weekly_coverage.bat` every Friday at 8am (uses `--dangerously-skip-permissions` for unattended execution). **2026-06-29 hardening:** the bat runs a **deterministic exports-publish backstop** (`"%PYTHON%" cli.py weekly-universe --skip-discovery`) UNCONDITIONALLY after the headless claude session, because on 2026-06-26 the headless `claude -p` session backgrounded the build and exited (no re-invocation in `-p` mode), leaving `exports/manifest.json` 10 days stale while the task showed rc=0. The backstop guarantees exports regenerate regardless of what the agent did; `weekly_coverage_prompt.md` also carries a CRITICAL rule forbidding backgrounding the build. The backstop, `git commit`, and `git push` each capture their exit code and `goto` a fail-label (`endlocal & exit /b <rc>`) so a failed publish/commit/push turns the task **RED** instead of green-but-stale (Codex-reviewed 2026-06-29). **2026-07-02:** added a second **deterministic performance-report backstop** — after the exports publish + git push the bat runs `"%PYTHON%" cli.py performance` UNCONDITIONALLY, because the full consolidated coverage report (`coverage_consolidated_*.html` + per-segment HTML + xlsx) is produced ONLY by the reporting-side performance step, which **no scheduled task ran** — so it silently went stale after 2026-05-29 while the Monday `WatchlistMondayReport` kept the positions-only `watchlist_report_*.html` fresh. It runs *after* git push so a transient provider hiccup never blocks the critical exports contract; `reports/` is gitignored so nothing is committed; `EMAIL_ENABLED=False` so no email is sent (`cli.py performance` honors the flag). It captures `PERF_RC` and `goto perffail` (RED) on failure so it can't silently drift again. Runtime ~17min (Finnhub cold-cache 62s rate-limit pauses dominate). Note: `weekly_coverage_prompt.md` line 230 named a dead `generate_performance.py` — **corrected 2026-07-20** (`262a85d`) to an explicit do-NOT, since the bat backstop already covers it. Keep that bat **CRLF + ASCII + goto-style (no paren blocks)**. **2026-07-20 — PRE-FLIGHT publish added, and the 07-17 root cause corrected.** The 07-17 red run (`0xC000013A`) was **not** caused by the dead `generate_performance.py` reference — that had been documented as harmless right here since 07-02. What actually happened: the task fired **13h late** (`LastRunTime` 21:04 against a Fri 08:00 trigger — the machine was off, so `StartWhenAvailable` caught it up), and the process tree was killed ~26s into `claude.exe`. `0xC000013A` is `STATUS_CONTROL_C_EXIT` — a console/shutdown kill, consistent with the laptop being shut down or sleeping right after the late start. The log (`.health/weekly_coverage_2026-07-17.log`, 942 bytes) stops mid-`claude.exe` with no `claude.exe headless exit code:` line, which is the tell: the bat itself was terminated. **The structural lesson is that every backstop ran *after* the long, fragile `claude.exe` step, so a mid-run kill defeated all of them at once** — exports sat 9 days stale, which in turn froze `exports/reporting_calendar.json` and disabled transcripts' skip gate (`CAL_SKIP_MAX_AGE_DAYS=8`), burning its shared AlphaVantage budget. Fix: the bat now runs `cli.py weekly-universe --skip-discovery` **before** the claude session as a non-gating pre-flight, so the published contract is refreshed within minutes of the task starting and a later kill leaves seven consumers current instead of stale. The post-session publish still runs and is still gated — the pre-flight is an additional floor, not a replacement. Also fixed that run's second finding: this workspace had **`hasTrustDialogAccepted: false`** in `C:\Users\jroyp\.claude.json` (the parent `Claude Folder` was `true`), so the headless session logged *"Ignoring 35 permissions.allow entries"* and ran with degraded permissions even when it wasn't killed. Set to `true` 2026-07-20. ⚠ The bat lives at `C:\Users\jroyp\run_weekly_coverage.bat`, which is **not in any git repo** — a pre-change copy is in `diagnostics/task_backup_2026-07-20/`.
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

## Exports are BOM-free, and are read back before shipping (2026-07-27)

**The published CSVs are UTF-8 without a BOM, and that is now enforced.** A BOM reached
`data/coverage_universe_tickers.csv` around 2026-07-25; `_step_export_artifacts` read its
fieldnames as plain utf-8, so the first field was `"﻿Ticker"` while every row set
`row["Ticker"]` — and `DictWriter(extrasaction="ignore")` silently dropped the join key from
everything it wrote. Measured cost, reproduced with each consumer's own read path:

| Artifact / consumer | Result |
|---|---|
| `exports/positions_and_researching.csv` | 84 of 84 rows blank `Ticker` |
| `exports/watchlist.csv` | 66 of 66 blank (undetected until an audit) |
| `earnings_agent/coverage.py:212` | **0 of 1,086 tickers** — Tier 2/3 collapsed |
| `post_earnings_movers/pem/universe.py:97` | 0 of 1,086 — cuts and position badges gone |
| `analyst-days/src/universe.py:180` | 0 rows matched any sector |

All of it while `positions_status.json` said `validation_passed: true`, because **validation
ran on the source and nothing read the published artifact back**.

Three rules follow, and each is pinned by a test:

1. **BOM-free is canonical.** This reverses the 2026-07-16 ratchet, which made the BOM
   canonical because that was what the files happened to contain. One canonical form was the
   right goal; BOM-ful was the wrong form. A `utf-8-sig` reader handles a BOM-free file, a
   plain-`utf-8` reader does not handle a BOM, and most consumers are the latter — so for a
   file ~20 siblings join on, the encoding must be the one that survives the least careful
   reader. `ticker_utils.write_universe_csv` writes plain utf-8.
2. **Read the source with `utf-8-sig` anyway.** Tolerant in, strict out.
   `universe/artifacts.py:71` always did this and said why; the exporter did not.
3. **Never `extrasaction="ignore"` on an export writer.** It converts a header/row key
   mismatch — a bug — into silent data loss. Let it raise.

**Step `[5d/6]` `check_published_exports`** (`universe/export_acceptance.py`) re-opens every
published CSV with the encoding the least careful consumer uses, asserts the join key is
present and populated, and cross-checks row counts against the status file that claims to
describe them. It runs after every writer and before `sigma_export`, so a broken contract is
reported before it propagates to the sibling repo. Deliberately dumb and deliberately last:
it shares no code with the writer, so it cannot share a bug with it. Non-gating — the universe
CSV update is still the real product — but it turns a silent green zero-ticker export into a
visible failure in the run summary and the health heartbeat.

**Extended 2026-07-28 (Codex R5).** The acceptance step now also fails on: an **empty**
universe/positions CSV (a header-only file has no BOM and no blank keys — it is simply empty,
and previously passed vacuously); bytes that are **not valid UTF-8** (reported as a problem
naming the artifact and offset, never an unhandled decode crash); a `watchlist.csv` row count
contradicting `watchlist_status.json.entry_count` (that file previously had NO status
cross-check — the 66-blank-row incident went undetected until an audit);
`universe_metadata.json` entry count contradicting `universe_status.json.ticker_count`; and
two **cross-artifact** consistency rules — every positions/watchlist `Ticker` must exist in
`universe.csv` (a ticker that doesn't is a hollow row with blank universe columns), and the
five per-state position JSONs must partition `positions_and_researching.csv` (keys a subset
of its tickers, counts summing to its row count). The module stays stdlib-csv/json only and
imports nothing from the writers — keep it that way, so it cannot share a bug with them.

**Hardened again the same day (Codex adversarial round 1).** The extension above shipped with
three defects **of the exact class the module exists to catch** — acceptance reporting clean while
the exports were broken. All three fixed, each pinned by a test written first and confirmed failing:

1. **A missing required artifact was treated as optional.** An absent `universe.csv` or
   `positions_and_researching.csv` recorded `None` and `continue`d, so every downstream check was
   skipped and `check_exports(strict=False)` — the mode the weekly pipeline calls — returned `[]`.
   An absent required artifact is the *worst* state, not a neutral one: there is nothing to misread,
   so consumers silently keep using whatever stale copy is on their disk. `CHECKS` now carries an
   explicit **`required`** flag, deliberately separate from `min_rows` — "may not be empty" and "may
   not be absent" are different contracts, and inferring one from the other is what hid this.
   `watchlist.csv` stays optional (deprecated filtered subset, legitimately absent or empty).
2. **A missing position-state JSON was silently ignored** — it set `all_present = False`, appended
   no problem, and skipped the partition check entirely. Now reported: *"I could not check"* must
   never read as *"I checked and it is fine."*
3. **The partition check was fooled by duplicate membership.** It compared a **sum of counts**
   against the row count, which two breakages satisfy by coincidence: put `AAPL` in two states and
   drop `MRNA`, and the total still matches while a name has silently vanished. A partition is now
   verified **as a partition** — set equality in both directions plus pairwise disjointness — and
   it *names* the missing and double-assigned tickers rather than reporting a totals mismatch.

**Fixed AS A CLASS 2026-07-29 (Codex adversarial round 3).** The unifying finding: the five
defects above were each fixed only on the artifact class that *exposed* them — the position-state
JSONs — while the identical bug classes stayed live on the **CSV and status-file siblings**. Three
rules now govern the module, stated in its docstring so the next fix cannot land on one sibling and
miss four:

1. **`check_exports(dir, strict=False)` must NEVER raise** — it is what the weekly pipeline calls,
   and a diagnostic that crashes is an outage. Four confirmed crashes are now findings: a
   `universe.csv` that is **locked, permission-denied, or a directory** (`exists()` is true for all
   three; **`exports/` lives in Dropbox, which briefly locks files mid-sync**, so this was the
   likeliest of the lot), a **CSV field over csv's 131,072-char limit** (one bloated `Notes` cell —
   reported, not worked around, because every consumer using stock csv hits the same wall),
   **deeply-nested JSON** (`RecursionError` is neither `OSError` nor `ValueError`, so it escaped
   every `json.loads` site), and a **non-object status file**. The BOM is now sniffed with
   `open('rb').read(3)` instead of `read_bytes()[:3]`, which loaded a multi-MB file to inspect three
   bytes.
2. **A wrong SHAPE is a recorded problem naming the shape** — never a crash, never a silent skip.
   `positions_status.json` containing `null` (the classic truncated/failed atomic write) raised
   `AttributeError`; `universe_metadata.json` as a bare number raised `TypeError`, and as a bare
   *string* silently measured its **character count** against `ticker_count`.
3. **"I could not check" must never read as "I checked and it is fine."** Deleting
   `universe_metadata.json` returned `[]` (so `JSON_COUNT_CHECKS` grew a `required` flag mirroring
   `CHECKS`); an absent `universe_status.json` silently skipped the row-count cross-check; and a
   **BOM'd `positions_status.json` claiming 84 entries against a 1-row CSV shipped CLEAN** — the
   parse failure was swallowed with no problem recorded, which is the founding incident's own
   failure mode on the status side. A **fourth** instance of the class, found while in there: a
   status file that parses fine but whose count field is **absent or non-numeric** (`"84"`, `null`,
   `true`) disabled the cross-check with nothing said. Parses were also **split per file** — both
   were done in one `try`, so a corrupt `universe_status.json` was reported as
   *"universe_metadata.json: unreadable as JSON"*, sending the operator to the wrong file.

Two further changes: the **duplicate half** of the partition check moved *outside* the
"all five state files parsed" gate — a ticker in two files that **both parsed** is double-assigned
whatever the unreadable file contains, so there is zero false-positive risk (the missing half stays
gated, and `NOT VERIFIED` is unchanged); and every problem string is **ASCII-sanitized at the exit**
(`_ascii`), because company names and tickers from a global universe reach these messages and a
cp1252 console would die at the moment it is reporting why the publish is broken.

`reporting_calendar.json` was added to `JSON_COUNT_CHECKS` **but not marked required**, deliberately:
every `required=True` artifact shares the property that its absence makes a consumer silently serve
**wrong** data from a stale copy, whereas transcripts' documented contract for the calendar is
zero-false-skip — an absent calendar degrades to a normal fetch (correct, merely expensive). What it
must never do is *disagree with its status file* while present, and that is now checked. Flip one
flag if the owner rules otherwise. `watchlist.json` / `manifest.json` stay unchecked (deprecated /
directory file).

**When adding an export:** add it to `CHECKS` in `export_acceptance.py` with its join key **and its
`required` flag**. An artifact nothing validates is an artifact that will eventually ship empty —
and a check that can be skipped, short-circuited, or satisfied by a coincidence is not validation
either. A published artifact's **status file is part of the same contract**: if the artifact is
present, an absent status file is a finding (`CsvCheck` deliberately has no separate
`status_required` field), and an absent *optional* artifact takes its status file with it.

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
