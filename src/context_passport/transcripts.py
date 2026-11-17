"""Import explicitly selected transcript files without executing their contents."""

from dataclasses import dataclass, field
import json
import re
from pathlib import Path


class TranscriptError(ValueError):
    """An input cannot be imported safely or needs an explicit selection."""


@dataclass(frozen=True)
class Message:
    id: str
    role: str
    text: str
    locator: str
    timestamp: str | None = None
    kind: str = "message"
    tool_name: str | None = None
    call_id: str | None = None


@dataclass
class Transcript:
    format: str
    messages: list[Message]
    conversation_id: str | None = None
    title: str | None = None
    warnings: list[str] = field(default_factory=list)


def load_transcript(
    path: Path, format: str = "auto", conversation: str | None = None
) -> Transcript:
    """Normalize a selected local conversation, retaining source locators."""
    if format not in {"auto", "claude", "claude-code", "codex", "chatgpt", "text"}:
        raise TranscriptError("Unknown format; choose auto, claude, claude-code, codex, chatgpt, or text.")
    result = _load_transcript(path, format, conversation)
    if format != "auto" and result.format != format:
        raise TranscriptError(f"Input structure does not match --format {format}; detected {result.format}.")
    if conversation is not None and conversation not in (result.conversation_id, result.title):
        raise TranscriptError("No conversation matches; use conversations to list exact ids/titles.")
    if not result.messages:
        raise TranscriptError("No visible messages; use a populated export or role-labelled user/assistant text.")
    return result


def _read(path: Path) -> str:
    try:
        if not path.is_file():
            raise TranscriptError("Cannot read selected file; select an existing regular file, not a directory.")
        if path.stat().st_size > 25 * 1024 * 1024:
            raise TranscriptError("Selected file exceeds the 25 MiB limit; export a smaller conversation.")
        with path.open("rb") as stream:
            content = stream.read(25 * 1024 * 1024 + 1)
        if len(content) > 25 * 1024 * 1024:
            raise TranscriptError("Selected file exceeds the 25 MiB limit; export a smaller conversation.")
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise TranscriptError("Selected file is not valid UTF-8; export it as UTF-8 JSON or text.") from None
    except OSError:
        raise TranscriptError("Cannot read selected file; check that it exists and is readable.") from None


def _load_transcript(path: Path, format: str, conversation: str | None) -> Transcript:
    raw = _read(path)
    if not raw.strip():
        raise TranscriptError("Empty transcript; select a populated export or role-labelled text file.")
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        return _coding(_jsonl(raw), conversation)
    if not raw.lstrip().startswith(("{", "[")):
        return _text(raw)
    try:
        data = _parse_json(raw)
    except json.JSONDecodeError as error:
        if path.suffix.lower() not in {".jsonl", ".ndjson"} and error.msg != "Extra data":
            raise TranscriptError(f"Invalid JSON at line {error.lineno}, column {error.colno}; repair the export.") from None
        return _coding(_jsonl(raw), conversation)
    if isinstance(data, dict):
        _record_fields(data, "$")
    if isinstance(data, dict) and data.get("type") in {"response_item", "event_msg", "session_meta"}:
        return _codex([(1, data)])
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        return _claude_code([(1, data)])
    data, prefix = _select(data, conversation)
    if "mapping" in data:
        return _chatgpt(data, prefix)
    result = Transcript("claude", [], data.get("uuid"), data.get("name"))
    calls: dict[str, str] = {}
    if not isinstance(data["chat_messages"], list):
        raise TranscriptError(f"{prefix}.chat_messages must be a list; export this conversation again.")
    for index, item in enumerate(data["chat_messages"]):
        location = f"{prefix}.chat_messages[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("sender"), str):
            raise TranscriptError(f"{location} must be a message object with a sender.")
        role = "user" if item["sender"] == "human" else item["sender"]
        if role not in {"user", "assistant", "tool"}:
            _warn(result, "System/developer or unsupported role messages omitted.")
            continue
        content = item.get("content", item.get("text", ""))
        if content == []:
            content = item.get("text", "")
        if item.get("attachments") or item.get("files"):
            _warn(result, "Multimodal attachments omitted.")
        if isinstance(content, list) and any(isinstance(block, dict) and
                block.get("type") in ("tool_use", "tool_result") for block in content):
            _claude_blocks(result, role, content, f"{location}.content", item.get("created_at"), calls)
        else:
            _append(result, role, _visible_text(content, result), location, item.get("created_at"))
    return result


