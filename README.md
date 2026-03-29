# Drishi Enterprise

Real-time AI interview assistant. Listens to live interview audio, transcribes questions, and instantly serves answers from a role-filtered Q&A database — or generates them with Claude when no match is found. Built for multi-candidate sessions with per-user isolation.

---

## How It Works

```
[Interview Audio / Chrome Extension]
           │  System audio / WebRTC / WebSocket
           ▼
[main.py — Audio threads :8000]
           │
           ├─ VAD (webrtcvad) — 20ms chunks, 1.2s silence threshold
           │
           ├─ STT — Whisper local / Sarvam AI / Deepgram / AssemblyAI
           │
           ├─ Validator — reject noise, filler, short phrases
           │
           ├─ Fragment merge (_MERGE_WAIT = 1.0s)
           │
           ├─ Priority 1:  Self-intro       → user.self_introduction     (<5ms)
           ├─ Priority 2:  DB match         → qa_database.find_answer()   (13–30ms, Jaccard ≥0.72)
           ├─ Priority 2b: Semantic search  → semantic_search.py          (~5ms, cosine ≥0.80)
           ├─ Priority 3:  Answer cache     → LRU in-memory               (<1ms)
           └─ Priority 4:  LLM stream       → Claude Haiku/Sonnet         (~2–3s TTFT)
                          │
                          ▼               ┌─ ElevenLabs TTS → /ws/tts → browser audio
           [SSE /api/stream  →  Flask UI / Phone Monitor]
                                          └─ per-session conversation history (5 pairs)
```

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo>
cd Drishi
cp .env.example .env
# Fill in ANTHROPIC_API_KEY (required) and optional keys
```

### 2. Configure PostgreSQL

`./run.sh` requires `DATABASE_URL` in `.env`. Start a local container if you don't have one:

```bash
docker run -d --name drishi-pg \
  -e POSTGRES_DB=drishi -e POSTGRES_USER=drishi -e POSTGRES_PASSWORD=drishi \
  -p 5434:5432 postgres:14

