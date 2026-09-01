from __future__ import annotations

import argparse
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts"


BRANCH_SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.branch_protection_preflight",
    SCRIPT_DIRECTORY / "branch_protection_preflight.py",
)
if BRANCH_SPEC is None or BRANCH_SPEC.loader is None:
    raise RuntimeError("Could not load branch_protection_preflight.py")
branch_protection_preflight = importlib.util.module_from_spec(BRANCH_SPEC)
sys.modules[BRANCH_SPEC.name] = branch_protection_preflight
sys.modules["branch_protection_preflight"] = branch_protection_preflight
BRANCH_SPEC.loader.exec_module(branch_protection_preflight)

MERGE_SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.merge_settings_preflight",
    SCRIPT_DIRECTORY / "merge_settings_preflight.py",
)
if MERGE_SPEC is None or MERGE_SPEC.loader is None:
    raise RuntimeError("Could not load merge_settings_preflight.py")
merge_settings_preflight = importlib.util.module_from_spec(MERGE_SPEC)
sys.modules[MERGE_SPEC.name] = merge_settings_preflight
MERGE_SPEC.loader.exec_module(merge_settings_preflight)


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
        "default_branch": "main",
        "require_auto_merge_workflows": False,
        "confirm_disable_merge_methods": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class MergeSettingsPreflightTests(unittest.TestCase):
    def configure(
        self, *, rules: object, merge: bool = False, rebase: bool = False
    ) -> None:
        FakeClient.responses = {
            "repos/octo/example": {
                "full_name": "octo/example",
                "archived": False,
                "allow_squash_merge": True,
                "allow_merge_commit": merge,
                "allow_rebase_merge": rebase,
            },
            "repos/octo/example/rules/branches/main?per_page=100": rules,
        }

    def test_requires_separate_confirmation_before_disabling_methods(self) -> None:
        self.configure(rules=[], rebase=True)

        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            result = merge_settings_preflight.run(arguments())

        self.assertEqual(
            result["decision"], "require-explicit-merge-method-removal-confirmation"
        )
        self.assertEqual(result["methods_to_disable"], ["rebase"])
        self.assertEqual(
            result["desired_merge_methods"],
            {"squash": True, "merge": False, "rebase": False},
        )

    def test_returns_mutation_plan_after_explicit_method_removal_confirmation(
        self,
    ) -> None:
        self.configure(rules=[], merge=True, rebase=True)

        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            result = merge_settings_preflight.run(
                arguments(confirm_disable_merge_methods=True)
            )

        self.assertEqual(result["decision"], "may-configure-merge-settings")
        self.assertEqual(result["methods_to_disable"], ["merge", "rebase"])

    def test_queue_requires_preserving_its_method_and_skips_auto_merge_assets(
        self,
    ) -> None:
        self.configure(
            rules=[{"type": "merge_queue", "parameters": {"merge_method": "rebase"}}],
            rebase=True,
        )

        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            result = merge_settings_preflight.run(
                arguments(require_auto_merge_workflows=True)
            )

        self.assertEqual(result["decision"], "skip-auto-merge-workflows")
        self.assertTrue(result["merge_queue_applies"])
        self.assertFalse(result["auto_merge_workflows_eligible"])
        self.assertEqual(result["required_merge_methods"], ["rebase"])

    def test_rejects_invalid_effective_rule_parameters(self) -> None:
        self.configure(rules=[{"type": "pull_request", "parameters": {}}])

        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                merge_settings_preflight.InspectionError, "allowed merge-method"
            ):
                merge_settings_preflight.run(arguments())

    def test_rejects_github_repository_identity_mismatch(self) -> None:
        self.configure(rules=[])
        FakeClient.responses["repos/octo/example"] = {
            "full_name": "octo/other",
            "archived": False,
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
        }

        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                merge_settings_preflight.InspectionError, "different repository"
            ):
                merge_settings_preflight.run(arguments())
