"""Health and cache visibility.

The probe costs nothing. It calls a keyless demo endpoint (0 credits) in both
modes, because what a health check needs to establish is that the data path --
DNS, TLS, the pooled connection, the upstream process -- is alive.

In keyed mode it additionally tries /v1/usage (also 0 credits) to report the
remaining allowance. That call is treated as best-effort and CANNOT turn the
service red. Observed 2026-09-15: /v1/usage returned HTTP 500 for the key used
during this build while every data endpoint answered normally. A billing
endpoint having a bad day is not a reason to fail a liveness probe and have an
orchestrator start restarting healthy containers.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter

from .. import upstream
from ..cache import cache
from ..config import settings
from ..models import CacheBlock, HealthResponse
from ..upstream import UpstreamError

router = APIRouter(tags=["ops"])

PROBE_PATH = "/demo/racing/next-to-go"  # 0 credits, no key required


@router.get("/health", response_model=HealthResponse, summary="Liveness, mode and cache state")
async def health() -> HealthResponse:
    reachable = True
    detail: Optional[str] = None

    started = time.perf_counter()
    try:
        await upstream.get_json(PROBE_PATH)
    except UpstreamError as exc:
        reachable = False
        detail = exc.problem.get("detail")
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    credits_remaining = await _credits_remaining() if settings.keyed else None

    stats = cache.stats.as_dict()
    return HealthResponse(
        status="ok" if reachable else "degraded",
        mode=settings.mode,
        upstream=settings.upstream_base,
        upstream_reachable=reachable,
        upstream_latency_ms=latency_ms,
        upstream_detail=detail,
        credits_remaining=credits_remaining,
        cache=CacheBlock(live_keys=cache.keys(), **stats),
        ttl_seconds={
            "races": settings.ttl_races,
            "best_odds": settings.ttl_best_odds,
            "history": settings.ttl_history,
        },
    )


async def _credits_remaining() -> Optional[str]:
    """Best-effort. Never raises, never affects the health verdict."""
    try:
        payload, headers = await upstream.get_json("/usage")
    except UpstreamError:
        return None
    for key, value in headers.items():
        if key.lower() == "x-credits-remaining":
            return str(value)
    if isinstance(payload, dict):
        for field in ("credits_remaining", "remaining", "credits_left"):
            if field in payload:
                return str(payload[field])
    return None
