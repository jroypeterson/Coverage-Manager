# Coverage candidate approval loop — plan

**Status:** scoped 2026-07-28, not started. Prerequisite (route the weekly post to `#ipo`)
shipped in `bf3840c`.

**The ask (JP, 2026-07-24):** new IPOs post to `#ipo`; a message asks whether to add the name
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

1. Post the weekly summary to `#ipo` via `chat.postMessage`; capture the returned `ts` and write
   it to `slack_thread_ts` on this week's ledger rows.
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
files:write`, and `conversations.replies` was verified live against the thread posted to `#ipo`
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

A candidate pending 60 days is a decline nobody made. Auto-expire to `declined`, and report it in
the weekly post: *"3 expired: X, Y, Z — reply `revive X` to restore."* Without this, S2's ledger
becomes the new immortal backlog and we have rebuilt the problem in a better file format.

---

## 3. Decisions needed from JP

1. **Expiry default** — expire to `declined` (revivable), or keep pending forever?
   *Recommend `declined`:* silence is not approval for adding coverage, and `revive` loses nothing.
2. **Does approval set `Core = Y`?**
   *Recommend no* — adding a row is tracking; `Core` is an analytic commitment (and 3 sibling
   projects gate their call budgets on it).
3. **Does an approved IPO also enter `positions_and_researching.csv`?**
   *Recommend no by default*, opt-in via `add TICKER following` → `Following for Interest`.

---

## 4. Sequencing

**S1 + S2 are worth shipping even if S3 never happens** — they convert an invisible backlog into
a visible, dated one, which is most of the current damage. S3 without S2 would function but
re-creates the drift it is meant to fix. S4 only matters once S2 exists.
