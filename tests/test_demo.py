import contextlib
import getpass
import json
import os
import pwd
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

_PROJECT = "-home-demo-acme-billing"
_SESSION_DATES = {
    "a1b2c3d4": "2026-10-03",
    "e5f6a7b8": "2026-10-09",
    "c9d0e1f2": "2026-10-20",
}
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)


@contextlib.contextmanager
def _isolated_home(path):
    """Point HOME at a temp folder for the duration of a demo call."""
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
    """Prefer /tmp so a demo directory is not created inside the real home."""
    home = os.path.realpath(os.path.expanduser("~"))
    tmp = os.path.realpath("/tmp")
    if os.path.isdir(tmp) and tmp != home and not tmp.startswith(home + os.sep):
        return tmp
    return None


def _identity():
    """Name, account, and home path of the machine running the tests."""
    home = os.path.realpath(os.path.expanduser("~"))
    user = getpass.getuser()
    names = []
    try:
        full_name = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
        names = [part for part in full_name.split() if len(part) >= 3]
    except (AttributeError, KeyError, OSError):
        names = []
    return home, user, names


def _events(directory):
    project = Path(directory) / "projects" / _PROJECT
    paths = sorted(project.glob("*.jsonl"))
    events = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    events.append(json.loads(line))
    return paths, events


def _blob(event):
    return json.dumps(event, ensure_ascii=False)


def _is_tool_event(event):
    message = event.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(block, dict) and block.get("type") in {"tool_use", "tool_result"}
        for block in content
    )


def _sessions(events):
    grouped = {}
    for event in events:
        grouped.setdefault(event.get("sessionId"), []).append(event)
    return grouped


def _event_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()


def _assert_anonymous(testcase, corpus, allowed_paths):
    cleaned = corpus
    for path in sorted((item for item in allowed_paths if item), key=len, reverse=True):
        cleaned = cleaned.replace(path, "")
        cleaned = cleaned.replace(os.path.realpath(path), "")
    home, user, names = _identity()
    testcase.assertNotIn(home, cleaned)
    if len(user) >= 3:
        testcase.assertIsNone(
            re.search(rf"\b{re.escape(user)}\b", cleaned, re.IGNORECASE)
        )
    for name in names:
        testcase.assertIsNone(
            re.search(rf"\b{re.escape(name)}\b", cleaned, re.IGNORECASE)
        )
    testcase.assertIsNone(
        re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", cleaned)
    )


class TestDemoBuild(unittest.TestCase):
    def test_build_returns_index_and_writes_fake_project(self):
        home, _user, _names = _identity()
        real_demo = os.path.join(home, ".sessionmem", "demo")
        already_there = os.path.exists(real_demo)
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            folder = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(folder)
            with _isolated_home(folder):
                from sessionmem.demo import build

                db = build(directory)
            self.assertEqual(
                os.path.abspath(os.fspath(db)),
                os.path.abspath(os.path.join(directory, "index.db")),
            )
            self.assertTrue(os.path.isfile(db))
            if not already_there:
                self.assertFalse(os.path.exists(real_demo))
            paths, events = _events(directory)
            self.assertTrue(paths)
            self.assertTrue(events)

    def test_three_sessions_tell_the_billing_story(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            folder = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(folder)
            with _isolated_home(folder):
                from sessionmem.demo import build

                build(directory)
            paths, events = _events(directory)
            grouped = _sessions(events)
            self.assertEqual(len(events), 10)
            self.assertEqual(len(grouped), 3)
            self.assertEqual(
                {path.name for path in paths},
                {f"{session_id}.jsonl" for session_id in grouped},
            )
            by_prefix = {}
            for session_id, session_events in grouped.items():
                self.assertRegex(session_id, _UUID_RE)
                by_prefix[session_id[:8].lower()] = session_events
            self.assertEqual(set(by_prefix), set(_SESSION_DATES))

            by_date = {}
            for prefix, session_events in by_prefix.items():
                dates = {(event.get("timestamp") or "")[:10] for event in session_events}
                self.assertEqual(dates, {_SESSION_DATES[prefix]}, prefix)
                by_date[_SESSION_DATES[prefix]] = session_events
            self.assertEqual(len(by_prefix["a1b2c3d4"]), 4)
            earliest = min(
                by_prefix["a1b2c3d4"],
                key=lambda event: event.get("timestamp") or "",
            )
            self.assertIn("sqlite", _blob(earliest).lower())

            chat = [event for event in events if not _is_tool_event(event)]
            self.assertEqual(len(chat), 9)
            self.assertEqual({event.get("type") for event in chat}, {"user", "assistant"})
            october_3 = "\n".join(_blob(event) for event in by_date["2026-10-03"])
            october_20 = "\n".join(_blob(event) for event in by_date["2026-10-20"])
            self.assertIn("sqlite", october_3.lower())
            self.assertIn("postgres", october_20.lower())

    def test_show_prints_messages_with_time_role_and_text(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            folder = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(folder)
            with _isolated_home(folder):
                from sessionmem.demo import build
                from tests.test_cli import _run

                db = build(directory)
                code, out, err = _run(["show", "--db", os.fspath(db), "a1b2c3d4"])
            self.assertEqual(code, 0, err)
            self.assertEqual(
                [line for line in out.splitlines() if line.strip()],
                [
                    "[2026-10-03 14:02] user: For the Acme billing prototype, use SQLite for the database.",
                    "[2026-10-03 14:02] assistant: SQLite is fine for a prototype. We can revisit the database later.",
                    "[2026-10-03 14:10] user: Invoices must never be sent automatically. A person approves every one first.",
                    "[2026-10-03 14:10] assistant: Agreed. I'll add an approval gate. Invoices stay pending until a person approves.",
                ],
            )

    def test_second_build_does_not_duplicate_rows(self):
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            folder = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(folder)
            with _isolated_home(folder):
                from sessionmem.demo import build

                first = build(directory)
                count = _event_count(first)
                self.assertEqual(count, 10)
                second = build(directory)
            self.assertEqual(os.path.abspath(os.fspath(second)), os.path.abspath(os.fspath(first)))
            self.assertEqual(_event_count(second), count)

    def test_transcripts_omit_the_machine_identity(self):
        home, _user, _names = _identity()
        with tempfile.TemporaryDirectory(dir=_scratch()) as tmp:
            folder = os.path.join(tmp, "home")
            directory = os.path.join(tmp, "demo")
            os.makedirs(folder)
            with _isolated_home(folder):
                from sessionmem.demo import build

                db = build(directory)
            paths, events = _events(directory)
            pieces = [path.read_text(encoding="utf-8") for path in paths]
            pieces.extend(_blob(event) for event in events)
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "SELECT session_id, project, cwd, text, raw FROM events"
                )
                pieces.extend("\n".join("" if value is None else str(value) for value in row) for row in rows)
            finally:
                conn.close()
            _assert_anonymous(self, "\n".join(pieces), [tmp, directory, folder])
