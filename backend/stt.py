"""
stt.py
======
Speech-to-Text client for incoming WhatsApp voice messages, via
OpenRouter's dedicated `/audio/transcriptions` endpoint
(https://openrouter.ai/docs/guides/overview/multimodal/stt), using the
SAME OPENROUTER_API_KEY / OPENROUTER_BASE_URL already configured for chat
in ai.py - no separate STT provider or API key.

Confirmed via OpenRouter's own docs before writing this: the JSON
request path takes `{model, input_audio: {data: <base64>, format},
language?}` and returns `{text, usage: {...}}`. The base64/JSON path is
used here (rather than the multipart path) because the audio is already
in memory as bytes from whatsapp.py's `download_media()` - no benefit to
re-encoding as a multipart form.

This module is new: nothing in the project previously processed incoming
audio messages at all (confirmed by inspection - routes.py only handled
MessageType.TEXT and MessageType.INTERACTIVE before this change).

Feature flag: incoming voice processing is entirely gated by
`VOICE_INPUT_ENABLED` (see config.py / voice_input.py), OFF by default -
this module itself has no gating logic, it is only ever called when that
flag is already on.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import requests

from config import settings
from logger import get_logger

logger = get_logger("stt")

# Maps a WhatsApp/Meta audio mime type to the `format` value OpenRouter's
# transcription endpoint expects. WhatsApp voice notes are almost always
# audio/ogg (Opus codec); other formats are included for forwarded audio
# files.
_MIME_TO_FORMAT = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/webm": "webm",
}


class STTError(Exception):
    """Raised when transcription fails for any reason (config, network, provider, unsupported format)."""


@dataclass
class STTResult:
    text: str
    duration_seconds: float | None


def is_configured() -> bool:
    return bool(settings.openrouter_api_key)


def transcribe(audio_bytes: bytes, mime_type: str) -> STTResult:
    """
    Transcribe `audio_bytes` (already downloaded from WhatsApp) via
    OpenRouter. Raises STTError on any failure - callers (voice_input.py)
    always catch this and fall back to the existing "unsupported media"
    behavior; a transcription failure must never break the rest of the
    pipeline or cause a crash.
    """
    if not settings.openrouter_api_key:
        raise STTError("OPENROUTER_API_KEY is not configured.")

    audio_format = _MIME_TO_FORMAT.get(mime_type)
    if audio_format is None:
        raise STTError(f"Unsupported audio mime type for transcription: '{mime_type}'.")

    size_mb = len(audio_bytes) / (1024 * 1024)
    if size_mb > settings.voice_max_file_size_mb:
        raise STTError(f"Audio file too large ({size_mb:.1f} MB > {settings.voice_max_file_size_mb} MB limit).")

    url = f"{settings.openrouter_base_url.rstrip('/')}/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.openrouter_stt_model,
        "input_audio": {
            "data": base64.b64encode(audio_bytes).decode("ascii"),
            "format": audio_format,
        },
    }

    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=settings.voice_transcription_timeout
        )
    except requests.Timeout as exc:
        raise STTError("Transcription request timed out.") from exc
    except requests.RequestException as exc:
        raise STTError(f"Network error calling OpenRouter STT: {exc}") from exc

    if response.status_code >= 400:
        logger.error("OpenRouter STT returned HTTP %d.", response.status_code)
        raise STTError(f"OpenRouter STT returned HTTP {response.status_code}.")

    try:
        data = response.json()
    except ValueError as exc:
        raise STTError("OpenRouter STT returned a non-JSON response.") from exc

    text = data.get("text")
    if not text:
        raise STTError("OpenRouter STT returned no transcribed text.")

    usage = data.get("usage") or {}
    logger.info("Transcribed voice message: %d chars, duration=%ss", len(text), usage.get("seconds"))
    return STTResult(text=text, duration_seconds=usage.get("seconds"))
