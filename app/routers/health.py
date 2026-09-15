"""Health and cache visibility.

The probe costs no CREDITS -- it calls a keyless demo endpoint (0 credits) in
both modes, because what a health check needs to establish is that the data
path (DNS, TLS, the pooled connection, the upstream process) is alive.

It is not free of REQUESTS, though, and that distinction bit during review. The
keyless demo tier allows 30 requests per minute per IP. An uncached probe makes
liveness-probe frequency the thing that sets upstream request volume: a 5s
Kubernetes probe is 12 requests a minute of a 30/minute budget, and a service
that trips that limit reports itself "degraded" because of its own health
checks. So the probe result is cached for PROBE_TTL_S seconds and the response
says how old it is.

The probe cache is deliberately a SEPARATE TTLCache instance from the data one.
Sharing it would fold health checks into the hit/miss/credit counters that
/health itself reports, and a cache whose statistics are moved by the act of
reading them is not worth reading.

In keyed mode it additionally tries /v1/usage (also 0 credits) to report the
remaining allowance. That call is best-effort and CANNOT turn the service red.
Observed 2026-09-15: /v1/usage returned HTTP 500 for the key used during this
build while every data endpoint answered normally. A billing endpoint having a
bad day is not a reason to fail a liveness probe and have an orchestrator start
restarting healthy containers.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter

from .. import upstream
from ..cache import TTLCache, cache
from ..config import settings
from ..models import CacheBlock, HealthResponse
from ..upstream import UpstreamError

router = APIRouter(tags=["ops"])

PROBE_PATH = "/demo/racing/next-to-go"  # 0 credits, no key required
PROBE_TTL_S = 10                        # see the module docstring
_probe_cache = TTLCache()               # NOT the shared data cache


async def _probe() -> Dict[str, Any]:
    """Run the upstream probe. Never raises: a failure IS the result."""
    started = time.perf_counter()
    try:
        await upstream.get_json(PROBE_PATH)
        return {
            "reachable": True,
            "detail": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except UpstreamError as exc:
        return {
            "reachable": False,
            "detail": exc.problem.get("detail"),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }


@router.get("/health", response_model=HealthResponse, summary="Liveness, mode and cache state")
async def health() -> HealthResponse:
    # A failed probe is cached like a successful one, on purpose: without that,
    # the unreachable case is the one where probe frequency once again equals
    # upstream request frequency, which is exactly when you least want it to.
    result, probe_cached, probe_age = await _probe_cache.get_or_fetch(
        "health:probe", PROBE_TTL_S, _probe, cost=0
    )

    credits_remaining = await _credits_remaining() if settings.keyed else None

    stats = cache.stats.as_dict()
    return HealthResponse(
        status="ok" if result["reachable"] else "degraded",
        mode=settings.mode,
        upstream=settings.upstream_base,
        upstream_reachable=result["reachable"],
        upstream_latency_ms=result["latency_ms"],
        upstream_detail=result["detail"],
        upstream_probe_cached=probe_cached,
        upstream_probe_age_seconds=round(probe_age, 1),
        upstream_probe_ttl_seconds=PROBE_TTL_S,
        credits_remaining=credits_remaining,
        cache=CacheBlock(live_keys=cache.keys(), **stats),
        ttl_seconds={
            "races": settings.ttl_races,
            "best_odds": settings.ttl_best_odds,
            "history": settings.ttl_history,
        },
    )


async def _credits_remaining() -> Optional[str]:
    """Best-effort. Never raises, never affects the health verdict.

    Cached on the same probe cache so a tight liveness loop does not turn into
    a tight /v1/usage loop either.
    """
    async def fetch() -> Any:
        try:
            payload, headers = await upstream.get_json("/usage")
        except UpstreamError:
            return None
        return {"payload": payload, "headers": dict(headers)}

    result, _hit, _age = await _probe_cache.get_or_fetch(
        "health:usage", PROBE_TTL_S, fetch, cost=0
    )
    if not result:
        return None
    payload, headers = result["payload"], result["headers"]
    for key, value in headers.items():
        if key.lower() == "x-credits-remaining":
            return str(value)
    if isinstance(payload, dict):
        for field in ("credits_remaining", "remaining", "credits_left"):
            if field in payload:
                return str(payload[field])
    return None
