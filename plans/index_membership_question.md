# Index membership — shape question

**Status:** open. No code has been written. This is a "should we, and where"
question, not an implementation plan.

## The ask, in the owner's words

> "More broadly I want my coverage manager to have my core stocks that I cover,
> particularly healthcare. I think more broadly, for historical testing and just
> comprehensiveness, it would be good to have all of the stocks that are also in
> the S&P 500 and the major Russell indices as well as S&P ACWI and World (just
> to know what the companies are and the stocks are). I don't know if that sits
> in coverage manager. Maybe it's a separate just index membership project. For
> instance one of the projects is screening S&P 500 companies versus the broader
> universe and so knowing all the individual constituents is helpful for
> analyses like that."

He wrote "S&P Aqui"; read as MSCI ACWI. S&P publishes the S&P 500 and the S&P
Global BMI; MSCI publishes ACWI and World. Flag it if that ambiguity changes the
answer.

## Scale

The curated universe is ~1,352 rows. S&P 500 ∪ Russell 3000 ∪ ACWI ∪ World is
perhaps 6,000–8,000 unique names globally, depending on overlap.

## The questions

1. **Same project or separate?** `data/coverage_universe_tickers.csv` is a
   decision record — every row got there through `candidate_ledger.csv` and a
   `[JP]` verdict. It carries `Sector (JP)`, a hand-assigned taxonomy whitelisted
   in `config.ALLOWED_SECTORS_JP`. What does that column mean for 6,000 names
   nobody has looked at? Is folding them in a category error, an attribute on
   existing rows, or a genuinely separate project?

2. **Is "historical testing" achievable at all?** Today's constituent list says
   nothing about 2019 membership, and reconstructing it from current data is
   survivorship-biased by construction. Does the stated use case actually require
   point-in-time membership? If the honest answer is "you cannot obtain history
   for free, you can only start accumulating it today", say so plainly.

3. **Sourcing and licensing, per index** — S&P 500, Russell 1000/2000/3000, MSCI
   ACWI, MSCI World. Which have a free, machine-readable, licensable-in-practice
   constituent source and which do not? Constituent lists are IP. Check the repo's
   own `.gitignore` for the rule it already applies to licensed index data, and
   check whether the remote is public.

4. **Smallest version that serves the stated use case** ("screening S&P 500
   companies versus the broader universe"). What would you do first, and what
   would you not do at all?

5. **What breaks.** As an attribute on the universe: per-row jobs (`enrich`,
   `delisted_check`, `reporting_calendar`, `cli.py performance`) scale with row
   count on a weekly request budget tuned to 1,352, and sibling projects import
   `exports/` by path. As a separate project: another scheduled job, another
   heartbeat, another staleness surface. Weigh both.

## Before answering, check what already exists

Do not assume this is greenfield. Search the whole fleet, not just this repo —
sibling projects live beside it under `Claude Folder/`. Look for any existing
constituent fetch, cached index list, or ETF-holdings download, in this repo and
in siblings such as `sector_chart_pack/`, `sigma-alert/`, `post_earnings_movers/`
and `forensic_triage/`. Relevant existing machinery in Coverage Manager:
`universe/crsp_snapshot.py`, `universe/foreign_identifiers.py`,
`universe/symbol_directory.py`, `providers/`.

If a capability already exists, say where, and say whether the right move is to
promote it rather than build it.

Verify against files rather than reasoning from plausibility. Be concise. Give a
recommendation with a first step, and name what you would explicitly decline.