# Add to .env:
DATABASE_URL=postgresql://drishi:drishi@localhost:5434/drishi
```

`run.sh` auto-starts `drishi-pg` on subsequent runs if Docker is available.

### 3. Start everything

```bash
./run.sh
```

Access mode is read from `.env` at startup — no interactive prompt.
Toggle ngrok on/off via **Settings → Launch Config** (persisted to `.env`).

Or manually:

```bash
source venv/bin/activate
python3 -W ignore main.py        # server on :8000
cd react_ui && npm run build     # build React UI (production)
```

### 4. Open in browser

| Interface | URL |
|---|---|
| React UI | http://localhost:8000/react/ |
| Flask classic UI | http://localhost:8000 |
| Admin Docs | http://localhost:8000/admin-docs |
| Settings | http://localhost:8000/react/#/settings |

---

## Performance (Measured)

| Path | Latency |
|---|---|
| Intro question | <5ms |
| DB hit (warm) | 13–30ms avg |
| Answer cache (repeated LLM) | <1ms |
| LLM Haiku (novel question) | ~2–3s TTFT, 3–5s full |
| SSE push to browser | <1ms |

**Benchmark (30 questions across all roles):**

| Metric | Result |
|---|---|
| DB/cache hit rate | 77% (23/30) |
| Average latency (all paths) | 13ms |
| Average DB similarity score | 0.99 |
| LLM fallbacks | 23% |
| DB entries | 1,264+ |
| Unique tags | 163 |

**Top Q&A coverage by tag:**

| Tag | Entries |
|---|---|
| linux | 348 |
| interview | 347 |
| sql | 327 |
| python | 301 |
| devops | 195 |
| django | 132 |
| telecom | 129 |
| production-support | 111 |
| aws | 91 |

---

## User Scenarios Tested

### Venkata — Python/Django Backend (3 yrs)
Role: `python` | Domain: Backend API Engineering

| Question | Source | Score | Time |
|---|---|---|---|
| Tell me about yourself | intro | — | 23ms |
| What is Django middleware? | db | 1.00 | 38ms |
| How do you handle DB migrations? | db | 1.00 | 57ms |
| What is FastAPI dependency injection? | db | 1.00 | 83ms |
| What are Python data types? | db | 1.00 | 11ms |
| What is the difference between list and tuple? | db | 1.00 | 9ms |
| Explain Celery task queues | llm | — | ~2s |

### Tejaswini — Production Support / Investment Banking (6 yrs)
Role: `general` | Domain: L1/L2 Trading Infra

| Question | Source | Score | Time |
|---|---|---|---|
| Tell me about yourself | intro | — | 8ms |
| How do you handle a P1 incident? | db | 1.00 | 48ms |
| ITRS Geneos monitoring experience | db | 0.75 | 58ms |
| SQL query for duplicate records | db | 0.80 | 57ms |
| What is RCA? | db | 1.00 | 34ms |
| How do you check CPU usage in Linux? | db | 1.00 | 14ms |
| AutoSys job failure handling | llm | — | ~2s |

### Balaji — Telecom IMS Engineer (5 yrs)
Role: `general` | Domain: SIP / SS7 / Diameter / IMS

| Question | Source | Score | Time |
|---|---|---|---|
| Tell me about yourself | intro | — | 2ms |
| Explain SIP registration flow | db | 1.00 | 15ms |
| What is HSS in IMS? | db | 0.67 | 15ms |
| Explain Diameter protocol | db | 1.00 | 13ms |
| What is P-CSCF and S-CSCF? | db | 1.00 | 18ms |
| What is the OSI model? | llm | — | ~2s |

### Ravi — Java / Spring Boot Developer (4 yrs)
Role: `java` | Domain: Enterprise Backend

| Question | Source | Score | Time |
|---|---|---|---|
| Tell me about yourself | intro | — | 5ms |
| Explain Java garbage collection | db | 1.00 | 13ms |
| What is Spring Boot auto-configuration? | db | 1.00 | 20ms |
| How does HashMap work in Java? | db | 1.00 | 11ms |
| What is the difference between abstract class and interface? | db | 1.00 | 8ms |

### Priya — JavaScript / React Developer (2 yrs)
Role: `javascript` | Domain: Frontend / Full-Stack

| Question | Source | Score | Time |
|---|---|---|---|
| Tell me about yourself | intro | — | 3ms |
| What is event loop in JavaScript? | db | 1.00 | 12ms |
| How does a SQL JOIN work? | db | 1.00 | 9ms |
| What is React virtual DOM? | db | 1.00 | 15ms |

---

## Question Validator

The validator rejects non-question audio before it hits the pipeline:

| Rejection reason | Example |
|---|---|
| `too_short` | "hi", "ok" |
| `too_vague` | "What is caching?" (single word topic) |
| `noise` / `filler` | "uh yeah so" |
| `non_it_question` | "Explain the concept of the pebble as a crimson" |
| `chemistry/science` | "What are the arsenic reaction compounds?" |
| `clearly_non_it` | "Explain photosynthesis process" |

Questions must be ≥6 words OR end with `?` OR have confidence ≥0.82 to pass.

The validator uses an `_IT_ADJACENT` vocabulary set (400+ terms) covering Python, Java, JavaScript, SQL, Linux, DevOps, Kubernetes, Telecom (SIP/IMS/SS7), AutoSys, Django, production support, and HR/behavioral keywords. Any question containing at least one IT-adjacent term passes; all others are rejected as non-IT noise.

Plural forms are handled correctly: "microservices", "services", "architectures", "communicate", "frameworks", "components" all pass without needing exact singular match.

---

## Interview Roles

Set from the terminal bar (Dashboard) or `POST /api/interview_role`.
Setting a role also updates `CODING_LANGUAGE` automatically.

| Chip | Role | LLM focus | DB filter |
|---|---|---|---|
| `gen` | general | No special context | all tags |
| `py` | python | Django, FastAPI, async | python, django |
| `java` | java | Spring Boot, JVM | java |
| `js` | javascript | React, Node.js, TypeScript | javascript |
| `sql` | sql | Query optimization, indexes | sql |
| `saas` | saas | Multi-tenancy, billing, REST | saas |
| `devops` | devops | Docker, K8s, CI/CD, Terraform | devops |
| `prod` | production_support | Incident management, RCA, monitoring | production-support |
| `telecom` | telecom | SIP, IMS, SS7, Diameter, VoLTE | telecom |

---

## Interview Round Switcher

Set from the terminal bar `[HR][TECH][DESIGN][CODE]` chips or `POST /api/interview_round`.
Persisted to `.env`. Adjusts LLM token budget, temperature, and answer style per round.

| Chip | Round | Tokens | Temp | Style |
|---|---|---|---|---|
| `HR` | hr | 80 | 0.30 | Conversational, first-person, STAR method, ≤80 words |
| `TECH` | tech | 100 (default) | 0.15 | Bullet points — default behaviour |
| `DESIGN` | design | 500 | 0.20 | Architecture overview + trade-offs + scalability |
| `CODE` | code | 700 | 0.15 | Code-first answers |

---

## STT Backends

| Backend | Model | Speed | Notes |
|---|---|---|---|
| `local` | `tiny.en` | ~200ms | Fast, good accuracy |
| `local` | `Systran/faster-distil-whisper-small.en` | ~400ms | Default, best local accuracy |
| `sarvam` | `saarika:v2.5` | ~1s | Great for Indian English speech; not recommended for system audio playback |
| `deepgram` | `nova-3` | ~600ms | Cloud, requires `DEEPGRAM_API_KEY` |
| `assemblyai` | — | ~2s | Highest accuracy, polling-based |

All local Whisper models use a domain-specific `initial_prompt` with 200+ IT/telecom terms to bias transcription toward technical vocabulary (Python, Java, SQL, SIP, IMS, SS7, Diameter, VoLTE, etc.). Post-transcription corrections fix common STT mishearings (e.g. "s i p" → "SIP", "vol t e" → "VoLTE").

---

## LLM Models

| Model | TTFT | Use case |
|---|---|---|
| `claude-haiku-4-5-20251001` | 0.8–2s | Default — live interviews |
| `claude-sonnet-4-6` | 1.5–4s | System design, detailed answers |

Prompt caching active on repeated system prompts — saves ~300–500ms.

---

## React UI Pages

Vite SPA built with React 18 + React Router v6. Served at `/react/` by Flask in production.

| Route | Page | Description |
|---|---|---|
| `/` | Interview | Live answer feed, terminal bar (STT/LLM/User/Role chips), SSE stream |
| `/monitor` | Monitor | Full-screen phone/tablet view |
| `/profiles` | Profiles | User profile management — create, activate, delete |
| `/questions` | Questions | Browse Q&A DB in table view, filter by type, search |
| `/lookup` | Keyword Lookup | Click any keyword to see all matching Q&A pairs |
| `/settings` | Settings | Speed presets, STT backend, silence threshold, audio source |
| `/qa-manager` | QA Manager | Add/edit Q&A pairs, search, bulk operations |
| `/api-keys` | API Keys | Anthropic, Deepgram, Sarvam, AssemblyAI key status |
| `/ext-users` | Ext Users | Chrome extension user management |

### Build for production

```bash
cd react_ui
npm run build         # outputs to web/static/react/
```

Access at `http://localhost:8000/react/`.

