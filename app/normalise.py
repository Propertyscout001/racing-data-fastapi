"""Fold the two upstream response shapes into one.

The keyless demo endpoints return an envelope:

    {"demo": true, "note": "...", "shape": "...", "races": [...]}

The keyed endpoints return a BARE ARRAY:

    [ {...race...}, {...race...} ]

Anything that supports both modes has to handle both, and the place to do that
is once, here, rather than in every route and again in the browser. Everything
downstream of this module sees a list.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .models import Event, HistoryBook, HistoryRunner, Price, Race, Runner, Selection


def unwrap(payload: Any, envelope_key: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Return (items, note) for either shape.

    `envelope_key` is 'races' or 'events' depending on the endpoint.
    """
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        items = payload.get(envelope_key)
        if isinstance(items, list):
            return items, payload.get("note")
        # Some demo responses answer an unknown filter with an empty envelope.
        return [], payload.get("note")
    return [], None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def race_from(raw: Dict[str, Any]) -> Race:
    runners: List[Runner] = []
    for r in raw.get("runners") or []:
        prices: List[Price] = []
        for b in r.get("bookmakers") or []:
            prices.append(
                Price(
                    bookmaker=b.get("key") or b.get("bookmaker") or "unknown",
                    win_price=_num(b.get("win_price")),
                    place_price=_num(b.get("place_price")),
                    age_seconds=_num(b.get("age_seconds")),
                    source_url=b.get("source_url"),
                )
            )
        priced = [p for p in prices if p.win_price is not None]
        best = max(priced, key=lambda p: p.win_price) if priced else None
        runners.append(
            Runner(
                name=r.get("name") or "?",
                number=r.get("number"),
                best_price=best.win_price if best else None,
                best_bookmaker=best.bookmaker if best else None,
                # Longest price first: that is the order a comparison table wants.
                prices=sorted(prices, key=lambda p: (p.win_price is None, -(p.win_price or 0))),
            )
        )
    return Race(
        race_id=raw.get("race_id"),
        venue=raw.get("venue_canonical") or raw.get("venue"),
        race_number=raw.get("race_number"),
        category=raw.get("category"),
        country=raw.get("country"),
        start_time=raw.get("start_time"),
        runners=runners,
    )


def event_from(raw: Dict[str, Any], sport: str) -> Event:
    selections: List[Selection] = []
    for s in raw.get("selections") or []:
        # all_prices is keyed-mode only; the demo envelope omits it.
        quoted = len(s.get("all_prices") or [])
        selections.append(
            Selection(
                name=s.get("name") or "?",
                best_price=_num(s.get("best_price")),
                best_bookmaker=s.get("best_bookmaker"),
                quoted_by=quoted,
            )
        )
    return Event(
        id=raw.get("id"),
        sport=raw.get("sport_key") or sport,
        home_team=raw.get("home_team"),
        away_team=raw.get("away_team"),
        commence_time=raw.get("commence_time"),
        selections=selections,
    )


def history_runners_from(raw: Dict[str, Any]) -> List[HistoryRunner]:
    out: List[HistoryRunner] = []
    for r in raw.get("runners") or []:
        books = [
            HistoryBook(
                bookmaker=b.get("key") or "unknown",
                open_price=_num(b.get("open_price")),
                close_price=_num(b.get("close_price")),
                high=_num(b.get("high")),
                low=_num(b.get("low")),
                move_pct=_num(b.get("move_pct")),
                points_count=b.get("points_count"),
            )
            for b in (r.get("bookmakers") or [])
        ]
        out.append(
            HistoryRunner(name=r.get("name") or "?", number=r.get("number"), bookmakers=books)
        )
    return out
