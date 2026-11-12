"""
webhook.py
==========
FastAPI router for the WhatsApp Cloud API webhook endpoint.

This module is intentionally "thin": it only handles HTTP/webhook
transport concerns -
    - the GET verification handshake Meta performs when the webhook URL
      is configured in the Meta Developer dashboard,
    - HMAC-SHA256 signature verification of every inbound POST,
    - parsing the payload into ParsedMessage objects,
and then delegates all business logic to `routes.process_inbound_message()`.
No pipeline/business logic lives here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from database import get_db
from logger import get_logger
from routes import process_inbound_message
from whatsapp import parse_webhook_payload, verify_signature, verify_webhook_subscription

logger = get_logger("webhook")

router = APIRouter(tags=["webhook"])


@router.get("/webhook")
async def verify_webhook(request: Request) -> Response:
    """
    Handle Meta's webhook verification handshake:
    GET /webhook?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    result = verify_webhook_subscription(mode, token, challenge)
    if result is None:
        return Response(content="Verification failed", status_code=status.HTTP_403_FORBIDDEN)

    return Response(content=result, status_code=status.HTTP_200_OK, media_type="text/plain")


@router.post("/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    """
    Receive an inbound WhatsApp Cloud API webhook POST. Verifies the
    request signature, parses every message in the payload, and processes
    each one through the full pipeline in routes.py.

    Always returns HTTP 200 (unless the signature is invalid) so Meta does
    not aggressively retry-storm the endpoint; individual message
    processing errors are handled and logged inside routes.py itself.
    """
    raw_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256")

    if not verify_signature(raw_body, signature_header):
        logger.warning("Rejected webhook POST with invalid or missing signature.")
        return Response(content="Invalid signature", status_code=status.HTTP_403_FORBIDDEN)

    try:
        payload = await request.json()
    except ValueError:
        logger.error("Received webhook POST with invalid JSON body.")
        return Response(content="Invalid JSON", status_code=status.HTTP_400_BAD_REQUEST)

    parsed_messages = parse_webhook_payload(payload)

    for parsed in parsed_messages:
        process_inbound_message(db, parsed)

    return Response(content="OK", status_code=status.HTTP_200_OK)
