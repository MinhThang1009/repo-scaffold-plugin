#!/usr/bin/env python3
"""Fail-closed capability preflight for GitHub Actions workflow assets."""

from __future__ import annotations

import argparse
import fnmatch
import json
import stat
from pathlib import Path
from typing import Any

from codeql_preflight import (
    GitHubClient,
    InspectionError,
    MAX_WORKFLOW_BYTES,
    UniqueKeyBaseLoader,
    split_repository,
    yaml,
)
import sync_action_pins


ALLOWED_ACTION_POLICIES = frozenset({"all", "local_only", "selected"})


def requires_issue_write(text: str, source: Path) -> bool:
    """Inspect YAML permission fields without treating comments or scripts as policy."""
    try:
        loader = UniqueKeyBaseLoader(text)
        try:
            document = loader.get_single_data()
        finally:
            loader.dispose()
    except (yaml.YAMLError, InspectionError, RecursionError) as exc:
        raise InspectionError(f"Could not parse workflow {source}: {exc}") from exc
    if not isinstance(document, dict):
        raise InspectionError(f"Workflow {source} is not a YAML mapping.")
    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict) or any(
        not isinstance(job, dict) for job in jobs.values()
    ):
        raise InspectionError(f"Workflow {source} has an invalid jobs mapping.")
    for scope in [document, *jobs.values()]:
        permissions = scope.get("permissions", {})
        if permissions == "write-all" or (
            isinstance(permissions, dict) and permissions.get("issues") == "write"
        ):
            return True
    return False


def requires_pull_request_write_tokens(text: str, source: Path) -> bool:
    """Detect pull-request workflows whose write scopes need an explicit check."""
    try:
        loader = UniqueKeyBaseLoader(text)
        try:
            document = loader.get_single_data()
        finally:
            loader.dispose()
    except (yaml.YAMLError, InspectionError, RecursionError) as exc:
        raise InspectionError(f"Could not parse workflow {source}: {exc}") from exc
    if not isinstance(document, dict):
        raise InspectionError(f"Workflow {source} is not a YAML mapping.")
    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict) or any(
        not isinstance(job, dict) for job in jobs.values()
    ):
        raise InspectionError(f"Workflow {source} has an invalid jobs mapping.")
    triggers = document.get("on")
    if isinstance(triggers, str):
        pull_request_trigger = triggers == "pull_request"
    elif isinstance(triggers, list):
        pull_request_trigger = "pull_request" in triggers
    elif isinstance(triggers, dict):
        pull_request_trigger = "pull_request" in triggers
    else:
        pull_request_trigger = False
    if not pull_request_trigger:
        return False
    for scope in [document, *jobs.values()]:
        permissions = scope.get("permissions", {})
        if permissions == "write-all" or (
            isinstance(permissions, dict) and "write" in permissions.values()
        ):
            return True
    return False


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


def selected_actions_policy(document: Any) -> dict[str, bool | list[str]]:
    """Validate the effective selected-actions response from GitHub."""
    if not isinstance(document, dict):
        raise InspectionError("Selected Actions policy response is invalid.")
    github_owned = document.get("github_owned_allowed")
    verified = document.get("verified_allowed")
    patterns = document.get("patterns_allowed")
    if not isinstance(github_owned, bool) or not isinstance(verified, bool):
        raise InspectionError("Selected Actions policy has invalid boolean settings.")
    if not isinstance(patterns, list) or any(
        not isinstance(pattern, str)
        or not pattern
        or any(character in pattern for character in "\r\n\x00")
        for pattern in patterns
    ):
        raise InspectionError("Selected Actions policy has invalid allowed patterns.")
    return {
        "github_owned_allowed": github_owned,
        "verified_allowed": verified,
        "patterns_allowed": patterns,
    }


def workflow_capabilities(
    workflows: list[Path],
) -> tuple[list[str], list[str], list[str]]:
    """Read external references and workflow capabilities that require confirmation."""
    if not workflows:
        raise InspectionError(
            "Selected Actions policy requires at least one --workflow input."
        )
    references: set[str] = set()
    issue_workflows: set[str] = set()
    pull_request_write_workflows: set[str] = set()
    for workflow in workflows:
        try:
            metadata = workflow.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or workflow.is_symlink()
                or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
                or workflow.suffix.casefold() not in {".yml", ".yaml"}
                or metadata.st_size > MAX_WORKFLOW_BYTES
            ):
                raise OSError("not a regular workflow file")
            with workflow.open("rb") as stream:
                raw = stream.read(MAX_WORKFLOW_BYTES + 1)
            if len(raw) > MAX_WORKFLOW_BYTES:
                raise OSError("workflow exceeds the byte safety cap")
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise InspectionError(
                f"Workflow input is missing or unsafe: {workflow}"
            ) from exc
        try:
            # This rejects aliases, unpinned actions, and non-action `uses:` forms
            # before the exact references below are compared with GitHub's policy.
            sync_action_pins.auditable_action_repositories(workflow, text)
        except ValueError as exc:
            raise InspectionError(str(exc)) from exc
        if requires_issue_write(text, workflow):
            issue_workflows.add(workflow.name)
        if requires_pull_request_write_tokens(text, workflow):
            pull_request_write_workflows.add(workflow.name)
        direct_references = {
            sync_action_pins.normalized_uses_reference(match)
            for match in sync_action_pins.workflow_uses_matches(text)
            if not (
                match.groupdict().get("quote") == ""
                and match.group("reference").startswith("*")
            )
        }
        direct_references.update(
            sync_action_pins.normalized_uses_reference(match)
            for match in sync_action_pins.anchored_action_reference_matches(text)
        )
        references.update(
            reference
            for reference in direct_references
            if not reference.startswith(("./", "docker://"))
        )
    return (
        sorted(references, key=str.casefold),
        sorted(issue_workflows, key=str.casefold),
        sorted(pull_request_write_workflows, key=str.casefold),
    )


