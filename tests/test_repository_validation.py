from __future__ import annotations

import importlib.util
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
