from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


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

    def test_duplicate_json_members_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"schema-version":1,"schema-version":1}', encoding="utf-8")

            with self.assertRaisesRegex(
                python_support.PolicyError, "duplicate JSON member"
            ):
                python_support.load_policy(path)

    def test_latest_canary_detects_a_new_stable_release(self) -> None:
        policy = python_support.parse_policy(policy_document())

        python_support.verify_latest_runtime(policy, "3.12")
        with self.assertRaisesRegex(
            python_support.PolicyError, "latest stable runtime is 3.13"
        ):
            python_support.verify_latest_runtime(policy, "3.13")

    def test_github_output_is_compact_and_uses_policy_latest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy_document()), encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
