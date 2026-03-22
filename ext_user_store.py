"""
Extension User Store — multi-user support for Chrome extension.

Users are stored in the main SQLite database (ext_users table).
Each token maps to an isolated pipeline:
    audio → STT → validate → DB(role) → LLM → own answer storage

Tokens are created by admin and shared with users.
The token IS the authentication — no separate secret code needed.
Usage is tracked per-user for future billing.
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_lock = threading.Lock()

# ── JSON migration (one-time, from old extension_users.json) ──────────────────

_OLD_JSON = Path(__file__).parent / 'extension_users.json'


def _migrate_json_to_db():
    """One-time migration: import users from extension_users.json → SQLite."""
    if not _OLD_JSON.exists():
        return
    try:
        import qa_database as _db
        data = json.loads(_OLD_JSON.read_text('utf-8'))
        users = data.get('users', {})
        for token, u in users.items():
            existing = _db_get_user(token)
            if existing:
                continue
            try:
                create_user(
                    token,
                    u.get('name', token),
                    u.get('role', ''),
                    u.get('coding_language', 'python'),
                    u.get('db_user_id', 1),
                )
                # Migrate settings if present
                updates = {}
                for field in ('speed_preset', 'silence_duration', 'llm_model', 'active'):
                    if field in u:
                        updates[field] = u[field]
                if updates:
                    update_user(token, updates)
            except Exception:
                pass
        # Rename old file so migration doesn't run again
        _OLD_JSON.rename(_OLD_JSON.with_suffix('.json.migrated'))
    except Exception:
        pass


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_get_user(token: str) -> Optional[dict]:
    try:
        import qa_database as _db
        conn = _db._get_conn()
        row = conn.execute(
            "SELECT * FROM ext_users WHERE token=?", (token,)
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_user(token: str) -> Optional[dict]:
    """Return user config if token exists and is active, else None."""
    if not token:
        return None
    user = _db_get_user(token)
    if user and user.get('active', 1):
        return user
    return None


def list_users() -> List[dict]:
    """Return all extension users ordered by creation time."""
    try:
        import qa_database as _db
        conn = _db._get_conn()
        rows = conn.execute(
            "SELECT * FROM ext_users ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def create_user(token: str, name: str, role: str = '',
                coding_language: str = 'python', db_user_id: int = 1) -> tuple:
    """Create a new extension user. Returns (ok, error_msg)."""
    token = token.strip()
    if not token or len(token) < 3:
        return False, 'Token must be at least 3 characters'
    if not name.strip():
        return False, 'Name is required'
    if _db_get_user(token):
        return False, f'Token "{token}" already exists'
    try:
        import qa_database as _db
        conn = _db._get_conn()
        conn.execute(
            """INSERT INTO ext_users
               (token, name, role, coding_language, db_user_id, active,
                speed_preset, silence_duration, llm_model,
                stt_backend, stt_model, created_at)
               VALUES (?,?,?,?,?,1,'balanced',1.2,'claude-haiku-4-5-20251001',
                       'sarvam','sarvam-saarika-v2',?)""",
            (token, name.strip(), role.strip(),
             coding_language.strip() or 'python',
             int(db_user_id) if str(db_user_id).isdigit() else 1,
             datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        return True, ''
    except Exception as e:
        return False, str(e)


def update_user(token: str, updates: dict) -> bool:
    """Update allowed fields for an existing user."""
    allowed = {
        'name', 'role', 'coding_language', 'db_user_id', 'active',
        'speed_preset', 'silence_duration', 'llm_model',
        'stt_backend', 'stt_model', 'last_seen',
    }
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return True
    try:
        import qa_database as _db
        conn = _db._get_conn()
        set_clause = ', '.join(f'{k}=?' for k in fields)
        conn.execute(
            f"UPDATE ext_users SET {set_clause} WHERE token=?",
            (*fields.values(), token),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def delete_user(token: str) -> bool:
    """Permanently remove an extension user and their usage logs."""
    try:
        import qa_database as _db
        conn = _db._get_conn()
        rows = conn.execute(
            "DELETE FROM ext_users WHERE token=?", (token,)
        ).rowcount
        conn.execute("DELETE FROM usage_log WHERE token=?", (token,))
        conn.commit()
        conn.close()
        return rows > 0
    except Exception:
        return False


# ── Usage tracking ────────────────────────────────────────────────────────────

def log_usage(token: str, question: str, source: str = 'db', answer_ms: int = 0):
    """Record one question event for a user (billing/analytics)."""
    if not token:
        return
    try:
        import qa_database as _db
        conn = _db._get_conn()
        conn.execute(
            "INSERT INTO usage_log (token, question, source, answer_ms, created_at) VALUES (?,?,?,?,?)",
            (token, question[:500], source, int(answer_ms), datetime.now().isoformat()),
        )
        # Increment counters
        if source == 'llm':
            conn.execute(
                "UPDATE ext_users SET total_questions=total_questions+1, "
                "total_llm_hits=total_llm_hits+1, last_seen=? WHERE token=?",
                (datetime.now().isoformat(), token),
            )
        else:
            conn.execute(
                "UPDATE ext_users SET total_questions=total_questions+1, "
                "last_seen=? WHERE token=?",
                (datetime.now().isoformat(), token),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_usage_log(token: str, limit: int = 50) -> List[dict]:
    """Return recent usage log entries for a token."""
    try:
        import qa_database as _db
        conn = _db._get_conn()
        rows = conn.execute(
            "SELECT * FROM usage_log WHERE token=? ORDER BY created_at DESC LIMIT ?",
            (token, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_all_usage_summary() -> List[dict]:
    """Return per-user usage summary for admin billing table."""
    try:
        import qa_database as _db
        conn = _db._get_conn()
        rows = conn.execute("""
            SELECT e.token, e.name, e.role, e.total_questions, e.total_llm_hits,
                   e.last_seen, e.active
            FROM ext_users e
            ORDER BY e.total_questions DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Per-user isolated answer storage ─────────────────────────────────────────

