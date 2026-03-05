from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


CANONICAL_KEYS = {"meta", "entities", "tracks", "events", "stats", "diagnostics"}
VALID_TEAMS = {"player", "ally", "enemy", "unknown"}


@dataclass
class ValidationResult:
    ok: bool
    errors: List[str]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_extraction(data: Dict[str, Any]) -> ValidationResult:
    errors: List[str] = []

    missing = [k for k in CANONICAL_KEYS if k not in data]
    if missing:
        errors.append(f"Missing canonical keys: {', '.join(sorted(missing))}")

    tracks = data.get("tracks", {})
    if not isinstance(tracks, dict):
        errors.append("tracks must be an object")
        return ValidationResult(False, errors)

    for entity_key, track in tracks.items():
        if not isinstance(track, dict):
            errors.append(f"tracks.{entity_key} must be an object")
            continue
        team = track.get("team", "unknown")
        if team not in VALID_TEAMS:
            errors.append(f"tracks.{entity_key}.team invalid: {team}")

        points = track.get("points", [])
        if not isinstance(points, list):
            errors.append(f"tracks.{entity_key}.points must be a list")
            continue

        previous_t = None
        for idx, p in enumerate(points):
            if not isinstance(p, dict):
                errors.append(f"tracks.{entity_key}.points[{idx}] must be an object")
                continue
            if "t" not in p:
                errors.append(f"tracks.{entity_key}.points[{idx}] missing t")
                continue
            t = float(p["t"])
            if previous_t is not None and t < previous_t:
                errors.append(f"tracks.{entity_key}.points not time-ordered at index {idx}")
            previous_t = t

    events = data.get("events", {})
    deaths = events.get("deaths", []) if isinstance(events, dict) else []
    if not isinstance(deaths, list):
        errors.append("events.deaths must be a list")

    return ValidationResult(ok=len(errors) == 0, errors=errors)


def to_legacy_schema(canonical: Dict[str, Any]) -> Dict[str, Any]:
    meta = canonical.get("meta", {})
    tracks = canonical.get("tracks", {})
    events = canonical.get("events", {})
    entities = canonical.get("entities", {})

    legacy_positions: Dict[str, List[List[float]]] = {}
    legacy_vehicles: List[Dict[str, Any]] = []

    for entity_key, track in tracks.items():
        points = []
        for p in track.get("points", []):
            points.append([
                float(p.get("t", 0.0)),
                float(p.get("x", 0.0)),
                float(p.get("y", 0.0)),
                float(p.get("z", 0.0)),
                float(p.get("yaw", 0.0)),
            ])
        legacy_positions[entity_key] = points

    for entity_key, info in entities.items():
        legacy_vehicles.append(
            {
                "entity_id": _safe_int(entity_key),
                "meta_eid": info.get("account_entity_id"),
                "name": info.get("player_name", f"entity_{entity_key}"),
                "ship_id": info.get("ship_id"),
                "team": info.get("team", "unknown"),
                "relation": info.get("team", "unknown"),
                "sunk": bool(info.get("sunk", False)),
                "death_clock": info.get("death_time"),
                "first_pos": points[0][1:4] if (points := legacy_positions.get(entity_key)) else None,
                "last_pos": points[-1][1:4] if (points := legacy_positions.get(entity_key)) else None,
                "max_hp": 0,
                "damage_taken": 0,
            }
        )

    deaths = []
    for d in events.get("deaths", []):
        deaths.append((_safe_int(d.get("entity_key")), float(d.get("time_s", 0.0))))

    battle_end = canonical.get("stats", {}).get("battle_end_s")
    if battle_end is None:
        battle_end = float(meta.get("duration", 0) or 0)

    return {
        "meta": meta,
        "ships": {},
        "vehicles": legacy_vehicles,
        "teams": {},
        "positions": legacy_positions,
        "deaths": deaths,
        "battle_end": battle_end,
        "capture_pts": [],
        "damage_stats": {},
        "stats": canonical.get("stats", {}),
        "extraction_note": "Converted from canonical replay schema",
    }
