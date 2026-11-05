import contextlib
import importlib
import io
import json
import os
import re
import shlex
import sqlite3
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


    def test_include_summaries_returns_the_compaction_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "transcripts")
            db = os.path.join(tmp, "index.db")
            _write_jsonl(
                os.path.join(root, "billing", "session.jsonl"),
                [
                    _event(
                        "quillword in the chat",
                        session_id="sess-quill",
                        ts="2026-06-01T00:00:00Z",
                    ),
                    {
                        "type": "assistant",
                        "isCompactSummary": True,
                        "sessionId": "sess-quill",
                        "timestamp": "2026-06-01T01:00:00Z",
                        "message": {"content": "quillword in the summary"},
                    },
                ],
            )
            self.assertEqual(_run(["ingest", "--db", db, root])[0], 0)
            hidden_code, hidden, hidden_err = _run(["search", "--db", db, "quillword"])
            shown_code, shown, shown_err = _run(
                ["search", "--db", db, "--include-summaries", "quillword"]
            )
            hidden_hits = [line for line in hidden.splitlines() if "quillword" in line]
            shown_hits = [line for line in shown.splitlines() if "quillword" in line]

            self.assertEqual(hidden_code, 0, hidden_err)
            self.assertEqual(shown_code, 0, shown_err)
            self.assertEqual(len(hidden_hits), 1)
            self.assertEqual(len(shown_hits), 2)


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


_DEMO_COMMANDS = {"ingest", "search", "timeline", "show", "stats", "demo"}
_PROJECT = "-home-demo-acme-billing"


@contextlib.contextmanager
def _isolated_home(path):
    """Point HOME at a temp folder so demo never touches the real home."""
    saved = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ["HOME"] = path
    os.environ["USERPROFILE"] = path
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _scratch():
    home = os.path.realpath(os.path.expanduser("~"))
    tmp = os.path.realpath("/tmp")
    if os.path.isdir(tmp) and tmp != home and not tmp.startswith(home + os.sep):
        return tmp
    return None


def _event_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()


def _example_commands(output):
    commands = []
    for line in output.splitlines():
        if "sessionmem" not in line:
            continue
        try:
            parts = shlex.split(line.strip())
        except ValueError:
            continue
        if "sessionmem" not in parts:
            continue
        argv = parts[parts.index("sessionmem") + 1 :]
        if argv and argv[0] in _DEMO_COMMANDS:
            commands.append(argv)
    return commands


