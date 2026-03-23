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

# Pre-built translation table: strip punctuation in a single C-level pass
# (faster than re.sub for punctuation removal — no regex engine overhead)
_PUNCT_STRIP = str.maketrans('?.!,;:\'"()[]{}\\-/|', '                  ')
_SPACE_RE    = re.compile(r'\s+')  # compiled once at import time
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import db_backend as _pgb

# ── Config ────────────────────────────────────────────────────────────────────
MATCH_THRESHOLD = 0.60
_lock = threading.Lock()

# Batch hit-count update queue — avoids spawning a thread per DB hit
_hit_queue: _queue.Queue = _queue.Queue()

def _hit_update_worker():
    """Single background thread that batches hit_count increments."""
    while True:
        row_id = _hit_queue.get()  # blocks until work arrives — no CPU spin
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

# Fast O(1) exact-match index: norm_q / norm_alias → row_id
# Guarantees that an exact question match ALWAYS wins over any boost-inflated score,
# regardless of hit_count ordering. Built alongside _score_cache, invalidated together.
_exact_index: Optional[Dict[str, int]] = None

# ── In-memory cache for role-specific prepared questions table ─────────────────
# Maps role (str) → list of (id, norm_q, q_toks, answer)
# Built on first lookup per role. Invalidated on any write to questions table.
_prepared_cache: Dict[str, List] = {}


def _get_score_cache() -> List:
    """Build/return the pre-tokenized scoring cache."""
    global _score_cache
    if _score_cache is not None:
        return _score_cache
    conn = _get_read_conn()
    rows = conn.execute(
        "SELECT id, normalized_q, keywords, aliases FROM qa_pairs ORDER BY hit_count DESC"
    ).fetchall()

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

    # Build exact-match index (norm_q + all alias norms → row_id)
    # Earlier rows (higher hit_count) take priority on duplicate norms.
    exact: Dict[str, int] = {}
    for (row_id, norm_q, q_toks, kw_toks, alias_entries) in cache:
        exact.setdefault(norm_q, row_id)
        for (norm_a, _) in alias_entries:
            exact.setdefault(norm_a, row_id)
    global _exact_index
    _exact_index = exact

    return _score_cache


def _invalidate_cache():
    global _score_cache, _prepared_cache, _exact_index
    _score_cache = None
    _prepared_cache = {}
    _exact_index = None


def _append_to_cache(row_id: int, norm: str, keywords: str = "", aliases: str = ""):
    """Incrementally append a newly-inserted row to the live cache (avoids full rebuild).
    Called by add_qa() so background auto-learn never triggers a full cache invalidation.
    Only used when cache already exists; falls back to full rebuild on next access if not.
    """
    global _score_cache
    if _score_cache is None:
        return  # Cache not built yet — will be built on next find_answer() call

    q_toks = frozenset(_tokens(norm))

    kw_toks: frozenset = frozenset()
    if keywords:
        kw_flat = keywords.replace('_', ' ')
        kw_set = set()
        for phrase in kw_flat.split(','):
            for tok in phrase.strip().lower().split():
                if len(tok) > 2 and tok not in _STOP_WORDS:
                    kw_set.add(_stem(tok))
        kw_toks = frozenset(kw_set)

    alias_entries = []
    if aliases:
        for alias in aliases.split('|'):
            alias = alias.strip()
            if alias:
                norm_a = normalize_question(alias)
                alias_entries.append((norm_a, frozenset(_tokens(norm_a))))

    # Append new entry; hit_count=0 so it goes to end (cache is ordered hit_count DESC)
    _score_cache.append((row_id, norm, q_toks, kw_toks, alias_entries))


# ── Schema ────────────────────────────────────────────────────────────────────

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS qa_pairs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    question         TEXT    NOT NULL,
    normalized_q     TEXT    NOT NULL,
    answer_theory    TEXT    DEFAULT '',
    answer_coding    TEXT    DEFAULT '',
    answer_humanized TEXT    DEFAULT '',
    type             TEXT    NOT NULL DEFAULT 'theory',
    keywords         TEXT    DEFAULT '',
    aliases          TEXT    DEFAULT '',
    tags             TEXT    DEFAULT '',
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    hit_count        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_type ON qa_pairs(type);
CREATE INDEX IF NOT EXISTS idx_normalized_q ON qa_pairs(normalized_q);
CREATE INDEX IF NOT EXISTS idx_hit_count ON qa_pairs(hit_count DESC);
CREATE INDEX IF NOT EXISTS idx_questions_role ON questions(role);

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

