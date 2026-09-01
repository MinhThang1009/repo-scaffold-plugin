#!/usr/bin/env python3
"""Fail-closed required-check preflight for classic branch protection."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

try:
    import yaml
except ImportError as exc:
    print(
        json.dumps(
            {
                "inspection_complete": False,
                "decision": "inconclusive",
                "error": "PyYAML is required.",
            }
        )
    )
    raise SystemExit(2) from exc


MAX_GH_REQUESTS = 750
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_WORKFLOWS = 500
MAX_WORKFLOW_BYTES = 5 * 1024 * 1024
MAX_TOTAL_WORKFLOW_BYTES = 64 * 1024 * 1024
MAX_INSPECTION_SECONDS = 600
GH_REQUEST_TIMEOUT_SECONDS = 60
MAX_JSON_NESTING = 100
FULL_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.IGNORECASE)
CONTEXT = re.compile(r"^[^\r\n\x00]{1,256}$")


class InspectionError(RuntimeError):
    """Raised when the preflight cannot prove that protection is safe."""


class DuplicateJsonMember(ValueError):
    """Raised for ambiguous JSON objects."""


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def require_json_nesting_within_limit(payload: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in payload:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING:
                raise InspectionError("GitHub API JSON exceeds the nesting safety cap.")
        elif character in "]}":
            depth -= 1


class UniqueKeyBaseLoader(yaml.BaseLoader):
    """YAML loader that rejects duplicate mapping keys without type coercion."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise InspectionError(
                    "Workflow has an unhashable mapping key."
                ) from exc
            if duplicate:
                raise InspectionError(f"Workflow has duplicate key {key!r}.")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def resolve_path_executable(name: str) -> str | None:
    """Resolve a tool from an absolute PATH entry outside the current directory."""
    forbidden = Path.cwd().resolve(strict=True)
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_directory:
            continue
        directory = Path(raw_directory.strip('"'))
        if not directory.is_absolute():
            continue
        candidate = shutil.which(name, path=str(directory))
        if candidate is None:
            continue
        try:
            resolved = Path(candidate).resolve(strict=True)
            resolved.relative_to(forbidden)
        except ValueError:
            return str(resolved)
        except (OSError, RuntimeError):
            continue
    return None


class GitHubClient:
    """Bounded GitHub CLI API reader that never mutates repository state."""

    def __init__(self, hostname: str) -> None:
        executable = resolve_path_executable("gh")
        if executable is None:
            raise InspectionError(
                "GitHub CLI was not found on an absolute PATH entry outside the working directory."
            )
        self.executable = executable
        self.hostname = hostname
        self.request_count = 0
        self.response_bytes = 0
        self.deadline = time.monotonic() + MAX_INSPECTION_SECONDS

    @staticmethod
    def _read(stream: Any, label: str) -> tuple[str, int]:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        if size > MAX_RESPONSE_BYTES:
            raise InspectionError(f"GitHub API {label} exceeds the byte safety cap.")
        stream.seek(0)
        try:
            return stream.read().decode("utf-8"), size
        except UnicodeDecodeError as exc:
            raise InspectionError(f"GitHub API {label} is not valid UTF-8.") from exc

    def json(self, endpoint: str) -> Any:
        if self.request_count >= MAX_GH_REQUESTS:
            raise InspectionError(
                "GitHub API inspection exceeded the request safety cap."
            )
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise InspectionError("GitHub API inspection exceeded the time safety cap.")
        self.request_count += 1
        command = [self.executable, "api", "--hostname", self.hostname, endpoint]
        environment = os.environ.copy()
        environment.update({"GH_PAGER": "cat", "NO_COLOR": "1"})
        try:
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                result = subprocess.run(
                    command,
                    check=False,
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                    timeout=min(GH_REQUEST_TIMEOUT_SECONDS, max(1, int(remaining))),
                )
                output, output_size = self._read(stdout, "response")
                error, error_size = self._read(stderr, "error response")
        except FileNotFoundError as exc:
            raise InspectionError(
                "GitHub CLI is not installed or not on PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise InspectionError(
                f"GitHub API request timed out for {endpoint!r}."
            ) from exc
        self.response_bytes += output_size + error_size
        if self.response_bytes > MAX_TOTAL_RESPONSE_BYTES:
            raise InspectionError(
                "GitHub API inspection exceeded the total response byte cap."
            )
        if result.returncode != 0:
            raise InspectionError(
                f"GitHub API request failed for {endpoint!r}: {(error or output).strip()}"
            )
        require_json_nesting_within_limit(output)
        try:
            return json.loads(output, object_pairs_hook=unique_json_object)
        except (ValueError, RecursionError) as exc:
            raise InspectionError(
                f"GitHub API returned invalid JSON for {endpoint!r}."
            ) from exc