class TestCliDemo(unittest.TestCase):
    def test_demo_dir_creates_index_and_second_run_keeps_stats(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            home = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(home)
            db = os.path.join(directory, "index.db")
            with _isolated_home(home):
                code, _out, err = _run(["demo", "--dir", directory])
                self.assertEqual(code, 0, err)
                self.assertTrue(os.path.isfile(db))
                stats_code, stats, stats_err = _run(["stats", "--db", db])
                self.assertEqual(stats_code, 0, stats_err)
                count = _event_count(db)
                self.assertEqual(count, 10)
                _assert_count(self, stats, "events", 10)
                _assert_count(self, stats, "sessions", 3)
                _assert_count(self, stats, "files", 3)
                code, _out, err = _run(["demo", "--dir", directory])
                self.assertEqual(code, 0, err)
                again_code, again, again_err = _run(["stats", "--db", db])
            self.assertEqual(again_code, 0, again_err)
            self.assertEqual(again, stats)
            self.assertEqual(_event_count(db), count)

    def test_timeline_sqlite_and_postgres_each_return_two_sessions(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            home = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(home)
            db = os.path.join(directory, "index.db")
            with _isolated_home(home):
                code, _out, err = _run(["demo", "--dir", directory])
                self.assertEqual(code, 0, err)
                sqlite_dates = self._timeline_dates(db, "sqlite")
                postgres_dates = self._timeline_dates(db, "postgres")
            self.assertEqual(sqlite_dates, ["2026-10-03", "2026-10-09"])
            self.assertEqual(postgres_dates, ["2026-10-09", "2026-10-20"])

    def _timeline_dates(self, db, query):
        code, out, err = _run(["timeline", "--db", db, query])
        self.assertEqual(code, 0, err)
        headers = [line for line in out.splitlines() if line.startswith("session ")]
        self.assertEqual(len(headers), 2, out)
        dates = []
        for header in headers:
            self.assertIn(_PROJECT, header)
            match = re.search(r"\| (\d{4}-\d{2}-\d{2}) to ", header)
            self.assertIsNotNone(match, header)
            dates.append(match.group(1))
        return dates

    def test_search_approval_gate_hits_every_session(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            home = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(home)
            db = os.path.join(directory, "index.db")
            with _isolated_home(home):
                self.assertEqual(_run(["demo", "--dir", directory])[0], 0)
                code, out, err = _run(["search", "--db", db, "approval gate"])
            self.assertEqual(code, 0, err)
            lines = [line.split() for line in out.splitlines() if line.strip()]
            self.assertEqual({parts[1] for parts in lines}, {"a1b2c3d4", "e5f6a7b8", "c9d0e1f2"}, out)
            self.assertEqual({parts[2] for parts in lines}, {"user", "assistant"}, out)

    def test_error_is_hidden_until_include_tools(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            home = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(home)
            db = os.path.join(directory, "index.db")
            with _isolated_home(home):
                self.assertEqual(_run(["demo", "--dir", directory])[0], 0)
                hidden_code, hidden, hidden_err = _run(["search", "--db", db, "ERROR"])
                shown_code, shown, shown_err = _run(
                    ["search", "--db", db, "--include-tools", "ERROR"]
                )
            self.assertEqual(hidden_code, 0, hidden_err)
            self.assertEqual(hidden.strip(), "No matches.")
            self.assertEqual(shown_code, 0, shown_err)
            self.assertIn("database is locked", shown)

    def test_show_first_session_prints_four_messages_starting_with_sqlite(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            home = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(home)
            db = os.path.join(directory, "index.db")
            with _isolated_home(home):
                self.assertEqual(_run(["demo", "--dir", directory])[0], 0)
                code, out, err = _run(["show", "--db", db, "a1b2c3d4"])
            self.assertEqual(code, 0, err)
            lines = [line for line in out.splitlines() if line.strip()]
            self.assertEqual(len(lines), 4, out)
            self.assertIn("sqlite", lines[0].lower())

    def test_demo_output_lists_commands_that_run(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            home = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(home)
            with _isolated_home(home):
                code, out, err = _run(["demo", "--dir", directory])
                self.assertEqual(code, 0, err)
                commands = _example_commands(out)
                self.assertTrue(any(argv[0] == "timeline" and "sqlite" in argv for argv in commands), out)
                self.assertTrue(any(argv[0] == "timeline" and "postgres" in argv for argv in commands), out)
                self.assertTrue(any(argv[0] == "show" and "a1b2c3d4" in argv for argv in commands), out)
                rendered = []
                for argv in commands:
                    command_code, command_out, command_err = _run(argv)
                    self.assertEqual(command_code, 0, f"{argv}\n{command_err}")
                    rendered.append(command_out)
            self.assertRegex("\n".join(rendered), r"2026-10-03|approval|session ")

    def test_default_dir_follows_home_when_the_command_runs(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            early = os.path.join(tmp, "early")
            late = os.path.join(tmp, "late")
            os.makedirs(early)
            os.makedirs(late)
            with _isolated_home(early):
                import sessionmem.demo
                import sessionmem.cli

                importlib.reload(sessionmem.demo)
                importlib.reload(sessionmem.cli)
            with _isolated_home(late):
                code, out, err = _run(["demo"])
            late_db = os.path.join(late, ".sessionmem", "demo", "index.db")
            early_db = os.path.join(early, ".sessionmem", "demo", "index.db")
            self.assertEqual(code, 0, err + out)
            self.assertTrue(os.path.isfile(late_db))
            self.assertFalse(os.path.exists(early_db))

    def test_demo_output_omits_the_machine_identity(self):
        from tests.test_demo import _assert_anonymous, _identity

        home, _user, _names = _identity()
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            folder = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(folder)
            with _isolated_home(folder):
                code, out, err = _run(["demo", "--dir", directory])
            self.assertEqual(code, 0, err)
            _assert_anonymous(self, out + err, [tmp, directory, folder])
            self.assertNotIn(home, (out + err).replace(tmp, ""))
