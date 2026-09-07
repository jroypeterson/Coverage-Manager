# The Bucket 5 question — a decision brief

**Status:** DECIDED 2026-09-06 — option **D + B**. Implemented; see the
bottom of this file. Raised by the 2026-09-04 weekly report.
**Decision owner:** JP. This is a rules call, not an engineering one; the code
change is small either way.

---

## What Bucket 5 is

From `weekly_coverage_prompt.md`:

> **Bucket 5: Russell index additions.** Flag companies entering the Russell 2000
> or Russell 1000 for the first time that are: market cap roughly $2B–$20B, not
> already in the CSV, **ANY sector — not limited to healthcare/tech.**

Since 2026-08-09, Bucket 5 **auto-adds**: a qualifying name enters the universe
without JP being asked. `universe/auto_add.py` documents why.

## The three things that are now wrong

### 1. The evidence that justified auto-adding Bucket 5 is falsified

`auto_add.py`'s docstring makes an explicit empirical argument:

> Measured across the whole candidate ledger: **31 candidates ever proposed, 29
> approved, 2 declined.** … Bucket 5 was 4-for-4 — and both declines (`DPC`
> Industrials $7.1B, `EROC` Energy $3.6B) fall in *neither* bucket, so the change
> would have produced **zero** wrong adds across the lane's entire history.

The 2026-09-04 report found that **`DPC` and `EROC` are both on this Russell
list, and both sit inside $2–20B.** The two names JP has ever declined would
now auto-add under Bucket 5. The premise "zero wrong adds" is false as of the
first Russell list after the rule changed.

### 2. `declined` is not a refusal

`auto_add.py` has three documented refusals: no market cap, a ticker on the
provenance-removals list, and already-in-universe. There is **no check for a
`declined` row in `candidate_ledger.csv`** — the string `declined` appears in
that module only inside the docstring sentence quoted above.

Consequence: a name JP explicitly declined re-enters the universe the moment any
vendor re-proposes it under a different bucket. Every auto-add is logged by `auto_add.plan()` and `sync_candidate_ledger`; what is not detected or logged is the **reversal** — that this add contradicts a prior decline.

### 3. "ANY sector" is now contradicting the lane's own written judgement

The 2026-09-04 run auto-added four Bucket 5 names. **Three** of them had been
**explicitly excluded on the merits, in writing, twice each, weeks earlier**:

| Ticker | Company | Sector | Cap | Prior written verdict |
|---|---|---|---|---|
| `LIME` | Neutron / Lime | Industrials | ~$2.5B | Excluded 2026-06-26 — "micromobility IPO, no in-sheet peers, low relevance"; **reaffirmed 07-03 after pricing** |
| `SSMR` | Sunshine Silver | Materials | ~$2.6B | Screened out 06-07 and 06-12 — "precious metals" |
| `LFTO` | Liftoff Mobile | Tech | ~$3.2B | *Recommended* 06-05/06-07/06-12 as a direct AppLovin comp, then lost in the pre-ledger drop |
| `AADX` | Applied Aerospace | Industrials | ~$2.2B | Screened out 06-07 — "defense — outside taxonomy"; again 06-12 |

So the system said **no** in June, **no again** in July, and **yes** in
September, on the same names, with nothing reconciling the two answers. The
report's own words: *"`SSMR` is a pre-revenue silver miner restarting a mine in
2028 — auto-added because Bucket 5 is sector-agnostic."*

Note that `LFTO` cuts the other way: the lane wanted it, and Bucket 5 is the
reason it finally arrived. Any fix that simply narrows Bucket 5 loses that.

## The options

**A. Leave it.** Bucket 5 is deliberately sector-agnostic because Russell
inclusion is itself the relevance test — an index add means institutional money
must now hold it, regardless of what the business does. Accept LIME/SSMR as the
cost of catching LFTO.

**B. Make `declined` a hard refusal, change nothing else.** Smallest change.
Fixes #1 and #2. Does not address #3 — LIME and SSMR were never `declined` rows,
they were prose exclusions in a report, so this would not have stopped either.

**C. B, plus a relevance gate on Bucket 5** — e.g. auto-add only where the sector
is core or core-adjacent, and queue everything else for a reply. Addresses all
three. Costs: Bucket 5 stops being mechanical, and the queue grows.

**D. B, plus demote Bucket 5 from auto-add back to queue entirely.** Every
Russell name becomes a one-line approval. Maximum control, most inbox.

**E. B, plus make a prior written exclusion machine-readable** so the lane can
refuse to auto-add a name it excluded on the merits within the last N weeks.
Addresses #3 at its root, but requires the "Considered and excluded" section —
today free prose — to become structured data.

## What is being asked

1. Which option?
2. If C: what is the sector predicate, given `auto_add.py` already argues that
   `Tech` is too broad a gate for Bucket 1 because "relevant to my universe" is a
   judgement a sector string cannot carry?
3. Should a prose exclusion in a prior report bind a later automatic add at all,
   or is a fresh Russell add legitimately new information that supersedes it?


---

## Decision, 2026-09-06 — option D + B

Reviewed by Codex (xhigh) against the repo; it corrected two claims above and
recommended **D including B**, explicitly rejecting C and E.

**What changed:**

- `auto_add.AUTO_BUCKETS` is now `{1, 2, 3}`. Bucket 5 is still *classified* —
  the queue reason names the rule — but no longer auto-adds.
- `auto_add.decide()` takes a `declined` set and refuses any ticker in it, with
  the decline named in the reason. `candidate_ledger.declined_tickers()` is the
  one definition; both `auto_add.plan()` call sites in
  `scripts/sync_candidate_ledger.py` pass it.
- The refusal **re-queues** rather than dropping. A decline is a judgement about
  a moment; a new trigger deserves to be seen, not silently written in.
- `weekly_coverage_prompt.md` updated: three auto buckets, not four.
- `test_the_new_rules_would_have_auto_added_nothing_JP_declined` retired — it
  asserted the falsified premise, so it was a passing test pinning a false claim
  about the world. Replaced by a guarantee that does not depend on any bucket's
  shape: a declined name never auto-adds, whatever the trigger or cap.

**Deliberately NOT done:**

- **No sector predicate on Bucket 5** (option C). Core-only would have missed
  both `LFTO` and `AADX`; `Tech` is too broad for the reason `auto_add`'s
  docstring already gives for Bucket 1; "core-adjacent" is the judgement the
  queue exists to collect.
- **No structured store of prior prose exclusions** (option E). Queueing
  dissolves the problem rather than parsing for it. A fresh Russell add is new
  information and should be reconsidered — but by a person, once.
- **The change is PROSPECTIVE.** `LFTO`, `AADX`, `LIME` and `SSMR` are already in
  the universe and stay there. Whether to remove `LIME` and `SSMR` — both
  screened out twice on the merits before Bucket 5 overrode that — is a separate
  decision that is still open and belongs to JP.
