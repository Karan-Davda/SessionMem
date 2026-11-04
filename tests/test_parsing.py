import json
import unittest

from sessionmem.parsing import event_kind, event_text, index_text


class TestEventText(unittest.TestCase):
    def test_user_event_plain_string_content_returns_that_string(self):
        event = {
            "type": "user",
            "message": {"role": "user", "content": "hello there"},
        }
        self.assertEqual(event_text(event), "hello there")

    def test_text_blocks_in_a_list_are_joined_with_newlines(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "first line"},
                    {"type": "text", "text": "second line"},
                ]
            },
        }
        self.assertEqual(event_text(event), "first line\nsecond line")

    def test_tool_use_block_renders_name_and_json_input(self):
        payload = {"path": "foo.py"}
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": payload},
                ]
            },
        }
        self.assertEqual(
            event_text(event),
            "[tool_use Read] " + json.dumps(payload),
        )

    def test_tool_result_block_renders_string_content(self):
        event = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": "file contents"},
                ]
            },
        }
        self.assertEqual(event_text(event), "[tool_result] file contents")

    def test_tool_result_block_renders_list_content(self):
        event = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": [
                            {"type": "text", "text": "line a"},
                            {"type": "text", "text": "line b"},
                        ],
                    }
                ]
            },
        }
        self.assertEqual(event_text(event), "[tool_result] line a\nline b")

    def test_thinking_blocks_are_skipped(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "secret chain of thought"},
                    {"type": "text", "text": "visible answer"},
                ]
            },
        }
        self.assertEqual(event_text(event), "visible answer")
        self.assertNotIn("secret chain of thought", event_text(event))

    def test_system_event_returns_its_content_string(self):
        event = {"type": "system", "content": "Session started"}
        self.assertEqual(event_text(event), "Session started")

    def test_malformed_event_without_message_returns_empty_string(self):
        self.assertEqual(event_text({}), "")

    def test_malformed_event_with_content_none_returns_empty_string(self):
        self.assertEqual(event_text({"message": {"content": None}}), "")

    def test_malformed_event_with_numeric_content_returns_empty_string(self):
        self.assertEqual(event_text({"message": {"content": 42}}), "")


class TestEventKind(unittest.TestCase):
    def test_all_tool_use_blocks_are_tool(self):
        event = {
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"path": "a"}},
                ]
            }
        }
        self.assertEqual(event_kind(event), "tool")

    def test_all_tool_result_blocks_are_tool(self):
        event = {
            "message": {
                "content": [
                    {"type": "tool_result", "content": "ok"},
                ]
            }
        }
        self.assertEqual(event_kind(event), "tool")

    def test_tool_use_and_tool_result_together_are_tool(self):
        event = {
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {}},
                    {"type": "tool_result", "content": "ok"},
                ]
            }
        }
        self.assertEqual(event_kind(event), "tool")

    def test_mixed_text_and_tool_use_is_chat(self):
        event = {
            "message": {
                "content": [
                    {"type": "text", "text": "let me read that"},
                    {"type": "tool_use", "name": "Read", "input": {}},
                ]
            }
        }
        self.assertEqual(event_kind(event), "chat")

    def test_plain_string_content_is_chat(self):
        event = {"message": {"content": "hello"}}
        self.assertEqual(event_kind(event), "chat")

    def test_empty_list_is_chat(self):
        event = {"message": {"content": []}}
        self.assertEqual(event_kind(event), "chat")


class TestIndexText(unittest.TestCase):
    def test_removes_system_reminder_blocks(self):
        result = index_text(
            "visible <system-reminder>do not index this</system-reminder> still here"
        )
        self.assertNotIn("<system-reminder>", result)
        self.assertNotIn("</system-reminder>", result)
        self.assertNotIn("do not index this", result)
        self.assertIn("visible", result)
        self.assertIn("still here", result)

    def test_removes_multiline_system_reminder_blocks(self):
        text = (
            "start\n"
            "<system-reminder>\n"
            "hidden line one\n"
            "hidden line two\n"
            "</system-reminder>\n"
            "end"
        )
        result = index_text(text)
        self.assertNotIn("<system-reminder>", result)
        self.assertNotIn("hidden line one", result)
        self.assertNotIn("hidden line two", result)
        self.assertIn("start", result)
        self.assertIn("end", result)

    def test_masks_api_keys(self):
        key = "sk-ant-api03-" + ("A" * 48)
        result = index_text(f"token {key} done")
        self.assertNotIn(key, result)
        self.assertIn("[REDACTED]", result)
        self.assertIn("token", result)
        self.assertIn("done", result)

    def test_masks_github_tokens(self):
        token = "ghp_" + ("b" * 36)
        result = index_text(f"auth {token} ok")
        self.assertNotIn(token, result)
        self.assertIn("[REDACTED]", result)
        self.assertIn("auth", result)
        self.assertIn("ok", result)

    def test_masks_aws_key_ids(self):
        key_id = "AKIAIOSFODNN7EXAMPLE"
        result = index_text(f"aws {key_id} id")
        self.assertNotIn(key_id, result)
        self.assertIn("[REDACTED]", result)
        self.assertIn("aws", result)
        self.assertIn("id", result)

    def test_truncates_to_50000_characters(self):
        self.assertEqual(index_text("a" * 50000), "a" * 50000)
        self.assertEqual(index_text("b" * 50001), "b" * 50000)
        self.assertEqual(len(index_text("c" * 60000)), 50000)
