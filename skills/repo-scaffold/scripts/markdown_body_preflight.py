#!/usr/bin/env python3
"""Reject hard-wrapped prose in a Markdown body before a GitHub mutation."""

from __future__ import annotations

import argparse
from bisect import bisect_right
import re
import stat
import sys
from collections import Counter
from pathlib import Path


FENCED_CODE_START_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
FENCED_CODE_END_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
LIST_ITEM_PATTERN = re.compile(r"^[ \t]*(?:[-+*](?:[ \t]+|$)|\d{1,9}[.)](?:[ \t]+|$))")
LIST_ITEM_CONTEXT_PATTERN = re.compile(
    r"^(?P<indent> *)(?P<marker>[-+*]|\d{1,9}[.)])(?P<padding>[ \t]+|$)"
)
STRUCTURAL_MARKDOWN_LINE_PATTERN = re.compile(
    r"^ {0,3}(?:#{1,6}(?:[ \t]|$)|>[ \t]?|"
    r"(?:(?:-[ \t]*){3,}|(?:_[ \t]*){3,}|(?:\*[ \t]*){3,})$)"
)
HTML_BLOCK_START_PATTERN = re.compile(
    r"^ {0,3}</?(?:address|article|aside|base|basefont|blockquote|"
    r"body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|"
    r"fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|"
    r"header|hr|html|iframe|legend|li|link|main|map|menu|menuitem|nav|"
    r"noframes|ol|optgroup|option|p|param|search|section|summary|table|"
    r"source|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:[ \t]|/?>|$)",
    re.IGNORECASE,
)
HTML_RAW_TEXT_START_PATTERN = re.compile(
    r"^ {0,3}<(?:pre|script|style)(?:[ \t>]|$)", re.IGNORECASE
)
HTML_RAW_TEXT_END_PATTERN = re.compile(r"</(?:pre|script|style)[ \t]*>", re.IGNORECASE)
HTML_PROCESSING_INSTRUCTION_START_PATTERN = re.compile(r"^ {0,3}<\?")
HTML_COMMENT_START_PATTERN = re.compile(r"^ {0,3}<!--")
HTML_DECLARATION_START_PATTERN = re.compile(r"^ {0,3}<![A-Z]")
HTML_CDATA_START_PATTERN = re.compile(r"^ {0,3}<!\[CDATA\[")
HTML_TAG_NAME_PATTERN = re.compile(r"^ {0,3}</?([A-Za-z][A-Za-z0-9-]*)")
HTML_CDATA_END_MARKER = "]]" + ">"
EMPTY_BULLET_LIST_MARKER_PATTERN = re.compile(r"^ {0,3}-[ \t]*$")
HTML_INLINE_TAG_PATTERN = re.compile(
    r"""(?:</[A-Za-z][A-Za-z0-9-]*[ \t\n]*>|<[A-Za-z][A-Za-z0-9-]*(?:(?:[ \t]|\n)+[A-Za-z_:][A-Za-z0-9_.:-]*(?:(?:[ \t]|\n)*=(?:[ \t]|\n)*(?:[^ \t\r\n"'=<>`]+|"[^"]*"|'[^']*'))?)*(?:[ \t]|\n)*/?>)"""
)
HTML_COMPLETE_TAG_LINE_PATTERN = re.compile(
    r"^ {0,3}" + HTML_INLINE_TAG_PATTERN.pattern + r"[ \t]*$"
)
AUTOLINK_PATTERN = re.compile(r"^<(?:https?://|mailto:|[^ <>@]+@[^ <>@]+>)")
BACKTICK_RUN_PATTERN = re.compile(r"[\x60]+")
GFM_TABLE_DELIMITER_CELL_PATTERN = re.compile(r":?-+:?")
SETEXT_HEADING_UNDERLINE_PATTERN = re.compile(r"^ {0,3}(?:=+[ \t]*|-+[ \t]*)$")
MAX_BODY_FILE_BYTES = 1024 * 1024
MAX_INLINE_CODE_LOOKAHEAD_CHARACTERS = 4 * MAX_BODY_FILE_BYTES
GFM_LINE_ENDING_PATTERN = re.compile(r"\r\n|\r|\n")


def split_gfm_lines(markdown: str) -> list[str]:
    """Split only physical line endings recognized by the GFM specification."""
    if not markdown:
        return []
    lines = GFM_LINE_ENDING_PATTERN.split(markdown)
    if lines[-1] == "":
        lines.pop()
    return lines


def is_gfm_blank_line(line: str) -> bool:
    """Return whether a line is empty or contains only GFM blank-line spaces."""
    return all(character in " \t" for character in line)


def strip_blockquote_markers(line: str) -> tuple[str, int]:
    """Remove nested block quote markers before inspecting their Markdown blocks."""
    depth = 0
    cursor = 0
    while cursor < len(line):
        marker_start = cursor
        spaces = 0
        while cursor < len(line) and line[cursor] == " " and spaces < 3:
            cursor += 1
            spaces += 1
        if cursor == len(line) or line[cursor] != ">":
            return line[marker_start:], depth
        cursor += 1
        if cursor < len(line) and line[cursor] in " \t":
            cursor += 1
        depth += 1
    return line[cursor:], depth


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


def preceded_by_odd_backslashes(line: str, index: int) -> bool:
    """Return whether a character is preceded by an odd run of backslashes."""
    cursor = index - 1
    while cursor >= 0 and line[cursor] == "\\":
        cursor -= 1
    return (index - cursor - 1) % 2 == 1


