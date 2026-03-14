"""Extract specific consumable entries from GameParams.data without writing full JSON."""
from __future__ import annotations

import argparse
import json
import pickle
import struct
import zlib
import sys
from types import ModuleType
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


SKIP_FIELDS = {"Cameras", "DockCamera", "damageDistribution", "salvoParams"}
UNIT_TO_KM = 0.03  # 1 unit == 30m

def _install_gameparams_module() -> None:
    # GameParams.data pickles reference a GameParams module; provide a stub.
    class GameParams(ModuleType):
        class TypeInfo(object):
            pass

        class GPData(object):
            pass

    sys.modules[GameParams.__name__] = GameParams(GameParams.__name__)


def _read_gameparams(path: Path) -> Any:
    _install_gameparams_module()
    data = path.read_bytes()
    data = struct.pack("B" * len(data), *data[::-1])
    data = zlib.decompress(data)
    return pickle.loads(data, encoding="latin1")


def _iter_items(obj: Any, path: Tuple[str, ...], seen: set[int]) -> Iterable[Tuple[Tuple[str, ...], Any]]:
    obj_id = id(obj)
    if obj_id in seen:
        return
    seen.add(obj_id)

    yield path, obj

    if isinstance(obj, dict):
        for k, v in obj.items():
            _k = str(k)
            yield from _iter_items(v, path + (_k,), seen)
        return

    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _iter_items(v, path + (f"[{i}]",), seen)
        return

    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict):
        for k in list(d.keys()):
            if k in SKIP_FIELDS:
                d.pop(k, None)
        for k, v in d.items():
            yield from _iter_items(v, path + (str(k),), seen)


def _extract_dict(obj: Any) -> Dict[str, Any] | None:
    if isinstance(obj, dict):
        return obj
    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict):
        cleaned = {k: v for k, v in d.items() if k not in SKIP_FIELDS}
        return cleaned
    return None


def _contains_target(d: Dict[str, Any], targets: set[str]) -> bool:
    # Prefer explicit key matches
    for k in ("gameParamsName", "game_params_name", "gameParams", "paramName", "name"):
        v = d.get(k)
        if isinstance(v, str) and v in targets:
            return True
    # Fallback: any string value equals a target
    for v in d.values():
        if isinstance(v, str) and v in targets:
            return True
    return False


def _summarize_numbers(d: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (int, float)):
            summary[k] = v
    return summary


def _compute_fields(d: Dict[str, Any]) -> Dict[str, Any]:
    logic = d.get("logic") if isinstance(d, dict) else None
    tactical = d.get("tacticalParams") if isinstance(d, dict) else None
    work_time = None
    reload_time = None
    dist_ship = None
    dist_torp = None
    if isinstance(d, dict):
        work_time = d.get("workTime")
        reload_time = d.get("reloadTime")
    if isinstance(logic, dict):
        dist_ship = logic.get("distShip")
        dist_torp = logic.get("distTorpedo")
    if isinstance(tactical, dict) and dist_ship is None:
        dist_ship = tactical.get("workRange")
    result: Dict[str, Any] = {}
    if isinstance(work_time, (int, float)):
        result["duration_s"] = float(work_time)
    if isinstance(reload_time, (int, float)):
        result["reload_s"] = float(reload_time)
    if isinstance(dist_ship, (int, float)):
        result["ship_range_units"] = float(dist_ship)
        result["ship_range_km"] = round(float(dist_ship) * UNIT_TO_KM, 3)
    # We only need hydro ship detection range; torpedo detection is intentionally omitted.
    return result


def _jsonable(value: Any, depth: int = 0, max_depth: int = 6) -> Any:
    if depth > max_depth:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, depth + 1, max_depth) for v in value]
    d = getattr(value, "__dict__", None)
    if isinstance(d, dict):
        cleaned = {k: v for k, v in d.items() if k not in SKIP_FIELDS}
        return _jsonable(cleaned, depth + 1, max_depth)
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract consumable entries from GameParams.data")
    parser.add_argument("--gameparams", required=True, help="Path to GameParams.data")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument("--keys", nargs="+", required=True, help="Consumable keys to find (e.g. PCY020_RLSSearchPremium)")
    args = parser.parse_args()

    gp_path = Path(args.gameparams)
    out_path = Path(args.out)
    targets = {k.strip() for k in args.keys if k.strip()}

    root = _read_gameparams(gp_path)

    results: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for path, obj in _iter_items(root, tuple(), seen):
        d = _extract_dict(obj)
        if not d:
            continue
        if _contains_target(d, targets):
            path_str = "/".join(path) if path else "<root>"
            # Skip legacy/alternate variants not needed for standard ship usage.
            if any(token in path_str for token in ("/PXY", "ModernEra")):
                continue
            # If this is the main PCY entry, attach computed fields per variant.
            if path_str in ("/PCY016_SonarSearchPremium", "/PCY020_RLSSearchPremium"):
                for key, val in list(d.items()):
                    if not isinstance(val, dict):
                        continue
                    consumable_type = str(val.get("consumableType") or "").lower()
                    if consumable_type not in ("sonar", "rls"):
                        continue
                    val["_computed"] = _compute_fields(val)
            results.append({
                "path": path_str,
                "data": _jsonable(d),
                "numeric_fields": _summarize_numbers(d),
                "computed": _compute_fields(d),
            })

    payload = {
        "targets": sorted(targets),
        "match_count": len(results),
        "matches": results,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(results)} matches to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
