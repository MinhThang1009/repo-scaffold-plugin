#!/usr/bin/env python3
"""Reject hard-wrapped prose in a Markdown body before a GitHub mutation."""

from __future__ import annotations

import argparse
import re
import stat
import sys
from collections import Counter
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
GENERIC_HTML_TAG_START_PATTERN = re.compile(
    r"^[ \t]{0,3}</?[A-Za-z][A-Za-z0-9.-]*(?:[ \t/>]|$)"
)
HTML_BLOCK_TAG_PATTERN = re.compile(r"^[ \t]{0,3}<([A-Za-z][A-Za-z0-9-]*)\b")
AUTOLINK_PATTERN = re.compile(r"^<(?:https?://|mailto:|[^ <>@]+@[^ <>@]+>)")
BACKTICK_RUN_PATTERN = re.compile(r"[\x60]+")
MAX_BODY_FILE_BYTES = 1024 * 1024


def strip_blockquote_markers(line: str) -> tuple[str, int]:
    """Remove nested block quote markers before inspecting their Markdown blocks."""
    depth = 0
    while True:
        match = re.match(r"^[ \t]{0,3}>[ \t]?", line)
        if match is None:
            return line, depth
        depth += 1
        line = line[match.end() :]


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
    future_run_counts: Counter[int],
) -> tuple[str, int | None]:
    """Mask inline-code spans so their Markdown-looking text stays inert."""
    runs = [
        (match.start(), match.end(), len(match.group()))
        for match in BACKTICK_RUN_PATTERN.finditer(line)
    ]
    next_same_run: dict[int, tuple[int, int] | None] = {}
    next_run_by_length: dict[int, tuple[int, int]] = {}
    for start, end, run_length in reversed(runs):
        next_same_run[start] = next_run_by_length.get(run_length)
        next_run_by_length[run_length] = (start, end)

    masked = list(line)
    cursor = 0
    run_index = 0
    active_length = delimiter_length
    if active_length is not None:
        closing_index = next(
            (
                index
                for index, (_, _, run_length) in enumerate(runs)
                if run_length == active_length
            ),
            None,
        )
        if closing_index is None:
            masked[:] = " " * len(line)
            return "".join(masked), active_length
        close_end = runs[closing_index][1]
        masked[:close_end] = " " * close_end
        cursor = close_end
        run_index = closing_index + 1
        active_length = None

    while run_index < len(runs):
        start, end, run_length = runs[run_index]
        closing_run = next_same_run[start]
        if closing_run is not None:
            close_end = closing_run[1]
            masked[start:close_end] = " " * (close_end - start)
            cursor = close_end
            while run_index < len(runs) and runs[run_index][0] < cursor:
                run_index += 1
            continue
        if future_run_counts[run_length] > 0:
            masked[start:] = " " * (len(line) - start)
            return "".join(masked), run_length
        cursor = end
        run_index += 1
    return "".join(masked), active_length


