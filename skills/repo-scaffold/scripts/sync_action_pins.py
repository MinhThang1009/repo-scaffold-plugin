#!/usr/bin/env python3
"""Synchronize reviewed workflow action pins to each action's latest release."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen


GITHUB_API_URL = "https://api.github.com"
ACTION_PIN_PATTERN = re.compile(
    r"(?m)^(?P<prefix>\s*(?:-\s*)?uses:\s*)(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.\-/]+)?)@(?P<sha>[0-9a-fA-F]{40})(?P<comment>\s*(?:#.*)?)$"
)
USES_PATTERN = re.compile(r"(?m)^\s*(?:-\s*)?uses:\s*(?P<reference>\S+)")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
RELEASE_TAG_PATTERN = re.compile(
    r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
)
STABLE_ACTION_TAG_PATTERN = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
WORKFLOW_DIRECTORIES = (
    Path(".github/workflows"),
    Path("skills/repo-scaffold/assets/workflows"),
)
ALLOWED_ACTION_REPOSITORIES = frozenset(
    {
        "actions/attest",
        "actions/cache",
        "actions/checkout",
        "actions/dependency-review-action",
        "actions/download-artifact",
        "actions/labeler",
        "actions/setup-python",
        "actions/stale",
        "actions/upload-artifact",
        "davidanson/markdownlint-cli2-action",
        "dependabot/fetch-metadata",
        "github/codeql-action",
        "googleapis/release-please-action",
        "lycheeverse/lychee-action",
        "ossf/scorecard-action",
        "peter-evans/create-pull-request",
    }
)
TAG_LIST_ACTION_REPOSITORIES = frozenset({"github/codeql-action"})


@dataclass(frozen=True)
class ActionRelease:
    """An immutable commit resolved from an action's stable release tag."""

    tag: str
    sha: str


def action_repository(action: str) -> str:
    """Return the owner/repository part of an action or sub-action reference."""
    return "/".join(action.split("/")[:2]).casefold()


def workflow_paths(repository_root: Path) -> list[Path]:
    """Return every tracked workflow that carries a synchronized action pin."""
    paths: list[Path] = []
    for relative_directory in WORKFLOW_DIRECTORIES:
        directory = repository_root / relative_directory
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(
                f"workflow directory is missing or unsafe: {relative_directory}"
            )
        paths.extend(path for path in directory.glob("*.yml") if path.is_file())
    if not paths:
        raise ValueError("no workflow files were found for action-pin synchronization")
    return sorted(paths)


def auditable_action_repositories(path: Path, content: str) -> set[str]:
    """Collect every externally hosted action pinned to an immutable SHA."""
    repositories: set[str] = set()
    pins = {match.group("reference") for match in USES_PATTERN.finditer(content)}
    pinned_references = {
        f"{match.group('action')}@{match.group('sha')}"
        for match in ACTION_PIN_PATTERN.finditer(content)
    }
    for reference in pins:
        if reference.startswith(("./", "docker://")):
            continue
        if reference not in pinned_references:
            raise ValueError(f"workflow action is not pinned to a full SHA: {path}")
        action = reference.rsplit("@", 1)[0]
        repository = action_repository(action)
        repositories.add(repository)
    return repositories


def action_repositories(path: Path, content: str) -> set[str]:
    """Collect only reviewed action repositories for the write-capable synchronizer."""
    repositories = auditable_action_repositories(path, content)
    for repository in repositories:
        if repository not in ALLOWED_ACTION_REPOSITORIES:
            raise ValueError(
                f"workflow action is not in the synchronization allowlist: {repository}"
            )
    return repositories


