#!/usr/bin/env python3
"""
WowsReplayDecoder_v2.py
=======================
Improved World of Warships Replay Decoder

Key fixes over v1:
  1. CORRECT SESSION ID → VEHICLE MAPPING
     Session entity IDs (e.g. 1087358–1087404) are sequential and map to
     meta vehicles sorted by their account entity_id ascending.
     The player's session ID is the one NOT appearing in type-10 packets.

  2. CORRECT TYPE-10 COORDINATE LAYOUT  (45-byte packets)
     [0:4]   session_entity_id  u32
     [4:8]   reserved           (0x0)
     [8:12]  x                  f32  (east/west)
     [12:16] y                  f32  (altitude, ~0)
     [16:20] z                  f32  (north/south)
     [20:32] misc state
     [32:36] yaw                f32  (0.0–1.0, multiply by 360 for degrees)

     The original code used offsets (1, x,y,z) and a u16 yaw — both wrong.

  3. CORRECT ZERO-POSITION FILTERING
     Positions (0, 0) appear before a ship is spotted. They are now filtered
     so tracks only contain real world positions.

  4. NAMED DAMAGE STATS
     Damage stats are now fully annotated with player names and team info
     instead of raw unnamed entity IDs.

  5. ACCURATE DEATH DETECTION  (type-8 method 0)
     Death packets are correlated with the corrected session ID mapping.

  6. PLAYER POSITION (type-37, 60 bytes)
     Layout verified:  [16:28] = x, y, z floats;  [28:30] = yaw u16.

Usage:
    python3 WowsReplayDecoder_v2.py -replay path/to/replay.wowsreplay
    python3 WowsReplayDecoder_v2.py -replay path/to/replay.wowsreplay -output out.json
    python3 WowsReplayDecoder_v2.py -replay path/to/replay.wowsreplay --verbose

Requirements:
    pip install cryptography
"""

import io
import os
import sys
import json
import struct
import string
import argparse
import zlib
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# ── Blowfish key (WoWS 15.x community-extracted) ────────────────────────────
WOWS_KEY = bytes.fromhex("29b7c909383f8488fa98ec4e131979fb")

PACKET_TYPES = {
    5:  "ENTITY_CREATE",   # vehicle/entity creation → used for session ID mapping
    8:  "ENTITY_METHOD",   # method calls: HP updates, death, damage events
    10: "ENTITY_MOVE",     # non-player ship position (45 bytes)
    22: "BASE_PLAYER_DATA",
    34: "CAMERA_UPDATE",
    37: "PLAYER_POSITION", # player's own ship (60 bytes)
}


# ════════════════════════════════════════════════════════════════════════════
# I/O helpers
# ════════════════════════════════════════════════════════════════════════════

def load_replay_metadata(path: str) -> dict:
    """Parse the JSON metadata block(s) at the start of the replay file."""
    result = {"block0": None, "block1": None, "binary_offset": 0}
    with open(path, "rb") as f:
        f.read(4)  # magic
        num_blocks = struct.unpack("<I", f.read(4))[0]
        if num_blocks > 10:
            raise ValueError(f"Unexpected block count: {num_blocks}")
        for i in range(num_blocks):
            size = struct.unpack("<I", f.read(4))[0]
            data = f.read(size)
            if size == 0:
                continue
            if i in (0, 1):
                try:
                    printable = set(string.printable)
                    cleaned = "".join(c for c in data.decode("ascii", errors="ignore")
                                     if c in printable)
                    cleaned = '{"' + cleaned.split('{"', 1)[1]
                    cleaned = cleaned[:cleaned.rfind("}") + 1]
                    result[f"block{i}"] = json.loads(cleaned)
                except Exception as e:
                    result[f"block{i}"] = {"parse_error": str(e)}
        result["binary_offset"] = f.tell()
    return result


