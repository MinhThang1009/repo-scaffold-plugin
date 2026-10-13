"""Contracts for fast feedback without weakening the full coverage gate."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from scripts import run_tests


class RunTestsTests(unittest.TestCase):
    def test_focused_selection_executes_only_requested_tests_and_propagates_failure(
        self,
    ) -> None:
        with mock.patch.object(
            run_tests.subprocess, "run", return_value=subprocess.CompletedProcess([], 5)
        ) as execute:
            with redirect_stdout(StringIO()):
                result = run_tests.main(
                    ["focused", "tests/test_pr_template_preflight.py::Example"]
                )
        self.assertEqual(result, 5)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(
            execute.call_args.args[0],
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--durations=20",
                "tests/test_pr_template_preflight.py::Example",
            ],
        )

    def test_focused_mode_requires_selection_and_full_mode_rejects_selection(
        self,
    ) -> None:
        for arguments in (
            ["focused"],
            ["full", "tests/test_run_tests.py"],
            ["full", "--source", "scripts/run_tests.py"],
        ):
            with (
                self.subTest(arguments=arguments),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                run_tests.parse_args(arguments)

    def test_full_run_does_not_inherit_environment_test_selection(self) -> None:
        with (
            mock.patch.dict(os.environ, {"PYTEST_ADDOPTS": "--lf -k previous"}),
            mock.patch.object(
                run_tests.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as execute,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(run_tests.main(["full"]), 0)
            self.assertNotIn("PYTEST_ADDOPTS", execute.call_args_list[0].kwargs["env"])
            self.assertEqual(os.environ["PYTEST_ADDOPTS"], "--lf -k previous")

    def test_focused_inputs_cannot_inject_pytest_options_or_coverage_globs(
        self,
    ) -> None:
        for arguments in (
            ["focused", "--", "--collect-only"],
            ["focused", "tests/test_run_tests.py", "--source", "missing.py"],
            ["focused", "tests/test_run_tests.py", "--source", "AGENTS.md"],
            ["focused", "tests/test_run_tests.py", "--source", "scripts/*py"],
        ):
            with (
                self.subTest(arguments=arguments),
                redirect_stderr(StringIO()),
                mock.patch.object(run_tests.subprocess, "run") as execute,
            ):
                self.assertEqual(run_tests.main(arguments), 2)
                execute.assert_not_called()

    def test_fresh_full_coverage_runs_pytest_once_and_keeps_required_configuration(
        self,
    ) -> None:
        data_files = []
        for _ in range(2):
            with (
                mock.patch.object(
                    run_tests.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ) as execute,
                redirect_stdout(StringIO()) as output,
            ):
                self.assertEqual(run_tests.main(["full"]), 0)
                commands = [call.args[0] for call in execute.call_args_list]
                self.assertEqual(len(commands), 2)
                self.assertEqual(
                    commands[0][:4], [sys.executable, "-m", "coverage", "run"]
                )
                self.assertEqual(
                    commands[0][-5:], ["-m", "pytest", "-q", "--durations=20", "tests"]
                )
                self.assertEqual(
                    commands[1][:4], [sys.executable, "-m", "coverage", "report"]
                )
                self.assertNotIn("--include", commands[1])
                for command in commands:
                    self.assertEqual(
                        command[command.index("--rcfile") + 1],
                        str(run_tests.REPOSITORY_ROOT / ".coveragerc"),
                    )
                data_file = commands[0][commands[0].index("--data-file") + 1]
                self.assertEqual(
                    commands[1][commands[1].index("--data-file") + 1], data_file
                )
                self.assertIn("Full repository gate", output.getvalue())
                data_files.append(data_file)
            self.assertFalse(Path(data_file).parent.exists())
        self.assertNotEqual(*data_files)

    def test_focused_coverage_filters_only_report_and_propagates_coverage_failure(
        self,
    ) -> None:
        with (
            mock.patch.object(
                run_tests.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 2),
                ],
            ) as execute,
            redirect_stdout(StringIO()) as output,
        ):
            result = run_tests.main(
                [
                    "focused",
                    "tests/test_pr_template_preflight.py",
                    "--source",
                    "skills/repo-scaffold/scripts/pr_template_preflight.py",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("Focused feedback only", output.getvalue())
        self.assertEqual(
            execute.call_args.args[0][-2:],
            ["--include", "skills/repo-scaffold/scripts/pr_template_preflight.py"],
        )

    def test_failed_tests_skip_report_and_clean_their_owned_data_directory(
        self,
    ) -> None:
        with (
            mock.patch.object(
                run_tests.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1),
            ) as execute,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(run_tests.main(["full"]), 1)
        self.assertEqual(execute.call_count, 1)
        command = execute.call_args.args[0]
        self.assertFalse(
            Path(command[command.index("--data-file") + 1]).parent.exists()
        )

    def test_process_errors_are_reported_without_a_success_status(self) -> None:
        with (
            mock.patch.object(
                run_tests.subprocess, "run", side_effect=OSError("denied")
            ),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()) as errors,
        ):
            self.assertEqual(run_tests.main(["full"]), 1)
        self.assertIn("could not run test checks", errors.getvalue())

    def test_entrypoint_preserves_pytest_exit_status(self) -> None:
        with (
            mock.patch.object(
                sys, "argv", ["run_tests.py", "focused", "tests/test_run_tests.py"]
            ),
            mock.patch.object(
                subprocess, "run", return_value=subprocess.CompletedProcess([], 5)
            ),
            redirect_stdout(StringIO()),
            self.assertRaises(SystemExit) as result,
        ):
            runpy.run_path(
                str(run_tests.REPOSITORY_ROOT / "scripts/run_tests.py"),
                run_name="__main__",
            )
        self.assertEqual(result.exception.code, 5)
