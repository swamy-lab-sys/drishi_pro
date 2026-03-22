"""
State Management for Drishi Pro

PRODUCTION-HARDENED STATE MACHINE

States:
    IDLE       - Waiting for speech
    LISTENING  - Capturing audio
    FINALIZE   - Confirming end-of-speech (silence detected)
    GENERATING - LLM is generating answer (HARD BLOCK)
    COOLDOWN   - Post-answer cooldown (HARD BLOCK)

RULES:
1. Generation lock = FIRST GATE (check before ANY processing)
2. Cooldown = HARD BLOCK (no input during cooldown)
3. Force-clear on startup (prevent stale locks)
4. Deduplication = prevent same question in rapid succession
5. ONE question at a time - no overlapping, no merging
"""

import threading
import time
from collections import deque
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional


class PipelineState(Enum):
    """Pipeline states for question handling."""
    IDLE = auto()        # Waiting for speech
    LISTENING = auto()   # Capturing audio
    FINALIZE = auto()    # Confirming end-of-speech
    GENERATING = auto()  # LLM generating answer (HARD BLOCK)
    COOLDOWN = auto()    # Post-answer cooldown (HARD BLOCK)


@dataclass
class PerformanceMetrics:
    """Performance timing for a single question."""
    audio_capture_start: float = 0.0
    audio_capture_end: float = 0.0
    silence_detected: float = 0.0
    transcription_start: float = 0.0
    transcription_end: float = 0.0
    validation_start: float = 0.0
    validation_end: float = 0.0
    llm_start: float = 0.0
    llm_end: float = 0.0
    ui_start: float = 0.0
    ui_end: float = 0.0
    total_start: float = 0.0
    total_end: float = 0.0

    def get_audio_duration(self) -> float:
        if self.audio_capture_end and self.audio_capture_start:
            return self.audio_capture_end - self.audio_capture_start
        return 0.0

    def get_transcription_duration(self) -> float:
        if self.transcription_end and self.transcription_start:
            return self.transcription_end - self.transcription_start
        return 0.0

    def get_validation_duration(self) -> float:
        if self.validation_end and self.validation_start:
            return self.validation_end - self.validation_start
        return 0.0

    def get_llm_duration(self) -> float:
        if self.llm_end and self.llm_start:
            return self.llm_end - self.llm_start
        return 0.0

    def get_ui_duration(self) -> float:
        if self.ui_end and self.ui_start:
            return self.ui_end - self.ui_start
        return 0.0

    def get_total_latency(self) -> float:
        if self.total_end and self.total_start:
            return self.total_end - self.total_start
        return 0.0

    def to_dict(self) -> dict:
        return {
            'audio_ms': int(self.get_audio_duration() * 1000),
            'transcription_ms': int(self.get_transcription_duration() * 1000),
            'validate_ms': int(self.get_validation_duration() * 1000),
            'llm_ms': int(self.get_llm_duration() * 1000),
            'ui_ms': int(self.get_ui_duration() * 1000),
            'total_ms': int(self.get_total_latency() * 1000),
        }


# =============================================================================
# GLOBAL STATE
# =============================================================================

# Current pipeline state
_current_state = PipelineState.IDLE
_state_lock = threading.Lock()

# Generation lock - NEVER interrupt once generation starts
_generating = False
_generation_lock = threading.Lock()

# Cooldown state - HARD BLOCK during cooldown
_in_cooldown = False
_cooldown_end_time = 0.0
_cooldown_lock = threading.Lock()

# Cooldown duration (seconds) - tuned for fast interview pacing
COOLDOWN_MIN = 0.2   # Very short answers — ready almost immediately
COOLDOWN_DEFAULT = 0.6  # Normal bullet answers
COOLDOWN_MAX = 1.5   # Code/long answers

# Last question tracking (for deduplication within session)
# Deque of (normalized_question, timestamp) — tracks last 5 to catch
# repeats even when another question was asked in between.
_DEDUP_HISTORY_SIZE = 5
_last_question = ""           # kept for backward-compat (get_last_question)
_last_question_time = 0.0
_question_history: deque = deque(maxlen=_DEDUP_HISTORY_SIZE)
_last_question_lock = threading.Lock()

