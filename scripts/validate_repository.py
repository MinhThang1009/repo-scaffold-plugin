#!/usr/bin/env python3
"""Validate repository metadata, links, templates, attestations, and releases."""

from __future__ import annotations

import ast
import configparser
import importlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt
from markdown_it.rules_inline.backticks import backtick as parse_backtick
from markdown_it.rules_inline.state_inline import StateInline
from markdown_it.token import Token

tomllib = importlib.import_module("tomllib" if sys.version_info >= (3, 11) else "tomli")


CACHE_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "mutants",
    "venv",
    ".venv",
}
COVERAGE_FAIL_UNDER = 100
COMMONMARK = MarkdownIt("commonmark")
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
RELEASE_PLEASE_ENGLISH_TEXT = {
    "pull-request-title-pattern": "chore${scope}: release${component} ${version}",
    "pull-request-header": ":robot: I have created a release *beep* *boop*",
    "pull-request-footer": (
        "This PR was generated with [Release Please]"
        "(https://github.com/googleapis/release-please). See [documentation]"
        "(https://github.com/googleapis/release-please#release-please)."
    ),
}
RELEASE_PLEASE_ENGLISH_CHANGELOG_SECTIONS = [
    {"type": "feat", "section": "Features"},
    {"type": "feature", "section": "Features"},
    {"type": "fix", "section": "Bug Fixes"},
    {"type": "perf", "section": "Performance Improvements"},
    {"type": "revert", "section": "Reverts"},
    {"type": "docs", "section": "Documentation", "hidden": True},
    {"type": "style", "section": "Styles", "hidden": True},
    {"type": "chore", "section": "Miscellaneous Chores", "hidden": True},
    {"type": "refactor", "section": "Code Refactoring", "hidden": True},
    {"type": "test", "section": "Tests", "hidden": True},
    {"type": "build", "section": "Build System", "hidden": True},
    {"type": "ci", "section": "Continuous Integration", "hidden": True},
]
TEMPLATE_TOKEN = re.compile(r"(?:\{\{|\$\{\{)")
ISSUE_FORM_ID = re.compile(r"^[0-9A-Za-z_-]+$")
ISSUE_FORM_INPUT_TYPES = {
    "checkboxes",
    "dropdown",
    "input",
    "markdown",
    "textarea",
    "upload",
}
ATTESTATION_VALIDATION_SCRIPT = """\
set -euo pipefail
if [[ ! -d dist ]] || ! find dist -type f -print -quit | grep -q .; then
  echo "::error::No release artifacts were downloaded under dist/."
  exit 1
fi
if find dist -type l -print -quit | grep -q .; then
  echo "::error::Downloaded release artifacts must not contain symbolic links."
  exit 1
fi
if find dist ! -type d ! -type f -print -quit | grep -q .; then
  echo "::error::Downloaded release artifacts must contain only directories and regular files."
  exit 1
fi
"""


class UniqueKeyBaseLoader(yaml.BaseLoader):
    """YAML loader that preserves scalar text and rejects duplicate keys."""

    def construct_mapping(
        self, node: yaml.nodes.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable mapping key",
                    key_node.start_mark,
                ) from error
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def resolve_path_executable(name: str, *, forbidden_root: Path) -> str | None:
    """Resolve a tool only from absolute PATH entries outside the repository."""
    forbidden = forbidden_root.resolve(strict=True)
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_directory:
            continue
        directory = Path(raw_directory.strip('"'))
        if not directory.is_absolute():
            continue
        candidate = shutil.which(name, path=str(directory))
        if candidate is None:
            continue
        try:
            resolved = Path(candidate).resolve(strict=True)
            resolved.relative_to(forbidden)
        except ValueError:
            return str(resolved)
        except (OSError, RuntimeError):
            continue
    return None


def is_project_path(path: Path, repository_root: Path) -> bool:
    """Return whether a path belongs to source rather than a cache or artifact."""
    relative = path.relative_to(repository_root)
    return not any(part in CACHE_DIRECTORIES for part in relative.parts)


def is_link_or_reparse(path: Path) -> bool:
    """Return whether an existing path is a symlink or Windows reparse point."""
    if path.is_symlink():
        return True
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def project_files(repository_root: Path, patterns: Iterable[str]) -> list[Path]:
    """Return matching first-party files, excluding known local artifacts."""
    repository_root = Path(os.path.abspath(repository_root))
    requested_patterns = tuple(patterns)
    files: set[Path] = set()
    for directory, child_directories, filenames in os.walk(
        repository_root, topdown=True, followlinks=False
    ):
        current = Path(directory)
        child_directories[:] = sorted(
            name
            for name in child_directories
            if name not in CACHE_DIRECTORIES and not is_link_or_reparse(current / name)
        )
        for name in sorted(filenames):
            path = current / name
            if is_link_or_reparse(path) or not path.is_file():
                continue
            if any(path.match(pattern) for pattern in requested_patterns):
                files.add(path)
    return sorted(files)


def load_yaml(path: Path) -> Any:
    """Load a YAML document without YAML 1.1 scalar coercion."""
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyBaseLoader)


def reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate member names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    """Load a JSON document and reject duplicate member names."""
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_json_pairs,
    )


def nonempty_string(value: Any) -> bool:
    """Return whether a value is a nonempty string."""
    return isinstance(value, str) and bool(value.strip())


def child_process_environment() -> dict[str, str]:
    """Keep mutmut's in-process selector out of child Python processes."""
    environment = os.environ.copy()
    environment.pop("MUTANT_UNDER_TEST", None)
    return environment


