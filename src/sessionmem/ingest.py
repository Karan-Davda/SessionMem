"""Incrementally ingest Claude Code JSONL transcripts into the index."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sessionmem.parsing import event_kind, event_text, index_text

_STORED_TYPES = frozenset({"user", "assistant", "system"})


@dataclass
class IngestResult:
    """How many files were visited and how many events were added or skipped."""

    files: int
    added: int
    skipped: int


def ingest_file(
    conn: sqlite3.Connection, path: str | Path, project: str
) -> tuple[int, int]:
    """Ingest new complete lines from one JSONL file. Returns (added, skipped)."""
    file_path = str(Path(path).resolve())
    stored = conn.execute(
        "SELECT offset FROM files WHERE path = ?", (file_path,)
    ).fetchone()
    offset = stored[0] if stored else 0
    size = Path(file_path).stat().st_size
    if size < offset:
        conn.execute("DELETE FROM events WHERE file = ?", (file_path,))
        offset = 0

    added = 0
    skipped = 0
    with open(file_path, "rb") as handle:
        handle.seek(offset)
        while True:
            raw_bytes = handle.readline()
            if not raw_bytes:
                break
            if not raw_bytes.endswith(b"\n"):
                break
            raw = raw_bytes.decode("utf-8", errors="replace")
            if raw.endswith("\n"):
                raw = raw[:-1]
            if raw.endswith("\r"):
                raw = raw[:-1]
            line_end = handle.tell()
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                skipped += 1
                offset = line_end
                continue
            if not isinstance(event, dict):
                skipped += 1
                offset = line_end
                continue
            event_type = event.get("type")
            if event_type not in _STORED_TYPES:
                offset = line_end
                continue
            byte_offset = offset
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO events (
                  file, byte_offset, session_id, project, cwd, uuid, ts,
                  role, is_summary, kind, raw, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_path,
                    byte_offset,
                    event.get("sessionId"),
                    project,
                    event.get("cwd"),
                    event.get("uuid"),
                    event.get("timestamp"),
                    event_type,
                    1 if event.get("isCompactSummary") else 0,
                    event_kind(event),
                    raw,
                    index_text(event_text(event)),
                ),
            )
            if cursor.rowcount:
                added += 1
            offset = line_end

    conn.execute(
        """
        INSERT INTO files (path, project, offset)
        VALUES (?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          project = excluded.project,
          offset = excluded.offset
        """,
        (file_path, project, offset),
    )
    return added, skipped


def ingest_tree(conn: sqlite3.Connection, root: str | Path) -> IngestResult:
    """Ingest every *.jsonl file under root, one transaction per file."""
    root_path = Path(root).resolve()
    files = 0
    added = 0
    skipped = 0
    for path in sorted(root_path.rglob("*.jsonl")):
        relative = path.relative_to(root_path)
        project = relative.parts[0]
        with conn:
            file_added, file_skipped = ingest_file(conn, path, project)
        files += 1
        added += file_added
        skipped += file_skipped
    return IngestResult(files=files, added=added, skipped=skipped)
