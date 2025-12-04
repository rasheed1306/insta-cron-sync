# Connect3 Instagram Data Ingestion Service: Detailed Implementation Guide

## Executive Summary

This document outlines the complete architecture for Connect3's autonomous Instagram event scraping system. The service will run on a schedule (cron via Vercel), continuously fetch posts from multiple club Instagram Business accounts, intelligently refresh authentication tokens, and store normalized event data in a SQL database. This foundation enables Connect3 to power real-time club discovery and personalized event recommendations for students.

---

## 1. Project Goals & Context

### Primary Objective
Extract upcoming events from Instagram Business accounts (10+ clubs initially, scaling to 50–100+) without manual intervention, while respecting rate limits and ensuring data freshness.

### Connect3 Product Context
Connect3 is a campus life platform that helps students discover clubs and events. Currently, clubs post events on Instagram without a centralized discovery mechanism. This ingestion service bridges that gap by:
- Automatically scraping event posts from club Instagram accounts.
- Normalizing post data into structured events (title, time, location).
- Feeding that data to Connect3's recommendation engine (via embedding similarity).
- Keeping the data fresh via scheduled syncs.

### Long-Term Vision
- Phase 1 (Current): Python ingestion service + manual account linking → SQL database.
- Phase 2: Integrate into Next.js backend as a scheduled API route (`/api/cron/instagram`).
- Phase 3: Club admin dashboard in Connect3 to authorize their Instagram account and track sync health.

---

## 2. Database Schema

### Table 1: `instagram_accounts`

**Purpose:** Store metadata and authentication tokens for each connected Instagram Business account.

**Schema:**

```
Column Name          | Type              | Constraints              | Description
---------------------|-------------------|--------------------------|-----------------------------------------------------
id                   | INTEGER           | PRIMARY KEY, AUTO_INC    | Unique identifier for this row
ig_user_id           | TEXT              | UNIQUE, NOT NULL         | Instagram's numeric user ID (from Graph API /me)
account_name         | TEXT              | NOT NULL                 | Human-readable name (e.g., "DSCubed", "UoM Robotics")
access_token         | TEXT              | NOT NULL                 | Long-lived access token (expires in ~60 days)
token_expires_at     | TIMESTAMP         | NOT NULL                 | Timestamp when current token expires
last_synced_at       | TIMESTAMP         | NULLABLE                 | Last timestamp we successfully pulled posts (null = first run)
priority             | INTEGER           | DEFAULT 2                | Priority for scheduling (1=high, 2=normal, 3=low)
created_at           | TIMESTAMP         | NOT NULL, DEFAULT NOW    | When this account was added to our system
updated_at           | TIMESTAMP         | NOT NULL, DEFAULT NOW    | Last update timestamp
```

**Index Recommendations:**
- Index on `ig_user_id` (for fast lookups during sync).
- Index on `priority, last_synced_at` (for ordered batch queries).

**Why This Design:**
- `ig_user_id` is the unique identifier from Instagram, not a foreign key to an internal clubs table, keeping the ingestion layer independent.
- `account_name` is just text; later, you can optionally link to a `clubs` table with a new `club_id` column.
- Token management is centralized: all refresh logic checks `token_expires_at * 0.9`.
- `priority` enables fair scheduling if you have more accounts than requests per run.

---

### Table 2: `instagram_posts`

**Purpose:** Store normalized post data scraped from Instagram, serving as the raw event candidate source.

**Schema:**

```
Column Name          | Type              | Constraints              | Description
---------------------|-------------------|--------------------------|-----------------------------------------------------
id                   | INTEGER           | PRIMARY KEY, AUTO_INC    | Unique identifier for this row
media_id             | TEXT              | UNIQUE, NOT NULL         | Instagram's post ID (e.g., "17123456789")
ig_user_id           | TEXT              | NOT NULL                 | Which account posted this (foreign key concept, not enforced)
caption              | TEXT              | NULLABLE                 | Post text/description
media_url            | TEXT              | NULLABLE                 | URL to the image or video
timestamp            | TIMESTAMP         | NOT NULL                 | When Instagram timestamp published this
created_at           | TIMESTAMP         | NOT NULL, DEFAULT NOW    | When we added this record
```

