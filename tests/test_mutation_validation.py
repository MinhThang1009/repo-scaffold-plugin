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

    def test_accepts_timeouts_as_detected_and_enforces_the_score_floor(self) -> None:
        self.assertEqual(
            validate_mutation_results.validate_statistics(
                statistics(killed=2, timeout=1)
            ),
            [],
        )
        self.assertEqual(
            validate_mutation_results.validate_statistics(
                statistics(killed=79, survived=21, total=100)
            ),
            [],
        )
        problems = validate_mutation_results.validate_statistics(
            statistics(killed=78, survived=22, total=100)
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("78.00% is below required 78.80%", problems[0])

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
            self.assertIn("3/3 mutants were killed", output.getvalue())

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
