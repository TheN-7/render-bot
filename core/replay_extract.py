#!/usr/bin/env python3
"""
replay_extract.py
=================
Extract all available data from a .wowsreplay file into a structured JSON file.

Usage:
    python3 replay_extract.py <replay_file.wowsreplay> [output.json]

    If output path is omitted, the JSON is written next to the replay file
    with the same base name and a .json extension.

Requirements:
    pip install cryptography

What is extracted
-----------------
  meta          — map, date, game mode, client version, player name/ship
  teams         — ally and enemy rosters with ship IDs and relation
  ships         — per-ship summary: max HP, damage taken, sunk flag,
                  first/last positions, entity IDs
  positions     — full time-stamped (t, x, z, yaw) trail per entity
  deaths        — entity + clock for each ship destroyed
  capture_pts   — type-51 capture-point events (entity, count, clock)
  battle_end    — estimated battle duration in seconds

Session entity ID mapping
-------------------------
Packets use transient "session" entity IDs (e.g. 1249941..1249987).
These differ from the permanent "metadata" entity IDs stored in the
JSON header. This script maps them by sorting both lists and zip-matching,
which is the same approach used by wows_damage.py and is reliable for
standard 12-v-12 random battles.
"""

import sys
import struct
import zlib
import json
import string
import os
from io import BytesIO
from collections import defaultdict

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
except ImportError:
    print("ERROR: 'cryptography' package required.  pip install cryptography")
    sys.exit(1)

from map_names import get_map_name, get_game_mode

# ── Blowfish key (WoWS 15.x, community-extracted) ───────────────────────────
WOWS_KEY = bytes.fromhex("29b7c909383f8488fa98ec4e131979fb")

# ── HP sentinel for "not yet spotted" ───────────────────────────────────────
HP_SENTINEL = 65536


# ════════════════════════════════════════════════════════════════════════════
# Decryption
# ════════════════════════════════════════════════════════════════════════════

def _decrypt_and_decompress(path: str) -> bytes:
    with open(path, "rb") as f:
        # Skip JSON header blocks to reach binary section
        f.read(4)                                           # magic
        num_blocks = struct.unpack("<I", f.read(4))[0]
        for _ in range(num_blocks):
            size = struct.unpack("<I", f.read(4))[0]
            f.read(size)
        raw = f.read()

    cipher = Cipher(algorithms.Blowfish(WOWS_KEY), modes.ECB(),
                    backend=default_backend())
    dec  = cipher.decryptor()
    prev = None
    out  = BytesIO()

    for i in range(0, len(raw) - (len(raw) % 8), 8):
        chunk = raw[i:i + 8]
        if len(chunk) < 8:
            break
        if i == 0:          # first block skipped (WoWS quirk)
            continue
        block = struct.unpack("q", dec.update(chunk))[0]
        if prev is not None:
            block ^= prev
        prev = block
        out.write(struct.pack("q", block))

    return zlib.decompress(out.getvalue())


# ════════════════════════════════════════════════════════════════════════════
# Metadata
# ════════════════════════════════════════════════════════════════════════════

def _load_metadata(path: str) -> dict:
    with open(path, "rb") as f:
        f.read(4)
        num_blocks = struct.unpack("<I", f.read(4))[0]
        size       = struct.unpack("<I", f.read(4))[0]
        raw_block0 = f.read(size)

    printable = set(string.printable)
    cleaned   = "".join(c for c in raw_block0.decode("ascii", errors="ignore")
                        if c in printable)
    cleaned   = '{"' + cleaned.split('{"', 1)[1]
    cleaned   = cleaned[:cleaned.rfind("}") + 1]
    return json.loads(cleaned)


# ════════════════════════════════════════════════════════════════════════════
# Packet Parser
# ════════════════════════════════════════════════════════════════════════════

