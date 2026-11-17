"""Context Passport command-line interface."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import sys

from . import __version__


def _parser():
    parser = argparse.ArgumentParser(
        prog="context-passport",
        description="Carry your work between AI tools. Local, reviewable, source-backed handoffs.",
    )
    parser.add_argument("--version", action="version", version=f"Context Passport {__version__}")
    commands = parser.add_subparsers(dest="command")
    create = commands.add_parser("create", help="Create a handoff from an explicitly selected transcript")
    create.add_argument("--from", dest="source", required=True, help="Export JSON, JSONL, or text file")
    create.add_argument("--format", default="auto", choices=("auto", "claude", "claude-code", "codex", "chatgpt", "text"))
    create.add_argument("--conversation", help="Exact conversation id or title for multi-chat exports")
    create.add_argument("--repo", help="Explicit Git worktree path; omitted by default")
    create.add_argument("--no-diff", action="store_true", help="Record changed paths but omit diff text")
    create.add_argument("--title", help="Display title for the handoff")
    create.add_argument("--target", choices=("generic", "claude", "codex"), default="generic")
    create.add_argument("--out", required=True, help="Private output directory")
    create.add_argument("--goal", action="append", default=[])
    create.add_argument("--decision", action="append", default=[])
    create.add_argument("--done", action="append", default=[])
    create.add_argument("--next", dest="next_step", action="append", default=[])
    create.add_argument("--blocker", action="append", default=[])
    create.add_argument("--force", action="store_true", help="Replace the three managed outputs only")
    inspect = commands.add_parser("inspect", help="Validate and summarize a passport")
    inspect.add_argument("path", help="passport.json file or output directory")
    inspect.add_argument("--json", action="store_true", help="Emit the sanitized JSON bundle")
    prompt = commands.add_parser("prompt", help="Print a validated handoff for the next assistant")
    prompt.add_argument("path", help="passport.json file or output directory")
    prompt.add_argument("--target", choices=("generic", "claude", "codex"))
    conversations = commands.add_parser("conversations", help="List available chats without their contents")
    conversations.add_argument("path", help="Export JSON file")
    demo = commands.add_parser("demo", help="Create a passport from a bundled, explicitly synthetic session")
    demo.add_argument("--out", required=True)
    demo.add_argument("--target", choices=("generic", "claude", "codex"), default="codex")
    demo.add_argument("--force", action="store_true")
    return parser


def _sanitized(data):
    from .redact import redact_value

    # Redaction counts are validated metadata, not credential-bearing content.
    # A category named "secret" must not cause its integer count to be scrubbed.
    previous = data.get("redactions", {}).get("counts", {}) if isinstance(data, dict) else {}
    content = {key: value for key, value in data.items() if key != "redactions"} if isinstance(data, dict) else data
    clean, counts = redact_value(content)
    if isinstance(clean, dict) and isinstance(data, dict) and "redactions" in data:
        merged = Counter(previous)
        merged.update(counts)
        clean["redactions"] = {"counts": dict(merged), "total": sum(merged.values())}
    return clean


def _valid_bundle(path):
    from .core import validate_passport
    from .storage import load_json

    raw = load_json(path)
    errors = validate_passport(raw)
    if errors:
        raise ValueError("Invalid passport: " + "; ".join(errors[:4]))
    return _sanitized(raw)


def _create(args, source=None, synthetic=False):
    from .core import build_passport, validate_passport
    from .git_capture import capture_git
    from .render import render_html, render_markdown
    from .storage import write_files
    from .transcripts import load_transcript

    path = Path(source or args.source).expanduser()
    transcript = load_transcript(path, format=getattr(args, "format", "auto"), conversation=getattr(args, "conversation", None))
    git = capture_git(Path(args.repo).expanduser(), include_diff=not args.no_diff) if getattr(args, "repo", None) else None
    notes = {key: getattr(args, flag, []) for key, flag in (
        ("goal", "goal"), ("decisions", "decision"), ("completed", "done"),
        ("next_steps", "next_step"), ("blockers", "blocker"),
    )}
    # The parser already bounded the input; read at most that same limit for provenance.
    with path.open("rb") as stream:
        raw = stream.read(25 * 1024 * 1024 + 1)
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Transcript exceeds the 25 MiB input limit")
    data = build_passport(
        transcript, title=getattr(args, "title", None) or ("Notes app · synthetic demo" if synthetic else None),
        target=args.target, git=git, notes=notes, source_name=path.name,
        source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    if synthetic:
        data["warnings"].insert(0, "SYNTHETIC DEMO: conversation and historical test output are fictional examples.")
    data = _sanitized(data)
    errors = validate_passport(data)
    if errors:
        raise ValueError("Cannot export invalid passport: " + "; ".join(errors[:4]))
    payload = {
        "passport.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        "HANDOFF.md": render_markdown(data),
        "review.html": render_html(data),
    }
    output = write_files(Path(args.out).expanduser(), payload, force=args.force, protected=[path])
    print(f"Created {data['title']}")
    print(f"  {len(data['evidence'])} evidence records · {len(data['test_runs'])} recorded test runs · {data['redactions']['total']} redactions")
    for name, target in output.items():
        print(f"  {name}: {target}")
    print("Review the files before sharing. Secret detection is best-effort, not a guarantee.")
    return 0


def _dispatch(args):
    if args.command == "create":
        return _create(args)
    if args.command == "demo":
        return _create(args, source=files("context_passport").joinpath("data/demo-session.jsonl"), synthetic=True)
    if args.command == "conversations":
        from .redact import redact_value
        from .transcripts import list_conversations

        options, _ = redact_value(list_conversations(Path(args.path).expanduser()))
        print(json.dumps(options, ensure_ascii=False, indent=2))
        return 0
    data = _valid_bundle(Path(args.path).expanduser())
    if args.command == "prompt":
        from .render import render_markdown

        print(render_markdown(data, target=args.target), end="")
    elif args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"{data['title']}\nSchema {data['schema_version']} · target: {data['target']}")
        for item in data["sections"]["goal"]:
            print(f"Goal: {item['text']}")
        print(f"Evidence: {len(data['evidence'])} · recorded test runs: {len(data['test_runs'])}")
        print(f"Redactions: {data['redactions']['total']} · warnings: {len(data['warnings'])}")
        print("Validation: passed. Review source statements before continuing.")
    return 0


def main(argv=None):
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return _dispatch(args)
    except (ValueError, OSError, RecursionError) as error:
        from .redact import redact

        message = redact(str(error)).text[:700]
        print(f"context-passport: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("context-passport: cancelled", file=sys.stderr)
        return 130