def validate_serialized_files(repository_root: Path) -> list[str]:
    """Validate every first-party JSON and YAML document."""
    problems: list[str] = []
    for path in project_files(repository_root, ("*.json",)):
        try:
            load_json(path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            problems.append(
                f"{path.relative_to(repository_root)}: invalid JSON: {error}"
            )
    for path in project_files(repository_root, ("*.yml", "*.yaml")):
        try:
            load_yaml(path)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            problems.append(
                f"{path.relative_to(repository_root)}: invalid YAML: {error}"
            )
    return problems


def validate_python_support_contract(repository_root: Path) -> list[str]:
    """Validate the centralized Python policy and every repository consumer."""
    script = repository_root / "scripts" / "python_support.py"
    policy_path = repository_root / ".github" / "python-support.json"
    if not script.is_file():
        return ["Python support contract: scripts/python_support.py is missing"]
    if not policy_path.is_file():
        return ["Python support contract: .github/python-support.json is missing"]

    command = [
        sys.executable,
        str(script),
        "--policy",
        str(policy_path),
        "validate",
    ]
    try:
        result = subprocess.run(  # noqa: S603 - interpreter and script are explicit
            command,
            cwd=repository_root,
            env=child_process_environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return ["Python support contract: policy validation timed out"]
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "validation failed"
        return [f"Python support contract: {line}" for line in detail.splitlines()]

    problems: list[str] = []
    policy_reference = ".github/python-support.json"
    for relative in ("README.md", "CONTRIBUTING.md", "requirements-dev.txt"):
        path = repository_root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            problems.append(f"{relative}: could not verify Python policy link: {error}")
            continue
        if policy_reference not in text:
            problems.append(
                f"{relative}: must reference the centralized Python support policy"
            )
        if re.search(
            r"(?i)\b(?:CPython|Python)\s+3\.\d+"
            r"(?:\s*(?:through|-)\s*3\.\d+|\s+or newer)",
            text,
        ):
            problems.append(
                f"{relative}: must not duplicate the supported Python version range"
            )

    workflow_path = repository_root / ".github" / "workflows" / "ci.yml"
    asset_path = (
        repository_root / "skills" / "repo-scaffold" / "assets" / "workflows" / "ci.yml"
    )
    skill_path = repository_root / "skills" / "repo-scaffold" / "SKILL.md"
    try:
        workflow_text = workflow_path.read_text(encoding="utf-8")
        workflow = load_yaml(workflow_path)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        problems.append(f".github/workflows/ci.yml: could not verify contract: {error}")
        workflow_text = ""
        workflow = None

    if re.search(r"(?m)^\s*python-version:\s*['\"]?3\.\d+", workflow_text):
        problems.append(
            ".github/workflows/ci.yml: supported Python feature releases must "
            "not be hardcoded"
        )
    jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
    if not isinstance(jobs, dict):
        problems.append(".github/workflows/ci.yml: jobs must be a mapping")
    else:
        prepare = jobs.get("prepare_ci")
        test = jobs.get("test")
        quality = jobs.get("quality")
        mutation_integration = jobs.get("mutation-cache-integration")
        canary = jobs.get("python-latest-canary")
        ci_success = jobs.get("ci-success")
        expected_prepare_outputs = {
            "matrix": "${{ steps.support.outputs.matrix }}",
            "latest": "${{ steps.support.outputs.latest }}",
        }
        prepare_outputs = prepare.get("outputs") if isinstance(prepare, dict) else None
        if not isinstance(prepare_outputs, dict) or any(
            prepare_outputs.get(name) != value
            for name, value in expected_prepare_outputs.items()
        ):
            problems.append(
                ".github/workflows/ci.yml: prepare_ci must expose policy "
                "matrix and latest outputs"
            )
        prepare_steps = prepare.get("steps") if isinstance(prepare, dict) else None
        if not isinstance(prepare_steps, list) or not any(
            isinstance(step, dict)
            and step.get("run")
            == 'python scripts/python_support.py emit-github-output >> "$GITHUB_OUTPUT"'
            for step in prepare_steps
        ):
            problems.append(
                ".github/workflows/ci.yml: prepare_ci must load the centralized policy"
            )
        expected_matrix = "${{ fromJSON(needs.prepare_ci.outputs.matrix) }}"
        if (
            not isinstance(test, dict)
            or test.get("needs") != "prepare_ci"
            or test.get("runs-on") != "${{ matrix.os }}"
            or not isinstance(test.get("strategy"), dict)
            or test["strategy"].get("matrix") != expected_matrix
        ):
            problems.append(
                ".github/workflows/ci.yml: test matrix must come from prepare_ci "
                "and runs-on must use matrix.os"
            )
        quality_steps = quality.get("steps") if isinstance(quality, dict) else None
        if (
            not isinstance(quality, dict)
            or quality.get("needs") != "prepare_ci"
            or not isinstance(quality_steps, list)
            or not any(
                isinstance(step, dict)
                and isinstance(step.get("with"), dict)
                and step["with"].get("python-version")
                == "${{ needs.prepare_ci.outputs.latest }}"
                for step in quality_steps
            )
        ):
            problems.append(
                ".github/workflows/ci.yml: quality must use the policy's latest release"
            )
        mutation_integration_steps = (
            mutation_integration.get("steps")
            if isinstance(mutation_integration, dict)
            else None
        )
        expected_mutation_install = (
            "python -m pip install --disable-pip-version-check --require-hashes "
            "--requirement requirements-mutation.lock"
        )
        mutation_integration_setup = isinstance(
            mutation_integration_steps, list
        ) and any(
            isinstance(step, dict)
            and isinstance(step.get("with"), dict)
            and step["with"].get("python-version")
            == "${{ needs.prepare_ci.outputs.latest }}"
            and step["with"].get("cache-dependency-path")
            == "requirements-mutation.lock"
            for step in mutation_integration_steps
        )
        mutation_integration_install = isinstance(
            mutation_integration_steps, list
        ) and any(
            isinstance(step, dict) and step.get("run") == expected_mutation_install
            for step in mutation_integration_steps
        )
        mutation_integration_run = isinstance(mutation_integration_steps, list) and any(
            isinstance(step, dict)
            and step.get("env") == {"REPO_SCAFFOLD_MUTMUT_INTEGRATION": "1"}
            and step.get("run")
            == "python -m pytest -q tests/test_mutation_runner_linux.py"
            for step in mutation_integration_steps
        )
        if (
            not isinstance(mutation_integration, dict)
            or mutation_integration.get("needs") != "prepare_ci"
            or not mutation_integration_setup
            or not mutation_integration_install
            or not mutation_integration_run
        ):
            problems.append(
                ".github/workflows/ci.yml: mutation cache integration must run "
                "the real Linux mutmut fork path with the hashed mutation lock"
            )
        canary_steps = canary.get("steps") if isinstance(canary, dict) else None
        canary_setup = isinstance(canary_steps, list) and any(
            isinstance(step, dict)
            and isinstance(step.get("with"), dict)
            and step["with"].get("python-version") == "3.x"
            and step["with"].get("check-latest") == "true"
            for step in canary_steps
        )
        canary_verification = isinstance(canary_steps, list) and any(
            isinstance(step, dict)
            and step.get("run")
            == "python scripts/python_support.py verify-latest-runtime"
            for step in canary_steps
        )
        canary_install = isinstance(canary_steps, list) and any(
            isinstance(step, dict)
            and step.get("run")
            == "python -m pip install --disable-pip-version-check "
            "--require-hashes --requirement requirements-dev.lock"
            for step in canary_steps
        )
        canary_tests = isinstance(canary_steps, list) and any(
            isinstance(step, dict) and step.get("run") == "python -m pytest -q"
            for step in canary_steps
        )
        triggers = workflow.get("on") if isinstance(workflow, dict) else None
        if (
            not isinstance(canary, dict)
            or canary.get("if")
            != "${{ github.event_name == 'schedule' || github.event_name == 'workflow_dispatch' }}"
            or not canary_setup
            or not canary_install
            or not canary_tests
            or not canary_verification
            or not isinstance(triggers, dict)
            or "schedule" not in triggers
        ):
            problems.append(
                ".github/workflows/ci.yml: scheduled 3.x canary must test and "
                "detect undeclared stable releases"
            )
        ci_success_needs = (
            ci_success.get("needs") if isinstance(ci_success, dict) else None
        )
        ci_success_steps = (
            ci_success.get("steps") if isinstance(ci_success, dict) else None
        )
        required_result_environment = {
            "MUTATION_CACHE_INTEGRATION_RESULT": (
                "${{ needs.mutation-cache-integration.result }}"
            ),
            "TEST_RESULT": "${{ needs.test.result }}",
            "QUALITY_RESULT": "${{ needs.quality.result }}",
        }
        ci_success_checks_results = isinstance(ci_success_steps, list) and any(
            isinstance(step, dict)
            and step.get("env") == required_result_environment
            and isinstance(step.get("run"), str)
            and '"$MUTATION_CACHE_INTEGRATION_RESULT" != "success"' in step["run"]
            and '"$TEST_RESULT" != "success"' in step["run"]
            and '"$QUALITY_RESULT" != "success"' in step["run"]
            for step in ci_success_steps
        )
        if (
            not isinstance(ci_success_needs, list)
            or set(ci_success_needs)
            != {"test", "quality", "mutation-cache-integration"}
            or not ci_success_checks_results
        ):
            problems.append(
                ".github/workflows/ci.yml: ci-success must require tests, quality, "
                "and mutation integration while keeping canaries outside the gate"
            )

    try:
        asset_text = asset_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        problems.append(f"{asset_path.relative_to(repository_root)}: {error}")
    else:
        if (
            "prepare_runtime:" not in asset_text
            or "fromJSON(needs.prepare_runtime.outputs.matrix)" not in asset_text
            or "latest-runtime-canary:" not in asset_text
            or "schedule:" not in asset_text
        ):
            problems.append(
                "skills/repo-scaffold/assets/workflows/ci.yml: scaffold CI must "
                "load a runtime policy dynamically and retain a scheduled canary"
            )
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        problems.append(f"{skill_path.relative_to(repository_root)}: {error}")
    else:
        for requirement in (
            "single source of truth",
            "scheduled compatibility canary",
            "do not duplicate supported versions",
        ):
            if requirement not in skill_text:
                problems.append(
                    "skills/repo-scaffold/SKILL.md: missing Python support "
                    f"requirement {requirement!r}"
                )

    ruff_path = repository_root / "ruff.toml"
    try:
        policy = load_json(policy_path)
        ruff_text = ruff_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        problems.append(f"ruff.toml: could not verify Python target: {error}")
    else:
        versions = policy.get("versions") if isinstance(policy, dict) else None
        target_matches = re.findall(
            r'(?m)^target-version\s*=\s*"(py\d+)"\s*$', ruff_text
        )
        if (
            not isinstance(versions, list)
            or not versions
            or not all(isinstance(version, str) for version in versions)
        ):
            problems.append(
                ".github/python-support.json: cannot derive the Ruff target"
            )
        else:
            expected_target = "py" + versions[0].replace(".", "")
            if target_matches != [expected_target]:
                problems.append(
                    "ruff.toml: target-version must match the minimum Python "
                    f"policy release ({expected_target})"
                )
    return problems


def iter_uses_values(value: Any) -> Iterable[Any]:
    """Yield every workflow ``uses`` value from a decoded YAML tree."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "uses":
                yield child
            yield from iter_uses_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_uses_values(child)


def validate_action_references(repository_root: Path) -> list[str]:
    """Require least-privilege defaults and immutable workflow dependencies."""
    workflow_roots = (
        repository_root / ".github" / "workflows",
        repository_root / "skills" / "repo-scaffold" / "assets" / "workflows",
    )
    paths = sorted(
        {
            path
            for root in workflow_roots
            if root.is_dir()
            for pattern in ("*.yml", "*.yaml")
            for path in root.glob(pattern)
            if path.is_file()
        }
    )
    problems: list[str] = []
    action_pins: dict[str, dict[str, set[str]]] = {}
    for path in paths:
        relative = path.relative_to(repository_root)
        try:
            document = load_yaml(path)
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if isinstance(document, dict):
            if "permissions" not in document:
                problems.append(
                    f"{relative}: workflow must declare top-level permissions"
                )
            permissions = document.get("permissions")
            if isinstance(permissions, str) and permissions in {
                "read-all",
                "write-all",
            }:
                problems.append(
                    f"{relative}: workflow must use named least-privilege scopes "
                    "instead of a broad permission preset"
                )
        for reference in iter_uses_values(document):
            if not isinstance(reference, str) or not reference.strip():
                problems.append(f"{relative}: uses must be a nonempty string")
                continue
            if reference.startswith("./"):
                continue
            if reference.startswith("docker://"):
                if re.fullmatch(r"docker://[^\s@]+@sha256:[0-9a-f]{64}", reference):
                    continue
                problems.append(
                    f"{relative}: external container reference must use a full "
                    f"sha256 digest: {reference}"
                )
                continue
            match = re.fullmatch(
                r"(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
                r"(?:/[A-Za-z0-9_.\-/]+)?)@(?P<sha>[0-9a-fA-F]{40})",
                reference,
            )
            if match is not None:
                action_repository = "/".join(
                    match.group("action").split("/")[:2]
                ).casefold()
                sha = match.group("sha").lower()
                action_pins.setdefault(action_repository, {}).setdefault(
                    sha, set()
                ).add(relative.as_posix())
                continue
            problems.append(
                f"{relative}: external action or workflow must use a full "
                f"commit SHA: {reference}"
            )
    for action_repository, pins in sorted(action_pins.items()):
        if len(pins) <= 1:
            continue
        evidence = "; ".join(
            f"{sha} in {', '.join(sorted(locations))}"
            for sha, locations in sorted(pins.items())
        )
        problems.append(
            f"workflow action pin drift: {action_repository} uses multiple "
            f"commit SHAs: {evidence}"
        )
    return problems


def validate_ci_toolchain_contract(repository_root: Path) -> list[str]:
    """Validate centralized CI bootstrap and standalone-tool consumers."""
    script = (
        repository_root / "skills" / "repo-scaffold" / "scripts" / "ci_toolchain.py"
    )
    policy_paths = (
        repository_root / ".github" / "ci-toolchain.json",
        repository_root / "skills" / "repo-scaffold" / "assets" / "ci-toolchain.json",
    )
    if not script.is_file():
        return [
            "CI toolchain contract: "
            "skills/repo-scaffold/scripts/ci_toolchain.py is missing"
        ]

    problems: list[str] = []
    for policy_path in policy_paths:
        relative = policy_path.relative_to(repository_root)
        if not policy_path.is_file():
            problems.append(f"CI toolchain contract: {relative} is missing")
            continue
        command = [
            sys.executable,
            str(script),
            "--policy",
            str(policy_path),
            "validate",
        ]
        try:
            result = subprocess.run(  # noqa: S603 - interpreter/script are explicit
                command,
                cwd=repository_root,
                env=child_process_environment(),
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            problems.append(f"CI toolchain contract: {relative} validation timed out")
            continue
        if result.returncode != 0:
            detail = (
                result.stderr.strip() or result.stdout.strip() or "validation failed"
            )
            problems.extend(
                f"CI toolchain contract: {relative}: {line}"
                for line in detail.splitlines()
            )

    installed_policy_path = policy_paths[0]
    asset_policy_path = policy_paths[1]
    try:
        installed_policy = load_json(installed_policy_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        installed_policy = None
    installed_tools = (
        installed_policy.get("standalone-tools")
        if isinstance(installed_policy, dict)
        else None
    )
    if not isinstance(installed_tools, dict) or set(installed_tools) != {
        "actionlint",
        "shellcheck",
    }:
        problems.append(
            ".github/ci-toolchain.json: standalone-tools must define exactly "
            "actionlint and shellcheck for repository workflow validation"
        )
    installed_npm_tools = (
        installed_policy.get("npm-tools")
        if isinstance(installed_policy, dict)
        else None
    )
    try:
        asset_policy = load_json(asset_policy_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        asset_policy = None
    if not isinstance(asset_policy, dict) or asset_policy.get("standalone-tools") != {}:
        problems.append(
            "skills/repo-scaffold/assets/ci-toolchain.json: generic scaffold "
            "must not prescribe unused standalone tools"
        )
    asset_npm_tools = (
        asset_policy.get("npm-tools") if isinstance(asset_policy, dict) else None
    )
    installed_tooling_python = (
        installed_policy.get("tooling-python-minimum")
        if isinstance(installed_policy, dict)
        else None
    )
    asset_tooling_python = (
        asset_policy.get("tooling-python-minimum")
        if isinstance(asset_policy, dict)
        else None
    )
    if (
        not nonempty_string(installed_tooling_python)
        or installed_tooling_python != asset_tooling_python
    ):
        problems.append(
            "CI toolchain policies: tooling Python minimum must stay synchronized"
        )
    if (
        not isinstance(installed_npm_tools, dict)
        or set(installed_npm_tools) != {"markdownlint-cli2"}
        or installed_npm_tools != asset_npm_tools
    ):
        problems.append(
            "CI toolchain policies: markdownlint-cli2 npm pin must be defined "
            "once per policy and stay synchronized"
        )

    for document_name in ("README.md", "CONTRIBUTING.md"):
        path = repository_root / document_name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            problems.append(
                f"{document_name}: could not verify CI toolchain link: {error}"
            )
            continue
        if ".github/ci-toolchain.json" not in text:
            problems.append(f"{document_name}: must reference the CI toolchain policy")
        if "ci_toolchain.py run-markdownlint" not in text:
            problems.append(
                f"{document_name}: markdownlint must consume the CI toolchain policy"
            )
        if re.search(r"markdownlint-cli2@\d", text):
            problems.append(
                f"{document_name}: markdownlint version must not be hardcoded"
            )

    workflow_path = repository_root / ".github" / "workflows" / "ci.yml"
    try:
        workflow_text = workflow_path.read_text(encoding="utf-8")
        workflow = load_yaml(workflow_path)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        problems.append(
            f".github/workflows/ci.yml: could not verify toolchain: {error}"
        )
        workflow_text = ""
        workflow = None
    if re.search(
        r"(?m)^\s*(?:SHELLCHECK|ACTIONLINT)_VERSION:\s*['\"]?\d",
        workflow_text,
    ):
        problems.append(
            ".github/workflows/ci.yml: standalone tool versions must not be hardcoded"
        )
    jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
    if isinstance(jobs, dict):
        prepare = jobs.get("prepare_ci")
        quality = jobs.get("quality")
        canary = jobs.get("toolchain-drift-canary")
    else:
        prepare = quality = canary = None
        problems.append(".github/workflows/ci.yml: jobs must be a mapping")
    expected_outputs = {
        "shellcheck_repository": "${{ steps.toolchain.outputs.shellcheck_repository }}",
        "shellcheck_tag": "${{ steps.toolchain.outputs.shellcheck_tag }}",
        "shellcheck_asset": "${{ steps.toolchain.outputs.shellcheck_asset }}",
        "shellcheck_archive_format": "${{ steps.toolchain.outputs.shellcheck_archive_format }}",
        "shellcheck_executable_path": "${{ steps.toolchain.outputs.shellcheck_executable_path }}",
        "shellcheck_sha256": "${{ steps.toolchain.outputs.shellcheck_sha256 }}",
        "actionlint_repository": "${{ steps.toolchain.outputs.actionlint_repository }}",
        "actionlint_tag": "${{ steps.toolchain.outputs.actionlint_tag }}",
        "actionlint_asset": "${{ steps.toolchain.outputs.actionlint_asset }}",
        "actionlint_archive_format": "${{ steps.toolchain.outputs.actionlint_archive_format }}",
        "actionlint_executable_path": "${{ steps.toolchain.outputs.actionlint_executable_path }}",
        "actionlint_sha256": "${{ steps.toolchain.outputs.actionlint_sha256 }}",
    }
    expected_prepare_outputs = {
        "matrix": "${{ steps.support.outputs.matrix }}",
        "latest": "${{ steps.support.outputs.latest }}",
        **expected_outputs,
    }
    prepare_outputs = prepare.get("outputs") if isinstance(prepare, dict) else None
    prepare_steps = prepare.get("steps") if isinstance(prepare, dict) else None
    if prepare_outputs != expected_prepare_outputs:
        problems.append(
            ".github/workflows/ci.yml: prepare_ci must expose standalone tool outputs"
        )
    if not isinstance(prepare_steps, list) or not any(
        isinstance(step, dict)
        and step.get("id") == "toolchain"
        and step.get("run")
        == "python skills/repo-scaffold/scripts/ci_toolchain.py "
        'emit-github-output >> "$GITHUB_OUTPUT"'
        for step in prepare_steps
    ):
        problems.append(
            ".github/workflows/ci.yml: prepare_ci must load the CI toolchain policy"
        )
    quality_steps = quality.get("steps") if isinstance(quality, dict) else None
    expected_environments = {
        "Install ShellCheck": {
            "SHELLCHECK_REPOSITORY": "${{ needs.prepare_ci.outputs.shellcheck_repository }}",
            "SHELLCHECK_TAG": "${{ needs.prepare_ci.outputs.shellcheck_tag }}",
            "SHELLCHECK_ASSET": "${{ needs.prepare_ci.outputs.shellcheck_asset }}",
            "SHELLCHECK_ARCHIVE_FORMAT": "${{ needs.prepare_ci.outputs.shellcheck_archive_format }}",
            "SHELLCHECK_EXECUTABLE_PATH": "${{ needs.prepare_ci.outputs.shellcheck_executable_path }}",
            "SHELLCHECK_SHA256": "${{ needs.prepare_ci.outputs.shellcheck_sha256 }}",
        },
        "Install actionlint": {
            "ACTIONLINT_REPOSITORY": "${{ needs.prepare_ci.outputs.actionlint_repository }}",
            "ACTIONLINT_TAG": "${{ needs.prepare_ci.outputs.actionlint_tag }}",
            "ACTIONLINT_ASSET": "${{ needs.prepare_ci.outputs.actionlint_asset }}",
            "ACTIONLINT_ARCHIVE_FORMAT": "${{ needs.prepare_ci.outputs.actionlint_archive_format }}",
            "ACTIONLINT_EXECUTABLE_PATH": "${{ needs.prepare_ci.outputs.actionlint_executable_path }}",
            "ACTIONLINT_SHA256": "${{ needs.prepare_ci.outputs.actionlint_sha256 }}",
        },
    }
    expected_extraction_fragments = {
        "Install ShellCheck": (
            'extract_dir="$RUNNER_TEMP/shellcheck-extract"',
            '--directory "$extract_dir"',
            'install -m 0755 "$extract_dir/$SHELLCHECK_EXECUTABLE_PATH" '
            '"$RUNNER_TEMP/shellcheck"',
        ),
        "Install actionlint": (
            'extract_dir="$RUNNER_TEMP/actionlint-extract"',
            '--directory "$extract_dir"',
            'install -m 0755 "$extract_dir/$ACTIONLINT_EXECUTABLE_PATH" '
            '"$RUNNER_TEMP/actionlint"',
        ),
    }
    for step_name, expected_environment in expected_environments.items():
        matching = [
            step
            for step in quality_steps or []
            if isinstance(step, dict) and step.get("name") == step_name
        ]
        if len(matching) != 1 or matching[0].get("env") != expected_environment:
            problems.append(
                f".github/workflows/ci.yml: {step_name} must consume policy outputs"
            )
            continue
        run_script = matching[0].get("run")
        if not isinstance(run_script, str) or any(
            fragment not in run_script
            for fragment in expected_extraction_fragments[step_name]
        ):
            problems.append(
                f".github/workflows/ci.yml: {step_name} must extract before install"
            )
    if isinstance(installed_tools, dict):
        forbidden_workflow_literals: set[str] = set()
        for tool in installed_tools.values():
            if not isinstance(tool, dict):
                continue
            version = str(tool.get("version", ""))
            for field in ("repository", "tag-template", "asset-template"):
                value = tool.get(field)
                if isinstance(value, str):
                    forbidden_workflow_literals.add(value.replace("{version}", version))
            executable = tool.get("executable-path-template")
            if isinstance(executable, str) and "/" in executable:
                forbidden_workflow_literals.add(
                    executable.replace("{version}", version)
                )
        for literal in sorted(forbidden_workflow_literals):
            if literal in workflow_text:
                problems.append(
                    ".github/workflows/ci.yml: standalone tool metadata must "
                    f"come from policy outputs, found {literal!r}"
                )
    canary_steps = canary.get("steps") if isinstance(canary, dict) else None
    triggers = workflow.get("on") if isinstance(workflow, dict) else None
    if (
        not isinstance(canary, dict)
        or canary.get("if")
        != "${{ github.event_name == 'schedule' || github.event_name == 'workflow_dispatch' }}"
        or not isinstance(canary_steps, list)
        or not any(
            isinstance(step, dict)
            and step.get("run")
            == "python skills/repo-scaffold/scripts/ci_toolchain.py "
            "verify-latest-releases"
            for step in canary_steps
        )
        or not isinstance(triggers, dict)
        or "schedule" not in triggers
    ):
        problems.append(
            ".github/workflows/ci.yml: scheduled/manual toolchain drift canary "
            "must verify latest releases"
        )

    documentation_path = (
        repository_root
        / "skills"
        / "repo-scaffold"
        / "assets"
        / "workflows"
        / "documentation.yml"
    )
    try:
        documentation_text = documentation_path.read_text(encoding="utf-8")
        documentation = load_yaml(documentation_path)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        problems.append(
            "skills/repo-scaffold/assets/workflows/documentation.yml: "
            f"could not verify toolchain: {error}"
        )
        documentation_text = ""
        documentation = None
    if re.search(r"(?m)^\s*python-version:\s*['\"]?3\.\d+", documentation_text):
        problems.append(
            "skills/repo-scaffold/assets/workflows/documentation.yml: "
            "documentation Python must not be hardcoded"
        )
    documentation_jobs = (
        documentation.get("jobs") if isinstance(documentation, dict) else None
    )
    if isinstance(documentation_jobs, dict):
        docs_prepare = documentation_jobs.get("prepare_docs")
        docs_contract = documentation_jobs.get("docs-contract")
    else:
        docs_prepare = docs_contract = None
    docs_outputs = (
        docs_prepare.get("outputs") if isinstance(docs_prepare, dict) else None
    )
    docs_prepare_steps = (
        docs_prepare.get("steps") if isinstance(docs_prepare, dict) else None
    )
    docs_steps = docs_contract.get("steps") if isinstance(docs_contract, dict) else None
    if (
        docs_outputs
        != {
            "documentation_python": "${{ steps.toolchain.outputs.documentation_python }}"
        }
        or not isinstance(docs_prepare_steps, list)
        or not any(
            isinstance(step, dict)
            and step.get("id") == "toolchain"
            and step.get("run")
            == 'python scripts/ci_toolchain.py emit-github-output >> "$GITHUB_OUTPUT"'
            for step in docs_prepare_steps
        )
    ):
        problems.append(
            "skills/repo-scaffold/assets/workflows/documentation.yml: "
            "prepare_docs must load the CI toolchain policy"
        )
    if (
        not isinstance(docs_contract, dict)
        or docs_contract.get("needs") != "prepare_docs"
        or not isinstance(docs_steps, list)
        or not any(
            isinstance(step, dict)
            and isinstance(step.get("with"), dict)
            and step["with"].get("python-version")
            == "${{ needs.prepare_docs.outputs.documentation_python }}"
            for step in docs_steps
        )
    ):
        problems.append(
            "skills/repo-scaffold/assets/workflows/documentation.yml: "
            "docs-contract must consume the rolling policy runtime"
        )

    skill_path = repository_root / "skills" / "repo-scaffold" / "SKILL.md"
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        problems.append(f"skills/repo-scaffold/SKILL.md: {error}")
    else:
        for requirement in (
            ".github/ci-toolchain.json",
            "ci_toolchain.py run-markdownlint",
            "scheduled/manual drift canary",
            "must not install an unreviewed release automatically",
        ):
            if requirement not in skill_text:
                problems.append(
                    "skills/repo-scaffold/SKILL.md: missing CI toolchain "
                    f"requirement {requirement!r}"
                )
    setup_path = (
        repository_root / "skills" / "repo-scaffold" / "references" / "github-setup.md"
    )
    try:
        setup_text = setup_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        problems.append(f"skills/repo-scaffold/references/github-setup.md: {error}")
    else:
        if "tooling-python-minimum" not in setup_text:
            problems.append(
                "skills/repo-scaffold/references/github-setup.md: CodeQL "
                "preflight must read the tooling Python policy"
            )
        if re.search(r"Python\s+3\.\d+\s+or newer", setup_text):
            problems.append(
                "skills/repo-scaffold/references/github-setup.md: tooling Python "
                "minimum must not be hardcoded"
            )
    return problems


def validate_mirrored_dependency_metadata(repository_root: Path) -> list[str]:
    """Keep intentionally mirrored dependency metadata synchronized."""
    problems: list[str] = []
    requirement_paths = (
        repository_root / "requirements-dev.txt",
        repository_root
        / "skills"
        / "repo-scaffold"
        / "assets"
        / "requirements-docs.txt",
    )
    for package in ("PyYAML", "markdown-it-py"):
        requirement_pins: list[str | None] = []
        for path in requirement_paths:
            relative = path.relative_to(repository_root).as_posix()
            try:
                matches = re.findall(
                    rf"(?im)^{re.escape(package)}==([^\s#]+)\s*$",
                    path.read_text(encoding="utf-8"),
                )
            except (OSError, UnicodeError) as error:
                problems.append(f"{relative}: could not verify {package} pin: {error}")
                requirement_pins.append(None)
                continue
            if len(matches) != 1:
                problems.append(f"{relative}: must contain exactly one {package} pin")
                requirement_pins.append(None)
            else:
                requirement_pins.append(matches[0])
        if None not in requirement_pins and len(set(requirement_pins)) != 1:
            problems.append(
                f"{package} pin drift: requirements-dev.txt and the scaffold "
                "docs requirements must match"
            )

    release_config_paths = (
        repository_root / "release-please-config.json",
        repository_root
        / "skills"
        / "repo-scaffold"
        / "assets"
        / "release-please-config.json",
    )
    schema_values: list[str | None] = []
    for path in release_config_paths:
        relative = path.relative_to(repository_root).as_posix()
        try:
            document = load_json(path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            problems.append(f"{relative}: could not verify $schema pin: {error}")
            schema_values.append(None)
            continue
        schema = document.get("$schema") if isinstance(document, dict) else None
        if not nonempty_string(schema):
            problems.append(f"{relative}: $schema must be a nonempty string")
            schema_values.append(None)
        else:
            schema_values.append(schema)
    if None not in schema_values and len(set(schema_values)) != 1:
        problems.append(
            "Release Please schema drift: installed and scaffold configs must match"
        )
    return problems


def normalize_package_name(name: str) -> str:
    """Normalize one Python distribution name using the PyPA comparison rule."""
    return re.sub(r"[-_.]+", "-", name).lower()


def validate_development_dependency_contract(repository_root: Path) -> list[str]:
    """Validate the hashed development lock and its CI coverage consumers."""
    problems: list[str] = []
    direct_path = repository_root / "requirements-dev.txt"
    lock_path = repository_root / "requirements-dev.lock"
    try:
        direct_text = direct_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [f"requirements-dev.txt: could not verify direct pins: {error}"]
    try:
        lock_text = lock_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [f"requirements-dev.lock: could not verify hashed lock: {error}"]

    direct_pins: dict[str, str] = {}
    for line_number, raw_line in enumerate(direct_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;\\]+)", line)
        if match is None:
            problems.append(
                "requirements-dev.txt:"
                f"{line_number}: direct dependencies must use exact name==version pins"
            )
            continue
        name = normalize_package_name(match.group(1))
        if name in direct_pins:
            problems.append(
                f"requirements-dev.txt:{line_number}: duplicate direct pin for {name}"
            )
        direct_pins[name] = match.group(2)

    for name, version in {"exceptiongroup": "1.3.1", "tomli": "2.4.1"}.items():
        if direct_pins.get(name) != version:
            problems.append(
                "requirements-dev.txt: the cross-version lock requires "
                f"{name}=={version}"
            )

    entry_pattern = re.compile(r"(?m)^([A-Za-z0-9_.-]+)==([^\s;\\]+)\s+\\$")
    matches = list(entry_pattern.finditer(lock_text))
    lock_pins: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = normalize_package_name(match.group(1))
        version = match.group(2)
        block_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(lock_text)
        )
        block = lock_text[match.start() : block_end]
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})(?:\s*\\)?", block)
        if not hashes:
            problems.append(
                f"requirements-dev.lock: {name}=={version} must have a SHA-256 hash"
            )
        if name in lock_pins:
            problems.append(f"requirements-dev.lock: duplicate locked package {name}")
        lock_pins[name] = version

    if not matches:
        problems.append("requirements-dev.lock: no locked package entries found")
    if "--generate-hashes" not in lock_text:
        problems.append("requirements-dev.lock: generator header must record hash mode")
    if re.search(r"(?m)^--(?:index-url|trusted-host)\b", lock_text):
        problems.append(
            "requirements-dev.lock: repository-specific index settings are forbidden"
        )
    for name, version in direct_pins.items():
        locked_version = lock_pins.get(name)
        if locked_version is None:
            problems.append(f"requirements-dev.lock: direct package {name} is missing")
        elif locked_version != version:
            problems.append(
                f"requirements-dev.lock: {name} pin {locked_version} does not match "
                f"requirements-dev.txt pin {version}"
            )

    workflow_path = repository_root / ".github" / "workflows" / "ci.yml"
    try:
        workflow = load_yaml(workflow_path)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        problems.append(f".github/workflows/ci.yml: could not verify lock use: {error}")
        workflow = None
    jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
    install_runs: list[str] = []
    cache_paths: list[Any] = []
    if isinstance(jobs, dict):
        for job in jobs.values():
            steps = job.get("steps") if isinstance(job, dict) else None
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if (
                    isinstance(run, str)
                    and "python -m pip install" in run
                    and "requirements-dev" in run
                ):
                    install_runs.append(" ".join(run.split()))
                uses = step.get("uses")
                step_with = step.get("with")
                if (
                    isinstance(uses, str)
                    and uses.startswith("actions/setup-python@")
                    and isinstance(step_with, dict)
                ):
                    cache_paths.append(step_with.get("cache-dependency-path"))
    expected_install = (
        "python -m pip install --disable-pip-version-check --require-hashes "
        "--requirement requirements-dev.lock"
    )
    if len(install_runs) != 3 or any(run != expected_install for run in install_runs):
        problems.append(
            ".github/workflows/ci.yml: every development install must use the "
            "hashed requirements-dev.lock"
        )
    if sorted(path for path in cache_paths if isinstance(path, str)) != [
        "requirements-dev.lock",
        "requirements-dev.lock",
        "requirements-dev.lock",
        "requirements-mutation.lock",
    ]:
        problems.append(
            ".github/workflows/ci.yml: every Python cache must key on its "
            "reviewed dependency lock"
        )

    quality = jobs.get("quality") if isinstance(jobs, dict) else None
    quality_steps = quality.get("steps") if isinstance(quality, dict) else None
    expected_coverage = (
        "python -m coverage erase\n"
        "python -m coverage run -m pytest -q\n"
        "python -m coverage report"
    )
    if not isinstance(quality_steps, list) or not any(
        isinstance(step, dict)
        and isinstance(step.get("run"), str)
        and step["run"].rstrip("\n") == expected_coverage
        for step in quality_steps
    ):
        problems.append(
            ".github/workflows/ci.yml: quality must enforce the repository coverage gate"
        )

    mypy_steps = [
        step
        for step in quality_steps or []
        if isinstance(step, dict)
        and isinstance(step.get("run"), str)
        and "python -m mypy" in step["run"]
    ]
    required_mypy_paths = {
        "skills/repo-scaffold/scripts/codeql_preflight.py",
        "skills/repo-scaffold/scripts/ci_toolchain.py",
        "skills/repo-scaffold/scripts/validate_scaffold.py",
        "scripts/prepare_mutation_cache.py",
        "scripts/python_support.py",
        "scripts/run_mutation_testing.py",
        "scripts/validate_mutation_results.py",
        "scripts/validate_repository.py",
        "scripts/validate_workflows.py",
        "tests",
    }
    mypy_arguments = (
        set(mypy_steps[0]["run"].split()) if len(mypy_steps) == 1 else set()
    )
    if not required_mypy_paths.issubset(mypy_arguments):
        problems.append(
            ".github/workflows/ci.yml: Mypy must check every production script and tests"
        )

    coverage_path = repository_root / ".coveragerc"
    coverage_config = configparser.ConfigParser()
    try:
        coverage_config.read_string(coverage_path.read_text(encoding="utf-8"))
        branch = coverage_config.getboolean("run", "branch")
        sources = {
            line.strip().replace("\\", "/")
            for line in coverage_config.get("run", "source").splitlines()
            if line.strip()
        }
        fail_under = coverage_config.getfloat("report", "fail_under")
    except (OSError, UnicodeError, configparser.Error, ValueError) as error:
        problems.append(f".coveragerc: could not verify coverage policy: {error}")
    else:
        expected_sources = {"scripts", "skills/repo-scaffold/scripts"}
        if (
            not branch
            or sources != expected_sources
            or fail_under < COVERAGE_FAIL_UNDER
        ):
            problems.append(
                ".coveragerc: require branch coverage for both script trees with "
                f"a fail-under floor of at least {COVERAGE_FAIL_UNDER}"
            )

    for relative in ("README.md", "CONTRIBUTING.md"):
        path = repository_root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            problems.append(
                f"{relative}: could not verify dependency guidance: {error}"
            )
            continue
        for required in (
            "requirements-dev.lock",
            "python -m coverage run -m pytest -q",
            "python -m coverage report",
        ):
            if required not in text:
                problems.append(
                    f"{relative}: development guidance must include {required}"
                )

    attributes_path = repository_root / ".gitattributes"
    try:
        attributes = attributes_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        problems.append(f".gitattributes: could not verify export exclusions: {error}")
    else:
        for relative in (".coveragerc", "requirements-dev.lock"):
            if not re.search(
                rf"(?m)^/?{re.escape(relative)}\s+export-ignore\s*$", attributes
            ):
                problems.append(f".gitattributes: {relative} must be export-ignore")

    ignore_path = repository_root / ".gitignore"
    try:
        ignore_lines = {
            line.strip()
            for line in ignore_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    except (OSError, UnicodeError) as error:
        problems.append(f".gitignore: could not verify coverage exclusions: {error}")
    else:
        if ".coverage*" in ignore_lines or not {
            ".coverage",
            ".coverage.*",
        }.issubset(ignore_lines):
            problems.append(
                ".gitignore: ignore coverage data files without ignoring .coveragerc"
            )
    return problems


def validate_mutation_testing_contract(repository_root: Path) -> list[str]:
    """Validate the isolated, evidence-preserving mutation-testing configuration."""
    relative_paths = (
        ".gitattributes",
        ".github/workflows/mutation-testing.yml",
        ".gitignore",
        "CONTRIBUTING.md",
        "README.md",
        "pyproject.toml",
        "requirements-mutation.lock",
        "requirements-mutation.txt",
        "scripts/prepare_mutation_cache.py",
        "scripts/run_mutation_testing.py",
        "scripts/validate_mutation_results.py",
        "tests/test_ci_toolchain.py",
        "tests/test_codeql_preflight.py",
        "tests/test_mutation_validation.py",
        "tests/test_mutation_cache.py",
        "tests/test_mutation_runner.py",
        "tests/test_mutation_runner_linux.py",
        "tests/test_python_support.py",
        "tests/test_repository_validation.py",
        "tests/test_scaffold_validation.py",
    )
    texts: dict[str, str] = {}
    problems: list[str] = []
    for relative in relative_paths:
        try:
            texts[relative] = (repository_root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            problems.append(f"{relative}: could not verify mutation contract: {error}")
    if len(texts) != len(relative_paths):
        return problems

    direct_lines = [
        line.strip()
        for line in texts["requirements-mutation.txt"].splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    expected_direct_lines = [
        "-r requirements-dev.txt",
        "mutmut==3.7.0",
        "toml==0.10.2",
    ]
    if direct_lines != expected_direct_lines:
        problems.append(
            "requirements-mutation.txt: must extend requirements-dev.txt and pin "
            "the reviewed mutmut and Python 3.10 toml versions"
        )

    lock_text = texts["requirements-mutation.lock"]
    if (
        "--generate-hashes" not in lock_text
        or "--output-file=requirements-mutation.lock" not in lock_text
        or re.search(r"(?m)^--(?:index-url|trusted-host)\b", lock_text)
    ):
        problems.append(
            "requirements-mutation.lock: must be generated in portable hash mode"
        )
    for requirement in ("mutmut==3.7.0", "toml==0.10.2"):
        entry = re.search(
            rf"(?ms)^{re.escape(requirement)}\s+\\$(.*?)(?=^[A-Za-z0-9_.-]+==|\Z)",
            lock_text,
        )
        if (
            entry is None
            or re.search(r"--hash=sha256:[0-9a-f]{64}", entry.group(0)) is None
        ):
            problems.append(
                f"requirements-mutation.lock: missing hashed {requirement} entry"
            )

    validator_relative = "scripts/validate_mutation_results.py"
    try:
        validator_tree = ast.parse(
            texts[validator_relative], filename=validator_relative
        )
    except SyntaxError as error:
        problems.append(f"{validator_relative}: invalid Python source: {error}")
    else:
        assignments = {
            node.targets[0].id: node.value
            for node in validator_tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }
        threshold = assignments.get("MINIMUM_MUTATION_SCORE_BASIS_POINTS")
        if not (
            isinstance(threshold, ast.Constant)
            and type(threshold.value) is int
            and threshold.value == 10_000
        ):
            problems.append(
                f"{validator_relative}: mutation score floor must remain 100.00%"
            )
        unsafe = assignments.get("UNSAFE_RESULT_FIELDS")
        unsafe_values = (
            tuple(
                element.value
                for element in unsafe.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
            if isinstance(unsafe, ast.Tuple)
            else ()
        )
        expected_unsafe_values = (
            "no_tests",
            "skipped",
            "suspicious",
            "check_was_interrupted_by_user",
            "segfault",
        )
        if (
            not isinstance(unsafe, ast.Tuple)
            or len(unsafe.elts) != len(expected_unsafe_values)
            or unsafe_values != expected_unsafe_values
        ):
            problems.append(
                f"{validator_relative}: incomplete result classes must fail and "
                "timeout must remain a detected result"
            )

    workflow_path = repository_root / ".github" / "workflows" / "mutation-testing.yml"
    try:
        workflow = load_yaml(workflow_path)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        problems.append(
            f".github/workflows/mutation-testing.yml: invalid workflow: {error}"
        )
        workflow = None
    triggers = workflow.get("on") if isinstance(workflow, dict) else None
    permissions = workflow.get("permissions") if isinstance(workflow, dict) else None
    jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
    job = jobs.get("mutation-quality") if isinstance(jobs, dict) else None
    steps = job.get("steps") if isinstance(job, dict) else None
    if not isinstance(triggers, dict) or set(triggers) != {
        "schedule",
        "workflow_dispatch",
    }:
        problems.append(
            ".github/workflows/mutation-testing.yml: use only scheduled and manual "
            "trusted triggers"
        )
    dispatch = triggers.get("workflow_dispatch") if isinstance(triggers, dict) else None
    expected_dispatch = {
        "inputs": {
            "clean": {
                "description": "Ignore incremental mutation state and run every mutant",
                "required": "false",
                "type": "boolean",
                "default": "false",
            }
        }
    }
    if dispatch != expected_dispatch:
        problems.append(
            ".github/workflows/mutation-testing.yml: manual runs must expose the "
            "clean full-run verification input"
        )
    if permissions != {"contents": "read"}:
        problems.append(
            ".github/workflows/mutation-testing.yml: permissions must be contents: read"
        )
    if (
        not isinstance(job, dict)
        or job.get("runs-on") != "ubuntu-latest"
        or job.get("timeout-minutes") != "120"
        or not isinstance(steps, list)
    ):
        problems.append(
            ".github/workflows/mutation-testing.yml: mutation-quality must use "
            "bounded Ubuntu execution"
        )
        steps = []
    run_steps = {
        "\n".join(line.rstrip() for line in step["run"].strip().splitlines())
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    }
    required_runs = {
        "python -m pip install --disable-pip-version-check --require-hashes "
        "--requirement requirements-mutation.lock",
        "python scripts/prepare_mutation_cache.py prepare",
        "python scripts/run_mutation_testing.py --max-children 4",
        "python scripts/prepare_mutation_cache.py record",
        "mutmut export-cicd-stats\n"
        "mutmut results --all true > mutants/mutation-results.txt\n"
        "python scripts/validate_mutation_results.py",
    }
    if not required_runs.issubset(run_steps):
        problems.append(
            ".github/workflows/mutation-testing.yml: install the hashed lock, run "
            "mutmut, and validate exported results"
        )
    mutation_run_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("run") == "python scripts/run_mutation_testing.py --max-children 4"
    ]
    expected_mutation_environment = {
        "REPO_SCAFFOLD_MUTATION_SOURCE_ROOT": "${{ github.workspace }}"
    }
    mutation_required_condition = (
        "${{ steps.mutation-clean-cache.outputs.cache-hit != 'true' }}"
    )
    if (
        len(mutation_run_steps) != 1
        or mutation_run_steps[0].get("id") != "mutation-run"
        or mutation_run_steps[0].get("env") != expected_mutation_environment
        or mutation_run_steps[0].get("if") != mutation_required_condition
    ):
        problems.append(
            ".github/workflows/mutation-testing.yml: mutation run must expose "
            "the tracked source root and skip only for a verified clean cache hit"
        )
    export_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("run"), str)
        and "python scripts/validate_mutation_results.py" in step["run"]
    ]
    if len(export_steps) != 1 or export_steps[0].get("if") != "${{ always() }}":
        problems.append(
            ".github/workflows/mutation-testing.yml: mutation diagnostics must "
            "export after failed runs"
        )
    upload_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/upload-artifact@")
    ]
    required_artifact_paths = {
        "mutants/mutmut-cicd-stats.json",
        "mutants/mutation-results.txt",
        "mutants/mutmut-stats.json",
        "mutants/mutation-cache-manifest.json",
        "mutants/**/*.meta",
        "mutants/**/*.py",
    }
    raw_artifact_settings = (
        upload_steps[0].get("with") if len(upload_steps) == 1 else None
    )
    artifact_settings = (
        raw_artifact_settings if isinstance(raw_artifact_settings, dict) else {}
    )
    artifact_path = artifact_settings.get("path")
    artifact_paths = (
        {line.strip() for line in artifact_path.splitlines() if line.strip()}
        if isinstance(artifact_path, str)
        else set()
    )
    if (
        len(upload_steps) != 1
        or upload_steps[0].get("if") != "${{ always() }}"
        or artifact_settings.get("name") != "mutation-results"
        or artifact_settings.get("retention-days") != "14"
        or not required_artifact_paths.issubset(artifact_paths)
    ):
        problems.append(
            ".github/workflows/mutation-testing.yml: retain summaries, generated "
            "mutants, and per-file metadata for diagnosis"
        )
    cache_paths = [
        step.get("with", {}).get("cache-dependency-path")
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("with"), dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/setup-python@")
    ]
    if cache_paths != ["requirements-mutation.lock"]:
        problems.append(
            ".github/workflows/mutation-testing.yml: Python cache must key on "
            "requirements-mutation.lock"
        )
    mutation_cache_restore_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/cache/restore@")
    ]
    mutation_cache_save_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/cache/save@")
    ]
    cache_platform_prefix = (
        "mutmut-v4-${{ runner.os }}-${{ runner.arch }}-python-"
        "${{ steps.python.outputs.python-version }}"
    )
    expected_clean_cache = {
        "path": "mutants/",
        "key": f"{cache_platform_prefix}-clean-${{{{ github.sha }}}}",
    }
    expected_incremental_cache_restore = {
        "path": "mutants/",
        "key": (
            f"{cache_platform_prefix}-incremental-${{{{ github.sha }}}}-"
            "${{ github.run_id }}-${{ github.run_attempt }}"
        ),
        "restore-keys": (
            f"{cache_platform_prefix}-incremental-${{{{ github.sha }}}}-\n"
            f"{cache_platform_prefix}-incremental-\n"
        ),
    }
    clean_restore_condition = "${{ !inputs.clean }}"
    incremental_restore_condition = (
        "${{ !inputs.clean && steps.mutation-clean-cache.outputs.cache-hit != 'true' }}"
    )
    clean_save_condition = (
        "${{ success() && inputs.clean && steps.mutation-run.outcome == 'success' "
        "&& steps.mutation-record.outcome == 'success' }}"
    )
    incremental_save_condition = (
        "${{ success() && !inputs.clean && "
        "steps.mutation-clean-cache.outputs.cache-hit != 'true' && "
        "steps.mutation-run.outcome == 'success' && "
        "steps.mutation-record.outcome == 'success' }}"
    )
    expected_incremental_cache_save = {
        "path": "mutants/",
        "key": expected_incremental_cache_restore["key"],
    }
    if (
        len(mutation_cache_restore_steps) != 2
        or mutation_cache_restore_steps[0].get("id") != "mutation-clean-cache"
        or mutation_cache_restore_steps[0].get("if") != clean_restore_condition
        or mutation_cache_restore_steps[0].get("with") != expected_clean_cache
        or mutation_cache_restore_steps[1].get("id") != "mutation-cache"
        or mutation_cache_restore_steps[1].get("if") != incremental_restore_condition
        or mutation_cache_restore_steps[1].get("with")
        != expected_incremental_cache_restore
        or len(mutation_cache_save_steps) != 2
        or mutation_cache_save_steps[0].get("if") != incremental_save_condition
        or mutation_cache_save_steps[0].get("with") != expected_incremental_cache_save
        or mutation_cache_save_steps[1].get("if") != clean_save_condition
        or mutation_cache_save_steps[1].get("with") != expected_clean_cache
    ):
        problems.append(
            ".github/workflows/mutation-testing.yml: mutation state cache must "
            "restore and save progressive state under immutable per-run keys, "
            "save verified clean results separately, and use runtime- and "
            "platform-scoped v4 keys"
        )

    cache_preparer_relative = "scripts/prepare_mutation_cache.py"
    try:
        cache_preparer_tree = ast.parse(
            texts[cache_preparer_relative], filename=cache_preparer_relative
        )
    except SyntaxError as error:
        problems.append(f"{cache_preparer_relative}: invalid Python source: {error}")
    else:
        assignments = {
            node.targets[0].id: node.value
            for node in cache_preparer_tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }
        killed_codes = assignments.get("KILLED_EXIT_CODES")
        killed_values = (
            {
                element.value
                for element in killed_codes.elts
                if isinstance(element, ast.Constant) and type(element.value) is int
            }
            if isinstance(killed_codes, ast.Set)
            else set()
        )
        function_names = {
            node.name
            for node in cache_preparer_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if killed_values != {1, 3} or not {
            "prepare_cache",
            "record_cache",
            "_tests_are_compatible",
        }.issubset(function_names):
            problems.append(
                f"{cache_preparer_relative}: preserve only mutmut killed exit codes "
                "and retain conservative prepare, record, and unchanged-test checks"
            )

    runner_relative = "scripts/run_mutation_testing.py"
    try:
        runner_tree = ast.parse(texts[runner_relative], filename=runner_relative)
    except SyntaxError as error:
        problems.append(f"{runner_relative}: invalid Python source: {error}")
    else:
        runner_functions = {
            node.name
            for node in runner_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if not {
            "_create_or_reuse_mutants",
            "load_reusable_sources",
            "run_mutation_testing",
        }.issubset(runner_functions):
            problems.append(
                f"{runner_relative}: must retain the reviewed mutmut generation hook"
            )
    prepare_cache_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("run") == "python scripts/prepare_mutation_cache.py prepare"
    ]
    record_cache_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("run") == "python scripts/prepare_mutation_cache.py record"
    ]
    record_condition = (
        "${{ steps.mutation-clean-cache.outputs.cache-hit != 'true' && "
        "steps.mutation-run.outcome == 'success' }}"
    )
    if (
        len(prepare_cache_steps) != 1
        or prepare_cache_steps[0].get("if") != mutation_required_condition
        or len(record_cache_steps) != 1
        or record_cache_steps[0].get("id") != "mutation-record"
        or record_cache_steps[0].get("if") != record_condition
    ):
        problems.append(
            ".github/workflows/mutation-testing.yml: incremental mutation state "
            "must be prepared after every restore and recorded only after mutmut "
            "succeeds"
        )
    if len(mutation_cache_save_steps) == 2 and len(export_steps) == 1:
        incremental_save_index = steps.index(mutation_cache_save_steps[0])
        export_index = steps.index(export_steps[0])
        clean_save_index = steps.index(mutation_cache_save_steps[1])
        if not incremental_save_index < export_index < clean_save_index:
            problems.append(
                ".github/workflows/mutation-testing.yml: save progressive mutation "
                "state before applying the score gate and save clean state only "
                "after the gate passes"
            )
    mutation_python_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/setup-python@")
    ]
    if (
        len(mutation_python_steps) != 1
        or mutation_python_steps[0].get("id") != "python"
    ):
        problems.append(
            ".github/workflows/mutation-testing.yml: setup-python must expose the "
            "resolved runtime version for the mutation cache key"
        )

    config_text = texts["pyproject.toml"]
    expected_source_paths = ["scripts", "skills/repo-scaffold/scripts"]
    try:
        config = tomllib.loads(config_text)
        mutation_config = config["tool"]["mutmut"]
        pytest_config = config["tool"]["pytest"]["ini_options"]
        if not isinstance(mutation_config, dict) or not isinstance(pytest_config, dict):
            raise TypeError("mutation and pytest settings must be TOML tables")
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        problems.append(f"pyproject.toml: could not verify mutation settings: {error}")
    else:
        if mutation_config.get("source_paths") != expected_source_paths:
            problems.append(
                "pyproject.toml: mutation source_paths must include both complete "
                "production script trees"
            )
        if mutation_config.get("pytest_add_cli_args_test_selection") != ["tests"]:
            problems.append(
                "pyproject.toml: mutation testing must collect first-party tests "
                "from tests/"
            )
        if mutation_config.get("mutate_only_covered_lines") is not False:
            problems.append(
                "pyproject.toml: mutation testing must include uncovered lines"
            )
        for restriction in (
            "only_mutate",
            "do_not_mutate",
            "do_not_mutate_patterns",
        ):
            if mutation_config.get(restriction):
                problems.append(
                    f"pyproject.toml: mutation setting {restriction!r} must not "
                    "exclude production code"
                )
        also_copy = mutation_config.get("also_copy")
        if not isinstance(also_copy, list) or not {
            "requirements-mutation.lock",
            "requirements-mutation.txt",
        }.issubset(also_copy):
            problems.append(
                "pyproject.toml: mutation workspace must copy both mutation "
                "requirement files"
            )
        if pytest_config.get("testpaths") != ["tests"]:
            problems.append(
                "pyproject.toml: pytest must collect only first-party tests from tests/"
            )

    production_python_files = {
        path.relative_to(repository_root).as_posix()
        for path in repository_root.rglob("*.py")
        if not set(path.relative_to(repository_root).parts) & CACHE_DIRECTORIES
        and path.relative_to(repository_root).parts[0] != "tests"
    }
    scoped_python_files = {
        path.relative_to(repository_root).as_posix()
        for source_path in expected_source_paths
        for path in (repository_root / source_path).rglob("*.py")
        if not set(path.relative_to(repository_root).parts) & CACHE_DIRECTORIES
    }
    unscoped_python_files = sorted(production_python_files - scoped_python_files)
    if unscoped_python_files:
        problems.append(
            "pyproject.toml: mutation source_paths omit production Python files: "
            f"{unscoped_python_files!r}"
        )

    loader_contract = {
        "tests/test_ci_toolchain.py": ("skills.repo-scaffold.scripts.ci_toolchain",),
        "tests/test_codeql_preflight.py": (
            "scripts.validate_workflows",
            "skills.repo-scaffold.scripts.codeql_preflight",
        ),
        "tests/test_mutation_validation.py": ("scripts.validate_mutation_results",),
        "tests/test_mutation_cache.py": ("scripts.prepare_mutation_cache",),
        "tests/test_mutation_runner.py": ("scripts.run_mutation_testing",),
        "tests/test_python_support.py": ("scripts.python_support",),
        "tests/test_repository_validation.py": (
            "scripts.validate_repository",
            "scripts.validate_workflows",
        ),
        "tests/test_scaffold_validation.py": (
            "skills.repo-scaffold.scripts.validate_scaffold",
        ),
    }
    for relative, expected_names in loader_contract.items():
        try:
            tree = ast.parse(texts[relative], filename=relative)
        except SyntaxError as error:
            problems.append(
                f"{relative}: could not verify mutation loader names: {error}"
            )
            continue
        loader_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "spec_from_file_location"
        ]
        actual_names = [
            node.args[0].value
            for node in loader_calls
            if node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ]
        if sorted(actual_names) != sorted(expected_names):
            problems.append(
                f"{relative}: mutation loaders must use canonical module names "
                f"{sorted(expected_names)!r}; found {sorted(actual_names)!r}"
            )

    documentation_contract = {
        "README.md": ("requirements-mutation.lock",),
        "CONTRIBUTING.md": (
            "100.00% mutation-score floor",
            "requirements-mutation.lock",
            "python scripts/run_mutation_testing.py --max-children 4",
            "mutmut results --all true > mutants/mutation-results.txt",
            "A timeout counts as detected",
        ),
    }
    for relative, fragments in documentation_contract.items():
        for fragment in fragments:
            if fragment not in texts[relative]:
                problems.append(
                    f"{relative}: mutation guidance must include {fragment}"
                )
    for relative in (
        "pyproject.toml",
        "requirements-mutation.lock",
        "requirements-mutation.txt",
    ):
        if (
            re.search(
                rf"(?m)^/?{re.escape(relative)}\s+export-ignore\s*$",
                texts[".gitattributes"],
            )
            is None
        ):
            problems.append(f".gitattributes: {relative} must be export-ignore")
    if "mutants/" not in {
        line.strip()
        for line in texts[".gitignore"].splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }:
        problems.append(".gitignore: mutants/ must be ignored")
    return problems


def validate_plugin_manifest(repository_root: Path) -> list[str]:
    """Validate the installed plugin manifest and its referenced skill tree."""
    repository_root = repository_root.resolve()
    path = repository_root / ".codex-plugin" / "plugin.json"
    problems: list[str] = []
    try:
        document = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        return [f".codex-plugin/plugin.json: invalid JSON: {error}"]
    if not isinstance(document, dict):
        return [".codex-plugin/plugin.json: root must be an object"]

    for field in ("name", "version", "description", "license", "skills"):
        if not nonempty_string(document.get(field)):
            problems.append(f".codex-plugin/plugin.json: {field} must be nonempty")
    if document.get("name") != "repo-scaffold":
        problems.append(".codex-plugin/plugin.json: name must be repo-scaffold")
    if document.get("license") != "MIT":
        problems.append(".codex-plugin/plugin.json: license must match repository MIT")
    version = document.get("version")
    if isinstance(version, str) and not SEMVER.fullmatch(version):
        problems.append(".codex-plugin/plugin.json: version must be valid SemVer")

    expected_repository = "https://github.com/MinhThang1009/repo-scaffold-plugin"
    if document.get("repository") != expected_repository:
        problems.append(
            ".codex-plugin/plugin.json: repository must identify the canonical "
            "GitHub source"
        )
    homepage = document.get("homepage")
    if not isinstance(homepage, str) or not homepage.startswith(
        f"{expected_repository}#"
    ):
        problems.append(
            ".codex-plugin/plugin.json: homepage must link to repository documentation"
        )
    author = document.get("author")
    if not isinstance(author, dict) or not all(
        nonempty_string(author.get(field)) for field in ("name", "url")
    ):
        problems.append(
            ".codex-plugin/plugin.json: author must include a nonempty name and URL"
        )

    interface = document.get("interface")
    required_interface_strings = (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
    )
    if not isinstance(interface, dict) or not all(
        nonempty_string(interface.get(field)) for field in required_interface_strings
    ):
        problems.append(
            ".codex-plugin/plugin.json: interface must include complete "
            "install-surface descriptions"
        )
    else:
        if interface.get("websiteURL") != expected_repository:
            problems.append(
                ".codex-plugin/plugin.json: interface.websiteURL must identify "
                "the canonical GitHub source"
            )
        expected_policy_urls = {
            "privacyPolicyURL": f"{expected_repository}/blob/main/PRIVACY.md",
            "termsOfServiceURL": f"{expected_repository}/blob/main/TERMS.md",
        }
        for field, expected_url in expected_policy_urls.items():
            if interface.get(field) != expected_url:
                problems.append(
                    f".codex-plugin/plugin.json: interface.{field} must link to "
                    "the canonical repository policy"
                )
        capabilities = interface.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or any(not nonempty_string(value) for value in capabilities)
        ):
            problems.append(
                ".codex-plugin/plugin.json: interface.capabilities must be a "
                "nonempty string array"
            )
        prompts = interface.get("defaultPrompt")
        if (
            not isinstance(prompts, list)
            or not 1 <= len(prompts) <= 3
            or any(
                not nonempty_string(prompt) or len(prompt) > 128 for prompt in prompts
            )
        ):
            problems.append(
                ".codex-plugin/plugin.json: interface.defaultPrompt must contain "
                "one to three nonempty prompts of at most 128 characters"
            )

    skills_value = document.get("skills")
    if isinstance(skills_value, str) and skills_value.strip():
        if not skills_value.startswith("./"):
            problems.append(".codex-plugin/plugin.json: skills path must start with ./")
        skills_path = (repository_root / skills_value).resolve()
        try:
            skills_path.relative_to(repository_root)
        except ValueError:
            problems.append(
                ".codex-plugin/plugin.json: skills must stay inside the repository"
            )
        else:
            if not skills_path.is_dir():
                problems.append(
                    ".codex-plugin/plugin.json: skills must reference a directory"
                )
            elif not any(skills_path.rglob("SKILL.md")):
                problems.append(
                    ".codex-plugin/plugin.json: skills contains no SKILL.md"
                )
            else:
                for skill_path in sorted(skills_path.rglob("SKILL.md")):
                    relative = skill_path.relative_to(repository_root).as_posix()
                    try:
                        metadata, _body = read_front_matter(skill_path)
                    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
                        problems.append(f"{relative}: invalid skill metadata: {error}")
                        continue
                    description = (
                        metadata.get("description")
                        if isinstance(metadata, dict)
                        else None
                    )
                    if (
                        not isinstance(metadata, dict)
                        or not nonempty_string(metadata.get("name"))
                        or not nonempty_string(description)
                    ):
                        problems.append(
                            f"{relative}: skill metadata must include nonempty name "
                            "and description"
                        )
                    elif isinstance(description, str) and len(description) > 400:
                        problems.append(
                            f"{relative}: skill description must stay concise "
                            "(400 characters or fewer)"
                        )
    return problems


def validate_release_please(repository_root: Path) -> list[str]:
    """Validate the repository's single automated release mode and version state."""
    workflow_root = repository_root / ".github" / "workflows"
    workflow_path = workflow_root / "release-please.yml"
    manual_dispatcher = workflow_root / "release-tag.yml"
    config_path = repository_root / "release-please-config.json"
    versions_path = repository_root / ".release-please-manifest.json"
    plugin_path = repository_root / ".codex-plugin" / "plugin.json"
    version_path = repository_root / "version.txt"
    problems: list[str] = []

    if not workflow_path.is_file():
        problems.append(".github/workflows/release-please.yml: missing")
    if manual_dispatcher.exists():
        problems.append(
            ".github/workflows/release-tag.yml: must not coexist with Release Please"
        )

    try:
        config = load_json(config_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        return [f"release-please-config.json: invalid JSON: {error}", *problems]
    if not isinstance(config, dict):
        return ["release-please-config.json: root must be an object", *problems]
    if config.get("release-type") != "simple":
        problems.append("release-please-config.json: release-type must be simple")
    if config.get("draft") is not True:
        problems.append("release-please-config.json: draft must be true")
    if config.get("force-tag-creation") is not True:
        problems.append("release-please-config.json: force-tag-creation must be true")
    for field, expected in RELEASE_PLEASE_ENGLISH_TEXT.items():
        if config.get(field) != expected:
            problems.append(
                f"release-please-config.json: {field} must use the approved "
                "English release text"
            )
    if config.get("changelog-sections") != RELEASE_PLEASE_ENGLISH_CHANGELOG_SECTIONS:
        problems.append(
            "release-please-config.json: changelog-sections must preserve the "
            "approved English headings and default visibility"
        )
    packages = config.get("packages")
    root_package = packages.get(".") if isinstance(packages, dict) else None
    if not isinstance(root_package, dict):
        problems.append("release-please-config.json: packages must define root package")
    else:
        required_extra_file = {
            "type": "json",
            "path": ".codex-plugin/plugin.json",
            "jsonpath": "$.version",
        }
        extra_files = root_package.get("extra-files")
        if not isinstance(extra_files, list) or required_extra_file not in extra_files:
            problems.append(
                "release-please-config.json: root package must update plugin version"
            )

    versions: dict[str, Any] = {}
    try:
        manifest = load_json(versions_path)
        if isinstance(manifest, dict) and set(manifest) == {"."}:
            versions[".release-please-manifest.json"] = manifest["."]
        else:
            problems.append(
                ".release-please-manifest.json: must contain only the root package"
            )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        problems.append(f".release-please-manifest.json: invalid JSON: {error}")
    try:
        plugin = load_json(plugin_path)
        if isinstance(plugin, dict):
            versions[".codex-plugin/plugin.json"] = plugin.get("version")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        problems.append(f".codex-plugin/plugin.json: invalid JSON: {error}")
    try:
        versions["version.txt"] = version_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        problems.append(f"version.txt: unreadable: {error}")

    invalid_versions = [
        version_source
        for version_source, version in versions.items()
        if not isinstance(version, str) or not SEMVER.fullmatch(version)
    ]
    for version_source in invalid_versions:
        problems.append(f"{version_source}: release version must be valid SemVer")
    for version_source, version in versions.items():
        if version_source in invalid_versions:
            continue
        build_metadata = version.partition("+")[2]
        if build_metadata.startswith("codex."):
            problems.append(
                f"{version_source}: public release version must not use a local "
                "Codex cachebuster"
            )
    valid_versions = {
        version
        for version_source, version in versions.items()
        if version_source not in invalid_versions
    }
    if len(versions) == 3 and len(valid_versions) > 1:
        problems.append("release version files must contain the same version")

    for workflow_file in sorted(workflow_root.glob("*.yml")):
        try:
            document = load_yaml(workflow_file)
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if not isinstance(document, dict):
            continue
        triggers = document.get("on")
        if not isinstance(triggers, dict):
            continue
        push = triggers.get("push")
        if isinstance(push, dict) and "tags" in push:
            problems.append(
                f"{workflow_file.relative_to(repository_root).as_posix()}: tag push "
                "trigger conflicts "
                "with Release Please"
            )
    return problems


def validate_release_attestation(repository_root: Path) -> list[str]:
    """Validate provenance isolation and reusable-workflow permission flow."""
    engine_paths = (
        repository_root / ".github" / "workflows" / "release.yml",
        repository_root
        / "skills"
        / "repo-scaffold"
        / "assets"
        / "workflows"
        / "release.yml",
    )
    caller_jobs = (
        (
            repository_root / ".github" / "workflows" / "release-please.yml",
            "publish_release",
        ),
        (
            repository_root
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "release-please.yml",
            "publish_release",
        ),
        (
            repository_root
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "release-tag.yml",
            "release",
        ),
    )
    caller_permissions = {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    attest_permissions = {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    problems: list[str] = []

    for path in engine_paths:
        relative = path.relative_to(repository_root).as_posix()
        try:
            document = load_yaml(path)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: release engine is unreadable: {error}")
            continue
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, dict):
            problems.append(f"{relative}: jobs must be a mapping")
            continue

        build = jobs.get("build")
        attest = jobs.get("attest")
        publish = jobs.get("publish")
        if not isinstance(build, dict):
            problems.append(f"{relative}: build job is missing")
        else:
            if build.get("permissions") != {"contents": "read"}:
                problems.append(f"{relative}: build permissions must be contents: read")
            build_steps = build.get("steps")
            archive_steps = (
                [
                    step
                    for step in build_steps
                    if isinstance(step, dict)
                    and step.get("name") in {"Build artifact", "Build plugin archive"}
                ]
                if isinstance(build_steps, list)
                else []
            )
            if (
                len(archive_steps) != 1
                or not isinstance(archive_steps[0].get("run"), str)
                or "git archive" not in archive_steps[0]["run"]
                or "--worktree-attributes" not in archive_steps[0]["run"]
            ):
                problems.append(
                    f"{relative}: archive build must use git archive with "
                    "--worktree-attributes"
                )

        if not isinstance(attest, dict):
            problems.append(f"{relative}: attest job is missing")
        else:
            if attest.get("needs") != "build":
                problems.append(f"{relative}: attest must depend only on build")
            if attest.get("runs-on") != "ubuntu-latest":
                problems.append(f"{relative}: attest must run on ubuntu-latest")
            if attest.get("timeout-minutes") != "15":
                problems.append(f"{relative}: attest timeout must be 15 minutes")
            if attest.get("permissions") != attest_permissions:
                problems.append(
                    f"{relative}: attest permissions must be contents: read, "
                    "id-token: write, and attestations: write"
                )

            steps = attest.get("steps")
            if (
                not isinstance(steps, list)
                or len(steps) != 3
                or not all(isinstance(step, dict) for step in steps)
            ):
                problems.append(
                    f"{relative}: attest must contain exactly receive, validate, "
                    "and attest steps"
                )
            else:
                receive, validate, generate = steps
                receive_ref = receive.get("uses")
                if not isinstance(receive_ref, str) or not re.fullmatch(
                    r"actions/download-artifact@[0-9a-f]{40}", receive_ref
                ):
                    problems.append(
                        f"{relative}: attest receive step must use a full-SHA "
                        "actions/download-artifact pin"
                    )
                if receive.get("with") != {
                    "name": "release-assets-${{ inputs.commit_sha }}",
                    "path": "dist/",
                }:
                    problems.append(
                        f"{relative}: attest must download the build artifact to dist/"
                    )
                if (
                    validate.get("name") != "Validate downloaded artifacts"
                    or validate.get("shell") != "bash"
                    or not isinstance(validate.get("run"), str)
                    or validate["run"].strip() != ATTESTATION_VALIDATION_SCRIPT.strip()
                ):
                    problems.append(
                        f"{relative}: attest validation step must match the "
                        "non-executing artifact safety contract"
                    )
                attest_ref = generate.get("uses")
                if not isinstance(attest_ref, str) or not re.fullmatch(
                    r"actions/attest@[0-9a-f]{40}", attest_ref
                ):
                    problems.append(
                        f"{relative}: provenance step must use a full-SHA "
                        "actions/attest pin"
                    )
                if generate.get("with") != {"subject-path": "dist/**"}:
                    problems.append(
                        f"{relative}: provenance subjects must cover dist/** only"
                    )

        if not isinstance(publish, dict):
            problems.append(f"{relative}: publish job is missing")
        else:
            if publish.get("needs") != ["build", "attest"]:
                problems.append(f"{relative}: publish must depend on build and attest")
            if publish.get("permissions") != {"contents": "write"}:
                problems.append(
                    f"{relative}: publish permissions must be contents: write"
                )

    for path, job_name in caller_jobs:
        relative = path.relative_to(repository_root).as_posix()
        try:
            document = load_yaml(path)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: release caller is unreadable: {error}")
            continue
        jobs = document.get("jobs") if isinstance(document, dict) else None
        job = jobs.get(job_name) if isinstance(jobs, dict) else None
        if not isinstance(job, dict):
            problems.append(f"{relative}: {job_name} caller job is missing")
        else:
            effective_permissions = job.get("permissions", document.get("permissions"))
            if effective_permissions == caller_permissions:
                continue
            problems.append(
                f"{relative}: {job_name} must pass contents: write, id-token: "
                "write, and attestations: write to the reusable release engine"
            )
    return problems


def validate_privileged_workflow_permissions(repository_root: Path) -> list[str]:
    """Keep write permissions isolated to the jobs that require them."""
    workflow_contracts = (
        (
            repository_root / ".github" / "workflows" / "release-please.yml",
            {"contents": "read"},
            "release_please",
            None,
        ),
        (
            repository_root
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "release-please.yml",
            {"contents": "read"},
            "release_please",
            None,
        ),
        (
            repository_root / ".github" / "workflows" / "codeql.yml",
            {"actions": "read", "contents": "read", "packages": "read"},
            "analyze",
            {
                "actions": "read",
                "contents": "read",
                "packages": "read",
                "security-events": "write",
            },
        ),
        (
            repository_root
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "codeql.yml",
            {"actions": "read", "contents": "read", "packages": "read"},
            "analyze",
            {
                "actions": "read",
                "contents": "read",
                "packages": "read",
                "security-events": "write",
            },
        ),
    )
    problems: list[str] = []
    for path, workflow_permissions, job_name, job_permissions in workflow_contracts:
        relative = path.relative_to(repository_root).as_posix()
        try:
            document = load_yaml(path)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: permission contract is unreadable: {error}")
            continue
        if not isinstance(document, dict):
            problems.append(f"{relative}: root must be a mapping")
            continue
        if document.get("permissions") != workflow_permissions:
            problems.append(f"{relative}: top-level permissions must be read-only")
        jobs = document.get("jobs")
        job = jobs.get(job_name) if isinstance(jobs, dict) else None
        if not isinstance(job, dict):
            problems.append(f"{relative}: {job_name} job is missing")
        elif job.get("permissions") != job_permissions:
            expectation = (
                "inherit read-only permissions"
                if job_permissions is None
                else "isolate security-events: write"
            )
            problems.append(f"{relative}: {job_name} must {expectation}")
    return problems


def read_front_matter(path: Path) -> tuple[Any, str]:
    """Return parsed YAML front matter and the remaining Markdown body."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing opening YAML front matter delimiter")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("missing closing YAML front matter delimiter") from error
    front_matter = "\n".join(lines[1:closing])
    return yaml.load(front_matter, Loader=UniqueKeyBaseLoader), "\n".join(
        lines[closing + 1 :]
    )


def validate_issue_form_body(relative: Path, form_body: list[Any]) -> list[str]:
    """Validate current GitHub issue-form body types and core constraints."""
    problems: list[str] = []
    seen_ids: set[str] = set()
    has_input = False
    for index, item in enumerate(form_body):
        prefix = f"{relative}: body[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{prefix} must be a mapping")
            continue
        item_type = item.get("type")
        if item_type not in ISSUE_FORM_INPUT_TYPES:
            problems.append(f"{prefix} has invalid type")
            continue
        if item_type != "markdown":
            has_input = True

        item_id = item.get("id")
        if item_id is not None:
            if not isinstance(item_id, str) or not ISSUE_FORM_ID.fullmatch(item_id):
                problems.append(
                    f"{prefix}.id may contain only letters, numbers, -, and _"
                )
            elif item_id in seen_ids:
                problems.append(f"{prefix}.id must be unique")
            else:
                seen_ids.add(item_id)

        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            problems.append(f"{prefix}.attributes must be a mapping")
            continue
        required_attribute = "value" if item_type == "markdown" else "label"
        if not nonempty_string(attributes.get(required_attribute)):
            problems.append(
                f"{prefix}.attributes.{required_attribute} must be nonempty"
            )

        if item_type == "dropdown":
            options = attributes.get("options")
            if (
                not isinstance(options, list)
                or not options
                or any(not nonempty_string(option) for option in options)
            ):
                problems.append(
                    f"{prefix}.attributes.options must be a nonempty string list"
                )
            elif len(set(options)) != len(options):
                problems.append(f"{prefix}.attributes.options must be unique")
        elif item_type == "checkboxes":
            options = attributes.get("options")
            if not isinstance(options, list) or not options:
                problems.append(f"{prefix}.attributes.options must be a nonempty list")
            else:
                labels: list[str] = []
                for option_index, option in enumerate(options):
                    if not isinstance(option, dict) or not nonempty_string(
                        option.get("label")
                    ):
                        problems.append(
                            f"{prefix}.attributes.options[{option_index}].label "
                            "must be nonempty"
                        )
                        continue
                    labels.append(option["label"])
                    required = option.get("required")
                    if required is not None and required not in {"true", "false"}:
                        problems.append(
                            f"{prefix}.attributes.options[{option_index}].required "
                            "must be a boolean"
                        )
                if len(set(labels)) != len(labels):
                    problems.append(
                        f"{prefix}.attributes.options labels must be unique"
                    )

        validations = item.get("validations")
        if validations is not None:
            if not isinstance(validations, dict):
                problems.append(f"{prefix}.validations must be a mapping")
            else:
                required = validations.get("required")
                if required is not None and required not in {"true", "false"}:
                    problems.append(f"{prefix}.validations.required must be a boolean")
                accept = validations.get("accept")
                if item_type == "upload" and accept is not None:
                    if not nonempty_string(accept):
                        problems.append(f"{prefix}.validations.accept must be nonempty")

    if not has_input:
        problems.append(f"{relative}: body must contain a non-markdown input")
    return problems


def validate_issue_templates(repository_root: Path) -> list[str]:
    """Validate issue template front matter, forms, and chooser configuration."""
    problems: list[str] = []
    template_roots = (
        repository_root / ".github" / "ISSUE_TEMPLATE",
        repository_root / "skills" / "repo-scaffold" / "assets" / "ISSUE_TEMPLATE",
    )
    for template_root in template_roots:
        for path in sorted(template_root.glob("*.md")):
            relative = path.relative_to(repository_root)
            try:
                front_matter, template_body = read_front_matter(path)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
                problems.append(f"{relative}: invalid front matter: {error}")
                continue
            if not isinstance(front_matter, dict):
                problems.append(f"{relative}: front matter must be a mapping")
                continue
            for field in ("name", "about"):
                if not nonempty_string(front_matter.get(field)):
                    problems.append(f"{relative}: {field} must be nonempty")
            name = front_matter.get("name")
            if isinstance(name, str) and len(name.strip()) <= 3:
                problems.append(f"{relative}: name must be more than 3 characters")
            if not template_body.strip():
                problems.append(f"{relative}: template body must be nonempty")

        for path in sorted(template_root.glob("*.yaml")):
            relative = path.relative_to(repository_root)
            problems.append(f"{relative}: issue forms must use the .yml extension")

        for path in sorted(template_root.glob("*.yml")):
            if path.name == "config.yml":
                continue
            relative = path.relative_to(repository_root)
            try:
                document = load_yaml(path)
            except (OSError, UnicodeError, yaml.YAMLError) as error:
                problems.append(f"{relative}: invalid issue form YAML: {error}")
                continue
            if not isinstance(document, dict):
                problems.append(f"{relative}: issue form root must be a mapping")
                continue
            for field in ("name", "description"):
                if not nonempty_string(document.get(field)):
                    problems.append(f"{relative}: {field} must be nonempty")
            name = document.get("name")
            if isinstance(name, str) and len(name.strip()) <= 3:
                problems.append(f"{relative}: name must be more than 3 characters")
            form_body = document.get("body")
            if not isinstance(form_body, list) or not form_body:
                problems.append(f"{relative}: body must be a nonempty list")
                continue
            problems.extend(validate_issue_form_body(relative, form_body))

        config_path = template_root / "config.yml"
        if not config_path.is_file():
            continue
        relative = config_path.relative_to(repository_root)
        try:
            document = load_yaml(config_path)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: invalid chooser YAML: {error}")
            continue
        if not isinstance(document, dict):
            problems.append(f"{relative}: root must be a mapping")
            continue
        if document.get("blank_issues_enabled") not in {"true", "false"}:
            problems.append(f"{relative}: blank_issues_enabled must be a boolean")
        contact_links = document.get("contact_links", [])
        if not isinstance(contact_links, list):
            problems.append(f"{relative}: contact_links must be a list")
            continue
        for index, contact in enumerate(contact_links):
            if not isinstance(contact, dict):
                problems.append(f"{relative}: contact_links[{index}] must be a mapping")
                continue
            for field in ("name", "url", "about"):
                if not nonempty_string(contact.get(field)):
                    problems.append(
                        f"{relative}: contact_links[{index}].{field} must be nonempty"
                    )
            url = contact.get("url")
            if (
                isinstance(url, str)
                and not TEMPLATE_TOKEN.search(url)
                and urlsplit(url).scheme != "https"
            ):
                problems.append(
                    f"{relative}: contact_links[{index}].url must use HTTPS"
                )
    return problems


def validate_dependabot(repository_root: Path) -> list[str]:
    """Validate installed and templated Dependabot configuration."""
    problems: list[str] = []
    paths = (
        repository_root / ".github" / "dependabot.yml",
        repository_root / "skills" / "repo-scaffold" / "assets" / "dependabot.yml",
    )
    allowed_intervals = {
        "daily",
        "weekly",
        "monthly",
        "quarterly",
        "semiannually",
        "yearly",
    }
    for path in paths:
        relative = path.relative_to(repository_root)
        try:
            document = load_yaml(path)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: invalid Dependabot YAML: {error}")
            continue
        if not isinstance(document, dict):
            problems.append(f"{relative}: root must be a mapping")
            continue
        if document.get("version") != "2":
            problems.append(f"{relative}: version must be 2")
        updates = document.get("updates")
        if not isinstance(updates, list) or not updates:
            problems.append(f"{relative}: updates must be a nonempty list")
            continue
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                problems.append(f"{relative}: updates[{index}] must be a mapping")
                continue
            for field in ("package-ecosystem", "directory"):
                if not nonempty_string(update.get(field)):
                    problems.append(f"{relative}: updates[{index}].{field} is required")
            schedule = update.get("schedule")
            if not isinstance(schedule, dict):
                problems.append(f"{relative}: updates[{index}].schedule is required")
            elif schedule.get("interval") not in allowed_intervals:
                problems.append(
                    f"{relative}: updates[{index}].schedule.interval is invalid"
                )
    return problems


OPAQUE_HTML_BLOCK = re.compile(
    r"(?is)^\s*(?:<!--|<\?|<![A-Z]|<!\[CDATA\[|"
    r"<(?:pre|script|style|textarea)(?:[ \t>]|$))"
)


def _source_line_offsets(text: str) -> list[int]:
    """Return source offsets for CommonMark's zero-based line maps."""
    return [
        0,
        *(match.end() for match in re.finditer(r"\r\n|\r|\n", text)),
        len(text),
    ]


def _blank_commonmark_blocks(text: str, token_types: frozenset[str]) -> str:
    """Blank selected CommonMark block tokens while preserving source layout."""
    output = list(text)
    offsets = _source_line_offsets(text)
    for token in COMMONMARK.parse(text):
        if token.type not in token_types or token.map is None:
            continue
        if token.type == "html_block" and not OPAQUE_HTML_BLOCK.match(token.content):
            continue
        start_line, end_line = token.map
        start = offsets[start_line]
        end = offsets[end_line]
        for position in range(start, end):
            if output[position] not in "\r\n":
                output[position] = " "
    return "".join(output)


def _without_root_indented_code(text: str) -> str:
    """Blank CommonMark indented code blocks."""
    return _blank_commonmark_blocks(text, frozenset({"code_block"}))


def _without_markdown_block_code(text: str) -> str:
    """Blank CommonMark block constructs whose contents are not Markdown."""
    return _blank_commonmark_blocks(
        text, frozenset({"code_block", "fence", "html_block"})
    )


CODE_SPANS_ENV_KEY = "repo_scaffold_code_spans"


def _record_code_span(state: StateInline, silent: bool) -> bool:
    """Record source offsets whenever markdown-it parses a code span."""
    opening = state.pos
    token_count = len(state.tokens)
    matched = parse_backtick(state, silent)
    if len(state.tokens) > token_count:
        state.env.setdefault(CODE_SPANS_ENV_KEY, []).append((opening, state.pos))
    return matched


CODE_SPAN_COMMONMARK = MarkdownIt("commonmark")
CODE_SPAN_COMMONMARK.inline.ruler.at("backticks", _record_code_span)


def _without_inline_code(text: str) -> str:
    """Blank code spans confirmed by the CommonMark parser."""
    output = list(text)
    offsets = _source_line_offsets(text)
    for token in COMMONMARK.parse(text):
        if token.type != "inline" or token.map is None:
            continue
        start_line, end_line = token.map
        block_start = offsets[start_line]
        block_end = offsets[end_line]
        environment: dict[str, Any] = {}
        CODE_SPAN_COMMONMARK.parseInline(text[block_start:block_end], environment)
        spans: list[tuple[int, int]] = environment.get(CODE_SPANS_ENV_KEY, [])
        for opening, closing in spans:
            for position in range(block_start + opening, block_start + closing):
                if output[position] not in "\r\n":
                    output[position] = " "
    return "".join(output)


def without_fenced_code(text: str) -> str:
    """Blank all CommonMark code and opaque HTML constructs."""
    return _without_inline_code(_without_markdown_block_code(text))


def _parse_commonmark(text: str) -> tuple[list[Token], dict[str, Any]]:
    """Parse Markdown with the CommonMark reference implementation port."""
    environment: dict[str, Any] = {}
    return COMMONMARK.parse(text, environment), environment


def _walk_markdown_tokens(tokens: Iterable[Token]) -> Iterable[Token]:
    """Yield block and nested inline tokens in source order."""
    for token in tokens:
        yield token
        if token.children:
            yield from _walk_markdown_tokens(token.children)


def _links_from_tokens(tokens: Iterable[Token]) -> list[tuple[bool, str]]:
    """Return normalized non-autolink destinations from parsed tokens."""
    links: list[tuple[bool, str]] = []
    for token in _walk_markdown_tokens(tokens):
        if token.type == "image":
            links.append((True, str(token.attrGet("src") or "")))
        elif token.type == "link_open" and token.markup != "autolink":
            links.append((False, str(token.attrGet("href") or "")))
    return links


def inline_markdown_links(text: str) -> list[tuple[bool, str]]:
    """Return CommonMark inline links and images as normalized destinations."""
    tokens, _environment = _parse_commonmark(text)
    return _links_from_tokens(tokens)


def inline_markdown_link_payloads(text: str) -> list[str]:
    """Return normalized destinations for CommonMark inline links and images."""
    return [destination for _is_image, destination in inline_markdown_links(text)]


def markdown_link_destinations(text: str) -> list[str]:
    """Return unique inline and reference-definition CommonMark destinations."""
    tokens, environment = _parse_commonmark(text)
    destinations = [
        destination for _is_image, destination in _links_from_tokens(tokens)
    ]
    references: dict[str, dict[str, str]] = environment.get("references", {})
    destinations.extend(reference["href"] for reference in references.values())
    return list(dict.fromkeys(destinations))


def validate_markdown_links(repository_root: Path) -> list[str]:
    """Validate that first-party relative Markdown links stay inside and exist."""
    problems: list[str] = []
    resolved_root = repository_root.resolve()
    for path in project_files(repository_root, ("*.md",)):
        relative = path.relative_to(repository_root)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            problems.append(f"{relative}: could not read Markdown: {error}")
            continue
        for destination in markdown_link_destinations(text):
            decoded_destination = unquote(destination)
            if (
                not destination
                or destination.startswith(("#", "/", "//"))
                or TEMPLATE_TOKEN.search(decoded_destination)
                or urlsplit(destination).scheme
            ):
                continue
            parsed = urlsplit(destination)
            decoded_path = unquote(parsed.path)
            if "\x00" in decoded_path:
                problems.append(
                    f"{relative}: relative link has an invalid path: {destination}"
                )
                continue
            try:
                target = (path.parent / decoded_path).resolve()
            except (OSError, RuntimeError, ValueError):
                problems.append(
                    f"{relative}: relative link has an invalid path: {destination}"
                )
                continue
            try:
                target.relative_to(resolved_root)
            except ValueError:
                problems.append(f"{relative}: link escapes repository: {destination}")
                continue
            try:
                target_exists = target.exists()
            except (OSError, ValueError):
                problems.append(
                    f"{relative}: relative link has an invalid path: {destination}"
                )
                continue
            if not target_exists:
                problems.append(f"{relative}: relative link is missing: {destination}")
    return problems


def validate_scaffold_contract(repository_root: Path) -> list[str]:
    """Run the distributable rendered-document contract against this plugin."""
    script = (
        repository_root
        / "skills"
        / "repo-scaffold"
        / "scripts"
        / "validate_scaffold.py"
    )
    template_root = repository_root / "skills" / "repo-scaffold" / "assets"
    if not script.is_file():
        return [
            "scaffold contract: skills/repo-scaffold/scripts/validate_scaffold.py is missing"
        ]
    command = [
        sys.executable,
        str(script),
        "--repository-root",
        str(repository_root),
        "--template-root",
        str(template_root),
    ]
    try:
        result = subprocess.run(  # noqa: S603 - interpreter and script are explicit
            command,
            cwd=repository_root,
            env=child_process_environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return ["scaffold contract: validation timed out"]
    if result.returncode == 0:
        return []
    detail = result.stderr.strip() or result.stdout.strip() or "validation failed"
    return [f"scaffold contract: {line}" for line in detail.splitlines()]


def release_archive_source_root(repository_root: Path) -> Path:
    """Use the tracked source worktree for a generated mutmut workspace."""
    raw_source_root = os.environ.get("REPO_SCAFFOLD_MUTATION_SOURCE_ROOT")
    if not raw_source_root:
        return repository_root
    try:
        source_root = Path(raw_source_root).resolve(strict=True)
        generated_root = repository_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return repository_root
    if (
        generated_root.parent == source_root
        and generated_root.name == "mutants"
        and (source_root / ".git").exists()
    ):
        return source_root
    return repository_root


def validate_release_archive(repository_root: Path) -> list[str]:
    """Build and inspect the exact archive shape used by the release workflow."""
    source_root = release_archive_source_root(repository_root)
    git = resolve_path_executable("git", forbidden_root=source_root)
    if git is None:
        return ["release archive: git is unavailable outside the repository"]
    archive_paths = (".codex-plugin", "skills", "README.md", "LICENSE")
    with tempfile.TemporaryDirectory(prefix="repo-scaffold-archive-") as directory:
        archive = Path(directory) / "plugin.zip"
        command = [
            git,
            "archive",
            "--worktree-attributes",
            "--format=zip",
            "--prefix=repo-scaffold/",
            "--output",
            str(archive),
            "HEAD",
            "--",
            *archive_paths,
        ]
        try:
            result = subprocess.run(  # noqa: S603 - executable is resolved safely
                command,
                cwd=source_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return ["release archive: git archive timed out"]
        if result.returncode != 0:
            detail = result.stderr.strip() or "git archive failed"
            return [f"release archive: {detail}"]

        problems: list[str] = []
        try:
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                for item in bundle.infolist():
                    path = PurePosixPath(item.filename)
                    if (
                        not path.parts
                        or path.parts[0] != "repo-scaffold"
                        or path.is_absolute()
                        or ".." in path.parts
                    ):
                        problems.append(
                            f"release archive: unsafe member {item.filename!r}"
                        )
                    mode = item.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        problems.append(
                            f"release archive: symbolic link {item.filename!r}"
                        )
        except (OSError, zipfile.BadZipFile) as error:
            return [f"release archive: invalid ZIP: {error}"]

        source_command = [
            git,
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            "HEAD",
            "--",
            *archive_paths,
        ]
        try:
            source_result = subprocess.run(  # noqa: S603 - executable is resolved safely
                source_command,
                cwd=source_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return ["release archive: source enumeration timed out"]
        if source_result.returncode != 0:
            detail = source_result.stderr.strip() or "git ls-tree failed"
            return [f"release archive: source enumeration failed: {detail}"]

        expected = {
            f"repo-scaffold/{relative}"
            for relative in source_result.stdout.split("\0")
            if relative
        }
        expected.update(
            {
                "repo-scaffold/.codex-plugin/plugin.json",
                "repo-scaffold/README.md",
                "repo-scaffold/LICENSE",
                "repo-scaffold/skills/repo-scaffold/SKILL.md",
                "repo-scaffold/skills/repo-scaffold/assets/.editorconfig",
                "repo-scaffold/skills/repo-scaffold/assets/gitattributes.template",
                "repo-scaffold/skills/repo-scaffold/scripts/ci_toolchain.py",
                "repo-scaffold/skills/repo-scaffold/scripts/codeql_preflight.py",
                "repo-scaffold/skills/repo-scaffold/scripts/validate_scaffold.py",
            }
        )
        for missing in sorted(expected - names):
            problems.append(f"release archive: missing {missing}")
        return problems


def validate_test_quality_contract(repository_root: Path) -> list[str]:
    """Reject structurally weak or duplicated test cases."""
    test_root = repository_root / "tests"
    try:
        paths = sorted(test_root.glob("test_*.py"))
    except OSError as error:
        return [f"test quality: could not inventory tests: {error}"]
    if not paths:
        return ["test quality: no test_*.py files found"]

    problems: list[str] = []
    bodies: dict[str, str] = {}
    test_count = 0
    weak_only = {"assertIsInstance", "assertIsNotNone"}
    for path in paths:
        relative = path.relative_to(repository_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, UnicodeError, SyntaxError) as error:
            problems.append(f"{relative}: could not inspect test quality: {error}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not (
                node.name.startswith("test_")
            ):
                continue
            test_count += 1
            location = f"{relative}:{node.lineno}:{node.name}"
            assertions: list[str] = []
            for child in ast.walk(node):
                if isinstance(child, ast.Assert):
                    assertions.append("assert")
                elif (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr.startswith("assert")
                ):
                    assertions.append(child.func.attr)
            if not assertions:
                problems.append(f"{location}: test has no assertion")
            elif set(assertions) <= weak_only:
                problems.append(
                    f"{location}: test only checks type or non-null presence"
                )

            body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            previous = bodies.get(body)
            if previous is not None:
                problems.append(f"{location}: duplicates test body at {previous}")
            else:
                bodies[body] = location
    if test_count == 0:
        problems.append("test quality: no test functions found")
    return problems


def validate_repository(repository_root: Path) -> list[str]:
    """Run every deterministic repository validation."""
    validators = (
        validate_serialized_files,
        validate_action_references,
        validate_python_support_contract,
        validate_ci_toolchain_contract,
        validate_mirrored_dependency_metadata,
        validate_development_dependency_contract,
        validate_mutation_testing_contract,
        validate_plugin_manifest,
        validate_release_please,
        validate_release_attestation,
        validate_privileged_workflow_permissions,
        validate_issue_templates,
        validate_dependabot,
        validate_markdown_links,
        validate_test_quality_contract,
        validate_scaffold_contract,
        validate_release_archive,
    )
    problems: list[str] = []
    for validator in validators:
        problems.extend(validator(repository_root))
    return problems


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    problems = validate_repository(repository_root)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print(
        "Repository metadata, action pins, dependency locks, coverage policy, "
        "CI policies, mutation testing, test quality, links, templates, "
        "attestations, and release archive are valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
