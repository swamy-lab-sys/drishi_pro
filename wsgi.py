"""
WSGI entry point for Render.com deployment.
"""
import sys
import os

print("[wsgi] Starting...", flush=True)

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'web'))

print("[wsgi] Importing server...", flush=True)
try:
    import server
    app = server.app
    print("[wsgi] App ready.", flush=True)
except Exception as e:
    print(f"[wsgi] IMPORT ERROR: {e}", flush=True)
    import traceback
    traceback.print_exc()
    raise
