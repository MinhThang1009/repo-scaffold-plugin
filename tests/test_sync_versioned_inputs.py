from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "sync_versioned_inputs.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location(
    "scripts.sync_versioned_inputs", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load sync_versioned_inputs.py")
versioned_inputs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = versioned_inputs
SPEC.loader.exec_module(versioned_inputs)


class VersionedInputSyncTests(unittest.TestCase):
    def write_repository(self, root: Path) -> None:
        registry = {
            "schema-version": 1,
            "workflow-directories": [
                ".github/workflows",
                "skills/repo-scaffold/assets/workflows",
            ],
            "release-please-configs": [
                "release-please-config.json",
                "skills/repo-scaffold/assets/release-please-config.json",
                "skills/repo-scaffold/assets/release-please-config.vi.json",
            ],
            "requirement-sources": [],
        }
        files = {
            ".github/freshness-trackers.json": json.dumps(registry),
            ".github/workflows/ci.yml": "uses: actions/checkout@"
            + "a" * 40
            + " # v1.0.0\n",
            "skills/repo-scaffold/assets/workflows/ci.yml": "uses: actions/checkout@"
            + "a" * 40
            + " # v1.0.0\n",
            "release-please-config.json": self.release_config(),
            "skills/repo-scaffold/assets/release-please-config.json": self.release_config(),
            "skills/repo-scaffold/assets/release-please-config.vi.json": self.release_config(),
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    @staticmethod
    def release_config() -> str:
        return (
            '{\n  "$schema": '
            '"https://raw.githubusercontent.com/googleapis/release-please/'
            'v17.11.1/schemas/config.json"\n}\n'
        )

    @staticmethod
    def release_lookup(repository: str) -> object:
        releases = {
            "actions/checkout": versioned_inputs.sync_action_pins.ActionRelease(
                "v2.0.0", "b" * 40
            ),
            "googleapis/release-please": versioned_inputs.sync_action_pins.ActionRelease(
                "v17.11.2", "c" * 40
            ),
        }
        return releases[repository]

    def test_synchronizes_registered_action_pins_and_schema_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)

            pending = versioned_inputs.synchronize_versioned_inputs(
                root, self.release_lookup, write=False
            )
            changed = versioned_inputs.synchronize_versioned_inputs(
                root, self.release_lookup, write=True
            )

            self.assertEqual(pending, changed)
            self.assertEqual(len(changed), 5)
            self.assertTrue(
                all(
                    "v17.11.2" in (root / relative).read_text(encoding="utf-8")
                    for relative in (
                        "release-please-config.json",
                        "skills/repo-scaffold/assets/release-please-config.json",
                        "skills/repo-scaffold/assets/release-please-config.vi.json",
                    )
                )
            )
            self.assertTrue(
                all(
                    "b" * 40 in (root / relative).read_text(encoding="utf-8")
                    for relative in (
                        ".github/workflows/ci.yml",
                        "skills/repo-scaffold/assets/workflows/ci.yml",
                    )
                )
            )
            self.assertEqual(
                versioned_inputs.synchronize_versioned_inputs(
                    root, self.release_lookup, write=False
                ),
                [],
            )

    def test_rejects_ambiguous_release_please_schema_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            config = root / "release-please-config.json"
            config.write_text(
                self.release_config() + self.release_config(), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "exactly one supported"):
                versioned_inputs.synchronize_versioned_inputs(
                    root, self.release_lookup, write=False
                )
            with self.assertRaisesRegex(ValueError, "exactly one supported"):
                versioned_inputs.synchronize_versioned_inputs(
                    root, self.release_lookup, write=True
                )
            self.assertIn(
                "a" * 40,
                (root / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
            )

    def test_rejects_prerelease_schema_tag_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)

            def prerelease_lookup(repository: str) -> object:
                if repository == "googleapis/release-please":
                    return versioned_inputs.sync_action_pins.ActionRelease(
                        "v17.11.3-rc.1", "c" * 40
                    )
                return self.release_lookup(repository)

            with self.assertRaisesRegex(ValueError, "stable SemVer"):
                versioned_inputs.synchronize_versioned_inputs(
                    root, prerelease_lookup, write=True
                )
            self.assertIn(
                "a" * 40,
                (root / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
