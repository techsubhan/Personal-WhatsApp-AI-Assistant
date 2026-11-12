"""
admin.py
========
Admin command parsing and dispatch.

Any inbound WhatsApp message that starts with "/" AND comes from a phone
number listed in ADMIN_NUMBERS (.env) is treated as an admin command
instead of being sent to the AI. Commands are handled entirely here and
never reach prompt_builder.py / ai.py.

Supported commands (per specification)
---------------------------------------
    /ai on | /ai off                        - global AI switch
    /block <wa_id>                          - block a contact
    /unblock <wa_id>                        - unblock a contact
    /memory clear <wa_id>                   - clear a contact's conversation memory
    /profile                                - show the owner profile
    /profile set <field> <value...>         - update a single owner profile field
    /status                                 - show current system status
    /help                                   - list available commands
    /contact ai on <wa_id>                  - enable AI for one contact
    /contact ai off <wa_id>                 - disable AI for one contact
    /workinghours on | /workinghours off    - toggle working-hours enforcement
    /set starttime <HH:MM>                  - set working-hours start time
    /set endtime <HH:MM>                    - set working-hours end time
    /set awaymessage <text...>              - set the away message
    /reload                                 - reload dynamic configuration from disk

Additional commands required to complete the Sensitive Information /
Owner Approval feature (feature 12 of the specification), which the
original admin-command list did not explicitly enumerate but which are
necessary for that feature to function at all:
    /approve <request_id>                   - approve a pending sensitive request
    /reject <request_id>                    - reject a pending sensitive request

Smart Memory admin commands (new, see smart_memory.py)
---------------------------------------------------------
    /smartmemory show <wa_id>                    - show every active Smart Memory fact for a contact
    /smartmemory list <wa_id> [category]          - list facts, optionally filtered by category
    /smartmemory search <wa_id> <query>           - semantic-ish search over a contact's facts
    /smartmemory delete <wa_id> <query>           - delete fact(s) matching a query
    /smartmemory clear <wa_id>                    - delete ALL Smart Memory facts for a contact

Backward-compatible /memory aliases (both command families are supported)
------------------------------------------------------------------------------
    /memory show <wa_id>                          - exact alias of /smartmemory show
    /memory list <wa_id> [category]                - exact alias of /smartmemory list
    /memory search <wa_id> <query>                  - exact alias of /smartmemory search
    /memory delete <wa_id> <query>                   - exact alias of /smartmemory delete
    /memory clear <wa_id>                             - UNCHANGED: clears Conversation History
                                                          (NOT an alias of /smartmemory clear - see note below)

Naming note (deliberate, necessary deviation from the literal spec wording):
the project specification asks for these as plain "/memory show", "/memory
list", "/memory search", "/memory delete", "/memory clear", aliased 1:1 onto
Smart Memory. Four of those five are supported exactly that way above.
"/memory clear <wa_id>" is the one exception: it already existed, with an
already-documented, already-relied-upon meaning (clear this contact's
Conversation History). Its call shape is syntactically identical to what a
Smart-Memory-clear alias would look like ("/memory clear <wa_id>" either
way), so there is no way to alias it to Smart Memory's "clear" without
silently changing what the existing command does for every admin who
already uses it - and "never break existing conversation history" /
backward compatibility were explicit, standing requirements for this
project. Backward compatibility wins: "/memory clear" keeps clearing
Conversation History, exactly as before. To clear ALL Smart Memory facts
for a contact, use "/smartmemory clear <wa_id>" (unambiguous, unaffected).
See README.md for the same note.
"""

from __future__ import annotations

from typing import List, Optional

from approval import resolve_approval
from config import (
    load_profile,
    load_runtime_settings,
    reload_all,
    save_profile,
    update_runtime_setting,
    settings as static_settings,
)
from contacts import (
    block_contact,
    get_contact,
    set_contact_ai_enabled,
    unblock_contact,
)
from logger import get_logger, log_admin_command
from memory import clear_memory
from models import AdminActionLog, MemoryCategory
from sqlalchemy.orm import Session
import smart_memory

logger = get_logger("admin")

