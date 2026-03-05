from __future__ import annotations

import sys
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ensure_vendor_path() -> None:
    root = Path(__file__).resolve().parent.parent
    vendor = root / "vendor"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))


_ensure_vendor_path()

from replay_unpack.replay_reader import ReplayReader  # type: ignore
from replay_unpack.core.network.net_packet import NetPacket  # type: ignore
from replay_unpack.core.entity import Entity  # type: ignore
from replay_unpack.clients.wows.network.packets import (  # type: ignore
    PACKETS_MAPPING,
    PACKETS_MAPPING_12_6,
    EntityCreate,
    Position,
    PlayerPosition,
    EntityMethod,
)
from replay_unpack.clients.wows.player import ReplayPlayer as WowsReplayPlayer  # type: ignore


@dataclass
class ReplayContext:
    path: str
    game: str
    engine_data: Dict[str, Any]
    extra_data: List[Any]
    decrypted_data: bytes
    version: List[str]


@dataclass
class TrackPoint:
    t: float
    x: float
    y: float
    z: float
    yaw: float
    pitch: float = 0.0
    roll: float = 0.0


@dataclass
class ShipTrack:
    entity_id: int
    account_entity_id: Optional[int]
    player_name: str
    team: str
    ship_id: Optional[int]
    points: List[TrackPoint] = field(default_factory=list)


@dataclass
class DeathEvent:
    entity_id: int
    t: float


@dataclass
class ReplayExtraction:
    meta: Dict[str, Any]
    tracks: Dict[int, ShipTrack]
    deaths: List[DeathEvent]
    packet_counts: Dict[str, int]
    diagnostics: Dict[str, Any]
    battle_state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DecodedPacket:
    time: float
    packet_type: int
    packet_name: str
    packet_obj: Any


class ReplayDecodeError(RuntimeError):
    pass


def read_replay(path: str) -> ReplayContext:
    reader = ReplayReader(path)
    replay = reader.get_replay_data()
    if replay.game != "wows":
        raise ReplayDecodeError(f"Unsupported replay game type: {replay.game}")

    version_raw = replay.engine_data.get("clientVersionFromXml") or replay.engine_data.get("clientVersionFromExe")
    if not version_raw:
        raise ReplayDecodeError("Replay metadata missing client version")
    version = str(version_raw).replace(" ", "").split(",")

    return ReplayContext(
        path=path,
        game=replay.game,
        engine_data=replay.engine_data,
        extra_data=replay.extra_data,
        decrypted_data=replay.decrypted_data,
        version=version,
    )


def _packet_mapping(version: List[str]) -> Dict[int, Any]:
    major_minor_patch = tuple(int(x) for x in (version + ["0", "0", "0"])[:3])
    if major_minor_patch >= (12, 6, 0):
        mapping = dict(PACKETS_MAPPING_12_6)
    else:
        mapping = dict(PACKETS_MAPPING)

    # WoWS 15.1.x shifted local-player position updates from 0x2b to 0x2c.
    # Keep 0x2b mapping untouched and add 0x2c for compatibility.
    if major_minor_patch >= (15, 1, 0):
        mapping[0x2C] = PlayerPosition

    return mapping


def decode_packets(context: ReplayContext) -> List[DecodedPacket]:
    mapping = _packet_mapping(context.version)
    data = context.decrypted_data
    stream = BytesIO(data)
    decoded: List[DecodedPacket] = []

    while stream.tell() < len(data):
        packet = NetPacket(stream)
        packet_cls = mapping.get(packet.type)
        packet_obj = packet_cls(packet.raw_data) if packet_cls else None
        packet_name = packet_cls.__name__ if packet_cls else f"TYPE_{packet.type}"
        decoded.append(
            DecodedPacket(
                time=packet.time,
                packet_type=packet.type,
                packet_name=packet_name,
                packet_obj=packet_obj,
            )
        )

    return decoded


