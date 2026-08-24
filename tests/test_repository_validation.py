from __future__ import annotations

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
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "validate_repository.py"
SPEC = importlib.util.spec_from_file_location(
    "scripts.validate_repository", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load validate_repository.py")
validate_repository = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validate_repository
SPEC.loader.exec_module(validate_repository)

WORKFLOW_SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "validate_workflows.py"
WORKFLOW_SPEC = importlib.util.spec_from_file_location(
    "scripts.validate_workflows", WORKFLOW_SCRIPT_PATH
)
if WORKFLOW_SPEC is None or WORKFLOW_SPEC.loader is None:
    raise RuntimeError("Could not load validate_workflows.py")
validate_workflows = importlib.util.module_from_spec(WORKFLOW_SPEC)
sys.modules[WORKFLOW_SPEC.name] = validate_workflows
WORKFLOW_SPEC.loader.exec_module(validate_workflows)


class SerializedFileValidationTests(unittest.TestCase):
    def test_yaml_loader_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.yml"
            path.write_text("name: first\nname: second\n", encoding="utf-8")

            with self.assertRaises(yaml.constructor.ConstructorError):
                validate_repository.load_yaml(path)

    def test_json_loader_rejects_duplicate_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"name": "first", "name": "second"}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate JSON member"):
                validate_repository.load_json(path)

    def test_helpers_identify_project_files_and_basic_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text('{"valid": true}', encoding="utf-8")
            cached = root / ".pytest_cache" / "cached.json"
            cached.parent.mkdir()
            cached.write_text('{"cached": true}', encoding="utf-8")
            generated = root / "mutants" / "generated.json"
            generated.parent.mkdir()
            generated.write_text('{"generated": true}', encoding="utf-8")

            self.assertEqual(
                validate_repository.project_files(root, ("*.json", "source.*")),
                [source],
            )
            self.assertTrue(validate_repository.is_project_path(source, root))
            self.assertFalse(validate_repository.is_project_path(cached, root))
            self.assertTrue(validate_repository.nonempty_string(" value "))

    def test_project_files_do_not_traverse_reparse_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text('{"valid": true}', encoding="utf-8")
            linked = root / "linked"
            linked.mkdir()
            (linked / "outside.json").write_text('{"outside": true}', encoding="utf-8")

            with mock.patch.object(
                validate_repository,
                "is_link_or_reparse",
                side_effect=lambda path: path == linked,
            ):
                files = validate_repository.project_files(root, ("*.json",))

            self.assertEqual(files, [source])

    def test_project_file_link_helpers_fail_closed(self) -> None:
        missing = Path("missing-project-entry")
        self.assertFalse(validate_repository.is_link_or_reparse(missing))
        with mock.patch.object(Path, "is_symlink", return_value=True):
            self.assertTrue(validate_repository.is_link_or_reparse(missing))
        with mock.patch.object(Path, "is_symlink", side_effect=OSError("denied")):
            self.assertTrue(validate_repository.is_link_or_reparse(missing))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            linked = root / "linked.json"
            linked.write_text("{}", encoding="utf-8")
            with mock.patch.object(
                validate_repository,
                "is_link_or_reparse",
                side_effect=lambda path: path == linked,
            ):
                self.assertEqual(
                    validate_repository.project_files(root, ("*.json",)), []
                )
            self.assertFalse(validate_repository.nonempty_string(" "))
            self.assertFalse(validate_repository.nonempty_string(7))
            with mock.patch.dict(
                os.environ,
                {"MUTANT_UNDER_TEST": "stats", "PRESERVED_VALUE": "yes"},
                clear=True,
            ):
                child_environment = validate_repository.child_process_environment()
                self.assertEqual(child_environment, {"PRESERVED_VALUE": "yes"})
                self.assertEqual(os.environ["MUTANT_UNDER_TEST"], "stats")
            self.assertEqual(
                validate_repository.reject_duplicate_json_pairs(
                    [("first", 1), ("second", 2)]
                ),
                {"first": 1, "second": 2},
            )

    def test_serialized_file_validator_reports_invalid_json_and_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "valid.json").write_text('{"valid": true}', encoding="utf-8")
            (root / "invalid.json").write_text("{", encoding="utf-8")
            (root / "valid.yml").write_text("name: valid\n", encoding="utf-8")
            (root / "invalid.yml").write_text(
                "name: first\nname: second\n", encoding="utf-8"
            )
            (root / "unhashable.yml").write_text(
                "? [first, second]\n: value\n", encoding="utf-8"
            )

            problems = validate_repository.validate_serialized_files(root)

            self.assertEqual(len(problems), 3)
            self.assertTrue(
                any("invalid.json: invalid JSON" in item for item in problems)
            )
            self.assertTrue(
                any("invalid.yml: invalid YAML" in item for item in problems)
            )
            self.assertTrue(
                any(
                    "unhashable.yml: invalid YAML" in item
                    and "found an unhashable mapping key" in item
                    for item in problems
                )
            )

    def test_executable_resolution_accepts_only_external_absolute_candidates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forbidden = root / "repository"
            external = root / "external"
            missing = root / "missing"
            forbidden.mkdir()
            external.mkdir()
            missing.mkdir()
            inside_tool = forbidden / "git"
            outside_tool = external / "git"
            inside_tool.touch()
            outside_tool.touch()
            path_value = os.pathsep.join(
                ["", "relative", str(missing), str(forbidden), str(external)]
            )

            def resolve_tool(_name: str, *, path: str) -> str | None:
                if path == str(forbidden):
                    return str(inside_tool)
                if path == str(external):
                    return str(outside_tool)
                return None

            with (
                mock.patch.dict(os.environ, {"PATH": path_value}),
                mock.patch.object(
                    validate_repository.shutil, "which", side_effect=resolve_tool
                ),
            ):
                result = validate_repository.resolve_path_executable(
                    "git", forbidden_root=forbidden
                )

            self.assertEqual(result, str(outside_tool.resolve()))

            with (
                mock.patch.dict(os.environ, {"PATH": "relative"}),
                mock.patch.object(validate_repository.shutil, "which") as which,
            ):
                self.assertIsNone(
                    validate_repository.resolve_path_executable(
                        "git", forbidden_root=forbidden
                    )
                )
            which.assert_not_called()

    def test_executable_resolution_ignores_unresolvable_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forbidden = root / "repository"
            broken_directory = root / "broken"
            external = root / "external"
            forbidden.mkdir()
            broken_directory.mkdir()
            external.mkdir()
            broken_tool = broken_directory / "git"
            outside_tool = external / "git"
            outside_tool.touch()
            original_resolve = Path.resolve

            def resolve_path(path: Path, strict: bool = False) -> Path:
                if path == broken_tool:
                    raise OSError("unresolvable candidate")
                return original_resolve(path, strict=strict)

            def resolve_tool(_name: str, *, path: str) -> str:
                if path == str(broken_directory):
                    return str(broken_tool)
                return str(outside_tool)

            with (
                mock.patch.dict(
                    os.environ,
                    {"PATH": os.pathsep.join([str(broken_directory), str(external)])},
                ),
                mock.patch.object(
                    validate_repository.shutil, "which", side_effect=resolve_tool
                ),
                mock.patch.object(validate_repository.Path, "resolve", resolve_path),
            ):
                result = validate_repository.resolve_path_executable(
                    "git", forbidden_root=forbidden
                )

            self.assertEqual(result, str(outside_tool.resolve()))


class PythonSupportContractValidationTests(unittest.TestCase):
    CONTRACT_FILES = (
        ".github/python-support.json",
        ".github/workflows/ci.yml",
        "CONTRIBUTING.md",
        "pyproject.toml",
        "README.md",
        "requirements-dev.in",
        "ruff.toml",
        "scripts/python_support.py",
        "skills/repo-scaffold/assets/workflows/ci.yml",
        "skills/repo-scaffold/references/workflow-contracts.md",
    )

    def copy_contract(self, root: Path) -> None:
        for relative in self.CONTRACT_FILES:
            source = PLUGIN_ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def test_repository_python_support_contract_is_synchronized(self) -> None:
        self.assertEqual(
            validate_repository.validate_python_support_contract(PLUGIN_ROOT),
            [],
        )

    def test_hardcoded_workflow_version_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "${{ needs.prepare_ci.outputs.latest }}",
                '"3.14"',
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_python_support_contract(root)

            self.assertIn(
                ".github/workflows/ci.yml: supported Python feature releases "
                "must not be hardcoded",
                problems,
            )
            self.assertIn(
                ".github/workflows/ci.yml: quality must use the policy's latest "
                "release",
                problems,
            )

    def test_test_job_runner_must_come_from_the_reviewed_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "runs-on: ${{ matrix.os }}", "runs-on: self-hosted", 1
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_python_support_contract(root)

        self.assertIn(
            ".github/workflows/ci.yml: test matrix must come from prepare_ci "
            "and runs-on must use matrix.os",
            problems,
        )

    def test_ruff_target_must_match_the_policy_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "ruff.toml").write_text(
                'target-version = "py311"\n', encoding="utf-8"
            )

            problems = validate_repository.validate_python_support_contract(root)

            self.assertIn(
                "ruff.toml: target-version must match the minimum Python "
                "policy release (py310)",
                problems,
            )

    def test_missing_policy_components_are_reported_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                validate_repository.validate_python_support_contract(root),
                ["Python support contract: scripts/python_support.py is missing"],
            )
            script = root / "scripts" / "python_support.py"
            script.parent.mkdir()
            script.write_text("pass\n", encoding="utf-8")
            self.assertEqual(
                validate_repository.validate_python_support_contract(root),
                ["Python support contract: .github/python-support.json is missing"],
            )

    def test_policy_subprocess_timeout_and_failure_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            with mock.patch.object(
                validate_repository.subprocess,
                "run",
                side_effect=validate_repository.subprocess.TimeoutExpired(
                    ["python"], 10
                ),
            ):
                self.assertEqual(
                    validate_repository.validate_python_support_contract(root),
                    ["Python support contract: policy validation timed out"],
                )

            failed = mock.Mock(returncode=1, stderr="line one\nline two\n", stdout="")
            with mock.patch.object(
                validate_repository.subprocess, "run", return_value=failed
            ):
                self.assertEqual(
                    validate_repository.validate_python_support_contract(root),
                    [
                        "Python support contract: line one",
                        "Python support contract: line two",
                    ],
                )

    def test_document_workflow_asset_skill_and_ruff_contract_failures_are_reported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "README.md").write_bytes(b"\xff")
            (root / "CONTRIBUTING.md").write_text(
                "Python 3.10 through 3.14\n", encoding="utf-8"
            )
            (root / "requirements-dev.in").write_text(
                ".github/python-support.json\nPython 3.10 or newer\n",
                encoding="utf-8",
            )
            (root / ".github" / "workflows" / "ci.yml").write_text(
                "jobs: []\n", encoding="utf-8"
            )
            (
                root / "skills" / "repo-scaffold" / "assets" / "workflows" / "ci.yml"
            ).write_text("jobs: {}\n", encoding="utf-8")
            (
                root
                / "skills"
                / "repo-scaffold"
                / "references"
                / "workflow-contracts.md"
            ).write_text("incomplete\n", encoding="utf-8")
            (root / ".github" / "python-support.json").write_text(
                '{"versions": []}', encoding="utf-8"
            )

            with mock.patch.object(
                validate_repository.subprocess,
                "run",
                return_value=mock.Mock(returncode=0, stderr="", stdout=""),
            ):
                problems = validate_repository.validate_python_support_contract(root)

            self.assertTrue(
                any("could not verify Python policy link" in item for item in problems)
            )
            self.assertTrue(
                any("must reference the centralized" in item for item in problems)
            )
            self.assertTrue(any("must not duplicate" in item for item in problems))
            self.assertIn(".github/workflows/ci.yml: jobs must be a mapping", problems)
            self.assertTrue(any("scaffold CI must load" in item for item in problems))
            self.assertEqual(
                sum("missing Python support requirement" in item for item in problems),
                3,
            )
            self.assertIn(
                ".github/python-support.json: cannot derive the Ruff target", problems
            )

    def test_unreadable_workflow_asset_skill_and_ruff_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            for relative in (
                ".github/workflows/ci.yml",
                "skills/repo-scaffold/assets/workflows/ci.yml",
                "skills/repo-scaffold/references/workflow-contracts.md",
                "ruff.toml",
            ):
                (root / relative).write_bytes(b"\xff")

            problems = validate_repository.validate_python_support_contract(root)

            self.assertTrue(
                any("could not verify contract" in item for item in problems)
            )
            self.assertTrue(
                any(
                    "assets" in item and "workflows" in item and "ci.yml" in item
                    for item in problems
                )
            )
            self.assertTrue(any("workflow-contracts.md" in item for item in problems))
            self.assertTrue(
                any("could not verify Python target" in item for item in problems)
            )

    def test_empty_workflow_jobs_report_every_required_python_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / ".github" / "workflows" / "ci.yml").write_text(
                "on:\n  schedule: []\njobs: {}\n", encoding="utf-8"
            )

            problems = validate_repository.validate_python_support_contract(root)

            expected_fragments = (
                "prepare_ci must expose policy matrix",
                "prepare_ci must load the centralized policy",
                "test matrix must come from prepare_ci",
                "quality must use the policy's latest release",
                "mutation cache integration must run",
                "scheduled 3.x canary",
                "ci-success must require tests",
            )
            for expected in expected_fragments:
                self.assertTrue(
                    any(expected in item for item in problems),
                    f"{expected}: {problems}",
                )

    def test_ci_success_must_check_the_mutation_integration_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "          MUTATION_CACHE_INTEGRATION_RESULT: "
                "${{ needs.mutation-cache-integration.result }}\n",
                "",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_python_support_contract(root)

        self.assertIn(
            ".github/workflows/ci.yml: ci-success must require tests, quality, "
            "and mutation integration while keeping canaries outside the gate",
            problems,
        )


class ActionReferenceValidationTests(unittest.TestCase):
    def test_repository_action_references_are_immutable(self) -> None:
        self.assertEqual(
            validate_repository.validate_action_references(PLUGIN_ROOT),
            [],
        )

    def test_mutable_action_reference_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            (workflow_root / "ci.yml").write_text(
                "permissions: {}\n"
                "jobs:\n"
                "  test:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - uses: actions/checkout@v7\n"
                "      - uses: ./local-action\n",
                encoding="utf-8",
            )

            self.assertEqual(
                validate_repository.validate_action_references(root),
                [
                    f"{Path('.github') / 'workflows' / 'ci.yml'}: external action or workflow "
                    "must use a full commit SHA: actions/checkout@v7"
                ],
            )

    def test_missing_and_broad_workflow_permissions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            (workflow_root / "missing.yml").write_text("jobs: {}\n", encoding="utf-8")
            (workflow_root / "scalar.yml").write_text("[]\n", encoding="utf-8")
            for preset in ("read-all", "write-all"):
                (workflow_root / f"{preset}.yml").write_text(
                    f"permissions: {preset}\njobs: {{}}\n", encoding="utf-8"
                )

            problems = validate_repository.validate_action_references(root)

        self.assertEqual(
            problems,
            [
                f"{Path('.github') / 'workflows' / 'missing.yml'}: workflow must "
                "declare top-level permissions",
                f"{Path('.github') / 'workflows' / 'read-all.yml'}: workflow must "
                "use named least-privilege scopes instead of a broad permission preset",
                f"{Path('.github') / 'workflows' / 'write-all.yml'}: workflow must "
                "use named least-privilege scopes instead of a broad permission preset",
            ],
        )

    def test_mismatched_action_repository_pins_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / ".github" / "workflows"
            asset = root / "skills" / "repo-scaffold" / "assets" / "workflows"
            installed.mkdir(parents=True)
            asset.mkdir(parents=True)
            for path, sha in (
                (installed / "ci.yml", "a" * 40),
                (asset / "ci.yml", "b" * 40),
            ):
                path.write_text(
                    "permissions: {}\n"
                    "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                    f"      - uses: actions/checkout@{sha}\n",
                    encoding="utf-8",
                )

            problems = validate_repository.validate_action_references(root)

            self.assertEqual(len(problems), 1)
            self.assertIn("workflow action pin drift: actions/checkout", problems[0])

    def test_invalid_uses_values_and_container_pins_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            (workflow_root / "ci.yml").write_text(
                "jobs:\n"
                "  test:\n"
                "    uses: []\n"
                "    steps:\n"
                "      - uses: docker://alpine:latest\n"
                f"      - uses: docker://alpine@sha256:{'a' * 64}\n",
                encoding="utf-8",
            )
            (workflow_root / "invalid.yml").write_text(
                "name: first\nname: second\n", encoding="utf-8"
            )

            problems = validate_repository.validate_action_references(root)

            self.assertTrue(
                any("uses must be a nonempty string" in item for item in problems)
            )
            self.assertTrue(
                any("container reference must use" in item for item in problems)
            )
            self.assertFalse(any("invalid.yml" in item for item in problems))


class ActionPinSyncContractTests(unittest.TestCase):
    def test_repository_has_a_pr_only_template_action_synchronizer(self) -> None:
        self.assertEqual(
            validate_repository.validate_action_pin_sync_contract(PLUGIN_ROOT),
            [],
        )

    def test_missing_script_and_invalid_workflow_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github" / "workflows" / "action-pin-sync.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("jobs: {}\n", encoding="utf-8")

            problems = validate_repository.validate_action_pin_sync_contract(root)

        self.assertTrue(any("script is unreadable" in problem for problem in problems))
        self.assertTrue(
            any("allowlisted GitHub API" in problem for problem in problems)
        )
        self.assertTrue(
            any("must run weekly and manually" in problem for problem in problems)
        )
        self.assertTrue(
            any("only contents and pull-requests" in problem for problem in problems)
        )
        self.assertTrue(
            any("synchronizer job contract" in problem for problem in problems)
        )

    def test_unreadable_and_non_mapping_synchronizer_workflows_are_reported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            unreadable = validate_repository.validate_action_pin_sync_contract(root)

            workflow = root / ".github" / "workflows" / "action-pin-sync.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("scalar\n", encoding="utf-8")
            non_mapping = validate_repository.validate_action_pin_sync_contract(root)

        self.assertTrue(
            any("workflow is unreadable" in problem for problem in unreadable)
        )
        self.assertTrue(
            any("workflow must be a mapping" in problem for problem in non_mapping)
        )

    def test_tampered_synchronizer_steps_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "scripts" / "sync_action_pins.py"
            workflow = root / ".github" / "workflows" / "action-pin-sync.yml"
            script.parent.mkdir(parents=True)
            workflow.parent.mkdir(parents=True)
            shutil.copy2(PLUGIN_ROOT / "scripts" / "sync_action_pins.py", script)
            original = (
                PLUGIN_ROOT / ".github" / "workflows" / "action-pin-sync.yml"
            ).read_text(encoding="utf-8")

            workflow.write_text(
                original.replace(
                    "python scripts/sync_action_pins.py --write", "echo bypass", 1
                ),
                encoding="utf-8",
            )
            invalid_sync = validate_repository.validate_action_pin_sync_contract(root)

            workflow.write_text(
                original.replace(
                    "branch: chore/synchronize-action-pins", "branch: unsafe", 1
                ),
                encoding="utf-8",
            )
            invalid_pr = validate_repository.validate_action_pin_sync_contract(root)

        self.assertTrue(
            any(
                "only through the reviewed script" in problem
                for problem in invalid_sync
            )
        )
        self.assertTrue(
            any("create a reviewed pull request" in problem for problem in invalid_pr)
        )


