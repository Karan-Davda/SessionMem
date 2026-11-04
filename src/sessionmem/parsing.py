"""Extract and sanitize text from Claude Code session events."""

from __future__ import annotations

import json
import re
from typing import Any

# Matches <system-reminder>...</system-reminder> blocks, including across newlines.
_SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>",
    re.DOTALL,
)

# Matches secret API keys such as Anthropic sk-ant-... values.
_API_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")

# Matches GitHub personal access tokens such as ghp_...
_GITHUB_TOKEN_RE = re.compile(r"ghp_[A-Za-z0-9]+")

# Matches AWS access key IDs (AKIA followed by 16 alphanumeric characters).
_AWS_KEY_ID_RE = re.compile(r"AKIA[0-9A-Z]{16}")

_MAX_INDEX_CHARS = 50_000
_TOOL_BLOCK_TYPES = frozenset({"tool_use", "tool_result"})


def event_text(event: dict[str, Any]) -> str:
    """Return searchable text for a session event, or "" if the event is malformed."""
    if not isinstance(event, dict):
        return ""
    if event.get("type") == "system":
        content = event.get("content")
        return content if isinstance(content, str) else ""

    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    return _render_content(message.get("content"))


def event_kind(event: dict[str, Any]) -> str:
    """Return "tool" when every content block is a tool call or result, else "chat"."""
    if not isinstance(event, dict):
        return "chat"
    message = event.get("message")
    if not isinstance(message, dict):
        return "chat"
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return "chat"
    if all(
        isinstance(block, dict) and block.get("type") in _TOOL_BLOCK_TYPES
        for block in content
    ):
        return "tool"
    return "chat"


def index_text(text: str) -> str:
    """Strip system reminders, redact secrets, and truncate text for indexing."""
    if not isinstance(text, str):
        return ""
    text = _SYSTEM_REMINDER_RE.sub("", text)
    text = _API_KEY_RE.sub("[REDACTED]", text)
    text = _GITHUB_TOKEN_RE.sub("[REDACTED]", text)
    text = _AWS_KEY_ID_RE.sub("[REDACTED]", text)
    return text[:_MAX_INDEX_CHARS]


def _render_content(content: Any) -> str:
    """Render a message content string or list of blocks into a single string."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [_render_block(block) for block in content]
    return "\n".join(part for part in parts if part is not None)


def _render_block(block: Any) -> str | None:
    """Render one content block, or None to skip it (e.g. thinking)."""
    if not isinstance(block, dict):
        return None
    kind = block.get("type")
    if kind == "thinking":
        return None
    if kind == "text":
        text = block.get("text")
        return text if isinstance(text, str) else None
    if kind == "tool_use":
        name = block.get("name", "")
        payload = block.get("input", {})
        return f"[tool_use {name}] {json.dumps(payload)}"
    if kind == "tool_result":
        return "[tool_result] " + _render_content(block.get("content"))
    return None
