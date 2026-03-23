"""Settings routes extracted from the monolithic web server."""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

import config
from app.services.settings_service import (
    get_audio_settings_payload,
    get_interview_role_payload,
    get_job_description_payload,
    get_launch_config_payload,
    get_server_ip_payload,
    save_job_description_payload,
    update_audio_settings,
    update_interview_role,
    update_launch_config,
)

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/api/coding_language", methods=["GET"])
def get_coding_language():
    """Return the current default coding language."""
    return jsonify({"language": config.CODING_LANGUAGE})


@settings_bp.route("/api/coding_language", methods=["POST"])
def set_coding_language():
    """Change the default coding language used for ambiguous coding questions."""
    data = request.get_json() or {}
    lang = data.get("language", "").lower().strip()
    allowed = {"python", "java", "javascript", "sql", "bash"}
    if lang not in allowed:
        return jsonify({"error": f"Unknown language. Allowed: {sorted(allowed)}"}), 400

    config.CODING_LANGUAGE = lang
    os.environ["CODING_LANGUAGE"] = lang
    return jsonify({"language": lang})


@settings_bp.route("/api/stt_model", methods=["GET"])
def get_stt_model():
    """Get current STT model name."""
    try:
        import stt

        return jsonify({"model": stt.model_name or config.STT_MODEL})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@settings_bp.route("/api/stt_model", methods=["POST"])
def set_stt_model():
    """Change STT model. Reloads the Whisper model."""
    data = request.get_json()
    if not data or "model" not in data:
        return jsonify({"error": "No model specified"}), 400

    new_model = data["model"]
    allowed = {
        "tiny.en",
        "base.en",
        "small.en",
        "medium.en",
        "large-v2",
        "large-v3",
        "distil-large-v3",
        "Systran/faster-distil-whisper-small.en",
        "Systran/faster-distil-whisper-medium.en",
        "Systran/faster-whisper-small.en",
        "Systran/faster-whisper-medium.en",
        "Systran/faster-whisper-large-v3",
    }
    if new_model not in allowed:
        return jsonify({"error": f"Invalid model. Allowed: {sorted(allowed)}"}), 400

    try:
        import stt
        from app.services.settings_service import update_audio_settings

        old_model = stt.model_name or config.STT_MODEL
        if new_model == old_model:
            return jsonify({"model": old_model, "changed": False})

        print(f"[SERVER] STT model change: {old_model} -> {new_model}")
        result = update_audio_settings({"stt_model": new_model})
        if "stt_model" in result.get("updated", {}):
            print(f"[SERVER] STT model loaded: {new_model}")
            return jsonify({"model": new_model, "changed": True})
        # Fallback: direct update
        config.STT_MODEL = new_model
        stt.DEFAULT_MODEL = new_model
        stt.load_model(new_model)
        return jsonify({"model": new_model, "changed": True})
    except Exception as exc:
        print(f"[SERVER] STT model change failed: {exc}")
        return jsonify({"error": str(exc)}), 500


@settings_bp.route("/api/audio_settings", methods=["GET"])
def get_audio_settings():
    """Return current live audio/capture config values."""
    return jsonify(get_audio_settings_payload())


@settings_bp.route("/api/audio_settings", methods=["POST"])
def set_audio_settings():
    """Update live audio/capture settings without restart."""
    data = request.get_json() or {}
    return jsonify(update_audio_settings(data))


@settings_bp.route("/api/jd_configure", methods=["POST"])
def jd_configure():
    """Analyze pasted JD, apply role/round settings, and async-seed Q&A pairs."""
    data = request.get_json() or {}
    jd_text = data.get("text", "").strip()
    if not jd_text:
        return jsonify({"error": "No JD text provided"}), 400
    try:
        from app.services.jd_service import configure_from_jd
        result = configure_from_jd(jd_text)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@settings_bp.route("/api/save_jd", methods=["POST"])
def save_jd():
    """Save job description text."""
    payload, status = save_job_description_payload(request.get_json())
    return jsonify(payload), status


@settings_bp.route("/api/get_jd")
def get_jd():
    """Get current job description."""
    payload, status = get_job_description_payload()
    return jsonify(payload), status


@settings_bp.route("/api/launch_config", methods=["GET"])
def get_launch_config():
    """Return the current launch configuration (mode, STT, LLM, active user)."""
    return jsonify(get_launch_config_payload())


@settings_bp.route("/api/launch_config", methods=["POST"])
def set_launch_config():
    """Update launch configuration and persist to .env."""
    data = request.get_json() or {}
    return jsonify(update_launch_config(data))


@settings_bp.route("/api/interview_role", methods=["GET"])
def get_interview_role():
    """Return the current interview role."""
    return jsonify(get_interview_role_payload())


@settings_bp.route("/api/interview_role", methods=["POST"])
def set_interview_role():
    """Set the interview role (python, java, javascript, sql, saas, system_design, general)."""
    data = request.get_json() or {}
    role = data.get("role", "").strip()
    result = update_interview_role(role)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)



@settings_bp.route("/api/ip")
def get_ip():
    """Get server LAN IP address."""
    response = jsonify(get_server_ip_payload())
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response


@settings_bp.route("/api/public_url")
def get_public_url():
    """Return the best public URL for this server (ngrok if active, else LAN IP)."""
    import config as cfg
    ngrok_domain = os.environ.get("NGROK_DOMAIN", "").strip()
    if ngrok_domain:
        url = f"https://{ngrok_domain}"
    else:
        ip = get_server_ip_payload()["ip"]
        port = int(os.environ.get("WEB_PORT", "8000"))
        url = f"http://{ip}:{port}"
    from flask import jsonify as _j
    resp = _j({"url": url})
    resp.headers.add("Access-Control-Allow-Origin", "*")
    return resp
