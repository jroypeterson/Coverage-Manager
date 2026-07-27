"""Detect delisted, acquired, or recycled tickers in the coverage universe.

For each ticker in the universe CSV, fetch a lightweight identity probe from
yfinance (`quoteType`, `longName`, `shortName`) and compare against the
universe-recorded `Company Name`. A meaningful mismatch suggests the ticker
has been recycled (e.g. an operating company was acquired/de-listed and the
symbol is now used by an ETF or another issuer).

In addition to the `.info` identity probe, a **price-recency probe** checks
whether yfinance still serves recent daily bars for the ticker. This is the
reliable tell for a clean acquisition/take-private: Yahoo keeps the stale
`.info` metadata (longName etc.) populated for months after a name stops
trading, so the identity probe alone misses these — but the price feed goes
empty immediately. A ticker with a populated `.info` and zero recent bars is
flagged `no recent price data (likely delisted/renamed)`. This is what would
have caught EXAS (Abbott, 2026-03), HOLX (Blackstone/TPG, 2026-04), and the
MPW→MPT / GMRE→XRN rebrands instead of letting them linger in the universe.

**A failed lookup is not a delisting** (fixed 2026-07-25). Every probe has three
possible outcomes, not two: the ticker looks dead, the ticker looks alive, or
*we could not find out*. Collapsing the third into the first is what made the
2026-07-25 run report 58 flags when independent quotes showed `ACLX` trading at
$115.07 on NASDAQ with 13.2M shares of volume. The same run recorded 53
price-probe failures out of 1,093 names — Yahoo was throttling, and throttling
was being written down as death. So the check now reports `inconclusive`
separately and never lets it reach the flagged list. See `_classify`.

Output:
  - `reports/delisted_check_{date}.csv`  — flagged rows with reason
  - `reports/delisted_check_{date}.md`   — human-readable summary

Flagged tickers stay in the universe — this is a non-gating warning. The user
moves them to `data/delisted_tickers.csv` manually after confirming.
"""

import csv
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from difflib import SequenceMatcher

import pandas as pd

from cache import cache_get, cache_set
from config import CSV_PATH, REPORTS_DIR
from logging_utils import get_logger, log_exception
from ticker_utils import normalize_company_for_comparison, normalize_ticker

logger = get_logger("delisted_check")

# v2: added price-recency fields (last_close_date / price_probe_ran / price_stale).
# v3: added `info_ok`. The bump is REQUIRED, not hygiene -- a v2 entry has no
# `info_ok`, which reads as False, so a cached "identity empty + price feed
# dead" row (a genuine delisting, both signals agreeing) would be downgraded to
# `inconclusive` for up to a full TTL. Bumping the namespace ignores older
# entries so the new logic sees real data on the next run.
IDENTITY_CACHE_NS = "identity_v3"
IDENTITY_CACHE_TTL_HOURS = 24.0 * 7  # weekly refresh is enough

# quoteType values from yfinance that should never appear in the equity universe
NON_EQUITY_QUOTE_TYPES = {"ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY"}

# Below this normalized-name similarity score, flag as a likely mismatch
NAME_SIMILARITY_THRESHOLD = 0.55

# A live ticker always has a daily bar within the last few trading sessions.
# If yfinance serves no bar within this many calendar days, treat the price
# feed as dead (acquired / taken private / renamed). 10 days clears a normal
# long weekend / holiday cluster with margin while still catching a stop in
# trading promptly on the weekly cadence.
PRICE_STALE_DAYS = 10

# Per-ticker verdicts. A probe has THREE outcomes, not two — the third is the
# whole point of this module's 2026-07-25 fix, so it gets a name rather than
# living as a special case of "flagged".
VERDICT_CLEAN = "clean"
VERDICT_FLAGGED = "flagged"
#: We could not find out. Never appears in the flagged list; reported apart.
VERDICT_INCONCLUSIVE = "inconclusive"

# Share of tickers whose lookup failed outright, above which the whole run is
# reported degraded. Yahoo throttles bursty traffic, and a throttled run's
# *flags* are also less trustworthy — a reader needs to see that in the report
# rather than infer it. 2% is well clear of the odd one-off failure and well
# below the 4.8% (53/1093) seen when Yahoo was actually rate-limiting us.
DEGRADED_FAILURE_RATE = 0.02

