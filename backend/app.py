"""
app.py
======
FastAPI application entrypoint.

Responsibilities
-----------------
- Initialize logging as the very first action.
- Initialize the database (create tables if they don't exist).
- Start/stop the background scheduler (approval-expiry sweeps) alongside
  the application lifespan.
- Mount the webhook router (webhook.py) and health router (health.py).
- Provide a root endpoint for a quick sanity check.

Run with:
    uvicorn app:app --host 0.0.0.0 --port 8000

(host/port are also read from APP_HOST / APP_PORT in .env; the values in
the uvicorn command only matter if you invoke uvicorn directly instead of
via `python app.py`.)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configure logging before importing anything else that might log at
# import time, so no log lines are lost before handlers are attached.
from logger import configure_logging, get_logger

configure_logging()
logger = get_logger("app")

from admin_api import router as admin_api_router  # noqa: E402
from database import init_db  # noqa: E402
from health import router as health_router  # noqa: E402
from scheduler import shutdown_scheduler, start_scheduler  # noqa: E402
from webhook import router as webhook_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting WhatsApp AI Assistant...")
    init_db()
    start_scheduler()
    logger.info("Startup complete.")
    yield
    logger.info("Shutting down WhatsApp AI Assistant...")
    shutdown_scheduler()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="WhatsApp AI Assistant",
    description="Production WhatsApp AI Assistant powered by OpenRouter (OpenAI OSS 120B).",
    version="1.0.0",
    lifespan=lifespan,
)

import os

# CORS: only needed because the dashboard frontend (dashboard/) runs on its
# own dev server / origin and calls this API directly. Origins are
# configurable via DASHBOARD_ORIGINS in .env (comma-separated), defaulting
# to the Vite dev server. Every /admin/* route is still protected by the
# X-Admin-Key header check in admin_api.py regardless of origin.
_dashboard_origins = [
    o.strip() for o in os.getenv("DASHBOARD_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_dashboard_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)
app.include_router(health_router)
app.include_router(admin_api_router)


@app.get("/")
async def root() -> dict:
    """Simple root endpoint confirming the service is running."""
    return {"service": "WhatsApp AI Assistant", "status": "running"}


if __name__ == "__main__":
    import uvicorn

    from config import settings

    uvicorn.run(
        "app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
    )
