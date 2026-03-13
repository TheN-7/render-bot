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


def _normalize_smoke_timeline(raw_timeline: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw_timeline, list):
        return []
    timeline: list[Dict[str, Any]] = []
    for row in raw_timeline:
        if not isinstance(row, dict):
            continue
        smokes_raw = row.get("smokes", [])
        smokes: list[Dict[str, Any]] = []
        if isinstance(smokes_raw, list):
            for smoke in smokes_raw:
                if not isinstance(smoke, dict):
                    continue
                smokes.append(
                    {
                        "entity_id": _safe_int(smoke.get("entity_id")) or 0,
                        "index": _safe_int(smoke.get("index")) if _safe_int(smoke.get("index")) is not None else -1,
                        "x": float(smoke.get("x", 0.0) or 0.0),
                        "z": float(smoke.get("z", 0.0) or 0.0),
                        "radius": float(smoke.get("radius", 0.0) or 0.0),
                        "height": float(smoke.get("height", 0.0) or 0.0),
                        "active": bool(smoke.get("active", True)),
                    }
                )
        smokes.sort(key=lambda item: (int(item.get("entity_id", 0)), int(item.get("index", 0))))
        timeline.append(
            {
                "time_s": float(row.get("time_s", 0.0) or 0.0),
                "smokes": smokes,
            }
        )
    timeline.sort(key=lambda item: float(item.get("time_s", 0.0)))
    return timeline


def _normalize_smoke_puffs(raw_puffs: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw_puffs, list):
        return []
    puffs: list[Dict[str, Any]] = []
    for puff in raw_puffs:
        if not isinstance(puff, dict):
            continue
        start_time = float(puff.get("start_time", puff.get("time_s", 0.0)) or 0.0)
        duration_s = float(puff.get("duration_s", 0.0) or 0.0)
        end_time = float(puff.get("end_time", start_time + duration_s) or 0.0)
        puffs.append(
            {
                "entity_id": _safe_int(puff.get("entity_id")) or 0,
                "index": _safe_int(puff.get("index")) if _safe_int(puff.get("index")) is not None else -1,
                "x": float(puff.get("x", 0.0) or 0.0),
                "z": float(puff.get("z", 0.0) or 0.0),
                "radius": float(puff.get("radius", 0.0) or 0.0),
                "height": float(puff.get("height", 0.0) or 0.0),
                "start_time": round(start_time, 3),
                "duration_s": round(duration_s, 3),
                "end_time": round(end_time, 3),
            }
        )
    puffs.sort(key=lambda item: (float(item.get("start_time", 0.0)), int(item.get("entity_id", 0)), int(item.get("index", 0))))
    return puffs


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
                "params_id": _safe_int(row.get("params_id")) or -1,
                "shell_kind": str(row.get("shell_kind") or "").strip().lower(),
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


def _team_label_for_team_id(team_id: Optional[int], local_team_id: Optional[int], enemy_team_id: Optional[int]) -> str:
    if team_id is None or team_id < 0:
        return "unknown"
    if local_team_id is not None and team_id == local_team_id:
        return "ally"
    if enemy_team_id is not None and team_id == enemy_team_id:
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


def _normalize_squadrons(raw_events: Any, local_team_id: Optional[int], enemy_team_id: Optional[int]) -> list[Dict[str, Any]]:
    if not isinstance(raw_events, list):
        return []
    events: list[Dict[str, Any]] = []
    for row in raw_events:
        if not isinstance(row, dict):
            continue
        team_id = _safe_int(row.get("team_id"))
        team_label = _team_label_for_team_id(team_id, local_team_id, enemy_team_id)
        events.append(
            {
                "time_s": float(row.get("time_s", 0.0) or 0.0),
                "event": str(row.get("event") or "update"),
                "squadron_id": _safe_int(row.get("squadron_id")) or -1,
                "params_id": _safe_int(row.get("params_id")) or -1,
                "x": float(row.get("x", 0.0) or 0.0) if row.get("x") is not None else None,
                "z": float(row.get("z", 0.0) or 0.0) if row.get("z") is not None else None,
                "team_id": team_id if team_id is not None else -1,
                "team": team_label,
                "team_side": _team_side_for_team_label(team_label),
                "visible": bool(row.get("visible", True)),
            }
        )
    events.sort(key=lambda item: (float(item.get("time_s", 0.0)), int(item.get("squadron_id", -1)), str(item.get("event", ""))))
    return events


