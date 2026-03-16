"""
Q&A Database for Drishi Pro

Persistent SQLite store of pre-curated question-answer pairs.
Before hitting the LLM API, the pipeline checks this DB for a match.

Match strategy (no extra deps):
  1. Exact normalized match against question OR any alias  → 1.0
  2. Best Jaccard score across question + all aliases      → confident if >= MATCH_THRESHOLD
  3. No match → fall through to LLM

Paraphrase handling:
  - Question-framing stop words removed ("explain", "describe", "tell me", etc.)
    so "What is X?" and "Can you explain X?" and "Tell me about X" all reduce
    to the same meaningful token set.
  - aliases column stores pipe-separated alternate phrasings for topic-word
    variations (e.g. "GIL" vs "global interpreter lock").
  - Keywords column gives extra boost for synonym terms.

Question types: 'theory' | 'coding' | 'both'
"""

import queue as _queue
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, List, Dict

# ── Config ────────────────────────────────────────────────────────────────────
try:
    import config
    _DB_DIR = Path(config.ANSWERS_DIR).expanduser()
except Exception:
    _DB_DIR = Path.home() / ".drishi"

DB_PATH = _DB_DIR / "qa_pairs.db"
MATCH_THRESHOLD = 0.72
_lock = threading.Lock()

# Batch hit-count update queue — avoids spawning a thread per DB hit
_hit_queue: _queue.Queue = _queue.Queue()

def _hit_update_worker():
    """Single background thread that batches hit_count increments."""
    while True:
        try:
            row_id = _hit_queue.get(timeout=5.0)
        except _queue.Empty:
            continue
        ids = [row_id]
        # Drain all pending ids in one batch
        while True:
            try:
                ids.append(_hit_queue.get_nowait())
            except _queue.Empty:
                break
        try:
            conn = _get_conn()
            placeholders = ','.join('?' * len(ids))
            conn.execute(
                f"UPDATE qa_pairs SET hit_count = hit_count + 1 WHERE id IN ({placeholders})",
                ids
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

_hit_worker = threading.Thread(target=_hit_update_worker, daemon=True)
_hit_worker.start()

# ── In-memory scoring cache ────────────────────────────────────────────────────
# Pre-computes token sets so find_answer() does pure set math, no per-call tokenization.
# Each entry: (id, norm_q, q_toks, kw_toks, alias_entries)
#   q_toks     = frozenset of tokens from normalized question
#   kw_toks    = frozenset of tokens from keywords column
#   alias_entries = list of (norm_alias_str, frozenset_alias_toks)
# Invalidated on any write. Only winner's answer text fetched from DB.
_score_cache: Optional[List] = None


def _get_score_cache() -> List:
    """Build/return the pre-tokenized scoring cache."""
    global _score_cache
    if _score_cache is not None:
        return _score_cache
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, normalized_q, keywords, aliases FROM qa_pairs ORDER BY hit_count DESC"
        ).fetchall()
    finally:
        conn.close()

    cache = []
    for r in rows:
        norm_q = r["normalized_q"] or ""
        q_toks = frozenset(_tokens(norm_q))

        # Pre-compute keyword token set
        kw_toks: frozenset = frozenset()
        if r["keywords"]:
            kw_flat = r["keywords"].replace('_', ' ')
            kw_set = set()
            for phrase in kw_flat.split(','):
                for tok in phrase.strip().lower().split():
                    if len(tok) > 2 and tok not in _STOP_WORDS:
                        kw_set.add(_stem(tok))
            kw_toks = frozenset(kw_set)

        # Pre-compute alias token sets
        alias_entries = []
        if r["aliases"]:
            for alias in r["aliases"].split('|'):
                alias = alias.strip()
                if alias:
                    norm_a = normalize_question(alias)
                    alias_entries.append((norm_a, frozenset(_tokens(norm_a))))

        cache.append((r["id"], norm_q, q_toks, kw_toks, alias_entries))

    _score_cache = cache
    return _score_cache


def _invalidate_cache():
    global _score_cache
    _score_cache = None


# ── Schema ────────────────────────────────────────────────────────────────────

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS qa_pairs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    question         TEXT    NOT NULL,
    normalized_q     TEXT    NOT NULL,
    answer_theory    TEXT    DEFAULT '',
    answer_coding    TEXT    DEFAULT '',
    type             TEXT    NOT NULL DEFAULT 'theory',
    keywords         TEXT    DEFAULT '',
    aliases          TEXT    DEFAULT '',
    tags             TEXT    DEFAULT '',
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    hit_count        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_type ON qa_pairs(type);

CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    role             TEXT    NOT NULL,
    experience_years INTEGER NOT NULL,
    resume_text      TEXT    DEFAULT '',
    job_description  TEXT    DEFAULT '',
    self_introduction TEXT   DEFAULT '',
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    role             TEXT    NOT NULL,
    question         TEXT    NOT NULL,
    prepared_answer  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS access_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key          TEXT    UNIQUE NOT NULL,
    label        TEXT    DEFAULT '',
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   REAL    NOT NULL,
    last_used_at REAL    DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_access_keys_key ON access_keys(key);
