import json

# Check for problematic characters in ship names
with open('ships_cache.json', 'r', encoding='utf-8') as f:
    ships = json.load(f)

print(f"Total ships in cache: {len(ships)}")

# Check first few ships for special characters
for i, (ship_id, ship_data) in enumerate(list(ships.items())[:10]):
    name = ship_data.get('name', 'No name')
    print(f"{i+1}. ID: {ship_id}, Name: {repr(name)}")
    
    # Try to encode as ASCII
    try:
        ascii_name = name.encode('ascii', errors='ignore').decode('ascii')
        if ascii_name != name:
            print(f"   Contains non-ASCII: {name} -> {ascii_name}")
    except Exception as e:
        print(f"   Encoding error: {e}")

# Look for the specific ship from the replay
target_ship_id = "3759585072"  # Player's ship ID
if target_ship_id in ships:
    ship_data = ships[target_ship_id]
    name = ship_data.get('name', 'No name')
    print(f"\nTarget ship ({target_ship_id}): {repr(name)}")
    print(f"Tier: {ship_data.get('tier')}")
    print(f"Type: {ship_data.get('type')}")
else:
    print(f"\nTarget ship {target_ship_id} not found in cache")
