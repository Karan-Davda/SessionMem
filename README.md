# sessionmem

Search everything you have discussed with Claude Code.

Claude Code saves every session as a JSONL file. Those files hold decisions
and reasoning, but nothing reads across them, so each new session starts from
zero. sessionmem will index them locally so you can search them and trace how
a decision changed over time.

Status: work in progress