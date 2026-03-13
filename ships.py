
import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp


async def fetch_all_ships(
    fields: str = "name",
    save_path: Optional[Union[Path, str]] = "files/ships.json",
    application_id: Optional[str] = None,
    base_url: Optional[str] = None,
):
    """Fetch all ships from the WoWS API and optionally save to JSON file."""
    if application_id is None or base_url is None:
        app_id, realm = _load_api_config()
        application_id = application_id or app_id
        base_url = base_url or REALM_BASE_URLS.get(realm.lower(), REALM_BASE_URLS[DEFAULT_REALM])
    fields_param = fields or "name"
    all_ships = {}
    
    async with aiohttp.ClientSession() as session:
        # First request to get total pages
        async with session.get(
            f"{base_url}?application_id={application_id}&fields={fields_param}&limit=100&page_no=1"
        ) as response:
            if response.status != 200:
                raise Exception(f"API request failed with status {response.status}")
            
            data = await response.json()
            if data.get("status") != "ok":
                raise Exception(f"API returned error: {data}")
            
            meta = data.get("meta", {})
            total_pages = meta.get("page_total", 1)
            
            # Add ships from first page
            if "data" in data:
                all_ships.update(data["data"])
        
        # Fetch remaining pages
        for page in range(2, total_pages + 1):
            async with session.get(
                f"{base_url}?application_id={application_id}&fields={fields_param}&limit=100&page_no={page}"
            ) as response:
                if response.status != 200:
                    print(f"Warning: Failed to fetch page {page}")
                    continue
                
                data = await response.json()
                if data.get("status") == "ok" and "data" in data:
                    all_ships.update(data["data"])
    
    # Save to JSON file
    if save_path:
        file_path = Path(save_path)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(all_ships, f, indent=2, ensure_ascii=False)
    
    return all_ships


def search_ships_by_name(search_term: str, limit: int = 10):
    """Search for ships by name in the ships.json file."""
    try:
        with open("files/ships.json", "r", encoding="utf-8") as f:
            ships_data = json.load(f)
    except FileNotFoundError:
        return []
    
    search_term_lower = search_term.lower()
    matches = []
    
    for ship_id, ship_info in ships_data.items():
        ship_name = ship_info.get("name", "")
        # Skip ships with names in brackets like [Yamato]
        if ship_name.startswith("[") and ship_name.endswith("]"):
            continue
        
        ship_name_lower = ship_name.lower()
        if search_term_lower in ship_name_lower:
            matches.append((ship_id, ship_name))
            if len(matches) >= limit:
                break
    
    return matches


async def fetch_ship_details(
    ship_id: str,
    *,
    application_id: Optional[str] = None,
    base_url: Optional[str] = None,
):
    """Fetch detailed ship information from the API."""
    if application_id is None or base_url is None:
        app_id, realm = _load_api_config()
        application_id = application_id or app_id
        base_url = base_url or REALM_BASE_URLS.get(realm.lower(), REALM_BASE_URLS[DEFAULT_REALM])

    async with aiohttp.ClientSession() as session:
        return await _fetch_ship_details(
            session,
            ship_id,
            application_id=application_id,
            base_url=base_url,
        )


def format_price(price_gold: int, price_credit: int) -> str:
    """Format ship price information."""
    price_parts = []
    if price_gold and price_gold > 0:
        price_parts.append(f"{price_gold:,} 🪙 Gold")
    if price_credit and price_credit > 0:
        price_parts.append(f"{price_credit:,} 💰 Credits")
    return "\n".join(price_parts) if price_parts else "N/A"


