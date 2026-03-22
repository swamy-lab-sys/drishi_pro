"""
Answer Storage for Drishi Pro

ACCUMULATING ANSWER MODE WITH PERFORMANCE METRICS

Features:
- ALL Q&A answers kept and displayed (no overwriting)
- New answers added to the list (interview history preserved)
- Performance metrics (latency) stored with each answer
- Thread-safe operations
"""

import json
import os
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

try:
    import event_bus as _event_bus
except ImportError:
    _event_bus = None   # Graceful fallback if bus not available

# Configuration
ANSWERS_DIR = Path.home() / ".drishi"
CURRENT_ANSWER_FILE = ANSWERS_DIR / "current_answer.json"
HISTORY_FILE = ANSWERS_DIR / "answer_history.jsonl"
MASTER_LOG_FILE = ANSWERS_DIR / "interview_master_log.jsonl"  # Permanent storage

# Thread-safe write lock
_write_lock = threading.Lock()

# Unique ID for the current server/main.py session.
# Generated at startup (clear_all) and embedded in current_answer.json.
# The browser compares this to sessionStorage to detect fresh page loads vs
# SSE reconnects — only reconnects restore the history feed.
import uuid as _uuid
_session_id: str = _uuid.uuid4().hex

# All answers for this session (accumulates, never overwrites)
_all_answers: List[Dict[str, Any]] = []

# Duplicate lookup index: lowercase question -> index in _all_answers
_answer_index: Dict[str, int] = {}

# Current answer buffer (the one being streamed right now)
_current_answer: Dict[str, Any] = {
    'question': '',
    'answer': '',
    'timestamp': '',
    'is_complete': False,
    'metrics': None,  # Performance metrics
}

# Directory creation cache - avoid repeated mkdir syscalls
_dir_ensured = False

# Throttled write state for streaming chunks
_last_write_time: float = 0.0
_WRITE_THROTTLE_INTERVAL = 0.03  # Write to disk at most every 30ms during streaming

# mtime of current_answer.json after our last write — used to skip the expensive
# cross-process sync read when we ourselves were the last writer.
_our_last_write_mtime: float = 0.0

# Read cache for get_all_answers() - avoids redundant disk reads from SSE poller
_read_cache: Optional[List[Dict[str, Any]]] = None
_read_cache_mtime: float = 0.0

# Live transcription state — shown in UI while STT is active
_transcribing_text: str = ""
_TRANSCRIBING_FILE = ANSWERS_DIR / "transcribing.json"


def ensure_answers_dir():
    """Create answers directory if not exists (cached after first call)."""
    global _dir_ensured
    if not _dir_ensured:
        ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
        _dir_ensured = True


