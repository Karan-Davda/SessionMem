"""Command-line interface for searching Claude Code session history."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sessionmem.ingest import ingest_tree
from sessionmem.query import (
    Filters,
    QueryError,
    matching_sessions,
    search,
    session_messages,
    stats,
    timeline,
)
from sessionmem.store import StoreError, connect

_SHORT_ID_LEN = 8


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run one command, and return a process exit code."""
    _prepare_stdout()
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None or code == 0:
            return 0
        return code if isinstance(code, int) else 1
    try:
        return _run(args)
    except QueryError as exc:
        return _error(str(exc))
    except StoreError as exc:
        return _error(str(exc))


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db",
        default=str(Path.home() / ".sessionmem" / "index.db"),
        help="path to the index database",
    )
    parser = argparse.ArgumentParser(prog="sessionmem")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", parents=[common])
    ingest.add_argument(
        "root",
        nargs="?",
        default=None,
        help="folder of Claude Code transcripts (default: ~/.claude/projects)",
    )

    search_cmd = commands.add_parser("search", parents=[common])
    search_cmd.add_argument("--role")
    search_cmd.add_argument("--project")
    search_cmd.add_argument("--since")
    search_cmd.add_argument("-n", "--limit", type=int, default=10)
    search_cmd.add_argument("query")

    timeline_cmd = commands.add_parser("timeline", parents=[common])
    timeline_cmd.add_argument("--per-session", type=int, default=5)
    timeline_cmd.add_argument("-n", "--limit", type=int, default=None)
    timeline_cmd.add_argument("query")

    show = commands.add_parser("show", parents=[common])
    show.add_argument("session_id", nargs="?", default="")

    commands.add_parser("stats", parents=[common])
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "ingest":
        root = _transcript_root(args.root)
        if not root.is_dir():
            return _error(f"transcript folder not found: {root}")
    db_path = Path(args.db).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        if args.command == "ingest":
            result = ingest_tree(conn, root)
            print(f"files {result.files}")
            print(f"added {result.added}")
            print(f"skipped {result.skipped}")
            return 0
        if args.command == "search":
            return _search(conn, args)
        if args.command == "timeline":
            return _timeline(conn, args)
        if args.command == "show":
            return _show(conn, args.session_id)
        if args.command == "stats":
            return _stats(conn)
    finally:
        conn.close()
    return _error(f"unknown command: {args.command}")


def _search(conn, args: argparse.Namespace) -> int:
    hits = search(
        conn,
        args.query,
        Filters(role=args.role, project=args.project, since=args.since),
        limit=args.limit,
    )
    if not hits:
        print("No matches.")
        return 0
    for hit in hits:
        snippet = _one_line(hit.snippet or "")
        print(
            f"{_date(hit.ts)} {_short_id(hit.session_id)} "
            f"{hit.role or ''} {hit.project or ''} {snippet}"
        )
    return 0


def _timeline(conn, args: argparse.Namespace) -> int:
    groups = timeline(conn, args.query)
    per_session = args.limit if args.limit is not None else args.per_session
    for group in groups:
        first = _date(group.hits[0].ts) if group.hits else ""
        last = _date(group.hits[-1].ts) if group.hits else ""
        print(
            f"{group.session_id or ''} {group.project or ''} "
            f"{first} {last} {len(group.hits)}".rstrip()
        )
        shown = group.hits[:per_session]
        for hit in shown:
            snippet = _one_line(hit.snippet or hit.text)
            print(f"{_date(hit.ts)} {hit.role or ''} {snippet}".rstrip())
        extra = len(group.hits) - len(shown)
        if extra:
            print(f"... {extra} more in this session")
    return 0


def _show(conn, prefix: str) -> int:
    if prefix == "":
        return _error("session id is empty")
    sessions = matching_sessions(conn, prefix)
    if not sessions:
        return _error(f"unknown session {prefix}")
    if len(sessions) > 1:
        print(
            f"error: {prefix} matches more than one session",
            file=sys.stderr,
        )
        for session_id in sessions:
            print(session_id, file=sys.stderr)
        return 1
    for message in session_messages(conn, prefix):
        print(_one_line(message.text))
    return 0


def _stats(conn) -> int:
    report = stats(conn)
    if report.events == 0:
        print("No events indexed yet.")
        return 0
    print(f"events {report.events}")
    print(f"sessions {report.sessions}")
    print(f"files {report.files}")
    if report.first or report.last:
        print(f"{_date(report.first)} {_date(report.last)}".strip())
    return 0


def _transcript_root(root: str | None) -> Path:
    """Pick the transcript folder when ingest runs, so HOME is current."""
    if root:
        return Path(root).expanduser()
    return Path.home() / ".claude" / "projects"


def _prepare_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(errors="replace")
    except (OSError, ValueError):
        return


def _one_line(text: str) -> str:
    """Collapse whitespace so a snippet stays on a single console line."""
    return " ".join(text.split())


def _short_id(session_id: str | None) -> str:
    if not session_id:
        return ""
    return session_id[:_SHORT_ID_LEN]


def _date(ts: str | None) -> str:
    if not ts:
        return ""
    return ts[:10]


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1
