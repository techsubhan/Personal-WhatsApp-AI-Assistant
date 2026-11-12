"""
utils.py
========
Small, dependency-light helper utilities shared across multiple modules.
Nothing in here should import from the higher-level business-logic modules
(contacts.py, memory.py, routes.py, etc.) to avoid circular imports - this
module sits at the bottom of the dependency graph alongside config.py and
logger.py.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator, Optional


def normalize_wa_id(wa_id: Optional[str]) -> str:
    """
    Normalize a WhatsApp phone number identifier: strip a leading '+',
    whitespace, and any non-digit characters. WhatsApp Cloud API always
    sends 'wa_id' values as digits-only already, but admin-entered numbers
    (via /block, /contact ai on, etc.) may include a '+' or spaces.
    """
    if not wa_id:
        return ""
    cleaned = wa_id.strip().lstrip("+")
    return "".join(ch for ch in cleaned if ch.isdigit())


def truncate(text: str, max_length: int = 200) -> str:
    """Truncate text to `max_length` characters, appending an ellipsis if cut."""
    if text is None:
        return ""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "\u2026"


class Timer:
    """
    Simple context manager for measuring elapsed wall-clock time in
    milliseconds, used to populate RequestLog.response_time_ms.

    Usage:
        with Timer() as t:
            do_work()
        print(t.elapsed_ms)
    """

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        self.elapsed_ms: int = 0
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)


@contextmanager
def timer() -> Generator[Timer, None, None]:
    """Function-style alternative to the Timer class for inline use."""
    t = Timer()
    t.__enter__()
    try:
        yield t
    finally:
        t.__exit__(None, None, None)


def safe_truncate_for_log(text: str, max_length: int = 300) -> str:
    """Truncate a string for safe, compact inclusion in a log line."""
    return truncate(text.replace("\n", " ").replace("\r", " "), max_length)
