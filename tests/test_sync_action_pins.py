from __future__ import annotations

import importlib.util
import json
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "sync_action_pins.py"
SPEC = importlib.util.spec_from_file_location("scripts.sync_action_pins", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load sync_action_pins.py")
sync_action_pins = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync_action_pins
SPEC.loader.exec_module(sync_action_pins)


class FakeResponse(BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class ActionPinSyncTests(unittest.TestCase):
    def write_workflow(self, root: Path, relative: str, content: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def make_repository(self, root: Path) -> tuple[Path, Path]:
        installed = self.write_workflow(
            root,
            ".github/workflows/ci.yml",
            "jobs:\n  test:\n    steps:\n"
            "      - uses: actions/checkout@" + "a" * 40 + " # v1.0.0\n"
            "      - uses: github/codeql-action/init@" + "b" * 40 + " # v2.0.0\n",
        )
        asset = self.write_workflow(
            root,
            "skills/repo-scaffold/assets/workflows/codeql.yml",
            "jobs:\n  test:\n    steps:\n"
            "      - uses: actions/checkout@" + "a" * 40 + " # v1.0.0\n"
            "      - uses: github/codeql-action/analyze@" + "b" * 40 + " # v2.0.0\n",
        )
        return installed, asset

    def releases(self, repository: str) -> Any:
        versions = {
            "actions/checkout": sync_action_pins.ActionRelease("v9.1.2", "c" * 40),
            "github/codeql-action": sync_action_pins.ActionRelease("v8.7.6", "d" * 40),
        }
        return versions[repository]

    def test_synchronize_updates_installed_and_template_workflows_together(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed, asset = self.make_repository(root)

            pending = sync_action_pins.synchronize_action_pins(
                root, self.releases, write=False
            )
            changed = sync_action_pins.synchronize_action_pins(
                root, self.releases, write=True
            )

            self.assertEqual(pending, [installed, asset])
            self.assertEqual(changed, [installed, asset])
            self.assertIn(
                f"actions/checkout@{'c' * 40} # v9.1.2",
                installed.read_text(encoding="utf-8"),
            )
            asset_text = asset.read_text(encoding="utf-8")
            self.assertIn(
                f"github/codeql-action/analyze@{'d' * 40} # v8.7.6", asset_text
            )
            self.assertNotIn("# v2.0.0", asset_text)

    def test_synchronize_leaves_current_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed, asset = self.make_repository(root)
            sync_action_pins.synchronize_action_pins(root, self.releases, write=True)

            changed = sync_action_pins.synchronize_action_pins(
                root, self.releases, write=True
            )

            self.assertEqual(changed, [])
            self.assertIn("# v9.1.2", installed.read_text(encoding="utf-8"))
            self.assertIn("# v8.7.6", asset.read_text(encoding="utf-8"))

    def test_synchronize_preserves_current_pin_comment_spacing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  test:\n    steps:\n"
                "      - uses: actions/checkout@" + "c" * 40 + "  # v9.1.2\n",
            )
            original = workflow.read_text(encoding="utf-8")

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [])
            self.assertEqual(workflow.read_text(encoding="utf-8"), original)

    def test_generated_project_can_scope_synchronization_to_its_workflows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  test:\n    steps:\n"
                "      - uses: actions/checkout@" + "a" * 40 + " # v1.0.0\n",
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            self.assertIn("# v9.1.2", workflow.read_text(encoding="utf-8"))

    def test_synchronize_updates_yaml_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/scheduled.yaml",
                "jobs:\n  test:\n    steps:\n"
                "      - uses: actions/checkout@" + "a" * 40 + " # v1.0.0\n",
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            self.assertIn("# v9.1.2", workflow.read_text(encoding="utf-8"))

    def test_synchronize_preserves_line_endings_and_adjacent_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_bytes(
                (
                    "jobs:\r\n"
                    "  test:\r\n"
                    "    steps:\r\n"
                    "      - uses: actions/checkout@" + "a" * 40 + "\r\n\r\n"
                ).encode()
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            updated = workflow.read_bytes()
            self.assertIn(f"actions/checkout@{'c' * 40} # v9.1.2".encode(), updated)
            self.assertEqual(updated.count(b"\r\n"), 5)
            self.assertNotIn(b"\n", updated.replace(b"\r\n", b""))

    def test_synchronize_updates_quoted_action_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "jobs:\n  test:\n    steps:\n"
                    + '      - uses: "actions/checkout@'
                    + "a" * 40
                    + '" # v1.0.0\n'
                    + "      - uses: 'actions/setup-python@"
                    + "b" * 40
                    + "' # v1.0.0\n"
                ),
            )
            releases = {
                "actions/checkout": sync_action_pins.ActionRelease("v9.1.2", "c" * 40),
                "actions/setup-python": sync_action_pins.ActionRelease(
                    "v6.0.0", "d" * 40
                ),
            }

            changed = sync_action_pins.synchronize_action_pins(
                root,
                releases.__getitem__,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertIn(f'uses: "actions/checkout@{"c" * 40}" # v9.1.2', content)
            self.assertIn(f"uses: 'actions/setup-python@{'d' * 40}' # v6.0.0", content)
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout", "actions/setup-python"},
            )

    def test_synchronize_updates_anchored_action_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "jobs:\n  test:\n    steps:\n"
                    + "      - uses: &checkout-pin actions/checkout@"
                    + "a" * 40
                    + " # v1.0.0\n"
                    + "      - uses: *checkout-pin\n"
                ),
            )
            releases = {
                "actions/checkout": sync_action_pins.ActionRelease("v9.1.2", "c" * 40)
            }

            changed = sync_action_pins.synchronize_action_pins(
                root,
                releases.__getitem__,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertIn(
                f"uses: &checkout-pin actions/checkout@{'c' * 40} # v9.1.2", content
            )
            self.assertIn("uses: *checkout-pin", content)
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )

    def test_synchronize_updates_block_scalar_action_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "jobs:\n  test:\n    steps:\n"
                    + "      - uses: >-\n          actions/checkout@"
                    + "a" * 40
                    + "\n"
                    + "      - uses: |-\n          actions/checkout@"
                    + "a" * 40
                    + "\n"
                ),
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertEqual(content.count(f"actions/checkout@{'c' * 40}"), 2)
            self.assertNotIn("# v9.1.2", content)
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )
            self.assertEqual(
                sync_action_pins.synchronize_action_pins(
                    root,
                    self.releases,
                    write=True,
                    workflow_directories=(Path(".github/workflows"),),
                ),
                [],
            )

    def test_synchronize_updates_explicit_uses_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  test:\n    steps:\n      - ? uses\n        : actions/checkout@"
                + "a" * 40
                + " # v1.0.0\n",
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertIn(f": actions/checkout@{'c' * 40} # v9.1.2", content)
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )
            with self.assertRaisesRegex(ValueError, "not pinned"):
                sync_action_pins.auditable_action_repositories(
                    workflow,
                    "jobs:\n  test:\n    steps:\n      - ? uses\n        : actions/checkout@v4\n",
                )

    def test_synchronize_updates_explicit_block_uses_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  test:\n    steps:\n      - ? |-\n          uses\n        : actions/checkout@"
                + "a" * 40
                + "\n      - ? >-\n          uses\n        : actions/checkout@"
                + "b" * 40
                + "\n",
            )
            releases = {
                "actions/checkout": sync_action_pins.ActionRelease("v9.1.2", "c" * 40)
            }

            changed = sync_action_pins.synchronize_action_pins(
                root,
                releases.__getitem__,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertEqual(content.count(f"actions/checkout@{'c' * 40}"), 2)
            with self.assertRaisesRegex(ValueError, "not pinned"):
                sync_action_pins.auditable_action_repositories(
                    workflow,
                    "jobs:\n  test:\n    steps:\n      - ? |-\n          uses\n        : actions/checkout@v4\n",
                )

    def test_synchronize_updates_quoted_uses_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                'jobs:\n  test:\n    steps:\n      - "uses": actions/checkout@'
                + "a" * 40
                + "\n      - { 'uses': actions/checkout@"
                + "b" * 40
                + ' }\n      - "uses": >-\n          actions/checkout@'
                + "b" * 40
                + "\n      - !!str uses: actions/checkout@"
                + "b" * 40
                + '\n      - "\\u0075ses": actions/checkout@'
                + "b" * 40
                + '\n      ? "u\\\n          ses"\n        : actions/checkout@'
                + "b" * 40
                + "\n",
            )
            releases = {
                "actions/checkout": sync_action_pins.ActionRelease("v9.1.2", "c" * 40)
            }

            changed = sync_action_pins.synchronize_action_pins(
                root,
                releases.__getitem__,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertEqual(content.count(f"actions/checkout@{'c' * 40}"), 6)
            with self.assertRaisesRegex(ValueError, "not pinned"):
                sync_action_pins.auditable_action_repositories(
                    workflow,
                    'jobs:\n  test:\n    steps:\n      - "uses": actions/checkout@v4\n',
                )

    def test_synchronize_updates_tagged_action_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "env:\n  CHECKOUT_ACTION: !custom &outside actions/checkout@"
                    + "a" * 40
                    + "\njobs:\n  test:\n    steps:\n"
                    + "      - uses: !!str actions/checkout@"
                    + "a" * 40
                    + "\n"
                    + "      - uses: !<tag:yaml.org,2002:str> actions/checkout@"
                    + "a" * 40
                    + "\n"
                    + "      - uses: ! actions/checkout@"
                    + "a" * 40
                    + "\n"
                    + "      - uses: !custom actions/checkout@"
                    + "a" * 40
                    + "\n"
                    + "      - uses: !!str 'actions/checkout@"
                    + "a" * 40
                    + "'\n"
                    + "      - uses: !!str &checkout actions/checkout@"
                    + "a" * 40
                    + "\n      - uses: *checkout\n      - uses: *outside\n"
                    + "      - uses: !!str >-\n          actions/checkout@"
                    + "a" * 40
                    + "\n"
                ),
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertEqual(content.count(f"actions/checkout@{'c' * 40}"), 8)
            self.assertIn("uses: *checkout", content)
            self.assertIn("uses: *outside", content)
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )

    def test_synchronize_updates_flow_style_action_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "jobs:\n  test:\n    steps:\n"
                    + "      - { uses: actions/checkout@"
                    + "a" * 40
                    + " }\n"
                    + "      - { name: Checkout, uses: actions/checkout@"
                    + "a" * 40
                    + " }\n"
                    + "      - { uses: actions/checkout@"
                    + "a" * 40
                    + ", name: Checkout }\n"
                    + "      - { uses: !custom actions/checkout@"
                    + "a" * 40
                    + " }\n"
                    + "      - { uses: &checkout actions/checkout@"
                    + "a" * 40
                    + " }\n      - { uses: *checkout }\n"
                    + "      - {\n          uses: actions/checkout@"
                    + "a" * 40
                    + "\n        }\n"
                    + "      - { name: Checkout,\n          uses: actions/checkout@"
                    + "a" * 40
                    + " }\n"
                    + "      - { uses: actions/checkout@"
                    + "a" * 40
                    + ",\n          name: Checkout }\n"
                    + "      - { if: \"${{ github.ref == 'refs/heads/main' }}\", uses: actions/checkout@"
                    + "a" * 40
                    + " }\n"
                    + "      - { uses: actions/checkout@"
                    + "a" * 40
                    + ", if: \"${{ github.ref == 'refs/heads/main' }}\" }\n"
                    + "      - { uses: actions/checkout@"
                    + "a" * 40
                    + ", with: { fetch-depth: 0 } }\n"
                    + "      - { with: { fetch-depth: 0 }, uses: actions/checkout@"
                    + "a" * 40
                    + " }\n"
                ),
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertEqual(content.count(f"actions/checkout@{'c' * 40}"), 12)
            self.assertIn(
                f"uses: actions/checkout@{'c' * 40}, name: Checkout }} # v9.1.2",
                content,
            )
            self.assertIn("uses: *checkout }", content)
            self.assertIn(
                f"uses: actions/checkout@{'c' * 40}\n        }} # v9.1.2",
                content,
            )
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )
            with self.assertRaisesRegex(ValueError, "not pinned"):
                sync_action_pins.auditable_action_repositories(
                    workflow,
                    "  - { uses: actions/checkout@v4 }\n",
                )
            self.assertEqual(
                sync_action_pins.synchronize_action_pins(
                    root,
                    self.releases,
                    write=True,
                    workflow_directories=(Path(".github/workflows"),),
                ),
                [],
            )

    def test_synchronize_updates_explicit_flow_uses_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "jobs:\n  test:\n    steps:\n"
                    + "      - { ? uses : actions/checkout@"
                    + "a" * 40
                    + " }\n"
                    + '      - { ? "uses" : actions/checkout@'
                    + "a" * 40
                    + " }\n"
                    + "      - { ? !!str uses : actions/checkout@"
                    + "a" * 40
                    + " }\n"
                ),
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertEqual(content.count(f"actions/checkout@{'c' * 40}"), 3)
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )
            with self.assertRaisesRegex(ValueError, "not pinned"):
                sync_action_pins.auditable_action_repositories(
                    workflow,
                    "  - { ? uses : actions/checkout@v4 }\n",
                )

    def test_synchronize_updates_flow_mapping_action_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "env: { UNUSED_ACTION: &unused actions/checkout@"
                    + "a" * 40
                    + ", CHECKOUT_ACTION: &checkout actions/checkout@"
                    + "a" * 40
                    + " }\njobs:\n  test:\n    steps:\n      - uses: *checkout\n"
                ),
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertIn(
                f"&checkout actions/checkout@{'c' * 40}",
                content,
            )
            self.assertIn(
                f"&unused actions/checkout@{'a' * 40}",
                content,
            )
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )
            with self.assertRaisesRegex(ValueError, "not pinned"):
                sync_action_pins.auditable_action_repositories(
                    workflow,
                    "env: { CHECKOUT_ACTION: &checkout actions/checkout@v4 }\n"
                    "jobs:\n  test:\n    steps:\n      - uses: *checkout\n",
                )

    def test_flow_mapping_ranges_mask_unclosed_flow_mappings(self) -> None:
        content = "  - {\n      uses: actions/checkout@" + "a" * 40 + "\n"

        self.assertEqual(
            sync_action_pins.flow_mapping_content_ranges(content),
            ((6, len(content)),),
        )
        self.assertEqual(sync_action_pins.workflow_uses_matches(content), ())

    def test_synchronize_handles_deeply_nested_flow_action_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                "jobs: { verify: { runs-on: ubuntu-latest, steps: [ { uses: actions/checkout@"
                + "a" * 40
                + " } ] } }\n",
            )

            self.assertEqual(
                sync_action_pins.auditable_action_repositories(
                    workflow, workflow.read_text(encoding="utf-8")
                ),
                {"actions/checkout"},
            )
            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertIn(f"actions/checkout@{'c' * 40}", content)
            self.assertEqual(
                sync_action_pins.synchronize_action_pins(
                    root,
                    self.releases,
                    write=True,
                    workflow_directories=(Path(".github/workflows"),),
                ),
                [],
            )
            with self.assertRaisesRegex(ValueError, "not pinned"):
                sync_action_pins.auditable_action_repositories(
                    workflow,
                    "jobs: { verify: { runs-on: ubuntu-latest, steps: [ { uses: actions/checkout@v4 } ] } }\n",
                )

    def test_flow_inline_uses_ignores_quoted_text(self) -> None:
        content = (
            'jobs: { verify: { name: ", uses: actions/checkout@'
            + "a" * 40
            + '", steps: [ { uses: actions/checkout@'
            + "b" * 40
            + " } ] } }\n"
        )

        self.assertEqual(
            [
                match.group("reference")
                for match in sync_action_pins.workflow_uses_matches(content)
            ],
            [f"actions/checkout@{'b' * 40}"],
        )

    def test_flow_inline_uses_handles_hash_in_plain_scalars(self) -> None:
        content = (
            "jobs: { verify: { name: literal#fragment, steps: [ { uses: actions/checkout@"
            + "a" * 40
            + " } ] } }\n"
        )

        self.assertEqual(
            [
                match.group("reference")
                for match in sync_action_pins.workflow_uses_matches(content)
            ],
            [f"actions/checkout@{'a' * 40}"],
        )

    def test_flow_mapping_quote_tracking_handles_escapes_and_comments(self) -> None:
        self.assertEqual(
            sync_action_pins.flow_mapping_ranges("{ # trailing comment"), ()
        )
        self.assertEqual(
            sync_action_pins.flow_mapping_quote_at(
                "{ name: 'it''s", 0, len("{ name: 'it''s")
            ),
            "'",
        )
        self.assertEqual(
            sync_action_pins.flow_mapping_quote_at('{ name: "\\x', 0, 11), '"'
        )
        self.assertEqual(
            sync_action_pins.flow_mapping_quote_at("{ # comment", 0, 11), "#"
        )
        self.assertIsNone(
            sync_action_pins.flow_mapping_quote_at("{ # comment\n", 0, 12)
        )

    def test_synchronize_handles_braces_in_flow_mapping_strings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "jobs:\n  test:\n    steps:\n      - {\n"
                    + '          name: "}",\n          uses: actions/checkout@'
                    + "a" * 40
                    + "\n        }\n"
                ),
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertEqual(content.count(f"actions/checkout@{'c' * 40}"), 1)
            self.assertIn(
                f"uses: actions/checkout@{'c' * 40}\n        }} # v9.1.2",
                content,
            )
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )

    def test_synchronize_handles_multiline_flow_mapping_strings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "jobs:\n  test:\n    steps:\n      - {\n"
                    + '          name: "one line\n            } still quoted",\n'
                    + "          uses: actions/checkout@"
                    + "a" * 40
                    + "\n        }\n      - {\n"
                    + "          name: 'one line\n            } still quoted',\n"
                    + "          uses: actions/checkout@"
                    + "a" * 40
                    + "\n        }\n"
                ),
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertEqual(content.count(f"actions/checkout@{'c' * 40}"), 2)
            self.assertEqual(content.count("} # v9.1.2"), 2)
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )

    def test_flow_mapping_brace_delta_preserves_escaped_quotes(self) -> None:
        self.assertEqual(
            sync_action_pins.flow_mapping_brace_delta("''", "'"),
            (0, "'"),
        )
        self.assertEqual(
            sync_action_pins.flow_mapping_brace_delta("\\}", '"'),
            (0, '"'),
        )

    def test_synchronize_updates_action_pin_anchored_outside_uses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "name: Anchored action\n"
                    "on: push\n"
                    "env:\n"
                    "  CHECKOUT_ACTION: &checkout actions/checkout@"
                    + "a" * 40
                    + " # v1.0.0\n"
                    + "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - uses: *checkout\n"
                ),
            )
            releases = {
                "actions/checkout": sync_action_pins.ActionRelease("v9.1.2", "c" * 40)
            }

            changed = sync_action_pins.synchronize_action_pins(
                root,
                releases.__getitem__,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertIn(
                f"CHECKOUT_ACTION: &checkout actions/checkout@{'c' * 40} # v9.1.2",
                content,
            )
            self.assertIn("uses: *checkout", content)
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(workflow, content),
                {"actions/checkout"},
            )
            with self.assertRaisesRegex(ValueError, "not pinned"):
                sync_action_pins.auditable_action_repositories(
                    workflow,
                    content.replace("c" * 40, "v9.1.2"),
                )

    def test_synchronize_ignores_unreferenced_action_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                "env:\n  UNUSED_ACTION: &unused actions/checkout@" + "a" * 40 + "\n",
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [])
            self.assertIn("a" * 40, workflow.read_text(encoding="utf-8"))

    def test_synchronize_ignores_uses_text_in_run_block_scalars(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                (
                    "jobs:\n  test:\n    steps:\n"
                    "      - uses: actions/checkout@"
                    + "a" * 40
                    + " # v1.0.0\n"
                    + "      - run: |\n"
                    + "          uses: actions/checkout@"
                    + "b" * 40
                    + " # not an action\n"
                    + "      - run: echo completed\n"
                    + '      - run: "echo started\n'
                    + "          uses: actions/checkout@"
                    + "d" * 40
                    + " # not an action\n"
                    + '          echo completed"\n'
                    + "      - run: 'echo started\n"
                    + "          uses: actions/checkout@"
                    + "e" * 40
                    + " # not an action\n"
                    + "          echo it''s completed'\n"
                ),
            )

            changed = sync_action_pins.synchronize_action_pins(
                root,
                self.releases,
                write=True,
                workflow_directories=(Path(".github/workflows"),),
            )

            self.assertEqual(changed, [workflow])
            content = workflow.read_text(encoding="utf-8")
            self.assertIn(f"actions/checkout@{'c' * 40} # v9.1.2", content)
            self.assertIn(f"uses: actions/checkout@{'b' * 40} # not an action", content)
            self.assertIn(f"uses: actions/checkout@{'d' * 40} # not an action", content)
            self.assertIn(f"uses: actions/checkout@{'e' * 40} # not an action", content)
            self.assertEqual(
                sync_action_pins.auditable_action_repositories(
                    workflow,
                    "jobs:\n  test:\n    steps:\n      - run: |\n"
                    + f"          uses: example/not-an-action@{'a' * 40}\n",
                ),
                set(),
            )

    def test_action_matching_ignores_multiline_quoted_scalar_edge_cases(self) -> None:
        action = "actions/checkout@" + "a" * 40
        for content in (
            'run: "one line"\n',
            'run: "echo \\"quoted\\"\n  uses: ' + action + '\n  echo done"\n',
            "run: 'echo it''s\n  uses: " + action + "\n  echo done'\n",
            "run: !custom |\n  uses: " + action + "\n",
            'run: !custom "echo started\n  uses: ' + action + '\n  echo done"\n',
            'run: "unterminated\n  uses: ' + action + "\n",
        ):
            with self.subTest(content=content):
                self.assertEqual(sync_action_pins.workflow_uses_matches(content), ())

    def test_action_repositories_rejects_unpinned_and_unallowed_references(
        self,
    ) -> None:
        path = Path("workflow.yml")
        for content, message in (
            ("  - uses: actions/checkout@v7\n", "not pinned"),
            ("  - uses: example/action@" + "a" * 40 + "\n", "allowlist"),
        ):
            with self.subTest(content=content):
                with self.assertRaisesRegex(ValueError, message):
                    sync_action_pins.action_repositories(path, content)
        self.assertEqual(
            sync_action_pins.auditable_action_repositories(
                path, "  - uses: actions/setup-node@" + "a" * 40 + "\n"
            ),
            {"actions/setup-node"},
        )
        self.assertEqual(
            sync_action_pins.action_repositories(
                path,
                "  - uses: ./local-action\n  - uses: docker://alpine@sha256:"
                + "a" * 64
                + "\n",
            ),
            set(),
        )

    def test_workflow_paths_rejects_missing_unsafe_and_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(
                sync_action_pins._path_has_link_or_reparse(root.parent, root)
            )
            with self.assertRaisesRegex(ValueError, "missing or unsafe"):
                sync_action_pins.workflow_paths(root)
            with self.assertRaisesRegex(ValueError, "safe relative path"):
                sync_action_pins.workflow_paths(root, (Path("../outside"),))
            for relative in sync_action_pins.WORKFLOW_DIRECTORIES:
                (root / relative).mkdir(parents=True)
            ignored = root / sync_action_pins.WORKFLOW_DIRECTORIES[0] / "ignored.yml"
            ignored.mkdir()
            with self.assertRaisesRegex(ValueError, "no workflow files"):
                sync_action_pins.workflow_paths(root)
            workflow = root / sync_action_pins.WORKFLOW_DIRECTORIES[0] / "workflow.yml"
            workflow.write_text("jobs: {}\n", encoding="utf-8")
            original_is_symlink = Path.is_symlink
            with mock.patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: path == workflow or original_is_symlink(path),
            ):
                with self.assertRaisesRegex(ValueError, "workflow file is unsafe"):
                    sync_action_pins.workflow_paths(root)
            ancestor = root / ".github"
            with (
                mock.patch.object(
                    sync_action_pins,
                    "_is_link_or_reparse",
                    side_effect=lambda path: path == ancestor,
                ),
                self.assertRaisesRegex(
                    ValueError, "workflow directory is missing or unsafe"
                ),
            ):
                sync_action_pins.workflow_paths(root)
            with (
                mock.patch.object(
                    sync_action_pins, "_is_link_or_reparse", return_value=True
                ),
                self.assertRaisesRegex(
                    ValueError, "repository root is missing or unsafe"
                ),
            ):
                sync_action_pins.workflow_paths(root)
            workflow.unlink()
            ignored.rmdir()
            link = root / sync_action_pins.WORKFLOW_DIRECTORIES[0]
            link.rmdir()
            try:
                link.symlink_to(
                    root / sync_action_pins.WORKFLOW_DIRECTORIES[1],
                    target_is_directory=True,
                )
            except OSError:
                return
            with self.assertRaisesRegex(ValueError, "missing or unsafe"):
                sync_action_pins.workflow_paths(root)

    def test_github_release_client_resolves_lightweight_and_annotated_tags(
        self,
    ) -> None:
        responses = iter(
            [
                {"tag_name": "v1.2.3"},
                {"object": {"type": "commit", "sha": "a" * 40}},
                {"tag_name": "v2.0.0"},
                {"object": {"type": "tag", "sha": "b" * 40}},
                {"object": {"type": "commit", "sha": "c" * 40}},
            ]
        )
        requests: list[tuple[str, str | None, int]] = []

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            requests.append(
                (request.full_url, request.get_header("Authorization"), timeout)
            )
            return FakeResponse(json.dumps(next(responses)).encode("utf-8"))

        client = sync_action_pins.GitHubReleaseClient("token", opener)

        self.assertEqual(
            client.latest_release("actions/checkout"),
            sync_action_pins.ActionRelease("v1.2.3", "a" * 40),
        )
        self.assertEqual(
            client.latest_release("actions/setup-python"),
            sync_action_pins.ActionRelease("v2.0.0", "c" * 40),
        )
        self.assertEqual(len(requests), 5)
        self.assertTrue(
            all(
                header == "Bearer token" and timeout == 30
                for _, header, timeout in requests
            )
        )
        self.assertTrue(any("git/tags/" in url for url, _, _ in requests))

    def test_github_release_client_resolves_nested_annotated_tags(self) -> None:
        responses = iter(
            [
                {"tag_name": "v1.2.3"},
                {"object": {"type": "tag", "sha": "a" * 40}},
                {"object": {"type": "tag", "sha": "b" * 40}},
                {"object": {"type": "commit", "sha": "c" * 40}},
            ]
        )
        requests: list[str] = []

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            requests.append(request.full_url)
            self.assertEqual(timeout, 30)
            return FakeResponse(json.dumps(next(responses)).encode("utf-8"))

        client = sync_action_pins.GitHubReleaseClient("token", opener)

        self.assertEqual(
            client.latest_release("actions/checkout"),
            sync_action_pins.ActionRelease("v1.2.3", "c" * 40),
        )
        self.assertEqual(
            requests,
            [
                "https://api.github.com/repos/actions/checkout/releases/latest",
                "https://api.github.com/repos/actions/checkout/git/ref/tags/v1.2.3",
                "https://api.github.com/repos/actions/checkout/git/tags/" + "a" * 40,
                "https://api.github.com/repos/actions/checkout/git/tags/" + "b" * 40,
            ],
        )

    def test_github_release_client_bounds_nested_annotated_tags(self) -> None:
        nested_tag = {"object": {"type": "tag", "sha": "a" * 40}}
        responses = iter([{"tag_name": "v1.2.3"}, nested_tag, nested_tag])

        def opener(_request: Any, *, timeout: int) -> FakeResponse:
            self.assertEqual(timeout, 30)
            return FakeResponse(json.dumps(next(responses)).encode("utf-8"))

        client = sync_action_pins.GitHubReleaseClient("token", opener)

        with mock.patch.object(sync_action_pins, "MAX_ANNOTATED_TAG_DEPTH", 1):
            with self.assertRaisesRegex(ValueError, "tag nesting exceeds"):
                client.latest_release("actions/checkout")

    def test_github_release_client_rejects_an_invalid_annotated_tag_sha(self) -> None:
        responses = iter(
            [
                {"tag_name": "v1.2.3"},
                {"object": {"type": "tag", "sha": "not-a-sha"}},
            ]
        )

        def opener(_request: Any, *, timeout: int) -> FakeResponse:
            self.assertEqual(timeout, 30)
            return FakeResponse(json.dumps(next(responses)).encode("utf-8"))

        client = sync_action_pins.GitHubReleaseClient("token", opener)

        with self.assertRaisesRegex(ValueError, "does not resolve to a commit"):
            client.latest_release("actions/checkout")

    def test_github_release_client_resolves_codeql_from_stable_action_tags(
        self,
    ) -> None:
        response = [
            {"name": "codeql-bundle-v9.9.9", "commit": {"sha": "a" * 40}},
            {"name": "v4.37.7", "commit": {"sha": "b" * 40}},
            {"name": "v4.37.8", "commit": {"sha": "c" * 40}},
            {"name": "v5.0.0-beta.1", "commit": {"sha": "d" * 40}},
        ]
        requests: list[str] = []

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            requests.append(request.full_url)
            self.assertEqual(timeout, 30)
            return FakeResponse(json.dumps(response).encode("utf-8"))

        client = sync_action_pins.GitHubReleaseClient("token", opener)

        self.assertEqual(
            client.latest_release("github/codeql-action"),
            sync_action_pins.ActionRelease("v4.37.8", "c" * 40),
        )
        self.assertEqual(
            requests,
            [
                "https://api.github.com/repos/github/codeql-action/tags?per_page=100&page=1"
            ],
        )

    def test_github_release_client_paginates_action_tags_and_fails_closed(self) -> None:
        older = {"name": "v4.37.8", "commit": {"sha": "b" * 40}}
        newer = {"name": "v5.0.0", "commit": {"sha": "c" * 40}}
        responses = iter([[older] * 100, [newer]])
        requests: list[str] = []

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            requests.append(request.full_url)
            self.assertEqual(timeout, 30)
            return FakeResponse(json.dumps(next(responses)).encode("utf-8"))

        client = sync_action_pins.GitHubReleaseClient("token", opener)
        self.assertEqual(
            client.latest_release("github/codeql-action"),
            sync_action_pins.ActionRelease("v5.0.0", "c" * 40),
        )
        self.assertEqual(
            requests,
            [
                "https://api.github.com/repos/github/codeql-action/tags?per_page=100&page=1",
                "https://api.github.com/repos/github/codeql-action/tags?per_page=100&page=2",
            ],
        )

        with mock.patch.object(sync_action_pins, "MAX_ACTION_TAG_PAGES", 1):
            capped = sync_action_pins.GitHubReleaseClient(
                "token",
                lambda *_args, **_kwargs: FakeResponse(
                    json.dumps([older] * 100).encode("utf-8")
                ),
            )
            with self.assertRaisesRegex(ValueError, "inventory exceeds"):
                capped.latest_release("github/codeql-action")

    def test_github_release_client_rejects_bad_inputs_and_responses(self) -> None:
        with self.assertRaisesRegex(ValueError, "GITHUB_TOKEN"):
            sync_action_pins.GitHubReleaseClient("")
        client = sync_action_pins.GitHubReleaseClient(
            "token", lambda *_args, **_kwargs: FakeResponse(b"[1]")
        )
        with self.assertRaisesRegex(ValueError, "invalid action repository"):
            client.latest_release("invalid")
        with self.assertRaisesRegex(ValueError, "not an object"):
            client.get_json("/test")
        with self.assertRaisesRegex(ValueError, "bounded object list"):
            client.get_json_list("/test")
        oversized = sync_action_pins.GitHubReleaseClient(
            "token",
            lambda *_args, **_kwargs: FakeResponse(
                b"x" * (sync_action_pins.MAX_RESPONSE_BYTES + 1)
            ),
        )
        with self.assertRaisesRegex(ValueError, "exceeds the size limit"):
            oversized.get_json("/test")
        malformed = sync_action_pins.GitHubReleaseClient(
            "token", lambda *_args, **_kwargs: FakeResponse(b"{")
        )
        with self.assertRaisesRegex(ValueError, "request failed"):
            malformed.get_json("/test")
        for payload, message in (
            ({"tag_name": "main"}, "invalid tag"),
            ({"tag_name": "v1.2.3"}, "no tag object"),
        ):
            with self.subTest(payload=payload):
                bad = sync_action_pins.GitHubReleaseClient(
                    "token",
                    lambda *_args, **_kwargs: FakeResponse(
                        json.dumps(payload).encode()
                    ),
                )
                with self.assertRaisesRegex(ValueError, message):
                    bad.latest_release("actions/checkout")
        failing = sync_action_pins.GitHubReleaseClient(
            "token", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline"))
        )
        with self.assertRaisesRegex(ValueError, "request failed"):
            failing.get_json("/test")

    def test_github_release_client_rejects_unresolvable_release_tags(self) -> None:
        cases = (
            (
                [
                    [
                        {
                            "name": "codeql-bundle-v9.9.9",
                            "commit": {"sha": "a" * 40},
                        },
                    ],
                ],
                "no stable action tag",
                "github/codeql-action",
            ),
            (
                [
                    {"tag_name": "v1.2.3"},
                    {"object": {"type": "tag", "sha": "a" * 40}},
                    {"object": []},
                ],
                "release tag is invalid",
                "actions/checkout",
            ),
            (
                [
                    {"tag_name": "v1.2.3"},
                    {"object": {"type": "commit", "sha": "not-a-sha"}},
                ],
                "does not resolve to a commit",
                "actions/checkout",
            ),
        )
        for payloads, message, repository in cases:
            with self.subTest(message=message):
                responses = iter(payloads)

                def opener(_request: Any, *, timeout: int) -> FakeResponse:
                    self.assertEqual(timeout, 30)
                    return FakeResponse(json.dumps(next(responses)).encode("utf-8"))

                client = sync_action_pins.GitHubReleaseClient("token", opener)
                with self.assertRaisesRegex(ValueError, message):
                    client.latest_release(repository)

    def test_main_writes_changed_paths_and_reports_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed, _asset = self.make_repository(root)
            stdout = StringIO()
            with (
                mock.patch.object(sync_action_pins, "GitHubReleaseClient") as client,
                redirect_stdout(stdout),
            ):
                client.return_value.latest_release.side_effect = self.releases
                self.assertEqual(
                    sync_action_pins.main(["--repository-root", str(root), "--write"]),
                    0,
                )
            changed_paths = [
                Path(path).resolve().relative_to(root.resolve()).as_posix()
                for path in stdout.getvalue().splitlines()
            ]
            self.assertEqual(
                changed_paths,
                [
                    installed.relative_to(root).as_posix(),
                    _asset.relative_to(root).as_posix(),
                ],
            )

            stdout = StringIO()
            with (
                mock.patch.object(sync_action_pins, "GitHubReleaseClient") as client,
                redirect_stdout(stdout),
            ):
                client.return_value.latest_release.side_effect = self.releases
                self.assertEqual(
                    sync_action_pins.main(["--repository-root", str(root), "--write"]),
                    0,
                )
            self.assertEqual(stdout.getvalue(), "Action pins are already current.\n")

        stderr = StringIO()
        with (
            mock.patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False),
            redirect_stderr(stderr),
        ):
            self.assertEqual(sync_action_pins.main([]), 1)
        self.assertIn("GITHUB_TOKEN", stderr.getvalue())

    def test_main_accepts_a_project_workflow_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = self.write_workflow(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  test:\n    steps:\n"
                "      - uses: actions/checkout@" + "a" * 40 + " # v1.0.0\n",
            )
            with mock.patch.object(sync_action_pins, "GitHubReleaseClient") as client:
                client.return_value.latest_release.side_effect = self.releases
                self.assertEqual(
                    sync_action_pins.main(
                        [
                            "--repository-root",
                            str(root),
                            "--workflow-directory",
                            ".github/workflows",
                            "--write",
                        ]
                    ),
                    0,
                )
            self.assertIn("# v9.1.2", workflow.read_text(encoding="utf-8"))

    def test_script_entrypoint_uses_main(self) -> None:
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT_PATH)]),
            mock.patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False),
            self.assertRaisesRegex(SystemExit, "1"),
        ):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")


if __name__ == "__main__":
    unittest.main()
