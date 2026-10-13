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
            execute.call_args.args[0][-2:], ["--include", "scripts/run_tests.py"]
        )

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
                    commands[0][-9:],
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
            execute.call_args.args[0][-2:],
            ["--include", "skills/repo-scaffold/scripts/pr_template_preflight.py"],
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
                ["--include", "scripts/run_tests.py," + canonical],
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
                        os.environ, {"PYTHONPYCACHEPREFIX": "unrelated-cache"}
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
