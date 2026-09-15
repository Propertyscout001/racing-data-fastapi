#!/usr/bin/env python3
"""Measure what gzip saves on this feed, right now.

    python3 tools/measure_gzip.py

Needs httpx. Hits a keyless demo endpoint, so it costs no credits and needs no
key. Reports the ratio as well as the byte counts, because the byte counts are
the part that does not keep: the demo sample's bookmaker panel varies between
calls, so a figure quoted as "7,324 bytes" is out of date by the next request.
The ratio has held around 4.7-4.8x across runs.
"""
from __future__ import annotations

import datetime

import httpx

URL = "https://api.puntersedge.online/v1/demo/racing/next-to-go"
UA = {"User-Agent": "racing-data-fastapi/measure"}


def main() -> None:
    with httpx.Client(timeout=15) as c:
        gz = c.get(URL, headers={**UA, "Accept-Encoding": "gzip"})
        # len(gz.content) is the DECOMPRESSED body -- httpx transparently
        # inflates it, so measuring that measures nothing. num_bytes_downloaded
        # is what actually crossed the wire. The response is chunked, so there
        # is no Content-Length header to read either.
        wire = gz.num_bytes_downloaded
        plain = c.get(URL, headers={**UA, "Accept-Encoding": "identity"})
        identity = len(plain.content)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"measured {now}  {URL}")
    print(f"identity           {identity:,} bytes")
    print(f"gzip (on the wire) {wire:,} bytes")
    print(f"ratio              {identity / wire:.2f}x")
    print("\nPayload size varies per call; quote the ratio, not the byte counts.")


if __name__ == "__main__":
    main()