HELP_TEXT = """Available admin commands:
/ai on | /ai off
/block <wa_id>
/unblock <wa_id>
/memory clear <wa_id>                  (Conversation History)
/profile
/profile set <field> <value>
/status
/help
/contact ai on <wa_id>
/contact ai off <wa_id>
/workinghours on | /workinghours off
/set starttime <HH:MM>
/set endtime <HH:MM>
/set awaymessage <text>
/approve <request_id>
/reject <request_id>
/smartmemory show <wa_id>              (alias: /memory show <wa_id>)
/smartmemory list <wa_id> [category]   (alias: /memory list <wa_id> [category])
/smartmemory search <wa_id> <query>    (alias: /memory search <wa_id> <query>)
/smartmemory delete <wa_id> <query>    (alias: /memory delete <wa_id> <query>)
/smartmemory clear <wa_id>             (deletes ALL Smart Memory facts - NOT aliased to /memory clear)
/reload"""


def is_admin(wa_id: str) -> bool:
    """Return True if the given WhatsApp ID is a configured admin number."""
    return wa_id in static_settings.admin_numbers


def is_command_text(text: str) -> bool:
    """Return True if a message body looks like an admin command (starts with '/')."""
    return bool(text) and text.strip().startswith("/")


def _record(db: Session, admin_wa_id: str, command: str, target_wa_id: Optional[str], result: str) -> None:
    db.add(
        AdminActionLog(
            admin_wa_id=admin_wa_id,
            command=command,
            target_wa_id=target_wa_id,
            result=result,
        )
    )
    db.commit()
    log_admin_command(admin_wa_id, command, result)


def handle_admin_command(db: Session, admin_wa_id: str, raw_text: str) -> str:
    """
    Parse and execute an admin command, returning the text to reply to the
    admin with. Never raises - any internal error is caught and reported
    back as a normal chat reply so the admin always gets feedback.
    """
    try:
        return _dispatch(db, admin_wa_id, raw_text)
    except Exception:  # noqa: BLE001 - admin commands must never crash the webhook handler
        logger.error("Unhandled error while processing admin command '%s'", raw_text, exc_info=True)
        return "Something went wrong processing that command. Check the logs for details."


def _dispatch(db: Session, admin_wa_id: str, raw_text: str) -> str:
    parts: List[str] = raw_text.strip().split()
    command = parts[0].lower()
    args = parts[1:]

    if command == "/help":
        _record(db, admin_wa_id, "/help", None, "ok")
        return HELP_TEXT

    if command == "/ai" and args:
        return _handle_global_ai_switch(db, admin_wa_id, args)

    if command == "/block" and args:
        return _handle_block(db, admin_wa_id, args, block=True)

    if command == "/unblock" and args:
        return _handle_block(db, admin_wa_id, args, block=False)

    if command == "/memory" and args:
        return _handle_memory_command(db, admin_wa_id, args)

    if command == "/profile":
        return _handle_profile(db, admin_wa_id, args)

    if command == "/status":
        return _handle_status(db, admin_wa_id)

    if command == "/contact" and len(args) >= 2 and args[0].lower() == "ai":
        return _handle_contact_ai(db, admin_wa_id, args[1:])

    if command == "/workinghours" and args:
        return _handle_working_hours_toggle(db, admin_wa_id, args)

    if command == "/set" and args:
        return _handle_set(db, admin_wa_id, args)

    if command == "/approve" and args:
        return _handle_approval(db, admin_wa_id, args, approve=True)

    if command == "/reject" and args:
        return _handle_approval(db, admin_wa_id, args, approve=False)

    if command == "/smartmemory" and args:
        return _handle_smart_memory(db, admin_wa_id, args)

    if command == "/reload":
        reload_all()
        _record(db, admin_wa_id, "/reload", None, "ok")
        return "Configuration reloaded from disk."

    _record(db, admin_wa_id, command, None, "unknown_command")
    return f"Unknown or incomplete command: '{raw_text}'.\n\n{HELP_TEXT}"


