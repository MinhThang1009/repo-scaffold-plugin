#!/usr/bin/env python3
"""Synchronize reviewed workflow action pins to each action's latest release."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen


GITHUB_API_URL = "https://api.github.com"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ACTION_TAG_PAGES = 20
MAX_ANNOTATED_TAG_DEPTH = 10
ACTION_PIN_PATTERN = re.compile(
    r"(?m)^(?P<prefix>\s*(?:-\s*)?uses:\s*)(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.\-/]+)?)@(?P<sha>[0-9a-fA-F]{40})(?P<comment>[ \t]*(?:#[^\r\n]*)?)(?=\r?$)"
)
USES_PATTERN = re.compile(r"(?m)^\s*(?:-\s*)?uses:\s*(?P<reference>\S+)")
BLOCK_SCALAR_HEADER_PATTERN = re.compile(
    r"^(?P<indent> *)[^#\r\n]+:\s*[>|][0-9+-]*[ \t]*(?:#.*)?(?:\r?\n)?$"
)
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


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether an existing path is a symlink or Windows reparse point."""
    if path.is_symlink():
        return True
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _path_has_link_or_reparse(path: Path, repository_root: Path) -> bool:
    """Return whether a repository-relative path crosses a link-like boundary."""
    boundary = Path(os.path.abspath(repository_root))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(boundary)
    except ValueError:
        return True
    current = boundary
    for part in (None, *relative.parts):
        if part is not None:
            current /= part
        if _is_link_or_reparse(current):
            return True
        if not os.path.lexists(current):
            break
    return False


def workflow_paths(
    repository_root: Path,
    workflow_directories: tuple[Path, ...] = WORKFLOW_DIRECTORIES,
) -> list[Path]:
    """Return every tracked workflow that carries a synchronized action pin."""
    repository_root = Path(os.path.abspath(repository_root))
    if not repository_root.is_dir() or _path_has_link_or_reparse(
        repository_root, repository_root
    ):
        raise ValueError(f"repository root is missing or unsafe: {repository_root}")
    paths: list[Path] = []
    for relative_directory in workflow_directories:
        if (
            relative_directory.is_absolute()
            or ".." in relative_directory.parts
            or not relative_directory.parts
        ):
            raise ValueError(
                f"workflow directory must be a safe relative path: {relative_directory}"
            )
        directory = repository_root / relative_directory
        if (
            _path_has_link_or_reparse(directory, repository_root)
            or not directory.is_dir()
        ):
            raise ValueError(
                f"workflow directory is missing or unsafe: {relative_directory}"
            )
        for pattern in ("*.yml", "*.yaml"):
            for path in directory.glob(pattern):
                if _path_has_link_or_reparse(path, repository_root):
                    raise ValueError(f"workflow file is unsafe: {path}")
                if path.is_file():
                    paths.append(path)
    if not paths:
        raise ValueError("no workflow files were found for action-pin synchronization")
    return sorted(paths)


def block_scalar_content_ranges(content: str) -> tuple[tuple[int, int], ...]:
    """Return offsets occupied by YAML literal or folded scalar content."""
    ranges: list[tuple[int, int]] = []
    block_indent: int | None = None
    block_start = 0
    offset = 0
    for line in content.splitlines(keepends=True):
        indentation = len(line) - len(line.lstrip(" "))
        if block_indent is not None and line.strip() and indentation <= block_indent:
            ranges.append((block_start, offset))
            block_indent = None
        if block_indent is None:
            header = BLOCK_SCALAR_HEADER_PATTERN.fullmatch(line)
            if header is not None:
                block_indent = len(header.group("indent"))
                block_start = offset + len(line)
        offset += len(line)
    if block_indent is not None:
        ranges.append((block_start, len(content)))
    return tuple(ranges)


def _matches_outside_block_scalars(
    pattern: re.Pattern[str], content: str
) -> tuple[re.Match[str], ...]:
    """Return regex matches that are not embedded in YAML block-scalar text."""
    ranges = block_scalar_content_ranges(content)
    return tuple(
        match
        for match in pattern.finditer(content)
        if not any(start <= match.start() < end for start, end in ranges)
    )


