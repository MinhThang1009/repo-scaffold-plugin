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
LIST_ITEM_PATTERN = re.compile(r"^[ \t]*(?:[-+*][ \t]+|\d+[.)][ \t]+)")
STRUCTURAL_MARKDOWN_LINE_PATTERN = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]|[-+*][ \t]+|\d+[.)][ \t]+|>[ \t]?|\||"
    r"(?:[-*_][ \t]*){3,}$)"
)
HTML_BLOCK_START_PATTERN = re.compile(
    r"^[ \t]{0,3}</?(?:address|article|aside|base|blockquote|body|caption|"
    r"center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|"
    r"figcaption|figure|footer|form|h[1-6]|head|header|hr|html|iframe|"
    r"legend|li|link|main|menu|menuitem|nav|ol|p|pre|script|section|"
    r"summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:[ \t>/]|$)"
)
CUSTOM_HTML_BLOCK_START_PATTERN = re.compile(
    r"^[ \t]{0,3}</?[A-Za-z][A-Za-z0-9]*[-:][A-Za-z0-9:-]*(?:[ \t>/]|$)"
)
HTML_BLOCK_TAG_PATTERN = re.compile(r"^[ \t]{0,3}<([A-Za-z][A-Za-z0-9-]*)\b")
AUTOLINK_PATTERN = re.compile(r"^<(?:https?://|mailto:|[^ <>@]+@[^ <>@]+>)")
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


def mask_inline_code(
    line: str,
    delimiter_length: int | None,
    remaining_markdown: str = "",
) -> tuple[str, int | None]:
    """Mask inline-code spans so their Markdown-looking text stays inert."""
    masked = list(line)
    cursor = 0
    active_length = delimiter_length
    while cursor < len(line):
        if active_length is not None:
            delimiter = "`" * active_length
            close = line.find(delimiter, cursor)
            end = len(line) if close < 0 else close + active_length
            masked[cursor:end] = " " * (end - cursor)
            cursor = end
            if close < 0:
                return "".join(masked), active_length
            active_length = None
            continue
        if line[cursor] != "`":
            cursor += 1
            continue
        end = cursor
        while end < len(line) and line[end] == "`":
            end += 1
        length = end - cursor
        delimiter = "`" * length
        close = line.find(delimiter, end)
        if close < 0:
            if delimiter not in remaining_markdown:
                cursor = end
                continue
            masked[cursor:] = " " * (len(line) - cursor)
            return "".join(masked), length
        mask_end = close + length
        masked[cursor:mask_end] = " " * (mask_end - cursor)
        cursor = mask_end
    return "".join(masked), active_length


def hard_wrapped_prose_lines(markdown: str) -> tuple[int, ...]:
    """Return line numbers where ordinary Markdown prose is hard-wrapped."""
    wrapped: list[int] = []
    previous_is_prose = False
    previous_is_list_item = False
    fence_character: str | None = None
    fence_length = 0
    comment_open = False
    inline_code_length: int | None = None
    indented_code = False
    html_block_tag: str | None = None

    raw_lines = markdown.splitlines()
    for line_index, raw_line in enumerate(raw_lines):
        line_number = line_index + 1
        line = raw_line.rstrip("\r\n")
        if comment_open:
            line, comment_open = strip_html_comments(line, True)
        if not line.strip():
            if not indented_code:
                previous_is_prose = False
                previous_is_list_item = False
            continue
        comment_start = line.find("<!--")
        backtick_start = line.find("`")
        if comment_start >= 0 and (
            backtick_start < 0 or comment_start < backtick_start
        ):
            line, comment_open = strip_html_comments(line, False)
            if not line.strip():
                previous_is_prose = False
                previous_is_list_item = False
                continue
        fence_start = (
            FENCED_CODE_START_PATTERN.match(line) if not comment_open else None
        )
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
            previous_is_list_item = False
            continue
        if fence_start is not None:
            fence_character = fence_start.group(1)[0]
            fence_length = len(fence_start.group(1))
            previous_is_prose = False
            previous_is_list_item = False
            continue
        if html_block_tag is not None:
            if re.search(rf"</{re.escape(html_block_tag)}[ \t]*>", line, re.IGNORECASE):
                html_block_tag = None
            previous_is_prose = False
            previous_is_list_item = False
            continue
        continued_inline_code = inline_code_length is not None
        line, inline_code_length = mask_inline_code(
            line, inline_code_length, "\n".join(raw_lines[line_index + 1 :])
        )
        line, comment_open = strip_html_comments(line, comment_open)

        stripped = line.strip()
        if not stripped:
            previous_is_prose = False
            previous_is_list_item = False
            continue
        expanded_line = line.expandtabs(4)
        leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
        is_list_item = LIST_ITEM_PATTERN.match(line) is not None
        if indented_code:
            if leading_spaces >= 4:
                previous_is_prose = False
                previous_is_list_item = False
                continue
            indented_code = False
        if (
            leading_spaces >= 4
            and not previous_is_prose
            and not previous_is_list_item
            and not is_list_item
        ):
            indented_code = True
            previous_is_prose = False
            previous_is_list_item = False
            continue
        is_html_block = (
            HTML_BLOCK_START_PATTERN.match(line) is not None
            or CUSTOM_HTML_BLOCK_START_PATTERN.match(line) is not None
        ) and AUTOLINK_PATTERN.match(line) is None
        if is_html_block:
            tag_match = HTML_BLOCK_TAG_PATTERN.match(line)
            if tag_match is not None and not re.search(
                rf"</{re.escape(tag_match.group(1))}[ \t]*>", line, re.IGNORECASE
            ):
                html_block_tag = tag_match.group(1)
        is_structural = (
            STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is not None or is_html_block
        )
        is_prose = not is_structural
        if (
            not continued_inline_code
            and (previous_is_prose or previous_is_list_item)
            and is_prose
        ):
            wrapped.append(line_number)
        previous_is_list_item = is_list_item
        previous_is_prose = is_prose and not line.endswith(("  ", "\\"))

    return tuple(wrapped)


def read_body_file(path: Path) -> str:
    """Read a bounded regular UTF-8 body file without following links."""
    if is_link_or_reparse(path) or not path.is_file():
        raise ValueError(f"body file must be a regular non-linked file: {path}")
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_BODY_FILE_BYTES + 1)
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
