"""
STT Auto-Learner — discovers and persists STT mishear corrections automatically.

Pipeline (fully async, zero hot-path impact):
  STT mishear detected → intent_correction LLM fixes it →
  submit_correction(wrong, right) → background queue →
  word-diff extraction → SQLite stt_corrections table →
  hot-reload into stt._COMPILED_CORRECTIONS (every 5 min)

No LLM on the learning path — extraction uses difflib word-level diff.
The intent-correction LLM already validated the correction; we just record it.
"""

from __future__ import annotations

import difflib
import queue
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_conn():
    import qa_database as _qdb
    return _qdb._get_conn()


def _ensure_table():
    """Create stt_corrections table if not present (migration-safe)."""
    try:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stt_corrections (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                wrong      TEXT    NOT NULL COLLATE NOCASE,
                right_text TEXT    NOT NULL,
                source     TEXT    DEFAULT 'auto',
                hit_count  INTEGER DEFAULT 0,
                created_at TEXT    NOT NULL,
                UNIQUE(wrong COLLATE NOCASE)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stt_wrong ON stt_corrections(wrong COLLATE NOCASE)")
        conn.commit()
        conn.close()
    except Exception:
        pass


_table_ready = False


def _init_table():
    global _table_ready
    if not _table_ready:
        _ensure_table()
        _table_ready = True


# ── Word-level diff extraction ─────────────────────────────────────────────────

def _normalize_for_diff(text: str) -> str:
    return text.lower().strip()


def extract_corrections(wrong: str, right: str) -> List[Tuple[str, str]]:
    """
    Find word-level substitutions between two strings.
    Returns list of (wrong_phrase, right_phrase) pairs.

    Examples:
      "What is gill in Python?" / "What is GIL in Python?"
        → [("gill", "GIL")]

      "What is cube nettis?" / "What is Kubernetes?"
        → [("cube nettis", "Kubernetes")]

      "What is CACD pipeline?" / "What is CI/CD pipeline?"
        → [("cacd", "CI/CD")]
    """
    w_words = _normalize_for_diff(wrong).split()
    r_words = right.split()   # keep original casing for right side
    r_norm  = [w.lower() for w in r_words]

    # Skip if strings are identical after normalization
    if w_words == r_norm:
        return []

    # Strip trailing punctuation from last word in each split
    _strip = re.compile(r'[?.!,;:]+$')

    matcher = difflib.SequenceMatcher(None, w_words, r_norm, autojunk=False)
    corrections = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'replace':
            w_phrase = ' '.join(w_words[i1:i2])
            r_phrase = ' '.join(r_words[j1:j2])   # original casing from corrected side
            # Strip trailing punctuation (prevents "gill?" → "GIL?" storing punctuation)
            w_phrase = _strip.sub('', w_phrase).strip()
            r_phrase = _strip.sub('', r_phrase).strip()
            if not w_phrase or not r_phrase:
                continue
            # Sanity: skip trivial case-only changes
            if w_phrase.lower() == r_phrase.lower():
                continue
            # Skip single common words (too broad, causes false positives)
            # Allow if it's clearly a proper noun, acronym, or multi-word correction
            if len(w_phrase.split()) == 1 and len(r_phrase.split()) == 1:
                if not (r_phrase[0].isupper() or r_phrase.isupper() or '/' in r_phrase):
                    continue
            corrections.append((w_phrase, r_phrase))
        elif tag == 'delete':
            # Handle word deletions: "deep coffee copy" → "deep copy" (coffee deleted)
            # Build a context window: 1 before + deleted words + 1 after (both sides)
            w_phrase = ' '.join(w_words[i1:i2])
            w_phrase_clean = _strip.sub('', w_phrase).strip()
            if not w_phrase_clean or len(w_phrase_clean.split()) > 3:
                continue
            ctx_start   = max(0, i1 - 1)
            ctx_end_w   = min(len(w_words), i2 + 1)   # include 1 after in wrong
            ctx_end_r   = min(len(r_words), j1 + 1)   # same position on right side
            ctx_start_r = max(0, j1 - 1)
            w_ctx = ' '.join(w_words[ctx_start:ctx_end_w])
            r_ctx = ' '.join(r_words[ctx_start_r:ctx_end_r])
            w_ctx = _strip.sub('', w_ctx).strip()
            r_ctx = _strip.sub('', r_ctx).strip()
            if w_ctx and r_ctx and w_ctx.lower() != r_ctx.lower() and len(r_ctx.split()) >= 1:
                corrections.append((w_ctx, r_ctx))
    return corrections


# ── Background submission queue ────────────────────────────────────────────────

_submission_queue: queue.Queue = queue.Queue(maxsize=500)
_worker_started = False
_worker_lock = threading.Lock()


def _start_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_correction_worker, daemon=True, name="stt-learner")
        t.start()
        _worker_started = True


