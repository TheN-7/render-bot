from __future__ import annotations

import sys
import math
import os
import json
import builtins
import pickle
import pickletools
import re
import statistics
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


_CONSUMABLE_PARAMS_CACHE: Optional[Dict[str, Any]] = None
_SHIPS_CACHE: Optional[Dict[str, Any]] = None
_MIN_CONSUMABLE_SCORE = 5


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


def _coerce_blob(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, BytesIO):
        return value.getvalue()
    return None


class _RestrictedUnpickler(pickle.Unpickler):
    _ALLOWED = {
        "dict",
        "list",
        "tuple",
        "set",
        "frozenset",
        "str",
        "bytes",
        "bytearray",
        "int",
        "float",
        "bool",
        "NoneType",
    }

    def find_class(self, module: str, name: str) -> Any:
        if module == "builtins" and name in self._ALLOWED:
            return getattr(builtins, name)
        raise pickle.UnpicklingError(f"global '{module}.{name}' is forbidden")


def _safe_unpickle(value: Any) -> Any:
    data = _coerce_blob(value)
    if data is None:
        return None
    try:
        return _RestrictedUnpickler(BytesIO(data)).load()
    except Exception:
        return None


def _collect_text_tokens(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(str(k).lower())
            if isinstance(v, (str, bytes)):
                out.add(str(v).lower())
            else:
                _collect_text_tokens(v, out)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _collect_text_tokens(v, out)

def _infer_consumable_kind_from_tokens(tokens: set[str]) -> Optional[str]:
    if any("radar" in t for t in tokens):
        return "radar"
    if any(token in t for t in tokens for token in ("hydro", "hydroacoustic", "acoustic", "sonar")):
        return "hydro"
    if any("smoke" in t for t in tokens):
        return "smoke"
    if any(token in t for t in tokens for token in ("speed", "boost", "engine", "speedbooster")):
        return "engine"
    if any(token in t for t in tokens for token in ("regen", "repair", "heal", "recover")):
        return "heal"
    return None


def _infer_consumable_kind(value: Any) -> Optional[str]:
    tokens: set[str] = set()
    _collect_text_tokens(value, tokens)
    return _infer_consumable_kind_from_tokens(tokens)


def _collect_range_candidates(value: Any, out: List[float]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k).lower()
            if isinstance(v, (int, float)) and any(token in key for token in ("range", "radius", "distance", "dist", "detect", "spot", "search")):
                out.append(float(v))
            else:
                _collect_range_candidates(v, out)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _collect_range_candidates(v, out)


def _coerce_range_m(value: float) -> Optional[float]:
    if value <= 0.0:
        return None
    if value <= 60.0:
        value = value * 1000.0
    if value < 500.0 or value > 30000.0:
        return None
    return float(value)


def _infer_range_m(value: Any) -> Optional[float]:
    candidates: List[float] = []
    _collect_range_candidates(value, candidates)
    if not candidates:
        return None
    meters = [_coerce_range_m(v) for v in candidates]
    meters = [v for v in meters if v is not None]
    if not meters:
        return None
    return float(max(meters))


def _infer_range_from_numbers(numbers: List[float]) -> Optional[float]:
    if not numbers:
        return None
    meters: List[float] = []
    for num in numbers:
        if not isinstance(num, (int, float)):
            continue
        val = float(num)
        if val <= 0.0:
            continue
        maybe = _coerce_range_m(val)
        if maybe is not None:
            meters.append(maybe)
    if not meters:
        return None
    # Prefer typical radar/hydro ranges.
    plausible = [v for v in meters if 4000.0 <= v <= 14000.0]
    if plausible:
        return float(max(plausible))
    return float(max(meters))


def _scan_pickled_blob(value: Any) -> tuple[set[str], List[float]]:
    data = _coerce_blob(value)
    if data is None:
        return set(), []
    tokens: set[str] = set()
    numbers: List[float] = []
    try:
        for op, arg, _pos in pickletools.genops(data):
            name = op.name
            if name in ("SHORT_BINUNICODE", "BINUNICODE", "UNICODE", "BINSTRING", "SHORT_BINSTRING", "STRING"):
                if isinstance(arg, bytes):
                    try:
                        s = arg.decode("utf-8", errors="ignore")
                    except Exception:
                        s = ""
                else:
                    s = str(arg or "")
                if s:
                    tokens.add(s.lower())
            elif name in ("BININT", "BININT1", "BININT2", "LONG", "LONG1", "LONG4", "BINFLOAT"):
                try:
                    numbers.append(float(arg))
                except Exception:
                    continue
    except Exception:
        return set(), []
    return tokens, numbers


def _collect_duration_candidates(value: Any, out: List[float]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k).lower()
            if isinstance(v, (int, float)) and any(token in key for token in ("worktime", "duration", "lifetime", "timeleft", "active")):
                out.append(float(v))
            else:
                _collect_duration_candidates(v, out)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _collect_duration_candidates(v, out)


def _infer_duration_s(value: Any) -> Optional[float]:
    candidates: List[float] = []
    _collect_duration_candidates(value, candidates)
    if not candidates:
        return None
    candidates = [v for v in candidates if v > 0.0]
    if not candidates:
        return None
    plausible = [v for v in candidates if 5.0 <= v <= 240.0]
    if plausible:
        return float(max(plausible))
    return float(max(candidates))


def _safe_median(values: List[float]) -> Optional[float]:
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    try:
        return float(statistics.median(vals))
    except Exception:
        return float(vals[len(vals) // 2])


def _load_gameparams_consumables() -> Dict[str, Any]:
    global _CONSUMABLE_PARAMS_CACHE
    if _CONSUMABLE_PARAMS_CACHE is not None:
        return _CONSUMABLE_PARAMS_CACHE
    root = Path(__file__).resolve().parent.parent
    path = root / "content" / "gameparams_consumables.json"
    if not path.exists():
        _CONSUMABLE_PARAMS_CACHE = {}
        return _CONSUMABLE_PARAMS_CACHE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _CONSUMABLE_PARAMS_CACHE = {}
        return _CONSUMABLE_PARAMS_CACHE

    matches = data.get("matches", []) if isinstance(data, dict) else []
    radar_variants: List[Dict[str, Any]] = []
    sonar_variants: List[Dict[str, Any]] = []

    def _parse_variant_key(key: str) -> Dict[str, Any]:
        tokens = [t for t in key.split("_") if t]
        info: Dict[str, Any] = {}
        if not tokens:
            return info
        class_codes = {"DD", "CL", "CA", "BB", "CV", "SS"}
        head = tokens[0]
        head_upper = head.upper()
        # Pattern: NATION_TIER (e.g., Deutsche_11)
        if len(tokens) == 2 and tokens[1].isdigit():
            info["nation_code"] = head_upper
            info["tier_min"] = int(tokens[1])
            info["tier_max"] = int(tokens[1])
            return info
        # Pattern: NATIONCLASS_TIER_... (e.g., UKDD_6_10_Jupiter_Test)
        for cls in class_codes:
            if head_upper.endswith(cls):
                nation_code = head_upper[: -len(cls)]
                if nation_code:
                    info["nation_code"] = nation_code
                info["class_code"] = cls
        if len(tokens) > 1 and tokens[1].isdigit():
            info["tier_min"] = int(tokens[1])
            info["tier_max"] = int(tokens[1])
        return info
        # Pattern: NATION_TIER_CLASS_... (e.g., US_8_CL_CLEV)
        if len(tokens) >= 3 and tokens[1].isdigit():
            info["nation_code"] = head_upper
            info["tier_min"] = int(tokens[1])
            info["tier_max"] = int(tokens[1])
            if tokens[2].upper() in class_codes:
                info["class_code"] = tokens[2].upper()
            return info
        # Pattern: NATION_CLASS_TIER_... (e.g., USSR_CA_8_10)
        if len(tokens) >= 3 and tokens[1].upper() in class_codes and tokens[2].isdigit():
            info["nation_code"] = head_upper
            info["class_code"] = tokens[1].upper()
            info["tier_min"] = int(tokens[2])
            info["tier_max"] = int(tokens[2])
            if len(tokens) >= 4 and tokens[3].isdigit():
                info["tier_max"] = int(tokens[3])
            return info
        # Fallback: at least capture nation code when obvious.
        info["nation_code"] = head_upper
        return info

    for entry in matches:
        if not isinstance(entry, dict):
            continue
        path_str = str(entry.get("path") or "")
        # Skip non-standard variants.
        if "/PXY" in path_str or "ModernEra" in path_str:
            continue
        if path_str not in ("/PCY016_SonarSearchPremium", "/PCY020_RLSSearchPremium"):
            continue
        payload = entry.get("data")
        if not isinstance(payload, dict):
            continue
        for key, val in payload.items():
            if key in ("canBuy", "typeinfo", "costCR", "freeOfCharge", "id", "index"):
                continue
            if not isinstance(val, dict):
                continue
            consumable_type = str(val.get("consumableType") or "").lower()
            if consumable_type not in ("rls", "sonar"):
                continue
            logic = val.get("logic", {}) if isinstance(val.get("logic"), dict) else {}
            dist_ship = _safe_float(logic.get("distShip"), None)
            dist_torp = _safe_float(logic.get("distTorpedo"), None)
            work_time = _safe_float(val.get("workTime"), None)
            reload_time = _safe_float(val.get("reloadTime"), None)
            variant = {
                "key": key,
                "consumable_type": consumable_type,
                "dist_ship": dist_ship,
                "dist_torp": dist_torp,
                "work_time": work_time,
                "reload_time": reload_time,
                # Range is stored in the same world units as replay coordinates (1 unit ≈ 30m).
                "range_m": float(dist_ship) if dist_ship is not None else None,
                "torp_range_m": float(dist_torp) if dist_torp is not None else None,
            }
            variant.update(_parse_variant_key(key))
            if consumable_type == "rls":
                radar_variants.append(variant)
            else:
                sonar_variants.append(variant)

    radar_ranges = [v["range_m"] for v in radar_variants if v.get("range_m")]
    radar_durations = [v["work_time"] for v in radar_variants if v.get("work_time")]
    sonar_ranges = [v["range_m"] for v in sonar_variants if v.get("range_m")]
    sonar_durations = [v["work_time"] for v in sonar_variants if v.get("work_time")]

    _CONSUMABLE_PARAMS_CACHE = {
        "radar_variants": radar_variants,
        "sonar_variants": sonar_variants,
        "radar_default_range_m": _safe_median(radar_ranges),
        "radar_default_duration_s": _safe_median(radar_durations),
        "sonar_default_range_m": _safe_median(sonar_ranges),
        "sonar_default_duration_s": _safe_median(sonar_durations),
    }
    return _CONSUMABLE_PARAMS_CACHE


def _load_ships_cache() -> Dict[str, Any]:
    global _SHIPS_CACHE
    if _SHIPS_CACHE is not None:
        return _SHIPS_CACHE
    path = Path(__file__).resolve().parent.parent / "ships_cache.json"
    if not path.exists():
        _SHIPS_CACHE = {}
        return _SHIPS_CACHE
    try:
        _SHIPS_CACHE = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _SHIPS_CACHE = {}
    return _SHIPS_CACHE


def _normalize_ship_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name).upper())


def _score_consumable_variant(variant: Dict[str, Any], ship_info: Dict[str, Any]) -> int:
    score = 0
    nation = str(ship_info.get("nation") or "").lower()
    tier = _safe_int(ship_info.get("tier"))
    ship_type = str(ship_info.get("type") or "")
    ship_name = _normalize_ship_name(ship_info.get("name") or "")
    variant_key = _normalize_ship_name(variant.get("key") or "")

    nation_codes = {
        "usa": {"US"},
        "ussr": {"USSR"},
        "uk": {"GB", "UK"},
        "germany": {"DE", "GER", "KM", "DEUTSCHE"},
        "japan": {"JP", "IJN"},
        "france": {"FR"},
        "italy": {"IT"},
        "europe": {"EU"},
        "pan_asia": {"PAZ"},
        "pan_america": {"PAM"},
        "common": set(),
    }
    v_nation = str(variant.get("nation_code") or "").upper()
    if nation in nation_codes and v_nation in nation_codes[nation]:
        score += 2
    tier_min = variant.get("tier_min")
    tier_max = variant.get("tier_max")
    if tier is not None and tier_min is not None and tier_max is not None:
        try:
            if int(tier_min) <= int(tier) <= int(tier_max):
                score += 2
        except Exception:
            pass
    class_code = str(variant.get("class_code") or "").upper()
    if ship_type == "Cruiser" and class_code in {"CL", "CA"}:
        score += 1
    elif ship_type == "Destroyer" and class_code == "DD":
        score += 1
    elif ship_type == "Battleship" and class_code == "BB":
        score += 1
    elif ship_type == "AirCarrier" and class_code == "CV":
        score += 1
    elif ship_type == "Submarine" and class_code == "SS":
        score += 1
    if ship_name and ship_name in variant_key:
        # Exact ship-name match should dominate.
        if ship_name == variant_key:
            score += 6
        else:
            score += 4
    else:
        # Try short token match (first 4 letters of each word)
        parts = re.findall(r"[A-Z0-9]+", str(ship_info.get("name") or "").upper())
        for part in parts:
            if len(part) >= 4 and part[:4] in variant_key:
                score += 2
                break
    return score


def _choose_consumable_variant(kind: str, ship_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cache = _load_gameparams_consumables()
    if not cache:
        return None
    variants = cache.get("radar_variants") if kind == "radar" else cache.get("sonar_variants")
    if not variants:
        return None
    best = None
    best_score = -1
    for variant in variants:
        score = _score_consumable_variant(variant, ship_info)
        if score > best_score:
            best = variant
            best_score = score
    if not best:
        return None
    # Allow slightly lower score when the variant only encodes nation+tier (no class/name).
    min_score = _MIN_CONSUMABLE_SCORE
    if not best.get("class_code") and not best.get("key", "").upper().startswith("STAR"):
        if best.get("tier_min") is not None and best.get("nation_code"):
            min_score = min(min_score, 4)
    if best_score < min_score:
        return None
    return best


def _fallback_consumable_params(kind: str, ship_info: Dict[str, Any]) -> Dict[str, Optional[float]]:
    # No defaults: only use ship-specific data from GameParams.
    return {"range_m": None, "duration_s": None, "reload_s": None}



def _collect_consumable_type(value: Any) -> Optional[int]:
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k).lower()
            if isinstance(v, (int, float)) and ("consumable" in key and ("type" in key or "id" in key)):
                return int(v)
            nested = _collect_consumable_type(v)
            if nested is not None:
                return nested
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            nested = _collect_consumable_type(v)
            if nested is not None:
                return nested
    return None


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
        if hasattr(value, "x") and hasattr(value, "z"):
            return float(value.x), float(value.z)
        if isinstance(value, dict):
            x = value.get("x") if "x" in value else value.get("X")
            z = value.get("z") if "z" in value else value.get("Z")
            if x is not None and z is not None:
                return float(x), float(z)
        return float(value[0]), float(value[2])
    except Exception:
        return None


def _vec_xy(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    try:
        if hasattr(value, "x") and hasattr(value, "y"):
            return float(value.x), float(value.y)
        if isinstance(value, dict):
            x = value.get("x") if "x" in value else value.get("X")
            y = value.get("y") if "y" in value else value.get("Y")
            if x is not None and y is not None:
                return float(x), float(y)
        return float(value[0]), float(value[1])
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


def _shell_kind_from_reason(reason_code: Optional[int]) -> Optional[str]:
    return {
        17: "ap",
        18: "he",
        19: "cs",
    }.get(reason_code)


def _kill_weapon_label(reason_code: Optional[int], weapon_kind: str, shell_kind: Optional[str]) -> str:
    labels = {
        2: "ATBA",
        3: "TORP",
        4: "BOMB",
        6: "FIRE",
        7: "RAM",
        9: "FLOOD",
        13: "DEPTH",
        14: "RKT",
        17: "AP",
        18: "HE",
        19: "CS",
        22: "SKIP",
        28: "ADBOMB",
    }
    if reason_code in labels:
        return labels[reason_code]
    if shell_kind == "ap":
        return "AP"
    if shell_kind == "he":
        return "HE"
    if shell_kind == "cs":
        return "CS"
    generic = {
        "gun": "GUN",
        "torpedo": "TORP",
        "bomb": "BOMB",
        "other": "KILL",
    }
    return generic.get(weapon_kind, "KILL")


def _infer_shell_kinds_for_params(
    vehicle_kills: List[Dict[str, Any]],
    main_artillery_params: Dict[int, set[int]],
) -> Dict[int, str]:
    votes_by_param: Dict[int, Dict[str, int]] = {}
    for row in vehicle_kills:
        killer_entity_id = _safe_int(row.get("killer_entity_id"))
        cause_param_id = _safe_int(row.get("cause_param_id"))
        reason_code = _safe_int(row.get("reason_code"))
        shell_kind = _shell_kind_from_reason(reason_code)
        if killer_entity_id is None or cause_param_id is None or cause_param_id < 0 or shell_kind is None:
            continue
        if cause_param_id not in main_artillery_params.get(killer_entity_id, set()):
            continue
        votes = votes_by_param.setdefault(cause_param_id, {})
        votes[shell_kind] = votes.get(shell_kind, 0) + 1

    kind_by_param: Dict[int, str] = {}
    for param_id, votes in votes_by_param.items():
        best_kind = max(sorted(votes.keys()), key=lambda key: votes[key])
        kind_by_param[param_id] = best_kind

    # If a ship exposes two main-gun ammo params and one is identified from a kill,
    # the other is usually the complementary shell type.
    for params in main_artillery_params.values():
        cleaned = sorted(param_id for param_id in params if param_id >= 0)
        if len(cleaned) != 2:
            continue
        known = {param_id: kind_by_param[param_id] for param_id in cleaned if param_id in kind_by_param}
        if len(known) != 1:
            continue
        known_param, known_kind = next(iter(known.items()))
        if known_kind not in ("ap", "he"):
            continue
        other_param = next((param_id for param_id in cleaned if param_id != known_param), None)
        if other_param is None or other_param in kind_by_param:
            continue
        kind_by_param[other_param] = "he" if known_kind == "ap" else "ap"

    return kind_by_param


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


def _snapshot_smoke_state(entities: Dict[int, Any], time_s: float) -> Dict[str, Any] | None:
    smokes: List[Dict[str, Any]] = []
    seen: set[tuple[int, int, float, float, float]] = set()
    for entity in entities.values():
        try:
            if entity.get_name() != "SmokeScreen":
                continue
        except Exception:
            continue
        props = entity.properties if hasattr(entity, "properties") else {}
        client = props.get("client", {}) if isinstance(props, dict) else {}
        cell = props.get("cell", {}) if isinstance(props, dict) else {}
        base = props.get("base", {}) if isinstance(props, dict) else {}
        prop_sources = [client, cell, base]

        def _first_prop(keys: tuple[str, ...]) -> Any:
            for key in keys:
                for src in prop_sources:
                    if isinstance(src, dict) and key in src:
                        return src.get(key)
            return None

        duration_s = 0.0
        end_time = None
        duration_keys = (
            "lifeTime",
            "life_time",
            "lifeDuration",
            "life_duration",
            "duration",
            "cloudLifeTime",
            "cloudDuration",
            "cloud_lifetime",
            "smokeLifeTime",
            "smokeDuration",
            "timeLeft",
            "time_left",
            "remainingTime",
            "remaining_time",
            "lifeTimeLeft",
            "life_time_left",
            "cloudTimeLeft",
            "smokeTimeLeft",
            "smoke_time_left",
        )
        value = _safe_float(_first_prop(duration_keys))
        if value is not None and value > 0.0:
            duration_s = float(value)

        end_keys = ("endTime", "end_time", "expireTime", "expire_time", "deathTime", "death_time")
        end_value = _safe_float(_first_prop(end_keys))
        if end_value is not None and end_value > float(time_s):
            end_time = float(end_value)
            if duration_s <= 0.0 or (end_time - float(time_s)) < duration_s:
                duration_s = max(0.0, end_time - float(time_s))

        points = _first_prop(("points", "Points"))
        if not isinstance(points, list) or not points:
            continue
        radius = _safe_float(_first_prop(("radius", "Radius")), 0.0)
        bc_radius = _safe_float(_first_prop(("bcRadius", "BCRadius")), 0.0)
        if radius <= 0.0 and bc_radius > 0.0:
            radius = bc_radius
        height = _safe_float(_first_prop(("height", "Height")), 0.0)
        active_idx = _safe_int(_first_prop(("activePointIndex", "active_point_index", "activeIdx", "active_index")))
        active_flag = _first_prop(("isActive", "active", "isVisible", "visible"))
        for idx, point in enumerate(points):
            pos = _vec_xz(point)
            if pos is None:
                continue
            x, z = pos
            point_duration = 0.0
            point_start = None
            point_end = None
            if isinstance(point, dict):
                for key in duration_keys:
                    value = _safe_float(point.get(key))
                    if value is not None and value > 0.0:
                        point_duration = float(value)
                        break
                for key in ("time", "time_s", "startTime", "start_time", "spawnTime", "spawn_time"):
                    value = _safe_float(point.get(key))
                    if value is not None and value >= 0.0:
                        point_start = float(value)
                        break
                for key in end_keys:
                    value = _safe_float(point.get(key))
                    if value is not None and value > float(time_s):
                        point_end = float(value)
                        if point_duration <= 0.0 or (point_end - float(time_s)) < point_duration:
                            point_duration = max(0.0, point_end - float(time_s))
                        break
            key = (int(getattr(entity, "id", 0) or 0), int(idx), round(float(x), 2), round(float(z), 2), round(float(radius), 2))
            if key in seen:
                continue
            seen.add(key)
            active = bool(active_idx is None or idx <= int(active_idx))
            if active_flag is not None:
                active = bool(active_flag) and active
            smokes.append(
                {
                    "entity_id": int(getattr(entity, "id", 0) or 0),
                    "index": int(idx),
                    "active": bool(active),
                    "active_point_index": int(active_idx) if active_idx is not None else None,
                    "x": round(float(x), 3),
                    "z": round(float(z), 3),
                    "radius": round(float(radius), 3),
                    "height": round(float(height), 3),
                    "duration_s": round(float(point_duration or duration_s), 3),
                    "point_start": round(float(point_start), 3) if point_start is not None else None,
                    "end_time": round(float(point_end or end_time), 3) if (point_end or end_time) is not None else None,
                }
            )

    if not smokes:
        return None
    smokes.sort(key=lambda item: (int(item.get("entity_id", 0)), int(item.get("index", 0))))
    return {
        "time_s": round(float(time_s), 3),
        "smokes": smokes,
    }


def _snapshot_health_state(entities: Dict[int, Any], time_s: float) -> Dict[str, Any] | None:
    vehicles: Dict[str, Dict[str, Any]] = {}
    for entity in entities.values():
        try:
            if entity.get_name() != "Vehicle":
                continue
        except Exception:
            continue
        client = entity.properties.get("client", {}) if hasattr(entity, "properties") else {}
        if not isinstance(client, dict):
            continue
        hp = _safe_float(client.get("health"), -1.0)
        max_hp = _safe_float(client.get("maxHealth"), 0.0)
        if hp < 0.0 and max_hp <= 0.0:
            continue
        alive_value = client.get("isAlive")
        alive = bool(alive_value) if alive_value is not None else hp > 0.0
        vehicles[str(int(entity.id))] = {
            "hp": max(0, int(round(hp))),
            "max_hp": max(0, int(round(max_hp))),
            "alive": bool(alive),
        }

    if not vehicles:
        return None

    return {
        "time_s": round(float(time_s), 3),
        "entities": vehicles,
    }


def _sum_damage_value(value: Any) -> float:
    if isinstance(value, dict):
        return sum(_sum_damage_value(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        if len(value) == 2:
            try:
                float(value[0])
                return float(value[1])
            except (TypeError, ValueError):
                pass
        return sum(_sum_damage_value(v) for v in value)
    return 0.0


def _local_player_row(info: Dict[str, Any], player_name: str) -> Dict[str, Any]:
    players = info.get("players", {})
    avatar_id = _safe_int(info.get("player_id"))
    rows = list(players.values()) if isinstance(players, dict) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if avatar_id is not None and _safe_int(row.get("avatarId")) == avatar_id:
            return row
    player_name_norm = _norm_name(player_name)
    if player_name_norm:
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _norm_name(row.get("name")) == player_name_norm:
                return row
    return {}


def _snapshot_player_status(info: Dict[str, Any], player_name: str, time_s: float) -> Dict[str, Any] | None:
    row = _local_player_row(info, player_name)
    if not row:
        return None

    avatar_entity_id = _safe_int(info.get("player_id"))
    ribbons_raw = info.get("ribbons", {})
    ribbons: Dict[str, int] = {}
    if avatar_entity_id is not None and isinstance(ribbons_raw, dict):
        raw_counts = ribbons_raw.get(avatar_entity_id, {})
        if isinstance(raw_counts, dict):
            for ribbon_id, count in raw_counts.items():
                rid = _safe_int(ribbon_id)
                cnt = _safe_int(count)
                if rid is None or cnt is None or cnt <= 0:
                    continue
                ribbons[str(rid)] = cnt

    return {
        "time_s": round(float(time_s), 3),
        "avatar_entity_id": avatar_entity_id if avatar_entity_id is not None else -1,
        "ship_entity_id": _safe_int(row.get("shipId")) or -1,
        "ship_params_id": _safe_int(row.get("shipParamsId")) or -1,
        "team_id": _safe_int(row.get("teamId")) if _safe_int(row.get("teamId")) is not None else -1,
        "player_name": str(row.get("name") or player_name or "").strip(),
        "max_health": max(0, _safe_int(row.get("maxHealth")) or 0),
        "damage_total": round(_sum_damage_value(info.get("damage_map", {})), 3),
        "ribbons": ribbons,
    }


def _filter_health_timeline(timeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    last_key = None
    for snap in timeline:
        entities = snap.get("entities", {})
        if not isinstance(entities, dict) or not entities:
            continue
        state_key = tuple(
            (
                str(entity_key),
                int((state or {}).get("hp", 0)),
                int((state or {}).get("max_hp", 0)),
                int(bool((state or {}).get("alive", False))),
            )
            for entity_key, state in sorted(entities.items(), key=lambda item: int(item[0]))
        )
        if state_key != last_key:
            filtered.append(snap)
            last_key = state_key
    return filtered


def _filter_player_status_timeline(timeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    last_key = None
    for snap in timeline:
        ribbons = snap.get("ribbons", {})
        if not isinstance(ribbons, dict):
            ribbons = {}
        state_key = (
            round(float(snap.get("damage_total", 0.0) or 0.0), 3),
            tuple((str(k), int(v)) for k, v in sorted(ribbons.items(), key=lambda item: int(item[0]))),
        )
        if state_key != last_key:
            filtered.append(snap)
            last_key = state_key
    return filtered


def _extract_battle_overlay(
    context: ReplayContext,
    packets: List[DecodedPacket],
    local_team_id: Optional[int],
    session_map: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    replay_player = WowsReplayPlayer(context.version)
    cap_positions: Dict[int, Dict[str, Any]] = {}
    timeline: List[Dict[str, Any]] = []
    smoke_timeline: List[Dict[str, Any]] = []
    smoke_puffs: List[Dict[str, Any]] = []
    smoke_births: Dict[tuple[int, int], float] = {}
    smoke_last_seen: Dict[tuple[int, int], float] = {}
    smoke_puff_by_key: Dict[tuple[int, int], Dict[str, Any]] = {}
    smoke_present = False
    smoke_last_idx: Dict[int, int] = {}
    smoke_last_idx_change: Dict[int, float] = {}
    smoke_debug_enabled = bool(os.getenv("RENDER_SMOKE_DEBUG"))
    smoke_debug_limit = max(1, _safe_int(os.getenv("RENDER_SMOKE_DEBUG_LIMIT")) or 6)
    smoke_debug: List[Dict[str, Any]] = []
    smoke_debug_seen: set[int] = set()
    smoke_debug_last_idx: Dict[int, int] = {}
    health_timeline: List[Dict[str, Any]] = []
    player_status_timeline: List[Dict[str, Any]] = []
    artillery_shots: List[Dict[str, Any]] = []
    torpedo_points: List[Dict[str, Any]] = []
    squadron_events: List[Dict[str, Any]] = []
    chat_messages: List[Dict[str, Any]] = []
    sensor_events: List[Dict[str, Any]] = []
    consumable_events: List[Dict[str, Any]] = []
    consumable_defs_by_entity: Dict[int, Dict[str, Dict[Any, float]]] = {}
    sensor_debug_enabled = bool(os.getenv("RENDER_SENSOR_DEBUG"))
    sensor_debug_limit = max(1, _safe_int(os.getenv("RENDER_SENSOR_DEBUG_LIMIT")) or 60)
    sensor_debug_set = bool(os.getenv("RENDER_SENSOR_DEBUG_SET"))
    sensor_debug: List[Dict[str, Any]] = []
    torpedo_params_by_owner: Dict[int, set[int]] = {}
    vehicle_kills: List[Dict[str, Any]] = []

    ship_info_by_entity: Dict[int, Dict[str, Any]] = {}
    ships_cache = _load_ships_cache()
    if session_map:
        for eid, entry in session_map.items():
            ship_id = _safe_int(entry.get("shipId"))
            if ship_id is None:
                continue
            ship = ships_cache.get(str(ship_id)) if ships_cache else None
            if not ship and ships_cache:
                ship = ships_cache.get(ship_id)
            if not ship:
                continue
            has_sonar = False
            modules = ship.get("modules", {}) if isinstance(ship, dict) else {}
            sonar = modules.get("sonar") if isinstance(modules, dict) else None
            if isinstance(sonar, dict):
                if sonar.get("id"):
                    has_sonar = True
                ids = sonar.get("ids")
                if isinstance(ids, list) and any(ids):
                    has_sonar = True
            ship_info_by_entity[int(eid)] = {
                "ship_id": ship_id,
                "name": ship.get("name"),
                "nation": ship.get("nation"),
                "tier": ship.get("tier"),
                "type": ship.get("type"),
                "has_sonar": has_sonar,
            }
    avatar_kills: List[Dict[str, Any]] = []
    seen_shots: set[tuple[int, int]] = set()
    seen_torp_points: set[tuple[int, int, float, float, float]] = set()
    seen_squadron_points: set[tuple[int, float, float, float]] = set()
    seen_chat_messages: set[tuple[float, str, str]] = set()
    packet_time_ref = [0.0]
    next_sample_t = 0.0
    max_time = float(packets[-1].time) if packets else 0.0
    subscriptions_added: List[tuple[str, List[Any], Any]] = []
    local_player_name = str(context.engine_data.get("playerName") or "").strip()
    squadron_meta: Dict[int, Dict[str, Any]] = {}
    player_name_by_id: Dict[int, str] = {}

    def _sample_overlay_state(sample_t: float) -> None:
        nonlocal smoke_present
        snap = _snapshot_battle_state(replay_player._battle_controller.entities, cap_positions, sample_t)
        if snap is not None:
            timeline.append(snap)
        smoke_snap = _snapshot_smoke_state(replay_player._battle_controller.entities, sample_t)
        if smoke_debug_enabled:
            for entity in replay_player._battle_controller.entities.values():
                try:
                    if entity.get_name() != "SmokeScreen":
                        continue
                except Exception:
                    continue
                entity_id = int(getattr(entity, "id", 0) or 0)
                client = entity.properties.get("client", {}) if hasattr(entity, "properties") else {}
                cell = entity.properties.get("cell", {}) if hasattr(entity, "properties") else {}
                base = entity.properties.get("base", {}) if hasattr(entity, "properties") else {}
                active_idx = _safe_int(client.get("activePointIndex")) if isinstance(client, dict) else None
                if active_idx is not None:
                    last_idx = smoke_debug_last_idx.get(entity_id)
                    smoke_debug_last_idx[entity_id] = active_idx
                    if last_idx is not None and last_idx == active_idx:
                        continue
                if entity_id in smoke_debug_seen and len(smoke_debug) >= smoke_debug_limit:
                    continue
                smoke_debug_seen.add(entity_id)

                def _filter_props(props: Any) -> Dict[str, Any]:
                    if not isinstance(props, dict):
                        return {}
                    keep: Dict[str, Any] = {}
                    for k, v in props.items():
                        lk = str(k).lower()
                        if not any(token in lk for token in ("time", "life", "duration", "active", "point", "radius", "cloud", "smoke", "visible", "start", "end", "expire")):
                            continue
                        if isinstance(v, (int, float, str, bool)):
                            keep[k] = v
                        elif isinstance(v, list):
                            keep[k] = v[:3]
                        elif isinstance(v, dict):
                            keep[k] = {kk: vv for kk, vv in v.items() if isinstance(vv, (int, float, str, bool))}
                    return keep

                points_sample = None
                if isinstance(client, dict):
                    pts = client.get("points")
                    if isinstance(pts, list) and pts:
                        first = pts[0]
                        if isinstance(first, dict):
                            points_sample = {"count": len(pts), "keys": sorted(first.keys())}
                        else:
                            points_sample = {"count": len(pts), "type": type(first).__name__}

                smoke_debug.append(
                    {
                        "time_s": round(float(sample_t), 3),
                        "entity_id": entity_id,
                        "client": _filter_props(client),
                        "cell": _filter_props(cell),
                        "base": _filter_props(base),
                        "points": points_sample,
                    }
                )
        if smoke_snap is not None:
            for smoke in smoke_snap.get("smokes", []):
                if not isinstance(smoke, dict):
                    continue
                entity_id = _safe_int(smoke.get("entity_id")) or 0
                index = _safe_int(smoke.get("index")) if _safe_int(smoke.get("index")) is not None else -1
                key = (int(entity_id), int(index))
                if not bool(smoke.get("active", True)):
                    continue
                active_idx = _safe_int(smoke.get("active_point_index"))
                if active_idx is not None:
                    last_idx = smoke_last_idx.get(int(entity_id))
                    if last_idx is None or last_idx != int(active_idx):
                        smoke_last_idx[int(entity_id)] = int(active_idx)
                        smoke_last_idx_change[int(entity_id)] = float(sample_t)
                smoke_last_seen[key] = float(sample_t)
                existing = smoke_puff_by_key.get(key)
                if existing is not None:
                    new_duration = float(smoke.get("duration_s", 0.0) or 0.0)
                    if new_duration > 0.0:
                        prev_duration = float(existing.get("duration_s", 0.0) or 0.0)
                        if prev_duration > 0.0 and new_duration < (prev_duration - 0.5):
                            existing["duration_mode"] = "remaining"
                        if existing.get("duration_mode") == "remaining":
                            existing["end_time"] = round(float(sample_t + new_duration), 3)
                        elif prev_duration <= 0.0 or new_duration < prev_duration:
                            existing["duration_s"] = round(new_duration, 3)
                    explicit_end = _safe_float(smoke.get("end_time"))
                    if explicit_end is not None and explicit_end > float(existing.get("start_time", 0.0) or 0.0):
                        existing["end_time"] = round(float(explicit_end), 3)
                if key not in smoke_births:
                    point_start = _safe_float(smoke.get("point_start"))
                    if point_start is not None and point_start > 0.0 and point_start <= float(sample_t) + 1.0:
                        start_time = float(point_start)
                    else:
                        start_time = float(sample_t)
                    smoke_births[key] = start_time
                    duration_s = float(smoke.get("duration_s", 0.0) or 0.0)
                    point_end = _safe_float(smoke.get("end_time"))
                    end_time = None
                    if point_end is not None and point_end > start_time:
                        end_time = float(point_end)
                        if duration_s <= 0.0 or (end_time - start_time) < duration_s:
                            duration_s = max(0.0, end_time - start_time)
                    puff = {
                        "entity_id": int(entity_id),
                        "index": int(index),
                        "x": float(smoke.get("x", 0.0) or 0.0),
                        "z": float(smoke.get("z", 0.0) or 0.0),
                        "radius": float(smoke.get("radius", 0.0) or 0.0),
                        "height": float(smoke.get("height", 0.0) or 0.0),
                        "start_time": round(float(start_time), 3),
                        "duration_s": round(duration_s, 3),
                        "end_time": round(end_time, 3) if end_time is not None else None,
                        "duration_mode": "absolute",
                    }
                    smoke_puffs.append(puff)
                    smoke_puff_by_key[key] = puff
            if smoke_snap.get("smokes"):
                smoke_present = True
                smoke_timeline.append(smoke_snap)
            elif smoke_present:
                smoke_present = False
                smoke_timeline.append({"time_s": round(float(sample_t), 3), "smokes": []})
        elif smoke_present:
            smoke_present = False
            smoke_timeline.append({"time_s": round(float(sample_t), 3), "smokes": []})
        health_snap = _snapshot_health_state(replay_player._battle_controller.entities, sample_t)
        if health_snap is not None:
            health_timeline.append(health_snap)
        try:
            info = replay_player.get_info()
        except Exception:
            info = None
        if isinstance(info, dict):
            player_status = _snapshot_player_status(info, local_player_name, sample_t)
            if player_status is not None:
                player_status_timeline.append(player_status)

    def _subscribe_method(method_hash: str, callback: Any) -> None:
        subscriptions = Entity._methods_subscriptions.get(method_hash)
        if subscriptions is None:
            subscriptions = []
            Entity._methods_subscriptions[method_hash] = subscriptions
        subscriptions.append(callback)
        subscriptions_added.append((method_hash, subscriptions, callback))

    def _refresh_player_names() -> None:
        try:
            info = replay_player.get_info()
        except Exception:
            return
        if not isinstance(info, dict):
            return
        players_blob = info.get("players", {})
        if isinstance(players_blob, dict):
            rows = list(players_blob.values())
        elif isinstance(players_blob, list):
            rows = list(players_blob)
        else:
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            for key in ("id", "accountDBID", "account_id", "db_id", "dbId", "playerId", "avatarId"):
                pid = _safe_int(row.get(key))
                if pid is not None and pid >= 0:
                    player_name_by_id[pid] = name

    def _record_consumable_def(entity_id: int, kind: str, range_m: float, consumable_type: Optional[int]) -> None:
        entry = consumable_defs_by_entity.setdefault(int(entity_id), {"by_kind": {}, "by_type": {}, "entries": []})
        if kind:
            prev = entry["by_kind"].get(kind)
            if prev is None or range_m > prev:
                entry["by_kind"][kind] = float(range_m)
        if consumable_type is not None:
            prev = entry["by_type"].get(consumable_type)
            if prev is None or range_m > prev:
                entry["by_type"][consumable_type] = float(range_m)

    def _ship_info(entity_id: int) -> Dict[str, Any]:
        return ship_info_by_entity.get(int(entity_id), {})

    def _collect_consumable_entries(value: Any, out: List[Dict[str, Any]]) -> None:
        if isinstance(value, dict):
            work_time = _safe_float(value.get("workTime"), None)
            reload_time = _safe_float(value.get("reloadTime"), None)
            if work_time is not None:
                kind = _infer_consumable_kind(value) or ""
                ctype = _collect_consumable_type(value)
                tokens: set[str] = set()
                _collect_text_tokens(value, tokens)
                id_tokens: set[str] = set()
                for key in ("titleIDs", "descIDs", "iconIDs", "gameParamsName", "game_params_name"):
                    raw = value.get(key)
                    if isinstance(raw, str) and raw.strip():
                        id_tokens.add(raw.strip().lower())
                out.append(
                    {
                        "kind": kind,
                        "work_time": float(work_time),
                        "reload_time": float(reload_time or 0.0),
                        "consumable_type": int(ctype) if ctype is not None else -1,
                        "tokens": sorted(list(tokens))[:16],
                        "ids": sorted(list(id_tokens))[:8],
                    }
                )
            for v in value.values():
                _collect_consumable_entries(v, out)
        elif isinstance(value, (list, tuple, set)):
            for v in value:
                _collect_consumable_entries(v, out)

    def _merge_consumable_entries(entity_id: int, entries: List[Dict[str, Any]]) -> None:
        if not entries:
            return
        bucket = consumable_defs_by_entity.setdefault(int(entity_id), {"by_kind": {}, "by_type": {}, "entries": []})
        existing = bucket.get("entries", [])
        for entry in entries:
            key = (
                entry.get("kind", ""),
                round(float(entry.get("work_time", 0.0)), 2),
                round(float(entry.get("reload_time", 0.0)), 2),
                int(entry.get("consumable_type", -1)),
            )
            if any(
                (
                    e.get("kind", ""),
                    round(float(e.get("work_time", 0.0)), 2),
                    round(float(e.get("reload_time", 0.0)), 2),
                    int(e.get("consumable_type", -1)),
                )
                == key
                for e in existing
            ):
                continue
            existing.append(entry)
        bucket["entries"] = existing

    def _match_consumable_entry(entity_id: int, duration: float, usage_tokens: Optional[set[str]] = None) -> tuple[Optional[Dict[str, Any]], bool]:
        if duration <= 0.0:
            return None, False
        entries = consumable_defs_by_entity.get(int(entity_id), {}).get("entries", [])
        if not entries:
            return None, False
        if usage_tokens:
            # Prefer explicit ID/token matches when available.
            for entry in entries:
                ids = entry.get("ids", []) or []
                if any(str(token).lower() in usage_tokens for token in ids):
                    return entry, True
        scored: List[tuple[float, Dict[str, Any]]] = []
        for entry in entries:
            wt = _safe_float(entry.get("work_time"), None)
            if wt is None or wt <= 0.0:
                continue
            diff = abs(float(duration) - float(wt))
            scored.append((diff, entry))
        if not scored:
            return None, False
        scored.sort(key=lambda item: item[0])
        best_diff, best = scored[0]
        wt = float(best.get("work_time", 0.0) or 0.0)
        tolerance = max(1.0, wt * 0.12)
        if best_diff > tolerance:
            return None, False
        # If other candidates are similarly close but a different kind, treat as ambiguous.
        close = [entry for diff, entry in scored if diff <= tolerance]
        kinds = {str(e.get("kind") or "") for e in close}
        sensor_kinds = {k for k in kinds if k in ("radar", "hydro")}
        has_other = any(k not in ("radar", "hydro") for k in kinds)
        if sensor_kinds and has_other:
            return None, False
        if len(sensor_kinds) > 1:
            return None, False
        return best, False

    def _has_non_sensor_match(entity_id: int, duration: float) -> bool:
        if duration <= 0.0:
            return False
        entries = consumable_defs_by_entity.get(int(entity_id), {}).get("entries", [])
        for entry in entries:
            kind = str(entry.get("kind") or "")
            if kind in ("radar", "hydro"):
                continue
            wt = _safe_float(entry.get("work_time"), None)
            if wt is None or wt <= 0.0:
                continue
            tolerance = max(1.0, float(wt) * 0.12)
            if abs(float(duration) - float(wt)) <= tolerance:
                return True
        return False

    def _lookup_consumable_params(kind: str, entity_id: int) -> Dict[str, Optional[float]]:
        info = _ship_info(entity_id)
        variant = _choose_consumable_variant(kind, info) if info else None
        if variant:
            return {
                "range_m": variant.get("range_m"),
                "duration_s": variant.get("work_time"),
                "reload_s": variant.get("reload_time"),
            }
        return _fallback_consumable_params(kind, info)

    def _scan_consumable_defs(obj: Any, entity_id: int) -> None:
        if isinstance(obj, dict):
            kind = _infer_consumable_kind(obj)
            range_m = _infer_range_m(obj)
            if kind and range_m is not None:
                ctype = _collect_consumable_type(obj)
                _record_consumable_def(entity_id, kind, range_m, ctype)
            for v in obj.values():
                _scan_consumable_defs(v, entity_id)
        elif isinstance(obj, (list, tuple, set)):
            for v in obj:
                _scan_consumable_defs(v, entity_id)

    def _on_set_consumables(entity: Any, *args: Any, **_kwargs: Any) -> None:
        if not args:
            return
        entity_id = _safe_int(getattr(entity, "id", None))
        if entity_id is None:
            return
        raw = args[0]
        data = _safe_unpickle(raw)
        if data is None and isinstance(raw, dict):
            data = raw
        if data is not None:
            _scan_consumable_defs(data, int(entity_id))
            entries: List[Dict[str, Any]] = []
            _collect_consumable_entries(data, entries)
            _merge_consumable_entries(int(entity_id), entries)
            if sensor_debug_enabled and sensor_debug_set and len(sensor_debug) < sensor_debug_limit:
                tokens: set[str] = set()
                _collect_text_tokens(data, tokens)
                sensor_debug.append(
                    {
                        "time_s": round(float(packet_time_ref[0]), 3),
                        "event": "setConsumables",
                        "entity_id": int(entity_id),
                        "raw_type": type(raw).__name__,
                        "tokens_sample": sorted(list(tokens))[:24],
                    }
                )
            return
        tokens, numbers = _scan_pickled_blob(raw)
        kind = _infer_consumable_kind_from_tokens(tokens) if tokens else None
        range_m = _infer_range_from_numbers(numbers) if numbers else None
        if kind and range_m is not None:
            _record_consumable_def(int(entity_id), kind, float(range_m), None)
        if sensor_debug_enabled and sensor_debug_set and len(sensor_debug) < sensor_debug_limit:
            sensor_debug.append(
                {
                    "time_s": round(float(packet_time_ref[0]), 3),
                    "event": "setConsumables",
                    "entity_id": int(entity_id),
                    "raw_type": type(raw).__name__,
                    "tokens_sample": sorted(list(tokens))[:24],
                    "range_candidates": [round(float(v), 3) for v in numbers[:8]],
                    "kind": kind or "",
                    "range_m": round(float(range_m), 3) if range_m is not None else None,
                }
            )

    def _on_consumable_used(entity: Any, *args: Any, **kwargs: Any) -> None:
        entity_id = _safe_int(getattr(entity, "id", None))
        if entity_id is None:
            return
        usage_raw = None
        if args:
            usage_raw = args[0]
        elif "consumableUsageParams" in kwargs:
            usage_raw = kwargs.get("consumableUsageParams")
        usage = usage_raw if isinstance(usage_raw, dict) else _safe_unpickle(usage_raw)
        kind = _infer_consumable_kind(usage) if usage is not None else None
        consumable_type = _collect_consumable_type(usage) if usage is not None else None
        ship_info = _ship_info(entity_id)
        usage_tokens: set[str] = set()
        if usage is not None:
            _collect_text_tokens(usage, usage_tokens)
        range_m = _infer_range_m(usage) if usage is not None else None
        if range_m is None:
            defs = consumable_defs_by_entity.get(int(entity_id), {})
            if consumable_type is not None:
                range_m = defs.get("by_type", {}).get(consumable_type)
            if range_m is None and kind:
                range_m = defs.get("by_kind", {}).get(kind)
        if usage is None and isinstance(usage_raw, (bytes, bytearray, memoryview, BytesIO)):
            tokens, numbers = _scan_pickled_blob(usage_raw)
            if kind is None and tokens:
                kind = _infer_consumable_kind_from_tokens(tokens)
            if range_m is None and numbers:
                range_m = _infer_range_from_numbers(numbers)
            if tokens:
                usage_tokens.update(tokens)
        radar_variant = _choose_consumable_variant("radar", ship_info) if ship_info else None
        sonar_variant = _choose_consumable_variant("hydro", ship_info) if ship_info else None
        chosen_variant = None
        sensor_fallback = False
        if kind is None:
            defs = consumable_defs_by_entity.get(int(entity_id), {})
            kinds = list(defs.get("by_kind", {}).keys())
            if len(kinds) == 1:
                kind = kinds[0]
            elif radar_variant and not sonar_variant:
                kind = "radar"
                chosen_variant = radar_variant
                sensor_fallback = True
            elif sonar_variant and not radar_variant:
                kind = "hydro"
                chosen_variant = sonar_variant
                sensor_fallback = True
        duration_s = 0.0
        if len(args) > 1:
            duration_s = _safe_float(args[1], 0.0)
        elif "workTimeLeft" in kwargs:
            duration_s = _safe_float(kwargs.get("workTimeLeft"), 0.0)
        if duration_s <= 0.0 and usage is not None:
            inferred = _infer_duration_s(usage)
            if inferred is not None:
                duration_s = inferred
        entry_match, entry_id_match = _match_consumable_entry(int(entity_id), float(duration_s), usage_tokens=usage_tokens if usage_tokens else None)
        if entry_match:
            entry_kind = str(entry_match.get("kind") or "")
            if entry_kind in ("radar", "hydro"):
                kind = entry_kind
                chosen_variant = _choose_consumable_variant(kind, ship_info) if ship_info else None
            elif entry_kind:
                kind = entry_kind
        elif kind is None and duration_s > 0.0 and _has_non_sensor_match(int(entity_id), float(duration_s)):
            return
        elif kind is None and radar_variant and sonar_variant and duration_s > 0.0:
            radar_time = radar_variant.get("work_time") or 0.0
            sonar_time = sonar_variant.get("work_time") or 0.0
            if radar_time > 0.0 or sonar_time > 0.0:
                radar_diff = abs(float(duration_s) - float(radar_time)) if radar_time else float("inf")
                sonar_diff = abs(float(duration_s) - float(sonar_time)) if sonar_time else float("inf")
                if radar_diff != sonar_diff:
                    if radar_diff < sonar_diff:
                        kind = "radar"
                        chosen_variant = radar_variant
                    else:
                        kind = "hydro"
                        chosen_variant = sonar_variant
        if chosen_variant:
            if range_m is None and chosen_variant.get("range_m") is not None:
                range_m = float(chosen_variant["range_m"])
            if duration_s <= 0.0 and chosen_variant.get("work_time") is not None:
                duration_s = float(chosen_variant["work_time"])
        low_confidence = (
            usage is None
            and not usage_tokens
            and consumable_type is None
            and range_m is None
            and not entry_match
        )
        low_confidence_reason = ""
        if kind in ("radar", "hydro"):
            if chosen_variant is None:
                chosen_variant = _choose_consumable_variant(kind, ship_info) if ship_info else None
            if range_m is None or duration_s <= 0.0:
                params = _lookup_consumable_params(kind, int(entity_id))
                if range_m is None and params.get("range_m") is not None:
                    range_m = float(params["range_m"])
                if duration_s <= 0.0 and params.get("duration_s") is not None:
                    duration_s = float(params["duration_s"])
            if chosen_variant:
                variant_range = _safe_float(chosen_variant.get("range_m"), None)
                variant_duration = _safe_float(chosen_variant.get("work_time"), None)
                if variant_range is not None:
                    range_m = float(variant_range)
                if variant_duration is not None and variant_duration > 0.0:
                    if duration_s <= 0.0:
                        duration_s = float(variant_duration)
                    else:
                        # Only accept this consumable if duration roughly matches the variant.
                        ratio = float(duration_s) / float(variant_duration)
                        if ratio < 0.90 or ratio > 1.25:
                            if sensor_fallback:
                                kind = None
                                chosen_variant = None
                            else:
                                return
                        if low_confidence:
                            tight = abs(float(duration_s) - float(variant_duration)) <= max(0.5, float(variant_duration) * 0.05)
                            if not tight:
                                if sensor_fallback:
                                    kind = None
                                    chosen_variant = None
                                else:
                                    return
                            else:
                                low_confidence_reason = "duration_only"
            if chosen_variant is None:
                if sensor_fallback:
                    kind = None
                else:
                    # No reliable match for this ship/consumable; skip rendering.
                    return
        if kind in ("heal", "engine", "smoke"):
            if duration_s <= 0.0:
                return
            start_t = round(float(packet_time_ref[0]), 3)
            consumable_events.append(
                {
                    "entity_id": int(entity_id),
                    "kind": str(kind),
                    "start_time": start_t,
                    "duration_s": round(float(duration_s), 3),
                    "end_time": round(float(start_t + duration_s), 3),
                }
            )
            return
        if kind is None and duration_s > 0.0:
            start_t = round(float(packet_time_ref[0]), 3)
            consumable_events.append(
                {
                    "entity_id": int(entity_id),
                    "kind": "unknown",
                    "start_time": start_t,
                    "duration_s": round(float(duration_s), 3),
                    "end_time": round(float(start_t + duration_s), 3),
                }
            )
            return
        if sensor_debug_enabled and len(sensor_debug) < sensor_debug_limit:
            tokens: set[str] = set()
            if usage is not None:
                _collect_text_tokens(usage, tokens)
            range_candidates: List[float] = []
            if usage is not None:
                _collect_range_candidates(usage, range_candidates)
            sensor_debug.append(
                {
                    "time_s": round(float(packet_time_ref[0]), 3),
                    "event": "consumableUsed",
                    "entity_id": int(entity_id),
                    "raw_type": type(usage_raw).__name__,
                    "usage_type": type(usage).__name__ if usage is not None else "None",
                    "kind": kind or "",
                    "consumable_type": int(consumable_type) if consumable_type is not None else -1,
                    "range_m": round(float(range_m), 3) if range_m is not None else None,
                    "duration_s": round(float(duration_s), 3),
                    "ship_name": str(ship_info.get("name") or ""),
                    "variant_key": str(chosen_variant.get("key")) if chosen_variant else "",
                    "entry_match": {
                        "kind": entry_match.get("kind"),
                        "work_time": entry_match.get("work_time"),
                        "reload_time": entry_match.get("reload_time"),
                        "id_match": entry_id_match,
                    } if entry_match else {},
                    "tokens_sample": sorted(list(tokens))[:24],
                    "range_candidates": [round(float(v), 3) for v in range_candidates[:8]],
                }
            )
        if kind not in ("radar", "hydro") or range_m is None:
            return
        if duration_s <= 0.0:
            return
        start_t = round(float(packet_time_ref[0]), 3)
        sensor_events.append(
            {
                "entity_id": int(entity_id),
                "kind": str(kind),
                "radius": round(float(range_m), 3),
                "start_time": start_t,
                "duration_s": round(float(duration_s), 3),
                "end_time": round(float(start_t + duration_s), 3),
                "consumable_type": int(consumable_type) if consumable_type is not None else -1,
                "confidence": "low" if low_confidence_reason else "normal",
                "confidence_reason": low_confidence_reason,
            }
        )

    def _on_artillery_shots(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        shot_packs = _iter_values(args[0]) if args else []
        t_fire = float(packet_time_ref[0])
        for pack in shot_packs:
            if not isinstance(pack, dict):
                continue
            owner_id = _safe_int(pack.get("ownerID"))
            params_id = _safe_int(pack.get("paramsID"))
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
                        "params_id": params_id if params_id is not None else -1,
                        "pack_shot_count": len(shots),
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
            params_id = _safe_int(pack.get("paramsID"))
            if owner_id is not None and params_id is not None:
                torpedo_params_by_owner.setdefault(owner_id, set()).add(params_id)
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

    def _record_squadron_event(
        event: str,
        squadron_id: Optional[int],
        pos: tuple[float, float] | None,
        team_id: Optional[int] = None,
        params_id: Optional[int] = None,
        visible: Optional[bool] = None,
    ) -> None:
        if squadron_id is None:
            return
        sid = int(squadron_id)
        meta = squadron_meta.setdefault(sid, {})
        if team_id is not None:
            meta["team_id"] = int(team_id)
        if params_id is not None:
            meta["params_id"] = int(params_id)
        if visible is not None:
            meta["visible"] = bool(visible)
        x = z = None
        if pos is not None:
            x, z = pos
        t = round(float(packet_time_ref[0]), 3)
        if x is not None and z is not None:
            dedup_key = (sid, t, round(x, 2), round(z, 2))
            if dedup_key in seen_squadron_points:
                return
            seen_squadron_points.add(dedup_key)
        squadron_events.append(
            {
                "time_s": t,
                "event": event,
                "squadron_id": sid,
                "team_id": int(meta.get("team_id")) if _safe_int(meta.get("team_id")) is not None else -1,
                "params_id": int(meta.get("params_id")) if _safe_int(meta.get("params_id")) is not None else -1,
                "x": round(float(x), 3) if x is not None else None,
                "z": round(float(z), 3) if z is not None else None,
                "visible": bool(meta.get("visible", True)),
            }
        )

    def _on_add_minimap_squadron(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        if len(args) < 4:
            return
        squadron_id = _safe_int(args[0])
        team_id = _safe_int(args[1])
        params_id = _safe_int(args[2])
        pos = _vec_xy(args[3])
        visible = None
        if len(args) > 4:
            visible = bool(args[4])
        _record_squadron_event("add", squadron_id, pos, team_id=team_id, params_id=params_id, visible=visible)

    def _on_update_minimap_squadron(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        if len(args) < 2:
            return
        squadron_id = _safe_int(args[0])
        pos = _vec_xy(args[1])
        _record_squadron_event("update", squadron_id, pos)

    def _on_remove_minimap_squadron(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        if not args:
            return
        squadron_id = _safe_int(args[0])
        _record_squadron_event("remove", squadron_id, None)

    def _on_squadron_visibility(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        if len(args) < 2:
            return
        squadron_id = _safe_int(args[0])
        visible = bool(_safe_int(args[1]) or 0)
        _record_squadron_event("visibility", squadron_id, None, visible=visible)

    def _on_chat_message(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        if not args:
            return

        def _coerce_text(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, bytes):
                try:
                    return value.decode("utf-8", errors="ignore")
                except Exception:
                    return ""
            if isinstance(value, str):
                return value
            return ""

        sender = ""
        message = ""

        sender_id = _safe_int(args[0]) if args else None
        if sender_id is not None and sender_id not in player_name_by_id:
            _refresh_player_names()
        if sender_id is not None:
            sender = player_name_by_id.get(sender_id, "")

        channel_tokens = {
            "battle_team",
            "battle_all",
            "battle_team_0",
            "battle_team_1",
            "battle_allies",
            "battle_enemy",
            "battle_common",
            "team",
            "global",
        }
        texts = [_coerce_text(v).strip() for v in args if isinstance(v, (str, bytes))]
        texts = [t for t in texts if t and t.lower() not in channel_tokens]

        if sender:
            candidates = [t for t in texts if t != sender]
            if candidates:
                message = max(candidates, key=len)
        if not message and texts:
            message = max(texts, key=len)
        if not sender:
            for t in texts:
                if t != message:
                    sender = t
                    break
        if not sender and sender_id is not None:
            sender = f"id_{sender_id}"
        if not message:
            return
        time_s = round(float(packet_time_ref[0]), 3)
        dedup_key = (time_s, sender, message)
        if dedup_key in seen_chat_messages:
            return
        seen_chat_messages.add(dedup_key)
        chat_messages.append(
            {
                "time_s": time_s,
                "sender": sender,
                "message": message,
            }
        )

    def _on_vehicle_kill(entity: Any, *args: Any, **_kwargs: Any) -> None:
        victim_entity_id = _safe_int(getattr(entity, "id", None))
        reason_code = _safe_int(args[1]) if len(args) > 1 else None
        cause_param_id = _safe_int(args[2]) if len(args) > 2 else None
        killer_entity_id = _safe_int(args[8]) if len(args) > 8 else None
        vehicle_kills.append(
            {
                "time_s": round(float(packet_time_ref[0]), 3),
                "victim_entity_id": victim_entity_id if victim_entity_id is not None else -1,
                "killer_entity_id": killer_entity_id if killer_entity_id is not None else -1,
                "reason_code": reason_code if reason_code is not None else -1,
                "cause_param_id": cause_param_id if cause_param_id is not None else -1,
            }
        )

    def _on_avatar_vehicle_death(_entity: Any, *args: Any, **_kwargs: Any) -> None:
        if len(args) < 3:
            return
        victim_entity_id = _safe_int(args[0])
        killer_entity_id = _safe_int(args[1])
        reason_code = _safe_int(args[2])
        avatar_kills.append(
            {
                "time_s": round(float(packet_time_ref[0]), 3),
                "victim_entity_id": victim_entity_id if victim_entity_id is not None else -1,
                "killer_entity_id": killer_entity_id if killer_entity_id is not None else -1,
                "reason_code": reason_code if reason_code is not None else -1,
            }
        )

    _subscribe_method("Avatar_receiveArtilleryShots", _on_artillery_shots)
    _subscribe_method("Avatar_receiveTorpedoes", _on_torpedoes)
    _subscribe_method("Avatar_receiveTorpedoDirection", _on_torpedo_direction)
    _subscribe_method("Avatar_receive_addMinimapSquadron", _on_add_minimap_squadron)
    _subscribe_method("Avatar_receive_updateMinimapSquadron", _on_update_minimap_squadron)
    _subscribe_method("Avatar_receive_removeMinimapSquadron", _on_remove_minimap_squadron)
    _subscribe_method("Avatar_receive_squadronVisibilityChanged", _on_squadron_visibility)
    _subscribe_method("Avatar_chatMessage", _on_chat_message)
    _subscribe_method("Account_chatMessage", _on_chat_message)
    _subscribe_method("Avatar_onChatMessage", _on_chat_message)
    _subscribe_method("Account_onChatMessage", _on_chat_message)
    _subscribe_method("Vehicle_kill", _on_vehicle_kill)
    _subscribe_method("Avatar_receiveVehicleDeath", _on_avatar_vehicle_death)
    _subscribe_method("Vehicle_setConsumables", _on_set_consumables)
    _subscribe_method("Vehicle_onConsumableUsed", _on_consumable_used)
    _subscribe_method("Avatar_useConsumable", _on_consumable_used)
    try:
        for p in packets:
            packet_time_ref[0] = float(p.time)
            if p.packet_obj is None:
                while float(p.time) >= next_sample_t:
                    _sample_overlay_state(next_sample_t)
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
                _sample_overlay_state(next_sample_t)
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
        _sample_overlay_state(max_time)

    if smoke_puffs:
        sample_step = 1.0
        for puff in smoke_puffs:
            if not isinstance(puff, dict):
                continue
            key = (int(puff.get("entity_id", 0) or 0), int(puff.get("index", -1) or -1))
            start_time = float(puff.get("start_time", 0.0) or 0.0)
            duration_s = float(puff.get("duration_s", 0.0) or 0.0)
            last_seen = float(smoke_last_seen.get(key, start_time))
            explicit_end = _safe_float(puff.get("end_time"))
            if explicit_end is not None and explicit_end > start_time:
                end_time = float(explicit_end)
            elif duration_s > 0.0:
                end_time = start_time + duration_s
            elif int(puff.get("entity_id", 0) or 0) in smoke_last_idx_change:
                end_time = float(smoke_last_idx_change[int(puff.get("entity_id", 0) or 0)]) + sample_step
            else:
                end_time = last_seen + sample_step
            puff["end_time"] = round(end_time, 3)

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

    filtered_health = _filter_health_timeline(health_timeline)
    filtered_smokes: List[Dict[str, Any]] = []
    last_smoke_key = None
    for snap in smoke_timeline:
        smokes = snap.get("smokes", []) if isinstance(snap, dict) else []
        if not isinstance(smokes, list):
            smokes = []
        state_key = tuple(
            (
                int(s.get("entity_id", 0)),
                int(s.get("index", 0)),
                round(_safe_float(s.get("x"), 0.0), 2),
                round(_safe_float(s.get("z"), 0.0), 2),
                round(_safe_float(s.get("radius"), 0.0), 2),
                int(bool(s.get("active", True))),
            )
            for s in smokes
            if isinstance(s, dict)
        )
        if state_key != last_smoke_key:
            filtered_smokes.append(snap)
            last_smoke_key = state_key
    filtered_player_status = _filter_player_status_timeline(player_status_timeline)

    if smoke_debug_enabled and smoke_debug:
        try:
            debug_path = Path(__file__).resolve().parent.parent / "content" / "smoke_debug.json"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            with debug_path.open("w", encoding="utf-8") as f:
                json.dump(smoke_debug, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    if sensor_debug_enabled and sensor_debug:
        try:
            debug_path = Path(__file__).resolve().parent.parent / "content" / "sensor_debug.json"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            with debug_path.open("w", encoding="utf-8") as f:
                json.dump(sensor_debug, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

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

    filtered_artillery_shots, secondary_groups, secondary_dropped, main_artillery_params, all_artillery_params = _filter_main_artillery_shots(artillery_shots)
    shell_kind_by_param = _infer_shell_kinds_for_params(vehicle_kills, main_artillery_params)
    for shot in filtered_artillery_shots:
        params_id = _safe_int(shot.get("params_id"))
        if params_id is None:
            continue
        shell_kind = shell_kind_by_param.get(params_id)
        if shell_kind:
            shot["shell_kind"] = shell_kind

    vehicle_kills_by_victim: Dict[int, List[Dict[str, Any]]] = {}
    for row in vehicle_kills:
        victim_entity_id = _safe_int(row.get("victim_entity_id"))
        if victim_entity_id is None or victim_entity_id < 0:
            continue
        vehicle_kills_by_victim.setdefault(victim_entity_id, []).append(row)
    for rows in vehicle_kills_by_victim.values():
        rows.sort(key=lambda row: float(row.get("time_s", 0.0)))

    used_vehicle_kills: set[tuple[int, int, int]] = set()

    def _match_vehicle_kill(victim_entity_id: int, time_s: float) -> Optional[Dict[str, Any]]:
        candidates = vehicle_kills_by_victim.get(victim_entity_id, [])
        best: Optional[Dict[str, Any]] = None
        best_delta = 9999.0
        for row in candidates:
            row_time = float(row.get("time_s", 0.0))
            delta = abs(row_time - time_s)
            if delta > 1.25 or delta >= best_delta:
                continue
            match_key = (
                int(row.get("victim_entity_id", -1)),
                int(round(row_time * 1000.0)),
                int(row.get("cause_param_id", -1)),
            )
            if match_key in used_vehicle_kills:
                continue
            best = row
            best_delta = delta
        if best is not None:
            used_vehicle_kills.add(
                (
                    int(best.get("victim_entity_id", -1)),
                    int(round(float(best.get("time_s", 0.0)) * 1000.0)),
                    int(best.get("cause_param_id", -1)),
                )
            )
        return best

    kill_feed: List[Dict[str, Any]] = []
    seen_kill_keys: set[tuple[int, int]] = set()
    for row in sorted(avatar_kills, key=lambda item: (float(item.get("time_s", 0.0)), int(item.get("victim_entity_id", -1)))):
        victim_entity_id = _safe_int(row.get("victim_entity_id"))
        if victim_entity_id is None or victim_entity_id < 0:
            continue
        time_s = round(float(row.get("time_s", 0.0)), 3)
        reason_code = _safe_int(row.get("reason_code"))
        killer_entity_id = _safe_int(row.get("killer_entity_id"))
        matched_vehicle_kill = _match_vehicle_kill(victim_entity_id, time_s)
        cause_param_id = _safe_int(matched_vehicle_kill.get("cause_param_id")) if matched_vehicle_kill else None
        if matched_vehicle_kill is not None and (killer_entity_id is None or killer_entity_id < 0):
            killer_entity_id = _safe_int(matched_vehicle_kill.get("killer_entity_id"))
        if matched_vehicle_kill is not None and (reason_code is None or reason_code < 0):
            reason_code = _safe_int(matched_vehicle_kill.get("reason_code"))

        weapon_kind = _infer_kill_weapon_kind(
            reason_code,
            cause_param_id,
            killer_entity_id,
            main_artillery_params,
            all_artillery_params,
            torpedo_params_by_owner,
        )
        shell_kind = _shell_kind_from_reason(reason_code)
        weapon_label = _kill_weapon_label(reason_code, weapon_kind, shell_kind)
        kill_key = (victim_entity_id, int(round(time_s * 1000.0)))
        if kill_key in seen_kill_keys:
            continue
        seen_kill_keys.add(kill_key)
        kill_feed.append(
            {
                "time_s": time_s,
                "victim_entity_id": victim_entity_id,
                "killer_entity_id": killer_entity_id if killer_entity_id is not None else -1,
                "reason_code": reason_code if reason_code is not None else -1,
                "cause_param_id": cause_param_id if cause_param_id is not None else -1,
                "weapon_kind": weapon_kind,
                "weapon_label": weapon_label,
                "shell_kind": shell_kind or "",
            }
        )

    if not kill_feed:
        for row in sorted(vehicle_kills, key=lambda item: (float(item.get("time_s", 0.0)), int(item.get("victim_entity_id", -1)))):
            victim_entity_id = _safe_int(row.get("victim_entity_id"))
            if victim_entity_id is None or victim_entity_id < 0:
                continue
            time_s = round(float(row.get("time_s", 0.0)), 3)
            reason_code = _safe_int(row.get("reason_code"))
            killer_entity_id = _safe_int(row.get("killer_entity_id"))
            cause_param_id = _safe_int(row.get("cause_param_id"))
            weapon_kind = _infer_kill_weapon_kind(
                reason_code,
                cause_param_id,
                killer_entity_id,
                main_artillery_params,
                all_artillery_params,
                torpedo_params_by_owner,
            )
            shell_kind = _shell_kind_from_reason(reason_code)
            kill_feed.append(
                {
                    "time_s": time_s,
                    "victim_entity_id": victim_entity_id,
                    "killer_entity_id": killer_entity_id if killer_entity_id is not None else -1,
                    "reason_code": reason_code if reason_code is not None else -1,
                    "cause_param_id": cause_param_id if cause_param_id is not None else -1,
                    "weapon_kind": weapon_kind,
                    "weapon_label": _kill_weapon_label(reason_code, weapon_kind, shell_kind),
                    "shell_kind": shell_kind or "",
                }
            )

    player_status_meta: Dict[str, Any] = {}
    for snap in filtered_player_status:
        ship_entity_id = _safe_int(snap.get("ship_entity_id"))
        ship_params_id = _safe_int(snap.get("ship_params_id"))
        avatar_entity_id = _safe_int(snap.get("avatar_entity_id"))
        if ship_entity_id is None and ship_params_id is None and avatar_entity_id is None:
            continue
        player_status_meta = {
            "player_name": str(snap.get("player_name") or local_player_name or "").strip(),
            "avatar_entity_id": avatar_entity_id if avatar_entity_id is not None else -1,
            "ship_entity_id": ship_entity_id if ship_entity_id is not None else -1,
            "ship_params_id": ship_params_id if ship_params_id is not None else -1,
            "team_id": _safe_int(snap.get("team_id")) if _safe_int(snap.get("team_id")) is not None else -1,
            "max_health": max(0, _safe_int(snap.get("max_health")) or 0),
        }
        break

    return {
        "captures_timeline": filtered,
        "smoke_timeline": filtered_smokes,
        "smoke_puffs": smoke_puffs,
        "health_timeline": filtered_health,
        "player_status_timeline": filtered_player_status,
        "player_status_meta": player_status_meta,
        "control_points": control_points,
        "final_scores": final_scores,
        "team_win_score": team_win_score,
        "local_team_id": local_team_id,
        "enemy_team_id": enemy_team_id,
        "artillery_shots": sorted(filtered_artillery_shots, key=lambda item: (float(item.get("time_s", 0.0)), int(item.get("shot_id", -1)))),
        "torpedo_points": sorted(
            torpedo_points,
            key=lambda item: (
                float(item.get("time_s", 0.0)),
                int(item.get("owner_entity_id", -1)),
                int(item.get("torpedo_id", -1)),
            ),
        ),
        "sensor_events": sorted(sensor_events, key=lambda item: (float(item.get("start_time", 0.0)), int(item.get("entity_id", -1)), str(item.get("kind", "")))),
        "consumable_events": sorted(consumable_events, key=lambda item: (float(item.get("start_time", 0.0)), int(item.get("entity_id", -1)), str(item.get("kind", "")))),
        "squadrons": sorted(
            squadron_events,
            key=lambda item: (
                float(item.get("time_s", 0.0)),
                int(item.get("squadron_id", -1)),
                str(item.get("event", "")),
            ),
        ),
        "kill_feed": sorted(kill_feed, key=lambda item: (float(item.get("time_s", 0.0)), int(item.get("victim_entity_id", -1)))),
        "chat_messages": sorted(chat_messages, key=lambda item: (float(item.get("time_s", 0.0)), str(item.get("sender", "")))),
        "secondary_artillery_groups": secondary_groups,
        "secondary_artillery_dropped_shots": secondary_dropped,
        "shell_kinds_resolved": len(shell_kind_by_param),
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


def _sanitize_track(points: List[TrackPoint]) -> List[TrackPoint]:
    if not points:
        return points

    grouped: List[List[TrackPoint]] = []
    current_group: List[TrackPoint] = [points[0]]
    for point in points[1:]:
        if abs(point.t - current_group[-1].t) <= 1e-6:
            current_group.append(point)
        else:
            grouped.append(current_group)
            current_group = [point]
    grouped.append(current_group)

    sanitized: List[TrackPoint] = []
    prev: Optional[TrackPoint] = None
    for group in grouped:
        if len(group) == 1:
            choice = group[0]
        elif prev is None:
            choice = max(group, key=lambda p: (abs(p.yaw), abs(p.x) + abs(p.z)))
        else:
            choice = min(
                group,
                key=lambda p: (
                    math.hypot(p.x - prev.x, p.z - prev.z),
                    abs(p.yaw) < 1e-6,
                ),
            )
        sanitized.append(choice)
        prev = choice
    return sanitized


def extract_events(context: ReplayContext, packets: List[DecodedPacket]) -> ReplayExtraction:
    meta = context.engine_data
    session_map, session_map_source = _build_session_map(context, packets)

    tracks: Dict[int, ShipTrack] = {}
    deaths: List[DeathEvent] = []
    packet_counts: Dict[str, int] = {}

    player_session_id = next((eid for eid, v in session_map.items() if _safe_int(v.get("relation")) == 0), None)
    local_team_id = next((_safe_int(v.get("teamId")) for v in session_map.values() if _safe_int(v.get("relation")) == 0), None)
    has_player_position = any(isinstance(p.packet_obj, PlayerPosition) for p in packets)

    for p in packets:
        packet_counts[p.packet_name] = packet_counts.get(p.packet_name, 0) + 1

        if isinstance(p.packet_obj, Position):
            eid = int(p.packet_obj.entityId)
            if has_player_position and player_session_id is not None and eid == player_session_id:
                # The local player ship also emits PlayerPosition packets. Mixing both streams creates
                # interleaved bogus jumps and unstable heading for the player marker.
                continue
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

    for eid, track in tracks.items():
        track.points.sort(key=lambda item: item.t)
        track.points = _sanitize_track(track.points)

    battle_state = _extract_battle_overlay(context, packets, local_team_id, session_map)
    diagnostics = {
        "session_map_size": len(session_map),
        "session_map_source": session_map_source,
        "player_session_id": player_session_id,
        "local_team_id": local_team_id,
        "captures_timeline": len(battle_state.get("captures_timeline", [])),
        "control_points": len(battle_state.get("control_points", [])),
        "artillery_shots": len(battle_state.get("artillery_shots", [])),
        "torpedo_points": len(battle_state.get("torpedo_points", [])),
        "squadron_events": len(battle_state.get("squadrons", [])),
        "kill_feed": len(battle_state.get("kill_feed", [])),
        "chat_messages": len(battle_state.get("chat_messages", [])),
        "smoke_snapshots": len(battle_state.get("smoke_timeline", [])),
        "sensor_events": len(battle_state.get("sensor_events", [])),
        "consumable_events": len(battle_state.get("consumable_events", [])),
        "secondary_artillery_groups": int(battle_state.get("secondary_artillery_groups", 0) or 0),
        "secondary_artillery_dropped_shots": int(battle_state.get("secondary_artillery_dropped_shots", 0) or 0),
        "shell_kinds_resolved": int(battle_state.get("shell_kinds_resolved", 0) or 0),
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
