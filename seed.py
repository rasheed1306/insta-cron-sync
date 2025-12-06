import requests
from datetime import datetime, timedelta, timezone
from config import supabase, INSTAGRAM_USER_ID, INSTAGRAM_ACCESS_TOKEN
from utils import get_instagram_api_url
from auth import refresh_token
from globals import RequestContext

def seed_initial_account():
    """
    Helper to seed the initial account from .env if it doesn't exist.
    """
    ig_user_id = INSTAGRAM_USER_ID
    access_token = INSTAGRAM_ACCESS_TOKEN
    
    if not ig_user_id or not access_token:
        print("Missing initial account details in .env, skipping seed.")
        return

    # Check if exists
    res = supabase.table('instagram_accounts').select("*").eq('ig_user_id', ig_user_id).execute()
    if not res.data:
        print("Seeding initial account from .env...")

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

        print("Initial account seeded.")
    else:
        print("Initial account already exists.")
