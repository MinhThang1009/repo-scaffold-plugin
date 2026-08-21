from __future__ import annotations

import importlib.util
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "validate_scaffold.py"
)
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.validate_scaffold", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load validate_scaffold.py")
validate_scaffold = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validate_scaffold
SPEC.loader.exec_module(validate_scaffold)


def readme(section_count: int = 8) -> str:
    sections = [
        f"## {number}. Section {number}" for number in range(1, section_count + 1)
    ]
    toc = [
        f"- [{number}. Section {number}](#{number}-section-{number})"
        for number in range(1, section_count + 1)
    ]
    return (
        '<div align="center">\n\n'
        "# Example\n\n"
        "A real project tagline.\n\n"
        "[![CI](https://example.com/badge.svg)](https://example.com)\n\n"
        "</div>\n\n"
        "## Contents\n\n" + "\n".join(toc) + "\n\n" + "\n\n".join(sections) + "\n"
    )


class ReadmeContractTests(unittest.TestCase):
    def test_accepts_centered_numbered_readme_with_complete_toc(self) -> None:
        self.assertEqual(validate_scaffold.validate_readme_text(readme()), [])

    def test_rejects_badge_outside_centered_header(self) -> None:
        text = readme().replace(
            "</div>\n\n## Contents",
            "</div>\n\n![Build](https://example.com/build.svg)\n\n## Contents",
        )

        self.assertIn(
            "README.md: header badges/images must be inside the centered div",
            validate_scaffold.validate_readme_text(text),
        )

    def test_rejects_nested_alt_and_html_images_outside_header(self) -> None:
        for image in (
            "![outer [inner]](https://example.com/build.svg)",
            '<img src="https://example.com/build.svg" alt="Build">',
        ):
            with self.subTest(image=image):
                text = readme().replace(
                    "</div>\n\n## Contents",
                    f"</div>\n\n{image}\n\n## Contents",
                )
                self.assertIn(
                    "README.md: header badges/images must be inside the centered div",
                    validate_scaffold.validate_readme_text(text),
                )

    def test_rejects_multiple_centered_headers(self) -> None:
        text = readme() + '\n<div align="center">\n\nDuplicate\n\n</div>\n'

        self.assertIn(
            'README.md: header must use one <div align="center"> block',
            validate_scaffold.validate_readme_text(text),
        )

    def test_requires_toc_for_eight_numbered_sections(self) -> None:
        text = readme().split("## Contents", maxsplit=1)[0]
        text += "\n".join(f"## {number}. Section {number}\n" for number in range(1, 9))

        self.assertIn(
            "README.md: long README must include a manual table of contents",
            validate_scaffold.validate_readme_text(text),
        )

    def test_rejects_nonsequential_sections(self) -> None:
        text = readme(section_count=2).replace("## 2. Section 2", "## 3. Section 2")

        self.assertIn(
            "README.md: H2 sections must be numbered sequentially from 1",
            validate_scaffold.validate_readme_text(text),
        )

    def test_rejects_subsection_that_does_not_match_parent(self) -> None:
        text = readme(section_count=2).replace(
            "## 1. Section 1", "## 1. Section 1\n\n### 2.1 Wrong parent"
        )

        self.assertIn(
            "README.md: H3 subsections must match their parent and be numbered "
            "sequentially",
            validate_scaffold.validate_readme_text(text),
        )

    def test_rejects_missing_or_misplaced_centered_header(self) -> None:
        missing_close = readme().replace("</div>", "")
        self.assertEqual(
            validate_scaffold.validate_readme_text(missing_close),
            ['README.md: header must use one <div align="center"> block'],
        )

        late_close = readme().replace("</div>\n\n## Contents", "## Contents\n\n</div>")
        self.assertIn(
            "README.md: centered header must close before the first H2",
            validate_scaffold.validate_readme_text(late_close),
        )

    def test_rejects_header_without_exactly_one_internal_h1_and_tagline(self) -> None:
        no_h1 = readme().replace("# Example\n\n", "")
        problems = validate_scaffold.validate_readme_text(no_h1)
        self.assertIn(
            "README.md: centered header must contain exactly one H1", problems
        )

        duplicate_h1 = readme().replace("# Example", "# Example\n\n# Duplicate")
        self.assertIn(
            "README.md: centered header must contain exactly one H1",
            validate_scaffold.validate_readme_text(duplicate_h1),
        )

        outside_h1 = readme() + "\n# Outside\n"
        self.assertIn(
            "README.md: H1 must be inside the centered header",
            validate_scaffold.validate_readme_text(outside_h1),
        )

        no_tagline = readme().replace("A real project tagline.\n\n", "")
        self.assertIn(
            "README.md: centered header must contain a nonempty tagline",
            validate_scaffold.validate_readme_text(no_tagline),
        )

    def test_requires_numbered_sections_and_restricts_unnumbered_h2s(self) -> None:
        header_only = readme(section_count=1).split("## Contents", maxsplit=1)[0]
        self.assertIn(
            "README.md: must contain numbered H2 sections",
            validate_scaffold.validate_readme_text(header_only),
        )

        extra_h2 = readme(section_count=2).replace(
            "## 2. Section 2", "## Extra\n\n## 2. Section 2"
        )
        self.assertIn(
            "README.md: only one unnumbered H2 table of contents is allowed",
            validate_scaffold.validate_readme_text(extra_h2),
        )

        late_toc = (
            readme(section_count=2).replace(
                "## Contents\n\n- [1. Section 1](#1-section-1)\n"
                "- [2. Section 2](#2-section-2)\n\n",
                "",
            )
            + "\n## Contents\n"
        )
        self.assertIn(
            "README.md: only one unnumbered H2 table of contents is allowed",
            validate_scaffold.validate_readme_text(late_toc),
        )

    def test_toc_requires_every_section_and_nested_subsection(self) -> None:
        missing_section = readme(section_count=2).replace(
            "- [2. Section 2](#2-section-2)\n", ""
        )
        self.assertIn(
            "README.md: table of contents is missing #2-section-2",
            validate_scaffold.validate_readme_text(missing_section),
        )

        subsection = readme(section_count=2).replace(
            "## 1. Section 1", "## 1. Section 1\n\n### 1.1 Details"
        )
        self.assertIn(
            "README.md: table of contents is missing #11-details",
            validate_scaffold.validate_readme_text(subsection),
        )

        complete = subsection.replace(
            "- [2. Section 2](#2-section-2)",
            "  - [1.1 Details](#11-details)\n- [2. Section 2](#2-section-2)",
        )
        self.assertNotIn(
            "README.md: table of contents is missing #11-details",
            validate_scaffold.validate_readme_text(complete),
        )

    def test_markdown_code_and_anchor_helpers_preserve_visible_structure(self) -> None:
        text = (
            "visible\n```python\n# hidden\n```\n~~~sh\nhidden\n~~~\n`inline` visible\n"
        )
        without_fences = validate_scaffold.without_fenced_code(text)
        self.assertEqual(without_fences.count("\n"), text.count("\n"))
        self.assertNotIn("# hidden", without_fences)
        self.assertIn("`inline` visible", without_fences)

        without_code = validate_scaffold.without_markdown_code(text)
        self.assertNotIn("inline", without_code)
        self.assertTrue(without_code.endswith("         visible\n"))
        self.assertEqual(
            validate_scaffold.github_anchor(" 1. Héllo, World! "), "1-héllo-world"
        )

    def test_markdown_code_helpers_preserve_exact_line_structure(self) -> None:
        fenced = "before\n```md\nhidden\n```\nafter"
        self.assertEqual(
            validate_scaffold.without_fenced_code(fenced),
            "before\n     \n      \n   \nafter",
        )
        multiline_inline = "before `hidden\ncontinued` after\n"
        self.assertEqual(
            validate_scaffold.without_markdown_code(multiline_inline),
            "before        \n           after\n",
        )

        invalid_closer = "````\n[hidden](missing.md)\n```\n[still-hidden](missing.md)\n"
        self.assertNotIn(
            "missing.md", validate_scaffold.without_markdown_code(invalid_closer)
        )

    def test_markdown_code_blocks_do_not_create_link_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "    [indented](missing.md)\n\n"
                "# Heading\n\t[indented-after-heading](missing.md)\n\n"
                "<pre>\n[raw-html](missing.md)\n</pre>\n"
                "<?processing\n[opaque-html](missing.md)\n?>\n",
                encoding="utf-8",
            )

            self.assertEqual(validate_scaffold.validate_markdown_sources(root), [])
            self.assertEqual(
                validate_scaffold._without_inline_code("unclosed `code"),
                "unclosed `code",
            )
            self.assertEqual(
                validate_scaffold._without_inline_code("``a`b``"), "       "
            )
            escaped_code = r"\`[visible](missing.md)\`"
            self.assertEqual(
                validate_scaffold._without_inline_code(escaped_code), escaped_code
            )
            self.assertEqual(
                validate_scaffold._without_inline_code(r"\\`code`"),
                "\\\\      ",
            )
            self.assertEqual(
                validate_scaffold._without_inline_code('<span title="`"> `code`'),
                '<span title="`">       ',
            )
            self.assertEqual(
                validate_scaffold._without_root_indented_code(
                    "- item\n    continuation\nplain\n"
                ),
                "- item\n    continuation\nplain\n",
            )
            visible_html = "<div>\n[visible](docs/example.md)\n"
            self.assertEqual(
                validate_scaffold._without_markdown_block_code(visible_html),
                visible_html,
            )

            (root / "README.md").write_text(
                "> paragraph\r\t[missing](docs/missing.md)\n", encoding="utf-8"
            )
            self.assertEqual(
                validate_scaffold.validate_markdown_sources(root),
                ["README.md: relative link is missing: docs/missing.md"],
            )

    def test_readme_validator_handles_header_and_toc_boundaries_exactly(self) -> None:
        text = (
            readme(section_count=2)
            .replace(
                "## 1. Section 1",
                "## 1. Section 1\n\n### 1.1 First\n\n### 1.2 Second",
            )
            .replace(
                "- [2. Section 2](#2-section-2)",
                "  - [1.1 First](#11-first)\n"
                "  - [1.2 Second](#12-second)\n"
                "- [2. Section 2](#2-section-2)",
            )
        )
        self.assertEqual(validate_scaffold.validate_readme_text(text), [])

        indented_markup = text.replace(
            "A real project tagline.",
            "   <!-- comment -->\n   <span>metadata</span>\n   A real project tagline.",
        )
        self.assertEqual(validate_scaffold.validate_readme_text(indented_markup), [])