def decrypt_and_decompress(path: str, binary_offset: int) -> bytes:
    """
    Read the binary section, decrypt with Blowfish ECB + XOR chaining,
    then zlib-decompress.  Returns the raw packet stream bytes.
    """
    with open(path, "rb") as f:
        f.seek(binary_offset)
        raw = f.read()

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    cipher = Cipher(algorithms.Blowfish(WOWS_KEY), modes.ECB(),
                    backend=default_backend())
    dec  = cipher.decryptor()
    out  = io.BytesIO()
    prev = None

    for i in range(0, len(raw) - (len(raw) % 8), 8):
        chunk = raw[i:i + 8]
        if len(chunk) < 8:
            break
        if i == 0:          # first block always skipped (WoWS quirk)
            continue
        block = struct.unpack("q", dec.update(chunk))[0]
        if prev is not None:
            block ^= prev
        prev = block
        out.write(struct.pack("q", block))

    return zlib.decompress(out.getvalue())


# ════════════════════════════════════════════════════════════════════════════
# Session ID ↔ Vehicle mapping  (THE KEY FIX)
# ════════════════════════════════════════════════════════════════════════════

def build_session_id_map(packet_data: bytes, vehicles: list) -> Dict[int, dict]:
    """
    Map session entity IDs (from packet stream) to vehicle metadata.

    How WoWS assigns session IDs:
      - All vehicles get consecutive even session IDs (e.g. 1087358, 1087360, …).
      - They are assigned in ascending order of the vehicle's account entity_id
        (the large IDs like 805652739 in metadata).
      - The player's own session ID does NOT appear in type-10 packets because
        the client uses type-37 for its own position.

    Returns: {session_id: vehicle_dict}
    """
    # Collect all session IDs from type-10 packets (non-player vehicles)
    type10_eids: set = set()
    pos = 0
    while pos + 12 <= len(packet_data):
        psize = struct.unpack_from("<I", packet_data, pos)[0]
        ptype = struct.unpack_from("<I", packet_data, pos + 4)[0]
        if psize == 0 or psize > 500_000 or ptype > 1000:
            pos += 1
            continue
        payload = packet_data[pos + 12: pos + 12 + psize]
        if ptype == 10 and len(payload) >= 4:
            eid = struct.unpack_from("<I", payload, 0)[0]
            type10_eids.add(eid)
        pos += 12 + psize

    # Infer all session IDs: the step between consecutive IDs is 2
    if not type10_eids:
        return {}

    min_eid = min(type10_eids)
    max_eid = max(type10_eids)
    # Generate the full range including the player's missing session ID
    all_sess_ids = sorted(range(min_eid, max_eid + 3, 2))

    # Sort vehicles by their account entity_id — this matches session ID order
    sorted_vehicles = sorted(vehicles, key=lambda v: v["id"])

    # Build the mapping
    sess_map: Dict[int, dict] = {}
    for sess_id, vehicle in zip(all_sess_ids, sorted_vehicles):
        sess_map[sess_id] = vehicle

    return sess_map


# ════════════════════════════════════════════════════════════════════════════
# Packet parsing
# ════════════════════════════════════════════════════════════════════════════

class Packet:
    __slots__ = ("offset", "ptype", "ptype_name", "clock", "size", "payload", "parsed")

    def __init__(self, offset, ptype, clock, size, payload):
        self.offset     = offset
        self.ptype      = ptype
        self.ptype_name = PACKET_TYPES.get(ptype, f"TYPE_{ptype}")
        self.clock      = clock
        self.size       = size
        self.payload    = payload
        self.parsed: dict = {}

    def to_dict(self):
        return {
            "offset":      self.offset,
            "type":        self.ptype,
            "type_name":   self.ptype_name,
            "clock":       round(self.clock, 3),
            "size":        self.size,
            "parsed":      self.parsed,
            "payload_hex": self.payload[:64].hex() + ("..." if len(self.payload) > 64 else ""),
        }


