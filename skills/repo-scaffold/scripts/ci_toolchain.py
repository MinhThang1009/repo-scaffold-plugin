#!/usr/bin/env python3
"""Validate centralized CI tool pins and detect upstream release drift."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


POLICY_FIELDS = {
    "schema-version",
    "documentation-python",
    "standalone-tools",
}
TOOL_FIELDS = {"repository", "version", "asset-template", "sha256"}
TOOL_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_RESPONSE_BYTES = 1_000_000


class ToolchainError(ValueError):
    """Raised when a CI toolchain policy or upstream release is invalid."""


@dataclass(frozen=True)
class ToolPin:
    """A reviewed standalone CI tool release and artifact digest."""

    name: str
    repository: str
    version: str
    asset_template: str
    sha256: str

    @property
    def asset_name(self) -> str:
        """Render the expected release asset name from the reviewed version."""
        return self.asset_template.replace("{version}", self.version)


@dataclass(frozen=True)
class CiToolchainPolicy:
    """Normalized bootstrap runtime and standalone-tool policy."""

    documentation_python: str
    tools: tuple[ToolPin, ...]


def reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate member names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ToolchainError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def parse_tool(name: str, value: Any) -> ToolPin:
    """Validate and normalize one standalone tool entry."""
    if TOOL_NAME.fullmatch(name) is None:
        raise ToolchainError(f"invalid standalone tool name: {name!r}")
    if not isinstance(value, dict):
        raise ToolchainError(f"standalone-tools.{name} must be an object")
    unexpected = sorted(set(value) - TOOL_FIELDS)
    missing = sorted(TOOL_FIELDS - set(value))
    if unexpected:
        raise ToolchainError(
            f"standalone-tools.{name} has unknown fields: {', '.join(unexpected)}"
        )
    if missing:
        raise ToolchainError(
            f"standalone-tools.{name} is missing fields: {', '.join(missing)}"
        )

    repository = value["repository"]
    version = value["version"]
    asset_template = value["asset-template"]
    sha256 = value["sha256"]
    if not isinstance(repository, str) or REPOSITORY.fullmatch(repository) is None:
        raise ToolchainError(
            f"standalone-tools.{name}.repository must be an owner/repository name"
        )
    if not isinstance(version, str) or SEMVER.fullmatch(version) is None:
        raise ToolchainError(
            f"standalone-tools.{name}.version must be a stable SemVer release"
        )
    if (
        not isinstance(asset_template, str)
        or asset_template.count("{version}") != 1
        or "/" in asset_template
        or "\\" in asset_template
        or "{" in asset_template.replace("{version}", "")
        or "}" in asset_template.replace("{version}", "")
    ):
        raise ToolchainError(
            f"standalone-tools.{name}.asset-template must contain exactly one "
            "{version} token and no path separators"
        )
    if not isinstance(sha256, str) or SHA256.fullmatch(sha256) is None:
        raise ToolchainError(
            f"standalone-tools.{name}.sha256 must be 64 lowercase hex characters"
        )
    return ToolPin(
        name=name,
        repository=repository,
        version=version,
        asset_template=asset_template,
        sha256=sha256,
    )


def parse_policy(document: Any) -> CiToolchainPolicy:
    """Validate and normalize a decoded CI toolchain policy."""
    if not isinstance(document, dict):
        raise ToolchainError("policy root must be an object")
    unexpected = sorted(set(document) - POLICY_FIELDS)
    missing = sorted(POLICY_FIELDS - set(document))
    if unexpected:
        raise ToolchainError(f"unknown fields: {', '.join(unexpected)}")
    if missing:
        raise ToolchainError(f"missing fields: {', '.join(missing)}")
    if type(document["schema-version"]) is not int or document["schema-version"] != 1:
        raise ToolchainError("schema-version must be the integer 1")
    if document["documentation-python"] != "3.x":
        raise ToolchainError(
            "documentation-python must be the rolling stable CPython selector 3.x"
        )
    raw_tools = document["standalone-tools"]
    if not isinstance(raw_tools, dict):
        raise ToolchainError("standalone-tools must be an object")
    tools = tuple(parse_tool(name, raw_tools[name]) for name in sorted(raw_tools))
    return CiToolchainPolicy(documentation_python="3.x", tools=tools)


def load_policy(path: Path) -> CiToolchainPolicy:
    """Read a UTF-8 JSON policy with duplicate-member rejection."""
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_json_pairs,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ToolchainError(f"could not read {path}: {error}") from error
    return parse_policy(document)


def github_output_lines(policy: CiToolchainPolicy) -> list[str]:
    """Render stable single-line outputs for GitHub Actions."""
    lines = [f"documentation_python={policy.documentation_python}"]
    for tool in policy.tools:
        prefix = tool.name.replace("-", "_")
        lines.append(f"{prefix}_version={tool.version}")
        lines.append(f"{prefix}_sha256={tool.sha256}")
    return lines


def fetch_latest_release(tool: ToolPin, *, opener: Any = urlopen) -> Any:
    """Fetch one bounded public GitHub release document without credentials."""
    url = f"https://api.github.com/repos/{tool.repository}/releases/latest"
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "repo-scaffold-ci-toolchain",
        },
    )
    try:
        with opener(request, timeout=15) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError) as error:
        raise ToolchainError(
            f"could not query latest release for {tool.repository}: {error}"
        ) from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ToolchainError(f"release response for {tool.repository} is too large")
    try:
        return json.loads(payload, object_pairs_hook=reject_duplicate_json_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ToolchainError(
            f"latest release for {tool.repository} returned invalid JSON: {error}"
        ) from error


def verify_latest_release(tool: ToolPin, document: Any) -> None:
    """Compare one reviewed pin and digest with the latest GitHub release."""
    if not isinstance(document, dict):
        raise ToolchainError(f"latest release for {tool.repository} must be an object")
    expected_tag = f"v{tool.version}"
    if document.get("tag_name") != expected_tag:
        raise ToolchainError(
            f"{tool.name} policy pins {expected_tag}, but latest release is "
            f"{document.get('tag_name')!r}; review the release and update the policy"
        )
    assets = document.get("assets")
    if not isinstance(assets, list):
        raise ToolchainError(f"latest release for {tool.repository} has no asset list")
    matches = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == tool.asset_name
    ]
    if len(matches) != 1:
        raise ToolchainError(
            f"latest release for {tool.repository} must contain exactly one "
            f"{tool.asset_name!r} asset"
        )
    expected_digest = f"sha256:{tool.sha256}"
    if matches[0].get("digest") != expected_digest:
        raise ToolchainError(
            f"{tool.name} asset digest differs from the reviewed policy"
        )


def verify_latest_releases(policy: CiToolchainPolicy) -> None:
    """Verify every standalone pin against its current upstream release."""
    for tool in policy.tools:
        verify_latest_release(tool, fetch_latest_release(tool))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(".github/ci-toolchain.json"),
        help="Path to the CI toolchain policy",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="Validate the policy")
    subparsers.add_parser(
        "emit-github-output",
        help="Emit bootstrap runtime and standalone-tool outputs",
    )
    subparsers.add_parser(
        "verify-latest-releases",
        help="Compare standalone pins and digests with upstream releases",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the selected CI toolchain operation."""
    args = parse_args(argv)
    try:
        policy = load_policy(args.policy)
        if args.command == "validate":
            print(
                f"CI toolchain policy is valid: {len(policy.tools)} standalone tool(s)"
            )
        elif args.command == "emit-github-output":
            print("\n".join(github_output_lines(policy)))
        else:
            verify_latest_releases(policy)
            print("Standalone CI tool pins match the latest upstream releases.")
    except ToolchainError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
