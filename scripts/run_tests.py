#!/usr/bin/env python3
"""Run focused feedback or the full test and coverage gate in one invocation."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Use fresh coverage data and preserve pytest and coverage failure statuses."""
    arguments = parse_args(argv)
    tests = arguments.tests if arguments.mode == "focused" else ["tests"]
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
    covered = arguments.mode == "full" or bool(sources)
    scope = (
        "Full repository gate" if arguments.mode == "full" else "Focused feedback only"
    )
    print(scope, flush=True)
    environment = os.environ.copy()
    if arguments.mode == "full":
        environment.pop("PYTEST_ADDOPTS", None)
    try:
        if not covered:
            return subprocess.run(
                [sys.executable, *pytest], cwd=REPOSITORY_ROOT, env=environment
            ).returncode
        with tempfile.TemporaryDirectory(prefix="repo-scaffold-coverage-") as directory:
            data_file = str(Path(directory) / ".coverage")
            configuration = str(REPOSITORY_ROOT / ".coveragerc")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "run",
                    "--rcfile",
                    configuration,
                    "--data-file",
                    data_file,
                    *pytest,
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
            )
            if result.returncode:
                return result.returncode
            report = [
                sys.executable,
                "-m",
                "coverage",
                "report",
                "--rcfile",
                configuration,
                "--data-file",
                data_file,
            ]
            if sources:
                report.extend(["--include", ",".join(sources)])
            return subprocess.run(report, cwd=REPOSITORY_ROOT).returncode
    except OSError as error:
        print(f"error: could not run test checks: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
