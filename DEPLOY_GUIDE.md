# Drishi Pro — Complete Deployment Guide

> AI-powered real-time interview assistant.
> Captures audio from Google Meet / Teams / Zoom,
> transcribes it, and shows answers instantly.

---

## What You Get (Two Parts)

```
┌─────────────────────────────────┐    ┌──────────────────────────────────┐
│   PART 1: Server (Render.com)   │    │   PART 2: Chrome Extension       │
│                                 │    │                                  │
│  • Handles AI answer generation │◄──►│  • Captures tab audio            │
│  • Speech-to-text (Deepgram)    │    │  • Shows answers in overlay      │
│  • Q&A database (330+ answers)  │    │  • Works on Meet/Teams/Zoom      │
│  • Protected by Secret Code     │    │  • Loaded locally in Chrome      │
└─────────────────────────────────┘    └──────────────────────────────────┘
         Deployed once                        Loaded on every machine
         (runs 24/7 on Render)                (takes 2 minutes)
```

---

## Folder Structure

```
Drishi/                        ← Server project (deploy on Render)
├── render.yaml                ← Blueprint config (Render reads this)
├── requirements-render.txt    ← Cloud-only Python packages
├── web/
│   └── server.py              ← Main server (Flask + WebSocket)
├── main.py                    ← Local mode entry point
├── qa_pairs.db                ← 330+ pre-loaded Q&A answers
└── ...

Drishi-Extension/              ← Chrome extension (load in browser)
├── manifest.json
├── background.js
├── offscreen.js / offscreen.html
├── popup.html / popup.js
├── meet_content.js
└── ...
```

---

## PART 1 — Deploy Server on Render.com