CREATE TABLE IF NOT EXISTS ext_users (
    token            TEXT    PRIMARY KEY,
    name             TEXT    NOT NULL,
    role             TEXT    DEFAULT '',
    coding_language  TEXT    DEFAULT 'python',
    db_user_id       INTEGER DEFAULT 1,
    active           INTEGER DEFAULT 1,
    speed_preset     TEXT    DEFAULT 'balanced',
    silence_duration REAL    DEFAULT 1.2,
    llm_model        TEXT    DEFAULT 'claude-haiku-4-5-20251001',
    stt_backend      TEXT    DEFAULT 'sarvam',
    stt_model        TEXT    DEFAULT 'sarvam-saarika-v2',
    created_at       TEXT    NOT NULL,
    last_seen        TEXT    DEFAULT '',
    total_questions  INTEGER DEFAULT 0,
    total_llm_hits   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS usage_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT    NOT NULL,
    question    TEXT    NOT NULL,
    source      TEXT    DEFAULT 'db',
    answer_ms   INTEGER DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_token ON usage_log(token);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_log(created_at);

CREATE TABLE IF NOT EXISTS stt_corrections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    wrong      TEXT    NOT NULL COLLATE NOCASE,
    right_text TEXT    NOT NULL,
    source     TEXT    DEFAULT 'auto',
    hit_count  INTEGER DEFAULT 0,
    created_at TEXT    NOT NULL,
    UNIQUE(wrong COLLATE NOCASE)
);
CREATE INDEX IF NOT EXISTS idx_stt_wrong ON stt_corrections(wrong COLLATE NOCASE);
"""

_MIGRATE_ALIASES = """
ALTER TABLE qa_pairs ADD COLUMN aliases TEXT DEFAULT '';
"""

_MIGRATE_USER_SKILLS = "ALTER TABLE users ADD COLUMN key_skills TEXT DEFAULT '';"
_MIGRATE_USER_INSTRUCTIONS = "ALTER TABLE users ADD COLUMN custom_instructions TEXT DEFAULT '';"
_MIGRATE_USER_DOMAIN = "ALTER TABLE users ADD COLUMN domain TEXT DEFAULT '';"
_MIGRATE_USER_UPDATED_AT = "ALTER TABLE users ADD COLUMN updated_at TEXT DEFAULT '';"


def _get_conn():
    return _pgb.get_conn()


def _get_read_conn():
    """Thread-local persistent read connection — never call .close() on this."""
    return _pgb.get_read_conn()


def init_db():
    """Create tables; add aliases/tags/answer_humanized columns if missing (migration)."""
    with _lock:
        conn = _get_conn()
        conn.executescript(_CREATE_SQL)
        # Safe migration: add columns if they don't exist
        cols = {row[1] for row in conn.execute("PRAGMA table_info(qa_pairs)")}
        if 'aliases' not in cols:
            conn.execute("ALTER TABLE qa_pairs ADD COLUMN aliases TEXT DEFAULT ''")
        if 'tags' not in cols:
            conn.execute("ALTER TABLE qa_pairs ADD COLUMN tags TEXT DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags ON qa_pairs(tags)")
        if 'answer_humanized' not in cols:
            conn.execute("ALTER TABLE qa_pairs ADD COLUMN answer_humanized TEXT DEFAULT ''")
        if 'company' not in cols:
            conn.execute("ALTER TABLE qa_pairs ADD COLUMN company TEXT DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_company ON qa_pairs(company)")
        if 'role_tag' not in cols:
            conn.execute("ALTER TABLE qa_pairs ADD COLUMN role_tag TEXT DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_role_tag ON qa_pairs(role_tag)")

        # Safe migration: add resume_file and resume_summary to users table
        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if 'resume_file' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN resume_file TEXT DEFAULT ''")
        if 'resume_summary' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN resume_summary TEXT DEFAULT ''")

        # Safe migration: add key_skills, custom_instructions, domain, updated_at, resume_path to users table
        for sql in [_MIGRATE_USER_SKILLS, _MIGRATE_USER_INSTRUCTIONS, _MIGRATE_USER_DOMAIN, _MIGRATE_USER_UPDATED_AT,
                    "ALTER TABLE users ADD COLUMN resume_path TEXT DEFAULT '';"]:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass  # Column already exists (sqlite3.OperationalError or psycopg2 DuplicateColumn)

        # Create ext_users and usage_log tables if they don't exist (safe migration)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ext_users (
                token TEXT PRIMARY KEY, name TEXT NOT NULL,
                role TEXT DEFAULT '', coding_language TEXT DEFAULT 'python',
                db_user_id INTEGER DEFAULT 1, active INTEGER DEFAULT 1,
                speed_preset TEXT DEFAULT 'balanced', silence_duration REAL DEFAULT 1.2,
                llm_model TEXT DEFAULT 'claude-haiku-4-5-20251001',
                stt_backend TEXT DEFAULT 'sarvam', stt_model TEXT DEFAULT 'sarvam-saarika-v2',
                created_at TEXT NOT NULL, last_seen TEXT DEFAULT '',
                total_questions INTEGER DEFAULT 0, total_llm_hits INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL,
                question TEXT NOT NULL, source TEXT DEFAULT 'db',
                answer_ms INTEGER DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_usage_token ON usage_log(token);
            CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_log(created_at);
        """)
        conn.commit()

        # Ensure performance indexes exist on existing databases
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_normalized_q ON qa_pairs(normalized_q)",
            "CREATE INDEX IF NOT EXISTS idx_hit_count ON qa_pairs(hit_count DESC)",
            "CREATE INDEX IF NOT EXISTS idx_questions_role ON questions(role)",
        ]:
            try:
                conn.execute(idx_sql)
            except Exception:
                pass

        conn.commit()

        # Seed Unix/Bash Q&A pairs if not already present
        count = conn.execute("SELECT COUNT(*) FROM qa_pairs WHERE tags LIKE '%unix-seed%'").fetchone()[0]
        needs_unix_seed = (count == 0)
        # Seed interview-prompt Q&A pairs if not already present
        count2 = conn.execute("SELECT COUNT(*) FROM qa_pairs WHERE tags LIKE '%python-seed%'").fetchone()[0]
        needs_interview_seed = (count2 == 0)
        conn.close()

    # Call seed functions OUTSIDE the lock — they acquire _lock internally per insertion
    if needs_unix_seed:
        _seed_unix_qa()
    if needs_interview_seed:
        _seed_interview_prompt_qa()

    # Backfill answer_humanized for any rows that are missing it
    _backfill_humanized()


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
        theory_ans = answer if qa_type == "theory" else ""
        coding_ans = answer if qa_type == "bash" else ""
        humanized = _build_humanized(theory_ans, coding_ans)
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
                            answer_humanized, type, keywords, aliases, tags,
                            created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (question, norm, theory_ans, coding_ans, humanized,
                         "theory" if qa_type == "theory" else "coding",
                         keywords, aliases, tags, ts, ts)
                    )
                    conn.commit()
            finally:
                conn.close()


