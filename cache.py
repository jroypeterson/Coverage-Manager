"""File-based cache for external data.

Stores JSON files in cache/<namespace>/<key>.json with timestamps.
Supports TTL-based expiry and manual clearing.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config import CACHE_DIR
from logging_utils import get_logger

logger = get_logger("cache")


def _cache_path(namespace, key):
    """Return the path for a cache entry."""
    safe_key = key.replace("/", "_").replace("\\", "_").replace(":", "_")
    return CACHE_DIR / namespace / f"{safe_key}.json"


def cache_get(namespace, key, max_age_hours):
    """Read from cache if not expired.

    Returns the cached data, or None if missing/expired/corrupt.
    """
    path = _cache_path(namespace, key)
    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)

        cached_at = datetime.fromisoformat(entry["_cached_at"])
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600

        if age_hours > max_age_hours:
            logger.debug("Cache expired for %s/%s (%.1fh > %.1fh)", namespace, key, age_hours, max_age_hours)
            return None

        return entry["data"]
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Corrupt cache entry %s/%s: %s", namespace, key, e)
        path.unlink(missing_ok=True)
        return None


def cache_set(namespace, key, data):
    """Write data to cache with current timestamp."""
    path = _cache_path(namespace, key)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "_cached_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, default=str)


def cache_clear(namespace=None):
    """Delete cached files. Returns count of files deleted.

    If namespace is None, clears all namespaces.
    """
    if not CACHE_DIR.exists():
        return 0

    count = 0
    if namespace:
        target = CACHE_DIR / namespace
        if target.exists():
            for f in target.glob("*.json"):
                f.unlink()
                count += 1
            logger.info("Cleared %d cache entries from %s", count, namespace)
    else:
        for ns_dir in CACHE_DIR.iterdir():
            if ns_dir.is_dir():
                for f in ns_dir.glob("*.json"):
                    f.unlink()
                    count += 1
        logger.info("Cleared %d cache entries from all namespaces", count)
    return count


def cache_stats():
    """Return dict of {namespace: entry_count} for diagnostics."""
    stats = {}
    if not CACHE_DIR.exists():
        return stats
    for ns_dir in CACHE_DIR.iterdir():
        if ns_dir.is_dir():
            stats[ns_dir.name] = len(list(ns_dir.glob("*.json")))
    return stats
