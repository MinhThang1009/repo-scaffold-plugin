#!/usr/bin/env python3
"""Fail-closed required-check preflight for classic branch protection."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import yaml

from codeql_preflight import (
    FULL_OBJECT_ID,
    GitHubClient,
    InspectionError,
    UniqueKeyBaseLoader,
    is_direct_workflow_path,
    split_repository,
)


MAX_WORKFLOWS = 500
MAX_WORKFLOW_BYTES = 5 * 1024 * 1024
MAX_TOTAL_WORKFLOW_BYTES = 64 * 1024 * 1024
CONTEXT = re.compile(r"^[^\r\n\x00]{1,256}$")


def parse_workflow(text: str, source: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_WORKFLOW_BYTES:
        raise InspectionError(f"Workflow {source!r} exceeds the byte safety cap.")
    try:
        document = yaml.load(text, Loader=UniqueKeyBaseLoader)
    except (yaml.YAMLError, InspectionError, RecursionError) as exc:
        raise InspectionError(f"Could not parse workflow {source!r}: {exc}") from exc
    if not isinstance(document, dict):
        raise InspectionError(f"Workflow {source!r} is not a YAML mapping.")
    return document


def event_covers(
    document: dict[str, Any], event: str, default_branch: str | None = None
) -> bool:
    """Return whether a required check covers the relevant protected events.

    A trusted ``pull_request_target`` workflow is the safe equivalent of a
    ``pull_request`` producer for this preflight. A branch filter is accepted
    only when it is the verified default branch, and path filters remain
    unsupported because they can silently suppress a required check.
    """
    candidate_events = (
        ("pull_request", "pull_request_target") if event == "pull_request" else (event,)
    )
    triggers = document.get("on")
    if isinstance(triggers, list):
        return any(candidate in triggers for candidate in candidate_events)
    if not isinstance(triggers, dict):
        return False
    for candidate in candidate_events:
        if candidate not in triggers:
            continue
        value = triggers[candidate]
        if value is None or value == "":
            return True
        if not isinstance(value, dict):
            continue
        if any(key in value for key in ("paths", "paths-ignore", "branches-ignore")):
            continue
        branches = value.get("branches")
        if branches is not None and (
            default_branch is None or branches != [default_branch]
        ):
            continue
        types = value.get("types")
        if types is None:
            return True
        if not isinstance(types, list) or not all(
            isinstance(event_type, str) for event_type in types
        ):
            continue
        if candidate in {"pull_request", "pull_request_target"}:
            if {
                "opened",
                "edited",
                "reopened",
                "synchronize",
            }.issubset(set(types)):
                return True
        elif "checks_requested" in types:
            return True
    return False


@dataclass(frozen=True)
class Producer:
    context: str
    identity: str
    workflow_blob_sha: str
    pull_request_coverage: bool
    pull_request_target_coverage: bool
    merge_group_coverage: bool
    unconditional: bool
    executable: bool


def workflow_producers(
    client: GitHubClient,
    owner: str,
    repo: str,
    commit: str,
    default_branch: str | None = None,
) -> list[Producer]:
    tree = client.json(f"repos/{owner}/{repo}/git/trees/{commit}?recursive=1")
    if not isinstance(tree, dict) or tree.get("truncated") is not False:
        raise InspectionError("Workflow tree is missing, invalid, or truncated.")
    entries = tree.get("tree")
    if not isinstance(entries, list):
        raise InspectionError("Workflow tree has no tree array.")
    workflows: list[dict[str, Any]] = []
    seen_workflow_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        path = entry["path"]
        pure_path = PurePosixPath(path)
        if pure_path.parent != PurePosixPath(".github/workflows"):
            continue
        if pure_path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if entry.get("type") != "blob":
            raise InspectionError(f"Workflow entry is not a blob: {path!r}")
        if not is_direct_workflow_path(path):
            raise InspectionError(f"Workflow path is not canonical: {path!r}")
        if path in seen_workflow_paths:
            raise InspectionError(f"Workflow path appears more than once: {path!r}")
        seen_workflow_paths.add(path)
        workflows.append(entry)
    if len(workflows) > MAX_WORKFLOWS:
        raise InspectionError("Workflow count exceeds the safety cap.")
    producers: list[Producer] = []
    total_bytes = 0
    for entry in workflows:
        blob = entry.get("sha")
        path = entry["path"]
        if not isinstance(blob, str) or not FULL_OBJECT_ID.fullmatch(blob):
            raise InspectionError(f"Workflow {path!r} has an invalid blob ID.")
        text = client.raw(f"repos/{owner}/{repo}/git/blobs/{blob}")
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_TOTAL_WORKFLOW_BYTES:
            raise InspectionError(
                "Workflow inspection exceeded the total byte safety cap."
            )
        document = parse_workflow(text, f"{path}@{commit}")
        jobs = document.get("jobs")
        if not isinstance(jobs, dict):
            raise InspectionError(f"Workflow {path!r} has no jobs mapping.")
        pull_request_coverage = event_covers(document, "pull_request", default_branch)
        pull_request_target_coverage = event_covers(
            document, "pull_request_target", default_branch
        )
        merge_group_coverage = event_covers(document, "merge_group", default_branch)
        for job_id, job in jobs.items():
            if not isinstance(job_id, str) or not isinstance(job, dict):
                raise InspectionError(f"Workflow {path!r} has an invalid job entry.")
            context = job.get("name", job_id)
            if not isinstance(context, str) or not context or "${{" in context:
                continue
            steps = job.get("steps")
            executable_steps = steps if isinstance(steps, list) else []
            step_controls_are_safe = isinstance(steps, list) and all(
                isinstance(step, dict)
                and not {"if", "continue-on-error"}.intersection(step)
                for step in executable_steps
            )
            executable = (
                "uses" not in job
                and isinstance(job.get("runs-on"), str)
                and step_controls_are_safe
                and any(
                    isinstance(step, dict)
                    and isinstance(step.get("uses") or step.get("run"), str)
                    and bool(step.get("uses") or step.get("run"))
                    for step in executable_steps
                )
            )
            producers.append(
                Producer(
                    context=context,
                    identity=f"{path}#{job_id}",
                    workflow_blob_sha=blob,
                    pull_request_coverage=pull_request_coverage,
                    pull_request_target_coverage=pull_request_target_coverage,
                    merge_group_coverage=merge_group_coverage,
                    unconditional=job.get("if")
                    in (None, "${{ always() }}", "always()"),
                    executable=executable and "continue-on-error" not in job,
                )
            )
    return producers


def app_id_for_check(payload: Any, context: str, now: datetime) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("check_runs"), list):
        raise InspectionError("Check Runs response is invalid.")
    check_runs = payload["check_runs"]
    total = payload.get("total_count")
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
        or total > 100
        or total != len(check_runs)
        or not all(isinstance(item, dict) for item in check_runs)
    ):
        raise InspectionError(
            "Check Runs response has an invalid or incomplete bounded result."
        )
    matches = [
        item
        for item in check_runs
        if str(item.get("name", "")).casefold() == context.casefold()
    ]
    if not matches:
        raise InspectionError(f"Required check {context!r} has no Check Run evidence.")
    app_ids: set[int] = set()
    success = False
    for item in matches:
        app = item.get("app")
        app_id = app.get("id") if isinstance(app, dict) else None
        completed_at = item.get("completed_at")
        if (
            not isinstance(app_id, int)
            or isinstance(app_id, bool)
            or app_id <= 0
            or not isinstance(completed_at, str)
        ):
            raise InspectionError(
                f"Required check {context!r} has incomplete Check Run evidence."
            )
        try:
            completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InspectionError(
                f"Required check {context!r} has an invalid completion time."
            ) from exc
        if completed.tzinfo is None:
            raise InspectionError(
                f"Required check {context!r} has a timezone-less completion time."
            )
        app_ids.add(app_id)
        success |= item.get("conclusion") == "success" and (
            now - timedelta(days=7) <= completed <= now
        )
    if len(app_ids) != 1:
        raise InspectionError(
            f"Required check {context!r} has conflicting GitHub App IDs."
        )
    if not success:
        raise InspectionError(
            f"Required check {context!r} has no successful recent Check Run."
        )
    return next(iter(app_ids))


def inspect_evidence(
    client: GitHubClient,
    owner: str,
    repo: str,
    sha: str,
    context: str,
    now: datetime,
) -> int:
    if not FULL_OBJECT_ID.fullmatch(sha):
        raise InspectionError("A controlling SHA is not a full Git object ID.")
    checks = client.json(f"repos/{owner}/{repo}/commits/{sha}/check-runs?per_page=100")
    statuses = client.json(f"repos/{owner}/{repo}/commits/{sha}/status?per_page=100")
    if not isinstance(statuses, dict) or not isinstance(statuses.get("statuses"), list):
        raise InspectionError("Combined status response is invalid.")
    status_entries = statuses["statuses"]
    total = statuses.get("total_count")
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
        or total > 100
        or total != len(status_entries)
        or not all(isinstance(status, dict) for status in status_entries)
    ):
        raise InspectionError(
            "Combined status response has an invalid or incomplete bounded result."
        )
    if any(
        str(status.get("context", "")).casefold() == context.casefold()
        for status in status_entries
    ):
        raise InspectionError(
            f"Required check {context!r} collides with a Commit Status on a controlling SHA."
        )
    return app_id_for_check(checks, context, now)


def validate_contexts(values: list[str]) -> list[str]:
    if not values or len(values) > 50:
        raise InspectionError("Provide between one and 50 required checks.")
    if any(not CONTEXT.fullmatch(value) for value in values):
        raise InspectionError("Required check contexts must be non-empty single lines.")
    if len({value.casefold() for value in values}) != len(values):
        raise InspectionError("Required check contexts must be unique.")
    return values


def require_boolean(document: dict[str, Any], field: str) -> bool:
    """Read a repository boolean without accepting truthy replacement values."""
    value = document.get(field)
    if not isinstance(value, bool):
        raise InspectionError(f"Repository response has an invalid {field!r} value.")
    return value


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Branch-protection preflight supports GitHub.com only.")
    owner, repo = split_repository(args.repository)
    contexts = validate_contexts(args.required_check)
    if (
        not isinstance(args.default_branch, str)
        or not args.default_branch.strip()
        or any(character in args.default_branch for character in "\r\n\x00")
    ):
        raise InspectionError("Default branch must be a non-empty single line.")
    if not isinstance(args.pull_request, int) or args.pull_request <= 0:
        raise InspectionError("Pull request number must be positive.")
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
        raise InspectionError("Archived repositories cannot have protection changed.")
    if require_boolean(repository, "disabled"):
        raise InspectionError("Disabled repositories cannot have protection changed.")
    permissions = repository.get("permissions")
    if not isinstance(permissions, dict) or "admin" not in permissions:
        raise InspectionError(
            "Repository administration permission is required to change protection."
        )
    if not require_boolean(permissions, "admin"):
        raise InspectionError(
            "Repository administration permission is required to change protection."
        )
    if repository.get("default_branch") != args.default_branch:
        raise InspectionError(
            "Requested branch is not the verified current default branch."
        )
    pr = client.json(f"repos/{owner}/{repo}/pulls/{args.pull_request}")
    if not isinstance(pr, dict):
        raise InspectionError("Pull request response is invalid.")
    if pr.get("state") != "open":
        raise InspectionError("Representative pull request is not open.")
    head = pr.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    merge_sha = pr.get("merge_commit_sha")
    if not isinstance(head_sha, str) or not isinstance(merge_sha, str):
        raise InspectionError(
            "Representative pull request has no head or test-merge SHA."
        )
    if pr.get("mergeable") is not True:
        raise InspectionError("Representative pull request is not confirmed mergeable.")
    base = pr.get("base")
    base_repo = base.get("repo") if isinstance(base, dict) else None
    base_name = base_repo.get("full_name") if isinstance(base_repo, dict) else None
    if (
        not isinstance(base, dict)
        or base.get("ref") != args.default_branch
        or not isinstance(base_name, str)
        or base_name.casefold() != args.repository.casefold()
    ):
        raise InspectionError(
            "Representative pull request does not verify the target repository and branch."
        )
    base_sha = base.get("sha") if isinstance(base, dict) else None
    rules = client.json(
        f"repos/{owner}/{repo}/rules/branches/"
        f"{quote(args.default_branch, safe='')}?per_page=100"
    )
    if not isinstance(rules, list):
        raise InspectionError("Effective rules response is invalid.")
    if len(rules) >= 100:
        raise InspectionError(
            "Effective rules response may be paginated; inspection is inconclusive."
        )
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("type"), str)
            or not rule["type"].strip()
        ):
            raise InspectionError("Effective rule has an invalid or missing type.")
    queue_required = any(rule["type"] == "merge_queue" for rule in rules)
    producers = workflow_producers(client, owner, repo, head_sha, args.default_branch)
    target_contexts = {
        context.casefold()
        for context in contexts
        if any(
            producer.context.casefold() == context.casefold()
            and producer.pull_request_target_coverage
            for producer in producers
        )
    }
    base_producers: list[Producer] | None = None
    if target_contexts:
        if not isinstance(base_sha, str) or not FULL_OBJECT_ID.fullmatch(base_sha):
            raise InspectionError(
                "Representative pull request has no verified base commit for its "
                "pull_request_target producer."
            )
        current_default_commit = client.json(
            f"repos/{owner}/{repo}/commits/{quote(args.default_branch, safe='')}"
        )
        current_default_sha = (
            current_default_commit.get("sha")
            if isinstance(current_default_commit, dict)
            else None
        )
        if current_default_sha != base_sha:
            raise InspectionError(
                "Representative pull request base does not match the current "
                "default branch commit."
            )
        base_producers = workflow_producers(
            client, owner, repo, base_sha, args.default_branch
        )
    now = datetime.now(timezone.utc)
    verified: list[dict[str, Any]] = []
    for context in contexts:
        matches = [
            producer
            for producer in producers
            if producer.context.casefold() == context.casefold()
        ]
        if len(matches) != 1:
            raise InspectionError(
                f"Required check {context!r} has {len(matches)} workflow producers."
            )
        producer = matches[0]
        if (
            not producer.pull_request_coverage
            or not producer.unconditional
            or not producer.executable
        ):
            raise InspectionError(
                f"Required check {context!r} is not an unconditional executable pull-request gate."
            )
        if producer.pull_request_target_coverage:
            matching_base_producers = [
                candidate
                for candidate in base_producers or []
                if candidate.context.casefold() == context.casefold()
            ]
            if (
                len(matching_base_producers) != 1
                or matching_base_producers[0].identity != producer.identity
                or matching_base_producers[0].workflow_blob_sha
                != producer.workflow_blob_sha
            ):
                raise InspectionError(
                    f"Required check {context!r} has a pull_request_target producer "
                    "that is missing from or differs from the verified base branch."
                )
        if queue_required and not producer.merge_group_coverage:
            raise InspectionError(
                f"Required check {context!r} lacks merge_group coverage."
            )
        head_app = inspect_evidence(client, owner, repo, head_sha, context, now)
        merge_app = inspect_evidence(client, owner, repo, merge_sha, context, now)
        if head_app != merge_app:
            raise InspectionError(
                f"Required check {context!r} changes GitHub App between controlling SHAs."
            )
        verified.append(
            {"context": context, "app_id": head_app, "producer": producer.identity}
        )
    return {
        "inspection_complete": True,
        "decision": "may-configure-classic-protection",
        "repository": args.repository,
        "default_branch": args.default_branch,
        "administration_permission": True,
        "pull_request": args.pull_request,
        "head_sha": head_sha,
        "test_merge_sha": merge_sha,
        "merge_queue_required": queue_required,
        "required_checks": verified,
        "github_api_requests": client.request_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--pull-request", required=True, type=int)
    parser.add_argument("--required-check", action="append", default=[])
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
