from __future__ import annotations

import argparse
import hashlib
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
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
NATIVE_GIT = shutil.which("git")
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

SCRIPT_PATH = SCRIPT_DIRECTORY / "branch_protection_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.branch_protection_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load branch_protection_preflight.py")
branch_protection_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = branch_protection_preflight
SPEC.loader.exec_module(branch_protection_preflight)


OWNER = "octo"
REPOSITORY = "example"
HEAD_SHA = "a" * 40
MERGE_SHA = "b" * 40
MERGE_GROUP_SHA = "7" * 40
BLOB_SHA = "c" * 40
BASE_SHA = "d" * 40
CHECK_SUITE_ID = 98765
HEAD_CHECK_SUITE_ID = 98764
MERGE_GROUP_CHECK_SUITE_ID = 98763


class FakeClient:
    responses: dict[str, object] = {}

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        self.request_count = 0

    def json(self, endpoint: str) -> object:
        self.request_count += 1
        return self.responses[endpoint]

    def raw(self, endpoint: str) -> str:
        self.request_count += 1
        value = self.responses[endpoint]
        if not isinstance(value, str):
            raise TypeError("Expected raw workflow text")
        return value


@lru_cache(maxsize=128)
def git_blob_id(text: str) -> str:
    """Use Git's object-format implementation for synthetic workflow fixtures."""
    if NATIVE_GIT is None:
        raise unittest.SkipTest("requires Git for canonical fixture object IDs")
    result = subprocess.run(
        [NATIVE_GIT, "hash-object", "--stdin", "--no-filters"],
        input=text.encode("utf-8"),
        cwd=PLUGIN_ROOT,
        capture_output=True,
        check=True,
        timeout=10,
    )
    object_id = result.stdout.decode("ascii").strip()
    if re.fullmatch(r"[0-9a-f]{40}", object_id) is None:
        raise AssertionError("Git did not return a canonical SHA-1 fixture object ID")
    return object_id


def check_runs(
    context: str,
    app_id: int = 15368,
    *,
    head_sha: str = HEAD_SHA,
    suite_id: int = CHECK_SUITE_ID,
) -> dict[str, Any]:
    return {
        "total_count": 1,
        "check_runs": [
            {
                "name": context,
                "head_sha": head_sha,
                "check_suite": {"id": suite_id},
                "app": {"id": app_id},
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "status": "completed",
                "conclusion": "success",
            }
        ],
    }


