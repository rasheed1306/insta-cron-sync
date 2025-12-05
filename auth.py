import requests
from datetime import datetime, timedelta, timezone
from config import supabase, INSTAGRAM_APP_ID, INSTAGRAM_APP_SECRET
from utils import get_facebook_api_url
from globals import RequestContext

def refresh_token(account):
    """
    Checks if the account's token needs refreshing and refreshes it if necessary.
    """
    access_token = account['access_token']
    token_expires_at_str = account['token_expires_at']
    
    # Parse timestamp (handling potential format differences)
    try:
        token_expires_at = datetime.fromisoformat(token_expires_at_str.replace('Z', '+00:00'))
        if token_expires_at.tzinfo is None:
            token_expires_at = token_expires_at.replace(tzinfo=timezone.utc)
    except ValueError:
        # Fallback if format is different
        token_expires_at = datetime.strptime(token_expires_at_str, "%Y-%m-%dT%H:%M:%S.%f%z")
        if token_expires_at.tzinfo is None:
            token_expires_at = token_expires_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    
    # Calculate remaining lifetime
    time_remaining = token_expires_at - now
    
    # Assuming max lifetime is ~60 days (5184000 seconds)
    max_lifetime = timedelta(days=60)
    
    # Refresh if less than 10% of lifetime remains (approx 6 days)
    if time_remaining < max_lifetime * 0.1:
        print(f"Refreshing token for account {account['account_name']}...")
        
        if RequestContext.total_requests_this_run >= RequestContext.MAX_REQUESTS_ALLOWED:
            print("Rate limit reached, skipping refresh.")
            return False

        params = {
            'grant_type': 'ig_refresh_token',
            'access_token': access_token
        }

        try:
            response = requests.get("https://graph.instagram.com/refresh_access_token", params=params)
            RequestContext.total_requests_this_run += 1
            
            if response.status_code == 200:
                data = response.json()
                new_access_token = data['access_token']
                expires_in_seconds = data.get('expires_in', 5184000) # Default to 60 days
                new_expires_at = now + timedelta(seconds=expires_in_seconds)
                
                # Update DB
                supabase.table('instagram_accounts').update({
                    'access_token': new_access_token,
                    'token_expires_at': new_expires_at.isoformat(),
                    'updated_at': now.isoformat()
                }).eq('id', account['id']).execute()
                
                print(f"Token refreshed successfully for {account['account_name']}.")
                return True
            else:
                print(f"Failed to refresh token: {response.text}")
                return False
        except Exception as e:
            print(f"Error refreshing token: {e}")
            return False
    else:
        # print(f"Token for {account['account_name']} is still valid.")
        return True
