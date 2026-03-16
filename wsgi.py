"""
WSGI entry point for Render.com deployment.
Lazy-loads the real app; /health responds immediately.
"""
import sys
import os
import time
import threading
import traceback
from flask import Flask, jsonify

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'web'))

_real_app = None
_import_error = None
_import_status = "starting"
_ready = threading.Event()


def _log(msg):
    print(f"[wsgi {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load():
    global _real_app, _import_error, _import_status
    try:
        _log("importing config..."); import config; _log("config OK")
        _log("importing state..."); import state; _log("state OK")
        _log("importing answer_storage..."); import answer_storage; _log("answer_storage OK")
        _log("importing llm_client..."); import llm_client; _log("llm_client OK")
        _log("importing fragment_context..."); import fragment_context; _log("fragment_context OK")
        _log("importing qa_database..."); import qa_database; _log("qa_database OK")
        _log("importing question_validator..."); from question_validator import validate_question; _log("question_validator OK")
        _log("importing server..."); import server; _log("server OK")
        _real_app = server.app
        _import_status = "ok"
        _log("Real app ready.")
    except Exception:
        _import_error = traceback.format_exc()
        _import_status = "error"
        _log(f"IMPORT FAILED:\n{_import_error}")
    finally:
        _ready.set()


threading.Thread(target=_load, daemon=False).start()

_boot = Flask(__name__)


@_boot.route('/health')
def health():
    return jsonify({'status': _import_status}), 200


@_boot.route('/status')
def status():
    return jsonify({'status': _import_status, 'error': _import_error}), 200


@_boot.route('/', defaults={'path': ''})
@_boot.route('/<path:path>')
def loading_page(path):
    if not _ready.is_set():
        return f'<h1>Drishi Pro starting... ({_import_status})</h1><p>Refresh in a moment.</p>', 503
    if _import_error:
        return f'<h1>Startup Error</h1><pre>{_import_error}</pre>', 500
    return '<h1>Ready — please refresh</h1>', 200


class _App:
    def __call__(self, environ, start_response):
        target = _real_app if _real_app is not None else _boot
        return target(environ, start_response)


app = _App()