**Index Recommendations:**
- Index on `media_id` (for dedup checks during insert).
- Index on `ig_user_id, timestamp` (for querying events from a specific club).
- Index on `timestamp` (for time-range queries in event detection).

**Why This Design:**
- `media_id` as UNIQUE ensures we never store the same post twice, even if sync runs multiple times.
- No foreign key to `instagram_accounts` keeps the schema flexible (you can drop and re-add accounts without cascading deletes).
- `caption` and `media_url` are the raw inputs for your event detection logic (parsing dates, keywords, etc.).
- `timestamp` is critical: it's used to stop pagination and to filter "future" vs. "past" posts for event detection.

---

## 3. Core Functions & Their Purpose

### 3.1 `refresh_token(account)`

**High-Level Purpose:**
Ensure the account's Instagram access token is always fresh and has sufficient remaining lifetime. Proactively renew tokens before they expire to avoid mid-sync failures.

**When It Runs:**
Called once per account at the start of each batch sync (before `fetch_new_posts`).

**Logic Flow:**

1. **Check Token Lifetime:**
   - Read the account's `access_token` and `token_expires_at` from the DB.
   - Calculate: `time_remaining = token_expires_at - now()`.
   - Calculate: `max_lifetime = token_expires_at - token_created_at` (approximately 60 days for long-lived tokens).
   - Check if: `time_remaining < max_lifetime * 0.1` (i.e., less than 10% of lifetime left).

2. **Decide to Refresh:**
   - If the condition above is true (token has <10% lifetime remaining), proceed to refresh.
   - Otherwise, skip the refresh call entirely (no wasted API request).

3. **Call the Refresh Endpoint:**
   - Use the Graph API endpoint: **`GET https://graph.facebook.com/oauth/access_token`**
   - Required query parameters:
     - `grant_type=fb_exchange_token`
     - `client_id={YOUR_META_APP_ID}`
     - `client_secret={YOUR_META_APP_SECRET}`
     - `fb_exchange_token={existing_access_token}`
   - The API returns a new long-lived token and its expiration timestamp.

4. **Update the Database:**
   - Store the new `access_token` in the DB.
   - Store the new `token_expires_at` (typically 60 days from the refresh call time).

5. **Error Handling:**
   - If the refresh fails (e.g., the token is completely invalid), log the error and skip to the next account. Don't crash the batch.

**Request Count Impact:** 1 request per account, only if needed (every ~6 days per account).

---

### 3.2 `fetch_new_posts(account)`

**High-Level Purpose:**
Retrieve new/recent posts from a single Instagram Business account and insert them into the database, avoiding duplicates and respecting pagination.

**When It Runs:**
Called once per account after `refresh_token()` in each batch sync.

**Logic Flow:**

1. **Prepare the Request:**
   - Extract the account's `ig_user_id`, `access_token`, and `last_synced_at` from the DB.
   - Construct the Graph API endpoint: **`GET https://graph.instagram.com/v20.0/{ig_user_id}/media`**
   - Query parameters:
     - `fields=id,caption,media_type,media_url,permalink,timestamp`
     - `limit=50` (pull 50 posts per page)
     - `access_token={access_token}`
   - Use client-side timestamp comparison to determine which posts are new (compare against `last_synced_at`).

2. **Initialize Tracking Variables:**
   - Set `newest_post_timestamp = null` (to track the first/newest post we see in this run).
   - Set `posts_inserted = 0` (to count how many posts we actually add to the DB).

3. **Paginate Through Results:**
   - Make the GET request to the Graph API.
   - For each post in the response's `data` array:
     - **Update newest_timestamp:** If `newest_post_timestamp` is still null, set it to this post's timestamp (it's the first/newest we've seen).
     - **Check for Duplicates:** Query the DB to see if `media_id` already exists in `instagram_posts`. If it does, skip this post.
     - **Check Against Last Sync:** If `last_synced_at` is not null and this post's `timestamp` is older than or equal to `last_synced_at`, stop pagination (we've reached posts we already saw).
     - **Insert the Post:** If it's new, insert the post record into `instagram_posts` with fields: `media_id`, `ig_user_id`, `caption`, `media_url`, `timestamp`, `created_at`.
     - **Increment Counter:** Increment `posts_inserted`.

