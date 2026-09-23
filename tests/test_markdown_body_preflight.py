from __future__ import annotations

import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ROOT_PREFLIGHT = PLUGIN_ROOT / "scripts" / "markdown_body_preflight.py"
SKILL_PREFLIGHT = (
    PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "markdown_body_preflight.py"
)


class MarkdownBodyPreflightTests(unittest.TestCase):
    def run_preflight(
        self, script: Path, body_text: str
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            body_file = Path(directory) / "body.md"
            body_file.write_text(body_text, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(script), "--body-file", str(body_file)],
                cwd=PLUGIN_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )

    def test_root_entrypoint_rejects_hard_wrapped_prose(self) -> None:
        result = self.run_preflight(ROOT_PREFLIGHT, "First line\ncontinued line\n")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("hard-wrapped prose at line(s): 2", result.stderr)

    def test_bundled_entrypoint_accepts_structural_markdown(self) -> None:
        result = self.run_preflight(
            SKILL_PREFLIGHT,
            "# Report\n\nA complete paragraph.\n\n- A list item\n- Another item\n\n| cell | cell |\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("does not contain hard-wrapped prose", result.stdout)

    def test_root_wrapper_runs_the_bundled_checker_in_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            body_file = Path(directory) / "body.md"
            body_file.write_text("Complete paragraph.\n", encoding="utf-8")
            argv = [str(ROOT_PREFLIGHT), "--body-file", str(body_file)]

            runpy.run_path(str(ROOT_PREFLIGHT), run_name="markdown_body_preflight")
            success_output = StringIO()
            with mock.patch.object(sys, "argv", argv), redirect_stdout(success_output):
                with self.assertRaises(SystemExit) as success:
                    runpy.run_path(str(ROOT_PREFLIGHT), run_name="__main__")
            self.assertEqual(success.exception.code, 0)
            self.assertIn(
                "does not contain hard-wrapped prose", success_output.getvalue()
            )

            body_file.write_text("First line\ncontinued line\n", encoding="utf-8")
            error_output = StringIO()
            with mock.patch.object(sys, "argv", argv), redirect_stderr(error_output):
                with self.assertRaises(SystemExit) as failure:
                    runpy.run_path(str(ROOT_PREFLIGHT), run_name="__main__")
            self.assertEqual(failure.exception.code, 1)
            self.assertIn("hard-wrapped prose at line(s): 2", error_output.getvalue())

    def test_rejects_list_continuations_and_inline_comment_prose(self) -> None:
        for body, line in (
            ("- First list item\n  continuation\n", 2),
            ("First part <!-- inline note -->\ncontinuation\n", 2),
            ("First <!-- inline note --> visible\ncontinuation\n", 2),
        ):
            with self.subTest(body=body):
                result = self.run_preflight(SKILL_PREFLIGHT, body)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"hard-wrapped prose at line(s): {line}", result.stderr)


if __name__ == "__main__":
    unittest.main()
