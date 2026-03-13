#!/usr/bin/env python3
"""
Update aircraft_params.json (and optional ships_cache.json) using WG API ship modules.
Also builds a CV -> plane module listing from ships_cache.json.

Example:
  python tools/update_aircraft_params.py --from-ships-cache --write-aircraft-params
  python tools/update_aircraft_params.py --from-ships-cache --update-plane-map --write-aircraft-params
  python tools/update_aircraft_params.py --all-ships --update-plane-map --sleep 0.25 --limit 200
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlencode
from urllib.request import urlopen


DEFAULT_APP_ID = "8b2cb69dae93ef01067015b9d3d9ba2c"


def _root_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_vendor_path() -> None:
    vendor = _root_dir() / "vendor"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))


def _aircraft_params_path() -> Path:
    return _root_dir() / "aircraft_params.json"


def _ships_cache_path() -> Path:
    return _root_dir() / "ships_cache.json"


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, payload: Dict[str, Any], *, sort_keys: bool) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=sort_keys), encoding="utf-8")


def _ship_ids_from_cache(*, all_ships: bool = False) -> List[int]:
    cache = _load_json(_ships_cache_path())
    ids: List[int] = []
    if isinstance(cache, dict):
        for ship_id, data in cache.items():
            if ship_id.startswith("_"):
                continue
            if not isinstance(data, dict):
                continue
            if not all_ships and str(data.get("type")) != "AirCarrier":
                continue
            try:
                ids.append(int(ship_id))
            except ValueError:
                continue
    return ids


def _ship_ids_from_replay(replay_path: Path) -> List[int]:
    _ensure_vendor_path()
    try:
        from replay_unpack.replay_reader import ReplayReader  # type: ignore
    except Exception:
        return []
    try:
        reader = ReplayReader(str(replay_path))
        replay = reader.get_replay_data()
    except Exception:
        return []
    engine = replay.engine_data or {}
    vehicles = engine.get("vehicles", []) or []
    ids: List[int] = []
    for row in vehicles:
        if not isinstance(row, dict):
            continue
        ship_id = row.get("shipId")
        try:
            ids.append(int(ship_id))
        except Exception:
            continue
    # Filter to CVs using ships_cache.
    cache = _load_json(_ships_cache_path())
    filtered: List[int] = []
    if isinstance(cache, dict):
        for ship_id in ids:
            entry = cache.get(str(ship_id))
            if isinstance(entry, dict) and str(entry.get("type")) == "AirCarrier":
                filtered.append(ship_id)
    return sorted(set(filtered))


def _map_module_type(raw_type: str) -> Optional[str]:
    t = str(raw_type or "").strip().lower()
    if not t:
        return None
    if "torpedo" in t:
        if "deep" in t:
            return "torpedo_deepwater"
        return "torpedo"
    if "skip" in t:
        return "skip_ap" if "ap" in t else "skip"
    if "attack" in t or "rocket" in t:
        return "rocket_ap" if "ap" in t else "rocket"
    if "dive" in t or "bomb" in t:
        return "bomber_ap" if "ap" in t else "bomber"
    if "fighter" in t:
        return "fighter"
    if "asw" in t:
        return "asw"
    return None


def _build_by_cv(cache: Dict[str, Any]) -> Dict[str, Any]:
    by_cv: Dict[str, Any] = {}
    if not isinstance(cache, dict):
        return by_cv

    def _module_ids(module: Any) -> List[str]:
        ids: List[str] = []
        if not isinstance(module, dict):
            return ids
        if isinstance(module.get("ids"), list):
            for mid in module.get("ids", []):
                try:
                    ids.append(str(int(mid)))
                except Exception:
                    continue
        elif module.get("id") is not None:
            try:
                ids.append(str(int(module.get("id"))))
            except Exception:
                pass
        return ids

    plane_tokens = ("torpedo", "bomber", "dive", "skip", "attack", "rocket", "fighter", "asw")

    for ship_id, data in cache.items():
        if ship_id.startswith("_") or not isinstance(data, dict):
            continue
        if str(data.get("type")) != "AirCarrier":
            continue
        planes: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()

        modules_tree = data.get("modules_tree", {})
        if isinstance(modules_tree, dict):
            for module_id, meta in modules_tree.items():
                if not isinstance(meta, dict):
                    continue
                raw_type = str(meta.get("type") or "")
                name = str(meta.get("name") or "")
                if raw_type and raw_type.lower() in ("engine", "hull", "artillery", "torpedoes", "fire_control", "sonar", "flight_control"):
                    continue
                combined = f"{raw_type} {name}".lower()
                if raw_type and any(tok in combined for tok in plane_tokens):
                    try:
                        mid = str(int(module_id))
                    except Exception:
                        mid = str(module_id)
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)
                    planes.append(
                        {
                            "id": mid,
                            "type": raw_type,
                            "name": name,
                            "normalized_type": _map_module_type(raw_type) or _map_module_type(name) or "",
                        }
                    )

        modules = data.get("modules", {})
        if isinstance(modules, dict):
            for key in ("torpedo_bomber", "dive_bomber", "bomber", "skip_bomber", "fighter", "rocket"):
                ids = _module_ids(modules.get(key))
                name = ""
                module = modules.get(key)
                if isinstance(module, dict):
                    name = str(module.get("name") or "")
                for mid in ids:
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)
                    planes.append(
                        {
                            "id": mid,
                            "type": key,
                            "name": name,
                            "normalized_type": _map_module_type(key) or _map_module_type(name) or "",
                        }
                    )

        by_cv[str(ship_id)] = {
            "name": str(data.get("name") or ""),
            "nation": str(data.get("nation") or ""),
            "tier": data.get("tier"),
            "planes": planes,
        }
    return by_cv


def _fetch_ship_modules(app_id: str, ship_id: int) -> Dict[str, Any]:
    params = {
        "application_id": app_id,
        "ship_id": str(ship_id),
    }
    url = "https://api.worldofwarships.eu/wows/encyclopedia/ships/?" + urlencode(params)
    with urlopen(url, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "ok":
        raise RuntimeError(payload.get("error") or f"WG API error for ship_id={ship_id}")
    data = payload.get("data", {})
    return data.get(str(ship_id), {}) if isinstance(data, dict) else {}


def _extract_aircraft_modules(ship_blob: Dict[str, Any]) -> Dict[str, str]:
    modules_tree = ship_blob.get("modules_tree", {})
    if not isinstance(modules_tree, dict):
        return {}
    mapping: Dict[str, str] = {}
    for module_id, module in modules_tree.items():
        if not isinstance(module, dict):
            continue
        module_type = _map_module_type(module.get("type"))
        if not module_type:
            continue
        mid = module.get("module_id", module_id)
        try:
            key = str(int(mid))
        except Exception:
            continue
        mapping[key] = module_type
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Update aircraft params via WG API")
    parser.add_argument("--ship-id", action="append", default=[], help="Ship ID (repeatable)")
    parser.add_argument("--from-replay", action="append", default=[], help="Replay file to scan for CV ship IDs")
    parser.add_argument("--from-ships-cache", action="store_true", help="Load CV ship IDs from ships_cache.json")
    parser.add_argument("--all-ships", action="store_true", help="Load all ship IDs from ships_cache.json (large)")
    parser.add_argument("--app-id", default=DEFAULT_APP_ID, help="WG API application_id")
    parser.add_argument("--force", action="store_true", help="Override existing mappings")
    parser.add_argument(
        "--update-plane-map",
        action="store_true",
        help="Update by_plane_id mappings via WG API (module IDs, not replay params_id)",
    )
    parser.add_argument("--write-ships-cache", action="store_true", help="Store mappings under ships_cache.json")
    parser.add_argument("--write-aircraft-params", action="store_true", help="Store mappings in aircraft_params.json")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of ships processed (0 = no limit)")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N ships (useful for batching)")
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between API calls")
    args = parser.parse_args()

    ship_ids: List[int] = []
    for raw in args.ship_id:
        if raw is None:
            continue
        try:
            ship_ids.append(int(raw))
        except ValueError:
            pass
    if args.from_ships_cache or args.all_ships:
        ship_ids.extend(_ship_ids_from_cache(all_ships=bool(args.all_ships)))
    for replay_path in args.from_replay or []:
        ship_ids.extend(_ship_ids_from_replay(Path(replay_path)))
    ship_ids = sorted({sid for sid in ship_ids if sid > 0})
    if args.offset and args.offset > 0:
        ship_ids = ship_ids[int(args.offset) :]
    if args.limit and args.limit > 0:
        ship_ids = ship_ids[: int(args.limit)]

    if not ship_ids and args.update_plane_map:
        print("No ship IDs provided.")
        return 1

    write_ships_cache = bool(args.write_ships_cache or not args.write_aircraft_params)
    write_aircraft_params = bool(args.write_aircraft_params)

    aircraft_path = _aircraft_params_path()
    current_aircraft = _load_json(aircraft_path)
    if not isinstance(current_aircraft, dict):
        current_aircraft = {}

    current_plane_map = {}
    if "by_plane_id" in current_aircraft and isinstance(current_aircraft["by_plane_id"], dict):
        current_plane_map = dict(current_aircraft["by_plane_id"])
    else:
        # Backward-compat: flat mapping
        current_plane_map = dict(current_aircraft)

    ships_cache_path = _ships_cache_path()
    ships_cache = _load_json(ships_cache_path)
    if not isinstance(ships_cache, dict):
        ships_cache = {}
    cache_map = ships_cache.get("__aircraft_params__")
    if not isinstance(cache_map, dict):
        cache_map = {}

    added = 0
    if args.update_plane_map:
        for ship_id in ship_ids:
            try:
                ship_blob = _fetch_ship_modules(args.app_id, ship_id)
            except Exception as exc:
                print(f"ERROR: ship_id {ship_id}: {exc}")
                continue
            updates = _extract_aircraft_modules(ship_blob)
            if not updates:
                continue
            for key, value in updates.items():
                if key in current_plane_map and not args.force:
                    pass
                else:
                    current_plane_map[key] = value
                if key in cache_map and not args.force:
                    pass
                else:
                    cache_map[key] = value
                added += 1
            if args.sleep and args.sleep > 0:
                time.sleep(float(args.sleep))

    if write_aircraft_params:
        by_cv = _build_by_cv(ships_cache)
        payload = {"by_plane_id": current_plane_map, "by_cv": by_cv}
        _save_json(aircraft_path, payload, sort_keys=True)
        print(f"Updated {aircraft_path} (+{added} mappings, {len(by_cv)} CVs).")
    if write_ships_cache and args.update_plane_map:
        ships_cache["__aircraft_params__"] = cache_map
        _save_json(ships_cache_path, ships_cache, sort_keys=False)
        print(f"Updated {ships_cache_path} (+{added} mappings).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
