#!/usr/bin/env python3
"""Fail-closed preflight using /dependency-graph/sbom evidence."""

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


def advanced_security_status(document: dict[str, Any]) -> str:
    """Require the published Code Security status for a non-public repository."""
    analysis = document.get("security_and_analysis")
    field = (
        "code_security"
        if isinstance(analysis, dict) and "code_security" in analysis
        else "advanced_security"
    )
    value = analysis.get(field) if isinstance(analysis, dict) else None
    status = value.get("status") if isinstance(value, dict) else None
    if not isinstance(status, str) or status not in {"enabled", "disabled"}:
        raise InspectionError(f"Repository response has an invalid {field} status.")
    return status


def dependency_graph_is_available(document: Any) -> bool:
    """Validate that GitHub returned an SBOM generated from the dependency graph."""
    if not isinstance(document, dict):
        raise InspectionError("Dependency graph SBOM response is invalid.")
    sbom = document.get("sbom")
    spdx_id = sbom.get("SPDXID") if isinstance(sbom, dict) else None
    if not isinstance(spdx_id, str) or not spdx_id:
        raise InspectionError("Dependency graph SBOM response has no SPDXID.")
    return True


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Inspect one GitHub.com repository before dependency-review installation."""
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Dependency-review preflight supports GitHub.com only.")
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
        raise InspectionError(
            "Archived repositories cannot install dependency-review workflows."
        )
    if require_boolean(repository, "disabled"):
        raise InspectionError(
            "Disabled repositories cannot install dependency-review workflows."
        )
    visibility = repository.get("visibility")
    if visibility not in SUPPORTED_VISIBILITIES:
        raise InspectionError("Repository response has an invalid visibility value.")

    github_code_security = "not-required"
    if visibility != "public":
        owner_document = repository.get("owner")
        owner_type = (
            owner_document.get("type") if isinstance(owner_document, dict) else None
        )
        if owner_type != "Organization":
            raise InspectionError(
                "Dependency review for private or internal repositories requires an "
                "eligible organization-owned repository."
            )
        github_code_security = advanced_security_status(repository)
        if github_code_security != "enabled":
            return {
                "inspection_complete": True,
                "decision": "enable-github-code-security-before-installing-dependency-review",
                "repository": args.repository,
                "visibility": visibility,
                "dependency_graph_available": False,
                "github_code_security": github_code_security,
                "github_api_requests": client.request_count,
            }

    dependency_graph_is_available(
        client.json(f"repos/{owner}/{repo}/dependency-graph/sbom")
    )
    return {
        "inspection_complete": True,
        "decision": "may-install-dependency-review-workflow",
        "repository": args.repository,
        "visibility": visibility,
        "dependency_graph_available": True,
        "github_code_security": github_code_security,
        "github_api_requests": client.request_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--hostname", default="github.com")
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