def html_comment_start(
    line: str,
    start: int,
    html_tag_spans: list[tuple[int, int]],
) -> int | None:
    """Find the next unescaped HTML comment opener outside complete HTML tags."""
    cursor = start
    tag_index = 0
    while cursor < len(line):
        comment_start = line.find("<!--", cursor)
        if comment_start < 0:
            return None
        while (
            tag_index < len(html_tag_spans)
            and html_tag_spans[tag_index][1] <= comment_start
        ):
            tag_index += 1
        if (
            tag_index < len(html_tag_spans)
            and html_tag_spans[tag_index][0]
            <= comment_start
            < html_tag_spans[tag_index][1]
        ):
            cursor = html_tag_spans[tag_index][1]
            continue
        if preceded_by_odd_backslashes(line, comment_start):
            cursor = comment_start + 4
            continue
        return comment_start
    return None


def first_unescaped_backtick(
    line: str,
    html_tag_spans: list[tuple[int, int]],
) -> int | None:
    """Find the first unescaped code delimiter outside inline HTML tags."""
    tag_index = 0
    for match in BACKTICK_RUN_PATTERN.finditer(line):
        while (
            tag_index < len(html_tag_spans)
            and html_tag_spans[tag_index][1] <= match.start()
        ):
            tag_index += 1
        inside_html_tag = (
            tag_index < len(html_tag_spans)
            and html_tag_spans[tag_index][0]
            <= match.start()
            < html_tag_spans[tag_index][1]
        )
        if not inside_html_tag and not preceded_by_odd_backslashes(line, match.start()):
            return match.start()
    return None


def line_ends_hard_break(
    line: str,
    html_tag_spans: list[tuple[int, int]],
    *,
    inline_code_open: bool,
    html_comment_open: bool,
) -> bool:
    """Detect explicit GFM hard-break syntax outside inline code, tags, and comments."""
    if inline_code_open or html_comment_open:
        return False
    trailing_spaces = len(line) - len(line.rstrip(" "))
    if trailing_spaces >= 2:
        marker_index = len(line) - trailing_spaces
        return not any(start <= marker_index < end for start, end in html_tag_spans)
    trailing_backslashes = len(line) - len(line.rstrip("\\"))
    if trailing_backslashes % 2 == 0:
        return False
    marker_index = len(line) - 1
    return not any(start <= marker_index < end for start, end in html_tag_spans)


def strip_html_comments(
    line: str,
    comment_open: bool,
    html_tag_spans: list[tuple[int, int]] | None = None,
) -> tuple[str, bool, list[tuple[int, int]]]:
    """Remove comments while adjusting the positions of surviving inline tags."""
    if html_tag_spans is None:
        html_tag_spans = html_inline_tag_spans(line)
    cursor = 0
    comment_start = 0 if comment_open else None
    hidden_ranges: list[tuple[int, int]] = []
    while cursor < len(line):
        if comment_open:
            comment_end = line.find("-->", cursor)
            if comment_end < 0:
                assert comment_start is not None
                hidden_ranges.append((comment_start, len(line)))
                break
            assert comment_start is not None
            hidden_ranges.append((comment_start, comment_end + 3))
            comment_open = False
            cursor = comment_end + 3
            comment_start = None
            continue
        next_comment_start = html_comment_start(line, cursor, html_tag_spans)
        if next_comment_start is None:
            break
        comment_start = next_comment_start
        comment_open = True
        cursor = next_comment_start + 4

    visible_parts: list[str] = []
    visible_cursor = 0
    for hidden_start, hidden_end in hidden_ranges:
        visible_parts.append(line[visible_cursor:hidden_start])
        visible_cursor = hidden_end
    visible_parts.append(line[visible_cursor:])

    shifted_spans: list[tuple[int, int]] = []
    removed_before = 0
    hidden_range_index = 0
    for start, end in html_tag_spans:
        while (
            hidden_range_index < len(hidden_ranges)
            and hidden_ranges[hidden_range_index][1] <= start
        ):
            hidden_start, hidden_end = hidden_ranges[hidden_range_index]
            removed_before += hidden_end - hidden_start
            hidden_range_index += 1
        overlaps_hidden = (
            hidden_range_index < len(hidden_ranges)
            and hidden_ranges[hidden_range_index][0] < end
            and hidden_ranges[hidden_range_index][1] > start
        )
        if not overlaps_hidden:
            shifted_spans.append((start - removed_before, end - removed_before))
    return "".join(visible_parts), comment_open, shifted_spans


