"""
scheduler.py
============
Background scheduled jobs, powered by APScheduler's BackgroundScheduler.

Responsibilities
-----------------
- Periodically expire stale (unanswered) owner-approval requests so a
  contact's message never gets stuck waiting forever if the owner never
  responds (approval.expire_stale_requests).
- Periodically expire Short-Term / Temporary Smart Memory facts past their
  `expires_at` (smart_memory.expire_stale_memories) - a separate, unrelated
  maintenance job for a separate, independent feature (see smart_memory.py
  / models.py for why Smart Memory and Conversation History are kept
  independent). Permanent and Long-Term-without-explicit-expiry memories
  are never touched by this sweep.
- Periodically send any due Scheduled Messages (scheduled_messages.py) -
  a genuinely independent feature from the two maintenance sweeps above
  (this one actually sends WhatsApp messages, not just cleans up state),
  registered here purely because it's the project's one existing
  background-job runner - no second scheduler was introduced.
- Provide a single, clearly-defined place where any future recurring
  maintenance job should be registered, started, and shut down cleanly
  alongside the FastAPI application lifecycle (see app.py).

Rate-limiter cooldowns and spam-detector mutes are intentionally NOT
scheduled here - they expire lazily/on-demand the next time that specific
contact is checked (see rate_limiter.py / spam_detector.py), which avoids
needless periodic work for state that self-heals on next access.
Human-override windows expire the same lazy way via
`Contact.is_human_override_active()`.
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from approval import expire_stale_requests
from database import session_scope
from logger import get_logger
from scheduled_messages import process_due_messages
from smart_memory import expire_stale_memories

logger = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None

# How often to sweep for expired approval requests.
_APPROVAL_EXPIRY_INTERVAL_SECONDS = 60

# How often to sweep for expired Smart Memory facts. Runs less frequently
# than the approval sweep since memory expiry is measured in days, not
# minutes (see SMART_MEMORY_SHORT_TERM_DAYS / SMART_MEMORY_TEMPORARY_DAYS
# in config.py), so there is no benefit to checking every 60 seconds.
_SMART_MEMORY_EXPIRY_INTERVAL_SECONDS = 3600

# How often to check for due scheduled messages. Frequent enough that a
# message scheduled for a specific minute goes out close to on time.
_SCHEDULED_MESSAGES_INTERVAL_SECONDS = 30


def _expire_approvals_job() -> None:
    """APScheduler job: expire any approval requests past their deadline."""
    try:
        with session_scope() as db:
            expired_count = expire_stale_requests(db)
            if expired_count:
                logger.info("Scheduled sweep expired %d approval request(s).", expired_count)
    except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler thread
        logger.error("Error while expiring stale approval requests.", exc_info=True)


def _expire_smart_memories_job() -> None:
    """APScheduler job: soft-delete any Smart Memory facts past their expiry."""
    try:
        with session_scope() as db:
            expired_count = expire_stale_memories(db)
            if expired_count:
                logger.info("Scheduled sweep expired %d Smart Memory record(s).", expired_count)
    except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler thread
        logger.error("Error while expiring stale Smart Memory records.", exc_info=True)


def _send_due_scheduled_messages_job() -> None:
    """APScheduler job: send any Scheduled Messages whose time has come."""
    try:
        with session_scope() as db:
            sent_count = process_due_messages(db)
            if sent_count:
                logger.info("Scheduled-message sweep sent %d message(s).", sent_count)
    except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler thread
        logger.error("Error while sending due scheduled messages.", exc_info=True)


def start_scheduler() -> BackgroundScheduler:
    """
    Create, configure, and start the background scheduler. Safe to call
    once at application startup. Returns the running scheduler instance
    so app.py can hold a reference for graceful shutdown.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _expire_approvals_job,
        trigger="interval",
        seconds=_APPROVAL_EXPIRY_INTERVAL_SECONDS,
        id="expire_approval_requests",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _expire_smart_memories_job,
        trigger="interval",
        seconds=_SMART_MEMORY_EXPIRY_INTERVAL_SECONDS,
        id="expire_smart_memories",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _send_due_scheduled_messages_job,
        trigger="interval",
        seconds=_SCHEDULED_MESSAGES_INTERVAL_SECONDS,
        id="send_due_scheduled_messages",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Background scheduler started (approval expiry every %ds, Smart Memory expiry every %ds, "
        "scheduled messages every %ds).",
        _APPROVAL_EXPIRY_INTERVAL_SECONDS, _SMART_MEMORY_EXPIRY_INTERVAL_SECONDS,
        _SCHEDULED_MESSAGES_INTERVAL_SECONDS,
    )
    return _scheduler


def shutdown_scheduler() -> None:
    """Gracefully stop the background scheduler, if running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped.")
    _scheduler = None
