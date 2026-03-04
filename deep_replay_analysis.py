#!/usr/bin/env python3
"""
Deep analysis of WoWS replay file to find unanalyzed parts and hidden data.
"""

import sys
import os
import struct
import json
import string
from pathlib import Path

def analyze_replay_structure(replay_path: str):
    """Comprehensive analysis of replay file structure."""
    
    print(f"Deep Analysis: {Path(replay_path).name}")
    print("=" * 60)
    
    with open(replay_path, "rb") as f:
        data = f.read()
    
    print(f"File size: {len(data):,} bytes ({len(data)/1024/1024:.1f} MB)")
    
    # Analyze file header
    if len(data) >= 4:
        magic = struct.unpack_from("<I", data[:4])[0]
        print(f"Magic bytes: 0x{magic:08X}")
        
        if magic == 0x5250524:  # Expected WoWS magic
            print("✅ Valid WoWS replay format")
        else:
            print("⚠️  Unexpected magic bytes - may be corrupted or different format")
    
    # Look for JSON blocks
    print("\n📋 JSON Block Analysis:")
    json_blocks = []
    pos = 0
    
    # Skip magic and block count
    if len(data) >= 8:
        pos = 8
        num_blocks = struct.unpack_from("<I", data[4:8])[0]
        print(f"Number of blocks: {num_blocks}")
        
        # Extract each block
        for i in range(num_blocks):
            if pos >= len(data):
                break
                
            block_size = struct.unpack_from("<I", data[pos:pos+4])[0]
            if pos + 4 + block_size > len(data):
                break
                
            block_data = data[pos+4:pos+4+block_size]
            pos += 4 + block_size
            
            # Try to parse as JSON
            try:
                # Look for JSON patterns
                block_str = block_data.decode('utf-8', errors='ignore')
                if block_str.strip().startswith('{') and block_str.strip().endswith('}'):
                    try:
                        json_obj = json.loads(block_str)
                        json_blocks.append(json_obj)
                        print(f"  Block {i+1}: Valid JSON ({len(block_str)} bytes)")
                        
                        # Analyze content
                        analyze_json_content(json_obj, i+1)
                    except json.JSONDecodeError:
                        print(f"  Block {i+1}: Invalid JSON ({len(block_str)} bytes)")
                else:
                    print(f"  Block {i+1}: Non-JSON data ({len(block_str)} bytes)")
                    
            except UnicodeDecodeError:
                print(f"  Block {i+1}: Binary data ({len(block_data)} bytes)")
        
        print(f"\nTotal JSON blocks found: {len(json_blocks)}")
    
    # Look for binary section
    print("\n🔍 Binary Section Analysis:")
    if pos < len(data):
        binary_data = data[pos:]
        print(f"Binary section size: {len(binary_data):,} bytes ({len(binary_data)/1024/1024:.1f} MB)")
        
        # Look for patterns in binary data
        analyze_binary_patterns(binary_data)
    
    return json_blocks

def analyze_json_content(json_obj: dict, block_num: int):
    """Analyze content of JSON blocks."""
    
    # Look for interesting keys
    interesting_keys = [
        'vehicles', 'players', 'ships', 'battleResult',
        'statistics', 'achievements', 'consumables',
        'signals', 'chat', 'events', 'damage',
        'playerInfo', 'clanInfo', 'rankInfo',
        'economy', 'credits', 'xp', 'freeXP',
        'commanders', 'modernizations', 'camouflages'
    ]
    
    found_keys = []
    for key in interesting_keys:
        if key in json_obj:
            found_keys.append(key)
    
    if found_keys:
        print(f"    Interesting keys: {', '.join(found_keys)}")
        
        # Analyze vehicles/players in detail
        if 'vehicles' in json_obj:
            vehicles = json_obj['vehicles']
            if isinstance(vehicles, list):
                print(f"    Vehicles: {len(vehicles)} entries")
                
                # Count by relation
                relations = {}
                for vehicle in vehicles:
                    if isinstance(vehicle, dict):
                        relation = vehicle.get('relation', 'unknown')
                        relations[relation] = relations.get(relation, 0) + 1
                
                print(f"    Relations: {relations}")
                
                # Look for damage information
                damage_keys = ['damage', 'damageDealt', 'damageTaken', 'totalDamage']
                for vehicle in vehicles:
                    if isinstance(vehicle, dict):
                        for key in damage_keys:
                            if key.lower() in [k.lower() for k in vehicle.keys()]:
                                print(f"    Found damage-related key: {key}")
        
        # Look for player-specific data
        player_keys = ['playerName', 'playerVehicle', 'playerID', 'accountDBID']
        player_data = {k: json_obj[k] for k in player_keys if k in json_obj}
        if player_data:
            print(f"    Player data: {list(player_data.keys())}")

def analyze_binary_patterns(binary_data: bytes):
    """Look for patterns in binary section."""
    
    # Check for common patterns
    patterns = {
        b'Blowfish': b'Blowfish' in binary_data,
        b'zlib': b'zlib' in binary_data,
        b'position': b'position' in binary_data.lower(),
        b'damage': b'damage' in binary_data.lower(),
        b'chat': b'chat' in binary_data.lower(),
        b'signal': b'signal' in binary_data.lower(),
        b'achievement': b'achievement' in binary_data.lower(),
        b'economy': b'economy' in binary_data.lower(),
        b'credit': b'credit' in binary_data.lower(),
        b'xp': b'xp' in binary_data.lower(),
        b'frags': b'frags' in binary_data.lower(),
        b'kills': b'kills' in binary_data.lower(),
    }
    
    found_patterns = []
    for pattern_name, pattern in patterns.items():
        if pattern:
            found_patterns.append(pattern_name.decode('utf-8'))
    
    if found_patterns:
        print(f"    Binary patterns: {', '.join(found_patterns)}")
    
    # Look for packet-like structures
    print(f"    Binary section starts with: {binary_data[:50].hex()}")
    
    # Check for potential text data
    try:
        text_data = binary_data.decode('utf-8', errors='ignore')
        if len(text_data) > 100:  # If there's substantial text
            print(f"    Embedded text found: {len(text_data)} characters")
            # Show first 100 characters
            print(f"    Sample: {repr(text_data[:100])}")
    except:
        pass

def main():
    if len(sys.argv) != 2:
        print("Usage: python deep_replay_analysis.py <replay.wowsreplay>")
        return
    
    replay_path = sys.argv[1]
    
    if not os.path.exists(replay_path):
        print(f"Error: File {replay_path} not found")
        return
    
    json_blocks = analyze_replay_structure(replay_path)
    
    print("\n🎯 Summary:")
    print("=" * 60)
    print(f"JSON blocks analyzed: {len(json_blocks)}")
    print(f"Binary section size: {len(open(replay_path, 'rb').read()) - (8 + 4 if len(open(replay_path, 'rb').read()) >= 8 else 0):,} bytes")

if __name__ == "__main__":
    main()
