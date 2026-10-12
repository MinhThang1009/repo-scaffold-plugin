"""Check the Linux fixture's fault selection without loading a live backend."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from tests import test_mutation_runner_linux as linux_fixtures


class MutationFixtureSelectionTests(unittest.TestCase):
    def test_text_fixture_selects_all_eight_nonadvancing_fault_shapes(self) -> None:
        case = linux_fixtures.LinuxMutationRunnerIntegrationTests(
            "test_text_work_budget_kills_real_nonadvancing_comment_and_line_mutants"
        )
        faults = (
            ("html_comment_start", "return line.find('<!--', None)"),
            ("html_comment_start", "return line.find('<!--')"),
            ("strip_html_comments", "return line.find('-->', None)"),
            ("strip_html_comments", "return line.find('-->')"),
            ("freshness_issue_lookup_substitution_is_safe", "cursor -= 1"),
            ("freshness_issue_lookup_substitution_is_safe", "cursor = 1"),
            ("freshness_issue_lookup_substitution_is_safe", "index = cursor - 1"),
            ("strip_html_comments", "cursor = next_comment_start - 4"),
        )

        def inert_backend_boundary(root: Path, *arguments: str) -> None:
            self.assertEqual(arguments[-2:], ("--max-children", "1"))
            directory = root / "mutants/scripts"
            directory.mkdir(parents=True)
            definitions = []
            codes = {}
            for ordinal, (name, body) in enumerate(faults, 1):
                mutant = f"x_{name}__mutmut_{ordinal}"
                definitions.append(f"def {mutant}(line):\n    {body}\n")
                codes[f"scripts.decision.{mutant}"] = 1
            (directory / "decision.py").write_text(
                "\n".join(definitions), encoding="utf-8"
            )
            (directory / "decision.py.meta").write_text(
                json.dumps({"exit_code_by_key": codes}), encoding="utf-8"
            )

        with (
            mock.patch.object(case, "source_definitions", return_value=""),
            mock.patch.object(
                case, "run_command", side_effect=inert_backend_boundary
            ) as backend,
        ):
            case.test_text_work_budget_kills_real_nonadvancing_comment_and_line_mutants()
        backend.assert_called_once()


if __name__ == "__main__":
    unittest.main()
