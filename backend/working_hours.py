"""
working_hours.py
=================
Working Hours enforcement.

Responsibilities
-----------------
- Determine whether "now" (in the configured timezone) falls inside the
  owner's configured working hours window.
- When outside working hours, apply the configured `outside_hours_action`:
    - "no_reply"       -> stay completely silent
    - "away_message"   -> send the configured away message
    - "emergency_only" -> stay silent unless the message matches one of the
                          configured emergency keywords, in which case
                          processing continues normally
- Emergency keyword messages ALWAYS continue processing normally,
  regardless of the configured outside_hours_action, since an emergency
  should never be silently dropped.

The working hours window supports overnight ranges (e.g. start_time
"22:00", end_time "06:00"), where "within hours" wraps past midnight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Optional

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - zoneinfo is stdlib on Python 3.9+
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

from config import load_runtime_settings
from logger import get_logger

logger = get_logger("working_hours")


@dataclass
class WorkingHoursDecision:
    """Result of evaluating a message against the working-hours policy."""

    allowed: bool
    reply_text: Optional[str] = None
    reason: str = ""
    is_emergency: bool = False


def _parse_hhmm(value: str) -> dt_time:
    hour_str, minute_str = value.split(":")
    return dt_time(hour=int(hour_str), minute=int(minute_str))


def _now_in_timezone(tz_name: str) -> datetime:
    if ZoneInfo is None:
        logger.warning("zoneinfo unavailable; falling back to naive local time.")
        return datetime.now()
    try:
        return datetime.now(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone '%s' in settings; defaulting to UTC.", tz_name)
        return datetime.now(ZoneInfo("UTC"))


def _is_within_window(current: dt_time, start: dt_time, end: dt_time) -> bool:
    """Handle both same-day windows (09:00-18:00) and overnight windows (22:00-06:00)."""
    if start <= end:
        return start <= current <= end
    # Overnight window wraps past midnight.
    return current >= start or current <= end


def _matches_emergency_keyword(message_text: str, keywords: list[str]) -> bool:
    if not message_text:
        return False
    lowered = message_text.lower()
    return any(keyword.lower() in lowered for keyword in keywords if keyword)


def evaluate(message_text: str) -> WorkingHoursDecision:
    """
    Evaluate the working-hours policy for an inbound message and return a
    decision the caller (routes.py) should act on.
    """
    runtime_settings = load_runtime_settings()
    wh = runtime_settings.get("working_hours", {})

    if not wh.get("enabled", False):
        return WorkingHoursDecision(allowed=True, reason="working_hours_disabled")

    try:
        start = _parse_hhmm(wh.get("start_time", "09:00"))
        end = _parse_hhmm(wh.get("end_time", "18:00"))
    except (ValueError, AttributeError):
        logger.error("Invalid working_hours start/end time in settings; allowing message through.")
        return WorkingHoursDecision(allowed=True, reason="invalid_config")

    tz_name = wh.get("timezone", "UTC")
    now = _now_in_timezone(tz_name)
    current_time = now.time()

    within_hours = _is_within_window(current_time, start, end)
    emergency_keywords = wh.get("emergency_keywords", [])
    is_emergency = _matches_emergency_keyword(message_text, emergency_keywords)

    if within_hours:
        return WorkingHoursDecision(allowed=True, reason="within_working_hours", is_emergency=is_emergency)

    if is_emergency:
        logger.info("Emergency keyword matched outside working hours; continuing processing.")
        return WorkingHoursDecision(allowed=True, reason="emergency_override", is_emergency=True)

    action = wh.get("outside_hours_action", "away_message")
    if action == "away_message":
        return WorkingHoursDecision(
            allowed=False,
            reply_text=wh.get("away_message", ""),
            reason="outside_hours_away_message",
        )
    if action == "emergency_only":
        return WorkingHoursDecision(allowed=False, reason="outside_hours_emergency_only_no_match")

    # Default / "no_reply"
    return WorkingHoursDecision(allowed=False, reason="outside_hours_no_reply")
