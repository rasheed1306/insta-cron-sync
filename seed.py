import os
import requests
from datetime import datetime, timedelta, timezone
from config import supabase
from utils import get_instagram_api_url
from auth import refresh_token
from globals import RequestContext

def seed_account(ig_user_id, access_token):
    """
    Seeds a single account if it doesn't exist.
    """
    if not ig_user_id or not access_token:
        return

    # Check if exists
    res = supabase.table('instagram_accounts').select("*").eq('ig_user_id', ig_user_id).execute()
    if not res.data:
        print(f"Seeding account {ig_user_id} from .env...")

        # Fetch the actual Instagram account name
        account_name = 'Initial Account'  # Fallback
        try:
            response = requests.get(get_instagram_api_url(f"{ig_user_id}"), params={
                'fields': 'name',
                'access_token': access_token
            })
            if response.status_code == 200:
                data = response.json()
                account_name = data.get('name', 'Initial Account')
                print(f"Fetched account name: {account_name}")
                RequestContext.total_requests_this_run += 1
            else:
                print(f"Failed to fetch account name: {response.text}")
        except Exception as e:
            print(f"Error fetching account name: {e}")

        # Temporary set expiry token to be now to force refresh on first run
        expires_at = datetime.now(timezone.utc)
        
        account_data = {
            'ig_user_id': ig_user_id,
            'account_name': account_name,
            'access_token': access_token,
            'token_expires_at': expires_at.isoformat(),
            'priority': 2 # Default priority is 2
        }
        supabase.table('instagram_accounts').insert(account_data).execute()
        account = supabase.table('instagram_accounts').select("*").eq('ig_user_id', ig_user_id).execute().data[0]
        refresh_token(account)

        print(f"Account {ig_user_id} seeded.")
    else:
        print(f"Account {ig_user_id} already exists.")

def seed_initial_account():
    """
    Scans environment variables for INSTAGRAM_USER_ID* and seeds them.
    """
    print("Scanning environment variables for seed accounts...")
    
    # Find all keys that start with INSTAGRAM_USER_ID
    env_vars = os.environ
    user_keys = [key for key in env_vars if key.startswith("INSTAGRAM_USER_ID")]
    
    if not user_keys:
        print("No INSTAGRAM_USER_ID* variables found in environment.")
        return

    for user_key in user_keys:
        user_id = env_vars.get(user_key)
        if not user_id:
            continue
            
        # Determine the suffix (e.g., "", "_2", "_3") to find matching token
        suffix = user_key.replace("INSTAGRAM_USER_ID", "")
        token_key = f"INSTAGRAM_ACCESS_TOKEN{suffix}"
        access_token = env_vars.get(token_key)
        
        if access_token:
            seed_account(user_id, access_token)
        else:
            print(f"Skipping {user_key}: No corresponding {token_key} found.")