# Selected user profile
_selected_user = None
_selected_user_lock = threading.Lock()

# Interview Mode Profile
_mode_profile = "interview" # interview | detailed
_mode_profile_lock = threading.Lock()

# Interview Timer
_session_start_time = time.time()
_timer_lock = threading.Lock()

# Current Models & Stats
_active_stt = "Initializing..."
_active_llm = "Initializing..."
_current_confidence = 0.0
_models_lock = threading.Lock()

# Deduplication window (seconds) - same question ignored within this window
DEDUP_WINDOW = 8.0

# Event that is SET when pipeline is idle (not generating, not in cooldown).
# Processing worker uses this to block without polling (replaces time.sleep loop).
_idle_event = threading.Event()
_idle_event.set()  # start idle

# Current performance metrics
_current_metrics: Optional[PerformanceMetrics] = None
_metrics_lock = threading.Lock()


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def get_state() -> PipelineState:
    """Get current pipeline state."""
    with _state_lock:
        return _current_state


def set_state(new_state: PipelineState):
    """Set pipeline state."""
    global _current_state
    with _state_lock:
        _current_state = new_state


def force_clear_all():
    """
    Force-clear all locks on startup.
    MUST be called at application start to prevent stale state.
    """
    global _generating, _in_cooldown, _cooldown_end_time
    global _last_question, _last_question_time, _current_state, _current_metrics

    with _generation_lock:
        _generating = False
    with _cooldown_lock:
        _in_cooldown = False
        _cooldown_end_time = 0.0
    with _last_question_lock:
        _last_question = ""
        _last_question_time = 0.0
        _question_history.clear()
    with _state_lock:
        _current_state = PipelineState.IDLE
    with _metrics_lock:
        _current_metrics = None
    _idle_event.set()


# =============================================================================
# GENERATION LOCK
# =============================================================================

def start_generation():
    """
    Mark that answer generation has started.
    This prevents any interruptions.
    """
    global _generating, _current_state
    _idle_event.clear()
    with _generation_lock:
        _generating = True
    with _state_lock:
        _current_state = PipelineState.GENERATING


def stop_generation():
    """Mark that answer generation has finished."""
    global _generating
    with _generation_lock:
        _generating = False
    # Set idle only if cooldown is also not active
    with _cooldown_lock:
        if not _in_cooldown:
            _idle_event.set()


def is_generating() -> bool:
    """Check if currently generating a response."""
    with _generation_lock:
        return _generating


# =============================================================================
# COOLDOWN MANAGEMENT
# =============================================================================

def calculate_adaptive_cooldown(answer_length: int = 0, is_code: bool = False) -> float:
    """
    Calculate adaptive cooldown based on answer characteristics.

    Args:
        answer_length: Length of answer in characters
        is_code: Whether the answer contains code

    Returns:
        float: Cooldown duration in seconds
    """
    # Base cooldown
    cooldown = COOLDOWN_DEFAULT

    # Shorter answers = shorter cooldown
    if answer_length < 150:
        cooldown = COOLDOWN_MIN
    elif answer_length < 350:
        cooldown = COOLDOWN_DEFAULT
    else:
        cooldown = 1.2

    # Code answers need slightly longer cooldown (user reading code)
    if is_code:
        cooldown = min(cooldown + 0.8, COOLDOWN_MAX)

    return cooldown


def start_cooldown(duration: float = None, answer_length: int = 0, is_code: bool = False):
    """
    Start the post-answer cooldown period.
    During cooldown, ALL input is ignored.

    Args:
        duration: Explicit cooldown duration (None = adaptive)
        answer_length: Length of answer for adaptive calculation
        is_code: Whether answer contains code
    """
    global _in_cooldown, _cooldown_end_time, _current_state

    # Calculate adaptive duration if not specified
    if duration is None:
        duration = calculate_adaptive_cooldown(answer_length, is_code)

    _idle_event.clear()
    with _cooldown_lock:
        _in_cooldown = True
        _cooldown_end_time = time.time() + duration
    with _state_lock:
        _current_state = PipelineState.COOLDOWN


