"""Enrich coverage CSV with comprehensive company identifiers.

Adds 16 new columns (ISIN, FIGI, CIK, Country, Currency, etc.) and renames
Sector -> Sector (JP), Subsector -> Subsector (JP). Safe to re-run (idempotent).
"""

import pandas as pd
import yfinance as yf
import requests
import time
import os
from datetime import datetime

from config import ALLOWED_SECTORS_JP, API_KEYS
from ticker_utils import (
    CSV_PATH, normalize_ticker, MANUAL_TICKER_MAP,
    EXCHANGE_TO_FIGI, EXCHANGE_TO_COUNTRY, COUNTRY_TO_ISO,
    COUNTRY_TO_ISIN_PREFIXES, COUNTRY_TO_ISO2, isin_check_digit_ok,
    normalize_company_for_comparison, backup_csv, read_universe_csv,
    write_universe_csv,
)
from logging_utils import configure_logging, get_logger, log_exception
from providers.fmp_provider import fetch_profile as _fmp_fetch_profile
from universe.isin_identity import VERDICT_OK, verify_isin_identity


class EnrichError(Exception):
    """Raised when single-ticker enrichment cannot produce a viable universe row."""

logger = get_logger("enrich_identifiers")

# New columns in desired order
NEW_COLUMNS_ORDER = [
    "Ticker", "Exchange", "Exchange Code", "Exchange Full Name",
    "Listing Type", "Other Listings", "Year Listed",
    "ISIN", "FIGI", "Composite FIGI", "Share Class FIGI", "CIK",
    "Company Name", "Country (HQ)", "Country (Listing)", "Country (ISO)",
    "Currency", "Website",
    "YF Sector", "YF Industry", "Sector (JP)", "Subsector (JP)",
]



def cell_is_empty(val):
    """Check if a cell value is empty/missing."""
    if val is None or pd.isna(val):
        return True
    s = str(val).strip()
    return s == "" or s == "nan"


def validate_isin_for_row(isin, row, ticker=""):
    """Return `isin` if it is structurally valid AND its 2-letter country
    prefix matches the row's listing country (or HQ as fallback), else `None`.
    Logs a warning on rejection.

    Two gates, both offline, run in this order — BEFORE any network check
    (the OpenFIGI identity check is downstream of this function):

    1. **ISO 6166 check digit** (`ticker_utils.isin_check_digit_ok`, added
       2026-07-28). Free, deterministic arithmetic that catches typos and
       structurally-invalid values regardless of country — including on rows
       whose countries are blank or unmapped, where the prefix rule cannot
       help. The live case: `CSU` carried `NET000CLBR01`, which is not
       structurally an ISIN at all.
    2. **Country prefix.** yfinance and FMP occasionally return a
       wrong-country ISIN for rebranded or recycled tickers — observed with
       the "FI" ticker for Fiserv (Swiss ISIN after the FISV→FI rebrand) and
       with several biotech tickers that were recycled to unrelated foreign
       issuers.

    The ISIN is accepted if its 2-letter prefix is in the acceptable-prefix
    set of EITHER `Country (HQ)` or `Country (Listing)`
    (`COUNTRY_TO_ISIN_PREFIXES` — set-valued, since e.g. Jersey issuers
    legitimately use JE or GB). ADRs need this looser rule because the
    underlying foreign ISIN (e.g., CH for a Swiss issuer listed on NASDAQ)
    is the canonical one, even though the listing country is the US.
    ADR-specific US-CUSIP ISINs (e.g., US-prefixed) also remain valid via
    the listing-country branch.

    Behavior when the row has no country info or the country isn't in
    `COUNTRY_TO_ISIN_PREFIXES`: the prefix check is skipped, the ISIN is
    accepted (the check-digit gate still applies).
    """
    if not isin:
        return None
    s = str(isin).strip()
    if not s or s == "-" or "error" in s.lower():
        return None
    if not isin_check_digit_ok(s):
        logger.warning(
            "ISIN rejected for %s: %s fails the ISO 6166 check digit "
            "(structurally invalid or a typo)",
            ticker or "?", s,
        )
        return None
    expected_prefixes = set()
    checked_countries = []
    for country_field in ("Country (HQ)", "Country (Listing)"):
        country = str(row.get(country_field, "") or "").strip()
        if country and country in COUNTRY_TO_ISIN_PREFIXES:
            expected_prefixes.update(COUNTRY_TO_ISIN_PREFIXES[country])
            checked_countries.append(country)
    isin_prefix = s[:2].upper()
    if expected_prefixes and isin_prefix not in expected_prefixes:
        logger.warning(
            "ISIN mismatch for %s: got %s (prefix %s) but row's countries "
            "are %s (expected one of %s) — rejecting",
            ticker or "?", s, isin_prefix, checked_countries,
            sorted(expected_prefixes),
        )
        return None
    return s


# ── Single-ticker enrichment ────────────────────────────────────────────────
# Used by `universe.watchlist.add(..., create_if_missing=True)` to build a
# universe row for a brand-new ticker without running the full 1091-row
# enrich pipeline. FMP's `/stable/profile` endpoint covers US names cleanly
# in one call; yfinance + OpenFIGI + SEC EDGAR fill in whatever FMP misses.

