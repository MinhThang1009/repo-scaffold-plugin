#!/usr/bin/env python3
"""Fail CI when an open code-scanning alert lacks an explicit disposition."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
DEFAULT_ALLOWLIST = Path(".github/code-scanning-allowlist.json")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PAGES = 20
MAX_ANALYSIS_PAGES = 20
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
PULL_REQUEST = re.compile(r"[1-9][0-9]*\Z")


class GateError(RuntimeError):
    """Raised when the gate cannot verify code-scanning alert state."""


class TransientGateError(GateError):
    """Raised when a bounded retry may recover a GitHub API request."""


class DuplicateJsonMember(ValueError):
    """Raised when a JSON document contains ambiguous duplicate members."""


@dataclass(frozen=True)
class AlertSelector:
    """One reviewed exception for an otherwise merge-blocking alert."""

    number: int
    tool: str
    rule: str
    path: str | None
    reason: str


@dataclass(frozen=True)
class Alert:
    """The minimum stable identity of one open code-scanning alert."""

    number: int
    tool: str
    rule: str
    path: str | None


def require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GateError(f"{field} must be a non-empty string")
    return value


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting ambiguous duplicate member names."""
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def load_allowlist(path: Path) -> tuple[AlertSelector, ...]:
    """Load a strict, reviewable allowlist from the checked-out base."""
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateJsonMember) as error:
        raise GateError(
            f"could not read code-scanning allowlist {path}: {error}"
        ) from error
    if not isinstance(document, dict) or document.get("schema-version") != 2:
        raise GateError("code-scanning allowlist must use schema-version 2")
    entries = document.get("allowlist")
    if not isinstance(entries, list):
        raise GateError("code-scanning allowlist allowlist must be a list")
    selectors: list[AlertSelector] = []
    seen: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "number",
            "tool",
            "rule",
            "path",
            "reason",
        }:
            raise GateError(
                "each code-scanning allowlist entry must have number, tool, rule, path, and reason"
            )
        number = entry["number"]
        if type(number) is not int or number < 1:
            raise GateError("code-scanning allowlist number must be a positive integer")
        path_value = entry["path"]
        if path_value is not None:
            path_value = require_text(path_value, field="code-scanning allowlist path")
            if Path(path_value).is_absolute() or ".." in Path(path_value).parts:
                raise GateError(
                    "code-scanning allowlist path must be repository-relative"
                )
        selector = AlertSelector(
            number,
            require_text(entry["tool"], field="code-scanning allowlist tool"),
            require_text(entry["rule"], field="code-scanning allowlist rule"),
            path_value,
            require_text(entry["reason"], field="code-scanning allowlist reason"),
        )
        if selector.number in seen:
            raise GateError("code-scanning allowlist must not repeat alert numbers")
        seen.add(selector.number)
        selectors.append(selector)
    return tuple(selectors)


def api_json(url: str, token: str) -> Any:
    """Read one bounded GitHub API response using the job token."""
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "repo-scaffold-code-scanning-gate",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub API host
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code in {408, 429, 500, 502, 503, 504}:
            raise TransientGateError(
                f"GitHub API request failed transiently: {error}"
            ) from error
        raise GateError(f"GitHub API request failed: {error}") from error
    except (URLError, OSError) as error:
        raise TransientGateError(
            f"GitHub API request failed transiently: {error}"
        ) from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise GateError("GitHub API response exceeds the allowed size")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GateError(f"GitHub API response is not valid JSON: {error}") from error


def merge_commit_has_parents(
    repository: str, sha: str, token: str, expected_parents: tuple[str, str]
) -> bool:
    """Return whether a GitHub-created merge commit has the expected PR parents."""
    document = api_json(f"{API_ROOT}/repos/{repository}/git/commits/{sha}", token)
    if not isinstance(document, dict):
        raise GateError("GitHub merge commit response must be an object")
    parents = document.get("parents")
    if not isinstance(parents, list):
        raise GateError("GitHub merge commit parents must be a list")
    parent_shas: list[str] = []
    for parent in parents:
        parent_sha = parent.get("sha") if isinstance(parent, dict) else None
        if not isinstance(parent_sha, str) or COMMIT_SHA.fullmatch(parent_sha) is None:
            raise GateError("GitHub merge commit parents must contain commit SHAs")
        parent_shas.append(parent_sha)
    return tuple(parent_shas) == expected_parents