# Share of the universe returning "no price history at all" above which the
# answer stops being believable as a property of the UNIVERSE and starts being a
# property of the RUN. A few dozen symbols genuinely have no yfinance history;
# a fifth of the coverage list does not. Because `no_data` is deliberately
# excluded from the transport-failure rate above (it is stable, and counting it
# would pin `degraded` on forever), a systemic fault that manifests AS `no_data`
# -- a Yahoo API change, an auth/endpoint break -- would otherwise sail through
# reporting zero flags and a green heartbeat. This is the backstop for that.
IMPLAUSIBLE_NO_DATA_RATE = 0.20

# --- Yahoo rate limiting ----------------------------------------------------
#
# The 2026-07-26 cold run lost 518 of 1,093 price probes and 488 `.info` calls
# to "Too Many Requests". The three-state verdict meant that degraded into 502
# honest `inconclusive` rows rather than 502 false delistings — but honest and
# useless is still useless. Yahoo has no published quota and no Retry-After
# header, so the only workable strategy is to notice refusal and back off.
RETRY_ATTEMPTS = 4
RETRY_BASE_SECONDS = 2.0
RETRY_MAX_SECONDS = 60.0
#: yfinance raises plain exceptions; rate limiting is only identifiable by text.
#: A bare "429" is deliberately NOT a marker -- it matches that digit string
#: anywhere in a message (an identifier, a price, a share count), costing a full
#: retry-and-backoff cycle on an error that will never clear.
_RATE_LIMIT_MARKERS = ("too many requests", "rate limit", "yfratelimit")
#: HTTP status forms, matched with enough context to actually mean a status code.
_RATE_LIMIT_STATUS = ("429 ", "429:", "status 429", "http 429", "error 429")

#: Indirection so tests exercise the backoff logic without actually sleeping.
#: The CLOCK is injectable too, not just the sleep: `wait()` re-checks the
#: deadline in a loop (it must, since another thread can extend the cooldown
#: mid-wait), so a no-op sleep against a real clock would busy-spin for the
#: full backoff instead of skipping it.
_sleep = time.sleep
_now = time.monotonic


def _is_rate_limited(exc) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return True
    return text.endswith("429") or any(m in text for m in _RATE_LIMIT_STATUS)


class _Throttle:
    """Process-wide backoff gate shared by every worker thread.

    Per-thread retry is not enough. When Yahoo starts refusing, the other
    workers keep hammering it, so each thread's private backoff expires into a
    server that is still angry and the run never recovers — which is exactly
    what the 2026-07-26 run did. One thread hitting a 429 therefore pauses ALL
    of them, and the delay escalates per trip and decays on success, so the run
    finds a sustainable rate by itself instead of guessing one up front.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._resume_at = 0.0
        self._level = 0
        self.trips = 0

    def wait(self):
        """Block until the shared cooldown has elapsed."""
        while True:
            with self._lock:
                remaining = self._resume_at - _now()
            if remaining <= 0:
                return
            _sleep(min(remaining, 1.0))

    def trip(self) -> float:
        """Record a rate-limit rejection; returns the new cooldown in seconds."""
        with self._lock:
            self._level += 1
            delay = min(RETRY_BASE_SECONDS * (2 ** (self._level - 1)),
                        RETRY_MAX_SECONDS)
            # Jitter, so the workers don't resume in lockstep and re-trip together.
            delay += random.uniform(0.0, delay * 0.25)
            self._resume_at = max(self._resume_at, _now() + delay)
            self.trips += 1
            return delay

    def relax(self):
        """A success: ease off one level so the run speeds back up."""
        with self._lock:
            if self._level:
                self._level -= 1

    def reset(self):
        with self._lock:
            self._resume_at, self._level, self.trips = 0.0, 0, 0


_THROTTLE = _Throttle()


#: Third `ok` value from `_with_retry`: the call answered, and the answer was
#: "I have nothing for this symbol". Distinct from both success and failure.
_NO_DATA = "no_data"


def _with_retry(fn, label, no_data_markers=()):
    """Call `fn`, retrying through Yahoo rate limiting.

    Returns `(value, ok)` where `ok` is True, False, or `_NO_DATA` when the
    exception text matches `no_data_markers` — an authoritative-sounding "no
    data" reply, which the caller must decide how much to believe.

    Only rate-limit errors are retried — a "no data" answer for a symbol is a
    real reply, and retrying it would just burn the budget that the throttled
    names need.
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        _THROTTLE.wait()
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 - yfinance raises bare Exceptions
            if _is_rate_limited(exc):
                if attempt >= RETRY_ATTEMPTS:
                    # Trip on the way out too. A fourth consecutive 429 is the
                    # STRONGEST evidence Yahoo is still refusing; returning
                    # without extending the cooldown sends the next ticker
                    # straight into a server we know is saying no.
                    _THROTTLE.trip()
                    logger.warning("%s: still rate limited after %d attempts",
                                   label, RETRY_ATTEMPTS)
                    return None, False
                delay = _THROTTLE.trip()
                logger.info("%s: rate limited, backing off %.1fs (attempt %d/%d)",
                            label, delay, attempt, RETRY_ATTEMPTS)
                continue
            text = str(exc).lower()
            if any(marker in text for marker in no_data_markers):
                # Answered, with nothing. Not retried, not logged as a failure.
                return None, _NO_DATA
            log_exception(logger, f"{label} failed", exc)
            return None, False
        _THROTTLE.relax()
        return value, True
    return None, False


