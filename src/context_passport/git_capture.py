"""Read-only, local Git context capture."""

import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading
import time


_COMMAND_TIMEOUT = 5.0
_CAPTURE_TIMEOUT = 20.0
_METADATA_LIMIT = 2 * 1024 * 1024


def _stream(command, repo, env, limit, timeout):
    """Retain at most limit+1 bytes; drain excess until exit or the deadline."""
    try:
        process = subprocess.Popen(command, cwd=repo, env=env, shell=False,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, bufsize=0)
    except OSError:
        raise GitCaptureError("Git is unavailable; install Git and select a repository.") from None
    output = bytearray()
    overflow = threading.Event()
    def read():
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                output.extend(chunk[:max(0, limit + 1 - len(output))])
                if len(output) > limit:
                    overflow.set()
        except OSError:
            pass
        finally:
            process.stdout.close()
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join(timeout=1)
        raise GitCaptureError("Git repository capture timed out.") from None
    reader.join(timeout=1)
    if reader.is_alive():
        raise GitCaptureError("Git repository output could not be read safely.")
    return bytes(output[:limit]), process.returncode, overflow.is_set()


class GitCaptureError(ValueError):
    """A repository cannot be captured safely."""


def _secret_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    for part in parts:
        name = part.lower()
        if name.startswith(".env") and (name == ".env" or name.startswith(".env.")):
            if not set(name.split(".")[2:]).intersection({"example", "template"}):
                return True
        if name in {".ssh", ".gnupg", ".aws", ".netrc", ".npmrc", ".pypirc", ".git-credentials"}:
            return True
        if name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore")) or name.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")):
            return True
        if re.search(r"(?:^|[._-])(?:credentials?|auth|authentication|tokens?|secrets?|passwords?|service[-_]account)(?:$|[._-])", name):
            return True
    return False


def capture_git(repo: Path, max_diff_bytes: int = 65536, include_diff: bool = True) -> dict:
    if type(max_diff_bytes) is not int or max_diff_bytes < 0:
        raise GitCaptureError("max_diff_bytes must be a non-negative integer.")
    repo = Path(repo)
    if not repo.is_dir():
        raise GitCaptureError("Select an existing Git repository directory.")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "LC_ALL": "C",
                "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""})
    command = ["git", "--no-pager", "--literal-pathspecs", "-c", "core.fsmonitor=false",
               "-c", "core.hooksPath=" + os.devnull, "-c", "diff.external=", "-c", "core.pager=cat",
               "-c", "protocol.allow=never", "-c", "protocol.ext.allow=never",
               "-c", "protocol.file.allow=never"]
    deadline = time.monotonic() + _CAPTURE_TIMEOUT
    def remaining():
        seconds = min(_COMMAND_TIMEOUT, deadline - time.monotonic())
        if seconds <= 0:
            raise GitCaptureError("Git repository capture timed out.")
        return seconds
    def git(*args, allowed=(0,)):
        data, code, truncated = _stream([*command, *args], repo, env, _METADATA_LIMIT, remaining())
        if truncated:
            raise GitCaptureError("Git repository metadata exceeds the capture limit; narrow the repository.")
        if code not in allowed:
            raise GitCaptureError("Git repository metadata could not be read; check repository integrity and configuration.")
        return data.decode("utf-8")
    # Clean/process filters can execute even during status and ordinary diff.
    # Read only their names, then shadow every configured driver before those commands.
    configured = git("config", "--name-only", "--get-regexp", r"^filter\..*\.(clean|smudge|process|required)$", allowed=(0, 1))
    filter_keys = configured.splitlines()
    # Keep keys separate from values: valid filter names can contain '='.
    env["GIT_CONFIG_COUNT"] = str(len(filter_keys))
    for index, key in enumerate(filter_keys):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = "false" if key.endswith(".required") else ""
    if git("rev-parse", "--is-inside-work-tree").strip() != "true":
        raise GitCaptureError("Select a Git working-tree repository.")
    repo = Path(git("rev-parse", "--show-toplevel").rstrip("\n"))
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1)).strip() or None
    head = git("rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1)).strip() or None
    changes = []
    entries = iter(git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=dirty").split("\0"))
    for entry in entries:
        if entry:
            change = {"path": entry[3:], "index_status": entry[0], "worktree_status": entry[1]}
            if entry[0] in "RC" or entry[1] in "RC":
                change["previous_path"] = next(entries)
            changes.append(change)
    paths = {path for change in changes
             for path in [change["path"], *([change["previous_path"]] if "previous_path" in change else [])]}
    symlinks = set()
    submodules = {record.split("\t", 1)[1] for record in git("ls-files", "--stage", "-z").split("\0") if record.startswith("160000 ")}
    if paths:
        metadata = git("ls-files", "--stage", "-z", "--", *sorted(paths))
        if head is not None:
            metadata += git("ls-tree", "-r", "-z", "HEAD", "--", *sorted(paths))
        for record in metadata.split("\0"):
            if record.startswith("120000 "):
                symlinks.add(record.split("\t", 1)[1])
        for path in paths:
            current = repo
            for part in PurePosixPath(path).parts:
                current = current / part
                if current.is_symlink():
                    symlinks.add(path)
                    break
    excluded = {path for path in paths if _secret_path(path) or path in symlinks}
    for change in changes:
        pair = {change["path"], change.get("previous_path", change["path"])}
        if pair & excluded:
            excluded.update(pair)
    excluded_paths = sorted(excluded)
    tracked = [path for change in changes if change["index_status"] != "?"
               for path in [change["path"], *([change["previous_path"]] if "previous_path" in change else [])]
               if path not in excluded_paths]
    baseline = "HEAD"
    if head is None and tracked and include_diff:
        import hashlib
        algorithm = git("rev-parse", "--show-object-format").strip()
        baseline = hashlib.new(algorithm, b"tree 0\0").hexdigest()
    diff = ""
    warnings = ["Submodule worktree contents were not inspected; their internal changes are omitted."] if submodules else []
    if not include_diff:
        warnings.append("Git diff omitted because include_diff=False.")
    if tracked and include_diff:
        data, code, truncated = _stream([*command, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=dirty", "--submodule=short", baseline, "--", *tracked], repo, env, max_diff_bytes, remaining())
        if code and not truncated:
            raise GitCaptureError("Git repository diff could not be captured safely.")
        diff = data.decode("utf-8", errors="ignore")
        if truncated:
            warnings.append(f"Git diff truncated at {max_diff_bytes} bytes.")
    return {
        "root_name": repo.name, "branch": branch,
        "head": head, "dirty": bool(changes),
        "changes": changes, "diff": diff, "excluded_paths": excluded_paths, "warnings": warnings,
    }
