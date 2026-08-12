from __future__ import annotations

import importlib.util
import json
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "prepare_mutation_cache.py"
SPEC = importlib.util.spec_from_file_location(
    "scripts.prepare_mutation_cache", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load prepare_mutation_cache.py")
prepare_mutation_cache = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare_mutation_cache
SPEC.loader.exec_module(prepare_mutation_cache)


class MutationCacheTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        files = {
            "scripts/alpha.py": "def alpha():\n    return 1\n",
            "scripts/beta.py": "def beta():\n    return 2\n",
            "tests/test_alpha.py": ("def test_alpha():\n    assert True\n"),
            "pyproject.toml": "[tool.mutmut]\n",
            "README.md": "# Fixture\n",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def write_state(
        self,
        root: Path,
        relative: str,
        exit_codes: dict[str, int | None] | None = None,
    ) -> None:
        source = root / relative
        mutant = root / "mutants" / relative
        mutant.parent.mkdir(parents=True, exist_ok=True)
        mutant.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        mutant.touch()
        document = {
            "exit_code_by_key": exit_codes
            or {
                f"{relative}.killed_one": 1,
                f"{relative}.killed_three": 3,
                f"{relative}.survived": 0,
                f"{relative}.timeout": 36,
                f"{relative}.pending": None,
            },
            "type_check_error_by_key": {f"{relative}.survived": "stale"},
            "durations_by_key": {},
            "estimated_durations_by_key": {},
        }
        Path(f"{mutant}.meta").write_text(json.dumps(document), encoding="utf-8")

    def record_fixture(self, root: Path) -> None:
        self.write_state(root, "scripts/alpha.py")
        self.write_state(root, "scripts/beta.py", {"beta.killed": 1})
        (root / "mutants" / "mutmut-stats.json").write_text("{}", encoding="utf-8")
        (root / "mutants" / "mutmut-cicd-stats.json").write_text("{}", encoding="utf-8")
        (root / "mutants" / "mutation-results.txt").write_text(
            "stale", encoding="utf-8"
        )
        prepare_mutation_cache.record_cache(root)

    def test_record_and_prepare_preserve_only_killed_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.record_fixture(root)

            result = prepare_mutation_cache.prepare_cache(root)
            meta_path = root / "mutants" / "scripts" / "alpha.py.meta"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

            self.assertEqual(
                result,
                prepare_mutation_cache.PreparationResult(False, 3, 3, 0),
            )
            self.assertEqual(
                meta["exit_code_by_key"],
                {
                    "scripts/alpha.py.killed_one": 1,
                    "scripts/alpha.py.killed_three": 3,
                    "scripts/alpha.py.survived": None,
                    "scripts/alpha.py.timeout": None,
                    "scripts/alpha.py.pending": None,
                },
            )
            self.assertEqual(meta["type_check_error_by_key"], {})
            self.assertGreater(
                (root / "mutants" / "scripts" / "alpha.py").stat().st_mtime_ns,
                (root / "scripts" / "alpha.py").stat().st_mtime_ns,
            )
            self.assertTrue((root / "mutants" / "mutmut-stats.json").exists())
            self.assertFalse((root / "mutants" / "mutmut-cicd-stats.json").exists())
            self.assertFalse(
                (root / "mutants" / "mutation-cache-manifest.json").exists()
            )

    def test_additive_tests_keep_kills_and_refresh_test_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.record_fixture(root)
            test_path = root / "tests" / "test_alpha.py"
            test_path.write_text(
                test_path.read_text(encoding="utf-8")
                + "\ndef test_survivor():\n    assert True\n",
                encoding="utf-8",
            )

            result = prepare_mutation_cache.prepare_cache(root)

            self.assertFalse(result.full_reset)
            self.assertEqual(result.preserved_killed, 3)
            self.assertFalse((root / "mutants" / "mutmut-stats.json").exists())
            alpha_meta = json.loads(
                (root / "mutants" / "scripts" / "alpha.py.meta").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIsNone(
                alpha_meta["exit_code_by_key"]["scripts/alpha.py.survived"]
            )

    def test_new_test_file_is_compatible_with_cached_kills(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.record_fixture(root)
            (root / "tests" / "test_new.py").write_text(
                "def test_new():\n    assert True\n", encoding="utf-8"
            )

            result = prepare_mutation_cache.prepare_cache(root)

            self.assertFalse(result.full_reset)
            self.assertEqual(result.preserved_killed, 3)
            self.assertFalse((root / "mutants" / "mutmut-stats.json").exists())

    def test_destructive_test_or_support_change_forces_full_reset(self) -> None:
        mutations = (
            lambda root: (root / "tests" / "test_alpha.py").write_text(
                "def replacement():\n    pass\n", encoding="utf-8"
            ),
            lambda root: (root / "README.md").write_text(
                "# Changed\n", encoding="utf-8"
            ),
            lambda root: (root / "tests" / "test_alpha.py").unlink(),
        )
        for mutate in mutations:
            with (
                self.subTest(mutate=mutate),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.make_repository(root)
                self.record_fixture(root)
                mutate(root)

                result = prepare_mutation_cache.prepare_cache(root)

                self.assertTrue(result.full_reset)
                self.assertEqual(list((root / "mutants").iterdir()), [])

    def test_source_change_forces_a_full_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.record_fixture(root)
            (root / "scripts" / "alpha.py").write_text(
                "def alpha():\n    return 10\n", encoding="utf-8"
            )

            result = prepare_mutation_cache.prepare_cache(root)

            self.assertTrue(result.full_reset)
            self.assertEqual(list((root / "mutants").iterdir()), [])
            self.assertFalse((root / "mutants" / "mutmut-stats.json").exists())

    def test_invalid_manifest_or_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.record_fixture(root)
            manifest = root / "mutants" / "mutation-cache-manifest.json"
            manifest.write_text(
                '{"schema_version": 1, "schema_version": 1}', encoding="utf-8"
            )

            result = prepare_mutation_cache.prepare_cache(root)

            self.assertTrue(result.full_reset)
            self.assertEqual(list((root / "mutants").iterdir()), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.record_fixture(root)
            (root / "mutants" / "scripts" / "alpha.py.meta").write_text(
                "[]", encoding="utf-8"
            )

            result = prepare_mutation_cache.prepare_cache(root)

            self.assertTrue(result.full_reset)
            self.assertEqual(list((root / "mutants").iterdir()), [])

    def test_restored_state_is_integrity_checked_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.record_fixture(root)
            unexpected = root / "mutants" / "tests" / "test_injected.py"
            unexpected.parent.mkdir(parents=True)
            unexpected.write_text("raise RuntimeError\n", encoding="utf-8")

            result = prepare_mutation_cache.prepare_cache(root)

            self.assertFalse(result.full_reset)
            self.assertFalse(unexpected.exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.record_fixture(root)
            (root / "mutants" / "scripts" / "alpha.py").write_text(
                "tampered\n", encoding="utf-8"
            )

            result = prepare_mutation_cache.prepare_cache(root)

            self.assertTrue(result.full_reset)
            self.assertEqual(list((root / "mutants").iterdir()), [])

    def test_manifest_schema_and_path_validation_is_strict(self) -> None:
        valid = prepare_mutation_cache.ProjectSnapshot(
            source_hashes={"scripts/alpha.py": "0" * 64},
            test_sources={"tests/test_alpha.py": "def test_alpha():\n    pass\n"},
            support_hashes={"README.md": "1" * 64},
        )
        invalid_documents = (
            [],
            {**prepare_mutation_cache.manifest_document(valid), "extra": 1},
            {
                **prepare_mutation_cache.manifest_document(valid),
                "schema_version": 2,
            },
            {
                **prepare_mutation_cache.manifest_document(valid),
                "source_hashes": {"../escape.py": "0" * 64},
            },
            {
                **prepare_mutation_cache.manifest_document(valid),
                "test_sources": {"scripts/not-a-test.py": "pass\n"},
            },
            {
                **prepare_mutation_cache.manifest_document(valid),
                "support_hashes": {"README.md": "not-a-digest"},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            for document in invalid_documents:
                with self.subTest(document=document):
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        prepare_mutation_cache.load_manifest(path)

    def test_manifest_validators_enforce_member_and_size_bounds(self) -> None:
        digest = "0" * 64
        cases = (
            lambda: prepare_mutation_cache._validate_digest_map(
                [], field="source_hashes"
            ),
            lambda: prepare_mutation_cache._validate_digest_map(
                {1: digest}, field="source_hashes"
            ),
            lambda: prepare_mutation_cache._validate_digest_map(
                {"scripts/alpha.py": "invalid"}, field="source_hashes"
            ),
            lambda: prepare_mutation_cache._validate_test_sources([]),
            lambda: prepare_mutation_cache._validate_test_sources({1: "source"}),
            lambda: prepare_mutation_cache._validate_test_sources(
                {"scripts/not-a-test.py": "source"}
            ),
            lambda: prepare_mutation_cache._validate_source_paths(
                {"outside.py": digest}
            ),
            lambda: prepare_mutation_cache._source_state_paths(
                Path("mutants"), "outside.py"
            ),
        )
        for operation in cases:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()

        with (
            mock.patch.object(prepare_mutation_cache, "MAX_TOTAL_BYTES", 0),
            self.assertRaisesRegex(ValueError, "test_sources exceeds"),
        ):
            prepare_mutation_cache._validate_test_sources(
                {"tests/test_alpha.py": "source"}
            )

    def test_project_inventory_enforces_symlink_file_and_total_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_text("content", encoding="utf-8")
            with (
                mock.patch.object(Path, "is_symlink", return_value=True),
                self.assertRaisesRegex(ValueError, "symlink"),
            ):
                prepare_mutation_cache._project_files(root)

            with (
                mock.patch.object(prepare_mutation_cache, "MAX_FILE_BYTES", 0),
                self.assertRaisesRegex(ValueError, "file .* exceeds"),
            ):
                prepare_mutation_cache._project_files(root)

            with (
                mock.patch.object(prepare_mutation_cache, "MAX_PROJECT_FILES", 0),
                self.assertRaisesRegex(ValueError, "inventory exceeds"),
            ):
                prepare_mutation_cache._project_files(root)

    def test_manifest_and_metadata_loaders_enforce_all_schema_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "document.json"
            path.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(prepare_mutation_cache, "MAX_TOTAL_BYTES", 0),
                self.assertRaisesRegex(ValueError, "manifest exceeds"),
            ):
                prepare_mutation_cache.load_manifest(path)

            metadata_documents = (
                "{",
                "{}",
                json.dumps(
                    {
                        "exit_code_by_key": [],
                        "durations_by_key": {},
                        "estimated_durations_by_key": {},
                    }
                ),
                json.dumps(
                    {
                        "exit_code_by_key": {"mutant": True},
                        "durations_by_key": {},
                        "estimated_durations_by_key": {},
                    }
                ),
                json.dumps(
                    {
                        "exit_code_by_key": {},
                        "durations_by_key": [],
                        "estimated_durations_by_key": {},
                    }
                ),
                json.dumps(
                    {
                        "exit_code_by_key": {},
                        "durations_by_key": {},
                        "estimated_durations_by_key": {},
                        "type_check_error_by_key": [],
                    }
                ),
            )
            for document in metadata_documents:
                with self.subTest(document=document):
                    path.write_text(document, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        prepare_mutation_cache._load_meta(path)

            path.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(prepare_mutation_cache, "MAX_META_BYTES", 0),
                self.assertRaisesRegex(ValueError, "unsafe or oversized"),
            ):
                prepare_mutation_cache._load_meta(path)

    def test_state_collection_requires_complete_bounded_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mutants = Path(directory) / "mutants"
            mutants.mkdir()
            sources = {"scripts/alpha.py": "0" * 64}
            with self.assertRaisesRegex(ValueError, "state is missing"):
                prepare_mutation_cache._collect_state_hashes(mutants, sources)

            for relative in prepare_mutation_cache._expected_state_paths(sources):
                path = mutants / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("state", encoding="utf-8")
            with (
                mock.patch.object(prepare_mutation_cache, "MAX_META_BYTES", 0),
                self.assertRaisesRegex(ValueError, "exceeds the size limits"),
            ):
                prepare_mutation_cache._collect_state_hashes(mutants, sources)

    def test_state_sanitizer_removes_directory_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            linked = root / "linked"
            with (
                mock.patch.object(
                    prepare_mutation_cache.os,
                    "walk",
                    return_value=[(str(root), ["linked"], [])],
                ),
                mock.patch.object(
                    Path,
                    "is_symlink",
                    autospec=True,
                    side_effect=lambda path: path == linked,
                ),
                mock.patch.object(Path, "unlink", autospec=True) as unlink,
            ):
                prepare_mutation_cache._sanitize_restored_state(root, {})

            unlink.assert_called_once_with(linked)

    def test_cache_paths_reject_symlink_directories_and_special_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mutants = root / "mutants"
            mutants.mkdir()
            with (
                mock.patch.object(
                    Path,
                    "is_symlink",
                    autospec=True,
                    side_effect=lambda path: path.name == "mutants",
                ),
                self.assertRaisesRegex(ValueError, "must not be a symlink"),
            ):
                prepare_mutation_cache._mutation_root(root)

            source_state = mutants / "scripts" / "alpha.py"
            source_state.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "not a file"):
                prepare_mutation_cache._remove_source_state(mutants, "scripts/alpha.py")

        with tempfile.TemporaryDirectory() as directory:
            mutants = Path(directory) / "mutants"
            source_state = mutants / "scripts" / "alpha.py"
            source_state.parent.mkdir(parents=True)
            source_state.write_text("state", encoding="utf-8")
            Path(f"{source_state}.meta").write_text("{}", encoding="utf-8")
            prepare_mutation_cache._remove_source_state(mutants, "scripts/alpha.py")
            self.assertFalse(source_state.exists())
            prepare_mutation_cache._remove_source_state(mutants, "scripts/alpha.py")

        mutation_root = mock.Mock(spec=Path)
        mutation_root.mkdir.return_value = None
        child = mock.Mock(spec=Path)
        child.name = "special"
        child.is_symlink.return_value = False
        child.is_file.return_value = False
        child.is_dir.return_value = False
        mutation_root.iterdir.return_value = [child]
        with self.assertRaisesRegex(ValueError, "unsupported mutation cache entry"):
            prepare_mutation_cache._clear_mutation_state(mutation_root)

    def test_incomplete_or_racing_source_state_is_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            mutants = root / "mutants"
            mutants.mkdir()
            with self.assertRaisesRegex(ValueError, "source state is incomplete"):
                prepare_mutation_cache._prepare_source_state(
                    root, mutants, "scripts/alpha.py"
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.record_fixture(root)
            with mock.patch.object(
                prepare_mutation_cache,
                "_prepare_source_state",
                side_effect=ValueError("raced"),
            ):
                result = prepare_mutation_cache.prepare_cache(root)

            self.assertFalse(result.full_reset)
            self.assertEqual(result.invalidated_files, 2)

    def test_prepare_discards_non_file_stats_and_summary_paths(self) -> None:
        cases = (
            ("mutmut-stats.json", True),
            ("mutmut-cicd-stats.json", False),
            ("mutation-results.txt", False),
        )
        for name, change_test in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.make_repository(root)
                self.record_fixture(root)
                target = root / "mutants" / name
                target.unlink()
                target.mkdir()
                if change_test:
                    test_path = root / "tests" / "test_alpha.py"
                    test_path.write_text(
                        test_path.read_text(encoding="utf-8")
                        + "\ndef test_added():\n    assert True\n",
                        encoding="utf-8",
                    )

                result = prepare_mutation_cache.prepare_cache(root)

                if name == "mutmut-stats.json":
                    self.assertTrue(result.full_reset)
                else:
                    self.assertFalse(result.full_reset)
                    self.assertFalse(target.exists())

    def test_record_requires_completed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            with self.assertRaisesRegex(ValueError, "completed mutation state"):
                prepare_mutation_cache.record_cache(root)

    def test_snapshot_rejects_symlinks_and_non_utf8_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            test_path = root / "tests" / "test_alpha.py"
            test_path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "not UTF-8"):
                prepare_mutation_cache.snapshot_project(root)

            test_path.write_text("pass\n", encoding="utf-8")
            link = root / "linked"
            try:
                link.symlink_to(root / "README.md")
            except OSError:
                self.skipTest("symlink creation is unavailable")
            with self.assertRaisesRegex(ValueError, "symlink"):
                prepare_mutation_cache.snapshot_project(root)

    def test_main_and_entrypoint_report_operations_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.write_state(root, "scripts/alpha.py", {"alpha.killed": 1})
            self.write_state(root, "scripts/beta.py", {"beta.killed": 1})
            (root / "mutants" / "mutmut-stats.json").write_text("{}", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    prepare_mutation_cache.main(
                        ["record", "--repository-root", str(root)]
                    ),
                    0,
                )
            self.assertEqual(output.getvalue(), "Recorded mutation cache inputs.\n")

            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    prepare_mutation_cache.main(
                        ["prepare", "--repository-root", str(root)]
                    ),
                    0,
                )
            self.assertIn("preserved_killed=2", output.getvalue())

            errors = StringIO()
            with redirect_stderr(errors):
                self.assertEqual(
                    prepare_mutation_cache.main(
                        ["record", "--repository-root", str(root / "missing")]
                    ),
                    1,
                )
            self.assertIn("repository root is not a directory", errors.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            self.write_state(root, "scripts/alpha.py", {"alpha.killed": 1})
            self.write_state(root, "scripts/beta.py", {"beta.killed": 1})
            (root / "mutants" / "mutmut-stats.json").write_text("{}", encoding="utf-8")
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                output = StringIO()
                with (
                    mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "record"]),
                    redirect_stdout(output),
                    self.assertRaises(SystemExit) as raised,
                ):
                    runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
            finally:
                os.chdir(original_cwd)
            self.assertEqual(raised.exception.code, 0)
            self.assertIn("Recorded mutation cache inputs", output.getvalue())

    def test_help_documents_both_cache_operations(self) -> None:
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            prepare_mutation_cache.parse_args(["--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("{prepare,record}", output.getvalue())


if __name__ == "__main__":
    unittest.main()
