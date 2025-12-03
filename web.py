# web.py
import os
from fastapi import FastAPI
import threading
import uvicorn

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Bot web service running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

def run_web():
    """Start uvicorn in a background thread. PORT is from env for Render."""
    port = int(os.environ.get("PORT", 10000))
    # uvicorn.run is blocking; start it in a thread.
    def _run():
        uvicorn.run("web:app", host="0.0.0.0", port=port, log_level="info")
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread

if __name__ == "__main__":
    # local run for dev
    run_web()
    import time
    while True:
        time.sleep(3600)
