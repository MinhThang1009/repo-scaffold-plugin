from __future__ import annotations

import argparse
import importlib.util
import runpy
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts"
CODEQL_SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.codeql_preflight",
    SCRIPT_DIRECTORY / "codeql_preflight.py",
)
if CODEQL_SPEC is None or CODEQL_SPEC.loader is None:
    raise RuntimeError("Could not load codeql_preflight.py")
codeql_preflight = importlib.util.module_from_spec(CODEQL_SPEC)
sys.modules[CODEQL_SPEC.name] = codeql_preflight
sys.modules["codeql_preflight"] = codeql_preflight
CODEQL_SPEC.loader.exec_module(codeql_preflight)

SCRIPT_PATH = SCRIPT_DIRECTORY / "branch_protection_preflight.py"
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

    def raw(self, endpoint: str) -> str:
        self.request_count += 1
        value = self.responses[endpoint]
        if not isinstance(value, str):
            raise TypeError("Expected raw workflow text")
        return value


def check_runs(context: str, app_id: int = 15368) -> dict[str, object]:
    return {
        "total_count": 1,
        "check_runs": [
            {
                "name": context,
                "app": {"id": app_id},
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "conclusion": "success",
            }
        ],
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

    def test_validate_contexts_rejects_empty_and_invalid_values(self) -> None:
        for values in ([], ["valid\ninvalid"]):
            with self.subTest(values=values):
                with self.assertRaises(branch_protection_preflight.InspectionError):
                    branch_protection_preflight.validate_contexts(values)
        with self.assertRaises(branch_protection_preflight.InspectionError):
            branch_protection_preflight.validate_contexts(["check"] * 51)

    def test_parsing_and_event_coverage_fail_closed(self) -> None:
        with mock.patch.object(branch_protection_preflight, "MAX_WORKFLOW_BYTES", 1):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "byte safety cap"
            ):
                branch_protection_preflight.parse_workflow("ab", "large.yml")
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "Could not parse"
        ):
            branch_protection_preflight.parse_workflow("jobs: [", "bad.yml")
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "not a YAML mapping"
        ):
            branch_protection_preflight.parse_workflow("- job", "list.yml")

        cases = [
            ({"on": ["pull_request"]}, "pull_request", True),
            ({"on": ["push"]}, "pull_request", False),
            ({}, "pull_request", False),
            ({"on": {"pull_request": None}}, "pull_request", True),
            ({"on": {"pull_request": ""}}, "pull_request", True),
            ({"on": {"pull_request": True}}, "pull_request", False),
            ({"on": {"pull_request": {}}}, "pull_request", True),
            (
                {"on": {"pull_request": {"paths": ["docs/**"]}}},
                "pull_request",
                False,
            ),
            (
                {"on": {"pull_request": {"types": ["opened"]}}},
                "pull_request",
                False,
            ),
            (
                {"on": {"merge_group": {"types": ["queued"]}}},
                "merge_group",
                False,
            ),
        ]
        for document, event, expected in cases:
            with self.subTest(document=document, event=event):
                self.assertIs(
                    branch_protection_preflight.event_covers(document, event), expected
                )

    def test_workflow_producers_reject_invalid_inputs_and_ignores_dynamic_jobs(
        self,
    ) -> None:
        client = FakeClient("github.com")
        endpoint = f"repos/{OWNER}/{REPOSITORY}/git/trees/{HEAD_SHA}?recursive=1"
        for tree, message in [
            ({"truncated": True}, "missing"),
            ({"truncated": False, "tree": {}}, "no tree array"),
        ]:
            FakeClient.responses = {endpoint: tree}
            with self.subTest(tree=tree):
                with self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, message
                ):
                    branch_protection_preflight.workflow_producers(
                        client, OWNER, REPOSITORY, HEAD_SHA
                    )

        entry = {
            "type": "blob",
            "path": ".github/workflows/ci.yml",
            "sha": BLOB_SHA,
        }
        FakeClient.responses = {endpoint: {"truncated": False, "tree": [entry]}}
        with mock.patch.object(branch_protection_preflight, "MAX_WORKFLOWS", 0):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "count exceeds"
            ):
                branch_protection_preflight.workflow_producers(
                    client, OWNER, REPOSITORY, HEAD_SHA
                )

        invalid_entry = dict(entry, sha="short")
        FakeClient.responses = {endpoint: {"truncated": False, "tree": [invalid_entry]}}
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "invalid blob"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )

        blob_endpoint = f"repos/{OWNER}/{REPOSITORY}/git/blobs/{BLOB_SHA}"
        FakeClient.responses = {
            endpoint: {"truncated": False, "tree": [entry]},
            blob_endpoint: "jobs: {}\n",
        }
        with mock.patch.object(
            branch_protection_preflight, "MAX_TOTAL_WORKFLOW_BYTES", 1
        ):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "total byte"
            ):
                branch_protection_preflight.workflow_producers(
                    client, OWNER, REPOSITORY, HEAD_SHA
                )

        FakeClient.responses[blob_endpoint] = "on: pull_request\n"
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "no jobs"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )
        FakeClient.responses[blob_endpoint] = "jobs:\n  invalid: []\n"
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "invalid job"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )
        FakeClient.responses[blob_endpoint] = """on: pull_request
jobs:
  dynamic:
    name: ${{ github.job }}
  reusable:
    uses: org/example/.github/workflows/reuse.yml@main
  non-executable:
    name: non-executable
    runs-on: ubuntu-latest
    steps: []
"""
        producers = branch_protection_preflight.workflow_producers(
            client, OWNER, REPOSITORY, HEAD_SHA
        )
        self.assertEqual(
            [producer.context for producer in producers], ["reusable", "non-executable"]
        )
        self.assertFalse(producers[0].executable)
        self.assertFalse(producers[1].executable)


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
            f"repos/{OWNER}/{REPOSITORY}/git/blobs/{BLOB_SHA}": workflow,
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

    def test_evidence_validation_rejects_ambiguous_or_stale_data(self) -> None:
        now = datetime.now(timezone.utc)
        valid = check_runs("ci-success")
        invalid_payloads = [
            {},
            {"total_count": True, "check_runs": []},
            {"total_count": -1, "check_runs": []},
            {"total_count": 101, "check_runs": []},
            {"total_count": 0, "check_runs": []},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(branch_protection_preflight.InspectionError):
                    branch_protection_preflight.app_id_for_check(
                        payload, "ci-success", now
                    )

        base = valid["check_runs"][0]
        for update, message in [
            ({"app": {}}, "incomplete"),
            ({"completed_at": "not-a-time"}, "invalid completion"),
            ({"completed_at": "2026-01-01T00:00:00"}, "timezone-less"),
        ]:
            item = dict(base, **update)
            with self.subTest(update=update):
                with self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, message
                ):
                    branch_protection_preflight.app_id_for_check(
                        {"total_count": 1, "check_runs": [item]}, "ci-success", now
                    )
        conflict = dict(base, app={"id": 2})
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "conflicting"
        ):
            branch_protection_preflight.app_id_for_check(
                {"total_count": 2, "check_runs": [base, conflict]}, "ci-success", now
            )
        stale = dict(base, completed_at="2000-01-01T00:00:00+00:00")
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "successful recent"
        ):
            branch_protection_preflight.app_id_for_check(
                {"total_count": 1, "check_runs": [stale]}, "ci-success", now
            )

    def test_inspect_evidence_rejects_invalid_statuses(self) -> None:
        client = FakeClient("github.com")
        check_endpoint = (
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/check-runs?per_page=100"
        )
        status_endpoint = (
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/status?per_page=100"
        )
        FakeClient.responses = {check_endpoint: check_runs("ci-success")}
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "full Git object"
        ):
            branch_protection_preflight.inspect_evidence(
                client,
                OWNER,
                REPOSITORY,
                "short",
                "ci-success",
                datetime.now(timezone.utc),
            )
        for status in [
            {},
            {"total_count": True, "statuses": []},
            {"total_count": -1, "statuses": []},
            {"total_count": 101, "statuses": []},
            {"total_count": 1, "statuses": [{"context": "CI-SUCCESS"}]},
        ]:
            FakeClient.responses[status_endpoint] = status
            with self.subTest(status=status):
                with self.assertRaises(branch_protection_preflight.InspectionError):
                    branch_protection_preflight.inspect_evidence(
                        client,
                        OWNER,
                        REPOSITORY,
                        HEAD_SHA,
                        "ci-success",
                        datetime.now(timezone.utc),
                    )

    def test_run_rejects_invalid_representative_pull_request_data(self) -> None:
        for overrides, message in [
            ({"hostname": "github.example"}, "GitHub.com only"),
            ({"pull_request": 0}, "positive"),
        ]:
            with self.subTest(overrides=overrides):
                args = preflight_args("ci-success")
                for key, value in overrides.items():
                    setattr(args, key, value)
                with self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, message
                ):
                    branch_protection_preflight.run(args)

        self.configure()
        pull_endpoint = f"repos/{OWNER}/{REPOSITORY}/pulls/7"
        for payload, message in [
            ([], "response is invalid"),
            ({"head": {}, "merge_commit_sha": MERGE_SHA}, "no head"),
            (
                {
                    "head": {"sha": HEAD_SHA},
                    "merge_commit_sha": MERGE_SHA,
                    "mergeable": False,
                },
                "not confirmed mergeable",
            ),
        ]:
            FakeClient.responses[pull_endpoint] = payload
            with self.subTest(payload=payload):
                with mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        branch_protection_preflight.InspectionError, message
                    ):
                        branch_protection_preflight.run(preflight_args("ci-success"))

        self.configure()
        FakeClient.responses[f"repos/{OWNER}/{REPOSITORY}/rules/branches/main"] = {}
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "rules response"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_non_gate_producer_and_app_mismatch(self) -> None:
        self.configure(self.WORKFLOW.replace("if: ${{ always() }}", "if: false"))
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "unconditional executable"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_SHA}/check-runs?per_page=100"
        ] = check_runs("ci-success", app_id=1)
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "changes GitHub App"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_cli_reports_success_and_inconclusive_result(self) -> None:
        self.configure()
        with (
            mock.patch.object(
                branch_protection_preflight,
                "parse_args",
                return_value=preflight_args("ci-success"),
            ),
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(branch_protection_preflight.main(), 0)
        self.assertIn("may-configure", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                branch_protection_preflight,
                "parse_args",
                side_effect=branch_protection_preflight.InspectionError("bad input"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(branch_protection_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

    def test_module_entrypoint_exits_for_invalid_cli_arguments(self) -> None:
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
