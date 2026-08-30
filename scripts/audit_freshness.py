#!/usr/bin/env python3
"""Detect stale repository-maintenance inputs from their authoritative sources."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import sync_action_pins


PYPI_ROOT = "https://pypi.org/pypi"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TRACKER_REGISTRY_BYTES = 1024 * 1024
MAX_TRACKER_ENTRIES = 256
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
PINNED_REQUIREMENT = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)==(?P<version>[^\s\\#]+)"
    r"(?:\s+\\)?(?:[ \t]+#[^\r\n]*)?\Z"
)
RELEASE_PLEASE_SCHEMA = re.compile(
    r"https://raw\.githubusercontent\.com/googleapis/release-please/"
    r"(?P<version>v\d+\.\d+\.\d+)/schemas/config\.json\Z"
)
DEFAULT_TRACKER_REGISTRY = Path(".github/freshness-trackers.json")


class AuditError(RuntimeError):
    """Raised when a freshness input or upstream response cannot be trusted."""


class DuplicateJsonMember(ValueError):
    """Raised when a tracker registry uses duplicate JSON members."""


class RejectRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so a fixed upstream cannot become an arbitrary target."""

    def redirect_request(
        self,
        request: Any,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        raise AuditError("upstream redirects are not allowed")


PYPI_OPENER = build_opener(RejectRedirectHandler())


@dataclass(frozen=True)
class RequirementSource:
    """One direct-requirement source and locks that must preserve its pins."""

    path: Path
    locks: tuple[Path, ...]


@dataclass(frozen=True)
class FreshnessTrackers:
    """The explicit versioned inputs that a repository elects to track."""

    workflow_directories: tuple[Path, ...]
    release_please_configs: tuple[Path, ...]
    requirement_sources: tuple[RequirementSource, ...]


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting ambiguous duplicate member names."""
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def safe_relative_path(value: object, *, field: str) -> Path:
    """Parse one canonical, cross-platform registry path within the repository."""
    if not isinstance(value, str) or not value:
        raise AuditError(f"{field} must be a non-empty relative path")
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or any(PureWindowsPath(part).drive for part in path.parts)
        or path.as_posix() != value
    ):
        raise AuditError(f"{field} must be a safe relative path: {value!r}")
    return Path(value)


def tracked_path(root: Path, relative: Path, *, kind: str) -> Path:
    """Resolve a configured path only when it remains inside the repository."""
    path = root / relative
    if sync_action_pins._path_has_link_or_reparse(path, root):
        raise AuditError(f"{kind} is missing or unsafe: {relative}")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as error:
        raise AuditError(f"{kind} is missing or unsafe: {relative}") from error
    return path


def load_trackers(root: Path, relative: Path) -> FreshnessTrackers:
    """Load the reviewed freshness registry from within the repository root."""
    registry_path = tracked_path(
        root,
        safe_relative_path(relative.as_posix(), field="registry"),
        kind="freshness tracker registry",
    )
    try:
        if registry_path.stat().st_size > MAX_TRACKER_REGISTRY_BYTES:
            raise AuditError(
                f"freshness tracker registry exceeds the size limit: {relative}"
            )
        document = json.loads(
            registry_path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_json_object,
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        DuplicateJsonMember,
        RecursionError,
    ) as error:
        raise AuditError(
            f"could not read freshness tracker registry {relative}: {error}"
        ) from error
    if not isinstance(document, dict) or document.get("schema-version") != 1:
        raise AuditError("freshness tracker registry must use schema-version 1")

    def paths(key: str, *, allow_empty: bool) -> tuple[Path, ...]:
        values = document.get(key)
        if not isinstance(values, list) or (not allow_empty and not values):
            raise AuditError(f"freshness tracker registry {key} must be a list")
        if len(values) > MAX_TRACKER_ENTRIES:
            raise AuditError(
                f"freshness tracker registry {key} exceeds the entry limit"
            )
        parsed = tuple(
            safe_relative_path(value, field=f"freshness tracker registry {key}")
            for value in values
        )
        if len(set(parsed)) != len(parsed):
            raise AuditError(f"freshness tracker registry {key} must not repeat paths")
        return parsed

    sources = document.get("requirement-sources")
    if not isinstance(sources, list):
        raise AuditError(
            "freshness tracker registry requirement-sources must be a list"
        )
    if len(sources) > MAX_TRACKER_ENTRIES:
        raise AuditError(
            "freshness tracker registry requirement-sources exceeds the entry limit"
        )
    requirement_sources: list[RequirementSource] = []
    seen_sources: set[Path] = set()
    for entry in sources:
        if not isinstance(entry, dict):
            raise AuditError(
                "freshness tracker registry requirement source must be an object"
            )
        source = safe_relative_path(
            entry.get("path"), field="freshness tracker registry requirement path"
        )
        locks = entry.get("locks")
        if not isinstance(locks, list):
            raise AuditError(
                "freshness tracker registry requirement locks must be a list"
            )
        parsed_locks = tuple(
            safe_relative_path(
                lock, field="freshness tracker registry requirement lock"
            )
            for lock in locks
        )
        if source in seen_sources or len(set(parsed_locks)) != len(parsed_locks):
            raise AuditError(
                "freshness tracker registry requirement paths must be unique"
            )
        seen_sources.add(source)
        requirement_sources.append(RequirementSource(source, parsed_locks))
    return FreshnessTrackers(
        workflow_directories=paths("workflow-directories", allow_empty=False),
        release_please_configs=paths("release-please-configs", allow_empty=True),
        requirement_sources=tuple(requirement_sources),
    )


def normalized_name(name: str) -> str:
    """Return the canonical comparison form used by Python package indexes."""
    return re.sub(r"[-_.]+", "-", name).casefold()


def read_json(url: str) -> dict[str, Any]:
    """Read one bounded JSON document from a fixed HTTPS upstream."""
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "repo-scaffold-freshness-audit",
        },
    )
    try:
        with PYPI_OPENER.open(request, timeout=30) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError) as error:
        raise AuditError(f"upstream request failed for {url}: {error}") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise AuditError(f"upstream response is too large for {url}")
    try:
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_json_object
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        DuplicateJsonMember,
        RecursionError,
    ) as error:
        raise AuditError(f"upstream response is not valid JSON for {url}") from error
    if not isinstance(document, dict):
        raise AuditError(f"upstream response is not an object for {url}")
    return document


def latest_pypi_release(package: str) -> str:
    """Return PyPI's current stable default release for one validated package."""
    if PACKAGE_NAME.fullmatch(package) is None:
        raise AuditError(f"unsafe Python package name: {package!r}")
    document = read_json(f"{PYPI_ROOT}/{package}/json")
    info = document.get("info")
    version = info.get("version") if isinstance(info, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise AuditError(f"PyPI response has no current version for {package}")
    return version


def pinned_requirements(path: Path) -> dict[str, tuple[str, str]]:
    """Read exact direct pins, ignoring comments and include directives."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise AuditError(f"could not read requirements file {path}: {error}") from error
    pins: dict[str, tuple[str, str]] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-r", "--requirement", "--hash=")):
            continue
        match = PINNED_REQUIREMENT.fullmatch(stripped)
        if match is None:
            raise AuditError(f"unsupported requirement in {path}: {stripped!r}")
        name = match.group("name")
        key = normalized_name(name)
        version = match.group("version")
        previous = pins.get(key)
        if previous is not None and previous[1] != version:
            raise AuditError(f"conflicting direct pins for {name} in {path}")
        pins[key] = (name, version)
    if not pins:
        raise AuditError(f"requirements file has no direct pins: {path}")
    return pins


def action_findings(
    root: Path,
    workflow_directories: tuple[Path, ...],
    release_lookup: Callable[[str], sync_action_pins.ActionRelease],
) -> list[dict[str, str]]:
    """Compare every action SHA with the exact immutable upstream release SHA."""
    findings: list[dict[str, str]] = []
    releases: dict[str, sync_action_pins.ActionRelease] = {}
    try:
        workflow_paths = sync_action_pins.workflow_paths(root, workflow_directories)
    except ValueError as error:
        raise AuditError(str(error)) from error
    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        sync_action_pins.auditable_action_repositories(path, text)
        for match in sync_action_pins.action_pin_matches(text):
            action = sync_action_pins.normalized_action_pin_part(match, "action")
            current_sha = sync_action_pins.normalized_action_pin_part(match, "sha")
            repository = sync_action_pins.action_repository(action)
            release = releases.get(repository)
            if release is None:
                release = release_lookup(repository)
                releases[repository] = release
            if current_sha.casefold() != release.sha:
                findings.append(
                    {
                        "kind": "action-pin",
                        "path": path.relative_to(root).as_posix(),
                        "subject": action,
                        "current": current_sha,
                        "latest": release.tag,
                        "details": f"Expected immutable SHA {release.sha}.",
                    }
                )
    return findings


def release_please_findings(
    root: Path, configs: tuple[Path, ...], latest_tag: str
) -> list[dict[str, str]]:
    """Compare configured schema versions with the latest Release Please release."""
    findings: list[dict[str, str]] = []
    for relative in configs:
        path = tracked_path(root, relative, kind="Release Please config")
        try:
            document = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=unique_json_object,
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            DuplicateJsonMember,
        ) as error:
            raise AuditError(
                f"could not read Release Please config {relative}: {error}"
            ) from error
        schema = document.get("$schema") if isinstance(document, dict) else None
        match = (
            RELEASE_PLEASE_SCHEMA.fullmatch(schema) if isinstance(schema, str) else None
        )
        if match is None:
            raise AuditError(
                f"Release Please config has an unsupported $schema: {relative}"
            )
        if match.group("version") != latest_tag:
            findings.append(
                {
                    "kind": "release-please-schema",
                    "path": relative.as_posix(),
                    "subject": "$schema",
                    "current": match.group("version"),
                    "latest": latest_tag,
                    "details": "Schema URL should track the current stable release.",
                }
            )
    return findings


def requirement_findings(
    root: Path,
    sources: tuple[RequirementSource, ...],
    latest_lookup: Callable[[str], str],
) -> list[dict[str, str]]:
    """Compare direct requirements to PyPI and ensure their locks carry the pin."""
    findings: list[dict[str, str]] = []
    latest_versions: dict[str, str] = {}
    for requirement_source in sources:
        source = tracked_path(root, requirement_source.path, kind="requirements file")
        pins = pinned_requirements(source)
        locks = {
            relative: pinned_requirements(
                tracked_path(root, relative, kind="requirements lock")
            )
            for relative in requirement_source.locks
        }
        for key, (name, current) in pins.items():
            if key not in latest_versions:
                latest_versions[key] = latest_lookup(name)
            latest = latest_versions[key]
            if current != latest:
                findings.append(
                    {
                        "kind": "python-package",
                        "path": requirement_source.path.as_posix(),
                        "subject": name,
                        "current": current,
                        "latest": latest,
                        "details": "Direct pin differs from PyPI's current release.",
                    }
                )
            for lock_relative, lock_pins in locks.items():
                locked = lock_pins.get(key)
                if locked is None or locked[1] != current:
                    findings.append(
                        {
                            "kind": "lock-consistency",
                            "path": lock_relative.as_posix(),
                            "subject": name,
                            "current": locked[1] if locked is not None else "absent",
                            "latest": current,
                            "details": (
                                "Must match direct pin in "
                                f"{requirement_source.path.as_posix()}."
                            ),
                        }
                    )
    return findings


def audit(
    root: Path, token: str, tracker_registry: Path = DEFAULT_TRACKER_REGISTRY
) -> dict[str, Any]:
    """Run every independent freshness check and return a deterministic report."""
    findings: list[dict[str, str]] = []
    errors: list[str] = []
    try:
        trackers = load_trackers(root, tracker_registry)
    except AuditError as error:
        errors.append(str(error))
        trackers = None
    if trackers is not None:
        try:
            client = sync_action_pins.GitHubReleaseClient(token)
            findings.extend(
                action_findings(
                    root, trackers.workflow_directories, client.latest_release
                )
            )
            if trackers.release_please_configs:
                findings.extend(
                    release_please_findings(
                        root,
                        trackers.release_please_configs,
                        client.latest_release("googleapis/release-please").tag,
                    )
                )
        except (OSError, ValueError, AuditError) as error:
            errors.append(str(error))
        try:
            findings.extend(
                requirement_findings(
                    root, trackers.requirement_sources, latest_pypi_release
                )
            )
        except AuditError as error:
            errors.append(str(error))
    status = "indeterminate" if errors else "attention" if findings else "current"
    return {
        "schema-version": 1,
        "checked-at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "findings": findings,
        "errors": errors,
    }


def markdown_table_cell(value: object) -> str:
    """Render one value without permitting it to add Markdown table cells/rows."""
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def markdown_report(report: dict[str, Any]) -> str:
    """Render a concise, issue-safe Markdown representation of an audit report."""
    lines = [
        "<!-- repo-scaffold-freshness-audit -->",
        "# Repository freshness report",
        "",
        f"- Checked: `{report['checked-at']}`",
        f"- Overall status: **{report['status']}**",
        "",
    ]
    findings = report["findings"]
    if findings:
        lines.extend(
            [
                "| Check | Path | Subject | Current | Latest |",
                "| --- | --- | --- | --- | --- |",
                *[
                    "| {kind} | `{path}` | `{subject}` | `{current}` | `{latest}` |".format(
                        **{
                            key: markdown_table_cell(value)
                            for key, value in finding.items()
                        }
                    )
                    for finding in findings
                ],
                "",
            ]
        )
    else:
        lines.extend(["No stale versioned inputs were found.", ""])
    errors = report["errors"]
    if errors:
        lines.extend(
            ["## Indeterminate checks", "", *[f"- {error}" for error in errors], ""]
        )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse explicit report destinations for scheduled workflow use."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--tracker-registry", type=Path, default=DEFAULT_TRACKER_REGISTRY
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Write reports and return current, stale, or indeterminate status."""
    arguments = parse_args(argv)
    report = audit(
        arguments.repository_root.resolve(),
        os.environ.get("GITHUB_TOKEN", ""),
        arguments.tracker_registry,
    )
    arguments.json_output.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    arguments.markdown_output.write_text(markdown_report(report), encoding="utf-8")
    print(f"Repository freshness status: {report['status']}")
    return {"current": 0, "attention": 1, "indeterminate": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
