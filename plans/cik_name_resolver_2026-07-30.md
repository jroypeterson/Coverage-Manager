# Plan: resolve blank CIKs by company name (v3)

**Project:** Coverage Manager
**Status:** proposed, not built. **v3 = v1 + a Codex review + a Fable review.**
**Shape changed in v3: this is a DETECTOR, not a filler. It writes nothing.**
**Date:** 2026-07-30

## The problem

236 of 1,096 universe rows carry no CIK, so CIK-keyed consumers
(`insider_ownership` Form 3/4/5, `earnings_agent`) cannot see them.

Two mechanisms exist and neither fills these:

- **`backfill-cik`** (weekly `[4a/6]`) resolves a blank CIK by looking the row's
  **current ticker** up in SEC's bulk map. It already gates the write on a
  **fuzzy** name match against SEC's title and rejects namesake collisions
  (`cik_backfill._name_matches`, threshold 0.55) — so the gap is not "no name
  check", it is that a renamed row's old ticker is **absent from the map
  entirely**. Measured 2026-07-29: fills **0 of 236**.
- **`check-ticker-changes`** (weekly `[4b/6]`) detects a renamed ticker by
  comparing SEC's ticker for the row's **CIK**, so a blank-CIK row is invisible
  to it (`ticker_change_check.py:215`).

They fail as a pair, circularly: *a row whose ticker changed AND whose CIK is
blank is unreachable by both.* `FGEN` was the proof.

**Corrected from v1:** v1 claimed the reporting calendar is affected. It is not —
`reporting_calendar.fetch_cik_map()` builds its own `{TICKER: cik}` from SEC and
never reads the CSV `CIK` column. Blank CSV CIKs hurt CIK-keyed *consumers*, not
that export.

## The escape hatch, already demonstrated

Resolving by **company name** found three renames the CIK-keyed detector
structurally cannot see (`RENB`→`LNAI`, `THAR`→`CNTN`, `ZOM`→`ZOMDF`), all since
applied. This plan makes that throwaway script a real verb.

## The verb

```
python cli.py resolve-cik-by-name [--limit N] [--tickers ...]
```

**Report-only. There is no write path** — see "it is a detector" below. Report:
`reports/cik_name_resolution_<date>.md`.

## Verdicts

v1 had a single `renamed` verdict. **That was wrong and would have produced false
findings**, demonstrated against live data: an exact-name lookup matches a
foreign primary listing to the same issuer's US ADR, which is a *different line*,
not a rename. Six live rows would have been mislabelled — `4507.T` Shionogi
(SEC: `SGIOF`), `CSL` (`CSLLY`), `FRE` Fresenius (`FSNUY`), `CUV.AX`, `TLX.AX`,
`NGEN.V`. `check-ticker-changes` already carries a guard for precisely this
class (`ticker_change_check.py:34,246`); this verb needs the same idea, keyed on
`Country (Listing)` / `Exchange` rather than ticker shape alone.

| verdict | condition | action |
|---|---|---|
| `stale_us_listing` | unique exact name match, row is **US-listed**, SEC ticker differs | report — the valuable class (the next `FGEN`) |
| `sec_registered_other_line` | unique match, row is a **non-US primary listing**, SEC ticker differs | report at LOW severity; explicitly **not** a rename |
| `ledger_conflict` | the blank is recorded in the provenance ledger | route through `provenance.triage()`; a re-review, not a fresh finding |
| `ambiguous_name` | more than one **distinct CIK** normalizes to the same name | report, never write |
| `short_name_suppressed` | normalized name < 4 chars | report, never write |
| `no_match` | nothing matched | report; expected for ~200 non-registrant foreign rows |

## It is a detector, not a filler (the write path is cut)

The Fable review argued `resolved_same_ticker` is an empty class and `--apply` is
dead weight. That is right, and checking it produced a sharper reason than either
review had.

Of the 236 blank-CIK rows, **3** have their ticker in SEC's map: `CSL`, `MED`,
`MOVE` — the exact three whose blank is **ledger-verified**, because the SEC
entity behind that ticker is a *different company* (790051 Carlisle, 910329
Medifast, 1734750 Corvex). `backfill-cik`'s fuzzy gate correctly refused them;
that refusal is *why* they are blank. The other 233 are blank because their
ticker is absent from the map entirely.

So a name match on a blank-CIK row can only ever resolve to a **different**
ticker. There is nothing for `--apply` to write that step `[4a/6]` has not
already written. Cutting it also removes v2's sequencing rationale — with no
in-run fill, this verb's position in the `4x` block barely matters.

*(Fable stated the reason as "none of the blank tickers appear in the map at
all". Three do. The conclusion holds; the mechanism is different and worth
recording, because those three are exactly the rows a naive resolver could
damage.)*

