# Supported transcript formats

The importer reads the file you explicitly select; it never discovers account data or opens a browser profile. Records normalize to public `Message` objects with deterministic `m0001`-style ids and source line/JSONPath locators.

## Claude web JSON

An individual conversation object, or an export array of conversation objects:

```json
{
  "uuid": "example-conversation",
  "name": "Search feature",
  "chat_messages": [
    {"sender": "human", "text": "Build accessible search."},
    {"sender": "assistant", "text": "Decision: Use SQLite.\nNext: Add keyboard navigation."}
  ]
}
```

Visible text content blocks are also supported. Tool-use and tool-result blocks preserve call links. Attachments, hidden thinking and unsupported blocks are omitted with warnings. Legacy text is a fallback when a content block array is absent or empty, never a way to recover hidden blocks.

## Claude Code JSONL

One object per line with a `message` containing `role` and `content`. Text blocks, `tool_use` and `tool_result` blocks are normalized. `sessionId` identifies a conversation. Tool-call input is kept as text/JSON, not executed.

```jsonl
{"sessionId":"example","message":{"role":"user","content":"Fix empty search results."}}
{"sessionId":"example","message":{"role":"assistant","content":[{"type":"tool_use","id":"c1","name":"Bash","input":{"command":"python -m unittest"}}]}}
{"sessionId":"example","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"c1","content":"{\"exit_code\":0,\"output\":\"OK\"}"}]}}
```

## Codex rollout JSONL

`session_meta`, visible `response_item` messages, `function_call`, `function_call_output`, and visible `event_msg` user/agent messages are supported. Mirrored event/response messages are not duplicated. Analysis/reasoning records are omitted.

```jsonl
{"type":"session_meta","payload":{"id":"example"}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Finish local search."}]}}
{"type":"response_item","payload":{"type":"function_call","name":"shell_command","call_id":"c1","arguments":"{\"command\":\"pytest -q\"}"}}
{"type":"response_item","payload":{"type":"function_call_output","call_id":"c1","output":"{\"exit_code\":1,\"output\":\"1 failed\"}"}}
```

A recorded test run requires a linked tool call with a recognized test runner and a tool result containing a structured integer `exit_code`, or a recognized runner exit-code line. Compound shell commands are deliberately not guessed. Narrative assertions like “tests passed” remain unverified transcript statements. Unsupported test-command wrappers may remain only in evidence.

## ChatGPT export JSON

Conversation objects with `mapping` and `current_node` are supported. Only the active branch from `current_node` is followed; alternate branches are not merged. Public user/assistant/tool messages are retained; analysis/system/developer content and attachments are omitted. A broken active branch is an error rather than a fallback to unrelated content.

## Plain text and Markdown

Label visible speakers using `User:`, `Human:`, `Assistant:`, `Claude:`, or `Codex:`; Markdown headings such as `## User` are accepted. Text within code fences is kept literal.

```text
User: Build accessible local search.
Assistant: Decision: Keep data on disk.
Next steps:
- Add an empty-result state.
- Check focus order.
```

Unlabelled plain text is treated as a selected note and warned about. Explicit `--format` mismatches are rejected rather than silently converted.

## Multiple conversations

`passport conversations FILE` lists ids, titles and visible-message counts without full message contents. Use `--conversation` with an exact id or unique title. An export containing more than one conversation is never silently reduced to the first one. Duplicate titles require a unique id.

## Limits and omissions

- Maximum selected file size: 25 MiB.
- Maximum JSON nesting: 128 levels.
- Maximum retained messages: 10,000, with an explicit truncation warning.
- Maximum text per message: 64 KiB UTF-8, with an explicit truncation marker.
- Multimodal/binary/base64 content, private reasoning, system/developer instructions, and unsupported records are not carried over.
- Original uploaded files are not imported from an export; no attachment downloads occur.
- Source locators identify source positions, not cryptographic proof of the claims.

Export formats change. If an import fails, create a minimal **synthetic** reproduction for an issue. Never attach an entire private export.
