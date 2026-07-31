"""ISIN -> issuer-name identity cross-check against OpenFIGI.

WHY THIS EXISTS
---------------
`enrich.validate_isin_for_row` is a *country* check: it accepts any ISIN whose
2-letter prefix matches the row's HQ or listing country. That caught six of the
seven wrong-issuer ISINs corrected on 2026-07-28 — but not Nipro (`8086.T`),
which carried NMS Holdings' `JP3750800009`: also Japanese, so the prefix
matched and the guard accepted another company's identity. yfinance's `.isin`
property still serves that exact value today. A prefix check is a country
check, not an identity check; this module is the identity check.

It asks OpenFIGI (already this repo's FIGI source; free, keyless at this
volume) who an ISIN belongs to, and compares that name to the row's
`Company Name`.

THREE STATES, NEVER TWO (found / clean / inconclusive)
------------------------------------------------------
* ``ok``           — OpenFIGI returned issuer names and at least one matches.
* ``conflict``     — OpenFIGI returned names and NONE match: the ISIN
                     identifies a different issuer.
* ``inconclusive`` — nothing was learned: API unreachable / rate-limited
                     (``openfigi-unreachable``), the ISIN has no OpenFIGI
                     coverage (``no-openfigi-coverage``), or the row has no
                     Company Name to compare (``no-company-name``).

``inconclusive`` is NEVER treated as validated. On the enrich write path all
non-``ok`` verdicts defer the write: a blank cell is refilled by the next run
once the API answers; a wrong value looks like data forever.

WHAT IS DELIBERATELY NOT A FINDING
----------------------------------
Same issuer, different security line. PharmaEssentia's GDR (`US7169722037`,
"PHARMAESSENTIA CORP-GDS REGS") and Alphabet's Canadian CDR (`CA02080M1005`,
"ALPHABET INC") both PASS this check — the issuer is right even though the
listing is not. That class is `crosscheck-foreign`'s ``listing-mismatch``,
kept apart on purpose.

NAME MATCHING (design notes, from live OpenFIGI captures 2026-07-28)
--------------------------------------------------------------------
OpenFIGI names are upper-case, legal-form-abbreviated, and — the big hazard —
**truncated at ~28 characters** ("SUN PHARMACEUTICAL INDUS", "KINIKSA
PHARMACEUTICALS INTE"). A guard that false-rejects legitimate rows gets
switched off, so matching is:

1. normalise: NFKD accent-strip, casefold, ``&``->``and``, punctuation to
   spaces, drop legal-form / share-class / stop tokens (Ltd, PLC, AG, KK,
   HLDGS, -CL B, BTA, publ, ADR, ...); if stripping empties a name, fall back
   to its unstripped tokens;
2. token match: equal, or one is a prefix of the other with the shorter side
   >= 4 chars (handles truncation: "indus" ~ "industries" — but NOT
   "zen" ~ "zentek", which is how Zen Technologies collided with Zentek);
3. names match when either token set is fully covered by the other, or as a
   last resort difflib ratio >= 0.85 on the normalised strings;
4. an ISIN maps to many securities; agreement with ANY of its names is ``ok``
   (a renamed issuer keeps its ISIN — both names identify the same company).

CACHING / RATE LIMITS (observed live 2026-07-28)
------------------------------------------------
Keyless OpenFIGI: ``ratelimit-policy: 25;w=60`` (25 requests/min) and 10
mapping jobs per request -> a full 795-ISIN universe pass is ~80 requests,
~3.5 minutes. Deterministic outcomes (names, and explicit "No identifier
found") are cached in ``cache/openfigi_isin_names.json``; transient transport
failures are never cached (CONVENTIONS.md 3b #7).
"""

from __future__ import annotations

import json
import os
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import requests

import config
from logging_utils import get_logger

logger = get_logger("isin_identity")

