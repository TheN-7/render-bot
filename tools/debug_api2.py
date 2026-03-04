import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from wows_api import load_credentials, WoWSAPI

def debug_make_request():
    creds = load_credentials()
    if not creds:
        print("No credentials found")
        return
    
    api = WoWSAPI(creds)
    
    print("Testing _make_request method...")
    
    params = {
        'application_id': creds.app_id,
        'fields': 'name,tier,type,nation',
        'limit': 10,
        'page_no': 1
    }
    
    # Test the exact same call as get_all_ships
    result = api._make_request('encyclopedia/ships/', params)
    
    if result:
        print(f"_make_request SUCCESS: Got {len(result)} items")
        print(f"Type of result: {type(result)}")
        print(f"Keys in result: {list(result.keys())[:5]}...")
        
        if 'data' in result:
            print(f"'data' key found with {len(result['data'])} items")
        else:
            print("'data' key NOT found")
            print("Available keys:", list(result.keys()))
    else:
        print("_make_request FAILED")

if __name__ == "__main__":
    debug_make_request()
