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


def parse_effective_rules(payload: Any) -> tuple[set[str], bool]:
    if not isinstance(payload, list):
        raise InspectionError("Effective rules response is invalid.")
    if len(payload) >= 100:
        raise InspectionError(
            "Effective rules response may be paginated; inspection is inconclusive."
        )

    required_methods: set[str] = set()
    has_merge_queue = False
    for rule in payload:
        if not isinstance(rule, dict):
            raise InspectionError("Effective rules response has an invalid rule.")
        rule_type = rule.get("type")
        if rule_type not in {"merge_queue", "pull_request"}:
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
    return required_methods, has_merge_queue


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

    rules = client.json(
        f"repos/{owner}/{repo}/rules/branches/"
        f"{quote(args.default_branch, safe='')}?per_page=100"
    )
    required_methods, has_merge_queue = parse_effective_rules(rules)
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
    disabled_methods = sorted(
        method for method, enabled in current.items() if enabled and not desired[method]
    )
    auto_merge_workflows_eligible = not has_merge_queue
    if disabled_methods and not args.confirm_disable_merge_methods:
        decision = "require-explicit-merge-method-removal-confirmation"
    elif args.require_auto_merge_workflows and not auto_merge_workflows_eligible:
        decision = "skip-auto-merge-workflows"
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
