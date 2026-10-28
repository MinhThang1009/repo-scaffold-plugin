from __future__ import annotations

import unittest

from tests.token_walk_budget import TokenReadBudget, guard_token_reads


class TokenWalkBudgetTests(unittest.TestCase):
    def test_values_offsets_and_original_decisions_are_preserved(self) -> None:
        calls = []

        def parser(tokens: list[str], position: int) -> bool:
            calls.append((list(tokens), position))
            return tokens[position:] == ["gh", "api", "--method", "HEAD"]

        guarded = guard_token_reads(parser)
        tokens = ["prefix", "gh", "api", "--method", "HEAD"]
        self.assertTrue(guarded(tokens, 1))
        self.assertFalse(guarded(tokens, 0))
        self.assertEqual(calls, [(tokens, 1), (tokens, 0)])
        self.assertEqual(tokens, ["prefix", "gh", "api", "--method", "HEAD"])

    def test_slices_share_the_same_allowance_instead_of_resetting_it(self) -> None:
        tokens = TokenReadBudget(["GET"], [2])
        sliced = tokens[:]
        self.assertEqual(sliced[0], "GET")
        with self.assertRaisesRegex(AssertionError, "linear read budget"):
            tokens[0]

    def test_index_reset_fault_fails_before_the_method_list_can_grow_unbounded(
        self,
    ) -> None:
        methods = []

        def index_reset(tokens: list[str], position: int) -> bool:
            sliced = tokens[position:]
            index = 2
            while index < len(sliced):
                token = sliced[index]
                if token == "-X":
                    methods.append(sliced[index + 1])
                    index = 1
                index += 1
            return bool(methods)

        with self.assertRaisesRegex(AssertionError, "linear read budget"):
            guard_token_reads(index_reset)(["gh", "api", "endpoint", "-X", "HEAD"], 0)
        self.assertLessEqual(len(methods), 28)

    def test_allowance_is_fresh_for_each_call_and_parser_exceptions_propagate(
        self,
    ) -> None:
        def parser(tokens: list[str]) -> bool:
            return tokens[0] == "GET"

        guarded = guard_token_reads(parser)
        for _ in range(32):
            self.assertTrue(guarded(["GET"]))
        with self.assertRaises(IndexError):
            guarded([])


if __name__ == "__main__":
    unittest.main()
