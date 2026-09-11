from __future__ import annotations

import argparse
import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
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
                "  contents: read\n"
                "  issues: write\n"
                "concurrency:\n"
                "  group: ${{ github.workflow }}-${{ github.repository }}\n"
                "  cancel-in-progress: false\n"
                "jobs:\n"
                "  audit:\n"
                "    name: freshness-audit\n"
                "    runs-on: ubuntu-latest\n"
                "    timeout-minutes: 15\n"
                "    steps:\n"
                "      - id: audit\n"
                "        env:\n"
                "          GITHUB_TOKEN: ${{ github.token }}\n"
                "        run: |\n"
                "          set +e\n"
                "          python scripts/audit_freshness.py \\\n"
                "            --repository-root . \\\n"
                "            --json-output $RUNNER_TEMP/freshness.json \\\n"
                "            --markdown-output $RUNNER_TEMP/freshness.md\n"
                "          checker_exit=$?\n"
                "          set -e\n"
                '          if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n'
                "            printf '%s\\n' '<!-- repo-scaffold-freshness-audit -->' > \"$RUNNER_TEMP/freshness.md\"\n"
                "            checker_exit=2\n"
                "          fi\n"
                '          printf \'checker_exit=%s\\n\' "$checker_exit" >> "$GITHUB_OUTPUT"\n'
                "      - name: Add report to job summary\n"
                "        shell: bash\n"
                '        run: cat "$RUNNER_TEMP/freshness.md" >> "$GITHUB_STEP_SUMMARY"\n'
                "      - name: Reconcile reminder issue\n"
                "        env:\n"
                "          GH_TOKEN: ${{ github.token }}\n"
                "          CHECKER_EXIT: ${{ steps.audit.outputs.checker_exit }}\n"
                "        run: |\n"
                "          set -euo pipefail\n"
                "          issue_numbers_output=$(\n"
                "            gh api --hostname github.com --paginate \\\n"
                '              "repos/$GITHUB_REPOSITORY/issues?state=open&per_page=100" \\\n'
                "              --jq '.[] | select(.pull_request == null) | "
                'select((.body // "") | contains("<!-- repo-scaffold-freshness-audit -->")) | .number\'\n'
                "          )\n"
                "          issue_numbers=()\n"
                '          if [[ -n "$issue_numbers_output" ]]; then\n'
                '            mapfile -t issue_numbers <<< "$issue_numbers_output"\n'
                "          fi\n"
                "          if (( ${#issue_numbers[@]} > 1 )); then\n"
                "            printf 'Found multiple open freshness reminder issues.\\n' >&2\n"
                "            exit 1\n"
                "          fi\n"
                "          marker='<!-- repo-scaffold-freshness-audit -->'\n"
                '          grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"\n'
                "          if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n"
                "            if (( ${#issue_numbers[@]} == 1 )); then\n"
                '              gh issue close "${issue_numbers[0]}" --repo "github.com/$GITHUB_REPOSITORY" --comment clean\n'
                "            fi\n"
                "            exit 0\n"
                "          fi\n"
                "          if (( ${#issue_numbers[@]} == 1 )); then\n"
                '            gh issue edit "${issue_numbers[0]}" --repo "github.com/$GITHUB_REPOSITORY" --body-file "$RUNNER_TEMP/freshness.md"\n'
                "          else\n"
                '            gh issue create --repo "github.com/$GITHUB_REPOSITORY" --title reminder --body-file "$RUNNER_TEMP/freshness.md"\n'
                "          fi\n"
                "          if [[ \"$CHECKER_EXIT\" != '0' ]]; then\n"
                "            exit 1\n"
                "          fi\n",
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
                "concurrency:\n"
                "  group: ${{ github.workflow }}-${{ github.repository }}\n"
                "  cancel-in-progress: false\n"
                "jobs:\n"
                "  audit:\n"
                "    name: freshness-audit\n"
                "    runs-on: ubuntu-latest\n"
                "    timeout-minutes: 15\n"
                "    steps:\n"
                "      - run: |\n"
                "          set +e\n"
                "          python scripts/audit_freshness.py \\\n"
                "            --repository-root . \\\n"
                "            --json-output $RUNNER_TEMP/freshness.json \\\n"
                "            --markdown-output $RUNNER_TEMP/freshness.md\n"
                "          checker_exit=$?\n"
                "          set -e\n"
                '          if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n'
                "            printf '%s\\n' '<!-- repo-scaffold-freshness-audit -->' > \"$RUNNER_TEMP/freshness.md\"\n"
                "            checker_exit=2\n"
                "          fi\n"
                "          gh api --hostname github.com --paginate "
                '"repos/$GITHUB_REPOSITORY/issues?state=open&per_page=100" '
                "--jq '.[] | select(.pull_request == null) | "
                'select((.body // "") | contains("<!-- repo-scaffold-freshness-audit -->")) | .number\'\n'
                "          marker='<!-- repo-scaffold-freshness-audit -->'\n"
                '          grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"\n'
                '          gh issue create --repo "github.com/$GITHUB_REPOSITORY" --title reminder --body-file "$RUNNER_TEMP/freshness.md"\n',
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
            "          set +e\n"
            "          python scripts/audit_freshness.py \\\n"
            "            --repository-root . \\\n"
            "            --json-output $RUNNER_TEMP/freshness.json \\\n"
            "            --markdown-output $RUNNER_TEMP/freshness.md\n"
            "          checker_exit=$?\n"
            "          set -e\n"
        )
        fallback_command = (
            '          if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n'
            "            printf '%s\\n' '<!-- repo-scaffold-freshness-audit -->' > \"$RUNNER_TEMP/freshness.md\"\n"
            "            checker_exit=2\n"
            "          fi\n"
        )
        valid = (
            "on:\n"
            "  schedule:\n"
            "    - cron: '17 6 * * 5'\n"
            "  workflow_dispatch:\n"
            "permissions:\n"
            "  contents: read\n"
            "  issues: write\n"
            "concurrency:\n"
            "  group: ${{ github.workflow }}-${{ github.repository }}\n"
            "  cancel-in-progress: false\n"
            "jobs:\n"
            "  audit:\n"
            "    name: freshness-audit\n"
            "    runs-on: ubuntu-latest\n"
            "    timeout-minutes: 15\n"
            "    steps:\n"
            "      - id: audit\n"
            "        env:\n"
            "          GITHUB_TOKEN: ${{ github.token }}\n"
            "        run: |\n"
            + audit_command
            + fallback_command
            + '          printf \'checker_exit=%s\\n\' "$checker_exit" >> "$GITHUB_OUTPUT"\n'
            + "      - name: Add report to job summary\n"
            + "        shell: bash\n"
            + '        run: cat "$RUNNER_TEMP/freshness.md" >> "$GITHUB_STEP_SUMMARY"\n'
            + "      - name: Reconcile reminder issue\n"
            + "        env:\n"
            + "          GH_TOKEN: ${{ github.token }}\n"
            + "          CHECKER_EXIT: ${{ steps.audit.outputs.checker_exit }}\n"
            + "        run: |\n"
            + "          set -euo pipefail\n"
            + "          marker='repo-scaffold-freshness-audit'\n"
            + '          grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"\n'
        )
        repository_lookup_command = (
            "          issue_numbers_output=$(\n"
            "            gh api --hostname github.com --paginate \\\n"
            '              "repos/$GITHUB_REPOSITORY/issues?state=open&per_page=100" \\\n'
            "              --jq '.[] | select(.pull_request == null) | "
            'select((.body // "") | contains("<!-- repo-scaffold-freshness-audit -->")) | .number\'\n'
            "          )\n"
            "          issue_numbers=()\n"
            '          if [[ -n "$issue_numbers_output" ]]; then\n'
            '            mapfile -t issue_numbers <<< "$issue_numbers_output"\n'
            "          fi\n"
            "          if (( ${#issue_numbers[@]} > 1 )); then\n"
            "            printf 'Found multiple open freshness reminder issues.\\n' >&2\n"
            "            exit 1\n"
            "          fi\n"
        )
        body_command = (
            "          if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n"
            "            if (( ${#issue_numbers[@]} == 1 )); then\n"
            '              gh issue close "${issue_numbers[0]}" --repo "github.com/$GITHUB_REPOSITORY" --comment clean\n'
            "            fi\n"
            "            exit 0\n"
            "          fi\n"
            "          if (( ${#issue_numbers[@]} == 1 )); then\n"
            '            gh issue edit "${issue_numbers[0]}" --repo "github.com/$GITHUB_REPOSITORY" --body-file "$RUNNER_TEMP/freshness.md"\n'
            "          else\n"
            '            gh issue create --repo "github.com/$GITHUB_REPOSITORY" --title reminder --body-file "$RUNNER_TEMP/freshness.md"\n'
            "          fi\n"
            "          if [[ \"$CHECKER_EXIT\" != '0' ]]; then\n"
            "            exit 1\n"
            "          fi\n"
        )
        valid += repository_lookup_command + body_command
        cases = {
            "valid": valid,
            "extra API option": valid.replace(
                repository_lookup_command,
                repository_lookup_command.replace("--paginate ", "--paginate --slurp "),
            ),
            "extra API output": valid.replace(
                repository_lookup_command,
                repository_lookup_command.replace("| .number'\n", "| .number, 999'\n"),
            ),
            "API result ignored": valid.replace(
                '          if [[ -n "$issue_numbers_output" ]]; then\n'
                '            mapfile -t issue_numbers <<< "$issue_numbers_output"\n'
                "          fi\n",
                "",
            ),
            "API result only logged": valid.replace(
                '            mapfile -t issue_numbers <<< "$issue_numbers_output"\n',
                '            echo "$issue_numbers_output"\n',
            ),
            "API result only tested": valid.replace(
                '            mapfile -t issue_numbers <<< "$issue_numbers_output"\n',
                '            if [[ -n "$issue_numbers_output" ]]; then :; fi\n',
            ),
            "checker result ignored": valid.replace(
                "          if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n",
                "          if true; then\n",
                1,
            ),
            "checker stale result ignored": valid.replace(
                "          if [[ \"$CHECKER_EXIT\" != '0' ]]; then\n",
                "          if true; then\n",
                1,
            ),
            "checker output spoofed": valid.replace(
                '          printf \'checker_exit=%s\\n\' "$checker_exit" >> "$GITHUB_OUTPUT"\n',
                '          printf \'checker_exit=%s\\n\' "0" >> "$GITHUB_OUTPUT"\n',
                1,
            ),
            "checker output capture missing": valid.replace(
                "          checker_exit=$?\n", "", 1
            ),
            "checker binding spoofed": valid.replace(
                "          CHECKER_EXIT: "
                + "$"
                + "{{ steps.audit.outputs.checker_exit }}\n",
                "          CHECKER_EXIT: 0\n",
                1,
            ),
            "checker binding missing": valid.replace(
                "          CHECKER_EXIT: "
                + "$"
                + "{{ steps.audit.outputs.checker_exit }}\n",
                "",
                1,
            ),
            "audit token spoofed": valid.replace(
                "          GITHUB_TOKEN: " + "$" + "{{ github.token }}\n",
                "          GITHUB_TOKEN: attacker\n",
                1,
            ),
            "issue token spoofed": valid.replace(
                "          GH_TOKEN: " + "$" + "{{ github.token }}\n",
                "          GH_TOKEN: attacker\n",
                1,
            ),
            "audit token missing": valid.replace(
                "          GITHUB_TOKEN: " + "$" + "{{ github.token }}\n",
                "",
                1,
            ),
            "issue token missing": valid.replace(
                "          GH_TOKEN: " + "$" + "{{ github.token }}\n",
                "",
                1,
            ),
            "workflow token spoofed": valid.replace(
                "jobs:\n",
                "env:\n  GH_TOKEN: attacker\njobs:\n",
                1,
            ),
            "GITHUB_OUTPUT spoofed": valid.replace(
                "          GITHUB_TOKEN: " + "$" + "{{ github.token }}\n",
                "          GITHUB_TOKEN: " + "$" + "{{ github.token }}\n"
                "          GITHUB_OUTPUT: /tmp/output\n",
                1,
            ),
            "RUNNER_TEMP spoofed": valid.replace(
                "          GITHUB_TOKEN: " + "$" + "{{ github.token }}\n",
                "          GITHUB_TOKEN: " + "$" + "{{ github.token }}\n"
                "          RUNNER_TEMP: /tmp\n",
                1,
            ),
            "shell checker overwrite": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          CHECKER_EXIT=0\n"
                "          marker='repo-scaffold-freshness-audit'\n",
                1,
            ),
            "shell checker unset": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          unset CHECKER_EXIT\n"
                "          marker='repo-scaffold-freshness-audit'\n",
                1,
            ),
            "shell path overwrite": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          PATH=/tmp/fake:$PATH\n"
                "          marker='repo-scaffold-freshness-audit'\n",
                1,
            ),
            "shell hash overwrite": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          hash -p /tmp/fake/gh gh\n"
                "          marker='repo-scaffold-freshness-audit'\n",
                1,
            ),
            "checker clean close missing": valid.replace(
                '          gh issue close "' + "$" + '{issue_numbers[0]}"',
                "          echo closed",
                1,
            ),
            "checker close missing issue": valid.replace(
                '          gh issue close "' + "$" + '{issue_numbers[0]}"',
                "          gh issue close",
                1,
            ),
            "checker clean exit missing": valid.replace(
                "          exit 0\n",
                "",
                1,
            ),
            "checker failure exit missing": valid.replace(
                "          if [[ \"$CHECKER_EXIT\" != '0' ]]; then\n"
                "            exit 1\n"
                "          fi\n",
                "",
                1,
            ),
            "shell function shadows gh": valid.replace(
                "          if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n",
                "          gh() { return 1; }\n"
                "          if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n",
                1,
            ),
            "shell alias shadows gh": valid.replace(
                "          if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n",
                "          alias gh='echo shadowed'\n"
                "          if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n",
                1,
            ),
            "conditional audit job": valid.replace(
                "    timeout-minutes: 15\n",
                "    timeout-minutes: 15\n    if: false\n",
                1,
            ),
            "continue-on-error audit job": valid.replace(
                "    timeout-minutes: 15\n",
                "    timeout-minutes: 15\n    continue-on-error: true\n",
                1,
            ),
            "conditional audit step": valid.replace(
                "      - id: audit\n"
                "        env:\n"
                "          GITHUB_TOKEN: ${{ github.token }}\n"
                "        run: |\n",
                "      - id: audit\n"
                "        env:\n"
                "          GITHUB_TOKEN: ${{ github.token }}\n"
                "        if: false\n"
                "        run: |\n",
                1,
            ),
            "continue-on-error audit step": valid.replace(
                "      - id: audit\n"
                "        env:\n"
                "          GITHUB_TOKEN: ${{ github.token }}\n"
                "        run: |\n",
                "      - id: audit\n"
                "        env:\n"
                "          GITHUB_TOKEN: ${{ github.token }}\n"
                "        continue-on-error: true\n"
                "        run: |\n",
                1,
            ),
            "non-Ubuntu runner": valid.replace(
                "    runs-on: ubuntu-latest\n", "    runs-on: windows-latest\n", 1
            ),
            "non-Bash workflow shell": valid.replace(
                "jobs:\n",
                "defaults:\n  run:\n    shell: pwsh\njobs:\n",
                1,
            ),
            "non-Bash job shell": valid.replace(
                "    runs-on: ubuntu-latest\n",
                "    runs-on: ubuntu-latest\n"
                "    defaults:\n"
                "      run:\n"
                "        shell: pwsh\n",
                1,
            ),
            "non-Bash step shell": valid.replace(
                "      - id: audit\n",
                "      - id: audit\n        shell: pwsh\n",
                1,
            ),
            "issue mutation before audit": valid.replace(
                audit_command, body_command + audit_command, 1
            ),
            "API lookup before audit": valid.replace(
                audit_command, repository_lookup_command + audit_command, 1
            ),
            "without current repository lookup": valid.replace(
                repository_lookup_command, ""
            ),
            "missing freshness timeout": valid.replace("    timeout-minutes: 15\n", ""),
            "wrong freshness job name": valid.replace(
                "    name: freshness-audit\n", "    name: reminder\n"
            ),
            "branch-scoped concurrency": valid.replace(
                "${{ github.repository }}", "${{ github.ref }}"
            ),
            "cancelling concurrency": valid.replace(
                "  cancel-in-progress: false", "  cancel-in-progress: true"
            ),
            "overbroad workflow permissions": valid.replace(
                "permissions:\n  contents: read\n  issues: write",
                "permissions:\n  contents: write\n  issues: write",
            ),
            "overbroad job permissions": valid.replace(
                "  audit:\n    name: freshness-audit\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n",
                "  audit:\n    name: freshness-audit\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    permissions: write-all\n    steps:\n",
            ),
            "reconciliation job cannot read contents": valid.replace(
                "  audit:\n    name: freshness-audit\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n",
                "  audit:\n"
                "    name: freshness-audit\n"
                "    runs-on: ubuntu-latest\n"
                "    timeout-minutes: 15\n"
                "    permissions:\n"
                "      contents: none\n"
                "      issues: write\n"
                "    steps:\n",
            ),
            "unbound close mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          gh issue close 1\n"
                "          marker='repo-scaffold-freshness-audit'\n",
            ),
            "same-line unbound close mutation": valid.replace(
                body_command,
                body_command.rstrip("\n") + "; gh issue close 1\n",
            ),
            "global repo unsupported mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                '          gh --repo "$REPOSITORY" issue reopen 1\n',
            ),
            "hidden issue after unsupported global option": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          gh --hostname github.com issue close 1\n",
            ),
            "unbound edit body": valid.replace(
                body_command,
                body_command
                + '          gh issue edit 1 --repo "$REPOSITORY" --body stale\n',
            ),
            "unsupported issue mutation": valid.replace(
                body_command,
                body_command + '          gh issue reopen 1 --repo "$REPOSITORY"\n',
            ),
            "create without title": valid.replace(" --title reminder", ""),
            "unbound close in separate step": valid.replace(
                "    steps:\n"
                "      - id: audit\n"
                "        env:\n"
                "          GITHUB_TOKEN: ${{ github.token }}\n"
                "        run: |\n",
                "    steps:\n      - run: gh issue close 1\n"
                "      - id: audit\n"
                "        env:\n"
                "          GITHUB_TOKEN: ${{ github.token }}\n"
                "        run: |\n",
            ),
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
            "with invalid cron syntax": valid.replace(
                "    - cron: '17 6 * * 5'\n", "    - cron: 'weekly'\n"
            ),
            "with reversed cron range": valid.replace(
                "    - cron: '17 6 * * 5'\n", "    - cron: '59-0 * * * *'\n"
            ),
            "with unknown schedule key": valid.replace(
                "    - cron: '17 6 * * 5'\n",
                "    - cron: '17 6 * * 5'\n      unexpected: value\n",
            ),
            "with invalid schedule timezone": valid.replace(
                "    - cron: '17 6 * * 5'\n",
                "    - cron: '17 6 * * 5'\n      timezone: Not/AZone\n",
            ),
            "with invalid workflow dispatch": valid.replace(
                "  workflow_dispatch:\n", "  workflow_dispatch: false\n"
            ),
            "with unknown workflow dispatch configuration": valid.replace(
                "  workflow_dispatch:\n",
                "  workflow_dispatch:\n    unexpected: value\n",
            ),
            "with invalid workflow dispatch inputs": valid.replace(
                "  workflow_dispatch:\n",
                "  workflow_dispatch:\n    inputs: []\n",
            ),
            "with dynamic printf format": valid.replace(
                "          set -euo pipefail\n",
                "          set -euo pipefail\n"
                "          fmt='%n'\n"
                '          printf "$fmt" CHECKER_EXIT\n',
                1,
            ),
            "with unknown exit status": valid.replace(
                '          grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"\n',
                '          grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"\n'
                "          exit 2\n",
                1,
            ),
            "with seeded issue number": valid.replace(
                "          issue_numbers=()\n", "          issue_numbers=99\n", 1
            ),
            "with issue number reseeded": valid.replace(
                "          issue_numbers=()\n",
                "          issue_numbers=()\n          issue_numbers=99\n",
                1,
            ),
            "with late issue number initialization": valid.replace(
                "          issue_numbers=()\n", "", 1
            ).replace(
                '            mapfile -t issue_numbers <<< "$issue_numbers_output"\n',
                '            mapfile -t issue_numbers <<< "$issue_numbers_output"\n'
                "          issue_numbers=()\n",
                1,
            ),
            "with branch-deleting close option": valid.replace(
                "--comment clean\n", "--comment clean --delete-branch\n", 1
            ),
            "with extra issue option": valid.replace(
                '--body-file "$RUNNER_TEMP/freshness.md"\n',
                '--body-file "$RUNNER_TEMP/freshness.md" --project 1\n',
                1,
            ),
            "with unbound dynamic title": valid.replace(
                "--title reminder", '--title "$UNTRUSTED_TITLE"', 1
            ),
            "with asynchronous audit step": valid.replace(
                "      - id: audit\n",
                "      - id: audit\n        background: true\n",
                1,
            ),
            "with snapshot job": valid.replace(
                "    timeout-minutes: 15\n",
                "    timeout-minutes: 15\n    snapshot: freshness-image\n",
                1,
            ),
            "with cache-mode job": valid.replace(
                "    timeout-minutes: 15\n",
                "    timeout-minutes: 15\n    cache-mode: write\n",
                1,
            ),
            "job needs skipped dependency": valid.replace(
                "  audit:\n",
                "  audit:\n    needs: gate\n",
                1,
            )
            + "  gate:\n    if: false\n    runs-on: ubuntu-latest\n    steps: []\n",
            "job matrix strategy": valid.replace(
                "  audit:\n",
                "  audit:\n    strategy:\n      matrix:\n        item: [one, two]\n",
                1,
            ),
            "without issue write": valid.replace("  issues: write\n", ""),
            "reconciliation job overrides issue write": valid.replace(
                "  audit:\n    name: freshness-audit\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n",
                "  audit:\n    name: freshness-audit\n    timeout-minutes: 15\n    permissions:\n      contents: read\n    steps:\n",
            ),
            "issue write belongs to another job": valid.replace(
                "  issues: write\n", "  issues: read\n"
            ).replace(
                "  audit:\n    name: freshness-audit\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n",
                "  audit:\n    name: freshness-audit\n    timeout-minutes: 15\n    permissions: {}\n    steps:\n",
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
            "body file is not checker report": valid.replace(
                body_command,
                '          gh issue create --repo "$REPOSITORY" --title reminder --body-file other.md\n',
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
                audit_command,
                "          # python scripts/audit_freshness.py\n"
                "          # --repository-root .\n"
                "          # --json-output report.json\n"
                "          # --markdown-output report.md\n"
                "          # marker='repo-scaffold-freshness-audit'\n",
                1,
            ),
            "echo-only command": valid.replace(
                audit_command,
                "          echo 'python scripts/audit_freshness.py'\n",
            ),
            "malformed audit command": valid.replace(
                audit_command,
                "          set +e\n"
                "          python scripts/audit_freshness.py 'unterminated\n",
            ),
            "malformed audit arguments": valid.replace(
                audit_command,
                "          python scripts/audit_freshness.py \\\n"
                "            --repository-root . \\\n"
                "            --json-output 'report.json \\\n"
                "            --markdown-output report.md\n",
            ),
            "audit options from another command": valid.replace(
                audit_command,
                "          python scripts/audit_freshness.py --repository-root .\n"
                "          echo --json-output report.json --markdown-output report.md\n",
            ),
            "without audit output": valid.replace(
                "            --markdown-output $RUNNER_TEMP/freshness.md\n", ""
            ),
            "audit uses another repository root": valid.replace(
                "            --repository-root . \\\n",
                "            --repository-root other \\\n",
            ),
            "audit uses another tracker registry": valid.replace(
                "            --repository-root . \\\n",
                "            --repository-root . \\\n"
                "            --tracker-registry other.json \\\n",
            ),
            "mutation targets another repository": valid.replace(
                ' --repo "github.com/$GITHUB_REPOSITORY"',
                ' --repo "attacker/repository"',
            ),
            "audit changes directory": valid.replace(
                audit_command,
                "          cd other\n" + audit_command,
            ),
            "conditional directory change": valid.replace(
                audit_command,
                "          if cd other; then\n" + audit_command + "          fi\n",
            ),
            "audit working directory override": valid.replace(
                "    steps:\n",
                "    defaults:\n"
                "      run:\n"
                "        working-directory: other\n"
                "    steps:\n",
                1,
            ),
            "hidden reusable workflow job": valid
            + "  hidden:\n"
            + "    uses: owner/repository/.github/workflows/reusable.yml@"
            + "0123456789abcdef0123456789abcdef01234567\n",
            "hidden global API lookup": valid
            + "  hidden-api:\n"
            + "    steps:\n"
            + "      - run: gh --hostname github.com api "
            + "\"repos/attacker/repository/issues\" --jq '.number'\n",
            "JSON and Markdown outputs collide": valid.replace(
                "            --json-output $RUNNER_TEMP/freshness.json \\\n",
                "            --json-output $RUNNER_TEMP/freshness.md \\\n",
            ),
            "REST issue mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          gh api --method POST repos/${REPOSITORY}/issues -f title=x\n",
            ),
            "REST body mutation with default method": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          gh api repos/${REPOSITORY}/issues --input issue.json\n",
            ),
            "path-qualified issue mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          /usr/bin/gh issue close 1\n",
            ),
            "Windows gh executable mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          gh.exe issue close 1\n",
            ),
            "quoted issue mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          bash -c 'gh issue close 1'\n",
            ),
            "backtick issue mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          echo `gh issue close 1`\n",
            ),
            "dynamic shell mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                '          bash -c "$COMMAND"\n',
            ),
            "wrapped dynamic shell mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                '          env bash -c "$COMMAND"\n',
            ),
            "dynamic command variable mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          $command issue close 1 --repo repo\n",
            ),
            "sourced hidden mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          source hidden.sh\n",
            ),
            "PowerShell process mutation": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          Start-Process gh -ArgumentList 'issue close 1 --repo repo'\n",
            ),
            "direct HTTP mutation client": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          curl -X POST https://api.github.com/repos/r/issues\n",
            ),
            "PowerShell HTTP mutation client": valid.replace(
                "          marker='repo-scaffold-freshness-audit'\n",
                "          marker='repo-scaffold-freshness-audit'\n"
                "          Invoke-RestMethod -Method Post -Uri https://api.github.com/repos/r/issues\n",
            ),
            "audit and reconciliation in different jobs": valid.replace(
                body_command,
                "",
            ).replace(
                "  audit:\n    name: freshness-audit\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n",
                "  reconcile:\n"
                "    permissions:\n"
                "      issues: write\n"
                "    steps:\n"
                "      - run: |\n"
                '          gh issue create --repo "$REPOSITORY" --title reminder --body-file report.md\n'
                "  audit:\n    name: freshness-audit\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n",
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

        for value, expected in (
            (None, False),
            ("weekly", False),
            ("17 6 * * 5", True),
            ("0/15 4-6 * JAN-MAR MON-FRI", True),
            ("59-0 * * * *", False),
            ("0-59 * * * *", True),
            ("0 0 * DEC-JAN *", False),
            ("0 0 * * FRI-MON", False),
            ("0 5 * * 7", False),
        ):
            with self.subTest(cron=value):
                self.assertEqual(
                    workflow_installation_preflight.freshness_cron_syntax_is_valid(
                        value
                    ),
                    expected,
                )

        for entry, expected in (
            ({"cron": "17 6 * * 5"}, True),
            ({"cron": "17 6 * * 5", "timezone": "Etc/UTC"}, True),
            ({"cron": "17 6 * * 5", "timezone": "Not/AZone"}, False),
            ({"cron": "17 6 * * 5", "timezone": None}, False),
            ({"cron": "17 6 * * 5", "unexpected": "value"}, False),
            ({"cron": "59-0 * * * *"}, False),
            (None, False),
        ):
            with self.subTest(schedule_entry=entry):
                self.assertEqual(
                    workflow_installation_preflight.freshness_schedule_entry_is_valid(
                        entry
                    ),
                    expected,
                )

        for dispatch_value, expected in (
            ("", True),
            (None, True),
            ("null", True),
            ({}, True),
            ({"inputs": {}}, True),
            ({"inputs": {"mode": {}}}, True),
            (
                {
                    "inputs": {
                        "mode": {
                            "description": "Mode",
                            "required": "true",
                            "default": "safe",
                            "type": "choice",
                            "options": ["safe", "full"],
                        }
                    }
                },
                True,
            ),
            (False, False),
            ([], False),
            ({"unexpected": {}}, False),
            ({"inputs": []}, False),
            ({"inputs": {"": {}}}, False),
            ({"inputs": {1: {}}}, False),
            ({"inputs": {"bad\nname": {}}}, False),
            ({"inputs": {"mode": []}}, False),
            ({"inputs": {"mode": {"unknown": "value"}}}, False),
            ({"inputs": {"mode": {"description": []}}}, False),
            ({"inputs": {"mode": {"required": True}}}, False),
            ({"inputs": {"mode": {"required": "maybe"}}}, False),
            ({"inputs": {"mode": {"type": "invalid"}}}, False),
            ({"inputs": {"mode": {"default": []}}}, False),
            ({"inputs": {"mode": {"type": "choice"}}}, False),
            ({"inputs": {"mode": {"type": "choice", "options": []}}}, False),
            (
                {"inputs": {"mode": {"type": "choice", "options": [1]}}},
                False,
            ),
            ({"inputs": {"mode": {"type": "string", "options": ["x"]}}}, False),
            ({"inputs": {f"input-{index}": {} for index in range(26)}}, False),
        ):
            with self.subTest(workflow_dispatch=dispatch_value):
                self.assertEqual(
                    workflow_installation_preflight.freshness_workflow_dispatch_is_valid(
                        dispatch_value
                    ),
                    expected,
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

            for content in (
                "jobs:\n  build:\n    container: alpine:latest\n",
                "jobs:\n"
                "  build:\n"
                "    services:\n"
                "      database:\n"
                "        image: postgres:latest\n",
            ):
                workflow.write_text(content, encoding="utf-8")
                with self.subTest(container_content=content):
                    with self.assertRaisesRegex(
                        workflow_installation_preflight.InspectionError,
                        "full sha256 digest",
                    ):
                        workflow_installation_preflight.workflow_capabilities(
                            [workflow]
                        )

            workflow.write_text(
                "jobs:\n"
                "  build:\n"
                "    container: alpine@sha256:" + "a" * 64 + "\n"
                "    services:\n"
                "      database:\n"
                "        image: postgres@sha256:" + "b" * 64 + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                workflow_installation_preflight.workflow_capabilities([workflow]),
                ([], [], [], [], False),
            )

            for content, message in (
                (
                    "jobs:\n  build:\n    container: {options: --init}\n",
                    "full sha256 digest",
                ),
                ("jobs:\n  build:\n    services: []\n", "services must be a mapping"),
            ):
                workflow.write_text(content, encoding="utf-8")
                with self.subTest(container_content=content):
                    with self.assertRaisesRegex(
                        workflow_installation_preflight.InspectionError, message
                    ):
                        workflow_installation_preflight.workflow_capabilities(
                            [workflow]
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
        self.assertTrue(
            workflow_installation_preflight.freshness_job_execution_is_unconditional(
                {"steps": [{}]}
            )
        )
        jobs: tuple[object, ...] = (
            None,
            {"if": "false", "steps": []},
            {"continue-on-error": "true", "steps": []},
            {"needs": "gate", "steps": []},
            {"strategy": {"matrix": {"item": ["one", "two"]}}, "steps": []},
            {"environment": "production", "steps": []},
            {"concurrency": {"group": "other"}, "steps": []},
            {"steps": {}},
            {"steps": [None]},
            {"steps": [{"if": "false"}]},
            {"steps": [{"continue-on-error": "true"}]},
            {"steps": [{"background": "true"}]},
            {"steps": [{"parallel": []}]},
            {"steps": [{"wait": "audit"}]},
            {"steps": [{"wait-all": "true"}]},
            {"steps": [{"cancel": "audit"}]},
            {"steps": [{"timeout-minutes": "1"}]},
            {"snapshot": "freshness-image", "steps": []},
            {"cache-mode": "write", "steps": []},
        )
        for job in jobs:
            with self.subTest(unconditional_job=job):
                self.assertFalse(
                    workflow_installation_preflight.freshness_job_execution_is_unconditional(
                        job
                    )
                )
        workflow_path = PLUGIN_ROOT / ".github" / "workflows" / "freshness.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        workflow_document = workflow_installation_preflight.workflow_document(
            workflow_text, workflow_path
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_execution_context_is_bash(
                workflow_document, workflow_document["jobs"]["audit"]
            )
        )
        self.assertIsNone(
            workflow_installation_preflight.freshness_shell_if_block_ranges([["fi"]])
        )
        self.assertIsNone(
            workflow_installation_preflight.freshness_shell_if_block_ranges(
                [["if", "true"]]
            )
        )
        self.assertEqual(
            workflow_installation_preflight.freshness_shell_if_block_ranges(
                [["if", "true"], ["if", "true"], ["fi"], ["fi"]]
            ),
            {1: 2, 0: 3},
        )
        defaults_candidate = workflow_installation_preflight.workflow_document(
            workflow_text, workflow_path
        )
        defaults_candidate["defaults"] = {"run": {"shell": "bash"}}
        self.assertTrue(
            workflow_installation_preflight.freshness_execution_context_is_bash(
                defaults_candidate, defaults_candidate["jobs"]["audit"]
            )
        )
        defaults_without_run = workflow_installation_preflight.workflow_document(
            workflow_text, workflow_path
        )
        defaults_without_run["defaults"] = {}
        self.assertTrue(
            workflow_installation_preflight.freshness_execution_context_is_bash(
                defaults_without_run, defaults_without_run["jobs"]["audit"]
            )
        )
        context_cases: tuple[tuple[str, Any], ...] = (
            ("invalid workflow", None),
            ("invalid job", None),
        )
        for name, invalid in context_cases:
            with self.subTest(context=name):
                self.assertFalse(
                    workflow_installation_preflight.freshness_execution_context_is_bash(
                        invalid,
                        workflow_document["jobs"]["audit"],
                    )
                )
        for name, mutate in (
            (
                "runner",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"runs-on": "windows-latest"}
                ),
            ),
            (
                "workflow defaults type",
                lambda candidate: candidate.update({"defaults": []}),
            ),
            (
                "workflow run defaults type",
                lambda candidate: candidate.update({"defaults": {"run": []}}),
            ),
            (
                "workflow shell",
                lambda candidate: candidate.update(
                    {"defaults": {"run": {"shell": "pwsh"}}}
                ),
            ),
        ):
            candidate = workflow_installation_preflight.workflow_document(
                workflow_text, workflow_path
            )
            mutate(candidate)
            with self.subTest(context=name):
                self.assertFalse(
                    workflow_installation_preflight.freshness_execution_context_is_bash(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        for name, mutate in (
            (
                "job defaults type",
                lambda candidate: candidate["jobs"]["audit"].update({"defaults": []}),
            ),
            (
                "job run defaults type",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"defaults": {"run": []}}
                ),
            ),
            (
                "job shell",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"defaults": {"run": {"shell": "pwsh"}}}
                ),
            ),
            (
                "step shell",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0].update(
                    {"shell": "pwsh"}
                ),
            ),
            (
                "invalid steps",
                lambda candidate: candidate["jobs"]["audit"].update({"steps": {}}),
            ),
        ):
            candidate = workflow_installation_preflight.workflow_document(
                workflow_text, workflow_path
            )
            mutate(candidate)
            with self.subTest(context=name):
                self.assertFalse(
                    workflow_installation_preflight.freshness_execution_context_is_bash(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        contract_job_text = "\n".join(
            step["run"]
            for step in workflow_document["jobs"]["audit"]["steps"]
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        )
        issue_id = "$" + "{issue_numbers[0]}"
        self.assertTrue(
            workflow_installation_preflight.freshness_shell_definitions_are_safe(
                contract_job_text
            )
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_shell_definitions_are_safe("")
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_issue_options_are_safe(
                ["gh", "issue", "reopen", "1"], 1, "reopen"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_issue_options_are_safe(
                ["gh", "issue", "close"], 1, "close"
            )
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_issue_options_are_safe(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo=repo",
                    "--title=title",
                    "--body-file=report.md",
                ],
                1,
                "create",
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_issue_options_are_safe(
                [
                    "gh",
                    "issue",
                    "edit",
                    "1",
                    "--repo",
                    "repo",
                    "--title",
                    "one",
                    "--title",
                    "two",
                ],
                1,
                "edit",
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_issue_options_are_safe(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    "repo",
                    "--title=",
                    "--body-file",
                    "report.md",
                ],
                1,
                "create",
            )
        )
        for definition in (
            "gh() { return 1; }",
            "gh ( ) { return 1; }",
            "function gh { return 1; }",
            "alias gh='echo shadowed'",
            "set-alias gh echo",
            "declare -fx gh",
            "PATH=/tmp/fake:$PATH",
            "export PATH",
            "BASH_ENV=/tmp/fake.sh",
            "ENV=/tmp/fake.sh",
            "hash -p /tmp/fake/gh gh",
            "read CHECKER_EXIT",
            "mapfile -t CHECKER_EXIT <<< 0",
            "printf -v CHECKER_EXIT 0",
            "typeset CHECKER_EXIT",
            "printf 'PATH=/tmp/fake\\n' >> $GITHUB_ENV",
            "printf '/tmp/fake\\n' >> $GITHUB_PATH",
            "title='${{ secrets.TOP_SECRET }}'",
            "printf '%s\\n' \"$GH_TOKEN\"",
            "secret_copy=$GITHUB_TOKEN",
            "gh auth token",
            "gh issue list",
            "grep secret /etc/passwd",
            "mapfile -t other <<< value",
            "printf '%s' \"${!secret_name}\"",
            "printf '%n' CHECKER_EXIT",
            "printf '%s' \"${CHECKER_EXIT:=0}\"",
            "fmt='%n'\nprintf \"$fmt\" CHECKER_EXIT",
            "printf %$fmt CHECKER_EXIT",
            'printf -v "$target" 0',
            "printf --",
            "printf `format` value",
            "exit 2",
            'gh issue close "${issue_numbers[0]}" --repo repo --comment clean --delete-branch',
            "gh issue create --repo repo --title title --body-file report.md --project 1",
            'gh issue create --repo repo --title "$UNTRUSTED_TITLE" --body-file report.md',
        ):
            with self.subTest(shell_definition=definition):
                self.assertFalse(
                    workflow_installation_preflight.freshness_shell_definitions_are_safe(
                        definition
                    )
                )
        self.assertFalse(
            workflow_installation_preflight.freshness_shell_definitions_are_safe(
                "echo 'unterminated"
            )
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_checker_result_controls_reconciliation(
                contract_job_text
            )
        )
        create_command = (
            "gh issue create \\\n"
            '    --repo "github.com/$GITHUB_REPOSITORY" \\\n'
            '    --title "$title" \\\n'
            '    --body-file "$RUNNER_TEMP/freshness.md"'
        )
        duplicate_create = contract_job_text.replace(
            create_command, create_command + "; " + create_command, 1
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_controls_reconciliation(
                duplicate_create
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_controls_reconciliation(
                contract_job_text + "\ngh --hostname github.com issue close 1"
            )
        )
        checker_flow_cases = (
            (
                "missing clean status",
                contract_job_text.replace(
                    "if [[ \"$CHECKER_EXIT\" == '0' ]]; then",
                    "if true; then",
                    1,
                ),
            ),
            (
                "missing stale status",
                contract_job_text.replace(
                    "if [[ \"$CHECKER_EXIT\" != '0' ]]; then",
                    "if true; then",
                    1,
                ),
            ),
            (
                "marker check after clean branch",
                contract_job_text.replace(
                    'grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"\n',
                    "",
                    1,
                ).replace(
                    "if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n",
                    "if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n"
                    '  grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"\n',
                    1,
                ),
            ),
            (
                "clean close missing",
                contract_job_text.replace(
                    f'gh issue close "{issue_id}"',
                    "echo closed",
                    1,
                ),
            ),
            (
                "clean exit missing",
                contract_job_text.replace("exit 0\n", "", 1),
            ),
            (
                "stale failure exit missing",
                contract_job_text.replace(
                    "if [[ \"$CHECKER_EXIT\" != '0' ]]; then\n  exit 1\nfi\n",
                    "",
                    1,
                ),
            ),
            (
                "ambiguous issue command",
                contract_job_text.replace(
                    f'gh issue close "{issue_id}"',
                    f'gh --hostname github.com issue close "{issue_id}"',
                    1,
                ),
            ),
            ("incomplete issue command", contract_job_text + "\ngh issue"),
            ("malformed shell", contract_job_text + "\necho 'unterminated"),
            (
                "close outside clean branch",
                contract_job_text.replace("gh issue close", "echo closed", 1).replace(
                    "  exit 0\nfi\ngrep -Fq",
                    "  exit 0\nfi\ngh issue close 1\ngrep -Fq",
                    1,
                ),
            ),
            ("unmatched shell block", contract_job_text + "\nif true"),
            ("unsupported issue mutation", contract_job_text + "\ngh issue reopen 1"),
            ("missing issue argument", contract_job_text + "\ngh issue close"),
            (
                "late title assignment",
                contract_job_text.replace(
                    "title='Repository freshness update required'\n", "", 1
                )
                + "\ntitle='Repository freshness update required'\n",
            ),
            (
                "title overwritten",
                contract_job_text.replace(
                    "title='Repository freshness update required'\n",
                    "title='Repository freshness update required'\ntitle=attacker\n",
                    1,
                ),
            ),
            (
                "title assignment missing",
                contract_job_text.replace(
                    "title='Repository freshness update required'\n", "", 1
                ),
            ),
        )
        for name, command in checker_flow_cases:
            with self.subTest(checker_flow=name):
                self.assertFalse(
                    workflow_installation_preflight.freshness_checker_result_controls_reconciliation(
                        command
                    )
                )
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
        self.assertTrue(
            workflow_installation_preflight.has_embedded_command(
                ["echo", r"C:\Program Files\gh.exe issue close 1"]
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_dynamic_shell_executor(["${{"])
        )
        self.assertFalse(
            workflow_installation_preflight.has_dynamic_shell_executor(["$UPPER"])
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
        self.assertEqual(
            workflow_installation_preflight.issue_mutation_command_blocks("gh issue"),
            [("__invalid__", [])],
        )
        self.assertEqual(
            workflow_installation_preflight.issue_mutation_command_blocks(
                "echo gh issue create"
            ),
            [("__invalid__", [])],
        )
        self.assertTrue(
            workflow_installation_preflight.has_issue_body_file_reconciliation_for_files(
                "gh issue create --repo r --title t --body-file report.md",
                {"report.md"},
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_issue_body_file_reconciliation_for_files(
                "gh issue create --repo r --title t --body-file other.md",
                {"report.md"},
            )
        )
        self.assertTrue(
            workflow_installation_preflight.has_issue_body_file_reconciliation(
                "gh --repo r issue create --title t --body-file report.md"
            )
        )
        self.assertTrue(
            workflow_installation_preflight.has_issue_body_file_reconciliation(
                "gh --repo=r issue create --title t --body-file report.md"
            )
        )
        for command in (
            "/usr/bin/gh issue create --repo r --title t --body-file report.md",
            "gh.exe issue create --repo r --title t --body-file report.md",
        ):
            with self.subTest(command=command):
                self.assertFalse(
                    workflow_installation_preflight.has_issue_body_file_reconciliation(
                        command
                    )
                )
        for command in (
            "$command issue create --repo r --title t --body-file report.md",
            "Start-Process gh -ArgumentList 'issue create --repo r --title t --body-file report.md'",
            "curl -X POST https://api.github.com/repos/r/issues",
            "Invoke-RestMethod -Method Post -Uri https://api.github.com/repos/r/issues",
            "irm -Method Post https://api.github.com/repos/r/issues",
        ):
            with self.subTest(command=command):
                self.assertFalse(
                    workflow_installation_preflight.has_issue_body_file_reconciliation(
                        command
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
        self.assertFalse(workflow_installation_preflight.has_dynamic_shell_executor([]))
        for tokens, expected in (
            ([], False),
            (["FOO=bar", "cd", "other"], True),
            (["1=bad", "cd", "other"], True),
            (["FOO=bar"], False),
            (["if", "cd", "other"], True),
            (["Set-Location", "other"], True),
        ):
            with self.subTest(tokens=tokens):
                self.assertEqual(
                    workflow_installation_preflight.has_directory_change_command(
                        tokens
                    ),
                    expected,
                )
        self.assertTrue(
            workflow_installation_preflight.has_dynamic_shell_executor(
                ["env", "--", "bash"]
            )
        )
        self.assertTrue(
            workflow_installation_preflight.has_dynamic_shell_executor(["env"])
        )
        self.assertTrue(
            workflow_installation_preflight.has_dynamic_shell_executor(["source"])
        )
        self.assertTrue(
            workflow_installation_preflight.has_dynamic_shell_executor(["."])
        )
        self.assertFalse(
            workflow_installation_preflight.has_dynamic_shell_executor(
                ["1=bad", "bash"]
            )
        )
        self.assertIsNone(
            workflow_installation_preflight.issue_subcommand_positions(
                ["gh", "issue", "create", "gh", "issue", "close"]
            )
        )
        self.assertEqual(
            workflow_installation_preflight.issue_subcommand_positions(
                ["/usr/bin/gh", "issue", "create"]
            ),
            (1,),
        )
        self.assertEqual(
            workflow_installation_preflight.issue_subcommand_positions(
                ["gh.exe", "issue", "create"]
            ),
            (1,),
        )
        self.assertEqual(
            workflow_installation_preflight.issue_mutation_command_blocks(
                "gh issue create gh issue close"
            ),
            [("__invalid__", [])],
        )
        audit_command = (
            "python scripts/audit_freshness.py --repository-root . "
            "--json-output report.json --markdown-output report.md"
        )
        self.assertTrue(
            workflow_installation_preflight.has_freshness_audit_invocation(
                audit_command
            )
        )
        self.assertEqual(
            workflow_installation_preflight.freshness_audit_markdown_outputs(
                audit_command + " --tracker-registry .github/freshness-trackers.json"
            ),
            {"report.md"},
        )
        self.assertIsNone(
            workflow_installation_preflight.freshness_audit_markdown_outputs(
                audit_command.replace("--repository-root .", "--repository-root other")
            )
        )
        self.assertIsNone(
            workflow_installation_preflight.freshness_audit_markdown_outputs(
                audit_command + " --tracker-registry other.json"
            )
        )
        self.assertIsNone(
            workflow_installation_preflight.freshness_audit_markdown_outputs(
                "cd other\n" + audit_command
            )
        )
        self.assertIsNone(
            workflow_installation_preflight.freshness_audit_markdown_outputs(
                audit_command.replace("--json-output report.json", "--json-output '")
            )
        )
        self.assertIsNone(
            workflow_installation_preflight.freshness_audit_markdown_outputs(
                audit_command + " --unexpected ignored"
            )
        )
        jq_expression = (
            '.[] | select(.pull_request == null) | select((.body // "") | '
            'contains("<!-- repo-scaffold-freshness-audit -->")) | .number'
        )
        api_lookup = (
            "gh api --hostname github.com --paginate "
            '"repos/$GITHUB_REPOSITORY/issues?state=open&per_page=100" '
            f"--jq '{jq_expression}'"
        )
        self.assertTrue(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace(
                    "repos/$GITHUB_REPOSITORY", "repos/attacker/repository"
                )
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace("gh api", "gh --hostname github.com api")
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace("gh api", "echo gh api")
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace("--hostname github.com ", "")
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace("github.com", "ghe.example.com")
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace("--hostname github.com", "github.com --hostname")
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace("--paginate ", "")
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace(
                    "state=open&per_page=100", "state=closed&per_page=100"
                )
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace(f"--jq '{jq_expression}'", "--jq '.number'")
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace(
                    f"--jq '{jq_expression}'", f"--jq '{jq_expression}, 999'"
                )
            )
        )
        for option in ("--slurp", "--include", "GET"):
            with self.subTest(option=option):
                self.assertFalse(
                    workflow_installation_preflight.has_freshness_repository_api_reads(
                        api_lookup.replace("--paginate ", f"--paginate {option} ")
                    )
                )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace("--paginate ", "-- ")
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup + " --method"
            )
        )
        for method in (
            "--method HEAD",
            "--method=HEAD",
            "-XHEAD",
            "-X HEAD",
        ):
            with self.subTest(method=method):
                self.assertFalse(
                    workflow_installation_preflight.has_freshness_repository_api_reads(
                        api_lookup.replace("--paginate ", f"--paginate {method} ")
                    )
                )
        for method in ("--method GET", "--method=GET", "-XGET", "-X GET"):
            with self.subTest(method=method):
                self.assertTrue(
                    workflow_installation_preflight.has_freshness_repository_api_reads(
                        api_lookup.replace("--paginate ", f"--paginate {method} ")
                    )
                )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                api_lookup.replace(
                    "--paginate ", "--paginate --method GET --method GET "
                )
            )
        )
        mutation_command = (
            'gh issue create --repo "github.com/$GITHUB_REPOSITORY" '
            "--title reminder --body-file report.md"
        )
        ordered_commands = "\n".join((audit_command, api_lookup, mutation_command))
        self.assertTrue(
            workflow_installation_preflight.freshness_command_order_is_valid(
                ordered_commands
            )
        )
        for operator in ("&", "|", "|&", "&&", "||"):
            with self.subTest(operator=operator):
                self.assertFalse(
                    workflow_installation_preflight.freshness_command_order_is_valid(
                        f" {operator} ".join(
                            (audit_command, api_lookup, mutation_command)
                        )
                    )
                )
        bound_api_lookup = (
            "issue_numbers_output=$(\n  "
            + api_lookup
            + '\n)\nmapfile -t issue_numbers <<< "$issue_numbers_output"'
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_api_result_is_consumed(
                bound_api_lookup
            )
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_api_result_controls_issue_selection(
                bound_api_lookup + '\ngh issue edit "${issue_numbers[0]}" --repo r '
                "--body-file report.md"
            )
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_api_result_controls_issue_selection(
                'output=$(gh api)\ngh issue edit "$output" --repo r '
                "--body-file report.md"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_api_result_controls_issue_selection(
                'unrelated=attacker\noutput=$(gh api)\necho "$output"\n'
                'gh issue edit "$unrelated" --repo r --body-file report.md'
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_api_result_controls_issue_selection(
                bound_api_lookup
                + '\ngh issue edit "${issue_numbers[0]}" --repo r --body-file report.md\n'
                + "gh issue edit 999 --repo r --body-file report.md"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_api_result_controls_issue_selection(
                bound_api_lookup + "\ngh issue close"
            )
        )
        for command in (
            'output=$(gh api)\necho "$output"\n'
            "gh issue create --repo r --title t --body-file report.md",
            'output=$(gh api)\nif [[ -n "$output" ]]; then :; fi\n'
            "gh issue create --repo r --title t --body-file report.md",
            'output=$(gh api)\nmapfile -t ids <<< "$other"\n'
            'gh issue edit "${ids[0]}" --repo r --body-file report.md',
            'output=$(gh api)\nmapfile -t 1bad <<< "$output"\n'
            'gh issue edit "${1bad[0]}" --repo r --body-file report.md',
            'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
            'ids=(999)\ngh issue edit "${ids[0]}" --repo r --body-file report.md',
            'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
            'unset ids\ngh issue edit "${ids[0]}" --repo r --body-file report.md',
            'output=$(gh api)\necho "$output"\ngh issue',
        ):
            with self.subTest(command=command):
                self.assertFalse(
                    workflow_installation_preflight.freshness_api_result_controls_issue_selection(
                        command
                    )
                )
        flow_cases = (
            (
                "plain variable suffix is rejected",
                'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
                'gh issue edit "$ids_suffix" --repo r --body-file report.md',
                False,
            ),
            (
                "plain variable suffix is not an Issue argument",
                'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
                'gh issue edit "$ids-suffix" --repo r --body-file report.md',
                False,
            ),
            (
                "malformed later shell line",
                "output=$(gh api)\necho 'open\nclosed'\n$output",
                False,
            ),
            (
                "malformed assignment",
                "output=$(gh api 'unterminated",
                False,
            ),
            (
                "result reassigned after an early reference",
                'output=$(gh api)\necho "$output"\noutput=bad\n'
                'gh issue edit "$output" --repo r --body-file report.md',
                False,
            ),
            (
                "duplicate here-string redirects",
                'output=$(gh api)\nmapfile -t ids <<< "$output" <<< "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "here-string has no target",
                'output=$(gh api)\nmapfile <<< "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "here-string has no source",
                'output=$(gh api)\necho "$output"\nmapfile -t ids <<<\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "collection source is unrelated",
                'output=$(gh api)\necho "$output"\nmapfile -t ids <<< "$other"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "invalid collection target",
                'output=$(gh api)\nmapfile -t 1bad <<< "$output"\n'
                'gh issue edit "${1bad[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "collection precedes lookup",
                'mapfile -t ids <<< "$output"\noutput=$(gh api)\necho "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "lookup source is reassigned",
                'output=$(gh api)\necho "$output"\noutput=bad\n'
                'mapfile -t ids <<< "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "unsupported global issue option",
                'output=$(gh api)\necho "$output"\n'
                'gh --hostname github.com issue edit "$output" --repo r '
                "--body-file report.md",
                False,
            ),
            (
                "read-only issue command",
                'output=$(gh api)\necho "$output"\ngh issue list',
                False,
            ),
            (
                "mutation without issue argument",
                'output=$(gh api)\necho "$output"\ngh issue edit',
                False,
            ),
            (
                "readarray collection",
                'output=$(gh api)\nreadarray -t ids <<< "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                True,
            ),
        )
        for name, command, expected in flow_cases:
            with self.subTest(flow_case=name):
                self.assertEqual(
                    workflow_installation_preflight.freshness_api_result_controls_issue_selection(
                        command
                    ),
                    expected,
                )
        self.assertFalse(
            workflow_installation_preflight.freshness_api_result_is_consumed(api_lookup)
        )
        for command, expected in (
            ("output=$(echo ready)", False),
            ("output=$(", False),
            ("output=$()", False),
            ("output=$ echo ready", False),
            ("bad-name=$(gh api)", False),
            ("output=$(gh api 'unterminated", False),
            ("output=$(gh api)\noutput=''\n$output", False),
            ("output=$(gh api)\n${output}", True),
            ("output=$(gh api)\n$output-suffix", True),
            ("output=$(gh api)\n$output_suffix", False),
            ("output=$(gh api)\n${outputevil}", False),
            ("output=$(echo $(gh api) )\n$output", True),
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    workflow_installation_preflight.freshness_api_result_is_consumed(
                        command
                    ),
                    expected,
                )
        for commands in (
            (mutation_command, audit_command, api_lookup),
            (api_lookup, audit_command, mutation_command),
            (audit_command, mutation_command, api_lookup),
        ):
            with self.subTest(commands=commands):
                self.assertFalse(
                    workflow_installation_preflight.freshness_command_order_is_valid(
                        "\n".join(commands)
                    )
                )
        self.assertFalse(
            workflow_installation_preflight.freshness_command_order_is_valid(
                "gh issue 'unterminated"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_command_order_is_valid(
                "gh issue create gh issue close"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_command_order_is_valid(
                "echo 'open\nclosed'"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                "gh api --method POST repos/$GITHUB_REPOSITORY/issues"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                "GITHUB_REPOSITORY=attacker/repository "
                "gh api --hostname github.com --paginate "
                '"repos/$GITHUB_REPOSITORY/issues?state=open&per_page=100"'
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                "gh api 'unterminated"
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                "echo ready"
            )
        )
        self.assertTrue(
            workflow_installation_preflight.has_freshness_repository_api_reads(
                "echo ready", require_lookup=False
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_freshness_audit_invocation("echo ready")
        )
        self.assertIsNone(
            workflow_installation_preflight.freshness_audit_markdown_outputs(
                audit_command + " " + audit_command
            )
        )
        self.assertEqual(
            workflow_installation_preflight.option_values(
                ["--repo", "owner/repository"], "--repo"
            ),
            ("owner/repository",),
        )
        self.assertEqual(
            workflow_installation_preflight.option_values(
                ["--repo=owner/repository"], "--repo"
            ),
            ("owner/repository",),
        )
        self.assertIsNone(
            workflow_installation_preflight.option_values(["--repo="], "--repo")
        )
        self.assertEqual(
            workflow_installation_preflight.option_values(["--other"], "--repo"),
            (),
        )
        self.assertEqual(
            workflow_installation_preflight.option_values(
                ["--repo", "one", "--repo", "two"], "--repo"
            ),
            ("one", "two"),
        )
        self.assertEqual(
            workflow_installation_preflight.option_values(
                ["--repo", "one", "--", "--repo", "two"], "--repo"
            ),
            ("one",),
        )
        self.assertTrue(
            workflow_installation_preflight.has_repository_root_working_directory(
                {
                    "jobs": {
                        "audit": {
                            "defaults": {"run": {"working-directory": "."}},
                            "steps": [{"working-directory": "."}],
                        }
                    }
                }
            )
        )
        working_directory_documents: tuple[dict[str, Any], ...] = (
            {},
            {"jobs": []},
            {"jobs": {"audit": []}},
            {"jobs": {"audit": {"defaults": []}}},
            {"jobs": {"audit": {"defaults": {"run": []}}}},
            {"jobs": {"audit": {"defaults": {}, "steps": {}}}},
            {"jobs": {"audit": {"defaults": {"run": {"working-directory": "other"}}}}},
            {"jobs": {"audit": {"steps": [{"working-directory": "other"}]}}},
        )
        for document in working_directory_documents:
            with self.subTest(document=document):
                self.assertFalse(
                    workflow_installation_preflight.has_repository_root_working_directory(
                        document
                    )
                )
        self.assertFalse(
            workflow_installation_preflight.option_has_one_value(
                ["--repo", "one", "--repo", "two"], "--repo"
            )
        )
        self.assertTrue(
            workflow_installation_preflight.has_direct_freshness_jobs(
                {"jobs": {"audit": {"steps": []}}}
            )
        )
        self.assertFalse(
            workflow_installation_preflight.has_direct_freshness_jobs(
                {
                    "jobs": {
                        "audit": {"steps": []},
                        "hidden": {"uses": "./.github/workflows/reusable.yml"},
                    }
                }
            )
        )
        self.assertTrue(
            workflow_installation_preflight.has_freshness_repository_context(
                {"jobs": {"audit": {"steps": []}}}
            )
        )
        self.assertTrue(
            workflow_installation_preflight.has_freshness_repository_context(
                {"jobs": {"audit": {"env": {}}}}
            )
        )
        for document in (
            {"jobs": []},
            {"jobs": {"audit": []}},
            {"jobs": {"audit": {"env": []}}},
            {"jobs": {"audit": {"env": {}, "steps": {}}}},
            {
                "jobs": {
                    "audit": {
                        "env": {"GITHUB_REPOSITORY": "attacker/repository"},
                        "steps": [],
                    }
                }
            },
            {
                "jobs": {
                    "audit": {
                        "steps": [{"env": {"GITHUB_REPOSITORY": "attacker/repository"}}]
                    }
                }
            },
            {"jobs": {"audit": {"steps": [{"env": []}]}}},
            {"jobs": {"audit": {"steps": [[]]}}},
        ):
            with self.subTest(document=document):
                self.assertFalse(
                    workflow_installation_preflight.has_freshness_repository_context(
                        document
                    )
                )
        self.assertFalse(
            workflow_installation_preflight.has_issue_body_file_reconciliation(
                "gh issue create -- --repo r --title t --body-file report.md"
            )
        )
        for tokens, expected in (
            (["gh", "api", "repos/example/issues"], False),
            (["/usr/bin/gh", "api", "repos/example/issues", "-f", "title=x"], True),
            (["gh", "api", "repos/example/issues", "-f", "title=x"], True),
            (
                ["gh", "api", "repos/example/issues", "--method", "GET", "-f", "q=x"],
                False,
            ),
            (["gh", "api", "repos/example/issues", "--method", "POST"], True),
            (["gh", "api", "repos/example/issues", "--method="], True),
            (["gh", "api", "repos/example/issues", "-XDELETE"], True),
            (["gh", "api", "repos/example/issues", "-X", "HEAD"], False),
            (["gh", "api", "repos/example/issues", "--method"], True),
        ):
            with self.subTest(tokens=tokens):
                self.assertEqual(
                    workflow_installation_preflight.github_api_is_mutation(tokens, 0),
                    expected,
                )
        for tokens, expected in (
            (["curl", "--fail", "https://example.test"], False),
            (["curl", "-x", "proxy", "https://example.test"], False),
            (["curl", "-f", "https://example.test"], False),
            (["curl", "-X", "GET", "https://example.test"], False),
            (["curl", "-XPOST", "https://example.test"], True),
            (["curl", "-d", "title=x", "https://example.test"], True),
            (["curl", "-F", "title=x", "https://example.test"], True),
            (["curl", "-T", "payload", "https://example.test"], True),
            (["curl", "--upload-file=payload", "https://example.test"], True),
            (["curl", "--json", '{"title":"x"}', "https://example.test"], True),
            (["curl", "-g", "-d", "q=x", "https://example.test"], True),
            (["curl", "-i", "-d", "q=x", "https://example.test"], True),
            (["curl", "-G", "-d", "q=x", "https://example.test"], False),
            (["curl", "-I", "-d", "q=x", "https://example.test"], False),
            (["curl", "--get", "--data", "q=x", "https://example.test"], False),
            (["curl", "--head", "https://example.test"], False),
            (["curl", "--method=POST", "https://example.test"], True),
            (["curl", "-X"], True),
            (["Invoke-RestMethod", "-Method=Post", "https://example.test"], True),
            (["wget", "--post-data=x", "https://example.test"], True),
        ):
            with self.subTest(tokens=tokens):
                self.assertEqual(
                    workflow_installation_preflight.network_client_is_mutation(tokens),
                    expected,
                )
        self.assertTrue(
            workflow_installation_preflight.has_least_privileged_freshness_permissions(
                {
                    "permissions": {"contents": "read", "issues": "write"},
                    "jobs": {
                        "audit": {
                            "permissions": {"contents": "read", "issues": "write"}
                        }
                    },
                }
            )
        )
        permission_documents: tuple[dict[str, Any], ...] = (
            {"permissions": {"issues": "write"}, "jobs": {}},
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": [],
            },
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": "not-a-job"},
            },
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": {"permissions": "write-all"}},
            },
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": {"permissions": {"contents": "write"}}},
            },
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": {"permissions": {"issues": "admin"}}},
            },
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": {"permissions": {"actions": "read"}}},
            },
        )
        for document in permission_documents:
            with self.subTest(document=document):
                self.assertFalse(
                    workflow_installation_preflight.has_least_privileged_freshness_permissions(
                        document
                    )
                )
        workflow_permissions = {"permissions": {"contents": "read", "issues": "write"}}
        self.assertTrue(
            workflow_installation_preflight.job_effective_contents_read(
                workflow_permissions, {}
            )
        )
        self.assertTrue(
            workflow_installation_preflight.job_effective_contents_read(
                workflow_permissions,
                {"permissions": {"contents": "read", "issues": "write"}},
            )
        )
        self.assertFalse(
            workflow_installation_preflight.job_effective_contents_read(
                workflow_permissions, {"permissions": {"issues": "write"}}
            )
        )

    def test_freshness_checker_status_binding_is_derived(self) -> None:
        workflow_path = PLUGIN_ROOT / ".github/workflows/freshness.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        workflow = workflow_installation_preflight.workflow_document(
            workflow_text, workflow_path
        )
        job = workflow["jobs"]["audit"]
        audit_step = next(step for step in job["steps"] if step.get("id") == "audit")
        binding_step = next(
            step
            for step in job["steps"]
            if isinstance(step.get("env"), dict) and "CHECKER_EXIT" in step["env"]
        )
        audit_run = audit_step["run"]
        binding_run = binding_step["run"]
        self.assertTrue(
            workflow_installation_preflight.freshness_checker_result_output_is_safe(
                audit_run
            )
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                workflow, job
            )
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_authentication_bindings_are_safe(
                workflow, job
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_authentication_bindings_are_safe(
                None, job
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_authentication_bindings_are_safe(
                workflow, None
            )
        )
        for scope in ("workflow", "job"):
            candidate = workflow_installation_preflight.workflow_document(
                workflow_text, workflow_path
            )
            target = candidate if scope == "workflow" else candidate["jobs"]["audit"]
            target["env"] = {"GH_TOKEN": "attacker"}
            with self.subTest(authentication_scope=scope):
                self.assertFalse(
                    workflow_installation_preflight.freshness_authentication_bindings_are_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        for variable in ("GIT_SSH_COMMAND", "LD_PRELOAD", "GH_CONFIG_DIR", "HOME"):
            for scope in ("workflow", "job"):
                candidate = workflow_installation_preflight.workflow_document(
                    workflow_text, workflow_path
                )
                target = (
                    candidate if scope == "workflow" else candidate["jobs"]["audit"]
                )
                target["env"] = {variable: "attacker"}
                with self.subTest(
                    authentication_scope=scope, unexpected_environment=variable
                ):
                    self.assertFalse(
                        workflow_installation_preflight.freshness_authentication_bindings_are_safe(
                            candidate, candidate["jobs"]["audit"]
                        )
                    )
            candidate = workflow_installation_preflight.workflow_document(
                workflow_text, workflow_path
            )
            audit_step = candidate["jobs"]["audit"]["steps"][2]
            audit_step["env"][variable] = "attacker"
            with self.subTest(
                authentication_step="audit", unexpected_environment=variable
            ):
                self.assertFalse(
                    workflow_installation_preflight.freshness_authentication_bindings_are_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        auth_cases = (
            (
                "wrong audit token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][2]["env"].update(
                    {"GITHUB_TOKEN": "attacker"}
                ),
            ),
            (
                "wrong issue token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][4]["env"].update(
                    {"GH_TOKEN": "attacker"}
                ),
            ),
            (
                "missing audit token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][2]["env"].pop(
                    "GITHUB_TOKEN"
                ),
            ),
            (
                "missing issue token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][4]["env"].pop(
                    "GH_TOKEN"
                ),
            ),
            (
                "audit uses issue token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][2]["env"].update(
                    {"GH_TOKEN": "${{ github.token }}"}
                ),
            ),
            (
                "issue uses audit token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][4]["env"].update(
                    {"GITHUB_TOKEN": "${{ github.token }}"}
                ),
            ),
            (
                "invalid token step environment",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0].update(
                    {"env": []}
                ),
            ),
            (
                "invalid token steps",
                lambda candidate: candidate["jobs"]["audit"].update({"steps": {}}),
            ),
            (
                "invalid token step item",
                lambda candidate: candidate["jobs"]["audit"]["steps"].append(None),
            ),
        )
        for name, mutate in auth_cases:
            candidate = workflow_installation_preflight.workflow_document(
                workflow_text, workflow_path
            )
            mutate(candidate)
            with self.subTest(authentication_case=name):
                self.assertFalse(
                    workflow_installation_preflight.freshness_authentication_bindings_are_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )

        output_cases = (
            ("missing audit result", audit_run.replace("checker_exit=$?\n", "", 1)),
            ("missing errexit disable", audit_run.replace("set +e\n", "", 1)),
            ("missing errexit restore", audit_run.replace("set -e\n", "", 1)),
            (
                "errexit restored too early",
                audit_run.replace("set +e\n", "set -e\n", 1),
            ),
            (
                "non-adjacent audit result",
                audit_run.replace(
                    "checker_exit=$?\n", "echo captured\nchecker_exit=$?\n", 1
                ),
            ),
            (
                "constant audit result",
                audit_run.replace('"$checker_exit" >>', '"0" >>', 1),
            ),
            (
                "negated audit result",
                audit_run.replace(
                    "python scripts/audit_freshness.py",
                    "! python scripts/audit_freshness.py",
                    1,
                ),
            ),
            (
                "additional output writer",
                audit_run.replace(
                    'printf \'checker_exit=%s\\n\' "$checker_exit" >> "$GITHUB_OUTPUT"',
                    "printf 'other=0\\nchecker_exit=0\\n' >> \"$GITHUB_OUTPUT\"\n"
                    'printf \'checker_exit=%s\\n\' "$checker_exit" >> "$GITHUB_OUTPUT"',
                    1,
                ),
            ),
            (
                "missing output",
                audit_run.replace(
                    'printf \'checker_exit=%s\\n\' "$checker_exit" >> "$GITHUB_OUTPUT"\n',
                    "",
                    1,
                ),
            ),
            (
                "unset audit result",
                audit_run.replace(
                    "checker_exit=$?\n", "checker_exit=$?\nunset checker_exit\n", 1
                ),
            ),
            (
                "read audit result",
                audit_run.replace(
                    "checker_exit=$?\n", "checker_exit=$?\nread checker_exit\n", 1
                ),
            ),
            (
                "printf audit result",
                audit_run.replace(
                    "checker_exit=$?\n",
                    "checker_exit=$?\nprintf -v checker_exit 0\n",
                    1,
                ),
            ),
            (
                "invalid fallback result",
                audit_run.replace("checker_exit=2\n", "checker_exit=0\n", 1),
            ),
            (
                "fallback outside guard",
                audit_run.replace("checker_exit=2\n", "", 1).replace(
                    "printf 'checker_exit=%s\\n' \"$checker_exit\"",
                    "checker_exit=2\nprintf 'checker_exit=%s\\n' \"$checker_exit\"",
                    1,
                ),
            ),
            (
                "multiple fallback guards",
                audit_run.replace(
                    'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n',
                    'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n'
                    'if [[ ! -f "$RUNNER_TEMP/other.md" ]]; then\n'
                    "fi\n",
                    1,
                ),
            ),
            (
                "fallback without guard",
                audit_run.replace(
                    'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n',
                    'if [[ -f "$RUNNER_TEMP/freshness.md" ]]; then\n',
                    1,
                ),
            ),
            (
                "fallback path mismatch",
                audit_run.replace(
                    'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n',
                    'if [[ ! -f "$RUNNER_TEMP/other.md" ]]; then\n',
                    1,
                ),
            ),
            (
                "fallback marker missing",
                audit_run.replace(
                    "<!-- repo-scaffold-freshness-audit -->",
                    "fallback report",
                    1,
                ),
            ),
            (
                "fallback writer is not printf",
                audit_run.replace("printf '%s\\n' \\\n", "echo \\\n", 1),
            ),
            (
                "output published inside fallback",
                audit_run.replace(
                    "fi\nprintf 'checker_exit=%s\\n' \"$checker_exit\"",
                    "printf 'checker_exit=%s\\n' \"$checker_exit\"\nfi",
                    1,
                ),
            ),
            (
                "fallback status before report",
                audit_run.replace(
                    "  printf '%s\\n' \\\n",
                    "  checker_exit=2\n  printf '%s\\n' \\\n",
                    1,
                ),
            ),
            ("malformed shell", audit_run + "echo 'unterminated"),
        )
        for name, command in output_cases:
            with self.subTest(output_case=name):
                self.assertFalse(
                    workflow_installation_preflight.freshness_checker_result_output_is_safe(
                        command
                    )
                )
        no_fallback = audit_run.replace(
            'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n'
            "  printf '%s\\n' \\\n"
            "    '<!-- repo-scaffold-freshness-audit -->' \\\n"
            "    '# Repository freshness report' \\\n"
            "    '' \\\n"
            "    'The checker failed before it could produce a report. Inspect this workflow run.' \\\n"
            '    > "$RUNNER_TEMP/freshness.md"\n'
            "  checker_exit=2\n"
            "fi\n",
            "",
            1,
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_output_is_safe(
                no_fallback
            )
        )
        self.assertIsNone(
            workflow_installation_preflight.freshness_audit_markdown_outputs(
                audit_run.replace(
                    '--json-output "$RUNNER_TEMP/freshness.json"',
                    '--json-output "$RUNNER_TEMP/freshness.json" '
                    '--json-output "$RUNNER_TEMP/other.json"',
                    1,
                )
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_shell_definitions_are_safe(
                "CHECKER_EXIT=0"
            )
        )
        with (
            mock.patch.object(
                workflow_installation_preflight,
                "shell_command_segments",
                return_value=[["echo", "ready"]],
            ),
            mock.patch.object(
                workflow_installation_preflight.shlex,
                "shlex",
                side_effect=ValueError("malformed"),
            ),
        ):
            self.assertFalse(
                workflow_installation_preflight.freshness_shell_definitions_are_safe(
                    "ignored"
                )
            )

        def fresh_workflow() -> Any:
            return workflow_installation_preflight.workflow_document(
                workflow_text, workflow_path
            )

        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                None, job
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                workflow, None
            )
        )
        for scope in ("workflow", "job"):
            candidate = fresh_workflow()
            target = candidate if scope == "workflow" else candidate["jobs"]["audit"]
            target["env"] = []
            with self.subTest(invalid_environment=scope):
                self.assertFalse(
                    workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        candidate = fresh_workflow()
        candidate["env"] = {"CHECKER_EXIT": "0"}
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["env"] = {"CHECKER_EXIT": "0"}
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        for variable in ("PATH", "BASH_ENV", "ENV"):
            candidate = fresh_workflow()
            candidate["jobs"]["audit"]["steps"][0]["env"] = {variable: "/tmp/fake"}
            with self.subTest(protected_environment=variable):
                self.assertFalse(
                    workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        for variable in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "RUNNER_TEMP"):
            candidate = fresh_workflow()
            candidate["jobs"]["audit"]["steps"][4]["env"] = {variable: "/tmp/fake"}
            with self.subTest(runner_file_environment=variable):
                self.assertFalse(
                    workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["env"]["PATH"] = "/tmp/fake"
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        invalid_step_values: tuple[object, ...] = ({}, [None])
        for invalid_steps in invalid_step_values:
            candidate = fresh_workflow()
            candidate["jobs"]["audit"]["steps"] = invalid_steps
            with self.subTest(invalid_steps=invalid_steps):
                self.assertFalse(
                    workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][0]["env"] = []
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["env"]["CHECKER_EXIT"] = "0"
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][0]["id"] = "audit"
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["env"].pop("CHECKER_EXIT")
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][2]["env"] = {
            "CHECKER_EXIT": "${{ steps.audit.outputs.checker_exit }}"
        }
        candidate["jobs"]["audit"]["steps"][4]["env"].pop("CHECKER_EXIT")
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][2]["run"] = None
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["run"] = None
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["run"] = "gh issue create"
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["run"] = binding_run.replace(
            "issue_numbers_output=$(", "issue_numbers_output=", 1
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )

    def test_freshness_contract_rejects_runtime_and_shell_bypasses(self) -> None:
        workflow_path = PLUGIN_ROOT / ".github/workflows/freshness.yml"
        contract_text = workflow_path.read_text(encoding="utf-8")
        contract_workflow = workflow_installation_preflight.workflow_document(
            contract_text, workflow_path
        )
        contract_job = contract_workflow["jobs"]["audit"]

        for name, mutate in (
            (
                "workflow container",
                lambda candidate: candidate.update({"container": "evil:latest"}),
            ),
            (
                "job container",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"container": "evil:latest"}
                ),
            ),
            (
                "job service",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"services": {"evil": {"image": "evil:latest"}}}
                ),
            ),
            (
                "unreviewed action",
                lambda candidate: candidate["jobs"]["audit"]["steps"].append(
                    {"uses": "evil/action@" + "a" * 40}
                ),
            ),
            (
                "unpinned freshness action",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0].update(
                    {"uses": "actions/checkout@main"}
                ),
            ),
            (
                "checkout repository override",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0]["with"].update(
                    {"repository": "attacker/repo"}
                ),
            ),
            (
                "checkout ref override",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0]["with"].update(
                    {"ref": "attacker"}
                ),
            ),
            (
                "setup Python override",
                lambda candidate: candidate["jobs"]["audit"]["steps"][1]["with"].update(
                    {"python-version": "attacker"}
                ),
            ),
        ):
            candidate = workflow_installation_preflight.workflow_document(
                contract_text, workflow_path
            )
            mutate(candidate)
            with self.subTest(runtime_context=name):
                self.assertFalse(
                    workflow_installation_preflight.freshness_execution_context_is_bash(
                        candidate, candidate["jobs"]["audit"]
                    )
                )

        for variable in (
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONSTARTUP",
            "GITHUB_STEP_SUMMARY",
            "GITHUB_STATE",
        ):
            candidate = workflow_installation_preflight.workflow_document(
                contract_text, workflow_path
            )
            candidate["jobs"]["audit"]["steps"][0]["env"] = {variable: "attacker"}
            with self.subTest(protected_environment=variable):
                if variable in {"GITHUB_TOKEN", "GH_TOKEN"}:
                    self.assertFalse(
                        workflow_installation_preflight.freshness_authentication_bindings_are_safe(
                            candidate, candidate["jobs"]["audit"]
                        )
                    )
                else:
                    self.assertFalse(
                        workflow_installation_preflight.freshness_checker_result_binding_is_safe(
                            candidate, candidate["jobs"]["audit"]
                        )
                    )

        audit_step = next(
            step for step in contract_job["steps"] if step.get("id") == "audit"
        )
        audit_run = audit_step["run"]
        for name, replacement in (
            (
                "JSON output outside runner temp",
                ("$RUNNER_TEMP/freshness.json", "report.json"),
            ),
            (
                "Markdown output outside runner temp",
                ("$RUNNER_TEMP/freshness.md", "report.md"),
            ),
        ):
            with self.subTest(report_path=name):
                self.assertFalse(
                    workflow_installation_preflight.freshness_checker_result_output_is_safe(
                        audit_run.replace(*replacement, 1)
                    )
                )

        for definition in (
            "GH_TOKEN=attacker",
            "GITHUB_TOKEN=attacker",
            "export GH_TOKEN=attacker",
            "PYTHONPATH=/tmp/evil",
            "python -c \"import subprocess; subprocess.run(['gh','issue','close','999'])\"",
            "node -e \"require('child_process').execFileSync('gh',['issue','close','999'])\"",
            "awk 'BEGIN { system(\"gh issue close 999\") }'",
            "./mutate_issue",
        ):
            with self.subTest(shell_definition=definition):
                self.assertFalse(
                    workflow_installation_preflight.freshness_shell_definitions_are_safe(
                        definition
                    )
                )

        contract_job_text = "\n".join(
            step["run"]
            for step in contract_job["steps"]
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        )
        binding_step = next(
            step
            for step in contract_job["steps"]
            if isinstance(step.get("env"), dict) and "CHECKER_EXIT" in step["env"]
        )
        binding_run = binding_step["run"]
        self.assertTrue(
            workflow_installation_preflight.freshness_reconciliation_shell_options_are_safe(
                binding_run
            )
        )
        for command in (
            binding_run.replace("set -euo pipefail\n", "", 1),
            binding_run.replace(
                "set -euo pipefail\n", "set +e\nset -euo pipefail\n", 1
            ),
            binding_run.replace("set -euo pipefail", "set -e", 1),
            binding_run.replace(
                "marker='<!-- repo-scaffold-freshness-audit -->'",
                'printf "fake" > "$RUNNER_TEMP/freshness.md"\n'
                "marker='<!-- repo-scaffold-freshness-audit -->'",
                1,
            ),
            "",
            "echo 'unterminated",
        ):
            with self.subTest(reconciliation_options=command):
                self.assertFalse(
                    workflow_installation_preflight.freshness_reconciliation_shell_options_are_safe(
                        command
                    )
                )
        for variable in ("GIT_SSH_COMMAND", "LD_PRELOAD", "GH_CONFIG_DIR", "HOME"):
            command = binding_run.replace(
                "gh api --hostname github.com",
                f"{variable}=/tmp/fake gh api --hostname github.com",
                1,
            )
            with self.subTest(shell_environment=variable):
                self.assertFalse(
                    workflow_installation_preflight.freshness_shell_definitions_are_safe(
                        command
                    )
                )
        self.assertTrue(
            workflow_installation_preflight.freshness_shell_control_flow_is_safe(
                contract_job_text
            )
        )
        expression_text = contract_text.replace(
            "title='Repository freshness update required'",
            "title='${{ secrets.TOP_SECRET }}'",
            1,
        )
        self.assertFalse(
            workflow_installation_preflight.is_freshness_reminder_workflow(
                expression_text, workflow_path
            )
        )
        token_text = contract_text.replace(
            "--comment 'The scheduled freshness audit is clean, so this reminder is closing automatically.'",
            '--comment "$GH_TOKEN"',
            1,
        )
        self.assertFalse(
            workflow_installation_preflight.is_freshness_reminder_workflow(
                token_text, workflow_path
            )
        )
        hidden_job_text = (
            contract_text
            + "\n  hidden:\n"
            + "    steps:\n"
            + "      - run: python -c \"import subprocess; subprocess.run(['gh','issue','close','999'])\"\n"
        )
        self.assertFalse(
            workflow_installation_preflight.is_freshness_reminder_workflow(
                hidden_job_text, workflow_path
            )
        )
        duplicate_guard = (
            "          if (( ${#issue_numbers[@]} > 1 )); then\n"
            "            printf 'Found multiple open freshness reminder issues.\\n' >&2\n"
            "            exit 1\n"
            "          fi\n"
        )
        flow_bypasses = (
            (
                "clean nested condition",
                contract_text.replace(
                    "if (( ${#issue_numbers[@]} == 1 )); then",
                    "if false; then",
                    1,
                ),
            ),
            (
                "stale nested condition",
                contract_text.replace(
                    "if (( ${#issue_numbers[@]} == 1 )); then",
                    "if false; then",
                    2,
                ),
            ),
            (
                "duplicate issue guard missing",
                contract_text.replace(duplicate_guard, "", 1),
            ),
            (
                "duplicate issue guard does not exit",
                contract_text.replace(
                    "            exit 1\n          fi\n",
                    "            :\n          fi\n",
                    1,
                ),
            ),
            (
                "clean cardinality guard missing",
                contract_text.replace(
                    "            if (( ${#issue_numbers[@]} == 1 )); then\n",
                    "",
                    1,
                ),
            ),
            (
                "stale cardinality guard missing",
                contract_text.replace(
                    "          if (( ${#issue_numbers[@]} == 1 )); then\n",
                    "",
                    1,
                ),
            ),
            (
                "marker check uses another variable",
                contract_text.replace(
                    'grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"',
                    'grep -Fq "$title" "$RUNNER_TEMP/freshness.md"',
                    1,
                ),
            ),
            (
                "marker is reassigned",
                contract_text.replace(
                    "          marker='<!-- repo-scaffold-freshness-audit -->'\n",
                    "          marker='<!-- repo-scaffold-freshness-audit -->'\n"
                    "          marker=attacker\n",
                    1,
                ),
            ),
            (
                "unreviewed command substitution",
                contract_text.replace(
                    "          marker='<!-- repo-scaffold-freshness-audit -->'\n",
                    '          printf "%s" "$(./mutate_issue)"\n'
                    "          marker='<!-- repo-scaffold-freshness-audit -->'\n",
                    1,
                ),
            ),
            (
                "loop around mutation",
                contract_text.replace(
                    "            gh issue close",
                    "            for item in; do\n            gh issue close",
                    1,
                ).replace(
                    "            --comment 'The scheduled freshness audit is clean, so this reminder is closing automatically.'",
                    "            --comment 'The scheduled freshness audit is clean, so this reminder is closing automatically.'\n            done",
                    1,
                ),
            ),
            (
                "unmatched closing shell block",
                contract_job_text + "\nfi",
            ),
            (
                "unmatched opening shell block",
                contract_job_text.replace("fi\n", "", 1),
            ),
        )
        for name, candidate_text in flow_bypasses:
            if candidate_text.startswith("set "):
                job_text = candidate_text
            else:
                candidate = workflow_installation_preflight.workflow_document(
                    candidate_text, workflow_path
                )
                job_text = "\n".join(
                    step["run"]
                    for step in candidate["jobs"]["audit"]["steps"]
                    if isinstance(step, dict) and isinstance(step.get("run"), str)
                )
            with self.subTest(flow_bypass=name):
                self.assertFalse(
                    workflow_installation_preflight.freshness_checker_result_controls_reconciliation(
                        job_text
                    )
                )

        lookup = (
            'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
            'gh issue edit "${ids[0]:-999}" --repo r --body-file report.md'
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_api_result_controls_issue_selection(
                lookup
            )
        )
        transformed_lookup = lookup.replace(":-999", "//1/999")
        self.assertFalse(
            workflow_installation_preflight.freshness_api_result_controls_issue_selection(
                transformed_lookup
            )
        )

    def test_freshness_defensive_helpers_and_workflow_shapes_fail_closed(self) -> None:
        action_step_cases: tuple[object, ...] = (
            None,
            {},
            [None],
            [{"uses": 1}],
            [{"uses": "one@two@three"}],
        )
        for steps in action_step_cases:
            with self.subTest(action_steps=steps):
                self.assertFalse(
                    workflow_installation_preflight.freshness_action_steps_are_safe(
                        steps
                    )
                )
        self.assertTrue(
            workflow_installation_preflight.freshness_action_steps_are_safe(
                [{"run": "echo"}]
            )
        )
        for (
            repository,
            reference,
        ) in (
            workflow_installation_preflight.FRESHNESS_REVIEWED_ACTION_REFERENCES.items()
        ):
            with self.subTest(reviewed_action=repository):
                step = {
                    "uses": reference,
                    "with": workflow_installation_preflight.FRESHNESS_ALLOWED_ACTION_INPUTS[
                        repository
                    ],
                }
                self.assertTrue(
                    workflow_installation_preflight.freshness_action_steps_are_safe(
                        [step]
                    )
                )
                step["uses"] = f"{repository}@{'a' * 40}"
                self.assertFalse(
                    workflow_installation_preflight.freshness_action_steps_are_safe(
                        [step]
                    )
                )

        for definition in (
            "alias gh='echo shadowed'",
            "declare -fx gh",
            "function gh { return 1; }",
            "gh ( ) { return 1; }",
        ):
            with self.subTest(shell_definition=definition):
                self.assertFalse(
                    workflow_installation_preflight.freshness_shell_definitions_are_safe(
                        definition
                    )
                )
        with (
            mock.patch.object(
                workflow_installation_preflight,
                "shell_command_segments",
                return_value=[["echo", "ready"]],
            ),
            mock.patch.object(
                workflow_installation_preflight.shlex,
                "shlex",
                side_effect=ValueError("malformed"),
            ),
        ):
            self.assertFalse(
                workflow_installation_preflight.freshness_shell_definitions_are_safe(
                    "ignored"
                )
            )

        variable_reference_cases = (
            ("$NAME", True),
            ("${NAME}", True),
            ("${NAMEevil}", False),
            ("$NAME-suffix", True),
            ("$NAMEevil", False),
        )
        for token, expected in variable_reference_cases:
            with self.subTest(variable_reference=token):
                self.assertEqual(
                    workflow_installation_preflight.freshness_variable_reference(
                        token, "NAME"
                    ),
                    expected,
                )

        workflow_path = PLUGIN_ROOT / ".github/workflows/freshness.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        workflow = workflow_installation_preflight.workflow_document(
            workflow_text, workflow_path
        )
        contract_job_text = "\n".join(
            step["run"]
            for step in workflow["jobs"]["audit"]["steps"]
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        )
        self.assertTrue(
            workflow_installation_preflight.freshness_summary_output_is_safe(
                contract_job_text
            )
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_summary_output_is_safe(
                "echo 'unterminated"
            )
        )
        summary_bypass = contract_job_text.replace(
            'cat "$RUNNER_TEMP/freshness.md" >> "$GITHUB_STEP_SUMMARY"',
            'cat "$RUNNER_TEMP/other.md" >> "$GITHUB_STEP_SUMMARY"',
            1,
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_summary_output_is_safe(
                summary_bypass
            )
        )
        duplicate_guard = (
            "\n".join(
                (
                    "if (( ${#issue_numbers[@]} > 1 )); then",
                    "  printf 'Found multiple open freshness reminder issues.\\n' >&2",
                    "  exit 1",
                    "fi",
                )
            )
            + "\n"
        )
        clean_condition = "if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n"
        out_of_order = contract_job_text.replace(duplicate_guard, "", 1).replace(
            clean_condition, clean_condition + duplicate_guard, 1
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_shell_control_flow_is_safe(
                out_of_order
            )
        )
        nonempty_guard = (
            "\n".join(
                (
                    'if [[ -n "$issue_numbers_output" ]]; then',
                    '  mapfile -t issue_numbers <<< "$issue_numbers_output"',
                    "fi",
                )
            )
            + "\n"
        )
        nonempty_after_clean = contract_job_text.replace(nonempty_guard, "", 1).replace(
            clean_condition, clean_condition + nonempty_guard, 1
        )
        self.assertFalse(
            workflow_installation_preflight.freshness_shell_control_flow_is_safe(
                nonempty_after_clean
            )
        )

        with (
            mock.patch.object(
                workflow_installation_preflight,
                "freshness_shell_control_flow_is_safe",
                return_value=True,
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "freshness_marker_check_is_safe",
                return_value=True,
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "shell_command_segments",
                return_value=None,
            ),
        ):
            self.assertFalse(
                workflow_installation_preflight.freshness_checker_result_controls_reconciliation(
                    "ignored"
                )
            )
        with (
            mock.patch.object(
                workflow_installation_preflight,
                "freshness_shell_control_flow_is_safe",
                return_value=True,
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "freshness_marker_check_is_safe",
                return_value=True,
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "shell_command_segments",
                return_value=[["echo", "ready"]],
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "freshness_shell_if_block_ranges",
                return_value=None,
            ),
        ):
            self.assertFalse(
                workflow_installation_preflight.freshness_checker_result_controls_reconciliation(
                    "ignored"
                )
            )
        with (
            mock.patch.object(
                workflow_installation_preflight,
                "freshness_shell_control_flow_is_safe",
                return_value=True,
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "freshness_marker_check_is_safe",
                return_value=True,
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "shell_command_segments",
                return_value=[["gh", "issue", "create", "gh", "issue", "close"]],
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "freshness_shell_if_block_ranges",
                return_value={},
            ),
        ):
            self.assertFalse(
                workflow_installation_preflight.freshness_checker_result_controls_reconciliation(
                    "ignored"
                )
            )

        workflow_preconditions = {
            name: mock.Mock(return_value=True)
            for name in (
                "has_repository_scoped_concurrency",
                "has_least_privileged_freshness_permissions",
                "has_repository_root_working_directory",
                "has_direct_freshness_jobs",
                "has_freshness_repository_context",
                "requires_issue_write",
            )
        }
        source = Path("freshness.yml")
        with (
            mock.patch.object(
                workflow_installation_preflight,
                "workflow_document",
                return_value={
                    "on": {
                        "schedule": [{"cron": "17 6 * * 5"}],
                        "workflow_dispatch": None,
                    },
                    "jobs": {"audit": {"steps": {}}},
                },
            ),
            mock.patch.multiple(
                workflow_installation_preflight, **workflow_preconditions
            ),
        ):
            self.assertFalse(
                workflow_installation_preflight.is_freshness_reminder_workflow(
                    "", source
                )
            )

        summary_bypass = workflow_text.replace(
            'cat "$RUNNER_TEMP/freshness.md" >> "$GITHUB_STEP_SUMMARY"',
            'cat "$RUNNER_TEMP/other.md" >> "$GITHUB_STEP_SUMMARY"',
            1,
        )
        self.assertFalse(
            workflow_installation_preflight.is_freshness_reminder_workflow(
                summary_bypass, workflow_path
            )
        )

        empty_job_document = {
            "on": {
                "schedule": [{"cron": "17 6 * * 5"}],
                "workflow_dispatch": None,
            },
            "jobs": {"audit": {"steps": []}},
        }
        with (
            mock.patch.object(
                workflow_installation_preflight,
                "workflow_document",
                return_value=empty_job_document,
            ),
            mock.patch.multiple(
                workflow_installation_preflight, **workflow_preconditions
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "issue_mutation_command_blocks",
                return_value=[],
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "has_freshness_repository_api_reads",
                return_value=True,
            ),
        ):
            self.assertFalse(
                workflow_installation_preflight.is_freshness_reminder_workflow(
                    "", source
                )
            )

        mutation_document = {
            "on": {
                "schedule": [{"cron": "17 6 * * 5"}],
                "workflow_dispatch": None,
            },
            "jobs": {"audit": {"steps": [{"run": "echo"}]}},
        }
        mutation_helpers = {
            name: mock.Mock(return_value=True)
            for name in (
                "freshness_job_execution_is_unconditional",
                "freshness_execution_context_is_bash",
                "freshness_authentication_bindings_are_safe",
                "freshness_checker_result_binding_is_safe",
                "freshness_shell_definitions_are_safe",
                "freshness_checker_result_controls_reconciliation",
                "freshness_api_result_controls_issue_selection",
                "freshness_summary_output_is_safe",
            )
        }
        with (
            mock.patch.object(
                workflow_installation_preflight,
                "workflow_document",
                return_value=mutation_document,
            ),
            mock.patch.multiple(
                workflow_installation_preflight, **workflow_preconditions
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "issue_mutation_command_blocks",
                return_value=[("create", ["gh", "issue", "create"])],
            ),
            mock.patch.object(
                workflow_installation_preflight,
                "has_freshness_repository_api_reads",
                return_value=True,
            ),
            mock.patch.multiple(workflow_installation_preflight, **mutation_helpers),
            mock.patch.object(
                workflow_installation_preflight,
                "freshness_command_order_is_valid",
                return_value=False,
            ),
        ):
            self.assertFalse(
                workflow_installation_preflight.is_freshness_reminder_workflow(
                    "", source
                )
            )

        container_document_cases: tuple[dict[str, Any], ...] = (
            {"jobs": []},
            {"jobs": {"invalid": None}},
        )
        for document in container_document_cases:
            with self.subTest(container_document=document):
                workflow_installation_preflight.validate_container_references(
                    document, Path("workflow.yml")
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