_UNIVERSE_ROW_COLUMNS = [
    "Ticker", "Exchange", "Exchange Code", "Exchange Full Name",
    "Listing Type", "Other Listings", "Year Listed",
    "ISIN", "FIGI", "Composite FIGI", "Share Class FIGI", "CIK",
    "Company Name", "Country (HQ)", "Country (Listing)", "Country (ISO)",
    "Currency", "Website",
    "YF Sector", "YF Industry", "Sector (JP)", "Subsector (JP)",
    "Sub-subsector (JP)", "Core",
]

# FMP's exchange names → Coverage Manager's standard Exchange column values.
_FMP_EXCHANGE_NORMALIZE = {
    "NASDAQ Global Select": "NASDAQ",
    "NASDAQ Global Market": "NASDAQ",
    "NASDAQ Capital Market": "NASDAQ",
    "NASDAQ": "NASDAQ",
    "New York Stock Exchange": "NYSE",
    "New York Stock Exchange Arca": "NYSE Arca",
    "NYSE": "NYSE",
    "NYSE American": "NYSE American",
    "NYSEArca": "NYSE Arca",
    "AMEX": "NYSE American",
    "BATS": "BATS",
    "OTC": "OTC",
}


def validate_sector_jp(sector):
    """Raise `EnrichError` if `sector` is not in the user-curated taxonomy."""
    if not sector or not str(sector).strip():
        raise EnrichError(
            "sector_jp is required when creating a new universe row — "
            f"must be one of: {sorted(ALLOWED_SECTORS_JP)}"
        )
    if sector not in ALLOWED_SECTORS_JP:
        raise EnrichError(
            f"unknown Sector (JP): {sector!r}. Allowed values: "
            f"{sorted(ALLOWED_SECTORS_JP)}"
        )


def _fetch_fmp_profile(ticker):
    """Hit FMP `/stable/profile` for a single ticker. Returns dict or {}.

    Delegates to the shared fmp_provider.fetch_profile implementation.
    """
    key = API_KEYS.get("FMP_API_KEY", "")
    return _fmp_fetch_profile(ticker, key)


#: Secondary gate only — see `_payload_names_match`, which is TOKEN-based first.
#: Deliberately stricter than `cik_backfill.NAME_MATCH_THRESHOLD` (0.55) because
#: it guards a strictly worse failure: that module binds one CIK, this one admits
#: an entire payload (CIK + FIGI + venue + currency + website + sector).
#:
#: 0.55 is provably too loose for the case that motivated this gate.
#: `SequenceMatcher("medartis", "medifast")` scores ~0.62 — Medartis and Medifast
#: are genuinely similar STRINGS, and character similarity cannot separate them.
#: Same for CSL Ltd vs Carlisle. So character distance is the fallback, not the
#: test.
PAYLOAD_NAME_THRESHOLD = 0.80

#: Shortest token that may carry a match on its own. Below this, coincidental
#: overlap dominates ("bio", "med", "pharma" are in half the universe).
_MIN_SHARED_TOKEN = 4

_LEGAL_FORM_TOKENS = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "limited",
    "ltd", "plc", "sa", "nv", "ag", "se", "spa", "as", "asa", "ab", "oyj",
    "holdings", "holding", "group", "the", "and", "of", "s", "a", "kgaa",
    "bv", "gmbh", "pt", "tbk", "psc", "llc", "lp", "trust",
})


def _payload_tokens(s: str) -> frozenset[str]:
    import re as _re
    raw = _re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split()
    return frozenset(t for t in raw if t and t not in _LEGAL_FORM_TOKENS)


def _payload_names_match(expected: str, vendor: str) -> bool | None:
    """Does a vendor payload describe the company this row is for?

    TOKEN-based, because character similarity demonstrably cannot do this job:
    `Medartis` vs `Medifast` scores 0.62 on difflib and they are different
    companies whose collision is the entire reason this gate exists. Tokens
    separate them cleanly — they share none.

    Returns True / False, or **None when the comparison cannot be made** (either
    side has no comparable token) — a comparison that cannot be made has no
    result, the rule `delisted_check` and `crsp_snapshot` already follow. The
    caller must treat None as "unknown", never as agreement.
    """
    import difflib

    e, v = _payload_tokens(expected), _payload_tokens(vendor)
    if not e or not v:
        return None
    # Equal, or one name is the other plus qualifiers ("Medacta" ⊂ "Medacta
    # International"). Subset, NOT raw substring: "ucb" is a literal substring of
    # "glucberry" and that must not read as a match.
    if e == v or e <= v or v <= e:
        return True
    shared = {t for t in (e & v) if len(t) >= _MIN_SHARED_TOKEN}
    if not shared:
        return False
    return difflib.SequenceMatcher(
        None, " ".join(sorted(e)), " ".join(sorted(v))
    ).ratio() >= PAYLOAD_NAME_THRESHOLD


