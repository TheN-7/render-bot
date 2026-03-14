#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
import pickletools


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.replay_extract import extract_replay  # noqa: E402
from core.replay_unpack_adapter import read_replay, decode_packets  # noqa: E402
from replay_unpack.core.entity import Entity  # type: ignore  # noqa: E402
from replay_unpack.clients.wows.player import ReplayPlayer as WowsReplayPlayer  # type: ignore  # noqa: E402
from replay_unpack.clients.wows.network.packets import EntityMethod  # type: ignore  # noqa: E402


def _coerce_blob(value: Any) -> bytes | None:
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


def _scan_pickled_blob(value: Any) -> Tuple[List[str], List[float]]:
    data = _coerce_blob(value)
    if data is None:
        return [], []
    tokens: List[str] = []
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
                    tokens.append(s)
            elif name in ("BININT", "BININT1", "BININT2", "LONG", "LONG1", "LONG4", "BINFLOAT"):
                try:
                    numbers.append(float(arg))
                except Exception:
                    continue
    except Exception:
        return [], []
    return tokens, numbers


def _summarize_args(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"argc": len(args), "kw": sorted(list(kwargs.keys()))}
    if not args:
        if "consumableUsageParams" in kwargs:
            raw = kwargs.get("consumableUsageParams")
        else:
            return summary
    else:
        raw = args[0]
    summary["arg0_type"] = type(raw).__name__
    blob = _coerce_blob(raw)
    if blob is not None:
        tokens, numbers = _scan_pickled_blob(blob)
        summary["arg0_blob_len"] = len(blob)
        summary["arg0_tokens_sample"] = [t for t in tokens[:24]]
        summary["arg0_numbers_sample"] = [round(n, 3) for n in numbers[:16]]
    else:
        summary["arg0_preview"] = str(raw)[:160]
    if len(args) > 1 and isinstance(args[1], (int, float)):
        summary["arg1"] = float(args[1])
    if "workTimeLeft" in kwargs and isinstance(kwargs.get("workTimeLeft"), (int, float)):
        summary["workTimeLeft"] = float(kwargs.get("workTimeLeft"))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace consumable-related method calls for a given ship params ID.")
    parser.add_argument("replay", help="Path to .wowsreplay")
    parser.add_argument("ship_id", type=int, help="Ship params ID to trace")
    args = parser.parse_args()

    canonical = extract_replay(args.replay)
    targets: Set[int] = set()
    for entity_key, track in (canonical.get("tracks", {}) or {}).items():
        ship_id = track.get("ship_id")
        if ship_id is None:
            continue
        if int(ship_id) == int(args.ship_id):
            try:
                targets.add(int(entity_key))
            except Exception:
                continue

    if not targets:
        print(f"No entities found for ship_id={args.ship_id}.")
        return 1

    context = read_replay(args.replay)
    packets = decode_packets(context)
    player = WowsReplayPlayer(context.version)

    events: List[Dict[str, Any]] = []
    packet_time_ref = [0.0]

    def _make_handler(event_name: str):
        def _handler(entity: Any, *call_args: Any, **call_kwargs: Any) -> None:
            try:
                entity_id = int(getattr(entity, "id", -1))
            except Exception:
                return
            if entity_id not in targets:
                return
            events.append(
                {
                    "time_s": round(float(packet_time_ref[0]), 3),
                    "entity_id": entity_id,
                    "event": event_name,
                    "args": _summarize_args(call_args, call_kwargs),
                }
            )

        return _handler

    hooks = [
        ("Vehicle", "onConsumableUsed"),
        ("Vehicle", "onConsumableEnabled"),
        ("Vehicle", "onConsumableSelected"),
        ("Vehicle", "onConsumableInterrupted"),
        ("Vehicle", "onConsumablePaused"),
        ("Avatar", "useConsumable"),
        ("Avatar", "selectConsumable"),
        ("Avatar", "pauseConsumable"),
        ("Avatar", "interruptConsumable"),
    ]
    for entity_name, method_name in hooks:
        Entity.subscribe_method_call(entity_name, method_name, _make_handler(f"{entity_name}_{method_name}"))

    for packet in packets:
        packet_time_ref[0] = float(packet.time)
        if packet.packet_obj is None:
            continue
        try:
            player._process_packet(float(packet.time), packet.packet_obj)
        except Exception:
            continue

    out_dir = ROOT / "replay_debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"consumable_trace_{args.ship_id}.json"
    out_path.write_text(json.dumps(events, indent=2), encoding="utf-8")

    print(f"Tracked entity ids: {sorted(targets)}")
    print(f"Events captured: {len(events)}")
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