def _seed_interview_prompt_qa():
    """Seed all INTERVIEW_PROMPT Q&A examples into the DB with role tags for instant lookup."""
    # Format: (question, answer, qa_type, keywords, aliases, tags)
    _QA = [
        # ── PYTHON ──────────────────────────────────────────────────────────
        (
            "What is a decorator in Python?",
            "- A decorator wraps a function to add behavior without touching its code\n- It uses the @ syntax and works great for logging, auth, or caching\n- I've built retry and timing decorators for production API endpoints",
            "theory", "python decorator function wrapping @login_required cache_page",
            "python decorator wrap function|decorator pattern python|what does @ do python",
            "python-seed"
        ),
        (
            "Difference between list and tuple in Python?",
            "- Lists are mutable so you can add or change items any time\n- Tuples are immutable and slightly faster for data that won't change\n- I use tuples for config constants and lists for collections that grow",
            "theory", "python list tuple mutable immutable difference",
            "list vs tuple python|list tuple difference|when to use tuple python",
            "python-seed"
        ),
        (
            "What is the GIL in Python?",
            "- The GIL lets only one thread run Python bytecode at a time\n- It prevents race conditions on objects but limits CPU-bound threading\n- I work around it using multiprocessing or asyncio for parallel tasks",
            "theory", "GIL global interpreter lock python threading cpu-bound",
            "global interpreter lock python|GIL python threading|python GIL explanation",
            "python-seed"
        ),
        (
            "What does yield do in Python?",
            "- yield pauses a function and returns a value without ending it\n- The next call to next() resumes from where it left off\n- I use generators in Django to stream large querysets without loading all rows",
            "theory", "python yield generator iterator pause resume",
            "what is yield python|yield keyword python|python generator yield",
            "python-seed"
        ),
        (
            "What is a lambda in Python?",
            "- A lambda is an anonymous one-line function — no def, no name, just inline logic\n- `sorted(users, key=lambda u: u['age'])` or `double = lambda x: x * 2`\n- I use lambdas in map/filter/sorted when writing a full function would be overkill",
            "theory", "python lambda anonymous function inline one-line",
            "lambda function python|anonymous function python|python lambda expression",
            "python-seed"
        ),
        (
            "What is a list comprehension in Python?",
            "- A list comprehension builds a new list in one line using a for-expression inside brackets\n- `evens = [x for x in range(20) if x % 2 == 0]` vs a 4-line for loop\n- I use them daily for transforming querysets, filtering lists, and building dicts fast",
            "theory", "python list comprehension for-expression one-line filter transform",
            "list comprehension python|python list comprehension syntax|dict comprehension python",
            "python-seed"
        ),
        (
            "What is a generator in Python?",
            "- A generator is a function that yields values one at a time instead of building a full list\n- `def rows(): yield from db.execute('SELECT ...')` — reads one row per iteration\n- I use generators for large file processing and streaming DB results without memory blowup",
            "theory", "python generator yield lazy evaluation memory efficient iterator",
            "python generator function|generator vs list python|what is generator in python",
            "python-seed"
        ),
        (
            "What is *args and **kwargs in Python?",
            "- `*args` collects extra positional arguments as a tuple; `**kwargs` collects keyword args as a dict\n- `def log(*args, **kwargs): print(args, kwargs)` — accepts anything without breaking\n- I use them in wrapper functions and middleware to pass-through arguments transparently",
            "theory", "python args kwargs positional keyword variable arguments",
            "*args **kwargs python|python args kwargs explanation|variable arguments python",
            "python-seed"
        ),
        (
            "What is the difference between is and == in Python?",
            "- `==` checks if two objects have equal values; `is` checks if they are the same object in memory\n- `[] == []` is True but `[] is []` is False — they're different objects\n- I use `is` only for None checks (`if x is None`) and `==` for value comparisons",
            "theory", "python is == equality identity operator difference",
            "is vs == python|python equality identity|python is operator",
            "python-seed"
        ),
        # ── LINUX / PRODUCTION SUPPORT ───────────────────────────────────────
        (
            "How do you check disk usage in Linux?",
            "- `df -h` shows disk usage per partition in human-readable size\n- `du -sh /var/log/*` finds which directory is eating space\n- If disk hits 100% I check `/tmp`, old logs, and core dumps first",
            "theory", "linux disk usage df du check partition storage",
            "check disk space linux|df -h command|linux disk full troubleshoot",
            "linux-seed,prod-support-seed"
        ),
        (
            "How do you troubleshoot high CPU in Linux?",
            "- `top` or `htop` shows which process is spiking CPU in real time\n- `ps aux --sort=-%cpu | head` gives the top CPU consumers at that moment\n- I've traced runaway processes to stuck loops or zombie child processes",
            "theory", "linux high cpu troubleshoot top htop ps process spike",
            "high cpu linux|troubleshoot cpu usage linux|linux cpu 100% fix",
            "linux-seed,prod-support-seed"
        ),
        (
            "How do you check a service that is not starting in Linux?",
            "- `systemctl status servicename` shows the current state and recent logs\n- `journalctl -u servicename -n 50` gives the last 50 log lines\n- I usually start with the exit code and work backwards to the root cause",
            "theory", "linux service not starting systemctl journalctl status logs",
            "service not starting linux|systemctl failed|linux service troubleshoot",
            "linux-seed,prod-support-seed"
        ),
        (
            "What is an OOM kill in Linux?",
            "- OOM killer runs when the kernel can't allocate memory to any process\n- It scores processes by memory usage and kills the highest-scoring one\n- I've seen it kill Java apps when the heap limit wasn't set correctly",
            "theory", "linux OOM killer out of memory kernel memory allocation",
            "OOM kill linux|out of memory killer|linux OOM killer explanation",
            "linux-seed,prod-support-seed"
        ),
        (
            "How do you analyze a production incident?",
            "- I start with `dmesg`, `journalctl`, and application logs to find the event\n- Then I check CPU, memory, and disk metrics around the incident window\n- After fixing, I write an RCA with timeline, impact, and prevention steps",
            "theory", "production incident analysis RCA root cause dmesg journalctl logs",
            "production incident troubleshoot|how to handle production incident|RCA production issue",
            "linux-seed,prod-support-seed"
        ),
        # ── DEVOPS / CI-CD ───────────────────────────────────────────────────
        (
            "What is the difference between Docker and a VM?",
            "- Docker shares the host OS kernel so containers start in seconds\n- VMs have their own OS, making them heavier but more isolated\n- I containerize apps with Docker and use VMs when full OS isolation is needed",
            "theory", "docker vm virtual machine container difference kernel OS isolation",
            "docker vs vm|container vs virtual machine|docker vm difference",
            "devops-seed"
        ),
        (
            "How does a CI/CD pipeline work?",
            "- Code push triggers a pipeline that builds, tests, and packages the artifact\n- If tests pass, the artifact is pushed to a registry and deployed automatically\n- I've set up GitHub Actions pipelines that deploy to Kubernetes on merge to main",
            "theory", "cicd pipeline continuous integration deployment build test deploy",
            "cicd pipeline explanation|how does CI/CD work|continuous integration deployment",
            "devops-seed"
        ),
        (
            "What is GitOps?",
            "- GitOps means Git is the single source of truth for your infra state\n- Tools like ArgoCD sync the cluster to match the desired state in Git\n- I've used ArgoCD so every deployment is a pull request, fully auditable",
            "theory", "gitops argocd git infrastructure as code declarative deployment",
            "what is GitOps|gitops argocd|git as source of truth infra",
            "devops-seed"
        ),
        (
            "What is a Helm chart?",
            "- A Helm chart is a template package for deploying apps on Kubernetes\n- It lets you parametrize manifests so the same chart works across environments\n- I use values.yaml overrides for dev, staging, and prod with the same chart",
            "theory", "helm chart kubernetes template package manifest deployment",
            "what is helm chart|helm kubernetes deployment|helm values.yaml",
            "devops-seed,kubernetes-seed"
        ),
        # ── SRE / MONITORING ─────────────────────────────────────────────────
        (
            "What is an error budget in SRE?",
            "- An error budget is the allowed downtime or error rate defined by the SLO\n- If the budget runs out, you freeze feature work and focus on reliability\n- I've used it to balance release velocity with service stability on-call",
            "theory", "error budget SRE SLO reliability downtime allowance",
            "what is error budget|sre error budget|error budget SLO explanation",
            "sre-seed"
        ),
        (
            "What are the four golden signals in SRE?",
            "- The four golden signals are latency, traffic, errors, and saturation\n- They cover the key dimensions that affect user experience and system health\n- I monitor these in Grafana and alert on error rate and latency p99 spikes",
            "theory", "four golden signals sre latency traffic errors saturation monitoring",
            "four golden signals|sre monitoring signals|latency traffic errors saturation",
            "sre-seed"
        ),
        (
            "What is the difference between SLO SLI and SLA?",
            "- SLI is the actual metric like request success rate or p99 latency\n- SLO is the target you set for that metric, like 99.9% over 30 days\n- SLA is the contract with consequences if you miss the SLO",
            "theory", "SLO SLI SLA service level objective indicator agreement difference",
            "SLO SLI SLA difference|what is SLO SLI|service level agreement objective",
            "sre-seed"
        ),
        # ── KUBERNETES ───────────────────────────────────────────────────────
        (
            "What is Kubernetes architecture?",
            "- Kubernetes has a control plane with API server, scheduler, and etcd for cluster state\n- Worker nodes run pods controlled by kubelet and the container runtime\n- I've deployed microservices on it with auto-scaling and rolling updates",
            "theory", "kubernetes architecture control plane worker node etcd kubelet scheduler",
            "kubernetes architecture|k8s components|kubernetes control plane worker node",
            "kubernetes-seed,devops-seed"
        ),
        (
            "Difference between StatefulSet and Deployment in Kubernetes?",
            "- Deployments manage stateless pods that can be replaced at any time\n- StatefulSets give each pod a stable identity, hostname, and persistent volume\n- I use StatefulSets for databases like PostgreSQL and Elasticsearch in Kubernetes",
            "theory", "kubernetes statefulset deployment stateless stateful pod identity volume",
            "statefulset vs deployment kubernetes|when to use statefulset|kubernetes stateful workloads",
            "kubernetes-seed"
        ),
        (
            "What is a liveness probe versus a readiness probe in Kubernetes?",
            "- Liveness probe restarts a pod if the app is stuck or crashed internally\n- Readiness probe removes the pod from the service endpoints until it's ready\n- I set both on every service so bad deploys don't get live traffic",
            "theory", "kubernetes liveness readiness probe health check pod restart",
            "liveness vs readiness probe|kubernetes health check|pod liveness readiness",
            "kubernetes-seed"
        ),
        (
            "What is RBAC in Kubernetes?",
            "- RBAC controls who can do what in the cluster using roles and bindings\n- A Role defines permissions, a RoleBinding assigns that Role to a user or group\n- I create service accounts with least-privilege roles for each workload",
            "theory", "kubernetes RBAC role based access control permissions service account",
            "kubernetes RBAC|role based access control k8s|kubernetes role binding",
            "kubernetes-seed"
        ),
        # ── OPENSTACK ────────────────────────────────────────────────────────
        (
            "What is the role of Nova in OpenStack?",
            "- Nova is OpenStack's compute service that manages VM lifecycle\n- It handles scheduling VMs on hypervisors and communicates with Neutron for networking\n- I've used Nova to launch, resize, and live-migrate instances across compute nodes",
            "theory", "openstack nova compute VM lifecycle hypervisor scheduler",
            "nova openstack|openstack nova service|what is nova openstack",
            "openstack-seed"
        ),
        (
            "What is live migration in OpenStack?",
            "- Live migration moves a running VM from one compute node to another with no downtime\n- It needs shared storage or block migration so the VM disk moves too\n- I've used it during hardware maintenance to drain nodes without guest impact",
            "theory", "openstack live migration VM compute node no downtime shared storage",
            "openstack live migration|VM live migration openstack|migrate VM without downtime",
            "openstack-seed"
        ),
        (
            "What is a security group in OpenStack?",
            "- A security group is a stateful firewall applied to VM network interfaces\n- Rules define allowed ingress and egress traffic by port and protocol\n- I manage them via the API to lock down prod VMs to only needed ports",
            "theory", "openstack security group firewall ingress egress port rules",
            "openstack security group|security group openstack|VM firewall openstack",
            "openstack-seed"
        ),
        # ── JAVA ─────────────────────────────────────────────────────────────
        (
            "How does garbage collection work in Java?",
            "- The JVM tracks object references and marks unreachable objects for collection\n- G1 GC divides the heap into regions and collects garbage incrementally\n- I've tuned GC pauses by adjusting heap size and switching to ZGC for low latency",
            "theory", "java garbage collection JVM G1GC ZGC heap memory management",
            "java GC explanation|garbage collection java|java JVM GC tuning",
            "java-seed"
        ),
        (
            "What is the difference between HashMap and ConcurrentHashMap in Java?",
            "- HashMap is not thread-safe so concurrent writes can corrupt its internal state\n- ConcurrentHashMap uses segment-level locking so multiple threads write safely\n- I use ConcurrentHashMap for shared caches in multi-threaded services",
            "theory", "java hashmap concurrenthashmap thread-safe synchronization difference",
            "hashmap vs concurrenthashmap|java concurrenthashmap|thread safe map java",
            "java-seed"
        ),
        (
            "What is the difference between checked and unchecked exceptions in Java?",
            "- Checked exceptions must be declared or caught at compile time\n- Unchecked exceptions extend RuntimeException and don't need explicit handling\n- I use unchecked exceptions for programming errors and checked for recoverable ones",
            "theory", "java checked unchecked exception runtime compile time difference",
            "checked vs unchecked exception java|java exception types|RuntimeException java",
            "java-seed"
        ),
        (
            "What is a functional interface in Java?",
            "- A functional interface has exactly one abstract method, used with lambda expressions\n- Runnable, Callable, Comparator, and Predicate are common examples from the JDK\n- I use them with Stream API to write concise filter and map operations",
            "theory", "java functional interface lambda single abstract method SAM",
            "functional interface java|java lambda interface|what is functional interface java",
            "java-seed"
        ),
        # ── JAVASCRIPT ───────────────────────────────────────────────────────
        (
            "How does the event loop work in JavaScript?",
            "- The event loop picks callbacks from the task queue when the call stack is empty\n- Promises use the microtask queue which runs before the next task queue item\n- I debug async ordering issues by thinking in terms of call stack, microtask, and task queue",
            "theory", "javascript event loop call stack task queue microtask async",
            "javascript event loop|how event loop works js|async javascript event loop",
            "javascript-seed"
        ),
        (
            "What is a closure in JavaScript?",
            "- A closure is a function that remembers variables from its outer scope after it returns\n- This lets inner functions access enclosing variables even after the outer function is done\n- I use closures for factory functions and to create private state in modules",
            "theory", "javascript closure scope outer function variable private state",
            "javascript closure|what is closure js|closure example javascript",
            "javascript-seed"
        ),
        (
            "What is the difference between var let and const in JavaScript?",
            "- var is function-scoped and hoisted, which can cause confusing bugs\n- let and const are block-scoped and not accessible before declaration\n- I always use const by default and let only when I need to reassign",
            "theory", "javascript var let const scope hoisting block function difference",
            "var vs let vs const|javascript variable declaration|let const var difference js",
            "javascript-seed"
        ),
        (
            "What is event delegation in JavaScript?",
            "- Event delegation attaches one listener to a parent instead of each child element\n- It works because events bubble up the DOM tree to the parent\n- I use it for dynamic lists where items are added after the page loads",
            "theory", "javascript event delegation bubble parent listener DOM dynamic",
            "event delegation javascript|js event delegation|bubbling event listener javascript",
            "javascript-seed"
        ),
        # ── HTML / CSS ────────────────────────────────────────────────────────
        (
            "What is the CSS box model?",
            "- Every HTML element has content, padding, border, and margin around it\n- box-sizing: border-box makes width include padding and border, which is more predictable\n- I set border-box globally in every project to avoid layout calculation bugs",
            "theory", "css box model content padding border margin box-sizing border-box",
            "css box model|what is box model css|border-box vs content-box css",
            "html-seed"
        ),
        (
            "What is the difference between flexbox and CSS grid?",
            "- Flexbox is one-dimensional, best for laying out items in a row or column\n- Grid is two-dimensional, great for full page layouts with rows and columns\n- I use flexbox for nav bars and card rows, grid for full page layouts",
            "theory", "css flexbox grid one-dimensional two-dimensional layout difference",
            "flexbox vs grid css|css layout flexbox grid|when to use flex vs grid",
            "html-seed"
        ),
        (
            "What is CSS specificity?",
            "- Specificity decides which rule applies when multiple rules target the same element\n- Inline styles beat IDs, IDs beat classes, classes beat element selectors\n- I avoid ID selectors in CSS to keep specificity low and styles easy to override",
            "theory", "css specificity inline id class element selector priority",
            "css specificity|what is css specificity|css selector priority",
            "html-seed"
        ),
        (
            "What are semantic HTML elements?",
            "- Semantic elements like header, nav, main, article describe what the content is\n- They help screen readers, SEO bots, and other developers understand the page structure\n- I use them in every project because they improve accessibility without extra effort",
            "theory", "semantic html elements header nav main article accessibility SEO",
            "semantic html|semantic elements html5|why use semantic html",
            "html-seed"
        ),
        # ── DJANGO ────────────────────────────────────────────────────────────
        (
            "What is the N+1 query problem in Django?",
            "- N+1 happens when you loop over a queryset and each iteration fires a new query\n- select_related does a SQL JOIN to fetch related objects in one query\n- I catch it with Django Debug Toolbar in development before it hits production",
            "theory", "django N+1 query problem select_related queryset performance",
            "N+1 problem django|django N+1 query|select_related django N+1",
            "django-seed,python-seed"
        ),
        (
            "How does Django signal work?",
            "- Signals let decoupled code react to events like saving or deleting a model\n- post_save fires after a model instance is saved, pre_save fires before\n- I use signals to send notifications or update related records after a save",
            "theory", "django signal post_save pre_save event decoupled model",
            "django signals|post_save signal django|how signals work django",
            "django-seed,python-seed"
        ),
        (
            "What is the difference between class-based views and function-based views in Django?",
            "- Class-based views inherit mixins and reduce boilerplate for standard CRUD operations\n- Function-based views are simpler and easier to trace for custom logic\n- I use CBVs for standard list and detail pages, FBVs for anything with complex branching",
            "theory", "django class-based views function-based CBV FBV difference mixins",
            "CBV vs FBV django|class based vs function based views django|when to use CBV FBV",
            "django-seed,python-seed"
        ),
        (
            "How does DRF serializer work?",
            "- A serializer converts Django model instances to JSON and validates incoming data\n- It works like a Django form but outputs data instead of HTML\n- I use ModelSerializer for standard CRUD and override validate_ methods for custom rules",
            "theory", "DRF serializer model serializer JSON validate data conversion",
            "drf serializer|django rest framework serializer|modelserializer drf",
            "django-seed,python-seed"
        ),
        # ── DJANGO / DRF DEEP ─────────────────────────────────────────────────
        (
            "What is select_related vs prefetch_related in Django?",
            "- select_related does a SQL JOIN for ForeignKey and OneToOne relations in one query\n- prefetch_related does a separate query and joins in Python — needed for ManyToMany\n- I always profile with Django Debug Toolbar to catch N+1 before it hits production",
            "theory", "django select_related prefetch_related JOIN query ManyToMany ForeignKey",
            "select_related vs prefetch_related|django queryset optimization|prefetch_related django",
            "django-seed,python-seed"
        ),
        (
            "How do you create a custom DRF permission class?",
            "- Subclass BasePermission and override has_permission or has_object_permission\n- Return True to allow, False to deny — DRF raises 403 automatically\n- I use custom permissions to enforce object-level ownership checks on every ViewSet",
            "theory", "DRF custom permission BasePermission has_permission has_object_permission",
            "custom drf permission|django rest framework permissions|object level permission drf",
            "django-seed,python-seed"
        ),
        (
            "How does DRF JWT authentication work?",
            "- The client POSTs credentials to /api/token/ and gets access and refresh tokens\n- The access token is short-lived; the client uses the refresh token to get a new one\n- I configure SimpleJWT with ROTATE_REFRESH_TOKENS and blacklist the old tokens on logout",
            "theory", "DRF JWT authentication token SimpleJWT access refresh token",
            "drf jwt auth|django rest framework JWT|simplejwt drf|jwt token django",
            "django-seed,python-seed"
        ),
        (
            "What is the difference between APIView and ViewSet in DRF?",
            "- APIView maps HTTP methods directly — get(), post(), put() methods on the class\n- ViewSet maps to CRUD actions — list(), create(), retrieve(), update() — wired via Router\n- I use ViewSet + DefaultRouter for standard CRUD and APIView for custom logic endpoints",
            "theory", "DRF APIView ViewSet router CRUD HTTP methods difference",
            "apiview vs viewset drf|django rest framework viewset|drf router viewset",
            "django-seed,python-seed"
        ),
        (
            "How does Celery work with Django?",
            "- Celery is a distributed task queue — Django sends tasks to a broker like Redis\n- Workers pull tasks from the queue and execute them outside the HTTP request cycle\n- I use it for sending emails, generating reports, and any task over 200ms",
            "theory", "celery django task queue redis broker worker background async",
            "celery django|django celery redis|celery task queue django|async tasks django",
            "django-seed,python-seed"
        ),
        (
            "How do you handle database migrations in Django?",
            "- `makemigrations` generates migration files from model changes; `migrate` applies them\n- I never delete migration files in production — I squash them if history gets too long\n- For team conflicts I always run `showmigrations` and resolve merge migrations before deploy",
            "theory", "django migrations makemigrations migrate squash showmigrations database schema",
            "django migrations|makemigrations migrate django|django migration conflicts",
            "django-seed,python-seed"
        ),
        (
            "What is Django caching and how do you use it?",
            "- Django's cache framework supports Redis, Memcached, or file-based backends\n- `cache.set('key', value, timeout=300)` stores data; `cache.get('key')` retrieves it\n- I cache expensive QuerySets with `cache_page` on views and manual cache.set for DB aggregates",
            "theory", "django caching redis memcached cache_page cache.set cache.get backend",
            "django cache|django redis caching|cache_page django|django caching strategy",
            "django-seed,python-seed"
        ),
        # ── FLASK ─────────────────────────────────────────────────────────────
        (
            "What is a Flask Blueprint?",
            "- A Blueprint groups related routes, templates, and static files into a module\n- It lets you split a large app into feature-based packages you register at startup\n- I use Blueprints to separate auth, API, and admin routes into their own files",
            "theory", "flask blueprint routes templates module register application factory",
            "flask blueprint|what is blueprint flask|flask app structure blueprint",
            "flask-seed,python-seed"
        ),
        (
            "What is the application context in Flask?",
            "- The application context pushes g and current_app so you can access them outside a request\n- It's needed when running background tasks or CLI commands outside the request cycle\n- I push it manually in Celery tasks that need database access via Flask-SQLAlchemy",
            "theory", "flask application context g current_app request context background task",
            "flask app context|application context flask|flask context object|flask g current_app",
            "flask-seed,python-seed"
        ),
        (
            "What is WSGI and how does Flask use it?",
            "- WSGI is a standard interface between Python web apps and servers like gunicorn\n- Flask implements the WSGI callable so any WSGI server can run it\n- I deploy Flask behind gunicorn with 4 workers and nginx as the reverse proxy",
            "theory", "WSGI flask gunicorn nginx python web server interface standard",
            "what is WSGI flask|flask wsgi gunicorn|wsgi server flask|flask production deployment",
            "flask-seed,python-seed"
        ),
        # ── SQL / POSTGRESQL ─────────────────────────────────────────────────
        (
            "What is the difference between INNER JOIN and LEFT JOIN in SQL?",
            "- INNER JOIN returns only rows where both tables have a matching key\n- LEFT JOIN returns all rows from the left table, with nulls where there's no match\n- I use LEFT JOIN when I need results even if the related record doesn't exist",
            "theory", "sql inner join left join difference null matching rows",
            "inner join vs left join|sql join types|left join inner join sql",
            "sql-seed"
        ),
        (
            "What are ACID properties in a database?",
            "- Atomicity means the whole transaction commits or none of it does\n- Consistency, Isolation, and Durability ensure data is valid, transactions don't interfere, and committed data survives crashes\n- I rely on ACID when money or inventory records must never be partially updated",
            "theory", "ACID atomicity consistency isolation durability database transaction",
            "ACID properties database|what is ACID|database ACID explained",
            "sql-seed"
        ),
        (
            "What is MVCC in PostgreSQL?",
            "- MVCC keeps old row versions so readers never block writers and vice versa\n- Each transaction sees a snapshot of the database as it was at its start time\n- It makes PostgreSQL fast for read-heavy workloads without explicit read locks",
            "theory", "postgresql MVCC multiversion concurrency control snapshot isolation row versions",
            "MVCC postgresql|what is MVCC|multiversion concurrency control postgres",
            "sql-seed"
        ),
        (
            "What is a window function in SQL?",
            "- A window function runs a calculation across a set of rows related to the current row\n- ROW_NUMBER, RANK, and LAG are common examples for ranking and comparing rows\n- I use them to calculate running totals and find the latest record per group",
            "theory", "sql window function ROW_NUMBER RANK LAG OVER partition running total",
            "sql window function|what is window function sql|ROW_NUMBER RANK sql",
            "sql-seed"
        ),
        (
            "What is the purpose of VACUUM in PostgreSQL?",
            "- VACUUM reclaims space from rows marked as dead after UPDATE or DELETE\n- Without it, table bloat grows and query performance degrades over time\n- I run autovacuum in production and manually ANALYZE after large batch loads",
            "theory", "postgresql vacuum autovacuum table bloat dead rows space reclaim",
            "postgresql vacuum|what is vacuum postgres|autovacuum postgresql|table bloat postgres",
            "sql-seed"
        ),
        # ── HR / GENERAL INTERVIEW ────────────────────────────────────────────
        (
            "What are your strengths?",
            "- I'm strong at debugging production issues quickly under pressure\n- I communicate clearly in incidents — updates go out before people ask\n- I own problems end-to-end and don't drop things once I've picked them up",
            "theory", "strengths interview personal qualities debugging communication ownership",
            "what are your strengths|tell me your strengths|interview strengths question",
            "hr-seed"
        ),
        (
            "What are your weaknesses?",
            "- I sometimes over-document things when a quick verbal update would be faster\n- I'm working on delegating more instead of fixing things myself every time\n- I've gotten better at this by consciously asking teammates before diving in",
            "theory", "weaknesses interview self-improvement delegation over-engineering",
            "what are your weaknesses|tell me your weakness|interview weakness question",
            "hr-seed"
        ),
        (
            "Where do you see yourself in five years?",
            "- I want to be leading a production support or SRE team, not just an individual contributor\n- I want to have built systems that catch incidents before users feel them\n- I'm also working toward cloud certifications to move into architecture over time",
            "theory", "five years career goal leadership SRE production support architect",
            "where do you see yourself in 5 years|career goals interview|5 year plan interview",
            "hr-seed"
        ),
        (
            "Why do you want to leave your current job?",
            "- I'm looking for a role with more scale and more complex systems to learn from\n- My current team is good but the growth path has plateaued for me\n- I want to work on infrastructure that handles real production load at volume",
            "theory", "leaving current job reason growth scale complex systems career",
            "why leave current job|reason for leaving job|why do you want to change job",
            "hr-seed"
        ),
        (
            "Tell me about a challenging incident you handled.",
            "- We had a production database connection pool exhaustion that took down the app during peak hours\n- I isolated it to a long-running query holding locks, killed it, and the pool cleared in 90 seconds\n- I then added a query timeout and a runbook so the on-call team could handle it without escalation",
            "theory", "production incident challenge story database connection pool lock resolution",
            "challenging incident story|production problem you solved|tell me about a difficult situation",
            "hr-seed,prod-support-seed"
        ),
        (
            "Why should we hire you?",
            "- I know production support and I've solved the kinds of incidents your team deals with daily\n- I pick up new tools fast and I don't need hand-holding on standard Linux and cloud environments\n- I take ownership — if I commit to something, it gets done",
            "theory", "why hire you unique value ownership production support linux cloud",
            "why should we hire you|why hire me interview|what makes you unique interview",
            "hr-seed"
        ),
        # ── PRODUCTION SUPPORT DEEP ──────────────────────────────────────────
        (
            "How do you handle a P1 production incident?",
            "- First I check monitoring dashboards and recent deployments to correlate the timeline\n- I isolate blast radius — is it one service, one region, or all users — then apply the fastest fix\n- After resolution I write an RCA with timeline, root cause, impact, and preventive action",
            "theory", "P1 incident production support blast radius RCA root cause analysis monitoring",
            "P1 incident handling|how to handle production incident P1|critical incident process",
            "prod-support-seed,linux-seed"
        ),
        (
            "How do you troubleshoot high memory usage on a Linux server?",
            "- `free -h` gives overall memory; `ps aux --sort=-%mem | head` shows top memory consumers\n- `cat /proc/<pid>/status` shows VmRSS for the exact process RSS and swap usage\n- I've caught memory leaks by graphing RSS over time in Grafana and killing the process before OOM fires",
            "theory", "linux high memory usage free ps aux /proc status RSS swap leak grafana",
            "high memory linux troubleshoot|linux memory usage investigation|memory leak linux server",
            "prod-support-seed,linux-seed"
        ),
        (
            "How do you investigate a process that is consuming 100% CPU?",
            "- `top -H -p <pid>` shows per-thread CPU so I can pinpoint the exact thread\n- `strace -p <pid> -c` samples syscalls to see if it's stuck in a tight loop or IO wait\n- I've found infinite loops in Python workers by dumping a traceback with `kill -USR1`",
            "theory", "linux 100% cpu process thread strace top -H syscall traceback loop",
            "100% cpu linux process|investigate high cpu process|thread cpu linux strace",
            "prod-support-seed,linux-seed"
        ),
        (
            "How do you analyze production logs quickly?",
            "- `grep -i 'error\\|exception' app.log | tail -200` gets the most recent errors fast\n- `awk '{print $1}' access.log | sort | uniq -c | sort -rn | head` shows top IPs or endpoints\n- I pipe to `less -S` for wide logs and use `zgrep` on rotated `.gz` files without unpacking",
            "theory", "production logs grep awk tail zgrep less analysis errors exceptions",
            "analyze production logs|log analysis linux|grep log errors production",
            "prod-support-seed,linux-seed"
        ),
        (
            "What is log rotation and how do you configure it?",
            "- Log rotation prevents disk fill-up by archiving old logs and creating fresh ones\n- `/etc/logrotate.d/myapp` defines rotate frequency, compress, and postrotate to reload the service\n- I always set `missingok` and `notifempty` so rotation doesn't fail if the log is missing",
            "theory", "logrotate log rotation compress archive disk /etc/logrotate.d missingok",
            "log rotation linux|configure logrotate|logrotate configuration|how logrotate works",
            "prod-support-seed,linux-seed"
        ),
        # ── AUTOSYS ───────────────────────────────────────────────────────────
        (
            "What is Autosys?",
            "- Autosys (CA Workload Automation) is an enterprise job scheduler that automates batch jobs across servers\n- Jobs are defined in JIL (Job Information Language) and can be chained into boxes (job groups)\n- I've used it to schedule file transfers, report generation, and ETL jobs in production",
            "theory", "autosys CA workload automation job scheduler batch JIL box ETL",
            "what is autosys|autosys job scheduler|CA workload automation autosys",
            "autosys-seed"
        ),
        (
            "What is JIL in Autosys?",
            "- JIL (Job Information Language) is the scripting language used to define Autosys jobs and boxes\n- You write JIL files with attributes like machine, command, start_times, and dependencies\n- I load JIL into Autosys using `jil < myjob.jil` to create or update job definitions",
            "theory", "autosys JIL job information language job definition attributes box",
            "what is JIL autosys|autosys JIL syntax|job information language autosys",
            "autosys-seed"
        ),
        (
            "What are the various job states in Autosys?",
            "- Key states are RUNNING, SUCCESS, FAILURE, TERMINATED, ON_HOLD, ON_ICE, INACTIVE, and ACTIVATED\n- ON_HOLD pauses a job but keeps it in the schedule; ON_ICE completely deactivates it until manually released\n- I use `autorep -j jobname -s` to check current status and `sendevent` to change states",
            "theory", "autosys job states running success failure on_hold on_ice inactive terminated",
            "autosys job status|autosys job states|on_hold on_ice autosys difference",
            "autosys-seed"
        ),
        (
            "What is the difference between ON_HOLD and ON_ICE in Autosys?",
            "- ON_HOLD pauses the job so it won't run at its next scheduled time but stays in the queue\n- ON_ICE completely deactivates the job — it won't run at all until you explicitly take it off ice\n- I put jobs ON_HOLD during maintenance windows and ON_ICE when permanently suspending a job",
            "theory", "autosys ON_HOLD ON_ICE difference deactivate pause maintenance",
            "on_hold vs on_ice autosys|autosys hold ice difference|put autosys job on hold",
            "autosys-seed"
        ),
        (
            "What is a Box job in Autosys?",
            "- A Box is a container job that groups related jobs together and controls their execution flow\n- Jobs inside a box inherit the box's start conditions and run according to their own dependencies\n- I use boxes to group ETL steps so the whole pipeline starts together and fails together",
            "theory", "autosys box job container group dependency ETL pipeline execution flow",
            "autosys box job|what is box in autosys|autosys job box container",
            "autosys-seed"
        ),
        (
            "What are basic Autosys commands?",
            "- `autorep -j jobname` shows job definition; `autorep -j jobname -s` shows current run status\n- `sendevent -E FORCE_STARTJOB -j jobname` manually triggers a job; `sendevent -E CHANGE_STATUS -s ON_HOLD -j jobname` holds it\n- I use `autostatd` to check the event daemon and `autoping` to verify server connectivity",
            "theory", "autosys commands autorep sendevent autostatd autoping FORCE_STARTJOB",
            "autosys basic commands|autorep sendevent commands|autosys command list",
            "autosys-seed"
        ),
        (
            "How do you monitor Autosys jobs?",
            "- `autorep -J ALL -s` lists all jobs and their current status across the scheduler\n- `grep -i 'error\\|failed' /var/log/autosys/*.log | grep $(date +%Y-%m-%d)` finds today's failures\n- I also use the Autosys GUI (WCC) to view job flows and drill into failed job output files",
            "theory", "autosys monitor jobs autorep WCC GUI log grep failed status",
            "monitor autosys jobs|autosys job monitoring|check autosys job status",
            "autosys-seed"
        ),
        (
            "How do you run a failed or ON_HOLD Autosys job?",
            "- `sendevent -E CHANGE_STATUS -s ON_HOLD -j jobname` to put it on hold first if needed\n- `sendevent -E FORCE_STARTJOB -j jobname` to force start a job regardless of its schedule\n- I always check job dependencies with `autorep -j boxname -d` before force-starting to avoid cascade failures",
            "theory", "autosys run failed job FORCE_STARTJOB sendevent ON_HOLD restart",
            "run failed autosys job|force start autosys job|restart autosys job on hold",
            "autosys-seed"
        ),
        (
            "How do you cancel or kill a running Autosys job?",
            "- `sendevent -E KILLJOB -j jobname` sends a kill signal to the running job process\n- `sendevent -E CHANGE_STATUS -s TERMINATED -j jobname` marks it terminated in the scheduler\n- I use KILLJOB only as a last resort and always check if the underlying process actually stopped",
            "theory", "autosys kill job KILLJOB sendevent TERMINATED cancel running job",
            "kill autosys job|cancel running autosys job|autosys KILLJOB command",
            "autosys-seed"
        ),
        (
            "What is sendevent in Autosys?",
            "- `sendevent` is the CLI command to send events to the Autosys event server to change job states\n- Common events are FORCE_STARTJOB, KILLJOB, CHANGE_STATUS, JOB_ON_HOLD, and JOB_OFF_HOLD\n- I use it in shell scripts to automate job control based on file arrival or upstream job status",
            "theory", "autosys sendevent CLI command events FORCE_STARTJOB KILLJOB CHANGE_STATUS",
            "sendevent autosys|autosys sendevent command|what is sendevent autosys",
            "autosys-seed"
        ),
    ]

    for question, answer, qa_type, keywords, aliases, tags in _QA:
        norm = normalize_question(question)
        ts = _nowts()
        theory_ans = answer if qa_type == "theory" else ""
        coding_ans = answer if qa_type == "coding" else ""
        humanized = _build_humanized(theory_ans, coding_ans)
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
                            answer_humanized, type, keywords, aliases, tags,
                            created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (question, norm, theory_ans, coding_ans, humanized,
                         qa_type, keywords, aliases, tags, ts, ts)
                    )
                    conn.commit()
            finally:
                conn.close()

    _invalidate_cache()
    print(f"[DB] Seeded {len(_QA)} interview Q&A pairs from INTERVIEW_PROMPT.")


