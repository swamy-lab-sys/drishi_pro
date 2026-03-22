"""Operational support routes extracted from the monolithic web server."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.ops_service import (
    build_session_export_md_response,
    build_session_export_response,
    get_answers_payload,
    get_api_status_payload,
    get_local_url_payload,
    get_logs_payload,
    get_session_info_payload,
    get_system_health_payload,
    get_transcribing_payload,
)

ops_bp = Blueprint("ops", __name__)


@ops_bp.route("/api/session-info")
def session_info():
    """Summary of current session for UI header."""
    return jsonify(get_session_info_payload())


@ops_bp.route("/api/system/health")
def system_health():
    """Real-time system monitoring."""
    return jsonify(get_system_health_payload())


@ops_bp.route("/api/status")
def get_api_status():
    """Expanded status of external API integrations."""
    return jsonify(get_api_status_payload())


@ops_bp.route("/api/answers")
def get_answers():
    """Get all answers. Pass ?user_token=<token> for a specific extension user."""
    user_token = request.args.get('user_token', '').strip()
    return jsonify(get_answers_payload(user_token=user_token))


@ops_bp.route("/api/transcribing")
def get_transcribing():
    """Return live transcription text for the hearing indicator."""
    return jsonify(get_transcribing_payload())


@ops_bp.route("/api/logs")
def get_logs():
    """Get recent debug logs."""
    try:
        return jsonify(get_logs_payload())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ops_bp.route("/api/local_url")
def local_url():
    """Return the local network URL for mobile QR code scanning."""
    return jsonify(get_local_url_payload(request.host))


@ops_bp.route("/api/session_export")
def session_export():
    """Export current session Q&A as JSON download."""
    return build_session_export_response()


@ops_bp.route("/api/session_export_md")
def session_export_md():
    """Export current session Q&A as Markdown download."""
    return build_session_export_md_response()
