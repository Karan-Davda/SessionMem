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
            groups = timeline(conn, token)
            conn.close()

            self.assertEqual(stored, 2)
            self.assertEqual(len(groups), 1)
            self.assertEqual(len(groups[0].hits), 1)
            self.assertEqual(groups[0].hits[0].uuid, shared)

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
            groups = timeline(conn, token)
            self.assertEqual(len(groups), 1)
            self.assertEqual(len(groups[0].hits), 2)
            self.assertEqual(len(session_messages(conn, "sess-1")), 2)
            conn.close()


def _insert_event(conn, text, *, kind="chat", is_summary=0, offset=0):
    conn.execute(
        """
        INSERT INTO events (file, byte_offset, kind, is_summary, raw, text)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("made-up.jsonl", offset, kind, is_summary, text, text),
    )
    conn.commit()


class TestSearchBasics(unittest.TestCase):
    def test_apostrophe_and_operator_queries_do_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_event(conn, "I can't find the notes", offset=0)
            _insert_event(conn, "AND is just a word here", offset=1)
            for query in ("can't", "AND"):
                hits = search(conn, query)
                self.assertIsInstance(hits, list)
            conn.close()

    def test_repeated_word_ranks_above_a_single_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_event(conn, "one zephyrterm mention", offset=0)
            _insert_event(
                conn,
                "zephyrterm zephyrterm zephyrterm",
                offset=1,
            )
            hits = search(conn, "zephyrterm")
            conn.close()

            self.assertGreaterEqual(len(hits), 2)
            self.assertIn("zephyrterm zephyrterm zephyrterm", hits[0].text)

    def test_each_hit_has_a_snippet_marking_the_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_event(conn, "please find snippetword now", offset=0)
            hits = search(conn, "snippetword")
            conn.close()

            self.assertGreaterEqual(len(hits), 1)
            for hit in hits:
                self.assertIn("«snippetword»", hit.snippet)

    def test_tool_and_summary_events_are_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_event(conn, "chat about widgetword", kind="chat", offset=0)
            _insert_event(
                conn, "tool about widgetword", kind="tool", offset=1
            )
            _insert_event(
                conn,
                "summary about widgetword",
                kind="chat",
                is_summary=1,
                offset=2,
            )
            hits = search(conn, "widgetword")
            conn.close()

            texts = [hit.text for hit in hits]
            self.assertEqual(texts, ["chat about widgetword"])


def _insert_indexed(
    conn,
    text,
    *,
    offset,
    role="user",
    project=None,
    ts=None,
    kind="chat",
    is_summary=0,
):
    conn.execute(
        """
        INSERT INTO events (
          file, byte_offset, project, ts, role, kind, is_summary, raw, text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "made-up.jsonl",
            offset,
            project,
            ts,
            role,
            kind,
            is_summary,
            text,
            text,
        ),
    )
    conn.commit()


def _filtered_search(conn, query, *, limit=10, **fields):
    from sessionmem.query import Filters

    return search(conn, query, Filters(**fields), limit=limit)


