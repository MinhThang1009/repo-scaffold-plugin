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
LIST_ITEM_PATTERN = re.compile(r"^[ \t]*(?:[-+*][ \t]+|\d{1,9}[.)][ \t]+)")
LIST_ITEM_CONTEXT_PATTERN = re.compile(r"^(?P<indent> *)(?:[-+*]|\d{1,9}[.)])[ \t]+")
STRUCTURAL_MARKDOWN_LINE_PATTERN = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}[ \t]|>[ \t]?|"
    r"(?:(?:-[ \t]*){3,}|(?:_[ \t]*){3,}|(?:\*[ \t]*){3,}|=+[ \t]*)$)"
)
HTML_BLOCK_START_PATTERN = re.compile(
    r"^[ \t]{0,3}</?(?:address|article|aside|base|basefont|blockquote|"
    r"body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|"
    r"fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|"
    r"header|hr|html|iframe|legend|li|link|main|map|menu|menuitem|nav|"
    r"noframes|ol|optgroup|option|p|param|search|section|summary|table|"
    r"source|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:[ \t>/]|$)",
    re.IGNORECASE,
)
HTML_RAW_TEXT_START_PATTERN = re.compile(
    r"^[ \t]{0,3}<(?:pre|script|style)(?:[ \t>/]|$)", re.IGNORECASE
)
HTML_RAW_TEXT_END_PATTERN = re.compile(r"</(?:pre|script|style)[ \t]*>", re.IGNORECASE)
HTML_PROCESSING_INSTRUCTION_START_PATTERN = re.compile(r"^[ \t]{0,3}<\?")
HTML_DECLARATION_START_PATTERN = re.compile(r"^[ \t]{0,3}<![A-Z]")
HTML_CDATA_START_PATTERN = re.compile(r"^[ \t]{0,3}<!\[CDATA\[")
HTML_TAG_NAME_PATTERN = re.compile(r"^[ \t]{0,3}</?([A-Za-z][A-Za-z0-9-]*)")
HTML_CDATA_END_MARKER = "]]" + ">"
HTML_COMPLETE_TAG_LINE_PATTERN = re.compile(
    r"""^[ \t]{0,3}(?:</[A-Za-z][A-Za-z0-9-]*[ \t]*>|<[A-Za-z][A-Za-z0-9-]*(?:[ \t]+[A-Za-z_:][A-Za-z0-9_.:-]*(?:[ \t]*=[ \t]*(?:[^ \t"'=<>`]+|"[^"]*"|'[^']*'))?)*[ \t]*/?>)[ \t]*$"""
)
AUTOLINK_PATTERN = re.compile(r"^<(?:https?://|mailto:|[^ <>@]+@[^ <>@]+>)")
BACKTICK_RUN_PATTERN = re.compile(r"[\x60]+")
GFM_TABLE_DELIMITER_CELL_PATTERN = re.compile(r":?-{3,}:?")
SETEXT_HEADING_UNDERLINE_PATTERN = re.compile(r"^[ \t]{0,3}(?:=+[ \t]*|-+[ \t]*)$")
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


def html_block_start(
    line: str, *, allow_type_7: bool = True
) -> tuple[bool, str | None]:
    """Return whether a line starts a CommonMark raw HTML block and its end rule."""
    if HTML_RAW_TEXT_START_PATTERN.match(line) is not None:
        return True, (
            None if HTML_RAW_TEXT_END_PATTERN.search(line) is not None else "raw-text"
        )
    if HTML_PROCESSING_INSTRUCTION_START_PATTERN.match(line) is not None:
        return True, None if "?>" in line else "processing-instruction"
    if HTML_CDATA_START_PATTERN.match(line) is not None:
        return True, None if HTML_CDATA_END_MARKER in line else "cdata"
    if HTML_DECLARATION_START_PATTERN.match(line) is not None:
        return True, None if ">" in line else "declaration"
    if HTML_BLOCK_START_PATTERN.match(line) is not None:
        return True, "blank-line"
    if (
        allow_type_7
        and HTML_COMPLETE_TAG_LINE_PATTERN.fullmatch(line) is not None
        and AUTOLINK_PATTERN.match(line) is None
    ):
        tag_match = HTML_TAG_NAME_PATTERN.match(line)
        if tag_match is not None and tag_match.group(1).casefold() not in {
            "pre",
            "script",
            "style",
        }:
            return True, "blank-line"
    return False, None


