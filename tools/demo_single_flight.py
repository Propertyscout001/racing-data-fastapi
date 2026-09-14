#!/usr/bin/env python3
"""Show that N concurrent callers cost one upstream call.

Fires N simultaneous requests at a cache key nothing has touched yet, then reads
/health to see what the upstream actually saw.

    python3 tools/demo_single_flight.py [n] [base_url]

Needs httpx. Without the per-key lock in app/cache.py, a cold key under
concurrency produces N upstream calls, not one -- the TTL alone does not save
you, because every one of those N requests checks the cache before any of them
has finished writing to it.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid

import httpx

N = int(sys.argv[1]) if len(sys.argv) > 1 else 25
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        before = (await c.get("/health")).json()["cache"]

        # A limit value nothing has requested yet guarantees a cold cache key.
        cold_limit = 3 + (uuid.uuid4().int % 40)
        url = f"/races/next?country=AU&limit={cold_limit}"

        t0 = time.perf_counter()
        responses = await asyncio.gather(*[c.get(url) for _ in range(N)])
        elapsed = (time.perf_counter() - t0) * 1000

        after = (await c.get("/health")).json()["cache"]

        ok = [r for r in responses if r.status_code == 200]
        charged = sum(r.json()["source"]["credits_charged"] for r in ok)

        print(f"url                 {url}")
        print(f"concurrent requests {N}")
        print(f"wall time           {elapsed:.0f} ms")
        print(f"200 responses       {len(ok)}")
        print(f"credits charged     {charged}  (sum of source.credits_charged across all {N})")
        print()
        for field in ("misses", "coalesced", "hits", "upstream_calls", "credits_spent"):
            print(f"{field:<18} {before[field]:>4} -> {after[field]:<4} "
                  f"(+{after[field] - before[field]})")


if __name__ == "__main__":
    asyncio.run(main())
