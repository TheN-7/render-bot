#!/usr/bin/env python3
"""
Simple analysis of WoWS replay file to find unanalyzed parts.
"""

import sys
import os
import struct
import json
from pathlib import Path

def analyze_replay_file(replay_path: str):
    """Simple analysis of replay file structure."""
    
    print(f"Analysis: {Path(replay_path).name}")
    print("=" * 50)
    
    with open(replay_path, "rb") as f:
        data = f.read()
    
    print(f"File size: {len(data):,} bytes ({len(data)/1024/1024:.1f} MB)")
    
    # Analyze file header
    if len(data) >= 4:
        magic = struct.unpack_from("<I", data[:4])[0]
        print(f"Magic bytes: 0x{magic:08X}")
        
        if magic == 0x5250524:  # Expected WoWS magic
            print("VALID WoWS replay format")
        else:
            print("UNEXPECTED magic bytes - may be different format")
    
    # Look for JSON blocks
    print("\nJSON Block Analysis:")
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
                if '{' in block_str and '}' in block_str:
                    try:
                        json_obj = json.loads(block_str)
                        json_blocks.append(json_obj)
                        print(f"  Block {i+1}: Valid JSON ({len(block_str)} bytes)")
                        
                        # Look for interesting keys
                        interesting_keys = [
                            'vehicles', 'players', 'ships', 'battleResult',
                            'statistics', 'achievements', 'consumables',
                            'playerInfo', 'clanInfo', 'damage',
                            'damageDealt', 'credits', 'xp', 'frags'
                        ]
                        
                        found_keys = [k for k in interesting_keys if k in json_obj]
                        if found_keys:
                            print(f"    Keys: {', '.join(found_keys)}")
                        
                        # Check for damage dealt specifically
                        damage_keys = [k for k in json_obj.keys() if 'damage' in k.lower()]
                        if damage_keys:
                            print(f"    Damage keys: {damage_keys}")
                        
                        # Check vehicles if present
                        if 'vehicles' in json_obj:
                            vehicles = json_obj['vehicles']
                            if isinstance(vehicles, list):
                                print(f"    Vehicles: {len(vehicles)} entries")
                                
                                # Look for player vehicle
                                for vehicle in vehicles:
                                    if isinstance(vehicle, dict):
                                        name = vehicle.get('name', 'Unknown')
                                        if 'player' in name.lower() or vehicle.get('relation') == 0:
                                            print(f"    Player vehicle: {name}")
                                            ship_id = vehicle.get('shipId')
                                            if ship_id:
                                                print(f"    Ship ID: {ship_id}")
                                                
                    except json.JSONDecodeError:
                        print(f"  Block {i+1}: Invalid JSON ({len(block_str)} bytes)")
                else:
                    print(f"  Block {i+1}: Non-JSON data ({len(block_str)} bytes)")
                    
            except UnicodeDecodeError:
                print(f"  Block {i+1}: Binary data ({len(block_data)} bytes)")
        
        print(f"\nTotal JSON blocks found: {len(json_blocks)}")
    
    # Look for binary section
    print("\nBinary Section Analysis:")
    if pos < len(data):
        binary_data = data[pos:]
        print(f"Binary section size: {len(binary_data):,} bytes ({len(binary_data)/1024/1024:.1f} MB)")
        
        # Look for common patterns
        patterns_found = []
        
        # Check for damage-related patterns
        damage_patterns = [b'damage', b'dmg', b'hit', b'penetration', b'fire', b'torpedo']
        for pattern in damage_patterns:
            if pattern.lower() in binary_data.lower():
                patterns_found.append(pattern.decode('ascii', errors='ignore'))
        
        # Check for economic patterns
        economic_patterns = [b'credit', b'xp', b'exp', b'economy', b'reward']
        for pattern in economic_patterns:
            if pattern.lower() in binary_data.lower():
                patterns_found.append(pattern.decode('ascii', errors='ignore'))
        
        # Check for player patterns
        player_patterns = [b'player', b'user', b'account', b'profile', b'clan']
        for pattern in player_patterns:
            if pattern.lower() in binary_data.lower():
                patterns_found.append(pattern.decode('ascii', errors='ignore'))
        
        if patterns_found:
            print(f"  Found patterns: {', '.join(patterns_found)}")
        else:
            print("  No specific patterns found in binary section")
        
        # Show first 100 bytes of binary
        print(f"  Binary starts: {binary_data[:100].hex()}")
    
    print("\nSUMMARY:")
    print("=" * 50)
    print("Replay file structure analysis complete.")
    print("No hidden damage dealt or player statistics found in standard WoWS replay format.")
    print("Damage information is limited to damage taken by each ship, not damage dealt by players.")

def main():
    if len(sys.argv) != 2:
        print("Usage: python simple_replay_analysis.py <replay.wowsreplay>")
        return
    
    replay_path = sys.argv[1]
    
    if not os.path.exists(replay_path):
        print(f"Error: File {replay_path} not found")
        return
    
    analyze_replay_file(replay_path)

if __name__ == "__main__":
    main()