def analyses_ready(
    repository: str,
    ref: str,
    sha: str,
    token: str,
    expected_categories: frozenset[str],
    expected_parents: tuple[str, str] | None = None,
) -> bool:
    """Return whether every configured CodeQL category is uploaded for this commit."""
    categories_by_sha: dict[str, set[str]] = {}
    for page in range(1, MAX_ANALYSIS_PAGES + 1):
        url = (
            f"{API_ROOT}/repos/{repository}/code-scanning/analyses?"
            f"ref={quote(ref, safe='')}&per_page=100&page={page}"
        )
        document = api_json(url, token)
        if not isinstance(document, list):
            raise GateError("GitHub analyses response must be a list")
        for item in document:
            if not isinstance(item, dict) or not isinstance(item.get("tool"), dict):
                continue
            analysis_sha = item.get("commit_sha")
            category = item.get("category")
            if (
                item["tool"].get("name") != "CodeQL"
                or not isinstance(analysis_sha, str)
                or COMMIT_SHA.fullmatch(analysis_sha) is None
                or not isinstance(category, str)
            ):
                continue
            categories_by_sha.setdefault(analysis_sha, set()).add(category)
        if len(document) < 100:
            break
    else:
        raise GateError(
            f"GitHub returned more than {MAX_ANALYSIS_PAGES * 100} analyses for {ref}"
        )
    if expected_parents is None:
        return expected_categories <= categories_by_sha.get(sha, set())
    return any(
        expected_categories <= categories
        and merge_commit_has_parents(repository, analysis_sha, token, expected_parents)
        for analysis_sha, categories in categories_by_sha.items()
    )


def pull_request_merge_sha(repository: str, number: str, token: str) -> str | None:
    """Return the current test-merge ref SHA, or ``None`` while GitHub computes it."""
    document = api_json(f"{API_ROOT}/repos/{repository}/pulls/{number}", token)
    if not isinstance(document, dict):
        raise GateError("GitHub pull request response must be an object")
    mergeable = document.get("mergeable")
    if mergeable is None:
        return None
    if mergeable is not True:
        raise GateError(f"pull request #{number} has no mergeable test commit")
    merge_ref = api_json(
        f"{API_ROOT}/repos/{repository}/git/ref/pull/{number}/merge", token
    )
    if not isinstance(merge_ref, dict):
        raise GateError("GitHub pull request merge ref response must be an object")
    merge_object = merge_ref.get("object")
    sha = merge_object.get("sha") if isinstance(merge_object, dict) else None
    if not isinstance(sha, str) or COMMIT_SHA.fullmatch(sha) is None:
        return None
    return sha


def wait_for_analyses(
    repository: str,
    ref: str,
    sha: str,
    token: str,
    attempts: int,
    delay: float,
    expected_categories: frozenset[str],
) -> None:
    """Wait briefly for the just-finished CodeQL uploads to become queryable."""
    for attempt in range(attempts):
        try:
            if analyses_ready(repository, ref, sha, token, expected_categories):
                return
        except TransientGateError:
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise GateError(
        f"CodeQL analyses for {ref} at {sha} were not queryable after {attempts} attempts"
    )


def wait_for_pull_request_analyses(
    repository: str,
    number: str,
    token: str,
    attempts: int,
    delay: float,
    expected_categories: frozenset[str],
    expected_parents: tuple[str, str],
) -> tuple[str, str]:
    """Wait for GitHub to create the test merge commit and receive CodeQL results."""
    ref = f"refs/pull/{number}/merge"
    for attempt in range(attempts):
        try:
            sha = pull_request_merge_sha(repository, number, token)
            if sha is not None and analyses_ready(
                repository, ref, sha, token, expected_categories, expected_parents
            ):
                return ref, sha
        except TransientGateError:
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise GateError(
        f"CodeQL analyses for pull request #{number} were not queryable after {attempts} attempts"
    )


