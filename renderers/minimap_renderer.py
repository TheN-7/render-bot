from __future__ import annotations

import json
import math
import re
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import urlopen

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

try:
    from utils.map_names import get_battlearena_entry
except Exception:
    get_battlearena_entry = None


COLOR_BG = (10, 20, 40)
COLOR_UNSPOTTED = (130, 130, 130)
COLOR_SUNK = (90, 90, 90)
WG_ICON_HEADING_OFFSET_DEG = -90.0
COLOR_FRIENDLY = (80, 220, 90)
COLOR_ENEMY = (255, 80, 80)
COLOR_UNKNOWN = (180, 180, 180)
SHIP_TYPE_TO_CODE = {
    "Destroyer": "DD",
    "Cruiser": "CA",
    "Battleship": "BB",
    "AirCarrier": "CV",
    "Submarine": "SS",
}


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=32)
def _load_font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


@lru_cache(maxsize=2048)
def _text_sprite(
    text: str,
    size: int,
    fill: Tuple[int, int, int],
    shadow: Tuple[int, int, int] | None = None,
) -> Image.Image | None:
    if not text:
        return None
    font = _load_font(size)
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    probe_draw = ImageDraw.Draw(probe)
    bbox = probe_draw.textbbox((0, 0), text, font=font)
    shadow_pad = 1 if shadow is not None else 0
    width = max(1, (bbox[2] - bbox[0]) + shadow_pad + 1)
    height = max(1, (bbox[3] - bbox[1]) + shadow_pad + 1)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ox = -bbox[0]
    oy = -bbox[1]
    if shadow is not None:
        draw.text((ox + 1, oy + 1), text, fill=shadow, font=font)
    draw.text((ox, oy), text, fill=fill, font=font)
    return img


def _paste_sprite(img: Image.Image, sprite: Image.Image | None, x: int, y: int) -> None:
    if sprite is None:
        return
    img.paste(sprite, (x, y), sprite)


@lru_cache(maxsize=1)
def _load_ship_cache() -> Dict[str, Dict[str, Any]]:
    cache_path = Path(__file__).resolve().parent.parent / "ships_cache.json"
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ship_entry(ship_id: Any) -> Dict[str, Any]:
    cache = _load_ship_cache()
    try:
        key = str(int(ship_id))
    except (TypeError, ValueError):
        return {}
    return cache.get(key, {})


def _ship_type(ship_id: Any) -> str:
    entry = _ship_entry(ship_id)
    return str(entry.get("type", ""))


def _ship_class_code(ship_id: Any) -> str:
    return SHIP_TYPE_TO_CODE.get(_ship_type(ship_id), "??")


def _ship_name(ship_id: Any) -> str:
    entry = _ship_entry(ship_id)
    return str(entry.get("name", ""))


def _root_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _icon_cache_dir() -> Path:
    p = _root_dir() / "content" / "wg_ship_type_icons"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _kill_icon_cache_dir() -> Path:
    p = _root_dir() / "content" / "sessionstats_kill_icons"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _map_cache_dir() -> Path:
    p = _root_dir() / "content" / "wg_map_icons"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_api_config() -> Tuple[str, str]:
    cfg_path = _root_dir() / "wws_api_config.json"
    app_id = ""
    realm = "eu"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        app_id = str(cfg.get("app_id", "")).strip()
        realm = str(cfg.get("realm", "eu")).strip().lower() or "eu"
    except Exception:
        pass
    return app_id, realm


def _base_url_for_realm(realm: str) -> str:
    realms = {
        "na": "https://api.worldofwarships.com/wows/",
        "eu": "https://api.worldofwarships.eu/wows/",
        "asia": "https://api.worldofwarships.asia/wows/",
        "ru": "https://api.worldofwarships.ru/wows/",
    }
    return realms.get(realm, realms["eu"])


def _download_bytes(url: str) -> bytes:
    with urlopen(url, timeout=20) as resp:
        return resp.read()


def _map_icon_url(canonical: Dict[str, Any]) -> str:
    meta = canonical.get("meta", {}) or {}
    icon_url = str(meta.get("map_icon_url", "") or "").strip()
    if icon_url:
        return icon_url
    if get_battlearena_entry is not None:
        entry = get_battlearena_entry(meta.get("mapId"))
        if isinstance(entry, dict):
            return str(entry.get("icon", "") or "").strip()
    return ""


@lru_cache(maxsize=32)
def _load_map_icon(url: str) -> Image.Image | None:
    if not url:
        return None
    filename = Path(url.split("?", 1)[0]).name or "map_icon.png"
    file_path = _map_cache_dir() / filename
    try:
        if not file_path.exists():
            file_path.write_bytes(_download_bytes(url))
        return Image.open(file_path).convert("RGBA")
    except Exception:
        return None


@lru_cache(maxsize=32)
def _map_background_layer(url: str, canvas_size: int, margin: int) -> Image.Image | None:
    icon = _load_map_icon(url)
    if icon is None:
        return None

    usable = canvas_size - 2 * margin
    if usable <= 0:
        return None

    bg = icon.resize((usable, usable), Image.Resampling.LANCZOS)
    # Keep map readable but subtle so tracks/icons stay visible.
    bg = ImageEnhance.Brightness(bg).enhance(0.75)
    alpha = bg.getchannel("A").point(lambda a: min(165, a))
    bg.putalpha(alpha)

    layer = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    layer.paste(bg, (margin, margin), bg)
    return layer


def _apply_map_background(img: Image.Image, canonical: Dict[str, Any], margin: int) -> Image.Image:
    url = _map_icon_url(canonical)
    layer = _map_background_layer(url, img.width, margin) if url else None
    if layer is None:
        return img
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


@lru_cache(maxsize=1)
def _load_wg_ship_type_images_meta() -> Dict[str, Dict[str, str]]:
    meta_path = _icon_cache_dir() / "ship_type_images.json"
    try:
        if meta_path.exists():
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data
    except Exception:
        pass

    app_id, realm = _read_api_config()
    if not app_id:
        return {}

    params = urlencode({"application_id": app_id, "fields": "ship_type_images"})
    url = f"{_base_url_for_realm(realm)}encyclopedia/info/?{params}"
    try:
        payload = json.loads(_download_bytes(url).decode("utf-8"))
        data = payload.get("data", {}).get("ship_type_images", {})
        if isinstance(data, dict) and data:
            meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return data
    except Exception:
        return {}
    return {}


@lru_cache(maxsize=1)
def _load_wg_class_icons() -> Dict[str, Image.Image]:
    icons: Dict[str, Image.Image] = {}
    meta = _load_wg_ship_type_images_meta()
    icon_dir = _icon_cache_dir()
    for ship_type in SHIP_TYPE_TO_CODE:
        # Prefer standard icon, then premium/elite as fallback.
        entry = meta.get(ship_type, {})
        url = entry.get("image") or entry.get("image_premium") or entry.get("image_elite")
        file_path = icon_dir / f"{ship_type}.png"
        try:
            if not file_path.exists() and url:
                file_path.write_bytes(_download_bytes(url))
            if file_path.exists():
                icons[ship_type] = Image.open(file_path).convert("RGBA")
        except Exception:
            continue
    return icons


@lru_cache(maxsize=128)
def _wg_tinted_icon(ship_type: str, color: Tuple[int, int, int], size: int) -> Image.Image | None:
    base_icon = _load_wg_class_icons().get(ship_type)
    if base_icon is None:
        return None
    target = max(12, size * 2 + 6)
    icon = base_icon.resize((target, target), Image.Resampling.LANCZOS)
    alpha = icon.getchannel("A")
    tinted = Image.new("RGBA", icon.size, (color[0], color[1], color[2], 0))
    tinted.putalpha(alpha)
    return tinted


def _world_half(canonical: Dict[str, Any]) -> float:
    max_extent = 0.0
    for track in canonical.get("tracks", {}).values():
        for p in track.get("points", []):
            max_extent = max(max_extent, abs(float(p.get("x", 0.0))), abs(float(p.get("z", 0.0))))
    if max_extent <= 0.0:
        return 700.0
    return math.ceil(max_extent * 1.1 / 50.0) * 50.0


def _to_px(x: float, z: float, half: float, size: int, margin: int = 40) -> Tuple[int, int]:
    usable = size - 2 * margin
    px = int((x + half) / (2 * half) * usable + margin)
    py = int((1.0 - (z + half) / (2 * half)) * usable + margin)
    return px, py


def _find_death_times(canonical: Dict[str, Any]) -> Dict[str, float]:
    deaths: Dict[str, float] = {}
    for event in canonical.get("events", {}).get("deaths", []):
        key = str(event.get("entity_key", ""))
        t = float(event.get("time_s", 0.0))
        if key and (key not in deaths or t < deaths[key]):
            deaths[key] = t
    return deaths