def backtick_run_lengths_by_line(
    lines: list[str],
) -> tuple[list[tuple[int, ...]], list[int]]:
    """Index inline-code delimiters while excluding comments and block code."""
    indexed: list[tuple[int, ...]] = []
    group_ids: list[int] = []
    group_id = 0
    fence_character: str | None = None
    fence_length = 0
    fence_quote_depth = 0
    comment_open = False
    html_block_tag: str | None = None
    previous_quote_depth = 0

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        line, quote_depth = strip_blockquote_markers(line)
        if fence_character is not None:
            fence_end = FENCED_CODE_END_PATTERN.match(line)
            if (
                quote_depth == fence_quote_depth
                and fence_end is not None
                and fence_end.group(1)[0] == fence_character
                and len(fence_end.group(1)) >= fence_length
            ):
                fence_character = None
                fence_length = 0
                group_id += 1
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = 0
            continue
        if html_block_tag is not None:
            if re.search(rf"</{re.escape(html_block_tag)}[ \t]*>", line, re.IGNORECASE):
                html_block_tag = None
                group_id += 1
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = 0
            continue
        if comment_open:
            line, comment_open = strip_html_comments(line, True)
        else:
            comment_start = line.find("<!--")
            backtick_start = line.find(chr(96))
            if comment_start >= 0 and (
                backtick_start < 0 or comment_start < backtick_start
            ):
                line, comment_open = strip_html_comments(line, False)
        if not line.strip():
            group_id += 1
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = 0
            continue
        fence_start = FENCED_CODE_START_PATTERN.match(line)
        if fence_start is not None:
            group_id += 1
            fence_character = fence_start.group(1)[0]
            fence_length = len(fence_start.group(1))
            fence_quote_depth = quote_depth
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = quote_depth
            continue
        is_html_block = (
            HTML_BLOCK_START_PATTERN.match(line) is not None
            or CUSTOM_HTML_BLOCK_START_PATTERN.match(line) is not None
            or GENERIC_HTML_TAG_START_PATTERN.match(line) is not None
        ) and AUTOLINK_PATTERN.match(line) is None
        if is_html_block:
            group_id += 1
            tag_match = HTML_BLOCK_TAG_PATTERN.match(line)
            tag_name = tag_match.group(1) if tag_match is not None else None
            self_closed = (
                tag_name is not None
                and re.search(
                    rf"<{re.escape(tag_name)}\b[^>]*?/\s*>", line, re.IGNORECASE
                )
                is not None
            )
            if (
                tag_name is not None
                and not self_closed
                and not re.search(
                    rf"</{re.escape(tag_name)}[ \t]*>", line, re.IGNORECASE
                )
            ):
                html_block_tag = tag_name
            else:
                group_id += 1
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = quote_depth
            continue
        is_list_item = LIST_ITEM_PATTERN.match(line) is not None
        is_other_block = (
            STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is not None
            and not is_list_item
        )
        if quote_depth and not previous_quote_depth:
            group_id += 1
        if is_list_item or is_other_block:
            group_id += 1
        indexed.append(
            tuple(len(match.group()) for match in BACKTICK_RUN_PATTERN.finditer(line))
        )
        group_ids.append(group_id)
        if is_other_block:
            group_id += 1
        previous_quote_depth = quote_depth
    return indexed, group_ids


def hard_wrapped_prose_lines(markdown: str) -> tuple[int, ...]:
    """Return line numbers where ordinary Markdown prose is hard-wrapped."""
    wrapped: list[int] = []
    previous_is_prose = False
    previous_is_list_item = False
    fence_character: str | None = None
    fence_length = 0
    fence_quote_depth = 0
    comment_open = False
    inline_code_length: int | None = None
    indented_code = False
    html_block_tag: str | None = None

    raw_lines = markdown.splitlines()
    run_lengths_by_line, group_ids = backtick_run_lengths_by_line(raw_lines)
    future_run_counts: dict[int, Counter[int]] = {}
    for group_id, run_lengths in zip(group_ids, run_lengths_by_line, strict=True):
        future_run_counts.setdefault(group_id, Counter()).update(run_lengths)
    active_group: int | None = None
    for line_index, raw_line in enumerate(raw_lines):
        group_id = group_ids[line_index]
        if active_group != group_id:
            inline_code_length = None
            active_group = group_id
        line_number = line_index + 1
        line = raw_line.rstrip("\r\n")
        group_run_counts = future_run_counts[group_id]
        for run_length in run_lengths_by_line[line_index]:
            group_run_counts[run_length] -= 1
            if group_run_counts[run_length] <= 0:
                del group_run_counts[run_length]
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
        line, quote_depth = strip_blockquote_markers(line)
        fence_start = (
            FENCED_CODE_START_PATTERN.match(line) if not comment_open else None
        )
        if fence_character is not None:
            fence_end = FENCED_CODE_END_PATTERN.match(line)
            if (
                quote_depth == fence_quote_depth
                and fence_end is not None
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
            fence_quote_depth = quote_depth
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
            line, inline_code_length, group_run_counts
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
            or GENERIC_HTML_TAG_START_PATTERN.match(line) is not None
        ) and AUTOLINK_PATTERN.match(line) is None
        if is_html_block:
            tag_match = HTML_BLOCK_TAG_PATTERN.match(line)
            tag_name = tag_match.group(1) if tag_match is not None else None
            self_closed = (
                tag_name is not None
                and re.search(
                    rf"<{re.escape(tag_name)}\b[^>]*?/\s*>", line, re.IGNORECASE
                )
                is not None
            )
            if (
                tag_name is not None
                and not self_closed
                and not re.search(
                    rf"</{re.escape(tag_name)}[ \t]*>", line, re.IGNORECASE
                )
            ):
                html_block_tag = tag_name
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
