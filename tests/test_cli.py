import contextlib
import io
import json
import os
import tempfile
import unittest


def _run(argv):
    """Call main(argv) and capture both streams. main must return an int."""
    from sessionmem.cli import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def _write_jsonl(path, events):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def _event(text, *, session_id, ts, role="user"):
    return {
        "type": role,
        "sessionId": session_id,
        "timestamp": ts,
        "message": {"content": text},
    }


def _assert_count(test, text, label, count):
    pattern = rf"(?i)(?:{label}\D{{0,20}}{count}\b|\b{count}\D{{0,20}}{label})"
    test.assertRegex(text, pattern)


class TestCliIngest(unittest.TestCase):
    def test_ingest_prints_counts_and_second_run_adds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "session.jsonl"),
                [
                    _event("first note", session_id="sess-1", ts="2026-01-01T00:00:00Z"),
                    _event("second note", session_id="sess-1", ts="2026-01-02T00:00:00Z"),
                ],
            )
            code, out, _err = _run(["ingest", "--db", db, root])
            self.assertEqual(code, 0)
            _assert_count(self, out, "files", 1)
            _assert_count(self, out, "added", 2)
            _assert_count(self, out, "skipped", 0)

            code, out, _err = _run(["ingest", "--db", db, root])
            self.assertEqual(code, 0)
            _assert_count(self, out, "added", 0)

    def test_ingest_without_folder_reads_claude_projects_under_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            _write_jsonl(
                os.path.join(home, ".claude", "projects", "billing", "session.jsonl"),
                [
                    _event(
                        "hometranscript quinoa",
                        session_id="sess-home",
                        ts="2026-01-04T00:00:00Z",
                    )
                ],
            )
            previous_home = os.environ.get("HOME")
            previous_profile = os.environ.get("USERPROFILE")
            os.environ["HOME"] = home
            os.environ["USERPROFILE"] = home
            try:
                code, out, err = _run(["ingest"])
                self.assertEqual(code, 0, err)
                _assert_count(self, out, "files", 1)
                _assert_count(self, out, "added", 1)
                self.assertNotIn("None", out)

                code, found, err = _run(["search", "quinoa"])
                self.assertEqual(code, 0, err)
                self.assertIn("hometranscript", found)
            finally:
                if previous_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = previous_home
                if previous_profile is None:
                    os.environ.pop("USERPROFILE", None)
                else:
                    os.environ["USERPROFILE"] = previous_profile


class TestCliSearch(unittest.TestCase):
    def test_search_prints_one_line_per_hit_with_snippet_on_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            session_id = "0123456789abcdef"
            _write_jsonl(
                os.path.join(root, "billing", "session.jsonl"),
                [
                    _event(
                        "before\nafter zebrafish",
                        session_id=session_id,
                        ts="2026-10-05T15:04:00Z",
                    )
                ],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            code, out, _err = _run(["search", "--db", db, "zebrafish"])
            lines = [line for line in out.splitlines() if line.strip()]

            self.assertEqual(code, 0)
            self.assertEqual(len(lines), 1)
            line = lines[0]
            self.assertIn("2026-10-05", line)
            self.assertIn("01234567", line)
            self.assertNotIn("89abcdef", line)
            self.assertIn("user", line)
            self.assertIn("billing", line)
            self.assertIn("zebrafish", line)
            self.assertNotIn("\n", line)

    def test_search_with_no_matches_prints_no_matches_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "session.jsonl"),
                [_event("nothing relevant", session_id="sess-1", ts="2026-01-01T00:00:00Z")],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            code, out, _err = _run(["search", "--db", db, "zebraabsent"])

            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), "No matches.")

    def test_search_with_only_punctuation_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            code, _out, err = _run(["search", "--db", db, "???"])

            self.assertEqual(code, 1)
            self.assertIn("no words", err.lower())

    def test_role_project_and_since_change_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "kept.jsonl"),
                [
                    _event(
                        "filterword keepuser",
                        session_id="sess-user",
                        ts="2026-10-05T00:00:00Z",
                        role="user",
                    ),
                    _event(
                        "filterword dropassistant",
                        session_id="sess-assistant",
                        ts="2026-10-05T01:00:00Z",
                        role="assistant",
                    ),
                    _event(
                        "filterword dropearly",
                        session_id="sess-early",
                        ts="2026-10-01T00:00:00Z",
                        role="user",
                    ),
                ],
            )
            _write_jsonl(
                os.path.join(root, "shipping", "other.jsonl"),
                [
                    _event(
                        "filterword dropshipping",
                        session_id="sess-ship",
                        ts="2026-10-05T02:00:00Z",
                        role="user",
                    )
                ],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            plain = _run(["search", "--db", db, "filterword"])[1]
            self.assertIn("dropassistant", plain)
            self.assertIn("dropshipping", plain)
            self.assertIn("dropearly", plain)

            by_role = _run(["search", "--db", db, "--role", "user", "filterword"])[1]
            self.assertIn("keepuser", by_role)
            self.assertNotIn("dropassistant", by_role)

            by_project = _run(
                ["search", "--db", db, "--project", "billing", "filterword"]
            )[1]
            self.assertIn("keepuser", by_project)
            self.assertNotIn("dropshipping", by_project)

            by_since = _run(
                ["search", "--db", db, "--since", "2026-10-05", "filterword"]
            )[1]
            self.assertIn("keepuser", by_since)
            self.assertNotIn("dropearly", by_since)

    def test_limit_and_short_option_return_two_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "session.jsonl"),
                [
                    _event(
                        f"limitword item {n}",
                        session_id="sess-limit",
                        ts=f"2026-07-0{n}T00:00:00Z",
                    )
                    for n in (1, 2, 3)
                ],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            for flag in ("--limit", "-n"):
                with self.subTest(flag=flag):
                    code, out, err = _run(["search", "--db", db, flag, "2", "limitword"])
                    hits = [line for line in out.splitlines() if "limitword" in line]
                    self.assertEqual(code, 0, err)
                    self.assertEqual(len(hits), 2)


