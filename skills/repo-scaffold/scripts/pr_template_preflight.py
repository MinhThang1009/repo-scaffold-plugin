#!/usr/bin/env python3
"""Select and validate a pull-request template before creating or editing a PR."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


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
    """Return the checked-in template catalog and reject ambiguous identifiers."""
    root = repository_root.resolve()
    catalog = {"default": root / ".github" / "PULL_REQUEST_TEMPLATE.md"}
    directory = root / ".github" / "PULL_REQUEST_TEMPLATE"
    if directory.is_dir():
        for path in sorted(directory.glob("*.md")):
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
    catalog = template_catalog(repository_root)
    path = catalog.get(template)
    if path is None:
        if template == "default" or template in TEMPLATE_BY_TITLE_TYPE.values():
            root = repository_root.resolve()
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
    try:
        markers = TEMPLATE_MARKER_PATTERN.findall(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(
            f"could not read trusted PR template {path}: {error}"
        ) from error
    if markers != [template]:
        raise ValueError(
            f"trusted PR template {path} must contain exactly "
            f"<!-- repo-scaffold:pr-template={template} -->"
        )
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the proposed title, optional focused template, and repository location."""
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Select and report the template before a GitHub mutation."""
    arguments = parse_args(argv)
    try:
        template = select_template(arguments.title, arguments.template)
        path = template_path(arguments.repository_root, template)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    relative = path.relative_to(arguments.repository_root.resolve())
    print(f"Selected PR template: {relative.as_posix()}")
    print(
        "Copy this UTF-8 template to a body file, complete its required checklist, "
        "then use gh pr create --body-file or gh pr edit --body-file."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