#: One YEAR of bars, not one month. The delisting signal is a *stale* last bar,
#: and bars only stay visible while they fall inside the requested window — a
#: name acquired three months ago returns nothing at all on a 1mo pull and so is
#: invisible, whereas a 1y pull still shows its final bars and flags correctly.
#: Verified 2026-07-26: ASRT (Zydus, delisted 2026-06-16) returns 3 bars on 1mo
#: and 9 on 1y, both ending 2026-06-29 — the wider window extends the detection
#: horizon from ~1 month to ~1 year at the cost of one request either way.
PROBE_PERIOD = "1y"

#: Price-probe outcomes. `no_data` is deliberately NOT merged into either
#: neighbour: it is an ANSWER (Yahoo replied) but not EVIDENCE (see below).
PRICE_OK = "ok"
PRICE_NO_DATA = "no_data"
PRICE_FAILED = "failed"


def _probe_recent_price(yf_obj):
    """Return (status, last_close_date) for a yfinance Ticker.

    `status` is PRICE_OK (bars returned), PRICE_NO_DATA (Yahoo answered but
    serves no bars for this symbol) or PRICE_FAILED (rate-limited or errored).

    **`no_data` is not evidence of a delisting.** yfinance raises
    `YFPricesMissingError` — "possibly delisted; no price data found" — for
    symbols that are trading perfectly well: verified 2026-07-26 that `ACLX`
    raises it on both a 1mo and a 1y window while quoting $115.07 on NASDAQ with
    13.2M shares of volume. Yahoo's own wording is simply wrong, and believing it
    is what produced 58 false flags. Only a *stale last bar* — real bars, ending
    too long ago — supports a delisting claim, so `no_data` is kept as its own
    outcome and never reaches a flag.

    Separating it from PRICE_FAILED matters operationally too: `no_data` is a
    stable property of a few dozen names, so counting it as a run failure would
    hold the degraded flag permanently on and train the reader to ignore it.
    """
    hist, ok = _with_retry(
        lambda: yf_obj.history(period=PROBE_PERIOD, auto_adjust=True,
                               raise_errors=True),
        "price probe",
        no_data_markers=("no price data found", "possibly delisted"),
    )
    if ok is _NO_DATA:
        return PRICE_NO_DATA, ""
    if not ok:
        return PRICE_FAILED, ""  # transient — do NOT treat as delisted
    try:
        # An EMPTY FRAME IS ANOMALOUS, not authoritative. With
        # `raise_errors=True`, yfinance signals a missing feed by RAISING -- so
        # a silent empty result means the error path is not behaving as assumed.
        # yfinance 1.4.1 already deprecates `raise_errors`; if it ever becomes
        # accepted-but-ignored, every throttled response arrives here as an
        # empty frame. Calling that `no_data` would cache it for a week, keep it
        # out of the degraded count, and report a green heartbeat for a run
        # that checked nothing. Treating it as a transport failure means that
        # scenario shows up as a loud, uncached, degraded run instead.
        if hist is None or hist.empty or "Close" not in hist:
            return PRICE_FAILED, ""
        closes = hist["Close"].dropna()
        if closes.empty:
            return PRICE_FAILED, ""
        return PRICE_OK, closes.index[-1].date().isoformat()
    except Exception:
        return PRICE_FAILED, ""


