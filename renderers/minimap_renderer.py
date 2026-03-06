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
RIBBON_ID_TO_ASSET = {
    0: "main_caliber",
    1: "torpedo",
    2: "bomb",
    3: "plane",
    4: "crit",
    5: "frag",
    6: "burn",
    7: "flood",
    8: "citadel",
    9: "base_defense",
    10: "base_capture",
    11: "base_capture_assist",
    12: "suppressed",
    13: "secondary_caliber",
    14: "main_caliber",
    15: "main_caliber",
    16: "main_caliber",
    17: "main_caliber",
    18: "building_kill",
    19: "detected",
    20: "bomb",
    21: "bomb",
    22: "bomb",
    23: "bomb",
    24: "rocket",
    25: "rocket",
    26: "rocket",
    27: "splane",
    28: "main_caliber",
    29: "bomb",
    30: "rocket",
    31: "dbomb",
    32: "acoustic_hit",
    33: "drop",
    34: "rocket",
    35: "rocket",
    39: "acoustic_hit",
    40: "acoustic_hit",
    41: "acoustic_hit",
    43: "dbomb",
    44: "dbomb",
    45: "mine",
    46: "demining_mine",
    47: "demining_minefield",
}


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=64)
def _load_font(size: int, bold: bool = False):
    font_names = (
        ["segoeuib.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf"]
        if bold
        else ["segoeui.ttf", "segoeuib.ttf", "arial.ttf", "arialbd.ttf"]
    )
    for font_name in font_names:
        try:
            return ImageFont.truetype(font_name, size)
        except Exception:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=2048)
def _text_sprite(
    text: str,
    size: int,
    fill: Tuple[int, int, int],
    shadow: Tuple[int, int, int] | None = None,
    bold: bool = False,
    stroke_width: int = 0,
    stroke_fill: Tuple[int, int, int] | None = None,
) -> Image.Image | None:
    if not text:
        return None
    font = _load_font(size, bold=bold)
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    probe_draw = ImageDraw.Draw(probe)
    bbox = probe_draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    shadow_pad = 1 if shadow is not None else 0
    width = max(1, (bbox[2] - bbox[0]) + shadow_pad + stroke_width + 1)
    height = max(1, (bbox[3] - bbox[1]) + shadow_pad + stroke_width + 1)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ox = -bbox[0]
    oy = -bbox[1]
    if shadow is not None:
        draw.text((ox + 1, oy + 1), text, fill=shadow, font=font, stroke_width=stroke_width, stroke_fill=stroke_fill)
    draw.text((ox, oy), text, fill=fill, font=font, stroke_width=stroke_width, stroke_fill=stroke_fill)
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


def _battle_hud_dir() -> Path:
    return _root_dir() / "gui" / "battle_hud"


def _ship_previews_dir() -> Path:
    return _root_dir() / "gui" / "ship_previews"


def _ship_icons_dir() -> Path:
    return _root_dir() / "gui" / "ship_icons"


def _ships_silhouettes_dir() -> Path:
    return _root_dir() / "gui" / "ships_silhouettes"


def _ship_dead_icons_dir() -> Path:
    return _root_dir() / "gui" / "ship_dead_icons"


@lru_cache(maxsize=1)
def _load_gameparams_ship_meta() -> Dict[str, Dict[str, Dict[str, Any]]]:
    gameparams_path = _battle_hud_dir() / "GameParams.json"
    try:
        payload = json.loads(gameparams_path.read_text(encoding="utf-8"))
    except Exception:
        return {"by_id": {}, "by_index": {}, "by_name": {}}

    by_id: Dict[str, Dict[str, Any]] = {}
    by_index: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return {"by_id": by_id, "by_index": by_index, "by_name": by_name}

    for value in payload.values():
        if not isinstance(value, dict):
            continue
        typeinfo = value.get("typeinfo")
        if not isinstance(typeinfo, dict) or str(typeinfo.get("type") or "") != "Ship":
            continue
        ship_id = _safe_int(value.get("id"))
        ship_index = str(value.get("index") or "").strip()
        ship_name = str(value.get("name") or "").strip()
        if ship_id is None or not ship_index:
            continue
        meta = {
            "id": ship_id,
            "index": ship_index,
            "name": ship_name,
            "originShipName": str(value.get("originShipName") or "").strip(),
            "species": str(typeinfo.get("species") or "").strip(),
            "nation": str(typeinfo.get("nation") or "").strip(),
        }
        by_id[str(ship_id)] = meta
        by_index[ship_index] = meta
        if ship_name:
            by_name[ship_name] = meta
    return {"by_id": by_id, "by_index": by_index, "by_name": by_name}


def _gameparams_ship_entry(ship_id: Any) -> Dict[str, Any]:
    key = str(_safe_int(ship_id) or "")
    if not key:
        return {}
    return _load_gameparams_ship_meta().get("by_id", {}).get(key, {})


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


def _ship_preview_cache_dir() -> Path:
    p = _root_dir() / "content" / "wg_ship_previews"
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
def _map_background_layer(url: str, map_size: int, margin: int) -> Image.Image | None:
    icon = _load_map_icon(url)
    if icon is None:
        return None

    usable = map_size - 2 * margin
    if usable <= 0:
        return None

    bg = icon.resize((usable, usable), Image.Resampling.LANCZOS)
    # Keep map readable but subtle so tracks/icons stay visible.
    bg = ImageEnhance.Brightness(bg).enhance(0.75)
    alpha = bg.getchannel("A").point(lambda a: min(165, a))
    bg.putalpha(alpha)

    layer = Image.new("RGBA", (map_size, map_size), (0, 0, 0, 0))
    layer.paste(bg, (margin, margin), bg)
    return layer


def _apply_map_background(img: Image.Image, canonical: Dict[str, Any], margin: int, map_size: int, offset_x: int = 0) -> Image.Image:
    url = _map_icon_url(canonical)
    layer = _map_background_layer(url, map_size, margin) if url else None
    if layer is None:
        return img
    base = img.convert("RGBA")
    base.alpha_composite(layer, (offset_x, 0))
    return base.convert("RGB")


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


