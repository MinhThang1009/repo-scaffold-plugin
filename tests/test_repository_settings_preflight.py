from __future__ import annotations

import argparse
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

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

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
        "expected_repository_id": 42,
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
        "id": 42,
        "archived": False,
        "disabled": False,
        "permissions": {"admin": True},
        "has_issues": False,
        "has_discussions": False,
        "topics": [],
    }
    value.update(overrides)
    return value


class RepositorySettingsPreflightTests(unittest.TestCase):
    @unittest.skipUnless(POWERSHELL, "requires PowerShell")
    def test_discovery_identity_rejects_array_wrapped_repository_receipts(self) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        section = reference.split("## Repository identity preflight", 1)[1].split(
            "## Description and topics", 1
        )[0]
        blocks = re.findall(r"```powershell\n(.*?)\n```", section, re.DOTALL)
        self.assertEqual(len(blocks), 2)
        valid_view = {
            "nameWithOwner": "OWNER/REPO",
            "url": "https://github.com/OWNER/REPO",
        }
        valid_identity = {"full_name": "OWNER/REPO", "id": 42}
        cases = (
            ("positive", valid_view, valid_identity, True),
            ("view-array", [valid_view], valid_identity, False),
            ("identity-array", valid_view, [valid_identity], False),
            ("missing-id", valid_view, {"full_name": "OWNER/REPO"}, False),
            ("text-id", valid_view, {**valid_identity, "id": "42"}, False),
        )
        prelude = r"""
$ErrorActionPreference='Stop'
$SELECTED_REPOSITORY_ID=$null
function gh {
  $global:LASTEXITCODE=0
  if ($args[0] -eq 'repo') { Get-Content -LiteralPath $env:VIEW -Raw }
  elseif ($args[0] -eq 'api') { Get-Content -LiteralPath $env:IDENTITY -Raw }
  else { throw 'Unexpected external command' }
}
$failure=$null
try {
"""
        suffix = r"""
} catch { $failure=$_.Exception.Message }
@{bound=($null -eq $failure -and $SELECTED_REPOSITORY_ID -eq 42);failure=$failure} | ConvertTo-Json -Compress
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "identity.ps1"
            script.write_bytes((prelude + blocks[1] + suffix).encode("utf-8-sig"))
            view = root / "view.json"
            identity = root / "identity.json"
            for label, view_receipt, identity_receipt, expected in cases:
                with self.subTest(case=label):
                    view.write_text(json.dumps(view_receipt), encoding="utf-8")
                    identity.write_text(json.dumps(identity_receipt), encoding="utf-8")
                    result = subprocess.run(
                        [
                            str(POWERSHELL),
                            "-NoProfile",
                            "-NonInteractive",
                            "-File",
                            str(script),
                        ],
                        env={
                            **os.environ,
                            "VIEW": str(view),
                            "IDENTITY": str(identity),
                        },
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    response = json.loads(result.stdout.decode("utf-8-sig"))
                    self.assertIs(response["bound"], expected, response)

    @unittest.skipUnless(POWERSHELL, "requires PowerShell")
    def test_communication_consent_must_be_boolean_before_becoming_a_preflight_request(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        section = reference.split("## Repository communication features", 1)[1]
        block = re.search(r"```powershell\n(.*?)\n```", section, re.DOTALL)
        assert block is not None
        source = block[1].split(
            "if ($enableIssuesRequested -or $enableDiscussionsRequested) {", 1
        )[0]
        for label, value in (
            ("true", True),
            ("false", False),
            ("text-false", "false"),
            ("text-true", "true"),
            ("number", 1),
            ("array", [True]),
            ("null", None),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture = root / "consent.json"
                fixture.write_text(json.dumps({"value": value}), encoding="utf-8")
                body = source.replace(
                    "$enableIssuesRequested = $false",
                    "$enableIssuesRequested = (Get-Content -Raw -LiteralPath $env:FIXTURE | ConvertFrom-Json).value",
                )
                command = root / "consent.ps1"
                command.write_bytes(
                    (
                        "$ErrorActionPreference='Stop'\n$requests=0; $failure=$null\ntry {\n"
                        + body
                        + "\nif ($enableIssuesRequested) { $requests++ }\n} catch { $failure=$_.Exception.Message }\n@{requests=$requests;failure=$failure} | ConvertTo-Json -Compress\n"
                    ).encode("utf-8-sig")
                )
                result = subprocess.run(
                    [
                        str(POWERSHELL),
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
                self.assertEqual(
                    observed["requests"], 1 if value is True else 0, observed
                )
                self.assertEqual(
                    observed["failure"] is None, isinstance(value, bool), observed
                )

    def test_unbound_identity_inspection_does_not_authorize_a_mutation(self) -> None:
        FakeClient.response = repository()
        with mock.patch.object(
            repository_settings_preflight, "GitHubClient", FakeClient
        ):
            result = repository_settings_preflight.run(
                arguments(
                    description=True,
                    description_value="Synthetic metadata",
                    expected_repository_id=None,
                )
            )
        self.assertEqual(result["decision"], "bind-repository-identity-before-mutation")
        self.assertEqual(result["repository_id"], 42)

    @unittest.skipUnless(POWERSHELL, "requires PowerShell")
    def test_selected_numeric_id_guard_blocks_changed_or_coerced_target_before_write(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        helper = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert helper is not None
        script_text = (
            r"""