def html_block_end_reached(end_rule: str, line: str) -> bool:
    """Return whether a raw HTML block has met its type-specific end condition."""
    if end_rule == "blank-line":
        return not line.strip()
    if end_rule == "raw-text":
        return HTML_RAW_TEXT_END_PATTERN.search(line) is not None
    if end_rule == "processing-instruction":
        return "?>" in line
    if end_rule == "declaration":
        return ">" in line
    if end_rule == "cdata":
        return HTML_CDATA_END_MARKER in line
    raise ValueError(f"unknown HTML block end rule: {end_rule}")


def split_gfm_table_cells(line: str) -> tuple[str, ...] | None:
    """Split a GFM table row at unescaped pipes outside inline code spans."""
    masked_line, _ = mask_inline_code(line, None, Counter())
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    found_pipe = False
    for character in masked_line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            current.append(character)
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
            found_pipe = True
        else:
            current.append(character)
    cells.append("".join(current).strip())
    if not found_pipe:
        return None
    if masked_line.lstrip().startswith("|") and cells and not cells[0]:
        cells.pop(0)
    if masked_line.rstrip().endswith("|") and cells and not cells[-1]:
        cells.pop()
    return tuple(cells)


def gfm_table_line_indexes(lines: list[str]) -> set[int]:
    """Index table headers, delimiters, and rows using GFM's block conditions."""
    table_lines: set[int] = set()
    index = 0
    while index + 1 < len(lines):
        header, header_quote_depth = strip_blockquote_markers(lines[index])
        delimiter, delimiter_quote_depth = strip_blockquote_markers(lines[index + 1])
        header_cells = split_gfm_table_cells(header)
        delimiter_cells = split_gfm_table_cells(delimiter)
        if (
            header_quote_depth != delimiter_quote_depth
            or header_cells is None
            or delimiter_cells is None
            or len(header_cells) != len(delimiter_cells)
            or not all(
                GFM_TABLE_DELIMITER_CELL_PATTERN.fullmatch(cell) is not None
                for cell in delimiter_cells
            )
        ):
            index += 1
            continue
        table_lines.update((index, index + 1))
        row_index = index + 2
        while row_index < len(lines):
            row, row_quote_depth = strip_blockquote_markers(lines[row_index])
            if (
                not row.strip()
                or row_quote_depth != header_quote_depth
                or begins_markdown_block(row)
            ):
                break
            table_lines.add(row_index)
            row_index += 1
        index = max(index + 2, row_index)
    return table_lines


def setext_heading_line_indexes(lines: list[str]) -> set[int]:
    """Index paragraph lines that form a setext heading with a following underline."""
    heading_lines: set[int] = set()
    for underline_index, raw_line in enumerate(lines):
        underline, underline_quote_depth = strip_blockquote_markers(raw_line)
        if SETEXT_HEADING_UNDERLINE_PATTERN.fullmatch(underline) is None:
            continue
        if LIST_ITEM_CONTEXT_PATTERN.match(underline.expandtabs(4)) is not None:
            continue
        candidate_index = underline_index - 1
        while candidate_index >= 0:
            candidate, quote_depth = strip_blockquote_markers(lines[candidate_index])
            expanded_candidate = candidate.expandtabs(4)
            leading_spaces = len(expanded_candidate) - len(
                expanded_candidate.lstrip(" ")
            )
            if (
                quote_depth != underline_quote_depth
                or not candidate.strip()
                or leading_spaces > 3
                or STRUCTURAL_MARKDOWN_LINE_PATTERN.match(candidate) is not None
                or LIST_ITEM_CONTEXT_PATTERN.match(expanded_candidate) is not None
                or FENCED_CODE_START_PATTERN.match(candidate) is not None
                or FENCED_CODE_END_PATTERN.match(candidate) is not None
                or html_block_start(candidate, allow_type_7=False)[0]
            ):
                break
            candidate_index -= 1
        if candidate_index + 1 < underline_index:
            heading_lines.update(range(candidate_index + 1, underline_index + 1))
    return heading_lines


def begins_markdown_block(line: str, *, allow_type_7: bool = True) -> bool:
    """Return whether a line starts a block that interrupts a GFM table."""
    expanded_line = line.expandtabs(4)
    leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
    return (
        FENCED_CODE_START_PATTERN.match(line) is not None
        or STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is not None
        or (LIST_ITEM_PATTERN.match(line) is not None and leading_spaces < 4)
        or html_block_start(line, allow_type_7=allow_type_7)[0]
    )