def open_alerts(repository: str, ref: str, token: str) -> tuple[Alert, ...]:
    """Return every open alert for the exact ref, not stale default-branch alerts."""
    alerts: list[Alert] = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{API_ROOT}/repos/{repository}/code-scanning/alerts?state=open&ref={quote(ref, safe='')}&per_page=100&page={page}"
        document = api_json(url, token)
        if not isinstance(document, list):
            raise GateError("GitHub alerts response must be a list")
        for item in document:
            if not isinstance(item, dict):
                raise GateError("GitHub alert entry must be an object")
            tool = item.get("tool")
            rule = item.get("rule")
            instance = item.get("most_recent_instance")
            if (
                not isinstance(tool, dict)
                or not isinstance(rule, dict)
                or not isinstance(instance, dict)
            ):
                raise GateError("GitHub alert entry is missing its stable identity")
            location = instance.get("location")
            path = location.get("path") if isinstance(location, dict) else None
            if path is not None and not isinstance(path, str):
                raise GateError("GitHub alert path must be text or null")
            number = item.get("number")
            if type(number) is not int:
                raise GateError("GitHub alert number must be an integer")
            alerts.append(
                Alert(
                    number,
                    require_text(tool.get("name"), field="GitHub alert tool"),
                    require_text(rule.get("id"), field="GitHub alert rule"),
                    path,
                )
            )
        if len(document) < 100:
            return tuple(alerts)
    raise GateError(f"GitHub returned more than {MAX_PAGES * 100} open alerts")


def wait_for_open_alerts(
    repository: str, ref: str, token: str, attempts: int, delay: float
) -> tuple[Alert, ...]:
    """Retry transient alert-list failures without treating them as approved."""
    for attempt in range(attempts):
        try:
            return open_alerts(repository, ref, token)
        except TransientGateError:
            if attempt + 1 < attempts:
                time.sleep(delay)
    raise GateError(
        f"Open code-scanning alerts for {ref} were not queryable after {attempts} attempts"
    )


def unapproved_alerts(
    alerts: tuple[Alert, ...], selectors: tuple[AlertSelector, ...]
) -> list[Alert]:
    """Return alerts that have no exact reviewed selector."""
    allowed = {(item.number, item.tool, item.rule, item.path) for item in selectors}
    return [
        alert
        for alert in alerts
        if (alert.number, alert.tool, alert.rule, alert.path) not in allowed
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--pull-request", default=os.environ.get("PR_NUMBER"))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF"))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--base-sha", default=os.environ.get("PR_BASE_SHA"))
    parser.add_argument("--head-sha", default=os.environ.get("PR_HEAD_SHA"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--delay-seconds", type=float, default=5.0)
    parser.add_argument("--expected-codeql-category", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        repository = require_text(args.repository, field="repository")
        if REPOSITORY.fullmatch(repository) is None:
            raise GateError("repository must be an owner/name pair")
        token = require_text(args.token, field="token")
        if args.attempts < 1 or args.delay_seconds < 0:
            raise GateError("attempts must be positive and delay must be non-negative")
        expected_categories = frozenset(args.expected_codeql_category)
        if not expected_categories or any(
            not isinstance(category, str) or not category.strip()
            for category in expected_categories
        ):
            raise GateError("at least one expected CodeQL category is required")
        selectors = load_allowlist(args.allowlist)
        if args.pull_request is not None:
            number = require_text(args.pull_request, field="pull request number")
            if PULL_REQUEST.fullmatch(number) is None:
                raise GateError("pull request number must be a positive integer")
            base_sha = require_text(args.base_sha, field="pull request base SHA")
            head_sha = require_text(args.head_sha, field="pull request head SHA")
            if (
                COMMIT_SHA.fullmatch(base_sha) is None
                or COMMIT_SHA.fullmatch(head_sha) is None
            ):
                raise GateError(
                    "pull request base and head SHAs must be 40-character lowercase Git commit SHAs"
                )
            ref, _ = wait_for_pull_request_analyses(
                repository,
                number,
                token,
                args.attempts,
                args.delay_seconds,
                expected_categories,
                (base_sha, head_sha),
            )
        else:
            ref = require_text(args.ref, field="ref")
            sha = require_text(args.sha, field="sha")
            if COMMIT_SHA.fullmatch(sha) is None:
                raise GateError("sha must be a 40-character lowercase Git commit SHA")
            wait_for_analyses(
                repository,
                ref,
                sha,
                token,
                args.attempts,
                args.delay_seconds,
                expected_categories,
            )
        unexpected = unapproved_alerts(
            wait_for_open_alerts(
                repository, ref, token, args.attempts, args.delay_seconds
            ),
            selectors,
        )
    except GateError as error:
        print(f"code-scanning gate error: {error}", file=sys.stderr)
        return 2
    if unexpected:
        for alert in unexpected:
            print(
                f"Unapproved open alert #{alert.number}: {alert.tool}/{alert.rule} at {alert.path or '<repository>'}",
                file=sys.stderr,
            )
        return 1
    print("All open code-scanning alerts for this ref have an explicit disposition.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
