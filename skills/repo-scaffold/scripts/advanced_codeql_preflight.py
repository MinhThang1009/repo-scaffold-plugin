#!/usr/bin/env python3
"""Fail-closed preflight for installing a CodeQL advanced-setup workflow."""

from __future__ import annotations

import argparse
import json
from typing import Any

import codeql_preflight
from codeql_preflight import GitHubClient, InspectionError, split_repository


SUPPORTED_VISIBILITIES = frozenset({"public", "private", "internal"})


def require_boolean(document: dict[str, Any], field: str) -> bool:
    """Read a repository boolean without accepting truthy substitute values."""
    value = document.get(field)
    if not isinstance(value, bool):
        raise InspectionError(f"Repository response has an invalid {field!r} value.")
    return value


def advanced_security_status(document: dict[str, Any]) -> str:
    """Read the Code Security status needed by non-public repositories."""
    analysis = document.get("security_and_analysis")
    value = analysis.get("advanced_security") if isinstance(analysis, dict) else None
    status = value.get("status") if isinstance(value, dict) else None
    if status not in {"enabled", "disabled"}:
        raise InspectionError(
            "Repository response has an invalid advanced_security status."
        )
    return status


def actions_are_enabled(document: Any) -> bool:
    """Require an unambiguous enabled flag from the Actions permissions API."""
    if not isinstance(document, dict) or not isinstance(document.get("enabled"), bool):
        raise InspectionError("GitHub Actions permissions response is invalid.")
    return document["enabled"]


def existing_setup_decision(inspection: dict[str, Any]) -> str:
    """Map the shared CodeQL inspection to an advanced-setup-safe decision."""
    if inspection.get("inspection_complete") is not True:
        raise InspectionError("CodeQL setup inspection did not complete.")
    decision = inspection.get("decision")
    if decision == "preserve-default-setup":
        return "disable-default-setup-before-installing-advanced-codeql"
    if decision == "require-explicit-switch-confirmation":
        return "preserve-existing-codeql-setup"
    if decision == "may-offer-default-setup":
        return "may-install-advanced-codeql-workflow"
    raise InspectionError("CodeQL setup inspection returned an unknown decision.")


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Inspect one repository before installing the CodeQL advanced asset."""
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Advanced CodeQL preflight supports GitHub.com only.")
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
        raise InspectionError("Archived repositories cannot install CodeQL workflows.")
    if require_boolean(repository, "disabled"):
        raise InspectionError("Disabled repositories cannot install CodeQL workflows.")
    visibility = repository.get("visibility")
    if visibility not in SUPPORTED_VISIBILITIES:
        raise InspectionError("Repository response has an invalid visibility value.")

    github_code_security = "not-required"
    if visibility != "public":
        owner_document = repository.get("owner")
        if (
            not isinstance(owner_document, dict)
            or owner_document.get("type") != "Organization"
        ):
            raise InspectionError(
                "Non-public CodeQL setup requires an eligible organization-owned repository."
            )
        github_code_security = advanced_security_status(repository)
        if github_code_security != "enabled":
            return {
                "inspection_complete": True,
                "decision": "enable-github-code-security-before-installing-advanced-codeql",
                "repository": args.repository,
                "visibility": visibility,
                "github_code_security": github_code_security,
                "github_actions_enabled": None,
                "existing_codeql_setup": None,
                "github_api_requests": client.request_count,
            }

    github_actions_enabled = actions_are_enabled(
        client.json(f"repos/{owner}/{repo}/actions/permissions")
    )
    if not github_actions_enabled:
        return {
            "inspection_complete": True,
            "decision": "enable-github-actions-before-installing-advanced-codeql",
            "repository": args.repository,
            "visibility": visibility,
            "github_code_security": github_code_security,
            "github_actions_enabled": False,
            "existing_codeql_setup": None,
            "github_api_requests": client.request_count,
        }

    existing_setup = codeql_preflight.run(
        argparse.Namespace(
            repo_root=args.repo_root,
            repository=args.repository,
            default_branch=args.default_branch,
            hostname=args.hostname,
            confirm_no_external_codeql=args.confirm_no_external_codeql,
        )
    )
    decision = existing_setup_decision(existing_setup)
    return {
        "inspection_complete": True,
        "decision": decision,
        "repository": args.repository,
        "visibility": visibility,
        "github_code_security": github_code_security,
        "github_actions_enabled": True,
        "existing_codeql_setup": existing_setup,
        "github_api_requests": client.request_count
        + int(existing_setup.get("github_api_requests", 0)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--confirm-no-external-codeql", action="store_true")
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
