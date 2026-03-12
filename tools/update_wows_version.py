from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List
from io import BytesIO


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.replay_extract import extract_replay
from core.replay_schema import validate_extraction
from core.replay_unpack_adapter import ReplayDecodeError, WowsReplayPlayer, decode_packets, read_replay
from replay_unpack.core.network.net_packet import NetPacket  # type: ignore
from replay_unpack.clients.wows.network.packets import (  # type: ignore
    PACKETS_MAPPING,
    PACKETS_MAPPING_12_6,
    PlayerPosition,
)


VERSIONS_DIR = ROOT / "vendor" / "replay_unpack" / "clients" / "wows" / "versions"
DEFAULT_REPORT_ROOT = ROOT / "replay_debug" / "version_updates"

RENDER_PACKET_NAMES = {
    "Position",
    "PlayerPosition",
    "EntityCreate",
    "EntityMethod",
    "EntityProperty",
    "NestedProperty",
    "BattleStats",
    "Map",
    "Version",
    "EntityEnter",
    "EntityLeave",
}


def _version_candidates(version_parts: List[str]) -> List[str]:
    clean = [str(part).strip() for part in version_parts if str(part).strip()]
    candidates: List[str] = []
    if len(clean) >= 4:
        candidates.append("_".join(clean[:4]))
    if len(clean) >= 3:
        short = "_".join(clean[:3])
        if short not in candidates:
            candidates.append(short)
    return candidates


def _target_version_dir_name(version_parts: List[str]) -> str:
    candidates = _version_candidates(version_parts)
    if not candidates:
        raise ReplayDecodeError("Replay version is missing or malformed")
    return candidates[-1]


def _packet_mapping(version_parts: List[str]) -> Dict[int, Any]:
    major_minor_patch = tuple(int(x) for x in (version_parts + ["0", "0", "0"])[:3])
    if major_minor_patch >= (12, 6, 0):
        mapping = dict(PACKETS_MAPPING_12_6)
    else:
        mapping = dict(PACKETS_MAPPING)

    if major_minor_patch >= (15, 1, 0):
        mapping[0x2C] = PlayerPosition
    return mapping


