"""
approval.py
============
Owner Approval workflow for sensitive-information requests.

When security.detect_sensitive_request() flags an inbound message, the
assistant does NOT answer it automatically. Instead:
  1. A `ApprovalRequest` row is created (status=PENDING) with an expiry
     timestamp.
  2. A notification is sent to the owner via WhatsApp, containing the
     contact's message and simple `/approve <id>` / `/reject <id>`
     instructions.
  3. Processing of that inbound message stops there; the contact receives
     no reply yet.
  4. When the owner later sends `/approve <id>` or `/reject <id>` (handled
     in admin.py), `resolve_approval()` below updates the request and
     returns enough information for routes.py to either generate the real
     AI reply now (approved) or send a polite decline (rejected).
  5. A scheduled job (scheduler.py) periodically calls
     `expire_stale_requests()` so abandoned requests don't stay PENDING
     forever.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import APPROVAL_PROMPT_PATH, load_runtime_settings
from logger import get_logger, log_approval
from models import ApprovalRequest, ApprovalStatus, Contact

logger = get_logger("approval")

_DEFAULT_APPROVAL_TEMPLATE = (
    "Approval needed (request #{request_id})\n"
    "Contact: {contact_name} ({contact_wa_id})\n"
    "Reason: {reason}\n"
    "Message: \"{user_message}\"\n\n"
    "Reply /approve {request_id} or /reject {request_id}. "
    "Expires in {timeout_minutes} minutes."
)

REJECTION_MESSAGE = (
    "I'm not able to help with that particular request. "
    "Is there something else I can assist you with?"
)


def _load_approval_template() -> str:
    if APPROVAL_PROMPT_PATH.exists():
        try:
            return APPROVAL_PROMPT_PATH.read_text(encoding="utf-8")
        except OSError:
            logger.error("Failed to read approval_prompt.txt; using built-in default.")
    return _DEFAULT_APPROVAL_TEMPLATE


def create_approval_request(
    db: Session, contact: Contact, user_message: str, reason: str
) -> ApprovalRequest:
    """Create a new PENDING approval request for a sensitive inbound message."""
    runtime_settings = load_runtime_settings()
    timeout_minutes = int(
        runtime_settings.get("approval", {}).get("approval_timeout_minutes", 15)
    )
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)

    request = ApprovalRequest(
        contact_id=contact.id,
        user_message=user_message,
        reason=reason,
        status=ApprovalStatus.PENDING,
        expires_at=expires_at,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    log_approval(contact.wa_id, "pending", reason)
    logger.info(
        "Created approval request #%d for contact %s (reason=%s, expires=%s)",
        request.id, contact.wa_id, reason, expires_at.isoformat(),
    )
    return request


def build_owner_notification(request: ApprovalRequest, contact: Contact) -> str:
    """Render the approval-request notification message to send to the owner."""
    runtime_settings = load_runtime_settings()
    timeout_minutes = runtime_settings.get("approval", {}).get("approval_timeout_minutes", 15)
    template = _load_approval_template()

    try:
        return template.format(
            request_id=request.id,
            contact_name=contact.display_name or "Unknown",
            contact_wa_id=contact.wa_id,
            reason=request.reason,
            user_message=request.user_message,
            timeout_minutes=timeout_minutes,
        )
    except (KeyError, IndexError):
        logger.error("Approval prompt template has an invalid placeholder; using built-in default.")
        return _DEFAULT_APPROVAL_TEMPLATE.format(
            request_id=request.id,
            contact_name=contact.display_name or "Unknown",
            contact_wa_id=contact.wa_id,
            reason=request.reason,
            user_message=request.user_message,
            timeout_minutes=timeout_minutes,
        )


def get_request_by_id(db: Session, request_id: int) -> Optional[ApprovalRequest]:
    return db.get(ApprovalRequest, request_id)


def resolve_approval(db: Session, request_id: int, approved: bool) -> Optional[ApprovalRequest]:
    """
    Resolve a pending approval request as approved or rejected. Returns the
    updated request, or None if the request doesn't exist or is no longer
    pending (already resolved/expired).
    """
    request = get_request_by_id(db, request_id)
    if request is None:
        logger.warning("Attempted to resolve unknown approval request #%d", request_id)
        return None

    if request.status != ApprovalStatus.PENDING:
        logger.warning(
            "Attempted to resolve approval request #%d which is already '%s'",
            request_id, request.status.value,
        )
        return request

    request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    request.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)

    contact = db.get(Contact, request.contact_id)
    wa_id = contact.wa_id if contact else "unknown"
    log_approval(wa_id, request.status.value)
    logger.info("Approval request #%d resolved as %s", request_id, request.status.value)
    return request


def expire_stale_requests(db: Session) -> int:
    """
    Mark any PENDING approval requests whose expiry time has passed as
    EXPIRED. Intended to be called periodically by scheduler.py. Returns
    the number of requests expired.
    """
    now = datetime.now(timezone.utc)
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.status == ApprovalStatus.PENDING,
        ApprovalRequest.expires_at.is_not(None),
        ApprovalRequest.expires_at < now,
    )
    stale_requests = list(db.execute(stmt).scalars().all())
    for request in stale_requests:
        request.status = ApprovalStatus.EXPIRED
        request.resolved_at = now
    if stale_requests:
        db.commit()
        logger.info("Expired %d stale approval request(s).", len(stale_requests))
    return len(stale_requests)