def payload_is_for_this_row(expected_name: str, vendor_name: str, ticker: str,
                            source: str) -> bool:
    """Gate an ENTIRE vendor payload, not one field at a time.

    Why the whole payload (2026-07-29): the ISIN write path has been identity-
    gated since `81ada8d`, but every *other* field from the same response — CIK,
    FIGI, Currency, Exchange, Country, Website, YF Sector/Industry — landed
    unchecked. So a lookup that resolved to the wrong company was half-rejected:
    its ISIN was refused while its CIK, website and venue were written. That is
    exactly the state found on `MED` (Medifast's CIK 910329 + medifastinc.com on
    a Medartis row), `MOVE` (Corvex's), `UCB` (United Community Banks') and `CSL`
    (Carlisle's CIK, FIGI and website on CSL Ltd) — nine rows repaired by hand
    over two days, all of one shape.

    The mechanism behind it: `_fetch_fmp_profile` is called with the RAW ticker
    while every yfinance call goes through `normalize_ticker`, which appends an
    exchange suffix. So for a bare foreign symbol FMP answers about the US
    namesake. Worse, its payload overwrites `Exchange` — and `normalize_ticker`
    *keys off* `Exchange`, so corrupting it makes the NEXT run's yfinance call go
    bare too. The loop closes and all four columns agree, which is why the
    failure is self-concealing.

    Rejecting the payload is better than rewriting the symbol: FMP's foreign
    symbol conventions differ from yfinance's, and a rejected payload simply
    falls through to the yfinance path, which IS normalized and resolves the
    right issuer. A warned skip is resolved by a human in seconds; a wrong value
    looks like data forever.
    """
    verdict = _payload_names_match(expected_name, vendor_name)
    if verdict is False:
        logger.warning(
            "%s: DISCARDING the whole %s payload - it describes %r, not %r. "
            "A bare foreign ticker resolving to a US namesake is the usual "
            "cause; the row keeps its existing values.",
            ticker, source, str(vendor_name), str(expected_name))
        return False
    return True


def _normalize_fmp_exchange(fmp_exchange_full, fmp_exchange_short):
    """Pick a Coverage-Manager-canonical exchange name from FMP's fields."""
    for candidate in (fmp_exchange_full, fmp_exchange_short):
        if not candidate:
            continue
        s = str(candidate).strip()
        if s in _FMP_EXCHANGE_NORMALIZE:
            return _FMP_EXCHANGE_NORMALIZE[s]
    # Fall back to short code if it already matches a known exchange name
    s = str(fmp_exchange_short or "").strip()
    if s and s in EXCHANGE_TO_COUNTRY:
        return s
    return ""


def _empty_row():
    return {c: "" for c in _UNIVERSE_ROW_COLUMNS}


# Reverse of COUNTRY_TO_ISO2 (1:1 by construction), for normalizing FMP's
# country field (which sometimes returns ISO 3166 alpha-2 codes like "US"
# instead of full names like "United States"). Built from the identity-code
# map, NOT the ISIN-prefix map: prefix sets are many-to-one (Jersey also
# accepts GB), so reversing them would map GB to the wrong country.
_ISO2_TO_COUNTRY = {v: k for k, v in COUNTRY_TO_ISO2.items()}