# ---------------------------------------------------------------------------
# Individual command handlers
# ---------------------------------------------------------------------------
def _handle_global_ai_switch(db: Session, admin_wa_id: str, args: List[str]) -> str:
    from models import GlobalSetting

    state = args[0].lower()
    if state not in ("on", "off"):
        return "Usage: /ai on | /ai off"

    enabled = state == "on"
    setting = db.get(GlobalSetting, GlobalSetting.KEY_GLOBAL_AI_ENABLED)
    if setting is None:
        setting = GlobalSetting(key=GlobalSetting.KEY_GLOBAL_AI_ENABLED, value=str(enabled))
        db.add(setting)
    else:
        setting.value = str(enabled)
    db.commit()

    _record(db, admin_wa_id, "/ai", None, state)
    return f"Global AI is now {'ON' if enabled else 'OFF'}."


def _handle_block(db: Session, admin_wa_id: str, args: List[str], block: bool) -> str:
    target = args[0]
    ok = block_contact(db, target) if block else unblock_contact(db, target)
    result = "ok" if ok else "contact_not_found"
    _record(db, admin_wa_id, "/block" if block else "/unblock", target, result)
    if not ok:
        return f"No known contact with number {target}."
    return f"Contact {target} has been {'blocked' if block else 'unblocked'}."


def _handle_memory_command(db: Session, admin_wa_id: str, args: List[str]) -> str:
    """
    Dispatch for `/memory <action> <wa_id> [...]`.

    Backward-compatible alias behavior
    -------------------------------------
    `/memory show`, `/memory list`, `/memory search`, and `/memory delete`
    are accepted as exact aliases of the corresponding `/smartmemory ...`
    commands (see `_handle_smart_memory`) - these four action words had no
    prior meaning under `/memory`, so aliasing them introduces no ambiguity
    and no behavior change for anyone already using this project.

    `/memory clear <wa_id>` is the one deliberate exception: it already
    existed, with an already-documented, already-relied-upon meaning
    (clear this contact's Conversation History - see `_handle_memory_clear`
    / `memory.clear_memory`). Its call shape (`/memory clear <wa_id>`) is
    syntactically identical to what a Smart-Memory-clear alias would look
    like, so there is no way to alias it to Smart Memory's `clear` without
    silently changing what the existing command does for every admin who
    already uses it. Backward compatibility wins here: `/memory clear`
    keeps clearing Conversation History, exactly as before. Smart Memory's
    clear-all-facts action remains available, unambiguously, under
    `/smartmemory clear <wa_id>`.
    """
    action = args[0].lower()

    if action == "clear":
        return _handle_memory_clear(db, admin_wa_id, args[1:])

    if action in ("show", "list", "search", "delete"):
        return _handle_smart_memory(db, admin_wa_id, args, command_label="/memory")

    return (
        "Usage:\n"
        "/memory clear <wa_id>                          - clear Conversation History\n"
        "/memory show <wa_id>                            - alias of /smartmemory show\n"
        "/memory list <wa_id> [category]                 - alias of /smartmemory list\n"
        "/memory search <wa_id> <query>                  - alias of /smartmemory search\n"
        "/memory delete <wa_id> <query>                  - alias of /smartmemory delete\n"
        "(To clear ALL Smart Memory facts for a contact, use /smartmemory clear <wa_id>.)"
    )


def _handle_memory_clear(db: Session, admin_wa_id: str, args: List[str]) -> str:
    if not args:
        return "Usage: /memory clear <wa_id>"
    target = args[0]
    contact = get_contact(db, target)
    if contact is None:
        _record(db, admin_wa_id, "/memory clear", target, "contact_not_found")
        return f"No known contact with number {target}."
    count = clear_memory(db, contact)
    _record(db, admin_wa_id, "/memory clear", target, f"cleared_{count}_messages")
    return f"Cleared {count} stored message(s) for {target}."


def _handle_profile(db: Session, admin_wa_id: str, args: List[str]) -> str:
    profile = load_profile()

    if not args:
        lines = [f"{key}: {value}" for key, value in profile.items()]
        _record(db, admin_wa_id, "/profile", None, "shown")
        return "Owner profile:\n" + "\n".join(lines)

    if args[0].lower() == "set" and len(args) >= 3:
        field = args[1]
        value = " ".join(args[2:])
        if field not in profile:
            return f"Unknown profile field '{field}'. Known fields: {', '.join(profile.keys())}"
        profile[field] = value
        save_profile(profile)
        _record(db, admin_wa_id, "/profile set", None, f"{field}={value}")
        return f"Updated profile field '{field}'."

    return "Usage: /profile  OR  /profile set <field> <value>"


