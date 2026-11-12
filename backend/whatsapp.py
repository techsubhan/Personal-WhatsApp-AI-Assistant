"""
whatsapp.py
===========
WhatsApp Business Cloud API client and inbound-message parsing.

This module is the single point of contact with Meta's WhatsApp Cloud API.
Nothing else in the project should call `requests` against `graph.facebook.com`
directly - everything goes through `WhatsAppClient` below.

Responsibilities
-----------------
1. Webhook verification (the GET handshake Meta performs when you configure
   a webhook URL).
2. Webhook payload signature verification (HMAC SHA-256 over the raw request
   body, compared against the `X-Hub-Signature-256` header).
3. Parsing every supported inbound message type into a clean, typed
   `ParsedMessage` object that the rest of the application can work with
   without ever touching Meta's raw JSON schema again.
4. Sending outbound messages (plain text replies).
5. Marking messages as read and (optionally) showing the native typing
   indicator that the Cloud API exposes via the `typing_indicator` field
   on a "mark as read" request.
6. Best-effort group vs. private chat detection.

Notes on group detection
-------------------------
The officially documented WhatsApp Business Cloud API webhook payload does
not include a first-class "this message came from a group" flag, because
Meta's Cloud API business accounts are not fully first-class members of
WhatsApp groups the way personal accounts are. To stay robust and safe by
default, this module treats a message as a "group message" if either:
  (a) the sender id ("wa_id"/"from") matches the WhatsApp group JID pattern
      (contains "-" together with "@g.us"-style suffixes seen in the
      underlying protocol), or
  (b) the payload explicitly carries a `context.group_id` / `group_id` key
      (present in some regional/BSP variations of the API).
By default (IGNORE_GROUP_MESSAGES=true), anything flagged as a group
message is ignored, exactly as required by the specification.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import requests

from config import settings
from logger import get_logger

logger = get_logger("whatsapp")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class WhatsAppAPIError(Exception):
    """Raised when a call to the WhatsApp Cloud API fails."""


class WebhookSignatureError(Exception):
    """Raised when an inbound webhook's HMAC signature does not match."""


# ---------------------------------------------------------------------------
# Parsed message types
# ---------------------------------------------------------------------------
class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACTS = "contacts"
    INTERACTIVE = "interactive"
    UNSUPPORTED = "unsupported"


@dataclass
class ParsedMessage:
    """A single normalized inbound WhatsApp message."""

    message_id: str
    wa_id: str  # Sender's WhatsApp ID (phone number, no "+")
    sender_name: Optional[str]
    message_type: MessageType
    timestamp: int
    is_group: bool = False

    # Populated depending on message_type
    text_body: Optional[str] = None
    media_id: Optional[str] = None
    mime_type: Optional[str] = None
    caption: Optional[str] = None
    filename: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_name: Optional[str] = None
    contacts: List[Dict[str, Any]] = field(default_factory=list)
    interactive_type: Optional[str] = None  # button_reply | list_reply
    interactive_id: Optional[str] = None
    interactive_title: Optional[str] = None

    raw: Dict[str, Any] = field(default_factory=dict)


def _looks_like_group_jid(wa_id: str, message_obj: Dict[str, Any]) -> bool:
    """Best-effort heuristic to detect a group-originated message."""
    if not wa_id:
        return False
    if "g.us" in wa_id or ("-" in wa_id and len(wa_id) > 20):
        return True
    context = message_obj.get("context", {}) if isinstance(message_obj, dict) else {}
    if isinstance(context, dict) and context.get("group_id"):
        return True
    if message_obj.get("group_id"):
        return True
    return False


