"""Bound token-parser work in tests without replacing parser decisions."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Concatenate, ParamSpec, SupportsIndex, overload


P = ParamSpec("P")


class TokenReadBudget(list[str]):
    """Share a linear read allowance with every slice of one input sequence."""

    def __init__(self, values: list[str], remaining: list[int] | None = None) -> None:
        super().__init__(values)
        self._remaining = (
            remaining if remaining is not None else [4 * super().__len__() + 8]
        )

    @overload
    def __getitem__(self, index: SupportsIndex) -> str: ...

    @overload
    def __getitem__(self, index: slice) -> TokenReadBudget: ...

    def __getitem__(self, index: SupportsIndex | slice) -> str | TokenReadBudget:
        self._remaining[0] -= 1
        if self._remaining[0] < 0:
            raise AssertionError("token walk exceeded its linear read budget")
        if isinstance(index, slice):
            return TokenReadBudget(super().__getitem__(index), self._remaining)
        return super().__getitem__(index)


def guard_token_reads(
    parser: Callable[Concatenate[list[str], P], bool],
) -> Callable[Concatenate[list[str], P], bool]:
    """Call the original parser and fail tests if token traversal stops advancing."""

    @wraps(parser)
    def guarded(tokens: list[str], *args: P.args, **kwargs: P.kwargs) -> bool:
        return parser(TokenReadBudget(tokens), *args, **kwargs)

    return guarded