def _fetch_identity(yf_ticker, use_cache=True):
    """Fetch identity + price-recency probe for a single ticker from yfinance.

    Returns dict with {quoteType, longName, shortName, last_close_date,
    info_ok, price_probe_ran, price_stale}.

    The two probes are fetched INDEPENDENTLY. A `.info` call that raises used to
    abort the whole function and return `{}`, discarding a price probe that
    would have succeeded — and `{}` was then classified as "likely delisted".
    That is precisely how a $115 NASDAQ name with 13M shares of daily volume got
    reported as delisted: `.info` was throttled, and nothing else was allowed to
    speak for the ticker. `info_ok` records whether the metadata call actually
    answered, so an empty-because-throttled `.info` is never mistaken for an
    empty-because-the-company-is-gone one.

    Staleness is decided HERE, at probe time, and frozen into `price_stale` —
    not recomputed at classify time. The identity cache (7-day TTL) would
    otherwise let a cached `last_close_date` "age into" staleness and falsely
    flag a live ticker that was fresh when probed.
    """
    if use_cache:
        cached = cache_get(IDENTITY_CACHE_NS, yf_ticker, IDENTITY_CACHE_TTL_HOURS)
        if cached is not None:
            return cached

    try:
        import yfinance as yf

        yf_obj = yf.Ticker(yf_ticker)
    except Exception as e:
        log_exception(logger, f"Identity lookup failed for {yf_ticker}", e)
        return {"info_ok": False, "price_probe_ran": False, "price_stale": False,
                "quoteType": "", "longName": "", "shortName": "",
                "last_close_date": ""}

    info, info_ok = _with_retry(lambda: yf_obj.info or {},
                                f"Identity metadata for {yf_ticker}")
    if not info_ok:
        info = {}

    price_status, last_close_date = _probe_recent_price(yf_obj)
    identity = {
        "quoteType": info.get("quoteType") or "",
        "longName": info.get("longName") or "",
        "shortName": info.get("shortName") or "",
        "last_close_date": last_close_date,
        "info_ok": info_ok,
        "price_status": price_status,
        # Back-compat for cached v3 payloads and existing readers.
        "price_probe_ran": price_status == PRICE_OK,
        # frozen-at-probe-time decision (avoids the cache-aging trap)
        "price_stale": bool(price_status == PRICE_OK
                            and _price_is_stale(last_close_date)),
    }
    # Do NOT cache a TRANSIENT failure of either probe -- that would disable the
    # check for this ticker for a full TTL on the strength of a network blip. A
    # `no_data` answer IS cacheable: it is a stable property of the symbol, and
    # re-probing it every run buys nothing but rate-limit pressure.
    if use_cache and info_ok and price_status in (PRICE_OK, PRICE_NO_DATA):
        cache_set(IDENTITY_CACHE_NS, yf_ticker, identity)
    return identity


def _name_similarity(recorded_name, yf_long, yf_short):
    """Best similarity ratio between recorded name and yfinance long/short names.

    Returns **None** when there is nothing to compare — no recorded name, or no
    yfinance name. That is not a similarity of 0.0: a comparison that could not
    be made has no result, and scoring it 0.0 reads as "these two names are
    completely different" when in truth only one name exists. Caught live on
    2026-07-25, where `ACLX` came back with a `quoteType` but empty
    `longName`/`shortName` and was flagged `name mismatch (similarity=0.00),
    yfinance=''` — a disagreement with an empty string.

    Both names are normalized (drop Inc/Corp/etc.) so corp-suffix differences
    don't trigger false positives.
    """
    recorded = normalize_company_for_comparison(recorded_name)
    if not recorded:
        return None  # nothing recorded to compare against

    best = None
    for yf_name in (yf_long, yf_short):
        if not yf_name:
            continue
        candidate = normalize_company_for_comparison(yf_name)
        if not candidate:
            continue
        score = SequenceMatcher(None, recorded, candidate).ratio()
        # Substring match gets a floor of 0.85 so e.g.
        # "premier" vs "premier inc holdings" doesn't get penalized.
        if recorded in candidate or candidate in recorded:
            score = max(score, 0.85)
        best = score if best is None else max(best, score)
    return best


def _price_is_stale(last_close_date, today=None):
    """True when last_close_date is older than PRICE_STALE_DAYS (or missing)."""
    if not last_close_date:
        return True
    try:
        last = date.fromisoformat(last_close_date)
    except (ValueError, TypeError):
        return True
    today = today or date.today()
    return (today - last).days > PRICE_STALE_DAYS


