from __future__ import annotations

import argparse
import importlib.util
import runpy
import sys
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
        "repo_root": ".",
        "default_branch": "main",
        "confirm_no_external_codeql": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def repository(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "full_name": "octo/example",
        "archived": False,
        "disabled": False,
        "visibility": "public",
        "owner": {"type": "Organization"},
        "security_and_analysis": {"advanced_security": {"status": "enabled"}},
    }
    value.update(overrides)
    return value


class AdvancedCodeqlPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.repository_response = repository()
        FakeClient.actions_response = {"enabled": True}

    def test_permits_eligible_repository_without_existing_codeql_setup(self) -> None:
        inspection = {
            "inspection_complete": True,
            "decision": "may-offer-default-setup",
            "github_api_requests": 7,
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
        self.assertEqual(result["github_code_security"], "not-required")
        self.assertTrue(result["github_actions_enabled"])
        self.assertEqual(result["github_api_requests"], 9)
        shared_arguments = shared_run.call_args.args[0]
        self.assertEqual(shared_arguments.repository, "octo/example")
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
                            "decision": "may-offer-default-setup",
                            "github_api_requests": 0,
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
                    "decision": "may-offer-default-setup",
                    "github_api_requests": 0,
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


if __name__ == "__main__":
    unittest.main()
