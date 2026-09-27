from __future__ import annotations

import importlib.util
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "update_pr_body.py"
SPEC = importlib.util.spec_from_file_location("update_pr_body", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load update_pr_body.py")
update_pr_body = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = update_pr_body
SPEC.loader.exec_module(update_pr_body)


HEAD_SHA = "A" * 40
TEMPLATE_MARKER = "<!-- repo-scaffold:pr-template=bugfix -->"
START_MARKER = "<!-- repo-scaffold:pr-head:start -->"
END_MARKER = "<!-- repo-scaffold:pr-head:end -->"


class UpdatePullRequestBodyTests(unittest.TestCase):
    def test_inserts_block_after_the_trusted_template_marker(self) -> None:
        body = f"{TEMPLATE_MARKER}\n\n## Purpose\nExplain the change.\n"

        updated = update_pr_body.update_body(body, HEAD_SHA)

        self.assertEqual(
            updated,
            f"{TEMPLATE_MARKER}\n"
            f"{START_MARKER}\n"
            f"Latest head commit: `{HEAD_SHA.lower()}`.\n"
            f"{END_MARKER}\n\n"
            "## Purpose\nExplain the change.\n",
        )

        no_trailing_newline = update_pr_body.update_body(TEMPLATE_MARKER, HEAD_SHA)
        self.assertTrue(no_trailing_newline.endswith(END_MARKER + "\n"))

    def test_replaces_only_the_existing_block_and_is_idempotent(self) -> None:
        body = (
            f"{TEMPLATE_MARKER}\n"
            f"{START_MARKER}\n"
            "Latest head commit: `old`.\n"
            f"{END_MARKER}\n\n"
            "## Purpose\nKeep this paragraph.\n"
        )

        updated = update_pr_body.update_body(body, HEAD_SHA)

        self.assertEqual(updated.count("Keep this paragraph."), 1)
        self.assertNotIn("`old`", updated)
        self.assertEqual(update_pr_body.update_body(updated, HEAD_SHA), updated)

        body_without_trailing_newline = (
            f"{TEMPLATE_MARKER}\n{START_MARKER}\nold\n{END_MARKER}"
        )
        self.assertTrue(
            update_pr_body.update_body(
                body_without_trailing_newline, HEAD_SHA
            ).endswith(END_MARKER + "\n")
        )

    def test_preserves_crlf_without_mixing_line_endings(self) -> None:
        body = f"{TEMPLATE_MARKER}\r\n\r\n## Purpose\r\nExplain.\r\n"

        updated = update_pr_body.update_body(body, HEAD_SHA)

        self.assertIn(f"{START_MARKER}\r\n", updated)
        self.assertNotIn("\n", updated.replace("\r\n", ""))

    def test_leaves_bodies_without_a_template_marker_unchanged(self) -> None:
        body = "A body supplied by an external system.\n"

        self.assertEqual(update_pr_body.update_body(body, HEAD_SHA), body)

    def test_rejects_malformed_or_detached_markers(self) -> None:
        cases = (
            f"{TEMPLATE_MARKER}\n{START_MARKER}\n",
            f"{TEMPLATE_MARKER}\n{END_MARKER}\n",
            f"{TEMPLATE_MARKER}\ntext\n{START_MARKER}\nold\n{END_MARKER}\n",
            f"{TEMPLATE_MARKER}\n{START_MARKER}\nold\n{END_MARKER}\n"
            f"{START_MARKER}\nold\n{END_MARKER}\n",
            f"{TEMPLATE_MARKER}\n{TEMPLATE_MARKER}\n",
            f"text\n{TEMPLATE_MARKER}\n",
        )
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, "marker"):
                    update_pr_body.update_body(body, HEAD_SHA)

    def test_rejects_non_text_bodies_and_unexpected_carriage_returns(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            update_pr_body.update_body(None, HEAD_SHA)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "unsupported carriage"):
            update_pr_body.update_body(f"{TEMPLATE_MARKER}\rbody", HEAD_SHA)

    def test_bounded_body_reader_rejects_missing_oversized_and_invalid_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "could not read"):
                update_pr_body.read_body(root / "missing.md")

            oversized = root / "oversized.md"
            oversized.write_bytes(b"x" * (update_pr_body.MAX_BODY_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "safety cap"):
                update_pr_body.read_body(oversized)

            invalid = root / "invalid.md"
            invalid.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "valid UTF-8"):
                update_pr_body.read_body(invalid)

    def test_write_body_rejects_oversized_and_unwritable_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "body.md"
            with self.assertRaisesRegex(ValueError, "generated"):
                update_pr_body.write_body(
                    output, "x" * (update_pr_body.MAX_BODY_BYTES + 1)
                )

            with mock.patch.object(Path, "write_bytes", side_effect=OSError("denied")):
                with self.assertRaisesRegex(ValueError, "could not write"):
                    update_pr_body.write_body(output, "body")

    def test_rejects_invalid_head_sha_and_mixed_line_endings(self) -> None:
        with self.assertRaisesRegex(ValueError, "40 hexadecimal"):
            update_pr_body.update_body(f"{TEMPLATE_MARKER}\n", "short")
        with self.assertRaisesRegex(ValueError, "mixes CRLF"):
            update_pr_body.update_body(f"{TEMPLATE_MARKER}\r\nsecond\n", HEAD_SHA)

    def test_cli_writes_output_and_reports_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body_file = root / "body.md"
            output_file = root / "updated.md"
            body_file.write_text(f"{TEMPLATE_MARKER}\n", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                result = update_pr_body.main(
                    [
                        "--body-file",
                        str(body_file),
                        "--output",
                        str(output_file),
                        "--head-sha",
                        HEAD_SHA,
                    ]
                )
            self.assertEqual(result, 0)
            self.assertIn("Updated", output.getvalue())
            self.assertTrue(output_file.is_file())

            output = StringIO()
            with redirect_stdout(output):
                result = update_pr_body.main(
                    [
                        "--body-file",
                        str(output_file),
                        "--output",
                        str(body_file),
                        "--head-sha",
                        HEAD_SHA,
                    ]
                )
            self.assertEqual(result, 0)
            self.assertIn("no trusted", output.getvalue())

    def test_cli_reports_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body_file = root / "body.md"
            output_file = root / "updated.md"
            body_file.write_text("body\n", encoding="utf-8")
            errors = StringIO()
            with redirect_stderr(errors):
                result = update_pr_body.main(
                    [
                        "--body-file",
                        str(body_file),
                        "--output",
                        str(output_file),
                        "--head-sha",
                        "invalid",
                    ]
                )
            self.assertEqual(result, 1)
            self.assertIn("40 hexadecimal", errors.getvalue())

    def test_script_entrypoint_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body_file = root / "body.md"
            output_file = root / "updated.md"
            body_file.write_text("body\n", encoding="utf-8")
            argv = [
                str(SCRIPT_PATH),
                "--body-file",
                str(body_file),
                "--output",
                str(output_file),
                "--head-sha",
                HEAD_SHA,
            ]
            output = StringIO()
            with redirect_stdout(output):
                old_argv = sys.argv
                sys.argv = argv
                try:
                    with self.assertRaises(SystemExit) as raised:
                        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
                finally:
                    sys.argv = old_argv
            self.assertEqual(raised.exception.code, 0)
            self.assertIn("no trusted", output.getvalue())


if __name__ == "__main__":
    unittest.main()
