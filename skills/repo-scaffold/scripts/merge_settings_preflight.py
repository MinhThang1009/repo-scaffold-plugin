#!/usr/bin/env python3
"""Fail-closed merge-settings preflight for repo-scaffold."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.parse import quote

from branch_protection_preflight import GitHubClient, InspectionError, split_repository


SUPPORTED_MERGE_METHODS = frozenset({"merge", "squash", "rebase"})


def require_boolean(document: dict[str, Any], field: str) -> bool:
    value = document.get(field)
    if not isinstance(value, bool):
        raise InspectionError(f"Repository response has an invalid {field!r} value.")
    return value


def status_check_contexts(value: Any, source: str) -> bool:
    if not isinstance(value, list):
        raise InspectionError(f"{source} has no status-check context list.")
    for context in value:
        if (
            not isinstance(context, str)
            or not context.strip()
            or len(context) > 256
            or any(character in context for character in "\r\n\x00")
        ):
            raise InspectionError(f"{source} has an invalid status-check context.")
    return bool(value)


def parse_effective_rules(payload: Any) -> tuple[set[str], bool, bool]:
    if not isinstance(payload, list):
        raise InspectionError("Effective rules response is invalid.")
    if len(payload) >= 100:
        raise InspectionError(
            "Effective rules response may be paginated; inspection is inconclusive."
        )

    required_methods: set[str] = set()
    has_merge_queue = False
    has_ruleset_status_checks = False
    for rule in payload:
        if not isinstance(rule, dict):
            raise InspectionError("Effective rules response has an invalid rule.")
        rule_type = rule.get("type")
        if rule_type not in {
            "merge_queue",
            "pull_request",
            "required_status_checks",
        }:
            continue
        parameters = rule.get("parameters")
        if not isinstance(parameters, dict):
            raise InspectionError(
                f"Effective {rule_type} rule has no parameters mapping."
            )
        if rule_type == "merge_queue":
            method = parameters.get("merge_method")
            if (
                not isinstance(method, str)
                or method.casefold() not in SUPPORTED_MERGE_METHODS
            ):
                raise InspectionError(
                    "Effective merge queue has a missing or unsupported merge method."
                )
            required_methods.add(method.casefold())
            has_merge_queue = True
            continue

        if rule_type == "required_status_checks":
            strict = parameters.get("strict_required_status_checks_policy")
            if not isinstance(strict, bool):
                raise InspectionError(
                    "Effective required-status-checks rule has an invalid strict policy."
                )
            checks = parameters.get("required_status_checks")
            if not isinstance(checks, list):
                raise InspectionError(
                    "Effective required-status-checks rule has no status-check list."
                )
            contexts: list[str] = []
            for check in checks:
                if not isinstance(check, dict):
                    raise InspectionError(
                        "Effective required-status-checks rule has an invalid check."
                    )
                context = check.get("context")
                if not isinstance(context, str):
                    raise InspectionError(
                        "Effective required-status-checks rule has an invalid check."
                    )
                integration_id = check.get("integration_id")
                if integration_id is not None and (
                    not isinstance(integration_id, int)
                    or isinstance(integration_id, bool)
                    or integration_id <= 0
                ):
                    raise InspectionError(
                        "Effective required-status-checks rule has an invalid integration ID."
                    )
                contexts.append(context)
            has_ruleset_status_checks |= status_check_contexts(
                contexts, "Effective required-status-checks rule"
            )
            continue

        methods = parameters.get("allowed_merge_methods")
        if not isinstance(methods, list) or not methods:
            raise InspectionError(
                "Effective pull-request rule has no allowed merge-method list."
            )
        for method in methods:
            if (
                not isinstance(method, str)
                or method.casefold() not in SUPPORTED_MERGE_METHODS
            ):
                raise InspectionError(
                    "Effective pull-request rule has an unsupported merge method."
                )
            required_methods.add(method.casefold())
    return required_methods, has_merge_queue, has_ruleset_status_checks


def branch_has_status_checks(payload: Any, branch: str) -> bool:
    if not isinstance(payload, dict) or payload.get("name") != branch:
        raise InspectionError("Default-branch response is invalid.")
    protected = payload.get("protected")
    if not isinstance(protected, bool):
        raise InspectionError("Default-branch response has an invalid protected value.")
    protection = payload.get("protection")
    if not isinstance(protection, dict):
        raise InspectionError("Default-branch response has no protection mapping.")
    required_status_checks = protection.get("required_status_checks")
    if not isinstance(required_status_checks, dict):
        raise InspectionError(
            "Default-branch response has no required-status-checks mapping."
        )
    has_status_checks = status_check_contexts(
        required_status_checks.get("contexts"), "Default-branch protection"
    )
    if has_status_checks and not protected:
        raise InspectionError(
            "Default-branch response reports status checks on an unprotected branch."
        )
    return has_status_checks


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Merge-settings preflight supports GitHub.com only.")
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
        raise InspectionError(
            "Archived repositories cannot have merge settings changed."
        )
    if require_boolean(repository, "disabled"):
        raise InspectionError(
            "Disabled repositories cannot have merge settings changed."
        )
    permissions = repository.get("permissions")
    if not isinstance(permissions, dict) or "admin" not in permissions:
        raise InspectionError(
            "Repository administration permission is required to change merge settings."
        )
    if not require_boolean(permissions, "admin"):
        raise InspectionError(
            "Repository administration permission is required to change merge settings."
        )

    rules = client.json(
        f"repos/{owner}/{repo}/rules/branches/"
        f"{quote(args.default_branch, safe='')}?per_page=100"
    )
    (
        required_methods,
        has_merge_queue,
        ruleset_status_checks_required,
    ) = parse_effective_rules(rules)
    desired = {
        "squash": True,
        "merge": "merge" in required_methods,
        "rebase": "rebase" in required_methods,
    }
    current = {
        "squash": require_boolean(repository, "allow_squash_merge"),
        "merge": require_boolean(repository, "allow_merge_commit"),
        "rebase": require_boolean(repository, "allow_rebase_merge"),
    }
    auto_merge_enabled = require_boolean(repository, "allow_auto_merge")
    disabled_methods = sorted(
        method for method, enabled in current.items() if enabled and not desired[method]
    )
    classic_status_checks_required: bool | None = None
    status_checks_required: bool | None = None
    if args.require_auto_merge_workflows and not has_merge_queue and auto_merge_enabled:
        if ruleset_status_checks_required:
            status_checks_required = True
        else:
            default_branch = client.json(
                f"repos/{owner}/{repo}/branches/{quote(args.default_branch, safe='')}"
            )
            classic_status_checks_required = branch_has_status_checks(
                default_branch, args.default_branch
            )
            status_checks_required = classic_status_checks_required
    auto_merge_workflows_eligible = (
        not has_merge_queue
        and auto_merge_enabled
        and (not args.require_auto_merge_workflows or status_checks_required is True)
    )
    if disabled_methods and not args.confirm_disable_merge_methods:
        decision = "require-explicit-merge-method-removal-confirmation"
    elif args.require_auto_merge_workflows and has_merge_queue:
        decision = "skip-auto-merge-workflows"
    elif args.require_auto_merge_workflows and not auto_merge_enabled:
        decision = "enable-auto-merge-before-installing-workflows"
    elif args.require_auto_merge_workflows and not status_checks_required:
        decision = "require-status-checks-before-installing-auto-merge-workflows"
    else:
        decision = "may-configure-merge-settings"
    return {
        "inspection_complete": True,
        "decision": decision,
        "required_merge_methods": sorted(required_methods),
        "current_merge_methods": current,
        "desired_merge_methods": desired,
        "methods_to_disable": disabled_methods,
        "merge_queue_applies": has_merge_queue,
        "ruleset_status_checks_required": ruleset_status_checks_required,
        "classic_status_checks_required": classic_status_checks_required,
        "status_checks_required": status_checks_required,
        "administration_permission": True,
        "auto_merge_enabled": auto_merge_enabled,
        "auto_merge_workflows_eligible": auto_merge_workflows_eligible,
        "github_api_requests": client.request_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--require-auto-merge-workflows", action="store_true")
    parser.add_argument("--confirm-disable-merge-methods", action="store_true")
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
