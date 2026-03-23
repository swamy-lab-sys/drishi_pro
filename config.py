"""
Configuration for Drishi Enterprise.

This module preserves the existing flat constant interface used across the
project, while sourcing runtime values from the canonical config schema in
`app.core.config_schema`. That keeps local and cloud deployments aligned.
"""
import os

from app.core.config_schema import load_runtime_config
from app.core.product import PRODUCT_NAME

_runtime = load_runtime_config()

# =============================================================================
# Audio Configuration
# =============================================================================

AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_DURATION_MS = 20  # Reduced for faster processing

# Voice Activity Detection
VAD_AGGRESSIVENESS = 1  # Less aggressive = faster processing
VAD_PADDING_MS = 200   # 200ms is enough; 400ms was adding latency on every question

# Recording limits — can be overridden via .env (persisted by settings service)
MAX_RECORDING_DURATION = float(os.environ.get("MAX_RECORDING_DURATION", "15.0"))
MIN_AUDIO_LENGTH = 0.4

# =============================================================================
# Adaptive Silence Detection
# =============================================================================

# 1.2s gives slow/deliberate speakers (interviewers) time to finish a thought
# without cutting them off mid-sentence.  Fast speakers still get captured
# correctly because the capture loop exits as soon as silence_chunks threshold
# is reached — it just waits up to 1.2s of continuous silence.
SILENCE_DEFAULT = float(os.environ.get("SILENCE_DEFAULT", "1.2"))
SILENCE_YOUTUBE = 0.45   # YouTube/tutorial audio — tight to cut filler quickly
SILENCE_MEET = 1.2
SILENCE_ZOOM = 1.2
SILENCE_TEAMS = 1.2

# =============================================================================
# Speech-to-Text
# =============================================================================

# Backend: "local" (faster-whisper) or "deepgram" (cloud, ChatGPT-level accuracy)
STT_BACKEND = _runtime.stt_backend

STT_MODEL = _runtime.stt_model
STT_DEVICE = None

# Deepgram API key (get free at https://deepgram.com — $200 free credit)
DEEPGRAM_API_KEY = _runtime.deepgram_api_key

# Sarvam AI — best for Indian English + Telugu/Tamil/Hindi/Kannada etc.
# Get free key at https://dashboard.sarvam.ai
SARVAM_API_KEY = _runtime.sarvam_api_key
# Language: "unknown" = auto-detect, "en-IN" = Indian English only,
# "te-IN"=Telugu, "hi-IN"=Hindi, "ta-IN"=Tamil, "kn-IN"=Kannada
SARVAM_LANGUAGE = _runtime.sarvam_language

# =============================================================================
# LLM Configuration (Claude)
# =============================================================================

LLM_MODEL = _runtime.llm_model
LLM_MAX_TOKENS_INTERVIEW = 100  # 3 short bullet points — faster generation
LLM_MAX_TOKENS_CODING = 700

# Default coding language for ambiguous questions ("write a function to...")
# Options: python, java, javascript, sql, bash
# Auto-detected from question keywords; this is the fallback when no language is mentioned.
CODING_LANGUAGE = _runtime.coding_language
LLM_TEMPERATURE_INTERVIEW = 0.4  # Higher for more natural human-like speech
LLM_TEMPERATURE_CODING = 0.1
LLM_TIMEOUT = 10.0

# =============================================================================
# Timing & Latency
# =============================================================================

DEDUP_WINDOW = 5.0

TARGET_SIMPLE_QUESTION_MS = 2000
TARGET_COMPLEX_QUESTION_MS = 4000
TARGET_CACHE_HIT_MS = 500

# =============================================================================
# Answer Caching
# =============================================================================

ENABLE_CACHE = True
CACHE_MAX_SIZE = 1000

# =============================================================================
# Web UI
# =============================================================================

WEB_PORT = _runtime.port
WEB_HOST = "0.0.0.0"

# =============================================================================
# Paths
# =============================================================================

RESUME_PATH = _runtime.resume_path
JD_PATH = "job_description.txt"
ANSWERS_DIR = "~/.drishi"

# =============================================================================
# Debug (ALL OFF for production)
# =============================================================================

DEBUG = False
VERBOSE = False
LOG_TO_FILE = False
DEBUG_MODE = False
SAVE_DEBUG_AUDIO = False

# =============================================================================
# Cloud / Render.com Mode
# =============================================================================

# Set CLOUD_MODE=true on Render — skips local audio listener, uses WebSocket audio
CLOUD_MODE = _runtime.cloud_mode

# Deployment profile used to keep local/cloud behavior on one config contract.
APP_MODE = _runtime.app_mode

# Secret code to authenticate Chrome extension users (required in cloud mode)
# Set via env var SECRET_CODE on Render dashboard
SECRET_CODE = _runtime.secret_code

ENABLE_MONITORING = _runtime.enable_monitoring

PRODUCT_TITLE = PRODUCT_NAME

# =============================================================================
# Launch Config (managed from admin UI, persisted to .env)
# =============================================================================

# "system" = capture local speakers  |  "extension" = Chrome extension sends audio
AUDIO_SOURCE = os.environ.get("AUDIO_SOURCE", "system")
# Start ngrok tunnel for global access
USE_NGROK = os.environ.get("USE_NGROK", "false").lower() in ("1", "true", "yes")
# Active user profile ID (empty = no profile)
USER_ID_OVERRIDE = os.environ.get("USER_ID_OVERRIDE", "")
# Interview role — sets coding language default and adds role context to LLM prompt
# Options: general, python, java, javascript, sql, saas, system_design
INTERVIEW_ROLE = os.environ.get("INTERVIEW_ROLE", "general")

# ElevenLabs TTS earpiece mode
ELEVENLABS_ENABLED = os.environ.get("ELEVENLABS_ENABLED", "false").lower() in ("1", "true", "yes")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
# Redis + Celery (async LLM tasks)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_ENABLED = os.environ.get("CELERY_ENABLED", "false").lower() in ("1", "true", "yes")


def runtime_profile() -> dict:
    """Expose a stable runtime profile for UI, ops, and future cloud migration."""
    return {
        "product_name": PRODUCT_NAME,
        "app_mode": APP_MODE,
        "cloud_mode": CLOUD_MODE,
        "web_port": WEB_PORT,
        "stt_backend": STT_BACKEND,
        "stt_model": STT_MODEL,
        "coding_language": CODING_LANGUAGE,
        "resume_path": RESUME_PATH,
        "has_secret_code": bool(SECRET_CODE),
        "enable_monitoring": ENABLE_MONITORING,
    }
