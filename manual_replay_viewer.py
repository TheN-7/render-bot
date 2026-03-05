#!/usr/bin/env python3
"""
Manual WoWS Replay Viewer - Multiple viewing methods.
"""

import sys
import os
import struct
import json
import zlib
from pathlib import Path

def view_raw_bytes(replay_path: str, num_bytes: int = 100):
    """View raw bytes of replay file."""
    print(f"Raw Bytes View: {Path(replay_path).name}")
    print("=" * 50)
    
    with open(replay_path, "rb") as f:
        data = f.read()
    
    print(f"File size: {len(data)} bytes")
    print(f"Showing first {num_bytes} bytes:")
    
    # Show bytes in hex and ASCII
    for i in range(0, min(num_bytes, len(data))):
        byte_val = data[i]
        hex_val = f"{byte_val:02X}"
        
        # Try to display as ASCII if printable
        if 32 <= byte_val <= 126:
            ascii_char = chr(byte_val)
        else:
            ascii_char = "."
        
        print(f"{i:3d}: {hex_val} {ascii_char}  {byte_val:3d}")

def view_json_blocks(replay_path: str):
    """View JSON blocks from replay file."""
    print(f"JSON Blocks View: {Path(replay_path).name}")
    print("=" * 50)
    
    with open(replay_path, "rb") as f:
        data = f.read()
    
    # Parse JSON blocks
    pos = 0
    if len(data) >= 8:
        magic = struct.unpack_from("<I", data[:4])[0]
        if magic == 0x5250524:  # WoWS magic
            pos = 8
            num_blocks = struct.unpack_from("<I", data[4:8])[0]
            
            for i in range(num_blocks):
                if pos >= len(data):
                    break
                    
                block_size = struct.unpack_from("<I", data[pos:pos+4])[0]
                if pos + 4 + block_size > len(data):
                    break
                    
                block_data = data[pos+4:pos+4+block_size]
                pos += 4 + block_size
                
                print(f"\n--- Block {i+1} ---")
                print(f"Size: {block_size} bytes")
                
                # Try to decode as JSON
                try:
                    block_str = block_data.decode('utf-8', errors='ignore')
                    if '{' in block_str and '}' in block_str:
                        try:
                            json_obj = json.loads(block_str)
                            print(f"Valid JSON with {len(json_obj)} keys")
                            
                            # Pretty print JSON
                            json_str = json.dumps(json_obj, indent=2, ensure_ascii=False)
                            print(json_str)
                            
                        except json.JSONDecodeError as e:
                            print(f"Invalid JSON: {e}")
                    else:
                        print(f"Non-JSON data ({len(block_str)} bytes)")
                        
                except UnicodeDecodeError:
                    print(f"Binary data ({len(block_data)} bytes)")
        
        else:
            print("Not a valid WoWS replay file")

def view_binary_section(replay_path: str):
    """View binary/encrypted section of replay file."""
    print(f"Binary Section View: {Path(replay_path).name}")
    print("=" * 50)
    
    with open(replay_path, "rb") as f:
        data = f.read()
    
    # Find binary section
    pos = 0
    if len(data) >= 8:
        magic = struct.unpack_from("<I", data[:4])[0]
        if magic == 0x5250524:
            pos = 8
            num_blocks = struct.unpack_from("<I", data[4:8])[0]
            
            # Skip JSON blocks
            for i in range(num_blocks):
                if pos >= len(data):
                    break
                block_size = struct.unpack_from("<I", data[pos:pos+4])[0]
                pos += 4 + block_size
            
            # Binary section starts here
            binary_data = data[pos:]
            print(f"Binary section size: {len(binary_data)} bytes")
            print(f"Binary section starts at offset: 0x{pos:X}")
            
            # Show first 256 bytes in multiple formats
            print("\nFirst 256 bytes (Hex):")
            hex_data = binary_data[:256].hex()
            for i in range(0, len(hex_data), 32):
                print(hex_data[i:i+32])
            
            print("\nFirst 256 bytes (Decimal):")
            for i in range(0, min(256, len(binary_data))):
                byte_val = binary_data[i]
                print(f"{byte_val:3d} ", end="")
                if (i + 1) % 16 == 0:
                    print()
            
            print("\nFirst 256 bytes (ASCII where possible):")
            for i in range(0, min(256, len(binary_data))):
                byte_val = binary_data[i]
                if 32 <= byte_val <= 126:
                    print(chr(byte_val), end="")
                else:
                    print(".", end="")
                if (i + 1) % 16 == 0:
                    print()
            
            # Look for patterns
            print("\nPattern Analysis:")
            patterns = {
                b'Blowfish': b'Blowfish' in binary_data,
                b'zlib': b'zlib' in binary_data,
                b'position': b'position' in binary_data.lower(),
                b'damage': b'damage' in binary_data.lower(),
                b'chat': b'chat' in binary_data.lower(),
                b'player': b'player' in binary_data.lower(),
                b'vehicle': b'vehicle' in binary_data.lower(),
                b'ship': b'ship' in binary_data.lower(),
            }
            
            for pattern_name, pattern in patterns.items():
                if pattern:
                    print(f"  {pattern_name.decode('ascii')}: Found")
                else:
                    print(f"  {pattern_name.decode('ascii')}: Not found")

