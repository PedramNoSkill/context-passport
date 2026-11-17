"""Bounded local reads and guarded writes of the three managed output files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

MANAGED = frozenset({"passport.json", "HANDOFF.md", "review.html"})


def write_files(directory, contents, force=False, protected=()):
    """Stage safe strings, then replace only managed filenames in a private directory."""
    directory = Path(directory).absolute()
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError("Output directories must not be symlinks")
    if directory.exists() and not directory.is_dir():
        raise ValueError("Output path must be a directory")
    if set(contents) != MANAGED:
        raise ValueError("Only passport.json, HANDOFF.md, and review.html may be written")
    protected_paths = {Path(path).resolve() for path in protected}
    for name in contents:
        target = directory / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"Output is not a regular file: {name}")
        if target.resolve() in protected_paths:
            raise ValueError("Output would overwrite the source transcript")
        for source in protected_paths:
            if target.exists() and source.exists() and os.path.samefile(source, target):
                raise ValueError("Output would overwrite the source transcript")
        if target.exists() and not force:
            raise ValueError(f"Output already exists: {name}; choose a new directory or use --force")
        if not isinstance(contents[name], str):
            raise ValueError("Output contents must be text")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    staged = {}
    try:
        for name, text in contents.items():
            fd, temporary = tempfile.mkstemp(prefix=".passport-", dir=directory)
            path = Path(temporary)
            staged[name] = path
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
        for name, path in staged.items():
            os.replace(path, directory / name)
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
    return {name: directory / name for name in contents}


def load_json(path, max_bytes=32 * 1024 * 1024):
    """Read a bounded JSON object; never interpret its contents as code."""
    path = Path(path)
    if path.is_dir():
        path = path / "passport.json"
    if not path.is_file():
        raise ValueError("Passport file not found; select passport.json or its directory")
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("Passport exceeds the file size limit")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("Passport must contain valid UTF-8 JSON") from error
    if not isinstance(data, dict):
        raise ValueError("Passport must be a JSON object")
    return data
