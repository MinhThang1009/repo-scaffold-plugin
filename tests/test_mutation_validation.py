from __future__ import annotations

import importlib.util
import json
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "validate_mutation_results.py"
SPEC = importlib.util.spec_from_file_location(
    "scripts.validate_mutation_results", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load validate_mutation_results.py")
validate_mutation_results = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validate_mutation_results
SPEC.loader.exec_module(validate_mutation_results)


def statistics(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "killed": 3,
        "survived": 0,
        "total": 3,
        "no_tests": 0,
        "skipped": 0,
        "suspicious": 0,
        "timeout": 0,
        "check_was_interrupted_by_user": 0,
        "segfault": 0,
    }
    document.update(changes)
    return document


class MutationStatisticsTests(unittest.TestCase):
    def test_accepts_a_complete_run_without_survivors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "statistics.json"
            path.write_text(json.dumps(statistics()), encoding="utf-8")

            loaded = validate_mutation_results.load_statistics(path)

        self.assertEqual(validate_mutation_results.validate_statistics(loaded), [])

    def test_rejects_every_incomplete_result_class(self) -> None:
        unsafe_fields = validate_mutation_results.UNSAFE_RESULT_FIELDS
        for field in unsafe_fields:
            with self.subTest(field=field):
                document = statistics(killed=2, **{field: 1})
                problems = validate_mutation_results.validate_statistics(document)
                self.assertTrue(any(field in problem for problem in problems))

        self.assertIn(
            "mutation run generated no mutants",
            validate_mutation_results.validate_statistics(
                statistics(killed=0, total=0)
            ),
        )
        self.assertIn(
            "mutation counters account for 3 results but total is 4",
            validate_mutation_results.validate_statistics(statistics(total=4)),
        )

    def test_accepts_timeouts_as_detected_and_requires_no_survivors(self) -> None:
        self.assertEqual(
            validate_mutation_results.validate_statistics(
                statistics(killed=2, timeout=1)
            ),
            [],
        )
        self.assertEqual(
            validate_mutation_results.validate_statistics(
                statistics(killed=0, survived=1, timeout=2)
            ),
            [
                "mutation score 66.66% is below required 100.00% "
                "(2 detected of 3 testable mutants)"
            ],
        )
        self.assertEqual(
            validate_mutation_results.validate_statistics(
                statistics(killed=100, survived=0, total=100)
            ),
            [],
        )
        problems = validate_mutation_results.validate_statistics(
            statistics(killed=99, survived=1, total=100)
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("99.00% is below required 100.00%", problems[0])

    def test_loader_reports_the_exact_schema_failure(self) -> None:
        with self.assertRaises(
            validate_mutation_results.DuplicateJsonMember
        ) as duplicate:
            validate_mutation_results.unique_json_object([("killed", 1), ("killed", 2)])
        self.assertEqual(
            str(duplicate.exception),
            "duplicate JSON member 'killed'",
        )

        cases = (
            ("[]", "mutation statistics root must be a JSON object"),
            (
                "{}",
                "mutation statistics fields differ: "
                "missing=['check_was_interrupted_by_user', 'killed', 'no_tests', "
                "'segfault', 'skipped', 'survived', 'suspicious', 'timeout', "
                "'total'], unexpected=[]",
            ),
            (
                json.dumps({**statistics(), "extra": 0}),
                "mutation statistics fields differ: missing=[], unexpected=['extra']",
            ),
            (
                json.dumps(statistics(killed=True)),
                "mutation statistic 'killed' must be a nonnegative integer",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "statistics.json"
            for content, expected in cases:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError) as raised:
                        validate_mutation_results.load_statistics(path)
                    self.assertEqual(str(raised.exception), expected)

    def test_loader_rejects_a_duplicate_in_an_otherwise_complete_document(
        self,
    ) -> None:
        members = [f'"{key}": {value}' for key, value in statistics().items()]
        members.append('"killed": 3')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "statistics.json"
            path.write_text("{" + ",".join(members) + "}", encoding="utf-8")

            with self.assertRaises(ValueError) as raised:
                validate_mutation_results.load_statistics(path)

        self.assertIn("duplicate JSON member 'killed'", str(raised.exception))

    def test_loader_requests_utf8_explicitly(self) -> None:
        path = mock.Mock(spec=Path)
        path.read_text.return_value = json.dumps(statistics())

        self.assertEqual(validate_mutation_results.load_statistics(path), statistics())
        path.read_text.assert_called_once_with(encoding="utf-8")

    def test_loader_rejects_invalid_json_schema_counts_and_duplicates(self) -> None:
        invalid_documents = (
            "[]",
            "{}",
            json.dumps({**statistics(), "extra": 0}),
            json.dumps(statistics(killed=True)),
            json.dumps(statistics(killed=-1)),
            '{"killed": 1, "killed": 2}',
            "{",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "statistics.json"
            for content in invalid_documents:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        validate_mutation_results.load_statistics(path)

            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "could not read"):
                validate_mutation_results.load_statistics(path)
            path.unlink()
            with self.assertRaisesRegex(ValueError, "could not read"):
                validate_mutation_results.load_statistics(path)

    def test_main_reports_success_validation_failure_and_load_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "statistics.json"
            output = StringIO()
            path.write_text(json.dumps(statistics()), encoding="utf-8")
            with redirect_stdout(output):
                self.assertEqual(validate_mutation_results.main([str(path)]), 0)
            self.assertEqual(
                output.getvalue(),
                "Mutation testing is complete: 3/3 mutants were killed, "
                "0 timed out; mutation score 100.00%.\n",
            )

            output = StringIO()
            path.write_text(
                json.dumps(statistics(killed=79, timeout=21, total=100)),
                encoding="utf-8",
            )
            with redirect_stdout(output):
                self.assertEqual(validate_mutation_results.main([str(path)]), 0)
            self.assertEqual(
                output.getvalue(),
                "Mutation testing is complete: 79/100 mutants were killed, "
                "21 timed out; mutation score 100.00%.\n",
            )

            errors = StringIO()
            path.write_text(
                json.dumps(statistics(killed=2, survived=1)), encoding="utf-8"
            )
            with redirect_stderr(errors):
                self.assertEqual(validate_mutation_results.main([str(path)]), 1)
            self.assertIn("mutation score", errors.getvalue())

            errors = StringIO()
            path.write_text("{", encoding="utf-8")
            with redirect_stderr(errors):
                self.assertEqual(validate_mutation_results.main([str(path)]), 1)
            self.assertIn("could not read mutation statistics", errors.getvalue())

    def test_default_argument_and_script_entrypoint_use_the_standard_artifact(
        self,
    ) -> None:
        self.assertEqual(
            validate_mutation_results.parse_args([]).statistics,
            Path("mutants/mutmut-cicd-stats.json"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "mutants" / "mutmut-cicd-stats.json"
            artifact.parent.mkdir()
            artifact.write_text(json.dumps(statistics()), encoding="utf-8")
            # Instrumented trampolines resolve Mutmut's configured source paths
            # against the current directory before recording the function hit.
            for source_path in ("scripts", "skills/repo-scaffold/scripts"):
                (root / source_path).mkdir(parents=True)
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                output = StringIO()
                with (
                    mock.patch.object(sys, "argv", [str(SCRIPT_PATH)]),
                    redirect_stdout(output),
                    self.assertRaises(SystemExit) as raised,
                ):
                    runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("3/3 mutants were killed", output.getvalue())

    def test_help_uses_the_module_contract_as_its_description(self) -> None:
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            validate_mutation_results.parse_args(["--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn(
            "Validate mutmut CI statistics without accepting incomplete mutation runs.",
            output.getvalue().replace("\n", " "),
        )
