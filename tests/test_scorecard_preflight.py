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

SCRIPT_PATH = SCRIPT_DIRECTORY / "scorecard_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.scorecard_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load scorecard_preflight.py")
scorecard_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scorecard_preflight
SPEC.loader.exec_module(scorecard_preflight)


class FakeClient:
    repository_response: object = {}
    actions_response: object = {"enabled": True}

    def __init__(self, hostname: str) -> None:
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
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def repository(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "full_name": "octo/example",
        "id": 42,
        "archived": False,
        "disabled": False,
        "visibility": "public",
        "owner": {"type": "Organization"},
        "security_and_analysis": {"advanced_security": {"status": "enabled"}},
    }
    value.update(overrides)
    return value


class ScorecardPreflightTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_documented_install_consumers_require_bound_current_identity(self) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        identity = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert identity is not None
        prelude = r"""
$ErrorActionPreference='Stop'
$SELECTED_REPOSITORY_ID=42
$REPO_SCAFFOLD_SKILL_ROOT=$env:SKILL_ROOT
$global:LASTEXITCODE=0
function python { $global:LASTEXITCODE=0; return (Get-Content -Raw -LiteralPath $env:FIXTURE) }
"""
        postlude = "\n$copies += 1\n} catch { $failure=$_.Exception.Message }\n@{copies=$copies;failure=$failure} | ConvertTo-Json -Compress\n"
        for marker, script_name, decision in (
            (
                "- **Scorecard SARIF upload**:",
                "scorecard_preflight.py",
                "may-install-scorecard-workflow",
            ),
            (
                "- **Dependency review workflow**:",
                "dependency_review_preflight.py",
                "may-install-dependency-review-workflow",
            ),
        ):
            section = reference.split(marker, 1)[1]
            block = re.search(r"```powershell\n(.*?)\n\s*```", section, re.DOTALL)
            assert block is not None
            consumer = "\n".join(
                line[2:] if line.startswith("  ") else line
                for line in block[1].splitlines()
            )
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                scripts = root / "scripts"
                scripts.mkdir()
                shutil.copyfile(SCRIPT_DIRECTORY / script_name, scripts / script_name)
                command = root / "consumer.ps1"
                command.write_bytes(
                    (
                        prelude
                        + identity[0]
                        + "\n$copies=0; $failure=$null\ntry {\n"
                        + consumer
                        + postlude
                    ).encode("utf-8-sig")
                )
                cases = (
                    (42, decision, True, "OWNER/REPO", 1),
                    (43, decision, True, "OWNER/REPO", 0),
                    (None, decision, True, "OWNER/REPO", 0),
                    ("42", decision, True, "OWNER/REPO", 0),
                    (True, decision, True, "OWNER/REPO", 0),
                    (
                        42,
                        "bind-repository-identity-before-mutation",
                        True,
                        "OWNER/REPO",
                        0,
                    ),
                    (42, decision, False, "OWNER/REPO", 0),
                    (42, decision, True, "other/target", 0),
                    (42, decision, True, ["OWNER/REPO"], 0),
                )
                base_cases = [(*case, {}) for case in cases]
                base_cases.extend(
                    (
                        42,
                        decision,
                        True,
                        "OWNER/REPO",
                        0,
                        overrides,
                    )
                    for overrides in (
                        {"decision": [decision]},
                        {"array_root": True},
                        {"inspection_complete": "true"},
                    )
                )
                if script_name == "dependency_review_preflight.py":
                    base_cases.extend(
                        (42, decision, True, "OWNER/REPO", expected, overrides)
                        for overrides, expected in (
                            ({"dependency_graph_available": False}, 0),
                            ({"dependency_graph_available": "true"}, 0),
                            ({"dependency_graph_available": None}, 0),
                            ({"visibility": "unknown"}, 0),
                            ({"visibility": ["public"]}, 0),
                            ({"github_code_security": ["not-required"]}, 0),
                            ({"github_code_security": None}, 0),
                            ({"github_code_security": "enabled"}, 0),
                            ({"visibility": "private"}, 0),
                            (
                                {
                                    "visibility": "internal",
                                    "github_code_security": "disabled",
                                },
                                0,
                            ),
                            (
                                {
                                    "visibility": "private",
                                    "github_code_security": "enabled",
                                },
                                1,
                            ),
                            (
                                {
                                    "visibility": "internal",
                                    "github_code_security": "enabled",
                                },
                                1,
                            ),
                        )
                    )
                for (
                    repository_id,
                    verdict,
                    complete,
                    repository_name,
                    copies,
                    overrides,
                ) in base_cases:
                    with self.subTest(
                        producer=script_name,
                        repository_id=repository_id,
                        decision=verdict,
                        complete=complete,
                        repository=repository_name,
                        overrides=overrides,
                    ):
                        fixture = root / "verdict.json"
                        evidence: dict[str, object] = {
                            "inspection_complete": complete,
                            "repository": repository_name,
                            "repository_id": repository_id,
                            "decision": verdict,
                        }
                        if script_name == "dependency_review_preflight.py":
                            evidence.update(
                                {
                                    "dependency_graph_available": True,
                                    "visibility": "public",
                                    "github_code_security": "not-required",
                                }
                            )
                        evidence.update(
                            {
                                key: value
                                for key, value in overrides.items()
                                if key != "array_root"
                            }
                        )
                        fixture.write_text(
                            json.dumps(
                                [evidence] if overrides.get("array_root") else evidence
                            ),
                            encoding="utf-8",
                        )
                        process = subprocess.run(
                            [
                                str(
                                    shutil.which("powershell.exe")
                                    or shutil.which("pwsh")
                                ),
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
                        self.assertEqual(process.returncode, 0, process.stderr)
                        result = json.loads(
                            process.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                        )
                        self.assertEqual(result["copies"], copies, result)

    def test_multi_request_verdict_revalidates_identity_and_applicability(self) -> None:
        original = repository(id=42)
        for final in (
            repository(id=43),
            repository(id=42, full_name="other/target"),
            repository(id=42, archived=True),
            repository(id=42, disabled=True),
            repository(id=42, visibility="private"),
            None,
        ):
            client = FakeClient("github.com")
            with (
                self.subTest(final=final),
                mock.patch.object(
                    client, "json", side_effect=[original, {"enabled": True}, final]
                ),
                mock.patch.object(
                    scorecard_preflight, "GitHubClient", return_value=client
                ),
                self.assertRaisesRegex(
                    scorecard_preflight.InspectionError,
                    "repository identity|repository.*changed|revalidation",
                ),
            ):
                scorecard_preflight.run(arguments(expected_repository_id=42))

    def test_repository_identity_cannot_change_between_discovery_and_verdict(
        self,
    ) -> None:
        FakeClient.repository_response = repository(id=42)
        with mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient):
            positive = scorecard_preflight.run(arguments(expected_repository_id=42))
        self.assertEqual(positive.get("repository_id"), 42)
        for identity in (43, None, True, "42", 0, -1, 42.5):
            FakeClient.repository_response = repository(id=identity)
            with (
                self.subTest(identity=identity),
                mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient),
                self.assertRaisesRegex(
                    scorecard_preflight.InspectionError,
                    "repository ID|repository identity",
                ),
            ):
                scorecard_preflight.run(arguments(expected_repository_id=42))

    def test_unbound_inspection_returns_discovery_only_not_installation_authorization(
        self,
    ) -> None:
        FakeClient.repository_response = repository(id=42)
        with mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient):
            result = scorecard_preflight.run(arguments(expected_repository_id=None))
        self.assertEqual(result["decision"], "bind-repository-identity-before-mutation")
        self.assertEqual(result["repository_id"], 42)

    def setUp(self) -> None:
        FakeClient.repository_response = repository()
        FakeClient.actions_response = {"enabled": True}

    def test_permits_public_repository_with_actions_enabled(self) -> None:
        with mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient):
            result = scorecard_preflight.run(arguments())

        self.assertEqual(result["decision"], "may-install-scorecard-workflow")
        self.assertEqual(result["github_code_security"], "not-required")
        self.assertTrue(result["github_actions_enabled"])
        self.assertEqual(result["github_api_requests"], 3)

    def test_requires_code_security_and_actions_for_nonpublic_repository(self) -> None:
        FakeClient.repository_response = repository(
            visibility="private",
            security_and_analysis={"advanced_security": {"status": "disabled"}},
        )
        with mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient):
            result = scorecard_preflight.run(arguments())
        self.assertEqual(
            result["decision"],
            "enable-github-code-security-before-installing-scorecard",
        )
        self.assertIsNone(result["github_actions_enabled"])
        self.assertEqual(result["github_api_requests"], 1)

        FakeClient.repository_response = repository(visibility="internal")
        FakeClient.actions_response = {"enabled": False}
        with mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient):
            result = scorecard_preflight.run(arguments())
        self.assertEqual(
            result["decision"], "enable-github-actions-before-installing-scorecard"
        )
        self.assertEqual(result["github_code_security"], "enabled")
        self.assertFalse(result["github_actions_enabled"])

    def test_rejects_untrusted_repository_or_api_evidence(self) -> None:
        cases: tuple[tuple[argparse.Namespace, object, str], ...] = (
            (arguments(hostname="github.example"), repository(), "GitHub.com only"),
            (arguments(), [], "response is invalid"),
            (arguments(), repository(full_name="octo/other"), "different repository"),
            (arguments(), repository(archived=True), "Archived"),
            (arguments(), repository(disabled=True), "Disabled"),
            (arguments(), repository(visibility="unknown"), "invalid visibility"),
            (arguments(), repository(visibility=[]), "invalid visibility"),
            (
                arguments(),
                repository(visibility="private", owner={"type": "User"}),
                "eligible organization",
            ),
            (
                arguments(),
                repository(
                    visibility="private",
                    security_and_analysis={"advanced_security": {"status": "unknown"}},
                ),
                "invalid advanced_security",
            ),
        )
        for args, response, message in cases:
            FakeClient.repository_response = response
            with self.subTest(message=message):
                with mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient):
                    with self.assertRaisesRegex(
                        scorecard_preflight.InspectionError, message
                    ):
                        scorecard_preflight.run(args)

        FakeClient.repository_response = repository()
        for response, message in (
            ({}, "permissions response is invalid"),
            ([], "permissions response is invalid"),
            (scorecard_preflight.InspectionError("not found"), "not found"),
        ):
            FakeClient.actions_response = response
            with self.subTest(response=response):
                with mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient):
                    with self.assertRaisesRegex(
                        scorecard_preflight.InspectionError, message
                    ):
                        scorecard_preflight.run(arguments())

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
                    mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient),
                ):
                    result = scorecard_preflight.run(arguments())
                    self.assertEqual(
                        result["decision"],
                        "may-install-scorecard-workflow"
                        if allowed
                        else "enable-github-code-security-before-installing-scorecard",
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
                mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient),
                self.assertRaisesRegex(
                    scorecard_preflight.InspectionError, "code_security"
                ),
            ):
                scorecard_preflight.run(arguments())

    def test_helpers_cli_and_documentation_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            scorecard_preflight.InspectionError, "invalid 'archived'"
        ):
            scorecard_preflight.require_boolean({}, "archived")
        with self.assertRaisesRegex(
            scorecard_preflight.InspectionError, "invalid advanced_security"
        ):
            scorecard_preflight.code_security_status({})

        with (
            mock.patch.object(
                scorecard_preflight, "parse_args", return_value=arguments()
            ),
            mock.patch.object(scorecard_preflight, "GitHubClient", FakeClient),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(scorecard_preflight.main(), 0)
        self.assertIn("may-install", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                scorecard_preflight,
                "parse_args",
                side_effect=scorecard_preflight.InspectionError("blocked"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(scorecard_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

        with mock.patch.object(
            sys,
            "argv",
            ["scorecard_preflight.py", "--repository", "octo/example"],
        ):
            self.assertEqual(
                scorecard_preflight.parse_args().repository, "octo/example"
            )
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        self.assertIn("scorecard_preflight.py", skill)
        self.assertIn("scorecard_preflight.py", setup)
        self.assertIn('scorecardPreflightResult.repository, "OWNER/REPO"', setup)
        self.assertIn("advanced_security", SCRIPT_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
