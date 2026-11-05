"""Write a small fictional transcript set and index it for a local demo."""

from __future__ import annotations

import json
from pathlib import Path

from sessionmem.ingest import ingest_tree
from sessionmem.store import connect

_PROJECT = "-home-demo-acme-billing"
_CWD = "/home/demo/acme-billing"
_OCT_03 = "a1b2c3d4-1111-4111-8111-111111111111"
_OCT_09 = "e5f6a7b8-2222-4222-8222-222222222222"
_OCT_20 = "c9d0e1f2-3333-4333-8333-333333333333"


def _event(session_id: str, ts: str, role: str, text: str, event_uuid: str, *, tool: bool = False) -> dict:
    """One Claude Code transcript event. A tool result stays a user message."""
    content: str | list[dict[str, str]]
    if tool:
        content = [{"type": "tool_result", "content": text}]
    else:
        content = text
    return {
        "type": role,
        "sessionId": session_id,
        "timestamp": ts,
        "cwd": _CWD,
        "uuid": event_uuid,
        "message": {"content": content},
    }


# Ten events, three sessions. No names, emails, or machine paths.
_SESSIONS = (
    {
        "file": f"{_OCT_03}.jsonl",
        "events": (
            _event(
                _OCT_03,
                "2026-10-03T14:02:00Z",
                "user",
                "For the Acme billing prototype, use SQLite for the database.",
                "11111111-aaaa-4111-8111-000000000001",
            ),
            _event(
                _OCT_03,
                "2026-10-03T14:02:30Z",
                "assistant",
                "SQLite is fine for a prototype. We can revisit the database later.",
                "11111111-aaaa-4111-8111-000000000002",
            ),
            _event(
                _OCT_03,
                "2026-10-03T14:10:00Z",
                "user",
                "Invoices must never be sent automatically. A person approves every one first.",
                "11111111-aaaa-4111-8111-000000000003",
            ),
            _event(
                _OCT_03,
                "2026-10-03T14:10:30Z",
                "assistant",
                "Agreed. I'll add an approval gate. Invoices stay pending until a person approves.",
                "11111111-aaaa-4111-8111-000000000004",
            ),
        ),
    },
    {
        "file": f"{_OCT_09}.jsonl",
        "events": (
            _event(
                _OCT_09,
                "2026-10-09T09:30:00Z",
                "user",
                "The nightly import is failing. Two workers write to the database at once.",
                "22222222-bbbb-4222-8222-000000000001",
            ),
            _event(
                _OCT_09,
                "2026-10-09T09:32:00Z",
                "user",
                "ERROR: database is locked (SQLite) in import_worker.py line 88",
                "22222222-bbbb-4222-8222-000000000002",
                tool=True,
            ),
            _event(
                _OCT_09,
                "2026-10-09T09:33:00Z",
                "assistant",
                "SQLite locks the whole file during writes. I suggest moving the database to Postgres.",
                "22222222-bbbb-4222-8222-000000000003",
            ),
            _event(
                _OCT_09,
                "2026-10-09T09:40:00Z",
                "user",
                "OK, switch to Postgres. Keep the approval gate exactly as it is.",
                "22222222-bbbb-4222-8222-000000000004",
            ),
        ),
    },
    {
        "file": f"{_OCT_20}.jsonl",
        "events": (
            _event(
                _OCT_20,
                "2026-10-20T16:15:00Z",
                "user",
                "Customers want to search old invoices. What should we use?",
                "33333333-cccc-4333-8333-000000000001",
            ),
            _event(
                _OCT_20,
                "2026-10-20T16:16:00Z",
                "assistant",
                "Stay on Postgres and add full-text search. "
                "The approval gate also needs an audit log.",
                "33333333-cccc-4333-8333-000000000002",
            ),
        ),
    },
)


def build(directory: str | Path) -> Path:
    """Replace any demo under directory and return the new index path."""
    root = Path(directory)
    project = root / "projects" / _PROJECT
    project.mkdir(parents=True, exist_ok=True)
    projects = root / "projects"
    for old in projects.rglob("*.jsonl"):
        old.unlink()
    for spec in _SESSIONS:
        path = project / spec["file"]
        lines = [json.dumps(event) for event in spec["events"]]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    db_path = root / "index.db"
    for suffix in ("", "-wal", "-shm"):
        leftover = Path(str(db_path) + suffix)
        if leftover.is_file():
            leftover.unlink()
    conn = connect(db_path)
    try:
        ingest_tree(conn, projects)
    finally:
        conn.close()
    return db_path
