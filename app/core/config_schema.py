"""Canonical config contract shared by local and cloud deployments.

This module is intentionally framework-agnostic. It provides a single schema
that future Flask/FastAPI service layers can use without duplicating env logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

# Load .env from project root (so `python3 main.py` works without run.sh)
_env_file = Path(__file__).parent.parent.parent / '.env'
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _, _v = _line.partition('=')
                if _k.strip() not in os.environ:  # don't override shell exports
                    os.environ[_k.strip()] = _v.strip()


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class RuntimeConfig:
    app_mode: str
    cloud_mode: bool
    port: int
    secret_code: str
    anthropic_api_key: str
    llm_model: str
    stt_backend: str
    stt_model: str
    deepgram_api_key: str
    sarvam_api_key: str
    sarvam_language: str
    coding_language: str
    resume_path: str
    pulse_source: str
    interview_debug: bool
    verbose: bool
    enable_monitoring: bool


def load_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        app_mode=os.environ.get("APP_MODE", "local"),
        cloud_mode=_get_bool("CLOUD_MODE", False),
        port=_get_int("PORT", 8000),
        secret_code=os.environ.get("SECRET_CODE", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        llm_model=os.environ.get("LLM_MODEL_OVERRIDE", "claude-haiku-4-5-20251001"),
        stt_backend=os.environ.get("STT_BACKEND", "local"),
        stt_model=os.environ.get("STT_MODEL_OVERRIDE", "Systran/faster-distil-whisper-small.en"),
        deepgram_api_key=os.environ.get("DEEPGRAM_API_KEY", ""),
        sarvam_api_key=os.environ.get("SARVAM_API_KEY", ""),
        sarvam_language=os.environ.get("SARVAM_LANGUAGE", "en-IN"),
        coding_language=os.environ.get("CODING_LANGUAGE", "python"),
        resume_path=os.environ.get("RESUME_PATH", "resume.txt"),
        pulse_source=os.environ.get("PULSE_SOURCE", ""),
        interview_debug=_get_bool("INTERVIEW_DEBUG", False),
        verbose=_get_bool("VERBOSE", False),
        enable_monitoring=_get_bool("ENABLE_MONITORING", True),
    )