def _handle_status(db: Session, admin_wa_id: str) -> str:
    from models import GlobalSetting

    setting = db.get(GlobalSetting, GlobalSetting.KEY_GLOBAL_AI_ENABLED)
    global_ai_enabled = True if setting is None else setting.value == "True"

    runtime_settings = load_runtime_settings()
    wh = runtime_settings.get("working_hours", {})

    lines = [
        f"Global AI: {'ON' if global_ai_enabled else 'OFF'}",
        f"Working hours enabled: {wh.get('enabled', False)}",
        f"Working hours: {wh.get('start_time')} - {wh.get('end_time')} ({wh.get('timezone')})",
        f"Outside-hours action: {wh.get('outside_hours_action')}",
        f"Ignore group messages: {static_settings.ignore_group_messages}",
        f"Rate limit: {static_settings.rate_limit_messages_per_minute} msgs/min",
        f"Primary AI model: {static_settings.openrouter_primary_model}",
        f"Fallback AI model: {static_settings.openrouter_fallback_model}",
    ]
    _record(db, admin_wa_id, "/status", None, "shown")
    return "System status:\n" + "\n".join(lines)


def _handle_contact_ai(db: Session, admin_wa_id: str, args: List[str]) -> str:
    if len(args) < 2:
        return "Usage: /contact ai on <wa_id>  OR  /contact ai off <wa_id>"
    state = args[0].lower()
    target = args[1]
    if state not in ("on", "off"):
        return "Usage: /contact ai on <wa_id>  OR  /contact ai off <wa_id>"

    ok = set_contact_ai_enabled(db, target, enabled=(state == "on"))
    result = "ok" if ok else "contact_not_found"
    _record(db, admin_wa_id, "/contact ai", target, result)
    if not ok:
        return f"No known contact with number {target}."
    return f"AI is now {'ON' if state == 'on' else 'OFF'} for contact {target}."


def _handle_working_hours_toggle(db: Session, admin_wa_id: str, args: List[str]) -> str:
    state = args[0].lower()
    if state not in ("on", "off"):
        return "Usage: /workinghours on | /workinghours off"
    update_runtime_setting("working_hours.enabled", state == "on")
    _record(db, admin_wa_id, "/workinghours", None, state)
    return f"Working hours enforcement is now {'ON' if state == 'on' else 'OFF'}."


def _handle_set(db: Session, admin_wa_id: str, args: List[str]) -> str:
    key = args[0].lower()
    rest = args[1:]

    if key == "starttime" and rest:
        update_runtime_setting("working_hours.start_time", rest[0])
        _record(db, admin_wa_id, "/set starttime", None, rest[0])
        return f"Working hours start time set to {rest[0]}."

    if key == "endtime" and rest:
        update_runtime_setting("working_hours.end_time", rest[0])
        _record(db, admin_wa_id, "/set endtime", None, rest[0])
        return f"Working hours end time set to {rest[0]}."

    if key == "awaymessage" and rest:
        message = " ".join(rest)
        update_runtime_setting("working_hours.away_message", message)
        _record(db, admin_wa_id, "/set awaymessage", None, message)
        return "Away message updated."

    return "Usage: /set starttime <HH:MM> | /set endtime <HH:MM> | /set awaymessage <text>"


def _handle_approval(db: Session, admin_wa_id: str, args: List[str], approve: bool) -> str:
    try:
        request_id = int(args[0])
    except ValueError:
        return f"Usage: /{'approve' if approve else 'reject'} <request_id>"

    request = resolve_approval(db, request_id, approved=approve)
    _record(
        db, admin_wa_id, "/approve" if approve else "/reject", None,
        f"request_{request_id}_{'approved' if approve else 'rejected'}" if request else "not_found",
    )
    if request is None:
        return f"No approval request found with id {request_id}."
    return f"Request #{request_id} has been {'approved' if approve else 'rejected'}."


def _format_memory_line(memory) -> str:
    """Render a single UserMemory row as one compact, human-readable line."""
    return (
        f"#{memory.id} [{memory.category.value}/{memory.subcategory}] "
        f"= {memory.value}  (importance={memory.importance.value}, "
        f"confidence={memory.confidence:.2f}, used={memory.access_count}x)"
    )