def format_nation(nation: str) -> str:
    """Format nation name to be more readable."""
    nation_map = {
        "usa": "🇺🇸 USA",
        "japan": "🇯🇵 Japan",
        "ussr": "🇷🇺 USSR",
        "germany": "🇩🇪 Germany",
        "uk": "🇬🇧 UK",
        "france": "🇫🇷 France",
        "italy": "🇮🇹 Italy",
        "pan_asia": "🌏 Pan-Asia",
        "pan_america": "🌎 Pan-America",
        "europe": "🇪🇺 Europe",
        "netherlands": "🇳🇱 Netherlands",
        "spain": "🇪🇸 Spain"
    }
    return nation_map.get(nation.lower(), nation.title())


def extract_caliber_from_name(name: str) -> str:
    """Extract caliber from gun name (e.g., '180 mm/65 SM-45' -> '180 mm')."""
    if not name:
        return "N/A"
    # Match pattern like "180 mm" or "406 mm" at the start
    match = re.search(r'(\d+)\s*mm', name)
    if match:
        return f"{match.group(1)} mm"
    return "N/A"


def get_main_guns_info(profile: dict) -> dict:
    """Extract main gun caliber and count from profile.

    This returns:
      - 'caliber': extracted from the first artillery slot name
      - 'count': a human-friendly count that prefers a "turrets x barrels" format
        (e.g., "3x3") when turret and barrel counts are available. If multiple
        slot types exist it will aggregate or list them sensibly. Falls back to the
        previous behavior of summing 'guns' or using 'hull.artillery_barrels'.
    """
    artillery = profile.get("artillery", {})
    hull = profile.get("hull", {})

    info = {"caliber": "N/A", "count": "N/A"}

    if artillery:
        # Try to get caliber from artillery slots name
        slots = artillery.get("slots", {}) or {}
        if slots:
            # Get the first slot's name to extract caliber
            first_slot = list(slots.values())[0]
            if first_slot and "name" in first_slot:
                info["caliber"] = extract_caliber_from_name(first_slot["name"])

            # Attempt to determine a turret x barrels format
            pairs = []  # list of (guns, barrels) tuples
            total_guns_only = 0
            for slot in slots.values():
                guns = slot.get("guns")
                barrels = slot.get("barrels")

                # Some API representations put barrels under 'barrels' and turret count under 'guns'
                # If only one is present try to make a best-effort assumption.
                if guns is None and barrels is not None:
                    # assume 1 turret of `barrels` if guns missing
                    guns = 1
                if guns is not None and barrels is not None:
                    try:
                        g = int(guns)
                        b = int(barrels)
                        pairs.append((g, b))
                    except Exception:
                        # Non-integer values - skip
                        pass
                elif guns is not None:
                    try:
                        total_guns_only += int(guns)
                    except Exception:
                        pass
                elif barrels is not None:
                    # if we only know barrels, assume a single turret of that barrel count
                    try:
                        pairs.append((1, int(barrels)))
                    except Exception:
                        pass

            # If we have at least one (guns, barrels) pair, format them
            if pairs:
                # If all pairs have same barrels, aggregate total turret count and show as NxM
                barrels_set = set(b for _, b in pairs)
                if len(barrels_set) == 1:
                    barrels_common = next(iter(barrels_set))
                    total_turrets = sum(g for g, _ in pairs)
                    info["count"] = f"{total_turrets}x{barrels_common}"
                else:
                    # Otherwise list each pair as 'GxB' joined by commas
                    info["count"] = ", ".join(f"{g}x{b}" for g, b in pairs)
            elif total_guns_only > 0:
                # Fallback to previous behavior: sum of guns
                info["count"] = total_guns_only

        # Fallback: try to get from hull
        if info["count"] == "N/A" and hull:
            artillery_barrels = hull.get("artillery_barrels")
            if artillery_barrels:
                info["count"] = artillery_barrels

    return info