VERDICT_OK = "ok"
VERDICT_CONFLICT = "conflict"
VERDICT_INCONCLUSIVE = "inconclusive"

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
BATCH_SIZE = 10          # keyless jobs-per-request limit
BATCH_SLEEP_SECONDS = 2.5  # keyless: 25 requests / 60s
RATE_LIMIT_BACKOFF_SECONDS = 15.0
CACHE_PATH = config.CACHE_DIR / "openfigi_isin_names.json"

# Tokens that carry no issuer identity: legal forms, share-class / line tags,
# and connective stopwords. Includes the truncated forms OpenFIGI produces
# ("lt" for Ltd) and both sides of abbreviation pairs (HLDGS/HOLDINGS).
_NON_IDENTITY_TOKENS = frozenset({
    # legal forms
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "ltd", "limited", "lt", "llc", "llp", "lp", "plc", "pcl", "pte", "pty",
    "sa", "se", "nv", "bv", "ag", "kgaa", "gmbh", "ab", "asa", "as", "aps",
    "oyj", "oy", "spa", "srl", "sarl", "kk", "publ", "tbk", "pt", "bhd",
    "berhad", "societe", "anonyme", "aktiengesellschaft", "aktiebolag",
    "kabushiki", "kaisha",
    # holding-structure noise
    "holdings", "holding", "hldgs", "hldg", "group", "grp",
    # depositary / share-class / line tags ("sp"/"spon" = sponsored ADR:
    # "TEVA PHARMACEUTICAL-SP ADR" is Teva's own US line, not another issuer)
    "adr", "ads", "gdr", "gds", "cdr", "reg", "regs", "cl", "class", "bta",
    "sponsored", "unsponsored", "sp", "spon", "ord", "shs",
    # stopwords
    "and", "the", "of", "de", "du", "des", "et", "van", "der", "di",
})

_PREFIX_MIN_CHARS = 4       # truncation tolerance floor ("zen" must not match "zentek")
_RATIO_FLOOR = 0.85         # last-resort fuzzy accept on normalised strings

# a few Latin letters NFKD leaves undecomposed
_CHAR_MAP = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae",
                           "ß": "ss", "đ": "d", "Đ": "d", "ł": "l", "Ł": "l"})


def _ascii(text) -> str:
    """cp1252-safe form for console/log output (the universe is global)."""
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


@dataclass(frozen=True)
class IsinIdentityResult:
    verdict: str
    reason: str
    openfigi_names: tuple = ()


# ── name normalisation + matching (pure) ─────────────────────────────────────


def _tokenize(name: str) -> list:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.translate(_CHAR_MAP).casefold().replace("&", " and ")
    s = "".join(c if c.isalnum() else " " for c in s)
    return s.split()


def normalize_issuer_tokens(name: str) -> list:
    """Identity-bearing tokens of a company name, in order.

    Single letters go too (share classes, truncation stubs like the final "R"
    of "LABORATORIOS FARMACEUTICOS R"). If stripping would remove everything,
    the unstripped tokens are returned instead — a guard must never compare
    two empty strings and call that a match.
    """
    raw = _tokenize(name)
    kept = [t for t in raw if t not in _NON_IDENTITY_TOKENS and len(t) > 1]
    return kept if kept else raw


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(c in it for c in needle)


def _vowelless_abbrev_match(a: str, b: str) -> bool:
    """Vendor names sometimes squeeze the vowels out of long words:
    "Charles River Lbrtrs ntrntl Inc" (a real universe row). A token with NO
    vowels that is a subsequence of the other token, first letters matching,
    is that abbreviation — narrow on purpose, so "soon"/"sonova" and
    "gift"/"genfit" (which keep their vowels) can never ride this rule."""
    for abbrev, full in ((a, b), (b, a)):
        if (len(abbrev) >= _PREFIX_MIN_CHARS and abbrev[0] == full[0]
                and not any(v in abbrev for v in "aeiou")
                and _is_subsequence(abbrev, full)):
            return True
    return False


