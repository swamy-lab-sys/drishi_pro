"""
WSGI entry point for Render.com deployment.
"""
import sys
import os
import traceback

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'web'))

try:
    import server
    app = server.app
    print("[wsgi] Real app loaded OK.", flush=True)
except Exception as _e:
    _tb = traceback.format_exc()
    print(f"[wsgi] IMPORT FAILED:\n{_tb}", flush=True)

    from flask import Flask, jsonify
    app = Flask(__name__)

    @app.route('/health')
    def health():
        return jsonify({'status': 'import_error', 'detail': _tb}), 200

    @app.route('/')
    def index():
        return f'<h1>Import Error</h1><pre>{_tb}</pre>', 200
