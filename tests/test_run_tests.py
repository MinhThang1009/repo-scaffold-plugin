"""Contracts for fast feedback without weakening the full coverage gate."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from scripts import run_tests


class RunTestsTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(
            run_tests, "project_input_fingerprint", return_value="unchanged"
        )
        self.fingerprint = patcher.start()
        self.addCleanup(patcher.stop)
        copies = mock.patch.object(run_tests, "validate_omitted_copies")
        copies.start()
        self.addCleanup(copies.stop)
        completion = mock.patch.object(run_tests, "validate_pytest_completion")
        self.completion = completion.start()
        self.addCleanup(completion.stop)

    def test_unmeasured_feedback_does_not_require_a_full_input_inventory(self) -> None:
        with (
            mock.patch.object(
                run_tests.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(run_tests.main(["focused", "tests/test_run_tests.py"]), 0)
        self.fingerprint.assert_not_called()
        self.completion.assert_not_called()

    def test_incomplete_pytest_success_stops_before_report_and_cleans_owned_state(
        self,
    ) -> None:
        for mode in ("full", "suite"):
            self.completion.side_effect = ValueError("pytest completion is incomplete")
            with (
                self.subTest(mode=mode),
                mock.patch.object(
                    run_tests.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ) as execute,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()) as errors,
            ):
                self.assertEqual(run_tests.main([mode]), 1)
                execute.assert_called_once()
                self.assertIn("pytest completion", errors.getvalue())
                receipt = self.completion.call_args.args[0]
                self.assertFalse(receipt.parent.exists())

    def test_inspection_is_cleared_in_every_child_without_mutating_parent_environment(
        self,
    ) -> None:
        for arguments in (
            ["full"],
            ["suite"],
            ["focused", "tests/test_run_tests.py"],
            ["focused", "tests/test_run_tests.py", "--source", "scripts/run_tests.py"],
        ):
            with (
                self.subTest(arguments=arguments),
                mock.patch.dict(os.environ, {"PYTHONINSPECT": "1"}),
                mock.patch.object(
                    run_tests.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ) as execute,
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(run_tests.main(arguments), 0)
                for call in execute.call_args_list:
                    self.assertFalse("PYTHONINSPECT" in call.kwargs["env"])
                self.assertEqual(os.environ["PYTHONINSPECT"], "1")

    def test_guarded_children_clear_optimization_without_changing_the_parent(
        self,
    ) -> None:
        for optimize in ("1", "2"):
            for arguments in (
                ["full"],
                ["suite"],
                [
                    "focused",
                    "tests/test_run_tests.py",
                    "--source",
                    "scripts/run_tests.py",
                ],
            ):
                with (
                    self.subTest(optimize=optimize, arguments=arguments),
                    mock.patch.dict(os.environ, {"PYTHONOPTIMIZE": optimize}),
                    mock.patch.object(
                        run_tests.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], 0),
                    ) as execute,
                    redirect_stdout(StringIO()),
                ):
                    self.assertEqual(run_tests.main(arguments), 0)
                    for call in execute.call_args_list:
                        self.assertFalse("PYTHONOPTIMIZE" in call.kwargs["env"])
                    self.assertEqual(os.environ["PYTHONOPTIMIZE"], optimize)

    def test_cli_inspection_rejection_is_fatal_even_if_diagnostic_output_fails(
        self,
    ) -> None:
        for broken_output in (False, True):
            fake_sys = mock.Mock(flags=mock.Mock(inspect=1), stderr=StringIO())
            with (
                self.subTest(broken_output=broken_output),
                mock.patch.object(run_tests, "sys", fake_sys),
                mock.patch.object(
                    run_tests.os, "_exit", side_effect=SystemExit(2)
                ) as terminate,
                mock.patch(
                    "builtins.print",
                    side_effect=OSError("closed") if broken_output else None,
                ) as diagnostic,
                self.assertRaises(SystemExit) as result,
            ):
                run_tests.reject_cli_inspection()
            self.assertEqual(result.exception.code, 2)
            terminate.assert_called_once_with(2)
            self.assertTrue(diagnostic.call_args.kwargs["flush"])
        with (
            mock.patch.object(run_tests, "sys", mock.Mock(flags=mock.Mock(inspect=0))),
            mock.patch.object(run_tests.os, "_exit") as terminate,
        ):
            run_tests.reject_cli_inspection()
            terminate.assert_not_called()

    def test_unmeasured_feedback_preserves_pytest_cache_and_temp_selection(
        self,
    ) -> None:
        controls = {
            "PYTEST_DEBUG_TEMPROOT": "feedback-temp",
            "TOX_ENV_DIR": "feedback-tox",
            "PYTEST_ADDOPTS": "--lf",
            "PYTHONOPTIMIZE": "2",
        }
        with (
            mock.patch.dict(os.environ, controls),
            mock.patch.object(
                run_tests.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as execute,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(run_tests.main(["focused", "tests/test_run_tests.py"]), 0)
            self.assertNotIn("--basetemp", execute.call_args.args[0])
            self.assertFalse(
                any(
                    argument.startswith("cache_dir=")
                    for argument in execute.call_args.args[0]
                )
            )
            for name, value in controls.items():
                self.assertEqual(execute.call_args.kwargs["env"][name], value)

    def test_suite_selection_and_child_execution_stay_bound_to_the_repository(
        self,
    ) -> None:
        selections = ["tests/test_run_tests.py", "tests/test_python_support.py"]
        for arguments in (["suite", *selections], ["full"]):
            with (
                self.subTest(mode=arguments[0]),
                mock.patch.object(
                    run_tests.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ) as execute,
                redirect_stdout(StringIO()) as output,
            ):
                self.assertEqual(run_tests.main(arguments), 0)
                for call in execute.call_args_list:
                    self.assertEqual(call.kwargs["cwd"], run_tests.REPOSITORY_ROOT)
                    self.assertIsInstance(call.kwargs["env"], dict)
                    self.assertEqual(call.args[0][0], sys.executable)
                if arguments[0] == "suite":
                    self.assertEqual(execute.call_args.args[0][-2:], selections)
                    self.assertEqual(
                        output.getvalue(),
                        "Pytest suite: " + ", ".join(selections) + "\n",
                    )
                else:
                    self.assertEqual(output.getvalue(), "Full repository gate\n")
        for call in self.fingerprint.call_args_list:
            self.assertEqual(call.args, (run_tests.REPOSITORY_ROOT,))

    def test_missing_mode_and_default_suite_scope_are_explicit(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
            run_tests.parse_args([])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(run_tests.parse_args(["suite"]).tests, ["tests"])

    def test_command_help_distinguishes_feedback_from_final_verification(self) -> None:
        expected_arguments = {
            "": "positional arguments: {focused,full,suite} "
            "focused Run explicit pytest files or node IDs "
            "full Run all tests once with the repository coverage floor "
            "suite Run pytest without coverage for runtime checks",
            "focused": "positional arguments: tests Test files or pytest node IDs",
            "suite": "positional arguments: tests Explicit test paths, or all tests by default",
        }
        for mode, expected in expected_arguments.items():
            with (
                self.subTest(mode=mode),
                redirect_stdout(StringIO()) as output,
                self.assertRaises(SystemExit) as raised,
            ):
                run_tests.parse_args([mode, "--help"] if mode else ["--help"])
            self.assertEqual(raised.exception.code, 0)
            blocks = output.getvalue().split("\n\n")
            arguments = next(
                block for block in blocks if block.startswith("positional arguments:")
            )
            self.assertEqual(" ".join(arguments.split()), expected)
            if not mode:
                self.assertEqual(
                    " ".join(blocks[1].split()),
                    "Run focused feedback or the full test and coverage gate in one invocation.",
                )
            elif mode == "focused":
                options = blocks[-1].split("\n", 1)[1]
                self.assertEqual(
                    " ".join(options.split()),
                    "-h, --help show this help message and exit "
                    "--source SOURCE Python file to report coverage for",
                )

    def test_configuration_read_is_bounded_and_accepts_the_exact_byte_limit(
        self,
    ) -> None:
        for size in (65536, 65537):
            with (
                self.subTest(size=size),
                mock.patch.object(
                    Path, "open", mock.mock_open(read_data=b" " * size)
                ) as opened,
            ):
                if size == 65536:
                    self.assertEqual(
                        run_tests.read_configuration(".coveragerc"), " " * size
                    )
                else:
                    with self.assertRaisesRegex(ValueError, "65536-byte limit"):
                        run_tests.read_configuration(".coveragerc")
                opened.return_value.read.assert_called_once_with(65537)

    def test_stale_results_identify_the_exact_failed_phase(self) -> None:
        for phase in ("the run", "report generation"):
            with (
                self.subTest(phase=phase),
                mock.patch.object(
                    run_tests, "project_input_fingerprint", return_value="after"
                ) as fingerprint,
                self.assertRaises(ValueError) as raised,
            ):
                run_tests.assert_inputs_current("before", phase)
            fingerprint.assert_called_once_with(run_tests.REPOSITORY_ROOT)
            self.assertEqual(
                str(raised.exception),
                f"test inputs changed during {phase}; result is not current",
            )

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
            mock.patch.dict(
                os.environ,
                {
                    "PYTEST_ADDOPTS": "--lf -k previous",
                    "PYTEST_PLUGINS": "selection_plugin",
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "0",
                },
            ),
            mock.patch.object(
                run_tests.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as execute,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(run_tests.main(["full"]), 0)
            self.assertNotIn("PYTEST_ADDOPTS", execute.call_args_list[0].kwargs["env"])
            self.assertNotIn("PYTEST_PLUGINS", execute.call_args_list[0].kwargs["env"])
            self.assertEqual(
                execute.call_args_list[0].kwargs["env"][
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
                ],
                "1",
            )
            self.assertEqual(os.environ["PYTEST_ADDOPTS"], "--lf -k previous")
            self.assertEqual(os.environ["PYTEST_PLUGINS"], "selection_plugin")

    def test_focused_inputs_cannot_inject_pytest_options_or_coverage_globs(
        self,
    ) -> None:
        for arguments in (
            ["focused", "--", "--collect-only"],
            ["focused", "tests/test_run_tests.py", "--source", "missing.py"],
            ["focused", "tests/test_run_tests.py", "--source", "AGENTS.md"],
            ["focused", "tests/test_run_tests.py", "--source", "scripts/*py"],
            ["focused", "tests/test_run_tests.py", "--source", "scripts/missing.py"],
            ["focused", "tests/test_run_tests.py", "--source", "scripts"],
            [
                "focused",
                "tests/test_run_tests.py",
                "--source",
                "scripts/decision.py:alternate.py",
            ],
            ["suite", "tests/test_run_tests.py:alternate.py"],
        ):
            with (
                self.subTest(arguments=arguments),
                redirect_stderr(StringIO()),
                mock.patch.object(run_tests.subprocess, "run") as execute,
            ):
                self.assertEqual(run_tests.main(arguments), 2)
                execute.assert_not_called()

    def test_coverage_controls_are_cleared_only_in_child_verification_environment(
        self,
    ) -> None:
        controls = {
            "COVERAGE_FORCE_CONFIG": "outside.ini",
            "COVERAGE_PROCESS_START": "outside.ini",
            "COVERAGE_PROCESS_CONFIG": "outside",
            "COVERAGE_DEBUG_FILE": "outside.log",
            "COVERAGE_FILE": "outside.coverage",
        }
        for mode in ("full", "suite", "focused"):
            arguments = (
                [mode]
                if mode != "focused"
                else [
                    "focused",
                    "tests/test_run_tests.py",
                    "--source",
                    "scripts/run_tests.py",
                ]
            )
            with (
                self.subTest(mode=mode),
                mock.patch.dict(os.environ, controls),
                mock.patch.object(
                    run_tests.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ) as execute,
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(run_tests.main(arguments), 0)
                for call in execute.call_args_list:
                    self.assertFalse(
                        any(
                            name.upper().startswith("COVERAGE_")
                            for name in call.kwargs["env"]
                        )
                    )
                self.assertEqual({key: os.environ[key] for key in controls}, controls)

    def test_declared_test_and_source_scope_cannot_escape_the_bound_repository(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "test_outside.py"
            outside.write_text("def test_outside(): pass\n", encoding="utf-8")
            for arguments in (
                ["suite", str(outside)],
                ["suite", "tests/../tests/test_run_tests.py"],
                ["focused", "tests/test_run_tests.py", "--source", str(outside)],
                [
                    "focused",
                    "tests/test_run_tests.py",
                    "--source",
                    "tests/test_run_tests.py",
                ],
            ):
                with (
                    self.subTest(arguments=arguments),
                    redirect_stderr(StringIO()),
                    redirect_stdout(StringIO()),
                    mock.patch.object(
                        run_tests.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], 0),
                    ) as execute,
                ):
                    self.assertEqual(run_tests.main(arguments), 2)
                    execute.assert_not_called()

    def test_literal_absolute_paths_inside_the_repository_are_normalized(self) -> None:
        with (
            mock.patch.object(
                run_tests.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as execute,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(
                run_tests.main(
                    [
                        "focused",
                        str(run_tests.REPOSITORY_ROOT / "tests/test_run_tests.py")
                        + "::RunTestsTests",
                        "--source",
                        str(run_tests.REPOSITORY_ROOT / "scripts/run_tests.py"),
                    ]
                ),
                0,
            )
        self.assertIn(
            "tests/test_run_tests.py::RunTestsTests", execute.call_args_list[0].args[0]
        )
        self.assertEqual(
            execute.call_args.args[0][-1:], [str(Path("scripts/run_tests.py"))]
        )

    def test_node_id_colons_do_not_name_filesystem_streams(self) -> None:
        selected = "tests/test_run_tests.py::RunTestsTests::case[x:y]"
        self.assertEqual(
            run_tests.repository_selection(selected, source=False), selected
        )

    def test_report_sources_require_unique_admitted_inventory_identities(self) -> None:
        for inventory, expected in (
            (
                ["scripts/run_tests.py", "scripts/run_tests.py"],
                "ambiguous path identities",
            ),
            (["scripts/python_support.py"], "outside the admitted inventory"),
        ):
            with (
                self.subTest(inventory=inventory),
                mock.patch.object(
                    run_tests, "coverage_source_inventory", return_value=inventory
                ),
                mock.patch.object(run_tests.subprocess, "run") as execute,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()) as errors,
            ):
                self.assertEqual(
                    run_tests.main(
                        [
                            "focused",
                            "tests/test_run_tests.py",
                            "--source",
                            "scripts/run_tests.py",
                        ]
                    ),
                    1,
                )
                self.assertIn(expected, errors.getvalue())
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
                    [*commands[0][-13:-11], *commands[0][-7:]],
                    [
                        "-m",
                        "pytest",
                        "-c",
                        str(run_tests.REPOSITORY_ROOT / "pyproject.toml"),
                        "-o",
                        "addopts=",
                        "-q",
                        "--durations=20",
                        "tests",
                    ],
                )
                self.assertEqual(
                    commands[1][:4], [sys.executable, "-m", "coverage", "report"]
                )
                self.assertNotIn("--include", commands[1])
                self.assertNotIn("--basetemp", commands[1])
                for command in commands:
                    self.assertNotEqual(
                        command[command.index("--rcfile") + 1],
                        str(run_tests.REPOSITORY_ROOT / ".coveragerc"),
                    )
                self.assertEqual(
                    commands[0][commands[0].index("--rcfile") + 1],
                    commands[1][commands[1].index("--rcfile") + 1],
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
                sys, "path", [str(run_tests.REPOSITORY_ROOT / "scripts"), *sys.path]
            ),
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
            execute.call_args.args[0][-1:],
            [str(Path("skills/repo-scaffold/scripts/pr_template_preflight.py"))],
        )

    def test_focused_copy_selections_report_verified_canonical_sources_once(
        self,
    ) -> None:
        for copy in sorted(run_tests.COVERAGE_OMITTED_COPIES):
            canonical = (Path("scripts") / Path(copy).name).as_posix()
            with (
                self.subTest(copy=copy),
                mock.patch.object(
                    run_tests.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ) as execute,
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(
                    run_tests.main(
                        [
                            "focused",
                            "tests/test_run_tests.py",
                            "--source",
                            "scripts/run_tests.py",
                            "--source",
                            copy,
                            "--source",
                            canonical,
                        ]
                    ),
                    0,
                )
            self.assertEqual(
                execute.call_args.args[0][-2:],
                [str(Path("scripts/run_tests.py")), str(Path(canonical))],
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
                sys, "path", [str(run_tests.REPOSITORY_ROOT / "scripts"), *sys.path]
            ),
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

    def test_changed_inputs_reject_success_before_and_after_reporting(self) -> None:
        for values, expected_calls in (
            (["before", "after"], 1),
            (["before", "before", "after"], 2),
        ):
            with (
                self.subTest(values=values),
                mock.patch.object(
                    run_tests, "project_input_fingerprint", side_effect=values
                ),
                mock.patch.object(
                    run_tests.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ) as execute,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()) as errors,
            ):
                self.assertEqual(run_tests.main(["full"]), 1)
                self.assertIn("inputs changed", errors.getvalue())
                self.assertEqual(execute.call_count, expected_calls)

    def test_suite_runs_the_requested_scope_and_rejects_stale_inputs(self) -> None:
        with (
            mock.patch.object(
                run_tests.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as execute,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(run_tests.main(["suite", "tests/test_run_tests.py"]), 0)
        self.assertIn("addopts=", execute.call_args.args[0])
        self.fingerprint.side_effect = ["before", "after"]
        with (
            mock.patch.object(
                run_tests.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            self.assertEqual(run_tests.main(["suite"]), 1)

    def test_interrupted_measurement_never_reports_or_retains_temporary_data(
        self,
    ) -> None:
        with (
            mock.patch.object(
                run_tests.subprocess, "run", side_effect=KeyboardInterrupt
            ) as execute,
            redirect_stdout(StringIO()),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_tests.main(["full"])
        self.assertEqual(execute.call_count, 1)
        command = execute.call_args.args[0]
        self.assertFalse(
            Path(command[command.index("--data-file") + 1]).parent.exists()
        )
        self.assertFalse(
            Path(execute.call_args.kwargs["env"]["PYTHONPYCACHEPREFIX"]).parent.exists()
        )

    def test_nonregular_input_admission_failure_never_starts_pytest_or_coverage(
        self,
    ) -> None:
        for arguments in (
            ["suite"],
            ["full"],
            ["focused", "tests/test_run_tests.py", "--source", "scripts/run_tests.py"],
        ):
            with (
                self.subTest(arguments=arguments),
                mock.patch.object(
                    run_tests,
                    "project_input_fingerprint",
                    side_effect=ValueError("nonregular verification input"),
                ),
                mock.patch.object(run_tests.subprocess, "run") as execute,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()) as errors,
            ):
                self.assertEqual(run_tests.main(arguments), 1)
                self.assertIn("nonregular verification input", errors.getvalue())
                execute.assert_not_called()

    def test_guarded_bytecode_directories_are_fresh_owned_and_cleaned_on_failure(
        self,
    ) -> None:
        prefixes = []
        for mode in ("full", "suite", "focused"):
            for status in (0, 1):
                arguments = (
                    [mode]
                    if mode != "focused"
                    else [
                        "focused",
                        "tests/test_run_tests.py",
                        "--source",
                        "scripts/run_tests.py",
                    ]
                )
                with (
                    self.subTest(mode=mode, status=status),
                    mock.patch.dict(
                        os.environ,
                        {
                            "PYTHONPYCACHEPREFIX": "unrelated-cache",
                            "PYTEST_DEBUG_TEMPROOT": "unrelated-temp",
                            "TOX_ENV_DIR": "unrelated-tox",
                        },
                    ),
                    mock.patch.object(
                        run_tests.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], status),
                    ) as execute,
                    redirect_stdout(StringIO()),
                ):
                    self.assertEqual(run_tests.main(arguments), status)
                    prefix = execute.call_args.kwargs["env"]["PYTHONPYCACHEPREFIX"]
                    self.assertNotEqual(prefix, "unrelated-cache")
                    self.assertEqual(
                        os.environ["PYTHONPYCACHEPREFIX"], "unrelated-cache"
                    )
                    first = execute.call_args_list[0]
                    command = first.args[0]
                    basetemp = Path(command[command.index("--basetemp") + 1])
                    cache = next(
                        argument.removeprefix("cache_dir=")
                        for argument in command
                        if argument.startswith("cache_dir=")
                    )
                    self.assertEqual(basetemp.parent, Path(prefix).parent)
                    self.assertEqual(Path(cache).parent, Path(prefix).parent)
                    self.assertNotEqual(basetemp, Path(cache))
                    self.assertNotIn("PYTEST_DEBUG_TEMPROOT", first.kwargs["env"])
                    self.assertEqual(
                        os.environ["PYTEST_DEBUG_TEMPROOT"], "unrelated-temp"
                    )
                    self.assertEqual(
                        first.kwargs["env"]["TOX_ENV_DIR"], "unrelated-tox"
                    )
                    self.assertTrue(
                        all(
                            call.kwargs["env"]["PYTHONPYCACHEPREFIX"] == prefix
                            for call in execute.call_args_list
                        )
                    )
                self.assertFalse(Path(prefix).parent.exists())
                prefixes.append(prefix)
        self.assertEqual(len(set(prefixes)), len(prefixes))

    def test_configuration_errors_fail_before_test_execution(self) -> None:
        for payload, message in (
            (b"x" * 65537, "65536-byte"),
            (b"\xff", "could not run"),
            (b"[report]\ninclude = scripts/*.py\n", "coverage policy"),
        ):
            with (
                self.subTest(message=message),
                mock.patch.object(Path, "open", mock.mock_open(read_data=payload)),
                mock.patch.object(run_tests.subprocess, "run") as execute,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()) as errors,
            ):
                self.assertEqual(
                    run_tests.main(
                        [
                            "focused",
                            "tests/test_run_tests.py",
                            "--source",
                            "scripts/run_tests.py",
                        ]
                    ),
                    1,
                )
                self.assertIn(message, errors.getvalue())
                execute.assert_not_called()

    def test_pytest_policy_failure_stops_before_collection(self) -> None:
        with (
            mock.patch.object(
                run_tests, "pytest_policy_errors", return_value=["unsafe pytest policy"]
            ),
            mock.patch.object(run_tests.subprocess, "run") as execute,
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()) as errors,
        ):
            self.assertEqual(run_tests.main(["full"]), 1)
            self.assertIn("unsafe pytest policy", errors.getvalue())
            execute.assert_not_called()

    def test_configuration_cannot_cross_a_link_boundary_or_be_a_directory(self) -> None:
        def admit_selection_reject_config(root: Path, path: Path) -> None:
            if path.name == ".coveragerc":
                raise ValueError("linked config")

        with (
            mock.patch.object(
                run_tests,
                "_assert_safe_project_path",
                side_effect=admit_selection_reject_config,
            ),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()) as errors,
        ):
            self.assertEqual(
                run_tests.main(
                    [
                        "focused",
                        "tests/test_run_tests.py",
                        "--source",
                        "scripts/run_tests.py",
                    ]
                ),
                1,
            )
            self.assertIn("linked config", errors.getvalue())
        with (
            mock.patch.object(Path, "is_file", return_value=False),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()) as errors,
        ):
            self.assertEqual(run_tests.main(["full"]), 1)
            self.assertIn("regular repository file", errors.getvalue())


class PytestCompletionTests(unittest.TestCase):
    def test_bootstrap_and_receipt_are_fresh_owned_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command, receipt = run_tests.guarded_pytest_command(
                ["-m", "pytest", "tests"], root
            )
            self.assertEqual(
                command,
                [
                    str(root / "pytest-bootstrap.py"),
                    str(receipt),
                    "-m",
                    "pytest",
                    "tests",
                ],
            )
            self.assertFalse(receipt.exists())
            self.assertEqual(
                (root / "pytest-bootstrap.py").read_text(encoding="utf-8"),
                run_tests.PYTEST_BOOTSTRAP,
            )
            compile(run_tests.PYTEST_BOOTSTRAP, "pytest-bootstrap.py", "exec")
            with self.assertRaises(FileExistsError):
                run_tests.guarded_pytest_command(["-m", "pytest", "tests"], root)

    def test_only_complete_success_receipts_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "pytest-completion.json"
            receipt.write_text('{"exit_code":0,"complete":true}', encoding="utf-8")
            run_tests.validate_pytest_completion(receipt)
            for payload in (
                b"",
                b"[]",
                b"{}",
                b"invalid",
                b"\xff",
                b'{"exit_code":false,"complete":true}',
                b'{"exit_code":0.0,"complete":true}',
                b'{"exit_code":1,"complete":true}',
                b'{"exit_code":0,"complete":false}',
                b'{"exit_code":0,"complete":1}',
                b'{"exit_code":0,"complete":true,"extra":1}',
                b'{"exit_code":0,"exit_code":0,"complete":true}',
                b"x" * 513,
            ):
                with self.subTest(payload=payload[:80]):
                    receipt.write_bytes(payload)
                    with self.assertRaisesRegex(ValueError, "pytest completion"):
                        run_tests.validate_pytest_completion(receipt)

    def test_missing_nonregular_linked_and_parser_failure_receipts_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "pytest-completion.json"
            with self.assertRaisesRegex(ValueError, "pytest completion"):
                run_tests.validate_pytest_completion(receipt)
            receipt.mkdir()
            with self.assertRaisesRegex(ValueError, "pytest completion"):
                run_tests.validate_pytest_completion(receipt)
            for function, error in (
                ("_assert_safe_project_path", ValueError("linked")),
                ("_read_bounded_bytes", OSError("unreadable")),
            ):
                with (
                    self.subTest(function=function),
                    mock.patch.object(Path, "is_file", return_value=True),
                    mock.patch.object(run_tests, function, side_effect=error),
                    self.assertRaisesRegex(ValueError, "pytest completion"),
                ):
                    run_tests.validate_pytest_completion(receipt)
            with (
                mock.patch.object(Path, "is_file", return_value=True),
                mock.patch.object(run_tests, "_read_bounded_bytes", return_value=b"{}"),
                mock.patch.object(run_tests.json, "loads", side_effect=RecursionError),
                self.assertRaisesRegex(ValueError, "pytest completion"),
            ):
                run_tests.validate_pytest_completion(receipt)


class CoverageInventoryTests(unittest.TestCase):
    def test_report_source_compilation_preserves_encodings_without_execution(
        self,
    ) -> None:
        for content in (
            b"raise RuntimeError('must not execute')\n",
            b"# coding: latin-1\nvalue = '\xe9'\n",
            b"\xef\xbb\xbfvalue = 1\n",
        ):
            with (
                self.subTest(content=content),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                relative = "source.PYW"
                (root / relative).write_bytes(content)
                run_tests.validate_report_source_syntax(root, [relative])
                self.assertEqual((root / relative).read_bytes(), content)

    def test_report_source_compilation_is_bounded_and_rejects_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.py"
            path.write_bytes(b"x=1\n")
            with mock.patch.object(run_tests, "MAX_FILE_BYTES", 4):
                run_tests.validate_report_source_syntax(root, [path.name])
                self.assertEqual(path.read_bytes(), b"x=1\n")
                path.write_bytes(b"x=1\n#")
                with self.assertRaisesRegex(ValueError, "size limits"):
                    run_tests.validate_report_source_syntax(root, [path.name])
            with (
                mock.patch.object(
                    run_tests,
                    "_assert_safe_project_path",
                    side_effect=ValueError("unsafe source"),
                ),
                mock.patch.object(run_tests, "_read_bounded_bytes") as read,
                self.assertRaisesRegex(ValueError, "unsafe source"),
            ):
                run_tests.validate_report_source_syntax(root, [path.name])
            read.assert_not_called()

    def test_report_source_compilation_debits_actual_aggregate_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = ["first.py", "second.py"]
            for relative in sources:
                (root / relative).write_bytes(b"x=1\n")
            with (
                mock.patch.object(run_tests, "MAX_FILE_BYTES", 4),
                mock.patch.object(run_tests, "MAX_TOTAL_BYTES", 7),
                mock.patch.object(
                    run_tests, "compile", create=True, wraps=compile
                ) as compiler,
                self.assertRaisesRegex(ValueError, "size limits"),
            ):
                run_tests.validate_report_source_syntax(root, sources)
            compiler.assert_called_once_with(
                b"x=1\n", "first.py", "exec", dont_inherit=True
            )

    def test_report_source_compilation_rejects_invalid_syntax_and_parser_recursion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "source.PY"
            (root / relative).write_bytes(b"return 1\n")
            with self.assertRaisesRegex(
                ValueError, "coverage source cannot be compiled"
            ):
                run_tests.validate_report_source_syntax(root, [relative])
            (root / relative).write_bytes(b"value = 1\n")
            with (
                mock.patch.object(
                    run_tests, "compile", create=True, side_effect=RecursionError
                ) as compiler,
                self.assertRaisesRegex(
                    ValueError, "coverage source cannot be compiled"
                ),
            ):
                run_tests.validate_report_source_syntax(root, [relative])
            compiler.assert_called_once_with(
                b"value = 1\n", relative, "exec", dont_inherit=True
            )

    def test_source_selection_accepts_python_window_files_not_bytecode_or_text(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            for suffix in (".py", ".pyw", ".pyc", ".pyo", ".txt"):
                relative = "scripts/selected" + suffix
                (root / relative).write_bytes(b"value = 1\n")
                with (
                    self.subTest(suffix=suffix),
                    mock.patch.object(run_tests, "REPOSITORY_ROOT", root),
                ):
                    if suffix in {".py", ".pyw"}:
                        self.assertEqual(
                            run_tests.repository_selection(relative, source=True),
                            relative,
                        )
                    else:
                        with self.assertRaisesRegex(
                            ValueError, "existing literal Python file"
                        ):
                            run_tests.repository_selection(relative, source=True)

    def test_root_admission_precedes_nested_copy_path_access_and_execution(
        self,
    ) -> None:
        for arguments in (
            ["full"],
            ["focused", "tests/test_run_tests.py", "--source", "scripts/run_tests.py"],
        ):
            with (
                self.subTest(arguments=arguments),
                mock.patch.object(
                    run_tests,
                    "coverage_source_inventory",
                    side_effect=ValueError("coverage source root must be a directory"),
                ),
                mock.patch.object(
                    run_tests,
                    "validate_omitted_copies",
                    side_effect=NotADirectoryError("nested path below file"),
                ) as copies,
                mock.patch.object(run_tests.subprocess, "run") as execute,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()) as errors,
            ):
                self.assertEqual(run_tests.main(arguments), 1)
                self.assertIn("source root must be a directory", errors.getvalue())
                copies.assert_not_called()
                execute.assert_not_called()

    def test_missing_or_non_directory_source_root_is_not_optional(self) -> None:
        for kind in ("missing", "file"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "scripts").mkdir()
                source = root / "skills/repo-scaffold/scripts"
                source.parent.mkdir(parents=True)
                if kind == "file":
                    source.write_bytes(b"not a directory")
                with self.assertRaisesRegex(
                    ValueError, "source root must be a directory"
                ):
                    run_tests.coverage_source_inventory(root)

    def test_full_inventory_includes_hidden_and_namespace_sources_not_tests_or_copies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in run_tests.COVERAGE_SOURCE_ROOTS:
                (root / relative).mkdir(parents=True)
            expected = [
                "scripts/.hidden.py",
                "scripts/audit_freshness.py",
                "skills/repo-scaffold/scripts/nested/window.pyw",
            ]
            for relative in (
                *expected,
                "tests/test_example.py",
                "scripts/notes.txt",
                "skills/repo-scaffold/scripts/audit_freshness.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("value = 1\n", encoding="utf-8")
            self.assertEqual(run_tests.coverage_source_inventory(root), expected)

    def test_empty_source_inventory_and_unsafe_roots_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in run_tests.COVERAGE_SOURCE_ROOTS:
                (root / relative).mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "contains no Python files"):
                run_tests.coverage_source_inventory(root)
            with (
                mock.patch.object(
                    run_tests,
                    "_assert_safe_project_path",
                    side_effect=ValueError("unsafe root"),
                ),
                self.assertRaisesRegex(ValueError, "unsafe root"),
            ):
                run_tests.coverage_source_inventory(root)
            with (
                mock.patch.object(
                    run_tests,
                    "_project_files",
                    side_effect=ValueError("inventory limit"),
                ) as inventory,
                self.assertRaisesRegex(ValueError, "inventory limit"),
            ):
                run_tests.coverage_source_inventory(root)
            inventory.assert_called_once_with(root, verification=True)


class CoverageCopyAdmissionTests(unittest.TestCase):
    def test_present_omitted_copies_are_verified_with_bounded_actual_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_tests.validate_omitted_copies(root)
            copied = root / "skills/repo-scaffold/scripts/audit_freshness.py"
            canonical = root / "scripts/audit_freshness.py"
            for path in (copied, canonical):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"same")
            run_tests.validate_omitted_copies(root)
            canonical.write_bytes(b"drift")
            with self.assertRaisesRegex(ValueError, "differs from canonical"):
                run_tests.validate_omitted_copies(root)
            canonical.write_bytes(b"same")
            with (
                mock.patch.object(run_tests, "MAX_FILE_BYTES", 3),
                self.assertRaisesRegex(ValueError, "source size limit"),
            ):
                run_tests.validate_omitted_copies(root)

    def test_missing_nonregular_or_linked_omitted_copy_inputs_fail_closed(self) -> None:
        for kind in ("missing-source", "source-directory", "copy-directory", "linked"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                copied = root / "skills/repo-scaffold/scripts/audit_freshness.py"
                canonical = root / "scripts/audit_freshness.py"
                copied.parent.mkdir(parents=True)
                canonical.parent.mkdir()
                if kind == "copy-directory":
                    copied.mkdir()
                else:
                    copied.write_bytes(b"same")
                if kind == "source-directory":
                    canonical.mkdir()
                elif kind != "missing-source":
                    canonical.write_bytes(b"same")
                if kind == "linked":
                    with (
                        mock.patch.object(
                            run_tests,
                            "_assert_safe_project_path",
                            side_effect=ValueError("linked input"),
                        ),
                        self.assertRaisesRegex(ValueError, "linked input"),
                    ):
                        run_tests.validate_omitted_copies(root)
                else:
                    with self.assertRaisesRegex(ValueError, "regular source/copy"):
                        run_tests.validate_omitted_copies(root)
