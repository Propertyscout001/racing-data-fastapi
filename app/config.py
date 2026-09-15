"""Runtime configuration, read from the environment.

The upstream API key is read here and nowhere else. It is attached to outbound
requests inside upstream.py and is never placed in a response body, a log line,
or anything the browser can reach.

ORDERING MATTERS. The Settings fields below call os.getenv as dataclass field
DEFAULTS, which are evaluated once at class-definition time -- i.e. at import.
Anything that populates os.environ from a .env file therefore has to run above
the class, not below it and not in a startup hook. Getting this backwards is
how a project ends up shipping a .env.example that silently does nothing.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import Dict

ENV_FILE = pathlib.Path(os.getenv("PE_ENV_FILE", ".env"))


def _load_env_file(path: pathlib.Path = ENV_FILE) -> bool:
    """Populate os.environ from a .env file. Real environment variables win.

    Uses python-dotenv when it is installed (it is in requirements.txt) and
    falls back to a small parser so the service still starts if someone
    installed only fastapi/uvicorn/httpx by hand.
    """
    if not path.is_file():
        return False
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path=str(path), override=False)
        return True
    except ImportError:
        pass
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value
    return True


# True when a .env file was found and read. app/main.py reports it at startup
# so "my key is in .env and I am still getting demo data" is never a mystery.
ENV_FILE_LOADED = _load_env_file()


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
    # omit this and the upstream will happily serve Japanese and Hong Kong
    # races under an "Australian racing" heading.
    #
    # The filter cuts BOTH ways, which is the half that is easy to miss. The
    # upstream sets country from the confirmed meeting, so a race whose meeting
    # has not been confirmed yet has country=null and a country filter drops it
    # as well. The upstream's own parameter documentation puts that at 58.8% of
    # horse races in a measured window. include_unresolved=true keeps those
    # races, which is why this service turns it on by default: AU-only WITHOUT
    # it is not "the Australian card", it is the confirmed slice of it.
    default_country: str = os.getenv("DEFAULT_COUNTRY", "AU")
    default_include_unresolved: bool = (
        os.getenv("DEFAULT_INCLUDE_UNRESOLVED", "true").strip().lower()
        not in ("0", "false", "no", "off")
    )

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