_user_storages: Dict[str, 'UserAnswerStorage'] = {}
_storages_lock = threading.Lock()


def get_user_storage(token: str) -> Optional['UserAnswerStorage']:
    """Get (or create) isolated answer storage for an extension user token."""
    if not token:
        return None
    with _storages_lock:
        if token not in _user_storages:
            cfg = get_user(token)
            if not cfg:
                return None
            _user_storages[token] = UserAnswerStorage(token)
        return _user_storages.get(token)


def release_user_storage(token: str):
    """Remove in-memory storage for a token (disk file preserved)."""
    with _storages_lock:
        _user_storages.pop(token, None)


class UserAnswerStorage:
    """
    Isolated per-user answer storage.
    Data lives in ~/.drishi/ext_users/<token>/current_answer.json
    """
    def __init__(self, token: str):
        import uuid
        self.token      = token
        self._dir       = Path.home() / '.drishi' / 'ext_users' / token
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file      = self._dir / 'current_answer.json'
        self._lock      = threading.Lock()
        self.session_id = uuid.uuid4().hex
        self._answers:  List[dict] = []
        self._index:    Dict[str, int] = {}
        self._current   = {
            'question': '', 'answer': '',
            'timestamp': '', 'is_complete': False, 'metrics': None,
        }
        self._last_write = 0.0
        self._load_existing()

    def _load_existing(self):
        try:
            if self._file.exists():
                data = json.loads(self._file.read_text('utf-8'))
                answers = (
                    data.get('answers', []) if isinstance(data, dict)
                    else (data if isinstance(data, list) else [])
                )
                with self._lock:
                    self._answers = [
                        a for a in answers
                        if isinstance(a, dict) and a.get('question') and a.get('is_complete')
                    ]
                    self._index = {
                        a['question'].strip().lower(): i
                        for i, a in enumerate(self._answers)
                    }
        except Exception:
            pass

    def _write(self, force=False):
        now = time.time()
        if not force and now - self._last_write < 0.03:
            return
        try:
            answers = list(self._answers)
            if self._current.get('question') and not self._current.get('is_complete'):
                answers = answers + [self._current]
            payload = {
                'session_id': self.session_id,
                'user_token': self.token,
                'answers': answers,
            }
            self._file.write_text(json.dumps(payload, ensure_ascii=False), 'utf-8')
            self._last_write = now
        except Exception:
            pass

    def set_processing_question(self, question: str):
        with self._lock:
            self._current = {
                'question': question.strip(), 'answer': '',
                'timestamp': datetime.now().isoformat(),
                'is_complete': False, 'metrics': None,
            }
            self._write(force=True)

    def append_answer_chunk(self, chunk: str):
        with self._lock:
            self._current['answer'] += chunk
            self._write(force=False)

    def update_current_question(self, new_q: str):
        with self._lock:
            if self._current.get('question') and not self._current.get('is_complete'):
                self._current['question'] = new_q.strip()
                self._write(force=True)

    def set_complete_answer(self, question: str, answer: str, metrics=None):
        with self._lock:
            entry = {
                'question': question.strip(), 'answer': answer.strip(),
                'timestamp': datetime.now().isoformat(),
                'is_complete': True, 'metrics': metrics,
            }
            q_lower = question.strip().lower()
            if q_lower in self._index:
                self._answers[self._index[q_lower]] = entry
            else:
                self._index[q_lower] = len(self._answers)
                self._answers.append(entry)
            self._current = entry
            self._write(force=True)
            try:
                hist = self._dir / 'history.jsonl'
                with open(hist, 'a', encoding='utf-8') as f:
                    json.dump(entry, f, ensure_ascii=False)
                    f.write('\n')
            except Exception:
                pass

    def get_all_answers(self) -> list:
        try:
            if self._file.exists():
                data = json.loads(self._file.read_text('utf-8'))
                answers = (
                    data.get('answers', []) if isinstance(data, dict)
                    else (data if isinstance(data, list) else [])
                )
                return list(reversed([a for a in answers if a.get('question')]))
        except Exception:
            pass
        with self._lock:
            result = list(self._answers)
            if self._current.get('question') and not self._current.get('is_complete'):
                result.append(dict(self._current))
        return list(reversed(result))


# ── Init: run JSON migration once at import time ──────────────────────────────
try:
    _migrate_json_to_db()
except Exception:
    pass
