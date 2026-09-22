#!/usr/bin/env python3
"""Select and validate a pull-request template before creating or editing a PR."""

from __future__ import annotations

import argparse
import os
import re
import stat
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
FENCED_CODE_START_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
FENCED_CODE_END_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*$")
STRUCTURAL_MARKDOWN_LINE_PATTERN = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]|[-+*][ \t]+|\d+[.)][ \t]+|>[ \t]?|\||"
    r"(?:[-*_][ \t]*){3,}$|<)"
)
MAX_BODY_FILE_BYTES = 1024 * 1024


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
    """Return the checked-in template catalog and reject ambiguous identifiers."""
    root = safe_repository_root(repository_root)
    catalog = {"default": root / ".github" / "PULL_REQUEST_TEMPLATE.md"}
    directory = root / ".github" / "PULL_REQUEST_TEMPLATE"
    if path_has_link_or_reparse(catalog["default"], root) or path_has_link_or_reparse(
        directory, root
    ):
        raise ValueError("trusted PR template catalog contains a linked path")
    if directory.is_dir():
        for path in sorted(directory.glob("*.md")):
            if path_has_link_or_reparse(path, root):
                raise ValueError(f"trusted PR template path is linked: {path}")
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


def hard_wrapped_prose_lines(markdown: str) -> tuple[int, ...]:
    """Return line numbers where ordinary Markdown prose is hard-wrapped."""
    wrapped: list[int] = []
    previous_is_prose = False
    fence_character: str | None = None
    fence_length = 0
    comment_open = False

    for line_number, raw_line in enumerate(markdown.splitlines(), start=1):
        line = raw_line.rstrip("\r\n")
        fence_start = FENCED_CODE_START_PATTERN.match(line)
        if fence_character is not None:
            fence_end = FENCED_CODE_END_PATTERN.match(line)
            if (
                fence_end is not None
                and fence_end.group(1)[0] == fence_character
                and len(fence_end.group(1)) >= fence_length
            ):
                fence_character = None
                fence_length = 0
            previous_is_prose = False
            continue
        if fence_start is not None:
            fence_character = fence_start.group(1)[0]
            fence_length = len(fence_start.group(1))
            previous_is_prose = False
            continue
        if comment_open:
            if "-->" in line:
                comment_open = False
            previous_is_prose = False
            continue
        if "<!--" in line:
            if "-->" not in line[line.find("<!--") + 4 :]:
                comment_open = True
            previous_is_prose = False
            continue

        stripped = line.strip()
        is_prose = (
            bool(stripped) and STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is None
        )
        if previous_is_prose and is_prose:
            wrapped.append(line_number)
        previous_is_prose = is_prose and not line.endswith(("  ", "\\"))

    return tuple(wrapped)


def read_body_file(path: Path) -> str:
    """Read a bounded regular UTF-8 body file without following links."""
    if is_link_or_reparse(path) or not path.is_file():
        raise ValueError(f"body file must be a regular non-linked file: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read body file {path}: {error}") from error
    if len(payload) > MAX_BODY_FILE_BYTES:
        raise ValueError(f"body file exceeds the {MAX_BODY_FILE_BYTES}-byte limit")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"body file is not valid UTF-8: {path}") from error


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
