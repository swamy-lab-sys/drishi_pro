"""
PostgreSQL backend adapter for qa_database.py

Uses pg8000.dbapi (pure Python) — no C extensions, no _ssl.so needed.
ssl_context=False disables TLS for localhost connections.

The PGConn wrapper makes pg8000 look identical to sqlite3 so qa_database.py
needs only two lines changed per connection function:

    import db_backend as _pgb
    if _pgb.is_pg(): return _pgb.get_conn()

Compatibility:
  row['key']          dict-key access          ✓
  row[0]              index access             ✓
  dict(row)           dict conversion          ✓
  conn.execute(q,p)   ? → %s translation       ✓
  conn.executemany    executemany              ✓
  conn.executescript  multi-stmt SQL           ✓  (splits on ;)
  PRAGMA WAL/sync     no-op                    ✓
  PRAGMA table_info   → information_schema     ✓  (row[1] = column name)
  cur.rowcount        rowcount                 ✓
  cur.lastrowid       last insert SERIAL id    ✓  (via lastval())
  cur.__iter__        for row in cursor        ✓
  conn.row_factory=…  no-op (always CompatRow) ✓
"""

import os
import re
import threading
from typing import List, Optional
from urllib.parse import urlparse

_DATABASE_URL: str = os.environ.get('DATABASE_URL', '')

if not _DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set.\n"
        "Add it to .env:  DATABASE_URL=postgresql://drishi:drishi@localhost:5434/drishi\n"
        "Start PostgreSQL: docker run -d --name drishi-pg "
        "-e POSTGRES_DB=drishi -e POSTGRES_USER=drishi -e POSTGRES_PASSWORD=drishi "
        "-p 5434:5432 postgres:14"
    )

# Parse connection params once at import
try:
    p = urlparse(_DATABASE_URL)
    _PG_CFG = {
        'host': p.hostname or 'localhost',
        'port': p.port or 5432,
        'database': p.path.lstrip('/') or 'drishi',
        'user': p.username or 'drishi',
        'password': p.password or '',
    }
except Exception as _e:
    raise RuntimeError(f"Invalid DATABASE_URL: {_DATABASE_URL!r} — {_e}") from _e

try:
    import pg8000.dbapi  # noqa
except ImportError as _e:
    raise RuntimeError("pg8000 is not installed. Run: pip install pg8000") from _e

# ── Simple connection pool ────────────────────────────────────────────────────

_pool_lock = threading.Lock()
_idle_conns: list = []
_MAX_IDLE = 10
_tls = threading.local()


def _new_raw_conn():
    """Open a new pg8000 dbapi connection without SSL."""
    import pg8000.dbapi as pg
    conn = pg.connect(
        host=_PG_CFG['host'],
        port=_PG_CFG['port'],
        user=_PG_CFG['user'],
        password=_PG_CFG['password'],
        database=_PG_CFG['database'],
        ssl_context=False,
    )
    conn.autocommit = False
    return conn


def _get_raw_conn():
    with _pool_lock:
        if _idle_conns:
            raw = _idle_conns.pop()
            try:
                raw.cursor().execute("SELECT 1")
                return raw
            except Exception:
                pass  # Stale — create new
    return _new_raw_conn()


def _return_raw_conn(raw):
    with _pool_lock:
        if len(_idle_conns) < _MAX_IDLE:
            try:
                raw.rollback()
                _idle_conns.append(raw)
                return
            except Exception:
                pass
    try:
        raw.close()
    except Exception:
        pass


# ── CompatRow ─────────────────────────────────────────────────────────────────

class CompatRow:
    """Row with both index access (row[0]) and key access (row['col'])."""
    __slots__ = ('_d', '_keys')

    def __init__(self, d: dict, keys: list):
        self._d = d
        self._keys = keys

    def __getitem__(self, item):
        if isinstance(item, int):
            return self._d[self._keys[item]]
        return self._d[item]

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __contains__(self, item):
        return item in self._d

    def keys(self):
        return self._keys

    def values(self):
        return [self._d[k] for k in self._keys]

    def items(self):
        return [(k, self._d[k]) for k in self._keys]

    def __iter__(self):
        # Makes dict(row) work: dict() calls keys() then row[key]
        return iter(self._keys)

    def __len__(self):
        return len(self._d)

    def __repr__(self):
        return f'CompatRow({self._d!r})'


# ── CompatCursor ──────────────────────────────────────────────────────────────

class CompatCursor:
    """Cursor-like wrapper that returns CompatRow objects and supports iteration."""

    def __init__(self, rows: List[CompatRow], rowcount: int = -1,
                 last_id: Optional[int] = None):
        self._rows = rows
        self._pos = 0
        self._rowcount = rowcount
        self._last_id = last_id

    def fetchone(self) -> Optional[CompatRow]:
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self) -> List[CompatRow]:
        remaining = self._rows[self._pos:]
        self._pos = len(self._rows)
        return remaining

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    @property
    def rowcount(self) -> int:
        return self._rowcount

    @property
    def lastrowid(self) -> Optional[int]:
        return self._last_id


# ── SQL helpers ───────────────────────────────────────────────────────────────

_PRAGMA_NOOP_RE = re.compile(
    r'PRAGMA\s+(journal_mode|synchronous|foreign_keys|cache_size|temp_store)',
    re.IGNORECASE,
)
_PRAGMA_TABLE_RE = re.compile(
    r'PRAGMA\s+table_info\s*\(\s*(\w+)\s*\)',
    re.IGNORECASE,
)
_AUTOINCREMENT_RE = re.compile(
    r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', re.IGNORECASE,
)
_COLLATE_NOCASE_RE = re.compile(r'\s+COLLATE\s+NOCASE', re.IGNORECASE)


