from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .replay_unpack_adapter import read_replay, decode_packets, extract_events
from .replay_schema import validate_extraction, to_legacy_schema
from utils.map_names import get_battlearena_entry, get_map_name


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_control_points(raw_points: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw_points, list):
        return []
    points: list[Dict[str, Any]] = []
    for row in raw_points:
        if not isinstance(row, dict):
            continue
        points.append(
            {
                "entity_id": _safe_int(row.get("entity_id")) or 0,
                "index": _safe_int(row.get("index")) if _safe_int(row.get("index")) is not None else -1,
                "x": float(row.get("x", 0.0) or 0.0),
                "z": float(row.get("z", 0.0) or 0.0),
                "radius": float(row.get("radius", 0.0) or 0.0),
                "capture_time_s": float(row.get("capture_time_s", 0.0) or 0.0),
            }
        )
    points.sort(key=lambda item: (int(item.get("index", -1)), int(item.get("entity_id", 0))))
    return points


def _normalize_capture_timeline(raw_timeline: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw_timeline, list):
        return []
    timeline: list[Dict[str, Any]] = []
    for row in raw_timeline:
        if not isinstance(row, dict):
            continue
        caps_raw = row.get("caps", [])
        caps: list[Dict[str, Any]] = []
        if isinstance(caps_raw, list):
            for cap in caps_raw:
                if not isinstance(cap, dict):
                    continue
                cap_team_id = _safe_int(cap.get("team_id"))
                cap_owner_id = _safe_int(cap.get("owner_team_id"))
                cap_invader_id = _safe_int(cap.get("invader_team_id"))
                caps.append(
                    {
                        "entity_id": _safe_int(cap.get("entity_id")) or 0,
                        "index": _safe_int(cap.get("index")) if _safe_int(cap.get("index")) is not None else -1,
                        "x": float(cap.get("x", 0.0) or 0.0),
                        "z": float(cap.get("z", 0.0) or 0.0),
                        "radius": float(cap.get("radius", 0.0) or 0.0),
                        "progress": max(0.0, min(1.0, float(cap.get("progress", 0.0) or 0.0))),
                        "capture_time_s": float(cap.get("capture_time_s", 0.0) or 0.0),
                        "capture_speed": float(cap.get("capture_speed", 0.0) or 0.0),
                        "team_id": cap_team_id if cap_team_id is not None else -1,
                        "owner_team_id": cap_owner_id if cap_owner_id is not None else -1,
                        "invader_team_id": cap_invader_id if cap_invader_id is not None else -1,
                        "has_invaders": bool(cap.get("has_invaders", False)),
                        "both_inside": bool(cap.get("both_inside", False)),
                        "is_enabled": bool(cap.get("is_enabled", True)),
                        "is_visible": bool(cap.get("is_visible", True)),
                    }
                )
        caps.sort(key=lambda item: (int(item.get("index", -1)), int(item.get("entity_id", 0))))

        scores: Dict[str, int] = {}
        team_scores_raw = row.get("team_scores", {})
        if isinstance(team_scores_raw, dict):
            for k, v in team_scores_raw.items():
                team_id = _safe_int(k)
                score = _safe_int(v)
                if team_id is None or score is None:
                    continue
                scores[str(team_id)] = score

        timeline.append(
            {
                "time_s": float(row.get("time_s", 0.0) or 0.0),
                "team_scores": scores,
                "team_win_score": _safe_int(row.get("team_win_score")) or 0,
                "caps": caps,
            }
        )
    timeline.sort(key=lambda item: float(item.get("time_s", 0.0)))
    return timeline


def _normalize_artillery_fires(raw_fires: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw_fires, list):
        return []
    fires: list[Dict[str, Any]] = []
    for row in raw_fires:
        if not isinstance(row, dict):
            continue
        t0 = float(row.get("time_s", 0.0) or 0.0)
        t1 = float(row.get("time_end_s", t0) or t0)
        if t1 < t0:
            t1 = t0
        fires.append(
            {
                "kind": "artillery_trace",
                "shooter_entity_key": str(_safe_int(row.get("shooter_entity_id")) or -1),
                "shot_id": _safe_int(row.get("shot_id")) or -1,
                "time_s": t0,
                "time_end_s": t1,
                "x0": float(row.get("x0", 0.0) or 0.0),
                "z0": float(row.get("z0", 0.0) or 0.0),
                "x1": float(row.get("x1", 0.0) or 0.0),
                "z1": float(row.get("z1", 0.0) or 0.0),
            }
        )
    fires.sort(key=lambda item: (float(item.get("time_s", 0.0)), int(item.get("shot_id", -1))))
    return fires