def preflight_args(*contexts: str, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "hostname": "github.com",
        "repository": f"{OWNER}/{REPOSITORY}",
        "expected_repository_id": 42,
        "default_branch": "main",
        "pull_request": 7,
        "merge_group_sha": None,
        "required_check": list(contexts),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class WorkflowInspectionTests(unittest.TestCase):
    def test_root_producer_inventory_refuses_unclassifiable_paths(self) -> None:
        valid = {
            "path": ".github/workflows/ci.yml",
            "type": "blob",
            "mode": "100644",
            "sha": git_blob_id("jobs: {}\n"),
        }
        unknowns: tuple[object, ...] = (
            None,
            {},
            {"path": None},
            {"path": 7},
            {"path": ""},
        )
        for unknown in unknowns:
            for first in (True, False):
                client = mock.Mock()
                client.raw.return_value = "jobs: {}\n"
                client.json.return_value = {
                    "truncated": False,
                    "tree": [unknown, valid] if first else [valid, unknown],
                }
                with (
                    self.subTest(entry=unknown, first=first),
                    self.assertRaisesRegex(
                        branch_protection_preflight.InspectionError, "unclassifiable"
                    ),
                ):
                    branch_protection_preflight.workflow_producers(
                        client, OWNER, REPOSITORY, HEAD_SHA, "main"
                    )
                client.raw.assert_not_called()

    def test_root_producer_inventory_bounds_all_entries_before_classification(
        self,
    ) -> None:
        client = mock.Mock()
        client.json.return_value = {
            "truncated": False,
            "tree": [{"path": "README.md"}, {"path": "other.md"}],
        }
        with (
            mock.patch.object(
                branch_protection_preflight, "MAX_REMOTE_TREE_ENTRIES", 1
            ),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "entry safety cap"
            ),
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA, "main"
            )
        client.raw.assert_not_called()

    def test_producer_refuses_wrong_git_blob_before_inspecting_jobs(self) -> None:
        body = b"on: pull_request\njobs: {}\n"
        blob = hashlib.sha1(
            b"blob " + str(len(body)).encode() + b"\0" + body, usedforsecurity=False
        ).hexdigest()
        client = mock.Mock()
        client.json.return_value = {
            "truncated": False,
            "tree": [
                {
                    "path": ".github/workflows/ci.yml",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob,
                }
            ],
        }
        client.raw.return_value = "on: pull_request\njobs: {test: {runs-on: ubuntu-latest, steps: [{run: echo wrong}]}}\n"
        with mock.patch.object(branch_protection_preflight, "parse_workflow") as parse:
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "blob identity"
            ):
                branch_protection_preflight.workflow_producers(
                    client, OWNER, REPOSITORY, HEAD_SHA, "main"
                )
        parse.assert_not_called()

    def test_job_context_accepts_only_bounded_closed_pr_name_disposition(self) -> None:
        expression = (
            "${{ github.event_name == 'pull_request' && "
            "github.event.pull_request.state == 'closed' && 'ci-closed-pr' || 'ci' }}"
        )
        cases: tuple[tuple[object, str | None], ...] = (
            (None, None),
            (True, None),
            ("", None),
            ("ci", "ci"),
            ("${{ matrix.name }}", None),
            ("${{" + "x" * 1024, None),
            (expression, "ci"),
            (expression.replace("'pull_request'", "'pull_request_target'"), "ci"),
            (expression.replace("'closed'", "'open'"), None),
            (expression.replace("'ci-closed-pr'", "'CI'"), None),
            (expression.replace("'ci-closed-pr'", "'${{ matrix.name }}'"), None),
            (expression.replace("'ci-closed-pr'", "'" + "x" * 257 + "'"), None),
            (expression.replace(" || 'ci'", " || '" + "x" * 257 + "'"), None),
            (expression.replace(" || 'ci'", " || '${{ matrix.name }}'"), None),
            (expression.replace(" || 'ci'", " || 'ci\\n'"), "ci\\n"),
            (expression.replace(" || 'ci'", " || 'ci\n'"), None),
            (expression.replace(" || 'ci'", " || 'it''s ci'"), "it's ci"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    branch_protection_preflight.required_event_job_context(value),
                    expected,
                )

    def test_lifecycle_checkout_exception_rejects_unpinned_and_noncheckout_steps(
        self,
    ) -> None:
        guard = "github.event_name != 'pull_request' || github.event.pull_request.state != 'closed'"
        valid = {
            "if": guard,
            "uses": "actions/checkout@" + "a" * 40,
            "with": {
                "persist-credentials": "false",
                "ref": "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.event.merge_group.head_sha }}",
            },
        }
        cases = (
            (None, False),
            ({"run": "echo validation"}, True),
            (valid, True),
            ({**valid, "continue-on-error": "false"}, False),
            ({**valid, "uses": None}, False),
            ({**valid, "uses": "actions/checkout@main"}, False),
            ({**valid, "uses": "other/checkout@" + "a" * 40}, False),
            ({**valid, "run": "exit 0"}, False),
            ({**valid, "with": None}, False),
        )
        for step, expected in cases:
            with self.subTest(step=step):
                self.assertEqual(
                    branch_protection_preflight.step_executes_for_required_events(step),
                    expected,
                )

    def test_shipped_commitlint_lifecycle_guard_is_executable_for_required_events(
        self,
    ) -> None:
        endpoint = f"repos/{OWNER}/{REPOSITORY}/git/trees/{HEAD_SHA}?recursive=1"
        for relative in (
            ".github/workflows/commitlint.yml",
            "skills/repo-scaffold/assets/workflows/commitlint.yml",
        ):
            workflow = (PLUGIN_ROOT / relative).read_text(encoding="utf-8")
            guard = "github.event_name != 'pull_request' || github.event.pull_request.state != 'closed'"
            cases = (
                ("shipped", workflow, True),
                ("false", workflow.replace(guard, "false"), False),
                (
                    "inverted",
                    workflow.replace("state != 'closed'", "state == 'closed'"),
                    False,
                ),
                (
                    "actor-filter",
                    workflow.replace(guard, "github.actor == 'trusted'"),
                    False,
                ),
                (
                    "wrong-ref",
                    workflow.replace(
                        "github.event.pull_request.head.sha",
                        "github.event.pull_request.base.sha",
                    ),
                    False,
                ),
                (
                    "credentials",
                    workflow.replace(
                        "persist-credentials: false", "persist-credentials: true"
                    ),
                    False,
                ),
            )
            for label, text, executable in cases:
                with self.subTest(workflow=relative, case=label):
                    blob_sha = git_blob_id(text)
                    FakeClient.responses = {
                        endpoint: {
                            "truncated": False,
                            "tree": [
                                {
                                    "type": "blob",
                                    "mode": "100644",
                                    "path": ".github/workflows/commitlint.yml",
                                    "sha": blob_sha,
                                }
                            ],
                        },
                        f"repos/{OWNER}/{REPOSITORY}/git/blobs/{blob_sha}": text,
                    }
                    producers = branch_protection_preflight.workflow_producers(
                        FakeClient("github.com"), OWNER, REPOSITORY, HEAD_SHA, "main"
                    )
                    self.assertEqual(len(producers), 1)
                    self.assertEqual(producers[0].executable, executable)
                    self.assertTrue(producers[0].unconditional)
                    self.assertTrue(producers[0].pull_request_coverage)
                    self.assertTrue(producers[0].merge_group_coverage)

    def test_event_coverage_requires_relevant_trigger_types_without_filters(
        self,
    ) -> None:
        covered = branch_protection_preflight.parse_workflow(
            """on:
  pull_request:
    types: [opened, edited, reopened, synchronize]
  merge_group:
    types: [checks_requested]
jobs: {}
""",
            "covered.yml",
        )
        filtered = branch_protection_preflight.parse_workflow(
            """on:
  pull_request:
    paths: [docs/**]
jobs: {}
""",
            "filtered.yml",
        )

        self.assertTrue(
            branch_protection_preflight.event_covers(covered, "pull_request")
        )
        self.assertTrue(
            branch_protection_preflight.event_covers(covered, "merge_group")
        )
        self.assertFalse(
            branch_protection_preflight.event_covers(filtered, "pull_request")
        )

    def test_trusted_pull_request_target_covers_default_branch(self) -> None:
        workflow = branch_protection_preflight.parse_workflow(
            """on:
  pull_request_target:
    types: [opened, edited, reopened, synchronize]
    branches: [main]
  merge_group:
    types: [checks_requested]
jobs: {}
""",
            "target.yml",
        )

        self.assertFalse(
            branch_protection_preflight.event_covers(workflow, "pull_request")
        )
        self.assertTrue(
            branch_protection_preflight.event_covers(workflow, "pull_request", "main")
        )
        self.assertFalse(
            branch_protection_preflight.event_covers(
                workflow, "pull_request", "develop"
            )
        )

    def test_parse_workflow_rejects_duplicate_keys(self) -> None:
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "duplicate"
        ):
            branch_protection_preflight.parse_workflow(
                "jobs: {}\njobs: {}\n", "bad.yml"
            )

    def test_validate_contexts_rejects_case_insensitive_duplicates(self) -> None:
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "unique"
        ):
            branch_protection_preflight.validate_contexts(["CI", "ci"])

    def test_validate_contexts_rejects_empty_and_invalid_values(self) -> None:
        for values in ([], ["valid\ninvalid"]):
            with self.subTest(values=values):
                with self.assertRaises(branch_protection_preflight.InspectionError):
                    branch_protection_preflight.validate_contexts(values)
        with self.assertRaises(branch_protection_preflight.InspectionError):
            branch_protection_preflight.validate_contexts(["check"] * 51)

    def test_parsing_and_event_coverage_fail_closed(self) -> None:
        with mock.patch.object(branch_protection_preflight, "MAX_WORKFLOW_BYTES", 1):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "byte safety cap"
            ):
                branch_protection_preflight.parse_workflow("ab", "large.yml")
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "Could not parse"
        ):
            branch_protection_preflight.parse_workflow("jobs: [", "bad.yml")
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "not a YAML mapping"
        ):
            branch_protection_preflight.parse_workflow("- job", "list.yml")

        cases = [
            ({"on": ["pull_request"]}, "pull_request", True),
            ({"on": ["push"]}, "pull_request", False),
            ({}, "pull_request", False),
            ({"on": {"pull_request": None}}, "pull_request", True),
            ({"on": {"pull_request": ""}}, "pull_request", True),
            ({"on": {"pull_request": True}}, "pull_request", False),
            ({"on": {"pull_request": {}}}, "pull_request", True),
            (
                {"on": {"pull_request": {"paths": ["docs/**"]}}},
                "pull_request",
                False,
            ),
            (
                {"on": {"pull_request": {"types": ["opened"]}}},
                "pull_request",
                False,
            ),
            (
                {"on": {"pull_request": {"types": [["opened"]]}}},
                "pull_request",
                False,
            ),
            (
                {"on": {"merge_group": {"types": ["queued"]}}},
                "merge_group",
                False,
            ),
        ]
        for document, event, expected in cases:
            with self.subTest(document=document, event=event):
                self.assertIs(
                    branch_protection_preflight.event_covers(document, event), expected
                )

    def test_workflow_producers_reject_invalid_inputs_and_ignores_dynamic_jobs(
        self,
    ) -> None:
        client = FakeClient("github.com")
        endpoint = f"repos/{OWNER}/{REPOSITORY}/git/trees/{HEAD_SHA}?recursive=1"
        for tree, message in [
            ({"truncated": True}, "missing"),
            ({"truncated": False, "tree": {}}, "no tree array"),
        ]:
            FakeClient.responses = {endpoint: tree}
            with self.subTest(tree=tree):
                with self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, message
                ):
                    branch_protection_preflight.workflow_producers(
                        client, OWNER, REPOSITORY, HEAD_SHA
                    )

        entry = {
            "type": "blob",
            "mode": "100644",
            "path": ".github/workflows/ci.yml",
            "sha": BLOB_SHA,
        }
        FakeClient.responses = {
            endpoint: {
                "truncated": False,
                "tree": [
                    entry,
                    {"type": "tree", "path": ".github/workflows"},
                    {
                        "type": "blob",
                        "path": ".github/workflows/nested/ci.yml",
                        "sha": BLOB_SHA,
                    },
                    {
                        "type": "blob",
                        "path": ".github/workflows/notes.txt",
                        "sha": BLOB_SHA,
                    },
                ],
            }
        }
        with mock.patch.object(branch_protection_preflight, "MAX_WORKFLOWS", 0):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "count exceeds"
            ):
                branch_protection_preflight.workflow_producers(
                    client, OWNER, REPOSITORY, HEAD_SHA
                )

        unsafe_entry = dict(entry, path=".github/workflows/a\n.yml")
        FakeClient.responses = {endpoint: {"truncated": False, "tree": [unsafe_entry]}}
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "not canonical"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )

        invalid_entry = dict(entry, sha="short")
        FakeClient.responses = {endpoint: {"truncated": False, "tree": [invalid_entry]}}
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "invalid blob"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )

        symlink_entry = dict(entry, mode="120000")
        FakeClient.responses = {endpoint: {"truncated": False, "tree": [symlink_entry]}}
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "not a regular file"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )

        non_blob_entry = dict(entry, type="tree")
        FakeClient.responses = {
            endpoint: {"truncated": False, "tree": [non_blob_entry]}
        }
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "not a blob"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )

        duplicate_entry = dict(entry, sha="c" * 40)
        FakeClient.responses = {
            endpoint: {"truncated": False, "tree": [entry, duplicate_entry]}
        }
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "appears more than once"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )

        def install_content(text: str) -> None:
            blob = git_blob_id(text)
            FakeClient.responses = {
                endpoint: {"truncated": False, "tree": [{**entry, "sha": blob}]},
                f"repos/{OWNER}/{REPOSITORY}/git/blobs/{blob}": text,
            }

        install_content("jobs: {}\n")
        with mock.patch.object(
            branch_protection_preflight, "MAX_TOTAL_WORKFLOW_BYTES", 1
        ):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "total byte"
            ):
                branch_protection_preflight.workflow_producers(
                    client, OWNER, REPOSITORY, HEAD_SHA
                )

        install_content("on: pull_request\n")
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "no jobs"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )
        install_content("jobs:\n  invalid: []\n")
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "invalid job"
        ):
            branch_protection_preflight.workflow_producers(
                client, OWNER, REPOSITORY, HEAD_SHA
            )
        install_content("""on: pull_request
jobs:
  malformed:
    name: malformed
    if: [evil]
    runs-on: ubuntu-latest
    steps:
      - run: echo checked
""")
        malformed_producers = branch_protection_preflight.workflow_producers(
            client, OWNER, REPOSITORY, HEAD_SHA
        )
        self.assertEqual(
            [producer.context for producer in malformed_producers], ["malformed"]
        )
        self.assertFalse(malformed_producers[0].unconditional)
        install_content("""on: pull_request
jobs:
  dynamic:
    name: ${{ github.job }}
  reusable:
    uses: org/example/.github/workflows/reuse.yml@main
  non-executable:
    name: non-executable
    runs-on: ubuntu-latest
    steps: []
""")
        producers = branch_protection_preflight.workflow_producers(
            client, OWNER, REPOSITORY, HEAD_SHA
        )
        self.assertEqual(
            [producer.context for producer in producers], ["reusable", "non-executable"]
        )
        self.assertFalse(producers[0].executable)
        self.assertFalse(producers[1].executable)


class BranchProtectionPreflightTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("powershell.exe") or shutil.which("pwsh"), "requires PowerShell"
    )
    def test_native_fresh_verdict_rejects_coerced_or_replaced_binding(self) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        identity = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert identity is not None
        function = (
            "function Assert-FreshRequiredCheckPreflight {"
            + reference.split("function Assert-FreshRequiredCheckPreflight {", 1)[
                1
            ].split("\n```", 1)[0]
        )
        schema = ""
        if "function Assert-RequiredCheckPreflightSchema {" in reference:
            schema = (
                "function Assert-RequiredCheckPreflightSchema {"
                + reference.split("function Assert-RequiredCheckPreflightSchema {", 1)[
                    1
                ].split("\n$preflightOutput =", 1)[0]
            )
        valid: dict[str, Any] = {
            "inspection_complete": True,
            "decision": "may-configure-classic-protection",
            "repository": "OWNER/REPO",
            "repository_id": 42,
            "default_branch": "main",
            "administration_permission": True,
            "pull_request": 7,
            "head_sha": HEAD_SHA,
            "test_merge_sha": MERGE_SHA,
            "merge_queue_required": False,
            "merge_group_sha": None,
            "required_checks": [
                {
                    "context": "ci-success",
                    "app_id": 15368,
                    "producer": ".github/workflows/ci.yml#gate",
                }
            ],
        }
        cases = [("positive", valid, 1)]
        for field, value in (
            ("repository_id", 43),
            ("repository_id", "42"),
            ("repository_id", None),
            ("inspection_complete", "true"),
            ("administration_permission", False),
            ("administration_permission", "true"),
            ("pull_request", "7"),
            ("head_sha", [HEAD_SHA]),
            ("decision", "bind-repository-identity-before-mutation"),
            (
                "required_checks",
                [
                    {
                        "context": "ci-success",
                        "app_id": "15368",
                        "producer": ".github/workflows/ci.yml#gate",
                    }
                ],
            ),
        ):
            cases.append((field, {**valid, field: value}, 0))
        prelude = r"""
$ErrorActionPreference='Stop'
$SELECTED_REPOSITORY_ID=42; $defaultBranch='main'
$branchProtectionPreflight='synthetic'; $preflightArguments=@()
$requiredCheckPreflight=Get-Content -LiteralPath $env:APPROVED -Raw | ConvertFrom-Json
$verifiedChecks=@($requiredCheckPreflight.required_checks)
function python { $global:LASTEXITCODE=0; Get-Content -LiteralPath $env:FIXTURE -Raw }
"""
        suffix = r"""
$mutations=0; $failure=$null
try { $result=Assert-FreshRequiredCheckPreflight; $mutations++ } catch { $failure=$_.Exception.Message }
@{mutations=$mutations;failure=$failure} | ConvertTo-Json -Compress
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved.json"
            approved.write_text(json.dumps(valid), encoding="utf-8")
            script = root / "consumer.ps1"
            script.write_bytes(
                (prelude + identity[0] + schema + function + suffix).encode("utf-8-sig")
            )
            for label, fresh_verdict, mutations in cases:
                with self.subTest(case=label):
                    fixture = root / "fixture.json"
                    fixture.write_text(json.dumps(fresh_verdict), encoding="utf-8")
                    result = subprocess.run(
                        [
                            str(shutil.which("powershell.exe") or shutil.which("pwsh")),
                            "-NoProfile",
                            "-NonInteractive",
                            "-File",
                            str(script),
                        ],
                        env={
                            **os.environ,
                            "FIXTURE": str(fixture),
                            "APPROVED": str(approved),
                        },
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    response = json.loads(
                        result.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                    )
                    self.assertEqual(response["mutations"], mutations, response)

    def test_binding_helpers_reject_nonfinite_rules_and_preserve_typed_pr_fields(
        self,
    ) -> None:
        self.assertNotEqual(
            branch_protection_preflight.rules_binding([{"v": True}]),
            branch_protection_preflight.rules_binding([{"v": 1}]),
        )
        for value in (float("nan"), object()):
            with self.assertRaises(branch_protection_preflight.InspectionError):
                branch_protection_preflight.rules_binding(value)
        self.assertNotEqual(
            branch_protection_preflight.pull_request_binding({"number": True}),
            branch_protection_preflight.pull_request_binding({"number": 1}),
        )

    def test_approving_verdict_requires_discovery_and_pull_request_repository_ids(
        self,
    ) -> None:
        self.configure()
        endpoint = f"repos/{OWNER}/{REPOSITORY}"
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(preflight_args("ci-success"))
        self.assertEqual(result.get("repository_id"), 42)
        for number in (None, True, "7", 8):
            self.configure()
            pr = cast(dict[str, Any], FakeClient.responses[endpoint + "/pulls/7"])
            pr["number"] = number
            with (
                self.subTest(number=number),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, "number"
                ),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))
        for identity in (43, None, True, "42", 0, -1, 42.5):
            self.configure()
            response = cast(dict[str, Any], FakeClient.responses[endpoint])
            response["id"] = identity
            with (
                self.subTest(identity=identity),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaises(branch_protection_preflight.InspectionError),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))
        self.configure()
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(
                preflight_args("ci-success", expected_repository_id=None)
            )
        self.assertEqual(result["decision"], "bind-repository-identity-before-mutation")
        self.configure()
        pr = cast(dict[str, Any], FakeClient.responses[endpoint + "/pulls/7"])
        pr["base"]["repo"]["id"] = 43
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaises(branch_protection_preflight.InspectionError),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

    def test_verdict_rejects_mid_inspection_repository_pull_request_and_rule_drift(
        self,
    ) -> None:
        endpoint = f"repos/{OWNER}/{REPOSITORY}"
        rule_endpoint = endpoint + "/rules/branches/main?per_page=100"
        for changes in (
            ("repository", {"id": 43}),
            ("repository", {"archived": True}),
            ("repository", {"permissions": {"admin": False}}),
            ("pull_request", {"state": "closed"}),
            ("pull_request", {"head": {"sha": "f" * 40}}),
            ("pull_request", {"merge_commit_sha": "f" * 40}),
            ("rules", [{"type": "merge_queue"}]),
        ):
            self.configure()
            domain, value = changes
            original = copy.deepcopy(FakeClient.responses)

            class DriftingClient(FakeClient):
                def json(self, path: str) -> object:
                    result = super().json(path)
                    if "/actions/runs?check_suite_id=" in path:
                        if domain == "rules":
                            self.responses[rule_endpoint] = value
                        else:
                            changed_path = (
                                endpoint
                                if domain == "repository"
                                else endpoint + "/pulls/7"
                            )
                            self.responses[changed_path] = {
                                **cast(dict[str, Any], original[changed_path]),
                                **cast(dict[str, Any], value),
                            }
                    return result

            with (
                self.subTest(changes=changes),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", DriftingClient
                ),
                self.assertRaises(branch_protection_preflight.InspectionError),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_final_reinspection_rejects_degraded_pr_and_changed_trusted_default(
        self,
    ) -> None:
        target_workflow = self.WORKFLOW.replace(
            "  pull_request:\n", "  pull_request_target:\n    branches: [main]\n"
        )
        endpoint = f"repos/{OWNER}/{REPOSITORY}"
        for domain, value in (
            ("pull_request", None),
            ("default_commit", None),
            ("default_commit", {"sha": "f" * 40}),
        ):
            self.configure(target_workflow)

            class DriftingClient(FakeClient):
                def json(self, path: str) -> object:
                    result = super().json(path)
                    if "/actions/runs?check_suite_id=" in path:
                        changed_path = endpoint + (
                            "/pulls/7" if domain == "pull_request" else "/commits/main"
                        )
                        self.responses[changed_path] = value
                    return result

            with (
                self.subTest(domain=domain, value=value),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", DriftingClient
                ),
                self.assertRaises(branch_protection_preflight.InspectionError),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    WORKFLOW = """name: CI
