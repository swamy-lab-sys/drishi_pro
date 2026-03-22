# Drishi Pro — Project Reference for Claude

## What is this?
Real-time AI interview assistant. Listens to interview audio → transcribes → finds/generates answer → streams to browser in <30ms (DB) or ~2–3s (LLM). Used for different interview roles: Python, Java, JavaScript, SQL, SaaS, System Design.

---

## Quick Start

```bash
./run.sh                    # start everything (venv, deps, audio, server)
./run.sh tests              # run test suite
python3 -W ignore main.py   # start server directly (port 8000)
```

Server: http://localhost:8000

---

## Architecture — Full Pipeline

```
Microphone / Chrome Extension
        │
        ▼
capture_worker()  [main.py:~491]
  VAD (webrtcvad) — 20ms chunks, 200ms padding
  Silence threshold: config.SILENCE_DEFAULT (default 1.2s, user adjustable)
        │
        ▼  audio bytes → audio_queue
processing_worker()  [main.py:~547]
  1. STT  →  stt.py  (local Whisper / Deepgram / Sarvam / AssemblyAI)
  2. Fragment merge: _MERGE_WAIT=1.0s (waits for follow-up if question looks incomplete)
  3. _looks_complete check: ≥6 words OR ends with '?' OR confidence ≥0.82
  4. validate_question() — reject filler/noise
  5. handle_question()  →  answer pipeline below
        │
        ▼
handle_question()  [app/services/interview_service.py]
  Priority 1: intro question   → user.self_introduction  (<5ms)
  Priority 2: DB match         → qa_database.find_answer()  (<20ms, Jaccard 0.72)
  Priority 3: answer cache     → answer_cache.get_cached_answer()  (<1ms)
  Priority 4: LLM stream       → llm_client.get_streaming_interview_answer()  (~2–3s Haiku)
        │
        ▼
answer_storage.py  +  event_bus.py
  push_chunk() / push_complete()  → SSE /api/stream  (<1ms)
        │
        ▼
Browser (index.html)  ← SSE chunks rendered live
```

**Cooldown after each answer:** 0.2s (short) / 0.6s (normal) / 1.5s (code answers)

---

## Key Files

| File | Purpose |
|---|---|
| `main.py` | Entry point. `capture_worker` + `processing_worker` threads. Pipeline orchestration. |
| `config.py` | All runtime constants. Sourced from `.env` + `app/core/config_schema.py`. |
| `state.py` | Thread-safe state machine (IDLE→LISTENING→GENERATING→COOLDOWN). Cooldown logic. Latency tracking. |
| `stt.py` | Speech-to-text. Supports local Whisper, Deepgram, Sarvam, AssemblyAI. |
| `llm_client.py` | Claude API client. Streaming answers. Role-based prompt injection. Prompt caching. |
| `qa_database.py` | SQLite Q&A store. Jaccard similarity lookup. 1092+ entries. |
| `answer_storage.py` | In-memory answer state. Thread-safe chunks + complete answer. |
| `event_bus.py` | Zero-latency SSE push. `push_chunk()` / `push_complete()` / `push_question_started()`. |
| `user_manager.py` | User profiles. Resume context builder. `build_resume_context_for_llm()`. |
| `fragment_context.py` | Cross-source fragment merge (voice + extension audio). |
| `question_validator.py` | Reject noise/filler. Normalize questions. |
| `answer_cache.py` | In-memory LRU cache for LLM-generated answers. Max 1000. |

### App Layer

```
app/
  api/routes/
    interview.py    POST /api/ask, GET /api/stream, POST /api/cc_question
    settings.py     GET/POST /api/audio_settings, /api/launch_config, /api/interview_role
    users.py        CRUD /api/users, /api/prepared-questions
    knowledge.py    CRUD /api/qa, /api/save_to_db, /api/search, /api/regenerate
    coding.py       /api/set_llm_model, /api/solve_problem, /api/clear_session
    ops.py          /api/session-info, /api/system/health, /api/answers, /api/logs
    ui.py           All HTML page routes (/, /settings, /monitor, /qa-manager, etc.)
    monitoring.py   /monitor-viewer/, /api/session/predictions
  services/
    interview_service.py   ask_question_payload() — the main answer orchestration
    settings_service.py    update_audio_settings(), update_interview_role(), _persist_env()
    ops_service.py         get_session_info_payload() — includes avg_latency_ms
    user_service.py        activate_user_payload()
    knowledge_service.py   DB CRUD wrappers
  core/
    config_schema.py       RuntimeConfig dataclass — canonical env var contract
    state.py               (same as root state.py)
```

---

## UI Pages

