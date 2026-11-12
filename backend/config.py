"""
config.py
=========
Central configuration module for the WhatsApp AI Assistant.

Responsibilities
-----------------
1. Load static/secret configuration from the ".env" file (via python-dotenv)
   into a strongly-typed ``Settings`` object. This covers values that should
   never change at runtime without a restart (API keys, tokens, DB URL, etc.).
2. Load/save *dynamic* operational configuration (things an admin can change
   at runtime through admin commands, e.g. working hours, typing delay,
   away message) from/to ``data/settings.json``.
3. Load/save the owner's personal profile from ``data/profile.json``.

Every other module in the project imports configuration from here instead
of reading environment variables or JSON files directly. This keeps
configuration access centralized, consistent, and easy to reload.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
LOGS_DIR = DATA_DIR / "logs"

SETTINGS_JSON_PATH = DATA_DIR / "settings.json"
PROFILE_JSON_PATH = DATA_DIR / "profile.json"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "system_prompt.txt"
APPROVAL_PROMPT_PATH = PROMPTS_DIR / "approval_prompt.txt"

# Ensure required directories exist as early as possible.
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Load environment variables from .env (does nothing if the file is absent,
# which allows the real environment/host to supply variables instead).
load_dotenv(BASE_DIR / ".env")


def _get_bool(name: str, default: bool) -> bool:
    """Parse a boolean-like environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_list(name: str, default: Optional[List[str]] = None) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return default or []
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Static settings sourced from environment variables (.env)."""

    # Application
    app_env: str = os.getenv("APP_ENV", "production")
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = field(default_factory=lambda: _get_int("APP_PORT", 8000))
    app_debug: bool = field(default_factory=lambda: _get_bool("APP_DEBUG", False))
    app_secret_key: str = os.getenv("APP_SECRET_KEY", "")

    # WhatsApp Cloud API
    whatsapp_access_token: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_business_account_id: str = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    whatsapp_app_secret: str = os.getenv("WHATSAPP_APP_SECRET", "")
    whatsapp_api_version: str = os.getenv("WHATSAPP_API_VERSION", "v20.0")
    owner_whatsapp_number: str = os.getenv("OWNER_WHATSAPP_NUMBER", "")

    # OpenRouter
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_primary_model: str = os.getenv("OPENROUTER_PRIMARY_MODEL", "openai/gpt-oss-120b")
    openrouter_fallback_model: str = os.getenv("OPENROUTER_FALLBACK_MODEL", "openai/gpt-oss-20b")
    openrouter_timeout_seconds: int = field(default_factory=lambda: _get_int("OPENROUTER_TIMEOUT_SECONDS", 30))
    openrouter_max_retries: int = field(default_factory=lambda: _get_int("OPENROUTER_MAX_RETRIES", 3))
    openrouter_temperature: float = field(default_factory=lambda: _get_float("OPENROUTER_TEMPERATURE", 0.7))
    openrouter_max_tokens: int = field(default_factory=lambda: _get_int("OPENROUTER_MAX_TOKENS", 800))
    openrouter_site_url: str = os.getenv("OPENROUTER_SITE_URL", "")
    openrouter_site_name: str = os.getenv("OPENROUTER_SITE_NAME", "WhatsApp AI Assistant")

    # Database
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'contacts.db').as_posix()}")

    # Admin / Security
    admin_numbers: List[str] = field(default_factory=lambda: _get_list("ADMIN_NUMBERS"))
    internal_api_key: str = os.getenv("INTERNAL_API_KEY", "")

    # Rate limiting
    rate_limit_messages_per_minute: int = field(default_factory=lambda: _get_int("RATE_LIMIT_MESSAGES_PER_MINUTE", 10))
    rate_limit_cooldown_seconds: int = field(default_factory=lambda: _get_int("RATE_LIMIT_COOLDOWN_SECONDS", 60))

    # Spam detection
    spam_repeat_threshold: int = field(default_factory=lambda: _get_int("SPAM_REPEAT_THRESHOLD", 4))
    spam_window_seconds: int = field(default_factory=lambda: _get_int("SPAM_WINDOW_SECONDS", 30))
    spam_mute_minutes: int = field(default_factory=lambda: _get_int("SPAM_MUTE_MINUTES", 15))

    # Human override
    human_override_timeout_minutes: int = field(default_factory=lambda: _get_int("HUMAN_OVERRIDE_TIMEOUT_MINUTES", 30))

    # Typing delay
    typing_delay_min_seconds: float = field(default_factory=lambda: _get_float("TYPING_DELAY_MIN_SECONDS", 2))
    typing_delay_max_seconds: float = field(default_factory=lambda: _get_float("TYPING_DELAY_MAX_SECONDS", 5))

    # Memory (Conversation History - unchanged, existing feature)
    memory_max_messages: int = field(default_factory=lambda: _get_int("MEMORY_MAX_MESSAGES", 20))
    memory_max_characters: int = field(default_factory=lambda: _get_int("MEMORY_MAX_CHARACTERS", 6000))

    # Smart Memory (new, independent feature - see smart_memory.py)
    # Prompt budget: caps how many Smart Memory facts, and how many total
    # characters of Smart Memory text, are ever injected into the AI prompt.
    smart_memory_max_items: int = field(default_factory=lambda: _get_int("SMART_MEMORY_MAX_ITEMS", 15))
    smart_memory_max_characters: int = field(default_factory=lambda: _get_int("SMART_MEMORY_MAX_CHARACTERS", 2000))
    # Whether to use the configured AI model to extract structured memories
    # from free-form messages (recommended). When disabled, or when the AI
    # call fails for any reason, extraction falls back to a deterministic
    # regex-based extractor covering common statement patterns.
    smart_memory_ai_extraction_enabled: bool = field(
        default_factory=lambda: _get_bool("SMART_MEMORY_AI_EXTRACTION_ENABLED", True)
    )
    # Minimum confidence (0.0-1.0) required for an automatically inferred
    # memory (source=conversation_inference / user_statement) to be saved.
    # Explicit user commands ("remember my ...") always bypass this check.
    smart_memory_min_confidence: float = field(
        default_factory=lambda: _get_float("SMART_MEMORY_MIN_CONFIDENCE", 0.55)
    )
    # Default retention policy (in days) applied to SHORT_TERM and
    # TEMPORARY memories when no explicit expiry is provided.
    smart_memory_short_term_days: int = field(default_factory=lambda: _get_int("SMART_MEMORY_SHORT_TERM_DAYS", 14))
    smart_memory_temporary_days: int = field(default_factory=lambda: _get_int("SMART_MEMORY_TEMPORARY_DAYS", 1))

    # Groups
    ignore_group_messages: bool = field(default_factory=lambda: _get_bool("IGNORE_GROUP_MESSAGES", True))

    # Text-to-Speech (AI Voice Reply) and Speech-to-Text (incoming voice).
    # Both use OpenRouter's dedicated audio endpoints
    # (/api/v1/audio/speech, /api/v1/audio/transcriptions) with the SAME
    # OPENROUTER_API_KEY/OPENROUTER_BASE_URL already configured above for
    # chat - no separate provider/API key. See tts.py / stt.py.
    openrouter_tts_model: str = os.getenv("OPENROUTER_TTS_MODEL", "openai/gpt-4o-mini-tts")
    openrouter_stt_model: str = os.getenv("OPENROUTER_STT_MODEL", "openai/whisper-large-v3")
    tts_timeout_seconds: int = field(default_factory=lambda: _get_int("TTS_TIMEOUT_SECONDS", 30))
    # Hard safety ceiling on how much text is ever sent to the TTS
    # provider per reply, regardless of the dashboard's configured
    # per-reply limit (see runtime settings "voice_reply.max_characters",
    # clamped to this value). Protects against runaway TTS API cost from
    # a single very long AI reply.
    tts_max_characters: int = field(default_factory=lambda: _get_int("TTS_MAX_CHARACTERS", 1200))

    # Incoming voice (speech-to-text)
    voice_input_enabled: bool = field(default_factory=lambda: _get_bool("VOICE_INPUT_ENABLED", False))
    voice_max_duration_seconds: int = field(default_factory=lambda: _get_int("VOICE_MAX_DURATION_SECONDS", 120))
    voice_max_file_size_mb: float = field(default_factory=lambda: _get_float("VOICE_MAX_FILE_SIZE_MB", 16))
    voice_transcription_timeout: int = field(default_factory=lambda: _get_int("VOICE_TRANSCRIPTION_TIMEOUT", 30))
    save_transcription: bool = field(default_factory=lambda: _get_bool("SAVE_TRANSCRIPTION", True))

    # Web search (see web_search.py). Empty by default - no provider is
    # wired up in this codebase; set these only after adding a real
    # search-provider integration (BACKEND API REQUIRED, see
    # web_search.py).
    web_search_provider: str = os.getenv("WEB_SEARCH_PROVIDER", "")
    web_search_api_key: str = os.getenv("WEB_SEARCH_API_KEY", "")

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_dir: str = os.getenv("LOG_DIR", str(LOGS_DIR))
    log_file: str = os.getenv("LOG_FILE", "app.log")
    log_max_bytes: int = field(default_factory=lambda: _get_int("LOG_MAX_BYTES", 5 * 1024 * 1024))
    log_backup_count: int = field(default_factory=lambda: _get_int("LOG_BACKUP_COUNT", 5))


settings = Settings()


# ---------------------------------------------------------------------------
# Dynamic runtime settings (data/settings.json)
# ---------------------------------------------------------------------------
_DEFAULT_RUNTIME_SETTINGS: Dict[str, Any] = {
    "global_ai_enabled": True,
    "working_hours": {
        "enabled": False,
        "start_time": "09:00",
        "end_time": "18:00",
        "timezone": "UTC",
        "outside_hours_action": "away_message",  # no_reply | emergency_only | away_message
        "away_message": "Thanks for your message! I'm currently outside working hours and will reply as soon as I'm back.",
        "emergency_keywords": ["emergency", "urgent", "help now", "asap"],
    },
    "welcome_message": "Hi! Thanks for reaching out. This is an automated assistant and I'll get back to you shortly.",
    "auto_save_unknown_contacts": True,
    "approval": {
        "require_owner_approval_for_sensitive": True,
        "approval_timeout_minutes": 15,
    },
    "typing_delay": {
        "enabled": True,
        "min_seconds": 2,
        "max_seconds": 5,
    },
    # AI Voice Reply (optional TTS). OFF by default so every existing
    # deployment continues sending text-only replies exactly as before -
    # see tts.py / voice_reply.py module docstrings for the full design.
    "voice_reply": {
        "enabled": False,
        "reply_mode": "text_only",  # text_only | voice_only | text_and_voice
        "voice": "alloy",
        "speed": 1.0,
        "format": "mp3",
        "max_characters": 600,
        "text_fallback_enabled": True,
    },
    # Web search control. OFF by default. NOTE: no real search provider
    # is wired up yet (see web_search.py) - no search API key/provider
    # exists anywhere in this project. These settings are real and
    # dashboard-editable, and the AI is told to say so honestly rather
    # than claim a search happened, but actually EXECUTING a search
    # requires a provider to be added - see web_search.py module
    # docstring for the exact integration point / BACKEND API REQUIRED.
    "web_search": {
        "enabled": False,
        "mode": "automatic",  # automatic | always | never | only_when_requested
    },
}

_runtime_lock = threading.Lock()


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        _write_json(path, default)
        return json.loads(json.dumps(default))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupted or unreadable file: restore defaults rather than crash.
        _write_json(path, default)
        return json.loads(json.dumps(default))


def load_runtime_settings() -> Dict[str, Any]:
    """Load the dynamic runtime settings, creating defaults on first run."""
    with _runtime_lock:
        data = _read_json(SETTINGS_JSON_PATH, _DEFAULT_RUNTIME_SETTINGS)
        # Merge in any new default keys that may have been added in newer
        # versions of the application, without overwriting existing values.
        merged = json.loads(json.dumps(_DEFAULT_RUNTIME_SETTINGS))
        _deep_merge(merged, data)
        return merged


def save_runtime_settings(data: Dict[str, Any]) -> None:
    """Persist the dynamic runtime settings to disk."""
    with _runtime_lock:
        _write_json(SETTINGS_JSON_PATH, data)


def update_runtime_setting(dotted_key: str, value: Any) -> Dict[str, Any]:
    """
    Update a single nested runtime setting using a dotted key path,
    e.g. update_runtime_setting("working_hours.start_time", "09:30").
    Returns the full updated settings dict.
    """
    data = load_runtime_settings()
    node = data
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    save_runtime_settings(data)
    return data


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ---------------------------------------------------------------------------
# Owner profile (data/profile.json)
# ---------------------------------------------------------------------------
_DEFAULT_PROFILE: Dict[str, Any] = {
    "name": "",
    "profession": "",
    "language": "English",
    "preferences": [],
    "writing_style": "friendly, concise, and professional",
    "rules": [
        "Never share private or sensitive information.",
        "Always be polite and helpful.",
    ],
}


def load_profile() -> Dict[str, Any]:
    """Load the owner profile, creating a default file on first run."""
    return _read_json(PROFILE_JSON_PATH, _DEFAULT_PROFILE)


def save_profile(data: Dict[str, Any]) -> None:
    """Persist the owner profile to disk."""
    with _runtime_lock:
        _write_json(PROFILE_JSON_PATH, data)


def reload_all() -> None:
    """
    Force-reload dynamic configuration from disk.
    Used by the admin `/reload` command. Static settings from .env require
    a process restart to change, by design, since secrets should not be
    hot-reloaded from disk without an explicit restart.
    """
    load_runtime_settings()
    load_profile()
