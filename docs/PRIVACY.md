# Privacy and trust model

Context Passport performs no outbound network requests. It has no analytics, cloud account, credential store, LLM calls, or background watcher. Installation may use the network through your package manager; running the CLI does not.

## Explicit inputs

- Only the transcript file you pass to `--from` is imported.
- Multi-conversation exports require selecting a conversation. Other conversations are not included in the generated bundle.
- Git capture is disabled until you pass `--repo`.
- The source basename and SHA-256 digest are included for provenance. The full original file is not copied.
- User notes passed on the command line may remain in shell history. Do not put secrets into notes or command arguments.

Do not point the tool at a directory full of credentials. It is not a data-loss prevention product.

## What redaction does

Redaction replaces recognized credential-like spans with labelled placeholders before output files are written. Counts contain categories and numbers, not removed values. Examples include private PEM keys, familiar GitHub and model-provider key shapes, Telegram bot tokens, JWTs, authorization headers, URL credentials, and secret/password assignments.

**Best-effort is not a guarantee.** Unusual formats, natural-language secrets, private business context, names and file paths can remain. Even a perfectly redacted API key does not make a conversation safe to publish. Inspect all three outputs yourself.

No `--unsafe`, `--no-redact`, or automatic-upload option is provided in this version.

## Git capture

The Git adapter runs a bounded set of read-only Git commands with optional locks disabled, shell execution disabled, and external diff/textconv, hooks and filesystem monitors disabled. It does not run project scripts or tests, install dependencies, fetch, stage, commit, push, or read untracked file contents.

Recognized credential paths and symlink content are excluded from diff capture. Branch names, changed filenames and allowed tracked diffs are still data you may consider private. Use `--no-diff`, or omit `--repo`, to reduce exposure.

Capture is a snapshot, not a lock: a concurrently changing working tree can become inconsistent. Recheck the current repository before continuing work. Historical transcript results also do not prove the current revision passes tests.

## Untrusted evidence

Transcripts can contain prompt injection, shell commands, HTML and fabricated tool output. A reference identifies where a statement came from; it does not certify truth or authenticity. The Markdown handoff labels evidence as untrusted source data. The HTML viewer escapes text instead of treating it as markup.

Nothing in a handoff should automatically be executed. The next assistant must follow its own user instructions, verify current state and ask for any required approval. A user-supplied transcript can forge a tool record; this tool does not cryptographically authenticate tool execution.

## Local output

New output directories and temporary files use restrictive permissions where the operating system supports them. Existing directories keep their existing mode. Only `passport.json`, `HANDOFF.md` and `review.html` are managed; `--force` does not delete unrelated files. Source/output collisions and symlink output paths are rejected.

Each output file is staged and replaced atomically, but the three-file bundle is not a database transaction. A disk or process failure during replacement can leave files from different generations. Re-run into a fresh directory if a write fails.

The three files include useful source excerpts and may remain sensitive. They are not encrypted. Delete them when no longer needed and follow your organization's data policies.

## Limits

Input and field limits are documented in [FORMATS.md](FORMATS.md). Truncation is made visible in warnings; absence of a claim from the summary does not mean the event did not occur. Review the evidence transcript.

Report a security issue privately using the process in [SECURITY.md](../SECURITY.md).
