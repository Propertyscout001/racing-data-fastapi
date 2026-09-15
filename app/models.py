"""Response models for this service.

These are deliberately narrower than the upstream payloads. The upstream race
object carries 33 top-level fields; a browser rendering a next-to-go board needs
about eight of them. Narrowing at the edge is what makes the proxy a stable
contract rather than a pass-through that breaks when upstream adds a field.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Price(BaseModel):
    bookmaker: str = Field(..., description="Bookmaker key, e.g. 'sportsbet'")
    win_price: Optional[float] = Field(None, description="Decimal win odds")
    place_price: Optional[float] = Field(None, description="Decimal place odds, when quoted")
    age_seconds: Optional[float] = Field(
        None, description="Seconds since this price was last observed upstream"
    )
    source_url: Optional[str] = Field(None, description="Bookmaker page this price came from")


class Runner(BaseModel):
    name: str
    number: Optional[int] = None
    best_price: Optional[float] = Field(
        None, description="Highest win price across the bookmakers quoting this runner"
    )
    best_bookmaker: Optional[str] = None
    prices: List[Price] = Field(default_factory=list)


class Race(BaseModel):
    race_id: Optional[str] = None
    venue: Optional[str] = None
    race_number: Optional[int] = None
    category: Optional[str] = Field(None, description="horse | harness | greyhound")
    country: Optional[str] = None
    start_time: Optional[str] = Field(None, description="ISO 8601 UTC")
    runners: List[Runner] = Field(default_factory=list)


class Source(BaseModel):
    """Where the payload came from and how stale it is.

    Present on every response so a caller can tell a cached answer from a fresh
    one, and a demo-tier sample from full data, without guessing.
    """
    mode: str = Field(..., description="'demo' (no upstream key) or 'keyed'")
    cached: bool
    cache_age_seconds: float
    upstream_path: str
    credits_charged: int = Field(
        ..., description="Upstream credits this particular request spent (0 on a cache hit)"
    )
    note: Optional[str] = None


class RacesResponse(BaseModel):
    source: Source
    count: int
    races: List[Race]


class RacePricesResponse(BaseModel):
    source: Source
    race: Race


class Selection(BaseModel):
    name: str
    best_price: Optional[float] = None
    best_bookmaker: Optional[str] = None
    quoted_by: int = Field(0, description="How many bookmakers quoted this selection")


class Event(BaseModel):
    id: Optional[str] = None
    sport: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    commence_time: Optional[str] = None
    selections: List[Selection] = Field(default_factory=list)


class BestOddsResponse(BaseModel):
    source: Source
    sport: str
    count: int
    events: List[Event]


class HistoryBook(BaseModel):
    bookmaker: str
    open_price: Optional[float] = None
    close_price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    move_pct: Optional[float] = None
    points_count: Optional[int] = None


class HistoryRunner(BaseModel):
    name: str
    number: Optional[int] = None
    bookmakers: List[HistoryBook] = Field(default_factory=list)


class PriceHistoryResponse(BaseModel):
    source: Source
    race_id: Optional[str] = None
    venue: Optional[str] = None
    race_number: Optional[int] = None
    category: Optional[str] = None
    start_time: Optional[str] = None
    runners: List[HistoryRunner] = Field(default_factory=list)


class CacheBlock(BaseModel):
    requests_served: int
    hits: int
    misses: int
    coalesced: int
    hit_rate: float
    upstream_calls: int
    upstream_errors: int
    credits_spent: int
    credits_avoided_by_cache: int
    live_keys: Dict[str, float]


class HealthResponse(BaseModel):
    status: str = Field(..., description="ok | degraded")
    mode: str
    upstream: str
    upstream_reachable: bool
    upstream_latency_ms: Optional[float] = Field(
        None, description="Round trip of the probe that produced this verdict"
    )
    upstream_detail: Optional[str] = None
    upstream_probe_cached: bool = Field(
        False,
        description=(
            "True when this verdict was reused rather than re-probed. The probe "
            "is cached so liveness-probe frequency does not set upstream request "
            "frequency -- the keyless demo tier allows 30 requests/minute per IP."
        ),
    )
    upstream_probe_age_seconds: float = 0.0
    upstream_probe_ttl_seconds: int = 0
    credits_remaining: Optional[str] = None
    cache: CacheBlock
    ttl_seconds: Dict[str, int]
