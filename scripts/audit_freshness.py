#!/usr/bin/env python3
"""Detect stale repository-maintenance inputs from their authoritative sources."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import sync_action_pins


PYPI_ROOT = "https://pypi.org/pypi"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
PINNED_REQUIREMENT = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)==(?P<version>[^\s\\]+)(?:\s+\\)?\Z"
)
RELEASE_PLEASE_SCHEMA = re.compile(
    r"https://raw\.githubusercontent\.com/googleapis/release-please/"
    r"(?P<version>v\d+\.\d+\.\d+)/schemas/config\.json\Z"
)
RELEASE_PLEASE_CONFIGS = (
    Path("release-please-config.json"),
    Path("skills/repo-scaffold/assets/release-please-config.json"),
    Path("skills/repo-scaffold/assets/release-please-config.vi.json"),
)
REQUIREMENT_SOURCES = (
    (
        Path("requirements-dev.in"),
        (Path("requirements-dev.txt"), Path("requirements-mutation.txt")),
    ),
    (Path("requirements-mutation.in"), (Path("requirements-mutation.txt"),)),
    (Path("skills/repo-scaffold/assets/requirements-docs.txt"), ()),
)


class AuditError(RuntimeError):
    """Raised when a freshness input or upstream response cannot be trusted."""


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
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed host
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError) as error:
        raise AuditError(f"upstream request failed for {url}: {error}") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise AuditError(f"upstream response is too large for {url}")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
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
    root: Path, release_lookup: Callable[[str], sync_action_pins.ActionRelease]
) -> list[dict[str, str]]:
    """Compare every action SHA with the exact immutable upstream release SHA."""
    findings: list[dict[str, str]] = []
    releases: dict[str, sync_action_pins.ActionRelease] = {}
    for path in sync_action_pins.workflow_paths(root):
        text = path.read_text(encoding="utf-8")
        sync_action_pins.action_repositories(path, text)
        for match in sync_action_pins.ACTION_PIN_PATTERN.finditer(text):
            action = match.group("action")
            repository = sync_action_pins.action_repository(action)
            release = releases.get(repository)
            if release is None:
                release = release_lookup(repository)
                releases[repository] = release
            if match.group("sha") != release.sha:
                findings.append(
                    {
                        "kind": "action-pin",
                        "path": path.relative_to(root).as_posix(),
                        "subject": action,
                        "current": match.group("sha"),
                        "latest": release.tag,
                        "details": f"Expected immutable SHA {release.sha}.",
                    }
                )
    return findings


def release_please_findings(root: Path, latest_tag: str) -> list[dict[str, str]]:
    """Compare configured schema versions with the latest Release Please release."""
    findings: list[dict[str, str]] = []
    for relative in RELEASE_PLEASE_CONFIGS:
        path = root / relative
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
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
    root: Path, latest_lookup: Callable[[str], str]
) -> list[dict[str, str]]:
    """Compare direct requirements to PyPI and ensure their locks carry the pin."""
    findings: list[dict[str, str]] = []
    latest_versions: dict[str, str] = {}
    for source_relative, lock_relatives in REQUIREMENT_SOURCES:
        source = root / source_relative
        pins = pinned_requirements(source)
        locks = {
            relative: pinned_requirements(root / relative)
            for relative in lock_relatives
        }
        for key, (name, current) in pins.items():
            latest = latest_versions.setdefault(key, latest_lookup(name))
            if current != latest:
                findings.append(
                    {
                        "kind": "python-package",
                        "path": source_relative.as_posix(),
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
                            "details": f"Must match direct pin in {source_relative.as_posix()}.",
                        }
                    )
    return findings


def audit(root: Path, token: str) -> dict[str, Any]:
    """Run every independent freshness check and return a deterministic report."""
    findings: list[dict[str, str]] = []
    errors: list[str] = []
    try:
        client = sync_action_pins.GitHubReleaseClient(token)
        findings.extend(action_findings(root, client.latest_release))
        findings.extend(
            release_please_findings(
                root, client.latest_release("googleapis/release-please").tag
            )
        )
    except (OSError, ValueError, AuditError) as error:
        errors.append(str(error))
    try:
        findings.extend(requirement_findings(root, latest_pypi_release))
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
                        **finding
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
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Write reports and return current, stale, or indeterminate status."""
    arguments = parse_args(argv)
    report = audit(
        arguments.repository_root.resolve(), os.environ.get("GITHUB_TOKEN", "")
    )
    arguments.json_output.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    arguments.markdown_output.write_text(markdown_report(report), encoding="utf-8")
    print(f"Repository freshness status: {report['status']}")
    return {"current": 0, "attention": 1, "indeterminate": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
