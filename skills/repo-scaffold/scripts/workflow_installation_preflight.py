#!/usr/bin/env python3
"""Fail-closed capability preflight for GitHub Actions workflow assets."""

from __future__ import annotations

import argparse
import fnmatch
import json
import stat
from pathlib import Path, PurePosixPath
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
CODE_SCANNING_ALLOWLIST_SCHEMA_VERSION = 3
MAX_CODE_SCANNING_ALLOWLIST_BYTES = 1024 * 1024
CODE_SCANNING_GATE_COMMAND = "scripts/check_code_scanning_alerts.py"
FRESHNESS_AUDIT_COMMAND = "python scripts/audit_freshness.py"
FRESHNESS_REMINDER_MARKER = "repo-scaffold-freshness-audit"
FRESHNESS_REMINDER_BODY_FILE = "--body-file"


class DuplicateJsonMember(ValueError):
    """Raised when a JSON companion file contains ambiguous duplicate keys."""


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting ambiguous duplicate member names."""
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def workflow_document(text: str, source: Path) -> dict[str, Any]:
    """Parse one workflow mapping without silently accepting duplicate keys."""
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
    return document


def workflow_run_commands(document: dict[str, Any]) -> list[str]:
    """Return shell command scalars from workflow steps."""
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict)
    commands: list[str] = []
    for job in jobs.values():
        assert isinstance(job, dict)
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                commands.append(step["run"])
    return commands


def executable_shell_lines(command: str) -> list[str]:
    """Return non-empty shell lines whose first token is not a comment."""
    return [
        line.lstrip()
        for line in command.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def local_reusable_workflow_names(document: dict[str, Any], source: Path) -> list[str]:
    """Return local reusable workflow inputs that must be preflighted together."""
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict)
    names: list[str] = []
    for job_name, job in jobs.items():
        assert isinstance(job, dict)
        call = job.get("uses")
        if call is None or not isinstance(call, str) or not call.startswith("./"):
            continue
        relative = call[2:]
        path = PurePosixPath(relative)
        if (
            not relative
            or "\\" in relative
            or "\x00" in relative
            or path.as_posix() != relative
            or path.parts[:2] != (".github", "workflows")
            or len(path.parts) != 3
            or path.suffix.casefold() not in {".yml", ".yaml"}
        ):
            raise InspectionError(
                f"Workflow {source} job {job_name!r} has an unsafe local reusable-workflow reference."
            )
        names.append(path.name)
    return names


def requires_issue_write(text: str, source: Path) -> bool:
    """Inspect YAML permission fields without treating comments or scripts as policy."""
    document = workflow_document(text, source)
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict)
    for scope in [document, *jobs.values()]:
        permissions = scope.get("permissions", {})
        if permissions == "write-all" or (
            isinstance(permissions, dict) and permissions.get("issues") == "write"
        ):
            return True
    return False


def requires_pull_request_write_tokens(text: str, source: Path) -> bool:
    """Detect pull-request workflows whose write scopes need an explicit check."""
    document = workflow_document(text, source)
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict)
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


def is_code_scanning_gate(text: str, source: Path) -> bool:
    """Identify a gate that requires the shipped allowlist freshness reminder."""
    return any(
        CODE_SCANNING_GATE_COMMAND in command
        for command in workflow_run_commands(workflow_document(text, source))
    )


def is_freshness_reminder_workflow(text: str, source: Path) -> bool:
    """Identify the scheduled reminder that audits code-scanning exception dates."""
    document = workflow_document(text, source)
    triggers = document.get("on")
    if (
        not isinstance(triggers, dict)
        or "schedule" not in triggers
        or "workflow_dispatch" not in triggers
        or not requires_issue_write(text, source)
    ):
        return False
    lines = [
        line
        for command in workflow_run_commands(document)
        for line in executable_shell_lines(command)
    ]
    return (
        any(line.startswith(FRESHNESS_AUDIT_COMMAND) for line in lines)
        and any(FRESHNESS_REMINDER_MARKER in line for line in lines)
        and any(FRESHNESS_REMINDER_BODY_FILE in line for line in lines)
    )


def validate_code_scanning_allowlist(path: Path) -> None:
    """Require a bounded schema-v3 allowlist companion before gate installation."""
    try:
        metadata = path.lstat()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
            or metadata.st_size > MAX_CODE_SCANNING_ALLOWLIST_BYTES
        ):
            raise OSError("not a regular allowlist file")
        with path.open("rb") as stream:
            raw = stream.read(MAX_CODE_SCANNING_ALLOWLIST_BYTES + 1)
        if len(raw) > MAX_CODE_SCANNING_ALLOWLIST_BYTES:
            raise OSError("allowlist exceeds the byte safety cap")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_json_object)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise InspectionError(
            f"Code-scanning allowlist input is missing or unsafe: {path}"
        ) from exc
    if (
        not isinstance(document, dict)
        or document.get("schema-version") != CODE_SCANNING_ALLOWLIST_SCHEMA_VERSION
        or not isinstance(document.get("allowlist"), list)
    ):
        raise InspectionError(
            "Code-scanning allowlist input must use schema-version 3 and an allowlist array."
        )


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
) -> tuple[list[str], list[str], list[str], list[str], bool]:
    """Read external references and workflow capabilities that require confirmation."""
    if not workflows:
        raise InspectionError(
            "Selected Actions policy requires at least one --workflow input."
        )
    references: set[str] = set()
    issue_workflows: set[str] = set()
    pull_request_write_workflows: set[str] = set()
    code_scanning_gate_workflows: set[str] = set()
    freshness_reminder_supplied = False
    workflow_inputs = {workflow.name: workflow for workflow in workflows}
    if len(workflow_inputs) != len(workflows):
        raise InspectionError(
            "Workflow inputs must have unique filenames for local reusable-workflow resolution."
        )
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
        document = workflow_document(text, workflow)
        for local_name in local_reusable_workflow_names(document, workflow):
            if local_name not in workflow_inputs:
                raise InspectionError(
                    f"Workflow {workflow} calls local reusable workflow {local_name!r}; "
                    "pass it as another --workflow input."
                )
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
        if is_code_scanning_gate(text, workflow):
            code_scanning_gate_workflows.add(workflow.name)
        if is_freshness_reminder_workflow(text, workflow):
            freshness_reminder_supplied = True
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
        sorted(code_scanning_gate_workflows, key=str.casefold),
        freshness_reminder_supplied,
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
        code_scanning_gate_workflows,
        freshness_reminder_supplied,
    ) = (
        workflow_capabilities(args.workflow)
        if args.workflow
        else ([], [], [], [], False)
    )
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
                code_scanning_gate_workflows,
                freshness_reminder_supplied,
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
    code_scanning_allowlist_supplied = args.code_scanning_allowlist is not None
    if code_scanning_gate_workflows and args.code_scanning_allowlist is not None:
        validate_code_scanning_allowlist(args.code_scanning_allowlist)
    code_scanning_companions_verified = not code_scanning_gate_workflows or (
        freshness_reminder_supplied and code_scanning_allowlist_supplied
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
    elif not code_scanning_companions_verified:
        decision = "include-code-scanning-companions-before-installing-workflows"
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
        "requires_code_scanning_companions": bool(code_scanning_gate_workflows),
        "code_scanning_gate_workflows": code_scanning_gate_workflows,
        "freshness_reminder_supplied": freshness_reminder_supplied,
        "code_scanning_allowlist_supplied": code_scanning_allowlist_supplied,
        "code_scanning_companions_verified": code_scanning_companions_verified,
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
    parser.add_argument("--code-scanning-allowlist", type=Path)
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