| URL | Template | Purpose |
|---|---|---|
| `/` | `index.html` | Main interview screen. Terminal bar, live answer feed. |
| `/settings` | `settings.html` | All settings: STT, LLM, launch config, coding language, appearance. |
| `/monitor` | `monitor.html` | 2nd screen / phone monitor view. |
| `/users` | `users.html` | User profile management. |
| `/qa-manager` | `qa_manager.html` | Browse/edit Q&A database. |
| `/lookup` | `lookup.html` | Manual keyword search in DB. |
| `/questions` | `questions.html` | Prepared questions list per user. |
| `/ext-users` | `ext_users.html` | Chrome extension user management. |
| `/api-dashboard` | `api_dashboard.html` | API keys, integrations status. |
| `/admin-docs` | `admin_docs.html` | Full project reference (HTML). |
| `/portal/<token>` | `user_portal.html` | Public user portal via token. |
| `/voice` | `voice.html` | Voice test interface. |

---

## Terminal Bar (index.html)

Dark chip bar at top of main screen. Chips update config live via API:

```
$ STT [tiny] [small] [sarvam] [deepgram] | LLM [haiku] [sonnet] | USER [dropdown] | ROLE [gen] [py] [java] [js] [sql] [saas]
     avg ___ms
```

- **STT chips** → POST `/api/audio_settings` `{stt_backend, stt_model}`
- **LLM chips** → POST `/api/set_llm_model` + `/api/launch_config`
- **USER dropdown** → POST `/api/launch_config` `{user_id_override}`
- **ROLE chips** → POST `/api/interview_role` `{role}` — sets coding_language + LLM role context
- **avg ms** — rolling 20-answer latency from `/api/session-info` → `avg_latency_ms`

---

## Configuration Constants

### config.py (edit these to tune defaults)

```python
VAD_PADDING_MS = 200          # End-of-speech padding. 200ms is good; don't go below 150ms.
SILENCE_DEFAULT = 1.2         # Seconds of silence before processing. User can change via settings.
MAX_RECORDING_DURATION = 15.0 # Hard cap on question length.
LLM_MAX_TOKENS_INTERVIEW = 100  # 3 bullet points. Keep small for speed.
LLM_MAX_TOKENS_CODING = 700
VERBOSE = False               # Set True temporarily to see full logs.
LOG_TO_FILE = False           # Writes ~/.drishi/logs/debug.log when True.
INTERVIEW_ROLE = "general"    # Set via /api/interview_role. Persisted to .env.
```

### state.py (cooldown tuning)

```python
COOLDOWN_MIN = 0.2      # Very short answers (<150 chars)
COOLDOWN_DEFAULT = 0.6  # Normal answers
COOLDOWN_MAX = 1.5      # Code answers (+0.8s bonus applied)
```

### main.py (merge window)

```python
_MERGE_WAIT = 1.0         # Wait this long for follow-up fragment before finalizing question
# _looks_complete: ≥6 words OR ends '?' OR confidence ≥0.82
```

### llm_client.py

```python
TEMP_INTERVIEW = 0.15   # Low = deterministic, fast. Don't raise above 0.3.
MODEL = "claude-haiku-4-5-20251001"  # Changed at runtime via /api/set_llm_model
FALLBACK_MODEL = "claude-sonnet-4-6" # Used on overload
client = Anthropic(max_retries=0, timeout=12.0)  # Fail fast
```

---

## API Quick Reference

### Interview
```
POST /api/ask              {"question": "...", "db_only": false}
     → {"answer":"...", "source":"db|cache|llm|intro", "score":0.94}
GET  /api/stream           SSE stream. Events: question_started, chunk, answer, error
POST /api/cc_question      {"text":"..."} — Chrome extension audio text injection
```

### Settings
```
GET  /api/audio_settings
POST /api/audio_settings   {"silence_duration":1.2, "max_duration":15, "stt_backend":"local",
                            "stt_model":"tiny.en", "coding_language":"python"}

GET  /api/launch_config
POST /api/launch_config    {"audio_source":"system|extension", "use_ngrok":false,
                            "llm_model":"haiku|sonnet", "user_id_override":"1"}

GET  /api/interview_role
POST /api/interview_role   {"role":"general|python|java|javascript|sql|saas|system_design"}
                            → also sets coding_language automatically

GET  /api/ip               → {"ip": "192.168.x.x"}
GET  /api/get_jd           → {"text":"..."}
POST /api/save_jd          {"text":"..."}
```

### Knowledge Base
```
GET  /api/qa               ?tag=python&search=decorator&limit=50
POST /api/qa               {"question":"...","answer":"...","tags":"python,oop"}
PUT  /api/qa/<id>
DELETE /api/qa/<id>
POST /api/save_to_db       {"question":"...","answer":"...","source":"interview"}
POST /api/bulk_save_to_db  {"items":[...]}
GET  /api/search           ?q=keyword
POST /api/qa/test          {"question":"..."} → DB lookup test
POST /api/regenerate       {"question":"..."} → force LLM regen + save
```

