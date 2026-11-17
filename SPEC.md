# Context Passport v0.1 implementation contract

A local, network-free Python 3.10+ CLI creating reviewable, source-backed handoffs between AI coding tools. No LLM calls; no auto-discovery of private chat directories. User explicitly selects one transcript. No commands from transcript or repository are executed. Public owner: PedramNoSkill. Package: `context_passport`, CLI `context-passport` and `passport`.

## Responsibilities and contracts

`src/context_passport/transcripts.py`:
- `class TranscriptError(ValueError)`
- `@dataclass(frozen=True) class Message`: `id: str`, `role: str`, `text: str`, `locator: str`, `timestamp: str | None = None`, `kind: str = "message"`, `tool_name: str | None = None`, `call_id: str | None = None`.
- `@dataclass class Transcript`: `format: str`, `messages: list[Message]`, `conversation_id: str | None = None`, `title: str | None = None`, `warnings: list[str] = field(default_factory=list)`.
- `load_transcript(path: Path, format: str = "auto", conversation: str | None = None) -> Transcript`
- `list_conversations(path: Path) -> list[dict]` returning `id`, `title`, `message_count` (never full text).
- Formats: Claude web conversation JSON / export array with chat_messages; Claude Code JSONL records with message.role and content blocks; Codex rollout JSONL response_item with message/function_call/function_call_output and event_msg records; ChatGPT export mapping (optional if feasible); plain role-labelled Markdown/text.
- JSON multi-conversation exports require explicit exact `--conversation` id or exact title; never silently select first. Unknown/empty/malformed inputs fail with actionable errors, invalid JSON lines report line number. Explicit format unsupported structure errors.
- Use source line/JSONPath locators; IDs `m0001`, `m0002`, ... deterministic. Include useful tool input/output text for matching commands/results, call_id for linking. Do not include `thinking`/`reasoning` blocks, binary/base64/image data, system/developer instructions, or multimodal attachments. Warn if omitted/truncated. Bound file <=25MiB, text/message <=64KiB, message count <=10000; truncation explicit. Do not silently lose huge input.

`src/context_passport/redact.py`:
- `@dataclass class RedactionResult`: `text: str`, `counts: dict[str,int]`, property `total`.
- `redact(text: str) -> RedactionResult` detects credential-like spans, replaces with `[REDACTED:<kind>]`, idempotent, never puts original secret in report.
- `redact_value(value: JSON-compatible) -> tuple[value, dict[str,int]]` recursively returns a sanitized copy, including secret values under sensitive dict keys. Preserve types except credential values needing replacement. Count redactions exactly once per replaced span. Cover private PEM keys, GitHub/OpenAI/Google/AWS access IDs, JWT, Telegram bot tokens, Authorization headers, URL userinfo/passwords, connection strings, secret assignments (password/token/api_key/secret/client_secret including JSON quoted keys). Never execute input or use a network service; best-effort not a guarantee. Do not over-redact normal statements such as 'no API key needed'.