class CiToolchainContractValidationTests(unittest.TestCase):
    CONTRACT_FILES = (
        ".github/ci-toolchain.json",
        ".github/workflows/ci.yml",
        "CONTRIBUTING.md",
        "pyproject.toml",
        "README.md",
        "skills/repo-scaffold/assets/ci-toolchain.json",
        "skills/repo-scaffold/assets/workflows/documentation.yml",
        "skills/repo-scaffold/references/github-setup.md",
        "skills/repo-scaffold/references/workflow-contracts.md",
        "skills/repo-scaffold/scripts/ci_toolchain.py",
    )

    def copy_contract(self, root: Path) -> None:
        for relative in self.CONTRACT_FILES:
            source = PLUGIN_ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def test_repository_ci_toolchain_contract_is_synchronized(self) -> None:
        self.assertEqual(
            validate_repository.validate_ci_toolchain_contract(PLUGIN_ROOT),
            [],
        )

    def test_hardcoded_documentation_python_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = (
                root
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "workflows"
                / "documentation.yml"
            )
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "${{ needs.prepare_docs.outputs.documentation_python }}",
                '"3.10"',
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertIn(
                "skills/repo-scaffold/assets/workflows/documentation.yml: "
                "documentation Python must not be hardcoded",
                problems,
            )
            self.assertIn(
                "skills/repo-scaffold/assets/workflows/documentation.yml: "
                "docs-contract must consume the rolling policy runtime",
                problems,
            )

    def test_hardcoded_standalone_tool_version_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "          SHELLCHECK_REPOSITORY:",
                '          SHELLCHECK_VERSION: "0.11.0"\n'
                "          SHELLCHECK_REPOSITORY:",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertIn(
                ".github/workflows/ci.yml: standalone tool versions must not "
                "be hardcoded",
                problems,
            )
            self.assertIn(
                ".github/workflows/ci.yml: Install ShellCheck must consume "
                "policy outputs",
                problems,
            )

    def test_hardcoded_standalone_repository_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "${{ needs.prepare_ci.outputs.shellcheck_repository }}",
                "koalaman/shellcheck",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertIn(
                ".github/workflows/ci.yml: standalone tool metadata must come "
                "from policy outputs, found 'koalaman/shellcheck'",
                problems,
            )

    def test_installing_from_the_extraction_target_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                '"$extract_dir/$ACTIONLINT_EXECUTABLE_PATH"',
                '"$ACTIONLINT_EXECUTABLE_PATH"',
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertIn(
                ".github/workflows/ci.yml: Install actionlint must extract "
                "before install",
                problems,
            )

    def test_missing_script_and_policies_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                validate_repository.validate_ci_toolchain_contract(root),
                [
                    "CI toolchain contract: "
                    "skills/repo-scaffold/scripts/ci_toolchain.py is missing"
                ],
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "skills" / "repo-scaffold" / "scripts" / "ci_toolchain.py"
            script.parent.mkdir(parents=True)
            script.write_text("pass\n", encoding="utf-8")

            problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertEqual(
                sum(
                    "CI toolchain contract:" in item and "is missing" in item
                    for item in problems
                ),
                2,
            )

    def test_policy_validation_timeout_and_failure_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            failed = mock.Mock(returncode=1, stderr="invalid policy\n", stdout="")
            with mock.patch.object(
                validate_repository.subprocess,
                "run",
                side_effect=[
                    validate_repository.subprocess.TimeoutExpired(["python"], 10),
                    failed,
                ],
            ):
                problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertTrue(any("validation timed out" in item for item in problems))
            self.assertTrue(any("invalid policy" in item for item in problems))

    def test_policy_sync_and_workflow_structure_regressions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            installed_path = root / ".github" / "ci-toolchain.json"
            installed = json.loads(installed_path.read_text(encoding="utf-8"))
            installed["standalone-tools"] = {"unexpected": "invalid"}
            installed["npm-tools"] = {}
            installed["tooling-python-minimum"] = "3.11"
            installed_path.write_text(json.dumps(installed), encoding="utf-8")
            asset_path = (
                root / "skills" / "repo-scaffold" / "assets" / "ci-toolchain.json"
            )
            asset = json.loads(asset_path.read_text(encoding="utf-8"))
            asset["standalone-tools"] = {"unexpected": {}}
            asset_path.write_text(json.dumps(asset), encoding="utf-8")
            (root / ".github" / "workflows" / "ci.yml").write_text(
                "jobs: []\n", encoding="utf-8"
            )
            documentation_path = (
                root
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "workflows"
                / "documentation.yml"
            )
            documentation_path.write_text("jobs: []\n", encoding="utf-8")
            (
                root
                / "skills"
                / "repo-scaffold"
                / "references"
                / "workflow-contracts.md"
            ).write_text("incomplete\n", encoding="utf-8")
            setup_path = (
                root / "skills" / "repo-scaffold" / "references" / "github-setup.md"
            )
            setup_path.write_text("Python 3.10 or newer\n", encoding="utf-8")

            with mock.patch.object(
                validate_repository.subprocess,
                "run",
                return_value=mock.Mock(returncode=0, stderr="", stdout=""),
            ):
                problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertTrue(
                any("standalone-tools must define exactly" in item for item in problems)
            )
            self.assertTrue(
                any("generic scaffold must not prescribe" in item for item in problems)
            )
            self.assertTrue(any("tooling Python minimum" in item for item in problems))
            self.assertTrue(
                any("markdownlint-cli2 npm pin" in item for item in problems)
            )
            self.assertIn(".github/workflows/ci.yml: jobs must be a mapping", problems)
            self.assertTrue(any("prepare_ci must expose" in item for item in problems))
            self.assertTrue(any("prepare_ci must load" in item for item in problems))
            self.assertTrue(any("drift canary" in item for item in problems))
            self.assertTrue(any("prepare_docs must load" in item for item in problems))
            self.assertTrue(
                any("docs-contract must consume" in item for item in problems)
            )
            self.assertEqual(
                sum("missing CI toolchain requirement" in item for item in problems),
                4,
            )
            self.assertTrue(
                any("must read the tooling Python policy" in item for item in problems)
            )
            self.assertTrue(
                any("minimum must not be hardcoded" in item for item in problems)
            )

    def test_unreadable_contract_consumers_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            for relative in (
                "README.md",
                ".github/workflows/ci.yml",
                "skills/repo-scaffold/assets/workflows/documentation.yml",
                "skills/repo-scaffold/references/workflow-contracts.md",
                "skills/repo-scaffold/references/github-setup.md",
            ):
                (root / relative).write_bytes(b"\xff")

            problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertTrue(
                any("could not verify CI toolchain link" in item for item in problems)
            )
            self.assertTrue(
                any("could not verify toolchain" in item for item in problems)
            )
            self.assertTrue(any("workflow-contracts.md" in item for item in problems))
            self.assertTrue(any("github-setup.md" in item for item in problems))

    def test_documentation_guidance_must_reference_policy_without_hardcoded_pin(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "CONTRIBUTING.md").write_text(
                "Run markdownlint-cli2@1.0.0 directly.\n", encoding="utf-8"
            )

            problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertTrue(
                any(
                    "must reference the CI toolchain policy" in item
                    for item in problems
                )
            )
            self.assertTrue(
                any("markdownlint must consume" in item for item in problems)
            )
            self.assertTrue(
                any("version must not be hardcoded" in item for item in problems)
            )

    def test_nonstring_tool_metadata_is_not_mirrored_into_workflow_literals(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            policy_path = root / ".github" / "ci-toolchain.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["standalone-tools"] = {
                "actionlint": {
                    "version": "1.0.0",
                    "repository": 7,
                    "tag-template": 7,
                    "asset-template": 7,
                    "executable-path-template": "actionlint",
                },
                "shellcheck": "invalid",
            }
            policy_path.write_text(json.dumps(policy), encoding="utf-8")

            with mock.patch.object(
                validate_repository.subprocess,
                "run",
                return_value=mock.Mock(returncode=0, stderr="", stdout=""),
            ):
                problems = validate_repository.validate_ci_toolchain_contract(root)

            self.assertFalse(any("found '7'" in item for item in problems))


class MirroredDependencyMetadataTests(unittest.TestCase):
    def test_repository_mirrored_metadata_is_synchronized(self) -> None:
        self.assertEqual(
            validate_repository.validate_mirrored_dependency_metadata(PLUGIN_ROOT),
            [],
        )

    def test_pyyaml_pin_and_release_schema_drift_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset_root = root / "skills" / "repo-scaffold" / "assets"
            asset_root.mkdir(parents=True)
            (root / "requirements-dev.in").write_text(
                "markdown-it-py==4.2.0\nPyYAML==6.0.3\n", encoding="utf-8"
            )
            (asset_root / "requirements-docs.txt").write_text(
                "markdown-it-py==4.1.0\nPyYAML==6.0.2\n", encoding="utf-8"
            )
            (root / "release-please-config.json").write_text(
                '{"$schema":"https://example.test/v2/schema.json"}',
                encoding="utf-8",
            )
            (asset_root / "release-please-config.json").write_text(
                '{"$schema":"https://example.test/v1/schema.json"}',
                encoding="utf-8",
            )

            problems = validate_repository.validate_mirrored_dependency_metadata(root)

            self.assertIn(
                "PyYAML pin drift: requirements-dev.in and the scaffold docs "
                "requirements must match",
                problems,
            )
            self.assertIn(
                "markdown-it-py pin drift: requirements-dev.in and the scaffold "
                "docs requirements must match",
                problems,
            )
            self.assertIn(
                "Release Please schema drift: installed and scaffold configs "
                "must match",
                problems,
            )

    def test_unreadable_missing_duplicate_pins_and_schemas_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset_root = root / "skills" / "repo-scaffold" / "assets"
            asset_root.mkdir(parents=True)
            (root / "requirements-dev.in").write_bytes(b"\xff")
            (asset_root / "requirements-docs.txt").write_text(
                "markdown-it-py==1.0\nmarkdown-it-py==2.0\nPyYAML==1.0\nPyYAML==2.0\n",
                encoding="utf-8",
            )
            (root / "release-please-config.json").write_text("[]", encoding="utf-8")
            (asset_root / "release-please-config.json").write_text(
                "{", encoding="utf-8"
            )

            problems = validate_repository.validate_mirrored_dependency_metadata(root)

            self.assertTrue(
                any("could not verify PyYAML pin" in item for item in problems)
            )
            self.assertTrue(any("exactly one PyYAML pin" in item for item in problems))
            self.assertTrue(
                any("could not verify markdown-it-py pin" in item for item in problems)
            )
            self.assertTrue(
                any("exactly one markdown-it-py pin" in item for item in problems)
            )
            self.assertTrue(
                any("$schema must be a nonempty string" in item for item in problems)
            )
            self.assertTrue(
                any("could not verify $schema pin" in item for item in problems)
            )


class DevelopmentDependencyContractTests(unittest.TestCase):
    CONTRACT_FILES = (
        ".coveragerc",
        ".gitattributes",
        ".gitignore",
        ".github/workflows/ci.yml",
        "CONTRIBUTING.md",
        "README.md",
        "requirements-dev.txt",
        "requirements-dev.in",
    )

    def copy_contract(self, root: Path) -> None:
        for relative in self.CONTRACT_FILES:
            source = PLUGIN_ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    @staticmethod
    def direct_version(root: Path, package: str) -> str:
        direct_text = (root / "requirements-dev.in").read_text(encoding="utf-8")
        match = re.search(
            rf"(?mi)^{re.escape(package)}==([^\s;\\]+)$",
            direct_text,
        )
        if match is None:
            raise AssertionError(f"missing test fixture pin for {package}")
        return match.group(1)

    def test_repository_dependency_and_coverage_contract_is_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_development_dependency_contract(PLUGIN_ROOT),
            [],
        )

    def test_direct_dependency_version_drift_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            coverage_version = self.direct_version(root, "coverage")
            lock_path = root / "requirements-dev.txt"
            lock_text = lock_path.read_text(encoding="utf-8").replace(
                f"coverage=={coverage_version} \\", "coverage==0.0.0 \\", 1
            )
            lock_path.write_text(lock_text, encoding="utf-8")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertIn(
                "requirements-dev.txt: coverage pin 0.0.0 does not match "
                f"requirements-dev.in pin {coverage_version}",
                problems,
            )

    def test_automated_dependency_versions_are_not_hardcoded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            current_version = self.direct_version(root, "coverage")
            replacement_version = "999.0.0"
            direct_path = root / "requirements-dev.in"
            lock_path = root / "requirements-dev.txt"
            direct_path.write_text(
                direct_path.read_text(encoding="utf-8").replace(
                    f"coverage=={current_version}",
                    f"coverage=={replacement_version}",
                    1,
                ),
                encoding="utf-8",
            )
            lock_path.write_text(
                lock_path.read_text(encoding="utf-8").replace(
                    f"coverage=={current_version} \\",
                    f"coverage=={replacement_version} \\",
                    1,
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                validate_repository.validate_development_dependency_contract(root),
                [],
            )

    def test_lock_entry_without_hash_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            types_pyyaml_version = self.direct_version(root, "types-PyYAML")
            lock_path = root / "requirements-dev.txt"
            lock_text = lock_path.read_text(encoding="utf-8")
            start = lock_text.index("types-pyyaml==")
            end = lock_text.index("typing-extensions==", start)
            unhashed_block = "\n".join(
                line
                for line in lock_text[start:end].splitlines()
                if "--hash=sha256:" not in line
            )
            lock_path.write_text(
                lock_text[:start] + unhashed_block + "\n" + lock_text[end:],
                encoding="utf-8",
            )

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertIn(
                f"requirements-dev.txt: types-pyyaml=={types_pyyaml_version} must have "
                "a SHA-256 hash",
                problems,
            )

    def test_ci_install_without_hash_enforcement_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                " --require-hashes", "", 1
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertIn(
                ".github/workflows/ci.yml: every development install must use "
                "the hashed requirements-dev.txt",
                problems,
            )

    def test_ci_mypy_must_cover_every_production_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "          scripts/run_mutation_testing.py\n", "", 1
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

        self.assertIn(
            ".github/workflows/ci.yml: Mypy must check every production script and tests",
            problems,
        )

    def test_coverage_floor_regression_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            coverage_path = root / ".coveragerc"
            coverage = coverage_path.read_text(encoding="utf-8").replace(
                "fail_under = 100", "fail_under = 99", 1
            )
            coverage_path.write_text(coverage, encoding="utf-8")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertIn(
                ".coveragerc: require branch coverage for both script trees, only "
                "the verified freshness-script copies omitted, and a fail-under "
                "floor of at least 100",
                problems,
            )

    def test_coverage_configuration_cannot_be_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            ignore_path = root / ".gitignore"
            ignore = ignore_path.read_text(encoding="utf-8").replace(
                ".coverage\n.coverage.*", ".coverage*", 1
            )
            ignore_path.write_text(ignore, encoding="utf-8")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertIn(
                ".gitignore: ignore coverage data files without ignoring .coveragerc",
                problems,
            )

    def test_missing_direct_and_lock_files_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(
                validate_repository.validate_development_dependency_contract(root)[
                    0
                ].startswith("requirements-dev.in: could not verify direct pins")
            )
            (root / "requirements-dev.in").write_text("pytest==1.0\n", encoding="utf-8")
            self.assertTrue(
                validate_repository.validate_development_dependency_contract(root)[
                    0
                ].startswith("requirements-dev.txt: could not verify hashed lock")
            )

    def test_invalid_direct_and_lock_shapes_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "requirements-dev.in").write_text(
                "# comment\ninvalid requirement\nPy_Test==1.0\npy-test==2.0\nmissing==3.0\n",
                encoding="utf-8",
            )
            (root / "requirements-dev.txt").write_text(
                "# generated without required flag\n"
                "--index-url https://example.test/simple\n"
                f"py-test==9.0 \\\n    --hash=sha256:{'a' * 64}\n"
                f"py_test==9.0 \\\n    --hash=sha256:{'b' * 64}\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertTrue(
                any("exact name==version pins" in item for item in problems)
            )
            self.assertTrue(
                any("duplicate direct pin for py-test" in item for item in problems)
            )
            self.assertTrue(
                any("duplicate locked package py-test" in item for item in problems)
            )
            self.assertTrue(
                any(
                    "generator header must record the hashed" in item
                    for item in problems
                )
            )
            self.assertTrue(
                any("index settings are forbidden" in item for item in problems)
            )
            self.assertTrue(
                any("direct package missing is missing" in item for item in problems)
            )
            self.assertTrue(any("pin 9.0 does not match" in item for item in problems))

    def test_cross_platform_compatibility_pins_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            direct_path = root / "requirements-dev.in"
            direct_text = direct_path.read_text(encoding="utf-8")
            for package in ("colorama", "exceptiongroup", "tomli"):
                direct_text = re.sub(
                    rf"(?mi)^{package}==[^\r\n]+\r?\n?",
                    "",
                    direct_text,
                )
            direct_path.write_text(
                direct_text,
                encoding="utf-8",
            )

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            for package in ("colorama", "exceptiongroup", "tomli"):
                self.assertIn(
                    "requirements-dev.in: the cross-platform lock requires a direct "
                    f"{package} pin",
                    problems,
                )

    def test_empty_lock_and_invalid_workflow_coverage_docs_and_exports_are_reported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "requirements-dev.txt").write_text(
                "# --generate-hashes\n", encoding="utf-8"
            )
            (root / ".github" / "workflows" / "ci.yml").write_text(
                "jobs: []\n", encoding="utf-8"
            )
            (root / ".coveragerc").write_text("invalid", encoding="utf-8")
            (root / "README.md").write_bytes(b"\xff")
            (root / "CONTRIBUTING.md").write_text("incomplete\n", encoding="utf-8")
            (root / ".gitattributes").write_text("", encoding="utf-8")
            (root / ".gitignore").write_bytes(b"\xff")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertTrue(
                any("no locked package entries" in item for item in problems)
            )
            self.assertTrue(
                any("could not verify coverage policy" in item for item in problems)
            )
            self.assertTrue(any("quality must enforce" in item for item in problems))
            self.assertTrue(
                any("could not verify dependency guidance" in item for item in problems)
            )
            self.assertEqual(
                sum(
                    "CONTRIBUTING.md: development guidance" in item for item in problems
                ),
                3,
            )
            self.assertEqual(
                sum(
                    ".gitattributes:" in item and "export-ignore" in item
                    for item in problems
                ),
                2,
            )
            self.assertTrue(
                any("could not verify coverage exclusions" in item for item in problems)
            )

    def test_unreadable_workflow_and_attributes_and_nonstep_jobs_are_reported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow_path.write_bytes(b"\xff")
            (root / ".gitattributes").write_bytes(b"\xff")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertTrue(
                any("could not verify lock use" in item for item in problems)
            )
            self.assertTrue(
                any("could not verify export exclusions" in item for item in problems)
            )

            workflow_path.write_text(
                "jobs:\n  invalid: scalar\n  mixed:\n    steps:\n      - scalar\n",
                encoding="utf-8",
            )
            problems = validate_repository.validate_development_dependency_contract(
                root
            )
            self.assertTrue(
                any("every development install" in item for item in problems)
            )


