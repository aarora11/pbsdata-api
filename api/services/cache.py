"""Simple in-memory cache with TTL."""
import time
from typing import Any, Optional

_cache: dict[str, tuple[Any, float]] = {}


def cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.time() > expires_at:
        del _cache[key]
        return None
    return value


def cache_set(key: str, value: Any, ttl: int = 300):
    _cache[key] = (value, time.time() + ttl)


def cache_delete(key: str):
    _cache.pop(key, None)


def cache_clear():
    _cache.clear()
