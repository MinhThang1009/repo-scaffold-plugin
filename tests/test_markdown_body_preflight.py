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

    def test_handles_nested_lists_code_spans_autolinks_and_code_blocks(self) -> None:
        rejected = "- outer\n  - inner\n    - third level\n      continuation\n"
        self.assertEqual(_bundled_lines(rejected), (4,))
        accepted = (
            "Use `<!--` literally.\n\n"
            "<https://example.test> is a complete paragraph.\n\n"
            "<div>inline HTML</div>\n\n"
            "<div>\nFirst line inside HTML\ncontinued inside HTML\n</div>\n\n"
            "<custom-element>\nFirst custom line\ncontinued custom line\n</custom-element>\n\n"
            "    print(1)\n    print(2)\n\n"
            "\tprint(3)\n\tprint(4)\n\n"
            "- Item\n```text\ncode\n```\nNew paragraph.\n"
        )
        self.assertEqual(_bundled_lines(accepted), ())

    def test_html_comment_content_does_not_change_following_prose_state(self) -> None:
        self.assertEqual(
            _bundled_lines("<!-- hidden\ncontent -->\nFirst line\ncontinued line\n"),
            (4,),
        )
        self.assertEqual(
            _bundled_lines("<!-- ```\nhidden\n``` -->\nFirst\ncontinued\n"),
            (5,),
        )

    def test_handles_multiline_and_unmatched_inline_code(self) -> None:
        self.assertEqual(
            _bundled_lines("Before `code\nspan` after.\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("Before `code\ncontinued line\nclose` after.\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("Before `literal\ncontinued prose.\n"),
            (2,),
        )


def _bundled_lines(body: str) -> tuple[int, ...]:
    """Expose the bundled parser for focused semantic cases."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "markdown_body_preflight", SKILL_PREFLIGHT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.hard_wrapped_prose_lines(body)


if __name__ == "__main__":
    unittest.main()