def _parse_json(raw: str) -> object:
    try:
        data = json.loads(raw)
    except RecursionError:
        raise TranscriptError("JSON is too deeply nested; reduce the export nesting depth.") from None
    pending = [(data, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 128:
            raise TranscriptError("JSON is too deeply nested; maximum supported nesting depth is 128.")
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise TranscriptError("JSON contains invalid Unicode surrogate text; export it as valid UTF-8.") from None
        elif isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return data


def _jsonl(raw: str) -> list[tuple[int, dict]]:
    records = []
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = _parse_json(line)
        except json.JSONDecodeError as error:
            raise TranscriptError(f"Invalid JSON at line {number}, column {error.colno}; repair this JSONL record.") from None
        if not isinstance(record, dict):
            raise TranscriptError(f"line {number}: JSONL record must be an object.")
        _record_fields(record, f"line {number}: $")
        records.append((number, record))
    return records


def _record_fields(record: dict, locator: str) -> None:
    for key in ("type", "sessionId"):
        if key in record and record[key] is not None and not isinstance(record[key], str):
            raise TranscriptError(f"{locator}.{key} must be a string or null.")


def _session_groups(records: list[tuple[int, dict]]) -> dict[str | None, list[tuple[int, dict]]]:
    groups: dict[str | None, list[tuple[int, dict]]] = {}
    current: str | None = None
    for number, record in records:
        session = record.get("sessionId")
        if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
            session = record["payload"].get("id")
        if isinstance(session, str):
            current = session
        groups.setdefault(current, []).append((number, record))
    if None in groups and len(groups) > 1:
        preamble = groups.pop(None)
        first = next(iter(groups))
        groups[first] = preamble + groups[first]
    return groups


def _coding(records: list[tuple[int, dict]], conversation: str | None = None) -> Transcript:
    groups = _session_groups(records)
    if len(groups) > 1:
        if conversation is None:
            raise TranscriptError("Multiple conversations; select an exact id with --conversation.")
        if conversation not in groups:
            raise TranscriptError("No conversation matches; use conversations to list exact ids/titles.")
        records = groups[conversation]
    if any(record.get("type") in {"response_item", "event_msg", "session_meta"}
           for _, record in records):
        return _codex(records)
    if any("message" in record for _, record in records):
        return _claude_code(records)
    raise TranscriptError("Unsupported JSONL structure; select a Claude Code or Codex rollout transcript.")


def _append(result: Transcript, role: str, text: str, locator: str,
            timestamp: str | None = None, kind: str = "message",
            tool_name: str | None = None, call_id: str | None = None) -> None:
    text, binary_omitted = re.subn(r"data:[a-z0-9.+/-]+(?:;[^,\s]*)?;base64,[a-z0-9+/=_-]+",
                                    "[OMITTED BINARY DATA]", text, flags=re.IGNORECASE)
    if binary_omitted:
        _warn(result, "Inline binary/base64 data omitted.")
    if role == "assistant":
        text, omitted = re.subn(
            r"<(think|thinking|reasoning|analysis)\b[^>]*>.*?(?:</\1\s*>|\Z)", "", text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if omitted:
            _warn(result, "Tagged hidden thinking/reasoning text omitted.")
            text = text.strip()
    if text:
        if len(result.messages) >= 10000:
            _warn(result, "Transcript truncated to the 10000 message limit; remaining messages omitted.")
            return
        encoded = text.encode("utf-8")
        if len(encoded) > 64 * 1024:
            suffix = "\n[TRUNCATED]"
            text = encoded[:64 * 1024 - len(suffix)].decode("utf-8", errors="ignore") + suffix
            _warn(result, "Message text truncated to the 64 KiB UTF-8 limit.")
        if timestamp is not None and not isinstance(timestamp, str):
            timestamp = str(timestamp)
        result.messages.append(Message(f"m{len(result.messages) + 1:04d}", role, text,
                                       locator, timestamp, kind, tool_name, call_id))


def _claude_code(records: list[tuple[int, dict]]) -> Transcript:
    result = Transcript("claude-code", [])
    calls: dict[str, str] = {}
    for number, record in records:
        if record.get("sessionId"):
            result.conversation_id = record["sessionId"]
        message = record.get("message")
        if not isinstance(message, dict):
            if record.get("type") in {"user", "assistant"} or "message" in record:
                raise TranscriptError(f"line {number}: $.message must be an object.")
            _warn(result, "Metadata or unsupported JSONL records omitted.")
            continue
        role = message.get("role")
        if not isinstance(role, str):
            raise TranscriptError(f"line {number}: $.message.role must be a string.")
        if role not in {"user", "assistant", "tool"}:
            _warn(result, "System/developer or unsupported role messages omitted.")
            continue
        content = message.get("content", "")
        locator = f"line {number}: $.message.content"
        timestamp = record.get("timestamp")
        _claude_blocks(result, role, content, locator, timestamp, calls)
    return result


def _claude_blocks(result: Transcript, role: str, content: object, locator: str,
                   timestamp: str | None, calls: dict[str, str]) -> None:
    if isinstance(content, str):
        _append(result, role, content, locator, timestamp)
        return
    if not isinstance(content, list):
        raise TranscriptError(f"{locator} must be text or a list of content blocks.")
    for index, block in enumerate(content):
        location = f"{locator}[{index}]"
        if not isinstance(block, dict):
            raise TranscriptError(f"{location} must be a content block object.")
        if block.get("type") == "tool_use":
            call_id, name = block.get("id"), block.get("name")
            if not isinstance(call_id, str) or not call_id:
                raise TranscriptError(f"{location}.id must be a nonempty string.")
            if not isinstance(name, str) or not name:
                raise TranscriptError(f"{location}.name must be a nonempty string.")
            if call_id:
                calls[call_id] = name
            _append(result, "assistant", json.dumps(_safe_tool_value(block.get("input", {}), result), ensure_ascii=False),
                    location, timestamp, "tool_call", name, call_id)
        elif block.get("type") == "tool_result":
            call_id = block.get("tool_use_id")
            if not isinstance(call_id, str) or not call_id:
                raise TranscriptError(f"{location}.tool_use_id must be a nonempty string.")
            if call_id not in calls:
                _warn(result, "Unmatched tool result without a preceding tool call; cannot verify execution.")
            _append(result, "tool", _visible_text(block.get("content", ""), result),
                    location, timestamp, "tool_result", calls.get(call_id), call_id)
        else:
            _append(result, role, _visible_text([block], result), location, timestamp)


def _codex(records: list[tuple[int, dict]]) -> Transcript:
    result = Transcript("codex", [])
    calls: dict[str, str] = {}
    mirrors: dict[tuple[str, str, str], int] = {}

    def visible(role: str, text: str, locator: str, timestamp: str | None, source: str) -> None:
        opposite = "event" if source == "response" else "response"
        key = (role, text, opposite)
        if mirrors.get(key, 0):
            mirrors[key] -= 1
            _warn(result, "Duplicate event/response message mirrors omitted.")
        else:
            # Only adjacent visible turns may be event/response mirrors.
            mirrors.clear()
            key = (role, text, source)
            mirrors[key] = 1
            _append(result, role, text, locator, timestamp)

    for number, record in records:
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            raise TranscriptError(f"line {number}: $.payload must be an object.")
        if record.get("type") in {"response_item", "event_msg"} and not isinstance(payload.get("type"), str):
            raise TranscriptError(f"line {number}: $.payload.type must be a string.")
        if record.get("type") == "session_meta":
            result.conversation_id = payload.get("id")
            continue
        if record.get("type") == "event_msg":
            kind = payload.get("type")
            if kind in {"user_message", "agent_message"}:
                if payload.get("images") or payload.get("local_images") or payload.get("attachments"):
                    _warn(result, "Multimodal attachments omitted.")
                role = "user" if kind == "user_message" else "assistant"
                visible(role, _visible_text(payload.get("message", ""), result),
                        f"line {number}: $.payload.message", record.get("timestamp"), "event")
            elif kind in {"agent_reasoning", "reasoning", "thinking"}:
                _warn(result, "Hidden reasoning event messages omitted.")
            continue
        if record.get("type") != "response_item":
            continue
        kind = payload.get("type")
        locator = f"line {number}: $.payload"
        timestamp = record.get("timestamp")
        if kind == "message":
            if payload.get("channel") is not None and not isinstance(payload["channel"], str):
                raise TranscriptError(f"{locator}.channel must be a string or null.")
            if not isinstance(payload.get("role"), str):
                raise TranscriptError(f"{locator}.role must be a string.")
            if payload.get("channel") not in {None, "final", "commentary"}:
                _warn(result, "Hidden reasoning or unsupported channels omitted.")
                continue
            role = payload.get("role")
            if role in {"user", "assistant", "tool"}:
                visible(role, _visible_text(payload.get("content", []), result),
                        locator, timestamp, "response")
            else:
                _warn(result, "System/developer or unsupported role messages omitted.")
        elif kind == "function_call":
            call_id, name = payload.get("call_id"), payload.get("name")
            if not isinstance(call_id, str) or not call_id:
                raise TranscriptError(f"{locator}.call_id must be a nonempty string.")
            if not isinstance(name, str) or not name:
                raise TranscriptError(f"{locator}.name must be a nonempty string.")
            if call_id:
                calls[call_id] = name
            arguments = payload.get("arguments", "")
            if isinstance(arguments, str):
                try:
                    structured = _parse_json(arguments)
                except json.JSONDecodeError:
                    structured = None
                if isinstance(structured, (dict, list)):
                    arguments = json.dumps(_safe_tool_value(structured, result), ensure_ascii=False)
            else:
                arguments = json.dumps(_safe_tool_value(arguments, result), ensure_ascii=False)
            _append(result, "assistant", arguments, locator, timestamp, "tool_call", name, call_id)
        elif kind == "function_call_output":
            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise TranscriptError(f"{locator}.call_id must be a nonempty string.")
            if call_id not in calls:
                _warn(result, "Unmatched tool result without a preceding tool call; cannot verify execution.")
            output = payload.get("output", "")
            if isinstance(output, str):
                try:
                    structured_output = _parse_json(output)
                except json.JSONDecodeError:
                    structured_output = None
                if isinstance(structured_output, (dict, list)):
                    output = structured_output
            text = (json.dumps(_safe_tool_value(output, result), ensure_ascii=False)
                    if isinstance(output, (dict, list)) else _visible_text(output, result))
            _append(result, "tool", text, locator, timestamp, "tool_result", calls.get(call_id), call_id)
        else:
            _warn(result, "Hidden reasoning or unsupported response items omitted.")
    return result


def _chatgpt(data: dict, prefix: str) -> Transcript:
    result = Transcript("chatgpt", [], data.get("id", data.get("conversation_id")), data.get("title"))
    mapping = data["mapping"]
    if not isinstance(mapping, dict):
        raise TranscriptError("ChatGPT mapping must be an object; export this conversation again.")
    current = data.get("current_node")
    if not isinstance(current, str) or current not in mapping:
        raise TranscriptError("ChatGPT current_node is missing or invalid; export the active conversation again.")
    branch = []
    seen = set()
    while current is not None:
        if not isinstance(current, str) or current not in mapping:
            raise TranscriptError("ChatGPT active branch has a missing parent node; repair or re-export it.")
        if current in seen:
            raise TranscriptError("ChatGPT active branch contains a parent cycle; repair or re-export it.")
        seen.add(current)
        node = mapping[current]
        if not isinstance(node, dict):
            raise TranscriptError("ChatGPT active branch node must be an object.")
        branch.append((current, node))
        current = node.get("parent")
    for key, node in reversed(branch):
        message = node.get("message")
        if message is None:
            continue
        location = f"{prefix}.mapping[{json.dumps(key)}].message"
        if not isinstance(message, dict):
            raise TranscriptError(f"{location} must be an object or null.")
        author = message.get("author", {})
        if not isinstance(author, dict):
            raise TranscriptError(f"{location}.author must be an object.")
        role = author.get("role")
        if not isinstance(role, str):
            raise TranscriptError(f"{location}.author.role must be a string.")
        if message.get("channel") is not None and not isinstance(message["channel"], str):
            raise TranscriptError(f"{location}.channel must be a string or null.")
        if role not in {"user", "assistant", "tool"}:
            _warn(result, "System/developer or unsupported role messages omitted.")
            continue
        if message.get("channel") not in {None, "final", "commentary"}:
            _warn(result, "Hidden reasoning or unsupported channels omitted.")
            continue
        content = message.get("content", {})
        if not isinstance(content, dict):
            raise TranscriptError(f"{location}.content must be an object.")
        if content.get("content_type") != "text":
            _warn(result, "Multimodal attachments or unsupported content omitted.")
            continue
        raw_parts = content.get("parts", [])
        if not isinstance(raw_parts, list):
            raise TranscriptError(f"{location}.content.parts must be a list.")
        parts = []
        for part in raw_parts:
            if isinstance(part, str):
                parts.append(part)
            else:
                _warn(result, "Multimodal attachments omitted.")
        _append(result, role, "\n".join(parts),
                f"{prefix}.mapping[{json.dumps(key)}].message", message.get("create_time"))
    return result


_ROLE_LABEL = re.compile(
    r"^(?:\*\*|__)?(user|human|assistant|claude|codex|chatgpt|system|developer|tool|thinking|reasoning|analysis)"
    r"(?:(?:\*\*|__)?\s*:\s*(?:\*\*|__)?(.*)|(?:\*\*|__)?\s*)$",
    re.IGNORECASE,
)


def _text(raw: str) -> Transcript:
    result = Transcript("text", [])
    role: str | None = None
    parts: list[str] = []
    start = 1
    fence: str | None = None

    def flush(end: int) -> None:
        if role in {"user", "assistant", "tool"}:
            _append(result, role, "\n".join(parts).strip(), f"line {start}-{end}")
        elif role:
            _warn(result, "System/developer or unsupported role messages omitted.")

    lines = raw.splitlines()
    for number, line in enumerate(lines, 1):
        fence_match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            parts.append(line)
            continue
        label = re.sub(r"^\s{0,3}#{1,6}\s+", "", line.strip())
        match = _ROLE_LABEL.match(label) if fence is None else None
        if match:
            flush(number - 1)
            role = match.group(1).lower()
            role = {"human": "user", "claude": "assistant", "codex": "assistant",
                    "chatgpt": "assistant"}.get(role, role)
            inline = match.group(2) or ""
            start = number if inline else number + 1
            parts = [inline] if inline else []
        elif role:
            parts.append(line)
        elif line.strip():
            _warn(result, "Unlabelled transcript preamble omitted.")
    flush(len(lines))
    return result


def list_conversations(path: Path) -> list[dict]:
    """Return only identifiers, titles and counts for a selected export."""
    raw = _read(path)
    try:
        data = _parse_json(raw)
    except json.JSONDecodeError as error:
        data = None
        if error.msg == "Extra data" or path.suffix.lower() in {".jsonl", ".ndjson"}:
            groups = _session_groups(_jsonl(raw))
            listing = []
            for records in groups.values():
                result = _coding(records)
                listing.append({"id": result.conversation_id, "title": result.title,
                                "message_count": len(result.messages)})
            return listing
    if isinstance(data, (list, dict)) and not (
        isinstance(data, dict) and ("message" in data or "type" in data)
    ):
        conversations = data if isinstance(data, list) else [data]
        if not conversations:
            raise TranscriptError("Empty export: no conversations to list.")
        listing = []
        for item in conversations:
            _select(item, None)
            if "chat_messages" in item:
                messages = item["chat_messages"]
                if not isinstance(messages, list):
                    raise TranscriptError("$.chat_messages must be a list.")
                if any(not isinstance(message, dict) or not isinstance(message.get("sender"), str)
                       for message in messages):
                    raise TranscriptError("$.chat_messages contains a malformed message object or sender.")
            listing.append({"id": item.get("uuid", item.get("id", item.get("conversation_id"))),
                            "title": item.get("name", item.get("title")),
                            "message_count": len(_chatgpt(item, "$").messages) if "mapping" in item
                            else len(item["chat_messages"])})
        return listing
    result = load_transcript(path)
    return [{"id": result.conversation_id, "title": result.title,
             "message_count": len(result.messages)}]


def _select(data: object, conversation: str | None) -> tuple[dict, str]:
    choices = list(enumerate(data)) if isinstance(data, list) else [(None, data)]
    if not choices:
        raise TranscriptError("Empty export: no conversations to select.")
    if any(not isinstance(item, dict) or not ("chat_messages" in item or "mapping" in item)
           for _, item in choices):
        raise TranscriptError("Unsupported JSON structure; use a Claude/ChatGPT export or coding-tool JSONL.")
    for _, item in choices:
        for key in ("uuid", "id", "conversation_id", "name", "title"):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                raise TranscriptError(f"Conversation {key} must be a string or null.")
    if conversation is None:
        if len(choices) > 1:
            raise TranscriptError("Multiple conversations; select an exact id or title with --conversation.")
    else:
        choices = [(index, item) for index, item in choices
                   if conversation in (item.get("uuid", item.get("id", item.get("conversation_id"))),
                                       item.get("name", item.get("title")))]
        if not choices:
            raise TranscriptError("No conversation matches; use conversations to list exact ids/titles.")
    if len(choices) > 1:
        raise TranscriptError("Ambiguous conversation title; select a unique conversation id.")
    index, item = choices[0]
    return item, "$" if index is None else f"$[{index}]"


def _warn(result: Transcript, message: str) -> None:
    if message not in result.warnings:
        result.warnings.append(message)


_OMITTED_KEYS = {"thinking", "reasoning", "analysis", "signature", "encrypted_content",
                 "image", "images", "image_url", "audio", "video", "attachments",
                 "attachment", "base64", "b64_json", "binary", "file_data"}
_OMITTED_TYPES = {"thinking", "redacted_thinking", "reasoning", "image", "input_image",
                  "output_image", "audio", "input_audio", "video", "document", "file", "base64"}


def _safe_tool_value(value: object, result: Transcript) -> object:
    if isinstance(value, dict):
        if isinstance(value.get("type"), str) and value["type"] in _OMITTED_TYPES:
            _warn(result, "Hidden reasoning or binary/multimodal tool data omitted.")
            return "[OMITTED CONTENT]"
        safe = {}
        for key, item in value.items():
            if key.lower() in _OMITTED_KEYS or (
                isinstance(item, dict) and isinstance(item.get("type"), str) and item["type"] in _OMITTED_TYPES
            ):
                _warn(result, "Hidden reasoning or binary/multimodal tool data omitted.")
            else:
                safe[key] = _safe_tool_value(item, result)
        return safe
    if isinstance(value, list):
        safe_items = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("type"), str) and item["type"] in _OMITTED_TYPES:
                _warn(result, "Hidden reasoning or binary/multimodal tool data omitted.")
            else:
                safe_items.append(_safe_tool_value(item, result))
        return safe_items
    return value


def _visible_text(value: object, result: Transcript) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts = []
        for block in value:
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                raise TranscriptError("Malformed content block: type must be a string.")
            if isinstance(block, dict) and block.get("type") in {"text", "input_text", "output_text"}:
                if isinstance(block.get("text"), str):
                    texts.append(block["text"])
            else:
                _warn(result, "Hidden reasoning, attachments, or unsupported content blocks omitted.")
        return "\n".join(texts)
    raise TranscriptError("Message content must be text or a list of content blocks.")