"""

_MIGRATE_ALIASES = """
ALTER TABLE qa_pairs ADD COLUMN aliases TEXT DEFAULT '';
"""


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables; add aliases/tags columns if missing (migration)."""
    with _lock:
        conn = _get_conn()
        conn.executescript(_CREATE_SQL)
        # Safe migration: add aliases column if it doesn't exist
        cols = {row[1] for row in conn.execute("PRAGMA table_info(qa_pairs)")}
        if 'aliases' not in cols:
            conn.execute("ALTER TABLE qa_pairs ADD COLUMN aliases TEXT DEFAULT ''")
        if 'tags' not in cols:
            conn.execute("ALTER TABLE qa_pairs ADD COLUMN tags TEXT DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags ON qa_pairs(tags)")
        
        # Safe migration: add resume_file and resume_summary to users table
        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if 'resume_file' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN resume_file TEXT DEFAULT ''")
        if 'resume_summary' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN resume_summary TEXT DEFAULT ''")

        # Safe migration: create access_keys table if it doesn't exist
        conn.execute("""
            CREATE TABLE IF NOT EXISTS access_keys (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key          TEXT    UNIQUE NOT NULL,
                label        TEXT    DEFAULT '',
                is_active    INTEGER NOT NULL DEFAULT 1,
                created_at   REAL    NOT NULL,
                last_used_at REAL    DEFAULT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_access_keys_key ON access_keys(key)")

        conn.commit()

        # Seed Unix/Bash Q&A pairs if not already present
        count = conn.execute("SELECT COUNT(*) FROM qa_pairs WHERE tags LIKE '%unix-seed%'").fetchone()[0]
        if count == 0:
            conn.close()
            _seed_unix_qa()
        else:
            conn.close()


def _seed_unix_qa():
    """Pre-populate database with common Unix/Bash interview Q&A pairs."""
    _UNIX_QA = [
        (
            "What does $# mean in bash?",
            "- `$#` holds the number of positional arguments passed to the script\n- `echo $#` inside a script prints how many args the caller provided\n- I check it at the top of scripts to validate required args before running",
            "bash", "bash special variable $# arguments", "$# dollar hash positional argument count", "unix-seed linux-seed bash-seed"
        ),
        (
            "What is $? in bash?",
            "- `$?` holds the exit status of the last command, 0 means success\n- I check it right after critical commands to catch silent failures\n- `if [ $? -ne 0 ]; then echo 'failed'; exit 1; fi` is my standard error check",
            "bash", "bash special variable $? exit status return code", "$? dollar question mark exit code last command", "unix-seed linux-seed bash-seed"
        ),
        (
            "Difference between $@ and $* in bash?",
            "- `$@` treats each argument as a separate quoted string, safe for filenames with spaces\n- `$*` joins all args into one string which breaks when values contain spaces\n- I always use `\"$@\"` when forwarding args to another command or function",
            "bash", "bash special variable $@ $* positional parameters", "$@ $* dollar at dollar star all arguments", "unix-seed linux-seed bash-seed"
        ),
        (
            "What is $0 in bash?",
            "- `$0` holds the name of the currently running script or shell\n- It's useful for printing usage messages like `echo \"Usage: $0 [options]\"`\n- I use it in error messages so users see which script failed",
            "bash", "bash special variable $0 script name", "$0 dollar zero script name", "unix-seed linux-seed bash-seed"
        ),
        (
            "What is $$ in bash?",
            "- `$$` holds the PID of the current shell process\n- It's used to create unique temp file names like `/tmp/tmpfile.$$`\n- I use it to avoid race conditions when multiple instances run simultaneously",
            "bash", "bash special variable $$ pid current shell", "$$ double dollar shell pid", "unix-seed linux-seed bash-seed"
        ),
        (
            "What is $! in bash?",
            "- `$!` holds the PID of the last backgrounded process\n- I use it right after `command &` to capture the PID for later `wait $!`\n- It's useful when you need to wait for a specific background job to finish",
            "bash", "bash special variable $! background pid", "$! dollar exclamation background process", "unix-seed linux-seed bash-seed"
        ),
        (
            "What is a shebang in a shell script?",
            "- The shebang `#!/bin/bash` tells the OS which interpreter to use for the script\n- Without it the OS uses the default shell which may not support bash syntax\n- I use `#!/usr/bin/env bash` so bash is found from PATH on any system",
            "theory", "shebang hashbang interpreter directive bash", "shebang she-bang hash bang shell script", "unix-seed linux-seed bash-seed"
        ),
        (
            "How to replace text in a file using sed?",
            "- `sed -i 's/old/new/g' file.txt` replaces all occurrences in-place\n- The `-i` flag edits the file directly; without it sed prints to stdout only\n- For line-range replace: `sed -i '10,20s/old/new/g' file.txt`",
            "bash", "sed replace text in-place substitute", "sed -i substitute replace string file", "unix-seed linux-seed bash-seed"
        ),
        (
            "How to replace text from specific line range in sed?",
            "- `sed -n '10,20p' file.txt` prints lines 10 to 20\n- `sed -i '11,19s/word/newword/g' file.txt` replaces only in lines 11–19\n- Line addresses like `11,19` limit the sed operation to that range only",
            "bash", "sed line range address replace specific lines", "sed nth line replace from line to line", "unix-seed linux-seed bash-seed"
        ),
        (
            "How to search for multiple matching strings in a file?",
            "- `grep -E 'pattern1|pattern2' file` searches for either pattern using extended regex\n- `grep -F -e 'str1' -e 'str2' file` matches fixed strings without regex overhead\n- I use `grep -rn 'pattern' .` to search recursively and show line numbers",
            "theory", "grep search multiple patterns match string file", "grep -E multiple patterns search find string", "unix-seed linux-seed"
        ),
        (
            "How to show lines around a matching pattern in grep?",
            "- `grep -A 2 'pattern' file` shows 2 lines after the match\n- `grep -B 2 'pattern' file` shows 2 lines before the match\n- `grep -C 2 'pattern' file` shows 2 lines before and after — I use this most often",
            "theory", "grep context lines above below around match -A -B -C", "grep show context above below matching pattern", "unix-seed linux-seed"
        ),
        (
            "How to use awk to print a specific column?",
            "- `awk '{print $2}' file` prints the second whitespace-delimited field\n- `awk -F: '{print $1}' /etc/passwd` splits on colon and prints usernames\n- I use awk for quick on-the-fly reports from log files and CSV data",
            "theory", "awk print column field separator NF NR", "awk field column print extract", "unix-seed linux-seed bash-seed"
        ),
        (
            "What is set -e and set -o pipefail in bash?",
            "- `set -e` exits the script immediately if any command returns non-zero\n- `set -o pipefail` makes the whole pipe fail if any stage in it fails\n- I put both at the top of all prod scripts so silent failures don't continue",
            "theory", "set -e errexit set -o pipefail bash error handling", "set -e set -o pipefail bash exit on error", "unix-seed linux-seed bash-seed"
        ),
        (
            "What is the difference between hard link and soft link?",
            "- A hard link points directly to the inode so deleting the original doesn't break it\n- A soft link (symlink) points to the filename and breaks if the original is deleted\n- I use symlinks for versioned binaries and hard links for atomic backup scripts",
            "theory", "hard link soft link symlink inode ln", "hardlink softlink symbolic link difference", "unix-seed linux-seed"
        ),
        (
            "What is stdin stdout stderr in Linux?",
            "- stdin (fd 0) is the input stream, stdout (fd 1) is normal output, stderr (fd 2) is error output\n- `command > out.txt 2>&1` redirects both stdout and stderr to a file\n- I redirect stderr separately with `2>err.log` to capture errors without polluting output",
            "theory", "stdin stdout stderr file descriptor redirect fd", "stdin stdout stderr standard input output error", "unix-seed linux-seed"
        ),
        (
            "What is a named pipe or FIFO in Linux?",
            "- A named pipe (FIFO) is a special file that connects two processes in a pipeline\n- Unlike a regular pipe, it exists on the filesystem so unrelated processes can use it\n- I create one with `mkfifo /tmp/mypipe` when streaming logs between two services",
            "theory", "named pipe fifo mkfifo ipc inter-process communication", "named pipe FIFO mkfifo Linux Unix", "unix-seed linux-seed"
        ),
        (
            "What is process substitution in bash?",
            "- Process substitution `<(command)` lets you use a command's output as a file\n- `diff <(sort file1) <(sort file2)` compares sorted versions without temp files\n- I use it to avoid creating intermediary temp files in complex shell pipelines",
            "theory", "process substitution bash <() >() temp file pipeline", "process substitution bash command output as file", "unix-seed linux-seed bash-seed"
        ),
        (
            "What is the importance of $# in shell scripting?",
            "- `$#` holds the count of arguments so the script can validate required input\n- Use `if [ $# -lt 2 ]; then echo 'Usage: script.sh arg1 arg2'; exit 1; fi`\n- I always validate $# at script start to catch missing arguments early",
            "theory", "bash $# importance argument count shell scripting", "importance of dollar hash shell scripting $#", "unix-seed linux-seed bash-seed"
        ),
        (
            "How to redirect stderr to stdout in bash?",
            "- `command 2>&1` redirects stderr (fd 2) to wherever stdout (fd 1) is going\n- `command > file.txt 2>&1` sends both stdout and stderr to the same file\n- Order matters: `2>&1 > file.txt` is wrong; always redirect stdout first",
            "theory", "redirect stderr stdout bash 2>&1 file descriptor", "redirect stderr stdout 2>&1 combine output", "unix-seed linux-seed bash-seed"
        ),
        (
            "What is the difference between single quotes and double quotes in bash?",
            "- Single quotes preserve everything literally — no variable expansion happens\n- Double quotes allow `$var`, `$(cmd)`, and `\\` escapes to be interpreted\n- I use single quotes for regex patterns and double quotes for strings with variables",
            "theory", "bash single quotes double quotes quoting variable expansion", "single quotes vs double quotes bash shell", "unix-seed linux-seed bash-seed"
        ),
        (
            "How to check if a file exists in bash?",
            "- `[ -f file.txt ]` returns true if the path exists and is a regular file\n- `[ -d /path ]` checks for a directory; `[ -e /path ]` checks for any file type\n- I use `if [ ! -f config.cfg ]; then echo 'config missing'; exit 1; fi` in deploy scripts",
            "bash", "bash check file exists -f -d -e conditional test", "check if file exists bash shell script", "unix-seed linux-seed bash-seed"
        ),
    ]

    for question, answer, qa_type, keywords, aliases, tags in _UNIX_QA:
        norm = normalize_question(question)
        ts = _nowts()
        with _lock:
            conn = _get_conn()
            try:
                existing = conn.execute(
                    "SELECT id FROM qa_pairs WHERE normalized_q=?", (norm,)
                ).fetchone()
                if not existing:
                    conn.execute(
                        """INSERT INTO qa_pairs
                           (question, normalized_q, answer_theory, answer_coding,
                            type, keywords, aliases, tags, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (question, norm, answer if qa_type == "theory" else "",
                         answer if qa_type == "bash" else "",
                         "theory" if qa_type == "theory" else "coding",
                         keywords, aliases, tags, ts, ts)
                    )
                    conn.commit()
            finally:
                conn.close()


# ── User Profile CRUD ─────────────────────────────────────────────────────────

def add_user(name: str, role: str, experience_years: int, resume_text: str = "", 
             job_description: str = "", self_introduction: str = "") -> int:
    ts = _nowts()
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                """INSERT INTO users (name, role, experience_years, resume_text, 
                   job_description, self_introduction, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, role, experience_years, resume_text, job_description, self_introduction, ts)
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

def get_user(user_id: int) -> Optional[Dict]:
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

def get_all_users() -> List[Dict]:
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

def update_user(user_id: int, name: str = None, role: str = None, experience_years: int = None,
                resume_text: str = None, job_description: str = None, self_introduction: str = None,
                resume_file: str = None, resume_summary: str = None) -> bool:
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                return False

            new_name    = name              if name              is not None else row["name"]
            new_role    = role              if role              is not None else row["role"]
            new_exp     = experience_years  if experience_years  is not None else row["experience_years"]
            new_resume  = resume_text       if resume_text       is not None else row["resume_text"]
            new_jd      = job_description   if job_description   is not None else row["job_description"]
            new_intro   = self_introduction if self_introduction is not None else row["self_introduction"]
            # New columns — fall back to existing value (may be None for old rows)
            _row_dict   = dict(row)
            new_rf      = resume_file    if resume_file    is not None else _row_dict.get("resume_file",    "")
            new_rs      = resume_summary if resume_summary is not None else _row_dict.get("resume_summary", "")

            conn.execute(
                """UPDATE users SET name=?, role=?, experience_years=?, resume_text=?,
                   job_description=?, self_introduction=?, resume_file=?, resume_summary=?
                   WHERE id=?""",
                (new_name, new_role, new_exp, new_resume, new_jd, new_intro, new_rf, new_rs, user_id)
            )
            conn.commit()
            return True
        finally:
            conn.close()

def delete_user(user_id: int) -> bool:
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ── Access Key CRUD ───────────────────────────────────────────────────────────

import secrets as _secrets


def create_access_key(user_id: int, label: str = '') -> Optional[str]:
    """Generate a new access key for a user. Returns the key string or None."""
    key = 'dk-' + _secrets.token_hex(8)  # e.g. dk-a3f9c271b8e2c415
    with _lock:
        conn = _get_conn()
        try:
            # Verify user exists
            if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                return None
            conn.execute(
                "INSERT INTO access_keys (user_id, key, label, is_active, created_at) "
                "VALUES (?, ?, ?, 1, ?)",
                (user_id, key, label or '', time.time())
            )
            conn.commit()
            return key
        finally:
            conn.close()


def get_user_by_key(key: str) -> Optional[Dict]:
    """Look up user by access key. Returns user dict if key is active, else None."""
    if not key or not key.startswith('dk-'):
        return None
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT ak.id as key_id, ak.key, ak.user_id, ak.label, ak.is_active, "
            "u.id, u.name, u.role, u.experience_years, u.resume_text, u.job_description, "
            "u.self_introduction, u.resume_file, u.resume_summary "
            "FROM access_keys ak JOIN users u ON ak.user_id = u.id "
            "WHERE ak.key=? AND ak.is_active=1",
            (key,)
        ).fetchone()
        if row:
            # Update last_used_at in background
            key_id = row['key_id']
            threading.Thread(target=update_key_last_used, args=(key_id,), daemon=True).start()
            return dict(row)
    finally:
        conn.close()
    return None


def update_key_last_used(key_id: int):
    """Update last_used_at timestamp for an access key."""
    try:
        conn = _get_conn()
        conn.execute("UPDATE access_keys SET last_used_at=? WHERE id=?", (time.time(), key_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_keys_for_user(user_id: int) -> List[Dict]:
    """Return all access keys for a user."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, key, label, is_active, created_at, last_used_at "
            "FROM access_keys WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_access_keys() -> List[Dict]:
    """Return all access keys with user names (for admin view)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT ak.id, ak.key, ak.label, ak.is_active, ak.created_at, ak.last_used_at, "
            "u.id as user_id, u.name as user_name, u.role as user_role "
            "FROM access_keys ak JOIN users u ON ak.user_id = u.id "
            "ORDER BY ak.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_access_key(key_id: int) -> bool:
    """Permanently delete an access key."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute("DELETE FROM access_keys WHERE id=?", (key_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def revoke_access_key(key_id: int) -> bool:
    """Disable an access key without deleting it."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute("UPDATE access_keys SET is_active=0 WHERE id=?", (key_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ── Prepared Questions CRUD ───────────────────────────────────────────────────

def add_prepared_question(role: str, question: str, prepared_answer: str) -> int:
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO questions (role, question, prepared_answer) VALUES (?, ?, ?)",
                (role, question, prepared_answer)
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

def get_all_questions() -> List[Dict]:
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute("SELECT * FROM questions ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

def delete_prepared_question(q_id: int) -> bool:
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute("DELETE FROM questions WHERE id=?", (q_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

def get_stats() -> Dict:
    with _lock:
        conn = _get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM qa_pairs").fetchone()[0]
            theory = conn.execute("SELECT COUNT(*) FROM qa_pairs WHERE type='theory'").fetchone()[0]
            coding = conn.execute("SELECT COUNT(*) FROM qa_pairs WHERE type='coding'").fetchone()[0]
            both = conn.execute("SELECT COUNT(*) FROM qa_pairs WHERE type='both'").fetchone()[0]
            total_hits = conn.execute("SELECT SUM(hit_count) FROM qa_pairs").fetchone()[0] or 0
            
            return {
                "total": total,
                "theory": theory,
                "coding": coding,
                "both": both,
                "total_hits": total_hits
            }
        finally:
            conn.close()


def find_prepared_answer(question: str, role: str = None) -> Optional[Tuple[str, float, int]]:
    """
    Search specifically in the new 'questions' table with role filtering.
    """
    norm_input = normalize_question(question)
    input_toks = frozenset(_tokens(norm_input))
    if not input_toks:
        return None

    with _lock:
        conn = _get_conn()
        try:
            if role:
                rows = conn.execute("SELECT * FROM questions WHERE role=?", (role,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM questions").fetchall()
        finally:
            conn.close()

    best_score = 0.0
    best_answer = None
    best_id = None

    for r in rows:
        norm_q = normalize_question(r["question"])
        if norm_input == norm_q:
            return r["prepared_answer"], 1.0, r["id"]
        
        q_toks = frozenset(_tokens(norm_q))
        if not q_toks:
            continue
            
        score = len(input_toks & q_toks) / len(input_toks | q_toks)
        
        if input_toks <= q_toks:
            score = max(score, MATCH_THRESHOLD + 0.05)
            
        if score > best_score:
            best_score = score
            best_answer = r["prepared_answer"]
            best_id = r["id"]

    if best_score >= MATCH_THRESHOLD:
        return best_answer, best_score, best_id
    
    return None


# ── Stop words ────────────────────────────────────────────────────────────────
# Includes both grammatical stop words AND question-framing words so that
# "What is X?", "Can you explain X?", "Tell me about X", "Describe X",
# "How does X work?" all reduce to the same meaningful token set.

_STOP_WORDS = {
    # Articles / pronouns / prepositions
    'a','an','the','is','are','was','were','be','been','being',
    'have','has','had','do','does','did','will','would','shall','should',
    'may','might','must','can','could','to','of','in','for','on','with',
    'at','by','from','as','into','through','during','before','after',
    'above','below','up','down','out','off','over','under','again',
    'further','then','once','and','but','or','nor','so','yet','both',
    'either','neither','not','only','own','same','than','too','very',
    'just','because','if','while','although','though','since','unless',
    'how','what','when','where','which','who','whom','this','that',
    'these','those','i','you','he','she','it','we','they','me','him',
    'her','us','them','my','your','his','its','our','their',
    'am','im','id','its','vs','via','per',
    # Question-framing verbs and phrases (key addition for paraphrase handling)
    'tell','explain','describe','define','elaborate','discuss','show',
    'write','give','provide','name','mention','state','share',
    'please','briefly','quickly','shortly','simply','basically',
    'example','examples','sample','demo','illustration',
    'mean','means','meant','refer','refers','called','known',
    'work','works','work','use','used','using','uses',
    'about','regarding','concerning','related','around',
    'make','makes','help','helps','need','needs','want','wants',
    'understand','understanding','know','knowing','think','thinking',
    'versus',
    'advantage','advantages','disadvantage','disadvantages',
    'benefit','benefits','pros','cons','feature','features',
    'concept','term','idea','notion','definition','meaning',
    'brief','overview','summary','introduction','basis','basic',
    'common','commonly','typically','usually','generally','often',
    'real','actually','exactly','specifically','mainly','mostly',
    'between','among','across','inside','outside',
    'object','module','library','package',
    'application','app','project','system',
    'interview','question','answer','ask','asked','say','said',
}


# ── Normalization ─────────────────────────────────────────────────────────────

def normalize_question(q: str) -> str:
    """Lowercase, strip punctuation, expand underscores, collapse spaces."""
    if not q:
        return ""
    q = q.lower().strip()
    q = re.sub(r"[?.!,;:'\"()\[\]{}\-/\\|]+", " ", q)
    q = q.replace("_", " ")          # select_related → select related
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _stem(word: str) -> str:
    """Light suffix stripping for singular/plural and common forms."""
    for suffix in ('ations', 'ation', 'ings', 'ing', 'tion', 'tions',
                   'ers', 'ies', 'es', 'ed', 's'):
        if word.endswith(suffix) and len(word) - len(suffix) > 2:
            return word[:-len(suffix)]
    return word


def _tokens(text: str) -> set:
    """
    Return meaningful stemmed tokens from a normalized string.
    Filters out stop words and very short words.
    """
    words = normalize_question(text).split()
    result = set()
    # Tech 2-char tokens to preserve (s3, k8, ec2 etc. come out as "s3","k8","ec2" after normalize)
    _KEEP_SHORT = {'s3', 'k8', 'ec', 'vm', 'ml', 'ai', 'ip', 'os', 'db', 'ui', 'ux', 'ci', 'cd'}
    for w in words:
        if w in _STOP_WORDS:
            continue
        if len(w) > 2 or w in _KEEP_SHORT:
            result.add(_stem(w))
    return result


# ── Scoring ───────────────────────────────────────────────────────────────────

def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union)


def _score_against(norm_input: str, norm_stored: str,
                   stored_keywords: str, stored_aliases: str) -> float:
    """
    Compute best similarity score between incoming question and one stored row.
    Checks:
      1. Against the stored question text
      2. Against each stored alias
    Returns the best score (0.0 – 1.0).
    """
    input_toks = _tokens(norm_input)
    if not input_toks:
        return 0.0

    # Build keyword token set once (shared across all comparisons)
    kw_toks: set = set()
    if stored_keywords:
        kw_flat = stored_keywords.replace('_', ' ')
        for phrase in kw_flat.split(','):
            for tok in phrase.strip().lower().split():
                if len(tok) > 2 and tok not in _STOP_WORDS:
                    kw_toks.add(_stem(tok))

    def _score_one(norm_candidate: str) -> float:
        if norm_input == norm_candidate:
            return 1.0
        stored_toks = _tokens(norm_candidate)
        if not stored_toks:
            return 0.0

        score = _jaccard(input_toks, stored_toks)

        # Boost A: all input tokens found in stored text → confident short query
        if input_toks <= stored_toks:
            score = max(score, MATCH_THRESHOLD + 0.05)

        # Boost B: keyword hits — only when no query token is completely foreign
        # (prevents "merge sort" from boosting into "bubble sort" via shared "sort" keyword)
        foreign_toks = input_toks - (stored_toks | kw_toks)
        kw_hits = len(input_toks & kw_toks)
        if kw_hits and not foreign_toks:
            score = min(1.0, score + 0.10 * kw_hits)

        # Boost C: all input tokens covered by stored + keywords combined
        # Requires at least one input token to appear directly in the stored question
        # (prevents unrelated entries from matching via shared keywords only)
        if kw_toks and input_toks <= (stored_toks | kw_toks) and (input_toks & stored_toks):
            score = max(score, MATCH_THRESHOLD + 0.05)

        return score

    best = _score_one(norm_stored)

    # Check each alias
    if stored_aliases:
        for alias in stored_aliases.split('|'):
            alias = alias.strip()
            if not alias:
                continue
            norm_alias = normalize_question(alias)
            s = _score_one(norm_alias)
            if s > best:
                best = s

    return best


# ── Lookup ────────────────────────────────────────────────────────────────────

def find_answer(question: str, want_code: bool = False) -> Optional[Tuple[str, float, int]]:
    """
    Search DB for a matching Q&A pair.
    Returns (answer_text, score, qa_id) or None.
    want_code=True  → prefer answer_coding
    want_code=False → prefer answer_theory

    Uses pre-tokenized in-memory cache: pure set math, no per-call tokenization on stored rows.
    Only the winning row's answer text is fetched from DB.
    """
    norm_input = normalize_question(question)
    if not norm_input:
        return None

    input_toks = frozenset(_tokens(norm_input))
    if not input_toks:
        return None

    best_score = 0.0
    best_id = None

    with _lock:
        cache = _get_score_cache()

    for (row_id, norm_q, q_toks, kw_toks, alias_entries) in cache:
        # Fast inline scoring (no function call overhead, pre-computed sets)
        if norm_input == norm_q:
            score = 1.0
        else:
            if not q_toks:
                score = 0.0
            else:
                inter = input_toks & q_toks
                score = len(inter) / len(input_toks | q_toks)

                # Boost A: all input tokens found in stored tokens
                if input_toks <= q_toks:
                    score = max(score, MATCH_THRESHOLD + 0.05)

                # Boost B: keyword hits with no foreign tokens
                if kw_toks:
                    foreign = input_toks - (q_toks | kw_toks)
                    kw_hits = len(input_toks & kw_toks)
                    if kw_hits and not foreign:
                        score = min(1.0, score + 0.10 * kw_hits)

                    # Boost C: all input covered by stored + keywords, with overlap
                    if input_toks <= (q_toks | kw_toks) and (input_toks & q_toks):
                        score = max(score, MATCH_THRESHOLD + 0.05)

            # Check aliases (only if score not already perfect)
            if score < 1.0:
                for (norm_a, a_toks) in alias_entries:
                    if norm_input == norm_a:
                        score = 1.0
                        break
                    if not a_toks:
                        continue
                    a_inter = input_toks & a_toks
                    a_score = len(a_inter) / len(input_toks | a_toks)
                    if input_toks <= a_toks:
                        a_score = max(a_score, MATCH_THRESHOLD + 0.05)
                    if kw_toks:
                        foreign = input_toks - (a_toks | kw_toks)
                        kw_hits = len(input_toks & kw_toks)
                        if kw_hits and not foreign:
                            a_score = min(1.0, a_score + 0.10 * kw_hits)
                        if input_toks <= (a_toks | kw_toks) and (input_toks & a_toks):
                            a_score = max(a_score, MATCH_THRESHOLD + 0.05)
                    if a_score > score:
                        score = a_score

        if score > best_score:
            best_score = score
            best_id = row_id
            if best_score >= 1.0:
                break  # exact match, stop early

    if best_score < MATCH_THRESHOLD or best_id is None:
        return None

    # Short-query guard: queries with very few meaningful tokens get inflated scores via
    # keyword/alias boosts despite semantic mismatch.
    # e.g. "What is system load?" → only "load" token → falsely hits "Load Balancer" entries.
    # e.g. "What is the basic Linux commands?" → only {command, linux} → falsely hits port-check entries.
    # For short queries, only accept near-exact matches (prevent boost inflation).
    if len(input_toks) <= 1 and best_score < 0.99:
        return None  # 1-token query: only exact normalized match
    if len(input_toks) == 2 and best_score < 0.92:
        return None  # 2-token query: require very high confidence

    # Fetch only the winner's answer text (one row by ID)
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT answer_theory, answer_coding FROM qa_pairs WHERE id=?", (best_id,)
            ).fetchone()
        finally:
            conn.close()

    if not row:
        return None

    theory_ans = (row["answer_theory"] or "").strip()
    coding_ans  = (row["answer_coding"]  or "").strip()
    answer = (coding_ans or theory_ans) if want_code else (theory_ans or coding_ans)

    if not answer:
        return None

    _hit_queue.put(best_id)
    return answer, best_score, best_id


def _increment_hit(qa_id: int):
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("UPDATE qa_pairs SET hit_count = hit_count + 1 WHERE id = ?", (qa_id,))
            conn.commit()
        finally:
            conn.close()


# ── CRUD ──────────────────────────────────────────────────────────────────────

def _nowts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def add_qa(question: str, answer_theory: str = "", answer_coding: str = "",
           qa_type: str = "theory", keywords: str = "", aliases: str = "",
           tags: str = "") -> int:
    """Insert a new Q&A pair. Returns new row id."""
    norm = normalize_question(question)
    ts = _nowts()
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                """INSERT INTO qa_pairs
                   (question, normalized_q, answer_theory, answer_coding,
                    type, keywords, aliases, tags, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (question.strip(), norm,
                 answer_theory.strip(), answer_coding.strip(),
                 qa_type, keywords.strip(), aliases.strip(), tags.strip(), ts, ts)
            )
            conn.commit()
            _invalidate_cache()
            return cur.lastrowid
        finally:
            conn.close()


def update_qa(qa_id: int, question: str = None, answer_theory: str = None,
              answer_coding: str = None, qa_type: str = None,
              keywords: str = None, aliases: str = None, tags: str = None) -> bool:
    """Update an existing Q&A pair."""
    ts = _nowts()
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute("SELECT * FROM qa_pairs WHERE id=?", (qa_id,)).fetchone()
            if not row:
                return False
            new_q       = question.strip()       if question      is not None else row["question"]
            new_norm    = normalize_question(new_q)
            new_theory  = answer_theory.strip()  if answer_theory is not None else row["answer_theory"]
            new_coding  = answer_coding.strip()  if answer_coding is not None else row["answer_coding"]
            new_type    = qa_type                if qa_type       is not None else row["type"]
            new_kw      = keywords.strip()       if keywords      is not None else row["keywords"]
            new_aliases = aliases.strip()        if aliases       is not None else (row["aliases"] or "")
            new_tags    = tags.strip()           if tags          is not None else (row["tags"] or "")
            conn.execute(
                """UPDATE qa_pairs SET question=?, normalized_q=?, answer_theory=?,
                   answer_coding=?, type=?, keywords=?, aliases=?, tags=?, updated_at=? WHERE id=?""",
                (new_q, new_norm, new_theory, new_coding,
                 new_type, new_kw, new_aliases, new_tags, ts, qa_id)
            )
            conn.commit()
            _invalidate_cache()
            return True
        finally:
            conn.close()


def delete_qa(qa_id: int) -> bool:
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute("DELETE FROM qa_pairs WHERE id=?", (qa_id,))
            conn.commit()
            _invalidate_cache()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_qa_pairs_for_index() -> List[Dict]:
    """Return all qa_pairs rows formatted for engine.update_indexes().
    Maps answer_theory (or answer_coding fallback) → 'prepared_answer'
    so the TF-IDF semantic tier can match against the full 767-row knowledge base.
    """
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT question, answer_theory, answer_coding FROM qa_pairs "
                "WHERE answer_theory != '' OR answer_coding != '' ORDER BY hit_count DESC"
            ).fetchall()
            result = []
            for r in rows:
                answer = (r["answer_theory"] or r["answer_coding"] or "").strip()
                if answer:
                    result.append({"question": r["question"], "prepared_answer": answer})
            return result
        finally:
            conn.close()