class MutationTestingContractTests(unittest.TestCase):
    CONTRACT_FILES = (
        ".gitattributes",
        ".github/workflows/mutation-testing.yml",
        ".gitignore",
        "CONTRIBUTING.md",
        "README.md",
        "pyproject.toml",
        "requirements-mutation.txt",
        "requirements-mutation.in",
        "scripts/prepare_mutation_cache.py",
        "scripts/run_mutation_testing.py",
        "scripts/validate_mutation_results.py",
        "tests/test_audit_freshness.py",
        "tests/test_ci_toolchain.py",
        "tests/test_codeql_preflight.py",
        "tests/test_validate_mutation_results.py",
        "tests/test_prepare_mutation_cache.py",
        "tests/test_run_mutation_testing.py",
        "tests/test_mutation_runner_linux.py",
        "tests/test_python_support.py",
        "tests/test_repository_validation.py",
        "tests/test_validate_scaffold.py",
    )

    def copy_contract(self, root: Path) -> None:
        for relative in self.CONTRACT_FILES:
            source = PLUGIN_ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def test_repository_mutation_contract_is_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_mutation_testing_contract(PLUGIN_ROOT),
            [],
        )

    def test_missing_contract_files_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = validate_repository.validate_mutation_testing_contract(
                Path(directory)
            )

        self.assertEqual(len(problems), len(self.CONTRACT_FILES))
        self.assertTrue(
            all("could not verify mutation contract" in item for item in problems)
        )

    def test_direct_and_lock_regressions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "requirements-mutation.in").write_text(
                "mutmut>=3\n", encoding="utf-8"
            )
            lock_path = root / "requirements-mutation.txt"
            lock_text = lock_path.read_text(encoding="utf-8")
            mutmut_start = lock_text.index("mutmut==")
            mutmut_end = lock_text.index("mypy==", mutmut_start)
            mutmut_block = "\n".join(
                line
                for line in lock_text[mutmut_start:mutmut_end].splitlines()
                if "--hash=sha256:" not in line
            )
            toml_start = lock_text.index("toml==")
            toml_end = lock_text.index("types-pyyaml==", toml_start)
            lock_path.write_text(
                "--index-url https://example.test/simple\n"
                + lock_text[:mutmut_start]
                + mutmut_block
                + "\n"
                + lock_text[mutmut_end:toml_start]
                + lock_text[toml_end:],
                encoding="utf-8",
            )

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(any("must extend requirements-dev.in" in p for p in problems))
        self.assertTrue(any("portable hash mode" in p for p in problems))
        self.assertTrue(any("missing hashed mutmut entry" in p for p in problems))
        self.assertTrue(any("missing hashed toml entry" in p for p in problems))

    def test_mutation_dependency_versions_are_not_hardcoded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            direct_path = root / "requirements-mutation.in"
            lock_path = root / "requirements-mutation.txt"
            direct_text = direct_path.read_text(encoding="utf-8")
            match = re.search(r"(?m)^mutmut==([^\s;\\]+)$", direct_text)
            self.assertIsNotNone(match)
            assert match is not None
            current_version = match.group(1)
            replacement_version = "999.0.0"
            direct_path.write_text(
                direct_text.replace(
                    f"mutmut=={current_version}",
                    f"mutmut=={replacement_version}",
                    1,
                ),
                encoding="utf-8",
            )
            lock_path.write_text(
                lock_path.read_text(encoding="utf-8").replace(
                    f"mutmut=={current_version} \\",
                    f"mutmut=={replacement_version} \\",
                    1,
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                validate_repository.validate_mutation_testing_contract(root),
                [],
            )

    def test_duplicate_mutation_pin_and_lock_mismatch_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            direct_path = root / "requirements-mutation.in"
            direct_text = direct_path.read_text(encoding="utf-8")
            match = re.search(r"(?m)^mutmut==([^\s;\\]+)$", direct_text)
            self.assertIsNotNone(match)
            assert match is not None
            current_version = match.group(1)
            direct_path.write_text(
                direct_text + "mutmut==999.0.0\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_mutation_testing_contract(root)

            self.assertTrue(
                any("use exact pins for only mutmut" in problem for problem in problems)
            )
            self.assertTrue(
                any(
                    f"mutmut pin {current_version} does not match "
                    "requirements-mutation.in pin 999.0.0" in problem
                    for problem in problems
                )
            )

    def test_invalid_and_unsafe_workflows_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow_path.write_text("jobs: [\n", encoding="utf-8")

            invalid = validate_repository.validate_mutation_testing_contract(root)

            self.assertTrue(any("invalid workflow" in item for item in invalid))
            self.assertTrue(any("trusted triggers" in item for item in invalid))
            self.assertTrue(any("permissions must" in item for item in invalid))
            self.assertTrue(any("bounded Ubuntu" in item for item in invalid))

            workflow_path.write_text(
                """
on:
  pull_request:
permissions:
  contents: write
jobs:
  mutation-quality:
    runs-on: windows-latest
    timeout-minutes: 0
    steps:
      - scalar
      - uses: actions/setup-python@0000000000000000000000000000000000000000
        with:
          cache-dependency-path: wrong.lock
      - run: echo unsafe
""",
                encoding="utf-8",
            )

            unsafe = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(any("trusted triggers" in item for item in unsafe))
        self.assertTrue(any("permissions must" in item for item in unsafe))
        self.assertTrue(any("bounded Ubuntu" in item for item in unsafe))
        self.assertTrue(any("install the hashed lock" in item for item in unsafe))
        self.assertTrue(any("Python cache must key" in item for item in unsafe))

    def test_mutation_diagnostics_must_export_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "      - name: Export mutation results\n        if: ${{ always() }}\n",
                "      - name: Export mutation results\n",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: mutation diagnostics must "
            "export after failed runs",
            problems,
        )

    def test_mutation_diagnostics_must_preserve_generated_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "            mutants/**/*.meta\n",
                "",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: retain summaries, generated "
            "mutants, and per-file metadata for diagnosis",
            problems,
        )

    def test_mutation_score_policy_cannot_be_lowered_or_misclassify_timeout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            validator_path = root / "scripts" / "validate_mutation_results.py"
            validator = validator_path.read_text(encoding="utf-8")
            validator = validator.replace(
                "MINIMUM_MUTATION_SCORE_BASIS_POINTS = 10_000",
                "MINIMUM_MUTATION_SCORE_BASIS_POINTS = 1",
                1,
            )
            unsafe_start = validator.index("UNSAFE_RESULT_FIELDS")
            validator = validator[:unsafe_start] + validator[unsafe_start:].replace(
                '    "segfault",',
                '    "segfault",\n    "timeout",',
                1,
            )
            validator_path.write_text(validator, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            "scripts/validate_mutation_results.py: mutation score floor must remain "
            "100.00%",
            problems,
        )
        self.assertIn(
            "scripts/validate_mutation_results.py: incomplete result classes must "
            "fail and timeout must remain a detected result",
            problems,
        )

    def test_invalid_mutation_validator_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "scripts" / "validate_mutation_results.py").write_text(
                "def invalid(:\n", encoding="utf-8"
            )

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(
            any(
                problem.startswith(
                    "scripts/validate_mutation_results.py: invalid Python source:"
                )
                for problem in problems
            )
        )

    def test_mutation_run_must_expose_the_tracked_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "        env:\n"
                "          REPO_SCAFFOLD_MUTATION_SOURCE_ROOT: "
                "${{ github.workspace }}\n",
                "",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: mutation run must expose "
            "the tracked source root, heartbeat, bounded resumable step, and skip "
            "only for a verified clean cache hit",
            problems,
        )

    def test_mutation_run_must_be_bounded_and_resumable(self) -> None:
        replacements = (
            ("        continue-on-error: true\n", "        continue-on-error: false\n"),
            ("        timeout-minutes: 150\n", "        timeout-minutes: 0\n"),
            ("            while sleep 60; do\n", "            while sleep 600; do\n"),
            (
                '          trap \'kill "$heartbeat_pid" 2>/dev/null || true; '
                'wait "$heartbeat_pid" 2>/dev/null || true\' EXIT\n',
                "          trap 'kill \"$heartbeat_pid\" 2>/dev/null || true' EXIT\n",
            ),
        )
        for original, replacement in replacements:
            with self.subTest(replacement=replacement.strip()):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.copy_contract(root)
                    workflow_path = (
                        root / ".github" / "workflows" / "mutation-testing.yml"
                    )
                    workflow = workflow_path.read_text(encoding="utf-8").replace(
                        original,
                        replacement,
                        1,
                    )
                    workflow_path.write_text(workflow, encoding="utf-8")

                    problems = validate_repository.validate_mutation_testing_contract(
                        root
                    )

                self.assertIn(
                    ".github/workflows/mutation-testing.yml: mutation run must "
                    "expose the tracked source root, heartbeat, bounded resumable "
                    "step, and skip only for a verified clean cache hit",
                    problems,
                )

    def test_mutation_concurrency_must_preserve_active_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "  cancel-in-progress: false\n", "  cancel-in-progress: true\n", 1
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: concurrent mutation runs "
            "must preserve active resumable progress",
            problems,
        )

    def test_mutation_state_cache_is_scoped_and_controls_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8")
            workflow = workflow.replace(
                "          path: mutants/\n"
                "          key: >-\n"
                "            mutmut-v5-${{ runner.os }}-${{ runner.arch }}-python-"
                "${{ steps.python.outputs.python-version }}-incremental-${{ github.sha }}-"
                "${{ github.run_id }}-${{ github.run_attempt }}\n"
                "          restore-keys: |\n"
                "            mutmut-v5-${{ runner.os }}-${{ runner.arch }}-python-"
                "${{ steps.python.outputs.python-version }}-incremental-${{ github.sha }}-\n"
                "            mutmut-v5-${{ runner.os }}-${{ runner.arch }}-python-"
                "${{ steps.python.outputs.python-version }}-incremental-\n",
                "          path: mutants/*.meta\n"
                "          key: mutmut-shared\n"
                "          restore-keys: mutmut-\n",
                1,
            ).replace(
                "        id: python\n",
                "",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: mutation state cache must "
            "restore and save resumable state under immutable per-run keys, "
            "save verified clean results separately, and use runtime- and "
            "platform-scoped v5 keys",
            problems,
        )
        self.assertIn(
            ".github/workflows/mutation-testing.yml: setup-python must expose the "
            "resolved runtime version for the mutation cache key",
            problems,
        )

    def test_incremental_cache_records_and_saves_interrupted_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8")
            workflow = workflow.replace(
                "        if: ${{ always() && steps.mutation-clean-cache.outputs."
                "cache-hit != 'true' }}\n",
                "        if: ${{ steps.mutation-run.outcome == 'success' }}\n",
                1,
            ).replace(
                "        if: ${{ always() && steps.mutation-clean-cache.outputs."
                "cache-hit != 'true' && steps.mutation-record.outcome == "
                "'success' }}\n",
                "        if: ${{ success() && steps.mutation-run.outcome == "
                "'success' }}\n",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: incremental mutation state "
            "must be prepared after every restore and recorded after completed or "
            "interrupted mutmut execution",
            problems,
        )
        self.assertIn(
            ".github/workflows/mutation-testing.yml: mutation state cache must "
            "restore and save resumable state under immutable per-run keys, save "
            "verified clean results separately, and use runtime- and platform-scoped "
            "v5 keys",
            problems,
        )

    def test_incremental_cache_must_save_progress_before_score_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8")
            incremental_start = workflow.index(
                "      - name: Save incremental mutation state\n"
            )
            export_start = workflow.index("      - name: Export mutation results\n")
            clean_start = workflow.index(
                "      - name: Save verified clean mutation state\n"
            )
            incremental_block = workflow[incremental_start:export_start]
            workflow = (
                workflow[:incremental_start]
                + workflow[export_start:clean_start]
                + incremental_block
                + workflow[clean_start:]
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: save progressive mutation "
            "state before applying the score gate and save clean state only after "
            "the gate passes",
            problems,
        )

    def test_incremental_cache_cannot_reuse_survivors_or_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            preparer_path = root / "scripts" / "prepare_mutation_cache.py"
            preparer = preparer_path.read_text(encoding="utf-8").replace(
                "KILLED_EXIT_CODES = {1, 3}",
                "KILLED_EXIT_CODES = {0, 1, 3, 36}",
                1,
            )
            preparer_path.write_text(preparer, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            "scripts/prepare_mutation_cache.py: preserve only mutmut killed exit "
            "codes and retain conservative prepare, record, and unchanged-test checks",
            problems,
        )

    def test_invalid_incremental_cache_preparer_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "scripts" / "prepare_mutation_cache.py").write_text(
                "def invalid(:\n", encoding="utf-8"
            )

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(
            any(
                problem.startswith(
                    "scripts/prepare_mutation_cache.py: invalid Python source:"
                )
                for problem in problems
            )
        )

    def test_invalid_incremental_runner_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "scripts" / "run_mutation_testing.py").write_text(
                "def invalid(:\n", encoding="utf-8"
            )

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(
            any(
                problem.startswith(
                    "scripts/run_mutation_testing.py: invalid Python source:"
                )
                for problem in problems
            )
        )

    def test_incremental_runner_must_keep_the_generation_hook(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            runner_path = root / "scripts" / "run_mutation_testing.py"
            runner = runner_path.read_text(encoding="utf-8").replace(
                "def _create_or_reuse_mutants(", "def removed_generation_hook(", 1
            )
            runner_path.write_text(runner, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            "scripts/run_mutation_testing.py: must retain the reviewed mutmut "
            "generation hook",
            problems,
        )

    def test_manual_mutation_run_must_retain_the_clean_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "  workflow_dispatch:\n"
                "    inputs:\n"
                "      clean:\n"
                "        description: Ignore incremental mutation state and run "
                "every mutant\n"
                "        required: false\n"
                "        type: boolean\n"
                "        default: false\n",
                "  workflow_dispatch:\n",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: manual runs must expose the "
            "clean full-run verification input",
            problems,
        )

    def test_resumable_mutation_state_is_bound_to_the_source_run(self) -> None:
        cases = (
            (
                '"$source_sha" != "$EXPECTED_SHA"',
                '"$source_sha" == "$EXPECTED_SHA"',
                ".github/workflows/mutation-testing.yml: resumable state must be "
                "bound to one completed mutation run from this repository and commit",
            ),
            (
                "          run-id: ${{ inputs.resume_run_id }}\n",
                "          run-id: ${{ github.run_id }}\n",
                ".github/workflows/mutation-testing.yml: resumable state must "
                "download the verified mutation-results artifact without executing it",
            ),
            (
                "      - name: Record resumed mutation state\n",
                "      - name: Record unverified mutation state\n",
                ".github/workflows/mutation-testing.yml: downloaded mutation state "
                "must be recorded before conservative cache preparation",
            ),
        )
        for original, replacement, expected in cases:
            with self.subTest(replacement=replacement.strip()):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.copy_contract(root)
                    workflow_path = (
                        root / ".github" / "workflows" / "mutation-testing.yml"
                    )
                    workflow = workflow_path.read_text(encoding="utf-8")
                    self.assertIn(original, workflow)
                    workflow_path.write_text(
                        workflow.replace(original, replacement, 1),
                        encoding="utf-8",
                    )

                    problems = validate_repository.validate_mutation_testing_contract(
                        root
                    )

                self.assertIn(expected, problems)

    def test_resumed_state_must_be_recorded_before_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8")
            record_start = workflow.index(
                "      - name: Record resumed mutation state\n"
            )
            prepare_start = workflow.index(
                "      - name: Prepare incremental mutation state\n"
            )
            run_start = workflow.index("      - name: Run mutation testing\n")
            record_block = workflow[record_start:prepare_start]
            workflow_path.write_text(
                workflow[:record_start]
                + workflow[prepare_start:run_start]
                + record_block
                + workflow[run_start:],
                encoding="utf-8",
            )

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: downloaded mutation state "
            "must be recorded before conservative cache preparation",
            problems,
        )

    def test_mutation_results_all_option_requires_a_boolean_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "mutmut results --all true > mutants/mutation-results.txt",
                "mutmut results --all > mutants/mutation-results.txt",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(any("validate exported results" in item for item in problems))

    def test_line_coverage_prepass_must_remain_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            config_path = root / "pyproject.toml"
            config = config_path.read_text(encoding="utf-8").replace(
                "mutate_only_covered_lines = false",
                "mutate_only_covered_lines = true",
                1,
            )
            config_path.write_text(config, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            "pyproject.toml: mutation testing must include uncovered lines",
            problems,
        )

    def test_mutation_scope_covers_every_production_python_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            unscoped = root / "tools" / "unscoped.py"
            unscoped.parent.mkdir()
            unscoped.write_text("VALUE = 1\n", encoding="utf-8")
            config_path = root / "pyproject.toml"
            config = config_path.read_text(encoding="utf-8").replace(
                'source_paths = ["scripts", "skills/repo-scaffold/scripts"]',
                'source_paths = ["scripts"]\ndo_not_mutate = ["scripts/*.py"]',
                1,
            )
            config = (
                config.replace(
                    'pytest_add_cli_args_test_selection = ["tests"]',
                    'pytest_add_cli_args_test_selection = ["other-tests"]',
                    1,
                )
                .replace(
                    '  "requirements-mutation.txt",',
                    "",
                    1,
                )
                .replace(
                    'testpaths = ["tests"]',
                    'testpaths = ["other-tests"]',
                    1,
                )
            )
            config_path.write_text(config, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            "pyproject.toml: mutation source_paths must include both complete "
            "production script trees",
            problems,
        )
        self.assertIn(
            "pyproject.toml: mutation setting 'do_not_mutate' must not exclude "
            "production code",
            problems,
        )
        self.assertTrue(
            any(
                "mutation source_paths omit production Python files" in p
                for p in problems
            )
        )
        self.assertTrue(
            any("must collect first-party tests from tests/" in p for p in problems)
        )
        self.assertTrue(
            any(
                "workspace must copy both mutation requirement files" in p
                for p in problems
            )
        )
        self.assertTrue(
            any("pytest must collect only first-party tests" in p for p in problems)
        )

    def test_mutation_loaders_must_use_canonical_module_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            test_path = root / "tests" / "test_python_support.py"
            content = test_path.read_text(encoding="utf-8").replace(
                '"scripts.python_support"',
                '"python_support"',
                1,
            )
            test_path.write_text(content, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(
            any(
                problem.startswith(
                    "tests/test_python_support.py: mutation loaders must use "
                    "canonical module names"
                )
                for problem in problems
            )
        )

    def test_invalid_mutation_loader_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "tests" / "test_python_support.py").write_text(
                "def broken(:\n", encoding="utf-8"
            )

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(
            any(
                problem.startswith(
                    "tests/test_python_support.py: could not verify mutation "
                    "loader names"
                )
                for problem in problems
            )
        )

    def test_config_docs_exports_and_ignore_regressions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "pyproject.toml").write_text("[tool.mutmut]\n", encoding="utf-8")
            (root / "README.md").write_text("mutmut\n", encoding="utf-8")
            (root / "CONTRIBUTING.md").write_text(
                "requirements-mutation.txt\n", encoding="utf-8"
            )
            (root / ".gitattributes").write_text("", encoding="utf-8")
            (root / ".gitignore").write_text("# empty\n", encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(
            any("could not verify mutation settings" in p for p in problems)
        )
        self.assertEqual(sum("mutation guidance" in p for p in problems), 5)
        self.assertEqual(sum("must be export-ignore" in p for p in problems), 3)
        self.assertIn(".gitignore: mutants/ must be ignored", problems)

    def test_nonmapping_mutation_configuration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "pyproject.toml").write_text(
                "[[tool.mutmut]]\n[[tool.pytest.ini_options]]\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertTrue(
            any(
                "could not verify mutation settings: mutation and pytest settings "
                "must be TOML tables" in problem
                for problem in problems
            )
        )