def _normalize_team(relation: Any) -> str:
    mapping = {0: "player", 1: "ally", 2: "enemy", "player": "player", "ally": "ally", "enemy": "enemy"}
    return mapping.get(relation, "unknown")


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _iter_values(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    try:
        return list(value)
    except Exception:
        return []


def _vec_xz(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    try:
        return float(value[0]), float(value[2])
    except Exception:
        return None


def _median_value(values: List[float]) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    mid = len(arr) // 2
    if len(arr) % 2 == 0:
        return (arr[mid - 1] + arr[mid]) / 2.0
    return arr[mid]


def _filter_main_artillery_shots(
    shots: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int, int, Dict[int, set[int]], Dict[int, set[int]]]:
    if not shots:
        return [], 0, 0, {}, {}

    grouped: Dict[tuple[int, int], List[Dict[str, Any]]] = {}
    for row in shots:
        shooter = _safe_int(row.get("shooter_entity_id"))
        params_id = _safe_int(row.get("params_id"))
        if shooter is None:
            shooter = -1
        if params_id is None:
            params_id = -1
        grouped.setdefault((shooter, params_id), []).append(row)

    secondary_groups: set[tuple[int, int]] = set()
    for group_key, rows in grouped.items():
        burst_counts = [max(1, _safe_int(r.get("pack_shot_count")) or 1) for r in rows]
        avg_burst = sum(burst_counts) / max(1, len(burst_counts))

        unique_times = sorted({round(float(r.get("time_s", 0.0)), 3) for r in rows})
        intervals = [unique_times[i] - unique_times[i - 1] for i in range(1, len(unique_times)) if unique_times[i] > unique_times[i - 1]]
        med_interval = _median_value(intervals) if intervals else 999.0
        fast_intervals = sum(1 for d in intervals if d <= 1.6)
        fast_ratio = fast_intervals / max(1, len(intervals))

        # Secondary batteries usually fire many single-shell bursts with short cadence.
        is_secondary = (
            (len(unique_times) >= 8 and avg_burst <= 1.5 and (med_interval <= 1.8 or fast_ratio >= 0.55))
            or (len(unique_times) >= 12 and avg_burst <= 2.0 and med_interval <= 2.0 and fast_ratio >= 0.50)
        )
        if is_secondary:
            secondary_groups.add(group_key)

    filtered: List[Dict[str, Any]] = []
    main_params_by_owner: Dict[int, set[int]] = {}
    all_params_by_owner: Dict[int, set[int]] = {}
    for group_key, rows in grouped.items():
        owner_id, params_id = group_key
        all_params_by_owner.setdefault(owner_id, set()).add(params_id)
        if group_key in secondary_groups:
            continue
        main_params_by_owner.setdefault(owner_id, set()).add(params_id)
        filtered.extend(rows)

    filtered.sort(key=lambda item: (float(item.get("time_s", 0.0)), int(item.get("shot_id", -1))))
    dropped = max(0, len(shots) - len(filtered))
    return filtered, len(secondary_groups), dropped, main_params_by_owner, all_params_by_owner


def _infer_kill_weapon_kind(
    reason_code: Optional[int],
    cause_param_id: Optional[int],
    killer_entity_id: Optional[int],
    main_artillery_params: Dict[int, set[int]],
    all_artillery_params: Dict[int, set[int]],
    torpedo_params: Dict[int, set[int]],
) -> str:
    if killer_entity_id is not None and cause_param_id is not None:
        if cause_param_id in torpedo_params.get(killer_entity_id, set()):
            return "torpedo"
        if cause_param_id in main_artillery_params.get(killer_entity_id, set()):
            return "gun"
        if cause_param_id in all_artillery_params.get(killer_entity_id, set()):
            return "gun"

    fallback = {
        17: "gun",
        18: "gun",
        2: "gun",
        3: "torpedo",
        13: "torpedo",
        28: "bomb",
    }
    if reason_code in fallback:
        return fallback[reason_code]
    return "other"


def _norm_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _get_battle_logic_entity(entities: Dict[int, Any]) -> Any:
    for entity in entities.values():
        try:
            if entity.get_name() == "BattleLogic":
                return entity
        except Exception:
            continue
    return None


def _snapshot_battle_state(
    entities: Dict[int, Any],
    cap_positions: Dict[int, Dict[str, Any]],
    time_s: float,
) -> Dict[str, Any] | None:
    battle_logic = _get_battle_logic_entity(entities)
    if battle_logic is None:
        return None

    client = battle_logic.properties.get("client", {}) if hasattr(battle_logic, "properties") else {}
    state = client.get("state", {}) if isinstance(client, dict) else {}
    if not isinstance(state, dict):
        return None

    cp_ids = []
    for value in state.get("controlPoints", []) or []:
        cid = _safe_int(value)
        if cid is not None:
            cp_ids.append(cid)

    missions = state.get("missions", {}) or {}
    teams_score_raw = missions.get("teamsScore", []) if isinstance(missions, dict) else []
    team_scores: Dict[str, int] = {}
    if isinstance(teams_score_raw, list):
        for row in teams_score_raw:
            if not isinstance(row, dict):
                continue
            team_id = _safe_int(row.get("teamId"))
            score = _safe_int(row.get("score"))
            if team_id is None or score is None:
                continue
            team_scores[str(team_id)] = score

    caps: List[Dict[str, Any]] = []
    for cid in cp_ids:
        zone = entities.get(cid)
        zone_client = zone.properties.get("client", {}) if (zone is not None and hasattr(zone, "properties")) else {}
        if not isinstance(zone_client, dict):
            zone_client = {}
        components = zone_client.get("componentsState", {}) or {}
        if not isinstance(components, dict):
            components = {}
        control_point = components.get("controlPoint", {}) or {}
        capture_logic = components.get("captureLogic", {}) or {}
        if not isinstance(control_point, dict):
            control_point = {}
        if not isinstance(capture_logic, dict):
            capture_logic = {}

        pos = cap_positions.get(cid, {})
        progress = _safe_float(capture_logic.get("progress"), 0.0)
        progress = max(0.0, min(1.0, progress))

        caps.append(
            {
                "entity_id": cid,
                "index": _safe_int(control_point.get("index")) if _safe_int(control_point.get("index")) is not None else -1,
                "x": _safe_float(pos.get("x"), 0.0),
                "z": _safe_float(pos.get("z"), 0.0),
                "radius": _safe_float(zone_client.get("radius"), 0.0),
                "owner_team_id": _safe_int(zone_client.get("ownerId")) if _safe_int(zone_client.get("ownerId")) is not None else -1,
                "team_id": _safe_int(zone_client.get("teamId")) if _safe_int(zone_client.get("teamId")) is not None else -1,
                "progress": round(progress, 4),
                "capture_time_s": _safe_float(capture_logic.get("captureTime"), 0.0),
                "capture_speed": _safe_float(capture_logic.get("captureSpeed"), 0.0),
                "invader_team_id": _safe_int(capture_logic.get("invaderTeam")) if _safe_int(capture_logic.get("invaderTeam")) is not None else -1,
                "has_invaders": bool(capture_logic.get("hasInvaders", 0)),
                "both_inside": bool(capture_logic.get("bothInside", 0)),
                "is_enabled": bool(capture_logic.get("isEnabled", 1)),
                "is_visible": bool(capture_logic.get("isVisible", 1)),
            }
        )

    caps.sort(key=lambda v: (int(v.get("index", -1)), int(v.get("entity_id", 0))))

    team_win_score = _safe_int(missions.get("teamWinScore")) if isinstance(missions, dict) else None
    return {
        "time_s": round(float(time_s), 3),
        "team_scores": team_scores,
        "team_win_score": team_win_score if team_win_score is not None else 0,
        "caps": caps,
    }


def _extract_battle_overlay(
    context: ReplayContext,
    packets: List[DecodedPacket],
    local_team_id: Optional[int],
) -> Dict[str, Any]:
    replay_player = WowsReplayPlayer(context.version)
    cap_positions: Dict[int, Dict[str, Any]] = {}
    timeline: List[Dict[str, Any]] = []
    artillery_shots: List[Dict[str, Any]] = []
    torpedo_points: List[Dict[str, Any]] = []
    seen_shots: set[tuple[int, int]] = set()
    seen_torp_points: set[tuple[int, int, float, float, float]] = set()
    packet_time_ref = [0.0]
    next_sample_t = 0.0
    max_time = float(packets[-1].time) if packets else 0.0
    subscriptions_added: List[tuple[str, List[Any], Any]] = []

    def _subscribe_method(method_hash: str, callback: Any) -> None:
        subscriptions = Entity._methods_subscriptions.get(method_hash)
        if subscriptions is None:
            subscriptions = []
            Entity._methods_subscriptions[method_hash] = subscriptions
        subscriptions.append(callback)
        subscriptions_added.append((method_hash, subscriptions, callback))

    def _on_artillery_shots(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        shot_packs = _iter_values(args[0]) if args else []
        t_fire = float(packet_time_ref[0])
        for pack in shot_packs:
            if not isinstance(pack, dict):
                continue
            owner_id = _safe_int(pack.get("ownerID"))
            shots = _iter_values(pack.get("shots", []))
            for shot in shots:
                if not isinstance(shot, dict):
                    continue
                shot_id = _safe_int(shot.get("shotID"))
                if owner_id is not None and shot_id is not None:
                    key = (owner_id, shot_id)
                    if key in seen_shots:
                        continue
                    seen_shots.add(key)

                start = _vec_xz(shot.get("pos"))
                target = _vec_xz(shot.get("tarPos"))
                if start is None or target is None:
                    continue
                x0, z0 = start
                x1, z1 = target

                flight_s = _safe_float(shot.get("serverTimeLeft"), 0.0)
                if flight_s <= 0.0:
                    hit_distance = _safe_float(shot.get("hitDistance"), 0.0)
                    speed = _safe_float(shot.get("speed"), 0.0)
                    if hit_distance > 0.0 and speed > 0.0:
                        flight_s = hit_distance / speed
                flight_s = min(45.0, max(0.15, flight_s))

                artillery_shots.append(
                    {
                        "shooter_entity_id": owner_id if owner_id is not None else -1,
                        "shot_id": shot_id if shot_id is not None else -1,
                        "time_s": round(t_fire, 3),
                        "time_end_s": round(t_fire + flight_s, 3),
                        "x0": round(x0, 3),
                        "z0": round(z0, 3),
                        "x1": round(x1, 3),
                        "z1": round(z1, 3),
                    }
                )

    def _append_torpedo_point(owner_id: Optional[int], torpedo_id: Optional[int], pos: tuple[float, float] | None) -> None:
        if pos is None:
            return
        x, z = pos
        t = round(float(packet_time_ref[0]), 3)
        oid = owner_id if owner_id is not None else -1
        tid = torpedo_id if torpedo_id is not None else -1
        dedup_key = (oid, tid, t, round(x, 2), round(z, 2))
        if dedup_key in seen_torp_points:
            return
        seen_torp_points.add(dedup_key)
        torpedo_points.append(
            {
                "owner_entity_id": oid,
                "torpedo_id": tid,
                "time_s": t,
                "x": round(float(x), 3),
                "z": round(float(z), 3),
            }
        )

    def _on_torpedoes(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        packs = _iter_values(args[0]) if args else []
        for pack in packs:
            if not isinstance(pack, dict):
                continue
            owner_id = _safe_int(pack.get("ownerID"))
            for torpedo in _iter_values(pack.get("torpedoes", [])):
                if not isinstance(torpedo, dict):
                    continue
                torpedo_id = _safe_int(torpedo.get("shotID"))
                pos = _vec_xz(torpedo.get("pos"))
                _append_torpedo_point(owner_id, torpedo_id, pos)

    def _on_torpedo_direction(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        if len(args) < 3:
            return
        owner_id = _safe_int(args[0])
        torpedo_id = _safe_int(args[1])
        pos = _vec_xz(args[2])
        _append_torpedo_point(owner_id, torpedo_id, pos)

    _subscribe_method("Avatar_receiveArtilleryShots", _on_artillery_shots)
    _subscribe_method("Avatar_receiveTorpedoes", _on_torpedoes)
    _subscribe_method("Avatar_receiveTorpedoDirection", _on_torpedo_direction)
    try:
        for p in packets:
            packet_time_ref[0] = float(p.time)
            if p.packet_obj is None:
                while float(p.time) >= next_sample_t:
                    snap = _snapshot_battle_state(replay_player._battle_controller.entities, cap_positions, next_sample_t)
                    if snap is not None:
                        timeline.append(snap)
                    next_sample_t += 1.0
                continue

            try:
                replay_player._process_packet(float(p.time), p.packet_obj)
            except Exception:
                # Keep extraction resilient to malformed/unsupported packets.
                pass

            if isinstance(p.packet_obj, EntityCreate):
                entity_id = _safe_int(getattr(p.packet_obj, "entityID", None))
                if entity_id is not None:
                    entity = replay_player._battle_controller.entities.get(entity_id)
                    try:
                        is_zone = entity is not None and entity.get_name() == "InteractiveZone"
                    except Exception:
                        is_zone = False
                    if is_zone:
                        pos = getattr(p.packet_obj, "position", None)
                        if pos is not None:
                            cap_positions[entity_id] = {
                                "entity_id": entity_id,
                                "x": round(float(pos.x), 3),
                                "z": round(float(pos.z), 3),
                            }

            while float(p.time) >= next_sample_t:
                snap = _snapshot_battle_state(replay_player._battle_controller.entities, cap_positions, next_sample_t)
                if snap is not None:
                    timeline.append(snap)
                next_sample_t += 1.0
    finally:
        for method_hash, subscriptions, callback in subscriptions_added:
            try:
                subscriptions.remove(callback)
            except ValueError:
                pass
            if not subscriptions:
                Entity._methods_subscriptions.pop(method_hash, None)

    if timeline and timeline[-1].get("time_s", 0.0) < max_time:
        final_snap = _snapshot_battle_state(replay_player._battle_controller.entities, cap_positions, max_time)
        if final_snap is not None:
            timeline.append(final_snap)

    # Keep only state-changing snapshots.
    filtered: List[Dict[str, Any]] = []
    last_key = None
    for snap in timeline:
        caps = snap.get("caps", []) if isinstance(snap, dict) else []
        scores = snap.get("team_scores", {}) if isinstance(snap, dict) else {}
        if not isinstance(caps, list):
            caps = []
        if not isinstance(scores, dict):
            scores = {}
        state_key = (
            tuple(sorted((str(k), int(v)) for k, v in scores.items())),
            tuple(
                (
                    int(c.get("entity_id", 0)),
                    int(c.get("invader_team_id", -1)),
                    int(c.get("owner_team_id", -1)),
                    int(bool(c.get("has_invaders", False))),
                    round(_safe_float(c.get("progress"), 0.0), 3),
                )
                for c in caps
            ),
        )
        if state_key != last_key:
            filtered.append(snap)
            last_key = state_key

    layout_by_id: Dict[int, Dict[str, Any]] = {}
    for snap in filtered:
        for cap in snap.get("caps", []):
            cid = _safe_int(cap.get("entity_id"))
            if cid is None:
                continue
            if cid in layout_by_id:
                continue
            layout_by_id[cid] = {
                "entity_id": cid,
                "index": _safe_int(cap.get("index")) if _safe_int(cap.get("index")) is not None else -1,
                "x": _safe_float(cap.get("x"), 0.0),
                "z": _safe_float(cap.get("z"), 0.0),
                "radius": _safe_float(cap.get("radius"), 0.0),
                "capture_time_s": _safe_float(cap.get("capture_time_s"), 0.0),
            }

    for cid, pos in cap_positions.items():
        if cid in layout_by_id:
            continue
        layout_by_id[cid] = {
            "entity_id": cid,
            "index": -1,
            "x": _safe_float(pos.get("x"), 0.0),
            "z": _safe_float(pos.get("z"), 0.0),
            "radius": 0.0,
            "capture_time_s": 0.0,
        }

    control_points = sorted(layout_by_id.values(), key=lambda v: (int(v.get("index", -1)), int(v.get("entity_id", 0))))
    final_scores: Dict[str, int] = {}
    team_win_score = 0
    if filtered:
        tail = filtered[-1]
        raw_scores = tail.get("team_scores", {})
        if isinstance(raw_scores, dict):
            for k, v in raw_scores.items():
                if _safe_int(v) is None:
                    continue
                final_scores[str(k)] = int(v)
        team_win_score = int(tail.get("team_win_score", 0) or 0)

    enemy_team_id: Optional[int] = None
    if local_team_id is not None:
        for key in sorted(final_scores.keys()):
            tid = _safe_int(key)
            if tid is None:
                continue
            if tid != local_team_id:
                enemy_team_id = tid
                break

    return {
        "captures_timeline": filtered,
        "control_points": control_points,
        "final_scores": final_scores,
        "team_win_score": team_win_score,
        "local_team_id": local_team_id,
        "enemy_team_id": enemy_team_id,
        "artillery_shots": sorted(artillery_shots, key=lambda item: (float(item.get("time_s", 0.0)), int(item.get("shot_id", -1)))),
        "torpedo_points": sorted(
            torpedo_points,
            key=lambda item: (
                float(item.get("time_s", 0.0)),
                int(item.get("owner_entity_id", -1)),
                int(item.get("torpedo_id", -1)),
            ),
        ),
    }


def _build_session_map_from_player_info(context: ReplayContext) -> Dict[int, Dict[str, Any]]:
    meta = context.engine_data
    vehicles = meta.get("vehicles", []) or []

    vehicles_by_account: Dict[int, Dict[str, Any]] = {}
    vehicles_by_name: Dict[str, Dict[str, Any]] = {}
    local_account_id: Optional[int] = None
    local_name = _norm_name(meta.get("playerName"))
    for vehicle in vehicles:
        account_id = _safe_int(vehicle.get("id"))
        if account_id is not None:
            vehicles_by_account[account_id] = vehicle
        nm = _norm_name(vehicle.get("name"))
        if nm:
            vehicles_by_name[nm] = vehicle
        if _safe_int(vehicle.get("relation")) == 0 and account_id is not None:
            local_account_id = account_id

    replay_player = WowsReplayPlayer(context.version)
    replay_player.play(context.decrypted_data)
    info = replay_player.get_info()
    players_blob = info.get("players", {})
    if isinstance(players_blob, dict):
        players = list(players_blob.values())
    elif isinstance(players_blob, list):
        players = list(players_blob)
    else:
        players = []
    if not players:
        return {}

    local_team_id: Optional[int] = None
    for row in players:
        row_account = _safe_int(row.get("id"))
        if local_account_id is not None and row_account == local_account_id:
            local_team_id = _safe_int(row.get("teamId"))
            break
    if local_team_id is None and local_name:
        for row in players:
            if _norm_name(row.get("name")) == local_name:
                local_team_id = _safe_int(row.get("teamId"))
                break

    session_map: Dict[int, Dict[str, Any]] = {}
    for row in players:
        ship_entity_id = _safe_int(row.get("shipId"))
        if ship_entity_id is None:
            continue

        row_account = _safe_int(row.get("id"))
        row_name = str(row.get("name") or f"entity_{ship_entity_id}")
        row_team_id = _safe_int(row.get("teamId"))
        relation = None

        if row_account is not None:
            meta_vehicle = vehicles_by_account.get(row_account)
            if meta_vehicle is not None:
                relation = _safe_int(meta_vehicle.get("relation"))
        if relation is None:
            meta_vehicle = vehicles_by_name.get(_norm_name(row_name))
            if meta_vehicle is not None:
                relation = _safe_int(meta_vehicle.get("relation"))
        if relation is None and row_team_id is not None and local_team_id is not None:
            if local_account_id is not None and row_account == local_account_id:
                relation = 0
            elif row_team_id == local_team_id:
                relation = 1
            else:
                relation = 2

        ship_params_id = _safe_int(row.get("shipParamsId"))
        if ship_params_id is None and row_account is not None:
            meta_vehicle = vehicles_by_account.get(row_account)
            if meta_vehicle is not None:
                ship_params_id = _safe_int(meta_vehicle.get("shipId"))

        session_map[ship_entity_id] = {
            "id": row_account,
            "name": row_name,
            "shipId": ship_params_id,
            "relation": relation if relation is not None else "unknown",
            "teamId": row_team_id,
            "avatarId": _safe_int(row.get("avatarId")),
        }

    return session_map


def _build_session_map_heuristic(meta: Dict[str, Any], packets: List[DecodedPacket]) -> Dict[int, Dict[str, Any]]:
    vehicles = meta.get("vehicles", []) or []
    seen_eids = sorted({p.packet_obj.entityId for p in packets if isinstance(p.packet_obj, Position)})
    if not seen_eids or not vehicles:
        return {}

    min_eid = min(seen_eids)
    max_eid = max(seen_eids)
    all_sess = list(range(min_eid, max_eid + 3, 2))
    sorted_vehicles = sorted(vehicles, key=lambda v: v.get("id", 0))
    return {sess: vehicle for sess, vehicle in zip(all_sess, sorted_vehicles)}


def _build_session_map(context: ReplayContext, packets: List[DecodedPacket]) -> tuple[Dict[int, Dict[str, Any]], str]:
    try:
        by_player_info = _build_session_map_from_player_info(context)
        if by_player_info:
            return by_player_info, "replay_player"
    except Exception:
        # Fall back to legacy heuristic map to keep extraction resilient.
        pass
    return _build_session_map_heuristic(context.engine_data, packets), "heuristic"


def extract_events(context: ReplayContext, packets: List[DecodedPacket]) -> ReplayExtraction:
    meta = context.engine_data
    session_map, session_map_source = _build_session_map(context, packets)

    tracks: Dict[int, ShipTrack] = {}
    deaths: List[DeathEvent] = []
    packet_counts: Dict[str, int] = {}

    player_session_id = next((eid for eid, v in session_map.items() if _safe_int(v.get("relation")) == 0), None)
    local_team_id = next((_safe_int(v.get("teamId")) for v in session_map.values() if _safe_int(v.get("relation")) == 0), None)

    for p in packets:
        packet_counts[p.packet_name] = packet_counts.get(p.packet_name, 0) + 1

        if isinstance(p.packet_obj, Position):
            eid = int(p.packet_obj.entityId)
            x = float(p.packet_obj.position.x)
            y = float(p.packet_obj.position.y)
            z = float(p.packet_obj.position.z)
            if abs(x) <= 0.5 and abs(z) <= 0.5:
                continue

            vehicle = session_map.get(eid, {})
            team = _normalize_team(vehicle.get("relation"))
            track = tracks.setdefault(
                eid,
                ShipTrack(
                    entity_id=eid,
                    account_entity_id=vehicle.get("id"),
                    player_name=vehicle.get("name", f"entity_{eid}"),
                    team=team,
                    ship_id=vehicle.get("shipId"),
                ),
            )
            track.points.append(
                TrackPoint(
                    t=round(float(p.time), 3),
                    x=round(x, 3),
                    y=round(y, 3),
                    z=round(z, 3),
                    yaw=round(float(p.packet_obj.yaw), 6),
                    pitch=round(float(p.packet_obj.pitch), 6),
                    roll=round(float(p.packet_obj.roll), 6),
                )
            )

        elif isinstance(p.packet_obj, PlayerPosition):
            if player_session_id is None:
                continue
            x = float(p.packet_obj.position.x)
            y = float(p.packet_obj.position.y)
            z = float(p.packet_obj.position.z)
            if abs(x) <= 0.5 and abs(z) <= 0.5:
                continue

            player_meta = session_map.get(player_session_id, {})
            track = tracks.setdefault(
                player_session_id,
                ShipTrack(
                    entity_id=player_session_id,
                    account_entity_id=player_meta.get("id"),
                    player_name=player_meta.get("name", meta.get("playerName", "player")),
                    team="player",
                    ship_id=player_meta.get("shipId"),
                ),
            )
            track.points.append(
                TrackPoint(
                    t=round(float(p.time), 3),
                    x=round(x, 3),
                    y=round(y, 3),
                    z=round(z, 3),
                    yaw=round(float(p.packet_obj.yaw), 6),
                    pitch=round(float(p.packet_obj.pitch), 6),
                    roll=round(float(p.packet_obj.roll), 6),
                )
            )

        elif isinstance(p.packet_obj, EntityMethod):
            if int(p.packet_obj.messageId) == 0:
                deaths.append(DeathEvent(entity_id=int(p.packet_obj.entityId), t=round(float(p.time), 3)))

    for track in tracks.values():
        track.points.sort(key=lambda item: item.t)

    battle_state = _extract_battle_overlay(context, packets, local_team_id)
    diagnostics = {
        "session_map_size": len(session_map),
        "session_map_source": session_map_source,
        "player_session_id": player_session_id,
        "local_team_id": local_team_id,
        "captures_timeline": len(battle_state.get("captures_timeline", [])),
        "control_points": len(battle_state.get("control_points", [])),
        "artillery_shots": len(battle_state.get("artillery_shots", [])),
        "torpedo_points": len(battle_state.get("torpedo_points", [])),
        "packet_total": sum(packet_counts.values()),
        "client_version": ".".join(context.version),
    }

    return ReplayExtraction(
        meta=meta,
        tracks=tracks,
        deaths=deaths,
        packet_counts=packet_counts,
        diagnostics=diagnostics,
        battle_state=battle_state,
    )