class GitHubReleaseClient:
    """Load stable release tags and immutable commits from GitHub's REST API."""

    def __init__(self, token: str, opener: Callable[..., Any] = urlopen) -> None:
        if not token:
            raise ValueError("GITHUB_TOKEN is required to synchronize action pins")
        self.token = token
        self.opener = opener

    def _get_document(self, path: str) -> Any:
        """Fetch one JSON document with the workflow token and an explicit timeout."""
        request = Request(
            f"{GITHUB_API_URL}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                document = json.loads(response.read().decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(
                f"GitHub API request failed for {path}: {error}"
            ) from error
        return document

    def get_json(self, path: str) -> dict[str, Any]:
        """Fetch one bounded GitHub API object with the workflow token."""
        document = self._get_document(path)
        if not isinstance(document, dict):
            raise ValueError(f"GitHub API response is not an object for {path}")
        return document

    def get_json_list(self, path: str) -> list[dict[str, Any]]:
        """Fetch a bounded GitHub API list whose entries are all objects."""
        document = self._get_document(path)
        if (
            not isinstance(document, list)
            or len(document) > 100
            or any(not isinstance(item, dict) for item in document)
        ):
            raise ValueError(
                f"GitHub API response is not a bounded object list for {path}"
            )
        return document

    def latest_action_tag(self, repository: str) -> ActionRelease:
        """Resolve the latest stable action tag when releases contain non-action tags."""
        tags = self.get_json_list(f"/repos/{repository}/tags?per_page=100")
        releases: list[tuple[tuple[int, int, int], ActionRelease]] = []
        for tag_document in tags:
            tag = tag_document.get("name")
            commit = tag_document.get("commit")
            match = (
                STABLE_ACTION_TAG_PATTERN.fullmatch(tag)
                if isinstance(tag, str)
                else None
            )
            sha = commit.get("sha") if isinstance(commit, dict) else None
            if (
                match is not None
                and isinstance(tag, str)
                and isinstance(sha, str)
                and SHA_PATTERN.fullmatch(sha)
            ):
                version: tuple[int, int, int] = (
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )
                releases.append(
                    (
                        version,
                        ActionRelease(tag=tag, sha=sha),
                    )
                )
        if not releases:
            raise ValueError(f"no stable action tag could be resolved: {repository}")
        return max(releases, key=lambda item: item[0])[1]

    def latest_release(self, repository: str) -> ActionRelease:
        """Resolve the stable latest release tag to its immutable commit SHA."""
        if REPOSITORY_PATTERN.fullmatch(repository) is None:
            raise ValueError(f"invalid action repository: {repository}")
        if repository in TAG_LIST_ACTION_REPOSITORIES:
            return self.latest_action_tag(repository)
        release = self.get_json(f"/repos/{repository}/releases/latest")
        tag = release.get("tag_name")
        if not isinstance(tag, str) or RELEASE_TAG_PATTERN.fullmatch(tag) is None:
            raise ValueError(f"latest action release has an invalid tag: {repository}")
        reference = self.get_json(
            f"/repos/{repository}/git/ref/tags/{quote(tag, safe='')}"
        )
        object_document = reference.get("object")
        if not isinstance(object_document, dict):
            raise ValueError(f"latest action release has no tag object: {repository}")
        object_type = object_document.get("type")
        sha = object_document.get("sha")
        if object_type == "tag" and isinstance(sha, str):
            object_document = self.get_json(f"/repos/{repository}/git/tags/{sha}").get(
                "object"
            )
            if not isinstance(object_document, dict):
                raise ValueError(f"latest action release tag is invalid: {repository}")
            object_type = object_document.get("type")
            sha = object_document.get("sha")
        if (
            object_type != "commit"
            or not isinstance(sha, str)
            or SHA_PATTERN.fullmatch(sha) is None
        ):
            raise ValueError(
                f"latest action release does not resolve to a commit: {repository}"
            )
        return ActionRelease(tag=tag, sha=sha)


def synchronize_action_pins(
    repository_root: Path,
    release_lookup: Callable[[str], ActionRelease],
    *,
    write: bool,
) -> list[Path]:
    """Update all allowed action pins atomically after resolving every release."""
    contents = {
        path: path.read_text(encoding="utf-8")
        for path in workflow_paths(repository_root)
    }
    repositories = sorted(
        {
            repository
            for path, content in contents.items()
            for repository in action_repositories(path, content)
        }
    )
    releases = {repository: release_lookup(repository) for repository in repositories}

    def replace(match: re.Match[str]) -> str:
        action = match.group("action")
        release = releases[action_repository(action)]
        return f"{match.group('prefix')}{action}@{release.sha} # {release.tag}"

    replacements = {
        path: ACTION_PIN_PATTERN.sub(replace, content)
        for path, content in contents.items()
    }
    changed = [path for path in contents if replacements[path] != contents[path]]
    if write:
        for path in changed:
            path.write_text(replacements[path], encoding="utf-8")
    return changed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the repository root and explicit write authorization."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--write", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Synchronize pins and print the changed paths for the PR creator."""
    arguments = parse_args(argv)
    try:
        client = GitHubReleaseClient(os.environ.get("GITHUB_TOKEN", ""))
        changed = synchronize_action_pins(
            arguments.repository_root.resolve(),
            client.latest_release,
            write=arguments.write,
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if changed:
        for path in changed:
            print(path.as_posix())
    else:
        print("Action pins are already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
