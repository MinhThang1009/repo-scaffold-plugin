"""Execute the actual runner on isolated projects with adversarial pytest config."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POLICY = (ROOT / ".coveragerc").read_text(encoding="utf-8")
DRIVER = (
    "from pathlib import Path; import sys; "
    "sys.path.insert(0, sys.argv[1]); "
    "from scripts import run_tests; "
    "run_tests.REPOSITORY_ROOT = Path(sys.argv[2]); "
    "raise SystemExit(run_tests.main(sys.argv[3:]))"
)


class NativeRunTestsTests(unittest.TestCase):
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
            [sys.executable, "-c", DRIVER, str(ROOT), str(root), mode],
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
