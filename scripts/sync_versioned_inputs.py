#!/usr/bin/env python3
"""Synchronize mechanical maintenance pins selected by the freshness registry."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

import audit_freshness
import sync_action_pins


SCHEMA_FIELD = re.compile(
    r'"\$schema"\s*:\s*"(?P<url>https://raw\.githubusercontent\.com/'
    r'googleapis/release-please/v\d+\.\d+\.\d+/schemas/config\.json)"'
)
RELEASE_PLEASE_TAG = re.compile(r"v\d+\.\d+\.\d+\Z")


def synchronize_release_please_schemas(
    repository_root: Path,
    configs: tuple[Path, ...],
    latest_tag: str,
    *,
    write: bool,
) -> list[Path]:
    """Update only validated Release Please schema URLs without reformatting JSON."""
    if RELEASE_PLEASE_TAG.fullmatch(latest_tag) is None:
        raise ValueError(
            f"Release Please latest tag is not a stable SemVer tag: {latest_tag}"
        )
    changed: list[Path] = []
    for relative in configs:
        path = audit_freshness.tracked_path(
            repository_root, relative, kind="Release Please config"
        )
        try:
            content = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise ValueError(
                f"could not read Release Please config {relative}: {error}"
            ) from error
        matches = list(SCHEMA_FIELD.finditer(content))
        if len(matches) != 1:
            raise ValueError(
                f"Release Please config must contain exactly one supported $schema: {relative}"
            )
        match = matches[0]
        schema_match = audit_freshness.RELEASE_PLEASE_SCHEMA.fullmatch(
            match.group("url")
        )
        if schema_match is None:
            raise ValueError(
                f"Release Please config has an unsupported $schema: {relative}"
            )
        if schema_match.group("version") == latest_tag:
            continue
        updated = (
            content[: match.start("url")]
            + f"https://raw.githubusercontent.com/googleapis/release-please/{latest_tag}/schemas/config.json"
            + content[match.end("url") :]
        )
        if write:
            path.write_bytes(updated.encode("utf-8"))
        changed.append(path)
    return changed


def synchronize_versioned_inputs(
    repository_root: Path,
    release_lookup: Callable[[str], sync_action_pins.ActionRelease],
    *,
    write: bool,
    tracker_registry: Path = audit_freshness.DEFAULT_TRACKER_REGISTRY,
) -> list[Path]:
    """Synchronize every registry-selected input with a deterministic upstream."""
    trackers = audit_freshness.load_trackers(repository_root, tracker_registry)
    releases: dict[str, sync_action_pins.ActionRelease] = {}

    def cached_release_lookup(repository: str) -> sync_action_pins.ActionRelease:
        if repository not in releases:
            releases[repository] = release_lookup(repository)
        return releases[repository]

    action_changes = sync_action_pins.synchronize_action_pins(
        repository_root,
        cached_release_lookup,
        write=False,
        workflow_directories=trackers.workflow_directories,
    )
    schema_changes: list[Path] = []
    if trackers.release_please_configs:
        schema_changes = synchronize_release_please_schemas(
            repository_root,
            trackers.release_please_configs,
            cached_release_lookup("googleapis/release-please").tag,
            write=False,
        )
    if write:
        sync_action_pins.synchronize_action_pins(
            repository_root,
            cached_release_lookup,
            write=True,
            workflow_directories=trackers.workflow_directories,
        )
        if trackers.release_please_configs:
            synchronize_release_please_schemas(
                repository_root,
                trackers.release_please_configs,
                cached_release_lookup("googleapis/release-please").tag,
                write=True,
            )
    return sorted(set(action_changes + schema_changes))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the repository root, tracker registry, and write authorization."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--tracker-registry",
        type=Path,
        default=audit_freshness.DEFAULT_TRACKER_REGISTRY,
    )
    parser.add_argument("--write", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Synchronize registry-selected inputs and print every changed path."""
    arguments = parse_args(argv)
    try:
        client = sync_action_pins.GitHubReleaseClient(
            os.environ.get("GITHUB_TOKEN", "")
        )
        changed = synchronize_versioned_inputs(
            arguments.repository_root.resolve(),
            client.latest_release,
            write=arguments.write,
            tracker_registry=arguments.tracker_registry,
        )
    except (OSError, ValueError, audit_freshness.AuditError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if changed:
        for path in changed:
            print(path.as_posix())
    else:
        print("Versioned maintenance inputs are already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