$ErrorActionPreference='Stop'
$packet=(Get-Content -Raw -LiteralPath $env:FIXTURE) | ConvertFrom-Json
$SELECTED_REPOSITORY_ID=$packet.selected
"""
            + helper[0]
            + r"""
$mutations=0; $failure=$null
try { Assert-SelectedRepositoryId -RepositoryId $packet.current; $mutations += 1 }
catch { $failure=$_.Exception.Message }
@{mutations=$mutations;failure=$failure} | ConvertTo-Json -Compress
"""
        )
        cases = [
            (42, 42, 1),
            (42, 43, 0),
            (42, None, 0),
            (None, 42, 0),
            (42, "42", 0),
            ("42", 42, 0),
            (42, True, 0),
            (True, 42, 0),
            (42, [42], 0),
            (42, 42.0, 0),
            (42, 0, 0),
            (0, 42, 0),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "identity.ps1"
            script.write_bytes(script_text.encode("utf-8-sig"))
            for selected, current, writes in cases:
                with self.subTest(selected=selected, current=current):
                    fixture = root / "identity.json"
                    fixture.write_text(
                        json.dumps({"selected": selected, "current": current}),
                        encoding="utf-8",
                    )
                    environment = {**os.environ, "FIXTURE": str(fixture)}
                    process = subprocess.run(
                        [
                            str(POWERSHELL),
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
                    self.assertEqual(result["mutations"], writes, result)

    def test_repository_id_binds_repeated_inspection_to_the_selected_target(
        self,
    ) -> None:
        FakeClient.response = repository(id=42)
        args = arguments(
            description=True,
            description_value="Synthetic metadata",
            expected_repository_id=42,
        )
        with mock.patch.object(
            repository_settings_preflight, "GitHubClient", FakeClient
        ):
            result = repository_settings_preflight.run(args)
        self.assertEqual(result.get("repository_id"), 42)
        for changed in (43, None, True, "42", 0, -1, 42.5):
            with self.subTest(identity=changed):
                FakeClient.response = repository(id=changed)
                with (
                    mock.patch.object(
                        repository_settings_preflight, "GitHubClient", FakeClient
                    ),
                    self.assertRaisesRegex(
                        repository_settings_preflight.InspectionError,
                        "repository ID|repository identity",
                    ),
                ):
                    repository_settings_preflight.run(args)

    @unittest.skipUnless(
        POWERSHELL, "requires PowerShell for documented verdict-consumer integration"
    )
    def test_metadata_verdict_consumer_rejects_ambiguous_completion_before_write(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        section = reference.split("## Description and topics\n", 1)[1].split(
            "## Repository communication features\n", 1
        )[0]
        block = re.search(r"```powershell\n(.*?)\n```", section, re.DOTALL)
        self.assertIsNotNone(block)
        assert block is not None
        identity_helper = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert identity_helper is not None
        prelude = r"""
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0
$script:mutations = 0
$script:readbacks = 0
$script:failure = $null
$script:descriptionValue = 'Synthetic metadata'
$script:verdict = Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $PSScriptRoot 'verdict.json')
$script:packet = $script:verdict | ConvertFrom-Json
$REPO_SCAFFOLD_SKILL_ROOT = Join-Path $PSScriptRoot 'skill'
function Read-Host { return $script:descriptionValue }
function python { $global:LASTEXITCODE = 0; return $script:verdict }
function gh {
  param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments)
  $global:LASTEXITCODE = 0
  if ($Arguments[0] -eq 'repo' -and $Arguments[1] -eq 'edit') {
    if ($Arguments[2] -cne 'github.com/OWNER/REPO') { throw 'Unexpected binding' }
    $script:mutations += 1
    if ($script:packet.synthetic_edit_failure) { $global:LASTEXITCODE = 1 }
    return 'Synthetic write accepted'
  }
  if ($Arguments[0] -eq 'api') {
    $script:readbacks += 1
    $currentTopics = if ($script:packet.synthetic_partial_state) { @() } else { @('topic1','topic2') }
    return (@{id=42;full_name='OWNER/REPO';description=$script:descriptionValue;topics=@($currentTopics)} | ConvertTo-Json -Compress)
  }
  throw 'Unexpected backend operation'
}
try {
"""
        postlude = r"""
} catch { $script:failure = $_.Exception.Message }
@{mutations=$script:mutations;readbacks=$script:readbacks;failure=$script:failure} | ConvertTo-Json -Compress
"""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            scripts = target / "skill/scripts"
            scripts.mkdir(parents=True)
            shutil.copyfile(SCRIPT_PATH, scripts / SCRIPT_PATH.name)
            command_file = target / "consumer.ps1"
            command_file.write_bytes(
                (
                    prelude
                    + "\n$SELECTED_REPOSITORY_ID=42\n"
                    + identity_helper[0]
                    + "\n"
                    + block[1]
                    + "\n"
                    + postlude
                ).encode("utf-8-sig")
            )
            cases: list[tuple[object, bool, bool, int, int, dict[str, object]]] = [
                (*case, {})
                for case in (
                    (True, False, False, 1, 1),
                    (False, False, False, 0, 0),
                    ("false", False, False, 0, 0),
                    ("true", False, False, 0, 0),
                    (1, False, False, 0, 0),
                    (None, False, False, 0, 0),
                    (True, True, False, 1, 1),
                    (True, True, True, 1, 1),
                )
            ]
            shape_overrides: tuple[dict[str, object], ...] = (
                {"repository": ["OWNER/REPO"]},
                {"decision": ["may-configure-repository-settings"]},
                {"requested_mutations": ["description"]},
                {
                    "requested_settings": {
                        "description": ["Synthetic metadata"],
                        "topics": ["topic1", "topic2"],
                    }
                },
                {
                    "requested_settings": {
                        "description": "Synthetic metadata",
                        "topics": [["topic1"], ["topic2"]],
                    }
                },
                {"current_topics": [["topic1"]]},
            )
            cases.extend(
                (True, False, False, 0, 0, override) for override in shape_overrides
            )
            cases.append(
                (
                    True,
                    False,
                    False,
                    1,
                    1,
                    {
                        "confirm_no_topics": True,
                        "requested_mutations": ["description"],
                        "requested_settings": {
                            "description": "Synthetic metadata",
                            "topics": [],
                        },
                        "current_topics": None,
                    },
                )
            )
            for (
                completion,
                edit_failure,
                partial_state,
                expected_writes,
                expected_reads,
                overrides,
            ) in cases:
                with self.subTest(
                    completion=completion,
                    edit_failure=edit_failure,
                    partial_state=partial_state,
                    overrides=overrides,
                ):
                    verdict: dict[str, object] = {
                        "inspection_complete": completion,
                        "decision": "may-configure-repository-settings",
                        "repository": "OWNER/REPO",
                        "repository_id": 42,
                        "requested_mutations": ["description", "topics"],
                        "requested_settings": {
                            "description": "Synthetic metadata",
                            "topics": ["topic1", "topic2"],
                        },
                        "current_topics": [],
                        "synthetic_edit_failure": edit_failure,
                        "synthetic_partial_state": partial_state,
                    }
                    verdict.update(overrides)
                    body = block[1]
                    if overrides.get("confirm_no_topics"):
                        body = body.replace(
                            '$topics = @("topic1", "topic2")', "$topics = @()"
                        )
                    command_file.write_bytes(
                        (
                            prelude
                            + "\n$SELECTED_REPOSITORY_ID=42\n"
                            + identity_helper[0]
                            + "\n"
                            + body
                            + "\n"
                            + postlude
                        ).encode("utf-8-sig")
                    )
                    (target / "verdict.json").write_text(
                        json.dumps(verdict), encoding="utf-8"
                    )
                    result = subprocess.run(
                        [
                            str(POWERSHELL),
                            "-NoProfile",
                            "-NonInteractive",
                            "-File",
                            str(command_file),
                        ],
                        cwd=target,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    outcome = json.loads(result.stdout.strip().splitlines()[-1])
                    self.assertEqual(outcome["mutations"], expected_writes)
                    self.assertEqual(outcome["readbacks"], expected_reads)
                    if partial_state:
                        self.assertIn("partially applied", outcome["failure"])
                    elif expected_writes:
                        self.assertIsNone(outcome["failure"])

    def test_every_documented_completion_consumer_checks_boolean_type(self) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        consumers = list(
            re.finditer(
                r"-not (?P<variable>\$[A-Za-z][A-Za-z0-9]*)\.inspection_complete",
                reference,
            )
        )
        self.assertTrue(consumers)
        for consumer in consumers:
            with self.subTest(variable=consumer["variable"], offset=consumer.start()):
                prior = reference[max(0, consumer.start() - 160) : consumer.start()]
                self.assertIn(
                    consumer["variable"] + ".inspection_complete -isnot [bool]", prior
                )

    @unittest.skipUnless(POWERSHELL, "requires PowerShell")
    def test_label_readback_cannot_certify_coerced_receipts_or_replaced_targets(
        self,
    ) -> None:
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        identity = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        writer = re.search(
            r"function Add-LabelIfMissing \{.*?\n\}", reference, re.DOTALL
        )
        assert identity is not None and writer is not None
        helper_sources: list[str] = []
        for name in ("Assert-CurrentLabelRepository", "Get-BoundActiveLabel"):
            helper = re.search(
                r"function " + name + r" \{.*?\n\}", reference, re.DOTALL
            )
            assert helper is not None
            helper_sources.append(helper[0])
        label_helpers = "\n".join(helper_sources)
        prefix = r"""
