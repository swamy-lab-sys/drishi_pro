#!/usr/bin/env python3
"""
One-time migration: copy all data from SQLite (~/.drishi/qa_pairs.db)
into a PostgreSQL database, then verify row counts match.

Usage:
    # 1. Start PostgreSQL and create the database:
    #    sudo service postgresql start
    #    sudo -u postgres createdb drishi
    #    sudo -u postgres psql -c "CREATE USER drishi WITH PASSWORD 'drishi';"
    #    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE drishi TO drishi;"
    #
    # 2. Set DATABASE_URL and run:
    #    DATABASE_URL=postgresql://drishi:drishi@localhost/drishi python3 migrate_to_postgres.py
    #
    # 3. Add to .env:
    #    DATABASE_URL=postgresql://drishi:drishi@localhost/drishi
"""

import os
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2-binary not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if not DATABASE_URL:
    print("ERROR: Set DATABASE_URL environment variable first.")
    print("  export DATABASE_URL=postgresql://drishi:drishi@localhost/drishi")
    sys.exit(1)

# ── Find SQLite DB ─────────────────────────────────────────────────────────────
try:
    sys.path.insert(0, str(Path(__file__).parent))
    import config
    SQLITE_PATH = Path(config.ANSWERS_DIR).expanduser() / "qa_pairs.db"
except Exception:
    SQLITE_PATH = Path.home() / ".drishi" / "qa_pairs.db"

if not SQLITE_PATH.exists():
    print(f"ERROR: SQLite database not found at {SQLITE_PATH}")
    sys.exit(1)

print(f"Source: {SQLITE_PATH}")
print(f"Target: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
print()

# ── Connect ────────────────────────────────────────────────────────────────────
sq = sqlite3.connect(str(SQLITE_PATH))
sq.row_factory = sqlite3.Row
pg = psycopg2.connect(DATABASE_URL)
pg.autocommit = False
cur = pg.cursor()

# ── Create PostgreSQL schema ───────────────────────────────────────────────────
print("Creating PostgreSQL schema...")

cur.execute("""
CREATE TABLE IF NOT EXISTS qa_pairs (
    id               SERIAL PRIMARY KEY,
    question         TEXT    NOT NULL,
    normalized_q     TEXT    NOT NULL DEFAULT '',
    answer_theory    TEXT    DEFAULT '',
    answer_coding    TEXT    DEFAULT '',
    answer_humanized TEXT    DEFAULT '',
    type             TEXT    NOT NULL DEFAULT 'theory',
    keywords         TEXT    DEFAULT '',
    aliases          TEXT    DEFAULT '',
    tags             TEXT    DEFAULT '',
    company          TEXT    DEFAULT '',
    role_tag         TEXT    DEFAULT '',
    created_at       TEXT    NOT NULL DEFAULT '',
    updated_at       TEXT    NOT NULL DEFAULT '',
    hit_count        INTEGER NOT NULL DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id                  SERIAL PRIMARY KEY,
    name                TEXT    NOT NULL,
    role                TEXT    NOT NULL DEFAULT '',
    experience_years    INTEGER NOT NULL DEFAULT 0,
    resume_text         TEXT    DEFAULT '',
    job_description     TEXT    DEFAULT '',
    self_introduction   TEXT    DEFAULT '',
    key_skills          TEXT    DEFAULT '',
    custom_instructions TEXT    DEFAULT '',
    domain              TEXT    DEFAULT '',
    resume_file         TEXT    DEFAULT '',
    resume_summary      TEXT    DEFAULT '',
    resume_path         TEXT    DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT '',
    updated_at          TEXT    DEFAULT ''
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS questions (
    id               SERIAL PRIMARY KEY,
    role             TEXT    NOT NULL DEFAULT '',
    question         TEXT    NOT NULL,
    prepared_answer  TEXT    NOT NULL DEFAULT ''
)
""")

cur.execute("""
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
    created_at       TEXT    NOT NULL DEFAULT '',
    last_seen        TEXT    DEFAULT '',
    total_questions  INTEGER DEFAULT 0,
    total_llm_hits   INTEGER DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS usage_log (
    id          SERIAL PRIMARY KEY,
    token       TEXT    NOT NULL DEFAULT '',
    question    TEXT    NOT NULL DEFAULT '',
    source      TEXT    DEFAULT 'db',
    answer_ms   INTEGER DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT ''
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS stt_corrections (
    id         SERIAL PRIMARY KEY,
    wrong      TEXT    NOT NULL,
    right_text TEXT    NOT NULL DEFAULT '',
    source     TEXT    DEFAULT 'auto',
    hit_count  INTEGER DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT '',
    UNIQUE(wrong)
)
""")

# Indexes
for idx in [
    "CREATE INDEX IF NOT EXISTS idx_type ON qa_pairs(type)",
    "CREATE INDEX IF NOT EXISTS idx_normalized_q ON qa_pairs(normalized_q)",
    "CREATE INDEX IF NOT EXISTS idx_hit_count ON qa_pairs(hit_count DESC)",
    "CREATE INDEX IF NOT EXISTS idx_tags ON qa_pairs(tags)",
    "CREATE INDEX IF NOT EXISTS idx_company ON qa_pairs(company)",
    "CREATE INDEX IF NOT EXISTS idx_role_tag ON qa_pairs(role_tag)",
    "CREATE INDEX IF NOT EXISTS idx_questions_role ON questions(role)",
    "CREATE INDEX IF NOT EXISTS idx_usage_token ON usage_log(token)",
    "CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_log(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_stt_wrong ON stt_corrections(wrong)",
]:
    try:
        cur.execute(idx)
    except Exception:
        pg.rollback()

pg.commit()
print("  Schema created.")
print()


# ── Migration helper ───────────────────────────────────────────────────────────

def _get_cols(sq_cur, table: str):
    """Return list of column names that exist in SQLite table."""
    info = sq_cur.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in info]