---

## Chrome Extension

### Setup

1. Open `chrome://extensions/` in Chrome
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked** → select `/path/to/Drishi/chrome_extension/`
4. Click the **Drishi Enterprise** icon in the toolbar
5. Extension popup → **Settings** tab → set **Server URL**:
   - Local: `http://localhost:8000`
   - Remote (ngrok): `https://your-domain.ngrok-free.app`
6. **Login** tab → enter your user token → Sign In

### Usage

1. Start the server: `./run.sh`
2. Navigate to your interview tab (Google Meet / Zoom / Teams / YouTube / any URL)
3. Click the extension icon **while on that tab** → popup opens
4. Click **▶ Start Stream** → audio capture begins from the active tab
5. Answers appear live at `/react/` or on the phone monitor

### Audio Capture — How It Works

The extension uses `chrome.tabCapture.getMediaStreamId({})` called from the **popup context** (no `targetTabId`). This captures whatever tab was active when the popup was opened — no tab switching, no redirects.

- Tab audio = only what you **hear** (remote/interviewer voice)
- Your own microphone input is never part of tab audio playback
- Sarvam STT (client-side) or raw PCM stream to server STT
- WebSocket connect timeout: **10s** (increased from 4s — accounts for ngrok latency)
- **POST fallback**: if WebSocket fails or times out, Sarvam transcripts are sent via `POST /api/cc_question` instead — no audio is lost

### Extension Features

| Feature | How it works |
|---|---|
| Audio capture | `tabCapture` → offscreen AudioWorklet (16kHz mono PCM) → WebSocket `/ws/audio` |
| Sarvam STT | Client-side: silence detection → WAV → Sarvam API → text to `/api/cc_question` |
| Raw PCM mode | Server-side STT: PCM-16 binary streamed directly to `/ws/audio` |
| WS POST fallback | If WebSocket unavailable, Sarvam transcript POSTed to `/api/cc_question` |
| Meeting captions | Per-element delta tracking (not full-text snapshot) → filters own speech → `/api/cc_question` |
| Remote logging | `rlog()` in background/offscreen: extension logs forwarded to server terminal via `POST /api/ext/log` |
| Monitor overlay | Floating answer overlay injected into meeting pages |
| Code interceptor | Detects coding problems on LeetCode, HackerRank, Codility, etc. |
| Typewriter | Auto-types generated solutions into coding platform editors |
| Web Speech TTS | `tts` permission — reads answers aloud in browser via Web Speech API |

### Supported Meeting Platforms (audio capture)

- Google Meet, Microsoft Teams, Zoom, Webex
- YouTube (any URL), and any normal `http/https` tab as fallback

### Supported Coding Platforms (auto-intercept)

- LeetCode, HackerRank, Codility, CodeSignal, Codewars
- Replit, Google Colab, Programiz

### CSP Compliance (MV3)

All event handlers use `addEventListener` — no inline `onclick=` attributes.
Popup login, configure link, and settings navigation are all wired in `popup.js`.

---

## Flask UI Pages (Classic)

| URL | Purpose |
|---|---|
| `/` | Main dashboard (index.html) |
| `/monitor` | Global monitor view — SSE answer feed |
| `/stream` | **Phone mic / loopback audio capture** — streams to server via WebSocket |
| `/settings` | Settings page |
| `/qa-manager` | QA database manager |
| `/ext-users` | Extension user admin |
| `/users` | User profile manager |
| `/lookup` | Manual keyword search |
| `/voice` | Voice test interface |
| `/api-dashboard` | API key status |
| `/admin-docs` | Full project reference |
| `/copilot` | Co-pilot view — friend watches live answers and sends hints |

### `/stream` — Zero-Indicator Audio Capture

A standalone capture page for remote candidate use. Uses `getUserMedia` on a selected audio device (including PulseAudio loopback "Monitor" devices) — **no tab capture, no recording indicator**.

