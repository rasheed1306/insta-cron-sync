import time
from datetime import datetime
from config import supabase
from globals import RequestContext
from auth import refresh_token
from ingest import fetch_new_posts
from seed import seed_initial_account

def run_batch():
    """
    Orchestrates the sync process for all accounts.
    """
    
    print(f"Starting batch run at {datetime.now()}")
    
    # Fetch accounts sorted by priority and last_synced_at
    response = supabase.table('instagram_accounts').select("*").order('priority', desc=False).order('last_synced_at', desc=False).execute()
    accounts = response.data
    
    print(f"Found {len(accounts)} accounts to process.")
    
    for account in accounts:
        if RequestContext.total_requests_this_run >= RequestContext.MAX_REQUESTS_ALLOWED:
            print("Max requests allowed reached for this batch. Stopping.")
            break
            
        try:
            # 1. Refresh Token
            refresh_token(account)
            
            # Check limit again
            if RequestContext.total_requests_this_run >= RequestContext.MAX_REQUESTS_ALLOWED:
                break
                
            # 2. Fetch New Posts
            fetch_new_posts(account)
            
            # Sleep to be nice to API
            time.sleep(2)
            
        except Exception as e:
            print(f"Error processing account {account['account_name'] if isinstance(account, dict) and 'account_name' in account else 'Unknown'}: {e}")
            continue
            
    print(f"Batch run completed. Total requests: {RequestContext.total_requests_this_run}")

if __name__ == "__main__":
    # Reset counter at start of run
    RequestContext.total_requests_this_run = 0
    
    seed_initial_account()
    
    # Run the batch
    run_batch()
