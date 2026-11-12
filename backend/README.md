# WhatsApp AI Assistant

A production-ready WhatsApp AI Assistant built with **FastAPI**, the **WhatsApp Business Cloud API**, and **OpenRouter** (OpenAI OSS 120B, with automatic fallback). It supports per-contact AI control, working hours, human override, spam/rate-limit protection, sensitive-information approval workflows, conversation memory, and full admin control via WhatsApp chat commands.

---

## Table of Contents

1. [Folder Structure](#folder-structure)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [Environment Variables](#environment-variables)
5. [Running the App](#running-the-app)
6. [Meta WhatsApp Cloud API Setup](#meta-whatsapp-cloud-api-setup)
7. [Webhook Setup](#webhook-setup)
8. [OpenRouter Setup](#openrouter-setup)
9. [Two Memory Systems: Conversation History vs. Smart Memory](#two-memory-systems-conversation-history-vs-smart-memory)
10. [Admin Commands](#admin-commands)
11. [Health & Status Endpoints](#health--status-endpoints)
12. [Troubleshooting](#troubleshooting)
13. [Deployment](#deployment)
14. [Production Tips](#production-tips)

---

## Folder Structure

```
whatsapp-ai-assistant/
├── app.py                 # FastAPI entrypoint (lifespan, routers, uvicorn runner)
├── config.py               # .env loading + dynamic settings.json + profile.json
├── database.py              # SQLAlchemy engine/session setup
├── models.py                # ORM models (Contact, Message, ApprovalRequest, UserMemory, MemoryHistory, ...)
├── memory.py                 # Conversation History storage/retrieval (unchanged, protected functions)
├── smart_memory.py             # Smart Memory: structured long-term facts (independent of memory.py)
├── contacts.py                # Contact lifecycle (unknown/saved/blocked, per-contact AI)
├── admin.py                    # Admin command parsing & dispatch
├── approval.py                  # Sensitive-info owner-approval workflow
├── working_hours.py              # Working hours + emergency keyword logic
├── human_override.py              # Pause AI when the owner manually replies
├── typing_delay.py                 # Randomized "typing..." delay
├── prompt_builder.py                # Assembles the final AI prompt (system + profile + Smart Memory + history)
├── ai.py                              # OpenRouter client (retries + fallback model)
├── whatsapp.py                         # WhatsApp Cloud API client + message parsing
├── logger.py                            # Centralized logging + secret redaction
├── scheduler.py                          # Background jobs (approval expiry + Smart Memory expiry sweeps)
├── utils.py                               # Shared small helpers
├── routes.py                               # Core message-processing pipeline
├── webhook.py                                # FastAPI webhook transport layer
├── rate_limiter.py                            # In-memory sliding-window rate limiter
├── spam_detector.py                            # In-memory repeated-message detector
├── security.py                                  # Sensitive-info detection + secret-leak scan + memory-content gating
├── health.py                                     # /health and /status endpoints
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
└── data/
    ├── prompts/
    │   ├── system_prompt.txt
    │   └── approval_prompt.txt
    ├── settings.json      # created automatically on first run
    ├── profile.json        # created automatically on first run
    ├── contacts.db          # SQLite database, created automatically (conversation history + Smart Memory tables)
    └── logs/
        └── app.log           # rotating log file, created automatically
```

---

## Requirements

- **Python 3.14.4**
- **Ubuntu 26.04 LTS** (or any Linux/macOS/Windows host with Python 3.14)
- A **Meta developer account** with a WhatsApp Business Cloud API app
- An **OpenRouter** account and API key
- Outbound internet access to `graph.facebook.com` and `openrouter.ai`

See `requirements.txt` for the exact Python package list and compatibility notes.

---

## Installation

```bash
# 1. Clone / copy the project
cd whatsapp-ai-assistant

# 2. Create a virtual environment (recommended)
python3.14 -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the example environment file and fill in your real values
cp .env.example .env
nano .env   # or any editor

# 5. Run the app
python app.py
```

On first run, the app automatically creates:
- `data/contacts.db` (SQLite database, all tables)
- `data/settings.json` (dynamic runtime settings, sane defaults)
- `data/profile.json` (empty owner profile - fill in via `/profile set` or by editing the file directly)
- `data/logs/app.log`

---

## Environment Variables

All environment variables are documented with inline comments in **`.env.example`**. Copy it to `.env` and fill in the following groups:

| Group | Key variables |
|---|---|
| Application | `APP_ENV`, `APP_HOST`, `APP_PORT`, `APP_DEBUG`, `APP_SECRET_KEY` |
| WhatsApp Cloud API | `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_API_VERSION`, `OWNER_WHATSAPP_NUMBER` |
| OpenRouter | `OPENROUTER_API_KEY`, `OPENROUTER_PRIMARY_MODEL`, `OPENROUTER_FALLBACK_MODEL`, `OPENROUTER_TIMEOUT_SECONDS`, `OPENROUTER_MAX_RETRIES` |
| Database | `DATABASE_URL` |
| Admin/Security | `ADMIN_NUMBERS`, `INTERNAL_API_KEY` |
| Rate limiting | `RATE_LIMIT_MESSAGES_PER_MINUTE`, `RATE_LIMIT_COOLDOWN_SECONDS` |
| Spam detection | `SPAM_REPEAT_THRESHOLD`, `SPAM_WINDOW_SECONDS`, `SPAM_MUTE_MINUTES` |
| Human override | `HUMAN_OVERRIDE_TIMEOUT_MINUTES` |
| Typing delay | `TYPING_DELAY_MIN_SECONDS`, `TYPING_DELAY_MAX_SECONDS` |
| Memory | `MEMORY_MAX_MESSAGES`, `MEMORY_MAX_CHARACTERS` |
| Groups | `IGNORE_GROUP_MESSAGES` |
| Logging | `LOG_LEVEL`, `LOG_DIR`, `LOG_FILE`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` |

Anything an admin should be able to change **without a restart** (working hours, away message, welcome message, typing delay range, approval timeout) lives in `data/settings.json` instead, and is editable live via admin chat commands (see below) or by editing the file and running `/reload`.

---

## Running the App

**Development:**
```bash
python app.py
```
This reads `APP_HOST` / `APP_PORT` / `APP_DEBUG` from `.env` and starts uvicorn (with auto-reload if `APP_DEBUG=true`).

**Production (recommended):**
```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2
```
> Note: if you run multiple uvicorn workers, remember that the rate limiter and spam detector keep their state **in-memory per worker process** (see [Production Tips](#production-tips)).

---

## Meta WhatsApp Cloud API Setup

1. Create a Meta developer app at [developers.facebook.com](https://developers.facebook.com/) and add the **WhatsApp** product.
2. From the WhatsApp > API Setup page, note your **Phone Number ID** and generate a (permanent, if possible) **Access Token**.
3. Under **App Settings > Basic**, copy the **App Secret** — this becomes `WHATSAPP_APP_SECRET`, used to verify webhook signatures.
4. Choose a `WHATSAPP_VERIFY_TOKEN` yourself (any random string) — you'll enter the same value in both `.env` and the Meta webhook configuration screen.
5. Fill in `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, and `WHATSAPP_API_VERSION` in `.env`.
6. Set `OWNER_WHATSAPP_NUMBER` to your own WhatsApp number (digits only, with country code, no `+`) so Human Override and sensitive-info approval notifications reach you.
7. Set `ADMIN_NUMBERS` to a comma-separated list of numbers allowed to run admin commands (usually just your own number).

## Webhook Setup

1. Expose your running app on a public HTTPS URL (see [Deployment](#deployment) for options — a reverse proxy with a valid TLS certificate is required; Meta will not call a plain HTTP or self-signed endpoint).
2. In the Meta app dashboard, go to **WhatsApp > Configuration** and set the **Callback URL** to:
   ```
   https://your-domain.example.com/webhook
   ```
3. Set the **Verify Token** to the same value as `WHATSAPP_VERIFY_TOKEN` in your `.env`.
4. Click **Verify and Save** — this triggers a `GET /webhook` request that `webhook.py` handles automatically.
5. Subscribe to the **messages** webhook field.
6. Send a test WhatsApp message to your business number and confirm you see log activity in `data/logs/app.log`.

## OpenRouter Setup

1. Create an account at [openrouter.ai](https://openrouter.ai/) and generate an API key.
2. Set `OPENROUTER_API_KEY` in `.env`.
3. `OPENROUTER_PRIMARY_MODEL` defaults to `openai/gpt-oss-120b` and `OPENROUTER_FALLBACK_MODEL` defaults to `openai/gpt-oss-20b`. Check OpenRouter's model catalog for the exact current model slugs available to your account, since provider-side model identifiers can change; update these two values if needed.
4. Optionally set `OPENROUTER_SITE_URL` / `OPENROUTER_SITE_NAME`, which OpenRouter uses for attribution in their dashboard (not required for functionality).

---

## Two Memory Systems: Conversation History vs. Smart Memory

The assistant runs **two completely independent memory systems** side by side. Neither one changes how the other works.

| | **Conversation History** (`memory.py`) | **Smart Memory** (`smart_memory.py`) |
|---|---|---|
| What it stores | Every inbound/outbound message, verbatim | Structured, durable facts about the contact (name, city, job, favorites, ...) |
| Table | `messages` | `user_memories` (+ `memory_history` for versioning) |
| Growth | Unbounded — every message is kept forever | Bounded — one active row per fact/key, updated in place |
| Used for | Recent-window chat context (`MEMORY_MAX_MESSAGES` / `MEMORY_MAX_CHARACTERS`) + full-history retrieval search | Long-term facts injected into every prompt, regardless of how long ago they were mentioned |
| Touched by Smart Memory code? | **Never.** `smart_memory.py` never reads or writes the `messages` table. | — |
| Touched by Conversation History code? | — | **Never.** `memory.py`'s four core functions (`add_message`, `get_recent_messages`, `get_conversation_history_for_prompt`, `clear_memory`) are unmodified and never call into `smart_memory.py`. |

### Architecture

```
Contact
   |
   |------ Message            (Conversation History — memory.py)
   |
   |------ UserMemory          (Smart Memory — smart_memory.py)
                |
                |------ MemoryHistory   (every update keeps the old value)
```

`prompt_builder.py` is the only place the two systems meet — it assembles the final prompt in this exact order:

```
System Prompt (includes the rendered Owner Profile)
        |
Smart Memory summary            <-- smart_memory.build_memory_prompt()
        |
[Retrieved relevant history]    <-- memory.search_conversation_history() (only for recall-style
        |                            questions Smart Memory couldn't already answer)
Conversation History (recent window)   <-- memory.get_conversation_history_for_prompt() (unchanged)
        |
Current User Message
```

### Database Models

**`UserMemory`** (one row per remembered fact):
`id`, `contact_id`, `category`, `subcategory`, `memory_type`, `key`, `value`, `summary`, `confidence`, `importance`, `retention_policy`, `tags`, `source`, `source_message_id`, `created_at`, `updated_at`, `last_used_at`, `expires_at`, `access_count`, `is_active`.

**`MemoryHistory`** (append-only audit trail — a row is written every time a `UserMemory` value changes):
`id`, `memory_id`, `old_value`, `new_value`, `changed_at`, `changed_by`.

Nothing is ever hard-deleted from `UserMemory` on a normal update — the previous value is preserved in `MemoryHistory` first, which supports rollback and auditing. `/smartmemory delete` and `/smartmemory clear` (see below) perform a genuine delete, since that is an explicit destructive admin action rather than a value update.

Category, memory type, importance, retention policy, and source are all backed by proper Python `Enum` classes (`MemoryCategory`, `MemoryType`, `ImportanceLevel`, `RetentionPolicy`, `MemorySource` in `models.py`) rather than raw strings, for type safety and validation. Subcategory is intentionally a free-text field (there are 200+ subcategories across 23 categories — see below — so an enum per subcategory would be unmaintainable); semantic matching over subcategories is handled separately (see "Semantic Search" below).

### Memory Categories

23 categories are supported, matching the specification exactly: **Personal, Contact, Education, Work, Projects, Skills, Preferences, Goals, Travel, Family, Friends & Relationships, Pets, Health, Finance, Social Accounts, Devices, Important Dates, Conversation Preferences, AI Settings, Interests, Events & Experiences, Memories, Other.** Each category has its own set of common subcategories (e.g. Personal → Full Name, Nickname, Date of Birth; Preferences → Favorite Programming Language, Favorite Movie, ...).

### Automatic Extraction

Every inbound user message is analyzed by `smart_memory.extract_memories_from_message()` (called from `routes.py`, right after the message is saved to Conversation History, wrapped so a Smart Memory failure can never break the reply pipeline). Extraction happens in two layers:

1. **AI-based extraction** (`SMART_MEMORY_AI_EXTRACTION_ENABLED=true`, the default): the configured OpenRouter model is asked to return a strict JSON list of `{category, subcategory, value, memory_type, importance, confidence}` objects for the message, given the full list of valid categories/types. This is what allows a single message to yield **multiple independent facts** — e.g. *"My name is Ali. I study at UOL. I live in Lahore."* correctly produces three separate memories (Full Name, University, City) rather than stopping after the first one.
2. **Regex-based fallback**: if AI extraction is disabled or fails, a smaller set of pattern-based extractors covers the common phrasings from the specification's own examples (name, city, university, company, favorite programming language, phone, email, birthday, etc.), each bounded to stop at the sentence boundary so multi-sentence messages don't bleed into one giant captured value.

Every extracted candidate is passed through the sensitive-content gate (see below) and the duplicate/conflict resolution logic (see "Memory Conflict Resolution") before being saved.

### Explicit Remember / Forget Commands

Natural-language directives always take priority over automatic inference and bypass the normal confidence threshold:

- *"Remember my phone number."* / *"Remember that I study at UOL."* → saved immediately, even at low confidence, with `source = user_explicit`.
- *"Don't remember this."* / *"Forget my address."* / *"Forget everything about my previous job."* → the matching Smart Memory record(s) are deleted immediately (previous value preserved in `MemoryHistory`).
- *"Update my city to Lahore."* → directly updates the `City` fact rather than creating a duplicate.

These are detected deterministically via `smart_memory.is_explicit_remember_command()` / `is_explicit_forget_command()` / the update-pattern matcher — not via the AI extraction path — so they behave predictably every time.

### Sensitive Information

Smart Memory **never automatically saves**: passwords, OTPs, PINs, recovery codes, API keys, access tokens, secret keys, private keys, authentication cookies, session tokens, CVV, or card numbers (`security.check_memory_content_sensitivity()`, hard-blocked regardless of instruction). Financial account details, government IDs, medical information, and bank details are **soft-blocked** — they are only stored if the user's message explicitly says *"remember this"* (or an equivalent explicit-remember phrasing); otherwise they are skipped even if mentioned in passing.

### Semantic Search

Retrieval does not depend on exact key matches. A configurable synonym/alias map plus fuzzy string matching (`difflib`) resolves phrasings like *"my favourite language"* → `Favorite Programming Language` (also matching "Programming Language", "Coding Language"), or *"phone / mobile / cell number"* → `Phone Number`, or *"address / home address / current address"* → `Address`. This applies both to what gets saved (normalizing a stated subcategory to its canonical key) and to what gets searched (`search_memory()`).

### Conversation Retrieval (fallback, not primary)

Memory retrieval always follows this order:

1. **Search Smart Memory first** (`search_memory()`).
2. If Smart Memory has nothing relevant, **search the complete Conversation History** for a small number of relevant past messages (`memory.search_conversation_history()` — a new, additive function; it does not change `get_recent_messages()`/`get_conversation_history_for_prompt()`). Only the relevant messages found are added to the prompt — the full history is never dumped in at once.
3. If neither has an answer, the assistant is instructed to say so honestly rather than invent one.

`search_conversation_history()` currently uses indexed `LIKE`-based filtering scoped to the requesting contact only (never a full unscoped table scan), with its function signature deliberately kept stable so the implementation can later be swapped for SQLite FTS5 or a vector-search backend without any caller needing to change.

### Memory Versioning

Every time a Smart Memory value changes, the **previous** value is written to `MemoryHistory` before the new value is applied — nothing is silently overwritten. This supports rollback, auditing, and historical tracking of how a fact evolved over time (e.g. City: Lahore → Karachi, both preserved).

### Automatic Importance Ranking

A memory's `importance` is automatically recomputed based on how often it's actually used (`access_count`, incremented every time a memory is returned by search/retrieval and included in a prompt):

```
access_count > 20  -> Critical
access_count > 10  -> High
access_count > 5   -> Medium
otherwise          -> Low
```

Frequently-referenced facts naturally rise in importance without any manual tagging.

### Expiration

**Short-Term** and **Temporary** memories expire automatically (`SMART_MEMORY_SHORT_TERM_DAYS` / `SMART_MEMORY_TEMPORARY_DAYS` in `.env`, applied when no explicit `expires_at` is given). A scheduled job (`scheduler.py`, running hourly) sweeps and soft-deletes (`is_active = False`) any expired records. **Permanent** memories never expire; **Long-Term** memories only expire if an explicit expiry is set. Expired/inactive memories are never loaded into a prompt.

### Memory Statistics

`smart_memory.py` exposes analytics functions intended for a future admin dashboard: total memory count, breakdown per category, most-used memories, most-recently-updated memories, oldest memories, last access time, and average confidence/importance across a contact's active memories.

### Memory Source

Every Smart Memory records where it came from, via the `MemorySource` enum: `user_explicit` (an explicit "remember ..." instruction), `user_statement` (inferred from an ordinary statement), `conversation_inference`, `admin_command`, `imported`, or `manual_update`. This keeps extraction transparent and debuggable — you can always tell whether a fact was stated outright or inferred.

### Memory Conflict Resolution

If a new value conflicts with an existing active memory for the same key (e.g. *City = Lahore*, later *"I moved to Karachi"*), the newest value **replaces** the old one — the previous value is preserved in `MemoryHistory` first, and there is never more than one active record for the same key at the same time.

### Prompt Budget

Smart Memory is never dumped into the prompt in full. `build_memory_prompt()` selects only relevant / high-importance / recently-used memories, bounded by two configurable limits in `.env`:

```
SMART_MEMORY_MAX_ITEMS=15          # max number of facts included per prompt
SMART_MEMORY_MAX_CHARACTERS=2000   # max total characters of the Smart Memory block
```

### Smart Memory Configuration Reference

| Variable | Default | Purpose |
|---|---|---|
| `SMART_MEMORY_MAX_ITEMS` | `15` | Max facts injected into a single prompt |
| `SMART_MEMORY_MAX_CHARACTERS` | `2000` | Max total characters of the injected Smart Memory block |
| `SMART_MEMORY_AI_EXTRACTION_ENABLED` | `true` | Use the AI model to extract structured facts (falls back to regex if disabled/unavailable) |
| `SMART_MEMORY_MIN_CONFIDENCE` | `0.55` | Minimum confidence for an automatically-inferred fact to be saved (explicit "remember" commands bypass this) |
| `SMART_MEMORY_SHORT_TERM_DAYS` | `14` | Default lifetime for Short-Term memories with no explicit expiry |
| `SMART_MEMORY_TEMPORARY_DAYS` | `1` | Default lifetime for Temporary memories with no explicit expiry |

---

## Admin Commands

Send these as WhatsApp text messages **from a number listed in `ADMIN_NUMBERS`**:

```
/ai on | /ai off                        Global AI switch
/block <wa_id>                          Block a contact
/unblock <wa_id>                        Unblock a contact
/memory clear <wa_id>                   Clear a contact's Conversation History
/profile                                Show the owner profile
/profile set <field> <value>            Update a profile field
/status                                 Show current system status
/help                                   List all commands
/contact ai on <wa_id>                  Enable AI for one contact
/contact ai off <wa_id>                 Disable AI for one contact
/workinghours on | /workinghours off    Toggle working-hours enforcement
/set starttime <HH:MM>                  Set working-hours start time
/set endtime <HH:MM>                    Set working-hours end time
/set awaymessage <text>                 Set the away message
/approve <request_id>                   Approve a pending sensitive-info request
/reject <request_id>                    Reject a pending sensitive-info request
/smartmemory show <wa_id>               Show every active Smart Memory fact for a contact
/smartmemory list <wa_id> [category]    List facts, optionally filtered by category
/smartmemory search <wa_id> <query>     Semantic-ish search over a contact's facts
/smartmemory delete <wa_id> <query>     Delete fact(s) matching a query
/smartmemory clear <wa_id>              Delete ALL Smart Memory facts for a contact
/reload                                 Reload dynamic settings/profile from disk
```

**Backward-compatible `/memory` aliases** — both command spellings are supported side by side:

```
/memory show <wa_id>                    exact alias of /smartmemory show
/memory list <wa_id> [category]         exact alias of /smartmemory list
/memory search <wa_id> <query>          exact alias of /smartmemory search
/memory delete <wa_id> <query>          exact alias of /smartmemory delete
```

> `/approve` and `/reject` are not part of a numbered command in the original feature list but are required to complete the Owner Approval workflow described under Sensitive Information Detection — without them there is no way to actually approve or reject a pending request from WhatsApp chat.
>
> **Naming note:** the specification's own wording lists the Smart Memory admin commands as plain `/memory show`, `/memory list`, `/memory search`, `/memory delete`, `/memory clear`, aliased 1:1 onto Smart Memory. Four of those five now work exactly that way — `/memory show`, `/memory list`, `/memory search`, and `/memory delete` are exact aliases of their `/smartmemory ...` equivalents, added with no behavior change since none of those four action words had any prior meaning under `/memory`.
>
> **`/memory clear <wa_id>` is the one deliberate exception** and is **not** aliased to `/smartmemory clear`. It already existed with a different, pre-existing, already-relied-upon meaning — clearing a contact's **Conversation History**. Its call shape (`/memory clear <wa_id>`) is syntactically identical to what a Smart-Memory-clear alias would look like, so there is no way to make `/memory clear` mean "delete all Smart Memory facts" without silently changing what the existing command does for anyone already using it — and backward compatibility for existing commands is a standing requirement for this project. `/memory clear` therefore keeps clearing Conversation History, exactly as before. To delete all Smart Memory facts for a contact, use `/smartmemory clear <wa_id>` (unaffected, unambiguous).

---

## Health & Status Endpoints

- `GET /health` — lightweight liveness probe (process up + uptime). Use this for load balancer / container health checks.
- `GET /status` — detailed dependency status: database connectivity, OpenRouter reachability, WhatsApp Cloud API reachability, and overall status (`ok` / `degraded`).

Neither endpoint requires authentication and neither ever returns secret values.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Webhook verification fails (403) | `WHATSAPP_VERIFY_TOKEN` mismatch | Ensure the value in Meta's dashboard exactly matches `.env` |
| POST /webhook returns 403 "Invalid signature" | Wrong or missing `WHATSAPP_APP_SECRET` | Double-check the App Secret from Meta App Settings > Basic |
| No replies are sent | Global AI off, contact AI off, working hours, or human override active | Send `/status` from an admin number to check current state |
| AI replies are generic fallback text | Both primary and fallback OpenRouter models failed | Check `data/logs/app.log` for `AI_ERROR` lines; verify `OPENROUTER_API_KEY` and model slugs |
| Contact never gets a welcome message | Contact was already marked `welcomed` previously, or `auto_save_unknown_contacts` already promoted them | Check the `contacts` table, or clear the contact row to reset |
| Admin commands ignored | Sender's number not in `ADMIN_NUMBERS` | Confirm the number format matches WhatsApp's `wa_id` format (digits only, country code, no leading `+`) |
| A fact never gets remembered | AI extraction failed silently, or confidence was below `SMART_MEMORY_MIN_CONFIDENCE`, or the content was sensitive and not explicitly "remember this" | Say "Remember that ..." explicitly to bypass the confidence threshold; check `data/logs/app.log` for `smart_memory` errors; run `/smartmemory show <wa_id>` to see what is actually stored |
| Smart Memory has a stale/wrong value | An update wasn't detected as referring to the same fact | Use `/smartmemory search <wa_id> <query>` to find the record's exact subcategory, then say "Update my <field> to <value>" or use `/smartmemory delete` |
| `pip install -r requirements.txt` fails on a package | A dependency dropped/changed its Python 3.14 wheel between when this project was generated and when you're installing it | Re-check that package's PyPI page for the latest version supporting `cp314`, and bump the minimum version in `requirements.txt` accordingly |

---

## Deployment

Any of the following work well:

- **Reverse proxy + systemd**: run `uvicorn app:app` under a `systemd` service, in front of **nginx** or **Caddy** for TLS termination (Caddy is the simplest option for automatic HTTPS certificates).
- **Docker**: build an image from `python:3.14-slim`, `COPY` the project in, `RUN pip install -r requirements.txt --break-system-packages` (or use a virtualenv inside the image), and `CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]`. Mount `data/` as a volume so the SQLite DB and logs persist across container restarts.
- **Managed platforms** (Render, Railway, Fly.io, a plain VPS): any platform that can run a long-lived Python process and expose an HTTPS URL works, since this app has no platform-specific dependencies.

Always place the app behind HTTPS — Meta requires it for webhook callback URLs.

---

## Production Tips

- **Back up `data/contacts.db` regularly** — it holds every contact, conversation history, and approval/audit log.
- **Rotate `WHATSAPP_ACCESS_TOKEN`** periodically and use a permanent token (via a Meta System User) rather than a short-lived one, so the assistant doesn't silently stop working.
- **Multiple worker processes**: `rate_limiter.py` and `spam_detector.py` keep state in-process memory by design (see their module docstrings). If you run more than one uvicorn/gunicorn worker, each worker enforces limits independently, effectively multiplying your configured limits. For a single-instance deployment (the common case for a personal WhatsApp assistant) this is not an issue; if you scale out, either pin to a single worker or move that state to a shared store (e.g. Redis) as a future enhancement.
- **Monitor `data/logs/app.log`** (rotated automatically per `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT`) and the `/status` endpoint for early warning of OpenRouter or WhatsApp API issues.
- **Review pending approvals**: sensitive-information requests that the owner never responds to auto-expire (`approval.approval_timeout_minutes` in `data/settings.json`, swept every 60 seconds by `scheduler.py`) so contacts are never left hanging indefinitely, but it's worth checking in on `/status` and the `approval_requests` table periodically.
- **Keep `.env` out of version control** — it's already excluded via `.gitignore`, but double-check before pushing to a shared repository.
