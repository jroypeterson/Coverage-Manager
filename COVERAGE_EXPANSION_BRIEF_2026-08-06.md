# Coverage expansion brief — spin-off discovery, exhaustive biopharma, Core Biopharma

**Date:** 2026-08-06 · **Author:** Opus 5 session · **For:** Fable 5 design review
**Repo:** `Coverage Manager` (the fleet's most-depended-on repo; ~20 sibling projects
read its `exports/`)

Every number below was measured live today, not recalled. Where something is unverified
it says so.

---

## 0. Context you need

Coverage Manager owns `data/coverage_universe_tickers.csv` — **1,093 rows**, the fleet's
canonical ticker universe. A weekly headless job (`WeeklyCoverageBuilder`, Fri 08:00)
runs a discovery lane that proposes new names, writes them to `data/candidate_ledger.csv`
as `pending`, and posts to Slack `#ipo-spinoffs-newissues`. JP approves by replying
`add TICKER`; a poller (shipped yesterday) applies it, enriches the row, and republishes
`exports/`.

Two columns matter for this brief:

- **`Sector (JP)`** — JP's own taxonomy. `Biopharma` = **687 rows**.
- **`Core`** — "names JP covers analytically." Read by three siblings
  (`forensic_triage` call-budget gate, `analyst-days/src/universe.py:load_core_watchlist`,
  `earnings_agent/coverage.py`), all of which filter `Core == "Y"`.

Measured distribution of `Core=Y` today:

| Sector (JP) | rows | Core=Y |
|---|---:|---:|
| Biopharma | 687 | **0** |
| MedTech | 139 | 136 |
| Healthcare Services | 103 | 82 |
| Tech | 63 | 14 |
| SaaS | 52 | 0 |
| everything else | 49 | 24 |

**`Core` is currently a flag that means "not biopharma."** That is the latent defect
behind JP's request, and it has been known but unaddressed.

---

## 1. Spin-off discovery — the proposal

### The finding

CM's inclusion rules cover spin-offs **twice**: Bucket 1 (core-sector spin-offs, *any*
size) and Bucket 3 (spin-offs / carve-outs / separations **> $10B**, any sector).
`Spin-off` and `Carve-out` are first-class values in the discovery trigger enum.

But every **discovery source** is IPO-shaped:

| Source | Finds spin-offs? |
|---|---|
| Finnhub IPO calendar ("primary for IPO discovery") | **No** — a spin-off has no offering |
| Gmail IPO-summary emails | No |
| Web search | Prompt uses it to *validate* "whether a company is truly newly listed or a spin-off" — not to find one |
| Russell reconstitution | Only after the fact |

Result, measured:

- Of **18** ledger candidates ever proposed: 9 IPO, 4 Russell, 3 other triggers,
  **1 spin-off** (Mobility Global / MBGL, CARFAX+Polk out of SPGI).
- MBGL filed its **Form 10-12B on 2026-05-07**; it was recommended **2026-07-10** —
  two months later, at listing. The forward-looking window the prompt calls "the
  highest-value part of the post" was missed.

### The available source

SEC **Form 10-12B** is the registration a US spin-off files to distribute shares onto an
exchange — typically 1–3 months before separation. Verified live today via EDGAR
full-text search (`efts.sec.gov`, `EDGAR_IDENTITY` already set in `.env`, CM already uses
EDGAR heavily):

- **348 Form 10-12B filings 2026-01-01 → 2026-08-06** (includes `/A` amendments; the
  list skews micro-cap and needs filtering — this is *not* 348 spin-offs).
- In that list, never mentioned in any CM report or the ledger:
  - **Honeywell Aerospace (HONA)** — filed 2026-05-14 and 2026-06-08. The aerospace leg
    of Honeywell's three-way split. Comfortably a **Bucket 3 mandatory** by size, and it
    has been invisible for three months. (Honeywell appears in CM reports exactly 3
    times, all as a *customer* in the Doncasters briefing.)
  - **ADI Global Distribution (ADIG)** — Resideo's distribution carve-out, filings
    2026-05-11 → 07-01. Size unverified; may fall under Bucket 3's $10B bar.
  - MBGL's own Form 10 — present, but the lane didn't read this feed.

Also available and untested: `browse-edgar?action=getcurrent&type=10-12B&output=atom`
returns very recent filings only (1 result today) — a weekly poll would work, but the
full-text search endpoint gives a proper date range.

### What I propose (for Fable to critique)

Add a **Form 10 discovery step** to the weekly lane, alongside the Finnhub IPO calendar:

1. Query EDGAR full-text search for `forms=10-12B` over the last ~10 days.
2. Drop `/A` amendments already seen (dedupe by CIK).
3. Resolve the **parent** — a Form 10 names the parent in the information statement, and
   the registrant CIK is new. Parent identification is the hard part; unresolved.
4. Route: parent in a Bucket-1 core sector → propose at any size. Registrant expected
   >$10B → propose under Bucket 3. Everything else → log, don't propose.
5. Surface in the report's **"Pipeline / filings to monitor"** section, which is the
   forward-looking section that already exists.

