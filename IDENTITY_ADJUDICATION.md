# Identity backlog — one adjudication table

Created 2026-07-29. **Purpose: replace the drip.** Nine rows were repaired one at
a time across two days, each requiring a separate sign-off, because each new audit
lens surfaced its own batch. This is the whole remaining queue in one place so it
can be decided in one sitting.

**State after the 2026-07-29 session** (universe 1,096 rows):

| Measure | Value |
|---|---:|
| ISIN → issuer conflicts | **4** (was 21) |
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

## A. Needs a JP decision (4 items)

### A1. `Listing Type` — ADR vs interlisted ordinary
**Status: DECIDED 2026-07-29 — add a separate `Instrument Type` column.** Not yet
built; it is `#250` on the board. This is the one that unblocks the ADR ISIN rule
(Rule A), which would otherwise reject 84 legitimate rows. Everything below in
this section is smaller.

### A2. The 4 remaining ISIN → issuer conflicts
None auto-applied; two independent sources could not settle any of them.

| Ticker | Row says | Stored ISIN resolves to | What would settle it |
|---|---|---|---|
| `2715.HK` | Estun Automation Co Ltd | ELKOP ESTONIA SE | Is this an H-share line at all? Estun is Shenzhen `002747`. If the row means the A-share, the ticker is wrong, not just the ISIN. |
| `CPH` | Cipher Pharmaceuticals Inc | CPH CHEMIE & PAPIER / CPH GROUP AG | Cipher's TSX ISIN. GLEIF gives US-prefixed lines (`US17253X2045/3035`) with no OpenFIGI coverage; the CA-prefixed equity line is unconfirmed. |
| `CBIO` | Crescent Biopharma Inc | GLYCOMIMETICS INC | `US38000Q2012` is confirmed by GLEIF (same `38000Q` CUSIP body — the GlycoMimetics→Crescent reverse merger) but has **no OpenFIGI coverage**, so it is one source, not two. |
| `MDLA` | Medela Potentia Tbk PT | MALARASEN AB | An Indonesian (`ID…`) ISIN. Neither source produced a candidate. |

**Recommendation:** `CBIO` is the one I would apply on one source — the CUSIP-body
lineage is strong direct evidence and the current value is provably another
company's. The other three I would leave and re-probe on the weekly.

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
