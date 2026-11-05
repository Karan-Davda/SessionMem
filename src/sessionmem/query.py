"""Query the index: search, timeline, and session messages."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

_EVENT_COLUMNS = (
    "id, file, session_id, uuid, ts, role, is_summary, kind, text"
)

# Same uuid collapses to one row (the first ingested). Missing uuids stay distinct.
_DEDUP_GROUP = """
CASE
  WHEN uuid IS NULL OR uuid = '' THEN 'row:' || id
  ELSE uuid
END
"""


@dataclass(frozen=True)
class Event:
    """One event as shown in search, timeline, and session views."""

    id: int
    file: str | None
    session_id: str | None
    uuid: str | None
    ts: str | None
    role: str | None
    is_summary: int
    kind: str
    text: str


def search(conn: sqlite3.Connection, query: str) -> list[Event]:
    """Return ranked FTS hits, one per uuid. Events with no uuid are not merged."""
    sql = f"""
        SELECT {_EVENT_COLUMNS}
        FROM events
        WHERE id IN (
          SELECT MIN(e.id)
          FROM events e
          JOIN events_fts ON events_fts.rowid = e.id
          WHERE events_fts MATCH ?
          GROUP BY {_DEDUP_GROUP}
        )
        ORDER BY id
        """
    rows = conn.execute(sql, (query,)).fetchall()
    return [_to_event(row) for row in rows]


def timeline(conn: sqlite3.Connection) -> list[Event]:
    """Return events in time order, counting each uuid once."""
    sql = f"""
        SELECT {_EVENT_COLUMNS}
        FROM events
        WHERE id IN (
          SELECT MIN(id)
          FROM events
          GROUP BY {_DEDUP_GROUP}
        )
        ORDER BY ts ASC, id ASC
        """
    return [_to_event(row) for row in conn.execute(sql).fetchall()]


def session_messages(conn: sqlite3.Connection, session_id: str) -> list[Event]:
    """Return messages for a session, one per uuid."""
    sql = f"""
        SELECT {_EVENT_COLUMNS}
        FROM events
        WHERE session_id = ?
          AND id IN (
            SELECT MIN(id)
            FROM events
            GROUP BY {_DEDUP_GROUP}
          )
        ORDER BY ts ASC, id ASC
        """
    return [_to_event(row) for row in conn.execute(sql, (session_id,)).fetchall()]


def _to_event(row: tuple) -> Event:
    return Event(*row)
