#!/usr/bin/env python3
"""A stand-in upstream that always fails, so the error paths can be tested.

You cannot conveniently make the real API return 402 on demand -- exhausting a
credit allowance to test an error handler is an expensive unit test. Point the
service at this instead:

    python3 tools/stub_upstream.py 402 &
    PE_API_KEY=not-a-real-key PE_UPSTREAM_BASE=http://127.0.0.1:8099/v1 \
      uvicorn app.main:app --port 8000

    curl -i localhost:8000/races/next

Stdlib only, no dependencies. Usage: stub_upstream.py [status] [port]
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# Real RFC 9457 documents, copied from live responses where one was observable.
BODIES = {
    402: {
        "type": "https://puntersedge.online/errors/payment-required",
        "title": "Monthly credit allowance exhausted",
        "status": 402,
        "detail": "Plan credits for this period are spent.",
    },
    429: {
        "type": "https://puntersedge.online/errors/rate-limit",
        "title": "Demo rate limit reached (30/min per IP).",
        "status": 429,
        "detail": "Demo rate limit reached (30/min per IP).",
    },
    401: {
        "type": "https://puntersedge.online/errors/unauthorized",
        "title": "Invalid API key",
        "status": 401,
        "detail": "Invalid API key",
    },
    503: {
        "type": "https://puntersedge.online/errors/unavailable",
        "title": "Upstream unavailable",
        "status": 503,
        "detail": "Temporarily unavailable.",
    },
}

STATUS = int(sys.argv[1]) if len(sys.argv) > 1 else 429
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8099


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(
            BODIES.get(STATUS, {"title": "stub error", "status": STATUS})
        ).encode()
        self.send_response(STATUS)
        self.send_header("Content-Type", "application/problem+json")
        self.send_header("Content-Length", str(len(body)))
        if STATUS == 429:
            self.send_header("Retry-After", "37")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("stub %s - %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    print(f"stub upstream on http://127.0.0.1:{PORT} returning {STATUS} for every GET")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
