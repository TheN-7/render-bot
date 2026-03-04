#!/usr/bin/env python3
"""
WowsReplayDecoder.py
====================
World of Warships Replay Binary Decoder & Minimap Data Extractor

Usage:
  python WowsReplayDecoder.py -replay path/to/replay.wowsreplay
  python WowsReplayDecoder.py -replay path/to/replay.wowsreplay -key 29b7c909383f8488fa98ec4e131979fb
  python WowsReplayDecoder.py -replay path/to/replay.wowsreplay -key <hex> -output decoded.json
  python WowsReplayDecoder.py -replay path/to/replay.wowsreplay --verbose

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

# ── Known working Blowfish key ───────────────────────────────────────────────
# Community-extracted from the WoWS 15.x client binary.
# The decryption is Blowfish ECB with XOR chaining — NOT plain ECB.
KNOWN_KEYS = [
    ("15.x (current)", bytes.fromhex("29b7c909383f8488fa98ec4e131979fb")),
]

# ── Real BigWorld packet types (empirically determined for WoWS 15.x) ────────
PACKET_TYPES = {
    8:  "ENTITY_METHOD",    # method calls — HP, damage, death events
    10: "ENTITY_MOVE",      # non-player ship position + yaw
    22: "BASE_PLAYER_DATA", # base data block (appears at battle start)
    34: "CAMERA_UPDATE",    # camera direction
    37: "PLAYER_POSITION",  # player's own ship position + yaw
}


# ── Metadata ──────────────────────────────────────────────────────────────────

def load_replay_metadata(path):
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


def read_binary_section(path, offset):
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read()


# ── Decryption ────────────────────────────────────────────────────────────────

def _xor_chain_decrypt(data, key):
    """
    Blowfish ECB with XOR chaining between consecutive 8-byte blocks.
    The first block is always skipped (WoWS file format quirk).
    This is NOT plain Blowfish ECB — each block is XOR'd with the previous
    decrypted block value, producing CBC-like output.
    Plain ECB produces high-entropy garbage that cannot be zlib-decompressed.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    from io import BytesIO

    cipher = Cipher(algorithms.Blowfish(key), modes.ECB(), backend=default_backend())
    dec    = cipher.decryptor()
    out    = BytesIO()
    prev   = None

    for i in range(0, len(data) - (len(data) % 8), 8):
        chunk = data[i:i + 8]
        if len(chunk) < 8:
            break
        if i == 0:          # first block always skipped
            continue
        block = struct.unpack("q", dec.update(chunk))[0]
        if prev is not None:
            block ^= prev
        prev = block
        out.write(struct.pack("q", block))

    return out.getvalue()


def decrypt_binary(data, key_hex=None, verbose=False):
    """
    Decrypt the binary section. Returns (decrypted_bytes_or_None, status_str).
    """
    keys_to_try = []
    if key_hex:
        try:
            key = bytes.fromhex(key_hex.replace(" ", "").replace("0x", ""))
            keys_to_try = [("User-supplied", key)]
        except ValueError:
            return None, f"ERROR: Invalid hex key: {key_hex!r}"
    else:
        keys_to_try = KNOWN_KEYS

    for label, key in keys_to_try:
        if verbose:
            print(f"  Trying key [{label}]: {key.hex()}")
        try:
            decrypted = _xor_chain_decrypt(data, key)
            zlib.decompress(decrypted)   # validation — raises if wrong key
            return decrypted, f"SUCCESS with key: {label} ({key.hex()})"
        except Exception:
            continue

    return None, (
        "DECRYPTION FAILED: No known key worked.\n"
        "Supply the correct key with:  -key <32_hex_chars>\n"
        "Working key for WoWS 15.x:   29b7c909383f8488fa98ec4e131979fb"
    )


# ── BigWorld Packet Parser ────────────────────────────────────────────────────

class BigWorldPacket:
    __slots__ = ("offset", "ptype", "ptype_name", "clock", "size", "payload", "parsed")

    def __init__(self, offset, ptype, clock, size, payload):
        self.offset     = offset
        self.ptype      = ptype
        self.ptype_name = PACKET_TYPES.get(ptype, f"TYPE_{ptype}")
        self.clock      = clock
        self.size       = size
        self.payload    = payload
        self.parsed     = {}

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


