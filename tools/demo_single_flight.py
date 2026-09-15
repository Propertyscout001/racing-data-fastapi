#!/usr/bin/env python3
"""Show that N concurrent callers cost one upstream call.

Fires N simultaneous requests at a COLD cache key, then reads /health to see
what the upstream actually saw.

    python3 tools/demo_single_flight.py [n] [base_url]

Needs httpx. Without the per-key lock in app/cache.py, a cold key under
concurrency produces N upstream calls, not one -- the TTL alone does not save
you, because every one of those N requests checks the cache before any of them
has finished writing to it.

Getting a cold key is mode-dependent, and this script checks which mode the
service is in before it fires:

  keyed mode  the cache key includes the query parameters, so a limit value
              nothing has asked for yet is cold by construction.
  demo  mode  /v1/demo/racing/next-to-go accepts no parameters, so every
              request shares the single cache key "races:demo" and limit is
              applied locally. A second run inside the TTL therefore proves
              nothing -- it is all hits. So in demo mode this script waits for
              the live key to expire first.
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid

import httpx

N = int(sys.argv[1]) if len(sys.argv) > 1 else 25
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"

DEMO_KEY = "races:demo"


async def cold_url(c: httpx.AsyncClient, health: dict) -> str:
    """Return a URL whose cache key is cold, waiting for the TTL if it must."""
    if health["mode"] == "keyed":
        return f"/races/next?country=AU&limit={3 + (uuid.uuid4().int % 40)}"

    remaining = (health.get("cache", {}).get("live_keys") or {}).get(DEMO_KEY)
    if remaining and remaining > 0:
        print(
            f"demo mode: every request shares the cache key '{DEMO_KEY}', so the key has "
            f"to expire before a cold-start run means anything.\n"
            f"waiting {remaining + 0.5:.1f}s for it...\n"
        )
        await asyncio.sleep(remaining + 0.5)
    return "/races/next?country=AU&limit=6"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        health = (await c.get("/health")).json()
        url = await cold_url(c, health)
        before = (await c.get("/health")).json()["cache"]

        t0 = time.perf_counter()
        responses = await asyncio.gather(*[c.get(url) for _ in range(N)])
        elapsed = (time.perf_counter() - t0) * 1000

        after = (await c.get("/health")).json()["cache"]

        ok = [r for r in responses if r.status_code == 200]
        charged = sum(r.json()["source"]["credits_charged"] for r in ok)

        print(f"mode                {health['mode']}")
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