on:
  pull_request:
    types: [opened, edited, reopened, synchronize]
  merge_group:
    types: [checks_requested]
jobs:
  ci-success:
    name: ci-success
    if: ${{ always() }}
    runs-on: ubuntu-latest
    steps:
      - run: echo checked
"""

    def configure(
        self, workflow: str | None = None, *, base_workflow: str | None = None
    ) -> None:
        workflow = self.WORKFLOW if workflow is None else workflow
        base_workflow = workflow if base_workflow is None else base_workflow
        blob_sha = git_blob_id(workflow)
        base_blob_sha = git_blob_id(base_workflow)
        tree_path = f"repos/{OWNER}/{REPOSITORY}/git/trees/{MERGE_SHA}?recursive=1"
        FakeClient.responses = {
            f"repos/{OWNER}/{REPOSITORY}": {
                "full_name": f"{OWNER}/{REPOSITORY}",
                "id": 42,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
                "permissions": {"admin": True},
            },
            f"repos/{OWNER}/{REPOSITORY}/pulls/7": {
                "number": 7,
                "state": "open",
                "base": {
                    "ref": "main",
                    "sha": BASE_SHA,
                    "repo": {"full_name": f"{OWNER}/{REPOSITORY}", "id": 42},
                },
                "head": {"sha": HEAD_SHA},
                "merge_commit_sha": MERGE_SHA,
                "mergeable": True,
            },
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100": [],
            f"repos/{OWNER}/{REPOSITORY}/commits/main": {"sha": BASE_SHA},
            tree_path: {
                "truncated": False,
                "tree": [
                    {
                        "type": "blob",
                        "mode": "100644",
                        "path": ".github/workflows/ci.yml",
                        "sha": blob_sha,
                    }
                ],
            },
            f"repos/{OWNER}/{REPOSITORY}/git/blobs/{blob_sha}": workflow,
            f"repos/{OWNER}/{REPOSITORY}/git/trees/{BASE_SHA}?recursive=1": {
                "truncated": False,
                "tree": [
                    {
                        "type": "blob",
                        "mode": "100644",
                        "path": ".github/workflows/ci.yml",
                        "sha": base_blob_sha,
                    }
                ],
            },
            f"repos/{OWNER}/{REPOSITORY}/git/blobs/{base_blob_sha}": base_workflow,
        }
        workflow_event = (
            "pull_request_target"
            if "pull_request_target:" in workflow
            else "pull_request"
        )
        for sha, suite_id in (
            (HEAD_SHA, HEAD_CHECK_SUITE_ID),
            (MERGE_SHA, CHECK_SUITE_ID),
        ):
            FakeClient.responses[
                f"repos/{OWNER}/{REPOSITORY}/commits/{sha}/check-runs?per_page=100"
            ] = check_runs("ci-success", head_sha=sha, suite_id=suite_id)
            FakeClient.responses[
                f"repos/{OWNER}/{REPOSITORY}/commits/{sha}/status?per_page=100"
            ] = {"total_count": 0, "statuses": []}
            FakeClient.responses[
                f"repos/{OWNER}/{REPOSITORY}/actions/runs?check_suite_id={suite_id}&per_page=100"
            ] = {
                "total_count": 1,
                "workflow_runs": [
                    {
                        "check_suite_id": suite_id,
                        "head_sha": sha,
                        "event": workflow_event,
                        "path": ".github/workflows/ci.yml",
                    }
                ],
            }

    def configure_merge_group(
        self,
        workflow: str | None = None,
        *,
        merge_group_workflow: str | None = None,
    ) -> None:
        workflow = self.WORKFLOW if workflow is None else workflow
        merge_group_workflow = (
            workflow if merge_group_workflow is None else merge_group_workflow
        )
        self.configure(workflow)
        merge_group_blob_sha = git_blob_id(merge_group_workflow)
        tree_path = (
            f"repos/{OWNER}/{REPOSITORY}/git/trees/{MERGE_GROUP_SHA}?recursive=1"
        )
        FakeClient.responses[tree_path] = {
            "truncated": False,
            "tree": [
                {
                    "type": "blob",
                    "mode": "100644",
                    "path": ".github/workflows/ci.yml",
                    "sha": merge_group_blob_sha,
                }
            ],
        }
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/git/blobs/{merge_group_blob_sha}"
        ] = merge_group_workflow
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_GROUP_SHA}/check-runs?per_page=100"
        ] = check_runs(
            "ci-success",
            head_sha=MERGE_GROUP_SHA,
            suite_id=MERGE_GROUP_CHECK_SUITE_ID,
        )
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_GROUP_SHA}/status?per_page=100"
        ] = {"total_count": 0, "statuses": []}
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/actions/runs?check_suite_id={MERGE_GROUP_CHECK_SUITE_ID}&per_page=100"
        ] = {
            "total_count": 1,
            "workflow_runs": [
                {
                    "check_suite_id": MERGE_GROUP_CHECK_SUITE_ID,
                    "head_sha": MERGE_GROUP_SHA,
                    "event": "merge_group",
                    "path": ".github/workflows/ci.yml",
                }
            ],
        }

    def test_run_produces_app_bound_protection_input(self) -> None:
        self.configure()

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(preflight_args("ci-success"))

        self.assertEqual(result["decision"], "may-configure-classic-protection")
        self.assertEqual(result["repository"], f"{OWNER}/{REPOSITORY}")
        self.assertEqual(result["default_branch"], "main")
        self.assertTrue(result["administration_permission"])
        self.assertEqual(
            result["required_checks"],
            [
                {
                    "context": "ci-success",
                    "app_id": 15368,
                    "producer": ".github/workflows/ci.yml#ci-success",
                }
            ],
        )

    def test_run_discovers_workflow_producers_from_test_merge_tree(self) -> None:
        self.configure()
        tree_path = f"repos/{OWNER}/{REPOSITORY}/git/trees/{MERGE_SHA}?recursive=1"
        tree = cast(dict[str, Any], FakeClient.responses[tree_path])
        tree["tree"].append(
            {
                "type": "blob",
                "mode": "100644",
                "path": ".github/workflows/base-ci.yml",
                "sha": git_blob_id(self.WORKFLOW),
            }
        )
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/git/blobs/{git_blob_id(self.WORKFLOW)}"
        ] = self.WORKFLOW

        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "2 workflow producers"
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_accepts_trusted_pull_request_target_for_default_branch(self) -> None:
        workflow = self.WORKFLOW.replace(
            "  pull_request:\n",
            "  pull_request_target:\n    branches: [main]\n",
        )
        self.configure(workflow)

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(preflight_args("ci-success"))

        self.assertEqual(result["decision"], "may-configure-classic-protection")

    def test_run_rejects_pull_request_target_changed_from_verified_base(self) -> None:
        trusted_workflow = self.WORKFLOW.replace(
            "  pull_request:\n",
            "  pull_request_target:\n    branches: [main]\n",
        )
        changed_workflow = trusted_workflow.replace(
            "run: echo checked", "run: echo bypass"
        )
        self.configure(changed_workflow, base_workflow=trusted_workflow)

        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "differs from the verified base branch",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_pull_request_target_added_only_in_the_pr(self) -> None:
        target_workflow = self.WORKFLOW.replace(
            "  pull_request:\n",
            "  pull_request_target:\n    branches: [main]\n",
        )
        self.configure(target_workflow, base_workflow=self.WORKFLOW)

        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "missing from or differs from the verified base branch",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_stale_pull_request_target_base(self) -> None:
        target_workflow = self.WORKFLOW.replace(
            "  pull_request:\n",
            "  pull_request_target:\n    branches: [main]\n",
        )
        self.configure(target_workflow)
        pull_request = cast(
            dict[str, Any], FakeClient.responses[f"repos/{OWNER}/{REPOSITORY}/pulls/7"]
        )
        pull_request["base"]["sha"] = "f" * 40

        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "does not match the current default branch commit",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_missing_pull_request_base_sha_for_target(self) -> None:
        target_workflow = self.WORKFLOW.replace(
            "  pull_request:\n",
            "  pull_request_target:\n    branches: [main]\n",
        )
        self.configure(target_workflow)
        pull_request = cast(
            dict[str, Any], FakeClient.responses[f"repos/{OWNER}/{REPOSITORY}/pulls/7"]
        )
        pull_request["base"]["sha"] = None

        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "no verified base commit",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_stale_or_missing_default_branch(self) -> None:
        for branch in (None, "develop", "Main", 7):
            self.configure()
            repository = cast(
                dict[str, Any], FakeClient.responses[f"repos/{OWNER}/{REPOSITORY}"]
            )
            repository["default_branch"] = branch
            with (
                self.subTest(branch=branch),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError,
                    "current default branch",
                ),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_pull_request_for_unverified_target(self) -> None:
        for base in (
            None,
            {},
            {"ref": "develop"},
            {"ref": "Main"},
            {"ref": "main", "repo": None},
            {"ref": "main", "repo": {}},
            {"ref": "main", "repo": {"full_name": 7}},
            {"ref": "main", "repo": {"full_name": "octo/other"}},
        ):
            self.configure()
            pr = cast(
                dict[str, Any],
                FakeClient.responses[f"repos/{OWNER}/{REPOSITORY}/pulls/7"],
            )
            pr["base"] = base
            with (
                self.subTest(base=base),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError,
                    "target repository and branch",
                ),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_non_open_representative_pull_request(self) -> None:
        for state in (None, "closed", "all", True):
            with self.subTest(state=state):
                self.configure()
                pr = cast(
                    dict[str, Any],
                    FakeClient.responses[f"repos/{OWNER}/{REPOSITORY}/pulls/7"],
                )
                pr["state"] = state
                with (
                    mock.patch.object(
                        branch_protection_preflight, "GitHubClient", FakeClient
                    ),
                    self.assertRaisesRegex(
                        branch_protection_preflight.InspectionError, "not open"
                    ),
                ):
                    branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_accepts_fork_head_with_matching_base_repository(self) -> None:
        self.configure()
        pr = cast(
            dict[str, Any], FakeClient.responses[f"repos/{OWNER}/{REPOSITORY}/pulls/7"]
        )
        pr["base"]["repo"]["full_name"] = "OCTO/EXAMPLE"
        pr["head"]["repo"] = {"full_name": "contributor/example"}
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            self.assertEqual(
                branch_protection_preflight.run(preflight_args("ci-success"))[
                    "decision"
                ],
                "may-configure-classic-protection",
            )

    def test_run_rejects_ineligible_repository_or_default_branch(self) -> None:
        self.configure()
        cases: list[tuple[object, str]] = [
            ([], "response is invalid"),
            (
                {
                    "full_name": "octo/other",
                    "archived": False,
                    "disabled": False,
                    "permissions": {"admin": True},
                },
                "different repository",
            ),
            (
                {
                    "full_name": f"{OWNER}/{REPOSITORY}",
                    "archived": True,
                    "disabled": False,
                    "permissions": {"admin": True},
                },
                "Archived",
            ),
            (
                {
                    "full_name": f"{OWNER}/{REPOSITORY}",
                    "archived": False,
                    "disabled": True,
                    "permissions": {"admin": True},
                },
                "Disabled",
            ),
            (
                {
                    "full_name": f"{OWNER}/{REPOSITORY}",
                    "archived": False,
                    "disabled": "no",
                    "permissions": {"admin": True},
                },
                "invalid 'disabled'",
            ),
            (
                {
                    "full_name": f"{OWNER}/{REPOSITORY}",
                    "archived": False,
                    "disabled": False,
                    "permissions": {},
                },
                "administration permission",
            ),
            (
                {
                    "full_name": f"{OWNER}/{REPOSITORY}",
                    "archived": False,
                    "disabled": False,
                    "permissions": {"admin": "yes"},
                },
                "invalid 'admin'",
            ),
            (
                {
                    "full_name": f"{OWNER}/{REPOSITORY}",
                    "archived": False,
                    "disabled": False,
                    "permissions": {"admin": False},
                },
                "administration permission",
            ),
        ]
        for response, message in cases:
            if isinstance(response, dict):
                response = {"id": 42, **response}
            FakeClient.responses[f"repos/{OWNER}/{REPOSITORY}"] = response
            with self.subTest(response=response):
                with mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        branch_protection_preflight.InspectionError, message
                    ):
                        branch_protection_preflight.run(preflight_args("ci-success"))

        self.configure()
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "Default branch"
            ):
                branch_protection_preflight.run(
                    preflight_args("ci-success", default_branch="\n")
                )

    def test_reference_binds_preflight_target_before_protection_mutation(self) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        protection = setup.split("## Branch protection", 1)[1].split("\n## ", 1)[0]
        self.assertIn("branch_protection_preflight.py", protection)
        self.assertIn("requires current administration", protection)
        self.assertIn("$defaultBranch = $repoView.defaultBranchRef.name", protection)
        self.assertIn("$requiredCheckPreflight.repository", protection)
        self.assertIn("$requiredCheckPreflight.default_branch", protection)
        self.assertIn("changed after preflight", protection)
        self.assertIn("Assert-FreshRequiredCheckPreflight", protection)
        self.assertIn("--merge-group-sha", protection)
        self.assertIn("$fresh.merge_group_sha", protection)
        self.assertIn("$fresh.head_sha", protection)
        self.assertIn("$fresh.test_merge_sha", protection)
        self.assertIn("$freshBindings", protection)
        self.assertIn("check_suite.id", protection)
        self.assertIn("workflow_dispatch", protection)
        self.assertIn("Actions: read", protection)

    def test_reference_carries_preflight_contexts_and_app_ids_into_mutation(
        self,
    ) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        protection = setup.split("## Branch protection", 1)[1].split("\n## ", 1)[0]
        preflight_setup, mutation = protection.split("PowerShell example:", 1)

        self.assertIn("foreach ($context in $requiredCheckNames)", preflight_setup)
        self.assertIn(
            '$preflightArguments += @("--required-check", [string]$context)',
            preflight_setup,
        )
        self.assertIn(
            "$requiredAppIdsByContext[[string]$check.context] = [int64]$check.app_id",
            preflight_setup,
        )
        self.assertIn(
            "$hasMergeQueue -ne [bool]$requiredCheckPreflight.merge_queue_required",
            mutation,
        )
        self.assertNotIn("$effectiveWorkflowChecks", mutation)
        self.assertNotIn("$requiredCheckNames = @()", mutation)
        self.assertNotIn("$requiredAppIdsByContext =", mutation)
        fresh_check = mutation.index("Assert-FreshRequiredCheckPreflight")
        first_protection_write = min(
            mutation.index("-X PATCH"), mutation.index("-X PUT")
        )
        self.assertLess(fresh_check, first_protection_write)

    def test_reference_revalidates_each_non_atomic_protection_write(self) -> None:
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        protection = setup.split("## Branch protection", 1)[1].split("\n## ", 1)[0]
        mutation = protection.split("PowerShell example:", 1)[1]
        existing_branch = mutation.split("if ($protectionExitCode -eq 0)", 1)[1].split(
            "} elseif (($protectionOutput | Out-String) -match '(?is)Branch not protected.*HTTP 404')",
            1,
        )[0]
        writes = (
            "$statusUpdateOutput = $statusPayload",
            "$adminUpdateOutput = gh api",
            "$reviewUpdateOutput =",
        )
        write_positions = [existing_branch.index(marker) for marker in writes]
        fresh_positions = [
            match.start()
            for match in re.finditer(
                r"\$(?:requiredCheckPreflight|null) = Assert-FreshRequiredCheckPreflight",
                existing_branch,
            )
        ]
        for index, write_position in enumerate(write_positions):
            previous_write = write_positions[index - 1] if index else -1
            self.assertTrue(
                any(
                    previous_write < position < write_position
                    for position in fresh_positions
                ),
                f"branch protection write {writes[index]!r} lacks its own fresh preflight",
            )

        for read_marker, write_position in zip(
            (
                "$statusProtectionBeforeWriteOutput",
                "$adminStateOutput",
                "$reviewProtectionBeforeWriteOutput",
            ),
            write_positions,
            strict=True,
        ):
            self.assertLess(existing_branch.index(read_marker), write_position)

        self.assertIn("$statusProtectionBeforeWriteOutput", existing_branch)
        self.assertIn("Get-RequiredStatusCheckSnapshot", existing_branch)
        self.assertIn("$initialStatusCheckState", existing_branch)
        self.assertIn("$reviewProtectionBeforeWrite", existing_branch)
        self.assertIn("A concurrent review policy appeared", existing_branch)
        self.assertIn("$fresh.merge_queue_required -isnot [bool]", protection)
        self.assertIn("same workflow blob, context, and App ID", protection)
        self.assertIn(
            "$fresh.merge_queue_required -ne $requiredCheckPreflight.merge_queue_required",
            protection,
        )
        self.assertIn("function ConvertTo-BranchProtectionAppId", protection)
        self.assertIn("$Value -gt 0", protection)
        self.assertIn("$null = Get-RequiredStatusCheckSnapshot $Protection", protection)
        self.assertIn("function Get-BranchProtectionReviewState", protection)
        self.assertIn("$expectedReviewApprovalCount = 0", protection)
        self.assertIn("$ExpectedReviewApprovalCount", protection)
        self.assertIn(
            "$Protection.required_status_checks.strict -isnot [bool]", protection
        )
        self.assertIn("$Protection.enforce_admins.enabled -isnot [bool]", protection)
        self.assertIn('if ($null -eq $statusProperty) { return "null" }', protection)
        self.assertIn("Required-status-check bindings are incomplete", protection)
        self.assertIn("if ($null -ne $strictProperty)", protection)
        self.assertIn("$null -eq $check.PSObject.Properties['app_id']", protection)

        create_branch = mutation.split(
            "} elseif (($protectionOutput | Out-String) -match '(?is)Branch not protected.*HTTP 404')",
            1,
        )[1].split(
            "} elseif (($protectionOutput | Out-String) -match '(?is)HTTP 403')",
            1,
        )[0]
        self.assertIn("Assert-FreshRequiredCheckPreflight", create_branch)
        self.assertIn("$protectionBeforeCreateOutput", create_branch)
        self.assertIn("protection appeared after the initial read", create_branch)
        self.assertLess(
            create_branch.index("$protectionBeforeCreateOutput"),
            create_branch.index("$createOutput = $payload"),
        )
        self.assertIn("does not document conditional writes", protection)
        self.assertIn("request-sized race", protection)

    def test_run_rejects_multiple_workflow_producers(self) -> None:
        self.configure(
            self.WORKFLOW
            + """\n  another-gate:
    name: ci-success
    runs-on: ubuntu-latest
    steps:
      - run: echo duplicate