def _normalize_country_name(raw):
    """Return a full country name for `raw`, which may be an ISO alpha-2
    code (FMP sometimes returns "US" instead of "United States") or the
    full name already. Unknown values pass through unchanged."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if len(s) == 2 and s.upper() in _ISO2_TO_COUNTRY:
        return _ISO2_TO_COUNTRY[s.upper()]
    return s


def enrich_single_ticker(ticker, sector_jp, exchange_hint=None, company_hint=None):
    """Build a full universe-CSV row for a brand-new ticker.

    Pass `company_hint` whenever the caller knows which company it means — it is
    what lets the whole vendor payload be identity-gated (see
    `payload_is_for_this_row`). Without it, a bare foreign ticker that a US
    company also uses silently populates the row with the namesake's CIK,
    website, venue and currency. **Strongly recommended for any non-US name.**

    Contract:
      - Validates `sector_jp` against the ALLOWED_SECTORS_JP taxonomy.
      - Primary source: FMP `/stable/profile` (ISIN, CIK, IPO year, sector,
        industry, website, exchange, currency, country, company name).
      - Fallback: yfinance `Ticker.info` + `Ticker.isin` for anything FMP
        left blank. Uses `exchange_hint` to normalize the yfinance symbol
        when the ticker has a regional suffix.
      - FIGI fields come from OpenFIGI (`fetch_openfigi_identifiers` on a
        single-row DataFrame).
      - CIK fallback: SEC EDGAR bulk map (for when FMP omits CIK on non-US
        names or when the SEC map lags a ticker rebrand).
      - Runs `validate_isin_for_row` so a wrong-country ISIN from yfinance
        never lands in the row.

    Raises `EnrichError` when the result is missing any of the
    watchlist-required metadata fields: Company Name, Sector (JP), Currency,
    Exchange. `Sector (JP)` comes from the caller; the other three must come
    from the data sources.

    Returns a dict keyed by the universe CSV's column names, suitable for
    appending to `data/coverage_universe_tickers.csv` via csv.DictWriter.
    """
    validate_sector_jp(sector_jp)

    ticker = str(ticker or "").strip()
    if not ticker:
        raise EnrichError("ticker is required")

    row = _empty_row()
    row["Ticker"] = ticker
    row["Sector (JP)"] = sector_jp
    row["Listing Type"] = "Primary"

    sources_used = []

    # ── 1. FMP /stable/profile ───────────────────────────────────────────
    fmp_isin_candidate = ""
    fmp = _fetch_fmp_profile(ticker)
    if fmp and company_hint and not payload_is_for_this_row(
            company_hint, fmp.get("companyName", ""), ticker, "FMP profile"):
        # Whole payload discarded, not just its ISIN. Falls through to the
        # yfinance path below, which goes through `normalize_ticker` and so asks
        # about the right listing.
        sources_used.append("fmp-rejected(identity)")
        fmp = {}
    if fmp:
        sources_used.append("fmp")
        row["Company Name"] = str(fmp.get("companyName", "") or "").strip()
        # Defer ISIN write until Country fields are populated so the same
        # validate_isin_for_row guard used for yfinance can run here too.
        fmp_isin_candidate = str(fmp.get("isin", "") or "").strip()
        cik = str(fmp.get("cik", "") or "").strip().lstrip("0")
        if cik:
            row["CIK"] = cik
        ipo = str(fmp.get("ipoDate", "") or "").strip()
        if ipo and len(ipo) >= 4:
            row["Year Listed"] = ipo[:4]
        row["Website"] = str(fmp.get("website", "") or "").strip()
        row["YF Sector"] = str(fmp.get("sector", "") or "").strip()
        row["YF Industry"] = str(fmp.get("industry", "") or "").strip()
        row["Currency"] = str(fmp.get("currency", "") or "").strip()
        row["Country (HQ)"] = _normalize_country_name(fmp.get("country", ""))
        exch = _normalize_fmp_exchange(
            fmp.get("exchangeFullName"), fmp.get("exchange")
        )
        if exch:
            row["Exchange"] = exch

    # Exchange hint override (non-US cases where FMP is weak)
    if exchange_hint:
        row["Exchange"] = exchange_hint

    # ── 2. Derive country (Listing)/(ISO) from exchange ──────────────────
    if row["Exchange"] and not row["Country (Listing)"]:
        row["Country (Listing)"] = EXCHANGE_TO_COUNTRY.get(row["Exchange"], "")
    if row["Country (HQ)"] and not row["Country (Listing)"]:
        row["Country (Listing)"] = row["Country (HQ)"]
    if row["Country (Listing)"]:
        row["Country (ISO)"] = COUNTRY_TO_ISO.get(row["Country (Listing)"], "")

    # Validate FMP's ISIN now that Country fields are populated. Mirrors
    # the same guard applied to yfinance's ISIN below — protects against
    # FMP returning a wrong-issuer ISIN on a recycled ticker.
    if fmp_isin_candidate:
        checked = validate_isin_for_row(fmp_isin_candidate, row, ticker=ticker)
        if checked:
            row["ISIN"] = checked

    # ── 3. yfinance fallback for empty fields ────────────────────────────
    needs_yf = not all(row[c] for c in ("Company Name", "Currency", "ISIN", "Year Listed"))
    if needs_yf:
        try:
            yf_ticker = normalize_ticker(
                ticker,
                company_name=row.get("Company Name", ""),
                exchange=row.get("Exchange", ""),
            )
            if yf_ticker:
                yt = yf.Ticker(yf_ticker)
                sources_used.append("yfinance")

                if not row["ISIN"]:
                    try:
                        candidate_isin = yt.isin
                        checked = validate_isin_for_row(candidate_isin, row, ticker=ticker)
                        if checked:
                            row["ISIN"] = checked
                    except Exception as e:
                        log_exception(logger, f"yfinance ISIN failed for {ticker}", e)

                try:
                    info = yt.info or {}
                except Exception as e:
                    log_exception(logger, f"yfinance info failed for {ticker}", e)
                    info = {}

                if info:
                    if not row["Company Name"]:
                        row["Company Name"] = str(info.get("longName", "") or info.get("shortName", "")).strip()
                    if not row["Exchange Code"]:
                        row["Exchange Code"] = str(info.get("exchange", "") or "").strip()
                    if not row["Exchange Full Name"]:
                        row["Exchange Full Name"] = str(info.get("fullExchangeName", "") or "").strip()
                    if not row["Currency"]:
                        row["Currency"] = str(info.get("currency", "") or "").strip()
                    if not row["Country (HQ)"]:
                        row["Country (HQ)"] = str(info.get("country", "") or "").strip()
                    if not row["Website"]:
                        row["Website"] = str(info.get("website", "") or "").strip()
                    if not row["YF Sector"]:
                        row["YF Sector"] = str(info.get("sector", "") or "").strip()
                    if not row["YF Industry"]:
                        row["YF Industry"] = str(info.get("industry", "") or "").strip()
                    if not row["Year Listed"]:
                        first_trade_ms = info.get("firstTradeDateEpochUtc") or info.get("firstTradeDateMilliseconds")
                        if first_trade_ms:
                            if first_trade_ms > 1e12:
                                first_trade_ms = first_trade_ms / 1000
                            year = datetime.utcfromtimestamp(first_trade_ms).year
                            if 1900 < year <= datetime.now().year:
                                row["Year Listed"] = str(year)
        except Exception as e:
            log_exception(logger, f"yfinance fallback failed for {ticker}", e)

    # Re-derive country fields if yfinance filled Country (HQ)
    if row["Country (HQ)"] and not row["Country (Listing)"]:
        row["Country (Listing)"] = row["Country (HQ)"]
    if row["Country (Listing)"] and not row["Country (ISO)"]:
        row["Country (ISO)"] = COUNTRY_TO_ISO.get(row["Country (Listing)"], "")

    # ── 4. OpenFIGI for FIGI fields ──────────────────────────────────────
    if not row["FIGI"] or not row["Composite FIGI"]:
        try:
            mini_df = pd.DataFrame([{
                "Ticker": ticker,
                "Company Name": row["Company Name"],
                "Exchange": row["Exchange"],
            }])
            figi_map = fetch_openfigi_identifiers(mini_df)
            figi_data = figi_map.get(ticker, {})
            if figi_data:
                sources_used.append("openfigi")
                for key in ("FIGI", "Composite FIGI", "Share Class FIGI"):
                    if not row[key] and figi_data.get(key):
                        row[key] = figi_data[key]
        except Exception as e:
            log_exception(logger, f"OpenFIGI lookup failed for {ticker}", e)

    # ── 5. SEC EDGAR CIK fallback ────────────────────────────────────────
    # Identity-gated, and via the TITLED loader. This module's own
    # `fetch_sec_cik_map` returns {TICKER: cik} and throws SEC's `title` away, so
    # there was nothing to compare and the CIK was bound on a bare ticker match
    # alone -- the one write `cik_backfill` refuses to make unguarded, for the
    # documented reason that a wrong CIK silently pulls another company's
    # filings while a blank one is visibly missing. `load_sec_cik_map` returns
    # (cik, title) and is 24h-cached, so this also stops a second ~1MB download.
    if not row["CIK"]:
        try:
            from universe.cik_backfill import fetch_sec_cik_map as _titled_map
            titled = _titled_map()
            hit = titled.get(ticker.upper())
            expected = company_hint or row.get("Company Name", "")
            if hit:
                cik, sec_title = hit[0], (hit[1] if len(hit) > 1 else "")
                if expected and not payload_is_for_this_row(
                        expected, sec_title, ticker, "SEC ticker map"):
                    sources_used.append("sec-rejected(identity)")
                else:
                    row["CIK"] = str(cik)
                    sources_used.append("sec")
        except Exception as e:
            log_exception(logger, f"SEC CIK lookup failed for {ticker}", e)

    # ── 5b. ISIN identity cross-check (OpenFIGI) ─────────────────────────
    # The prefix guard above is a COUNTRY check; this is the IDENTITY check.
    # Nipro (8086.T) carried NMS Holdings' JP-prefixed ISIN and passed the
    # prefix rule — any non-`ok` verdict (conflict OR inconclusive) defers
    # the write. A blank ISIN is refilled by the next enrich run; an
    # unverified one must never read as validated (found/clean/inconclusive).
    if row["ISIN"] and row["Company Name"]:
        identity = verify_isin_identity(row["ISIN"], row["Company Name"])
        if identity.verdict != VERDICT_OK:
            logger.warning(
                "dropping ISIN %s for %s: identity %s (%s)",
                row["ISIN"], ticker, identity.verdict, identity.reason,
            )
            row["ISIN"] = ""

    logger.info(
        "enrich_single_ticker(%s): sources=%s",
        ticker, ",".join(sources_used) or "none",
    )

    # ── 6. Validate required watchlist metadata fields ───────────────────
    required = ("Company Name", "Exchange", "Currency", "Sector (JP)")
    missing = [f for f in required if not row[f]]
    if missing:
        raise EnrichError(
            f"could not resolve required metadata for {ticker}: missing "
            f"{', '.join(missing)}. Sources tried: {sources_used or ['none']}. "
            f"Check the ticker spelling or pass --exchange for non-US names."
        )

    return row


def fetch_yfinance_identifiers(df):
    """Fetch identifiers from yfinance for all tickers.

    Returns dict of {original_ticker: {field: value, ...}}.
    """
    results = {}
    total = len(df)

    for i, (_, row) in enumerate(df.iterrows()):
        orig_ticker = str(row["Ticker"]).strip()
        company = str(row.get("Company Name", "")).strip()
        exchange = str(row.get("Exchange", "")).strip()
        yf_ticker = normalize_ticker(orig_ticker, company, exchange)

        if not yf_ticker:
            continue

        if i > 0 and i % 50 == 0:
            logger.info("yfinance: %s/%s...", i, total)

        data = {}
        try:
            t = yf.Ticker(yf_ticker)

            # ISIN — prefix-checked against the row's listing country, then
            # identity-checked against the issuer's name (OpenFIGI). The
            # identity call runs ONLY when the row's ISIN cell is blank —
            # `enrich_dataframe` fills blanks only, so checking rows that
            # already carry an ISIN would burn an API call per row for a
            # value that will never be written.
            try:
                checked = validate_isin_for_row(t.isin, row, ticker=orig_ticker)
                if checked and not cell_is_empty(row.get("ISIN")):
                    data["ISIN"] = checked  # never written; kept for parity
                elif checked:
                    identity = verify_isin_identity(
                        checked, str(row.get("Company Name", "") or ""))
                    if identity.verdict == VERDICT_OK:
                        data["ISIN"] = checked
                    else:
                        logger.warning(
                            "not writing ISIN %s for %s: identity %s (%s)",
                            checked, orig_ticker, identity.verdict,
                            identity.reason,
                        )
            except Exception as e:
                log_exception(logger, f"ISIN lookup failed for {orig_ticker}", e)

            # Info dict
            try:
                info = t.info
                if info:
                    data["Exchange Code"] = info.get("exchange", "")
                    data["Exchange Full Name"] = info.get("fullExchangeName", "")
                    data["Currency"] = info.get("currency", "")
                    data["Country (HQ)"] = info.get("country", "")
                    data["Website"] = info.get("website", "")
                    data["YF Sector"] = info.get("sector", "")
                    data["YF Industry"] = info.get("industry", "")

                    # Year Listed from firstTradeDateMilliseconds
                    first_trade_ms = info.get("firstTradeDateEpochUtc") or info.get("firstTradeDateMilliseconds")
                    if first_trade_ms:
                        # firstTradeDateEpochUtc is in seconds, firstTradeDateMilliseconds in ms
                        if first_trade_ms > 1e12:
                            first_trade_ms = first_trade_ms / 1000
                        year = datetime.utcfromtimestamp(first_trade_ms).year
                        if 1900 < year <= datetime.now().year:
                            data["Year Listed"] = str(year)
            except Exception as e:
                log_exception(logger, f"Info lookup failed for {orig_ticker}", e)

        except Exception as e:
            log_exception(logger, f"Ticker lookup failed for {orig_ticker}", e)

        results[orig_ticker] = data
        time.sleep(0.05)  # Light rate limiting

    return results


def fetch_openfigi_identifiers(df):
    """Fetch FIGI identifiers from OpenFIGI API.

    Returns dict of {original_ticker: {figi, composite_figi, share_class_figi}}.
    """
    results = {}

    # Build request items
    items = []
    for _, row in df.iterrows():
        orig_ticker = str(row["Ticker"]).strip()
        exchange = str(row.get("Exchange", "")).strip()
        company = str(row.get("Company Name", "")).strip()

        yf_ticker = normalize_ticker(orig_ticker, company, exchange)
        if not yf_ticker:
            continue

        # Extract the base symbol for OpenFIGI
        # For tickers like "7733.T", use "7733"; for "ABBV", use "ABBV"
        base_symbol = yf_ticker.split(".")[0] if "." in yf_ticker else yf_ticker

        figi_exch = EXCHANGE_TO_FIGI.get(exchange, "")

        item = {"idType": "TICKER", "idValue": base_symbol}
        if figi_exch:
            item["exchCode"] = figi_exch
        items.append((orig_ticker, item))

    # Free tier (no API key) allows max 10 items per request
    batch_size = 10
    total_batches = (len(items) + batch_size - 1) // batch_size
    url = "https://api.openfigi.com/v3/mapping"
    headers = {"Content-Type": "application/json"}

    for batch_idx in range(0, len(items), batch_size):
        batch = items[batch_idx:batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1
        logger.info("OpenFIGI batch %s/%s (%s items)...", batch_num, total_batches, len(batch))

        payload = [item for _, item in batch]
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                response_data = resp.json()
                for j, (orig_ticker, _) in enumerate(batch):
                    if j < len(response_data):
                        entry = response_data[j]
                        if "data" in entry and entry["data"]:
                            d = entry["data"][0]
                            results[orig_ticker] = {
                                "FIGI": d.get("figi", ""),
                                "Composite FIGI": d.get("compositeFIGI", ""),
                                "Share Class FIGI": d.get("shareClassFIGI", ""),
                            }
            elif resp.status_code == 429:
                logger.warning("OpenFIGI rate limited, waiting 10s...")
                time.sleep(10)
                # Retry this batch
                try:
                    resp = requests.post(url, json=payload, headers=headers, timeout=30)
                    if resp.status_code == 200:
                        response_data = resp.json()
                        for j, (orig_ticker, _) in enumerate(batch):
                            if j < len(response_data):
                                entry = response_data[j]
                                if "data" in entry and entry["data"]:
                                    d = entry["data"][0]
                                    results[orig_ticker] = {
                                        "FIGI": d.get("figi", ""),
                                        "Composite FIGI": d.get("compositeFIGI", ""),
                                        "Share Class FIGI": d.get("shareClassFIGI", ""),
                                    }
                except Exception as e:
                    log_exception(logger, "OpenFIGI retry failed", e)
            else:
                logger.warning("OpenFIGI error: HTTP %s", resp.status_code)
        except Exception as e:
            log_exception(logger, "OpenFIGI request error", e)

        # Rate limiting: 25 req/min without API key
        if batch_num < total_batches:
            time.sleep(3)

    return results


def fetch_sec_cik_map():
    """Download SEC EDGAR bulk ticker->CIK mapping.

    Returns dict of {TICKER: cik_number_string}.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": "CoverageManager/1.0 (coverage-research@example.com)"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.warning("SEC EDGAR error: HTTP %s", resp.status_code)
            return {}
        data = resp.json()
        cik_map = {}
        for entry in data.values():
            ticker = str(entry.get("ticker", "")).strip().upper()
            cik = entry.get("cik_str")
            if ticker and cik:
                cik_map[ticker] = str(cik)
        logger.info("SEC EDGAR: loaded %s ticker->CIK mappings", len(cik_map))
        return cik_map
    except Exception as e:
        log_exception(logger, "SEC EDGAR error", e)
        return {}


