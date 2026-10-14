#!/usr/bin/env python3
"""Run focused feedback or the full test and coverage gate in one invocation."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.coverage_policy import coverage_policy_errors, pytest_policy_errors
from scripts.coverage_policy import COVERAGE_SOURCE_ROOTS, COVERAGE_OMITTED_COPIES
from scripts.prepare_mutation_cache import (
    _assert_safe_project_path,
    _project_files,
    project_input_fingerprint,
    _read_bounded_bytes,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
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


def repository_selection(value: str, *, source: bool) -> str:
    """Bind a literal test/source path before invoking pytest or report filters."""
    filename, separator, node = value.partition("::") if not source else (value, "", "")
    path = Path(filename)
    if ".." in path.parts or any(character in filename for character in ",*?["):
        raise ValueError(
            "selection must be a literal repository path without traversal"
        )
    absolute = path if path.is_absolute() else REPOSITORY_ROOT / path
    relative = absolute.relative_to(REPOSITORY_ROOT)
    if any(":" in part for part in relative.parts):
        raise ValueError(
            "selection cannot name colon-delimited paths or alternate data streams"
        )
    roots = COVERAGE_SOURCE_ROOTS if source else {"tests"}
    if not any(
        relative == Path(root) or Path(root) in relative.parents for root in roots
    ):
        raise ValueError(
            "selection is outside the declared repository test/source scope"
        )
    _assert_safe_project_path(REPOSITORY_ROOT, absolute)
    if source and (
        path.suffix.lower() not in {".py", ".pyw"} or not absolute.is_file()
    ):
        raise ValueError("--source requires an existing literal Python file")
    return relative.as_posix() + separator + node


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


def validate_omitted_copies(repository_root: Path) -> None:
    """Admit a present omission only when its canonical source has identical bytes."""
    for relative in sorted(COVERAGE_OMITTED_COPIES):
        copied = repository_root / relative
        _assert_safe_project_path(repository_root, copied)
        if not copied.exists():
            continue
        canonical = repository_root / "scripts" / copied.name
        contents = []
        for path in (copied, canonical):
            _assert_safe_project_path(repository_root, path)
            if not path.is_file():
                raise ValueError(
                    f"coverage omission requires a regular source/copy: {path}"
                )
            contents.append(
                _read_bounded_bytes(
                    path, MAX_FILE_BYTES, "coverage copy exceeds the source size limit"
                )
            )
        if contents[0] != contents[1]:
            raise ValueError(
                f"coverage omitted copy differs from canonical source: {relative}"
            )


def coverage_source_inventory(repository_root: Path) -> list[str]:
    """Bind both source directories and enumerate even undiscovered Python files."""
    for relative in sorted(COVERAGE_SOURCE_ROOTS):
        root = repository_root / relative
        _assert_safe_project_path(repository_root, root)
        if not root.is_dir():
            raise ValueError(f"coverage source root must be a directory: {relative}")
    omitted = {Path(copied) for copied in COVERAGE_OMITTED_COPIES}
    sources = [
        relative
        for relative, path in _project_files(repository_root, verification=True)
        if path.suffix.lower() in {".py", ".pyw"}
        and any(Path(root) in Path(relative).parents for root in COVERAGE_SOURCE_ROOTS)
        and Path(relative) not in omitted
    ]
    if not sources:
        raise ValueError("coverage source inventory contains no Python files")
    return sources


def validate_report_source_syntax(repository_root: Path, sources: list[str]) -> None:
    """Reject unparseable morfs that Coverage may silently treat as non-Python."""
    total_bytes = 0
    for relative in sources:
        path = repository_root / relative
        _assert_safe_project_path(repository_root, path)
        content = _read_bounded_bytes(
            path,
            min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total_bytes),
            "coverage source inventory exceeds the size limits",
        )
        total_bytes += len(content)
        try:
            compile(content, relative, "exec", dont_inherit=True)
        except (SyntaxError, RecursionError) as error:
            raise ValueError(
                f"coverage source cannot be compiled as Python: {relative}"
            ) from error


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
    inventory = coverage_source_inventory(REPOSITORY_ROOT)
    validate_omitted_copies(REPOSITORY_ROOT)
    identities = {Path(relative): relative for relative in inventory}
    if len(identities) != len(inventory):
        raise ValueError("coverage inventory contains ambiguous path identities")
    for copied in COVERAGE_OMITTED_COPIES:
        canonical = Path("scripts") / Path(copied).name
        if canonical in identities:
            identities[Path(copied)] = identities[canonical]
    report_sources = []
    for source in sources:
        canonical_source = identities.get(Path(source))
        if canonical_source is None:
            raise ValueError(
                f"selected coverage source is outside the admitted inventory: {source}"
            )
        if canonical_source not in report_sources:
            report_sources.append(canonical_source)
    validate_report_source_syntax(REPOSITORY_ROOT, report_sources or inventory)
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
        report = [
            *common,
            "report",
            *settings,
            *(str(Path(source)) for source in (report_sources or inventory)),
        ]
        result = subprocess.run(report, cwd=REPOSITORY_ROOT, env=environment)
        if result.returncode:
            return result.returncode
        assert_inputs_current(fingerprint, "report generation")
        return 0


def reject_cli_inspection() -> None:
    """Reject a REPL that can suppress SystemExit before creating owned outputs."""
    if sys.flags.inspect:
        try:
            print(
                "error: run test checks without Python inspection (-i/PYTHONINSPECT)",
                file=sys.stderr,
                flush=True,
            )
        finally:
            # SystemExit returns to the REPL; no runner-owned resources exist yet.
            os._exit(2)


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
    try:
        tests = [repository_selection(test, source=False) for test in tests]
        sources = [repository_selection(source, source=True) for source in sources]
    except (OSError, ValueError) as error:
        print(f"error: invalid test/source selection: {error}", file=sys.stderr)
        return 2
    pytest = ["-m", "pytest", "-q", "--durations=20", *tests]
    scope = {
        "full": "Full repository gate",
        "suite": "Pytest suite: " + ", ".join(tests),
        "focused": "Focused feedback only",
    }[arguments.mode]
    print(scope, flush=True)
    environment = os.environ.copy()
    environment.pop("PYTHONINSPECT", None)
    if arguments.mode != "focused" or sources:
        environment.pop("PYTHONOPTIMIZE", None)
        for name in list(environment):
            if name.upper().startswith("COVERAGE_"):
                environment.pop(name)
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
        runtime = (
            tempfile.TemporaryDirectory(prefix="repo-scaffold-test-runtime-")
            if fingerprint is not None
            else nullcontext()
        )
        with runtime as directory:
            if directory is not None:
                environment["PYTHONPYCACHEPREFIX"] = str(Path(directory) / "bytecode")
                environment.pop("PYTEST_DEBUG_TEMPROOT", None)
                pytest[2:2] = [
                    "--basetemp",
                    str(Path(directory) / "pytest-temp"),
                    "-o",
                    "cache_dir=" + str(Path(directory) / "pytest-cache"),
                ]
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
    reject_cli_inspection()
    raise SystemExit(main())
