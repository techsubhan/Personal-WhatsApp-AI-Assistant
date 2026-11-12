"""
models.py
=========
SQLAlchemy ORM models for the WhatsApp AI Assistant.

Tables
------
- GlobalSetting     : simple key/value store for DB-backed global flags
                       (e.g. the global AI on/off switch).
- Contact           : one row per WhatsApp contact (phone number). Tracks
                       AI enabled/disabled, blocked status, human override,
                       and whether the contact is "known" (saved) or not.
- Message           : conversation memory. One row per inbound/outbound
                       message, linked to a Contact.
- ApprovalRequest   : sensitive-information approval workflow state.
- AdminActionLog    : audit trail of admin commands executed.
- UserMemory        : Smart Memory - structured, permanent-by-default facts
                       about a contact (name, city, preferences, etc.),
                       completely independent of the Message/Conversation
                       History table above. See smart_memory.py.
- MemoryHistory     : version history for UserMemory rows, so an updated
                       or overwritten fact's previous value is never lost.

Design notes
------------
Rate limiting and spam detection deliberately use in-process, in-memory
state (see rate_limiter.py / spam_detector.py) rather than database tables,
since they operate on short rolling time windows (seconds/minutes) where
DB round-trips would add unnecessary overhead. Everything that must survive
a restart or be auditable (contacts, AI switches, conversation history,
approvals, admin actions, smart memory) is persisted here.

Conversation History vs. Smart Memory
--------------------------------------
These are two deliberately independent systems that both hang off Contact:

    Contact
        |
        |------ messages          (Conversation History - every message,
        |                          verbatim, see Message / memory.py)
        |
        |------ approval_requests (sensitive-information approval workflow)
        |
        |------ smart_memories    (Smart Memory - structured, deduplicated
                                   facts about the contact, see UserMemory /
                                   smart_memory.py)

Conversation History (Message) is never modified, summarized, or deleted
by the Smart Memory feature. Smart Memory never stores raw message text;
it stores extracted, structured facts with their own lifecycle
(confidence, importance, retention policy, expiry, versioning).
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    String,
    Boolean,
    DateTime,
    Integer,
    Float,
    Text,
    ForeignKey,
    Enum as SAEnum,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _utcnow() -> datetime:
    """Timezone-aware UTC 'now' helper used as a default for timestamp columns."""
    return datetime.now(timezone.utc)


class GlobalSetting(Base):
    """
    Generic key/value table for global, database-backed settings.
    Primarily used for the Global AI Switch, as required by the spec
    ("Stored inside database"). Other simple global flags may be added here
    without a schema migration.
    """

    __tablename__ = "global_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    # Well-known keys
    KEY_GLOBAL_AI_ENABLED = "global_ai_enabled"


class ContactStatus(str, enum.Enum):
    """High-level classification of a contact used by contacts.py / admin.py."""

    UNKNOWN = "unknown"
    SAVED = "saved"
    BLOCKED = "blocked"


class Contact(Base):
    """
    A single WhatsApp contact (identified by phone number, WhatsApp's
    'wa_id'). Stores per-contact AI state, block state, and human-override
    bookkeeping.
    """

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wa_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    status: Mapped[ContactStatus] = mapped_column(
        SAEnum(ContactStatus), default=ContactStatus.UNKNOWN, nullable=False
    )
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Human override: when the owner manually replies to this contact, AI
    # pauses until `human_override_until` elapses.
    human_override_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Welcome message bookkeeping so we only greet a new contact once.
    welcomed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Best-effort language/script label from the most recent inbound
    # message (see language.py) - e.g. "roman_urdu", "urdu", "english".
    # Informational only: the AI's actual reply language is still driven
    # by the system prompt's "mirror their language" instruction, not by
    # this field (see language.py module docstring for why). Added via
    # database.py's additive-migration step so it's safe on an
    # already-running deployment.
    detected_language: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Per-contact web search toggle (Section 14). Only takes effect when
    # the global web_search runtime setting is also enabled - see
    # web_search.should_search(). Added via database.py's
    # additive-migration step.
    web_search_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="contact", cascade="all, delete-orphan"
    )
    approval_requests: Mapped[list["ApprovalRequest"]] = relationship(
        "ApprovalRequest", back_populates="contact", cascade="all, delete-orphan"
    )
    smart_memories: Mapped[list["UserMemory"]] = relationship(
        "UserMemory", back_populates="contact", cascade="all, delete-orphan"
    )

    def is_human_override_active(self) -> bool:
        """Return True if the owner is currently 'manually handling' this chat."""
        if self.human_override_until is None:
            return False
        return _utcnow() < self.human_override_until


class MessageRole(str, enum.Enum):
    """Who authored a stored conversation message."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    OWNER = "owner"  # Manual message sent by the owner (human override)