def is_in_cooldown() -> bool:
    """
    Check if currently in cooldown period.
    Auto-clears cooldown if time has elapsed.
    """
    global _in_cooldown, _cooldown_end_time, _current_state
    with _cooldown_lock:
        if _in_cooldown:
            if time.time() >= _cooldown_end_time:
                _in_cooldown = False
                with _state_lock:
                    _current_state = PipelineState.IDLE
                _idle_event.set()
                return False
            return True
        return False


def get_cooldown_remaining() -> float:
    """Get remaining cooldown time in seconds."""
    with _cooldown_lock:
        if not _in_cooldown:
            return 0.0
        remaining = _cooldown_end_time - time.time()
        return max(0.0, remaining)


# =============================================================================
# FIRST GATE CHECK
# =============================================================================

def should_block_input() -> bool:
    """
    FIRST GATE CHECK: Should input be blocked?
    Returns True if generating OR in cooldown.

    CRITICAL: This must be the FIRST check after audio capture.
    If True, discard input silently (no logging, no validation).

    Optimized: fast-path check of _generating flag first (no lock needed
    for a simple bool read on CPython due to GIL), then check cooldown
    only if not generating.
    """
    # Fast path: if generating, no need to check cooldown
    if _generating:
        return True
    return is_in_cooldown()


def wait_until_idle(timeout: float = 30.0) -> bool:
    """Block until pipeline is idle (not generating or in cooldown).
    Returns True if idle, False if timed out. Replaces busy-wait polling loops."""
    return _idle_event.wait(timeout=timeout)


def should_ignore_audio() -> bool:
    """Alias for should_block_input for backward compatibility."""
    return should_block_input()


# =============================================================================
# QUESTION DEDUPLICATION
# =============================================================================

def set_last_question(question: str):
    """
    Record the last question processed.
    Used for deduplication within the session.
    """
    global _last_question, _last_question_time
    normalized = question.lower().strip()
    now = time.time()
    with _last_question_lock:
        _last_question = normalized
        _last_question_time = now
        _question_history.append((normalized, now))


def is_duplicate_question(question: str) -> bool:
    """
    Check if question is a duplicate of any of the last 5 questions
    within DEDUP_WINDOW seconds. Catches repeats even when another
    question was asked in between.

    Args:
        question: question text to check

    Returns:
        True if duplicate
    """
    normalized = question.lower().strip()
    if not normalized:
        return False
    now = time.time()
    with _last_question_lock:
        for prev_q, prev_t in _question_history:
            if (now - prev_t) <= DEDUP_WINDOW and prev_q == normalized:
                return True
    return False


def get_last_question() -> str:
    """Get the last processed question."""
    with _last_question_lock:
        return _last_question


def get_selected_user() -> Optional[dict]:
    """Get currently selected user profile."""
    with _selected_user_lock:
        return _selected_user


def set_selected_user(user: dict):
    """Set current user profile."""
    global _selected_user
    with _selected_user_lock:
        _selected_user = user


def get_mode_profile() -> str:
    with _mode_profile_lock:
        return _mode_profile


def set_mode_profile(profile: str):
    global _mode_profile
    with _mode_profile_lock:
        _mode_profile = profile


def set_confidence(val: float):
    global _current_confidence
    with _models_lock:
        _current_confidence = val


def get_session_elapsed() -> int:
    with _timer_lock:
        return int(time.time() - _session_start_time)


def set_active_models(stt: str, llm: str):
    global _active_stt, _active_llm
    with _models_lock:
        _active_stt = stt
        _active_llm = llm


# ── Latency tracking (rolling avg of last 20 answers) ────────
_latency_lock = threading.Lock()
_latency_samples: deque = deque(maxlen=20)  # O(1) append+evict, no pop(0)


def record_answer_latency(ms: float) -> None:
    """Record pipeline latency for a completed answer."""
    with _latency_lock:
        _latency_samples.append(ms)


def get_avg_latency_ms() -> float | None:
    """Return rolling average latency in ms, or None if no data yet."""
    with _latency_lock:
        if not _latency_samples:
            return None
        return round(sum(_latency_samples) / len(_latency_samples))


