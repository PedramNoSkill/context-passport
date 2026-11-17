"""Read-only Git capture tested only against disposable repositories."""

import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class GitCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="context-passport-git-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "synthetic repo"
        self.repo.mkdir()
        self.env = os.environ.copy()
        self.env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                         "GIT_TERMINAL_PROMPT": "0"})
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Synthetic Test")
        self.git("config", "user.email", "synthetic@example.invalid")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                              capture_output=True,
                              check=True, timeout=5).stdout.decode("utf-8")

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self):
        self.git("add", "--all")
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-m", "Synthetic baseline")

    def capture(self, **kwargs):
        module = importlib.import_module("context_passport.git_capture")
        return module.capture_git(self.repo, **kwargs)

    def test_clean_repository_contract(self):
        self.write("readme.txt", "Synthetic baseline\n")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        try:
            result = self.capture()
        except ModuleNotFoundError:
            self.fail("capture_git API has not been implemented")
        self.assertEqual(result, {
            "root_name": self.repo.name, "branch": "main", "head": head,
            "dirty": False, "changes": [], "diff": "", "excluded_paths": [], "warnings": [],
        })

    def test_dirty_capture_uses_one_final_worktree_diff(self):
        self.write("staged.txt", "baseline\n")
        self.write("unstaged.txt", "before\n")
        self.write("deleted.txt", "remove me\n")
        self.commit()
        self.write("staged.txt", "intermediate\n")
        self.git("add", "staged.txt")
        self.write("staged.txt", "final value\n")
        self.write("unstaged.txt", "after\n")
        (self.repo / "deleted.txt").unlink()
        self.write("untracked.txt", "UNTRACKED CONTENT MUST NEVER BE READ\n")
        result = self.capture()
        self.assertTrue(result["dirty"])
        self.assertEqual({c["path"]: (c["index_status"], c["worktree_status"]) for c in result["changes"]}, {
            "deleted.txt": (" ", "D"), "staged.txt": ("M", "M"),
            "unstaged.txt": (" ", "M"), "untracked.txt": ("?", "?"),
        })
        self.assertIn("+final value", result["diff"])
        self.assertIn("+after", result["diff"])
        self.assertIn("-remove me", result["diff"])
        self.assertNotIn("intermediate", result["diff"])
        self.assertEqual(result["diff"].count("diff --git a/staged.txt"), 1)
        self.assertNotIn("UNTRACKED CONTENT", result["diff"])

    def test_nul_rename_order_and_literal_unusual_paths(self):
        previous = "old name λ.txt"
        renamed = "renamed [literal].txt" if os.name == "nt" else ":(glob)*.txt"
        line_name = "line break.txt" if os.name == "nt" else "line\nbreak.txt"
        others = ["-leading.txt", "雪 spaces.txt", line_name]
        for name in [previous, *others]:
            self.write(name, "original content\n")
        self.commit()
        self.git("mv", "--", previous, renamed)
        for name in others:
            self.write(name, "changed " + name.replace("\n", " ") + "\n")
        result = self.capture()
        by_path = {c["path"]: c for c in result["changes"]}
        self.assertEqual(set(by_path), {renamed, *others})
        self.assertEqual(by_path[renamed], {"path": renamed, "index_status": "R", "worktree_status": " ", "previous_path": previous})
        self.assertIn("rename from ", result["diff"])
        self.assertIn("rename to ", result["diff"])
        for name in others:
            self.assertIn("+changed " + name.replace("\n", " "), result["diff"])

    def test_unborn_branch_captures_staged_and_unstaged_text(self):
        self.write("first.txt", "staged first content\n")
        self.git("add", "first.txt")
        self.write("first.txt", "current first content\n")
        self.write("loose.txt", "UNTRACKED NEVER READ\n")
        try:
            result = self.capture()
        except subprocess.CalledProcessError:
            self.fail("capture must support unborn HEAD")
        self.assertIsNone(result["head"])
        self.assertIn(result["branch"], ("main", None))
        self.assertTrue(result["dirty"])
        self.assertEqual({c["path"] for c in result["changes"]}, {"first.txt", "loose.txt"})
        self.assertIn("+current first content", result["diff"])
        self.assertNotIn("staged first content", result["diff"])
        self.assertNotIn("UNTRACKED NEVER READ", result["diff"])

    def test_invalid_repository_has_actionable_path_free_error(self):
        module = importlib.import_module("context_passport.git_capture")
        error_type = getattr(module, "GitCaptureError", None)
        self.assertIsNotNone(error_type, "GitCaptureError API is required")
        plain = self.base / "not a repository"
        plain.mkdir()
        file = self.base / "not a directory"
        file.write_text("synthetic", encoding="utf-8")
        for path in [self.base / "missing", plain, file]:
            with self.subTest(kind=path.name):
                with self.assertRaises(error_type) as raised:
                    module.capture_git(path)
                self.assertIn("repository", str(raised.exception).lower())
                self.assertNotIn(str(self.base), str(raised.exception))

    def test_secret_paths_excluded_but_env_examples_remain_reviewable(self):
        secret_paths = [".env", ".env.production", "nested/.env.local", "private.pem", "client.key",
                        "credentials.json", "auth.json", "tokens.json", "service-account.json",
                        ".ssh/id_rsa", "id_ed25519", ".aws/credentials", ".netrc"]
        allowed = [".env.example", ".env.template", ".env.production.example", "regular.txt"]
        for name in [*secret_paths, *allowed]:
            self.write(name, "before synthetic\n")
        self.commit()
        for name in secret_paths:
            self.write(name, "SYNTHETIC_SECRET_MARKER " + name + "\n")
        for name in allowed:
            self.write(name, "VISIBLE_EXAMPLE " + name + "\n")
        result = self.capture()
        self.assertEqual(set(result["excluded_paths"]), set(secret_paths))
        self.assertNotIn("SYNTHETIC_SECRET_MARKER", result["diff"])
        self.assertEqual({c["path"] for c in result["changes"]}, set(secret_paths + allowed))
        for name in allowed:
            self.assertIn("+VISIBLE_EXAMPLE " + name, result["diff"])

    @unittest.skipIf(os.name == "nt", "executable POSIX marker fixture")
    def test_hostile_git_configuration_never_executes_programs(self):
        self.write(".gitattributes", "*.txt diff=hostile filter=hostile\n")
        self.write("tracked.txt", "before\n")
        self.commit()
        self.write("tracked.txt", "after\n")
        marker = self.base / "EXECUTED"
        executable = self.base / "hostile"
        executable.write_text("#!/bin/sh\nprintf executed >> '" + str(marker) + "'\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
        config = self.base / "hostile.gitconfig"
        config.write_text("[core]\n fsmonitor = " + str(executable) + "\n hooksPath = " + str(self.base) + "\n pager = " + str(executable) +
                          "\n[diff]\n external = " + str(executable) + "\n[diff \"hostile\"]\n command = " + str(executable) +
                          "\n textconv = " + str(executable) + "\n[filter \"hostile\"]\n clean = " + str(executable) +
                          "\n smudge = " + str(executable) + "\n required = true\n", encoding="utf-8")
        self.git("config", "core.fsmonitor", str(executable))
        self.git("config", "diff.hostile.textconv", str(executable))
        self.git("config", "diff.hostile.command", str(executable))
        self.git("config", "filter.hostile.clean", str(executable))
        self.git("config", "filter.hostile.required", "true")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_EXTERNAL_DIFF": str(executable),
                                     "GIT_PAGER": str(executable), "PAGER": str(executable)}):
            try:
                result = self.capture()
            except Exception:
                self.assertFalse(marker.exists(), "hostile Git configuration executed")
                raise
        self.assertFalse(marker.exists(), "hostile Git configuration executed")
        self.assertTrue(result["dirty"])
        self.assertIn("+after", result["diff"])

    @unittest.skipIf(os.name == "nt", "executable POSIX marker fixture")
    def test_filter_name_containing_equals_never_executes_programs(self):
        self.write(".gitattributes", "*.txt filter=demo=driver\n")
        self.write("tracked.txt", "before\n")
        self.commit()
        self.write("tracked.txt", "after\n")
        marker = self.base / "FILTER_EXECUTED"
        executable = self.base / "hostile-filter"
        executable.write_text("#!/bin/sh\nprintf executed > '" + str(marker) + "'\ncat\n", encoding="utf-8")
        executable.chmod(0o700)
        self.git("config", "filter.demo=driver.clean", "'" + str(executable) + "'")
        self.git("config", "filter.demo=driver.required", "true")
        self.assertEqual(self.git("config", "--name-only", "--get-regexp", r"^filter\..*\.clean$"),
                         "filter.demo=driver.clean\n")
        self.assertFalse(marker.exists(), "fixture executed the filter before capture")
        try:
            result = self.capture()
        finally:
            self.assertFalse(marker.exists(), "Git filter with '=' in its name executed")
        self.assertTrue(result["dirty"])
        self.assertIn({"path": "tracked.txt", "index_status": " ", "worktree_status": "M"},
                      result["changes"])
        self.assertIn("+after", result["diff"])

    @unittest.skipIf(os.name == "nt", "symlinks require Windows developer mode")
    def test_symlink_contents_excluded_from_head_index_and_worktree(self):
        external = self.base / "outside"
        external.mkdir()
        (external / "child.txt").write_text("OUTSIDE_SECRET\n", encoding="utf-8")
        for name in ["live-link", "deleted-link", "replaced-link"]:
            (self.repo / name).symlink_to(external / "child.txt")
        self.write("became-link", "ordinary\n")
        self.write("directory/child.txt", "original child\n")
        self.commit()
        (self.repo / "live-link").unlink()
        (self.repo / "live-link").symlink_to(self.base / "other secret")
        (self.repo / "deleted-link").unlink()
        (self.repo / "replaced-link").unlink()
        self.write("replaced-link", "ordinary replacement\n")
        self.git("add", "replaced-link")
        (self.repo / "became-link").unlink()
        (self.repo / "became-link").symlink_to(external / "child.txt")
        (self.repo / "new-link").symlink_to(external / "child.txt")
        self.git("add", "new-link")
        (self.repo / "directory/child.txt").unlink()
        (self.repo / "directory").rmdir()
        (self.repo / "directory").symlink_to(external, target_is_directory=True)
        result = self.capture()
        self.assertTrue({"live-link", "deleted-link", "replaced-link", "became-link", "new-link", "directory/child.txt"}.issubset(result["excluded_paths"]))
        self.assertEqual(result["diff"], "")
        self.assertNotIn("OUTSIDE_SECRET", repr(result))
        self.assertNotIn(str(self.base), repr(result))

    def test_secret_rename_excludes_both_sides(self):
        self.write(".env", "SYNTHETIC_RENAMED_SECRET\n")
        self.commit()
        self.git("mv", "--", ".env", "innocent.txt")
        result = self.capture()
        self.assertEqual(set(result["excluded_paths"]), {".env", "innocent.txt"})
        self.assertEqual(result["diff"], "")
        self.assertEqual(result["changes"][0]["previous_path"], ".env")

    def test_diff_is_streamed_bounded_with_explicit_truncation(self):
        self.write("large.txt", "before\n")
        self.commit()
        self.write("large.txt", "雪 synthetic large patch\n" * 100000)
        import context_passport.git_capture as module
        original_run = subprocess.run
        def forbid_full_diff_capture(command, *args, **kwargs):
            if "diff" in command and kwargs.get("stdout") == subprocess.PIPE:
                self.fail("diff must be streamed, never captured without a bound")
            return original_run(command, *args, **kwargs)
        with patch.object(module.subprocess, "run", side_effect=forbid_full_diff_capture):
            result = self.capture(max_diff_bytes=1024)
        self.assertLessEqual(len(result["diff"].encode("utf-8")), 1024)
        self.assertIn("diff --git", result["diff"])
        self.assertNotIn("\ufffd", result["diff"])
        self.assertTrue(any("diff" in warning.lower() and "truncat" in warning.lower() for warning in result["warnings"]))

    def test_stream_overflow_drains_without_killing_the_producer(self):
        import context_passport.git_capture as module

        marker = self.base / "producer-finished"
        code = ("import pathlib,sys; "
                "sys.stdout.buffer.write(b'x' * (1024 * 1024)); "
                "sys.stdout.buffer.flush(); "
                "pathlib.Path(sys.argv[1]).write_text('finished', encoding='utf-8')")
        output, exit_code, truncated = module._stream(
            [sys.executable, "-c", code, str(marker)],
            self.repo, self.env, 1024, 5,
        )
        self.assertTrue(marker.exists(), "overflow must let the producer release its working directory")
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, b"x" * 1024)
        self.assertTrue(truncated)

    def test_detached_head_has_null_branch(self):
        self.write("readme.txt", "baseline\n")
        self.commit()
        self.git("checkout", "--detach", "HEAD")
        try:
            result = self.capture(include_diff=False)
        except subprocess.CalledProcessError:
            self.fail("detached HEAD must remain capturable")
        self.assertIsNone(result["branch"])
        self.assertEqual(result["head"], self.git("rev-parse", "HEAD").strip())
        self.assertFalse(result["dirty"])
        self.assertEqual(result["diff"], "")

    def test_metadata_output_limit_fails_explicitly(self):
        for number in range(50):
            self.write(f"untracked synthetic file {number:03}.txt", "never read\n")
        import context_passport.git_capture as module
        with patch.object(module, "_METADATA_LIMIT", 256, create=True):
            with self.assertRaisesRegex(module.GitCaptureError, "limit"):
                self.capture(include_diff=False)

    def test_invalid_diff_limits_are_rejected_before_capture(self):
        import context_passport.git_capture as module
        for value in [-1, 1.5, True, "1024", None]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(module.GitCaptureError, "max_diff_bytes"):
                    module.capture_git(self.repo, max_diff_bytes=value)

    def test_selected_subdirectory_uses_repository_root_basename(self):
        self.write("nested/file.txt", "before\n")
        self.commit()
        self.write("nested/file.txt", "after\n")
        self.git("remote", "add", "origin", "https://synthetic.invalid/PRIVATE_REMOTE")
        import context_passport.git_capture as module
        with patch.dict(os.environ, {"GIT_DIR": str(self.base / "nonexistent override"),
                                     "GIT_WORK_TREE": str(self.base)}):
            result = module.capture_git(self.repo / "nested")
        self.assertEqual(result["root_name"], self.repo.name)
        self.assertEqual(result["changes"][0]["path"], "nested/file.txt")
        self.assertIn("+after", result["diff"])
        self.assertNotIn(str(self.base), repr(result))
        self.assertNotIn("PRIVATE_REMOTE", repr(result))

    def test_corrupt_git_config_returns_sanitized_capture_error(self):
        (self.repo / ".git/config").write_text("[synthetic broken config\n", encoding="utf-8")
        import context_passport.git_capture as module
        try:
            self.capture()
        except module.GitCaptureError as exc:
            self.assertIn("repository", str(exc).lower())
            self.assertNotIn(str(self.base), str(exc))
        except Exception:
            self.fail("invalid repository config must raise GitCaptureError")
        else:
            self.fail("invalid repository config must be rejected")

    def test_capture_deadline_bounds_multiple_git_commands(self):
        self.write("readme.txt", "baseline\n")
        self.commit()
        import context_passport.git_capture as module
        clock = [0.0]
        budgets = []

        def slow_git(command, repo, env, limit, timeout):
            budgets.append(timeout)
            clock[0] += 0.08
            if "config" in command:
                return b"", 1, False
            if "--is-inside-work-tree" in command:
                return b"true\n", 0, False
            return str(self.repo).encode(), 0, False

        with patch.object(module, "_CAPTURE_TIMEOUT", 0.2), \
                patch.object(module.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(module, "_stream", side_effect=slow_git):
            with self.assertRaisesRegex(module.GitCaptureError, "timed out"):
                self.capture()
        self.assertEqual(len(budgets), 3)
        self.assertGreater(budgets[0], budgets[1])
        self.assertGreater(budgets[1], budgets[2])

    @unittest.skipIf(os.name == "nt", "executable POSIX marker fixture")
    def test_submodule_worktree_filters_never_execute(self):
        nested = self.repo / "nested"
        nested.mkdir()
        def nested_git(*args):
            return subprocess.run(["git", *args], cwd=nested, env=self.env,
                                  capture_output=True, check=True, timeout=5).stdout.decode()
        nested_git("init", "-b", "main")
        nested_git("config", "user.name", "Synthetic Test")
        nested_git("config", "user.email", "synthetic@example.invalid")
        (nested / ".gitattributes").write_text("*.txt filter=nested\n", encoding="utf-8")
        (nested / "file.txt").write_text("before\n", encoding="utf-8")
        nested_git("add", "--all")
        nested_git("commit", "-m", "Synthetic nested baseline")
        self.write(".gitmodules", '[submodule "nested"]\n path = nested\n url = https://synthetic.invalid/never-fetch\n')
        self.commit()
        marker = self.base / "NESTED_EXECUTED"
        executable = self.base / "nested-hostile"
        executable.write_text("#!/bin/sh\nprintf executed > '" + str(marker) + "'\ncat\n", encoding="utf-8")
        executable.chmod(0o700)
        nested_git("config", "filter.nested.clean", str(executable))
        nested_git("config", "filter.nested.required", "true")
        (nested / "file.txt").write_text("after!\n", encoding="utf-8")
        self.capture()
        self.assertFalse(marker.exists(), "nested repository filter executed")

    def test_no_diff_launches_no_diff_and_leaves_repository_unchanged(self):
        self.write("tracked.txt", "baseline\n")
        self.commit()
        self.write("tracked.txt", "worktree edit\n")
        self.git("add", "tracked.txt")
        self.write("tracked.txt", "another edit\n")
        self.write("untracked.txt", "UNTRACKED_SECRET\n")
        snapshot = {p.relative_to(self.repo).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in (self.repo / ".git").rglob("*") if p.is_file()}
        import context_passport.git_capture as module
        original_popen = subprocess.Popen
        commands = []
        def guarded_popen(command, *args, **kwargs):
            self.assertNotIn("diff", command, "include_diff=False must not launch diff")
            self.assertIs(kwargs.get("shell"), False)
            self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
            commands.append(command)
            return original_popen(command, *args, **kwargs)
        with patch.object(module.subprocess, "Popen", side_effect=guarded_popen):
            result = self.capture(include_diff=False)
        self.assertTrue(commands)
        self.assertTrue(result["dirty"])
        self.assertEqual(result["diff"], "")
        self.assertTrue(any("diff" in w.lower() and "omitted" in w.lower() for w in result["warnings"]))
        after = {p.relative_to(self.repo).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in (self.repo / ".git").rglob("*") if p.is_file()}
        self.assertEqual(snapshot, after)
        self.assertEqual((self.repo / "tracked.txt").read_text(encoding="utf-8"), "another edit\n")

    @unittest.skipIf(os.name == "nt", "executable POSIX marker fixture")
    def test_missing_promisor_object_never_launches_remote_transport(self):
        self.write("tracked.txt", "before\n")
        self.commit()
        blob = self.git("rev-parse", "HEAD:tracked.txt").strip()
        marker = self.base / "FETCH_EXECUTED"
        executable = self.base / "remote-hostile"
        executable.write_text("#!/bin/sh\nprintf executed > '" + str(marker) + "'\nexit 1\n", encoding="utf-8")
        executable.chmod(0o700)
        self.git("config", "extensions.partialClone", "origin")
        self.git("config", "remote.origin.promisor", "true")
        self.git("config", "remote.origin.url", "ext::" + str(executable))
        self.git("config", "protocol.ext.allow", "always")
        (self.repo / ".git/objects" / blob[:2] / blob[2:]).unlink()
        self.write("tracked.txt", "changed\n")
        import context_passport.git_capture as module
        try:
            self.capture()
        except module.GitCaptureError:
            pass
        self.assertFalse(marker.exists(), "Git launched an automatic remote transport")


if __name__ == "__main__":
    unittest.main()
