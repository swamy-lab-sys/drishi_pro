"""
WSGI entry point for Render.com deployment.

Loads the real app in a background thread so gunicorn binds and
responds to /health immediately. Render's health check passes right
away while the heavy imports (scipy, sklearn, anthropic) load in the
background. Once ready, all requests are forwarded to the real app.
"""
import sys
import os
import threading
import traceback
from flask import Flask, jsonify

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'web'))

# State
_real_app = None
_import_error = None
_ready = threading.Event()

def _load():
    global _real_app, _import_error
    try:
        import server
        _real_app = server.app
        print("[wsgi] Real app loaded OK.", flush=True)
    except Exception:
        _import_error = traceback.format_exc()
        print(f"[wsgi] Import failed:\n{_import_error}", flush=True)
    finally:
        _ready.set()

threading.Thread(target=_load, daemon=False).start()

# Minimal startup Flask app — responds instantly while real app loads
_boot = Flask(__name__)

@_boot.route('/health')
def health():
    if not _ready.is_set():
        return jsonify({'status': 'loading'}), 200
    if _import_error:
        return jsonify({'status': 'error', 'detail': _import_error}), 200
    return jsonify({'status': 'ok'}), 200

@_boot.route('/', defaults={'path': ''})
@_boot.route('/<path:path>')
def loading_page(path):
    if not _ready.is_set():
        return '<h1>Drishi Pro is starting up...</h1><p>Please refresh in a moment.</p>', 503
    if _import_error:
        return f'<h1>Startup Error</h1><pre>{_import_error}</pre>', 500
    return '<h1>Ready — please refresh</h1>', 200


class _App:
    """Routes to real app once loaded, boot app until then."""
    def __call__(self, environ, start_response):
        target = _real_app if _real_app is not None else _boot
        return target(environ, start_response)


app = _App()
