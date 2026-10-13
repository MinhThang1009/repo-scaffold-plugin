#!/usr/bin/env python3
"""Run focused feedback or the full test and coverage gate in one invocation."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.coverage_policy import coverage_policy_errors, pytest_policy_errors
from scripts.prepare_mutation_cache import (
    _assert_safe_project_path,
    project_input_fingerprint,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Keep focused selection explicit and the full suite unconditional."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    focused = modes.add_parser("focused", help="Run explicit pytest files or node IDs")
    focused.add_argument("tests", nargs="+", help="Test files or pytest node IDs")
    focused.add_argument(
        "--source",
        action="append",
        default=[],
        help="Python file to report coverage for",
    )
    modes.add_parser(
        "full", help="Run all tests once with the repository coverage floor"
    )
    suite = modes.add_parser(
        "suite", help="Run pytest without coverage for runtime checks"
    )
    suite.add_argument(
        "tests",
        nargs="*",
        default=["tests"],
        help="Explicit test paths, or all tests by default",
    )
    return parser.parse_args(argv)


def assert_inputs_current(fingerprint: str | None, phase: str) -> None:
    """Reject a result if inventoried source, test or metadata bytes changed."""
    if (
        fingerprint is not None
        and project_input_fingerprint(REPOSITORY_ROOT) != fingerprint
    ):
        raise ValueError(f"test inputs changed during {phase}; result is not current")


def read_configuration(name: str) -> str:
    """Read only a bounded regular config within the selected repository."""
    path = REPOSITORY_ROOT / name
    _assert_safe_project_path(REPOSITORY_ROOT, path)
    if not path.is_file():
        raise ValueError(f"{name}: configuration must be a regular repository file")
    with path.open("rb") as stream:
        payload = stream.read(64 * 1024 + 1)
    if len(payload) > 64 * 1024:
        raise ValueError("configuration exceeds the 65536-byte limit")
    return payload.decode("utf-8")


def run_coverage(
    pytest: list[str],
    sources: list[str],
    environment: dict[str, str],
    fingerprint: str | None,
) -> int:
    """Measure with a frozen policy and unique data, then verify the final inputs."""
    configuration_text = read_configuration(".coveragerc")
    policy_errors = coverage_policy_errors(configuration_text)
    if policy_errors:
        raise ValueError("; ".join(policy_errors))
    with tempfile.TemporaryDirectory(prefix="repo-scaffold-coverage-") as directory:
        data_file = str(Path(directory) / ".coverage")
        frozen_configuration = Path(directory) / ".coveragerc"
        frozen_configuration.write_text(configuration_text, encoding="utf-8")
        common = [
            sys.executable,
            "-m",
            "coverage",
        ]
        settings = ["--rcfile", str(frozen_configuration), "--data-file", data_file]
        result = subprocess.run(
            [*common, "run", *settings, *pytest],
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        if result.returncode:
            return result.returncode
        assert_inputs_current(fingerprint, "the run")
        report = [*common, "report", *settings]
        if sources:
            report.extend(["--include", ",".join(sources)])
        result = subprocess.run(report, cwd=REPOSITORY_ROOT, env=environment)
        if result.returncode:
            return result.returncode
        assert_inputs_current(fingerprint, "report generation")
        return 0


def main(argv: list[str] | None = None) -> int:
    """Use fresh coverage data and preserve pytest and coverage failure statuses."""
    arguments = parse_args(argv)
    tests = arguments.tests if arguments.mode in {"focused", "suite"} else ["tests"]
    if any(test.startswith("-") for test in tests):
        print(
            "error: supply test paths or node IDs, not pytest options", file=sys.stderr
        )
        return 2
    sources = arguments.source if arguments.mode == "focused" else []
    for source in sources:
        path = Path(source)
        if (
            path.suffix != ".py"
            or not (REPOSITORY_ROOT / path).is_file()
            or any(character in source for character in ",*?[")
        ):
            print(
                "error: --source requires an existing literal Python file",
                file=sys.stderr,
            )
            return 2
    pytest = ["-m", "pytest", "-q", "--durations=20", *tests]
    scope = {
        "full": "Full repository gate",
        "suite": "Pytest suite: " + ", ".join(tests),
        "focused": "Focused feedback only",
    }[arguments.mode]
    print(scope, flush=True)
    environment = os.environ.copy()
    if arguments.mode != "focused":
        for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            environment.pop(name, None)
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        pytest[2:2] = ["-c", str(REPOSITORY_ROOT / "pyproject.toml"), "-o", "addopts="]
    try:
        fingerprint = (
            project_input_fingerprint(REPOSITORY_ROOT)
            if arguments.mode != "focused" or sources
            else None
        )
        if arguments.mode != "focused":
            pytest_errors = pytest_policy_errors(read_configuration("pyproject.toml"))
            if pytest_errors:
                raise ValueError("; ".join(pytest_errors))
        if arguments.mode == "full" or sources:
            return run_coverage(pytest, sources, environment, fingerprint)
        result = subprocess.run(
            [sys.executable, *pytest],
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        if result.returncode:
            return result.returncode
        assert_inputs_current(fingerprint, "the run")
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: could not run test checks: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