def parse_packets(data, verbose=False):
    """
    Parse BigWorld packet stream.

    Real WoWS 15.x packet header — 12 bytes:
      [0:4]  payload_size  uint32 LE
      [4:8]  packet_type   uint32 LE
      [8:12] clock         float32 LE  (game time in seconds)
    Followed by payload_size bytes of payload.
    """
    packets = []
    pos     = 0
    errors  = 0

    while pos + 12 <= len(data):
        psize = struct.unpack_from("<I", data, pos)[0]
        ptype = struct.unpack_from("<I", data, pos + 4)[0]
        clock = struct.unpack_from("<f", data, pos + 8)[0]

        if psize == 0 or psize > 500_000 or ptype > 1000:
            pos    += 1
            errors += 1
            if errors > 200:
                if verbose:
                    print(f"WARNING: too many parse errors near offset {pos}")
                break
            continue

        errors  = 0
        payload = data[pos + 12: pos + 12 + psize]
        pkt     = BigWorldPacket(pos, ptype, clock, psize, payload)
        _parse_payload(pkt)
        packets.append(pkt)
        pos += 12 + psize

    return packets


def _parse_payload(pkt):
    """
    Decode known packet types into structured fields in pkt.parsed.

    Coordinate system: X = east/west, Y = altitude (~0 at sea), Z = north/south.
    """
    p = pkt.payload
    t = pkt.ptype
    try:
        # ENTITY_MOVE (type 10): non-player ship position
        # [entity_id:u32][x:f32][y:f32][z:f32][yaw:u16][36 more bytes]
        if t == 10 and len(p) >= 15:
            eid      = struct.unpack_from("<I", p, 0)[0]
            x, y, z  = struct.unpack_from("<fff", p, 1)
            yaw_raw  = struct.unpack_from("<H", p, 13)[0]
            pkt.parsed["entity_id"] = eid
            pkt.parsed["x"]         = round(x, 2)
            pkt.parsed["y"]         = round(y, 2)
            pkt.parsed["z"]         = round(z, 2)
            pkt.parsed["yaw_deg"]   = round(yaw_raw / 65535 * 360, 1)

        # PLAYER_POSITION (type 37): player's own ship
        # [16 bytes misc][x:f32][y:f32][z:f32][yaw:u16][...]
        elif t == 37 and len(p) >= 30:
            x, y, z  = struct.unpack_from("<fff", p, 16)
            yaw_raw  = struct.unpack_from("<H", p, 28)[0]
            pkt.parsed["entity_id"] = "player"
            pkt.parsed["x"]         = round(x, 2)
            pkt.parsed["y"]         = round(y, 2)
            pkt.parsed["z"]         = round(z, 2)
            pkt.parsed["yaw_deg"]   = round(yaw_raw / 65535 * 360, 1)

        # ENTITY_METHOD (type 8): [entity_id:u32][method_id:u32][args...]
        elif t == 8 and len(p) >= 8:
            eid = struct.unpack_from("<I", p, 0)[0]
            mid = struct.unpack_from("<I", p, 4)[0]
            pkt.parsed["entity_id"] = eid
            pkt.parsed["method_id"] = mid
            # Method 67 = HP broadcast: args[12:16] = current HP (uint32)
            if mid == 67 and len(p) >= 24:
                hp = struct.unpack_from("<I", p, 20)[0]
                if 1_000 < hp < 200_000:
                    pkt.parsed["hp"] = hp
            elif mid == 0:
                pkt.parsed["death"] = True

    except (struct.error, IndexError):
        pass


# ── Minimap Data Extractor ───────────────────────────────────────────────────

