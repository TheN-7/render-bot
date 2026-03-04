#!/usr/bin/env python3
"""
Setup script for WoWS API integration.

This script helps you configure your API credentials and tests the connection.
"""

import os
import json
import sys
from wows_api import load_credentials, WoWSAPI, create_ship_cache

def setup_credentials():
    """Interactive setup for API credentials."""
    print("WoWS API Setup")
    print("=" * 50)
    print("This script will help you configure your WoWS API credentials.")
    print()
    
    # Get API Application ID
    app_id = input("Enter your WoWS Application ID: ").strip()
    if not app_id:
        print("ERROR: Application ID is required!")
        return False
    
    # Get realm
    print("\nAvailable realms:")
    print("  na - North America")
    print("  eu - Europe") 
    print("  asia - Asia")
    print("  ru - Russia")
    
    realm = input("Enter your realm (default: na): ").strip().lower()
    if not realm:
        realm = "eu"
    elif realm not in ["na", "eu", "asia", "ru"]:
        print("WARNING: Invalid realm, using 'na'")
        realm = "na"
    
    # Save to config file
    config = {
        "app_id": "8b2cb69dae93ef01067015b9d3d9ba2c",
        "realm": realm
    }
    
    config_file = "wws_api_config.json"
    try:
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"SUCCESS: Configuration saved to {config_file}")
    except Exception as e:
        print(f"ERROR: Error saving config: {e}")
        return False
    
    # Test the configuration
    print("\nTesting API connection...")
    try:
        creds = load_credentials()
        if creds:
            api = WoWSAPI(creds)
            
            # Test API with a simple request
            ships_data = api.get_all_ships()
            if ships_data:
                print(f"SUCCESS: API connection successful!")
                print(f"   Retrieved {len(ships_data)} ships from encyclopedia")
                
                # Test ship lookup
                if len(ships_data) > 0:
                    sample_ship_id = list(ships_data.keys())[0]
                    sample_ship = ships_data[sample_ship_id]
                    print(f"   Sample ship: {sample_ship.get('name', 'Unknown')}")
                
                return True
            else:
                print("ERROR: API test failed - no data returned")
                return False
        else:
            print("ERROR: Could not load credentials")
            return False
            
    except Exception as e:
        print(f"ERROR: API test failed: {e}")
        return False

def create_ship_cache_interactive():
    """Create ship cache with progress indication."""
    print("\nCreating ship cache...")
    
    creds = load_credentials()
    if not creds:
        print("ERROR: API credentials not configured!")
        return False
    
    api = WoWSAPI(creds)
    ships_cache = create_ship_cache(api)
    
    if ships_cache:
        print(f"SUCCESS: Ship cache created with {len(ships_cache)} ships")
        return True
    else:
        print("ERROR: Failed to create ship cache")
        return False

def show_status():
    """Show current configuration status."""
    print("Current Configuration Status")
    print("=" * 50)
    
    # Check environment variables
    app_id_env = os.getenv('WWS_APP_ID')
    realm_env = os.getenv('WWS_REALM')
    
    if app_id_env:
        print(f"SUCCESS: Environment variables configured:")
        print(f"   WWS_APP_ID: {app_id_env[:8]}...{app_id_env[-4:]}")
        print(f"   WWS_REALM: {realm_env or 'na'}")
    else:
        print("INFO: No environment variables found")
    
    # Check config file
    config_file = "wws_api_config.json"
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            print(f"SUCCESS: Config file found: {config_file}")
            print(f"   App ID: {config.get('app_id', 'Not set')[:8]}...{config.get('app_id', '')[-4:]}")
            print(f"   Realm: {config.get('realm', 'Not set')}")
        except Exception as e:
            print(f"ERROR: Error reading config file: {e}")
    else:
        print("INFO: No config file found")
    
    # Check ship cache
    cache_file = "ships_cache.json"
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cache = json.load(f)
            print(f"SUCCESS: Ship cache found: {len(cache)} ships")
        except Exception as e:
            print(f"ERROR: Error reading ship cache: {e}")
    else:
        print("INFO: No ship cache found")

def main():
    print("WoWS Replay Tools - API Setup")
    print("=" * 50)
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "setup":
            if setup_credentials():
                create_ship_cache_interactive()
        elif command == "test":
            creds = load_credentials()
            if creds:
                api = WoWSAPI(creds)
                ships_data = api.get_all_ships()
                if ships_data:
                    print(f"SUCCESS: API working! Found {len(ships_data)} ships")
                else:
                    print("ERROR: API test failed")
            else:
                print("ERROR: No credentials found")
        elif command == "cache":
            create_ship_cache_interactive()
        elif command == "status":
            show_status()
        else:
            print("Available commands:")
            print("  setup  - Interactive setup")
            print("  test   - Test API connection")
            print("  cache  - Create ship cache")
            print("  status - Show current status")
    else:
        print("Usage:")
        print("  python setup_api.py setup   - Interactive setup")
        print("  python setup_api.py test    - Test API connection")
        print("  python setup_api.py cache   - Create ship cache")
        print("  python setup_api.py status  - Show current status")
        print()
        show_status()

if __name__ == "__main__":
    main()
