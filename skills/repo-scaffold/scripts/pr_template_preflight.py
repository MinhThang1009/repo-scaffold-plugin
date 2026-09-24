#!/usr/bin/env python3
"""Select and validate a pull-request template before creating or editing a PR."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

from markdown_body_preflight import (
    MAX_BODY_FILE_BYTES as MAX_BODY_FILE_BYTES,
    hard_wrapped_prose_lines as _shared_hard_wrapped_prose_lines,
    read_body_file as _shared_read_body_file,
)

MAX_TEMPLATE_DIRECTORY_ENTRIES = 128
MAX_TEMPLATE_DIRECTORY_SCAN_ENTRIES = 10_000
TEMPLATE_EXTENSIONS = frozenset({".markdown", ".md", ".txt"})
# Keep the preflight's default-template search order explicit and deterministic.
TEMPLATE_LOCATIONS = (Path(".github"), Path("."), Path("docs"))


TITLE_TYPE_PATTERN = re.compile(r"^(?P<type>feat|fix|docs)(?:\([^()\r\n]+\))?!?: ")
TEMPLATE_BY_TITLE_TYPE = {
    "feat": "feature",
    "fix": "bugfix",
    "docs": "documentation",
}
TEMPLATE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
TEMPLATE_MARKER_PATTERN = re.compile(
    r"^<!-- repo-scaffold:pr-template=([a-z][a-z0-9-]*) -->[ \t]*$",
    re.MULTILINE,
)


def is_link_or_reparse(path: Path) -> bool:
    """Return whether an existing path is a link or Windows reparse point."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def path_has_link_or_reparse(path: Path, repository_root: Path) -> bool:
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
        if is_link_or_reparse(current):
            return True
        if not os.path.lexists(current):
            break
    return False


def safe_repository_root(repository_root: Path) -> Path:
    """Return an absolute repository root without following linked components."""
    root = Path(os.path.abspath(repository_root))
    if path_has_link_or_reparse(root, root):
        raise ValueError(f"repository root is linked or a reparse point: {root}")
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    return root


def required_template(title: str) -> str:
    """Return the template required by a Conventional Commit PR title."""
    match = TITLE_TYPE_PATTERN.match(title)
    if match is None:
        return "default"
    return TEMPLATE_BY_TITLE_TYPE[match.group("type")]


def select_template(title: str, requested_template: str | None = None) -> str:
    """Select a known template without allowing title-mapping bypasses."""
    required = required_template(title)
    if requested_template is None:
        return required
    if required != "default" and requested_template != required:
        raise ValueError(
            f"pull-request title requires the {required!r} template, not "
            f"{requested_template!r}"
        )
    return requested_template


def template_catalog(repository_root: Path) -> dict[str, Path]:
    """Discover supported PR templates and reject ambiguous catalog entries."""
    root = safe_repository_root(repository_root)
    default_templates_by_location: dict[Path, list[Path]] = {}
    focused_templates: list[Path] = []

    def directory_entries(directory: Path) -> list[Path]:
        entries: list[Path] = []
        for entry_index, path in enumerate(directory.iterdir()):
            if entry_index >= MAX_TEMPLATE_DIRECTORY_SCAN_ENTRIES:
                raise ValueError(
                    "trusted PR template catalog scan exceeds "
                    f"{MAX_TEMPLATE_DIRECTORY_SCAN_ENTRIES} directory entries: "
                    f"{directory}"
                )
            entries.append(path)
        return entries

    for location in TEMPLATE_LOCATIONS:
        parent = root / location
        if location != Path(".") and path_has_link_or_reparse(parent, root):
            raise ValueError("trusted PR template catalog contains a linked path")
        if not parent.is_dir():
            continue
        for entry in directory_entries(parent):
            if Path(entry.name).stem.casefold() == "pull_request_template" and (
                Path(entry.name).suffix.casefold() in TEMPLATE_EXTENSIONS
            ):
                if path_has_link_or_reparse(entry, root):
                    raise ValueError(f"trusted PR template path is linked: {entry}")
                if entry.is_file():
                    default_templates_by_location.setdefault(location, []).append(entry)
            if entry.name.casefold() != "pull_request_template":
                continue
            if path_has_link_or_reparse(entry, root):
                raise ValueError("trusted PR template catalog contains a linked path")
            if not entry.is_dir():
                continue
            for path in directory_entries(entry):
                if path.suffix.casefold() not in TEMPLATE_EXTENSIONS:
                    continue
                if path_has_link_or_reparse(path, root):
                    raise ValueError(f"trusted PR template path is linked: {path}")
                if not path.is_file():
                    continue
                if len(focused_templates) >= MAX_TEMPLATE_DIRECTORY_ENTRIES:
                    raise ValueError(
                        "trusted PR template catalog exceeds "
                        f"{MAX_TEMPLATE_DIRECTORY_ENTRIES} focused templates"
                    )
                focused_templates.append(path)

    default_template = root / ".github" / "PULL_REQUEST_TEMPLATE.md"
    for location in TEMPLATE_LOCATIONS:
        candidates = default_templates_by_location.get(location, [])
        if len(candidates) > 1:
            paths = ", ".join(str(path) for path in sorted(candidates))
            raise ValueError(
                f"ambiguous trusted PR default templates in {location}: {paths}"
            )
        if candidates:
            default_template = candidates[0]
            break
    catalog = {"default": default_template}
    for path in sorted(focused_templates):
        template_id = path.stem
        if TEMPLATE_ID_PATTERN.fullmatch(template_id) is None:
            raise ValueError(
                f"unsupported pull-request template identifier: {template_id!r}"
            )
        if template_id in catalog:
            raise ValueError(
                f"duplicate pull-request template identifier: {template_id!r}"
            )
        catalog[template_id] = path
    return catalog