def _handle_smart_memory(db: Session, admin_wa_id: str, args: List[str], command_label: str = "/smartmemory") -> str:
    """
    Dispatch for the `/smartmemory <action> <wa_id> [...]` command family.
    See the module docstring for why this is namespaced separately from
    the pre-existing `/memory clear <wa_id>` (Conversation History) command.

    `command_label` controls only what gets written to the AdminActionLog
    audit trail (e.g. "/memory" vs "/smartmemory") so that when this same
    logic is invoked via the backward-compatible `/memory show|list|search|
    delete` aliases (see `_handle_memory_command`), the audit log
    accurately reflects which spelling the admin actually typed, rather
    than always showing "/smartmemory" regardless of entry point.
    """
    if len(args) < 2:
        return (
            f"Usage: {command_label} <show|list|search|delete|clear> <wa_id> [category|query]"
        )

    action = args[0].lower()
    target_wa_id = args[1]
    rest = args[2:]

    contact = get_contact(db, target_wa_id)
    if contact is None:
        _record(db, admin_wa_id, f"{command_label} {action}", target_wa_id, "contact_not_found")
        return f"No known contact with number {target_wa_id}."

    if action == "show":
        memories = smart_memory.get_all_memory(db, contact)
        _record(db, admin_wa_id, f"{command_label} show", target_wa_id, f"{len(memories)}_shown")
        if not memories:
            return f"No Smart Memory facts stored for {target_wa_id}."
        lines = [_format_memory_line(m) for m in memories]
        return f"Smart Memory for {target_wa_id} ({len(memories)} fact(s)):\n" + "\n".join(lines)

    if action == "list":
        category_filter: Optional[MemoryCategory] = None
        if rest:
            try:
                category_filter = MemoryCategory(rest[0].lower().replace(" ", "_"))
            except ValueError:
                valid = ", ".join(c.value for c in MemoryCategory)
                return f"Unknown category '{rest[0]}'. Valid categories: {valid}"
        memories = smart_memory.get_all_memory(db, contact, category=category_filter)
        _record(db, admin_wa_id, f"{command_label} list", target_wa_id, f"{len(memories)}_listed")
        if not memories:
            scope = f" in category '{category_filter.value}'" if category_filter else ""
            return f"No Smart Memory facts{scope} for {target_wa_id}."
        lines = [_format_memory_line(m) for m in memories]
        return f"Smart Memory for {target_wa_id}:\n" + "\n".join(lines)

    if action == "search":
        if not rest:
            return f"Usage: {command_label} search <wa_id> <query>"
        query = " ".join(rest)
        memories = smart_memory.search_memory(db, contact, query)
        _record(db, admin_wa_id, f"{command_label} search", target_wa_id, f"query={query}:{len(memories)}_found")
        if not memories:
            return f"No Smart Memory facts matched '{query}' for {target_wa_id}."
        lines = [_format_memory_line(m) for m in memories]
        return f"Matches for '{query}':\n" + "\n".join(lines)

    if action == "delete":
        if not rest:
            return f"Usage: {command_label} delete <wa_id> <query>"
        query = " ".join(rest)
        deleted_count = smart_memory.delete_memory(db, contact, query_text=query)
        _record(db, admin_wa_id, f"{command_label} delete", target_wa_id, f"query={query}:{deleted_count}_deleted")
        if deleted_count == 0:
            return f"No Smart Memory facts matched '{query}' for {target_wa_id}; nothing deleted."
        return f"Deleted {deleted_count} Smart Memory fact(s) matching '{query}' for {target_wa_id}."

    if action == "clear":
        all_memories = smart_memory.get_all_memory(db, contact)
        deleted_count = 0
        for memory in all_memories:
            deleted_count += smart_memory.delete_memory(db, contact, memory_id=memory.id)
        _record(db, admin_wa_id, f"{command_label} clear", target_wa_id, f"{deleted_count}_deleted")
        return f"Cleared all {deleted_count} Smart Memory fact(s) for {target_wa_id}."

    return f"Usage: {command_label} <show|list|search|delete|clear> <wa_id> [category|query]"