**Linux one-time setup:**
```bash
pactl load-module module-loopback latency_msec=5
# Permanent:
echo "load-module module-loopback latency_msec=5" | sudo tee -a /etc/pulse/default.pa
```

**Usage:**
1. Open `/stream` in a background tab or install as PWA (standalone window)
2. Select **"Monitor of …"** device (auto-highlighted) → Start Capture
3. Streams interviewer audio (Chrome output only) → Sarvam STT → answers

**Install as PWA** (no tab bar, no address bar, fully standalone):
- Open `/stream` → click install icon in Chrome address bar → opens as app window

---

## User Switching & Intro Isolation

Switch between candidate profiles instantly from the terminal bar USER dropdown or via API.

```bash
POST /api/launch_config  {"user_id_override": "4"}
```

- **Intro isolation**: "Tell me about yourself" always returns the **current** user's intro — never a cached previous user's intro
- On switch: intro LRU entries auto-evicted, session dedup bypassed for intro questions
- Intro is never stored in answer cache — always freshly generated from active profile
- LLM context (skills, experience, role) updates instantly on switch

---

## Multi-User / Chrome Extension

### Admin setup

1. Open `/ext-users`
2. Click **+ New user** → fill name, token, role, coding language
3. Copy the **token** → share with candidate
4. Copy the **monitor URL** → candidate opens on their phone

### User setup (candidate)

1. Load `chrome_extension/` as unpacked extension in Chrome
2. Extension popup → **Settings** tab → paste **Server URL**
3. **Login** tab → paste **User Token** → Sign In
4. Navigate to interview tab → open popup → click **▶ Start Stream**
5. Answers appear on their monitor only

### Isolation guarantees

- Each token has its own `UserAnswerStorage`
- DB lookups filtered by `db_user_id` (role-based answer sets)
- LLM prompt includes user's resume + role context
- No answer cross-contamination between tokens

---

## Remote Candidate Setup (Hyderabad ↔ Bangalore)

For a remote candidate connecting to a server in another city via ngrok.

**Server (Bangalore):** runs `./run.sh` with `USE_NGROK=true` and fixed `NGROK_DOMAIN`.

**Candidate (Hyderabad):** has three options depending on constraints:

| Method | Indicators | Cost | Reliability |
|---|---|---|---|
| Chrome extension (tab capture) | Tab dot 🔴 (only if screen shared) | ₹0 | 90% |
| `/stream` + PulseAudio loopback | None | ₹0 | 85% |
| 3.5mm TRRS audio splitter | None | ₹150 | **100%** |

**Recommended (100%):** 3.5mm TRRS splitter → laptop headphone jack → one end earphones, other end phone mic-in → phone opens `/stream` → streams to server.

**Phone as monitor:** Open `https://<ngrok-domain>/monitor` on phone → answers appear in real-time.

**Phone mic capture on monitor page:** Tap **🎤 MIC OFF** button on `/monitor` to stream phone mic audio directly to server. Use with laptop in speaker mode (no earphones). Hold **🤫 HOLD WHILE SPEAKING** while answering to prevent your own voice from being transcribed.

See `REMOTE_SETUP_OPTIONS.md` for full comparison, decision guide, and hardware recommendations.

---

## Database

- **1,264+ Q&A pairs** across 163 tags
- Jaccard similarity threshold: 0.72
- Lookup: `qa_database.find_answer(question, want_code, user_role)`
- Add via `/react/#/qa-manager` or `POST /api/save_to_db`

### PostgreSQL (required for `run.sh`)

`./run.sh` requires `DATABASE_URL` set in `.env` and exits with an error if it is missing.
The Docker container (`drishi-pg`) is auto-started on each `run.sh` run if it isn't already running.

```bash
DATABASE_URL=postgresql://drishi:drishi@localhost:5434/drishi
```

Uses `pg8000` (pure Python driver — no C extension, no ssl conflicts).

### SQLite fallback

Running `python3 -W ignore main.py` directly (without `run.sh`) still falls back to
`~/.drishi/qa_pairs.db` when `DATABASE_URL` is not set — useful for development.

---

## Configuration (`.env`)

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# STT (optional cloud backends)
DEEPGRAM_API_KEY=...
SARVAM_API_KEY=...
SARVAM_LANGUAGE=en-IN       # Skip auto-detect, saves ~400ms

# Database
DATABASE_URL=postgresql://drishi:drishi@localhost:5434/drishi   # optional

# Persisted by settings UI
STT_BACKEND=local
STT_MODEL_OVERRIDE=Systran/faster-distil-whisper-small.en
LLM_MODEL_OVERRIDE=claude-haiku-4-5-20251001
SILENCE_DEFAULT=1.2
MAX_RECORDING_DURATION=15.0
AUDIO_SOURCE=system          # system | extension
USE_NGROK=false
NGROK_DOMAIN=                # optional fixed cloudflare/ngrok domain
USER_ID_OVERRIDE=            # active user ID
CODING_LANGUAGE=python
INTERVIEW_ROLE=general
INTERVIEW_ROUND=tech         # tech | hr | design | code
WEB_PORT=8000

# ElevenLabs TTS earpiece (optional)
ELEVENLABS_ENABLED=false
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM

