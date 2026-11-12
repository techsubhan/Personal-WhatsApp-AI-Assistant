"""
security.py
============
Security-related detection and sanitization utilities used throughout the
request pipeline.

Responsibilities
-----------------
1. Sensitive Information Detection: flag inbound messages that request
   passwords, OTPs, bank details, private/personal data, financial
   information, private contacts, or photos, so the caller (routes.py) can
   route them through the owner-approval workflow instead of answering
   automatically.
2. Secret Leak Scanner: scan an AI-generated reply before it is ever sent
   to a contact, and strip/redact anything that looks like a configured
   secret (API keys, access tokens, internal config values) or a verbatim
   fragment of the system prompt / owner profile, so those can never leak
   to an end user even if the model tries to repeat them back.
3. Input sanitization: strip null bytes/control characters and enforce a
   sane maximum length on all inbound user text before it is used anywhere
   (logs, prompts, DB), and flag likely prompt-injection attempts (e.g.
   "ignore previous instructions", "reveal your system prompt") for
   logging and extra caution - without ever trying to silently "fix" the
   message, since that could hide an attack rather than surface it.
4. Smart Memory content filtering: a separate, stricter check used only by
   smart_memory.py before a fact is ever auto-saved to long-term storage.
   This is deliberately distinct from (1) above - (1) flags a contact
   *asking for* sensitive information so it can be routed to the owner for
   approval; (4) flags the contact's *own statement* containing sensitive
   content (e.g. "my password is hunter2") so it is never written to
   permanent Smart Memory storage in the first place, regardless of who
   said it or why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from config import settings, load_profile, SYSTEM_PROMPT_PATH
from logger import get_logger, log_warning

logger = get_logger("security")

MAX_INPUT_LENGTH = 4000
REDACTION_PLACEHOLDER = "[redacted]"


# ---------------------------------------------------------------------------
# 1. Sensitive Information Detection
# ---------------------------------------------------------------------------
@dataclass
class SensitiveMatch:
    category: str
    matched_text: str


# Each entry: (category label, compiled regex). Patterns are intentionally
# broad (favoring recall over precision) since a false positive here only
# costs an extra owner-approval round trip, while a false negative could
# leak sensitive information.
_SENSITIVE_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    ("password", re.compile(r"\b(password|passcode|pass ?word|login credentials?)\b", re.IGNORECASE)),
    ("otp", re.compile(r"\b(otp|one[- ]time (password|code)|verification code|2fa code)\b", re.IGNORECASE)),
    ("bank_details", re.compile(
        r"\b(bank (account|details|routing)|iban|swift code|account number|sort code)\b", re.IGNORECASE
    )),
    ("financial_information", re.compile(
        r"\b(credit card|debit card|card number|cvv|cvc|expiry date|salary|income|net worth|"
        r"financial statement|finance|financial (info|information|details?))\b",
        re.IGNORECASE,
    )),
    ("private_contacts", re.compile(
        r"\b(phone number|home address|where (do|does) .* live|private contact|private number|"
        r"contact details of)\b|\bnumber\b[^.!?\n]{0,15}\b(bhej|send)\b",
        re.IGNORECASE,
    )),
    ("personal_secrets", re.compile(
        r"\b(secret|confidential|private key|ssn|social security number|national id|passport number)\b",
        re.IGNORECASE,
    )),
    ("photos", re.compile(r"\b(send (me )?(a )?photo|send (me )?(a )?picture|share (a )?photo of)\b", re.IGNORECASE)),
]


_HUMAN_HANDOFF_PATTERNS: List[re.Pattern[str]] = [
    # English
    re.compile(r"\b(talk to (a |the )?(human|owner|person|someone)|speak to (a |the )?(human|owner)|"
               r"connect me (to|with) (a |the )?(human|owner))\b", re.IGNORECASE),
    # Roman Urdu / Roman Punjabi - deliberately anchored to "human"/
    # "owner"/"insaan" (not a bare "se baat karni hai"), since the latter
    # alone also matches ordinary sentences about wanting to talk to some
    # OTHER named person (e.g. "Mujhe Ahmed se baat karni hai" about a
    # third party), which would incorrectly trigger a handoff. See
    # is_human_handoff_request()'s owner_name parameter for how a message
    # naming the actual configured owner is still caught correctly.
    re.compile(r"\b(insaan se baat|human se baat|owner se baat|baat kar[vw]a)\b", re.IGNORECASE),
    # Urdu script
    re.compile(r"(انسان سے بات|مالک سے بات|بات کروا)"),
]


def is_human_handoff_request(text: str, owner_name: Optional[str] = None) -> bool:
    """
    True if the message is an explicit request to be connected to a human
    / the owner (e.g. "Mujhe Subhan se baat karni hai" where Subhan is
    the configured owner, "I want to talk to a human"). Callers activate
    the existing Human Override mechanism on a match - see routes.py
    Step 8c.

    `owner_name`, when given (the owner's configured display name from
    their profile), additionally matches "<owner_name> se baat" /
    "<owner_name> se baat karni/karo/karwao" - this is intentionally
    separate from the generic patterns above specifically so a bare
    "<any random name> se baat karni hai" about some unrelated third
    party does NOT false-positive into a handoff (see the module comment
    on _HUMAN_HANDOFF_PATTERNS).
    """
    if not text:
        return False
    if any(p.search(text) for p in _HUMAN_HANDOFF_PATTERNS):
        return True
    if owner_name and owner_name.strip():
        name = re.escape(owner_name.strip())
        owner_patterns = [
            re.compile(rf"\b{name}\s+se\s+baat\b", re.IGNORECASE),  # Roman Urdu/Punjabi
            re.compile(rf"\btalk to\s+{name}\b", re.IGNORECASE),  # English
            re.compile(rf"\bspeak to\s+{name}\b", re.IGNORECASE),  # English
        ]
        if any(p.search(text) for p in owner_patterns):
            return True
    return False


def detect_sensitive_request(text: str) -> Optional[SensitiveMatch]:
    """
    Scan an inbound message for a request touching sensitive information.
    Returns the first matching category, or None if nothing matched.
    """
    if not text:
        return None

    for category, pattern in _SENSITIVE_PATTERNS:
        match = pattern.search(text)
        if match:
            return SensitiveMatch(category=category, matched_text=match.group(0))

    return None


# ---------------------------------------------------------------------------
# 2. Secret Leak Scanner
# ---------------------------------------------------------------------------
def _known_secret_values() -> List[str]:
    """Collect every configured secret/internal value that must never be echoed back."""
    values = [
        settings.whatsapp_access_token,
        settings.whatsapp_app_secret,
        settings.whatsapp_verify_token,
        settings.openrouter_api_key,
        settings.app_secret_key,
        settings.internal_api_key,
        settings.database_url,
        # web_search_api_key: TTS/STT deliberately reuse
        # settings.openrouter_api_key (already covered above), so no new
        # secret was introduced by those two features - only web search's
        # (currently-unconfigured, empty-by-default) provider key adds a
        # genuinely new value here.
        settings.web_search_api_key,
    ]
    return [v for v in values if v and len(v) >= 6]


def _system_prompt_fragments() -> List[str]:
    """
    Return long substrings of the system prompt file (if present), used to
    detect the AI verbatim-repeating large chunks of internal instructions.
    Only substrings of meaningful length are checked to avoid false
    positives on short, common phrases.
    """
    if not SYSTEM_PROMPT_PATH.exists():
        return []
    try:
        content = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if len(content) < 40:
        return []
    # Use the whole file as a single fragment to check for containment;
    # checking for the *whole* prompt appearing verbatim is intentionally
    # strict enough to avoid interfering with normal conversation.
    return [content]


def scan_and_sanitize_reply(reply_text: str) -> str:
    """
    Scan an AI-generated reply for secret leakage before it is sent to a
    contact. Any configured secret value found verbatim in the reply is
    replaced with a redaction placeholder. If the reply appears to contain
    the system prompt verbatim, the whole reply is replaced with a safe
    generic message instead, since that indicates a serious prompt leak
    rather than an incidental substring match.
    """
    sanitized = reply_text

    for secret in _known_secret_values():
        if secret in sanitized:
            logger.error("Detected a configured secret value in an AI reply; redacting it.")
            sanitized = sanitized.replace(secret, REDACTION_PLACEHOLDER)

    for fragment in _system_prompt_fragments():
        if fragment in sanitized:
            logger.error("AI reply appears to contain the system prompt verbatim; blocking reply.")
            return "Sorry, I can't share that. Is there something else I can help you with?"

    profile = load_profile()
    profile_rules_text = " ".join(profile.get("rules", []))
    if profile_rules_text and len(profile_rules_text) > 20 and profile_rules_text in sanitized:
        logger.error("AI reply appears to contain the owner profile's internal rules verbatim; redacting.")
        sanitized = sanitized.replace(profile_rules_text, REDACTION_PLACEHOLDER)

    return sanitized


# ---------------------------------------------------------------------------
# 3. Input sanitization / prompt-injection detection
# ---------------------------------------------------------------------------
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore (all )?(previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"disregard (the )?system prompt", re.IGNORECASE),
    re.compile(r"reveal (your |the )?(system prompt|instructions|rules)", re.IGNORECASE),
    re.compile(r"you are now (in )?(developer|debug|jailbreak) mode", re.IGNORECASE),
    re.compile(r"print (your |the )?(api key|access token|secret)", re.IGNORECASE),
]


def sanitize_input(text: str) -> str:
    """
    Strip null bytes and other control characters from inbound user text
    and enforce a maximum length, so malformed or oversized input can
    never destabilize downstream processing (DB storage, prompt building,
    logging).
    """
    if not text:
        return ""
    cleaned = _CONTROL_CHAR_PATTERN.sub("", text)
    if len(cleaned) > MAX_INPUT_LENGTH:
        cleaned = cleaned[:MAX_INPUT_LENGTH]
    return cleaned.strip()


def detect_prompt_injection(text: str) -> bool:
    """
    Return True if the message looks like an attempt to manipulate the AI
    into ignoring its instructions or leaking internal configuration. This
    is used for logging/monitoring - the message is still processed
    normally through the standard prompt structure (which keeps the system
    prompt and user content clearly separated), but a flagged attempt is
    recorded so repeated attempts are visible to the admin.
    """
    if not text:
        return False
    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            log_warning(f"Possible prompt-injection attempt detected: matched pattern '{pattern.pattern}'")
            return True
    return False


# ---------------------------------------------------------------------------
# 4. Smart Memory content filtering
# ---------------------------------------------------------------------------
# These credential-like categories must NEVER be written to Smart Memory,
# even if the contact explicitly says "remember my password" - unlike the
# "soft-blocked" categories below, there is no legitimate reason for a
# WhatsApp assistant to retain these long-term, and doing so would create
# a serious standing security liability.
_HARD_BLOCKED_MEMORY_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    ("password", re.compile(r"\bpassword\b|\bpass ?word\b", re.IGNORECASE)),
    ("otp", re.compile(r"\b(otp|one[- ]time (password|code)|2fa code)\b", re.IGNORECASE)),
    ("pin", re.compile(r"\b(pin code|my pin\b|\bpin is\b)", re.IGNORECASE)),
    ("recovery_code", re.compile(r"\brecovery code(s)?\b|\bbackup code(s)?\b", re.IGNORECASE)),
    ("api_key", re.compile(r"\bapi[- ]?key\b", re.IGNORECASE)),
    ("access_token", re.compile(r"\baccess token\b|\bbearer token\b", re.IGNORECASE)),
    ("secret_key", re.compile(r"\bsecret key\b|\bclient secret\b", re.IGNORECASE)),
    ("private_key", re.compile(r"\bprivate key\b", re.IGNORECASE)),
    ("auth_cookie", re.compile(r"\b(auth(entication)? cookie|session cookie)\b", re.IGNORECASE)),
    ("session_token", re.compile(r"\bsession token\b", re.IGNORECASE)),
    ("cvv", re.compile(r"\bcvv\b|\bcvc\b|\bcard verification\b", re.IGNORECASE)),
    ("card_number", re.compile(r"\b(?:\d[ -]*?){13,19}\b"), ),  # long digit runs (card-like)
    ("card_number_phrase", re.compile(r"\b(card|debit|credit) number\b", re.IGNORECASE)),
]

# These categories are only allowed to be auto-saved when the message also
# contains an explicit "remember this" style instruction (checked
# separately in smart_memory.py via detect_explicit_remember_command());
# otherwise they are skipped even though they are not hard-blocked.
_SOFT_BLOCKED_MEMORY_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    ("bank_details", re.compile(r"\b(bank (account|details|routing)|iban|swift code|sort code)\b", re.IGNORECASE)),
    ("government_id", re.compile(r"\b(ssn|social security number|national id|passport number|cnic)\b", re.IGNORECASE)),
    ("medical_information", re.compile(
        r"\b(diagnosis|medical (condition|record)|prescription|medication|disability)\b", re.IGNORECASE
    )),
    ("financial_account", re.compile(r"\b(account number|routing number|financial account)\b", re.IGNORECASE)),
]


@dataclass
class MemoryContentCheck:
    """Result of checking a candidate Smart Memory value for sensitive content."""

    hard_blocked: bool
    soft_blocked: bool
    matched_category: Optional[str] = None


def check_memory_content_sensitivity(text: str) -> MemoryContentCheck:
    """
    Check whether a piece of text that smart_memory.py is about to save as
    a permanent fact contains sensitive content.

    Returns a `MemoryContentCheck`:
      - hard_blocked=True  -> must NEVER be saved, no exceptions (secrets/credentials).
      - soft_blocked=True  -> must only be saved if the user explicitly said
                              "remember this" (financial accounts, government
                              IDs, medical information, bank details).
      - both False         -> safe to save under normal automatic rules.
    """
    if not text:
        return MemoryContentCheck(hard_blocked=False, soft_blocked=False)

    for category, pattern in _HARD_BLOCKED_MEMORY_PATTERNS:
        if pattern.search(text):
            logger.info("Blocked a candidate Smart Memory value: matched hard-blocked category '%s'.", category)
            return MemoryContentCheck(hard_blocked=True, soft_blocked=False, matched_category=category)

    for category, pattern in _SOFT_BLOCKED_MEMORY_PATTERNS:
        if pattern.search(text):
            return MemoryContentCheck(hard_blocked=False, soft_blocked=True, matched_category=category)

    return MemoryContentCheck(hard_blocked=False, soft_blocked=False)
