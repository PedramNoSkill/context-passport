"""Managed output files must never overwrite input or follow output symlinks."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class OutputSafetyTests(unittest.TestCase):
    def test_writes_only_managed_files_and_refuses_overwrite(self):
        from context_passport.storage import write_files
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve() / "passport"
            contents = {"passport.json": "{}\n", "HANDOFF.md": "# Safe\n", "review.html": "<!doctype html>"}
            paths = write_files(output, contents)
            self.assertEqual(set(paths), set(contents))
            for name, value in contents.items():
                self.assertEqual((output / name).read_text(), value)
            (output / "unrelated.txt").write_text("keep me")
            with self.assertRaises(ValueError):
                write_files(output, contents)
            write_files(output, {**contents, "HANDOFF.md": "changed"}, force=True)
            self.assertEqual((output / "HANDOFF.md").read_text(), "changed")
            self.assertEqual((output / "unrelated.txt").read_text(), "keep me")

    def test_refuses_symlinks_and_protected_sources_even_with_force(self):
        from context_passport.storage import write_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            actual = root / "actual"
            actual.mkdir()
            link = root / "linked"
            try:
                link.symlink_to(actual, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks unavailable")
            contents = {"passport.json": "{}", "HANDOFF.md": "safe", "review.html": "safe"}
            with self.assertRaises(ValueError):
                write_files(link, contents, force=True)
            source = actual / "HANDOFF.md"
            source.write_text("original transcript")
            with self.assertRaises(ValueError):
                write_files(actual, contents, force=True, protected=[source])
            self.assertEqual(source.read_text(), "original transcript")
            source.unlink()
            target = root / "private.txt"
            target.write_text("private")
            source.symlink_to(target)
            with self.assertRaises(ValueError):
                write_files(actual, contents, force=True)
            self.assertEqual(target.read_text(), "private")
            self.assertEqual(list(actual.glob(".passport-*")), [])

    def test_json_loader_rejects_invalid_or_oversized_bundles(self):
        from context_passport.storage import load_json
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp).resolve() / "passport.json"
            file.write_text('{"schema_version":"1.0"}')
            self.assertEqual(load_json(file)["schema_version"], "1.0")
            self.assertEqual(load_json(file.parent)["schema_version"], "1.0")
            file.write_text("invalid JSON")
            with self.assertRaises(ValueError):
                load_json(file)
            file.write_text("[1,2,3]")
            with self.assertRaises(ValueError):
                load_json(file)
            file.write_text("{}" * 30)
            with self.assertRaises(ValueError):
                load_json(file, max_bytes=10)


if __name__ == "__main__":
    unittest.main()
