"""The core must separate literal transcript statements from execution evidence."""
from __future__ import annotations

import unittest
from types import SimpleNamespace


def message(mid: str, role: str, text: str, **kwargs):
    return SimpleNamespace(
        id=mid, role=role, text=text, locator=kwargs.get("locator", "line 1"),
        timestamp=None, kind=kwargs.get("kind", "message"),
        tool_name=kwargs.get("tool_name"), call_id=kwargs.get("call_id"),
    )


def transcript(*messages):
    return SimpleNamespace(
        format="text", messages=list(messages), conversation_id=None,
        title=None, warnings=[],
    )


class BuildPassportTests(unittest.TestCase):
    def test_first_user_goal_keeps_literal_evidence(self):
        from context_passport.core import build_passport
        source = transcript(
            message("m0001", "user", "Add a keyboard-accessible search box."),
            message("m0002", "assistant", "I will inspect the app."),
        )
        result = build_passport(source, source_name="session.md")
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["sections"]["goal"], [{
            "text": "Add a keyboard-accessible search box.",
            "evidence": ["m0001"], "basis": "user_message",
        }])
        self.assertEqual(result["source"]["name"], "session.md")
        self.assertEqual(result["evidence"][0]["text"], source.messages[0].text)
        self.assertEqual(result["test_runs"], [])

    def test_explicit_sections_and_manual_notes_keep_their_sources(self):
        from context_passport.core import build_passport
        source = transcript(
            message("m0001", "user", "Build a search page."),
            message("m0002", "assistant", "Decision: Use SQLite.\nDone: Added the index.\nNext steps:\n- Fix keyboard focus.\n- [ ] Add an empty state.\nBlocked: Missing search fixture."),
        )
        result = build_passport(source, notes={"goal": ["Finish accessible search."], "decisions": ["No hosted search service."]})
        self.assertEqual(result["sections"]["goal"][0]["text"], "Finish accessible search.")
        self.assertEqual(result["sections"]["goal"][0]["basis"], "user_note")
        decisions = result["sections"]["decisions"]
        self.assertEqual([item["text"] for item in decisions], ["Use SQLite.", "No hosted search service."])
        self.assertEqual(decisions[0]["evidence"], ["m0002"])
        self.assertEqual(decisions[0]["basis"], "transcript_statement")
        self.assertEqual([item["text"] for item in result["sections"]["next_steps"]], ["Fix keyboard focus.", "Add an empty state."])
        refs = {item["id"] for item in result["evidence"]}
        for items in result["sections"].values():
            for item in items:
                self.assertTrue(set(item["evidence"]).issubset(refs))
        self.assertEqual(result["sections"]["completed"][0]["text"], "Added the index.")
        self.assertEqual(result["sections"]["blockers"][0]["text"], "Missing search fixture.")

    def test_fenced_examples_require_matching_closers_before_extracting_sections(self):
        from context_passport.core import build_passport

        for opening, nested, closing in [
            ("````markdown", "```text\nCompleted: sample only\n```", "````"),
            ("~~~~markdown", "~~~text\nCompleted: sample only\n~~~", "~~~~"),
            ("```markdown", "~~~text\nCompleted: sample only\n~~~", "```"),
            ("~~~markdown", "```text\nCompleted: sample only\n```", "~~~"),
            ("```markdown", "```text\nCompleted: sample only\n``` trailing", "````"),
            ("~~~markdown", "~~~text\nCompleted: sample only\n~~~ trailing", "~~~~"),
        ]:
            with self.subTest(opening=opening, nested=nested):
                text = ("Decision: Keep outside.\n" + opening + "\n" + nested + "\n"
                        + closing + " \t\nCompleted: Actual work.\nNext: Verify outside.")
                result = build_passport(transcript(message("m0001", "assistant", text)))
                self.assertEqual(result["sections"]["completed"], [{
                    "text": "Actual work.", "evidence": ["m0001"],
                    "basis": "transcript_statement",
                }])
                self.assertEqual([item["text"] for item in result["sections"]["decisions"]],
                                 ["Keep outside."])
                self.assertEqual([item["text"] for item in result["sections"]["next_steps"]],
                                 ["Verify outside."])

    def test_only_linked_tool_exit_status_counts_as_a_test_run(self):
        from context_passport.core import build_passport
        source = transcript(
            message("m0001", "user", "Fix the search bug."),
            message("m0002", "assistant", "Done: All tests passed."),
            message("m0003", "assistant", '{"command":"python -m unittest discover -v"}', kind="tool_call", tool_name="terminal", call_id="call-1"),
            message("m0004", "tool", '{"output":"FAILED (failures=1)","exit_code":1}', kind="tool_result", tool_name="terminal", call_id="call-1"),
            message("m0005", "assistant", '{"command":"git status"}', kind="tool_call", tool_name="terminal", call_id="call-2"),
            message("m0006", "tool", '{"output":"clean","exit_code":0}', kind="tool_result", tool_name="terminal", call_id="call-2"),
        )
        result = build_passport(source)
        self.assertEqual(result["test_runs"], [{
            "command": "python -m unittest discover -v", "exit_code": 1,
            "status": "failed", "evidence": ["m0003", "m0004"],
        }])
        self.assertEqual(result["sections"]["completed"][0]["basis"], "transcript_statement")

    def test_background_test_command_does_not_record_a_run(self):
        from context_passport.core import build_passport

        source = transcript(
            message("m0001", "assistant", '{"command":"pytest & true"}',
                    kind="tool_call", tool_name="terminal", call_id="background-1"),
            message("m0002", "tool", '{"exit_code":0}',
                    kind="tool_result", tool_name="terminal", call_id="background-1"),
        )
        self.assertEqual(build_passport(source)["test_runs"], [])

    def test_validator_rejects_missing_fields_unknown_refs_and_wrong_types(self):
        from context_passport.core import build_passport, validate_passport
        result = build_passport(transcript(message("m0001", "user", "Build search.")))
        self.assertEqual(validate_passport(result), [])
        import copy
        invalid = copy.deepcopy(result)
        invalid["sections"]["goal"][0]["evidence"] = ["missing"]
        self.assertTrue(any("evidence" in error for error in validate_passport(invalid)))
        invalid = copy.deepcopy(result)
        invalid["schema_version"] = "99"
        self.assertTrue(validate_passport(invalid))
        self.assertTrue(validate_passport([]))
        invalid = copy.deepcopy(result)
        invalid["test_runs"] = [{"command": "pytest", "exit_code": True, "status": "passed", "evidence": ["m0001"]}]
        self.assertTrue(validate_passport(invalid))
        invalid = copy.deepcopy(result)
        invalid["evidence"].append(copy.deepcopy(invalid["evidence"][0]))
        self.assertTrue(validate_passport(invalid))
        invalid = copy.deepcopy(result)
        invalid["git"] = {"changes": "not a list"}
        self.assertTrue(validate_passport(invalid))

    def test_validator_handles_untrusted_json_types_without_crashing(self):
        from context_passport.core import build_passport, validate_passport
        import copy
        original = build_passport(transcript(message("m0001", "user", "Build search.")))
        for path, value in [
            (("target",), []),
            (("evidence", 0, "role"), {}),
            (("sections", "goal", 0, "basis"), []),
            (("redactions", "total"), False),
            (("source", "sha256"), 45),
        ]:
            data = copy.deepcopy(original)
            parent = data
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = value
            with self.subTest(path=path):
                self.assertTrue(validate_passport(data))


    def test_chinese_and_russian_markers_are_extracted_literally(self):
        from context_passport.core import build_passport
        result = build_passport(transcript(
            message("m0001", "user", "目标: 完成本地搜索。"),
            message("m0002", "assistant", "决定: 使用 SQLite。\n下一步: 添加键盘导航。\nРешение: Не использовать облако.\nДалее: Проверить тесты."),
        ))
        self.assertEqual(result["sections"]["goal"][0]["text"], "完成本地搜索。")
        self.assertEqual([x["text"] for x in result["sections"]["decisions"]], ["使用 SQLite。", "Не использовать облако."])
        self.assertEqual([x["text"] for x in result["sections"]["next_steps"]], ["添加键盘导航。", "Проверить тесты."])

    def test_redaction_categories_cannot_smuggle_arbitrary_strings(self):
        from context_passport.core import build_passport, validate_passport
        result = build_passport(transcript(message("m0001", "user", "Build search.")))
        result["redactions"] = {"total": 1, "counts": {"untrusted credential text": 1}}
        self.assertTrue(validate_passport(result))


if __name__ == "__main__":
    unittest.main()
