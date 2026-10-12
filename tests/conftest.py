"""Release unreachable test allocations before an in-process runner freezes GC."""

from __future__ import annotations

import gc


def pytest_sessionfinish(session: object, exitstatus: int) -> None:
    """Collect temporary module cycles without changing the session verdict."""
    del session, exitstatus
    gc.collect()
