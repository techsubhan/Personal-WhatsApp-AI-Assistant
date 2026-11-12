"""
rate_limiter.py
================
Rate Limiter: prevents a single contact from flooding the assistant with
messages, using an in-memory sliding time window per contact.

Design notes
------------
State is kept entirely in process memory (a dict guarded by a lock) rather
than in the database, since rate limiting only needs to reason about a
short rolling window (seconds/minutes) and must be extremely fast on the
hot path of every inbound message. This does mean the rate limiter resets
if the process restarts, which is an acceptable trade-off for this kind of
short-window protection (see models.py module docstring for the same
reasoning applied project-wide).

This module is thread-safe, since FastAPI runs synchronous endpoint code
in a thread pool and multiple webhook requests could be processed
concurrently.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

from config import settings
from logger import get_logger

logger = get_logger("rate_limiter")


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after_seconds: float = 0.0
    reason: str = ""


class RateLimiter:
    """Sliding-window rate limiter keyed by WhatsApp contact ID."""

    def __init__(self, max_per_minute: Optional[int] = None, cooldown_seconds: Optional[int] = None) -> None:
        self._max_per_minute = max_per_minute or settings.rate_limit_messages_per_minute
        self._cooldown_seconds = cooldown_seconds or settings.rate_limit_cooldown_seconds
        self._window_seconds = 60.0
        self._lock = threading.Lock()
        self._timestamps: Dict[str, Deque[float]] = {}
        self._cooldown_until: Dict[str, float] = {}

    def check(self, wa_id: str) -> RateLimitResult:
        """
        Check whether a new message from `wa_id` is allowed right now.
        This both checks AND records the message attempt atomically.
        """
        now = time.monotonic()

        with self._lock:
            cooldown_expiry = self._cooldown_until.get(wa_id)
            if cooldown_expiry is not None:
                if now < cooldown_expiry:
                    return RateLimitResult(
                        allowed=False,
                        retry_after_seconds=round(cooldown_expiry - now, 1),
                        reason="cooldown_active",
                    )
                # Cooldown has elapsed; clear it and continue normally.
                del self._cooldown_until[wa_id]

            history = self._timestamps.setdefault(wa_id, deque())

            # Drop timestamps older than the sliding window.
            while history and now - history[0] > self._window_seconds:
                history.popleft()

            if len(history) >= self._max_per_minute:
                # Limit exceeded: start a cooldown period for this contact.
                self._cooldown_until[wa_id] = now + self._cooldown_seconds
                logger.warning(
                    "Rate limit exceeded for %s (%d msgs/min); cooldown for %ds.",
                    wa_id, self._max_per_minute, self._cooldown_seconds,
                )
                return RateLimitResult(
                    allowed=False,
                    retry_after_seconds=float(self._cooldown_seconds),
                    reason="rate_limit_exceeded",
                )

            history.append(now)
            return RateLimitResult(allowed=True)

    def reset(self, wa_id: str) -> None:
        """Clear all rate-limit state for a contact (e.g. for admin/testing use)."""
        with self._lock:
            self._timestamps.pop(wa_id, None)
            self._cooldown_until.pop(wa_id, None)


# Singleton limiter instance used throughout the application.
rate_limiter = RateLimiter()
