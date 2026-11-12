"""
human_override.py
==================
Human Override: if the owner manually replies to a contact (e.g. from the
WhatsApp Business app while Cloud API + Business App coexistence is
enabled, which delivers the owner's outbound message back to us as a
"message echo" webhook event), the AI must stop auto-replying to that
contact for a configurable cool-down window, so the owner can take over
the conversation without the bot talking over them.

Responsibilities
-----------------
- Activate an override window for a contact when an owner-authored message
  is detected.
- Check whether an override is currently active for a contact.
- Clear an override early (e.g. via a future admin command or automatically
  once it naturally expires - `Contact.is_human_override_active()` already
  handles expiry transparently by comparing against the current time).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from config import settings
from logger import get_logger
from models import Contact

logger = get_logger("human_override")


def activate_override(db: Session, contact: Contact) -> datetime:
    """
    Activate (or extend) the human-override window for a contact, starting
    from now, for HUMAN_OVERRIDE_TIMEOUT_MINUTES minutes. Returns the new
    expiry timestamp.
    """
    expiry = datetime.now(timezone.utc) + timedelta(
        minutes=settings.human_override_timeout_minutes
    )
    contact.human_override_until = expiry
    db.commit()
    logger.info(
        "Activated human override for contact %s until %s",
        contact.wa_id, expiry.isoformat(),
    )
    return expiry


def is_override_active(contact: Contact) -> bool:
    """Return True if the AI should currently stay paused for this contact."""
    return contact.is_human_override_active()


def clear_override(db: Session, contact: Contact) -> None:
    """Immediately end the human-override window for a contact."""
    contact.human_override_until = None
    db.commit()
    logger.info("Cleared human override for contact %s", contact.wa_id)


def is_owner_authored_message(sender_wa_id: str) -> bool:
    """
    Return True if a message's sender matches the configured owner number.
    Used by routes.py when processing "message echo" webhook events (the
    owner's own outbound messages, sent manually from the WhatsApp
    Business app) to decide whether to trigger an override.
    """
    if not settings.owner_whatsapp_number:
        return False
    return sender_wa_id.strip() == settings.owner_whatsapp_number.strip()
