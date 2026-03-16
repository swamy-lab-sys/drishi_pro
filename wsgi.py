"""
WSGI entry point for Render.com deployment.
Falls back to a minimal Flask app if the real app fails to import,
so gunicorn always binds and error details are visible via /health.
"""
import sys
import os
import traceback

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'web'))

_import_error = None

try:
    import server
    app = server.app
    print("[wsgi] Real app loaded OK.", flush=True)
except Exception as _e:
    _import_error = traceback.format_exc()
    print(f"[wsgi] IMPORT FAILED:\n{_import_error}", flush=True)

    from flask import Flask, jsonify
    app = Flask(__name__)

    @app.route('/health')
    def health():
        return jsonify({'status': 'import_error', 'detail': _import_error}), 200

    @app.route('/')
    def index():
        return f'<h1>Import Error</h1><pre>{_import_error}</pre>', 200
