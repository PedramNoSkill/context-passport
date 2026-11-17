"""Synthetic transcript import cases; never inspect private chat stores."""

import json
from pathlib import Path
import tempfile
import unittest


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def source(self, text, name="selected.json"):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_claude_web_imports_visible_messages_with_provenance(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({
            "uuid": "synthetic-chat", "name": "Build importer",
            "chat_messages": [
                {"uuid": "user-1", "sender": "human", "text": "Build an importer.",
                 "created_at": "2026-01-01T12:00:00Z"},
                {"uuid": "assistant-1", "sender": "assistant", "text": "Done: parsed messages."},
            ],
        }))
        transcript = load_transcript(path)
        self.assertEqual(transcript.format, "claude")
        self.assertEqual(transcript.conversation_id, "synthetic-chat")
        self.assertEqual(transcript.title, "Build importer")
        self.assertEqual([m.id for m in transcript.messages], ["m0001", "m0002"])
        self.assertEqual([m.role for m in transcript.messages], ["user", "assistant"])
        self.assertEqual(transcript.messages[0].text, "Build an importer.")
        self.assertEqual(transcript.messages[0].locator, "$.chat_messages[0]")
        self.assertEqual(transcript.messages[0].timestamp, "2026-01-01T12:00:00Z")
        self.assertEqual(transcript.messages[0].kind, "message")
        self.assertIsNone(transcript.messages[0].tool_name)
        self.assertIsNone(transcript.messages[0].call_id)
        self.assertEqual(transcript.warnings, [])

    def test_claude_web_omits_private_blocks_and_instructions(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({"chat_messages": [
            {"sender": "system", "text": "SYSTEM-PRIVATE"},
            {"sender": "developer", "text": "DEVELOPER-PRIVATE"},
            {"sender": "assistant", "text": "DUPLICATE-FALLBACK", "content": [
                {"type": "thinking", "thinking": "THINKING-PRIVATE"},
                {"type": "reasoning", "text": "REASONING-PRIVATE"},
                {"type": "image", "source": {"data": "BASE64-PRIVATE"}},
                {"type": "text", "text": "Decision: retain visible evidence."},
            ], "attachments": [{"content": "ATTACHMENT-PRIVATE"}]},
        ]}))
        result = load_transcript(path)
        self.assertEqual([m.text for m in result.messages], ["Decision: retain visible evidence."])
        self.assertEqual(result.messages[0].id, "m0001")
        warnings = " ".join(result.warnings).lower()
        self.assertIn("omitt", warnings)
        for private in ("SYSTEM-PRIVATE", "DEVELOPER-PRIVATE", "THINKING-PRIVATE",
                        "REASONING-PRIVATE", "BASE64-PRIVATE", "ATTACHMENT-PRIVATE"):
            self.assertNotIn(private, repr(result))

    def test_export_selection_is_explicit_and_listing_has_no_message_text(self):
        from context_passport.transcripts import (TranscriptError, list_conversations,
                                                  load_transcript)
        path = self.source(json.dumps([
            {"uuid": "one", "name": "First", "chat_messages": [
                {"sender": "human", "text": "PRIVATE-BODY-ONE"}]},
            {"uuid": "two", "name": "Second", "chat_messages": [
                {"sender": "human", "text": "PRIVATE-BODY-TWO"}]},
        ]))
        self.assertEqual(list_conversations(path), [
            {"id": "one", "title": "First", "message_count": 1},
            {"id": "two", "title": "Second", "message_count": 1},
        ])
        with self.assertRaisesRegex(TranscriptError, "(?i)multiple.*--conversation"):
            load_transcript(path)
        for selector in ("two", "Second"):
            self.assertEqual(load_transcript(path, conversation=selector).messages[0].text,
                             "PRIVATE-BODY-TWO")
        with self.assertRaisesRegex(TranscriptError, "(?i)no.*conversation.*match"):
            load_transcript(path, conversation="Sec")

    def test_duplicate_conversation_titles_require_a_unique_id(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        path = self.source(json.dumps([
            {"uuid": "one", "name": "Duplicate", "chat_messages": [
                {"sender": "human", "text": "first"}]},
            {"uuid": "two", "name": "Duplicate", "chat_messages": [
                {"sender": "human", "text": "second"}]},
        ]))
        with self.assertRaisesRegex(TranscriptError, "(?i)ambiguous.*id"):
            load_transcript(path, conversation="Duplicate")
        result = load_transcript(path, conversation="two")
        self.assertEqual(result.messages[0].locator, "$[1].chat_messages[0]")

    def test_claude_code_pairs_tool_calls_with_visible_results(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "user", "sessionId": "session-synthetic", "timestamp": "now",
             "message": {"role": "user", "content": "Run the tests."}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "PRIVATE-THOUGHT"},
                {"type": "text", "text": "Checking tests."},
                {"type": "tool_use", "id": "tool-1", "name": "Bash",
                 "input": {"command": "python3 -m unittest -v"}},
            ]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tool-1",
                 "content": "Process exited with code 0\nOK"},
            ]}},
        ]
        path = self.source("\n".join(json.dumps(r) for r in records), "chosen.jsonl")
        result = load_transcript(path)
        self.assertEqual(result.format, "claude-code")
        self.assertEqual(result.conversation_id, "session-synthetic")
        self.assertEqual([m.kind for m in result.messages],
                         ["message", "message", "tool_call", "tool_result"])
        call, output = result.messages[-2:]
        self.assertEqual(call.role, "assistant")
        self.assertEqual(output.role, "tool")
        self.assertEqual((call.call_id, output.call_id), ("tool-1", "tool-1"))
        self.assertEqual((call.tool_name, output.tool_name), ("Bash", "Bash"))
        self.assertEqual(json.loads(call.text), {"command": "python3 -m unittest -v"})
        self.assertIn("code 0", output.text)
        self.assertIn("line 2", call.locator)
        self.assertIn("content[2]", call.locator)
        self.assertNotIn("PRIVATE-THOUGHT", repr(result))

    def test_codex_rollout_imports_response_messages_and_function_pairs(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "session_meta", "payload": {"id": "codex-synthetic"}},
            {"type": "response_item", "timestamp": "2026-01-01", "payload": {
                "type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "Run checks."}]}},
            {"type": "response_item", "payload": {"type": "reasoning",
                                                   "summary": "PRIVATE-REASONING"}},
            {"type": "response_item", "payload": {"type": "function_call",
                "call_id": "exec-1", "name": "exec_command",
                "arguments": '{"cmd":"python3 -m unittest"}'}},
            {"type": "response_item", "payload": {"type": "function_call_output",
                "call_id": "exec-1", "output": "Process exited with code 0\nOK"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Done: checks passed."}]}},
        ]
        path = self.source("\n".join(map(json.dumps, records)), "rollout.jsonl")
        result = load_transcript(path)
        self.assertEqual(result.format, "codex")
        self.assertEqual(result.conversation_id, "codex-synthetic")
        self.assertEqual([m.text for m in result.messages][::3],
                         ["Run checks.", "Done: checks passed."])
        call, output = result.messages[1:3]
        self.assertEqual((call.kind, output.kind), ("tool_call", "tool_result"))
        self.assertEqual((call.call_id, output.call_id), ("exec-1", "exec-1"))
        self.assertEqual(output.tool_name, "exec_command")
        self.assertEqual(json.loads(call.text)["cmd"], "python3 -m unittest")
        self.assertIn("line 4", call.locator)
        self.assertNotIn("PRIVATE-REASONING", repr(result))
        self.assertTrue(result.warnings)

    def test_codex_event_messages_are_visible_and_mirrors_are_not_duplicated(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Build it."}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Build it."}]}},
            {"type": "event_msg", "payload": {"type": "agent_reasoning", "text": "PRIVATE"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "Done: built."}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Done: built."}]}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Next?"}},
        ]
        result = load_transcript(self.source("\n".join(map(json.dumps, records)), "events.jsonl"))
        self.assertEqual([m.text for m in result.messages], ["Build it.", "Done: built.", "Next?"])
        self.assertEqual([m.role for m in result.messages], ["user", "assistant", "user"])
        self.assertIn("line 6", result.messages[-1].locator)
        self.assertTrue(any("duplicate" in w.lower() for w in result.warnings))
        self.assertNotIn("PRIVATE", repr(result))

    def test_chatgpt_mapping_uses_only_active_branch_and_visible_channels(self):
        from context_passport.transcripts import list_conversations, load_transcript

        def node(parent, role, text, channel=None):
            return {"parent": parent, "message": {"author": {"role": role},
                "channel": channel, "content": {"content_type": "text", "parts": [text]}}}

        path = self.source(json.dumps({"id": "gpt-one", "title": "Synthetic branches",
            "current_node": "active", "mapping": {
                "root": {"parent": None, "message": None},
                "system": node("root", "system", "PRIVATE-SYSTEM"),
                "user": node("system", "user", "Fix parser."),
                "abandoned": node("user", "assistant", "ABANDONED-ANSWER"),
                "thinking": node("user", "assistant", "PRIVATE-ANALYSIS", "analysis"),
                "active": node("thinking", "assistant", "Done: parser fixed.", "final"),
            }}))
        result = load_transcript(path)
        self.assertEqual(result.format, "chatgpt")
        self.assertEqual(result.conversation_id, "gpt-one")
        self.assertEqual(result.title, "Synthetic branches")
        self.assertEqual([m.text for m in result.messages], ["Fix parser.", "Done: parser fixed."])
        self.assertIn('["active"]', result.messages[-1].locator)
        for omitted in ("PRIVATE-SYSTEM", "PRIVATE-ANALYSIS", "ABANDONED-ANSWER"):
            self.assertNotIn(omitted, repr(result))
        self.assertEqual(list_conversations(path), [{"id": "gpt-one", "title": "Synthetic branches",
                                                   "message_count": 2}])

    def test_role_labelled_markdown_preserves_code_fences_and_line_provenance(self):
        from context_passport.transcripts import load_transcript

        text = ("# Synthetic transcript\n\n## System\nPRIVATE-SYSTEM\n\n"
                "## User\nBuild parser.\n\n## Assistant\nDone: built.\n"
                "```text\nUser: this is code, not a turn\n```\n\n"
                "**User:** Next: test it.\nAssistant: Running tests.")
        result = load_transcript(self.source(text, "selected.md"))
        self.assertEqual(result.format, "text")
        self.assertEqual([m.role for m in result.messages], ["user", "assistant", "user", "assistant"])
        self.assertEqual(result.messages[0].text, "Build parser.")
        self.assertIn("User: this is code", result.messages[1].text)
        self.assertEqual(result.messages[2].text, "Next: test it.")
        self.assertIn("line 7", result.messages[0].locator)
        self.assertNotIn("PRIVATE-SYSTEM", repr(result))

    def test_malformed_jsonl_error_reports_physical_line_without_echoing_input(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        path = self.source('{"type":"user","message":{"role":"user","content":"ok"}}'
                           '\n\n{"PRIVATE-PAYLOAD": broken}', "broken.jsonl")
        with self.assertRaisesRegex(TranscriptError, "(?i)json.*line 3") as caught:
            load_transcript(path)
        self.assertNotIn("PRIVATE-PAYLOAD", str(caught.exception))

    def test_empty_unknown_and_malformed_inputs_have_actionable_errors(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        for text, name, expected in [
            ("", "empty.txt", "(?i)empty"),
            ("   \n", "empty.json", "(?i)empty"),
            ("[]", "none.json", "(?i)empty|no conversations"),
            ("{}", "unknown.json", "(?i)unsupported|unrecognized"),
            ('{"chat_messages": [\n}', "bad.json", "(?i)json.*line 2"),
            ("unlabelled prose", "unknown.txt", "(?i)role.label|no.*messages"),
            ('{"chat_messages": []}', "no-messages.json", "(?i)no.*messages"),
        ]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(TranscriptError, expected):
                    load_transcript(self.source(text, name))

    def test_explicit_format_rejects_mismatched_structure_and_unknown_names(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        path = self.source('{"chat_messages":[{"sender":"human","text":"hi"}]}')
        for format_name in ("codex", "claude-code", "chatgpt", "text", "made-up"):
            with self.subTest(format=format_name):
                with self.assertRaisesRegex(TranscriptError, "(?i)format|structure"):
                    load_transcript(path, format=format_name)
        self.assertEqual(load_transcript(path, format="claude").format, "claude")

    def test_utf8_missing_file_and_directory_errors_are_actionable(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        invalid = self.root / "invalid.json"
        invalid.write_bytes(b"\xff\x00")
        for path, expected in [(invalid, "(?i)utf.8"),
                               (self.root / "missing.txt", "(?i)read.*file|not.*exist"),
                               (self.root, "(?i)regular file|directory")]:
            with self.subTest(name=path.name):
                with self.assertRaisesRegex(TranscriptError, expected):
                    load_transcript(path)

    def test_file_size_limit_rejects_more_than_25_mib_in_both_public_readers(self):
        from context_passport.transcripts import (TranscriptError, list_conversations,
                                                  load_transcript)

        path = self.root / "oversized.json"
        with path.open("wb") as stream:
            stream.truncate(25 * 1024 * 1024 + 1)
        for reader in (load_transcript, list_conversations):
            with self.subTest(reader=reader.__name__):
                with self.assertRaisesRegex(TranscriptError, "(?i)25.*MiB|too large"):
                    reader(path)

    def test_message_text_is_bounded_in_utf8_bytes_with_explicit_truncation(self):
        from context_passport.transcripts import load_transcript

        text = "界" * 40000
        path = self.source(json.dumps({"chat_messages": [{"sender": "human", "text": text}]}))
        message_result = load_transcript(path)
        message = message_result.messages[0]
        self.assertLessEqual(len(message.text.encode("utf-8")), 64 * 1024)
        self.assertTrue(message.text.startswith("界"))
        self.assertIn("[TRUNCATED]", message.text)
        self.assertTrue(any("truncat" in w.lower() and "64" in w for w in message_result.warnings))

    def test_message_count_cap_is_deterministic_and_warns(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({"chat_messages": [
            {"sender": "human", "text": f"message {index}"} for index in range(10002)]}))
        result = load_transcript(path)
        self.assertEqual(len(result.messages), 10000)
        self.assertEqual(result.messages[-1].id, "m10000")
        self.assertEqual(result.messages[-1].text, "message 9999")
        self.assertTrue(any("10000" in w and "truncat" in w.lower() for w in result.warnings))

    def test_malformed_record_shapes_raise_transcript_errors_with_locations(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        cases = [
            ({"chat_messages": "not a list"}, "shape.json", "(?i)chat_messages.*list"),
            ({"chat_messages": [None]}, "shape.json", "(?i)chat_messages.*0"),
            ({"chat_messages": [{"sender": "human", "content": 7}]}, "shape.json", "(?i)content"),
            ({"type": "user", "message": {"role": "user", "content": [7]}},
             "shape.jsonl", "(?i)line 1.*content"),
            ({"type": "response_item", "payload": []}, "shape.jsonl", "(?i)line 1.*payload"),
            ([1, 2], "shape.json", "(?i)unsupported.*structure"),
        ]
        for data, name, expected in cases:
            with self.subTest(data=data):
                with self.assertRaisesRegex(TranscriptError, expected):
                    load_transcript(self.source(json.dumps(data), name))

    def test_chatgpt_malformed_active_branch_fails_without_falling_back(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        cases = [
            ({"mapping": {}, "current_node": "missing"}, "(?i)current_node|missing.*node"),
            ({"mapping": {"loop": {"parent": "loop", "message": None}}, "current_node": "loop"},
             "(?i)cycle"),
            ({"mapping": {"a": {"parent": "absent", "message": None}}, "current_node": "a"},
             "(?i)missing.*node|parent"),
            ({"mapping": {"root": {"parent": None, "message": None}}}, "(?i)current_node"),
            ({"mapping": [], "current_node": "a"}, "(?i)mapping.*object"),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                with self.assertRaisesRegex(TranscriptError, expected):
                    load_transcript(self.source(json.dumps(data)))

    def test_jsonl_metadata_only_unknown_records_and_nonobjects_are_not_silently_accepted(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        cases = [
            ('{"type":"user","message":{"role":"user","content":"ok"}}\n7',
             "(?i)line 2.*object"),
            ('{"type":"unrelated","data":"private"}', "(?i)unsupported|unrecognized"),
            ('{"type":"session_meta","payload":{"id":"one"}}', "(?i)no.*messages"),
            ('{"type":"user","message":{"role":"user","content":"ok"}}\n'
             '{"type":"assistant","message":7}', "(?i)line 2.*message"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(TranscriptError, expected):
                    load_transcript(self.source(raw, "chosen.jsonl"))

    def test_tool_payloads_omit_binary_and_reasoning_but_preserve_structured_exit(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {
                    "command": "python -m unittest", "thinking": "PRIVATE-THINKING",
                    "image": {"type": "image", "source": {"type": "base64", "data": "PRIVATE-BINARY"}}}},
            ]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": [
                    {"type": "text", "text": "OK"},
                    {"type": "image", "source": {"data": "PRIVATE-BINARY"}},
                ]}]}},
        ]
        result = load_transcript(self.source("\n".join(map(json.dumps, records)), "tools.jsonl"))
        self.assertEqual(json.loads(result.messages[0].text), {"command": "python -m unittest"})
        self.assertEqual(result.messages[1].text, "OK")
        self.assertNotIn("PRIVATE-THINKING", repr(result))
        self.assertNotIn("PRIVATE-BINARY", repr(result))
        self.assertTrue(result.warnings)

    def test_codex_json_arguments_and_structured_outputs_keep_exit_codes_without_private_fields(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "response_item", "payload": {"type": "function_call", "call_id": "c",
                "name": "exec_command", "arguments": json.dumps({"cmd": "pytest", "reasoning": "PRIVATE"})}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c",
                "output": {"stdout": "1 passed", "exit_code": 0,
                           "image": {"type": "image", "data": "PRIVATE"}}}},
        ]
        result = load_transcript(self.source("\n".join(map(json.dumps, records)), "chosen.jsonl"))
        self.assertEqual(json.loads(result.messages[0].text), {"cmd": "pytest"})
        self.assertEqual(json.loads(result.messages[1].text), {"stdout": "1 passed", "exit_code": 0})
        self.assertNotIn("PRIVATE", repr(result))

    def test_listing_supports_coding_jsonl_and_role_text_without_full_bodies(self):
        from context_passport.transcripts import list_conversations

        for raw, name, expected_id in [
            ('{"type":"user","sessionId":"cc","message":{"role":"user","content":"PRIVATE-BODY"}}',
             "cc.jsonl", "cc"),
            ('{"type":"session_meta","payload":{"id":"cx"}}\n'
             '{"type":"event_msg","payload":{"type":"user_message","message":"PRIVATE-BODY"}}',
             "cx.jsonl", "cx"),
            ("User: PRIVATE-BODY", "selected.txt", None),
        ]:
            with self.subTest(name=name):
                listing = list_conversations(self.source(raw, name))
                self.assertEqual(listing, [{"id": expected_id, "title": None, "message_count": 1}])
                self.assertNotIn("PRIVATE-BODY", repr(listing))

    def test_codex_analysis_channels_are_never_imported(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "channel": "analysis", "content": [{"type": "output_text", "text": "PRIVATE-ANALYSIS"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "developer",
                "content": [{"type": "input_text", "text": "PRIVATE-DEVELOPER"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "channel": "final", "content": [{"type": "output_text", "text": "Visible final"}]}},
        ]
        result = load_transcript(self.source("\n".join(map(json.dumps, records)), "chosen.jsonl"))
        self.assertEqual([m.text for m in result.messages], ["Visible final"])
        self.assertNotIn("PRIVATE-", repr(result))
        self.assertTrue(result.warnings)

    def test_tool_results_without_calls_warn_instead_of_inventing_a_tool_name(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "missing-call", "output": "exit_code: 0"}}),
            "orphan.jsonl")
        result = load_transcript(path)
        self.assertEqual(result.messages[0].call_id, "missing-call")
        self.assertIsNone(result.messages[0].tool_name)
        self.assertTrue(any("unmatched" in w.lower() or "without" in w.lower() for w in result.warnings))

    def test_listing_rejects_empty_and_invalid_exports_with_transcript_errors(self):
        from context_passport.transcripts import TranscriptError, list_conversations

        for raw in ("[]", "{}", "null", "", "{broken", '{"chat_messages": 7}',
                    '{"chat_messages": [null]}'):
            with self.subTest(raw=raw):
                with self.assertRaises(TranscriptError):
                    list_conversations(self.source(raw))

    def test_jsonl_first_invalid_line_and_single_record_physical_line_are_reported(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        with self.assertRaisesRegex(TranscriptError, "(?i)json.*line 1"):
            load_transcript(self.source("not json\n{}", "invalid.jsonl"))
        path = self.source('\n\n{"type":"event_msg","payload":{"type":"user_message",'
                           '"message":"Visible"}}', "one.jsonl")
        result = load_transcript(path)
        self.assertIn("line 3", result.messages[0].locator)

    def test_coding_transcript_selector_is_checked_exactly(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        path = self.source('{"type":"user","sessionId":"selected-session",'
                           '"message":{"role":"user","content":"Visible"}}', "session.jsonl")
        self.assertEqual(load_transcript(path, conversation="selected-session").messages[0].text, "Visible")
        with self.assertRaisesRegex(TranscriptError, "(?i)no.*conversation.*match"):
            load_transcript(path, conversation="other-session")

    def test_multiple_jsonl_sessions_require_selection_and_keep_physical_locators(self):
        from context_passport.transcripts import TranscriptError, list_conversations, load_transcript

        records = [
            {"type": "user", "sessionId": "first", "message": {"role": "user", "content": "one"}},
            {"type": "user", "sessionId": "second", "message": {"role": "user", "content": "two"}},
        ]
        path = self.source("\n".join(map(json.dumps, records)), "sessions.jsonl")
        with self.assertRaisesRegex(TranscriptError, "(?i)multiple.*--conversation"):
            load_transcript(path)
        selected = load_transcript(path, conversation="first")
        self.assertEqual([m.text for m in selected.messages], ["one"])
        self.assertIn("line 1", selected.messages[0].locator)
        self.assertEqual(list_conversations(path), [
            {"id": "first", "title": None, "message_count": 1},
            {"id": "second", "title": None, "message_count": 1}])

    def test_codex_session_metadata_separates_multiple_rollouts(self):
        from context_passport.transcripts import TranscriptError, list_conversations, load_transcript

        records = [
            {"type": "session_meta", "payload": {"id": "first"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "one"}},
            {"type": "session_meta", "payload": {"id": "second"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "two"}},
        ]
        path = self.source("\n".join(map(json.dumps, records)), "sessions.jsonl")
        with self.assertRaisesRegex(TranscriptError, "(?i)multiple.*--conversation"):
            load_transcript(path)
        result = load_transcript(path, conversation="second")
        self.assertEqual([m.text for m in result.messages], ["two"])
        self.assertIn("line 4", result.messages[0].locator)
        self.assertEqual([item["id"] for item in list_conversations(path)], ["first", "second"])

    def test_malformed_tool_identifiers_are_actionable_errors(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        for payload in [
            {"type": "function_call", "call_id": [], "name": "exec", "arguments": "{}"},
            {"type": "function_call", "call_id": "c", "name": 7, "arguments": "{}"},
            {"type": "function_call_output", "call_id": {}, "output": "OK"},
        ]:
            with self.subTest(payload=payload):
                path = self.source(json.dumps({"type": "response_item", "payload": payload}), "bad.jsonl")
                with self.assertRaisesRegex(TranscriptError, "(?i)line 1.*call_id|line 1.*name"):
                    load_transcript(path)

    def test_malformed_claude_tool_identifiers_are_actionable_errors(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        for block in [
            {"type": "tool_use", "id": [], "name": "Bash", "input": {}},
            {"type": "tool_use", "id": "c", "name": 7, "input": {}},
            {"type": "tool_result", "tool_use_id": {}, "content": "OK"},
        ]:
            with self.subTest(block=block):
                path = self.source(json.dumps({"type": "assistant", "message": {
                    "role": "assistant", "content": [block]}}), "bad.jsonl")
                with self.assertRaisesRegex(TranscriptError, "(?i)line 1.*id|line 1.*name"):
                    load_transcript(path)

    def test_invalid_chatgpt_message_shapes_raise_actionable_errors(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        for message in (7, {"author": 7}, {"author": {"role": "user"}, "content": 7},
                        {"author": {"role": "user"}, "content": {"content_type": "text", "parts": 7}}):
            with self.subTest(message=message):
                path = self.source(json.dumps({"mapping": {"active": {
                    "parent": None, "message": message}}, "current_node": "active"}))
                with self.assertRaisesRegex(TranscriptError, "(?i)mapping|message|content|parts|author"):
                    load_transcript(path)

    def test_chatgpt_numeric_timestamps_normalize_to_public_string_type(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({"id": "one", "title": "Synthetic", "current_node": "a",
            "mapping": {"a": {"parent": None, "message": {"author": {"role": "user"},
                "create_time": 1700000000.5, "content": {"content_type": "text", "parts": ["Hi"]}}}}}))
        result = load_transcript(path)
        self.assertIsInstance(result.messages[0].timestamp, str)
        self.assertEqual(result.messages[0].timestamp, "1700000000.5")

    def test_tagged_hidden_thinking_and_reasoning_turns_are_omitted_from_text(self):
        from context_passport.transcripts import load_transcript

        text = ("User: Build parser.\nAssistant: <think>PRIVATE-A\nPRIVATE-B</think>Visible answer.\n"
                "Reasoning: PRIVATE-C\n\nAssistant: Done: parser built.\n"
                "<thinking>PRIVATE-D</thinking>\n")
        result = load_transcript(self.source(text, "selected.txt"))
        self.assertEqual([m.text for m in result.messages],
                         ["Build parser.", "Visible answer.", "Done: parser built."])
        self.assertNotIn("PRIVATE-", repr(result))
        self.assertTrue(result.warnings)

    def test_tool_values_strip_data_uris_and_nested_hidden_blocks_with_warnings(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "c", "name": "Bash", "input": {
                "command": "pytest", "sample": "data:image/png;base64,UFJJVkFURQ==",
                "nested": [{"type": "reasoning", "text": "PRIVATE"}, {"exit_code": 0}]}},
        ]}}), "selected.jsonl")
        result = load_transcript(path)
        self.assertNotIn("UFJJVkFURQ==", repr(result))
        self.assertNotIn("PRIVATE", repr(result))
        self.assertIn('"exit_code": 0', result.messages[0].text)
        self.assertTrue(any("omitt" in warning.lower() for warning in result.warnings))

    def test_claude_web_empty_content_array_uses_legacy_text_only_when_no_blocks_exist(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({"chat_messages": [
            {"sender": "human", "content": [], "text": "Legacy visible text"},
            {"sender": "assistant", "content": [{"type": "thinking", "thinking": "PRIVATE"}],
             "text": "PRIVATE-FALLBACK"},
        ]}))
        result = load_transcript(path)
        self.assertEqual([m.text for m in result.messages], ["Legacy visible text"])
        self.assertNotIn("PRIVATE", repr(result))

    def test_malformed_role_and_block_fields_never_escape_as_raw_type_errors(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        cases = [
            {"type": "user", "message": {"role": [], "content": "bad"}},
            {"type": "response_item", "payload": {"type": "message", "role": [], "content": "bad"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                      "channel": [], "content": "bad"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": [], "text": "bad"}]}},
        ]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaisesRegex(TranscriptError, "(?i)malformed|must be|structure"):
                    load_transcript(self.source(json.dumps(data), "bad.jsonl"))

    def test_structurally_omitted_event_attachments_generate_a_warning(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({"type": "event_msg", "payload": {
            "type": "user_message", "message": "Visible user", "images": ["PRIVATE-IMAGE"],
            "local_images": ["PRIVATE-PATH"]}}), "selected.jsonl")
        result = load_transcript(path)
        self.assertEqual([m.text for m in result.messages], ["Visible user"])
        self.assertNotIn("PRIVATE-", repr(result))
        self.assertTrue(any("attachment" in w.lower() and "omitt" in w.lower()
                            for w in result.warnings))

    def test_deeply_nested_json_and_invalid_unicode_fail_actionably(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        for raw, expected in [
            ("[" * 1100 + "0" + "]" * 1100, "(?i)nested|complex|depth"),
            ('{"chat_messages":[{"sender":"human","text":"\\ud800"}]}', "(?i)unicode|surrogate"),
        ]:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(TranscriptError, expected):
                    load_transcript(self.source(raw))

    def test_unlabelled_unknown_jsonl_records_are_warned_when_visible_messages_remain(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "user", "message": {"role": "user", "content": "Visible"}},
            {"type": "future-format", "payload": {"text": "OMITTED-UNKNOWN"}},
        ]
        result = load_transcript(self.source("\n".join(map(json.dumps, records)), "selected.jsonl"))
        self.assertEqual([m.text for m in result.messages], ["Visible"])
        self.assertTrue(any("unsupported" in w.lower() and "omitt" in w.lower() for w in result.warnings))

    def test_codex_repeated_visible_turns_are_not_globally_deduplicated(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Repeat"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "First answer"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Repeat"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Second answer"}]}},
        ]
        result = load_transcript(self.source("\n".join(map(json.dumps, records)), "repeated.jsonl"))
        self.assertEqual([m.text for m in result.messages],
                         ["Repeat", "First answer", "Repeat", "Second answer"])

    def test_chatgpt_conversation_id_alias_supports_exact_selection(self):
        from context_passport.transcripts import list_conversations, load_transcript

        exports = [{"conversation_id": name, "title": name, "current_node": "a", "mapping": {
            "a": {"parent": None, "message": {"author": {"role": "user"},
                "content": {"content_type": "text", "parts": ["Visible"]}}}}} for name in ("one", "two")]
        path = self.source(json.dumps(exports))
        self.assertEqual([item["id"] for item in list_conversations(path)], ["one", "two"])
        self.assertEqual(load_transcript(path, conversation="two").conversation_id, "two")

    def test_claude_web_content_tool_blocks_are_normalized_with_call_links(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({"uuid": "web", "chat_messages": [
            {"sender": "assistant", "content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "c", "name": "Bash", "input": {"command": "pytest"}},
                {"type": "tool_result", "tool_use_id": "c", "content": "exit_code: 0"},
            ]},
        ]}))
        result = load_transcript(path)
        self.assertEqual([m.kind for m in result.messages], ["message", "tool_call", "tool_result"])
        self.assertEqual([m.call_id for m in result.messages[1:]], ["c", "c"])
        self.assertEqual(result.messages[-1].tool_name, "Bash")
        self.assertIn("chat_messages[0].content[2]", result.messages[-1].locator)

    def test_malformed_metadata_and_non_scalar_discriminators_fail_actionably(self):
        from context_passport.transcripts import TranscriptError, list_conversations, load_transcript

        cases = [
            {"uuid": {"private": "value"}, "name": "bad", "chat_messages": [
                {"sender": "human", "text": "visible"}]},
            {"uuid": "one", "name": [], "chat_messages": [{"sender": "human", "text": "visible"}]},
            {"type": "user", "sessionId": [], "message": {"role": "user", "content": "visible"}},
            {"type": "response_item", "payload": {"type": []}},
            {"type": [], "message": {"role": "user", "content": "visible"}},
        ]
        for data in cases:
            for reader in (load_transcript, list_conversations):
                with self.subTest(reader=reader.__name__, data=data):
                    path = self.source(json.dumps(data))
                    with self.assertRaisesRegex(TranscriptError, "(?i)malformed|must be|structure"):
                        reader(path)

    def test_root_multimodal_tool_payload_cannot_bypass_content_filtering(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "response_item", "payload": {"type": "function_call", "call_id": "c",
                "name": "exec", "arguments": "{}"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c",
                "output": {"type": "image", "data": "PRIVATE-BINARY"}}},
        ]
        result = load_transcript(self.source("\n".join(map(json.dumps, records)), "selected.jsonl"))
        self.assertNotIn("PRIVATE-BINARY", repr(result))
        self.assertTrue(result.warnings)

    def test_nested_discriminator_shapes_do_not_crash_importers(self):
        from context_passport.transcripts import TranscriptError, load_transcript

        data = {"mapping": {"a": {"parent": None, "message": {"author": {"role": []},
                "content": {"content_type": "text", "parts": ["visible"]}}}}, "current_node": "a"}
        with self.assertRaisesRegex(TranscriptError, "(?i)role.*string"):
            load_transcript(self.source(json.dumps(data)))
        data["mapping"]["a"]["message"]["author"]["role"] = "assistant"
        data["mapping"]["a"]["message"]["channel"] = []
        with self.assertRaisesRegex(TranscriptError, "(?i)channel.*string"):
            load_transcript(self.source(json.dumps(data)))

    def test_arbitrary_tool_json_types_are_preserved_without_interpreting_them_as_blocks(self):
        from context_passport.transcripts import load_transcript

        path = self.source(json.dumps({"type": "response_item", "payload": {
            "type": "function_call", "call_id": "c", "name": "exec", "arguments": {
                "cmd": "pytest", "schema": {"type": ["string", "null"]}}}}), "selected.jsonl")
        result = load_transcript(path)
        self.assertEqual(json.loads(result.messages[0].text)["schema"]["type"], ["string", "null"])

    def test_json_serialized_tool_outputs_are_filtered_as_structured_data(self):
        from context_passport.transcripts import load_transcript

        records = [
            {"type": "response_item", "payload": {"type": "function_call", "call_id": "c",
                "name": "exec_command", "arguments": "{}"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c",
                "output": json.dumps({"stdout": "OK", "exit_code": 0, "reasoning": "PRIVATE-REASONING",
                                      "image": {"type": "image", "data": "PRIVATE-BINARY"}})}},
        ]
        result = load_transcript(self.source("\n".join(map(json.dumps, records)), "selected.jsonl"))
        self.assertEqual(json.loads(result.messages[-1].text), {"stdout": "OK", "exit_code": 0})
        self.assertNotIn("PRIVATE-", repr(result))

    def test_codex_array_tool_outputs_filter_private_blocks_and_preserve_values(self):
        from context_passport.transcripts import load_transcript

        private = [{"type": "reasoning", "text": "hidden reasoning"},
                   {"type": "image", "image_url": "private-image"}]
        ordinary = [1, "visible", None, {"items": ["kept"], "type": ["string", "null"]}]
        for output, expected in [
            (json.dumps(private), []),
            (json.dumps(private + ordinary), ordinary),
            (json.dumps(ordinary), ordinary),
            (private + ordinary, ordinary),
        ]:
            with self.subTest(output=output):
                records = [
                    {"type": "response_item", "payload": {"type": "function_call",
                        "call_id": "c", "name": "exec_command", "arguments": "{}"}},
                    {"type": "response_item", "payload": {"type": "function_call_output",
                        "call_id": "c", "output": output}},
                ]
                result = load_transcript(self.source(
                    "\n".join(map(json.dumps, records)), "arrays.jsonl"))
                self.assertEqual(json.loads(result.messages[-1].text), expected)
                self.assertNotIn("hidden reasoning", repr(result))
                self.assertNotIn("private-image", repr(result))
                if output != json.dumps(ordinary):
                    self.assertTrue(any("omitted" in warning for warning in result.warnings))

    def test_limit_boundaries_remain_unmodified_without_truncation_warnings(self):
        from context_passport.transcripts import load_transcript

        text = "x" * (64 * 1024)
        path = self.source(json.dumps({"chat_messages": [{"sender": "human", "text": text}]}))
        result = load_transcript(path)
        self.assertEqual(result.messages[0].text, text)
        self.assertFalse(any("truncat" in w.lower() for w in result.warnings))
        path = self.source("\n".join(json.dumps({"type": "event_msg", "payload": {
            "type": "user_message", "message": str(index)}}) for index in range(10001)), "many.jsonl")
        result = load_transcript(path)
        self.assertEqual(len(result.messages), 10000)
        self.assertEqual(result.messages[-1].id, "m10000")
        self.assertTrue(any("10000" in w and "truncat" in w.lower() for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