def get_torpedo_info(profile: dict) -> dict:
    """Extract torpedo info from the profile (caliber, count, range, damage, reload, visibility)."""
    torps = profile.get("torpedoes", {})
    info = {"caliber": "N/A", "count": "N/A", "range": "N/A", "max_damage": None, "reload_time": None, "visibility_dist": None}

    if not torps:
        return info

    slots = torps.get("slots", {}) or {}
    calibers = {}
    names = []
    total_barrels = 0
    total_launchers = 0

    for slot in slots.values():
        # caliber may be in slot['caliber'] or in name
        cal = None
        if slot.get("caliber"):
            cal = f"{slot.get('caliber')} mm"
        elif slot.get("name"):
            ext = extract_caliber_from_name(slot.get("name"))
            if ext != "N/A":
                cal = ext

        guns = slot.get("guns") or 0
        barrels = slot.get("barrels") or 0
        if cal:
            calibers[cal] = calibers.get(cal, 0) + (guns or 1) * (barrels or 1)
        total_launchers += (guns or 0)
        total_barrels += (barrels or 0) * (guns or 1)
        if slot.get("name"):
            names.append(slot.get("name"))

    if calibers:
        # produce a readable string similar to secondaries/main guns
        parts = []
        for cal, tot in calibers.items():
            parts.append(f"{cal} ({tot} total barrels)")
        info["caliber"] = ", ".join(parts)

    # Count representation: try to find 'launchers x barrels' if consistent
    if slots:
        # If each slot has guns and barrels and barrels are consistent, show NxM
        pairs = []
        for slot in slots.values():
            g = slot.get("guns")
            b = slot.get("barrels")
            if g is not None and b is not None:
                pairs.append((int(g), int(b)))
        if pairs:
            barrels_set = set(b for _, b in pairs)
            if len(barrels_set) == 1:
                total_launchers = sum(g for g, _ in pairs)
                info["count"] = f"{total_launchers}x{next(iter(barrels_set))}"
            else:
                info["count"] = ", ".join(f"{g}x{b}" for g, b in pairs)
        else:
            # fallback to totals
            if total_launchers > 0 and total_barrels > 0:
                info["count"] = f"{total_launchers} launchers, {total_barrels} barrels"
            elif total_barrels > 0:
                info["count"] = f"{total_barrels} barrels"

    # range, damage, reload, visibility
    if torps.get("distance"):
        info["range"] = f"{torps.get('distance')} km"
    if torps.get("max_damage"):
        info["max_damage"] = torps.get("max_damage")
    if torps.get("reload_time"):
        info["reload_time"] = torps.get("reload_time")
    # visibility distance (how far torpedoes can be spotted)
    if torps.get("visibility_dist") is not None:
        try:
            vd = float(torps.get("visibility_dist"))
            info["visibility_dist"] = vd
        except Exception:
            info["visibility_dist"] = torps.get("visibility_dist")

    if names:
        info["name"] = ", ".join(names)

    return info


