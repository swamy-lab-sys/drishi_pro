"""
Debug Logger for Drishi Pro

Comprehensive logging for performance debugging.

LOG FILES:
- ~/.drishi/logs/debug.log      - All debug messages
- ~/.drishi/logs/performance.log - Performance metrics only
- ~/.drishi/logs/errors.log     - Errors only

USAGE:
    from debug_logger import log, log_timing, log_error, timer

    log("Starting capture...")
    with timer("audio_capture"):
        audio = capture_audio()
    log_timing("transcription", 0.342)
"""

import atexit
import os
import time
import threading
from datetime import datetime
from pathlib import Path
from functools import wraps
from contextlib import contextmanager
from typing import Optional, Dict, Any, List

# Configuration
LOG_DIR = Path.home() / ".drishi" / "logs"
DEBUG_LOG = LOG_DIR / "debug.log"
PERF_LOG = LOG_DIR / "performance.log"
ERROR_LOG = LOG_DIR / "errors.log"

# Thread-safe lock
_log_lock = threading.Lock()

# Global timing storage for current request
_current_timings: Dict[str, float] = {}
_request_start: float = 0

# Enable/disable logging (can be controlled via env var)
LOGGING_ENABLED = os.environ.get("INTERVIEW_DEBUG", "1") == "1"

# Directory creation cache
_dir_ensured = False

# --- Buffered write state ---
_FLUSH_INTERVAL = 0.5  # Flush buffered logs every 500ms
_log_buffers: Dict[str, List[str]] = {}  # filepath -> list of lines
_flush_timer: Optional[threading.Timer] = None
_flush_running = True


def ensure_log_dir():
    """Create log directory if not exists (cached)."""
    global _dir_ensured
    if not _dir_ensured:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _dir_ensured = True


def _flush_buffers():
    """Flush all buffered log lines to their respective files."""
    global _flush_timer
    with _log_lock:
        for filepath_str, lines in _log_buffers.items():
            if not lines:
                continue
            try:
                with open(filepath_str, 'a', encoding='utf-8') as f:
                    f.write("".join(lines))
                    f.flush()
            except Exception:
                pass
            lines.clear()

    # Re-schedule if still running
    if _flush_running:
        _flush_timer = threading.Timer(_FLUSH_INTERVAL, _flush_buffers)
        _flush_timer.daemon = True
        _flush_timer.start()


def _start_flush_timer():
    """Start the periodic flush timer."""
    global _flush_timer
    if _flush_timer is None:
        _flush_timer = threading.Timer(_FLUSH_INTERVAL, _flush_buffers)
        _flush_timer.daemon = True
        _flush_timer.start()


def _shutdown_logger():
    """Flush remaining logs on shutdown."""
    global _flush_running
    _flush_running = False
    if _flush_timer:
        _flush_timer.cancel()
    # Final flush
    with _log_lock:
        for filepath_str, lines in _log_buffers.items():
            if not lines:
                continue
            try:
                with open(filepath_str, 'a', encoding='utf-8') as f:
                    f.write("".join(lines))
                    f.flush()
            except Exception:
                pass
            lines.clear()


atexit.register(_shutdown_logger)


def _write_log(filepath: Path, message: str, include_timestamp: bool = True, immediate: bool = False):
    """Write to log file (thread-safe, buffered).

    Args:
        filepath: Log file path.
        message: Log message.
        include_timestamp: Prepend timestamp.
        immediate: If True, flush to disk right away (for errors).
    """
    if not LOGGING_ENABLED:
        return

    ensure_log_dir()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] {message}\n" if include_timestamp else message + "\n"

    filepath_str = str(filepath)

    with _log_lock:
        buf = _log_buffers.get(filepath_str)
        if buf is None:
            buf = []
            _log_buffers[filepath_str] = buf
        buf.append(line)

    if immediate:
        _flush_buffers()
    else:
        _start_flush_timer()


def log(message: str, level: str = "DEBUG"):
    """
    Log a debug message.

    Args:
        message: The message to log
        level: DEBUG, INFO, WARN, ERROR
    """
    _write_log(DEBUG_LOG, f"[{level}] {message}", immediate=(level == "ERROR"))

    # Also print to console for real-time monitoring
    if level in ("WARN", "ERROR"):
        print(f"[{level}] {message}")