def action_pin_matches(content: str) -> tuple[re.Match[str], ...]:
    """Return immutable action-pin mappings, excluding YAML scalar content."""
    return _matches_outside_block_scalars(ACTION_PIN_PATTERN, content)


def workflow_uses_matches(content: str) -> tuple[re.Match[str], ...]:
    """Return workflow ``uses`` mappings, excluding YAML scalar content."""
    return _matches_outside_block_scalars(USES_PATTERN, content)


def auditable_action_repositories(path: Path, content: str) -> set[str]:
    """Collect every externally hosted action pinned to an immutable SHA."""
    repositories: set[str] = set()
    pins = {match.group("reference") for match in workflow_uses_matches(content)}
    pinned_references = {
        f"{match.group('action')}@{match.group('sha')}"
        for match in action_pin_matches(content)
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
                payload = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, UnicodeError) as error:
            raise ValueError(
                f"GitHub API request failed for {path}: {error}"
            ) from error
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError(f"GitHub API response exceeds the size limit for {path}")
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(
                f"GitHub API request failed for {path}: {error}"
            ) from error

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
        releases: list[tuple[tuple[int, int, int], ActionRelease]] = []
        for page in range(1, MAX_ACTION_TAG_PAGES + 1):
            tags = self.get_json_list(
                f"/repos/{repository}/tags?per_page=100&page={page}"
            )
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
            if len(tags) < 100:
                break
        else:
            raise ValueError(
                f"action tag inventory exceeds {MAX_ACTION_TAG_PAGES} pages: {repository}"
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
        tag_depth = 0
        while object_document.get("type") == "tag":
            if tag_depth >= MAX_ANNOTATED_TAG_DEPTH:
                raise ValueError(
                    f"latest action release tag nesting exceeds "
                    f"{MAX_ANNOTATED_TAG_DEPTH}: {repository}"
                )
            sha = object_document.get("sha")
            if not isinstance(sha, str) or SHA_PATTERN.fullmatch(sha) is None:
                break
            object_document = self.get_json(f"/repos/{repository}/git/tags/{sha}").get(
                "object"
            )
            if not isinstance(object_document, dict):
                raise ValueError(f"latest action release tag is invalid: {repository}")
            tag_depth += 1
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
    workflow_directories: tuple[Path, ...] = WORKFLOW_DIRECTORIES,
) -> list[Path]:
    """Update all allowed action pins after resolving every release."""
    contents = {
        path: path.read_bytes().decode("utf-8")
        for path in workflow_paths(repository_root, workflow_directories)
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
        comment = match.group("comment")
        if match.group("sha").casefold() == release.sha and comment.strip() == (
            f"# {release.tag}"
        ):
            return match.group(0)
        prefix = comment[: comment.index("#")] if "#" in comment else " "
        return f"{match.group('prefix')}{action}@{release.sha}{prefix}# {release.tag}"

    replacements: dict[Path, str] = {}
    for path, content in contents.items():
        replacement_parts: list[str] = []
        previous_end = 0
        for match in action_pin_matches(content):
            replacement_parts.extend(
                (content[previous_end : match.start()], replace(match))
            )
            previous_end = match.end()
        replacement_parts.append(content[previous_end:])
        replacements[path] = "".join(replacement_parts)
    changed = [path for path in contents if replacements[path] != contents[path]]
    if write:
        for path in changed:
            path.write_bytes(replacements[path].encode("utf-8"))
    return changed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the repository root, optional workflow scope, and write authorization."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--workflow-directory",
        action="append",
        type=Path,
        help="Relative workflow directory to synchronize; repeat to include more.",
    )
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
            workflow_directories=(
                tuple(arguments.workflow_directory)
                if arguments.workflow_directory is not None
                else WORKFLOW_DIRECTORIES
            ),
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