def get_secondary_guns_info(profile: dict) -> dict:
    """Extract secondary gun caliber, reload, and range from profile.

    This function now supports an arbitrary number of `atbas` and `anti_aircraft` slots
    by iterating them and aggregating calibers, reload rates, and damage where available.
    It also fixes missing variable references by explicitly reading `hull` and
    `anti_aircraft` from the profile.
    """
    atbas = profile.get("atbas") if isinstance(profile, dict) else {}
    atbas = atbas if isinstance(atbas, dict) else {}
    hull = profile.get("hull") if isinstance(profile, dict) else {}
    hull = hull if isinstance(hull, dict) else {}
    anti_aircraft = profile.get("anti_aircraft") if isinstance(profile, dict) else {}
    anti_aircraft = anti_aircraft if isinstance(anti_aircraft, dict) else {}

    info = {"caliber": "N/A", "reload": "N/A", "range": "N/A", "range_min": "N/A", "range_max": "N/A"}

    # Aggregate data from atbas slots (supports any number of slots)
    slots = atbas.get("slots", {}) or {}
    calibers = {}
    reloads = set()
    damages = set()
    names = []

    for slot in slots.values():
        name = slot.get("name")
        # Try direct caliber field, otherwise extract from name
        caliber_val = None
        if slot.get("caliber"):
            caliber_val = f"{slot.get('caliber')} mm"
        elif name:
            extracted = extract_caliber_from_name(name)
            if extracted != "N/A":
                caliber_val = extracted

        if caliber_val:
            # Some slots include `guns` (count); otherwise count presence as 1
            count = slot.get("guns") or 1
            calibers[caliber_val] = calibers.get(caliber_val, 0) + count

        # Reload: prefer explicit `gun_rate` (rounds/min) converted to seconds per shot, else use `shot_delay` (seconds)
        gun_rate = slot.get("gun_rate")
        shot_delay = slot.get("shot_delay")
        reload_secs = None
        if gun_rate:
            try:
                gr = float(gun_rate)
                if gr > 0:
                    reload_secs = 60.0 / gr
            except Exception:
                pass
        elif shot_delay:
            try:
                sd = float(shot_delay)
                if sd > 0:
                    reload_secs = sd
            except Exception:
                pass

        if reload_secs is not None:
            # store seconds as a float; formatting happens later
            reloads.add(round(reload_secs, 2))

        if slot.get("damage") is not None:
            damages.add(slot.get("damage"))
        if name:
            names.append(name)

    # Also check anti_aircraft slots for any non-AA caliber entries (distance > 0)
    aa_slots = anti_aircraft.get("slots", {}) or {}
    for slot in aa_slots.values():
        distance_val = slot.get("distance", -1)
        if distance_val and distance_val > 0:
            caliber = slot.get("caliber")
            if caliber:
                cal_str = f"{caliber} mm"
                count = slot.get("guns") or 1
                calibers[cal_str] = calibers.get(cal_str, 0) + count
            # if no explicit caliber, try extracting from name
            elif slot.get("name"):
                ext = extract_caliber_from_name(slot.get("name"))
                if ext != "N/A":
                    calibers[ext] = calibers.get(ext, 0) + (slot.get("guns") or 1)

    # Remove any calibers that are actually torpedo calibers (avoid misclassification)
    torp_calibers = set()
    torps = profile.get("torpedoes", {}) or {}
    for slot in torps.get("slots", {}).values() if isinstance(torps.get("slots", {}), dict) else []:
        if slot.get("caliber"):
            torp_calibers.add(f"{slot.get('caliber')} mm")
        elif slot.get("name"):
            ext = extract_caliber_from_name(slot.get("name"))
            if ext != "N/A":
                torp_calibers.add(ext)

    # Build human-readable caliber string (e.g., '127 mm (x2), 25 mm')
    if calibers:
        # filter out torpedo calibers
        for tc in torp_calibers:
            if tc in calibers:
                del calibers[tc]

        parts = []
        for cal, cnt in calibers.items():
            if cnt and cnt > 1:
                parts.append(f"{cal} (x{cnt})")
            else:
                parts.append(f"{cal}")
        if parts:
            info["caliber"] = ", ".join(parts)

    # Consolidate reload information (format seconds per shot as 'Xs')
    if reloads:
        # reloads contains numeric seconds; sort ascending and format with 1 decimal
        formatted = ", ".join(f"{s:.1f}s" for s in sorted(reloads))
        info["reload"] = formatted

    # Prefer a representative damage value if available (max)
    if damages:
        try:
            info["damage"] = max(damages)
        except Exception:
            info["damage"] = next(iter(damages))

    if names:
        info["name"] = ", ".join(names)

    # Range info for secondaries — prefer atbas.distance, fallback to hull.range
    atbas_distance = atbas.get("distance") if isinstance(atbas, dict) else None
    if atbas_distance and isinstance(atbas_distance, (int, float)) and atbas_distance > 0:
        info["range"] = f"{atbas_distance} km"
    else:
        if hull:
            range_info = hull.get("range", {})
            if range_info:
                range_min = range_info.get("min")
                range_max = range_info.get("max")
                if range_min and range_min > 0:
                    info["range_min"] = f"{range_min} km"
                if range_max and range_max > 0:
                    info["range_max"] = f"{range_max} km"
                if info["range_min"] != "N/A" or info["range_max"] != "N/A":
                    if info["range_min"] != "N/A" and info["range_max"] != "N/A":
                        info["range"] = f"{info['range_min']} - {info['range_max']}"
                    elif info["range_min"] != "N/A":
                        info["range"] = f"{info['range_min']} - N/A"
                    else:
                        info["range"] = f"N/A - {info['range_max']}"

    return info


