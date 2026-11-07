"""
Persistence layer — a single SQLite database.

SQLite fits a personal study app well: zero setup, one file under ./data,
transactional, and fast enough that every query here completes in well under
a millisecond. All access goes through the helpers below so SQL never gets
assembled from user input (every value is a bound parameter).

JSON-shaped columns are stored as TEXT and decoded on the way out.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

from app.config import settings

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS notebooks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT 'Untitled notebook',
    title_auto    INTEGER NOT NULL DEFAULT 1,   -- 1 while the AI may still rename it
    emoji         TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',
    questions     TEXT NOT NULL DEFAULT '[]',   -- suggested questions (JSON list)
    guide_status  TEXT NOT NULL DEFAULT 'idle',  -- idle | processing | ready | error
    guide_error   TEXT NOT NULL DEFAULT '',
    chat_config   TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    opened_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id             TEXT PRIMARY KEY,
    notebook_id    TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,     -- youtube | video | audio | pdf | docx | text | website | paste | note
    title          TEXT NOT NULL,
    url            TEXT NOT NULL DEFAULT '',
    file_path      TEXT NOT NULL DEFAULT '',
    mime           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'queued',  -- queued | processing | ready | error
    status_message TEXT NOT NULL DEFAULT '',
    error          TEXT NOT NULL DEFAULT '',
    warnings       TEXT NOT NULL DEFAULT '[]',
    meta           TEXT NOT NULL DEFAULT '{}',
    guide          TEXT NOT NULL DEFAULT '{}',
    content        TEXT NOT NULL DEFAULT '',
    char_count     INTEGER NOT NULL DEFAULT 0,
    selected       INTEGER NOT NULL DEFAULT 1,
    position       INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_nb ON sources(notebook_id, position);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    notebook_id TEXT NOT NULL,
    ord         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    start_sec   REAL,
    end_sec     REAL,
    page        INTEGER,
    label       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id, ord);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,       -- user | assistant
    content     TEXT NOT NULL DEFAULT '',
    thoughts    TEXT NOT NULL DEFAULT '',
    citations   TEXT NOT NULL DEFAULT '{}',
    followups   TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'done',  -- done | stopped | error
    feedback    INTEGER NOT NULL DEFAULT 0,
    meta        TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_nb ON messages(notebook_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    id             TEXT PRIMARY KEY,
    notebook_id    TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    type           TEXT NOT NULL,
    title          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'processing',  -- processing | ready | error
    status_message TEXT NOT NULL DEFAULT '',
    error          TEXT NOT NULL DEFAULT '',
    options        TEXT NOT NULL DEFAULT '{}',
    content        TEXT NOT NULL DEFAULT '{}',
    citations      TEXT NOT NULL DEFAULT '{}',
    source_ids     TEXT NOT NULL DEFAULT '[]',
    file_path      TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_nb ON artifacts(notebook_id, created_at);

CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    title       TEXT NOT NULL DEFAULT 'New Note',
    content     TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'manual',  -- manual | saved_response
    citations   TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    origin_type TEXT NOT NULL DEFAULT '',   -- message | artifact | note | source
    origin_id   TEXT NOT NULL DEFAULT '',
    origin_title TEXT NOT NULL DEFAULT '',
    comment     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS flashcard_reviews (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    card_index  INTEGER NOT NULL,
    status      TEXT NOT NULL,   -- understood | review
    updated_at  REAL NOT NULL,
    PRIMARY KEY (artifact_id, card_index)
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id          TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    notebook_id TEXT NOT NULL,
    score       INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    answers     TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    day         TEXT NOT NULL,
    notebook_id TEXT NOT NULL,
    kind        TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, notebook_id, kind)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns holding JSON, per table, decoded on read.
_JSON_COLUMNS: dict[str, tuple[str, ...]] = {
    "notebooks": ("questions", "chat_config"),
    "sources": ("warnings", "meta", "guide"),
    "messages": ("citations", "followups", "meta"),
    "artifacts": ("options", "content", "citations", "source_ids"),
    "notes": ("citations",),
    "quiz_attempts": ("answers",),
}

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def new_id(prefix: str = "") -> str:
    """Short, URL-safe, unguessable identifier."""
    return f"{prefix}{secrets.token_urlsafe(9)}".replace("-", "x").replace("_", "y")


def now() -> float:
    return time.time()


def _connect() -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(str(settings.db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
    )
    return conn


def get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect()
        return _conn


def close() -> None:
    """Close the shared connection (used by tests to point at a fresh file)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


@contextmanager
def transaction():
    conn = get_conn()
    with _lock:
        conn.execute("BEGIN")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def _decode(table: str, row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    for col in _JSON_COLUMNS.get(table, ()):
        if col in data and isinstance(data[col], str):
            try:
                data[col] = json.loads(data[col]) if data[col] else None
            except (ValueError, TypeError):
                data[col] = None
    return data


def _encode(table: str, values: dict[str, Any]) -> dict[str, Any]:
    out = {}
    json_cols = _JSON_COLUMNS.get(table, ())
    for key, value in values.items():
        if key in json_cols and not isinstance(value, str):
            out[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            out[key] = int(value)
        else:
            out[key] = value
    return out


# Identifiers are never user input, but they are still checked against this
# set so a typo can't turn into an injectable string.
_TABLES = frozenset(_JSON_COLUMNS) | {"chunks", "bookmarks", "flashcard_reviews", "activity", "app_settings"}


def _check_ident(table: str, columns: Iterable[str]) -> None:
    if table not in _TABLES:
        raise ValueError(f"unknown table {table}")
    for col in columns:
        if not col.replace("_", "").isalnum():
            raise ValueError(f"bad column {col}")


def query(table: str, sql: str, params: tuple | list = ()) -> list[dict]:
    conn = get_conn()
    with _lock:
        rows = conn.execute(sql, params).fetchall()
    return [_decode(table, r) for r in rows]


def query_one(table: str, sql: str, params: tuple | list = ()) -> dict | None:
    conn = get_conn()
    with _lock:
        row = conn.execute(sql, params).fetchone()
    return _decode(table, row)


def execute(sql: str, params: tuple | list = ()) -> int:
    conn = get_conn()
    with _lock:
        cur = conn.execute(sql, params)
        return cur.rowcount


def insert(table: str, values: dict[str, Any]) -> None:
    values = _encode(table, values)
    _check_ident(table, values)
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))


def update(table: str, row_id: str, values: dict[str, Any], *, id_col: str = "id") -> int:
    if not values:
        return 0
    values = _encode(table, values)
    _check_ident(table, list(values) + [id_col])
    assignments = ", ".join(f"{k} = ?" for k in values)
    return execute(
        f"UPDATE {table} SET {assignments} WHERE {id_col} = ?", tuple(values.values()) + (row_id,)
    )


def get(table: str, row_id: str) -> dict | None:
    _check_ident(table, ())
    return query_one(table, f"SELECT * FROM {table} WHERE id = ?", (row_id,))


def delete(table: str, row_id: str) -> int:
    _check_ident(table, ())
    return execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


# ── Settings ─────────────────────────────────────────────────────

def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("app_settings", "SELECT value FROM app_settings WHERE key = ?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO app_settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


# ── Activity log (for streaks / progress) ────────────────────────

def log_activity(notebook_id: str, kind: str) -> None:
    day = time.strftime("%Y-%m-%d", time.localtime())
    execute(
        "INSERT INTO activity(day, notebook_id, kind, count) VALUES (?, ?, ?, 1) "
        "ON CONFLICT(day, notebook_id, kind) DO UPDATE SET count = count + 1",
        (day, notebook_id, kind),
    )
