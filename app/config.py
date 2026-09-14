"""Runtime configuration, read from the environment.

The upstream API key is read here and nowhere else. It is attached to outbound
requests inside upstream.py and is never placed in a response body, a log line,
or anything the browser can reach.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Upstream
    upstream_base: str = os.getenv(
        "PE_UPSTREAM_BASE", "https://api.puntersedge.online/v1"
    ).rstrip("/")
    api_key: str = os.getenv("PE_API_KEY", "").strip()

    # Cache TTLs, seconds. Racing prices move faster than a sports futures
    # market, so they get a shorter TTL.
    ttl_races: int = _int_env("CACHE_TTL_RACES", 20)
    ttl_best_odds: int = _int_env("CACHE_TTL_BEST_ODDS", 60)
    ttl_history: int = _int_env("CACHE_TTL_HISTORY", 900)
    ttl_usage: int = _int_env("CACHE_TTL_USAGE", 30)

    # httpx
    timeout_s: float = float(os.getenv("UPSTREAM_TIMEOUT_S", "10"))
    max_keepalive: int = _int_env("UPSTREAM_MAX_KEEPALIVE", 20)
    keepalive_expiry_s: float = float(os.getenv("UPSTREAM_KEEPALIVE_EXPIRY_S", "60"))

    # Default country filter. /v1/racing/next-to-go is NOT Australia-only --
    # omit this and the upstream will happily serve Japanese races under an
    # "Australian racing" heading.
    default_country: str = os.getenv("DEFAULT_COUNTRY", "AU")

    @property
    def keyed(self) -> bool:
        """True when an upstream key is configured; False = keyless demo mode."""
        return bool(self.api_key)

    @property
    def mode(self) -> str:
        return "keyed" if self.keyed else "demo"


settings = Settings()

# Credit cost per successful upstream call, from the published pricing table.
# Used only to report what the cache saved -- the upstream is the authority,
# and every real response carries X-Credits-Used / X-Credits-Remaining.
CREDIT_COST: Dict[str, int] = {
    "/demo/racing/next-to-go": 0,
    "/demo/best-odds": 0,
    "/demo/book-sport": 0,
    "/usage": 0,
    "/racing/next-to-go": 2,
    "/racing/price-history": 5,
    "/best-odds/{sport_key}": 3,
}


def credit_cost(path_template: str) -> int:
    return CREDIT_COST.get(path_template, 0)