class MarkdownSourceContractTests(unittest.TestCase):
    def test_markdown_reader_never_dereferences_symbolic_links(self) -> None:
        path = Path("linked.md")
        with (
            mock.patch.object(Path, "is_symlink", return_value=True),
            mock.patch.object(Path, "read_text") as read_text,
        ):
            text, problem = validate_scaffold.read_markdown(path, label="linked.md")

        self.assertIsNone(text)
        self.assertEqual(
            problem,
            "linked.md: symbolic-link Markdown is not dereferenced or validated",
        )
        read_text.assert_not_called()

        root = mock.MagicMock(spec=Path)
        root.__truediv__.return_value = path
        with mock.patch.object(Path, "is_symlink", return_value=True):
            self.assertEqual(
                validate_scaffold.validate_readme(root),
                ["README.md: symbolic-link Markdown is not dereferenced or validated"],
            )

    def test_reports_unresolved_namespaced_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "{{REPO_SCAFFOLD_PROJECT_NAME}}\n", encoding="utf-8"
            )

            self.assertEqual(
                validate_scaffold.validate_markdown_sources(root),
                ["README.md: contains an unresolved scaffold marker"],
            )

    def test_reports_relative_link_that_escapes_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "[outside](../outside.md)\n", encoding="utf-8"
            )

            self.assertEqual(
                validate_scaffold.validate_markdown_sources(root),
                ["README.md: relative link escapes repository"],
            )

    def test_reports_invalid_relative_link_path_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "[invalid](docs/%00.md)\n", encoding="utf-8"
            )

            self.assertEqual(
                validate_scaffold.validate_markdown_sources(root),
                ["README.md: relative link has an invalid path: docs/%00.md"],
            )

            original_resolve = Path.resolve

            def resolve(path: Path, strict: bool = False) -> Path:
                if path.name == "resolve-error.md":
                    raise OSError("invalid path")
                return original_resolve(path, strict=strict)

            (root / "README.md").write_text(
                "[invalid](docs/resolve-error.md)\n", encoding="utf-8"
            )
            with mock.patch.object(Path, "resolve", autospec=True, side_effect=resolve):
                self.assertEqual(
                    validate_scaffold.validate_markdown_sources(root),
                    [
                        "README.md: relative link has an invalid path: "
                        "docs/resolve-error.md"
                    ],
                )

            original_exists = Path.exists

            def exists(path: Path) -> bool:
                if path.name == "error.md":
                    raise OSError("invalid path")
                return original_exists(path)

            (root / "README.md").write_text(
                "[invalid](docs/error.md)\n", encoding="utf-8"
            )
            with mock.patch.object(Path, "exists", autospec=True, side_effect=exists):
                self.assertEqual(
                    validate_scaffold.validate_markdown_sources(root),
                    ["README.md: relative link has an invalid path: docs/error.md"],
                )

    def test_inventory_excludes_generated_directories_and_symbolic_markdown(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "README.md"
            source.write_text("Source\n", encoding="utf-8")
            ignored = root / "node_modules" / "ignored.md"
            ignored.parent.mkdir()
            ignored.write_text("Ignored\n", encoding="utf-8")
            generated = root / "mutants" / "generated.md"
            generated.parent.mkdir()
            generated.write_text("Generated\n", encoding="utf-8")
            linked = root / "linked.md"
            linked.write_text("Link stand-in\n", encoding="utf-8")
            original_is_symlink = Path.is_symlink

            def is_symlink(path: Path) -> bool:
                return path == linked or original_is_symlink(path)

            with mock.patch.object(validate_scaffold.Path, "is_symlink", is_symlink):
                files = validate_scaffold.markdown_files(root)
                problems = validate_scaffold.validate_markdown_sources(root)

            self.assertEqual(files, [source])
            self.assertEqual(
                problems,
                ["linked.md: symbolic-link Markdown is not dereferenced or validated"],
            )
            self.assertTrue(validate_scaffold.is_project_markdown(source, root))
            self.assertFalse(validate_scaffold.is_project_markdown(ignored, root))
            self.assertFalse(validate_scaffold.is_project_markdown(generated, root))

    def test_inventory_rejects_reparse_directories_without_traversing_them(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            linked = root / "linked"
            linked.mkdir()
            (linked / "outside.md").write_text("Outside\n", encoding="utf-8")

            with mock.patch.object(
                validate_scaffold,
                "is_link_or_reparse",
                side_effect=lambda path: path == linked,
            ):
                files = validate_scaffold.markdown_files(root)
                problems = validate_scaffold.validate_markdown_sources(root)

            self.assertEqual(files, [])
            self.assertEqual(
                problems,
                [
                    "linked: linked or reparse-point path is not dereferenced or validated"
                ],
            )

    def test_link_boundary_helpers_cover_root_files_and_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "outside.md"
            self.assertTrue(validate_scaffold.path_has_link_or_reparse(outside, root))
            self.assertFalse(
                validate_scaffold.path_has_link_or_reparse(
                    root / "missing" / "file.md", root
                )
            )

            non_markdown = root / "notes.txt"
            non_markdown.write_text("notes", encoding="utf-8")
            linked = root / "linked.md"
            linked.write_text("linked", encoding="utf-8")
            with mock.patch.object(
                validate_scaffold,
                "is_link_or_reparse",
                side_effect=lambda path: path == linked,
            ):
                files, rejected = validate_scaffold.markdown_inventory(root)
                text, problem = validate_scaffold.read_markdown(
                    linked, label="linked.md", repository_root=root
                )
            self.assertEqual(files, [])
            self.assertEqual(rejected, [linked])
            self.assertIsNone(text)
            self.assertIn("reparse-point Markdown", problem or "")

            with mock.patch.object(
                validate_scaffold, "is_link_or_reparse", return_value=True
            ):
                text, problem = validate_scaffold.read_markdown(
                    linked, label="linked.md"
                )
            self.assertIsNone(text)
            self.assertIn("reparse-point Markdown", problem or "")

            ordinary = root / "ordinary.md"
            ordinary.write_text("ordinary", encoding="utf-8")
            original_is_file = Path.is_file
            with mock.patch.object(
                Path,
                "is_file",
                autospec=True,
                side_effect=lambda path: (
                    False if path == ordinary else original_is_file(path)
                ),
            ):
                self.assertEqual(validate_scaffold.markdown_files(root), [linked])

            with mock.patch.object(
                validate_scaffold,
                "path_has_link_or_reparse",
                return_value=True,
            ):
                self.assertEqual(
                    validate_scaffold.markdown_inventory(root), ([], [root])
                )
                self.assertEqual(
                    validate_scaffold.validate_markdown_sources(root),
                    [
                        ".: linked or reparse-point path is not dereferenced or "
                        "validated"
                    ],
                )

    def test_reports_encoding_newline_marker_and_missing_link_problems(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "invalid.md").write_bytes(b"\xff")
            (root / "README.md").write_text(
                "{{REPO_SCAFFOLD_NAME}} [missing](docs/missing.md)",
                encoding="utf-8",
            )

            problems = validate_scaffold.validate_markdown_sources(root)

            self.assertTrue(
                any("unreadable UTF-8 Markdown" in item for item in problems)
            )
            self.assertIn("README.md: must end with a newline", problems)
            self.assertIn("README.md: contains an unresolved scaffold marker", problems)
            self.assertIn(
                "README.md: relative link is missing: docs/missing.md", problems
            )

    def test_marker_exclusions_and_nonlocal_links_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            docs = root / "docs"
            assets.mkdir()
            docs.mkdir()
            (docs / "existing file.md").write_text("Existing\n", encoding="utf-8")
            (assets / "template.md").write_text(
                "{{REPO_SCAFFOLD_NAME}} [marker]({{REPO_SCAFFOLD_LINK}})\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text(
                "[anchor](#local) [external](https://example.com) "
                "[protocol](//example.com) "
                '[existing](<docs/existing%20file.md> "title")\n'
                "```md\n[ignored](missing.md)\n```\n",
                encoding="utf-8",
            )

            self.assertEqual(
                validate_scaffold.validate_markdown_sources(
                    root, marker_exclusions=[assets]
                ),
                [],
            )
            self.assertTrue(validate_scaffold.path_is_below(assets, root))
            self.assertFalse(validate_scaffold.path_is_below(root, assets))

    def test_markdown_links_process_every_file_and_destination_kind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            docs.mkdir()
            (docs / "existing.md").write_text("Existing\n", encoding="utf-8")
            (root / "first.md").write_text(
                "[empty]() [anchor](#section) [external](mailto:test@example.com)\n",
                encoding="utf-8",
            )
            (root / "second.md").write_text(
                '[existing](<docs/existing.md> "a multi word title") '
                "[protocol](//example.com/path) [missing](docs/missing.md)\n",
                encoding="utf-8",
            )

            self.assertEqual(
                validate_scaffold.validate_markdown_sources(root),
                ["second.md: relative link is missing: docs/missing.md"],
            )

    def test_balanced_escaped_angle_and_reference_destinations_are_supported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            docs.mkdir()
            (docs / "a(b).md").write_text("Target\n", encoding="utf-8")
            (docs / "a b.md").write_text("Target\n", encoding="utf-8")
            (docs / "a&b.md").write_text("Target\n", encoding="utf-8")
            (root / "README.md").write_text(
                "[balanced](docs/a(b).md)\n"
                r"[escaped](docs/a\(b\).md)"
                "\n"
                "[angle](<docs/a b.md>)\n"
                "[entity](docs/a&amp;b.md)\n"
                "[reference]: <docs/a b.md>\n"
                "[multiline-reference]:\n  <docs/a b.md>\n",
                encoding="utf-8",
            )

            self.assertEqual(validate_scaffold.validate_markdown_sources(root), [])

    def test_nested_labels_footnotes_and_multiline_links_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "[outer [inner]](docs/nested-missing.md)\n"
                '[multiline](\n  docs/multiline-missing.md\n  "title"\n)\n'
                "[^1]: This is footnote text, not a link destination.\n",
                encoding="utf-8",
            )

            self.assertEqual(
                validate_scaffold.validate_markdown_sources(root),
                [
                    "README.md: relative link is missing: docs/nested-missing.md",
                    "README.md: relative link is missing: docs/multiline-missing.md",
                ],
            )

        self.assertEqual(
            validate_scaffold.markdown_link_destinations(r"[x](<docs/a\>b.md>)"),
            ["docs/a%3Eb.md"],
        )
        self.assertEqual(
            validate_scaffold.inline_markdown_link_payloads(
                "[outer [inner](docs/inner.md)](docs/outer.md)"
            ),
            ["docs/inner.md"],
        )

        self.assertEqual(
            validate_scaffold.inline_markdown_link_payloads(
                '[angle](<docs/file.md> "title")'
            ),
            ["docs/file.md"],
        )
        self.assertEqual(
            validate_scaffold.inline_markdown_link_payloads(
                "[bad-angle](<broken>\n[bad-line](broken\n[bad-end](broken"
            ),
            [],
        )

    def test_commonmark_parser_handles_nested_containers_and_references(self) -> None:
        module = validate_scaffold
        cases = {
            "0.\r\t0.\t\t[]()": [],
            "-\r\t<?\n[]()?>": [""],
            "- <x>\r\t[]()": [],
            "- j\n    ```[]()": [],
            "><?\n[]()": [""],
            "[x][ref]\n\n[ref]: docs/reference.md": ["docs/reference.md"],
            "[unused]: docs/unused.md": ["docs/unused.md"],
        }
        for source, expected in cases.items():
            self.assertEqual(
                module.markdown_link_destinations(source), expected, source
            )

    def test_full_validation_reports_invalid_utf8_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_bytes(b"\xff")

            problems = validate_scaffold.validate_scaffold(root)

            self.assertEqual(len(problems), 1)
            self.assertTrue(
                all("README.md: unreadable UTF-8 Markdown" in item for item in problems)
            )


class CodeOfConductContractTests(unittest.TestCase):
    def test_missing_and_custom_policies_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(validate_scaffold.validate_code_of_conduct(root), [])

            (root / "CODE_OF_CONDUCT.md").write_text(
                "# Community Rules\n\nFollow the project rules.\n", encoding="utf-8"
            )
            self.assertEqual(validate_scaffold.validate_code_of_conduct(root), [])

    def test_current_contributor_covenant_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CODE_OF_CONDUCT.md").write_text(
                "# Contributor Covenant\n\n"
                "This Code of Conduct is adapted from the Contributor Covenant, "
                "version 3.0, permanently available at "
                "https://www.contributor-covenant.org/version/3/0/.\n",
                encoding="utf-8",
            )

            self.assertEqual(validate_scaffold.validate_code_of_conduct(root), [])

    def test_obsolete_and_unfinished_contributor_covenant_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CODE_OF_CONDUCT.md").write_text(
                "# Contributor Covenant\n\n"
                "[INSERT CONTACT METHOD]\n\n"
                "[NOTE: customize enforcement]\n\n"
                "This Code of Conduct is adapted from the Contributor Covenant, "
                "version 2.0, available at "
                "https://www.contributor-covenant.org/version/2/0/.\n",
                encoding="utf-8",
            )

            problems = validate_scaffold.validate_code_of_conduct(root)

            self.assertIn(
                "CODE_OF_CONDUCT.md: contains an unresolved reporting placeholder",
                problems,
            )
            self.assertIn(
                "CODE_OF_CONDUCT.md: contains an unresolved Contributor Covenant note",
                problems,
            )
            self.assertIn(
                "CODE_OF_CONDUCT.md: Contributor Covenant must be version 3.0 or newer",
                problems,
            )

    def test_attribution_version_and_url_must_agree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CODE_OF_CONDUCT.md").write_text(
                "# Contributor Covenant\n\n"
                "Contributor Covenant, version 3.1 is available at "
                "https://www.contributor-covenant.org/version/3/0/.\n",
                encoding="utf-8",
            )

            self.assertEqual(
                validate_scaffold.validate_code_of_conduct(root),
                [
                    "CODE_OF_CONDUCT.md: attribution URL must match Contributor "
                    "Covenant version 3.1"
                ],
            )

    def test_unreadable_policy_and_missing_attribution_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "CODE_OF_CONDUCT.md"
            path.write_bytes(b"\xff")
            unreadable = validate_scaffold.validate_code_of_conduct(root)
            self.assertEqual(len(unreadable), 1)
            self.assertTrue(
                unreadable[0].startswith(
                    "CODE_OF_CONDUCT.md: unreadable UTF-8 Markdown"
                )
            )

            path.write_text(
                "# Contributor Covenant\n\nNo attribution version.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                validate_scaffold.validate_code_of_conduct(root),
                [
                    "CODE_OF_CONDUCT.md: Contributor Covenant attribution must "
                    "identify one version"
                ],
            )

    def test_patch_version_uses_three_component_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CODE_OF_CONDUCT.md").write_text(
                "# Contributor Covenant\n\n"
                "Contributor Covenant, version 3.1.2 is available at "
                "https://www.contributor-covenant.org/version/3/1/2/.\n",
                encoding="utf-8",
            )

            self.assertEqual(validate_scaffold.validate_code_of_conduct(root), [])


