"""
logger.py
=========
Centralized logging configuration for the WhatsApp AI Assistant.

Responsibilities
-----------------
- Configure a root application logger ("whatsapp_ai") with both a rotating
  file handler (data/logs/app.log) and a console handler.
- Provide `get_logger(name)` for every other module to obtain a properly
  configured child logger (e.g. `get_logger("whatsapp_ai.ai")`).
- Provide a `SecretRedactionFilter` that scrubs known secret values (API
  keys, access tokens, app secrets) from every log record before it is
  emitted, so secrets can never leak into log files even by accident.
- Provide small helper functions for the categories of events the spec
  calls out explicitly (errors, replies, requests, approvals, admin
  commands, warnings, AI errors) so call sites stay short and consistent.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path
from typing import Iterable

from config import settings

_ROOT_LOGGER_NAME = "whatsapp_ai"
_CONFIGURED = False


class SecretRedactionFilter(logging.Filter):
    """
    Logging filter that replaces any occurrence of configured secret values
    with a fixed placeholder, so that even a bug elsewhere that accidentally
    logs a secret-bearing string cannot leak it to disk or console.
    """

    PLACEHOLDER = "***REDACTED***"

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        # Only redact secrets that are non-trivial in length, to avoid
        # accidentally redacting short/common substrings.
        self._secrets = [s for s in secrets if s and len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True

        redacted = message
        for secret in self._secrets:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, self.PLACEHOLDER)

        if redacted != message:
            record.msg = redacted
            record.args = ()

        return True


def _build_secret_list() -> list[str]:
    """Collect all known secret values from configuration for redaction."""
    return [
        settings.whatsapp_access_token,
        settings.whatsapp_app_secret,
        settings.whatsapp_verify_token,
        settings.openrouter_api_key,
        settings.app_secret_key,
        settings.internal_api_key,
        settings.web_search_api_key,
    ]


def configure_logging() -> None:
    """
    Configure the root application logger exactly once. Safe to call
    multiple times (subsequent calls are no-ops).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / settings.log_file

    root_logger = logging.getLogger(_ROOT_LOGGER_NAME)
    root_logger.setLevel(settings.log_level.upper())
    root_logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_path),
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    redaction_filter = SecretRedactionFilter(_build_secret_list())
    file_handler.addFilter(redaction_filter)
    console_handler.addFilter(redaction_filter)

    # Avoid duplicate handlers if configure_logging() is somehow invoked
    # more than once within the same process.
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    _CONFIGURED = True


def get_logger(name: str = _ROOT_LOGGER_NAME) -> logging.Logger:
    """
    Return a logger under the application's root logger namespace.
    Ensures `configure_logging()` has run first.
    """
    configure_logging()
    if name == _ROOT_LOGGER_NAME or name.startswith(_ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


# ---------------------------------------------------------------------------
# Convenience helpers for the specific event categories called out in the
# specification. These keep call sites in other modules short and ensure a
# consistent log message shape across the whole project.
# ---------------------------------------------------------------------------
_app_logger = get_logger()


def log_request(wa_id: str, message_type: str, detail: str = "") -> None:
    _app_logger.info("REQUEST | contact=%s type=%s %s", wa_id, message_type, detail)


def log_reply(wa_id: str, model_used: str, char_count: int) -> None:
    _app_logger.info(
        "REPLY | contact=%s model=%s chars=%d", wa_id, model_used, char_count
    )


def log_admin_command(admin_wa_id: str, command: str, result: str = "") -> None:
    _app_logger.info(
        "ADMIN_COMMAND | admin=%s command=%s result=%s", admin_wa_id, command, result
    )


def log_approval(wa_id: str, status: str, reason: str = "") -> None:
    _app_logger.info(
        "APPROVAL | contact=%s status=%s reason=%s", wa_id, status, reason
    )


def log_warning(message: str) -> None:
    _app_logger.warning(message)


def log_error(message: str, exc_info: bool = False) -> None:
    _app_logger.error(message, exc_info=exc_info)


def log_ai_error(model: str, error: str) -> None:
    _app_logger.error("AI_ERROR | model=%s error=%s", model, error)