`src/context_passport/git_capture.py`:
- `class GitCaptureError(ValueError)`
- `capture_git(repo: Path, max_diff_bytes: int = 65536, include_diff: bool = True) -> dict`
- Return `{root_name, branch, head, dirty, changes, diff, excluded_paths, warnings}`. branch/head nullable for unborn branch, root_name basename only. changes list `{path, index_status, worktree_status, previous_path?}` from porcelain -z; changed, staged, untracked, deleted, renamed, spaces/unicode supported. Correctly mark unborn dirty repos.
- Read-only Git commands only; shell=False, subprocess deadlines; disable fsmonitor/hooks/external diff/textconv and no optional locks. Never execute repository code. Only tracked text diff; do not read untracked file contents. Exclude known secret-bearing paths (`.env`, `.env.*` except example/template, private keys *.pem/*.key, credentials/auth/tokens files, ~/.ssh etc) from diff and report them as excluded. Exclude symlinks as contents. Never git add/commit/fetch/remote/push. Bound capture and make truncation explicit. Use NUL status and literal pathspecs. Avoid leaking remote URLs or absolute paths. Parent runs redaction across result as second defense.

`src/context_passport/core.py` (parent-owned):
- `build_passport(transcript, title=None, target="generic", git=None, notes=None, source_name="transcript", source_sha256="") -> dict`
- `notes` mapping singular/plural section names to lists of text (`goal`, `decisions`, `completed`, `next_steps`, `blockers`); user notes are evidence entries too.
- Schema 1.0 top-level `{schema_version, id, title, created_at, target, source, sections, test_runs, git, evidence, redactions, warnings}`.
- sections keys `goal`, `decisions`, `completed`, `next_steps`, `blockers`, each list `{text, evidence:[id], basis:"user_note"|"user_message"|"transcript_statement"}`.
- evidence list normalized message dicts; no hidden reasoning. Goal first human request unless explicit Goal marker / manual goal. Extract explicit Goal/Decision/Done/Next/Blocked headings and checkbox tasks. Deterministic literal extraction, no invented paraphrases; state limitations. Test runs only matched tool calls/results with structured exit code, status `passed`/`failed` based on exit; distinguish success of runner vs actual tests, only known test commands. Report assistant claims in narrative only.
- All export strings recursively redacted, sources only explicitly selected input basename+hash, no original transcript copied.
- `validate_passport(data) -> list[str]` validates required schema, types, evidence references, sections, git/test shapes and limits. Strictly reject unsupported versions; handoff import prompt renders only valid bundles.

`src/context_passport/render.py` (parent-owned):
- `render_markdown(data,target=None)->str`, `render_html(data)->str`: direct model-neutral, Claude/Codex handoff instruction emphasizing quoted evidence/untrusted input and no automatic command execution. Self-contained offline HTML with search/filter/collapsible evidence, no CDN/scripts fetched. Escape untrusted content (including embedded JSON/script-breakout) and Markdown fences.

`src/context_passport/cli.py` (parent-owned):
- `main(argv=None)->int`, argparse, actionable bounded errors no raw traceback in ordinary usage.
- `create --from PATH [--format auto|claude|claude-code|codex|chatgpt|text] [--conversation ID] [--repo PATH] [--no-diff] [--title TEXT] [--target generic|claude|codex] --out DIR [--goal TEXT] [--decision TEXT repeat] [--done TEXT repeat] [--next TEXT repeat] [--blocker TEXT repeat] [--force]`.
- No --repo = omit Git, no implicit current-directory capture. Reject output path/source collisions, symlink destinations, existing bundle files unless --force. Atomic guarded writes, permissions 0700 dir / 0600 files when platform permits, never leave unsanitized files. Produce passport.json, HANDOFF.md, review.html. --force only those three named files, not directory removal.
- `inspect PATH [--json]` validates and summarizes bundle; redacts displayed input; does not execute HTML.
- `prompt PATH [--target ...]` prints a fresh validated safe handoff; no host integration or injection into another app. User pastes/adds file.
- `conversations PATH` lists choices for export files.
- `demo --out DIR` runs real capture on bundled synthetic fixture (marked synthetic), output must match actual implementation.

## Testing/development

Strict vertical RED/GREEN: write one failing behavior test, run it, implement minimal code, rerun. Use stdlib `unittest`; no runtime dependencies. Each implementer owns their module and corresponding test file only. Parent owns integration/core/render/CLI/docs/packaging. No child commits or GitHub actions. Parent coordinates final stage/commit/push.

## Packaging/delivery

`pyproject.toml` setuptools or hatchling build backend, version 0.1.0, MIT. Test Python3.10/3.12/3.14 in GitHub Actions on Ubuntu, Windows/macOS smoke if affordable. Pin action commit SHAs. README in English, Simplified Chinese, and Russian, architecture/schema docs, privacy caveats, runnable demo and actual output. No fabricated telemetry, stars, execution logs or benchmarks. Do not copy any user's chat/server/credentials into repo. Source fixture is explicitly synthetic.