def _classify(row, identity):
    """Return (verdict, reason) for a single ticker.

    `verdict` is one of VERDICT_CLEAN / VERDICT_FLAGGED / VERDICT_INCONCLUSIVE.

    **The three-state result is the point.** "yfinance told us nothing" and
    "yfinance couldn't be reached" produce identical-looking data, and treating
    them alike is a claim the evidence does not support: an absent answer is
    absent for exactly one of those two reasons, and only one of them is a
    delisting. Every rule below therefore checks whether a probe actually ran
    before reading anything into what it returned.

    Rules, in order:
      Nothing answered            => INCONCLUSIVE (we did not find out).
      No metadata, price is live  => CLEAN (it trades; the vendor's metadata is
                                    simply missing — recorded, not flagged).
      No metadata, no price feed  => FLAGGED, both signals agreeing.
      Price feed gone stale       => FLAGGED 'no recent price data'.
      Non-equity quoteType        => FLAGGED 'recycled to {ETF|MUTUALFUND|...}'.
      Low name similarity         => FLAGGED 'name mismatch'.
      Otherwise                   => CLEAN.

    The price-recency rule sits above the name-similarity rule because a clean
    acquisition keeps `.info` (and thus the name match) intact for months; the
    dead price feed is the earlier, more reliable signal. `price_stale` is the
    decision frozen at probe time (see `_fetch_identity`), so a stale read is
    never an artifact of cache age.
    """
    quote_type = (identity.get("quoteType") or "").upper()
    long_name = identity.get("longName") or ""
    short_name = identity.get("shortName") or ""
    recorded_name = str(row.get("Company Name", "") or "")
    info_ok = bool(identity.get("info_ok"))
    price_status = identity.get("price_status") or (
        PRICE_OK if identity.get("price_probe_ran") else PRICE_FAILED)
    probe_ran = price_status == PRICE_OK
    has_identity = bool(quote_type or long_name or short_name)

    # A stale last bar is decidable from the price probe ALONE, so it is checked
    # before anything conditioned on `.info`. Previously a throttled `.info`
    # discarded a perfectly good stale-price answer and reported the price feed
    # "inconclusive" when it had in fact answered -- which is the EXAS case the
    # module exists for.
    if identity.get("price_stale"):
        last_seen = identity.get("last_close_date") or "never"
        return VERDICT_FLAGGED, (
            f"no recent price data (likely delisted/renamed, or extended halt); "
            f"last bar={last_seen}"
        )

    if not identity or (not info_ok and price_status == PRICE_FAILED):
        # Neither probe answered. This is the absence of evidence, not evidence
        # of absence -- the distinction the pre-2026-07-25 code collapsed.
        return VERDICT_INCONCLUSIVE, (
            "yfinance lookup failed (both metadata and price probe); "
            "no evidence either way"
        )

    if not has_identity:
        if not info_ok:
            # The metadata call itself failed, so its emptiness says nothing.
            if probe_ran and not identity.get("price_stale"):
                return VERDICT_CLEAN, (
                    f"metadata lookup failed but price feed is live "
                    f"(last bar={identity.get('last_close_date') or 'unknown'})"
                )
            return VERDICT_INCONCLUSIVE, (
                "metadata lookup failed; price feed inconclusive"
            )
        if probe_ran and not identity.get("price_stale"):
            # Answered, and answered empty -- but the thing demonstrably trades.
            # A vendor metadata gap, not a delisting.
            return VERDICT_CLEAN, (
                f"no yfinance identity metadata, but price feed is live "
                f"(last bar={identity.get('last_close_date') or 'unknown'})"
            )
        # No metadata AND no usable price feed. Tempting to call this delisted --
        # two signals agreeing! -- but they are not independent: ACLX returns
        # neither, and trades at $115. Both being empty means we learned nothing.
        return VERDICT_INCONCLUSIVE, (
            f"no yfinance identity metadata and no price data "
            f"(price probe: {price_status}); no evidence either way"
        )

    if quote_type in NON_EQUITY_QUOTE_TYPES:
        return VERDICT_FLAGGED, f"ticker recycled to non-equity instrument ({quote_type})"

    if quote_type and quote_type not in {"EQUITY", "ADR", ""}:
        # Surface any other unexpected types but don't hard-flag
        pass

    score = _name_similarity(recorded_name, long_name, short_name)
    if score is None:
        # A quoteType but no name. Nothing to compare, so no mismatch can be
        # asserted -- surfaced as a vendor gap instead of invented as a finding.
        return VERDICT_CLEAN, (
            f"yfinance returned no company name to compare "
            f"(quoteType={quote_type or 'none'}); identity rule skipped"
        )
    if score < NAME_SIMILARITY_THRESHOLD:
        return VERDICT_FLAGGED, (
            f"company name mismatch (similarity={score:.2f}); recorded="
            f"{recorded_name!r}, yfinance={long_name or short_name!r}"
        )

    if not probe_ran:
        # LAST, deliberately: every metadata-only rule above (recycled quoteType,
        # name mismatch) is decidable without the price feed and keeps its flag.
        # What is left is "metadata looks fine" -- and the price feed is the ONLY
        # signal that catches a clean acquisition, because Yahoo keeps `.info`
        # (and therefore the name match) intact for months afterwards. With no
        # usable price feed we did not check the one thing that would have found
        # it, so calling this clean asserts a check we never ran.
        if price_status == PRICE_NO_DATA:
            return VERDICT_INCONCLUSIVE, (
                "identity looks unchanged, but yfinance serves no price history "
                "for this symbol at all - not evidence of a delisting (ACLX "
                "does the same while trading normally), and re-running will not "
                "resolve it; confirm against a second source"
            )
        return VERDICT_INCONCLUSIVE, (
            "identity looks unchanged but the price probe failed; the delisting "
            "signal was not checked"
        )

    return VERDICT_CLEAN, ""