def _team_side_for_team_label(team_label: Any) -> str:
    s = str(team_label or "").lower()
    if s in ("player", "ally"):
        return "friendly"
    if s == "enemy":
        return "enemy"
    return "unknown"


def _normalize_torpedo_points(raw_points: Any, owner_team: Dict[str, str]) -> list[Dict[str, Any]]:
    if not isinstance(raw_points, list):
        return []
    points: list[Dict[str, Any]] = []
    for row in raw_points:
        if not isinstance(row, dict):
            continue
        owner_id = _safe_int(row.get("owner_entity_id"))
        owner_key = str(owner_id) if owner_id is not None else "-1"
        team_label = owner_team.get(owner_key, "unknown")
        points.append(
            {
                "owner_entity_key": owner_key,
                "torpedo_id": _safe_int(row.get("torpedo_id")) or -1,
                "time_s": float(row.get("time_s", 0.0) or 0.0),
                "x": float(row.get("x", 0.0) or 0.0),
                "z": float(row.get("z", 0.0) or 0.0),
                "team": team_label,
                "team_side": _team_side_for_team_label(team_label),
            }
        )
    points.sort(
        key=lambda item: (
            float(item.get("time_s", 0.0)),
            str(item.get("owner_entity_key", "")),
            int(item.get("torpedo_id", -1)),
        )
    )
    return points


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
    owner_team = {str(entity_id): str(track.team or "unknown") for entity_id, track in extraction.tracks.items()}

    deaths = []
    for d in extraction.deaths:
        key = str(d.entity_id)
        deaths.append({"entity_key": key, "time_s": d.t})
        if key in entities and (entities[key].get("death_time") is None or d.t < float(entities[key]["death_time"])):
            entities[key]["death_time"] = d.t
            entities[key]["sunk"] = True

    battle_end_s = max((p["t"] for t in tracks.values() for p in t.get("points", [])), default=0.0)

    battle_state = extraction.battle_state or {}
    captures_timeline = _normalize_capture_timeline(battle_state.get("captures_timeline", []))
    artillery_fires = _normalize_artillery_fires(battle_state.get("artillery_shots", []))
    torpedo_points = _normalize_torpedo_points(battle_state.get("torpedo_points", []), owner_team)
    control_points = _normalize_control_points(battle_state.get("control_points", []))
    local_team_id = _safe_int(battle_state.get("local_team_id"))
    enemy_team_id = _safe_int(battle_state.get("enemy_team_id"))

    if control_points:
        meta["control_points"] = control_points
    if local_team_id is not None:
        meta["local_team_id"] = local_team_id
    if enemy_team_id is not None:
        meta["enemy_team_id"] = enemy_team_id

    final_scores: Dict[str, int] = {}
    final_scores_raw = battle_state.get("final_scores", {})
    if isinstance(final_scores_raw, dict):
        for key, value in final_scores_raw.items():
            team_id = _safe_int(key)
            score = _safe_int(value)
            if team_id is None or score is None:
                continue
            final_scores[str(team_id)] = score

    team_win_score = _safe_int(battle_state.get("team_win_score")) or 0

    data = {
        "meta": meta,
        "entities": entities,
        "tracks": tracks,
        "events": {
            "deaths": sorted(deaths, key=lambda item: item["time_s"]),
            "captures": captures_timeline,
            "fires": artillery_fires,
            "spotting": [],
            "torpedoes": torpedo_points,
        },
        "stats": {
            "tracked_entities": len(tracks),
            "track_points": sum(len(t.get("points", [])) for t in tracks.values()),
            "battle_end_s": battle_end_s,
            "deaths": len(deaths),
            "artillery_shots": len(artillery_fires),
            "torpedo_points": len(torpedo_points),
            "team_scores_final": final_scores,
            "team_win_score": team_win_score,
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
