import os
import sqlite3
import tempfile
import unittest

from sessionmem.store import StoreError, connect


class TestConnect(unittest.TestCase):
    def test_connect_twice_on_the_same_file_does_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "index.db")
            conn = connect(db_path)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            conn.close()
            self.assertIn("files", tables)
            self.assertIn("events", tables)
            self.assertIn("events_fts", tables)

            conn = connect(db_path)
            conn.close()

    def test_unsupported_schema_version_raises_store_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "index.db")
            connect(db_path).close()

            raw = sqlite3.connect(db_path)
            raw.execute("PRAGMA user_version = 999")
            raw.commit()
            raw.close()

            with self.assertRaises(StoreError):
                connect(db_path)


class TestEventsFts(unittest.TestCase):
    def test_inserted_event_is_found_by_full_text_query_then_gone_after_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            conn.execute(
                "INSERT INTO events (file, byte_offset, raw, text) "
                "VALUES (?, ?, ?, ?)",
                (
                    "session.jsonl",
                    0,
                    '{"type":"user"}',
                    "uniquefts token about widgets",
                ),
            )
            conn.commit()

            found = conn.execute(
                "SELECT text FROM events_fts WHERE events_fts MATCH ?",
                ("uniquefts",),
            ).fetchall()
            self.assertEqual(len(found), 1)
            self.assertIn("widgets", found[0][0])

            conn.execute("DELETE FROM events")
            conn.commit()
            gone = conn.execute(
                "SELECT text FROM events_fts WHERE events_fts MATCH ?",
                ("uniquefts",),
            ).fetchall()
            self.assertEqual(gone, [])
            conn.close()
