# Connect3 Instagram Ingestion Service

Autonomous service to scrape event posts from club Instagram Business accounts, normalize data, and store it in Supabase.

## 🚀 Quick Start

### Prerequisites
- Python 3.13+
- `uv` package manager
- Supabase project
- Meta App (Instagram Graph API)

### Installation
```bash
uv sync
```

### Configuration
Create a `.env` file:
```env
SUPABASE_URL=...
SUPABASE_KEY=... (Service Role Key required for ingestion)
INSTAGRAM_APP_ID=...
INSTAGRAM_APP_SECRET=...
INSTAGRAM_USER_ID=...
INSTAGRAM_ACCESS_TOKEN=...
# Add more users dynamically: INSTAGRAM_USER_ID_2, INSTAGRAM_ACCESS_TOKEN_2, etc.
```

## 🛠 Usage

### Manual Batch Run
Run the ingestion script directly:
```bash
uv run main.py
```

### Start API Server (for Cron)
Starts a FastAPI server exposing a trigger endpoint.
```bash
uv run uvicorn server:app --reload
```

**Trigger Task:**
```bash
curl -X POST http://127.0.0.1:8000/run-task
```

## 🏗 Architecture

- **Core Logic:** Modularized Python scripts (`ingest.py`, `auth.py`, `seed.py`).
- **Server:** FastAPI wrapper (`server.py`) handling background tasks to prevent timeouts.
- **Database:** Supabase (PostgreSQL).
  - `instagram_accounts`: Stores tokens & metadata. Linked to `profiles` via `profile_id`.
  - `instagram_posts`: Stores raw post data.
- **Security:** Row Level Security (RLS) enabled. Ingestion uses Service Role to bypass.

## ⚙️ Key Features

- **Smart Rate Limiting:** Max **75 requests/run** to stay safely within Graph API limits (200/hr).
- **Auto-Token Refresh:** Proactively refreshes long-lived tokens < 6 days from expiry.
- **Incremental Sync:** Only fetches posts newer than `last_synced_at`.
- **Dynamic Seeding:** Automatically detects and seeds accounts from `.env` vars (`INSTAGRAM_USER_ID*`).

## 🗺 Roadmap

- [x] Phase 1: Python Ingestion Service (Current)
- [ ] Phase 2: Migrate to Vercel Cron / Next.js API Routes
- [ ] Phase 3: Club Admin Dashboard for OAuth linking

*See `instagram-ingest-guide updated.md` for the detailed architectural vision.*