def get_session_info() -> dict:
    """Return summary of session state for UI header."""
    with _selected_user_lock:
        u = _selected_user
    with _models_lock:
        stt = _active_stt
        llm = _active_llm
        conf = _current_confidence
    with _mode_profile_lock:
        mp = _mode_profile

    result = {
        "user_name": u.get("name") if u else "None",
        "user_role": u.get("role") if u else "None",
        "user_exp": u.get("experience_years") if u else 0,
        "stt": stt,
        "llm": llm,
        "mode": mp,
        "elapsed": get_session_elapsed(),
        "confidence": round(conf * 100),
    }
    avg = get_avg_latency_ms()
    if avg is not None:
        result["avg_latency_ms"] = avg
    return result


# =============================================================================
# PERFORMANCE METRICS
# =============================================================================

def start_metrics() -> PerformanceMetrics:
    """Start a new performance metrics tracking session."""
    global _current_metrics
    with _metrics_lock:
        _current_metrics = PerformanceMetrics()
        _current_metrics.total_start = time.time()
        return _current_metrics


def get_current_metrics() -> Optional[PerformanceMetrics]:
    """Get current metrics."""
    with _metrics_lock:
        return _current_metrics


def mark_audio_start():
    """Mark audio capture start time."""
    if _current_metrics:  # no lock: single writer per question (GIL-safe float assign)
        _current_metrics.audio_capture_start = time.time()


def mark_audio_end():
    """Mark audio capture end time."""
    if _current_metrics:
        _current_metrics.audio_capture_end = time.time()


def mark_silence_detected():
    """Mark silence detection time."""
    if _current_metrics:
        _current_metrics.silence_detected = time.time()


def mark_transcription_start():
    """Mark transcription start time."""
    if _current_metrics:
        _current_metrics.transcription_start = time.time()


def mark_transcription_end():
    """Mark transcription end time."""
    if _current_metrics:
        _current_metrics.transcription_end = time.time()


def mark_llm_start():
    """Mark LLM generation start time."""
    if _current_metrics:
        _current_metrics.llm_start = time.time()


def mark_llm_end():
    """Mark LLM generation end time."""
    if _current_metrics:
        _current_metrics.llm_end = time.time()


def mark_validation_start():
    """Mark validation start time."""
    if _current_metrics:
        _current_metrics.validation_start = time.time()


def mark_validation_end():
    """Mark validation end time."""
    if _current_metrics:
        _current_metrics.validation_end = time.time()


def mark_ui_start():
    """Mark UI render start time."""
    if _current_metrics:
        _current_metrics.ui_start = time.time()


def mark_ui_end():
    """Mark UI render end time."""
    if _current_metrics:
        _current_metrics.ui_end = time.time()


def mark_ui_update():
    """Mark UI update time (legacy - use mark_ui_start/mark_ui_end)."""
    if _current_metrics:
        _current_metrics.ui_start = time.time()


def finalize_metrics() -> Optional[dict]:
    """Finalize and return metrics as dict."""
    global _current_metrics
    with _metrics_lock:
        if _current_metrics:
            _current_metrics.total_end = time.time()
            result = _current_metrics.to_dict()
            return result
        return None


def get_metrics_summary() -> str:
    """Get a human-readable metrics summary."""
    with _metrics_lock:
        if not _current_metrics:
            return "No metrics available"

        m = _current_metrics
        parts = []

        audio_ms = int(m.get_audio_duration() * 1000)
        if audio_ms > 0:
            parts.append(f"Audio: {audio_ms}ms")

        trans_ms = int(m.get_transcription_duration() * 1000)
        if trans_ms > 0:
            parts.append(f"STT: {trans_ms}ms")

        validate_ms = int(m.get_validation_duration() * 1000)
        if validate_ms > 0:
            parts.append(f"Validate: {validate_ms}ms")

        llm_ms = int(m.get_llm_duration() * 1000)
        if llm_ms > 0:
            parts.append(f"LLM: {llm_ms}ms")

        ui_ms = int(m.get_ui_duration() * 1000)
        if ui_ms > 0:
            parts.append(f"UI: {ui_ms}ms")

        total_ms = int(m.get_total_latency() * 1000)
        if total_ms > 0:
            parts.append(f"Total: {total_ms}ms")

        return " | ".join(parts) if parts else "Timing in progress"
