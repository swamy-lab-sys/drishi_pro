"""
Output Manager for Drishi Pro

Handles console output and log file writing.
Optimized: keeps file handle open during streaming to avoid repeated open/close.
"""

import threading
import time
from pathlib import Path

# Configuration
ANSWERS_DIR = Path.home() / ".drishi"
ANSWERS_LOG = ANSWERS_DIR / "answers.log"

_write_lock = threading.Lock()
_log_handle = None
_dir_ensured = False


def ensure_answers_dir():
    """Create answers directory if not exists (cached)."""
    global _dir_ensured
    if not _dir_ensured:
        ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
        _dir_ensured = True


def _get_log_handle():
    """Get or open the log file handle."""
    global _log_handle
    if _log_handle is None or _log_handle.closed:
        ensure_answers_dir()
        _log_handle = open(ANSWERS_LOG, "a", encoding="utf-8")
    return _log_handle


def write_header(question_text):
    """Write question header to log and console."""
    timestamp = time.strftime("%H:%M:%S")
    header = f"\n{'='*60}\n"
    header += f"TIME: {timestamp}\n"
    header += f"QUESTION: {question_text}\n"
    header += f"{'='*60}\n"

    with _write_lock:
        f = _get_log_handle()
        f.write(header)
        f.flush()

    print(f"\n[Q] {question_text}")
    print("-" * 30)


def write_answer_chunk(chunk):
    """Write answer text to log and console."""
    with _write_lock:
        f = _get_log_handle()
        f.write(chunk)
        # No flush per chunk - flush on footer instead

    print(chunk, end='', flush=True)


def write_footer():
    """Write answer footer to log and flush."""
    footer = f"\n{'='*60}\n"

    with _write_lock:
        f = _get_log_handle()
        f.write(footer)
        f.flush()


def clear_answer_buffer():
    """Clear the answers log file."""
    global _log_handle
    ensure_answers_dir()
    with _write_lock:
        if _log_handle and not _log_handle.closed:
            _log_handle.close()
        _log_handle = open(ANSWERS_LOG, "w", encoding="utf-8")
        _log_handle.write(f"# Drishi Pro\n")
        _log_handle.write(f"# Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        _log_handle.flush()