def _iter_packets(data: bytes):
    """
    Yield (ptype, clock, payload) for every valid packet in the stream.

    Real WoWS 15.x header (12 bytes):
      [0:4]  payload_size  uint32 LE
      [4:8]  packet_type   uint32 LE
      [8:12] clock         float32 LE
    """
    pos = 0
    while pos + 12 <= len(data):
        psize = struct.unpack_from("<I", data, pos)[0]
        ptype = struct.unpack_from("<I", data, pos + 4)[0]
        clock = struct.unpack_from("<f", data, pos + 8)[0]
        if psize == 0 or psize > 500_000 or ptype > 1000:
            pos += 1
            continue
        yield ptype, clock, data[pos + 12: pos + 12 + psize]
        pos += 12 + psize


# ════════════════════════════════════════════════════════════════════════════
# Data extraction
# ════════════════════════════════════════════════════════════════════════════

def _extract(data: bytes, meta: dict) -> dict:
    vehicles   = meta.get("vehicles", [])
    player_v   = next((v for v in vehicles if v.get("relation") == 0), None)
    others     = [v for v in vehicles if v.get("relation") != 0]

    # ── entity ID mapping ───────────────────────────────────────────────────
    # Type-10 packets carry a session entity ID for every non-player ship.
    # Sorted session IDs map index-for-index to non-player vehicles.
    t10_eids: set = set()
    for ptype, _clock, payload in _iter_packets(data):
        if ptype == 10 and len(payload) == 45:
            t10_eids.add(struct.unpack_from("<I", payload, 0)[0])

    session_eids = sorted(t10_eids)
    sess_to_meta = {eid: others[i]
                    for i, eid in enumerate(session_eids)
                    if i < len(others)}

    # The player's session entity IDs (type-8 and type-37) are the extras
    # not present in type-10.  We don't need to identify them individually.

    # ── per-pass extraction ─────────────────────────────────────────────────
    hp_track   = defaultdict(list)   # sess_eid → [hp, ...]
    positions  = defaultdict(list)   # sess_eid/"player" → [{t,x,z,yaw}, ...]
    deaths     = {}                  # sess_eid → clock
    cap_events = []                  # capture-point events
    max_clock  = 0.0

    for ptype, clock, payload in _iter_packets(data):
        if clock < 2000:             # filter bizarre float outliers
            max_clock = max(max_clock, clock)

        # ── ENTITY_METHOD (type 8): HP, death ───────────────────────────────
        if ptype == 8 and len(payload) >= 8:
            sess_eid = struct.unpack_from("<I", payload, 0)[0]
            method   = struct.unpack_from("<I", payload, 4)[0]
            args     = payload[8:]

            # Method 67 = HP broadcast every ~2 s
            # args layout: [u32][u32][f32][u32 HP][...]  HP at byte offset 12
            if method == 67 and len(args) >= 16:
                hp = struct.unpack_from("<I", args, 12)[0]
                if hp != HP_SENTINEL and 1_000 < hp < 200_000:
                    hp_track[sess_eid].append(hp)

            # Method 0 = entity destroyed / leaves world
            elif method == 0 and sess_eid not in deaths:
                # Only record as death if it had HP data (i.e. it was a ship)
                deaths[sess_eid] = round(clock, 2)

        # ── ENTITY_MOVE (type 10): non-player ship position ─────────────────
        # payload: [sess_eid:u32][x:f32][y:f32][z:f32][yaw:u16][…]
        elif ptype == 10 and len(payload) >= 15:
            sess_eid = struct.unpack_from("<I", payload, 0)[0]
            x, y, z  = struct.unpack_from("<fff", payload, 1)
            yaw_raw  = struct.unpack_from("<H", payload, 13)[0]
            positions[sess_eid].append({
                "t":   round(clock, 2),
                "x":   round(x, 2),
                "z":   round(z, 2),
                "yaw": round(yaw_raw / 65535 * 360, 1),
            })

        # ── PLAYER_POSITION (type 37): player's own ship ─────────────────────
        # payload 60 bytes: [16 misc bytes][x:f32][y:f32][z:f32][yaw:u16][…]
        elif ptype == 37 and len(payload) >= 30:
            x, y, z  = struct.unpack_from("<fff", payload, 16)
            yaw_raw  = struct.unpack_from("<H", payload, 28)[0]
            positions["player"].append({
                "t":   round(clock, 2),
                "x":   round(x, 2),
                "z":   round(z, 2),
                "yaw": round(yaw_raw / 65535 * 360, 1),
            })

        # ── TYPE 51: capture-point event (entity, cap_count) ─────────────────
        elif ptype == 51 and len(payload) >= 8:
            sess_eid = struct.unpack_from("<I", payload, 0)[0]
            cap_val  = struct.unpack_from("<I", payload, 4)[0]
            cap_events.append({
                "t":        round(clock, 2),
                "sess_eid": sess_eid,
                "count":    cap_val,
            })

    # ── Build per-ship summary ───────────────────────────────────────────────
    ships = []
    for sess_eid in session_eids:
        vm      = sess_to_meta.get(sess_eid)
        name    = vm["name"]        if vm else f"eid={sess_eid}"
        ship_id = vm.get("shipId")  if vm else None
        rel     = vm.get("relation", -1) if vm else -1
        team    = {1: "ally", 2: "enemy"}.get(rel, "unknown")
        meta_id = vm["id"]          if vm else None

        track   = hp_track.get(sess_eid, [])
        pts     = positions.get(sess_eid, [])

        max_hp  = max(track) if track else None
        min_hp  = min(track) if track else None
        dmg     = (max_hp - min_hp) if (max_hp is not None) else None

        # Only treat method-0 as death if the ship actually had HP data
        sunk    = (sess_eid in deaths) and (track is not None and len(track) > 0)
        death_t = deaths.get(sess_eid) if sunk else None

        first_pos = pts[0]  if pts else None
        last_pos  = pts[-1] if pts else None

        # Build full vehicle entry matching official battle result format
        vehicle_entry = {
            "player": vm.get("player") if vm else None,
            "index": vm.get("index") if vm else None,
            "name": name,
            "nation": vm.get("nation") if vm else None,
            "class": vm.get("class") if vm else None,
            "tier": vm.get("tier") if vm else None,
            "is_enemy": rel == 2,
            "relation": "enemy" if rel == 2 else ("ally" if rel == 1 else "unknown"),
            "server_results": {
                "xp": None,
                "raw_xp": None,
                "damage": dmg,
                "received_damage": dmg,
            },
            "session_eid": sess_eid,
            "meta_eid": meta_id,
            "ship_id": ship_id,
            "max_hp": max_hp,
            "min_hp": min_hp,
            "damage_taken": dmg,
            "hp_pct_lost": round(dmg / max_hp * 100, 1) if (dmg and max_hp) else None,
            "sunk": sunk,
            "death_clock": death_t,
            "spotted": bool(track),
            "pos_count": len(pts),
            "first_pos": first_pos,
            "last_pos": last_pos,
        }
        ships.append(vehicle_entry)

    # Player ship
    player_pts = positions.get("player", [])
    player_track = []  # player HP not available via method 67 in this version
    player_entry = {
        "name":      player_v["name"]            if player_v else "?",
        "ship_id":   player_v.get("shipId")      if player_v else None,
        "team":      "player",
        "meta_eid":  player_v["id"]              if player_v else None,
        "sess_eid":  None,
        "max_hp":    None,
        "min_hp":    None,
        "damage_taken": None,
        "hp_pct_lost":  None,
        "sunk":      False,
        "death_clock": None,
        "spotted":   True,
        "pos_count": len(player_pts),
        "first_pos": player_pts[0]  if player_pts else None,
        "last_pos":  player_pts[-1] if player_pts else None,
    }

    # ── Build team lists ─────────────────────────────────────────────────────
    allies  = [v for v in vehicles if v.get("relation") == 1]
    enemies = [v for v in vehicles if v.get("relation") == 2]

    def _veh_entry(v):
        return {
            "name":    v["name"],
            "ship_id": v.get("shipId"),
            "meta_eid": v["id"],
        }

    # ── Assemble final document ──────────────────────────────────────────────
    # Convert to human-readable names
    map_display_name = meta.get("mapDisplayName")
    map_id = meta.get("mapId")
    game_mode_id = meta.get("gameMode")
    
    return {
        "vehicles": ships + [player_entry],
        "metadata": {
            "map": get_map_name(map_display_name, map_id),
            "map_display_name": map_display_name,
            "game_mode": get_game_mode(int(game_mode_id) if game_mode_id else None),
            "game_type": meta.get("gameType"),
            "match_group": meta.get("matchGroup"),
            "version": {
                "major": 15,
                "minor": 1,
                "patch": 0,
            },
            "duration": meta.get("duration"),
            "timestamp": meta.get("dateTime"),
            "battle_result": {
                "type": "Unknown",
                "team_id": 0,
            },
        },
        "meta": {
            "replay_file":        os.path.basename(meta.get("_source_path", "")),
            "date":               meta.get("dateTime"),
            "map":                get_map_name(map_display_name, map_id),
            "map_display_name":   map_display_name,
            "map_id":             map_id,
            "game_mode":          get_game_mode(int(game_mode_id) if game_mode_id else None),
            "scenario":           meta.get("scenario"),
            "client_version":     meta.get("clientVersionFromExe"),
            "battle_duration_s":  round(max_clock, 1),
            "max_battle_time_s":  meta.get("duration"),
            "player_name":        meta.get("playerName"),
            "player_vehicle":     meta.get("playerVehicle"),
        },
        "player": player_entry,
        "teams": {
            "ally":  [_veh_entry(v) for v in allies],
            "enemy": [_veh_entry(v) for v in enemies],
        },
        "ships":       ships,
        "positions":   {
            str(sess_eid): trail
            for sess_eid, trail in positions.items()
        },
        "deaths":      [
            {"sess_eid": eid, "clock": clk}
            for eid, clk in sorted(deaths.items(), key=lambda x: x[1])
        ],
        "capture_events": cap_events,
        "stats": {
            "total_damage_taken_all": sum(
                s.get("damage_taken") or 0 for s in ships
            ),
            "damage_taken_by_team": {
                "ally": sum(
                    s.get("damage_taken") or 0 for s in ships if s.get("relation") == "ally"
                ),
                "enemy": sum(
                    s.get("damage_taken") or 0 for s in ships if s.get("relation") == "enemy"
                ),
            },
            "ships_sunk":  sum(1 for s in ships if s.get("sunk")),
            "ships_spotted": sum(1 for s in ships if s.get("spotted")),
            "ships_total": len(ships) + 1,
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# Entry Point
# ════════════════════════════════════════════════════════════════════════════

def extract_replay(replay_path: str, output_path: str | None = None) -> str:
    if not os.path.isfile(replay_path):
        raise FileNotFoundError(f"Replay not found: {replay_path!r}")

    if output_path is None:
        base = os.path.splitext(replay_path)[0]
        output_path = base + ".json"

    print(f"Loading  : {replay_path}")
    meta = _load_metadata(replay_path)
    meta["_source_path"] = replay_path

    print("Decrypting & decompressing…")
    data = _decrypt_and_decompress(replay_path)
    print(f"  {len(data):,} bytes of packet data")

    print("Extracting data…")
    doc = _extract(data, meta)

    print(f"Writing  : {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)

    ships_with_data = sum(1 for s in doc["ships"] if s["spotted"])
    print(f"Done.  {len(doc['ships'])} ships tracked, "
          f"{ships_with_data} spotted, "
          f"{doc['stats']['ships_sunk']} sunk, "
          f"{doc['stats']['total_damage_taken_all']:,} total damage taken (all ships).")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} <replay.wowsreplay> [output.json]")
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) >= 3 else None
    try:
        extract_replay(sys.argv[1], out)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
