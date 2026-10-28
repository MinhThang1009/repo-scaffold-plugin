from __future__ import annotations

import argparse
import copy
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
        "expected_repository_id": 42,
        "default_branch": "main",
        "require_auto_merge_workflows": False,
        "confirm_disable_merge_methods": False,
        "enable_delete_branch_on_merge": False,
        "squash_merge_commit_title": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class MergeSettingsPreflightTests(unittest.TestCase):
    def test_boolean_caller_controls_reject_substitutes_before_client_creation(
        self,
    ) -> None:
        values: tuple[object, ...] = (None, "false", "true", 0, 1, [], {}, [True])
        for control in (
            "require_auto_merge_workflows",
            "confirm_disable_merge_methods",
        ):
            for value in values:
                with (
                    self.subTest(control=control, value=value),
                    mock.patch.object(
                        merge_settings_preflight, "GitHubClient"
                    ) as client,
                    self.assertRaisesRegex(
                        merge_settings_preflight.InspectionError, "Boolean"
                    ),
                ):
                    merge_settings_preflight.run(arguments(**{control: value}))
                client.assert_not_called()

    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_auto_merge_asset_copy_guard_rejects_malformed_consent_and_eligibility(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        section = reference.split(
            "Run the exact workflow-installation preflight for the selected asset first.",
            1,
        )[1]
        block = re.search(r"```powershell\n(.*?)\n```", section, re.DOTALL)
        assert block is not None
        variants: tuple[tuple[object, object, int], ...] = (
            (True, True, 1),
            (False, True, 0),
            (True, False, 0),
            ("false", True, 0),
            ([True], True, 0),
            (True, "true", 0),
            (True, 1, 0),
            (None, True, 0),
        )
        for requested, eligible, calls in variants:
            with (
                self.subTest(requested=requested, eligible=eligible),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                fixture = root / "copy-consent.json"
                fixture.write_text(
                    json.dumps({"requested": requested, "eligible": eligible}),
                    encoding="utf-8",
                )
                prefix = "$ErrorActionPreference='Stop'\n$packet=Get-Content -Raw -LiteralPath $env:FIXTURE | ConvertFrom-Json\n$installAutoMergeAssetsRequested=$packet.requested\n$installAutoMergeWorkflows=$packet.eligible\n$calls=0;$failure=$null\nfunction Get-ValidatedFreshMergePreflight { $script:calls++;return @{auto_merge_workflows_eligible=$true} }\ntry {\n"
                suffix = "\n} catch {$failure=$_.Exception.Message}\n@{calls=$calls;failure=$failure} | ConvertTo-Json -Compress\n"
                command = root / "copy.ps1"
                command.write_bytes((prefix + block[1] + suffix).encode("utf-8-sig"))
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
                self.assertEqual(observed["calls"], calls, observed)
                self.assertEqual(
                    observed["failure"] is None,
                    isinstance(requested, bool) and isinstance(eligible, bool),
                    observed,
                )

    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_auto_merge_capability_and_asset_consent_are_not_truthiness(self) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        start = "if ($installAutoMergeAssetsRequested -isnot [bool] -or\n"
        fragment = (
            start
            + reference.split(start, 1)[1].split(
                "\nfunction Get-ValidatedFreshMergePreflight", 1
            )[0]
        )
        variants: tuple[tuple[object, object, bool, bool], ...] = (
            (True, True, True, False),
            (True, False, False, False),
            (False, True, False, False),
            (False, False, False, False),
            ("false", True, False, True),
            (True, "false", False, True),
            ([True], True, False, True),
            (True, 1, False, True),
            (None, True, False, True),
        )
        for requested, consent, enabled, error in variants:
            with (
                self.subTest(requested=requested, consent=consent),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                fixture = root / "consent.json"
                fixture.write_text(
                    json.dumps({"requested": requested, "consent": consent}),
                    encoding="utf-8",
                )
                prefix = "$ErrorActionPreference='Stop'\n$packet=Get-Content -Raw -LiteralPath $env:FIXTURE | ConvertFrom-Json\n$installAutoMergeAssetsRequested=$packet.requested\n$autoMergeCapabilityEnableApproved=$packet.consent\n$finalMergePreflight=@{auto_merge_enabled=$false}\n$enableAutoMergeNow=$false\n$failure=$null\ntry {\n"
                suffix = "\n} catch {$failure=$_.Exception.Message}\n@{enable=$enableAutoMergeNow;failure=$failure} | ConvertTo-Json -Compress\n"
                command = root / "merge-consent.ps1"
                command.write_bytes((prefix + fragment + suffix).encode("utf-8-sig"))
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
                self.assertEqual(observed["enable"], enabled, observed)
                self.assertEqual(observed["failure"] is not None, error, observed)

    def test_multi_read_merge_verdict_rejects_target_policy_and_classic_gate_drift(
        self,
    ) -> None:
        for changed_endpoint, delta in (
            (
                "repos/octo/example/branches/main",
                {
                    "name": "main",
                    "protected": True,
                    "protection": {
                        "required_status_checks": {"contexts": ["other-gate"]}
                    },
                },
            ),
            ("repos/octo/example", {"id": 43}),
            ("repos/octo/example", {"archived": True}),
            ("repos/octo/example", {"permissions": {"admin": False}}),
            ("repos/octo/example", {"allow_auto_merge": False}),
            ("repos/octo/example", {"allow_rebase_merge": True}),
            (
                "repos/octo/example/rules/branches/main?per_page=100",
                [
                    {
                        "type": "pull_request",
                        "parameters": {"allowed_merge_methods": ["merge"]},
                    }
                ],
            ),
            (
                "repos/octo/example/branches/main",
                {
                    "name": "main",
                    "protected": False,
                    "protection": {"required_status_checks": {"contexts": []}},
                },
            ),
        ):
            self.configure(
                rules=[],
                branch={
                    "name": "main",
                    "protected": True,
                    "protection": {
                        "required_status_checks": {"contexts": ["ci-success"]}
                    },
                },
            )

            class ChangingClient(FakeClient):
                def json(self, endpoint: str) -> object:
                    value = super().json(endpoint)
                    if (
                        endpoint == "repos/octo/example/branches/main"
                        and self.request_count == 3
                    ):
                        original = self.responses[changed_endpoint]
                        self.responses[changed_endpoint] = (
                            {**original, **delta}
                            if isinstance(original, dict) and isinstance(delta, dict)
                            else delta
                        )
                    return value

            with (
                self.subTest(endpoint=changed_endpoint, delta=delta),
                mock.patch.object(
                    merge_settings_preflight, "GitHubClient", ChangingClient
                ),
                self.assertRaises(merge_settings_preflight.InspectionError),
            ):
                merge_settings_preflight.run(
                    arguments(require_auto_merge_workflows=True)
                )

    def test_unbound_identity_inspection_does_not_authorize_a_mutation(self) -> None:
        self.configure(rules=[])
        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            result = merge_settings_preflight.run(
                arguments(expected_repository_id=None)
            )
        self.assertEqual(result["decision"], "bind-repository-identity-before-mutation")
        self.assertEqual(result["repository_id"], 42)

    def test_repository_id_binds_repeated_inspection_to_the_selected_target(
        self,
    ) -> None:
        self.configure(rules=[])
        response = FakeClient.responses["repos/octo/example"]
        assert isinstance(response, dict)
        response["id"] = 42
        args = arguments(expected_repository_id=42)
        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            result = merge_settings_preflight.run(args)
        self.assertEqual(result.get("repository_id"), 42)
        for changed in (43, None, True, "42", 0, -1, 42.5):
            with self.subTest(identity=changed):
                response["id"] = changed
                with (
                    mock.patch.object(
                        merge_settings_preflight, "GitHubClient", FakeClient
                    ),
                    self.assertRaisesRegex(
                        merge_settings_preflight.InspectionError,
                        "repository ID|repository identity",
                    ),
                ):
                    merge_settings_preflight.run(args)

    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_fresh_merge_verdict_is_typed_before_authorizing_next_mutation(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        function = (
            "function Get-ValidatedFreshMergePreflight {"
            + reference.split("function Get-ValidatedFreshMergePreflight {", 1)[
                1
            ].split("\n$mergeArguments =", 1)[0]
        )
        identity_helper = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert identity_helper is not None
        schema = "\n$SELECTED_REPOSITORY_ID=42\n" + identity_helper[0] + "\n"
        if "function Assert-MergePreflightSchema {" in reference:
            schema += (
                "function Assert-MergePreflightSchema {"
                + reference.split("function Assert-MergePreflightSchema {", 1)[1].split(
                    "\n$preflightOutput =", 1
                )[0]
            )
        valid: dict[str, object] = {
            "inspection_complete": True,
            "decision": "may-configure-merge-settings",
            "repository": "OWNER/REPO",
            "repository_id": 42,
            "default_branch": "main",
            "administration_permission": True,
            "auto_merge_enabled": True,
            "auto_merge_workflows_eligible": True,
            "merge_queue_applies": False,
            "ruleset_status_checks_required": True,
            "classic_status_checks_required": None,
            "status_checks_required": True,
            "required_merge_methods": ["squash"],
            "methods_to_disable": [],
            "desired_merge_methods": {"squash": True, "merge": False, "rebase": False},
            "current_merge_methods": {"squash": True, "merge": False, "rebase": False},
            "requested_settings": {
                "delete_branch_on_merge": True,
                "squash_merge_commit_title": "PR_TITLE",
            },
            "current_settings": {
                "delete_branch_on_merge": True,
                "squash_merge_commit_title": "PR_TITLE",
            },
        }
        cases: list[tuple[str, dict[str, object], bool]] = [("positive", valid, True)]
        for field in (
            "auto_merge_enabled",
            "auto_merge_workflows_eligible",
            "administration_permission",
            "inspection_complete",
            "merge_queue_applies",
            "ruleset_status_checks_required",
            "classic_status_checks_required",
            "status_checks_required",
        ):
            cases.append((field, {**valid, field: "false"}, False))
        cases.append(
            (
                "current-delete",
                {
                    **valid,
                    "current_settings": {
                        "delete_branch_on_merge": "false",
                        "squash_merge_commit_title": "PR_TITLE",
                    },
                },
                False,
            )
        )
        cases.append(
            ("repository-array", {**valid, "repository": ["OWNER/REPO"]}, False)
        )
        for mapping in ("current_merge_methods", "desired_merge_methods"):
            for method in ("squash", "merge", "rebase"):
                malformed = copy.deepcopy(valid)
                method_state = malformed[mapping]
                assert isinstance(method_state, dict)
                method_state[method] = "false"
                cases.append((f"{mapping}:{method}", malformed, False))
        for field in ("required_merge_methods", "methods_to_disable"):
            for invalid_inventory in (
                None,
                "squash",
                ["squash", "squash"],
                ["unknown"],
                [1],
            ):
                cases.append(
                    (
                        f"{field}:{invalid_inventory}",
                        {**valid, field: invalid_inventory},
                        False,
                    )
                )
        for field in ("classic_status_checks_required", "status_checks_required"):
            cases.append(
                (
                    f"missing:{field}",
                    {key: value for key, value in valid.items() if key != field},
                    False,
                )
            )
        prelude = r"""
$ErrorActionPreference='Stop'
$DEFAULT_BRANCH='main'
$mergeSettingsPreflight='synthetic'
$preflightArguments=@()
$finalMergePreflight=(Get-Content -Raw -LiteralPath $env:APPROVED) | ConvertFrom-Json
function python { $global:LASTEXITCODE=0; return (Get-Content -Raw -LiteralPath $env:FIXTURE) }
"""
        postlude = r"""
$failure=$null; $mutations=0
try {
 $result=Get-ValidatedFreshMergePreflight -ExpectedAutoMergeEnabled $true -ExpectedDeleteBranchOnMerge $true -ExpectedSquashMergeCommitTitle 'PR_TITLE' -RequireDesiredMergeMethods
 $mutations += 1
} catch { $failure=$_.Exception.Message }
@{failure=$failure;mutations=$mutations} | ConvertTo-Json -Compress
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "consumer.ps1"
            script.write_bytes(
                (prelude + schema + function + postlude).encode("utf-8-sig")
            )
            approved = root / "approved.json"
            approved.write_text(json.dumps(valid), encoding="utf-8")
            for label, state, accepted in cases:
                with self.subTest(case=label):
                    fixture = root / "fixture.json"
                    fixture.write_text(json.dumps(state), encoding="utf-8")
                    environment = {
                        **os.environ,
                        "APPROVED": str(approved),
                        "FIXTURE": str(fixture),
                    }
                    process = subprocess.run(
                        [
                            str(shutil.which("powershell.exe") or shutil.which("pwsh")),
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
                    self.assertEqual(process.returncode, 0, process.stderr)
                    result = json.loads(
                        process.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                    )
                    self.assertEqual(result["mutations"], 1 if accepted else 0, result)

    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_final_merge_readback_requires_bound_identity_and_typed_settings(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        beginning = (
            "$finalMergeSettings = ($finalMergeOutput | Out-String) | ConvertFrom-Json"
        )
        consumer = (
            beginning
            + reference.split(beginning, 1)[1].split(
                "$postMergePreflightOutput = python", 1
            )[0]
        )
        identity_helper = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert identity_helper is not None
        prelude = r"""
$ErrorActionPreference='Stop'
$mergeSettingsFailure=$null
$completedMergeUpdates=@()
$enableMergeCommit=$true
$enableRebaseMerge=$false
$expectedAutoMergeEnabled=$true
$finalMergeOutput=Get-Content -Raw -LiteralPath $env:FIXTURE
$failure=$null
try {
"""
        postlude = "\n} catch { $failure=$_.Exception.Message }\n@{failure=$failure} | ConvertTo-Json -Compress\n"
        valid: dict[str, object] = {
            "full_name": "OWNER/REPO",
            "id": 42,
            "allow_squash_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
            "allow_auto_merge": True,
            "squash_merge_commit_title": "PR_TITLE",
        }
        cases: list[tuple[str, dict[str, object], bool]] = [
            ("positive", valid, True),
            ("different-repository", {**valid, "full_name": "other/target"}, False),
            (
                "missing-repository",
                {key: value for key, value in valid.items() if key != "full_name"},
                False,
            ),
            (
                "nontext-title",
                {**valid, "squash_merge_commit_title": ["PR_TITLE"]},
                False,
            ),
        ]
        for field in (
            "allow_squash_merge",
            "allow_merge_commit",
            "allow_rebase_merge",
            "delete_branch_on_merge",
            "allow_auto_merge",
        ):
            for value in ("false", "true", 1, None):
                cases.append((f"{field}:{value}", {**valid, field: value}, False))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "consumer.ps1"
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
            for label, state, accepted in cases:
                with self.subTest(case=label):
                    fixture = root / "state.json"
                    fixture.write_text(json.dumps(state), encoding="utf-8")
                    environment = os.environ.copy()
                    environment["FIXTURE"] = str(fixture)
                    process = subprocess.run(
                        [
                            str(shutil.which("powershell.exe") or shutil.which("pwsh")),
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
                    self.assertEqual(process.returncode, 0, process.stderr)
                    result = json.loads(
                        process.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                    )
                    self.assertEqual(result["failure"] is None, accepted, result)

    def test_merge_mutation_docs_bind_and_recheck_the_optional_auto_merge_plan(
        self,
    ) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        merge = setup.split("## Merge settings", 1)[1].split("\n## ", 1)[0]
        self.assertIn('mergeSettingsPreflightResult.repository, "OWNER/REPO"', merge)
        self.assertIn(
            "mergeSettingsPreflightResult.default_branch -cne $DEFAULT_BRANCH", merge
        )
        self.assertIn(
            "$finalPreflightOutput = python $mergeSettingsPreflight @preflightArguments",
            merge,
        )
        self.assertIn(
            "$postMergePreflightOutput = python $mergeSettingsPreflight @preflightArguments",
            merge,
        )
        self.assertIn("$installAutoMergeAssetsRequested = $false", merge)
        self.assertIn("$autoMergeCapabilityEnableApproved = $false", merge)
        self.assertIn("$copyMergePreflight = Get-ValidatedFreshMergePreflight", merge)
        for variable in (
            "mergeSettingsPreflightResult",
            "finalMergePreflight",
            "result",
            "postMergePreflight",
        ):
            self.assertIn(f"Assert-MergePreflightSchema -Verdict ${variable}", merge)
        self.assertIn(
            "Repeat this check before the second asset if both were selected.", merge
        )
        self.assertIn(
            "$enableAutoMergeNow = -not [bool]$finalMergePreflight.auto_merge_enabled",
            merge,
        )
        self.assertIn(
            "$finalMergeSettings.allow_rebase_merge -ne [bool]$postMergePreflight.desired_merge_methods.rebase",
            merge,
        )
        self.assertIn("if ($enableAutoMergeNow) {", merge)
        self.assertIn("$expectedAutoMergeEnabled", merge)
        self.assertIn('"--enable-delete-branch-on-merge"', merge)
        self.assertIn('"--squash-merge-commit-title", "PR_TITLE"', merge)
        self.assertIn("requested_settings.delete_branch_on_merge -ne $true", merge)
        self.assertIn(
            'requested_settings.squash_merge_commit_title -cne "PR_TITLE"', merge
        )
        self.assertLess(
            merge.index(
                "$finalPreflightOutput = python $mergeSettingsPreflight @preflightArguments"
            ),
            merge.index("$mergeOutput = & gh @mergeArguments"),
        )
        fresh_checks = [
            match.start()
            for match in re.finditer(
                r"\$null = Get-ValidatedFreshMergePreflight", merge
            )
        ]
        self.assertEqual(len(fresh_checks), 2)
        self.assertLess(fresh_checks[0], merge.index("$squashTitleOutput = & gh api"))
        self.assertLess(
            fresh_checks[1], merge.index("$autoMergeOutput = & gh repo edit")
        )
        self.assertLess(
            merge.index("$autoMergeOutput = & gh repo edit"),
            merge.index(
                "$postMergePreflightOutput = python $mergeSettingsPreflight @preflightArguments"
            ),
        )

    def configure(
        self,
        *,
        rules: object,
        merge: bool = False,
        rebase: bool = False,
        auto_merge: bool = True,
        branch: object | None = None,
    ) -> None:
        FakeClient.responses = {
            "repos/octo/example": {
                "full_name": "octo/example",
                "id": 42,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
                "permissions": {"admin": True},
                "allow_squash_merge": True,
                "allow_merge_commit": merge,
                "allow_rebase_merge": rebase,
                "allow_auto_merge": auto_merge,
                "delete_branch_on_merge": False,
                "squash_merge_commit_title": "COMMIT_OR_PR_TITLE",
            },
            "repos/octo/example/rules/branches/main?per_page=100": rules,
            "repos/octo/example/branches/main": branch
            if branch is not None
            else {
                "name": "main",
                "protected": False,
                "protection": {
                    "required_status_checks": {"contexts": []},
                },
            },
        }

    def test_rejects_unverified_default_branch_before_reading_rules(self) -> None:
        for branch in (None, "develop", "Main", 7):
            self.configure(rules=[])
            repository = FakeClient.responses["repos/octo/example"]
            assert isinstance(repository, dict)
            repository["default_branch"] = branch
            del FakeClient.responses[
                "repos/octo/example/rules/branches/main?per_page=100"
            ]
            with (
                self.subTest(branch=branch),
                mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient),
                self.assertRaisesRegex(
                    merge_settings_preflight.InspectionError, "current default branch"
                ),
            ):
                merge_settings_preflight.run(arguments())

    def test_rejects_missing_or_malformed_rule_type(self) -> None:
        rules: tuple[dict[str, object], ...] = (
            {},
            {"type": None},
            {"type": 7},
            {"type": []},
            {"type": {}},
            {"type": ""},
            {"type": " "},
        )
        for rule in rules:
            with (
                self.subTest(rule=rule),
                self.assertRaisesRegex(
                    merge_settings_preflight.InspectionError, "rule type"
                ),
            ):
                merge_settings_preflight.parse_effective_rules([rule])

    def test_requires_separate_confirmation_before_disabling_methods(self) -> None:
        self.configure(rules=[], rebase=True)

        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            result = merge_settings_preflight.run(arguments())

        self.assertEqual(
            result["decision"], "require-explicit-merge-method-removal-confirmation"
        )
        self.assertEqual(result["methods_to_disable"], ["rebase"])
        self.assertTrue(result["administration_permission"])
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

    def test_binds_delete_branch_and_squash_title_mutation_inputs(self) -> None:
        self.configure(rules=[])
        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            result = merge_settings_preflight.run(
                arguments(
                    enable_delete_branch_on_merge=True,
                    squash_merge_commit_title="PR_TITLE",
                )
            )

        self.assertEqual(
            result["requested_settings"],
            {
                "delete_branch_on_merge": True,
                "squash_merge_commit_title": "PR_TITLE",
            },
        )
        self.assertEqual(
            result["current_settings"],
            {
                "delete_branch_on_merge": False,
                "squash_merge_commit_title": "COMMIT_OR_PR_TITLE",
            },
        )

    def test_cli_accepts_exact_merge_setting_inputs(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                "merge_settings_preflight.py",
                "--repository",
                "octo/example",
                "--default-branch",
                "main",
                "--enable-delete-branch-on-merge",
                "--squash-merge-commit-title",
                "PR_TITLE",
            ],
        ):
            parsed = merge_settings_preflight.parse_args()
        self.assertTrue(parsed.enable_delete_branch_on_merge)
        self.assertEqual(parsed.squash_merge_commit_title, "PR_TITLE")

    def test_rejects_unsupported_requested_squash_title(self) -> None:
        self.configure(rules=[])
        with (
            mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                merge_settings_preflight.InspectionError, "title is unsupported"
            ),
        ):
            merge_settings_preflight.run(arguments(squash_merge_commit_title="PR_BODY"))

    def test_rejects_malformed_merge_setting_inputs_and_current_state(self) -> None:
        self.configure(rules=[])
        with (
            mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                merge_settings_preflight.InspectionError,
                "Delete-branch request must be a boolean",
            ),
        ):
            merge_settings_preflight.run(arguments(enable_delete_branch_on_merge="yes"))

        self.configure(rules=[])
        repository = FakeClient.responses["repos/octo/example"]
        assert isinstance(repository, dict)
        repository["squash_merge_commit_title"] = "UNSUPPORTED"
        with (
            mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                merge_settings_preflight.InspectionError,
                "invalid 'squash_merge_commit_title'",
            ),
        ):
            merge_settings_preflight.run(
                arguments(squash_merge_commit_title="PR_TITLE")
            )

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

    def test_auto_merge_capability_requires_a_status_check_before_enablement(
        self,
    ) -> None:
        self.configure(rules=[], auto_merge=False)

        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            blocked = merge_settings_preflight.run(
                arguments(require_auto_merge_workflows=True)
            )

        self.assertEqual(
            blocked["decision"],
            "require-status-checks-before-installing-auto-merge-workflows",
        )
        self.assertFalse(blocked["auto_merge_enabled"])
        self.assertFalse(blocked["auto_merge_workflows_eligible"])

        branch_with_checks = {
            "name": "main",
            "protected": True,
            "protection": {"required_status_checks": {"contexts": ["ci"]}},
        }
        self.configure(rules=[], auto_merge=False, branch=branch_with_checks)
        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            enableable = merge_settings_preflight.run(
                arguments(require_auto_merge_workflows=True)
            )
        self.assertEqual(
            enableable["decision"], "enable-auto-merge-before-installing-workflows"
        )
        self.assertTrue(enableable["status_checks_required"])

        self.configure(
            rules=[],
            auto_merge=True,
            branch=branch_with_checks,
        )
        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            ready = merge_settings_preflight.run(
                arguments(require_auto_merge_workflows=True)
            )

        self.assertEqual(ready["decision"], "may-configure-merge-settings")
        self.assertTrue(ready["auto_merge_enabled"])
        self.assertTrue(ready["auto_merge_workflows_eligible"])
        self.assertTrue(ready["classic_status_checks_required"])
        self.assertEqual(ready["repository"], "octo/example")
        self.assertEqual(ready["default_branch"], "main")

    def test_auto_merge_workflows_require_effective_status_checks(self) -> None:
        self.configure(rules=[])

        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            blocked = merge_settings_preflight.run(
                arguments(require_auto_merge_workflows=True)
            )

        self.assertEqual(
            blocked["decision"],
            "require-status-checks-before-installing-auto-merge-workflows",
        )
        self.assertFalse(blocked["status_checks_required"])
        self.assertFalse(blocked["auto_merge_workflows_eligible"])

        self.configure(
            rules=[
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "ci"}],
                    },
                }
            ]
        )
        with mock.patch.object(merge_settings_preflight, "GitHubClient", FakeClient):
            ready = merge_settings_preflight.run(
                arguments(require_auto_merge_workflows=True)
            )

        self.assertEqual(ready["decision"], "may-configure-merge-settings")
        self.assertTrue(ready["ruleset_status_checks_required"])
        self.assertIsNone(ready["classic_status_checks_required"])
        self.assertTrue(ready["auto_merge_workflows_eligible"])
        self.assertEqual(ready["github_api_requests"], 4)

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
                        (set(), False, False),
                    )
                else:
                    with self.assertRaisesRegex(
                        merge_settings_preflight.InspectionError, message
                    ):
                        merge_settings_preflight.parse_effective_rules(payload)

        methods, queue, status_checks = merge_settings_preflight.parse_effective_rules(
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
        self.assertFalse(status_checks)

        for contexts, message in [
            ("ci", "no status-check context list"),
            ([1], "invalid status-check context"),
            ([""], "invalid status-check context"),
            (["x" * 257], "invalid status-check context"),
            (["ci\n"], "invalid status-check context"),
        ]:
            with self.subTest(contexts=contexts):
                with self.assertRaisesRegex(
                    merge_settings_preflight.InspectionError, message
                ):
                    merge_settings_preflight.status_check_contexts(contexts, "test")

        invalid_rules = [
            (
                {"required_status_checks": []},
                "invalid strict policy",
            ),
            (
                {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": {},
                },
                "no status-check list",
            ),
            (
                {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": ["ci"],
                },
                "invalid check",
            ),
            (
                {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{}],
                },
                "invalid check",
            ),
            (
                {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": "ci", "integration_id": True}
                    ],
                },
                "invalid integration ID",
            ),
            (
                {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": "ci", "integration_id": 0}],
                },
                "invalid integration ID",
            ),
        ]
        for parameters, message in invalid_rules:
            with self.subTest(parameters=parameters):
                with self.assertRaisesRegex(
                    merge_settings_preflight.InspectionError, message
                ):
                    merge_settings_preflight.parse_effective_rules(
                        [{"type": "required_status_checks", "parameters": parameters}]
                    )

        for branch, message in [
            ({}, "response is invalid"),
            (
                {
                    "name": "other",
                    "protected": False,
                    "protection": {"required_status_checks": {"contexts": []}},
                },
                "response is invalid",
            ),
            (
                {
                    "name": "main",
                    "protected": "no",
                    "protection": {"required_status_checks": {"contexts": []}},
                },
                "invalid protected value",
            ),
            (
                {"name": "main", "protected": False, "protection": []},
                "no protection mapping",
            ),
            (
                {"name": "main", "protected": False, "protection": {}},
                "no required-status-checks mapping",
            ),
            (
                {
                    "name": "main",
                    "protected": False,
                    "protection": {"required_status_checks": {"contexts": [1]}},
                },
                "invalid status-check context",
            ),
            (
                {
                    "name": "main",
                    "protected": False,
                    "protection": {"required_status_checks": {"contexts": ["ci"]}},
                },
                "unprotected branch",
            ),
        ]:
            with self.subTest(branch=branch):
                with self.assertRaisesRegex(
                    merge_settings_preflight.InspectionError, message
                ):
                    merge_settings_preflight.branch_has_status_checks(branch, "main")

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
                    "disabled": False,
                    "permissions": {"admin": True},
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
                    "disabled": True,
                    "permissions": {"admin": True},
                    "allow_squash_merge": True,
                    "allow_merge_commit": False,
                    "allow_rebase_merge": False,
                    "allow_auto_merge": True,
                },
                "Disabled",
            ),
            (
                {
                    "full_name": "octo/example",
                    "archived": False,
                    "disabled": False,
                    "permissions": {},
                    "allow_squash_merge": True,
                    "allow_merge_commit": False,
                    "allow_rebase_merge": False,
                    "allow_auto_merge": True,
                },
                "administration permission",
            ),
            (
                {
                    "full_name": "octo/example",
                    "archived": False,
                    "disabled": False,
                    "permissions": {"admin": "yes"},
                    "allow_squash_merge": True,
                    "allow_merge_commit": False,
                    "allow_rebase_merge": False,
                    "allow_auto_merge": True,
                },
                "invalid 'admin'",
            ),
            (
                {
                    "full_name": "octo/example",
                    "archived": False,
                    "disabled": False,
                    "permissions": {"admin": False},
                    "allow_squash_merge": True,
                    "allow_merge_commit": False,
                    "allow_rebase_merge": False,
                    "allow_auto_merge": True,
                },
                "administration permission",
            ),
            (
                {
                    "full_name": "octo/example",
                    "archived": False,
                    "disabled": False,
                    "permissions": {"admin": True},
                    "allow_squash_merge": "yes",
                    "default_branch": "main",
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
                    "disabled": False,
                    "permissions": {"admin": True},
                    "allow_squash_merge": True,
                    "allow_merge_commit": False,
                    "allow_rebase_merge": False,
                    "allow_auto_merge": "yes",
                    "default_branch": "main",
                },
                "allow_auto_merge",
            ),
        ]:
            if isinstance(repository, dict):
                repository = {"id": 42, **repository}
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
