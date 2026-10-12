"""Assert bounded parser work in tests while calling the actual source parser."""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import wraps
from types import FrameType
from typing import Literal, ParamSpec, TypeVar


P = ParamSpec("P")
R = TypeVar("R")


def guard_text_work(parser: Callable[P, R], *, input_name: str) -> Callable[P, R]:
    """Bound call events, not elapsed time; preserve input, result and tracing."""

    @wraps(parser)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        text = args[0] if args else kwargs[input_name]
        if not isinstance(text, str):
            raise TypeError("parser work budget requires a string input")
        remaining = 64 * len(text) + 128
        previous = sys.getprofile()
        if previous is not None and not callable(previous):
            raise RuntimeError("parser work budget requires a callable profiler")

        def count_calls(
            frame: FrameType,
            event: Literal["call", "return", "c_call", "c_return", "c_exception"],
            argument: object,
        ) -> None:
            nonlocal remaining
            if previous is not None:
                previous(frame, event, argument)
            remaining -= 1
            if remaining < 0:
                raise AssertionError("parser exceeded its linear call budget")

        try:
            sys.setprofile(count_calls)
            return parser(*args, **kwargs)
        finally:
            sys.setprofile(previous)

    return guarded
