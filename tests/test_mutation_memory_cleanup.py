from __future__ import annotations

import gc
import runpy
import unittest
import weakref
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HOOK = runpy.run_path(str(ROOT / "tests/conftest.py"))["pytest_sessionfinish"]


class MutationMemoryCleanupTests(unittest.TestCase):
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
