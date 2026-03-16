#!/usr/bin/env python3
"""
Web Server for Drishi Pro

Provides real-time UI for viewing interview answers.

Features:
- Server-Sent Events (SSE) for real-time updates
- Syntax highlighting for code blocks
- Mobile-first responsive design
- Performance metrics display
"""

import sys
import os
import json
import time
import hashlib
import re
import struct
import queue
import threading
from collections import deque
import psutil
import requests as _requests
from pathlib import Path
from flask import Flask, render_template, Response, jsonify, request, abort
from flask_sock import Sock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import state
import answer_storage
import llm_client # Needed for direct API calls
import fragment_context
import qa_database
from question_validator import validate_question, is_code_request

# Import debug logger
try:
    import debug_logger as dlog
    LOGGING_ENABLED = True
except ImportError:
    LOGGING_ENABLED = False
    class DlogStub:
        def log(self, *args, **kwargs): pass
    dlog = DlogStub()

# ── Cached JD / resume loader ─────────────────────────────────────────────────
# Avoids re-reading files on every cc_question request. Refreshes when mtime changes.
_jd_cache    = {'text': '', 'mtime': 0.0}
_resume_cache = {'text': '', 'mtime': 0.0}

def _get_jd_text() -> str:
    """Return JD text, re-reading only when file changes."""
    try:
        from config import JD_PATH
        p = Path.cwd() / JD_PATH
        if not p.exists():
            return ''
        mtime = p.stat().st_mtime
        if mtime != _jd_cache['mtime']:
            _jd_cache['text'] = p.read_text(encoding='utf-8')
            _jd_cache['mtime'] = mtime
    except Exception:
        pass
    return _jd_cache['text']

def _get_resume_text(resume_path: Path) -> str:
    """Return resume text, re-reading only when file changes."""
    try:
        if not resume_path.exists():
            return ''
        mtime = resume_path.stat().st_mtime
        if mtime != _resume_cache['mtime']:
            from resume_loader import load_resume
            _resume_cache['text'] = load_resume(resume_path)
            _resume_cache['mtime'] = mtime
    except Exception:
        pass
    return _resume_cache['text']

# Configuration
DEFAULT_PORT = 8000
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

# WebSocket support (for cloud audio streaming from Chrome extension)
sock = Sock(app)

# ── Per-user mobile streaming state ───────────────────────────────────────────
# Fully isolated per user_id. Zero impact on WS pipeline — push is non-blocking.
#
# _user_answer_buf  : last 20 answers per user (in-memory, cleared on server restart)
# _user_sse_queues  : list of Queue objects — one per connected mobile tab/device
# _mobile_state_lock: single lock protecting both dicts
#
_user_answer_buf: dict   = {}   # user_id -> deque(maxlen=20)
_user_sse_queues: dict   = {}   # user_id -> [Queue, ...]
_mobile_state_lock        = threading.Lock()


def _push_answer_to_mobile(user_id: int, payload: dict):
    """
    Push a new answer payload to:
      1. The per-user ring buffer (so latecoming mobile devices get recent history)
      2. Every active SSE queue for that user (phones, tablets, extra tabs)

    Called from _handle_ws_text — runs in the WebSocket thread.
    Uses put_nowait so it never blocks the audio pipeline.
    """
    if not user_id:
        return
    with _mobile_state_lock:
        if user_id not in _user_answer_buf:
            _user_answer_buf[user_id] = deque(maxlen=20)
        _user_answer_buf[user_id].append(payload)
        for q in list(_user_sse_queues.get(user_id, [])):
            try:
                q.put_nowait(payload)
            except Exception:
                pass  # full queue — skip, don't block


def _register_mobile_sse(user_id: int, q: queue.Queue):
    with _mobile_state_lock:
        if user_id not in _user_sse_queues:
            _user_sse_queues[user_id] = []
        _user_sse_queues[user_id].append(q)


def _unregister_mobile_sse(user_id: int, q: queue.Queue):
    with _mobile_state_lock:
        qs = _user_sse_queues.get(user_id, [])
        try:
            qs.remove(q)
        except ValueError:
            pass


def _get_user_buffer(user_id: int) -> list:
    with _mobile_state_lock:
        return list(_user_answer_buf.get(user_id, []))


# Disable Flask logging
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ── Secret code auth ──────────────────────────────────────────────────────────
SECRET_CODE = config.SECRET_CODE  # "" means no auth required (local mode)

def _check_auth(req=None):
    """Return True if request is authenticated (or no secret code is set)."""
    req = req or request
    token = (
        req.headers.get('X-Auth-Token', '')
        or req.args.get('token', '')
        or (req.get_json(silent=True) or {}).get('token', '')
    )
    # Accept master SECRET_CODE (admin) or any active per-user key
    if not SECRET_CODE:
        return True  # local mode — no auth
    if token == SECRET_CODE:
        return True
    # Check per-user key
    if token and token.startswith('dk-'):
        return qa_database.get_user_by_key(token) is not None
    return False

