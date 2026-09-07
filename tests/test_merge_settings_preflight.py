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
        self,
        *,
        rules: object,
        merge: bool = False,
        rebase: bool = False,
        auto_merge: bool = True,
    ) -> None:
        FakeClient.responses = {
            "repos/octo/example": {
                "full_name": "octo/example",
                "archived": False,
                "allow_squash_merge": True,
                "allow_merge_commit": merge,
                "allow_rebase_merge": rebase,
                "allow_auto_merge": auto_merge,
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

    def test_auto_merge_workflows_require_enabled_repository_capability(self) -> None:
        self.configure(rules=[], auto_merge=False)

        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            blocked = merge_settings_preflight.run(
                arguments(require_auto_merge_workflows=True)
            )

        self.assertEqual(
            blocked["decision"], "enable-auto-merge-before-installing-workflows"
        )
        self.assertFalse(blocked["auto_merge_enabled"])
        self.assertFalse(blocked["auto_merge_workflows_eligible"])

        self.configure(rules=[], auto_merge=True)
        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            ready = merge_settings_preflight.run(
                arguments(require_auto_merge_workflows=True)
            )

        self.assertEqual(ready["decision"], "may-configure-merge-settings")
        self.assertTrue(ready["auto_merge_enabled"])
        self.assertTrue(ready["auto_merge_workflows_eligible"])

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

    def test_rule_parser_and_boolean_validation_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            merge_settings_preflight.InspectionError, "invalid 'archived'"
        ):
            merge_settings_preflight.require_boolean({}, "archived")

        cases = [
            ({}, "response is invalid"),
            ([{}] * 100, "may be paginated"),
            (["rule"], "invalid rule"),
            ([{"type": "other"}], None),
            ([{"type": "merge_queue"}], "no parameters"),
            (
                [{"type": "merge_queue", "parameters": {"merge_method": "bad"}}],
                "unsupported merge method",
            ),
            ([{"type": "pull_request", "parameters": {}}], "allowed merge-method"),
            (
                [
                    {
                        "type": "pull_request",
                        "parameters": {"allowed_merge_methods": ["bad"]},
                    }
                ],
                "unsupported merge method",
            ),
        ]
        for payload, message in cases:
            with self.subTest(payload=payload):
                if message is None:
                    self.assertEqual(
                        merge_settings_preflight.parse_effective_rules(payload),
                        (set(), False),
                    )
                else:
                    with self.assertRaisesRegex(
                        merge_settings_preflight.InspectionError, message
                    ):
                        merge_settings_preflight.parse_effective_rules(payload)

        methods, queue = merge_settings_preflight.parse_effective_rules(
            [
                {
                    "type": "pull_request",
                    "parameters": {"allowed_merge_methods": ["Merge", "squash"]},
                },
                {
                    "type": "merge_queue",
                    "parameters": {"merge_method": "rebase"},
                },
            ]
        )
        self.assertEqual(methods, {"merge", "squash", "rebase"})
        self.assertTrue(queue)

    def test_run_rejects_invalid_repository_and_arguments(self) -> None:
        for overrides, message in [
            ({"hostname": "github.example"}, "GitHub.com only"),
            ({"default_branch": " "}, "Default branch"),
        ]:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(
                    merge_settings_preflight.InspectionError, message
                ):
                    merge_settings_preflight.run(arguments(**overrides))

        self.configure(rules=[])
        for repository, message in [
            ([], "response is invalid"),
            (
                {
                    "full_name": "octo/example",
                    "archived": True,
                    "allow_squash_merge": True,
                    "allow_merge_commit": False,
                    "allow_rebase_merge": False,
                    "allow_auto_merge": True,
                },
                "Archived",
            ),
            (
                {
                    "full_name": "octo/example",
                    "archived": False,
                    "allow_squash_merge": "yes",
                    "allow_merge_commit": False,
                    "allow_rebase_merge": False,
                    "allow_auto_merge": True,
                },
                "allow_squash_merge",
            ),
            (
                {
                    "full_name": "octo/example",
                    "archived": False,
                    "allow_squash_merge": True,
                    "allow_merge_commit": False,
                    "allow_rebase_merge": False,
                    "allow_auto_merge": "yes",
                },
                "allow_auto_merge",
            ),
        ]:
            FakeClient.responses["repos/octo/example"] = repository
            with self.subTest(repository=repository):
                with mock.patch.object(
                    merge_settings_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        merge_settings_preflight.InspectionError, message
                    ):
                        merge_settings_preflight.run(arguments())

    def test_cli_reports_success_and_inconclusive_result(self) -> None:
        self.configure(rules=[])
        with (
            mock.patch.object(
                merge_settings_preflight, "parse_args", return_value=arguments()
            ),
            mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(merge_settings_preflight.main(), 0)
        self.assertIn("may-configure", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                merge_settings_preflight,
                "parse_args",
                side_effect=merge_settings_preflight.InspectionError("bad input"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(merge_settings_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

    def test_module_entrypoint_exits_for_invalid_cli_arguments(self) -> None:
        with self.assertRaises(SystemExit):
            runpy.run_path(
                str(SCRIPT_DIRECTORY / "merge_settings_preflight.py"),
                run_name="__main__",
            )
