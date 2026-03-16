"""
Configuration for Drishi Pro

PRODUCTION SETTINGS - Optimized for latency and token efficiency.
"""
import os

# =============================================================================
# Audio Configuration
# =============================================================================

AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_DURATION_MS = 20  # Reduced for faster processing

# Voice Activity Detection
VAD_AGGRESSIVENESS = 1  # Less aggressive = faster processing
VAD_PADDING_MS = 400   # Reduced: less padding = faster end-of-speech detection

# Recording limits
MAX_RECORDING_DURATION = 12.0
MIN_AUDIO_LENGTH = 0.4

# =============================================================================
# Adaptive Silence Detection
# =============================================================================

SILENCE_DEFAULT = 0.45  # Optimized: 450ms silence triggers STT 350ms faster
SILENCE_YOUTUBE = 0.45
SILENCE_MEET = 0.55
SILENCE_ZOOM = 0.55
SILENCE_TEAMS = 0.55

# =============================================================================
# Speech-to-Text
# =============================================================================

# Backend: "local" (faster-whisper) or "deepgram" (cloud, ChatGPT-level accuracy)
STT_BACKEND = os.environ.get("STT_BACKEND", "local")

STT_MODEL = os.environ.get("STT_MODEL_OVERRIDE", "Systran/faster-distil-whisper-small.en")
STT_DEVICE = None

# Deepgram API key (get free at https://deepgram.com — $200 free credit)
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")

# Sarvam AI — best for Indian English + Telugu/Tamil/Hindi/Kannada etc.
# Get free key at https://dashboard.sarvam.ai
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
# Language: "unknown" = auto-detect, "en-IN" = Indian English only,
# "te-IN"=Telugu, "hi-IN"=Hindi, "ta-IN"=Tamil, "kn-IN"=Kannada
SARVAM_LANGUAGE = os.environ.get("SARVAM_LANGUAGE", "unknown")

# =============================================================================
# LLM Configuration (Claude)
# =============================================================================

LLM_MODEL = os.environ.get("LLM_MODEL_OVERRIDE", "claude-haiku-4-5-20251001")
LLM_MAX_TOKENS_INTERVIEW = 100  # 3 short bullet points — faster generation
LLM_MAX_TOKENS_CODING = 700

# Default coding language for ambiguous questions ("write a function to...")
# Options: python, java, javascript, sql, bash
# Auto-detected from question keywords; this is the fallback when no language is mentioned.
CODING_LANGUAGE = os.environ.get("CODING_LANGUAGE", "python")
LLM_TEMPERATURE_INTERVIEW = 0.4  # Higher for more natural human-like speech
LLM_TEMPERATURE_CODING = 0.1
LLM_TIMEOUT = 10.0

# =============================================================================
# Timing & Latency
# =============================================================================

COOLDOWN_BASE = 0.3
COOLDOWN_PER_CHAR = 0.001
COOLDOWN_CODE_BONUS = 0.8
COOLDOWN_DURATION = 1.5
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

WEB_PORT = 8000
WEB_HOST = "0.0.0.0"

# =============================================================================
# Paths
# =============================================================================

RESUME_PATH = os.environ.get("RESUME_PATH", "resume.txt")
JD_PATH = "job_description.txt"
ANSWERS_DIR = "~/.drishi"

# =============================================================================
# Debug (ALL OFF for production)
# =============================================================================

DEBUG = False
VERBOSE = True
LOG_TO_FILE = True
DEBUG_MODE = False
SAVE_DEBUG_AUDIO = False

# =============================================================================
# Cloud / Render.com Mode
# =============================================================================

# Set CLOUD_MODE=true on Render — skips local audio listener, uses WebSocket audio
CLOUD_MODE = os.environ.get("CLOUD_MODE", "false").lower() == "true"

# Secret code to authenticate Chrome extension users (required in cloud mode)
# Set via env var SECRET_CODE on Render dashboard
SECRET_CODE = os.environ.get("SECRET_CODE", "")
