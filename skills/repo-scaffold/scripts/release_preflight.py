#!/usr/bin/env python3
"""Fail-closed preflight for release workflow and attestation installation."""

from __future__ import annotations

import argparse
import json
from typing import Any

from codeql_preflight import GitHubClient, InspectionError, split_repository


SUPPORTED_VISIBILITIES = frozenset({"public", "private", "internal"})


def require_boolean(document: dict[str, Any], field: str) -> bool:
    """Read a repository boolean without accepting truthy substitute values."""
    value = document.get(field)
    if not isinstance(value, bool):
        raise InspectionError(f"Repository response has an invalid {field!r} value.")
    return value


def default_branch(document: dict[str, Any]) -> str:
    """Require GitHub to return a usable default branch name."""
    value = document.get("default_branch")
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in "\r\n\x00")
    ):
        raise InspectionError("Repository response has an invalid default branch.")
    return value


def attestation_decision(
    visibility: str, request_attestations: bool, enterprise_cloud: bool
) -> str:
    """Choose only a documented attestation variant from verified eligibility."""
    if not request_attestations:
        return "may-install-release-workflows"
    if visibility == "public":
        return "may-install-attestation-workflows"
    if enterprise_cloud:
        return "may-install-attestation-workflows"
    return "render-no-attestation-variant"


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Inspect one exact GitHub.com repository before release asset installation."""
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Release preflight supports GitHub.com only.")
    if not isinstance(args.default_branch, str) or not args.default_branch.strip():
        raise InspectionError("Default branch must be a non-empty string.")
    owner, repo = split_repository(args.repository)
    client = GitHubClient(args.hostname)
    repository = client.json(f"repos/{owner}/{repo}")
    if not isinstance(repository, dict):
        raise InspectionError("Repository response is invalid.")
    full_name = repository.get("full_name")
    if (
        not isinstance(full_name, str)
        or full_name.casefold() != args.repository.casefold()
    ):
        raise InspectionError("GitHub returned a different repository than requested.")
    if require_boolean(repository, "archived"):
        raise InspectionError("Archived repositories cannot install release workflows.")
    if require_boolean(repository, "disabled"):
        raise InspectionError("Disabled repositories cannot install release workflows.")
    actual_default_branch = default_branch(repository)
    if actual_default_branch != args.default_branch:
        raise InspectionError(
            "Requested default branch does not match the repository default branch."
        )
    visibility = repository.get("visibility")
    if visibility not in SUPPORTED_VISIBILITIES:
        raise InspectionError("Repository response has an invalid visibility value.")
    is_fork = require_boolean(repository, "fork")
    decision = attestation_decision(
        visibility, args.with_attestations, args.github_enterprise_cloud
    )
    return {
        "inspection_complete": True,
        "decision": decision,
        "repository": args.repository,
        "default_branch": actual_default_branch,
        "visibility": visibility,
        "is_fork": is_fork,
        "attestations_requested": args.with_attestations,
        "github_enterprise_cloud_confirmed": args.github_enterprise_cloud,
        "github_api_requests": client.request_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--with-attestations", action="store_true")
    parser.add_argument("--github-enterprise-cloud", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        result = run(parse_args())
    except (InspectionError, OSError, UnicodeError) as exc:
        print(
            json.dumps(
                {
                    "inspection_complete": False,
                    "decision": "inconclusive",
                    "error": str(exc),
                }
            )
        )
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