def advance_list_context(
    line: str,
    context: list[tuple[int, int]],
    *,
    previous_is_prose: bool,
    previous_is_list_item: bool,
) -> tuple[bool, bool]:
    """Update open list containers and report an item start or container exit."""
    expanded_line = line.expandtabs(4)
    leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
    previous_depth = len(context)
    marker = LIST_ITEM_CONTEXT_PATTERN.match(expanded_line)
    if marker is not None:
        marker_indent = len(marker.group("indent"))
        is_list_item = marker_indent < 4 or bool(
            context and marker_indent >= context[-1][1]
        )
        if is_list_item:
            while context and marker_indent <= context[-1][0]:
                context.pop()
            while context and marker_indent < context[-1][1]:
                context.pop()
            context.append((marker_indent, marker.end()))
            return True, len(context) < previous_depth
    if line.strip() and context and leading_spaces < context[-1][1]:
        allow_type_7 = not (previous_is_prose or previous_is_list_item)
        lazy_continuation = (
            previous_is_prose or previous_is_list_item
        ) and not begins_markdown_block(line, allow_type_7=allow_type_7)
        if not lazy_continuation:
            while context and leading_spaces < context[-1][1]:
                context.pop()
    return False, len(context) < previous_depth


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
    fence_list_indent: int | None = None
    comment_open = False
    html_block: tuple[str, int, int | None] | None = None
    previous_quote_depth = 0
    previous_is_prose = False
    previous_is_list_item = False
    indented_code = False
    list_context: list[tuple[int, int]] = []
    table_lines = gfm_table_line_indexes(lines)

    for line_index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        line, quote_depth = strip_blockquote_markers(line)
        expanded_line = line.expandtabs(4)
        leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
        if html_block is not None:
            end_rule, start_quote_depth, list_indent = html_block
            list_container_ended = (
                list_indent is not None
                and bool(line.strip())
                and leading_spaces < list_indent
            )
            if quote_depth < start_quote_depth or list_container_ended:
                html_block = None
                group_id += 1
            else:
                ended = html_block_end_reached(end_rule, line)
                indexed.append(())
                group_ids.append(group_id)
                if ended:
                    html_block = None
                    group_id += 1
                previous_is_prose = False
                previous_is_list_item = False
                previous_quote_depth = quote_depth
                continue
        if fence_character is not None:
            list_container_ended = (
                fence_list_indent is not None
                and bool(line.strip())
                and leading_spaces < fence_list_indent
            )
            if quote_depth < fence_quote_depth or list_container_ended:
                fence_character = None
                fence_length = 0
                fence_list_indent = None
                group_id += 1
            else:
                fence_end = FENCED_CODE_END_PATTERN.match(line)
                if (
                    quote_depth == fence_quote_depth
                    and fence_end is not None
                    and fence_end.group(1)[0] == fence_character
                    and len(fence_end.group(1)) >= fence_length
                ):
                    fence_character = None
                    fence_length = 0
                    fence_list_indent = None
                    group_id += 1
                indexed.append(())
                group_ids.append(group_id)
                previous_quote_depth = quote_depth
                previous_is_prose = False
                previous_is_list_item = False
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
            previous_is_prose = False
            previous_is_list_item = False
            continue
        if quote_depth > previous_quote_depth:
            previous_is_prose = False
            previous_is_list_item = False
            group_id += 1
        is_list_item, list_context_shrank = advance_list_context(
            line,
            list_context,
            previous_is_prose=previous_is_prose,
            previous_is_list_item=previous_is_list_item,
        )
        if list_context_shrank:
            group_id += 1
        list_indent = list_context[-1][1] if list_context else None
        fence_start = FENCED_CODE_START_PATTERN.match(line)
        if fence_start is not None:
            group_id += 1
            fence_character = fence_start.group(1)[0]
            fence_length = len(fence_start.group(1))
            fence_quote_depth = quote_depth
            fence_list_indent = list_indent
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            continue
        starts_html_block, new_html_end_rule = html_block_start(
            line,
            allow_type_7=not (previous_is_prose or previous_is_list_item),
        )
        if starts_html_block:
            group_id += 1
            if new_html_end_rule is not None:
                html_block = (new_html_end_rule, quote_depth, list_indent)
            else:
                group_id += 1
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            continue
        if line_index in table_lines:
            group_id += 1
            indexed.append(
                tuple(
                    len(match.group()) for match in BACKTICK_RUN_PATTERN.finditer(line)
                )
            )
            group_ids.append(group_id)
            group_id += 1
            previous_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            continue
        if indented_code:
            if leading_spaces >= 4:
                indexed.append(())
                group_ids.append(group_id)
                previous_quote_depth = quote_depth
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
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            continue
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
        is_structural = is_list_item or is_other_block
        previous_is_list_item = is_list_item
        previous_is_prose = not is_structural and not line.endswith(("  ", "\\"))
    return indexed, group_ids