# Async task queue (optional — requires Redis)
CELERY_ENABLED=false
REDIS_URL=redis://localhost:6379/0
```

---

## Project Structure

```
Drishi/
├── main.py                  ← entry point — audio capture + processing threads
├── config.py                ← all runtime constants
├── state.py                 ← pipeline state machine + latency tracking
├── stt.py                   ← STT (Whisper / Sarvam / Deepgram / AssemblyAI)
├── llm_client.py            ← Claude API, streaming, per-session history, round params
├── qa_database.py           ← SQLite/PG Q&A store, Jaccard lookup
├── semantic_search.py       ← sentence-transformers cosine fallback (all-MiniLM-L6-v2)
├── tts_client.py            ← ElevenLabs TTS streaming (flag-gated)
├── celery_app.py            ← async LLM task queue via Redis (flag-gated)
├── db_backend.py            ← PostgreSQL adapter (pg8000, drop-in sqlite3 compat)
├── answer_storage.py        ← in-memory answer state (thread-safe)
├── event_bus.py             ← SSE zero-latency push
├── user_manager.py          ← user profiles + resume context
├── question_validator.py    ← noise/filler rejection, normalization
├── answer_cache.py          ← LRU cache for LLM answers (max 1000)
├── fragment_context.py      ← cross-source fragment merge
├── run.sh                   ← startup script (venv, deps, cloudflare/ngrok, redis, server)
├── .env                     ← secrets + persisted settings
├── qa_pairs.db              ← Q&A database (source)
│
├── app/
│   ├── api/routes/          ← Flask blueprint handlers
│   │   ├── interview.py     ← POST /api/ask, GET /api/stream, /api/interview_tips, /api/prep_questions
│   │   ├── settings.py      ← audio_settings, launch_config, interview_role, interview_round, jd_configure
│   │   ├── users.py         ← CRUD /api/users
│   │   ├── knowledge.py     ← CRUD /api/qa, /api/save_to_db
│   │   ├── coding.py        ← /api/set_llm_model, /api/solve_problem
│   │   ├── ops.py           ← /api/session-info, /api/answers, /api/logs
│   │   ├── monitoring.py    ← /monitor-viewer/, /api/session/predictions
│   │   └── ui.py            ← all HTML routes
│   ├── services/
│   │   ├── interview_service.py  ← ask pipeline (DB → semantic → cache → LLM)
│   │   ├── jd_service.py         ← JD Auto-Configure (analyze + seed Q&A)
│   │   ├── settings_service.py   ← update_audio/launch/role/round settings
│   │   ├── knowledge_service.py  ← DB CRUD + semantic index update
│   │   └── user_service.py       ← user profile management
│   └── core/
│       └── config_schema.py ← RuntimeConfig dataclass
│
├── react_ui/                ← React 18 + Vite SPA
│   ├── src/
│   │   ├── pages/           ← Interview, Monitor, Profiles, Questions,
│   │   │                       KeywordLookup, Settings, QAManager,
│   │   │                       APIKeys, ExtUsers
│   │   ├── components/      ← Sidebar, TopBar, Layout, AnswerCard,
│   │   │                       TerminalBar, Toast
│   │   └── hooks/           ← useSSE, useAnswers, useApi, useSessionInfo
│   └── vite.config.js       ← proxy /api→:8000, build→web/static/react/
│
├── web/
│   ├── server.py            ← Flask app factory
│   ├── static/
│   │   ├── react/           ← production React build (npm run build)
│   │   ├── js/
│   │   │   ├── drishi-stream.js       ← SSE client
│   │   │   └── pcm_worklet.js         ← AudioWorklet for /stream + /monitor mic
│   │   └── stream_manifest.json       ← PWA manifest for /stream page
│   └── templates/
│       ├── monitor.html     ← phone monitor (🎤 mic capture + 🤫 mute button)
│       ├── stream_audio.html ← zero-indicator loopback audio capture page
│       └── ...              ← other Jinja2 pages
│
├── chrome_extension/        ← Load unpacked from this folder
│   ├── manifest.json        ← MV3, tabCapture + offscreen permissions
│   ├── popup.html / popup.js ← CSP-compliant (no inline onclick)
│   ├── background.js        ← service worker (audio start/stop handlers)
│   ├── audio_offscreen.js / audio_offscreen.html ← AudioWorklet capture
│   ├── audio_processor_worklet.js  ← 16kHz mono, 64ms chunks
│   ├── meeting_captions.js  ← DOM caption reader (Meet/Teams/Zoom)
│   ├── monitor_content.js   ← meeting page overlay
│   ├── coder_content.js     ← LeetCode/HackerRank interceptor
│   └── typewriter.js        ← auto-types answers into editors
│
└── tests/                   ← pytest test suite
```

---

## API Reference

### Interview

```
POST /api/ask              {"question":"...", "db_only":false}
                           → {"answer":"...", "source":"db|cache|llm|intro", "score":0.94}
                           Optional: "quick_mode":true  → skip DB, compact LLM, 280 tokens, guaranteed code block