### Users
```
GET  /api/users
POST /api/users            {"name":"...","role":"...","experience_years":3}
GET  /api/users/<id>
PUT  /api/users/<id>
DELETE /api/users/<id>
POST /api/users/activate/<id>
GET  /api/users/<id>/profile
PATCH /api/users/<id>/profile
GET  /api/users/<id>/resume
```

### Ops / Monitoring
```
GET  /api/session-info     → {db_count, cache_hits%, avg_latency_ms, user_name, stt, llm, ...}
GET  /api/system/health    → {cpu:%, ram:%, stt_status, llm_status}
GET  /api/answers          → current session answer cards
GET  /api/transcribing     → live transcription text
GET  /api/logs             → last 100 debug log lines
POST /api/clear_session    → wipe current session answers
GET  /api/session_export   → download JSON
GET  /api/session_export_md → download Markdown
GET  /health               → {"status":"ok"}
```

### Coding
```
POST /api/set_llm_model    {"model":"haiku|sonnet"}
GET  /api/performance      latency stats
POST /api/solve_problem    {"problem":"...", "language":"python"}
GET  /api/coding_state
GET  /api/latest_code
POST /api/control/start|pause|stop|toggle_mode
```

---

## STT Backends

| Backend | Speed | Accuracy | Cost | Notes |
|---|---|---|---|---|
| `local` + `tiny.en` | ~200ms | Good | Free | Best for fast interviews |
| `local` + `Systran/faster-distil-whisper-small.en` | ~400ms | Best local | Free | Default recommendation |
| `local` + `small.en` | ~800ms | High | Free | Use for difficult accents |
| `deepgram` | ~600ms | Excellent | API key | cloud, `DEEPGRAM_API_KEY` |
| `sarvam` | ~1s | Great for Indian EN | API key | `SARVAM_API_KEY`, `SARVAM_LANGUAGE=en-IN` |
| `assemblyai` | ~2s | Excellent | API key | polling-based, use for post-processing |

STT is set via terminal bar or `/api/audio_settings`. Persisted to `.env`.

---

## LLM Models

| Model | TTFT | Use Case |
|---|---|---|
| `claude-haiku-4-5-20251001` | ~0.8–2s | Live interviews — default |
| `claude-sonnet-4-6` | ~1.5–4s | Complex system design, detailed answers |

Prompt caching active (`cache_control: ephemeral`) — saves ~300–500ms on repeated system prompts.

---

## Interview Roles (terminal bar ROLE chips)

| Chip | Role value | Effect |
|---|---|---|
| `gen` | `general` | No special context |
| `py` | `python` | LLM uses Python for all code. Focus: Django, FastAPI, async. |
| `java` | `java` | LLM uses Java. Focus: Spring Boot, JVM, threading. |
| `js` | `javascript` | LLM uses JavaScript/TypeScript. Focus: React, Node.js. |
| `sql` | `sql` | Always includes SQL examples. Focus: query optimization, indexes. |
| `saas` | `saas` | Focus: multi-tenancy, billing, REST APIs, B2B product. |

Role context is injected into the LLM system prompt. Also sets `coding_language` for DB lookup.

---

## Chrome Extension

Load from: `chrome_extension/` (root directory, NOT a dist/ folder)

```
chrome_extension/
  manifest.json               MV3, loads from root
  background.js               Service worker
  audio_offscreen.js/.html    Web Audio API capture (offscreen page)
  audio_processor_worklet.js  AudioWorklet — 16kHz mono streaming
  popup.html/.js              Extension UI
  monitor_content.js          Injects monitor overlay into meeting pages
  monitor_control_handler.js  Controls monitor overlay
  coder_content.js            LeetCode/HackerRank code interceptor
  typewriter.js               Auto-types answers into coding platforms
  page_bridge.js              Page-level injection bridge
```

Extension sends audio to server via WebSocket/POST. Server URL set in popup.

---

## .env Variables

```bash
ANTHROPIC_API_KEY=sk-ant-...     # Required
DEEPGRAM_API_KEY=...             # Optional — for cloud STT
SARVAM_API_KEY=...               # Optional — for Indian English STT
SARVAM_LANGUAGE=en-IN            # Default en-IN (skip auto-detect, saves ~400ms)

# Persisted by settings UI:
STT_BACKEND=local
STT_MODEL_OVERRIDE=Systran/faster-distil-whisper-small.en
LLM_MODEL_OVERRIDE=claude-haiku-4-5-20251001
SILENCE_DEFAULT=1.2
MAX_RECORDING_DURATION=15.0
AUDIO_SOURCE=system              # system | extension
USE_NGROK=false
USER_ID_OVERRIDE=                # active user ID
CODING_LANGUAGE=python
INTERVIEW_ROLE=general
NGROK_DOMAIN=                    # optional fixed ngrok domain
WEB_PORT=8000
```

