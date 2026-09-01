#!/usr/bin/env python3
"""Select the trusted pull-request template before creating or editing a PR."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


TITLE_TYPE_PATTERN = re.compile(
    r"^(?P<type>feat|fix|docs)(?:\([^()\r\n]+\))?!?: "
)
TEMPLATE_BY_TITLE_TYPE = {
    "feat": "feature",
    "fix": "bugfix",
    "docs": "documentation",
}


def select_template(title: str) -> str:
    """Return the template required by a Conventional Commit PR title."""
    match = TITLE_TYPE_PATTERN.match(title)
    if match is None:
        return "default"
    return TEMPLATE_BY_TITLE_TYPE[match.group("type")]


def template_path(repository_root: Path, template: str) -> Path:
    """Return the checked-in template path and require it to be a regular file."""
    root = repository_root.resolve()
    path = (
        root / ".github" / "PULL_REQUEST_TEMPLATE.md"
        if template == "default"
        else root / ".github" / "PULL_REQUEST_TEMPLATE" / f"{template}.md"
    )
    if not path.is_file():
        raise ValueError(f"trusted PR template is missing: {path}")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the proposed title and repository location."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True, help="Proposed pull-request title")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path("."),
        help="Repository containing trusted PR templates",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Select and report the template before a GitHub mutation."""
    arguments = parse_args(argv)
    try:
        template = select_template(arguments.title)
        path = template_path(arguments.repository_root, template)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    try:
        relative = path.relative_to(arguments.repository_root.resolve())
    except ValueError:
        relative = path
    print(f"Selected PR template: {relative.as_posix()}")
    print(
        "Copy this UTF-8 template to a body file, complete its required checklist, "
        "then use gh pr create --body-file or gh pr edit --body-file."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