GET  /api/stream           SSE — events: question_started, chunk, answer, error
POST /api/cc_question      {"text":"..."} — Chrome extension text injection

GET  /api/interview_tips   ?role=python&round=tech
                           → {role, round, round_tips: [...6 round tips], role_tips: [...4 role tips], total: 10}
                           Roles: general|python|java|javascript|sql|saas|devops|production_support|telecom
                           Rounds: tech|hr|design|code

GET  /api/prep_questions   ?role=python&tag=django&limit=20
                           → {role, tag_filter, count, questions: [{id, question, tags, has_code, hit_count}]}
                           Sorted by hit_count (most-asked first). Default limit=20, max=50.

GET  /api/get_answer_by_index?index=N   → code block #N (flat index across all answers)
                                           {"found":true, "index":1, "total":2, "question":"...", "code":"..."}
```

### Settings

```
GET  /api/audio_settings
POST /api/audio_settings   {"silence_duration":1.2, "stt_backend":"local", "stt_model":"tiny.en"}

GET  /api/launch_config
POST /api/launch_config    {"llm_model":"haiku|sonnet", "user_id_override":"1",
                            "elevenlabs_enabled":true}

GET  /api/interview_role
POST /api/interview_role   {"role":"general|python|java|javascript|sql|saas|devops|production_support|telecom"}
                           → also sets coding_language automatically

GET  /api/interview_round
POST /api/interview_round  {"round":"tech|hr|design|code"}
                           → adjusts LLM token budget, temperature, answer style

POST /api/jd_configure     {"text":"<full job description>"}
                           → analyzes JD, applies role+round, seeds ~20 Q&A in background
                           → returns {role, company, skills, round_hint, experience_years,
                                      settings_applied, seeding:"started"}

WS   /ws/tts               Send: {"text":"...", "voice_id":"optional"}
                           Receive: binary MP3 chunks → {"event":"done"}
```

### Knowledge Base

```
GET    /api/qa             ?tag=python&search=decorator&limit=50
POST   /api/qa             {"question":"...","answer_theory":"...","answer_coding":"...","tags":"python"}
PUT    /api/qa/<id>
DELETE /api/qa/<id>
GET    /api/qa/tags        → {"tag": count, ...}
POST   /api/save_to_db     {"question":"...","answer":"..."}
POST   /api/search         {"query":"..."}
```

### Ops

```
GET  /api/session-info     → {db_count, cache_hits%, avg_latency_ms, user_name, stt, llm, mode}
GET  /api/system/health    → {cpu:%, ram:%, stt_status, llm_status}
GET  /api/answers          → current session answer cards
GET  /api/transcribing     → live transcription text
GET  /api/logs             → last 100 debug log lines
POST /api/clear_session
GET  /api/session_export   → JSON download
GET  /api/session_export_md → Markdown download
```

### Users

```
GET    /api/users
POST   /api/users          {"name":"...","role":"...","experience_years":3}
GET    /api/users/<id>
PUT    /api/users/<id>
DELETE /api/users/<id>
POST   /api/users/activate/<id>
GET    /api/users/<id>/profile
PATCH  /api/users/<id>/profile
```

---

## Semantic Search

When Jaccard similarity finds no match (score < 0.72), the pipeline falls back to semantic search before hitting the LLM:

- Model: `sentence-transformers/all-MiniLM-L6-v2` (384-dim, ~22MB)
- Threshold: cosine similarity ≥ 0.80
- Index built in background at startup — no latency impact on server start
- Newly inserted Q&A rows are embedded and appended automatically

**Install** (optional — server runs without it, graceful no-op if missing):
```bash
pip install sentence-transformers
```

---

## JD Auto-Configure

Paste a job description and let the LLM configure the session automatically.

**Via Settings → JD Configure tab:**
1. Paste the full JD → click **Configure from JD**
2. LLM extracts: role, company, top 10 skills, round hint, experience
3. `INTERVIEW_ROLE` and `INTERVIEW_ROUND` applied immediately
4. ~20 targeted Q&A pairs seeded into DB in the background

**Via API:**
```bash
POST /api/jd_configure  {"text": "<full job description>"}
```

Response:
```json
{
  "role": "python",
  "company": "Acme Corp",
  "skills": ["django", "fastapi", "postgresql", "redis"],
  "round_hint": "tech",
  "experience_years": 4,
  "settings_applied": {"interview_role": "python", "interview_round": "tech", "coding_language": "python"},
  "seeding": "started"
}
```

---

## ElevenLabs TTS Earpiece Mode

Stream answers as speech to your earpiece via the browser.

**Setup:**
1. Get an API key at [elevenlabs.io](https://elevenlabs.io) (free tier available)
2. Add to `.env`: `ELEVENLABS_API_KEY=...`
3. Settings → AI/Model → **ElevenLabs TTS** toggle → Enable
4. Click **Test TTS** to verify
5. Every subsequent complete answer streams automatically via `/ws/tts`

**How it works:** `eleven_turbo_v2` model, WebSocket binary MP3 chunks → `Audio()` browser playback. Markdown stripped before sending. Adds ~0.5s audio latency.

---

## Conversation History (Per-Session)

Each browser session gets its own rolling conversation history (last 5 Q&A pairs). This lets the LLM handle natural follow-up questions:

- "Can you explain that again with more detail?" — LLM has context of what "that" refers to
- Follow-up code questions don't need to re-state the problem

History is keyed by `session_id` — different browser tabs/users are isolated.
`POST /api/clear_session` resets history for the current session.

---

## Deployment

### Local

```bash
./run.sh
```

### Tunnel (global access — Cloudflare or ngrok)

`run.sh` tries Cloudflare Tunnel first (free, no auth, stable), falls back to ngrok:

```bash
# In .env
USE_NGROK=true
NGROK_DOMAIN=your-domain.ngrok-free.app   # optional fixed ngrok domain
```

**Cloudflare Tunnel (recommended):**
```bash
# Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
sudo apt install cloudflared     # or brew install cloudflare/cloudflare/cloudflared
```

Restart the server — tunnel URL is shown in startup output.

### Async LLM (Celery + Redis — optional)

For high-concurrency deployments:

```bash
# Install
pip install celery redis