def detect_listing_type_and_other_listings(df, yf_data):
    """Determine Listing Type and Other Listings for each ticker.

    Listing Type: "Primary" if company domicile matches exchange country,
                  "ADR/Cross-listed" if mismatch.
    Other Listings: other tickers in the CSV for the same company.
    """
    listing_types = {}
    other_listings = {}

    # Build normalized company name -> list of tickers
    name_to_tickers = {}
    for _, row in df.iterrows():
        ticker = str(row["Ticker"]).strip()
        company = str(row.get("Company Name", "")).strip()
        if not company or company == "nan":
            continue
        norm_name = normalize_company_for_comparison(company)
        if norm_name:
            name_to_tickers.setdefault(norm_name, []).append(ticker)

    for _, row in df.iterrows():
        ticker = str(row["Ticker"]).strip()
        exchange = str(row.get("Exchange", "")).strip()

        # Listing Type
        yf_info = yf_data.get(ticker, {})
        country_hq = yf_info.get("Country (HQ)", "")
        country_listing = EXCHANGE_TO_COUNTRY.get(exchange, "")

        if country_hq and country_listing:
            if country_hq.lower() == country_listing.lower():
                listing_types[ticker] = "Primary"
            else:
                listing_types[ticker] = "ADR/Cross-listed"
        else:
            listing_types[ticker] = ""

        # Other Listings - find other tickers with same normalized company name
        company = str(row.get("Company Name", "")).strip()
        if company and company != "nan":
            norm_name = normalize_company_for_comparison(company)
            siblings = name_to_tickers.get(norm_name, [])
            others = [t for t in siblings if t != ticker]
            if others:
                other_listings[ticker] = ", ".join(others)
            elif listing_types.get(ticker) == "ADR/Cross-listed" and country_hq:
                other_listings[ticker] = f"Primary listing likely in {country_hq}"
            else:
                other_listings[ticker] = ""
        else:
            other_listings[ticker] = ""

    return listing_types, other_listings


