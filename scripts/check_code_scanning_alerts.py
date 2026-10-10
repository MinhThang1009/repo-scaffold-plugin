#!/usr/bin/env python3
"""Fail CI when an open code-scanning alert lacks an explicit disposition."""

from __future__ import annotations

from http.client import HTTPException, HTTPResponse

import argparse
import json
import math
import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
DEFAULT_ALLOWLIST = Path(".github/code-scanning-allowlist.json")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ALLOWLIST_BYTES = 1 * 1024 * 1024
MAX_ALLOWLIST_ENTRIES = 256
MAX_ALLOWLIST_REVIEW_DAYS = 366
MAX_PAGES = 20
MAX_ANALYSIS_PAGES = 20
MAX_POLLING_ATTEMPTS = 120
MAX_POLL_DELAY_SECONDS = 60
MAX_GATE_API_REQUESTS = 500
MAX_GATE_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_GATE_CHECK_SECONDS = 1200
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
PULL_REQUEST = re.compile(r"[1-9][0-9]*\Z")


class GateError(RuntimeError):
    """Raised when the gate cannot verify code-scanning alert state."""


class TransientGateError(GateError):
    """Raised when a bounded retry may recover a GitHub API request."""


@dataclass
class GateBudget:
    """Aggregate one synchronous inspection, including nested polling stages."""

    deadline: float = field(
        default_factory=lambda: monotonic() + MAX_GATE_CHECK_SECONDS
    )
    requests: int = 0
    response_bytes: int = 0

    def remaining_seconds(self) -> float:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise GateError("Code-scanning inspection exceeded its elapsed budget")
        return remaining

    def begin_request(self) -> tuple[float, int]:
        remaining = self.remaining_seconds()
        if self.requests >= MAX_GATE_API_REQUESTS:
            raise GateError("Code-scanning inspection exceeded its request budget")
        remaining_bytes = MAX_GATE_RESPONSE_BYTES - self.response_bytes
        if remaining_bytes <= 0:
            raise GateError(
                "Code-scanning inspection exhausted its response byte budget"
            )
        self.requests += 1
        return min(30.0, remaining), min(MAX_RESPONSE_BYTES, remaining_bytes)

    def consume_response(self, size: int) -> None:
        self.response_bytes += size
        if self.response_bytes > MAX_GATE_RESPONSE_BYTES:
            raise GateError(
                "Code-scanning inspection exceeded its response byte budget"
            )
        self.remaining_seconds()


GATE_BUDGET: ContextVar[GateBudget | None] = ContextVar(
    "code_scanning_gate_budget", default=None
)


@contextmanager
def inspection_budget() -> Iterator[GateBudget]:
    """Reuse a nested budget and discard it on every independent-call exit."""
    current = GATE_BUDGET.get()
    if current is not None:
        current.remaining_seconds()
        yield current
        current.remaining_seconds()
        return
    budget = GateBudget()
    binding = GATE_BUDGET.set(budget)
    try:
        yield budget
        budget.remaining_seconds()
    finally:
        GATE_BUDGET.reset(binding)


class RejectRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so the workflow token never leaves GitHub's API host."""

    def redirect_request(
        self,
        request: Any,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        if response is not None:
            try:
                response.close()
            except OSError as error:
                raise GateError("Redirect response could not be closed") from error
        raise GateError("GitHub API redirects are not allowed")

    def http_error_302(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any
    ) -> Request | None:
        return self.redirect_request(req, fp, code, msg, headers, req.full_url)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class DuplicateJsonMember(ValueError):
    """Raised when a JSON document contains ambiguous duplicate members."""


GITHUB_API_OPENER = build_opener(RejectRedirectHandler())


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


def safe_alert_path(value: str) -> str:
    """Validate the canonical POSIX path emitted by GitHub code scanning."""
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or any(ord(character) < 0x20 for character in value)
        or any(PureWindowsPath(part).drive for part in path.parts)
        or path.as_posix() != value
    ):
        raise GateError("code-scanning allowlist path must be canonical POSIX")
    return value