# .env
CELERY_ENABLED=true
REDIS_URL=redis://localhost:6379/0

# run.sh auto-starts Redis (Docker) and Celery worker when CELERY_ENABLED=true
```

When `CELERY_ENABLED=false` (default), all LLM calls run synchronously — no change to behaviour.

### Production (Flask serves React)

```bash
cd react_ui && npm run build    # builds to web/static/react/
python3 -W ignore main.py       # serves React at /react/*
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ld.so` assertion crash on startup | libssl3 bug — `run.sh` auto-patches `/tmp/libssl.so.3` via ELF relocation fix |
| `Fatal Python error: Failed to import the site module` on startup | Corrupted `functools.cpython-310.pyc`. Fix: `sudo rm /usr/lib/python3.10/__pycache__/functools.cpython-310.pyc`. Workaround (no sudo): `run.sh` sets `PYTHONPATH=~/.drishi/pyfix` with a clean copy. |
| `Error processing line 1 of google_generativeai-*.pth` | Mixed Python version .pth file. Fix: `rm ~/.local/lib/python3.10/site-packages/google_generativeai-*-py3.13-nspkg.pth` |
| Flask won't start — psycopg2 error | `sudo rm -rf venv/lib/python3.10/site-packages/psycopg2*` |
| Port 8000 already in use | `run.sh` auto-stops Docker containers; or `kill $(fuser 8000/tcp)` |
| ngrok: session already active | `run.sh` runs `pkill -x ngrok` before starting; or kill manually |
| Extension shows "connection failed" | Check Server URL in popup — must match running server or ngrok URL |
| Answers not appearing on user monitor | Verify token in extension matches `/ext-users` token |
| Question rejected: too_vague | Ask the full question (≥6 words) |
| Question rejected: non_it_question | Ensure question contains at least one recognizable IT term |
| DB answers not returning | Check `user_id_override` in launch_config matches active profile |
| React blank page | Ensure `npm run build` was run: `cd react_ui && npm run build` |
| STT not transcribing | Check `stt_status` in `/api/system/health` — restart server if "Error" |
| High latency (>3s avg) | Switch STT to `tiny`, LLM to `haiku`, lower silence threshold |
| STT garbling YouTube audio | Switch from Sarvam to `local` (Whisper) — Sarvam is tuned for speech not playback audio |
| Audio backend shows `parec` | Check `professional_audio.py` — `sounddevice` is now preferred; install `python3-sounddevice` if missing |
| PostgreSQL not connecting | Ensure `DATABASE_URL` in `.env` and `docker ps` shows `drishi-pg` running |

---

## Focus Mode (Interview Screen)

Press `F` on the main dashboard (`/`) to enter **Focus Mode** — fullscreen Q&A feed with no distractions.

### What it does

- Hides sidebar, topbar, terminal bar, bottom bar — only answers visible
- Latest answer always on top, auto-scrolls
- Card body **blurs while streaming** (unblurs on complete — prevents interviewer seeing half-answers)
- After each answer, input auto-refocuses for immediate next keyword

### Focus Ask Bar

A floating dark bar at the bottom of the screen for instant keyword-driven answers:

```
[ decorator ] [ palindrome ] [ fibonacci ] [ generator ] [ lambda ] [ closure ] [ args/kwargs ] [ singleton ]
  ________________________________________    _________
 |  Type: decorator, palindrome...        |  |  Ask ↵  |
