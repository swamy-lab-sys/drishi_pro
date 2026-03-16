"""
WSGI entry point for Render.com deployment.

Gunicorn imports this module and uses the `app` object.
Sets up sys.path so all project modules (config, state, etc.) are importable.
"""
import sys
import os

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)                          # config, state, qa_database, etc.
sys.path.insert(0, os.path.join(_root, 'web'))     # server.py

import server  # web/server.py
app = server.app