def parse_webhook_payload(payload: Dict[str, Any]) -> List[ParsedMessage]:
    """
    Parse a full WhatsApp Cloud API webhook POST body into a list of
    ParsedMessage objects. Status-only callbacks (sent/delivered/read
    receipts) and payloads without a `messages` array yield an empty list.
    """
    parsed: List[ParsedMessage] = []

    try:
        entries = payload.get("entry", [])
    except AttributeError:
        logger.warning("Received non-dict webhook payload; ignoring.")
        return parsed

    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages")
            if not messages:
                continue  # e.g. this "change" was a status update, not a message

            contacts_index = {
                c.get("wa_id"): c.get("profile", {}).get("name")
                for c in value.get("contacts", [])
                if isinstance(c, dict)
            }

            for msg in messages:
                try:
                    parsed_msg = _parse_single_message(msg, contacts_index)
                    if parsed_msg is not None:
                        parsed.append(parsed_msg)
                except Exception:  # noqa: BLE001 - never let one bad message crash the batch
                    logger.error(
                        "Failed to parse an inbound message; skipping it.",
                        exc_info=True,
                    )

    return parsed


def _parse_single_message(
    msg: Dict[str, Any], contacts_index: Dict[str, Optional[str]]
) -> Optional[ParsedMessage]:
    wa_id = msg.get("from", "")
    message_id = msg.get("id", "")
    timestamp = int(msg.get("timestamp", time.time()))
    sender_name = contacts_index.get(wa_id)
    is_group = _looks_like_group_jid(wa_id, msg)

    raw_type = msg.get("type", "unsupported")

    base_kwargs = dict(
        message_id=message_id,
        wa_id=wa_id,
        sender_name=sender_name,
        timestamp=timestamp,
        is_group=is_group,
        raw=msg,
    )

    if raw_type == "text":
        return ParsedMessage(
            message_type=MessageType.TEXT,
            text_body=msg.get("text", {}).get("body", ""),
            **base_kwargs,
        )

    if raw_type == "image":
        image = msg.get("image", {})
        return ParsedMessage(
            message_type=MessageType.IMAGE,
            media_id=image.get("id"),
            mime_type=image.get("mime_type"),
            caption=image.get("caption"),
            **base_kwargs,
        )

    if raw_type == "audio":
        audio = msg.get("audio", {})
        return ParsedMessage(
            message_type=MessageType.AUDIO,
            media_id=audio.get("id"),
            mime_type=audio.get("mime_type"),
            **base_kwargs,
        )

    if raw_type == "video":
        video = msg.get("video", {})
        return ParsedMessage(
            message_type=MessageType.VIDEO,
            media_id=video.get("id"),
            mime_type=video.get("mime_type"),
            caption=video.get("caption"),
            **base_kwargs,
        )

    if raw_type == "document":
        document = msg.get("document", {})
        return ParsedMessage(
            message_type=MessageType.DOCUMENT,
            media_id=document.get("id"),
            mime_type=document.get("mime_type"),
            caption=document.get("caption"),
            filename=document.get("filename"),
            **base_kwargs,
        )

    if raw_type == "sticker":
        sticker = msg.get("sticker", {})
        return ParsedMessage(
            message_type=MessageType.STICKER,
            media_id=sticker.get("id"),
            mime_type=sticker.get("mime_type"),
            **base_kwargs,
        )

    if raw_type == "location":
        location = msg.get("location", {})
        return ParsedMessage(
            message_type=MessageType.LOCATION,
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            location_name=location.get("name"),
            **base_kwargs,
        )

    if raw_type == "contacts":
        return ParsedMessage(
            message_type=MessageType.CONTACTS,
            contacts=msg.get("contacts", []),
            **base_kwargs,
        )

    if raw_type == "interactive":
        interactive = msg.get("interactive", {})
        interactive_type = interactive.get("type")  # button_reply | list_reply
        selection = interactive.get(interactive_type, {}) if interactive_type else {}
        return ParsedMessage(
            message_type=MessageType.INTERACTIVE,
            interactive_type=interactive_type,
            interactive_id=selection.get("id"),
            interactive_title=selection.get("title"),
            **base_kwargs,
        )

    logger.info("Ignoring unsupported inbound message type: %s", raw_type)
    return ParsedMessage(message_type=MessageType.UNSUPPORTED, **base_kwargs)


