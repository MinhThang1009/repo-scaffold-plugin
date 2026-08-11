from __future__ import annotations

import importlib.util
import json
import os
import runpy
import shutil
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
SPEC = importlib.util.spec_from_file_location("validate_repository", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load validate_repository.py")
validate_repository = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validate_repository
SPEC.loader.exec_module(validate_repository)

WORKFLOW_SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "validate_workflows.py"
WORKFLOW_SPEC = importlib.util.spec_from_file_location(
    "workflow_validation", WORKFLOW_SCRIPT_PATH
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

            problems = validate_repository.validate_serialized_files(root)

            self.assertEqual(len(problems), 2)
            self.assertTrue(
                any("invalid.json: invalid JSON" in item for item in problems)
            )
            self.assertTrue(
                any("invalid.yml: invalid YAML" in item for item in problems)
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
        "README.md",
        "requirements-dev.txt",
        "ruff.toml",
        "scripts/python_support.py",
        "skills/repo-scaffold/SKILL.md",
        "skills/repo-scaffold/assets/workflows/ci.yml",
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
            (root / "requirements-dev.txt").write_text(
                ".github/python-support.json\nPython 3.10 or newer\n",
                encoding="utf-8",
            )
            (root / ".github" / "workflows" / "ci.yml").write_text(
                "jobs: []\n", encoding="utf-8"
            )
            (
                root / "skills" / "repo-scaffold" / "assets" / "workflows" / "ci.yml"
            ).write_text("jobs: {}\n", encoding="utf-8")
            (root / "skills" / "repo-scaffold" / "SKILL.md").write_text(
                "incomplete\n", encoding="utf-8"
            )
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
                "skills/repo-scaffold/SKILL.md",
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
            self.assertTrue(any("SKILL.md" in item for item in problems))
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
                "scheduled 3.x canary",
                "ci-success must keep the scheduled canary",
            )
            for expected in expected_fragments:
                self.assertTrue(
                    any(expected in item for item in problems),
                    f"{expected}: {problems}",
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


class CiToolchainContractValidationTests(unittest.TestCase):
    CONTRACT_FILES = (
        ".github/ci-toolchain.json",
        ".github/workflows/ci.yml",
        "CONTRIBUTING.md",
        "README.md",
        "skills/repo-scaffold/SKILL.md",
        "skills/repo-scaffold/assets/ci-toolchain.json",
        "skills/repo-scaffold/assets/workflows/documentation.yml",
        "skills/repo-scaffold/references/github-setup.md",
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
            (root / "skills" / "repo-scaffold" / "SKILL.md").write_text(
                "incomplete\n", encoding="utf-8"
            )
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
                "skills/repo-scaffold/SKILL.md",
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
            self.assertTrue(any("SKILL.md" in item for item in problems))
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
            (root / "requirements-dev.txt").write_text(
                "PyYAML==6.0.3\n", encoding="utf-8"
            )
            (asset_root / "requirements-docs.txt").write_text(
                "PyYAML==6.0.2\n", encoding="utf-8"
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
                "PyYAML pin drift: requirements-dev.txt and the scaffold docs "
                "requirements must match",
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
            (root / "requirements-dev.txt").write_bytes(b"\xff")
            (asset_root / "requirements-docs.txt").write_text(
                "PyYAML==1.0\nPyYAML==2.0\n", encoding="utf-8"
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
        "requirements-dev.lock",
        "requirements-dev.txt",
    )

    def copy_contract(self, root: Path) -> None:
        for relative in self.CONTRACT_FILES:
            source = PLUGIN_ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def test_repository_dependency_and_coverage_contract_is_valid(self) -> None:
        self.assertEqual(
            validate_repository.validate_development_dependency_contract(PLUGIN_ROOT),
            [],
        )

    def test_direct_dependency_version_drift_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            lock_path = root / "requirements-dev.lock"
            lock_text = lock_path.read_text(encoding="utf-8").replace(
                "coverage==7.15.4 \\", "coverage==0.0.0 \\", 1
            )
            lock_path.write_text(lock_text, encoding="utf-8")

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertIn(
                "requirements-dev.lock: coverage pin 0.0.0 does not match "
                "requirements-dev.txt pin 7.15.4",
                problems,
            )

    def test_lock_entry_without_hash_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            lock_path = root / "requirements-dev.lock"
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
                "requirements-dev.lock: types-pyyaml==6.0.12.20260724 must have "
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
                "the hashed requirements-dev.lock",
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
                ".coveragerc: require branch coverage for both script trees with "
                "a fail-under floor of at least 100",
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
                ].startswith("requirements-dev.txt: could not verify direct pins")
            )
            (root / "requirements-dev.txt").write_text(
                "pytest==1.0\n", encoding="utf-8"
            )
            self.assertTrue(
                validate_repository.validate_development_dependency_contract(root)[
                    0
                ].startswith("requirements-dev.lock: could not verify hashed lock")
            )

    def test_invalid_direct_and_lock_shapes_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "requirements-dev.txt").write_text(
                "# comment\ninvalid requirement\nPy_Test==1.0\npy-test==2.0\nmissing==3.0\n",
                encoding="utf-8",
            )
            (root / "requirements-dev.lock").write_text(
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
                    "generator header must record hash mode" in item
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

    def test_cross_version_compatibility_pins_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            direct_path = root / "requirements-dev.txt"
            direct_text = direct_path.read_text(encoding="utf-8")
            direct_path.write_text(
                direct_text.replace("exceptiongroup==1.3.1\n", "").replace(
                    "tomli==2.4.1", "tomli==0.0.0"
                ),
                encoding="utf-8",
            )

            problems = validate_repository.validate_development_dependency_contract(
                root
            )

            self.assertIn(
                "requirements-dev.txt: the cross-version lock requires "
                "exceptiongroup==1.3.1",
                problems,
            )
            self.assertIn(
                "requirements-dev.txt: the cross-version lock requires tomli==2.4.1",
                problems,
            )

    def test_empty_lock_and_invalid_workflow_coverage_docs_and_exports_are_reported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "requirements-dev.lock").write_text(
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
        "requirements-mutation.lock",
        "requirements-mutation.txt",
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
            (root / "requirements-mutation.txt").write_text(
                "mutmut>=3\n", encoding="utf-8"
            )
            lock_path = root / "requirements-mutation.lock"
            lock_text = lock_path.read_text(encoding="utf-8")
            mutmut_start = lock_text.index("mutmut==3.6.0")
            mutmut_end = lock_text.index("mypy==", mutmut_start)
            mutmut_block = "\n".join(
                line
                for line in lock_text[mutmut_start:mutmut_end].splitlines()
                if "--hash=sha256:" not in line
            )
            toml_start = lock_text.index("toml==0.10.2")
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

        self.assertTrue(any("must extend requirements-dev.txt" in p for p in problems))
        self.assertTrue(any("portable hash mode" in p for p in problems))
        self.assertTrue(any("hashed mutmut==3.6.0" in p for p in problems))
        self.assertTrue(any("hashed toml==0.10.2" in p for p in problems))

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

    def test_config_docs_exports_and_ignore_regressions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_contract(root)
            (root / "pyproject.toml").write_text("[tool.mutmut]\n", encoding="utf-8")
            (root / "README.md").write_text("mutmut\n", encoding="utf-8")
            (root / "CONTRIBUTING.md").write_text(
                "requirements-mutation.lock\n", encoding="utf-8"
            )
            (root / ".gitattributes").write_text("", encoding="utf-8")
            (root / ".gitignore").write_text("# empty\n", encoding="utf-8")

            problems = validate_repository.validate_mutation_testing_contract(root)

        self.assertEqual(sum("missing mutation setting" in p for p in problems), 5)
        self.assertIn(
            "pyproject.toml: pytest must collect only first-party tests from tests/",
            problems,
        )
        self.assertEqual(sum("mutation guidance" in p for p in problems), 2)
        self.assertEqual(sum("must be export-ignore" in p for p in problems), 3)
        self.assertIn(".gitignore: mutants/ must be ignored", problems)


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
            validate_repository.link_destination(' <docs/file.md> "title" '),
            "docs/file.md",
        )
        self.assertEqual(
            validate_repository.link_destination("docs/file.md 'title'"),
            "docs/file.md",
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

    def test_release_archive_inspects_required_unsafe_and_symbolic_members(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write_archive(command: list[str], **_kwargs: object) -> mock.Mock:
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
            "validate_release_please",
            "validate_release_attestation",
            "validate_issue_templates",
            "validate_dependabot",
            "validate_markdown_links",
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
                validate_repository.validate_plugin_manifest(root),
                [".codex-plugin/plugin.json: skills must be nonempty"],
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
        (root / "release-please-config.json").write_text(
            json.dumps(
                {
                    "release-type": "simple",
                    **validate_repository.RELEASE_PLEASE_VIETNAMESE_TEXT,
                    "changelog-sections": (
                        validate_repository.RELEASE_PLEASE_VIETNAMESE_CHANGELOG_SECTIONS
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
                                }
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

    def test_rejects_release_metadata_that_is_not_fully_vietnamese(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            config_path = root / "release-please-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["pull-request-header"] = "Automated release PR"
            config["changelog-sections"][0]["section"] = "Features"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            problems = validate_repository.validate_release_please(root)

            self.assertIn(
                "release-please-config.json: pull-request-header must use the "
                "approved Vietnamese release text",
                problems,
            )
            self.assertIn(
                "release-please-config.json: changelog-sections must preserve the "
                "approved Vietnamese headings and default visibility",
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

        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Before changing `pull-request-title-pattern`", skill)
        self.assertIn("update each existing release PR title", skill)

    def test_accepts_intentional_semver_build_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_configuration(root)
            (root / ".codex-plugin" / "plugin.json").write_text(
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
            (root / ".release-please-manifest.json").write_text(
                '{".": "1.2.3+codex.test"}', encoding="utf-8"
            )
            (root / "version.txt").write_text("1.2.3+codex.test\n", encoding="utf-8")

            problems = validate_repository.validate_release_please(root)

            for source in (
                ".codex-plugin/plugin.json",
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
                    "root package must update plugin version" in item
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


class ReleaseAttestationValidationTests(unittest.TestCase):
    def write_valid_configuration(self, root: Path) -> None:
        action_sha = "a" * 40
        engine = {
            "jobs": {
                "build": {"permissions": {"contents": "read"}},
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
        )
        for expected in expected_fragments:
            self.assertTrue(any(expected in item for item in problems), expected)

    def test_legacy_issue_templates_and_chooser_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            markdown_cases = {
                "missing.md": "No front matter\n",
                "shape.md": "---\n- invalid\n---\nBody\n",
                "fields.md": "---\nname: Bug\nabout: ''\n---\n",
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


class DependabotValidationTests(unittest.TestCase):
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
                "      interval: sometimes\n",
                encoding="utf-8",
            )

            problems = validate_repository.validate_dependabot(root)

            expected_fragments = (
                "invalid Dependabot YAML",
                "version must be 2",
                "updates[0] must be a mapping",
                "updates[1].package-ecosystem is required",
                "updates[1].directory is required",
                "updates[1].schedule is required",
                "updates[2].schedule.interval is invalid",
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

        stderr = mock.Mock()
        with (
            mock.patch.object(
                validate_workflows.subprocess,
                "run",
                side_effect=validate_workflows.subprocess.TimeoutExpired(
                    ["actionlint"], 60
                ),
            ),
            mock.patch.object(validate_workflows.sys, "stderr", stderr),
        ):
            timeout_result = validate_workflows.run_actionlint(
                "actionlint", [workflow], working_directory=working_directory
            )

        self.assertEqual(timeout_result, 2)
        self.assertTrue(
            any(
                "actionlint timed out" in call.args[0]
                for call in stderr.write.call_args_list
            )
        )

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

    def test_shellcheck_rejects_workflows_without_shell_blocks(self) -> None:
        stderr = mock.Mock()
        with (
            mock.patch.object(
                validate_workflows, "workflow_shell_blocks", return_value=[]
            ),
            mock.patch.object(validate_workflows.sys, "stderr", stderr),
        ):
            result = validate_workflows.run_shellcheck("shellcheck", [Path("ci.yml")])

        self.assertEqual(result, 2)
        self.assertTrue(
            any(
                "No shell run blocks" in call.args[0]
                for call in stderr.write.call_args_list
            )
        )

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
            self.assertEqual(actionlint.call_count, 1)

        with (
            mock.patch.object(
                validate_workflows,
                "resolve_path_executable",
                side_effect=["actionlint", "shellcheck"],
            ),
            mock.patch.object(validate_workflows, "run_actionlint", return_value=0),
            mock.patch.object(validate_workflows, "run_shellcheck", return_value=5),
        ):
            self.assertEqual(validate_workflows.main(), 5)

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
        copied_files = actionlint.call_args_list[1].args[1]
        copied_root = actionlint.call_args_list[1].kwargs["working_directory"]
        self.assertTrue(copied_files)
        self.assertTrue(all(path.parent.name == "workflows" for path in copied_files))
        self.assertTrue(
            all(str(path).startswith(str(copied_root)) for path in copied_files)
        )

    def test_script_entrypoint_returns_main_status(self) -> None:
        stderr = mock.Mock()
        with (
            mock.patch.dict(os.environ, {"PATH": ""}),
            mock.patch.object(sys, "stderr", stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(WORKFLOW_SCRIPT_PATH), run_name="__main__")

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
