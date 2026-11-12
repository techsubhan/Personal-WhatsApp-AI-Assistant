"""
tts.py
======
Text-to-Speech client for the optional "AI Voice Reply" feature.

Uses OpenRouter's dedicated `/audio/speech` endpoint
(https://openrouter.ai/docs/features/multimodal/tts), the SAME
OPENROUTER_API_KEY / OPENROUTER_BASE_URL already configured for chat in
ai.py. No separate TTS provider or API key - OpenRouter is the single
unified AI provider for chat, TTS, and STT (see stt.py), exactly
mirroring how OpenRouterClient in ai.py is already structured (one typed
exception, no raw requests/HTTP errors ever escape to callers).

Confirmed via OpenRouter's own docs (openrouter.ai/blog/announcements/
announcing-audio-apis) before writing this: `POST {base_url}/audio/speech`
takes `{model, input, voice, response_format}` and returns a raw audio
byte stream, using the same Bearer-token auth as chat completions.

Language handling
------------------
This project has no dedicated language-detection module for controlling
AI behavior - the existing text-reply pipeline already mirrors the
contact's language purely through the system prompt instruction ("mirror
their language", see prompt_builder.py / data/prompts/system_prompt.txt).
A separate `language.py` module now exists (see that file) purely for
*labeling* detected language/script for logging and the dashboard - it is
NOT used to pick a TTS voice or change AI behavior, since OpenRouter's
speech models auto-detect the input language from the text itself. The
exact AI-generated reply text (already in the mirrored language) is
passed straight to the TTS endpoint.

Security
--------
- OPENROUTER_API_KEY is read from Settings (.env) and never logged,
  returned, or exposed to the frontend.
- Callers must never log the input text or the resulting audio bytes;
  this module logs only lengths/status, matching the project's existing
  logging-security posture (see security.py's module docstring).
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from config import settings
from logger import get_logger

logger = get_logger("tts")


class TTSError(Exception):
    """Raised when speech synthesis fails for any reason (config, network, provider)."""


@dataclass
class TTSResult:
    """A successfully synthesized voice reply, ready to upload to WhatsApp."""

    audio_bytes: bytes
    audio_format: str  # e.g. "mp3" - matches WhatsApp-supported audio types
    mime_type: str


# WhatsApp-deliverable formats only (see whatsapp.py's upload_media() /
# Meta's supported outbound audio MIME types). OpenRouter's speech
# endpoint can also return "pcm", which is deliberately not offered here
# since WhatsApp cannot play raw PCM.
_FORMAT_MIME_TYPES = {
    "mp3": "audio/mpeg",
    "aac": "audio/aac",
    "opus": "audio/ogg",
}


def is_configured() -> bool:
    """True if enough configuration exists to attempt TTS at all."""
    return bool(settings.openrouter_api_key)


def synthesize(text: str, *, voice: str, speed: float, audio_format: str) -> TTSResult:
    """
    Synthesize `text` into speech via OpenRouter. Raises TTSError on any
    failure (missing config, unsupported format, timeout, non-2xx
    response, network error) - callers (voice_reply.py) always catch this
    and fall back gracefully; a voice failure must never break the
    existing text-reply pipeline.
    """
    if not settings.openrouter_api_key:
        raise TTSError("OPENROUTER_API_KEY is not configured.")
    if audio_format not in _FORMAT_MIME_TYPES:
        raise TTSError(f"Unsupported audio format '{audio_format}'.")
    if not text or not text.strip():
        raise TTSError("Nothing to synthesize (empty text).")

    # Hard safety ceiling - see Settings.tts_max_characters docstring.
    if len(text) > settings.tts_max_characters:
        text = text[: settings.tts_max_characters]

    url = f"{settings.openrouter_base_url.rstrip('/')}/audio/speech"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.openrouter_tts_model,
        "input": text,
        "voice": voice,
        "response_format": audio_format,
        "speed": speed,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=settings.tts_timeout_seconds)
    except requests.Timeout as exc:
        raise TTSError("TTS request timed out.") from exc
    except requests.RequestException as exc:
        raise TTSError(f"Network error calling OpenRouter TTS: {exc}") from exc

    if response.status_code >= 400:
        # Never log response body verbatim - it can echo back the request
        # (including the synthesized text) or provider-side error detail.
        logger.error("OpenRouter TTS returned HTTP %d.", response.status_code)
        raise TTSError(f"OpenRouter TTS returned HTTP {response.status_code}.")

    audio_bytes = response.content
    if not audio_bytes:
        raise TTSError("OpenRouter TTS returned an empty audio response.")

    logger.info("Synthesized voice reply: %d bytes, format=%s, model=%s", len(audio_bytes), audio_format, settings.openrouter_tts_model)
    return TTSResult(audio_bytes=audio_bytes, audio_format=audio_format, mime_type=_FORMAT_MIME_TYPES[audio_format])
