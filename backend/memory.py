"""
memory.py
=========
Conversation memory: stores chat history per contact and retrieves an
intelligently size-limited window of it for prompt construction.

Responsibilities
-----------------
- Persist every inbound and outbound message (`Message` rows) linked to a
  `Contact`.
- Retrieve recent conversation history for a contact, bounded by both a
  maximum message count (`MEMORY_MAX_MESSAGES`) and a maximum total
  character budget (`MEMORY_MAX_CHARACTERS`), so the AI prompt never grows
  unbounded regardless of how long a conversation has been going on.
- Provide a "clear memory" operation for the `/memory clear` admin command.
- Provide retrieval-based search across a contact's *complete* conversation
  history (unbounded by MEMORY_MAX_MESSAGES / MEMORY_MAX_CHARACTERS), used
  when Smart Memory (see smart_memory.py) does not have an answer to a
  question and the full history needs to be consulted instead. This is a
  new, additive capability (`search_conversation_history`) - it does not
  change how `get_recent_messages` / `get_conversation_history_for_prompt`
  behave for normal prompt construction.

Design note: history is always returned oldest-first (chronological order)
so it can be dropped directly into the OpenRouter `messages` array after
the system prompt.

Note on the four core functions below (add_message, get_recent_messages,
get_conversation_history_for_prompt, clear_memory): these are considered
stable/protected. Conversation History and Smart Memory (smart_memory.py)
are independent systems by design - Smart Memory never touches the
`Message` table, and nothing in this file was changed to accommodate it.
`search_conversation_history`, appended at the end of this file, is the
only new addition here.
"""

from __future__ import annotations

from typing import Dict, List

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from config import settings
from logger import get_logger
from models import Contact, Message, MessageRole

logger = get_logger("memory")


def add_message(
    db: Session,
    contact: Contact,
    role: MessageRole,
    content: str,
    message_type: str = "text",
    whatsapp_message_id: str | None = None,
) -> Message:
    """Persist a single message to the conversation memory for a contact."""
    message = Message(
        contact_id=contact.id,
        role=role,
        content=content,
        message_type=message_type,
        whatsapp_message_id=whatsapp_message_id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_recent_messages(db: Session, contact: Contact) -> List[Message]:
    """
    Fetch the most recent messages for a contact, newest-first from the DB,
    then bound by both message count and total character budget, and
    finally return them oldest-first (chronological) for prompt use.
    """
    stmt = (
        select(Message)
        .where(Message.contact_id == contact.id)
        .order_by(Message.created_at.desc())
        .limit(settings.memory_max_messages)
    )
    newest_first = list(db.execute(stmt).scalars().all())

    # Apply the character budget by trimming from the oldest end first,
    # since the most recent messages are the most valuable context.
    bounded: List[Message] = []
    total_chars = 0
    for message in newest_first:
        message_len = len(message.content)
        if total_chars + message_len > settings.memory_max_characters and bounded:
            break
        bounded.append(message)
        total_chars += message_len

    bounded.reverse()  # oldest-first
    return bounded


def get_conversation_history_for_prompt(db: Session, contact: Contact) -> List[Dict[str, str]]:
    """
    Return conversation history formatted as OpenRouter/OpenAI-style
    chat messages: [{"role": "user"|"assistant", "content": "..."}].
    Owner (human-override) messages are represented as assistant messages
    since, from the end-user's perspective, they came "from the assistant
    side" of the conversation.
    """
    messages = get_recent_messages(db, contact)
    formatted: List[Dict[str, str]] = []
    for message in messages:
        if message.role == MessageRole.USER:
            role = "user"
        elif message.role in (MessageRole.ASSISTANT, MessageRole.OWNER):
            role = "assistant"
        else:
            continue  # skip system messages stored for auditing purposes
        formatted.append({"role": role, "content": message.content})
    return formatted


def clear_memory(db: Session, contact: Contact) -> int:
    """Delete all stored conversation memory for a contact. Returns rows deleted."""
    stmt = delete(Message).where(Message.contact_id == contact.id)
    result = db.execute(stmt)
    db.commit()
    logger.info("Cleared conversation memory for contact %s (%d messages)", contact.wa_id, result.rowcount)
    return result.rowcount


# =============================================================================
# Retrieval-based conversation search (new, additive)
# =============================================================================
# Conversation History stores every message permanently and without limit,
# but only a small recent window of it is ever sent to the LLM via
# get_conversation_history_for_prompt() (by design, to keep prompts small
# and fast). For questions like "what phone number did I give you?" or
# "what country did I visit last year?" that may reference something well
# outside that recent window, smart_memory.py falls back to this function
# to search the *entire* history for a contact and retrieve only the
# handful of most relevant messages, rather than ever sending the whole
# history to the model.
#
# Implementation note (indexed retrieval, no full table scan):
# `Message.contact_id` already has a b-tree index (see models.py), so the
# WHERE clause below is always an indexed lookup restricted to the
# requesting contact only - it never scans other contacts' messages.
# Relevance ranking within that contact's messages currently uses a
# lightweight keyword-overlap heuristic (SQL LIKE per keyword + in-Python
# scoring), which is a deliberate, simple starting point. The function
# signature and return type are intentionally kept minimal (contact +
# free-text query -> ranked List[Message]) so this implementation can
# later be swapped for SQLite FTS5 full-text search, or for a vector
# similarity search over message embeddings, without changing the public
# API that smart_memory.py (or any future caller) depends on.
# =============================================================================
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "am", "i", "you", "he", "she",
    "it", "we", "they", "my", "your", "his", "her", "its", "our", "their",
    "what", "when", "where", "which", "who", "whom", "how", "did", "do", "does",
    "to", "of", "in", "on", "at", "for", "with", "about", "me", "and", "or",
    "that", "this", "these", "those", "be", "been", "have", "has", "had",
}


