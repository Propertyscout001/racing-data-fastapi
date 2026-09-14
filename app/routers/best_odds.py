"""Sports best-odds route.

There is no 'horse-racing' sport key -- racing lives under /races/*. The sport
keys this feed serves are listed in SPORT_KEYS below; anything else is refused
here, before it costs a credit, rather than being forwarded.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Path

from .. import normalise, upstream
from ..cache import cache
from ..config import credit_cost, settings
from ..models import BestOddsResponse, Source
from ..upstream import UpstreamError

router = APIRouter(tags=["sports"])

# Verified against https://api.puntersedge.online/openapi.json on 2026-09-15.
SPORT_KEYS = {
    "afl", "aflw", "nrl", "nrlw", "nba", "wnba", "nfl", "ncaaf", "mlb", "nhl",
    "mma", "tennis_atp", "tennis_wta", "cricket_test", "cricket_other",
    "rugby_union", "super_league", "soccer_epl", "soccer_other",
    "basketball_other",
}


@router.get(
    "/best-odds/{sport}",
    response_model=BestOddsResponse,
    summary="Best available price per selection across bookmakers",
    description=(
        "Keyed mode calls /v1/best-odds/{sport_key} (3 credits). Keyless mode "
        "calls /v1/demo/best-odds (0 credits), which returns a truncated sample."
    ),
)
async def best_odds(
    sport: str = Path(..., description="Sport key, e.g. afl, nrl, nba"),
) -> BestOddsResponse:
    sport = sport.lower().strip()
    if sport not in SPORT_KEYS:
        # Refuse locally. Forwarding a typo would spend a credit to be told no.
        raise UpstreamError(
            400,
            {
                "type": "https://github.com/Propertyscout001/racing-data-fastapi/errors/unknown-sport",
                "title": "Unknown sport key",
                "status": 400,
                "detail": f"'{sport}' is not a sport key this feed serves.",
                "valid_sport_keys": sorted(SPORT_KEYS),
                "retryable": False,
            },
        )

    if settings.keyed:
        path = f"/best-odds/{sport}"
        params: Optional[dict] = None
        cost = credit_cost("/best-odds/{sport_key}")
    else:
        path = "/demo/best-odds"
        params = {"sport": sport}
        cost = credit_cost("/demo/best-odds")

    key = f"best-odds:{settings.mode}:{sport}"

    async def fetch() -> Any:
        payload, _headers = await upstream.get_json(path, params=params)
        return payload

    payload, hit, age = await cache.get_or_fetch(key, settings.ttl_best_odds, fetch, cost=cost)
    raw, note = normalise.unwrap(payload, "events")
    events = [normalise.event_from(e, sport) for e in raw]

    return BestOddsResponse(
        source=Source(
            mode=settings.mode,
            cached=hit,
            cache_age_seconds=round(age, 2),
            upstream_path=path,
            credits_charged=0 if hit else cost,
            note=note,
        ),
        sport=sport,
        count=len(events),
        events=events,
    )
