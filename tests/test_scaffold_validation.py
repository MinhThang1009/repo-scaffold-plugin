from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "validate_scaffold.py"
)
SPEC = importlib.util.spec_from_file_location("scaffold_validation", SCRIPT_PATH)
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


class MarkdownSourceContractTests(unittest.TestCase):
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


class TemplateContractTests(unittest.TestCase):
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

    def test_bundled_markdown_assets_satisfy_the_contract(self) -> None:
        asset_root = PLUGIN_ROOT / "skills" / "repo-scaffold" / "assets"

        self.assertEqual(validate_scaffold.validate_template_assets(asset_root), [])


if __name__ == "__main__":
    unittest.main()