def _normalize_vehicle_code(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    match = re.match(r"^[A-Za-z0-9]+", text)
    if match:
        return match.group(0).upper()
    return text.split("_", 1)[0].split("-", 1)[0].strip().upper()


def _player_vehicle_code(canonical: Dict[str, Any], ship_id: Any) -> str:
    meta = canonical.get("meta", {}) or {}
    player_vehicle = _normalize_vehicle_code(meta.get("playerVehicle"))
    if player_vehicle:
        return player_vehicle
    ship_meta = _gameparams_ship_entry(ship_id)
    return _normalize_vehicle_code(ship_meta.get("index"))


def _neutral_ship_silhouette(image: Image.Image, tint: Tuple[int, int, int] = (230, 236, 242)) -> Image.Image | None:
    try:
        rgba = image.convert("RGBA")
    except Exception:
        return None
    alpha = rgba.getchannel("A")
    if alpha.getbbox() is None:
        return None
    if alpha.getextrema() == (255, 255):
        return rgba
    silhouette = Image.new("RGBA", rgba.size, (tint[0], tint[1], tint[2], 0))
    silhouette.putalpha(alpha)
    return silhouette


def _ship_preview_candidates(vehicle_code: str, ship_id: Any) -> List[Path]:
    candidates: List[Path] = []
    if vehicle_code:
        candidates.extend(
            [
                _ship_previews_dir() / f"{vehicle_code}.png",
                _ship_previews_dir() / "medium" / f"{vehicle_code}.png",
            ]
        )
    sid = _safe_int(ship_id)
    if sid is not None and sid >= 0:
        for key in ("contour", "medium", "large", "small"):
            candidates.append(_ship_preview_cache_dir() / f"{sid}_{key}.png")
    if vehicle_code:
        candidates.extend(
            [
                _ship_preview_cache_dir() / f"{vehicle_code}.png",
                _ship_preview_cache_dir() / "medium" / f"{vehicle_code}.png",
            ]
        )
    return candidates


@lru_cache(maxsize=128)
def _load_ship_preview_base(ship_id: int, vehicle_code: str) -> Image.Image | None:
    for path in _ship_preview_candidates(vehicle_code, ship_id):
        if not path.exists():
            continue
        try:
            if path.stat().st_size <= 0:
                continue
        except OSError:
            continue
        try:
            preview = Image.open(path).convert("RGBA")
        except Exception:
            continue
        path_norm = str(path).replace("\\", "/").lower()
        if "/gui/ship_previews/" in path_norm:
            return preview
        normalized = _neutral_ship_silhouette(preview)
        if normalized is not None:
            return normalized
        return preview

    app_id, realm = _read_api_config()
    if not app_id or ship_id < 0:
        return None

    params = urlencode({"application_id": app_id, "ship_id": ship_id, "fields": "images"})
    url = f"{_base_url_for_realm(realm)}encyclopedia/ships/?{params}"
    try:
        payload = json.loads(_download_bytes(url).decode("utf-8"))
    except Exception:
        return None

    data = payload.get("data", {})
    if not isinstance(data, dict) or not data:
        return None
    ship_data = data.get(str(ship_id))
    if not isinstance(ship_data, dict):
        ship_data = next((v for v in data.values() if isinstance(v, dict)), {})
    images = ship_data.get("images", {}) if isinstance(ship_data, dict) else {}
    if not isinstance(images, dict):
        return None

    for key in ("contour", "medium", "large", "small"):
        img_url = str(images.get(key) or "").strip()
        if not img_url:
            continue
        file_path = _ship_preview_cache_dir() / f"{ship_id}_{key}.png"
        try:
            if not file_path.exists():
                file_path.write_bytes(_download_bytes(img_url))
            if file_path.stat().st_size <= 0:
                continue
            preview = Image.open(file_path).convert("RGBA")
            normalized = _neutral_ship_silhouette(preview)
            if normalized is not None:
                return normalized
            return preview
        except Exception:
            continue
    return None


@lru_cache(maxsize=256)
def _load_ship_preview(ship_id: int, vehicle_code: str, max_w: int, max_h: int) -> Image.Image | None:
    base = _load_ship_preview_base(ship_id, vehicle_code)
    if base is None:
        return None
    target_w = max(1, int(max_w))
    target_h = max(1, int(max_h))
    ratio = min(target_w / max(1, base.width), target_h / max(1, base.height))
    ratio = max(1e-6, ratio)
    size = (
        max(1, int(round(base.width * ratio))),
        max(1, int(round(base.height * ratio))),
    )
    if size == base.size:
        return base
    return base.resize(size, Image.Resampling.LANCZOS)


def _resize_fit(base: Image.Image, max_w: int, max_h: int) -> Image.Image:
    target_w = max(1, int(max_w))
    target_h = max(1, int(max_h))
    ratio = min(target_w / max(1, base.width), target_h / max(1, base.height))
    ratio = max(1e-6, ratio)
    size = (
        max(1, int(round(base.width * ratio))),
        max(1, int(round(base.height * ratio))),
    )
    if size == base.size:
        return base
    return base.resize(size, Image.Resampling.LANCZOS)


@lru_cache(maxsize=128)
def _load_ship_alive_icon(vehicle_code: str, max_w: int, max_h: int) -> Image.Image | None:
    vehicle_code = _normalize_vehicle_code(vehicle_code)
    if not vehicle_code:
        return None
    candidates = [
        _ship_icons_dir() / f"{vehicle_code}.png",
        _ships_silhouettes_dir() / f"{vehicle_code}.png",
        _ship_previews_dir() / f"{vehicle_code}.png",
        _ship_previews_dir() / "medium" / f"{vehicle_code}.png",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            if path.stat().st_size <= 0:
                continue
            return _resize_fit(Image.open(path).convert("RGBA"), max_w, max_h)
        except Exception:
            continue
    return None


@lru_cache(maxsize=128)
def _load_ship_dead_icon(vehicle_code: str, max_w: int, max_h: int) -> Image.Image | None:
    vehicle_code = _normalize_vehicle_code(vehicle_code)
    if not vehicle_code:
        return None
    candidates = [_ship_dead_icons_dir() / f"{vehicle_code}.png"]
    base = None
    for path in candidates:
        if not path.exists():
            continue
        try:
            if path.stat().st_size <= 0:
                continue
            base = Image.open(path).convert("RGBA")
            break
        except Exception:
            continue
    if base is None:
        return None
    return _resize_fit(base, max_w, max_h)


def _compose_ship_status_icon(
    alive_icon: Image.Image | None,
    dead_icon: Image.Image | None,
    max_w: int,
    max_h: int,
    hp_ratio: float,
    sunk: bool,
) -> Image.Image | None:
    hp_ratio = max(0.0, min(1.0, float(hp_ratio)))
    if sunk:
        return dead_icon or alive_icon
    if alive_icon is None:
        return dead_icon
    if dead_icon is None:
        return alive_icon

    canvas_w = max(1, int(max_w))
    canvas_h = max(1, int(max_h))
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    dead_x = (canvas_w - dead_icon.width) // 2
    dead_y = (canvas_h - dead_icon.height) // 2
    canvas.paste(dead_icon, (dead_x, dead_y), dead_icon)

    alive_x = (canvas_w - alive_icon.width) // 2
    alive_y = (canvas_h - alive_icon.height) // 2
    alive_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    alive_layer.paste(alive_icon, (alive_x, alive_y), alive_icon)

    visible_w = max(0, min(alive_icon.width, int(round(alive_icon.width * hp_ratio))))
    if visible_w <= 0:
        return canvas

    mask = Image.new("L", (canvas_w, canvas_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rectangle(
        [
            alive_x,
            alive_y,
            alive_x + max(0, visible_w - 1),
            alive_y + alive_icon.height - 1,
        ],
        fill=255,
    )
    canvas = Image.composite(alive_layer, canvas, mask)
    return canvas


@lru_cache(maxsize=1)
def _gameparams_supported_ribbon_ids() -> frozenset[int]:
    gameparams_path = _battle_hud_dir() / "GameParams.json"
    try:
        raw = gameparams_path.read_text(encoding="utf-8")
    except Exception:
        return frozenset(RIBBON_ID_TO_ASSET.keys())

    ribbon_ids: set[int] = set()
    for match in re.finditer(r'"(?:subRibbons|triggerRibbonsTypes)"\s*:\s*\[(.*?)\]', raw, flags=re.S):
        for value in re.findall(r"-?\d+", match.group(1)):
            try:
                ribbon_ids.add(int(value))
            except ValueError:
                continue
    if not ribbon_ids:
        ribbon_ids.update(RIBBON_ID_TO_ASSET.keys())
    return frozenset(ribbon_ids)


@lru_cache(maxsize=1)
def _ribbon_asset_roots() -> Tuple[str, ...]:
    roots: List[str] = []
    local_root = _root_dir() / "gui" / "ribbons"
    if local_root.exists():
        roots.append(str(local_root))
    sub_root = local_root / "subribbons"
    if sub_root.exists():
        roots.append(str(sub_root))
    return tuple(roots)


@lru_cache(maxsize=128)
def _load_ribbon_icon(ribbon_id: int, size: int) -> Image.Image | None:
    rid = int(ribbon_id)
    if rid not in _gameparams_supported_ribbon_ids():
        return None
    asset_name = RIBBON_ID_TO_ASSET.get(rid)
    if not asset_name:
        return None
    for root in _ribbon_asset_roots():
        file_path = Path(root) / f"ribbon_{asset_name}.png"
        if not file_path.exists():
            continue
        try:
            icon = Image.open(file_path).convert("RGBA")
        except Exception:
            continue
        if size > 0 and icon.size != (size, size):
            icon = icon.resize((size, size), Image.Resampling.LANCZOS)
        return icon
    return None


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


def _sidebar_width(map_size: int) -> int:
    width = max(360, min(560, int(map_size * 0.48)))
    if width % 2:
        width += 1
    return width


def _split_lineups(render_tracks: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    friendly = [v for v in render_tracks.values() if v.get("team_side") == "friendly"]
    enemy = [v for v in render_tracks.values() if v.get("team_side") == "enemy"]
    friendly.sort(key=lambda v: int(v.get("team_number_local") or 999))
    enemy.sort(key=lambda v: int(v.get("team_number_local") or 999))
    return friendly, enemy


def _render_layout(render_tracks: Dict[str, Dict[str, Any]], map_size: int) -> Dict[str, Any]:
    sidebar_w = _sidebar_width(map_size)
    total_w = map_size + sidebar_w
    pad = max(10, map_size // 90)
    panel_w = sidebar_w - pad * 2
    top_y = pad
    font_size = max(11, map_size // 82)
    line_h = max(15, font_size + 4)
    header_h = max(22, font_size + 10)
    friendly, enemy = _split_lineups(render_tracks)
    lineup_rows = max(max(len(friendly), len(enemy)), 12)
    lineup_h = header_h + lineup_rows * line_h + 8
    player_h = max(120, min(190, int(map_size * 0.19)))
    lineup_y = max(top_y + player_h + pad * 2 + 96, map_size - lineup_h - pad)
    feed_y = top_y + player_h + pad
    feed_bottom = max(feed_y + 96, lineup_y - pad)
    feed_rect = (map_size + pad, feed_y, map_size + pad + panel_w, feed_bottom)
    col_gap = pad
    col_w = max(120, (panel_w - col_gap) // 2)
    friendly_rect = (map_size + pad, lineup_y, map_size + pad + col_w, lineup_y + lineup_h)
    enemy_rect = (friendly_rect[2] + col_gap, lineup_y, map_size + pad + panel_w, lineup_y + lineup_h)
    return {
        "map_size": map_size,
        "width": total_w,
        "height": map_size,
        "sidebar_x": map_size,
        "sidebar_width": sidebar_w,
        "sidebar_pad": pad,
        "panel_width": panel_w,
        "font_size": font_size,
        "line_h": line_h,
        "header_h": header_h,
        "friendly_items": friendly,
        "enemy_items": enemy,
        "player_rect": (map_size + pad, top_y, map_size + pad + panel_w, top_y + player_h),
        "feed_rect": feed_rect,
        "friendly_rect": friendly_rect,
        "enemy_rect": enemy_rect,
    }


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


def _extract_chat_feed(canonical: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = canonical.get("events", {}) or {}
    raw = events.get("chat", [])
    if not isinstance(raw, list):
        return []
    chat: List[Dict[str, Any]] = []
    for row in raw:
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
    chat.sort(key=lambda item: float(item.get("time_s", 0.0)))
    return chat


def _extract_health_timelines(canonical: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    events = canonical.get("events", {}) or {}
    raw = events.get("health", [])
    if not isinstance(raw, list):
        return {}

    timelines: Dict[str, Dict[str, Any]] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        t = float(row.get("time_s", 0.0) or 0.0)
        entities = row.get("entities", {})
        if not isinstance(entities, dict):
            continue
        for entity_key, state in entities.items():
            if not isinstance(state, dict):
                continue
            key = str(entity_key)
            timeline = timelines.setdefault(key, {"times": [], "hp": [], "alive": [], "max_hp": 0})
            hp = max(0, _safe_int(state.get("hp")) or 0)
            max_hp = max(0, _safe_int(state.get("max_hp")) or 0)
            timeline["times"].append(t)
            timeline["hp"].append(hp)
            timeline["alive"].append(bool(state.get("alive", True)))
            timeline["max_hp"] = max(int(timeline.get("max_hp", 0) or 0), max_hp)
    return timelines


def _health_state_at(health_timelines: Dict[str, Dict[str, Any]], entity_key: Any, t: float) -> Optional[Dict[str, Any]]:
    timeline = health_timelines.get(str(entity_key))
    if not isinstance(timeline, dict):
        return None
    times = timeline.get("times", [])
    hp_values = timeline.get("hp", [])
    alive_values = timeline.get("alive", [])
    if not isinstance(times, list) or not times:
        return None
    idx = bisect_right(times, t) - 1
    if idx < 0:
        idx = 0
    idx = min(idx, len(times) - 1, len(hp_values) - 1, len(alive_values) - 1)
    max_hp = max(0, int(timeline.get("max_hp", 0) or 0))
    hp = max(0, int(hp_values[idx]))
    ratio = float(hp) / float(max_hp) if max_hp > 0 else 0.0
    return {
        "hp": hp,
        "max_hp": max_hp,
        "alive": bool(alive_values[idx]),
        "ratio": max(0.0, min(1.0, ratio)),
    }


def _extract_player_status_timeline(canonical: Dict[str, Any]) -> Dict[str, Any]:
    events = canonical.get("events", {}) or {}
    raw = events.get("player_status", [])
    if not isinstance(raw, list):
        raw = []

    status = {
        "times": [],
        "damage_total": [],
        "ribbons": [],
        "player_name": str((canonical.get("meta", {}) or {}).get("playerName") or "").strip(),
        "ship_entity_key": str((canonical.get("meta", {}) or {}).get("player_ship_entity_id") or ""),
        "ship_id": _safe_int((canonical.get("meta", {}) or {}).get("player_ship_id")) or -1,
        "team_id": _safe_int((canonical.get("meta", {}) or {}).get("local_team_id")) if _safe_int((canonical.get("meta", {}) or {}).get("local_team_id")) is not None else -1,
        "max_health": 0,
    }
    for row in raw:
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
        status["times"].append(float(row.get("time_s", 0.0) or 0.0))
        status["damage_total"].append(float(row.get("damage_total", 0.0) or 0.0))
        status["ribbons"].append(ribbons)
        if str(row.get("player_name") or "").strip():
            status["player_name"] = str(row.get("player_name") or "").strip()
        ship_entity_key = str(row.get("ship_entity_key") or "").strip()
        if ship_entity_key and ship_entity_key != "-1":
            status["ship_entity_key"] = ship_entity_key
        ship_id = _safe_int(row.get("ship_id"))
        if ship_id is not None and ship_id >= 0:
            status["ship_id"] = ship_id
        team_id = _safe_int(row.get("team_id"))
        if team_id is not None and team_id >= 0:
            status["team_id"] = team_id
        status["max_health"] = max(int(status.get("max_health", 0) or 0), max(0, _safe_int(row.get("max_health")) or 0))
    return status


def _player_status_at(status_timeline: Dict[str, Any], t: float) -> Dict[str, Any]:
    times = status_timeline.get("times", [])
    if not isinstance(times, list) or not times:
        return {
            "damage_total": 0.0,
            "ribbons": {},
            "player_name": str(status_timeline.get("player_name") or "").strip(),
            "ship_entity_key": str(status_timeline.get("ship_entity_key") or ""),
            "ship_id": _safe_int(status_timeline.get("ship_id")) or -1,
            "team_id": _safe_int(status_timeline.get("team_id")) if _safe_int(status_timeline.get("team_id")) is not None else -1,
            "max_health": max(0, _safe_int(status_timeline.get("max_health")) or 0),
        }
    idx = bisect_right(times, t) - 1
    if idx < 0:
        idx = 0
    idx = min(idx, len(times) - 1, len(status_timeline.get("damage_total", [])) - 1, len(status_timeline.get("ribbons", [])) - 1)
    return {
        "damage_total": float(status_timeline.get("damage_total", [0.0])[idx] or 0.0),
        "ribbons": dict(status_timeline.get("ribbons", [{}])[idx] or {}),
        "player_name": str(status_timeline.get("player_name") or "").strip(),
        "ship_entity_key": str(status_timeline.get("ship_entity_key") or ""),
        "ship_id": _safe_int(status_timeline.get("ship_id")) or -1,
        "team_id": _safe_int(status_timeline.get("team_id")) if _safe_int(status_timeline.get("team_id")) is not None else -1,
        "max_health": max(0, _safe_int(status_timeline.get("max_health")) or 0),
    }


def _feed_name_key(value: Any) -> str:
    s = str(value or "").strip()
    s = re.sub(r"^\[[^\]]+\]\s*", "", s)
    return _norm_name(s)


def _feed_name_color(team_side: str) -> Tuple[int, int, int]:
    if team_side == "friendly":
        return COLOR_FRIENDLY
    if team_side == "enemy":
        return COLOR_ENEMY
    return (225, 225, 225)


def _player_team_side(render_tracks: Dict[str, Dict[str, Any]], player_name: str) -> str:
    target = _feed_name_key(player_name)
    if not target:
        return "unknown"
    for track in render_tracks.values():
        if _feed_name_key(track.get("player_name")) == target:
            return str(track.get("team_side") or "unknown")
    return "unknown"


def _ship_state_at(track: Dict[str, Any], t: float) -> Optional[Dict[str, float]]:
    points = list(track.get("points", []) or [])
    if not points:
        return None
    times = [float(p.get("t", 0.0)) for p in points]
    idx = bisect_right(times, t) - 1
    if idx < 0:
        idx = 0
    if idx >= len(points) - 1:
        p = points[idx]
        return {
            "x": float(p.get("x", 0.0)),
            "z": float(p.get("z", 0.0)),
            "yaw": float(p.get("yaw", 0.0) or 0.0),
        }
    p0 = points[idx]
    p1 = points[idx + 1]
    t0 = float(p0.get("t", 0.0))
    t1 = float(p1.get("t", t0))
    if t1 <= t0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, (t - t0) / (t1 - t0)))
    return {
        "x": float(p0.get("x", 0.0)) + (float(p1.get("x", 0.0)) - float(p0.get("x", 0.0))) * ratio,
        "z": float(p0.get("z", 0.0)) + (float(p1.get("z", 0.0)) - float(p0.get("z", 0.0))) * ratio,
        "yaw": float(p0.get("yaw", 0.0) or 0.0),
    }


def _estimate_torpedo_speed(tracks: Dict[str, Dict[str, Any]]) -> float:
    samples: List[float] = []
    for track in tracks.values():
        points = track.get("points", [])
        if len(points) < 2:
            continue
        for p0, p1 in zip(points, points[1:5]):
            t0 = float(p0.get("t", 0.0))
            t1 = float(p1.get("t", t0))
            if t1 <= t0:
                continue
            dist = math.hypot(float(p1.get("x", 0.0)) - float(p0.get("x", 0.0)), float(p1.get("z", 0.0)) - float(p0.get("z", 0.0)))
            speed = dist / (t1 - t0)
            if 1.0 <= speed <= 100.0:
                samples.append(speed)
                break
    if not samples:
        return 7.5
    samples.sort()
    return float(samples[len(samples) // 2])


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
                "dir": None,
                "speed": None,
                "predict_s": 0.0,
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

    default_speed = _estimate_torpedo_speed(tracks)
    owner_tracks = canonical.get("tracks", {}) or {}
    for track in tracks.values():
        points = track.get("points", [])
        if not points:
            continue
        direction: Tuple[float, float] | None = None
        speed = default_speed
        if len(points) >= 2:
            p0 = points[0]
            p1 = points[1]
            dt = max(1e-3, float(p1.get("t", 0.0)) - float(p0.get("t", 0.0)))
            dx = float(p1.get("x", 0.0)) - float(p0.get("x", 0.0))
            dz = float(p1.get("z", 0.0)) - float(p0.get("z", 0.0))
            dist = math.hypot(dx, dz)
            if dist >= 1e-6:
                direction = (dx / dist, dz / dist)
                speed = dist / dt
        if direction is None:
            owner_track = owner_tracks.get(str(track.get("owner_entity_key") or ""))
            if isinstance(owner_track, dict):
                ship_state = _ship_state_at(owner_track, float(points[0].get("t", 0.0)))
            else:
                ship_state = None
            if ship_state is not None:
                dx = float(points[0].get("x", 0.0)) - float(ship_state.get("x", 0.0))
                dz = float(points[0].get("z", 0.0)) - float(ship_state.get("z", 0.0))
                dist = math.hypot(dx, dz)
                if dist >= 0.2:
                    direction = (dx / dist, dz / dist)
                else:
                    heading = _yaw_to_heading_deg(ship_state.get("yaw", 0.0))
                    rad = math.radians(heading)
                    direction = (math.sin(rad), math.cos(rad))
        track["dir"] = direction
        track["speed"] = float(speed if speed > 0.0 else default_speed)
        if len(points) == 1:
            track["predict_s"] = 22.0 if str(track.get("team_side")) == "friendly" else 14.0
        else:
            track["predict_s"] = 6.0

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
        last_t = float(times[-1])
        predict_s = float(track.get("predict_s", 0.0) or 0.0)
        direction = track.get("dir")
        speed = float(track.get("speed", 0.0) or 0.0)
        if direction is not None and speed > 0.0 and (t - last_t) <= predict_s:
            dx, dz = direction
            dt = max(0.0, t - last_t)
            return float(points[-1]["x"]) + dx * speed * dt, float(points[-1]["z"]) + dz * speed * dt
        if t - last_t > max_stale_s:
            return None
        return float(points[-1]["x"]), float(points[-1]["z"])

    p0 = points[idx]
    p1 = points[idx + 1]
    t0 = float(p0.get("t", 0.0))
    t1 = float(p1.get("t", t0))
    if t1 <= t0:
        return float(p0.get("x", 0.0)), float(p0.get("z", 0.0))
    if (t1 - t0) > max_gap_s:
        direction = track.get("dir")
        speed = float(track.get("speed", 0.0) or 0.0)
        predict_s = float(track.get("predict_s", 0.0) or 0.0)
        if direction is not None and speed > 0.0 and (t - t0) <= min(predict_s, t1 - t0):
            dx, dz = direction
            dt = max(0.0, t - t0)
            return float(p0.get("x", 0.0)) + dx * speed * dt, float(p0.get("z", 0.0)) + dz * speed * dt
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
    if not points or not times:
        return None
    if len(points) < 2:
        direction = track.get("dir")
        if isinstance(direction, tuple):
            return float(direction[0]), float(direction[1])
        return None
    idx = bisect_right(times, t) - 1
    if idx < 0:
        return None
    if idx >= len(points) - 1:
        direction = track.get("dir")
        if isinstance(direction, tuple):
            return float(direction[0]), float(direction[1])
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
        return "icon_frag_main_caliber.png"
    if reason_code == 2:
        return "icon_frag_atba.png"
    if reason_code in (3, 5, 11, 13):
        return "icon_frag_torpedo.png"
    if reason_code in (4, 28):
        return "icon_frag_bomb.png"
    if reason_code == 6:
        return "icon_frag_burning.png"
    if reason_code == 9:
        return "icon_frag_flood.png"
    if reason_code == 14:
        return "icon_frag_rocket.png"
    if reason_code == 22:
        return "icon_frag_skip.png"

    weapon_kind = str(entry.get("weapon_kind") or "other")
    if weapon_kind == "gun":
        return "icon_frag_main_caliber.png"
    if weapon_kind == "torpedo":
        return "icon_frag_torpedo.png"
    if weapon_kind == "bomb":
        return "icon_frag_bomb.png"
    return "frags.png"


@lru_cache(maxsize=64)
def _load_kill_icon(filename: str, size: int) -> Image.Image | None:
    candidates = [
        _battle_hud_dir() / "icon_frag" / filename,
        _kill_icon_cache_dir() / filename,
    ]
    for file_path in candidates:
        if not file_path.exists():
            continue
        try:
            icon = Image.open(file_path).convert("RGBA")
        except Exception:
            continue
        if size > 0 and icon.size != (size, size):
            icon = icon.resize((size, size), Image.Resampling.LANCZOS)
        return icon
    return None


def _draw_kill_feed_panel(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    canonical: Dict[str, Any],
    render_tracks: Dict[str, Dict[str, Any]],
    kill_feed: List[Dict[str, Any]],
    t: float,
    layout: Dict[str, Any],
) -> None:
    chat_feed = _extract_chat_feed(canonical)
    entity_sides = {str(key): str(track.get("team_side") or "unknown") for key, track in render_tracks.items()}
    rows: List[Dict[str, Any]] = []
    for row in kill_feed:
        if float(row.get("time_s", 0.0)) <= t + 1e-6:
            rows.append({"type": "kill", **row})
    for row in chat_feed:
        if float(row.get("time_s", 0.0)) <= t + 1e-6:
            rows.append({"type": "chat", **row})
    if not rows:
        return

    map_size = int(layout.get("map_size", 600))
    font_size = max(10, int(layout.get("font_size", 10)))
    time_font_size = max(9, font_size - 1)
    icon_size = max(14, map_size // 42)
    line_h = max(16, icon_size + 2)
    panel_rect = tuple(layout.get("feed_rect", (0, 0, 0, 0)))
    panel_x = int(panel_rect[0])
    panel_y = int(panel_rect[1])
    col_w = max(100, int(panel_rect[2]) - int(panel_rect[0]))
    panel_h_max = max(60, int(panel_rect[3]) - int(panel_rect[1]))
    available_rows = max(4, min(14, (panel_h_max - 28) // line_h))
    rows.sort(key=lambda item: (float(item.get("time_s", 0.0)), 0 if str(item.get("type")) == "kill" else 1))
    visible = rows[-available_rows:]
    panel_h = min(panel_h_max, 20 + len(visible) * line_h + 8)

    draw.rectangle([panel_x, panel_y, panel_x + col_w, panel_y + panel_h], fill=None, outline=(100, 100, 100))
    _paste_sprite(img, _text_sprite("Battle feed", font_size, (225, 225, 225), shadow=(0, 0, 0), bold=True, stroke_width=1, stroke_fill=(0, 0, 0)), panel_x + 6, panel_y + 4)

    y = panel_y + 20
    for entry in reversed(visible):
        mins, secs = divmod(int(float(entry.get("time_s", 0.0))), 60)
        stamp = f"{mins}:{secs:02d}"
        stamp_sprite = _text_sprite(stamp, time_font_size, (180, 180, 180), shadow=(0, 0, 0))
        _paste_sprite(img, stamp_sprite, panel_x + 6, y + 1)

        tx = panel_x + 42
        if str(entry.get("type")) == "chat":
            sender_raw = str(entry.get("sender") or "").strip()
            sender = _marker_name_text(sender_raw, max_len=14)
            sender_side = _player_team_side(render_tracks, sender_raw)
            sender_sprite = _text_sprite(sender or "chat", font_size + 1, _feed_name_color(sender_side), shadow=(0, 0, 0), stroke_width=1, stroke_fill=(0, 0, 0))
            _paste_sprite(img, sender_sprite, tx, y)
            msg_x = tx + (sender_sprite.width if sender_sprite is not None else 0) + 6
            message = str(entry.get("message") or "").strip()
            if len(message) > 34:
                message = message[:33] + "~"
            msg_sprite = _text_sprite(f": {message}", time_font_size, (232, 232, 232), shadow=(0, 0, 0))
            _paste_sprite(img, msg_sprite, msg_x, y + 1)
        else:
            killer = _entity_name_for_feed(canonical, str(entry.get("killer_entity_key") or "-1"))
            victim = _entity_name_for_feed(canonical, str(entry.get("victim_entity_key") or "-1"))
            killer = _marker_name_text(killer, max_len=13)
            victim = _marker_name_text(victim, max_len=13)
            weapon_label = str(entry.get("weapon_label") or "KILL")
            pill_fill, pill_text = _kill_panel_style(entry)
            killer_side = entity_sides.get(str(entry.get("killer_entity_key") or "-1"), _player_team_side(render_tracks, killer))
            victim_side = entity_sides.get(str(entry.get("victim_entity_key") or "-1"), _player_team_side(render_tracks, victim))

            killer_sprite = _text_sprite(killer, font_size + 1, _feed_name_color(killer_side), shadow=(0, 0, 0), stroke_width=1, stroke_fill=(0, 0, 0))
            _paste_sprite(img, killer_sprite, tx, y)
            icon_x = tx + (killer_sprite.width if killer_sprite is not None else 0) + 6
            icon_y = y - 1
            icon = _load_kill_icon(_kill_icon_filename(entry), icon_size)
            if icon is not None:
                img.paste(icon, (icon_x, icon_y), icon)
                victim_x = icon_x + icon_size + 6
            else:
                pill_sprite = _text_sprite(weapon_label, time_font_size, pill_text, bold=True)
                pill_w = (pill_sprite.width if pill_sprite is not None else 0) + 8
                draw.rectangle([icon_x, y, icon_x + pill_w, y + 11], fill=pill_fill, outline=(20, 20, 20))
                _paste_sprite(img, pill_sprite, icon_x + 4, y + 1)
                victim_x = icon_x + pill_w + 6
            victim_sprite = _text_sprite(victim, font_size + 1, _feed_name_color(victim_side), shadow=(0, 0, 0), stroke_width=1, stroke_fill=(0, 0, 0))
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


def _draw_hp_bar(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    width: int,
    height: int,
    ratio: float,
    color: Tuple[int, int, int],
    sunk: bool = False,
) -> None:
    ratio = max(0.0, min(1.0, ratio))
    left = cx - width // 2
    top = cy
    right = left + width
    bottom = top + height
    draw.rectangle([left, top, right, bottom], fill=(12, 12, 12), outline=(80, 80, 80))
    if ratio <= 0.0:
        return
    fill_color = (135, 135, 135) if sunk else color
    inner_left = left + 1
    inner_top = top + 1
    inner_bottom = bottom - 1
    inner_right = inner_left + max(1, int((width - 2) * ratio))
    draw.rectangle([inner_left, inner_top, inner_right, inner_bottom], fill=fill_color)


def _entity_track_for_player(render_tracks: Dict[str, Dict[str, Any]], player_name: str, entity_key: str = "") -> tuple[str, Dict[str, Any]] | tuple[str, None]:
    if entity_key and entity_key in render_tracks:
        return entity_key, render_tracks[entity_key]
    target = _feed_name_key(player_name)
    if target:
        for key, track in render_tracks.items():
            if _feed_name_key(track.get("player_name")) == target:
                return str(key), track
    return "", None


def _draw_player_status_panel(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    canonical: Dict[str, Any],
    render_tracks: Dict[str, Dict[str, Any]],
    health_timelines: Dict[str, Dict[str, Any]],
    player_status_timeline: Dict[str, Any],
    t: float,
    layout: Dict[str, Any],
) -> None:
    rect = tuple(layout.get("player_rect", (0, 0, 0, 0)))
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return

    status = _player_status_at(player_status_timeline, t)
    player_name = str(status.get("player_name") or (canonical.get("meta", {}) or {}).get("playerName") or "").strip()
    ship_entity_key, track = _entity_track_for_player(render_tracks, player_name, str(status.get("ship_entity_key") or ""))
    ship_id = _safe_int(status.get("ship_id"))
    if (ship_id is None or ship_id < 0) and isinstance(track, dict):
        ship_id = _safe_int(track.get("ship_id"))
    ship_id = ship_id if ship_id is not None else -1
    ship_type = _ship_type(ship_id)
    ship_code = _ship_class_code(ship_id)
    ship_name = _ship_name(ship_id) or "Unknown ship"
    vehicle_code = _player_vehicle_code(canonical, ship_id)
    if ship_name == "Unknown ship":
        raw_ship_name = str(_gameparams_ship_entry(ship_id).get("name") or "").strip()
        if raw_ship_name:
            ship_name = raw_ship_name.split("_", 1)[-1].replace("_", " ")
    font_size = max(10, int(layout.get("font_size", 10)))
    damage_font_size = max(font_size + 4, int(font_size * 1.5))
    title_font_size = font_size + 1
    x0, y0, x1, y1 = map(int, rect)

    draw.rectangle(rect, fill=(6, 10, 14), outline=(95, 95, 95))
    _paste_sprite(img, _text_sprite("Player ship", title_font_size, (225, 225, 225), shadow=(0, 0, 0), bold=True, stroke_width=1, stroke_fill=(0, 0, 0)), x0 + 8, y0 + 5)

    damage_text = f"{int(round(float(status.get('damage_total', 0.0) or 0.0))):,} dmg"
    damage_sprite = _text_sprite(damage_text, damage_font_size, (255, 230, 180), shadow=(0, 0, 0), bold=True, stroke_width=1, stroke_fill=(0, 0, 0))
    if damage_sprite is not None:
        _paste_sprite(img, damage_sprite, x1 - damage_sprite.width - 10, y0 + 28)

    ribbons = dict(status.get("ribbons") or {})
    badge_font = max(11, font_size + 1)
    icon_size = max(34, int(font_size * 3.2))
    ribbons_reserved_h = max(86, font_size + icon_size + 30)
    ribbons_title_y = y1 - ribbons_reserved_h
    preview_x = x0 + 10
    preview_y = y0 + 28
    preview_w = max(88, min(170, int((x1 - x0) * 0.34)))
    preview_h = max(50, ribbons_title_y - preview_y - 8)
    preview_rect = (preview_x, preview_y, preview_x + preview_w, preview_y + preview_h)
    draw.rounded_rectangle(preview_rect, radius=8, fill=(12, 16, 22), outline=(50, 60, 72))
    health = _health_state_at(health_timelines, ship_entity_key, t) if ship_entity_key else None
    player_sunk = health is not None and not bool(health.get("alive", True))
    hp_ratio = float(health.get("ratio", 1.0) or 1.0) if health is not None else 1.0
    alive_icon = _load_ship_alive_icon(vehicle_code, preview_w - 12, preview_h - 12)
    dead_icon = _load_ship_dead_icon(vehicle_code, preview_w - 12, preview_h - 12)
    preview = _compose_ship_status_icon(alive_icon, dead_icon, preview_w - 12, preview_h - 12, hp_ratio, player_sunk)
    if preview is None and not player_sunk:
        preview = _load_ship_preview(ship_id, vehicle_code, preview_w - 12, preview_h - 12)
    if preview is not None:
        px = preview_x + (preview_w - preview.width) // 2
        py = preview_y + (preview_h - preview.height) // 2
        img.paste(preview, (px, py), preview)
    else:
        fallback_color = (160, 160, 160) if player_sunk else (224, 232, 240)
        fallback_icon = _wg_tinted_icon(ship_type, fallback_color, max(14, min(preview_w, preview_h) // 2))
        if fallback_icon is not None:
            px = preview_x + (preview_w - fallback_icon.width) // 2
            py = preview_y + (preview_h - fallback_icon.height) // 2
            img.paste(fallback_icon, (px, py), fallback_icon)
        else:
            local = ImageDraw.Draw(img)
            _draw_ship_icon(local, preview_x + preview_w // 2, preview_y + preview_h // 2, ship_code, fallback_color, (220, 220, 220), size=max(10, min(preview_w, preview_h) // 4))

    text_x = preview_rect[2] + 12
    info_y = y0 + 28
    line_gap = max(16, font_size + 4)
    _paste_sprite(img, _text_sprite(player_name or "Player", title_font_size + 1, COLOR_FRIENDLY, shadow=(0, 0, 0), stroke_width=1, stroke_fill=(0, 0, 0)), text_x, info_y)
    _paste_sprite(img, _text_sprite(ship_name, font_size + 1, (235, 235, 235), shadow=(0, 0, 0), stroke_width=1, stroke_fill=(0, 0, 0)), text_x, info_y + line_gap)
    details = ship_code if ship_name != "Unknown ship" else (ship_code if not vehicle_code else f"{ship_code}  {vehicle_code}")
    _paste_sprite(img, _text_sprite(details, font_size, (180, 180, 180), shadow=(0, 0, 0)), text_x, info_y + line_gap * 2)

    if health is not None:
        hp_ratio = max(0.0, min(1.0, float(health.get("ratio", 0.0) or 0.0)))
        hp_pct = int(round(hp_ratio * 100.0))
        hp_text = f"HP {int(health['hp']):,} / {int(health['max_hp']):,}  {hp_pct}%"
        hp_color = COLOR_FRIENDLY if bool(health.get("alive", True)) else (165, 165, 165)
        _paste_sprite(img, _text_sprite(hp_text, font_size, hp_color, shadow=(0, 0, 0), stroke_width=1, stroke_fill=(0, 0, 0)), text_x, info_y + line_gap * 3)

    _paste_sprite(img, _text_sprite("Ribbons", font_size, (205, 205, 205), shadow=(0, 0, 0), bold=True), x0 + 10, ribbons_title_y)
    badge_x = x0 + 10
    badge_y = ribbons_title_y + font_size + 5
    badge_max_x = x1 - 10
    supported_ribbons = _gameparams_supported_ribbon_ids()
    items = sorted(
        (
            (ribbon_id, count)
            for ribbon_id, count in ribbons.items()
            if (_safe_int(ribbon_id) is not None and int(ribbon_id) in supported_ribbons)
        ),
        key=lambda item: (-int(item[1]), int(item[0])),
    )
    hidden = 0
    for ribbon_id, count in items:
        rid = _safe_int(ribbon_id)
        icon = _load_ribbon_icon(rid or -1, icon_size) if rid is not None else None
        count_sprite = _text_sprite(f"x{int(count)}", badge_font, (242, 242, 255), shadow=(0, 0, 0), bold=True)
        has_icon = icon is not None and count_sprite is not None
        if has_icon:
            badge_w = icon.width + 8 + count_sprite.width + 14
            badge_h = max(icon.height, count_sprite.height) + 6
        else:
            fallback = _text_sprite(f"R{int(ribbon_id)} x{int(count)}", badge_font, (242, 242, 255), shadow=None, bold=True)
            if fallback is None:
                continue
            icon = fallback
            count_sprite = None
            badge_w = fallback.width + 14
            badge_h = fallback.height + 6
        if badge_x + badge_w > badge_max_x:
            badge_x = x0 + 10
            badge_y += badge_h + 4
        if badge_y + badge_h > y1 - 6:
            hidden += 1
            continue
        draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=6, fill=(16, 24, 34), outline=(75, 90, 108))
        if has_icon:
            _paste_sprite(img, icon, badge_x + 6, badge_y + (badge_h - icon.height) // 2)
            _paste_sprite(img, count_sprite, badge_x + 6 + icon.width + 8, badge_y + (badge_h - count_sprite.height) // 2)
        else:
            _paste_sprite(img, icon, badge_x + 7, badge_y + 2)
        badge_x += badge_w + 6
    if hidden > 0:
        more_sprite = _text_sprite(f"+{hidden} more", badge_font, (180, 180, 180), shadow=(0, 0, 0))
        _paste_sprite(img, more_sprite, x1 - (more_sprite.width if more_sprite is not None else 0) - 10, y1 - (more_sprite.height if more_sprite is not None else 0) - 6)


def _draw_lineup_panel(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    layout: Dict[str, Any],
    current_t: float | None = None,
    death_times: Dict[str, float] | None = None,
) -> None:
    friendly = list(layout.get("friendly_items", []))
    enemy = list(layout.get("enemy_items", []))
    line_h = int(layout.get("line_h", 12))
    header_h = int(layout.get("header_h", 20))
    font_size = int(layout.get("font_size", 10))
    friendly_rect = tuple(layout.get("friendly_rect", (0, 0, 0, 0)))
    enemy_rect = tuple(layout.get("enemy_rect", (0, 0, 0, 0)))

    draw.rectangle(friendly_rect, fill=None, outline=(90, 120, 90))
    draw.rectangle(enemy_rect, fill=None, outline=(130, 70, 70))
    _paste_sprite(img, _text_sprite("Friendly lineup", font_size, COLOR_FRIENDLY, shadow=(0, 0, 0), bold=True, stroke_width=1, stroke_fill=(0, 0, 0)), int(friendly_rect[0]) + 6, int(friendly_rect[1]) + 4)
    _paste_sprite(img, _text_sprite("Enemy lineup", font_size, COLOR_ENEMY, shadow=(0, 0, 0), bold=True, stroke_width=1, stroke_fill=(0, 0, 0)), int(enemy_rect[0]) + 6, int(enemy_rect[1]) + 4)

    def _line_text(item: Dict[str, Any], rect: Tuple[Any, Any, Any, Any]) -> str:
        num = _lineup_number_text(item.get("team_number_local"))
        ship_code = _ship_class_code(item.get("ship_id"))
        rect_w = max(120, int(rect[2]) - int(rect[0]))
        max_len = max(8, min(20, (rect_w - 44) // max(6, font_size)))
        name = _marker_name_text(item.get("player_name"), max_len=max_len)
        return f"{num:>2} {ship_code} {name}"

    def _is_sunk(item: Dict[str, Any]) -> bool:
        if current_t is None or death_times is None:
            return False
        entity_key = str(item.get("entity_id", "") or "")
        if not entity_key:
            return False
        death_t = death_times.get(entity_key)
        return death_t is not None and float(current_t) >= float(death_t)

    def _draw_row(item: Dict[str, Any], rect: Tuple[Any, Any, Any, Any], x: int, y: int, alive_fill: Tuple[int, int, int]) -> None:
        sunk = _is_sunk(item)
        fill = alive_fill if not sunk else (132, 132, 132)
        row_text = _line_text(item, rect)
        row_sprite = _text_sprite(row_text, font_size + 1, fill, shadow=(0, 0, 0), stroke_width=1, stroke_fill=(0, 0, 0))
        _paste_sprite(img, row_sprite, x, y)

    rows = max(len(friendly), len(enemy), 12)
    for i in range(rows):
        y_f = int(friendly_rect[1]) + header_h + i * line_h
        y_e = int(enemy_rect[1]) + header_h + i * line_h
        if i < len(friendly):
            _draw_row(friendly[i], friendly_rect, int(friendly_rect[0]) + 6, y_f, (225, 240, 225))
        if i < len(enemy):
            _draw_row(enemy[i], enemy_rect, int(enemy_rect[0]) + 6, y_e, (240, 225, 225))


def _build_frame_base(
    canonical: Dict[str, Any],
    layout: Dict[str, Any],
    margin: int,
    show_grid: bool,
    header_font_size: int,
    bg_color: Tuple[int, int, int] = COLOR_BG,
) -> Image.Image:
    map_size = int(layout.get("map_size", 600))
    canvas_w = int(layout.get("width", map_size))
    canvas_h = int(layout.get("height", map_size))
    sidebar_x = int(layout.get("sidebar_x", map_size))
    img = Image.new("RGB", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([sidebar_x, 0, canvas_w, canvas_h], fill=(0, 0, 0))
    draw.line([(sidebar_x, 0), (sidebar_x, canvas_h)], fill=(40, 40, 40), width=2)
    img = _apply_map_background(img, canonical, margin, map_size)
    draw = ImageDraw.Draw(img)

    if show_grid:
        grid_steps = 9 if map_size >= 800 else 7
        grid_divisor = max(1, grid_steps - 1)
        for i in range(grid_steps):
            x = margin + i * (map_size - 2 * margin) // grid_divisor
            draw.line([(x, margin), (x, map_size - margin)], fill=(35, 55, 85), width=1)
            draw.line([(margin, x), (map_size - margin, x)], fill=(35, 55, 85), width=1)
        if map_size >= 800:
            draw.rectangle([margin, margin, map_size - margin, map_size - margin], outline=(60, 90, 130), width=2)

    friendly_total = len(layout.get("friendly_items", []))
    enemy_total = len(layout.get("enemy_items", []))
    count_sprite = _text_sprite(f"friendly {friendly_total} | enemy {enemy_total}", header_font_size, (220, 220, 220))
    title_sprite = _text_sprite(_map_title(canonical), header_font_size, (220, 220, 220))
    _paste_sprite(img, count_sprite, 10, 10)
    _paste_sprite(img, title_sprite, 10, 10 + max(16, header_font_size + 5))
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


def _clamp_track_to_time(points: List[Dict[str, Any]], t_limit: float) -> List[Dict[str, Any]]:
    if not points:
        return []
    out = [p for p in points if float(p.get("t", 0.0)) <= t_limit + 1e-6]
    if out:
        return out
    return [points[0]]


def render_static(canonical: Dict[str, Any], canvas_size: int = 1024, show_labels: bool = True, show_grid: bool = True, bg_color: Tuple[int, int, int] = COLOR_BG) -> Image.Image:
    font = _load_font(12)
    half = _world_half(canonical)
    margin = 40
    death_times = _find_death_times(canonical)
    render_tracks = _normalize_render_tracks(canonical)
    health_timelines = _extract_health_timelines(canonical)
    player_status_timeline = _extract_player_status_timeline(canonical)
    layout = _render_layout(render_tracks, canvas_size)
    img = _build_frame_base(canonical, layout, margin, show_grid, 12, bg_color=bg_color)
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
        pts = list(track.get("points", []) or [])
        if not pts:
            continue
        ship_type = _ship_type(track.get("ship_id"))
        ship_class = _ship_class_code(track.get("ship_id"))
        death_t = death_times.get(str(entity_key))
        sunk = death_t is not None and battle_end >= death_t
        render_pts = _clamp_track_to_time(pts, float(death_t)) if sunk and death_t is not None else pts
        last_t = float(render_pts[-1].get("t", 0.0))
        heading_deg = _stable_heading_deg(render_pts, previous=None)
        spotted = (battle_end - last_t) <= spot_timeout and not bool(track.get("always_unspotted", False))
        ever_spotted = (not bool(track.get("always_unspotted", False))) and bool(render_pts)
        color = _status_color(_color_side(track), spotted=spotted, sunk=sunk, ever_spotted=ever_spotted)
        poly = [_to_px(float(p.get("x", 0.0)), float(p.get("z", 0.0)), half, canvas_size, margin) for p in render_pts]
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
        health = _health_state_at(health_timelines, entity_key, float(death_t) if sunk and death_t is not None else battle_end)
        if health is not None:
            _draw_hp_bar(draw, ex, ey + 13, 28, 5, float(health.get("ratio", 0.0)), color, sunk=sunk or (not bool(health.get("alive", True))))

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
    _draw_player_status_panel(img, draw, canonical, render_tracks, health_timelines, player_status_timeline, battle_end, layout)
    _draw_kill_feed_panel(img, draw, canonical, render_tracks, kill_feed, battle_end, layout)
    _draw_lineup_panel(img, draw, layout, current_t=battle_end, death_times=death_times)
    return img


def estimate_animation_frame_count(canonical: Dict[str, Any], speed: float = 3.0) -> int:
    step = max(0.05, float(speed))
    max_clock = float(canonical.get("stats", {}).get("battle_end_s", 0.0))
    if max_clock <= 0:
        tracks = canonical.get("tracks", {}) or {}
        max_clock = max(
            (float(p.get("t", 0.0)) for t in tracks.values() for p in (t.get("points", []) or [])),
            default=0.0,
        )
    return max(1, int(math.floor(max_clock / step)) + 2)


def iter_animation_frames(canonical: Dict[str, Any], canvas_size: int = 600, speed: float = 3.0, show_grid: bool = True):
    half = _world_half(canonical)
    margin = 40
    step = max(0.05, float(speed))
    death_times = _find_death_times(canonical)
    render_tracks = _normalize_render_tracks(canonical)
    health_timelines = _extract_health_timelines(canonical)
    player_status_timeline = _extract_player_status_timeline(canonical)
    layout = _render_layout(render_tracks, canvas_size)
    prepared_tracks = _prepare_track_render_data(render_tracks, half, canvas_size, margin)
    max_clock = float(canonical.get("stats", {}).get("battle_end_s", 0.0))
    if max_clock <= 0:
        max_clock = max((float(p.get("t", 0.0)) for t in render_tracks.values() for p in t.get("points", [])), default=0.0)
    spot_timeout = max(6.0, step * 1.5)
    capture_timeline = _capture_timeline(canonical)
    artillery_traces = _extract_artillery_traces(canonical)
    torpedo_tracks = _extract_torpedo_tracks(canonical)
    kill_feed = _extract_kill_feed(canonical)
    heading_memory: Dict[str, float] = {}
    ever_spotted_memory: Dict[str, bool] = {}
    ui_font_size = max(11, canvas_size // 56)
    marker_size = max(6, canvas_size // 96)
    clock_x = canvas_size - max(80, ui_font_size * 7)
    base_frame = _build_frame_base(canonical, layout, margin, show_grid, ui_font_size)

    t = 0.0
    while t <= max_clock + step:
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
            death_t = death_times.get(str(entity_key))
            sunk = death_t is not None and t >= death_t
            if sunk and death_t is not None:
                idx = max(0, bisect_right(times, float(death_t)) - 1)
                synthetic_start = False
            points = all_points[max(0, idx - 9) : idx + 1]
            last_t = times[idx]
            prev_heading = heading_memory.get(str(entity_key))
            if sunk and prev_heading is not None:
                heading_deg = prev_heading
            else:
                heading_deg = _stable_heading_deg(points, previous=prev_heading, max_step_deg=32.0)
            heading_memory[str(entity_key)] = heading_deg
            spotted = (t - last_t) <= spot_timeout and not synthetic_start and not bool(track.get("always_unspotted", False))
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
                size=marker_size,
                sunk=sunk,
            )
            health = _health_state_at(health_timelines, entity_key, float(death_t) if sunk and death_t is not None else t)
            if health is not None:
                _draw_hp_bar(
                    draw,
                    cx,
                    cy + marker_size + 5,
                    max(20, marker_size * 4),
                    max(4, marker_size // 2),
                    float(health.get("ratio", 0.0)),
                    color,
                    sunk=sunk or (not bool(health.get("alive", True))),
                )

        mins, secs = divmod(int(t), 60)
        _paste_sprite(img, _text_sprite(f"{mins}:{secs:02d}", ui_font_size, (220, 220, 220)), clock_x, 10)
        _draw_score_overlay(img, canonical, capture_snapshot, canvas_size)
        _draw_player_status_panel(img, draw, canonical, render_tracks, health_timelines, player_status_timeline, t, layout)
        _draw_kill_feed_panel(img, draw, canonical, render_tracks, kill_feed, t, layout)
        _draw_lineup_panel(img, draw, layout, current_t=t, death_times=death_times)
        yield img
        t += step


def render_gif_frames(canonical: Dict[str, Any], canvas_size: int = 600, speed: float = 3.0, show_grid: bool = True) -> List[Image.Image]:
    return list(iter_animation_frames(canonical, canvas_size=canvas_size, speed=speed, show_grid=show_grid))