def format_ship_stats(profile: dict) -> str:
    """Format default_profile stats into a readable string."""
    if not profile:
        return "No stats available"
    
    stats_parts = []
    
    # Mobility
    mobility = profile.get("mobility", {})
    if mobility:
        stats_parts.append(f"**Speed:** {mobility.get('max_speed', 'N/A')} knots")
        stats_parts.append(f"**Turning Radius:** {mobility.get('turning_radius', 'N/A')} m")
        stats_parts.append(f"**Rudder Time:** {mobility.get('rudder_time', 'N/A')} s")
    
    # Hull/Health
    hull = profile.get("hull", {})
    if hull:
        stats_parts.append(f"**HP:** {hull.get('health', 'N/A'):,}" if isinstance(hull.get('health'), int) else f"**HP:** {hull.get('health', 'N/A')}")
    
    # Artillery
    artillery = profile.get("artillery", {})
    if artillery:
        stats_parts.append(f"**Main Battery Range:** {artillery.get('distance', 'N/A')} km")
        stats_parts.append(f"**Gun Rate:** {artillery.get('gun_rate', 'N/A')} rounds/min")
        if "shells" in artillery:
            shells = artillery["shells"]
            if "AP" in shells:
                stats_parts.append(f"**AP Damage:** {shells['AP'].get('damage', 'N/A')}")
            if "HE" in shells:
                stats_parts.append(f"**HE Damage:** {shells['HE'].get('damage', 'N/A')}")
    
    # Torpedoes
    torpedoes = profile.get("torpedoes", {})
    if torpedoes:
        stats_parts.append(f"**Torpedo Range:** {torpedoes.get('distance', 'N/A')} km")
        stats_parts.append(f"**Torpedo Damage:** {torpedoes.get('max_damage', 'N/A'):,}" if isinstance(torpedoes.get('max_damage'), int) else f"**Torpedo Damage:** {torpedoes.get('max_damage', 'N/A')}")
        stats_parts.append(f"**Torpedo Speed:** {torpedoes.get('torpedo_speed', 'N/A')} knots")
    
    # Concealment
    concealment = profile.get("concealment", {})
    if concealment:
        stats_parts.append(f"**Surface Detection:** {concealment.get('detect_distance_by_ship', 'N/A')} km")
        stats_parts.append(f"**Air Detection:** {concealment.get('detect_distance_by_plane', 'N/A')} km")
    
    # Anti-Aircraft
    aa = profile.get("anti_aircraft", {})
    if aa:
        stats_parts.append(f"**AA Defense:** {aa.get('defense', 'N/A')}")
    
    return "\n".join(stats_parts) if stats_parts else "No detailed stats available"

DEFAULT_APP_ID = "8b2cb69dae93ef01067015b9d3d9ba2c"
DEFAULT_REALM = "eu"
REALM_BASE_URLS = {
    "na": "https://api.worldofwarships.com/wows/encyclopedia/ships/",
    "eu": "https://api.worldofwarships.eu/wows/encyclopedia/ships/",
    "asia": "https://api.worldofwarships.asia/wows/encyclopedia/ships/",
    "ru": "https://api.worldofwarships.ru/wows/encyclopedia/ships/",
}


