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

SCRIPT_PATH = SCRIPT_DIRECTORY / "security_features_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.security_features_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load security_features_preflight.py")
security_features_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = security_features_preflight
SPEC.loader.exec_module(security_features_preflight)


class FakeClient:
    response: object = {}
    raw_error: Exception | None = None

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        self.request_count = 0

    def json(self, endpoint: str) -> object:
        self.request_count += 1
        if endpoint != "repos/octo/example":
            raise AssertionError(f"Unexpected endpoint: {endpoint}")
        return self.response

    def raw(self, endpoint: str) -> str:
        self.request_count += 1
        if endpoint != "repos/octo/example/vulnerability-alerts":
            raise AssertionError(f"Unexpected endpoint: {endpoint}")
        if self.raw_error is not None:
            raise self.raw_error
        return ""

    def require_empty_response(self, endpoint: str, expected_status: int) -> None:
        payload = self.raw(endpoint)
        if not payload:
            payload = "HTTP/2.0 204 No Content\n\n"
        transport = mock.Mock()
        transport._run.return_value = payload
        codeql_preflight.GitHubClient.require_empty_response(
            transport, endpoint, expected_status
        )


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "hostname": "github.com",
        "repository": "octo/example",
        "expected_repository_id": 42,
        "dependabot_alerts": False,
        "automated_security_fixes": False,
        "secret_scanning": False,
        "push_protection": False,
        "private_vulnerability_reporting": False,
        "confirm_private_secret_protection_eligibility": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def repository(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "full_name": "octo/example",
        "id": 42,
        "archived": False,
        "disabled": False,
        "permissions": {"admin": True},
        "fork": False,
        "visibility": "public",
        "owner": {"type": "Organization"},
        "security_and_analysis": {
            "dependabot_security_updates": {"status": "disabled"},
            "secret_scanning": {"status": "disabled"},
            "secret_scanning_push_protection": {"status": "disabled"},
        },
    }
    value.update(overrides)
    return value


class SecurityFeaturesPreflightTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_security_consent_rejects_truthy_non_booleans_before_constructing_requests(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        beginning = "# Set each value only from explicit user approval."
        source = (
            beginning
            + reference.split(beginning, 1)[1].split(
                "function Assert-SecurityFeaturePreflightSchema", 1
            )[0]
        )
        for field in (
            "enableDependabotAlertsRequested",
            "enableAutomatedSecurityFixesRequested",
            "enableSecretScanningRequested",
            "enablePushProtectionRequested",
            "enablePrivateVulnerabilityReportingRequested",
            "confirmPrivateSecretProtectionEligibility",
        ):
            for value in ("false", 1, [True], None):
                with (
                    self.subTest(field=field, value=value),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    fixture = root / "consent.json"
                    fixture.write_text(json.dumps({"value": value}), encoding="utf-8")
                    body = source.replace(
                        "$" + field + " = $false",
                        "$"
                        + field
                        + " = (Get-Content -Raw -LiteralPath $env:FIXTURE | ConvertFrom-Json).value",
                    )
                    if field == "confirmPrivateSecretProtectionEligibility":
                        body = body.replace(
                            "$enableSecretScanningRequested = $false",
                            "$enableSecretScanningRequested = $true",
                        )
                    command = root / "security-choice.ps1"
                    command.write_bytes(
                        (
                            "$ErrorActionPreference='Stop'\n$SELECTED_REPOSITORY_ID=42\n$failure=$null\ntry {\n"
                            + body
                            + "\n} catch { $failure=$_.Exception.Message }\n@{failure=$failure} | ConvertTo-Json -Compress\n"
                        ).encode("utf-8-sig")
                    )
                    result = subprocess.run(
                        [
                            str(shutil.which("powershell.exe") or shutil.which("pwsh")),
                            "-NoProfile",
                            "-NonInteractive",
                            "-File",
                            str(command),
                        ],
                        env={**os.environ, "FIXTURE": str(fixture)},
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    observed = json.loads(
                        result.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                    )
                    self.assertIsNotNone(observed["failure"], observed)

    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_native_security_verdict_schema_prevents_coerced_initial_and_fresh_approval(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        identity = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert identity is not None
        schema = (
            "function Assert-SecurityFeaturePreflightSchema {"
            + reference.split("function Assert-SecurityFeaturePreflightSchema {", 1)[
                1
            ].split("\n$securityPreflightResult =", 1)[0]
        )
        consumer = (
            "function Get-ValidatedSecurityFeaturePreflight {"
            + reference.split("function Get-ValidatedSecurityFeaturePreflight {", 1)[
                1
            ].split("\n```", 1)[0]
        )
        self.assertIn(
            "Assert-SecurityFeaturePreflightSchema -Verdict $securityPreflightResult",
            reference,
        )
        self.assertIn(
            "Assert-SecurityFeaturePreflightSchema -Verdict $result", consumer
        )
        valid: dict[str, object] = {
            "inspection_complete": True,
            "decision": "may-configure-security-features",
            "repository": "OWNER/REPO",
            "repository_id": 42,
            "administration_permission": True,
            "requested_features": ["dependabot_alerts"],
            "visibility": "public",
            "owner_type": "Organization",
            "is_fork": False,
            "private_security_feature_eligibility": "not-required",
            "dependabot_alerts_precondition": None,
            "security_and_analysis": {
                "dependabot_security_updates": None,
                "secret_scanning": "disabled",
                "secret_scanning_push_protection": None,
            },
        }
        cases: list[tuple[str, dict[str, object], int]] = [("positive", valid, 1)]
        cases.extend(
            (
                (
                    "private-reporting",
                    {
                        **valid,
                        "requested_features": ["private_vulnerability_reporting"],
                        "visibility": "private",
                    },
                    0,
                ),
                (
                    "public-reporting",
                    {
                        **valid,
                        "requested_features": ["private_vulnerability_reporting"],
                        "is_fork": True,
                    },
                    1,
                ),
                (
                    "push-disabled",
                    {**valid, "requested_features": ["push_protection"]},
                    0,
                ),
                (
                    "push-observed",
                    {
                        **valid,
                        "requested_features": ["push_protection"],
                        "security_and_analysis": {
                            "dependabot_security_updates": None,
                            "secret_scanning": "enabled",
                            "secret_scanning_push_protection": None,
                        },
                    },
                    1,
                ),
                (
                    "fixes-unobserved",
                    {
                        **valid,
                        "requested_features": ["automated_security_fixes"],
                        "dependabot_alerts_precondition": "requested-for-prior-enable",
                    },
                    0,
                ),
                (
                    "fixes-observed",
                    {
                        **valid,
                        "requested_features": ["automated_security_fixes"],
                        "dependabot_alerts_precondition": "verified-enabled",
                    },
                    1,
                ),
            )
        )
        for field, value in (
            ("administration_permission", False),
            ("administration_permission", "false"),
            ("repository", ["OWNER/REPO"]),
            ("requested_features", "dependabot_alerts"),
            ("requested_features", ["dependabot_alerts", "dependabot_alerts"]),
            ("requested_features", ["unknown"]),
            ("visibility", ["public"]),
            ("owner_type", "unknown"),
            ("is_fork", "false"),
            ("security_and_analysis", {}),
            ("dependabot_alerts_precondition", "verified-enabled"),
            ("decision", "bind-repository-identity-before-mutation"),
            ("repository_id", 43),
        ):
            cases.append((field, {**valid, field: value}, 0))
        for field in ("administration_permission", "dependabot_alerts_precondition"):
            cases.append(
                (
                    "missing:" + field,
                    {key: value for key, value in valid.items() if key != field},
                    0,
                )
            )
        prelude = r"""
$ErrorActionPreference='Stop'
$SELECTED_REPOSITORY_ID=42; $securityFeaturesPreflight='synthetic'
function python { $global:LASTEXITCODE=0; Get-Content -LiteralPath $env:FIXTURE -Raw -Encoding UTF8 }
function Write-Warning { param([string]$Message) }
"""
        suffix = r"""
$mutations=0; $failure=$null
try {
 $result=Get-ValidatedSecurityFeaturePreflight -FeatureArguments @($env:FEATURE_ARGUMENT) -ExpectedFeature $env:EXPECTED_FEATURE
 if ($null -ne $result) { $mutations++ }
} catch { $failure=$_.Exception.Message }
@{mutations=$mutations;failure=$failure} | ConvertTo-Json -Compress
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "consumer.ps1"
            script.write_bytes(
                (prelude + identity[0] + schema + consumer + suffix).encode("utf-8-sig")
            )
            for label, verdict, expected in cases:
                with self.subTest(case=label):
                    selected_feature = (
                        "private_vulnerability_reporting"
                        if label in {"private-reporting", "public-reporting"}
                        else "push_protection"
                        if label in {"push-disabled", "push-observed"}
                        else "automated_security_fixes"
                        if label in {"fixes-unobserved", "fixes-observed"}
                        else "dependabot_alerts"
                    )
                    fixture = root / "fixture.json"
                    fixture.write_text(json.dumps(verdict), encoding="utf-8")
                    result = subprocess.run(
                        [
                            str(shutil.which("powershell.exe") or shutil.which("pwsh")),
                            "-NoProfile",
                            "-NonInteractive",
                            "-File",
                            str(script),
                        ],
                        env={
                            **os.environ,
                            "FIXTURE": str(fixture),
                            "EXPECTED_FEATURE": selected_feature,
                            "FEATURE_ARGUMENT": "--enable-"
                            + selected_feature.replace("_", "-"),
                        },
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    response = json.loads(
                        result.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                    )
                    self.assertEqual(response["mutations"], expected, response)

    def test_dependabot_prerequisite_loss_on_final_read_is_inconclusive(self) -> None:
        FakeClient.response = repository()
        with (
            mock.patch.object(security_features_preflight, "GitHubClient", FakeClient),
            mock.patch.object(
                FakeClient,
                "raw",
                side_effect=[
                    "",
                    security_features_preflight.InspectionError("HTTP 404"),
                ],
            ),
            self.assertRaises(security_features_preflight.InspectionError),
        ):
            security_features_preflight.run(arguments(automated_security_fixes=True))

    def test_dependabot_prerequisite_cannot_accept_arbitrary_success_body(self) -> None:
        FakeClient.response = repository()
        for response in (
            'HTTP/2.0 200 OK\nContent-Type: application/json\n\n{"enabled":false}',
            'HTTP/2.0 204 No Content\n\n{"enabled":false}',
            "incomplete transport envelope",
        ):
            with (
                self.subTest(response=response),
                mock.patch.object(
                    security_features_preflight, "GitHubClient", FakeClient
                ),
                mock.patch.object(FakeClient, "raw", return_value=response),
                self.assertRaises(security_features_preflight.InspectionError),
            ):
                security_features_preflight.run(
                    arguments(automated_security_fixes=True)
                )

    def test_multi_read_security_verdict_rejects_changed_target_or_prerequisite(
        self,
    ) -> None:
        original = repository()
        for final in (
            repository(id=43),
            repository(archived=True),
            repository(disabled=True),
            repository(permissions={"admin": False}),
            repository(visibility="private"),
            repository(owner={"type": "User"}),
            repository(
                security_and_analysis={"secret_scanning": {"status": "enabled"}}
            ),
            None,
        ):

            class ChangingClient(FakeClient):
                def __init__(self, hostname: str) -> None:
                    super().__init__(hostname)
                    self.metadata_reads = 0

                def json(self, endpoint: str) -> object:
                    self.request_count += 1
                    self.metadata_reads += 1
                    return original if self.metadata_reads == 1 else final

            FakeClient.raw_error = None
            with (
                self.subTest(final=final),
                mock.patch.object(
                    security_features_preflight, "GitHubClient", ChangingClient
                ),
                self.assertRaises(security_features_preflight.InspectionError),
            ):
                security_features_preflight.run(
                    arguments(automated_security_fixes=True)
                )

    def test_public_fork_reporting_eligibility_preserves_identity_and_admin_gates(
        self,
    ) -> None:
        for owner_type in ("User", "Organization"):
            FakeClient.response = repository(fork=True, owner={"type": owner_type})
            with (
                self.subTest(owner=owner_type),
                mock.patch.object(
                    security_features_preflight, "GitHubClient", FakeClient
                ),
            ):
                result = security_features_preflight.run(
                    arguments(private_vulnerability_reporting=True)
                )
                self.assertEqual(result["decision"], "may-configure-security-features")
                self.assertEqual(result["repository_id"], 42)
                self.assertIs(result["is_fork"], True)
                self.assertEqual(
                    result["requested_features"], ["private_vulnerability_reporting"]
                )
        for changes in (
            {"visibility": "private"},
            {"visibility": "internal"},
            {"permissions": {"admin": False}},
            {"id": 43},
            {"archived": True},
            {"disabled": True},
        ):
            FakeClient.response = repository(fork=True, **changes)
            with (
                self.subTest(changes=changes),
                mock.patch.object(
                    security_features_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaises(security_features_preflight.InspectionError),
            ):
                security_features_preflight.run(
                    arguments(private_vulnerability_reporting=True)
                )

    def test_unbound_identity_inspection_does_not_authorize_a_mutation(self) -> None:
        FakeClient.response = repository()
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            result = security_features_preflight.run(
                arguments(secret_scanning=True, expected_repository_id=None)
            )
        self.assertEqual(result["decision"], "bind-repository-identity-before-mutation")
        self.assertEqual(result["repository_id"], 42)

    def test_repository_id_binds_repeated_inspection_to_the_selected_target(
        self,
    ) -> None:
        FakeClient.response = repository(id=42)
        args = arguments(secret_scanning=True, expected_repository_id=42)
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            result = security_features_preflight.run(args)
        self.assertEqual(result.get("repository_id"), 42)
        for changed in (43, None, True, "42", 0, -1, 42.5):
            with self.subTest(identity=changed):
                FakeClient.response = repository(id=changed)
                with (
                    mock.patch.object(
                        security_features_preflight, "GitHubClient", FakeClient
                    ),
                    self.assertRaisesRegex(
                        security_features_preflight.InspectionError,
                        "repository ID|repository identity",
                    ),
                ):
                    security_features_preflight.run(args)

    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_documented_security_writers_verify_failed_ack_and_strict_readback(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        identity_helper = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert identity_helper is not None
        sections = (
            ("- **Dependabot alerts**:", "- **Secret scanning + push protection**:", 2),
            (
                "- **Secret scanning + push protection**:",
                "- **CodeQL advanced setup**:",
                2,
            ),
            (
                "- **Private vulnerability reporting**:",
                "- **Dependency review workflow**:",
                1,
            ),
        )
        prelude = r"""
$ErrorActionPreference='Stop'
$script:writes=0; $script:reads=0; $script:warnings=@()
$global:LASTEXITCODE=0
$enableDependabotAlertsRequested=$true
$enableAutomatedSecurityFixesRequested=$true
$enableSecretScanningRequested=$true
$enablePushProtectionRequested=$true
$enablePrivateVulnerabilityReportingRequested=$true
$confirmPrivateSecretProtectionEligibility=$false
function Write-Warning { param([string]$Message); $script:warnings += $Message }
function Get-ValidatedSecurityFeaturePreflight {
 param([string[]]$FeatureArguments,[string]$ExpectedFeature,[switch]$ConfirmPrivateSecretProtectionEligibility)
 if($env:CASE -eq 'denied') { return $null }
 return @{inspection_complete=$true;repository='OWNER/REPO';requested_features=@($ExpectedFeature)}
}
function gh {
 $arguments=@($args); $global:LASTEXITCODE=0
 if($arguments -contains 'PUT' -or $arguments -contains 'edit') {
  $script:writes += 1
  if($env:CASE -eq 'failed-ack') { $global:LASTEXITCODE=1 }
  return 'Synthetic acknowledgment'
 }
 $script:reads += 1
 if($env:CASE -eq 'read-unavailable') { $global:LASTEXITCODE=1; return 'Synthetic read failure' }
 if($arguments -contains 'repos/OWNER/REPO/vulnerability-alerts') {
  if($env:CASE -eq 'wrong-alert-status') { return "HTTP/2.0 200 OK`n`n" }
  if($env:CASE -eq 'alert-body') { return "HTTP/2.0 204 No Content`n`n{}" }
  return "HTTP/2.0 204 No Content`n`n"
 }
 if($arguments -contains 'repos/OWNER/REPO/automated-security-fixes') {
  if($env:CASE -eq 'malformed') { return '{"enabled":"true","paused":"false"}' }
  return '{"enabled":true,"paused":false}'
 }
 if($arguments -contains 'repos/OWNER/REPO/private-vulnerability-reporting') {
  if($arguments -contains '.enabled') { return 'true' }
  if($env:CASE -eq 'malformed') { return '{"enabled":"true"}' }
  return '{"enabled":true}'
 }
 if($arguments -contains '.security_and_analysis.secret_scanning.status') { return 'enabled' }
 if($env:CASE -eq 'malformed') { return '{"id":42,"full_name":"OWNER/REPO","security_and_analysis":{"secret_scanning":{"status":["enabled"]},"secret_scanning_push_protection":{"status":["enabled"]}},"secret_scanning":["enabled"],"push_protection":["enabled"]}' }
 return '{"id":42,"full_name":"OWNER/REPO","security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}},"secret_scanning":"enabled","push_protection":"enabled"}'
}
"""
        postlude = "\n@{writes=$script:writes;reads=$script:reads;warnings=@($script:warnings)} | ConvertTo-Json -Depth 10 -Compress\n"
        for start, end, count in sections:
            section = reference.split(start, 1)[1].split(end, 1)[0]
            block = re.search(r"```powershell\n(.*?)\n\s*```", section, re.DOTALL)
            assert block is not None
            consumer = "\n".join(
                line[2:] if line.startswith("  ") else line
                for line in block[1].splitlines()
            )
            with tempfile.TemporaryDirectory() as directory:
                script = Path(directory) / "consumer.ps1"
                script.write_bytes(
                    (
                        prelude
                        + "\n$SELECTED_REPOSITORY_ID=42\n"
                        + identity_helper[0]
                        + "\n"
                        + consumer
                        + postlude
                    ).encode("utf-8-sig")
                )
                for case in (
                    "positive",
                    "failed-ack",
                    "malformed",
                    "read-unavailable",
                    "denied",
                    *(
                        ("wrong-alert-status", "alert-body")
                        if start == "- **Dependabot alerts**:"
                        else ()
                    ),
                ):
                    with self.subTest(section=start, case=case):
                        environment = os.environ.copy()
                        environment["CASE"] = case
                        result = subprocess.run(
                            [
                                str(
                                    shutil.which("powershell.exe")
                                    or shutil.which("pwsh")
                                ),
                                "-NoProfile",
                                "-NonInteractive",
                                "-File",
                                str(script),
                            ],
                            env=environment,
                            capture_output=True,
                            timeout=30,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        state = json.loads(
                            result.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                        )
                        self.assertEqual(
                            state["writes"], 0 if case == "denied" else count
                        )
                        self.assertEqual(
                            state["reads"], 0 if case == "denied" else count
                        )
                        if case == "positive":
                            self.assertEqual(state["warnings"], [])
                        elif case != "denied":
                            self.assertTrue(state["warnings"])

    def test_runs_for_verified_repository_and_exposes_requested_plan(self) -> None:
        FakeClient.response = repository()
        args = arguments(
            dependabot_alerts=True,
            secret_scanning=True,
            push_protection=True,
            private_vulnerability_reporting=True,
        )
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            result = security_features_preflight.run(args)

        self.assertEqual(result["decision"], "may-configure-security-features")
        self.assertEqual(
            result["requested_features"],
            [
                "dependabot_alerts",
                "secret_scanning",
                "push_protection",
                "private_vulnerability_reporting",
            ],
        )
        self.assertEqual(result["security_and_analysis"]["secret_scanning"], "disabled")
        self.assertEqual(result["private_security_feature_eligibility"], "not-required")
        self.assertTrue(result["administration_permission"])
        self.assertEqual(result["github_api_requests"], 1)

    def test_rejects_invalid_feature_selection_and_repository_identity(self) -> None:
        for args, message in [
            (
                arguments(hostname="github.example", dependabot_alerts=True),
                "GitHub.com only",
            ),
            (arguments(), "Select at least one"),
        ]:
            with self.subTest(args=args):
                with self.assertRaisesRegex(
                    security_features_preflight.InspectionError, message
                ):
                    security_features_preflight.run(args)

        cases = [
            ([], "response is invalid"),
            (repository(full_name="octo/other"), "different repository"),
            (repository(archived=True), "Archived"),
            (repository(disabled=True), "Disabled"),
            (repository(permissions={}), "administration permission"),
            (repository(permissions={"admin": False}), "administration permission"),
            (repository(permissions={"admin": "yes"}), "invalid 'admin'"),
            (repository(fork="no"), "invalid 'fork'"),
            (repository(visibility="unknown"), "invalid visibility"),
            (repository(visibility=[]), "invalid visibility"),
            (repository(owner={"type": "Enterprise"}), "unsupported owner"),
            (repository(owner={"type": []}), "unsupported owner"),
        ]
        for response, message in cases:
            FakeClient.response = response
            with self.subTest(response=response):
                with mock.patch.object(
                    security_features_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        security_features_preflight.InspectionError, message
                    ):
                        security_features_preflight.run(
                            arguments(dependabot_alerts=True)
                        )

    def test_security_status_validation_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            security_features_preflight.InspectionError, "invalid 'archived'"
        ):
            security_features_preflight.require_boolean({}, "archived")
        cases: list[tuple[object, str]] = [
            (None, "no security_and_analysis"),
            ({"secret_scanning": "enabled"}, "invalid 'secret_scanning'"),
            (
                {"secret_scanning": {"status": "unknown"}},
                "invalid 'secret_scanning'",
            ),
            (
                {"secret_scanning": {"status": []}},
                "invalid 'secret_scanning'",
            ),
        ]
        for analysis, message in cases:
            with self.subTest(analysis=analysis):
                with self.assertRaisesRegex(
                    security_features_preflight.InspectionError, message
                ):
                    security_features_preflight.security_statuses(
                        {"security_and_analysis": analysis}
                    )
        self.assertEqual(
            security_features_preflight.security_statuses(
                {"security_and_analysis": {}}
            ),
            {
                "dependabot_security_updates": None,
                "secret_scanning": None,
                "secret_scanning_push_protection": None,
            },
        )

    def test_enforces_security_feature_dependencies_and_eligibility(self) -> None:
        cases = [
            (
                arguments(push_protection=True),
                repository(),
                "Push protection requires",
            ),
            (
                arguments(private_vulnerability_reporting=True),
                repository(visibility="private"),
                "public repositories",
            ),
            (
                arguments(private_vulnerability_reporting=True),
                repository(visibility="internal", fork=True),
                "public repositories",
            ),
        ]
        for args, response, message in cases:
            FakeClient.response = response
            with self.subTest(args=args, response=response):
                with mock.patch.object(
                    security_features_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        security_features_preflight.InspectionError, message
                    ):
                        security_features_preflight.run(args)

        enabled = repository(
            security_and_analysis={"secret_scanning": {"status": "enabled"}}
        )
        FakeClient.response = enabled
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            result = security_features_preflight.run(arguments(push_protection=True))
        self.assertEqual(result["requested_features"], ["push_protection"])

    def test_private_secret_features_require_explicit_eligibility_confirmation(
        self,
    ) -> None:
        FakeClient.response = repository(visibility="public")
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            public = security_features_preflight.run(arguments(secret_scanning=True))
        self.assertEqual(public["decision"], "may-configure-security-features")
        self.assertEqual(public["private_security_feature_eligibility"], "not-required")

        for visibility in ("private", "internal"):
            FakeClient.response = repository(visibility=visibility)
            with (
                self.subTest(visibility=visibility, eligibility="unconfirmed"),
                mock.patch.object(
                    security_features_preflight, "GitHubClient", FakeClient
                ),
            ):
                unconfirmed = security_features_preflight.run(
                    arguments(secret_scanning=True)
                )
            self.assertTrue(unconfirmed["inspection_complete"])
            self.assertEqual(
                unconfirmed["decision"],
                "confirm-private-secret-protection-eligibility",
            )
            self.assertEqual(
                unconfirmed["private_security_feature_eligibility"],
                "confirmation-required",
            )

            for feature in ("secret_scanning", "push_protection"):
                FakeClient.response = repository(visibility=visibility)
                args = arguments(
                    secret_scanning=True,
                    push_protection=feature == "push_protection",
                    confirm_private_secret_protection_eligibility=True,
                )
                with (
                    self.subTest(visibility=visibility, feature=feature),
                    mock.patch.object(
                        security_features_preflight, "GitHubClient", FakeClient
                    ),
                ):
                    confirmed = security_features_preflight.run(args)
                self.assertEqual(
                    confirmed["decision"], "may-configure-security-features"
                )
                self.assertEqual(
                    confirmed["private_security_feature_eligibility"],
                    "user-confirmed",
                )

        with self.assertRaisesRegex(
            security_features_preflight.InspectionError, "must be boolean"
        ):
            security_features_preflight.run(
                arguments(
                    secret_scanning=True,
                    confirm_private_secret_protection_eligibility="yes",
                )
            )

        with self.assertRaisesRegex(
            security_features_preflight.InspectionError, "without requesting"
        ):
            security_features_preflight.run(
                arguments(
                    dependabot_alerts=True,
                    confirm_private_secret_protection_eligibility=True,
                )
            )

    def test_automated_security_fixes_require_dependabot_alert_evidence(self) -> None:
        FakeClient.response = repository()
        FakeClient.raw_error = None
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            result = security_features_preflight.run(
                arguments(automated_security_fixes=True)
            )
        self.assertEqual(result["dependabot_alerts_precondition"], "verified-enabled")
        self.assertEqual(result["github_api_requests"], 4)

        FakeClient.raw_error = security_features_preflight.InspectionError("not found")
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                security_features_preflight.InspectionError,
                "Automated security fixes require Dependabot alerts",
            ):
                security_features_preflight.run(
                    arguments(automated_security_fixes=True)
                )

        for status in (403, 404, 503):
            FakeClient.raw_error = security_features_preflight.InspectionError(
                f"GitHub API request failed: HTTP {status}: synthetic response"
            )
            with (
                self.subTest(status=status),
                mock.patch.object(
                    security_features_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    security_features_preflight.InspectionError,
                    f"HTTP {status}.*state remains unverified",
                ),
            ):
                security_features_preflight.run(
                    arguments(automated_security_fixes=True)
                )

        FakeClient.raw_error = None
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            result = security_features_preflight.run(
                arguments(dependabot_alerts=True, automated_security_fixes=True)
            )
        self.assertEqual(
            result["dependabot_alerts_precondition"], "requested-for-prior-enable"
        )
        self.assertEqual(result["github_api_requests"], 1)

    def test_cli_reports_success_and_inconclusive_result(self) -> None:
        FakeClient.response = repository()
        with (
            mock.patch.object(
                security_features_preflight,
                "parse_args",
                return_value=arguments(dependabot_alerts=True),
            ),
            mock.patch.object(security_features_preflight, "GitHubClient", FakeClient),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(security_features_preflight.main(), 0)
        self.assertIn("may-configure", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                security_features_preflight,
                "parse_args",
                side_effect=security_features_preflight.InspectionError("blocked"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(security_features_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

    def test_cli_parser_and_module_entrypoint(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                "security_features_preflight.py",
                "--repository",
                "octo/example",
                "--enable-secret-scanning",
                "--confirm-private-secret-protection-eligibility",
            ],
        ):
            args = security_features_preflight.parse_args()
        self.assertTrue(args.secret_scanning)
        self.assertTrue(args.confirm_private_secret_protection_eligibility)
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    def test_cli_redacts_api_error_details_and_preserves_http_status(self) -> None:
        with (
            mock.patch.object(
                security_features_preflight,
                "parse_args",
                return_value=arguments(),
            ),
            mock.patch.object(
                security_features_preflight,
                "run",
                side_effect=security_features_preflight.InspectionError(
                    "GitHub API request failed: HTTP 403: synthetic-secret-response"
                ),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(security_features_preflight.main(), 2)

        response_text = print_mock.call_args.args[0]
        response = json.loads(response_text)
        self.assertEqual(response["github_http_status"], 403)
        self.assertNotIn("synthetic-secret-response", response_text)

        with (
            mock.patch.object(
                security_features_preflight,
                "parse_args",
                return_value=arguments(),
            ),
            mock.patch.object(
                security_features_preflight,
                "run",
                side_effect=OSError("synthetic-local-error"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(security_features_preflight.main(), 2)
        local_error_text = print_mock.call_args.args[0]
        local_error_response = json.loads(local_error_text)
        self.assertNotIn("github_http_status", local_error_response)
        self.assertNotIn("synthetic-local-error", local_error_text)

    def test_skill_and_reference_require_the_preflight_before_mutation(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        security = setup.split("## Security features", 1)[1].split("\n## ", 1)[0]
        self.assertIn("security_features_preflight.py", skill)
        self.assertIn("exact approved feature", skill)
        self.assertIn("security_features_preflight.py", security)
        self.assertIn("--enable-push-protection", security)
        self.assertIn("--confirm-private-secret-protection-eligibility", security)
        self.assertIn("Secret Protection eligibility", security)
        self.assertIn("public repository", security)
        self.assertNotIn("public non-fork", security)
        self.assertIn("Dependabot alerts before automated security fixes", security)
        self.assertIn("administration permission", security)
        self.assertIn("$requestedSecurityFeatures", security)
        self.assertIn("$approvedSecurityFeatures", security)
        self.assertIn("Compare-Object", security)
        self.assertIn("if ($enablePrivateVulnerabilityReportingRequested)", security)

    def test_each_security_setting_revalidates_then_checks_write_and_readback(
        self,
    ) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        security = setup.split("## Security features", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Get-ValidatedSecurityFeaturePreflight", security)
        self.assertIn('$result.repository, "OWNER/REPO"', security)
        self.assertIn("$features.Count -ne 1", security)
        self.assertIn("$exitCode -ne 0", security)
        self.assertIn("if ($requestedSecurityFeatures.Count -gt 0)", security)
        self.assertIn("No security-feature changes were requested", security)
        self.assertLess(
            security.index("if ($requestedSecurityFeatures.Count -gt 0)"),
            security.index(
                "$securityPreflightOutput = python $securityFeaturesPreflight"
            ),
        )

        cases = (
            (
                "- **Dependabot alerts**",
                "- **Secret scanning + push protection**",
                "-X PUT",
                "repos/OWNER/REPO/vulnerability-alerts",
                "repos/OWNER/REPO/vulnerability-alerts",
                "if ($enableDependabotAlertsRequested)",
            ),
            (
                "- **Dependabot alerts**",
                "- **Secret scanning + push protection**",
                "-X PUT",
                "repos/OWNER/REPO/automated-security-fixes",
                "repos/OWNER/REPO/automated-security-fixes",
                "if ($enableAutomatedSecurityFixesRequested)",
            ),
            (
                "- **Secret scanning + push protection**",
                "- **CodeQL advanced setup**",
                "--enable-secret-scanning",
                "--enable-secret-scanning",
                ".security_and_analysis.secret_scanning.status",
                "if ($enableSecretScanningRequested)",
            ),
            (
                "- **Secret scanning + push protection**",
                "- **CodeQL advanced setup**",
                "--enable-secret-scanning-push-protection",
                "--enable-secret-scanning-push-protection",
                "$pushProtectionState.security_and_analysis.secret_scanning_push_protection.status",
                "if ($enablePushProtectionRequested)",
            ),
            (
                "- **Private vulnerability reporting**",
                "- **Dependency review workflow**",
                "-X PUT",
                "repos/OWNER/REPO/private-vulnerability-reporting",
                "$reportingState.enabled -isnot [bool]",
                "if ($enablePrivateVulnerabilityReportingRequested)",
            ),
        )
        for (
            start_marker,
            end_marker,
            mutation,
            mutation_flag,
            verification,
            guard,
        ) in cases:
            with self.subTest(mutation=mutation):
                start = security.index(start_marker)
                end = security.index(end_marker, start + len(start_marker))
                section = security[start:end]
                guard_index = section.index(guard)
                preflight_index = section.index(
                    "Get-ValidatedSecurityFeaturePreflight", guard_index
                )
                mutation_index = section.index(mutation, preflight_index)
                write_path_index = section.index(mutation_flag, mutation_index)
                exit_check_index = section.index("$LASTEXITCODE", write_path_index)
                verify_index = section.index(
                    verification, exit_check_index + len("$LASTEXITCODE")
                )
                self.assertLess(guard_index, preflight_index)
                self.assertLess(preflight_index, mutation_index)
                self.assertLess(mutation_index, exit_check_index)
                self.assertLess(exit_check_index, verify_index)

        dependabot = security.split("- **Dependabot alerts**", 1)[1].split(
            "- **Secret scanning + push protection**", 1
        )[0]
        self.assertIn("$fixState.enabled -ne $true", dependabot)
        self.assertIn("$fixState.paused -ne $false", dependabot)

        push_protection = security.split("- **Secret scanning + push protection**", 1)[
            1
        ].split("- **CodeQL advanced setup**", 1)[0]
        self.assertIn(
            '$pushProtectionState.security_and_analysis.secret_scanning.status -cne "enabled"',
            push_protection,
        )
        self.assertIn(
            '$pushProtectionState.security_and_analysis.secret_scanning_push_protection.status -cne "enabled"',
            push_protection,
        )


if __name__ == "__main__":
    unittest.main()
