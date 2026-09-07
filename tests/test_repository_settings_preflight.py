from __future__ import annotations

import argparse
import importlib.util
import re
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

SCRIPT_PATH = SCRIPT_DIRECTORY / "repository_settings_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.repository_settings_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load repository_settings_preflight.py")
repository_settings_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repository_settings_preflight
SPEC.loader.exec_module(repository_settings_preflight)


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
        "description": False,
        "description_value": None,
        "topics": False,
        "topic": [],
        "create_label": [],
        "issues": False,
        "discussions": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def repository(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "full_name": "octo/example",
        "archived": False,
        "disabled": False,
        "permissions": {"admin": True},
        "has_issues": False,
        "has_discussions": False,
    }
    value.update(overrides)
    return value


class RepositorySettingsPreflightTests(unittest.TestCase):
    def test_returns_input_bound_plan_for_verified_repository(self) -> None:
        FakeClient.response = repository()
        args = arguments(
            description=True,
            description_value="A project",
            topics=True,
            topic=["python", "github-actions"],
            issues=True,
            discussions=True,
            create_label=["bug", "good first issue"],
        )
        with mock.patch.object(
            repository_settings_preflight, "GitHubClient", FakeClient
        ):
            result = repository_settings_preflight.run(args)

        self.assertEqual(result["decision"], "may-configure-repository-settings")
        self.assertEqual(
            result["requested_mutations"],
            ["description", "topics", "issues", "discussions", "labels"],
        )
        self.assertEqual(
            result["requested_settings"],
            {
                "description": "A project",
                "topics": ["python", "github-actions"],
                "labels": ["bug", "good first issue"],
                "issues": True,
                "discussions": True,
            },
        )
        self.assertEqual(
            result["current_features"], {"issues": False, "discussions": False}
        )
        self.assertEqual(result["github_api_requests"], 1)

    def test_rejects_invalid_requests_before_remote_access(self) -> None:
        cases: list[tuple[argparse.Namespace, str]] = [
            (arguments(), "Select at least one"),
            (arguments(description=True), "non-empty description"),
            (arguments(topics=True), "at least one topic"),
            (arguments(topic=["python"]), "without requesting"),
            (arguments(topics=True, topic=["two words"]), "single tokens"),
            (arguments(topics=True, topic=["Python", "python"]), "unique"),
            (arguments(topics=True, topic=[1]), "Topics must be strings"),
            (arguments(create_label=["bug", "BUG"]), "Labels must be unique"),
            (arguments(create_label=[" "]), "Labels must be non-empty"),
        ]
        for args, message in cases:
            with self.subTest(args=args):
                with self.assertRaisesRegex(
                    repository_settings_preflight.InspectionError, message
                ):
                    repository_settings_preflight.requested_mutations(args)

    def test_rejects_invalid_host_identity_state_and_permissions(self) -> None:
        cases: list[tuple[argparse.Namespace, object | None, str]] = [
            (
                arguments(hostname="github.example", issues=True),
                None,
                "GitHub.com only",
            ),
            (arguments(issues=True), [], "response is invalid"),
            (
                arguments(issues=True),
                repository(full_name="octo/other"),
                "different repository",
            ),
            (arguments(issues=True), repository(archived=True), "Archived"),
            (arguments(issues=True), repository(disabled=True), "Disabled"),
            (
                arguments(issues=True),
                repository(permissions={}),
                "administration permission",
            ),
            (
                arguments(issues=True),
                repository(permissions={"admin": False}),
                "administration permission",
            ),
            (
                arguments(issues=True),
                repository(permissions={"admin": "yes"}),
                "invalid 'admin'",
            ),
            (
                arguments(issues=True),
                repository(has_issues="yes"),
                "invalid 'has_issues'",
            ),
        ]
        for args, response, message in cases:
            with self.subTest(args=args, response=response):
                if response is not None:
                    FakeClient.response = response
                with mock.patch.object(
                    repository_settings_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        repository_settings_preflight.InspectionError, message
                    ):
                        repository_settings_preflight.run(args)

    def test_cli_reports_success_and_inconclusive_result(self) -> None:
        FakeClient.response = repository()
        with (
            mock.patch.object(
                repository_settings_preflight,
                "parse_args",
                return_value=arguments(issues=True),
            ),
            mock.patch.object(
                repository_settings_preflight, "GitHubClient", FakeClient
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(repository_settings_preflight.main(), 0)
        self.assertIn("may-configure", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                repository_settings_preflight,
                "parse_args",
                side_effect=repository_settings_preflight.InspectionError("blocked"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(repository_settings_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

    def test_cli_parser_and_module_entrypoint(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                "repository_settings_preflight.py",
                "--repository",
                "octo/example",
                "--set-description",
                "--description",
                "A project",
                "--set-topics",
                "--topic",
                "python",
                "--enable-issues",
                "--create-label",
                "bug",
            ],
        ):
            args = repository_settings_preflight.parse_args()
        self.assertTrue(args.description)
        self.assertEqual(args.topic, ["python"])
        self.assertTrue(args.issues)
        self.assertEqual(args.create_label, ["bug"])
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    def test_skill_and_reference_require_preflight_before_mutation(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        self.assertIn("repository_settings_preflight.py", skill)
        metadata = setup.split("## Description and topics", 1)[1].split("\n## ", 1)[0]
        communication = setup.split("## Repository communication features", 1)[1].split(
            "\n## ", 1
        )[0]
        labels = setup.split("## Labels", 1)[1].split("\n## ", 1)[0]
        self.assertIn("repository_settings_preflight.py", metadata)
        self.assertIn("repository_settings_preflight.py", communication)
        self.assertIn("repository_settings_preflight.py", labels)
        self.assertIn("--create-label", labels)
        self.assertIn("requested_settings", metadata)
        self.assertIn("requested_settings", communication)
        self.assertIn("requested_settings", labels)
        self.assertIn("approvedLabelSettings", labels)
        planned = re.search(
            r"\$plannedLabelNames = @\((?P<names>.*?)\n\)", labels, re.DOTALL
        )
        self.assertIsNotNone(planned)
        assert planned is not None
        for label in re.findall(r'Add-LabelIfMissing "([^"]+)"', labels):
            self.assertIn(f'"{label}"', planned.group("names"))


if __name__ == "__main__":
    unittest.main()
