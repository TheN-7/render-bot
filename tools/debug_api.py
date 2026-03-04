import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from wows_api import load_credentials, WoWSAPI

def debug_api():
    creds = load_credentials()
    if not creds:
        print("No credentials found")
        return
    
    api = WoWSAPI(creds)
    
    print("Testing basic API call...")
    # Test with minimal parameters first
    params = {
        'application_id': creds.app_id,
        'limit': 10,
        'page_no': 1
    }
    
    result = api._make_request('encyclopedia/ships/', params)
    
    if result:
        print(f"SUCCESS: Got {len(result)} ships")
        print("Sample ship data:")
        for i, (ship_id, ship_data) in enumerate(list(result.items())[:3]):
            print(f"  {i+1}. ID: {ship_id}, Name: {ship_data.get('name', 'No name')}")
        
        # Check meta information
        # Try a direct request to see the structure
        print("\nTesting raw API response...")
        import requests
        
        url = f"{creds.base_url}encyclopedia/ships/"
        test_params = {
            'application_id': creds.app_id,
            'limit': 5,
            'page_no': 1
        }
        
        try:
            response = requests.get(url, params=test_params, timeout=10)
            print(f"Status: {response.status_code}")
            print(f"URL: {response.url}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"Status: {data.get('status')}")
                print(f"Meta: {data.get('meta', {})}")
                print(f"Data count: {len(data.get('data', {}))}")
            else:
                print(f"Error: {response.text}")
        except Exception as e:
            print(f"Request error: {e}")
    else:
        print("FAILED: No data returned")

if __name__ == "__main__":
    debug_api()
