from __future__ import annotations

import argparse
import importlib.util
import runpy
import sys
import tempfile
import unittest
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

SYNC_SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.sync_action_pins",
    SCRIPT_DIRECTORY / "sync_action_pins.py",
)
if SYNC_SPEC is None or SYNC_SPEC.loader is None:
    raise RuntimeError("Could not load sync_action_pins.py")
sync_action_pins = importlib.util.module_from_spec(SYNC_SPEC)
sys.modules[SYNC_SPEC.name] = sync_action_pins
sys.modules["sync_action_pins"] = sync_action_pins
SYNC_SPEC.loader.exec_module(sync_action_pins)

SCRIPT_PATH = SCRIPT_DIRECTORY / "workflow_installation_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.workflow_installation_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load workflow_installation_preflight.py")
workflow_installation_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workflow_installation_preflight
SPEC.loader.exec_module(workflow_installation_preflight)


class FakeClient:
    responses: dict[str, object] = {}

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        self.request_count = 0

    def json(self, endpoint: str) -> object:
        self.request_count += 1
        return self.responses[endpoint]


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "hostname": "github.com",
        "repository": "octo/example",
        "require_external_actions": False,
        "require_issues": False,
        "workflow": [],
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class WorkflowInstallationPreflightTests(unittest.TestCase):
    def configure(
        self,
        *,
        actions_enabled: bool = True,
        allowed_actions: str = "all",
        issues_enabled: bool = True,
    ) -> None:
        FakeClient.responses = {
            "repos/octo/example": {
                "full_name": "octo/example",
                "archived": False,
                "disabled": False,
                "has_issues": issues_enabled,
                "visibility": "public",
            },
            "repos/octo/example/actions/permissions": {
                "enabled": actions_enabled,
                "allowed_actions": allowed_actions,
            },
        }

    def test_allows_confirmed_actions_and_issue_workflow_capabilities(self) -> None:
        self.configure()

        with mock.patch.object(
            workflow_installation_preflight, "GitHubClient", FakeClient
        ):
            result = workflow_installation_preflight.run(
                arguments(require_external_actions=True, require_issues=True)
            )

        self.assertEqual(result["decision"], "may-install-workflow-assets")
        self.assertTrue(result["external_actions_verified"])
        self.assertTrue(result["issue_workflows_eligible"])
        self.assertEqual(result["github_api_requests"], 2)

    def test_blocks_disabled_actions_and_external_action_restrictions(self) -> None:
        cases = [
            (False, "all", "enable-github-actions-before-installing-workflows"),
            (True, "local_only", "allow-external-actions-before-installing-workflows"),
        ]
        for enabled, policy, expected in cases:
            with self.subTest(enabled=enabled, policy=policy):
                self.configure(actions_enabled=enabled, allowed_actions=policy)
                with mock.patch.object(
                    workflow_installation_preflight, "GitHubClient", FakeClient
                ):
                    result = workflow_installation_preflight.run(
                        arguments(require_external_actions=True)
                    )
                self.assertEqual(result["decision"], expected)
                self.assertFalse(result["external_actions_verified"])

        self.configure(actions_enabled=True, allowed_actions="selected")
        FakeClient.responses[
            "repos/octo/example/actions/permissions/selected-actions"
        ] = {
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": [],
        }
        with mock.patch.object(
            workflow_installation_preflight, "GitHubClient", FakeClient
        ):
            with self.assertRaisesRegex(
                workflow_installation_preflight.InspectionError, "--workflow"
            ):
                workflow_installation_preflight.run(
                    arguments(require_external_actions=True)
                )

    def test_compares_each_selected_action_with_effective_allowlist(self) -> None:
        self.configure(allowed_actions="selected")
        FakeClient.responses[
            "repos/octo/example/actions/permissions/selected-actions"
        ] = {
            "github_owned_allowed": True,
            "verified_allowed": True,
            "patterns_allowed": ["octo/allowed@*"],
        }
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ci.yml"
            workflow.write_text(
                "steps:\n"
                "  - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                "  - uses: octo/allowed@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
                "  - uses: octo/unapproved@cccccccccccccccccccccccccccccccccccccccc\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                result = workflow_installation_preflight.run(
                    arguments(require_external_actions=True, workflow=[workflow])
                )

        self.assertEqual(
            result["decision"], "allow-selected-actions-before-installing-workflows"
        )
        self.assertFalse(result["external_actions_verified"])
        self.assertEqual(
            result["unapproved_action_references"],
            ["octo/unapproved@cccccccccccccccccccccccccccccccccccccccc"],
        )
        self.assertEqual(result["github_api_requests"], 3)

    def test_approves_selected_policy_only_after_exact_workflow_comparison(
        self,
    ) -> None:
        self.configure(allowed_actions="selected")
        FakeClient.responses[
            "repos/octo/example/actions/permissions/selected-actions"
        ] = {
            "github_owned_allowed": True,
            "verified_allowed": False,
            "patterns_allowed": ["octo/allowed@*"],
        }
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ci.yaml"
            workflow.write_text(
                "steps:\n"
                "  - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                "  - uses: octo/allowed@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                result = workflow_installation_preflight.run(
                    arguments(require_external_actions=True, workflow=[workflow])
                )
        self.assertEqual(result["decision"], "may-install-workflow-assets")
        self.assertTrue(result["external_actions_verified"])
        self.assertEqual(result["unapproved_action_references"], [])

    def test_selected_policy_fails_closed_for_missing_or_unsafe_workflow_inputs(
        self,
    ) -> None:
        self.configure(allowed_actions="selected")
        FakeClient.responses[
            "repos/octo/example/actions/permissions/selected-actions"
        ] = {
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": [],
        }
        with mock.patch.object(
            workflow_installation_preflight, "GitHubClient", FakeClient
        ):
            with self.assertRaisesRegex(
                workflow_installation_preflight.InspectionError, "--workflow"
            ):
                workflow_installation_preflight.run(
                    arguments(require_external_actions=True)
                )
        with tempfile.TemporaryDirectory() as directory:
            unsafe = Path(directory) / "workflow.txt"
            unsafe.write_text("steps: []\n", encoding="utf-8")
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                with self.assertRaisesRegex(
                    workflow_installation_preflight.InspectionError, "unsafe"
                ):
                    workflow_installation_preflight.run(
                        arguments(require_external_actions=True, workflow=[unsafe])
                    )
            unpinned = Path(directory) / "unpinned.yml"
            unpinned.write_text(
                "steps:\n  - uses: octo/unpinned@v1\n", encoding="utf-8"
            )
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                with self.assertRaisesRegex(
                    workflow_installation_preflight.InspectionError, "full SHA"
                ):
                    workflow_installation_preflight.run(
                        arguments(require_external_actions=True, workflow=[unpinned])
                    )

    def test_selected_policy_does_not_assume_private_pattern_eligibility(self) -> None:
        self.configure(allowed_actions="selected")
        repository_response = FakeClient.responses["repos/octo/example"]
        assert isinstance(repository_response, dict)
        repository_response["visibility"] = "private"
        FakeClient.responses[
            "repos/octo/example/actions/permissions/selected-actions"
        ] = {
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": ["octo/allowed@*"],
        }
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ci.yml"
            workflow.write_text(
                "steps:\n"
                "  - uses: octo/allowed@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                result = workflow_installation_preflight.run(
                    arguments(require_external_actions=True, workflow=[workflow])
                )
        self.assertFalse(result["external_actions_verified"])
        self.assertEqual(
            result["unapproved_action_references"],
            ["octo/allowed@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
        )

    def test_selected_policy_rejects_invalid_api_response_and_visibility(self) -> None:
        for response, message in (
            ([], "response is invalid"),
            (
                {
                    "github_owned_allowed": "yes",
                    "verified_allowed": False,
                    "patterns_allowed": [],
                },
                "invalid boolean",
            ),
            (
                {
                    "github_owned_allowed": False,
                    "verified_allowed": False,
                    "patterns_allowed": ["bad\npattern"],
                },
                "invalid allowed patterns",
            ),
        ):
            with self.subTest(response=response):
                with self.assertRaisesRegex(
                    workflow_installation_preflight.InspectionError, message
                ):
                    workflow_installation_preflight.selected_actions_policy(response)

        self.configure(allowed_actions="selected")
        repository_response = FakeClient.responses["repos/octo/example"]
        assert isinstance(repository_response, dict)
        repository_response["visibility"] = "unknown"
        FakeClient.responses[
            "repos/octo/example/actions/permissions/selected-actions"
        ] = {
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": [],
        }
        with mock.patch.object(
            workflow_installation_preflight, "GitHubClient", FakeClient
        ):
            with self.assertRaisesRegex(
                workflow_installation_preflight.InspectionError, "invalid visibility"
            ):
                workflow_installation_preflight.run(
                    arguments(require_external_actions=True)
                )

    def test_blocks_issue_dependent_assets_when_issues_are_disabled(self) -> None:
        self.configure(issues_enabled=False)

        with mock.patch.object(
            workflow_installation_preflight, "GitHubClient", FakeClient
        ):
            result = workflow_installation_preflight.run(arguments(require_issues=True))

        self.assertEqual(
            result["decision"], "enable-issues-before-installing-issue-workflows"
        )
        self.assertFalse(result["issue_workflows_eligible"])

    def test_infers_issue_requirement_from_shipped_workflow_name(self) -> None:
        self.configure(issues_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "freshness.yml"
            workflow.write_text("jobs: {}\n", encoding="utf-8")
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                result = workflow_installation_preflight.run(
                    arguments(workflow=[workflow])
                )

        self.assertEqual(
            result["decision"], "enable-issues-before-installing-issue-workflows"
        )
        self.assertTrue(result["requires_issues"])
        self.assertEqual(result["detected_issue_workflows"], ["freshness.yml"])

    def test_infers_external_actions_from_workflow_input(self) -> None:
        self.configure(allowed_actions="local_only")
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ci.yml"
            workflow.write_text(
                "steps:\n"
                "  - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                result = workflow_installation_preflight.run(
                    arguments(workflow=[workflow])
                )

        self.assertEqual(
            result["decision"], "allow-external-actions-before-installing-workflows"
        )
        self.assertTrue(result["requires_external_actions"])
        self.assertFalse(result["external_actions_verified"])

    def test_rejects_invalid_responses_and_arguments(self) -> None:
        for overrides, message in [
            ({"hostname": "github.example"}, "GitHub.com only"),
            ({"repository": "not/a/repository"}, "OWNER/REPO"),
        ]:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(
                    workflow_installation_preflight.InspectionError, message
                ):
                    workflow_installation_preflight.run(arguments(**overrides))

        self.configure()
        cases = [
            ([], "response is invalid"),
            (
                {
                    "full_name": "octo/other",
                    "archived": False,
                    "disabled": False,
                    "has_issues": True,
                },
                "different repository",
            ),
            (
                {
                    "full_name": "octo/example",
                    "archived": True,
                    "disabled": False,
                    "has_issues": True,
                },
                "Archived",
            ),
            (
                {
                    "full_name": "octo/example",
                    "archived": False,
                    "disabled": True,
                    "has_issues": True,
                },
                "Disabled",
            ),
            (
                {
                    "full_name": "octo/example",
                    "archived": False,
                    "disabled": False,
                    "has_issues": "yes",
                },
                "has_issues",
            ),
        ]
        for repository, message in cases:
            with self.subTest(repository=repository):
                FakeClient.responses["repos/octo/example"] = repository
                with mock.patch.object(
                    workflow_installation_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        workflow_installation_preflight.InspectionError, message
                    ):
                        workflow_installation_preflight.run(arguments())

    def test_parser_and_script_entrypoint_fail_closed(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                str(SCRIPT_PATH),
                "--repository",
                "octo/example",
                "--require-external-actions",
                "--require-issues",
                "--workflow",
                "assets/workflows/ci.yml",
            ],
        ):
            parsed = workflow_installation_preflight.parse_args()

        self.assertEqual(parsed.repository, "octo/example")
        self.assertTrue(parsed.require_external_actions)
        self.assertTrue(parsed.require_issues)
        self.assertEqual(parsed.workflow, [Path("assets/workflows/ci.yml")])
        with mock.patch.object(sys, "argv", [str(SCRIPT_PATH)]):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
        self.assertEqual(raised.exception.code, 2)

        self.configure()
        for permissions, message in [
            ([], "permissions response is invalid"),
            ({"enabled": "yes", "allowed_actions": "all"}, "'enabled'"),
            ({"enabled": True, "allowed_actions": "unknown"}, "'allowed_actions'"),
        ]:
            with self.subTest(permissions=permissions):
                FakeClient.responses["repos/octo/example/actions/permissions"] = (
                    permissions
                )
                with mock.patch.object(
                    workflow_installation_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        workflow_installation_preflight.InspectionError, message
                    ):
                        workflow_installation_preflight.run(arguments())

    def test_cli_reports_success_and_inconclusive_result(self) -> None:
        self.configure()
        with (
            mock.patch.object(
                workflow_installation_preflight,
                "parse_args",
                return_value=arguments(),
            ),
            mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(workflow_installation_preflight.main(), 0)
        self.assertIn("may-install", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                workflow_installation_preflight,
                "parse_args",
                side_effect=workflow_installation_preflight.InspectionError(
                    "bad input"
                ),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(workflow_installation_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])
