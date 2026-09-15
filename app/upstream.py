"""The single outbound HTTP client, plus upstream error translation.

Two things matter here and both are easy to get wrong:

1. ONE AsyncClient for the process lifetime. A fresh client per request throws
   away the TLS session and the pooled socket. Measured against this API on
   2026-09-15, a new-connection call had a median round trip of 187.9 ms
   against 47.8 ms on a reused connection (tools/measure_connection_reuse.py
   reproduces it).

2. Upstream failures are RFC 9457 problem+json. They must not be flattened into
   a generic 500, and the caller must be able to tell "come back later" from
   "no point coming back". See map_upstream_error().
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import httpx

from .config import settings

log = logging.getLogger("proxy.upstream")

USER_AGENT = "racing-data-fastapi/1.0 (+https://github.com/Propertyscout001/racing-data-fastapi)"

_client: Optional[httpx.AsyncClient] = None


def build_client() -> httpx.AsyncClient:
    headers = {
        # Responses compress well. Quote the RATIO, not byte counts: the demo
        # sample's bookmaker panel varies per call, so an absolute figure is
        # stale by the next request. Measured with tools/measure_gzip.py on
        # 2026-09-15, the demo next-to-go payload was 7,025 bytes identity
        # against 1,460 on the wire -- 4.81x, and 4.7-4.8x across repeat runs.
        "Accept-Encoding": "gzip",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if settings.api_key:
        headers["X-API-Key"] = settings.api_key
    return httpx.AsyncClient(
        base_url=settings.upstream_base,
        headers=headers,
        timeout=httpx.Timeout(settings.timeout_s),
        limits=httpx.Limits(
            max_keepalive_connections=settings.max_keepalive,
            keepalive_expiry=settings.keepalive_expiry_s,
        ),
        follow_redirects=True,
    )


async def startup() -> None:
    global _client
    if _client is None:
        _client = build_client()


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def client() -> httpx.AsyncClient:
    if _client is None:  # pragma: no cover - lifespan always runs first
        raise RuntimeError("upstream client not started")
    return _client


class UpstreamError(Exception):
    """An upstream failure already translated into a problem+json document."""

    def __init__(
        self,
        status: int,
        problem: Dict[str, Any],
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(problem.get("title", "upstream error"))
        self.status = status
        self.problem = problem
        self.retry_after = retry_after


def _problem(
    status: int,
    type_: str,
    title: str,
    detail: str,
    **extra: Any,
) -> Dict[str, Any]:
    doc = {
        "type": f"https://github.com/Propertyscout001/racing-data-fastapi/errors/{type_}",
        "title": title,
        "status": status,
        "detail": detail,
    }
    doc.update(extra)
    return doc


def map_upstream_error(resp: httpx.Response) -> UpstreamError:
    """Translate an upstream non-2xx into the status this service should return.

    The distinction that actually matters to whoever calls this service:

      402  the account's monthly credits are spent. Retrying does not help
           until the billing period rolls over, so no Retry-After is sent and
           the problem document is flagged retryable=false.
      429  a rate limit -- transient by definition. Retry-After is forwarded
           (or defaulted) and the document is flagged retryable=true.

    401/403 mean *this service* is misconfigured or under-provisioned. The
    caller did nothing wrong and has no key to fix, so returning 401 to them
    would be a lie; they get 502 with an explicit detail instead.
    """
    try:
        body = resp.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    upstream_detail = body.get("detail") or body.get("title") or resp.text[:400]
    status = resp.status_code

    if status == 402:
        return UpstreamError(
            402,
            _problem(
                402,
                "upstream-credits-exhausted",
                "Upstream credit allowance exhausted",
                "This service's PuntersEdge credit allowance for the current "
                "period is spent. Retrying will not help until it resets or the "
                "plan is raised.",
                retryable=False,
                upstream_status=402,
                upstream_detail=upstream_detail,
            ),
        )

    if status == 429:
        retry_after = _retry_after_seconds(resp)
        return UpstreamError(
            429,
            _problem(
                429,
                "upstream-rate-limited",
                "Upstream rate limit reached",
                "The PuntersEdge rate limit was reached. This is transient -- "
                "retry after the interval in the Retry-After header.",
                retryable=True,
                retry_after_seconds=retry_after,
                upstream_status=429,
                upstream_detail=upstream_detail,
            ),
            retry_after=retry_after,
        )

    if status in (401, 403):
        # Do not leak which of the two it was any further than the detail line,
        # and never echo the key.
        log.error("upstream auth failure status=%s detail=%s", status, upstream_detail)
        return UpstreamError(
            502,
            _problem(
                502,
                "upstream-auth",
                "Upstream rejected this service's credentials",
                "The configured PE_API_KEY is missing, invalid, or the endpoint "
                "is gated to a higher plan. This is a server-side configuration "
                "problem, not a problem with your request.",
                retryable=False,
                upstream_status=status,
            ),
        )

    if status == 404:
        return UpstreamError(
            404,
            _problem(
                404,
                "not-found",
                "Not found upstream",
                str(upstream_detail),
                retryable=False,
                upstream_status=404,
            ),
        )

    if status == 422:
        return UpstreamError(
            400,
            _problem(
                400,
                "invalid-request",
                "Upstream rejected the request parameters",
                str(upstream_detail),
                retryable=False,
                upstream_status=422,
            ),
        )

    if status >= 500:
        return UpstreamError(
            502,
            _problem(
                502,
                "upstream-unavailable",
                "Upstream returned an error",
                f"PuntersEdge returned HTTP {status}.",
                retryable=True,
                upstream_status=status,
                upstream_detail=upstream_detail,
            ),
        )

    return UpstreamError(
        502,
        _problem(
            502,
            "upstream-unexpected",
            "Unexpected upstream response",
            f"PuntersEdge returned HTTP {status}.",
            retryable=False,
            upstream_status=status,
            upstream_detail=upstream_detail,
        ),
    )


def _retry_after_seconds(resp: httpx.Response) -> int:
    raw = resp.headers.get("retry-after")
    if raw:
        try:
            return max(1, int(float(raw)))
        except ValueError:
            pass
    # The keyless demo tier limits 30 requests/minute per IP, so a full minute
    # is the safe default when upstream does not say.
    return 60


async def get_json(
    path: str,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Dict[str, str]]:
    """GET an upstream path. Raises UpstreamError on anything that is not 2xx.

    Deliberately does NOT retry. A silent retry on a price endpoint can return
    a quote recorded before a move, which is worse than an error.
    """
    try:
        resp = await client().get(path, params=params)
    except httpx.TimeoutException as exc:
        raise UpstreamError(
            504,
            _problem(
                504,
                "upstream-timeout",
                "Upstream timed out",
                f"No response from PuntersEdge within {settings.timeout_s}s.",
                retryable=True,
            ),
        ) from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(
            502,
            _problem(
                502,
                "upstream-unreachable",
                "Could not reach upstream",
                f"{type(exc).__name__} contacting PuntersEdge.",
                retryable=True,
            ),
        ) from exc

    if resp.status_code >= 400:
        raise map_upstream_error(resp)

    credit_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower().startswith("x-credits")
    }
    return resp.json(), credit_headers
