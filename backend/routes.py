"""
routes.py
=========
The core message-processing pipeline. This module implements the exact
processing flow from the specification, step by step, wiring together
every other module in the project. `webhook.py` is intentionally thin: it
only handles HTTP/webhook transport concerns (verification, parsing) and
delegates each individual inbound message to `process_inbound_message()`
below.

Processing order implemented here
-----------------------------------
 1. Admin command check
 2. Owner "human override" detection (message-echo best-effort, see note)
 3. Group detection
 4. Global AI switch
 5. Working hours
 6. Contact checker (unknown / saved / blocked)
 7. Per-contact AI switch
 8. Human override (existing pause window)
 9. Rate limiter
10. Spam detection
11. Sensitive information detection -> owner approval workflow
12. Typing delay
13. Prompt building
14. AI generation (with automatic fallback, handled inside ai.py)
15. Secret-leak scan
16. Save conversation memory
17. Send WhatsApp reply
18. Request logging

Note on human-override / message echoes
-----------------------------------------
The WhatsApp Business Cloud API does not have a single universally
documented schema for "the owner manually replied from the WhatsApp
Business App" (this depends on whether Cloud API + Business App
coexistence is enabled for the account). This module makes a best-effort
attempt to detect it by checking whether the inbound event's sender
matches OWNER_WHATSAPP_NUMBER and whether a recipient id is present in the
raw payload (`to` / `recipient_id`), which is how several regional
coexistence payload variants surface it. If your account's specific
payload shape differs, adjust `_extract_echo_recipient()` accordingly.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import admin
import contacts
import human_override
import language
import security
import smart_memory
import typing_delay
import voice_input
import voice_reply
import web_search
import working_hours
from ai import AIGenerationError, openrouter_client
from approval import (
    REJECTION_MESSAGE,
    build_owner_notification,
    create_approval_request,
    get_request_by_id,
)
from config import load_profile, settings
from logger import get_logger, log_ai_error, log_error, log_reply, log_request, log_warning
from memory import add_message, get_conversation_history_for_prompt
from models import ApprovalStatus, Contact, MessageRole, ProcessedWebhookMessage, RequestLog
from prompt_builder import build_messages
from rate_limiter import rate_limiter
from smart_memory import extract_memories_from_message
from spam_detector import spam_detector
from utils import Timer, normalize_wa_id, safe_truncate_for_log
from whatsapp import MessageType, ParsedMessage, whatsapp_client

logger = get_logger("routes")

# Human handoff acknowledgment, sent immediately when
# security.is_human_handoff_request() matches (see Step 8c below), keyed
# by language.detect()'s label so the contact gets it in roughly their
# own language/script without a separate AI call (a handoff should be
# instant, not wait on a model response).
HUMAN_HANDOFF_ACK = {
    "english": "Got it - I've paused so the owner can reply to you personally. They'll be with you shortly.",
    "urdu": "ٹھیک ہے، میں رک گیا ہوں تاکہ مالک آپ سے خود بات کر سکیں۔ وہ جلد آپ سے رابطہ کریں گے۔",
    "roman_urdu": "Theek hai, main ruk gaya hoon taake owner aap se khud baat kar sakein. Woh jald aap se rabta karenge.",
    "punjabi": "ٹھیک اے، میں رک گیا واں تاں جو مالک تہاڈے نال خود گل کر سکن۔",
    "roman_punjabi": "Theek ae, main ruk gaya waan taan jo owner tuhade naal khud gal kar sakan.",
    "arabic": "حسنًا، لقد توقفت ليتمكن المالك من الرد عليك شخصيًا. سيتواصل معك قريبًا.",
    "german": "Verstanden - ich habe pausiert, damit der Inhaber Ihnen persönlich antworten kann. Er meldet sich in Kürze.",
}


def _get_message_text(parsed: ParsedMessage) -> Optional[str]:
    """Extract a usable text string from a parsed message, if any."""
    if parsed.message_type == MessageType.TEXT:
        return parsed.text_body
    if parsed.message_type == MessageType.INTERACTIVE:
        return parsed.interactive_title or parsed.interactive_id
    return None


def _is_ai_answerable(parsed: ParsedMessage) -> bool:
    """
    Only plain text messages and interactive button/list replies go
    through the full AI pipeline. Media/location/contacts messages are
    parsed (whatsapp.py must be able to parse every supported type) but
    are not auto-answered, per the "Ignore unsupported messages" feature.
    """
    return parsed.message_type in (MessageType.TEXT, MessageType.INTERACTIVE)


def _extract_echo_recipient(parsed: ParsedMessage) -> Optional[str]:
    """Best-effort extraction of the intended recipient from an owner message-echo event."""
    raw = parsed.raw or {}
    candidate = raw.get("to") or raw.get("recipient_id")
    if isinstance(candidate, str) and candidate:
        return normalize_wa_id(candidate)
    return None


def _log_request(
    db: Session,
    parsed: Optional[ParsedMessage],
    status: str,
    detail: str,
    elapsed_ms: Optional[int] = None,
) -> None:
    """Persist a compact RequestLog row and emit a structured log line."""
    wa_id = parsed.wa_id if parsed else None
    message_id = parsed.message_id if parsed else None
    try:
        db.add(
            RequestLog(
                whatsapp_message_id=message_id,
                contact_wa_id=wa_id,
                status=status,
                detail=safe_truncate_for_log(detail, 500),
                response_time_ms=elapsed_ms,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 - logging must never break the pipeline
        db.rollback()
        log_error("Failed to persist RequestLog row.", exc_info=True)
    log_request(wa_id or "unknown", status, detail)


def _try_claim_message(db: Session, wamid: str) -> bool:
    """
    Atomically claim a WhatsApp message id for processing. Returns True
    if this call successfully claimed it (i.e. it has never been claimed
    before) and processing should continue; False if it was already
    claimed (by this request or a concurrent/duplicate one) and the
    caller must do nothing further.

    Relies entirely on ProcessedWebhookMessage.wamid's UNIQUE/primary-key
    constraint for atomicity - see that model's docstring in models.py.
    Commits immediately as its own tiny transaction (independent of
    whatever else this request does with `db` afterwards), so a
    concurrent request's claim attempt is guaranteed to see it right
    away, even under SQLite's WAL mode.
    """
    db.add(ProcessedWebhookMessage(wamid=wamid))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def process_inbound_message(db: Session, parsed: ParsedMessage) -> None:
    """
    Run a single inbound WhatsApp message through the full processing
    pipeline. Never raises - all errors are caught, logged, and recorded
    so a single bad message can never take down the webhook handler.

    Idempotency: the very first thing this function does is atomically
    claim `parsed.message_id` (the WhatsApp wamid). If it has already
    been claimed - by an earlier delivery of the same webhook event, or
    by a concurrent duplicate request racing this one - processing stops
    immediately, before any AI call or WhatsApp send, and nothing else
    happens. See _try_claim_message() / ProcessedWebhookMessage.
    """
    if parsed.message_id:
        if not _try_claim_message(db, parsed.message_id):
            # A RequestLog row for this exact wamid may already exist from
            # the original (successful) processing - RequestLog itself
            # has a unique constraint on whatsapp_message_id, so a DB
            # write here would collide by design. The file/console log
            # line below is sufficient visibility for a duplicate that,
            # by definition, did nothing further.
            logger.info("Ignoring duplicate webhook delivery for message %s.", parsed.message_id)
            log_request(parsed.wa_id or "unknown", "ignored", "duplicate_webhook_ignored")
            return
    else:
        logger.warning("Inbound message has no wamid; cannot deduplicate it.")

    with Timer() as t:
        try:
            _process_inbound_message_inner(db, parsed)
        except Exception:  # noqa: BLE001 - top-level safety net
            log_error(f"Unhandled error processing message {parsed.message_id}", exc_info=True)
            _log_request(db, parsed, "error", "unhandled_exception", t.elapsed_ms)
            return
    # _process_inbound_message_inner is responsible for its own success/ignored logging.


def _process_inbound_message_inner(db: Session, parsed: ParsedMessage) -> None:
    wa_id = normalize_wa_id(parsed.wa_id)
    text = _get_message_text(parsed)

    # -----------------------------------------------------------------
    # Step 1: Admin command check
    # -----------------------------------------------------------------
    if text and admin.is_admin(wa_id) and admin.is_command_text(text):
        # Mark message as read so the admin sees the tick even for commands.
        try:
            whatsapp_client.mark_as_read(parsed.message_id)
        except Exception:  # noqa: BLE001
            pass

        reply_text = admin.handle_admin_command(db, wa_id, text)
        try:
            whatsapp_client.send_text_message(wa_id, reply_text)
        except Exception:  # noqa: BLE001
            log_error("Failed to send admin command reply.", exc_info=True)

        # If this was an approval decision, follow through by notifying
        # the original contact whose message triggered the approval flow.
        lowered = text.strip().lower()
        if lowered.startswith("/approve") or lowered.startswith("/reject"):
            _finalize_approval_followup(db, text)

        _log_request(db, parsed, "success", "admin_command")
        return

    # -----------------------------------------------------------------
    # Step 2: Owner human-override detection (message echo, best effort)
    # -----------------------------------------------------------------
    if human_override.is_owner_authored_message(wa_id):
        recipient_wa_id = _extract_echo_recipient(parsed)
        if recipient_wa_id:
            target_contact, _ = contacts.get_or_create_contact(db, recipient_wa_id)
            human_override.activate_override(db, target_contact)
            if text:
                add_message(db, target_contact, MessageRole.OWNER, text)
            _log_request(db, parsed, "ignored", "owner_message_echo_override_activated")
        else:
            _log_request(db, parsed, "ignored", "owner_message_echo_no_recipient_found")
        return

    # -----------------------------------------------------------------
    # Step 3: Group detection
    # -----------------------------------------------------------------
    if parsed.is_group and settings.ignore_group_messages:
        _log_request(db, parsed, "ignored", "group_message_ignored")
        return

    # -----------------------------------------------------------------
    # Step 4: Global AI switch
    # -----------------------------------------------------------------
    if not _is_global_ai_enabled(db):
        _log_request(db, parsed, "ignored", "global_ai_off")
        return

    # -----------------------------------------------------------------
    # Step 5: Working hours (only meaningful when we have text to check
    # for emergency keywords against; media-only messages are evaluated
    # with an empty string, which simply cannot match an emergency keyword)
    # -----------------------------------------------------------------
    wh_decision = working_hours.evaluate(text or "")
    if not wh_decision.allowed:
        if wh_decision.reply_text:
            _safe_send(wa_id, wh_decision.reply_text, parsed.message_id)
        _log_request(db, parsed, "ignored", f"outside_working_hours:{wh_decision.reason}")
        return

    # -----------------------------------------------------------------
    # Step 6: Contact checker (unknown / saved / blocked)
    # -----------------------------------------------------------------
    contact, _created = contacts.get_or_create_contact(db, wa_id, parsed.sender_name)
    contacts.touch_last_message_time(db, contact)

    if contacts.is_blocked(contact):
        _log_request(db, parsed, "ignored", "contact_blocked")
        return

    welcome_text = contacts.handle_new_contact_welcome(db, contact)
    if welcome_text:
        _safe_send(wa_id, welcome_text, parsed.message_id)
        # Per spec, processing continues after the welcome message.

    # -----------------------------------------------------------------
    # Step 7: Per-contact AI switch
    # -----------------------------------------------------------------
    if not contacts.is_ai_enabled_for_contact(contact):
        _log_request(db, parsed, "ignored", "contact_ai_disabled")
        return

    # -----------------------------------------------------------------
    # Step 8: Human override (existing pause window for this contact)
    # -----------------------------------------------------------------
    if human_override.is_override_active(contact):
        _log_request(db, parsed, "ignored", "human_override_active")
        return

    # -----------------------------------------------------------------
    # Step 8b: Incoming voice transcription (optional, OFF by default -
    # see voice_input.py). Runs only after every gate above already
    # passed (blocked/AI-disabled/override-active contacts never reach
    # here), so it inherits the same gates as every other message type
    # with no duplicated checks. On success, the transcript is treated
    # exactly like a normal typed text message for the rest of this
    # function - same variable, same pipeline, no parallel logic.
    # -----------------------------------------------------------------
    if parsed.message_type == MessageType.AUDIO and text is None:
        text = voice_input.maybe_transcribe(parsed)

    # -----------------------------------------------------------------
    # Only text / interactive-reply messages (including successfully
    # transcribed voice messages) continue to the AI pipeline.
    # -----------------------------------------------------------------
    if not _is_ai_answerable(parsed) and text is None:
        _log_request(db, parsed, "ignored", f"unsupported_message_type:{parsed.message_type.value}")
        return

    if not text or not text.strip():
        reason = "voice_transcription_unavailable" if parsed.message_type == MessageType.AUDIO else "empty_text_body"
        _log_request(db, parsed, "ignored", reason)
        return

    text = security.sanitize_input(text)
    security.detect_prompt_injection(text)  # logs a warning internally if matched

    # Save the inbound message to Conversation History BEFORE any of the
    # early-return branches below (human handoff, contact-save, sensitive
    # approval) so the dashboard's conversation view always shows what the
    # contact actually said, regardless of which path handles it next.
    # (BUG FIX: this used to happen further down, after the human-handoff
    # check's early return - meaning a handoff request like "Mujhe Subhan
    # se baat karni hai" was never saved to history, only the assistant's
    # acknowledgment was. Rate-limited/spam-muted messages still correctly
    # skip this, by original design, since those returns happen earlier.)
    add_message(db, contact, MessageRole.USER, text, message_type=parsed.message_type.value,
                whatsapp_message_id=parsed.message_id)

    # Centralized language/script labeling (see language.py) - stored on
    # the contact for the dashboard; does NOT change how the AI replies
    # (that's still the system prompt's "mirror their language"
    # instruction - see language.py's module docstring for why).
    detected = language.detect(text)
    if contact.detected_language != detected.language:
        contact.detected_language = detected.language
        db.commit()

    # -----------------------------------------------------------------
    # Step 8c: Human handoff detection - "I want to talk to a human"
    # style requests activate the existing Human Override mechanism
    # immediately (contacts.py / human_override.py, unchanged) instead of
    # continuing to the AI. Reuses that system entirely - no second
    # override mechanism.
    # -----------------------------------------------------------------
    if security.is_human_handoff_request(text, owner_name=load_profile().get("name")):
        human_override.activate_override(db, contact)
        handoff_ack = HUMAN_HANDOFF_ACK.get(detected.language, HUMAN_HANDOFF_ACK["english"])
        _safe_send(wa_id, handoff_ack, parsed.message_id)
        add_message(db, contact, MessageRole.ASSISTANT, handoff_ack)
        if settings.owner_whatsapp_number:
            try:
                whatsapp_client.send_text_message(
                    settings.owner_whatsapp_number,
                    f"Human handoff requested by {contact.display_name or wa_id} ({wa_id}). AI paused for this contact.",
                )
            except Exception:  # noqa: BLE001
                log_error("Failed to notify owner of human handoff request.", exc_info=True)
        _log_request(db, parsed, "success", "human_handoff_requested")
        return

    # -----------------------------------------------------------------
    # Step 9: Rate limiter
    # -----------------------------------------------------------------
    rl_result = rate_limiter.check(wa_id)
    if not rl_result.allowed:
        _log_request(db, parsed, "ignored", f"rate_limited:{rl_result.reason}")
        return

    # -----------------------------------------------------------------
    # Step 10: Spam detection
    # -----------------------------------------------------------------
    spam_result = spam_detector.check(wa_id, text)
    if spam_result.action == "mute":
        _log_request(db, parsed, "ignored", "spam_muted")
        return
    if spam_result.action == "warn":
        _safe_send(wa_id, spam_result.reply_text or "", parsed.message_id)
        _log_request(db, parsed, "ignored", "spam_warning_sent")
        return

    # -----------------------------------------------------------------
    # Step 11: Sensitive information detection -> owner approval
    # (Conversation History for this message was already saved above,
    # right after language detection - see the comment there.)
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # Step 11a: Explicit "save <phone> as <name>" contact command.
    # Handled deterministically (not via the LLM) so the confirmation is
    # always accurate - see smart_memory.try_save_named_contact() for the
    # storage design. Short-circuits the rest of the pipeline (including
    # the sensitive-info/approval check and the AI reply) exactly like the
    # existing "forget ..." command does.
    # -----------------------------------------------------------------
    saved_contact = smart_memory.try_save_named_contact(db, contact, text, message_id=parsed.message_id)
    if saved_contact is not None:
        phone, name = saved_contact
        confirmation = f"Saved. {name} is now stored as {phone}."
        _safe_send(wa_id, confirmation, parsed.message_id)
        add_message(db, contact, MessageRole.ASSISTANT, confirmation)
        _log_request(db, parsed, "success", f"contact_saved:{phone}")
        return

    # Smart Memory: analyze every incoming user message for durable facts
    # worth remembering (or explicit "forget ..." instructions to act on
    # immediately). This is completely independent of Conversation History
    # above - see smart_memory.py / memory.py module docstrings - and is
    # wrapped defensively so a Smart Memory failure (e.g. a transient AI
    # extraction error) can never block the normal reply pipeline below.
    try:
        extract_memories_from_message(db, contact, text, message_id=parsed.message_id)
    except Exception:  # noqa: BLE001 - Smart Memory must never break the main pipeline
        log_error("Smart Memory extraction failed for an inbound message.", exc_info=True)

    sensitive_match = security.detect_sensitive_request(text)
    if sensitive_match is not None:
        # The approval request is ALWAYS created and immediately visible in
        # the admin dashboard's Approvals page - that is the primary review
        # surface now that a dashboard exists. Sending a WhatsApp ping to
        # OWNER_WHATSAPP_NUMBER is a best-effort convenience notification
        # on top of that, not a precondition for the approval workflow to
        # function. Previously, when OWNER_WHATSAPP_NUMBER was unset, this
        # branch sent an immediate rejection instead of ever creating the
        # approval - meaning every correctly-detected sensitive request
        # (e.g. "bank account details") was silently auto-refused with no
        # record anywhere for the owner to review, which defeated the
        # entire purpose of the approval workflow and the dashboard.
        request = create_approval_request(db, contact, text, sensitive_match.category)
        if settings.owner_whatsapp_number:
            notification = build_owner_notification(request, contact)
            try:
                whatsapp_client.send_text_message(settings.owner_whatsapp_number, notification)
            except Exception:  # noqa: BLE001
                log_error("Failed to send owner approval notification.", exc_info=True)
        else:
            log_warning(
                "Sensitive request routed to approval, but OWNER_WHATSAPP_NUMBER is not "
                "configured, so no WhatsApp notification was sent. Review it from the "
                "dashboard's Approvals page instead."
            )
        _log_request(db, parsed, "success", f"sensitive_pending_approval:{request.id}")
        return

    # -----------------------------------------------------------------
    # Step 12: Typing delay
    # -----------------------------------------------------------------
    typing_delay.apply_typing_delay(parsed.message_id)

    # -----------------------------------------------------------------
    # Step 13-15: Prompt building, AI generation (fallback handled inside
    # ai.py), and response handling
    # -----------------------------------------------------------------
    _generate_and_send_ai_reply(db, contact, text, parsed)


def _generate_and_send_ai_reply(
    db: Session, contact: Contact, user_message: str, parsed: Optional[ParsedMessage], *, approved: bool = False
) -> None:
    """
    Build the prompt, call the AI, scan the response for secret leakage,
    save it to memory, and send it to the contact. Used both for the
    normal pipeline and for the post-approval follow-up.

    `approved=True` (post-approval follow-up only) adds the extra system
    note that authorizes the AI to actually answer this one message - see
    prompt_builder.build_messages()'s `extra_system_note` docstring for
    why that's necessary: without it, the AI's standing instruction to
    always deflect sensitive requests would make it refuse again even
    after the owner approved.
    """
    notes = []
    if approved:
        notes.append(
            "IMPORTANT: the account owner has just explicitly reviewed and APPROVED this "
            "specific request via the admin dashboard. You may now directly answer it, "
            "including sharing any sensitive information from the owner's profile/rules "
            "below if that is what was requested. This approval applies only to this one "
            "message, not to the rest of the conversation."
        )
    search_wanted = web_search.should_search(user_message, per_contact_enabled=contact.web_search_enabled)
    if search_wanted:
        if web_search.is_configured():
            # No provider is actually implemented yet (see web_search.py)
            # even when configured - never silently proceed as if a
            # search happened.
            notes.append(
                "The person's message would normally trigger a web search, but no search "
                "provider is implemented yet. Do not claim you searched the web; answer from "
                "your own knowledge instead, and briefly mention your information may not be current."
            )
        else:
            notes.append(
                "The person's message would normally trigger a web search, but web search is "
                "currently disabled/not configured. Tell them plainly (in their own language) "
                "that web search isn't available right now, rather than pretending to search."
            )
    extra_note = "\n\n".join(notes) if notes else None
    messages = build_messages(db, contact, user_message, extra_system_note=extra_note)

    try:
        ai_response = openrouter_client.generate_reply(messages)
    except AIGenerationError as exc:
        log_ai_error("primary+fallback", str(exc))
        fallback_text = (
            "Sorry, I'm having trouble generating a response right now. Please try again shortly."
        )
        _safe_send(contact.wa_id, fallback_text, parsed.message_id if parsed else None)
        if parsed:
            _log_request(db, parsed, "error", f"ai_generation_failed:{exc}")
        return

    # Step 16: Secret-leak scan
    safe_reply_text = security.scan_and_sanitize_reply(ai_response.text)

    # Step 17: Save conversation memory (the text of the reply is always
    # recorded here regardless of which channel actually delivers it to
    # the contact below, so future prompts still have full context).
    add_message(db, contact, MessageRole.ASSISTANT, safe_reply_text)

    # Step 17b: AI Voice Reply (optional, OFF by default - see
    # voice_reply.py module docstring). Runs after the secret-leak scan
    # above, so voice can never speak anything that was just redacted.
    voice_settings = voice_reply.get_voice_settings()
    voice_sent = False
    voice_status = "skipped:disabled"
    if voice_reply.should_attempt_voice(voice_settings):
        voice_sent, voice_status = voice_reply.attempt_voice_reply(contact.wa_id, safe_reply_text)

    # Step 18: Send WhatsApp text reply (skipped only in voice_only mode
    # once voice has actually succeeded - see should_send_text()'s
    # docstring for the fallback rule when voice fails).
    if voice_reply.should_send_text(voice_settings, voice_sent):
        _safe_send(contact.wa_id, safe_reply_text, parsed.message_id if parsed else None)
    log_reply(contact.wa_id, ai_response.model_used, len(safe_reply_text))

    if parsed:
        search_status = "not_requested" if not search_wanted else ("unconfigured" if not web_search.is_configured() else "configured_but_unimplemented")
        _log_request(
            db, parsed, "success",
            f"ai_reply_sent:model={ai_response.model_used}:fallback={ai_response.used_fallback}:"
            f"voice={voice_status}:search={search_status}",
        )


def finalize_approval_by_id(db: Session, request_id: int) -> Optional[ApprovalStatus]:
    """
    Complete the approval loop for a request that has just been resolved
    (approved or rejected) by EITHER the WhatsApp `/approve <id>` /
    `/reject <id>` admin command OR the dashboard's
    `POST /admin/approvals/{id}/resolve` endpoint (admin_api.py) - both
    call this same function so there is exactly one place that performs
    the actual follow-through, instead of two parallel implementations.

    - Rejected: sends the standard REJECTION_MESSAGE to the original
      contact and records it in Conversation History.
    - Approved: generates the real AI reply for the original pending
      message and sends it to the original contact, exactly as if it had
      been answered immediately. It also now retries Smart Memory
      extraction on that original message with `force_explicit=True`,
      since the owner's approval is the explicit authorization that was
      missing the first time around (the message was withheld from Smart
      Memory by the soft-block rule while the request was pending - see
      smart_memory.extract_memories_from_message()'s docstring).

    Returns the resolved ApprovalStatus, or None if the request doesn't
    exist / isn't in a resolved state / its contact is missing (nothing to
    follow through on in any of those cases).
    """
    request = get_request_by_id(db, request_id)
    if request is None or request.status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        return None

    contact = db.get(Contact, request.contact_id)
    if contact is None:
        return None

    if request.status == ApprovalStatus.REJECTED:
        _safe_send(contact.wa_id, REJECTION_MESSAGE)
        add_message(db, contact, MessageRole.ASSISTANT, REJECTION_MESSAGE)
        return ApprovalStatus.REJECTED

    # Approved: generate the real AI reply now, using the original message.
    typing_delay.apply_typing_delay()
    _generate_and_send_ai_reply(db, contact, request.user_message, None, approved=True)

    try:
        smart_memory.extract_memories_from_message(
            db, contact, request.user_message, force_explicit=True
        )
    except Exception:  # noqa: BLE001 - Smart Memory must never break the approval flow
        log_error("Smart Memory extraction failed for an approved request.", exc_info=True)

    return ApprovalStatus.APPROVED


def _finalize_approval_followup(db: Session, admin_command_text: str) -> None:
    """
    Parse `/approve <id>` or `/reject <id>` admin command text (the
    WhatsApp path) and delegate to finalize_approval_by_id().
    """
    parts = admin_command_text.strip().split()
    if len(parts) < 2:
        return
    try:
        request_id = int(parts[1])
    except ValueError:
        return
    finalize_approval_by_id(db, request_id)


def _is_global_ai_enabled(db: Session) -> bool:
    """Read the DB-backed global AI switch (defaults to True if never set)."""
    from models import GlobalSetting

    setting = db.get(GlobalSetting, GlobalSetting.KEY_GLOBAL_AI_ENABLED)
    if setting is None:
        return True
    return setting.value == "True"


def _safe_send(wa_id: str, text: str, message_id: Optional[str] = None) -> None:
    """Send a WhatsApp text message, swallowing and logging any API error."""
    if not text:
        return
    try:
        if message_id:
            whatsapp_client.mark_as_read(message_id)
        whatsapp_client.send_text_message(wa_id, text)
    except Exception:  # noqa: BLE001 - sending failures must never crash the pipeline
        log_error(f"Failed to send WhatsApp message to {wa_id}.", exc_info=True)
