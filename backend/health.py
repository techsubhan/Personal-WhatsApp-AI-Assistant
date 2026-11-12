"""
health.py
=========
Production health-check endpoints.

    GET /health  - lightweight liveness probe (process is up, DB reachable)
    GET /status  - detailed status of every external dependency
                   (database, OpenRouter, WhatsApp Cloud API)

These are plain, unauthenticated read-only endpoints intended for use by
uptime monitors / container orchestrators / load balancers. They never
reveal secrets - only boolean reachability and non-sensitive metadata.
"""

from __future__ import annotations

import time

import requests
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from logger import get_logger
from whatsapp import whatsapp_client

logger = get_logger("health")

router = APIRouter(tags=["health"])

_APP_START_TIME = time.monotonic()


def _check_database(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        logger.error("Database health check failed.", exc_info=True)
        return False


def _check_openrouter() -> bool:
    """
    Lightweight OpenRouter reachability check. Calls the `/models` listing
    endpoint (cheap, no token cost) rather than issuing a real chat
    completion, purely to confirm the API key and network path are valid.
    """
    if not settings.openrouter_api_key:
        return False
    try:
        response = requests.get(
            f"{settings.openrouter_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            timeout=10,
        )
        return response.status_code == 200
    except requests.RequestException:
        logger.error("OpenRouter health check failed.", exc_info=True)
        return False


@router.get("/health")
async def health() -> dict:
    """Lightweight liveness endpoint - just confirms the process is running."""
    return {
        "status": "ok",
        "uptime_seconds": int(time.monotonic() - _APP_START_TIME),
    }


@router.get("/status")
async def full_status(db: Session = Depends(get_db)) -> dict:
    """Detailed dependency status for monitoring/dashboards."""
    database_ok = _check_database(db)
    openrouter_ok = _check_openrouter()
    whatsapp_ok = whatsapp_client.check_status()

    overall_ok = database_ok and openrouter_ok and whatsapp_ok

    return {
        "application": {
            "status": "ok",
            "environment": settings.app_env,
            "uptime_seconds": int(time.monotonic() - _APP_START_TIME),
        },
        "database": {"status": "ok" if database_ok else "error"},
        "openrouter": {
            "status": "ok" if openrouter_ok else "error",
            "primary_model": settings.openrouter_primary_model,
            "fallback_model": settings.openrouter_fallback_model,
        },
        "whatsapp": {"status": "ok" if whatsapp_ok else "error"},
        "overall_status": "ok" if overall_ok else "degraded",
    }
