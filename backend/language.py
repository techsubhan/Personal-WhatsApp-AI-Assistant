"""
language.py
============
Centralized language/script detection for logging, the dashboard, and
Contact.detected_language - NOT for controlling the AI's actual reply
language.

Honest limitation up front: this project has no ML-based language
identification library available (no network access to install one in
this environment, and none was previously integrated), so this is a
heuristic, word-list/Unicode-range based detector, not a trained model.
It is good enough to LABEL a message for display purposes, but is not
guaranteed accurate on short or ambiguous text - it deliberately never
crosses this uncertainty into changing AI behavior.

The AI's actual reply-language behavior is unchanged by this module: it
already mirrors the contact's language via the system prompt instruction
("mirror their language", see prompt_builder.py /
data/prompts/system_prompt.txt), which handles this far more robustly
than any keyword list could (it reads the whole message in context, not
just isolated marker words). This module exists to answer "what language
did the dashboard/logs say this was", not "what language should the AI
reply in".

Supported labels: english, urdu, roman_urdu, punjabi, roman_punjabi,
arabic, german, unknown.

CRITICAL (per product requirement): this module only ever LABELS text.
It never transliterates or translates between scripts - Roman Urdu is
never "converted" to Hindi/Devanagari or vice versa; detection is
read-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Arabic-script Unicode block (covers Arabic, Urdu, and Punjabi/Shahmukhi,
# which all use the Perso-Arabic script family).
_ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")

# Letters that exist in Urdu's extended alphabet but NOT in standard
# Arabic (retroflex consonants + Urdu-specific vowel letters). Their
# presence is a strong signal for Urdu over plain Arabic within
# Arabic-script text.
_URDU_ONLY_LETTERS = set("ٹڈڑںے")

# A handful of Punjabi(Shahmukhi)-leaning function words that are
# uncommon or absent in standard Urdu writing. Genuinely hard to
# distinguish from Urdu reliably with a word list alone (the two share
# nearly the entire Perso-Arabic alphabet) - treated as a weak signal.
_PUNJABI_SHAHMUKHI_MARKERS = {"نوں", "دا", "دی", "توں", "ایں", "سانوں"}

# Common Urdu function words/verb forms that are distinctive of
# Urdu/Hindustani grammar and not standard Modern Standard Arabic
# vocabulary - used to break the "Arabic-script but no retroflex letter
# present" tie for short messages (see _URDU_ONLY_LETTERS above, which
# alone misses short Urdu sentences that happen not to use a retroflex
# letter).
_URDU_FUNCTION_WORDS = {"کو", "ہے", "ہیں", "کرو", "کریں", "سے", "میں", "تم", "آپ", "نہیں", "کیا"}

# German is the one Latin-script language here with unambiguous letters.
_GERMAN_CHAR_RE = re.compile(r"[äöüßÄÖÜ]")
_GERMAN_WORDS = {
    "ich", "und", "nicht", "der", "die", "das", "ist", "wie", "warum", "bitte",
    "danke", "schick", "eine", "nachricht", "kannst", "du", "mir",
}

# Roman Urdu / Roman Punjabi are both Latin-script transliterations of
# Perso-Arabic-family languages and share a large amount of vocabulary
# (both descend from Punjabi/Hindustani contact varieties), which makes a
# pure word-list split inherently approximate. These lists favor words
# each variety uses noticeably more often than the other.
_ROMAN_URDU_WORDS = {
    "hai", "hain", "kya", "nahi", "acha", "accha", "kar", "kar do", "kar lo",
    "raha", "rahi", "rahe", "mein", "main", "tum", "aap", "kyun", "abhi",
    "bhai", "bhejo", "bhej", "karo", "karein", "chahiye", "mujhe", "tumhe",
    "tumhein", "unko", "iska", "uska", "sath", "phir", "kaise", "kahan",
}
_ROMAN_PUNJABI_WORDS = {
    "nu", "menu", "sanu", "tuhanu", "tuhaddi", "tainu", "ki", "haan", "nai", "nahin",
    "hoya", "kithe", "vi", "ni", "assi", "tussi", "changa", "changi", "karda",
    "kardi", "kardan", "chal", "vekh", "puchna",
}


@dataclass
class LanguageDetection:
    language: str  # english | urdu | roman_urdu | punjabi | roman_punjabi | arabic | german | unknown
    script: str  # latin | arabic | unknown


def detect(text: str) -> LanguageDetection:
    """Heuristically label the language/script of `text`. Never raises."""
    if not text or not text.strip():
        return LanguageDetection(language="unknown", script="unknown")

    if _ARABIC_SCRIPT_RE.search(text):
        urdu_letter_signal = sum(1 for ch in text if ch in _URDU_ONLY_LETTERS)
        urdu_word_signal = sum(1 for w in _URDU_FUNCTION_WORDS if w in text)
        punjabi_signal = sum(1 for marker in _PUNJABI_SHAHMUKHI_MARKERS if marker in text)
        if punjabi_signal >= 1:
            return LanguageDetection(language="punjabi", script="arabic")
        if urdu_letter_signal > 0 or urdu_word_signal > 0:
            return LanguageDetection(language="urdu", script="arabic")
        return LanguageDetection(language="arabic", script="arabic")

    if _GERMAN_CHAR_RE.search(text):
        return LanguageDetection(language="german", script="latin")

    words = set(re.findall(r"[a-zA-Z']+", text.lower()))
    german_hits = len(words & _GERMAN_WORDS)
    urdu_hits = len(words & _ROMAN_URDU_WORDS)
    punjabi_hits = len(words & _ROMAN_PUNJABI_WORDS)

    if german_hits >= 2:
        return LanguageDetection(language="german", script="latin")
    if punjabi_hits >= 1 and punjabi_hits >= urdu_hits:
        return LanguageDetection(language="roman_punjabi", script="latin")
    if urdu_hits >= 1:
        return LanguageDetection(language="roman_urdu", script="latin")
    if words:
        return LanguageDetection(language="english", script="latin")
    return LanguageDetection(language="unknown", script="unknown")
