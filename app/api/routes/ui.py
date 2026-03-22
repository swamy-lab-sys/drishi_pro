"""UI page routes for Drishi Enterprise."""

from __future__ import annotations

from flask import Blueprint, render_template

ui_bp = Blueprint("ui", __name__)


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