def split_repository(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if (
        len(parts) != 2
        or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
        or any(part in {".", ".."} for part in parts)
    ):
        raise InspectionError("Repository must be an explicit OWNER/REPO identifier.")
    return parts[0], parts[1]


def parse_workflow(text: str, source: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_WORKFLOW_BYTES:
        raise InspectionError(f"Workflow {source!r} exceeds the byte safety cap.")
    try:
        loader = UniqueKeyBaseLoader(text)
        try:
            document = loader.get_single_data()
        finally:
            loader.dispose()
    except (yaml.YAMLError, InspectionError, RecursionError) as exc:
        raise InspectionError(f"Could not parse workflow {source!r}: {exc}") from exc
    if not isinstance(document, dict):
        raise InspectionError(f"Workflow {source!r} is not a YAML mapping.")
    return document


def event_covers(document: dict[str, Any], event: str) -> bool:
    triggers = document.get("on")
    if isinstance(triggers, list):
        return event in triggers
    if not isinstance(triggers, dict) or event not in triggers:
        return False
    value = triggers[event]
    if value is None or value == "":
        return True
    if not isinstance(value, dict):
        return False
    for key in ("branches", "branches-ignore", "paths", "paths-ignore"):
        if key in value:
            return False
    types = value.get("types")
    if types is None:
        return True
    if event == "pull_request":
        return isinstance(types, list) and {
            "opened",
            "edited",
            "reopened",
            "synchronize",
        }.issubset(set(types))
    return isinstance(types, list) and "checks_requested" in types


def effective_job_name(job_id: str, job: dict[str, Any]) -> str | None:
    name = job.get("name", job_id)
    if not isinstance(name, str) or not name or "${{" in name:
        return None
    return name


def job_is_unconditional(job: dict[str, Any]) -> bool:
    condition = job.get("if")
    return condition is None or condition in {"${{ always() }}", "always()"}


def job_has_executable_steps(job: dict[str, Any]) -> bool:
    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        return False
    return any(
        isinstance(step, dict)
        and isinstance(step.get("uses") or step.get("run"), str)
        and bool(step.get("uses") or step.get("run"))
        for step in steps
    )


@dataclass(frozen=True)
class Producer:
    context: str
    identity: str
    pull_request_coverage: bool
    merge_group_coverage: bool
    unconditional: bool
    executable: bool


def remote_workflow_producers(
    client: GitHubClient, owner: str, repo: str, commit: str
) -> list[Producer]:
    tree = client.json(f"repos/{owner}/{repo}/git/trees/{commit}?recursive=1")
    if not isinstance(tree, dict) or tree.get("truncated") is not False:
        raise InspectionError("Workflow tree is missing, invalid, or truncated.")
    entries = tree.get("tree")
    if not isinstance(entries, list):
        raise InspectionError("Workflow tree has no tree array.")
    workflows = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("type") == "blob"
        and isinstance(entry.get("path"), str)
        and PurePosixPath(entry["path"]).parent == PurePosixPath(".github/workflows")
        and PurePosixPath(entry["path"]).suffix.lower() in {".yml", ".yaml"}
    ]
    if len(workflows) > MAX_WORKFLOWS:
        raise InspectionError("Workflow count exceeds the safety cap.")
    producers: list[Producer] = []
    total_bytes = 0
    for entry in workflows:
        blob = entry.get("sha")
        path = entry["path"]
        if not isinstance(blob, str) or not FULL_OBJECT_ID.fullmatch(blob):
            raise InspectionError(f"Workflow {path!r} has an invalid blob ID.")
        response = client.json(f"repos/{owner}/{repo}/git/blobs/{blob}")
        if not isinstance(response, dict) or response.get("encoding") != "base64":
            raise InspectionError(f"Workflow {path!r} blob response is invalid.")
        content = response.get("content")
        if not isinstance(content, str):
            raise InspectionError(f"Workflow {path!r} blob content is invalid.")
        try:
            text = base64.b64decode("".join(content.split()), validate=True).decode(
                "utf-8"
            )
        except (ValueError, UnicodeError) as exc:
            raise InspectionError(
                f"Workflow {path!r} is not valid UTF-8 base64 content."
            ) from exc
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_TOTAL_WORKFLOW_BYTES:
            raise InspectionError(
                "Workflow inspection exceeded the total byte safety cap."
            )
        document = parse_workflow(text, f"{path}@{commit}")
        jobs = document.get("jobs")
        if not isinstance(jobs, dict):
            raise InspectionError(f"Workflow {path!r} has no jobs mapping.")
        pull_request_coverage = event_covers(document, "pull_request")
        merge_group_coverage = event_covers(document, "merge_group")
        for job_id, job in jobs.items():
            if not isinstance(job_id, str) or not isinstance(job, dict):
                raise InspectionError(f"Workflow {path!r} has an invalid job entry.")
            context = effective_job_name(job_id, job)
            if context is None:
                continue
            producers.append(
                Producer(
                    context=context,
                    identity=f"{path}#{job_id}",
                    pull_request_coverage=pull_request_coverage,
                    merge_group_coverage=merge_group_coverage,
                    unconditional=job_is_unconditional(job),
                    executable="uses" not in job
                    and isinstance(job.get("runs-on"), str)
                    and job_has_executable_steps(job),
                )
            )
    return producers


def check_run_app_ids(payload: Any, context: str, now: datetime) -> set[int]:
    if not isinstance(payload, dict) or not isinstance(payload.get("check_runs"), list):
        raise InspectionError("Check Runs response is invalid.")
    total_count = payload.get("total_count")
    if (
        not isinstance(total_count, int)
        or isinstance(total_count, bool)
        or total_count not in range(0, 101)
    ):
        raise InspectionError("Check Runs response requires unbounded pagination.")
    matches = [
        item
        for item in payload["check_runs"]
        if isinstance(item, dict)
        and str(item.get("name", "")).casefold() == context.casefold()
    ]
    if not matches:
        raise InspectionError(f"Required check {context!r} has no Check Run evidence.")
    app_ids: set[int] = set()
    successful = False
    for item in matches:
        app = item.get("app")
        app_id = app.get("id") if isinstance(app, dict) else None
        completed_at = item.get("completed_at")
        if (
            not isinstance(app_id, int)
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
        if item.get("conclusion") == "success" and completed >= now - timedelta(days=7):
            successful = True
    if len(app_ids) != 1:
        raise InspectionError(
            f"Required check {context!r} has conflicting GitHub App IDs."
        )
    if not successful:
        raise InspectionError(
            f"Required check {context!r} has no successful recent Check Run."
        )
    return app_ids


def assert_no_status_collision(payload: Any, context: str) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("statuses"), list):
        raise InspectionError("Combined status response is invalid.")
    total_count = payload.get("total_count")
    if (
        not isinstance(total_count, int)
        or isinstance(total_count, bool)
        or total_count not in range(0, 101)
    ):
        raise InspectionError("Combined status response requires unbounded pagination.")
    if any(
        isinstance(status, dict)
        and str(status.get("context", "")).casefold() == context.casefold()
        for status in payload["statuses"]
    ):
        raise InspectionError(
            f"Required check {context!r} collides with a Commit Status on a controlling SHA."
        )


def inspect_evidence(
    client: GitHubClient, owner: str, repo: str, sha: str, context: str, now: datetime
) -> int:
    if not FULL_OBJECT_ID.fullmatch(sha):
        raise InspectionError("A controlling SHA is not a full Git object ID.")
    checks = client.json(f"repos/{owner}/{repo}/commits/{sha}/check-runs?per_page=100")
    statuses = client.json(f"repos/{owner}/{repo}/commits/{sha}/status?per_page=100")
    app_ids = check_run_app_ids(checks, context, now)
    assert_no_status_collision(statuses, context)
    return next(iter(app_ids))


def has_merge_queue(client: GitHubClient, owner: str, repo: str, branch: str) -> bool:
    payload = client.json(
        f"repos/{owner}/{repo}/rules/branches/{quote(branch, safe='')}"
    )
    if not isinstance(payload, list):
        raise InspectionError("Effective rules response is invalid.")
    return any(
        isinstance(rule, dict) and rule.get("type") == "merge_queue" for rule in payload
    )


def validate_contexts(values: list[str]) -> list[str]:
    if not values or len(values) > 50:
        raise InspectionError("Provide between one and 50 required checks.")
    contexts: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not CONTEXT.fullmatch(value) or value.casefold() in seen:
            raise InspectionError(
                "Required check contexts must be unique, non-empty single lines."
            )
        seen.add(value.casefold())
        contexts.append(value)
    return contexts


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Branch-protection preflight supports GitHub.com only.")
    owner, repo = split_repository(args.repository)
    contexts = validate_contexts(args.required_check)
    if not isinstance(args.pull_request, int) or args.pull_request <= 0:
        raise InspectionError("Pull request number must be positive.")
    client = GitHubClient(args.hostname)
    pr = client.json(f"repos/{owner}/{repo}/pulls/{args.pull_request}")
    if not isinstance(pr, dict):
        raise InspectionError("Pull request response is invalid.")
    head = pr.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    merge_sha = pr.get("merge_commit_sha")
    if not isinstance(head_sha, str) or not isinstance(merge_sha, str):
        raise InspectionError(
            "Representative pull request has no head or test-merge SHA."
        )
    if pr.get("mergeable") is not True:
        raise InspectionError("Representative pull request is not confirmed mergeable.")
    queue_required = has_merge_queue(client, owner, repo, args.default_branch)
    producers = remote_workflow_producers(client, owner, repo, head_sha)
    now = datetime.now(UTC)
    verified: list[dict[str, Any]] = []
    for context in contexts:
        matching = [
            producer
            for producer in producers
            if producer.context.casefold() == context.casefold()
        ]
        if len(matching) != 1:
            raise InspectionError(
                f"Required check {context!r} has {len(matching)} workflow producers."
            )
        producer = matching[0]
        if (
            not producer.pull_request_coverage
            or not producer.unconditional
            or not producer.executable
        ):
            raise InspectionError(
                f"Required check {context!r} is not an unconditional executable pull-request gate."
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
