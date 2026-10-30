"""Contracts for complete, disjoint Windows CI test shards."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.test_shards import (
    ALL_TEST_SHARD,
    NATIVE_RUNNER_TEST,
    WINDOWS_TEST_SHARDS,
    discover_pytest_test_files,
    suite_test_paths,
)


class WindowsTestShardTests(unittest.TestCase):
    @staticmethod
    def write_files(root: Path, relative_paths: tuple[str, ...]) -> None:
        for relative in relative_paths:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pass\n", encoding="utf-8")

    def test_default_pytest_patterns_are_discovered_in_sorted_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_files(
                root,
                (
                    "tests/test_zeta.py",
                    "tests/nested/alpha_test.py",
                    "tests/helper.py",
                    "tests/conftest.py",
                ),
            )

            self.assertEqual(
                discover_pytest_test_files(root),
                ("tests/nested/alpha_test.py", "tests/test_zeta.py"),
            )

    def test_windows_shards_partition_every_test_file_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_files(
                root,
                (
                    NATIVE_RUNNER_TEST,
                    "tests/test_repository_validation.py",
                    "tests/nested/other_test.py",
                    "tests/helper.py",
                ),
            )

            inventory = set(discover_pytest_test_files(root))
            native = set(suite_test_paths(root, "native-runner"))
            remainder = set(suite_test_paths(root, "remainder"))

            self.assertEqual(WINDOWS_TEST_SHARDS, ("native-runner", "remainder"))
            self.assertEqual(native, {NATIVE_RUNNER_TEST})
            self.assertFalse(native & remainder)
            self.assertEqual(native | remainder, inventory)
            self.assertIn("tests/nested/other_test.py", remainder)

    def test_all_shard_keeps_the_full_pytest_tests_root(self) -> None:
        self.assertEqual(
            suite_test_paths(Path("unused"), ALL_TEST_SHARD),
            ("tests",),
        )

    def test_unknown_shard_is_rejected_before_discovery(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown pytest test shard"):
            suite_test_paths(Path("unused"), "unreviewed")

    def test_native_runner_shard_requires_its_test_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_files(root, ("tests/test_other.py",))

            with self.assertRaisesRegex(
                ValueError, "native runner test module is missing"
            ):
                suite_test_paths(root, "native-runner")

    def test_empty_remainder_shard_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_files(root, (NATIVE_RUNNER_TEST,))

            with self.assertRaisesRegex(
                ValueError, "remainder pytest test shard is empty"
            ):
                suite_test_paths(root, "remainder")

    def test_empty_pytest_inventory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_files(root, ("tests/conftest.py",))

            with self.assertRaisesRegex(
                ValueError, "pytest test-file inventory is empty"
            ):
                discover_pytest_test_files(root)
