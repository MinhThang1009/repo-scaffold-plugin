from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
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


class PythonSupportContractValidationTests(unittest.TestCase):
    CONTRACT_FILES = (
        ".github/python-support.json",
        ".github/workflows/ci.yml",
        "CONTRIBUTING.md",
        "README.md",
        "requirements-dev.txt",
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


class CiToolchainContractValidationTests(unittest.TestCase):
    CONTRACT_FILES = (
        ".github/ci-toolchain.json",
        ".github/workflows/ci.yml",
        "CONTRIBUTING.md",
        "README.md",
        "skills/repo-scaffold/SKILL.md",
        "skills/repo-scaffold/assets/ci-toolchain.json",
        "skills/repo-scaffold/assets/workflows/documentation.yml",
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
                "${{ needs.prepare_ci.outputs.shellcheck_version }}",
                '"0.11.0"',
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


class WorkflowShellValidationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