def enrich_dataframe(df, yf_data, figi_data, cik_map, listing_types, other_listings):
    """Add all new columns to the dataframe and reorder."""
    # Rename Sector -> Sector (JP), Subsector -> Subsector (JP)
    rename_map = {}
    if "Sector" in df.columns and "Sector (JP)" not in df.columns:
        rename_map["Sector"] = "Sector (JP)"
    if "Subsector" in df.columns and "Subsector (JP)" not in df.columns:
        rename_map["Subsector"] = "Subsector (JP)"
    if rename_map:
        df = df.rename(columns=rename_map)

    # Initialize new columns if they don't exist, and ensure string dtype
    new_cols = [
        "Exchange Code", "Exchange Full Name", "Listing Type", "Other Listings",
        "Year Listed", "ISIN", "FIGI", "Composite FIGI", "Share Class FIGI", "CIK",
        "Country (HQ)", "Country (Listing)", "Country (ISO)", "Currency", "Website",
        "YF Sector", "YF Industry",
    ]
    for col in new_cols:
        if col not in df.columns:
            df[col] = ""
        # Ensure column is object dtype so we can assign strings freely
        df[col] = df[col].astype(object)

    # Populate data (idempotent — only fill empty cells)
    for idx, row in df.iterrows():
        ticker = str(row["Ticker"]).strip()
        exchange = str(row.get("Exchange", "")).strip()
        yf_info = yf_data.get(ticker, {})
        figi_info = figi_data.get(ticker, {})

        # yfinance fields
        yf_fields = [
            "Exchange Code", "Exchange Full Name", "Currency",
            "Country (HQ)", "Website", "YF Sector", "YF Industry", "ISIN",
        ]
        for field in yf_fields:
            if cell_is_empty(row.get(field)):
                val = yf_info.get(field, "")
                if val and str(val).strip():
                    df.at[idx, field] = val

        # Year Listed
        if cell_is_empty(row.get("Year Listed")):
            year = yf_info.get("Year Listed")
            if year:
                df.at[idx, "Year Listed"] = year

        # FIGI fields
        for field in ["FIGI", "Composite FIGI", "Share Class FIGI"]:
            if cell_is_empty(row.get(field)):
                val = figi_info.get(field, "")
                if val:
                    df.at[idx, field] = val

        # CIK (US tickers only) — identity-gated.
        #
        # This is the bulk sibling of the single-ticker fallback and it ran over
        # every row in the universe binding a CIK on a BARE TICKER MATCH alone.
        # `cik_backfill` refuses to make exactly this write unguarded, for the
        # reason its docstring gives: a wrong CIK silently pulls another
        # company's filings, while a blank one is visibly missing. Tickers are
        # shared between issuers across venues — CSL Ltd / Carlisle Companies,
        # Medartis / Medifast — so a bare match is not identity.
        #
        # `cik_map` here is {TICKER: cik} with no title, so the gate can only run
        # when a titled map is available; the untitled path is left as-is rather
        # than silently trusted, and the skip is logged.
        if cell_is_empty(row.get("CIK")):
            cik = cik_map.get(ticker.upper())
            if cik:
                stored_name = str(row.get("Company Name", "") or "").strip()
                sec_title = ""
                if isinstance(cik, (tuple, list)) and len(cik) > 1:
                    cik, sec_title = cik[0], cik[1]
                if sec_title and stored_name and not payload_is_for_this_row(
                        stored_name, sec_title, ticker, "SEC ticker map (bulk)"):
                    pass                       # rejected; leave the cell blank
                else:
                    df.at[idx, "CIK"] = cik

        # Country (Listing) from exchange mapping
        if cell_is_empty(row.get("Country (Listing)")):
            country = EXCHANGE_TO_COUNTRY.get(exchange, "")
            if country:
                df.at[idx, "Country (Listing)"] = country

        # Country (ISO) from Country (Listing)
        if cell_is_empty(row.get("Country (ISO)")):
            country_listing = df.at[idx, "Country (Listing)"]
            if not cell_is_empty(country_listing):
                iso = COUNTRY_TO_ISO.get(str(country_listing).strip(), "")
                if iso:
                    df.at[idx, "Country (ISO)"] = iso

        # Listing Type
        if cell_is_empty(row.get("Listing Type")):
            lt = listing_types.get(ticker, "")
            if lt:
                df.at[idx, "Listing Type"] = lt

        # Other Listings
        if cell_is_empty(row.get("Other Listings")):
            ol = other_listings.get(ticker, "")
            if ol:
                df.at[idx, "Other Listings"] = ol

    # Reorder columns
    final_cols = [c for c in NEW_COLUMNS_ORDER if c in df.columns]
    # Add any extra columns not in our order (shouldn't happen, but safety)
    for c in df.columns:
        if c not in final_cols:
            final_cols.append(c)
    df = df[final_cols]

    return df


