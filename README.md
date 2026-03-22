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
           ├─ Priority 1: Self-intro     → user.self_introduction    (<5ms)
           ├─ Priority 2: DB match       → qa_database.find_answer()  (13–30ms, Jaccard ≥0.72)
           ├─ Priority 3: Answer cache   → LRU in-memory              (<1ms)
           └─ Priority 4: LLM stream     → Claude Haiku/Sonnet        (~2–3s TTFT)
                          │
                          ▼
           [SSE /api/stream  →  React UI at /react/  /  Phone Monitor]
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

### 2. Start everything

```bash
./run.sh
```

Prompts for access mode:
- **[1]** Local / LAN only
- **[2]** Local + ngrok (random URL)
- **[3]** Local + ngrok (fixed domain)

Or manually:

```bash
source venv/bin/activate
python3 -W ignore main.py        # server on :8000
cd react_ui && npm run build     # build React UI (production)
```

### 3. Open in browser

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

---

## Interview Roles

Set from the terminal bar (Dashboard) or `POST /api/interview_role`.

| Chip | Role | LLM focus | DB filter |
|---|---|---|---|
| `gen` | general | No special context | all tags |
| `py` | python | Django, FastAPI, async | python, django |
| `java` | java | Spring Boot, JVM | java |
| `js` | javascript | React, Node.js, TypeScript | javascript |
| `sql` | sql | Query optimization, indexes | sql |
| `saas` | saas | Multi-tenancy, billing, REST | saas |

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
5. Set **Server URL**:
   - Local: `http://localhost:8000`
   - With ngrok: `https://your-domain.ngrok-free.app`

### Usage

1. Start the server: `./run.sh`
2. Join your Google Meet / Zoom / Teams interview
3. Click **Start** in the extension popup → audio capture begins
4. Interviewer questions are transcribed → answers appear live at `http://localhost:8000/react/`
5. The extension also injects a floating answer overlay on meeting pages

### Extension Features

| Feature | How it works |
|---|---|
| Audio capture | Captures tab/system audio via WebRTC AudioWorklet (16kHz mono) |
| Monitor overlay | Floating answer overlay injected into Google Meet / Zoom pages |
| Code interceptor | Detects coding problems on LeetCode, HackerRank, Codility, etc. |
| Typewriter | Auto-types generated solutions into coding platform editors |

### Supported coding platforms (auto-intercept)

- LeetCode, HackerRank, Codility, CodeSignal, Codewars
- Replit, Google Colab, Programiz

---

## Flask UI Pages (Classic)

| URL | Purpose |
|---|---|
| `/` | Main dashboard (index.html) |
| `/monitor` | Global monitor view |
| `/settings` | Settings page |
| `/qa-manager` | QA database manager |
| `/ext-users` | Extension user admin |
| `/users` | User profile manager |
| `/lookup` | Manual keyword search |
| `/voice` | Voice test interface |
| `/api-dashboard` | API key status |
| `/admin-docs` | Full project reference |

---

## Multi-User / Chrome Extension

### Admin setup

1. Open `/ext-users`
2. Click **+ New user** → fill name, token, role, coding language
3. Copy the **token** → share with candidate
4. Copy the **monitor URL** → candidate opens on their phone

### User setup (candidate)

1. Load `chrome_extension/` as unpacked extension in Chrome
2. Extension popup → paste **Server URL** and **User Token**
3. Click **Start** → answers appear on their monitor only

### Isolation guarantees

- Each token has its own `UserAnswerStorage`
- DB lookups filtered by `db_user_id` (role-based answer sets)
- LLM prompt includes user's resume + role context
- No answer cross-contamination between tokens

---

## Database

SQLite at `~/.drishi/qa_pairs.db` (default) or PostgreSQL via `DATABASE_URL`.

- **1,264+ Q&A pairs** across 163 tags
- Jaccard similarity threshold: 0.72
- Lookup: `qa_database.find_answer(question, want_code, user_role)`
- Add via `/react/#/qa-manager` or `POST /api/save_to_db`

