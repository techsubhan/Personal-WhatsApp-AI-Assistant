"""
voice_reply.py
===============
Orchestrates the optional "AI Voice Reply" feature: turns an AI-generated
text reply into a WhatsApp voice message, on top of the existing text-reply
pipeline, without replacing it.

This module is called from exactly one place - the tail of
routes.py's `_generate_and_send_ai_reply()` - which is itself the single
function used by both the normal message pipeline and the post-approval
follow-up (`finalize_approval_by_id()`). Every existing gate (global AI
switch, per-contact AI, human override, working hours, blocked contacts,
spam/mute, sensitive-info approval) already runs *before* that function is
ever reached, so voice reply automatically inherits all of them for free -
no separate gating logic was duplicated here.

Flow (mirrors the spec exactly):
    AI text reply already generated + secret-scanned
        -> voice_reply.enabled? no -> caller sends text only (unchanged)
        -> yes -> check reply_mode
        -> synthesize via tts.py
        -> upload + send via whatsapp_client (whatsapp.py)
        -> confirm actual API success
        -> log a safe (no audio/content) result string
        -> delete the temporary audio file (always, even on failure)

Never fakes success: `attempt_voice_reply()` only returns sent=True after
a real WhatsApp API call succeeds.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, Tuple

from config import load_runtime_settings
from logger import get_logger, log_error
from tts import TTSError, is_configured as tts_is_configured, synthesize
from whatsapp import WhatsAppAPIError, whatsapp_client

logger = get_logger("voice_reply")

REPLY_MODE_TEXT_ONLY = "text_only"
REPLY_MODE_VOICE_ONLY = "voice_only"
REPLY_MODE_TEXT_AND_VOICE = "text_and_voice"


def get_voice_settings() -> Dict[str, Any]:
    """Read the current `voice_reply` block from runtime settings (settings.json)."""
    return load_runtime_settings().get("voice_reply", {})


def should_attempt_voice(voice_settings: Dict[str, Any]) -> bool:
    return bool(voice_settings.get("enabled")) and voice_settings.get("reply_mode") in (
        REPLY_MODE_VOICE_ONLY,
        REPLY_MODE_TEXT_AND_VOICE,
    )


def should_send_text(voice_settings: Dict[str, Any], voice_sent: bool) -> bool:
    """
    Decide whether the text reply should still be sent, given the
    configured mode and whether voice actually succeeded.
    - text_only / text_and_voice: always send text (voice is additive).
    - voice_only: skip text only if voice succeeded; otherwise fall back
      to text if `text_fallback_enabled` allows it (never silently send
      nothing unless the owner explicitly disabled fallback).
    """
    mode = voice_settings.get("reply_mode", REPLY_MODE_TEXT_ONLY)
    if not voice_settings.get("enabled") or mode != REPLY_MODE_VOICE_ONLY:
        return True
    if voice_sent:
        return False
    return bool(voice_settings.get("text_fallback_enabled", True))


def attempt_voice_reply(to_wa_id: str, text: str) -> Tuple[bool, str]:
    """
    Try to synthesize and send a voice reply. Returns (sent, status) where
    status is a short, safe-to-log detail string (never contains the
    reply text or any secret) - e.g. "sent", "failed:tts_not_configured",
    "failed:tts_error", "failed:whatsapp_upload_error",
    "failed:whatsapp_send_error".

    Never raises - a voice failure must never break the surrounding text
    reply pipeline.
    """
    if not tts_is_configured():
        logger.info("Voice reply requested but TTS is not configured (missing TTS_API_KEY).")
        return False, "failed:tts_not_configured"

    voice_settings = get_voice_settings()
    tmp_path = None
    try:
        try:
            result = synthesize(
                text,
                voice=voice_settings.get("voice", "alloy"),
                speed=float(voice_settings.get("speed", 1.0)),
                audio_format=voice_settings.get("format", "mp3"),
            )
        except TTSError as exc:
            logger.warning("Voice synthesis failed: %s", exc)
            return False, "failed:tts_error"

        # Temporary file only for the duration of the upload call below -
        # deleted in the `finally` block regardless of outcome. Never
        # persisted, never logged by path or content.
        fd, tmp_path = tempfile.mkstemp(suffix=f".{result.audio_format}", prefix="voice_reply_")
        with os.fdopen(fd, "wb") as f:
            f.write(result.audio_bytes)

        try:
            media_id = whatsapp_client.upload_media(tmp_path, result.mime_type)
        except WhatsAppAPIError as exc:
            log_error(f"Voice reply: WhatsApp media upload failed: {exc}")
            return False, "failed:whatsapp_upload_error"

        try:
            whatsapp_client.send_audio_message(to_wa_id, media_id)
        except WhatsAppAPIError as exc:
            log_error(f"Voice reply: WhatsApp audio send failed: {exc}")
            return False, "failed:whatsapp_send_error"

        logger.info("Voice reply sent to %s (%d bytes, format=%s)", to_wa_id, len(result.audio_bytes), result.audio_format)
        return True, "sent"
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                logger.warning("Failed to delete temporary voice reply file (non-fatal).")