def mask_inline_code(
    line: str,
    delimiter_length: int | None,
    future_run_counts: Counter[int],
    html_tag_spans: list[tuple[int, int]] | None = None,
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

    if html_tag_spans is None:
        html_tag_spans = html_inline_tag_spans(line)
    masked = list(line)
    cursor = 0
    run_index = 0
    tag_index = 0
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
        while tag_index < len(html_tag_spans) and html_tag_spans[tag_index][1] <= start:
            tag_index += 1
        if (
            tag_index < len(html_tag_spans)
            and html_tag_spans[tag_index][0] <= start < html_tag_spans[tag_index][1]
        ):
            cursor = html_tag_spans[tag_index][1]
            while run_index < len(runs) and runs[run_index][0] < cursor:
                run_index += 1
            tag_index += 1
            continue
        if active_length is None and preceded_by_odd_backslashes(line, start):
            cursor = end
            run_index += 1
            continue
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
        return is_gfm_blank_line(line)
    if end_rule == "raw-text":
        return HTML_RAW_TEXT_END_PATTERN.search(line) is not None
    if end_rule == "processing-instruction":
        return "?>" in line
    if end_rule == "declaration":
        return ">" in line
    if end_rule == "cdata":
        return HTML_CDATA_END_MARKER in line
    raise ValueError(f"unknown HTML block end rule: {end_rule}")


def html_inline_tag_spans(line: str) -> list[tuple[int, int]]:
    """Return complete HTML tag spans that are not escaped with backslashes."""
    return html_inline_tag_spans_by_line([line])[0][0]


def html_inline_tag_spans_by_line(
    lines: list[str],
) -> tuple[list[list[tuple[int, int]]], list[bool], list[bool]]:
    """Index complete inline HTML tags and continuation lines in one Markdown body."""
    spans_by_line: list[list[tuple[int, int]]] = [[] for _ in lines]
    continues_to_next_line = [False for _ in lines]
    continues_from_previous = [False for _ in lines]
    if not lines:
        return spans_by_line, continues_to_next_line, continues_from_previous
    normalized_lines = [strip_blockquote_markers(line)[0] for line in lines]
    line_starts: list[int] = []
    cursor = 0
    for line in normalized_lines:
        line_starts.append(cursor)
        cursor += len(line) + 1
    markdown = "\n".join(normalized_lines)
    for match in HTML_INLINE_TAG_PATTERN.finditer(markdown):
        if re.search(r"\n[ \t]*\n", match.group()) is not None:
            continue
        preceding_backslashes = 0
        cursor = match.start() - 1
        while cursor >= 0 and markdown[cursor] == "\\":
            preceding_backslashes += 1
            cursor -= 1
        if preceding_backslashes % 2 == 0:
            start_line = bisect_right(line_starts, match.start()) - 1
            end_line = bisect_right(line_starts, match.end() - 1) - 1
            start_offset = match.start() - line_starts[start_line]
            end_offset = match.end() - line_starts[end_line]
            for covered_line in range(start_line, end_line + 1):
                span_start = start_offset if covered_line == start_line else 0
                span_end = (
                    end_offset
                    if covered_line == end_line
                    else len(normalized_lines[covered_line])
                )
                spans_by_line[covered_line].append((span_start, span_end))
                if covered_line != start_line:
                    continues_from_previous[covered_line] = True
                if covered_line != end_line:
                    continues_to_next_line[covered_line] = True
    return spans_by_line, continues_to_next_line, continues_from_previous


def split_gfm_table_cells(line: str) -> tuple[str, ...] | None:
    """Split a GFM table row at unescaped pipe delimiters."""
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    found_pipe = False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            current.append(character)
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip(" \t"))
            current = []
            found_pipe = True
        else:
            current.append(character)
    cells.append("".join(current).strip(" \t"))
    if not found_pipe:
        return None
    if line.lstrip(" \t").startswith("|") and cells and not cells[0]:
        cells.pop(0)
    if line.rstrip(" \t").endswith("|") and cells and not cells[-1]:
        cells.pop()
    return tuple(cells)


def gfm_table_line_indexes(lines: list[str]) -> set[int]:
    """Index table headers, delimiters, and rows using GFM's block conditions."""
    table_lines: set[int] = set()
    index = 0
    while index + 1 < len(lines):
        # A matching header and delimiter row can split a table from an
        # already-open paragraph, so a preceding blank line is not required.
        header, header_quote_depth = strip_blockquote_markers(lines[index])
        delimiter, delimiter_quote_depth = strip_blockquote_markers(lines[index + 1])
        if (
            FENCED_CODE_START_PATTERN.match(header) is not None
            or STRUCTURAL_MARKDOWN_LINE_PATTERN.match(header) is not None
            or html_block_start(header, allow_type_7=False)[0]
        ):
            index += 1
            continue
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
                is_gfm_blank_line(row)
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
    paragraph_lines: list[int] = []
    paragraph_quote_depth: int | None = None
    table_lines = gfm_table_line_indexes(lines)
    for line_index, raw_line in enumerate(lines):
        line, quote_depth = strip_blockquote_markers(raw_line)
        if is_gfm_blank_line(line) or line_index in table_lines:
            paragraph_lines.clear()
            paragraph_quote_depth = None
            continue
        if paragraph_lines and quote_depth != paragraph_quote_depth:
            paragraph_lines.clear()
            paragraph_quote_depth = None
        if SETEXT_HEADING_UNDERLINE_PATTERN.fullmatch(line) is not None:
            if (
                paragraph_lines
                and quote_depth == paragraph_quote_depth
                and EMPTY_BULLET_LIST_MARKER_PATTERN.fullmatch(line) is None
            ):
                heading_lines.update(paragraph_lines)
                heading_lines.add(line_index)
            paragraph_lines.clear()
            paragraph_quote_depth = None
            continue
        expanded_line = line.expandtabs(4)
        leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
        starts_block = (
            leading_spaces > 3
            and not paragraph_lines
            or STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is not None
            or LIST_ITEM_CONTEXT_PATTERN.match(expanded_line) is not None
            or FENCED_CODE_START_PATTERN.match(line) is not None
            or FENCED_CODE_END_PATTERN.match(line) is not None
            or HTML_COMMENT_START_PATTERN.match(line) is not None
            or html_block_start(line, allow_type_7=not paragraph_lines)[0]
        )
        if starts_block:
            paragraph_lines.clear()
            paragraph_quote_depth = None
            continue
        if not paragraph_lines:
            paragraph_quote_depth = quote_depth
        paragraph_lines.append(line_index)
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