def require_auth(f):
    """Decorator: returns 401 if secret code is wrong."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _check_auth():
            return jsonify({'error': 'Invalid or missing secret code', 'code': 401}), 401
        return f(*args, **kwargs)
    return decorated


# ── Runtime-mutable admin settings ───────────────────────────────────────────
# Initialized from env vars; updated live via /api/admin/settings.
# Persisted to ~/.drishi/runtime_settings.json (survives process restart;
# cleared on Render redeploy — redeploy picks up env vars again).

_RT_PATH = Path.home() / '.drishi' / 'runtime_settings.json'

_rt = {
    'stt_backend': os.environ.get('STT_BACKEND', 'sarvam'),
    'stt_language': os.environ.get('SARVAM_LANGUAGE', 'unknown'),
    'llm_model':    os.environ.get('LLM_MODEL_OVERRIDE', 'claude-haiku-4-5-20251001'),
}

_VALID_STT   = {'sarvam', 'deepgram', 'local'}
_VALID_LLM   = {
    'claude-haiku-4-5-20251001',
    'claude-sonnet-4-6',
    'claude-opus-4-6',
}
_VALID_LANG  = {
    'unknown', 'en-IN', 'hi-IN', 'te-IN', 'ta-IN',
    'kn-IN', 'ml-IN', 'mr-IN', 'gu-IN', 'bn-IN', 'pa-IN',
}


def _apply_rt():
    """Push runtime settings into config + llm_client module globals."""
    try:
        config.STT_BACKEND = _rt['stt_backend']
        config.LLM_MODEL   = _rt['llm_model']
        import llm_client as _llmc
        _llmc.MODEL        = _rt['llm_model']
    except Exception:
        pass


def _save_rt():
    try:
        _RT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RT_PATH.write_text(json.dumps(_rt, indent=2))
    except Exception:
        pass


def _load_rt():
    """Load persisted settings from disk (overrides env vars if file exists)."""
    try:
        if _RT_PATH.exists():
            saved = json.loads(_RT_PATH.read_text())
            for k in ('stt_backend', 'stt_language', 'llm_model'):
                if k in saved:
                    _rt[k] = saved[k]
            _apply_rt()
    except Exception:
        pass


_load_rt()   # run once at import time


# ── Cloud STT helpers — no stt.py / faster-whisper needed ────────────────────

def _build_wav(pcm16_bytes: bytes) -> bytes:
    """Build minimal 44-byte WAV header for PCM-16 mono 16 kHz."""
    sample_rate, channels, bps = 16000, 1, 16
    data_size  = len(pcm16_bytes)
    byte_rate  = sample_rate * channels * bps // 8
    block_align = channels * bps // 8
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16, 1, channels, sample_rate,
        byte_rate, block_align, bps,
        b'data', data_size,
    )
    return header + pcm16_bytes


def _cloud_transcribe_pcm16(pcm16_bytes: bytes) -> str:
    """Transcribe PCM-16 mono 16 kHz via Deepgram Nova-3."""
    key = os.environ.get('DEEPGRAM_API_KEY', '')
    if not key:
        return ''
    try:
        resp = _requests.post(
            'https://api.deepgram.com/v1/listen'
            '?model=nova-3&language=en&punctuate=true&smart_format=true',
            headers={'Authorization': f'Token {key}', 'Content-Type': 'audio/wav'},
            data=_build_wav(pcm16_bytes),
            timeout=15,
        )
        resp.raise_for_status()
        chs = resp.json().get('results', {}).get('channels', [])
        if chs:
            alts = chs[0].get('alternatives', [])
            if alts:
                return alts[0].get('transcript', '').strip()
    except Exception:
        pass
    return ''


def _cloud_transcribe_sarvam(pcm16_bytes: bytes) -> str:
    """
    Transcribe PCM-16 mono 16 kHz via Sarvam AI saarika:v2.5.
    Best for Indian English, Telugu, Hindi, Tamil, Kannada, etc.
    Auto-translates regional languages to English when lang=unknown.
    """
    key = os.environ.get('SARVAM_API_KEY', '')
    if not key:
        # Graceful fallback to Deepgram if Sarvam key missing
        return _cloud_transcribe_pcm16(pcm16_bytes)

    lang = _rt.get('stt_language', 'unknown') or 'unknown'
    try:
        resp = _requests.post(
            'https://api.sarvam.ai/speech-to-text',
            headers={'api-subscription-key': key},
            files={'file': ('audio.wav', _build_wav(pcm16_bytes), 'audio/wav')},
            data={'model': 'saarika:v2.5', 'language_code': lang},
            timeout=15,
        )
        resp.raise_for_status()
        result    = resp.json()
        text      = (result.get('transcript') or '').strip()
        detected  = result.get('language_code', 'en-IN')

        # Auto-translate non-English in auto-detect mode
        if lang == 'unknown' and detected and detected != 'en-IN' and text:
            try:
                tr = _requests.post(
                    'https://api.sarvam.ai/translate',
                    headers={'api-subscription-key': key},
                    json={'input': text, 'source_language_code': detected,
                          'target_language_code': 'en-IN',
                          'speaker_gender': 'Male', 'mode': 'formal'},
                    timeout=10,
                )
                tr.raise_for_status()
                text = tr.json().get('translated_text', text).strip() or text
            except Exception:
                pass
        return text
    except Exception:
        pass
    return ''


def _cloud_transcribe(pcm16_bytes: bytes) -> str:
    """Route to correct cloud STT backend based on current runtime setting."""
    backend = _rt.get('stt_backend', 'sarvam')
    if backend == 'sarvam':
        return _cloud_transcribe_sarvam(pcm16_bytes)
    return _cloud_transcribe_pcm16(pcm16_bytes)  # deepgram / fallback


# Global state for latest generated code AND control
latest_code = {
    'code': '',
    'timestamp': 0,
    'platform': '',
    'source': '',      # 'chat', 'editor', 'url'
    'status': 'idle',  # idle, generating, paused, complete, error
    'mode': 'auto',    # 'auto' (auto-type) or 'view' (view-only)
    'control': 'stopped'  # 'stopped', 'running', 'paused'
}

# Deduplication: track recent problems to avoid duplicates
recent_problems = {
    'last_hash': '',
    'last_time': 0,
}
DEDUP_WINDOW_SECONDS = 10  # Ignore same problem within 10 seconds


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    # No Cache headers
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/users')
def users_page():
    """Serve users list dashboard."""
    return render_template('users.html')


@app.route('/api-dashboard')
def api_dashboard():
    """Serve API configuration dashboard."""
    return render_template('api_dashboard.html')


@app.route('/api/session-info')
def session_info():
    """Summary of current session for UI header."""
    info = state.get_session_info()
    # Add counts
    db_stats = qa_database.get_stats()
    info["db_count"] = db_stats.get("total", 0)
    # Cache hit rate
    try:
        import answer_cache
        stats = answer_cache.get_stats()
        total = stats.get('hits', 0) + stats.get('misses', 0)
        info["cache_hits"] = round(stats['hits'] * 100 / total) if total > 0 else 0
    except Exception:
        info["cache_hits"] = 0
    return jsonify(info)


@app.route('/api/settings/mode-profile', methods=['POST'])
def set_mode_profile():
    """Set profile: interview or detailed."""
    data = request.get_json()
    profile = data.get("profile", "interview")
    state.set_mode_profile(profile)
    return jsonify({"status": "updated", "mode": profile})


@app.route('/health')
def health_check():
    """Render.com health check — must return 200 quickly."""
    return jsonify({'status': 'ok', 'cloud': config.CLOUD_MODE})


@app.route('/api/auth', methods=['POST', 'OPTIONS'])
def api_auth():
    """Validate secret code. Returns session token on success."""
    if request.method == 'OPTIONS':
        return '', 204
    data = request.get_json(silent=True) or {}
    code = data.get('code', '') or request.args.get('code', '')
    if SECRET_CODE and code != SECRET_CODE:
        return jsonify({'ok': False, 'error': 'Wrong secret code'}), 401
    return jsonify({'ok': True, 'token': SECRET_CODE or 'local'})


# ── WebSocket audio endpoint (cloud mode) ─────────────────────────────────────
@sock.route('/ws/audio')
def audio_websocket(ws):
    """
    Receive raw PCM-16 audio chunks from Chrome extension.
    Each message = one speech segment (captured between silence gaps on client).
    Transcribes via cloud STT → runs answer pipeline → streams answer back.

    Auth: ?token=<SECRET_CODE> in query string.
    """
    # Auth check — accept master code or per-user key
    token = request.args.get('token', '')
    ws_user = None  # user dict resolved from per-user key, or None

    if SECRET_CODE:
        if token == SECRET_CODE:
            pass  # admin / master key — no per-user context
        elif token and token.startswith('dk-'):
            ws_user = qa_database.get_user_by_key(token)
            if ws_user is None:
                ws.send(json.dumps({'type': 'error', 'message': 'Invalid or revoked access key.'}))
                return
        else:
            ws.send(json.dumps({'type': 'error', 'message': 'Invalid secret code. Set it in extension Settings.'}))
            return

    name_hint = ws_user['name'] if ws_user else 'Drishi Pro'
    # Build mobile URL so extension can show it to the user
    _host = request.host_url.rstrip('/')
    _mobile_url = f"{_host}/m/{token}" if (ws_user and token.startswith('dk-')) else None
    ws.send(json.dumps({
        'type': 'connected',
        'message': f'{name_hint} ready. Listening...',
        'user': ws_user['name'] if ws_user else None,
        'mobile_url': _mobile_url,
    }))

    # Cloud mode: use _cloud_transcribe() which routes to Sarvam or Deepgram.
    # Local mode: import stt.py (needs faster-whisper installed locally).
    _use_cloud_stt = config.CLOUD_MODE or _rt.get('stt_backend') in ('sarvam', 'deepgram')

    if not _use_cloud_stt:
        import stt as _stt  # local mode only — needs faster-whisper installed

    while True:
        try:
            data = ws.receive(timeout=60)
        except Exception:
            break
        if data is None:
            break

        # ── Control messages (JSON strings) ──────────────────────────────
        if isinstance(data, str):
            try:
                msg = json.loads(data)
                if msg.get('type') == 'ping':
                    ws.send(json.dumps({'type': 'pong'}))
                elif msg.get('type') == 'text_question':
                    # Manual typed question from extension popup
                    _handle_ws_text(ws, msg.get('text', ''), ws_user)
            except Exception:
                pass
            continue

        # ── Binary: PCM-16 mono 16 kHz audio bytes ───────────────────────
        try:
            if len(data) < 6400:  # < 0.2s at 16kHz 16-bit — too short, skip
                continue

            # Transcribe
            ws.send(json.dumps({'type': 'status', 'message': 'Transcribing...'}))
            if _use_cloud_stt:
                text = _cloud_transcribe(data)
            else:
                import numpy as np
                audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                text = _stt.transcribe(audio)

            if not text or len(text.strip()) < 4:
                ws.send(json.dumps({'type': 'status', 'message': 'Listening...'}))
                continue

            ws.send(json.dumps({'type': 'transcript', 'text': text}))
            _handle_ws_text(ws, text, ws_user)

        except Exception as e:
            ws.send(json.dumps({'type': 'error', 'message': f'Processing error: {e}'}))


def _handle_ws_text(ws, text: str, ws_user=None):
    """Run the answer pipeline for a transcribed question and send answer via WS.
    ws_user: user dict resolved from per-user access key, or None for shared/admin mode.
    """
    from question_validator import validate_question, is_code_request
    import answer_cache as _cache
    import qa_database as _qadb

    text = text.strip()
    if not text:
        return

    # Validate
    ok, cleaned, reason = validate_question(text)
    if not ok:
        ws.send(json.dumps({'type': 'rejected', 'reason': reason or 'Not an interview question'}))
        return

    _mobile_user_id = (ws_user or {}).get('id') or (ws_user or {}).get('user_id')

    def _send_answer(payload: dict):
        """Send answer to laptop extension AND push to mobile."""
        ws.send(json.dumps(payload))
        if _mobile_user_id:
            _push_answer_to_mobile(_mobile_user_id, payload)

    # Dedup
    cached = _cache.get_cached_answer(cleaned)
    if cached:
        _send_answer({'type': 'answer', 'question': cleaned,
                      'answer': cached, 'source': 'cache', 'is_complete': True})
        return

    wants_code = is_code_request(cleaned)

    # QA DB lookup
    db_result = _qadb.find_answer(cleaned, want_code=wants_code)
    if db_result:
        db_answer, db_score, db_id = db_result
        _cache.cache_answer(cleaned, db_answer)
        _send_answer({'type': 'answer', 'question': cleaned,
                      'answer': db_answer, 'source': f'db-{db_score:.2f}', 'is_complete': True})
        return

    # LLM generation
    ws.send(json.dumps({'type': 'status', 'message': 'Generating answer...'}))
    try:
        from llm_client import get_coding_answer, get_interview_answer
        from user_manager import build_resume_context_for_llm
        user_ctx = build_resume_context_for_llm(user=ws_user)
        if wants_code:
            answer = get_coding_answer(cleaned, user_context=user_ctx)
        else:
            answer = get_interview_answer(cleaned, user_context=user_ctx)

        if answer:
            _cache.cache_answer(cleaned, answer)
            _send_answer({'type': 'answer', 'question': cleaned,
                          'answer': answer, 'source': 'llm', 'is_complete': True})
        else:
            ws.send(json.dumps({'type': 'error', 'message': 'Could not generate answer'}))
    except Exception as e:
        ws.send(json.dumps({'type': 'error', 'message': f'LLM error: {e}'}))


@app.route('/api/system/health')
def system_health():
    """Real-time system monitoring."""
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    return jsonify({
        "cpu": cpu,
        "ram": ram,
        "stt_status": "Listening",
        "llm_status": "Idle"
    })

@app.route('/api/session/predictions')
def get_predictions():
    """Get predicted next topics."""
    from semantic_engine import engine
    topics = engine.predict_next_topics()
    return jsonify(topics)


@app.route('/api/status')
def get_api_status():
    """Get expanded status of various APIs."""
    status = []
    
    # Sarvam AI
    sarvam_key = os.environ.get("SARVAM_API_KEY", "")
    status.append({
        'service': 'Sarvam AI',
        'status': 'Active' if sarvam_key else 'Missing Key',
        'key_configured': bool(sarvam_key),
        'usage': '15 requests today',
        'latency': '0.9s'
    })
    
    # Anthropic
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    status.append({
        'service': 'Anthropic Claude',
        'status': 'Active' if anthropic_key else 'Missing Key',
        'key_configured': bool(anthropic_key),
        'usage': '42 requests today',
        'latency': '1.4s'
    })
    
    # OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    status.append({
        'service': 'OpenAI',
        'status': 'Active' if openai_key else 'Missing Key',
        'key_configured': bool(openai_key),
        'usage': '8 requests today',
        'latency': '1.1s'
    })
    
    # Deepgram
    deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "")
    status.append({
        'service': 'Deepgram STT',
        'status': 'Active' if deepgram_key else 'Missing Key',
        'key_configured': bool(deepgram_key),
        'usage': '120 minutes',
        'latency': '0.4s'
    })
    
    return jsonify(status)


@app.route('/api/users/activate/<int:user_id>', methods=['POST'])
def activate_user(user_id):
    """Switch active user profile."""
    user = qa_database.get_user(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    state.set_selected_user(user)
    return jsonify({"status": "activated", "name": user["name"]})


# ── User CRUD API ─────────────────────────────────────────────────────────────

@app.route('/api/users', methods=['GET'])
def list_users():
    """List all users."""
    users = qa_database.get_all_users()
    return jsonify(users)


@app.route('/api/users', methods=['POST'])
def create_user():
    """Create a new user."""
    data = request.get_json()
    if not data or 'name' not in data or 'role' not in data:
        return jsonify({'error': 'name and role are required'}), 400
    
    user_id = qa_database.add_user(
        name=data['name'],
        role=data['role'],
        experience_years=int(data.get('experience_years', 0)),
        resume_text=data.get('resume_text', ''),
        job_description=data.get('job_description', ''),
        self_introduction=data.get('self_introduction', '')
    )
    return jsonify({'id': user_id, 'status': 'created'}), 201


@app.route('/api/users/<int:user_id>', methods=['GET'])
def get_user_route(user_id):
    """Get a single user."""
    user = qa_database.get_user(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify(user)


@app.route('/api/users/<int:user_id>', methods=['PUT'])
def update_user_route(user_id):
    """Update an existing user."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    ok = qa_database.update_user(
        user_id=user_id,
        name=data.get('name'),
        role=data.get('role'),
        experience_years=data.get('experience_years'),
        resume_text=data.get('resume_text'),
        job_description=data.get('job_description'),
        self_introduction=data.get('self_introduction')
    )
    if not ok:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'status': 'updated'})


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user_route(user_id):
    """Delete a user."""
    ok = qa_database.delete_user(user_id)
    if not ok:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'status': 'deleted'})


