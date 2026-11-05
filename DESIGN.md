# Design

## Decisions

### Raw-first storage

The original line is kept verbatim. Derived text is stored separately.
A wrong rule then cannot destroy the original line.
Rejected: storing only the cleaned text.

### Incremental ingest

Ingest records a byte offset and continues from there on the next run.
A half-written last line is left for the next run.

### Search

Search uses FTS5 with the porter unicode61 tokenizer and BM25 ranking.
Each user word is quoted before it is sent to FTS5.
Punctuation and the word AND then cannot break the query.

### Tools

Claude Code stores tool output under the user role.
Chat and tool events are classified separately.
Tools are excluded unless you ask for them.

### Duplicates

On one real history, ingest added 3,138 rows and `stats` shows 2,542 events.
The difference is 596 rows, about 19 percent, which were extra copies.
490 uuids were repeated across files, because a resumed session copies earlier messages.
Duplicates are hidden when you search, and every row is kept.
`show` hides a repeated uuid only inside the one session you asked for, so a resumed session still shows its full conversation.
`EXPLAIN QUERY PLAN` showed the uuid index used as a covering scan.
Rejected: deleting the copied rows at ingest, and hiding copies across sessions in `show`.

### How it was built

Tests were written first, then the feature.
For key behaviours the code was broken on purpose, and the check was that the right test failed.
Running on real history found five CLI gaps that the tests had missed.
Each gap was fixed in its own commit.

### Not done

There is no web UI.
There is no embeddings or semantic search.
There is no secret scanning beyond best-effort masking.

## What real data taught me

Two long messages, sent two minutes apart, looked the same in a snippet.
They had different uuids and different lengths, so they are two real messages and are not merged.
Rejected: merging near-identical messages.

Pasted output once looked like words were glued together; the stored text and the displayed text were correct, and the cause was the terminal wrapping the line when it was copied.
