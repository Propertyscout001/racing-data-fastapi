"""Sports best-odds route.

There is no 'horse-racing' sport key -- racing lives under /races/*. The sport
keys this feed serves are listed in SPORT_KEYS below; anything else is refused
here rather than forwarded.

Note what that allowlist is and is NOT for. It is not a cost saving: the
upstream documents that an unknown sport_key returns 404 and costs nothing, so
forwarding a typo is free. It is for the answer the caller gets -- a 400 that
names the twenty valid keys, one round trip sooner than a bare upstream 404
that names none. The trade is that a key PuntersEdge adds after the snapshot
date below is refused by THIS service until the list is updated, which is what
PE_EXTRA_SPORT_KEYS exists to unblock without a code change.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Path

from .. import normalise, upstream
from ..cache import cache
from ..config import credit_cost, settings
from ..models import BestOddsResponse, Source
from ..upstream import UpstreamError

router = APIRouter(tags=["sports"])

# A SNAPSHOT of the upstream sport keys, verified against
# https://api.puntersedge.online/openapi.json on 2026-09-15. It is a local copy
# of someone else's list, so it goes stale the day upstream adds a sport --
# re-check it against that document rather than trusting this file.
SPORT_KEYS = {
    "afl", "aflw", "nrl", "nrlw", "nba", "wnba", "nfl", "ncaaf", "mlb", "nhl",
    "mma", "tennis_atp", "tennis_wta", "cricket_test", "cricket_other",
    "rugby_union", "super_league", "soccer_epl", "soccer_other",
    "basketball_other",
}

# Escape hatch: PE_EXTRA_SPORT_KEYS=padel,darts adds keys at runtime, so a new
# upstream sport does not have to wait for a release of this service.
SPORT_KEYS |= {
    k.strip().lower()
    for k in os.getenv("PE_EXTRA_SPORT_KEYS", "").split(",")
    if k.strip()
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
        # Refused locally for the ANSWER, not for the cost: upstream returns a
        # free 404 for an unknown sport_key. A 400 that lists the valid keys is
        # more use than a 404 that lists none, and it arrives a round trip
        # earlier. Set PE_EXTRA_SPORT_KEYS if upstream has added a sport this
        # snapshot does not know about.
        raise UpstreamError(
            400,
            {
                "type": "https://github.com/Propertyscout001/racing-data-fastapi/errors/unknown-sport",
                "title": "Unknown sport key",
                "status": 400,
                "detail": (
                    f"'{sport}' is not in this service's snapshot of the upstream "
                    "sport keys (taken 2026-09-15). If PuntersEdge has added it "
                    "since, set PE_EXTRA_SPORT_KEYS to allow it through."
                ),
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
