"""SQLite storage for transcript files, events, and the FTS5 search index."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_CREATE_FILES = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  offset INTEGER NOT NULL
)
"""

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  file TEXT,
  byte_offset INTEGER,
  session_id TEXT,
  project TEXT,
  cwd TEXT,
  uuid TEXT,
  ts TEXT,
  role TEXT,
  is_summary INTEGER DEFAULT 0,
  kind TEXT DEFAULT 'chat',
  raw TEXT NOT NULL,
  text TEXT NOT NULL,
  UNIQUE(file, byte_offset)
)
"""

_CREATE_EVENTS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
  text,
  content='events',
  content_rowid='id',
  tokenize='porter unicode61'
)
"""

_CREATE_INSERT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
  INSERT INTO events_fts(rowid, text) VALUES (new.id, new.text);
END
"""

_CREATE_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
  INSERT INTO events_fts(events_fts, rowid, text)
  VALUES ('delete', old.id, old.text);
END
"""


class StoreError(Exception):
    """Raised when an on-disk index cannot be opened with this schema version."""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (or create) the index database, creating tables if needed."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        conn.close()
        raise StoreError(
            f"{path} has schema version {version}, but this build supports "
            f"{SCHEMA_VERSION}. Delete the index to rebuild it, or upgrade "
            f"sessionmem."
        )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_FILES)
    conn.execute(_CREATE_EVENTS)
    conn.execute(_CREATE_EVENTS_FTS)
    conn.execute(_CREATE_INSERT_TRIGGER)
    conn.execute(_CREATE_DELETE_TRIGGER)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn
