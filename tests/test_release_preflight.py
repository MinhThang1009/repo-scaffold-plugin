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

SCRIPT_PATH = SCRIPT_DIRECTORY / "release_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.release_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load release_preflight.py")
release_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_preflight
SPEC.loader.exec_module(release_preflight)


class FakeClient:
    response: object = {}

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        self.request_count = 0

    def json(self, endpoint: str) -> object:
        self.request_count += 1
        if endpoint != "repos/octo/example":
            raise AssertionError(f"Unexpected endpoint: {endpoint}")
        return self.response


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "hostname": "github.com",
        "repository": "octo/example",
        "default_branch": "main",
        "with_attestations": False,
        "github_enterprise_cloud": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def repository(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "full_name": "octo/example",
        "archived": False,
        "disabled": False,
        "fork": False,
        "visibility": "public",
        "default_branch": "main",
    }
    value.update(overrides)
    return value


class ReleasePreflightTests(unittest.TestCase):
    def test_accepts_verified_public_repository_for_attestations(self) -> None:
        FakeClient.response = repository()
        with mock.patch.object(release_preflight, "GitHubClient", FakeClient):
            result = release_preflight.run(arguments(with_attestations=True))

        self.assertEqual(result["decision"], "may-install-attestation-workflows")
        self.assertEqual(result["github_api_requests"], 1)
        self.assertFalse(result["is_fork"])

    def test_requires_enterprise_cloud_confirmation_for_nonpublic_attestations(
        self,
    ) -> None:
        FakeClient.response = repository(visibility="private")
        with mock.patch.object(release_preflight, "GitHubClient", FakeClient):
            result = release_preflight.run(arguments(with_attestations=True))
        self.assertEqual(result["decision"], "render-no-attestation-variant")

        with mock.patch.object(release_preflight, "GitHubClient", FakeClient):
            result = release_preflight.run(
                arguments(with_attestations=True, github_enterprise_cloud=True)
            )
        self.assertEqual(result["decision"], "may-install-attestation-workflows")

    def test_allows_release_workflows_without_an_attestation_request(self) -> None:
        FakeClient.response = repository(visibility="internal", fork=True)
        with mock.patch.object(release_preflight, "GitHubClient", FakeClient):
            result = release_preflight.run(arguments())
        self.assertEqual(result["decision"], "may-install-release-workflows")
        self.assertTrue(result["is_fork"])

    def test_rejects_untrusted_or_unwritable_repository_state(self) -> None:
        cases: tuple[tuple[argparse.Namespace, object, str], ...] = (
            (arguments(hostname="github.example"), repository(), "GitHub.com only"),
            (arguments(default_branch=""), repository(), "non-empty"),
            (arguments(), [], "response is invalid"),
            (arguments(), repository(full_name="octo/other"), "different repository"),
            (arguments(), repository(archived=True), "Archived"),
            (arguments(), repository(disabled=True), "Disabled"),
            (arguments(), repository(default_branch="trunk"), "does not match"),
            (arguments(), repository(visibility="unknown"), "invalid visibility"),
            (arguments(), repository(fork="no"), "invalid 'fork'"),
            (
                arguments(),
                repository(default_branch="main\nnext"),
                "invalid default branch",
            ),
        )
        for args, response, message in cases:
            FakeClient.response = response
            with self.subTest(message=message):
                with mock.patch.object(release_preflight, "GitHubClient", FakeClient):
                    with self.assertRaisesRegex(
                        release_preflight.InspectionError, message
                    ):
                        release_preflight.run(args)

    def test_helpers_and_cli_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            release_preflight.InspectionError, "invalid 'archived'"
        ):
            release_preflight.require_boolean({}, "archived")
        with self.assertRaisesRegex(
            release_preflight.InspectionError, "invalid default"
        ):
            release_preflight.default_branch({})
        self.assertEqual(
            release_preflight.attestation_decision("public", True, False),
            "may-install-attestation-workflows",
        )

        FakeClient.response = repository()
        with (
            mock.patch.object(
                release_preflight, "parse_args", return_value=arguments()
            ),
            mock.patch.object(release_preflight, "GitHubClient", FakeClient),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(release_preflight.main(), 0)
        self.assertIn("may-install", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                release_preflight,
                "parse_args",
                side_effect=release_preflight.InspectionError("blocked"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(release_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

    def test_parser_entrypoint_and_documentation_require_preflight(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                "release_preflight.py",
                "--repository",
                "octo/example",
                "--default-branch",
                "main",
                "--with-attestations",
            ],
        ):
            args = release_preflight.parse_args()
        self.assertTrue(args.with_attestations)
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        release = setup.split("## release-please token", 1)[1].split("\n## ", 1)[0]
        self.assertIn("release_preflight.py", skill)
        self.assertIn("release_preflight.py", release)
        self.assertIn("render-no-attestation-variant", release)


if __name__ == "__main__":
    unittest.main()
