import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import urlopen


# Map ID to human-readable names conversion
MAP_NAMES = {
    # Standard maps
    0: "Big Race",
    1: "New Dawn", 
    2: "Fault Line",
    3: "Two Brothers",
    4: "Islands of Ice",
    5: "Hotspot",
    6: "Shatter",
    7: "North",
    8: "Land of Fire",
    9: "Neighbours",
    10: "Sea of Fortune",
    11: "Ocean",
    12: "Trap",
    13: "Two Brothers",  # 20_NE_two_brothers
    14: "Warrior's Path",
    15: "Mountain Range",
    16: "Atlantic",
    17: "Estuary",
    18: "Crucible",
    19: "Sleeping Giant",
    20: "Korea",
    21: "Haven",
    22: "Tears of the Desert",
    23: "Mountain Range",
    24: "Crucible",
    25: "Sleeping Giant",
    26: "Shatter",
    27: "Northern Lights",
    28: "Loop",
    29: "Helena",
    30: "Crash Zone Alpha",
    31: "Crash Zone Bravo",
    32: "Crash Zone Charlie",
    33: "Crash Zone Delta",
    34: "Faroe Islands",
    35: "Fiji Islands",
    36: "Greece",
    37: "Iceland",
    38: "Japan",
    39: "Malta",
    40: "Monaco",
    41: "Naples",
    42: "Northern Lights",
    43: "Oslo",
    44: "Panama",
    45: "Philippines",
    46: "Portugal",
    47: "Scotland",
    48: "Spain",
    49: "Sweden",
    50: "Switzerland",
    51: "Turkey",
    52: "UK",
    53: "USA",
    54: "USSR",
    55: "Venezuela",
    56: "Yugoslavia",
}

# Map display name to clean name conversion
MAP_DISPLAY_NAMES = {
    "20_NE_two_brothers": "Two Brothers",
    "10_NA_big_race": "Big Race",
    "11_NA_new_dawn": "New Dawn",
    "12_NA_fault_line": "Fault Line",
    "13_NE_two_brothers": "Two Brothers",
    "14_NA_islands_of_ice": "Islands of Ice",
    "15_NA_hotspot": "Hotspot",
    "16_NA_shatter": "Shatter",
    "17_NA_north": "North",
    "18_NA_land_of_fire": "Land of Fire",
    "19_NA_neighbours": "Neighbours",
    "20_NA_sea_of_fortune": "Sea of Fortune",
    "21_NA_ocean": "Ocean",
    "22_NA_trap": "Trap",
    "23_NA_warriors_path": "Warrior's Path",
    "24_NA_mountain_range": "Mountain Range",
    "25_NA_atlantic": "Atlantic",
    "26_NA_estuary": "Estuary",
    "27_NA_crucible": "Crucible",
    "28_NA_sleeping_giant": "Sleeping Giant",
    "29_NA_korea": "Korea",
    "30_NA_haven": "Haven",
    "31_NA_tears_of_the_desert": "Tears of the Desert",
    "32_NA_mountain_range": "Mountain Range",
    "33_NA_crucible": "Crucible",
    "34_NA_sleeping_giant": "Sleeping Giant",
    "35_NA_shatter": "Shatter",
    "36_NA_northern_lights": "Northern Lights",
    "37_NA_loop": "Loop",
    "38_NA_helena": "Helena",
    "39_NA_crash_zone_alpha": "Crash Zone Alpha",
    "40_NA_crash_zone_bravo": "Crash Zone Bravo",
    "41_NA_crash_zone_charlie": "Crash Zone Charlie",
    "42_NA_crash_zone_delta": "Crash Zone Delta",
    "43_NA_faroe_islands": "Faroe Islands",
    "44_NA_fiji_islands": "Fiji Islands",
    "45_NA_greece": "Greece",
    "46_NA_iceland": "Iceland",
    "47_NA_japan": "Japan",
    "48_NA_malta": "Malta",
    "49_NA_monaco": "Monaco",
    "50_NA_naples": "Naples",
    "51_NA_northern_lights": "Northern Lights",
    "52_NA_oslo": "Oslo",
    "53_NA_panama": "Panama",
    "54_NA_philippines": "Philippines",
    "55_NA_portugal": "Portugal",
    "56_NA_scotland": "Scotland",
    "57_NA_spain": "Spain",
    "58_NA_sweden": "Sweden",
    "59_NA_switzerland": "Switzerland",
    "60_NA_turkey": "Turkey",
    "61_NA_uk": "UK",
    "62_Na_usa": "USA",
    "63_NA_ussr": "USSR",
    "64_NA_venezuela": "Venezuela",
    "65_NA_yugoslavia": "Yugoslavia",
}

