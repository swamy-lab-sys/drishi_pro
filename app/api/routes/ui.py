"""UI page routes for Drishi Enterprise."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, render_template, send_from_directory

ui_bp = Blueprint("ui", __name__)

# Path to the Vite production build (npm run build → web/static/react/)
_REACT_BUILD = Path(__file__).parents[3] / "web" / "static" / "react"


def _react_index():
    """Serve the React SPA index.html."""
    return send_from_directory(str(_REACT_BUILD), "index.html")


@ui_bp.route("/")
def index():
    """Serve main page."""
    return render_template("index.html")


@ui_bp.route("/users")
def users_page():
    """Serve users list dashboard."""
    return render_template("users.html")


@ui_bp.route("/ext-users")
def ext_users_page():
    """Extension user management — admin creates tokens shared with each user."""
    return render_template("ext_users.html")


@ui_bp.route("/api-dashboard")
def api_dashboard():
    """Serve API configuration dashboard."""
    return render_template("api_dashboard.html")


@ui_bp.route("/settings")
def settings_page():
    """Serve the full settings page."""
    return render_template("settings.html")


@ui_bp.route("/profile")
def profile_page():
    """User profile editor with skills and custom instructions."""
    return render_template("profile.html")


@ui_bp.route("/questions")
def questions():
    """Serve questions database page."""
    return render_template("questions.html")


@ui_bp.route("/monitor")
def monitor():
    """Remote monitor page. ?user=<token> shows per-user answers."""
    from flask import request as _req
    user_token = _req.args.get('user', '').strip()
    if user_token:
        return render_template("user_monitor.html", user_token=user_token)
    return render_template("monitor.html")


@ui_bp.route("/lookup")
def lookup():
    """Keyword lookup page — type any word/topic, see full Q&A details instantly."""
    return render_template("lookup.html")


@ui_bp.route("/voice")
def voice_ui():
    """Serve push-to-talk voice interface."""
    return render_template("voice.html")


@ui_bp.route("/qa-manager")
def qa_manager():
    """Serve the Q&A database dashboard."""
    return render_template("qa_manager.html")


@ui_bp.route("/portal/<token>")
def user_portal(token):
    """User self-service portal — no admin login needed. Token is the auth."""
    return render_template("user_portal.html", user_token=token)


@ui_bp.route("/admin-docs")
def admin_docs():
    """Admin-only documentation page — full project reference."""
    return render_template("admin_docs.html")


# ── React SPA routes (served from web/static/react/ build) ──────────────────
# Old Flask routes remain unchanged; React lives on /react/* paths.
# In dev mode these are served by Vite (port 5173). In production they come here.

@ui_bp.route("/react/")
@ui_bp.route("/react")
def react_dashboard():
    """React main dashboard (SPA)."""
    if not _REACT_BUILD.exists():
        return "React build not found. Run: cd react_ui && npm run build", 404
    return _react_index()


@ui_bp.route("/react/monitor")
def react_monitor():
    """React monitor page (SPA) — same build, different route handled by React Router."""
    if not _REACT_BUILD.exists():
        return "React build not found. Run: cd react_ui && npm run build", 404
    return _react_index()


@ui_bp.route("/react/settings")
def react_settings():
    """React settings page (SPA)."""
    if not _REACT_BUILD.exists():
        return "React build not found. Run: cd react_ui && npm run build", 404
    return _react_index()


@ui_bp.route("/react/qa-manager")
def react_qa_manager():
    """React QA Manager page (SPA)."""
    if not _REACT_BUILD.exists():
        return "React build not found. Run: cd react_ui && npm run build", 404
    return _react_index()


@ui_bp.route("/react/ext-users")
def react_ext_users():
    """React Ext Users page (SPA)."""
    if not _REACT_BUILD.exists():
        return "React build not found. Run: cd react_ui && npm run build", 404
    return _react_index()


@ui_bp.route("/react/assets/<path:filename>")
def react_assets(filename):
    """Serve Vite build assets (JS/CSS chunks)."""
    return send_from_directory(str(_REACT_BUILD / "assets"), filename)