def _dump_raw_packets(replay_path: str, output_path: Path, filter_names: set[str] | None = None) -> None:
    context = read_replay(replay_path)
    mapping = _packet_mapping(context.version)
    data = context.decrypted_data
    stream = BytesIO(data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        while stream.tell() < len(data):
            packet = NetPacket(stream)
            packet_cls = mapping.get(packet.type)
            packet_name = packet_cls.__name__ if packet_cls else f"TYPE_{packet.type}"
            if filter_names and packet_name not in filter_names:
                continue
            raw_bytes = packet.raw_data
            if isinstance(raw_bytes, BytesIO):
                raw_bytes = raw_bytes.getvalue()
            handle.write(
                json.dumps(
                    {
                        "time": round(float(packet.time), 6),
                        "packet_type": hex(int(packet.type)),
                        "packet_name": packet_name,
                        "raw_len": len(raw_bytes),
                        "raw_hex": raw_bytes.hex(),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )


def _existing_version_dir(version_parts: List[str]) -> Path | None:
    for candidate in _version_candidates(version_parts):
        path = VERSIONS_DIR / candidate
        if path.is_dir():
            return path
    return None


def _packet_summary(replay_path: str) -> Dict[str, Any]:
    context = read_replay(replay_path)
    packets = decode_packets(context)
    type_counts = Counter(int(packet.packet_type) for packet in packets)
    name_counts = Counter(str(packet.packet_name) for packet in packets)
    unknown_types = sorted({int(packet.packet_type) for packet in packets if str(packet.packet_name).startswith("TYPE_")})

    return {
        "version": ".".join(context.version),
        "packet_count": len(packets),
        "packet_types": {hex(packet_type): count for packet_type, count in sorted(type_counts.items())},
        "packet_names": dict(sorted(name_counts.items())),
        "unknown_packet_types": [hex(packet_type) for packet_type in unknown_types],
    }


def _strict_player_check(replay_path: str) -> Dict[str, Any]:
    context = read_replay(replay_path)
    try:
        player = WowsReplayPlayer(context.version)
        player.play(context.decrypted_data, strict_mode=True)
        info = player.get_info() or {}
        players = info.get("players", {}) if isinstance(info, dict) else {}
        player_count = len(players) if isinstance(players, dict) else len(players or [])
        return {"ok": True, "player_count": player_count}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _extraction_summary(replay_path: str) -> Dict[str, Any]:
    try:
        canonical = extract_replay(replay_path)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    validation = validate_extraction(canonical)
    events = canonical.get("events", {}) or {}
    diagnostics = canonical.get("diagnostics", {}) or {}

    return {
        "ok": True,
        "canonical": canonical,
        "validation_ok": bool(validation.ok),
        "validation_errors": list(validation.errors),
        "shape": {
            "tracks": len(canonical.get("tracks", {}) or {}),
            "entities": len(canonical.get("entities", {}) or {}),
            "deaths": len(events.get("deaths", []) or []),
            "kills": len(events.get("kills", []) or []),
            "captures": len(events.get("captures", []) or []),
            "health": len(events.get("health", []) or []),
            "player_status": len(events.get("player_status", []) or []),
            "chat": len(events.get("chat", []) or []),
            "torpedoes": len(events.get("torpedoes", []) or []),
            "artillery": len(events.get("artillery", []) or []),
            "packet_counts": len(diagnostics.get("packet_counts", {}) or {}),
        },
    }


def _compare_summaries(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    base_unknown = set(baseline.get("packet_summary", {}).get("unknown_packet_types", []) or [])
    cand_unknown = set(candidate.get("packet_summary", {}).get("unknown_packet_types", []) or [])
    base_types = set((baseline.get("packet_summary", {}) or {}).get("packet_types", {}).keys())
    cand_types = set((candidate.get("packet_summary", {}) or {}).get("packet_types", {}).keys())

    base_extract = baseline.get("extract", {}) or {}
    cand_extract = candidate.get("extract", {}) or {}
    base_shape = base_extract.get("shape", {}) if base_extract.get("ok") else {}
    cand_shape = cand_extract.get("shape", {}) if cand_extract.get("ok") else {}
    shape_delta = {}
    for key in sorted(set(base_shape) | set(cand_shape)):
        if base_shape.get(key) != cand_shape.get(key):
            shape_delta[key] = {"baseline": base_shape.get(key), "candidate": cand_shape.get(key)}

    manual_review_reasons: List[str] = []
    if not candidate.get("strict_play", {}).get("ok"):
        manual_review_reasons.append("strict replay player check failed")
    if not candidate.get("extract", {}).get("ok"):
        manual_review_reasons.append("canonical extraction failed")
    if candidate.get("extract", {}).get("ok") and not candidate.get("extract", {}).get("validation_ok"):
        manual_review_reasons.append("canonical validation failed")
    if cand_unknown - base_unknown:
        manual_review_reasons.append("new unknown packet types detected")
    # New packet types alone are not a blocker if they are known and parsing succeeds.
    # They are still recorded in the report for manual inspection.

    return {
        "new_unknown_packet_types": sorted(cand_unknown - base_unknown),
        "packet_types_only_in_candidate": sorted(cand_types - base_types),
        "packet_types_only_in_baseline": sorted(base_types - cand_types),
        "shape_delta": shape_delta,
        "manual_review_needed": bool(manual_review_reasons),
        "manual_review_reasons": manual_review_reasons,
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_report_markdown(path: Path, report: Dict[str, Any]) -> None:
    baseline = report.get("baseline", {}) or {}
    candidate = report.get("candidate", {}) or {}
    scaffold = report.get("scaffold", {}) or {}
    comparison = report.get("comparison", {}) or {}

    lines = [
        "# WoWS Version Update Report",
        "",
        f"- Baseline replay: `{baseline.get('path', '')}`",
        f"- Candidate replay: `{candidate.get('path', '')}`",
        f"- Baseline version: `{baseline.get('version', '')}`",
        f"- Candidate version: `{candidate.get('version', '')}`",
        f"- Scaffold created: `{scaffold.get('created', False)}`",
        f"- Target version folder: `{scaffold.get('target_dir', '')}`",
        f"- Manual review needed: `{comparison.get('manual_review_needed', False)}`",
        "",
        "## Manual Review Reasons",
    ]
    reasons = comparison.get("manual_review_reasons", []) or []
    if reasons:
        lines.extend([f"- {reason}" for reason in reasons])
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## New Unknown Packet Types",
        ]
    )
    unknowns = comparison.get("new_unknown_packet_types", []) or []
    if unknowns:
        lines.extend([f"- `{value}`" for value in unknowns])
    else:
        lines.append("- none")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_side_report(replay_path: Path) -> Dict[str, Any]:
    context = read_replay(str(replay_path))
    return {
        "path": str(replay_path),
        "version": ".".join(context.version),
        "supported_dir": str(_existing_version_dir(context.version) or ""),
        "packet_summary": _packet_summary(str(replay_path)),
        "strict_play": _strict_player_check(str(replay_path)),
        "extract": _extraction_summary(str(replay_path)),
    }


def _scaffold_candidate_version(baseline_version_dir: Path, candidate_version_parts: List[str], apply: bool) -> Dict[str, Any]:
    target_name = _target_version_dir_name(candidate_version_parts)
    target_dir = VERSIONS_DIR / target_name
    already_exists = target_dir.is_dir()
    created = False

    if apply and not already_exists and baseline_version_dir.resolve() != target_dir.resolve():
        shutil.copytree(baseline_version_dir, target_dir)
        created = True

    return {
        "created": created,
        "already_exists": already_exists,
        "source_dir": str(baseline_version_dir),
        "target_dir": str(target_dir),
    }


def build_report(baseline_replay: Path, candidate_replay: Path, apply: bool) -> Dict[str, Any]:
    baseline_context = read_replay(str(baseline_replay))
    candidate_context = read_replay(str(candidate_replay))

    baseline_version_dir = _existing_version_dir(baseline_context.version)
    if baseline_version_dir is None:
        raise RuntimeError(
            f"Baseline replay version {'.'.join(baseline_context.version)} is not supported locally, "
            "so there is nothing safe to copy from."
        )

    scaffold = _scaffold_candidate_version(baseline_version_dir, candidate_context.version, apply)
    baseline = _build_side_report(baseline_replay)
    candidate = _build_side_report(candidate_replay)

    report = {
        "baseline": baseline,
        "candidate": candidate,
        "scaffold": scaffold,
        "comparison": _compare_summaries(baseline, candidate),
    }
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scaffold and compare WoWS replay support between a known-good version and a newer replay version.",
    )
    parser.add_argument("baseline_replay", help="Replay from the last known working WoWS version")
    parser.add_argument("candidate_replay", help="Replay from the newer WoWS version to evaluate")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_REPORT_ROOT),
        help="Directory where reports and extracted canonicals will be written",
    )
    parser.add_argument(
        "--no-apply",
        action="store_true",
        help="Do not create the new vendor version folder; only report what would happen",
    )
    parser.add_argument(
        "--skip-canonical-dump",
        action="store_true",
        help="Do not write baseline/candidate canonical JSON files when extraction succeeds",
    )
    parser.add_argument(
        "--dump-raw-packets",
        action="store_true",
        help="Write raw packet dumps (.jsonl) for baseline and candidate replays",
    )
    parser.add_argument(
        "--dump-render-packets",
        action="store_true",
        help="Write filtered raw packet dumps (.jsonl) with only render-relevant packet types",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_replay = Path(args.baseline_replay).expanduser().resolve()
    candidate_replay = Path(args.candidate_replay).expanduser().resolve()
    report_root = Path(args.output_dir).expanduser().resolve()

    report = build_report(baseline_replay, candidate_replay, apply=not args.no_apply)

    candidate_version = report.get("candidate", {}).get("version", "unknown").replace(".", "_")
    output_dir = report_root / candidate_version
    _write_json(output_dir / "baseline_report.json", report.get("baseline", {}) or {})
    _write_json(output_dir / "candidate_report.json", report.get("candidate", {}) or {})
    _write_json(output_dir / "comparison_report.json", report)
    _write_report_markdown(output_dir / "comparison_report.md", report)

    if not args.skip_canonical_dump:
        baseline_extract = (report.get("baseline", {}) or {}).get("extract", {}) or {}
        candidate_extract = (report.get("candidate", {}) or {}).get("extract", {}) or {}
        if baseline_extract.get("ok") and isinstance(baseline_extract.get("canonical"), dict):
            _write_json(output_dir / "baseline_canonical.json", baseline_extract["canonical"])
        if candidate_extract.get("ok") and isinstance(candidate_extract.get("canonical"), dict):
            _write_json(output_dir / "candidate_canonical.json", candidate_extract["canonical"])

    if args.dump_raw_packets or args.dump_render_packets:
        filters = RENDER_PACKET_NAMES if args.dump_render_packets else None
        _dump_raw_packets(str(baseline_replay), output_dir / "baseline_raw_packets.jsonl", filters)
        _dump_raw_packets(str(candidate_replay), output_dir / "candidate_raw_packets.jsonl", filters)

    comparison = report.get("comparison", {}) or {}
    print(f"Baseline version:  {report['baseline']['version']}")
    print(f"Candidate version: {report['candidate']['version']}")
    print(f"Target folder:     {report['scaffold']['target_dir']}")
    print(f"Folder created:    {report['scaffold']['created']}")
    print(f"Manual review:     {comparison.get('manual_review_needed', False)}")
    if comparison.get("manual_review_reasons"):
        print("Reasons:")
        for reason in comparison["manual_review_reasons"]:
            print(f"  - {reason}")
    print(f"Report written to: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