def ends_open_paragraph(line: str) -> bool:
    """Return whether a line ends an open paragraph under GFM block rules."""
    if is_gfm_blank_line(line):
        return False
    expanded_line = line.expandtabs(4)
    leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
    list_marker = LIST_ITEM_CONTEXT_PATTERN.match(expanded_line)
    if list_marker is not None:
        if leading_spaces >= 4 or not expanded_line[list_marker.end() :].strip():
            return False
        marker = list_marker.group("marker")
        return marker[0] in "-*+" or int(marker[:-1]) == 1
    return (
        FENCED_CODE_START_PATTERN.match(line) is not None
        or STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is not None
        or SETEXT_HEADING_UNDERLINE_PATTERN.fullmatch(line) is not None
        or html_block_start(line, allow_type_7=False)[0]
    )


def is_lazy_blockquote_continuation(
    line: str,
    *,
    quote_depth: int,
    previous_quote_depth: int,
    previous_paragraph_open: bool,
) -> bool:
    """Return whether omitted quote markers lazily continue an open paragraph."""
    return (
        quote_depth < previous_quote_depth
        and previous_paragraph_open
        and not is_gfm_blank_line(line)
        and not ends_open_paragraph(line)
    )


def matching_backtick_run_ahead(
    lines: list[str],
    line_index: int,
    start_offset: int,
    delimiter_lengths: set[int],
    quote_depth: int,
    table_lines: set[int],
    *,
    scan_end: list[int] | None = None,
    work_budget: list[int] | None = None,
) -> tuple[int, int, int] | None:
    """Find a candidate code-span closer before the current Markdown block ends."""

    def record_scan_end(line_index: int) -> None:
        if scan_end is not None:
            scan_end[:] = [line_index]

    if not delimiter_lengths:
        record_scan_end(line_index - 1)
        return None
    for candidate_index in range(line_index, len(lines)):
        if work_budget is not None:
            work_budget[0] -= max(1, len(lines[candidate_index]))
            if work_budget[0] < 0:
                raise ValueError(
                    "inline-code lookahead exceeds the "
                    f"{MAX_INLINE_CODE_LOOKAHEAD_CHARACTERS}-character work budget"
                )
        candidate_line, candidate_quote_depth = strip_blockquote_markers(
            lines[candidate_index].rstrip("\r\n")
        )
        candidate_ends_paragraph = ends_open_paragraph(candidate_line)
        if (
            candidate_quote_depth > quote_depth
            or is_gfm_blank_line(candidate_line)
            or (candidate_quote_depth < quote_depth and candidate_ends_paragraph)
        ):
            record_scan_end(candidate_index - 1)
            return None
        if candidate_index > line_index and (
            candidate_index in table_lines or candidate_ends_paragraph
        ):
            record_scan_end(candidate_index - 1)
            return None
        search_start = start_offset if candidate_index == line_index else 0
        for match in BACKTICK_RUN_PATTERN.finditer(candidate_line, search_start):
            if len(match.group()) in delimiter_lengths:
                return candidate_index, match.start(), match.end()
    record_scan_end(len(lines) - 1)
    return None


def advance_list_context(
    line: str,
    context: list[tuple[int, int]],
    *,
    previous_is_prose: bool,
    previous_is_list_item: bool,
    previous_paragraph_open: bool | None = None,
) -> tuple[bool, bool]:
    """Update open list containers and report an item start or container exit."""
    expanded_line = line.expandtabs(4)
    leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
    previous_depth = len(context)
    paragraph_open = (
        previous_is_prose
        if previous_paragraph_open is None
        else previous_paragraph_open
    )
    marker = LIST_ITEM_CONTEXT_PATTERN.match(expanded_line)
    if marker is not None:
        marker_indent = len(marker.group("indent"))
        has_item_content = bool(expanded_line[marker.end() :].strip(" "))
        is_list_item = marker_indent < 4 or bool(
            context and marker_indent >= context[-1][1]
        )
        marker_text = marker.group("marker")
        if (
            is_list_item
            and paragraph_open
            and (
                not has_item_content
                or (marker_text[-1] in ".)" and int(marker_text[:-1]) != 1)
            )
        ):
            is_list_item = False
        if is_list_item:
            while context and marker_indent <= context[-1][0]:
                context.pop()
            while context and marker_indent < context[-1][1]:
                context.pop()
            padding_width = len(marker.group("padding"))
            effective_padding = (
                padding_width if has_item_content and padding_width <= 4 else 1
            )
            content_indent = (
                marker_indent + len(marker.group("marker")) + effective_padding
            )
            context.append((marker_indent, content_indent))
            return True, len(context) < previous_depth
    if not is_gfm_blank_line(line) and context and leading_spaces < context[-1][1]:
        allow_type_7 = not (paragraph_open or previous_is_list_item)
        lazy_continuation = (
            paragraph_open or previous_is_list_item
        ) and not begins_markdown_block(line, allow_type_7=allow_type_7)
        if not lazy_continuation:
            while context and leading_spaces < context[-1][1]:
                context.pop()
    return False, len(context) < previous_depth


def list_item_starts_with_paragraph(line: str) -> bool:
    """Return whether a nonempty list item starts with a paragraph block."""
    expanded_line = line.expandtabs(4)
    marker = LIST_ITEM_CONTEXT_PATTERN.match(expanded_line)
    if marker is None:
        return False
    content = expanded_line[marker.end() :]
    if (
        not content.strip(" ")
        or len(marker.group("padding")) > 4
        or HTML_COMMENT_START_PATTERN.match(content) is not None
    ):
        return False
    return not begins_markdown_block(content)


