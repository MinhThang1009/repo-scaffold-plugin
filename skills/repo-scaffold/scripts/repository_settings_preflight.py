#!/usr/bin/env python3
"""Fail-closed preflight for basic GitHub repository-setting mutations."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from codeql_preflight import (
    request_metrics,
    GitHubClient,
    InspectionError,
    split_repository,
    verified_repository_id,
)


MUTATIONS = (
    "description",
    "topics",
    "issues",
    "discussions",
    "labels",
)
MAX_REPOSITORY_TOPICS = 20
TOPIC_NAME = re.compile(r"[a-z0-9-]{1,50}\Z", re.IGNORECASE | re.ASCII)


def require_boolean(document: dict[str, Any], field: str) -> bool:
    value = document.get(field)
    if not isinstance(value, bool):
        raise InspectionError(f"Repository response has an invalid {field!r} value.")
    return value


def validated_topics(values: object) -> list[str]:
    """Validate GitHub's bounded topic-name collection without changing requests."""
    if not isinstance(values, list):
        raise InspectionError("Topics must be strings in an array.")
    if len(values) > MAX_REPOSITORY_TOPICS:
        raise InspectionError("A repository may have at most 20 topics.")
    topics: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise InspectionError("Topics must be strings.")
        if TOPIC_NAME.fullmatch(value) is None:
            raise InspectionError(
                "Topics must be non-empty single tokens using letters, numbers, "
                "and hyphens, with at most 50 characters."
            )
        topics.append(value)
    if len({topic.casefold() for topic in topics}) != len(topics):
        raise InspectionError("Topics must be unique without regard to case.")
    return topics


def requested_mutations(args: argparse.Namespace) -> list[str]:
    if not args.topics and args.topic:
        raise InspectionError("Topics were supplied without requesting a topic change.")
    requested = [
        mutation
        for mutation in MUTATIONS
        if (mutation == "labels" and bool(args.create_label))
        or (mutation != "labels" and getattr(args, mutation) is True)
    ]
    if not requested:
        raise InspectionError("Select at least one repository setting to change.")
    if args.description and (
        not isinstance(args.description_value, str)
        or not args.description_value.strip()
    ):
        raise InspectionError("A non-empty description is required when requested.")
    if args.topics:
        if not args.topic:
            raise InspectionError(
                "Provide at least one topic when topics are requested."
            )
        validated_topics(args.topic)
    if args.create_label:
        if any(
            not isinstance(label, str) or not label.strip()
            for label in args.create_label
        ):
            raise InspectionError("Labels must be non-empty strings.")
        normalized_labels = [label.casefold() for label in args.create_label]
        if len(set(normalized_labels)) != len(normalized_labels):
            raise InspectionError("Labels must be unique without regard to case.")
    return requested


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Repository-settings preflight supports GitHub.com only.")
    requested = requested_mutations(args)
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
    repository_id = verified_repository_id(
        repository, getattr(args, "expected_repository_id", None)
    )
    if require_boolean(repository, "archived"):
        raise InspectionError("Archived repositories cannot have settings changed.")
    if require_boolean(repository, "disabled"):
        raise InspectionError("Disabled repositories cannot have settings changed.")
    permissions = repository.get("permissions")
    if not isinstance(permissions, dict) or "admin" not in permissions:
        raise InspectionError(
            "Repository administration permission is required to change settings."
        )
    if not require_boolean(permissions, "admin"):
        raise InspectionError(
            "Repository administration permission is required to change settings."
        )

    current_topics = validated_topics(repository.get("topics")) if args.topics else None
    if current_topics is not None:
        resulting_topics = {topic.casefold() for topic in current_topics + args.topic}
        if len(resulting_topics) > MAX_REPOSITORY_TOPICS:
            raise InspectionError(
                "The combined topic set exceeds GitHub's 20-topic limit; "
                "preserve existing topics and revise the requested additions."
            )

    current_features = {
        "issues": require_boolean(repository, "has_issues"),
        "discussions": require_boolean(repository, "has_discussions"),
    }
    requested_settings = {
        "description": args.description_value if args.description else None,
        "topics": list(args.topic) if args.topics else [],
        "labels": list(args.create_label),
        "issues": args.issues,
        "discussions": args.discussions,
    }
    return {
        "inspection_complete": True,
        "decision": (
            "bind-repository-identity-before-mutation"
            if getattr(args, "expected_repository_id", None) is None
            else "may-configure-repository-settings"
        ),
        "requested_mutations": requested,
        "requested_settings": requested_settings,
        "repository": args.repository,
        "repository_id": repository_id,
        "current_features": current_features,
        "current_topics": current_topics,
        **request_metrics(client.request_count),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--expected-repository-id", type=int)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--set-description", dest="description", action="store_true")
    parser.add_argument("--description", dest="description_value")
    parser.add_argument("--set-topics", dest="topics", action="store_true")
    parser.add_argument("--topic", action="append", default=[])
    parser.add_argument("--create-label", action="append", default=[])
    parser.add_argument("--enable-issues", dest="issues", action="store_true")
    parser.add_argument("--enable-discussions", dest="discussions", action="store_true")
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
