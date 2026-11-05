"""Query the index: search, timeline, and session messages."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from sessionmem.parsing import KIND_CHAT

_EVENT_COLUMNS = (
    "id, file, project, session_id, uuid, ts, role, is_summary, kind, text"
)

# Same uuid collapses to one row (the first ingested). Missing uuids stay distinct.
_DEDUP_GROUP = """
CASE
  WHEN uuid IS NULL OR uuid = '' THEN 'row:' || id
  ELSE uuid
END
"""

# Matches one search word: letters, digits, underscores, apostrophes, and hyphens.
_FTS_WORD_RE = re.compile(r"[\w'-]+")


class QueryError(Exception):
    """Raised when a search string contains no searchable words."""


@dataclass(frozen=True)
class Filters:
    """Optional restrictions for search. Unset fields are not applied."""

    role: str | None = None
    project: str | None = None
    since: str | None = None
    include_tools: bool = False
    include_summaries: bool = False


@dataclass(frozen=True)
class Event:
    """One event as shown in search, timeline, and session views."""

    id: int
    file: str | None
    project: str | None
    session_id: str | None
    uuid: str | None
    ts: str | None
    role: str | None
    is_summary: int
    kind: str
    text: str
    snippet: str | None = None


@dataclass(frozen=True)
class SessionHits:
    """Matching hits for one session, oldest first."""

    session_id: str | None
    project: str | None
    hits: list[Event]


@dataclass(frozen=True)
class Stats:
    """Counts for the whole index.

    events counts each uuid once: the first ingested copy. Rows with no
    uuid each count as their own event. sessions is the number of distinct
    session ids among those counted events. files is the number of rows in
    the files table, so a file whose events are all duplicates of earlier
    uuids still counts. first and last are the earliest and latest
    timestamps among the counted events. A duplicate copy does not extend
    that range.
    """

    events: int
    sessions: int
    files: int
    first: str | None
    last: str | None


def to_fts_query(text: str) -> str:
    """Quote each word so punctuation and words like AND are matched literally."""
    words = _FTS_WORD_RE.findall(text or "")
    if not words:
        raise QueryError("Search query has no words to look up.")
    return " ".join(f'"{word}"' for word in words)


def search(
    conn: sqlite3.Connection,
    query: str,
    filters: Filters = Filters(),
    *,
    limit: int | None = 10,
) -> list[Event]:
    """Return bm25-ranked hits, one per uuid, with a highlighted snippet."""
    fts_query = to_fts_query(query)
    where, params = _search_filters(filters)
    where.append("events_fts MATCH ?")
    params.append(fts_query)
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ?"
        params.append(limit)
    sql = f"""
        WITH hits AS (
          SELECT e.id, e.file, e.project, e.session_id, e.uuid, e.ts, e.role,
                 e.is_summary, e.kind, e.text,
                 snippet(events_fts, 0, '«', '»', ' … ', 16) AS snippet,
                 bm25(events_fts) AS rank
          FROM events e
          JOIN events_fts ON events_fts.rowid = e.id
          WHERE {' AND '.join(where)}
        )
        SELECT id, file, project, session_id, uuid, ts, role, is_summary,
               kind, text, snippet
        FROM hits
        WHERE uuid IS NULL OR uuid = '' OR id IN (
          SELECT MIN(id) FROM hits
          WHERE uuid IS NOT NULL AND uuid != ''
          GROUP BY uuid
        )
        ORDER BY rank, id
        {limit_clause}
        """
    rows = conn.execute(sql, params).fetchall()
    return [_to_event(row) for row in rows]


def _search_filters(filters: Filters) -> tuple[list[str], list[object]]:
    """Build parameterized WHERE fragments. Values stay out of the SQL text."""
    clauses: list[str] = []
    params: list[object] = []
    if not filters.include_tools:
        clauses.append("e.kind = ?")
        params.append(KIND_CHAT)
    if not filters.include_summaries:
        clauses.append("e.is_summary = 0")
    if filters.role is not None:
        clauses.append("e.role = ?")
        params.append(filters.role)
    if filters.project is not None:
        clauses.append("instr(lower(e.project), lower(?)) > 0")
        params.append(filters.project)
    if filters.since is not None:
        clauses.append("e.ts >= ?")
        params.append(filters.since)
    return clauses, params


def timeline(
    conn: sqlite3.Connection,
    query: str,
    filters: Filters = Filters(),
) -> list[SessionHits]:
    """Group search hits by session, oldest hit first within each group."""
    grouped: dict[str | None, list[Event]] = {}
    for hit in search(conn, query, filters, limit=None):
        grouped.setdefault(hit.session_id, []).append(hit)
    sessions = []
    for session_id, hits in grouped.items():
        ordered = sorted(hits, key=lambda hit: (hit.ts or "", hit.id))
        sessions.append(
            SessionHits(
                session_id=session_id,
                project=ordered[0].project,
                hits=ordered,
            )
        )
    sessions.sort(key=lambda group: (group.hits[0].ts or "", group.hits[0].id))
    return sessions


def session_messages(conn: sqlite3.Connection, prefix: str) -> list[Event]:
    """Return one session's messages, oldest first, excluding summaries."""
    if prefix == "":
        return []
    sql = f"""
        SELECT {_EVENT_COLUMNS}
        FROM events
        WHERE substr(session_id, 1, length(?)) = ?
          AND is_summary = 0
          AND id IN (
            SELECT MIN(id)
            FROM events
            WHERE substr(session_id, 1, length(?)) = ?
              AND is_summary = 0
            GROUP BY {_DEDUP_GROUP}
          )
        ORDER BY ts ASC, id ASC
        """
    rows = conn.execute(sql, (prefix, prefix, prefix, prefix)).fetchall()
    return [_to_event(row) for row in rows]


def stats(conn: sqlite3.Connection) -> Stats:
    """Summarize the index using the deduped event set and the files table."""
    row = conn.execute(
        f"""
        WITH counted AS (
          SELECT session_id, ts
          FROM events
          WHERE id IN (
            SELECT MIN(id)
            FROM events
            GROUP BY {_DEDUP_GROUP}
          )
        )
        SELECT
          (SELECT COUNT(*) FROM counted),
          (SELECT COUNT(DISTINCT session_id) FROM counted),
          (SELECT COUNT(*) FROM files),
          (SELECT MIN(ts) FROM counted),
          (SELECT MAX(ts) FROM counted)
        """
    ).fetchone()
    return Stats(
        events=row[0],
        sessions=row[1],
        files=row[2],
        first=row[3],
        last=row[4],
    )


def _to_event(row: tuple) -> Event:
    return Event(*row)