def _correction_worker():
    """Background thread: drains submission queue, writes to DB."""
    _init_table()
    while True:
        try:
            item = _submission_queue.get(timeout=30)
        except queue.Empty:
            continue
        if item is None:
            break
        wrong_orig, right_orig = item
        try:
            pairs = extract_corrections(wrong_orig, right_orig)
            if not pairs:
                # If no word-level diff, store the full phrase pair as fallback
                _upsert(wrong_orig.lower().strip(), right_orig.strip())
            else:
                for wrong_phrase, right_phrase in pairs:
                    _upsert(wrong_phrase.lower().strip(), right_phrase.strip())
        except Exception as e:
            pass  # Never let learner crash the server


def _upsert(wrong: str, right_text: str):
    """Insert or increment hit_count if already known."""
    if not wrong or not right_text or wrong == right_text.lower():
        return
    # Skip if already in static corrections (no need to store again)
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO stt_corrections (wrong, right_text, source, created_at)
               VALUES (?, ?, 'auto', datetime('now'))
               ON CONFLICT(wrong) DO UPDATE SET
                   hit_count = hit_count + 1,
                   right_text = excluded.right_text""",
            (wrong, right_text)
        )
        conn.commit()
        conn.close()
        print(f"[STT/learn] Stored correction: {wrong!r} → {right_text!r}")
    except Exception:
        pass


# ── Public API ─────────────────────────────────────────────────────────────────

def submit_correction(wrong: str, right: str):
    """
    Called (async, non-blocking) when intent correction detects a mishear.
    Both strings are full question-level text.
    The background worker extracts word-level pairs and persists them.
    """
    if not wrong or not right or wrong.strip().lower() == right.strip().lower():
        return
    _start_worker()
    try:
        _submission_queue.put_nowait((wrong.strip(), right.strip()))
    except queue.Full:
        pass  # Drop if queue full — next occurrence will be caught


def load_learned_corrections() -> Dict[str, str]:
    """Load all learned corrections from DB as {wrong_lower: right} dict."""
    _init_table()
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT wrong, right_text FROM stt_corrections ORDER BY hit_count DESC"
        ).fetchall()
        conn.close()
        return {r["wrong"].lower(): r["right_text"] for r in rows}
    except Exception:
        return {}


def get_all_corrections(limit: int = 200) -> List[dict]:
    """Return corrections for admin UI (most-hit first)."""
    _init_table()
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, wrong, right_text, source, hit_count, created_at "
            "FROM stt_corrections ORDER BY hit_count DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def delete_correction(correction_id: int) -> bool:
    """Delete a learned correction by ID (admin action)."""
    _init_table()
    try:
        conn = _get_conn()
        rows = conn.execute(
            "DELETE FROM stt_corrections WHERE id=?", (correction_id,)
        ).rowcount
        conn.commit()
        conn.close()
        return rows > 0
    except Exception:
        return False


# ── Hot-reload into stt._COMPILED_CORRECTIONS ─────────────────────────────────

_last_reload: float = 0.0
_RELOAD_INTERVAL = 300  # 5 minutes


def reload_into_stt(force: bool = False):
    """
    Merge learned corrections into stt._COMPILED_CORRECTIONS.
    Called at startup and then every 5 minutes by a background thread.
    Thread-safe: replaces the list atomically.
    """
    global _last_reload
    now = time.time()
    if not force and now - _last_reload < _RELOAD_INTERVAL:
        return
    _last_reload = now

    learned = load_learned_corrections()
    if not learned:
        return

    try:
        import stt as _stt
        # Build new compiled list for learned corrections
        new_compiled = [
            (re.compile(re.escape(wrong), re.IGNORECASE), right)
            for wrong, right in learned.items()
        ]
        # Atomically prepend learned corrections before static ones
        # (learned corrections take priority if there's overlap)
        static = list(_stt._STATIC_COMPILED_CORRECTIONS)
        _stt._COMPILED_CORRECTIONS = new_compiled + static
        pass  # silent reload
    except Exception as e:
        print(f"[STT/learn] Reload error: {e}")


def _start_reload_thread():
    """Background thread that periodically hot-reloads into stt module."""
    def _loop():
        # Wait a bit for server to fully start
        time.sleep(10)
        while True:
            try:
                reload_into_stt()
            except Exception:
                pass
            time.sleep(_RELOAD_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="stt-learner-reload")
    t.start()