def print_summary(df, yf_data, figi_data, cik_map):
    """Print enrichment summary."""
    total = len(df)

    def count_filled(col):
        return sum(1 for _, row in df.iterrows() if not cell_is_empty(row.get(col)))

    print("\n" + "=" * 60)
    print("ENRICHMENT SUMMARY")
    print("=" * 60)
    print(f"Total tickers: {total}")
    print(f"\nColumn fill rates:")
    check_cols = [
        "Exchange Code", "Exchange Full Name", "Listing Type", "Year Listed",
        "ISIN", "FIGI", "Composite FIGI", "Share Class FIGI", "CIK",
        "Country (HQ)", "Country (Listing)", "Currency", "Website",
        "YF Sector", "YF Industry",
    ]
    for col in check_cols:
        if col in df.columns:
            filled = count_filled(col)
            pct = filled / total * 100 if total > 0 else 0
            print(f"  {col:25s}: {filled:4d}/{total} ({pct:.0f}%)")

    print(f"\nAPI results:")
    print(f"  yfinance: {len(yf_data)} tickers returned data")
    print(f"  OpenFIGI: {len(figi_data)} tickers matched")
    print(f"  SEC CIK:  {len(cik_map)} total mappings loaded")
    print("=" * 60)


def main():
    configure_logging()
    print("=" * 60)
    print("Coverage Universe Identifier Enrichment")
    print("=" * 60)

    # Step 1: Backup
    print("\n1. Creating backup...")
    backup_path = backup_csv(CSV_PATH)
    print(f"   Backup: {backup_path}")

    # Step 2: Load CSV
    print("\n2. Loading CSV...")
    df = read_universe_csv(CSV_PATH)
    print(f"   {len(df)} rows, columns: {list(df.columns)}")

    # Step 3: Fetch SEC CIK (single bulk download, fast)
    print("\n3. Fetching SEC CIK mappings...")
    # TITLED map ({TICKER: (cik, sec_title)}), so `enrich_dataframe`'s identity
    # gate can actually fire. With the untitled {TICKER: cik} form there is no
    # name to compare and the gate silently degrades to "write it anyway" — a
    # guard that cannot fire is decoration, which is the failure mode this repo
    # keeps paying for. Falls back to the untitled map if the titled loader is
    # unavailable, so a download failure degrades rather than crashes.
    try:
        from universe.cik_backfill import fetch_sec_cik_map as _titled_map
        cik_map = _titled_map()
        if not cik_map:
            cik_map = fetch_sec_cik_map()
    except Exception as e:  # noqa: BLE001
        log_exception(logger, "titled SEC map unavailable; falling back", e)
        cik_map = fetch_sec_cik_map()

    # Step 4: Fetch yfinance identifiers
    print(f"\n4. Fetching yfinance identifiers for {len(df)} tickers...")
    yf_data = fetch_yfinance_identifiers(df)
    print(f"   yfinance returned data for {len(yf_data)} tickers")

    # Step 5: Fetch OpenFIGI identifiers
    print(f"\n5. Fetching OpenFIGI identifiers...")
    figi_data = fetch_openfigi_identifiers(df)
    print(f"   OpenFIGI matched {len(figi_data)} tickers")

    # Step 6: Detect listing types
    print("\n6. Detecting listing types and cross-listings...")
    listing_types, other_listings = detect_listing_type_and_other_listings(df, yf_data)

    # Step 7: Enrich dataframe
    print("\n7. Enriching dataframe...")
    df = enrich_dataframe(df, yf_data, figi_data, cik_map, listing_types, other_listings)

    # Step 8: Save
    print("\n8. Saving enriched CSV...")
    write_universe_csv(df, CSV_PATH)
    print(f"   Saved: {CSV_PATH}")

    # Summary
    print_summary(df, yf_data, figi_data, cik_map)


if __name__ == "__main__":
    main()