def log_info(message: str):
    """Log an info message."""
    log(message, "INFO")


def log_warn(message: str):
    """Log a warning message."""
    log(message, "WARN")


def log_error(message: str, exception: Optional[Exception] = None):
    """Log an error message."""
    if exception:
        message = f"{message}: {type(exception).__name__}: {exception}"
    log(message, "ERROR")
    _write_log(ERROR_LOG, message, immediate=True)


def log_timing(component: str, duration_seconds: float, extra: str = ""):
    """
    Log timing for a component.

    Args:
        component: Name of the component (audio, stt, llm, etc.)
        duration_seconds: Duration in seconds
        extra: Extra info to append
    """
    ms = int(duration_seconds * 1000)
    msg = f"[TIMING] {component}: {ms}ms"
    if extra:
        msg += f" ({extra})"
    log(msg, "PERF")

    # Store in current timings
    _current_timings[component] = duration_seconds


def start_request():
    """Mark the start of a new request/question processing."""
    global _request_start, _current_timings
    _request_start = time.time()
    _current_timings = {}
    log("=" * 60, "INFO")
    log("NEW REQUEST STARTED", "INFO")


def end_request(question: str = "", answer_length: int = 0):
    """
    Mark the end of a request and log summary.

    Args:
        question: The question that was processed
        answer_length: Length of the answer in characters
    """
    global _request_start, _current_timings

    total_time = time.time() - _request_start if _request_start > 0 else 0

    # Build performance summary
    summary_parts = []
    for component, duration in _current_timings.items():
        ms = int(duration * 1000)
        summary_parts.append(f"{component}={ms}ms")

    summary_parts.append(f"total={int(total_time * 1000)}ms")

    summary = " | ".join(summary_parts)

    # Log to debug
    log(f"REQUEST COMPLETED: {summary}", "INFO")
    if question:
        log(f"Question: {question[:80]}...", "INFO") if len(question) > 80 else log(f"Question: {question}", "INFO")
    log(f"Answer length: {answer_length} chars", "INFO")
    log("=" * 60, "INFO")

    # Log to performance file
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    perf_line = f"{timestamp} | {summary}"
    if question:
        perf_line += f" | Q: {question[:50]}"
    _write_log(PERF_LOG, perf_line, include_timestamp=False)

    # Force flush at end of request so performance data is visible
    _flush_buffers()

    # Reset
    _request_start = 0
    _current_timings = {}

    return total_time


@contextmanager
def timer(component: str, extra: str = ""):
    """
    Context manager for timing a block of code.

    Usage:
        with timer("audio_capture"):
            audio = capture_audio()
    """
    start = time.time()
    log(f"[START] {component}", "DEBUG")
    try:
        yield
    finally:
        duration = time.time() - start
        log_timing(component, duration, extra)


