"""
Performance Logger for Drishi Pro

Persistent logging of all question/answer performance metrics.

LOG FILE: ~/.drishi/performance.log

LOG FORMAT:
2026-01-21 10:42:31 | audio=1.1s | asr=0.3s | validate=5ms | llm=1.4s | ui=20ms | total=2.8s

FEATURES:
- Persistent log file (survives restarts)
- Thread-safe writes
- Console summary ([PERF] prefix)
- Metrics attached to UI
"""

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

# Configuration
LOG_DIR = Path.home() / ".drishi"
LOG_FILE = LOG_DIR / "performance.log"

# Thread-safe write lock
_log_lock = threading.Lock()


def ensure_log_dir():
    """Create log directory if not exists."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def format_duration(seconds: float) -> str:
    """Format duration for log file."""
    if seconds is None or seconds <= 0:
        return "0ms"
    if seconds < 1.0:
        return f"{int(seconds * 1000)}ms"
    return f"{seconds:.1f}s"


def log_performance(
    question: str,
    audio_duration: float = 0.0,
    asr_duration: float = 0.0,
    validate_duration: float = 0.0,
    llm_duration: float = 0.0,
    ui_duration: float = 0.0,
    total_duration: float = 0.0,
) -> str:
    """
    Log performance metrics to file and return console summary.

    Args:
        question: The interview question (for context)
        audio_duration: Audio capture time (seconds)
        asr_duration: Speech-to-text time (seconds)
        validate_duration: Question validation time (seconds)
        llm_duration: LLM generation time (seconds)
        ui_duration: UI render time (seconds)
        total_duration: Total end-to-end time (seconds)

    Returns:
        str: Formatted log line for console output
    """
    ensure_log_dir()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Format log line
    log_line = (
        f"{timestamp} | "
        f"audio={format_duration(audio_duration)} | "
        f"asr={format_duration(asr_duration)} | "
        f"validate={format_duration(validate_duration)} | "
        f"llm={format_duration(llm_duration)} | "
        f"ui={format_duration(ui_duration)} | "
        f"total={format_duration(total_duration)}"
    )

    # Write to file (thread-safe)
    with _log_lock:
        try:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(log_line + "\n")
                f.flush()
        except Exception as e:
            pass  # Silent failure - don't break the pipeline

    return log_line


def log_with_metrics(metrics: Dict) -> str:
    """
    Log from metrics dictionary (from state.py).

    Args:
        metrics: dict with audio_ms, transcription_ms, llm_ms, total_ms, etc.

    Returns:
        str: Formatted log line
    """
    return log_performance(
        question="",
        audio_duration=metrics.get('audio_ms', 0) / 1000.0,
        asr_duration=metrics.get('transcription_ms', 0) / 1000.0,
        validate_duration=metrics.get('validate_ms', 0) / 1000.0,
        llm_duration=metrics.get('llm_ms', 0) / 1000.0,
        ui_duration=metrics.get('ui_ms', 0) / 1000.0,
        total_duration=metrics.get('total_ms', 0) / 1000.0,
    )


def get_console_summary(metrics: Dict) -> str:
    """
    Get a short console summary with [PERF] prefix.

    Args:
        metrics: dict with timing values in milliseconds

    Returns:
        str: Formatted summary like "[PERF] audio=1.1s | asr=300ms | llm=1.4s | total=2.8s"
    """
    parts = ["[PERF]"]

    if metrics.get('audio_ms'):
        parts.append(f"audio={format_duration(metrics['audio_ms'] / 1000.0)}")

    if metrics.get('transcription_ms'):
        parts.append(f"asr={format_duration(metrics['transcription_ms'] / 1000.0)}")

    if metrics.get('validate_ms'):
        parts.append(f"validate={format_duration(metrics['validate_ms'] / 1000.0)}")

    if metrics.get('llm_ms'):
        parts.append(f"llm={format_duration(metrics['llm_ms'] / 1000.0)}")

    if metrics.get('ui_ms'):
        parts.append(f"ui={format_duration(metrics['ui_ms'] / 1000.0)}")

    if metrics.get('total_ms'):
        parts.append(f"total={format_duration(metrics['total_ms'] / 1000.0)}")

    return " | ".join(parts)


def get_log_file_path() -> str:
    """Get the path to the performance log file."""
    return str(LOG_FILE)


def clear_log():
    """Clear the performance log file."""
    ensure_log_dir()
    with _log_lock:
        try:
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                f.write("")
        except Exception:
            pass


def get_recent_logs(n: int = 10) -> list:
    """
    Get the most recent log entries.

    Args:
        n: Number of entries to return

    Returns:
        list of log line strings
    """
    if not LOG_FILE.exists():
        return []

    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            return [line.strip() for line in lines[-n:] if line.strip()]
    except Exception:
        return []


def get_average_latency(n: int = 10) -> Dict:
    """
    Calculate average latencies from recent logs.

    Args:
        n: Number of recent entries to average

    Returns:
        dict with average values for each metric
    """
    logs = get_recent_logs(n)
    if not logs:
        return {}

    totals = {
        'audio': [],
        'asr': [],
        'validate': [],
        'llm': [],
        'ui': [],
        'total': [],
    }

    for line in logs:
        # Parse each metric from log line
        for metric in totals.keys():
            try:
                # Find pattern like "audio=1.1s" or "audio=300ms"
                import re
                pattern = rf'{metric}=(\d+(?:\.\d+)?)(ms|s)'
                match = re.search(pattern, line)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2)
                    if unit == 's':
                        value *= 1000  # Convert to ms
                    totals[metric].append(value)
            except Exception:
                pass

    # Calculate averages
    averages = {}
    for metric, values in totals.items():
        if values:
            averages[f'{metric}_avg_ms'] = sum(values) / len(values)

    return averages


# Module-level test
if __name__ == "__main__":
    print("Performance Logger Test")
    print("=" * 60)
    print(f"Log file: {get_log_file_path()}")
    print()

    # Test logging
    log_line = log_performance(
        question="What is Python?",
        audio_duration=1.1,
        asr_duration=0.3,
        validate_duration=0.005,
        llm_duration=1.4,
        ui_duration=0.02,
        total_duration=2.835,
    )
    print(f"Logged: {log_line}")

    # Test console summary
    metrics = {
        'audio_ms': 1100,
        'transcription_ms': 300,
        'validate_ms': 5,
        'llm_ms': 1400,
        'ui_ms': 20,
        'total_ms': 2835,
    }
    summary = get_console_summary(metrics)
    print(f"Console: {summary}")

    # Show recent logs
    print()
    print("Recent logs:")
    for line in get_recent_logs(5):
        print(f"  {line}")