def _tokens_equal(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < _PREFIX_MIN_CHARS:
        return False
    if a.startswith(b) or b.startswith(a):
        return True
    return _vowelless_abbrev_match(a, b)


def _covered(smaller: list, larger: list) -> bool:
    return all(any(_tokens_equal(t, u) for u in larger) for t in smaller)


def issuer_names_match(stored_name: str, figi_name: str) -> bool:
    """True when the two names plausibly identify the same issuer."""
    a = normalize_issuer_tokens(stored_name)
    b = normalize_issuer_tokens(figi_name)
    if not a or not b:
        return False
    if _covered(a, b) or _covered(b, a):
        return True
    return SequenceMatcher(None, " ".join(a), " ".join(b)).ratio() >= _RATIO_FLOOR


def check_isin_identity(company_name, names) -> IsinIdentityResult:
    """Pure verdict from a stored name and the ISIN's OpenFIGI names.

    ``names`` is the `fetch_isin_names` value for the ISIN: a list of issuer
    names, ``[]`` when OpenFIGI has no coverage, ``None`` when the lookup
    failed in transit.
    """
    if names is None:
        return IsinIdentityResult(VERDICT_INCONCLUSIVE, "openfigi-unreachable")
    stored = str(company_name or "").strip()
    if not stored:
        return IsinIdentityResult(VERDICT_INCONCLUSIVE, "no-company-name",
                                  tuple(names))
    if not names:
        return IsinIdentityResult(VERDICT_INCONCLUSIVE, "no-openfigi-coverage")
    if any(issuer_names_match(stored, n) for n in names):
        return IsinIdentityResult(VERDICT_OK, "issuer-name-match", tuple(names))
    return IsinIdentityResult(VERDICT_CONFLICT, "issuer-name-mismatch",
                              tuple(names))


# ── OpenFIGI client (edge) ───────────────────────────────────────────────────


def _load_cache(cache_path: Path) -> dict:
    try:
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("ISIN-name cache unreadable (%s) — refetching", e)
    return {}


def _save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_name(cache_path.name + ".tmp.json")
    tmp.write_text(json.dumps(cache, indent=1, ensure_ascii=True),
                   encoding="utf-8")
    os.replace(tmp, cache_path)


def _post_batch(post, payload):
    """One mapping request with a single 429 retry. Returns list | None."""
    resp = post(OPENFIGI_URL, json=payload, timeout=30,
                headers={"Content-Type": "application/json"})
    if resp.status_code == 429:
        logger.warning("OpenFIGI rate-limited; backing off %.0fs",
                       RATE_LIMIT_BACKOFF_SECONDS)
        return "RETRY"  # caller sleeps with ITS injected clock, then retries
    if resp.status_code != 200:
        logger.warning("OpenFIGI HTTP %s on ISIN mapping batch", resp.status_code)
        return None
    return resp.json()


def _fetch_isin_records(isins, *, use_cache=True, cache_path=None, post=None,
                        sleep=time.sleep, need="names"):
    """Core OpenFIGI mapping loop.

    Returns ``{isin: {"names": [...], "types": [...]} | None}`` — ``None`` means
    the lookup failed in transit (transient, NEVER cached).

    ``need`` names the cache field the caller actually requires. A cached entry
    that predates that field counts as a **miss**, so an older cache cannot
    answer a newer question with silence: the name cache shipped 2026-07-28,
    ``securityType2`` was only captured on 2026-07-31, and every pre-existing
    entry would otherwise have reported "no security type" — which reads exactly
    like "OpenFIGI has no coverage" and is the module's own founding mistake.
    """
    cache_path = Path(cache_path) if cache_path else CACHE_PATH
    post = post or requests.post
    cache = _load_cache(cache_path)

    unique = list(dict.fromkeys(str(i).strip() for i in isins if str(i).strip()))
    out = {}
    to_fetch = []
    for isin in unique:
        if use_cache and isin in cache and need in cache[isin]:
            out[isin] = {"names": list(cache[isin].get("names", [])),
                         "types": list(cache[isin].get("types", []))}
        else:
            to_fetch.append(isin)

    wrote = False
    batches = [to_fetch[i:i + BATCH_SIZE]
               for i in range(0, len(to_fetch), BATCH_SIZE)]
    for bi, batch in enumerate(batches):
        payload = [{"idType": "ID_ISIN", "idValue": isin} for isin in batch]
        data = None
        try:
            data = _post_batch(post, payload)
            if data == "RETRY":
                sleep(RATE_LIMIT_BACKOFF_SECONDS)
                data = _post_batch(post, payload)
                if data == "RETRY":
                    data = None
        except Exception as e:
            logger.warning("OpenFIGI ISIN batch failed: %s", _ascii(e))
            data = None

        if data is None or len(data) != len(batch):
            if data is not None:
                logger.warning("OpenFIGI returned %d entries for %d jobs — "
                               "discarding batch (position is never the key)",
                               len(data), len(batch))
            for isin in batch:
                out[isin] = None          # transient: uncached
        else:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for isin, entry in zip(batch, data):
                rows = entry.get("data", [])
                names = sorted({(d.get("name") or "").strip() for d in rows} - {""})
                # securityType2 is the coarse instrument class -- "Depositary
                # Receipt" vs "Common Stock" -- which is exactly the question
                # `Instrument Type` asks. Kept as a SET because one ISIN maps to
                # many FIGIs (one per venue) and they must agree for the answer
                # to be usable; disagreement is a finding, not a coin toss.
                types = sorted({(d.get("securityType2") or "").strip() for d in rows} - {""})
                out[isin] = {"names": names, "types": types}
                cache[isin] = {"names": names, "types": types, "fetched_at": now}
                wrote = True

        if bi + 1 < len(batches):
            sleep(BATCH_SLEEP_SECONDS)

    if wrote:
        _save_cache(cache_path, cache)
    return out


def fetch_isin_names(isins, *, use_cache=True, cache_path=None, post=None,
                     sleep=time.sleep):
    """Map ISINs to their OpenFIGI issuer names.

    Returns ``{isin: list_of_names | [] | None}`` — ``[]`` means OpenFIGI
    explicitly knows nothing (deterministic, cached), ``None`` means the
    lookup failed in transit (transient, NEVER cached, so the next run
    retries instead of trusting a blackout).
    """
    recs = _fetch_isin_records(isins, use_cache=use_cache, cache_path=cache_path,
                               post=post, sleep=sleep, need="names")
    return {k: (None if v is None else v["names"]) for k, v in recs.items()}


def fetch_isin_security_types(isins, *, use_cache=True, cache_path=None,
                              post=None, sleep=time.sleep):
    """Map ISINs to their OpenFIGI ``securityType2`` values.

    Same three-state contract as :func:`fetch_isin_names`: ``[]`` is an
    authoritative "no coverage", ``None`` is a transient failure.
    """
    recs = _fetch_isin_records(isins, use_cache=use_cache, cache_path=cache_path,
                               post=post, sleep=sleep, need="types")
    return {k: (None if v is None else v["types"]) for k, v in recs.items()}


def verify_isin_identity(isin, company_name, *, use_cache=True, fetch=None):
    """Single-ISIN identity verdict; the enrich write path's entry point."""
    fetch = fetch or fetch_isin_names
    isin = str(isin or "").strip()
    if not isin:
        return IsinIdentityResult(VERDICT_INCONCLUSIVE, "no-isin")
    names = fetch([isin], use_cache=use_cache).get(isin)
    result = check_isin_identity(company_name, names)
    if result.verdict != VERDICT_OK:
        logger.warning(
            "ISIN identity %s for %s (%s): %s%s", result.verdict,
            _ascii(company_name) or "?", isin, result.reason,
            " — OpenFIGI says: " + _ascii("; ".join(result.openfigi_names))
            if result.openfigi_names else "")
    return result


# ── whole-universe audit (read-only) ─────────────────────────────────────────


def audit_universe(df=None, *, tickers=None, sample=None, use_cache=True,
                   fetch=None):
    """Run the identity check over every universe row that carries an ISIN.

    Read-only; never writes the CSV. Returns a dict with ``ok`` /
    ``conflicts`` / ``inconclusive`` row lists — conflicts are evidence for a
    human (JP) to rule on, mirroring how the first seven corrections were
    approved, never auto-applied.
    """
    if df is None:
        from ticker_utils import read_universe_csv
        df = read_universe_csv()
    fetch = fetch or fetch_isin_names

    rows = []
    for _, row in df.iterrows():
        isin = str(row.get("ISIN", "") or "").strip()
        if not isin:
            continue
        ticker = str(row.get("Ticker", "") or "").strip()
        if tickers and ticker not in tickers:
            continue
        rows.append({
            "ticker": ticker,
            "company": str(row.get("Company Name", "") or "").strip(),
            "isin": isin,
            "country_hq": str(row.get("Country (HQ)", "") or "").strip(),
            "country_listing": str(row.get("Country (Listing)", "") or "").strip(),
        })
    no_isin = int(len(df) - len(rows))  # before any --sample truncation
    if sample:
        rows = rows[:sample]

    names_map = fetch([r["isin"] for r in rows], use_cache=use_cache)

    result = {"checked": len(rows), "ok": 0, "conflicts": [],
              "inconclusive": [], "no_isin": no_isin}
    for r in rows:
        verdict = check_isin_identity(r["company"], names_map.get(r["isin"]))
        r["reason"] = verdict.reason
        r["openfigi_names"] = list(verdict.openfigi_names)
        if verdict.verdict == VERDICT_OK:
            result["ok"] += 1
        elif verdict.verdict == VERDICT_CONFLICT:
            result["conflicts"].append(r)
        else:
            result["inconclusive"].append(r)
    return result


def write_report(result, reports_dir=None, run_date=None):
    """Markdown evidence file: reports/isin_identity_<date>.md."""
    reports_dir = Path(reports_dir) if reports_dir else config.REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = run_date or date.today().isoformat()
    path = reports_dir / f"isin_identity_{run_date}.md"

    lines = [
        f"# ISIN -> issuer-name identity audit — {run_date}",
        "",
        f"Checked {result['checked']} rows with ISINs "
        f"({result['no_isin']} rows carry none). "
        f"ok: {result['ok']} · conflicts: {len(result['conflicts'])} · "
        f"inconclusive: {len(result['inconclusive'])}.",
        "",
        "A **conflict** means OpenFIGI says the stored ISIN belongs to a "
        "different issuer than the row's Company Name. Evidence for a human "
        "call — never auto-applied. Same-issuer different-listing lines "
        "(GDRs, Canadian CDRs) pass this check by design; that class is "
        "`crosscheck-foreign`'s listing-mismatch.",
    ]
    if result["conflicts"]:
        lines += ["", "## Conflicts — the ISIN identifies someone else", "",
                  "| Ticker | Company (stored) | ISIN | OpenFIGI says |",
                  "|---|---|---|---|"]
        for r in result["conflicts"]:
            lines.append("| {} | {} | {} | {} |".format(
                r["ticker"], r["company"], r["isin"],
                "; ".join(r["openfigi_names"])))
    if result["inconclusive"]:
        lines += ["", "## Inconclusive — nothing was learned (NOT clean)", "",
                  "| Ticker | Company (stored) | ISIN | Reason |",
                  "|---|---|---|---|"]
        for r in result["inconclusive"]:
            lines.append("| {} | {} | {} | {} |".format(
                r["ticker"], r["company"], r["isin"], r["reason"]))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(*, tickers=None, sample=None, use_cache=True):
    result = audit_universe(tickers=tickers, sample=sample, use_cache=use_cache)
    report = write_report(result)
    result["report_path"] = str(report)
    return result
