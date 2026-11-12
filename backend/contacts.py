"""
contacts.py
===========
Contact lifecycle management.

Responsibilities
-----------------
- Look up or create the `Contact` row for an inbound WhatsApp sender.
- Classify a contact as UNKNOWN / SAVED / BLOCKED.
- Provide the welcome-message workflow for first-time (unknown) contacts,
  including the "optionally save contact" behavior driven by the
  `auto_save_unknown_contacts` runtime setting.
- Provide per-contact AI enable/disable toggling (used by admin.py).
- Provide block/unblock operations (used by admin.py).

This module intentionally never calls the WhatsApp API directly - it only
manipulates database state and returns plain data/decisions. The actual
sending of the welcome message is done by the caller (routes.py) using the
text this module returns, keeping side effects centralized in one place.
"""

from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import load_runtime_settings
from logger import get_logger
from models import Contact, ContactStatus

logger = get_logger("contacts")


def get_contact(db: Session, wa_id: str) -> Optional[Contact]:
    """Fetch a contact by WhatsApp ID, or None if it doesn't exist yet."""
    stmt = select(Contact).where(Contact.wa_id == wa_id)
    return db.execute(stmt).scalar_one_or_none()


def get_or_create_contact(
    db: Session, wa_id: str, display_name: Optional[str] = None
) -> Tuple[Contact, bool]:
    """
    Fetch a contact, creating it (as UNKNOWN) if it doesn't exist yet.
    Returns (contact, was_created).
    """
    contact = get_contact(db, wa_id)
    if contact is not None:
        if display_name and not contact.display_name:
            contact.display_name = display_name
            db.commit()
        return contact, False

    contact = Contact(
        wa_id=wa_id,
        display_name=display_name,
        status=ContactStatus.UNKNOWN,
        ai_enabled=True,
        is_blocked=False,
        welcomed=False,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    logger.info("Created new contact record for %s", wa_id)
    return contact, True


def handle_new_contact_welcome(db: Session, contact: Contact) -> Optional[str]:
    """
    If this contact has never been welcomed, return the configured welcome
    message text (so the caller can send it) and mark them as welcomed.
    Also applies the `auto_save_unknown_contacts` setting: if enabled, the
    contact is promoted from UNKNOWN to SAVED immediately; otherwise it
    stays UNKNOWN (still gets a welcome message once, but is not persisted
    as a "known" contact for reporting purposes).

    Returns None if the contact has already been welcomed before.
    """
    if contact.welcomed:
        return None

    runtime_settings = load_runtime_settings()
    welcome_message = runtime_settings.get("welcome_message", "")

    contact.welcomed = True
    if runtime_settings.get("auto_save_unknown_contacts", True):
        contact.status = ContactStatus.SAVED

    db.commit()
    logger.info("Sent welcome message to new contact %s", contact.wa_id)
    return welcome_message


def save_contact(db: Session, contact: Contact) -> None:
    """Explicitly mark a contact as SAVED (known)."""
    contact.status = ContactStatus.SAVED
    db.commit()


def block_contact(db: Session, wa_id: str) -> bool:
    """Block a contact by WhatsApp ID. Returns True if a contact was found and blocked."""
    contact = get_contact(db, wa_id)
    if contact is None:
        return False
    contact.is_blocked = True
    contact.status = ContactStatus.BLOCKED
    db.commit()
    logger.info("Blocked contact %s", wa_id)
    return True


def unblock_contact(db: Session, wa_id: str) -> bool:
    """Unblock a contact by WhatsApp ID. Returns True if a contact was found and unblocked."""
    contact = get_contact(db, wa_id)
    if contact is None:
        return False
    contact.is_blocked = False
    contact.status = ContactStatus.SAVED
    db.commit()
    logger.info("Unblocked contact %s", wa_id)
    return True


def set_contact_ai_enabled(db: Session, wa_id: str, enabled: bool) -> bool:
    """Enable or disable the AI for a specific contact. Returns True if found."""
    contact = get_contact(db, wa_id)
    if contact is None:
        return False
    contact.ai_enabled = enabled
    db.commit()
    logger.info("Set ai_enabled=%s for contact %s", enabled, wa_id)
    return True


def is_blocked(contact: Contact) -> bool:
    """Return True if this contact is currently blocked."""
    return bool(contact.is_blocked or contact.status == ContactStatus.BLOCKED)


def is_ai_enabled_for_contact(contact: Contact) -> bool:
    """Return True if the per-contact AI switch is enabled for this contact."""
    return bool(contact.ai_enabled)


def touch_last_message_time(db: Session, contact: Contact) -> None:
    """Update the contact's last_message_at timestamp to now."""
    from datetime import datetime, timezone

    contact.last_message_at = datetime.now(timezone.utc)
    db.commit()
