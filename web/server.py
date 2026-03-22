try:
    from gevent import monkey
    monkey.patch_all()
    HAS_GEVENT = True
except ImportError:
    HAS_GEVENT = False

import sys
import os
import json
import time
from pathlib import Path
from flask import Flask, jsonify, request
from flask_sock import Sock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import ext_user_store as _ext_user_store

import config
import answer_storage
from app.services.settings_service import get_server_ip
from app.api.routes.coding import coding_bp
from app.api.routes.documents import documents_bp
from app.api.routes.interview import interview_bp
from app.api.routes.knowledge import knowledge_bp
from app.api.routes.live_capture import live_capture_bp
from app.api.routes.monitoring import monitoring_bp
from app.api.routes.ops import ops_bp
from app.api.routes.runtime import runtime_bp
from app.api.routes.security import security_bp
from app.api.routes.settings import settings_bp
from app.api.routes.ui import ui_bp
from app.api.routes.users import users_bp
from app.core.product import PRODUCT_NAME, TAGLINE, WEB_TITLE

# Configuration
DEFAULT_PORT = config.WEB_PORT
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
app.register_blueprint(coding_bp)
app.register_blueprint(documents_bp)
app.register_blueprint(interview_bp)
app.register_blueprint(knowledge_bp)
app.register_blueprint(live_capture_bp)
app.register_blueprint(monitoring_bp)
app.register_blueprint(ops_bp)
app.register_blueprint(runtime_bp)
app.register_blueprint(security_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(ui_bp)
app.register_blueprint(users_bp)


@app.context_processor
def inject_product_context():
    """Provide canonical product identity to every template."""
    return {
        'product_name': PRODUCT_NAME,
        'product_tagline': TAGLINE,
        'product_title': WEB_TITLE,
        'app_mode': config.APP_MODE,
        'cloud_mode': config.CLOUD_MODE,
    }

# Fix proxy headers so Flask sees correct host/scheme when behind ngrok/nginx
from werkzeug.middleware.proxy_fix import ProxyFix
# We use x_proto=1 to help flask-sock detect secure (wss) connections correctly behind ngrok
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

# WebSocket support (for cloud audio streaming from Chrome extension)
sock = Sock(app)

# ── Load active user profile on startup ───────────────────────────────────────
# main.py sets USER_ID_OVERRIDE in the env; web/server.py is a child process
# that inherits this var. We replicate main.py's user-loading logic here so
# that state.get_selected_user() works correctly for DB lookups in _handle_ws_text.
def _init_active_user():
    import qa_database as _qadb
    import state as _st

    # ── Inherit session_id from main.py so browser doesn't see a session flip ──
    # main.py writes its session_id to current_answer.json at startup.
    # If the web server uses its own session_id, every write flips the id and
    # the browser resets its card list on the next SSE reconnect.
    try:
        _ans_file = answer_storage.CURRENT_ANSWER_FILE
        if _ans_file.exists():
            import json as _json
            with open(_ans_file, 'r', encoding='utf-8') as _f:
                _d = _json.load(_f)
            if isinstance(_d, dict) and _d.get('session_id'):
                answer_storage._session_id = _d['session_id']
    except Exception:
        pass  # keep the generated id on any error

    # ── Load active user (USER_ID_OVERRIDE set by run.sh) ─────────────────────
    _uid_str = os.environ.get('USER_ID_OVERRIDE', '').strip()
    if _uid_str.isdigit():
        _user = _qadb.get_user(int(_uid_str))
        if _user:
            _st.set_selected_user(_user)
            from app.services.user_service import _persist_active_user
            _persist_active_user(_user)
            print(f"[Server] Active user loaded: {_user['name']} (role={_user.get('role','')})")
            return
    # Fallback: pick the first user in the DB so role is never empty
    try:
        users = _qadb.get_all_users()
        if users:
            _st.set_selected_user(dict(users[0]))
            from app.services.user_service import _persist_active_user
            _persist_active_user(dict(users[0]))
            print(f"[Server] Active user (fallback): {users[0]['name']} (role={users[0].get('role','')})")
    except Exception as _e:
        print(f"[Server] Could not load active user: {_e}")

_init_active_user()

# Disable Flask logging
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ── Secret code auth ──────────────────────────────────────────────────────────
SECRET_CODE = config.SECRET_CODE  # "" means no auth required (local mode)

# ── Extension ID lock ─────────────────────────────────────────────────────────
# Set EXTENSION_ID in .env to the Chrome extension's ID (found on chrome://extensions).
# When set, the server only accepts WebSocket connections whose Origin header matches
# "chrome-extension://<EXTENSION_ID>".  Empty = no restriction (local dev mode).
_ALLOWED_EXTENSION_ID = os.environ.get('EXTENSION_ID', '').strip()

def _check_extension_origin(req=None):
    """
    Return True if the WebSocket Origin is from the allowed extension.
    Chrome automatically sets Origin: chrome-extension://EXTENSION_ID on every
    request from an extension — this cannot be spoofed by web pages.
    Always passes when EXTENSION_ID env var is not set.
    """
    if not _ALLOWED_EXTENSION_ID:
        return True
    req = req or request
    origin = req.headers.get('Origin', '')
    allowed = f'chrome-extension://{_ALLOWED_EXTENSION_ID}'
    ok = origin == allowed
    if not ok:
        print(f"[Auth] Extension origin mismatch: got={repr(origin)} expected={repr(allowed)}")
    return ok

def _check_auth(req=None):
    """Return True if request is authenticated (or no secret code is set)."""
    if not SECRET_CODE:
        return True
    req = req or request
    # Accept token from header, query param, or JSON body
    token = (
        req.headers.get('X-Auth-Token', '')
        or req.args.get('token', '')
        or (req.get_json(silent=True) or {}).get('token', '')
    )
    return token == SECRET_CODE

def require_auth(f):
    """Decorator: returns 401 if secret code is wrong."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _check_auth():
            return jsonify({'error': 'Invalid or missing secret code', 'code': 401}), 401
        return f(*args, **kwargs)
    return decorated


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization,ngrok-skip-browser-warning'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    # Bypass ngrok browser warning for all responses (needed for remote access via ngrok tunnel)
    response.headers['ngrok-skip-browser-warning'] = 'true'
    # Static assets (JS/CSS/images) get 5-minute browser cache — reduces reload latency
    # API endpoints get no-cache so answers/state are always fresh
    path = request.path
    is_static = path.startswith('/static/') or path.endswith(('.js', '.css', '.png', '.ico', '.woff', '.woff2'))
    if is_static:
        response.headers['Cache-Control'] = 'public, max-age=300'
    else:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@app.route('/health')
def health():
    """Simple health check for Chrome extension diagnostics."""
    return jsonify({"status": "ok", "time": time.time()})


# ── Token Validation (Public — called by extension on login / session check) ───

@app.route('/api/validate')
def validate_token():
    """Validate a user_token and return profile info. No admin auth required."""
    token = request.args.get('user_token', '').strip()
    if not token:
        return jsonify({'valid': False, 'error': 'No token provided'}), 400
    user = _ext_user_store.get_user(token)
    if not user:
        return jsonify({'valid': False, 'error': 'Token not recognised'}), 401
    return jsonify({
        'valid': True,
        'name': user.get('name', ''),
        'role': user.get('role', ''),
        'coding_language': user.get('coding_language', 'python'),
    })


# ── Extension User Management (Admin API) ──────────────────────────────────────
# These endpoints let the admin create / manage per-user tokens for the extension.
# Auth: same SECRET_CODE as all other protected endpoints.

@app.route('/api/ext_users', methods=['GET'])
@require_auth
def list_ext_users():
    """List all extension users."""
    users = _ext_user_store.list_users()
    # Attach live connection status
    for u in users:
        u['connected'] = u['token'] in _ext_active_sessions
    return jsonify(users)


@app.route('/api/ext_users', methods=['POST'])
@require_auth
def create_ext_user():
    """Create a new extension user token."""
    body = request.get_json(silent=True) or {}
    token = (body.get('token') or '').strip()
    name  = (body.get('name')  or '').strip()
    role  = (body.get('role')  or '').strip()
    lang  = (body.get('coding_language') or 'python').strip()
    db_id = body.get('db_user_id', 1)
    ok, err = _ext_user_store.create_user(token, name, role, lang, db_id)
    if not ok:
        return jsonify({'error': err}), 400
    return jsonify({'ok': True, 'token': token}), 201


@app.route('/api/ext_users/<token>', methods=['PATCH'])
@require_auth
def update_ext_user(token):
    """Update an extension user (admin — all fields allowed)."""
    body = request.get_json(silent=True) or {}
    allowed = {'name', 'role', 'coding_language', 'db_user_id', 'active',
               'speed_preset', 'silence_duration', 'llm_model'}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not _ext_user_store.update_user(token, updates):
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'ok': True})


@app.route('/api/ext_users/<token>/settings', methods=['GET'])
def get_ext_user_settings(token):
    """Public — returns user settings for the self-service portal page."""
    cfg = _ext_user_store.get_user(token)
    if not cfg:
        return jsonify({'error': 'Invalid token'}), 404
    return jsonify({
        'token':            token,
        'name':             cfg.get('name', ''),
        'role':             cfg.get('role', ''),
        'db_user_id':       cfg.get('db_user_id', 1),
        'speed_preset':     cfg.get('speed_preset', 'balanced'),
        'silence_duration': cfg.get('silence_duration', 1.2),
        'llm_model':        cfg.get('llm_model', 'claude-haiku-4-5-20251001'),
        'connected':        token in _ext_active_sessions,
    })


@app.route('/api/ext_users/<token>/settings', methods=['PATCH'])
def update_ext_user_settings(token):
    """Public — user updates their own speed/model via the portal (token = auth)."""
    cfg = _ext_user_store.get_user(token)
    if not cfg:
        return jsonify({'error': 'Invalid token'}), 404
    body = request.get_json(silent=True) or {}
    _PRESET_SILENCE = {'fast': 0.6, 'balanced': 1.2, 'slow': 2.0, 'very_slow': 3.0}
    updates = {}
    if 'name' in body and body['name'].strip():
        updates['name'] = body['name'].strip()[:120]
    if 'role' in body:
        updates['role'] = body['role'].strip()[:120]
    if 'speed_preset' in body and body['speed_preset'] in _PRESET_SILENCE:
        updates['speed_preset']     = body['speed_preset']
        updates['silence_duration'] = _PRESET_SILENCE[body['speed_preset']]
    if 'llm_model' in body and body['llm_model'] in (
            'claude-haiku-4-5-20251001', 'claude-sonnet-4-6'):
        updates['llm_model'] = body['llm_model']
    if updates:
        _ext_user_store.update_user(token, updates)
    return jsonify({'ok': True})


@app.route('/api/ext_users/<token>', methods=['DELETE'])
@require_auth
def delete_ext_user(token):
    """Delete an extension user."""
    if not _ext_user_store.delete_user(token):
        return jsonify({'error': 'User not found'}), 404
    _ext_user_store.release_user_storage(token)
    _ext_active_sessions.discard(token)
    return jsonify({'ok': True})


# ── Extension login — token-based auth (no SECRET_CODE needed) ────────────────

@app.route('/api/ext/login', methods=['POST'])
def ext_login():
    """Extension calls this on startup with the user's token.
    Returns user config, monitor URL, and WebSocket URL.
    The token IS the authentication — no separate secret code needed.
    """
    body = request.get_json(silent=True) or {}
    token = (body.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'Token required'}), 400
    cfg = _ext_user_store.get_user(token)
    if not cfg:
        return jsonify({'error': 'Invalid or inactive token. Contact your admin.'}), 401
    # Build URLs based on request origin
    base = request.host_url.rstrip('/')
    monitor_url = f"{base}/monitor?user={token}"
    portal_url  = f"{base}/portal/{token}"
    ws_url      = f"ws://{request.host}/ws/audio?user_token={token}"
    return jsonify({
        'ok': True,
        'name':          cfg.get('name', ''),
        'role':          cfg.get('role', ''),
        'token':         token,
        'monitor_url':   monitor_url,
        'portal_url':    portal_url,
        'ws_url':        ws_url,
        'speed_preset':  cfg.get('speed_preset', 'balanced'),
        'llm_model':     cfg.get('llm_model', 'claude-haiku-4-5-20251001'),
        'stt_backend':   cfg.get('stt_backend', 'sarvam'),
    })


@app.route('/api/ext_users/<token>/usage')
def get_ext_user_usage(token):
    """Return recent usage log for a user (accessible by token or admin)."""
    cfg = _ext_user_store.get_user(token)
    if not cfg:
        return jsonify({'error': 'Invalid token'}), 404
    log = _ext_user_store.get_usage_log(token, limit=100)
    return jsonify({
        'token': token,
        'name':  cfg.get('name', ''),
        'total_questions': cfg.get('total_questions', 0),
        'total_llm_hits':  cfg.get('total_llm_hits', 0),
        'last_seen':       cfg.get('last_seen', ''),
        'log': log,
    })


@app.route('/api/admin/usage_summary')
@require_auth
def admin_usage_summary():
    """Admin endpoint — per-user usage summary for billing."""
    return jsonify(_ext_user_store.get_all_usage_summary())


# ── STT auto-learner admin endpoints ──────────────────────────────────────────

@app.route('/api/admin/stt_corrections')
@require_auth
def get_stt_corrections():
    """Admin — list all auto-learned STT corrections (most-hit first)."""
    try:
        import stt_learner as _sl
        return jsonify(_sl.get_all_corrections())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/stt_corrections/<int:correction_id>', methods=['DELETE'])
@require_auth
def delete_stt_correction(correction_id):
    """Admin — delete a learned correction by ID."""
    try:
        import stt_learner as _sl
        if _sl.delete_correction(correction_id):
            # Force hot-reload so the correction is removed immediately
            _sl.reload_into_stt(force=True)
            return jsonify({'ok': True})
        return jsonify({'error': 'Not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/stt_corrections', methods=['POST'])
@require_auth
def add_stt_correction():
    """Admin — manually add a correction (same as auto-learn but immediate)."""
    body = request.get_json(silent=True) or {}
    wrong = (body.get('wrong') or '').strip().lower()
    right = (body.get('right') or '').strip()
    if not wrong or not right:
        return jsonify({'error': 'wrong and right are required'}), 400
    try:
        import stt_learner as _sl
        _sl._init_table()
        _sl._upsert(wrong, right)
        _sl.reload_into_stt(force=True)
        return jsonify({'ok': True}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Coding Problem: Solve from Screenshot ─────────────────────────────────────

@app.route('/api/solve_from_image', methods=['POST'])
def solve_from_image():
    """Use Claude vision to extract and solve a coding problem from a screenshot.
    Accepts JSON: { image: <base64 string>, media_type: 'image/png' (optional) }
    Returns: { solution: <text>, status: 'ok' }
    """
    data = request.get_json(force=True, silent=True) or {}
    image_b64 = (data.get('image') or '').strip()
    if not image_b64:
        return jsonify({'error': 'image (base64) required'}), 400
    media_type = data.get('media_type', 'image/png')
    # Validate media type
    if media_type not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp'):
        media_type = 'image/png'
    try:
        from llm_client import solve_coding_from_image as _solve_img
        solution = _solve_img(image_b64, media_type=media_type)
        if not solution:
            return jsonify({'error': 'No solution generated'}), 500
        return jsonify({'solution': solution, 'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Post-Interview Debrief ─────────────────────────────────────────────────────

@app.route('/api/ext_users/<token>/debrief')
def get_user_debrief(token):
    """Generate a post-interview debrief from a user's recent session.
    Query params: limit=<int> (default 30), analyze=1 (default 0) for LLM analysis.
    Returns: { questions: [...], stats: {...}, analysis: <str> }
    """
    limit = min(int(request.args.get('limit', 30)), 100)
    do_analyze = request.args.get('analyze', '0') == '1'

    usage = _ext_user_store.get_usage_log(token, limit=limit)
    if not usage:
        return jsonify({'questions': [], 'stats': {'total': 0, 'db_hits': 0, 'llm_hits': 0, 'avg_ms': 0, 'db_rate': 0}, 'analysis': ''})

    # Build stats
    total = len(usage)
    db_hits  = sum(1 for u in usage if (u.get('source') or '').startswith('db'))
    llm_hits = sum(1 for u in usage if (u.get('source') or '') == 'llm')
    avg_ms   = sum(u.get('answer_ms') or 0 for u in usage) / max(total, 1)
    questions_list = [
        {'question': u.get('question', ''), 'source': u.get('source', ''),
         'answer_ms': u.get('answer_ms', 0), 'created_at': u.get('created_at', '')}
        for u in usage
    ]

    analysis = ''
    if do_analyze and usage:
        try:
            from llm_client import client as _llm_client, MODEL as _LLM_MODEL
            _qs_text = '\n'.join(f"- {u['question']}" for u in usage[:20])
            _resp = _llm_client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=400,
                messages=[{'role': 'user', 'content':
                    f"These are interview questions a candidate was asked:\n{_qs_text}\n\n"
                    f"In 3-4 sentences: what topics/domains were covered? What should they study more? "
                    f"Keep it actionable and concise."}],
            )
            analysis = _resp.content[0].text.strip()
        except Exception:
            pass

    return jsonify({
        'questions': questions_list,
        'stats': {
            'total': total,
            'db_hits': db_hits,
            'llm_hits': llm_hits,
            'avg_ms': round(avg_ms),
            'db_rate': round(db_hits / max(total, 1), 3),   # fraction 0.0–1.0 for frontend * 100
        },
        'analysis': analysis,
    })


# ── Track active extension user connections ────────────────────────────────────
# Simple set of tokens that currently have an open /ws/audio connection.
_ext_active_sessions: set = set()


def _strip_noise_prefix(text):
    """Remove STT hallucinated noise prefixes like 'DCSCO, ' before the real question.
    tiny.en hallucinates short all-caps tokens from background audio.
    Also applies to Sarvam client-side text if it picks up noise.
    Also strips Sarvam language-detection artifacts like 'English. ' at the start.
    """
    import re as _re
    text = text.strip()
    # Strip Sarvam language-detection prefix: "English. ", "Hindi. Explain..." etc.
    text = _re.sub(
        r'^(English|Hindi|Telugu|Tamil|Kannada|Malayalam|Marathi|Gujarati|Bengali|Punjabi)[,.\s]+',
        '', text, flags=_re.IGNORECASE
    ).strip()
    # Strip hallucinated all-caps tokens from tiny.en/base.en
    cleaned = _re.sub(r'^(?:[A-Z]{2,6}(?:\s+[A-Z]{2,6})*[,.\s]+)', '', text)
    if cleaned and _re.search(r'[a-z]', cleaned):
        return cleaned.strip()
    return text


def _dedup_garbled_stt(text: str) -> str:
    """
    Fix Sarvam STT artifact where it repeats question-words at the start.
    E.g. "Is which which higher what what is a generator?" → "What is a generator?"
    Also collapses any immediately-repeated word anywhere in the sentence.
    """
    import re as _re
    # 1. Collapse consecutive repeated words: "which which" → "which", "what what" → "what"
    text = _re.sub(r'\b(\w+)(\s+\1)+\b', r'\1', text, flags=_re.IGNORECASE)
    # 2. Strip junk filler-word prefix (2+ non-content words before real content)
    #    E.g. "Is which higher what is a generator?" → "is a generator?"
    _JUNK = r'(?:is|which|higher|lower|yeah|okay|um|uh|so|now|and|the|a|an)\s+'
    text = _re.sub(r'^(?:' + _JUNK + r'){2,}', '', text, flags=_re.IGNORECASE).strip()
    # 3. Ensure first character is capitalised
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


@app.route('/api/stt_config')
def stt_config():
    """Return per-user STT config to the Chrome extension.
    Authenticated by user_token (the user's personal token).
    Falls back to global STT config if no user_token provided.
    """
    user_token = request.args.get('user_token', '').strip()
    # Legacy: also accept SECRET_CODE for backward compat
    legacy_token = request.args.get('token', '')
    if not user_token and SECRET_CODE and legacy_token != SECRET_CODE:
        return jsonify({"error": "unauthorized"}), 401

    cfg = _ext_user_store.get_user(user_token) if user_token else {}
    stt_backend = (cfg or {}).get('stt_backend', config.STT_BACKEND)
    return jsonify({
        "sarvam_key":  config.SARVAM_API_KEY or "",
        "stt_backend": stt_backend,
        "stt_model":   (cfg or {}).get('stt_model', config.STT_MODEL),
    })


@app.route('/monitor-viewer')
def monitor_viewer_shortcut():
    """Shortcut for the monitor viewer with a cleaner URL."""
    return app.send_static_file('monitor_viewer/index.html')


@app.route('/v')
@app.route('/v/')
@app.route('/v/<session_id>')
@app.route('/v/<session_id>/<key>')
def monitor_viewer_ultra_shortcut(session_id='default', key='none'):
    """Ultra-short URL: /v/ or /v/603410
    Serves the viewer directly so the URL stays clean in the address bar.
    """
    return app.send_static_file('monitor_viewer/index.html')


# ── WebSocket audio endpoint (cloud mode) ─────────────────────────────────────
@sock.route('/ws/audio')
def audio_websocket(ws):
    """
    Receive raw PCM-16 audio chunks from Chrome extension.
    Each message = one speech segment (captured between silence gaps on client).
    Transcribes via cloud STT → runs answer pipeline → streams answer back.

    Auth: ?token=<SECRET_CODE>&user_token=<USER_TOKEN> in query string.
    When user_token is provided each user gets an isolated pipeline + answer storage.
    When omitted the global (system audio) pipeline is used — backward compatible.
    """
    import numpy as np

    # ── Auth: user_token IS the authentication for extension users ───────────
    # No separate SECRET_CODE needed — admin creates the token and shares it.
    # Legacy SECRET_CODE path still supported for backward compat.
    user_token = request.args.get('user_token', '').strip()
    legacy_token = request.args.get('token', '')

    # Extension ID lock (optional extra security)
    if not _check_extension_origin():
        ws.send(json.dumps({'type': 'error', 'message': 'Unauthorized extension origin.'}))
        return

    # If legacy SECRET_CODE is set and this looks like a legacy connection (no user_token), enforce it
    if not user_token and SECRET_CODE and legacy_token != SECRET_CODE:
        ws.send(json.dumps({'type': 'error', 'message': 'Invalid secret code.'}))
        return

    # ── Per-user session resolution ──────────────────────────────────────────
    _ext_session = None   # None → global (system audio) mode

    if user_token:
        user_cfg = _ext_user_store.get_user(user_token)
        if not user_cfg:
            ws.send(json.dumps({
                'type': 'error',
                'message': f'Token "{user_token}" not recognised. Ask your admin to create your account.',
            }))
            return
        _ext_session = {
            'token':    user_token,
            'cfg':      user_cfg,
            'storage':  _ext_user_store.get_user_storage(user_token),
            'user_role': user_cfg.get('role', ''),
            'db_user_id': user_cfg.get('db_user_id', 1),
            'llm_model': user_cfg.get('llm_model', config.LLM_MODEL),
            'silence_duration': user_cfg.get('silence_duration', 1.2),
        }
        _ext_active_sessions.add(user_token)
        user_label = f"{user_cfg.get('name', user_token)} ({user_cfg.get('role', 'unknown role')})"
        print(f"[WS/audio] User connected: {user_label} | token={user_token} | from {request.remote_addr}")
        ws.send(json.dumps({
            'type': 'connected',
            'message': f'{PRODUCT_NAME} ready — Hello {user_cfg.get("name", user_token)}! Listening...',
            'user': {'name': user_cfg.get('name'), 'role': user_cfg.get('role'), 'token': user_token},
        }))
    else:
        print(f"[WS/audio] Client connected from {request.remote_addr} (global/system-audio mode)")
        ws.send(json.dumps({'type': 'connected', 'message': f'{PRODUCT_NAME} ready. Listening...'}))

    import stt as _stt
    import re
    import gevent
    import gevent.queue as _gq

    SAMPLE_RATE        = 16000
    SILENCE_THRESHOLD  = 0.008   # RMS below this = silence
    # Per-user silence duration (each extension chunk ≈ 250ms)
    _sil_dur = (_ext_session or {}).get('silence_duration', 1.2)
    SILENCE_CHUNKS_END = max(2, round(_sil_dur / 0.25))  # e.g. 1.2s→5, 0.6s→2, 3.0s→12
    PRE_ROLL_CHUNKS    = 3       # ~0.75s pre-roll before first speech chunk
    MIN_SPEECH_SAMPLES = 12000   # 0.75s minimum before transcribing
    MAX_SPEECH_SAMPLES = 160000  # 10s speech safety cap

    pre_roll       = []
    audio_chunks   = []
    buffer_samples = 0
    in_speech      = False
    speech_seen    = False
    silence_count  = 0
    chunk_count    = 0

    def _reset_buffer():
        nonlocal pre_roll, audio_chunks, buffer_samples, in_speech, speech_seen, silence_count
        pre_roll = []; audio_chunks = []; buffer_samples = 0
        in_speech = False; speech_seen = False; silence_count = 0

    # _strip_noise_prefix / _dedup_garbled_stt are defined at module level

    # ── Fragment buffer: merge text fragments from fast/slow speakers ──────────
    # When the extension's silence-detection splits one question into multiple
    # chunks (e.g. "How can you" + "achieve multiple inheritance?"), we collect
    # them in a rolling window and flush as one merged question.
    # FRAG_WINDOW_SECS scales with the user's silence_duration:
    #   fast (0.6s) → 1.2s window, balanced (1.2s) → 2.0s, slow/very_slow → 2.5s
    _frag_base = {0.6: 1.2, 1.2: 2.0, 2.0: 2.5, 3.0: 2.5}
    FRAG_WINDOW_SECS = _frag_base.get(_sil_dur, 2.0)

    _frag_parts = []          # accumulated text pieces
    _frag_timer = [None]      # greenlet handle (list so closure can mutate)

    # ── Serialized question queue: one at a time ───────────────────────────────
    # Prevents concurrent LLM calls racing on answer_storage state.
    _q_queue = _gq.Queue()

    def _flush_fragment():
        """Timer-fired: merge buffered fragments and enqueue for processing."""
        merged = ' '.join(_frag_parts).strip()
        _frag_parts.clear()
        _frag_timer[0] = None
        if not merged:
            return
        merged = _dedup_garbled_stt(merged)
        print(f"[WS/frag] Flushed ({FRAG_WINDOW_SECS}s window): {repr(merged)}")
        _q_queue.put(merged)

    def _on_text_question(text: str):
        """Buffer a fragment; reset the merge-window timer."""
        text = _strip_noise_prefix(text).strip()
        if not text:
            return

        # Skip trailing noise: single/double word fragments that arrive after a
        # complete question (ending with ?) — e.g. Sarvam echoes "Python." after
        # "What is polymorphism in Python?" as a separate STT segment.
        if _frag_parts:
            combined = ' '.join(_frag_parts).rstrip()
            if combined.endswith('?') and len(text.split()) <= 2:
                print(f"[WS/frag] Trailing noise skipped: {repr(text)}")
                # Flush what we have immediately — complete question already buffered
                if _frag_timer[0] is not None:
                    _frag_timer[0].kill()
                    _frag_timer[0] = None
                _flush_fragment()
                return

        if _frag_timer[0] is not None:
            _frag_timer[0].kill()
        _frag_parts.append(text)
        # Use a shorter window once we have a complete-sounding question
        combined = ' '.join(_frag_parts)
        window = 1.2 if combined.rstrip().endswith('?') else FRAG_WINDOW_SECS
        _frag_timer[0] = gevent.spawn_later(window, _flush_fragment)
        print(f"[WS/frag] Buffered: {repr(text)} (window={window}s)")

    def _question_worker():
        """Sequential processor — prevents concurrent LLM / answer_storage races."""
        while True:
            text = _q_queue.get()
            try:
                _handle_ws_text(ws, text, ext_session=_ext_session)
            except Exception as _qe:
                print(f"[WS/queue] Error: {_qe}")

    def _split_questions(text):
        """Split merged transcription on sentence boundaries (? or .) followed by capital."""
        parts = re.split(r'(?<=[?.!])\s+(?=[A-Z])', text.strip())
        return [p.strip() for p in parts if len(p.strip()) >= 4]

    def _process_audio(audio):
        """STT + pipeline — runs in a gevent greenlet so receive loop never blocks."""
        try:
            ws.send(json.dumps({'type': 'status', 'message': 'Transcribing...'}))
            text, confidence = _stt.transcribe(audio)
            print(f"[WS/audio] STT raw: {repr(text)} (conf={confidence:.2f}, samples={len(audio)})")
            if not text or len(text.strip()) < 4:
                ws.send(json.dumps({'type': 'status', 'message': 'Listening...'}))
                return
            text = _strip_noise_prefix(text)
            print(f"[WS/audio] STT clean: {repr(text)}")
            # Split on sentence boundaries — handles two merged questions
            questions = _split_questions(text)
            for q in questions:
                ws.send(json.dumps({'type': 'transcript', 'text': q}))
                print(f"[WS/audio] → pipeline: {repr(q)}")
                _handle_ws_text(ws, q, ext_session=_ext_session)
        except Exception as e:
            import traceback
            print(f"[WS/audio] STT/pipeline error: {e}\n{traceback.format_exc()}")
            try:
                ws.send(json.dumps({'type': 'error', 'message': f'Processing error: {e}'}))
            except Exception:
                pass

    def _flush_buffer():
        """Snapshot current buffer, reset immediately, spawn STT in background greenlet."""
        if not speech_seen or buffer_samples < MIN_SPEECH_SAMPLES:
            _reset_buffer()
            return
        audio = np.concatenate(audio_chunks)
        _reset_buffer()   # ← reset NOW so receive loop collects next question immediately
        gevent.spawn(_process_audio, audio)

    # Start the sequential question processor (one LLM call at a time)
    gevent.spawn(_question_worker)

    try:
        while True:
            try:
                data = ws.receive(timeout=60)
            except Exception as e:
                print(f"[WS/audio] Receive error (disconnect): {e}")
                break
            if data is None:
                who = f"user={user_token}" if user_token else "global"
                print(f"[WS/audio] Client disconnected ({who})")
                break

            # ── Control messages (JSON strings) ──────────────────────────────
            if isinstance(data, str):
                try:
                    msg = json.loads(data)
                    if msg.get('type') == 'ping':
                        ws.send(json.dumps({'type': 'pong'}))
                    elif msg.get('type') == 'text_question':
                        _on_text_question(msg.get('text', ''))
                except Exception:
                    pass
                continue

            # ── Binary: PCM-16 mono 16 kHz audio bytes ───────────────────────
            chunk_count += 1
            chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            if len(chunk) < 256:
                continue

            chunk_rms = float(np.sqrt(np.mean(chunk ** 2)))
            is_silent = chunk_rms < SILENCE_THRESHOLD

            if not is_silent:
                if not speech_seen:
                    for pr in pre_roll:
                        audio_chunks.append(pr)
                        buffer_samples += len(pr)
                    pre_roll = []
                in_speech = True; speech_seen = True; silence_count = 0
                audio_chunks.append(chunk); buffer_samples += len(chunk)
            else:
                if speech_seen:
                    silence_count += 1
                    audio_chunks.append(chunk); buffer_samples += len(chunk)
                else:
                    pre_roll.append(chunk)
                    if len(pre_roll) > PRE_ROLL_CHUNKS:
                        pre_roll.pop(0)

            if speech_seen and (chunk_count % 10 == 0 or not is_silent):
                print(f"[WS/audio] chunk #{chunk_count} rms={chunk_rms:.4f} "
                      f"silence_count={silence_count} buffered={buffer_samples/SAMPLE_RATE:.1f}s")

            end_of_speech = in_speech and silence_count >= SILENCE_CHUNKS_END
            buffer_full   = buffer_samples >= MAX_SPEECH_SAMPLES

            if end_of_speech or buffer_full:
                reason = "end-of-speech" if end_of_speech else "buffer-cap"
                print(f"[WS/audio] Flush ({reason}): {buffer_samples/SAMPLE_RATE:.2f}s → spawning STT")
                _flush_buffer()   # resets buffer instantly, STT runs in background

    finally:
        # Clean up active session tracking on disconnect
        if user_token:
            _ext_active_sessions.discard(user_token)


# ── Follow-up detection ────────────────────────────────────────────────────────
import re as _re_server
_FOLLOWUP_RE = _re_server.compile(
    r'^\s*(can you |could you |please )?(elaborate|tell me more|give (me )?an example|'
    r'explain (further|more|that|it)|go on|continue|what do you mean|'
    r'how so|why so|say more|expand on (that|this|it)?|expand that|'
    r'dig deeper|be more specific|clarify|more details?|'
    r'what about that|and then what|i mean|like what|for instance)\b',
    _re_server.IGNORECASE,
)

# Per-session last Q+A for follow-up context  {token or 'global': (question, answer)}
# Capped at 500 entries to prevent unbounded growth with many extension users
_last_qa_per_session: dict = {}
_LAST_QA_MAX = 500

def _set_last_qa(key: str, value: tuple) -> None:
    """Thread-safe bounded insert into _last_qa_per_session."""
    _last_qa_per_session[key] = value
    if len(_last_qa_per_session) > _LAST_QA_MAX:
        # Drop oldest key (dict insertion order preserved in Python 3.7+)
        try:
            oldest = next(iter(_last_qa_per_session))
            if oldest != 'global':
                del _last_qa_per_session[oldest]
        except StopIteration:
            pass


def _is_followup_question(text: str) -> bool:
    # Regex must match AND the phrase must be short (pure follow-up, not a new question)
    # Use < 9 words to avoid catching "Can you elaborate on X concept in detail?" style
    return bool(_FOLLOWUP_RE.search(text)) and len(text.split()) < 9


# ── Per-user resume context builder ───────────────────────────────────────────

def _load_ext_user_resume(ext_session: dict) -> str:
    """Load the resume text for an extension user from their DB user record or upload path."""
    if not ext_session:
        return ""
    db_user_id = ext_session.get('db_user_id', 1)
    try:
        import qa_database as _qdb
        user = _qdb.get_user(db_user_id)
        if not user:
            return ""
        # Prefer stored resume_text, fall back to resume_path file
        resume_text = (user.get('resume_text') or '').strip()
        if resume_text:
            return resume_text
        resume_path = (user.get('resume_path') or '').strip()
        if resume_path:
            from pathlib import Path as _P
            rp = _P(resume_path)
            if rp.exists():
                from user_manager import extract_pdf_text
                return extract_pdf_text(str(rp))
    except Exception:
        pass
    return ""


def _build_ext_user_context(ext_session: dict) -> str:
    """Build an active_user_context string for an extension user (mirrors build_resume_context_for_llm)."""
    if not ext_session:
        from user_manager import build_resume_context_for_llm
        return build_resume_context_for_llm()
    cfg = ext_session.get('cfg', {})
    name = cfg.get('name', '')
    role = ext_session.get('user_role', cfg.get('role', ''))
    resume_text = _load_ext_user_resume(ext_session)

    parts = []
    if name:
        parts.append(f"CANDIDATE NAME: {name}")
    if role:
        parts.append(f"ROLE APPLYING FOR: {role}")
    if resume_text:
        # Trim resume to avoid huge prompts — first 2000 chars covers most relevant info
        parts.append(f"YOUR RESUME (answer as this person):\n{resume_text[:2000]}")
    return '\n'.join(parts)


def _handle_ws_text(ws, text: str, ext_session: dict = None):
    """Run the answer pipeline for a transcribed/typed question.
    Fully mirrors main.handle_question():
      cache → set_processing_question → DB (user_role) → intent correction → LLM

    ext_session (dict | None):
        None  → global mode (system audio) — uses module-level answer_storage + state
        dict  → per-user extension mode — keys: 'storage', 'user_role', 'cfg', 'token'

    Answers go to the dashboard via storage + event_bus (same as local path).
    """
    from question_validator import validate_question, is_code_request
    import answer_cache as _cache
    import qa_database as _qadb

    # ── Select storage and user_role based on mode ─────────────────────────────
    if ext_session:
        _storage  = ext_session['storage']   # UserAnswerStorage instance
        _user_role = ext_session.get('user_role', '')
        _session_label = f"[user={ext_session.get('token', '?')}]"
    else:
        import answer_storage as _storage
        import state as _state
        _active_user = _state.get_selected_user()
        _user_role   = (_active_user or {}).get("role", "") if _active_user else ""
        _session_label = "[global]"

    text = _strip_noise_prefix(text.strip())
    if not text:
        return

    # Validate — mirrors main.py validate_question gate
    ok, cleaned, reason = validate_question(text)
    if not ok:
        print(f"[WS/text]{_session_label} Rejected ({reason}): {repr(text)}")
        try:
            ws.send(json.dumps({'type': 'rejected', 'reason': reason or 'Not an interview question'}))
        except Exception:
            pass
        return

    # ── Follow-up detection: expand previous answer instead of new DB lookup ──
    _session_key = ext_session.get('token', 'global') if ext_session else 'global'
    if _is_followup_question(cleaned):
        _prev = _last_qa_per_session.get(_session_key)
        if _prev:
            prev_q, prev_a = _prev
            followup_prompt = (
                f"Previous question: {prev_q}\n"
                f"Previous answer: {prev_a}\n\n"
                f"Follow-up request: {cleaned}\n\n"
                f"Expand on the previous answer with a specific example or more detail. "
                f"Keep the same 3-bullet format. Stay in character as the resume person."
            )
            print(f"[WS/text]{_session_label} Follow-up detected — expanding previous answer")
            try:
                ws.send(json.dumps({'type': 'status', 'message': 'Elaborating...'}))
            except Exception:
                pass
            try:
                import llm_client as _llm2
                _llm2.clear_session()
                _fu_ctx = _build_ext_user_context(ext_session) if ext_session else ""
                _fu_chunks = []
                _storage.set_processing_question(cleaned)
                for _chunk in _llm2.get_streaming_interview_answer(
                    followup_prompt, active_user_context=_fu_ctx,
                    model=(ext_session.get('llm_model') if ext_session else None),
                ):
                    _fu_chunks.append(_chunk)
                    _storage.append_answer_chunk(_chunk)
                _llm2.clear_session()
                from llm_client import humanize_response as _humanize2
                _fu_answer = _humanize2(''.join(_fu_chunks))
                if _fu_answer:
                    _storage.set_complete_answer(cleaned, _fu_answer, {'source': 'llm-followup'})
                    try:
                        ws.send(json.dumps({'type': 'answer', 'question': cleaned,
                                            'answer': _fu_answer, 'source': 'llm-followup', 'is_complete': True}))
                    except Exception:
                        pass
                    _set_last_qa(_session_key, (cleaned, _fu_answer))
                    if ext_session:
                        _ext_user_store.log_usage(
                            ext_session.get('token', ''), cleaned, 'llm',
                            int((time.time() - time.time()) * 1000),
                        )
            except Exception as _fue:
                print(f"[WS/text]{_session_label} Follow-up LLM error: {_fue}")
            return

    def _send_ws(question, answer, source):
        """Send answer back over the WebSocket to the Chrome extension."""
        try:
            ws.send(json.dumps({'type': 'answer', 'question': question,
                                'answer': answer, 'source': source, 'is_complete': True}))
        except Exception:
            pass  # WS may have closed — dashboard still gets the answer via storage

    _t0_total = time.time()

    def _finish(question, answer, metrics):
        """Save answer to dashboard (storage + disk), send over WS, track usage."""
        _storage.set_complete_answer(question, answer, metrics)
        src = (metrics or {}).get('source', 'unknown')
        _send_ws(question, answer, src)
        # Store for follow-up context
        _set_last_qa(_session_key, (question, answer))
        # Track usage for billing
        if ext_session:
            _ms = int((time.time() - _t0_total) * 1000)
            _src_simple = 'llm' if 'llm' in src else ('cache' if 'cache' in src else 'db')
            _ext_user_store.log_usage(ext_session.get('token', ''), question, _src_simple, _ms)

    # Step 0: Introduction question — return stored self_introduction instantly
    from user_manager import is_introduction_question as _is_intro
    if _is_intro(cleaned):
        _intro_text = ""
        if ext_session:
            # ext-user: load self_introduction from their linked db_user_id profile
            try:
                import qa_database as _qdb2
                _db_uid = ext_session.get('db_user_id') or ext_session.get('cfg', {}).get('db_user_id', 1)
                _profile = _qdb2.get_user(_db_uid)
                _intro_text = (_profile.get('self_introduction') or '').strip() if _profile else ''
            except Exception:
                pass
        else:
            # global mode: use the selected user profile
            import state as _state2
            _sel = _state2.get_selected_user()
            _intro_text = (_sel.get('self_introduction') or '').strip() if _sel else ''
        if _intro_text:
            print(f"[WS/text]{_session_label} Intro question → stored self_introduction")
            _storage.set_processing_question(cleaned)
            _finish(cleaned, _intro_text, {'source': 'intro'})
            return

    # Step 1: Cache (role-scoped to prevent cross-user cache leakage)
    cached = _cache.get_cached_answer(cleaned, role=_user_role)
    if cached:
        print(f"[WS/text]{_session_label} Cache hit: {repr(cleaned)}")
        _storage.set_processing_question(cleaned)
        _finish(cleaned, cached, {'source': 'cache'})
        return

    wants_code = is_code_request(cleaned)

    # Step 2: Notify dashboard — creates placeholder card
    _storage.set_processing_question(cleaned)

    # Step 3: DB lookup with user_role
    _db_t0 = time.time()
    db_result = _qadb.find_answer(cleaned, want_code=wants_code, user_role=_user_role)
    _db_ms = (time.time() - _db_t0) * 1000

    if db_result:
        db_answer, db_score, db_id = db_result
        print(f"[WS/text]{_session_label} DB hit {_db_ms:.0f}ms score={db_score:.2f} id={db_id}: {repr(cleaned)}")
        _cache.cache_answer(cleaned, db_answer, role=_user_role)
        _finish(cleaned, db_answer, {'source': f'db-{db_id}', 'db_score': round(db_score, 2)})
        return

    print(f"[WS/text]{_session_label} DB miss {_db_ms:.0f}ms (role={repr(_user_role)}): {repr(cleaned)}")

    # Step 4: Intent correction + second DB lookup
    from question_validator import _has_tech_term as _htc
    if not _htc(cleaned.lower()):
        try:
            from main import correct_question_intent
            _corrected = correct_question_intent(cleaned)
            if _corrected and _corrected.lower() != cleaned.lower():
                print(f"[WS/text]{_session_label} Intent corrected: {repr(cleaned)} → {repr(_corrected)}")
                # Auto-learn: store this correction for future STT without LLM overhead
                try:
                    import stt_learner as _sl
                    _sl.submit_correction(cleaned, _corrected)
                except Exception:
                    pass
                _storage.update_current_question(_corrected)
                _db2 = _qadb.find_answer(_corrected, want_code=wants_code, user_role=_user_role)
                if _db2:
                    db_answer, db_score, db_id = _db2
                    print(f"[WS/text]{_session_label} DB hit after correction score={db_score:.2f} id={db_id}")
                    _cache.cache_answer(cleaned, db_answer, role=_user_role)
                    _cache.cache_answer(_corrected, db_answer, role=_user_role)
                    _finish(_corrected, db_answer, {'source': f'db-{db_id}', 'db_score': round(db_score, 2)})
                    return
                cleaned = _corrected
        except Exception as _e:
            print(f"[WS/text]{_session_label} Intent correction skipped: {_e}")

    # Step 5: LLM fallback
    print(f"[WS/text]{_session_label} → LLM: {repr(cleaned)}")
    try:
        ws.send(json.dumps({'type': 'status', 'message': 'Generating answer...'}))
    except Exception:
        pass
    try:
        import llm_client as _llm
        from llm_client import (get_coding_answer, get_streaming_interview_answer,
                                 classify_question_type)
        from user_manager import build_resume_context_for_llm
        _llm.clear_session()

        # ── Resume-aware context: use per-user resume for ext sessions ────────
        if ext_session:
            user_ctx = _build_ext_user_context(ext_session)
        else:
            user_ctx = build_resume_context_for_llm()

        # ── Question type classification: pick the right answer strategy ──────
        q_type = classify_question_type(cleaned)
        print(f"[WS/text]{_session_label} question_type={q_type}: {repr(cleaned[:50])}")

        if wants_code or q_type == 'coding':
            answer = get_coding_answer(cleaned, user_context=user_ctx)
            _llm.clear_session()
            if answer:
                _cache.cache_answer(cleaned, answer, role=_user_role)
                _finish(cleaned, answer, {'source': 'llm', 'q_type': q_type})
            else:
                try:
                    ws.send(json.dumps({'type': 'error', 'message': 'Could not generate answer'}))
                except Exception:
                    pass
        else:
            # Streaming: push incremental chunks to storage in real-time
            _per_user_model = ext_session.get('llm_model') if ext_session else None
            from llm_client import humanize_response as _humanize
            chunks = []
            for chunk in get_streaming_interview_answer(
                cleaned, active_user_context=user_ctx,
                model=_per_user_model, question_type=q_type,
            ):
                chunks.append(chunk)
                _storage.append_answer_chunk(chunk)
            _llm.clear_session()
            # Humanize final answer (removes bold/formal words/AI leaks) — same as main.py
            answer = _humanize(''.join(chunks))
            if answer:
                _cache.cache_answer(cleaned, answer, role=_user_role)
                _finish(cleaned, answer, {'source': 'llm', 'q_type': q_type})
            else:
                try:
                    ws.send(json.dumps({'type': 'error', 'message': 'Could not generate answer'}))
                except Exception:
                    pass
    except Exception as e:
        import traceback
        print(f"[WS/text]{_session_label} LLM error: {e}\n{traceback.format_exc()}")
        try:
            ws.send(json.dumps({'type': 'error', 'message': f'LLM error: {e}'}))
        except Exception:
            pass


# ── Browser Monitor WebSocket endpoint ────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from monitor_manager import MonitorManager
from monitor_control import (
    request_control, respond_control, disable_control, handle_control_command,
)

_monitor_manager = MonitorManager()


@sock.route('/ws/monitor')
def monitor_websocket(ws):
    """
    WebSocket relay for browser monitoring (browser events, WebRTC signaling,
    remote control). Compatible with the remote-monitor Chrome extension.
    """
    if not getattr(config, 'ENABLE_MONITORING', True):
        try:
            ws.send(json.dumps({
                "type": "error",
                "message": "Monitoring is disabled on this server for performance reasons. Restart with monitoring enabled to use this feature."
            }))
            time.sleep(1)
        except: pass
        return

    session_id: str | None = None
    role: str | None = None
    client_id: str | None = None

    # Handshake debug (non-blocking log, no stdout flush)
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    try:
        import debug_logger as _dlog
        _dlog.log(f"[WS/monitor] connect ip={ip}", "DEBUG")
    except Exception:
        pass

    # Per-connection heartbeat for non-extension viewers (dashboard.js doesn't ping)
    import threading as _threading
    _ws_alive = [True]

    def _keepalive():
        while _ws_alive[0]:
            time.sleep(45)
            if not _ws_alive[0]:
                break
            try:
                ws.send(json.dumps({"type": "ping"}))
            except Exception:
                break

    _ping_thread = _threading.Thread(target=_keepalive, daemon=True)
    _ping_thread.start()

    try:
        while True:
            try:
                data = ws.receive(timeout=300)
            except Exception as e:
                if "1002" in str(e) or "handshake" in str(e).lower():
                    print(f"[WS] Protocol Error: {e}")
                break
            if data is None:
                print("[WS] Connection closed normally")
                break

            try:
                payload = json.loads(data)
            except Exception:
                continue

            try:
                payload_type = payload.get('type')

                if payload_type == 'agent_connect':
                    session_id = payload.get('session_id') or 'default'
                    role = 'agent'
                    reg = _monitor_manager.register_agent(ws, session_id)
                    client_id = reg.get('client_id')
                    print(f"[WS] AGENT_CONNECT: session={session_id}, agent_id={client_id}")
                    ws.send(json.dumps(reg))
                    continue

                if payload_type == 'register':
                    role = payload.get('role', 'viewer')
                    session_id = payload.get('session_id') or payload.get('sessionId') or 'default'
                    print(f"[WS] REGISTER: role={role}, session={session_id}")
                    if role == 'sender':
                        reg = _monitor_manager.register_sender(ws, session_id)
                        # Persist active session so agent_host.py can auto-detect it
                        try:
                            import pathlib
                            _sid_path = pathlib.Path.home() / ".drishi" / "session_id"
                            _sid_path.parent.mkdir(parents=True, exist_ok=True)
                            _sid_path.write_text(session_id)
                        except Exception:
                            pass
                        # Notify any connected agents to switch to this session
                        _monitor_manager.broadcast_to_all_agents({
                            'type': 'session_change',
                            'session_id': session_id,
                        })
                    else:
                        reg = _monitor_manager.register_viewer(ws, session_id)
                    client_id = reg.get('client_id')
                    ws.send(json.dumps(reg))
                    continue

                if payload_type == 'ping':
                    ws.send(json.dumps({
                        'type': 'pong',
                        'session_id': session_id,
                        'timestamp': payload.get('timestamp'),
                    }))
                    continue

                if payload_type == 'signal':
                    if role not in {'sender', 'viewer'} or not session_id or not client_id:
                        ws.send(json.dumps({'type': 'error', 'message': 'Register before signaling.'}))
                        continue
                    signal_payload = {
                        'type': 'signal',
                        'signal_type': payload.get('signal_type', 'unknown'),
                        'session_id': session_id,
                        'viewer_id': payload.get('viewer_id'),
                        'data': payload.get('data', {}),
                    }
                    if role == 'sender':
                        viewer_id = payload.get('viewer_id')
                        if viewer_id:
                            _monitor_manager.send_to_viewer(session_id, viewer_id, signal_payload)
                    else:
                        signal_payload['viewer_id'] = client_id
                        _monitor_manager.send_to_sender(session_id, signal_payload)
                    continue

                if payload_type == 'control_request':
                    if role != 'viewer' or not session_id or not client_id:
                        ws.send(json.dumps({'type': 'error', 'message': 'Register as viewer first.'}))
                        continue
                    provided_secret = payload.get('secret')
                    print(f"[WS] CONTROL_REQUEST: session={session_id}, viewer={client_id}, key={provided_secret}")
                    request_control(_monitor_manager, session_id, client_id, payload)
                    continue

                if payload_type == 'control':
                    if role != 'viewer' or not session_id or not client_id:
                        ws.send(json.dumps({'type': 'error', 'message': 'Register as viewer first.'}))
                        continue
                    handle_control_command(_monitor_manager, session_id, client_id, payload)
                    continue

                if payload_type == 'control_response':
                    if role != 'sender' or not session_id:
                        ws.send(json.dumps({'type': 'error', 'message': 'Register as sender first.'}))
                        continue
                    viewer_id_resp = payload.get('viewer_id')
                    approved_resp = bool(payload.get('approved'))
                    print(f"[WS] CONTROL_RESPONSE: session={session_id}, viewer={viewer_id_resp}, approved={approved_resp}")
                    respond_control(
                        _monitor_manager, session_id,
                        viewer_id_resp, approved_resp
                    )
                    continue

            except Exception as e:
                print(f"[WS] Error processing {payload_type if 'payload_type' in locals() else 'unknown'} message: {e}")
                import traceback
                traceback.print_exc()

            if payload_type == 'control_disable':
                if role != 'sender' or not session_id:
                    ws.send(json.dumps({'type': 'error', 'message': 'Register as sender first.'}))
                    continue
                disable_control(_monitor_manager, session_id)
                continue

            if payload_type == 'event':
                if role != 'sender' or not session_id:
                    ws.send(json.dumps({'type': 'error', 'message': 'Register as sender first.'}))
                    continue
                event_type = payload.get('event_type', 'unknown')
                event_data = payload.get('data', {})
                _monitor_manager.apply_event(session_id, event_type, event_data)
                # Skip metrics() lock on high-frequency mousemove events (10/sec) to avoid
                # lock contention. Metrics are still sent on lower-frequency events.
                include_metrics = event_type != 'mousemove'
                broadcast_payload = {
                    'type': 'event',
                    'session_id': session_id,
                    'event_type': event_type,
                    'data': event_data,
                }
                if include_metrics:
                    broadcast_payload['metrics'] = _monitor_manager.metrics(session_id)
                _monitor_manager.broadcast(session_id, broadcast_payload)

    finally:
        _ws_alive[0] = False
        _monitor_manager.unregister(ws, session_id)

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
    print(f"{PRODUCT_NAME} - Web UI Server")
    print("=" * 60)
    _lan_ip = get_server_ip()
    print(f"\nServer: http://localhost:{args.port}  ← open this in Chrome")
    print(f"Mobile: http://{_lan_ip}:{args.port}")
    print(f"Data: {answer_storage.get_answers_file_path()}")
    print("\nPress Ctrl+C to stop\n")
    print("=" * 60 + "\n")

    try:
        if HAS_GEVENT:
            from gevent.pywsgi import WSGIServer
            # Note: We do NOT use handler_class=WebSocketHandler here because flask-sock
            # uses simple-websocket which handles the upgrade and framing itself.
            # Using both causes '1002 WebSocket Protocol Error' (Invalid frame header).
            server = WSGIServer((args.host, args.port), app, log=None, error_log=None)
            print(f"[Server] Gevent-WSGI listening on {args.port} (Proxy-optimized)")
            server.serve_forever()
        else:
            print(f"[Server] gevent not installed — using Werkzeug (remote access may be unstable)")
            from werkzeug.serving import WSGIRequestHandler
            WSGIRequestHandler.protocol_version = "HTTP/1.1"
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