---

## Performance Tuning Cheatsheet

### Make it faster:
1. Terminal bar → STT: switch to `tiny` (200ms vs 400ms)
2. Settings → Silence: Fast preset (0.6s)
3. Terminal bar → LLM: `haiku` (fastest)
4. Terminal bar → ROLE: set correct role (better DB hits)
5. Settings → check avg latency meter — green = fast, yellow = balanced, red = slow

### Current tuned values (already applied):
- `VAD_PADDING_MS = 200` (was 400)
- `_MERGE_WAIT = 1.0s` (was 2.5s)
- `COOLDOWN_DEFAULT = 0.6s` (was 0.8s)
- `COOLDOWN_MAX = 1.5s` (was 2.0s)
- `_looks_complete`: ≥6 words (was 8), confidence ≥0.82 (was 0.85)
- `VERBOSE = False`, `LOG_TO_FILE = False`
- CDN scripts `defer` (saves ~2s page load)
- Sarvam `en-IN` default (saves ~400ms auto-detect)
- ngrok `--request-header-add "ngrok-skip-browser-warning: true"`

### Measured pipeline latency:
| Path | Latency |
|---|---|
| Intro question | <5ms |
| DB hit | 13–30ms avg |
| Answer cache (LLM repeat) | <1ms |
| LLM (Haiku, novel question) | 2–3s TTFT, 3–5s full |
| SSE push to browser | <1ms |

---

## Database

SQLite at `~/.drishi/qa_pairs.db` (copied from `qa_pairs.db` at startup).

- 1092+ Q&A pairs tagged by role/domain
- Jaccard similarity threshold: 0.72
- Lookup: `qa_database.find_answer(question, want_code, user_role)`
- Add via: POST `/api/save_to_db` or "Save to DB" button on answer card
- Browse/edit: `/qa-manager`

---

## Common Tasks

### Add a new API endpoint
1. Add route in `app/api/routes/<file>.py`
2. Add logic in `app/services/<file>_service.py`
3. Import service function in route file

### Add a new settings field
1. Add to `config.py` with `os.environ.get()`
2. Add to `app/core/config_schema.py` `RuntimeConfig` dataclass
3. Add save logic in `app/services/settings_service.py` (`_persist_env`)
4. Add API handler in `app/api/routes/settings.py`
5. Add UI in `settings.html`

### Tune the pipeline for a new interview type
1. Terminal bar: set ROLE chip
2. Settings: adjust silence duration (Fast/Normal/Slow preset)
3. Add role-specific Q&A to DB via `/qa-manager`
4. Update `_ROLE_CONTEXT` dict in `llm_client.py` if new role needed

### Debug a slow answer
1. Check `[PERF]` logs in terminal (STT ms, DB ms, LLM TTFT, pipeline total)
2. Settings page → Audio & STT → live latency meter (green/yellow/red)
3. `/api/session-info` → `avg_latency_ms`
4. `/api/system/health` → cpu/ram

### Restart server cleanly
```bash
kill $(fuser 8000/tcp 2>/dev/null); sleep 1
source venv/bin/activate && python3 -W ignore main.py
```

---

## File Structure (abbreviated)

```
Drishi-Pro/
├── main.py                  ← entry point, audio threads
├── config.py                ← all constants
├── state.py                 ← pipeline state machine + latency tracking
├── stt.py                   ← speech-to-text (all backends)
├── llm_client.py            ← Claude API, prompts, streaming
├── qa_database.py           ← SQLite Q&A store
├── answer_storage.py        ← in-memory answer state
├── event_bus.py             ← SSE zero-latency push
├── user_manager.py          ← user profiles + resume context
├── question_validator.py    ← noise/filler rejection
├── answer_cache.py          ← LRU cache for LLM answers
├── fragment_context.py      ← cross-source merge
├── run.sh                   ← startup script
├── .env                     ← secrets + persisted settings
├── qa_pairs.db              ← Q&A database (source)
├── resume.txt               ← default resume
├── app/
│   ├── api/routes/          ← Flask blueprint route handlers
│   ├── services/            ← business logic extracted from routes
│   ├── core/
│   │   └── config_schema.py ← env var contract
│   └── workers/             ← background worker threads
├── web/templates/           ← Jinja2 HTML templates
├── chrome_extension/        ← Chrome extension (load from this folder)
└── tests/                   ← pytest test suite
```