### Prerequisites
- GitHub account
- Render.com account (free tier works for testing; Starter $7/mo for always-on)
- Anthropic API key → [console.anthropic.com](https://console.anthropic.com/)
- Deepgram API key → [console.deepgram.com](https://console.deepgram.com/) (free $200 credit)

---

### Step 1 — Push Project to GitHub

```bash
# From the Drishi/ project folder
git init                          # skip if already a git repo
git add .
git commit -m "Drishi Pro v4 — Render deployment"
git remote add origin https://github.com/YOUR_USERNAME/drishi-pro.git
git push -u origin main
```

---

### Step 2 — Create Service via Blueprint

1. Go to [dashboard.render.com](https://dashboard.render.com)
2. Click **New** → **Blueprint**
3. Click **Connect a repository** → select your GitHub repo
4. Render finds `render.yaml` automatically
5. Click **Apply** — service `drishi-pro` is created

> What Render does automatically:
> - Runs `pip install -r requirements-render.txt`
> - Starts `python web/server.py`
> - Assigns a URL like `https://drishi-pro.onrender.com`

---

### Step 3 — Set Secret Environment Variables

After the service is created:

1. Go to your service → **Environment** tab
2. Add these variables:

| Variable | Value | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | ✅ Yes |
| `DEEPGRAM_API_KEY` | `...` | ✅ Yes (for cloud STT) |
| `SECRET_CODE` | Choose a password e.g. `drishi2026!` | ✅ Yes |
| `SARVAM_API_KEY` | `...` | ❌ Optional |

3. Click **Save Changes** → service auto-redeploys

---

### Step 4 — Verify Deployment

Once deployed, test it:

```bash
# Replace with your actual Render URL
curl https://drishi-pro.onrender.com/health
```

Expected response:
```json
{"status": "ok", "cloud": true}
```

Test secret code:
```bash
curl -X POST https://drishi-pro.onrender.com/api/auth \
  -H "Content-Type: application/json" \
  -d '{"code": "drishi2026!"}'
```

Expected response:
```json
{"ok": true, "token": "drishi2026!"}
```

---

## PART 2 — Load Chrome Extension

> No code changes needed. The extension is configured from inside Chrome.

### Step 1 — Open Extensions Page

Open Chrome → address bar → type:
```
chrome://extensions
```

### Step 2 — Enable Developer Mode

Top-right corner → turn **ON** the "Developer mode" toggle.

### Step 3 — Load the Extension

1. Click **"Load unpacked"** button (top-left)
2. Browse to the `Drishi-Extension/` folder
3. Click **Select Folder**
4. **Drishi Pro** appears in your extensions list

### Step 4 — Pin the Extension

Click the puzzle icon 🧩 in Chrome toolbar → click the pin 📌 next to **Drishi Pro**

---

### Step 5 — Configure Server URL and Secret Code

1. Click the **Drishi Pro icon** in the Chrome toolbar
2. Click the **⚙ Settings** tab
3. Fill in:

| Field | Value |
|---|---|
| **Server URL** | `https://drishi-pro.onrender.com` (your Render URL) |
| **Secret Code** | Same value as `SECRET_CODE` on Render |

4. Click **Save Settings**
5. Click **Test Connection** → should show `✓ Server + auth OK!`

> **Local mode:** Use `http://localhost:8000` as URL, leave Secret Code blank.

---

### Step 6 — Using in an Interview

1. Open **Google Meet / Teams / Zoom** in Chrome
2. Start or join the interview call
3. Click **Drishi Pro** icon in toolbar
4. In the **🎧 Interview** tab, click **"🎙 Capture This Tab's Audio"**
5. Button turns green → **Capturing Live**
6. The interviewer speaks → answer appears automatically in the popup
7. Answers also appear as an overlay on the Meet/Teams page

---

## How It Works (Flow)

```
[Interviewer speaks on Google Meet/Teams/Zoom]
              │
              ▼
[Chrome tab audio captured by Drishi Extension]
    • chrome.tabCapture API grabs the tab's audio stream
    • Offscreen document processes audio chunks
    • Client-side VAD (voice activity detection) detects speech
    • Speech segment (PCM-16, 16kHz) sent to server
              │
              ▼  WebSocket (wss://drishi-pro.onrender.com/ws/audio)
              │
[Render.com Server — web/server.py]
    • Validates secret code token
    • Converts PCM-16 bytes → numpy float32 audio
    • Sends to Deepgram API → transcript text
    • Validates: is it an interview question?
    • Checks Q&A database (330+ cached answers, <30ms)
    • If not cached → Claude AI generates answer (1-3s)
              │
              ▼  WebSocket response (JSON)
              │
[Chrome Extension receives answer]
    • Popup shows question + answer
    • Overlay appears on Meet/Teams page
    • "Copy" button for quick paste
    • Code answers show with syntax highlighting
```

---

## Quick Ask (Without Audio)

You can also type a question manually:

1. Click Drishi Pro icon
2. Type question in the input box at the bottom
3. Press **Enter** or click **Ask**
4. Answer appears immediately

---

## Code Question Flow (Programiz)

When a coding question is answered:

1. Answer appears with code in the popup
2. Open [programiz.com/python-online-compiler](https://www.programiz.com/python-online-compiler)
3. In the editor, type `#1` (or `#2`, `#3` for multiple answers)
4. Extension auto-types the code solution!

---

## Troubleshooting

### "Cannot reach server" in Settings
- Check the Render URL is correct (no trailing slash)
- Check the service is running on Render dashboard
- Free tier sleeps after 15 min — upgrade to Starter for always-on

### "Wrong secret code"
- Check SECRET_CODE env var on Render matches what you typed in extension

### Capture button not working
- Make sure you are on a Google Meet / Teams / Zoom tab
- Chrome may ask for permission the first time — click Allow

### No answer appearing
- Check DEEPGRAM_API_KEY is set on Render (required for cloud STT)
- Check ANTHROPIC_API_KEY is set on Render

### Extension not loading
- Make sure Developer Mode is ON in chrome://extensions
- Make sure you selected the correct `Drishi-Extension/` folder (not the parent)

---

## Local Development (No Render needed)

```bash
cd Drishi/
cp .env.example .env
# Edit .env → add ANTHROPIC_API_KEY

./run.sh
# Opens http://localhost:8000
```

In Chrome extension Settings:
- Server URL: `http://localhost:8000`
- Secret Code: (leave blank)

---

## Summary

| Task | What to do |
|---|---|
| Deploy server | Push to GitHub → Render Blueprint → set 3 env vars |
| Load extension | chrome://extensions → Load unpacked → select `Drishi-Extension/` |
| Configure extension | Settings tab → enter Render URL + Secret Code → Save |
| Use in interview | Open Meet → click Capture → answers appear automatically |
| Update server | `git push` → Render auto-redeploys |
| Update extension | Edit files → chrome://extensions → click 🔄 refresh icon |
