import requests
from datetime import datetime, timezone
from config import supabase
from utils import get_instagram_api_url
from globals import RequestContext

def fetch_new_posts(account):
    """
    Fetches new posts for the account and inserts them into the database.
    """
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
        if RequestContext.total_requests_this_run >= RequestContext.MAX_REQUESTS_ALLOWED:
            print("Rate limit reached, stopping fetch.")
            break
            
        try:
            response = requests.get(url, params=params if 'access_token' not in url else None)
            RequestContext.total_requests_this_run += 1
            
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
                    supabase.table('instagram_posts').upsert(post_record, on_conflict='media_id').execute()
                    posts_inserted += 1
                except Exception as e:
                    print(f"Error inserting post {post['id']}: {e}")

            # Pagination
            paging = data.get('paging', {})
            url = paging.get('next')
            
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

def refresh_post_media_url(media_id, access_token):
    """
    Refreshes the media_url for a specific post by fetching it again from Instagram.
    Useful when the stored media_url expires (returns 403).
    """
    print(f"Refreshing media URL for post {media_id}...")
    
    if RequestContext.total_requests_this_run >= RequestContext.MAX_REQUESTS_ALLOWED:
        print("Rate limit reached, cannot refresh media URL.")
        return False

    url = get_instagram_api_url(f"{media_id}")
    params = {
        'fields': 'media_url,permalink',
        'access_token': access_token
    }

    try:
        response = requests.get(url, params=params)
        RequestContext.total_requests_this_run += 1

        if response.status_code == 200:
            data = response.json()
            new_media_url = data.get('media_url') or data.get('permalink')
            
            if new_media_url:
                # Update DB
                supabase.table('instagram_posts').update({
                    'media_url': new_media_url
                }).eq('media_id', media_id).execute()
                
                print(f"Successfully refreshed media URL for post {media_id}.")
                return True
            else:
                print(f"No media_url found for post {media_id}.")
                return False
        else:
            print(f"Failed to refresh media URL: {response.text}")
            return False
    except Exception as e:
        print(f"Error refreshing media URL: {e}")
        return False