def _write_current(force: bool = False):
    """Write all answers (including current in-progress) to file for web UI.

    Args:
        force: If True, always write immediately (used for complete answers).
               If False, throttle writes to reduce disk I/O during streaming.
    """
    global _last_write_time, _all_answers, _answer_index, _read_cache

    now = time.monotonic()
    if not force and (now - _last_write_time) < _WRITE_THROTTLE_INTERVAL:
        return

    try:
        ensure_answers_dir()

        # Cross-process disk sync: only on forced writes (complete answers)
        # During streaming (force=False), skip the expensive disk read since
        # the writer process owns the authoritative state.
        if force and CURRENT_ANSWER_FILE.exists():
            try:
                with open(CURRENT_ANSWER_FILE, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        raw = json.loads(content)
                        # Handle envelope {session_id, answers} or legacy list
                        disk_data = raw.get('answers', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                        # Find answers on disk that we don't have in memory
                        memory_questions = {a.get('question', '').strip().lower() for a in _all_answers if a.get('question')}
                        for disk_ans in disk_data:
                            if isinstance(disk_ans, dict) and disk_ans.get('question'):
                                q_lower = disk_ans['question'].strip().lower()
                                if q_lower not in memory_questions:
                                    # Add disk answer to memory
                                    _answer_index[q_lower] = len(_all_answers)
                                    _all_answers.append(disk_ans)
                                    memory_questions.add(q_lower)
            except Exception:
                pass  # If read fails, proceed with memory state

        # Build full list: completed answers + current in-progress answer
        display_list = list(_all_answers)
        # If current answer is being streamed (not yet in _all_answers), add it
        if _current_answer.get('question') and not _current_answer.get('is_complete'):
            display_list.append(_current_answer)
        # Wrap in envelope so the browser can identify the server session
        payload = {"session_id": _session_id, "answers": display_list}
        with open(CURRENT_ANSWER_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            if force:
                os.fsync(f.fileno())
        _last_write_time = now
        _read_cache = None  # Invalidate read cache after write
        global _our_last_write_mtime
        try:
            _our_last_write_mtime = CURRENT_ANSWER_FILE.stat().st_mtime
        except Exception:
            pass
    except Exception:
        pass


def _save_to_history():
    """Append completed answer to history file."""
    try:
        ensure_answers_dir()
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            json.dump(_current_answer, f, ensure_ascii=False)
            f.write('\n')
            f.flush()
    except Exception:
        pass


def _log_permanent(data: Dict[str, Any]):
    """Save to master log (called inline, no thread spawn)."""
    try:
        ensure_answers_dir()
        # Append to master log - NEVER CLEARED
        with open(MASTER_LOG_FILE, 'a', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
            f.write('\n')
    except Exception:
        pass


def clear_all(force_clear: bool = False):
    """Clear all stored answers.

    Args:
        force_clear: If True, delete disk files (fresh start).
                    If False, only clear memory (allows recovery on restart).
    """
    global _current_answer, _all_answers, _answer_index, _dir_ensured, _session_id

    _dir_ensured = False
    ensure_answers_dir()

    with _write_lock:
        _current_answer = {
            'question': '',
            'answer': '',
            'timestamp': '',
            'is_complete': False,
            'metrics': None,
        }
        _all_answers = []
        _answer_index = {}
        # New session ID so the browser knows to start fresh
        _session_id = _uuid.uuid4().hex

        # Only delete files if explicitly requested (manual clear)
        if force_clear:
            try:
                with open(CURRENT_ANSWER_FILE, 'w', encoding='utf-8') as f:
                    json.dump({"session_id": _session_id, "answers": []}, f, ensure_ascii=False)

                # Clear history file (Session only)
                if HISTORY_FILE.exists():
                    HISTORY_FILE.unlink()

                # NOTE: We DO NOT clear MASTER_LOG_FILE

            except Exception:
                pass


def set_processing_question(question_text: str):
    """
    Set current question in 'thinking' mode.
    New question starts streaming -- added to the list, not replacing.
    """
    global _current_answer
    with _write_lock:
        _current_answer = {
            'question': question_text.strip(),
            'answer': '',  # Start empty for streaming
            'timestamp': datetime.now().isoformat(),
            'is_complete': False,
            'metrics': None,
        }
        _write_current(force=True)
    # Push placeholder event so frontend creates card instantly (before DB/LLM answer)
    if _event_bus:
        _event_bus.push_question_started(_current_answer['question'])


def append_answer_chunk(chunk: str):
    """Append a chunk of text to the current answer (Streaming).

    Writes are throttled to reduce disk I/O during rapid streaming.
    Pushes delta event immediately via event_bus (<1ms vs 80ms disk throttle).
    """
    global _current_answer
    with _write_lock:
        _current_answer['answer'] += chunk
        _write_current(force=False)
    # Push delta to all SSE clients immediately (non-blocking, outside lock)
    if _event_bus:
        _event_bus.push_chunk(_current_answer['question'], chunk)


def update_current_question(new_question: str):
    """Update the current placeholder's question text after intent correction.

    Called when the LLM corrects a misheard/garbled question so that the
    placeholder card key on disk matches the key used by set_complete_answer().
    """
    global _current_answer
    with _write_lock:
        if _current_answer.get('question') and not _current_answer.get('is_complete'):
            _current_answer['question'] = new_question.strip()
            _write_current(force=True)


def flush_current_to_disk():
    """Force-flush the current in-progress answer to disk.

    Call this after streaming ends to ensure the final state is persisted.
    """
    with _write_lock:
        _write_current(force=True)


def set_complete_answer(
    question_text: str,
    answer_text: str,
    metrics: Optional[Dict[str, int]] = None
):
    """
    Set complete answer with optional metrics.

    ATOMIC OPERATION:
    - Syncs from disk first (handles multi-process scenarios)
    - Finalizes the current answer
    - Adds it to the accumulated session list
    - Stores performance metrics
    - Single UI update
    - Logs to permanent storage
    """
    global _current_answer, _all_answers, _answer_index

    ensure_answers_dir()

    with _write_lock:
        # CRITICAL: Sync from disk only if another process wrote since our last write.
        # Skip expensive disk read when we were the last writer (single-process scenarios).
        _should_sync = False
        if CURRENT_ANSWER_FILE.exists():
            try:
                disk_mtime = CURRENT_ANSWER_FILE.stat().st_mtime
                _should_sync = disk_mtime > _our_last_write_mtime + 0.01
            except Exception:
                pass

        if _should_sync and CURRENT_ANSWER_FILE.exists():
            try:
                with open(CURRENT_ANSWER_FILE, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        raw = json.loads(content)
                        # Handle envelope format {session_id, answers} or legacy list
                        disk_data = raw.get('answers', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                        # Merge disk data with memory (disk is source of truth)
                        disk_questions = {a.get('question', '').strip().lower() for a in disk_data if a.get('question')}
                        memory_questions = {a.get('question', '').strip().lower() for a in _all_answers}

                        # If disk has answers we don't have in memory, use disk as base
                        if disk_questions - memory_questions:
                            _all_answers = [a for a in disk_data if isinstance(a, dict) and a.get('question')]
                            # Rebuild index
                            _answer_index = {}
                            for i, ans in enumerate(_all_answers):
                                q_lower = ans.get('question', '').strip().lower()
                                if q_lower:
                                    _answer_index[q_lower] = i
            except Exception:
                pass  # If read fails, proceed with memory state

        _current_answer = {
            'question': question_text.strip(),
            'answer': answer_text.strip(),
            'timestamp': datetime.now().isoformat(),
            'is_complete': True,
            'metrics': metrics,
        }
        # O(1) duplicate lookup via index dict
        q_lower = question_text.strip().lower()
        duplicate_idx = _answer_index.get(q_lower)

        if duplicate_idx is not None and duplicate_idx < len(_all_answers):
            # Update existing entry instead of adding duplicate
            _all_answers[duplicate_idx] = _current_answer.copy()
        else:
            # New question -- add to the list and index
            _answer_index[q_lower] = len(_all_answers)
            _all_answers.append(_current_answer.copy())

        _write_current(force=True)
        _save_to_history()

        # Permanent logging inline (cheap append, no thread needed)
        _log_permanent(_current_answer.copy())

    # Push complete event to all SSE clients immediately (outside lock)
    if _event_bus:
        _event_bus.push_complete(question_text, answer_text, metrics)



def set_transcribing(text: str):
    """Update live transcription state shown in UI (🎤 Hearing: ...).
    Pushes via event_bus so SSE clients receive it instantly.
    """
    global _transcribing_text
    _transcribing_text = text.strip() if text else ""
    # Push live transcription to SSE clients immediately
    if _event_bus:
        _event_bus.push_transcribing(_transcribing_text)
    try:
        ensure_answers_dir()
        with open(_TRANSCRIBING_FILE, 'w', encoding='utf-8') as f:
            json.dump({'text': _transcribing_text}, f)
    except Exception:
        pass


def get_transcribing() -> str:
    """Get current transcription text (for SSE)."""
    try:
        if _TRANSCRIBING_FILE.exists():
            with open(_TRANSCRIBING_FILE, 'r', encoding='utf-8') as f:
                return json.load(f).get('text', '')
    except Exception:
        pass
    return _transcribing_text


def get_current_answer() -> Optional[Dict[str, Any]]:
    """
    Get the current (latest) answer.

    Returns:
        dict with question, answer, timestamp, is_complete, metrics
        or None if no answer
    """
    if _current_answer.get('question'):
        return _current_answer.copy()
    return None


def get_all_answers() -> list:
    """
    Get ALL session answers (newest first).
    No deduplication, no limit -- every Q&A is preserved for interview reference.

    Uses mtime-guarded cache to avoid redundant disk reads from SSE poller.
    The main process writes to disk, the web server (separate process) reads from disk.
    """
    global _read_cache, _read_cache_mtime

    display_list = []

    # Check disk file with mtime guard to avoid redundant reads
    try:
        if CURRENT_ANSWER_FILE.exists():
            mtime = CURRENT_ANSWER_FILE.stat().st_mtime
            if _read_cache is not None and mtime == _read_cache_mtime:
                # File hasn't changed, return cached result
                return list(_read_cache)

            # File changed, re-read
            with open(CURRENT_ANSWER_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Handle envelope {session_id, answers} or legacy list
                if isinstance(data, dict) and 'answers' in data:
                    display_list = [a for a in data['answers'] if a.get('question')]
                elif isinstance(data, list):
                    display_list = [a for a in data if a.get('question')]

            # Newest first for display
            display_list.reverse()
            # Cache the result
            _read_cache = list(display_list)
            _read_cache_mtime = mtime
            return display_list
    except Exception:
        pass

    # Fallback to in-memory if disk file missing (main process only)
    if not display_list:
        with _write_lock:
            display_list = list(_all_answers)
            if _current_answer.get('question') and not _current_answer.get('is_complete'):
                display_list.append(_current_answer.copy())

    # Newest first for display
    display_list.reverse()
    return display_list


def is_already_answered(question_text: str) -> Optional[Dict[str, Any]]:
    """O(1) check if a question has already been answered.

    Returns the existing answer dict if found, None otherwise.
    """
    q_lower = question_text.strip().lower()
    with _write_lock:
        idx = _answer_index.get(q_lower)
        if idx is not None and idx < len(_all_answers):
            return _all_answers[idx].copy()
    return None


def get_latest_answer() -> Optional[Dict[str, Any]]:
    """Get the most recent answer."""
    return get_current_answer()


def get_answers_file_path() -> str:
    """Get path to current answer file."""
    return str(CURRENT_ANSWER_FILE)


def get_history_file_path() -> str:
    """Get path to history file."""
    return str(HISTORY_FILE)


def get_answer_count() -> int:
    """Get count of historical answers."""
    if not HISTORY_FILE.exists():
        return 0
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


# Backward compatibility aliases
def clear_and_start_new(question_text: str):
    """Deprecated: Use set_complete_answer instead."""
    pass


def start_new_answer(question_text: str):
    """Deprecated: Use set_complete_answer instead."""
    pass


def finalize_answer():
    """Deprecated: Use set_complete_answer instead."""
    pass


def clear_answers():
    """Alias for clear_all."""
    clear_all()


def load_history_on_startup():
    """Load existing answers from disk into memory on startup.
    
    Filters out incomplete answers (from crashes) and handles corrupted files.
    """
    global _all_answers, _answer_index
    
    # Ensure directory exists
    ensure_answers_dir()

    if CURRENT_ANSWER_FILE.exists():
        try:
            with open(CURRENT_ANSWER_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return

                raw = json.loads(content)
                # Handle envelope {session_id, answers} or legacy list
                data = raw.get('answers', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                if data:
                    with _write_lock:
                        # Filter for COMPLETE answers only (exclude partial answers from crashes)
                        _all_answers = [
                            a for a in data
                            if isinstance(a, dict)
                            and a.get('question')
                            and a.get('is_complete', True)  # Only complete answers
                        ]
                        
                        # Rebuild index
                        _answer_index = {}
                        for i, ans in enumerate(_all_answers):
                            q_lower = ans.get('question', '').strip().lower()
                            if q_lower:
                                _answer_index[q_lower] = i
                        
                        if _all_answers:
                            print(f"[STORAGE] ✓ Restored {len(_all_answers)} Q&A from previous session")
        except json.JSONDecodeError as e:
            print(f"[STORAGE] ⚠️ Corrupted history file, starting fresh: {e}")
            # Rename corrupted file so data isn't lost, then start fresh
            try:
                import shutil
                _history_path = Path(config.ANSWERS_DIR).expanduser() / "history.json"
                _backup_path = _history_path.with_suffix(f".corrupted.{int(time.time())}.json")
                shutil.move(str(_history_path), str(_backup_path))
                print(f"[STORAGE] Corrupted file saved to: {_backup_path.name}")
            except Exception:
                pass
            clear_all(force_clear=True)
        except Exception as e:
            print(f"[STORAGE] ⚠️ Failed to load history: {e}")

# NOTE: Do not auto-load at import. main.py calls clear_all() immediately after import.
# Web server.py should call load_history_on_startup() explicitly if needed.