def _load_api_config() -> Tuple[str, str]:
    """Resolve API credentials from env or local config file."""
    app_id = os.getenv("WWS_APP_ID") or DEFAULT_APP_ID
    realm = os.getenv("WWS_REALM") or DEFAULT_REALM
    config_path = Path(__file__).resolve().with_name("wws_api_config.json")
    if config_path.exists():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            app_id = payload.get("app_id") or app_id
            realm = payload.get("realm") or realm
        except Exception:
            pass
    return str(app_id), str(realm)


def _cache_path() -> Path:
    return Path(__file__).resolve().with_name("ships_cache.json")


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_ranges(profile: dict) -> Dict[str, Any]:
    ranges: Dict[str, Any] = {}

    artillery = profile.get("artillery", {}) if isinstance(profile, dict) else {}
    if isinstance(artillery, dict) and artillery.get("distance") is not None:
        ranges["main_battery_km"] = artillery.get("distance")

    torpedoes = profile.get("torpedoes", {}) if isinstance(profile, dict) else {}
    if isinstance(torpedoes, dict) and torpedoes.get("distance") is not None:
        ranges["torpedo_km"] = torpedoes.get("distance")

    atbas = profile.get("atbas", {}) if isinstance(profile, dict) else {}
    if isinstance(atbas, dict) and atbas.get("distance") is not None:
        ranges["secondary_km"] = atbas.get("distance")
    else:
        hull = profile.get("hull", {}) if isinstance(profile, dict) else {}
        if isinstance(hull, dict):
            range_info = hull.get("range", {})
            if isinstance(range_info, dict):
                max_range = range_info.get("max")
                if max_range is not None:
                    ranges["secondary_km"] = max_range

    aa = profile.get("anti_aircraft", {}) if isinstance(profile, dict) else {}
    if isinstance(aa, dict):
        aa_distance = aa.get("distance")
        if aa_distance is not None:
            ranges["aa_max_km"] = aa_distance
        else:
            slots = aa.get("slots", {}) or {}
            max_slot = None
            if isinstance(slots, dict):
                for slot in slots.values():
                    try:
                        dist = slot.get("distance")
                    except Exception:
                        dist = None
                    if dist is not None:
                        try:
                            dist = float(dist)
                        except Exception:
                            pass
                        if max_slot is None or dist > max_slot:
                            max_slot = dist
            if max_slot is not None:
                ranges["aa_max_km"] = max_slot

    return ranges


def _summarize_modules(ship_blob: Dict[str, Any]) -> Dict[str, Any]:
    modules = ship_blob.get("modules", {}) if isinstance(ship_blob, dict) else {}
    modules_tree = ship_blob.get("modules_tree", {}) if isinstance(ship_blob, dict) else {}

    tree_map: Dict[str, Dict[str, Any]] = {}
    if isinstance(modules_tree, dict):
        for module_id, module in modules_tree.items():
            if not isinstance(module, dict):
                continue
            mod_id = module.get("module_id", module_id)
            try:
                mod_id = str(int(mod_id))
            except Exception:
                mod_id = str(mod_id)
            tree_map[mod_id] = {
                "type": module.get("type"),
                "name": module.get("name"),
            }

    def _normalize_ids(raw: Any) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, (list, tuple, set)):
            values = list(raw)
        else:
            values = [raw]
        ids: List[str] = []
        for val in values:
            try:
                ids.append(str(int(val)))
            except Exception:
                ids.append(str(val))
        return ids

    modules_summary: Dict[str, Dict[str, Any]] = {}
    if isinstance(modules, dict):
        for raw_type, module_id in modules.items():
            ids = _normalize_ids(module_id)
            entry: Dict[str, Any] = {}
            if len(ids) == 1:
                entry["id"] = ids[0]
                if ids[0] in tree_map and tree_map[ids[0]].get("name"):
                    entry["name"] = tree_map[ids[0]].get("name")
            else:
                entry["ids"] = ids
                names = [tree_map[i].get("name") for i in ids if i in tree_map and tree_map[i].get("name")]
                if names:
                    entry["names"] = names
            modules_summary[str(raw_type)] = entry

    return {"modules": modules_summary, "modules_tree": tree_map}