class MarkdownLinkValidationTests(unittest.TestCase):
    def test_missing_relative_link_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "See [missing](docs/missing.md).\n", encoding="utf-8"
            )

            self.assertEqual(
                validate_repository.validate_markdown_links(root),
                ["README.md: relative link is missing: docs/missing.md"],
            )

    def test_template_and_external_links_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "[template]({{PROJECT_LINK}})\n"
                "[external](https://example.com)\n"
                "[anchor](#section)\n",
                encoding="utf-8",
            )

            self.assertEqual(validate_repository.validate_markdown_links(root), [])

    def test_balanced_escaped_angle_and_reference_destinations_are_supported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            docs.mkdir()
            (docs / "a(b).md").write_text("Target\n", encoding="utf-8")
            (docs / "a b.md").write_text("Target\n", encoding="utf-8")
            (docs / "a&b.md").write_text("Target\n", encoding="utf-8")
            (root / "README.md").write_text(
                "[balanced](docs/a(b).md)\n"
                r"[escaped](docs/a\(b\).md)"
                "\n"
                "[angle](<docs/a b.md>)\n"
                "[entity](docs/a&amp;b.md)\n"
                "[reference]: <docs/a b.md>\n"
                "[multiline-reference]:\n  <docs/a b.md>\n",
                encoding="utf-8",
            )

            self.assertEqual(validate_repository.validate_markdown_links(root), [])

    def test_nested_labels_footnotes_and_multiline_links_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "[outer [inner]](docs/nested-missing.md)\n"
                "[multiline](\n  docs/multiline-missing.md\n  'title'\n)\n"
                "[^1]: This is footnote text, not a link destination.\n",
                encoding="utf-8",
            )

            self.assertEqual(
                validate_repository.validate_markdown_links(root),
                [
                    "README.md: relative link is missing: docs/nested-missing.md",
                    "README.md: relative link is missing: docs/multiline-missing.md",
                ],
            )

        self.assertEqual(
            validate_repository.markdown_link_destinations(r"[x](<docs/a\>b.md>)"),
            ["docs/a%3Eb.md"],
        )
        self.assertEqual(
            validate_repository.inline_markdown_link_payloads(
                "[outer [inner](docs/inner.md)](docs/outer.md)"
            ),
            ["docs/inner.md"],
        )

    def test_markdown_helpers_remove_code_and_parse_titled_destinations(self) -> None:
        text = (
            "visible `inline`\n"
            "```python\n[hidden](missing.md)\n```\n"
            "~~~sh\nhidden\n~~~\n"
        )
        visible = validate_repository.without_fenced_code(text)
        self.assertIn("visible ", visible)
        self.assertNotIn("missing.md", visible)
        self.assertEqual(
            validate_repository.inline_markdown_link_payloads(
                '[angle](<docs/file.md> "title")'
            ),
            ["docs/file.md"],
        )

        hidden = (
            "    [indented](missing.md)\n\n"
            "# Heading\n\t[indented-after-heading](missing.md)\n\n"
            "<pre>\n[raw-html](missing.md)\n</pre>\n"
            "<?processing\n[opaque-html](missing.md)\n?>\n"
            "````\n[fenced](missing.md)\n```\n[still-fenced](missing.md)\n"
        )
        self.assertNotIn("missing.md", validate_repository.without_fenced_code(hidden))
        self.assertEqual(
            validate_repository._without_inline_code("unclosed `code"),
            "unclosed `code",
        )
        escaped_code = r"\`[visible](missing.md)\`"
        self.assertEqual(
            validate_repository._without_inline_code(escaped_code), escaped_code
        )
        self.assertEqual(
            validate_repository._without_inline_code(r"\\`code`"),
            "\\\\      ",
        )
        self.assertEqual(validate_repository._without_inline_code("``a`b``"), "       ")
        self.assertEqual(
            validate_repository._without_inline_code(
                "before `hidden\ncontinued` after\n"
            ),
            "before        \n           after\n",
        )
        self.assertEqual(
            validate_repository._without_inline_code('<span title="`"> `code`'),
            '<span title="`">       ',
        )
        self.assertEqual(
            validate_repository._without_root_indented_code(
                "- item\n    continuation\nplain\n"
            ),
            "- item\n    continuation\nplain\n",
        )
        visible_html = "<div>\n[visible](docs/example.md)\n"
        self.assertEqual(
            validate_repository._without_markdown_block_code(visible_html),
            visible_html,
        )

    def test_markdown_link_validator_reports_invalid_paths_without_crashing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "[invalid](docs/%00.md)\n", encoding="utf-8"
            )

            self.assertEqual(
                validate_repository.validate_markdown_links(root),
                ["README.md: relative link has an invalid path: docs/%00.md"],
            )

            original_resolve = Path.resolve

            def resolve(path: Path, strict: bool = False) -> Path:
                if path.name == "resolve-error.md":
                    raise OSError("invalid path")
                return original_resolve(path, strict=strict)

            (root / "README.md").write_text(
                "[invalid](docs/resolve-error.md)\n", encoding="utf-8"
            )
            with mock.patch.object(Path, "resolve", autospec=True, side_effect=resolve):
                self.assertEqual(
                    validate_repository.validate_markdown_links(root),
                    [
                        "README.md: relative link has an invalid path: "
                        "docs/resolve-error.md"
                    ],
                )

            original_exists = Path.exists

            def exists(path: Path) -> bool:
                if path.name == "error.md":
                    raise OSError("invalid path")
                return original_exists(path)

            (root / "README.md").write_text(
                "[invalid](docs/error.md)\n", encoding="utf-8"
            )
            with mock.patch.object(Path, "exists", autospec=True, side_effect=exists):
                self.assertEqual(
                    validate_repository.validate_markdown_links(root),
                    ["README.md: relative link has an invalid path: docs/error.md"],
                )

            (root / "README.md").write_text(
                "> paragraph\r\t[missing](docs/missing.md)\n", encoding="utf-8"
            )
            self.assertEqual(
                validate_repository.validate_markdown_links(root),
                ["README.md: relative link is missing: docs/missing.md"],
            )
        self.assertEqual(
            validate_repository.inline_markdown_link_payloads(
                "[bad-angle](<broken>\n[bad-line](broken\n[bad-end](broken"
            ),
            [],
        )

    def test_commonmark_parser_handles_nested_containers_and_references(self) -> None:
        module = validate_repository
        cases = {
            "0.\r\t0.\t\t[]()": [],
            "-\r\t<?\n[]()?>": [""],
            "- <x>\r\t[]()": [],
            "- j\n    ```[]()": [],
            "><?\n[]()": [""],
            "[x][ref]\n\n[ref]: docs/reference.md": ["docs/reference.md"],
            "[unused]: docs/unused.md": ["docs/unused.md"],
        }
        for source, expected in cases.items():
            self.assertEqual(
                module.markdown_link_destinations(source), expected, source
            )

    def test_markdown_links_report_unreadable_and_escaping_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "invalid.md").write_bytes(b"\xff")
            (root / "README.md").write_text(
                "[escape]: ../outside.md\n"
                "[root](/absolute) [protocol](//example.com)\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_markdown_links(root)

            self.assertTrue(any("could not read Markdown" in item for item in problems))
            self.assertIn("README.md: link escapes repository: ../outside.md", problems)


class ScaffoldAndArchiveValidationTests(unittest.TestCase):
    def test_release_archive_uses_only_the_matching_mutation_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory).resolve()
            (source_root / ".git").mkdir()
            generated_root = source_root / "mutants"
            generated_root.mkdir()
            unrelated_root = source_root / "other"
            unrelated_root.mkdir()
            untracked_source_root = source_root / "untracked"
            untracked_source_root.mkdir()
            untracked_generated_root = untracked_source_root / "mutants"
            untracked_generated_root.mkdir()

            with mock.patch.dict(
                os.environ,
                {"REPO_SCAFFOLD_MUTATION_SOURCE_ROOT": str(source_root)},
            ):
                self.assertEqual(
                    validate_repository.release_archive_source_root(generated_root),
                    source_root,
                )
                self.assertEqual(
                    validate_repository.release_archive_source_root(unrelated_root),
                    unrelated_root,
                )

            with mock.patch.dict(
                os.environ,
                {"REPO_SCAFFOLD_MUTATION_SOURCE_ROOT": str(untracked_source_root)},
            ):
                self.assertEqual(
                    validate_repository.release_archive_source_root(
                        untracked_generated_root
                    ),
                    untracked_generated_root,
                )

            with mock.patch.dict(
                os.environ,
                {"REPO_SCAFFOLD_MUTATION_SOURCE_ROOT": str(source_root / "missing")},
            ):
                self.assertEqual(
                    validate_repository.release_archive_source_root(generated_root),
                    generated_root,
                )

            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    validate_repository.release_archive_source_root(generated_root),
                    generated_root,
                )

    def test_scaffold_contract_reports_missing_timeout_failure_and_success(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                validate_repository.validate_scaffold_contract(root),
                [
                    "scaffold contract: "
                    "skills/repo-scaffold/scripts/validate_scaffold.py is missing"
                ],
            )
            script = (
                root / "skills" / "repo-scaffold" / "scripts" / "validate_scaffold.py"
            )
            script.parent.mkdir(parents=True)
            script.write_text("pass\n", encoding="utf-8")

            with mock.patch.object(
                validate_repository.subprocess,
                "run",
                side_effect=validate_repository.subprocess.TimeoutExpired(
                    ["python"], 60
                ),
            ):
                self.assertEqual(
                    validate_repository.validate_scaffold_contract(root),
                    ["scaffold contract: validation timed out"],
                )

            failed = mock.Mock(returncode=1, stderr="first\nsecond\n", stdout="")
            with mock.patch.object(
                validate_repository.subprocess, "run", return_value=failed
            ):
                self.assertEqual(
                    validate_repository.validate_scaffold_contract(root),
                    ["scaffold contract: first", "scaffold contract: second"],
                )

            with mock.patch.object(
                validate_repository.subprocess,
                "run",
                return_value=mock.Mock(returncode=0, stderr="", stdout=""),
            ):
                self.assertEqual(
                    validate_repository.validate_scaffold_contract(root), []
                )

    def test_release_archive_reports_tool_timeout_process_and_zip_failures(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                validate_repository, "resolve_path_executable", return_value=None
            ):
                self.assertEqual(
                    validate_repository.validate_release_archive(root),
                    ["release archive: git is unavailable outside the repository"],
                )

            with (
                mock.patch.object(
                    validate_repository, "resolve_path_executable", return_value="git"
                ),
                mock.patch.object(
                    validate_repository.subprocess,
                    "run",
                    side_effect=validate_repository.subprocess.TimeoutExpired(
                        ["git"], 60
                    ),
                ),
            ):
                self.assertEqual(
                    validate_repository.validate_release_archive(root),
                    ["release archive: git archive timed out"],
                )

            with (
                mock.patch.object(
                    validate_repository, "resolve_path_executable", return_value="git"
                ),
                mock.patch.object(
                    validate_repository.subprocess,
                    "run",
                    return_value=mock.Mock(returncode=1, stderr="archive failed\n"),
                ),
            ):
                self.assertEqual(
                    validate_repository.validate_release_archive(root),
                    ["release archive: archive failed"],
                )

            def invalid_zip(command: list[str], **_kwargs: object) -> mock.Mock:
                archive = Path(command[command.index("--output") + 1])
                archive.write_bytes(b"not a zip")
                return mock.Mock(returncode=0, stderr="")

            with (
                mock.patch.object(
                    validate_repository, "resolve_path_executable", return_value="git"
                ),
                mock.patch.object(
                    validate_repository.subprocess, "run", side_effect=invalid_zip
                ),
            ):
                self.assertTrue(
                    validate_repository.validate_release_archive(root)[0].startswith(
                        "release archive: invalid ZIP"
                    )
                )

            source_outcome: mock.Mock | BaseException = (
                validate_repository.subprocess.TimeoutExpired(["git", "ls-tree"], 60)
            )

            def archive_then_source(command: list[str], **_kwargs: object) -> mock.Mock:
                if command[1] == "archive":
                    archive = Path(command[command.index("--output") + 1])
                    with validate_repository.zipfile.ZipFile(archive, "w") as bundle:
                        bundle.writestr("repo-scaffold/.codex-plugin/plugin.json", "{}")
                    return mock.Mock(returncode=0, stderr="", stdout="")
                if isinstance(source_outcome, BaseException):
                    raise source_outcome
                return source_outcome

            with (
                mock.patch.object(
                    validate_repository, "resolve_path_executable", return_value="git"
                ),
                mock.patch.object(
                    validate_repository.subprocess,
                    "run",
                    side_effect=archive_then_source,
                ),
            ):
                self.assertEqual(
                    validate_repository.validate_release_archive(root),
                    ["release archive: source enumeration timed out"],
                )

            for stderr, expected in (
                (
                    "enumeration failed\n",
                    "release archive: source enumeration failed: enumeration failed",
                ),
                ("", "release archive: source enumeration failed: git ls-tree failed"),
            ):
                source_outcome = mock.Mock(returncode=1, stderr=stderr, stdout="")
                with (
                    mock.patch.object(
                        validate_repository,
                        "resolve_path_executable",
                        return_value="git",
                    ),
                    mock.patch.object(
                        validate_repository.subprocess,
                        "run",
                        side_effect=archive_then_source,
                    ),
                ):
                    self.assertEqual(
                        validate_repository.validate_release_archive(root), [expected]
                    )

    def test_release_archive_inspects_required_unsafe_and_symbolic_members(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write_archive(command: list[str], **_kwargs: object) -> mock.Mock:
                if command[1] == "ls-tree":
                    return mock.Mock(
                        returncode=0,
                        stderr="",
                        stdout="skills/repo-scaffold/assets/extra.txt\0",
                    )
                archive = Path(command[command.index("--output") + 1])
                with validate_repository.zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr("repo-scaffold/.codex-plugin/plugin.json", "{}")
                    bundle.writestr("repo-scaffold/README.md", "README")
                    bundle.writestr("../escape", "unsafe")
                    link = validate_repository.zipfile.ZipInfo("repo-scaffold/link")
                    link.create_system = 3
                    link.external_attr = (
                        validate_repository.stat.S_IFLNK | 0o777
                    ) << 16
                    bundle.writestr(link, "target")
                return mock.Mock(returncode=0, stderr="")

            with (
                mock.patch.object(
                    validate_repository, "resolve_path_executable", return_value="git"
                ),
                mock.patch.object(
                    validate_repository.subprocess, "run", side_effect=write_archive
                ),
            ):
                problems = validate_repository.validate_release_archive(root)

            self.assertTrue(
                any("missing repo-scaffold/LICENSE" in item for item in problems)
            )
            self.assertIn(
                "release archive: missing repo-scaffold/.claude-plugin/plugin.json",
                problems,
            )
            self.assertIn(
                "release archive: missing "
                "repo-scaffold/skills/repo-scaffold/assets/extra.txt",
                problems,
            )
            for script in (
                "ci_toolchain.py",
                "codeql_preflight.py",
                "validate_scaffold.py",
            ):
                self.assertIn(
                    "release archive: missing "
                    f"repo-scaffold/skills/repo-scaffold/scripts/{script}",
                    problems,
                )
            self.assertTrue(
                any("unsafe member '../escape'" in item for item in problems)
            )
            self.assertTrue(
                any("symbolic link 'repo-scaffold/link'" in item for item in problems)
            )

    def test_repository_aggregator_and_main_report_all_results(self) -> None:
        validator_names = (
            "validate_serialized_files",
            "validate_action_references",
            "validate_python_support_contract",
            "validate_ci_toolchain_contract",
            "validate_mirrored_dependency_metadata",
            "validate_development_dependency_contract",
            "validate_mutation_testing_contract",
            "validate_plugin_manifest",
            "validate_skill_reference_paths",
            "validate_multi_agent_plugin_contract",
            "validate_release_please",
            "validate_release_attestation",
            "validate_privileged_workflow_permissions",
            "validate_action_pin_sync_contract",
            "validate_required_check_concurrency",
            "validate_issue_templates",
            "validate_release_notes_config",
            "validate_dependabot",
            "validate_markdown_links",
            "validate_community_health_tracking_contract",
            "validate_freshness_tracking_contract",
            "validate_code_scanning_gate_contract",
            "validate_test_quality_contract",
            "validate_scaffold_contract",
            "validate_release_archive",
        )
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            for name in validator_names:
                stack.enter_context(
                    mock.patch.object(validate_repository, name, return_value=[name])
                )
            self.assertEqual(
                validate_repository.validate_repository(root), list(validator_names)
            )

        error_output = StringIO()
        with (
            mock.patch.object(
                validate_repository,
                "validate_repository",
                return_value=["first", "second"],
            ),
            redirect_stderr(error_output),
        ):
            self.assertEqual(validate_repository.main(), 1)
        self.assertEqual(
            error_output.getvalue().splitlines(), ["error: first", "error: second"]
        )

        output = StringIO()
        with (
            mock.patch.object(
                validate_repository, "validate_repository", return_value=[]
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(validate_repository.main(), 0)
        self.assertIn("release archive are valid", output.getvalue())

    def test_script_entrypoint_returns_main_status(self) -> None:
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

        self.assertEqual(raised.exception.code, 0)


class TestQualityContractTests(unittest.TestCase):
    @staticmethod
    def write_test(root: Path, name: str, content: str) -> Path:
        test_root = root / "tests"
        test_root.mkdir(exist_ok=True)
        path = test_root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_accepts_semantic_unittest_mock_and_plain_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_test(
                root,
                "test_valid.py",
                """
class Tests:
    def test_value(self):
        self.assertEqual(1, 1)

    def test_mock(self):
        dependency.assert_called_once()

async def test_async_value():
    assert True
""",
            )

            problems = validate_repository.validate_test_quality_contract(root)

        self.assertEqual(problems, [])

    def test_rejects_missing_tests_functions_and_inventory_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                validate_repository.validate_test_quality_contract(root),
                ["test quality: no test_*.py files found"],
            )
            self.write_test(root, "test_empty.py", "def helper():\n    return True\n")
            self.assertEqual(
                validate_repository.validate_test_quality_contract(root),
                ["test quality: no test functions found"],
            )

            with mock.patch.object(Path, "glob", side_effect=OSError("denied")):
                self.assertEqual(
                    validate_repository.validate_test_quality_contract(root),
                    ["test quality: could not inventory tests: denied"],
                )

    def test_rejects_tests_without_semantic_assertions_and_duplicate_bodies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_test(
                root,
                "test_weak.py",
                """
class Tests:
    def test_no_assertion(self):
        operation()

    def test_type_only(self):
        self.assertIsInstance(operation(), str)

    def test_nonnull_only(self):
        self.assertIsNotNone(operation())

    def test_first_duplicate(self):
        self.assertEqual(1, 1)

    def test_second_duplicate(self):
        self.assertEqual(1, 1)
""",
            )

            problems = validate_repository.validate_test_quality_contract(root)

        self.assertTrue(any("test has no assertion" in item for item in problems))
        self.assertEqual(
            sum("only checks type or non-null presence" in item for item in problems),
            2,
        )
        self.assertTrue(any("duplicates test body" in item for item in problems))

    def test_reports_unreadable_and_invalid_python_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unreadable = self.write_test(root, "test_encoding.py", "valid = True\n")
            unreadable.write_bytes(b"\xff")
            self.write_test(root, "test_syntax.py", "def test_broken(:\n")

            problems = validate_repository.validate_test_quality_contract(root)

        self.assertEqual(
            sum("could not inspect test quality" in item for item in problems), 2
        )


class PluginManifestValidationTests(unittest.TestCase):
    @staticmethod
    def valid_manifest() -> dict[str, object]:
        repository = "https://github.com/MinhThang1009/repo-scaffold-plugin"
        return {
            "name": "repo-scaffold",
            "version": "1.2.3",
            "description": "Description",
            "author": {"name": "Maintainer", "url": repository},
            "homepage": f"{repository}#readme",
            "repository": repository,
            "license": "MIT",
            "skills": "./skills",
            "interface": {
                "displayName": "Repo Scaffold",
                "shortDescription": "Create repository standards.",
                "longDescription": "Create documented repository standards.",
                "developerName": "Maintainer",
                "category": "Productivity",
                "websiteURL": repository,
                "privacyPolicyURL": f"{repository}/blob/main/PRIVACY.md",
                "termsOfServiceURL": f"{repository}/blob/main/TERMS.md",
                "capabilities": ["Write"],
                "defaultPrompt": ["Scaffold this repository."],
            },
        }

    def test_manifest_skills_path_cannot_escape_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_root = root / ".codex-plugin"
            manifest_root.mkdir()
            (manifest_root / "plugin.json").write_text(
                """
{
  "name": "example",
  "version": "1.0.0",
  "description": "Example",
  "license": "MIT",
  "skills": "../../outside"
}
""".strip(),
                encoding="utf-8",
            )

            self.assertIn(
                ".codex-plugin/plugin.json: skills must stay inside the repository",
                validate_repository.validate_plugin_manifest(root),
            )

    def write_manifest(self, root: Path, document: object) -> None:
        manifest_root = root / ".codex-plugin"
        manifest_root.mkdir(exist_ok=True)
        (manifest_root / "plugin.json").write_text(
            json.dumps(document), encoding="utf-8"
        )

    def test_manifest_rejects_invalid_json_and_nonobject_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_root = root / ".codex-plugin"
            manifest_root.mkdir()
            (manifest_root / "plugin.json").write_text("{", encoding="utf-8")
            self.assertTrue(
                validate_repository.validate_plugin_manifest(root)[0].startswith(
                    ".codex-plugin/plugin.json: invalid JSON"
                )
            )
            (manifest_root / "plugin.json").write_text("[]", encoding="utf-8")
            self.assertEqual(
                validate_repository.validate_plugin_manifest(root),
                [".codex-plugin/plugin.json: root must be an object"],
            )

    def test_semver_rejects_empty_and_leading_zero_identifiers(self) -> None:
        for version in ("1.2.3-..", "1.2.3-01", "1.2.3+.."):
            with self.subTest(version=version):
                self.assertIsNone(validate_repository.SEMVER.fullmatch(version))

    def test_manifest_rejects_metadata_and_skill_directory_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = {
                "name": "wrong",
                "version": "latest",
                "description": " ",
                "license": "Apache-2.0",
                "skills": "missing",
            }
            self.write_manifest(root, document)

            problems = validate_repository.validate_plugin_manifest(root)

            self.assertTrue(
                any("description must be nonempty" in item for item in problems)
            )
            self.assertTrue(
                any("name must be repo-scaffold" in item for item in problems)
            )
            self.assertTrue(
                any("license must match repository MIT" in item for item in problems)
            )
            self.assertTrue(
                any("version must be valid SemVer" in item for item in problems)
            )
            self.assertTrue(
                any("skills must reference a directory" in item for item in problems)
            )

            skills = root / "skills"
            skills.mkdir()
            document["skills"] = "skills"
            document["description"] = "Description"
            self.write_manifest(root, document)
            self.assertTrue(
                any(
                    "skills contains no SKILL.md" in item
                    for item in validate_repository.validate_plugin_manifest(root)
                )
            )

    def test_manifest_ignores_nonstring_skills_after_reporting_the_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_manifest(
                root,
                {
                    "name": "repo-scaffold",
                    "version": "1.0.0",
                    "description": "Description",
                    "license": "MIT",
                    "skills": [],
                },
            )

            self.assertEqual(
                validate_repository.validate_plugin_manifest(root)[0],
                ".codex-plugin/plugin.json: skills must be nonempty",
            )

    def test_manifest_accepts_published_metadata_and_concise_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = self.valid_manifest()
            self.write_manifest(root, document)
            skill = root / "skills" / "repo-scaffold" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: repo-scaffold\n"
                "description: Scaffold a repository.\n---\n\nInstructions.\n",
                encoding="utf-8",
            )

            self.assertEqual(validate_repository.validate_plugin_manifest(root), [])

    def test_manifest_canonicalizes_a_repository_alias_before_relativizing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory)
            root = container / "repository"
            root.mkdir()
            self.write_manifest(root, self.valid_manifest())
            skill = root / "skills" / "repo-scaffold" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: repo-scaffold\n"
                "description: Scaffold a repository.\n---\n\nInstructions.\n",
                encoding="utf-8",
            )
            alias = container / "repository-alias"
            try:
                alias.symlink_to(root, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlinks are unavailable: {error}")

            self.assertEqual(validate_repository.validate_plugin_manifest(alias), [])

    def test_manifest_rejects_incomplete_publishing_and_skill_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = "https://github.com/MinhThang1009/repo-scaffold-plugin"
            document = self.valid_manifest()
            document.pop("repository")
            document.pop("homepage")
            document.pop("author")
            interface = document["interface"]
            self.assertIsInstance(interface, dict)
            assert isinstance(interface, dict)
            interface.pop("websiteURL")
            interface.pop("privacyPolicyURL")
            interface["termsOfServiceURL"] = f"{repository}/TERMS.md"
            interface["capabilities"] = []
            interface["defaultPrompt"] = ["x" * 129]
            document["skills"] = "skills"
            self.write_manifest(root, document)
            skill = root / "skills" / "repo-scaffold" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: repo-scaffold\ndescription: "
                + "x" * 401
                + "\n---\n\nInstructions.\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_plugin_manifest(root)

        expected = (
            "repository must identify",
            "homepage must link",
            "author must include",
            "interface.websiteURL",
            "interface.privacyPolicyURL",
            "interface.termsOfServiceURL",
            "interface.capabilities",
            "interface.defaultPrompt",
            "skills path must start with ./",
            "skill description must stay concise",
        )
        for fragment in expected:
            self.assertTrue(any(fragment in problem for problem in problems), fragment)

    def test_manifest_reports_invalid_and_incomplete_skill_front_matter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_manifest(root, self.valid_manifest())
            skills = root / "skills"
            invalid = skills / "invalid" / "SKILL.md"
            invalid.parent.mkdir(parents=True)
            invalid.write_text("---\nname: invalid\n", encoding="utf-8")
            incomplete = skills / "incomplete" / "SKILL.md"
            incomplete.parent.mkdir(parents=True)
            incomplete.write_text(
                "---\nname: incomplete\ndescription: \n---\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_plugin_manifest(root)

        self.assertTrue(any("invalid skill metadata" in item for item in problems))
        self.assertTrue(
            any(
                "skill metadata must include nonempty name and description" in item
                for item in problems
            )
        )


class SkillReferenceValidationTests(unittest.TestCase):
    def test_reparse_points_are_rejected_without_dereferencing_them(self) -> None:
        metadata = mock.Mock(st_mode=0, st_file_attributes=0x400)
        with mock.patch.object(Path, "lstat", return_value=metadata):
            self.assertTrue(validate_repository.is_link_or_reparse(Path("linked")))

    def test_linked_skill_entry_point_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "example" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("Read `references/example.md`.\n", encoding="utf-8")

            with mock.patch.object(
                validate_repository,
                "is_link_or_reparse",
                side_effect=lambda path: path.name == "SKILL.md",
            ):
                problems = validate_repository.validate_skill_reference_paths(root)

        self.assertEqual(
            problems,
            [
                "skills/example/SKILL.md: linked or reparse-point skill entry point "
                "is not read"
            ],
        )

    def test_linked_skills_root_and_enumeration_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_root = root / "skills"
            skill_root.mkdir()

            with mock.patch.object(
                validate_repository,
                "is_link_or_reparse",
                side_effect=lambda path: path.name == "skills",
            ):
                self.assertEqual(
                    validate_repository.validate_skill_reference_paths(root),
                    ["skills: linked or reparse-point directory is not traversed"],
                )

            with mock.patch.object(Path, "rglob", side_effect=OSError("denied")):
                self.assertEqual(
                    validate_repository.validate_skill_reference_paths(root),
                    ["skills: cannot enumerate skill entry points: denied"],
                )

    def test_reference_validator_rejects_unreadable_and_linked_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "example" / "SKILL.md"
            references = skill.parent / "references"
            references.mkdir(parents=True)
            skill.write_bytes(b"\xff")
            unreadable = validate_repository.validate_skill_reference_paths(root)

            skill.write_text("Read `references/present.md`.\n", encoding="utf-8")
            present = references / "present.md"
            present.write_text("Present\n", encoding="utf-8")
            with mock.patch.object(
                validate_repository,
                "is_link_or_reparse",
                side_effect=lambda path: path.name == "references",
            ):
                linked_directory = validate_repository.validate_skill_reference_paths(
                    root
                )
            with mock.patch.object(
                validate_repository,
                "is_link_or_reparse",
                side_effect=lambda path: path.name == "present.md",
            ):
                linked_file = validate_repository.validate_skill_reference_paths(root)

        self.assertTrue(any("SKILL.md: unreadable" in item for item in unreadable))
        self.assertTrue(
            any("skill and references directories" in item for item in linked_directory)
        )
        self.assertTrue(
            any(
                "references/present.md: linked or reparse-point path" in item
                for item in linked_file
            )
        )

    def test_entrypoint_routes_readme_work_to_the_readme_reference(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "Before creating or updating a README, read `references/readme.md`",
            skill,
        )

    def test_referenced_files_must_exist_be_utf8_and_stay_in_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "example" / "SKILL.md"
            references = skill.parent / "references"
            references.mkdir(parents=True)
            (references / "present.md").write_text("Present\n", encoding="utf-8")
            (skill.parent / "outside.md").write_text("Outside\n", encoding="utf-8")
            skill.write_text(
                "Read `references/present.md`, `references/missing.md`, and "
                "`references/../outside.md`.\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_skill_reference_paths(root)

            self.assertIn(
                "skills/example/SKILL.md: missing referenced file references/missing.md",
                problems,
            )
            self.assertTrue(
                any("references/../outside.md" in problem for problem in problems)
            )

            (references / "missing.md").write_bytes(b"\xff")
            problems = validate_repository.validate_skill_reference_paths(root)

            directory_reference = references / "directory.md"
            directory_reference.mkdir()
            skill.write_text("Read `references/directory.md`.\n", encoding="utf-8")
            non_file = validate_repository.validate_skill_reference_paths(root)

        self.assertTrue(
            any(
                "unreadable referenced file references/missing.md" in problem
                for problem in problems
            )
        )
        self.assertIn(
            "skills/example/SKILL.md: referenced path is not a file "
            "references/directory.md",
            non_file,
        )


class MultiAgentPluginContractTests(unittest.TestCase):
    @staticmethod
    def write_valid_contract(root: Path) -> None:
        shared = {
            "name": "repo-scaffold",
            "version": "1.2.3",
            "description": "Shared Agent Skills plugin",
            "author": {"name": "Maintainer"},
            "homepage": "https://example.test/readme",
            "repository": "https://example.test",
            "license": "MIT",
            "keywords": ["agent-skills"],
        }
        codex_root = root / ".codex-plugin"
        codex_root.mkdir()
        (codex_root / "plugin.json").write_text(json.dumps(shared), encoding="utf-8")
        claude_root = root / ".claude-plugin"
        claude_root.mkdir()
        claude = {
            "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
            "displayName": "Repo Scaffold",
            **shared,
        }
        (claude_root / "plugin.json").write_text(json.dumps(claude), encoding="utf-8")
        skill = root / "skills" / "repo-scaffold" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        language_mappings = "".join(
            f"`{vietnamese.as_posix()}` → `{target.as_posix()}`\n"
            for _english, vietnamese, target in (
                validate_repository.MULTILINGUAL_SCAFFOLD_ASSET_PAIRS
            )
        )
        skill.write_text(
            "Follow the active host, system, developer, and project instructions\n"
            "Codex can use AGENTS.md\n"
            "Claude Code reads `CLAUDE.md`\n"
            "references/agent-compatibility.md\n"
            f"{language_mappings}",
            encoding="utf-8",
        )
        reference_text = (
            "Agent Skills Codex Claude Code\n"
            "https://developers.openai.com/plugins/build/plugins\n"
            "https://learn.chatgpt.com/docs/agent-configuration/agents-md\n"
            "https://learn.chatgpt.com/docs/agent-configuration/subagents\n"
            "https://code.claude.com/docs/en/plugins\n"
            "https://code.claude.com/docs/en/skills\n"
            "https://code.claude.com/docs/en/memory\n"
            "https://code.claude.com/docs/en/sub-agents\n"
        )
        references = skill.parent / "references"
        references.mkdir()
        for name in ("agent-compatibility.md", "agent-compatibility.vi.md"):
            (references / name).write_text(reference_text, encoding="utf-8")
        (references / "scaffold-generation.md").write_text(
            language_mappings,
            encoding="utf-8",
        )
        asset_root = skill.parent / "assets"
        (asset_root / "CLAUDE.md").parent.mkdir(parents=True)
        (asset_root / "CLAUDE.md").write_text(
            validate_repository.CLAUDE_SHARED_INSTRUCTIONS,
            encoding="utf-8",
        )
        for (
            english,
            vietnamese,
            _target,
        ) in validate_repository.MULTILINGUAL_SCAFFOLD_ASSET_PAIRS:
            english_path = asset_root / english
            vietnamese_path = asset_root / vietnamese
            english_path.parent.mkdir(parents=True, exist_ok=True)
            vietnamese_path.parent.mkdir(parents=True, exist_ok=True)
            english_path.write_text("English source\n", encoding="utf-8")
            vietnamese_path.write_text("Nội dung tiếng Việt\n", encoding="utf-8")

    def test_accepts_synchronized_host_adapters_and_shared_documentation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)

            self.assertEqual(
                validate_repository.validate_multi_agent_plugin_contract(root), []
            )

    def test_reports_missing_scaffold_generation_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)
            generation_reference = (
                root
                / "skills"
                / "repo-scaffold"
                / "references"
                / "scaffold-generation.md"
            )
            generation_reference.unlink()

            problems = validate_repository.validate_multi_agent_plugin_contract(root)

        self.assertTrue(
            any(
                item.startswith(
                    "skills/repo-scaffold/references/scaffold-generation.md: "
                )
                for item in problems
            )
        )
        self.assertTrue(
            any("must map AGENTS.vi.md to AGENTS.md" in item for item in problems)
        )

    def test_plugin_release_workflow_bundles_and_synchronizes_both_adapters(
        self,
    ) -> None:
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        for fragment in (
            "codex_manifest_version",
            "claude_manifest_version",
            "Codex and Claude plugin manifest versions must match.",
            "HEAD -- .claude-plugin .codex-plugin skills README.md LICENSE",
        ):
            self.assertIn(fragment, workflow)

    def test_scaffold_templates_support_language_and_host_adapters(self) -> None:
        asset_root = PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets"

        self.assertEqual(
            (asset_root / "CLAUDE.md").read_text(encoding="utf-8"),
            validate_repository.CLAUDE_SHARED_INSTRUCTIONS,
        )
        generation_text = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "references"
            / "scaffold-generation.md"
        ).read_text(encoding="utf-8")
        for (
            english_name,
            vietnamese_name,
            canonical_target,
        ) in validate_repository.MULTILINGUAL_SCAFFOLD_ASSET_PAIRS:
            english = (asset_root / english_name).read_text(encoding="utf-8")
            vietnamese = (asset_root / vietnamese_name).read_text(encoding="utf-8")
            self.assertTrue(english.strip(), english_name)
            self.assertTrue(vietnamese.strip(), vietnamese_name)
            self.assertNotEqual(english, vietnamese, vietnamese_name)
            self.assertRegex(vietnamese, r"[À-ỹ]", vietnamese_name)
            self.assertIn(
                f"`{vietnamese_name.as_posix()}` → `{canonical_target.as_posix()}`",
                generation_text,
            )

    def test_rejects_drifted_shared_instruction_and_localized_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)
            asset_root = root / "skills" / "repo-scaffold" / "assets"
            (asset_root / "CLAUDE.md").write_text(
                "Extra instructions\n", encoding="utf-8"
            )
            (asset_root / "AGENTS.vi.md").write_text(
                "English source\n", encoding="utf-8"
            )

            problems = validate_repository.validate_multi_agent_plugin_contract(root)

        self.assertIn(
            "skills/repo-scaffold/assets/CLAUDE.md: must contain only @AGENTS.md "
            "so Claude Code and AGENTS.md consumers share one instruction source",
            problems,
        )
        self.assertIn(
            "skills/repo-scaffold/assets/AGENTS.vi.md: must contain Vietnamese prose",
            problems,
        )
        self.assertIn(
            "skills/repo-scaffold/assets/AGENTS.vi.md: must not duplicate the "
            "English source",
            problems,
        )

    def test_rejects_reference_without_current_subagent_documentation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)
            reference = (
                root
                / "skills"
                / "repo-scaffold"
                / "references"
                / "agent-compatibility.vi.md"
            )
            reference.write_text(
                reference.read_text(encoding="utf-8").replace(
                    "https://code.claude.com/docs/en/sub-agents\n", ""
                ),
                encoding="utf-8",
            )

            problems = validate_repository.validate_multi_agent_plugin_contract(root)

        self.assertIn(
            "skills/repo-scaffold/references/agent-compatibility.vi.md: must "
            "document Codex, Claude Code, and Agent Skills",
            problems,
        )

    def test_rejects_empty_multilingual_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)
            asset_root = root / "skills" / "repo-scaffold" / "assets"
            (asset_root / "AGENTS.md").write_text("", encoding="utf-8")
            (asset_root / "CONTRIBUTING.vi.md").write_text("", encoding="utf-8")

            problems = validate_repository.validate_multi_agent_plugin_contract(root)

        self.assertIn(
            "skills/repo-scaffold/assets/AGENTS.md: English source must be nonempty",
            problems,
        )
        self.assertIn(
            "skills/repo-scaffold/assets/CONTRIBUTING.vi.md: Vietnamese source must "
            "be nonempty",
            problems,
        )

    def test_reports_unreadable_multilingual_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)
            asset_root = root / "skills" / "repo-scaffold" / "assets"
            unreadable_english = asset_root / "AGENTS.md"
            unreadable_vietnamese = asset_root / "CONTRIBUTING.vi.md"
            unreadable_english.unlink()
            unreadable_english.mkdir()
            unreadable_vietnamese.unlink()
            unreadable_vietnamese.mkdir()

            problems = validate_repository.validate_multi_agent_plugin_contract(root)

        self.assertTrue(
            any(
                problem.startswith("skills/repo-scaffold/assets/AGENTS.md: unreadable:")
                for problem in problems
            )
        )
        self.assertTrue(
            any(
                problem.startswith(
                    "skills/repo-scaffold/assets/CONTRIBUTING.vi.md: unreadable:"
                )
                for problem in problems
            )
        )

    def test_reports_unreadable_nonobject_and_inconsistent_contract_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing_problems = validate_repository.validate_multi_agent_plugin_contract(
                root
            )
            self.assertTrue(
                any(
                    ".codex-plugin/plugin.json: invalid JSON" in item
                    for item in missing_problems
                )
            )
            self.assertTrue(
                any(
                    "skills/repo-scaffold/SKILL.md: unreadable" in item
                    for item in missing_problems
                )
            )
            self.assertEqual(
                sum("agent-compatibility" in item for item in missing_problems), 2
            )

            self.write_valid_contract(root)
            (root / ".codex-plugin" / "plugin.json").write_text("[]", encoding="utf-8")
            self.assertIn(
                ".codex-plugin/plugin.json: root must be an object",
                validate_repository.validate_multi_agent_plugin_contract(root),
            )

            claude = json.loads(
                (root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
            )
            codex = dict(claude)
            for field in (
                "name",
                "version",
                "description",
                "author",
                "homepage",
                "repository",
                "license",
                "keywords",
            ):
                codex[field] = f"different-{field}"
            (root / ".codex-plugin" / "plugin.json").write_text(
                json.dumps(codex), encoding="utf-8"
            )
            claude["$schema"] = "wrong"
            claude["displayName"] = "Wrong"
            (root / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(claude), encoding="utf-8"
            )
            (root / "skills" / "repo-scaffold" / "SKILL.md").write_text(
                "missing guidance\n", encoding="utf-8"
            )
            (
                root
                / "skills"
                / "repo-scaffold"
                / "references"
                / "agent-compatibility.md"
            ).write_text("incomplete\n", encoding="utf-8")

            problems = validate_repository.validate_multi_agent_plugin_contract(root)

        for field in (
            "name",
            "version",
            "description",
            "author",
            "homepage",
            "repository",
            "license",
            "keywords",
        ):
            self.assertIn(
                ".claude-plugin/plugin.json: "
                f"{field} must match .codex-plugin/plugin.json",
                problems,
            )
        self.assertIn(
            ".claude-plugin/plugin.json: $schema must identify the Claude Code "
            "plugin manifest",
            problems,
        )
        self.assertIn(
            ".claude-plugin/plugin.json: displayName must be Repo Scaffold",
            problems,
        )
        self.assertIn(
            "skills/repo-scaffold/SKILL.md: must retain host-neutral agent "
            "compatibility guidance",
            problems,
        )
        self.assertIn(
            "skills/repo-scaffold/references/agent-compatibility.md: must document "
            "Codex, Claude Code, and Agent Skills",
            problems,
        )


class ReleasePleaseValidationTests(unittest.TestCase):
    def write_valid_configuration(self, root: Path) -> None:
        workflow_root = root / ".github" / "workflows"
        workflow_root.mkdir(parents=True)
        (workflow_root / "release-please.yml").write_text(
            """
on:
  push:
    branches: ["main"]
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - run: echo ${{ secrets.RELEASE_PLEASE_TOKEN }}
""".strip(),
            encoding="utf-8",
        )
        plugin_root = root / ".codex-plugin"
        plugin_root.mkdir()
        (plugin_root / "plugin.json").write_text(
            '{"version": "1.2.3"}', encoding="utf-8"
        )
        claude_plugin_root = root / ".claude-plugin"
        claude_plugin_root.mkdir()
        (claude_plugin_root / "plugin.json").write_text(
            '{"version": "1.2.3"}', encoding="utf-8"
        )
        (root / "release-please-config.json").write_text(
            json.dumps(
                {
                    "release-type": "simple",
                    **validate_repository.RELEASE_PLEASE_ENGLISH_TEXT,
                    "changelog-sections": (
                        validate_repository.RELEASE_PLEASE_ENGLISH_CHANGELOG_SECTIONS
                    ),
                    "draft": True,
                    "force-tag-creation": True,
                    "packages": {
                        ".": {
                            "extra-files": [
                                {
                                    "type": "json",
                                    "path": ".codex-plugin/plugin.json",
                                    "jsonpath": "$.version",
                                },
                                {
                                    "type": "json",
                                    "path": ".claude-plugin/plugin.json",
                                    "jsonpath": "$.version",
                                },
                            ]
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (root / ".release-please-manifest.json").write_text(
            '{".": "1.2.3"}', encoding="utf-8"
        )
        (root / "version.txt").write_text("1.2.3\n", encoding="utf-8")

    def test_accepts_single_release_mode_with_synchronized_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)

            self.assertEqual(validate_repository.validate_release_please(root), [])

    def test_rejects_release_metadata_that_is_not_fully_english(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            config_path = root / "release-please-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["pull-request-header"] = "PR phát hành tự động"
            config["changelog-sections"][0]["section"] = "Tính năng"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            problems = validate_repository.validate_release_please(root)

            self.assertIn(
                "release-please-config.json: pull-request-header must use the "
                "approved English release text",
                problems,
            )
            self.assertIn(
                "release-please-config.json: changelog-sections must preserve the "
                "approved English headings and default visibility",
                problems,
            )

    def test_template_exposes_every_localizable_release_field(self) -> None:
        config = json.loads(
            (
                PLUGIN_ROOT
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "release-please-config.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            config["pull-request-title-pattern"],
            "chore${scope}: release${component} ${version}",
        )
        self.assertEqual(
            config["pull-request-header"],
            ":robot: I have created a release *beep* *boop*",
        )
        self.assertIn("Release Please", config["pull-request-footer"])
        self.assertEqual(
            config["changelog-sections"],
            [
                {"type": "feat", "section": "Features"},
                {"type": "feature", "section": "Features"},
                {"type": "fix", "section": "Bug Fixes"},
                {"type": "perf", "section": "Performance Improvements"},
                {"type": "revert", "section": "Reverts"},
                {"type": "docs", "section": "Documentation", "hidden": True},
                {"type": "style", "section": "Styles", "hidden": True},
                {
                    "type": "chore",
                    "section": "Miscellaneous Chores",
                    "hidden": True,
                },
                {
                    "type": "refactor",
                    "section": "Code Refactoring",
                    "hidden": True,
                },
                {"type": "test", "section": "Tests", "hidden": True},
                {"type": "build", "section": "Build System", "hidden": True},
                {
                    "type": "ci",
                    "section": "Continuous Integration",
                    "hidden": True,
                },
            ],
        )

        generation = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "references"
            / "scaffold-generation.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Before changing `pull-request-title-pattern`", generation)
        self.assertIn("update each existing release PR title", generation)

    def test_skill_resolves_one_language_per_project(self) -> None:
        generation = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "references"
            / "scaffold-generation.md"
        ).read_text(encoding="utf-8")
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")

        self.assertIn("`SCAFFOLD_LANGUAGE`, either `en` or `vi`", generation)
        self.assertIn("the user's explicit language request", generation)
        self.assertIn("active project instructions", generation)
        self.assertIn("then `en` as the", generation)
        self.assertIn("Never leave an", generation)
        self.assertIn("English/Vietnamese hybrid", generation)
        self.assertIn("commit, pull-request,", generation)
        self.assertIn(
            "or release text created as part of an authorized scaffold", generation
        )
        self.assertIn("chore${scope}: release${component} ${version}", setup)
        self.assertIn("chore${scope}: phát hành${component} ${version}", setup)
        self.assertIn("Performance Improvements", setup)
        self.assertIn("Cải thiện hiệu năng", setup)

    def test_accepts_intentional_semver_build_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            (root / ".codex-plugin" / "plugin.json").write_text(
                '{"version": "1.2.3+build.7"}', encoding="utf-8"
            )
            (root / ".claude-plugin" / "plugin.json").write_text(
                '{"version": "1.2.3+build.7"}', encoding="utf-8"
            )
            (root / ".release-please-manifest.json").write_text(
                '{".": "1.2.3+build.7"}', encoding="utf-8"
            )
            (root / "version.txt").write_text("1.2.3+build.7\n", encoding="utf-8")

            self.assertEqual(validate_repository.validate_release_please(root), [])

    def test_rejects_local_codex_cachebuster_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            (root / ".codex-plugin" / "plugin.json").write_text(
                '{"version": "1.2.3+codex.test"}', encoding="utf-8"
            )
            (root / ".claude-plugin" / "plugin.json").write_text(
                '{"version": "1.2.3+codex.test"}', encoding="utf-8"
            )
            (root / ".release-please-manifest.json").write_text(
                '{".": "1.2.3+codex.test"}', encoding="utf-8"
            )
            (root / "version.txt").write_text("1.2.3+codex.test\n", encoding="utf-8")

            problems = validate_repository.validate_release_please(root)

            for source in (
                ".codex-plugin/plugin.json",
                ".claude-plugin/plugin.json",
                ".release-please-manifest.json",
                "version.txt",
            ):
                self.assertIn(
                    f"{source}: public release version must not use a local "
                    "Codex cachebuster",
                    problems,
                )

    def test_rejects_tag_dispatcher_and_version_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            (root / ".github" / "workflows" / "release-tag.yml").write_text(
                "on:\n  push:\n    tags: ['v*']\n", encoding="utf-8"
            )
            (root / "version.txt").write_text("1.2.4\n", encoding="utf-8")

            problems = validate_repository.validate_release_please(root)

            self.assertIn(
                ".github/workflows/release-tag.yml: must not coexist with "
                "Release Please",
                problems,
            )
            self.assertIn(
                ".github/workflows/release-tag.yml: tag push trigger conflicts "
                "with Release Please",
                problems,
            )
            self.assertIn(
                "release version files must contain the same version", problems
            )

    def test_rejects_claude_plugin_version_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            (root / ".claude-plugin" / "plugin.json").write_text(
                '{"version": "1.2.4"}', encoding="utf-8"
            )

            self.assertIn(
                "release version files must contain the same version",
                validate_repository.validate_release_please(root),
            )

    def test_missing_workflow_and_invalid_config_shapes_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release-please-config.json").write_text("{", encoding="utf-8")
            problems = validate_repository.validate_release_please(root)
            self.assertTrue(
                problems[0].startswith("release-please-config.json: invalid JSON")
            )
            self.assertIn(".github/workflows/release-please.yml: missing", problems)

            (root / "release-please-config.json").write_text("[]", encoding="utf-8")
            self.assertEqual(
                validate_repository.validate_release_please(root),
                [
                    "release-please-config.json: root must be an object",
                    ".github/workflows/release-please.yml: missing",
                ],
            )

    def test_release_config_and_version_file_regressions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            config_path = root / "release-please-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["release-type"] = "node"
            config["draft"] = False
            config["force-tag-creation"] = False
            config["packages"] = []
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / ".release-please-manifest.json").write_text(
                '{".": "bad", "extra": "1.0.0"}', encoding="utf-8"
            )
            (root / ".codex-plugin" / "plugin.json").write_text("{", encoding="utf-8")
            (root / "version.txt").write_text("bad\n", encoding="utf-8")
            invalid_workflow = root / ".github" / "workflows" / "invalid.yml"
            invalid_workflow.write_text("name: first\nname: second\n", encoding="utf-8")
            scalar_workflow = root / ".github" / "workflows" / "scalar.yml"
            scalar_workflow.write_text("- invalid\n", encoding="utf-8")
            no_triggers = root / ".github" / "workflows" / "no-triggers.yml"
            no_triggers.write_text("jobs: {}\n", encoding="utf-8")

            problems = validate_repository.validate_release_please(root)

            for expected in (
                "release-type must be simple",
                "draft must be true",
                "force-tag-creation must be true",
                "packages must define root package",
                "must contain only the root package",
                ".codex-plugin/plugin.json: invalid JSON",
                "version.txt: release version must be valid SemVer",
            ):
                self.assertTrue(any(expected in item for item in problems), expected)

    def test_root_package_must_update_plugin_and_version_sources_must_be_readable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            config_path = root / "release-please-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["packages"]["."]["extra-files"] = []
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / ".release-please-manifest.json").write_text("{", encoding="utf-8")
            (root / "version.txt").write_bytes(b"\xff")

            problems = validate_repository.validate_release_please(root)

            self.assertTrue(
                any(
                    "root package must update all plugin versions" in item
                    for item in problems
                )
            )
            self.assertTrue(
                any("manifest.json: invalid JSON" in item for item in problems)
            )
            self.assertTrue(any("version.txt: unreadable" in item for item in problems))

    def test_nonobject_plugin_version_document_is_ignored_then_detected_as_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            (root / ".codex-plugin" / "plugin.json").write_text("[]", encoding="utf-8")

            problems = validate_repository.validate_release_please(root)

            self.assertFalse(
                any("plugin.json: invalid JSON" in item for item in problems)
            )


class PrivilegedWorkflowPermissionTests(unittest.TestCase):
    def test_repository_and_templates_isolate_write_permissions(self) -> None:
        self.assertEqual(
            validate_repository.validate_privileged_workflow_permissions(PLUGIN_ROOT),
            [],
        )

    def test_rejects_workflow_level_writes_and_missing_job_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            asset_root = root / "skills" / "repo-scaffold" / "assets" / "workflows"
            workflow_root.mkdir(parents=True)
            asset_root.mkdir(parents=True)
            release = {
                "permissions": {"contents": "write"},
                "jobs": {"release_please": {}},
            }
            codeql = {
                "permissions": {
                    "actions": "read",
                    "contents": "read",
                    "packages": "read",
                    "security-events": "write",
                },
                "jobs": {"analyze": {}},
            }
            for path, document in (
                (workflow_root / "release-please.yml", release),
                (asset_root / "release-please.yml", release),
                (workflow_root / "codeql.yml", codeql),
                (asset_root / "codeql.yml", codeql),
            ):
                path.write_text(
                    yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
                )

            problems = validate_repository.validate_privileged_workflow_permissions(
                root
            )

            self.assertEqual(
                sum(
                    "top-level permissions must be read-only" in item
                    for item in problems
                ),
                4,
            )
            self.assertEqual(
                sum(
                    "analyze must isolate security-events: write" in item
                    for item in problems
                ),
                2,
            )

    def test_reports_unreadable_scalar_and_missing_job_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            asset_root = root / "skills" / "repo-scaffold" / "assets" / "workflows"
            workflow_root.mkdir(parents=True)
            asset_root.mkdir(parents=True)
            (workflow_root / "release-please.yml").write_text(
                "permissions: [\n", encoding="utf-8"
            )
            (asset_root / "release-please.yml").write_text("scalar\n", encoding="utf-8")
            codeql = {
                "permissions": {
                    "actions": "read",
                    "contents": "read",
                    "packages": "read",
                },
                "jobs": {},
            }
            for path in (
                workflow_root / "codeql.yml",
                asset_root / "codeql.yml",
            ):
                path.write_text(
                    yaml.safe_dump(codeql, sort_keys=False), encoding="utf-8"
                )

            problems = validate_repository.validate_privileged_workflow_permissions(
                root
            )

            self.assertTrue(
                any("permission contract is unreadable" in item for item in problems)
            )
            self.assertTrue(any("root must be a mapping" in item for item in problems))
            self.assertEqual(
                sum("analyze job is missing" in item for item in problems), 2
            )


class RequiredCheckConcurrencyTests(unittest.TestCase):
    def test_required_check_workflows_serialize_without_cancellation(self) -> None:
        self.assertEqual(
            validate_repository.validate_required_check_concurrency(PLUGIN_ROOT),
            [],
        )

    def test_invalid_and_cancelling_concurrency_contracts_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            asset_root = root / "skills" / "repo-scaffold" / "assets" / "workflows"
            workflow_root.mkdir(parents=True)
            asset_root.mkdir(parents=True)
            (workflow_root / "ci.yml").write_text("concurrency: [\n", encoding="utf-8")
            (workflow_root / "dependency-review.yml").write_text(
                "scalar\n", encoding="utf-8"
            )
            (workflow_root / "commitlint.yml").write_text(
                "concurrency:\n"
                "  group: required-${{ github.ref }}\n"
                "  cancel-in-progress: true\n",
                encoding="utf-8",
            )
            (asset_root / "ci.yml").write_text(
                "concurrency:\n"
                "  group: required-${{ github.ref }}\n"
                "  cancel-in-progress: true\n",
                encoding="utf-8",
            )
            (asset_root / "dependency-review.yml").write_text(
                "concurrency:\n  cancel-in-progress: false\n",
                encoding="utf-8",
            )
            (asset_root / "commitlint.yml").write_text(
                "concurrency:\n"
                "  group: required-${{ github.ref }}\n"
                "  cancel-in-progress: false\n",
                encoding="utf-8",
            )
            (asset_root / "documentation.yml").write_text(
                "concurrency:\n"
                "  group: required-${{ github.ref }}\n"
                "  cancel-in-progress: true\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_required_check_concurrency(root)

            self.assertTrue(any("contract is unreadable" in item for item in problems))
            self.assertTrue(any("root must be a mapping" in item for item in problems))
            self.assertEqual(
                sum("must serialize" in item for item in problems),
                4,
            )


class CodeScanningGateContractTests(unittest.TestCase):
    def test_validator_reports_missing_malformed_and_unsafe_gate_contracts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = validate_repository.validate_code_scanning_gate_contract(root)
            self.assertTrue(
                any("allowlist.json: unreadable" in item for item in missing)
            )
            self.assertTrue(
                any("code-scanning-gate.yml: unreadable" in item for item in missing)
            )
            self.assertTrue(
                any("bundled gate script is missing" in item for item in missing)
            )

            allowlist = root / ".github" / "code-scanning-allowlist.json"
            allowlist.parent.mkdir(parents=True)
            allowlist.write_text("{}", encoding="utf-8")
            workflow_paths = (
                root / ".github" / "workflows" / "code-scanning-gate.yml",
                root
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "workflows"
                / "code-scanning-gate.yml",
            )
            for workflow in workflow_paths:
                workflow.parent.mkdir(parents=True, exist_ok=True)
                workflow.write_text("{}", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "check_code_scanning_alerts.py").write_text(
                "", encoding="utf-8"
            )
            malformed = validate_repository.validate_code_scanning_gate_contract(root)
            self.assertTrue(any("require schema-version" in item for item in malformed))
            self.assertTrue(
                any("trusted pull-request gate contract" in item for item in malformed)
            )

            source = (
                PLUGIN_ROOT / ".github" / "workflows" / "code-scanning-gate.yml"
            ).read_text(encoding="utf-8")
            for workflow in workflow_paths:
                workflow.write_text(
                    source.replace('--pull-request "$PR_NUMBER"', "--ref invalid"),
                    encoding="utf-8",
                )
            allowlist.write_text(
                '{"schema-version": 1, "allowlist": []}', encoding="utf-8"
            )
            unsafe = validate_repository.validate_code_scanning_gate_contract(root)
            self.assertTrue(
                any("only base-branch alert-gate code" in item for item in unsafe)
            )

    def test_gate_uses_base_trusted_code_and_polls_for_the_test_merge(self) -> None:
        workflow = PLUGIN_ROOT / ".github" / "workflows" / "code-scanning-gate.yml"
        asset = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "code-scanning-gate.yml"
        )
        text = workflow.read_text(encoding="utf-8")

        self.assertEqual(text, asset.read_text(encoding="utf-8"))
        document = validate_repository.load_yaml(workflow)
        self.assertEqual(
            document["on"],
            {"pull_request_target": {"types": ["opened", "reopened", "synchronize"]}},
        )
        self.assertEqual(
            document["permissions"],
            {
                "contents": "read",
                "pull-requests": "read",
                "security-events": "read",
            },
        )
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('--pull-request "$PR_NUMBER"', text)
        self.assertNotIn("merge_commit_sha", text)
        self.assertNotIn("github.event.pull_request.head.sha", text)


class PullRequestTemplateContractTests(unittest.TestCase):
    def test_agents_and_trusted_workflows_enforce_the_template_contract(self) -> None:
        workflow = PLUGIN_ROOT / ".github" / "workflows" / "pr-template.yml"
        asset = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "pr-template.yml"
        )
        workflow_text = workflow.read_text(encoding="utf-8")

        self.assertEqual(workflow_text, asset.read_text(encoding="utf-8"))
        document = validate_repository.load_yaml(workflow)
        self.assertEqual(
            document["on"],
            {
                "pull_request_target": {
                    "types": [
                        "opened",
                        "edited",
                        "ready_for_review",
                        "reopened",
                        "synchronize",
                    ]
                }
            },
        )
        self.assertEqual(document["permissions"], {"contents": "read"})
        self.assertEqual(document["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(document["jobs"]["pr_template"]["name"], "pr-template")

        for fragment in (
            "ref: ${{ github.event.pull_request.base.sha }}",
            "persist-credentials: false",
            "PR_BODY: ${{ github.event.pull_request.body }}",
            "PR_IS_DRAFT: ${{ github.event.pull_request.draft }}",
            'Path(".github/PULL_REQUEST_TEMPLATE.md")',
            'Path(".github/PULL_REQUEST_TEMPLATE")',
            "repo-scaffold:pr-template=",
            "repo-scaffold:required-checklist:start",
            "repo-scaffold:optional-checklist:start",
            "Pull request body must select exactly one trusted template",
            "Pull request body must preserve every required heading and checklist",
            "Mark each required checklist item only after it is complete",
            "github.event.pull_request.user.login != 'dependabot[bot]'",
            "release-please--branches--",
        ):
            self.assertIn(fragment, workflow_text)

        template_ids = (
            "default",
            "feature",
            "bugfix",
            "documentation",
            "security",
            "deployment",
        )
        template_paths = {
            "default": (
                PLUGIN_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
                PLUGIN_ROOT
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "PULL_REQUEST_TEMPLATE.md",
                PLUGIN_ROOT
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "PULL_REQUEST_TEMPLATE.vi.md",
            ),
            **{
                template_id: (
                    PLUGIN_ROOT
                    / ".github"
                    / "PULL_REQUEST_TEMPLATE"
                    / f"{template_id}.md",
                    PLUGIN_ROOT
                    / "skills"
                    / "repo-scaffold"
                    / "assets"
                    / "PULL_REQUEST_TEMPLATE"
                    / f"{template_id}.md",
                    PLUGIN_ROOT
                    / "skills"
                    / "repo-scaffold"
                    / "assets"
                    / "PULL_REQUEST_TEMPLATE.vi"
                    / f"{template_id}.md",
                )
                for template_id in template_ids
                if template_id != "default"
            },
        }
        for template_id, paths in template_paths.items():
            for path in paths:
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    f"<!-- repo-scaffold:pr-template={template_id} -->",
                    text,
                    path,
                )
                self.assertIn("repo-scaffold:required-checklist:start", text, path)
                self.assertIn("repo-scaffold:required-checklist:end", text, path)
                self.assertIn("repo-scaffold:optional-checklist:start", text, path)
                self.assertIn("repo-scaffold:optional-checklist:end", text, path)
                self.assertRegex(text, r"(?m)^- \[ \] \S", path)

        for path in (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets" / "AGENTS.md",
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets" / "AGENTS.vi.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("--body-file", text)
            self.assertIn("--fill", text)
            self.assertRegex(text, r"ready(?:\s+|_)for(?:\s+|_)review", path)

        pull_request_reference = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "references"
            / "pull-request-contract.md"
        ).read_text(encoding="utf-8")
        self.assertIn("--body-file", pull_request_reference)
        self.assertIn("--fill", pull_request_reference)
        self.assertRegex(
            pull_request_reference,
            r"ready(?:\s+|_|-)for(?:\s+|_|-)review",
        )

    def test_gate_selects_the_marked_specialized_template(self) -> None:
        workflow = validate_repository.load_yaml(
            PLUGIN_ROOT / ".github" / "workflows" / "pr-template.yml"
        )
        run = workflow["jobs"]["pr_template"]["steps"][-1]["run"]
        script = run.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github"
            template_root.mkdir()
            shutil.copy2(
                PLUGIN_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
                template_root / "PULL_REQUEST_TEMPLATE.md",
            )
            shutil.copytree(
                PLUGIN_ROOT / ".github" / "PULL_REQUEST_TEMPLATE",
                template_root / "PULL_REQUEST_TEMPLATE",
            )
            feature_body = (
                template_root / "PULL_REQUEST_TEMPLATE" / "feature.md"
            ).read_text(encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={**os.environ, "PR_BODY": feature_body},
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            deployment_body = (
                template_root / "PULL_REQUEST_TEMPLATE" / "deployment.md"
            ).read_text(encoding="utf-8")
            deployment_result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={**os.environ, "PR_BODY": deployment_body},
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(deployment_result.returncode, 0, deployment_result.stderr)

            ready_incomplete = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={**os.environ, "PR_BODY": feature_body, "PR_IS_DRAFT": "false"},
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertNotEqual(ready_incomplete.returncode, 0)
            self.assertIn("only after it is complete", ready_incomplete.stderr)

            ready_body = re.sub(
                r"(<!-- repo-scaffold:required-checklist:start -->)(.*?)"
                r"(<!-- repo-scaffold:required-checklist:end -->)",
                lambda match: (
                    match.group(1)
                    + match.group(2).replace("- [ ]", "- [x]")
                    + match.group(3)
                ),
                feature_body,
                flags=re.DOTALL,
            )
            ready_completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={**os.environ, "PR_BODY": ready_body, "PR_IS_DRAFT": "false"},
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(ready_completed.returncode, 0, ready_completed.stderr)

            vietnamese_root = root / "vietnamese"
            vietnamese_template_root = vietnamese_root / ".github"
            vietnamese_template_root.mkdir(parents=True)
            shutil.copy2(
                PLUGIN_ROOT
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "PULL_REQUEST_TEMPLATE.vi.md",
                vietnamese_template_root / "PULL_REQUEST_TEMPLATE.md",
            )
            shutil.copytree(
                PLUGIN_ROOT
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "PULL_REQUEST_TEMPLATE.vi",
                vietnamese_template_root / "PULL_REQUEST_TEMPLATE",
            )
            vietnamese_body = (
                vietnamese_template_root / "PULL_REQUEST_TEMPLATE" / "feature.md"
            ).read_text(encoding="utf-8")
            vietnamese_result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=vietnamese_root,
                env={**os.environ, "PR_BODY": vietnamese_body},
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(vietnamese_result.returncode, 0, vietnamese_result.stderr)

            body_without_optional_items = re.sub(
                r"\n## If applicable\n\n"
                r"<!-- repo-scaffold:optional-checklist:start -->.*?"
                r"<!-- repo-scaffold:optional-checklist:end -->\n",
                "\n",
                feature_body,
                flags=re.DOTALL,
            )
            without_optional_items = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={**os.environ, "PR_BODY": body_without_optional_items},
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(
                without_optional_items.returncode, 0, without_optional_items.stderr
            )

            missing_marker = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={
                    **os.environ,
                    "PR_BODY": feature_body.replace(
                        "<!-- repo-scaffold:pr-template=feature -->\n\n", ""
                    ),
                },
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertNotEqual(missing_marker.returncode, 0)
            self.assertIn(
                "must select exactly one trusted template", missing_marker.stderr
            )

    def test_workflow_never_checks_out_or_executes_the_pull_request_head(self) -> None:
        workflow = (
            PLUGIN_ROOT / ".github" / "workflows" / "pr-template.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("pull_request_target", workflow)
        self.assertNotIn("github.event.pull_request.head.sha", workflow)
        self.assertNotIn("github.head_ref", workflow)
        self.assertNotIn("github.event.pull_request.user.type != 'Bot'", workflow)


class ReleaseAttestationValidationTests(unittest.TestCase):
    def write_valid_configuration(self, root: Path) -> None:
        action_sha = "a" * 40
        engine = {
            "jobs": {
                "build": {
                    "permissions": {"contents": "read"},
                    "steps": [
                        {
                            "name": "Build artifact",
                            "run": "git archive --worktree-attributes HEAD",
                        }
                    ],
                },
                "attest": {
                    "needs": "build",
                    "runs-on": "ubuntu-latest",
                    "timeout-minutes": 15,
                    "permissions": {
                        "contents": "read",
                        "id-token": "write",
                        "attestations": "write",
                    },
                    "steps": [
                        {
                            "name": "Receive release artifacts",
                            "uses": f"actions/download-artifact@{action_sha}",
                            "with": {
                                "name": "release-assets-${{ inputs.commit_sha }}",
                                "path": "dist/",
                            },
                        },
                        {
                            "name": "Validate downloaded artifacts",
                            "shell": "bash",
                            "run": validate_repository.ATTESTATION_VALIDATION_SCRIPT,
                        },
                        {
                            "name": "Attest release artifacts",
                            "uses": f"actions/attest@{action_sha}",
                            "with": {"subject-path": "dist/**"},
                        },
                    ],
                },
                "publish": {
                    "needs": ["build", "attest"],
                    "permissions": {"contents": "write"},
                },
            }
        }
        caller_permissions = {
            "contents": "write",
            "id-token": "write",
            "attestations": "write",
        }
        documents = {
            ".github/workflows/release.yml": engine,
            "skills/repo-scaffold/assets/workflows/release.yml": engine,
            ".github/workflows/release-please.yml": {
                "jobs": {"publish_release": {"permissions": caller_permissions}}
            },
            "skills/repo-scaffold/assets/workflows/release-please.yml": {
                "jobs": {"publish_release": {"permissions": caller_permissions}}
            },
            "skills/repo-scaffold/assets/workflows/release-tag.yml": {
                "jobs": {"release": {"permissions": caller_permissions}}
            },
        }
        for relative, document in documents.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    def test_accepts_isolated_attestation_and_permission_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)

            self.assertEqual(validate_repository.validate_release_attestation(root), [])

    def test_rejects_privilege_and_publish_gate_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            engine_path = root / ".github" / "workflows" / "release.yml"
            engine = yaml.safe_load(engine_path.read_text(encoding="utf-8"))
            engine["jobs"]["attest"]["permissions"].pop("id-token")
            engine["jobs"]["attest"]["steps"].insert(
                0,
                {
                    "name": "Unsafe checkout",
                    "uses": "actions/checkout@" + "b" * 40,
                },
            )
            engine["jobs"]["publish"]["needs"] = ["build"]
            engine_path.write_text(
                yaml.safe_dump(engine, sort_keys=False), encoding="utf-8"
            )
            caller_path = root / ".github" / "workflows" / "release-please.yml"
            caller = yaml.safe_load(caller_path.read_text(encoding="utf-8"))
            caller["jobs"]["publish_release"]["permissions"].pop("attestations")
            caller_path.write_text(
                yaml.safe_dump(caller, sort_keys=False), encoding="utf-8"
            )

            problems = validate_repository.validate_release_attestation(root)

            self.assertIn(
                ".github/workflows/release.yml: attest permissions must be "
                "contents: read, id-token: write, and attestations: write",
                problems,
            )
            self.assertIn(
                ".github/workflows/release.yml: attest must contain exactly "
                "receive, validate, and attest steps",
                problems,
            )
            self.assertIn(
                ".github/workflows/release.yml: publish must depend on build and "
                "attest",
                problems,
            )
            self.assertIn(
                ".github/workflows/release-please.yml: publish_release must pass "
                "contents: write, id-token: write, and attestations: write to the "
                "reusable release engine",
                problems,
            )

    def test_rejects_every_engine_job_and_step_contract_regression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            engine_path = root / ".github" / "workflows" / "release.yml"
            engine = {
                "jobs": {
                    "build": {"permissions": {}},
                    "attest": {
                        "needs": "other",
                        "runs-on": "windows-latest",
                        "timeout-minutes": 1,
                        "permissions": {},
                        "steps": [
                            {"uses": "actions/download-artifact@main", "with": {}},
                            {"name": "Wrong", "shell": "pwsh", "run": "execute"},
                            {"uses": "actions/attest@main", "with": {}},
                        ],
                    },
                    "publish": {"needs": ["build"], "permissions": {}},
                }
            }
            engine_path.write_text(
                yaml.safe_dump(engine, sort_keys=False), encoding="utf-8"
            )
            asset_engine = (
                root
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "workflows"
                / "release.yml"
            )
            asset_engine.write_text("jobs: {}\n", encoding="utf-8")

            installed_caller = root / ".github" / "workflows" / "release-please.yml"
            installed_caller.write_text(
                "jobs:\n  first: value\n  first: duplicate\n", encoding="utf-8"
            )
            asset_caller = (
                root
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "workflows"
                / "release-please.yml"
            )
            asset_caller.write_text("jobs: {}\n", encoding="utf-8")
            tag_caller = (
                root
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "workflows"
                / "release-tag.yml"
            )
            tag_caller.write_text(
                yaml.safe_dump(
                    {
                        "permissions": {
                            "contents": "write",
                            "id-token": "write",
                            "attestations": "write",
                        },
                        "jobs": {"release": {}},
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            problems = validate_repository.validate_release_attestation(root)

            expected_fragments = (
                "build permissions must be contents: read",
                "archive build must use git archive with --worktree-attributes",
                "attest must depend only on build",
                "attest must run on ubuntu-latest",
                "attest timeout must be 15 minutes",
                "attest permissions must be contents: read",
                "download-artifact pin",
                "download the build artifact to dist/",
                "validation step must match",
                "actions/attest pin",
                "subjects must cover dist/** only",
                "publish must depend on build and attest",
                "publish permissions must be contents: write",
                "build job is missing",
                "attest job is missing",
                "publish job is missing",
                "release caller is unreadable",
                "publish_release caller job is missing",
            )
            for expected in expected_fragments:
                self.assertTrue(any(expected in item for item in problems), expected)

    def test_engine_with_nonmapping_jobs_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            (root / ".github" / "workflows" / "release.yml").write_text(
                "jobs: []\n", encoding="utf-8"
            )

            self.assertTrue(
                any(
                    "jobs must be a mapping" in item
                    for item in validate_repository.validate_release_attestation(root)
                )
            )

    def test_unreadable_release_engine_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            (root / ".github" / "workflows" / "release.yml").write_text(
                "jobs: first\njobs: second\n", encoding="utf-8"
            )

            self.assertTrue(
                any(
                    "release engine is unreadable" in item
                    for item in validate_repository.validate_release_attestation(root)
                )
            )


class IssueFormValidationTests(unittest.TestCase):
    def test_scaffold_issue_forms_require_core_contributor_input(self) -> None:
        expected_forms = {
            ".github/ISSUE_TEMPLATE/bug_report.yml": {
                "description": True,
                "reproduction": True,
                "expected": True,
                "environment": True,
                "evidence": False,
            },
            ".github/ISSUE_TEMPLATE/feature_request.yml": {
                "problem": True,
                "proposal": True,
                "alternatives": False,
                "compatibility": True,
            },
            "skills/repo-scaffold/assets/ISSUE_TEMPLATE/bug_report.yml": {
                "description": True,
                "reproduction": True,
                "expected_actual": True,
                "environment": True,
                "evidence": False,
            },
            "skills/repo-scaffold/assets/ISSUE_TEMPLATE/feature_request.yml": {
                "problem": True,
                "solution": True,
                "alternatives": False,
                "context": False,
            },
            "skills/repo-scaffold/assets/ISSUE_TEMPLATE/bug_report.vi.yml": {
                "description": True,
                "reproduction": True,
                "expected_actual": True,
                "environment": True,
                "evidence": False,
            },
            "skills/repo-scaffold/assets/ISSUE_TEMPLATE/feature_request.vi.yml": {
                "problem": True,
                "solution": True,
                "alternatives": False,
                "context": False,
            },
        }

        for relative, required_inputs in expected_forms.items():
            document = validate_repository.load_yaml(PLUGIN_ROOT / relative)
            body = document["body"]
            inputs = {item["id"]: item for item in body if item["type"] == "textarea"}
            self.assertEqual(set(inputs), set(required_inputs), relative)
            for identifier, required in required_inputs.items():
                self.assertEqual(
                    inputs[identifier]["validations"]["required"],
                    str(required).lower(),
                    f"{relative}: {identifier}",
                )
            checkboxes = [item for item in body if item["type"] == "checkboxes"]
            self.assertEqual(len(checkboxes), 1, relative)
            self.assertEqual(
                checkboxes[0]["attributes"]["options"][0]["required"],
                "true",
                relative,
            )

        for path in (
            PLUGIN_ROOT / ".github" / "ISSUE_TEMPLATE",
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets" / "ISSUE_TEMPLATE",
        ):
            self.assertEqual(list(path.glob("bug_report*.md")), [], path)
            self.assertEqual(list(path.glob("feature_request*.md")), [], path)

    def test_scaffold_localized_chooser_is_not_misclassified_as_an_issue_form(
        self,
    ) -> None:
        self.assertEqual(validate_repository.validate_issue_templates(PLUGIN_ROOT), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            (template_root / "config.vi.yml").write_text(
                "blank_issues_enabled: false\n", encoding="utf-8"
            )

            problems = validate_repository.validate_issue_templates(root)

        self.assertIn(
            f"{Path('.github/ISSUE_TEMPLATE/config.vi.yml')}: name must be nonempty",
            problems,
        )

    def test_upload_input_matches_current_github_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            (template_root / "evidence.yml").write_text(
                """
name: Evidence upload
description: Attach files that help reproduce the problem.
body:
  - type: upload
    id: evidence
    attributes:
      label: Attach relevant files
      description: Include screenshots or non-sensitive logs.
    validations:
      required: false
      accept: ".png,.jpg,.log"
""".strip(),
                encoding="utf-8",
            )

            self.assertEqual(
                validate_repository.validate_issue_templates(root),
                [],
            )

    def test_issue_form_requires_unique_valid_ids_and_an_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            (template_root / "invalid.yml").write_text(
                """
name: Invalid form
description: Exercise issue-form validation.
body:
  - type: markdown
    id: invalid id
    attributes:
      value: Guidance
  - type: markdown
    id: duplicate
    attributes:
      value: More guidance
  - type: markdown
    id: duplicate
    attributes:
      value: Final guidance
""".strip(),
                encoding="utf-8",
            )

            problems = validate_repository.validate_issue_templates(root)
            relative = Path(".github") / "ISSUE_TEMPLATE" / "invalid.yml"

            self.assertIn(
                f"{relative}: body[0].id may contain only letters, numbers, -, and _",
                problems,
            )
            self.assertIn(
                f"{relative}: body[2].id must be unique",
                problems,
            )
            self.assertIn(
                f"{relative}: body must contain a non-markdown input",
                problems,
            )

    def test_issue_form_requires_documented_yml_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            path = template_root / "bug.yaml"
            path.write_text(
                """
name: Bug report
description: Report a problem.
body:
  - type: textarea
    attributes:
      label: What happened?
""".strip(),
                encoding="utf-8",
            )

            self.assertEqual(
                validate_repository.validate_issue_templates(root),
                [f"{path.relative_to(root)}: issue forms must use the .yml extension"],
            )

    def test_issue_form_body_rejects_invalid_attributes_options_and_validations(
        self,
    ) -> None:
        relative = Path(".github/ISSUE_TEMPLATE/invalid.yml")
        body: list[object] = [
            "invalid",
            {"type": "unknown"},
            {"type": "input", "id": "valid", "attributes": []},
            {
                "type": "dropdown",
                "attributes": {"label": "Choose", "options": []},
                "validations": [],
            },
            {
                "type": "dropdown",
                "attributes": {"label": "Choose", "options": ["same", "same"]},
                "validations": {"required": "maybe"},
            },
            {
                "type": "checkboxes",
                "attributes": {"label": "Confirm", "options": []},
            },
            {
                "type": "checkboxes",
                "attributes": {
                    "label": "Confirm",
                    "options": [
                        "invalid",
                        {"label": "same", "required": "maybe"},
                        {"label": "same", "required": "true"},
                    ],
                },
            },
            {
                "type": "upload",
                "attributes": {"label": "Upload"},
                "validations": {"accept": " "},
            },
            {"type": "markdown", "attributes": {"value": " "}},
            {
                "type": "dropdown",
                "attributes": {"label": "Valid", "options": ["one", "two"]},
            },
            {
                "type": "checkboxes",
                "attributes": {
                    "label": "Valid",
                    "options": [{"label": "unique", "required": "false"}],
                },
            },
            {
                "type": "input",
                "id": "unexpected",
                "unexpected": "value",
                "attributes": {"label": "Duplicate label"},
            },
            {
                "type": "input",
                "id": "duplicate_label",
                "attributes": {"label": "Duplicate label"},
            },
            {
                "type": "checkboxes",
                "id": "cross_input",
                "attributes": {
                    "label": "Confirm another",
                    "options": [{"label": "Duplicate label"}],
                },
            },
        ]

        problems = validate_repository.validate_issue_form_body(relative, body)

        expected_fragments = (
            "body[0] must be a mapping",
            "body[1] has invalid type",
            "body[2].attributes must be a mapping",
            "body[3].attributes.options must be a nonempty string list",
            "body[3].validations must be a mapping",
            "body[4].attributes.options must be unique",
            "body[4].validations.required must be a boolean",
            "body[5].attributes.options must be a nonempty list",
            "body[6].attributes.options[0].label must be nonempty",
            "body[6].attributes.options[1].required must be a boolean",
            "body[6].attributes.options labels must be unique",
            "body[7].validations.accept must be nonempty",
            "body[8].attributes.value must be nonempty",
            "body[11] contains unsupported keys",
            "body[12].attributes.label must be unique",
            "body[13].attributes.options labels must be unique among form inputs",
        )
        for expected in expected_fragments:
            self.assertTrue(any(expected in item for item in problems), expected)

    def test_issue_form_accepts_type_and_rejects_unsupported_top_level_keys(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            (template_root / "valid.yml").write_text(
                "name: Valid form\n"
                "description: Validate the top-level schema.\n"
                "type: bug\n"
                "body:\n"
                "  - type: input\n"
                "    id: details\n"
                "    attributes:\n"
                "      label: Details\n",
                encoding="utf-8",
            )
            (template_root / "invalid.yml").write_text(
                "name: Invalid form\n"
                "description: Validate the top-level schema.\n"
                "type: ''\n"
                "unexpected: value\n"
                "body:\n"
                "  - type: input\n"
                "    id: details\n"
                "    attributes:\n"
                "      label: Details\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_issue_templates(root)

        self.assertFalse(
            any(
                item.startswith(".github/ISSUE_TEMPLATE/valid.yml") for item in problems
            )
        )
        self.assertTrue(
            any("type must be a nonempty string" in item for item in problems)
        )
        self.assertTrue(
            any("issue form contains unsupported keys" in item for item in problems)
        )

    def test_legacy_issue_templates_and_chooser_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            markdown_cases = {
                "missing.md": "No front matter\n",
                "shape.md": "---\n- invalid\n---\nBody\n",
                "fields.md": "---\nname: Bug\nabout: ''\n---\n",
                "valid.md": "---\nname: Valid template\nabout: Useful guidance\n---\nBody\n",
            }
            for name, content in markdown_cases.items():
                (template_root / name).write_text(content, encoding="utf-8")
            (template_root / "invalid.yml").write_text(
                "name: first\nname: second\n", encoding="utf-8"
            )
            (template_root / "scalar.yml").write_text("- invalid\n", encoding="utf-8")
            (template_root / "empty.yml").write_text(
                "name: Bug\ndescription: ''\nbody: []\n", encoding="utf-8"
            )
            (template_root / "config.yml").write_text(
                "blank_issues_enabled: maybe\ncontact_links: invalid\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_issue_templates(root)

            expected_fragments = (
                "invalid front matter",
                "front matter must be a mapping",
                "about must be nonempty",
                "name must be more than 3 characters",
                "template body must be nonempty",
                "invalid issue form YAML",
                "issue form root must be a mapping",
                "description must be nonempty",
                "body must be a nonempty list",
                "blank_issues_enabled must be a boolean",
                "contact_links must be a list",
            )
            for expected in expected_fragments:
                self.assertTrue(any(expected in item for item in problems), expected)

    def test_issue_chooser_contact_links_require_complete_https_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / ".github" / "ISSUE_TEMPLATE"
            asset = root / "skills" / "repo-scaffold" / "assets" / "ISSUE_TEMPLATE"
            installed.mkdir(parents=True)
            asset.mkdir(parents=True)
            (installed / "config.yml").write_text(
                "blank_issues_enabled: false\n"
                "contact_links:\n"
                "  - invalid\n"
                "  - name: ''\n"
                "    url: http://example.com\n"
                "    about: ''\n"
                "  - name: Template\n"
                "    url: '{{REPO_SCAFFOLD_URL}}'\n"
                "    about: Valid placeholder\n",
                encoding="utf-8",
            )
            (asset / "config.yml").write_text("- invalid\n", encoding="utf-8")

            problems = validate_repository.validate_issue_templates(root)

            self.assertTrue(
                any("contact_links[0] must be a mapping" in item for item in problems)
            )
            self.assertTrue(
                any(
                    "contact_links[1].name must be nonempty" in item
                    for item in problems
                )
            )
            self.assertTrue(
                any(
                    "contact_links[1].about must be nonempty" in item
                    for item in problems
                )
            )
            self.assertTrue(
                any("contact_links[1].url must use HTTPS" in item for item in problems)
            )
            self.assertTrue(
                any("config.yml: root must be a mapping" in item for item in problems)
            )

    def test_front_matter_and_chooser_require_closing_delimiter_and_unique_yaml(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "missing-close.md"
            markdown.write_text("---\nname: Missing close\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing closing"):
                validate_repository.read_front_matter(markdown)

            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            (template_root / "config.yml").write_text(
                "blank_issues_enabled: false\nblank_issues_enabled: true\n",
                encoding="utf-8",
            )

            self.assertTrue(
                any(
                    "invalid chooser YAML" in item
                    for item in validate_repository.validate_issue_templates(root)
                )
            )


class ReleaseNotesConfigValidationTests(unittest.TestCase):
    def test_repository_release_notes_configurations_are_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_release_notes_config(PLUGIN_ROOT), []
        )

    def test_release_notes_config_rejects_roots_and_empty_categories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_yaml = validate_repository.yaml.YAMLError("invalid YAML")
            with mock.patch.object(
                validate_repository,
                "load_yaml",
                side_effect=[invalid_yaml, [], {"changelog": {"categories": []}}],
            ):
                problems = validate_repository.validate_release_notes_config(root)
            with mock.patch.object(
                validate_repository,
                "load_yaml",
                return_value={"changelog": "invalid"},
            ):
                problems.extend(validate_repository.validate_release_notes_config(root))
            with mock.patch.object(
                validate_repository,
                "load_yaml",
                side_effect=[
                    {
                        "changelog": {
                            "exclude": "invalid",
                            "categories": [{"title": "Other", "labels": ["*"]}],
                        }
                    },
                    {
                        "changelog": {
                            "exclude": {"labels": []},
                            "categories": [{"title": "Other", "labels": ["*"]}],
                        }
                    },
                    {
                        "changelog": {
                            "categories": [{"title": "Other", "labels": ["*"]}]
                        }
                    },
                ],
            ):
                problems.extend(validate_repository.validate_release_notes_config(root))

        expected_fragments = (
            "invalid release-notes YAML",
            "release-notes root must be a mapping",
            "changelog must be a mapping",
            "changelog.exclude must be a mapping",
            "changelog.exclude.labels must be a nonempty string list",
            "changelog.categories must be a nonempty list",
        )
        for expected in expected_fragments:
            self.assertTrue(any(expected in item for item in problems), expected)

    def test_release_notes_config_rejects_invalid_categories_and_catchall(self) -> None:
        document = {
            "changelog": {
                "categories": [
                    "invalid",
                    {},
                    {"title": "Invalid labels", "labels": []},
                    {"title": "Duplicate catchall", "labels": ["*", "*"]},
                ]
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                validate_repository, "load_yaml", return_value=document
            ):
                problems = validate_repository.validate_release_notes_config(root)

        expected_fragments = (
            "changelog.categories[0] must be a mapping",
            "changelog.categories[1].title must be nonempty",
            "changelog.categories[1].labels must be a nonempty string list",
            "changelog.categories[2].labels must be a nonempty string list",
            "must contain exactly one '*' catchall",
        )
        for expected in expected_fragments:
            self.assertTrue(any(expected in item for item in problems), expected)


class DependabotValidationTests(unittest.TestCase):
    def test_repository_dependabot_configuration_is_valid(self) -> None:
        self.assertEqual(validate_repository.validate_dependabot(PLUGIN_ROOT), [])

    def test_dependabot_rejects_invalid_yaml_root_and_update_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / ".github" / "dependabot.yml"
            asset = root / "skills" / "repo-scaffold" / "assets" / "dependabot.yml"
            installed.parent.mkdir(parents=True)
            asset.parent.mkdir(parents=True)
            installed.write_text("version: 2\nversion: 2\n", encoding="utf-8")
            asset.write_text(
                "version: 1\n"
                "updates:\n"
                "  - invalid\n"
                "  - package-ecosystem: ''\n"
                "    directory: ''\n"
                "    schedule: invalid\n"
                "  - package-ecosystem: pip\n"
                "    directory: /\n"
                "    schedule:\n"
                "      interval: sometimes\n"
                "  - package-ecosystem: pip\n"
                "    schedule:\n"
                "      interval: weekly\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_dependabot(root)

            expected_fragments = (
                "invalid Dependabot YAML",
                "version must be 2",
                "updates[0] must be a mapping",
                "updates[1].package-ecosystem is required",
                "updates[1].directory must be nonempty",
                "updates[1].schedule is required",
                "updates[2].schedule.interval is invalid",
                "updates[3].directory or directories is required",
            )
            for expected in expected_fragments:
                self.assertTrue(any(expected in item for item in problems), expected)

    def test_dependabot_rejects_nonmapping_root_and_empty_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / ".github" / "dependabot.yml"
            asset = root / "skills" / "repo-scaffold" / "assets" / "dependabot.yml"
            installed.parent.mkdir(parents=True)
            asset.parent.mkdir(parents=True)
            installed.write_text("- invalid\n", encoding="utf-8")
            asset.write_text("version: 2\nupdates: []\n", encoding="utf-8")

            problems = validate_repository.validate_dependabot(root)

            self.assertTrue(any("root must be a mapping" in item for item in problems))
            self.assertTrue(
                any("updates must be a nonempty list" in item for item in problems)
            )

    def test_dependabot_rejects_invalid_directories_and_unsynchronized_actions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / ".github" / "dependabot.yml"
            asset = root / "skills" / "repo-scaffold" / "assets" / "dependabot.yml"
            installed.parent.mkdir(parents=True)
            asset.parent.mkdir(parents=True)
            installed.write_text(
                "version: 2\n"
                "updates:\n"
                "  - package-ecosystem: github-actions\n"
                "    directories: ['/', '/']\n"
                "    schedule:\n"
                "      interval: weekly\n",
                encoding="utf-8",
            )
            asset.write_text(
                "version: 2\n"
                "updates:\n"
                "  - package-ecosystem: github-actions\n"
                "    directory: /\n"
                "    directories: ['/templates']\n"
                "    schedule:\n"
                "      interval: weekly\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_dependabot(root)

            self.assertTrue(any("nonempty unique list" in item for item in problems))
            self.assertTrue(any("not both" in item for item in problems))
            self.assertTrue(
                any(
                    "must group every installed workflow action" in item
                    for item in problems
                )
            )

    def test_dependabot_rejects_unsynchronized_python_and_incomplete_template(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / ".github" / "dependabot.yml"
            asset = root / "skills" / "repo-scaffold" / "assets" / "dependabot.yml"
            installed.parent.mkdir(parents=True)
            asset.parent.mkdir(parents=True)
            installed.write_text(
                "version: 2\n"
                "updates:\n"
                "  - package-ecosystem: pip\n"
                "    directory: /\n"
                "    schedule:\n"
                "      interval: weekly\n"
                "  - package-ecosystem: github-actions\n"
                "    directories: ['/', '/skills/repo-scaffold/assets/workflows']\n"
                "    schedule:\n"
                "      interval: weekly\n"
                "    groups:\n"
                "      synchronized-actions:\n"
                "        group-by: dependency-name\n",
                encoding="utf-8",
            )
            asset.write_text(
                "version: 2\n"
                "updates:\n"
                "  - package-ecosystem: github-actions\n"
                "    directory: /templates\n"
                "    schedule:\n"
                "      interval: weekly\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_dependabot(root)

            self.assertTrue(
                any("must synchronize root locks" in item for item in problems)
            )
            self.assertTrue(any("fixed root pip updater" in item for item in problems))
            self.assertTrue(
                any("fixed root GitHub Actions updater" in item for item in problems)
            )
            self.assertTrue(
                any(
                    "must group every installed workflow action" in item
                    for item in problems
                )
            )

    def test_dependabot_rendering_contract_keeps_mandatory_documentation_pip(
        self,
    ) -> None:
        generation = (
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "references"
            / "scaffold-generation.md"
        ).read_text(encoding="utf-8")
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")

        for document in (generation, setup):
            self.assertIn("requirements-docs.txt", document)
            self.assertIn("fixed root `pip`", document)
            self.assertIn("Do not emit a duplicate root `pip` block", document)
            self.assertIn('patterns: ["*"]', document)


class WorkflowShellValidationTests(unittest.TestCase):
    def write_workflow(self, directory: str, content: str) -> Path:
        path = Path(directory) / "ci.yml"
        path.write_text(content.strip(), encoding="utf-8")
        return path

    def test_executable_resolution_skips_unsafe_and_unusable_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forbidden = root / "repository"
            external = root / "external"
            missing = root / "missing"
            forbidden.mkdir()
            external.mkdir()
            missing.mkdir()
            inside_tool = forbidden / "actionlint"
            outside_tool = external / "actionlint"
            inside_tool.touch()
            outside_tool.touch()
            path_value = os.pathsep.join(
                ["", "relative", str(missing), str(forbidden), str(external)]
            )

            def resolve_tool(_name: str, *, path: str) -> str | None:
                if path == str(forbidden):
                    return str(inside_tool)
                if path == str(external):
                    return str(outside_tool)
                return None

            with (
                mock.patch.dict(os.environ, {"PATH": path_value}),
                mock.patch.object(
                    validate_workflows.shutil,
                    "which",
                    side_effect=resolve_tool,
                ),
            ):
                result = validate_workflows.resolve_path_executable(
                    "actionlint", forbidden_root=forbidden
                )

            self.assertEqual(result, str(outside_tool.resolve()))

    def test_executable_resolution_returns_none_without_safe_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            forbidden = Path(directory)
            with (
                mock.patch.dict(os.environ, {"PATH": "relative"}),
                mock.patch.object(validate_workflows.shutil, "which") as which,
            ):
                result = validate_workflows.resolve_path_executable(
                    "actionlint", forbidden_root=forbidden
                )

            self.assertIsNone(result)
            which.assert_not_called()

    def test_executable_resolution_requires_existing_roots_and_defaults_empty_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(validate_workflows.shutil, "which") as which,
                self.assertRaises(FileNotFoundError),
            ):
                validate_workflows.resolve_path_executable(
                    "actionlint", forbidden_root=missing
                )
            which.assert_not_called()

    def test_executable_resolution_strips_only_path_entry_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forbidden = root / "repository"
            external = root / "XexternalX"
            forbidden.mkdir()
            external.mkdir()
            tool = external / "actionlint"
            tool.touch()
            with (
                mock.patch.dict(os.environ, {"PATH": f'"{external}"'}),
                mock.patch.object(
                    validate_workflows.shutil, "which", return_value=str(tool)
                ) as which,
            ):
                result = validate_workflows.resolve_path_executable(
                    "actionlint", forbidden_root=forbidden
                )

            self.assertEqual(result, str(tool.resolve()))
            which.assert_called_once_with("actionlint", path=str(external))

    def test_executable_resolution_ignores_unresolvable_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forbidden = root / "repository"
            broken_directory = root / "broken-directory"
            external = root / "external"
            forbidden.mkdir()
            broken_directory.mkdir()
            external.mkdir()
            broken_tool = broken_directory / "actionlint"
            outside_tool = external / "actionlint"
            outside_tool.touch()
            original_resolve = Path.resolve

            def resolve_path(path: Path, strict: bool = False) -> Path:
                if path == broken_tool:
                    raise OSError("unresolvable candidate")
                return original_resolve(path, strict=strict)

            def resolve_tool(_name: str, *, path: str) -> str | None:
                if path == str(broken_directory):
                    return str(broken_tool)
                return str(outside_tool)

            with (
                mock.patch.dict(
                    os.environ,
                    {"PATH": os.pathsep.join([str(broken_directory), str(external)])},
                ),
                mock.patch.object(
                    validate_workflows.shutil,
                    "which",
                    side_effect=resolve_tool,
                ),
                mock.patch.object(validate_workflows.Path, "resolve", resolve_path),
            ):
                result = validate_workflows.resolve_path_executable(
                    "actionlint", forbidden_root=forbidden
                )

            self.assertEqual(result, str(outside_tool.resolve()))

    def test_actionlint_runner_returns_process_status_and_timeout(self) -> None:
        workflow = Path("ci.yml")
        working_directory = Path("repository")
        with mock.patch.object(
            validate_workflows.subprocess,
            "run",
            return_value=mock.Mock(returncode=7),
        ) as subprocess_run:
            result = validate_workflows.run_actionlint(
                "actionlint", [workflow], working_directory=working_directory
            )

        self.assertEqual(result, 7)
        subprocess_run.assert_called_once_with(
            ["actionlint", "-no-color", "-shellcheck=", "ci.yml"],
            cwd=working_directory,
            check=False,
            timeout=60,
        )

        stderr = StringIO()
        with (
            mock.patch.object(
                validate_workflows.subprocess,
                "run",
                side_effect=validate_workflows.subprocess.TimeoutExpired(
                    ["actionlint"], 60
                ),
            ),
            redirect_stderr(stderr),
        ):
            timeout_result = validate_workflows.run_actionlint(
                "actionlint", [workflow], working_directory=working_directory
            )

        self.assertEqual(timeout_result, 2)
        self.assertEqual(stderr.getvalue(), "actionlint timed out.\n")

    def test_bash_block_is_normalized_to_binary_lf_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ci.yml"
            path.write_bytes(
                b"jobs:\r\n"
                b"  test:\r\n"
                b"    runs-on: ubuntu-latest\r\n"
                b"    steps:\r\n"
                b"      - name: Test\r\n"
                b"        run: |\r\n"
                b"          echo ok\r\n"
            )

            blocks = validate_workflows.workflow_shell_blocks(path)

            self.assertEqual(blocks, [("test: Test", "bash", b"echo ok\n")])

    def test_unknown_shell_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ci.yml"
            path.write_text(
                """
jobs:
  test:
    runs-on: windows-latest
    steps:
      - shell: pwsh
        run: Write-Output ok
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unsupported shell"):
                validate_workflows.workflow_shell_blocks(path)

    def test_workflow_parser_rejects_invalid_roots_jobs_steps_and_runs(self) -> None:
        cases = [
            ("- workflow", "workflow root must be a mapping"),
            ("name: CI", "workflow jobs must be a mapping"),
            (
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: invalid",
                "steps must be a list",
            ),
            (
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: [7]",
                "run must be text",
            ),
        ]

        with tempfile.TemporaryDirectory() as directory:
            for index, (content, message) in enumerate(cases):
                path = Path(directory) / f"case-{index}.yml"
                path.write_text(content, encoding="utf-8")
                with self.subTest(message=message):
                    with self.assertRaisesRegex(ValueError, message):
                        validate_workflows.workflow_shell_blocks(path)

    def test_workflow_parser_reads_utf8_and_ignores_jobs_without_steps(self) -> None:
        path = mock.Mock(spec=Path)
        path.read_text.return_value = (
            "jobs:\n  scalar: 7\n  empty:\n    runs-on: ubuntu-latest\n"
        )

        self.assertEqual(validate_workflows.workflow_shell_blocks(path), [])
        path.read_text.assert_called_once_with(encoding="utf-8")

    def test_workflow_parser_errors_are_exact(self) -> None:
        cases = (
            ("- workflow", "workflow root must be a mapping"),
            ("name: CI", "workflow jobs must be a mapping"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (content, expected) in enumerate(cases):
                path = Path(directory) / f"exact-{index}.yml"
                path.write_text(content, encoding="utf-8")
                with self.subTest(expected=expected):
                    with self.assertRaises(ValueError) as raised:
                        validate_workflows.workflow_shell_blocks(path)
                    self.assertEqual(str(raised.exception), expected)

    def test_workflow_parser_uses_defaults_lists_and_step_fallback_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_workflow(
                directory,
                """
jobs:
  ignored-scalar: invalid
  ignored-reusable:
    uses: owner/repository/.github/workflows/reusable.yml@main
  test:
    runs-on: [ubuntu-latest, self-hosted]
    defaults:
      run:
        shell: sh -e {0}
    steps:
      - uses: actions/checkout@main
      - invalid
      - run: echo ok
      - name: Explicit
        shell: bash
        run: echo explicit
""",
            )

            blocks = validate_workflows.workflow_shell_blocks(path)

            self.assertEqual(
                blocks,
                [
                    ("test: step 2", "sh", b"echo ok"),
                    ("test: Explicit", "bash", b"echo explicit"),
                ],
            )

    def test_workflow_parser_handles_nonmapping_defaults_and_non_linux_runner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_workflow(
                directory,
                """
jobs:
  test:
    runs-on: windows-latest
    defaults: invalid
    steps:
      - run: echo ok
""",
            )

            with self.assertRaisesRegex(ValueError, "unsupported shell None"):
                validate_workflows.workflow_shell_blocks(path)

    def test_shellcheck_has_a_finite_timeout(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                validate_workflows, "workflow_shell_blocks"
            ) as extract_blocks,
            mock.patch.object(validate_workflows.subprocess, "run") as subprocess_run,
        ):
            path = Path(directory) / "ci.yml"
            extract_blocks.return_value = [("test: Test", "bash", b"echo ok\n")]
            subprocess_run.return_value = mock.Mock(returncode=0)

            result = validate_workflows.run_shellcheck("shellcheck", [path])

            self.assertEqual(result, 0)
            extract_blocks.assert_called_once_with(path)
            subprocess_run.assert_called_once_with(
                [
                    "shellcheck",
                    "--shell=bash",
                    "--format=gcc",
                    "-",
                ],
                input=b"echo ok\n",
                check=False,
                capture_output=True,
                timeout=30,
            )

    def test_shellcheck_reports_extraction_timeout_and_tool_failures(self) -> None:
        path = Path("ci.yml")
        extraction_stderr = mock.Mock()
        with (
            mock.patch.object(
                validate_workflows,
                "workflow_shell_blocks",
                side_effect=ValueError("invalid workflow"),
            ),
            mock.patch.object(validate_workflows.sys, "stderr", extraction_stderr),
        ):
            extraction_result = validate_workflows.run_shellcheck("shellcheck", [path])
        self.assertEqual(extraction_result, 2)
        self.assertTrue(
            any(
                "could not extract shell blocks" in call.args[0]
                for call in extraction_stderr.write.call_args_list
            )
        )

        timeout_stderr = mock.Mock()
        with (
            mock.patch.object(
                validate_workflows,
                "workflow_shell_blocks",
                return_value=[("test: Test", "bash", b"echo ok")],
            ),
            mock.patch.object(
                validate_workflows.subprocess,
                "run",
                side_effect=validate_workflows.subprocess.TimeoutExpired(
                    ["shellcheck"], 30
                ),
            ),
            mock.patch.object(validate_workflows.sys, "stderr", timeout_stderr),
        ):
            timeout_result = validate_workflows.run_shellcheck("shellcheck", [path])
        self.assertEqual(timeout_result, 2)
        self.assertTrue(
            any(
                "ShellCheck timed out" in call.args[0]
                for call in timeout_stderr.write.call_args_list
            )
        )

        failure_stderr = mock.Mock()
        failure_stderr.buffer = BytesIO()
        process = mock.Mock(returncode=3, stdout=b"stdout\n", stderr=b"stderr\n")
        with (
            mock.patch.object(
                validate_workflows,
                "workflow_shell_blocks",
                return_value=[("test: Test", "sh", b"exit 3")],
            ),
            mock.patch.object(
                validate_workflows.subprocess, "run", return_value=process
            ),
            mock.patch.object(validate_workflows.sys, "stderr", failure_stderr),
        ):
            failure_result = validate_workflows.run_shellcheck("shellcheck", [path])
        self.assertEqual(failure_result, 3)
        self.assertEqual(failure_stderr.buffer.getvalue(), b"stdout\nstderr\n")
        self.assertEqual(
            [call.args for call in failure_stderr.write.call_args_list],
            [("ci.yml (test: Test):",), ("\n",)],
        )

    def test_shellcheck_rejects_workflows_without_shell_blocks(self) -> None:
        stderr = StringIO()
        with (
            mock.patch.object(
                validate_workflows, "workflow_shell_blocks", return_value=[]
            ),
            redirect_stderr(stderr),
        ):
            result = validate_workflows.run_shellcheck("shellcheck", [Path("ci.yml")])

        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "No shell run blocks were found.\n")

    def test_main_reports_missing_tools_or_workflow_groups(self) -> None:
        stderr = mock.Mock()
        with (
            mock.patch.object(
                validate_workflows,
                "resolve_path_executable",
                return_value=None,
            ),
            mock.patch.object(validate_workflows.sys, "stderr", stderr),
        ):
            self.assertEqual(validate_workflows.main(), 2)
        self.assertTrue(
            any(
                "actionlint is required" in call.args[0]
                for call in stderr.write.call_args_list
            )
        )

    def test_main_uses_exact_tool_names_roots_and_diagnostics(self) -> None:
        repository_root = WORKFLOW_SCRIPT_PATH.resolve().parents[1]
        resolver = mock.Mock(return_value=None)
        stderr = StringIO()
        with (
            mock.patch.object(validate_workflows, "resolve_path_executable", resolver),
            redirect_stderr(stderr),
        ):
            self.assertEqual(validate_workflows.main(), 2)
        resolver.assert_called_once_with("actionlint", forbidden_root=repository_root)
        self.assertEqual(
            stderr.getvalue(),
            "actionlint is required on an absolute PATH entry outside the "
            "repository.\n",
        )

        resolver = mock.Mock(side_effect=["actionlint", None])
        stderr = StringIO()
        with (
            mock.patch.object(validate_workflows, "resolve_path_executable", resolver),
            redirect_stderr(stderr),
        ):
            self.assertEqual(validate_workflows.main(), 2)
        self.assertEqual(
            resolver.call_args_list,
            [
                mock.call("actionlint", forbidden_root=repository_root),
                mock.call("shellcheck", forbidden_root=repository_root),
            ],
        )
        self.assertEqual(
            stderr.getvalue(),
            "ShellCheck is required on an absolute PATH entry outside the "
            "repository.\n",
        )

        stderr = StringIO()
        with (
            mock.patch.object(
                validate_workflows,
                "resolve_path_executable",
                side_effect=["actionlint", "shellcheck"],
            ),
            mock.patch.object(validate_workflows.Path, "glob", return_value=[]),
            redirect_stderr(stderr),
        ):
            self.assertEqual(validate_workflows.main(), 2)
        self.assertEqual(
            stderr.getvalue(),
            "Expected installed workflows and workflow assets.\n",
        )

        stderr = mock.Mock()
        with (
            mock.patch.object(
                validate_workflows,
                "resolve_path_executable",
                side_effect=["actionlint", None],
            ),
            mock.patch.object(validate_workflows.sys, "stderr", stderr),
        ):
            self.assertEqual(validate_workflows.main(), 2)
        self.assertTrue(
            any(
                "ShellCheck is required" in call.args[0]
                for call in stderr.write.call_args_list
            )
        )

        stderr = mock.Mock()
        with (
            mock.patch.object(
                validate_workflows,
                "resolve_path_executable",
                side_effect=["actionlint", "shellcheck"],
            ),
            mock.patch.object(validate_workflows.Path, "glob", return_value=[]),
            mock.patch.object(validate_workflows.sys, "stderr", stderr),
        ):
            self.assertEqual(validate_workflows.main(), 2)
        self.assertTrue(
            any(
                "Expected installed workflows" in call.args[0]
                for call in stderr.write.call_args_list
            )
        )

    def test_main_propagates_validators_and_checks_copied_assets(self) -> None:
        repository_root = WORKFLOW_SCRIPT_PATH.resolve().parents[1]
        installed_workflows = sorted(
            (repository_root / ".github" / "workflows").glob("*.yml")
        )
        asset_workflows = sorted(
            (
                repository_root / "skills" / "repo-scaffold" / "assets" / "workflows"
            ).glob("*.yml")
        )
        with (
            mock.patch.object(
                validate_workflows,
                "resolve_path_executable",
                side_effect=["actionlint", "shellcheck"],
            ),
            mock.patch.object(
                validate_workflows, "run_actionlint", return_value=4
            ) as actionlint,
        ):
            self.assertEqual(validate_workflows.main(), 4)
            actionlint.assert_called_once_with(
                "actionlint",
                installed_workflows,
                working_directory=repository_root,
            )

        with (
            mock.patch.object(
                validate_workflows,
                "resolve_path_executable",
                side_effect=["actionlint", "shellcheck"],
            ),
            mock.patch.object(validate_workflows, "run_actionlint", return_value=0),
            mock.patch.object(
                validate_workflows, "run_shellcheck", return_value=5
            ) as shellcheck,
        ):
            self.assertEqual(validate_workflows.main(), 5)
            shellcheck.assert_called_once_with(
                "shellcheck", [*installed_workflows, *asset_workflows]
            )

        with (
            mock.patch.object(
                validate_workflows,
                "resolve_path_executable",
                side_effect=["actionlint", "shellcheck"],
            ),
            mock.patch.object(
                validate_workflows, "run_actionlint", side_effect=[0, 6]
            ) as actionlint,
            mock.patch.object(validate_workflows, "run_shellcheck", return_value=0),
        ):
            self.assertEqual(validate_workflows.main(), 6)

        self.assertEqual(actionlint.call_count, 2)
        self.assertEqual(
            actionlint.call_args_list[0],
            mock.call(
                "actionlint",
                installed_workflows,
                working_directory=repository_root,
            ),
        )
        self.assertEqual(actionlint.call_args_list[1].args[0], "actionlint")
        copied_files = actionlint.call_args_list[1].args[1]
        copied_root = actionlint.call_args_list[1].kwargs["working_directory"]
        self.assertTrue(copied_files)
        self.assertTrue(copied_root.name.startswith("repo-scaffold-actionlint-"))
        self.assertTrue(
            all(
                path.parent.name == "workflows" and path.parent.parent.name == ".github"
                for path in copied_files
            )
        )
        self.assertTrue(
            all(str(path).startswith(str(copied_root)) for path in copied_files)
        )

    def test_main_requires_both_installed_and_asset_workflow_groups(self) -> None:
        repository_root = WORKFLOW_SCRIPT_PATH.resolve().parents[1]
        installed = repository_root / ".github" / "workflows" / "ci.yml"
        asset = (
            repository_root
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "ci.yml"
        )
        for glob_results in ([installed], [asset]):
            with (
                self.subTest(glob_results=glob_results),
                mock.patch.object(
                    validate_workflows,
                    "resolve_path_executable",
                    side_effect=["actionlint", "shellcheck"],
                ),
                mock.patch.object(
                    validate_workflows.Path,
                    "glob",
                    side_effect=[glob_results, []]
                    if glob_results == [installed]
                    else [[], glob_results],
                ),
                mock.patch.object(validate_workflows, "run_actionlint") as actionlint,
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(validate_workflows.main(), 2)
            actionlint.assert_not_called()

    def test_script_entrypoint_returns_main_status(self) -> None:
        stderr = mock.Mock()
        with (
            mock.patch.dict(os.environ, {"PATH": ""}),
            mock.patch.object(sys, "stderr", stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(WORKFLOW_SCRIPT_PATH), run_name="__main__")

        self.assertEqual(raised.exception.code, 2)


class CommunityHealthTrackingValidationTests(unittest.TestCase):
    def copy_contract(self, root: Path) -> None:
        relative_paths = (
            ".github/community-health-trackers.json",
            ".github/workflows/community-health.yml",
            "skills/repo-scaffold/assets/community-health-trackers.json",
            "skills/repo-scaffold/assets/workflows/community-health.yml",
            "skills/repo-scaffold/scripts/check_community_health.py",
        )
        for relative in relative_paths:
            source = PLUGIN_ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    def test_current_community_health_tracking_contract_is_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_community_health_tracking_contract(
                PLUGIN_ROOT
            ),
            [],
        )

    def test_missing_tracking_contract_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = validate_repository.validate_community_health_tracking_contract(
                Path(directory)
            )
        self.assertEqual(len(problems), 5)
        self.assertTrue(
            any("check_community_health.py" in problem for problem in problems)
        )
        self.assertTrue(any("registry" in problem for problem in problems))
        self.assertTrue(any("workflow" in problem for problem in problems))

    def test_registry_drift_and_incomplete_inventory_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            installed = root / ".github" / "community-health-trackers.json"
            installed.write_text(
                json.dumps(
                    {
                        "schema-version": 2,
                        "files": [None, {"id": 3}, {"id": "readme"}],
                    }
                ),
                encoding="utf-8",
            )
            problems = validate_repository.validate_community_health_tracking_contract(
                root
            )
        self.assertTrue(any("must match" in problem for problem in problems))
        self.assertTrue(
            any("every supported surface" in problem for problem in problems)
        )

    def test_nonmapping_registry_and_workflow_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            for relative in (
                ".github/community-health-trackers.json",
                "skills/repo-scaffold/assets/community-health-trackers.json",
            ):
                (root / relative).write_text("[]\n", encoding="utf-8")
            (root / ".github/workflows/community-health.yml").write_text(
                "- workflow\n", encoding="utf-8"
            )
            (
                root / "skills/repo-scaffold/assets/workflows/community-health.yml"
            ).write_text("name: first\nname: duplicate\n", encoding="utf-8")
            problems = validate_repository.validate_community_health_tracking_contract(
                root
            )
        self.assertTrue(
            any("every supported surface" in problem for problem in problems)
        )
        self.assertTrue(
            any("workflow must be a mapping" in problem for problem in problems)
        )
        self.assertTrue(
            any("could not verify upstream" in problem for problem in problems)
        )

    def test_workflow_contract_regressions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            installed = root / ".github/workflows/community-health.yml"
            workflow = validate_repository.load_yaml(installed)
            workflow["on"] = {"push": ""}
            workflow["permissions"] = {"contents": "write"}
            workflow["concurrency"] = {"cancel-in-progress": "true"}
            workflow["jobs"] = {"upstream-drift": {"name": "wrong"}}
            installed.write_text(
                yaml.safe_dump(workflow, sort_keys=False)
                .replace(
                    "skills/repo-scaffold/scripts/check_community_health.py",
                    "missing.py",
                )
                .replace("repo-scaffold-community-health-drift", "missing-marker")
                .replace("--body-file", "--body"),
                encoding="utf-8",
            )
            problems = validate_repository.validate_community_health_tracking_contract(
                root
            )
        expected = (
            "must match its scaffold asset",
            "use only schedule",
            "permissions must be",
            "must not cancel",
            "job contract is invalid",
            "reconcile one marker issue",
        )
        for fragment in expected:
            self.assertTrue(any(fragment in problem for problem in problems), fragment)


class FreshnessTrackingContractTests(unittest.TestCase):
    def copy_contract(self, root: Path) -> None:
        relative_paths = (
            ".github/freshness-trackers.json",
            ".github/workflows/freshness.yml",
            "scripts/audit_freshness.py",
            "scripts/sync_action_pins.py",
            "skills/repo-scaffold/assets/freshness-trackers.json",
            "skills/repo-scaffold/assets/workflows/freshness.yml",
            "skills/repo-scaffold/scripts/audit_freshness.py",
            "skills/repo-scaffold/scripts/sync_action_pins.py",
        )
        for relative in relative_paths:
            source = PLUGIN_ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    def test_current_freshness_tracking_contract_is_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_freshness_tracking_contract(PLUGIN_ROOT),
            [],
        )

    def test_missing_freshness_tracking_contract_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = validate_repository.validate_freshness_tracking_contract(
                Path(directory)
            )
        self.assertTrue(any("freshness" in problem for problem in problems))

    def test_freshness_contract_drift_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "scripts/audit_freshness.py").write_text(
                "checker-drift\n", encoding="utf-8"
            )
            (root / "skills/repo-scaffold/scripts/audit_freshness.py").write_text(
                "drift\n", encoding="utf-8"
            )
            (root / "skills/repo-scaffold/assets/freshness-trackers.json").write_text(
                "{}\n", encoding="utf-8"
            )
            (root / ".github/freshness-trackers.json").write_text(
                "[]\n", encoding="utf-8"
            )
            workflow = root / ".github/workflows/freshness.yml"
            workflow.write_text(
                "name: stale\n"
                "on:\n"
                "  push:\n"
                "permissions:\n"
                "  contents: read\n"
                "  issues: none\n"
                "jobs: {}\n",
                encoding="utf-8",
            )
            (root / "skills/repo-scaffold/assets/workflows/freshness.yml").write_text(
                "- workflow\n", encoding="utf-8"
            )
            problems = validate_repository.validate_freshness_tracking_contract(root)
            self.copy_contract(root)
            asset_workflow = (
                root / "skills/repo-scaffold/assets/workflows/freshness.yml"
            )
            asset_workflow.write_text(
                asset_workflow.read_text(encoding="utf-8") + "# drift\n",
                encoding="utf-8",
            )
            workflow_drift = validate_repository.validate_freshness_tracking_contract(
                root
            )
        for fragment in (
            "must match its scaffold copy",
            "must load an explicit tracker registry",
            "must track its shipped inputs",
            "must use schema-version 1",
            "use only schedule",
            "must use contents",
            "reconcile one marker issue",
            "workflow must be a mapping",
        ):
            self.assertTrue(any(fragment in problem for problem in problems), fragment)
        self.assertTrue(
            any("workflow must match" in problem for problem in workflow_drift)
        )


if __name__ == "__main__":
    unittest.main()