# ---------------------------------------------------------------------------
# Webhook verification helpers
# ---------------------------------------------------------------------------
def verify_webhook_subscription(
    mode: Optional[str], token: Optional[str], challenge: Optional[str]
) -> Optional[str]:
    """
    Handle Meta's webhook verification GET request.
    Returns the challenge string to echo back if verification succeeds,
    otherwise returns None (caller should respond with HTTP 403).
    """
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return challenge
    logger.warning(
        "Webhook verification failed: mode=%s token_match=%s",
        mode,
        token == settings.whatsapp_verify_token,
    )
    return None


def verify_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """
    Verify the `X-Hub-Signature-256` header Meta attaches to every webhook
    POST request, using HMAC-SHA256 with the app secret.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    if not settings.whatsapp_app_secret:
        # Misconfiguration: without an app secret we cannot verify anything.
        logger.error("WHATSAPP_APP_SECRET is not configured; cannot verify webhook signatures.")
        return False

    expected_signature = hmac.new(
        key=settings.whatsapp_app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    provided_signature = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected_signature, provided_signature)


# ---------------------------------------------------------------------------
# WhatsApp Cloud API client
# ---------------------------------------------------------------------------
class WhatsAppClient:
    """Thin, typed wrapper around the WhatsApp Business Cloud API."""

    def __init__(self) -> None:
        self._graph_root = f"https://graph.facebook.com/{settings.whatsapp_api_version}"
        self._base_url = f"{self._graph_root}/{settings.whatsapp_phone_number_id}"
        self._headers = {
            "Authorization": f"Bearer {settings.whatsapp_access_token}",
            "Content-Type": "application/json",
        }
        self._timeout = 20

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}/messages"
        try:
            response = requests.post(
                url, headers=self._headers, json=payload, timeout=self._timeout
            )
        except requests.RequestException as exc:
            logger.error("Network error calling WhatsApp API: %s", exc)
            raise WhatsAppAPIError(f"Network error calling WhatsApp API: {exc}") from exc

        if response.status_code >= 400:
            logger.error(
                "WhatsApp API error %s: %s", response.status_code, response.text
            )
            raise WhatsAppAPIError(
                f"WhatsApp API returned {response.status_code}: {response.text}"
            )

        return response.json()

    def send_text_message(self, to_wa_id: str, body: str, preview_url: bool = False) -> Dict[str, Any]:
        """Send a plain text reply to a contact."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "text",
            "text": {"body": body, "preview_url": preview_url},
        }
        result = self._post(payload)
        logger.info("Sent text message to %s (%d chars)", to_wa_id, len(body))
        return result

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        """
        Download inbound media (e.g. a voice note) from the WhatsApp
        Cloud API. This is a two-step Graph API flow: first resolve the
        media id to a short-lived URL via `GET /{media_id}`, then fetch
        the bytes from that URL with the same auth header. Returns
        (raw_bytes, mime_type). Used by stt.py for incoming voice
        messages - added alongside upload_media()/send_audio_message()
        on this same client, no separate/duplicate client introduced.
        """
        lookup_url = f"{self._graph_root}/{media_id}"
        try:
            lookup = requests.get(lookup_url, headers=self._headers, timeout=self._timeout)
        except requests.RequestException as exc:
            raise WhatsAppAPIError(f"Network error resolving media URL: {exc}") from exc
        if lookup.status_code >= 400:
            logger.error("WhatsApp media lookup error %s: %s", lookup.status_code, lookup.text)
            raise WhatsAppAPIError(f"WhatsApp media lookup returned {lookup.status_code}")

        info = lookup.json()
        media_url = info.get("url")
        mime_type = info.get("mime_type", "application/octet-stream")
        if not media_url:
            raise WhatsAppAPIError("WhatsApp media lookup response did not contain a url.")

        try:
            media_response = requests.get(
                media_url, headers={"Authorization": self._headers["Authorization"]}, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(f"Network error downloading media: {exc}") from exc
        if media_response.status_code >= 400:
            logger.error("WhatsApp media download error %s", media_response.status_code)
            raise WhatsAppAPIError(f"WhatsApp media download returned {media_response.status_code}")

        logger.info("Downloaded inbound media (%d bytes, mime_type=%s)", len(media_response.content), mime_type)
        return media_response.content, mime_type

    def upload_media(self, file_path: str, mime_type: str) -> str:
        """
        Upload a local media file to the WhatsApp Cloud API's `/media`
        endpoint and return the resulting media id, which can then be
        passed to `send_audio_message()` (or any other `send_*_message`
        that takes a media id). Added for AI Voice Reply - the WhatsApp
        Cloud API has always supported media upload/send; this project
        simply never implemented it since only text replies existed
        before. Uses the same base URL/auth as every other call in this
        client; no separate/duplicate client was introduced.
        """
        url = f"{self._base_url}/media"
        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_path, f, mime_type)}
                data = {"messaging_product": "whatsapp", "type": mime_type}
                response = requests.post(
                    url,
                    headers={"Authorization": self._headers["Authorization"]},  # no Content-Type: multipart sets its own boundary
                    files=files,
                    data=data,
                    timeout=self._timeout,
                )
        except OSError as exc:
            logger.error("Failed to read media file for upload: %s", exc)
            raise WhatsAppAPIError(f"Failed to read media file for upload: {exc}") from exc
        except requests.RequestException as exc:
            logger.error("Network error uploading media to WhatsApp API: %s", exc)
            raise WhatsAppAPIError(f"Network error uploading media: {exc}") from exc

        if response.status_code >= 400:
            logger.error("WhatsApp media upload error %s: %s", response.status_code, response.text)
            raise WhatsAppAPIError(f"WhatsApp media upload returned {response.status_code}")

        media_id = response.json().get("id")
        if not media_id:
            raise WhatsAppAPIError("WhatsApp media upload response did not contain an id.")
        logger.info("Uploaded media to WhatsApp (mime_type=%s)", mime_type)
        return media_id

    def send_audio_message(self, to_wa_id: str, media_id: str) -> Dict[str, Any]:
        """
        Send a previously-uploaded audio media id as a voice message. Used
        by voice_reply.py for AI Voice Reply. Only ever called with a
        media id from `upload_media()` in this same request - audio bytes
        are never referenced by a persistent URL.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "audio",
            "audio": {"id": media_id},
        }
        result = self._post(payload)
        logger.info("Sent audio message to %s", to_wa_id)
        return result

    def mark_as_read(self, message_id: str, show_typing_indicator: bool = False) -> None:
        """
        Mark an inbound message as read. Optionally requests the native
        WhatsApp typing indicator (shown for a short window on the Cloud
        API), used to make the assistant feel more natural while the AI
        response is being generated.
        """
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        if show_typing_indicator:
            payload["typing_indicator"] = {"type": "text"}
        try:
            self._post(payload)
        except WhatsAppAPIError:
            # Non-fatal: failing to mark-as-read should never block a reply.
            logger.warning("Failed to mark message %s as read.", message_id)

    def check_status(self) -> bool:
        """
        Lightweight connectivity/auth check against the Graph API, used by
        health.py. Returns True if the WhatsApp API is reachable and the
        access token is valid for this phone number.
        """
        url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}"
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
                params={"fields": "id"},
                timeout=10,
            )
            return response.status_code == 200
        except requests.RequestException as exc:
            logger.error("WhatsApp health check failed: %s", exc)
            return False


# Singleton client instance used throughout the application.
whatsapp_client = WhatsAppClient()