```

- Type a **keyword** → expands to a full question automatically
- **Ghost text autocomplete**: type `dec` → see `orator` in green, press **Tab** to complete
- Press **Enter** or click Ask → previous answers cleared, LLM generates clean code answer
- Input automatically cleared and re-focused after each answer

### Quick Ask (quick_mode)

Ask bar uses `quick_mode: true` which:
- **Skips the DB** — goes straight to LLM with a compact 280-token prompt
- Always returns a **code block** in proper Python format
- ~1–2s response time

API usage:
```
POST /api/ask  {"question":"What are decorators?", "quick_mode":true}
```

### Keyword expansion

50+ keywords mapped to full questions. Examples:

| You type | Question sent to LLM |
|---|---|
| `decorator` | What are decorators in Python? Show a short example |
| `fibonacci` | Write a Python function for fibonacci sequence |
| `async` | How does async/await work in Python? |
| `join` | How do SQL JOINs work? Show examples |
| `promise` | What is a Promise in JavaScript? |

Short inputs (≤3 words, no `?`) auto-get: _"What is X in Python? Give a short explanation and code example"_

### Role-aware chips

Chips in the ask bar update automatically when you switch role in the terminal bar:

| Role | Chips shown |
|---|---|
| `python` | decorator, generator, *args/**kwargs, list comprehension, asyncio, dataclass |
| `sql` | inner join, group by, index, subquery, window function, explain |
| `javascript` | promise, closure, async/await, prototype, event loop, arrow function |
| `java` | interface, abstract class, generics, streams, spring boot, thread |
| `saas` | multi-tenancy, rate limiting, jwt, webhook, idempotency, soft delete |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `F` | Toggle focus mode |
| `Esc` | Exit focus mode |
| `/` | Jump to ask input (focus mode) |
| `Tab` | Accept ghost text suggestion |
| `Enter` | Submit ask |
| `Ctrl+Alt+1` – `Ctrl+Alt+9` | Silently fetch + auto-type code #1–9 into active coding editor |
| `Ctrl+Alt+Enter` | Invisible solve: capture problem → generate → auto-type |

---

## Code Tokens #N — Stealth Coding Mode

Every code block gets an invisible `#N` index used for silent auto-typing into any coding editor.
**Zero visible notifications to the interviewer at any point.**

### Triggers

| Trigger | Action |
|---|---|
| Type `#N` + Enter in editor | Ghost trigger: deletes text, silently auto-types code #N |
| `Ctrl+Alt+N` (1–9) | Silent fetch + type code N without touching editor |
| `Ctrl+Alt+Enter` | Invisible: capture problem from page → generate solution → auto-type |

### Features

- Supports `#1000+` — any index works for long sessions
- Language auto-detected from editor boilerplate (Python / Java / JavaScript / C++)
- **HackerRank**: captures all sections (statement + I/O + constraints + examples)
- **LeetCode**: captures title + full description
- **Programiz / CoderPad / Replit**: works via ghost trigger or `Ctrl+Alt+N`
- All status and errors go to browser console only — nothing visible to interviewer

### Token assignment

- `focusAsk()` clears the session → `#1` always = latest answer
- Multiple code blocks in one answer → `#1`, `#2`, `#3` in order

---

## Mobile Monitor

Use your phone to read answers during the interview while your laptop handles the audio.

### Setup

1. Enable ngrok: Settings → Launch Config → toggle ngrok on (or set `USE_NGROK=true` in `.env`)
2. Restart server: `./run.sh` — ngrok URL shown in startup output
3. On your phone: open `https://<your-ngrok-url>/monitor`
3. That's it — answers stream live, latest on top

### Mobile features (iPhone-optimized)

- Sidebar/topbar hidden automatically on screen width ≤640px
- Font **18px by default** (larger than desktop 15px)
- **A− / A+** buttons in the status bar for live font adjustment (saved to phone)
- Status indicator: green = Listening · orange = Transcribing · purple = Generating
- Tap **REMOTE** button to copy the URL to share

### Laptop ↔ Phone flow

```
Laptop:                          Phone (iPhone):
  Server running on :8000          open ngrok URL /monitor
  Mic captures audio               answers appear in real-time
  STT → DB → LLM                   read and respond to interviewer
  answer streams via SSE  →→→→→→→→  big text, clean view, no clutter
```

---

## Performance Tuning

### Make it faster

1. Terminal bar → STT: switch to `tiny` (200ms vs 400ms)
2. Settings → Silence: use **Fast** preset (0.6s)
3. Terminal bar → LLM: `haiku` (fastest)
4. Terminal bar → ROLE: set correct role (more DB hits, fewer LLM calls)

### Tuned defaults (already applied)

| Setting | Value | Was |
|---|---|---|
| `VAD_PADDING_MS` | 200ms | 400ms |
| `_MERGE_WAIT` | 1.0s | 2.5s |
| `COOLDOWN_DEFAULT` | 0.6s | 0.8s |
| `COOLDOWN_MAX` | 1.5s | 2.0s |
| `_looks_complete` | ≥6 words / conf ≥0.82 | ≥8 words / conf ≥0.85 |
| Sarvam language | `en-IN` fixed | auto-detect (+400ms) |
| Audio backend | `sounddevice` first | `parec` when PULSE_SOURCE set |

### Audio backend priority

`professional_audio.py` tries backends in this order:
1. **sounddevice** — routes through PulseAudio, honours `PULSE_SOURCE` for both mic and speaker monitor capture. Best quality.
2. **pyaudio** — fallback if sounddevice unavailable.
3. **parec** — last resort; useful only if neither sounddevice nor pyaudio is installed.

Set `PULSE_SOURCE` to your speaker monitor name (e.g. `alsa_output.pci-0000_00_1f.3.analog-stereo.monitor`) to capture system audio instead of microphone.