# ── Prepared Questions API ───────────────────────────────────────────────────

@app.route('/api/prepared-questions', methods=['GET'])
def list_prepared_questions():
    """List all prepared questions."""
    questions = qa_database.get_all_questions()
    return jsonify(questions)


@app.route('/api/prepared-questions', methods=['POST'])
def create_prepared_question():
    """Add a new prepared question."""
    data = request.get_json()
    if not data or 'question' not in data or 'prepared_answer' not in data or 'role' not in data:
        return jsonify({'error': 'question, prepared_answer, and role are required'}), 400
    
    q_id = qa_database.add_prepared_question(
        role=data['role'],
        question=data['question'],
        prepared_answer=data['prepared_answer']
    )
    return jsonify({'id': q_id, 'status': 'created'}), 201


@app.route('/api/prepared-questions/<int:q_id>', methods=['DELETE'])
def delete_prepared_question_route(q_id):
    """Delete a prepared question."""
    ok = qa_database.delete_prepared_question(q_id)
    if not ok:
        return jsonify({'error': 'Question not found'}), 404
    return jsonify({'status': 'deleted'})


# ── Access Key Management API ─────────────────────────────────────────────────

@app.route('/api/access-keys', methods=['GET'])
def list_all_access_keys():
    """Admin: list all access keys across all users."""
    keys = qa_database.get_all_access_keys()
    return jsonify(keys)


@app.route('/api/access-keys/user/<int:user_id>', methods=['GET'])
def list_user_access_keys(user_id):
    """List access keys for a specific user."""
    keys = qa_database.get_keys_for_user(user_id)
    return jsonify(keys)


@app.route('/api/access-keys', methods=['POST'])
def create_access_key():
    """Create a new access key for a user."""
    data = request.get_json(silent=True) or {}
    user_id = data.get('user_id')
    label   = data.get('label', '')
    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400
    key = qa_database.create_access_key(int(user_id), label)
    if key is None:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'key': key, 'label': label, 'status': 'created'}), 201


@app.route('/api/access-keys/<int:key_id>', methods=['DELETE'])
def delete_access_key(key_id):
    """Permanently delete an access key."""
    ok = qa_database.delete_access_key(key_id)
    if not ok:
        return jsonify({'error': 'Key not found'}), 404
    return jsonify({'status': 'deleted'})


@app.route('/api/access-keys/<int:key_id>/revoke', methods=['POST'])
def revoke_access_key(key_id):
    """Disable an access key (revoke without deleting)."""
    ok = qa_database.revoke_access_key(key_id)
    if not ok:
        return jsonify({'error': 'Key not found'}), 404
    return jsonify({'status': 'revoked'})


# ── Mobile View ───────────────────────────────────────────────────────────────

@app.route('/m/<key>')
def mobile_view(key):
    """
    Mobile answer view.
    Candidate opens this URL on their phone during the interview.
    Auth = the personal access key (dk-xxxxx) in the URL.
    """
    user = qa_database.get_user_by_key(key)
    if not user:
        return render_template('mobile_error.html',
                               error='Invalid or revoked access key.'), 403
    return render_template('mobile.html',
                           user_name=user['name'],
                           user_role=user['role'],
                           access_key=key)


@app.route('/m/<key>/stream')
def mobile_stream(key):
    """
    SSE stream for mobile view.

    On connect:
      - Validates the key
      - Sends all buffered answers (up to 20) immediately so latecomers catch up
      - Then streams new answers in real time

    On mobile sleep/disconnect:
      - EventSource auto-reconnects (browser handles this)
      - Gets buffer replay on reconnect

    Handles multiple concurrent mobiles per user (phone + tablet, etc.)
    """
    user = qa_database.get_user_by_key(key)
    if not user:
        return Response('data: {"type":"error","message":"Invalid key"}\n\n',
                        mimetype='text/event-stream', status=403)

    user_id = user.get('id') or user.get('user_id')
    buffered = _get_user_buffer(user_id)
    q = queue.Queue(maxsize=50)
    _register_mobile_sse(user_id, q)

    def generate():
        try:
            # 1. Replay buffer immediately (catch-up for late connects & reconnects)
            for item in buffered:
                yield f'data: {json.dumps(item)}\n\n'

            # 2. Acknowledge connection
            yield f'data: {json.dumps({"type":"connected","user":user["name"]})}\n\n'

            # 3. Stream live answers
            while True:
                try:
                    item = q.get(timeout=25)
                    yield f'data: {json.dumps(item)}\n\n'
                except queue.Empty:
                    yield ':ping\n\n'  # keepalive prevents proxy timeouts
        finally:
            _unregister_mobile_sse(user_id, q)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # disable nginx buffering
            'Connection': 'keep-alive',
        }
    )


@app.route('/')
def index():
    """Serve main page."""
    return render_template('index.html')


@app.route('/questions')
def questions():
    """Serve questions database page."""
    return render_template('questions.html')


@app.route('/api/answers')
def get_answers():
    """Get all answers."""
    answers = answer_storage.get_all_answers()
    return jsonify(answers)


@app.route('/api/transcribing')
def get_transcribing():
    """Return live transcription text for the hearing indicator."""
    text = answer_storage.get_transcribing()
    return jsonify({'text': text or ''})


@app.route('/api/ask', methods=['POST'])
def ask_question():
    """
    Manually submit a question from the UI.

    JSON body: { question: str, db_only: bool, model: str }

    The question goes through the same pipeline as voice questions:
    1. DB lookup (fast)
    2. LLM fallback (if db_only is False)
    3. Answer stored and pushed to SSE stream
    """
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get('question') or '').strip()
    db_only  = bool(data.get('db_only', False))

    if not question:
        return jsonify({'error': 'question is required'}), 400

    # Introduction question shortcut — check BEFORE validation so phrases like
    # "Introduce yourself" (command, no question mark) aren't rejected by the validator.
    from user_manager import is_introduction_question
    if is_introduction_question(question):
        _active = state.get_selected_user()
        if _active and (_active.get('self_introduction') or '').strip():
            intro = _active['self_introduction'].strip()
            answer_storage.set_complete_answer(question, intro, {'source': 'intro'})
            return jsonify({'answer': intro, 'source': 'intro'})

    # Light validation for manually typed questions — only reject obvious noise.
    # Voice-mode strict rules (incomplete fragments, short noise) don't apply here.
    try:
        from question_validator import validate_question, is_code_request
        valid, cleaned, reason = validate_question(question)
        if not valid and reason not in ('incomplete', 'too_short', 'no_question_pattern'):
            # Only hard-reject genuinely invalid input; let fragments through for manual ask
            return jsonify({'error': f'Question rejected: {reason}'}), 422
        question = cleaned  # Use cleaned/corrected version
    except Exception:
        pass

    wants_code = is_code_request(question)

    # 1. DB lookup
    db_result = qa_database.find_answer(question, want_code=wants_code)
    if db_result:
        db_answer, db_score, db_id = db_result
        metrics = {'source': f'db-{db_id}', 'db_score': round(db_score, 2)}
        answer_storage.set_complete_answer(question, db_answer, metrics)
        return jsonify({'answer': db_answer, 'source': 'db', 'score': db_score})

    if db_only:
        return jsonify({'answer': '', 'source': 'db', 'score': 0,
                        'message': 'No DB match — LLM disabled'})

    # 2. LLM (run in background thread so request returns quickly)
    def _run_llm():
        try:
            import answer_cache
            from user_manager import build_resume_context_for_llm
            if wants_code:
                answer = llm_client.get_coding_answer(question)
            else:
                user_ctx = build_resume_context_for_llm()
                if user_ctx:
                    chunks = list(llm_client.get_streaming_interview_answer(question, '', '', user_ctx))
                else:
                    jd   = _get_jd_text()
                    rp   = Path.home() / '.drishi' / 'uploaded_resume.txt'
                    rsm  = _get_resume_text(rp)
                    chunks = list(llm_client.get_streaming_interview_answer(question, rsm, jd))
                answer = llm_client.humanize_response(''.join(chunks))
            if answer:
                src_tag = 'api-code' if wants_code else 'api'
                metrics = {'source': src_tag}
                answer_storage.set_complete_answer(question, answer, metrics)
                answer_cache.cache_answer(question, answer)
                # Async auto-learn
                try:
                    from main import _submit_for_learning
                    _submit_for_learning(question, answer, wants_code)
                except Exception:
                    pass
        except Exception as ex:
            dlog.log(f"[ask endpoint] LLM error: {ex}", "ERROR")

    t = threading.Thread(target=_run_llm, daemon=True)
    t.start()

    # Mark as processing so SSE picks it up
    answer_storage.set_processing_question(question)
    return jsonify({'status': 'generating', 'source': 'llm'})


UPLOADED_RESUME_PATH = Path.home() / ".drishi" / "uploaded_resume.txt"

@app.route('/api/upload_resume', methods=['POST'])
def upload_resume():
    """Upload resume file. Saves as plain text to shared location."""
    from flask import request
    if 'resume' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['resume']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file:
        try:
            from resume_loader import invalidate_resume_cache

            UPLOADED_RESUME_PATH.parent.mkdir(parents=True, exist_ok=True)

            # Read file content as text
            content = file.read()
            try:
                text = content.decode('utf-8')
            except UnicodeDecodeError:
                text = content.decode('latin-1')

            # Skip binary PDF content - extract only readable text
            if text.startswith('%PDF'):
                # Try pdftotext if available
                import subprocess, tempfile
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                try:
                    result = subprocess.run(['pdftotext', tmp_path, '-'], capture_output=True, text=True, timeout=10)
                    text = result.stdout.strip()
                except Exception:
                    text = ""
                finally:
                    os.unlink(tmp_path)

            if not text.strip():
                return jsonify({'error': 'Could not extract text from file'}), 400

            # Save as plain text
            with open(UPLOADED_RESUME_PATH, 'w', encoding='utf-8') as f:
                f.write(text)

            invalidate_resume_cache()
            print(f"[SERVER] Resume uploaded: {len(text)} chars")
            return jsonify({'success': True, 'message': f'Resume uploaded ({len(text)} chars)'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'Unknown error'}), 400