# ── Humanized answer helpers ───────────────────────────────────────────────────

def _build_humanized(answer_theory: str, answer_coding: str) -> str:
    """
    Convert raw bullet-point answer text to spoken-style plain text.
    Lazy-imports humanize_response from llm_client to avoid circular import at
    module load time (llm_client itself imports config which is safe).
    Falls back to a simple inline strip if import fails.
    """
    raw = (answer_theory or answer_coding or "").strip()
    if not raw:
        return ""
    try:
        from llm_client import humanize_response as _hr
        return _hr(raw)
    except Exception:
        # Minimal fallback: strip leading "- " bullet markers only; keep backticks
        # so the web frontend's renderInline() can style commands as <code>
        lines = []
        for line in raw.splitlines():
            line = line.strip().lstrip("- ").strip()
            if line:
                lines.append(line)
        return " ".join(lines)


def _backfill_humanized():
    """Populate answer_humanized for any rows where it is empty.

    Also re-generates seeded rows where answer_theory contains inline backticks
    but answer_humanized does not — this detects a stale backfill from the old
    humanize_response() which stripped backticks (breaking <code> rendering in
    the web frontend's renderInline()).
    """
    with _lock:
        conn = _get_conn()
        try:
            # Rows with empty humanized: always backfill
            empty_rows = conn.execute(
                "SELECT id, answer_theory, answer_coding FROM qa_pairs "
                "WHERE answer_humanized = '' OR answer_humanized IS NULL"
            ).fetchall()
            # Stale rows: theory has backtick code but humanized lost them (old bug)
            stale_rows = conn.execute(
                "SELECT id, answer_theory, answer_coding FROM qa_pairs "
                "WHERE answer_theory LIKE '%`%' AND answer_humanized NOT LIKE '%`%' "
                "AND (answer_humanized != '' AND answer_humanized IS NOT NULL)"
            ).fetchall()
        finally:
            conn.close()

    rows = list(empty_rows) + list(stale_rows)
    if not rows:
        return

    updates = []
    for r in rows:
        humanized = _build_humanized(r["answer_theory"] or "", r["answer_coding"] or "")
        if humanized:
            updates.append((humanized, r["id"]))

    if not updates:
        return

    with _lock:
        conn = _get_conn()
        try:
            conn.executemany(
                "UPDATE qa_pairs SET answer_humanized=? WHERE id=?", updates
            )
            conn.commit()
        finally:
            conn.close()

    stale_count = len(stale_rows)
    empty_count = len(empty_rows)
    if stale_count:
        print(f"  [DB] Re-generated answer_humanized (backtick fix) for {stale_count} seeded rows")
    if empty_count:
        print(f"  [DB] Backfilled answer_humanized for {empty_count} empty rows")


