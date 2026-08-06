# Coverage candidate approval loop — plan

**Status:** scoped 2026-07-28, not started. Prerequisite (route the weekly post to `#ipo-spinoffs-newissues`)
shipped in `bf3840c`.

**The ask (JP, 2026-07-24):** new IPOs post to `#ipo-spinoffs-newissues`; a message asks whether to add the name
to coverage; JP answers **yes/no in-thread** and the universe CSV updates. The
add-to-coverage decision is explicitly **independent of whether JP wants to participate in the
IPO itself** — the thread must never read as "should I buy this?"

---

## 1. Why this is smaller than it looks

The structured half of this pipeline **is already built and tested**. It has simply never been
fed.

| Half | State |
|---|---|
| **A — discovery → human report** | Alive. `weekly_coverage_prompt.md` runs headless every Friday, writes `reports/weekly_coverage_universe_additions_<date>.md` + backgrounds, posts to Slack, drafts an email. |
| **B — structured → staged → committed** | **Dead code.** `discovery/candidates.py` ships `validate_discovery_output` / `stage_candidates` / `read_staged_candidates` / `commit_staged_candidates`, a JSON Schema (`discovery/discovery_output_schema.json`), and `weekly_universe._step_discovery` to drive them. Covered by `tests/test_discovery.py` + `tests/test_universe_csv_roundtrip.py`. |

The two halves have never been connected. `_step_discovery` reads
`data/discovery_output_<date>.json` — **a file the prompt is never told to write.** Verified
2026-07-28: zero `discovery_output_*.json` files have ever existed, the prompt contains zero
references to the schema, and the only artifact in `data/` from this lane is
`discovery_input_2026-03-30.json`. So the step logs *"No discovery output found"* and no-ops —
and the Friday task passes `--skip-discovery` anyway.

**Consequence, measured:** 15 recommended names have queued up with nothing added to the
universe CSV since at least 6/19 — SKHY, MBGL (7/10) · BSP, DPC, RKLB (7/03) · SNDK, EROC, PBLS
(6/19) · FPS, MANE, MWH, KARD (6/26) · CSQR, STDN, 2475.HK (7/24). Prompt step 13 is *"Ask me
which additions I want to add"*, which cannot execute in a headless `claude -p` session. There
is no one to ask, so the queue only grows.

**So this is a wiring job, not a build.** Do not rewrite `discovery/candidates.py`.

---

## 2. Increments

### S1 — Make discovery emit structured output (~1 session)

Have the weekly prompt write `data/discovery_output_<date>.json` conforming to the existing
schema, **alongside** the markdown report (the report stays — it is the readable artifact).
Add one field to the schema: `status` ∈ `pending | approved | declined | expired`.

Unblocks immediately, before any approval work exists:
- a machine-readable backlog instead of prose re-derived weekly from last week's report
- `universe_status.json:last_discovery_run` starts telling the truth (`_find_last_discovery_run`
  globs for exactly these files, so it has always been `None`)
- `_step_discovery` stops no-opping

**Risk: none.** Nothing touches the universe CSV until a human approves.

### S2 — A durable candidate ledger (~1 session) — *highest value per hour*

Today "pending" is prose, re-derived each Friday by an LLM reading the previous report. It has
already drifted: 15 names accumulated with no record of when each was first proposed, and no
way to tell a name JP silently passed on from one nobody ever asked him about.

Add `data/candidate_ledger.csv`, append-only, one row per candidate ever proposed:

```
ticker, company, first_proposed, last_seen, trigger, sector, subsector,
status, decision_date, decision_source, slack_thread_ts, notes
```

The weekly run reconciles rather than rewrites: new candidates appended, existing rows' `last_seen`
bumped, decided rows never touched.

> This is the same lesson as the fleet triage board (`PROJECT_IDEAS.md` #183: prose scanned by
> an LLM is the wrong data model for a queue). Do not re-learn it here.

### S3 — In-thread approval (~1–2 sessions) — *the actual ask*

1. Post the weekly summary to `#ipo-spinoffs-newissues` via `scripts/post_coverage_to_ipo.py`; capture the returned
   `ts` and write it to `slack_thread_ts` on this week's ledger rows.

   **The thread already carries each company's full briefing** (shipped 2026-07-28) — business
   description, financials, bull/bear, swing factor. So approval happens in the same thread as
   the evidence: JP reads the case and replies `add CSQR` under it, with no context-switch to
   Dropbox and no ambiguity about which candidate a bare `yes` refers to. Build the poller
   against that thread, not a separate approval message.
2. A poller reads `conversations.replies` on those threads, filters to JP (`U0ALRRASV6X`), and
   parses a small grammar: `add TICKER` · `no TICKER` · `add all` · `skip all` ·
   `add TICKER following` (S4 adds `revive TICKER`).
3. Applies decisions to the ledger, commits approvals to the universe CSV via
   `commit_staged_candidates`, and **replies in-thread** confirming exactly what changed.

**Reuse, do not rebuild.** `agentic_trading/engine/slack_replies.py` is the working template —
persisted `ts` cursor, approver gate, idempotent apply, and the cursor advances even on messages
it ignores so a non-command reply can't wedge the loop. `earnings_agent/slack_api.py:fetch_thread_replies`
is the fetch primitive.

