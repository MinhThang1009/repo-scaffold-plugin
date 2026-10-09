#!/usr/bin/env python3
"""Fail-closed preflight for installing a CodeQL advanced-setup workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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
    if type(getattr(args, "confirm_no_external_codeql", None)) is not bool:
        raise InspectionError("Advanced CodeQL absence confirmation must be Boolean.")
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Advanced CodeQL preflight supports GitHub.com only.")
    owner, repo = split_repository(args.repository)
    repo_root = Path(os.path.abspath(os.fspath(args.repo_root)))
    codeql_preflight.require_safe_root(repo_root)
    if not repo_root.is_dir():
        raise InspectionError("Repository root is not a directory.")
    client = GitHubClient(args.hostname, forbidden_root=repo_root)
    repository = client.json(f"repos/{owner}/{repo}")
    if not isinstance(repository, dict):
        raise InspectionError("Repository response is invalid.")
    full_name = repository.get("full_name")
    if (
        not isinstance(full_name, str)
        or full_name.casefold() != args.repository.casefold()
    ):
        raise InspectionError("GitHub returned a different repository than requested.")
    repository_id = codeql_preflight.verified_repository_id(
        repository, getattr(args, "expected_repository_id", None)
    )
    codeql_preflight.require_verified_default_branch(
        repository, args.repository, args.default_branch
    )
    if require_boolean(repository, "archived"):
        raise InspectionError("Archived repositories cannot install CodeQL workflows.")
    if require_boolean(repository, "disabled"):
        raise InspectionError("Disabled repositories cannot install CodeQL workflows.")
    visibility = repository.get("visibility")
    if visibility not in tuple(SUPPORTED_VISIBILITIES):
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
                "repository_id": repository_id,
                "default_branch": args.default_branch,
                "visibility": visibility,
                "github_code_security": github_code_security,
                "github_actions_enabled": None,
                "existing_codeql_setup": None,
                **codeql_preflight.request_metrics(client.request_count),
            }

    github_actions_enabled = actions_are_enabled(
        client.json(f"repos/{owner}/{repo}/actions/permissions")
    )
    if not github_actions_enabled:
        return {
            "inspection_complete": True,
            "decision": "enable-github-actions-before-installing-advanced-codeql",
            "repository": args.repository,
            "repository_id": repository_id,
            "default_branch": args.default_branch,
            "visibility": visibility,
            "github_code_security": github_code_security,
            "github_actions_enabled": False,
            "existing_codeql_setup": None,
            **codeql_preflight.request_metrics(client.request_count),
        }

    existing_setup = codeql_preflight.run(
        argparse.Namespace(
            repo_root=args.repo_root,
            repository=args.repository,
            expected_repository_id=repository_id,
            default_branch=args.default_branch,
            hostname=args.hostname,
            confirm_no_external_codeql=args.confirm_no_external_codeql,
        ),
        _client=client,
    )
    if (
        not isinstance(existing_setup, dict)
        or not isinstance(existing_setup.get("repository"), str)
        or existing_setup["repository"].casefold() != args.repository.casefold()
        or existing_setup.get("default_branch") != args.default_branch
    ):
        raise InspectionError(
            "Nested CodeQL verdict is not bound to the requested repository and branch."
        )
    codeql_preflight.verified_repository_id(
        {"id": existing_setup.get("repository_id")}, repository_id
    )
    decision = existing_setup_decision(existing_setup)
    if decision == "may-install-advanced-codeql-workflow" and (
        existing_setup.get("default_setup_state") != "not-configured"
        or existing_setup.get("workflow_inspection_performed") is not True
        or existing_setup.get("analysis_inspection_performed") is not True
        or existing_setup.get("external_codeql_absence_confirmed") is not True
        or existing_setup.get("advanced_workflows") != []
        or existing_setup.get("has_codeql_analysis") is not False
    ):
        raise InspectionError(
            "Nested CodeQL absence proof is incomplete, contradictory, or unverified."
        )
    codeql_preflight.revalidate_repository_state(
        client,
        args.repository,
        repository,
        (
            "default_branch",
            "archived",
            "disabled",
            "visibility",
            "owner.id",
            "owner.type",
            "security_and_analysis.code_security.status",
            "security_and_analysis.advanced_security.status",
        ),
    )
    if not actions_are_enabled(
        client.json(f"repos/{owner}/{repo}/actions/permissions")
    ):
        raise InspectionError(
            "GitHub Actions changed during advanced CodeQL inspection."
        )
    return {
        "inspection_complete": True,
        "decision": (
            "bind-repository-identity-before-mutation"
            if getattr(args, "expected_repository_id", None) is None
            else decision
        ),
        "repository": args.repository,
        "repository_id": repository_id,
        "default_branch": args.default_branch,
        "visibility": visibility,
        "github_code_security": github_code_security,
        "github_actions_enabled": True,
        "existing_codeql_setup": existing_setup,
        **codeql_preflight.request_metrics(client.request_count),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--expected-repository-id", type=int)
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