def _parse_payload(pkt: Packet) -> None:
    """
    Decode known packet types.

    TYPE-10  ENTITY_MOVE  (45 bytes, non-player ships):
      [0:4]   session_entity_id  u32
      [4:8]   reserved           u32 (always 0)
      [8:12]  x                  f32  east/west
      [12:16] y                  f32  altitude
      [16:20] z                  f32  north/south
      [20:32] misc state bytes
      [32:36] yaw_norm           f32  0..1  → × 360 = degrees

    TYPE-37  PLAYER_POSITION  (≥60 bytes):
      [16:20] x   f32
      [20:24] y   f32
      [24:28] z   f32
      [28:30] yaw u16  0..65535 → × 360/65535 = degrees

    TYPE-8   ENTITY_METHOD:
      [0:4]   entity_id  u32
      [4:8]   method_id  u32
      method 67 (+HP broadcast): bytes [20:24] = current HP u32
      method  0 (death):         marks entity as destroyed
    """
    p = pkt.payload
    t = pkt.ptype
    try:
        # ── ENTITY_MOVE (type 10) ────────────────────────────────────────────
        if t == 10 and len(p) >= 36:
            eid = struct.unpack_from("<I", p, 0)[0]
            x, y, z = struct.unpack_from("<fff", p, 8)   # correct offsets
            yaw_rad  = struct.unpack_from("<f", p, 32)[0]  # radians (-π..π)
            import math as _math
            pkt.parsed["entity_id"] = eid
            pkt.parsed["x"]         = round(x, 2)
            pkt.parsed["y"]         = round(y, 2)
            pkt.parsed["z"]         = round(z, 2)
            pkt.parsed["yaw_deg"]   = round(_math.degrees(yaw_rad) % 360, 1)

        # ── PLAYER_POSITION (type 37) ────────────────────────────────────────
        elif t == 37 and len(p) >= 30:
            x, y, z = struct.unpack_from("<fff", p, 16)
            yaw_raw = struct.unpack_from("<H", p, 28)[0]
            pkt.parsed["entity_id"] = "player"
            pkt.parsed["x"]         = round(x, 2)
            pkt.parsed["y"]         = round(y, 2)
            pkt.parsed["z"]         = round(z, 2)
            pkt.parsed["yaw_deg"]   = round(yaw_raw / 65535.0 * 360.0, 1)

        # ── ENTITY_METHOD (type 8) ───────────────────────────────────────────
        elif t == 8 and len(p) >= 8:
            eid = struct.unpack_from("<I", p, 0)[0]
            mid = struct.unpack_from("<I", p, 4)[0]
            pkt.parsed["entity_id"] = eid
            pkt.parsed["method_id"] = mid
            if mid == 67 and len(p) >= 24:   # HP broadcast
                hp = struct.unpack_from("<I", p, 20)[0]
                if 1_000 < hp < 200_000:
                    pkt.parsed["hp"] = hp
            elif mid == 0:
                pkt.parsed["death"] = True

    except (struct.error, IndexError):
        pass


def parse_packets(data: bytes, verbose: bool = False) -> List[Packet]:
    """Parse the full BigWorld packet stream."""
    packets: List[Packet] = []
    pos    = 0
    errors = 0

    while pos + 12 <= len(data):
        psize = struct.unpack_from("<I", data, pos)[0]
        ptype = struct.unpack_from("<I", data, pos + 4)[0]
        clock = struct.unpack_from("<f", data, pos + 8)[0]

        if psize == 0 or psize > 500_000 or ptype > 1000:
            pos    += 1
            errors += 1
            if errors > 200:
                if verbose:
                    print(f"WARNING: too many parse errors near offset {pos:#x}")
                break
            continue

        errors  = 0
        payload = data[pos + 12: pos + 12 + psize]
        pkt     = Packet(pos, ptype, clock, psize, payload)
        _parse_payload(pkt)
        packets.append(pkt)
        pos += 12 + psize

    return packets


