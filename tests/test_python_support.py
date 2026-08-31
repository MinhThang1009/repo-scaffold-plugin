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
SPEC = importlib.util.spec_from_file_location("scripts.python_support", SCRIPT_PATH)
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

    def test_policy_shape_errors_are_exact_and_actionable(self) -> None:
        cases: tuple[tuple[object, str], ...] = (
            ([], "policy root must be an object"),
            (
                {**policy_document(), "alpha": True, "zeta": True},
                "unknown fields: alpha, zeta",
            ),
            (
                {
                    "schema-version": 1,
                    "versions": ["3.12"],
                    "full-coverage-os": ["ubuntu-latest"],
                },
                "missing fields: boundary-coverage-os, implementation",
            ),
            (
                {**policy_document(), "schema-version": True},
                "schema-version must be the integer 1",
            ),
            (
                {**policy_document(), "implementation": "pypy"},
                "implementation must be cpython",
            ),
            (
                {**policy_document(), "versions": ["3.10.1"]},
                "versions must use stable CPython feature-release syntax such as 3.14",
            ),
            (
                {**policy_document(), "versions": ["3.10", "3.12"]},
                "versions must be ordered, contiguous, and gap-free",
            ),
            (
                {
                    **policy_document(),
                    "full-coverage-os": ["ubuntu-latest", "windows-latest"],
                    "boundary-coverage-os": ["windows-latest", "ubuntu-latest"],
                },
                "full-coverage-os and boundary-coverage-os must not overlap: "
                "ubuntu-latest, windows-latest",
            ),
        )
        for document, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(python_support.PolicyError) as raised:
                    python_support.parse_policy(document)
                self.assertEqual(str(raised.exception), expected)

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

    def test_policy_rejects_excessive_list_entries(self) -> None:
        document = policy_document()
        document["versions"] = [
            f"3.{minor}"
            for minor in range(10, 10 + python_support.MAX_SUPPORTED_VERSIONS + 1)
        ]

        with self.assertRaisesRegex(python_support.PolicyError, "more than"):
            python_support.parse_policy(document)

    def test_policy_rejects_invalid_version_and_runner_syntax(self) -> None:
        cases = [
            ("versions", ["3.10.1"], "feature-release syntax"),
            ("versions", ["2.7"], "feature-release syntax"),
            ("full-coverage-os", ["ubuntu latest"], "unsupported GitHub-hosted"),
            ("boundary-coverage-os", ["-windows"], "unsupported GitHub-hosted"),
            ("full-coverage-os", ["self-hosted"], "unsupported GitHub-hosted"),
            ("boundary-coverage-os", ["custom-runner"], "unsupported GitHub-hosted"),
        ]

        for field, value, message in cases:
            document = policy_document()
            document[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(python_support.PolicyError, message):
                    python_support.parse_policy(document)

    def test_policy_reports_the_exact_allowed_runner_labels(self) -> None:
        document = policy_document()
        document["full-coverage-os"] = ["self-hosted"]

        with self.assertRaises(python_support.PolicyError) as raised:
            python_support.parse_policy(document)

        self.assertEqual(
            str(raised.exception),
            "unsupported GitHub-hosted runner label 'self-hosted'; allowed labels: "
            "macos-latest, ubuntu-latest, windows-latest",
        )

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

    def test_policy_loader_rejects_oversized_and_recursive_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(b" " * (python_support.MAX_POLICY_BYTES + 1))

            with self.assertRaisesRegex(python_support.PolicyError, "size limit"):
                python_support.load_policy(path)

            path.write_text(json.dumps(policy_document()), encoding="utf-8")
            with (
                mock.patch.object(
                    python_support.json,
                    "loads",
                    side_effect=RecursionError("too deep"),
                ),
                self.assertRaisesRegex(python_support.PolicyError, "could not read"),
            ):
                python_support.load_policy(path)

    def test_latest_canary_detects_a_new_stable_release(self) -> None:
        policy = python_support.parse_policy(policy_document())

        python_support.verify_latest_runtime(policy, "3.12")
        with self.assertRaisesRegex(
            python_support.PolicyError, "latest stable runtime is 3.13"
        ) as raised:
            python_support.verify_latest_runtime(policy, "3.13")
        self.assertEqual(
            str(raised.exception),
            "latest stable runtime is 3.13, but policy ends at 3.12; verify "
            "compatibility, then update .github/python-support.json",
        )

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
            self.assertEqual(
                output.getvalue(),
                'matrix={"include":[{"os":"ubuntu-latest","python-version":'
                '"3.10"},{"os":"ubuntu-latest","python-version":"3.11"},'
                '{"os":"ubuntu-latest","python-version":"3.12"},{"os":'
                '"windows-latest","python-version":"3.10"},{"os":'
                '"windows-latest","python-version":"3.12"}]}\nlatest=3.12\n',
            )

    def test_command_help_is_a_stable_user_facing_contract(self) -> None:
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            python_support.parse_args(["--help"])

        self.assertEqual(raised.exception.code, 0)
        help_text = " ".join(output.getvalue().split())
        for fragment in (
            "Validate Python support policy and emit its deterministic CI matrix.",
            "Path to the Python support policy",
            "Validate the policy",
            "Emit matrix and latest values for GITHUB_OUTPUT",
            "Compare the running interpreter with the policy's latest release",
        ):
            self.assertIn(fragment, help_text)
        self.assertNotIn("XX", help_text)

        verify_help = StringIO()
        with redirect_stdout(verify_help), self.assertRaises(SystemExit) as raised:
            python_support.parse_args(["verify-latest-runtime", "--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(
            "Override the running major.minor version for deterministic tests",
            " ".join(verify_help.getvalue().split()),
        )
        self.assertNotIn("XX", verify_help.getvalue())

    def test_policy_path_defaults_and_a_subcommand_is_required(self) -> None:
        self.assertEqual(
            python_support.parse_args(["validate"]).policy.as_posix(),
            ".github/python-support.json",
        )
        errors = StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            python_support.parse_args([])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn(
            "the following arguments are required: command", errors.getvalue()
        )

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