"""
        )

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "2 workflow producers"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_commit_status_collision(self) -> None:
        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_SHA}/status?per_page=100"
        ] = {"total_count": 1, "statuses": [{"context": "ci-success"}]}

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "Commit Status"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_uses_the_head_when_the_test_merge_has_no_statuses(self) -> None:
        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_SHA}/check-runs?per_page=100"
        ] = {"total_count": 0, "check_runs": []}

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(preflight_args("ci-success"))

        self.assertEqual(result["decision"], "may-configure-classic-protection")
        self.assertEqual(result["required_checks"][0]["app_id"], 15368)

    def test_run_uses_test_merge_evidence_without_requiring_head_evidence(self) -> None:
        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/check-runs?per_page=100"
        ] = {"total_count": 0, "check_runs": []}

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(preflight_args("ci-success"))

        self.assertEqual(result["decision"], "may-configure-classic-protection")
        self.assertEqual(result["required_checks"][0]["app_id"], 15368)

    def test_run_does_not_fall_back_to_head_when_merge_has_other_checks(self) -> None:
        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_SHA}/check-runs?per_page=100"
        ] = check_runs("different-check")

        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "no Check Run evidence on the controlling test-merge SHA",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_binds_check_run_to_eligible_workflow_run(self) -> None:
        self.configure()
        endpoint = f"repos/{OWNER}/{REPOSITORY}/actions/runs?check_suite_id={CHECK_SUITE_ID}&per_page=100"
        original = cast(dict[str, Any], FakeClient.responses[endpoint])
        original_run = cast(dict[str, Any], original["workflow_runs"][0])
        invalid_runs = [
            ({**original_run, "event": "workflow_dispatch"}, "eligible for required"),
            (
                {**original_run, "event": "pull_request_review_comment"},
                "eligible for required",
            ),
            ({**original_run, "path": ".github/workflows/other.yml"}, "workflow path"),
            ({**original_run, "head_sha": HEAD_SHA}, "workflow path"),
        ]
        for invalid_run, message in invalid_runs:
            FakeClient.responses[endpoint] = {
                "total_count": 1,
                "workflow_runs": [invalid_run],
            }
            with (
                self.subTest(invalid_run=invalid_run),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, message
                ),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

        FakeClient.responses[endpoint] = {"total_count": 0, "workflow_runs": []}
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "no unique Actions workflow run",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

        for invalid_suite_id in (True, CHECK_SUITE_ID + 1):
            FakeClient.responses[endpoint] = {
                "total_count": 1,
                "workflow_runs": [{**original_run, "check_suite_id": invalid_suite_id}],
            }
            with (
                self.subTest(check_suite_id=invalid_suite_id),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError,
                    "invalid Actions workflow-run Check Suite ID",
                ),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

        FakeClient.responses[endpoint] = {"total_count": 101, "workflow_runs": []}
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "workflow-runs response has an invalid or incomplete",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

        invalid_payloads: list[tuple[object, str]] = [
            ([], "Actions workflow-runs response is invalid"),
            ({"total_count": True, "workflow_runs": []}, "invalid or incomplete"),
            ({"total_count": 1, "workflow_runs": [None]}, "invalid or incomplete"),
        ]
        for payload, message in invalid_payloads:
            FakeClient.responses[endpoint] = payload
            with (
                self.subTest(workflow_runs=payload),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, message
                ),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

        valid_run = cast(dict[str, Any], original_run)
        FakeClient.responses[endpoint] = {
            "total_count": 2,
            "workflow_runs": [valid_run, dict(valid_run)],
        }
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "no unique Actions workflow run",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_accepts_action_run_path_with_ref_suffix(self) -> None:
        self.configure()
        endpoint = f"repos/{OWNER}/{REPOSITORY}/actions/runs?check_suite_id={CHECK_SUITE_ID}&per_page=100"
        action_run_response = cast(dict[str, Any], FakeClient.responses[endpoint])
        action_runs = cast(list[dict[str, Any]], action_run_response["workflow_runs"])
        action_runs[0]["path"] = ".github/workflows/ci.yml@refs/pull/7/merge"

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(preflight_args("ci-success"))

        self.assertEqual(result["decision"], "may-configure-classic-protection")

    def test_run_rejects_check_run_without_valid_sha_or_suite(self) -> None:
        self.configure()
        endpoint = (
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_SHA}/check-runs?per_page=100"
        )
        original = cast(dict[str, Any], FakeClient.responses[endpoint])
        original_run = cast(dict[str, Any], original["check_runs"][0])
        invalid_runs = [
            ({**original_run, "head_sha": "f" * 40}),
            ({**original_run, "check_suite": {"id": True}}),
            (
                {
                    key: value
                    for key, value in original_run.items()
                    if key != "check_suite"
                }
            ),
        ]
        for invalid_run in invalid_runs:
            FakeClient.responses[endpoint] = {
                "total_count": 1,
                "check_runs": [invalid_run],
            }
            with (
                self.subTest(check_run=invalid_run),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError,
                    "no valid Check Run SHA or suite ID",
                ),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_ignores_noncontrolling_head_status_collisions(self) -> None:
        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/status?per_page=100"
        ] = {"total_count": 1, "statuses": [{"context": "ci-success"}]}

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(preflight_args("ci-success"))

        self.assertEqual(result["decision"], "may-configure-classic-protection")

    def test_run_requires_merge_group_coverage_when_queue_applies(self) -> None:
        workflow = self.WORKFLOW.replace(
            "  merge_group:\n    types: [checks_requested]\n", ""
        )
        self.configure_merge_group(workflow)
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
        ] = [{"type": "merge_queue"}]
        args = preflight_args("ci-success", merge_group_sha=MERGE_GROUP_SHA)

        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "merge_group"
            ):
                branch_protection_preflight.run(args)

    def test_queue_preflight_requires_and_verifies_merge_group_evidence(self) -> None:
        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
        ] = [{"type": "merge_queue"}]
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "full merge-group SHA is required",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

        self.configure_merge_group()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
        ] = [{"type": "merge_queue"}]
        args = preflight_args("ci-success", merge_group_sha=MERGE_GROUP_SHA)
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(args)
        self.assertTrue(result["merge_queue_required"])
        self.assertEqual(result["merge_group_sha"], MERGE_GROUP_SHA)

    def test_queue_preflight_rejects_wrong_event_or_different_app(self) -> None:
        self.configure_merge_group()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
        ] = [{"type": "merge_queue"}]
        run_endpoint = (
            f"repos/{OWNER}/{REPOSITORY}/actions/runs?"
            f"check_suite_id={MERGE_GROUP_CHECK_SUITE_ID}&per_page=100"
        )
        run_payload = cast(dict[str, Any], FakeClient.responses[run_endpoint])
        run_payload["workflow_runs"][0]["event"] = "push"
        args = preflight_args("ci-success", merge_group_sha=MERGE_GROUP_SHA)
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "event eligible for required status checks",
            ),
        ):
            branch_protection_preflight.run(args)

        self.configure_merge_group()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
        ] = [{"type": "merge_queue"}]
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_GROUP_SHA}/check-runs?per_page=100"
        ] = check_runs(
            "ci-success",
            app_id=1,
            head_sha=MERGE_GROUP_SHA,
            suite_id=MERGE_GROUP_CHECK_SUITE_ID,
        )
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "different GitHub Apps",
            ),
        ):
            branch_protection_preflight.run(args)

    def test_queue_preflight_rejects_missing_duplicate_or_changed_producers(
        self,
    ) -> None:
        no_required_job = self.WORKFLOW.replace("name: ci-success", "name: other")
        duplicate_job = (
            self.WORKFLOW
            + """
  duplicate:
    name: ci-success
    runs-on: ubuntu-latest
    steps:
      - run: echo duplicate