### PostgreSQL (optional)

```bash
# Start PostgreSQL container
docker run -d --name drishi-pg \
  -e POSTGRES_DB=drishi -e POSTGRES_USER=drishi -e POSTGRES_PASSWORD=drishi \
  -p 5434:5432 postgres:14

# Set in .env
DATABASE_URL=postgresql://drishi:drishi@localhost:5434/drishi
```

Uses `pg8000` (pure Python driver — no C extension, no ssl conflicts).
Falls back to SQLite automatically if `DATABASE_URL` is not set or PostgreSQL is unreachable.

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
NGROK_DOMAIN=                # optional fixed ngrok domain
USER_ID_OVERRIDE=            # active user ID
CODING_LANGUAGE=python
INTERVIEW_ROLE=general
WEB_PORT=8000
```

---

## Project Structure

```
Drishi/
├── main.py                  ← entry point — audio capture + processing threads
├── config.py                ← all runtime constants
├── state.py                 ← pipeline state machine + latency tracking
├── stt.py                   ← STT (Whisper / Sarvam / Deepgram / AssemblyAI)
├── llm_client.py            ← Claude API, streaming, prompt caching
├── qa_database.py           ← SQLite/PG Q&A store, Jaccard lookup
├── db_backend.py            ← PostgreSQL adapter (pg8000, drop-in sqlite3 compat)
├── answer_storage.py        ← in-memory answer state (thread-safe)
├── event_bus.py             ← SSE zero-latency push
├── user_manager.py          ← user profiles + resume context
├── question_validator.py    ← noise/filler rejection, normalization
├── answer_cache.py          ← LRU cache for LLM answers (max 1000)
├── fragment_context.py      ← cross-source fragment merge
├── run.sh                   ← startup script (venv, deps, ngrok, server)
├── .env                     ← secrets + persisted settings
├── qa_pairs.db              ← Q&A database (source)
│
├── app/
│   ├── api/routes/          ← Flask blueprint handlers
│   │   ├── interview.py     ← POST /api/ask, GET /api/stream
│   │   ├── settings.py      ← audio_settings, launch_config, interview_role
│   │   ├── users.py         ← CRUD /api/users
│   │   ├── knowledge.py     ← CRUD /api/qa, /api/save_to_db
│   │   ├── coding.py        ← /api/set_llm_model, /api/solve_problem
│   │   ├── ops.py           ← /api/session-info, /api/answers, /api/logs
│   │   ├── monitoring.py    ← /monitor-viewer/, /api/session/predictions
│   │   └── ui.py            ← all HTML + React production routes
│   ├── services/            ← business logic
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
│   ├── static/react/        ← production React build (npm run build)
│   └── templates/           ← Jinja2 HTML pages (classic UI)
│
├── chrome_extension/        ← Load unpacked from this folder
│   ├── manifest.json        ← MV3, permissions
│   ├── popup.html / popup.js
│   ├── background.js        ← service worker
│   ├── audio_offscreen.js / audio_offscreen.html
│   ├── audio_processor_worklet.js  ← 16kHz mono WebRTC streaming
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

GET  /api/stream           SSE — events: question_started, chunk, answer, error
POST /api/cc_question      {"text":"..."} — Chrome extension text injection
```

### Settings

```
GET  /api/audio_settings
POST /api/audio_settings   {"silence_duration":1.2, "stt_backend":"local", "stt_model":"tiny.en"}

GET  /api/launch_config
POST /api/launch_config    {"llm_model":"haiku|sonnet", "user_id_override":"1"}

GET  /api/interview_role
POST /api/interview_role   {"role":"general|python|java|javascript|sql|saas"}
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

## Deployment

### Local

```bash
./run.sh
```

### ngrok (share with remote users)

```bash
# In .env
USE_NGROK=true
NGROK_DOMAIN=your-domain.ngrok-free.app   # optional fixed domain
```

Restart the server — ngrok URL is shown in startup output and reflected in the UI.

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