def get_all_qa(search: str = "", tag: str = "") -> List[Dict]:
    with _lock:
        conn = _get_conn()
        try:
            conditions = []
            params: list = []

            if search:
                norm = normalize_question(search)
                conditions.append(
                    "(normalized_q LIKE ? OR keywords LIKE ? OR aliases LIKE ? OR tags LIKE ?)"
                )
                params += [f"%{norm}%", f"%{search.lower()}%",
                           f"%{search.lower()}%", f"%{search.lower()}%"]

            if tag and tag != 'all':
                # Match exact tag word in comma-separated tags column
                conditions.append("(',' || LOWER(tags) || ',') LIKE ?")
                params.append(f"%,{tag.lower()},%")

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            sql = f"SELECT * FROM qa_pairs {where} ORDER BY updated_at DESC"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_qa(qa_id: int) -> Optional[Dict]:
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute("SELECT * FROM qa_pairs WHERE id=?", (qa_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_stats() -> Dict:
    with _lock:
        conn = _get_conn()
        try:
            total  = conn.execute("SELECT COUNT(*) FROM qa_pairs").fetchone()[0]
            theory = conn.execute("SELECT COUNT(*) FROM qa_pairs WHERE type='theory'").fetchone()[0]
            coding = conn.execute("SELECT COUNT(*) FROM qa_pairs WHERE type='coding'").fetchone()[0]
            both   = conn.execute("SELECT COUNT(*) FROM qa_pairs WHERE type='both'").fetchone()[0]
            hits   = conn.execute("SELECT COALESCE(SUM(hit_count),0) FROM qa_pairs").fetchone()[0]
            # Build tags breakdown
            all_tags = conn.execute("SELECT tags FROM qa_pairs WHERE tags != ''").fetchall()
            tags_breakdown: Dict[str, int] = {}
            for row in all_tags:
                for tag in row[0].split(','):
                    tag = tag.strip()
                    if tag:
                        tags_breakdown[tag] = tags_breakdown.get(tag, 0) + 1
            return {"total": total, "theory": theory, "coding": coding,
                    "both": both, "total_hits": hits,
                    "tags_breakdown": tags_breakdown}
        finally:
            conn.close()


# ── Auto-tagging ───────────────────────────────────────────────────────────────

def auto_tag_entry(row: dict) -> str:
    """
    Return a comma-separated tag string based on question/answer content.
    Multiple tags can apply; empty string if none match.
    """
    q = (row.get("question") or "").lower()
    a = (row.get("answer_theory") or "").lower() + " " + (row.get("answer_coding") or "").lower()
    combined = q + " " + a

    tags = []

    _PYTHON_TERMS = {
        'python', 'django', 'flask', 'decorator', 'generator', 'list', 'tuple',
        'dict', 'args', 'kwargs', 'orm', 'celery', 'pip', 'virtualenv', 'pep8',
        'lambda', 'comprehension', 'iterator', 'asyncio', 'threading', 'multiprocessing',
        'pickle', 'json', 'pandas', 'numpy', 'pytest', 'unittest', 'class', 'inheritance',
        'polymorphism', 'encapsulation', 'abstraction', 'dunder', '__init__', 'self',
    }
    if any(term in combined for term in _PYTHON_TERMS):
        tags.append('python')

    _DJANGO_TERMS = {
        'django', 'orm', 'migration', 'model', 'view', 'serializer', 'drf',
        'jwt', 'rest framework', 'viewset', 'router', 'queryset', 'makemigrations',
        'admin', 'middleware', 'signal', 'template', 'url pattern', 'wsgi', 'asgi',
    }
    if any(term in combined for term in _DJANGO_TERMS):
        tags.append('django')

    _DEVOPS_TERMS = {
        'docker', 'kubernetes', 'k8s', 'ci/cd', 'jenkins', 'pipeline', 'container',
        'helm', 'git', 'github', 'gitlab', 'dockerfile', 'image', 'registry',
        'deployment', 'pod', 'service', 'ingress', 'namespace', 'kubectl',
        'configmap', 'secret', 'cronjob', 'daemonset', 'statefulset', 'replicaset',
        'argocd', 'ansible', 'terraform', 'ci cd',
    }
    if any(term in combined for term in _DEVOPS_TERMS):
        tags.append('devops')

    _SRE_TERMS = {
        'sre', 'slo', 'sli', 'sla', 'prometheus', 'grafana', 'monitoring',
        'alerting', 'error budget', 'incident', 'on-call', 'oncall', 'runbook',
        'postmortem', 'toil', 'reliability', 'availability', 'latency', 'throughput',
        'observability', 'tracing', 'loki', 'pagerduty', 'opsgenie',
    }
    if any(term in combined for term in _SRE_TERMS):
        tags.append('sre')

    _AWS_TERMS = {
        'aws', 'ec2', 's3', 'ecs', 'eks', 'rds', 'cloudwatch', 'route 53',
        'iam', 'vpc', 'lambda', 'cloudfront', 'elb', 'alb', 'nlb', 'auto scaling',
        'sns', 'sqs', 'dynamodb', 'aurora', 'elasticache', 'codepipeline',
        'codebuild', 'codedeploy', 'ecr', 'secrets manager', 'kms',
    }
    if any(term in combined for term in _AWS_TERMS):
        tags.append('aws')

    if 'terraform' in combined:
        tags.append('terraform')

    if 'ansible' in combined:
        tags.append('ansible')

    _LINUX_TERMS = {
        'linux', 'bash', 'shell', 'cron', 'systemd', 'nginx', 'apache',
        'chmod', 'chown', 'grep', 'awk', 'sed', 'ssh', 'tcp', 'udp',
        'firewall', 'iptables', 'kernel', 'process', 'daemon', 'journalctl',
        'rsync', 'tar', 'curl', 'wget',
    }
    if any(term in combined for term in _LINUX_TERMS):
        tags.append('linux')

    _HR_TERMS = {
        'strength', 'weakness', 'salary', 'notice period', 'challenge', 'pressure',
        'leaving', 'change', 'team conflict', 'responsibility', 'why join',
        'five year', '5 year', 'hobbies', 'yourself', 'introduce', 'ctc',
        'compensation', 'relocation', 'availability', 'last working day',
    }
    if any(term in combined for term in _HR_TERMS):
        tags.append('hr')

    _CODING_TRIGGERS = {
        'write', 'find', 'implement', 'sort', 'search', 'reverse',
        'fibonacci', 'palindrome', 'factorial', 'anagram', 'linked list',
        'binary tree', 'stack', 'queue', 'recursion', 'dynamic programming',
    }
    if any(term in q for term in _CODING_TRIGGERS):
        tags.append('coding')

    # Deduplicate while preserving order
    seen = set()
    unique_tags = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)

    return ','.join(unique_tags)


def apply_auto_tags() -> int:
    """
    Update all rows that have empty tags with auto-generated tags.
    Returns the number of rows updated.
    """
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT id, question, answer_theory, answer_coding FROM qa_pairs "
                "WHERE tags IS NULL OR tags = ''"
            ).fetchall()
            updated = 0
            ts = _nowts()
            for row in rows:
                row_dict = dict(row)
                new_tags = auto_tag_entry(row_dict)
                if new_tags:
                    conn.execute(
                        "UPDATE qa_pairs SET tags=?, updated_at=? WHERE id=?",
                        (new_tags, ts, row_dict["id"])
                    )
                    updated += 1
            conn.commit()
            return updated
        finally:
            conn.close()