**Open questions for Fable:** How to resolve parent → child reliably without an LLM
call per filing? Is market cap even knowable pre-separation (no shares trade yet — the
information statement gives a distribution ratio, not a cap)? Is `10-12G` (OTC) worth
including? Should non-US spin-offs be attempted at all, given there is no equivalent feed?

---

## 2. Exhaustive biopharma — JP's new requirement

> "I want to capture all biopharma stocks from now on. I want coverage manager to have an
> exhaustive list of biopharma."

### Measured gap

Via the **FMP company-screener** endpoint, verified working on CM's paid **Starter** tier
today (`/stable/company-screener`, `isEtf=false&isFund=false&isActivelyTrading=true`):

| FMP industry | US-listed (NASDAQ/NYSE/AMEX) |
|---|---:|
| Biotechnology | 621 |
| Drug Manufacturers — Specialty & Generic | 61 |
| Drug Manufacturers — General | 20 |
| Medical — Pharmaceuticals | 18 |
| **Union** | **720** |

Against the current universe:

- **475** of those 720 are already in CM
- **245 are not** ← the exhaustiveness gap
- CM's 687 `Biopharma` rows include ~189 without a CIK (foreign lines), so CM's US
  biopharma is ~498 — consistent with the above.

**Caveat I could not resolve:** whether FMP's screener silently caps a result set
(Biotechnology returned exactly 621 at `limit=5000`, which looks real but I did not
paginate to confirm). Also unverified: how much of the 245 is genuine coverage versus
shells, sub-$50M microcaps, and recent reverse-split husks.

### Alternative / corroborating enumeration sources (all verified reachable today)

| Source | What it gives | Constraint |
|---|---|---|
| **FMP screener** (paid, already in use) | 720 US biopharma with sector/industry/mktcap | vendor taxonomy; industry boundaries are FMP's, not JP's |
| **SEC by SIC code** — 2834 Pharmaceutical Preparations, 2836 Biological Products, 8731 Commercial Physical & Biological Research | Authoritative for *US filers*; 100 rows/page, paginates | SIC is self-declared and stale; 8731 also catches CROs and non-pharma research |
| **Robinhood scanner** (`FILTER_TYPE_SECTOR`, predicates `=` / `ANY_OF`) | Live market screen | Only `Sector`, no industry — "Healthcare" lumps MedTech + services + biopharma. Universe limited to RH-tradable (excludes most foreign lines and OTC) |
| **IBKR `search_contracts`** | Contract/instrument resolution incl. foreign listings | Search-by-query, not enumerate-by-sector |

### Design questions for Fable

1. **Where is the boundary?** Does "all biopharma" include: pre-revenue shells;
   sub-$50M microcaps; OTC/pink; CROs and CDMOs (SIC 8731); diagnostics; animal health;
   cannabis-pharma; non-US listings (CM's biopharma is already ~27% foreign)? JP's
   framework is HC-focused with a *macro* edge, and the existing standing rule is
   "biotech only with an unusually strong reason" — which this request **overrides for
   universe membership** but arguably not for *attention*.
2. **What is the source of truth**, and what reconciles the vendor taxonomy to
   `Sector (JP)`? FMP says "Biotechnology"; JP's taxonomy has Biopharma → {Biotech,
   Specialty & Generic Pharma, Large Pharma}. Who maps, and is it re-derived weekly or
   frozen at add time?
3. **Blast radius.** Going 687 → ~930 biopharma rows takes the universe ~1,093 → ~1,340
   (+22%). That lands on: weekly fundamentals fetch runtime (currently ~17 min for the
   performance backstop), the AlphaVantage/Finnhub/FMP call budgets, `history-backfill`
   (3 FMP calls per uncached ticker → ~735 extra calls), `forensic_triage`'s daily
   call-budget gate, `transcripts`' fetch universe, and every `exports/` consumer.
   **Is exhaustive membership worth that, or should the exhaustive list be a separate
   artifact that does not enter the enriched universe?** This is the question I most
   want Fable's view on.
4. **Churn.** Biotech has heavy delisting/reverse-merger turnover. An exhaustive list is
   a *maintenance* commitment, not a one-time load. CM's `delisted_check` already runs
   weekly but is rate-limited by Yahoo (a cold pass on 1,093 names already trips
   backoff); +245 names makes that worse.

---

## 3. Core Biopharma — a second attention list

> "I want to have a core biopharma list that is separate from my normal core coverage.
> Core biopharma will have large cap like LLY, NVO, Vertex but then key companies like
> APMD that are interesting for specific reasons. Also PBLS."

Verified status of the named seeds:

| Ticker | In universe? | `Core` | Note |
|---|---|---|---|
| LLY | yes | blank | Biopharma / Large Pharma |
| NVO | yes | blank | Biopharma / Large Pharma |
| VRTX | yes | blank | Biopharma / Biotech |
| PBLS | yes | blank | Parabilis Medicines — approved from the ledger 2026-07-28 |
| **APMD** | **no** | — | Apnimed; still `pending` in the ledger, awaiting JP |

### Design questions for Fable

