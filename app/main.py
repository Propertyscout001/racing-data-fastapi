"""racing-data-fastapi -- a FastAPI backend in front of the PuntersEdge odds API.

What this file wires together:

  * one httpx.AsyncClient for the process lifetime (connection reuse)
  * a TTL cache with single-flight, so N concurrent callers cost 1 upstream call
  * RFC 9457 problem+json on every error path, including a deliberate
    402-vs-429 distinction
  * the upstream key held server-side only -- it is never in a response body,
    never in the OpenAPI document, and never reaches the browser

It starts and answers with no upstream key at all by proxying the keyless demo
endpoints, so you can run it before deciding whether to register.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import upstream
from .config import settings
from .routers import best_odds, health, races
from .upstream import UpstreamError

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("proxy")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

SIGNUP = (
    "https://puntersedge.online/api"
    "?utm_source=racing-data-fastapi&utm_medium=code"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await upstream.startup()
    log.info(
        "mode=%s upstream=%s ttl_races=%ss",
        settings.mode,
        settings.upstream_base,
        settings.ttl_races,
    )
    if not settings.keyed:
        log.info("no PE_API_KEY set -- serving the keyless demo endpoints. Free key: %s", SIGNUP)
    try:
        yield
    finally:
        await upstream.shutdown()


app = FastAPI(
    title="racing-data-fastapi",
    version="1.0.0",
    summary="A cached, key-custodying FastAPI backend over the PuntersEdge Australian racing and sports odds API.",
    description=(
        "A reference backend for reading the PuntersEdge odds feed.\n\n"
        "Runs without an upstream API key by proxying the keyless demo "
        "endpoints. Set `PE_API_KEY` to serve the full feed.\n\n"
        f"Free key: {SIGNUP}"
    ),
    license_info={"name": "MIT"},
    lifespan=lifespan,
)

app.include_router(races.router)
app.include_router(best_odds.router)
app.include_router(health.router)


@app.middleware("http")
async def strip_client_key(request: Request, call_next):
    """A client of this service has no business supplying an upstream key.

    Nothing downstream reads the inbound header, but saying so explicitly makes
    the trust boundary obvious to the next person editing this file: the key
    comes from the environment, one place, and travels outbound only.
    """
    response = await call_next(request)
    response.headers["X-Upstream-Mode"] = settings.mode
    return response


def _problem_response(status: int, problem: Dict[str, Any], retry_after=None) -> JSONResponse:
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status,
        content=problem,
        media_type="application/problem+json",
        headers=headers,
    )


@app.exception_handler(UpstreamError)
async def upstream_error_handler(_request: Request, exc: UpstreamError) -> JSONResponse:
    return _problem_response(exc.status, exc.problem, exc.retry_after)


@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return _problem_response(
        400,
        {
            "type": "https://github.com/Propertyscout001/racing-data-fastapi/errors/invalid-request",
            "title": "Invalid request parameters",
            "status": 400,
            "detail": "One or more query or path parameters failed validation.",
            "errors": [
                {"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()
            ],
            "retryable": False,
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _problem_response(
        exc.status_code,
        {
            "type": "https://github.com/Propertyscout001/racing-data-fastapi/errors/http-error",
            "title": str(exc.detail),
            "status": exc.status_code,
            "detail": str(exc.detail),
            "retryable": exc.status_code >= 500,
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
    # Log the traceback, return nothing that could carry a connection string or
    # a key into the response body.
    log.exception("unhandled error")
    return _problem_response(
        500,
        {
            "type": "https://github.com/Propertyscout001/racing-data-fastapi/errors/internal",
            "title": "Internal error",
            "status": 500,
            "detail": "The service failed to handle this request.",
            "retryable": True,
        },
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
