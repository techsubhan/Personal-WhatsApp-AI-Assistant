# System Analysis — WhatsApp AI Assistant Admin Dashboard

## A. Existing architecture (before this change)

| Layer | Reality |
|---|---|
| Backend | FastAPI (Python 3.12+), single process, `app.py` entrypoint |
| Routers mounted | `webhook.py` (`GET/POST /webhook`), `health.py` (`GET /health`, `GET /status`), root `GET /` |
| Database | SQLite via SQLAlchemy (`data/contacts.db`), models in `models.py` |
| AI/LLM | OpenRouter (`ai.py`), primary model `openai/gpt-oss-120b`, fallback `openai/gpt-oss-20b` |
| WhatsApp | Meta WhatsApp Cloud API (`whatsapp.py`) |
| Scheduler | APScheduler — approval expiry sweep + Smart Memory expiry sweep (`scheduler.py`) |
| Config | `.env` (static secrets) + `data/settings.json` (dynamic runtime settings) + `data/profile.json` (owner profile) |
| Frontend | None existed prior to this change |
| Admin interface | WhatsApp chat commands (`admin.py`), parsed from messages sent by numbers in `ADMIN_NUMBERS` |

There was no dashboard-facing HTTP API and no auth/session layer prior to this
change. All control happened via WhatsApp chat commands or direct file/DB edits.

## B. Feature → dashboard mapping

| Feature | Existing implementation | Dashboard page | Dashboard action |
|---|---|---|---|
| Global AI switch | `GlobalSetting` DB row (`contacts.py`/`admin.py`) | AI control | Toggle |
| Per-contact AI | `Contact.ai_enabled` (`contacts.py`) | Contacts | Toggle |
| Block/unblock | `contacts.block_contact`/`unblock_contact` | Contacts | Block button |
| Conversation history | `Message` table (`memory.py`) | Conversations | List/read |
| Clear conversation | `memory.clear_memory` | Conversations | Clear button |
| Approvals | `ApprovalRequest` table (`approval.py`) | Approvals | Approve/Reject |
| Human override | `Contact.human_override_until` (`human_override.py`) | Human override | Activate/clear |
| Smart Memory | `UserMemory`/`MemoryHistory` (`smart_memory.py`) | Smart memory | View/search/delete |
| Working hours | `settings.json.working_hours` (`working_hours.py`) | Working hours | Edit schedule |
| Owner profile | `data/profile.json` | Settings | Edit |
| Admin audit log | `AdminActionLog` table | Activity log | List |
| Request log | `RequestLog` table | Activity log | List |
| System health | `GET /health`, `GET /status` | System health | Read-only (pre-existing) |
| Rate limiting / spam | In-memory only, no persistence | — | Not viewable historically (by original design) |

## C. New API surface added (`admin_api.py`)