**Consequence: no ledger writes either.** The ledger's contract is *which cells a
human verified*, at a bar of two independent sources (`provenance.py:143-148`);
an automated fill has one source and would need boilerplate reasons, diluting the
discipline the ledger exists to enforce. `backfill-cik`, the existing automated
filler, correctly writes none. This verb writes none.

## Reuse `provenance.triage()`

`ledger_conflict` is a thin wrapper over `universe/provenance.py:triage()`, not
parallel logic. That function already returns `row-verified` / `row-superseded` /
`row-held` / `row-unverified`, and `row-superseded` is literally this scenario: a
resolver offering `790051` for `CSL` is re-offering a value already rejected.

Precision fix to v2's prose: `CSL` is caught by `short_name_suppressed` first
(`norm("CSL Limited")` = `"csl"`, 3 chars), before the ledger layer fires. `MED`
(`"medartis"`) and `MOVE` (`"medacta"`) are **not** short-suppressed and land in
`no_match` under exact matching, since Medartis ≠ Medifast. The ledger layer is
therefore defence-in-depth, not the thing that saves `CSL`.

## Guards

- **Exact match after normalization** — no similarity threshold. A wrong CIK
  silently pulls another company's filings; a blank one is visibly missing.
- **One normalizer, committed to:** `ticker_utils.normalize_company_for_comparison`.
  Known misses to document alongside `SpaceX`: it does not strip `THE`, so
  "The X Group"-style SEC titles become false `no_match`.
- **Ambiguity is counted over distinct CIKs, not SEC map entries.** Share classes
  give one CIK several tickers under the same title; counting entries would
  false-ambiguate them.
- Never touch a populated CIK.

## Live yield, simulated against real data

**10 unique hits, 0 ambiguous, 3 short-suppressed** — every hit a
different-ticker finding.

**`stale_us_listing` (5)** — the valuable class:
`ADAP`→`ADAPY`, `APTO`→`APTOF`, `CASI`→`CASIF`, `LIAN`→`LIANY`, and
**`CYBN`→`HELP`**.

`CYBN` was verified independently against EDGAR: **CYBIN INC., CIK 1833141, now
trading as `HELP` on Nasdaq, still filing (6-K 2026-07-21)**. A live, uncaught
ticker change sitting in the universe today — the next `FGEN`, invisible to both
existing detectors. It alone justifies the verb.

**`sec_registered_other_line` (5)**: `4507.T`, `FRE`, `CUV.AX`, `TLX.AX`,
`NGEN.V` — confirming the verdict split; a single `renamed` verdict would have
mislabelled half the output.

**`sec_registered_other_line` is not "never actionable."** After human review,
filling Shionogi's or Telix's CIK on the `.T`/`.AX` row would be correct — the
repo already treats CIK as an *entity*-level identifier that legitimately lives
on a foreign-primary row (`UCB`, a Belgian issuer, carries CIK 1290640 by
deliberate ledger entry), and `ticker_change_check` skips non-plain-US symbols so
no spurious flags follow.

## Integration

- Weekly: **report-only**, in the `4x` block. Position is not load-bearing now
  that nothing is filled in-run.
- **No `SystemExit` in the orchestrator path** — `run_step` catches ordinary
  exceptions and weekly status is string-based. The CLI may exit `2`; the weekly
  step returns a `failed: review needed ...` status string so the heartbeat reads
  `partial` while the run continues.
- Add `cik_name_resolution_*.{md,csv}` to `UNIVERSE_ARCHIVE_PATTERNS`.

## Open questions — resolved

1. **Louder US-HQ `no_match`?** Mild yes: its own low-severity section, no alarm.
   The 18 US-HQ blanks are mostly OTC shells (`AMPE`, `CALA`, `EFTR`, `TLIS`)
   where `no_match` likely means *deregistered* — which is `delisted_check`'s
   territory, so this should point there rather than duplicate it.
2. **Audit populated CIKs too?** Defer. `check-ticker-changes` already validates
   those weekly and lanes `[4c]`/`[4d]` patrol the stored-identifier stock.
3. **Exact too strict?** Keep exact. The simulation finds plenty as-is, and the
   documented false negatives are the right trade for a repo whose scar tissue
   (`MED`, `CSL`, `ZEN`) is entirely wrong-identifier, never missing-identifier.

## Yield, corrected

**18** US-HQ blank-CIK rows remain, not v1's 23 — the hand fixes already landed
(`LNAI`, `CNTN`, `ZOMDF`, `KYNB` all carry CIKs). ~200 of the 236 blanks are
foreign non-registrants that will sit in `no_match` permanently. The row yield is
small; the value is concentrated in `stale_us_listing`.
