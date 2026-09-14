"""Racing routes.

Two upstream shapes, one contract out. See app/normalise.py.

Note the country default. /v1/racing/next-to-go is NOT Australia-only -- drop
the filter and Japanese and Hong Kong races arrive mixed into what your UI is
almost certainly labelling "Australian racing". The demo endpoint takes no
parameters at all and always returns an AU sample, so in demo mode the country
and category filters are applied here, after the fetch, on whatever the sample
contained.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Path, Query

from .. import normalise, upstream
from ..cache import cache
from ..config import credit_cost, settings
from ..models import (
    PriceHistoryResponse,
    Race,
    RacePricesResponse,
    RacesResponse,
    Source,
)
from ..upstream import UpstreamError

router = APIRouter(tags=["racing"])

# race_id -> raw race dict, refreshed every time a snapshot is fetched. Lets
# /races/{race_id}/prices answer out of data already paid for.
_race_index: Dict[str, Dict[str, Any]] = {}

WIDE_SNAPSHOT_LIMIT = 200  # upstream cap for num_races on next-to-go


def _upstream_path() -> str:
    return "/racing/next-to-go" if settings.keyed else "/demo/racing/next-to-go"


async def _fetch_snapshot(
    country: Optional[str],
    limit: int,
    categories: Optional[str],
) -> Tuple[List[Dict[str, Any]], Optional[str], bool, float, int]:
    """Return (raw_races, note, cache_hit, age, credits_charged)."""
    path = _upstream_path()
    cost = credit_cost(path)

    if settings.keyed:
        params: Dict[str, Any] = {"num_races": limit}
        if country:
            params["country"] = country
        if categories:
            params["categories"] = categories
        key = f"races:{country or '*'}:{limit}:{categories or '*'}"
    else:
        params = {}           # the demo endpoint accepts none
        key = "races:demo"

    async def fetch() -> Any:
        payload, _headers = await upstream.get_json(path, params=params)
        return payload

    payload, hit, age = await cache.get_or_fetch(key, settings.ttl_races, fetch, cost=cost)
    raw, note = normalise.unwrap(payload, "races")

    for r in raw:
        rid = r.get("race_id")
        if rid:
            _race_index[rid] = r

    return raw, note, hit, age, (0 if hit else cost)


@router.get(
    "/races/next",
    response_model=RacesResponse,
    summary="Next races to jump",
    description=(
        "Upcoming races with every bookmaker price this feed carries, best price "
        "per runner precomputed. Serves the keyless PuntersEdge demo sample when "
        "no PE_API_KEY is configured, and the full feed when one is."
    ),
)
async def races_next(
    country: str = Query(
        settings.default_country,
        description="ISO country codes, comma separated. 'any' disables the filter.",
    ),
    limit: int = Query(10, ge=1, le=WIDE_SNAPSHOT_LIMIT),
    category: Optional[str] = Query(
        None, description="horse, harness or greyhound. Omit for all."
    ),
) -> RacesResponse:
    country_filter = None if country.lower() in ("any", "all", "*") else country
    raw, note, hit, age, charged = await _fetch_snapshot(country_filter, limit, category)

    races = [normalise.race_from(r) for r in raw]

    # Demo mode gets a fixed sample, so honour the filters here instead.
    if not settings.keyed:
        if country_filter:
            wanted = {c.strip().upper() for c in country_filter.split(",")}
            races = [r for r in races if (r.country or "").upper() in wanted]
        if category:
            races = [r for r in races if (r.category or "") == category]
    races = races[:limit]

    return RacesResponse(
        source=Source(
            mode=settings.mode,
            cached=hit,
            cache_age_seconds=round(age, 2),
            upstream_path=_upstream_path(),
            credits_charged=charged,
            note=note,
        ),
        count=len(races),
        races=races,
    )


@router.get(
    "/races/{race_id}/prices",
    response_model=RacePricesResponse,
    summary="Every bookmaker price for one race",
    description=(
        "Reads the race out of the snapshot /races/next already fetched, so in "
        "the common case this route costs zero additional upstream credits."
    ),
)
async def race_prices(
    race_id: str = Path(..., description="race_id from /races/next"),
) -> RacePricesResponse:
    charged = 0
    hit = True
    age = 0.0

    raw = _race_index.get(race_id)
    if raw is None:
        # Cold index (fresh process, or a race outside the last window). Pull a
        # wide snapshot once; it lands in the shared cache and the index, so the
        # next caller asking for any race on the card pays nothing.
        _raw_list, _note, hit, age, charged = await _fetch_snapshot(
            settings.default_country, WIDE_SNAPSHOT_LIMIT, None
        )
        raw = _race_index.get(race_id)

    if raw is None:
        raise UpstreamError(
            404,
            {
                "type": "https://github.com/Propertyscout001/racing-data-fastapi/errors/race-not-open",
                "title": "Race not in the current snapshot",
                "status": 404,
                "detail": (
                    f"race_id '{race_id}' is not in the currently quoted set. "
                    "Races leave next-to-go once they jump; use "
                    "/races/{race_id}/history for a race that has already run."
                ),
                "retryable": False,
            },
        )

    return RacePricesResponse(
        source=Source(
            mode=settings.mode,
            cached=hit,
            cache_age_seconds=round(age, 2),
            upstream_path=_upstream_path(),
            credits_charged=charged,
            note=None if charged else "served from the snapshot cache, no upstream call",
        ),
        race=normalise.race_from(raw),
    )


@router.get(
    "/races/{race_id}/history",
    response_model=PriceHistoryResponse,
    summary="Open/close/high/low per runner-bookmaker for a race that has run",
    description=(
        "Wraps /v1/racing/price-history (5 credits). Requires an upstream key. "
        "Upstream returns 404 for a race that has not jumped yet -- history is "
        "written once the market has closed, not while it is live."
    ),
)
async def race_history(
    race_id: str = Path(..., description="race_id of a race that has already run"),
    include_points: bool = Query(
        False, description="True returns every recorded point, not just open/close/high/low"
    ),
) -> PriceHistoryResponse:
    if not settings.keyed:
        raise UpstreamError(
            501,
            {
                "type": "https://github.com/Propertyscout001/racing-data-fastapi/errors/requires-key",
                "title": "Price history needs an upstream key",
                "status": 501,
                "detail": (
                    "This instance is running in keyless demo mode. There is no "
                    "demo endpoint for price history. Set PE_API_KEY to enable "
                    "it; a free key is at "
                    "https://puntersedge.online/api?utm_source=racing-data-fastapi&utm_medium=code"
                ),
                "retryable": False,
            },
        )

    path = "/racing/price-history"
    cost = credit_cost(path)
    params = {"race_id": race_id, "include_points": str(include_points).lower(), "max_points": 100}
    key = f"history:{race_id}:{include_points}"

    async def fetch() -> Any:
        payload, _headers = await upstream.get_json(path, params=params)
        return payload

    payload, hit, age = await cache.get_or_fetch(key, settings.ttl_history, fetch, cost=cost)

    return PriceHistoryResponse(
        source=Source(
            mode=settings.mode,
            cached=hit,
            cache_age_seconds=round(age, 2),
            upstream_path=path,
            credits_charged=0 if hit else cost,
        ),
        race_id=payload.get("race_id"),
        venue=payload.get("venue_canonical") or payload.get("venue"),
        race_number=payload.get("race_number"),
        category=payload.get("category"),
        start_time=payload.get("start_time"),
        runners=normalise.history_runners_from(payload),
    )