4. **Handle Pagination:**
   - After processing the current page, check the response for a `paging.next` field.
   - If `paging.next` exists AND we haven't reached `last_synced_at`, fetch the next page using that URL.
   - Repeat the loop until either:
     - No `paging.next` exists (we've reached the end of posts), OR
     - We encounter posts older than `last_synced_at` (we've caught up to where we left off).

5. **Update the Account Record:**
   - After successfully inserting all new posts, update the account's `last_synced_at` to `newest_post_timestamp`.
   - This ensures the next sync only pulls posts newer than this run.

6. **Logging & Error Handling:**
   - Log the number of posts inserted (e.g., "Inserted 12 new posts for DSCubed").
   - If the Graph API request fails, log the error and return early (the account's `last_synced_at` is not updated, so the next run will retry).

**Request Count Impact:**
- 1 request for the first page (always made).
- 1 request per additional page (if pagination is followed; optional for MVP, defer to future).
- Total: 1–5 requests per account depending on how many new posts there are.

**Request Counting:**
- Increment a global request counter each time you make a Graph API call (GET to `/media`).
- Check if `total_requests_made_this_run < 150` before making each request.
- If limit is about to be exceeded, stop processing remaining accounts and log a summary.

---

### 3.3 `run_batch()`

**High-Level Purpose:**
Orchestrate the complete sync cycle: load all accounts, refresh tokens, fetch new posts, manage rate limiting, and handle errors gracefully.

**When It Runs:**
Triggered by a cron job every 15–60 minutes (recommendation: every 30 minutes as a good balance).

**Logic Flow:**

1. **Initialize:**
   - Set `total_requests_this_run = 0` (for rate limit tracking).
   - Set `max_requests_allowed = 150`.
   - Load all accounts from the DB, optionally ordered by `priority ASC, last_synced_at ASC` (process high-priority and stale accounts first).

2. **For Each Account:**
   - **Try Block Start:**
     - Call `refresh_token(account)`:
       - This checks if the token needs renewal and refreshes if necessary.
       - If refresh happens, increment `total_requests_this_run` by 1.
     - Check if `total_requests_this_run >= max_requests_allowed`:
       - If yes, log a warning and skip to the next account (or break the loop).
     - Call `fetch_new_posts(account)`:
       - This fetches new posts and inserts them.
       - Increment `total_requests_this_run` by the number of API calls made (usually 1 per account).
     - Check again if `total_requests_this_run >= max_requests_allowed`:
       - If yes, skip remaining accounts for this run.
     - Sleep for 2 seconds (gives Instagram's servers breathing room and helps avoid triggering additional rate limit checks).
   - **Catch Block:**
     - If any error occurs (token invalid, network error, etc.), log the error with the account name and continue to the next account.
     - This ensures one broken account doesn't crash the entire batch.

3. **Post-Batch Summary:**
   - Log final statistics (e.g., "Processed 10 accounts, made 25 API requests, inserted 156 new posts").
   - If `total_requests_this_run >= max_requests_allowed`, log a note for the admin to monitor upcoming runs.

**Request Counting System:**

```
Overview:
  At the start of run_batch():
    total_requests_this_run = 0
    max_requests_allowed = 150

  For each account:
    Before refresh_token():
      if total_requests_this_run >= max_requests_allowed:
        log warning and skip to next account

    During refresh_token():
      if token_needs_refresh:
        make 1 Graph API call (increment total_requests_this_run)

    Before fetch_new_posts():
      if total_requests_this_run >= max_requests_allowed:
        log warning and skip to next account

    During fetch_new_posts():
      for each page of results:
        if total_requests_this_run >= max_requests_allowed:
          break pagination loop
          skip remaining accounts
        make 1 Graph API call (increment total_requests_this_run)
        process posts

    Sleep 2 seconds

  At the end:
    log total_requests_this_run and summary stats
```

**Rationale for 150 Request Limit:**
- Instagram Graph API allows ~200 calls/hour per account for app-level rate limits.
- With 10 accounts and 1 sync run per hour, that's 10 requests minimum if each account uses 1 call.
- 150 provides significant headroom (15x safe margin) while still allowing multiple pages of pagination if an account has been offline for a while.
- If you hit 150 regularly, it's a signal to either increase sync frequency or investigate why accounts need more pages.

---

## 4. Instagram Graph API Endpoints Reference

### 4.1 Get Instagram User ID (Initial Setup)

**Endpoint:** `GET https://graph.instagram.com/me?fields=id,username`

**Parameters:**
- `access_token` (query param): Long-lived user token.

**Response Example:**
```json
{
  "id": "17123456789",
  "username": "dscubed.unimelb"
}
```

**Usage:** Called once when setting up a new account, to get the `ig_user_id` for storage.

---

### 4.2 Fetch Media (Posts)

**Endpoint:** `GET https://graph.instagram.com/v20.0/{ig_user_id}/media`

**Parameters:**
- `fields=id,caption,media_type,media_url,permalink,timestamp` (query param)
- `limit=50` (query param): Number of posts to return per page.
- `access_token` (query param): Long-lived access token for this account.

**Response Example:**
```json
{
  "data": [
    {
      "id": "18140003908439651",
      "caption": "Thank you to everyone who joined us...",
      "media_type": "CAROUSEL_ALBUM",
      "media_url": "https://www.instagram.com/p/DPGNhJSkQvk/",
      "permalink": "https://www.instagram.com/p/DPGNhJSkQvk/",
      "timestamp": "2025-09-27T07:19:10+0000"
    }
  ],
  "paging": {
    "cursors": {
      "before": "...",
      "after": "..."
    },
    "next": "https://graph.instagram.com/v20.0/17123456789/media?after=XYZ&fields=..."
  }
}
```

**Usage:** Called by `fetch_new_posts()` to retrieve posts. Follow `paging.next` for additional pages.

---

### 4.3 Refresh Access Token

**Endpoint:** `GET https://graph.facebook.com/oauth/access_token`

**Parameters:**
- `grant_type=fb_exchange_token` (query param)
- `client_id={YOUR_META_APP_ID}` (query param)
- `client_secret={YOUR_META_APP_SECRET}` (query param)
- `fb_exchange_token={current_access_token}` (query param)

**Response Example:**
```json
{
  "access_token": "NEW_LONG_LIVED_TOKEN...",
  "token_type": "bearer",
  "expires_in": 5184000
}
```

**Usage:** Called by `refresh_token()` when token has <10% lifetime remaining. `expires_in` is in seconds (5184000 = 60 days).

---

## 5. Rate Limiting Strategy

### Overview

Instagram and Meta's Graph API enforce rate limits at multiple levels:
- Per‑account limits (~200 calls/hour per IG account per app).
- App-level aggregate limits.

### Implementation

**Request Counting:**
- Maintain a counter `total_requests_this_run` at the start of `run_batch()`.
- Increment it by 1 each time you make any GET request to Graph API endpoints.
- Before making a request, check: `if total_requests_this_run >= 150: skip_this_request`.

**Spacing Requests:**
- Add a `time.sleep(2)` between accounts in `run_batch()` to avoid spikes.
- This spreads requests over time and appears as more human-like traffic to Instagram.

**Pagination Limits:**
- In `fetch_new_posts()`, limit pagination to a maximum of 3–5 pages per account per run (optional for MVP).
- This further reduces per-run request count and distributes historical backfill across multiple cron runs.

**Monitoring:**
- Log `total_requests_this_run` at the end of each batch.
- If you consistently hit 150, consider:
  - Increasing the interval between cron runs (e.g., every 60 minutes instead of 30).
  - Lowering the page limit in pagination.
  - Archiving inactive accounts (low priority).

**Error Codes to Watch:**
- **429 (Too Many Requests):** You hit a rate limit. Log and back off.
- **400 (Bad Request):** Usually an invalid parameter or expired token. Log and skip account.
- **401 (Unauthorized):** Token is invalid. Attempt refresh or mark account as needing re-authorization.

---

## 6. Deployment & Scheduling

### Option 1: Vercel Cron (Recommended for Next.js Apps)

**Setup:**
1. Create a Next.js API route at `app/api/cron/instagram/route.ts`.
2. Inside the route handler, call your `run_batch()` logic (import the Python function via a process call, or rewrite in Node.js/TypeScript).
3. Create a `vercel.json` config file:
   ```json
   {
     "crons": [
       {
         "path": "/api/cron/instagram",
         "schedule": "*/30 * * * *"
       }
     ]
   }
   ```
4. Deploy to Vercel. The cron will automatically trigger every 30 minutes.

**Advantages:**
- Integrated with your Next.js app (same project, same secrets management).
- Automatic retries and logging via Vercel.
- No additional infrastructure.

**Cron Schedule Examples:**
- `*/30 * * * *`: Every 30 minutes.
- `0 * * * *`: Every hour at the top of the hour.
- `0 9 * * *`: Daily at 9 AM UTC.

---

### Option 2: GitHub Actions (Standalone Python Service)

**Setup:**
1. Commit your Python ingestion script to a GitHub repo.
2. Create `.github/workflows/instagram-sync.yml`:
   ```yaml
   name: Instagram Sync
   on:
     schedule:
       - cron: "*/30 * * * *"
   
   jobs:
     sync:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - name: Run scraper
           run: python ingest_instagram.py
           env:
             DATABASE_URL: ${{ secrets.DATABASE_URL }}
             INSTAGRAM_APP_ID: ${{ secrets.INSTAGRAM_APP_ID }}
   ```
3. GitHub will run the job on schedule.

**Advantages:**
- Simple if your ingestion is pure Python.
- Free tier allows plenty of scheduled runs.

---

### Local/VPS Cron (For Development)

**Setup:**
1. SSH into your server.
2. Edit crontab: `crontab -e`.
3. Add a line: `*/30 * * * * /usr/bin/python3 /path/to/ingest_instagram.py >> /var/log/ig_ingest.log 2>&1`
4. Cron will run the script every 30 minutes.

**Advantages:**
- Full control, no third-party dependencies.
- Good for testing and development.

---

## 7. Integration with Next.js & Connect3

### Current Architecture (Phase 1)

```
Standalone Python Service (Vercel Cron or GitHub Actions)
        ↓
   Graph API (Instagram)
        ↓
   Supabase (SQL Database)
        ↓
   instagram_accounts, instagram_posts tables
```

### Phase 2: Next.js Integration

**Goal:** Move the ingestion logic into the Next.js backend so everything is in one codebase.

**Implementation:**
1. **Create API Route:** `app/api/cron/instagram/route.ts`
   - This route handler calls your ingestion functions.
   - Imports a utility module with `refresh_token()`, `fetch_new_posts()`, `run_batch()`.

2. **Utility Module:** `lib/instagram/ingest.ts`
   - Rewrite the ingestion logic in TypeScript/Node.js.
   - Use a database client (e.g., `@supabase/supabase-js` or raw SQL).
   - Keep the same function signatures and logic flow.

3. **Environment Variables:** Store in `.env.local`:
   ```
   NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_SERVICE_KEY=...
   INSTAGRAM_APP_ID=...
   INSTAGRAM_APP_SECRET=...
   ```

4. **Cron Configuration:** Add to `vercel.json`:
   ```json
   {
     "crons": [
       {
         "path": "/api/cron/instagram",
         "schedule": "*/30 * * * *"
       }
     ]
   }
   ```

5. **Response Handling:** Return a JSON response with status and summary:
   ```json
   {
     "success": true,
     "accounts_processed": 10,
     "posts_inserted": 156,
     "requests_made": 25,
     "timestamp": "2025-12-05T00:30:00Z"
   }
   ```

### Phase 3: Club Admin Dashboard

**Goal:** Allow club admins to authorize their Instagram account and monitor sync health.

**Features:**
1. **OAuth Flow for Instagram:**
   - Club admin clicks "Connect Instagram Account" in Connect3 dashboard.
   - Redirects to Meta OAuth, club admin logs in and approves.
   - Next.js backend receives an authorization code, exchanges it for a long-lived token, and stores in `instagram_accounts`.

2. **Sync Status Page:**
   - Show last sync time, number of posts synced, any errors.
   - Display a timeline of detected events from that account.

3. **Manual Sync Trigger:**
   - Button to immediately run sync for a single account (useful for testing).

---

## 8. Event Detection & Next Steps

### Current Scope

This document covers **data ingestion** only: fetching raw posts from Instagram and storing them in a SQL database. It does not cover event detection or recommendations.

### Next Phase: Event Detection

Once posts are in the database, a separate service or module will:
1. Parse captions for event keywords ("event", "O-Week", specific dates/times).
2. Extract dates and times using NLP or regex.
3. Create structured `events` records with title, time, location, club, etc.
4. Mark events as "confirmed" (from Instagram's event feature) or "inferred" (from post parsing).

### Following Phase: Recommendations

The recommendation engine will:
1. Embed event data (club name, event title, keywords) into vectors.
2. Embed user preferences into vectors.
3. Use cosine similarity to recommend events to users.

---

## 9. Operational Checklist

### Before Going Live

- [ ] Create `instagram_accounts` and `instagram_posts` tables in Supabase.
- [ ] Add indexes on `ig_user_id`, `media_id`, `timestamp`.
- [ ] Set up environment variables (App ID, Secret, Database URL).
- [ ] Test `refresh_token()` with a real token to ensure refresh endpoint works.
- [ ] Test `fetch_new_posts()` with a dummy account to ensure pagination works.
- [ ] Test `run_batch()` locally with 1–2 accounts.
- [ ] Implement error logging (e.g., to Logsnag or a log table).
- [ ] Deploy cron job (Vercel, GitHub Actions, or local crontab).
- [ ] Monitor first few runs manually; check logs and database records.

### Ongoing Monitoring

- [ ] Track `total_requests_this_run` over time; ensure it stays <150.
- [ ] Monitor post insertion rates; ensure growth is expected.
- [ ] Check for token expiration errors; if frequent, adjust refresh threshold.
- [ ] Log API response times; if slow, consider paginating less or running less frequently.

---

## 10. Troubleshooting Guide

| Issue | Likely Cause | Solution |
|-------|--------------|----------|
| "Insufficient developer role" | Instagram tester not accepted the invite. | Visit Instagram settings → Accounts → Tester Invites and accept. |
| Token refresh fails with 400 | Token is already expired or invalid. | Delete the account and re-authorize from scratch. |
| No posts being fetched | `last_synced_at` is in the future (clock skew). | Reset `last_synced_at` to NULL in the database. |
| Request count exceeds 150 | Too many accounts or pagination is too aggressive. | Increase cron interval (run less frequently) or reduce page limit. |
| Duplicate posts in DB | Dedup logic failed or `media_id` index is corrupt. | Query for duplicates and delete manually; rebuild index. |

---

## 11. Code Structure Recommendation

If implementing in a Next.js project, organize as:

```
app/
  api/
    cron/
      instagram/
        route.ts          ← Entry point for cron
lib/
  instagram/
    ingest.ts           ← Ingestion functions (refresh_token, fetch_new_posts, run_batch)
    types.ts            ← TypeScript types (Account, Post, etc.)
    db.ts               ← Database helpers (queries, inserts)
    logger.ts           ← Logging utility
.env.local              ← Secrets (app ID, secret, DB URL)
```

---

## 12. Summary

This ingestion service is the foundation for Connect3's event discovery layer. By continuously pulling data from Instagram, refreshing tokens proactively, and respecting rate limits, you build a reliable, low-maintenance data pipeline that feeds fresh event information into your platform.

**Key Takeaways:**
- **Database:** Two simple tables (`instagram_accounts`, `instagram_posts`) store all needed data.
- **Functions:** Three focused functions handle token refresh, post fetching, and orchestration.
- **Rate Limiting:** Explicit request counting and 150-call-per-run limit ensures compliance.
- **Deployment:** Vercel Cron integrates cleanly with your Next.js app; GitHub Actions works for standalone Python.
- **Scalability:** The design scales from 10 to 100+ accounts with no major changes, just longer sync times.
- **Evolution:** Future phases add club dashboards and AI-powered recommendations.

---

**Document Version:** 1.0  
**Last Updated:** December 5, 2025  
**Status:** Ready for Development