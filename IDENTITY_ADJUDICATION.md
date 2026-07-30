# Identity backlog — one adjudication table

Created 2026-07-29. **Purpose: replace the drip.** Nine rows were repaired one at
a time across two days, each requiring a separate sign-off, because each new audit
lens surfaced its own batch. This is the whole remaining queue in one place so it
can be decided in one sitting.

**State after the 2026-07-29 session** (universe 1,096 rows):

| Measure | Value |
|---|---:|
| ISIN → issuer conflicts | **0** (was 21) |
| ISIN → issuer inconclusive | ~30 |
| Venue-consistency warnings | **0** |
| Country-prefix warnings | **0** |
| ISINs failing the ISO 6166 check digit | **0** |
| Blank CIK | 236 |
| Blank ISIN | 304 |

Mechanisms now on the weekly cadence, so none of this can silently
re-accumulate: `delisted_check` [4/6], `cik_backfill` [4a/6],
`ticker_change_check` [4b/6], `crosscheck-foreign` [4c/6],
**`verify-isin-issuers` [4d/6]** (added 2026-07-29), plus
`validate_venue_consistency` inside `run_all_validations`.

---

## A. Needs a JP decision (3 open, 1 decided)

### A1. `Listing Type` — ADR vs interlisted ordinary
**Status: DECIDED 2026-07-29 — add a separate `Instrument Type` column.** Not yet
built; it is `#250` on the board. This is the one that unblocks the ADR ISIN rule
(Rule A), which would otherwise reject 84 legitimate rows. Everything below in
this section is smaller.

### A2. ISIN → issuer conflicts — **CLOSED 2026-07-30**

All 21 are resolved. The last three were settled by a research pass with web
search, which is what the registers alone could not do:

