"""Build explicit evidence-backed handoffs without an LLM or network access."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import json
import re
import shlex

SECTION_NAMES = ("goal", "decisions", "completed", "next_steps", "blockers")
SECTION_ALIASES = {
    "goal": "goal", "goals": "goal", "objective": "goal", "目标": "goal", "цель": "goal",
    "decision": "decisions", "decisions": "decisions", "决定": "decisions", "решение": "decisions",
    "done": "completed", "completed": "completed", "完成": "completed", "готово": "completed",
    "next": "next_steps", "next step": "next_steps", "next steps": "next_steps",
    "todo": "next_steps", "next_steps": "next_steps", "下一步": "next_steps", "далее": "next_steps",
    "blocked": "blockers", "blocker": "blockers", "blockers": "blockers",
    "阻塞": "blockers", "препятствие": "blockers",
}


def _literal_sections(evidence):
    sections = {name: [] for name in SECTION_NAMES}
    for message in evidence:
        if message["role"] not in {"user", "assistant"} or message["kind"] != "message":
            continue
        active = None
        fence = None
        for line in message["text"].splitlines():
            raw = line.strip()
            fence_match = re.match(r"^(`{3,}|~{3,})(.*)$", raw)
            if fence_match:
                marker, remainder = fence_match.groups()
                if fence is None:
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence) and not remainder.strip():
                    fence = None
                active = None
                continue
            if fence is not None:
                continue
            match = re.match(r"^(?:#{1,6}\s*)?(?:\*\*)?([^:*#]{1,30})(?:\*\*)?\s*:\s*(.*)$", raw)
            heading = raw.lstrip("#").strip().strip("*").casefold()
            if match and match.group(1).strip().casefold() in SECTION_ALIASES:
                active = SECTION_ALIASES[match.group(1).strip().casefold()]
                text = match.group(2).strip()
            elif raw.startswith("#") and heading in SECTION_ALIASES:
                active = SECTION_ALIASES[heading]
                text = ""
            elif active and re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", raw):
                text = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", raw)
            else:
                active = None
                continue
            checkbox = re.match(r"^\[([ xX])\]\s*(.*)$", text)
            destination = active
            if checkbox:
                destination = "completed" if checkbox.group(1).casefold() == "x" else "next_steps"
                text = checkbox.group(2)
            if text:
                item = {"text": text, "evidence": [message["id"]], "basis": "transcript_statement"}
                if item not in sections[destination]:
                    sections[destination].append(item)
    return sections



def _test_command(text):
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    command = payload.get("command", payload.get("cmd"))
    if not isinstance(command, str):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words:
        return None
    # Recognize a single runner command, never a quoted claim or a compound shell script.
    if re.search(r"(?:&&|\|\||[;&|\n`]|\$\()", command):
        return None
    executable = Path(words[0]).name
    direct = executable in {"pytest", "pytest.exe", "vitest", "jest", "nosetests"}
    python = executable.startswith("python") and len(words) > 2 and words[1] == "-m" and words[2] in {"pytest", "unittest"}
    package = executable in {"npm", "pnpm", "yarn", "bun"} and len(words) > 1 and (words[1] == "test" or words[1:3] == ["run", "test"])
    native = executable in {"cargo", "go", "dotnet"} and len(words) > 1 and words[1] == "test"
    return command if direct or python or package or native else None


def _tool_test_runs(evidence):
    calls = {}
    runs = []
    for item in evidence:
        if item["kind"] == "tool_call" and item.get("call_id"):
            command = _test_command(item["text"])
            if command:
                calls[item["call_id"]] = (item["id"], command)
        if item["kind"] != "tool_result" or item.get("call_id") not in calls:
            continue
        text = item["text"]
        try:
            output = json.loads(text)
        except (ValueError, TypeError):
            output = None
        code = output.get("exit_code") if isinstance(output, dict) else None
        if type(code) is not int:
            matched = re.search(r"(?mi)^\s*(?:Process exited with code|Exit code:)\s*(-?\d+)\s*$", text)
            code = int(matched.group(1)) if matched else None
        if code is None:
            continue
        call_id, command = calls.pop(item["call_id"])
        runs.append({"command": command, "exit_code": code,
                     "status": "passed" if code == 0 else "failed",
                     "evidence": [call_id, item["id"]]})
    return runs


def build_passport(
    transcript, title=None, target="generic", git=None, notes=None,
    source_name="transcript", source_sha256="",
):
    """Capture literal statements and their source references, never inferred facts."""
    evidence = [
        {name: getattr(message, name) for name in (
            "id", "role", "text", "locator", "timestamp", "kind", "tool_name", "call_id"
        )}
        for message in transcript.messages
    ]
    sections = _literal_sections(evidence)
    first_user = next((item for item in evidence if item["role"] == "user" and item["kind"] == "message"), None)
    if first_user and not sections["goal"]:
        sections["goal"].append({
            "text": first_user["text"], "evidence": [first_user["id"]],
            "basis": "user_message",
        })
    counter = 0
    for label, values in (notes or {}).items():
        section = SECTION_ALIASES.get(label.casefold())
        if section is None:
            raise ValueError(f"Unknown note section: {label}")
        if section == "goal" and values:
            sections[section] = []
        for text in values:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Notes must be nonempty strings")
            counter += 1
            reference = f"n{counter:04d}"
            evidence.append({"id": reference, "role": "user", "text": text.strip(),
                             "locator": f"user note:{section}", "timestamp": None,
                             "kind": "user_note", "tool_name": None, "call_id": None})
            sections[section].append({"text": text.strip(), "evidence": [reference], "basis": "user_note"})
    return {
        "schema_version": "1.0", "id": "cp_" + uuid4().hex,
        "title": title or transcript.title or "Untitled handoff",
        "created_at": datetime.now(timezone.utc).isoformat(), "target": target,
        "source": {
            "name": Path(source_name).name, "sha256": source_sha256,
            "format": transcript.format, "conversation_id": transcript.conversation_id,
            "message_count": len(transcript.messages),
        },
        "sections": sections, "test_runs": _tool_test_runs(evidence), "git": git,
        "evidence": evidence, "redactions": {"total": 0, "counts": {}},
        "warnings": list(transcript.warnings) + [
            "Transcript statements are literal, unverified claims; review source evidence.",
            "A zero exit code records the test runner outcome, not independent verification of assertions.",
        ],
    }


def validate_passport(data):
    """Return schema errors, without executing or rendering any supplied content."""
    errors = []
    if not isinstance(data, dict):
        return ["passport must be a JSON object"]
    if data.get("schema_version") != "1.0":
        errors.append("unsupported schema_version; expected 1.0")
    for key in ("id", "title", "created_at", "target"):
        if not isinstance(data.get(key), str) or not data[key]:
            errors.append(f"{key} must be a nonempty string")
    if data.get("target") not in ("generic", "claude", "codex"):
        errors.append("target must be generic, claude, or codex")
    source = data.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("name"), str) or not isinstance(source.get("format"), str):
        errors.append("source must have string name and format")
    elif type(source.get("message_count")) is not int or source["message_count"] < 0:
        errors.append("source.message_count must be a nonnegative integer")
    if isinstance(source, dict):
        digest = source.get("sha256")
        if not isinstance(digest, str) or (digest and not re.fullmatch(r"[0-9a-f]{64}", digest)):
            errors.append("source.sha256 must be an empty string or a SHA-256 digest")
        if source.get("conversation_id") is not None and not isinstance(source["conversation_id"], str):
            errors.append("source.conversation_id must be a string or null")
    evidence = data.get("evidence")
    ids = set()
    if not isinstance(evidence, list) or len(evidence) > 11000:
        errors.append("evidence must be a bounded list")
        evidence = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"evidence[{index}] must be an object")
            continue
        identity = item.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[mn][0-9]{4,6}", identity) or identity in ids:
            errors.append(f"evidence[{index}] has invalid or duplicate id")
        else:
            ids.add(identity)
        if item.get("role") not in ("user", "assistant", "tool"):
            errors.append(f"evidence[{index}] has unsupported role")
        for key in ("text", "locator", "kind"):
            if not isinstance(item.get(key), str):
                errors.append(f"evidence[{index}].{key} must be a string")
        for key in ("timestamp", "tool_name", "call_id"):
            if item.get(key) is not None and not isinstance(item[key], str):
                errors.append(f"evidence[{index}].{key} must be a string or null")
        if isinstance(item.get("text"), str) and len(item["text"]) > 131072:
            errors.append(f"evidence[{index}].text is too large")

    def references(value, context):
        if not isinstance(value, list) or not value or any(not isinstance(ref, str) or ref not in ids for ref in value):
            errors.append(f"{context} has invalid evidence references")

    sections = data.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(SECTION_NAMES):
        errors.append("sections must contain goal, decisions, completed, next_steps, blockers")
        sections = {}
    for name, items in sections.items():
        if not isinstance(items, list) or len(items) > 10000:
            errors.append(f"sections.{name} must be a bounded list")
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                errors.append(f"sections.{name} has an invalid item")
                continue
            if item.get("basis") not in ("user_note", "user_message", "transcript_statement"):
                errors.append(f"sections.{name} has invalid basis")
            references(item.get("evidence"), f"sections.{name}")
    runs = data.get("test_runs")
    if not isinstance(runs, list):
        errors.append("test_runs must be a list")
        runs = []
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("command"), str):
            errors.append("test_runs has an invalid item")
            continue
        code = run.get("exit_code")
        if type(code) is not int or run.get("status") != ("passed" if code == 0 else "failed"):
            errors.append("test_runs has an inconsistent exit_code/status")
        references(run.get("evidence"), "test_runs")
    git = data.get("git")
    if git is not None:
        if not isinstance(git, dict):
            errors.append("git must be an object or null")
        else:
            if not isinstance(git.get("root_name"), str) or type(git.get("dirty")) is not bool or not isinstance(git.get("diff"), str):
                errors.append("git has invalid root_name, dirty, or diff")
            for key in ("branch", "head"):
                if git.get(key) is not None and not isinstance(git[key], str):
                    errors.append(f"git.{key} must be a string or null")
            changes = git.get("changes")
            if not isinstance(changes, list):
                errors.append("git.changes must be a list")
            else:
                for item in changes:
                    if not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in ("path", "index_status", "worktree_status")):
                        errors.append("git.changes has an invalid item")
            for key in ("warnings", "excluded_paths"):
                if not isinstance(git.get(key), list) or any(not isinstance(v, str) for v in git.get(key, [])):
                    errors.append(f"git.{key} must be a list of strings")
    redactions = data.get("redactions")
    if not isinstance(redactions, dict) or type(redactions.get("total")) is not int or not isinstance(redactions.get("counts"), dict):
        errors.append("redactions must contain total and counts")
    elif redactions["total"] < 0 or any(not isinstance(k, str) or k not in {"private_key", "github", "openai", "google", "aws_access_key", "jwt", "telegram", "authorization", "url_credentials", "secret"} or type(v) is not int or v < 0 for k, v in redactions["counts"].items()) or sum(redactions["counts"].values()) != redactions["total"]:
        errors.append("redactions counts are inconsistent")
    warnings = data.get("warnings")
    if not isinstance(warnings, list) or any(not isinstance(value, str) for value in warnings):
        errors.append("warnings must be a list of strings")
    return errors
