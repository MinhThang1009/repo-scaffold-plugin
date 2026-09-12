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
from typing import Any
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

    def test_parser_helpers_convert_recursive_input_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            yaml_path = root / "recursive.yml"
            json_path = root / "recursive.json"
            yaml_path.write_text("name: value\n", encoding="utf-8")
            json_path.write_text("{}", encoding="utf-8")

            with mock.patch.object(
                validate_repository.yaml,
                "load",
                side_effect=RecursionError("too deep"),
            ):
                with self.assertRaisesRegex(yaml.YAMLError, "nesting exceeds"):
                    validate_repository.load_yaml(yaml_path)
            with mock.patch.object(
                validate_repository.json,
                "loads",
                side_effect=RecursionError("too deep"),
            ):
                with self.assertRaisesRegex(ValueError, "nesting exceeds"):
                    validate_repository.load_json(json_path)

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

    def test_mutable_job_and_service_container_images_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            (workflow_root / "containers.yml").write_text(
                "permissions: {}\n"
                "jobs:\n"
                "  build:\n"
                "    container: alpine:latest\n"
                "    services:\n"
                "      database:\n"
                "        image: postgres:latest\n"
                "    steps: []\n",
                encoding="utf-8",
            )
            (workflow_root / "pinned.yml").write_text(
                "permissions: {}\n"
                "jobs:\n"
                "  build:\n"
                "    container: alpine@sha256:" + "a" * 64 + "\n"
                "    services:\n"
                "      database:\n"
                "        image: postgres@sha256:" + "b" * 64 + "\n"
                "    steps: []\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_action_references(root)

        self.assertEqual(len(problems), 2)
        self.assertTrue(any("job 'build' container image" in item for item in problems))
        self.assertTrue(any("service 'database' image" in item for item in problems))

    def test_invalid_job_container_shapes_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            (workflow_root / "invalid-containers.yml").write_text(
                "permissions: {}\n"
                "jobs:\n"
                "  missing-image:\n"
                "    container: {options: --init}\n"
                "  invalid-services:\n"
                "    services: []\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_action_references(root)

        self.assertEqual(len(problems), 2)
        self.assertTrue(any("missing-image" in item for item in problems))
        self.assertTrue(any("services must be a mapping" in item for item in problems))

    def test_missing_and_broad_workflow_permissions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            for preset in ("read-all", "write-all"):
                (workflow_root / f"{preset}.yml").write_text(
                    f"permissions: {preset}\njobs: {{}}\n", encoding="utf-8"
                )
            (workflow_root / "missing.yml").write_text("jobs: {}\n", encoding="utf-8")
            (workflow_root / "scalar.yml").write_text("[]\n", encoding="utf-8")

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

    def test_workflow_aliases_are_walked_once(self) -> None:
        digest = "a" * 64
        shared = {"uses": f"docker://example/image@sha256:{digest}"}
        value: dict[str, Any] = {"base": shared}
        for index in range(20):
            value = {
                "left": value,
                "right": value,
            }

        self.assertEqual(
            list(validate_repository.iter_uses_values(value)),
            [f"docker://example/image@sha256:{digest}"],
        )

    def test_linked_workflow_boundaries_are_reported_without_dereferencing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            workflow = workflow_root / "ci.yml"
            workflow.write_text("permissions: {}\njobs: {}\n", encoding="utf-8")
            (workflow_root / "ignored.yml").mkdir()

            self.assertTrue(
                validate_repository.path_has_link_or_reparse(root.parent, root)
            )
            self.assertFalse(
                validate_repository.path_has_link_or_reparse(workflow, root)
            )
            self.assertFalse(
                validate_repository.path_has_link_or_reparse(root / "missing", root)
            )
            with mock.patch.object(
                validate_repository, "is_link_or_reparse", return_value=True
            ):
                self.assertTrue(
                    validate_repository.path_has_link_or_reparse(workflow, root)
                )
            self.assertEqual(validate_repository.validate_action_references(root), [])

            with mock.patch.object(
                validate_repository,
                "path_has_link_or_reparse",
                side_effect=lambda path, _repository_root: path == workflow_root,
            ):
                self.assertEqual(
                    validate_repository.validate_action_references(root),
                    [
                        ".github/workflows: workflow directory is linked or a "
                        "reparse point"
                    ],
                )
            with mock.patch.object(
                validate_repository,
                "path_has_link_or_reparse",
                side_effect=lambda path, _repository_root: path == workflow,
            ):
                self.assertEqual(
                    validate_repository.validate_action_references(root),
                    [
                        ".github/workflows/ci.yml: workflow file is linked or a "
                        "reparse point"
                    ],
                )


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
            any(
                "versioned-input sync: script is unreadable" in problem
                for problem in problems
            )
        )
        self.assertTrue(
            any("allowlisted GitHub API" in problem for problem in problems)
        )
        self.assertTrue(
            any("must run weekly and manually" in problem for problem in problems)
        )
        self.assertTrue(
            any(
                "workflow permissions must be contents: read" in problem
                for problem in problems
            )
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

    def test_tampered_synchronizer_contract_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "scripts" / "sync_action_pins.py"
            versioned_inputs_script = root / "scripts" / "sync_versioned_inputs.py"
            workflow = root / ".github" / "workflows" / "action-pin-sync.yml"
            script.parent.mkdir(parents=True)
            workflow.parent.mkdir(parents=True)
            shutil.copy2(PLUGIN_ROOT / "scripts" / "sync_action_pins.py", script)
            shutil.copy2(
                PLUGIN_ROOT / "scripts" / "sync_versioned_inputs.py",
                versioned_inputs_script,
            )
            original = (
                PLUGIN_ROOT / ".github" / "workflows" / "action-pin-sync.yml"
            ).read_text(encoding="utf-8")

            workflow.write_text(
                original.replace(
                    "python scripts/sync_versioned_inputs.py --write", "echo bypass", 1
                ),
                encoding="utf-8",
            )
            invalid_sync = validate_repository.validate_action_pin_sync_contract(root)

            workflow.write_text(
                original.replace(
                    "branch: chore/synchronize-versioned-inputs", "branch: unsafe", 1
                ),
                encoding="utf-8",
            )
            invalid_pr = validate_repository.validate_action_pin_sync_contract(root)

            workflow.write_text(
                original.replace(
                    "token: ${{ secrets.VERSION_SYNC_TOKEN }}",
                    "token: ${{ secrets.RELEASE_PLEASE_TOKEN }}",
                    1,
                ),
                encoding="utf-8",
            )
            invalid_token = validate_repository.validate_action_pin_sync_contract(root)

            workflow.write_text(
                original.replace("contents: read", "contents: write", 1),
                encoding="utf-8",
            )
            invalid_workflow_permissions = (
                validate_repository.validate_action_pin_sync_contract(root)
            )

            workflow.write_text(
                original.replace(
                    "permissions:\n      contents: read",
                    "permissions:\n      contents: write",
                    1,
                ),
                encoding="utf-8",
            )
            invalid_job_permissions = (
                validate_repository.validate_action_pin_sync_contract(root)
            )

            workflow.write_text(
                original.replace("Verify version-sync token", "Bypass token check", 1),
                encoding="utf-8",
            )
            invalid_token_guard = validate_repository.validate_action_pin_sync_contract(
                root
            )

        self.assertTrue(
            any(
                "only through the reviewed script" in problem
                for problem in invalid_sync
            )
        )
        self.assertTrue(
            any("token-backed draft pull request" in problem for problem in invalid_pr)
        )
        self.assertTrue(
            any(
                "token-backed draft pull request" in problem
                for problem in invalid_token
            )
        )
        self.assertTrue(
            any(
                "workflow permissions must be contents: read" in problem
                for problem in invalid_workflow_permissions
            )
        )
        self.assertTrue(
            any(
                "job permissions must remain contents: read" in problem
                for problem in invalid_job_permissions
            )
        )
        self.assertTrue(
            any(
                "must fail clearly when VERSION_SYNC_TOKEN is absent" in problem
                for problem in invalid_token_guard
            )
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
        "requirements-mutation.txt",
        "scripts/pr_template_preflight.py",
        "skills/repo-scaffold/scripts/pr_template_preflight.py",
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

    def test_mutation_lock_dependency_version_drift_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            ruff_version = self.direct_version(root, "ruff")
            mutation_lock_path = root / "requirements-mutation.txt"
            mutation_lock_path.write_text(
                mutation_lock_path.read_text(encoding="utf-8").replace(
                    f"ruff=={ruff_version}", "ruff==0.0.0", 1
                ),
                encoding="utf-8",
            )

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertIn(
                "requirements-mutation.txt: ruff pin 0.0.0 does not match "
                f"requirements-dev.in pin {ruff_version}",
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
            mutation_lock_path = root / "requirements-mutation.txt"
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
            mutation_lock_path.write_text(
                mutation_lock_path.read_text(encoding="utf-8").replace(
                    f"coverage=={current_version}",
                    f"coverage=={replacement_version}",
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
                "          scripts/pr_template_preflight.py\n", "", 1
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

        self.assertIn(
            ".github/workflows/ci.yml: Mypy must check every production script and tests",
            problems,
        )

    def test_ci_mypy_reports_unreadable_production_script_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            scripts = root / "scripts"
            original_glob = Path.glob

            def glob(path: Path, pattern: str) -> object:
                if path == scripts:
                    raise OSError("access denied")
                return original_glob(path, pattern)

            with mock.patch.object(Path, "glob", autospec=True, side_effect=glob):
                problems = validate_repository.validate_development_dependency_contract(
                    root
                )

        self.assertIn(
            "scripts: could not inventory production scripts for Mypy: access denied",
            problems,
        )

    def test_development_guidance_must_cover_the_complete_ci_mypy_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            contributing_path = root / "CONTRIBUTING.md"
            contributing = contributing_path.read_text(encoding="utf-8").replace(
                "scripts/audit_official_docs.py ", "", 1
            )
            contributing_path.write_text(contributing, encoding="utf-8")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

        self.assertIn(
            "CONTRIBUTING.md: development guidance must run the complete CI Mypy command",
            problems,
        )

    def test_development_guidance_mypy_contract_rejects_unreadable_document(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "README.md").write_bytes(b"\xff")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

        self.assertIn(
            "README.md: could not verify Mypy development guidance:",
            "\n".join(problems),
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

    def test_missing_mutation_lock_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "requirements-mutation.txt").unlink()

            self.assertTrue(
                validate_repository.validate_development_dependency_contract(root)[
                    0
                ].startswith(
                    "requirements-mutation.txt: could not verify inherited development pins"
                )
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
        "tests/test_branch_protection_preflight.py",
        "tests/test_advanced_codeql_preflight.py",
        "tests/test_ci_toolchain.py",
        "tests/test_codeql_preflight.py",
        "tests/test_dependency_review_preflight.py",
        "tests/test_scorecard_preflight.py",
        "tests/test_merge_settings_preflight.py",
        "tests/test_release_preflight.py",
        "tests/test_repository_settings_preflight.py",
        "tests/test_security_features_preflight.py",
        "tests/test_workflow_installation_preflight.py",
        "tests/test_validate_mutation_results.py",
        "tests/test_prepare_mutation_cache.py",
        "tests/test_run_mutation_testing.py",
        "tests/test_sync_action_pins.py",
        "tests/test_mutation_runner_linux.py",
        "tests/test_python_support.py",
        "tests/test_repository_validation.py",
        "tests/test_validate_scaffold.py",
    )

    def test_sharded_workflow_validator_rejects_every_incomplete_shape(self) -> None:
        valid: dict[str, Any] = {
            "jobs": {
                "mutation-plan": {
                    "steps": [
                        {
                            "run": "python scripts/run_mutation_testing.py --max-children 4 --plan-shards 32"
                        }
                    ]
                },
                "mutation-shards": {
                    "strategy": {
                        "matrix": {"shard": [str(index) for index in range(32)]}
                    },
                    "steps": [
                        {
                            "run": 'python scripts/run_mutation_testing.py --max-children 4 --shard-index "$SHARD_INDEX"'
                        }
                    ],
                },
                "mutation-quality": {
                    "steps": [{"run": "python scripts/merge_mutation_shards.py"}]
                },
            }
        }
        cases: tuple[tuple[object, str], ...] = (
            (None, "invalid workflow"),
            ({"jobs": {}}, "require plan, shard, and aggregate"),
            (
                {"jobs": {**valid["jobs"], "mutation-shards": []}},
                "mutation jobs must map",
            ),
            (
                {
                    "jobs": {
                        **valid["jobs"],
                        "mutation-shards": {
                            **valid["jobs"]["mutation-shards"],
                            "strategy": {"matrix": {"shard": []}},
                        },
                    }
                },
                "run all 32 exact mutation shards",
            ),
            (
                {
                    "jobs": {
                        **valid["jobs"],
                        "mutation-quality": {"steps": []},
                    }
                },
                "plan, execute, and merge",
            ),
        )
        for workflow, message in cases:
            with self.subTest(message=message):
                self.assertIn(
                    message,
                    "\n".join(
                        validate_repository.validate_sharded_mutation_workflow(workflow)
                    ),
                )
        self.assertEqual(
            validate_repository.validate_sharded_mutation_workflow(valid), []
        )

    def copy_contract(self, root: Path) -> None:
        for relative in self.CONTRACT_FILES:
            source = PLUGIN_ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        shutil.copy2(
            PLUGIN_ROOT / "tests" / "fixtures" / "mutation-testing-legacy.yml",
            root / ".github" / "workflows" / "mutation-testing.yml",
        )

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

    def test_mutation_schedule_must_continue_daily(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow_path = root / ".github" / "workflows" / "mutation-testing.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                '    - cron: "41 5 * * *"', '    - cron: "41 5 1 * *"', 1
            )
            workflow_path.write_text(workflow, encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertIn(
            ".github/workflows/mutation-testing.yml: schedule daily continuation "
            "of interrupted mutation runs",
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
                "            mutmut-v6-${{ runner.os }}-${{ runner.arch }}-python-"
                "${{ steps.python.outputs.python-version }}-incremental-${{ github.sha }}-"
                "${{ github.run_id }}-${{ github.run_attempt }}\n"
                "          restore-keys: |\n"
                "            mutmut-v6-${{ runner.os }}-${{ runner.arch }}-python-"
                "${{ steps.python.outputs.python-version }}-incremental-${{ github.sha }}-\n"
                "            mutmut-v6-${{ runner.os }}-${{ runner.arch }}-python-"
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
            "platform-scoped v6 keys",
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
            "v6 keys",
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
                    '  "AGENTS.md",',
                    "",
                    1,
                )
                .replace(
                    '  ".agents",',
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
                "workspace must copy the plugin marketplace, maintainer "
                "instructions, and both mutation requirement files" in p
                for p in problems
            )
        )
        self.assertTrue(
            any("pytest must collect only first-party tests" in p for p in problems)
        )

    def test_mutation_workspace_copies_required_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            config_path = root / "pyproject.toml"
            original_config = config_path.read_text(encoding="utf-8")
            for copied_path in (".agents", ".claude", "AGENTS.md"):
                with self.subTest(copied_path=copied_path):
                    config_path.write_text(
                        original_config.replace(f'  "{copied_path}",\n', "", 1),
                        encoding="utf-8",
                    )

                    problems = validate_repository.validate_mutation_testing_contract(
                        root
                    )

                    self.assertTrue(
                        any(
                            "workspace must copy the plugin marketplace" in problem
                            for problem in problems
                        )
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
                "branch_protection_preflight.py",
                "advanced_codeql_preflight.py",
                "codeql_preflight.py",
                "dependency_review_preflight.py",
                "scorecard_preflight.py",
                "merge_settings_preflight.py",
                "release_preflight.py",
                "repository_settings_preflight.py",
                "security_features_preflight.py",
                "workflow_installation_preflight.py",
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
            "validate_policy_drift_reminder_contract",
            "validate_mirrored_dependency_metadata",
            "validate_development_dependency_contract",
            "validate_mutation_testing_contract",
            "validate_plugin_manifest",
            "validate_skill_reference_paths",
            "validate_multi_agent_plugin_contract",
            "validate_maintainer_agent_instructions",
            "validate_release_please",
            "validate_release_attestation",
            "validate_privileged_workflow_permissions",
            "validate_scorecard_manual_dispatch",
            "validate_action_pin_sync_contract",
            "validate_required_check_concurrency",
            "validate_issue_templates",
            "validate_release_notes_config",
            "validate_dependabot",
            "validate_markdown_links",
            "validate_pr_template_catalog_documentation",
            "validate_community_health_tracking_contract",
            "validate_freshness_tracking_contract",
            "validate_official_docs_tracking_contract",
            "validate_code_scanning_gate_contract",
            "validate_workflow_script_copy_contract",
            "validate_pr_template_preflight_contract",
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
        (root / "AGENTS.md").write_text(
            "## Repository purpose\n"
            "python -m pytest -q\n"
            "python scripts/validate_repository.py\n"
            "python skills/repo-scaffold/scripts/validate_scaffold.py\n"
            "claude plugin validate --strict .\n"
            "scripts/pr_template_preflight.py\n",
            encoding="utf-8",
        )
        claude_instructions = root / ".claude" / "CLAUDE.md"
        claude_instructions.parent.mkdir()
        claude_instructions.write_text(
            validate_repository.CLAUDE_SHARED_INSTRUCTIONS,
            encoding="utf-8",
        )
        shared = {
            "name": "repo-scaffold",
            "version": "1.2.3",
            "description": (
                "Agent Skills plugin for Codex and Claude Code that scaffolds "
                "repositories to production GitHub.com standards."
            ),
            "author": {"name": "Maintainer"},
            "homepage": "https://example.test/readme",
            "repository": "https://example.test",
            "license": "MIT",
            "keywords": ["agent-skills"],
        }
        codex_root = root / ".codex-plugin"
        codex_root.mkdir()
        (codex_root / "plugin.json").write_text(json.dumps(shared), encoding="utf-8")
        codex_marketplace_root = root / ".agents" / "plugins"
        codex_marketplace_root.mkdir(parents=True)
        codex_marketplace = {
            "name": validate_repository.CODEX_MARKETPLACE_NAME,
            "interface": {"displayName": "Repo Scaffold plugins"},
            "plugins": [
                {
                    "name": "repo-scaffold",
                    "source": {"source": "local", "path": "./"},
                    "policy": {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                    },
                    "category": "Productivity",
                }
            ],
        }
        (codex_marketplace_root / "marketplace.json").write_text(
            json.dumps(codex_marketplace), encoding="utf-8"
        )
        claude_root = root / ".claude-plugin"
        claude_root.mkdir()
        claude = {
            "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
            "displayName": "Repo Scaffold",
            **shared,
        }
        (claude_root / "plugin.json").write_text(json.dumps(claude), encoding="utf-8")
        marketplace = {
            "$schema": "https://json.schemastore.org/claude-code-marketplace.json",
            "name": validate_repository.CLAUDE_MARKETPLACE_NAME,
            "owner": validate_repository.CLAUDE_MARKETPLACE_OWNER,
            "description": "Repo Scaffold plugins for Claude Code.",
            "plugins": [
                {
                    "name": "repo-scaffold",
                    "source": "./",
                    "description": shared["description"],
                }
            ],
        }
        (claude_root / "marketplace.json").write_text(
            json.dumps(marketplace), encoding="utf-8"
        )
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
            "https://code.claude.com/docs/en/plugin-marketplaces\n"
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
            self.assertEqual(
                validate_repository.validate_maintainer_agent_instructions(root), []
            )

    def test_rejects_missing_or_drifted_maintainer_agent_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)
            (root / "AGENTS.md").write_text("## Repository purpose\n", encoding="utf-8")
            (root / ".claude" / "CLAUDE.md").write_text(
                "Extra instructions\n", encoding="utf-8"
            )

            problems = validate_repository.validate_maintainer_agent_instructions(root)

        self.assertIn(
            "AGENTS.md: missing maintainer instruction 'python -m pytest -q'",
            problems,
        )
        self.assertIn(
            ".claude/CLAUDE.md: must contain only @AGENTS.md so Codex and Claude Code "
            "maintainers share one instruction source",
            problems,
        )

    def test_reports_unreadable_maintainer_agent_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)
            agents_path = root / "AGENTS.md"
            claude_path = root / ".claude" / "CLAUDE.md"
            agents_path.unlink()
            agents_path.mkdir()
            claude_path.unlink()
            claude_path.mkdir()

            problems = validate_repository.validate_maintainer_agent_instructions(root)

        self.assertTrue(
            any(problem.startswith("AGENTS.md: unreadable:") for problem in problems)
        )
        self.assertTrue(
            any(
                problem.startswith(".claude/CLAUDE.md: unreadable:")
                for problem in problems
            )
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
            "HEAD -- .agents .claude-plugin .codex-plugin skills README.md LICENSE",
        ):
            self.assertIn(fragment, workflow)

    def test_public_submission_dossier_covers_claude_code_distribution(self) -> None:
        dossier = (PLUGIN_ROOT / "PLUGIN_SUBMISSION.md").read_text(encoding="utf-8")

        for fragment in (
            ".agents/plugins/marketplace.json",
            ".codex-plugin",
            ".claude-plugin",
            "claude-community",
            "separately curated Anthropic marketplace",
            "in-app forms",
            "Skills only",
            "Apps Management write access",
            "identity verification",
            "claude plugin validate --strict .",
            "claude --plugin-dir",
        ):
            self.assertIn(fragment, dossier)

    def test_claude_submission_guidance_uses_community_marketplace(self) -> None:
        documents = {
            PLUGIN_ROOT
            / "README.md": "`claude-community` marketplace through its in-app forms.",
            PLUGIN_ROOT
            / "PLUGIN_SUBMISSION.md": "`claude-community` marketplace through one of its current in-app forms",
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "references"
            / "agent-compatibility.md": "`claude-community` marketplace through one of its current in-app forms.",
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "references"
            / "agent-compatibility.vi.md": "`claude-community` của Anthropic qua một trong các form trong app hiện hành.",
        }

        for path, expected in documents.items():
            text = path.read_text(encoding="utf-8")

            self.assertIn(expected, text, path.name)
            self.assertIn("claude-plugins-official", text, path.name)

    def test_readme_uninstalls_from_the_documented_marketplace(self) -> None:
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("codex plugin remove repo-scaffold@repo-scaffold-plugins", readme)
        self.assertIn(
            "claude plugin uninstall repo-scaffold@repo-scaffold-plugins", readme
        )
        self.assertNotIn("codex plugin remove repo-scaffold@personal", readme)

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

    def test_rejects_missing_or_drifted_claude_marketplace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)
            marketplace_path = root / validate_repository.CLAUDE_MARKETPLACE_PATH
            marketplace_path.unlink()

            missing = validate_repository.validate_multi_agent_plugin_contract(root)

            self.assertTrue(
                any(
                    problem.startswith(".claude-plugin/marketplace.json: invalid JSON:")
                    for problem in missing
                )
            )

            marketplace_path.write_text("[]", encoding="utf-8")
            non_object = validate_repository.validate_multi_agent_plugin_contract(root)

            self.assertIn(
                ".claude-plugin/marketplace.json: root must be an object",
                non_object,
            )

            marketplace_path.write_text(
                json.dumps(
                    {
                        "name": "wrong",
                        "owner": {},
                        "plugins": [{"name": "repo-scaffold", "source": "../"}],
                    }
                ),
                encoding="utf-8",
            )
            drifted = validate_repository.validate_multi_agent_plugin_contract(root)

        self.assertIn(
            ".claude-plugin/marketplace.json: name must be repo-scaffold-plugins",
            drifted,
        )
        self.assertIn(
            ".claude-plugin/marketplace.json: owner must identify the plugin "
            "maintainer",
            drifted,
        )
        self.assertIn(
            ".claude-plugin/marketplace.json: plugins must expose only the root "
            "repo-scaffold plugin through source ./",
            drifted,
        )

    def test_rejects_missing_or_drifted_codex_marketplace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_contract(root)
            marketplace_path = root / validate_repository.CODEX_MARKETPLACE_PATH
            marketplace_path.unlink()

            missing = validate_repository.validate_multi_agent_plugin_contract(root)

            self.assertTrue(
                any(
                    problem.startswith(
                        ".agents/plugins/marketplace.json: invalid JSON:"
                    )
                    for problem in missing
                )
            )

            marketplace_path.write_text("[]", encoding="utf-8")
            non_object = validate_repository.validate_multi_agent_plugin_contract(root)

            self.assertIn(
                ".agents/plugins/marketplace.json: root must be an object",
                non_object,
            )

            marketplace_path.write_text(
                json.dumps({"name": "wrong", "interface": {}, "plugins": {}}),
                encoding="utf-8",
            )
            drifted = validate_repository.validate_multi_agent_plugin_contract(root)

        self.assertIn(
            ".agents/plugins/marketplace.json: name must be repo-scaffold-plugins",
            drifted,
        )
        self.assertIn(
            ".agents/plugins/marketplace.json: interface must expose the Repo "
            "Scaffold marketplace display name",
            drifted,
        )
        self.assertIn(
            ".agents/plugins/marketplace.json: plugins must expose only the root "
            "repo-scaffold plugin",
            drifted,
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
            (root / ".github" / "workflows" / "release-tag.yaml").write_text(
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
                ".github/workflows/release-tag.yaml: must not coexist with "
                "Release Please",
                problems,
            )
            self.assertIn(
                ".github/workflows/release-tag.yaml: tag push trigger conflicts "
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

    def test_rejects_codeql_trigger_that_omits_edited_pull_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            asset_root = root / "skills" / "repo-scaffold" / "assets" / "workflows"
            for relative in (
                Path(".github/workflows/release-please.yml"),
                Path("skills/repo-scaffold/assets/workflows/release-please.yml"),
                Path(".github/workflows/codeql.yml"),
                Path("skills/repo-scaffold/assets/workflows/codeql.yml"),
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PLUGIN_ROOT / relative, destination)
            for path in (workflow_root / "codeql.yml", asset_root / "codeql.yml"):
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        "types: [opened, edited, reopened, synchronize]",
                        "types: [opened, reopened, synchronize]",
                    ),
                    encoding="utf-8",
                )

            problems = validate_repository.validate_privileged_workflow_permissions(
                root
            )

            self.assertEqual(
                sum(
                    "CodeQL pull_request trigger must include edited" in item
                    for item in problems
                ),
                2,
            )

    def test_rejects_codeql_trigger_that_omits_merge_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            asset_root = root / "skills" / "repo-scaffold" / "assets" / "workflows"
            for relative in (
                Path(".github/workflows/release-please.yml"),
                Path("skills/repo-scaffold/assets/workflows/release-please.yml"),
                Path(".github/workflows/codeql.yml"),
                Path("skills/repo-scaffold/assets/workflows/codeql.yml"),
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PLUGIN_ROOT / relative, destination)
            for path in (workflow_root / "codeql.yml", asset_root / "codeql.yml"):
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        "  merge_group:\n    types: [checks_requested]\n", ""
                    ),
                    encoding="utf-8",
                )

            problems = validate_repository.validate_privileged_workflow_permissions(
                root
            )

            self.assertEqual(
                sum(
                    "CodeQL merge_group trigger must request checks" in item
                    for item in problems
                ),
                2,
            )

    def test_rejects_codeql_trigger_that_omits_manual_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            asset_root = root / "skills" / "repo-scaffold" / "assets" / "workflows"
            for relative in (
                Path(".github/workflows/release-please.yml"),
                Path("skills/repo-scaffold/assets/workflows/release-please.yml"),
                Path(".github/workflows/codeql.yml"),
                Path("skills/repo-scaffold/assets/workflows/codeql.yml"),
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PLUGIN_ROOT / relative, destination)
            for path in (workflow_root / "codeql.yml", asset_root / "codeql.yml"):
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        "  workflow_dispatch:\n", "", 1
                    ),
                    encoding="utf-8",
                )

            problems = validate_repository.validate_privileged_workflow_permissions(
                root
            )

            self.assertEqual(
                sum(
                    "CodeQL must support manual security scans" in item
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


class SecurityManualDispatchTests(unittest.TestCase):
    def test_scorecard_workflows_support_manual_security_scans(self) -> None:
        self.assertEqual(
            validate_repository.validate_scorecard_manual_dispatch(PLUGIN_ROOT), []
        )

    def test_scorecard_workflows_reject_missing_manual_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = (
                Path(".github/workflows/scorecard.yml"),
                Path("skills/repo-scaffold/assets/workflows/scorecard.yml"),
            )
            for relative in paths:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (PLUGIN_ROOT / relative)
                    .read_text(encoding="utf-8")
                    .replace("  workflow_dispatch:\n", "", 1),
                    encoding="utf-8",
                )

            problems = validate_repository.validate_scorecard_manual_dispatch(root)

            self.assertEqual(
                sum(
                    "Scorecard must support manual security scans" in item
                    for item in problems
                ),
                2,
            )

    def test_scorecard_workflows_report_unreadable_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / ".github" / "workflows"
            asset_root = root / "skills" / "repo-scaffold" / "assets" / "workflows"
            workflow_root.mkdir(parents=True)
            asset_root.mkdir(parents=True)
            (workflow_root / "scorecard.yml").write_text("on: [\n", encoding="utf-8")
            (asset_root / "scorecard.yml").write_text(
                "on:\n  workflow_dispatch:\n", encoding="utf-8"
            )

            problems = validate_repository.validate_scorecard_manual_dispatch(root)

            self.assertEqual(len(problems), 1)
            self.assertIn("Scorecard workflow is unreadable", problems[0])


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

    def test_required_check_contract_rejects_missing_merge_group_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                Path(".github/workflows/ci.yml"),
                Path(".github/workflows/commitlint.yml"),
                Path(".github/workflows/dependency-review.yml"),
                Path(".github/workflows/pr-template.yml"),
                Path("skills/repo-scaffold/assets/workflows/ci.yml"),
                Path("skills/repo-scaffold/assets/workflows/commitlint.yml"),
                Path("skills/repo-scaffold/assets/workflows/dependency-review.yml"),
                Path("skills/repo-scaffold/assets/workflows/pr-template.yml"),
                Path("skills/repo-scaffold/assets/workflows/documentation.yml"),
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PLUGIN_ROOT / relative, destination)
            for relative in (
                Path(".github/workflows/pr-template.yml"),
                Path("skills/repo-scaffold/assets/workflows/pr-template.yml"),
            ):
                path = root / relative
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        "  merge_group:\n    types: [checks_requested]\n", ""
                    ),
                    encoding="utf-8",
                )

            problems = validate_repository.validate_required_check_concurrency(root)

            self.assertEqual(
                sum("must run for merge_group" in item for item in problems),
                2,
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

            allowlist.write_text(
                json.dumps(
                    {
                        "schema-version": 3,
                        "allowlist": [],
                        "unreviewed-inputs": ["ignored.json"],
                    }
                ),
                encoding="utf-8",
            )
            unknown_fields = validate_repository.validate_code_scanning_gate_contract(
                root
            )
            self.assertTrue(
                any("require schema-version" in item for item in unknown_fields)
            )

            allowlist.write_text(
                '{"schema-version": 3, "allowlist": ['
                '{"number": 0, "tool": "CodeQL", "rule": "x", '
                '"path": null, "reason": "x", "reviewed-on": "2026-09-09", '
                '"review-period-days": 90}]}',
                encoding="utf-8",
            )
            invalid_selector = validate_repository.validate_code_scanning_gate_contract(
                root
            )
            self.assertTrue(
                any(
                    "exact positive alert selector" in item for item in invalid_selector
                )
            )

            allowlist.write_text(
                '{"schema-version": 3, "allowlist": ['
                '{"number": true, "tool": "CodeQL", "rule": "x", '
                '"path": null, "reason": "x", "reviewed-on": "2026-09-09", '
                '"review-period-days": 90}]}',
                encoding="utf-8",
            )
            boolean_number = validate_repository.validate_code_scanning_gate_contract(
                root
            )
            self.assertTrue(
                any("exact positive alert selector" in item for item in boolean_number)
            )

            for invalid_path in (
                "",
                "../escape",
                "C:/example.py",
                "scripts//example.py",
            ):
                with self.subTest(invalid_path=invalid_path):
                    allowlist.write_text(
                        '{"schema-version": 3, "allowlist": ['
                        '{"number": 1, "tool": "CodeQL", "rule": "x", '
                        f'"path": {json.dumps(invalid_path)}, "reason": "x", '
                        '"reviewed-on": "2026-09-09", "review-period-days": 90}]}',
                        encoding="utf-8",
                    )
                    invalid_path_result = (
                        validate_repository.validate_code_scanning_gate_contract(root)
                    )
                    self.assertTrue(
                        any(
                            "exact positive alert selector" in item
                            for item in invalid_path_result
                        )
                    )

            allowlist.write_text(
                '{"schema-version": 3, "allowlist": ['
                '{"number": 1, "tool": "CodeQL", "rule": "x", '
                '"path": null, "reason": "x", "reviewed-on": "not-a-date", '
                '"review-period-days": 90}]}',
                encoding="utf-8",
            )
            invalid_review_date = (
                validate_repository.validate_code_scanning_gate_contract(root)
            )
            self.assertTrue(
                any("reviewed-on must use ISO" in item for item in invalid_review_date)
            )

            valid_entry = {
                "number": 1,
                "tool": "CodeQL",
                "rule": "py/example",
                "path": None,
                "reason": "Reviewed.",
                "reviewed-on": "2000-01-01",
                "review-period-days": 90,
            }
            allowlist.write_text(
                json.dumps(
                    {
                        "schema-version": 3,
                        "allowlist": [valid_entry, {**valid_entry}],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(validate_repository, "datetime") as clock:
                clock.now.return_value.date.return_value = validate_repository.date(
                    2026, 9, 9
                )
                duplicate_numbers = (
                    validate_repository.validate_code_scanning_gate_contract(root)
                )
                clock.now.assert_called_once_with(validate_repository.timezone.utc)
            self.assertTrue(
                any(
                    "alert numbers must be unique" in item for item in duplicate_numbers
                )
            )

            allowlist.write_text(
                json.dumps(
                    {
                        "schema-version": 3,
                        "allowlist": [
                            {**valid_entry, "number": number}
                            for number in range(
                                1,
                                validate_repository.MAX_CODE_SCANNING_ALLOWLIST_ENTRIES
                                + 2,
                            )
                        ],
                    }
                ),
                encoding="utf-8",
            )
            oversized = validate_repository.validate_code_scanning_gate_contract(root)
            self.assertTrue(
                any(
                    f"exceeds the {validate_repository.MAX_CODE_SCANNING_ALLOWLIST_ENTRIES}-entry limit"
                    in item
                    for item in oversized
                )
            )

            allowlist.write_text(
                json.dumps(
                    {
                        "schema-version": 3,
                        "allowlist": [{**valid_entry, "reviewed-on": "2999-01-01"}],
                    }
                ),
                encoding="utf-8",
            )
            future_review_date = (
                validate_repository.validate_code_scanning_gate_contract(root)
            )
            self.assertTrue(
                any(
                    "reviewed-on cannot be in the future" in item
                    for item in future_review_date
                )
            )

            source_paths = (
                PLUGIN_ROOT / ".github" / "workflows" / "code-scanning-gate.yml",
                PLUGIN_ROOT
                / "skills"
                / "repo-scaffold"
                / "assets"
                / "workflows"
                / "code-scanning-gate.yml",
            )
            for workflow, source_path in zip(workflow_paths, source_paths, strict=True):
                workflow.write_text(
                    source_path.read_text(encoding="utf-8").replace(
                        '--pull-request "$PR_NUMBER"', "--ref invalid"
                    ),
                    encoding="utf-8",
                )
            allowlist.write_text(
                '{"schema-version": 3, "allowlist": []}', encoding="utf-8"
            )
            unsafe = validate_repository.validate_code_scanning_gate_contract(root)
            self.assertTrue(
                any("only base-branch alert-gate code" in item for item in unsafe)
            )

    def test_gate_uses_default_trusted_code_and_polls_for_the_test_merge(self) -> None:
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

        asset_text = asset.read_text(encoding="utf-8")
        document = validate_repository.load_yaml(workflow)
        asset_document = validate_repository.load_yaml(asset)
        self.assertEqual(
            document["on"],
            {
                "pull_request_target": {
                    "types": ["opened", "edited", "reopened", "synchronize"],
                    "branches": ["main"],
                },
                "merge_group": {"types": ["checks_requested"]},
            },
        )
        self.assertEqual(
            asset_document["on"]["pull_request_target"]["branches"],
            ["{{REPO_SCAFFOLD_DEFAULT_BRANCH_GLOB_JSON_ESCAPED}}"],
        )
        self.assertEqual(
            document["permissions"],
            {
                "contents": "read",
                "pull-requests": "read",
                "security-events": "read",
            },
        )
        self.assertNotIn("ref: ${{ github.event.pull_request.base.sha }}", text)
        self.assertIn("ref: ${{ github.event.merge_group.base_sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('--pull-request "$PR_NUMBER"', text)
        self.assertIn('--base-sha "$PR_BASE_SHA"', text)
        self.assertIn('--head-sha "$PR_HEAD_SHA"', text)
        self.assertIn('--ref "$MERGE_GROUP_REF"', text)
        self.assertIn('--sha "$MERGE_GROUP_SHA"', text)
        self.assertIn('--expected-codeql-category "/language:actions"', text)
        self.assertIn('--expected-codeql-category "/language:python"', text)
        self.assertIn(
            '--expected-codeql-category "/language:{{REPO_SCAFFOLD_CODEQL_LANGUAGE}}"',
            asset_text,
        )
        self.assertNotIn("merge_commit_sha", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("github.event_name == 'pull_request_target'", text)
        self.assertIn("github.event_name == 'merge_group'", text)

    def test_validator_rejects_gate_missing_merge_queue_alert_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                Path(".github/code-scanning-allowlist.json"),
                Path(".github/workflows/code-scanning-gate.yml"),
                Path("skills/repo-scaffold/assets/workflows/code-scanning-gate.yml"),
                Path("scripts/check_code_scanning_alerts.py"),
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PLUGIN_ROOT / relative, destination)
            for relative in (
                Path(".github/workflows/code-scanning-gate.yml"),
                Path("skills/repo-scaffold/assets/workflows/code-scanning-gate.yml"),
            ):
                path = root / relative
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        "  merge_group_code_scanning_gate:\n",
                        "  renamed_merge_group_code_scanning_gate:\n",
                    ),
                    encoding="utf-8",
                )

            problems = validate_repository.validate_code_scanning_gate_contract(root)

            self.assertEqual(
                sum(
                    "trusted pull-request gate contract and merge-queue contract"
                    in item
                    for item in problems
                ),
                2,
            )

    def test_validator_rejects_gate_with_untrusted_merge_queue_sha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                Path(".github/code-scanning-allowlist.json"),
                Path(".github/workflows/code-scanning-gate.yml"),
                Path("skills/repo-scaffold/assets/workflows/code-scanning-gate.yml"),
                Path("scripts/check_code_scanning_alerts.py"),
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PLUGIN_ROOT / relative, destination)
            for relative in (
                Path(".github/workflows/code-scanning-gate.yml"),
                Path("skills/repo-scaffold/assets/workflows/code-scanning-gate.yml"),
            ):
                path = root / relative
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        '--sha "$MERGE_GROUP_SHA"', '--sha "$UNTRUSTED_SHA"'
                    ),
                    encoding="utf-8",
                )

            problems = validate_repository.validate_code_scanning_gate_contract(root)

            self.assertEqual(
                sum(
                    "must execute trusted merge-queue alert-gate code" in item
                    for item in problems
                ),
                2,
            )

    def test_validator_rejects_gate_with_an_incorrect_base_branch_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                Path(".github/code-scanning-allowlist.json"),
                Path(".github/workflows/code-scanning-gate.yml"),
                Path("skills/repo-scaffold/assets/workflows/code-scanning-gate.yml"),
                Path("scripts/check_code_scanning_alerts.py"),
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PLUGIN_ROOT / relative, destination)
            for relative in (
                Path(".github/workflows/code-scanning-gate.yml"),
                Path("skills/repo-scaffold/assets/workflows/code-scanning-gate.yml"),
            ):
                path = root / relative
                path.write_text(
                    "\n".join(
                        "    branches: [incorrect-base]"
                        if line.startswith("    branches:")
                        else line
                        for line in path.read_text(encoding="utf-8").splitlines()
                    )
                    + "\n",
                    encoding="utf-8",
                )

            problems = validate_repository.validate_code_scanning_gate_contract(root)

            self.assertEqual(
                sum("trusted pull-request gate contract" in item for item in problems),
                2,
            )


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
                },
                "merge_group": {"types": ["checks_requested"]},
            },
        )
        self.assertEqual(document["permissions"], {"contents": "read"})
        self.assertEqual(document["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(document["jobs"]["pr_template"]["name"], "pr-template")
        self.assertEqual(
            document["jobs"]["merge_group_pr_template"]["name"], "pr-template"
        )
        self.assertEqual(
            document["jobs"]["merge_group_pr_template"]["if"],
            "${{ github.event_name == 'merge_group' }}",
        )

        for fragment in (
            "ref: ${{ github.event.pull_request.base.sha }}",
            "persist-credentials: false",
            "PR_BODY: ${{ github.event.pull_request.body }}",
            "PR_TITLE: ${{ github.event.pull_request.title }}",
            "PR_IS_DRAFT: ${{ github.event.pull_request.draft }}",
            'Path(".github/PULL_REQUEST_TEMPLATE.md")',
            'Path(".github/PULL_REQUEST_TEMPLATE")',
            "repo-scaffold:pr-template=",
            "repo-scaffold:required-checklist:start",
            "repo-scaffold:optional-checklist:start",
            "Pull request body must select exactly one trusted template",
            "Pull request title type",
            "Pull request body must preserve every required heading and checklist",
            "Mark each required checklist item only after it is complete",
            "github.event.pull_request.user.login != 'dependabot[bot]'",
            "github.event_name == 'pull_request_target'",
            "release-please--branches--",
            "github.event.merge_group.head_sha",
            "Pull request template requirements were checked before merge-queue admission.",
        ):
            self.assertIn(fragment, workflow_text)

        template_ids = (
            "default",
            "feature",
            "bugfix",
            "documentation",
            "security",
            "deployment",
            "dependency-update",
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
            self.assertIn("scripts/pr_template_preflight.py", text)
            self.assertIn("--template security", text)
            self.assertIn("--template deployment", text)
            self.assertIn("--template dependency-update", text)
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
        self.assertIn("scripts/pr_template_preflight.py", pull_request_reference)
        self.assertIn("--template security", pull_request_reference)
        self.assertIn("--template deployment", pull_request_reference)
        self.assertIn("--template dependency-update", pull_request_reference)
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

            dependency_update_body = (
                template_root / "PULL_REQUEST_TEMPLATE" / "dependency-update.md"
            ).read_text(encoding="utf-8")
            dependency_update_result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={**os.environ, "PR_BODY": dependency_update_body},
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(
                dependency_update_result.returncode, 0, dependency_update_result.stderr
            )

            default_body = (template_root / "PULL_REQUEST_TEMPLATE.md").read_text(
                encoding="utf-8"
            )
            for title, template_id in (
                ("feat: require the focused template", "feature"),
                ("fix(ci)!: require the focused template", "bugfix"),
                ("docs: require the focused template", "documentation"),
            ):
                selected_body = (
                    template_root / "PULL_REQUEST_TEMPLATE" / f"{template_id}.md"
                ).read_text(encoding="utf-8")
                with self.subTest(title=title, template_id=template_id):
                    selected_result = subprocess.run(
                        [sys.executable, "-c", script],
                        cwd=root,
                        env={
                            **os.environ,
                            "PR_BODY": selected_body,
                            "PR_TITLE": title,
                        },
                        capture_output=True,
                        check=False,
                        text=True,
                    )
                    self.assertEqual(
                        selected_result.returncode, 0, selected_result.stderr
                    )
                    default_result = subprocess.run(
                        [sys.executable, "-c", script],
                        cwd=root,
                        env={
                            **os.environ,
                            "PR_BODY": default_body,
                            "PR_TITLE": title,
                        },
                        capture_output=True,
                        check=False,
                        text=True,
                    )
                    self.assertNotEqual(default_result.returncode, 0)
                    self.assertIn(
                        f"must use the {template_id!r} template",
                        default_result.stderr,
                    )

            maintenance_result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={
                    **os.environ,
                    "PR_BODY": default_body,
                    "PR_TITLE": "ci: retain the default template",
                },
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(
                maintenance_result.returncode, 0, maintenance_result.stderr
            )

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

            feature_payload = feature_body.replace(
                "<!-- repo-scaffold:pr-template=feature -->\n\n", "", 1
            )
            hidden_content_bodies = {
                "fenced code": (
                    "<!-- repo-scaffold:pr-template=feature -->\n\n"
                    f"```markdown\n{feature_payload}\n```\n"
                ),
                "fenced code with a non-closing fence": (
                    "<!-- repo-scaffold:pr-template=feature -->\n\n"
                    f"```markdown\n``` not-a-closing-fence\n{feature_payload}\n```\n"
                ),
                "HTML comment": (
                    "<!-- repo-scaffold:pr-template=feature -->\n\n"
                    f"<!--\n{feature_payload}\n-->\n"
                ),
            }
            for hiding_method, hidden_body in hidden_content_bodies.items():
                with self.subTest(hiding_method=hiding_method):
                    hidden_result = subprocess.run(
                        [sys.executable, "-c", script],
                        cwd=root,
                        env={**os.environ, "PR_BODY": hidden_body},
                        capture_output=True,
                        check=False,
                        text=True,
                    )
                    self.assertNotEqual(hidden_result.returncode, 0)
                    self.assertIn(
                        "must contain exactly one required checklist section",
                        hidden_result.stderr,
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
                any("GitHub Actions updates are owned" in item for item in problems)
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
                any("GitHub Actions updates are owned" in item for item in problems)
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

    def test_workflow_discovery_includes_both_supported_yaml_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            yml = root / "ci.yml"
            yaml_file = root / "scheduled.yaml"
            ignored = root / "notes.txt"
            nested = root / "nested"
            yml.write_text("jobs: {}\n", encoding="utf-8")
            yaml_file.write_text("jobs: {}\n", encoding="utf-8")
            ignored.write_text("ignored\n", encoding="utf-8")
            nested.mkdir()
            (nested / "nested.yml").write_text("jobs: {}\n", encoding="utf-8")

            self.assertEqual(
                validate_workflows.discover_workflows(root), [yml, yaml_file]
            )

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

    def test_workflow_parser_converts_recursive_yaml_failures(self) -> None:
        path = mock.Mock(spec=Path)
        path.read_text.return_value = "jobs: {}\n"

        with mock.patch.object(
            validate_workflows.yaml,
            "load",
            side_effect=RecursionError("too deep"),
        ):
            with self.assertRaisesRegex(yaml.YAMLError, "nesting exceeds"):
                validate_workflows.workflow_shell_blocks(path)

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

    def test_workflow_parser_honors_workflow_level_default_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_workflow(
                directory,
                """
defaults:
  run:
    shell: sh -e {0}
jobs:
  test:
    runs-on: ${{ matrix.os }}
    steps:
      - run: echo ok
""",
            )

            self.assertEqual(
                validate_workflows.workflow_shell_blocks(path),
                [("test: step 0", "sh", b"echo ok")],
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

    def test_reconciliation_job_must_keep_effective_issue_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            installed = root / ".github/workflows/community-health.yml"
            workflow_text = installed.read_text(encoding="utf-8")
            workflow_text = workflow_text.replace(
                "  upstream-drift:\n    name: community-health-upstream\n",
                "  upstream-drift:\n"
                "    name: community-health-upstream\n"
                "    permissions:\n"
                "      contents: read\n",
                1,
            )
            installed.write_text(workflow_text, encoding="utf-8")
            problems = validate_repository.validate_community_health_tracking_contract(
                root
            )

        self.assertTrue(
            any("effective issues: write permission" in problem for problem in problems)
        )


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

    def test_freshness_job_runtime_contract_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            for relative in (
                ".github/workflows/freshness.yml",
                "skills/repo-scaffold/assets/workflows/freshness.yml",
            ):
                path = root / relative
                path.write_text(
                    path.read_text(encoding="utf-8")
                    .replace("    name: freshness-audit\n", "    name: reminder\n")
                    .replace("    timeout-minutes: 15\n", ""),
                    encoding="utf-8",
                )
            problems = validate_repository.validate_freshness_tracking_contract(root)

        self.assertTrue(
            any(
                "freshness audit job must use the 'freshness-audit' name and a "
                "15-minute timeout" in problem
                for problem in problems
            )
        )

    def test_freshness_checker_status_binding_is_derived(self) -> None:
        workflow_path = PLUGIN_ROOT / ".github/workflows/freshness.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        workflow = validate_repository.load_yaml_text(workflow_text)
        job = workflow["jobs"]["audit"]
        audit_step = next(step for step in job["steps"] if step.get("id") == "audit")
        binding_step = next(
            step
            for step in job["steps"]
            if isinstance(step.get("env"), dict) and "CHECKER_EXIT" in step["env"]
        )
        audit_run = audit_step["run"]
        binding_run = binding_step["run"]
        self.assertTrue(
            validate_repository.freshness_checker_result_output_is_safe(audit_run)
        )
        self.assertFalse(
            validate_repository.freshness_checker_result_output_is_safe("echo ready")
        )
        self.assertTrue(
            validate_repository.freshness_checker_result_binding_is_safe(workflow, job)
        )
        self.assertTrue(
            validate_repository.freshness_authentication_bindings_are_safe(
                workflow, job
            )
        )
        self.assertFalse(
            validate_repository.freshness_authentication_bindings_are_safe(None, job)
        )
        self.assertFalse(
            validate_repository.freshness_authentication_bindings_are_safe(
                workflow, None
            )
        )
        for scope in ("workflow", "job"):
            candidate = validate_repository.load_yaml_text(workflow_text)
            target = candidate if scope == "workflow" else candidate["jobs"]["audit"]
            target["env"] = {"GH_TOKEN": "attacker"}
            with self.subTest(authentication_scope=scope):
                self.assertFalse(
                    validate_repository.freshness_authentication_bindings_are_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        for variable in ("GIT_SSH_COMMAND", "LD_PRELOAD", "GH_CONFIG_DIR", "HOME"):
            for scope in ("workflow", "job"):
                candidate = validate_repository.load_yaml_text(workflow_text)
                target = (
                    candidate if scope == "workflow" else candidate["jobs"]["audit"]
                )
                target["env"] = {variable: "attacker"}
                with self.subTest(
                    authentication_scope=scope, unexpected_environment=variable
                ):
                    self.assertFalse(
                        validate_repository.freshness_authentication_bindings_are_safe(
                            candidate, candidate["jobs"]["audit"]
                        )
                    )
            candidate = validate_repository.load_yaml_text(workflow_text)
            audit_step = candidate["jobs"]["audit"]["steps"][2]
            audit_step["env"][variable] = "attacker"
            with self.subTest(
                authentication_step="audit", unexpected_environment=variable
            ):
                self.assertFalse(
                    validate_repository.freshness_authentication_bindings_are_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        auth_cases = (
            (
                "wrong audit token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][2]["env"].update(
                    {"GITHUB_TOKEN": "attacker"}
                ),
            ),
            (
                "wrong issue token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][4]["env"].update(
                    {"GH_TOKEN": "attacker"}
                ),
            ),
            (
                "missing audit token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][2]["env"].pop(
                    "GITHUB_TOKEN"
                ),
            ),
            (
                "missing issue token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][4]["env"].pop(
                    "GH_TOKEN"
                ),
            ),
            (
                "audit uses issue token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][2]["env"].update(
                    {"GH_TOKEN": "${{ github.token }}"}
                ),
            ),
            (
                "issue uses audit token",
                lambda candidate: candidate["jobs"]["audit"]["steps"][4]["env"].update(
                    {"GITHUB_TOKEN": "${{ github.token }}"}
                ),
            ),
            (
                "invalid token step environment",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0].update(
                    {"env": []}
                ),
            ),
            (
                "invalid token steps",
                lambda candidate: candidate["jobs"]["audit"].update({"steps": {}}),
            ),
            (
                "invalid token step item",
                lambda candidate: candidate["jobs"]["audit"]["steps"].append(None),
            ),
        )
        for name, mutate in auth_cases:
            candidate = validate_repository.load_yaml_text(workflow_text)
            mutate(candidate)
            with self.subTest(authentication_case=name):
                self.assertFalse(
                    validate_repository.freshness_authentication_bindings_are_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )

        output_cases = (
            ("missing audit result", audit_run.replace("checker_exit=$?\n", "", 1)),
            ("missing errexit disable", audit_run.replace("set +e\n", "", 1)),
            ("missing errexit restore", audit_run.replace("set -e\n", "", 1)),
            (
                "errexit restored too early",
                audit_run.replace("set +e\n", "set -e\n", 1),
            ),
            (
                "errexit disabled through long option",
                audit_run.replace("set -e\n", "set -e\nset +o errexit\n", 1),
            ),
            (
                "non-adjacent audit result",
                audit_run.replace(
                    "checker_exit=$?\n", "echo captured\nchecker_exit=$?\n", 1
                ),
            ),
            (
                "constant audit result",
                audit_run.replace('"$checker_exit" >>', '"0" >>', 1),
            ),
            (
                "additional audit printf",
                audit_run.replace(
                    "set -e\n", "set -e\nprintf 'side effect' > /tmp/ignored\n", 1
                ),
            ),
            (
                "negated audit result",
                audit_run.replace(
                    "python scripts/audit_freshness.py",
                    "! python scripts/audit_freshness.py",
                    1,
                ),
            ),
            (
                "additional output writer",
                audit_run.replace(
                    'printf \'checker_exit=%s\\n\' "$checker_exit" >> "$GITHUB_OUTPUT"',
                    "printf 'other=0\\nchecker_exit=0\\n' >> \"$GITHUB_OUTPUT\"\n"
                    'printf \'checker_exit=%s\\n\' "$checker_exit" >> "$GITHUB_OUTPUT"',
                    1,
                ),
            ),
            (
                "missing output",
                audit_run.replace(
                    'printf \'checker_exit=%s\\n\' "$checker_exit" >> "$GITHUB_OUTPUT"\n',
                    "",
                    1,
                ),
            ),
            (
                "unset audit result",
                audit_run.replace(
                    "checker_exit=$?\n", "checker_exit=$?\nunset checker_exit\n", 1
                ),
            ),
            (
                "read audit result",
                audit_run.replace(
                    "checker_exit=$?\n", "checker_exit=$?\nread checker_exit\n", 1
                ),
            ),
            (
                "printf audit result",
                audit_run.replace(
                    "checker_exit=$?\n",
                    "checker_exit=$?\nprintf -v checker_exit 0\n",
                    1,
                ),
            ),
            (
                "invalid fallback result",
                audit_run.replace("checker_exit=2\n", "checker_exit=0\n", 1),
            ),
            (
                "fallback outside guard",
                audit_run.replace("checker_exit=2\n", "", 1).replace(
                    "printf 'checker_exit=%s\\n' \"$checker_exit\"",
                    "checker_exit=2\nprintf 'checker_exit=%s\\n' \"$checker_exit\"",
                    1,
                ),
            ),
            (
                "multiple fallback guards",
                audit_run.replace(
                    'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n',
                    'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n'
                    'if [[ ! -f "$RUNNER_TEMP/other.md" ]]; then\n'
                    "fi\n",
                    1,
                ),
            ),
            (
                "fallback without guard",
                audit_run.replace(
                    'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n',
                    'if [[ -f "$RUNNER_TEMP/freshness.md" ]]; then\n',
                    1,
                ),
            ),
            (
                "fallback path mismatch",
                audit_run.replace(
                    'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n',
                    'if [[ ! -f "$RUNNER_TEMP/other.md" ]]; then\n',
                    1,
                ),
            ),
            (
                "fallback marker missing",
                audit_run.replace(
                    "<!-- repo-scaffold-freshness-audit -->",
                    "fallback report",
                    1,
                ),
            ),
            (
                "fallback writer is not printf",
                audit_run.replace("printf '%s\\n' \\\n", "echo \\\n", 1),
            ),
            (
                "output published inside fallback",
                audit_run.replace(
                    "fi\nprintf 'checker_exit=%s\\n' \"$checker_exit\"",
                    "printf 'checker_exit=%s\\n' \"$checker_exit\"\nfi",
                    1,
                ),
            ),
            (
                "fallback status before report",
                audit_run.replace(
                    "  checker_exit=2\nfi",
                    "  checker_exit=2\nfi",
                    1,
                ).replace(
                    "  printf '%s\\n' \\\n",
                    "  checker_exit=2\n  printf '%s\\n' \\\n",
                    1,
                ),
            ),
            ("malformed shell", audit_run + "echo 'unterminated"),
        )
        for name, command in output_cases:
            with self.subTest(output_case=name):
                self.assertFalse(
                    validate_repository.freshness_checker_result_output_is_safe(command)
                )
        no_fallback = audit_run.replace(
            'if [[ ! -f "$RUNNER_TEMP/freshness.md" ]]; then\n'
            "  printf '%s\\n' \\\n"
            "    '<!-- repo-scaffold-freshness-audit -->' \\\n"
            "    '# Repository freshness report' \\\n"
            "    '' \\\n"
            "    'The checker failed before it could produce a report. Inspect this workflow run.' \\\n"
            '    > "$RUNNER_TEMP/freshness.md"\n'
            "  checker_exit=2\n"
            "fi\n",
            "",
            1,
        )
        self.assertFalse(
            validate_repository.freshness_checker_result_output_is_safe(no_fallback)
        )
        self.assertFalse(
            validate_repository.freshness_shell_definitions_are_safe("CHECKER_EXIT=0")
        )
        with (
            mock.patch.object(
                validate_repository,
                "shell_command_segments",
                return_value=[["echo", "ready"]],
            ),
            mock.patch.object(
                validate_repository.shlex, "shlex", side_effect=ValueError("malformed")
            ),
        ):
            self.assertFalse(
                validate_repository.freshness_shell_definitions_are_safe("ignored")
            )

        def fresh_workflow() -> Any:
            return validate_repository.load_yaml_text(workflow_text)

        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(None, job)
        )
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(workflow, None)
        )
        for scope in ("workflow", "job"):
            candidate = fresh_workflow()
            target = candidate if scope == "workflow" else candidate["jobs"]["audit"]
            target["env"] = []
            with self.subTest(invalid_environment=scope):
                self.assertFalse(
                    validate_repository.freshness_checker_result_binding_is_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        candidate = fresh_workflow()
        candidate["env"] = {"CHECKER_EXIT": "0"}
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["env"] = {"CHECKER_EXIT": "0"}
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        for variable in ("PATH", "BASH_ENV", "ENV"):
            candidate = fresh_workflow()
            candidate["jobs"]["audit"]["steps"][0]["env"] = {variable: "/tmp/fake"}
            with self.subTest(protected_environment=variable):
                self.assertFalse(
                    validate_repository.freshness_checker_result_binding_is_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        for variable in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "RUNNER_TEMP"):
            candidate = fresh_workflow()
            candidate["jobs"]["audit"]["steps"][4]["env"] = {variable: "/tmp/fake"}
            with self.subTest(runner_file_environment=variable):
                self.assertFalse(
                    validate_repository.freshness_checker_result_binding_is_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["env"]["PATH"] = "/tmp/fake"
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        invalid_step_values: tuple[object, ...] = ({}, [None])
        for invalid_steps in invalid_step_values:
            candidate = fresh_workflow()
            candidate["jobs"]["audit"]["steps"] = invalid_steps
            with self.subTest(invalid_steps=invalid_steps):
                self.assertFalse(
                    validate_repository.freshness_checker_result_binding_is_safe(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][0]["env"] = []
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["env"]["CHECKER_EXIT"] = "0"
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][0]["id"] = "audit"
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["env"].pop("CHECKER_EXIT")
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][2]["env"] = {
            "CHECKER_EXIT": "${{ steps.audit.outputs.checker_exit }}"
        }
        candidate["jobs"]["audit"]["steps"][4]["env"].pop("CHECKER_EXIT")
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][2]["run"] = None
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["run"] = None
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["run"] = "gh issue create"
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["run"] = binding_run.replace(
            "issue_numbers_output=$(", "issue_numbers_output=", 1
        )
        self.assertFalse(
            validate_repository.freshness_checker_result_binding_is_safe(
                candidate, candidate["jobs"]["audit"]
            )
        )
        candidate = fresh_workflow()
        steps = candidate["jobs"]["audit"]["steps"]
        steps[2], steps[4] = steps[4], steps[2]
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                candidate, workflow_text
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][2]["run"] = audit_run.replace(
            "printf 'checker_exit=%s\\n' \"$checker_exit\"",
            "printf 'checker_exit=%s\\n' \"0\"",
            1,
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                candidate, workflow_text
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["env"].pop("CHECKER_EXIT")
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                candidate, workflow_text
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][4]["run"] += "\nPATH=/tmp/fake:$PATH"
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                candidate, workflow_text
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][2]["env"]["GITHUB_TOKEN"] = "attacker"
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                candidate, workflow_text
            )
        )
        candidate = fresh_workflow()
        candidate["jobs"]["audit"]["steps"][2]["env"]["GITHUB_OUTPUT"] = "/tmp/output"
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                candidate, workflow_text
            )
        )

    def test_root_freshness_registry_cannot_disable_shipped_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            registry_path = root / ".github/freshness-trackers.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["workflow-directories"] = [".github/workflows"]
            registry["release-please-configs"] = []
            registry["requirement-sources"] = []
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            problems = validate_repository.validate_freshness_tracking_contract(root)

        self.assertIn(
            ".github/freshness-trackers.json: freshness registry must track its shipped inputs",
            problems,
        )

    def test_freshness_workflow_cannot_hide_a_job_behind_reusable_workflow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            for relative in (
                ".github/workflows/freshness.yml",
                "skills/repo-scaffold/assets/workflows/freshness.yml",
            ):
                path = root / relative
                path.write_text(
                    path.read_text(encoding="utf-8")
                    + "\n  hidden:\n"
                    + "    uses: owner/repository/.github/workflows/reusable.yml@"
                    + "0123456789abcdef0123456789abcdef01234567\n",
                    encoding="utf-8",
                )
            problems = validate_repository.validate_freshness_tracking_contract(root)

        self.assertEqual(
            sum(
                "freshness workflow must run the checker" in problem
                for problem in problems
            ),
            2,
        )

    def test_missing_freshness_tracking_contract_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problems = validate_repository.validate_freshness_tracking_contract(
                Path(directory)
            )
        self.assertTrue(any("freshness" in problem for problem in problems))

    def test_issue_permission_helpers_resolve_effective_job_scope(self) -> None:
        self.assertTrue(
            validate_repository.has_explicit_repository_binding(
                'gh issue create --repo "$REPOSITORY" --body-file report.md'
            )
        )
        self.assertTrue(
            validate_repository.has_explicit_repository_binding(
                "gh issue edit --repo=$REPOSITORY --body-file report.md"
            )
        )
        self.assertFalse(
            validate_repository.has_explicit_repository_binding(
                'gh issue create --repo "" --body-file report.md'
            )
        )
        self.assertFalse(
            validate_repository.has_explicit_repository_binding(
                "gh issue create --repo --title reminder --body-file report.md"
            )
        )
        self.assertFalse(
            validate_repository.has_explicit_repository_binding(
                'gh issue create --repo "unterminated'
            )
        )
        self.assertFalse(
            validate_repository.has_explicit_repository_binding(
                "python audit.py --repository-root ."
            )
        )
        self.assertTrue(
            validate_repository.has_embedded_command(
                ["echo", r"C:\Program Files\gh.exe issue close 1"]
            )
        )
        self.assertFalse(validate_repository.has_dynamic_shell_executor(["${{"]))
        self.assertFalse(validate_repository.has_dynamic_shell_executor(["$UPPER"]))
        self.assertTrue(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md"
            )
        )
        self.assertTrue(
            validate_repository.has_repo_bound_issue_reconciliation(
                'gh issue create --repo "github.com/$GITHUB_REPOSITORY" '
                "--title reminder --body-file report.md",
                expected_repository_values={"github.com/$GITHUB_REPOSITORY"},
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo attacker/repository --title reminder "
                "--body-file report.md",
                expected_repository_values={"github.com/$GITHUB_REPOSITORY"},
            )
        )
        self.assertTrue(
            validate_repository.has_repo_bound_issue_reconciliation(
                r"""gh issue create \
  --repo $REPOSITORY \
  --title reminder \
  --body-file report.md"""
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue close 1 --repo $REPOSITORY"
            )
        )
        self.assertTrue(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue close 1 --repo $REPOSITORY\n"
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue close 1\n"
                "gh issue create --repo $REPOSITORY --body-file report.md"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --body-file report.md\n"
                "gh issue edit --repo $REPOSITORY --body-file report.md"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                'gh issue create --repo "$REPOSITORY" --body-file ""'
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "# gh issue create --repo $REPOSITORY --body-file report.md"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation("gh issue")
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create 'unterminated"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "echo gh issue create"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation("gh issue list")
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue archive 1 --repo $REPOSITORY"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md; gh issue close 1"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md\n"
                "gh issue edit 1 --repo $REPOSITORY --body stale"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md\n"
                "gh api --method POST repos/$REPOSITORY/issues -f title=x"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md\n"
                "bash -c 'gh issue close 1'"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md\n"
                'env bash -c "$COMMAND"'
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md\n"
                "gh --repo $REPOSITORY issue reopen 1"
            )
        )
        self.assertIsNone(
            validate_repository.reminder_issue_mutation_blocks(
                "gh --hostname github.com issue close 1"
            )
        )
        self.assertTrue(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh --repo $REPOSITORY issue create --title reminder "
                "--body-file report.md"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md\n"
                "echo `gh issue close 1`"
            )
        )
        self.assertFalse(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --title reminder --body-file report.md "
                "-- --repo $REPOSITORY"
            )
        )
        for command in (
            "/usr/bin/gh issue create --repo r --title t --body-file report.md",
            "gh.exe issue create --repo r --title t --body-file report.md",
        ):
            with self.subTest(command=command):
                self.assertFalse(
                    validate_repository.has_repo_bound_issue_reconciliation(command)
                )
        for command in (
            "$command issue create --repo r --title t --body-file report.md",
            "Start-Process gh -ArgumentList 'issue create --repo r --title t --body-file report.md'",
            "curl -X POST https://api.github.com/repos/r/issues",
            "Invoke-RestMethod -Method Post -Uri https://api.github.com/repos/r/issues",
            "iwr -Method Post https://api.github.com/repos/r/issues",
        ):
            with self.subTest(command=command):
                self.assertIsNone(
                    validate_repository.reminder_issue_mutation_blocks(command)
                )
        self.assertTrue(
            validate_repository.has_repo_bound_issue_reconciliation(
                "gh issue create --repo $REPOSITORY --title reminder "
                "--body-file report.md\n"
                "gh api repos/$REPOSITORY/issues --method GET -f q=x"
            )
        )
        self.assertIsNone(validate_repository.option_values(["--repo="], "--repo"))
        self.assertEqual(
            validate_repository.option_values(
                ["--repo", "one", "--", "--repo", "two"], "--repo"
            ),
            ("one",),
        )
        self.assertFalse(
            validate_repository.has_value_bearing_option(
                "gh issue create --repo", "--repo"
            )
        )
        self.assertFalse(validate_repository.has_dynamic_shell_executor([]))
        for tokens, expected in (
            ([], False),
            (["FOO=bar", "cd", "other"], True),
            (["1=bad", "cd", "other"], True),
            (["FOO=bar"], False),
            (["if", "cd", "other"], True),
            (["Set-Location", "other"], True),
        ):
            with self.subTest(tokens=tokens):
                self.assertEqual(
                    validate_repository.has_directory_change_command(tokens), expected
                )
        self.assertTrue(
            validate_repository.has_dynamic_shell_executor(["env", "--", "bash"])
        )
        self.assertTrue(validate_repository.has_dynamic_shell_executor(["env"]))
        self.assertTrue(validate_repository.has_dynamic_shell_executor(["bash"]))
        self.assertTrue(validate_repository.has_dynamic_shell_executor(["source"]))
        self.assertTrue(validate_repository.has_dynamic_shell_executor(["."]))
        self.assertFalse(
            validate_repository.has_dynamic_shell_executor(["1=bad", "bash"])
        )
        for tokens, expected in (
            (["gh", "api", "repos/example/issues", "--method="], True),
            (["/usr/bin/gh", "api", "repos/example/issues", "-f", "title=x"], True),
            (["gh", "api", "repos/example/issues", "-XDELETE"], True),
            (["gh", "api", "repos/example/issues", "-X", "HEAD"], False),
            (["gh", "api", "repos/example/issues", "--method"], True),
        ):
            with self.subTest(tokens=tokens):
                self.assertEqual(
                    validate_repository.github_api_is_mutation(tokens, 0), expected
                )
        for tokens, expected in (
            (["curl", "--fail", "https://example.test"], False),
            (["curl", "-x", "proxy", "https://example.test"], False),
            (["curl", "-f", "https://example.test"], False),
            (["curl", "-X", "GET", "https://example.test"], False),
            (["curl", "-XPOST", "https://example.test"], True),
            (["curl", "-d", "title=x", "https://example.test"], True),
            (["curl", "-F", "title=x", "https://example.test"], True),
            (["curl", "-T", "payload", "https://example.test"], True),
            (["curl", "--upload-file=payload", "https://example.test"], True),
            (["curl", "--json", '{"title":"x"}', "https://example.test"], True),
            (["curl", "-g", "-d", "q=x", "https://example.test"], True),
            (["curl", "-i", "-d", "q=x", "https://example.test"], True),
            (["curl", "-G", "-d", "q=x", "https://example.test"], False),
            (["curl", "-I", "-d", "q=x", "https://example.test"], False),
            (["curl", "--get", "--data", "q=x", "https://example.test"], False),
            (["curl", "--head", "https://example.test"], False),
            (["curl", "--method=POST", "https://example.test"], True),
            (["curl", "-X"], True),
            (["Invoke-WebRequest", "-Method=Post", "https://example.test"], True),
            (["wget", "--post-data=x", "https://example.test"], True),
        ):
            with self.subTest(tokens=tokens):
                self.assertEqual(
                    validate_repository.network_client_is_mutation(tokens), expected
                )
        self.assertEqual(
            validate_repository.freshness_audit_markdown_outputs(
                "python scripts/audit_freshness.py --repository-root . "
                "--json-output report.json --markdown-output report.md"
            ),
            {"report.md"},
        )
        self.assertEqual(
            validate_repository.freshness_audit_markdown_outputs(
                "python scripts/audit_freshness.py --repository-root . "
                "--json-output report.json --markdown-output report.md "
                "--tracker-registry .github/freshness-trackers.json"
            ),
            {"report.md"},
        )
        self.assertIsNone(
            validate_repository.freshness_audit_markdown_outputs(
                "python scripts/audit_freshness.py --repository-root other "
                "--json-output report.json --markdown-output report.md"
            )
        )
        self.assertIsNone(
            validate_repository.freshness_audit_markdown_outputs(
                "python scripts/audit_freshness.py --repository-root . "
                "--json-output report.json --markdown-output report.md "
                "--tracker-registry other.json"
            )
        )
        self.assertIsNone(
            validate_repository.freshness_audit_markdown_outputs(
                "python scripts/audit_freshness.py --repository-root . "
                "--json-output report.json --markdown-output report.md "
                "--unexpected ignored"
            )
        )
        self.assertIsNone(
            validate_repository.freshness_audit_markdown_outputs(
                "cd other\n"
                "python scripts/audit_freshness.py --repository-root . "
                "--json-output report.json --markdown-output report.md"
            )
        )
        jq_expression = (
            '.[] | select(.pull_request == null) | select((.body // "") | '
            'contains("<!-- repo-scaffold-freshness-audit -->")) | .number'
        )
        api_lookup = (
            "gh api --hostname github.com --paginate "
            '"repos/$GITHUB_REPOSITORY/issues?state=open&per_page=100" '
            f"--jq '{jq_expression}'"
        )
        self.assertTrue(
            validate_repository.has_freshness_repository_api_reads(api_lookup)
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace(
                    "repos/$GITHUB_REPOSITORY", "repos/attacker/repository"
                )
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace("gh api", "gh --hostname github.com api")
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace("gh api", "echo gh api")
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace("--hostname github.com ", "")
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace("github.com", "ghe.example.com")
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace("--hostname github.com", "github.com --hostname")
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace("--paginate ", "")
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace(
                    "state=open&per_page=100", "state=closed&per_page=100"
                )
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace(f"--jq '{jq_expression}'", "--jq '.number'")
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace(
                    f"--jq '{jq_expression}'", f"--jq '{jq_expression}, 999'"
                )
            )
        )
        for option in ("--slurp", "--include", "GET"):
            with self.subTest(option=option):
                self.assertFalse(
                    validate_repository.has_freshness_repository_api_reads(
                        api_lookup.replace("--paginate ", f"--paginate {option} ")
                    )
                )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace("--paginate ", "-- ")
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup + " --method"
            )
        )
        for method in (
            "--method HEAD",
            "--method=HEAD",
            "-XHEAD",
            "-X HEAD",
        ):
            with self.subTest(method=method):
                self.assertFalse(
                    validate_repository.has_freshness_repository_api_reads(
                        api_lookup.replace("--paginate ", f"--paginate {method} ")
                    )
                )
        for method in ("--method GET", "--method=GET", "-XGET", "-X GET"):
            with self.subTest(method=method):
                self.assertTrue(
                    validate_repository.has_freshness_repository_api_reads(
                        api_lookup.replace("--paginate ", f"--paginate {method} ")
                    )
                )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                api_lookup.replace(
                    "--paginate ", "--paginate --method GET --method GET "
                )
            )
        )
        audit_command = (
            "python scripts/audit_freshness.py --repository-root . "
            "--json-output report.json --markdown-output report.md"
        )
        mutation_command = (
            'gh issue create --repo "github.com/$GITHUB_REPOSITORY" '
            "--title reminder --body-file report.md"
        )
        ordered_commands = "\n".join((audit_command, api_lookup, mutation_command))
        self.assertTrue(
            validate_repository.freshness_command_order_is_valid(ordered_commands)
        )
        for operator in ("&", "|", "|&", "&&", "||"):
            with self.subTest(operator=operator):
                self.assertFalse(
                    validate_repository.freshness_command_order_is_valid(
                        f" {operator} ".join(
                            (audit_command, api_lookup, mutation_command)
                        )
                    )
                )
        bound_api_lookup = (
            "issue_numbers_output=$(\n  "
            + api_lookup
            + '\n)\nmapfile -t issue_numbers <<< "$issue_numbers_output"'
        )
        self.assertTrue(
            validate_repository.freshness_api_result_is_consumed(bound_api_lookup)
        )
        self.assertTrue(
            validate_repository.freshness_api_result_controls_issue_selection(
                bound_api_lookup + '\ngh issue edit "${issue_numbers[0]}" --repo r '
                "--body-file report.md"
            )
        )
        self.assertTrue(
            validate_repository.freshness_api_result_controls_issue_selection(
                'output=$(gh api)\ngh issue edit "$output" --repo r '
                "--body-file report.md"
            )
        )
        self.assertFalse(
            validate_repository.freshness_api_result_controls_issue_selection(
                'unrelated=attacker\noutput=$(gh api)\necho "$output"\n'
                'gh issue edit "$unrelated" --repo r --body-file report.md'
            )
        )
        self.assertFalse(
            validate_repository.freshness_api_result_controls_issue_selection(
                bound_api_lookup
                + '\ngh issue edit "${issue_numbers[0]}" --repo r --body-file report.md\n'
                + "gh issue edit 999 --repo r --body-file report.md"
            )
        )
        self.assertFalse(
            validate_repository.freshness_api_result_controls_issue_selection(
                bound_api_lookup + "\ngh issue close"
            )
        )
        for command in (
            'output=$(gh api)\necho "$output"\n'
            "gh issue create --repo r --title t --body-file report.md",
            'output=$(gh api)\nif [[ -n "$output" ]]; then :; fi\n'
            "gh issue create --repo r --title t --body-file report.md",
            'output=$(gh api)\nmapfile -t ids <<< "$other"\n'
            'gh issue edit "${ids[0]}" --repo r --body-file report.md',
            'output=$(gh api)\nmapfile -t 1bad <<< "$output"\n'
            'gh issue edit "${1bad[0]}" --repo r --body-file report.md',
            'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
            'ids=(999)\ngh issue edit "${ids[0]}" --repo r --body-file report.md',
            'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
            'unset ids\ngh issue edit "${ids[0]}" --repo r --body-file report.md',
            'output=$(gh api)\necho "$output"\ngh issue',
        ):
            with self.subTest(command=command):
                self.assertFalse(
                    validate_repository.freshness_api_result_controls_issue_selection(
                        command
                    )
                )
        flow_cases = (
            (
                "plain variable suffix is rejected",
                'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
                'gh issue edit "$ids_suffix" --repo r --body-file report.md',
                False,
            ),
            (
                "plain variable suffix is not an Issue argument",
                'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
                'gh issue edit "$ids-suffix" --repo r --body-file report.md',
                False,
            ),
            (
                "malformed later shell line",
                "output=$(gh api)\necho 'open\nclosed'\n$output",
                False,
            ),
            (
                "malformed assignment",
                "output=$(gh api 'unterminated",
                False,
            ),
            (
                "result reassigned after an early reference",
                'output=$(gh api)\necho "$output"\noutput=bad\n'
                'gh issue edit "$output" --repo r --body-file report.md',
                False,
            ),
            (
                "duplicate here-string redirects",
                'output=$(gh api)\nmapfile -t ids <<< "$output" <<< "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "here-string has no target",
                'output=$(gh api)\nmapfile <<< "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "here-string has no source",
                'output=$(gh api)\necho "$output"\nmapfile -t ids <<<\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "collection source is unrelated",
                'output=$(gh api)\necho "$output"\nmapfile -t ids <<< "$other"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "invalid collection target",
                'output=$(gh api)\nmapfile -t 1bad <<< "$output"\n'
                'gh issue edit "${1bad[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "collection precedes lookup",
                'mapfile -t ids <<< "$output"\noutput=$(gh api)\necho "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "lookup source is reassigned",
                'output=$(gh api)\necho "$output"\noutput=bad\n'
                'mapfile -t ids <<< "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                False,
            ),
            (
                "unsupported global issue option",
                'output=$(gh api)\necho "$output"\n'
                'gh --hostname github.com issue edit "$output" --repo r '
                "--body-file report.md",
                False,
            ),
            (
                "read-only issue command",
                'output=$(gh api)\necho "$output"\ngh issue list',
                False,
            ),
            (
                "mutation without issue argument",
                'output=$(gh api)\necho "$output"\ngh issue edit',
                False,
            ),
            (
                "readarray collection",
                'output=$(gh api)\nreadarray -t ids <<< "$output"\n'
                'gh issue edit "${ids[0]}" --repo r --body-file report.md',
                True,
            ),
        )
        for name, command, expected in flow_cases:
            with self.subTest(flow_case=name):
                self.assertEqual(
                    validate_repository.freshness_api_result_controls_issue_selection(
                        command
                    ),
                    expected,
                )
        self.assertFalse(
            validate_repository.freshness_api_result_is_consumed(api_lookup)
        )
        for command, expected in (
            ("output=$(echo ready)", False),
            ("output=$(", False),
            ("output=$()", False),
            ("output=$ echo ready", False),
            ("bad-name=$(gh api)", False),
            ("output=$(gh api 'unterminated", False),
            ("output=$(gh api)\noutput=''\n$output", False),
            ("output=$(gh api)\n${output}", True),
            ("output=$(gh api)\n$output-suffix", True),
            ("output=$(gh api)\n$output_suffix", False),
            ("output=$(gh api)\n${outputevil}", False),
            ("output=$(echo $(gh api) )\n$output", True),
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    validate_repository.freshness_api_result_is_consumed(command),
                    expected,
                )
        for commands in (
            (mutation_command, audit_command, api_lookup),
            (api_lookup, audit_command, mutation_command),
            (audit_command, mutation_command, api_lookup),
        ):
            with self.subTest(commands=commands):
                self.assertFalse(
                    validate_repository.freshness_command_order_is_valid(
                        "\n".join(commands)
                    )
                )
        self.assertFalse(
            validate_repository.freshness_command_order_is_valid(
                "gh issue 'unterminated"
            )
        )
        self.assertFalse(
            validate_repository.freshness_command_order_is_valid(
                "gh issue create gh issue close"
            )
        )
        self.assertFalse(
            validate_repository.freshness_command_order_is_valid("echo 'open\nclosed'")
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                "gh api --method POST repos/$GITHUB_REPOSITORY/issues"
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                "GITHUB_REPOSITORY=attacker/repository "
                "gh api --hostname github.com --paginate "
                '"repos/$GITHUB_REPOSITORY/issues?state=open&per_page=100"'
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads(
                "gh api 'unterminated"
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_repository_api_reads("echo ready")
        )
        self.assertTrue(
            validate_repository.has_freshness_repository_api_reads(
                "echo ready", require_lookup=False
            )
        )
        self.assertTrue(
            validate_repository.has_repository_root_working_directory(
                {
                    "jobs": {
                        "audit": {
                            "defaults": {"run": {"working-directory": "."}},
                            "steps": [{"working-directory": "."}],
                        }
                    }
                }
            )
        )
        working_directory_documents: tuple[dict[str, Any], ...] = (
            {},
            {"jobs": []},
            {"jobs": {"audit": []}},
            {"jobs": {"audit": {"defaults": []}}},
            {"jobs": {"audit": {"defaults": {"run": []}}}},
            {"jobs": {"audit": {"defaults": {}, "steps": {}}}},
            {"jobs": {"audit": {"defaults": {"run": {"working-directory": "other"}}}}},
            {"jobs": {"audit": {"steps": [{"working-directory": "other"}]}}},
        )
        for document in working_directory_documents:
            with self.subTest(document=document):
                self.assertFalse(
                    validate_repository.has_repository_root_working_directory(document)
                )
        self.assertIsNone(
            validate_repository.freshness_audit_markdown_outputs(
                "python scripts/audit_freshness.py --repository-root ."
            )
        )
        self.assertTrue(
            validate_repository.has_direct_freshness_jobs(
                {"jobs": {"audit": {"steps": []}}}
            )
        )
        self.assertFalse(
            validate_repository.has_direct_freshness_jobs(
                {
                    "jobs": {
                        "audit": {"steps": []},
                        "hidden": {"uses": "./.github/workflows/reusable.yml"},
                    }
                }
            )
        )
        self.assertTrue(
            validate_repository.has_freshness_repository_context(
                {"jobs": {"audit": {"steps": []}}}
            )
        )
        self.assertTrue(
            validate_repository.has_freshness_repository_context(
                {"jobs": {"audit": {"env": {}}}}
            )
        )
        for document in (
            {"jobs": []},
            {"jobs": {"audit": []}},
            {"jobs": {"audit": {"env": []}}},
            {"jobs": {"audit": {"env": {}, "steps": {}}}},
            {
                "jobs": {
                    "audit": {
                        "env": {"GITHUB_REPOSITORY": "attacker/repository"},
                        "steps": [],
                    }
                }
            },
            {
                "jobs": {
                    "audit": {
                        "steps": [{"env": {"GITHUB_REPOSITORY": "attacker/repository"}}]
                    }
                }
            },
            {"jobs": {"audit": {"steps": [{"env": []}]}}},
            {"jobs": {"audit": {"steps": [[]]}}},
        ):
            with self.subTest(document=document):
                self.assertFalse(
                    validate_repository.has_freshness_repository_context(document)
                )
        self.assertIsNone(
            validate_repository.freshness_audit_markdown_outputs(
                "python scripts/audit_freshness.py --repository-root . "
                "--json-output report.md --markdown-output report.md"
            )
        )
        self.assertIsNone(
            validate_repository.freshness_audit_markdown_outputs(
                "python scripts/audit_freshness.py 'unterminated"
            )
        )
        self.assertIsNone(
            validate_repository.freshness_audit_markdown_outputs(
                "bash -c 'python scripts/audit_freshness.py --repository-root .'"
            )
        )
        self.assertIsNone(
            validate_repository.freshness_audit_markdown_outputs(
                "python scripts/audit_freshness.py --repository-root . "
                "--json-output report.json --markdown-output report.md "
                "python scripts/audit_freshness.py"
            )
        )
        self.assertEqual(
            validate_repository.freshness_audit_markdown_outputs("echo ready"),
            set(),
        )
        contract_workflow = validate_repository.load_yaml(
            PLUGIN_ROOT / ".github/workflows/freshness.yml"
        )
        contract_text = (PLUGIN_ROOT / ".github/workflows/freshness.yml").read_text(
            encoding="utf-8"
        )
        self.assertTrue(
            validate_repository.has_least_privileged_freshness_permissions(
                contract_workflow
            )
        )
        permission_documents: tuple[dict[str, Any], ...] = (
            {"permissions": {"issues": "write"}, "jobs": {}},
            {"permissions": {"contents": "read", "issues": "write"}, "jobs": []},
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": "not-a-job"},
            },
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": {"permissions": "write-all"}},
            },
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": {"permissions": {"contents": "write"}}},
            },
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": {"permissions": {"issues": "admin"}}},
            },
            {
                "permissions": {"contents": "read", "issues": "write"},
                "jobs": {"audit": {"permissions": {"actions": "read"}}},
            },
        )
        for document in permission_documents:
            with self.subTest(document=document):
                self.assertFalse(
                    validate_repository.has_least_privileged_freshness_permissions(
                        document
                    )
                )
        self.assertTrue(
            validate_repository.has_freshness_job_reconciliation(
                contract_workflow, contract_text
            )
        )
        self.assertTrue(
            validate_repository.freshness_job_execution_is_unconditional(
                contract_workflow["jobs"]["audit"]
            )
        )
        self.assertTrue(
            validate_repository.freshness_execution_context_is_bash(
                contract_workflow, contract_workflow["jobs"]["audit"]
            )
        )
        self.assertIsNone(validate_repository.freshness_shell_if_block_ranges([["fi"]]))
        self.assertIsNone(
            validate_repository.freshness_shell_if_block_ranges([["if", "true"]])
        )
        self.assertEqual(
            validate_repository.freshness_shell_if_block_ranges(
                [["if", "true"], ["if", "true"], ["fi"], ["fi"]]
            ),
            {1: 2, 0: 3},
        )
        defaults_candidate = validate_repository.load_yaml_text(contract_text)
        defaults_candidate["defaults"] = {"run": {"shell": "bash"}}
        self.assertTrue(
            validate_repository.freshness_execution_context_is_bash(
                defaults_candidate, defaults_candidate["jobs"]["audit"]
            )
        )
        defaults_without_run = validate_repository.load_yaml_text(contract_text)
        defaults_without_run["defaults"] = {}
        self.assertTrue(
            validate_repository.freshness_execution_context_is_bash(
                defaults_without_run, defaults_without_run["jobs"]["audit"]
            )
        )
        context_cases: tuple[tuple[str, Any], ...] = (
            ("invalid workflow", None),
            ("invalid job", None),
        )
        for name, invalid in context_cases:
            with self.subTest(context=name):
                self.assertFalse(
                    validate_repository.freshness_execution_context_is_bash(
                        invalid,
                        contract_workflow["jobs"]["audit"],
                    )
                )
        for name, mutate in (
            (
                "runner",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"runs-on": "windows-latest"}
                ),
            ),
            (
                "workflow defaults type",
                lambda candidate: candidate.update({"defaults": []}),
            ),
            (
                "workflow run defaults type",
                lambda candidate: candidate.update({"defaults": {"run": []}}),
            ),
            (
                "workflow shell",
                lambda candidate: candidate.update(
                    {"defaults": {"run": {"shell": "pwsh"}}}
                ),
            ),
        ):
            candidate = validate_repository.load_yaml_text(contract_text)
            mutate(candidate)
            with self.subTest(context=name):
                self.assertFalse(
                    validate_repository.freshness_execution_context_is_bash(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        for name, mutate in (
            (
                "job defaults type",
                lambda candidate: candidate["jobs"]["audit"].update({"defaults": []}),
            ),
            (
                "job run defaults type",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"defaults": {"run": []}}
                ),
            ),
            (
                "job shell",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"defaults": {"run": {"shell": "pwsh"}}}
                ),
            ),
            (
                "step shell",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0].update(
                    {"shell": "pwsh"}
                ),
            ),
            (
                "invalid steps",
                lambda candidate: candidate["jobs"]["audit"].update({"steps": {}}),
            ),
        ):
            candidate = validate_repository.load_yaml_text(contract_text)
            mutate(candidate)
            with self.subTest(context=name):
                self.assertFalse(
                    validate_repository.freshness_execution_context_is_bash(
                        candidate, candidate["jobs"]["audit"]
                    )
                )
        contract_job_text = "\n".join(
            step["run"]
            for step in contract_workflow["jobs"]["audit"]["steps"]
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        )
        issue_id = "$" + "{issue_numbers[0]}"
        self.assertTrue(
            validate_repository.freshness_shell_definitions_are_safe(contract_job_text)
        )
        self.assertTrue(validate_repository.freshness_shell_definitions_are_safe(""))
        self.assertTrue(
            validate_repository.freshness_shell_expansions_are_safe(contract_job_text)
        )
        for expansion in (
            "echo $(true)",
            "issue_numbers_output=$(gh api)\nissue_numbers_output=$(gh api)",
            "echo ${IFS}",
            "echo $UNTRUSTED",
        ):
            with self.subTest(shell_expansion=expansion):
                self.assertFalse(
                    validate_repository.freshness_shell_expansions_are_safe(expansion)
                )
        self.assertTrue(
            validate_repository.freshness_variable_is_reassigned(
                ["printf", "%n", "target"], "target"
            )
        )
        self.assertTrue(
            validate_repository.freshness_variable_is_reassigned(
                ["${target:=0}"], "target"
            )
        )
        self.assertFalse(
            validate_repository.freshness_issue_options_are_safe(
                ["gh", "issue", "reopen", "1"], 1, "reopen"
            )
        )
        self.assertFalse(
            validate_repository.freshness_issue_options_are_safe(
                ["gh", "issue", "close"], 1, "close"
            )
        )
        self.assertTrue(
            validate_repository.freshness_issue_options_are_safe(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo=repo",
                    "--title=title",
                    "--body-file=report.md",
                ],
                1,
                "create",
            )
        )
        self.assertFalse(
            validate_repository.freshness_issue_options_are_safe(
                [
                    "gh",
                    "issue",
                    "edit",
                    "1",
                    "--repo",
                    "repo",
                    "--title",
                    "one",
                    "--title",
                    "two",
                ],
                1,
                "edit",
            )
        )
        self.assertFalse(
            validate_repository.freshness_issue_options_are_safe(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    "repo",
                    "--title=",
                    "--body-file",
                    "report.md",
                ],
                1,
                "create",
            )
        )
        self.assertFalse(
            validate_repository.freshness_issue_options_are_safe(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    "repo",
                    "--title",
                    "$UNTRUSTED_TITLE",
                    "--body-file",
                    "report.md",
                ],
                1,
                "create",
            )
        )
        self.assertFalse(
            validate_repository.freshness_issue_options_are_safe(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    "--title",
                    "title",
                    "--body-file",
                    "report.md",
                ],
                1,
                "create",
            )
        )
        for definition in (
            "gh() { return 1; }",
            "gh ( ) { return 1; }",
            "function gh { return 1; }",
            "alias gh='echo shadowed'",
            "set-alias gh echo",
            "declare -fx gh",
            "PATH=/tmp/fake:$PATH",
            "export PATH",
            "BASH_ENV=/tmp/fake.sh",
            "ENV=/tmp/fake.sh",
            "hash -p /tmp/fake/gh gh",
            "read CHECKER_EXIT",
            "mapfile -t CHECKER_EXIT <<< 0",
            "printf -v CHECKER_EXIT 0",
            "typeset CHECKER_EXIT",
            "printf 'PATH=/tmp/fake\\n' >> $GITHUB_ENV",
            "printf '/tmp/fake\\n' >> $GITHUB_PATH",
            "title='${{ secrets.TOP_SECRET }}'",
            "printf '%s\\n' \"$GH_TOKEN\"",
            "secret_copy=$GITHUB_TOKEN",
            "gh auth token",
            "gh issue list",
            "grep secret /etc/passwd",
            "mapfile -t other <<< value",
            "printf '%s' \"${!secret_name}\"",
            "printf '%n' CHECKER_EXIT",
            "printf '%s' \"${CHECKER_EXIT:=0}\"",
            "fmt='%n'\nprintf \"$fmt\" CHECKER_EXIT",
            "printf %$fmt CHECKER_EXIT",
            'printf -v "$target" 0',
            "printf --",
            "printf `format` value",
            "exit 2",
            'gh issue close "${issue_numbers[0]}" --repo repo --comment clean --delete-branch',
            "gh issue create --repo repo --title title --body-file report.md --project 1",
            'gh issue create --repo repo --title "$UNTRUSTED_TITLE" --body-file report.md',
        ):
            with self.subTest(shell_definition=definition):
                self.assertFalse(
                    validate_repository.freshness_shell_definitions_are_safe(definition)
                )
        self.assertFalse(
            validate_repository.freshness_shell_definitions_are_safe(
                "echo 'unterminated"
            )
        )
        self.assertTrue(
            validate_repository.freshness_checker_result_controls_reconciliation(
                contract_job_text
            )
        )
        create_command = (
            "gh issue create \\\n"
            '    --repo "github.com/$GITHUB_REPOSITORY" \\\n'
            '    --title "$title" \\\n'
            '    --body-file "$RUNNER_TEMP/freshness.md"'
        )
        duplicate_create = contract_job_text.replace(
            create_command, create_command + "; " + create_command, 1
        )
        self.assertFalse(
            validate_repository.freshness_checker_result_controls_reconciliation(
                duplicate_create
            )
        )
        self.assertFalse(
            validate_repository.freshness_checker_result_controls_reconciliation(
                contract_job_text + "\ngh --hostname github.com issue close 1"
            )
        )
        checker_flow_cases = (
            (
                "missing clean status",
                contract_job_text.replace(
                    "if [[ \"$CHECKER_EXIT\" == '0' ]]; then",
                    "if true; then",
                    1,
                ),
            ),
            (
                "missing stale status",
                contract_job_text.replace(
                    "if [[ \"$CHECKER_EXIT\" != '0' ]]; then",
                    "if true; then",
                    1,
                ),
            ),
            (
                "marker check after clean branch",
                contract_job_text.replace(
                    'grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"\n',
                    "",
                    1,
                ).replace(
                    "if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n",
                    "if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n"
                    '  grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"\n',
                    1,
                ),
            ),
            (
                "clean close missing",
                contract_job_text.replace(
                    f'gh issue close "{issue_id}"',
                    "echo closed",
                    1,
                ),
            ),
            (
                "clean exit missing",
                contract_job_text.replace("exit 0\n", "", 1),
            ),
            (
                "stale failure exit missing",
                contract_job_text.replace(
                    "if [[ \"$CHECKER_EXIT\" != '0' ]]; then\n  exit 1\nfi\n",
                    "",
                    1,
                ),
            ),
            (
                "ambiguous issue command",
                contract_job_text.replace(
                    f'gh issue close "{issue_id}"',
                    f'gh --hostname github.com issue close "{issue_id}"',
                    1,
                ),
            ),
            ("incomplete issue command", contract_job_text + "\ngh issue"),
            ("malformed shell", contract_job_text + "\necho 'unterminated"),
            (
                "close outside clean branch",
                contract_job_text.replace("gh issue close", "echo closed", 1).replace(
                    "  exit 0\nfi\ngrep -Fq",
                    "  exit 0\nfi\ngh issue close 1\ngrep -Fq",
                    1,
                ),
            ),
            ("unmatched shell block", contract_job_text + "\nif true"),
            ("unsupported issue mutation", contract_job_text + "\ngh issue reopen 1"),
            ("missing issue argument", contract_job_text + "\ngh issue close"),
            (
                "title overwritten",
                contract_job_text.replace(
                    "title='Repository freshness update required'\n",
                    "title='Repository freshness update required'\ntitle=attacker\n",
                    1,
                ),
            ),
            (
                "title assignment missing",
                contract_job_text.replace(
                    "title='Repository freshness update required'\n", "", 1
                ),
            ),
            (
                "issue number seeded",
                contract_job_text.replace(
                    "issue_numbers=()\n", "issue_numbers=99\n", 1
                ),
            ),
            (
                "issue number reseeded",
                contract_job_text.replace(
                    "issue_numbers=()\n",
                    "issue_numbers=()\nissue_numbers=99\n",
                    1,
                ),
            ),
            (
                "late issue number initialization",
                contract_job_text.replace("issue_numbers=()\n", "", 1)
                + "\nissue_numbers=()\n",
            ),
            (
                "late title assignment",
                contract_job_text.replace(
                    "title='Repository freshness update required'\n", "", 1
                )
                + "\ntitle='Repository freshness update required'\n",
            ),
        )
        for name, command in checker_flow_cases:
            with self.subTest(checker_flow=name):
                self.assertFalse(
                    validate_repository.freshness_checker_result_controls_reconciliation(
                        command
                    )
                )
        jobs: tuple[object, ...] = (
            None,
            {"if": "false", "steps": []},
            {"continue-on-error": "true", "steps": []},
            {"needs": "gate", "steps": []},
            {"strategy": {"matrix": {"item": ["one", "two"]}}, "steps": []},
            {"environment": "production", "steps": []},
            {"concurrency": {"group": "other"}, "steps": []},
            {"steps": {}},
            {"steps": [None]},
            {"steps": [{"if": "false"}]},
            {"steps": [{"continue-on-error": "true"}]},
            {"steps": [{"background": "true"}]},
            {"steps": [{"parallel": []}]},
            {"steps": [{"wait": "audit"}]},
            {"steps": [{"wait-all": "true"}]},
            {"steps": [{"cancel": "audit"}]},
            {"steps": [{"timeout-minutes": "1"}]},
            {"snapshot": "freshness-image", "steps": []},
            {"cache-mode": "write", "steps": []},
        )
        for job in jobs:
            with self.subTest(unconditional_job=job):
                self.assertFalse(
                    validate_repository.freshness_job_execution_is_unconditional(job)
                )
        for field, value in (
            ("if", "false"),
            ("continue-on-error", "true"),
            ("needs", "gate"),
            ("strategy", {"matrix": {"item": ["one", "two"]}}),
            ("environment", "production"),
            ("concurrency", {"group": "other"}),
        ):
            candidate = validate_repository.load_yaml_text(contract_text)
            candidate["jobs"]["audit"][field] = value
            with self.subTest(unconditional_field=field):
                self.assertFalse(
                    validate_repository.has_freshness_job_reconciliation(
                        candidate, contract_text
                    )
                )
        for step_field, step_value in (
            ("if", "false"),
            ("continue-on-error", "true"),
            ("background", "true"),
            ("parallel", []),
            ("wait", "audit"),
            ("wait-all", "true"),
            ("cancel", "audit"),
            ("timeout-minutes", "1"),
        ):
            candidate = validate_repository.load_yaml_text(contract_text)
            candidate["jobs"]["audit"]["steps"][0][step_field] = step_value
            with self.subTest(unconditional_step_field=step_field):
                self.assertFalse(
                    validate_repository.has_freshness_job_reconciliation(
                        candidate, contract_text
                    )
                )
        ignored_api_output_text = contract_text.replace(
            '          if [[ -n "$issue_numbers_output" ]]; then\n'
            '            mapfile -t issue_numbers <<< "$issue_numbers_output"\n'
            "          fi\n",
            "",
            1,
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                validate_repository.load_yaml_text(ignored_api_output_text),
                ignored_api_output_text,
            )
        )
        logged_api_output_text = contract_text.replace(
            '            mapfile -t issue_numbers <<< "$issue_numbers_output"\n',
            '            echo "$issue_numbers_output"\n',
            1,
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                validate_repository.load_yaml_text(logged_api_output_text),
                logged_api_output_text,
            )
        )
        contents_none_workflow_text = contract_text.replace(
            "    timeout-minutes: 15\n",
            "    timeout-minutes: 15\n"
            "    permissions:\n"
            "      contents: none\n"
            "      issues: write\n",
            1,
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                validate_repository.load_yaml_text(contents_none_workflow_text),
                contents_none_workflow_text,
            )
        )
        early_mutation = (
            '          gh issue create --repo "github.com/$GITHUB_REPOSITORY" '
            '--title "$title" --body-file "$RUNNER_TEMP/freshness.md"\n'
        )
        mutation_before_audit_text = contract_text.replace(
            "          python scripts/audit_freshness.py \\\n",
            early_mutation + "          python scripts/audit_freshness.py \\\n",
            1,
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                validate_repository.load_yaml_text(mutation_before_audit_text),
                mutation_before_audit_text,
            )
        )
        wrong_repository_text = contract_text.replace(
            '--repo "github.com/$GITHUB_REPOSITORY"',
            '--repo "attacker/repository"',
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                validate_repository.load_yaml_text(wrong_repository_text),
                wrong_repository_text,
            )
        )
        modified_text = contract_text.replace(
            '--body-file "$RUNNER_TEMP/freshness.md"',
            '--body-file "$RUNNER_TEMP/other.md"',
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                validate_repository.load_yaml_text(modified_text), modified_text
            )
        )
        workflow_with_noop = dict(contract_workflow)
        workflow_with_noop["jobs"] = {
            **contract_workflow["jobs"],
            "noop": {"steps": []},
        }
        self.assertTrue(
            validate_repository.has_freshness_job_reconciliation(
                workflow_with_noop, contract_text
            )
        )
        global_api_workflow_text = (
            contract_text
            + "\n"
            + "  hidden-api:\n"
            + "    steps:\n"
            + "      - run: gh --hostname github.com api "
            + "\"repos/attacker/repository/issues\" --jq '.number'\n"
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                validate_repository.load_yaml_text(global_api_workflow_text),
                global_api_workflow_text,
            )
        )
        workflow_with_mutation_without_lookup = dict(contract_workflow)
        workflow_with_mutation_without_lookup["jobs"] = {
            **contract_workflow["jobs"],
            "mutation": {
                "steps": [
                    {
                        "run": (
                            'gh issue create --repo "github.com/$GITHUB_REPOSITORY" '
                            "--title reminder --body-file report.md"
                        )
                    }
                ]
            },
        }
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                workflow_with_mutation_without_lookup, contract_text
            )
        )
        workflow_with_read_only_mutation = dict(contract_workflow)
        workflow_with_read_only_mutation["jobs"] = {
            **contract_workflow["jobs"],
            "audit": {
                **contract_workflow["jobs"]["audit"],
                "permissions": {"issues": "read"},
            },
        }
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                workflow_with_read_only_mutation, contract_text
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                {
                    "permissions": {"contents": "read", "issues": "write"},
                    "jobs": [],
                },
                contract_text,
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(None, contract_text)
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                {
                    "permissions": {"contents": "read", "issues": "write"},
                    "jobs": {"audit": {"steps": [{}]}},
                },
                "echo ready",
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                {
                    "permissions": {"contents": "read", "issues": "write"},
                    "jobs": {"audit": "not-a-job"},
                },
                contract_text,
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                {
                    "permissions": {"contents": "read", "issues": "write"},
                    "jobs": {
                        "audit": {"steps": [{"run": "gh issue create 'unterminated"}]}
                    },
                },
                contract_text,
            )
        )
        self.assertEqual(
            validate_repository.issue_subcommand_positions(
                ["gh", "--repo=r", "issue", "create"]
            ),
            (2,),
        )
        self.assertEqual(
            validate_repository.issue_subcommand_positions(
                ["/usr/bin/gh", "issue", "create"]
            ),
            (1,),
        )
        self.assertEqual(
            validate_repository.issue_subcommand_positions(
                ["gh.exe", "issue", "create"]
            ),
            (1,),
        )
        self.assertIsNone(
            validate_repository.issue_subcommand_positions(
                ["gh", "issue", "create", "gh", "issue", "close"]
            )
        )
        self.assertIsNone(
            validate_repository.reminder_issue_mutation_blocks("echo gh issue create")
        )
        self.assertIsNone(
            validate_repository.reminder_issue_mutation_blocks(
                "gh issue create gh issue close"
            )
        )
        no_permission_workflow = {
            "permissions": {"contents": "read", "issues": "write"},
            "jobs": {
                "audit": {
                    "permissions": {"contents": "read"},
                    "steps": [
                        {
                            "run": (
                                "gh issue create --repo r --title t "
                                "--body-file report.md"
                            )
                        }
                    ],
                }
            },
        }
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                no_permission_workflow,
                "gh issue create --repo r --title t --body-file report.md",
            )
        )
        self.assertFalse(validate_repository.permissions_grant_issue_write(None))
        self.assertFalse(
            validate_repository.permissions_grant_issue_write({"permissions": {}})
        )
        self.assertTrue(
            validate_repository.permissions_grant_issue_write(
                {"permissions": "write-all"}
            )
        )
        self.assertTrue(
            validate_repository.permissions_grant_issue_write(
                {"permissions": {"issues": "write"}}
            )
        )
        self.assertFalse(
            validate_repository.permissions_grant_issue_write(
                {"permissions": {"issues": "read"}}
            )
        )
        self.assertFalse(validate_repository.job_effective_issue_write(None, {}))
        self.assertFalse(validate_repository.job_effective_issue_write({}, None))
        self.assertTrue(
            validate_repository.job_effective_issue_write(
                {"permissions": {"issues": "write"}}, {}
            )
        )
        self.assertTrue(
            validate_repository.job_effective_issue_write(
                {}, {"permissions": "write-all"}
            )
        )
        self.assertFalse(
            validate_repository.job_effective_issue_write(
                {"permissions": {"issues": "write"}},
                {"permissions": {"issues": "read"}},
            )
        )
        workflow_permissions = {"permissions": {"contents": "read", "issues": "write"}}
        self.assertFalse(validate_repository.job_effective_contents_read(None, {}))
        self.assertFalse(validate_repository.job_effective_contents_read({}, None))
        self.assertTrue(
            validate_repository.job_effective_contents_read(workflow_permissions, {})
        )
        self.assertTrue(
            validate_repository.job_effective_contents_read(
                workflow_permissions,
                {"permissions": {"contents": "read", "issues": "write"}},
            )
        )
        self.assertFalse(
            validate_repository.job_effective_contents_read(
                workflow_permissions, {"permissions": {"issues": "write"}}
            )
        )

    def test_freshness_contract_rejects_runtime_and_shell_bypasses(self) -> None:
        workflow_path = PLUGIN_ROOT / ".github/workflows/freshness.yml"
        contract_text = workflow_path.read_text(encoding="utf-8")
        contract_workflow = validate_repository.load_yaml_text(contract_text)
        contract_job = contract_workflow["jobs"]["audit"]

        for name, mutate in (
            (
                "workflow container",
                lambda candidate: candidate.update({"container": "evil:latest"}),
            ),
            (
                "job container",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"container": "evil:latest"}
                ),
            ),
            (
                "job service",
                lambda candidate: candidate["jobs"]["audit"].update(
                    {"services": {"evil": {"image": "evil:latest"}}}
                ),
            ),
            (
                "unreviewed action",
                lambda candidate: candidate["jobs"]["audit"]["steps"].append(
                    {"uses": "evil/action@" + "a" * 40}
                ),
            ),
            (
                "unpinned freshness action",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0].update(
                    {"uses": "actions/checkout@main"}
                ),
            ),
            (
                "checkout repository override",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0]["with"].update(
                    {"repository": "attacker/repo"}
                ),
            ),
            (
                "checkout ref override",
                lambda candidate: candidate["jobs"]["audit"]["steps"][0]["with"].update(
                    {"ref": "attacker"}
                ),
            ),
            (
                "setup Python override",
                lambda candidate: candidate["jobs"]["audit"]["steps"][1]["with"].update(
                    {"python-version": "attacker"}
                ),
            ),
        ):
            candidate = validate_repository.load_yaml_text(contract_text)
            mutate(candidate)
            with self.subTest(runtime_context=name):
                self.assertFalse(
                    validate_repository.freshness_execution_context_is_bash(
                        candidate, candidate["jobs"]["audit"]
                    )
                )

        for variable in (
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONSTARTUP",
            "GITHUB_STEP_SUMMARY",
            "GITHUB_STATE",
        ):
            candidate = validate_repository.load_yaml_text(contract_text)
            candidate["jobs"]["audit"]["steps"][0]["env"] = {variable: "attacker"}
            with self.subTest(protected_environment=variable):
                if variable in {"GITHUB_TOKEN", "GH_TOKEN"}:
                    self.assertFalse(
                        validate_repository.freshness_authentication_bindings_are_safe(
                            candidate, candidate["jobs"]["audit"]
                        )
                    )
                else:
                    self.assertFalse(
                        validate_repository.freshness_checker_result_binding_is_safe(
                            candidate, candidate["jobs"]["audit"]
                        )
                    )

        audit_step = next(
            step for step in contract_job["steps"] if step.get("id") == "audit"
        )
        audit_run = audit_step["run"]
        for name, replacement in (
            (
                "JSON output outside runner temp",
                ("$RUNNER_TEMP/freshness.json", "report.json"),
            ),
            (
                "Markdown output outside runner temp",
                ("$RUNNER_TEMP/freshness.md", "report.md"),
            ),
        ):
            with self.subTest(report_path=name):
                self.assertFalse(
                    validate_repository.freshness_checker_result_output_is_safe(
                        audit_run.replace(*replacement, 1)
                    )
                )

        for definition in (
            "GH_TOKEN=attacker",
            "GITHUB_TOKEN=attacker",
            "export GH_TOKEN=attacker",
            "OTHER=value echo",
            "PYTHONPATH=/tmp/evil",
            "python -c \"import subprocess; subprocess.run(['gh','issue','close','999'])\"",
            "node -e \"require('child_process').execFileSync('gh',['issue','close','999'])\"",
            "awk 'BEGIN { system(\"gh issue close 999\") }'",
            "./mutate_issue",
        ):
            with self.subTest(shell_definition=definition):
                self.assertFalse(
                    validate_repository.freshness_shell_definitions_are_safe(definition)
                )

        contract_job_text = "\n".join(
            step["run"]
            for step in contract_job["steps"]
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        )
        binding_step = next(
            step
            for step in contract_job["steps"]
            if isinstance(step.get("env"), dict) and "CHECKER_EXIT" in step["env"]
        )
        binding_run = binding_step["run"]
        parameter_expansion_bypass = binding_run.replace(
            "--comment 'The scheduled freshness audit is clean, so this reminder is closing automatically.'",
            "--comment \\\n# hidden continuation\n"
            "curl${IFS}touch${IFS}/tmp/freshness-preflight-bypass",
            1,
        )
        self.assertFalse(
            validate_repository.freshness_shell_definitions_are_safe(
                parameter_expansion_bypass
            )
        )
        _bypass_workflow = validate_repository.load_yaml_text(
            contract_text.replace(
                "--comment 'The scheduled freshness audit is clean, so this reminder is closing automatically.'",
                "--comment \\\n                # hidden continuation\n"
                "                curl${IFS}touch${IFS}/tmp/freshness-preflight-bypass",
                1,
            )
        )
        self.assertTrue(
            validate_repository.freshness_reconciliation_shell_options_are_safe(
                binding_run
            )
        )
        for command in (
            binding_run.replace("set -euo pipefail\n", "", 1),
            binding_run.replace(
                "set -euo pipefail\n", "set +e\nset -euo pipefail\n", 1
            ),
            binding_run.replace("set -euo pipefail", "set -e", 1),
            binding_run.replace(
                "marker='<!-- repo-scaffold-freshness-audit -->'",
                'printf "fake" > "$RUNNER_TEMP/freshness.md"\n'
                "marker='<!-- repo-scaffold-freshness-audit -->'",
                1,
            ),
            "",
            "echo 'unterminated",
        ):
            with self.subTest(reconciliation_options=command):
                self.assertFalse(
                    validate_repository.freshness_reconciliation_shell_options_are_safe(
                        command
                    )
                )
        for variable in ("GIT_SSH_COMMAND", "LD_PRELOAD", "GH_CONFIG_DIR", "HOME"):
            command = binding_run.replace(
                "gh api --hostname github.com",
                f"{variable}=/tmp/fake gh api --hostname github.com",
                1,
            )
            with self.subTest(shell_environment=variable):
                self.assertFalse(
                    validate_repository.freshness_shell_definitions_are_safe(command)
                )
        self.assertTrue(
            validate_repository.freshness_shell_control_flow_is_safe(contract_job_text)
        )
        expression_workflow = validate_repository.load_yaml_text(
            contract_text.replace(
                "title='Repository freshness update required'",
                "title='${{ secrets.TOP_SECRET }}'",
                1,
            )
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                expression_workflow,
                contract_text.replace(
                    "title='Repository freshness update required'",
                    "title='${{ secrets.TOP_SECRET }}'",
                    1,
                ),
            )
        )
        token_text = contract_text.replace(
            "--comment 'The scheduled freshness audit is clean, so this reminder is closing automatically.'",
            '--comment "$GH_TOKEN"',
            1,
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                validate_repository.load_yaml_text(token_text), token_text
            )
        )
        negated_api_text = contract_text.replace(
            "gh api --hostname github.com",
            "! gh api --hostname github.com",
            1,
        )
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                validate_repository.load_yaml_text(negated_api_text), negated_api_text
            )
        )
        hidden_job_workflow = validate_repository.load_yaml_text(contract_text)
        hidden_job_workflow["jobs"]["hidden"] = {
            "steps": [
                {
                    "run": "python -c \"import subprocess; subprocess.run(['gh','issue','close','999'])\""
                }
            ]
        }
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                hidden_job_workflow, contract_text
            )
        )
        duplicate_guard = (
            "          if (( ${#issue_numbers[@]} > 1 )); then\n"
            "            printf 'Found multiple open freshness reminder issues.\\n' >&2\n"
            "            exit 1\n"
            "          fi\n"
        )
        flow_bypasses = (
            (
                "clean nested condition",
                contract_text.replace(
                    "if (( ${#issue_numbers[@]} == 1 )); then",
                    "if false; then",
                    1,
                ),
            ),
            (
                "stale nested condition",
                contract_text.replace(
                    "if (( ${#issue_numbers[@]} == 1 )); then",
                    "if false; then",
                    2,
                ),
            ),
            (
                "duplicate issue guard missing",
                contract_text.replace(duplicate_guard, "", 1),
            ),
            (
                "duplicate issue guard does not exit",
                contract_text.replace(
                    "            exit 1\n          fi\n",
                    "            :\n          fi\n",
                    1,
                ),
            ),
            (
                "clean cardinality guard missing",
                contract_text.replace(
                    "            if (( ${#issue_numbers[@]} == 1 )); then\n",
                    "",
                    1,
                ),
            ),
            (
                "stale cardinality guard missing",
                contract_text.replace(
                    "          if (( ${#issue_numbers[@]} == 1 )); then\n",
                    "",
                    1,
                ),
            ),
            (
                "marker check uses another variable",
                contract_text.replace(
                    'grep -Fq "$marker" "$RUNNER_TEMP/freshness.md"',
                    'grep -Fq "$title" "$RUNNER_TEMP/freshness.md"',
                    1,
                ),
            ),
            (
                "marker is reassigned",
                contract_text.replace(
                    "          marker='<!-- repo-scaffold-freshness-audit -->'\n",
                    "          marker='<!-- repo-scaffold-freshness-audit -->'\n"
                    "          marker=attacker\n",
                    1,
                ),
            ),
            (
                "unreviewed command substitution",
                contract_text.replace(
                    "          marker='<!-- repo-scaffold-freshness-audit -->'\n",
                    '          printf "%s" "$(./mutate_issue)"\n'
                    "          marker='<!-- repo-scaffold-freshness-audit -->'\n",
                    1,
                ),
            ),
            (
                "loop around mutation",
                contract_text.replace(
                    "            gh issue close",
                    "            for item in; do\n            gh issue close",
                    1,
                ).replace(
                    "            --comment 'The scheduled freshness audit is clean, so this reminder is closing automatically.'",
                    "            --comment 'The scheduled freshness audit is clean, so this reminder is closing automatically.'\n            done",
                    1,
                ),
            ),
            (
                "unmatched closing shell block",
                contract_job_text + "\nfi",
            ),
            (
                "unmatched opening shell block",
                contract_job_text.replace("fi\n", "", 1),
            ),
        )
        for name, candidate_text in flow_bypasses:
            if candidate_text.startswith("set "):
                job_text = candidate_text
            else:
                candidate = validate_repository.load_yaml_text(candidate_text)
                job_text = "\n".join(
                    step["run"]
                    for step in candidate["jobs"]["audit"]["steps"]
                    if isinstance(step, dict) and isinstance(step.get("run"), str)
                )
            with self.subTest(flow_bypass=name):
                self.assertFalse(
                    validate_repository.freshness_checker_result_controls_reconciliation(
                        job_text
                    )
                )

        lookup = (
            'output=$(gh api)\nmapfile -t ids <<< "$output"\n'
            'gh issue edit "${ids[0]:-999}" --repo r --body-file report.md'
        )
        self.assertFalse(
            validate_repository.freshness_api_result_controls_issue_selection(lookup)
        )
        transformed_lookup = lookup.replace(":-999", "//1/999")
        self.assertFalse(
            validate_repository.freshness_api_result_controls_issue_selection(
                transformed_lookup
            )
        )

    def test_freshness_defensive_helpers_and_workflow_shapes_fail_closed(self) -> None:
        action_step_cases: tuple[object, ...] = (
            None,
            {},
            [None],
            [{"uses": 1}],
            [{"uses": "one@two@three"}],
        )
        for steps in action_step_cases:
            with self.subTest(action_steps=steps):
                self.assertFalse(
                    validate_repository.freshness_action_steps_are_safe(steps)
                )
        self.assertTrue(
            validate_repository.freshness_action_steps_are_safe([{"run": "echo"}])
        )
        for (
            repository,
            reference,
        ) in validate_repository.FRESHNESS_REVIEWED_ACTION_REFERENCES.items():
            with self.subTest(reviewed_action=repository):
                step = {
                    "uses": reference,
                    "with": validate_repository.FRESHNESS_ALLOWED_ACTION_INPUTS[
                        repository
                    ],
                }
                self.assertTrue(
                    validate_repository.freshness_action_steps_are_safe([step])
                )
                step["uses"] = f"{repository}@{'a' * 40}"
                self.assertFalse(
                    validate_repository.freshness_action_steps_are_safe([step])
                )

        for definition in (
            "alias gh='echo shadowed'",
            "declare -fx gh",
            "function gh { return 1; }",
            "gh ( ) { return 1; }",
        ):
            with self.subTest(shell_definition=definition):
                self.assertFalse(
                    validate_repository.freshness_shell_definitions_are_safe(definition)
                )
        with (
            mock.patch.object(
                validate_repository,
                "shell_command_segments",
                return_value=[["echo", "ready"]],
            ),
            mock.patch.object(
                validate_repository.shlex,
                "shlex",
                side_effect=ValueError("malformed"),
            ),
        ):
            self.assertFalse(
                validate_repository.freshness_shell_definitions_are_safe("ignored")
            )

        variable_reference_cases = (
            ("$NAME", True),
            ("${NAME}", True),
            ("${NAMEevil}", False),
            ("$NAME-suffix", True),
            ("$NAMEevil", False),
        )
        for token, expected in variable_reference_cases:
            with self.subTest(variable_reference=token):
                self.assertEqual(
                    validate_repository.freshness_variable_reference(token, "NAME"),
                    expected,
                )

        workflow_path = PLUGIN_ROOT / ".github/workflows/freshness.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        workflow = validate_repository.load_yaml_text(workflow_text)
        contract_job_text = "\n".join(
            step["run"]
            for step in workflow["jobs"]["audit"]["steps"]
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        )
        self.assertTrue(
            validate_repository.freshness_summary_output_is_safe(contract_job_text)
        )
        self.assertFalse(
            validate_repository.freshness_summary_output_is_safe("echo 'unterminated")
        )
        self.assertFalse(
            validate_repository.freshness_summary_output_is_safe(
                contract_job_text.replace(
                    'cat "$RUNNER_TEMP/freshness.md" >> "$GITHUB_STEP_SUMMARY"',
                    'cat "$RUNNER_TEMP/other.md" >> "$GITHUB_STEP_SUMMARY"',
                    1,
                )
            )
        )
        duplicate_guard = (
            "\n".join(
                (
                    "if (( ${#issue_numbers[@]} > 1 )); then",
                    "  printf 'Found multiple open freshness reminder issues.\\n' >&2",
                    "  exit 1",
                    "fi",
                )
            )
            + "\n"
        )
        clean_condition = "if [[ \"$CHECKER_EXIT\" == '0' ]]; then\n"
        out_of_order = contract_job_text.replace(duplicate_guard, "", 1).replace(
            clean_condition, clean_condition + duplicate_guard, 1
        )
        self.assertFalse(
            validate_repository.freshness_shell_control_flow_is_safe(out_of_order)
        )
        nonempty_guard = (
            "\n".join(
                (
                    'if [[ -n "$issue_numbers_output" ]]; then',
                    '  mapfile -t issue_numbers <<< "$issue_numbers_output"',
                    "fi",
                )
            )
            + "\n"
        )
        nonempty_after_clean = contract_job_text.replace(nonempty_guard, "", 1).replace(
            clean_condition, clean_condition + nonempty_guard, 1
        )
        self.assertFalse(
            validate_repository.freshness_shell_control_flow_is_safe(
                nonempty_after_clean
            )
        )

        with (
            mock.patch.object(
                validate_repository,
                "freshness_shell_control_flow_is_safe",
                return_value=True,
            ),
            mock.patch.object(
                validate_repository, "freshness_marker_check_is_safe", return_value=True
            ),
            mock.patch.object(
                validate_repository, "shell_command_segments", return_value=None
            ),
        ):
            self.assertFalse(
                validate_repository.freshness_checker_result_controls_reconciliation(
                    "ignored"
                )
            )
        with (
            mock.patch.object(
                validate_repository,
                "freshness_shell_control_flow_is_safe",
                return_value=True,
            ),
            mock.patch.object(
                validate_repository, "freshness_marker_check_is_safe", return_value=True
            ),
            mock.patch.object(
                validate_repository,
                "shell_command_segments",
                return_value=[["echo", "ready"]],
            ),
            mock.patch.object(
                validate_repository,
                "freshness_shell_if_block_ranges",
                return_value=None,
            ),
        ):
            self.assertFalse(
                validate_repository.freshness_checker_result_controls_reconciliation(
                    "ignored"
                )
            )
        with (
            mock.patch.object(
                validate_repository,
                "freshness_shell_control_flow_is_safe",
                return_value=True,
            ),
            mock.patch.object(
                validate_repository, "freshness_marker_check_is_safe", return_value=True
            ),
            mock.patch.object(
                validate_repository,
                "shell_command_segments",
                return_value=[["gh", "issue", "create", "gh", "issue", "close"]],
            ),
            mock.patch.object(
                validate_repository,
                "freshness_shell_if_block_ranges",
                return_value={},
            ),
        ):
            self.assertFalse(
                validate_repository.freshness_checker_result_controls_reconciliation(
                    "ignored"
                )
            )

        preconditions = {
            name: mock.Mock(return_value=True)
            for name in (
                "has_least_privileged_freshness_permissions",
                "has_repository_root_working_directory",
                "has_direct_freshness_jobs",
                "has_freshness_repository_context",
                "has_repo_bound_issue_reconciliation",
                "has_freshness_repository_api_reads",
            )
        }
        with mock.patch.multiple(validate_repository, **preconditions):
            self.assertFalse(
                validate_repository.has_freshness_job_reconciliation(
                    {"jobs": {"audit": {"steps": {}}}}, ""
                )
            )
        summary_candidate = validate_repository.load_yaml_text(workflow_text)
        summary_step = next(
            step
            for step in summary_candidate["jobs"]["audit"]["steps"]
            if step.get("name") == "Add report to job summary"
        )
        summary_step["run"] = 'cat "$RUNNER_TEMP/other.md" >> "$GITHUB_STEP_SUMMARY"'
        self.assertFalse(
            validate_repository.has_freshness_job_reconciliation(
                summary_candidate, workflow_text
            )
        )
        with (
            mock.patch.multiple(validate_repository, **preconditions),
            mock.patch.object(
                validate_repository, "reminder_issue_mutation_blocks", return_value=None
            ),
        ):
            self.assertFalse(
                validate_repository.has_freshness_job_reconciliation(
                    {"jobs": {"audit": {"steps": []}}}, ""
                )
            )

        container_document_cases: tuple[object, ...] = (
            None,
            [],
            {"jobs": []},
            {"jobs": {"invalid": None}},
        )
        for document in container_document_cases:
            with self.subTest(container_document=document):
                self.assertEqual(
                    validate_repository.validate_job_container_images(
                        document, Path("workflow.yml")
                    ),
                    [],
                )

    def test_freshness_reconciliation_requires_effective_permission_and_repo_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            installed = root / ".github/workflows/freshness.yml"
            workflow_text = installed.read_text(encoding="utf-8")
            workflow_text = workflow_text.replace(
                "  audit:\n    name: freshness-audit\n",
                "  audit:\n"
                "    name: freshness-audit\n"
                "    permissions:\n"
                "      contents: read\n",
                1,
            ).replace(
                '--repo "github.com/$GITHUB_REPOSITORY"',
                '--repository "github.com/$GITHUB_REPOSITORY"',
            )
            installed.write_text(workflow_text, encoding="utf-8")
            problems = validate_repository.validate_freshness_tracking_contract(root)
            self.copy_contract(root)
            installed = root / ".github/workflows/freshness.yml"
            contents_none_text = installed.read_text(encoding="utf-8").replace(
                "    timeout-minutes: 15\n",
                "    timeout-minutes: 15\n"
                "    permissions:\n"
                "      contents: none\n"
                "      issues: write\n",
                1,
            )
            installed.write_text(contents_none_text, encoding="utf-8")
            contents_problems = (
                validate_repository.validate_freshness_tracking_contract(root)
            )

        self.assertTrue(
            any(
                "audit job must have effective issues: write permission" in problem
                for problem in problems
            )
        )
        self.assertTrue(
            any(
                "reminder mutations must bind the current GitHub repository" in problem
                for problem in problems
            )
        )
        self.assertTrue(
            any(
                "audit job must have effective contents: read permission" in problem
                for problem in contents_problems
            )
        )

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
            "repository-scoped",
            "reconcile one marker issue",
            "workflow must be a mapping",
        ):
            self.assertTrue(any(fragment in problem for problem in problems), fragment)
        self.assertTrue(
            any("workflow must match" in problem for problem in workflow_drift)
        )