@app.route('/api/resume_status')
def resume_status():
    """Check if resume was uploaded via UI."""
    uploaded = UPLOADED_RESUME_PATH.exists() and UPLOADED_RESUME_PATH.stat().st_size > 0
    return jsonify({'uploaded': uploaded})


@app.route('/api/users/<int:user_id>/upload_resume', methods=['POST'])
def upload_user_resume(user_id):
    """Upload and extract a PDF/text resume for a specific user profile."""
    user = qa_database.get_user(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if 'resume' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['resume']
    if not file or file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    try:
        import tempfile
        from user_manager import extract_pdf_text, summarize_resume

        suffix = '.pdf' if (file.filename or '').lower().endswith('.pdf') else '.txt'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        try:
            text = extract_pdf_text(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        if not text.strip():
            return jsonify({'error': 'Could not extract text from file'}), 400

        summary = summarize_resume(text)

        qa_database.update_user(
            user_id=user_id,
            resume_text=text,
            resume_file=file.filename,
            resume_summary=summary,
        )

        # Refresh state if this user is currently active
        active = state.get_selected_user()
        if active and active.get('id') == user_id:
            updated = qa_database.get_user(user_id)
            if updated:
                state.set_selected_user(updated)

        print(f"[SERVER] Resume uploaded for user {user_id}: {len(text)} chars")
        return jsonify({
            'success': True,
            'message': f'Resume uploaded ({len(text)} chars)',
            'summary': summary,
            'filename': file.filename,
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/save_jd', methods=['POST'])
def save_jd():
    """Save job description text."""
    from flask import request
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    
    try:
        from config import JD_PATH
        jd_path = Path.cwd() / JD_PATH
        with open(jd_path, 'w') as f:
            f.write(data['text'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/get_jd')
def get_jd():
    """Get current job description."""
    try:
        from config import JD_PATH
        jd_path = Path.cwd() / JD_PATH
        if jd_path.exists():
            with open(jd_path, 'r') as f:
                return jsonify({'text': f.read()})
        return jsonify({'text': ''})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ip')
def get_ip():
    """Get server LAN IP address."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    
    response = jsonify({'ip': IP})
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response


@app.route('/api/stream')
def stream():
    """
    SSE stream — hybrid: file-poll (150ms) for audio questions from main.py process
    + in-process event_bus drain for /api/ask events (same process, zero latency).

    Architecture note: main.py launches web/server.py as a subprocess. They share
    disk files (~/.drishi/*.json) but NOT memory. The event_bus only works within
    a single process. Audio answers written by main.py are detected here via mtime
    polling of current_answer.json (written every ~80ms during streaming).

    Event types sent to client:
      init          — full answers array on connect (JSON array)
      question      — new question started, answer forming (shows placeholder card)
      answer        — completed answer {question, answer, is_complete, metrics}
      transcribing  — live STT text {text}
      ping          — keepalive every 20s
    """
    import event_bus
    import queue as _queue

    _ANSWERS_FILE    = Path.home() / ".drishi" / "current_answer.json"
    _TR_FILE         = Path.home() / ".drishi" / "transcribing.json"
    POLL_INTERVAL    = 0.08   # 80ms — matches disk write throttle for live streaming

    # In-process event bus subscription (for /api/ask in server.py's process)
    iq = event_bus.subscribe()

    def _read_file_answers():
        try:
            if not _ANSWERS_FILE.exists():
                return []
            with open(_ANSWERS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, list) else []
        except Exception:
            return []

    def event_stream():
        # Track what we've already sent: question_lower → 'thinking' | 'complete'
        sent = {}
        sent_partial = {}   # question_lower → answer-length already streamed to client
        last_file_mtime  = 0.0
        last_tr_mtime    = 0.0
        last_ping        = time.time()

        try:
            # ── Init: send full current state on connect ──────────────────────
            try:
                answers = _read_file_answers()
                yield f"event: init\ndata: {json.dumps(answers)}\n\n"
                for a in answers:
                    if a.get('question'):
                        qk = a['question'].strip().lower()
                        sent[qk] = 'complete' if a.get('is_complete') else 'thinking'
                try:
                    last_file_mtime = _ANSWERS_FILE.stat().st_mtime
                except Exception:
                    pass
                tr = answer_storage.get_transcribing()
                if tr:
                    yield f"event: transcribing\ndata: {json.dumps({'text': tr})}\n\n"
            except Exception:
                yield "event: init\ndata: []\n\n"

            while True:
                now = time.time()

                # ── 1. Drain in-process event bus (for /api/ask, non-blocking) ─
                drained = 0
                while drained < 30:
                    try:
                        msg = iq.get_nowait()
                        t_type, d = msg['t'], msg['d']
                        yield f"event: {t_type}\ndata: {json.dumps(d)}\n\n"
                        # Keep tracking in sync with in-process events
                        if t_type == 'question' and d.get('question'):
                            sent[d['question'].strip().lower()] = 'thinking'
                        elif t_type == 'answer' and d.get('question'):
                            sent[d['question'].strip().lower()] = 'complete'
                        drained += 1
                    except _queue.Empty:
                        break

                # ── 2. Poll answers file for changes from main.py process ──────
                try:
                    cur_mtime = _ANSWERS_FILE.stat().st_mtime if _ANSWERS_FILE.exists() else 0.0
                    if cur_mtime > last_file_mtime + 0.004:
                        last_file_mtime = cur_mtime
                        for ans in _read_file_answers():
                            if not ans.get('question'):
                                continue
                            qk          = ans['question'].strip().lower()
                            is_complete = bool(ans.get('is_complete'))
                            prev        = sent.get(qk)

                            if prev is None:
                                # Brand new question
                                if is_complete:
                                    sent[qk] = 'complete'
                                    yield f"event: answer\ndata: {json.dumps(ans)}\n\n"
                                else:
                                    sent[qk] = 'thinking'
                                    yield f"event: question\ndata: {json.dumps({'question': ans['question']})}\n\n"

                            elif prev == 'thinking' and not is_complete:
                                # Still streaming — send new chunks as they arrive
                                cur_answer = ans.get('answer', '')
                                last_len = sent_partial.get(qk, 0)
                                if len(cur_answer) > last_len:
                                    new_chunk = cur_answer[last_len:]
                                    sent_partial[qk] = len(cur_answer)
                                    yield f"event: chunk\ndata: {json.dumps({'q': ans['question'], 'c': new_chunk})}\n\n"

                            elif prev == 'thinking' and is_complete:
                                # Answer just finished
                                sent[qk] = 'complete'
                                sent_partial.pop(qk, None)
                                yield f"event: answer\ndata: {json.dumps(ans)}\n\n"
                except Exception:
                    pass

                # ── 3. Poll transcribing file ─────────────────────────────────
                try:
                    tr_mtime = _TR_FILE.stat().st_mtime if _TR_FILE.exists() else 0.0
                    if tr_mtime > last_tr_mtime + 0.004:
                        last_tr_mtime = tr_mtime
                        with open(_TR_FILE, 'r', encoding='utf-8') as f:
                            tr_data = json.load(f)
                        yield f"event: transcribing\ndata: {json.dumps({'text': tr_data.get('text','')})}\n\n"
                except Exception:
                    pass

                # ── 4. Keepalive ──────────────────────────────────────────────
                if now - last_ping >= 20:
                    last_ping = now
                    yield "event: ping\ndata: {}\n\n"

                time.sleep(POLL_INTERVAL)

        except GeneratorExit:
            pass
        finally:
            event_bus.unsubscribe(iq)

    response = Response(event_stream(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


@app.route('/api/set_llm_model', methods=['POST'])
def set_llm_model():
    """Change LLM model from the UI settings."""
    data = request.get_json() or {}
    m = data.get('model', '')
    model_map = {
        'haiku':  'claude-haiku-4-5-20251001',
        'sonnet': 'claude-sonnet-4-6',
    }
    if m not in model_map:
        return jsonify({'error': f'Unknown model: {m}'}), 400
    try:
        os.environ['LLM_MODEL_OVERRIDE'] = model_map[m]
        import llm_client as _lc
        _lc.MODEL = model_map[m]
        return jsonify({'model': m, 'id': model_map[m]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/coding_language', methods=['GET'])
def get_coding_language():
    """Return the current default coding language."""
    import config as _c
    return jsonify({'language': _c.CODING_LANGUAGE})


@app.route('/api/coding_language', methods=['POST'])
def set_coding_language():
    """Change the default coding language used for ambiguous coding questions."""
    data = request.get_json() or {}
    lang = data.get('language', '').lower().strip()
    allowed = {'python', 'java', 'javascript', 'sql', 'bash'}
    if lang not in allowed:
        return jsonify({'error': f'Unknown language. Allowed: {sorted(allowed)}'}), 400
    import config as _c
    import llm_client as _lc
    _c.CODING_LANGUAGE = lang
    os.environ['CODING_LANGUAGE'] = lang
    return jsonify({'language': lang})


@app.route('/api/stt_model', methods=['GET'])
def get_stt_model():
    """Get current STT model name (local mode only)."""
    if config.CLOUD_MODE:
        return jsonify({'model': 'deepgram-nova-3', 'cloud': True})
    try:
        import stt
        return jsonify({'model': stt.model_name or config.STT_MODEL})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stt_model', methods=['POST'])
def set_stt_model():
    """Change STT model (local mode only)."""
    if config.CLOUD_MODE:
        return jsonify({'error': 'STT model switching not available in cloud mode'}), 400

    data = request.get_json()
    if not data or 'model' not in data:
        return jsonify({'error': 'No model specified'}), 400

    new_model = data['model']
    allowed = ['tiny.en', 'base.en', 'small.en', 'medium.en']
    if new_model not in allowed:
        return jsonify({'error': f'Invalid model. Allowed: {allowed}'}), 400

    try:
        import stt
        old_model = stt.model_name or config.STT_MODEL
        if new_model == old_model:
            return jsonify({'model': old_model, 'changed': False})

        print(f"[SERVER] STT model change: {old_model} -> {new_model}")
        config.STT_MODEL = new_model
        stt.DEFAULT_MODEL = new_model
        stt.load_model(new_model)
        print(f"[SERVER] STT model loaded: {new_model}")
        return jsonify({'model': new_model, 'changed': True})
    except Exception as e:
        print(f"[SERVER] STT model change failed: {e}")
        return jsonify({'error': str(e)}), 500


# ── Admin: runtime STT + LLM settings ────────────────────────────────────────

@app.route('/api/admin/settings', methods=['GET'])
def get_admin_settings():
    """Return current runtime STT + LLM settings plus API key presence."""
    # Allow master key OR per-user key (admin intent check is on frontend)
    if not _check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({
        'stt_backend':      _rt.get('stt_backend', 'sarvam'),
        'stt_language':     _rt.get('stt_language', 'unknown'),
        'llm_model':        _rt.get('llm_model', 'claude-haiku-4-5-20251001'),
        'sarvam_key_set':   bool(os.environ.get('SARVAM_API_KEY')),
        'deepgram_key_set': bool(os.environ.get('DEEPGRAM_API_KEY')),
        'anthropic_key_set': bool(os.environ.get('ANTHROPIC_API_KEY')),
        'cloud_mode':       config.CLOUD_MODE,
        'valid_stt':        sorted(_VALID_STT),
        'valid_llm':        sorted(_VALID_LLM),
        'valid_lang':       sorted(_VALID_LANG),
    })


@app.route('/api/admin/settings', methods=['POST'])
def update_admin_settings():
    """Update runtime STT backend, language, and/or LLM model. Applied immediately."""
    if not _check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    changed = []

    if 'stt_backend' in data:
        val = str(data['stt_backend']).strip().lower()
        if val not in _VALID_STT:
            return jsonify({'error': f'Invalid stt_backend. Choose: {sorted(_VALID_STT)}'}), 400
        _rt['stt_backend'] = val
        changed.append('stt_backend')

    if 'stt_language' in data:
        val = str(data['stt_language']).strip()
        if val not in _VALID_LANG:
            return jsonify({'error': f'Invalid language. Choose: {sorted(_VALID_LANG)}'}), 400
        _rt['stt_language'] = val
        changed.append('stt_language')

    if 'llm_model' in data:
        val = str(data['llm_model']).strip()
        if val not in _VALID_LLM:
            return jsonify({'error': f'Invalid llm_model. Choose: {sorted(_VALID_LLM)}'}), 400
        _rt['llm_model'] = val
        changed.append('llm_model')

    if not changed:
        return jsonify({'error': 'Nothing to update'}), 400

    _apply_rt()    # push changes into config + llm_client.MODEL
    _save_rt()     # persist to disk
    print(f'[ADMIN] Settings updated: {changed} → {_rt}')
    return jsonify({'status': 'updated', 'changed': changed, **_rt})


@app.route('/api/logs')
def get_logs():
    """Get recent debug logs."""
    try:
        log_file = Path.home() / ".drishi" / "logs" / "debug.log"
        if log_file.exists():
            with open(log_file, 'r') as f:
                lines = f.readlines()
                return jsonify({"logs": [l.strip() for l in lines[-100:]]})
        return jsonify({"logs": []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/performance')
def get_performance():
    """Get recent performance logs."""
    try:
        log_file = Path.home() / ".drishi" / "logs" / "performance.log"
        if log_file.exists():
            with open(log_file, 'r') as f:
                lines = f.readlines()
                return jsonify({"logs": [l.strip() for l in lines[-50:]]})
        return jsonify({"logs": []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/clear_session', methods=['POST'])
def clear_session():
    """Manually clear all Q&A history (start fresh interview session)."""
    try:
        answer_storage.clear_all(force_clear=True)
        print("[API] 🗑️  Session cleared manually - starting fresh")
        return jsonify({'status': 'cleared', 'message': 'All Q&A history cleared'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _ensure_example_call(lines):
    """LLM now generates code with examples, so just return as-is."""
    return lines


def extract_code_from_answer(answer_text):
    """Parse markdown code blocks from an answer into clean lines.

    Returns (language, lines) or (None, []) if no code block found.
    """
    match = re.search(r'```(\w*)\n(.*?)```', answer_text, re.DOTALL)
    if match:
        lang = match.group(1) or 'python'
        code = match.group(2).rstrip('\n')
        lines = code.split('\n')
        return lang, _ensure_example_call(lines)
    
    stripped = answer_text.strip()
    if not stripped:
        return None, []
    
    if (
        re.match(r'^(def |class |import |from |for |while |if |print\()', stripped) or
        '\ndef ' in stripped or
        '\nclass ' in stripped or
        '\nprint(' in stripped or
        (stripped.count('\n') >= 2 and '(' in stripped and ':' in stripped)
    ):
        lines = stripped.split('\n')
        return 'python', _ensure_example_call(lines)
    
    return None, []


@app.route('/api/code_payload')
def code_payload():
    """Return the latest code answer for the Chrome extension."""
    answers = answer_storage.get_all_answers()
    # Find the most recent answer that contains code
    for ans in answers:
        if not ans.get('answer') or not ans.get('is_complete'):
            continue
        lang, lines = extract_code_from_answer(ans['answer'])
        if lines:
            code_text = '\n'.join(lines)
            code_id = hashlib.md5(code_text.encode()).hexdigest()[:12]
            return jsonify({
                'has_code': True,
                'code_id': code_id,
                'language': lang,
                'lines': lines,
                'question': ans.get('question', ''),
                'timestamp': ans.get('timestamp', ''),
            })
    return jsonify({
        'has_code': False,
        'code_id': None,
        'language': None,
        'lines': [],
        'question': None,
        'timestamp': None,
    })


@app.route('/api/code_payloads')
def code_payloads():
    """Return ALL code answers numbered for the Chrome extension.

    Codes are numbered in chronological order (oldest = #1).
    """
    answers = answer_storage.get_all_answers()
    # get_all_answers returns newest-first, reverse to get chronological
    answers = list(reversed(answers))
    codes = []
    index = 1
    for ans in answers:
        if not ans.get('answer') or not ans.get('is_complete'):
            continue
        lang, lines = extract_code_from_answer(ans['answer'])
        if lines:
            code_text = '\n'.join(lines)
            code_id = hashlib.md5(code_text.encode()).hexdigest()[:12]
            codes.append({
                'index': index,
                'code_id': code_id,
                'language': lang,
                'lines': lines,
                'question': ans.get('question', ''),
                'timestamp': ans.get('timestamp', ''),
            })
            index += 1
    return jsonify({'codes': codes, 'count': len(codes)})


@app.route('/api/coding_state')
def coding_state():
    """Return whether a coding answer is currently being generated."""
    answers = answer_storage.get_all_answers()
    is_generating = False
    last_code_ts = None
    for ans in answers:
        if not ans.get('is_complete') and ans.get('answer'):
            _, lines = extract_code_from_answer(ans['answer'])
            if lines:
                is_generating = True
                break
        if ans.get('is_complete') and ans.get('answer'):
            _, lines = extract_code_from_answer(ans['answer'])
            if lines and not last_code_ts:
                last_code_ts = ans.get('timestamp')
    return jsonify({
        'is_generating': is_generating,
        'last_code_timestamp': last_code_ts,
    })


@app.route('/api/solve_problem', methods=['POST'])
def solve_problem():
    """
    Solve a coding problem from URL/Extension.
    Input: JSON { 'problem': str, 'editor': str, 'url': str }
    Output: JSON { 'solution': str }
    """
    global recent_problems
    import hashlib

    data = request.get_json()
    if not data or 'problem' not in data:
        return jsonify({'error': 'No problem text provided'}), 400

    problem_text = data.get('problem', '')
    editor_content = data.get('editor', '')
    url = data.get('url', '')
    source = data.get('source', 'editor')  # 'chat' or 'editor'

    # Deduplication check
    problem_hash = hashlib.md5((problem_text[:500] + url).encode()).hexdigest()
    now = time.time()

    if (problem_hash == recent_problems['last_hash'] and
        now - recent_problems['last_time'] < DEDUP_WINDOW_SECONDS):
        print(f"[API] ⏭️  DUPLICATE - Skipping (same problem within {DEDUP_WINDOW_SECONDS}s)")
        return jsonify({'solution': '', 'duplicate': True})

    recent_problems['last_hash'] = problem_hash
    recent_problems['last_time'] = now

    # Log the request
    print(f"\n[API] ⚡ SOLVE REQUEST RECEIVED")
    print(f"      Source: {source}")
    print(f"      URL: {url}")
    dlog.log(f"[API] Solve request from {source} for {url}", "INFO")
    
    # Mark as generating
    global latest_code
    latest_code['status'] = 'generating'
    latest_code['platform'] = url
    latest_code['source'] = source
    latest_code['timestamp'] = time.time()
    
    # CHAT MODE: Force view-only
    if source == 'chat':
        latest_code['mode'] = 'view'
        print(f"      [CHAT MODE] Forced to VIEW-ONLY (never types into chat)")

    # Call LLM
    try:
        print(f"\n{'='*50}")
        print(f" QUESTION (Extracted Problem Text):")
        print(f"{'-'*50}\n{problem_text}\n{'-'*50}")
        
        solution = llm_client.get_platform_solution(problem_text, editor_content, url)
        
        # Store the generated code for display on localhost:8000
        latest_code['code'] = solution
        latest_code['status'] = 'complete'
        latest_code['timestamp'] = time.time()
        
        # ALSO store in answer_storage so it appears on homepage!
        # Extract a short question title from the problem
        q_lines = problem_text.strip().split('\n')
        short_question = q_lines[0][:100] if q_lines else 'Coding Problem'
        if url:
            # Extract platform from URL
            import re
            platform_match = re.search(r'(hackerrank|leetcode|codewars|codility|codesignal)', url.lower())
            if platform_match:
                short_question = f"[{platform_match.group(1).upper()}] {short_question}"
        
        answer_storage.set_complete_answer(
            question_text=short_question,
            answer_text=solution,
            metrics={'source': source, 'url': url[:50] if url else None}
        )
        
        print(f"\n ANSWER (Generated Code):")
        print(f"{'-'*50}\n{solution}\n{'-'*50}")
        print(f"✅ Solution generated ({len(solution)} chars)")
        print(f"{'='*50}\n")
        
        response = jsonify({'solution': solution})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    except Exception as e:
        latest_code['status'] = 'error'
        dlog.log_error("[API] Solve failed", e)
        response = jsonify({'error': str(e)})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


@app.route('/api/latest_code')
def get_latest_code():
    """Get the latest generated code for dual display."""
    global latest_code
    return jsonify(latest_code)


@app.route('/api/control/start', methods=['POST'])
def control_start():
    """Start/Resume code generation."""
    global latest_code
    latest_code['control'] = 'running'
    print("[CONTROL] ▶ START/RESUME")
    return jsonify({'status': 'running', 'mode': latest_code['mode']})


@app.route('/api/control/pause', methods=['POST'])
def control_pause():
    """Pause code generation."""
    global latest_code
    latest_code['control'] = 'paused'
    print("[CONTROL] ⏸ PAUSE")
    return jsonify({'status': 'paused', 'mode': latest_code['mode']})


@app.route('/api/control/stop', methods=['POST'])
def control_stop():
    """Stop code generation (hard kill)."""
    global latest_code
    latest_code['control'] = 'stopped'
    latest_code['status'] = 'idle'
    print("[CONTROL] ⛔ STOP")
    return jsonify({'status': 'stopped', 'mode': latest_code['mode']})


@app.route('/api/control/toggle_mode', methods=['POST'])
def control_toggle_mode():
    """Toggle between auto-type and view-only modes."""
    global latest_code
    latest_code['mode'] = 'view' if latest_code['mode'] == 'auto' else 'auto'
    mode_name = 'AUTO-TYPE' if latest_code['mode'] == 'auto' else 'VIEW-ONLY'
    print(f"[CONTROL] 🔁 MODE → {mode_name}")
    return jsonify({'mode': latest_code['mode'], 'status': latest_code['control']})


# ═══════════════════════════════════════════════
# GOOGLE MEET CC + CHAT CAPTURE API
# ═══════════════════════════════════════════════

cc_capture_state = {
    'enabled': False,
    'last_question': '',
    'last_timestamp': 0,
}

# ── Chat session log: questions captured via Google Meet / Teams chat ──────────
# Each entry: {'question': str, 'source': str, 'timestamp': float, 'status': str}
_chat_session: list = []
_chat_lock = threading.Lock()


@app.route('/api/get_answer_by_index', methods=['GET'])
def get_answer_by_index():
    """
    Get a specific answer by its 1-based index (cronological).
    #1 = First question asked
    #2 = Second question asked
    #-1 or #0 = Latest question
    """
    try:
        from flask import request
        index_str = request.args.get('index', '0')
        index = int(index_str)
        
        # Get all answers (Newest -> Oldest)
        all_answers = answer_storage.get_all_answers()
        
        if not all_answers:
            return jsonify({'found': False, 'error': 'No questions found'}), 404
            
        # Chronological list (Oldest -> Newest)
        chronological_answers = list(reversed(all_answers))
        
        target_answer = None
        real_index = 0
        
        if index <= 0:
            # Get latest
            target_answer = chronological_answers[-1]
            real_index = len(chronological_answers)
        else:
            # 1-based index
            if 1 <= index <= len(chronological_answers):
                target_answer = chronological_answers[index - 1]
                real_index = index
            else:
                return jsonify({'found': False, 'error': f'Index {index} out of bounds (1-{len(chronological_answers)})'}), 404
        
        if target_answer:
            raw_answer = target_answer.get('answer', '')
            code = raw_answer

            # 1. Try to extract markdown code block first
            import re
            # Match ```python ... ``` or just ``` ... ```
            code_match = re.search(r'```(?:python|py)?\n(.*?)```', raw_answer, re.DOTALL | re.IGNORECASE)
            if code_match:
                code = code_match.group(1)
            else:
                # 2. If no markdown, but it looks like code (def function), use it all
                # Otherwise, if it's just text, it will be commented out by content.js
                pass

            return jsonify({
                'found': True,
                'index': real_index,
                'total': len(chronological_answers),
                'question': target_answer.get('question', ''),
                'code': code.strip()
            })
            
        return jsonify({'found': False, 'error': 'Answer not found'}), 404
        
    except ValueError:
        return jsonify({'found': False, 'error': 'Invalid index format'}), 400
    except Exception as e:
        return jsonify({'found': False, 'error': str(e)}), 500


@app.route('/api/cc_control', methods=['POST'])
def cc_control():
    """Control CC/Chat capture state."""
    global cc_capture_state
    from flask import request
    data = request.get_json() or {}
    action = data.get('action', '')

    if action == 'start':
        cc_capture_state['enabled'] = True
        print("[CC] 🎙️ CC/Chat capture ENABLED")
    elif action == 'stop':
        cc_capture_state['enabled'] = False
        print("[CC] ⏹️ CC/Chat capture DISABLED")
    elif action == 'status':
        pass

    return jsonify({
        'enabled': cc_capture_state['enabled'],
        'last_question': cc_capture_state['last_question'][:50] if cc_capture_state['last_question'] else '',
    })


# ── Short keyword expansion table ─────────────────────────────────────────────
# Maps lowercase keyword (or 2-3 word phrase) → full question template.
# Used so interviewers can type "encapsulation" in chat during an interview
# instead of "What is encapsulation?" — saves time under pressure.
_KEYWORD_EXPAND = {
    # OOP concepts
    'encapsulation':        'What is encapsulation?',
    'polymorphism':         'What is polymorphism?',
    'inheritance':          'What is inheritance?',
    'abstraction':          'What is abstraction?',
    'oops':                 'What are the four pillars of OOP?',
    'oop':                  'What are the four pillars of OOP?',
    'oops concepts':        'What are the four pillars of OOP?',
    'solid':                'What are SOLID principles?',
    'solid principles':     'What are SOLID principles?',
    # Python concepts
    'generators':           'What are generators in Python?',
    'generator':            'What are generators in Python?',
    'decorators':           'What are decorators in Python?',
    'decorator':            'What are decorators in Python?',
    'metaclass':            'What is a metaclass in Python?',
    'gil':                  'What is the GIL in Python?',
    'global interpreter lock': 'What is the GIL in Python?',
    'list comprehension':   'What is list comprehension in Python?',
    'lambda':               'What is a lambda function in Python?',
    'mutable immutable':    'What is the difference between mutable and immutable in Python?',
    'args kwargs':          'What are *args and **kwargs in Python?',
    '*args **kwargs':       'What are *args and **kwargs in Python?',
    'pickling':             'What is pickling in Python?',
    'shallow deep copy':    'What is the difference between shallow copy and deep copy?',
    'iterator':             'What is an iterator in Python?',
    'context manager':      'What is a context manager in Python?',
    # Data structures / algorithms
    'palindrome':           'Write a function to check if a string is a palindrome.',
    'fibonacci':            'Write a function to generate Fibonacci numbers.',
    'fibonacci series':     'Write a function to generate the Fibonacci series.',
    'factorial':            'Write a function to calculate factorial of a number.',
    'even numbers':         'Write a function to find all even numbers in a list.',
    'odd numbers':          'Write a function to find all odd numbers in a list.',
    'prime numbers':        'Write a function to find all prime numbers up to N.',
    'prime':                'Write a function to check if a number is prime.',
    'anagram':              'Write a function to check if two strings are anagrams.',
    'reverse string':       'Write a function to reverse a string.',
    'bubble sort':          'Write a bubble sort algorithm.',
    'merge sort':           'Write a merge sort algorithm.',
    'binary search':        'Write a binary search algorithm.',
    'linked list':          'Write a singly linked list implementation.',
    'stack':                'Write a stack implementation in Python.',
    'queue':                'Write a queue implementation in Python.',
    # Django
    'orm':                  'What is Django ORM?',
    'django orm':           'What is Django ORM?',
    'migrations':           'What are Django migrations?',
    'django migrations':    'What are Django migrations?',
    'signals':              'What are Django signals?',
    'django signals':       'What are Django signals?',
    'middleware':           'What is Django middleware?',
    'django middleware':    'What is Django middleware?',
    'rest framework':       'What is Django REST Framework?',
    'drf':                  'What is Django REST Framework?',
    'serializer':           'What are serializers in DRF?',
    'viewsets':             'What are ViewSets in DRF?',
    'authentication':       'What are authentication methods in Django?',
    'jwt':                  'What is JWT authentication?',
    'celery':               'What is Celery and how is it used with Django?',
    # DevOps / Cloud
    'docker':               'What is Docker and how does it work?',
    'kubernetes':           'What is Kubernetes?',
    'k8s':                  'What is Kubernetes?',
    'terraform':            'What is Terraform?',
    'ansible':              'What is Ansible?',
    'ci cd':                'What is CI/CD?',
    'cicd':                 'What is CI/CD?',
    'jenkins':              'What is Jenkins?',
    'nginx':                'What is Nginx?',
    'load balancer':        'What is a load balancer?',
    'load balancing':       'What is load balancing?',
    'microservices':        'What are microservices?',
    'kafka':                'What is Apache Kafka?',
    'redis':                'What is Redis?',
    'aws':                  'What are the core AWS services?',
    's3':                   'What is AWS S3?',
    'ec2':                  'What is AWS EC2?',
    'lambda function':      'What is AWS Lambda?',
    'terraform script':     'Write a basic Terraform configuration to create an EC2 instance.',
    'ansible script':       'Write an Ansible playbook to install and start Nginx.',
    'ansible playbook':     'Write an Ansible playbook to install and start Nginx.',
    'dockerfile':           'Write a Dockerfile for a Python Flask application.',
    'docker compose':       'Write a Docker Compose file for a web app with a database.',
    # General CS
    'sql':                  'What is SQL and what are its key commands?',
    'nosql':                'What is NoSQL and how does it differ from SQL?',
    'sql nosql':            'What is the difference between SQL and NoSQL databases?',
    'indexing':             'What is database indexing?',
    'caching':              'What is caching and how does it improve performance?',
    'rest api':             'What is a REST API?',
    'restful':              'What is a RESTful API?',
    'http methods':         'What are HTTP methods?',
    'status codes':         'What are common HTTP status codes?',
    'git':                  'What is Git and what are its core commands?',
    'git merge rebase':     'What is the difference between git merge and git rebase?',
    'threading':            'What is multithreading in Python?',
    'multiprocessing':      'What is multiprocessing in Python?',
    'async await':          'What is async/await in Python?',
    'cors':                 'What is CORS?',
}


def _expand_short_keyword(text: str) -> str:
    """
    If 'text' is a short chat input that matches a known tech keyword,
    return the expanded full question. Otherwise return text unchanged.

    Matches on lowercased, stripped text so "  Encapsulation  " and
    "ENCAPSULATION" both expand correctly.
    """
    stripped = text.strip()
    if len(stripped.split()) > 6:
        # Already long enough — don't touch
        return stripped
    key = stripped.lower().rstrip('?.! ')
    expanded = _KEYWORD_EXPAND.get(key)
    if expanded:
        print(f"[CC] 🔄 Keyword expanded: '{stripped}' → '{expanded}'")
        return expanded
    return stripped


@app.route('/api/cc_question', methods=['POST'])
def cc_question():
    """
    Receive a question captured from Google Meet CC/Chat.
    Process through the same pipeline as audio STT.
    """
    global cc_capture_state

    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'No question provided'}), 400

    question_text = data.get('question', '').strip()
    source = data.get('source', 'cc')  # 'cc' or 'chat'
    platform = data.get('platform', 'google-meet')

    if not question_text:
        return jsonify({'error': 'Empty question'}), 400

    # Deduplicate (same question within 5 seconds)
    if (question_text == cc_capture_state['last_question'] and
        time.time() - cc_capture_state['last_timestamp'] < 5):
        return jsonify({'status': 'duplicate', 'skipped': True})

    cc_capture_state['last_question'] = question_text
    cc_capture_state['last_timestamp'] = time.time()

    # Log to chat session tracker (only questions that pass validation, not raw UI noise)
    is_chat_source = source in ('google-meet-chat', 'teams-chat', 'chat', 'cc')

    print(f"\n[CC] 📝 Question from {source.upper()}: {question_text[:80]}...")

    # ── Short keyword expansion ────────────────────────────────────────────────
    # During interviews, users may type single/short tech keywords instead of
    # full questions (no time to type).  Expand these before validation.
    question_text = _expand_short_keyword(question_text)

    # Fragment merging: merge with recent voice/chat context
    merged_text, was_merged = fragment_context.merge_with_context(question_text)
    if was_merged:
        print(f"[CC] 🔗 Fragment merged: '{question_text[:40]}' -> '{merged_text[:60]}'")
        question_text = merged_text

    # Validate question
    is_valid, cleaned_question, rejection_reason = validate_question(question_text)
    if not is_valid:
        print(f"[CC] ❌ Question rejected: {rejection_reason}")
        return jsonify({
            'status': 'rejected',
            'reason': rejection_reason,
            'original': question_text[:50]
        })
    question_text = cleaned_question
    print(f"[CC] ✅ Question validated: {question_text[:60]}...")

    # Log to chat session — only AFTER validation passes (no UI noise)
    if is_chat_source:
        with _chat_lock:
            _chat_session.append({
                'question': question_text,
                'source': source,
                'timestamp': time.time(),
                'status': 'answered',
            })

    # CHECK IF ALREADY ANSWERED - O(1) lookup via index dict
    existing = answer_storage.is_already_answered(question_text)
    if existing:
        print(f"[CC] Already answered, showing existing: {question_text[:40]}...")
        return jsonify({
            'status': 'already_answered',
            'question': question_text[:50],
            'answer_preview': existing.get('answer', '')[:100]
        })

    # Introduction question shortcut — return stored self_introduction instantly
    from user_manager import is_introduction_question
    if is_introduction_question(question_text):
        _active = state.get_selected_user()
        if _active and (_active.get('self_introduction') or '').strip():
            intro = _active['self_introduction'].strip()
            answer_storage.set_complete_answer(question_text, intro, {'source': 'intro'})
            fragment_context.save_context(question_text, f"chat-{source}")
            return jsonify({
                'status': 'answered',
                'question': question_text[:50],
                'answer': intro,
                'source': 'intro',
            })

    # Load resume / JD — prefer active user context, fall back to global files
    from user_manager import get_active_user_context
    _resume_summary, _user_role, _jd_from_user = get_active_user_context()
    resume_text = _resume_summary or _get_resume_text(UPLOADED_RESUME_PATH)
    jd_text     = _jd_from_user   or _get_jd_text()


    # Get answer from LLM (or DB cache)
    # Chat questions are typically coding-focused (interviewers paste code/problems in chat)
    # So we prioritize coding answers for chat, theory for voice
    try:
        # Check if it's explicitly a code request
        wants_code = is_code_request(question_text)
        
        # For CHAT questions: resolve theory vs coding more carefully
        if source == 'chat' and not wants_code:
            q_lower_stripped = question_text.lower().strip()
            # Clear theory indicators (start of question)
            theory_starters = [
                'what is', 'what are', 'what was', 'what does',
                'explain', 'describe', 'difference between',
                'why', 'when would', 'how does', 'how do',
                'tell me about', 'can you explain',
            ]
            # Clear infra/script indicators anywhere in question
            infra_indicators = [
                'ansible', 'terraform', 'playbook', 'pipeline',
                'dockerfile', 'jenkinsfile', 'yaml', 'manifest',
                'bash script', 'shell script', 'helm chart',
                'kubernetes manifest', 'k8s manifest',
            ]
            is_theory = any(q_lower_stripped.startswith(ind) for ind in theory_starters)
            is_infra  = any(ind in q_lower_stripped for ind in infra_indicators)

            if is_infra:
                wants_code = True
                print(f"[CC] 🔧 Infra/script question → coding mode")
            elif not is_theory:
                wants_code = True
                print(f"[CC] 💬 Chat question → treating as coding request")

        # ── DB lookup before LLM ──────────────────────────────────────
        db_result = qa_database.find_answer(question_text, want_code=wants_code)
        if db_result:
            # DB hit: synchronous and fast (<50ms) — return immediately
            answer, score, qa_id = db_result
            print(f"[CC] DB hit (score={score:.2f}, id={qa_id}) — skipping API call")
            source_label = f'db-{source}'
            answer_storage.set_complete_answer(
                question_text=question_text,
                answer_text=answer,
                metrics={'source': source_label}
            )
            fragment_context.save_context(question_text, f"chat-{source}")
            print(f"[CC] Answer ready ({len(answer)} chars)")
            return jsonify({
                'status': 'answered',
                'question': question_text[:50],
                'answer': answer,
                'answer_length': len(answer),
                'source': source_label,
            })

        # ── LLM API call: stream in background, return 202 immediately ────────
        # This matches how main.py handles voice questions — bullets appear live
        # in the SSE stream while this HTTP call returns instantly.
        source_label = f'cc-{source}'
        answer_storage.set_processing_question(question_text)

        def _stream_answer_bg(q=question_text, wc=wants_code,
                              res=resume_text, jd=jd_text, sl=source_label):
            try:
                from user_manager import build_resume_context_for_llm
                if wc:
                    print(f"[CC] Code request — calling LLM (bg)")
                    answer = llm_client.get_coding_answer(q)
                    answer_storage.set_complete_answer(q, answer, {'source': sl})
                else:
                    print(f"[CC] Theory question — streaming LLM (bg)")
                    user_ctx = build_resume_context_for_llm()
                    raw_chunks = []
                    for chunk in llm_client.get_streaming_interview_answer(q, res, jd, user_ctx):
                        raw_chunks.append(chunk)
                        answer_storage.append_answer_chunk(chunk)
                    answer = llm_client.humanize_response("".join(raw_chunks))
                    answer_storage.set_complete_answer(q, answer, {'source': sl})
                # Auto-save new LLM answer to DB for future instant retrieval
                try:
                    qa_database.save_interview_qa(q, answer)
                except Exception:
                    pass
                fragment_context.save_context(q, f"chat-{sl}")
                print(f"[CC] BG answer ready ({len(answer)} chars)")
            except Exception as e:
                print(f"[CC] BG stream error: {e}")

        threading.Thread(target=_stream_answer_bg, daemon=True).start()
        return jsonify({
            'status': 'processing',
            'question': question_text[:50],
            'source': source_label,
        }), 202

    except Exception as e:
        print(f"[CC] ❌ LLM error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/cc_status')
def cc_status():
    """Get current CC capture status."""
    return jsonify({
        'enabled': cc_capture_state['enabled'],
        'last_question': cc_capture_state['last_question'][:50] if cc_capture_state['last_question'] else '',
        'last_timestamp': cc_capture_state['last_timestamp'],
    })


# ═══════════════════════════════════════════════
# VOICE MODE - PUSH-TO-TALK INTERFACE
# ═══════════════════════════════════════════════

@app.route('/voice')
def voice_ui():
    """Serve push-to-talk voice interface."""
    return render_template('voice.html')


@app.route('/voice/transcribe', methods=['POST'])
def transcribe_audio():
    """
    Transcribe audio from browser MediaRecorder.
    Accepts audio blob, returns transcription.
    """
    if 'audio' not in request.files:
        return jsonify({'success': False, 'error': 'No audio file'}), 400

    audio_file = request.files['audio']
    
    try:
        # Save temporary audio file
        import tempfile
        import numpy as np
        from pydub import AudioSegment
        
        # Save uploaded audio
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            audio_file.save(tmp.name)
            tmp_path = tmp.name
        
        try:
            # Convert to the format expected by Whisper
            audio = AudioSegment.from_file(tmp_path)
            audio = audio.set_frame_rate(16000).set_channels(1)
            
            # Convert to numpy array
            samples = np.array(audio.get_array_of_samples()).astype(np.float32) / 32768.0
            
            # Transcribe using existing STT engine
            from stt import transcribe
            transcription, confidence = transcribe(samples)
            
            # Clean up
            os.unlink(tmp_path)
            
            if transcription and len(transcription.strip()) > 0:
                return jsonify({
                    'success': True,
                    'transcription': transcription.strip(),
                    'confidence': float(confidence)
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Could not transcribe audio'
                }), 400
                
        except Exception as e:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise e
            
    except Exception as e:
        print(f"[VOICE] Transcription error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Transcription failed: {str(e)}'
        }), 500


@app.route('/api/solve', methods=['POST'])
def solve_voice_question():
    """
    Solve a question from voice mode.
    Input: JSON { 'problem': str, 'source': 'voice' }
    Output: JSON { 'solution': str }
    """
    data = request.get_json()
    if not data or 'problem' not in data:
        return jsonify({'error': 'No question provided'}), 400

    question_text = data.get('problem', '').strip()
    source = data.get('source', 'voice')

    if not question_text:
        return jsonify({'error': 'Empty question'}), 400

    print(f"\n[VOICE] 🎤 Question received: {question_text}")

    # Load context (resume only if uploaded via UI)
    try:
        from config import JD_PATH
        from resume_loader import load_resume, load_job_description
        resume_text = load_resume(UPLOADED_RESUME_PATH) if UPLOADED_RESUME_PATH.exists() else ""
        jd_text = load_job_description(Path.cwd() / JD_PATH)
    except:
        resume_text = ""
        jd_text = ""

    # Generate answer
    try:
        from question_validator import is_code_request
        wants_code = is_code_request(question_text)

        # ── DB lookup before LLM ──────────────────────────────────────
        db_result = qa_database.find_answer(question_text, want_code=wants_code)
        if db_result:
            answer, score, qa_id = db_result
            print(f"[VOICE] DB hit (score={score:.2f}, id={qa_id}) — skipping API call")
            src_label = 'db-voice'
        elif wants_code:
            answer = llm_client.get_coding_answer(question_text)
            src_label = source
        else:
            answer = llm_client.get_interview_answer(
                question_text,
                resume_text=resume_text,
                job_description=jd_text,
                include_code=False
            )
            src_label = source

        if answer:
            # Store for display on main UI too
            answer_storage.set_complete_answer(
                question_text=question_text,
                answer_text=answer,
                metrics={'source': src_label}
            )
            print(f"[VOICE] Answer ready ({len(answer)} chars)")

            return jsonify({
                'success': True,
                'solution': answer
            })
        else:
            return jsonify({'error': 'No answer generated'}), 500

    except Exception as e:
        print(f"[VOICE] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500



# ═══════════════════════════════════════════════
# Q&A DATABASE DASHBOARD + CRUD API
# ═══════════════════════════════════════════════

@app.route('/qa-manager')
def qa_manager():
    """Serve the Q&A database dashboard."""
    return render_template('qa_manager.html')



@app.route('/api/qa', methods=['POST'])
def qa_add():
    """Add a new Q&A pair."""
    data = request.get_json()
    if not data or not data.get('question'):
        return jsonify({'error': 'question is required'}), 400
    qa_id = qa_database.add_qa(
        question=data['question'],
        answer_theory=data.get('answer_theory', ''),
        answer_coding=data.get('answer_coding', ''),
        qa_type=data.get('type', 'theory'),
        keywords=data.get('keywords', ''),
        aliases=data.get('aliases', ''),
        tags=data.get('tags', ''),
    )
    return jsonify({'id': qa_id, 'status': 'created'}), 201


@app.route('/api/qa/<int:qa_id>', methods=['GET'])
def qa_get(qa_id):
    """Get a single Q&A pair."""
    row = qa_database.get_qa(qa_id)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row)


@app.route('/api/qa/<int:qa_id>', methods=['PUT'])
def qa_update(qa_id):
    """Update an existing Q&A pair."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    ok = qa_database.update_qa(
        qa_id=qa_id,
        question=data.get('question'),
        answer_theory=data.get('answer_theory'),
        answer_coding=data.get('answer_coding'),
        qa_type=data.get('type'),
        keywords=data.get('keywords'),
        aliases=data.get('aliases'),
        tags=data.get('tags'),
    )
    if not ok:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'status': 'updated'})


@app.route('/api/qa/<int:qa_id>', methods=['DELETE'])
def qa_delete(qa_id):
    """Delete a Q&A pair."""
    ok = qa_database.delete_qa(qa_id)
    if not ok:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'status': 'deleted'})


@app.route('/api/qa/tags', methods=['GET'])
def qa_tags():
    """Return all unique tags with counts for filter UI."""
    stats = qa_database.get_stats()
    return jsonify(stats.get('tags_breakdown', {}))


@app.route('/api/qa/auto-tag', methods=['POST'])
def qa_auto_tag():
    """Re-run auto-tagging on all untagged entries."""
    try:
        updated = qa_database.apply_auto_tags()
        return jsonify({'status': 'ok', 'updated': updated})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/qa', methods=['GET'])
def qa_list_by_tag():
    """List Q&A pairs, optionally filtered by ?tag=<tag>&search=<text>."""
    search = request.args.get('search', '').strip()
    tag    = request.args.get('tag', '').strip()
    rows   = qa_database.get_all_qa(search, tag=tag)
    stats  = qa_database.get_stats()
    return jsonify({'items': rows, 'stats': stats})


@app.route('/api/qa/test', methods=['POST'])
def qa_test():
    """Test DB lookup for a given question text (preview before real call)."""
    data = request.get_json()
    if not data or not data.get('question'):
        return jsonify({'error': 'question required'}), 400
    want_code = data.get('want_code', False)
    result = qa_database.find_answer(data['question'], want_code=want_code)
    if result:
        answer, score, qa_id = result
        return jsonify({'found': True, 'score': round(score, 3), 'qa_id': qa_id, 'answer': answer})
    return jsonify({'found': False, 'score': 0})


@app.route('/api/regenerate', methods=['POST'])
def regenerate_answer():
    """Force a fresh API answer for a question, bypassing DB cache."""
    data = request.get_json()
    if not data or not data.get('question'):
        return jsonify({'error': 'question required'}), 400

    question_text = data.get('question', '').strip()
    if not question_text:
        return jsonify({'error': 'Empty question'}), 400

    print(f"\n[REGEN] Forcing API answer for: {question_text[:60]}...")

    try:
        from config import JD_PATH
        from resume_loader import load_resume, load_job_description
        resume_text = load_resume(UPLOADED_RESUME_PATH) if UPLOADED_RESUME_PATH.exists() else ""
        jd_text = load_job_description(Path.cwd() / JD_PATH)
    except Exception:
        resume_text = ""
        jd_text = ""

    try:
        from question_validator import is_code_request
        wants_code = is_code_request(question_text)

        if wants_code:
            answer = llm_client.get_coding_answer(question_text)
        else:
            answer = llm_client.get_interview_answer(
                question_text,
                resume_text=resume_text,
                job_description=jd_text
            )

        if answer:
            answer_storage.set_complete_answer(
                question_text=question_text,
                answer_text=answer,
                metrics={'source': 'api-regen'}
            )
            print(f"[REGEN] Done ({len(answer)} chars)")
            return jsonify({'status': 'ok', 'answer': answer})
        else:
            return jsonify({'error': 'No answer generated'}), 500

    except Exception as e:
        print(f"[REGEN] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat_questions')
def get_chat_questions():
    """Return questions captured from Google Meet / Teams chat in this session."""
    with _chat_lock:
        items = list(reversed(_chat_session))  # newest first
    return jsonify({'items': items, 'count': len(items)})


@app.route('/api/save_to_db', methods=['POST'])
def save_to_db():
    """
    Quick-save a Q&A pair from the current interview session to the permanent DB.
    Input: { question: str, answer: str, source: str }
    """
    data = request.get_json()
    if not data or not data.get('question'):
        return jsonify({'error': 'question required'}), 400

    question = data.get('question', '').strip()
    answer   = data.get('answer', '').strip()
    source   = data.get('source', 'interview').strip()

    if not answer:
        return jsonify({'error': 'answer required'}), 400

    qa_id = qa_database.save_interview_qa(question, answer, source=source)
    if qa_id == -1:
        return jsonify({'status': 'exists', 'message': 'Question already in DB'})

    print(f"[SAVE] Saved interview Q to DB (id={qa_id}): {question[:60]}")
    return jsonify({'status': 'saved', 'id': qa_id})


@app.route('/api/local_url')
def local_url():
    """Return the local network URL for mobile QR code scanning."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = '127.0.0.1'
    port = request.host.split(':')[-1] if ':' in request.host else '8000'
    return jsonify({'url': f'http://{local_ip}:{port}/', 'ip': local_ip, 'port': port})


@app.route('/api/session_export')
def session_export():
    """Export current session Q&A as JSON download."""
    import datetime
    answers = answer_storage.get_all_answers()
    completed = [a for a in answers if a.get('is_complete') and a.get('answer')]
    # Reverse to get chronological order
    completed = list(reversed(completed))
    payload = {
        'exported_at': datetime.datetime.now().isoformat(),
        'total': len(completed),
        'session': completed,
    }
    response = Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=interview_session_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.json'}
    )
    return response


@app.route('/api/session_export_md')
def session_export_md():
    """Export current session Q&A as Markdown download."""
    import datetime
    answers = answer_storage.get_all_answers()
    completed = [a for a in answers if a.get('is_complete') and a.get('answer')]
    completed = list(reversed(completed))
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = [f'# Interview Session — {ts}\n', f'**{len(completed)} questions**\n\n---\n']
    for i, a in enumerate(completed, 1):
        lines.append(f'## Q{i}. {a.get("question", "").strip()}\n')
        lines.append(f'{a.get("answer", "").strip()}\n\n---\n')
    md = '\n'.join(lines)
    response = Response(
        md,
        mimetype='text/markdown',
        headers={'Content-Disposition': f'attachment; filename=interview_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.md'}
    )
    return response


@app.route('/api/bulk_save_to_db', methods=['POST'])
def bulk_save_to_db():
    """Bulk-save selected Q&A pairs to the permanent database."""
    data = request.get_json()
    if not data or 'items' not in data:
        return jsonify({'error': 'No items provided'}), 400

    items = data['items']
    saved = 0
    skipped = 0
    errors = []

    for item in items:
        q = (item.get('question') or '').strip()
        a = (item.get('answer') or '').strip()
        if not q or not a:
            skipped += 1
            continue
        try:
            qa_id = qa_database.save_interview_qa(q, a)
            if qa_id and qa_id > 0:
                saved += 1
            else:
                skipped += 1
        except Exception as e:
            errors.append(str(e))
            skipped += 1

    print(f"[BULK-SAVE] Saved {saved}, skipped {skipped}")
    return jsonify({'saved': saved, 'skipped': skipped, 'errors': errors[:5]})


def main():
    """Main entry point."""
    import sys as _sys
    # Parse --port/--host without argparse (avoids corrupted system .pyc issue)
    # Render.com passes PORT via environment variable
    _port = int(os.environ.get("PORT", DEFAULT_PORT))
    _host = '0.0.0.0'
    _argv = _sys.argv[1:]
    for _i, _a in enumerate(_argv):
        if _a == '--port' and _i + 1 < len(_argv):
            try: _port = int(_argv[_i + 1])
            except ValueError: pass
        elif _a.startswith('--port='):
            try: _port = int(_a.split('=', 1)[1])
            except ValueError: pass
        elif _a == '--host' and _i + 1 < len(_argv):
            _host = _argv[_i + 1]
        elif _a.startswith('--host='):
            _host = _a.split('=', 1)[1]

    class args:
        port = _port
        host = _host

    print("=" * 60)
    print("Drishi Pro - Web UI Server")
    print("=" * 60)
    print(f"\nServer: http://localhost:{args.port}  ← open this in Chrome")
    print(f"Mobile: http://<your-ip>:{args.port}")
    print(f"Data: {answer_storage.get_answers_file_path()}")
    print("\nPress Ctrl+C to stop\n")
    print("=" * 60 + "\n")

    try:
        # Use threaded mode for better SSE performance
        from werkzeug.serving import WSGIRequestHandler
        WSGIRequestHandler.protocol_version = "HTTP/1.1"  # Enable keep-alive
        app.run(host=args.host, port=args.port, debug=False, threaded=True, use_reloader=False)
    except OSError as e:
        if 'Address already in use' in str(e):
            print(f"\nError: Port {args.port} in use")
            print(f"Try: python3 web/server.py --port {args.port + 1}")
        else:
            print(f"\nError: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nServer stopped")
        sys.exit(0)


if __name__ == '__main__':
    main()
