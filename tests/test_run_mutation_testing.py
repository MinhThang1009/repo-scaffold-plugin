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
        self.SourceFileMutationData = self.FakeSourceFileMutationData

    class FakeSourceFileMutationData:
        def __init__(self, *, path: Path) -> None:
            self.path = path
            self.hash_by_function_name: dict[str, str] = {}

        def load(self) -> None:
            self.hash_by_function_name = {"x_alpha": "0123456789ab"}

    @staticmethod
    def get_mutant_name(filename: Path, function: str) -> str:
        return f"{filename.with_suffix('').as_posix().replace('/', '.')}.{function}"

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


class PlanningMutmut(FakeMutmut):
    def __init__(self) -> None:
        super().__init__()
        self.collect_source_file_mutation_data = self._collect

        class Config:
            @staticmethod
            def ensure_loaded() -> None:
                return None

        self.Config = Config

    @staticmethod
    def copy_src_dir() -> None:
        return None

    @staticmethod
    def copy_also_copy_files() -> None:
        return None

    @staticmethod
    def setup_source_paths() -> None:
        return None

    @staticmethod
    def store_lines_covered_by_tests() -> None:
        return None

    def create_mutants(self, max_children: int) -> None:
        self.arguments = ([], max_children)

    def _run(self, names: list[str], max_children: int) -> None:
        raise AssertionError("plan generation must not execute mutant tests")

    @staticmethod
    def _collect(
        *, mutant_names: list[str]
    ) -> tuple[list[tuple[object, str, object]], dict[str, object]]:
        return (
            [
                (object(), "scripts.alpha__mutmut_1", object()),
                (object(), "scripts.alpha__mutmut_2", object()),
                (object(), "scripts.beta__mutmut_1", object()),
            ],
            {},
        )


class ModernPlanningMutmut(PlanningMutmut):
    @staticmethod
    def config() -> None:
        return None

    @staticmethod
    def set_mutant_under_test(name: str | None) -> None:
        if name is not None:
            os.environ["MUTANT_UNDER_TEST"] = name


