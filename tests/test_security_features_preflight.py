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


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "hostname": "github.com",
        "repository": "octo/example",
        "dependabot_alerts": False,
        "automated_security_fixes": False,
        "secret_scanning": False,
        "push_protection": False,
        "private_vulnerability_reporting": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def repository(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "full_name": "octo/example",
        "archived": False,
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
            (repository(fork="no"), "invalid 'fork'"),
            (repository(visibility="unknown"), "invalid visibility"),
            (repository(owner={"type": "Enterprise"}), "unsupported owner"),
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
        for analysis, message in [
            (None, "no security_and_analysis"),
            ({"secret_scanning": "enabled"}, "invalid 'secret_scanning'"),
            (
                {"secret_scanning": {"status": "unknown"}},
                "invalid 'secret_scanning'",
            ),
        ]:
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
                "public non-fork",
            ),
            (
                arguments(private_vulnerability_reporting=True),
                repository(fork=True),
                "public non-fork",
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

    def test_automated_security_fixes_require_dependabot_alert_evidence(self) -> None:
        FakeClient.response = repository()
        FakeClient.raw_error = None
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            result = security_features_preflight.run(
                arguments(automated_security_fixes=True)
            )
        self.assertEqual(result["dependabot_alerts_precondition"], "verified-enabled")
        self.assertEqual(result["github_api_requests"], 2)

        FakeClient.raw_error = security_features_preflight.InspectionError("not found")
        with mock.patch.object(security_features_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                security_features_preflight.InspectionError,
                "Automated security fixes require Dependabot alerts",
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
            ],
        ):
            args = security_features_preflight.parse_args()
        self.assertTrue(args.secret_scanning)
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    def test_skill_and_reference_require_the_preflight_before_mutation(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        security = setup.split("## Security features", 1)[1].split("\n## ", 1)[0]
        self.assertIn("security_features_preflight.py", skill)
        self.assertIn("security_features_preflight.py", security)
        self.assertIn("--enable-push-protection", security)
        self.assertIn("non-fork repository", security)
        self.assertIn("Dependabot alerts before automated security fixes", security)


if __name__ == "__main__":
    unittest.main()
