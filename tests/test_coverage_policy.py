"""Coverage policy admission must preserve the full repository denominator."""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.coverage_policy import coverage_policy_errors, UNSAFE_COVERAGE_OPTIONS
from scripts.coverage_policy import pytest_policy_errors, PYTEST_DISCOVERY_OVERRIDES


POLICY = (Path(__file__).resolve().parents[1] / ".coveragerc").read_text(
    encoding="utf-8"
)


class CoveragePolicyTests(unittest.TestCase):
    def test_coverage_lists_accept_comma_and_lf_delimiters(self) -> None:
        source_paths = "scripts\n    skills/repo-scaffold/scripts"
        copy_paths = (
            "skills/repo-scaffold/scripts/audit_freshness.py\n    "
            "skills/repo-scaffold/scripts/render_pr_body_evidence.py\n    "
            "skills/repo-scaffold/scripts/sync_action_pins.py"
        )
        for paths in (source_paths, copy_paths):
            for separator in (",", ", ", ",,\n    "):
                with self.subTest(paths=paths, separator=separator):
                    text = POLICY.replace(paths, paths.replace("\n    ", separator))
                    self.assertEqual(coverage_policy_errors(text), [])

    def test_coverage_lists_do_not_split_unicode_line_separators(self) -> None:
        for paths in (
            "scripts\n    skills/repo-scaffold/scripts",
            "skills/repo-scaffold/scripts/audit_freshness.py\n    "
            "skills/repo-scaffold/scripts/render_pr_body_evidence.py\n    "
            "skills/repo-scaffold/scripts/sync_action_pins.py",
        ):
            for separator in ("\u2028", "\u2029", "\u0085", "\v", "\f"):
                with self.subTest(paths=paths, separator=repr(separator)):
                    text = POLICY.replace(paths, paths.replace("\n    ", separator))
                    self.assertTrue(coverage_policy_errors(text))

    def test_comma_list_members_starting_with_hash_are_not_comments(self) -> None:
        for paths in (
            "scripts\n    skills/repo-scaffold/scripts",
            "skills/repo-scaffold/scripts/audit_freshness.py\n    "
            "skills/repo-scaffold/scripts/render_pr_body_evidence.py\n    "
            "skills/repo-scaffold/scripts/sync_action_pins.py",
        ):
            with self.subTest(paths=paths):
                text = POLICY.replace(
                    paths, paths.replace("\n    ", ", ") + ", #extra-path"
                )
                self.assertTrue(coverage_policy_errors(text))

    def test_pytest_policy_parser_limit_is_a_controlled_admission_error(self) -> None:
        canonical = '[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["."]\n'
        self.assertEqual(
            pytest_policy_errors("metadata = [[{x=[0]}]]\n" + canonical), []
        )
        for nested in ("[" * 2000 + "0" + "]" * 2000, "{x=" * 2000 + "0" + "}" * 2000):
            with self.subTest(shape=nested[:3]):
                text = "metadata = " + nested + "\n" + canonical
                self.assertLess(len(text.encode("utf-8")), 65536)
                self.assertEqual(
                    pytest_policy_errors(text),
                    ["pyproject.toml: pytest policy nesting exceeds parser limit"],
                )

    def test_prefixed_sections_cannot_shadow_the_admitted_coverage_policy(self) -> None:
        for section, settings in (
            ("run", "branch = false\ndebug_file = outside.log\n"),
            ("report", "fail_under = 0\ninclude = scripts/decision.py\n"),
            ("paths", "alias = scripts\n"),
            ("html", "directory = outside\n"),
        ):
            for text in (
                POLICY + f"\n[coverage:{section}]\n{settings}",
                POLICY.replace(f"[{section}]", f"[coverage:{section}]")
                if section in {"run", "report"}
                else POLICY + f"\n[coverage:{section}]\n",
            ):
                with self.subTest(section=section, text=text):
                    errors = coverage_policy_errors(text)
                    self.assertTrue(
                        any("prefixed sections" in error for error in errors)
                    )

    def test_configuration_byte_limits_apply_before_parsing(self) -> None:
        for checker in (coverage_policy_errors, pytest_policy_errors):
            with self.subTest(checker=checker.__name__):
                self.assertIn("65536-byte", checker("x" * 65537)[0])

    def test_pytest_policy_preserves_canonical_discovery_and_rejects_alternate_tables(
        self,
    ) -> None:
        canonical = (
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\n'
        )
        self.assertEqual(pytest_policy_errors(canonical), [])
        for text in (
            "invalid",
            "",
            "tool = 1",
            "[tool.pytest]\npython_files = ['test_good.py']",
            "[tool.pytest]\nini_options = 1",
            canonical.replace('["tests"]', '["other"]'),
            canonical.replace('["."]', '[".."]'),
        ):
            with self.subTest(text=text):
                self.assertTrue(pytest_policy_errors(text))
        for key in PYTEST_DISCOVERY_OVERRIDES:
            with self.subTest(key=key):
                self.assertTrue(
                    pytest_policy_errors(canonical + f"{key} = ['test_good.py']\n")
                )

    def test_canonical_policy_and_safe_explicit_error_setting_are_accepted(
        self,
    ) -> None:
        self.assertEqual(coverage_policy_errors(POLICY), [])
        self.assertEqual(
            coverage_policy_errors(
                POLICY.replace("[report]", "[report]\nignore_errors = false")
            ),
            [],
        )
        self.assertEqual(coverage_policy_errors(POLICY.replace("/", "\\")), [])

    def test_incomplete_invalid_or_nonfinite_policy_is_rejected(self) -> None:
        for text in (
            "invalid",
            POLICY.replace("branch = True", "branch = False"),
            POLICY.replace("source =", "source =\n other"),
            POLICY.replace("fail_under = 100", "fail_under = 99"),
            POLICY.replace("fail_under = 100", "fail_under = nan"),
            POLICY.replace("fail_under = 100", "fail_under = inf"),
            POLICY.replace("fail_under = 100", "fail_under = 101"),
            POLICY.replace("fail_under = 100", "fail_under = invalid"),
            POLICY.replace(
                "include_namespace_packages = True",
                "include_namespace_packages = False",
            ),
            POLICY.replace(
                "include_namespace_packages = True",
                "include_namespace_packages = invalid",
            ),
            POLICY.replace("include_namespace_packages = True", ""),
            POLICY + "\n[paths]\nalias = scripts\n",
        ):
            with self.subTest(text=text):
                self.assertTrue(coverage_policy_errors(text))

    def test_measurement_and_report_overrides_cannot_hide_missing_coverage(
        self,
    ) -> None:
        for section, options in UNSAFE_COVERAGE_OPTIONS.items():
            for option in options:
                with self.subTest(section=section, option=option):
                    errors = coverage_policy_errors(
                        POLICY.replace(
                            f"[{section}]",
                            f"[{section}]\n{option} = scripts/run_tests.py",
                        )
                    )
                    self.assertTrue(any("denominator" in error for error in errors))
        for value in ("true", "invalid"):
            with self.subTest(value=value):
                self.assertTrue(
                    coverage_policy_errors(
                        POLICY.replace("[report]", f"[report]\nignore_errors = {value}")
                    )
                )

    def test_guarded_configs_cannot_redirect_coverage_or_pytest_outputs(self) -> None:
        for value in ("outside.log", ""):
            self.assertTrue(
                coverage_policy_errors(
                    POLICY.replace("[run]", f"[run]\ndebug_file = {value}")
                )
            )
        canonical = (
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\n'
        )
        for option in ("log_file", "cache_dir"):
            for value in ("outside", ""):
                with self.subTest(option=option, value=value):
                    errors = pytest_policy_errors(canonical + f'{option} = "{value}"\n')
                    self.assertTrue(
                        any(
                            "redirect guarded pytest output" in error
                            for error in errors
                        )
                    )