class TestCliTimeline(unittest.TestCase):
    def test_timeline_prints_one_header_per_session_and_more_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "alpha.jsonl"),
                [
                    _event(
                        f"timelineword alpha {n}",
                        session_id="alpha-session-111",
                        ts=f"2026-02-0{n}T00:00:00Z",
                    )
                    for n in (1, 2, 3)
                ],
            )
            _write_jsonl(
                os.path.join(root, "billing", "beta.jsonl"),
                [
                    _event(
                        f"timelineword beta {n}",
                        session_id="beta-session-222",
                        ts=f"2026-03-0{n}T00:00:00Z",
                    )
                    for n in (1, 2)
                ],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            code, out, _err = _run(
                ["timeline", "--db", db, "--per-session", "1", "timelineword"]
            )
            lines = [line for line in out.splitlines() if line.strip()]

            self.assertEqual(code, 0)
            self.assertEqual(sum("alpha-se" in line for line in lines), 1)
            self.assertEqual(sum("beta-ses" in line for line in lines), 1)
            self.assertIn("... 2 more in this session", out)
            self.assertIn("... 1 more in this session", out)

    def test_hit_line_has_date_role_and_snippet_and_header_has_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "alpha.jsonl"),
                [
                    _event(
                        "rangeword first",
                        session_id="alpha-session-111",
                        ts="2026-04-01T00:00:00Z",
                        role="user",
                    ),
                    _event(
                        "rangeword second",
                        session_id="alpha-session-111",
                        ts="2026-04-09T00:00:00Z",
                        role="assistant",
                    ),
                ],
            )
            _write_jsonl(
                os.path.join(root, "billing", "beta.jsonl"),
                [
                    _event(
                        "rangeword only",
                        session_id="beta-session-222",
                        ts="2026-05-02T00:00:00Z",
                        role="user",
                    )
                ],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            code, out, err = _run(["timeline", "--db", db, "rangeword"])
            lines = [line for line in out.splitlines() if line.strip()]

            self.assertEqual(code, 0, err)
            self.assertEqual(
                lines[0],
                "session alpha-se | billing | 2026-04-01 to 2026-04-09 | 2 hits",
            )
            self.assertEqual(
                lines[3],
                "session beta-ses | billing | 2026-05-02 to 2026-05-02 | 1 hit",
            )
            hits = [lines[1], lines[2]]
            self.assertEqual(hits[0][: len("2026-04-01 user ")], "2026-04-01 user ")
            self.assertIn("first", hits[0])
            self.assertEqual(
                hits[1][: len("2026-04-09 assistant ")],
                "2026-04-09 assistant ",
            )
            self.assertIn("second", hits[1])


class TestCliShow(unittest.TestCase):
    def test_show_abbreviated_id_prints_session_in_time_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "session.jsonl"),
                [
                    _event(
                        "showword later",
                        session_id="0123456789abcdef",
                        ts="2026-04-02T00:00:00Z",
                    ),
                    _event(
                        "showword earlier",
                        session_id="0123456789abcdef",
                        ts="2026-04-01T00:00:00Z",
                    ),
                ],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            code, out, _err = _run(["show", "--db", db, "01234567"])

            self.assertEqual(code, 0)
            self.assertLess(out.index("showword earlier"), out.index("showword later"))

    def test_show_unknown_id_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "session.jsonl"),
                [_event("present", session_id="0123456789abcdef", ts="2026-04-01T00:00:00Z")],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            code, _out, err = _run(["show", "--db", db, "missing-session"])

            self.assertEqual(code, 1)
            self.assertIn("missing-session", err)

    def test_show_empty_id_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            code, _out, err = _run(["show", "--db", db, ""])

            self.assertEqual(code, 1)
            self.assertTrue(err.strip())

    def test_show_ambiguous_id_lists_candidates_and_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "one.jsonl"),
                [
                    _event(
                        "first candidate",
                        session_id="aaaa-1111-session",
                        ts="2026-05-01T00:00:00Z",
                    )
                ],
            )
            _write_jsonl(
                os.path.join(root, "billing", "two.jsonl"),
                [
                    _event(
                        "second candidate",
                        session_id="aaaa-2222-session",
                        ts="2026-05-02T00:00:00Z",
                    )
                ],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            code, _out, err = _run(["show", "--db", db, "aaaa"])

            self.assertEqual(code, 1)
            self.assertIn("aaaa-1111-session", err)
            self.assertIn("aaaa-2222-session", err)


class TestCliStats(unittest.TestCase):
    def test_stats_prints_counts_and_date_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "session.jsonl"),
                [
                    _event("one", session_id="sess-1", ts="2026-01-01T00:00:00Z"),
                    _event("two", session_id="sess-1", ts="2026-03-01T00:00:00Z"),
                ],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            code, out, _err = _run(["stats", "--db", db])

            self.assertEqual(code, 0)
            _assert_count(self, out, "events", 2)
            _assert_count(self, out, "sessions", 1)
            _assert_count(self, out, "files", 1)
            self.assertIn("2026-01-01", out)
            self.assertIn("2026-03-01", out)

    def test_empty_database_prints_no_events_indexed_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            code, out, err = _run(["stats", "--db", db])

            self.assertEqual(code, 0, err)
            self.assertEqual(out.strip(), "No events indexed yet. Run: sessionmem ingest.")