def _handle_pragma(sql: str):
    """Returns (pg_sql_or_None, is_noop)."""
    stripped = sql.strip()
    if _PRAGMA_NOOP_RE.match(stripped):
        return None, True
    m = _PRAGMA_TABLE_RE.match(stripped)
    if m:
        tbl = m.group(1)
        # row[1] = column_name — matches SQLite's PRAGMA table_info layout
        pg_sql = (
            "SELECT '' AS cid, column_name AS name, data_type AS type, "
            "0 AS notnull, '' AS dflt_value, 0 AS pk "
            "FROM information_schema.columns "
            f"WHERE table_schema = 'public' AND table_name = '{tbl}' "
            "ORDER BY ordinal_position"
        )
        return pg_sql, False
    return stripped, False


def _translate_q(sql: str) -> str:
    """Replace ? bind placeholders with %s (pg8000 dbapi uses %s)."""
    result: list = []
    in_str = False
    i = 0
    while i < len(sql):
        c = sql[i]
        if in_str:
            result.append(c)
            if c == "'" and (i + 1 >= len(sql) or sql[i + 1] != "'"):
                in_str = False
            elif c == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                result.append(sql[i + 1])
                i += 2
                continue
        elif c == "'":
            in_str = True
            result.append(c)
        elif c == '?':
            result.append('%s')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def _adapt_create(stmt: str) -> str:
    stmt = _AUTOINCREMENT_RE.sub('SERIAL PRIMARY KEY', stmt)
    stmt = _COLLATE_NOCASE_RE.sub('', stmt)
    return stmt


def _exec(cur, sql: str, params=None):
    """Execute SQL on a dbapi cursor, returning (rows, rowcount, last_id)."""
    sql = sql.strip()

    if sql.upper().startswith('PRAGMA'):
        pg_sql, is_noop = _handle_pragma(sql)
        if is_noop:
            return [], 0, None
        sql = pg_sql
        params = None

    sql = _translate_q(sql)
    cur.execute(sql, params or ())

    rows = []
    if cur.description:
        keys = [d[0] for d in cur.description]
        for raw_row in cur.fetchall():
            rows.append(CompatRow(dict(zip(keys, raw_row)), keys))

    last_id = None
    if sql.lstrip().upper().startswith('INSERT'):
        try:
            cur.execute("SELECT lastval()")
            r = cur.fetchone()
            last_id = r[0] if r else None
        except Exception:
            pass

    return rows, cur.rowcount, last_id


# ── PGConn ────────────────────────────────────────────────────────────────────

class PGConn:
    """Drop-in replacement for sqlite3.Connection backed by pg8000.dbapi."""

    def __init__(self, raw, persistent: bool = False):
        self._raw = raw
        self._persistent = persistent

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _):
        pass  # No-op — PGConn always uses CompatRow

    def execute(self, sql: str, params=None) -> CompatCursor:
        cur = self._raw.cursor()
        try:
            rows, rowcount, last_id = _exec(cur, sql, params)
        except Exception:
            try:
                self._raw.rollback()
            except Exception:
                pass
            raise
        return CompatCursor(rows, rowcount, last_id)

    def executemany(self, sql: str, seq) -> CompatCursor:
        sql = _translate_q(sql)
        cur = self._raw.cursor()
        total = 0
        try:
            for params in seq:
                cur.execute(sql, params)
                total += 1
        except Exception:
            try:
                self._raw.rollback()
            except Exception:
                pass
            raise
        return CompatCursor([], total)

    def executescript(self, sql: str):
        """Execute semicolon-separated statements (best-effort)."""
        cur = self._raw.cursor()
        for stmt in sql.split(';'):
            stmt = stmt.strip()
            if not stmt:
                continue
            if stmt.upper().startswith('PRAGMA'):
                continue
            stmt = _adapt_create(stmt)
            stmt = _translate_q(stmt)
            try:
                cur.execute(stmt)
                self._raw.commit()
            except Exception as e:
                try:
                    self._raw.rollback()
                except Exception:
                    pass
                # Silently skip "already exists" — executescript is idempotent
                err = str(e).lower()
                if any(k in err for k in ('already exists', 'duplicate', 'relation')):
                    pass  # expected on re-init

    def commit(self):
        try:
            self._raw.commit()
        except Exception:
            pass

    def rollback(self):
        try:
            self._raw.rollback()
        except Exception:
            pass

    def close(self):
        if self._persistent:
            return
        _return_raw_conn(self._raw)


# ── Public API ────────────────────────────────────────────────────────────────

def is_pg() -> bool:
    """Always True — PostgreSQL is the only supported backend."""
    return True


def get_conn() -> PGConn:
    """Get a pooled connection. Call .close() when done."""
    return PGConn(_get_raw_conn())


def get_read_conn() -> PGConn:
    """Thread-local persistent read connection. Never call .close()."""
    holder = getattr(_tls, 'pg_read_conn', None)
    if holder is not None:
        try:
            holder._raw.cursor().execute("SELECT 1")
            return holder
        except Exception:
            _tls.pg_read_conn = None
    raw = _new_raw_conn()
    raw.autocommit = True
    pg = PGConn(raw, persistent=True)
    _tls.pg_read_conn = pg
    return pg