# ════════════════════════════════════════════════════════════════════════════
# Minimap & stats extractor
# ════════════════════════════════════════════════════════════════════════════

def extract_battle_data(packets: List[Packet],
                        sess_map: Dict[int, dict],
                        player_sess_id: int) -> dict:
    """
    Build complete battle dataset from parsed packets + vehicle mapping.

    Returns:
        positions     – {session_id: [{t, x, z, yaw}, ...]}  (filtered non-zero)
        damage_stats  – {session_id: {name, team, max_hp, damage_taken, sunk}}
        deaths        – {session_id: {name, time}}
        spawns        – {session_id: {name, x, z, time}}
        packet_summary
    """
    positions:   Dict[int, list]  = defaultdict(list)
    spawns:      Dict[int, dict]  = {}
    deaths:      Dict[int, dict]  = {}
    hp_track:    Dict[int, list]  = defaultdict(list)
    player_track: list            = []

    for pkt in packets:
        pr = pkt.parsed

        # Non-player positions
        if pkt.ptype == 10 and "x" in pr:
            eid = pr["entity_id"]
            x, z = pr["x"], pr["z"]
            # Filter out (0,0) — pre-spotting placeholder positions
            if abs(x) > 0.5 or abs(z) > 0.5:
                point = {"t": round(pkt.clock, 2), "x": x, "z": z,
                         "yaw": pr.get("yaw_deg")}
                positions[eid].append(point)
                if eid not in spawns:
                    spawns[eid] = {"time": round(pkt.clock, 2), "x": x, "z": z}

        # Player position
        elif pkt.ptype == 37 and "x" in pr:
            x, z = pr["x"], pr["z"]
            if abs(x) > 0.5 or abs(z) > 0.5:
                player_track.append({"t": round(pkt.clock, 2), "x": x, "z": z,
                                     "yaw": pr.get("yaw_deg")})

        # HP & death events
        elif pkt.ptype == 8:
            eid = pr.get("entity_id")
            if eid is None:
                continue
            if "hp" in pr:
                hp_track[eid].append(pr["hp"])
            if pr.get("death"):
                v = sess_map.get(eid, {})
                deaths[eid] = {
                    "name": v.get("name", f"entity_{eid}"),
                    "team": _rel_to_team(v.get("relation")),
                    "time": round(pkt.clock, 2),
                }

    # Add player track under its session ID
    if player_track:
        positions[player_sess_id] = player_track
        if player_sess_id not in spawns and player_track:
            pt = player_track[0]
            spawns[player_sess_id] = {"time": pt["t"], "x": pt["x"], "z": pt["z"]}

    # Build damage stats with names
    damage_stats: Dict[int, dict] = {}
    for eid, hps in hp_track.items():
        max_hp   = max(hps)
        min_hp   = min(hps)
        v        = sess_map.get(eid, {})
        damage_stats[eid] = {
            "name":         v.get("name", f"entity_{eid}"),
            "team":         _rel_to_team(v.get("relation")),
            "ship_id":      v.get("shipId"),
            "max_hp":       max_hp,
            "min_hp":       min_hp,
            "damage_taken": max_hp - min_hp,
            "sunk":         eid in deaths,
        }

    # Annotate spawns/positions with names
    named_positions: Dict[str, dict] = {}
    for eid, track in positions.items():
        v    = sess_map.get(eid, {})
        name = v.get("name", ("Player" if eid == player_sess_id else f"entity_{eid}"))
        named_positions[str(eid)] = {
            "name":    name,
            "team":    _rel_to_team(v.get("relation", 0) if eid == player_sess_id else v.get("relation")),
            "ship_id": v.get("shipId"),
            "track":   track,
        }

    named_spawns = {}
    for eid, sp in spawns.items():
        v    = sess_map.get(eid, {})
        name = v.get("name", ("Player" if eid == player_sess_id else f"entity_{eid}"))
        named_spawns[str(eid)] = {"name": name, **sp}

    named_deaths = {str(k): v for k, v in deaths.items()}
    named_damage  = {str(k): v for k, v in damage_stats.items()}

    return {
        "positions":     named_positions,
        "spawns":        named_spawns,
        "deaths":        named_deaths,
        "damage_stats":  named_damage,
        "packet_summary": _summarize_packets(packets),
    }