def _normalize_kill_feed(raw_kills: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw_kills, list):
        return []
    kills: list[Dict[str, Any]] = []
    for row in raw_kills:
        if not isinstance(row, dict):
            continue
        killer_entity_id = _safe_int(row.get("killer_entity_id"))
        victim_entity_id = _safe_int(row.get("victim_entity_id"))
        if victim_entity_id is None:
            continue
        kills.append(
            {
                "time_s": float(row.get("time_s", 0.0) or 0.0),
                "killer_entity_key": str(killer_entity_id if killer_entity_id is not None else -1),
                "victim_entity_key": str(victim_entity_id),
                "reason_code": _safe_int(row.get("reason_code")) or -1,
                "cause_param_id": _safe_int(row.get("cause_param_id")) or -1,
                "weapon_kind": str(row.get("weapon_kind") or "other"),
                "weapon_label": str(row.get("weapon_label") or "KILL"),
                "shell_kind": str(row.get("shell_kind") or "").strip().lower(),
            }
        )
    kills.sort(key=lambda item: (float(item.get("time_s", 0.0)), str(item.get("victim_entity_key", ""))))
    return kills


def _normalize_chat_feed(raw_chat: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw_chat, list):
        return []
    chat: list[Dict[str, Any]] = []
    for row in raw_chat:
        if not isinstance(row, dict):
            continue
        message = str(row.get("message") or "").strip()
        if not message:
            continue
        chat.append(
            {
                "time_s": float(row.get("time_s", 0.0) or 0.0),
                "sender": str(row.get("sender") or "").strip(),
                "message": message,
            }
        )
    chat.sort(key=lambda item: (float(item.get("time_s", 0.0)), str(item.get("sender", ""))))
    return chat


def _normalize_health_timeline(raw_timeline: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw_timeline, list):
        return []
    timeline: list[Dict[str, Any]] = []
    for row in raw_timeline:
        if not isinstance(row, dict):
            continue
        entities_raw = row.get("entities", {})
        entities: Dict[str, Dict[str, Any]] = {}
        if isinstance(entities_raw, dict):
            for entity_key, state in entities_raw.items():
                if not isinstance(state, dict):
                    continue
                entities[str(entity_key)] = {
                    "hp": max(0, _safe_int(state.get("hp")) or 0),
                    "max_hp": max(0, _safe_int(state.get("max_hp")) or 0),
                    "alive": bool(state.get("alive", True)),
                }
        if not entities:
            continue
        timeline.append(
            {
                "time_s": float(row.get("time_s", 0.0) or 0.0),
                "entities": entities,
            }
        )
    timeline.sort(key=lambda item: float(item.get("time_s", 0.0)))
    return timeline


