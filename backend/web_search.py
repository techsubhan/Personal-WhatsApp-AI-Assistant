"""
web_search.py
==============
Web search control: real, dashboard-editable settings and decision logic
for WHEN a search should be attempted - but no actual search provider is
wired up, because none exists anywhere in this project (confirmed by
inspection: no search API client, no search API key, nothing). Building
a fake search that just answers from model knowledge while claiming a
search happened would violate the explicit "never claim a search
happened unless a real search occurred" requirement, so this module
stops at the honest boundary: it decides *whether* a search should
happen, and if one should but can't (`is_configured()` is False), the
caller tells the person plainly instead of pretending.

BACKEND API REQUIRED to make search actually execute:
    Any HTTP search API (e.g. Serper.dev, Bing Web Search, Google
    Programmable Search) would work. Once you have a provider + API key:
      1. Set WEB_SEARCH_PROVIDER and WEB_SEARCH_API_KEY in .env.
      2. Implement `_call_provider(query)` below to POST/GET to that
         provider and return a short text summary of the top results.
      3. `search()`'s existing NotImplementedError branch is the only
         thing that needs replacing - decide()/is_configured() and every
         caller already work unchanged once a provider is added.

Runtime settings (dashboard-editable, see admin_api.py "voice_reply"
sibling "web_search" block): `enabled` (global on/off) and `mode`:
    - "never": never search, regardless of message content.
    - "always": search on every message (once a provider exists).
    - "only_when_requested": search only if the message explicitly asks
      for one ("search", "google", "look up", Roman Urdu "search karo").
    - "automatic" (default): search only for messages that look
      current-events/fact-lookup shaped AND explicitly ask for it or use
      time-sensitive language ("latest", "current", "today", "abhi",
      "aaj kal") - deliberately conservative, since over-searching a
      personal WhatsApp assistant's replies is wasteful and slow.
"""

from __future__ import annotations

import re

from config import load_runtime_settings, settings

_EXPLICIT_REQUEST_RE = re.compile(
    r"\b(search|google (it|this|that)|look ?up|search karo|google par|search kar)\b", re.IGNORECASE
)
_TIME_SENSITIVE_RE = re.compile(
    r"\b(latest|current|today|right now|as of now|abhi|aaj ?kal|is waqt)\b", re.IGNORECASE
)


class WebSearchError(Exception):
    """Raised by search() when called without a configured provider."""


def is_configured() -> bool:
    return bool(settings.web_search_api_key and settings.web_search_provider)


def get_settings() -> dict:
    return load_runtime_settings().get("web_search", {"enabled": False, "mode": "automatic"})


def should_search(text: str, *, per_contact_enabled: bool = True) -> bool:
    """
    Decide whether this message should trigger a search, per the current
    global settings and mode. Does NOT check `is_configured()` - callers
    check that separately so they can give an honest "search is disabled"
    reply instead of silently doing nothing when a search *should* have
    happened but no provider exists.
    """
    web_settings = get_settings()
    if not web_settings.get("enabled") or not per_contact_enabled:
        return False

    mode = web_settings.get("mode", "automatic")
    if mode == "never":
        return False
    if mode == "always":
        return True
    if mode == "only_when_requested":
        return bool(_EXPLICIT_REQUEST_RE.search(text or ""))
    # "automatic"
    return bool(_EXPLICIT_REQUEST_RE.search(text or "") or _TIME_SENSITIVE_RE.search(text or ""))


def search(query: str) -> str:
    """
    Execute a real web search and return a short text summary. Always
    raises WebSearchError today - see the module docstring's "BACKEND API
    REQUIRED" section for the one function that needs implementing to
    make this real once a provider is chosen. Deliberately does NOT
    return a fabricated result.
    """
    if not is_configured():
        raise WebSearchError("No web search provider is configured (WEB_SEARCH_PROVIDER/WEB_SEARCH_API_KEY).")
    raise WebSearchError(
        f"Web search provider '{settings.web_search_provider}' is set but not implemented - "
        "see web_search.py's module docstring."
    )
