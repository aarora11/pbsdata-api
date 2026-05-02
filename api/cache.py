"""Simple in-process response cache keyed by schedule lifetime.

PBS data is immutable within a schedule month, so cached responses are valid until
the next ingest completes. Call cache_invalidate_schedule(schedule_id) at ingest end.
"""
import time
from typing import Any

_cache: dict[str, tuple[float, Any]] = {}
_schedule_keys: dict[str, set[str]] = {}  # schedule_id → set of cache keys


def cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    _, value = entry
    return value


def cache_set(key: str, value: Any, schedule_id: str) -> None:
    _cache[key] = (time.monotonic(), value)
    _schedule_keys.setdefault(schedule_id, set()).add(key)


def cache_invalidate_schedule(schedule_id: str) -> None:
    for key in _schedule_keys.pop(schedule_id, set()):
        _cache.pop(key, None)