def check_universe(csv_path=None, max_workers=4, use_cache=True):
    """Run the delisted/recycled check across the full universe CSV.

    Returns dict with keys:
      - flagged: list of dicts with ticker/recorded_name/quoteType/yf_name/reason
      - inconclusive: same shape, for tickers we could not find out about. These
        are NOT delisting candidates and must never be merged into `flagged`.
      - metadata_gaps: same shape, for tickers that trade fine but whose vendor
        identity metadata came back empty (reported so the gap isn't silent)
      - checked: total tickers checked
      - missing_data: count of tickers yfinance returned no identity for
      - price_probe_failures: count of tickers whose price probe raised
      - info_failures: count of tickers whose `.info` call raised
      - rate_limit_trips: how many times Yahoo refused and the shared throttle
        backed off — the direct measure of how hard we were being rejected
      - degraded: True when the failure rate exceeds DEGRADED_FAILURE_RATE — the
        run hit Yahoo throttling and even its flags deserve less trust

    max_workers defaults to 4 (was 6, originally 10). The run pulls 1mo of
    history per ticker in addition to `.info`, and Yahoo rate-limits bursty
    traffic hard: a cold full-universe pass at 6 lost ~half its probes. Lowering
    the ceiling is only half the answer — `_Throttle` adapts the real rate
    downward when Yahoo pushes back, so the run self-paces rather than relying
    on a constant guessed here. A cold pass is consequently SLOW by design;
    correctness of the verdict matters more than the wall clock on a weekly job.
    """
    csv_path = csv_path or CSV_PATH
    df = pd.read_csv(csv_path)

    rows = df.to_dict(orient="records")
    logger.info("Probing yfinance identity for %d tickers...", len(rows))

    pairs = []
    for row in rows:
        yf_t = normalize_ticker(
            row.get("Ticker", ""),
            company_name=row.get("Company Name", ""),
            exchange=row.get("Exchange", ""),
        )
        if not yf_t:
            continue
        pairs.append((row, yf_t))

    identities = {}
    _THROTTLE.reset()

    def _fetch_one(yf_t):
        return yf_t, _fetch_identity(yf_t, use_cache=use_cache)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_fetch_one, yf_t) for _, yf_t in pairs]
        for i, fut in enumerate(as_completed(futures), start=1):
            yf_t, identity = fut.result()
            identities[yf_t] = identity
            if i % 100 == 0:
                logger.info("  progress: %d/%d", i, len(pairs))

    flagged = []
    inconclusive = []
    metadata_gaps = []
    missing_identity = 0
    price_no_data = 0
    price_probe_failures = 0
    info_failures = 0
    any_transport_failure = 0
    for row, yf_t in pairs:
        identity = identities.get(yf_t, {})
        price_status = identity.get("price_status") or (
            PRICE_OK if identity.get("price_probe_ran") else PRICE_FAILED)
        info_ok = bool(identity.get("info_ok"))
        # Counted ONLY when `.info` actually answered. Otherwise this number
        # conflates "Yahoo has no metadata for this symbol" with "Yahoo refused
        # to talk to us", which is the exact ambiguity this module was rewritten
        # to remove -- it would just have survived inside a counter, inflating
        # during throttled runs and overlapping the transport-failure line.
        if info_ok and (
            not identity.get("quoteType")
            and not identity.get("longName")
            and not identity.get("shortName")
        ):
            missing_identity += 1
        if price_status == PRICE_NO_DATA:
            price_no_data += 1
        elif price_status == PRICE_FAILED:
            price_probe_failures += 1
        if not info_ok:
            info_failures += 1
        # TRANSPORT failures only. A `no_data` reply is an answer and a stable
        # property of a few dozen symbols; counting it here would hold `degraded`
        # permanently on against a fixed baseline, and an alarm that is always
        # lit is one the reader learns to ignore -- which defeats the point of
        # having wired it into the heartbeat at all.
        if not identity or price_status == PRICE_FAILED or not info_ok:
            any_transport_failure += 1
        verdict, reason = _classify(row, identity)
        if verdict == VERDICT_CLEAN and not reason:
            continue
        entry = {
            "ticker": row.get("Ticker", ""),
            "yf_ticker": yf_t,
            "recorded_name": row.get("Company Name", ""),
            "yf_long_name": identity.get("longName", ""),
            "yf_short_name": identity.get("shortName", ""),
            "quote_type": identity.get("quoteType", ""),
            "last_close_date": identity.get("last_close_date", ""),
            "sector_jp": row.get("Sector (JP)", ""),
            "subsector_jp": row.get("Subsector (JP)", ""),
            "reason": reason,
        }
        if verdict == VERDICT_FLAGGED:
            flagged.append(entry)
        elif verdict == VERDICT_INCONCLUSIVE:
            inconclusive.append(entry)
        else:  # clean, but carrying a note worth surfacing
            metadata_gaps.append(entry)

    for bucket in (flagged, inconclusive, metadata_gaps):
        bucket.sort(key=lambda r: r["ticker"])

    checked = len(pairs)
    # The UNION of affected tickers, not max() of the two counts. The probes fail
    # independently, so 1.5% price-only plus 1.5% metadata-only degrades 3% of
    # the universe while max() reports 1.5% and stays silent.
    degraded = bool(checked and any_transport_failure / checked > DEGRADED_FAILURE_RATE)
    no_data_implausible = bool(
        checked and price_no_data / checked > IMPLAUSIBLE_NO_DATA_RATE)
    if no_data_implausible:
        logger.warning(
            "IMPLAUSIBLE: %d/%d tickers (%.0f%%) report no price history at "
            "all. That is a property of this RUN, not of the universe - "
            "suspect a Yahoo API change or broken error handling, not a wave "
            "of delistings.", price_no_data, checked,
            100.0 * price_no_data / checked)
        degraded = True

    return {
        "checked": checked,
        "flagged": flagged,
        "inconclusive": inconclusive,
        "metadata_gaps": metadata_gaps,
        "missing_data": missing_identity,
        "price_no_data": price_no_data,
        "no_data_implausible": no_data_implausible,
        "price_probe_failures": price_probe_failures,
        "info_failures": info_failures,
        "tickers_with_a_failed_probe": any_transport_failure,
        "rate_limit_trips": _THROTTLE.trips,
        "degraded": degraded,
    }