def extract_minimap_data(packets, metadata):
    """
    Build a structured minimap dataset from parsed packets + JSON metadata.
    Returns positions, spawns, deaths, damage_stats, and team lists.
    """
    entity_positions = defaultdict(list)
    entity_spawns    = {}
    entity_leaves    = {}
    hp_track         = defaultdict(list)

    for pkt in packets:
        pr = pkt.parsed

        if pkt.ptype in (10, 37) and "x" in pr:
            eid = pr["entity_id"]
            pt  = {"t": round(pkt.clock, 2), "x": pr["x"], "z": pr["z"],
                   "yaw": pr.get("yaw_deg")}
            entity_positions[eid].append(pt)
            if eid not in entity_spawns:
                entity_spawns[eid] = {"time": round(pkt.clock, 2),
                                      "x": pr["x"], "z": pr["z"]}

        elif pkt.ptype == 8:
            eid = pr.get("entity_id")
            if not eid:
                continue
            if "hp" in pr:
                hp_track[eid].append(pr["hp"])
            if pr.get("death"):
                entity_leaves[eid] = {"time": round(pkt.clock, 2)}

    damage_stats = {}
    for eid, hps in hp_track.items():
        max_hp = max(hps)
        min_hp = min(hps)
        damage_stats[eid] = {
            "max_hp": max_hp, "min_hp": min_hp,
            "damage_taken": max_hp - min_hp,
            "sunk": eid in entity_leaves,
        }

    block0   = metadata.get("block0") or {}
    vehicles = block0.get("vehicles", [])

    return {
        "meta":          {
            "map":            block0.get("mapDisplayName"),
            "date":           block0.get("dateTime"),
            "player":         block0.get("playerName"),
            "player_ship":    block0.get("playerVehicle"),
            "game_type":      block0.get("gameType"),
            "client_version": block0.get("clientVersionFromExe"),
        },
        "teams":          _build_team_lists(vehicles),
        "entity_spawns":  {str(k): v for k, v in entity_spawns.items()},
        "entity_leaves":  {str(k): v for k, v in entity_leaves.items()},
        "positions":      {str(eid): trail
                           for eid, trail in entity_positions.items() if trail},
        "damage_stats":   {str(k): v for k, v in damage_stats.items()},
        "packet_summary": _summarize_packets(packets),
    }


def _build_team_lists(vehicles):
    teams = {"player": [], "ally": [], "enemy": []}
    for v in vehicles:
        entry = {"name": v["name"], "ship_id": v["shipId"], "entity_id": v["id"]}
        r = v.get("relation", -1)
        if r == 0:   teams["player"].append(entry)
        elif r == 1: teams["ally"].append(entry)
        elif r == 2: teams["enemy"].append(entry)
    return teams


def _extract_damage_statistics(packets):
    hp_track = defaultdict(list)
    deaths   = set()
    for pkt in packets:
        if pkt.ptype != 8:
            continue
        pr  = pkt.parsed
        eid = pr.get("entity_id")
        if not eid:
            continue
        if "hp" in pr:
            hp_track[eid].append(pr["hp"])
        if pr.get("death"):
            deaths.add(eid)
    result = {}
    for eid, hps in hp_track.items():
        max_hp = max(hps)
        result[eid] = {
            "max_hp": max_hp, "min_hp": min(hps),
            "damage_taken": max_hp - min(hps),
            "sunk": eid in deaths,
        }
    return result


def _summarize_packets(packets):
    summary = defaultdict(int)
    for pkt in packets:
        summary[pkt.ptype_name] += 1
    return dict(sorted(summary.items(), key=lambda x: -x[1]))


# ── Console Report ────────────────────────────────────────────────────────────

def print_metadata_report(metadata):
    m = metadata.get("block0") or {}
    print("=" * 60)
    print("  WORLD OF WARSHIPS REPLAY - METADATA REPORT")
    print("=" * 60)
    print(f"  Date/Time    : {m.get('dateTime', 'N/A')}")
    print(f"  Map          : {m.get('mapDisplayName', 'N/A')} (ID {m.get('mapId', '?')})")
    print(f"  Game Type    : {m.get('gameType', 'N/A')}")
    print(f"  Client Ver   : {m.get('clientVersionFromExe', 'N/A')}")
    print(f"  Your Ship    : {m.get('playerVehicle', 'N/A')}")
    print(f"  Your Name    : {m.get('playerName', 'N/A')}")
    print()
    vehicles = m.get("vehicles", [])
    you     = [v for v in vehicles if v.get("relation") == 0]
    allies  = [v for v in vehicles if v.get("relation") == 1]
    enemies = [v for v in vehicles if v.get("relation") == 2]
    for label, group in [("YOUR SHIP", you), (f"ALLIES ({len(allies)})", allies),
                         (f"ENEMIES ({len(enemies)})", enemies)]:
        print(f"  ── {label} {'─'*(45-len(label))}")
        sym_map = {0: "★", 1: "+", 2: "-"}
        for v in group:
            print(f"     {sym_map.get(v.get('relation'),'?')} {v['name']:<24} entity_id={v['id']}")
        print()
    print(f"  Binary offset: {metadata['binary_offset']} bytes")
    print()


