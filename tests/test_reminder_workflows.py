from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

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


class ReminderWorkflowTests(unittest.TestCase):
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
                        environment = {
                            **os.environ,
                            "REPOSITORY": "synthetic/example",
                            "RUNNER_TEMP": ".",
                            "GITHUB_STEP_SUMMARY": "summary.md",
                            "RUN_URL": "https://example.test/run",
                            "CHECKER_EXIT": "0" if clean else "1",
                            "PYTHON_CANARY_RESULT": "success" if clean else "failure",
                            "TOOLCHAIN_CANARY_RESULT": "success",
                            "TEST_API_EXIT": str(api_exit),
                            "TEST_NUMBERS": numbers,
                        }
                        stub = """gh() {
  if [[ "$1" == api ]]; then
    if [[ -n "$TEST_NUMBERS" ]]; then printf '%s\\n' "$TEST_NUMBERS"; fi
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
