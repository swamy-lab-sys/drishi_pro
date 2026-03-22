
# Drishi Enterprise

Real-time interview intelligence platform. Captures live interview questions via audio or Chrome extension, generates AI answers instantly, and displays them on a phone-friendly monitor — all with per-user isolation for multi-candidate sessions.

---

## How It Works

```
[Interview Audio]
      │
      ▼
[Chrome Extension / System Audio]
      │  WebSocket /ws/audio
      ▼
[Server — web/server.py]
      │
      ├─ STT (Whisper local / Sarvam AI / deepgram)
      │
      ├─ Validate (duplicate / noise filter)
      │
      ├─ DB lookup (role-filtered Q&A cache)
      │
      └─ LLM fallback (OpenAI / Groq / Gemini)
            │
            ▼
     [Answer Storage]  ←── per user token or global
            │
            ▼
     [SSE /api/stream + REST /api/answers]
            │
            ▼
     [Browser Dashboard / Phone Monitor]
```

---

## Quick Start

### 1. Install dependencies

```bash
cd /path/to/Drishi-Pro
pip install -r requirements.txt
```

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill in values (see [Configuration](#configuration)).

```bash
cp .env.example .env
```

### 3. Start the server

```bash
python run.py
```

Server runs at `http://localhost:5000` by default.

### 4. Open the dashboard

Navigate to `http://localhost:5000` in your browser.

---

## Audio Modes

Drishi supports two independent audio capture paths.

### Mode 1 — System Audio (local only)

Uses PyAudio to capture the system microphone directly on the server machine.

- No extension needed
- Transcription: Whisper (local) or Deepgram
- Start/stop from the main dashboard

### Mode 2 — Chrome Extension (recommended for remote sessions)

The Chrome extension captures tab audio (Google Meet, Teams, etc.) and streams it to the server.

**Sub-modes:**

| Sub-mode | When | How |
|---|---|---|
| **Raw PCM** | No Sarvam key configured | Extension sends binary PCM-16 frames; server runs Whisper/Deepgram |
| **Sarvam STT** | `SARVAM_API_KEY` set in extension | Extension does client-side STT via Sarvam AI, sends text JSON to server |

---

## Multi-User Setup

Multiple candidates can use the system simultaneously — each gets a completely isolated pipeline.

### Admin workflow

1. Open `http://localhost:5000/ext-users`
2. Click **Add New User** — fill in token (e.g. `alice`), name, role, coding language
3. Copy the user's **token** and **monitor URL** from the table
4. Share the token with the user; share the monitor URL with them for their phone

### User workflow

1. Install the Chrome extension (see [Chrome Extension](#chrome-extension))
2. Open extension → **Settings** tab
3. Paste their token into **User Token** field
4. Start capturing — answers appear on their monitor URL only

### Isolation guarantees

- Each token gets its own `UserAnswerStorage` instance
- Answers stored at `~/.drishi/ext_users/<token>/current_answer.json`
- DB lookups are filtered by `db_user_id` (role-based answer sets)
- LLM prompt includes the user's role (e.g. "Senior Java Developer")
- No answer cross-contamination between tokens

### Backward compatibility

If no `user_token` is set in the extension, everything routes through the global pipeline — existing single-user behavior is unchanged.

---

## Chrome Extension

### Loading the extension

1. Open Chrome → `chrome://extensions/`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select the `chrome_extension/` folder
4. Pin the Drishi icon to your toolbar

### Extension tabs

| Tab | Purpose |
|---|---|
| **Main** | Start/stop capture, view live answers, copy answers |
| **Type** | Code Typer — auto-types answers into focused input fields |
| **Monitor** | Opens your browser-based phone monitor |
| **Settings** | Server URL, Secret Code, User Token, Sarvam key, audio device |

### Settings to configure

| Setting | Description |
|---|---|
| Server URL | Your Drishi server (e.g. `http://localhost:5000` or ngrok URL) |
| Secret Code | Must match `SECRET_CODE` in server `.env` |
| User Token | Your unique token (from admin). Leave blank for global mode |
| Sarvam API Key | Optional. Enables client-side STT via Sarvam AI |

### Audio stream source

The extension captures **tab audio** — it works with any browser tab playing audio (Google Meet, Teams, Zoom web, etc.). It does not require screen sharing.

---

## Web Dashboard Pages

| URL | Page | Description |
|---|---|---|
| `/` | Dashboard | Live answer feed, start/stop system audio, SSE stream |
| `/monitor` | Monitor | Full-screen answer view for phone/tablet |
| `/monitor?user=<token>` | User Monitor | Per-user answer view (polled, mobile-friendly) |
| `/ext-users` | Extension Users | Admin panel — create/manage user tokens |
| `/users` | DB Users | Manage DB user profiles (role-based answer sets) |
| `/questions` | Questions | Browse and search the Q&A database |
| `/qa-manager` | QA Manager | Edit/delete/import Q&A entries |
| `/lookup` | Lookup | Type any keyword → see matching Q&A instantly |
| `/profile` | Profile | Edit skills, custom instructions for LLM prompts |
| `/settings` | Settings | API keys, STT config, model selection |
| `/api-dashboard` | API Dashboard | Live API health and key status |
| `/voice` | Voice UI | Push-to-talk voice interface |

---

## Configuration

All configuration lives in `.env` at the project root.

| Variable | Required | Description |
|---|---|---|
| `SECRET_CODE` | Yes | Auth token for WebSocket and admin API calls |
| `OPENAI_API_KEY` | Yes* | GPT-4o / GPT-4o-mini for answer generation |
| `GROQ_API_KEY` | No | Groq LLM alternative (faster, cheaper) |
| `GEMINI_API_KEY` | No | Google Gemini LLM alternative |
| `DEEPGRAM_API_KEY` | No | Deepgram cloud STT (extension raw PCM mode) |
| `SARVAM_API_KEY` | No | Set in Chrome extension for client-side STT |
| `DB_HOST` | No | PostgreSQL host (defaults to local SQLite) |
| `DB_PORT` | No | PostgreSQL port |
| `DB_NAME` | No | Database name |
| `DB_USER` | No | Database user |
| `DB_PASSWORD` | No | Database password |
| `PORT` | No | Server port (default: `5000`) |
| `PRODUCT_NAME` | No | UI display name (default: `Drishi Enterprise`) |

*At least one LLM key is required for answer generation.

---

## Project Structure

```
Drishi-Pro/
├── run.py                      # Entry point — starts Flask + WebSocket server
├── web/
│   ├── server.py               # WebSocket server, audio processing, answer pipeline
│   └── templates/              # Jinja2 HTML pages
│       ├── index.html          # Main dashboard
│       ├── monitor.html        # Global full-screen monitor
│       ├── user_monitor.html   # Per-user phone monitor
│       ├── ext_users.html      # Admin — extension user management
│       └── ...                 # Other dashboard pages
├── app/
│   ├── api/
│   │   └── routes/
│   │       ├── ops.py          # /api/answers, /api/stream, /api/questions
│   │       ├── ui.py           # HTML page routes
│   │       └── ...
│   └── services/
│       ├── ops_service.py      # Answer payload builder (global + per-user)
│       └── ...
├── ext_user_store.py           # User registry + UserAnswerStorage (per-user isolation)
├── extension_users.json        # User data store (flat JSON, written by API)
├── answer_storage.py           # Global answer storage (system audio / no-token mode)
├── state.py                    # Global state (selected user, system audio status)
├── chrome_extension/
│   ├── manifest.json
│   ├── popup.html              # Extension popup UI
│   ├── popup.js                # Extension logic (settings, answers, code typer)
│   ├── background.js           # Service worker (tab capture coordination)
│   ├── audio_offscreen.js      # Offscreen audio capture + WebSocket streaming
│   └── audio_processor_worklet.js  # AudioWorklet for low-latency capture
├── db/                         # Database models and Q&A access layer
├── llm/                        # LLM clients (OpenAI, Groq, Gemini)
├── stt/                        # STT clients (Whisper, Deepgram)
└── .env                        # Configuration (never committed)
```

---

## Adding Questions to the Database

Questions can be added via:

1. **QA Manager** (`/qa-manager`) — web UI, paste question + answer, assign to a DB user
2. **Lookup page** (`/lookup`) — search existing questions by keyword
3. **Direct DB** — insert into the `questions` table with `user_id` set to the target DB user profile

The DB lookup runs before the LLM — if a matching question is found for the user's role, it returns instantly (sub-100ms) without an API call.

---

## Deployment

### Local (default)

```bash
python run.py
```

Runs at `http://localhost:5000`.

### ngrok (share with remote users)

```bash
ngrok http 5000
```

Copy the `https://xxxx.ngrok-free.app` URL → paste into Chrome extension **Server URL**. Extension users can now connect from anywhere.

### Docker

```bash
docker build -t drishi-enterprise .
docker run -p 5000:5000 --env-file .env drishi-enterprise
```

### Render / Railway / Fly.io

1. Set all `.env` variables as environment variables in the platform dashboard
2. Set start command: `python run.py`
3. Set port to `5000` (or use `PORT` env var)

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Extension shows "WebSocket connection failed" | Check server URL in Settings — must include `http://` or `https://`. Verify server is running. |
| Extension shows "Offline" after connecting | Secret Code mismatch — check `SECRET_CODE` in `.env` matches extension Settings |
| Answers not appearing on user monitor | Verify user token in extension matches token in `/ext-users` admin panel |
| "User not found or disabled" in WS logs | Token doesn't exist or user is disabled — check `/ext-users` |
| No audio captured | On Google Meet: extension needs tab audio permission. Try refreshing the Meet tab after loading extension. |
| Whisper not transcribing | Ensure `whisper` Python package is installed: `pip install openai-whisper` |
| DB answers not returning | Check `db_user_id` in user config matches the DB user profile with questions |
| AudioContext suspended | Known Chrome behavior on autoplay — extension auto-resumes. If persistent, reload the extension. |

---

## API Reference

### REST Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/answers` | No | Get answers. `?user_token=<token>` for per-user |
| `GET` | `/api/stream` | No | SSE stream of live answer updates |
| `GET` | `/api/questions` | No | List Q&A database entries |
| `POST` | `/api/questions` | Yes | Add a question/answer pair |
| `GET` | `/api/ext_users` | Yes | List all extension users |
| `POST` | `/api/ext_users` | Yes | Create extension user |
| `PATCH` | `/api/ext_users/<token>` | Yes | Update user (e.g. toggle active) |
| `DELETE` | `/api/ext_users/<token>` | Yes | Delete extension user |
| `GET` | `/api/settings` | No | Get current settings |
| `POST` | `/api/settings` | No | Update settings |
| `GET` | `/api/status` | No | Server health check |

Auth = requires `Authorization: Bearer <SECRET_CODE>` header or `token=<SECRET_CODE>` query param.

### WebSocket

**Endpoint:** `ws://<host>/ws/audio`

**Query params:**
- `token=<SECRET_CODE>` — required
- `user_token=<token>` — optional, routes to per-user pipeline

**Binary frames:** Raw PCM-16 mono 16kHz audio chunks

**JSON messages sent by client:**
```json
{ "type": "ping" }
{ "type": "text_question", "text": "What is a goroutine?" }
```

**JSON messages sent by server:**
```json
{ "type": "connected", "message": "Connected as Alice (java-dev-1)" }
{ "type": "pong" }
{ "type": "answer_chunk", "text": "A goroutine is..." }
{ "type": "answer_complete" }
```