def _team_side(value: Any) -> str:
    s = str(value or "").lower()
    if s in ("enemy", "foe", "red"):
        return "enemy"
    if s in ("ally", "player", "friendly", "green"):
        return "friendly"
    return "unknown"


def _status_color(team_side: str, spotted: bool, sunk: bool, ever_spotted: bool = False) -> Tuple[int, int, int]:
    if sunk:
        return COLOR_SUNK
    # Keep allied side consistently green for easier ownership checks.
    if team_side == "friendly":
        return COLOR_FRIENDLY
    if team_side == "enemy":
        if (not spotted) and ever_spotted:
            return COLOR_UNSPOTTED
        return COLOR_ENEMY
    if not spotted:
        return COLOR_UNSPOTTED
    return COLOR_UNKNOWN


def _color_side(track: Dict[str, Any]) -> str:
    # Prefer explicit replay team labels when available (ally/player/enemy).
    hinted = _team_side(track.get("team_label_side"))
    if hinted in ("friendly", "enemy"):
        return hinted
    return _team_side(track.get("team_side"))


def _spread_marker_position(cx: int, cy: int, idx: int, cell: int = 16) -> Tuple[int, int]:
    # Small deterministic spread so overlapping spawn clusters remain distinguishable.
    if idx <= 0:
        return cx, cy
    ring = 1 + (idx - 1) // 8
    pos = (idx - 1) % 8
    angle = (pos * 45.0) * math.pi / 180.0
    radius = ring * (cell // 2)
    return int(cx + math.cos(angle) * radius), int(cy + math.sin(angle) * radius)


def _spread_world_position(x: float, z: float, idx: int, cell: float = 40.0) -> Tuple[float, float]:
    if idx <= 0:
        return x, z
    ring = 1 + (idx - 1) // 8
    pos = (idx - 1) % 8
    angle = (pos * 45.0) * math.pi / 180.0
    radius = ring * (cell * 0.5)
    return x + math.cos(angle) * radius, z + math.sin(angle) * radius


def _map_title(canonical: Dict[str, Any]) -> str:
    meta = canonical.get("meta", {}) or {}
    title = meta.get("map_name_resolved") or meta.get("mapDisplayName") or meta.get("mapName")
    if title is None:
        return "Unknown Map"
    return str(title)


def _norm_name(value: Any) -> str:
    s = str(value or "").strip().lower()
    # Keep letters, digits, underscore and dash to match player names robustly.
    return re.sub(r"[^a-z0-9_-]+", "", s)


def _lineup_number_text(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "?"


def _marker_name_text(value: Any, max_len: int = 14) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "~"


def _draw_polyline_with_gaps(
    draw: ImageDraw.ImageDraw,
    poly: List[Tuple[int, int]],
    color: Tuple[int, int, int],
    width: int = 2,
    max_jump_px: int = 30,
) -> None:
    if len(poly) < 2:
        return
    max_jump_sq = max_jump_px * max_jump_px
    start = 0
    for i in range(1, len(poly)):
        dx = poly[i][0] - poly[i - 1][0]
        dy = poly[i][1] - poly[i - 1][1]
        if dx * dx + dy * dy > max_jump_sq:
            if i - start >= 2:
                draw.line(poly[start:i], fill=color, width=width)
            start = i
    if len(poly) - start >= 2:
        draw.line(poly[start:], fill=color, width=width)


def _extract_artillery_traces(canonical: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = canonical.get("events", {}) or {}
    fires = events.get("fires", [])
    if not isinstance(fires, list):
        return []
    traces: List[Dict[str, Any]] = []
    for fire in fires:
        if not isinstance(fire, dict):
            continue
        if str(fire.get("kind") or "") not in ("", "artillery_trace"):
            continue
        t0 = float(fire.get("time_s", 0.0) or 0.0)
        t1 = float(fire.get("time_end_s", t0) or t0)
        if t1 < t0:
            t1 = t0
        traces.append(
            {
                "time_s": t0,
                "time_end_s": t1,
                "params_id": _safe_int(fire.get("params_id")) or -1,
                "shell_kind": str(fire.get("shell_kind") or "").strip().lower(),
                "x0": float(fire.get("x0", 0.0) or 0.0),
                "z0": float(fire.get("z0", 0.0) or 0.0),
                "x1": float(fire.get("x1", 0.0) or 0.0),
                "z1": float(fire.get("z1", 0.0) or 0.0),
            }
        )
    traces.sort(key=lambda item: float(item.get("time_s", 0.0)))
    return traces


def _extract_kill_feed(canonical: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = canonical.get("events", {}) or {}
    raw = events.get("kills", [])
    if not isinstance(raw, list):
        return []
    kills: List[Dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        kills.append(
            {
                "time_s": float(row.get("time_s", 0.0) or 0.0),
                "killer_entity_key": str(row.get("killer_entity_key") or "-1"),
                "victim_entity_key": str(row.get("victim_entity_key") or "-1"),
                "reason_code": _safe_int(row.get("reason_code")) or -1,
                "weapon_kind": str(row.get("weapon_kind") or "other"),
                "weapon_label": str(row.get("weapon_label") or "KILL"),
                "shell_kind": str(row.get("shell_kind") or "").strip().lower(),
            }
        )
    kills.sort(key=lambda item: float(item.get("time_s", 0.0)))
    return kills


def _extract_torpedo_tracks(canonical: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    events = canonical.get("events", {}) or {}
    raw = events.get("torpedoes", [])
    if not isinstance(raw, list):
        return {}

    tracks: Dict[str, Dict[str, Any]] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        owner_key = str(row.get("owner_entity_key") or "-1")
        torpedo_id = _safe_int(row.get("torpedo_id"))
        torpedo_id = torpedo_id if torpedo_id is not None else -1
        track_key = f"{owner_key}:{torpedo_id}"
        track = tracks.setdefault(
            track_key,
            {
                "owner_entity_key": owner_key,
                "torpedo_id": torpedo_id,
                "team_side": str(row.get("team_side") or "unknown"),
                "points": [],
                "times": [],
            },
        )
        t = float(row.get("time_s", 0.0) or 0.0)
        x = float(row.get("x", 0.0) or 0.0)
        z = float(row.get("z", 0.0) or 0.0)
        track["points"].append({"t": t, "x": x, "z": z})

    for track in tracks.values():
        points = track.get("points", [])
        points.sort(key=lambda item: float(item.get("t", 0.0)))
        deduped: List[Dict[str, float]] = []
        last = None
        for p in points:
            key = (round(float(p.get("t", 0.0)), 3), round(float(p.get("x", 0.0)), 2), round(float(p.get("z", 0.0)), 2))
            if key == last:
                continue
            deduped.append(p)
            last = key
        track["points"] = deduped
        track["times"] = [float(p.get("t", 0.0)) for p in deduped]

    return tracks


def _torpedo_position_at(track: Dict[str, Any], t: float, max_stale_s: float = 3.5, max_gap_s: float = 4.0) -> Tuple[float, float] | None:
    points = track.get("points", [])
    times = track.get("times", [])
    if not points or not times:
        return None
    idx = bisect_right(times, t) - 1
    if idx < 0:
        return None
    if idx >= len(points) - 1:
        if t - float(times[-1]) > max_stale_s:
            return None
        return float(points[-1]["x"]), float(points[-1]["z"])

    p0 = points[idx]
    p1 = points[idx + 1]
    t0 = float(p0.get("t", 0.0))
    t1 = float(p1.get("t", t0))
    if t1 <= t0:
        return float(p0.get("x", 0.0)), float(p0.get("z", 0.0))
    if (t1 - t0) > max_gap_s:
        if (t - t0) > max_stale_s:
            return None
        return float(p0.get("x", 0.0)), float(p0.get("z", 0.0))

    ratio = max(0.0, min(1.0, (t - t0) / (t1 - t0)))
    x = float(p0.get("x", 0.0)) + (float(p1.get("x", 0.0)) - float(p0.get("x", 0.0))) * ratio
    z = float(p0.get("z", 0.0)) + (float(p1.get("z", 0.0)) - float(p0.get("z", 0.0))) * ratio
    return x, z


def _torpedo_direction_at(track: Dict[str, Any], t: float) -> Tuple[float, float] | None:
    points = track.get("points", [])
    times = track.get("times", [])
    if len(points) < 2 or not times:
        return None
    idx = bisect_right(times, t) - 1
    if idx < 0:
        return None
    if idx >= len(points) - 1:
        idx = len(points) - 2
    p0 = points[idx]
    p1 = points[idx + 1]
    dx = float(p1.get("x", 0.0)) - float(p0.get("x", 0.0))
    dz = float(p1.get("z", 0.0)) - float(p0.get("z", 0.0))
    dist = math.hypot(dx, dz)
    if dist < 1e-6:
        return None
    return dx / dist, dz / dist


def _draw_torpedoes(
    draw: ImageDraw.ImageDraw,
    torpedo_tracks: Dict[str, Dict[str, Any]],
    t: float,
    half: float,
    canvas_size: int,
    margin: int,
) -> None:
    for track in torpedo_tracks.values():
        pos = _torpedo_position_at(track, t)
        if pos is None:
            continue
        x, z = pos
        px, py = _to_px(x, z, half, canvas_size, margin)
        direction = _torpedo_direction_at(track, t)
        side = str(track.get("team_side") or "unknown")
        if side == "friendly":
            color = (255, 255, 255)
        elif side == "enemy":
            color = (255, 70, 70)
        else:
            color = (180, 180, 180)

        if direction is None:
            points = [(px, py - 4), (px + 4, py), (px, py + 4), (px - 4, py)]
            draw.polygon(points, fill=color, outline=(20, 20, 20))
            continue

        dx, dz = direction
        front = _to_px(x + dx * 10.0, z + dz * 10.0, half, canvas_size, margin)
        back = _to_px(x - dx * 8.0, z - dz * 8.0, half, canvas_size, margin)
        left = _to_px(x - dz * 5.0, z + dx * 5.0, half, canvas_size, margin)
        right = _to_px(x + dz * 5.0, z - dx * 5.0, half, canvas_size, margin)
        wake = _to_px(x - dx * 18.0, z - dz * 18.0, half, canvas_size, margin)
        draw.line([wake, back], fill=color, width=1)
        draw.polygon([front, right, back, left], fill=color, outline=(20, 20, 20))


def _draw_artillery_traces(
    img: Image.Image,
    traces: List[Dict[str, Any]],
    t: float,
    half: float,
    canvas_size: int,
    margin: int,
) -> None:
    if not traces:
        return
    draw_rgba = ImageDraw.Draw(img, "RGBA")
    for trace in traces:
        t0 = float(trace.get("time_s", 0.0))
        t1 = float(trace.get("time_end_s", t0))
        if t < t0 or t > t1:
            continue
        span = max(0.1, t1 - t0)
        progress = min(1.0, max(0.0, (t - t0) / span))
        x0 = float(trace.get("x0", 0.0))
        z0 = float(trace.get("z0", 0.0))
        x1 = float(trace.get("x1", 0.0))
        z1 = float(trace.get("z1", 0.0))
        xp = x0 + (x1 - x0) * progress
        zp = z0 + (z1 - z0) * progress

        dx = x1 - x0
        dz = z1 - z0
        dist = math.hypot(dx, dz)
        if dist < 1e-6:
            continue
        ux = dx / dist
        uz = dz / dist
        seg_world = max(10.0, min(55.0, dist * 0.05))
        hx = ux * (seg_world * 0.5)
        hz = uz * (seg_world * 0.5)
        sx, sy = _to_px(xp - hx, zp - hz, half, canvas_size, margin)
        ex, ey = _to_px(xp + hx, zp + hz, half, canvas_size, margin)
        alpha = int(160 + 60 * (1.0 - progress))
        shell_kind = str(trace.get("shell_kind") or "").strip().lower()
        if shell_kind == "he":
            color = (255, 238, 170, alpha)
        elif shell_kind == "cs":
            color = (255, 224, 160, alpha)
        else:
            color = (245, 245, 245, alpha)
        draw_rgba.line([(sx, sy), (ex, ey)], fill=color, width=1)


def _entity_name_for_feed(canonical: Dict[str, Any], entity_key: str) -> str:
    key = str(entity_key or "-1")
    if key in ("", "-1"):
        return "Environment"
    entities = canonical.get("entities", {}) or {}
    entity = entities.get(key, {}) if isinstance(entities, dict) else {}
    name = str(entity.get("player_name") or "").strip()
    if name:
        return name
    return f"entity_{key}"


def _kill_panel_style(entry: Dict[str, Any]) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    shell_kind = str(entry.get("shell_kind") or "").strip().lower()
    weapon_kind = str(entry.get("weapon_kind") or "other")
    if shell_kind == "ap":
        return (245, 245, 245), (20, 20, 20)
    if shell_kind == "he":
        return (255, 238, 170), (30, 30, 30)
    if shell_kind == "cs":
        return (255, 224, 160), (40, 35, 20)
    if weapon_kind == "torpedo":
        return (255, 84, 84), (255, 255, 255)
    if weapon_kind == "bomb":
        return (255, 185, 120), (30, 30, 30)
    return (150, 150, 150), (255, 255, 255)


def _kill_icon_filename(entry: Dict[str, Any]) -> str:
    reason_code = _safe_int(entry.get("reason_code"))
    if reason_code in (1, 16, 17, 18, 19):
        return "icon_main.png"
    if reason_code == 2:
        return "icon_atba.png"
    if reason_code in (3, 5, 11, 13):
        return "icon_torpedo.png"
    if reason_code in (4, 28):
        return "icon_bomb.png"
    if reason_code == 6:
        return "icon_burn.png"
    if reason_code == 9:
        return "icon_flood.png"
    if reason_code == 14:
        return "icon_rocket.png"
    if reason_code == 22:
        return "icon_skip.png"

    weapon_kind = str(entry.get("weapon_kind") or "other")
    if weapon_kind == "gun":
        return "icon_main.png"
    if weapon_kind == "torpedo":
        return "icon_torpedo.png"
    if weapon_kind == "bomb":
        return "icon_bomb.png"
    return "icon_kill.png"


@lru_cache(maxsize=64)
def _load_kill_icon(filename: str, size: int) -> Image.Image | None:
    file_path = _kill_icon_cache_dir() / filename
    if not file_path.exists():
        return None
    try:
        icon = Image.open(file_path).convert("RGBA")
    except Exception:
        return None
    if size > 0 and icon.size != (size, size):
        icon = icon.resize((size, size), Image.Resampling.LANCZOS)
    return icon


def _draw_kill_feed_panel(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    canonical: Dict[str, Any],
    render_tracks: Dict[str, Dict[str, Any]],
    kill_feed: List[Dict[str, Any]],
    t: float,
    canvas_size: int,
) -> None:
    visible = [row for row in kill_feed if float(row.get("time_s", 0.0)) <= t + 1e-6]
    if not visible:
        return

    font_size = 10
    time_font_size = 9
    icon_size = max(14, canvas_size // 42)
    line_h = max(16, icon_size + 2)
    col_w = max(220, canvas_size // 3)
    rows = max(
        sum(1 for v in render_tracks.values() if v.get("team_side") == "friendly"),
        sum(1 for v in render_tracks.values() if v.get("team_side") == "enemy"),
        12,
    )
    lineup_panel_h = 20 + rows * 12 + 8
    panel_x = canvas_size - col_w - 8
    panel_y = 48 + lineup_panel_h + 10
    available_rows = max(4, min(10, (canvas_size - panel_y - 20) // line_h))
    visible = visible[-available_rows:]
    panel_h = 20 + len(visible) * line_h + 8

    draw.rectangle([panel_x, panel_y, panel_x + col_w, panel_y + panel_h], fill=None, outline=(100, 100, 100))
    _paste_sprite(img, _text_sprite("Kill feed", font_size, (225, 225, 225)), panel_x + 6, panel_y + 4)

    y = panel_y + 20
    for entry in reversed(visible):
        killer = _entity_name_for_feed(canonical, str(entry.get("killer_entity_key") or "-1"))
        victim = _entity_name_for_feed(canonical, str(entry.get("victim_entity_key") or "-1"))
        killer = _marker_name_text(killer, max_len=13)
        victim = _marker_name_text(victim, max_len=13)
        weapon_label = str(entry.get("weapon_label") or "KILL")
        pill_fill, pill_text = _kill_panel_style(entry)

        mins, secs = divmod(int(float(entry.get("time_s", 0.0))), 60)
        stamp = f"{mins}:{secs:02d}"
        stamp_sprite = _text_sprite(stamp, time_font_size, (165, 165, 165))
        _paste_sprite(img, stamp_sprite, panel_x + 6, y + 1)

        tx = panel_x + 42
        killer_sprite = _text_sprite(killer, font_size, (235, 235, 235))
        _paste_sprite(img, killer_sprite, tx, y)
        icon_x = tx + (killer_sprite.width if killer_sprite is not None else 0) + 6
        icon_y = y - 1
        icon = _load_kill_icon(_kill_icon_filename(entry), icon_size)
        if icon is not None:
            img.paste(icon, (icon_x, icon_y), icon)
            victim_x = icon_x + icon_size + 6
        else:
            pill_sprite = _text_sprite(weapon_label, time_font_size, pill_text)
            pill_w = (pill_sprite.width if pill_sprite is not None else 0) + 8
            draw.rectangle([icon_x, y, icon_x + pill_w, y + 11], fill=pill_fill, outline=(20, 20, 20))
            _paste_sprite(img, pill_sprite, icon_x + 4, y + 1)
            victim_x = icon_x + pill_w + 6
        victim_sprite = _text_sprite(victim, font_size, (235, 235, 235))
        _paste_sprite(img, victim_sprite, victim_x, y)
        y += line_h


def _normalize_render_tracks(canonical: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    tracks = canonical.get("tracks", {}) or {}
    entities = canonical.get("entities", {}) or {}
    meta_vehicles = canonical.get("meta", {}).get("vehicles", []) or []

    # Build immutable lineup from replay roster.
    lineup: List[Dict[str, Any]] = []
    for idx, v in enumerate(meta_vehicles):
        relation = int(v.get("relation", 2) if v.get("relation") is not None else 2)
        team_side = "enemy" if relation == 2 else "friendly"
        account_id = str(v.get("id", "")).strip()
        name = str(v.get("name", "")).strip()
        ship_id = v.get("shipId")
        lineup.append(
            {
                "slot_id": idx,
                "team_side": team_side,
                "relation": relation,
                "account_id": account_id,
                "name": name,
                "name_norm": _norm_name(name),
                "ship_id": ship_id,
                "used": False,
            }
        )

    # Assign stable lineup numbers from replay roster:
    # friendly team gets 1..N, enemy team gets N+1..N+M (globally unique).
    friendly_slots = [s for s in lineup if s["team_side"] == "friendly"]
    enemy_slots = [s for s in lineup if s["team_side"] == "enemy"]
    friendly_slots.sort(key=lambda s: s["slot_id"])
    enemy_slots.sort(key=lambda s: s["slot_id"])

    for i, slot in enumerate(friendly_slots, start=1):
        slot["team_number_local"] = i
        slot["team_number"] = i

    offset = len(friendly_slots)
    for i, slot in enumerate(enemy_slots, start=1):
        slot["team_number_local"] = i
        slot["team_number"] = offset + i

    for slot in lineup:
        if "team_number" not in slot:
            slot["team_number_local"] = None
            slot["team_number"] = None

    lineup_by_account: Dict[str, Dict[str, Any]] = {s["account_id"]: s for s in lineup if s["account_id"]}
    lineup_by_name: Dict[str, List[Dict[str, Any]]] = {}
    lineup_by_ship: Dict[str, List[Dict[str, Any]]] = {}
    for slot in lineup:
        if slot["name_norm"]:
            lineup_by_name.setdefault(slot["name_norm"], []).append(slot)
        if slot["ship_id"] is not None:
            lineup_by_ship.setdefault(str(slot["ship_id"]), []).append(slot)
    for slots in lineup_by_name.values():
        slots.sort(key=lambda s: (s["used"], 0 if s["relation"] == 0 else 1))
    for slots in lineup_by_ship.values():
        slots.sort(key=lambda s: (s["used"], 0 if s["relation"] == 0 else 1))

    normalized: Dict[str, Dict[str, Any]] = {}
    friendly_starts: List[Tuple[float, float]] = []
    enemy_starts: List[Tuple[float, float]] = []
    unresolved: List[Tuple[str, Dict[str, Any], List[Dict[str, Any]], str, str, str]] = []

    for entity_key, track in tracks.items():
        points = list(track.get("points", []) or [])
        if not points:
            continue
        player_name = str(track.get("player_name") or f"entity_{entity_key}")
        name_norm = _norm_name(player_name)
        entity_meta = entities.get(str(entity_key), {}) or {}
        team_hint = _team_side(track.get("team") or entity_meta.get("team"))
        account_entity_id = entity_meta.get("account_entity_id")
        account_id = str(account_entity_id) if account_entity_id is not None else ""

        slot = None
        if account_id:
            maybe = lineup_by_account.get(account_id)
            if maybe and (not maybe["used"]) and (team_hint == "unknown" or maybe.get("team_side") == team_hint):
                slot = maybe
        if slot is None and name_norm:
            by_name = [
                s
                for s in lineup_by_name.get(name_norm, [])
                if (not s["used"]) and (team_hint == "unknown" or s.get("team_side") == team_hint)
            ]
            if len(by_name) == 1:
                slot = by_name[0]
        if slot is None:
            ship_id = track.get("ship_id")
            if ship_id is not None:
                by_ship = [
                    s
                    for s in lineup_by_ship.get(str(ship_id), [])
                    if (not s["used"]) and (team_hint == "unknown" or s.get("team_side") == team_hint)
                ]
                if len(by_ship) == 1:
                    slot = by_ship[0]

        if slot:
            slot["used"] = True
            team_side = team_hint if team_hint in ("friendly", "enemy") else slot["team_side"]
            ship_id = slot.get("ship_id", track.get("ship_id"))
            account_resolved = slot.get("account_id") or account_id or None
            label_name = slot.get("name") or player_name
            team_number = slot.get("team_number")
            team_number_local = slot.get("team_number_local")
        else:
            team_side = team_hint if team_hint in ("friendly", "enemy") else "unknown"
            ship_id = track.get("ship_id")
            account_resolved = account_id or None
            label_name = player_name
            team_number = None
            team_number_local = None

        first = points[0]
        start = (float(first.get("x", 0.0)), float(first.get("z", 0.0)))
        if team_side == "friendly":
            friendly_starts.append(start)
        elif team_side == "enemy":
            enemy_starts.append(start)
        if slot is None:
            unresolved.append((str(entity_key), track, points, player_name, account_id, team_hint))

        normalized[str(entity_key)] = {
            "entity_id": track.get("entity_id", entity_key),
            "player_name": label_name,
            "ship_id": ship_id,
            "team_side": team_side,
            "team_label_side": team_hint,
            "team_number": team_number,
            "team_number_local": team_number_local,
            "account_entity_id": account_resolved,
            "points": points,
            "always_unspotted": False,
        }

    def _avg(points: List[Tuple[float, float]], default_x: float, default_z: float) -> Tuple[float, float]:
        if not points:
            return default_x, default_z
        return (
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
        )

    half = _world_half(canonical)
    friendly_center = _avg(friendly_starts, -half * 0.55, 0.0)
    enemy_center = _avg(enemy_starts, half * 0.55, 0.0)

    # Resolve unmatched tracks by positional fallback onto remaining lineup slots.
    def _choose_slot(side: str, ship_id_value: Any) -> Dict[str, Any] | None:
        candidates = [s for s in lineup if (not s["used"]) and s["team_side"] == side]
        if not candidates:
            return None
        if ship_id_value is not None:
            exact = [s for s in candidates if str(s.get("ship_id")) == str(ship_id_value)]
            if len(exact) == 1:
                return exact[0]
        return candidates[0]

    if unresolved:
        half = _world_half(canonical)
        friendly_center = (
            sum(p[0] for p in friendly_starts) / len(friendly_starts) if friendly_starts else -half * 0.55,
            sum(p[1] for p in friendly_starts) / len(friendly_starts) if friendly_starts else 0.0,
        )
        enemy_center = (
            sum(p[0] for p in enemy_starts) / len(enemy_starts) if enemy_starts else half * 0.55,
            sum(p[1] for p in enemy_starts) / len(enemy_starts) if enemy_starts else 0.0,
        )

        for ek, raw_track, pts, raw_name, raw_acc, team_hint in unresolved:
            if not pts:
                continue
            x0 = float(pts[0].get("x", 0.0))
            z0 = float(pts[0].get("z", 0.0))
            if team_hint in ("friendly", "enemy"):
                guessed_side = team_hint
            else:
                d_f = math.hypot(x0 - friendly_center[0], z0 - friendly_center[1])
                d_e = math.hypot(x0 - enemy_center[0], z0 - enemy_center[1])
                guessed_side = "friendly" if d_f <= d_e else "enemy"
            chosen = _choose_slot(guessed_side, raw_track.get("ship_id"))
            if chosen is None:
                # Last resort: any side with remaining slot.
                chosen = _choose_slot("friendly", raw_track.get("ship_id")) or _choose_slot("enemy", raw_track.get("ship_id"))
            if chosen is None:
                # No lineup slots left; keep unknown as friendly by default to avoid random red/green mixes.
                if guessed_side not in ("friendly", "enemy"):
                    guessed_side = "friendly"
                chosen_name = raw_name
                chosen_ship_id = raw_track.get("ship_id")
                chosen_account = raw_acc or None
            else:
                chosen["used"] = True
                if team_hint in ("friendly", "enemy"):
                    guessed_side = team_hint
                else:
                    guessed_side = chosen["team_side"]
                chosen_name = chosen.get("name") or raw_name
                chosen_ship_id = chosen.get("ship_id", raw_track.get("ship_id"))
                chosen_account = chosen.get("account_id") or raw_acc or None

            normalized[ek]["team_side"] = guessed_side
            normalized[ek]["player_name"] = chosen_name
            normalized[ek]["ship_id"] = chosen_ship_id
            normalized[ek]["account_entity_id"] = chosen_account
            normalized[ek]["team_number"] = chosen.get("team_number") if chosen is not None else None
            normalized[ek]["team_number_local"] = chosen.get("team_number_local") if chosen is not None else None

    # Create synthetic placeholders for lineup entries that still have no track.
    synth_idx_friendly = 0
    synth_idx_enemy = 0
    synth_entity_id = -1
    for slot in lineup:
        if slot["used"]:
            continue
        if slot.get("team_side") == "friendly":
            sx, sz = _spread_world_position(friendly_center[0], friendly_center[1], synth_idx_friendly, cell=42.0)
            synth_idx_friendly += 1
            yaw = 0.0
        else:
            sx, sz = _spread_world_position(enemy_center[0], enemy_center[1], synth_idx_enemy, cell=42.0)
            synth_idx_enemy += 1
            yaw = math.pi
        key = f"synthetic_{abs(synth_entity_id)}"
        synth_entity_id -= 1
        normalized[key] = {
            "entity_id": key,
            "player_name": slot.get("name") or f"entity_{key}",
            "ship_id": slot.get("ship_id"),
            "team_side": slot["team_side"],
            "team_label_side": slot["team_side"],
            "team_number": slot.get("team_number"),
            "team_number_local": slot.get("team_number_local"),
            "account_entity_id": slot.get("account_id") or None,
            "points": [{"t": 0.0, "x": sx, "y": 0.0, "z": sz, "yaw": yaw, "pitch": 0.0, "roll": 0.0}],
            "always_unspotted": True,
        }

    # Final fallback numbering for any unresolved entries: keep display numbers unique globally.
    max_display = max((int(v.get("team_number") or 0) for v in normalized.values()), default=0)
    next_display = max_display + 1
    max_friendly_local = max((int(v.get("team_number_local") or 0) for v in normalized.values() if v.get("team_side") == "friendly"), default=0)
    max_enemy_local = max((int(v.get("team_number_local") or 0) for v in normalized.values() if v.get("team_side") == "enemy"), default=0)
    next_friendly_local = max_friendly_local + 1
    next_enemy_local = max_enemy_local + 1
    for item in normalized.values():
        if item.get("team_number") is not None:
            continue
        if item.get("team_side") == "friendly":
            item["team_number"] = next_display
            item["team_number_local"] = next_friendly_local
            next_display += 1
            next_friendly_local += 1
        elif item.get("team_side") == "enemy":
            item["team_number"] = next_display
            item["team_number_local"] = next_enemy_local
            next_display += 1
            next_enemy_local += 1

    return normalized


def _draw_ship_icon(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    code: str,
    fill_color: Tuple[int, int, int] | None,
    outline_color: Tuple[int, int, int],
    size: int = 7,
) -> None:
    if code == "DD":
        draw.polygon([(cx, cy - size - 1), (cx - size, cy + size), (cx + size, cy + size)], fill=fill_color, outline=outline_color)
    elif code == "BB":
        draw.polygon(
            [
                (cx - size - 1, cy),
                (cx - size // 2, cy - size),
                (cx + size // 2, cy - size),
                (cx + size + 1, cy),
                (cx + size // 2, cy + size),
                (cx - size // 2, cy + size),
            ],
            fill=fill_color,
            outline=outline_color,
        )
    elif code == "CA":
        draw.polygon([(cx, cy - size), (cx + size, cy), (cx, cy + size), (cx - size, cy)], fill=fill_color, outline=outline_color)
    elif code == "CV":
        draw.rectangle([cx - size, cy - size + 1, cx + size, cy + size - 1], fill=fill_color, outline=outline_color)
        if fill_color is not None:
            draw.line([(cx, cy - size + 2), (cx, cy + size - 2)], fill=(30, 30, 30), width=1)
    elif code == "SS":
        draw.ellipse([cx - size - 1, cy - size // 2, cx + size + 1, cy + size // 2], fill=fill_color, outline=outline_color)
        draw.rectangle([cx - 2, cy - size, cx + 2, cy - size // 2], fill=fill_color, outline=outline_color)
    else:
        draw.ellipse([cx - size, cy - size, cx + size, cy + size], fill=fill_color, outline=outline_color)


@lru_cache(maxsize=64)
def _wg_outline_icon_mask(ship_type: str, size: int) -> Image.Image | None:
    base_icon = _load_wg_class_icons().get(ship_type)
    if base_icon is None:
        return None
    target = max(12, size * 2 + 6)
    icon = base_icon.resize((target, target), Image.Resampling.LANCZOS)
    alpha = icon.getchannel("A")
    inner = alpha.filter(ImageFilter.MinFilter(3))
    # Thin one-pixel inner edge mask for sunk-outline rendering.
    edge = ImageChops.subtract(alpha, inner)
    return edge


def _heading_bucket(heading_deg: float, bucket_deg: float = 5.0) -> int:
    return int(round((heading_deg % 360.0) / bucket_deg)) % int(round(360.0 / bucket_deg))


@lru_cache(maxsize=4096)
def _ship_marker_image(
    ship_type: str,
    code: str,
    color: Tuple[int, int, int],
    size: int,
    sunk: bool,
    heading_bucket: int,
    bucket_deg: float = 5.0,
) -> Image.Image:
    heading_deg = (heading_bucket * bucket_deg) % 360.0
    if sunk:
        outline_color = (150, 150, 150)
        edge_mask = _wg_outline_icon_mask(ship_type, size)
        icon = None
        if edge_mask is not None:
            icon = Image.new("RGBA", edge_mask.size, (outline_color[0], outline_color[1], outline_color[2], 0))
            icon.putalpha(edge_mask)
    else:
        icon = _wg_tinted_icon(ship_type, color, size)

    if icon is not None:
        return icon.rotate(-(heading_deg + WG_ICON_HEADING_OFFSET_DEG), resample=Image.Resampling.BICUBIC, expand=True)

    local_size = max(18, size * 3)
    local = Image.new("RGBA", (local_size, local_size), (0, 0, 0, 0))
    local_draw = ImageDraw.Draw(local)
    lc = local_size // 2
    if sunk:
        _draw_ship_icon(
            local_draw,
            lc,
            lc,
            code,
            fill_color=None,
            outline_color=(150, 150, 150),
            size=max(4, size),
        )
    else:
        _draw_ship_icon(
            local_draw,
            lc,
            lc,
            code,
            fill_color=color,
            outline_color=(220, 220, 220),
            size=max(4, size),
        )
    return local.rotate(-(heading_deg + WG_ICON_HEADING_OFFSET_DEG), resample=Image.Resampling.BICUBIC, expand=True)


def _draw_ship_marker(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    ship_type: str,
    code: str,
    color: Tuple[int, int, int],
    heading_deg: float,
    marker_label: Any,
    size: int,
    sunk: bool = False,
) -> None:
    icon = _ship_marker_image(ship_type, code, color, size, sunk, _heading_bucket(heading_deg))
    x = cx - icon.width // 2
    y = cy - icon.height // 2
    img.paste(icon, (x, y), icon)

    # Marker name overlay above icon.
    txt = _marker_name_text(marker_label)
    if txt:
        label = _text_sprite(txt, max(9, size + 3), (255, 255, 255), (0, 0, 0))
        if label is not None:
            tx = cx - label.width // 2
            ty = cy - size - label.height - 4
            img.paste(label, (tx, ty), label)


def _draw_lineup_panel(draw: ImageDraw.ImageDraw, render_tracks: Dict[str, Dict[str, Any]], canvas_size: int) -> None:
    friendly = [v for v in render_tracks.values() if v.get("team_side") == "friendly"]
    enemy = [v for v in render_tracks.values() if v.get("team_side") == "enemy"]
    friendly.sort(key=lambda v: int(v.get("team_number_local") or 999))
    enemy.sort(key=lambda v: int(v.get("team_number_local") or 999))

    font = _load_font(10)
    line_h = 12
    col_w = max(220, canvas_size // 3)
    rows = max(len(friendly), len(enemy), 12)
    panel_h = 20 + rows * line_h + 8
    left_x = 8
    top_y = 48
    right_x = canvas_size - col_w - 8

    draw.rectangle([left_x, top_y, left_x + col_w, top_y + panel_h], fill=None, outline=(90, 120, 90))
    draw.rectangle([right_x, top_y, right_x + col_w, top_y + panel_h], fill=None, outline=(130, 70, 70))
    draw.text((left_x + 6, top_y + 4), "Friendly lineup", fill=COLOR_FRIENDLY, font=font)
    draw.text((right_x + 6, top_y + 4), "Enemy lineup", fill=COLOR_ENEMY, font=font)

    def _line_text(item: Dict[str, Any]) -> str:
        num = _lineup_number_text(item.get("team_number_local"))
        ship_code = _ship_class_code(item.get("ship_id"))
        name = str(item.get("player_name") or "unknown")
        return f"{num:>2} {ship_code} {name}"

    for i in range(rows):
        y = top_y + 20 + i * line_h
        if i < len(friendly):
            draw.text((left_x + 6, y), _line_text(friendly[i]), fill=(220, 235, 220), font=font)
        if i < len(enemy):
            draw.text((right_x + 6, y), _line_text(enemy[i]), fill=(235, 220, 220), font=font)


def _build_frame_base(
    canonical: Dict[str, Any],
    render_tracks: Dict[str, Dict[str, Any]],
    canvas_size: int,
    margin: int,
    show_grid: bool,
    header_font_size: int,
    bg_color: Tuple[int, int, int] = COLOR_BG,
) -> Image.Image:
    img = Image.new("RGB", (canvas_size, canvas_size), bg_color)
    img = _apply_map_background(img, canonical, margin)
    draw = ImageDraw.Draw(img)

    if show_grid:
        grid_steps = 9 if canvas_size >= 800 else 7
        grid_divisor = max(1, grid_steps - 1)
        for i in range(grid_steps):
            x = margin + i * (canvas_size - 2 * margin) // grid_divisor
            draw.line([(x, margin), (x, canvas_size - margin)], fill=(35, 55, 85), width=1)
            draw.line([(margin, x), (canvas_size - margin, x)], fill=(35, 55, 85), width=1)
        if canvas_size >= 800:
            draw.rectangle([margin, margin, canvas_size - margin, canvas_size - margin], outline=(60, 90, 130), width=2)

    friendly_total = sum(1 for tr in render_tracks.values() if tr.get("team_side") == "friendly")
    enemy_total = sum(1 for tr in render_tracks.values() if tr.get("team_side") == "enemy")
    count_sprite = _text_sprite(f"friendly {friendly_total} | enemy {enemy_total}", header_font_size, (220, 220, 220))
    title_sprite = _text_sprite(_map_title(canonical), header_font_size, (220, 220, 220))
    _paste_sprite(img, count_sprite, 10, 10)
    _paste_sprite(img, title_sprite, 10, 10 + max(16, header_font_size + 5))
    _draw_lineup_panel(draw, render_tracks, canvas_size)
    return img


def _prepare_track_render_data(
    render_tracks: Dict[str, Dict[str, Any]],
    half: float,
    canvas_size: int,
    margin: int,
) -> Dict[str, Dict[str, Any]]:
    prepared: Dict[str, Dict[str, Any]] = {}
    for entity_key, track in render_tracks.items():
        points = list(track.get("points", []) or [])
        if not points:
            continue
        times = [float(p.get("t", 0.0)) for p in points]
        pixels = [
            _to_px(float(p.get("x", 0.0)), float(p.get("z", 0.0)), half, canvas_size, margin)
            for p in points
        ]
        prepared[str(entity_key)] = {
            "track": track,
            "points": points,
            "times": times,
            "pixels": pixels,
            "ship_type": _ship_type(track.get("ship_id")),
            "ship_class": _ship_class_code(track.get("ship_id")),
        }
    return prepared


def _capture_timeline(canonical: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = canonical.get("events", {}) or {}
    raw = events.get("captures", [])
    if not isinstance(raw, list):
        return []
    timeline: List[Dict[str, Any]] = []
    for snap in raw:
        if not isinstance(snap, dict):
            continue
        time_s = float(snap.get("time_s", 0.0) or 0.0)
        team_scores_raw = snap.get("team_scores", {})
        team_scores: Dict[int, int] = {}
        if isinstance(team_scores_raw, dict):
            for key, value in team_scores_raw.items():
                team_id = _safe_int(key)
                score = _safe_int(value)
                if team_id is None or score is None:
                    continue
                team_scores[team_id] = score
        caps_raw = snap.get("caps", [])
        caps = caps_raw if isinstance(caps_raw, list) else []
        timeline.append(
            {
                "time_s": time_s,
                "team_scores": team_scores,
                "team_win_score": _safe_int(snap.get("team_win_score")) or 0,
                "caps": caps,
            }
        )
    timeline.sort(key=lambda item: float(item.get("time_s", 0.0)))
    return timeline


def _capture_snapshot_at(timeline: List[Dict[str, Any]], t: float) -> Optional[Dict[str, Any]]:
    if not timeline:
        return None
    last = timeline[0]
    for snap in timeline:
        if float(snap.get("time_s", 0.0)) <= t + 1e-6:
            last = snap
        else:
            break
    return last


def _resolve_score_team_ids(canonical: Dict[str, Any], team_scores: Dict[int, int]) -> Tuple[Optional[int], Optional[int]]:
    meta = canonical.get("meta", {}) or {}
    local_team_id = _safe_int(meta.get("local_team_id"))
    enemy_team_id = _safe_int(meta.get("enemy_team_id"))

    ids = sorted(team_scores.keys())
    if local_team_id is None and ids:
        local_team_id = ids[0]
    if enemy_team_id is None and local_team_id is not None:
        enemy_team_id = next((tid for tid in ids if tid != local_team_id), None)
    if enemy_team_id is None and len(ids) >= 2:
        enemy_team_id = ids[1]
    return local_team_id, enemy_team_id


def _team_color_for_id(team_id: Optional[int], local_team_id: Optional[int], enemy_team_id: Optional[int]) -> Tuple[int, int, int]:
    if team_id is None or team_id < 0:
        return COLOR_UNKNOWN
    if local_team_id is not None and team_id == local_team_id:
        return COLOR_FRIENDLY
    if enemy_team_id is not None and team_id == enemy_team_id:
        return COLOR_ENEMY
    return COLOR_UNKNOWN


def _draw_score_overlay(img: Image.Image, canonical: Dict[str, Any], snapshot: Optional[Dict[str, Any]], canvas_size: int) -> None:
    team_scores: Dict[int, int] = {}
    team_win_score = 0

    if isinstance(snapshot, dict):
        snap_scores = snapshot.get("team_scores", {})
        if isinstance(snap_scores, dict):
            for key, value in snap_scores.items():
                team_id = _safe_int(key)
                score = _safe_int(value)
                if team_id is None or score is None:
                    continue
                team_scores[team_id] = score
        team_win_score = _safe_int(snapshot.get("team_win_score")) or 0

    if not team_scores:
        stats = canonical.get("stats", {}) or {}
        raw_final = stats.get("team_scores_final", {})
        if isinstance(raw_final, dict):
            for key, value in raw_final.items():
                team_id = _safe_int(key)
                score = _safe_int(value)
                if team_id is None or score is None:
                    continue
                team_scores[team_id] = score
        team_win_score = _safe_int(stats.get("team_win_score")) or team_win_score

    if not team_scores:
        return

    local_team_id, enemy_team_id = _resolve_score_team_ids(canonical, team_scores)
    ids = sorted(team_scores.keys())
    left_id = local_team_id if local_team_id in team_scores else (ids[0] if ids else None)
    right_id = enemy_team_id if enemy_team_id in team_scores else next((tid for tid in ids if tid != left_id), None)
    if right_id is None and len(ids) >= 2:
        right_id = ids[1]

    left_score = team_scores.get(left_id, 0) if left_id is not None else 0
    right_score = team_scores.get(right_id, 0) if right_id is not None else 0

    font_score_size = max(14, canvas_size // 36)
    font_sub_size = max(9, canvas_size // 78)
    left_txt = str(left_score)
    right_txt = str(right_score)
    sep_txt = ":"
    gap = 8

    left_color = _team_color_for_id(left_id, local_team_id, enemy_team_id)
    right_color = _team_color_for_id(right_id, local_team_id, enemy_team_id)
    left_sprite = _text_sprite(left_txt, font_score_size, left_color, (0, 0, 0))
    sep_sprite = _text_sprite(sep_txt, font_score_size, (220, 220, 220), (0, 0, 0))
    right_sprite = _text_sprite(right_txt, font_score_size, right_color, (0, 0, 0))
    if left_sprite is None or sep_sprite is None or right_sprite is None:
        return
    lw = left_sprite.width
    sw = sep_sprite.width
    rw = right_sprite.width
    total_w = lw + sw + rw + gap * 2
    x = canvas_size // 2 - total_w // 2
    y = 8

    _paste_sprite(img, left_sprite, x, y)
    x += lw + gap
    _paste_sprite(img, sep_sprite, x, y)
    x += sw + gap
    _paste_sprite(img, right_sprite, x, y)

    if team_win_score > 0:
        sub = f"target {team_win_score}"
        sub_sprite = _text_sprite(sub, font_sub_size, (200, 200, 200), (0, 0, 0))
        if sub_sprite is None:
            return
        tw = sub_sprite.width
        tx = canvas_size // 2 - tw // 2
        ty = y + left_sprite.height + 1
        _paste_sprite(img, sub_sprite, tx, ty)


def _cap_label(index: Any, fallback_i: int) -> str:
    idx = _safe_int(index)
    if idx is None or idx < 0:
        return str(fallback_i + 1)
    if idx < 26:
        return chr(ord("A") + idx)
    return str(idx + 1)


def _draw_capture_overlay(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    canonical: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
    half: float,
    canvas_size: int,
    margin: int,
) -> None:
    draw_rgba = ImageDraw.Draw(img, "RGBA")
    meta = canonical.get("meta", {}) or {}
    control_points = meta.get("control_points", [])
    if not isinstance(control_points, list):
        control_points = []

    caps_by_id: Dict[int, Dict[str, Any]] = {}
    team_scores: Dict[int, int] = {}
    if isinstance(snapshot, dict):
        snap_scores = snapshot.get("team_scores", {})
        if isinstance(snap_scores, dict):
            for key, value in snap_scores.items():
                team_id = _safe_int(key)
                score = _safe_int(value)
                if team_id is None or score is None:
                    continue
                team_scores[team_id] = score
        for cap in snapshot.get("caps", []):
            if not isinstance(cap, dict):
                continue
            cap_id = _safe_int(cap.get("entity_id"))
            if cap_id is None:
                continue
            caps_by_id[cap_id] = cap

    if not control_points and caps_by_id:
        control_points = list(caps_by_id.values())

    if not control_points:
        return

    local_team_id, enemy_team_id = _resolve_score_team_ids(canonical, team_scores)
    if local_team_id is None:
        local_team_id = _safe_int(meta.get("local_team_id"))
    if enemy_team_id is None:
        enemy_team_id = _safe_int(meta.get("enemy_team_id"))

    font = _load_font(max(10, canvas_size // 70))

    def _as_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    ordered = sorted(control_points, key=lambda row: (_safe_int(row.get("index")) if _safe_int(row.get("index")) is not None else 999, _safe_int(row.get("entity_id")) or 0))
    for i, cp in enumerate(ordered):
        if not isinstance(cp, dict):
            continue
        cp_id = _safe_int(cp.get("entity_id"))
        current = caps_by_id.get(cp_id, cp) if cp_id is not None else cp

        x = _as_float(current.get("x", cp.get("x", 0.0)), 0.0)
        z = _as_float(current.get("z", cp.get("z", 0.0)), 0.0)
        px, py = _to_px(x, z, half, canvas_size, margin)

        radius_world = _as_float(current.get("radius", cp.get("radius", 0.0)), 0.0)
        if radius_world > 0.0:
            radius_px = max(14, int(radius_world / (2.0 * half) * (canvas_size - 2 * margin)))
        else:
            radius_px = max(14, int((canvas_size - 2 * margin) * 0.03))

        cap_team_id = _safe_int(current.get("team_id"))
        if cap_team_id is None:
            cap_team_id = _safe_int(cp.get("team_id"))
        invader_team_id = _safe_int(current.get("invader_team_id"))
        has_invaders = bool(current.get("has_invaders", False))
        both_inside = bool(current.get("both_inside", False))
        progress = max(0.0, min(1.0, _as_float(current.get("progress", 0.0), 0.0)))

        if both_inside:
            ring_color = (240, 200, 90)
        elif has_invaders:
            ring_color = _team_color_for_id(invader_team_id, local_team_id, enemy_team_id)
        else:
            # Neutral points must stay gray/white until captured by a team.
            if cap_team_id is None or cap_team_id < 0:
                ring_color = (205, 205, 205)
            else:
                ring_color = _team_color_for_id(cap_team_id, local_team_id, enemy_team_id)

        if both_inside:
            fill_alpha = 58
        elif has_invaders:
            fill_alpha = 52
        elif cap_team_id is None or cap_team_id < 0:
            fill_alpha = 26
        else:
            fill_alpha = 42
        fill_color = (ring_color[0], ring_color[1], ring_color[2], fill_alpha)
        draw_rgba.ellipse([px - radius_px, py - radius_px, px + radius_px, py + radius_px], fill=fill_color)
        draw.ellipse([px - radius_px, py - radius_px, px + radius_px, py + radius_px], outline=ring_color, width=2)

        if has_invaders and progress > 0.0:
            arc_pad = max(2, radius_px // 6)
            draw.arc(
                [px - radius_px + arc_pad, py - radius_px + arc_pad, px + radius_px - arc_pad, py + radius_px - arc_pad],
                start=-90,
                end=-90 + int(360 * progress),
                fill=ring_color,
                width=3,
            )

        label = _cap_label(current.get("index", cp.get("index")), i)
        status = ""
        if both_inside:
            status = "contested"
        elif has_invaders:
            capture_time = _as_float(current.get("capture_time_s", cp.get("capture_time_s", 0.0)), 0.0)
            capture_speed = _as_float(current.get("capture_speed", 0.0), 0.0)
            if capture_speed > 1e-4:
                remaining = max(0.0, (1.0 - progress) / capture_speed)
            elif capture_time > 0.0:
                remaining = max(0.0, (1.0 - progress) * capture_time)
            else:
                remaining = 0.0
            if remaining > 0.0:
                status = f"{int(math.ceil(remaining))}s"
            elif progress > 0.0:
                status = f"{int(round(progress * 100))}%"

        text = f"{label} {status}".strip()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = px - tw // 2
        ty = py - radius_px - th - 2
        draw.text((tx + 1, ty + 1), text, fill=(0, 0, 0), font=font)
        draw.text((tx, ty), text, fill=ring_color, font=font)
        draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=ring_color, outline=ring_color)


def _yaw_to_heading_deg(yaw_value: Any) -> float:
    try:
        yaw = float(yaw_value)
    except (TypeError, ValueError):
        return 0.0
    if abs(yaw) <= (2.0 * math.pi + 0.5):
        return math.degrees(yaw) % 360.0
    return yaw % 360.0


def _angle_delta_deg(target: float, base: float) -> float:
    return ((target - base + 180.0) % 360.0) - 180.0


def _lerp_angle_deg(base: float, target: float, factor: float) -> float:
    return (base + _angle_delta_deg(target, base) * factor) % 360.0


def _movement_heading_deg(points: List[Dict[str, Any]], window: int = 10, min_segment: float = 0.35) -> float | None:
    if len(points) < 2:
        return None
    tail = points[-window:]
    sum_dx = 0.0
    sum_dz = 0.0
    for i in range(1, len(tail)):
        x1 = float(tail[i - 1].get("x", 0.0))
        z1 = float(tail[i - 1].get("z", 0.0))
        x2 = float(tail[i].get("x", 0.0))
        z2 = float(tail[i].get("z", 0.0))
        dx = x2 - x1
        dz = z2 - z1
        dist = math.hypot(dx, dz)
        if dist < min_segment:
            continue
        sum_dx += dx
        sum_dz += dz
    if abs(sum_dx) < 1e-6 and abs(sum_dz) < 1e-6:
        return None
    return math.degrees(math.atan2(sum_dx, sum_dz)) % 360.0


def _yaw_mean_heading_deg(points: List[Dict[str, Any]], window: int = 10) -> float:
    tail = points[-window:]
    vals = [_yaw_to_heading_deg(p.get("yaw", 0.0)) for p in tail]
    if not vals:
        return 0.0
    s = sum(math.sin(math.radians(v)) for v in vals)
    c = sum(math.cos(math.radians(v)) for v in vals)
    if abs(s) < 1e-9 and abs(c) < 1e-9:
        return vals[-1]
    return math.degrees(math.atan2(s, c)) % 360.0


def _stable_heading_deg(points: List[Dict[str, Any]], previous: float | None = None, max_step_deg: float = 30.0) -> float:
    if not points:
        return previous if previous is not None else 0.0

    yaw_heading = _yaw_mean_heading_deg(points, window=10)
    move_heading = _movement_heading_deg(points, window=10, min_segment=0.35)
    if move_heading is None:
        raw = yaw_heading
    else:
        # Movement direction is usually less jittery than raw yaw for icon facing.
        raw = _lerp_angle_deg(yaw_heading, move_heading, 0.75)

    if previous is None:
        return raw

    delta = _angle_delta_deg(raw, previous)
    if delta > max_step_deg:
        raw = (previous + max_step_deg) % 360.0
    elif delta < -max_step_deg:
        raw = (previous - max_step_deg) % 360.0

    return _lerp_angle_deg(previous, raw, 0.55)


def render_static(canonical: Dict[str, Any], canvas_size: int = 1024, show_labels: bool = True, show_grid: bool = True, bg_color: Tuple[int, int, int] = COLOR_BG) -> Image.Image:
    img = Image.new("RGB", (canvas_size, canvas_size), bg_color)
    font = _load_font(12)
    half = _world_half(canonical)
    margin = 40
    death_times = _find_death_times(canonical)
    render_tracks = _normalize_render_tracks(canonical)
    img = _build_frame_base(canonical, render_tracks, canvas_size, margin, show_grid, 12, bg_color=bg_color)
    draw = ImageDraw.Draw(img)
    battle_end = float(canonical.get("stats", {}).get("battle_end_s", 0.0))
    if battle_end <= 0:
        battle_end = max((float(p.get("t", 0.0)) for t in render_tracks.values() for p in t.get("points", [])), default=0.0)
    spot_timeout = 10.0
    capture_timeline = _capture_timeline(canonical)
    capture_snapshot = _capture_snapshot_at(capture_timeline, battle_end)
    torpedo_tracks = _extract_torpedo_tracks(canonical)
    kill_feed = _extract_kill_feed(canonical)

    _draw_capture_overlay(img, draw, canonical, capture_snapshot, half, canvas_size, margin)
    _draw_torpedoes(draw, torpedo_tracks, battle_end, half, canvas_size, margin)

    ordered = sorted(render_tracks.items(), key=lambda kv: kv[1].get("team_side", "unknown"))
    bucket_counts: Dict[Tuple[int, int], int] = {}
    for entity_key, track in ordered:
        pts = track.get("points", [])
        if not pts:
            continue
        ship_type = _ship_type(track.get("ship_id"))
        ship_class = _ship_class_code(track.get("ship_id"))
        death_t = death_times.get(str(entity_key))
        sunk = death_t is not None and battle_end >= death_t
        last_t = float(pts[-1].get("t", 0.0))
        heading_deg = _stable_heading_deg(pts, previous=None)
        spotted = (battle_end - last_t) <= spot_timeout and not bool(track.get("always_unspotted", False))
        ever_spotted = (not bool(track.get("always_unspotted", False))) and bool(pts)
        color = _status_color(_color_side(track), spotted=spotted, sunk=sunk, ever_spotted=ever_spotted)
        poly = [_to_px(float(p.get("x", 0.0)), float(p.get("z", 0.0)), half, canvas_size, margin) for p in pts]
        if len(poly) >= 2:
            trail_color = tuple(max(0, c // 2) for c in color)
            _draw_polyline_with_gaps(draw, poly[-70:], trail_color, width=2, max_jump_px=34)

        sx, sy = poly[0]
        ex, ey = poly[-1]
        bucket = (ex // 16, ey // 16)
        idx = bucket_counts.get(bucket, 0)
        bucket_counts[bucket] = idx + 1
        ex, ey = _spread_marker_position(ex, ey, idx, cell=16)
        draw.ellipse([sx - 3, sy - 3, sx + 3, sy + 3], fill=tuple(max(0, c // 2) for c in color), outline=color)
        _draw_ship_marker(
            img,
            draw,
            ex,
            ey,
            ship_type,
            ship_class,
            color,
            heading_deg,
            track.get("player_name"),
            size=8,
            sunk=sunk,
        )

        if show_labels:
            player_name = track.get("player_name") or f"entity_{entity_key}"
            ship_name = _ship_name(track.get("ship_id"))
            num_txt = _lineup_number_text(track.get("team_number_local"))
            if ship_name:
                label = f"#{num_txt} {player_name} {ship_class} {ship_name}"
            else:
                label = f"#{num_txt} {player_name} {ship_class}"
            draw.text((ex + 8, ey - 8), str(label), fill=color, font=font)

    duration = int(battle_end)
    duration_sprite = _text_sprite(f"duration={duration}s tracked={len(render_tracks)}", 12, (220, 220, 220))
    _paste_sprite(img, duration_sprite, 10, canvas_size - 22)
    _draw_score_overlay(img, canonical, capture_snapshot, canvas_size)
    _draw_kill_feed_panel(img, draw, canonical, render_tracks, kill_feed, battle_end, canvas_size)
    return img


def render_gif_frames(canonical: Dict[str, Any], canvas_size: int = 600, speed: int = 3, show_grid: bool = True) -> List[Image.Image]:
    half = _world_half(canonical)
    margin = 40
    death_times = _find_death_times(canonical)
    render_tracks = _normalize_render_tracks(canonical)
    prepared_tracks = _prepare_track_render_data(render_tracks, half, canvas_size, margin)
    max_clock = float(canonical.get("stats", {}).get("battle_end_s", 0.0))
    if max_clock <= 0:
        max_clock = max((float(p.get("t", 0.0)) for t in render_tracks.values() for p in t.get("points", [])), default=0.0)
    spot_timeout = max(6.0, float(speed) * 1.5)
    capture_timeline = _capture_timeline(canonical)
    artillery_traces = _extract_artillery_traces(canonical)
    torpedo_tracks = _extract_torpedo_tracks(canonical)
    kill_feed = _extract_kill_feed(canonical)
    heading_memory: Dict[str, float] = {}
    ever_spotted_memory: Dict[str, bool] = {}
    base_frame = _build_frame_base(canonical, render_tracks, canvas_size, margin, show_grid, 11)

    frames: List[Image.Image] = []
    t = 0.0
    while t <= max_clock + speed:
        img = base_frame.copy()
        draw = ImageDraw.Draw(img)
        bucket_counts: Dict[Tuple[int, int], int] = {}
        capture_snapshot = _capture_snapshot_at(capture_timeline, t)
        _draw_capture_overlay(img, draw, canonical, capture_snapshot, half, canvas_size, margin)
        _draw_artillery_traces(img, artillery_traces, t, half, canvas_size, margin)
        _draw_torpedoes(draw, torpedo_tracks, t, half, canvas_size, margin)

        for entity_key, prepared in prepared_tracks.items():
            track = prepared["track"]
            all_points = prepared["points"]
            times = prepared["times"]
            pixels = prepared["pixels"]
            if not all_points or not times or not pixels:
                continue
            ekey = str(entity_key)
            side = _color_side(track)
            idx = bisect_right(times, t) - 1
            synthetic_start = False
            if idx < 0:
                if side == "enemy" and not ever_spotted_memory.get(ekey, False):
                    # Enemy ships should not render before first spot.
                    continue
                # Show known participants from t=0 using first known position as unspotted placeholder.
                idx = 0
                synthetic_start = True
            points = all_points[max(0, idx - 9) : idx + 1]
            last_t = times[idx]
            prev_heading = heading_memory.get(str(entity_key))
            heading_deg = _stable_heading_deg(points, previous=prev_heading, max_step_deg=32.0)
            heading_memory[str(entity_key)] = heading_deg
            spotted = (t - last_t) <= spot_timeout and not synthetic_start and not bool(track.get("always_unspotted", False))
            death_t = death_times.get(str(entity_key))
            sunk = death_t is not None and t >= death_t
            if spotted:
                ever_spotted_memory[ekey] = True
            ever_spotted = ever_spotted_memory.get(ekey, False)
            if side == "enemy" and (not ever_spotted) and (not spotted):
                # Enemy ships remain hidden until they are first spotted.
                continue
            color = _status_color(side, spotted=spotted, sunk=sunk, ever_spotted=ever_spotted)
            poly = pixels[max(0, idx - 23) : idx + 1]
            if len(poly) >= 2:
                _draw_polyline_with_gaps(
                    draw,
                    poly,
                    tuple(max(0, c // 2) for c in color),
                    width=2,
                    max_jump_px=24,
                )
            cx, cy = poly[-1]
            bucket = (cx // 14, cy // 14)
            idx = bucket_counts.get(bucket, 0)
            bucket_counts[bucket] = idx + 1
            cx, cy = _spread_marker_position(cx, cy, idx, cell=14)
            _draw_ship_marker(
                img,
                draw,
                cx,
                cy,
                prepared["ship_type"],
                prepared["ship_class"],
                color,
                heading_deg,
                track.get("player_name"),
                size=6,
                sunk=sunk,
            )

        mins, secs = divmod(int(t), 60)
        _paste_sprite(img, _text_sprite(f"{mins}:{secs:02d}", 11, (220, 220, 220)), canvas_size - 80, 10)
        _draw_score_overlay(img, canonical, capture_snapshot, canvas_size)
        _draw_kill_feed_panel(img, draw, canonical, render_tracks, kill_feed, t, canvas_size)
        frames.append(img)
        t += max(1, speed)

    return frames