def load_allowlist(path: Path) -> tuple[AlertSelector, ...]:
    """Load a strict, reviewable allowlist from the checked-out base."""
    try:
        with path.open("rb") as allowlist_file:
            payload = allowlist_file.read(MAX_ALLOWLIST_BYTES + 1)
        if len(payload) > MAX_ALLOWLIST_BYTES:
            raise GateError(
                f"code-scanning allowlist exceeds the {MAX_ALLOWLIST_BYTES}-byte limit"
            )
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_json_object
        )
    except GateError:
        raise
    except (
        OSError,
        UnicodeError,
        ValueError,
        RecursionError,
    ) as error:
        raise GateError(
            f"could not read code-scanning allowlist {path}: {error}"
        ) from error
    if (
        not isinstance(document, dict)
        or type(document.get("schema-version")) is not int
        or document.get("schema-version") not in {2, 3}
    ):
        raise GateError("code-scanning allowlist must use schema-version 2 or 3")
    schema_version = document["schema-version"]
    entries = document.get("allowlist")
    if not isinstance(entries, list):
        raise GateError("code-scanning allowlist allowlist must be a list")
    if len(entries) > MAX_ALLOWLIST_ENTRIES:
        raise GateError(
            f"code-scanning allowlist exceeds the {MAX_ALLOWLIST_ENTRIES}-entry limit"
        )
    selectors: list[AlertSelector] = []
    seen: set[int] = set()
    for entry in entries:
        required_fields = {
            "number",
            "tool",
            "rule",
            "path",
            "reason",
        }
        if schema_version == 3:
            required_fields |= {"reviewed-on", "review-period-days"}
        if not isinstance(entry, dict) or set(entry) != required_fields:
            raise GateError(
                "each code-scanning allowlist entry must have the required selector fields"
            )
        if schema_version == 3:
            reviewed_on = entry["reviewed-on"]
            review_period_days = entry["review-period-days"]
            if (
                not isinstance(reviewed_on, str)
                or not reviewed_on.strip()
                or not isinstance(review_period_days, int)
                or isinstance(review_period_days, bool)
                or not 1 <= review_period_days <= MAX_ALLOWLIST_REVIEW_DAYS
            ):
                raise GateError("code-scanning allowlist review period is invalid")
            try:
                reviewed_date = date.fromisoformat(reviewed_on)
            except ValueError as error:
                raise GateError(
                    "code-scanning allowlist reviewed-on must use ISO date format"
                ) from error
            if reviewed_date > datetime.now(timezone.utc).date():
                raise GateError(
                    "code-scanning allowlist reviewed-on cannot be in the future"
                )
        number = entry["number"]
        if type(number) is not int or number < 1:
            raise GateError("code-scanning allowlist number must be a positive integer")
        path_value = entry["path"]
        if path_value is not None:
            path_value = require_text(path_value, field="code-scanning allowlist path")
            path_value = safe_alert_path(path_value)
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


def read_http_payload(
    response: Any, limit: int, account: Callable[[int], None] | None = None
) -> bytes:
    """Read bounded bytes without accepting ambiguous native length framing."""
    expected: int | None = None
    if isinstance(response, HTTPResponse):
        values = response.headers.get_all("Content-Length", [])
        if len(values) > 100 or sum(len(value) for value in values) > 8192:
            raise ValueError("HTTP Content-Length exceeds the header safety bound")
        canonical: str | None = None
        members = 0
        for value in values:
            for item in value.split(","):
                members += 1
                item = item.strip(" \t")
                if members > 100 or re.fullmatch(r"[0-9]+", item) is None:
                    raise ValueError("HTTP Content-Length is invalid")
                normalized = item.lstrip("0") or "0"
                if canonical is not None and normalized != canonical:
                    raise ValueError("HTTP Content-Length values conflict")
                canonical = normalized
        if canonical is not None:
            if len(canonical) > len(str(limit)) or (
                len(canonical) == len(str(limit)) and canonical > str(limit)
            ):
                raise ValueError(
                    "HTTP Content-Length exceeds the response safety bound"
                )
            if not response.chunked:
                expected = int(canonical)
    payload: bytes = response.read(limit + 1)
    if account is not None:
        account(len(payload))
    if isinstance(response, HTTPResponse) and (
        response.length not in {None, 0}
        or (expected is not None and len(payload) != expected)
    ):
        raise ValueError("HTTP response is incomplete")
    return payload


