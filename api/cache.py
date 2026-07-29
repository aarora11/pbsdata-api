"""In-process response cache keyed by schedule lifetime.

PBS data is immutable within a schedule month, so a cached response stays valid
until that schedule is re-ingested. Call cache_invalidate_schedule(schedule_id) at
the end of every ingest.

Two limitations found by the 2026-07 audit, both addressed here:

* No expiry. The stored timestamp was written and then ignored, so entries lived
  for the life of the process and the documented CACHE_TTL_* settings did nothing.
* Unbounded growth. Nothing evicted entries except an explicit invalidation.

One limitation remains by design: this cache is per-process. The deployment runs
gunicorn with multiple workers, so cache_invalidate_schedule only clears the worker
that handled the ingest callback. Because keys embed schedule_id, a *new* schedule
is unaffected — but re-ingesting a corrected schedule keeps the same id, and other
workers will serve the superseded response until the TTL expires. The TTL below is
what bounds that exposure. Moving to Redis removes it entirely.
"""
import time
from collections import OrderedDict
from typing import Any

from api.config import get_settings

# Hard ceiling on entries. Aggregate responses are large, and an unbounded dict in
# a long-lived worker is a slow memory leak.
MAX_ENTRIES = 512

_cache: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
_schedule_keys: dict[str, set[str]] = {}  # schedule_id -> set of cache keys


def _ttl_seconds() -> float:
    settings = get_settings()
    return float(getattr(settings, "CACHE_TTL_SCHEDULE_SECONDS", 86400) or 86400)


def cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, value = entry
    if (time.monotonic() - stored_at) > _ttl_seconds():
        _evict(key)
        return None
    _cache.move_to_end(key)  # LRU: mark as recently used
    return value


def cache_set(key: str, value: Any, schedule_id: str) -> None:
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    _schedule_keys.setdefault(schedule_id, set()).add(key)

    while len(_cache) > MAX_ENTRIES:
        oldest, _ = _cache.popitem(last=False)
        for keys in _schedule_keys.values():
            keys.discard(oldest)


def _evict(key: str) -> None:
    _cache.pop(key, None)
    for keys in _schedule_keys.values():
        keys.discard(key)


def cache_invalidate_schedule(schedule_id: str) -> None:
    for key in _schedule_keys.pop(schedule_id, set()):
        _cache.pop(key, None)


def cache_clear() -> None:
    """Drop everything. Used by tests."""
    _cache.clear()
    _schedule_keys.clear()


def cache_stats() -> dict:
    """Introspection for the data health endpoint and tests."""
    return {
        "entries": len(_cache),
        "max_entries": MAX_ENTRIES,
        "ttl_seconds": _ttl_seconds(),
        "schedules_tracked": len(_schedule_keys),
    }