def hard_wrapped_prose_lines(markdown: str) -> tuple[int, ...]:
    """Return line numbers where ordinary Markdown prose is hard-wrapped."""
    wrapped: list[int] = []
    previous_is_prose = False
    previous_is_list_item = False
    fence_character: str | None = None
    fence_length = 0
    fence_quote_depth = 0
    fence_list_indent: int | None = None
    comment_open = False
    inline_code_length: int | None = None
    indented_code = False
    html_block: tuple[str, int, int | None] | None = None
    previous_quote_depth = 0
    list_context: list[tuple[int, int]] = []

    raw_lines = markdown.splitlines()
    run_lengths_by_line, group_ids = backtick_run_lengths_by_line(raw_lines)
    table_lines = gfm_table_line_indexes(raw_lines)
    setext_heading_lines = setext_heading_line_indexes(raw_lines)
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
        expanded_line = line.expandtabs(4)
        leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
        if html_block is not None:
            end_rule, start_quote_depth, list_indent = html_block
            list_container_ended = (
                list_indent is not None
                and bool(line.strip())
                and leading_spaces < list_indent
            )
            if quote_depth < start_quote_depth or list_container_ended:
                html_block = None
                inline_code_length = None
            else:
                ended = html_block_end_reached(end_rule, line)
                if ended:
                    html_block = None
                previous_is_prose = False
                previous_is_list_item = False
                previous_quote_depth = quote_depth
                continue
        if not line.strip():
            if not indented_code:
                previous_is_prose = False
                previous_is_list_item = False
            previous_quote_depth = quote_depth
            continue
        if fence_character is not None:
            list_container_ended = (
                fence_list_indent is not None and leading_spaces < fence_list_indent
            )
            if quote_depth < fence_quote_depth or list_container_ended:
                fence_character = None
                fence_length = 0
                fence_list_indent = None
                inline_code_length = None
            else:
                fence_end = FENCED_CODE_END_PATTERN.match(line)
                if (
                    quote_depth == fence_quote_depth
                    and fence_end is not None
                    and fence_end.group(1)[0] == fence_character
                    and len(fence_end.group(1)) >= fence_length
                ):
                    fence_character = None
                    fence_length = 0
                    fence_list_indent = None
                previous_is_prose = False
                previous_is_list_item = False
                previous_quote_depth = quote_depth
                continue
        if quote_depth > previous_quote_depth:
            previous_is_prose = False
            previous_is_list_item = False
            inline_code_length = None
        is_list_item, _ = advance_list_context(
            line,
            list_context,
            previous_is_prose=previous_is_prose,
            previous_is_list_item=previous_is_list_item,
        )
        list_indent = list_context[-1][1] if list_context else None
        fence_start = (
            FENCED_CODE_START_PATTERN.match(line) if not comment_open else None
        )
        if fence_start is not None:
            fence_character = fence_start.group(1)[0]
            fence_length = len(fence_start.group(1))
            fence_quote_depth = quote_depth
            fence_list_indent = list_indent
            previous_is_prose = False
            previous_is_list_item = False
            previous_quote_depth = quote_depth
            continue
        starts_html_block, new_html_end_rule = html_block_start(
            line,
            allow_type_7=not (previous_is_prose or previous_is_list_item),
        )
        if starts_html_block:
            if new_html_end_rule is not None:
                html_block = (new_html_end_rule, quote_depth, list_indent)
            previous_is_prose = False
            previous_is_list_item = False
            previous_quote_depth = quote_depth
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
        is_table_line = line_index in table_lines
        is_structural = (
            is_table_line
            or is_list_item
            or line_index in setext_heading_lines
            or STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is not None
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
        previous_quote_depth = quote_depth

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
