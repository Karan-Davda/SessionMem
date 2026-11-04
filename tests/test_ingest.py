import json
import os
import tempfile
import unittest

from sessionmem.ingest import IngestResult, ingest_file, ingest_tree
from sessionmem.store import connect


def _write_jsonl(path, lines):
    """Write JSONL text. Items that are dicts are serialized; strs are raw lines."""
    chunks = []
    for line in lines:
        if isinstance(line, dict):
            chunks.append(json.dumps(line))
        else:
            chunks.append(line)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(chunks))
        if chunks:
            handle.write("\n")


def _user_event(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


class TestIngestFile(unittest.TestCase):
    def test_adds_conversation_events_and_ignores_queue_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            _write_jsonl(
                path,
                [
                    _user_event("hello from alice"),
                    {
                        "type": "assistant",
                        "message": {
                            "content": [{"type": "text", "text": "hi alice"}]
                        },
                    },
                    {"type": "queue-operation", "operation": "dequeue"},
                ],
            )
            conn = connect(os.path.join(tmp, "index.db"))
            added, skipped = ingest_file(conn, path, "demo")
            roles = [
                row[0] for row in conn.execute("SELECT role FROM events").fetchall()
            ]
            conn.close()

            self.assertEqual(added, 2)
            self.assertEqual(skipped, 0)
            self.assertCountEqual(roles, ["user", "assistant"])
            self.assertNotIn("queue-operation", roles)

    def test_running_ingest_twice_adds_nothing_the_second_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            _write_jsonl(path, [_user_event("stable event")])
            conn = connect(os.path.join(tmp, "index.db"))
            first_added, _ = ingest_file(conn, path, "demo")
            second_added, second_skipped = ingest_file(conn, path, "demo")
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.close()

            self.assertEqual(first_added, 1)
            self.assertEqual(second_added, 0)
            self.assertEqual(second_skipped, 0)
            self.assertEqual(count, 1)

    def test_appended_lines_are_picked_up_on_the_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            _write_jsonl(path, [_user_event("first uniquealpha")])
            conn = connect(os.path.join(tmp, "index.db"))
            ingest_file(conn, path, "demo")

            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(_user_event("second uniquebeta")) + "\n")

            added, skipped = ingest_file(conn, path, "demo")
            texts = [row[0] for row in conn.execute("SELECT text FROM events")]
            conn.close()

            self.assertEqual(added, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(len(texts), 2)
            self.assertTrue(any("uniquealpha" in text for text in texts))
            self.assertTrue(any("uniquebeta" in text for text in texts))

    def test_half_written_last_line_is_left_until_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            complete = json.dumps(_user_event("completeone")) + "\n"
            partial = json.dumps(_user_event("completetwo"))  # no trailing newline
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(complete + partial)

            conn = connect(os.path.join(tmp, "index.db"))
            added, skipped = ingest_file(conn, path, "demo")
            texts = [row[0] for row in conn.execute("SELECT text FROM events")]
            self.assertEqual(added, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(len(texts), 1)
            self.assertIn("completeone", texts[0])
            self.assertFalse(any("completetwo" in text for text in texts))

            with open(path, "a", encoding="utf-8") as handle:
                handle.write("\n")

            added, skipped = ingest_file(conn, path, "demo")
            texts = [row[0] for row in conn.execute("SELECT text FROM events")]
            conn.close()
            self.assertEqual(added, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(len(texts), 2)
            self.assertTrue(any("completetwo" in text for text in texts))

    def test_file_rewritten_shorter_is_reindexed_without_stale_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            _write_jsonl(
                path,
                [
                    _user_event("oldalpha token"),
                    _user_event("oldbeta token"),
                ],
            )
            conn = connect(os.path.join(tmp, "index.db"))
            ingest_file(conn, path, "demo")

            _write_jsonl(path, [_user_event("newgamma token")])
            added, skipped = ingest_file(conn, path, "demo")

            stale = conn.execute(
                "SELECT text FROM events_fts WHERE events_fts MATCH ?",
                ("oldalpha",),
            ).fetchall()
            fresh = conn.execute(
                "SELECT text FROM events_fts WHERE events_fts MATCH ?",
                ("newgamma",),
            ).fetchall()
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.close()

            self.assertEqual(added, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(stale, [])
            self.assertEqual(len(fresh), 1)
            self.assertEqual(count, 1)

    def test_invalid_json_lines_are_skipped_and_do_not_stop_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            _write_jsonl(
                path,
                [
                    _user_event("goodone token"),
                    "{not-json",
                    _user_event("goodtwo token"),
                ],
            )
            conn = connect(os.path.join(tmp, "index.db"))
            added, skipped = ingest_file(conn, path, "demo")
            texts = [row[0] for row in conn.execute("SELECT text FROM events")]
            conn.close()

            self.assertEqual(added, 2)
            self.assertEqual(skipped, 1)
            self.assertEqual(len(texts), 2)
            self.assertTrue(any("goodone" in text for text in texts))
            self.assertTrue(any("goodtwo" in text for text in texts))

    def test_raw_line_is_stored_exactly_while_searchable_text_masks_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            secret = "sk-ant-api03-" + ("A" * 48)
            event = _user_event("use " + secret + " please")
            raw_line = json.dumps(event)
            _write_jsonl(path, [event])
            conn = connect(os.path.join(tmp, "index.db"))
            ingest_file(conn, path, "demo")
            raw, text = conn.execute("SELECT raw, text FROM events").fetchone()
            conn.close()

            self.assertEqual(raw, raw_line)
            self.assertIn(secret, raw)
            self.assertNotIn(secret, text)
            self.assertIn("[REDACTED]", text)
            self.assertIn("please", text)

    def test_is_compact_summary_sets_summary_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            _write_jsonl(
                path,
                [
                    _user_event("ordinary chat"),
                    {
                        "type": "assistant",
                        "isCompactSummary": True,
                        "message": {"content": "compact summary about widgets"},
                    },
                ],
            )
            conn = connect(os.path.join(tmp, "index.db"))
            ingest_file(conn, path, "demo")
            rows = {
                row[0]: row[1]
                for row in conn.execute("SELECT role, is_summary FROM events")
            }
            conn.close()

            self.assertEqual(rows["user"], 0)
            self.assertEqual(rows["assistant"], 1)

    def test_system_event_is_stored_with_role_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            _write_jsonl(
                path,
                [{"type": "system", "content": "Session started"}],
            )
            conn = connect(os.path.join(tmp, "index.db"))
            added, skipped = ingest_file(conn, path, "demo")
            role, text = conn.execute("SELECT role, text FROM events").fetchone()
            conn.close()

            self.assertEqual(added, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(role, "system")
            self.assertEqual(text, "Session started")


class TestIngestTree(unittest.TestCase):
    def test_project_name_is_the_first_folder_under_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            path = os.path.join(root, "demo-project", "session.jsonl")
            _write_jsonl(path, [_user_event("tree event")])
            conn = connect(os.path.join(tmp, "index.db"))
            result = ingest_tree(conn, root)
            project = conn.execute("SELECT project FROM files").fetchone()[0]
            conn.close()

            self.assertIsInstance(result, IngestResult)
            self.assertEqual(result.files, 1)
            self.assertEqual(result.added, 1)
            self.assertEqual(result.skipped, 0)
            self.assertEqual(project, "demo-project")
