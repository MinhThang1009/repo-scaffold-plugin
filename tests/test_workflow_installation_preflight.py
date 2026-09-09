from __future__ import annotations

import argparse
import importlib.util
import json
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
        "confirm_pull_request_write_tokens": False,
        "workflow": [],
        "code_scanning_allowlist": None,
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

    def test_selected_actions_approval_does_not_bypass_issues_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "reminder.yml"
            for inferred in (False, True):
                workflow.write_text(
                    ("permissions: {issues: write}\n" if inferred else "") + "steps:\n"
                    "  - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
                    encoding="utf-8",
                )
                for approved in (False, True):
                    for issues_enabled in (False, True):
                        self.configure(
                            allowed_actions="selected", issues_enabled=issues_enabled
                        )
                        FakeClient.responses[
                            "repos/octo/example/actions/permissions/selected-actions"
                        ] = {
                            "github_owned_allowed": approved,
                            "verified_allowed": False,
                            "patterns_allowed": [],
                        }
                        with (
                            self.subTest(
                                inferred=inferred,
                                approved=approved,
                                issues_enabled=issues_enabled,
                            ),
                            mock.patch.object(
                                workflow_installation_preflight,
                                "GitHubClient",
                                FakeClient,
                            ),
                        ):
                            result = workflow_installation_preflight.run(
                                arguments(
                                    workflow=[workflow], require_issues=not inferred
                                )
                            )
                            expected = (
                                "allow-selected-actions-before-installing-workflows"
                            )
                            if approved:
                                expected = (
                                    "may-install-workflow-assets"
                                    if issues_enabled
                                    else "enable-issues-before-installing-issue-workflows"
                                )
                            self.assertEqual(result["decision"], expected)

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

    def test_infers_issue_requirement_from_declared_permission(self) -> None:
        self.configure(issues_enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            for filename, permission in (
                ("ci.yml", "permissions:\n  issues: write"),
                ("custom.yml", "permissions: {issues: write}"),
                ("write-all.yml", "permissions: write-all"),
                ("quoted-issues.yml", 'permissions: {"issues": "write"}'),
                ("single-quoted-issues.yml", "permissions: {'issues': 'write'}"),
                ("quoted-permissions.yml", '"permissions": "write-all"'),
                ("single-quoted-permissions.yml", "'permissions': 'write-all'"),
                ("anchored.yml", "permissions: {issues: &access write}"),
                ("escaped.yml", 'permissions: {"iss\\u0075es": "wr\\u0069te"}'),
            ):
                with self.subTest(filename=filename, permission=permission):
                    workflow = Path(directory) / filename
                    workflow.write_text(f"{permission}\njobs: {{}}\n", encoding="utf-8")
                    with mock.patch.object(
                        workflow_installation_preflight, "GitHubClient", FakeClient
                    ):
                        result = workflow_installation_preflight.run(
                            arguments(workflow=[workflow])
                        )

                    self.assertEqual(
                        result["decision"],
                        "enable-issues-before-installing-issue-workflows",
                    )
                    self.assertTrue(result["requires_issues"])
                    self.assertEqual(result["detected_issue_workflows"], [filename])

    def test_permission_alias_at_job_scope_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ci.yml"
            workflow.write_text(
                "jobs:\n  first:\n    permissions: &access {issues: &write write}\n"
                "  reminder:\n    permissions: *access\n",
                encoding="utf-8",
            )
            self.assertEqual(
                workflow_installation_preflight.workflow_capabilities([workflow]),
                ([], ["ci.yml"], [], [], False),
            )

    def test_permission_text_outside_permission_fields_is_not_a_requirement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ci.yml"
            workflow.write_text(
                "# permissions: write-all\n"
                "jobs:\n  example:\n    permissions: {contents: read}\n"
                "    steps:\n      - run: |\n          echo 'issues: write'\n",
                encoding="utf-8",
            )
            self.assertEqual(
                workflow_installation_preflight.workflow_capabilities([workflow]),
                ([], [], [], [], False),
            )

    def test_rejects_unparseable_or_ambiguous_permission_documents(self) -> None:
        for document in (
            "permissions: [",
            "permissions: {}\npermissions: write-all\n",
            "[]",
            "jobs: []",
            "jobs: {invalid: []}",
        ):
            with self.subTest(document=document):
                with self.assertRaises(workflow_installation_preflight.InspectionError):
                    workflow_installation_preflight.requires_issue_write(
                        document, Path("ci.yml")
                    )

    def test_workflow_read_is_bounded_even_if_metadata_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ci.yml"
            workflow.write_text("permissions: {}\n", encoding="utf-8")
            metadata = workflow.stat()
            with mock.patch.object(
                workflow_installation_preflight, "MAX_WORKFLOW_BYTES", 8
            ):
                with self.assertRaisesRegex(
                    workflow_installation_preflight.InspectionError, "unsafe"
                ):
                    workflow_installation_preflight.workflow_capabilities([workflow])
                with mock.patch.object(
                    Path,
                    "lstat",
                    return_value=mock.Mock(
                        st_mode=metadata.st_mode, st_size=0, st_file_attributes=0
                    ),
                ):
                    with self.assertRaisesRegex(
                        workflow_installation_preflight.InspectionError, "unsafe"
                    ):
                        workflow_installation_preflight.workflow_capabilities(
                            [workflow]
                        )

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

    def test_code_scanning_gate_requires_freshness_and_allowlist_companions(
        self,
    ) -> None:
        self.configure()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gate = root / "code-scanning-gate.yml"
            gate.write_text(
                "jobs:\n"
                "  gate:\n"
                "    steps:\n"
                "      - run: python scripts/check_code_scanning_alerts.py\n",
                encoding="utf-8",
            )
            reminder = root / "freshness.yml"
            reminder.write_text(
                "on:\n"
                "  schedule:\n"
                "    - cron: '17 6 * * 5'\n"
                "  workflow_dispatch:\n"
                "permissions:\n"
                "  issues: write\n"
                "jobs:\n"
                "  audit:\n"
                "    steps:\n"
                "      - run: |\n"
                "          python scripts/audit_freshness.py \\\n"
                "            --repository-root . \\\n"
                "            --json-output report.json \\\n"
                "            --markdown-output report.md\n"
                "          marker='repo-scaffold-freshness-audit'\n"
                '          gh issue create --repo "$REPOSITORY" --body-file report.md\n',
                encoding="utf-8",
            )
            allowlist = root / "code-scanning-allowlist.json"
            allowlist.write_text(
                '{"schema-version": 3, "allowlist": []}\n', encoding="utf-8"
            )
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                missing_all = workflow_installation_preflight.run(
                    arguments(workflow=[gate])
                )
                missing_allowlist = workflow_installation_preflight.run(
                    arguments(workflow=[gate, reminder])
                )
                ready = workflow_installation_preflight.run(
                    arguments(
                        workflow=[gate, reminder], code_scanning_allowlist=allowlist
                    )
                )

        for result in (missing_all, missing_allowlist):
            self.assertEqual(
                result["decision"],
                "include-code-scanning-companions-before-installing-workflows",
            )
            self.assertTrue(result["requires_code_scanning_companions"])
            self.assertEqual(
                result["code_scanning_gate_workflows"], ["code-scanning-gate.yml"]
            )
            self.assertFalse(result["code_scanning_companions_verified"])
        self.assertFalse(missing_all["freshness_reminder_supplied"])
        self.assertFalse(missing_all["code_scanning_allowlist_supplied"])
        self.assertTrue(missing_allowlist["freshness_reminder_supplied"])
        self.assertFalse(missing_allowlist["code_scanning_allowlist_supplied"])
        self.assertEqual(ready["decision"], "may-install-workflow-assets")
        self.assertTrue(ready["code_scanning_companions_verified"])

    def test_code_scanning_gate_rejects_invalid_allowlist_companion(self) -> None:
        self.configure()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gate = root / "code-scanning-gate.yml"
            gate.write_text(
                "jobs:\n"
                "  gate:\n"
                "    steps:\n"
                "      - run: python scripts/check_code_scanning_alerts.py\n",
                encoding="utf-8",
            )
            reminder = root / "freshness.yml"
            reminder.write_text(
                "on:\n"
                "  schedule:\n"
                "    - cron: '17 6 * * 5'\n"
                "  workflow_dispatch:\n"
                "permissions:\n"
                "  issues: write\n"
                "jobs:\n"
                "  audit:\n"
                "    steps:\n"
                "      - run: |\n"
                "          python scripts/audit_freshness.py \\\n"
                "            --repository-root . \\\n"
                "            --json-output report.json \\\n"
                "            --markdown-output report.md\n"
                "          marker='repo-scaffold-freshness-audit'\n"
                '          gh issue create --repo "$REPOSITORY" --body-file report.md\n',
                encoding="utf-8",
            )
            invalid_allowlist = root / "code-scanning-allowlist.json"
            invalid_allowlist.write_text("{}\n", encoding="utf-8")
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                with self.assertRaisesRegex(
                    workflow_installation_preflight.InspectionError,
                    "schema-version 3",
                ):
                    workflow_installation_preflight.run(
                        arguments(
                            workflow=[gate, reminder],
                            code_scanning_allowlist=invalid_allowlist,
                        )
                    )

    def test_freshness_companion_requires_scheduled_issue_reconciliation(self) -> None:
        audit_command = (
            "          python scripts/audit_freshness.py \\\n"
            "            --repository-root . \\\n"
            "            --json-output report.json \\\n"
            "            --markdown-output report.md\n"
        )
        valid = (
            "on:\n"
            "  schedule:\n"
            "    - cron: '17 6 * * 5'\n"
            "  workflow_dispatch:\n"
            "permissions:\n"
            "  issues: write\n"
            "jobs:\n"
            "  audit:\n"
            "    steps:\n"
            "      - run: |\n"
            + audit_command
            + "          marker='repo-scaffold-freshness-audit'\n"
            + '          gh issue create --repo "$REPOSITORY" --body-file report.md\n'
        )
        body_command = (
            '          gh issue create --repo "$REPOSITORY" --body-file report.md\n'
        )
        cases = {
            "valid": valid,
            "without schedule": valid.replace(
                "  schedule:\n    - cron: '17 6 * * 5'\n",
                "",
            ),
            "without manual dispatch": valid.replace("  workflow_dispatch:\n", ""),
            "with untrusted trigger": valid.replace(
                "  workflow_dispatch:\n",
                "  workflow_dispatch:\n  pull_request_target:\n",
            ),
            "with empty schedule": valid.replace(
                "  schedule:\n    - cron: '17 6 * * 5'\n", "  schedule: []\n"
            ),
            "with malformed schedule": valid.replace(
                "  schedule:\n    - cron: '17 6 * * 5'\n", "  schedule: bad\n"
            ),
            "without issue write": valid.replace("  issues: write\n", ""),
            "reconciliation job overrides issue write": valid.replace(
                "  audit:\n    steps:\n",
                "  audit:\n    permissions:\n      contents: read\n    steps:\n",
            ),
            "issue write belongs to another job": valid.replace(
                "  issues: write\n", "  issues: read\n"
            ).replace(
                "  audit:\n    steps:\n",
                "  audit:\n    permissions: {}\n    steps:\n",
            )
            + "  permissioned:\n    permissions:\n      issues: write\n",
            "without explicit repository": valid.replace(
                body_command, "          gh issue create --body-file report.md\n"
            ),
            "without repository value": valid.replace(
                body_command,
                "          gh issue create --repo= --body-file report.md\n",
            ),
            "repository option consumes another option": valid.replace(
                body_command,
                "          gh issue create --repo --body-file report.md\n",
            ),
            "without body-file value": valid.replace(
                body_command,
                '          gh issue create --repo "$REPOSITORY" --body-file=\n',
            ),
            "without durable body": valid.replace(body_command, ""),
            "issue listing is not reconciliation": valid.replace(
                body_command,
                "          gh issue list --body-file report.md\n",
            ),
            "body file belongs to another command": valid.replace(
                body_command,
                "          gh issue create --title reminder\n"
                "          echo --body-file report.md\n",
            ),
            "comment-only command": valid.replace(
                audit_command
                + "          marker='repo-scaffold-freshness-audit'\n"
                + body_command,
                "          # python scripts/audit_freshness.py\n"
                "          # --repository-root .\n"
                "          # --json-output report.json\n"
                "          # --markdown-output report.md\n"
                "          # marker='repo-scaffold-freshness-audit'\n"
                '          # gh issue create --repo "$REPOSITORY" --body-file report.md\n',
            ),
            "echo-only command": valid.replace(
                audit_command,
                "          echo 'python scripts/audit_freshness.py'\n",
            ),
            "malformed audit command": valid.replace(
                audit_command,
                "          python scripts/audit_freshness.py 'unterminated\n",
            ),
            "malformed audit arguments": valid.replace(
                audit_command,
                "          python scripts/audit_freshness.py \\\n"
                "            --repository-root . \\\n"
                "            --json-output 'report.json \\\n"
                "            --markdown-output report.md\n",
            ),
            "without audit output": valid.replace(
                "            --markdown-output report.md\n", ""
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "freshness.yml"
            for name, text in cases.items():
                with self.subTest(name=name):
                    source.write_text(text, encoding="utf-8")
                    self.assertEqual(
                        workflow_installation_preflight.is_freshness_reminder_workflow(
                            text, source
                        ),
                        name == "valid",
                    )
            self.assertFalse(
                workflow_installation_preflight.is_freshness_reminder_workflow(
                    valid, source.with_name("reminder.yml")
                )
            )

    def test_rejects_mutable_external_container_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "container.yml"
            workflow.write_text(
                "jobs:\n  build:\n    steps:\n      - uses: docker://alpine:latest\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                workflow_installation_preflight.InspectionError,
                "full sha256 digest",
            ):
                workflow_installation_preflight.workflow_capabilities([workflow])

            workflow.write_text(
                "jobs:\n"
                "  build:\n"
                "    steps:\n"
                "      - uses: docker://alpine@sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                workflow_installation_preflight.workflow_capabilities([workflow]),
                ([], [], [], [], False),
            )

    def test_code_scanning_allowlist_validation_rejects_unsafe_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(
                workflow_installation_preflight.InspectionError, "missing or unsafe"
            ):
                workflow_installation_preflight.validate_code_scanning_allowlist(root)

            allowlist = root / "code-scanning-allowlist.json"
            allowlist.write_text(
                '{"schema-version": 3, "schema-version": 3, "allowlist": []}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                workflow_installation_preflight.InspectionError, "missing or unsafe"
            ):
                workflow_installation_preflight.validate_code_scanning_allowlist(
                    allowlist
                )

            metadata = allowlist.stat()
            allowlist.write_text('{"schema-version": 3}\n', encoding="utf-8")
            with (
                mock.patch.object(
                    workflow_installation_preflight,
                    "MAX_CODE_SCANNING_ALLOWLIST_BYTES",
                    1,
                ),
                mock.patch.object(
                    Path,
                    "lstat",
                    return_value=mock.Mock(
                        st_mode=metadata.st_mode,
                        st_size=0,
                        st_file_attributes=0,
                    ),
                ),
                self.assertRaisesRegex(
                    workflow_installation_preflight.InspectionError, "missing or unsafe"
                ),
            ):
                workflow_installation_preflight.validate_code_scanning_allowlist(
                    allowlist
                )

    def test_code_scanning_allowlist_validation_rejects_invalid_entries(self) -> None:
        valid_entry = {
            "number": 1,
            "tool": "CodeQL",
            "rule": "py/example",
            "path": "scripts/example.py",
            "reason": "Reviewed.",
            "reviewed-on": "2000-01-01",
            "review-period-days": 90,
        }
        with tempfile.TemporaryDirectory() as directory:
            allowlist = Path(directory) / "code-scanning-allowlist.json"

            def write(entries: object) -> None:
                allowlist.write_text(
                    json.dumps({"schema-version": 3, "allowlist": entries}),
                    encoding="utf-8",
                )

            write([valid_entry])
            with mock.patch.object(
                workflow_installation_preflight, "datetime"
            ) as clock:
                clock.now.return_value.date.return_value = (
                    workflow_installation_preflight.date(2026, 9, 9)
                )
                workflow_installation_preflight.validate_code_scanning_allowlist(
                    allowlist
                )
                clock.now.assert_called_once_with(
                    workflow_installation_preflight.timezone.utc
                )
            write([{**valid_entry, "path": None}])
            workflow_installation_preflight.validate_code_scanning_allowlist(allowlist)

            invalid_entries = (
                ("missing selector field", [{**valid_entry, "reason": None}], "entry"),
                (
                    "missing required field",
                    [
                        {
                            key: value
                            for key, value in valid_entry.items()
                            if key != "rule"
                        }
                    ],
                    "exact selector",
                ),
                (
                    "duplicate alert number",
                    [valid_entry, {**valid_entry, "tool": "Semgrep"}],
                    "numbers must be unique",
                ),
                (
                    "non-positive alert number",
                    [{**valid_entry, "number": 0}],
                    "positive integer",
                ),
                (
                    "empty path",
                    [{**valid_entry, "path": ""}],
                    "non-empty canonical POSIX",
                ),
                (
                    "non-canonical path",
                    [{**valid_entry, "path": "../escape"}],
                    "canonical POSIX",
                ),
                (
                    "windows path",
                    [{**valid_entry, "path": "C:/example.py"}],
                    "canonical POSIX",
                ),
                (
                    "invalid review date",
                    [{**valid_entry, "reviewed-on": "not-a-date"}],
                    "ISO date",
                ),
                (
                    "empty review date",
                    [{**valid_entry, "reviewed-on": ""}],
                    "non-empty ISO date",
                ),
                (
                    "future review date",
                    [{**valid_entry, "reviewed-on": "2999-01-01"}],
                    "future",
                ),
                (
                    "zero review period",
                    [{**valid_entry, "review-period-days": 0}],
                    "review period",
                ),
                (
                    "boolean review period",
                    [{**valid_entry, "review-period-days": True}],
                    "review period",
                ),
                (
                    "excessive review period",
                    [{**valid_entry, "review-period-days": 367}],
                    "review period",
                ),
            )
            for name, entries, message in invalid_entries:
                with self.subTest(name=name):
                    write(entries)
                    with self.assertRaisesRegex(
                        workflow_installation_preflight.InspectionError, message
                    ):
                        workflow_installation_preflight.validate_code_scanning_allowlist(
                            allowlist
                        )

            with self.subTest(name="entry limit"):
                write(
                    [
                        {**valid_entry, "number": number}
                        for number in range(
                            1,
                            workflow_installation_preflight.MAX_CODE_SCANNING_ALLOWLIST_ENTRIES
                            + 2,
                        )
                    ]
                )
                with self.assertRaisesRegex(
                    workflow_installation_preflight.InspectionError, "entry limit"
                ):
                    workflow_installation_preflight.validate_code_scanning_allowlist(
                        allowlist
                    )

    def test_companion_helpers_reject_ambiguous_json_and_nonstep_lists(self) -> None:
        with self.assertRaisesRegex(
            workflow_installation_preflight.DuplicateJsonMember, "duplicate"
        ):
            workflow_installation_preflight.unique_json_object(
                [("schema-version", 3), ("schema-version", 3)]
            )
        self.assertEqual(
            workflow_installation_preflight.workflow_run_commands(
                {"jobs": {"audit": {"steps": {}}}}
            ),
            [],
        )
        self.assertFalse(
            workflow_installation_preflight.has_issue_body_file_reconciliation(
                "gh issue create 'unterminated"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_issue_body_file_reconciliation(
                'gh issue create \\\n  --body-file "unterminated'
            )
        )
        self.assertTrue(
            workflow_installation_preflight.has_nonempty_option_value(
                ["--repo", "owner/repository"], "--repo"
            )
        )
        self.assertTrue(
            workflow_installation_preflight.has_nonempty_option_value(
                ["--repo=owner/repository"], "--repo"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_nonempty_option_value(
                ["--repo="], "--repo"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_nonempty_option_value(
                ["--repo", "--title"], "--repo"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_nonempty_option_value(
                ["--repo"], "--repo"
            )
        )

    def test_local_reusable_workflows_are_required_as_preflight_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            caller = root / "release-please.yml"
            caller.write_text(
                "jobs:\n  publish:\n    uses: ./.github/workflows/release.yml\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                workflow_installation_preflight.InspectionError,
                "pass it as another --workflow",
            ):
                workflow_installation_preflight.workflow_capabilities([caller])

    def test_unsafe_local_reusable_workflow_references_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            caller = root / "caller.yml"
            caller.write_text(
                "jobs:\n  publish:\n    uses: ./.github/workflows/../release.yml\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                workflow_installation_preflight.InspectionError,
                "unsafe local reusable-workflow reference",
            ):
                workflow_installation_preflight.workflow_capabilities([caller])

    def test_duplicate_workflow_input_names_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "one" / "shared.yml"
            second = root / "two" / "shared.yml"
            with self.assertRaisesRegex(
                workflow_installation_preflight.InspectionError,
                "unique filenames",
            ):
                workflow_installation_preflight.workflow_capabilities([first, second])

    def test_shipped_release_caller_and_reusable_workflow_are_resolved_together(
        self,
    ) -> None:
        assets = PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets" / "workflows"
        capabilities = workflow_installation_preflight.workflow_capabilities(
            [assets / "release-please.yml", assets / "release.yml"]
        )
        self.assertIn(
            "googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7",
            capabilities[0],
        )
        self.assertIn(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            capabilities[0],
        )

    def test_shipped_code_scanning_companions_are_accepted_together(self) -> None:
        self.configure()
        assets = PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets"
        with mock.patch.object(
            workflow_installation_preflight, "GitHubClient", FakeClient
        ):
            result = workflow_installation_preflight.run(
                arguments(
                    workflow=[
                        assets / "workflows" / "code-scanning-gate.yml",
                        assets / "workflows" / "freshness.yml",
                    ],
                    code_scanning_allowlist=assets / "code-scanning-allowlist.json",
                )
            )

        self.assertEqual(result["decision"], "may-install-workflow-assets")
        self.assertTrue(result["freshness_reminder_supplied"])
        self.assertTrue(result["code_scanning_companions_verified"])

    def test_pull_request_write_scopes_require_explicit_confirmation(self) -> None:
        self.configure()
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "dependabot-auto-merge.yml"
            workflow.write_text(
                "on: [pull_request]\n"
                "permissions:\n"
                "  contents: write\n"
                "jobs:\n"
                "  merge:\n"
                "    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                workflow_installation_preflight, "GitHubClient", FakeClient
            ):
                result = workflow_installation_preflight.run(
                    arguments(workflow=[workflow])
                )
                confirmed = workflow_installation_preflight.run(
                    arguments(
                        workflow=[workflow], confirm_pull_request_write_tokens=True
                    )
                )

        self.assertEqual(
            result["decision"],
            "confirm-pull-request-write-tokens-before-installing-workflows",
        )
        self.assertEqual(
            result["pull_request_write_workflows"], ["dependabot-auto-merge.yml"]
        )
        self.assertFalse(result["pull_request_write_tokens_confirmed"])
        self.assertEqual(confirmed["decision"], "may-install-workflow-assets")

    def test_pull_request_write_token_gate_ignores_read_only_and_target_workflows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cases = {
                "read-only.yml": "on: pull_request\npermissions: {contents: read}\njobs: {}\n",
                "target.yml": "on: pull_request_target\npermissions: {contents: write}\njobs: {}\n",
                "job-scope.yml": "on: pull_request\njobs:\n  merge:\n    permissions: {pull-requests: write}\n",
            }
            for filename, document in cases.items():
                with self.subTest(filename=filename):
                    workflow = Path(directory) / filename
                    workflow.write_text(document, encoding="utf-8")
                    expected = filename == "job-scope.yml"
                    self.assertEqual(
                        workflow_installation_preflight.workflow_capabilities(
                            [workflow]
                        )[2],
                        [filename] if expected else [],
                    )

    def test_pull_request_write_token_gate_fails_closed_for_invalid_workflows(
        self,
    ) -> None:
        source = Path("workflow.yml")
        for document, message in (
            ("on: [", "Could not parse"),
            ("[]", "not a YAML mapping"),
            ("jobs: []", "invalid jobs mapping"),
        ):
            with self.subTest(document=document):
                with self.assertRaisesRegex(
                    workflow_installation_preflight.InspectionError, message
                ):
                    workflow_installation_preflight.requires_pull_request_write_tokens(
                        document, source
                    )
        self.assertTrue(
            workflow_installation_preflight.requires_pull_request_write_tokens(
                "on: {pull_request: {}}\npermissions: write-all\njobs: {}\n",
                source,
            )
        )

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
                "--code-scanning-allowlist",
                "assets/code-scanning-allowlist.json",
            ],
        ):
            parsed = workflow_installation_preflight.parse_args()

        self.assertEqual(parsed.repository, "octo/example")
        self.assertTrue(parsed.require_external_actions)
        self.assertTrue(parsed.require_issues)
        self.assertFalse(parsed.confirm_pull_request_write_tokens)
        self.assertEqual(parsed.workflow, [Path("assets/workflows/ci.yml")])
        self.assertEqual(
            parsed.code_scanning_allowlist,
            Path("assets/code-scanning-allowlist.json"),
        )
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