def selected_policy_allows(
    reference: str, policy: dict[str, bool | list[str]], *, public_repository: bool
) -> bool:
    """Apply GitHub's selected-actions allowlist to one exact action reference."""
    action = reference.rsplit("@", 1)[0]
    # GitHub documents this setting for actions in the `actions` organization.
    if policy["github_owned_allowed"] is True and action.casefold().startswith(
        "actions/"
    ):
        return True
    # GitHub does not expose a stable REST attribute proving a Marketplace creator
    # is verified. Require a literal configured pattern instead of guessing.
    # GitHub documents patterns as applicable to public repositories. Do not infer
    # Enterprise Cloud eligibility for a private or internal repository.
    if not public_repository:
        return False
    patterns = policy["patterns_allowed"]
    assert isinstance(patterns, list)
    return any(
        fnmatch.fnmatchcase(reference.casefold(), pattern.casefold())
        for pattern in patterns
    )


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
    visibility = repository.get("visibility")

    (
        external_action_references,
        detected_issue_workflows,
        pull_request_write_workflows,
    ) = workflow_capabilities(args.workflow) if args.workflow else ([], [], [])
    requires_external_actions = args.require_external_actions or bool(
        external_action_references
    )
    requires_issues = args.require_issues or bool(detected_issue_workflows)

    actions_enabled, allowed_actions = actions_permissions(
        client.json(f"repos/{owner}/{repo}/actions/permissions")
    )
    selected_policy: dict[str, bool | list[str]] | None = None
    unapproved_action_references: list[str] = []
    if actions_enabled and requires_external_actions and allowed_actions == "selected":
        selected_policy = selected_actions_policy(
            client.json(f"repos/{owner}/{repo}/actions/permissions/selected-actions")
        )
        if visibility not in {"public", "private", "internal"}:
            raise InspectionError(
                "Repository response has an invalid visibility value."
            )
        if not external_action_references:
            (
                external_action_references,
                detected_issue_workflows,
                pull_request_write_workflows,
            ) = workflow_capabilities(args.workflow)
        unapproved_action_references = [
            reference
            for reference in external_action_references
            if not selected_policy_allows(
                reference,
                selected_policy,
                public_repository=visibility == "public",
            )
        ]
    external_actions_verified = actions_enabled and (
        not requires_external_actions
        or allowed_actions == "all"
        or (
            allowed_actions == "selected"
            and not unapproved_action_references
            and bool(external_action_references)
        )
    )
    issue_workflows_eligible = not requires_issues or issues_enabled
    pull_request_write_tokens_confirmed = (
        not pull_request_write_workflows or args.confirm_pull_request_write_tokens
    )
    if not actions_enabled:
        decision = "enable-github-actions-before-installing-workflows"
    elif requires_external_actions and allowed_actions == "local_only":
        decision = "allow-external-actions-before-installing-workflows"
    elif (
        requires_external_actions
        and allowed_actions == "selected"
        and not external_actions_verified
    ):
        decision = "allow-selected-actions-before-installing-workflows"
    elif not issue_workflows_eligible:
        decision = "enable-issues-before-installing-issue-workflows"
    elif not pull_request_write_tokens_confirmed:
        decision = "confirm-pull-request-write-tokens-before-installing-workflows"
    else:
        decision = "may-install-workflow-assets"
    return {
        "inspection_complete": True,
        "decision": decision,
        "repository": args.repository,
        "visibility": visibility,
        "github_actions_enabled": actions_enabled,
        "allowed_actions": allowed_actions,
        "requires_external_actions": requires_external_actions,
        "external_actions_verified": external_actions_verified,
        "external_action_references": external_action_references,
        "unapproved_action_references": unapproved_action_references,
        "selected_actions_policy": selected_policy,
        "issues_enabled": issues_enabled,
        "requires_issues": requires_issues,
        "detected_issue_workflows": detected_issue_workflows,
        "issue_workflows_eligible": issue_workflows_eligible,
        "requires_pull_request_write_tokens": bool(pull_request_write_workflows),
        "pull_request_write_workflows": pull_request_write_workflows,
        "pull_request_write_tokens_confirmed": pull_request_write_tokens_confirmed,
        "github_api_requests": client.request_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--require-external-actions", action="store_true")
    parser.add_argument("--require-issues", action="store_true")
    parser.add_argument("--confirm-pull-request-write-tokens", action="store_true")
    parser.add_argument("--workflow", type=Path, action="append", default=[])
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