def _rel_to_team(relation) -> str:
    return {0: "player", 1: "ally", 2: "enemy"}.get(relation, "unknown")


def _summarize_packets(packets: List[Packet]) -> dict:
    summary: Dict[str, int] = defaultdict(int)
    for pkt in packets:
        summary[pkt.ptype_name] += 1
    return dict(sorted(summary.items(), key=lambda x: -x[1]))


# ════════════════════════════════════════════════════════════════════════════
# Console report
# ════════════════════════════════════════════════════════════════════════════

def print_report(metadata: dict, battle_data: dict, sess_map: Dict[int, dict],
                 player_sess_id: int) -> None:
    block0 = metadata.get("block0") or {}
    vehicles = block0.get("vehicles", [])

    print("=" * 70)
    print("  WORLD OF WARSHIPS REPLAY - IMPROVED DECODER v2")
    print("=" * 70)
    print(f"  Date        : {block0.get('dateTime', 'N/A')}")
    print(f"  Map         : {block0.get('mapDisplayName', 'N/A')}  (ID {block0.get('mapId', '?')})")
    print(f"  Game Type   : {block0.get('gameType', 'N/A')}")
    print(f"  Client Ver  : {block0.get('clientVersionFromExe', 'N/A')}")
    print(f"  Your Ship   : {block0.get('playerVehicle', 'N/A')}")
    print(f"  Your Name   : {block0.get('playerName', 'N/A')}")
    print()

    # Team roster
    you     = [v for v in vehicles if v.get("relation") == 0]
    allies  = [v for v in vehicles if v.get("relation") == 1]
    enemies = [v for v in vehicles if v.get("relation") == 2]
    for label, group, sym in [("YOUR SHIP", you, "*"),
                               (f"ALLIES ({len(allies)})", allies, "+"),
                               (f"ENEMIES ({len(enemies)})", enemies, "-")]:
        print(f"  -- {label} {'-'*(46-len(label))}")
        for v in group:
            # Find session id for this vehicle
            sess = next((s for s, mv in sess_map.items() if mv["id"] == v["id"]), None)
            sess_str = f"sess={sess}" if sess else "sess=?"
            print(f"     {sym} {v['name']:<28} {sess_str}")
        print()

    # Position data quality
    pos = battle_data["positions"]
    print(f"  Entities tracked  : {len(pos)}")
    total_pts = sum(len(v["track"]) for v in pos.values())
    print(f"  Total track points: {total_pts:,}")
    print()

    # Damage report
    dmg = battle_data["damage_stats"]
    deaths = battle_data["deaths"]

    print("=" * 70)
    print("  DAMAGE TAKEN (HP lost from HP broadcast packets)")
    print("=" * 70)
    header = f"  {'Name':<28} {'Team':<8} {'MaxHP':>8}  {'DmgTaken':>9}  {'DmgPct':>7}  Status"
    print(header)
    print("  " + "-" * 66)

    for eid_str, s in sorted(dmg.items(), key=lambda x: -x[1]["damage_taken"]):
        pct   = s["damage_taken"] / s["max_hp"] * 100 if s["max_hp"] else 0
        sunk  = " SUNK" if s["sunk"] else "alive"
        print(f"  {s['name']:<28} {s['team']:<8} {s['max_hp']:>8,}  "
              f"{s['damage_taken']:>9,}  {pct:>6.1f}%  {sunk}")

    print()
    total_dmg = sum(s["damage_taken"] for s in dmg.values())
    ally_dmg  = sum(s["damage_taken"] for s in dmg.values() if s["team"] in ("ally","player"))
    enemy_dmg = sum(s["damage_taken"] for s in dmg.values() if s["team"] == "enemy")
    sunk_cnt  = sum(1 for s in dmg.values() if s["sunk"])
    print(f"  Total damage taken : {total_dmg:,}")
    print(f"  Ally  side total   : {ally_dmg:,}")
    print(f"  Enemy side total   : {enemy_dmg:,}")
    print(f"  Ships sunk         : {sunk_cnt}")
    print()

    # Deaths timeline
    if deaths:
        print("=" * 70)
        print("  SHIP DEATHS (chronological)")
        print("=" * 70)
        for eid_str, dth in sorted(deaths.items(), key=lambda x: x[1]["time"]):
            t = dth["time"]
            mins, secs = divmod(int(t), 60)
            print(f"  {mins:02d}:{secs:02d}  {dth['name']:<28}  [{dth['team']}]")
        print()

    # Packet summary
    print("=" * 70)
    print("  PACKET TYPE BREAKDOWN")
    print("=" * 70)
    for name, count in battle_data["packet_summary"].items():
        print(f"  {name:<28} {count:>7,}")
    print()


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="WoWS Replay Decoder v2 - improved session ID mapping & positions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-replay",   required=True, help="Path to .wowsreplay file")
    parser.add_argument("-output",   default=None,  help="Output JSON file")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--meta-only", action="store_true")
    args = parser.parse_args()

    if not os.path.isfile(args.replay):
        print(f"ERROR: File not found: {args.replay}")
        sys.exit(1)

    print(f"\nLoading: {args.replay}")
    metadata = load_replay_metadata(args.replay)

    block0   = metadata.get("block0") or {}
    vehicles = block0.get("vehicles", [])
    player_v = next((v for v in vehicles if v.get("relation") == 0), None)

    if args.meta_only:
        print(json.dumps(metadata, indent=2))
        if args.output:
            with open(args.output, "w") as f:
                json.dump(metadata, f, indent=2)
        return

    print("Decrypting & decompressing binary section...")
    try:
        packet_data = decrypt_and_decompress(args.replay, metadata["binary_offset"])
    except Exception as e:
        print(f"ERROR during decryption: {e}")
        sys.exit(2)
    print(f"  Decompressed: {len(packet_data):,} bytes")

    print("Building session ID -> vehicle map...")
    sess_map = build_session_id_map(packet_data, vehicles)

    # Player session ID = the one missing from type-10 packets (sorted full range)
    all_sess = sorted(sess_map.keys())
    player_sess_id = next(
        (s for s in all_sess if sess_map.get(s, {}).get("relation") == 0),
        -1
    )
    print(f"  Mapped {len(sess_map)} vehicles | Player session ID: {player_sess_id}")

    print("Parsing packets...")
    packets = parse_packets(packet_data, verbose=args.verbose)
    print(f"  Parsed {len(packets):,} packets")

    if args.verbose:
        print("\n-- First 30 known-type packets --")
        shown = 0
        for pkt in packets:
            if pkt.ptype in PACKET_TYPES and shown < 30:
                print(f"  [{pkt.offset:#010x}] t={pkt.clock:7.2f}  {pkt.ptype_name:<22} "
                      f"size={pkt.size:<6} {pkt.parsed}")
                shown += 1

    print("Extracting battle data...")
    battle_data = extract_battle_data(packets, sess_map, player_sess_id)

    print_report(metadata, battle_data, sess_map, player_sess_id)

    if args.output:
        output = {
            "metadata":    metadata,
            "session_map": {str(k): v for k, v in sess_map.items()},
            "battle_data": battle_data,
            "raw_packets": [p.to_dict() for p in packets] if args.verbose else [],
        }
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Saved to: {args.output}")


if __name__ == "__main__":
    main()
