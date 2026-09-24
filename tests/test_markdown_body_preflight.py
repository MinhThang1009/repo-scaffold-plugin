from __future__ import annotations

import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ROOT_PREFLIGHT = PLUGIN_ROOT / "scripts" / "markdown_body_preflight.py"
SKILL_PREFLIGHT = (
    PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "markdown_body_preflight.py"
)


class MarkdownBodyPreflightTests(unittest.TestCase):
    def run_preflight(
        self, script: Path, body_text: str
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            body_file = Path(directory) / "body.md"
            body_file.write_text(body_text, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(script), "--body-file", str(body_file)],
                cwd=PLUGIN_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )

    def test_root_entrypoint_rejects_hard_wrapped_prose(self) -> None:
        result = self.run_preflight(ROOT_PREFLIGHT, "First line\ncontinued line\n")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("hard-wrapped prose at line(s): 2", result.stderr)

    def test_bundled_entrypoint_accepts_structural_markdown(self) -> None:
        result = self.run_preflight(
            SKILL_PREFLIGHT,
            "# Report\n\nA complete paragraph.\n\n- A list item\n- Another item\n\n| cell | cell |\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("does not contain hard-wrapped prose", result.stdout)

    def test_root_wrapper_runs_the_bundled_checker_in_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            body_file = Path(directory) / "body.md"
            body_file.write_text("Complete paragraph.\n", encoding="utf-8")
            argv = [str(ROOT_PREFLIGHT), "--body-file", str(body_file)]

            runpy.run_path(str(ROOT_PREFLIGHT), run_name="markdown_body_preflight")
            success_output = StringIO()
            with mock.patch.object(sys, "argv", argv), redirect_stdout(success_output):
                with self.assertRaises(SystemExit) as success:
                    runpy.run_path(str(ROOT_PREFLIGHT), run_name="__main__")
            self.assertEqual(success.exception.code, 0)
            self.assertIn(
                "does not contain hard-wrapped prose", success_output.getvalue()
            )

            body_file.write_text("First line\ncontinued line\n", encoding="utf-8")
            error_output = StringIO()
            with mock.patch.object(sys, "argv", argv), redirect_stderr(error_output):
                with self.assertRaises(SystemExit) as failure:
                    runpy.run_path(str(ROOT_PREFLIGHT), run_name="__main__")
            self.assertEqual(failure.exception.code, 1)
            self.assertIn("hard-wrapped prose at line(s): 2", error_output.getvalue())

    def test_rejects_list_continuations_and_inline_comment_prose(self) -> None:
        self.assertEqual(
            _bundled_lines("Paragraph ends here.\n> New quoted paragraph.\n"),
            (),
        )
        for body, line in (
            ("- First list item\n  continuation\n", 2),
            ("First part <!-- inline note -->\ncontinuation\n", 2),
            ("First <!-- inline note --> visible\ncontinuation\n", 2),
            (
                "Paragraph ends here.\n> First quoted line\n> continued quote\n",
                3,
            ),
            ("> First quoted line\n> continued quoted line\n", 2),
            ("> First quoted line\ncontinued lazy quote line\n", 2),
        ):
            with self.subTest(body=body):
                result = self.run_preflight(SKILL_PREFLIGHT, body)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"hard-wrapped prose at line(s): {line}", result.stderr)

    def test_backticks_inside_inline_html_attributes_are_not_code_delimiters(
        self,
    ) -> None:
        body = 'Inline <span title="`"> first line\ncontinued prose `literal`\n'

        self.assertEqual(_bundled_lines(body), (2,))

    def test_backticks_inside_multiline_inline_html_attributes_are_inert(self) -> None:
        for body, expected in (
            (
                'Inline <span title="`\n'
                'continued attribute"> first line\ncontinued prose `literal`\n',
                (3,),
            ),
            (
                '> Inline <span title="`\n'
                '> continued attribute"> first line\n'
                "> continued prose `literal`\n",
                (3,),
            ),
            (
                'Inline <span title="`\ncontinued\nattribute"> first line\n'
                "continued prose `literal`\n",
                (4,),
            ),
        ):
            with self.subTest(body=body):
                self.assertEqual(_bundled_lines(body), expected)

    def test_html_comment_markers_in_escaped_text_and_attributes_are_inert(
        self,
    ) -> None:
        escaped_comment = (
            r"Use \<!-- as a literal token"
            "\n"
            "First prose line\ncontinued prose\n"
        )
        self.assertEqual(_bundled_lines(escaped_comment), (2, 3))

        attribute_comment = (
            '<span title="<!--">First prose line\ncontinued prose</span>\n'
        )
        self.assertEqual(_bundled_lines(attribute_comment), (2,))

        tick = chr(96)
        comment_before_attribute = (
            '<!-- hidden --> <span title="'
            + tick
            + '">First prose line\ncontinued prose '
            + tick
            + "literal"
            + tick
            + "\nthird prose line\n"
        )
        self.assertEqual(_bundled_lines(comment_before_attribute), (2, 3))

    def test_escaped_tag_like_text_does_not_mask_inline_code_delimiters(self) -> None:
        body = (
            'Escaped \\<span title="`"> first paragraph line\n'
            "continued code span` after.\n"
        )

        self.assertEqual(_bundled_lines(body), ())

    def test_handles_nested_lists_code_spans_autolinks_and_code_blocks(self) -> None:
        rejected = "- outer\n  - inner\n    - third level\n      continuation\n"
        self.assertEqual(_bundled_lines(rejected), (4,))
        accepted = (
            "Use `<!--` literally.\n\n"
            "<https://example.test> is a complete paragraph.\n\n"
            "<div>inline HTML</div>\n\n"
            "<div>\nFirst line inside HTML\ncontinued inside HTML\n</div>\n\n"
            '<svg>\n<circle cx="1" cy="1" />\n<circle cx="2" cy="2" />\n</svg>\n\n'
            "<custom-element>\nFirst custom line\ncontinued custom line\n</custom-element>\n\n"
            "> ```text\n> quoted code line\n> continued code line\n> ```\n\n"
            "> First quote.\n>\n> A separate quote paragraph.\n\n"
            "    print(1)\n    print(2)\n\n"
            "\tprint(3)\n\tprint(4)\n\n"
            "- Item\n```text\ncode\n```\nNew paragraph.\n"
        )
        self.assertEqual(_bundled_lines(accepted), ())
        prose_after_code = (
            "Literal ``` marker.\n```text\ncode\n```\n"
            "First paragraph line.\ncontinued paragraph line.\n"
        )
        self.assertEqual(_bundled_lines(prose_after_code), (6,))
        prose_after_comment = (
            "Literal ``` marker.\n<!-- ``` hidden -->\n"
            "First paragraph line.\ncontinued paragraph line.\n"
        )
        self.assertEqual(_bundled_lines(prose_after_comment), (4,))
        code_spans_do_not_cross_paragraphs = (
            "First paragraph has " + chr(96) + "\n\n"
            "Second paragraph has " + chr(96) + "literal\nand continues here.\n"
        )
        self.assertEqual(_bundled_lines(code_spans_do_not_cross_paragraphs), (4,))
        code_spans_do_not_cross_block_headings = (
            "First paragraph has " + chr(96) + "\n"
            "# Heading has " + chr(96) + "\n"
            "Second paragraph line one.\nsecond paragraph line two.\n"
        )
        self.assertEqual(_bundled_lines(code_spans_do_not_cross_block_headings), (4,))

    def test_html_blocks_follow_commonmark_start_and_end_conditions(self) -> None:
        cases = (
            (
                "inline HTML does not start a block",
                "<span>First paragraph line\ncontinued paragraph line\n",
                (2,),
            ),
            (
                "a slash must complete a self-closing type 6 tag",
                "<div/x>\nFirst prose line\ncontinued prose line\n\n",
                (2, 3),
            ),
            (
                "a slash is not a raw-text type 1 delimiter",
                "<pre/x>\nFirst prose line\ncontinued prose line\n\n",
                (2, 3),
            ),
            (
                "a complete self-closing type 6 tag starts a block",
                "<div/>\nraw content\ncontinued raw content\n\n",
                (),
            ),
            (
                "type 1 block closes on any matching family tag",
                "<script>\nraw content\n</style>\n\n"
                "First paragraph line\ncontinued paragraph line\n",
                (6,),
            ),
            (
                "type 6 block ends at a blank line",
                "<div>\nraw content\n\n"
                "First paragraph line\ncontinued paragraph line\n",
                (5,),
            ),
            (
                "type 7 block ends at a blank line",
                "<custom-widget>\nraw content\n\n"
                "First paragraph line\ncontinued paragraph line\n",
                (5,),
            ),
            (
                "textarea is type 7 in GFM and ends at a blank line",
                "<textarea>\nraw content\n\n"
                "First paragraph line\ncontinued paragraph line\n",
                (5,),
            ),
            (
                "type 7 block cannot interrupt an open paragraph",
                "First paragraph line\n<custom-widget>\ncontinued paragraph\n",
                (2, 3),
            ),
            (
                "fence markers inside raw HTML do not extend the HTML block",
                "<div>\n```text\nraw content\n\n"
                "First paragraph line\ncontinued paragraph line\n",
                (6,),
            ),
            (
                "processing instruction content is raw HTML",
                "<?processor\nraw content\n?>\n",
                (),
            ),
            (
                "single-line raw-text HTML blocks do not leak parser state",
                "<script></script>\nFirst prose line\ncontinued prose\n",
                (3,),
            ),
            (
                "declaration content is raw HTML",
                "<!DOCTYPE html\nraw content\n>\n",
                (),
            ),
            (
                "CDATA content is raw HTML",
                "<![CDATA[\nraw content\ncontinued raw content\n]]>\n",
                (),
            ),
        )
        for name, body, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(_bundled_lines(body), expected)

    def test_only_gfm_tables_are_structural_table_rows(self) -> None:
        self.assertEqual(
            _bundled_lines(
                "First paragraph line\n| this is not a table\ncontinued prose\n"
            ),
            (2, 3),
        )
        self.assertEqual(
            _bundled_lines(
                "Header one | Header two\n--- | ---\nValue one | Value two\n"
            ),
            (),
        )
        self.assertEqual(
            _bundled_lines("| Header | Value |\n| - | - |\n| row | value |\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines(
                "> Header one | Header two\n> --- | ---\n> Value one | Value two\n"
            ),
            (),
        )
        split_table_cells = _bundled_module().split_gfm_table_cells
        escaped_and_code_pipes = split_table_cells(
            r"| escaped \| pipe | `literal\|pipe` |"
        )
        self.assertEqual(len(escaped_and_code_pipes), 2)
        unescaped_code_pipe = split_table_cells(r"| `literal|pipe` |")
        self.assertEqual(len(unescaped_code_pipe), 2)
        html_attribute_pipe = _bundled_module().split_gfm_table_cells(
            '| <span title="left|right">Header</span> | Value |'
        )
        self.assertEqual(len(html_attribute_pipe), 3)
        escaped_html_attribute_pipe = split_table_cells(
            r'| <span title="left\|right">Header</span> | Value |'
        )
        self.assertEqual(len(escaped_html_attribute_pipe), 2)

        invalid_table_with_code_pipe = (
            "| `Header|inside` | Value |\n"
            "| --- | --- |\n"
            "First prose line\ncontinued prose line\n"
        )
        self.assertEqual(_bundled_lines(invalid_table_with_code_pipe), (2, 3, 4))

        invalid_table_with_html_attribute_pipe = (
            '| <span title="left|right">Header | Value |\n'
            "| --- | --- |\n"
            "First prose line\ncontinued prose line\n"
        )
        self.assertEqual(
            _bundled_lines(invalid_table_with_html_attribute_pipe), (2, 3, 4)
        )

    def test_gfm_table_can_start_inside_an_open_paragraph(self) -> None:
        self.assertEqual(
            _bundled_lines(
                "Paragraph first line\n| Header | Value |\n"
                "| --- | --- |\n| row | value |\n"
            ),
            (),
        )
        self.assertEqual(
            _bundled_lines(
                "Paragraph first line\nHeader | Value\n--- | ---\nrow | value\n"
            ),
            (),
        )
        self.assertEqual(
            _bundled_lines(
                "Paragraph first line\ncontinued paragraph line\n"
                "| Header | Value |\n| --- | --- |\n| row | value |\n"
            ),
            (2,),
        )
        self.assertEqual(
            _bundled_lines(
                "> Paragraph first line\n> | Header | Value |\n"
                "> | --- | --- |\n> | row | value |\n"
            ),
            (),
        )
        self.assertEqual(
            _bundled_lines("- Header | Value |\n  | --- | --- |\n  | row | value |\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("```text\n\n| Header | Value |\n| --- | --- |\n````\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("# Header | Value |\n| --- | --- |\n| row | value |\n"),
            (3,),
        )
        self.assertEqual(
            _bundled_lines(
                "Paragraph.\n> Quoted blockquote.\n"
                "New paragraph first line\ncontinued prose\n"
            ),
            (3, 4),
        )
        self.assertEqual(
            _bundled_lines(
                "> > Inner paragraph.\n"
                "> Parent paragraph first line\n> continued parent paragraph\n"
            ),
            (2, 3),
        )

    def test_lazy_blockquote_continuations_keep_paragraph_context(self) -> None:
        self.assertEqual(
            _bundled_lines(
                "> > First paragraph line\n"
                "> continued paragraph line\n"
                "> final paragraph line\n"
            ),
            (2, 3),
        )
        self.assertEqual(
            _bundled_lines(
                "> > First paragraph line  \n"
                "> continued paragraph line\n"
                "> final paragraph line\n"
            ),
            (3,),
        )
        self.assertEqual(
            _bundled_lines("> `inline code starts\ncode ends`\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines(
                "> > First paragraph line\n"
                "continued paragraph line\n"
                "final paragraph line\n"
            ),
            (2, 3),
        )
        self.assertEqual(
            _bundled_lines(
                "> > First paragraph line\n"
                "> # New heading\n"
                "New paragraph first line\n"
                "continued paragraph line\n"
            ),
            (4,),
        )
        self.assertEqual(
            _bundled_lines("> > `inline code starts\n> code ends`\n"),
            (),
        )

    def test_lazy_blockquote_boundary_classification(self) -> None:
        module = _bundled_module()
        self.assertTrue(
            module.is_lazy_blockquote_continuation(
                "continuation",
                quote_depth=1,
                previous_quote_depth=2,
                previous_paragraph_open=True,
            )
        )
        self.assertFalse(
            module.is_lazy_blockquote_continuation(
                "# Heading",
                quote_depth=1,
                previous_quote_depth=2,
                previous_paragraph_open=True,
            )
        )
        self.assertFalse(
            module.is_lazy_blockquote_continuation(
                "continuation",
                quote_depth=1,
                previous_quote_depth=2,
                previous_paragraph_open=False,
            )
        )
        self.assertFalse(
            module.is_lazy_blockquote_continuation(
                "continuation",
                quote_depth=2,
                previous_quote_depth=2,
                previous_paragraph_open=True,
            )
        )
        self.assertFalse(
            module.is_lazy_blockquote_continuation(
                "",
                quote_depth=1,
                previous_quote_depth=2,
                previous_paragraph_open=True,
            )
        )
        for line in (
            "",
            "plain text",
            "2. item",
            "1.",
            "    - code",
            "<custom-tag>",
        ):
            with self.subTest(line=line):
                self.assertFalse(module.ends_open_paragraph(line))
        for line in (
            "# heading",
            "---",
            "===",
            "```text",
            "- item",
            "1. item",
            "<div>",
        ):
            with self.subTest(line=line):
                self.assertTrue(module.ends_open_paragraph(line))

    def test_setext_headings_and_indented_list_like_code_remain_structural(
        self,
    ) -> None:
        self.assertEqual(_bundled_lines("Section heading\n================\n"), ())
        self.assertEqual(
            _bundled_lines(
                "A multi-line setext heading\nwith a second heading line\n===\n"
            ),
            (),
        )
        self.assertEqual(
            _bundled_lines("    - literal code line\n    continuation\n"), ()
        )
        self.assertEqual(_bundled_lines("===\ncontinued prose\n"), (2,))
        self.assertEqual(
            _bundled_lines(
                "Paragraph before comment\n<!-- block comment -->\n"
                "===\ncontinued prose\n"
            ),
            (4,),
        )

    def test_list_continuation_indent_is_relative_to_item_content(self) -> None:
        self.assertEqual(
            _bundled_lines(
                "- item\n\n    First continuation line\n    second continuation line\n"
            ),
            (4,),
        )
        self.assertEqual(
            _bundled_lines(
                "- outer\n  - inner\n\n"
                "      First nested continuation\n"
                "      second nested continuation\n"
            ),
            (5,),
        )
        self.assertEqual(
            _bundled_lines(
                "- item\n\n      literal code line\n      literal code continuation\n"
            ),
            (),
        )
        tick = chr(96)
        code_span_body = (
            "- item\n\n"
            f"    First line with {tick}code\n"
            f"    closing {tick} and more text\n"
            "    final continuation line\n"
        )
        self.assertEqual(
            _bundled_lines(code_span_body),
            (5,),
        )
        self.assertEqual(
            _bundled_lines(
                "-    padded marker\n\n"
                "     first continuation\n"
                "     second continuation\n"
            ),
            (4,),
        )

    def test_list_items_starting_with_indented_code_keep_content_context(self) -> None:
        self.assertEqual(
            _bundled_lines(
                " 1.     indented code\n\n"
                "    first paragraph line\n"
                "    second paragraph line\n\n"
                "        more code\n"
            ),
            (4,),
        )
        self.assertEqual(
            _bundled_lines(
                "1.     first code line\n"
                "       second code line\n\n"
                "   first paragraph line\n"
                "   continued paragraph line\n"
            ),
            (5,),
        )
        self.assertEqual(
            _bundled_lines(
                "1.     <!-- literal code\n"
                "       continued literal code\n\n"
                "   first paragraph line\n"
                "   continued paragraph line\n"
            ),
            (5,),
        )
        self.assertEqual(
            _bundled_lines("-\n      first code line\n      second code line\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("-    \n      first code line\n      second code line\n"),
            (),
        )

    def test_ordered_list_start_number_one_is_required_to_interrupt_prose(self) -> None:
        self.assertEqual(
            _bundled_lines("First paragraph line\n2. second paragraph line\n"),
            (2,),
        )
        self.assertEqual(
            _bundled_lines("First paragraph line\n1. first list item\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("First paragraph line\n01. first list item\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("First paragraph line\n\n2. first list item\n"),
            (),
        )

    def test_hard_break_keeps_the_paragraph_open_for_block_precedence(self) -> None:
        self.assertEqual(
            _bundled_lines("Paragraph first line  \n1.\ncontinued prose line\n"),
            (3,),
        )
        self.assertEqual(
            _bundled_lines(
                "Paragraph first line  \n"
                "    middle paragraph line\n"
                "    last paragraph line\n"
            ),
            (3,),
        )
        self.assertEqual(
            _bundled_lines("- first list paragraph  \n  1.\n  continued paragraph\n"),
            (3,),
        )

    def test_comments_inside_non_prose_blocks_do_not_hide_later_wrapping(self) -> None:
        fence = chr(96) * 3
        cases = (
            (
                "fenced code",
                f"{fence}text\n<!-- literal in code\n{fence}\n"
                "First prose line\ncontinued prose line\n",
                (5,),
            ),
            (
                "indented code",
                "    <!-- literal in code\nFirst prose line\ncontinued prose line\n",
                (3,),
            ),
            (
                "raw HTML block",
                "<script>\n<!-- literal raw text\n</script>\n"
                "First prose line\ncontinued prose line\n",
                (5,),
            ),
        )
        for name, body, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(_bundled_lines(body), expected)

    def test_only_valid_thematic_breaks_are_structural(self) -> None:
        self.assertEqual(
            _bundled_lines("First paragraph line\n_-_\ncontinued prose\n"),
            (2, 3),
        )
        self.assertEqual(
            _bundled_lines("Paragraph\n\n- - -\n\nNew paragraph\ncontinued\n"),
            (6,),
        )
        self.assertEqual(
            _bundled_lines(
                "First paragraph line\n1234567890. not a list\ncontinued prose\n"
            ),
            (2, 3),
        )
        self.assertEqual(_bundled_lines("Paragraph before empty list item\n-\n"), (2,))
        self.assertEqual(_bundled_lines("Paragraph before empty list item\n\n-\n"), ())

    def test_empty_atx_headings_are_structural(self) -> None:
        for heading in ("#", "##", "######", "   ###   ", "> #"):
            with self.subTest(heading=heading):
                quote = "> " if heading.startswith(">") else ""
                body = (
                    f"{heading}\n{quote}First paragraph line\n{quote}continued prose\n"
                )
                self.assertEqual(_bundled_lines(body), (3,))

        self.assertEqual(
            _bundled_lines("#######\nFirst paragraph line\ncontinued prose\n"),
            (2, 3),
        )

    def test_tabs_use_gfm_four_column_block_indentation(self) -> None:
        self.assertEqual(
            _bundled_lines(
                "Paragraph first line\n\t# not an ATX heading\ncontinued prose\n"
            ),
            (2, 3),
        )
        self.assertEqual(
            _bundled_lines(
                "Paragraph first line\n\t```text\ncode-looking continuation\n````\n"
            ),
            (2, 3),
        )
        self.assertEqual(
            _bundled_lines(
                "Paragraph first line\n\t> not a blockquote\ncontinued prose\n"
            ),
            (2, 3),
        )
        self.assertEqual(
            _bundled_lines(
                "Paragraph first line\n\t<div>not an HTML block\ncontinued prose\n"
            ),
            (2, 3),
        )

    def test_markdown_parser_boundary_helpers_fail_closed(self) -> None:
        module = _bundled_module()
        self.assertEqual(module.html_block_start("</pre>"), (False, None))
        self.assertEqual(module.html_block_start("<script></script>"), (True, None))
        with self.assertRaisesRegex(ValueError, "unknown HTML block end rule"):
            module.html_block_end_reached("unknown", "line")
        self.assertEqual(module.setext_heading_line_indexes(["Title", "- "]), set())
        self.assertEqual(module.setext_heading_line_indexes(["# Title", "==="]), set())
        self.assertEqual(module.setext_heading_line_indexes(["==="]), set())
        self.assertEqual(
            module.strip_blockquote_markers("> " * 4096 + "body"),
            ("body", 4096),
        )
        self.assertEqual(
            module.strip_blockquote_markers("    > body"), ("    > body", 0)
        )
        html_block_lines: set[int] = set()
        module.hard_wrapped_prose_lines(
            "<pre>\n## hidden heading\n</pre>\n\n"
            "<div>\n- [ ] hidden checklist\n\n## visible heading\n",
            html_block_line_indexes=html_block_lines,
        )
        self.assertEqual(html_block_lines, {1, 2, 3, 5, 6, 7})
        list_html_block_lines: set[int] = set()
        module.hard_wrapped_prose_lines(
            "- item\n  <pre>\ncontinued prose outside the list\n",
            html_block_line_indexes=list_html_block_lines,
        )
        self.assertEqual(list_html_block_lines, {2})
        tick = chr(96)
        opening = f"opening {tick} before <!-- note -->\n"
        self.assertIsNone(
            module.matching_backtick_run_ahead(["text"], 0, 0, set(), 0, set())
        )
        self.assertEqual(
            module.matching_backtick_run_ahead(
                [opening, "continued prose\n", f"closing {tick} after\n"],
                0,
                opening.index("<!--") + 4,
                {1},
                0,
                set(),
            ),
            (2, 8, 9),
        )
        self.assertEqual(
            module.matching_backtick_run_ahead(
                [
                    "opening\n",
                    "other " + tick * 2 + "\n",
                    "closing " + tick + "\n",
                ],
                0,
                0,
                {1},
                0,
                set(),
            ),
            (2, 8, 9),
        )
        self.assertIsNone(
            module.matching_backtick_run_ahead(
                ["opening\n", "", f"closing {tick}\n"], 0, 0, {1}, 0, set()
            )
        )
        self.assertIsNone(
            module.matching_backtick_run_ahead(
                ["opening\n", "# New block\n", f"closing {tick}\n"],
                0,
                0,
                {1},
                0,
                set(),
            )
        )
        self.assertIsNone(
            module.matching_backtick_run_ahead(
                ["opening\n", "Title\n", "---\n", f"closing {tick}\n"],
                0,
                0,
                {1},
                0,
                set(),
            )
        )
        self.assertIsNone(
            module.matching_backtick_run_ahead(
                ["opening\n", f"> closing {tick}\n"], 0, 0, {1}, 0, set()
            )
        )
        self.assertIsNone(
            module.matching_backtick_run_ahead(
                ["opening\n", "no closer\n"], 0, 0, {1}, 0, set()
            )
        )
        self.assertIsNone(
            module.matching_backtick_run_ahead(
                ["opening\n", f"closing {tick}\n"], 0, 0, {1}, 0, {1}
            )
        )

        context = [(0, 2), (2, 4)]
        is_item, context_exited = module.advance_list_context(
            "   - sibling",
            context,
            previous_is_prose=False,
            previous_is_list_item=False,
        )
        self.assertTrue(is_item)
        self.assertFalse(context_exited)
        self.assertEqual(context, [(0, 2), (3, 5)])

        is_item, context_exited = module.advance_list_context(
            "# New block",
            context,
            previous_is_prose=True,
            previous_is_list_item=False,
        )
        self.assertFalse(is_item)
        self.assertTrue(context_exited)
        self.assertEqual(context, [])

        context = [(0, 2), (3, 5)]
        is_item, context_exited = module.advance_list_context(
            "lazy continuation",
            context,
            previous_is_prose=True,
            previous_is_list_item=False,
        )
        self.assertFalse(is_item)
        self.assertFalse(context_exited)
        self.assertEqual(context, [(0, 2), (3, 5)])

    def test_non_prose_line_collection_includes_code_and_html_blocks(self) -> None:
        module = _bundled_module()
        non_prose_lines: set[int] = set()
        html_block_lines: set[int] = set()
        markdown = (
            "<!-- repo-scaffold:pr-template=feature -->\n"
            "\n"
            "    <!-- repo-scaffold:required-checklist:start -->\n"
            "    - [x] literal code, not a checklist section\n"
            "\n"
            "```markdown\n"
            "## hidden heading\n"
            "```\n"
            "<pre>\n"
            "raw HTML content\n"
            "</pre>\n"
            "## Visible heading\n"
        )

        self.assertEqual(
            module.hard_wrapped_prose_lines(
                markdown,
                html_block_line_indexes=html_block_lines,
                non_prose_line_numbers=non_prose_lines,
            ),
            (),
        )
        self.assertEqual(non_prose_lines, {3, 4, 6, 7, 8, 9, 10, 11})
        self.assertEqual(html_block_lines, {9, 10, 11})

        non_prose_without_html_indexes: set[int] = set()
        module.hard_wrapped_prose_lines(
            markdown,
            non_prose_line_numbers=non_prose_without_html_indexes,
        )
        self.assertEqual(
            non_prose_without_html_indexes,
            {3, 4, 6, 7, 8, 9, 10, 11},
        )

    def test_inline_html_helpers_cover_default_and_blank_line_paths(self) -> None:
        module = _bundled_module()
        self.assertEqual(module.html_inline_tag_spans_by_line([]), ([], [], []))
        self.assertFalse(module.list_item_starts_with_paragraph("plain prose"))
        masked, delimiter = module.mask_inline_code(
            "text <span title='value'> and `literal`",
            None,
            module.Counter(),
        )
        self.assertIsNone(delimiter)
        self.assertNotIn("`literal`", masked)

        self.assertEqual(
            module.html_inline_tag_spans_by_line(['<span title="open', "", 'close">']),
            ([[], [], []], [False, False, False], [False, False, False]),
        )
        self.assertEqual(
            module.backtick_run_lengths_by_line(["plain text"]), ([()], [0])
        )
        fence = chr(96) * 3
        fenced_lines = [f"{fence}text", "literal ` backticks", fence]
        self.assertEqual(
            module.backtick_run_lengths_by_line(fenced_lines)[0],
            [(), (), ()],
        )
        excluded_line_indexes: set[int] = set()
        self.assertEqual(
            module.backtick_run_lengths_by_line(
                fenced_lines,
                excluded_line_indexes=excluded_line_indexes,
            )[0],
            [(), (), ()],
        )
        self.assertEqual(excluded_line_indexes, {0, 1, 2})

    def test_comment_removal_rebases_surviving_inline_html_spans(self) -> None:
        module = _bundled_module()
        tick = chr(96)
        line = (
            '<span title="value">'
            + tick
            + "code"
            + tick
            + "</span><!--first--><!-- second <i>hidden</i> --> <b>visible</b>"
        )
        tag_spans = module.html_inline_tag_spans(line)

        self.assertEqual(
            module.html_comment_start(line, 0, tag_spans),
            line.index("<!--first-->"),
        )
        self.assertEqual(
            module.first_unescaped_backtick(line, tag_spans),
            line.index(tick),
        )

        visible, comment_open, shifted_spans = module.strip_html_comments(line, False)

        self.assertEqual(
            visible,
            '<span title="value">' + tick + "code" + tick + "</span> <b>visible</b>",
        )
        self.assertFalse(comment_open)
        self.assertEqual(shifted_spans, module.html_inline_tag_spans(visible))

        self.assertFalse(
            module.line_ends_hard_break(
                "text  ",
                [(0, len("text  "))],
                inline_code_open=False,
                html_comment_open=False,
            )
        )
        self.assertEqual(
            module.backtick_run_lengths_by_line(
                ["<span>inline</span> " + tick + "code" + tick]
            ),
            ([(1, 1)], [0]),
        )

    def test_fences_and_raw_html_end_when_their_list_container_ends(self) -> None:
        self.assertEqual(
            _bundled_lines(
                "- item\n  ```text\n  raw code\n\n"
                "First paragraph line\ncontinued paragraph line\n"
            ),
            (6,),
        )
        self.assertEqual(
            _bundled_lines(
                "- item\n  <script>\n  raw content\n\n"
                "First paragraph line\ncontinued paragraph line\n"
            ),
            (6,),
        )

    def test_large_body_preflight_work_is_bounded_by_body_size(self) -> None:
        body = "# Independent heading\n" * 45_000

        result = self.run_preflight(SKILL_PREFLIGHT, body)

        self.assertEqual(result.returncode, 0, result.stderr)

        repeated_setext_headings = "Title\n===\n" * 5_000
        result = self.run_preflight(SKILL_PREFLIGHT, repeated_setext_headings)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_html_comment_content_does_not_change_following_prose_state(self) -> None:
        self.assertEqual(
            _bundled_lines("<!-- hidden\ncontent -->\nFirst line\ncontinued line\n"),
            (4,),
        )
        self.assertEqual(
            _bundled_lines("<!-- ```\nhidden\n``` -->\nFirst\ncontinued\n"),
            (5,),
        )

    def test_html_comment_text_inside_multiline_code_spans_is_inert(self) -> None:
        tick = chr(96)
        comment = chr(60) + chr(33) + chr(45) * 2
        body = (
            tick
            + "Code span begins\n"
            + "and "
            + comment
            + " not a comment\n"
            + "continues"
            + tick
            + " end\n"
            + "First prose line\ncontinued prose\n"
        )

        self.assertEqual(_bundled_lines(body), (4, 5))

        comment_after_close = (
            tick
            + "Code span begins\n"
            + "and "
            + comment
            + " code text\n"
            + "closes"
            + tick
            + " end "
            + comment
            + " actual "
            + tick
            + " note -->\n"
            + "First prose line\ncontinued prose\n"
        )
        self.assertEqual(_bundled_lines(comment_after_close), (4, 5))

    def test_unmatched_delimiters_inside_code_spans_do_not_hide_real_comments(
        self,
    ) -> None:
        tick = chr(96)
        comment = chr(60) + chr(33) + chr(45) * 2
        body = (
            tick
            + "Outer "
            + tick * 2
            + " literal"
            + tick
            + " end\n"
            + "Before "
            + comment
            + " actual comment "
            + tick * 2
            + "\ncomment ends -->\nFirst prose line\ncontinued prose\n"
        )

        self.assertEqual(_bundled_lines(body), (5,))

    def test_handles_multiline_and_unmatched_inline_code(self) -> None:
        self.assertEqual(
            _bundled_lines("Before `code\nspan` after.\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("Before `code\ncontinued line\nclose` after.\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("Before `literal\ncontinued prose.\n"),
            (2,),
        )

    def test_unmatched_inline_code_comment_lookahead_is_cached_and_bounded(
        self,
    ) -> None:
        module = _bundled_module()
        body = "opening `\n" + "continued <!-- inline note -->\n" * 300
        original_lookahead = module.matching_backtick_run_ahead

        with mock.patch.object(
            module,
            "matching_backtick_run_ahead",
            wraps=original_lookahead,
        ) as lookahead:
            wrapped_lines = module.hard_wrapped_prose_lines(body)

        self.assertEqual(wrapped_lines, tuple(range(2, 302)))
        self.assertEqual(lookahead.call_count, 1)

        with mock.patch.object(module, "MAX_INLINE_CODE_LOOKAHEAD_CHARACTERS", 100):
            with self.assertRaisesRegex(ValueError, "lookahead exceeds"):
                module.hard_wrapped_prose_lines(body)
        with mock.patch.object(module, "MAX_INLINE_CODE_LOOKAHEAD_CHARACTERS", 5):
            with self.assertRaisesRegex(ValueError, "lookahead exceeds"):
                module.matching_backtick_run_ahead(
                    ["opening", "> " * 10],
                    0,
                    0,
                    {1},
                    0,
                    set(),
                    work_budget=[5],
                )

    def test_escaped_backticks_do_not_open_inline_code_spans(self) -> None:
        tick = chr(96)
        body = (
            "First prose line with an escaped "
            + chr(92)
            + tick
            + " marker\n"
            + "second prose line with "
            + tick
            + "an inline code span"
            + tick
            + "\n"
            + "continued prose\n"
        )

        self.assertEqual(_bundled_lines(body), (2, 3))

    def test_hard_break_detection_uses_the_unmasked_source_line(self) -> None:
        slash = chr(92)
        tick = chr(96)
        self.assertEqual(_bundled_lines("First line  \ncontinued prose\n"), ())
        self.assertEqual(
            _bundled_lines("First line" + slash + "\ncontinued prose\n"),
            (),
        )
        self.assertEqual(
            _bundled_lines("First line" + slash * 2 + "\ncontinued prose\n"),
            (2,),
        )
        self.assertEqual(
            _bundled_lines("First line  <!-- note -->\ncontinued prose\n"),
            (2,),
        )
        self.assertEqual(
            _bundled_lines(
                "First line " + tick + "code" + tick + "\ncontinued prose\n"
            ),
            (2,),
        )

    def test_only_gfm_cr_and_lf_sequences_start_physical_lines(self) -> None:
        module = _bundled_module()
        self.assertEqual(module.split_gfm_lines(""), [])
        self.assertEqual(
            module.split_gfm_lines("first\u2028second\r\nthird\rfourth\n"),
            ["first\u2028second", "third", "fourth"],
        )
        self.assertEqual(
            module.split_gfm_lines("final line without newline"),
            ["final line without newline"],
        )
        self.assertTrue(module.is_gfm_blank_line(" \t"))
        self.assertFalse(module.is_gfm_blank_line("\u2028"))
        for separator in ("\v", "\f", "\x85", "\u2028", "\u2029"):
            with self.subTest(separator=ord(separator)):
                self.assertEqual(
                    _bundled_lines(f"First line{separator}continued prose\n"),
                    (),
                )

    def test_unicode_whitespace_only_lines_do_not_separate_gfm_paragraphs(self) -> None:
        for separator in ("\v", "\f", "\x85", "\u2028", "\u2029", "\u00a0"):
            body = f"First line\n{separator}\ncontinued prose\n"
            with self.subTest(separator=ord(separator)):
                self.assertEqual(_bundled_lines(body), (2, 3))

    def test_explicit_gfm_hard_breaks_do_not_flag_list_continuations(self) -> None:
        slash = chr(92)
        cases = (
            ("- First item  \n  continued text\n", ()),
            (f"- First item{slash}\n  continued text\n", ()),
            ("1. First item  \n   continued text\n", ()),
            ("> - First item  \n>   continued text\n", ()),
            ("- First item\n  continued text\n", (2,)),
        )

        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(_bundled_lines(body), expected)


def _bundled_module():
    """Load the bundled parser for focused semantic cases."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "markdown_body_preflight", SKILL_PREFLIGHT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundled_lines(body: str) -> tuple[int, ...]:
    """Return wrapped lines from the bundled parser."""
    return _bundled_module().hard_wrapped_prose_lines(body)


if __name__ == "__main__":
    unittest.main()