def print_decryption_status(status, decrypted):
    print("=" * 60)
    print("  DECRYPTION STATUS")
    print("=" * 60)
    if decrypted:
        print(f"  ✓ {status}")
        print(f"  Decrypted size : {len(decrypted):,} bytes")
        print(f"  First 8 bytes  : {decrypted[:8].hex()}")
    else:
        for line in status.split("\n"):
            print(f"  {line}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="WoWS Replay Decoder — extract minimap/battle data from .wowsreplay files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-replay",     required=True,       help="Path to .wowsreplay file")
    parser.add_argument("-key",        default=None,        help="Blowfish key as 32 hex chars")
    parser.add_argument("-output",     default=None,        help="Output JSON file")
    parser.add_argument("--verbose",   action="store_true", help="Print every parsed packet")
    parser.add_argument("--meta-only", action="store_true", help="Only print metadata, skip decryption")
    args = parser.parse_args()

    if not os.path.isfile(args.replay):
        print(f"ERROR: File not found: {args.replay}")
        sys.exit(1)

    print(f"\nLoading: {args.replay}")
    metadata = load_replay_metadata(args.replay)
    print_metadata_report(metadata)

    if args.meta_only:
        if args.output:
            with open(args.output, "w") as f:
                json.dump(metadata, f, indent=2)
            print(f"Metadata saved to: {args.output}")
        return

    raw_binary = read_binary_section(args.replay, metadata["binary_offset"])
    print(f"Binary section: {len(raw_binary):,} bytes\n")

    decrypted, status = decrypt_binary(raw_binary, args.key, verbose=args.verbose)
    print_decryption_status(status, decrypted)

    if decrypted is None:
        sys.exit(2)

    game_data = zlib.decompress(decrypted)
    print(f"Decompressed: {len(game_data):,} bytes\n")

    print("Parsing packets...")
    packets = parse_packets(game_data, verbose=args.verbose)
    print(f"Parsed {len(packets):,} packets\n")

    if args.verbose:
        print("── First 50 packets ──")
        for pkt in packets[:50]:
            print(f"  [{pkt.offset:08x}] t={pkt.clock:7.2f}  {pkt.ptype_name:<22} "
                  f"size={pkt.size:<6} {pkt.parsed}")

    minimap_data = extract_minimap_data(packets, metadata)

    print("── Summary ──────────────────────────────────")
    print(f"  Entity trails   : {len(minimap_data['positions'])}")
    print(f"  Entity spawns   : {len(minimap_data['entity_spawns'])}")
    print(f"  Entity deaths   : {len(minimap_data['entity_leaves'])}")
    print(f"  HP-tracked ships: {len(minimap_data['damage_stats'])}")
    print()

    if minimap_data["damage_stats"]:
        print("── Damage (HP lost per visible entity) ──────")
        for eid, s in sorted(minimap_data["damage_stats"].items(),
                             key=lambda x: -x[1]["damage_taken"])[:15]:
            sunk = " SUNK" if s.get("sunk") else ""
            print(f"  entity {eid:<10} maxHP={s['max_hp']:>7,}  dmg={s['damage_taken']:>7,}{sunk}")
        print()

    print("── Packet Type Breakdown ────────────────────")
    for name, count in minimap_data["packet_summary"].items():
        print(f"  {name:<28} {count:>6}")

    if args.output:
        output_data = {
            "metadata":    metadata,
            "minimap":     minimap_data,
            "raw_packets": [p.to_dict() for p in packets] if args.verbose else [],
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nSaved to: {args.output}")


if __name__ == "__main__":
    main()
