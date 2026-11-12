"""
scheduled_messages.py
======================
CRUD and send-execution for owner-scheduled outbound WhatsApp messages
(dashboard feature - "Scheduled Messages" per the product spec). New
model (`models.ScheduledMessage`), reusing the existing `whatsapp_client`
(whatsapp.py) to actually send - no second WhatsApp client - and the
existing `scheduler.py` background-job infrastructure to check for due
messages, as its own independent job (unrelated to that module's existing
approval/Smart-Memory expiry sweeps - see scheduler.py's updated
docstring).

Never fakes execution: `process_due_messages()` only marks a message
`sent` after `whatsapp_client.send_text_message()` actually succeeds, and
marks it `failed` (with a safe, non-secret error detail) otherwise -
mirroring the "never fake success" requirement used everywhere else in
this project (voice_reply.py, human_override.py, etc.).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from logger import get_logger
from models import ScheduledMessage, ScheduledMessageStatus
from whatsapp import WhatsAppAPIError, whatsapp_client

logger = get_logger("scheduled_messages")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create(db: Session, contact_wa_id: str, message_text: str, scheduled_at: datetime, tz: str = "UTC") -> ScheduledMessage:
    entry = ScheduledMessage(
        contact_wa_id=contact_wa_id,
        message_text=message_text,
        scheduled_at=scheduled_at,
        timezone=tz,
        status=ScheduledMessageStatus.PENDING,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_all(db: Session, status: Optional[ScheduledMessageStatus] = None) -> List[ScheduledMessage]:
    stmt = select(ScheduledMessage).order_by(ScheduledMessage.scheduled_at.desc())
    if status is not None:
        stmt = stmt.where(ScheduledMessage.status == status)
    return list(db.execute(stmt).scalars().all())


def get(db: Session, message_id: int) -> Optional[ScheduledMessage]:
    return db.get(ScheduledMessage, message_id)


def cancel(db: Session, message_id: int) -> Optional[ScheduledMessage]:
    entry = db.get(ScheduledMessage, message_id)
    if entry is None or entry.status != ScheduledMessageStatus.PENDING:
        return None
    entry.status = ScheduledMessageStatus.CANCELLED
    entry.cancelled_at = _utcnow()
    db.commit()
    return entry


def retry(db: Session, message_id: int) -> Optional[ScheduledMessage]:
    """Re-queue a failed message to send immediately (sets scheduled_at to now)."""
    entry = db.get(ScheduledMessage, message_id)
    if entry is None or entry.status != ScheduledMessageStatus.FAILED:
        return None
    entry.status = ScheduledMessageStatus.PENDING
    entry.scheduled_at = _utcnow()
    entry.error_detail = None
    db.commit()
    return entry


def process_due_messages(db: Session) -> int:
    """
    Send every PENDING message whose scheduled_at has passed. Called by
    scheduler.py's background job. Returns the number of messages sent
    (for logging only). Never raises - a single bad message must not
    stop the rest of the sweep or crash the scheduler thread.
    """
    stmt = select(ScheduledMessage).where(
        ScheduledMessage.status == ScheduledMessageStatus.PENDING,
        ScheduledMessage.scheduled_at <= _utcnow(),
    )
    due = list(db.execute(stmt).scalars().all())
    sent_count = 0
    for entry in due:
        try:
            whatsapp_client.send_text_message(entry.contact_wa_id, entry.message_text)
            entry.status = ScheduledMessageStatus.SENT
            entry.sent_at = _utcnow()
            sent_count += 1
            logger.info("Sent scheduled message #%d to %s.", entry.id, entry.contact_wa_id)
        except WhatsAppAPIError as exc:
            entry.status = ScheduledMessageStatus.FAILED
            entry.failed_at = _utcnow()
            entry.error_detail = str(exc)[:255]
            logger.error("Scheduled message #%d failed to send: %s", entry.id, exc)
        db.commit()
    return sent_count
