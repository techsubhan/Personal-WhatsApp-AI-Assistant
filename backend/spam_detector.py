"""
spam_detector.py
=================
Spam Detection: identifies repeated or suspicious messages from a contact
within a short time window and escalates through three actions:

    ignore  -> below the repeat threshold; message is processed normally
    warn    -> repeat threshold reached; a one-time warning reply is sent
               and the message itself is still not answered by the AI
    mute    -> repeats continue past the warning; the contact is
               temporarily muted (no replies at all) for a configurable
               duration

Like rate_limiter.py, all state is kept in-memory (per-process), guarded
by a lock, since this only needs to reason about a short rolling window.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple

from config import settings
from logger import get_logger

logger = get_logger("spam_detector")

SpamAction = str  # "none" | "warn" | "mute"

WARNING_MESSAGE = (
    "It looks like you've sent the same message several times in a row. "
    "I've received it - no need to resend! I'll pause automated replies "
    "briefly if this continues."
)


@dataclass
class SpamResult:
    action: SpamAction
    reply_text: Optional[str] = None
    muted_until_seconds: Optional[float] = None


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


class SpamDetector:
    """Tracks recent message content per contact to detect repeated spam."""

    def __init__(
        self,
        repeat_threshold: Optional[int] = None,
        window_seconds: Optional[int] = None,
        mute_minutes: Optional[int] = None,
    ) -> None:
        self._repeat_threshold = repeat_threshold or settings.spam_repeat_threshold
        self._window_seconds = window_seconds or settings.spam_window_seconds
        self._mute_minutes = mute_minutes or settings.spam_mute_minutes
        self._lock = threading.Lock()

        # wa_id -> deque[(timestamp, normalized_text)]
        self._history: Dict[str, Deque[Tuple[float, str]]] = {}
        # wa_id -> monotonic timestamp until which the contact is muted
        self._muted_until: Dict[str, float] = {}
        # wa_id -> whether a warning has already been issued for the
        # current spam streak (so we only warn once before muting)
        self._warned: Dict[str, bool] = {}

    def is_muted(self, wa_id: str) -> bool:
        with self._lock:
            expiry = self._muted_until.get(wa_id)
            if expiry is None:
                return False
            if time.monotonic() >= expiry:
                del self._muted_until[wa_id]
                self._warned.pop(wa_id, None)
                return False
            return True

    def check(self, wa_id: str, message_text: str) -> SpamResult:
        """
        Record this message and return the action to take. Must be called
        for every inbound message so the rolling history stays accurate.
        """
        now = time.monotonic()
        normalized = _normalize(message_text)

        with self._lock:
            mute_expiry = self._muted_until.get(wa_id)
            if mute_expiry is not None:
                if now < mute_expiry:
                    return SpamResult(action="mute", muted_until_seconds=mute_expiry - now)
                del self._muted_until[wa_id]
                self._warned.pop(wa_id, None)

            history = self._history.setdefault(wa_id, deque())
            while history and now - history[0][0] > self._window_seconds:
                history.popleft()

            history.append((now, normalized))
            repeat_count = sum(1 for _, text in history if text == normalized)

            if repeat_count < self._repeat_threshold:
                return SpamResult(action="none")

            already_warned = self._warned.get(wa_id, False)

            if not already_warned:
                self._warned[wa_id] = True
                logger.info(
                    "Spam threshold reached for %s (%d repeats); issuing warning.",
                    wa_id, repeat_count,
                )
                return SpamResult(action="warn", reply_text=WARNING_MESSAGE)

            # Already warned once and still spamming: mute.
            mute_seconds = self._mute_minutes * 60
            self._muted_until[wa_id] = now + mute_seconds
            logger.warning(
                "Muting %s for %d minutes after continued spam (%d repeats).",
                wa_id, self._mute_minutes, repeat_count,
            )
            return SpamResult(action="mute", muted_until_seconds=float(mute_seconds))

    def reset(self, wa_id: str) -> None:
        """Clear all spam-detection state for a contact."""
        with self._lock:
            self._history.pop(wa_id, None)
            self._muted_until.pop(wa_id, None)
            self._warned.pop(wa_id, None)


# Singleton detector instance used throughout the application.
spam_detector = SpamDetector()