"""
        )
        for workflow, expected in (
            (no_required_job, "has 0 merge_group workflow producers"),
            (duplicate_job, "has 2 merge_group workflow producers"),
        ):
            self.configure_merge_group(merge_group_workflow=workflow)
            FakeClient.responses[
                f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
            ] = [{"type": "merge_queue"}]
            with (
                self.subTest(expected=expected),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, expected
                ),
            ):
                branch_protection_preflight.run(
                    preflight_args("ci-success", merge_group_sha=MERGE_GROUP_SHA)
                )

        changed_workflow = self.WORKFLOW.replace("echo checked", "echo changed")
        self.configure_merge_group(merge_group_workflow=changed_workflow)
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
        ] = [{"type": "merge_queue"}]
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "no matching executable merge_group producer",
            ),
        ):
            branch_protection_preflight.run(
                preflight_args("ci-success", merge_group_sha=MERGE_GROUP_SHA)
            )

    def test_queue_preflight_rejects_missing_merge_group_check_evidence(self) -> None:
        self.configure_merge_group()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
        ] = [{"type": "merge_queue"}]
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_GROUP_SHA}/check-runs?per_page=100"
        ] = {"total_count": 0, "check_runs": []}
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "no successful Check Run on the verified merge_group SHA",
            ),
        ):
            branch_protection_preflight.run(
                preflight_args("ci-success", merge_group_sha=MERGE_GROUP_SHA)
            )

    def test_merge_group_sha_is_inapplicable_without_an_effective_queue(self) -> None:
        self.configure()
        args = preflight_args("ci-success", merge_group_sha=MERGE_GROUP_SHA)
        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "no effective merge queue applies",
            ),
        ):
            branch_protection_preflight.run(args)

    def test_run_does_not_coerce_check_run_names_into_required_contexts(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("True", True),
            ("{}", {}),
            ("None", None),
            ("7", 7),
        )
        for context, name in cases:
            workflow = self.WORKFLOW.replace("name: ci-success", f"name: '{context}'")
            for receipt_name, accepted in ((name, False), (context, True)):
                self.configure(workflow)
                endpoint = f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_SHA}/check-runs?per_page=100"
                payload = cast(dict[str, Any], FakeClient.responses[endpoint])
                payload["check_runs"][0]["name"] = receipt_name
                with (
                    self.subTest(context=context, name=receipt_name),
                    mock.patch.object(
                        branch_protection_preflight, "GitHubClient", FakeClient
                    ),
                ):
                    if accepted:
                        verdict = branch_protection_preflight.run(
                            preflight_args(context)
                        )
                        self.assertEqual(
                            verdict["decision"], "may-configure-classic-protection"
                        )
                    else:
                        with self.assertRaisesRegex(
                            branch_protection_preflight.InspectionError,
                            "Check Run evidence",
                        ):
                            branch_protection_preflight.run(preflight_args(context))

    def test_run_rejects_check_run_that_is_not_completed(self) -> None:
        statuses: tuple[object, ...] = (
            "missing",
            None,
            False,
            0,
            [],
            {},
            "queued",
            "in_progress",
            "Completed",
            "completed ",
            "completed",
        )
        for status in statuses:
            self.configure()
            endpoint = f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_SHA}/check-runs?per_page=100"
            payload = cast(dict[str, Any], FakeClient.responses[endpoint])
            check_run = payload["check_runs"][0]
            if status == "missing":
                check_run.pop("status")
            else:
                check_run["status"] = status
            with (
                self.subTest(status=status),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
            ):
                if status == "completed":
                    verdict = branch_protection_preflight.run(
                        preflight_args("ci-success")
                    )
                    self.assertEqual(
                        verdict["decision"], "may-configure-classic-protection"
                    )
                else:
                    with self.assertRaisesRegex(
                        branch_protection_preflight.InspectionError,
                        "completed Check Run",
                    ):
                        branch_protection_preflight.run(preflight_args("ci-success"))

    def test_check_run_freshness_has_both_time_boundaries(self) -> None:
        now = datetime(2026, 9, 8, tzinfo=timezone.utc)
        for offset, accepted in (
            (timedelta(days=-7), True),
            (timedelta(0), True),
            (timedelta(days=-7, microseconds=-1), False),
            (timedelta(microseconds=1), False),
        ):
            payload = check_runs("ci-success")
            payload["check_runs"][0]["completed_at"] = (now + offset).isoformat()
            with self.subTest(offset=offset):
                if accepted:
                    self.assertEqual(
                        branch_protection_preflight.app_id_for_check(
                            payload, "ci-success", now
                        ),
                        15368,
                    )
                else:
                    with self.assertRaises(branch_protection_preflight.InspectionError):
                        branch_protection_preflight.app_id_for_check(
                            payload, "ci-success", now
                        )

    def test_run_rejects_malformed_effective_rule_entries(self) -> None:
        for rule in (
            None,
            "merge_queue",
            {},
            {"type": None},
            {"type": 7},
            {"type": ""},
        ):
            self.configure()
            FakeClient.responses[
                f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
            ] = [rule]
            with (
                self.subTest(rule=rule),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, "Effective rule"
                ),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_evidence_validation_rejects_ambiguous_or_stale_data(self) -> None:
        now = datetime.now(timezone.utc)
        valid = check_runs("ci-success")
        invalid_payloads = [
            {},
            {"total_count": True, "check_runs": []},
            {"total_count": -1, "check_runs": []},
            {"total_count": 101, "check_runs": []},
            {"total_count": 0, "check_runs": []},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(branch_protection_preflight.InspectionError):
                    branch_protection_preflight.app_id_for_check(
                        payload, "ci-success", now
                    )

        base = cast(dict[str, Any], valid["check_runs"][0])
        for payload in (
            {"total_count": 0, "check_runs": [base]},
            {"total_count": 1, "check_runs": [None]},
        ):
            with self.subTest(incomplete_payload=payload):
                with self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError,
                    "invalid or incomplete",
                ):
                    branch_protection_preflight.app_id_for_check(
                        payload, "ci-success", now
                    )
        evidence_updates: list[tuple[dict[str, Any], str]] = [
            ({"app": {}}, "incomplete"),
            ({"app": {"id": True}}, "incomplete"),
            ({"app": {"id": False}}, "incomplete"),
            ({"app": {"id": "15368"}}, "incomplete"),
            ({"app": {"id": 0}}, "incomplete"),
            ({"app": {"id": -1}}, "incomplete"),
            ({"completed_at": "not-a-time"}, "invalid completion"),
            ({"completed_at": "2026-01-01T00:00:00"}, "timezone-less"),
        ]
        for update, message in evidence_updates:
            item = dict(base, **update)
            with self.subTest(update=update):
                with self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, message
                ):
                    branch_protection_preflight.app_id_for_check(
                        {"total_count": 1, "check_runs": [item]}, "ci-success", now
                    )
        conflict = dict(base, app={"id": 2})
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "multiple matching Check Runs"
        ):
            branch_protection_preflight.app_id_for_check(
                {"total_count": 2, "check_runs": [base, conflict]}, "ci-success", now
            )
        stale = dict(base, completed_at="2000-01-01T00:00:00+00:00")
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "successful recent"
        ):
            branch_protection_preflight.app_id_for_check(
                {"total_count": 1, "check_runs": [stale]}, "ci-success", now
            )

    def test_inspect_evidence_rejects_invalid_statuses(self) -> None:
        client = FakeClient("github.com")
        check_endpoint = (
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/check-runs?per_page=100"
        )
        status_endpoint = (
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/status?per_page=100"
        )
        FakeClient.responses = {check_endpoint: check_runs("ci-success")}
        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError, "full Git object"
        ):
            branch_protection_preflight.inspect_evidence(
                client,
                OWNER,
                REPOSITORY,
                "short",
                "ci-success",
                datetime.now(timezone.utc),
                ".github/workflows/ci.yml",
            )
        for status in [
            {},
            {"total_count": True, "statuses": []},
            {"total_count": -1, "statuses": []},
            {"total_count": 101, "statuses": []},
            {"total_count": 1, "statuses": [{"context": "CI-SUCCESS"}]},
            {"total_count": 0, "statuses": [{"context": "other"}]},
            {"total_count": 1, "statuses": [None]},
        ]:
            FakeClient.responses[status_endpoint] = status
            with self.subTest(status=status):
                with self.assertRaises(branch_protection_preflight.InspectionError):
                    branch_protection_preflight.inspect_evidence(
                        client,
                        OWNER,
                        REPOSITORY,
                        HEAD_SHA,
                        "ci-success",
                        datetime.now(timezone.utc),
                        ".github/workflows/ci.yml",
                    )

    def test_inspect_evidence_requires_a_check_run(self) -> None:
        client = FakeClient("github.com")
        FakeClient.responses = {
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/check-runs?per_page=100": {
                "total_count": 0,
                "check_runs": [],
            },
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/status?per_page=100": {
                "total_count": 0,
                "statuses": [],
            },
        }

        with self.assertRaisesRegex(
            branch_protection_preflight.InspectionError,
            "has no Check Run evidence",
        ):
            branch_protection_preflight.inspect_evidence(
                client,
                OWNER,
                REPOSITORY,
                HEAD_SHA,
                "ci-success",
                datetime.now(timezone.utc),
                ".github/workflows/ci.yml",
            )

    def test_inspect_evidence_returns_the_verified_app_id(self) -> None:
        client = FakeClient("github.com")
        FakeClient.responses = {
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/check-runs?per_page=100": check_runs(
                "ci-success", head_sha=HEAD_SHA, suite_id=HEAD_CHECK_SUITE_ID
            ),
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/status?per_page=100": {
                "total_count": 0,
                "statuses": [],
            },
            f"repos/{OWNER}/{REPOSITORY}/actions/runs?check_suite_id={HEAD_CHECK_SUITE_ID}&per_page=100": {
                "total_count": 1,
                "workflow_runs": [
                    {
                        "check_suite_id": HEAD_CHECK_SUITE_ID,
                        "head_sha": HEAD_SHA,
                        "event": "pull_request",
                        "path": ".github/workflows/ci.yml",
                    }
                ],
            },
        }

        app_id = branch_protection_preflight.inspect_evidence(
            client,
            OWNER,
            REPOSITORY,
            HEAD_SHA,
            "ci-success",
            datetime.now(timezone.utc),
            ".github/workflows/ci.yml",
        )

        self.assertEqual(app_id, 15368)

    def test_run_requires_check_run_on_the_controlling_head(self) -> None:
        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_SHA}/check-runs?per_page=100"
        ] = {"total_count": 0, "check_runs": []}
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{HEAD_SHA}/check-runs?per_page=100"
        ] = {"total_count": 0, "check_runs": []}

        with (
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            self.assertRaisesRegex(
                branch_protection_preflight.InspectionError,
                "no Check Run evidence on the controlling head SHA",
            ),
        ):
            branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_invalid_representative_pull_request_data(self) -> None:
        invalid_arguments: list[tuple[dict[str, object], str]] = [
            ({"hostname": "github.example"}, "GitHub.com only"),
            ({"pull_request": 0}, "positive"),
        ]
        for overrides, message in invalid_arguments:
            with self.subTest(overrides=overrides):
                args = preflight_args("ci-success")
                for key, value in overrides.items():
                    setattr(args, key, value)
                with self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError, message
                ):
                    branch_protection_preflight.run(args)

        self.configure()
        pull_endpoint = f"repos/{OWNER}/{REPOSITORY}/pulls/7"
        for head_sha, merge_sha in (
            ("main", MERGE_SHA),
            ("g" * 40, MERGE_SHA),
            (HEAD_SHA, "refs/pull/7/merge"),
            (HEAD_SHA, "1" * 39),
        ):
            self.configure()
            pull_request = cast(dict[str, Any], FakeClient.responses[pull_endpoint])
            pull_request["head"]["sha"] = head_sha
            pull_request["merge_commit_sha"] = merge_sha
            with (
                self.subTest(head_sha=head_sha, merge_sha=merge_sha),
                mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError,
                    "no valid full head or test-merge SHA",
                ),
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

        self.configure()
        for payload, message in [
            ([], "response is invalid"),
            (
                {"state": "open", "head": {}, "merge_commit_sha": MERGE_SHA},
                "no valid full head or test-merge SHA",
            ),
            (
                {
                    "state": "open",
                    "head": {"sha": HEAD_SHA},
                    "merge_commit_sha": MERGE_SHA,
                    "mergeable": False,
                },
                "not confirmed mergeable",
            ),
        ]:
            FakeClient.responses[pull_endpoint] = payload
            with self.subTest(payload=payload):
                with mock.patch.object(
                    branch_protection_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        branch_protection_preflight.InspectionError, message
                    ):
                        branch_protection_preflight.run(preflight_args("ci-success"))

        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
        ] = {}
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "rules response"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/rules/branches/main?per_page=100"
        ] = [{}] * 100
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "may be paginated"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

    def test_run_rejects_non_gate_producer_and_uses_controlling_app(self) -> None:
        self.configure(self.WORKFLOW.replace("if: ${{ always() }}", "if: false"))
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(
                branch_protection_preflight.InspectionError, "unconditional executable"
            ):
                branch_protection_preflight.run(preflight_args("ci-success"))

        for workflow in (
            self.WORKFLOW.replace(
                "      - run: echo checked",
                "      - if: false\n        run: echo checked",
            ),
            self.WORKFLOW.replace(
                "    runs-on: ubuntu-latest",
                "    runs-on: ubuntu-latest\n    continue-on-error: true",
            ),
            self.WORKFLOW.replace(
                "      - run: echo checked",
                "      - continue-on-error: true\n        run: echo checked",
            ),
        ):
            self.configure(workflow)
            with mock.patch.object(
                branch_protection_preflight, "GitHubClient", FakeClient
            ):
                with self.assertRaisesRegex(
                    branch_protection_preflight.InspectionError,
                    "unconditional executable",
                ):
                    branch_protection_preflight.run(preflight_args("ci-success"))

        self.configure()
        FakeClient.responses[
            f"repos/{OWNER}/{REPOSITORY}/commits/{MERGE_SHA}/check-runs?per_page=100"
        ] = check_runs("ci-success", app_id=1, head_sha=MERGE_SHA)
        with mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient):
            result = branch_protection_preflight.run(preflight_args("ci-success"))
        self.assertEqual(result["required_checks"][0]["app_id"], 1)

    def test_cli_reports_success_and_inconclusive_result(self) -> None:
        self.configure()
        with (
            mock.patch.object(
                branch_protection_preflight,
                "parse_args",
                return_value=preflight_args("ci-success"),
            ),
            mock.patch.object(branch_protection_preflight, "GitHubClient", FakeClient),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(branch_protection_preflight.main(), 0)
        self.assertIn("may-configure", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                branch_protection_preflight,
                "parse_args",
                side_effect=branch_protection_preflight.InspectionError("bad input"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(branch_protection_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

    def test_cli_accepts_merge_group_sha(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                str(SCRIPT_PATH),
                "--repository",
                f"{OWNER}/{REPOSITORY}",
                "--default-branch",
                "main",
                "--pull-request",
                "7",
                "--required-check",
                "ci-success",
                "--merge-group-sha",
                MERGE_GROUP_SHA,
            ],
        ):
            args = branch_protection_preflight.parse_args()
        self.assertEqual(args.merge_group_sha, MERGE_GROUP_SHA)

    def test_module_entrypoint_exits_for_invalid_cli_arguments(self) -> None:
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
