#!/usr/bin/env python3
"""Validate rendered Markdown and GitHub template conventions."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml


SKIPPED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
    "venv",
}
SCAFFOLD_MARKER = re.compile(r"\{\{REPO_SCAFFOLD_[A-Z0-9_]+\}\}")
HEADING = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
NUMBERED_SECTION = re.compile(r"^(\d+)\.\s+\S")
NUMBERED_SUBSECTION = re.compile(r"^(\d+)\.(\d+)\s+\S")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")
MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)\n]+\)")
CENTERED_DIV = re.compile(r'<div\s+align=["\']center["\']\s*>', re.IGNORECASE)
LONG_README_SECTION_COUNT = 8


class UniqueKeyBaseLoader(yaml.BaseLoader):
    """YAML loader that preserves text and rejects duplicate keys."""

    def construct_mapping(
        self, node: yaml.nodes.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def is_project_markdown(path: Path, repository_root: Path) -> bool:
    """Return whether a Markdown file belongs to the project source."""
    relative = path.relative_to(repository_root)
    return not any(part in SKIPPED_DIRECTORIES for part in relative.parts)


def markdown_files(repository_root: Path) -> list[Path]:
    """Return project-owned Markdown files under the repository root."""
    return sorted(
        path
        for path in repository_root.rglob("*.md")
        if not path.is_symlink()
        and path.is_file()
        and is_project_markdown(path, repository_root)
    )


def without_fenced_code(text: str) -> str:
    """Blank fenced code while preserving line and character positions."""
    output: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        match = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", line)
        if match and fence is None:
            fence = match.group(1)[0]
            output.append("\n" if line.endswith("\n") else "")
            continue
        if match and fence == match.group(1)[0]:
            fence = None
            output.append("\n" if line.endswith("\n") else "")
            continue
        if fence is None:
            output.append(line)
        else:
            output.append("\n" if line.endswith("\n") else "")
    return "".join(output)


def without_markdown_code(text: str) -> str:
    """Blank fenced and inline code before structural Markdown parsing."""
    visible = without_fenced_code(text)
    return re.sub(
        r"(`+)(.+?)\1",
        lambda match: " " * len(match.group(0)),
        visible,
        flags=re.DOTALL,
    )


def github_anchor(heading: str) -> str:
    """Return the GitHub-style anchor used by numbered project headings."""
    normalized = heading.strip().lower()
    normalized = re.sub(r"[^\w\s-]", "", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", "-", normalized)


def path_is_below(path: Path, parent: Path) -> bool:
    """Return whether path is located at or below parent."""
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def validate_markdown_sources(
    repository_root: Path, *, marker_exclusions: Iterable[Path] = ()
) -> list[str]:
    """Validate encoding, final newlines, markers, and relative links."""
    exclusions = tuple(path.resolve() for path in marker_exclusions)
    problems: list[str] = []
    resolved_root = repository_root.resolve()
    for path in sorted(repository_root.rglob("*.md")):
        if path.is_symlink() and is_project_markdown(path, repository_root):
            relative = path.relative_to(repository_root).as_posix()
            problems.append(
                f"{relative}: symbolic-link Markdown is not dereferenced or validated"
            )
    for path in markdown_files(repository_root):
        relative = path.relative_to(repository_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            problems.append(f"{relative}: unreadable UTF-8 Markdown: {error}")
            continue
        if not text.endswith("\n"):
            problems.append(f"{relative}: must end with a newline")
        excluded = any(path_is_below(path, prefix) for prefix in exclusions)
        if not excluded and SCAFFOLD_MARKER.search(text):
            problems.append(f"{relative}: contains an unresolved scaffold marker")

        for destination in MARKDOWN_LINK.findall(without_markdown_code(text)):
            destination = destination.strip().split(maxsplit=1)[0].strip("<>")
            if (
                not destination
                or destination.startswith("#")
                or SCAFFOLD_MARKER.search(destination)
                or urlsplit(destination).scheme
                or destination.startswith("//")
            ):
                continue
            target = (path.parent / unquote(urlsplit(destination).path)).resolve()
            try:
                target.relative_to(resolved_root)
            except ValueError:
                problems.append(f"{relative}: relative link escapes repository")
                continue
            if not target.exists():
                problems.append(f"{relative}: relative link is missing: {destination}")
    return problems


def validate_readme_text(text: str, *, label: str = "README.md") -> list[str]:
    """Validate the centered header and numbered README outline contract."""
    visible = without_fenced_code(text)
    problems: list[str] = []

    openings = list(CENTERED_DIV.finditer(visible))
    opening = openings[0] if len(openings) == 1 else None
    closing_position = visible.lower().find("</div>", opening.end() if opening else 0)
    first_h2 = re.search(r"(?m)^##[ \t]+", visible)
    if opening is None or closing_position < 0:
        problems.append(f'{label}: header must use one <div align="center"> block')
        return problems
    if first_h2 and closing_position > first_h2.start():
        problems.append(f"{label}: centered header must close before the first H2")

    header = visible[opening.end() : closing_position]
    header_h1 = re.findall(r"(?m)^#[ \t]+\S.*$", header)
    if len(header_h1) != 1:
        problems.append(f"{label}: centered header must contain exactly one H1")
    if re.search(
        r"(?m)^#[ \t]+", visible[: opening.start()] + visible[closing_position:]
    ):
        problems.append(f"{label}: H1 must be inside the centered header")
    tagline_lines = [
        line.strip()
        for line in header.splitlines()
        if line.strip()
        and not line.lstrip().startswith(("#", "![", "[![", "<!--", "-->", "<"))
    ]
    if not tagline_lines:
        problems.append(f"{label}: centered header must contain a nonempty tagline")

    header_end = closing_position + len("</div>")
    pre_sections = visible[: first_h2.start()] if first_h2 else visible
    outside_header = pre_sections[: opening.start()] + pre_sections[header_end:]
    if MARKDOWN_IMAGE.search(outside_header):
        problems.append(
            f"{label}: header badges/images must be inside the centered div"
        )

    headings = [
        (match.group(1), match.group(2).strip(), match.start())
        for match in HEADING.finditer(visible)
    ]
    h2s = [(title, position) for marks, title, position in headings if marks == "##"]
    numbered_h2s: list[tuple[int, str, int]] = []
    unnumbered_h2s: list[tuple[str, int]] = []
    for title, position in h2s:
        match = NUMBERED_SECTION.match(title)
        if match:
            numbered_h2s.append((int(match.group(1)), title, position))
        else:
            unnumbered_h2s.append((title, position))
    expected_sections = list(range(1, len(numbered_h2s) + 1))
    actual_sections = [number for number, _title, _position in numbered_h2s]
    if actual_sections != expected_sections:
        problems.append(f"{label}: H2 sections must be numbered sequentially from 1")
    if not numbered_h2s:
        problems.append(f"{label}: must contain numbered H2 sections")

    toc: tuple[str, int] | None = None
    if unnumbered_h2s:
        first_numbered_position = numbered_h2s[0][2] if numbered_h2s else len(visible)
        candidates = [
            item for item in unnumbered_h2s if item[1] < first_numbered_position
        ]
        if len(unnumbered_h2s) != 1 or len(candidates) != 1:
            problems.append(
                f"{label}: only one unnumbered H2 table of contents is allowed"
            )
        else:
            toc = candidates[0]
    if len(numbered_h2s) >= LONG_README_SECTION_COUNT and toc is None:
        problems.append(f"{label}: long README must include a manual table of contents")

    current_section: int | None = None
    child_counts: dict[int, int] = {}
    numbered_headings: list[tuple[int, str]] = []
    for marks, title, _position in headings:
        if marks == "##":
            section_match = NUMBERED_SECTION.match(title)
            current_section = int(section_match.group(1)) if section_match else None
            if current_section is not None:
                numbered_headings.append((2, title))
        elif marks == "###" and current_section is not None:
            subsection_match = NUMBERED_SUBSECTION.match(title)
            expected_child = child_counts.get(current_section, 0) + 1
            if (
                subsection_match is None
                or int(subsection_match.group(1)) != current_section
                or int(subsection_match.group(2)) != expected_child
            ):
                problems.append(
                    f"{label}: H3 subsections must match their parent and be "
                    "numbered sequentially"
                )
            else:
                child_counts[current_section] = expected_child
                numbered_headings.append((3, title))

    if toc is not None:
        toc_start = toc[1]
        toc_end = numbered_h2s[0][2] if numbered_h2s else len(visible)
        toc_body = visible[toc_start:toc_end]
        for level, title in numbered_headings:
            anchor = github_anchor(title)
            indentation = r"[ \t]{2,}" if level == 3 else r""
            entry = re.compile(
                rf"(?m)^{indentation}[-*+]\s+\[[^\]]+\]\(#{re.escape(anchor)}\)\s*$"
            )
            if not entry.search(toc_body):
                problems.append(f"{label}: table of contents is missing #{anchor}")
    return problems


def validate_readme(repository_root: Path) -> list[str]:
    """Validate the exact root README path."""
    path = repository_root / "README.md"
    if not path.is_file():
        return ["README.md: missing exact root README path"]
    return validate_readme_text(path.read_text(encoding="utf-8"))


def validate_markdown_issue_templates(
    repository_root: Path, *, template_directory: Path | None = None
) -> list[str]:
    """Validate legacy Markdown issue-template front matter and body."""
    template_root = template_directory or (
        repository_root / ".github" / "ISSUE_TEMPLATE"
    )
    problems: list[str] = []
    for path in sorted(template_root.glob("*.md")):
        relative = path.relative_to(repository_root).as_posix()
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", text, re.DOTALL)
        if match is None:
            problems.append(f"{relative}: missing complete YAML front matter")
            continue
        try:
            metadata = yaml.load(match.group(1), Loader=UniqueKeyBaseLoader)
        except yaml.YAMLError as error:
            problems.append(f"{relative}: invalid YAML front matter: {error}")
            continue
        required = {"name", "about"}
        supported = required | {"title", "labels", "assignees"}
        if (
            not isinstance(metadata, dict)
            or not required.issubset(metadata)
            or not set(metadata).issubset(supported)
        ):
            problems.append(
                f"{relative}: front matter must contain {sorted(required)} and only "
                f"supported keys {sorted(supported)}"
            )
        elif not all(isinstance(value, str) for value in metadata.values()):
            problems.append(f"{relative}: front matter values must be strings")
        elif not metadata["name"].strip() or not metadata["about"].strip():
            problems.append(f"{relative}: name and about must be nonempty")
        if not match.group(2).strip():
            problems.append(f"{relative}: template body must be nonempty")
    return problems


def pull_request_templates(repository_root: Path) -> list[Path]:
    """Return supported single and multi-template Markdown paths."""
    candidates = {
        repository_root / "PULL_REQUEST_TEMPLATE.md",
        repository_root / "docs" / "PULL_REQUEST_TEMPLATE.md",
        repository_root / ".github" / "PULL_REQUEST_TEMPLATE.md",
    }
    candidates.update(
        (repository_root / ".github" / "PULL_REQUEST_TEMPLATE").glob("*.md")
    )
    return sorted(path for path in candidates if path.is_file())


def validate_pull_request_templates(repository_root: Path) -> list[str]:
    """Require actionable content in every pull-request template."""
    problems: list[str] = []
    for path in pull_request_templates(repository_root):
        relative = path.relative_to(repository_root).as_posix()
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            problems.append(f"{relative}: template must be nonempty")
        if not re.search(r"(?m)^\s*[-*+]\s+\[ \]\s+\S", text):
            problems.append(f"{relative}: template must contain a checklist item")
    return problems


def validate_template_assets(template_root: Path) -> list[str]:
    """Validate Markdown-specific contracts in the plugin's source assets."""
    problems: list[str] = []
    header_path = template_root / "README-header.md"
    if not header_path.is_file():
        problems.append(f"{header_path}: missing README header asset")
    else:
        header = header_path.read_text(encoding="utf-8")
        synthetic = header + "\n## 1. Overview\n"
        problems.extend(validate_readme_text(synthetic, label="README-header.md asset"))
    problems.extend(
        validate_markdown_issue_templates(
            template_root, template_directory=template_root / "ISSUE_TEMPLATE"
        )
    )
    pull_template = template_root / "PULL_REQUEST_TEMPLATE.md"
    if pull_template.is_file():
        text = pull_template.read_text(encoding="utf-8")
        if not re.search(r"(?m)^\s*[-*+]\s+\[ \]\s+\S", text):
            problems.append(
                "PULL_REQUEST_TEMPLATE.md asset must contain a checklist item"
            )
    return problems


def validate_scaffold(
    repository_root: Path, *, template_root: Path | None = None
) -> list[str]:
    """Run every deterministic rendered-document validation."""
    exclusions = (template_root.parent,) if template_root else ()
    problems = validate_markdown_sources(repository_root, marker_exclusions=exclusions)
    problems.extend(validate_readme(repository_root))
    problems.extend(validate_markdown_issue_templates(repository_root))
    problems.extend(validate_pull_request_templates(repository_root))
    if template_root is not None:
        problems.extend(validate_template_assets(template_root))
    return problems


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Rendered repository root (default: current directory)",
    )
    parser.add_argument(
        "--template-root",
        type=Path,
        help="Optional internal repo-scaffold asset root allowed to contain markers",
    )
    return parser.parse_args()


def main() -> int:
    """Validate the selected repository and report every problem."""
    args = parse_args()
    root = args.repository_root.resolve(strict=True)
    template_root = (
        args.template_root.resolve(strict=True) if args.template_root else None
    )
    problems = validate_scaffold(root, template_root=template_root)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print("Rendered Markdown and GitHub templates satisfy the scaffold contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