class Message(Base):
    """
    A single message in the conversation memory for a contact. Used to
    reconstruct context for the AI prompt and for admin/debugging purposes.
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MessageRole] = mapped_column(SAEnum(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    whatsapp_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    contact: Mapped["Contact"] = relationship("Contact", back_populates="messages")


class ApprovalStatus(str, enum.Enum):
    """State machine for an owner-approval request on a sensitive message."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequest(Base):
    """
    Represents a request sent to the owner to approve (or reject) an AI
    reply that involves sensitive information (passwords, OTP, bank
    details, etc.), per the Sensitive Information Detection feature.
    """

    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus), default=ApprovalStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    contact: Mapped["Contact"] = relationship("Contact", back_populates="approval_requests")


class AdminActionLog(Base):
    """Audit trail of every admin command executed, for accountability."""

    __tablename__ = "admin_action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_wa_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    command: Mapped[str] = mapped_column(String(255), nullable=False)
    target_wa_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class RequestLog(Base):
    """
    Lightweight persisted log of processed webhook requests, used by the
    health/status endpoints to report recent activity and error rates.
    Detailed structured logs still go to rotating log files via logger.py;
    this table is only a compact queryable summary.
    """

    __tablename__ = "request_logs"
    __table_args__ = (UniqueConstraint("whatsapp_message_id", name="uq_request_logs_wa_msg_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    whatsapp_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    contact_wa_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # success | error | ignored
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class ScheduledMessageStatus(str, enum.Enum):
    """State machine for a dashboard-created scheduled outbound message."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScheduledMessage(Base):
    """
    A message the owner scheduled (from the dashboard) to be sent to a
    contact at a future time. Independent of `scheduler.py`'s existing
    maintenance jobs (approval/Smart-Memory expiry sweeps) - this is a
    genuinely new, explicitly-requested feature, not an extension of that
    unrelated maintenance scheduler's purpose. See scheduled_messages.py.
    """

    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_wa_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    status: Mapped[ScheduledMessageStatus] = mapped_column(
        SAEnum(ScheduledMessageStatus), default=ScheduledMessageStatus.PENDING, nullable=False, index=True
    )
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessedWebhookMessage(Base):
    """
    Webhook idempotency claim table. A row is inserted (and immediately
    committed, as its own tiny transaction) for a WhatsApp message id
    (wamid) the INSTANT routes.py starts processing it - BEFORE any AI
    call, WhatsApp send, or other expensive work. The `wamid` column's
    UNIQUE constraint is what makes the claim atomic: if two webhook
    deliveries for the same message arrive concurrently (Meta retries,
    or a genuine simultaneous duplicate), exactly one INSERT succeeds and
    the other raises IntegrityError, which the caller treats as
    "already being processed / already processed - do nothing".

    This is deliberately a separate, minimal table from `RequestLog`:
    RequestLog's existing unique constraint on whatsapp_message_id is
    written only at the END of processing (after the AI reply has already
    been generated and sent), so it cannot prevent duplicate processing -
    only duplicate logging after the fact. This table exists specifically
    to be checked/claimed FIRST.
    """

    __tablename__ = "processed_webhook_messages"

    wamid: Mapped[str] = mapped_column(String(128), primary_key=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# =============================================================================
# Smart Memory
# =============================================================================
# Everything below this point is new, additive schema for the Smart Memory
# feature (see smart_memory.py). It is entirely independent of Message /
# Conversation History above: Smart Memory stores structured, deduplicated
# facts about a contact, not raw message text, and has its own lifecycle
# (confidence, importance, retention policy, expiry, versioning via
# MemoryHistory). No existing table, column, or relationship was modified
# to add this - see the Contact.smart_memories relationship added above,
# which follows the exact same pattern as the pre-existing
# Contact.messages / Contact.approval_requests relationships.
# =============================================================================


class MemoryCategory(str, enum.Enum):
    """
    Top-level classification for a Smart Memory fact. Matches the 23
    categories defined in the project specification exactly.
    """

    PERSONAL = "personal"
    CONTACT = "contact"
    EDUCATION = "education"
    WORK = "work"
    PROJECTS = "projects"
    SKILLS = "skills"
    PREFERENCES = "preferences"
    GOALS = "goals"
    TRAVEL = "travel"
    FAMILY = "family"
    FRIENDS_AND_RELATIONSHIPS = "friends_and_relationships"
    PETS = "pets"
    HEALTH = "health"
    FINANCE = "finance"
    SOCIAL_ACCOUNTS = "social_accounts"
    DEVICES = "devices"
    IMPORTANT_DATES = "important_dates"
    CONVERSATION_PREFERENCES = "conversation_preferences"
    AI_SETTINGS = "ai_settings"
    INTERESTS = "interests"
    EVENTS_AND_EXPERIENCES = "events_and_experiences"
    MEMORIES = "memories"
    OTHER = "other"


class MemoryType(str, enum.Enum):
    """
    What *kind* of thing a Smart Memory fact represents, independent of
    which category it was filed under (e.g. a Goal-type memory could live
    under the "work", "personal", or "finance" category).
    """

    FACT = "fact"
    PREFERENCE = "preference"
    GOAL = "goal"
    EVENT = "event"
    RELATIONSHIP = "relationship"
    EXPERIENCE = "experience"
    OPINION = "opinion"
    SKILL = "skill"
    PROJECT = "project"
    REMINDER = "reminder"
    ACHIEVEMENT = "achievement"
    CONTACT = "contact"
    HEALTH = "health"
    FINANCE = "finance"
    TRAVEL = "travel"
    EDUCATION = "education"
    WORK = "work"
    DEVICE = "device"
    CUSTOM = "custom"


class ImportanceLevel(str, enum.Enum):
    """
    How important a memory is when deciding what fits inside the Smart
    Memory prompt budget. Automatically re-ranked over time based on
    access_count (see smart_memory.py `_recompute_importance`).
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RetentionPolicy(str, enum.Enum):
    """
    How long a memory should live. PERMANENT memories never expire.
    LONG_TERM memories only expire if an explicit expires_at is set.
    MEDIUM_TERM / SHORT_TERM / TEMPORARY memories are assigned a default
    expiry (see config.py smart_memory_short_term_days /
    smart_memory_temporary_days) and are periodically swept by
    scheduler.py once expired.
    """

    PERMANENT = "permanent"
    LONG_TERM = "long_term"
    MEDIUM_TERM = "medium_term"
    SHORT_TERM = "short_term"
    TEMPORARY = "temporary"


class MemorySource(str, enum.Enum):
    """
    Where a Smart Memory fact came from, for transparency/debugging.
    Note: this column is an addition beyond the bare field list originally
    enumerated for UserMemory, because the specification's own "Memory
    Source" section requires every memory to record its source - it is
    included here so that requirement is actually satisfiable.
    """

    USER_EXPLICIT = "user_explicit"                # "Remember my phone number."
    USER_STATEMENT = "user_statement"               # "My name is Ali."
    CONVERSATION_INFERENCE = "conversation_inference"  # inferred from context
    ADMIN_COMMAND = "admin_command"                 # created/edited by an admin
    IMPORTED = "imported"                            # bulk-imported from elsewhere
    MANUAL_UPDATE = "manual_update"                  # edited directly, e.g. via API


class UserMemory(Base):
    """
    Smart Memory: a single structured, deduplicated fact about a contact
    (e.g. category=personal/subcategory="Full Name"/value="Ali").

    Deduplication key: (contact_id, category, subcategory) is treated as
    the logical identity of a fact - saving a new value for a subcategory
    that already has an active memory updates the existing row (and
    records the previous value in MemoryHistory) rather than creating a
    duplicate. See smart_memory.save_memory() / update_memory().

    This table is completely independent of Message (Conversation
    History): it never stores raw message text as `value`, only extracted
    facts, and it has its own confidence/importance/retention/expiry
    lifecycle that Conversation History does not have.
    """

    __tablename__ = "user_memories"
    __table_args__ = (
        # Composite index for the (contact, category, subcategory) dedup
        # lookup used on every save_memory() call - this is the primary
        # hot-path index, keeping that check O(log n) instead of a full
        # table scan even as a contact accumulates many memories.
        Index("ix_user_memories_contact_category_subcategory", "contact_id", "category", "subcategory"),
        Index("ix_user_memories_contact_active", "contact_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    category: Mapped[MemoryCategory] = mapped_column(SAEnum(MemoryCategory), nullable=False)
    subcategory: Mapped[str] = mapped_column(String(100), nullable=False)
    memory_type: Mapped[MemoryType] = mapped_column(SAEnum(MemoryType), nullable=False)

    # `key` is a short, normalized identifier for this fact (often equal to
    # `subcategory`, e.g. "full_name"), used for fast exact-match lookups
    # in get_memory(); `value` is the actual stored fact content.
    key: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    importance: Mapped[ImportanceLevel] = mapped_column(
        SAEnum(ImportanceLevel), default=ImportanceLevel.MEDIUM, nullable=False
    )
    retention_policy: Mapped[RetentionPolicy] = mapped_column(
        SAEnum(RetentionPolicy), default=RetentionPolicy.LONG_TERM, nullable=False
    )
    source: Mapped[MemorySource] = mapped_column(
        SAEnum(MemorySource), default=MemorySource.USER_STATEMENT, nullable=False
    )

    # Comma-separated free-form tags for extra semantic/search hints
    # (e.g. "career,rust,programming"). Kept as a plain string rather than
    # a separate table to avoid an extra join on the hot retrieval path;
    # revisit as a normalized table only if tag-based querying becomes a
    # first-class feature.
    tags: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # The Conversation History message (if any) that this memory was
    # extracted from, for traceability. Intentionally NOT a foreign key
    # with cascading delete: a Smart Memory fact should survive even if
    # the original message is later pruned, since the two systems are
    # independent by design.
    source_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    contact: Mapped["Contact"] = relationship("Contact", back_populates="smart_memories")
    history: Mapped[list["MemoryHistory"]] = relationship(
        "MemoryHistory", back_populates="memory", cascade="all, delete-orphan"
    )

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Return True if this memory has an expiry timestamp that has passed."""
        if self.expires_at is None:
            return False
        reference = now or _utcnow()
        return reference >= self.expires_at


class MemoryHistory(Base):
    """
    Version history for a UserMemory row. Every time a Smart Memory's
    value is updated or overwritten, the previous value is recorded here
    first, so nothing is ever silently lost - this table exists purely to
    satisfy "never lose previous information" from the specification and
    to support future rollback/audit tooling.
    """

    __tablename__ = "memory_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[int] = mapped_column(
        ForeignKey("user_memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    changed_by: Mapped[MemorySource] = mapped_column(SAEnum(MemorySource), nullable=False)

    memory: Mapped["UserMemory"] = relationship("UserMemory", back_populates="history")
