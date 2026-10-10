from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import runpy
import shutil
import subprocess
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

SCRIPT_PATH = SCRIPT_DIRECTORY / "advanced_codeql_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.advanced_codeql_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load advanced_codeql_preflight.py")
advanced_codeql_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = advanced_codeql_preflight
SPEC.loader.exec_module(advanced_codeql_preflight)


class FakeClient:
    repository_response: object = {}
    actions_response: object = {"enabled": True}

    def __init__(self, hostname: str, *, forbidden_root: Path | None = None) -> None:
        del forbidden_root
        self.hostname = hostname
        self.request_count = 0

    def json(self, endpoint: str) -> object:
        self.request_count += 1
        if endpoint == "repos/octo/example":
            if isinstance(self.repository_response, Exception):
                raise self.repository_response
            return self.repository_response
        if endpoint == "repos/octo/example/actions/permissions":
            if isinstance(self.actions_response, Exception):
                raise self.actions_response
            return self.actions_response
        raise AssertionError(f"Unexpected endpoint: {endpoint}")


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "hostname": "github.com",
        "repository": "octo/example",
        "expected_repository_id": 42,
        "repo_root": ".",
        "default_branch": "main",
        "confirm_no_external_codeql": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def repository(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "full_name": "octo/example",
        "id": 42,
        "default_branch": "main",
        "archived": False,
        "disabled": False,
        "visibility": "public",
        "owner": {"type": "Organization"},
        "security_and_analysis": {"advanced_security": {"status": "enabled"}},
    }
    value.update(overrides)
    return value


