#!/usr/bin/env python3
"""Reproduce the connection-reuse measurement quoted in the README.

    python3 tools/measure_connection_reuse.py

Needs httpx. Hits a keyless demo endpoint, so it costs no credits and needs no
key. The demo tier allows 30 requests/minute per IP; this script stays under it.
"""
from __future__ import annotations

import statistics
import time

import httpx

URL = "https://api.puntersedge.online/v1/demo/racing/next-to-go"
HEADERS = {"Accept-Encoding": "gzip", "User-Agent": "racing-data-fastapi/measure"}


def main() -> None:
    cold = []
    for _ in range(5):
        t0 = time.perf_counter()
        with httpx.Client(timeout=15) as c:  # new TLS handshake every iteration
            c.get(URL, headers=HEADERS)
        cold.append((time.perf_counter() - t0) * 1000)

    warm = []
    with httpx.Client(timeout=15, headers=HEADERS) as c:
        c.get(URL)  # prime the pool, not measured
        for _ in range(8):
            t0 = time.perf_counter()
            c.get(URL)
            warm.append((time.perf_counter() - t0) * 1000)

    print(f"cold (new client per call, n={len(cold)}): "
          f"median {statistics.median(cold):6.1f} ms  min {min(cold):6.1f}  max {max(cold):6.1f}")
    print(f"warm (reused connection,  n={len(warm)}): "
          f"median {statistics.median(warm):6.1f} ms  min {min(warm):6.1f}  max {max(warm):6.1f}")
    print(f"\nreuse saves a median of "
          f"{statistics.median(cold) - statistics.median(warm):.1f} ms per call from here.")


if __name__ == "__main__":
    main()