class TestSearchFilters(unittest.TestCase):
    def test_role_user_returns_only_user_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_indexed(conn, "roleword from the user", role="user", offset=0)
            _insert_indexed(
                conn, "roleword from the assistant", role="assistant", offset=1
            )
            hits = _filtered_search(conn, "roleword", role="user")
            conn.close()

            self.assertTrue(hits)
            self.assertTrue(all(hit.role == "user" for hit in hits))
            self.assertTrue(any("from the user" in hit.text for hit in hits))
            self.assertFalse(any("assistant" in hit.text for hit in hits))

    def test_project_billing_matches_path_regardless_of_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_indexed(
                conn,
                "projectword inside acme",
                project="-home-demo-Acme-Billing",
                offset=0,
            )
            _insert_indexed(
                conn,
                "projectword inside shipping",
                project="-home-demo-Acme-Shipping",
                offset=1,
            )
            hits = _filtered_search(conn, "projectword", project="billing")
            conn.close()

            texts = [hit.text for hit in hits]
            self.assertEqual(texts, ["projectword inside acme"])

    def test_since_includes_that_date_and_excludes_earlier(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_indexed(
                conn,
                "sinceword on the day",
                ts="2026-10-05T12:00:00Z",
                offset=0,
            )
            _insert_indexed(
                conn,
                "sinceword the day before",
                ts="2026-10-04T18:00:00Z",
                offset=1,
            )
            hits = _filtered_search(conn, "sinceword", since="2026-10-05")
            conn.close()

            texts = [hit.text for hit in hits]
            self.assertIn("sinceword on the day", texts)
            self.assertNotIn("sinceword the day before", texts)

    def test_include_tools_returns_tool_output_and_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_indexed(conn, "toolword in chat", kind="chat", offset=0)
            _insert_indexed(
                conn, "toolword in tool output", kind="tool", offset=1
            )
            hits = _filtered_search(conn, "toolword", include_tools=True)
            conn.close()

            self.assertCountEqual(
                [hit.text for hit in hits],
                ["toolword in chat", "toolword in tool output"],
            )

    def test_include_summaries_returns_compaction_summaries_and_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_indexed(conn, "summaryword in chat", is_summary=0, offset=0)
            _insert_indexed(
                conn,
                "summaryword in compaction",
                is_summary=1,
                offset=1,
            )
            hits = _filtered_search(conn, "summaryword", include_summaries=True)
            conn.close()

            self.assertCountEqual(
                [hit.text for hit in hits],
                ["summaryword in chat", "summaryword in compaction"],
            )

    def test_limit_two_returns_at_most_two_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            for offset, text in enumerate(
                ("limitword one", "limitword two", "limitword three")
            ):
                _insert_indexed(conn, text, offset=offset)
            hits = _filtered_search(conn, "limitword", limit=2)
            conn.close()

            self.assertLessEqual(len(hits), 2)
            self.assertEqual(len(hits), 2)


def _insert_row(
    conn,
    text,
    *,
    offset,
    session_id,
    ts,
    file="made-up.jsonl",
    project=None,
    uuid=None,
    role="user",
    kind="chat",
    is_summary=0,
):
    conn.execute(
        """
        INSERT INTO events (
          file, byte_offset, session_id, project, uuid, ts, role,
          kind, is_summary, raw, text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file,
            offset,
            session_id,
            project,
            uuid,
            ts,
            role,
            kind,
            is_summary,
            text,
            text,
        ),
    )
    conn.commit()


class TestTimeline(unittest.TestCase):
    def test_word_in_three_sessions_groups_by_first_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "chronoword aaa",
                session_id="sess-aaa",
                project="acme",
                ts="2026-01-03T00:00:00Z",
                offset=0,
            )
            _insert_row(
                conn,
                "chronoword bbb",
                session_id="sess-bbb",
                project="billing",
                ts="2026-01-01T00:00:00Z",
                offset=1,
            )
            _insert_row(
                conn,
                "chronoword ccc",
                session_id="sess-ccc",
                project="shipping",
                ts="2026-01-02T00:00:00Z",
                offset=2,
            )
            groups = timeline(conn, "chronoword")
            conn.close()

            self.assertEqual(
                [group.session_id for group in groups],
                ["sess-bbb", "sess-ccc", "sess-aaa"],
            )
            self.assertEqual(
                [group.project for group in groups],
                ["billing", "shipping", "acme"],
            )

    def test_hits_inside_a_group_are_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "orderword later",
                session_id="sess-bbb",
                project="billing",
                ts="2026-05-09T00:00:00Z",
                offset=0,
            )
            _insert_row(
                conn,
                "orderword earlier",
                session_id="sess-bbb",
                project="billing",
                ts="2026-05-01T00:00:00Z",
                offset=1,
            )
            groups = timeline(conn, "orderword")
            conn.close()

            self.assertEqual(len(groups), 1)
            self.assertEqual(
                [hit.text for hit in groups[0].hits],
                ["orderword earlier", "orderword later"],
            )

    def test_tool_output_is_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "kindword chat",
                session_id="sess-chat",
                project="acme",
                ts="2026-01-01T00:00:00Z",
                kind="chat",
                offset=0,
            )
            _insert_row(
                conn,
                "kindword tool",
                session_id="sess-chat",
                project="acme",
                ts="2026-01-02T00:00:00Z",
                kind="tool",
                offset=1,
            )
            groups = timeline(conn, "kindword")
            conn.close()

            texts = [hit.text for group in groups for hit in group.hits]
            self.assertEqual(texts, ["kindword chat"])


class TestSessionMessages(unittest.TestCase):
    def test_abbreviated_session_id_returns_messages_in_time_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "prefixword later",
                session_id="0123456789abcdef",
                ts="2026-04-02T00:00:00Z",
                offset=0,
            )
            _insert_row(
                conn,
                "prefixword earlier",
                session_id="0123456789abcdef",
                ts="2026-04-01T00:00:00Z",
                offset=1,
            )
            messages = session_messages(conn, "01234567")
            conn.close()

            self.assertEqual(
                [message.text for message in messages],
                ["prefixword earlier", "prefixword later"],
            )

    def test_unknown_prefix_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "something else",
                session_id="0123456789abcdef",
                ts="2026-04-01T00:00:00Z",
                offset=0,
            )
            messages = session_messages(conn, "no-such-session")
            conn.close()

            self.assertEqual(messages, [])

    def test_empty_prefix_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "prefixword present",
                session_id="0123456789abcdef",
                ts="2026-04-01T00:00:00Z",
                offset=0,
            )
            messages = session_messages(conn, "")
            conn.close()

            self.assertEqual(messages, [])

    def test_compaction_summaries_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "keepword visible",
                session_id="sumsession123",
                ts="2026-04-01T00:00:00Z",
                is_summary=0,
                offset=0,
            )
            _insert_row(
                conn,
                "keepword hidden summary",
                session_id="sumsession123",
                ts="2026-04-02T00:00:00Z",
                is_summary=1,
                offset=1,
            )
            messages = session_messages(conn, "sumsession")
            conn.close()

            self.assertEqual([message.text for message in messages], ["keepword visible"])

    def test_uuid_copied_into_a_second_session_is_returned_for_that_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "copyword original",
                session_id="alpha-session-111",
                uuid="uuid-copied-1",
                ts="2026-01-01T00:00:00Z",
                offset=0,
            )
            _insert_row(
                conn,
                "copyword duplicate",
                session_id="beta-session-222",
                uuid="uuid-copied-1",
                ts="2026-02-01T00:00:00Z",
                file="other.jsonl",
                offset=0,
            )
            messages = session_messages(conn, "beta-session")
            conn.close()

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].uuid, "uuid-copied-1")
            self.assertIn("duplicate", messages[0].text)


class TestStats(unittest.TestCase):
    def test_counts_duplicate_uuids_once(self):
        """events counts each uuid once.

        A uuid stored in more than one row counts as a single event: the
        first ingested copy. Rows with no uuid each count as their own
        event. sessions is the number of distinct session ids among those
        counted events. files is the number of rows in the files table, so
        a file whose events are all duplicates of earlier uuids still
        counts. first and last are the earliest and latest timestamps among
        the counted events. A duplicate copy does not extend that range.
        """
        from sessionmem.query import stats

        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "kept copy",
                session_id="s1",
                uuid="u1",
                ts="2026-01-01T00:00:00Z",
                file="a.jsonl",
                offset=0,
            )
            _insert_row(
                conn,
                "duplicate copy",
                session_id="s2",
                uuid="u1",
                ts="2026-06-01T00:00:00Z",
                file="b.jsonl",
                offset=0,
            )
            _insert_row(
                conn,
                "other uuid",
                session_id="s2",
                uuid="u2",
                ts="2026-03-01T00:00:00Z",
                file="b.jsonl",
                offset=1,
            )
            _insert_row(
                conn,
                "no uuid",
                session_id="s1",
                uuid=None,
                ts="2026-02-01T00:00:00Z",
                file="c.jsonl",
                offset=0,
            )
            for path in ("a.jsonl", "b.jsonl", "c.jsonl"):
                _insert_file(conn, path)
            report = stats(conn)
            conn.close()

            self.assertEqual(report.events, 3)
            self.assertEqual(report.sessions, 2)
            self.assertEqual(report.files, 3)
            self.assertEqual(report.first, "2026-01-01T00:00:00Z")
            self.assertEqual(report.last, "2026-03-01T00:00:00Z")

    def test_file_of_only_duplicate_uuids_still_counts(self):
        from sessionmem.query import stats

        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(os.path.join(tmp, "index.db"))
            _insert_row(
                conn,
                "original",
                session_id="s1",
                uuid="u9",
                ts="2026-01-01T00:00:00Z",
                file="earlier.jsonl",
                offset=0,
            )
            _insert_row(
                conn,
                "same uuid again",
                session_id="s1",
                uuid="u9",
                ts="2026-08-01T00:00:00Z",
                file="only-dupes.jsonl",
                offset=0,
            )
            _insert_file(conn, "earlier.jsonl")
            _insert_file(conn, "only-dupes.jsonl")
            report = stats(conn)
            conn.close()

            self.assertEqual(report.events, 1)
            self.assertEqual(report.files, 2)
            self.assertEqual(report.last, "2026-01-01T00:00:00Z")


def _insert_file(conn, path, project="demo", offset=0):
    conn.execute(
        "INSERT INTO files (path, project, offset) VALUES (?, ?, ?)",
        (path, project, offset),
    )
    conn.commit()
