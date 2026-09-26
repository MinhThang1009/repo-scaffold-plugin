from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/community-health.yml",
    ".github/workflows/freshness.yml",
    ".github/workflows/official-docs.yml",
    "skills/repo-scaffold/assets/workflows/community-health.yml",
    "skills/repo-scaffold/assets/workflows/freshness.yml",
)
BASH = shutil.which("bash")
if BASH is None and os.name == "nt":
    git_bash = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    )
    if git_bash.is_file():
        BASH = str(git_bash)


def child_cli_environment(overrides: dict[str, str]) -> dict[str, str]:
    """Keep mutmut's parent-test trampoline state out of copied CLI children."""
    environment = os.environ.copy()
    environment.pop("MUTANT_UNDER_TEST", None)
    environment.pop("MUTMUT_DEPENDENCY_DEPTH", None)
    environment.update(overrides)
    return environment


class ReminderWorkflowTests(unittest.TestCase):
    @staticmethod
    def install_body_preflight(root: Path, *, root_entrypoint: bool = False) -> None:
        script = root / "scripts" / "markdown_body_preflight.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        bundled_script = (
            ROOT / "skills" / "repo-scaffold" / "scripts" / "markdown_body_preflight.py"
        )
        if root_entrypoint:
            shutil.copyfile(ROOT / "scripts" / "markdown_body_preflight.py", script)
            bundled_destination = (
                root / "skills" / "repo-scaffold" / "scripts" / script.name
            )
            bundled_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundled_script, bundled_destination)
        else:
            shutil.copyfile(bundled_script, script)

    def test_child_cli_environment_does_not_inherit_mutmut_runtime_flags(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"MUTANT_UNDER_TEST": "stats", "MUTMUT_DEPENDENCY_DEPTH": "1"},
        ):
            environment = child_cli_environment({"CHILD_TEST_VALUE": "preserved"})

        self.assertNotIn("MUTANT_UNDER_TEST", environment)
        self.assertNotIn("MUTMUT_DEPENDENCY_DEPTH", environment)
        self.assertEqual(environment["CHILD_TEST_VALUE"], "preserved")

    def test_every_issue_body_writer_preflights_before_edit_or_create(self) -> None:
        report_paths = {
            ".github/workflows/ci.yml": "$report",
            ".github/workflows/community-health.yml": "$RUNNER_TEMP/community-health.md",
            ".github/workflows/freshness.yml": "$RUNNER_TEMP/freshness.md",
            ".github/workflows/official-docs.yml": "$RUNNER_TEMP/official-docs.md",
            "skills/repo-scaffold/assets/workflows/community-health.yml": "$RUNNER_TEMP/community-health.md",
            "skills/repo-scaffold/assets/workflows/freshness.yml": "$RUNNER_TEMP/freshness.md",
        }
        for relative, report_path in report_paths.items():
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            scripts = [
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            ]
            self.assertEqual(len(scripts), 1, relative)
            script = scripts[0]
            preflight = (
                f'python scripts/markdown_body_preflight.py --body-file "{report_path}"'
            )
            with self.subTest(workflow=relative):
                self.assertIn(preflight, script)
                self.assertLess(script.index(preflight), script.index("gh issue edit"))
                self.assertLess(
                    script.index(preflight), script.index("gh issue create")
                )

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_hard_wrapped_issue_reports_fail_before_mutation(self) -> None:
        cases = (
            (
                ".github/workflows/ci.yml",
                "ci-policy-drift.md",
                "repo-scaffold-ci-policy-drift",
                True,
            ),
            (
                ".github/workflows/community-health.yml",
                "community-health.md",
                "repo-scaffold-community-health-drift",
                False,
            ),
            (
                ".github/workflows/freshness.yml",
                "freshness.md",
                "repo-scaffold-freshness-audit",
                False,
            ),
            (
                ".github/workflows/official-docs.yml",
                "official-docs.md",
                "repo-scaffold-official-docs-audit",
                False,
            ),
            (
                "skills/repo-scaffold/assets/workflows/community-health.yml",
                "community-health.md",
                "repo-scaffold-community-health-drift",
                False,
            ),
            (
                "skills/repo-scaffold/assets/workflows/freshness.yml",
                "freshness.md",
                "repo-scaffold-freshness-audit",
                False,
            ),
        )
        stub = """gh() {
  if [[ "$1" == api ]]; then printf '41\\n'; return 0; fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
        for relative, report_name, marker, root_entrypoint in cases:
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            script = next(
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            )
            if root_entrypoint:
                preflight_line = (
                    'python scripts/markdown_body_preflight.py --body-file "$report"\n'
                )
                self.assertIn(preflight_line, script)
                script = script.replace(
                    preflight_line,
                    'printf "%s\\n" "" "First line" "continued line" >> "$report"\n'
                    + preflight_line,
                    1,
                )
            with self.subTest(workflow=relative):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.install_body_preflight(root, root_entrypoint=root_entrypoint)
                    (root / report_name).write_text(
                        f"<!-- {marker} -->\nFirst line\ncontinued line\n",
                        encoding="utf-8",
                    )
                    environment = child_cli_environment(
                        {
                            "REPOSITORY": "synthetic/example",
                            "GITHUB_REPOSITORY": "synthetic/example",
                            "RUNNER_TEMP": ".",
                            "CHECKER_EXIT": "1",
                            "PYTHON_CANARY_RESULT": "failure",
                            "TOOLCHAIN_CANARY_RESULT": "success",
                            "RUN_URL": "https://github.com/synthetic/example/actions/runs/1",
                            "GITHUB_STEP_SUMMARY": str(root / "summary.md"),
                        }
                    )
                    result = subprocess.run(
                        [str(BASH), "--noprofile", "--norc", "-s"],
                        input=stub + script,
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                        check=False,
                    )

                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("hard-wrapped prose", result.stderr)
                self.assertNotIn("MUTATION:", result.stdout)

    def test_reminder_workflows_serialize_repository_issue_state(self) -> None:
        for relative in WORKFLOWS:
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            if relative == ".github/workflows/ci.yml":
                concurrency = document["jobs"]["policy-drift-reminder"]["concurrency"]
                expected_group = (
                    "repo-scaffold-ci-policy-drift-${{ github.repository }}"
                )
            else:
                concurrency = document["concurrency"]
                expected_group = (
                    "repo-scaffold-community-health-${{ github.repository }}"
                    if relative.endswith("/community-health.yml")
                    else (
                        "repo-scaffold-official-docs-${{ github.repository }}"
                        if relative.endswith("/official-docs.yml")
                        else "repo-scaffold-freshness-${{ github.repository }}"
                    )
                )
            self.assertEqual(
                concurrency,
                {"group": expected_group, "cancel-in-progress": "false"},
                relative,
            )
            self.assertNotIn("github.workflow", concurrency["group"], relative)

    def test_issue_lookup_is_bounded_search(self) -> None:
        for relative in WORKFLOWS:
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            scripts = [
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            ]
            self.assertEqual(len(scripts), 1, relative)
            script = scripts[0]
            self.assertIn("search/issues?q=repo:", script, relative)
            self.assertIn("is:issue+is:open+in:body+", script, relative)
            self.assertIn("%22%3C%21--+", script, relative)
            self.assertIn("+--%3E%22&per_page=2", script, relative)
            self.assertIn("per_page=2", script, relative)
            self.assertIn("--jq '[.items[].number] | join(\" \")'", script, relative)
            self.assertNotIn("--paginate", script, relative)

    def test_issue_writing_reminders_checkout_the_default_branch(self) -> None:
        for relative in (
            ".github/workflows/community-health.yml",
            ".github/workflows/freshness.yml",
            ".github/workflows/official-docs.yml",
            "skills/repo-scaffold/assets/workflows/community-health.yml",
            "skills/repo-scaffold/assets/workflows/freshness.yml",
        ):
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            checkout_steps = [
                step
                for job in document["jobs"].values()
                for step in job["steps"]
                if step.get("uses", "").startswith("actions/checkout@")
            ]
            self.assertEqual(len(checkout_steps), 1, relative)
            self.assertEqual(
                checkout_steps[0]["with"],
                {
                    "ref": "${{ github.event.repository.default_branch }}",
                    "persist-credentials": "false",
                },
                relative,
            )

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_clean_status_requires_report_marker_before_close(self) -> None:
        for relative, report in (
            (".github/workflows/freshness.yml", "freshness.md"),
            (".github/workflows/community-health.yml", "community-health.md"),
            (".github/workflows/official-docs.yml", "official-docs.md"),
            ("skills/repo-scaffold/assets/workflows/freshness.yml", "freshness.md"),
        ):
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            script = next(
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            )
            with self.subTest(workflow=relative):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.install_body_preflight(root)
                    (root / report).write_text("wrong-marker\n", encoding="utf-8")
                    environment = child_cli_environment(
                        {
                            "REPOSITORY": "synthetic/example",
                            "GITHUB_REPOSITORY": "synthetic/example",
                            "RUNNER_TEMP": ".",
                            "CHECKER_EXIT": "0",
                        }
                    )
                    stub = """gh() {
  if [[ "$1" == api ]]; then printf '41\\n'; return 0; fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
                    result = subprocess.run(
                        [str(BASH), "--noprofile", "--norc", "-s"],
                        input=stub + script,
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                        check=False,
                    )
                    self.assertNotIn("MUTATION:close", result.stdout)
                    self.assertNotEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_issue_lookup_must_succeed_before_any_reminder_mutation(self) -> None:
        for relative in WORKFLOWS:
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            scripts = [
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            ]
            self.assertEqual(len(scripts), 1, relative)
            for api_exit, numbers, clean, mutation in (
                (23, "", False, ""),
                (23, "41", False, ""),
                (23, "", True, ""),
                (0, "", False, "create"),
                (0, "41", False, "edit"),
                (0, "", True, ""),
                (0, "41", True, "close"),
                (0, "41\n42", False, ""),
            ):
                with self.subTest(
                    workflow=relative, api_exit=api_exit, numbers=numbers, clean=clean
                ):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        self.install_body_preflight(root)
                        for name, marker in (
                            ("freshness", "repo-scaffold-freshness-audit"),
                            (
                                "community-health",
                                "repo-scaffold-community-health-drift",
                            ),
                            ("official-docs", "repo-scaffold-official-docs-audit"),
                        ):
                            (root / f"{name}.md").write_text(
                                f"<!-- {marker} -->\n", encoding="utf-8"
                            )
                        environment = child_cli_environment(
                            {
                                "REPOSITORY": "synthetic/example",
                                "GITHUB_REPOSITORY": "synthetic/example",
                                "RUNNER_TEMP": ".",
                                "GITHUB_STEP_SUMMARY": "summary.md",
                                "RUN_URL": "https://example.test/run",
                                "CHECKER_EXIT": "0" if clean else "1",
                                "PYTHON_CANARY_RESULT": "success"
                                if clean
                                else "failure",
                                "TOOLCHAIN_CANARY_RESULT": "success",
                                "TEST_API_EXIT": str(api_exit),
                                "TEST_NUMBERS": numbers,
                            }
                        )
                        stub = """gh() {
  if [[ "$1" == api ]]; then
    if [[ -n "$TEST_NUMBERS" ]]; then printf '%s\\n' "${TEST_NUMBERS//$'\\n'/ }"; fi
    return "$TEST_API_EXIT"
  fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
                        result = subprocess.run(
                            [str(BASH), "--noprofile", "--norc", "-s"],
                            input=stub + scripts[0],
                            cwd=root,
                            env=environment,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            timeout=15,
                            check=False,
                        )
                        mutations = [
                            line
                            for line in result.stdout.splitlines()
                            if line.startswith("MUTATION:")
                        ]
                        self.assertEqual(
                            mutations,
                            [f"MUTATION:{mutation}"] if mutation else [],
                            result.stderr,
                        )
                        if api_exit:
                            self.assertEqual(result.returncode, api_exit, result.stderr)
                        elif numbers == "41\n42":
                            self.assertNotEqual(result.returncode, 0)
                        elif clean:
                            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_community_health_rejects_unexpected_checker_status(self) -> None:
        relative = ".github/workflows/community-health.yml"
        document = yaml.load(
            (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )
        script = next(
            step["run"]
            for job in document["jobs"].values()
            for step in job["steps"]
            if "Reconcile" in step.get("name", "") and "issue" in step["name"]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.install_body_preflight(root)
            (root / "community-health.md").write_text(
                "<!-- repo-scaffold-community-health-drift -->\n", encoding="utf-8"
            )
            environment = child_cli_environment(
                {
                    "REPOSITORY": "synthetic/example",
                    "GITHUB_REPOSITORY": "synthetic/example",
                    "RUNNER_TEMP": ".",
                    "CHECKER_EXIT": "127",
                }
            )
            stub = """gh() {
  if [[ "$1" == api ]]; then return 0; fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
            result = subprocess.run(
                [str(BASH), "--noprofile", "--norc", "-s"],
                input=stub + script,
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("unexpected exit status", result.stderr)
        self.assertNotIn("MUTATION:", result.stdout)

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_freshness_rejects_unexpected_checker_status(self) -> None:
        for relative in (
            ".github/workflows/freshness.yml",
            "skills/repo-scaffold/assets/workflows/freshness.yml",
        ):
            document = yaml.load(
                (ROOT / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            script = next(
                step["run"]
                for job in document["jobs"].values()
                for step in job["steps"]
                if "Reconcile" in step.get("name", "") and "issue" in step["name"]
            )
            with self.subTest(workflow=relative):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.install_body_preflight(root)
                    (root / "freshness.md").write_text(
                        "<!-- repo-scaffold-freshness-audit -->\n",
                        encoding="utf-8",
                    )
                    environment = child_cli_environment(
                        {
                            "GITHUB_REPOSITORY": "synthetic/example",
                            "RUNNER_TEMP": ".",
                            "CHECKER_EXIT": "127",
                        }
                    )
                    stub = """gh() {
  if [[ "$1" == api ]]; then return 0; fi
  printf 'MUTATION:%s\\n' "$2"
}
"""
                    result = subprocess.run(
                        [str(BASH), "--noprofile", "--norc", "-s"],
                        input=stub + script,
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                        check=False,
                    )

                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn("unexpected exit status", result.stderr)
                    self.assertNotIn("MUTATION:", result.stdout)
