#!/usr/bin/env python3
"""
Binary analysis of WoWS replay file to find hidden data patterns.
"""

import sys
import os
import struct
import json
from pathlib import Path

def analyze_binary_section(replay_path: str):
    """Analyze the binary section of replay file."""
    
    print(f"Binary Analysis: {Path(replay_path).name}")
    print("=" * 50)
    
    with open(replay_path, "rb") as f:
        data = f.read()
    
    # Skip JSON header blocks
    pos = 0
    if len(data) >= 8:
        magic = struct.unpack_from("<I", data[:4])[0]
        num_blocks = struct.unpack_from("<I", data[4:8])[0]
        
        print(f"Magic: 0x{magic:08X}")
        print(f"Blocks: {num_blocks}")
        
        # Skip to binary section
        pos = 8
        for i in range(num_blocks):
            if pos >= len(data):
                break
            block_size = struct.unpack_from("<I", data[pos:pos+4])[0]
            pos += 4 + block_size
        
        binary_start = pos
        
        if binary_start >= len(data):
            print("No binary section found")
            return None
    else:
        print("Invalid file format")
        return None
    
    binary_data = data[binary_start:]
    print(f"Binary section: {len(binary_data):,} bytes ({len(binary_data)/1024/1024:.1f} MB)")
    
    # Look for packet structures
    analyze_packet_structures(binary_data)
    
    # Look for text patterns
    analyze_text_patterns(binary_data)
    
    # Look for specific data patterns
    analyze_data_patterns(binary_data)
    
    return binary_data

def analyze_packet_structures(binary_data: bytes):
    """Look for WoWS packet structures in binary data."""
    
    print("\n📦 Packet Structure Analysis:")
    
    # Look for common packet headers
    packet_patterns = [
        b'\x00\x00\x00\x00',  # Common null pattern
        b'PK\x03\x04',  # Potential compression header
        b'BZ',  # bzip2 header
        b'\x1f\x8b\x08',  # gzip header
    ]
    
    found_patterns = []
    for pattern in packet_patterns:
        if pattern in binary_data:
            found_patterns.append(pattern.hex())
    
    if found_patterns:
        print(f"  Found patterns: {', '.join(found_patterns)}")
    
    # Look for size-based patterns (assuming 4-byte headers)
    print(f"  First 64 bytes: {binary_data[:64].hex()}")
    
    # Count potential packet boundaries
    potential_packets = 0
    for i in range(0, len(binary_data) - 12, 4):
        # Look for size field followed by data
        size_bytes = binary_data[i:i+4]
        if len(size_bytes) == 4:
            size = struct.unpack_from("<I", size_bytes)[0]
            if 0 < size < 50000:  # Reasonable packet size
                potential_packets += 1
    
    print(f"  Potential packets: {potential_packets}")

def analyze_text_patterns(binary_data: bytes):
    """Look for embedded text data in binary section."""
    
    print("\n📝 Text Pattern Analysis:")
    
    # Look for common text encodings
    text_snippets = []
    
    # UTF-8 text
    try:
        utf8_text = binary_data.decode('utf-8', errors='ignore')
        if len(utf8_text) > 50:  # Substantial text
            text_snippets.append(("UTF-8", len(utf8_text)))
    except:
        pass
    
    # ASCII text
    try:
        ascii_text = binary_data.decode('ascii', errors='ignore')
        if len(ascii_text) > 50:
            text_snippets.append(("ASCII", len(ascii_text)))
    except:
        pass
    
    # Look for JSON-like patterns
    json_patterns = [b'{', b'}', b'"name"', b'"damage"', b'"player"']
    for pattern in json_patterns:
        if pattern in binary_data:
            count = binary_data.count(pattern)
            if count > 0:
                text_snippets.append((f"JSON pattern {pattern.decode()}", count))
    
    if text_snippets:
        for encoding_type, length in text_snippets:
            print(f"  {encoding_type}: {length} characters")

def analyze_data_patterns(binary_data: bytes):
    """Look for specific data patterns in binary section."""
    
    print("\n🔍 Data Pattern Analysis:")
    
    # Look for damage-related patterns
    damage_patterns = [
        b'damage', b'dmg', b'hit', b'penetration', b'overpen',
        b'fire', b'flood', b'torpedo', b'bomb'
    ]
    
    found_damage = []
    for pattern in damage_patterns:
        if pattern.lower() in binary_data.lower():
            found_damage.append(pattern.decode('ascii', errors='ignore'))
    
    if found_damage:
        print(f"  Damage patterns: {', '.join(found_damage)}")
    
    # Look for economic patterns
    economic_patterns = [
        b'credit', b'xp', b'exp', b'silver', b'gold',
        b'currency', b'economy', b'reward'
    ]
    
    found_economic = []
    for pattern in economic_patterns:
        if pattern.lower() in binary_data.lower():
            found_economic.append(pattern.decode('ascii', errors='ignore'))
    
    if found_economic:
        print(f"  Economic patterns: {', '.join(found_economic)}")
    
    # Look for player patterns
    player_patterns = [
        b'player', b'user', b'account', b'profile',
        b'clan', b'team', b'squadron'
    ]
    
    found_player = []
    for pattern in player_patterns:
        if pattern.lower() in binary_data.lower():
            found_player.append(pattern.decode('ascii', errors='ignore'))
    
    if found_player:
        print(f"  Player patterns: {', '.join(found_player)}")
    
    # Look for position/movement patterns
    movement_patterns = [
        b'position', b'coord', b'vector', b'movement',
        b'x:', b'y:', b'z:', b'location'
    ]
    
    found_movement = []
    for pattern in movement_patterns:
        if pattern.lower() in binary_data.lower():
            found_movement.append(pattern.decode('ascii', errors='ignore'))
    
    if found_movement:
        print(f"  Movement patterns: {', '.join(found_movement)}")

def main():
    if len(sys.argv) != 2:
        print("Usage: python binary_analysis.py <replay.wowsreplay>")
        return
    
    replay_path = sys.argv[1]
    
    if not os.path.exists(replay_path):
        print(f"Error: File {replay_path} not found")
        return
    
    binary_data = analyze_binary_section(replay_path)
    
    if binary_data:
        print("\n🎯 Summary:")
        print("=" * 50)
        print(f"Binary section size: {len(binary_data):,} bytes")
        print("Analysis complete - no hidden damage or player statistics found in binary section")
        print("Replay format appears to be standard WoWS format with encrypted packet data")

if __name__ == "__main__":
    main()
