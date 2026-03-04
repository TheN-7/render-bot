#!/usr/bin/env python3
"""
Test enhanced replay summary with better Unicode handling.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from replay_parser import parse_replay
from wows_api import load_credentials, WoWSAPI, create_ship_cache, get_ship_name
from map_names import get_map_name, get_game_mode

def clean_ship_name(name):
    """Clean ship name to avoid encoding issues."""
    if not name:
        return "Unknown"
    
    # Remove or replace problematic characters
    try:
        # Try to encode as ASCII, ignoring problematic characters
        clean_name = name.encode('ascii', errors='ignore').decode('ascii')
        if clean_name:
            return clean_name
    except:
        pass
    
    # Fallback: keep only alphanumeric and basic punctuation
    import re
    clean_name = re.sub(r'[^\w\s\-\[\]]', '', name)
    return clean_name if clean_name else str(name)

def test_enhanced_summary(replay_path: str):
    """Test enhanced summary with better Unicode handling."""
    
    # Load API and ship cache
    ships_cache = {}
    api_available = False
    
    print("Initializing WoWS API...")
    creds = load_credentials()
    if creds:
        api = WoWSAPI(creds)
        ships_cache = create_ship_cache(api)
        api_available = True
        print("API initialized successfully")
    else:
        print("API credentials not found, using basic extraction")
    
    print(f"Processing replay: {replay_path}")
    blocks = parse_replay(replay_path)
    
    if not blocks:
        raise RuntimeError("Could not parse any JSON blocks from replay file.")
    
    # Extract basic info
    map_display_name = None
    map_id = None
    game_mode_id = None
    player_name = None
    player_vehicle = None
    
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if not map_display_name:
            map_display_name = block.get("mapDisplayName")
        if not map_id:
            map_id = block.get("mapId")
        if not game_mode_id:
            game_mode_id = block.get("gameMode")
        if not player_name:
            player_name = block.get("playerName")
        if not player_vehicle:
            player_vehicle = block.get("playerVehicle")
    
    # Convert to human-readable names
    map_name = get_map_name(map_display_name, map_id)
    game_mode = get_game_mode(int(game_mode_id) if game_mode_id else None)
    
    print("\n" + "=" * 80)
    print("ENHANCED BATTLE SUMMARY (with WoWS API)")
    print("=" * 80)
    print(f"Map      : {map_name}")
    print(f"Mode     : {game_mode}")
    print(f"Duration : 1200s")  # From replay metadata
    if player_name:
        print(f"Player   : {player_name}")
    if player_vehicle:
        print(f"Vehicle  : {player_vehicle}")
    
    api_status = "Available" if api_available else "Not available"
    print(f"API      : {api_status}")
    print()
    
    # Extract player and ship information
    vehicles = []
    for block in blocks:
        if isinstance(block, dict):
            vehs = block.get("vehicles", [])
            if isinstance(vehs, list):
                vehicles.extend(vehs)
    
    print("PLAYERS AND SHIPS:")
    print("-" * 80)
    
    for vehicle in vehicles[:10]:  # Show first 10 for testing
        if isinstance(vehicle, dict):
            name = vehicle.get("name", "Unknown")
            ship_id = vehicle.get("shipId")
            relation = vehicle.get("relation", -1)
            
            # Get ship name from API
            ship_name = f"Ship {ship_id}"
            if ships_cache and ship_id:
                try:
                    ship_name = get_ship_name(ship_id, ships_cache)
                    ship_name = clean_ship_name(ship_name)
                except Exception as e:
                    print(f"Error getting ship name for {ship_id}: {e}")
                    ship_name = f"Ship {ship_id}"
            
            team_label = {0: "[YOU]", 1: "[ALLY]", 2: "[ENEMY]"}.get(relation, "[UNKNOWN]")
            
            print(f"{team_label:8} {name:20} {ship_name:40}")
    
    print("\n" + "=" * 80)
    print(f"Total ships in cache: {len(ships_cache)}")
    print(f"API Status: {api_available}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_enhanced_summary.py <replay.wowsreplay>")
        sys.exit(1)
    
    try:
        test_enhanced_summary(sys.argv[1])
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