def template_path(repository_root: Path, template: str) -> Path:
    """Return a selected template only when it has its required marker."""
    root = safe_repository_root(repository_root)
    catalog = template_catalog(root)
    path = catalog.get(template)
    if path is None:
        if template == "default" or template in TEMPLATE_BY_TITLE_TYPE.values():
            expected = (
                root / ".github" / "PULL_REQUEST_TEMPLATE.md"
                if template == "default"
                else root / ".github" / "PULL_REQUEST_TEMPLATE" / f"{template}.md"
            )
            raise ValueError(f"trusted PR template is missing: {expected}")
        available = ", ".join(sorted(catalog))
        raise ValueError(
            f"unknown pull-request template {template!r}; available templates: {available}"
        )
    if not path.is_file():
        raise ValueError(f"trusted PR template is missing: {path}")
    if path_has_link_or_reparse(path, root):
        raise ValueError(f"trusted PR template path is linked: {path}")
    try:
        template_text = read_body_file(path)
        normalized_template_text = template_text.replace("\r\n", "\n").replace(
            "\r", "\n"
        )
        markers = TEMPLATE_MARKER_PATTERN.findall(normalized_template_text)
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(
            f"could not read trusted PR template {path}: {error}"
        ) from error
    if markers != [template]:
        raise ValueError(
            f"trusted PR template {path} must contain exactly "
            f"<!-- repo-scaffold:pr-template={template} -->"
        )
    return path


def hard_wrapped_prose_lines(markdown: str) -> tuple[int, ...]:
    """Return line numbers where ordinary Markdown prose is hard-wrapped."""
    return _shared_hard_wrapped_prose_lines(markdown)


def read_body_file(path: Path) -> str:
    """Read a bounded regular UTF-8 body file without following links."""
    return _shared_read_body_file(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the proposed title, optional body/template, and repository location."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True, help="Proposed pull-request title")
    parser.add_argument(
        "--template",
        help=(
            "Focused template identifier for a title without a mandatory mapping "
            "(for example: security, deployment, or dependency-update)"
        ),
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path("."),
        help="Repository containing the checked-in PR template catalog",
    )
    parser.add_argument(
        "--body-file",
        type=Path,
        help="UTF-8 pull-request body to reject when prose is hard-wrapped",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Select and report the template before a GitHub mutation."""
    arguments = parse_args(argv)
    try:
        template = select_template(arguments.title, arguments.template)
        path = template_path(arguments.repository_root, template)
        if arguments.body_file is not None:
            wrapped_lines = hard_wrapped_prose_lines(
                read_body_file(arguments.body_file)
            )
            if wrapped_lines:
                lines = ", ".join(str(line) for line in wrapped_lines)
                raise ValueError(
                    f"body file contains hard-wrapped prose at line(s): {lines}"
                )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    relative = path.relative_to(safe_repository_root(arguments.repository_root))
    print(f"Selected PR template: {relative.as_posix()}")
    print(
        "Copy this UTF-8 template to a body file, complete its required checklist, "
        "then use gh pr create --body-file or gh pr edit --body-file."
    )
    if arguments.body_file is not None:
        print("PR body does not contain hard-wrapped prose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
