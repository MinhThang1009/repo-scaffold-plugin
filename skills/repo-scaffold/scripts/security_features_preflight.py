#!/usr/bin/env python3
"""Fail-closed preflight for GitHub repository security-feature mutations."""

from __future__ import annotations

import argparse
import json
from typing import Any

from codeql_preflight import GitHubClient, InspectionError, split_repository


SECURITY_FEATURES = (
    "dependabot_alerts",
    "automated_security_fixes",
    "secret_scanning",
    "push_protection",
    "private_vulnerability_reporting",
)
SECURITY_ANALYSIS_FIELDS = (
    "dependabot_security_updates",
    "secret_scanning",
    "secret_scanning_push_protection",
)


def require_boolean(document: dict[str, Any], field: str) -> bool:
    value = document.get(field)
    if not isinstance(value, bool):
        raise InspectionError(f"Repository response has an invalid {field!r} value.")
    return value


def security_statuses(document: dict[str, Any]) -> dict[str, str | None]:
    analysis = document.get("security_and_analysis")
    if not isinstance(analysis, dict):
        raise InspectionError(
            "Repository response has no security_and_analysis mapping."
        )
    statuses: dict[str, str | None] = {}
    for field in SECURITY_ANALYSIS_FIELDS:
        value = analysis.get(field)
        if value is None:
            statuses[field] = None
            continue
        if not isinstance(value, dict) or value.get("status") not in {
            "enabled",
            "disabled",
        }:
            raise InspectionError(
                f"Repository security_and_analysis has an invalid {field!r} value."
            )
        statuses[field] = value["status"]
    return statuses


def requested_features(args: argparse.Namespace) -> list[str]:
    requested = [
        feature for feature in SECURITY_FEATURES if getattr(args, feature) is True
    ]
    if not requested:
        raise InspectionError("Select at least one security feature to enable.")
    return requested


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Security-feature preflight supports GitHub.com only.")
    owner, repo = split_repository(args.repository)
    requested = requested_features(args)
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
            "Archived repositories cannot have security settings changed."
        )
    is_fork = require_boolean(repository, "fork")
    visibility = repository.get("visibility")
    if visibility not in {"public", "private", "internal"}:
        raise InspectionError("Repository response has an invalid visibility value.")
    owner_document = repository.get("owner")
    owner_type = (
        owner_document.get("type") if isinstance(owner_document, dict) else None
    )
    if owner_type not in {"User", "Organization"}:
        raise InspectionError("Repository response has an unsupported owner type.")
    statuses = security_statuses(repository)

    if (
        args.push_protection
        and not args.secret_scanning
        and statuses["secret_scanning"] != "enabled"
    ):
        raise InspectionError(
            "Push protection requires secret scanning to be enabled first."
        )
    if args.private_vulnerability_reporting and (visibility != "public" or is_fork):
        raise InspectionError(
            "Private vulnerability reporting is limited to public non-fork repositories."
        )

    return {
        "inspection_complete": True,
        "decision": "may-configure-security-features",
        "requested_features": requested,
        "repository": args.repository,
        "visibility": visibility,
        "is_fork": is_fork,
        "owner_type": owner_type,
        "security_and_analysis": statuses,
        "github_api_requests": client.request_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--hostname", default="github.com")
    for feature in SECURITY_FEATURES:
        parser.add_argument(
            "--enable-" + feature.replace("_", "-"),
            dest=feature,
            action="store_true",
        )
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
