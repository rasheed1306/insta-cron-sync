from fastapi import FastAPI, BackgroundTasks
from main import run_batch, seed_initial_account
from globals import RequestContext

app = FastAPI()

def run_sync_task():
    """
    Wrapper to run the sync task in background.
    """
    print("Triggering background sync task...")
    # Reset counter at start of run
    RequestContext.total_requests_this_run = 0
    
    # Ensure initial seed (optional, but good for consistency)
    seed_initial_account()
    
    # Run the batch
    run_batch()

@app.get("/")
def read_root():
    return {"status": "Connect3 Instagram Ingestion Service is running"}

@app.post("/run-task")
def trigger_task(background_tasks: BackgroundTasks):
    """
    Endpoint to trigger the batch sync process.
    Returns immediately while the task runs in the background.
    """
    background_tasks.add_task(run_sync_task)
    return {"status": "started", "message": "Batch sync task has been triggered in the background"}
