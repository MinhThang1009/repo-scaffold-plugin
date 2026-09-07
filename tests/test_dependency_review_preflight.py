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

SCRIPT_PATH = SCRIPT_DIRECTORY / "dependency_review_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.dependency_review_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load dependency_review_preflight.py")
dependency_review_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dependency_review_preflight
SPEC.loader.exec_module(dependency_review_preflight)


class FakeClient:
    repository_response: object = {}
    sbom_response: object = {"sbom": {"SPDXID": "SPDXRef-DOCUMENT"}}

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        self.request_count = 0

    def json(self, endpoint: str) -> object:
        self.request_count += 1
        if endpoint == "repos/octo/example":
            if isinstance(self.repository_response, Exception):
                raise self.repository_response
            return self.repository_response
        if endpoint == "repos/octo/example/dependency-graph/sbom":
            if isinstance(self.sbom_response, Exception):
                raise self.sbom_response
            return self.sbom_response
        raise AssertionError(f"Unexpected endpoint: {endpoint}")


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "hostname": "github.com",
        "repository": "octo/example",
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


class DependencyReviewPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.repository_response = repository()
        FakeClient.sbom_response = {"sbom": {"SPDXID": "SPDXRef-DOCUMENT"}}

    def test_permits_public_repository_with_a_dependency_graph(self) -> None:
        with mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient):
            result = dependency_review_preflight.run(arguments())

        self.assertEqual(result["decision"], "may-install-dependency-review-workflow")
        self.assertTrue(result["dependency_graph_available"])
        self.assertEqual(result["github_code_security"], "not-required")
        self.assertEqual(result["github_api_requests"], 2)

    def test_requires_enabled_code_security_for_nonpublic_repository(self) -> None:
        FakeClient.repository_response = repository(
            visibility="private",
            security_and_analysis={"advanced_security": {"status": "disabled"}},
        )
        with mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient):
            result = dependency_review_preflight.run(arguments())
        self.assertEqual(
            result["decision"],
            "enable-github-code-security-before-installing-dependency-review",
        )
        self.assertFalse(result["dependency_graph_available"])
        self.assertEqual(result["github_api_requests"], 1)

        FakeClient.repository_response = repository(visibility="internal")
        with mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient):
            result = dependency_review_preflight.run(arguments())
        self.assertEqual(result["decision"], "may-install-dependency-review-workflow")
        self.assertEqual(result["github_code_security"], "enabled")

    def test_rejects_untrusted_repository_or_dependency_graph_evidence(self) -> None:
        cases: tuple[tuple[argparse.Namespace, object, str], ...] = (
            (arguments(hostname="github.example"), repository(), "GitHub.com only"),
            (arguments(), [], "response is invalid"),
            (arguments(), repository(full_name="octo/other"), "different repository"),
            (arguments(), repository(archived=True), "Archived"),
            (arguments(), repository(disabled=True), "Disabled"),
            (arguments(), repository(visibility="unknown"), "invalid visibility"),
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
                with mock.patch.object(
                    dependency_review_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        dependency_review_preflight.InspectionError, message
                    ):
                        dependency_review_preflight.run(args)

        FakeClient.repository_response = repository()
        for response, message in (
            ([], "invalid"),
            ({}, "no SPDXID"),
            ({"sbom": {}}, "no SPDXID"),
            (dependency_review_preflight.InspectionError("not found"), "not found"),
        ):
            FakeClient.sbom_response = response
            with self.subTest(response=response):
                with mock.patch.object(
                    dependency_review_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        dependency_review_preflight.InspectionError, message
                    ):
                        dependency_review_preflight.run(arguments())

    def test_helpers_cli_and_documentation_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            dependency_review_preflight.InspectionError, "invalid 'archived'"
        ):
            dependency_review_preflight.require_boolean({}, "archived")
        with self.assertRaisesRegex(
            dependency_review_preflight.InspectionError, "invalid advanced_security"
        ):
            dependency_review_preflight.advanced_security_status({})

        with (
            mock.patch.object(
                dependency_review_preflight, "parse_args", return_value=arguments()
            ),
            mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(dependency_review_preflight.main(), 0)
        self.assertIn("may-install", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                dependency_review_preflight,
                "parse_args",
                side_effect=dependency_review_preflight.InspectionError("blocked"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(dependency_review_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

        with mock.patch.object(
            sys,
            "argv",
            ["dependency_review_preflight.py", "--repository", "octo/example"],
        ):
            self.assertEqual(
                dependency_review_preflight.parse_args().repository, "octo/example"
            )
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        self.assertIn("dependency_review_preflight.py", skill)
        self.assertIn("dependency_review_preflight.py", setup)
        self.assertIn("dependency-graph/sbom", SCRIPT_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
