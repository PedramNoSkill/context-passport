from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import unittest


class CliHelpTests(unittest.TestCase):
    def test_help_and_version_need_no_credentials(self):
        from context_passport.cli import main
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--version"]), 0)
        self.assertIn("0.1.0", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([]), 0)
        for command in ("create", "inspect", "prompt", "conversations", "demo"):
            self.assertIn(command, out.getvalue())
        error = io.StringIO()
        with redirect_stderr(error):
            self.assertEqual(main(["not-a-command"]), 2)
        self.assertNotIn("Traceback", error.getvalue())

    def test_create_inspect_and_prompt_roundtrip(self):
        from context_passport.cli import main
        from pathlib import Path
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "conversation.txt"
            source.write_text("User: Build a local notes app.\nAssistant: Decision: Keep data on disk.\nNext: Add keyboard shortcuts.\n")
            output = root / "passport"
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["create", "--from", str(source), "--out", str(output), "--target", "codex", "--goal", "Finish the notes app."])
            self.assertEqual(code, 0)
            self.assertTrue((output / "passport.json").is_file())
            data = json.loads((output / "passport.json").read_text(encoding="utf-8"))
            self.assertEqual(data["target"], "codex")
            self.assertEqual(data["sections"]["goal"][0]["text"], "Finish the notes app.")
            self.assertEqual(set(p.name for p in output.iterdir()), {"passport.json", "HANDOFF.md", "review.html"})
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["inspect", str(output)]), 0)
            self.assertIn("Finish the notes app.", out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["prompt", str(output), "--target", "claude"]), 0)
            self.assertIn("Finish the notes app.", out.getvalue())
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(main(["create", "--from", str(source), "--out", str(output)]), 2)
            self.assertNotIn("Traceback", error.getvalue())

    def test_redacted_demo_can_be_inspected_and_prompted_idempotently(self):
        from context_passport.cli import main
        from pathlib import Path
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve() / "demo"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["demo", "--out", str(output)]), 0)
            data = json.loads((output / "passport.json").read_text(encoding="utf-8"))
            self.assertGreater(data["redactions"]["total"], 0)
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(["inspect", str(output), "--json"]), 0)
            inspected = json.loads(stream.getvalue())
            self.assertEqual(inspected["redactions"], data["redactions"])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["prompt", str(output)]), 0)
            self.assertNotIn("demo-only-not-a-real-credential", json.dumps(inspected))


if __name__ == "__main__":
    unittest.main()
