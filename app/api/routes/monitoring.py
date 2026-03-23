"""Monitoring viewer and lightweight session-intelligence routes."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, redirect, send_from_directory

monitoring_bp = Blueprint("monitoring", __name__)

MONITOR_VIEWER_DIR = Path(__file__).resolve().parents[3] / "web" / "static" / "monitor_viewer"


@monitoring_bp.route("/monitor-viewer/")
@monitoring_bp.route("/monitor-viewer/<path:filename>")
def monitor_viewer(filename: str = "index.html"):
    """Serve the browser monitor viewer UI."""
    from app.core.config_schema import load_runtime_config
    if not load_runtime_config().enable_monitoring:
        return "Monitoring is disabled on this server. Restart with monitoring enabled to access this feature.", 403
    if not MONITOR_VIEWER_DIR.exists():
        return "Monitor viewer not installed", 404
    return send_from_directory(MONITOR_VIEWER_DIR, filename)


@monitoring_bp.route("/v/<session_id>")
@monitoring_bp.route("/v/<session_id>/<key>")
def viewer_shortlink(session_id: str, key: str = ""):
    """Short URL — redirects to the live monitor page."""
    return redirect("/monitor")


@monitoring_bp.route("/api/session/predictions")
def get_predictions():
    """Return predicted next interview topics."""
    from semantic_engine import engine

    return jsonify(engine.predict_next_topics())
