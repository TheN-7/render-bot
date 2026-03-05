#!/usr/bin/env python3
"""
entities_analyzer.py
====================
Advanced entity type analysis for WoWS replays.

Classifies and extracts entity data based on WoWS 15.1.0 definitions.
Uses entity_definitions.py mappings to understand entity structure.
"""

import sys
import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any

try:
    from core.entity_definitions import (
        get_entity_type_info,
        get_extractable_fields,
        is_battle_entity,
        is_map_object,
        list_all_entity_types,
        ENTITY_TYPES,
        USER_DATA_OBJECTS,
        BATTLE_COMPONENTS,
        INTERFACES,
    )
except ImportError:
    print("ERROR: entity_definitions.py not found in core/")
    sys.exit(1)

try:
    from core.replay_schema import to_legacy_schema
except ImportError:
    to_legacy_schema = None


def analyze_entities_from_replay(replay_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze all entities in a replay file.
    
    Args:
        replay_data: Parsed replay dictionary (from replay_extract.py)
        
    Returns:
        Categorized entity analysis
    """
    
    if "tracks" in replay_data and "events" in replay_data and to_legacy_schema is not None:
        replay_data = to_legacy_schema(replay_data)

    analysis = {
        "timestamp": None,
        "battle_entities": defaultdict(list),
        "map_objects": defaultdict(list),
        "ships": defaultdict(list),
        "players": defaultdict(list),
        "interactive_zones": [],
        "control_points": [],
        "entity_count": 0,
        "entity_relationships": [],
        "data_quality": {},
    }
    
    # ─────────────────────────────────────────────────────────────────────────
    # Analyze metadata
    # ─────────────────────────────────────────────────────────────────────────
    meta = replay_data.get("meta", {})
    
    analysis["map"] = meta.get("mapName", "Unknown")
    analysis["game_mode"] = meta.get("gameMode", "Unknown")
    analysis["duration"] = meta.get("duration", 0)
    analysis["timestamp"] = meta.get("dateTime", None)
    
    # ─────────────────────────────────────────────────────────────────────────
    # Analyze ships (handle both dict and list formats)
    # ─────────────────────────────────────────────────────────────────────────
    ships_data = replay_data.get("ships", {})
    vehicles_data = replay_data.get("vehicles", [])
    
    # If ships is a dict (standard format)
    if isinstance(ships_data, dict):
        for entity_id, ship_data in ships_data.items():
            ship_info = {
                "entity_id": entity_id,
                "type": "Vehicle",
                "team": ship_data.get("relation", None),
                "initial_hp": ship_data.get("maxHp", 0),
                "damage_taken": ship_data.get("damageTaken", 0),
                "sunk": ship_data.get("sunk", False),
                "first_position": ship_data.get("pos0", None),
                "last_position": ship_data.get("posLast", None),
            }
            
            team_key = "allies" if ship_info["team"] == 0 else "enemies"
            analysis["ships"][team_key].append(ship_info)
            analysis["entity_count"] += 1
    
    # If vehicles is a list (alternative format from replay_extract.py)
    if isinstance(vehicles_data, list):
        for vehicle in vehicles_data:
            ship_info = {
                "entity_id": vehicle.get("session_eid", None),
                "type": "Vehicle",
                "name": vehicle.get("name", "Unknown"),
                "team": vehicle.get("relation", "unknown"),
                "initial_hp": vehicle.get("max_hp", 0),
                "damage_taken": vehicle.get("damage_taken", 0),
                "sunk": vehicle.get("sunk", False),
                "first_position": vehicle.get("first_pos", None),
                "last_position": vehicle.get("last_pos", None),
                "pos_count": vehicle.get("pos_count", 0),
            }
            
            team_key = "allies" if ship_info["team"] == "ally" else "enemies"
            analysis["ships"][team_key].append(ship_info)
            analysis["entity_count"] += 1
    
    # ─────────────────────────────────────────────────────────────────────────
    # Analyze teams (players) - if available in dict format
    # ─────────────────────────────────────────────────────────────────────────
    teams = replay_data.get("teams", {})
    
    if isinstance(teams, dict):
        for team_name, players in teams.items():
            team_type = "allies" if team_name == "team0" else "enemies"
            
            for player in players:
                player_info = {
                    "type": "Avatar",  # Players are Avatar entities
                    "name": player.get("name", "Unknown"),
                    "account_id": player.get("accountDBID", None),
                    "ship_id": player.get("shipId", None),
                    "ship_name": player.get("name", "Unknown"),
                    "clan": player.get("clanTag", None),
                }
                
                analysis["players"][team_type].append(player_info)
                analysis["entity_count"] += 1
    
    
    # ─────────────────────────────────────────────────────────────────────────
    # Analyze positions (movement tracking)
    # ─────────────────────────────────────────────────────────────────────────
    positions = replay_data.get("positions", {})
    position_trails = {}
    
    if isinstance(positions, dict):
        for entity_id, trail in positions.items():
            if isinstance(trail, list) and len(trail) > 0:
                # Check if trail is list of dicts or list of tuples
                first_item = trail[0]
                
                if isinstance(first_item, dict):
                    # Dict format: [{"t": time, "x": x, "z": z}, ...]
                    start_time = first_item.get('t')
                    end_time = trail[-1].get('t') if isinstance(trail[-1], dict) else None
                    distance = calculate_distance_traveled_dict(trail)
                else:
                    # Tuple format: [(time, x, y, z, yaw), ...]
                    start_time = first_item[0] if len(first_item) > 0 else None
                    end_time = trail[-1][0] if len(trail[-1]) > 0 else None
                    distance = calculate_distance_traveled(trail)
                
                position_trails[entity_id] = {
                    "points": len(trail),
                    "start_time": start_time,
                    "end_time": end_time,
                    "distance_traveled": distance,
                }
    
    analysis["movement_tracking"] = position_trails
    
    # ─────────────────────────────────────────────────────────────────────────
    # Analyze battle events
    # ─────────────────────────────────────────────────────────────────────────
    deaths = replay_data.get("deaths", [])
    capture_pts = replay_data.get("capture_pts", [])
    
    analysis["battle_events"] = {
        "sunk_ships": len(deaths),
        "capacity_point_changes": len(capture_pts),
        "deaths": deaths,
        "captures": capture_pts,
    }
    
    # ─────────────────────────────────────────────────────────────────────────
    # Data quality assessment
    # ─────────────────────────────────────────────────────────────────────────
    analysis["data_quality"] = {
        "has_meta": bool(meta),
        "has_ships": bool(ships_data or vehicles_data),
        "has_teams": bool(teams),
        "has_positions": bool(positions),
        "has_battle_events": bool(deaths or capture_pts),
        "completeness_score": calculate_completeness(
            meta, ships_data or vehicles_data, teams, positions, deaths, capture_pts
        ),
    }
    
    return analysis


def analyze_missing_data(replay_data: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Identify what data is missing from the replay.
    """
    if "tracks" in replay_data and "events" in replay_data and to_legacy_schema is not None:
        replay_data = to_legacy_schema(replay_data)

    missing = {}
    
    # Check for expected data sections
    expected_sections = {
        "meta": "Basic metadata (map, mode, duration)",
        "teams": "Team rosters and player info",
        "ships": "Ship statistics",
        "positions": "Position/movement trails",
        "deaths": "Sunk ship events",
        "capture_pts": "Capture point events",
    }
    
    for section, description in expected_sections.items():
        if section not in replay_data or not replay_data[section]:
            missing[section] = description
    
    return missing


def classify_entity(entity_data: Dict[str, Any]) -> tuple:
    """
    Classify entity by analyzing its structure.
    
    Returns:
        (entity_type, confidence, reasons)
    """
    
    indicators = {
        "Vehicle": ["maxHp", "damageTaken", "sunk", "pos"],
        "Avatar": ["accountDBID", "shipId", "name", "clanTag"],
        "InteractiveZone": ["position", "radius", "team", "points"],
        "ControlPoint": ["position", "capture_progress"],
    }
    
    scores = {}
    for entity_type, keys in indicators.items():
        matched = sum(1 for key in keys if key in entity_data)
        confidence = matched / len(keys)
        scores[entity_type] = confidence
    
    best_type = max(scores, key=scores.get) if scores else "Unknown"
    confidence = scores.get(best_type, 0)
    
    matched_keys = [k for k in indicators.get(best_type, []) if k in entity_data]
    
    return best_type, confidence, matched_keys


def calculate_distance_traveled(trail: List[tuple]) -> float:
    """Calculate total distance traveled from position trail (tuple format)."""
    if len(trail) < 2:
        return 0.0
    
    total_distance = 0.0
    for i in range(len(trail) - 1):
        # trail format: (time, x, y, z, yaw)
        p1 = trail[i]
        p2 = trail[i + 1]
        
        if len(p1) >= 4 and len(p2) >= 4:
            dx = p2[1] - p1[1]
            dy = p2[2] - p1[2]
            dz = p2[3] - p1[3]
            distance = (dx**2 + dy**2 + dz**2) ** 0.5
            total_distance += distance
    
    return total_distance


def calculate_distance_traveled_dict(trail: List[dict]) -> float:
    """Calculate total distance traveled from position trail (dict format)."""
    if len(trail) < 2:
        return 0.0
    
    total_distance = 0.0
    for i in range(len(trail) - 1):
        # trail format: {"t": time, "x": x, "z": z, "yaw": yaw}
        p1 = trail[i]
        p2 = trail[i + 1]
        
        if isinstance(p1, dict) and isinstance(p2, dict):
            x1 = p1.get('x', 0)
            z1 = p1.get('z', 0)
            x2 = p2.get('x', 0)
            z2 = p2.get('z', 0)
            
            dx = x2 - x1
            dz = z2 - z1
            distance = (dx**2 + dz**2) ** 0.5
            total_distance += distance
    
    return total_distance


def calculate_completeness(meta, ships, teams, positions, deaths, capture_pts) -> float:
    """Calculate data completeness score (0.0 to 1.0)."""
    score = 0.0
    max_score = 6.0
    
    if meta:
        score += 1.0
    if ships:
        score += 1.0
    if teams:
        score += 1.0
    if positions:
        score += 1.0
    if deaths:
        score += 0.5
    if capture_pts:
        score += 0.5
    
    return score / max_score


def print_entity_analysis(analysis: Dict[str, Any]):
    """Pretty print entity analysis results."""
    
    print(f"\n{'='*70}")
    print(f"ENTITY ANALYSIS REPORT")
    print(f"{'='*70}")
    
    print(f"\nBattle Info:")
    print(f"  Map: {analysis.get('map', 'Unknown')}")
    print(f"  Mode: {analysis.get('game_mode', 'Unknown')}")
    print(f"  Duration: {analysis.get('duration', 0)}s")
    print(f"  Total Entities: {analysis['entity_count']}")
    
    print(f"\nData Quality:")
    quality = analysis.get("data_quality", {})
    for key, value in quality.items():
        if isinstance(value, bool):
            status = "[+]" if value else "[-]"
            print(f"  {status} {key}")
        elif isinstance(value, float):
            pct = value * 100
            print(f"  Completeness: {pct:.1f}%")
    
    print(f"\nMovement Tracking:")
    movement = analysis.get("movement_tracking", {})
    if movement:
        print(f"  Entities tracked: {len(movement)}")
        avg_points = sum(m["points"] for m in movement.values()) / len(movement)
        print(f"  Avg position samples per entity: {avg_points:.0f}")
    else:
        print(f"  No movement data")
    
    print(f"\nBattle Events:")
    events = analysis.get("battle_events", {})
    print(f"  Ships sunk: {events.get('sunk_ships', 0)}")
    print(f"  Capture point changes: {events.get('capacity_point_changes', 0)}")
    
    print(f"\n{'='*70}\n")


def main():
    """Analyze entities from a replay file."""
    
    if len(sys.argv) < 2:
        print("Entity Analysis Tool for WoWS Replays")
        print("="*50)
        print("Usage: python entities_analyzer.py <replay.json>")
        print()
        print("Requires: Replay JSON file extracted from replay_extract.py")
        return
    
    replay_json_path = sys.argv[1]
    
    try:
        with open(replay_json_path, "r", encoding="utf-8") as f:
            replay_data = json.load(f)
        
        print(f"Loading replay: {Path(replay_json_path).name}")
        print(f"File size: {len(str(replay_data)):,} bytes")
        print()
        
        # Run analysis
        analysis = analyze_entities_from_replay(replay_data)
        missing = analyze_missing_data(replay_data)
        
        # Print results
        print_entity_analysis(analysis)
        
        if missing:
            print(f"Missing Data Sections:")
            for section, description in missing.items():
                print(f"  [-] {section}: {description}")
            print()
        
        # Save analysis
        output_path = Path(replay_json_path).stem + "_entities_analysis.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, default=str)
        
        print(f"[+] Analysis saved to: {output_path}\n")
        
    except FileNotFoundError:
        print(f"ERROR: File not found: {replay_json_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
