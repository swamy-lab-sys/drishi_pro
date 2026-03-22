"""
Shared pytest fixtures.

Importing web.server here (at conftest load time) ensures Flask app and all
dependent modules are fully initialised before individual test modules are
collected — avoiding the Python 3.10 import-lock race that occurs when
test_interview_flow.py (which imports `main`) runs before test_interview_routes.py
tries to import web.server.
"""

from web.server import app as _app  # noqa: F401  — import side-effect intentional
