"""Security and runtime control routes."""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from app.services.security_service import (
    authenticate_payload,
    set_mode_profile_payload,
)

security_bp = Blueprint("security", __name__)


@security_bp.route("/api/settings/mode-profile", methods=["POST"])
def set_mode_profile():
    """Set profile: interview or detailed."""
    return jsonify(set_mode_profile_payload(request.get_json()))


@security_bp.route("/api/auth", methods=["POST", "OPTIONS"])
def api_auth():
    """Validate secret code. Returns session token on success."""
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True) or {}
    code = data.get("code", "") or request.args.get("code", "")
    payload, status = authenticate_payload(code)
    return jsonify(payload), status


@security_bp.route("/api/env_keys", methods=["GET"])
def get_env_keys():
    """Return current API key status (masked values)."""
    import config  # noqa: PLC0415
    keys_config = [
        {"id": "openai", "name": "OpenAI API Key", "env_var": "OPENAI_API_KEY", "color": "#22C55E"},
        {"id": "groq", "name": "Groq API Key", "env_var": "GROQ_API_KEY", "color": "#3B82F6"},
        {"id": "gemini", "name": "Gemini API Key", "env_var": "GEMINI_API_KEY", "color": "#EF4444"},
        {"id": "deepgram", "name": "Deepgram API Key", "env_var": "DEEPGRAM_API_KEY", "color": "#8B5CF6"},
        {"id": "sarvam", "name": "Sarvam API Key", "env_var": "SARVAM_API_KEY", "color": "#F59E0B"},
    ]
    result = []
    for k in keys_config:
        val = os.environ.get(k["env_var"], "")
        masked = ("sk-..." + val[-4:]) if len(val) > 8 else ("••••" if val else "")
        result.append({
            "id": k["id"], "name": k["name"], "env_var": k["env_var"],
            "color": k["color"],
            "active": bool(val),
            "masked": masked,
        })
    return jsonify(result)


@security_bp.route("/api/env_keys", methods=["POST"])
def update_env_key():
    """Update an API key at runtime (updates os.environ and config)."""
    import config  # noqa: PLC0415
    data = request.get_json(silent=True) or {}
    env_var = data.get("env_var", "").strip()
    value = data.get("value", "").strip()
    allowed = {"OPENAI_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "DEEPGRAM_API_KEY", "SARVAM_API_KEY"}
    if env_var not in allowed:
        return jsonify({"error": "Not allowed"}), 400
    os.environ[env_var] = value
    # Update config module attr if it exists
    attr = env_var  # config uses same names
    if hasattr(config, attr):
        setattr(config, attr, value)
    return jsonify({"ok": True, "env_var": env_var, "active": bool(value)})
