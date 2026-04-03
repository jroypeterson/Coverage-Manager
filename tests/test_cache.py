"""Tests for cache.py — file-based caching."""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from cache import cache_get, cache_set, cache_clear, cache_stats, _cache_path
from config import CACHE_DIR


@pytest.fixture(autouse=True)
def clean_test_cache():
    """Use a test namespace and clean up after each test."""
    yield
    # Clean up test namespace — delete files, tolerate locked dirs on Windows/Dropbox
    test_dir = CACHE_DIR / "_test"
    if test_dir.exists():
        for f in test_dir.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            test_dir.rmdir()
        except OSError:
            pass


class TestCacheSetGet:
    def test_set_and_get(self):
        cache_set("_test", "key1", {"ticker": "AAPL", "price": 150.0})
        result = cache_get("_test", "key1", max_age_hours=1.0)
        assert result == {"ticker": "AAPL", "price": 150.0}

    def test_get_missing(self):
        result = cache_get("_test", "nonexistent", max_age_hours=1.0)
        assert result is None

    def test_get_expired(self):
        # Write an entry with a timestamp 2 hours ago
        path = _cache_path("_test", "old_key")
        path.parent.mkdir(parents=True, exist_ok=True)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with open(path, "w") as f:
            json.dump({"_cached_at": old_time, "data": "old_data"}, f)

        result = cache_get("_test", "old_key", max_age_hours=1.0)
        assert result is None

    def test_get_not_expired(self):
        cache_set("_test", "fresh_key", "fresh_data")
        result = cache_get("_test", "fresh_key", max_age_hours=1.0)
        assert result == "fresh_data"

    def test_corrupt_json(self):
        path = _cache_path("_test", "corrupt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json{{{")
        result = cache_get("_test", "corrupt", max_age_hours=1.0)
        assert result is None
        assert not path.exists()  # corrupt entry should be deleted

    def test_overwrite(self):
        cache_set("_test", "overwrite", "v1")
        cache_set("_test", "overwrite", "v2")
        result = cache_get("_test", "overwrite", max_age_hours=1.0)
        assert result == "v2"


class TestCacheClear:
    def test_clear_namespace(self):
        cache_set("_test", "a", 1)
        cache_set("_test", "b", 2)
        count = cache_clear("_test")
        assert count == 2
        assert cache_get("_test", "a", max_age_hours=1.0) is None

    def test_clear_empty(self):
        count = cache_clear("_test_empty")
        assert count == 0


class TestCacheStats:
    def test_stats(self):
        cache_set("_test", "s1", 1)
        cache_set("_test", "s2", 2)
        stats = cache_stats()
        assert stats.get("_test") == 2
