# Plan: resolve blank CIKs by company name (v2)

**Project:** Coverage Manager
**Status:** proposed, not built. **v2 incorporates a Codex review of v1.**
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

## Verdicts (revised)

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
| `resolved_same_ticker` | unique exact name match, SEC ticker **equals** the row's | fill CIK under `--apply` |
| `stale_us_listing` | unique match, row is **US-listed**, SEC ticker differs | report — the valuable class (the next `FGEN`) |
| `sec_registered_other_line` | unique match, row is a **non-US primary listing**, SEC ticker differs | report at LOW severity; explicitly **not** a rename |
| `ledger_verified_blank_conflict` | the blank is **verified** in the provenance ledger | report as a re-review, not a fresh finding |
| `ambiguous_name` | >1 SEC entity normalizes to the same name | report, never write |
| `short_name_suppressed` | normalized name < 4 chars | report, never write |
| `no_match` | nothing matched | report; expected for ~200 non-registrant foreign rows |

## Read the ledger BEFORE reporting

**New in v2, and the correction that matters most.** `data/identity_provenance.json`
already records a **blank CIK as a verified value** for `MED`, `MOVE` and `CSL` —
Medartis and Medacta have no EDGAR record at all, and `CSL`'s CIK was blanked
because 790051 is Carlisle Companies'. A resolver that only *writes* provenance
after applying would re-raise those three every single week, and `CSL` would land
in the mislabelled-rename class on top.

So the verb loads the ledger first and routes any finding against a
ledger-verified cell into `ledger_verified_blank_conflict`. Applied CIKs are
written back as ledger entries conforming to the schema (`field`, `value`, ISO
`verified`, `sources`, plus `single_source_reason` when only one source exists).

## Guards

- **Exact match after normalization** — no similarity threshold in v1 of the verb.
  A wrong CIK silently pulls another company's filings; a blank one is visibly
  missing. Use a **dedicated exact SEC-title key**, or reuse
  `isin_identity`'s token normalizer as an exact tuple. (v1 claimed this "mirrors"
  `normalize_company_for_comparison` — it does not; that helper does not strip
  `THE` and is much simpler.)
- Never overwrite a populated CIK. Blanks only.
- A short normalized name is not evidence.

## Integration

- Weekly: **report-only**, sequenced after `backfill-cik` and before
  `check-ticker-changes`, so a CIK filled this run is visible to the rename
  detector in the same run.
- **No `SystemExit` in the orchestrator path** — `run_step` catches ordinary
  exceptions and weekly status is string-based. The CLI may exit `2`; the weekly
  step returns a `failed: review needed ...` status string so the heartbeat reads
  `partial` while the run continues.
- Add `cik_name_resolution_*.{md,csv}` to `UNIVERSE_ARCHIVE_PATTERNS`.

## Expected yield

Corrected from v1: **18** US-HQ blank-CIK rows remain, not 23 — the hand fixes
already landed (`LNAI`, `CNTN`, `ZOMDF`, `KYNB` all carry CIKs now). ~200 of the
236 blanks are foreign non-registrants that will sit in `no_match` permanently.
So v1's row yield is **small**, and the value is concentrated in
`stale_us_listing` — catching the next `FGEN` before a human has to notice it.

## Open questions

1. Should `no_match` on a **US-HQ** row be louder? A US company with no SEC
   entity is odd and may indicate a wrong `Country (HQ)`.
2. Should the verb also audit rows that already **have** a CIK (does the stored
   CIK's SEC title still match the row name)? Different defect, same machinery.
3. Is exact-after-normalization too strict? It will miss `SpaceX` vs
   `Space Exploration Technologies Corp`, which `cik_backfill` already documents
   as a deliberate false negative.
