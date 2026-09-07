#!/usr/bin/env python3
"""Fail-closed capability preflight for GitHub Actions workflow assets."""

from __future__ import annotations

import argparse
import json
from typing import Any

from codeql_preflight import GitHubClient, InspectionError, split_repository


ALLOWED_ACTION_POLICIES = frozenset({"all", "local_only", "selected"})


def require_boolean(document: dict[str, Any], field: str) -> bool:
    """Read a GitHub boolean without accepting truthy replacement values."""
    value = document.get(field)
    if not isinstance(value, bool):
        raise InspectionError(f"Repository response has an invalid {field!r} value.")
    return value


def actions_permissions(document: Any) -> tuple[bool, str]:
    """Validate the repository-scoped GitHub Actions policy response."""
    if not isinstance(document, dict):
        raise InspectionError("GitHub Actions permissions response is invalid.")
    enabled = document.get("enabled")
    allowed_actions = document.get("allowed_actions")
    if not isinstance(enabled, bool):
        raise InspectionError(
            "GitHub Actions permissions response has an invalid 'enabled' value."
        )
    if allowed_actions not in ALLOWED_ACTION_POLICIES:
        raise InspectionError(
            "GitHub Actions permissions response has an invalid 'allowed_actions' value."
        )
    return enabled, allowed_actions


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Inspect one repository before copying a dependent workflow asset."""
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError(
            "Workflow-installation preflight supports GitHub.com only."
        )
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
        raise InspectionError("Archived repositories cannot install workflow assets.")
    if require_boolean(repository, "disabled"):
        raise InspectionError("Disabled repositories cannot install workflow assets.")
    issues_enabled = require_boolean(repository, "has_issues")

    actions_enabled, allowed_actions = actions_permissions(
        client.json(f"repos/{owner}/{repo}/actions/permissions")
    )
    external_actions_verified = actions_enabled and (
        not args.require_external_actions or allowed_actions == "all"
    )
    issue_workflows_eligible = not args.require_issues or issues_enabled
    if not actions_enabled:
        decision = "enable-github-actions-before-installing-workflows"
    elif args.require_external_actions and allowed_actions == "local_only":
        decision = "allow-external-actions-before-installing-workflows"
    elif args.require_external_actions and allowed_actions == "selected":
        decision = "verify-selected-actions-before-installing-workflows"
    elif not issue_workflows_eligible:
        decision = "enable-issues-before-installing-issue-workflows"
    else:
        decision = "may-install-workflow-assets"
    return {
        "inspection_complete": True,
        "decision": decision,
        "repository": args.repository,
        "github_actions_enabled": actions_enabled,
        "allowed_actions": allowed_actions,
        "external_actions_verified": external_actions_verified,
        "issues_enabled": issues_enabled,
        "issue_workflows_eligible": issue_workflows_eligible,
        "github_api_requests": client.request_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--require-external-actions", action="store_true")
    parser.add_argument("--require-issues", action="store_true")
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
