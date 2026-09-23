#!/usr/bin/env python3
"""Reject hard-wrapped prose in a Markdown body before a GitHub mutation."""

from __future__ import annotations

import argparse
import re
import stat
import sys
from pathlib import Path


FENCED_CODE_START_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
FENCED_CODE_END_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*$")
LIST_ITEM_PATTERN = re.compile(r"^[ \t]{0,3}(?:[-+*][ \t]+|\d+[.)][ \t]+)")
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


def strip_html_comments(line: str, comment_open: bool) -> tuple[str, bool]:
    """Remove HTML comments while retaining visible text on either side."""
    visible: list[str] = []
    cursor = 0
    while cursor < len(line):
        if comment_open:
            comment_end = line.find("-->", cursor)
            if comment_end < 0:
                return "".join(visible), True
            comment_open = False
            cursor = comment_end + 3
            continue
        comment_start = line.find("<!--", cursor)
        if comment_start < 0:
            visible.append(line[cursor:])
            break
        visible.append(line[cursor:comment_start])
        comment_open = True
        cursor = comment_start + 4
    return "".join(visible), comment_open


def hard_wrapped_prose_lines(markdown: str) -> tuple[int, ...]:
    """Return line numbers where ordinary Markdown prose is hard-wrapped."""
    wrapped: list[int] = []
    previous_is_prose = False
    previous_is_list_item = False
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
        if comment_open or "<!--" in line:
            line, comment_open = strip_html_comments(line, comment_open)

        stripped = line.strip()
        if not stripped:
            previous_is_prose = False
            previous_is_list_item = False
            continue
        is_structural = STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is not None
        is_list_item = LIST_ITEM_PATTERN.match(line) is not None
        is_prose = not is_structural
        if (previous_is_prose or previous_is_list_item) and is_prose:
            wrapped.append(line_number)
        previous_is_list_item = is_list_item
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


def main(argv: list[str] | None = None) -> int:
    """Validate a body file before it is passed to a GitHub write command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--body-file",
        type=Path,
        required=True,
        help="UTF-8 Markdown body to reject when prose is hard-wrapped",
    )
    arguments = parser.parse_args(argv)
    try:
        wrapped_lines = hard_wrapped_prose_lines(read_body_file(arguments.body_file))
        if wrapped_lines:
            lines = ", ".join(str(line) for line in wrapped_lines)
            raise ValueError(
                f"body file contains hard-wrapped prose at line(s): {lines}"
            )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("Markdown body does not contain hard-wrapped prose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