def list_item_indented_code_start_indent(
    line: str,
    context: list[tuple[int, int]],
    *,
    previous_paragraph_open: bool,
) -> int | None:
    """Return the code indentation when a list marker starts with indented code."""
    expanded_line = line.expandtabs(4)
    marker = LIST_ITEM_CONTEXT_PATTERN.match(expanded_line)
    if marker is None:
        return None
    marker_indent = len(marker.group("indent"))
    is_list_item = marker_indent < 4 or bool(
        context and marker_indent >= context[-1][1]
    )
    marker_text = marker.group("marker")
    if (
        is_list_item
        and previous_paragraph_open
        and marker_text[-1] in ".)"
        and int(marker_text[:-1]) != 1
    ):
        is_list_item = False
    padding_width = len(marker.group("padding"))
    has_item_content = bool(expanded_line[marker.end() :].strip(" "))
    if not is_list_item or not has_item_content or padding_width <= 4:
        return None
    content_indent = marker_indent + len(marker.group("marker")) + 1
    return content_indent + 4


def indented_code_start_indent(
    line: str,
    context: list[tuple[int, int]],
    *,
    previous_paragraph_open: bool,
) -> int | None:
    """Return the required indentation when this line starts an indented code block."""
    if is_gfm_blank_line(line) or previous_paragraph_open:
        return None
    expanded_line = line.expandtabs(4)
    leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
    marker = LIST_ITEM_CONTEXT_PATTERN.match(expanded_line)
    if marker is not None:
        marker_indent = len(marker.group("indent"))
        is_list_item = marker_indent < 4 or bool(
            context and marker_indent >= context[-1][1]
        )
    else:
        is_list_item = False
    if is_list_item:
        return None
    content_indent = next(
        (
            item_content_indent
            for _item_indent, item_content_indent in reversed(context)
            if leading_spaces >= item_content_indent
        ),
        0,
    )
    required_indent = content_indent + 4
    return required_indent if leading_spaces >= required_indent else None


