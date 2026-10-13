from __future__ import annotations

import gc
import runpy
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest import mock

from scripts import run_mutation_testing


ROOT = Path(__file__).resolve().parents[1]
HOOK = runpy.run_path(str(ROOT / "tests/conftest.py"))["pytest_sessionfinish"]


class MutationMemoryCleanupTests(unittest.TestCase):
    def test_runner_startup_collects_post_pytest_cycles_before_freezing(self) -> None:
        class Cycle:
            self: Cycle

        enabled = gc.isenabled()
        gc.disable()
        try:
            orphan = Cycle()
            orphan.self = orphan
            reference = weakref.ref(orphan)
            del orphan
            live = {"verdict": 1, "cache": object()}

            def freeze_boundary() -> object:
                self.assertIsNone(reference(), "post-pytest cycles must not be frozen")
                self.assertFalse(gc.isenabled())
                return live

            backend_runner = mock.Mock(startup=freeze_boundary)
            factory = mock.Mock(return_value=backend_runner)

            def execute(names: list[str], children: int) -> None:
                self.assertEqual(names, [])
                self.assertEqual(children, 4)
                created = backend.get_mutant_runner(children)
                self.assertIs(created.startup(), live)

            backend = mock.Mock(
                get_mutant_runner=factory,
                create_mutants_for_file=mock.Mock(),
                _run=execute,
            )
            with (
                tempfile.TemporaryDirectory() as directory,
                mock.patch.object(gc, "unfreeze") as unfreeze,
            ):
                run_mutation_testing.run_mutation_testing(
                    Path(directory), max_children=4, mutmut_main=backend
                )
            self.assertIs(backend.get_mutant_runner, factory)
            factory.assert_called_once_with(4)
            unfreeze.assert_not_called()
        finally:
            gc.collect()
            if enabled:
                gc.enable()

    def test_session_finish_reclaims_unreachable_cycles_and_preserves_live_state(
        self,
    ) -> None:
        class Cycle:
            self: Cycle

        enabled = gc.isenabled()
        gc.disable()
        try:
            orphan = Cycle()
            orphan.self = orphan
            reference = weakref.ref(orphan)
            del orphan
            live = Cycle()
            live.self = live
            session = mock.Mock(exitstatus=1)
            self.assertIsNotNone(reference())
            self.assertIsNone(HOOK(session, 1))
            self.assertIsNone(reference())
            self.assertIs(live.self, live)
            self.assertEqual(session.exitstatus, 1)
            self.assertFalse(gc.isenabled())
        finally:
            if enabled:
                gc.enable()

    def test_session_finish_performs_one_full_collection_without_unfreezing(
        self,
    ) -> None:
        with (
            mock.patch.object(gc, "collect", return_value=0) as collect,
            mock.patch.object(gc, "unfreeze") as unfreeze,
        ):
            self.assertIsNone(HOOK(object(), 0))
        collect.assert_called_once_with()
        unfreeze.assert_not_called()

    def test_native_trim_runs_only_after_collection_and_uses_the_exact_abi(
        self,
    ) -> None:
        for result in (0, 1):
            with self.subTest(result=result):
                collected = mock.Mock(return_value=0)

                def trim_free_pages(pad: int) -> int:
                    collected.assert_called_once_with()
                    self.assertEqual(pad, 0)
                    return result

                trim = mock.Mock(side_effect=trim_free_pages)
                with (
                    mock.patch.object(run_mutation_testing.sys, "platform", "linux"),
                    mock.patch.object(run_mutation_testing.gc, "collect", collected),
                    mock.patch.object(
                        run_mutation_testing.ctypes,
                        "CDLL",
                        return_value=mock.Mock(malloc_trim=trim),
                    ) as library,
                ):
                    run_mutation_testing.release_pre_fork_memory()
                library.assert_called_once_with(None)
                self.assertEqual(trim.argtypes, [run_mutation_testing.ctypes.c_size_t])
                self.assertIs(trim.restype, run_mutation_testing.ctypes.c_int)
                trim.assert_called_once_with(0)

    def test_unsupported_native_trim_does_not_replace_the_engine_verdict(self) -> None:
        for error in (OSError("unsupported libc"), AttributeError("no malloc_trim")):
            with (
                self.subTest(error=type(error).__name__),
                mock.patch.object(run_mutation_testing.sys, "platform", "linux"),
                mock.patch.object(run_mutation_testing.gc, "collect") as collect,
                mock.patch.object(
                    run_mutation_testing.ctypes, "CDLL", side_effect=error
                ),
            ):
                run_mutation_testing.release_pre_fork_memory()
                collect.assert_called_once_with()
        with (
            mock.patch.object(run_mutation_testing.sys, "platform", "darwin"),
            mock.patch.object(run_mutation_testing.gc, "collect") as collect,
            mock.patch.object(run_mutation_testing.ctypes, "CDLL") as library,
        ):
            run_mutation_testing.release_pre_fork_memory()
            collect.assert_called_once_with()
            library.assert_not_called()

    def test_startup_exception_and_factory_arguments_are_preserved(self) -> None:
        startup = mock.Mock(side_effect=LookupError("startup failed"))
        runner = mock.Mock(startup=startup)
        factory = mock.Mock(return_value=runner)
        with mock.patch.object(
            run_mutation_testing, "release_pre_fork_memory"
        ) as release:
            created = run_mutation_testing.pre_fork_runner_factory(factory)(
                4, debug=False
            )
            factory.assert_called_once_with(4, debug=False)
            with self.assertRaisesRegex(LookupError, "startup failed"):
                created.startup("sentinel", control=True)
            release.assert_called_once_with()
            startup.assert_called_once_with("sentinel", control=True)
