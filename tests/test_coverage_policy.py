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