$ErrorActionPreference='Stop'
$SELECTED_REPOSITORY_ID=42
$script:packet=Get-Content -Raw -LiteralPath $env:FIXTURE | ConvertFrom-Json
$script:writes=0; $script:labelReads=0; $script:repoReads=0
$existingLabels=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
if ($script:packet.existing) { [void]$existingLabels.Add('bug') }
function Get-ValidatedLabelPreflight { return @{repository_id=42} }
function gh {
  param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments)
  $global:LASTEXITCODE=0
  if ($Arguments[0] -eq 'label') {
    $script:writes++
    if ($script:packet.failed_ack) { $global:LASTEXITCODE=1 }
    return 'Synthetic create acknowledgment'
  }
  if ($Arguments[0] -eq 'api') {
    if ($Arguments[-1] -eq 'repos/OWNER/REPO') {
      $script:repoReads++
      $targetId = if ($script:packet.replaced -or ($script:packet.changed_after_label -and $script:labelReads -gt 0)) { 43 } else { 42 }
      return (@{id=$targetId;full_name='OWNER/REPO';archived=($script:packet.inactive -eq $true);disabled=$false} | ConvertTo-Json -Compress)
    }
    if ($Arguments[-1] -eq 'repos/OWNER/REPO/labels/bug') {
      $script:labelReads++
      return ($script:packet.label | ConvertTo-Json -Depth 8 -Compress)
    }
  }
  throw 'Unexpected endpoint or operation'
}
"""
        suffix = "\n$failure=$null\ntry { Add-LabelIfMissing -Name bug -Color d73a4a -Description 'Synthetic label' } catch { $failure=$_.Exception.Message }\n@{writes=$script:writes;labelReads=$script:labelReads;repoReads=$script:repoReads;verified=($null -eq $failure -and $existingLabels.Contains('bug'));failure=$failure} | ConvertTo-Json -Compress\n"
        base_label: dict[str, object] = {
            "id": 91,
            "name": "bug",
            "color": "d73a4a",
            "description": "Synthetic label",
            "archived_at": None,
            "archived_by": None,
        }
        cases: list[tuple[dict[str, object], bool]] = [
            ({}, True),
            ({"failed_ack": True}, True),
            ({"existing": True}, True),
            ({"replaced": True}, False),
            ({"changed_after_label": True}, False),
            ({"inactive": True}, False),
            ({"label": {**base_label, "name": ["bug"]}}, False),
            ({"label": {**base_label, "color": ["d73a4a"]}}, False),
            ({"label": {**base_label, "description": ["Synthetic label"]}}, False),
            ({"label": {**base_label, "id": "91"}}, False),
            (
                {
                    "label": {
                        **base_label,
                        "archived_at": None,
                        "archived_by": {"id": 7},
                    }
                },
                False,
            ),
            (
                {
                    "label": {
                        key: value
                        for key, value in base_label.items()
                        if key != "archived_at"
                    }
                },
                False,
            ),
            ({"label": {**base_label, "archived_at": "2026-10-04T00:00:00Z"}}, False),
            (
                {
                    "existing": True,
                    "label": {**base_label, "archived_at": "2026-10-04T00:00:00Z"},
                },
                False,
            ),
            (
                {
                    "existing": True,
                    "label": {**base_label, "description": None, "color": "abcdef"},
                },
                True,
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "label-post-write.ps1"
            script.write_bytes(
                (prefix + identity[0] + label_helpers + writer[0] + suffix).encode(
                    "utf-8-sig"
                )
            )
            fixture = root / "receipt.json"
            for overrides, verified in cases:
                with self.subTest(overrides=overrides):
                    fixture.write_text(
                        json.dumps({"label": base_label, **overrides}), encoding="utf-8"
                    )
                    result = subprocess.run(
                        [
                            str(POWERSHELL),
                            "-NoProfile",
                            "-NonInteractive",
                            "-File",
                            str(script),
                        ],
                        env={**os.environ, "FIXTURE": str(fixture)},
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    outcome = json.loads(
                        result.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                    )
                    self.assertEqual(outcome["verified"], verified, outcome)
                    if overrides.get("existing"):
                        self.assertEqual(outcome["writes"], 0)
                    else:
                        self.assertEqual(outcome["writes"], 1)
                    if verified:
                        self.assertIsNone(outcome["failure"])
                    else:
                        self.assertIsNotNone(outcome["failure"])

    @unittest.skipUnless(POWERSHELL, "requires PowerShell")
    def test_fresh_communication_and_label_guards_reject_coerced_plans(self) -> None:
        FakeClient.response = repository()
        reference = (
            PLUGIN_ROOT / "skills/repo-scaffold/references/github-setup.md"
        ).read_text(encoding="utf-8")
        identity = re.search(
            r"function Assert-SelectedRepositoryId \{.*?\n\}", reference, re.DOTALL
        )
        assert identity is not None
        prefix = "$ErrorActionPreference='Stop'\n$SELECTED_REPOSITORY_ID=42\n$repositorySettingsPreflight=$env:PRODUCER\nfunction python { $global:LASTEXITCODE=0; Get-Content -Raw -LiteralPath $env:FIXTURE }\n"
        for name, args, invocation in (
            (
                "Test-FreshCommunicationPreflight",
                arguments(issues=True),
                "if (Test-FreshCommunicationPreflight -Feature issues) { $writes++ }",
            ),
            (
                "Get-ValidatedLabelPreflight",
                arguments(create_label=["bug"]),
                "$null=Get-ValidatedLabelPreflight -Name bug; $writes++",
            ),
        ):
            source = re.search(
                r"function " + name + r" \{.*?\n\}", reference, re.DOTALL
            )
            assert source is not None
            with mock.patch.object(
                repository_settings_preflight, "GitHubClient", FakeClient
            ):
                base = repository_settings_preflight.run(args)
            mutations = base["requested_mutations"]
            settings = base["requested_settings"]
            variants: list[tuple[dict[str, object], int]] = [
                ({}, 1),
                ({"repository": ["octo/example"]}, 0),
                ({"decision": ["may-configure-repository-settings"]}, 0),
                ({"requested_mutations": mutations[0]}, 0),
                ({"requested_mutations": [mutations]}, 0),
            ]
            if name == "Test-FreshCommunicationPreflight":
                variants.append(
                    (
                        {
                            "requested_settings": {
                                **settings,
                                "issues": "true",
                                "discussions": "false",
                            }
                        },
                        0,
                    )
                )
            else:
                variants.append(
                    ({"requested_settings": {**settings, "labels": [["bug"]]}}, 0)
                )
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                command = root / "fresh-consumer.ps1"
                suffix = (
                    "\n$writes=0; $failure=$null\ntry {\n"
                    + invocation
                    + "\n} catch { $failure=$_.Exception.Message }\n@{writes=$writes;failure=$failure} | ConvertTo-Json -Compress\n"
                )
                command.write_bytes(
                    (
                        prefix
                        + identity[0]
                        + source[0].replace("OWNER/REPO", "octo/example")
                        + suffix
                    ).encode("utf-8-sig")
                )
                fixture = root / "verdict.json"
                for override, writes in variants:
                    with self.subTest(consumer=name, override=override):
                        fixture.write_text(
                            json.dumps({**base, **override}), encoding="utf-8"
                        )
                        result = subprocess.run(
                            [
                                str(POWERSHELL),
                                "-NoProfile",
                                "-NonInteractive",
                                "-File",
                                str(command),
                            ],
                            env={
                                **os.environ,
                                "PRODUCER": str(SCRIPT_PATH),
                                "FIXTURE": str(fixture),
                            },
                            capture_output=True,
                            timeout=30,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        observed = json.loads(
                            result.stdout.decode("utf-8-sig").strip().splitlines()[-1]
                        )
                        self.assertEqual(observed["writes"], writes, observed)

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

    def test_topic_platform_limits_are_checked_before_remote_access(self) -> None:
        for topics in (
            ["contains.dot"],
            ["x" * 51],
            [f"topic-{number}" for number in range(21)],
        ):
            with (
                self.subTest(topics=topics),
                self.assertRaises(repository_settings_preflight.InspectionError),
            ):
                repository_settings_preflight.requested_mutations(
                    arguments(topics=True, topic=topics)
                )

    def test_topic_addition_cannot_exceed_the_preserved_existing_set(self) -> None:
        FakeClient.response = repository(
            topics=[f"existing-{number}" for number in range(20)]
        )
        with (
            mock.patch.object(
                repository_settings_preflight, "GitHubClient", FakeClient
            ),
            self.assertRaisesRegex(
                repository_settings_preflight.InspectionError, "combined topic"
            ),
        ):
            repository_settings_preflight.run(
                arguments(topics=True, topic=["new-topic"])
            )

    def test_topic_readback_requires_a_valid_bounded_existing_array(self) -> None:
        for current in (
            None,
            "topic",
            [False],
            ["bad.topic"],
            ["one", "ONE"],
            [f"topic-{number}" for number in range(21)],
        ):
            with self.subTest(current=current):
                FakeClient.response = repository(topics=current)
                with (
                    mock.patch.object(
                        repository_settings_preflight, "GitHubClient", FakeClient
                    ),
                    self.assertRaises(repository_settings_preflight.InspectionError),
                ):
                    repository_settings_preflight.run(
                        arguments(topics=True, topic=["new-topic"])
                    )

    def test_topic_plan_preserves_existing_names_at_the_exact_limit(self) -> None:
        existing = [f"existing-{number}" for number in range(19)]
        FakeClient.response = repository(topics=existing)
        with mock.patch.object(
            repository_settings_preflight, "GitHubClient", FakeClient
        ):
            result = repository_settings_preflight.run(
                arguments(topics=True, topic=["new-topic", "EXISTING-0"])
            )
        self.assertEqual(result["current_topics"], existing)
        self.assertEqual(
            result["requested_settings"]["topics"], ["new-topic", "EXISTING-0"]
        )

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
