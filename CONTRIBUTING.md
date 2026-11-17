# Contributing

Start with an issue describing a reproducible workflow or format sample. Use synthetic fixtures: never upload your own full chat exports, credentials, private code, or hidden reasoning.

## Setup

Python 3.10+ and Git are required for the full test suite.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install ruff build
python -m unittest discover -s tests -v
ruff check src tests
python -m build
```

On Windows, activate with `.venv\Scripts\Activate.ps1`. The runtime has no third-party dependencies. Keep network access out of runtime paths.

## Changes

1. Add a small failing test reproducing the behavior.
2. Implement a focused change; use literal source locators and avoid inferred facts.
3. Exercise malformed input, size limits, Unicode, paths and privacy boundaries where relevant.
4. Update docs and format fixtures when an import contract changes.
5. Run tests, lint and a real `passport demo --out` before opening a PR.

Do not execute commands stored in transcripts or repositories. Do not add broad automatic home-directory scanning. New formats should omit system/developer prompts and private reasoning, and document unsupported records clearly.

Generated bundles have a versioned schema. Incompatible changes require a schema-version change and tests rejecting unsupported versions.

## Pull request checklist

- Reproduction and meaningful tests included.
- No credentials or private transcripts in changed files or fixtures.
- No runtime dependencies or network calls without a discussed design change.
- Documentation describes actual behavior and limitations.
- Screenshot/demo files are generated from a labelled synthetic example.
