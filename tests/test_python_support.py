from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "python_support.py"
SPEC = importlib.util.spec_from_file_location("python_support", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load python_support.py")
python_support = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = python_support
SPEC.loader.exec_module(python_support)


def policy_document() -> dict[str, object]:
    return {
        "schema-version": 1,
        "implementation": "cpython",
        "versions": ["3.10", "3.11", "3.12"],
        "full-coverage-os": ["ubuntu-latest"],
        "boundary-coverage-os": ["windows-latest"],
    }


class PythonSupportPolicyTests(unittest.TestCase):
    def write_policy(self, directory: str, document: object | None = None) -> Path:
        path = Path(directory) / "policy.json"
        path.write_text(
            json.dumps(policy_document() if document is None else document),
            encoding="utf-8",
        )
        return path

    def test_matrix_covers_every_primary_version_and_secondary_boundaries(self) -> None:
        policy = python_support.parse_policy(policy_document())

        self.assertEqual(
            python_support.build_matrix(policy),
            {
                "include": [
                    {"os": "ubuntu-latest", "python-version": "3.10"},
                    {"os": "ubuntu-latest", "python-version": "3.11"},
                    {"os": "ubuntu-latest", "python-version": "3.12"},
                    {"os": "windows-latest", "python-version": "3.10"},
                    {"os": "windows-latest", "python-version": "3.12"},
                ]
            },
        )

    def test_policy_rejects_version_gaps(self) -> None:
        document = policy_document()
        document["versions"] = ["3.10", "3.12"]

        with self.assertRaisesRegex(
            python_support.PolicyError, "ordered, contiguous, and gap-free"
        ):
            python_support.parse_policy(document)

    def test_policy_rejects_overlapping_os_coverage(self) -> None:
        document = policy_document()
        document["boundary-coverage-os"] = ["ubuntu-latest"]

        with self.assertRaisesRegex(python_support.PolicyError, "must not overlap"):
            python_support.parse_policy(document)

    def test_policy_rejects_invalid_document_shapes_and_metadata(self) -> None:
        cases = [
            ([], "policy root must be an object"),
            ({**policy_document(), "unexpected": True}, "unknown fields"),
            (
                {
                    key: value
                    for key, value in policy_document().items()
                    if key != "implementation"
                },
                "missing fields",
            ),
            ({**policy_document(), "schema-version": True}, "integer 1"),
            ({**policy_document(), "implementation": "pypy"}, "must be cpython"),
        ]

        for document, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(python_support.PolicyError, message):
                    python_support.parse_policy(document)

    def test_policy_rejects_invalid_string_lists(self) -> None:
        cases = [
            ("versions", [], "nonempty array"),
            ("versions", ["3.10", ""], "only nonempty strings"),
            ("versions", ["3.10", "3.10"], "must not contain duplicates"),
            ("full-coverage-os", "ubuntu-latest", "nonempty array"),
        ]

        for field, value, message in cases:
            document = policy_document()
            document[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(python_support.PolicyError, message):
                    python_support.parse_policy(document)

    def test_policy_rejects_invalid_version_and_runner_syntax(self) -> None:
        cases = [
            ("versions", ["3.10.1"], "feature-release syntax"),
            ("versions", ["2.7"], "feature-release syntax"),
            ("full-coverage-os", ["ubuntu latest"], "invalid GitHub-hosted"),
            ("boundary-coverage-os", ["-windows"], "invalid GitHub-hosted"),
        ]

        for field, value, message in cases:
            document = policy_document()
            document[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(python_support.PolicyError, message):
                    python_support.parse_policy(document)

    def test_duplicate_json_members_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"schema-version":1,"schema-version":1}', encoding="utf-8")

            with self.assertRaisesRegex(
                python_support.PolicyError, "duplicate JSON member"
            ):
                python_support.load_policy(path)

    def test_json_pair_loader_accepts_unique_members(self) -> None:
        self.assertEqual(
            python_support.reject_duplicate_json_pairs([("first", 1), ("second", 2)]),
            {"first": 1, "second": 2},
        )

    def test_policy_loader_wraps_file_decode_and_json_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_utf8 = root / "invalid-utf8.json"
            invalid_utf8.write_bytes(b"\xff")
            invalid_json = root / "invalid-json.json"
            invalid_json.write_text("{", encoding="utf-8")
            missing = root / "missing.json"

            for path in (invalid_utf8, invalid_json, missing):
                with self.subTest(path=path.name):
                    with self.assertRaisesRegex(
                        python_support.PolicyError, "could not read"
                    ):
                        python_support.load_policy(path)

    def test_latest_canary_detects_a_new_stable_release(self) -> None:
        policy = python_support.parse_policy(policy_document())

        python_support.verify_latest_runtime(policy, "3.12")
        with self.assertRaisesRegex(
            python_support.PolicyError, "latest stable runtime is 3.13"
        ):
            python_support.verify_latest_runtime(policy, "3.13")

    def test_latest_canary_rejects_invalid_runtime_syntax(self) -> None:
        policy = python_support.parse_policy(policy_document())

        with self.assertRaisesRegex(python_support.PolicyError, "major.minor syntax"):
            python_support.verify_latest_runtime(policy, "3.12.1")

    def test_single_version_matrix_does_not_duplicate_boundary_entries(self) -> None:
        document = policy_document()
        document["versions"] = ["3.12"]
        policy = python_support.parse_policy(document)

        self.assertEqual(
            python_support.build_matrix(policy),
            {
                "include": [
                    {"os": "ubuntu-latest", "python-version": "3.12"},
                    {"os": "windows-latest", "python-version": "3.12"},
                ]
            },
        )

    def test_running_feature_release_reflects_current_interpreter(self) -> None:
        self.assertEqual(
            python_support.running_python_feature_release(),
            f"{sys.version_info.major}.{sys.version_info.minor}",
        )

    def test_github_output_is_compact_and_uses_policy_latest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            output = StringIO()

            with redirect_stdout(output):
                result = python_support.main(
                    ["--policy", str(path), "emit-github-output"]
                )

            self.assertEqual(result, 0)
            matrix_line, latest_line = output.getvalue().splitlines()
            self.assertTrue(matrix_line.startswith("matrix={"))
            self.assertNotIn(" ", matrix_line)
            self.assertEqual(latest_line, "latest=3.12")

    def test_main_covers_validate_and_runtime_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)

            validate_output = StringIO()
            with redirect_stdout(validate_output):
                validate_result = python_support.main(
                    ["--policy", str(path), "validate"]
                )
            self.assertEqual(validate_result, 0)
            self.assertIn("3.10, 3.11, 3.12", validate_output.getvalue())

            explicit_output = StringIO()
            with redirect_stdout(explicit_output):
                explicit_result = python_support.main(
                    [
                        "--policy",
                        str(path),
                        "verify-latest-runtime",
                        "--runtime",
                        "3.12",
                    ]
                )
            self.assertEqual(explicit_result, 0)
            self.assertIn("3.12 is declared", explicit_output.getvalue())

            detected_output = StringIO()
            with (
                mock.patch.object(
                    python_support,
                    "running_python_feature_release",
                    return_value="3.12",
                ) as detected,
                redirect_stdout(detected_output),
            ):
                detected_result = python_support.main(
                    ["--policy", str(path), "verify-latest-runtime"]
                )
            self.assertEqual(detected_result, 0)
            detected.assert_called_once_with()

    def test_main_reports_policy_errors(self) -> None:
        error_output = StringIO()

        with redirect_stderr(error_output):
            result = python_support.main(
                ["--policy", "does-not-exist.json", "validate"]
            )

        self.assertEqual(result, 1)
        self.assertIn("error: could not read", error_output.getvalue())

    def test_script_entrypoint_exits_after_successful_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(directory)
            output = StringIO()
            argv = [str(SCRIPT_PATH), "--policy", str(path), "validate"]

            with (
                mock.patch.object(sys, "argv", argv),
                redirect_stdout(output),
                self.assertRaises(SystemExit) as raised,
            ):
                runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

            self.assertEqual(raised.exception.code, 0)
            self.assertIn("Python support policy is valid", output.getvalue())


if __name__ == "__main__":
    unittest.main()