**Scopes: already confirmed sufficient (2026-07-28).** ClaudeBot holds
`chat:write, channels:history, channels:read, users:read, im:read, im:history, incoming-webhook,
files:write`, and `conversations.replies` was verified live against the thread posted to `#ipo-spinoffs-newissues`
(`ts 1785262763.598659`). **No new Slack app configuration is required.**

**Where it runs.** Replies arrive whenever JP reads Slack, so the poll needs a schedule.
Recommend folding it into the existing **`notion_watchlist daily sync` (07:30 ET)** — it already
runs daily, already writes to CM, and already posts to `#coverage`. One extra step, no new
scheduled task, no new battery-kill surface. A standalone daily task is the fallback.

**Two gotchas that will bite:**
- Write through `ticker_utils.write_universe_csv` only. A bare pandas round-trip float-ifies
  `CIK` / `Year Listed` and corrupts the published exports (`316e2f8`, and `commit_staged_candidates`
  is named in `CLAUDE.md` as the weekly vector for exactly this).
- **A commit must be followed by an exports republish** (`cli.py weekly-universe --skip-discovery`),
  or the seven downstream consumers won't see the new name until Friday.

### S4 — Aging / expiry (~half session)

**Confirmed: expire at 60 days pending → `declined`.** Report it in the weekly post: *"3 expired:
X, Y, Z — reply `revive X` to restore."* Without this, S2's ledger becomes the new immortal
backlog and we have rebuilt the problem in a better file format.

Expiry is a ledger status change only — it never touches the universe CSV, so it is safe to run
unattended.

**One-time on first run:** the 15 names already queued (SKHY, MBGL, BSP, DPC, RKLB, SNDK, EROC,
PBLS, FPS, MANE, MWH, KARD, CSQR, STDN, 2475.HK) predate the ledger and several are already past
60 days. Do **not** silently expire them on import — they were never actually put to JP. Import
them all as `pending` with their true `first_proposed` date and post one catch-up thread to
`#ipo-spinoffs-newissues` asking for a decision, with the clock starting from import.

---

## 3. Decisions — settled by JP 2026-07-28

1. **Expiry: expire.** A candidate pending 60 days becomes `declined`, revivable with
   `revive TICKER`. Silence is not approval.
2. **`Core` stays blank.** JP sets `Core` himself, separately. **Approval means "add to Coverage
   Manager"** — see §3a, which is the substantive half of this decision.
3. **No `positions_and_researching.csv` row by default.** Opt-in via `add TICKER following` →
   `Following for Interest`.

### 3a. What "approval" must actually do

JP: *"approval just means add to coverage manager, which means I want you to get all the
appropriate metadata to track the name — and at some point I may track it via Sigma Alerts."*

So an approved candidate is **not** an append of ticker + name. It must land as a fully
populated row across all 28 universe columns, then propagate. Every piece of this already
exists — chain the existing entry points, do not write new fetchers:

| Step | Call | Fills |
|---|---|---|
| 1 | `universe/enrich.py:enrich_single_ticker(ticker, sector_jp, exchange_hint)` | Exchange / Code / Full Name, Listing Type, Other Listings, Year Listed, ISIN, FIGI ×3, CIK, Country ×3, Currency, Website, YF Sector / Industry |
| 2 | `cli.py ipo-backfill --min-year <YYYY> [--limit N]` | **`IPO Date`, `Est Lockup 90d`, `Est Lockup 180d`** — verified offer date from Renaissance. Highest-value step for an IPO add: yfinance/FMP report first-trade, not offer, and the lockup dates are a real forward signal. **There is no `--tickers` flag** — targeting is by year, most-recently-listed first, which sweeps up other recent names missing an offer date (usually a bonus, but it spends quota). Watch the 115-calls/month cap. |
| 3 | `cli.py backfill-lei` (if an ISIN resolved) | `LEI` (GLEIF, ~46% hit rate) |
| 4 | `Sector (JP)` / `Subsector (JP)` / `Sub-subsector (JP)` | From the candidate record — the classification is the analyst judgment the weekly report already made. Must validate against `ALLOWED_SECTORS_JP`. |
| 5 | `Core` | **Left blank** (decision 2) |
| 6 | `ticker_utils.write_universe_csv` | never a bare pandas round-trip |
| 7 | `cli.py weekly-universe --skip-discovery` | republishes `exports/*` **and runs `sigma_export`** |

**Step 7 is what makes the name Sigma-trackable, and it is automatic.** `sigma_export` pushes
`ticker_metadata.json` into the sigma-alert clone, and sigma screens the full universe for 2σ
moves (Core/position lists only get the extra 1σ cut). So "add to coverage" already means
"appears in Sigma Alerts" — no separate opt-in, and no work beyond not skipping step 7.

**Enrichment failure must block the add, not half-write it.** A row with a blank CIK is invisible
to `insider_ownership` and `earnings_agent`; a wrong one is worse (see `cik_backfill`'s
name-similarity gate — it deliberately skips rather than guesses). If step 1 can't resolve the
identity, reply in-thread with what failed and leave the candidate `pending`. Do not append a
stub and call it added.

---

## 4. Sequencing

**S1 + S2 are worth shipping even if S3 never happens** — they convert an invisible backlog into
a visible, dated one, which is most of the current damage. S3 without S2 would function but
re-creates the drift it is meant to fix. S4 only matters once S2 exists.
