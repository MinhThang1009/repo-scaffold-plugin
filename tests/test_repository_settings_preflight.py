from __future__ import annotations

import argparse
import importlib.util
import re
import runpy
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml


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
        self.assertIn("function Assert-CurrentPlannedLabels", labels)
        self.assertIn(
            "`Assert-CurrentPlannedLabels` immediately before copying it.", labels
        )
        self.assertIn('metadataPreflight.repository, "OWNER/REPO"', metadata)
        self.assertIn('communicationPreflight.repository, "OWNER/REPO"', communication)
        self.assertIn('labelPreflight.repository, "OWNER/REPO"', labels)
        self.assertIn("function Test-FreshCommunicationPreflight", communication)
        self.assertIn("$mutations[0] -cne $Feature", communication)
        self.assertLess(
            communication.index('Test-FreshCommunicationPreflight -Feature "issues"'),
            communication.index("$issuesOutput = & gh repo edit"),
        )
        self.assertLess(
            communication.index(
                'Test-FreshCommunicationPreflight -Feature "discussions"'
            ),
            communication.index("$discussionsOutput = & gh repo edit"),
        )
        planned = re.search(
            r"\$plannedLabelNames = @\((?P<names>.*?)\n\)", labels, re.DOTALL
        )
        self.assertIsNotNone(planned)
        assert planned is not None
        planned_names = set(re.findall(r'"([^"]+)"', planned.group("names")))
        mapping = re.search(
            r"\$optionalLabelNamesByAsset = \[ordered\]@\{(?P<entries>.*?)\n\}",
            labels,
            re.DOTALL,
        )
        self.assertIsNotNone(mapping)
        assert mapping is not None
        mapped_labels: set[str] = set()
        mapped_assets: set[str] = set()
        labels_by_asset: dict[str, set[str]] = {}
        for match in re.finditer(
            r'^\s+"(?P<asset>[^"]+)"\s*=\s*@\((?P<labels>[^)]*)\)',
            mapping.group("entries"),
            re.MULTILINE,
        ):
            asset_name = match.group("asset")
            asset_labels = set(re.findall(r'"([^"]+)"', match.group("labels")))
            mapped_assets.add(asset_name)
            labels_by_asset[asset_name] = asset_labels
            mapped_labels.update(asset_labels)
        allowed_assets = re.search(
            r"\$allowedOptionalLabelAssets = @\((?P<assets>.*?)\n\)",
            labels,
            re.DOTALL,
        )
        self.assertIsNotNone(allowed_assets)
        assert allowed_assets is not None
        self.assertEqual(
            mapped_assets,
            set(re.findall(r'"([^"]+)"', allowed_assets.group("assets"))),
        )
        self.assertEqual(
            mapped_labels,
            {
                "automerge",
                "ci",
                "tests",
                "feature",
                "fix",
                "ignore-for-release",
                "Stale",
                "pinned",
                "security",
            },
        )
        for label in re.findall(r'Add-LabelIfMissing "([^"]+)"', labels):
            self.assertIn(label, planned_names | mapped_labels)

        assets = PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets"
        labeler_config = yaml.safe_load(
            (assets / "labeler.yml").read_text(encoding="utf-8")
        )
        self.assertEqual(labels_by_asset["labeler.yml"], {"ci", "tests"})
        self.assertEqual(
            mapped_labels & set(labeler_config),
            {"ci", "tests"},
        )
        release_config = yaml.safe_load(
            (assets / "release-config.yml").read_text(encoding="utf-8")
        )
        configured_release_labels = set(
            release_config["changelog"]["exclude"]["labels"]
        )
        for category in release_config["changelog"]["categories"]:
            configured_release_labels.update(category["labels"])
        self.assertEqual(
            mapped_labels & configured_release_labels,
            {"feature", "fix", "ignore-for-release"},
        )
        self.assertEqual(
            labels_by_asset["release-config.yml"],
            {"feature", "fix", "ignore-for-release"},
        )
        auto_merge_workflow = (assets / "workflows" / "auto-merge.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(labels_by_asset["auto-merge.yml"], {"automerge"})
        self.assertIn("'automerge'", auto_merge_workflow)
        stale_workflow = yaml.safe_load(
            (assets / "workflows" / "stale.yml").read_text(encoding="utf-8")
        )
        stale_inputs = stale_workflow["jobs"]["stale"]["steps"][0]["with"]
        self.assertEqual(stale_inputs["stale-issue-label"], "Stale")
        self.assertEqual(stale_inputs["stale-pr-label"], "Stale")
        self.assertEqual(labels_by_asset["stale.yml"], {"Stale", "pinned", "security"})
        self.assertEqual(
            mapped_labels & set(stale_inputs["exempt-issue-labels"].split(",")),
            {"pinned", "security"},
        )

        add_label = labels.split("function Add-LabelIfMissing", 1)[1].split(
            "function Get-ValidatedLabelPreflight", 1
        )[0]
        self.assertLess(
            add_label.index("Get-ValidatedLabelPreflight -Name $Name"),
            add_label.index("gh label create $Name"),
        )
        fresh_preflight = labels.split("function Get-ValidatedLabelPreflight", 1)[
            1
        ].split('Add-LabelIfMissing "bug"', 1)[0]
        self.assertIn("--create-label $Name", fresh_preflight)
        self.assertIn('$mutations[0] -cne "labels"', fresh_preflight)
        self.assertIn("$labels[0] -cne $Name", fresh_preflight)
        self.assertIn("$finalLabelListOutput = gh api", labels)
        self.assertIn("$missingFinalLabels.Count -gt 0", labels)


if __name__ == "__main__":
    unittest.main()