def _normalize_player_status_timeline(raw_timeline: Any) -> list[Dict[str, Any]]:
    if not isinstance(raw_timeline, list):
        return []
    timeline: list[Dict[str, Any]] = []
    for row in raw_timeline:
        if not isinstance(row, dict):
            continue
        ribbons_raw = row.get("ribbons", {})
        ribbons: Dict[str, int] = {}
        if isinstance(ribbons_raw, dict):
            for ribbon_id, count in ribbons_raw.items():
                rid = _safe_int(ribbon_id)
                cnt = _safe_int(count)
                if rid is None or cnt is None or cnt <= 0:
                    continue
                ribbons[str(rid)] = cnt
        timeline.append(
            {
                "time_s": float(row.get("time_s", 0.0) or 0.0),
                "avatar_entity_id": _safe_int(row.get("avatar_entity_id")) or -1,
                "ship_entity_key": str(_safe_int(row.get("ship_entity_id")) or -1),
                "ship_id": _safe_int(row.get("ship_params_id")) or -1,
                "team_id": _safe_int(row.get("team_id")) if _safe_int(row.get("team_id")) is not None else -1,
                "player_name": str(row.get("player_name") or "").strip(),
                "max_health": max(0, _safe_int(row.get("max_health")) or 0),
                "damage_total": float(row.get("damage_total", 0.0) or 0.0),
                "ribbons": ribbons,
            }
        )
    timeline.sort(key=lambda item: float(item.get("time_s", 0.0)))
    return timeline


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
    smoke_timeline = _normalize_smoke_timeline(battle_state.get("smoke_timeline", []))
    smoke_puffs = _normalize_smoke_puffs(battle_state.get("smoke_puffs", []))
    artillery_fires = _normalize_artillery_fires(battle_state.get("artillery_shots", []))
    torpedo_points = _normalize_torpedo_points(battle_state.get("torpedo_points", []), owner_team)
    kill_feed = _normalize_kill_feed(battle_state.get("kill_feed", []))
    chat_feed = _normalize_chat_feed(battle_state.get("chat_messages", []))
    health_timeline = _normalize_health_timeline(battle_state.get("health_timeline", []))
    player_status_timeline = _normalize_player_status_timeline(battle_state.get("player_status_timeline", []))
    control_points = _normalize_control_points(battle_state.get("control_points", []))
    local_team_id = _safe_int(battle_state.get("local_team_id"))
    enemy_team_id = _safe_int(battle_state.get("enemy_team_id"))
    squadron_events = _normalize_squadrons(battle_state.get("squadrons", []), local_team_id, enemy_team_id)
    player_status_meta = battle_state.get("player_status_meta", {}) if isinstance(battle_state.get("player_status_meta"), dict) else {}

    if control_points:
        meta["control_points"] = control_points
    if local_team_id is not None:
        meta["local_team_id"] = local_team_id
    if enemy_team_id is not None:
        meta["enemy_team_id"] = enemy_team_id
    player_avatar_entity_id = _safe_int(player_status_meta.get("avatar_entity_id"))
    player_ship_entity_id = _safe_int(player_status_meta.get("ship_entity_id"))
    player_ship_id = _safe_int(player_status_meta.get("ship_params_id"))
    if player_avatar_entity_id is not None and player_avatar_entity_id >= 0:
        meta["player_avatar_entity_id"] = player_avatar_entity_id
    if player_ship_entity_id is not None and player_ship_entity_id >= 0:
        meta["player_ship_entity_id"] = player_ship_entity_id
    if player_ship_id is not None and player_ship_id >= 0:
        meta["player_ship_id"] = player_ship_id

    for snap in health_timeline:
        entities_raw = snap.get("entities", {})
        if not isinstance(entities_raw, dict):
            continue
        for entity_key, state in entities_raw.items():
            if entity_key not in entities or not isinstance(state, dict):
                continue
            max_hp = max(0, _safe_int(state.get("max_hp")) or 0)
            hp = max(0, _safe_int(state.get("hp")) or 0)
            if max_hp > 0:
                entities[entity_key]["max_hp"] = max_hp
            entities[entity_key]["initial_hp"] = max(entities[entity_key].get("initial_hp", 0), hp)

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
            "smokes": smoke_timeline,
            "smoke_puffs": smoke_puffs,
            "fires": artillery_fires,
            "kills": kill_feed,
            "chat": chat_feed,
            "health": health_timeline,
            "player_status": player_status_timeline,
            "spotting": [],
            "torpedoes": torpedo_points,
            "squadrons": squadron_events,
        },
        "stats": {
            "tracked_entities": len(tracks),
            "track_points": sum(len(t.get("points", [])) for t in tracks.values()),
            "battle_end_s": battle_end_s,
            "deaths": len(deaths),
            "kills": len(kill_feed),
            "chat_messages": len(chat_feed),
            "health_snapshots": len(health_timeline),
            "player_status_samples": len(player_status_timeline),
            "artillery_shots": len(artillery_fires),
            "torpedo_points": len(torpedo_points),
            "squadron_events": len(squadron_events),
            "smoke_snapshots": len(smoke_timeline),
            "smoke_puffs": len(smoke_puffs) if smoke_puffs else sum(len(s.get("smokes", [])) for s in smoke_timeline),
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