def write_report(result, reports_dir=None, run_date=None):
    """Write CSV + markdown reports for flagged tickers.

    Returns dict of {csv_path, md_path}.
    """
    reports_dir = reports_dir or REPORTS_DIR
    run_date = run_date or date.today().strftime("%Y-%m-%d")
    reports_dir.mkdir(parents=True, exist_ok=True)

    csv_path = reports_dir / f"delisted_check_{run_date}.csv"
    md_path = reports_dir / f"delisted_check_{run_date}.md"

    fieldnames = [
        "ticker", "yf_ticker", "recorded_name", "yf_long_name", "yf_short_name",
        "quote_type", "last_close_date", "sector_jp", "subsector_jp", "reason",
    ]
    # `verdict` is written so a reader of the CSV alone can never mistake an
    # inconclusive row for a delisting candidate.
    csv_fields = ["verdict"] + fieldnames
    inconclusive = result.get("inconclusive", [])
    metadata_gaps = result.get("metadata_gaps", [])
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        for verdict, bucket in ((VERDICT_FLAGGED, result["flagged"]),
                                (VERDICT_INCONCLUSIVE, inconclusive),
                                (VERDICT_CLEAN, metadata_gaps)):
            for row in bucket:
                writer.writerow({"verdict": verdict, **row})

    def _table(rows):
        out = ["| Ticker | Recorded Name | yfinance Name | quoteType | Last Bar | Reason |",
               "|--------|---------------|---------------|-----------|----------|--------|"]
        for row in rows:
            yf_name = row["yf_long_name"] or row["yf_short_name"]
            last_bar = row.get("last_close_date") or "-"
            out.append(
                f"| {row['ticker']} | {row['recorded_name']} | {yf_name} | "
                f"{row['quote_type']} | {last_bar} | {row['reason']} |"
            )
        return out

    lines = []
    lines.append(f"# Delisted / recycled ticker check — {run_date}")
    lines.append("")
    lines.append(f"- Checked: {result['checked']} tickers")
    lines.append(f"- Flagged (likely delisted/recycled): {len(result['flagged'])}")
    lines.append(f"- Inconclusive (lookup failed — **not** delisting candidates): "
                 f"{len(inconclusive)}")
    lines.append(f"- No yfinance identity metadata: {result['missing_data']}")
    no_data = result.get("price_no_data", 0)
    if no_data:
        lines.append(
            f"- No price history at all: {no_data} — Yahoo *answered* and has "
            f"nothing for these symbols. Not evidence: `ACLX` answers the same "
            f"way while trading at $115 on NASDAQ. Re-running will not resolve "
            f"them; they need a second source."
        )
    probe_fail = result.get("price_probe_failures", 0)
    info_fail = result.get("info_failures", 0)
    if probe_fail or info_fail:
        lines.append(
            f"- Transport failures: {probe_fail} price, {info_fail} metadata "
            f"— transient Yahoo errors, not delistings; these drive the "
            f"degraded flag and a re-run should clear them"
        )
    trips = result.get("rate_limit_trips", 0)
    if trips:
        lines.append(f"- Rate-limit backoffs: {trips}")
    if result.get("degraded"):
        lines.append("")
        lines.append(
            f"> :warning: **This run was degraded.** More than "
            f"{DEGRADED_FAILURE_RATE:.0%} of lookups failed, which is the "
            f"signature of Yahoo rate-limiting rather than a wave of "
            f"delistings. Treat the flags below as provisional and re-run "
            f"before acting on any of them."
        )
    lines.append("")
    if result["flagged"]:
        lines.extend(_table(result["flagged"]))
    else:
        lines.append("_No flagged tickers — universe identity matches yfinance._")

    if inconclusive:
        lines.append("")
        lines.append("## Inconclusive — we could not find out")
        lines.append("")
        lines.append(
            "yfinance gave us nothing usable for these. That is **not** evidence "
            "of a delisting: a throttled lookup and a dead company return the "
            "same empty response, and only one of them means anything. They are "
            "listed apart so they are never actioned as delistings. Rows whose "
            "reason says *transport* failure should clear on a re-run; rows "
            "saying yfinance **serves no price history at all** will not — "
            "those need a second source, not another attempt."
        )
        lines.append("")
        lines.extend(_table(inconclusive))

    if metadata_gaps:
        lines.append("")
        lines.append("## Trading, but missing vendor identity metadata")
        lines.append("")
        lines.append(
            "These have a live price feed, so they are demonstrably not "
            "delisted — yfinance simply returned no `longName`/`quoteType`. "
            "Recorded so the vendor gap is visible rather than silent."
        )
        lines.append("")
        lines.extend(_table(metadata_gaps))

    lines.append("")
    lines.append(
        "Review flagged rows. To mark a ticker as delisted/acquired, "
        "remove it from `data/coverage_universe_tickers.csv` and append "
        "an entry to `data/delisted_tickers.csv` with last-known sector "
        "and market cap data. **Confirm against a second source first** — a "
        "flag is a prompt to look, not a finding."
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return {"csv_path": str(csv_path), "md_path": str(md_path)}


def main(use_cache=True):
    """CLI entry point: run the check and write the report."""
    result = check_universe(use_cache=use_cache)
    paths = write_report(result)
    logger.info(
        "Delisted check: %d/%d flagged, %d inconclusive "
        "(no identity: %d, probe failures: %d price / %d metadata)",
        len(result["flagged"]), result["checked"],
        len(result.get("inconclusive", [])), result["missing_data"],
        result.get("price_probe_failures", 0), result.get("info_failures", 0),
    )
    if result.get("degraded"):
        logger.warning(
            "  DEGRADED RUN: over %.0f%% of lookups failed - this is Yahoo "
            "throttling, not a delisting wave. Flags are provisional; re-run "
            "before acting on them.", DEGRADED_FAILURE_RATE * 100,
        )
    logger.info("  CSV: %s", paths["csv_path"])
    logger.info("  MD:  %s", paths["md_path"])
    if result["flagged"]:
        for row in result["flagged"][:20]:
            logger.warning(
                "  FLAG %s (recorded=%r, yf=%r, qt=%s): %s",
                row["ticker"], row["recorded_name"],
                row["yf_long_name"] or row["yf_short_name"],
                row["quote_type"], row["reason"],
            )
        if len(result["flagged"]) > 20:
            logger.warning("  ... and %d more (see report)", len(result["flagged"]) - 20)
    return result
