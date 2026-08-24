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
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class GateError(RuntimeError):
    """Raised when the gate cannot verify code-scanning alert state."""


@dataclass(frozen=True)
class AlertSelector:
    """One reviewed exception for an otherwise merge-blocking alert."""

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


def load_allowlist(path: Path) -> tuple[AlertSelector, ...]:
    """Load a strict, reviewable allowlist from the checked-out base."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GateError(
            f"could not read code-scanning allowlist {path}: {error}"
        ) from error
    if not isinstance(document, dict) or document.get("schema-version") != 1:
        raise GateError("code-scanning allowlist must use schema-version 1")
    entries = document.get("allowlist")
    if not isinstance(entries, list):
        raise GateError("code-scanning allowlist allowlist must be a list")
    selectors: list[AlertSelector] = []
    seen: set[tuple[str, str, str | None]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "tool",
            "rule",
            "path",
            "reason",
        }:
            raise GateError(
                "each code-scanning allowlist entry must have tool, rule, path, and reason"
            )
        path_value = entry["path"]
        if path_value is not None:
            path_value = require_text(path_value, field="code-scanning allowlist path")
            if Path(path_value).is_absolute() or ".." in Path(path_value).parts:
                raise GateError(
                    "code-scanning allowlist path must be repository-relative"
                )
        selector = AlertSelector(
            require_text(entry["tool"], field="code-scanning allowlist tool"),
            require_text(entry["rule"], field="code-scanning allowlist rule"),
            path_value,
            require_text(entry["reason"], field="code-scanning allowlist reason"),
        )
        identity = (selector.tool, selector.rule, selector.path)
        if identity in seen:
            raise GateError("code-scanning allowlist must not repeat selectors")
        seen.add(identity)
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
    except (HTTPError, URLError, OSError) as error:
        raise GateError(f"GitHub API request failed: {error}") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise GateError("GitHub API response exceeds the allowed size")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GateError(f"GitHub API response is not valid JSON: {error}") from error


def analyses_ready(
    repository: str, ref: str, sha: str, token: str, expected: int
) -> bool:
    """Return whether CodeQL has uploaded every expected analysis for this commit."""
    url = f"{API_ROOT}/repos/{repository}/code-scanning/analyses?ref={quote(ref, safe='')}&per_page=100"
    document = api_json(url, token)
    if not isinstance(document, list):
        raise GateError("GitHub analyses response must be a list")
    return (
        sum(
            isinstance(item, dict) and item.get("commit_sha") == sha
            for item in document
        )
        >= expected
    )


def wait_for_analyses(
    repository: str,
    ref: str,
    sha: str,
    token: str,
    attempts: int,
    delay: float,
    expected: int,
) -> None:
    """Wait briefly for the just-finished CodeQL uploads to become queryable."""
    for attempt in range(attempts):
        if analyses_ready(repository, ref, sha, token, expected):
            return
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise GateError(
        f"CodeQL analyses for {ref} at {sha} were not queryable after {attempts} attempts"
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
            if not isinstance(number, int):
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


def unapproved_alerts(
    alerts: tuple[Alert, ...], selectors: tuple[AlertSelector, ...]
) -> list[Alert]:
    """Return alerts that have no exact reviewed selector."""
    allowed = {(item.tool, item.rule, item.path) for item in selectors}
    return [
        alert for alert in alerts if (alert.tool, alert.rule, alert.path) not in allowed
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF"))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--delay-seconds", type=float, default=5.0)
    parser.add_argument("--expected-analyses", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        repository = require_text(args.repository, field="repository")
        if REPOSITORY.fullmatch(repository) is None:
            raise GateError("repository must be an owner/name pair")
        ref = require_text(args.ref, field="ref")
        sha = require_text(args.sha, field="sha")
        token = require_text(args.token, field="token")
        if args.attempts < 1 or args.delay_seconds < 0 or args.expected_analyses < 1:
            raise GateError(
                "attempts and expected analyses must be positive, delay must be non-negative"
            )
        selectors = load_allowlist(args.allowlist)
        wait_for_analyses(
            repository,
            ref,
            sha,
            token,
            args.attempts,
            args.delay_seconds,
            args.expected_analyses,
        )
        unexpected = unapproved_alerts(open_alerts(repository, ref, token), selectors)
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
