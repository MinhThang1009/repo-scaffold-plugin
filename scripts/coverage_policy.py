"""Validate the repository coverage denominator before executing its gate."""

from __future__ import annotations

import configparser
import importlib
import sys

tomllib = importlib.import_module("tomllib" if sys.version_info >= (3, 11) else "tomli")


COVERAGE_SOURCE_ROOTS = {"scripts", "skills/repo-scaffold/scripts"}
MAX_TEST_CONFIGURATION_BYTES = 64 * 1024
COVERAGE_OMITTED_COPIES = {
    "skills/repo-scaffold/scripts/audit_freshness.py",
    "skills/repo-scaffold/scripts/render_pr_body_evidence.py",
    "skills/repo-scaffold/scripts/sync_action_pins.py",
}
UNSAFE_COVERAGE_OPTIONS = {
    "run": {"include", "source_pkgs", "source_dirs", "plugins"},
    "report": {
        "include",
        "omit",
        "exclude_lines",
        "exclude_also",
        "partial_branches",
        "partial_branches_always",
        "partial_also",
        "contexts",
    },
}


PYTEST_DISCOVERY_OVERRIDES = {
    "python_files",
    "python_classes",
    "python_functions",
    "norecursedirs",
    "collect_imported_tests",
    "consider_namespace_packages",
}
PYTEST_OUTPUT_OVERRIDES = {"log_file", "cache_dir"}


def pytest_policy_errors(text: str) -> list[str]:
    """Bind full verification to the canonical first-party discovery contract."""
    if len(text.encode("utf-8")) > MAX_TEST_CONFIGURATION_BYTES:
        return ["pyproject.toml: configuration exceeds the 65536-byte limit"]
    try:
        document = tomllib.loads(text)
    except ValueError as error:
        return [f"pyproject.toml: could not verify pytest policy: {error}"]
    tool = document.get("tool")
    pytest = tool.get("pytest") if isinstance(tool, dict) else None
    if not isinstance(pytest, dict) or set(pytest) != {"ini_options"}:
        return [
            "pyproject.toml: full checks require the canonical pytest ini_options table"
        ]
    options = pytest["ini_options"]
    if (
        not isinstance(options, dict)
        or options.get("testpaths") != ["tests"]
        or options.get("pythonpath") != ["."]
    ):
        return [
            "pyproject.toml: full checks require testpaths=['tests'] and pythonpath=['.']"
        ]
    problems = [
        f"pyproject.toml: {key} cannot replace canonical test discovery"
        for key in sorted(PYTEST_DISCOVERY_OVERRIDES & options.keys())
    ]
    problems.extend(
        f"pyproject.toml: {key} cannot redirect guarded pytest output"
        for key in sorted(PYTEST_OUTPUT_OVERRIDES & options.keys())
    )
    return problems


def coverage_policy_errors(text: str) -> list[str]:
    """Require both source trees, the exact floor and no hidden report exclusions."""
    if len(text.encode("utf-8")) > MAX_TEST_CONFIGURATION_BYTES:
        return [".coveragerc: configuration exceeds the 65536-byte limit"]
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read_string(text)
        # Coverage.py prefers coverage:-prefixed sections, even in .coveragerc.
        # Admit one dialect so its effective policy matches this validation.
        if any(section.startswith("coverage:") for section in config.sections()):
            return [
                ".coveragerc: coverage:-prefixed sections are not allowed; "
                "use canonical unprefixed sections"
            ]
        branch = config.getboolean("run", "branch")
        sources = {
            line.strip().replace("\\", "/")
            for line in config.get("run", "source").splitlines()
            if line.strip()
        }
        omitted = {
            line.strip().replace("\\", "/")
            for line in config.get("run", "omit").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        floor = config.getfloat("report", "fail_under")
        namespace_packages = config.getboolean("report", "include_namespace_packages")
    except (configparser.Error, ValueError) as error:
        return [f".coveragerc: could not verify coverage policy: {error}"]
    problems: list[str] = []
    if (
        not branch
        or sources != COVERAGE_SOURCE_ROOTS
        or omitted != COVERAGE_OMITTED_COPIES
        or floor != 100
    ):
        problems.append(
            ".coveragerc: require branch coverage for both script trees, only "
            "verified byte-for-byte script copies omitted, and a fail-under "
            "floor of at least 100"
        )
    if not namespace_packages:
        problems.append(
            ".coveragerc: unimported namespace packages must remain in the denominator"
        )
    if config.has_section("paths"):
        problems.append(
            ".coveragerc: path aliases cannot merge the repository coverage denominator"
        )
    if config.has_option("run", "debug_file"):
        problems.append(
            ".coveragerc: debug_file cannot redirect guarded coverage output"
        )
    for section, options in UNSAFE_COVERAGE_OPTIONS.items():
        for option in sorted(options):
            if config.has_option(section, option):
                problems.append(
                    f".coveragerc: [{section}] {option} cannot narrow or replace "
                    "the repository coverage denominator"
                )
    if config.has_option("report", "ignore_errors"):
        try:
            ignore_errors = config.getboolean("report", "ignore_errors")
        except ValueError as error:
            problems.append(f".coveragerc: invalid ignore_errors: {error}")
        else:
            if ignore_errors:
                problems.append(".coveragerc: report errors must not be ignored")
    return problems
