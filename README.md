# sessionmem

sessionmem searches and traces everything you discussed with Claude Code.

[![tests](https://github.com/Karan-Davda/SessionMem/actions/workflows/tests.yml/badge.svg)](https://github.com/Karan-Davda/SessionMem/actions/workflows/tests.yml)

## The problem

Claude Code stores every session as a file under `~/.claude/projects`.
It offers no way to search across sessions.
It offers no way to see how a decision changed.

## Try it in 30 seconds

These commands build a small fake history. They do not read your Claude Code files.
You need Python 3.9 or newer.

```
git clone https://github.com/Karan-Davda/SessionMem
cd SessionMem
pip install -e .
sessionmem demo --dir /tmp/sm-demo
sessionmem timeline sqlite --db /tmp/sm-demo/index.db
sessionmem timeline postgres --db /tmp/sm-demo/index.db
```

`sessionmem demo --dir /tmp/sm-demo` printed:

```
Wrote /tmp/sm-demo/projects/-home-demo-acme-billing
sessionmem search --db /tmp/sm-demo/index.db 'approval gate'
sessionmem timeline --db /tmp/sm-demo/index.db sqlite
sessionmem timeline --db /tmp/sm-demo/index.db postgres
sessionmem search --db /tmp/sm-demo/index.db --include-tools ERROR
sessionmem show --db /tmp/sm-demo/index.db a1b2c3d4
```

`sessionmem timeline sqlite --db /tmp/sm-demo/index.db` printed:

```
session a1b2c3d4 | -home-demo-acme-billing | 2026-10-03 to 2026-10-03 | 2 hits
2026-10-03 user For the Acme billing prototype, use «SQLite» for the database.
2026-10-03 assistant «SQLite» is fine for a prototype. We can revisit the database later.
session e5f6a7b8 | -home-demo-acme-billing | 2026-10-09 to 2026-10-09 | 1 hit
2026-10-09 assistant «SQLite» locks the whole file during writes. I suggest moving the database to Postgres.
```

`sessionmem timeline postgres --db /tmp/sm-demo/index.db` printed:

```
session e5f6a7b8 | -home-demo-acme-billing | 2026-10-09 to 2026-10-09 | 2 hits
2026-10-09 assistant SQLite locks the whole file during writes. I suggest moving the database to «Postgres».
2026-10-09 user OK, switch to «Postgres». Keep the approval gate exactly as it is.
session c9d0e1f2 | -home-demo-acme-billing | 2026-10-20 to 2026-10-20 | 1 hit
2026-10-20 assistant Stay on «Postgres» and add full-text search. The approval gate also needs an audit log.
```

SQLite is chosen on Oct 3, fails on Oct 9, and Postgres takes over.
All of that is found with those two commands.

## Use it on your own history

`sessionmem ingest` reads `~/.claude/projects`. The index defaults to `~/.sessionmem/index.db`.

```
sessionmem ingest
sessionmem search "approval gate"
sessionmem search "approval gate" --project billing --since 2026-10-01
sessionmem timeline database --per-session 3
sessionmem show a1b2c3d4
sessionmem stats
```

Other flags: `--role`, `--include-tools`, `--include-summaries`, `-n`, `--width`, `--full`, and `--db` for a different index.

## How it works

The raw line of each message is stored in SQLite.
Ingest continues from the last saved byte offset in each file.
Search uses FTS5 and ranks hits with BM25.
A message copied into a resumed session stays in the database.
Search, timeline, and show hide that copy when you read. See [DESIGN.md](DESIGN.md).

## Privacy

Everything stays on your machine.
Nothing is sent anywhere.
The index contains your conversations, so do not share it or commit it.
The index keeps the original text of every message, so it can contain anything that appeared in your sessions, including secrets. Masking only affects the searchable text and is best effort.

## Limits

sessionmem shows how a topic developed.
It does not decide which statement is current.
Times are UTC, as Claude Code stores them.
Claude Code's file format may change.

## Development

```
python -m unittest
```

CI runs Python 3.9, 3.10, 3.11, 3.12, and 3.13 on Linux, macOS, and Windows.
