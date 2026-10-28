"""Execute the actual runner on isolated projects with adversarial pytest config."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import py_compile
import shutil
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
POLICY = (ROOT / ".coveragerc").read_text(encoding="utf-8")
WarningBudgetCase = tuple[str, str, int | None, bool]
DRIVER = (
    "from pathlib import Path; import sys; "
    "sys.path.insert(0, sys.argv[1]); "
    "from scripts import run_tests; "
    "run_tests.REPOSITORY_ROOT = Path(sys.argv[2]); "
    "raise SystemExit(run_tests.main(sys.argv[3:]))"
)


class NativeRunTestsTests(unittest.TestCase):
    def warning_budget_result(
        self,
        root: Path,
        mode: str,
        hook: str,
        budget: int | None,
        emit_warning: bool,
    ) -> subprocess.CompletedProcess[str]:
        self.project(root)
        if budget is not None:
            configuration = root / "pyproject.toml"
            configuration.write_text(
                configuration.read_text(encoding="utf-8")
                + f"max_warnings = {budget}\n",
                encoding="utf-8",
            )
        test = (
            "import warnings\nfrom scripts.decision import value\n"
            "def test_good():\n    assert value() == 1\n"
        )
        if emit_warning:
            test += "    warnings.warn('synthetic warning budget', UserWarning)\n"
        (root / "tests/test_good.py").write_text(test, encoding="utf-8")
        if hook != "none":
            program = (
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    session.exitstatus = 0\n"
            )
            if hook == "wrapper":
                program = (
                    "import pytest\n@pytest.hookimpl(wrapper=True, tryfirst=True)\n"
                    "def pytest_sessionfinish(session, exitstatus):\n"
                    "    result = yield\n    session.exitstatus = 0\n    return result\n"
                )
            elif hook.startswith("registered-"):
                priority = hook.removeprefix("registered-")
                modifier = "" if priority == "normal" else f", {priority}=True"
                program = (
                    "import pytest\nclass Relabel:\n"
                    f"    @pytest.hookimpl(wrapper=True{modifier})\n"
                    "    def pytest_sessionfinish(self, session, exitstatus):\n"
                    "        result = yield\n        session.exitstatus = 0\n        return result\n"
                    "@pytest.hookimpl(trylast=True)\n"
                    "def pytest_configure(config):\n"
                    "    config.pluginmanager.register(Relabel(), 'synthetic-relabel')\n"
                )
            (root / "tests/conftest.py").write_text(program, encoding="utf-8")
        arguments = (
            ["tests", "--source", "scripts/decision.py"] if mode == "focused" else []
        )
        return self.run_runner(
            root,
            mode,
            arguments=arguments,
            coverage_environment={"PYTHONWARNINGS": "default"},
        )

    def warning_budget_results(
        self, cases: list[WarningBudgetCase]
    ) -> list[tuple[WarningBudgetCase, subprocess.CompletedProcess[str]]]:
        """Overlap isolated subprocesses, not unittest reporting or shared state."""

        def run(case: WarningBudgetCase) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as directory:
                return self.warning_budget_result(Path(directory), *case)

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(zip(cases, pool.map(run, cases)))

    def test_warning_budget_workers_preserve_cap_order_and_owned_cleanup(self) -> None:
        import threading

        cases: list[WarningBudgetCase] = [
            ("suite", "none", 0, False),
            ("full", "ordinary", 1, True),
            ("focused", "wrapper", None, True),
            ("suite", "registered-normal", 1, True),
        ]
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        roots: list[Path] = []
        active = peak = 0

        def probe(root: Path, *case: object) -> subprocess.CompletedProcess[str]:
            nonlocal active, peak
            with lock:
                roots.append(root)
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=5)
                return subprocess.CompletedProcess([], 0, stdout=repr(case), stderr="")
            finally:
                with lock:
                    active -= 1

        with (
            mock.patch.object(self, "warning_budget_result", side_effect=probe),
            mock.patch(
                __name__ + ".ThreadPoolExecutor", wraps=ThreadPoolExecutor
            ) as executor,
        ):
            results = self.warning_budget_results(cases)

        executor.assert_called_once_with(max_workers=2)
        self.assertEqual((active, peak), (0, 2))
        self.assertEqual([case for case, _ in results], cases)
        self.assertEqual(
            [result.stdout for _, result in results], list(map(repr, cases))
        )
        self.assertEqual(len(set(roots)), len(cases))
        self.assertTrue(all(not root.exists() for root in roots))

    def test_warning_budget_worker_errors_propagate_after_owned_cleanup(self) -> None:
        import threading

        roots: list[Path] = []
        lock = threading.Lock()

        def fail(root: Path, *case: object) -> subprocess.CompletedProcess[str]:
            del case
            with lock:
                roots.append(root)
            raise ValueError("synthetic worker failure")

        with (
            mock.patch.object(self, "warning_budget_result", side_effect=fail),
            self.assertRaisesRegex(ValueError, "synthetic worker failure"),
        ):
            self.warning_budget_results([("suite", "none", 0, False)] * 4)

        self.assertTrue(roots)
        self.assertTrue(all(not root.exists() for root in roots))

    def test_late_warning_budget_failure_cannot_be_relabelled_as_success(self) -> None:
        cases: list[WarningBudgetCase] = [
            (mode, hook, 0, True)
            for mode in ("suite", "full", "focused")
            for hook in (
                "none",
                "ordinary",
                "wrapper",
                "registered-normal",
                "registered-tryfirst",
                "registered-trylast",
            )
        ]
        for (mode, hook, _, _), result in self.warning_budget_results(cases):
            with self.subTest(mode=mode, hook=hook):
                self.assertEqual(
                    result.returncode,
                    1
                    if hook in {"wrapper", "registered-normal", "registered-tryfirst"}
                    else 6,
                    result.stdout + result.stderr,
                )
                self.assertIn("maximum allowed warnings exceeded", result.stdout)
                self.assertNotIn("TOTAL", result.stdout)
                if hook in {"wrapper", "registered-normal", "registered-tryfirst"}:
                    self.assertIn("pytest completion receipt", result.stderr)

    def test_warning_budgets_preserve_success_and_optional_warning_policy(self) -> None:
        cases: list[WarningBudgetCase] = [
            (mode, hook, budget, emit_warning)
            for mode in ("suite", "full", "focused")
            for hook in (
                "none",
                "ordinary",
                "wrapper",
                "registered-normal",
                "registered-tryfirst",
                "registered-trylast",
            )
            for budget, emit_warning in ((0, False), (1, True), (None, True))
        ]
        for (mode, hook, budget, _), result in self.warning_budget_results(cases):
            with self.subTest(mode=mode, hook=hook, budget=budget):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("warnings exceeded", result.stdout)
                if mode != "suite":
                    self.assertIn("100.00%", result.stdout)

    def test_warning_budget_preserves_filter_capture_and_cli_precedence(self) -> None:
        for mode in ("suite", "full", "focused"):
            for handling in ("filtered", "captured"):
                with (
                    self.subTest(mode=mode, handling=handling),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    configuration = root / "pyproject.toml"
                    policy = (
                        configuration.read_text(encoding="utf-8") + "max_warnings = 0\n"
                    )
                    if handling == "filtered":
                        policy += 'filterwarnings = ["ignore:synthetic warning budget:UserWarning"]\n'
                    configuration.write_text(policy, encoding="utf-8")
                    test = (
                        "import warnings, pytest\nfrom scripts.decision import value\n"
                        "def test_good():\n    assert value() == 1\n"
                    )
                    if handling == "captured":
                        test += "    with pytest.warns(UserWarning):\n        warnings.warn('synthetic warning budget', UserWarning)\n"
                    else:
                        test += "    warnings.warn('synthetic warning budget', UserWarning)\n"
                    (root / "tests/test_good.py").write_text(test, encoding="utf-8")
                    arguments = (
                        ["tests", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )

                    result = self.run_runner(root, mode, arguments=arguments)

                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )
                    self.assertNotIn("warnings exceeded", result.stdout)

        for configured, override, expected in (
            ("0", 1, 0),
            ("1", 0, 6),
            ("invalid", 1, 0),
            ("-1", 0, 6),
        ):
            with (
                self.subTest(configured=configured, override=override),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.project(root)
                configuration = root / "pyproject.toml"
                configuration.write_text(
                    configuration.read_text(encoding="utf-8")
                    + f'max_warnings = "{configured}"\n',
                    encoding="utf-8",
                )
                (root / "tests/test_good.py").write_text(
                    "import warnings\nfrom scripts.decision import value\n"
                    "def test_good():\n    assert value() == 1\n"
                    "    warnings.warn('synthetic warning budget', UserWarning)\n",
                    encoding="utf-8",
                )

                result = self.run_runner(
                    root,
                    "focused",
                    arguments=["tests", "--source", "scripts/decision.py"],
                    addopts=f"--max-warnings={override}",
                )

                self.assertEqual(
                    result.returncode, expected, result.stdout + result.stderr
                )

    def test_guarded_cleanup_preserves_readonly_outside_hardlink_attributes(
        self,
    ) -> None:
        for mode in ("suite", "full"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                container = Path(directory)
                root = container / "target"
                root.mkdir()
                self.project(root)
                bucket = container / "runtime"
                bucket.mkdir()
                sentinel = container / "outside.dat"
                sentinel.write_bytes(b"immutable outside fixture")
                os.chmod(sentinel, stat.S_IREAD)
                before = sentinel.stat()
                (root / "tests/test_good.py").write_text(
                    "import os\nfrom scripts.decision import value\n"
                    "def test_good(tmp_path):\n    assert value()==1\n"
                    f"    os.link({str(sentinel)!r}, tmp_path / 'readonly-link')\n",
                    encoding="utf-8",
                )
                try:
                    result = self.run_runner(
                        root,
                        mode,
                        coverage_environment={
                            name: str(bucket) for name in ("TMPDIR", "TEMP", "TMP")
                        },
                    )
                    self.assertEqual(
                        sentinel.stat().st_mode,
                        before.st_mode,
                        result.stdout + result.stderr,
                    )
                    self.assertEqual(
                        sentinel.read_bytes(), b"immutable outside fixture"
                    )
                    self.assertEqual(
                        result.returncode, 1 if sys.platform == "win32" else 0
                    )
                    if sys.platform == "win32":
                        self.assertIn("cleanup", result.stderr)
                        self.assertEqual(len(list(bucket.iterdir())), 1)
                finally:
                    os.chmod(sentinel, stat.S_IREAD | stat.S_IWRITE)

    def test_complete_sessions_preserve_skips_xfails_and_duplicate_nodeids(
        self,
    ) -> None:
        for mode in ("suite", "full", "focused"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.project(root)
                (root / "tests/test_applicability.py").write_text(
                    "import pytest\n"
                    "@pytest.mark.skip(reason='not applicable')\ndef test_skip():\n    assert False\n"
                    "@pytest.mark.xfail(reason='expected')\ndef test_xfail():\n    assert False\n",
                    encoding="utf-8",
                )
                arguments = [] if mode == "full" else ["tests", "tests/test_good.py"]
                if mode == "focused":
                    arguments += ["--source", "scripts/decision.py"]
                result = self.run_runner(root, mode, arguments=arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("1 skipped", result.stdout)
                self.assertIn("1 xfailed", result.stdout)

    def test_measured_fixture_inspection_cannot_certify_unexecuted_test_bodies(
        self,
    ) -> None:
        for option in ("--setup-only", "--setup-plan"):
            for origin in ("config", "environment"):
                with (
                    self.subTest(option=option, origin=origin),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root, addopts=option if origin == "config" else "")
                    (root / "scripts/decision.py").write_text(
                        "value = 1\n", encoding="utf-8"
                    )
                    (root / "tests/test_good.py").write_text(
                        "from scripts.decision import value\n"
                        "def test_good():\n    assert value == 2\n",
                        encoding="utf-8",
                    )
                    result = self.run_runner(
                        root,
                        "focused",
                        addopts=option if origin == "environment" else "",
                        arguments=[
                            "tests/test_good.py",
                            "--source",
                            "scripts/decision.py",
                        ],
                    )
                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertIn("pytest completion", result.stderr)
                    self.assertNotIn("TOTAL", result.stdout)

    def test_session_errors_cannot_be_relabelled_as_success(self) -> None:
        for mode in ("suite", "full", "focused"):
            for exception, native_status in (
                ("RuntimeError('synthetic hook failure')", 3),
                ("KeyboardInterrupt()", 2),
            ):
                for relabel in (False, True):
                    with (
                        self.subTest(mode=mode, exception=exception, relabel=relabel),
                        tempfile.TemporaryDirectory() as directory,
                    ):
                        root = Path(directory)
                        self.project(root)
                        hook = (
                            "import pytest\n"
                            "@pytest.hookimpl(trylast=True)\n"
                            "def pytest_runtest_logfinish(nodeid):\n"
                            f"    raise {exception}\n"
                        )
                        if relabel:
                            hook += (
                                "def pytest_sessionfinish(session, exitstatus):\n"
                                "    session.exitstatus = 0\n"
                            )
                        (root / "tests/conftest.py").write_text(hook, encoding="utf-8")
                        arguments = (
                            ["tests", "--source", "scripts/decision.py"]
                            if mode == "focused"
                            else []
                        )

                        result = self.run_runner(root, mode, arguments=arguments)

                        self.assertEqual(
                            result.returncode,
                            1 if relabel else native_status,
                            result.stdout + result.stderr,
                        )
                        self.assertNotIn("TOTAL", result.stdout)
                        if relabel:
                            self.assertIn("pytest completion", result.stderr)

    def test_failed_session_status_is_not_hidden_by_sessionfinish_hook(self) -> None:
        for mode in ("suite", "full", "focused"):
            for relabel in (False, True):
                with (
                    self.subTest(mode=mode, relabel=relabel),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    (root / "tests/test_good.py").write_text(
                        "from scripts.decision import value\n"
                        "def test_good(request):\n    assert value() == 1\n"
                        "    request.session.shouldfail = 'synthetic session policy failure'\n",
                        encoding="utf-8",
                    )
                    if relabel:
                        (root / "tests/conftest.py").write_text(
                            "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n",
                            encoding="utf-8",
                        )
                    arguments = (
                        ["tests", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )

                    result = self.run_runner(root, mode, arguments=arguments)

                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertNotIn("TOTAL", result.stdout)
                    if relabel:
                        self.assertIn("pytest completion", result.stderr)

    def test_sessionfinish_zero_exit_preserves_original_verdict(self) -> None:
        for mode in ("suite", "full", "focused"):
            for failed in (False, True):
                with (
                    self.subTest(mode=mode, failed=failed),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    if failed:
                        (root / "tests/test_good.py").write_text(
                            "from scripts.decision import value\n"
                            "def test_good(request):\n    assert value() == 1\n"
                            "    request.session.shouldfail = 'synthetic failure'\n",
                            encoding="utf-8",
                        )
                    (root / "tests/conftest.py").write_text(
                        "import pytest\n@pytest.hookimpl(tryfirst=True)\n"
                        "def pytest_sessionfinish(session, exitstatus):\n"
                        "    pytest.exit('synthetic zero exit', returncode=0)\n",
                        encoding="utf-8",
                    )
                    arguments = (
                        ["tests", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )

                    result = self.run_runner(root, mode, arguments=arguments)

                    self.assertEqual(
                        result.returncode,
                        1 if failed else 0,
                        result.stdout + result.stderr,
                    )
                    if failed:
                        self.assertIn("pytest completion", result.stderr)
                        self.assertNotIn("TOTAL", result.stdout)

    def test_error_notification_exit_cannot_override_verification(self) -> None:
        for mode in ("suite", "full", "focused"):
            for callback, exception in (
                ("pytest_internalerror", "RuntimeError('synthetic error')"),
                ("pytest_keyboard_interrupt", "KeyboardInterrupt()"),
            ):
                with (
                    self.subTest(mode=mode, callback=callback),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    (root / "tests/conftest.py").write_text(
                        "import pytest\n@pytest.hookimpl(trylast=True)\n"
                        "def pytest_runtest_logfinish(nodeid):\n"
                        f"    raise {exception}\n"
                        "@pytest.hookimpl(tryfirst=True)\n"
                        f"def {callback}():\n"
                        "    pytest.exit('synthetic handler exit', returncode=0)\n",
                        encoding="utf-8",
                    )
                    arguments = (
                        ["tests", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )

                    result = self.run_runner(root, mode, arguments=arguments)

                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertNotIn("TOTAL", result.stdout)

    def test_failed_reports_cannot_be_relabelled_as_a_successful_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root, failing=True)
            (root / "tests/conftest.py").write_text(
                "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n",
                encoding="utf-8",
            )
            result = self.run_runner(root, "suite")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("pytest completion", result.stderr)

    def test_zero_exit_before_finishing_collected_tests_cannot_pass_guarded_runs(
        self,
    ) -> None:
        for mode in ("suite", "full", "focused"):
            for exit_call in ("os._exit(0)", "pytest.exit('early stop', returncode=0)"):
                with (
                    self.subTest(mode=mode, exit_call=exit_call),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root, failing=True)
                    (root / "tests/test_aaa_exit.py").write_text(
                        "import os, pytest\nfrom scripts.decision import value\n"
                        "def test_early():\n    assert value() == 1\n    "
                        + exit_call
                        + "\n",
                        encoding="utf-8",
                    )
                    arguments = (
                        ["tests", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )

                    result = self.run_runner(root, mode, arguments=arguments)

                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertIn("pytest completion", result.stderr)
                    self.assertNotIn("TOTAL", result.stdout)

    def test_comma_delimited_coverage_lists_preserve_native_gate_results(self) -> None:
        for paths in (
            "scripts\n    skills/repo-scaffold/scripts",
            "skills/repo-scaffold/scripts/audit_freshness.py\n    "
            "skills/repo-scaffold/scripts/render_pr_body_evidence.py\n    "
            "skills/repo-scaffold/scripts/sync_action_pins.py",
        ):
            for uncovered in (False, True):
                with (
                    self.subTest(paths=paths, uncovered=uncovered),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    if uncovered:
                        (root / "scripts/uncovered.py").write_text(
                            "def unused():\n    return 2\n", encoding="utf-8"
                        )
                    (root / ".coveragerc").write_text(
                        POLICY.replace(paths, paths.replace("\n    ", ", ")),
                        encoding="utf-8",
                    )

                    result = self.run_runner(root)

                    self.assertEqual(
                        result.returncode,
                        2 if uncovered else 0,
                        result.stdout + result.stderr,
                    )

    def test_unicode_coverage_list_separators_fail_before_native_tests(self) -> None:
        for paths in (
            "scripts\n    skills/repo-scaffold/scripts",
            "skills/repo-scaffold/scripts/audit_freshness.py\n    "
            "skills/repo-scaffold/scripts/render_pr_body_evidence.py\n    "
            "skills/repo-scaffold/scripts/sync_action_pins.py",
        ):
            with self.subTest(paths=paths), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.project(root)
                (root / ".coveragerc").write_text(
                    POLICY.replace(paths, paths.replace("\n    ", "\u2028")),
                    encoding="utf-8",
                )

                result = self.run_runner(root)

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("require branch coverage", result.stderr)
                self.assertNotIn("1 passed", result.stdout)

    def test_guarded_pytest_config_nesting_fails_before_test_execution_without_traceback(
        self,
    ) -> None:
        for mode in ("full", "suite"):
            for nested in (
                "[" * 2000 + "0" + "]" * 2000,
                "{x=" * 2000 + "0" + "}" * 2000,
            ):
                with (
                    self.subTest(mode=mode, shape=nested[:3]),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root, failing=True)
                    configuration = root / "pyproject.toml"
                    original = configuration.read_text(encoding="utf-8")
                    configuration.write_text(
                        "metadata = " + nested + "\n" + original, encoding="utf-8"
                    )
                    result = self.run_runner(root, mode)
                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertTrue(
                        "pytest policy nesting exceeds parser limit" in result.stderr
                    )
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn("test_bad", result.stdout)

    def test_guarded_runs_preserve_unrewritten_assertions_under_inherited_optimization(
        self,
    ) -> None:
        for mode in ("full", "suite", "focused"):
            for optimize in ("1", "2"):
                with (
                    self.subTest(mode=mode, optimize=optimize),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    (root / "tests/checks.py").write_text(
                        "def check():\n    assert False, 'helper invariant fails'\n",
                        encoding="utf-8",
                    )
                    (root / "tests/test_good.py").write_text(
                        "from checks import check\nfrom scripts.decision import value\n"
                        "def test_good():\n    check()\n    assert value() == 1\n",
                        encoding="utf-8",
                    )
                    arguments = (
                        ["tests/test_good.py", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )
                    result = self.run_runner(
                        root,
                        mode,
                        arguments=arguments,
                        coverage_environment={"PYTHONOPTIMIZE": optimize},
                    )
                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertIn(
                        "AssertionError: helper invariant fails", result.stdout
                    )

    def test_guarded_successful_children_use_the_unoptimized_runtime(self) -> None:
        for mode in ("full", "suite", "focused"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.project(root)
                (root / "tests/test_good.py").write_text(
                    "import sys\nfrom scripts.decision import value\n"
                    "def test_good():\n    assert sys.flags.optimize == 0\n"
                    "    assert value() == 1\n",
                    encoding="utf-8",
                )
                arguments = (
                    ["tests/test_good.py", "--source", "scripts/decision.py"]
                    if mode == "focused"
                    else []
                )
                result = self.run_runner(
                    root,
                    mode,
                    arguments=arguments,
                    coverage_environment={"PYTHONOPTIMIZE": "2"},
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_inspection_cannot_turn_failing_tests_into_success(self) -> None:
        for inspection in ("environment", "command-line"):
            for arguments in (
                ["full"],
                ["suite"],
                ["focused", "tests/test_bad.py"],
                ["focused", "tests/test_bad.py", "--source", "scripts/decision.py"],
            ):
                with (
                    self.subTest(inspection=inspection, arguments=arguments),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root, failing=True)
                    for filename in (
                        "run_tests.py",
                        "coverage_policy.py",
                        "prepare_mutation_cache.py",
                    ):
                        shutil.copy2(
                            ROOT / "scripts" / filename, root / "scripts" / filename
                        )
                    environment = os.environ.copy()
                    for name in ("GH_TOKEN", "GITHUB_TOKEN", "PYTHONINSPECT"):
                        environment.pop(name, None)
                    if inspection == "environment":
                        environment["PYTHONINSPECT"] = "1"
                    flags = ["-i"] if inspection == "command-line" else []
                    result = subprocess.run(
                        [sys.executable, *flags, "scripts/run_tests.py", *arguments],
                        cwd=root,
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(
                        result.returncode, 2, result.stdout + result.stderr
                    )
                    self.assertIn("inspection", result.stderr)
                    self.assertNotIn("Full repository gate", result.stdout)

    def test_unparseable_admitted_sources_cannot_disappear_from_the_denominator(
        self,
    ) -> None:
        for mode in ("full", "focused"):
            for suffix in (".py", ".pyw", ".PY", ".PYW"):
                for source in ("def broken(:\n", "return 1\n"):
                    with (
                        self.subTest(mode=mode, suffix=suffix, source=source),
                        tempfile.TemporaryDirectory() as directory,
                    ):
                        root = Path(directory)
                        self.project(root)
                        relative = "scripts/unparseable" + suffix
                        (root / relative).write_text(source, encoding="utf-8")
                        arguments = (
                            [
                                "tests/test_good.py",
                                "--source",
                                "scripts/decision.py",
                                "--source",
                                relative,
                            ]
                            if mode == "focused"
                            else []
                        )
                        result = self.run_runner(root, mode, arguments=arguments)
                        self.assertEqual(
                            result.returncode, 1, result.stdout + result.stderr
                        )
                        self.assertIn(
                            "coverage source cannot be compiled", result.stderr
                        )
                        self.assertNotIn("1 passed", result.stdout)

    def test_optimized_parents_cannot_skip_source_compile_rejections(self) -> None:
        for optimization in ("1", "2"):
            for mode, suffix in (("full", ".PY"), ("focused", ".PYW")):
                with (
                    self.subTest(optimization=optimization, mode=mode, suffix=suffix),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    relative = "scripts/unparseable" + suffix
                    (root / relative).write_text("assert (yield 1)\n", encoding="utf-8")
                    arguments = (
                        [
                            "tests/test_good.py",
                            "--source",
                            "scripts/decision.py",
                            "--source",
                            relative,
                        ]
                        if mode == "focused"
                        else []
                    )
                    result = self.run_runner(
                        root,
                        mode,
                        arguments=arguments,
                        coverage_environment={"PYTHONOPTIMIZE": optimization},
                    )
                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertIn("coverage source cannot be compiled", result.stderr)
                    self.assertNotIn("1 passed", result.stdout)
                    self.assertNotIn("TOTAL", result.stdout)

    def test_source_analysis_preserves_warning_policy_and_owned_data_paths(
        self,
    ) -> None:
        for mode in ("full", "focused"):
            for warning_policy in ("default", "error"):
                with (
                    self.subTest(mode=mode, warning_policy=warning_policy),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    container = Path(directory)
                    root = container / "project"
                    root.mkdir()
                    self.project(root)
                    outside = container / "must-not-create" / ".coverage"
                    (root / ".coveragerc").write_text(
                        POLICY.replace(
                            "[run]",
                            f"[run]\nparallel = true\ndata_file = {outside.as_posix()}",
                        ),
                        encoding="utf-8",
                    )
                    (root / ".coverage").write_bytes(b"preserve-existing-data")
                    arguments = (
                        ["tests/test_good.py", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )
                    result = self.run_runner(
                        root,
                        mode,
                        arguments=arguments,
                        coverage_environment={"PYTHONWARNINGS": warning_policy},
                    )
                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )
                    self.assertNotIn("no-data-collected", result.stderr)
                    self.assertFalse(outside.parent.exists())
                    self.assertEqual(
                        (root / ".coverage").read_bytes(), b"preserve-existing-data"
                    )

    def test_report_parser_failures_cannot_drop_compilable_sources(self) -> None:
        for mode in ("full", "focused"):
            for suffix in (".PY", ".PYW"):
                with (
                    self.subTest(mode=mode, suffix=suffix),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    relative = "scripts/uncovered" + suffix
                    source = "def unused():\n    result = 1\n    \f    return result\n"
                    compile(source, relative, "exec", dont_inherit=True, optimize=0)
                    (root / relative).write_text(source, encoding="utf-8")
                    arguments = (
                        [
                            "tests",
                            "--source",
                            "scripts/decision.py",
                            "--source",
                            relative,
                        ]
                        if mode == "focused"
                        else []
                    )
                    result = self.run_runner(root, mode, arguments=arguments)
                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertIn("coverage source cannot be analyzed", result.stderr)
                    self.assertNotIn("1 passed", result.stdout)
                    self.assertNotIn("TOTAL", result.stdout)

    def test_valid_uppercase_suffix_sources_stay_in_full_reports(self) -> None:
        for suffix in (".PY", ".PYW"):
            for covered in (False, True):
                with (
                    self.subTest(suffix=suffix, covered=covered),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    relative = "scripts/extra" + suffix
                    (root / relative).write_text(
                        "def extra():\n    return 2\n", encoding="utf-8"
                    )
                    if covered:
                        (root / "tests/test_good.py").write_text(
                            "import runpy\nfrom scripts.decision import value\n"
                            "def test_good():\n    assert value() == 1\n"
                            f"    assert runpy.run_path({relative!r})['extra']() == 2\n",
                            encoding="utf-8",
                        )
                    result = self.run_runner(root)
                    self.assertEqual(
                        result.returncode,
                        0 if covered else 2,
                        result.stdout + result.stderr,
                    )

    def test_unselected_unparseable_source_does_not_expand_focused_or_suite_scope(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            (root / "scripts/unparseable.PY").write_text(
                "def broken(:\n", encoding="utf-8"
            )
            for mode in ("suite", "focused"):
                arguments = (
                    ["tests/test_good.py", "--source", "scripts/decision.py"]
                    if mode == "focused"
                    else []
                )
                result = self.run_runner(root, mode, arguments=arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_runner_fixtures_type_check_on_supported_platforms(self) -> None:
        for platform in ("linux", "darwin", "win32"):
            with (
                self.subTest(platform=platform),
                tempfile.TemporaryDirectory() as directory,
            ):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "mypy",
                        "--platform",
                        platform,
                        "--explicit-package-bases",
                        "--cache-dir",
                        directory,
                        str(Path(__file__).resolve()),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=45,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_guarded_pytest_temp_and_cache_outputs_are_private_and_preserved(
        self,
    ) -> None:
        for mode in ("suite", "full", "focused"):
            for failing in (False, True):
                with (
                    self.subTest(mode=mode, failing=failing),
                    tempfile.TemporaryDirectory() as directory,
                    tempfile.TemporaryDirectory() as outside,
                ):
                    root = Path(directory)
                    self.project(root)
                    external = Path(outside)
                    (external / "preserve.txt").write_bytes(b"unrelated")
                    saved = root / ".pytest_cache/v/cache/lastfailed"
                    saved.parent.mkdir(parents=True)
                    saved.write_bytes(b'{"unrelated::test": true}')
                    receipt = external / "receipt.txt"
                    (root / "tests/test_good.py").write_text(
                        "from pathlib import Path\nfrom scripts.decision import value\n"
                        "def test_good(tmp_path, request):\n    assert value() == 1\n"
                        "    (tmp_path / 'created.txt').write_text('owned')\n"
                        "    request.config.cache.set('synthetic/ownership', 'owned')\n"
                        f"    Path({str(receipt)!r}).write_text(str(tmp_path.resolve()) + '\\n' + str(request.config.cache._cachedir.resolve()), encoding='utf-8')\n"
                        f"    assert {not failing!r}\n",
                        encoding="utf-8",
                    )
                    arguments = (
                        ["tests/test_good.py", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )
                    results = self.run_runner(
                        root,
                        mode,
                        arguments=arguments,
                        addopts=(
                            "--basetemp "
                            + repr(outside.replace("\\", "/"))
                            + " -o "
                            + repr("cache_dir=" + outside.replace("\\", "/"))
                            + " --cache-clear"
                        )
                        if mode == "focused"
                        else "",
                        coverage_environment={
                            "PYTEST_DEBUG_TEMPROOT": outside,
                            "TOX_ENV_DIR": outside,
                        },
                    )
                    self.assertEqual(
                        results.returncode,
                        1 if failing else 0,
                        results.stdout + results.stderr,
                    )
                    temporary, cache = map(
                        Path, receipt.read_text(encoding="utf-8").splitlines()
                    )
                    self.assertEqual(temporary.parent.parent, cache.parent)
                    self.assertFalse(cache.parent.exists())
                    self.assertEqual(saved.read_bytes(), b'{"unrelated::test": true}')
                    self.assertEqual(
                        (external / "preserve.txt").read_bytes(), b"unrelated"
                    )
                    self.assertEqual(
                        sorted(item.name for item in external.iterdir()),
                        ["preserve.txt", "receipt.txt"],
                    )

    def test_concurrent_guarded_pytest_outputs_use_distinct_owned_roots(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as output_directory,
        ):
            root = Path(directory)
            self.project(root)
            receipts = Path(output_directory)
            (root / "tests/test_good.py").write_text(
                "import os\nfrom pathlib import Path\nfrom scripts.decision import value\n"
                "def test_good(tmp_path, request):\n    assert value() == 1\n"
                "    request.config.cache.set('synthetic/ownership', 'owned')\n"
                f"    (Path({str(receipts)!r}) / str(os.getpid())).write_text(str(tmp_path.parent.resolve()) + '\\n' + str(request.config.cache._cachedir.resolve()), encoding='utf-8')\n",
                encoding="utf-8",
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: self.run_runner(root), range(2)))
            for result in results:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            records = [
                path.read_text(encoding="utf-8").splitlines()
                for path in receipts.iterdir()
            ]
            self.assertEqual(len(records), 2)
            roots = [Path(temporary).parent for temporary, _ in records]
            self.assertEqual(len(set(roots)), 2)
            for (temporary, cache), runtime_root in zip(records, roots):
                self.assertEqual(Path(cache).parent, runtime_root)
                self.assertFalse(Path(temporary).exists())
                self.assertFalse(runtime_root.exists())

    def test_owned_pytest_outputs_use_one_physical_root_through_temp_aliases(
        self,
    ) -> None:
        for mode in ("suite", "full", "focused"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                root = base / "project"
                root.mkdir()
                self.project(root)
                actual = base / "actual-temp"
                actual.mkdir()
                alias = base / "alias-temp"
                if sys.platform == "win32":
                    powershell = (
                        Path(os.environ["SystemRoot"])
                        / "System32/WindowsPowerShell/v1.0/powershell.exe"
                    )
                    created = subprocess.run(
                        [
                            str(powershell),
                            "-NoProfile",
                            "-NonInteractive",
                            "-Command",
                            "New-Item -ItemType Junction -Path $env:REPO_TEST_TEMP_ALIAS -Value $env:REPO_TEST_TEMP_TARGET -ErrorAction Stop | Out-Null",
                        ],
                        env={
                            **os.environ,
                            "REPO_TEST_TEMP_ALIAS": str(alias),
                            "REPO_TEST_TEMP_TARGET": str(actual),
                        },
                        capture_output=True,
                        text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        timeout=15,
                        check=False,
                    )
                    self.assertEqual(created.returncode, 0, created.stderr)
                else:
                    alias.symlink_to(actual, target_is_directory=True)
                receipt = base / "receipt.txt"
                (root / "tests/test_good.py").write_text(
                    "import sys\nfrom pathlib import Path\nfrom scripts.decision import value\n"
                    "def test_good(tmp_path, request):\n    assert value() == 1\n"
                    "    request.config.cache.set('synthetic/ownership', 'owned')\n"
                    f"    Path({str(receipt)!r}).write_text(str(tmp_path.parent.resolve()) + '\\n' + str(request.config.cache._cachedir.resolve()) + '\\n' + str(Path(sys.pycache_prefix).parent.resolve()), encoding='utf-8')\n",
                    encoding="utf-8",
                )
                arguments = (
                    ["tests/test_good.py", "--source", "scripts/decision.py"]
                    if mode == "focused"
                    else []
                )
                result = self.run_runner(
                    root,
                    mode,
                    arguments=arguments,
                    coverage_environment={
                        "TMP": str(alias),
                        "TEMP": str(alias),
                        "TMPDIR": str(alias),
                    },
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                temporary, cache, runtime = map(
                    Path, receipt.read_text(encoding="utf-8").splitlines()
                )
                self.assertEqual(temporary.parent, runtime)
                self.assertEqual(cache.parent, runtime)
                self.assertFalse(runtime.exists())
                self.assertTrue(alias.exists())
                self.assertEqual(list(actual.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "requires Windows case-insensitive paths")
    def test_full_report_omits_only_verified_copies_with_native_casing(self) -> None:
        for covered in (False, True):
            with (
                self.subTest(covered=covered),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.project(root)
                for relative in (
                    "scripts/audit_freshness.py",
                    "skills/repo-scaffold/scripts/AUDIT_FRESHNESS.PY",
                ):
                    (root / relative).write_text(
                        "def extra():\n    return 2\n", encoding="utf-8"
                    )
                if covered:
                    (root / "tests/test_good.py").write_text(
                        "from scripts.decision import value\nfrom scripts.audit_freshness import extra\n"
                        "def test_good():\n    assert value() == 1\n    assert extra() == 2\n",
                        encoding="utf-8",
                    )
                result = self.run_runner(root)
                self.assertEqual(
                    result.returncode,
                    0 if covered else 2,
                    result.stdout + result.stderr,
                )
                self.assertNotIn("AUDIT_FRESHNESS.PY", result.stdout)

    @unittest.skipUnless(os.name == "nt", "requires Windows case-insensitive paths")
    def test_case_insensitive_roots_cannot_hide_fixture_changes(self) -> None:
        for mode in ("suite", "full"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.project(root)
                (root / "scripts").rename(root / "Scripts")
                fixture = root / "Scripts/vendor/state.txt"
                fixture.parent.mkdir()
                fixture.write_text("before", encoding="utf-8")
                (root / "tests/test_good.py").write_text(
                    "import runpy\nfrom pathlib import Path\ndef test_good():\n"
                    "    assert runpy.run_path('Scripts/decision.py')['value']() == 1\n"
                    "    Path('Scripts/vendor/state.txt').write_text('after', encoding='utf-8')\n",
                    encoding="utf-8",
                )
                result = self.run_runner(root, mode)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("inputs changed", result.stderr)

    def test_full_inventory_reports_undiscovered_pywindow_source(self) -> None:
        for covered in (False, True):
            with (
                self.subTest(covered=covered),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.project(root)
                relative = "skills/repo-scaffold/scripts/nested/.hidden.pyw"
                source = root / relative
                source.parent.mkdir()
                source.write_text("def extra():\n    return 2\n", encoding="utf-8")
                if covered:
                    (root / "tests/test_good.py").write_text(
                        "import runpy\nfrom scripts.decision import value\n"
                        "def test_good():\n    assert value() == 1\n"
                        f"    assert runpy.run_path({relative!r})['extra']() == 2\n",
                        encoding="utf-8",
                    )
                result = self.run_runner(root)
                self.assertEqual(
                    result.returncode,
                    0 if covered else 2,
                    result.stdout + result.stderr,
                )
                if not covered:
                    self.assertIn(".hidden.pyw", result.stdout)

    def test_focused_pywindow_sources_follow_the_full_coverage_contract(self) -> None:
        for covered in (False, True):
            with (
                self.subTest(covered=covered),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.project(root)
                relative = "skills/repo-scaffold/scripts/.hidden.pyw"
                (root / relative).write_text(
                    "def extra():\n    return 2\n", encoding="utf-8"
                )
                if covered:
                    (root / "tests/test_good.py").write_text(
                        "import runpy\nfrom scripts.decision import value\ndef test_good():\n"
                        "    assert value() == 1\n"
                        f"    assert runpy.run_path({relative!r})['extra']() == 2\n",
                        encoding="utf-8",
                    )
                result = self.run_runner(
                    root,
                    "focused",
                    arguments=[
                        "tests/test_good.py",
                        "--source",
                        "scripts/decision.py",
                        "--source",
                        relative,
                    ],
                )
                self.assertEqual(
                    result.returncode,
                    0 if covered else 2,
                    result.stdout + result.stderr,
                )
                self.assertNotIn("invalid test/source selection", result.stderr)
                if not covered:
                    self.assertIn(".hidden.pyw", result.stdout)

    @unittest.skipUnless(os.name == "nt", "requires Windows case-insensitive paths")
    def test_windows_source_casing_reports_the_admitted_canonical_identity(
        self,
    ) -> None:
        for selected in (
            "Scripts/Decision.PY",
            "Skills/Repo-Scaffold/Scripts/Audit_Freshness.PY",
        ):
            with (
                self.subTest(selected=selected),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.project(root)
                for name in (
                    "scripts/audit_freshness.py",
                    "skills/repo-scaffold/scripts/audit_freshness.py",
                ):
                    (root / name).write_text(
                        "def extra():\n    return 2\n", encoding="utf-8"
                    )
                (root / "tests/test_good.py").write_text(
                    "from scripts.decision import value\nfrom scripts.audit_freshness import extra\n"
                    "def test_good():\n    assert value() == 1\n    assert extra() == 2\n",
                    encoding="utf-8",
                )
                result = self.run_runner(
                    root,
                    "focused",
                    arguments=["tests/test_good.py", "--source", selected],
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("100%", result.stdout)

    @unittest.skipUnless(os.name == "nt", "requires Windows alternate data streams")
    def test_alternate_data_stream_source_is_rejected_before_measured_execution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            alternate = "scripts/decision.py:alternate.py"
            (root / alternate).write_text(
                "def value():\n    return 2\n", encoding="utf-8"
            )
            result = self.run_runner(
                root,
                "focused",
                arguments=["tests/test_good.py", "--source", alternate],
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("alternate data streams", result.stderr)
            self.assertNotIn("1 passed", result.stdout)

    def test_measured_checks_reject_missing_or_non_directory_source_roots(self) -> None:
        for kind in ("missing", "file"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.project(root)
                source = root / "skills/repo-scaffold/scripts"
                source.rmdir()
                if kind == "file":
                    source.write_text("not a source directory", encoding="utf-8")
                result = self.run_runner(root)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("coverage source root must be a directory", result.stderr)
                self.assertNotIn("1 passed", result.stdout)

    def test_undiscovered_python_files_remain_in_full_and_focused_reports(self) -> None:
        for mode in ("full", "focused"):
            for covered in (False, True):
                with (
                    self.subTest(mode=mode, covered=covered),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    hidden = root / "scripts/.hidden.py"
                    hidden.write_text("def extra():\n    return 2\n", encoding="utf-8")
                    if covered:
                        (root / "tests/test_good.py").write_text(
                            "import runpy\nfrom scripts.decision import value\n"
                            "def test_good():\n    assert value() == 1\n"
                            "    assert runpy.run_path('scripts/.hidden.py')['extra']() == 2\n",
                            encoding="utf-8",
                        )
                    arguments = (
                        [
                            "tests/test_good.py",
                            "--source",
                            "scripts/decision.py",
                            "--source",
                            "scripts/.hidden.py",
                        ]
                        if mode == "focused"
                        else []
                    )
                    result = self.run_runner(root, mode, arguments=arguments)
                    self.assertEqual(
                        result.returncode,
                        0 if covered else 2,
                        result.stdout + result.stderr,
                    )
                    if not covered:
                        self.assertIn(".hidden.py", result.stdout)

    def test_guarded_runs_do_not_execute_stale_source_bytecode(self) -> None:
        for mode in ("suite", "full", "focused"):
            for invalidation in (
                py_compile.PycInvalidationMode.TIMESTAMP,
                py_compile.PycInvalidationMode.UNCHECKED_HASH,
            ):
                with (
                    self.subTest(mode=mode, invalidation=invalidation),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    source = root / "scripts/decision.py"
                    before = source.stat()
                    shared = root / ".pytest_cache/old-bytecode"
                    with mock.patch.object(sys, "pycache_prefix", str(shared)):
                        cache_name = py_compile.compile(
                            str(source), doraise=True, invalidation_mode=invalidation
                        )
                    assert cache_name is not None
                    cached = Path(cache_name)
                    cached_bytes = cached.read_bytes()
                    source.write_text("def value():\n    return 2\n", encoding="utf-8")
                    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
                    self.assertEqual(source.stat().st_size, before.st_size)
                    arguments = (
                        ["tests/test_good.py", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )
                    result = self.run_runner(
                        root,
                        mode,
                        arguments=arguments,
                        coverage_environment={"PYTHONPYCACHEPREFIX": str(shared)},
                    )
                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertIn("test_good", result.stdout)
                    self.assertEqual(cached.read_bytes(), cached_bytes)

    def test_focused_omitted_copy_uses_canonical_coverage_in_every_requested_scope(
        self,
    ) -> None:
        for covered in (False, True):
            for selected in (
                "scripts/audit_freshness.py",
                "skills/repo-scaffold/scripts/audit_freshness.py",
            ):
                with (
                    self.subTest(covered=covered, selected=selected),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    for relative in (
                        "scripts/audit_freshness.py",
                        "skills/repo-scaffold/scripts/audit_freshness.py",
                    ):
                        (root / relative).write_text(
                            "def extra():\n    return 2\n", encoding="utf-8"
                        )
                    if covered:
                        (root / "tests/test_good.py").write_text(
                            "from scripts.decision import value\nfrom scripts.audit_freshness import extra\ndef test_good():\n    assert value() == 1\n    assert extra() == 2\n",
                            encoding="utf-8",
                        )
                    result = self.run_runner(
                        root,
                        "focused",
                        arguments=[
                            "tests/test_good.py",
                            "--source",
                            "scripts/decision.py",
                            "--source",
                            selected,
                        ],
                    )
                    self.assertEqual(
                        result.returncode,
                        0 if covered else 2,
                        result.stdout + result.stderr,
                    )

    def test_omitted_copy_requires_matching_canonical_bytes(self) -> None:
        for kind in ("matching", "drift", "missing-source"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.project(root)
                (root / "skills/repo-scaffold/scripts/audit_freshness.py").write_text(
                    "value = 1\n" if kind == "matching" else "value = 2\n",
                    encoding="utf-8",
                )
                if kind != "missing-source":
                    (root / "scripts/audit_freshness.py").write_text(
                        "value = 1\n", encoding="utf-8"
                    )
                    (root / "tests/test_good.py").write_text(
                        "from scripts.decision import value\nfrom scripts.audit_freshness import value as checked\ndef test_good():\n    assert value() == checked == 1\n",
                        encoding="utf-8",
                    )
                result = self.run_runner(root)
                self.assertEqual(
                    result.returncode,
                    0 if kind == "matching" else 1,
                    result.stdout + result.stderr,
                )

    def test_config_output_sinks_cannot_mutate_unrelated_paths(self) -> None:
        for kind in ("coverage-debug", "pytest-log", "pytest-cache"):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory() as directory,
                tempfile.TemporaryDirectory() as output_directory,
            ):
                root = Path(directory)
                self.project(root)
                output = Path(output_directory) / "unrelated.log"
                output.write_bytes(b"preserve-existing\n")
                if kind == "coverage-debug":
                    (root / ".coveragerc").write_text(
                        POLICY.replace(
                            "[run]",
                            "[run]\ndebug = config\ndebug_file = " + str(output),
                        ),
                        encoding="utf-8",
                    )
                else:
                    config = root / "pyproject.toml"
                    key, value = (
                        ("log_file", str(output))
                        if kind == "pytest-log"
                        else ("cache_dir", output_directory)
                    )
                    config.write_text(
                        config.read_text(encoding="utf-8")
                        + key
                        + " = "
                        + repr(value.replace("\\", "/"))
                        + "\n",
                        encoding="utf-8",
                    )
                result = self.run_runner(root)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(output.read_bytes(), b"preserve-existing\n")
                self.assertEqual(
                    [item.name for item in Path(output_directory).iterdir()],
                    ["unrelated.log"],
                )

    def project(self, root: Path, *, addopts: str = "", failing: bool = False) -> None:
        (root / "scripts").mkdir()
        (root / "tests").mkdir()
        (root / "skills/repo-scaffold/scripts").mkdir(parents=True)
        (root / ".coveragerc").write_text(POLICY, encoding="utf-8")
        (root / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n'
            'markers = ["good: synthetic positive case"]\n'
            f"addopts = {addopts!r}\n",
            encoding="utf-8",
        )
        (root / "scripts/decision.py").write_text(
            "def value():\n    return 1\n", encoding="utf-8"
        )
        (root / "tests/test_good.py").write_text(
            "import pytest\nfrom scripts.decision import value\n"
            "@pytest.mark.good\ndef test_good():\n    assert value() == 1\n",
            encoding="utf-8",
        )
        if failing:
            (root / "tests/test_bad.py").write_text(
                "from scripts.decision import value\ndef test_bad():\n    assert value() == 2\n",
                encoding="utf-8",
            )

    def run_runner(
        self,
        root: Path,
        mode: str = "full",
        *,
        addopts: str = "",
        plugins: str = "",
        coverage_environment: dict[str, str] | None = None,
        arguments: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTEST_ADDOPTS"] = addopts
        environment["PYTEST_PLUGINS"] = plugins
        environment["MUTANT_UNDER_TEST"] = ""
        environment["MUTMUT_DEPENDENCY_DEPTH"] = "-1"
        environment.update(coverage_environment or {})
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            environment.pop(name, None)
        return subprocess.run(
            [
                sys.executable,
                "-c",
                DRIVER,
                str(ROOT),
                str(root),
                mode,
                *(arguments or []),
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    def test_full_and_suite_do_not_omit_failing_tests_due_to_config_or_environment(
        self,
    ) -> None:
        for mode in ("full", "suite"):
            for selection in (
                "-k good",
                "-m good",
                "--lf --last-failed-no-failures none",
            ):
                with (
                    self.subTest(mode=mode, selection=selection),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root, addopts=selection, failing=True)
                    result = self.run_runner(root, mode, addopts="-k good")
                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertIn("test_bad", result.stdout)
                    self.assertNotIn("deselected", result.stdout)

    def test_full_uses_the_canonical_config_despite_a_higher_priority_local_ini(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root, failing=True)
            (root / "pytest.ini").write_text(
                "[pytest]\npython_files = test_good.py\n", encoding="utf-8"
            )
            result = self.run_runner(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("test_bad", result.stdout)

    def test_discovery_overrides_cannot_omit_failing_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root, failing=True)
            config = root / "pyproject.toml"
            config.write_text(
                config.read_text(encoding="utf-8")
                + 'python_files = ["test_good.py"]\n',
                encoding="utf-8",
            )
            result = self.run_runner(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("canonical test discovery", result.stderr)

    def test_inherited_plugin_cannot_deselect_a_failing_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root, failing=True)
            (root / "selection_plugin.py").write_text(
                "def pytest_collection_modifyitems(config, items):\n"
                "    removed = [item for item in items if item.name == 'test_bad']\n"
                "    items[:] = [item for item in items if item.name != 'test_bad']\n"
                "    config.hook.pytest_deselected(items=removed)\n",
                encoding="utf-8",
            )
            result = self.run_runner(root, plugins="selection_plugin")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("test_bad", result.stdout)

    def test_prefixed_coverage_sections_cannot_override_native_gate_results(
        self,
    ) -> None:
        for mode in ("full", "focused"):
            for settings in (
                "[coverage:report]\nfail_under = 0\n",
                "[coverage:report]\ninclude = scripts/decision.py\n",
                "[coverage:run]\nbranch = false\n",
                "[coverage:paths]\nalias = scripts\n",
            ):
                with (
                    self.subTest(mode=mode, settings=settings),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    (root / "scripts/decision.py").write_text(
                        "def value(flag=True):\n    result = 1\n"
                        "    if flag:\n        result = 1\n    return result\n",
                        encoding="utf-8",
                    )
                    (root / "scripts/uncovered.py").write_text(
                        "def unused():\n    return 2\n", encoding="utf-8"
                    )
                    arguments = (
                        ["tests/test_good.py", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )
                    control = self.run_runner(root, mode, arguments=arguments)
                    self.assertEqual(
                        control.returncode, 2, control.stdout + control.stderr
                    )
                    (root / ".coveragerc").write_text(
                        POLICY + "\n" + settings, encoding="utf-8"
                    )
                    result = self.run_runner(root, mode, arguments=arguments)
                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertIn("prefixed sections", result.stderr)
                    self.assertNotIn("1 passed", result.stdout)

    def test_uncovered_source_cannot_be_hidden_with_a_report_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            (root / "scripts/uncovered.py").write_text(
                "def unused():\n    return 2\n", encoding="utf-8"
            )
            result = self.run_runner(root)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            for option in ("include", "omit", "exclude_also"):
                with self.subTest(option=option):
                    (root / ".coveragerc").write_text(
                        POLICY.replace(
                            "[report]", f"[report]\n{option} = scripts/decision.py"
                        ),
                        encoding="utf-8",
                    )
                    rejected = self.run_runner(root)
                    self.assertEqual(
                        rejected.returncode, 1, rejected.stdout + rejected.stderr
                    )
                    self.assertIn("denominator", rejected.stderr)

    def test_empty_fixture_directory_changes_invalidate_guarded_results(self) -> None:
        for mode in ("suite", "full", "focused"):
            for operation in ("add", "remove", "rename", "unchanged"):
                with (
                    self.subTest(mode=mode, operation=operation),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    empty = root / "tests/fixtures/empty"
                    empty.mkdir(parents=True)
                    changes = {
                        "add": "    empty.with_name('added').mkdir()\n",
                        "remove": "    empty.rmdir()\n",
                        "rename": "    empty.rename(empty.with_name('renamed'))\n",
                        "unchanged": "",
                    }
                    (root / "tests/test_good.py").write_text(
                        "from pathlib import Path\nfrom scripts.decision import value\n"
                        "def test_good():\n    assert value() == 1\n"
                        "    empty = Path('tests/fixtures/empty')\n"
                        "    assert empty.is_dir()\n" + changes[operation],
                        encoding="utf-8",
                    )
                    arguments = (
                        ["tests/test_good.py", "--source", "scripts/decision.py"]
                        if mode == "focused"
                        else []
                    )
                    result = self.run_runner(root, mode, arguments=arguments)
                    self.assertEqual(
                        result.returncode,
                        0 if operation == "unchanged" else 1,
                        result.stdout + result.stderr,
                    )
                    if operation != "unchanged":
                        self.assertIn("inputs changed", result.stderr)

    def test_source_changes_during_passing_tests_reject_the_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            (root / "tests/test_good.py").write_text(
                "from pathlib import Path\nfrom scripts.decision import value\n"
                "def test_good():\n    assert value() == 1\n"
                "    Path('scripts/decision.py').write_text('def value():\\n    return 2\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            result = self.run_runner(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("inputs changed", result.stderr)

    def test_ignored_directory_names_cannot_hide_first_party_changes(self) -> None:
        for mode in ("suite", "full"):
            for location in (
                "scripts/vendor/input.py",
                "scripts/build/input.py",
                "tests/vendor/input.json",
            ):
                with (
                    self.subTest(mode=mode, location=location),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    self.project(root)
                    path = root / location
                    path.parent.mkdir(parents=True)
                    is_source = path.suffix == ".py"
                    path.write_text(
                        "value = 1\n" if is_source else "{}\n", encoding="utf-8"
                    )
                    imported = (
                        "from "
                        + location.removesuffix(".py").replace("/", ".")
                        + " import value as extra\n"
                        if is_source
                        else ""
                    )
                    asserted = "    assert extra == 1\n" if is_source else ""
                    changed = "value = 2\n" if is_source else '{"changed":true}\n'
                    (root / "tests/test_good.py").write_text(
                        "from pathlib import Path\nfrom scripts.decision import value\n"
                        + imported
                        + "def test_good():\n    assert value() == 1\n"
                        + asserted
                        + f"    Path({location!r}).write_text({changed!r}, encoding='utf-8')\n",
                        encoding="utf-8",
                    )
                    result = self.run_runner(root, mode)
                    self.assertEqual(
                        result.returncode, 1, result.stdout + result.stderr
                    )
                    self.assertIn("inputs changed", result.stderr)

    def test_concurrent_successful_runs_use_fresh_data_and_preserve_existing_coverage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            (root / ".coverage").write_bytes(b"unrelated-existing-data")
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: self.run_runner(root), range(2)))
            for result in results:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("100%", result.stdout)
            self.assertEqual(
                (root / ".coverage").read_bytes(), b"unrelated-existing-data"
            )

    def test_coverage_environment_cannot_override_frozen_policy_or_write_external_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            (root / "scripts/uncovered.py").write_text(
                "def hidden():\n    return 2\n", encoding="utf-8"
            )
            override = root / "override.ini"
            override.write_text(
                "[report]\ninclude = scripts/decision.py\n", encoding="utf-8"
            )
            outside = root / "unrelated.coverage"
            outside.write_bytes(b"preserve-existing")
            result = self.run_runner(
                root,
                coverage_environment={
                    "COVERAGE_FORCE_CONFIG": str(override),
                    "COVERAGE_FILE": str(outside),
                    "COVERAGE_DEBUG": "config",
                    "COVERAGE_DEBUG_FILE": str(root / "unrelated.log"),
                },
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(outside.read_bytes(), b"preserve-existing")
            self.assertFalse((root / "unrelated.log").exists())

    def test_unimported_namespace_source_is_in_the_full_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            (root / "scripts/nested").mkdir()
            (root / "scripts/nested/uncovered.py").write_text(
                "def hidden():\n    return 2\n", encoding="utf-8"
            )
            result = self.run_runner(root)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("uncovered.py", result.stdout)

    def test_partial_branch_override_and_alias_cannot_hide_uncovered_code(self) -> None:
        for kind in ("partial_also", "paths"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.project(root)
                if kind == "partial_also":
                    (root / "scripts/decision.py").write_text(
                        "def value(flag=True):\n    if flag:\n        result = 1\n    return result\n",
                        encoding="utf-8",
                    )
                    config = POLICY.replace(
                        "[report]", "[report]\npartial_also = if flag:"
                    )
                else:
                    (root / "skills/repo-scaffold/scripts/decision.py").write_text(
                        "def value():\n    return 1\n", encoding="utf-8"
                    )
                    config = (
                        POLICY
                        + "\n[paths]\nalias =\n    scripts\n    skills/repo-scaffold/scripts\n"
                    )
                control = self.run_runner(root)
                self.assertEqual(control.returncode, 2, control.stdout + control.stderr)
                (root / ".coveragerc").write_text(config, encoding="utf-8")
                rejected = self.run_runner(root)
                self.assertEqual(
                    rejected.returncode, 1, rejected.stdout + rejected.stderr
                )
                self.assertIn("denominator", rejected.stderr)