class OfficialDocumentationTrackingContractTests(unittest.TestCase):
    def copy_contract(self, root: Path) -> None:
        for relative in (
            ".github/official-docs-trackers.json",
            ".github/workflows/ci.yml",
            ".github/workflows/official-docs.yml",
            "scripts/audit_official_docs.py",
        ):
            source = PLUGIN_ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    def test_current_official_documentation_contract_is_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_official_docs_tracking_contract(PLUGIN_ROOT),
            [],
        )

    def test_reconciliation_job_must_keep_effective_issue_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            workflow = root / ".github/workflows/official-docs.yml"
            workflow_text = workflow.read_text(encoding="utf-8").replace(
                "  audit:\n    name: official-docs-review\n",
                "  audit:\n"
                "    name: official-docs-review\n"
                "    permissions:\n"
                "      contents: read\n",
                1,
            )
            workflow.write_text(workflow_text, encoding="utf-8")
            problems = validate_repository.validate_official_docs_tracking_contract(
                root
            )

        self.assertTrue(
            any(
                "audit job must have effective issues: write permission" in problem
                for problem in problems
            )
        )

    def test_critical_policy_claims_must_track_every_affected_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            registry_path = root / ".github" / "official-docs-trackers.json"
            cases = {
                "github-actions-dependabot": "skills/repo-scaffold/assets/dependabot.yml",
                "github-dependabot-auto-merge": "skills/repo-scaffold/assets/workflows/dependabot-auto-merge.yml",
                "github-dependency-review": "skills/repo-scaffold/scripts/dependency_review_preflight.py",
                "github-dependency-graph-sbom-api": "skills/repo-scaffold/scripts/dependency_review_preflight.py",
                "github-actions-permissions-api": [
                    "skills/repo-scaffold/scripts/workflow_installation_preflight.py",
                    "skills/repo-scaffold/scripts/advanced_codeql_preflight.py",
                    "skills/repo-scaffold/scripts/scorecard_preflight.py",
                ],
                "github-actions-workflow-permissions-syntax": "skills/repo-scaffold/scripts/workflow_installation_preflight.py",
                "github-actions-workflow-runs-api": "skills/repo-scaffold/references/github-setup.md",
                "github-codeql-advanced-setup": "skills/repo-scaffold/scripts/advanced_codeql_preflight.py",
                "github-codeql-default-setup-api": "skills/repo-scaffold/scripts/advanced_codeql_preflight.py",
                "github-code-scanning-sarif-upload": "skills/repo-scaffold/scripts/scorecard_preflight.py",
                "github-code-scanning-alerts-api": [
                    "scripts/check_code_scanning_alerts.py",
                    "skills/repo-scaffold/scripts/codeql_preflight.py",
                ],
                "github-repository-contents-api": "skills/repo-scaffold/scripts/codeql_preflight.py",
                "github-community-profile-metrics-api": [
                    "skills/repo-scaffold/scripts/check_community_health.py",
                    "skills/repo-scaffold/references/github-setup.md",
                ],
                "github-repository-license-api": "skills/repo-scaffold/references/github-setup.md",
                "github-action-pin-repository-tags-api": "skills/repo-scaffold/scripts/sync_action_pins.py",
                "github-git-refs-api": "skills/repo-scaffold/assets/workflows/release.yml",
                "github-git-tags-api": "skills/repo-scaffold/assets/workflows/release.yml",
                "github-releases-api": "skills/repo-scaffold/scripts/ci_toolchain.py",
                "github-pull-requests-api": [
                    "scripts/check_code_scanning_alerts.py",
                    "skills/repo-scaffold/scripts/branch_protection_preflight.py",
                    "skills/repo-scaffold/references/github-setup.md",
                ],
                "github-git-commits-api": [
                    "scripts/check_code_scanning_alerts.py",
                ],
                "github-repository-commits-api": "skills/repo-scaffold/scripts/codeql_preflight.py",
                "github-community-health-branches-api": "skills/repo-scaffold/scripts/check_community_health.py",
                "github-community-health-git-trees-api": [
                    "skills/repo-scaffold/scripts/check_community_health.py",
                    "skills/repo-scaffold/scripts/branch_protection_preflight.py",
                    "skills/repo-scaffold/scripts/codeql_preflight.py",
                    "skills/repo-scaffold/references/github-setup.md",
                ],
                "github-git-blobs-api": [
                    "skills/repo-scaffold/scripts/branch_protection_preflight.py",
                    "skills/repo-scaffold/scripts/codeql_preflight.py",
                ],
                "github-check-runs-api": [
                    "skills/repo-scaffold/scripts/branch_protection_preflight.py",
                    "skills/repo-scaffold/references/github-setup.md",
                ],
                "github-commit-statuses-api": [
                    "skills/repo-scaffold/scripts/branch_protection_preflight.py",
                    "skills/repo-scaffold/references/github-setup.md",
                ],
                "github-reminder-issues-api": "skills/repo-scaffold/assets/workflows/freshness.yml",
                "github-repository-labels-api": "skills/repo-scaffold/references/github-setup.md",
                "github-branch-protection-status-checks": [
                    "README.md",
                    "skills/repo-scaffold/scripts/branch_protection_preflight.py",
                    "skills/repo-scaffold/scripts/merge_settings_preflight.py",
                ],
                "github-branches-api": [
                    "README.md",
                    "skills/repo-scaffold/scripts/merge_settings_preflight.py",
                ],
                "github-effective-branch-rules-api": [
                    "README.md",
                    "skills/repo-scaffold/scripts/branch_protection_preflight.py",
                    "skills/repo-scaffold/scripts/merge_settings_preflight.py",
                ],
                "github-merge-queue-auto-merge": [
                    "README.md",
                    "skills/repo-scaffold/assets/workflows/auto-merge.yml",
                ],
                "github-security-analysis-settings": "skills/repo-scaffold/scripts/security_features_preflight.py",
                "github-repository-security-features-api": [
                    "skills/repo-scaffold/references/github-setup.md",
                    "skills/repo-scaffold/scripts/security_features_preflight.py",
                ],
                "github-users-api": "skills/repo-scaffold/references/github-setup.md",
                "github-artifact-attestations": "skills/repo-scaffold/scripts/release_preflight.py",
                "github-actions-secrets-api": "skills/repo-scaffold/scripts/release_preflight.py",
                "github-repository-settings-api": [
                    "skills/repo-scaffold/scripts/repository_settings_preflight.py",
                    "skills/repo-scaffold/scripts/branch_protection_preflight.py",
                    "skills/repo-scaffold/scripts/codeql_preflight.py",
                    "skills/repo-scaffold/scripts/dependency_review_preflight.py",
                    "skills/repo-scaffold/scripts/advanced_codeql_preflight.py",
                    "skills/repo-scaffold/scripts/merge_settings_preflight.py",
                    "skills/repo-scaffold/scripts/release_preflight.py",
                    "skills/repo-scaffold/scripts/scorecard_preflight.py",
                    "skills/repo-scaffold/scripts/security_features_preflight.py",
                    "skills/repo-scaffold/scripts/workflow_installation_preflight.py",
                ],
            }
            for identifier, removed_paths in cases.items():
                for removed_path in (
                    removed_paths
                    if isinstance(removed_paths, list)
                    else [removed_paths]
                ):
                    registry = validate_repository.load_json(registry_path)
                    claim = next(
                        item for item in registry["claims"] if item["id"] == identifier
                    )
                    claim["paths"].remove(removed_path)
                    registry_path.write_text(json.dumps(registry), encoding="utf-8")
                    with self.subTest(identifier=identifier, removed_path=removed_path):
                        problems = validate_repository.validate_official_docs_tracking_contract(
                            root
                        )
                        self.assertTrue(
                            any(identifier in problem for problem in problems), problems
                        )
                    self.copy_contract(root)

    def test_critical_policy_claims_reject_missing_and_malformed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            registry_path = root / ".github" / "official-docs-trackers.json"

            registry = validate_repository.load_json(registry_path)
            registry["claims"] = [
                claim
                for claim in registry["claims"]
                if claim["id"] != "github-check-runs-api"
            ]
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            missing = validate_repository.validate_official_docs_tracking_contract(root)

            self.copy_contract(root)
            registry = validate_repository.load_json(registry_path)
            claim = next(
                item
                for item in registry["claims"]
                if item["id"] == "github-commit-statuses-api"
            )
            claim["paths"] = (
                "skills/repo-scaffold/scripts/branch_protection_preflight.py"
            )
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            malformed = validate_repository.validate_official_docs_tracking_contract(
                root
            )

        self.assertTrue(
            any(
                "github-check-runs-api claim is missing" in problem
                for problem in missing
            )
        )
        self.assertTrue(
            any(
                "github-commit-statuses-api claim must track every affected path"
                in problem
                for problem in malformed
            )
        )

    def test_missing_and_drifted_official_documentation_contract_is_reported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = validate_repository.validate_official_docs_tracking_contract(root)
            self.copy_contract(root)
            (root / ".github/official-docs-trackers.json").write_text(
                '{"schema-version": 1, "claims": []}\n', encoding="utf-8"
            )
            (root / "scripts/audit_official_docs.py").write_text(
                "checker-drift\n", encoding="utf-8"
            )
            (root / ".github/workflows/official-docs.yml").write_text(
                "name: stale\n"
                "on:\n"
                "  push:\n"
                "permissions:\n"
                "  contents: write\n"
                "jobs: {}\n",
                encoding="utf-8",
            )
            drifted = validate_repository.validate_official_docs_tracking_contract(root)
            (root / ".github/workflows/official-docs.yml").write_text(
                "[]\n", encoding="utf-8"
            )
            non_mapping = validate_repository.validate_official_docs_tracking_contract(
                root
            )
        self.assertTrue(any("tracker registry" in problem for problem in missing))
        self.assertTrue(any("audit_official_docs.py" in problem for problem in missing))
        self.assertTrue(any("versioned non-empty" in problem for problem in drifted))
        self.assertTrue(any("marker" in problem for problem in drifted))
        self.assertTrue(any("must use only" in problem for problem in drifted))
        self.assertTrue(any("permissions" in problem for problem in drifted))
        self.assertTrue(any("job contract" in problem for problem in drifted))
        self.assertTrue(any("must be a mapping" in problem for problem in non_mapping))

    def test_untracked_official_documentation_link_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "untracked.md").write_text(
                "[New Claude documentation](https://code.claude.com/docs/en/new-topic)\n",
                encoding="utf-8",
            )
            problems = validate_repository.validate_official_docs_tracking_contract(
                root
            )
        self.assertTrue(
            any(
                "untracked.md: official documentation URL is not tracked" in problem
                for problem in problems
            )
        )

    def test_tracked_link_outside_claim_paths_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "unlisted.md").write_text(
                "[Claude skills](https://code.claude.com/docs/en/skills)\n",
                encoding="utf-8",
            )
            problems = validate_repository.validate_official_docs_tracking_contract(
                root
            )
        self.assertTrue(
            any(
                "unlisted.md: official documentation URL must list this file" in problem
                for problem in problems
            )
        )

    def test_registry_tracks_every_declared_authoritative_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "untracked.md").write_text(
                "[New GitHub CLI documentation](https://cli.github.com/new-topic)\n",
                encoding="utf-8",
            )
            problems = validate_repository.validate_official_docs_tracking_contract(
                root
            )
        self.assertTrue(
            any(
                "untracked.md: official documentation URL is not tracked" in problem
                for problem in problems
            )
        )

    def test_invalid_tracker_entries_and_unreadable_markdown_are_handled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / ".github/official-docs-trackers.json").write_text(
                json.dumps(
                    {
                        "schema-version": 1,
                        "claims": [
                            {
                                "url": "https://code.claude.com/docs/en/skills",
                                "paths": [],
                            },
                            "not an object",
                            {"url": 7, "paths": "not a list"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            unreadable = root / "unreadable.md"
            original_read_text = Path.read_text

            def read_text_or_raise(path: Path, *args: Any, **kwargs: Any) -> str:
                if path == unreadable:
                    raise OSError("denied")
                return original_read_text(path, *args, **kwargs)

            with (
                mock.patch.object(
                    validate_repository, "project_files", return_value=[unreadable]
                ),
                mock.patch.object(
                    Path, "read_text", autospec=True, side_effect=read_text_or_raise
                ),
            ):
                problems = validate_repository.validate_official_docs_tracking_contract(
                    root
                )
        self.assertTrue(
            any(
                "unreadable.md: could not read Markdown" in problem
                for problem in problems
            )
        )

    def test_malformed_tracker_url_reports_critical_claim_gaps_without_crashing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / ".github/official-docs-trackers.json").write_text(
                json.dumps(
                    {
                        "schema-version": 1,
                        "claims": [{"url": "https://[invalid", "paths": ["README.md"]}],
                    }
                ),
                encoding="utf-8",
            )
            problems = validate_repository.validate_official_docs_tracking_contract(
                root
            )
        for identifier in (
            "github-actions-dependabot",
            "github-dependency-review",
            "github-dependency-graph-sbom-api",
            "github-actions-permissions-api",
            "github-actions-workflow-runs-api",
            "github-codeql-advanced-setup",
            "github-codeql-default-setup-api",
            "github-code-scanning-alerts-api",
            "github-repository-contents-api",
            "github-community-profile-metrics-api",
            "github-repository-license-api",
            "github-action-pin-repository-tags-api",
            "github-git-refs-api",
            "github-git-tags-api",
            "github-releases-api",
            "github-pull-requests-api",
            "github-git-commits-api",
            "github-repository-commits-api",
            "github-community-health-branches-api",
            "github-community-health-git-trees-api",
            "github-git-blobs-api",
            "github-check-runs-api",
            "github-commit-statuses-api",
            "github-repository-labels-api",
            "github-reminder-issues-api",
            "github-branch-protection-status-checks",
            "github-branches-api",
            "github-effective-branch-rules-api",
            "github-merge-queue-auto-merge",
            "github-security-analysis-settings",
            "github-repository-security-features-api",
            "github-users-api",
            "github-artifact-attestations",
            "github-actions-secrets-api",
            "github-repository-settings-api",
        ):
            with self.subTest(identifier=identifier):
                self.assertTrue(
                    any(
                        f"{identifier} claim is missing" in problem
                        for problem in problems
                    )
                )

    def test_malformed_and_nonofficial_markdown_urls_do_not_break_tracking(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "links.md").write_text("# Links\n", encoding="utf-8")
            with mock.patch.object(
                validate_repository,
                "markdown_link_destinations",
                return_value=["https://[invalid", "https://example.test"],
            ):
                problems = validate_repository.validate_official_docs_tracking_contract(
                    root
                )
        self.assertEqual(problems, [])

    def test_missing_ci_registry_gate_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / ".github/workflows/ci.yml").write_text(
                "name: CI\n", encoding="utf-8"
            )
            problems = validate_repository.validate_official_docs_tracking_contract(
                root
            )
        self.assertTrue(any("ci.yml: must validate" in problem for problem in problems))

    def test_unreadable_ci_registry_gate_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            ci_path = root / ".github/workflows/ci.yml"
            ci_path.unlink()
            problems = validate_repository.validate_official_docs_tracking_contract(
                root
            )
        self.assertTrue(any("ci.yml: unreadable" in problem for problem in problems))


class WorkflowScriptCopyContractTests(unittest.TestCase):
    def test_current_workflow_script_copy_contract_is_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_workflow_script_copy_contract(PLUGIN_ROOT), []
        )

    def test_missing_documented_workflow_script_copy_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "assets" / "workflows" / "documentation.yml"
            source = root / "scripts" / "validate_scaffold.py"
            reference = root / "references" / "scaffold-generation.md"
            workflow.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            reference.parent.mkdir(parents=True)
            workflow.write_text(
                "run: python scripts/validate_scaffold.py --repository-root .\n",
                encoding="utf-8",
            )
            source.write_text("# source\n", encoding="utf-8")
            reference.write_text("# Scaffold generation contract\n", encoding="utf-8")
            contract = (
                (
                    Path("assets/workflows/documentation.yml"),
                    Path("scripts/validate_scaffold.py"),
                    "../scripts/validate_scaffold.py",
                    Path("scripts/validate_scaffold.py"),
                    True,
                ),
            )
            with (
                mock.patch.object(
                    validate_repository,
                    "WORKFLOW_SCRIPT_COPY_CONTRACT",
                    contract,
                ),
                mock.patch.object(
                    validate_repository,
                    "SCAFFOLD_GENERATION_REFERENCE",
                    Path("references/scaffold-generation.md"),
                ),
            ):
                problems = validate_repository.validate_workflow_script_copy_contract(
                    root
                )

        self.assertEqual(
            problems,
            [
                "references/scaffold-generation.md: must document copying "
                "assets/workflows/documentation.yml dependency "
                "scripts/validate_scaffold.py"
            ],
        )

    def test_missing_bundled_source_or_workflow_invocation_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "assets" / "workflows" / "documentation.yml"
            reference = root / "references" / "scaffold-generation.md"
            workflow.parent.mkdir(parents=True)
            reference.parent.mkdir(parents=True)
            workflow.write_text("run: echo unavailable\n", encoding="utf-8")
            reference.write_text(
                "| Workflow asset | Bundled source | Generated destination |\n"
                "| --- | --- | --- |\n"
                "| `assets/workflows/documentation.yml` | "
                "`../scripts/validate_scaffold.py` | "
                "`scripts/validate_scaffold.py` |\n",
                encoding="utf-8",
            )
            contract = (
                (
                    Path("assets/workflows/documentation.yml"),
                    Path("scripts/validate_scaffold.py"),
                    "../scripts/validate_scaffold.py",
                    Path("scripts/validate_scaffold.py"),
                    True,
                ),
            )
            with (
                mock.patch.object(
                    validate_repository,
                    "WORKFLOW_SCRIPT_COPY_CONTRACT",
                    contract,
                ),
                mock.patch.object(
                    validate_repository,
                    "SCAFFOLD_GENERATION_REFERENCE",
                    Path("references/scaffold-generation.md"),
                ),
            ):
                problems = validate_repository.validate_workflow_script_copy_contract(
                    root
                )

        self.assertEqual(
            problems,
            [
                "scripts/validate_scaffold.py: bundled workflow dependency is missing",
                "assets/workflows/documentation.yml: must invoke "
                "scripts/validate_scaffold.py",
            ],
        )

    def test_unreadable_workflow_or_reference_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scripts" / "validate_scaffold.py"
            reference = root / "references" / "scaffold-generation.md"
            source.parent.mkdir(parents=True)
            reference.parent.mkdir(parents=True)
            source.write_text("# source\n", encoding="utf-8")
            reference.write_text(
                "| Workflow asset | Bundled source | Generated destination |\n"
                "| --- | --- | --- |\n"
                "| `assets/workflows/documentation.yml` | "
                "`../scripts/validate_scaffold.py` | "
                "`scripts/validate_scaffold.py` |\n",
                encoding="utf-8",
            )
            contract = (
                (
                    Path("assets/workflows/documentation.yml"),
                    Path("scripts/validate_scaffold.py"),
                    "../scripts/validate_scaffold.py",
                    Path("scripts/validate_scaffold.py"),
                    True,
                ),
            )
            with (
                mock.patch.object(
                    validate_repository,
                    "WORKFLOW_SCRIPT_COPY_CONTRACT",
                    contract,
                ),
                mock.patch.object(
                    validate_repository,
                    "SCAFFOLD_GENERATION_REFERENCE",
                    Path("references/scaffold-generation.md"),
                ),
            ):
                unreadable_workflow = (
                    validate_repository.validate_workflow_script_copy_contract(root)
                )
            reference.unlink()
            with mock.patch.object(
                validate_repository,
                "SCAFFOLD_GENERATION_REFERENCE",
                Path("references/scaffold-generation.md"),
            ):
                unreadable_reference = (
                    validate_repository.validate_workflow_script_copy_contract(root)
                )

        self.assertTrue(
            unreadable_workflow[0].startswith(
                "assets/workflows/documentation.yml: could not read workflow script "
            )
        )
        self.assertTrue(
            unreadable_reference[0].startswith(
                "references/scaffold-generation.md: could not read workflow script "
            )
        )


class PullRequestTemplatePreflightDistributionTests(unittest.TestCase):
    def test_current_preflight_distribution_contract_is_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_pr_template_preflight_contract(PLUGIN_ROOT),
            [],
        )

    def test_missing_bundled_preflight_or_copy_contract_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_script = root / "scripts" / "pr_template_preflight.py"
            reference = (
                root
                / "skills"
                / "repo-scaffold"
                / "references"
                / "scaffold-generation.md"
            )
            root_script.parent.mkdir(parents=True)
            root_script.write_text("print('unrelated')\n", encoding="utf-8")
            reference.parent.mkdir(parents=True)
            reference.write_text("# Scaffold generation contract\n", encoding="utf-8")
            assets = root / "skills" / "repo-scaffold" / "assets"
            assets.mkdir(parents=True)
            for name in ("AGENTS.md", "AGENTS.vi.md"):
                (assets / name).write_text("No preflight\n", encoding="utf-8")

            problems = validate_repository.validate_pr_template_preflight_contract(root)

        self.assertIn(
            "skills/repo-scaffold/scripts/pr_template_preflight.py: bundled "
            "preflight script is missing",
            problems,
        )
        self.assertIn(
            "scripts/pr_template_preflight.py: must delegate to the bundled "
            "preflight script",
            problems,
        )
        self.assertIn(
            "skills/repo-scaffold/references/scaffold-generation.md: must document "
            "copying scripts/pr_template_preflight.py",
            problems,
        )

    def test_missing_entrypoint_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            problems = validate_repository.validate_pr_template_preflight_contract(root)

        self.assertIn(
            "scripts/pr_template_preflight.py: maintainer preflight entry point is "
            "missing",
            problems,
        )

    def test_unreadable_preflight_contract_files_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_script = root / "scripts" / "pr_template_preflight.py"
            source = root / "skills" / "repo-scaffold" / "scripts" / root_script.name
            reference = (
                root
                / "skills"
                / "repo-scaffold"
                / "references"
                / "scaffold-generation.md"
            )
            assets = root / "skills" / "repo-scaffold" / "assets"
            for path in (
                root_script,
                source,
                reference,
                assets / "AGENTS.md",
                assets / "AGENTS.vi.md",
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("preflight\n", encoding="utf-8")

            with mock.patch.object(Path, "read_text", side_effect=OSError("denied")):
                problems = validate_repository.validate_pr_template_preflight_contract(
                    root
                )

        self.assertIn(
            "scripts/pr_template_preflight.py: unreadable: denied",
            problems,
        )
        self.assertIn(
            "skills/repo-scaffold/references/scaffold-generation.md: could not read "
            "preflight copy contract: denied",
            problems,
        )
        self.assertIn(
            "skills/repo-scaffold/assets/AGENTS.md: unreadable: denied",
            problems,
        )


class PolicyDriftReminderContractTests(unittest.TestCase):
    def test_current_policy_drift_reminder_contract_is_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_policy_drift_reminder_contract(PLUGIN_ROOT),
            [],
        )

    def test_missing_policy_drift_reminder_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_directory = root / ".github" / "workflows"
            workflow_directory.mkdir(parents=True)
            shutil.copyfile(
                PLUGIN_ROOT / ".github" / "workflows" / "ci.yml",
                workflow_directory / "ci.yml",
            )
            (workflow_directory / "ci.yml").write_text(
                "name: CI\non: {}\njobs: {}\n", encoding="utf-8"
            )
            problems = validate_repository.validate_policy_drift_reminder_contract(root)
        self.assertEqual(
            problems,
            [
                ".github/workflows/ci.yml: policy drift reminder must depend on both "
                "scheduled canaries with least-privilege issue access"
            ],
        )

    def test_policy_drift_reminder_reports_missing_semantics_and_unreadable_workflow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_directory = root / ".github" / "workflows"
            workflow_directory.mkdir(parents=True)
            workflow_path = workflow_directory / "ci.yml"
            shutil.copyfile(
                PLUGIN_ROOT / ".github" / "workflows" / "ci.yml", workflow_path
            )
            workflow_path.write_text(
                workflow_path.read_text(encoding="utf-8").replace(
                    "if [[ \"$PYTHON_CANARY_RESULT\" != 'failure' && \"$TOOLCHAIN_CANARY_RESULT\" != 'failure' ]]; then",
                    "if [[ \"$PYTHON_CANARY_RESULT\" == 'failure' && \"$TOOLCHAIN_CANARY_RESULT\" == 'failure' ]]; then",
                ),
                encoding="utf-8",
            )
            missing_semantics = (
                validate_repository.validate_policy_drift_reminder_contract(root)
            )
            workflow_path.unlink()
            unreadable = validate_repository.validate_policy_drift_reminder_contract(
                root
            )
        self.assertEqual(
            missing_semantics,
            [
                ".github/workflows/ci.yml: policy drift reminder must reconcile "
                "one marker issue from both canary results"
            ],
        )
        self.assertTrue(
            unreadable[0].startswith(
                ".github/workflows/ci.yml: could not verify policy reminder:"
            )
        )

    def test_policy_drift_reminder_requires_explicit_repository_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_directory = root / ".github" / "workflows"
            workflow_directory.mkdir(parents=True)
            workflow_path = workflow_directory / "ci.yml"
            workflow_text = (
                PLUGIN_ROOT / ".github" / "workflows" / "ci.yml"
            ).read_text(encoding="utf-8")
            workflow_text = workflow_text.replace(
                '--repo "github.com/${REPOSITORY}"',
                '--repository "github.com/${REPOSITORY}"',
            )
            workflow_path.write_text(workflow_text, encoding="utf-8")
            problems = validate_repository.validate_policy_drift_reminder_contract(root)

        self.assertEqual(
            problems,
            [
                ".github/workflows/ci.yml: policy drift reminder must reconcile "
                "one marker issue from both canary results"
            ],
        )

    def test_policy_drift_reminder_requires_repository_scoped_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_directory = root / ".github" / "workflows"
            workflow_directory.mkdir(parents=True)
            workflow_text = (
                PLUGIN_ROOT / ".github" / "workflows" / "ci.yml"
            ).read_text(encoding="utf-8")
            workflow_text = workflow_text.replace(
                "${{ github.workflow }}-policy-drift-${{ github.repository }}",
                "${{ github.workflow }}-policy-drift-${{ github.ref }}",
            )
            (workflow_directory / "ci.yml").write_text(workflow_text, encoding="utf-8")
            problems = validate_repository.validate_policy_drift_reminder_contract(root)

        self.assertEqual(
            problems,
            [
                ".github/workflows/ci.yml: policy drift reminder must use a "
                "repository-scoped non-cancelling concurrency group"
            ],
        )


if __name__ == "__main__":
    unittest.main()