def timed(component: str):
    """
    Decorator for timing a function.

    Usage:
        @timed("transcription")
        def transcribe(audio):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with timer(component):
                return func(*args, **kwargs)
        return wrapper
    return decorator


def log_audio_capture(duration_seconds: float, audio_length_seconds: float, samples: int):
    """Log audio capture details."""
    log(f"[AUDIO] Captured {audio_length_seconds:.2f}s audio ({samples} samples) in {duration_seconds*1000:.0f}ms", "INFO")
    log_timing("audio_capture", duration_seconds, f"{audio_length_seconds:.2f}s audio")


def log_transcription(duration_seconds: float, text: str, confidence: float):
    """Log transcription details."""
    text_preview = text[:60] + "..." if len(text) > 60 else text
    log(f"[STT] '{text_preview}' (conf={confidence:.2f}) in {duration_seconds*1000:.0f}ms", "INFO")
    log_timing("transcription", duration_seconds, f"conf={confidence:.2f}")


def log_validation(duration_seconds: float, is_valid: bool, reason: str, text: str):
    """Log validation details."""
    status = "VALID" if is_valid else f"REJECTED({reason})"
    text_preview = text[:50] + "..." if len(text) > 50 else text
    log(f"[VALIDATE] {status}: '{text_preview}' in {duration_seconds*1000:.0f}ms", "INFO")
    log_timing("validation", duration_seconds, status)


def log_llm_start(question: str):
    """Log LLM request start."""
    log(f"[LLM] Starting generation for: {question[:60]}...", "INFO")


def log_llm_chunk(chunk_num: int, chunk_length: int):
    """Log LLM streaming chunk."""
    log(f"[LLM] Chunk #{chunk_num}: {chunk_length} chars", "DEBUG")


def log_llm_complete(duration_seconds: float, answer_length: int, is_streaming: bool):
    """Log LLM completion."""
    mode = "streaming" if is_streaming else "single-shot"
    log(f"[LLM] Complete ({mode}): {answer_length} chars in {duration_seconds*1000:.0f}ms", "INFO")
    log_timing("llm", duration_seconds, f"{answer_length} chars, {mode}")


def log_ui_update(duration_seconds: float, update_type: str):
    """Log UI update."""
    log(f"[UI] {update_type} in {duration_seconds*1000:.0f}ms", "DEBUG")
    log_timing("ui", duration_seconds, update_type)


def log_cache_hit(question: str):
    """Log cache hit."""
    log(f"[CACHE] HIT for: {question[:50]}...", "INFO")


def log_cache_miss(question: str):
    """Log cache miss."""
    log(f"[CACHE] MISS for: {question[:50]}...", "DEBUG")


def log_state_change(old_state: str, new_state: str):
    """Log state machine transition."""
    log(f"[STATE] {old_state} -> {new_state}", "DEBUG")


def log_queue_status(queue_size: int, action: str):
    """Log queue status."""
    log(f"[QUEUE] {action}, size={queue_size}", "DEBUG")


def get_log_paths() -> Dict[str, str]:
    """Get paths to all log files."""
    return {
        "debug": str(DEBUG_LOG),
        "performance": str(PERF_LOG),
        "errors": str(ERROR_LOG),
    }


def clear_logs():
    """Clear all log files."""
    global _dir_ensured
    _dir_ensured = False
    ensure_log_dir()
    for log_file in [DEBUG_LOG, PERF_LOG, ERROR_LOG]:
        try:
            with open(log_file, 'w') as f:
                f.write("")
        except Exception:
            pass
    log("Logs cleared", "INFO")


def get_recent_performance(n: int = 20) -> list:
    """Get recent performance log entries."""
    if not PERF_LOG.exists():
        return []
    try:
        with open(PERF_LOG, 'r') as f:
            lines = f.readlines()
            return [line.strip() for line in lines[-n:] if line.strip()]
    except Exception:
        return []


def print_performance_summary():
    """Print a summary of recent performance."""
    logs = get_recent_performance(10)
    if not logs:
        print("No performance data yet")
        return

    print("\n" + "=" * 70)
    print("RECENT PERFORMANCE (last 10 requests)")
    print("=" * 70)
    for line in logs:
        print(line)
    print("=" * 70 + "\n")


# Test
if __name__ == "__main__":
    print("Debug Logger Test")
    print(f"Log directory: {LOG_DIR}")
    print(f"Debug log: {DEBUG_LOG}")
    print(f"Performance log: {PERF_LOG}")
    print()

    # Simulate a request
    start_request()

    with timer("audio_capture"):
        time.sleep(0.5)  # Simulate audio capture

    log_transcription(0.3, "What is Python?", 0.95)
    log_validation(0.005, True, "", "What is Python?")

    log_llm_start("What is Python?")
    with timer("llm_generation"):
        time.sleep(0.8)  # Simulate LLM
    log_llm_complete(0.8, 250, True)

    total = end_request("What is Python?", 250)
    print(f"\nTotal request time: {total*1000:.0f}ms")

    print("\nLog files created:")
    for name, path in get_log_paths().items():
        exists = "EXISTS" if Path(path).exists() else "NOT FOUND"
        print(f"  {name}: {path} [{exists}]")
