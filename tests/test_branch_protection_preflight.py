from __future__ import annotations

import argparse
import base64
import importlib.util
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "repo-scaffold"
    / "scripts"
    / "branch_protection_preflight.py"
)
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.branch_protection_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load branch_protection_preflight.py")
branch_protection_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = branch_protection_preflight
SPEC.loader.exec_module(branch_protection_preflight)


OWNER = "octo"
REPOSITORY = "example"
HEAD_SHA = "a" * 40
MERGE_SHA = "b" * 40
BLOB_SHA = "c" * 40


class FakeClient:
    responses: dict[str, object] = {}

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        self.request_count = 0

    def json(self, endpoint: str) -> object:
        self.request_count += 1
        return self.responses[endpoint]


def check_runs(context: str, app_id: int = 15368) -> dict[str, object]:
    return {
        "total_count": 1,
        "check_runs": [
            {
                "name": context,
                "app": {"id": app_id},
                "completed_at": datetime.now(UTC).isoformat(),
                "conclusion": "success",
            }
        ],
    }


def workflow_blob(workflow: str) -> dict[str, str]:
    return {
        "encoding": "base64",
        "content": base64.b64encode(workflow.encode("utf-8")).decode("ascii"),
    }


def preflight_args(*contexts: str) -> argparse.Namespace:
    return argparse.Namespace(
        hostname="github.com",
        repository=f"{OWNER}/{REPOSITORY}",
        default_branch="main",
        pull_request=7,
        required_check=list(contexts),
    )


class WorkflowInspectionTests(unittest.TestCase):
    def test_event_coverage_requires_relevant_trigger_types_without_filters(
        self,
    ) -> None:
        covered = branch_protection_preflight.parse_workflow(
            """on:
  pull_request:
    types: [opened, edited, reopened, synchronize]
  merge_group:
    types: [checks_requested]
jobs: {}
""",
            "covered.yml",
        )
        filtered = branch_protection_preflight.parse_workflow(
            """on:
  pull_request:
    paths: [docs/**]
jobs: {}
""",
            "filtered.yml",
        )

        self.assertTrue(
            branch_protection_preflight.event_covers(covered, "pull_request")
        )
        self.assertTrue(
            branch_protection_preflight.event_covers(covered, "merge_group")
        )
        self.assertFalse(
            branch_protection_preflight.event_covers(filtered, "pull_request")
        )

    def test_parse_workflow_rejects_duplicate_keys(self) -> None:
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "duplicate"
        ):
            branch_protection_preflight.parse_workflow(
                "jobs: {}\njobs: {}\n", "bad.yml"
            )

    def test_validate_contexts_rejects_case_insensitive_duplicates(self) -> None:
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "unique"
        ):
            branch_protection_preflight.validate_contexts(["CI", "ci"])


class BranchProtectionPreflightTests(unittest.TestCase):
    WORKFLOW = """name: CI
on:
  pull_request:
    types: [opened, edited, reopened, synchronize]
  merge_group:
    types: [checks_requested]
jobs:
  ci-success:
    name: ci-success
    if: ${{ always() }}
    runs-on: ubuntu-latest
    steps:
      - run: echo checked
"""

    def configure(self, workflow: str | None = None) -> None:
        workflow = self.WORKFLOW if workflow is None else workflow
        tree_path = f"repos/{OWNER}/{REPOSITORY}/git/trees/{HEAD_SHA}?recursive=1"
        FakeClient.responses = {
            f"repos/{OWNER}/{REPOSITORY}/pulls/7": {
                "head": {"sha": HEAD_SHA},
                "merge_commit_sha": MERGE_SHA,
                "mergeable": True,
            },
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main": [],
            tree_path: {
                "truncated": False,
                "tree": [
                    {
                        "type": "blob",
                        "path": ".github/workflows/ci.yml",
                        "sha": BLOB_SHA,
                    }
                ],
            },
            f"repos/{OWNER}/{REPOSITORY}/git/blobs/{BLOB_SHA}": workflow_blob(workflow),
        }
        for sha in (HEAD_SHA, MERGE_SHA):
            FakeClient.responses[
                f"repos/{OWNER}/{REPOSITORY}/commits/{sha}/check-runs?per_page=100"
            ] = check_runs("ci-success")
            FakeClient.responses[
                f"repos/{OWNER}/{REPOSITORY}/commits/{sha}/status?per_page=100"
            ] = {"total_count": 0, "statuses": []}

    def test_run_produces_app_bound_protection_input(self) -> None:
        self.configure()

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(preflight_args("ci-success"))

        self.assertEqual(result["decision"], "may-configure-classic-protection")
        self.assertEqual(
            result["required_checks"],
            [
                {
                    "context": "ci-success",
                    "app_id": 15368,
                    "producer": ".github/workflows/ci.yml#ci-success",
                }
            ],
        )

    def test_run_rejects_multiple_workflow_producers(self) -> None:
        self.configure(
            self.WORKFLOW
            + """\n  another-gate:
    name: ci-success
    runs-on: ubuntu-latest
    steps:
      - run: echo duplicate
"""
        )

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "2 workflow producers"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_commit_status_collision(self) -> None:
        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/status?per_page=100"
        ] = {"total_count": 1, "statuses": [{"context": "ci-success"}]}

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "Commit Status"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_requires_merge_group_coverage_when_queue_applies(self) -> None:
        self.configure(
            self.WORKFLOW.replace("  merge_group:\n    types: [checks_requested]\n", "")
        )
        FakeClient.responses[f"repos/{OWNER}/{REPOSITORY}/rules/branches/main"] = [
            {"type": "merge_queue"}
        ]

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "merge_group"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))