def save_interview_qa(question: str, answer: str, source: str = 'interview') -> int:
    """
    Save a question+answer captured during a real interview to the DB.

    Auto-detects tags and type (theory vs coding).
    Marks with source tag (e.g. 'interview', 'google-meet-chat', 'teams-chat').
    Returns new row id, or -1 if question already exists.
    """
    if not question or not answer:
        return -1

    # Gate: only save validated interview questions to prevent garbage accumulation.
    # This avoids saving noise/translated side-talk as future "answers".
    try:
        from question_validator import clean_and_validate
        _valid, _cleaned, _reason = clean_and_validate(question)
        if not _valid:
            return -1  # Don't save garbage questions to DB
        # Reject obviously low-quality questions (too short or no tech content)
        if len(question.split()) < 4:
            return -1
    except Exception:
        pass

    # Check if question already exists in DB
    norm = normalize_question(question)
    with _lock:
        conn = _get_conn()
        try:
            existing = conn.execute(
                "SELECT id FROM qa_pairs WHERE normalized_q=?", (norm,)
            ).fetchone()
            if existing:
                return -1  # Already in DB, skip
        finally:
            conn.close()

    # Detect type: theory or coding
    from question_validator import is_code_request
    try:
        wants_code = is_code_request(question)
    except Exception:
        wants_code = bool(re.search(r'```|def\s+\w+\s*\(|class\s+\w+', answer))

    qa_type = 'coding' if wants_code else 'theory'
    answer_theory = '' if wants_code else answer
    answer_coding = answer if wants_code else ''

    # Auto-tag
    row_dict = {'question': question, 'answer_theory': answer_theory, 'answer_coding': answer_coding}
    auto_tags = auto_tag_entry(row_dict)
    # Prepend source tag so it's filterable
    source_clean = source.replace('cc-', '').replace('db-', '').replace('chat-', '')
    all_tags = source_clean
    if auto_tags:
        all_tags = source_clean + ',' + auto_tags if source_clean else auto_tags

    return add_qa(
        question=question,
        answer_theory=answer_theory,
        answer_coding=answer_coding,
        qa_type=qa_type,
        keywords='',
        aliases='',
        tags=all_tags,
    )


def get_interview_captured(limit: int = 50) -> List[Dict]:
    """Return recently captured interview Q&A pairs (tagged with interview/chat)."""
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM qa_pairs
                   WHERE (',' || LOWER(tags) || ',') LIKE '%,interview,%'
                      OR (',' || LOWER(tags) || ',') LIKE '%,chat,%'
                      OR (',' || LOWER(tags) || ',') LIKE '%,google-meet-chat,%'
                      OR (',' || LOWER(tags) || ',') LIKE '%,teams-chat,%'
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# Init on import
init_db()
