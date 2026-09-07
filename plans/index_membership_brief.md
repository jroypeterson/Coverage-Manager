# Index membership — project brief

**Filed 2026-09-06. PINNED, board row #354.** JP: *"let's file this as a project
that I will resume tomorrow so make it a high priority project."*
**Resume here.** The question doc is `plans/index_membership_question.md`; this
is the answer and the plan.

---

## In one line

The S&P 500 and Russell lists **already exist and already work**. They live in a
per-project cache that four consumers read as if it were a contract, and nothing
accumulates history. The work is **promotion, not collection**.

## What exists today (verified on disk 2026-09-06)

| Index | Where | State |
|---|---|---|
| S&P 500 | `Coverage Manager/providers/wikipedia_provider.py` → `cache/constituents/sp500.json` (503 names + GICS sector/sub-industry, cached 08-28); second, stale copy at `sigma-alert/sources/sp500.txt` (507 lines, still carries the pre-rename `FISV`) | Two divergent sources |
| Russell 1000/2000/3000 | `sector_chart_pack/russell.py` — Vanguard VONE/VTWO/VTHR holdings JSON. 1,024 / 1,986 / 2,966 as of 07-31 | Works weekly; **gitignored cache, overwritten — no history accumulating** |
| US total market | `Coverage Manager/universe/crsp_snapshot.py` — 3,477 names | Quarterly, **archived since 2026-03-31**. The pattern to copy |
| MSCI ACWI ex-US IMI / EM IMI | `cache/foreign_ids/holdings_244048.csv` (IXUS, ~4,200) + `holdings_244050.csv` (IEMG), refreshed 09-02 by `foreign_identifiers.py` | Nobody thinks of these as index lists, but IXUS *is* ACWI ex-USA IMI |
| US exchange directory | `universe/symbol_directory.py` — ~7,499 operating companies | Weekly since 08-06 |

`sector_chart_pack/sp500_valuation.build_universe()` already returns the exact
object the ask describes: `{ticker: {name, sector, idx: ["sp500","r1000",…]}}`
over S&P 500 ∪ Russell 3000. **The "S&P 500 vs broader universe" screen is
already running.**

### Consumers of the cache today (four)

`sector_chart_pack` (FCF-yield + justified-P/E screens), `post_earnings_movers`,
`forensic_triage`, and `screens_equity/surprise_screens/universes.py` (named
`core` and `sp500` runs).

## The decision

**A reference-data lane inside Coverage Manager, published separately from the
coverage universe.** Not rows in `coverage_universe_tickers.csv`; not a new repo.
Two independent reviews (Fable 5.1, Codex xhigh) reached this separately.

### Why not rows in the universe

`screens_equity/surprise_screens/universes.py` already made this mistake and
documented it — this is the strongest evidence in the file:

> *"Sector comes from the VENDOR for every name, deliberately — not from Coverage
> Manager for the 112 covered ones and the vendor for the rest. Mixing the two
> taxonomies was the first version and it was wrong: it produced `Tech` (JP, 469
> observations) beside `Technology` (vendor, 1,329), `Financials` beside
> `Financial Services`… The same economic sector ended up with two different bars."*

Plus: the CSV is a decision record (every row has a `[JP]` verdict behind it);
`Sector (JP)` is whitelisted in `config.ALLOWED_SECTORS_JP` and Bucket 1 auto-add
gates on it; and ~13 sibling projects import `exports/` by path. Per-row jobs
(`enrich`, `delisted_check`, `reporting_calendar`, the ~17-min `cli.py
performance`) are tuned to 1,352 rows on a weekly request budget.

### Why not a new project

Another Task Scheduler entry (ten already), another heartbeat, another
`DEPENDENCIES.md` register, another brief to drift. CM already owns "snapshot an
external list weekly, diff it, reconcile it" three times over.

## Licensing — SETTLED 2026-09-06

JP: *"can we just not expose the index stuff publicly. we are getting index
membership from public sources so I kind of assume it shouldn't be an issue."*

**Correct, and it is already the house rule.** The Coverage-Manager remote is
**PUBLIC** (verified: `gh repo view` → `"visibility":"PUBLIC"`), and `.gitignore`
already carries `data/crsp/` and `data/estimates_history/` with the reasoning
written out: *"kept on disk, never pushed… the archive is the point, it just must
not be republished."* Index membership is a third instance of a rule that exists.

- **Redistribution** — the real concern. Solved by gitignoring. Never in `exports/`.
- **Automated access terms** — not changed by keeping data private, but the fleet
  already fetches IXUS/IEMG weekly, fund holdings are the fund's own SEC-mandated
  disclosure, and the SEC N-PORT fallback is the clean public-record alternative.
  Practical risk accepted; the realistic failure is the endpoint changing, which
  `russell.py` already handles (falls back to last good, reports age,
  `STALE_DAYS = 120` past which the cache is "reported as unfit, not silently used").

## Historical testing — the honest answer

**Prospectively yes, retroactively no, not for free.** FTSE sells Russell
history; MSCI publishes nothing free. Do NOT reconstruct 2019 membership from
current lists — that is survivorship bias by construction.

Two things soften it: **CRSP is already archived** (2026-03-31 and 2026-06-30 on
disk), and **S&P 500 history is partially recoverable from `sigma-alert` git
history** — 6 commits touch `sources/sp500.txt`, including monthly refreshes,
back to ~April 2026.

The stated use case — screening S&P 500 vs the broader universe — is
cross-sectional on today's members and **does not need history**. The one thing
that makes history exist later is snapshotting now.

## First step

1. Move `sector_chart_pack/russell.py` into CM as `universe/index_membership.py`;
   have it also read the existing `sp500.json`.
2. Weekly step writing dated snapshots to `data/index_membership/<index>_<asof>.json`
   (**gitignored**, like `data/crsp/`) plus one stable local `latest.json` in the
   `build_universe` shape — `{ticker: {name, gics_sector, idx: [...]}}`, with
   `as_of`, `source` and license metadata per index, and a 400–600 count gate.
3. Failure behaviour: last-good + visibly stale, never an empty list.
4. Migrate the four consumers through one schema-pinned adapter. Retire
   `sigma-alert/sources/sp500.txt` after one compatibility cycle.
5. Add a reconciliation line to the Friday report: "N universe rows in S&P 500 /
   R1000 / R2000; entered/left since last week."

About a day, mostly moving code. History starts the day it ships.

## Explicitly declined

1. Index members as rows in the coverage universe.
2. A separate repo or scheduler.
3. New S&P/Russell collectors duplicating fleet code.
4. Reconstructing pre-2026 membership as a feed (study input at most).
5. **ACWI / World for now** — and note this is a *data-quality* objection, not a
   licensing one that privacy fixes: iShares ACWI and URTH are **sampled fund
   portfolios**, not exact index membership. IXUS + CRSP already answer "what are
   the companies." Revisit only when a named consumer needs the flag.
6. Publishing any constituent list into committed `exports/`.

## Open, needs JP

- If "S&P Aqui" meant the **S&P Global BMI** rather than MSCI ACWI, sourcing
  reopens and the answer becomes "don't" — no broad ETF replicates it cheaply.
- Whether `universe_metadata.json` should later publish an `idx` flag for the
  1,350 covered names. Smaller than a full constituent list, but still lands
  membership data in a public repo — a deliberate call, out of step one.

## Live defect found while scoping (unrelated to the build)

A renamed GICS key can silently collapse every company into `Unclassified`.
Worth a guard whoever touches this next.