def _migrate_table(table: str, identity_col: str = 'id'):
    """Copy all rows from SQLite table to PostgreSQL."""
    sq_cur = sq.cursor()
    cols = _get_cols(sq_cur, table)
    if not cols:
        print(f"  {table}: table not found in SQLite, skipping.")
        return 0

    rows = sq_cur.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        print(f"  {table}: 0 rows, nothing to migrate.")
        return 0

    # Get PG columns (may differ slightly)
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (table,)
    )
    pg_cols = [r[0] for r in cur.fetchall()]

    # Only insert columns that exist in both
    common = [c for c in cols if c in pg_cols]
    ph = ', '.join(['%s'] * len(common))
    col_list = ', '.join(common)

    inserted = 0
    for row in rows:
        vals = tuple(row[c] for c in common)
        try:
            cur.execute(
                f"INSERT INTO {table} ({col_list}) VALUES ({ph}) "
                f"ON CONFLICT DO NOTHING",
                vals,
            )
            inserted += 1
        except Exception as e:
            pg.rollback()
            print(f"  WARNING: skipped a row in {table}: {e}")

    pg.commit()

    # Reset sequence if table has an integer primary key
    if identity_col in pg_cols:
        try:
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{identity_col}'), "
                f"COALESCE(MAX({identity_col}), 0) + 1, false) FROM {table}"
            )
            pg.commit()
        except Exception:
            pg.rollback()

    print(f"  {table}: {inserted} rows migrated.")
    return inserted


# ── Migrate all tables ─────────────────────────────────────────────────────────
print("Migrating tables...")

total = 0
total += _migrate_table('qa_pairs')
total += _migrate_table('users')
total += _migrate_table('questions')
total += _migrate_table('ext_users', identity_col='token')  # TEXT PK, no sequence
total += _migrate_table('usage_log')
total += _migrate_table('stt_corrections')

print()
print(f"Migration complete. Total rows: {total}")

# ── Verify ─────────────────────────────────────────────────────────────────────
print()
print("Verification (SQLite vs PostgreSQL row counts):")
for tbl in ['qa_pairs', 'users', 'questions', 'ext_users', 'usage_log', 'stt_corrections']:
    try:
        sq_count = sq.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    except Exception:
        sq_count = 'N/A'
    try:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        pg_count = cur.fetchone()[0]
    except Exception:
        pg_count = 'N/A'
    match = "✓" if sq_count == pg_count else "✗ MISMATCH"
    print(f"  {tbl:<20} SQLite={sq_count}  PG={pg_count}  {match}")

sq.close()
pg.close()

print()
print("Done! Add this to your .env:")
print(f"  DATABASE_URL={DATABASE_URL}")
