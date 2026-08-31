#!/usr/bin/env python3
"""Validate Python support policy and emit its deterministic CI matrix."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POLICY_FIELDS = {
    "schema-version",
    "implementation",
    "versions",
    "full-coverage-os",
    "boundary-coverage-os",
}
MAX_POLICY_BYTES = 64 * 1024
MAX_SUPPORTED_VERSIONS = 32
PYTHON_MINOR = re.compile(r"^3\.(0|[1-9]\d*)$")
GITHUB_HOSTED_RUNNERS = frozenset({"ubuntu-latest", "windows-latest", "macos-latest"})


class PolicyError(ValueError):
    """Raised when the Python support policy is ambiguous or invalid."""


@dataclass(frozen=True)
class PythonSupportPolicy:
    """Normalized Python support and operating-system coverage policy."""

    versions: tuple[str, ...]
    full_coverage_os: tuple[str, ...]
    boundary_coverage_os: tuple[str, ...]

    @property
    def latest(self) -> str:
        """Return the latest explicitly supported Python feature release."""
        return self.versions[-1]


def reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate member names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def require_string_list(
    document: dict[str, Any], field: str, *, max_items: int
) -> tuple[str, ...]:
    """Return a nonempty list of unique, nonempty strings."""
    value = document.get(field)
    if not isinstance(value, list) or not value:
        raise PolicyError(f"{field} must be a nonempty array")
    if len(value) > max_items:
        raise PolicyError(f"{field} must not contain more than {max_items} entries")
    if not all(isinstance(item, str) and item for item in value):
        raise PolicyError(f"{field} must contain only nonempty strings")
    if len(value) != len(set(value)):
        raise PolicyError(f"{field} must not contain duplicates")
    return tuple(value)


def parse_policy(document: Any) -> PythonSupportPolicy:
    """Validate and normalize a decoded policy document."""
    if not isinstance(document, dict):
        raise PolicyError("policy root must be an object")
    unexpected = sorted(set(document) - POLICY_FIELDS)
    missing = sorted(POLICY_FIELDS - set(document))
    if unexpected:
        raise PolicyError(f"unknown fields: {', '.join(unexpected)}")
    if missing:
        raise PolicyError(f"missing fields: {', '.join(missing)}")
    if type(document["schema-version"]) is not int or document["schema-version"] != 1:
        raise PolicyError("schema-version must be the integer 1")
    if document["implementation"] != "cpython":
        raise PolicyError("implementation must be cpython")

    versions = require_string_list(
        document, "versions", max_items=MAX_SUPPORTED_VERSIONS
    )
    parsed_versions: list[tuple[int, int]] = []
    for version in versions:
        match = PYTHON_MINOR.fullmatch(version)
        if match is None:
            raise PolicyError(
                "versions must use stable CPython feature-release syntax such as 3.14"
            )
        parsed_versions.append((3, int(match.group(1))))
    expected_versions = [
        (3, minor) for minor in range(parsed_versions[0][1], parsed_versions[-1][1] + 1)
    ]
    if parsed_versions != expected_versions:
        raise PolicyError("versions must be ordered, contiguous, and gap-free")

    full_coverage_os = require_string_list(
        document, "full-coverage-os", max_items=len(GITHUB_HOSTED_RUNNERS)
    )
    boundary_coverage_os = require_string_list(
        document, "boundary-coverage-os", max_items=len(GITHUB_HOSTED_RUNNERS)
    )
    for runner in (*full_coverage_os, *boundary_coverage_os):
        if runner not in GITHUB_HOSTED_RUNNERS:
            allowed = ", ".join(sorted(GITHUB_HOSTED_RUNNERS))
            raise PolicyError(
                f"unsupported GitHub-hosted runner label {runner!r}; "
                f"allowed labels: {allowed}"
            )
    overlap = sorted(set(full_coverage_os) & set(boundary_coverage_os))
    if overlap:
        raise PolicyError(
            "full-coverage-os and boundary-coverage-os must not overlap: "
            + ", ".join(overlap)
        )
    return PythonSupportPolicy(
        versions=versions,
        full_coverage_os=full_coverage_os,
        boundary_coverage_os=boundary_coverage_os,
    )


def load_policy(path: Path) -> PythonSupportPolicy:
    """Read a UTF-8 JSON policy with duplicate-member rejection."""
    try:
        with path.open("rb") as policy_file:
            payload = policy_file.read(MAX_POLICY_BYTES + 1)
        if len(payload) > MAX_POLICY_BYTES:
            raise PolicyError(f"policy exceeds the {MAX_POLICY_BYTES}-byte size limit")
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_json_pairs,
        )
    except PolicyError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise PolicyError(f"could not read {path}: {error}") from error
    return parse_policy(document)


def build_matrix(policy: PythonSupportPolicy) -> dict[str, list[dict[str, str]]]:
    """Build full primary-OS coverage plus boundary-only secondary coverage."""
    include = [
        {"os": runner, "python-version": version}
        for runner in policy.full_coverage_os
        for version in policy.versions
    ]
    boundary_versions: tuple[str, ...] = (policy.versions[0],)
    if policy.latest != policy.versions[0]:
        boundary_versions += (policy.latest,)
    include.extend(
        {"os": runner, "python-version": version}
        for runner in policy.boundary_coverage_os
        for version in boundary_versions
    )
    return {"include": include}


def running_python_feature_release() -> str:
    """Return the current interpreter's major.minor feature release."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def verify_latest_runtime(policy: PythonSupportPolicy, runtime: str) -> None:
    """Fail when the latest stable canary differs from the declared latest release."""
    if PYTHON_MINOR.fullmatch(runtime) is None:
        raise PolicyError(f"runtime must use major.minor syntax, got {runtime!r}")
    if runtime != policy.latest:
        raise PolicyError(
            f"latest stable runtime is {runtime}, but policy ends at {policy.latest}; "
            "verify compatibility, then update .github/python-support.json"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(".github/python-support.json"),
        help="Path to the Python support policy",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="Validate the policy")
    subparsers.add_parser(
        "emit-github-output",
        help="Emit matrix and latest values for GITHUB_OUTPUT",
    )
    verify_parser = subparsers.add_parser(
        "verify-latest-runtime",
        help="Compare the running interpreter with the policy's latest release",
    )
    verify_parser.add_argument(
        "--runtime",
        help="Override the running major.minor version for deterministic tests",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the selected policy operation."""
    args = parse_args(argv)
    try:
        policy = load_policy(args.policy)
        if args.command == "validate":
            print(f"Python support policy is valid: {', '.join(policy.versions)}")
        elif args.command == "emit-github-output":
            matrix = json.dumps(build_matrix(policy), separators=(",", ":"))
            print(f"matrix={matrix}")
            print(f"latest={policy.latest}")
        else:
            runtime = args.runtime or running_python_feature_release()
            verify_latest_runtime(policy, runtime)
            print(f"Latest stable Python {runtime} is declared in the policy.")
    except PolicyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