def backtick_run_lengths_by_line(
    lines: list[str],
    html_tag_spans_by_line: list[list[tuple[int, int]]] | None = None,
    *,
    excluded_line_indexes: set[int] | None = None,
) -> tuple[list[tuple[int, ...]], list[int]]:
    """Index inline-code delimiters while excluding comments and block code."""
    if html_tag_spans_by_line is None:
        html_tag_spans_by_line, _, _ = html_inline_tag_spans_by_line(lines)
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
    previous_paragraph_open = False
    indented_code_indent: int | None = None
    indented_code_quote_depth = 0
    list_context: list[tuple[int, int]] = []
    pending_inline_backticks_by_group: dict[int, list[int]] = {}
    active_inline_code_closers: dict[int, tuple[int, int, int]] = {}
    negative_inline_code_lookahead_ends: dict[
        tuple[int, int, tuple[int, ...]], int
    ] = {}
    lookahead_work_budget = [MAX_INLINE_CODE_LOOKAHEAD_CHARACTERS]
    table_lines = gfm_table_line_indexes(lines)

    def exclude_line(line_index: int) -> None:
        if excluded_line_indexes is not None:
            excluded_line_indexes.add(line_index)

    for line_index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        line, quote_depth = strip_blockquote_markers(line)
        html_tag_spans = html_tag_spans_by_line[line_index]
        active_code_closer = active_inline_code_closers.get(group_id)
        expanded_line = line.expandtabs(4)
        leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
        if html_block is not None:
            end_rule, start_quote_depth, list_indent = html_block
            list_container_ended = (
                list_indent is not None
                and not is_gfm_blank_line(line)
                and leading_spaces < list_indent
            )
            if quote_depth < start_quote_depth or list_container_ended:
                html_block = None
                group_id += 1
            else:
                ended = html_block_end_reached(end_rule, line)
                exclude_line(line_index)
                indexed.append(())
                group_ids.append(group_id)
                if ended:
                    html_block = None
                    group_id += 1
                previous_is_prose = False
                previous_is_list_item = False
                previous_paragraph_open = False
                previous_quote_depth = quote_depth
                continue
        if fence_character is not None:
            list_container_ended = (
                fence_list_indent is not None
                and not is_gfm_blank_line(line)
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
                exclude_line(line_index)
                indexed.append(())
                group_ids.append(group_id)
                previous_quote_depth = quote_depth
                previous_is_prose = False
                previous_is_list_item = False
                previous_paragraph_open = False
                continue
        if indented_code_indent is not None and not is_gfm_blank_line(line):
            if (
                quote_depth >= indented_code_quote_depth
                and leading_spaces >= indented_code_indent
            ):
                exclude_line(line_index)
                indexed.append(())
                group_ids.append(group_id)
                previous_quote_depth = quote_depth
                previous_is_prose = False
                previous_is_list_item = False
                previous_paragraph_open = False
                continue
            indented_code_indent = None
            group_id += 1
        lazy_quote_continuation = is_lazy_blockquote_continuation(
            line,
            quote_depth=quote_depth,
            previous_quote_depth=previous_quote_depth,
            previous_paragraph_open=previous_paragraph_open,
        )
        effective_quote_depth = (
            previous_quote_depth if lazy_quote_continuation else quote_depth
        )
        quote_context_changed = effective_quote_depth != previous_quote_depth
        line_previous_is_list_item = (
            previous_is_list_item if not quote_context_changed else False
        )
        line_previous_paragraph_open = (
            previous_paragraph_open if not quote_context_changed else False
        )
        line_list_code_start_indent = (
            None
            if comment_open
            else list_item_indented_code_start_indent(
                line,
                list_context,
                previous_paragraph_open=line_previous_paragraph_open,
            )
        )
        line_fence_start = (
            not comment_open and FENCED_CODE_START_PATTERN.match(line) is not None
        )
        line_html_block_start = (
            not comment_open
            and html_block_start(
                line,
                allow_type_7=not (
                    line_previous_paragraph_open or line_previous_is_list_item
                ),
            )[0]
        )
        line_indented_code_start = (
            not comment_open
            and indented_code_start_indent(
                line,
                list_context,
                previous_paragraph_open=line_previous_paragraph_open,
            )
            is not None
        )
        skip_comment_processing = (
            line_fence_start
            or line_html_block_start
            or line_indented_code_start
            or line_list_code_start_indent is not None
        )
        if skip_comment_processing:
            pass
        elif active_code_closer is not None and line_index == active_code_closer[0]:
            code_end = active_code_closer[2]
            suffix_html_spans = [
                (start - code_end, end - code_end)
                for start, end in html_tag_spans
                if start >= code_end
            ]
            visible_suffix, comment_open, suffix_html_spans = strip_html_comments(
                line[code_end:], False, suffix_html_spans
            )
            line = line[:code_end] + visible_suffix
            html_tag_spans = [
                (start, end) for start, end in html_tag_spans if end <= code_end
            ] + [(start + code_end, end + code_end) for start, end in suffix_html_spans]
        elif comment_open:
            line, comment_open, html_tag_spans = strip_html_comments(
                line, True, html_tag_spans
            )
        else:
            comment_start = html_comment_start(line, 0, html_tag_spans)
            backtick_start = first_unescaped_backtick(line, html_tag_spans)
            comment_inside_code = (
                active_code_closer is not None and line_index <= active_code_closer[0]
            )
            if comment_start is not None and (
                backtick_start is None or comment_start < backtick_start
            ):
                if (
                    not comment_inside_code
                    and HTML_COMMENT_START_PATTERN.match(line) is None
                ):
                    pending_backticks = pending_inline_backticks_by_group.get(
                        group_id, []
                    )
                    delimiter_lengths = tuple(sorted(set(pending_backticks)))
                    lookahead_key = (
                        group_id,
                        effective_quote_depth,
                        delimiter_lengths,
                    )
                    negative_end = negative_inline_code_lookahead_ends.get(
                        lookahead_key, -1
                    )
                    scan_end: list[int] = []
                    if line_index <= negative_end:
                        active_code_closer = None
                    else:
                        active_code_closer = matching_backtick_run_ahead(
                            lines,
                            line_index,
                            comment_start + 4,
                            set(delimiter_lengths),
                            effective_quote_depth,
                            table_lines,
                            scan_end=scan_end,
                            work_budget=lookahead_work_budget,
                        )
                        if active_code_closer is None and scan_end:
                            negative_inline_code_lookahead_ends[lookahead_key] = (
                                scan_end[0]
                            )
                    if active_code_closer is not None:
                        active_inline_code_closers[group_id] = active_code_closer
                        comment_inside_code = True
                if not comment_inside_code:
                    line, comment_open, html_tag_spans = strip_html_comments(
                        line, False, html_tag_spans
                    )
        if is_gfm_blank_line(line):
            group_id += 1
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = 0
            previous_is_prose = False
            previous_is_list_item = False
            previous_paragraph_open = False
            continue
        if effective_quote_depth > previous_quote_depth:
            previous_is_prose = False
            previous_is_list_item = False
            group_id += 1
        elif effective_quote_depth < previous_quote_depth:
            previous_is_prose = False
            previous_is_list_item = False
            group_id += 1
        is_list_item, list_context_shrank = advance_list_context(
            line,
            list_context,
            previous_is_prose=previous_is_prose,
            previous_is_list_item=previous_is_list_item,
            previous_paragraph_open=line_previous_paragraph_open,
        )
        if list_context_shrank:
            group_id += 1
        list_indent = list_context[-1][1] if list_context else None
        if is_list_item and line_list_code_start_indent is not None:
            group_id += 1
            indented_code_indent = line_list_code_start_indent
            indented_code_quote_depth = quote_depth
            exclude_line(line_index)
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            previous_paragraph_open = False
            continue
        fence_start = FENCED_CODE_START_PATTERN.match(line)
        if fence_start is not None:
            group_id += 1
            fence_character = fence_start.group(1)[0]
            fence_length = len(fence_start.group(1))
            fence_quote_depth = quote_depth
            fence_list_indent = list_indent
            exclude_line(line_index)
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            previous_paragraph_open = False
            continue
        starts_html_block, new_html_end_rule = html_block_start(
            line,
            allow_type_7=not (line_previous_paragraph_open or previous_is_list_item),
        )
        if starts_html_block:
            group_id += 1
            if new_html_end_rule is not None:
                html_block = (new_html_end_rule, quote_depth, list_indent)
            else:
                group_id += 1
            exclude_line(line_index)
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            previous_paragraph_open = False
            continue
        code_indent = list_indent if list_indent is not None else 0
        if (
            leading_spaces >= code_indent + 4
            and not line_previous_paragraph_open
            and not is_list_item
        ):
            indented_code_indent = code_indent + 4
            indented_code_quote_depth = quote_depth
            exclude_line(line_index)
            indexed.append(())
            group_ids.append(group_id)
            previous_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            previous_paragraph_open = False
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
            previous_paragraph_open = False
            continue
        is_other_block = (
            STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is not None
            and not is_list_item
        )
        if effective_quote_depth and not previous_quote_depth:
            group_id += 1
        if is_list_item or is_other_block:
            group_id += 1
        indexed.append(
            tuple(len(match.group()) for match in BACKTICK_RUN_PATTERN.finditer(line))
        )
        group_ids.append(group_id)
        if is_other_block:
            group_id += 1
        line_group_id = group_ids[-1]
        line_code_closer = active_inline_code_closers.get(line_group_id)
        if line_code_closer is not None and line_index == line_code_closer[0]:
            active_inline_code_closers.pop(line_group_id)
            pending_inline_backticks_by_group.pop(line_group_id, None)
        elif line_code_closer is None:
            pending_backticks = pending_inline_backticks_by_group.setdefault(
                line_group_id, []
            )
            tag_index = 0
            for match in BACKTICK_RUN_PATTERN.finditer(line):
                while (
                    tag_index < len(html_tag_spans)
                    and html_tag_spans[tag_index][1] <= match.start()
                ):
                    tag_index += 1
                inside_html_tag = (
                    tag_index < len(html_tag_spans)
                    and html_tag_spans[tag_index][0]
                    <= match.start()
                    < html_tag_spans[tag_index][1]
                )
                if inside_html_tag:
                    continue
                run_length = len(match.group())
                if run_length in pending_backticks:
                    del pending_backticks[pending_backticks.index(run_length) :]
                elif not preceded_by_odd_backslashes(line, match.start()):
                    pending_backticks.append(run_length)
        previous_quote_depth = effective_quote_depth
        is_structural = is_list_item or is_other_block
        previous_is_list_item = is_list_item and list_item_starts_with_paragraph(line)
        previous_is_prose = not is_structural and not line.endswith(("  ", "\\"))
        if is_list_item:
            previous_paragraph_open = list_item_starts_with_paragraph(line)
        elif is_other_block or (
            line_previous_paragraph_open
            and SETEXT_HEADING_UNDERLINE_PATTERN.fullmatch(line) is not None
        ):
            previous_paragraph_open = False
        else:
            previous_paragraph_open = True
    return indexed, group_ids


def hard_wrapped_prose_lines(
    markdown: str,
    *,
    html_block_line_indexes: set[int] | None = None,
    non_prose_line_numbers: set[int] | None = None,
) -> tuple[int, ...]:
    """Return wrapped-prose lines and optionally collect opaque Markdown lines."""
    wrapped: list[int] = []
    previous_is_prose = False
    previous_is_list_item = False
    previous_paragraph_open = False
    fence_character: str | None = None
    fence_length = 0
    fence_quote_depth = 0
    fence_list_indent: int | None = None
    comment_open = False
    inline_code_length: int | None = None
    indented_code_indent: int | None = None
    indented_code_quote_depth = 0
    html_block: tuple[str, int, int | None] | None = None
    previous_quote_depth = 0
    list_context: list[tuple[int, int]] = []

    raw_lines = split_gfm_lines(markdown)
    (
        html_tag_spans_by_line,
        html_tag_starts_by_line,
        html_tag_continuations_by_line,
    ) = html_inline_tag_spans_by_line(raw_lines)
    raw_block_line_indexes: set[int] = set()
    run_lengths_by_line, group_ids = backtick_run_lengths_by_line(
        raw_lines,
        html_tag_spans_by_line,
        excluded_line_indexes=raw_block_line_indexes,
    )
    table_lines = gfm_table_line_indexes(raw_lines)
    setext_heading_lines = setext_heading_line_indexes(raw_lines)
    future_run_counts: dict[int, Counter[int]] = {}
    for group_id, run_lengths in zip(group_ids, run_lengths_by_line, strict=True):
        future_run_counts.setdefault(group_id, Counter()).update(run_lengths)
    active_group: int | None = None
    inline_html_tag_pending = False
    for line_index, raw_line in enumerate(raw_lines):
        inline_html_tag_continuation = (
            inline_html_tag_pending and html_tag_continuations_by_line[line_index]
        )
        inline_html_tag_pending = False
        group_id = group_ids[line_index]
        if active_group != group_id:
            inline_code_length = None
            active_group = group_id
        line_number = line_index + 1
        line, quote_depth = strip_blockquote_markers(raw_line.rstrip("\r\n"))
        source_line = line
        html_tag_spans = html_tag_spans_by_line[line_index]
        raw_expanded_line = line.expandtabs(4)
        raw_leading_spaces = len(raw_expanded_line) - len(raw_expanded_line.lstrip(" "))
        if indented_code_indent is not None and not is_gfm_blank_line(line):
            if (
                quote_depth >= indented_code_quote_depth
                and raw_leading_spaces >= indented_code_indent
                and line_index in raw_block_line_indexes
            ):
                previous_is_prose = False
                previous_is_list_item = False
                previous_paragraph_open = False
                previous_quote_depth = quote_depth
                continue
            indented_code_indent = None
        if html_block is not None and html_block_line_indexes is not None:
            _end_rule, start_quote_depth, list_indent = html_block
            list_container_ended = (
                list_indent is not None
                and not is_gfm_blank_line(line)
                and raw_leading_spaces < list_indent
            )
            if quote_depth >= start_quote_depth and not list_container_ended:
                html_block_line_indexes.add(line_number)
        lazy_quote_continuation = is_lazy_blockquote_continuation(
            line,
            quote_depth=quote_depth,
            previous_quote_depth=previous_quote_depth,
            previous_paragraph_open=previous_paragraph_open,
        )
        effective_quote_depth = (
            previous_quote_depth if lazy_quote_continuation else quote_depth
        )
        quote_context_changed = effective_quote_depth != previous_quote_depth
        line_previous_paragraph_open = (
            previous_paragraph_open if not quote_context_changed else False
        )
        line_list_code_start_indent = (
            None
            if comment_open
            else list_item_indented_code_start_indent(
                line,
                list_context,
                previous_paragraph_open=line_previous_paragraph_open,
            )
        )
        group_run_counts = future_run_counts[group_id]
        for run_length in run_lengths_by_line[line_index]:
            group_run_counts[run_length] -= 1
            if group_run_counts[run_length] <= 0:
                del group_run_counts[run_length]
        if line_index not in raw_block_line_indexes and comment_open:
            line, comment_open, html_tag_spans = strip_html_comments(
                line, True, html_tag_spans
            )
        elif line_index not in raw_block_line_indexes and inline_code_length is None:
            comment_start = html_comment_start(line, 0, html_tag_spans)
            backtick_start = first_unescaped_backtick(line, html_tag_spans)
            if comment_start is not None and (
                backtick_start is None or comment_start < backtick_start
            ):
                line, comment_open, html_tag_spans = strip_html_comments(
                    line, False, html_tag_spans
                )
                if is_gfm_blank_line(line):
                    previous_is_prose = False
                    previous_is_list_item = False
                    previous_paragraph_open = False
                    continue
        expanded_line = line.expandtabs(4)
        leading_spaces = len(expanded_line) - len(expanded_line.lstrip(" "))
        if html_block is not None:
            end_rule, start_quote_depth, list_indent = html_block
            list_container_ended = (
                list_indent is not None
                and not is_gfm_blank_line(line)
                and leading_spaces < list_indent
            )
            if quote_depth < start_quote_depth or list_container_ended:
                html_block = None
                inline_code_length = None
            else:
                if html_block_line_indexes is not None:
                    html_block_line_indexes.add(line_index + 1)
                ended = html_block_end_reached(end_rule, line)
                if ended:
                    html_block = None
                previous_is_prose = False
                previous_is_list_item = False
                previous_paragraph_open = False
                previous_quote_depth = quote_depth
                continue
        if is_gfm_blank_line(line):
            inline_html_tag_pending = False
            if indented_code_indent is None:
                previous_is_prose = False
                previous_is_list_item = False
                previous_paragraph_open = False
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
                previous_paragraph_open = False
                previous_quote_depth = quote_depth
                continue
        if effective_quote_depth > previous_quote_depth:
            previous_is_prose = False
            previous_is_list_item = False
            inline_code_length = None
        elif effective_quote_depth < previous_quote_depth:
            previous_is_prose = False
            previous_is_list_item = False
            inline_code_length = None
        is_list_item, _ = advance_list_context(
            line,
            list_context,
            previous_is_prose=previous_is_prose,
            previous_is_list_item=previous_is_list_item,
            previous_paragraph_open=line_previous_paragraph_open,
        )
        list_indent = list_context[-1][1] if list_context else None
        if is_list_item and line_list_code_start_indent is not None:
            indented_code_indent = line_list_code_start_indent
            indented_code_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            previous_paragraph_open = False
            previous_quote_depth = quote_depth
            inline_html_tag_pending = False
            continue
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
            previous_paragraph_open = False
            previous_quote_depth = quote_depth
            inline_html_tag_pending = False
            continue
        starts_html_block, new_html_end_rule = html_block_start(
            line,
            allow_type_7=not (line_previous_paragraph_open or previous_is_list_item),
        )
        if starts_html_block:
            if html_block_line_indexes is not None:
                html_block_line_indexes.add(line_index + 1)
            if new_html_end_rule is not None:
                html_block = (new_html_end_rule, quote_depth, list_indent)
            previous_is_prose = False
            previous_is_list_item = False
            previous_paragraph_open = False
            previous_quote_depth = quote_depth
            inline_html_tag_pending = False
            continue
        code_indent = list_indent if list_indent is not None else 0
        if (
            not is_gfm_blank_line(line)
            and leading_spaces >= code_indent + 4
            and not line_previous_paragraph_open
            and not is_list_item
        ):
            indented_code_indent = code_indent + 4
            indented_code_quote_depth = quote_depth
            previous_is_prose = False
            previous_is_list_item = False
            previous_paragraph_open = False
            previous_quote_depth = quote_depth
            inline_html_tag_pending = False
            continue
        continued_inline_code = inline_code_length is not None
        line, inline_code_length = mask_inline_code(
            line,
            inline_code_length,
            group_run_counts,
            html_tag_spans,
        )
        line, comment_open, html_tag_spans = strip_html_comments(
            line, comment_open, html_tag_spans
        )

        if is_gfm_blank_line(line):
            previous_is_prose = False
            previous_is_list_item = False
            previous_paragraph_open = False
            continue
        is_table_line = line_index in table_lines
        is_structural = (
            is_table_line
            or is_list_item
            or line_index in setext_heading_lines
            or STRUCTURAL_MARKDOWN_LINE_PATTERN.match(line) is not None
        )
        is_prose = not is_structural
        hard_break = line_ends_hard_break(
            source_line,
            html_tag_spans_by_line[line_index],
            inline_code_open=inline_code_length is not None,
            html_comment_open=comment_open,
        )
        if (
            not continued_inline_code
            and not inline_html_tag_continuation
            and (previous_is_prose or previous_is_list_item)
            and is_prose
        ):
            wrapped.append(line_number)
        previous_is_list_item = (
            is_list_item
            and not hard_break
            and list_item_starts_with_paragraph(source_line)
        )
        previous_is_prose = is_prose and not hard_break
        if is_prose:
            previous_paragraph_open = True
        elif is_list_item:
            previous_paragraph_open = list_item_starts_with_paragraph(source_line)
        elif line_index in setext_heading_lines:
            previous_paragraph_open = (
                SETEXT_HEADING_UNDERLINE_PATTERN.fullmatch(line) is None
            )
        else:
            previous_paragraph_open = False
        previous_quote_depth = effective_quote_depth
        inline_html_tag_pending = html_tag_starts_by_line[line_index]

    if non_prose_line_numbers is not None:
        non_prose_line_numbers.update(index + 1 for index in raw_block_line_indexes)
        if html_block_line_indexes is not None:
            non_prose_line_numbers.update(html_block_line_indexes)

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
