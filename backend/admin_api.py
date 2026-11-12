"""
admin_api.py
============
Dashboard-facing Admin REST API.

This module is NEW and ADDITIVE: it does not change any existing route,
model, or business-logic function. It only wraps the already-existing
functions in contacts.py, memory.py, approval.py, human_override.py,
smart_memory.py, config.py, and models.py behind proper HTTP endpoints so
a real admin dashboard can call them, instead of the owner having to type
WhatsApp chat commands (see admin.py) or edit data/*.json by hand.

Auth
----
Every route below (except none - all are protected) requires a header:

    X-Admin-Key: <INTERNAL_API_KEY>

matching the INTERNAL_API_KEY value already defined in .env / config.py
(previously unused for auth - this is its first real use). If
INTERNAL_API_KEY is empty, every request is rejected (fail closed), so the
dashboard cannot accidentally be left open on a fresh install.

Mounting
--------
app.py mounts this router with:

    from admin_api import router as admin_api_router
    app.include_router(admin_api_router)

No other change to app.py's request-handling behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session

from approval import resolve_approval
from config import (
    load_profile,
    load_runtime_settings,
    reload_all,
    save_profile,
    settings as static_settings,
    update_runtime_setting,
)
from contacts import (
    block_contact,
    get_contact,
    set_contact_ai_enabled,
    unblock_contact,
)
from database import get_db
from human_override import activate_override, clear_override
from logger import get_logger, log_admin_command
from memory import clear_memory
from models import (
    AdminActionLog,
    ApprovalRequest,
    ApprovalStatus,
    Contact,
    ContactStatus,
    GlobalSetting,
    Message,
    RequestLog,
    ScheduledMessageStatus,
)
import scheduled_messages
from smart_memory import delete_memory, get_all_memory, get_memory_statistics, search_memory
from stt import is_configured as stt_is_configured
from tts import is_configured as tts_is_configured
from working_hours import evaluate as evaluate_working_hours
import web_search

logger = get_logger("admin_api")

router = APIRouter(prefix="/admin", tags=["admin-dashboard"])

_DASHBOARD_ACTOR = "dashboard"  # recorded as admin_wa_id in AdminActionLog for traceability


# =============================================================================
# Auth
# =============================================================================
def require_admin_key(x_admin_key: Optional[str] = Header(default=None)) -> None:
    """
    Every /admin/* route depends on this. Fails closed: if INTERNAL_API_KEY
    is not configured in .env, the dashboard API is entirely disabled
    rather than silently open.
    """
    if not static_settings.internal_api_key:
        raise HTTPException(
            status_code=503,
            detail="Dashboard API is disabled: set INTERNAL_API_KEY in .env to enable it.",
        )
    if not x_admin_key or x_admin_key != static_settings.internal_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key header.")


def _record_action(db: Session, command: str, target_wa_id: Optional[str], result: str) -> None:
    """Mirror of admin.py's _record(), reused here so dashboard actions land in the same audit trail."""
    db.add(
        AdminActionLog(
            admin_wa_id=_DASHBOARD_ACTOR,
            command=command,
            target_wa_id=target_wa_id,
            result=result,
        )
    )
    db.commit()
    log_admin_command(_DASHBOARD_ACTOR, command, result)


def _require_contact(db: Session, wa_id: str) -> Contact:
    contact = get_contact(db, wa_id)
    if contact is None:
        raise HTTPException(status_code=404, detail=f"No contact found with wa_id '{wa_id}'.")
    return contact


def _global_ai_enabled(db: Session) -> bool:
    setting = db.get(GlobalSetting, GlobalSetting.KEY_GLOBAL_AI_ENABLED)
    return True if setting is None else setting.value == "True"


# =============================================================================
# Request/response models
# =============================================================================
class ToggleBody(BaseModel):
    enabled: bool


class BlockBody(BaseModel):
    blocked: bool


class ApprovalActionBody(BaseModel):
    approve: bool


class OverrideBody(BaseModel):
    action: str  # "activate" | "clear"


class SettingsPatchBody(BaseModel):
    """Dotted-key -> value pairs, e.g. {"working_hours.start_time": "09:30"}."""
    updates: dict


class ProfilePatchBody(BaseModel):
    updates: dict


# =============================================================================
# Overview
# =============================================================================
@router.get("/overview/stats", dependencies=[Depends(require_admin_key)])
def overview_stats(db: Session = Depends(get_db)) -> dict:
    total_contacts = db.execute(select(func.count(Contact.id))).scalar_one()
    saved_contacts = db.execute(
        select(func.count(Contact.id)).where(Contact.status == ContactStatus.SAVED)
    ).scalar_one()
    blocked_contacts = db.execute(
        select(func.count(Contact.id)).where(Contact.is_blocked.is_(True))
    ).scalar_one()
    ai_disabled_contacts = db.execute(
        select(func.count(Contact.id)).where(Contact.ai_enabled.is_(False))
    ).scalar_one()
    total_messages = db.execute(select(func.count(Message.id))).scalar_one()
    pending_approvals = db.execute(
        select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == ApprovalStatus.PENDING)
    ).scalar_one()
    active_overrides = db.execute(
        select(func.count(Contact.id)).where(Contact.human_override_until.is_not(None))
    ).scalar_one()
    # Re-check expiry in Python since "active" depends on comparing to now(),
    # which SQLite can't do portably in a WHERE clause here.
    active_override_contacts = db.execute(
        select(Contact).where(Contact.human_override_until.is_not(None))
    ).scalars().all()
    active_overrides = sum(1 for c in active_override_contacts if c.is_human_override_active())

    memory_stats = get_memory_statistics(db)
    runtime_settings = load_runtime_settings()

    return {
        "contacts": {
            "total": total_contacts,
            "saved": saved_contacts,
            "blocked": blocked_contacts,
            "ai_disabled": ai_disabled_contacts,
        },
        "messages": {"total": total_messages},
        "approvals": {"pending": pending_approvals},
        "human_override": {"active": active_overrides},
        "smart_memory": {"total_active_facts": memory_stats["total_memories"]},
        "ai": {
            "global_ai_enabled": _global_ai_enabled(db),
            "working_hours_enabled": runtime_settings.get("working_hours", {}).get("enabled", False),
        },
    }


# =============================================================================
# AI Control
# =============================================================================
@router.get("/ai/status", dependencies=[Depends(require_admin_key)])
def ai_status(db: Session = Depends(get_db)) -> dict:
    decision = evaluate_working_hours("")
    return {
        "global_ai_enabled": _global_ai_enabled(db),
        "primary_model": static_settings.openrouter_primary_model,
        "fallback_model": static_settings.openrouter_fallback_model,
        "typing_delay_enabled": load_runtime_settings().get("typing_delay", {}).get("enabled", True),
        "working_hours": {
            "within_hours": decision.allowed or decision.reason == "working_hours_disabled",
            "reason": decision.reason,
        },
        # AI Voice Reply / incoming voice: model choice is read-only here
        # (OPENROUTER_API_KEY never exposed) - editable settings
        # (enabled/mode/voice/speed/etc.) live under /admin/settings
        # "voice_reply", same pattern as typing_delay. Both TTS and STT
        # share the same OpenRouter credential, so a single "configured"
        # flag covers both.
        "voice_reply": {
            "tts_model": static_settings.openrouter_tts_model,
            "tts_configured": tts_is_configured(),
        },
        "voice_input": {
            "enabled": static_settings.voice_input_enabled,
            "stt_model": static_settings.openrouter_stt_model,
            "stt_configured": stt_is_configured(),
        },
        "web_search": {
            "configured": web_search.is_configured(),
            "provider": static_settings.web_search_provider or None,
        },
    }


@router.post("/ai/toggle", dependencies=[Depends(require_admin_key)])
def ai_toggle(body: ToggleBody, db: Session = Depends(get_db)) -> dict:
    setting = db.get(GlobalSetting, GlobalSetting.KEY_GLOBAL_AI_ENABLED)
    if setting is None:
        setting = GlobalSetting(key=GlobalSetting.KEY_GLOBAL_AI_ENABLED, value=str(body.enabled))
        db.add(setting)
    else:
        setting.value = str(body.enabled)
    db.commit()
    _record_action(db, "/ai (dashboard)", None, "on" if body.enabled else "off")
    return {"global_ai_enabled": body.enabled}


# =============================================================================
# Contacts
# =============================================================================
@router.get("/contacts", dependencies=[Depends(require_admin_key)])
def list_contacts(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    blocked: Optional[bool] = Query(default=None),
    ai_enabled: Optional[bool] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    stmt = select(Contact)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Contact.wa_id.ilike(like), Contact.display_name.ilike(like)))
    if status_filter:
        try:
            stmt = stmt.where(Contact.status == ContactStatus(status_filter))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status '{status_filter}'.")
    if blocked is not None:
        stmt = stmt.where(Contact.is_blocked.is_(blocked))
    if ai_enabled is not None:
        stmt = stmt.where(Contact.ai_enabled.is_(ai_enabled))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    stmt = stmt.order_by(Contact.last_message_at.desc().nullslast()).offset((page - 1) * page_size).limit(page_size)
    contacts = db.execute(stmt).scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_contact_brief(c) for c in contacts],
    }


def _contact_brief(c: Contact) -> dict:
    return {
        "wa_id": c.wa_id,
        "display_name": c.display_name,
        "status": c.status.value,
        "ai_enabled": c.ai_enabled,
        "is_blocked": c.is_blocked,
        "human_override_active": c.is_human_override_active(),
        "detected_language": c.detected_language,
        "web_search_enabled": c.web_search_enabled,
        "created_at": c.created_at.isoformat(),
        "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
    }


@router.get("/contacts/{wa_id}", dependencies=[Depends(require_admin_key)])
def get_contact_detail(wa_id: str, db: Session = Depends(get_db)) -> dict:
    contact = _require_contact(db, wa_id)
    return _contact_brief(contact)


@router.post("/contacts/{wa_id}/block", dependencies=[Depends(require_admin_key)])
def set_contact_block(wa_id: str, body: BlockBody, db: Session = Depends(get_db)) -> dict:
    ok = block_contact(db, wa_id) if body.blocked else unblock_contact(db, wa_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No contact found with wa_id '{wa_id}'.")
    _record_action(db, "/block (dashboard)" if body.blocked else "/unblock (dashboard)", wa_id, "ok")
    return {"wa_id": wa_id, "is_blocked": body.blocked}


@router.patch("/contacts/{wa_id}/ai", dependencies=[Depends(require_admin_key)])
def set_contact_ai(wa_id: str, body: ToggleBody, db: Session = Depends(get_db)) -> dict:
    ok = set_contact_ai_enabled(db, wa_id, enabled=body.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No contact found with wa_id '{wa_id}'.")
    _record_action(db, "/contact ai (dashboard)", wa_id, "on" if body.enabled else "off")
    return {"wa_id": wa_id, "ai_enabled": body.enabled}


@router.patch("/contacts/{wa_id}/web-search", dependencies=[Depends(require_admin_key)])
def set_contact_web_search(wa_id: str, body: ToggleBody, db: Session = Depends(get_db)) -> dict:
    contact = _require_contact(db, wa_id)
    contact.web_search_enabled = body.enabled
    db.commit()
    _record_action(db, "/contact web-search (dashboard)", wa_id, "on" if body.enabled else "off")
    return {"wa_id": wa_id, "web_search_enabled": body.enabled}


# =============================================================================
# Conversations
# =============================================================================
@router.get("/contacts/{wa_id}/messages", dependencies=[Depends(require_admin_key)])
def list_messages(wa_id: str, db: Session = Depends(get_db), limit: int = Query(default=100, ge=1, le=500)) -> dict:
    contact = _require_contact(db, wa_id)
    stmt = (
        select(Message)
        .where(Message.contact_id == contact.id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    messages = list(db.execute(stmt).scalars().all())
    messages.reverse()
    return {
        "wa_id": wa_id,
        "items": [
            {
                "id": m.id,
                "role": m.role.value,
                "content": m.content,
                "message_type": m.message_type,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.delete("/contacts/{wa_id}/messages", dependencies=[Depends(require_admin_key)])
def clear_contact_messages(wa_id: str, db: Session = Depends(get_db)) -> dict:
    contact = _require_contact(db, wa_id)
    count = clear_memory(db, contact)
    _record_action(db, "/memory clear (dashboard)", wa_id, f"cleared_{count}_messages")
    return {"wa_id": wa_id, "cleared": count}


# =============================================================================
# Approvals
# =============================================================================
@router.get("/approvals", dependencies=[Depends(require_admin_key)])
def list_approvals(
    db: Session = Depends(get_db),
    status_filter: Optional[str] = Query(default=None, alias="status"),
) -> dict:
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
    if status_filter:
        try:
            stmt = stmt.where(ApprovalRequest.status == ApprovalStatus(status_filter))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status '{status_filter}'.")
    requests = db.execute(stmt).scalars().all()
    items = []
    for r in requests:
        contact = db.get(Contact, r.contact_id)
        items.append(
            {
                "id": r.id,
                "wa_id": contact.wa_id if contact else None,
                "contact_name": contact.display_name if contact else None,
                "user_message": r.user_message,
                "reason": r.reason,
                "status": r.status.value,
                "action_summary": _approval_action_summary(r.status),
                "created_at": r.created_at.isoformat(),
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            }
        )
    return {"items": items}


def _approval_action_summary(status: ApprovalStatus) -> str:
    return {
        ApprovalStatus.PENDING: "Awaiting owner decision",
        ApprovalStatus.APPROVED: "AI reply sent to contact",
        ApprovalStatus.REJECTED: "Rejection sent to contact",
        ApprovalStatus.EXPIRED: "Expired before owner responded",
    }.get(status, status.value)


@router.post("/approvals/{request_id}/resolve", dependencies=[Depends(require_admin_key)])
def resolve_approval_request(request_id: int, body: ApprovalActionBody, db: Session = Depends(get_db)) -> dict:
    request = resolve_approval(db, request_id, approved=body.approve)
    if request is None:
        raise HTTPException(status_code=404, detail=f"No approval request with id {request_id}.")

    # Reuse the SAME follow-through logic the WhatsApp `/approve <id>` /
    # `/reject <id>` admin-command path uses (routes.py), so an approval
    # made from the dashboard actually sends the real AI reply (approved)
    # or the rejection message (rejected) to the original contact, and -
    # on approval - retries Smart Memory extraction now that the owner has
    # explicitly authorized it. Without this, resolve_approval() above only
    # flips the DB status and nothing is ever sent to the contact.
    import routes  # local import: avoids a circular import at module load time

    action_result = "already_resolved"
    if request.status in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        outcome = routes.finalize_approval_by_id(db, request_id)
        if outcome == ApprovalStatus.APPROVED:
            action_result = "ai_reply_sent"
        elif outcome == ApprovalStatus.REJECTED:
            action_result = "rejection_sent"
        else:
            action_result = "no_contact_found"

    _record_action(
        db,
        "/approve (dashboard)" if body.approve else "/reject (dashboard)",
        None,
        f"request_{request_id}_{request.status.value}_{action_result}",
    )
    return {"id": request_id, "status": request.status.value, "action_result": action_result}


# =============================================================================
# Human Override
# =============================================================================
@router.get("/overrides", dependencies=[Depends(require_admin_key)])
def list_overrides(db: Session = Depends(get_db)) -> dict:
    stmt = select(Contact).where(Contact.human_override_until.is_not(None))
    contacts = db.execute(stmt).scalars().all()
    return {
        "items": [
            {
                "wa_id": c.wa_id,
                "display_name": c.display_name,
                "active": c.is_human_override_active(),
                "expires_at": c.human_override_until.isoformat() if c.human_override_until else None,
            }
            for c in contacts
        ]
    }


@router.post("/contacts/{wa_id}/override", dependencies=[Depends(require_admin_key)])
def set_contact_override(wa_id: str, body: OverrideBody, db: Session = Depends(get_db)) -> dict:
    contact = _require_contact(db, wa_id)
    if body.action == "activate":
        expiry = activate_override(db, contact)
        _record_action(db, "/override activate (dashboard)", wa_id, expiry.isoformat())
        return {"wa_id": wa_id, "active": True, "expires_at": expiry.isoformat()}
    elif body.action == "clear":
        clear_override(db, contact)
        _record_action(db, "/override clear (dashboard)", wa_id, "cleared")
        return {"wa_id": wa_id, "active": False, "expires_at": None}
    raise HTTPException(status_code=400, detail="action must be 'activate' or 'clear'.")


# =============================================================================
# Smart Memory
# =============================================================================
@router.get("/contacts/{wa_id}/memories", dependencies=[Depends(require_admin_key)])
def list_contact_memories(
    wa_id: str, db: Session = Depends(get_db), search: Optional[str] = Query(default=None)
) -> dict:
    contact = _require_contact(db, wa_id)
    memories = search_memory(db, contact, search) if search else get_all_memory(db, contact)
    return {
        "wa_id": wa_id,
        "items": [
            {
                "id": m.id,
                "category": m.category.value,
                "subcategory": m.subcategory,
                "value": m.value,
                "importance": m.importance.value,
                "confidence": m.confidence,
                "retention_policy": m.retention_policy.value,
                "access_count": m.access_count,
                "updated_at": m.updated_at.isoformat(),
                "expires_at": m.expires_at.isoformat() if m.expires_at else None,
            }
            for m in memories
        ],
    }


@router.delete("/contacts/{wa_id}/memories/{memory_id}", dependencies=[Depends(require_admin_key)])
def delete_contact_memory(wa_id: str, memory_id: int, db: Session = Depends(get_db)) -> dict:
    contact = _require_contact(db, wa_id)
    count = delete_memory(db, contact, memory_id=memory_id)
    if count == 0:
        raise HTTPException(status_code=404, detail=f"No active memory #{memory_id} for contact {wa_id}.")
    _record_action(db, "/smartmemory delete (dashboard)", wa_id, f"deleted_memory_{memory_id}")
    return {"wa_id": wa_id, "deleted_id": memory_id}


# =============================================================================
# Working Hours / Settings / Profile
# =============================================================================
@router.get("/settings", dependencies=[Depends(require_admin_key)])
def get_settings() -> dict:
    return load_runtime_settings()


@router.patch("/settings", dependencies=[Depends(require_admin_key)])
def patch_settings(body: SettingsPatchBody, db: Session = Depends(get_db)) -> dict:
    updated = {}
    for dotted_key, value in body.updates.items():
        updated = update_runtime_setting(dotted_key, value)
    _record_action(db, "/set (dashboard)", None, ",".join(body.updates.keys()))
    return updated


@router.get("/profile", dependencies=[Depends(require_admin_key)])
def get_owner_profile() -> dict:
    return load_profile()


@router.patch("/profile", dependencies=[Depends(require_admin_key)])
def patch_owner_profile(body: ProfilePatchBody, db: Session = Depends(get_db)) -> dict:
    profile = load_profile()
    for field, value in body.updates.items():
        if field not in profile:
            raise HTTPException(status_code=400, detail=f"Unknown profile field '{field}'.")
        profile[field] = value
    save_profile(profile)
    _record_action(db, "/profile set (dashboard)", None, ",".join(body.updates.keys()))
    return profile


@router.post("/reload", dependencies=[Depends(require_admin_key)])
def reload_config(db: Session = Depends(get_db)) -> dict:
    reload_all()
    _record_action(db, "/reload (dashboard)", None, "ok")
    return {"reloaded": True, "at": datetime.now(timezone.utc).isoformat()}


# =============================================================================
# Logs / Activity
# =============================================================================
@router.get("/logs/admin-actions", dependencies=[Depends(require_admin_key)])
def list_admin_action_logs(db: Session = Depends(get_db), limit: int = Query(default=100, ge=1, le=500)) -> dict:
    stmt = select(AdminActionLog).order_by(AdminActionLog.created_at.desc()).limit(limit)
    logs = db.execute(stmt).scalars().all()
    return {
        "items": [
            {
                "id": l.id,
                "admin_wa_id": l.admin_wa_id,
                "command": l.command,
                "target_wa_id": l.target_wa_id,
                "result": l.result,
                "created_at": l.created_at.isoformat(),
            }
            for l in logs
        ]
    }


@router.get("/logs/requests", dependencies=[Depends(require_admin_key)])
def list_request_logs(db: Session = Depends(get_db), limit: int = Query(default=100, ge=1, le=500)) -> dict:
    stmt = select(RequestLog).order_by(RequestLog.created_at.desc()).limit(limit)
    logs = db.execute(stmt).scalars().all()
    return {
        "items": [
            {
                "id": l.id,
                "contact_wa_id": l.contact_wa_id,
                "status": l.status,
                "detail": l.detail,
                "response_time_ms": l.response_time_ms,
                "created_at": l.created_at.isoformat(),
            }
            for l in logs
        ]
    }


# =============================================================================
# Scheduled Messages
# =============================================================================
class ScheduledMessageCreateBody(BaseModel):
    contact_wa_id: str
    message_text: str
    scheduled_at: str  # ISO 8601
    timezone: str = "UTC"


def _scheduled_message_brief(m) -> dict:
    return {
        "id": m.id,
        "contact_wa_id": m.contact_wa_id,
        "message_text": m.message_text,
        "scheduled_at": m.scheduled_at.isoformat(),
        "timezone": m.timezone,
        "status": m.status.value,
        "error_detail": m.error_detail,
        "created_at": m.created_at.isoformat(),
        "sent_at": m.sent_at.isoformat() if m.sent_at else None,
        "failed_at": m.failed_at.isoformat() if m.failed_at else None,
        "cancelled_at": m.cancelled_at.isoformat() if m.cancelled_at else None,
    }


@router.get("/scheduled-messages", dependencies=[Depends(require_admin_key)])
def list_scheduled_messages(
    db: Session = Depends(get_db), status_filter: Optional[str] = Query(default=None, alias="status")
) -> dict:
    status_enum = None
    if status_filter:
        try:
            status_enum = ScheduledMessageStatus(status_filter)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status '{status_filter}'.")
    items = scheduled_messages.list_all(db, status=status_enum)
    return {"items": [_scheduled_message_brief(m) for m in items]}


@router.post("/scheduled-messages", dependencies=[Depends(require_admin_key)])
def create_scheduled_message(body: ScheduledMessageCreateBody, db: Session = Depends(get_db)) -> dict:
    try:
        scheduled_at = datetime.fromisoformat(body.scheduled_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="scheduled_at must be a valid ISO 8601 datetime.")
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    if not body.message_text.strip():
        raise HTTPException(status_code=400, detail="message_text cannot be empty.")
    entry = scheduled_messages.create(db, body.contact_wa_id, body.message_text, scheduled_at, body.timezone)
    _record_action(db, "/scheduled-message create (dashboard)", body.contact_wa_id, f"scheduled_#{entry.id}")
    return _scheduled_message_brief(entry)


@router.post("/scheduled-messages/{message_id}/cancel", dependencies=[Depends(require_admin_key)])
def cancel_scheduled_message(message_id: int, db: Session = Depends(get_db)) -> dict:
    entry = scheduled_messages.cancel(db, message_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No cancellable (pending) scheduled message with that id.")
    _record_action(db, "/scheduled-message cancel (dashboard)", entry.contact_wa_id, f"cancelled_#{message_id}")
    return _scheduled_message_brief(entry)


@router.post("/scheduled-messages/{message_id}/retry", dependencies=[Depends(require_admin_key)])
def retry_scheduled_message(message_id: int, db: Session = Depends(get_db)) -> dict:
    entry = scheduled_messages.retry(db, message_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No retryable (failed) scheduled message with that id.")
    _record_action(db, "/scheduled-message retry (dashboard)", entry.contact_wa_id, f"retried_#{message_id}")
    return _scheduled_message_brief(entry)