def _build_stats(profile: dict) -> Dict[str, Any]:
    mobility = profile.get("mobility") if isinstance(profile, dict) else {}
    mobility = mobility if isinstance(mobility, dict) else {}
    hull = profile.get("hull") if isinstance(profile, dict) else {}
    hull = hull if isinstance(hull, dict) else {}
    artillery = profile.get("artillery") if isinstance(profile, dict) else {}
    artillery = artillery if isinstance(artillery, dict) else {}
    torpedoes = profile.get("torpedoes") if isinstance(profile, dict) else {}
    torpedoes = torpedoes if isinstance(torpedoes, dict) else {}
    concealment = profile.get("concealment") if isinstance(profile, dict) else {}
    concealment = concealment if isinstance(concealment, dict) else {}
    aa = profile.get("anti_aircraft") if isinstance(profile, dict) else {}
    aa = aa if isinstance(aa, dict) else {}

    stats: Dict[str, Any] = {
        "mobility": {
            "max_speed": mobility.get("max_speed"),
            "turning_radius": mobility.get("turning_radius"),
            "rudder_time": mobility.get("rudder_time"),
        },
        "hull": {"health": hull.get("health")},
        "artillery": {
            "range": artillery.get("distance"),
            "gun_rate": artillery.get("gun_rate"),
            "ap_damage": (artillery.get("shells") or {}).get("AP", {}).get("damage")
            if isinstance(artillery.get("shells"), dict)
            else None,
            "he_damage": (artillery.get("shells") or {}).get("HE", {}).get("damage")
            if isinstance(artillery.get("shells"), dict)
            else None,
            "main_guns": get_main_guns_info(profile),
        },
        "torpedoes": {
            "range": torpedoes.get("distance"),
            "max_damage": torpedoes.get("max_damage"),
            "torpedo_speed": torpedoes.get("torpedo_speed"),
            "reload_time": torpedoes.get("reload_time"),
            "info": get_torpedo_info(profile),
        },
        "concealment": {
            "surface_detect": concealment.get("detect_distance_by_ship"),
            "air_detect": concealment.get("detect_distance_by_plane"),
        },
        "anti_aircraft": {"defense": aa.get("defense")},
        "secondary": get_secondary_guns_info(profile),
    }

    return stats


async def _fetch_ship_details(
    session: aiohttp.ClientSession,
    ship_id: str,
    *,
    application_id: str,
    base_url: str,
) -> Optional[Dict[str, Any]]:
    async with session.get(
        f"{base_url}?application_id={application_id}&ship_id={ship_id}"
    ) as response:
        if response.status != 200:
            return None
        data = await response.json()
        if data.get("status") == "ok" and "data" in data:
            return data["data"].get(ship_id)
        return None


