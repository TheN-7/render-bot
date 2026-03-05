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
from replay_unpack.clients.wows.network.packets import (  # type: ignore
    PACKETS_MAPPING,
    PACKETS_MAPPING_12_6,
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


def _norm_name(value: Any) -> str:
    return str(value or "").strip().lower()


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

    diagnostics = {
        "session_map_size": len(session_map),
        "session_map_source": session_map_source,
        "player_session_id": player_session_id,
        "packet_total": sum(packet_counts.values()),
        "client_version": ".".join(context.version),
    }

    return ReplayExtraction(
        meta=meta,
        tracks=tracks,
        deaths=deaths,
        packet_counts=packet_counts,
        diagnostics=diagnostics,
    )
