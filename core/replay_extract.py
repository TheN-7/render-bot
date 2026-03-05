from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .replay_unpack_adapter import read_replay, decode_packets, extract_events
from .replay_schema import validate_extraction, to_legacy_schema
from utils.map_names import get_battlearena_entry, get_map_name


def _build_canonical(extraction) -> Dict[str, Any]:
    meta = dict(extraction.meta or {})
    map_id = meta.get("mapId")
    arena_entry = get_battlearena_entry(map_id)
    if arena_entry:
        meta["map_name_resolved"] = arena_entry.get("name")
        meta["map_icon_url"] = arena_entry.get("icon")
        meta["battle_arena_id"] = arena_entry.get("battle_arena_id", map_id)
    else:
        meta["map_name_resolved"] = get_map_name(meta.get("mapDisplayName"), map_id)

    entities: Dict[str, Dict[str, Any]] = {}
    tracks: Dict[str, Dict[str, Any]] = {}

    for entity_id, track in extraction.tracks.items():
        key = str(entity_id)
        entities[key] = {
            "entity_id": entity_id,
            "account_entity_id": track.account_entity_id,
            "player_name": track.player_name,
            "team": track.team,
            "ship_id": track.ship_id,
            "sunk": False,
            "death_time": None,
        }
        tracks[key] = {
            "entity_id": entity_id,
            "player_name": track.player_name,
            "ship_id": track.ship_id,
            "team": track.team,
            "points": [
                {
                    "t": p.t,
                    "x": p.x,
                    "y": p.y,
                    "z": p.z,
                    "yaw": p.yaw,
                    "pitch": p.pitch,
                    "roll": p.roll,
                }
                for p in track.points
            ],
        }

    deaths = []
    for d in extraction.deaths:
        key = str(d.entity_id)
        deaths.append({"entity_key": key, "time_s": d.t})
        if key in entities and (entities[key].get("death_time") is None or d.t < float(entities[key]["death_time"])):
            entities[key]["death_time"] = d.t
            entities[key]["sunk"] = True

    battle_end_s = max((p["t"] for t in tracks.values() for p in t.get("points", [])), default=0.0)

    data = {
        "meta": meta,
        "entities": entities,
        "tracks": tracks,
        "events": {
            "deaths": sorted(deaths, key=lambda item: item["time_s"]),
            "captures": [],
            "fires": [],
            "spotting": [],
        },
        "stats": {
            "tracked_entities": len(tracks),
            "track_points": sum(len(t.get("points", [])) for t in tracks.values()),
            "battle_end_s": battle_end_s,
            "deaths": len(deaths),
        },
        "diagnostics": {
            **extraction.diagnostics,
            "packet_counts": extraction.packet_counts,
        },
    }

    validation = validate_extraction(data)
    data["diagnostics"]["validation"] = {
        "ok": validation.ok,
        "errors": validation.errors,
    }
    return data


def extract_replay(input_replay: str, output_json: Optional[str] = None, emit_legacy: bool = False) -> Dict[str, Any]:
    context = read_replay(input_replay)
    packets = decode_packets(context)
    extraction = extract_events(context, packets)
    canonical = _build_canonical(extraction)

    if output_json:
        out_path = Path(output_json)
        out_path.write_text(json.dumps(canonical, indent=2), encoding="utf-8")

    if emit_legacy:
        legacy = to_legacy_schema(canonical)
        canonical["legacy"] = legacy

    return canonical


def extract_replay_to_files(input_replay: str, canonical_output: str, legacy_output: Optional[str] = None) -> Dict[str, Any]:
    data = extract_replay(input_replay, canonical_output, emit_legacy=bool(legacy_output))
    if legacy_output and "legacy" in data:
        Path(legacy_output).write_text(json.dumps(data["legacy"], indent=2), encoding="utf-8")
    return data