| Ticker | Resolution | Key source |
|---|---|---|
| `2715.HK` | `EE0000000453` → **`CNE100007F15`** | **HKEX's own List of Securities** — Estun completed an H-share IPO listing 2026-03-09 as code 2715, so the answer to "is there an HK line at all?" is yes. ⚠ The A-share line is `CNE100001X35`; do not let a future issuer-level check "correct" the HK row to it (the `EVO` instrument trap). |
| `CPH` | `CH0001624714` → **`CA17253X1050`**, **and the LEI too** | Cipher Pharmaceuticals (TSX: CPH, dermatology). The wrong ISIN and wrong LEI arrived as a **matched pair** both pointing at CPH Group AG, the Swiss namesake — the `7741.T`/`FAGR.BR` pattern. LEI → `213800T44AN9XSAOY605` (GLEIF: CIPHER PHARMACEUTICALS INC., verified live). |
| `MDLA` | `SE0008937411` → **`ID1000209901`** | PT Medela Potentia Tbk, Indonesian healthcare distributor, IDX IPO 2025-04-15. TradingView + KSEI (Indonesia's numbering agency). |

**The generalisable lesson, now three-for-three:** a held conflict is usually a
*corporate action or a listing nobody had looked up*, not a bad identifier.
`FGEN` (rename), `CBIO` (reverse split), and now `2715.HK` (a brand-new listing
that post-dated the cell). Search before escalating.

### ⚑ Two SCOPE questions this surfaced — genuinely JP's call

Neither is an identifier fact, and both come from the 2026-04-03 bulk import.

1. **`CPH` is a dermatology pharma carrying `Sector (JP) = MedTech`,
   `Subsector = Hearing Aid`, `Core = Y`.** Cochlear is already in the universe
   separately (`COH Au`, Core=Y, Hearing Aid), so the Hearing-Aid tag looks
   inherited rather than chosen. Was Cipher ever a deliberate pick? If yes, the
   sector should be Biopharma; if no, the row is a contaminated artifact.
   (`Exchange Code = YHD` is a Yahoo placeholder and junk either way.)
2. **`MDLA` carries `Sector (JP) = SaaS`** — which betrays its origin as
   **Medallia**, the US SaaS company that traded as NYSE `MDLA` until its Thoma
   Bravo take-private in Oct 2021. The exact `ZEN` shape. The Indonesian
   distributor that now owns the ticker plausibly *fits* an HC universe as
   Healthcare Services / distribution, but was almost certainly never a
   deliberate pick. Keep-and-reclassify, or quarantine?

### A3. `ALBT` / `FGEN` — name decisions, now resolved
Closed 2026-07-29 on JP's call: `FGEN`→`KYNB` (Kyntra Bio, same registrant),
`ALBT` removed as a scope change (now Change Agents Corporation, no longer
healthcare). Recorded here only so the class is visible: **when OpenFIGI and
GLEIF disagree about a NAME, the register is usually ahead of the row** — that is
a rename to confirm, not a conflict to escalate.

### A4. Re-keying the bare foreign tickers — **needs a decision, and it is not what it looked like**
Originally proposed as "the generator". The audit changed that:

- `normalize_ticker` **already** derives the correct suffixed yfinance symbol for
  all 22 bare foreign rows from `Exchange` — zero remain bare. So re-keying is
  **not** required to stop vendor contamination.
- `sigma-alert` has **already built** `to_metadata_key()`,
  `foreign_collision_bases()` and `disambiguate_collision_metadata()`
  specifically to work around CM stripping suffixes from
  `universe_metadata.json` keys. Publishing suffixed keys would **break its
  lookups**.

So the remaining cost of the current scheme is the **published-key collision**:
`universe_metadata.json["ROG"]` is **Roche**, while the `ROG` row is **Rogers
Corporation** (`Core=Y`) and `ROG.SW` has no key at all. One company is silently
absent from the published contract, by design, and the exporter has been printing
`normalization_collisions: 1` on every run.

**Recommendation:** treat this as a coordinated two-repo change with a schema
bump, not a CM-only edit — CM publishes raw-ticker keys, sigma-alert drops its
workaround. Worth doing, but it is a release, not a fix.

---

## B. Mechanical, no decision needed (queued)

- **236 blank CIKs.** `backfill-cik` fills 0 of them because it resolves by
  *current ticker* and these rows are mostly foreign (no SEC registrant) or
  renamed. The **name-based resolver** is the fix; it found 3 renames in one pass
  (`RENB`→`LNAI`, `THAR`→`CNTN`, `ZOM`→`ZOMDF`, all applied) and should become a
  CLI verb.
- **304 blank ISINs.** `backfill-foreign-ids` is the tool; coverage ceiling is the
  iShares⋈N-PORT fund overlap. Adding funds raises it (one-line change).
- **~30 inconclusive ISINs.** Mostly US micro-caps with no OpenFIGI mapping. Two
  are *prefix-implausible* and deserve suspicion despite the verdict:
  `BAVA.CO` (Bavarian Nordic) carrying `SGXZ32918005`, and `SAAS` (Microlise, LSE)
  carrying `AU0000297590`.
- **`DAY`** — fixed 2026-07-29. Its `Company Name` read "NASDAQ US Dividend
  Achievers 50 Index"; every other cell was already Dayforce's. Name → `Dayforce,
  Inc.`, CIK → 1725057. It was the only index-named row (a sweep found 6 more
  "Trust" hits, all legitimate REITs).

---

## C. The structural finding, recorded so it is not re-derived

Two independent reviews (Codex unsteered, Fable architectural) converged: the
one-off fixes were **one defect generator plus two aggravators**.

1. **The ticker string does three jobs** — CSV primary key, published join key,
   and *vendor query symbol*. A bare foreign symbol resolves to a US namesake.
   Precisely located: `_fetch_fmp_profile` gets the **raw** ticker while every
   yfinance call goes through `normalize_ticker`. FMP's payload then overwrites
   `Exchange` — which `normalize_ticker` keys off — so the corruption is
   **self-reinforcing** and all four columns end up agreeing. That is why it was
   invisible for four months. **Closed 2026-07-29** by the payload identity gate.
2. **The stock is frozen.** All identity cells arrived in the 2026-04-03 bulk
   import unverified, and every guard added since only protects *new writes into
   blank cells*. The guards protect the front door of a house furnished before
   the door existed — which is why each new lens finds a fresh batch of the same
   vintage. **Mitigated** by putting every audit on the weekly cadence.
3. **No provenance state.** Nothing records "verified on date X by whom", so
   *every* vendor-vs-row disagreement escalates to a human because there is no
   prior on which side is stale. Both directions occur: `FGEN`'s ISIN was right
   and its name stale; `MED`'s name was right and everything else wrong.
   **Open** — this is the provenance-ledger item, and it is what would shrink the
   sign-off queue to genuine judgment calls.
