import json
import os
import tempfile
import unittest

from sessionmem.ingest import ingest_file
from sessionmem.query import search, session_messages, timeline
from sessionmem.store import connect


def _write_jsonl(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")


def _user_event(text, **fields):
    event = {
        "type": "user",
        "message": {"role": "user", "content": text},
        "sessionId": "sess-1",
        "timestamp": "2026-01-01T00:00:00.000Z",
    }
    event.update(fields)
    return event


class TestUuidDedup(unittest.TestCase):
    def test_search_returns_one_hit_for_duplicate_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = "dup-uuid-1111"
            token = "shareduniquealpha"
            a = os.path.join(tmp, "a.jsonl")
            b = os.path.join(tmp, "b.jsonl")
            _write_jsonl(a, [_user_event(token, uuid=shared)])
            _write_jsonl(b, [_user_event(token, uuid=shared)])
            conn = connect(os.path.join(tmp, "index.db"))
            ingest_file(conn, a, "demo")
            ingest_file(conn, b, "demo")

            stored = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            hits = search(conn, token)
            conn.close()

            self.assertEqual(stored, 2)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].uuid, shared)

    def test_timeline_counts_duplicate_uuid_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = "dup-uuid-1111"
            token = "shareduniquealpha"
            a = os.path.join(tmp, "a.jsonl")
            b = os.path.join(tmp, "b.jsonl")
            _write_jsonl(a, [_user_event(token, uuid=shared)])
            _write_jsonl(b, [_user_event(token, uuid=shared)])
            conn = connect(os.path.join(tmp, "index.db"))
            ingest_file(conn, a, "demo")
            ingest_file(conn, b, "demo")

            stored = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            events = timeline(conn)
            conn.close()

            self.assertEqual(stored, 2)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].uuid, shared)

    def test_session_messages_returns_duplicate_uuid_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = "dup-uuid-1111"
            token = "shareduniquealpha"
            a = os.path.join(tmp, "a.jsonl")
            b = os.path.join(tmp, "b.jsonl")
            _write_jsonl(a, [_user_event(token, uuid=shared)])
            _write_jsonl(b, [_user_event(token, uuid=shared)])
            conn = connect(os.path.join(tmp, "index.db"))
            ingest_file(conn, a, "demo")
            ingest_file(conn, b, "demo")

            stored = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            messages = session_messages(conn, "sess-1")
            conn.close()

            self.assertEqual(stored, 2)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].uuid, shared)

    def test_events_without_uuid_are_never_treated_as_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = "plainuniquebeta"
            a = os.path.join(tmp, "a.jsonl")
            b = os.path.join(tmp, "b.jsonl")
            _write_jsonl(a, [_user_event(token)])
            _write_jsonl(b, [_user_event(token)])
            conn = connect(os.path.join(tmp, "index.db"))
            ingest_file(conn, a, "demo")
            ingest_file(conn, b, "demo")

            stored = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(stored, 2)
            self.assertEqual(len(search(conn, token)), 2)
            self.assertEqual(len(timeline(conn)), 2)
            self.assertEqual(len(session_messages(conn, "sess-1")), 2)
            conn.close()