def _extract_keywords(query_text: str) -> List[str]:
    """Extract meaningful search keywords from a free-text query, dropping stopwords."""
    words = [w.strip(".,!?\"'()").lower() for w in query_text.split()]
    return [w for w in words if w and w not in _STOPWORDS and len(w) > 2]


def search_conversation_history(
    db: Session, contact: Contact, query_text: str, limit: int = 5
) -> List[Message]:
    """
    Search a contact's *complete* conversation history for messages
    relevant to `query_text`, returning at most `limit` messages ordered
    by relevance (keyword overlap) and then recency, oldest-of-the-
    relevant-set first (so they read naturally if inserted into a prompt).

    This is intentionally separate from `get_recent_messages()` above,
    which only ever looks at the most recent window - this function looks
    across the entire stored history for the contact, but still narrows
    down to a small number of the most relevant messages rather than ever
    returning (or sending to the LLM) the full history at once.

    Returns an empty list if no keywords can be extracted from the query
    or no messages match any of them - callers should treat that as "not
    found in conversation history" and answer honestly rather than
    guessing, per the project's memory-retrieval policy.
    """
    keywords = _extract_keywords(query_text)
    if not keywords:
        return []

    # Indexed on contact_id; each keyword adds an OR'd LIKE clause scoped
    # to this contact only, so we still never scan another contact's rows.
    conditions = [Message.content.ilike(f"%{kw}%") for kw in keywords]
    stmt = (
        select(Message)
        .where(Message.contact_id == contact.id)
        .where(_or_conditions(conditions))
        .order_by(Message.created_at.desc())
        .limit(200)  # candidate pool cap, keeps scoring cheap even for very long histories
    )
    candidates = list(db.execute(stmt).scalars().all())
    if not candidates:
        return []

    def _score(message: Message) -> int:
        content_lower = message.content.lower()
        return sum(1 for kw in keywords if kw in content_lower)

    scored = sorted(candidates, key=lambda m: (_score(m), m.created_at), reverse=True)
    top_matches = scored[:limit]
    # Return in chronological order for natural reading if injected into a prompt.
    top_matches.sort(key=lambda m: m.created_at)
    return top_matches


def _or_conditions(conditions: List):
    """Combine a list of SQLAlchemy filter conditions with OR (small local helper)."""
    from sqlalchemy import or_

    return or_(*conditions)