1. **Schema.** Options: (a) a new `Core Biopharma` column alongside `Core`; (b) widen
   `Core` to a multi-valued tag; (c) a separate file like `positions_and_researching.csv`.
   Constraint: **three siblings hard-filter `Core == "Y"`**, so any change to `Core`'s
   semantics is a cross-repo breaking change; a new *additive* column is not (precedent:
   `LEI`, `IPO Date`, `Instrument Type` all shipped additive with no schema bump).
2. **Is it one list or two axes?** LLY/NVO/VRTX are in for *size and franchise*; APMD is
   in because it threatens two `Core=Y` MedTech names (INSP, RMD); PBLS is in on JP's
   interest. Those are three different reasons. Does the schema need a *reason*, so the
   list can be audited later and so a name can be retired when its reason expires? CM's
   whole provenance ledger design says yes — but it is also how a simple flag becomes a
   project.
3. **Does it need its own export?** MedTech/HC Services get `Core=Y` and flow to
   `forensic_triage`/`earnings_agent`/`analyst-days`. If Core Biopharma is to drive the
   same lanes, it needs to reach `exports/universe_metadata.json` — that IS a schema
   change (additive field, consumers pinned to `{3,4}`).
4. **Seeding.** How is the initial list built — JP curates by hand, or is there a
   defensible mechanical starting set (e.g. all Large Pharma + Biopharma > $10B + every
   name with an approved drug + anything with a competitive read-through to an existing
   `Core=Y` name)? Note `quality_companies` is a prior project that shipped **empty
   because it needed JP's curation and never got it** — a real precedent for this
   failing the same way.

---

## 4. Renaissance IPO — can it check our work?

JP asked whether the Renaissance IPO emails or API can verify the lane.

**Measured constraints** (`providers/renaissance_ipo.py`, and CM's docs):

- Endpoint is `api.renaissancecapital.com/free/CompanyIpoDate`, keyed by
  **`TickerSymbol` or `CIK`** — it answers *"when did THIS company IPO?"*
- **It cannot enumerate.** There is no "list IPOs in a date range" call on the free tier.
- **Hard quota: 120 calls/month** (CM self-caps at 115). Spend ledger shows 38 in
  2026-06, 23 in 2026-07.
- Results are cached ~forever because an IPO date is immutable; a 404 is a real answer
  ("no IPO on record") and is also cached.

So Renaissance is a **verifier, not a discovery source**. It can confirm an offer date
for a name the lane already found — which is exactly how CM uses it (`cli.py ipo-backfill`).
It cannot answer "what did we miss?"

**Question for Fable:** is there a Renaissance *email/newsletter* product (JP receives
some IPO summary email — the prompt already searches Gmail for `subject:"IPO Summary"`)
that enumerates weekly pricings, and would that be a better completeness check than the
API? Worth a web search. Also worth checking: does Renaissance publish a free weekly
recap page that could be scraped for a completeness cross-check against Finnhub?

---

## 5. Robinhood / IBKR — JP has both, are they useful here?

Neither is currently used by the discovery lane. Verified capabilities:

- **Robinhood MCP** (headless-capable, account ••••0504): `get_scanner_filter_specs`
  confirms a **`FILTER_TYPE_SECTOR`** filter with `=`/`ANY_OF`, plus market cap, float,
  shares outstanding, earnings date, and a full technical suite. `create_scan` /
  `run_scan` execute live. **Limits:** sector only (no industry), and the universe is
  RH-tradable instruments — so foreign lines and most OTC are absent. Creating a scan is
  a *write* to JP's account, so I did not test one.
- **IBKR MCP**: `search_contracts` (query → contract, incl. foreign listings),
  `get_company_themes`, `get_company_connections`, `search_investment_topics`,
  `get_price_snapshot`. Note the fleet already records **IBKR Lite blocks the API**
  (Pro-only) for the *native* API; this MCP connector is a separate, interactive path.

**My prior** (low confidence — Fable should push back): neither is a good *enumeration*
source, because both are broker-tradability-scoped rather than market-complete. But
`get_company_connections` / `get_company_themes` may be a genuinely novel input for the
"peers already in the sheet" column and for Bucket 4 strategic adjacency, which is
currently the most judgment-heavy and least mechanical bucket. **Is that worth building,
or is it a toy?**

---

## 6. What I want from this review

1. **Kill or confirm** the Form 10 spin-off proposal, and solve parent-resolution.
2. A recommendation on **§2 Q3** — exhaustive biopharma *in* the enriched universe vs. a
   separate lighter-weight artifact. This is the highest-consequence decision here.
3. A concrete **schema** for Core Biopharma that does not break the three `Core == "Y"`
   consumers, and a view on whether it carries a reason field.
4. A **seeding strategy** for Core Biopharma that does not repeat `quality_companies`
   (shipped empty, waiting on curation that never came).
5. Whether Renaissance has an enumerable product we are missing (**web search**).
6. A verdict on Robinhood/IBKR — useful, or a distraction.
7. **Anything I have not thought of.** Particularly: is there a better boundary for
   "biopharma" than a vendor industry string, and is there a completeness *check* — some
   independent list we could reconcile against — rather than just a bigger ingest?