def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _battlearena_cache_path() -> Path:
    cache_dir = _repo_root() / "content"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "wg_battlearenas_cache.json"


def _read_api_credentials() -> tuple[str, str]:
    app_id = os.getenv("WWS_APP_ID", "").strip()
    realm = os.getenv("WWS_REALM", "").strip().lower() or "eu"

    if not app_id:
        cfg_path = _repo_root() / "wws_api_config.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            app_id = str(cfg.get("app_id", "")).strip()
            realm = str(cfg.get("realm", realm)).strip().lower() or "eu"
        except Exception:
            pass
    return app_id, realm


def _base_url_for_realm(realm: str) -> str:
    realm_urls = {
        "na": "https://api.worldofwarships.com/wows/",
        "eu": "https://api.worldofwarships.eu/wows/",
        "asia": "https://api.worldofwarships.asia/wows/",
        "ru": "https://api.worldofwarships.ru/wows/",
    }
    return realm_urls.get(realm, realm_urls["eu"])


def _fetch_battlearenas_data() -> Dict[str, Dict[str, Any]]:
    app_id, realm = _read_api_credentials()
    if not app_id:
        return {}

    params = urlencode({"application_id": app_id})
    url = f"{_base_url_for_realm(realm)}encyclopedia/battlearenas/?{params}"
    try:
        with urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        data = payload.get("data", {})
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


@lru_cache(maxsize=1)
def _load_battlearenas_data() -> Dict[str, Dict[str, Any]]:
    cache_path = _battlearena_cache_path()

    # Try cache first.
    try:
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached:
                return cached
    except Exception:
        pass

    # Fetch from API.
    data = _fetch_battlearenas_data()
    if data:
        try:
            cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass
    return data


def get_battlearena_entry(map_id: Any) -> Optional[Dict[str, Any]]:
    try:
        key = str(int(map_id))
    except (TypeError, ValueError):
        return None

    data = _load_battlearenas_data()
    entry = data.get(key)
    if isinstance(entry, dict):
        return entry
    return None


def get_map_name(map_display_name=None, map_id=None):
    """Get human-readable map name from either display name or ID"""
    # Prefer official WG battlearena mapping when map_id is available.
    if map_id is not None:
        entry = get_battlearena_entry(map_id)
        if entry and entry.get("name"):
            return str(entry["name"])

    if map_display_name:
        return MAP_DISPLAY_NAMES.get(map_display_name, map_display_name.replace("_", " ").title())
    elif map_id:
        return MAP_NAMES.get(map_id, f"Unknown Map ({map_id})")
    return "Unknown Map"

# Game mode conversion
GAME_MODES = {
    0: "Unknown",
    1: "Co-op",
    2: "Random", 
    3: "Ranked",
    4: "Clan",
    5: "Scenario",
    6: "Operations",
    7: "Random",  # Often used for Random battles
    8: "Training",
    9: "Brawl",
    10: "Asymmetric",
    11: "Grand Battle",
}

def get_game_mode(mode_id):
    """Get human-readable game mode"""
    return GAME_MODES.get(mode_id, f"Unknown Mode ({mode_id})")
