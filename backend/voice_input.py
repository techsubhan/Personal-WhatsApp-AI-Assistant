"""
voice_input.py
===============
Orchestrates incoming voice message handling: download the audio from
WhatsApp, transcribe it via stt.py, and hand back plain text for
routes.py to feed into the EXISTING text pipeline unchanged (same
sensitive-detection, approval, Smart Memory, and AI-reply code every
typed message already goes through - no parallel pipeline).

Entirely gated by `VOICE_INPUT_ENABLED` (config.py), OFF by default: this
project previously had no incoming-audio handling at all (confirmed by
inspection - `MessageType.AUDIO` was parsed by whatsapp.py but never
processed by routes.py). With the flag off, `maybe_transcribe()` always
returns None immediately, so behavior is byte-for-byte unchanged from
before this feature existed - voice notes fall through to the existing
"unsupported_message_type" ignore path exactly as they did previously.

Duration limiting: `VOICE_MAX_DURATION_SECONDS` cannot be enforced before
transcription without an audio-decoding library (none is available in
this project/environment), so it is enforced as a post-transcription
check using the duration OpenRouter's STT response reports
(`usage.seconds`) - a transcript that came from audio longer than the
configured limit is discarded rather than silently accepted. This is a
real limitation, not hidden: pre-transcription duration/size validation
is limited to file size (which IS available before any network call).
"""

from __future__ import annotations

from typing import Optional

from config import settings
from logger import get_logger, log_error
from stt import STTError, is_configured as stt_is_configured, transcribe
from whatsapp import MessageType, ParsedMessage, WhatsAppAPIError, whatsapp_client

logger = get_logger("voice_input")


def maybe_transcribe(parsed: ParsedMessage) -> Optional[str]:
    """
    If voice input is enabled and `parsed` is an audio message with a
    media id, download and transcribe it. Returns the transcript text on
    success, or None on any failure/misconfiguration/disabled state -
    callers must already have a sensible fallback for None (the existing
    "unsupported message type" ignore path). Never raises.
    """
    if not settings.voice_input_enabled:
        return None
    if parsed.message_type != MessageType.AUDIO or not parsed.media_id:
        return None
    if not stt_is_configured():
        logger.info("Voice message received but STT is not configured (missing OPENROUTER_API_KEY).")
        return None

    try:
        audio_bytes, mime_type = whatsapp_client.download_media(parsed.media_id)
    except WhatsAppAPIError as exc:
        log_error(f"Voice input: failed to download media: {exc}")
        return None

    try:
        result = transcribe(audio_bytes, mime_type)
    except STTError as exc:
        logger.warning("Voice input: transcription failed: %s", exc)
        return None
    finally:
        # `audio_bytes` only ever lives in memory here - transcribe() does
        # not write it to disk (OpenRouter's base64/JSON path), so there
        # is no temp file to clean up on this path. If a future provider
        # requires a temp file, it must be deleted in a `finally` block
        # exactly like voice_reply.py's outgoing-audio temp file.
        del audio_bytes

    if result.duration_seconds and result.duration_seconds > settings.voice_max_duration_seconds:
        logger.warning(
            "Voice input: discarding transcript - audio duration %.0fs exceeds VOICE_MAX_DURATION_SECONDS=%d.",
            result.duration_seconds, settings.voice_max_duration_seconds,
        )
        return None

    logger.info("Voice input transcribed successfully (%d chars).", len(result.text))
    return result.text