class ShardMutmut(FakeMutmut):
    class Config:
        @staticmethod
        def ensure_loaded() -> None:
            return None

    def __init__(self, verdicts: dict[str, int | None]) -> None:
        super().__init__()
        self.verdicts = verdicts

    def collect_source_file_mutation_data(
        self, *, mutant_names: list[str]
    ) -> tuple[list[tuple[object, str, int | None]], dict[str, object]]:
        return (
            [
                (object(), name, value)
                for name, value in self.verdicts.items()
                if not mutant_names or name in mutant_names
            ],
            {},
        )


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
            self.assertIsNotNone(implementation.cwd)
            assert implementation.cwd is not None
            self.assertTrue(os.path.samefile(implementation.cwd, root))
            self.assertEqual(implementation.arguments, ([], 4))
            self.assertTrue(implementation.results[0].unmodified)
            self.assertEqual(
                implementation.results[0].current_hashes,
                {"scripts.alpha.x_alpha": "0123456789ab"},
            )
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

    def test_shard_reuse_runs_only_mutants_without_cached_kills(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = self.write_marker(root, ["scripts/alpha.py"])
            implementation = ShardMutmut({"killed": 1, "pending": None})
            with mock.patch.object(
                run_mutation_testing.multiprocessing,
                "get_start_method",
                return_value="fork",
            ):
                run_mutation_testing.run_mutation_testing(
                    root,
                    max_children=1,
                    mutant_names=["killed", "pending"],
                    mutmut_main=implementation,
                )
            self.assertEqual(implementation.arguments, (["pending"], 1))
            self.assertFalse(marker.exists())

    def test_shard_reuse_skips_a_fully_cached_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = self.write_marker(root, ["scripts/alpha.py"])
            implementation = ShardMutmut({"killed": 3})
            with mock.patch.object(
                run_mutation_testing.multiprocessing,
                "get_start_method",
                return_value="fork",
            ):
                run_mutation_testing.run_mutation_testing(
                    root,
                    max_children=1,
                    mutant_names=["killed"],
                    mutmut_main=implementation,
                )
            self.assertIsNone(implementation.arguments)
            self.assertFalse(marker.exists())

    def test_pending_shard_lookup_is_exact_and_preserves_assignment_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            implementation = ShardMutmut(
                {"first": None, "second": None, "killed": 1, "unassigned": None}
            )
            with mock.patch.object(
                implementation,
                "collect_source_file_mutation_data",
                wraps=implementation.collect_source_file_mutation_data,
            ) as collector:
                pending = run_mutation_testing._pending_reusable_mutants(
                    root, implementation, ["second", "killed", "first"]
                )
            self.assertEqual(pending, ["second", "first"])
            collector.assert_called_once_with(mutant_names=[])

    def test_pending_shard_lookup_rejects_missing_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous_cwd = Path.cwd()
            with self.assertRaisesRegex(ValueError, "missing a shard assignment"):
                run_mutation_testing._pending_reusable_mutants(
                    root, ShardMutmut({"killed": 1}), ["killed", "missing"]
                )
            self.assertEqual(Path.cwd(), previous_cwd)

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
                self.assertRaisesRegex(ValueError, "link or reparse point"),
            ):
                run_mutation_testing.load_reusable_sources(root)

            marker.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(Path, "is_symlink", return_value=False),
                mock.patch.object(
                    Path,
                    "stat",
                    autospec=True,
                    return_value=SimpleNamespace(st_mode=0, st_size=1024 * 1024 + 1),
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
                "scripts/C:alpha.py",
                "scripts/\nalpha.py",
            ):
                with self.subTest(source=source):
                    marker.write_text(
                        json.dumps({"schema_version": 1, "sources": [source]}),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, "invalid reusable"):
                        run_mutation_testing.load_reusable_sources(root)

    def test_shard_plan_is_deterministic_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = run_mutation_testing.write_shard_plan(
                root,
                [
                    "scripts.beta__mutmut_1",
                    "scripts.alpha__mutmut_2",
                    "scripts.alpha__mutmut_1",
                ],
                2,
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["shards"],
                [
                    ["scripts.alpha__mutmut_1", "scripts.beta__mutmut_1"],
                    ["scripts.alpha__mutmut_2"],
                ],
            )
            self.assertEqual(
                run_mutation_testing.load_shard_names(root, 1),
                ["scripts.alpha__mutmut_2"],
            )
            path.write_text(
                '{"schema_version":1,"shards":[["a"],["a"]]}', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "more than once"):
                run_mutation_testing.load_shard_names(root, 0)

    def test_planning_generates_an_exact_shard_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            implementation = PlanningMutmut()
            with mock.patch.dict(os.environ, {"MUTANT_UNDER_TEST": "existing-run"}):
                path = run_mutation_testing.prepare_mutation_shards(
                    root, max_children=4, shard_count=2, mutmut_main=implementation
                )
                self.assertEqual(os.environ.get("MUTANT_UNDER_TEST"), "existing-run")
            self.assertEqual(implementation.arguments, ([], 4))
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["shards"],
                [
                    ["scripts.alpha__mutmut_1", "scripts.beta__mutmut_1"],
                    ["scripts.alpha__mutmut_2"],
                ],
            )

    def test_planning_supports_current_mutmut_config_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            implementation = ModernPlanningMutmut()
            path = run_mutation_testing.prepare_mutation_shards(
                root, max_children=4, shard_count=2, mutmut_main=implementation
            )
            self.assertTrue(path.is_file())
            self.assertNotIn("MUTANT_UNDER_TEST", os.environ)

    def test_planning_rejects_invalid_or_linked_repository_roots(self) -> None:
        with self.assertRaisesRegex(ValueError, "repository root"):
            run_mutation_testing.prepare_mutation_shards(
                Path("missing-mutation-repository"),
                max_children=4,
                shard_count=2,
                mutmut_main=PlanningMutmut(),
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(
                    run_mutation_testing,
                    "_is_link_or_reparse",
                    side_effect=lambda path: path == root,
                ),
                self.assertRaisesRegex(ValueError, "repository root is a link"),
            ):
                run_mutation_testing.prepare_mutation_shards(
                    root,
                    max_children=4,
                    shard_count=2,
                    mutmut_main=PlanningMutmut(),
                )

    def test_planning_reuse_requires_fork_process_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_marker(root, ["scripts/alpha.py"])
            with (
                mock.patch.object(
                    run_mutation_testing.multiprocessing,
                    "get_start_method",
                    return_value="spawn",
                ),
                self.assertRaisesRegex(ValueError, "requires fork"),
            ):
                run_mutation_testing.prepare_mutation_shards(
                    root,
                    max_children=4,
                    shard_count=2,
                    mutmut_main=PlanningMutmut(),
                )

    def test_planning_restores_process_state_after_generation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            implementation = PlanningMutmut()
            original = implementation.create_mutants_for_file
            previous_cwd = Path.cwd()
            previous_marker = os.environ.get("MUTANT_UNDER_TEST")
            with (
                mock.patch.object(
                    implementation,
                    "create_mutants",
                    side_effect=OSError("generation failed"),
                ),
                self.assertRaisesRegex(OSError, "generation failed"),
            ):
                run_mutation_testing.prepare_mutation_shards(
                    root, max_children=4, shard_count=2, mutmut_main=implementation
                )
            self.assertEqual(Path.cwd(), previous_cwd)
            self.assertEqual(implementation.create_mutants_for_file, original)
            self.assertFalse(
                root.joinpath(
                    "mutants", run_mutation_testing.REUSABLE_SOURCES_NAME
                ).exists()
            )
            self.assertEqual(os.environ.get("MUTANT_UNDER_TEST"), previous_marker)

    def test_shard_plan_rejects_invalid_names_shapes_and_indices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for names, shard_count, message in (
                (["valid"], 0, "shard count"),
                ([], 1, "cannot be empty"),
                (["duplicate", "duplicate"], 1, "duplicate"),
                (["invalid\nname"], 1, "invalid mutant name"),
            ):
                with self.subTest(names=names, shard_count=shard_count):
                    with self.assertRaisesRegex(ValueError, message):
                        run_mutation_testing.shard_mutants(names, shard_count)

            plan = root / "mutants" / run_mutation_testing.SHARD_PLAN_NAME
            plan.parent.mkdir()
            with self.assertRaisesRegex(ValueError, "could not read"):
                run_mutation_testing.load_shard_names(root, 0)
            plan.write_text(
                '{"schema_version":0,"shards":[["name"]]}', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "invalid schema"):
                run_mutation_testing.load_shard_names(root, 0)
            plan.write_text(
                '{"schema_version":1,"shards":[["name"]]}', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "outside"):
                run_mutation_testing.load_shard_names(root, 1)
            plan.write_text('{"schema_version":1,"shards":[[]]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid shard"):
                run_mutation_testing.load_shard_names(root, 0)

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

        with (
            mock.patch.object(
                run_mutation_testing.importlib.metadata,
                "version",
                side_effect=run_mutation_testing.importlib.metadata.PackageNotFoundError,
            ),
            self.assertRaisesRegex(ValueError, "but it is not installed"),
        ):
            run_mutation_testing.load_mutmut()

        with (
            mock.patch.object(
                run_mutation_testing.importlib.metadata,
                "version",
                return_value=run_mutation_testing.MUTMUT_VERSION,
            ),
            mock.patch.object(
                run_mutation_testing.importlib,
                "import_module",
                side_effect=ModuleNotFoundError("No module named 'mutmut.__main__'"),
            ),
            self.assertRaisesRegex(ValueError, "could not import mutmut"),
        ):
            run_mutation_testing.load_mutmut()

    def test_marker_loader_rejects_linked_mutants_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(
                    run_mutation_testing,
                    "_is_link_or_reparse",
                    side_effect=lambda path: path.name == "mutants",
                ),
                self.assertRaisesRegex(ValueError, "link or reparse point"),
            ):
                run_mutation_testing.load_reusable_sources(root)

    def test_marker_path_must_remain_below_repository_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            with self.assertRaisesRegex(ValueError, "escapes repository"):
                run_mutation_testing._assert_safe_marker_path(
                    root, Path(directory) / "outside.json"
                )

    def test_runner_rejects_a_linked_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(
                    run_mutation_testing,
                    "_is_link_or_reparse",
                    side_effect=lambda path: path == root,
                ),
                self.assertRaisesRegex(ValueError, "repository root is a link"),
            ):
                run_mutation_testing.run_mutation_testing(
                    root, max_children=1, mutmut_main=FakeMutmut()
                )

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
            runner.assert_called_once_with(root, max_children=8, mutant_names=None)

            with mock.patch.object(
                run_mutation_testing,
                "prepare_mutation_shards",
                return_value=root / "mutants" / "mutation-shards.json",
            ) as planner:
                self.assertEqual(
                    run_mutation_testing.main(
                        ["--repository-root", str(root), "--plan-shards", "2"]
                    ),
                    0,
                )
            planner.assert_called_once_with(root, max_children=4, shard_count=2)

            errors = StringIO()
            with redirect_stderr(errors):
                self.assertEqual(
                    run_mutation_testing.main(
                        ["--plan-shards", "2", "--shard-index", "0"]
                    ),
                    1,
                )
            self.assertIn("cannot plan mutation shards", errors.getvalue())

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
