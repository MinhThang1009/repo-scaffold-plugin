"""Select deterministic, complete pytest file shards for Windows CI."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

ALL_TEST_SHARD = "all"
WINDOWS_TEST_SHARDS = ("native-runner", "remainder")
NATIVE_RUNNER_TEST = "tests/test_run_tests_native.py"


def _is_pytest_test_file(name: str) -> bool:
    """Match pytest's default Python test-file patterns."""
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith(
        "_test.py"
    )


def discover_pytest_test_files(repository_root: Path) -> tuple[str, ...]:
    """Return every safe, first-party pytest test module in sorted order."""
    from scripts.prepare_mutation_cache import _project_files

    files = tuple(
        relative
        for relative, _path in _project_files(repository_root, verification=True)
        if PurePosixPath(relative).parts[0] == "tests"
        and _is_pytest_test_file(PurePosixPath(relative).name)
    )
    if not files:
        raise ValueError("pytest test-file inventory is empty")
    return files


def suite_test_paths(repository_root: Path, shard: str) -> tuple[str, ...]:
    """Return one complete suite selection; Windows shards are disjoint."""
    if shard == ALL_TEST_SHARD:
        return ("tests",)
    if shard not in WINDOWS_TEST_SHARDS:
        raise ValueError(f"unknown pytest test shard: {shard}")

    files = discover_pytest_test_files(repository_root)
    if NATIVE_RUNNER_TEST not in files:
        raise ValueError(
            f"required native runner test module is missing: {NATIVE_RUNNER_TEST}"
        )
    if shard == "native-runner":
        return (NATIVE_RUNNER_TEST,)

    remainder = tuple(path for path in files if path != NATIVE_RUNNER_TEST)
    if not remainder:
        raise ValueError("remainder pytest test shard is empty")
    return remainder
