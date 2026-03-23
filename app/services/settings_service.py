"""Settings service extracted from route handlers.

This is the first backend service extraction from `web/server.py`.
The goal is to keep the current behavior while reducing route-level logic.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import config


def _persist_env(key: str, value: str) -> None:
    """Write or update a key=value line in the .env file so settings survive restart."""
    try:
        env_path = Path.cwd() / ".env"
        if not env_path.exists():
            env_path.write_text(f"{key}={value}\n", encoding="utf-8")
            return
        content = env_path.read_text(encoding="utf-8")
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(f"{key}={value}", content)
        else:
            content = content.rstrip("\n") + f"\n{key}={value}\n"
        # Atomic write: write to temp then rename — prevents partial writes on concurrent saves
        tmp_path = env_path.with_name(".env.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, env_path)
    except Exception:
        pass  # Non-fatal: in-memory update already applied


def get_audio_settings_payload() -> dict:
    """Return the currently effective live audio configuration."""
    return {
        "silence_duration": config.SILENCE_DEFAULT,
        "max_duration": config.MAX_RECORDING_DURATION,
        "stt_backend": config.STT_BACKEND,
        "stt_model": config.STT_MODEL,
        "app_mode": config.APP_MODE,
        "cloud_mode": config.CLOUD_MODE,
    }


def get_launch_config_payload() -> dict:
    """Return the current launch configuration (mode, STT, LLM, user)."""
    return {
        "audio_source": config.AUDIO_SOURCE,
        "use_ngrok": config.USE_NGROK,
        "stt_backend": config.STT_BACKEND,
        "stt_model": config.STT_MODEL,
        "llm_model": config.LLM_MODEL,
        "user_id_override": config.USER_ID_OVERRIDE,
    }


def update_launch_config(data: dict) -> dict:
    """Update launch config and persist to .env so it survives restarts."""
    changed = {}

    if "audio_source" in data:
        val = data["audio_source"].lower().strip()
        if val in ("system", "extension"):
            config.AUDIO_SOURCE = val
            os.environ["AUDIO_SOURCE"] = val
            _persist_env("AUDIO_SOURCE", val)
            changed["audio_source"] = val
            print(f"[SETTINGS] Audio source → {val}")

    if "use_ngrok" in data:
        val = bool(data["use_ngrok"])
        config.USE_NGROK = val
        os.environ["USE_NGROK"] = "true" if val else "false"
        _persist_env("USE_NGROK", "true" if val else "false")
        changed["use_ngrok"] = val
        print(f"[SETTINGS] ngrok → {'enabled' if val else 'disabled'} (takes effect on next restart)")

    if "llm_model" in data:
        model_map = {
            "haiku": "claude-haiku-4-5-20251001",
            "sonnet": "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6": "claude-sonnet-4-6",
        }
        val = model_map.get(data["llm_model"].strip())
        if val:
            config.LLM_MODEL = val
            os.environ["LLM_MODEL_OVERRIDE"] = val
            _persist_env("LLM_MODEL_OVERRIDE", val)
            try:
                import llm_client
                llm_client.MODEL = val
            except Exception:
                pass
            changed["llm_model"] = val
            print(f"[SETTINGS] LLM model → {val}")

    if "user_id_override" in data:
        val = str(data["user_id_override"]).strip()
        config.USER_ID_OVERRIDE = val
        os.environ["USER_ID_OVERRIDE"] = val
        _persist_env("USER_ID_OVERRIDE", val)
        changed["user_id_override"] = val
        # Activate the user in the running session if possible
        if val:
            try:
                from app.services.user_service import activate_user_payload
                result, _ = activate_user_payload(int(val))
                print(f"[SETTINGS] Active user → {result.get('name', val)} (id={val})")
            except Exception:
                pass

    if changed:
        print(f"[SETTINGS] Launch config applied: {changed}")
    return {"updated": changed}


def update_audio_settings(data: dict) -> dict:
    """Update live audio/capture settings without a restart.
    All valid changes are persisted to .env so they survive restart.
    """
    changed = {}

    if "silence_duration" in data:
        val = float(data["silence_duration"])
        if 0.3 <= val <= 4.0:
            config.SILENCE_DEFAULT = val
            changed["silence_duration"] = val
            _persist_env("SILENCE_DEFAULT", str(val))
            print(f"[SETTINGS] Silence duration → {val}s")

    if "max_duration" in data:
        val = float(data["max_duration"])
        if 5.0 <= val <= 30.0:
            config.MAX_RECORDING_DURATION = val
            changed["max_duration"] = val
            _persist_env("MAX_RECORDING_DURATION", str(val))
            print(f"[SETTINGS] Max recording duration → {val}s")

    if "stt_backend" in data:
        backend = data["stt_backend"].lower().strip()
        allowed = {"local", "deepgram", "sarvam", "assemblyai"}
        if backend in allowed:
            config.STT_BACKEND = backend
            os.environ["STT_BACKEND"] = backend
            changed["stt_backend"] = backend
            _persist_env("STT_BACKEND", backend)
            print(f"[SETTINGS] STT backend → {backend}")
            # Default Sarvam to en-IN for fastest response (skip auto-detect overhead)
            if backend == "sarvam" and not os.environ.get("SARVAM_LANGUAGE"):
                config.SARVAM_LANGUAGE = "en-IN"
                os.environ["SARVAM_LANGUAGE"] = "en-IN"
                _persist_env("SARVAM_LANGUAGE", "en-IN")

    if "stt_model" in data:
        model = data["stt_model"].strip()
        allowed_models = {
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
        if model in allowed_models:
            config.STT_MODEL = model
            os.environ["STT_MODEL_OVERRIDE"] = model
            _persist_env("STT_MODEL_OVERRIDE", model)
            try:
                import stt as _stt

                _stt.DEFAULT_MODEL = model
                _stt.load_model(model)
            except Exception:
                pass
            changed["stt_model"] = model
            print(f"[SETTINGS] STT model → {model}")

    if "coding_language" in data:
        lang = data["coding_language"].lower().strip()
        allowed_langs = {"python", "java", "javascript", "sql", "bash", "typescript", "go"}
        if lang in allowed_langs:
            config.CODING_LANGUAGE = lang
            os.environ["CODING_LANGUAGE"] = lang
            _persist_env("CODING_LANGUAGE", lang)
            changed["coding_language"] = lang
            print(f"[SETTINGS] Coding language → {lang}")

    if changed:
        print(f"[SETTINGS] Applied: {changed}")
    return {"updated": changed}


_ROLE_LANG_MAP = {
    "python": "python",
    "java": "java",
    "javascript": "javascript",
    "sql": "sql",
    "saas": "python",
    "system_design": "python",
    "devops": "bash",
    "production_support": "bash",
    "telecom": "bash",
    "general": "python",
}


def get_interview_role_payload() -> dict:
    return {"role": config.INTERVIEW_ROLE}


def update_interview_role(role: str) -> dict:
    """Set the interview role — updates coding language default and LLM context."""
    allowed = {"general", "python", "java", "javascript", "sql", "saas", "system_design",
               "devops", "production_support", "telecom"}
    role = role.lower().strip()
    if role not in allowed:
        return {"error": f"Unknown role: {role}"}
    config.INTERVIEW_ROLE = role
    os.environ["INTERVIEW_ROLE"] = role
    _persist_env("INTERVIEW_ROLE", role)
    # Sync coding language to the role's primary language
    lang = _ROLE_LANG_MAP.get(role, "python")
    config.CODING_LANGUAGE = lang
    os.environ["CODING_LANGUAGE"] = lang
    _persist_env("CODING_LANGUAGE", lang)
    print(f"[SETTINGS] Interview role → {role}  |  coding language → {lang}")
    return {"updated": {"interview_role": role, "coding_language": lang}}


def get_interview_round_payload() -> dict:
    return {"round": config.INTERVIEW_ROUND}


def update_interview_round(round_name: str) -> dict:
    """Set interview round — adjusts LLM token budget, temperature, answer style."""
    allowed = {"tech", "hr", "design", "code"}
    round_name = round_name.lower().strip()
    if round_name not in allowed:
        return {"error": f"Unknown round: {round_name}. Allowed: {sorted(allowed)}"}
    config.INTERVIEW_ROUND = round_name
    os.environ["INTERVIEW_ROUND"] = round_name
    _persist_env("INTERVIEW_ROUND", round_name)
    print(f"[SETTINGS] Interview round → {round_name}")
    return {"updated": {"interview_round": round_name}}


def save_job_description_payload(data: dict) -> tuple[dict, int]:
    if not data or "text" not in data:
        return {"error": "No text provided"}, 400

    try:
        jd_path = Path.cwd() / config.JD_PATH
        with open(jd_path, "w", encoding="utf-8") as handle:
            handle.write(data["text"])
        return {"success": True}, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def get_job_description_payload() -> tuple[dict, int]:
    try:
        jd_path = Path.cwd() / config.JD_PATH
        if jd_path.exists():
            with open(jd_path, "r", encoding="utf-8") as handle:
                return {"text": handle.read()}, 200
        return {"text": ""}, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def get_server_ip() -> str:
    """Detect LAN IP by attempting to connect to an external address."""
    ip_addr = "127.0.0.1"
    sock = None
    try:
        # We don't actually send data; this just finds the local interface
        # that would be used to route to the internet.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip_addr = sock.getsockname()[0]
    except Exception:
        # Fallback to hostname-based IP if socket trick fails
        try:
            ip_addr = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip_addr = "127.0.0.1"
    finally:
        if sock is not None:
            sock.close()
    return ip_addr


def get_server_ip_payload() -> dict:
    """Return the detected server IP as a JSON-compatible payload."""
    return {"ip": get_server_ip()}