@inspection_budget()
def api_json(url: str, token: str) -> Any:
    """Read one bounded GitHub API response using the job token."""
    budget = GATE_BUDGET.get()
    if budget is None:
        raise GateError("Code-scanning API request has no inspection budget")
    timeout, read_limit = budget.begin_request()
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
        with GITHUB_API_OPENER.open(request, timeout=timeout) as response:
            payload = read_http_payload(response, read_limit, budget.consume_response)
    except HTTPError as error:
        try:
            error.close()
        except OSError as cleanup_error:
            raise GateError(
                "GitHub API error response could not be closed"
            ) from cleanup_error
        if error.code in {408, 429, 500, 502, 503, 504}:
            raise TransientGateError(
                f"GitHub API request failed transiently: {error}"
            ) from error
        raise GateError(f"GitHub API request failed: {error}") from error
    except (URLError, OSError) as error:
        raise TransientGateError(
            f"GitHub API request failed transiently: {error}"
        ) from error
    except (HTTPException, ValueError) as error:
        raise GateError(f"GitHub API HTTP response is malformed: {error}") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise GateError("GitHub API response exceeds the allowed size")
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=unique_json_object)
    except (
        UnicodeError,
        ValueError,
        RecursionError,
    ) as error:
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


def validate_polling_controls(attempts: int, delay: float) -> None:
    """Reject malformed or excessive retry work before any network call or sleep."""
    if type(attempts) is not int or not 1 <= attempts <= MAX_POLLING_ATTEMPTS:
        raise GateError(f"attempts must be an integer from 1 to {MAX_POLLING_ATTEMPTS}")
    if (
        type(delay) not in (int, float)
        or not 0 <= delay <= MAX_POLL_DELAY_SECONDS
        or not math.isfinite(delay)
    ):
        raise GateError(
            f"delay must be finite and from 0 to {MAX_POLL_DELAY_SECONDS} seconds"
        )


def _sleep_before_retry(delay: float) -> None:
    budget = GATE_BUDGET.get()
    if budget is None:
        raise GateError("Code-scanning retry has no inspection budget")
    if delay >= budget.remaining_seconds():
        raise GateError("Code-scanning retry delay would exhaust the elapsed budget")
    time.sleep(delay)
    budget.remaining_seconds()


@inspection_budget()
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
    validate_polling_controls(attempts, delay)
    for attempt in range(attempts):
        try:
            if analyses_ready(repository, ref, sha, token, expected_categories):
                return
        except TransientGateError:
            pass
        if attempt + 1 < attempts:
            _sleep_before_retry(delay)
    raise GateError(
        f"CodeQL analyses for {ref} at {sha} were not queryable after {attempts} attempts"
    )


@inspection_budget()
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
    validate_polling_controls(attempts, delay)
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
            _sleep_before_retry(delay)
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
            if path is not None:
                path = safe_alert_path(path)
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


@inspection_budget()
def wait_for_open_alerts(
    repository: str, ref: str, token: str, attempts: int, delay: float
) -> tuple[Alert, ...]:
    """Retry transient alert-list failures without treating them as approved."""
    validate_polling_controls(attempts, delay)
    for attempt in range(attempts):
        try:
            return open_alerts(repository, ref, token)
        except TransientGateError:
            if attempt + 1 < attempts:
                _sleep_before_retry(delay)
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


def _run_gate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    # A shared workflow step exports this optional variable for both PR and
    # merge-group events. GitHub resolves the unavailable PR property to an
    # empty string, which must select the ref/SHA path instead of PR polling.
    parser.add_argument("--pull-request", default=os.environ.get("PR_NUMBER") or None)
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
    repository = require_text(args.repository, field="repository")
    if REPOSITORY.fullmatch(repository) is None:
        raise GateError("repository must be an owner/name pair")
    token = require_text(args.token, field="token")
    validate_polling_controls(args.attempts, args.delay_seconds)
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
        wait_for_open_alerts(repository, ref, token, args.attempts, args.delay_seconds),
        selectors,
    )
    if unexpected:
        for alert in unexpected:
            print(
                f"Unapproved open alert #{alert.number}: {alert.tool}/{alert.rule} at {alert.path or '<repository>'}",
                file=sys.stderr,
            )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        with inspection_budget():
            status = _run_gate(argv)
        if status == 0:
            print(
                "All open code-scanning alerts for this ref have an explicit disposition."
            )
        return status
    except GateError as error:
        print(f"code-scanning gate error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
