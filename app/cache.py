"""A TTL cache with single-flight, and the credit accounting that justifies it.

The arithmetic is the whole reason this service exists.

/v1/racing/next-to-go costs 2 credits per successful call. The free tier is
1,500 credits a month. Proxy it one-to-one and 750 page loads exhausts the
month -- roughly 25 a day. Put a 20 second TTL in front of it and the upstream
cost stops tracking your traffic and starts tracking the clock: 3 calls a
minute worst case, 6 credits a minute, about 8,640 credits a day if you poll
flat out around the clock, but a *fixed* number no matter whether one person or
ten thousand are watching. Widen the TTL until that fixed number fits the plan.

Single-flight matters as much as the TTL. Without it, N concurrent requests
arriving on a cold key all miss together and all call upstream -- the cache
reports a 99% hit rate and the bill still arrives. One asyncio.Lock per key
collapses that stampede into one upstream call.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple


@dataclass
class _Entry:
    value: Any
    expires_at: float
    stored_at: float


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    coalesced: int = 0          # requests that waited on someone else's fetch
    upstream_calls: int = 0     # fetches that returned a payload
    upstream_errors: int = 0    # fetches that raised; nothing was cached
    credits_spent: int = 0
    credits_avoided: int = 0    # what a one-to-one proxy would have spent

    def as_dict(self) -> Dict[str, Any]:
        served = self.hits + self.misses + self.coalesced
        return {
            "requests_served": served,
            "hits": self.hits,
            "misses": self.misses,
            "coalesced": self.coalesced,
            "hit_rate": round((self.hits + self.coalesced) / served, 4) if served else 0.0,
            "upstream_calls": self.upstream_calls,
            "upstream_errors": self.upstream_errors,
            "credits_spent": self.credits_spent,
            "credits_avoided_by_cache": self.credits_avoided,
        }


class TTLCache:
    def __init__(self) -> None:
        self._data: Dict[str, _Entry] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()
        self.stats = CacheStats()

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def peek(self, key: str) -> Tuple[Optional[Any], Optional[float]]:
        """Return (value, age_seconds) without touching hit/miss counters."""
        entry = self._data.get(key)
        if entry is None or entry.expires_at <= time.monotonic():
            return None, None
        return entry.value, time.monotonic() - entry.stored_at

    async def get_or_fetch(
        self,
        key: str,
        ttl: int,
        fetch: Callable[[], Awaitable[Any]],
        cost: int = 0,
    ) -> Tuple[Any, bool, float]:
        """Return (value, cache_hit, age_seconds).

        On a miss exactly one caller runs `fetch`; the rest wait on the lock and
        are served the result it stored. They are counted as `coalesced`, not as
        hits, because conflating the two is how a cache appears to be working
        while the upstream bill says otherwise.
        """
        now = time.monotonic()
        entry = self._data.get(key)
        if entry is not None and entry.expires_at > now:
            self.stats.hits += 1
            self.stats.credits_avoided += cost
            return entry.value, True, now - entry.stored_at

        async with self._guard:
            lock = self._lock_for(key)

        already_held = lock.locked()
        async with lock:
            # Re-check: while waiting, the holder may have filled the key.
            now = time.monotonic()
            entry = self._data.get(key)
            if entry is not None and entry.expires_at > now:
                if already_held:
                    self.stats.coalesced += 1
                else:
                    self.stats.hits += 1
                self.stats.credits_avoided += cost
                return entry.value, True, now - entry.stored_at

            self.stats.misses += 1
            try:
                value = await fetch()
            except Exception:
                # A failed fetch is not cached: the next caller retries rather
                # than being served a stored error. Counted separately so the
                # numbers reconcile -- misses == upstream_calls + upstream_errors.
                self.stats.upstream_errors += 1
                raise
            self.stats.upstream_calls += 1
            self.stats.credits_spent += cost
            now = time.monotonic()
            self._data[key] = _Entry(value=value, expires_at=now + ttl, stored_at=now)
            return value, False, 0.0

    def keys(self) -> Dict[str, float]:
        """Live keys mapped to their remaining TTL in seconds."""
        now = time.monotonic()
        return {
            k: round(e.expires_at - now, 1)
            for k, e in self._data.items()
            if e.expires_at > now
        }

    def clear(self) -> None:
        self._data.clear()


cache = TTLCache()
