# v0.1.0 release verification

Status: **local verification and independent code review passed**. GitHub Actions results are recorded separately by the CI workflow.

## Scope

Initial release of the local Python CLI, transcript importers, source-backed extraction, credential redaction, optional read-only Git capture, guarded output storage, Markdown handoff, offline HTML review, packaging, and CI.

## Executed checks

- Python 3.10: all 120 unittest cases passed against the final source tree; the same 120 cases also passed against the installed final wheel from outside the checkout.
- Python 3.12: all 120 unittest cases passed.
- Python 3.14: all 120 unittest cases passed.
- Ruff: `ruff check src tests` passed.
- Diff whitespace checks passed.
- Installed wheel: both CLI entry points, synthetic demo generation, and bundle inspection passed in a clean Python 3.10 environment.
- Offline HTML: evidence search hid all five records for an unmatched query and restored all five on reset; no external resources loaded. No horizontal overflow at 1280px desktop or 390px mobile widths.
- Source archive: translated README files, format/privacy documentation, and the generated example are included.

The final source and wheel test results above include all four corrections below. The bundled demo's historical test output is explicitly fictional and is not used as release verification.

## Independent review

Three fresh reviewers examined separate complete initial diffs. The CLI/rendering/storage/packaging/workflow review found no concrete security or logic blockers. The other reviews identified:

1. JSON-encoded array tool results could preserve reasoning/image blocks instead of filtering them.
2. A background shell command such as `pytest & true` could be classified as a successful test run.
3. A shorter nested Markdown fence could close a longer outer fence during section extraction.
4. Git filter names containing `=` could bypass command-line filter overrides.

All four findings were corrected with observed failing regressions before each fix. JSON arrays now use the recursive tool-content filter; background shell operators are rejected by runner recognition; section extraction tracks the opening fence character and length; and Git filter overrides pass full keys and values separately through Git's configuration environment. The Git regression checks that a repository-configured marker program is never executed while the expected diff remains available. Final source and installed-wheel suites pass all 120 tests. Independent re-review of the correction diff passed with no security concerns or logic errors. The separate test-portability diff was also independently approved.

## Compatibility corrections

Test fixtures no longer require `TMPDIR`, resolve temporary root aliases before calling symlink-rejecting output code, read exported JSON as UTF-8, and use valid filename variants on Windows. A run with `TMPDIR` removed passed all 19 Git tests; a temporary-parent symlink probe passed the three CLI tests. The original POSIX filename cases remain exercised on POSIX.

## Limits

This report is an engineering review, not a security certification. Credential detection is best-effort. Transcript claims and historical exit codes are not proof of current repository state. Windows/macOS results must be taken from the actual GitHub Actions run once available; local Linux tests do not establish those platform results.
