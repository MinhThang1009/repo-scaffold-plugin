from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "run_mutation_testing.py"
SPEC = importlib.util.spec_from_file_location(
    "scripts.run_mutation_testing", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load run_mutation_testing.py")
run_mutation_testing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_mutation_testing
SPEC.loader.exec_module(run_mutation_testing)


class FakeMutmut:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.original_calls: list[tuple[Path, Path]] = []
        self.results: list[SimpleNamespace] = []
        self.cwd: Path | None = None
        self.arguments: tuple[list[str], int] | None = None
        self.FileMutationResult = SimpleNamespace

    def create_mutants_for_file(
        self, filename: Path, output_path: Path
    ) -> SimpleNamespace:
        self.original_calls.append((filename, output_path))
        return SimpleNamespace(unmodified=False)

    def _run(self, names: list[str], max_children: int) -> None:
        self.cwd = Path.cwd()
        self.arguments = (names, max_children)
        self.results = [
            self.create_mutants_for_file(
                Path("scripts/alpha.py"), Path("mutants/scripts/alpha.py")
            ),
            self.create_mutants_for_file(
                Path("scripts/new.py"), Path("mutants/scripts/new.py")
            ),
        ]
        if self.fail:
            raise OSError("mutmut failed")


class MutationRunnerTests(unittest.TestCase):
    def write_marker(self, root: Path, sources: object) -> Path:
        marker = root / "mutants" / run_mutation_testing.REUSABLE_SOURCES_NAME
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"schema_version": 1, "sources": sources}), encoding="utf-8"
        )
        return marker

    def test_runner_preserves_reusable_generation_and_delegates_new_sources(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = self.write_marker(root, ["scripts/alpha.py"])
            implementation = FakeMutmut()
            original = implementation.create_mutants_for_file
            previous_cwd = Path.cwd()

            with mock.patch.object(
                run_mutation_testing.multiprocessing,
                "get_start_method",
                return_value="fork",
            ):
                run_mutation_testing.run_mutation_testing(
                    root, max_children=4, mutmut_main=implementation
                )

            self.assertEqual(Path.cwd(), previous_cwd)
            self.assertEqual(implementation.cwd, root.resolve())
            self.assertEqual(implementation.arguments, ([], 4))
            self.assertTrue(implementation.results[0].unmodified)
            self.assertFalse(implementation.results[1].unmodified)
            self.assertEqual(
                implementation.original_calls,
                [(Path("scripts/new.py"), Path("mutants/scripts/new.py"))],
            )
            self.assertEqual(implementation.create_mutants_for_file, original)
            self.assertFalse(marker.exists())

    def test_runner_without_marker_uses_normal_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            implementation = FakeMutmut()

            run_mutation_testing.run_mutation_testing(
                root, max_children=2, mutmut_main=implementation
            )

            self.assertEqual(len(implementation.original_calls), 2)

    def test_runner_restores_process_state_after_mutmut_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = self.write_marker(root, ["scripts/alpha.py"])
            implementation = FakeMutmut(fail=True)
            original = implementation.create_mutants_for_file
            previous_cwd = Path.cwd()

            with (
                mock.patch.object(
                    run_mutation_testing.multiprocessing,
                    "get_start_method",
                    return_value="fork",
                ),
                self.assertRaisesRegex(OSError, "mutmut failed"),
            ):
                run_mutation_testing.run_mutation_testing(
                    root, max_children=1, mutmut_main=implementation
                )

            self.assertEqual(Path.cwd(), previous_cwd)
            self.assertEqual(implementation.create_mutants_for_file, original)
            self.assertFalse(marker.exists())

    def test_marker_loader_rejects_malformed_and_unsafe_documents(self) -> None:
        invalid_documents: tuple[object, ...] = (
            [],
            {"schema_version": 1},
            {"schema_version": 2, "sources": []},
            {"schema_version": 1, "sources": "scripts/alpha.py"},
            {"schema_version": 1, "sources": [1]},
            {"schema_version": 1, "sources": ["../escape.py"]},
            {"schema_version": 1, "sources": ["tests/test_alpha.py"]},
            {"schema_version": 1, "sources": ["scripts/alpha.txt"]},
            {
                "schema_version": 1,
                "sources": ["scripts/alpha.py", "scripts/alpha.py"],
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                run_mutation_testing.load_reusable_sources(root), frozenset()
            )
            marker = self.write_marker(root, [])
            for document in invalid_documents:
                with self.subTest(document=document):
                    marker.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        run_mutation_testing.load_reusable_sources(root)

            marker.write_text('{"sources": [], "sources": []}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "could not read"):
                run_mutation_testing.load_reusable_sources(root)

            marker.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "could not read"):
                run_mutation_testing.load_reusable_sources(root)

            with (
                mock.patch.object(Path, "is_symlink", return_value=True),
                self.assertRaisesRegex(ValueError, "unsafe or oversized"),
            ):
                run_mutation_testing.load_reusable_sources(root)

            marker.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(Path, "is_symlink", return_value=False),
                mock.patch.object(
                    Path,
                    "stat",
                    autospec=True,
                    return_value=SimpleNamespace(st_size=1024 * 1024 + 1),
                ),
                self.assertRaisesRegex(ValueError, "unsafe or oversized"),
            ):
                run_mutation_testing.load_reusable_sources(root)

    def test_marker_loader_enforces_count_and_path_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = self.write_marker(root, [])
            with mock.patch.object(run_mutation_testing, "MAX_REUSABLE_SOURCES", 0):
                marker.write_text(
                    json.dumps({"schema_version": 1, "sources": ["scripts/alpha.py"]}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "invalid schema"):
                    run_mutation_testing.load_reusable_sources(root)

            for source in (
                "",
                "/scripts/alpha.py",
                "scripts\\alpha.py",
                "./scripts/alpha.py",
            ):
                with self.subTest(source=source):
                    marker.write_text(
                        json.dumps({"schema_version": 1, "sources": [source]}),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, "invalid reusable"):
                        run_mutation_testing.load_reusable_sources(root)

    def test_reviewed_mutmut_loader_rejects_version_drift(self) -> None:
        implementation = object()
        with (
            mock.patch.object(
                run_mutation_testing.importlib.metadata,
                "version",
                return_value=run_mutation_testing.MUTMUT_VERSION,
            ),
            mock.patch.object(
                run_mutation_testing.importlib,
                "import_module",
                return_value=implementation,
            ) as importer,
        ):
            self.assertIs(run_mutation_testing.load_mutmut(), implementation)
        importer.assert_called_once_with("mutmut.__main__")

        with (
            mock.patch.object(
                run_mutation_testing.importlib.metadata,
                "version",
                return_value="9.9.9",
            ),
            self.assertRaisesRegex(ValueError, "requires mutmut"),
        ):
            run_mutation_testing.load_mutmut()

    def test_incremental_reuse_requires_fork_process_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = self.write_marker(root, ["scripts/alpha.py"])
            with (
                mock.patch.object(
                    run_mutation_testing.multiprocessing,
                    "get_start_method",
                    return_value="spawn",
                ),
                self.assertRaisesRegex(ValueError, "requires fork"),
            ):
                run_mutation_testing.run_mutation_testing(
                    root, max_children=1, mutmut_main=FakeMutmut()
                )
            self.assertTrue(marker.exists())

    def test_uninitialized_generation_hook_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "was not initialized"):
            run_mutation_testing._create_or_reuse_mutants(
                Path("scripts/new.py"), Path("mutants/scripts/new.py")
            )

    def test_main_argument_parsing_and_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                run_mutation_testing, "run_mutation_testing"
            ) as runner:
                self.assertEqual(
                    run_mutation_testing.main(
                        ["--repository-root", str(root), "--max-children", "8"]
                    ),
                    0,
                )
            runner.assert_called_once_with(root, max_children=8)

            errors = StringIO()
            with (
                mock.patch.object(
                    run_mutation_testing,
                    "run_mutation_testing",
                    side_effect=ValueError("invalid state"),
                ),
                redirect_stderr(errors),
            ):
                self.assertEqual(run_mutation_testing.main([]), 1)
            self.assertIn("invalid state", errors.getvalue())

        with self.assertRaisesRegex(ValueError, "repository root"):
            run_mutation_testing.run_mutation_testing(
                Path("missing-mutation-repository"),
                max_children=4,
                mutmut_main=FakeMutmut(),
            )

        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            run_mutation_testing.parse_args(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--max-children", output.getvalue())

        with (
            mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--help"]),
            redirect_stdout(StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
        self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