async def update_ships_cache(
    *,
    cache_path: Optional[Path] = None,
    concurrency: int = 6,
    sleep_seconds: float = 0.0,
    limit: int = 0,
) -> int:
    """Update ships_cache.json with modules, ranges, and stats for all ships."""
    application_id, realm = _load_api_config()
    base_url = REALM_BASE_URLS.get(realm.lower(), REALM_BASE_URLS[DEFAULT_REALM])

    cache_path = cache_path or _cache_path()
    existing_cache = _load_json(cache_path)
    preserved_meta = {
        key: value for key, value in existing_cache.items() if str(key).startswith("_")
    }

    # Fetch basic list to discover ship IDs
    all_ships = await fetch_all_ships(
        fields="name,tier,type,nation",
        save_path=None,
        application_id=application_id,
        base_url=base_url,
    )
    ship_ids = [sid for sid in all_ships.keys() if str(sid).isdigit()]
    if limit and limit > 0:
        ship_ids = ship_ids[: int(limit)]

    cache: Dict[str, Any] = {}

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _worker(sid: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        async with sem:
            data = await _fetch_ship_details(
                session,
                sid,
                application_id=application_id,
                base_url=base_url,
            )
            if sleep_seconds and sleep_seconds > 0:
                await asyncio.sleep(float(sleep_seconds))
            return sid, data

    async with aiohttp.ClientSession() as session:
        batch: List[str] = []
        for sid in ship_ids:
            batch.append(str(sid))
            if len(batch) >= max(1, int(concurrency)) * 5:
                tasks = [asyncio.create_task(_worker(x)) for x in batch]
                for ship_id, ship_blob in await asyncio.gather(*tasks):
                    if ship_blob is None:
                        # fallback to basic data if available
                        basic = all_ships.get(ship_id, {}) if isinstance(all_ships, dict) else {}
                        if ship_id in existing_cache:
                            cache[ship_id] = existing_cache[ship_id]
                        elif basic:
                            cache[ship_id] = {
                                "tier": basic.get("tier"),
                                "type": basic.get("type"),
                                "name": basic.get("name"),
                                "nation": basic.get("nation"),
                            }
                        continue

                    profile = ship_blob.get("default_profile", {}) or {}
                    entry = {
                        "tier": ship_blob.get("tier"),
                        "type": ship_blob.get("type"),
                        "name": ship_blob.get("name"),
                        "nation": ship_blob.get("nation"),
                        "modules": {},
                        "modules_tree": {},
                        "ranges": _extract_ranges(profile),
                        "stats": _build_stats(profile),
                    }
                    module_summary = _summarize_modules(ship_blob)
                    entry["modules"] = module_summary.get("modules", {})
                    entry["modules_tree"] = module_summary.get("modules_tree", {})
                    cache[ship_id] = entry
                batch = []

        if batch:
            tasks = [asyncio.create_task(_worker(x)) for x in batch]
            for ship_id, ship_blob in await asyncio.gather(*tasks):
                if ship_blob is None:
                    basic = all_ships.get(ship_id, {}) if isinstance(all_ships, dict) else {}
                    if ship_id in existing_cache:
                        cache[ship_id] = existing_cache[ship_id]
                    elif basic:
                        cache[ship_id] = {
                            "tier": basic.get("tier"),
                            "type": basic.get("type"),
                            "name": basic.get("name"),
                            "nation": basic.get("nation"),
                        }
                    continue
                profile = ship_blob.get("default_profile", {}) or {}
                entry = {
                    "tier": ship_blob.get("tier"),
                    "type": ship_blob.get("type"),
                    "name": ship_blob.get("name"),
                    "nation": ship_blob.get("nation"),
                    "modules": {},
                    "modules_tree": {},
                    "ranges": _extract_ranges(profile),
                    "stats": _build_stats(profile),
                }
                module_summary = _summarize_modules(ship_blob)
                entry["modules"] = module_summary.get("modules", {})
                entry["modules_tree"] = module_summary.get("modules_tree", {})
                cache[ship_id] = entry

    # Preserve metadata keys (e.g., "__aircraft_params__")
    for key, value in preserved_meta.items():
        cache[key] = value

    _save_json(cache_path, cache)
    return len(cache)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update ships_cache.json with ship modules and stats.")
    parser.add_argument("--update-cache", action="store_true", help="Fetch ships and update ships_cache.json")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of ships processed (0 = all)")
    parser.add_argument("--concurrency", type=int, default=6, help="Concurrent API requests")
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between requests")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.update_cache:
        print("No action specified. Use --update-cache to refresh ships_cache.json.")
        return 1

    try:
        count = asyncio.run(
            update_ships_cache(
                concurrency=args.concurrency,
                sleep_seconds=args.sleep,
                limit=args.limit,
            )
        )
    except KeyboardInterrupt:
        print("Cancelled.")
        return 1

    print(f"Updated ships_cache.json with {count} entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