# ── User Profile CRUD ─────────────────────────────────────────────────────────

def add_user(name: str, role: str, experience_years: int, resume_text: str = "",
             job_description: str = "", self_introduction: str = "",
             key_skills: str = "", custom_instructions: str = "", domain: str = "") -> int:
    ts = _nowts()
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                """INSERT INTO users (name, role, experience_years, resume_text,
                   job_description, self_introduction, key_skills, custom_instructions, domain, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (name, role, experience_years, resume_text, job_description,
                 self_introduction, key_skills.strip(), custom_instructions.strip(), domain.strip(), ts)
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
                resume_file: str = None, resume_summary: str = None, resume_path: str = None,
                key_skills: str = None, custom_instructions: str = None, domain: str = None) -> bool:
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                return False

            _row_dict   = dict(row)
            new_name    = name              if name              is not None else row["name"]
            new_role    = role              if role              is not None else row["role"]
            new_exp     = experience_years  if experience_years  is not None else row["experience_years"]
            new_resume  = resume_text       if resume_text       is not None else row["resume_text"]
            new_jd      = job_description   if job_description   is not None else row["job_description"]
            new_intro   = self_introduction if self_introduction is not None else row["self_introduction"]
            new_rf      = resume_file    if resume_file    is not None else _row_dict.get("resume_file",    "")
            new_rs      = resume_summary if resume_summary is not None else _row_dict.get("resume_summary", "")
            new_rp      = resume_path    if resume_path    is not None else _row_dict.get("resume_path",    "")
            new_ks = key_skills           if key_skills           is not None else (row["key_skills"] if "key_skills" in row.keys() else '')
            new_ci = custom_instructions  if custom_instructions  is not None else (row["custom_instructions"] if "custom_instructions" in row.keys() else '')
            new_dm = domain               if domain               is not None else (row["domain"] if "domain" in row.keys() else '')
            ts = _nowts()

            conn.execute(
                """UPDATE users SET name=?, role=?, experience_years=?, resume_text=?,
                   job_description=?, self_introduction=?, resume_file=?, resume_summary=?, resume_path=?,
                   key_skills=?, custom_instructions=?, domain=?, updated_at=? WHERE id=?""",
                (new_name, new_role, new_exp, new_resume, new_jd, new_intro, new_rf, new_rs, new_rp,
                 new_ks, new_ci, new_dm, ts, user_id)
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
    _invalidate_cache()  # Invalidate prepared cache so new question is picked up

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


def _get_prepared_cache(role: str) -> List:
    """Build/return in-memory cache for a role's prepared questions. O(1) on warm."""
    global _prepared_cache
    if role in _prepared_cache:
        return _prepared_cache[role]
    with _lock:
        conn = _get_conn()
        try:
            if role:
                rows = conn.execute(
                    "SELECT id, question, prepared_answer FROM questions WHERE role=?", (role,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, question, prepared_answer FROM questions"
                ).fetchall()
        finally:
            conn.close()
    cache = []
    for r in rows:
        norm_q = normalize_question(r["question"])
        q_toks = frozenset(_tokens(norm_q))
        cache.append((r["id"], norm_q, q_toks, r["prepared_answer"]))
    _prepared_cache[role] = cache
    return cache


def find_prepared_answer(question: str, role: str = None) -> Optional[Tuple[str, float, int]]:
    """Search the 'questions' table with role filtering using in-memory cache."""
    norm_input = normalize_question(question)
    input_toks = frozenset(_tokens(norm_input))
    if not input_toks:
        return None

    cache = _get_prepared_cache(role or "")

    best_score = 0.0
    best_answer = None
    best_id = None

    for qid, norm_q, q_toks, answer in cache:
        if norm_input == norm_q:
            return answer, 1.0, qid
        if not q_toks:
            continue
        score = len(input_toks & q_toks) / len(input_toks | q_toks)
        if input_toks <= q_toks:
            score = max(score, MATCH_THRESHOLD + 0.05)
        if score > best_score:
            best_score = score
            best_answer = answer
            best_id = qid

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
    q = q.translate(_PUNCT_STRIP)    # single C-level pass, no regex engine
    q = q.replace("_", " ")          # select_related → select related
    q = _SPACE_RE.sub(" ", q).strip() # pre-compiled, avoids re.compile per call
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
        # Inversely weighted by stored length so shorter/more-specific matches score higher
        if input_toks <= stored_toks:
            boost_a = MATCH_THRESHOLD + 0.05 + (len(input_toks) / max(len(stored_toks), 1)) * 0.15
            score = max(score, boost_a)

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

def find_answer(question: str, want_code: bool = False,
                user_role: str = "", company: str = "", role_tag: str = "") -> Optional[Tuple[str, float, int]]:
    """
    Search DB for a matching Q&A pair.
    Returns (answer_text, score, qa_id) or None.
    want_code=True  → prefer answer_coding
    want_code=False → prefer answer_theory
    user_role       → if provided, also checks the role-filtered questions table first

    Search order:
      1. Role-specific questions table (if user_role provided) — highest priority
      2. General qa_pairs table (in-memory Jaccard scoring cache)

    Uses pre-tokenized in-memory cache: pure set math, no per-call tokenization on stored rows.
    Only the winning row's answer text is fetched from DB.
    """
    # Priority 1: role-specific prepared answers
    if user_role:
        role_result = find_prepared_answer(question, role=user_role)
        if role_result:
            return role_result
    norm_input = normalize_question(question)
    if not norm_input:
        return None

    input_toks = frozenset(_tokens(norm_input))
    if not input_toks:
        return None

    # ── Fast exact-match shortcut (O(1)) ──────────────────────────────────────
    # Checks norm_q and all alias norms. If found, skip the full Jaccard scan.
    # This guarantees an exact question match ALWAYS wins, regardless of how
    # boost math scores other high-hit-count entries in the sorted cache.
    with _lock:
        cache = _get_score_cache()
        _eidx = _exact_index

    _fast_exact_id = _eidx.get(norm_input) if _eidx else None

    best_score = 1.0 if _fast_exact_id is not None else 0.0
    best_id    = _fast_exact_id
    best_q_toks = frozenset()

    for (row_id, norm_q, q_toks, kw_toks, alias_entries) in cache:
        # Fast exit: already have a perfect match from exact-index or prior iteration
        if best_score >= 1.0:
            break
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
                # Score inversely weighted by stored length — prefers specific matches
                if input_toks <= q_toks:
                    boost_a = MATCH_THRESHOLD + 0.05 + (len(input_toks) / max(len(q_toks), 1)) * 0.15
                    score = max(score, boost_a)

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
                        boost_a = MATCH_THRESHOLD + 0.05 + (len(input_toks) / max(len(a_toks), 1)) * 0.15
                        a_score = max(a_score, boost_a)
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
            best_q_toks = q_toks  # capture winner inline — no second scan needed
            if best_score >= 1.0:
                break  # exact match, stop early

    if best_score < MATCH_THRESHOLD or best_id is None:
        return None

    # Short-query guard: queries with very few meaningful tokens get inflated scores via
    # keyword/alias boosts despite semantic mismatch.
    # e.g. "What is system load?" → only "load" token → falsely hits "Load Balancer" entries.
    # Exception: if ALL input tokens are contained in the winning stored question (Boost A),
    # the match is semantically valid even at lower score — the query is a subset of the topic.
    _all_contained = bool(best_q_toks) and input_toks <= best_q_toks

    if len(input_toks) <= 1 and best_score < 0.99:
        # Allow 1-token queries only when the token is confirmed inside a specific stored question
        # (Boost A fired: input_toks ⊆ stored_toks). Prevents generic words from false-matching.
        if not (_all_contained and len(best_q_toks) <= 3):
            return None
    if len(input_toks) == 2 and best_score < 0.80 and not _all_contained:
        return None  # 2-token query: require confidence unless tokens fully contained

    # Fetch only the winner's answer text + tags for company/role boost
    # Uses thread-local read connection — no open/close overhead on the hot path
    conn = _get_read_conn()
    row = conn.execute(
        "SELECT answer_theory, answer_coding, answer_humanized, company, role_tag, tags "
        "FROM qa_pairs WHERE id=?",
        (best_id,)
    ).fetchone()

    # Company / role_tag boost: +10% if entry matches user's company or role
    if row and (company or role_tag):
        _row_company = (row["company"] or "").lower()
        _row_role    = (row["role_tag"] or "").lower()
        _row_tags    = (row["tags"] or "").lower()
        _boost = 0.0
        if company and company.lower() in (_row_company + ' ' + _row_tags):
            _boost += 0.10
        if role_tag and role_tag.lower() in (_row_role + ' ' + _row_tags):
            _boost += 0.08
        if _boost:
            best_score = min(1.0, best_score + _boost)

    if not row:
        return None

    humanized  = (row["answer_humanized"] or "").strip()
    theory_ans = (row["answer_theory"]    or "").strip()
    coding_ans = (row["answer_coding"]    or "").strip()

    # For code requests return raw coding answer (code blocks must stay intact);
    # humanized is derived from theory text and should not replace actual code.
    if want_code and coding_ans:
        answer = coding_ans
    elif humanized:
        answer = humanized
    else:
        answer = theory_ans or coding_ans

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
           tags: str = "", company: str = "", role_tag: str = "") -> int:
    """Insert a new Q&A pair. Returns new row id."""
    norm = normalize_question(question)
    ts = _nowts()
    humanized = _build_humanized(answer_theory.strip(), answer_coding.strip())
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                """INSERT INTO qa_pairs
                   (question, normalized_q, answer_theory, answer_coding, answer_humanized,
                    type, keywords, aliases, tags, company, role_tag, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (question.strip(), norm,
                 answer_theory.strip(), answer_coding.strip(), humanized,
                 qa_type, keywords.strip(), aliases.strip(), tags.strip(),
                 company.strip(), role_tag.strip(), ts, ts)
            )
            conn.commit()
            new_id = cur.lastrowid
            # Incremental cache update: append without full rebuild
            # (only invalidate prepared_cache — role-specific table is unaffected by qa_pairs inserts)
            _append_to_cache(new_id, norm, keywords.strip(), aliases.strip())
            _prepared_cache.clear()
            return new_id
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
            new_humanized = _build_humanized(new_theory, new_coding)
            conn.execute(
                """UPDATE qa_pairs SET question=?, normalized_q=?, answer_theory=?,
                   answer_coding=?, answer_humanized=?, type=?, keywords=?, aliases=?,
                   tags=?, updated_at=? WHERE id=?""",
                (new_q, new_norm, new_theory, new_coding, new_humanized,
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
