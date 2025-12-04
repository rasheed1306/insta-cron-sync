import os
import time
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
INSTAGRAM_APP_ID = os.getenv("INSTAGRAM_APP_ID")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing required environment variables: SUPABASE_URL or SUPABASE_KEY")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Global request counter for rate limiting
total_requests_this_run = 0
MAX_REQUESTS_ALLOWED = 150

def get_instagram_api_url(endpoint):
    return f"https://graph.instagram.com/v24.0/{endpoint}"

def get_facebook_api_url(endpoint):
    return f"https://graph.facebook.com/{endpoint}"

def refresh_token(account):
    """
    Checks if the account's token needs refreshing and refreshes it if necessary.
    """
    global total_requests_this_run
    
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
        
        if total_requests_this_run >= MAX_REQUESTS_ALLOWED:
            print("Rate limit reached, skipping refresh.")
            return False

        params = {
            'grant_type': 'fb_exchange_token',
            'client_id': INSTAGRAM_APP_ID,
            'client_secret': INSTAGRAM_APP_SECRET,
            'fb_exchange_token': access_token
        }
        
        try:
            response = requests.get(get_facebook_api_url("oauth/access_token"), params=params)
            total_requests_this_run += 1
            
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

def fetch_new_posts(account):
    """
    Fetches new posts for the account and inserts them into the database.
    """
    global total_requests_this_run
    
    ig_user_id = account['ig_user_id']
    access_token = account['access_token']
    last_synced_at_str = account.get('last_synced_at')
    
    last_synced_at = None
    if last_synced_at_str:
        try:
            last_synced_at = datetime.fromisoformat(last_synced_at_str.replace('Z', '+00:00'))
            if last_synced_at.tzinfo is None:
                last_synced_at = last_synced_at.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    print(f"Fetching posts for {account['account_name']}...")
    
    url = get_instagram_api_url(f"{ig_user_id}/media")
    params = {
        'fields': 'id,caption,media_type,media_url,permalink,timestamp',
        'limit': 50,
        'access_token': access_token
    }
    
    newest_post_timestamp = None
    posts_inserted = 0
    
    while url:
        if total_requests_this_run >= MAX_REQUESTS_ALLOWED:
            print("Rate limit reached, stopping fetch.")
            break
            
        try:
            response = requests.get(url, params=params if 'access_token' not in url else None)
            total_requests_this_run += 1
            
            if response.status_code != 200:
                print(f"Error fetching posts: {response.text}")
                break
                
            data = response.json()
            posts = data.get('data', [])
            
            if not posts:
                break
                
            for post in posts:
                post_timestamp_str = post['timestamp']
                post_timestamp = datetime.fromisoformat(post_timestamp_str.replace('Z', '+00:00'))
                if post_timestamp.tzinfo is None:
                    post_timestamp = post_timestamp.replace(tzinfo=timezone.utc)
                
                # Track newest timestamp seen in this run
                if newest_post_timestamp is None:
                    newest_post_timestamp = post_timestamp
                elif post_timestamp > newest_post_timestamp:
                    newest_post_timestamp = post_timestamp
                
                # Stop if we reach posts older than last sync
                if last_synced_at and post_timestamp <= last_synced_at:
                    print("Reached previously synced posts.")
                    url = None # Stop pagination
                    break
                
                # Check for duplicates (media_id)
                # Ideally we rely on DB unique constraint, but we can check or just try insert
                # Supabase upsert or insert with ignore duplicates is better
                
                post_record = {
                    'media_id': post['id'],
                    'ig_user_id': ig_user_id,
                    'caption': post.get('caption'),
                    'media_url': post.get('media_url') or post.get('permalink'), # Fallback to permalink if media_url missing
                    'timestamp': post_timestamp.isoformat(),
                    'created_at': datetime.now(timezone.utc).isoformat()
                }
                
                try:
                    # Using upsert with ignoreDuplicates=True equivalent (on_conflict='do nothing' logic)
                    # Supabase-py upsert:
                    supabase.table('instagram_posts').upsert(post_record, on_conflict='media_id').execute()
                    posts_inserted += 1
                except Exception as e:
                    print(f"Error inserting post {post['id']}: {e}")

            # Pagination
            paging = data.get('paging', {})
            url = paging.get('next')
            
            # If we stopped in the loop, url is already None
            
        except Exception as e:
            print(f"Exception during fetch: {e}")
            break
            
    # Update last_synced_at if we found new posts
    if newest_post_timestamp:
        # If last_synced_at was None, or we found newer posts
        if last_synced_at is None or newest_post_timestamp > last_synced_at:
            supabase.table('instagram_accounts').update({
                'last_synced_at': newest_post_timestamp.isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat()
            }).eq('id', account['id']).execute()
            
    print(f"Inserted {posts_inserted} new posts for {account['account_name']}.")

def run_batch():
    """
    Orchestrates the sync process for all accounts.
    """
    global total_requests_this_run
    total_requests_this_run = 0
    
    print(f"Starting batch run at {datetime.now()}")
    
    # Fetch accounts sorted by priority and last_synced_at
    # Note: Supabase-py order syntax might vary, using simple select for now
    response = supabase.table('instagram_accounts').select("*").order('priority', desc=False).order('last_synced_at', desc=False).execute()
    accounts = response.data
    
    print(f"Found {len(accounts)} accounts to process.")
    
    for account in accounts:
        if total_requests_this_run >= MAX_REQUESTS_ALLOWED:
            print("Max requests allowed reached for this batch. Stopping.")
            break
            
        try:
            # 1. Refresh Token
            refresh_token(account)
            
            # Check limit again
            if total_requests_this_run >= MAX_REQUESTS_ALLOWED:
                break
                
            # 2. Fetch New Posts
            fetch_new_posts(account)
            
            # Sleep to be nice to API
            time.sleep(2)
            
        except Exception as e:
            print(f"Error processing account {account['account_name'] if isinstance(account, dict) and 'account_name' in account else 'Unknown'}: {e}")
            continue
            
    print(f"Batch run completed. Total requests: {total_requests_this_run}")

def seed_initial_account():
    """
    Helper to seed the initial account from .env if it doesn't exist.
    """
    ig_user_id = os.getenv("INSTAGRAM_USER_ID")
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    
    if not ig_user_id or not access_token:
        print("Missing initial account details in .env, skipping seed.")
        return

    # Check if exists
    res = supabase.table('instagram_accounts').select("*").eq('ig_user_id', ig_user_id).execute()
    if not res.data:
        print("Seeding initial account from .env...")
        # Calculate a default expiry (e.g. 60 days from now) since we don't know when it was created
        expires_at = datetime.now(timezone.utc) + timedelta(days=60)        
        account_data = {
            'ig_user_id': ig_user_id,
            'account_name': 'Initial Account', # Placeholder name
            'access_token': access_token,
            'token_expires_at': expires_at.isoformat(),
            'priority': 1
        }
        supabase.table('instagram_accounts').insert(account_data).execute()
        print("Initial account seeded.")
    else:
        print("Initial account already exists.")

if __name__ == "__main__":
    # Optional: Seed account for testing
    seed_initial_account()
    
    # Run the batch
    run_batch()
