from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOTS = (
    ROOT / ".github/workflows",
    ROOT / "skills/repo-scaffold/assets/workflows",
)
NODE = shutil.which("node")
BASH = shutil.which("bash")
if BASH is None and os.name == "nt":
    git_bash = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    )
    if git_bash.is_file():
        BASH = str(git_bash)


def workflow_job(root: Path, name: str, job: str) -> dict[str, Any]:
    return yaml.load((root / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)[
        "jobs"
    ][job]


class DocumentationPolicyGateTests(unittest.TestCase):
    def job(self) -> dict[str, Any]:
        return workflow_job(
            ROOT / "skills/repo-scaffold/assets/workflows",
            "documentation.yml",
            "docs-contract",
        )

    def test_documentation_context_runs_after_all_policy_outcomes(self) -> None:
        job = self.job()
        self.assertEqual(job["if"], "${{ always() }}")
        guard = job["steps"][0]
        self.assertEqual(guard["if"], "${{ always() }}")
        self.assertNotIn("continue-on-error", guard)

    def run_guard(self, result: str, runtime: str) -> subprocess.CompletedProcess[str]:
        if BASH is None:
            self.skipTest("Bash is unavailable")
        environment = os.environ.copy()
        environment.update(
            {"PREPARE_DOCS_RESULT": result, "DOCUMENTATION_PYTHON": runtime}
        )
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            environment.pop(name, None)
        with tempfile.TemporaryDirectory() as directory:
            return subprocess.run(
                [BASH, "-c", self.job()["steps"][0]["run"]],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
                check=False,
            )

    def test_documentation_guard_accepts_valid_successful_policy(self) -> None:
        for runtime in ("3.x", "3.10", "3.14"):
            with self.subTest(runtime=runtime):
                self.assertEqual(self.run_guard("success", runtime).returncode, 0)

    def test_documentation_guard_rejects_unsuccessful_policy_preparation(self) -> None:
        for result in ("failure", "cancelled", "skipped", "indeterminate", ""):
            with self.subTest(result=result):
                checked = self.run_guard(result, "3.x")
                self.assertNotEqual(checked.returncode, 0)
                self.assertIn("documentation policy", checked.stderr)

    def test_documentation_guard_rejects_missing_or_invalid_runtime_output(
        self,
    ) -> None:
        for runtime in ("", "3.014", "latest", "$(touch unexpected)", "3.x\n3.14"):
            with self.subTest(runtime=runtime):
                self.assertNotEqual(self.run_guard("success", runtime).returncode, 0)


class PullRequestLifecycleWorkflowTests(unittest.TestCase):
    def test_closed_pr_noop_uses_a_distinct_nonrequired_context(self) -> None:
        for root in WORKFLOW_ROOTS:
            for file_name, job_id, event, context in (
                ("commitlint.yml", "commitlint", "pull_request", "commitlint"),
                (
                    "code-scanning-gate.yml",
                    "code_scanning_gate",
                    "pull_request_target",
                    "code-scanning-gate",
                ),
            ):
                with self.subTest(root=root, context=context):
                    job = workflow_job(root, file_name, job_id)
                    self.assertEqual(
                        job.get("name"),
                        "${{ github.event_name == '"
                        + event
                        + "' && github.event.pull_request.state == 'closed' && '"
                        + context
                        + "-closed-pr' || '"
                        + context
                        + "' }}",
                    )

    def test_required_jobs_remain_unconditional_and_checkout_binds_head(self) -> None:
        for root in WORKFLOW_ROOTS:
            with self.subTest(root=root):
                commitlint = workflow_job(root, "commitlint.yml", "commitlint")
                gate = workflow_job(
                    root, "code-scanning-gate.yml", "code_scanning_gate"
                )
                self.assertNotIn("if", commitlint)
                self.assertNotIn("if", gate)
                self.assertEqual(
                    gate["steps"][0]["with"]["ref"],
                    "${{ github.event_name == 'merge_group' && github.event.merge_group.base_sha || github.event.repository.default_branch }}",
                )
                self.assertEqual(
                    gate["steps"][0]["with"]["persist-credentials"], "false"
                )
                checkout = commitlint["steps"][0]
                self.assertEqual(
                    checkout["with"]["ref"],
                    "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.event.merge_group.head_sha }}",
                )
                self.assertEqual(
                    checkout["if"],
                    "github.event_name != 'pull_request' || github.event.pull_request.state != 'closed'",
                )
                for job in (commitlint, gate):
                    self.assertEqual(
                        job["steps"][1]["env"]["PR_STATE"],
                        "${{ github.event.pull_request.state }}",
                    )

    @unittest.skipUnless(NODE, "requires Node.js")
    def test_commitlint_closed_and_unknown_states_do_not_read_missing_git_refs(
        self,
    ) -> None:
        for root in WORKFLOW_ROOTS:
            job = workflow_job(root, "commitlint.yml", "commitlint")
            script = (
                job["steps"][1]["run"]
                .split("node <<'NODE'\n", 1)[1]
                .rsplit("\nNODE", 1)[0]
            )
            for state, status in (
                ("closed", 0),
                ("", 1),
                ("unknown", 1),
                ("Closed", 1),
                ("closed ", 1),
            ):
                with (
                    self.subTest(root=root, state=state),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    result = subprocess.run(
                        [str(NODE), "-e", script],
                        cwd=directory,
                        env={
                            **os.environ,
                            "EVENT_NAME": "pull_request",
                            "PR_STATE": state,
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertNotIn("Invalid revision", result.stderr)
                    if state == "closed":
                        self.assertIn("not applicable", result.stdout)
                    else:
                        self.assertIn("state must be open or closed", result.stderr)

    @unittest.skipUnless(NODE, "requires Node.js")
    def test_commitlint_open_pr_and_merge_queue_fail_with_missing_refs(self) -> None:
        for workflow_root in WORKFLOW_ROOTS:
            job = workflow_job(workflow_root, "commitlint.yml", "commitlint")
            script = (
                job["steps"][1]["run"]
                .split("node <<'NODE'\n", 1)[1]
                .rsplit("\nNODE", 1)[0]
            )
            for event, state in (
                ("pull_request", "open"),
                ("merge_group", ""),
                ("merge_group", "closed"),
            ):
                with (
                    self.subTest(root=workflow_root, event=event, state=state),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    result = subprocess.run(
                        [str(NODE), "-e", script],
                        cwd=directory,
                        env={
                            **os.environ,
                            "EVENT_NAME": event,
                            "PR_STATE": state,
                            "BASE_SHA": "a" * 40,
                            "HEAD_SHA": "b" * 40,
                            "PR_TITLE": "fix: valid title",
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=20,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("not applicable", result.stdout)
                    self.assertNotIn("Validated", result.stdout)

    @unittest.skipUnless(NODE, "requires Node.js")
    def test_commitlint_open_pr_and_merge_queue_still_validate_real_commits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*arguments: str) -> str:
                return subprocess.run(
                    ["git", *arguments],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                ).stdout.strip()

            git("init", "--quiet")
            git("config", "user.name", "Repo Scaffold Tests")
            git("config", "user.email", "tests@example.invalid")
            git("commit", "--quiet", "--allow-empty", "-m", "test: seed base")
            base = git("rev-parse", "HEAD")
            for subject, title, commit_status, title_status in (
                ("fix: valid change", "fix: valid title", 0, 0),
                ("Invalid header", "fix: valid title", 1, 0),
                ("fix: valid change", "Invalid title", 0, 1),
            ):
                git("commit", "--quiet", "--allow-empty", "-m", subject)
                head = git("rev-parse", "HEAD")
                for workflow_root in WORKFLOW_ROOTS:
                    job = workflow_job(workflow_root, "commitlint.yml", "commitlint")
                    script = (
                        job["steps"][1]["run"]
                        .split("node <<'NODE'\n", 1)[1]
                        .rsplit("\nNODE", 1)[0]
                    )
                    for event in ("pull_request", "merge_group"):
                        with self.subTest(
                            subject=subject,
                            title=title,
                            event=event,
                            workflow_root=workflow_root,
                        ):
                            result = subprocess.run(
                                [str(NODE), "-e", script],
                                cwd=root,
                                env={
                                    **os.environ,
                                    "EVENT_NAME": event,
                                    "PR_STATE": "open",
                                    "BASE_SHA": base,
                                    "HEAD_SHA": head,
                                    "PR_TITLE": title,
                                },
                                check=False,
                                capture_output=True,
                                text=True,
                                timeout=20,
                            )
                            expected = commit_status or (
                                title_status if event == "pull_request" else 0
                            )
                            self.assertEqual(result.returncode, expected, result.stderr)
                            self.assertNotIn("not applicable", result.stdout)
                            if event == "pull_request" and title_status:
                                self.assertIn("pull request title:", result.stderr)
                base = head

    @unittest.skipUnless(BASH, "requires Bash (Git Bash on Windows)")
    def test_code_scanning_closed_unknown_open_and_merge_queue_paths(self) -> None:
        for root in WORKFLOW_ROOTS:
            script = workflow_job(root, "code-scanning-gate.yml", "code_scanning_gate")[
                "steps"
            ][1]["run"]
            pr_arguments = (
                "--pull-request 136 --base-sha " + "a" * 40 + " --head-sha " + "b" * 40
            )
            queue_arguments = (
                "--ref refs/heads/gh-readonly-queue/main/test --sha " + "c" * 40
            )
            cases = (
                ("pull_request_target", "closed", 7, 0, None),
                ("pull_request_target", "", 0, 1, None),
                ("pull_request_target", "unknown", 0, 1, None),
                ("pull_request_target", "Closed", 0, 1, None),
                ("pull_request_target", "closed ", 0, 1, None),
                ("pull_request_target", "open", 0, 0, pr_arguments),
                ("pull_request_target", "open", 7, 7, pr_arguments),
                ("merge_group", "", 0, 0, queue_arguments),
                ("merge_group", "", 7, 7, queue_arguments),
                ("merge_group", "closed", 7, 7, queue_arguments),
                ("unsupported", "closed", 0, 1, None),
            )
            for event, state, gate_status, status, invocation in cases:
                with (
                    self.subTest(
                        root=root, event=event, state=state, gate_status=gate_status
                    ),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    result = subprocess.run(
                        [
                            str(BASH),
                            "-e",
                            "-c",
                            'python() { printf "GATE:%s\\n" "$*"; return "$GATE_EXIT_STATUS"; }\n'
                            + script,
                        ],
                        cwd=directory,
                        env={
                            **os.environ,
                            "EVENT_NAME": event,
                            "PR_STATE": state,
                            "GATE_EXIT_STATUS": str(gate_status),
                            "PR_NUMBER": "136",
                            "PR_BASE_SHA": "a" * 40,
                            "PR_HEAD_SHA": "b" * 40,
                            "MERGE_GROUP_REF": "refs/heads/gh-readonly-queue/main/test",
                            "MERGE_GROUP_SHA": "c" * 40,
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, status, result.stderr)
                    if invocation is None:
                        self.assertNotIn("GATE:", result.stdout)
                    else:
                        self.assertIn(invocation, result.stdout)
                        self.assertIn(
                            "--expected-codeql-category /language:actions",
                            result.stdout,
                        )
                        self.assertIn(
                            "--attempts 120 --delay-seconds 10", result.stdout
                        )
                    self.assertEqual(
                        "not applicable" in result.stdout,
                        state == "closed" and event == "pull_request_target",
                    )