class AdvancedCodeqlPreflightTests(unittest.TestCase):
    def test_boolean_caller_controls_reject_substitutes_before_client_creation(
        self,
    ) -> None:
        values: tuple[object, ...] = (None, "false", "true", 0, 1, [], {}, [True])
        for control in ("confirm_no_external_codeql",):
            for value in values:
                with (
                    self.subTest(control=control, value=value),
                    mock.patch.object(
                        advanced_codeql_preflight, "GitHubClient"
                    ) as client,
                    self.assertRaisesRegex(
                        advanced_codeql_preflight.InspectionError, "Boolean"
                    ),
                ):
                    advanced_codeql_preflight.run(arguments(**{control: value}))
                client.assert_not_called()

    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_documented_advanced_consumer_rejects_coerced_identity_and_branch(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        section = reference.split("- **CodeQL advanced setup**:", 1)[1]
        block = re.search(r"```powershell\n(.*?)\n\s*```", section, re.DOTALL)
        identity = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert block is not None and identity is not None
        body = "\n".join(
            line[2:] if line.startswith("  ") else line
            for line in block[1].splitlines()
        )
        body = body.replace(
            "$advancedCodeqlNoExternalConfirmed = $false",
            "$advancedCodeqlNoExternalConfirmed = (Get-Content -Raw -LiteralPath $env:FIXTURE | ConvertFrom-Json).uploader_consent",
        ).replace("OWNER/REPO", "octo/example")
        inner = {
            "inspection_complete": True,
            "decision": "may-offer-default-setup",
            "repository": "octo/example",
            "repository_id": 42,
            "default_branch": "main",
            "default_setup_state": "not-configured",
            "workflow_inspection_performed": True,
            "analysis_inspection_performed": True,
            "external_codeql_absence_confirmed": True,
            "advanced_workflows": [],
            "has_codeql_analysis": False,
        }
        FakeClient.repository_response = repository()
        FakeClient.actions_response = {"enabled": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copyfile(SCRIPT_PATH, scripts / SCRIPT_PATH.name)
            with (
                mock.patch.object(
                    advanced_codeql_preflight, "GitHubClient", FakeClient
                ),
                mock.patch.object(
                    advanced_codeql_preflight.codeql_preflight,
                    "run",
                    return_value=inner,
                ),
            ):
                base = advanced_codeql_preflight.run(arguments(repo_root=str(root)))
            prefix = "$ErrorActionPreference='Stop'\n$SELECTED_REPOSITORY_ID=42\n$DEFAULT_BRANCH='main'\n$REPO_ROOT=$env:SKILL_ROOT\n$REPO_SCAFFOLD_SKILL_ROOT=$env:SKILL_ROOT\nfunction python { $global:LASTEXITCODE=0; Get-Content -Raw -LiteralPath $env:FIXTURE }\n"
            suffix = "\n$copies++\n} catch {$failure=$_.Exception.Message}\n@{copies=$copies;failure=$failure} | ConvertTo-Json -Compress\n"
            command = root / "advanced-consumer.ps1"
            command.write_bytes(
                (
                    prefix
                    + identity[0]
                    + "\n$copies=0; $failure=$null\ntry {\n"
                    + body
                    + suffix
                ).encode("utf-8-sig")
            )
            variants: tuple[dict[str, object], ...] = (
                {},
                {"repository": ["octo/example"]},
                {"decision": ["may-install-advanced-codeql-workflow"]},
                {"default_branch": ["main"]},
                {"uploader_consent": "false"},
                {"uploader_consent": [True]},
            )
            fixture = root / "verdict.json"
            for override in variants:
                with self.subTest(override=override):
                    fixture.write_text(
                        json.dumps({**base, "uploader_consent": True, **override}),
                        encoding="utf-8",
                    )
                    result = subprocess.run(
                        [
                            str(shutil.which("powershell.exe") or shutil.which("pwsh")),
                            "-NoProfile",
                            "-NonInteractive",
                            "-File",
                            str(command),
                        ],
                        env={
                            **os.environ,
                            "FIXTURE": str(fixture),
                            "SKILL_ROOT": str(root),
                        },
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    observed = json.loads(
                        result.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                    )
                    self.assertEqual(observed["copies"], 0 if override else 1, observed)

    def test_rejects_non_directory_root_before_creating_api_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "not-a-directory"
            root.write_text("synthetic input", encoding="utf-8")
            with (
                mock.patch.object(advanced_codeql_preflight, "GitHubClient") as client,
                self.assertRaisesRegex(
                    advanced_codeql_preflight.InspectionError, "not a directory"
                ),
            ):
                advanced_codeql_preflight.run(arguments(repo_root=str(root)))
            client.assert_not_called()

    def test_nested_approval_cannot_replace_absence_proof_with_unknown_or_partial_flags(
        self,
    ) -> None:
        valid = {
            "inspection_complete": True,
            "decision": "may-offer-default-setup",
            "repository": "octo/example",
            "repository_id": 42,
            "default_branch": "main",
            "default_setup_state": "not-configured",
            "workflow_inspection_performed": True,
            "analysis_inspection_performed": True,
            "external_codeql_absence_confirmed": True,
            "advanced_workflows": [],
            "has_codeql_analysis": False,
        }
        for field, value in (
            ("workflow_inspection_performed", None),
            ("analysis_inspection_performed", False),
            ("external_codeql_absence_confirmed", "true"),
            ("advanced_workflows", None),
            ("advanced_workflows", ["local:unapproved.yml"]),
            ("has_codeql_analysis", None),
            ("has_codeql_analysis", 0),
            ("default_setup_state", "configured"),
        ):
            with (
                self.subTest(field=field, value=value),
                mock.patch.object(
                    advanced_codeql_preflight, "GitHubClient", FakeClient
                ),
                mock.patch.object(
                    advanced_codeql_preflight.codeql_preflight,
                    "run",
                    return_value={**valid, field: value},
                ),
                self.assertRaisesRegex(
                    advanced_codeql_preflight.InspectionError, "absence|proof|state"
                ),
            ):
                advanced_codeql_preflight.run(arguments())

    def test_advanced_verdict_revalidates_repository_and_actions_after_nested_inspection(
        self,
    ) -> None:
        original = repository(id=42)
        inner = {
            "inspection_complete": True,
            "decision": "may-offer-default-setup",
            "repository": "octo/example",
            "repository_id": 42,
            "default_branch": "main",
            "github_api_requests": 0,
            "default_setup_state": "not-configured",
            "workflow_inspection_performed": True,
            "analysis_inspection_performed": True,
            "external_codeql_absence_confirmed": True,
            "advanced_workflows": [],
            "has_codeql_analysis": False,
        }
        for final in (
            repository(id=43),
            repository(id=42, visibility="private"),
            repository(id=42, archived=True),
            None,
        ):
            client = FakeClient("github.com")
            with (
                self.subTest(final=final),
                mock.patch.object(
                    client, "json", side_effect=[original, {"enabled": True}, final]
                ),
                mock.patch.object(
                    advanced_codeql_preflight, "GitHubClient", return_value=client
                ),
                mock.patch.object(
                    advanced_codeql_preflight.codeql_preflight,
                    "run",
                    return_value=inner,
                ),
                self.assertRaises(advanced_codeql_preflight.InspectionError),
            ):
                advanced_codeql_preflight.run(arguments())
        client = FakeClient("github.com")
        with (
            mock.patch.object(
                client,
                "json",
                side_effect=[original, {"enabled": True}, original, {"enabled": False}],
            ),
            mock.patch.object(
                advanced_codeql_preflight, "GitHubClient", return_value=client
            ),
            mock.patch.object(
                advanced_codeql_preflight.codeql_preflight, "run", return_value=inner
            ),
            self.assertRaisesRegex(
                advanced_codeql_preflight.InspectionError, "Actions changed"
            ),
        ):
            advanced_codeql_preflight.run(arguments())

    def test_unbound_advanced_inspection_does_not_authorize_workflow_installation(
        self,
    ) -> None:
        inner = {
            "inspection_complete": True,
            "decision": "may-offer-default-setup",
            "repository": "octo/example",
            "repository_id": 42,
            "default_branch": "main",
            "github_api_requests": 0,
            "default_setup_state": "not-configured",
            "workflow_inspection_performed": True,
            "analysis_inspection_performed": True,
            "external_codeql_absence_confirmed": True,
            "advanced_workflows": [],
            "has_codeql_analysis": False,
        }
        with (
            mock.patch.object(advanced_codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                advanced_codeql_preflight.codeql_preflight, "run", return_value=inner
            ),
        ):
            result = advanced_codeql_preflight.run(
                arguments(expected_repository_id=None)
            )
        self.assertEqual(result["decision"], "bind-repository-identity-before-mutation")

    def test_nested_codeql_verdict_must_bind_the_outer_repository_and_branch(
        self,
    ) -> None:
        original = repository(id=42)
        inspection = {
            "inspection_complete": True,
            "decision": "may-offer-default-setup",
            "repository": "octo/example",
            "repository_id": 42,
            "default_branch": "main",
            "default_setup_state": "not-configured",
            "github_api_requests": 0,
        }
        for inner in (
            {**inspection, "repository_id": 43},
            {**inspection, "repository_id": "42"},
            {**inspection, "repository": "other/target"},
            {**inspection, "default_branch": "develop"},
        ):
            FakeClient.repository_response = original
            with (
                self.subTest(inner=inner),
                mock.patch.object(
                    advanced_codeql_preflight, "GitHubClient", FakeClient
                ),
                mock.patch.object(
                    advanced_codeql_preflight.codeql_preflight,
                    "run",
                    return_value=inner,
                ),
                self.assertRaises(advanced_codeql_preflight.InspectionError),
            ):
                advanced_codeql_preflight.run(arguments(expected_repository_id=42))

    def setUp(self) -> None:
        FakeClient.repository_response = repository()
        FakeClient.actions_response = {"enabled": True}

    def test_permits_eligible_repository_without_existing_codeql_setup(self) -> None:
        inspection = {
            "inspection_complete": True,
            "repository": "octo/example",
            "repository_id": 42,
            "default_branch": "main",
            "decision": "may-offer-default-setup",
            "github_api_requests": 7,
            "default_setup_state": "not-configured",
            "workflow_inspection_performed": True,
            "analysis_inspection_performed": True,
            "external_codeql_absence_confirmed": True,
            "advanced_workflows": [],
            "has_codeql_analysis": False,
        }
        with (
            mock.patch.object(advanced_codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                advanced_codeql_preflight.codeql_preflight,
                "run",
                return_value=inspection,
            ) as shared_run,
        ):
            result = advanced_codeql_preflight.run(arguments())

        self.assertEqual(result["decision"], "may-install-advanced-codeql-workflow")
        self.assertEqual(result["repository"], "octo/example")
        self.assertEqual(result["default_branch"], "main")
        self.assertEqual(result["github_code_security"], "not-required")
        self.assertTrue(result["github_actions_enabled"])
        self.assertEqual(result["github_api_requests"], 4)
        shared_arguments = shared_run.call_args.args[0]
        self.assertEqual(shared_arguments.repository, "octo/example")
        self.assertEqual(shared_arguments.expected_repository_id, 42)
        self.assertIsInstance(shared_run.call_args.kwargs["_client"], FakeClient)
        self.assertEqual(shared_arguments.default_branch, "main")
        self.assertTrue(shared_arguments.confirm_no_external_codeql)

        FakeClient.repository_response = repository(visibility="private")
        with (
            mock.patch.object(advanced_codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                advanced_codeql_preflight.codeql_preflight,
                "run",
                return_value=inspection,
            ),
        ):
            result = advanced_codeql_preflight.run(arguments())
        self.assertEqual(result["github_code_security"], "enabled")

    def test_requires_code_security_and_actions_before_deep_inspection(self) -> None:
        FakeClient.repository_response = repository(
            visibility="private",
            security_and_analysis={"advanced_security": {"status": "disabled"}},
        )
        with (
            mock.patch.object(advanced_codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                advanced_codeql_preflight.codeql_preflight, "run"
            ) as shared_run,
        ):
            result = advanced_codeql_preflight.run(arguments())
        self.assertEqual(
            result["decision"],
            "enable-github-code-security-before-installing-advanced-codeql",
        )
        self.assertIsNone(result["github_actions_enabled"])
        shared_run.assert_not_called()

        FakeClient.repository_response = repository()
        FakeClient.actions_response = {"enabled": False}
        with (
            mock.patch.object(advanced_codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                advanced_codeql_preflight.codeql_preflight, "run"
            ) as shared_run,
        ):
            result = advanced_codeql_preflight.run(arguments())
        self.assertEqual(
            result["decision"],
            "enable-github-actions-before-installing-advanced-codeql",
        )
        shared_run.assert_not_called()

    def test_preserves_existing_setup_modes(self) -> None:
        cases = (
            (
                "preserve-default-setup",
                "disable-default-setup-before-installing-advanced-codeql",
            ),
            ("require-explicit-switch-confirmation", "preserve-existing-codeql-setup"),
        )
        for shared_decision, expected in cases:
            with (
                self.subTest(shared_decision=shared_decision),
                mock.patch.object(
                    advanced_codeql_preflight, "GitHubClient", FakeClient
                ),
                mock.patch.object(
                    advanced_codeql_preflight.codeql_preflight,
                    "run",
                    return_value={
                        "inspection_complete": True,
                        "repository": "octo/example",
                        "repository_id": 42,
                        "default_branch": "main",
                        "decision": shared_decision,
                        "github_api_requests": 1,
                    },
                ),
            ):
                result = advanced_codeql_preflight.run(arguments())
            self.assertEqual(result["decision"], expected)

    def test_rejects_invalid_evidence_and_unknown_shared_decision(self) -> None:
        cases: tuple[tuple[argparse.Namespace, object, object, str], ...] = (
            (
                arguments(hostname="github.example"),
                repository(),
                {"enabled": True},
                "GitHub.com only",
            ),
            (arguments(), [], {"enabled": True}, "response is invalid"),
            (
                arguments(),
                repository(full_name="octo/other"),
                {"enabled": True},
                "different repository",
            ),
            (
                arguments(default_branch="develop"),
                repository(),
                {"enabled": True},
                "verified current default branch",
            ),
            (arguments(), repository(archived=True), {"enabled": True}, "Archived"),
            (arguments(), repository(disabled=True), {"enabled": True}, "Disabled"),
            (
                arguments(),
                repository(visibility="unknown"),
                {"enabled": True},
                "invalid visibility",
            ),
            (
                arguments(),
                repository(visibility=[]),
                {"enabled": True},
                "invalid visibility",
            ),
            (
                arguments(),
                repository(visibility="private", owner={"type": "User"}),
                {"enabled": True},
                "organization-owned",
            ),
            (
                arguments(),
                repository(visibility="private", security_and_analysis={}),
                {"enabled": True},
                "invalid advanced_security",
            ),
            (arguments(), repository(), {}, "permissions response is invalid"),
        )
        for args, repository_response, actions_response, message in cases:
            FakeClient.repository_response = repository_response
            FakeClient.actions_response = actions_response
            with (
                self.subTest(message=message),
                mock.patch.object(
                    advanced_codeql_preflight, "GitHubClient", FakeClient
                ),
            ):
                with self.assertRaisesRegex(
                    advanced_codeql_preflight.InspectionError, message
                ):
                    advanced_codeql_preflight.run(args)

        FakeClient.repository_response = repository()
        FakeClient.actions_response = {"enabled": True}
        with (
            mock.patch.object(advanced_codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                advanced_codeql_preflight.codeql_preflight,
                "run",
                return_value={
                    "inspection_complete": True,
                    "repository": "octo/example",
                    "repository_id": 42,
                    "default_branch": "main",
                    "decision": "unknown",
                },
            ),
        ):
            with self.assertRaisesRegex(
                advanced_codeql_preflight.InspectionError, "unknown decision"
            ):
                advanced_codeql_preflight.run(arguments())

    def test_standalone_code_security_controls_nonpublic_eligibility(self) -> None:
        for visibility in ("private", "internal"):
            for analysis, allowed in (
                ({"code_security": {"status": "enabled"}}, True),
                (
                    {
                        "code_security": {"status": "enabled"},
                        "advanced_security": {"status": "disabled"},
                    },
                    True,
                ),
                (
                    {
                        "code_security": {"status": "disabled"},
                        "advanced_security": {"status": "enabled"},
                    },
                    False,
                ),
            ):
                FakeClient.repository_response = repository(
                    visibility=visibility, security_and_analysis=analysis
                )
                with (
                    self.subTest(visibility=visibility, analysis=analysis),
                    mock.patch.object(
                        advanced_codeql_preflight, "GitHubClient", FakeClient
                    ),
                    mock.patch.object(
                        advanced_codeql_preflight.codeql_preflight,
                        "run",
                        return_value={
                            "inspection_complete": True,
                            "repository": "octo/example",
                            "repository_id": 42,
                            "default_branch": "main",
                            "decision": "may-offer-default-setup",
                            "github_api_requests": 0,
                            "default_setup_state": "not-configured",
                            "workflow_inspection_performed": True,
                            "analysis_inspection_performed": True,
                            "external_codeql_absence_confirmed": True,
                            "advanced_workflows": [],
                            "has_codeql_analysis": False,
                        },
                    ),
                ):
                    result = advanced_codeql_preflight.run(arguments())
                    self.assertEqual(
                        result["decision"],
                        "may-install-advanced-codeql-workflow"
                        if allowed
                        else "enable-github-code-security-before-installing-advanced-codeql",
                    )

    def test_malformed_code_security_cannot_fall_back_to_legacy_entitlement(
        self,
    ) -> None:
        value: object
        for value in (None, {}, [], {"status": []}, {"status": "unknown"}):
            FakeClient.repository_response = repository(
                visibility="private",
                security_and_analysis={
                    "code_security": value,
                    "advanced_security": {"status": "enabled"},
                },
            )
            with (
                self.subTest(value=value),
                mock.patch.object(
                    advanced_codeql_preflight, "GitHubClient", FakeClient
                ),
                mock.patch.object(
                    advanced_codeql_preflight.codeql_preflight,
                    "run",
                    side_effect=AssertionError(
                        "Malformed entitlement reached setup inspection"
                    ),
                ),
                self.assertRaisesRegex(
                    advanced_codeql_preflight.InspectionError, "code_security"
                ),
            ):
                advanced_codeql_preflight.run(arguments())

    def test_helpers_cli_and_documentation_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            advanced_codeql_preflight.InspectionError, "invalid 'archived'"
        ):
            advanced_codeql_preflight.require_boolean({}, "archived")
        with self.assertRaisesRegex(
            advanced_codeql_preflight.InspectionError, "invalid advanced_security"
        ):
            advanced_codeql_preflight.advanced_security_status({})
        with self.assertRaisesRegex(
            advanced_codeql_preflight.InspectionError, "permissions response is invalid"
        ):
            advanced_codeql_preflight.actions_are_enabled({})
        with self.assertRaisesRegex(
            advanced_codeql_preflight.InspectionError, "did not complete"
        ):
            advanced_codeql_preflight.existing_setup_decision({})

        with (
            mock.patch.object(
                advanced_codeql_preflight, "parse_args", return_value=arguments()
            ),
            mock.patch.object(advanced_codeql_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                advanced_codeql_preflight.codeql_preflight,
                "run",
                return_value={
                    "inspection_complete": True,
                    "repository": "octo/example",
                    "repository_id": 42,
                    "default_branch": "main",
                    "decision": "may-offer-default-setup",
                    "github_api_requests": 0,
                    "default_setup_state": "not-configured",
                    "workflow_inspection_performed": True,
                    "analysis_inspection_performed": True,
                    "external_codeql_absence_confirmed": True,
                    "advanced_workflows": [],
                    "has_codeql_analysis": False,
                },
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(advanced_codeql_preflight.main(), 0)
        self.assertIn("may-install", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                advanced_codeql_preflight,
                "parse_args",
                side_effect=advanced_codeql_preflight.InspectionError("blocked"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(advanced_codeql_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

        with mock.patch.object(
            sys,
            "argv",
            [
                "advanced_codeql_preflight.py",
                "--repo-root",
                ".",
                "--repository",
                "octo/example",
                "--default-branch",
                "main",
            ],
        ):
            self.assertEqual(
                advanced_codeql_preflight.parse_args().repository, "octo/example"
            )
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        self.assertIn("advanced_codeql_preflight.py", skill)
        self.assertIn("advanced_codeql_preflight.py", setup)
        self.assertIn('advancedCodeqlResult.repository, "OWNER/REPO"', setup)
        self.assertIn("advancedCodeqlResult.default_branch -cne $DEFAULT_BRANCH", setup)


if __name__ == "__main__":
    unittest.main()