class TemplateContractTests(unittest.TestCase):
    def test_specialized_template_checks_report_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issue_root = root / ".github" / "ISSUE_TEMPLATE"
            issue_root.mkdir(parents=True)
            (issue_root / "invalid.md").write_bytes(b"\xff")
            pull_template = root / ".github" / "PULL_REQUEST_TEMPLATE.md"
            pull_template.write_bytes(b"\xff")

            issue_problems = validate_scaffold.validate_markdown_issue_templates(root)
            pull_problems = validate_scaffold.validate_pull_request_templates(root)

            self.assertEqual(len(issue_problems), 1)
            self.assertIn("unreadable UTF-8 Markdown", issue_problems[0])
            self.assertEqual(len(pull_problems), 1)
            self.assertIn("unreadable UTF-8 Markdown", pull_problems[0])

    def test_template_asset_checks_report_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_root = Path(directory)
            (template_root / "README-header.md").write_bytes(b"\xff")
            (template_root / "PULL_REQUEST_TEMPLATE.md").write_bytes(b"\xff")

            problems = validate_scaffold.validate_template_assets(template_root)

            self.assertEqual(len(problems), 2)
            self.assertTrue(
                all("unreadable UTF-8 Markdown" in problem for problem in problems)
            )

    def test_duplicate_yaml_key_reports_exact_context_and_key(self) -> None:
        with self.assertRaises(
            validate_scaffold.yaml.constructor.ConstructorError
        ) as raised:
            validate_scaffold.yaml.load(
                "name: First\nname: Second\n",
                Loader=validate_scaffold.UniqueKeyBaseLoader,
            )

        message = str(raised.exception)
        self.assertIn("while constructing a mapping", message)
        self.assertIn("found duplicate key 'name'", message)
        self.assertNotIn("XX", message)

    def test_unhashable_yaml_key_reports_a_controlled_constructor_error(self) -> None:
        with self.assertRaises(
            validate_scaffold.yaml.constructor.ConstructorError
        ) as raised:
            validate_scaffold.yaml.load(
                "? [first, second]\n: value\n",
                Loader=validate_scaffold.UniqueKeyBaseLoader,
            )

        self.assertIn("found an unhashable mapping key", str(raised.exception))

    def test_issue_template_requires_complete_front_matter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            (template_root / "bug.md").write_text(
                "---\nname: Bug\nunsupported: value\n---\nBody\n",
                encoding="utf-8",
            )

            problems = validate_scaffold.validate_markdown_issue_templates(root)

            self.assertIn("front matter must contain", problems[0])

    def test_issue_templates_report_front_matter_value_and_body_errors(self) -> None:
        cases = {
            "missing.md": "No front matter\n",
            "duplicate.md": "---\nname: First\nname: Second\nabout: About\n---\nBody\n",
            "shape.md": "---\n- invalid\n---\nBody\n",
            "value.md": "---\nname: [invalid]\nabout: About\n---\nBody\n",
            "empty-name.md": "---\nname: ' '\nabout: About\n---\nBody\n",
            "empty-body.md": "---\nname: Name\nabout: About\n---\n",
            "valid.md": "---\nname: Name\nabout: About\ntitle: ''\nlabels: ''\nassignees: ''\n---\nBody\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github" / "ISSUE_TEMPLATE"
            template_root.mkdir(parents=True)
            for name, content in cases.items():
                (template_root / name).write_text(content, encoding="utf-8")

            problems = validate_scaffold.validate_markdown_issue_templates(root)

            self.assertTrue(
                any("missing complete YAML front matter" in item for item in problems)
            )
            self.assertTrue(
                any("invalid YAML front matter" in item for item in problems)
            )
            self.assertTrue(
                any("front matter must contain" in item for item in problems)
            )
            self.assertTrue(
                any("front matter values must be strings" in item for item in problems)
            )
            self.assertTrue(
                any("name and about must be nonempty" in item for item in problems)
            )
            self.assertTrue(
                any("template body must be nonempty" in item for item in problems)
            )
            self.assertFalse(any(item.startswith("valid.md") for item in problems))

    def test_pull_request_template_requires_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / ".github"
            template_root.mkdir()
            (template_root / "PULL_REQUEST_TEMPLATE.md").write_text(
                "## Summary\n\nDescribe the change.\n", encoding="utf-8"
            )

            self.assertEqual(
                validate_scaffold.validate_pull_request_templates(root),
                [
                    ".github/PULL_REQUEST_TEMPLATE.md: template must contain a "
                    "checklist item"
                ],
            )

    def test_pull_request_template_discovery_checks_all_supported_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [
                root / "PULL_REQUEST_TEMPLATE.md",
                root / "docs" / "PULL_REQUEST_TEMPLATE.md",
                root / ".github" / "PULL_REQUEST_TEMPLATE.md",
                root / ".github" / "PULL_REQUEST_TEMPLATE" / "focused.md",
            ]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("- [ ] Verify the change\n", encoding="utf-8")

            self.assertEqual(
                validate_scaffold.pull_request_templates(root), sorted(paths)
            )
            self.assertEqual(
                validate_scaffold.validate_pull_request_templates(root), []
            )

            paths[0].write_text("", encoding="utf-8")
            problems = validate_scaffold.validate_pull_request_templates(root)
            self.assertIn(
                "PULL_REQUEST_TEMPLATE.md: template must be nonempty", problems
            )
            self.assertIn(
                "PULL_REQUEST_TEMPLATE.md: template must contain a checklist item",
                problems,
            )

    def test_bundled_markdown_assets_satisfy_the_contract(self) -> None:
        asset_root = PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets"

        self.assertEqual(validate_scaffold.validate_template_assets(asset_root), [])

    def test_template_assets_report_missing_header_and_invalid_pull_template(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "PULL_REQUEST_TEMPLATE.md").write_text(
                "Describe the change.\n", encoding="utf-8"
            )

            problems = validate_scaffold.validate_template_assets(root)

            self.assertTrue(
                any("missing README header asset" in item for item in problems)
            )
            self.assertIn(
                "PULL_REQUEST_TEMPLATE.md asset must contain a checklist item",
                problems,
            )

            (root / "PULL_REQUEST_TEMPLATE.md").unlink()
            self.assertEqual(
                validate_scaffold.validate_template_assets(root),
                [f"{root / 'README-header.md'}: missing README header asset"],
            )

    def test_linked_template_boundaries_are_reported_without_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readme_path = root / "README.md"
            readme_path.write_text(readme(section_count=1), encoding="utf-8")
            issue_root = root / ".github" / "ISSUE_TEMPLATE"
            issue_root.mkdir(parents=True)

            with mock.patch.object(
                validate_scaffold,
                "path_has_link_or_reparse",
                side_effect=lambda path, _root: path in {readme_path, issue_root},
            ):
                self.assertEqual(
                    validate_scaffold.validate_readme(root),
                    [
                        "README.md: linked or reparse-point Markdown is not "
                        "dereferenced or validated"
                    ],
                )
                self.assertEqual(
                    validate_scaffold.validate_markdown_issue_templates(root),
                    [
                        ".github/ISSUE_TEMPLATE: linked or reparse-point path is "
                        "not dereferenced or validated"
                    ],
                )

            multi_template = root / ".github" / "PULL_REQUEST_TEMPLATE"
            with mock.patch.object(
                validate_scaffold,
                "path_has_link_or_reparse",
                side_effect=lambda path, _root: path == multi_template,
            ):
                self.assertEqual(validate_scaffold.pull_request_templates(root), [])

            header = root / "README-header.md"
            pull = root / "PULL_REQUEST_TEMPLATE.md"
            with mock.patch.object(
                validate_scaffold,
                "path_has_link_or_reparse",
                side_effect=lambda path, _root: path in {header, pull},
            ):
                problems = validate_scaffold.validate_template_assets(root)
            self.assertIn(
                "README-header.md asset: linked or reparse-point Markdown is not "
                "dereferenced or validated",
                problems,
            )
            self.assertIn(
                "PULL_REQUEST_TEMPLATE.md asset: linked or reparse-point Markdown "
                "is not dereferenced or validated",
                problems,
            )

    def test_readme_path_and_scaffold_aggregator_cover_optional_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                validate_scaffold.validate_readme(root),
                ["README.md: missing exact root README path"],
            )
            (root / "README.md").write_text(readme(section_count=1), encoding="utf-8")
            template_root = root / "assets"
            template_root.mkdir()

            with (
                mock.patch.object(
                    validate_scaffold,
                    "validate_markdown_sources",
                    return_value=["source"],
                ) as sources,
                mock.patch.object(
                    validate_scaffold, "validate_readme", return_value=["readme"]
                ),
                mock.patch.object(
                    validate_scaffold,
                    "validate_markdown_issue_templates",
                    return_value=["issue"],
                ),
                mock.patch.object(
                    validate_scaffold,
                    "validate_code_of_conduct",
                    return_value=["conduct"],
                ),
                mock.patch.object(
                    validate_scaffold,
                    "validate_pull_request_templates",
                    return_value=["pull"],
                ),
                mock.patch.object(
                    validate_scaffold,
                    "validate_template_assets",
                    return_value=["asset"],
                ) as assets,
            ):
                problems = validate_scaffold.validate_scaffold(
                    root, template_root=template_root
                )

            self.assertEqual(
                problems, ["source", "conduct", "readme", "issue", "pull", "asset"]
            )
            sources.assert_called_once_with(
                root, marker_exclusions=(template_root.parent,)
            )
            assets.assert_called_once_with(template_root)

    def test_main_reports_problems_and_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_root = root / "assets"
            template_root.mkdir()
            args = validate_scaffold.argparse.Namespace(
                repository_root=root,
                template_root=template_root,
            )
            error_output = StringIO()
            with (
                mock.patch.object(validate_scaffold, "parse_args", return_value=args),
                mock.patch.object(
                    validate_scaffold, "validate_scaffold", return_value=["problem"]
                ) as validator,
                redirect_stderr(error_output),
            ):
                self.assertEqual(validate_scaffold.main(), 1)
            self.assertIn("error: problem", error_output.getvalue())
            validator.assert_called_once_with(
                Path(os.path.abspath(root)),
                template_root=Path(os.path.abspath(template_root)),
            )

            args = validate_scaffold.argparse.Namespace(
                repository_root=root,
                template_root=None,
            )
            output = StringIO()
            with (
                mock.patch.object(validate_scaffold, "parse_args", return_value=args),
                mock.patch.object(
                    validate_scaffold, "validate_scaffold", return_value=[]
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(validate_scaffold.main(), 0)
            self.assertEqual(
                output.getvalue(),
                "Rendered Markdown and GitHub templates satisfy the scaffold "
                "contract.\n",
            )

    def test_readme_uses_the_exact_root_path_and_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readme_path = root / "README.md"
            readme_path.write_text(readme(section_count=1), encoding="utf-8")

            self.assertEqual(validate_scaffold.validate_readme(root), [])

    def test_main_requires_existing_repository_root(self) -> None:
        args = validate_scaffold.argparse.Namespace(
            repository_root=Path("missing-repository-root"),
            template_root=None,
        )
        with (
            mock.patch.object(validate_scaffold, "parse_args", return_value=args),
            self.assertRaises(FileNotFoundError),
        ):
            validate_scaffold.main()

    def test_main_rejects_non_directories_and_missing_template_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular_file = root / "file"
            regular_file.write_text("file", encoding="utf-8")

            cases = (
                (regular_file, None, ValueError),
                (root, root / "missing", FileNotFoundError),
                (root, regular_file, ValueError),
            )
            for repository_root, template_root, error_type in cases:
                with self.subTest(
                    repository_root=repository_root, template_root=template_root
                ):
                    args = validate_scaffold.argparse.Namespace(
                        repository_root=repository_root,
                        template_root=template_root,
                    )
                    with (
                        mock.patch.object(
                            validate_scaffold, "parse_args", return_value=args
                        ),
                        self.assertRaises(error_type),
                    ):
                        validate_scaffold.main()

    def test_parse_args_preserves_defaults_options_and_help(self) -> None:
        with mock.patch.object(sys, "argv", [str(SCRIPT_PATH)]):
            defaults = validate_scaffold.parse_args()
        self.assertEqual(defaults.repository_root, Path.cwd())
        self.assertIsNone(defaults.template_root)

        with tempfile.TemporaryDirectory() as directory:
            repository_root = Path(directory) / "repository"
            template_root = Path(directory) / "templates"
            with mock.patch.object(
                sys,
                "argv",
                [
                    str(SCRIPT_PATH),
                    "--repository-root",
                    str(repository_root),
                    "--template-root",
                    str(template_root),
                ],
            ):
                selected = validate_scaffold.parse_args()
        self.assertEqual(selected.repository_root, repository_root)
        self.assertEqual(selected.template_root, template_root)

        output = StringIO()
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--help"]),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as raised,
        ):
            validate_scaffold.parse_args()
        self.assertEqual(raised.exception.code, 0)
        help_text = " ".join(output.getvalue().split())
        self.assertIn(
            "Validate rendered Markdown and GitHub template conventions.", help_text
        )
        self.assertIn(
            "Rendered repository root (default: current directory)", help_text
        )
        self.assertIn(
            "Optional internal repo-scaffold asset root allowed to contain markers",
            help_text,
        )

    def test_parse_args_declares_the_exact_argument_contract(self) -> None:
        parser = mock.Mock()
        expected = object()
        parser.parse_args.return_value = expected
        with mock.patch.object(
            validate_scaffold.argparse,
            "ArgumentParser",
            return_value=parser,
        ) as parser_factory:
            self.assertIs(validate_scaffold.parse_args(), expected)

        parser_factory.assert_called_once_with(description=validate_scaffold.__doc__)
        self.assertEqual(
            parser.add_argument.call_args_list,
            [
                mock.call(
                    "--repository-root",
                    type=Path,
                    default=Path.cwd(),
                    help="Rendered repository root (default: current directory)",
                ),
                mock.call(
                    "--template-root",
                    type=Path,
                    help=(
                        "Optional internal repo-scaffold asset root allowed to "
                        "contain markers"
                    ),
                ),
            ],
        )
        parser.parse_args.assert_called_once_with()

    def test_script_entrypoint_returns_main_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(readme(section_count=1), encoding="utf-8")
            argv = [str(SCRIPT_PATH), "--repository-root", str(root)]
            output = StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                redirect_stdout(output),
                self.assertRaises(SystemExit) as raised,
            ):
                runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

            self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
