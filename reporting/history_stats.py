"""Pure stats helpers for 5-year valuation history.

None-safe: all functions tolerate any mix of None / NaN values in the input.
Used by `reporting/generate.py` to enrich Phase 1 (Portfolio ∪ Researching)
rows with avg / +1σ / -1σ / min / max / vs-avg-pct columns.
"""

import math


def _clean(values):
    """Drop None / NaN / non-numeric entries."""
    out = []
    for v in values or []:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f):
            continue
        out.append(f)
    return out


def _mean(values):
    if not values:
        return None
    return sum(values) / len(values)


def _stdev(values):
    """Sample standard deviation (n-1 denominator). Returns None for n<2."""
    if len(values) < 2:
        return None
    m = _mean(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def stats_from_series(values, current=None):
    """Return summary stats over a numeric series, with current-vs-avg %.

    Args:
        values: iterable of historical values (None / NaN tolerated, ignored).
        current: optional float — the live TTM value to compare against the
                 historical average. None → vs_avg_pct returned as None.

    Returns dict with keys: avg, plus_1sd, minus_1sd, min, max, vs_avg_pct.
    Each value is float or None.
    """
    cleaned = _clean(values)
    avg = _mean(cleaned)
    sd = _stdev(cleaned)

    if avg is None:
        plus_1sd = None
        minus_1sd = None
    elif sd is None:
        plus_1sd = None
        minus_1sd = None
    else:
        plus_1sd = avg + sd
        minus_1sd = avg - sd

    lo = min(cleaned) if cleaned else None
    hi = max(cleaned) if cleaned else None

    vs_avg_pct = None
    if current is not None and avg not in (None, 0):
        try:
            vs_avg_pct = (float(current) - avg) / avg * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            vs_avg_pct = None

    return {
        "avg": avg,
        "plus_1sd": plus_1sd,
        "minus_1sd": minus_1sd,
        "min": lo,
        "max": hi,
        "vs_avg_pct": vs_avg_pct,
    }
