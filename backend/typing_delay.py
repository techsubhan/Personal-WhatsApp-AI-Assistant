"""
typing_delay.py
================
Typing Delay: introduces a randomized, configurable delay before the
assistant's reply is sent, and (where possible) shows WhatsApp's native
typing indicator during that window, so the assistant feels less like an
instantaneous bot and more like a natural conversation partner.

Responsibilities
-----------------
- Compute a random delay within the configured [min_seconds, max_seconds]
  range (data/settings.json -> typing_delay), falling back to the static
  .env defaults if runtime settings are unavailable.
- Optionally trigger the WhatsApp Cloud API's typing indicator (via
  `WhatsAppClient.mark_as_read(show_typing_indicator=True)`) for the
  duration of the delay.
- Actually sleep for the computed delay (blocking call - this project's
  webhook handler processes messages via FastAPI's threadpool for
  synchronous endpoints, so a blocking sleep here does not stall the
  entire application).
"""

from __future__ import annotations

import random
import time
from typing import Optional

from config import load_runtime_settings, settings
from logger import get_logger

logger = get_logger("typing_delay")


def compute_delay_seconds() -> float:
    """
    Compute a random typing delay in seconds, honoring the runtime
    `typing_delay` setting (enabled/min/max), falling back to the static
    .env defaults (TYPING_DELAY_MIN_SECONDS / TYPING_DELAY_MAX_SECONDS)
    when the runtime setting is missing.
    """
    runtime_settings = load_runtime_settings()
    typing_cfg = runtime_settings.get("typing_delay", {})

    if not typing_cfg.get("enabled", True):
        return 0.0

    min_seconds = float(typing_cfg.get("min_seconds", settings.typing_delay_min_seconds))
    max_seconds = float(typing_cfg.get("max_seconds", settings.typing_delay_max_seconds))

    if max_seconds < min_seconds:
        min_seconds, max_seconds = max_seconds, min_seconds

    return round(random.uniform(min_seconds, max_seconds), 2)


def apply_typing_delay(message_id: Optional[str] = None) -> float:
    """
    Show the WhatsApp typing indicator (if a message_id is provided) and
    block for a randomized, natural-feeling delay before the caller sends
    the actual reply. Returns the number of seconds actually waited.
    """
    delay_seconds = compute_delay_seconds()

    if delay_seconds <= 0:
        return 0.0

    if message_id:
        # Import locally to avoid a hard circular dependency at module
        # load time between whatsapp.py and typing_delay.py.
        from whatsapp import whatsapp_client

        whatsapp_client.mark_as_read(message_id, show_typing_indicator=True)

    logger.debug("Applying typing delay of %.2fs", delay_seconds)
    time.sleep(delay_seconds)
    return delay_seconds
