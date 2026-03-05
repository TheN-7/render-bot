from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .replay_extract import extract_replay
from .replay_schema import to_legacy_schema


def load_canonical_data(path: str) -> Dict[str, Any]:
    src = Path(path)
    if src.suffix.lower() == ".wowsreplay":
        return extract_replay(str(src))
    data = json.loads(src.read_text(encoding="utf-8"))
    if "tracks" in data and "events" in data:
        return data
    # If legacy input is supplied, convert into canonical-like container minimally.
    if "positions" in data:
        return _legacy_to_canonical(data)
    raise ValueError("Unsupported replay input format")


def _legacy_to_canonical(legacy: Dict[str, Any]) -> Dict[str, Any]:
    tracks: Dict[str, Dict[str, Any]] = {}
    entities: Dict[str, Dict[str, Any]] = {}
    for entity_key, points in (legacy.get("positions") or {}).items():
        tracks[entity_key] = {
            "entity_id": int(entity_key) if str(entity_key).isdigit() else entity_key,
            "player_name": f"entity_{entity_key}",
            "ship_id": None,
            "team": "unknown",
            "points": [
                {
                    "t": float(p[0]) if len(p) > 0 else 0.0,
                    "x": float(p[1]) if len(p) > 1 else 0.0,
                    "y": float(p[2]) if len(p) > 2 else 0.0,
                    "z": float(p[3]) if len(p) > 3 else 0.0,
                    "yaw": float(p[4]) if len(p) > 4 else 0.0,
                    "pitch": 0.0,
                    "roll": 0.0,
                }
                for p in points
            ],
        }
        entities[entity_key] = {
            "entity_id": tracks[entity_key]["entity_id"],
            "account_entity_id": None,
            "player_name": tracks[entity_key]["player_name"],
            "team": "unknown",
            "ship_id": None,
            "sunk": False,
            "death_time": None,
        }

    deaths = [{"entity_key": str(d[0]), "time_s": float(d[1])} for d in legacy.get("deaths", []) if isinstance(d, (list, tuple)) and len(d) >= 2]

    return {
        "meta": legacy.get("meta", {}),
        "entities": entities,
        "tracks": tracks,
        "events": {"deaths": deaths, "captures": [], "fires": [], "spotting": []},
        "stats": {"battle_end_s": float(legacy.get("battle_end", 0.0) or 0.0)},
        "diagnostics": {"source": "legacy-converted"},
    }


def canonical_to_track_rows(canonical: Dict[str, Any]) -> Dict[str, List[Tuple[float, float, float, float]]]:
    rows: Dict[str, List[Tuple[float, float, float, float]]] = {}
    for entity_key, track in canonical.get("tracks", {}).items():
        pts = []
        for point in track.get("points", []):
            pts.append((
                float(point.get("t", 0.0)),
                float(point.get("x", 0.0)),
                float(point.get("z", 0.0)),
                float(point.get("yaw", 0.0)),
            ))
        rows[str(entity_key)] = pts
    return rows


def canonical_to_legacy(canonical: Dict[str, Any]) -> Dict[str, Any]:
    return to_legacy_schema(canonical)
