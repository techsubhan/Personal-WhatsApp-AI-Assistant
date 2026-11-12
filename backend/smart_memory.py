"""
smart_memory.py
================
Smart Memory: a structured, deduplicated, long-term memory system that
remembers important facts about a contact (name, city, favourite
programming language, goals, etc.) across the entire lifetime of the
relationship - completely independent of Conversation History
(memory.py / the `Message` table), which is left entirely untouched by
this module.

Relationship to Conversation History
--------------------------------------
    Contact
        |
        |------ Conversation Messages   (memory.py, Message table -
        |                                 every message, verbatim, bounded
        |                                 recent window sent to the LLM)
        |
        |------ Smart Memories          (this module, UserMemory table -
                                          structured facts, permanent by
                                          default, small curated summary
                                          sent to the LLM)

Both systems are consulted when answering a question (Smart Memory first,
then a targeted retrieval search over full Conversation History via
`memory.search_conversation_history()` - see prompt_builder.py), but they
are stored, updated, and pruned completely independently.

Responsibilities
-----------------
- Define the category/subcategory schema (23 categories) used to classify
  every Smart Memory fact.
- Extract candidate memories from an inbound message, either via the
  configured AI model (primary path, handles arbitrary phrasing across the
  whole schema) or a deterministic regex fallback (used when AI extraction
  is disabled/unavailable/fails), and via explicit user commands
  ("remember ...", "forget ...", "update my city to ...") which always take
  priority over automatic inference.
- Enforce the sensitive-content rules: hard-blocked categories (passwords,
  OTPs, API keys, etc.) are NEVER auto-saved, even on explicit request;
  soft-blocked categories (bank details, government IDs, medical info) are
  only saved when the user explicitly says "remember this".
- Deduplicate and version facts: saving a new value for a (contact,
  category, subcategory) that already has an active memory updates the
  existing row and records the previous value in `MemoryHistory`, rather
  than creating a duplicate row or silently losing the old value.
- Provide semantic-ish search over stored memories (alias/synonym
  matching + fuzzy matching), designed so the matching strategy can later
  be swapped for embeddings/vector search without changing the public
  function signatures below.
- Automatically re-rank importance based on usage (access_count).
- Expire Short-Term / Temporary memories once past their `expires_at`.
- Build the compact "Smart Memory" text block injected into the AI prompt,
  respecting a configurable item-count and character budget.
- Provide memory analytics (`get_memory_statistics`) for future admin
  dashboards.

Required public functions (per project specification)
--------------------------------------------------------
    save_memory()
    update_memory()
    delete_memory()
    search_memory()
    get_memory()
    get_all_memory()
    extract_memories_from_message()
    build_memory_prompt()
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai import AIGenerationError, openrouter_client
from config import settings
from logger import get_logger
from models import (
    Contact,
    ImportanceLevel,
    MemoryCategory,
    MemoryHistory,
    MemorySource,
    MemoryType,
    RetentionPolicy,
    UserMemory,
)
from security import check_memory_content_sensitivity

logger = get_logger("smart_memory")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# Category / subcategory schema
# =============================================================================
# The canonical subcategory list for every category, exactly as defined in
# the project specification. Used for (a) validating/normalizing whatever
# subcategory the AI-extraction step returns, and (b) semantic search
# matching. `CATEGORY_SUBCATEGORIES` is intentionally plain data (not
# enforced by the database - `subcategory` is a free string column) so new
# subcategories can be added here without a schema migration.
# =============================================================================
CATEGORY_SUBCATEGORIES: Dict[MemoryCategory, List[str]] = {
    MemoryCategory.PERSONAL: [
        "Full Name", "First Name", "Last Name", "Nickname", "Gender",
        "Date of Birth", "Age", "Marital Status", "Nationality", "Religion",
        "Languages", "Bio", "Personality Traits",
    ],
    MemoryCategory.CONTACT: [
        "Phone Number", "Secondary Phone", "Email", "Address", "City",
        "State", "Province", "Country", "Postal Code", "Time Zone",
        "Emergency Contact",
    ],
    MemoryCategory.EDUCATION: [
        "School", "College", "University", "Degree", "Department",
        "Semester", "Student ID", "Graduation Year", "CGPA",
        "Certifications", "Courses",
    ],
    MemoryCategory.WORK: [
        "Profession", "Company", "Position", "Experience", "Salary",
        "Business", "Department", "Employee ID", "Office Location",
        "Work Schedule",
    ],
    MemoryCategory.PROJECTS: [
        "Current Project", "Completed Projects", "FYP", "Freelance Projects",
        "Startup", "GitHub", "Portfolio", "Website", "Technologies Used",
        "APIs Used",
    ],
    MemoryCategory.SKILLS: [
        "Programming Languages", "Frameworks", "Databases", "DevOps",
        "AI Skills", "Cloud Platforms", "Operating Systems", "Soft Skills",
        "Spoken Languages", "Certifications",
    ],
    MemoryCategory.PREFERENCES: [
        "Favorite Programming Language", "Favorite IDE", "Favorite OS",
        "Favorite Browser", "Favorite Color", "Favorite Food",
        "Favorite Drink", "Favorite Movie", "Favorite TV Show",
        "Favorite Book", "Favorite Music", "Favorite Sport", "Favorite Team",
        "Favorite Player", "Favorite Game", "Favorite Place", "Favorite Brand",
    ],
    MemoryCategory.GOALS: [
        "Career Goal", "Learning Goal", "Personal Goal", "Financial Goal",
        "Fitness Goal", "Business Goal", "Travel Goal", "Daily Goal",
        "Long-term Goal",
    ],
    MemoryCategory.TRAVEL: [
        "Countries Visited", "Cities Visited", "Current Country",
        "Current City", "Dream Destination", "Passport Status",
        "Visa Status", "Travel Plans",
    ],
    MemoryCategory.FAMILY: [
        "Father", "Mother", "Brother", "Sister", "Wife", "Husband",
        "Children", "Relatives",
    ],
    MemoryCategory.FRIENDS_AND_RELATIONSHIPS: [
        "Best Friend", "Friends", "Mentor", "Team Members", "Partner",
        "Important Contacts",
    ],
    MemoryCategory.PETS: ["Pet Name", "Pet Type", "Pet Breed", "Pet Age"],
    MemoryCategory.HEALTH: [
        "Blood Group", "Allergies", "Medical Conditions", "Disabilities",
        "Diet Preference", "Fitness Routine", "Height", "Weight",
    ],
    MemoryCategory.FINANCE: [
        "Currency", "Monthly Budget", "Payment Preference", "Bank Name",
        "Expense Goal", "Saving Goal",
    ],
    MemoryCategory.SOCIAL_ACCOUNTS: [
        "Facebook", "Instagram", "LinkedIn", "GitHub", "X", "YouTube",
        "Discord", "Telegram", "Website",
    ],
    MemoryCategory.DEVICES: [
        "Laptop", "Desktop", "Phone", "Tablet", "Operating System",
        "Browser", "IDE", "Processor", "RAM", "GPU",
    ],
    MemoryCategory.IMPORTANT_DATES: [
        "Birthday", "Anniversary", "Exam Date", "Interview Date",
        "Deadline", "Meeting Date", "Appointment", "Reminder",
    ],
    MemoryCategory.CONVERSATION_PREFERENCES: [
        "Preferred Language", "Preferred Tone", "Preferred Response Length",
        "Emoji Preference", "Formal Style", "Code Style", "Explanation Level",
    ],
    MemoryCategory.AI_SETTINGS: [
        "AI Enabled", "Human Override", "Working Hours",
        "Custom Instructions", "AI Personality", "Reply Speed",
    ],
    MemoryCategory.INTERESTS: [
        "Technology", "AI", "Programming", "Sports", "Movies", "Music",
        "Books", "Gaming", "Business", "Politics", "Science",
    ],
    MemoryCategory.EVENTS_AND_EXPERIENCES: [
        "Trips", "Competitions", "Achievements", "Awards", "Conferences",
        "Internships", "Jobs", "Major Life Events",
    ],
    MemoryCategory.MEMORIES: [
        "Funny Memories", "Personal Stories", "Childhood Memories",
        "Important Conversations", "Favorite Moments",
    ],
    MemoryCategory.OTHER: ["Other"],
}

# Flat lookup of every canonical subcategory string -> its category, and a
# flat list of all subcategory strings, both used for normalization/fuzzy
# matching in `_normalize_subcategory` and `_resolve_query_aliases`.
_ALL_SUBCATEGORIES: List[str] = [
    sub for subs in CATEGORY_SUBCATEGORIES.values() for sub in subs
]
_SUBCATEGORY_TO_CATEGORY: Dict[str, MemoryCategory] = {
    sub.lower(): cat for cat, subs in CATEGORY_SUBCATEGORIES.items() for sub in subs
}

# Hand-authored synonym/alias map for common ways people phrase these
# subcategories, used by both extraction (regex fallback) and semantic
# search. This is a deliberately simple, swappable "semantic layer" - see
# `_resolve_query_aliases` docstring for how this could later be replaced
# by a real embedding/vector search without changing any public function
# signature in this module.
SUBCATEGORY_ALIASES: Dict[str, str] = {
    # Contact
    "phone": "Phone Number", "mobile": "Phone Number", "cell": "Phone Number",
    "cell number": "Phone Number", "mobile number": "Phone Number", "number": "Phone Number",
    "whatsapp number": "Phone Number", "contact number": "Phone Number",
    "email address": "Email", "mail": "Email",
    "home address": "Address", "current address": "Address", "residence": "Address",
    "where i live": "Address", "where i live now": "City",
    # Preferences
    "favourite programming language": "Favorite Programming Language",
    "favorite programming language": "Favorite Programming Language",
    "favourite language": "Favorite Programming Language",
    "favorite language": "Favorite Programming Language",
    "programming language": "Favorite Programming Language",
    "coding language": "Favorite Programming Language",
    "preferred programming language": "Favorite Programming Language",
    "favourite editor": "Favorite IDE", "favorite editor": "Favorite IDE",
    "favourite food": "Favorite Food", "favorite food": "Favorite Food",
    "favourite movie": "Favorite Movie", "favorite movie": "Favorite Movie",
    "favourite color": "Favorite Color", "favorite color": "Favorite Color",
    "favourite colour": "Favorite Color", "favorite colour": "Favorite Color",
    # Education
    "uni": "University", "college": "College", "school": "School",
    "student id": "Student ID", "gpa": "CGPA", "cgpa": "CGPA",
    # Work
    "job": "Position", "job title": "Position", "employer": "Company",
    "workplace": "Company", "profession": "Profession", "occupation": "Profession",
    # Personal
    "name": "Full Name", "my name": "Full Name", "full name": "Full Name",
    "birthday": "Date of Birth", "dob": "Date of Birth", "date of birth": "Date of Birth",
    # Travel
    "current location": "Current City", "current city": "Current City",
    "where i am now": "Current City", "moved to": "City",
    # Social accounts
    "github profile": "GitHub", "github username": "GitHub",
    "linkedin profile": "LinkedIn", "insta": "Instagram",
    # Family
    "wife": "Wife", "husband": "Husband", "spouse": "Wife",
    # Devices
    "laptop": "Laptop", "pc": "Desktop", "computer": "Desktop",
}


def _normalize_subcategory(raw_subcategory: str) -> Tuple[MemoryCategory, str]:
    """
    Normalize an arbitrary subcategory string (as returned by the AI
    extraction step, or typed by an admin) to the closest canonical
    (category, subcategory) pair in `CATEGORY_SUBCATEGORIES`.

    Resolution order: exact case-insensitive match -> alias map ->
    fuzzy match (difflib) against all known subcategories -> fall back to
    (OTHER, "Other") with the raw string preserved by the caller in `tags`
    so no information is silently discarded.
    """
    if not raw_subcategory:
        return MemoryCategory.OTHER, "Other"

    lowered = raw_subcategory.strip().lower()

    if lowered in _SUBCATEGORY_TO_CATEGORY:
        canonical = next(s for s in _ALL_SUBCATEGORIES if s.lower() == lowered)
        return _SUBCATEGORY_TO_CATEGORY[lowered], canonical

    if lowered in SUBCATEGORY_ALIASES:
        canonical = SUBCATEGORY_ALIASES[lowered]
        return _SUBCATEGORY_TO_CATEGORY[canonical.lower()], canonical

    close = difflib.get_close_matches(lowered, [s.lower() for s in _ALL_SUBCATEGORIES], n=1, cutoff=0.75)
    if close:
        canonical = next(s for s in _ALL_SUBCATEGORIES if s.lower() == close[0])
        return _SUBCATEGORY_TO_CATEGORY[close[0]], canonical

    return MemoryCategory.OTHER, "Other"


def _resolve_query_aliases(query_text: str) -> List[str]:
    """
    Resolve a free-text search query to a list of candidate canonical
    subcategory names using the alias map and fuzzy matching.

    Design note (future vector search): this function is the single
    "semantic matching" entry point used by `search_memory()` and
    `build_memory_prompt()`'s relevance boost. It currently uses a
    lightweight lexical/alias approach (exact alias lookup, then
    difflib fuzzy matching), which requires no external services and
    works fully offline. If a proper embedding/vector similarity backend
    is added later, only this function's *implementation* needs to
    change - its signature (str -> List[str] of candidate subcategory
    names) and every caller can stay exactly the same.
    """
    lowered = query_text.strip().lower()
    candidates: List[str] = []

    for alias, canonical in SUBCATEGORY_ALIASES.items():
        if alias in lowered:
            candidates.append(canonical)

    for word in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", lowered):
        close = difflib.get_close_matches(word, [s.lower() for s in _ALL_SUBCATEGORIES], n=2, cutoff=0.8)
        for match in close:
            canonical = next(s for s in _ALL_SUBCATEGORIES if s.lower() == match)
            if canonical not in candidates:
                candidates.append(canonical)

    return candidates


# =============================================================================
# Explicit command detection ("remember ...", "forget ...", "update ...")
# =============================================================================
_REMEMBER_PATTERNS = [
    re.compile(r"^\s*(please\s+)?remember\b", re.IGNORECASE),
    re.compile(r"\bremember that\b", re.IGNORECASE),
    re.compile(r"\bremember this\b", re.IGNORECASE),
    re.compile(r"\bremember my\b", re.IGNORECASE),
]
_FORGET_PATTERNS = [
    re.compile(r"^\s*(please\s+)?forget\b", re.IGNORECASE),
    re.compile(r"\bdon'?t remember\b", re.IGNORECASE),
    re.compile(r"\bforget (that|everything about|my)\b", re.IGNORECASE),
]
_UPDATE_PATTERN = re.compile(
    r"\bupdate my ([a-zA-Z \-]+?) to (.+)$", re.IGNORECASE
)


def is_explicit_remember_command(text: str) -> bool:
    """Return True if the message explicitly asks the assistant to remember something."""
    return bool(text) and any(p.search(text) for p in _REMEMBER_PATTERNS)


def is_explicit_forget_command(text: str) -> bool:
    """Return True if the message explicitly asks the assistant to forget something."""
    return bool(text) and any(p.search(text) for p in _FORGET_PATTERNS)


# ---------------------------------------------------------------------------
# Explicit "save <phone> as <name>" contact command
# ---------------------------------------------------------------------------
# This is a third explicit-command form alongside "remember ..." / "forget
# ...": the user is explicitly asking the assistant to save a *named
# third-party phone number* (e.g. "save 030-363-00910 as Bhai Waseem"),
# distinct from the built-in Contact table (which represents the WhatsApp
# conversation partner sending the message, not people they mention).
#
# Storage design: saved as a Smart Memory fact under MemoryCategory.CONTACT,
# with the phone number itself used as the `subcategory` (not a canonical
# label) and the person's name as the `value`. This deliberately reuses
# save_memory()'s existing dedup-by-(contact, category, subcategory) logic
# unmodified: re-saving the SAME phone number later (e.g. to correct the
# name) updates the existing memory in place via update_memory() and keeps
# the previous name in MemoryHistory, while DIFFERENT phone numbers simply
# get their own subcategory key and coexist as separate memories. No new
# storage/dedup logic was introduced - see save_memory()'s docstring.
_CONTACT_SAVE_PATTERNS = [
    # "save 030-363-00910 as Bhai Waseem" / "add ... as ..." (phone, name)
    (
        re.compile(
            r"\b(?:save|add)\b.{0,20}?([\d][\d \-\+\(\)]{5,20}\d)\s+as\s+[\"']?([A-Za-z][^.!?\n\"']{0,58}?)[\"']?(?=[.!?\n]|$)",
            re.IGNORECASE,
        ),
        "phone_first",
    ),
    # "030-363-00910 = Bhai Waseem" / "030-363-00910 is Bhai Waseem" (phone, name)
    (
        re.compile(
            r"\b([\d][\d \-\+\(\)]{5,20}\d)\s*(?:=|is)\s*[\"']?([A-Za-z][^.!?\n\"']{0,58}?)[\"']?(?=[.!?\n]|$)",
            re.IGNORECASE,
        ),
        "phone_first",
    ),
    # Roman Urdu, phone-first: "<phone> [ko] <name> ke naam/name [se] save
    # kar (lo/do/den/dena/karo)" - e.g. "03331234567 Subhan ke naam se save
    # kar lo" (phone, name)
    (
        re.compile(
            r"\b([\d][\d \-\+\(\)]{5,20}\d)\s*(?:ko\s+)?([A-Za-z][^.!?\n]{0,40}?)\s+ke\s+"
            r"(?:naam|name)\s*(?:se)?\s*save\s*kar",
            re.IGNORECASE,
        ),
        "phone_first",
    ),
    # Roman Urdu, name-first: "<name> ke naam/name se <phone> save kar
    # (lo/do/...)" - e.g. "Subhan ke naam se 03331234567 save kar lo"
    # (name, phone - reversed from every pattern above)
    (
        re.compile(
            r"\b([A-Za-z][^.!?\n]{0,40}?)\s+ke\s+(?:naam|name)\s*se\s+([\d][\d \-\+\(\)]{5,20}\d)\s*save\s*kar",
            re.IGNORECASE,
        ),
        "name_first",
    ),
]


def try_save_named_contact(
    db: Session, contact: Contact, text: str, *, message_id: Optional[str] = None
) -> Optional[Tuple[str, str]]:
    """
    Detect an explicit "save <phone> as <name>" style instruction - in
    English or the equivalent common Roman Urdu phrasing ("<phone> <name>
    ke naam se save kar lo" / "<name> ke naam se <phone> save kar do") -
    and, if matched, save it as a Smart Memory CONTACT fact (see module
    note above for the storage design). Returns (phone, name) if a save
    happened, otherwise None (caller falls through to normal processing).

    Deliberately bypasses the automatic-extraction confidence threshold
    and soft-block-without-explicit-confirmation rule (allow_soft_blocked=
    True): the user explicitly asked for this to be saved, exactly as if
    they had said "remember this" - the same trust level
    is_explicit_remember_command() already grants elsewhere in this file.
    """
    if not text:
        return None
    for pattern, order in _CONTACT_SAVE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if order == "phone_first":
            phone_raw, name_raw = match.group(1), match.group(2)
        else:
            name_raw, phone_raw = match.group(1), match.group(2)
        phone = re.sub(r"\s+", " ", phone_raw.strip())
        name = name_raw.strip().rstrip(".,")
        if not phone or not name:
            continue
        memory = save_memory(
            db, contact, MemoryCategory.CONTACT, phone, MemoryType.CONTACT, name,
            confidence=1.0, importance=ImportanceLevel.HIGH,
            retention_policy=RetentionPolicy.PERMANENT, source=MemorySource.USER_EXPLICIT,
            source_message_id=message_id, allow_soft_blocked=True,
        )
        if memory is not None:
            logger.info("Saved named contact for %s: %s -> %s", contact.wa_id, phone, name)
            return phone, name
    return None


# =============================================================================
# Extraction candidate representation
# =============================================================================
@dataclass
class MemoryCandidate:
    """A single candidate fact identified in a message, before validation/saving."""

    category: MemoryCategory
    subcategory: str
    memory_type: MemoryType
    value: str
    confidence: float = 0.7
    summary: Optional[str] = None
    tags: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex-based fallback extractor
# ---------------------------------------------------------------------------
# Covers the common statement patterns given as examples in the project
# specification. This is intentionally NOT exhaustive across all ~200
# subcategories - it is the safety-net path used only when AI extraction
# is disabled or fails, so the feature degrades gracefully rather than
# silently doing nothing.
#
# Sentence-boundary note: every captured value below is matched
# non-greedily up to (but not including) the next sentence-ending
# punctuation mark or newline, via `[^.!?\n]{0,N}?` followed by a
# `(?=[.!?\n]|$)` lookahead. An earlier version of these patterns instead
# whitelisted a period inside the captured character class (e.g.
# `[\w \-\.]`) so that names/institutions containing a "." (like "St.
# Xavier's" or "Dr.") could be captured - but that also meant the pattern
# had no way to tell "a period inside the fact" apart from "a period
# ending the sentence", so on a multi-sentence message like "My name is
# Ali. I study at UOL. I live in Lahore." it greedily swallowed everything
# up to the character-count cap instead of stopping after "Ali". The
# lookahead-based patterns below fix that: they stop at the first
# sentence-ending punctuation, which correctly handles multi-sentence
# messages (see the specification's own example) at the minor cost of not
# capturing a mid-value period (an edge case not covered by any example in
# the specification).
_REGEX_EXTRACTORS: List[Tuple[re.Pattern[str], MemoryCategory, str, MemoryType]] = [
    (re.compile(r"\bmy name is ([A-Za-z][^.!?\n]{0,58}?)(?=[.!?\n]|$)", re.IGNORECASE),
     MemoryCategory.PERSONAL, "Full Name", MemoryType.FACT),
    (re.compile(r"\bi live in ([A-Za-z][^.!?\n]{0,58}?)(?=[.!?\n]|$)", re.IGNORECASE),
     MemoryCategory.CONTACT, "City", MemoryType.FACT),
    (re.compile(r"\bi moved to ([A-Za-z][^.!?\n]{0,58}?)(?=[.!?\n]|$)", re.IGNORECASE),
     MemoryCategory.CONTACT, "City", MemoryType.FACT),
    (re.compile(r"\bi study at ([A-Za-z][^.!?\n]{0,78}?)(?=[.!?\n]|$)", re.IGNORECASE),
     MemoryCategory.EDUCATION, "University", MemoryType.EDUCATION),
    (re.compile(r"\bi go to ([A-Za-z][^.!?\n]{0,78}?) university", re.IGNORECASE),
     MemoryCategory.EDUCATION, "University", MemoryType.EDUCATION),
    (re.compile(r"\bi work at ([A-Za-z][^.!?\n]{0,78}?)(?=[.!?\n]|$)", re.IGNORECASE),
     MemoryCategory.WORK, "Company", MemoryType.WORK),
    (re.compile(r"\bi work as (?:an? )?([A-Za-z][^.!?\n]{0,58}?)(?=[.!?\n]|$)", re.IGNORECASE),
     MemoryCategory.WORK, "Position", MemoryType.WORK),
    (re.compile(r"\bmy favou?rite programming language is ([A-Za-z][^.!?\n]{0,38}?)(?=[.!?\n]|$)", re.IGNORECASE),
     MemoryCategory.PREFERENCES, "Favorite Programming Language", MemoryType.PREFERENCE),
    (re.compile(r"\bmy favou?rite (food|movie|color|colour|drink|book|game|sport|music) is ([^.!?\n]{0,58}?)(?=[.!?\n]|$)", re.IGNORECASE),
     MemoryCategory.PREFERENCES, "__DYNAMIC_FAVORITE__", MemoryType.PREFERENCE),
    (re.compile(r"\bmy (?:phone|mobile|cell) number is ([\d \-\+\(\)]{6,20})", re.IGNORECASE),
     MemoryCategory.CONTACT, "Phone Number", MemoryType.CONTACT),
    (re.compile(r"\bmy email(?: address)? is ([\w\.\-]+@[\w\-]+\.[\w\.\-]+)", re.IGNORECASE),
     MemoryCategory.CONTACT, "Email", MemoryType.CONTACT),
    (re.compile(r"\bmy birthday is ([^.!?\n]{3,38}?)(?=[.!?\n]|$)", re.IGNORECASE),
     MemoryCategory.IMPORTANT_DATES, "Birthday", MemoryType.EVENT),
]


def _regex_based_extraction(text: str) -> List[MemoryCandidate]:
    """Deterministic fallback extractor covering common statement patterns."""
    candidates: List[MemoryCandidate] = []
    for pattern, category, subcategory, memory_type in _REGEX_EXTRACTORS:
        match = pattern.search(text)
        if not match:
            continue
        if subcategory == "__DYNAMIC_FAVORITE__":
            favorite_kind = match.group(1).strip().title()
            value = match.group(2).strip()
            dyn_subcategory = f"Favorite {favorite_kind}"
            category, dyn_subcategory = _normalize_subcategory(dyn_subcategory)
            subcategory = dyn_subcategory
        else:
            value = match.group(1).strip().rstrip(".")
        if value:
            candidates.append(
                MemoryCandidate(
                    category=category, subcategory=subcategory,
                    memory_type=memory_type, value=value, confidence=0.6,
                )
            )
    return candidates


# ---------------------------------------------------------------------------
# AI-based extractor (primary path)
# ---------------------------------------------------------------------------
_EXTRACTION_SYSTEM_PROMPT = """You extract structured personal facts from a single WhatsApp message.

Return ONLY a JSON array (no prose, no markdown fences). Each element must be an object:
{"category": "<one of the categories below>", "subcategory": "<short label>", "value": "<the fact, concise>", "confidence": <0.0-1.0>}

If the message contains no useful, memorable personal fact about the sender, return an empty array: []

Valid categories: personal, contact, education, work, projects, skills, preferences, goals, travel,
family, friends_and_relationships, pets, health, finance, social_accounts, devices, important_dates,
conversation_preferences, ai_settings, interests, events_and_experiences, memories, other

Extract EVERY distinct fact in the message independently - a message can yield multiple facts.
Never extract passwords, OTPs, PINs, recovery codes, API keys, tokens, secret/private keys, session
data, or card numbers/CVV under any circumstances - omit them entirely rather than including them.
Only extract what the sender stated about themselves or their own life, not questions or requests."""


def _llm_based_extraction(message_text: str) -> List[MemoryCandidate]:
    """
    Use the configured AI model (OpenRouter, via ai.py - including its
    automatic primary/fallback model behavior) to extract structured
    memory candidates from a message. Returns an empty list on any
    failure (network error, malformed JSON, disabled setting) so callers
    can transparently fall back to the regex extractor.
    """
    if not settings.smart_memory_ai_extraction_enabled or not settings.openrouter_api_key:
        return []

    try:
        response = openrouter_client.generate_reply(
            [
                {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": message_text},
            ]
        )
    except AIGenerationError as exc:
        logger.warning("Smart Memory AI extraction failed, falling back to regex extractor: %s", exc)
        return []

    raw_text = response.text.strip()
    # Defensively strip markdown code fences if the model added them anyway.
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.IGNORECASE)

    try:
        parsed = json.loads(raw_text)
        if not isinstance(parsed, list):
            raise ValueError("Expected a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Smart Memory AI extraction returned unparsable JSON (%s); falling back to regex.", exc)
        return []

    candidates: List[MemoryCandidate] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        raw_category = str(item.get("category", "other")).strip().lower()
        raw_subcategory = str(item.get("subcategory", "")).strip()
        value = str(item.get("value", "")).strip()
        try:
            confidence = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        if not value:
            continue

        try:
            category = MemoryCategory(raw_category)
        except ValueError:
            category = MemoryCategory.OTHER

        normalized_category, normalized_subcategory = _normalize_subcategory(raw_subcategory)
        # Prefer the model's own category if it parsed validly and agrees
        # with a known subcategory mapping; otherwise trust the normalizer.
        final_category = category if raw_subcategory.lower() not in _SUBCATEGORY_TO_CATEGORY else normalized_category

        candidates.append(
            MemoryCandidate(
                category=final_category,
                subcategory=normalized_subcategory if normalized_subcategory != "Other" else (raw_subcategory or "Other"),
                memory_type=MemoryType.FACT,
                value=value,
                confidence=max(0.0, min(1.0, confidence)),
            )
        )

    return candidates


# =============================================================================
# Core CRUD API (required public functions)
# =============================================================================
def _find_active_memory(
    db: Session, contact: Contact, category: MemoryCategory, subcategory: str
) -> Optional[UserMemory]:
    """Look up the current active memory for a (contact, category, subcategory) triple."""
    stmt = (
        select(UserMemory)
        .where(
            UserMemory.contact_id == contact.id,
            UserMemory.category == category,
            UserMemory.subcategory == subcategory,
            UserMemory.is_active.is_(True),
        )
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _default_expiry_for_retention(retention_policy: RetentionPolicy) -> Optional[datetime]:
    """Compute a default expires_at for retention policies that decay over time."""
    now = _utcnow()
    if retention_policy == RetentionPolicy.SHORT_TERM:
        return now + timedelta(days=settings.smart_memory_short_term_days)
    if retention_policy == RetentionPolicy.TEMPORARY:
        return now + timedelta(days=settings.smart_memory_temporary_days)
    # PERMANENT and LONG_TERM: no automatic expiry (LONG_TERM only expires
    # if the caller explicitly supplies an expires_at value).
    return None


def save_memory(
    db: Session,
    contact: Contact,
    category: MemoryCategory,
    subcategory: str,
    memory_type: MemoryType,
    value: str,
    *,
    confidence: float = 1.0,
    importance: ImportanceLevel = ImportanceLevel.MEDIUM,
    retention_policy: RetentionPolicy = RetentionPolicy.LONG_TERM,
    source: MemorySource = MemorySource.USER_STATEMENT,
    tags: Optional[List[str]] = None,
    summary: Optional[str] = None,
    source_message_id: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    allow_soft_blocked: bool = False,
) -> Optional[UserMemory]:
    """
    Save (or update, if one already exists for this contact/category/
    subcategory) a Smart Memory fact.

    Never creates duplicates: if an active memory already exists for the
    same (contact, category, subcategory), this delegates to
    `update_memory()` instead of inserting a new row - the previous value
    is preserved in `MemoryHistory`.

    Sensitive-content safety: every value is checked with
    `security.check_memory_content_sensitivity()` before saving.
    Hard-blocked content (passwords, OTPs, API keys, etc.) is NEVER saved,
    regardless of `allow_soft_blocked`. Soft-blocked content (bank details,
    government IDs, medical information) is only saved when
    `allow_soft_blocked=True` (the caller should only set this when the
    user explicitly said "remember this").

    Returns the saved/updated `UserMemory`, or None if the value was
    blocked for sensitivity reasons.
    """
    check = check_memory_content_sensitivity(value)
    if check.hard_blocked:
        logger.warning(
            "Refused to save Smart Memory value for contact %s: hard-blocked category '%s'.",
            contact.wa_id, check.matched_category,
        )
        return None
    if check.soft_blocked and not allow_soft_blocked:
        logger.info(
            "Skipped saving Smart Memory value for contact %s: soft-blocked category '%s' "
            "without an explicit 'remember this' instruction.",
            contact.wa_id, check.matched_category,
        )
        return None

    normalized_category, normalized_subcategory = _normalize_subcategory(subcategory)
    # If the caller's category disagrees with the normalizer but the
    # subcategory itself wasn't recognized at all, trust the caller's
    # category and keep their original subcategory string verbatim.
    if normalized_subcategory == "Other" and subcategory:
        normalized_category, normalized_subcategory = category, subcategory

    existing = _find_active_memory(db, contact, normalized_category, normalized_subcategory)
    if existing is not None:
        if existing.value.strip().lower() == value.strip().lower():
            # Identical value re-stated: just record usage, not a duplicate/update.
            existing.last_used_at = _utcnow()
            db.commit()
            db.refresh(existing)
            return existing
        return update_memory(db, existing, value, changed_by=source)

    if expires_at is None:
        expires_at = _default_expiry_for_retention(retention_policy)

    memory = UserMemory(
        contact_id=contact.id,
        category=normalized_category,
        subcategory=normalized_subcategory,
        memory_type=memory_type,
        key=normalized_subcategory.lower().replace(" ", "_"),
        value=value.strip(),
        summary=summary,
        confidence=max(0.0, min(1.0, confidence)),
        importance=importance,
        retention_policy=retention_policy,
        source=source,
        tags=",".join(tags) if tags else None,
        source_message_id=source_message_id,
        expires_at=expires_at,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    logger.info(
        "Saved new Smart Memory for contact %s: %s/%s = %r (source=%s)",
        contact.wa_id, normalized_category.value, normalized_subcategory, value, source.value,
    )
    return memory


def update_memory(
    db: Session, memory: UserMemory, new_value: str, *, changed_by: MemorySource = MemorySource.MANUAL_UPDATE
) -> UserMemory:
    """
    Update an existing Smart Memory's value, recording the previous value
    in `MemoryHistory` first so it is never silently lost. This is also
    how "memory conflict resolution" is implemented per the specification:
    the newest verified value always replaces the previous one, and the
    previous one is preserved for audit/rollback purposes.
    """
    history_entry = MemoryHistory(
        memory_id=memory.id,
        old_value=memory.value,
        new_value=new_value,
        changed_by=changed_by,
    )
    db.add(history_entry)

    memory.value = new_value.strip()
    memory.updated_at = _utcnow()
    db.commit()
    db.refresh(memory)

    logger.info(
        "Updated Smart Memory #%d (%s/%s) for contact_id=%d -> %r",
        memory.id, memory.category.value, memory.subcategory, memory.contact_id, new_value,
    )
    return memory


def delete_memory(
    db: Session,
    contact: Contact,
    *,
    category: Optional[MemoryCategory] = None,
    subcategory: Optional[str] = None,
    memory_id: Optional[int] = None,
    query_text: Optional[str] = None,
) -> int:
    """
    Delete (soft-delete: `is_active=False`) one or more Smart Memories for
    a contact. Exactly one of `memory_id`, `(category, subcategory)`, or
    `query_text` should be provided to select which memories to remove.

    A soft delete is used (rather than a hard row delete) so the value is
    still recoverable via `MemoryHistory`/direct DB inspection if a
    "forget" command was a mistake, while `get_memory` / `get_all_memory` /
    `search_memory` / `build_memory_prompt` all exclude inactive memories,
    so a forgotten fact is immediately and completely invisible to both
    the AI prompt and normal lookups - satisfying "forget ... immediately"
    from the user's perspective.

    Returns the number of memories soft-deleted.
    """
    targets: List[UserMemory] = []

    if memory_id is not None:
        memory = db.get(UserMemory, memory_id)
        if memory is not None and memory.contact_id == contact.id and memory.is_active:
            targets = [memory]
    elif category is not None and subcategory is not None:
        existing = _find_active_memory(db, contact, category, subcategory)
        if existing is not None:
            targets = [existing]
    elif query_text:
        targets = search_memory(db, contact, query_text, include_low_relevance=True)
    else:
        logger.warning("delete_memory called without a valid selector for contact %s.", contact.wa_id)
        return 0

    for memory in targets:
        db.add(
            MemoryHistory(
                memory_id=memory.id,
                old_value=memory.value,
                new_value="[deleted]",
                changed_by=MemorySource.USER_EXPLICIT,
            )
        )
        memory.is_active = False

    if targets:
        db.commit()
        logger.info(
            "Deleted %d Smart Memory record(s) for contact %s.", len(targets), contact.wa_id
        )

    return len(targets)


def get_memory(
    db: Session, contact: Contact, category: MemoryCategory, subcategory: str
) -> Optional[UserMemory]:
    """
    Fetch a single active, non-expired Smart Memory by exact
    (category, subcategory), bumping its usage stats (access_count,
    last_used_at) and re-ranking its importance accordingly.
    """
    memory = _find_active_memory(db, contact, category, subcategory)
    if memory is None or memory.is_expired():
        return None

    memory.access_count += 1
    memory.last_used_at = _utcnow()
    _recompute_importance(memory)
    db.commit()
    db.refresh(memory)
    return memory


def get_all_memory(
    db: Session,
    contact: Contact,
    *,
    category: Optional[MemoryCategory] = None,
    include_inactive: bool = False,
    include_expired: bool = False,
) -> List[UserMemory]:
    """
    Fetch every Smart Memory for a contact (optionally filtered to a
    single category). By default only active, non-expired memories are
    returned (what the AI prompt and normal application logic should
    see); admin tooling can pass `include_inactive=True` /
    `include_expired=True` to audit the full history instead.
    """
    stmt = select(UserMemory).where(UserMemory.contact_id == contact.id)
    if category is not None:
        stmt = stmt.where(UserMemory.category == category)
    if not include_inactive:
        stmt = stmt.where(UserMemory.is_active.is_(True))
    stmt = stmt.order_by(UserMemory.updated_at.desc())

    results = list(db.execute(stmt).scalars().all())
    if not include_expired:
        results = [m for m in results if not m.is_expired()]
    return results


def search_memory(
    db: Session, contact: Contact, query_text: str, *, include_low_relevance: bool = False
) -> List[UserMemory]:
    """
    Search a contact's Smart Memory using semantic-ish matching: exact
    subcategory/key matches first, then alias-resolved subcategory
    matches, then plain substring matches against value/tags, ranked in
    that order. See `_resolve_query_aliases()` for the (swappable)
    matching strategy.

    `include_low_relevance=True` widens the match to include any memory
    whose value or tags loosely mention a query keyword, which is useful
    for `delete_memory(query_text=...)` (e.g. "forget everything about my
    previous job" should catch Work-category memories even without an
    exact subcategory alias hit).
    """
    all_active = get_all_memory(db, contact)
    if not all_active:
        return []

    alias_candidates = {c.lower() for c in _resolve_query_aliases(query_text)}
    keywords = [w.lower() for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", query_text)]

    scored: List[Tuple[int, UserMemory]] = []
    for memory in all_active:
        score = 0
        subcat_lower = memory.subcategory.lower()
        if subcat_lower in alias_candidates:
            score += 5
        if any(kw in subcat_lower for kw in keywords):
            score += 3
        if any(kw in memory.value.lower() for kw in keywords):
            score += 2
        if memory.tags and any(kw in memory.tags.lower() for kw in keywords):
            score += 1

        if score > 0 or include_low_relevance:
            scored.append((score, memory))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not include_low_relevance:
        scored = [pair for pair in scored if pair[0] > 0]
    return [memory for _, memory in scored]


# =============================================================================
# Automatic importance ranking
# =============================================================================
def _recompute_importance(memory: UserMemory) -> None:
    """
    Automatically re-rank a memory's importance based on how often it has
    been used (accessed), per the specification's thresholds. Mutates the
    memory in place; caller is responsible for committing.
    """
    if memory.access_count > 20:
        memory.importance = ImportanceLevel.CRITICAL
    elif memory.access_count > 10:
        memory.importance = ImportanceLevel.HIGH
    elif memory.access_count > 5:
        memory.importance = ImportanceLevel.MEDIUM
    else:
        memory.importance = ImportanceLevel.LOW


# =============================================================================
# Extraction orchestration
# =============================================================================
def extract_memories_from_message(
    db: Session, contact: Contact, message_text: str, *, message_id: Optional[str] = None,
    force_explicit: bool = False,
) -> List[UserMemory]:
    """
    Analyze a single inbound user message and create/update/delete Smart
    Memory facts accordingly. This is the main entry point called from
    routes.py for every user message (after it has already been saved to
    Conversation History - this function never touches that table).

    Priority order:
      1. Explicit "forget ..." commands -> delete matching memories immediately.
      2. Explicit "remember ..." / "update my X to Y" commands -> always
         saved (bypassing the automatic confidence threshold), except
         hard-blocked sensitive content which is NEVER saved regardless.
      3. Automatic extraction (AI-based primary path, regex-based
         fallback) -> saved only if confidence meets
         `SMART_MEMORY_MIN_CONFIDENCE` and the content isn't sensitive.

    `force_explicit`: when True, the automatic-extraction path (step 3) is
    treated exactly like an explicit "remember" command (soft-blocked
    content is allowed, importance is HIGH, retention is PERMANENT). This
    is used for exactly one caller: routes.py's post-approval follow-up,
    where the owner has just explicitly approved a sensitive request
    (bank details, etc.) via the Approvals workflow - that owner approval
    *is* the explicit confirmation this flag represents, so the message's
    facts (previously withheld by the soft-block rule while pending) are
    now saved to Smart Memory the same way an explicit "remember this"
    would be. No new storage/authorization system was introduced.

    Returns the list of `UserMemory` rows that were created or updated
    (empty for a pure "forget" command, since nothing new is saved).
    """
    if not message_text or not message_text.strip():
        return []

    text = message_text.strip()

    # --- 1. Explicit forget command -----------------------------------
    if is_explicit_forget_command(text):
        # Strip the leading command word(s) to isolate the subject, e.g.
        # "forget my address" -> "my address" -> alias-resolved to "Address".
        subject = re.sub(
            r"^\s*(please\s+)?(forget|don'?t remember)\b\s*(that|everything about)?\s*",
            "", text, flags=re.IGNORECASE,
        ).strip()
        deleted_count = delete_memory(db, contact, query_text=subject or text)
        logger.info(
            "Processed explicit forget command for contact %s ('%s'): %d memory(ies) removed.",
            contact.wa_id, text, deleted_count,
        )
        return []

    explicit_remember = is_explicit_remember_command(text) or force_explicit

    # --- 2. Explicit "update my X to Y" command ------------------------
    update_match = _UPDATE_PATTERN.search(text)
    saved: List[UserMemory] = []
    if update_match:
        raw_subject, raw_value = update_match.group(1).strip(), update_match.group(2).strip().rstrip(".")
        category, subcategory = _normalize_subcategory(raw_subject)
        memory = save_memory(
            db, contact, category, subcategory, MemoryType.FACT, raw_value,
            confidence=1.0, importance=ImportanceLevel.HIGH,
            retention_policy=RetentionPolicy.PERMANENT, source=MemorySource.USER_EXPLICIT,
            source_message_id=message_id, allow_soft_blocked=True,
        )
        if memory is not None:
            saved.append(memory)
        return saved

    # --- 3. Automatic / explicit-remember extraction -------------------
    candidates = _llm_based_extraction(text)
    if not candidates:
        candidates = _regex_based_extraction(text)
    elif explicit_remember:
        # Always also run the regex extractor when the user used an
        # explicit "remember" phrasing, so a simple explicit statement is
        # never missed just because the AI path returned an incomplete set.
        for extra in _regex_based_extraction(text):
            if not any(c.subcategory == extra.subcategory for c in candidates):
                candidates.append(extra)

    for candidate in candidates:
        if not explicit_remember and candidate.confidence < settings.smart_memory_min_confidence:
            logger.info(
                "Skipped low-confidence Smart Memory candidate for contact %s: %s/%s (confidence=%.2f)",
                contact.wa_id, candidate.category.value, candidate.subcategory, candidate.confidence,
            )
            continue

        memory = save_memory(
            db, contact, candidate.category, candidate.subcategory, candidate.memory_type,
            candidate.value,
            confidence=candidate.confidence,
            importance=ImportanceLevel.HIGH if explicit_remember else ImportanceLevel.MEDIUM,
            retention_policy=RetentionPolicy.PERMANENT if explicit_remember else RetentionPolicy.LONG_TERM,
            source=MemorySource.USER_EXPLICIT if explicit_remember else MemorySource.USER_STATEMENT,
            tags=candidate.tags or None,
            summary=candidate.summary,
            source_message_id=message_id,
            allow_soft_blocked=explicit_remember,
        )
        if memory is not None:
            saved.append(memory)

    return saved


# =============================================================================
# Prompt construction
# =============================================================================
def build_memory_prompt(db: Session, contact: Contact, current_message: Optional[str] = None) -> str:
    """
    Build the compact Smart Memory text block to inject into the AI
    prompt (see prompt_builder.py), respecting the configured item-count
    and character budget (`SMART_MEMORY_MAX_ITEMS` /
    `SMART_MEMORY_MAX_CHARACTERS`).

    Selection strategy: memories relevant to `current_message` (if
    provided) are prioritized first, then the remaining budget is filled
    by importance (Critical > High > Medium > Low) and recency of use.
    Included memories have their usage stats bumped (access_count,
    last_used_at) and importance re-ranked, since being selected for the
    live prompt counts as "being used".

    Returns an empty string if there are no active memories for this
    contact (callers should simply omit the Smart Memory section of the
    prompt in that case).
    """
    all_active = get_all_memory(db, contact)
    if not all_active:
        return ""

    relevant_ids: set = set()
    if current_message:
        relevant_ids = {m.id for m in search_memory(db, contact, current_message)}

    _IMPORTANCE_RANK = {
        ImportanceLevel.CRITICAL: 3, ImportanceLevel.HIGH: 2,
        ImportanceLevel.MEDIUM: 1, ImportanceLevel.LOW: 0,
    }

    def _sort_key(memory: UserMemory):
        is_relevant = 1 if memory.id in relevant_ids else 0
        recency = memory.last_used_at or memory.updated_at
        return (is_relevant, _IMPORTANCE_RANK.get(memory.importance, 0), recency)

    ordered = sorted(all_active, key=_sort_key, reverse=True)

    selected: List[UserMemory] = []
    total_chars = 0
    for memory in ordered:
        if len(selected) >= settings.smart_memory_max_items:
            break
        line_length = len(memory.subcategory) + len(memory.value) + 4
        if total_chars + line_length > settings.smart_memory_max_characters and selected:
            break
        selected.append(memory)
        total_chars += line_length

    if not selected:
        return ""

    for memory in selected:
        memory.access_count += 1
        memory.last_used_at = _utcnow()
        _recompute_importance(memory)
    db.commit()

    lines = [f"- {memory.subcategory}: {memory.value}" for memory in selected]
    return "\n".join(lines)


# =============================================================================
# Expiration
# =============================================================================
def expire_stale_memories(db: Session) -> int:
    """
    Soft-delete (`is_active=False`) any Short-Term or Temporary memory
    whose `expires_at` has passed, and any Long-Term memory that was
    given an explicit `expires_at` which has also passed. Permanent
    memories are never touched. Intended to be called periodically by
    scheduler.py. Returns the number of memories expired.
    """
    now = _utcnow()
    stmt = select(UserMemory).where(
        UserMemory.is_active.is_(True),
        UserMemory.expires_at.is_not(None),
        UserMemory.expires_at < now,
        UserMemory.retention_policy != RetentionPolicy.PERMANENT,
    )
    stale = list(db.execute(stmt).scalars().all())
    for memory in stale:
        memory.is_active = False
    if stale:
        db.commit()
        logger.info("Expired %d stale Smart Memory record(s).", len(stale))
    return len(stale)


# =============================================================================
# Statistics (for future admin dashboards)
# =============================================================================
_IMPORTANCE_NUMERIC = {
    ImportanceLevel.CRITICAL: 4, ImportanceLevel.HIGH: 3,
    ImportanceLevel.MEDIUM: 2, ImportanceLevel.LOW: 1,
}


def get_memory_statistics(db: Session, contact: Optional[Contact] = None) -> Dict[str, object]:
    """
    Compute Smart Memory analytics, either scoped to a single contact or
    globally (across all contacts) when `contact` is None. Returns a plain
    dict intended to be consumed by admin tooling / future dashboards.
    """
    stmt = select(UserMemory).where(UserMemory.is_active.is_(True))
    if contact is not None:
        stmt = stmt.where(UserMemory.contact_id == contact.id)
    memories = list(db.execute(stmt).scalars().all())
    memories = [m for m in memories if not m.is_expired()]

    if not memories:
        return {
            "total_memories": 0, "memories_per_category": {}, "most_used": [],
            "recently_updated": [], "oldest": [], "average_confidence": 0.0,
            "average_importance": 0.0,
        }

    per_category: Dict[str, int] = {}
    for memory in memories:
        per_category[memory.category.value] = per_category.get(memory.category.value, 0) + 1

    most_used = sorted(memories, key=lambda m: m.access_count, reverse=True)[:10]
    recently_updated = sorted(memories, key=lambda m: m.updated_at, reverse=True)[:10]
    oldest = sorted(memories, key=lambda m: m.created_at)[:10]

    avg_confidence = sum(m.confidence for m in memories) / len(memories)
    avg_importance = sum(_IMPORTANCE_NUMERIC.get(m.importance, 0) for m in memories) / len(memories)

    def _brief(m: UserMemory) -> Dict[str, object]:
        return {
            "id": m.id, "category": m.category.value, "subcategory": m.subcategory,
            "value": m.value, "access_count": m.access_count,
            "importance": m.importance.value, "updated_at": m.updated_at.isoformat(),
        }

    return {
        "total_memories": len(memories),
        "memories_per_category": per_category,
        "most_used": [_brief(m) for m in most_used],
        "recently_updated": [_brief(m) for m in recently_updated],
        "oldest": [_brief(m) for m in oldest],
        "average_confidence": round(avg_confidence, 3),
        "average_importance": round(avg_importance, 2),
    }