All routes are prefixed `/admin` and require header `X-Admin-Key: <INTERNAL_API_KEY>`.
If `INTERNAL_API_KEY` is empty, every route returns `503` (fails closed).

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/overview/stats` | Dashboard home metrics |
| GET | `/admin/ai/status` | Global AI + working-hours state |
| POST | `/admin/ai/toggle` | Global AI on/off |
| GET | `/admin/contacts` | List/search/filter/paginate contacts |
| GET | `/admin/contacts/{wa_id}` | Contact detail |
| POST | `/admin/contacts/{wa_id}/block` | Block/unblock |
| PATCH | `/admin/contacts/{wa_id}/ai` | Per-contact AI toggle |
| GET | `/admin/contacts/{wa_id}/messages` | Conversation history |
| DELETE | `/admin/contacts/{wa_id}/messages` | Clear conversation history |
| GET | `/admin/approvals` | List approval requests |
| POST | `/admin/approvals/{id}/resolve` | Approve/reject |
| GET | `/admin/overrides` | List active human overrides |
| POST | `/admin/contacts/{wa_id}/override` | Activate/clear override |
| GET | `/admin/contacts/{wa_id}/memories` | List/search Smart Memory facts |
| DELETE | `/admin/contacts/{wa_id}/memories/{id}` | Delete a fact |
| GET/PATCH | `/admin/settings` | Runtime settings (working hours, approval, typing delay, etc.) |
| GET/PATCH | `/admin/profile` | Owner profile |
| POST | `/admin/reload` | Reload dynamic config from disk |
| GET | `/admin/logs/admin-actions` | Admin action audit trail |
| GET | `/admin/logs/requests` | Request/error log |

Every one of these wraps an already-existing function from `contacts.py`,
`memory.py`, `approval.py`, `human_override.py`, `smart_memory.py`, or
`config.py` — no business logic was rewritten. Dashboard-originated actions
are recorded in the same `AdminActionLog` table as WhatsApp `/commands`
(actor = `"dashboard"`), so the Activity log stays a single, unified trail.

## E. Changelog — approval follow-through, contact saving, Smart Memory, theming, responsiveness

Second round of targeted fixes on top of the dashboard above. Nothing was
rebuilt; every change below is additive or a small, scoped edit to an
existing function, per the "do not start over" instruction.

### 1–2. Approval approve/reject now actually does something

**Root cause found:** `admin_api.py`'s dashboard resolve endpoint only ever
called `approval.resolve_approval()`, which just flips the DB status. The
*actual* send-to-contact logic (`_finalize_approval_followup`) existed only
in `routes.py` and was wired exclusively to the WhatsApp `/approve <id>`
text-command path - the dashboard never reached it.

**Fix:** `routes.py` now exposes a public `finalize_approval_by_id(db,
request_id)`, refactored out of the old private function. Both the
WhatsApp admin-command path and `admin_api.py`'s dashboard endpoint call
this one function - no duplicate business logic. `admin_api.py`'s
`/admin/approvals/{id}/resolve` now calls it after resolving, and the
approvals list response gained an `action_summary` field ("AI reply sent
to contact" / "Rejection sent to contact" / "Awaiting owner decision" /
"Expired before owner responded") for the dashboard's history view.
Dashboard's Approvals page already displayed contact name, number, and the
full original message - that part needed no backend change.

### 3. "Save 030-363-00910 as Bhai Waseem" now actually saves it

**Root cause found:** this phrase didn't match any existing
sensitive-detection or Smart-Memory-extraction pattern, so it fell through
to the plain LLM reply with no real capability behind it - hence the
hallucinated "Open Contacts → Add contact" instructions.

**Fix:** new `smart_memory.try_save_named_contact()` recognizes explicit
"save/add `<phone>` as `<name>`" and "`<phone>` = / is `<name>`" phrasing,
and saves it as a Smart Memory fact under `MemoryCategory.CONTACT` with the
**phone number as the subcategory key** and the **name as the value**.
This reuses `save_memory()`'s existing dedup-by-(contact, category,
subcategory) logic completely unmodified: re-saving the same phone number
later (to correct a name) updates that memory in place via
`update_memory()` (old name preserved in `MemoryHistory`), while different
phone numbers get their own subcategory key and simply coexist. No second
contact system was introduced - `Contact` (the WhatsApp Business
conversation partner) and this Smart Memory address-book entry are
different, correctly separate concepts. `routes.py` now calls this
deterministically (bypassing the LLM entirely for this one intent) right
after saving the inbound message, and sends back an accurate confirmation
instead of a hallucinated one.

### 4. Bank/sensitive details reaching Approval History but not Smart Memory

**Root cause found:** by existing design (`save_memory()`'s
`allow_soft_blocked` rule), bank-detail-category content is soft-blocked
from automatic Smart Memory extraction unless the message was phrased as
an explicit "remember this" command - a real safety rule, working as
designed, just never re-applied after the owner approved the request.

**Fix:** `extract_memories_from_message()` gained one new optional
parameter, `force_explicit: bool = False`. When `True`, the
automatic-extraction path is treated exactly like an explicit "remember"
command (soft-block bypassed, importance HIGH, retention PERMANENT).
`finalize_approval_by_id()` now calls it with `force_explicit=True`
immediately after generating the approved AI reply - the owner's approval
*is* the explicit authorization this flag represents. No new
memory/authorization system; the existing soft-block rule and its existing
bypass mechanism are simply invoked at the correct additional moment.

### 5. Smart Memory dashboard display for named contacts

`SmartMemory.tsx` now special-cases `category === "contact"` entries whose
subcategory looks like a phone number: the name (`value`) is shown as the
primary line and the phone number (`subcategory`) as a secondary mono
line, instead of the generic "category · subcategory" header used for
every other memory type. The Smart Memory page also now shows which
contact (name + wa_id) is selected above the list, and each row now shows
a Permanent/Expires status badge alongside importance.

### 6. Response delay setting

No backend change was needed - `/admin/settings` already exposed
`typing_delay.enabled/min_seconds/max_seconds` exactly as
`typing_delay.py` and `config.py` define them (confirmed by inspection: no
hard-coded range exists in the backend, so none was invented in the UI).
Moved from a generic Settings section into its own clearly-labeled
"Response delay" card on the AI Control page (natural home for it,
alongside the AI status/typing-delay other AI behavior settings), with an
explanatory line and a single editable copy instead of two.

### 7. Light / Dark / System theme

Rebuilt the color system on CSS variables (`src/index.css`) instead of
fixed Tailwind hex values, so the same utility classes (`bg-canvas`,
`text-ink-primary`, etc.) resolve differently per `[data-theme]`. Light
mode is a deliberately separate token set (white/near-white surface steps,
deepened accent colors for AA contrast on white), not an inversion of
dark mode. `ThemeContext` manages light/dark/system (system follows
`prefers-color-scheme` live), persists the choice, and a small
pre-hydration script in `index.html` sets the attribute before first paint
to avoid a flash of the wrong theme. A segmented `ThemeToggle` sits in the
Topbar on every page.

### 8. Responsive design

- Conversations: was a fixed three-column layout that would overflow on
  mobile. Now the contact list and the message thread are each full-width
  and shown one at a time below the `md` breakpoint (with a back button in
  the thread header), and side-by-side above it - unchanged desktop
  behavior.
- Approvals: request cards now stack (name/message above, action buttons
  below) below `sm`, instead of a fixed row that could overflow.
- Smart Memory rows: same stacking treatment for the delete button.
- Contacts and Logs tables were already wrapped in horizontal-scroll
  containers, which instruction #8 explicitly allows for tabular data.
- Sidebar/Topbar/Layout were already responsive (drawer + hamburger on
  mobile) from the first pass and needed no change here.

## F. What was and wasn't touched (this round)

**Modified backend files:** `smart_memory.py` (new `try_save_named_contact()`
+ `_CONTACT_SAVE_PATTERNS`, new `force_explicit` param on
`extract_memories_from_message()` — additive, default-off, every existing
caller unaffected), `routes.py` (new contact-save interception step + public
`finalize_approval_by_id()` refactored out of the previously-private
follow-up function), `admin_api.py` (approval-resolve endpoint now calls
the follow-through; approvals list gained `action_summary`).
**Untouched:** `models.py`, `contacts.py`, `approval.py`,
`human_override.py`, `typing_delay.py`, `config.py`, `admin.py`, database
schema, WhatsApp send logic, AI generation logic, prompt builder — no
second contact/memory/approval system was introduced anywhere.
**Modified frontend files:** `Approvals.tsx`, `SmartMemory.tsx`,
`AIControl.tsx`, `Settings.tsx`, `Conversations.tsx`, `Topbar.tsx`,
`App.tsx`, `tailwind.config.js`, `index.css`, `index.html`, API types.
**New frontend files:** `ThemeContext.tsx`, `ThemeToggle.tsx`.

## G. Changelog — sensitive-request refusal bug (root cause + fix)

Investigated exactly per the report: three real messages
("Subhan bank account details share kar", "Finance send kar", "033...
Subhan ke name save kar lo") were producing a direct AI refusal instead of
an approval request or a contact save. Found **two distinct, separate**
root causes - not one:

### Root cause 1 — approvals were silently rejected when `OWNER_WHATSAPP_NUMBER` was unset

- **File / function:** `routes.py`, the sensitive-request branch of
  `_process_inbound_message_inner()`.
- **Current (buggy) behavior:** `security.detect_sensitive_request()`
  correctly matched "bank account" in "Subhan bank account details share
  kar" (verified by direct regex test). But the code then checked
  `if not settings.owner_whatsapp_number:` and, if that env var was empty,
  sent an immediate `REJECTION_MESSAGE` to the contact and **returned
  without ever calling `create_approval_request()`** - so a correctly
  detected sensitive request never became a reviewable approval at all.
  This defeated the entire point of having a dashboard: the owner should
  be able to review pending approvals *there*, without needing WhatsApp
  notifications configured.
- **Correct behavior:** the approval request is created unconditionally
  the moment sensitive content is detected, and is visible on the
  dashboard's Approvals page immediately. The WhatsApp ping to
  `OWNER_WHATSAPP_NUMBER` is now purely a best-effort convenience
  notification layered on top - if it's not configured, a warning is
  logged and nothing else changes.
- **Change:** merged the two branches in `routes.py` into one path that
  always calls `create_approval_request()`, and only conditionally
  attempts the WhatsApp notification.

### Root cause 2 — the sensitive-detection regex was too narrow to catch common phrasings, so unmatched messages went straight to an AI that was instructed to self-refuse

- **File / function:** `security.py`, `_SENSITIVE_PATTERNS` /
  `detect_sensitive_request()`; and `smart_memory.py`,
  `_CONTACT_SAVE_PATTERNS` / `try_save_named_contact()`.
- **Current (buggy) behavior:** "Finance send kar" matched no pattern at
  all (no "bank", "account", "financial statement", etc.), and "033...
  Subhan ke name save kar lo" matched neither the sensitive patterns
  (which only look for the literal English phrase "phone number") nor the
  earlier round's contact-save patterns (which only recognized English
  "save X as Y" word order). Both messages therefore fell through to the
  normal AI reply path. `data/prompts/system_prompt.txt` explicitly
  instructs the AI: *"If a request involves sensitive information ... do
  not respond to that part of the request yourself; the system will route
  it for the owner's approval separately."* Since the system never routed
  these two messages anywhere, the AI applied that instruction on its own
  judgment and simply refused conversationally - a dead end with no
  approval and no contact save.
- **Correct behavior:** broaden detection to the phrasings actually used,
  in both English and common Roman Urdu word order, so routing happens
  *before* the AI ever sees these messages (verified with the project's
  own stated design philosophy in `security.py`: "favor recall over
  precision... a false positive here only costs an extra approval round
  trip").
- **Change:**
  - `security.py`: added `finance` / `financial info(rmation)` /
    `financial detail(s)` to the `financial_information` category, and
    `private number` / `number ... bhej|send` to `private_contacts`.
  - `smart_memory.py`: added two Roman-Urdu contact-save patterns -
    phone-first ("`<phone>` [ko] `<name>` ke naam/name [se] save kar
    lo/do") and name-first ("`<name>` ke naam/name se `<phone>` save kar
    lo/do") - alongside the existing English patterns, reusing the exact
    same storage path (see section C.3 above) with no new logic. Verified
    with regex tests against the reported messages plus negative controls
    (e.g. "Finance send kar" correctly does NOT match the contact-save
    patterns; "what's your room number" correctly does NOT match the
    broadened `private_contacts` pattern).

### Root cause 3 (found during root-causing #1/#2) — even after fixing routing, an APPROVED request would still self-refuse

- **File / function:** `prompt_builder.py`, `build_messages()`;
  `routes.py`, `_generate_and_send_ai_reply()` /
  `finalize_approval_by_id()`.
- **Current (buggy) behavior:** the post-approval reply
  (`finalize_approval_by_id()` → `_generate_and_send_ai_reply()`) reused
  `build_messages()` with the exact same system prompt used for every
  other message - including the same "never respond to sensitive
  requests yourself" instruction. The AI has no way to know a specific
  message was already reviewed and approved, so it would refuse again on
  the retry, breaking the "APPROVED → perform the requested action → send
  approved information" flow required by the spec.
- **Correct behavior:** the post-approval generation call needs to tell
  the AI, for that one message only, that the owner already approved it.
- **Change:** `build_messages()` gained an optional `extra_system_note`
  parameter, inserted as an extra system-role message right after the
  main system prompt (the main `data/prompts/system_prompt.txt` file is
  untouched - every other message still gets the normal deflection rule).
  `_generate_and_send_ai_reply()` gained an `approved: bool = False`
  parameter; `finalize_approval_by_id()` now calls it with
  `approved=True`.

No architecture was removed or bypassed: the approval workflow, the
soft-block Smart Memory rules, and the "never reveal internal
configuration" instruction are all still fully in force - these three
fixes only closed genuine gaps in *routing coverage* (patterns) and a
*missing signal* (post-approval context), exactly as scoped.

## H. Changelog — AI Voice Reply (optional TTS)

### Inspection findings (before writing any code)

- **No TTS provider existed anywhere in the project** (confirmed by
  grepping every file for "tts"/"speech" - zero matches before this
  change).
- **`WhatsAppClient` (whatsapp.py) had no media upload/send capability**
  - only `send_text_message`, `mark_as_read`, `check_status` existed.
  The WhatsApp Cloud API itself has always supported media upload
  (`POST /media`) and sending audio messages (`POST /messages` with
  `type: "audio"`); this project simply never implemented that half of
  the API. **BACKEND/API CAPABILITY:** confirmed available at the Meta
  API level, not previously wired up in this codebase - now added as new
  methods on the existing `WhatsAppClient`, not a second client.
- **No incoming-voice (speech-to-text) pipeline exists.** `routes.py`
  only processes `MessageType.TEXT` and `MessageType.INTERACTIVE` -
  inbound `audio` messages are parsed (whatsapp.py already recognizes the
  type) but never transcribed or replied to. This is out of scope for
  the current request (which is specifically **outgoing** AI Voice
  Reply) and was left untouched, per "keep these two features
  independent."
- **No dedicated language-detection module exists.** The existing text
  pipeline mirrors the contact's language purely through a system-prompt
  instruction to the AI itself ("mirror their language" -
  `data/prompts/system_prompt.txt`), not a separate detect-then-branch
  step anywhere in the code. TTS language-awareness is achieved by
  reusing this: the AI-generated reply text is already in the mirrored
  language by the time it reaches TTS, and OpenAI's TTS model
  auto-detects language from input text natively - no new
  language-detection system was built or faked.

### What was added

- **`tts.py` (new file):** minimal client for OpenAI's
  `/v1/audio/speech` REST endpoint, mirroring `ai.py`'s existing
  integration shape (one typed exception, no raw HTTP errors escape,
  same timeout/retry-free simplicity as appropriate for a single
  synchronous call). Chosen as the default provider specifically because
  it needs only one API key, matching how `OPENROUTER_API_KEY` is
  already integrated - `TTS_PROVIDER` exists as the extension point for
  a different provider later.
- **`whatsapp.py` (extended, not duplicated):** `upload_media()` and
  `send_audio_message()` added to the existing `WhatsAppClient`, reusing
  its existing base URL, auth header, and timeout.
- **`voice_reply.py` (new file):** orchestrates TTS + WhatsApp media
  send + guaranteed temp-file cleanup, and implements the three reply
  modes (text_only / voice_only / text_and_voice) including the
  text-fallback-on-failure rule. Deliberately contains **no** gating
  logic of its own (no re-check of global AI switch, blocked contacts,
  human override, working hours, or approval status) - see below.
- **`config.py`:** new static `TTS_*` settings (provider/key/model/
  timeout/hard character ceiling, `.env`-only, never exposed to the
  frontend) and a new `voice_reply` block in the existing runtime
  settings default (`enabled`, `reply_mode`, `voice`, `speed`, `format`,
  `max_characters`, `text_fallback_enabled`) - picked up automatically
  by the existing generic `load_runtime_settings()` /
  `update_runtime_setting()` / `/admin/settings` machinery with zero
  changes to any of those functions.
- **`routes.py`:** `_generate_and_send_ai_reply()` (the single function
  used by both the normal pipeline and the post-approval follow-up) now
  calls `voice_reply.attempt_voice_reply()` after the existing
  secret-leak scan and before deciding whether to still send text. The
  voice outcome is appended to the same `_log_request()` detail string
  already used for every reply (`...:voice=sent` /
  `...:voice=failed:tts_error` / `...:voice=skipped:disabled`), so it
  shows up in the existing Activity Log page with no new endpoint.

### Security - why no new gating code was needed

Every existing gate (global AI switch, per-contact AI, human override,
working hours, blocked contacts, spam/mute, sensitive-info approval) runs
in `routes.py` **before** `_generate_and_send_ai_reply()` is ever called.
Voice reply hooks into the tail of that exact function, so it inherits
every one of those gates automatically - a blocked contact, a
human-override conversation, or a message that's still pending approval
never reaches the voice-reply code path at all, with no duplicate
checks written. For the approval flow specifically: `finalize_approval_by_id()`
already calls this same function with `approved=True` (see section G.3),
so "bank details → approval → owner approves → voice reply sent" works
through the exact same integration point, with zero additional wiring.

### Dashboard

Added an "AI voice reply" card to the AI Control page (`AIControl.tsx`),
next to Response Delay: enabled toggle, reply mode selector, voice name,
speed, audio format (restricted to WhatsApp-deliverable formats - mp3/
aac/opus; wav/flac exist in `tts.py`'s format map for provider
flexibility but are not exposed in the dashboard since WhatsApp's Cloud
API doesn't reliably accept them), max characters per reply, and text
fallback toggle - plus a read-only "Provider: openai · configured/not
configured" line sourced from `/admin/ai/status`. All values are real
`/admin/settings` reads/writes; nothing is mocked.

### Defaults (unchanged-behavior guarantee)

`voice_reply.enabled = false`, `reply_mode = "text_only"` - every
existing deployment continues sending text-only replies exactly as
before until the owner explicitly turns this on from the dashboard.

## I. Changelog — OpenRouter-unified AI/TTS/STT, idempotency, language, handoff, search, scheduling

### A. What already worked (before this round, unchanged)
Global AI switch, per-contact AI, block/unblock, conversation history,
Smart Memory (including named-contact saves and post-approval
persistence), the full Approvals workflow with real WhatsApp
follow-through, Human Override (manual), Working Hours, Response Delay,
AI Voice Reply (outgoing, was OpenAI-direct - see below), the admin
dashboard's 11 pages, and every WhatsApp admin chat command in `admin.py`.

### B. What was missing (confirmed by inspection, not assumed)
- OpenRouter had no audio endpoints wired up; TTS used a separate OpenAI
  key/provider.
- No STT/incoming-voice handling existed at all - `MessageType.AUDIO`
  was parsed but never processed.
- No webhook idempotency - a retried/duplicate webhook delivery
  reprocessed the message and sent a second reply. Root cause:
  `RequestLog`'s unique constraint is only written *after* processing
  completes, so it can't prevent duplicate processing, only duplicate
  logging.
- No language/script detection module (the AI's reply-language mirroring
  was, and remains, prompt-driven, not code-driven).
- No human-handoff detection.
- No distinction in the system prompt against claiming unsupported
  actions (calling, deleting messages).
- No web search capability of any kind (no provider, no API key, no
  gating logic).
- No scheduled-message system (the existing `scheduler.py` only ran
  internal maintenance sweeps, never sent anything to a contact).

### C. What changed (this round)
1. **OpenRouter-unified TTS** (`tts.py` rewritten) and **OpenRouter STT**
   (`stt.py`, new) - both confirmed against OpenRouter's own published
   API docs before implementation (`/audio/speech`,
   `/audio/transcriptions`), both using the existing
   `OPENROUTER_API_KEY`/`OPENROUTER_BASE_URL` - no second provider/key.
2. **Incoming voice** (`voice_input.py`, new; `whatsapp.py` gained
   `download_media()`) - gated by `VOICE_INPUT_ENABLED` (off by default),
   feeds the transcript into the exact same existing text pipeline (same
   sensitive-detection, approval, Smart Memory, AI-reply code every typed
   message already uses).
3. **Webhook idempotency** (`models.ProcessedWebhookMessage`, new;
   `routes._try_claim_message()`) - an atomic DB-constraint claim on the
   WhatsApp message id, made *before* any processing starts. Verified
   with a real concurrency test (10 simultaneous threads racing to claim
   the same id via the identical SQLite UNIQUE-constraint pattern used in
   the actual model): exactly 1 succeeds, every time, including the
   sequential once/twice/3x cases the spec asked for.
4. **Language/script detection** (`language.py`, new) - heuristic
   Unicode-range + word-list based (no ML library available in this
   environment), used only for labeling (`Contact.detected_language`,
   dashboard display) - never for changing the AI's actual reply
   language, which stays prompt-driven as before. Verified against all 7
   canonical examples from the spec (English, Urdu, Roman Urdu, Punjabi,
   Roman Punjabi, Arabic, German) - all pass.
5. **Human handoff** (`security.is_human_handoff_request()`, new;
   wired into `routes.py`) - activates the existing
   `human_override.activate_override()` (no second override system),
   sends an instant multilingual acknowledgment, and notifies the owner
   if `OWNER_WHATSAPP_NUMBER` is configured. Verified against the spec's
   example phrases plus negative controls.
6. **Unsupported-action guardrail** - one line added to
   `data/prompts/system_prompt.txt` telling the AI it cannot call,
   delete, or take any action beyond sending a reply, and must say so
   plainly rather than claim otherwise (no code capability existed for
   any of these, so this is the correct, honest fix - not a workaround).
7. **Web search** (`web_search.py`, new) - **real, dashboard-editable
   gating logic** (global enable + 4 modes + per-contact toggle), but
   **no search provider is implemented** because none exists anywhere in
   this project and none was invented. When a search *would* apply, the
   AI is given a system note to say so honestly instead of silently
   proceeding as if nothing should happen or fabricating a search. See
   `web_search.py`'s docstring for the exact one function
   (`_call_provider`-equivalent) to implement once a real provider/key is
   chosen - everything else (decision logic, settings, dashboard) already
   works.
8. **Scheduled messages** (`models.ScheduledMessage`,
   `scheduled_messages.py`, both new) - full CRUD
   (`/admin/scheduled-messages`, list/create/cancel/retry) plus a new,
   independent background job in the existing `scheduler.py` (not a
   second scheduler) that actually sends due messages via the existing
   `whatsapp_client` and only marks a message `sent` after a real API
   call succeeds.
9. **Safe additive DB migration** (`database.py`) - `Contact` gained two
   new columns (`detected_language`, `web_search_enabled`). Since
   `create_all()` never alters existing tables, a small idempotent
   `PRAGMA table_info` + `ALTER TABLE ... ADD COLUMN` step was added so
   this doesn't break an already-running deployment's existing SQLite
   file - checked and skipped automatically on every future startup once
   applied.
10. **Security audit** - the one genuinely new secret introduced
    (`web_search_api_key`) was added to both existing redaction lists
    (`security.py`'s `_known_secret_values()` and `logger.py`'s
    `_build_secret_list()`). TTS/STT introduced no new secret (they reuse
    `openrouter_api_key`, already covered).
11. **Dashboard**: AI Control page gained Voice Input status (read-only)
    and a full Web Search settings card; Contacts page gained a Language
    column and a per-contact Web Search toggle; new Scheduled Messages
    page (list/create/cancel/retry) with its own sidebar entry.

### D. Backend APIs required (for full section 9/web-search execution)
```
Any real search provider's HTTP API - none is assumed or hardcoded.
Once chosen, implement web_search.py's search() function to call it and
return a text summary. Settings/gating/dashboard already work; only the
actual HTTP call to a provider is missing, by design (no credentials
exist for one, and inventing a fake one was explicitly disallowed).
```

### E. What remains unsupported / honest limitations
- **Web search execution** - gating logic is real; the actual search
  call is not implemented (see D above). The AI is told to say so
  honestly rather than fake a result.
- **Language detection accuracy** - heuristic (Unicode ranges + word
  lists), not a trained model; verified against the spec's canonical
  examples but will be less reliable on ambiguous/very short real-world
  messages, particularly distinguishing Urdu from Punjabi written in
  Perso-Arabic script (the two share nearly their entire alphabet).
  Deliberately never used to change AI behavior, only to label.
- **Voice duration limiting** - `VOICE_MAX_DURATION_SECONDS` is enforced
  *after* transcription (using the duration OpenRouter's response
  reports), not before, since no audio-decoding library is available in
  this environment to measure duration from raw bytes pre-upload. File
  size *is* checked before the network call.
- **Call/Delete actions** - correctly reported as unsupported via a
  system-prompt instruction, since no such capability exists in the
  WhatsApp Cloud API integration in this project (or, for delete, in
  the Cloud API itself for messages already delivered to a user).


## J. Changelog — full A-to-Z audit (bugs found and fixed, nothing rebuilt)

A dedicated audit pass over the entire project as it stood after every
prior round - not new feature work. Verified real issues only; each fix
below is backed by an executed test, not just a claim.

### Bugs found and fixed

1. **Conversation History data loss on human handoff** (`routes.py`) -
   `add_message()` for the inbound user message was positioned *after*
   the human-handoff early-return, so a message like "Mujhe Subhan se
   baat karni hai" was never saved to Conversation History - only the
   assistant's acknowledgment was. The dashboard's Conversations page
   would show a one-sided exchange. **Fix:** moved the save to happen
   immediately after language detection, before every early-return
   branch (handoff, contact-save, sensitive-approval). Verified via
   direct position-in-file check.

2. **Human handoff false-positive risk** (`security.py`) - the Roman
   Urdu pattern `se baat karni hai` matched *any* "I want to talk to
   &lt;name&gt;" phrasing, not just requests to reach the owner - e.g.
   "Mujhe Ahmed se baat karni hai" (about an unrelated third party) would
   have incorrectly activated Human Override. **Fix:** removed the bare
   pattern; `is_human_handoff_request()` now only matches explicit
   human/owner/insaan wording, or the message naming the *actual
   configured owner* (from their profile). Verified with 9 test cases
   including the original false-positive case (now correctly `False`)
   and all previously-passing true positives (still `True`).

3. **Unused imports** (`admin_api.py`) - `List`, `MessageRole`,
   `UserMemory`, `get_recent_messages`, `get_request_by_id` were imported
   but never used. Not a functional bug, but removed for cleanliness
   since this file was authored this engagement.

4. **Stale/inaccurate UI copy** (`HumanOverride.tsx`) - the page's
   description only mentioned the manual-reply trigger for Human
   Override, not the newer automatic handoff-phrase trigger added this
   session. Updated the copy to describe both.

### Verified correct (no bug found, checked deliberately)

- Every one of the 27 `/admin/*` routes requires the auth dependency -
  none are accidentally unprotected.
- `RequestLog`'s unique constraint and the new `ProcessedWebhookMessage`
  claim table cannot collide with each other in practice: exactly one
  `_log_request()`/`RequestLog` write happens per successfully-processed
  wamid (every call site is immediately followed by `return`, checked
  programmatically across the whole file), and the duplicate-webhook
  path deliberately uses the plain file-logger instead of a second DB
  write for that same wamid.
- The additive DB migration (`Contact.detected_language`,
  `Contact.web_search_enabled`) was re-tested against a populated table:
  existing rows correctly receive the column's default value, and
  running the migration twice is a safe no-op.
- Every dashboard API call (22 call sites across `src/api/*.ts`) has a
  matching backend route, and vice versa - no orphaned frontend calls,
  no silently-broken endpoints, cross-checked by extracting both path
  lists and diffing them.
- The contact-save regex family and the sensitive-info regex family
  don't overlap on adversarial inputs (e.g. "Save Subhan's bank account
  number ... as his account" correctly routes to sensitive/approval, not
  a silent contact save) - checked with 4 adversarial probes.
- Voice module error paths (`tts.py`, `stt.py`, `voice_reply.py`,
  `voice_input.py`) never embed message text or audio bytes in a log
  line or exception message - only lengths, formats, and status codes.
- Both secret-redaction lists (`security.py`'s `_known_secret_values()`
  and `logger.py`'s `_build_secret_list()`) include every `_key`/
  `_token`/`_secret` field in `Settings`, cross-checked field-by-field.
- `webhook.py`'s payload parser already gracefully skips status-only
  callbacks (delivered/read receipts) and malformed entries without
  raising - confirmed by reading `parse_webhook_payload()`.

### Final verification run for this round
- All 30 backend `.py` files: `python3 -m py_compile` clean.
- All 43 frontend `.ts`/`.tsx` files: bracket-balance clean.
- 24/24 automated regression checks passed (contact-save patterns,
  sensitive detection incl. negative control, human handoff incl. the
  false-positive-fix case, all 7 language/script examples, webhook
  idempotency incl. a genuine 10-thread concurrency test, DB migration
  safety, and two regression guards for this round's fixes themselves).

### Remaining limitations (unchanged from section E, still honest)
Web search execution still requires a real provider to be added; language
detection is still a heuristic, not a trained model; voice duration
limiting is still enforced post-transcription rather than pre-upload;
call/delete actions are still correctly reported as unsupported rather
than faked. Nothing in this audit round changed the honesty of those
prior disclosures.
