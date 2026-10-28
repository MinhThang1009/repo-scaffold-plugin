from __future__ import annotations

import sys
import unittest
from types import FrameType
from unittest import mock

from tests.parser_work_budget import guard_text_work


class ParserWorkBudgetTests(unittest.TestCase):
    def test_native_profiler_is_rejected_without_replacing_it(self) -> None:
        def parser(text: str) -> bool:
            self.fail("parser must not run with an unsupported profiler")

        profiler = object()
        with (
            mock.patch.object(sys, "getprofile", return_value=profiler),
            mock.patch.object(sys, "setprofile") as replace,
        ):
            with self.assertRaisesRegex(RuntimeError, "callable profiler"):
                guard_text_work(parser, input_name="text")("x")
            self.assertIs(sys.getprofile(), profiler)
            replace.assert_not_called()

    def test_original_input_arguments_and_result_identity_are_preserved(self) -> None:
        text = "<!--first--><!--second-->"
        result = object()

        def parser(line: str, *, start: int) -> object:
            self.assertIs(line, text)
            self.assertEqual(start, 12)
            return result

        guarded = guard_text_work(parser, input_name="line")
        self.assertIs(guarded(line=text, start=12), result)
        self.assertIs(guarded(text, start=12), result)
        self.assertIs(getattr(guarded, "__wrapped__"), parser)

    def test_append_loop_fails_before_retaining_unbounded_output(self) -> None:
        retained: list[str] = []

        def parser(text: str) -> bool:
            while text:
                retained.append(text.strip())
            return False

        previous = sys.getprofile()
        with self.assertRaisesRegex(AssertionError, "linear call budget"):
            guard_text_work(parser, input_name="text")("x")
        self.assertIs(sys.getprofile(), previous)
        self.assertLessEqual(len(retained), 192)

    def test_find_loop_without_output_is_also_bounded(self) -> None:
        def parser(line: str) -> None:
            while line.find("<!--") >= 0:
                pass

        with self.assertRaisesRegex(AssertionError, "linear call budget"):
            guard_text_work(parser, input_name="line")("<!--escaped-->")

    def test_allowance_is_fresh_and_original_exceptions_propagate(self) -> None:
        expected = ValueError("original parser failure")

        def parser(text: str) -> bool:
            if not text:
                raise expected
            return text.strip() == "x"

        guarded = guard_text_work(parser, input_name="text")
        for _ in range(32):
            self.assertTrue(guarded("x"))
        previous = sys.getprofile()
        with self.assertRaises(ValueError) as raised:
            guarded("")
        self.assertIs(raised.exception, expected)
        self.assertIs(sys.getprofile(), previous)

    def test_existing_profile_is_forwarded_and_trace_hook_is_not_replaced(self) -> None:
        observed: list[str] = []

        def profile(frame: FrameType, event: str, argument: object) -> None:
            del frame, argument
            observed.append(event)

        def parser(text: str) -> bool:
            return text.strip() == "x"

        previous = sys.getprofile()
        trace = sys.gettrace()
        try:
            sys.setprofile(profile)
            self.assertTrue(guard_text_work(parser, input_name="text")("x"))
            self.assertIs(sys.getprofile(), profile)
            self.assertIs(sys.gettrace(), trace)
            self.assertIn("call", observed)
            self.assertIn("c_call", observed)
        finally:
            sys.setprofile(previous)


if __name__ == "__main__":
    unittest.main()