def extract_player_info(replay_path: str):
    """Extract and display player information from replay."""
    print(f"Player Info: {Path(replay_path).name}")
    print("=" * 50)
    
    with open(replay_path, "rb") as f:
        data = f.read()
    
    # Parse JSON blocks
    pos = 0
    if len(data) >= 8:
        magic = struct.unpack_from("<I", data[:4])[0]
        if magic == 0x5250524:
            pos = 8
            num_blocks = struct.unpack_from("<I", data[4:8])[0]
            
            for i in range(num_blocks):
                if pos >= len(data):
                    break
                    
                block_size = struct.unpack_from("<I", data[pos:pos+4])[0]
                if pos + 4 + block_size > len(data):
                    break
                    
                block_data = data[pos+4:pos+4+block_size]
                pos += 4 + block_size
                
                try:
                    block_str = block_data.decode('utf-8', errors='ignore')
                    if '{' in block_str and '}' in block_str:
                        json_obj = json.loads(block_str)
                        
                        # Look for player-specific data
                        player_fields = [
                            'playerName', 'playerVehicle', 'playerID', 'accountDBID',
                            'avatar', 'clanTag', 'level', 'rank'
                        ]
                        
                        found_fields = {}
                        for field in player_fields:
                            if field in json_obj:
                                found_fields[field] = json_obj[field]
                        
                        if found_fields:
                            print("\nPlayer Information:")
                            for field, value in found_fields.items():
                                print(f"  {field}: {value}")
                        
                        # Look for vehicles
                        if 'vehicles' in json_obj:
                            vehicles = json_obj['vehicles']
                            print(f"\nVehicles: {len(vehicles)} entries")
                            
                            player_vehicle = None
                            for vehicle in vehicles:
                                if isinstance(vehicle, dict):
                                    name = vehicle.get('name', 'Unknown')
                                    relation = vehicle.get('relation', -1)
                                    ship_id = vehicle.get('shipId')
                                    
                                    if relation == 0:  # Player
                                        player_vehicle = vehicle
                                        print(f"\nYOUR VEHICLE:")
                                        print(f"  Name: {name}")
                                        print(f"  Ship ID: {ship_id}")
                                        print(f"  Relation: Player (0)")
                                    
                                    print(f"  {name}: Ship ID {ship_id}, Relation {relation}")
                        
                        # Look for battle info
                        battle_fields = [
                            'mapDisplayName', 'mapId', 'gameMode', 'duration',
                            'winnerTeam', 'battleResult', 'dateTime'
                        ]
                        
                        found_battle = {}
                        for field in battle_fields:
                            if field in json_obj:
                                found_battle[field] = json_obj[field]
                        
                        if found_battle:
                            print("\nBattle Information:")
                            for field, value in found_battle.items():
                                print(f"  {field}: {value}")
                        
                        break  # Found player info, no need to check other blocks
                        
                except UnicodeDecodeError:
                    print(f"Block {i+1}: Binary data")
        
        else:
            print("Not a valid WoWS replay file")

def interactive_viewer(replay_path: str):
    """Interactive replay viewer with menu."""
    while True:
        print("\n" + "=" * 60)
        print("WoWS Replay Viewer - Interactive Mode")
        print("=" * 60)
        print("1. View raw bytes (first 100 bytes)")
        print("2. View JSON blocks")
        print("3. View binary section")
        print("4. Extract player info")
        print("5. Exit")
        print("=" * 60)
        
        choice = input("Enter your choice (1-5): ").strip()
        
        if choice == "1":
            view_raw_bytes(replay_path)
        elif choice == "2":
            view_json_blocks(replay_path)
        elif choice == "3":
            view_binary_section(replay_path)
        elif choice == "4":
            extract_player_info(replay_path)
        elif choice == "5":
            print("Exiting...")
            break
        else:
            print("Invalid choice. Please enter 1-5.")

def main():
    if len(sys.argv) != 2:
        print("Usage: python manual_replay_viewer.py <replay.wowsreplay>")
        print("       python manual_replay_viewer.py <replay.wowsreplay> interactive")
        return
    
    replay_path = sys.argv[1]
    
    if not os.path.exists(replay_path):
        print(f"Error: File {replay_path} not found")
        return
    
    # Check if interactive mode
    if len(sys.argv) > 2 and sys.argv[2].lower() == "interactive":
        interactive_viewer(replay_path)
    else:
        # Default: show all views
        print("Showing all available views...")
        view_raw_bytes(replay_path, 100)
        print("\n" + "=" * 50 + "\n")
        view_json_blocks(replay_path)
        print("\n" + "=" * 50 + "\n")
        view_binary_section(replay_path)
        print("\n" + "=" * 50 + "\n")
        extract_player_info(replay_path)

if __name__ == "__main__":
    main()
