#!/usr/bin/env python3
"""Validate repository metadata, links, templates, and release contents."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml


CACHE_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "venv",
    ".venv",
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")
MARKDOWN_REFERENCE = re.compile(r"(?m)^\s{0,3}\[[^\]]+\]:\s*(\S+)")
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
TEMPLATE_TOKEN = re.compile(r"(?:\{\{|\$\{\{)")
ISSUE_FORM_ID = re.compile(r"^[0-9A-Za-z_-]+$")
ISSUE_FORM_INPUT_TYPES = {
    "checkboxes",
    "dropdown",
    "input",
    "markdown",
    "textarea",
    "upload",
}


class UniqueKeyBaseLoader(yaml.BaseLoader):
    """YAML loader that preserves scalar text and rejects duplicate keys."""

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


def resolve_path_executable(name: str, *, forbidden_root: Path) -> str | None:
    """Resolve a tool only from absolute PATH entries outside the repository."""
    forbidden = forbidden_root.resolve(strict=True)
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_directory:
            continue
        directory = Path(raw_directory.strip('"'))
        if not directory.is_absolute():
            continue
        candidate = shutil.which(name, path=str(directory))
        if candidate is None:
            continue
        try:
            resolved = Path(candidate).resolve(strict=True)
            resolved.relative_to(forbidden)
        except ValueError:
            return str(resolved)
        except (OSError, RuntimeError):
            continue
    return None


def is_project_path(path: Path, repository_root: Path) -> bool:
    """Return whether a path belongs to source rather than a cache or artifact."""
    relative = path.relative_to(repository_root)
    return not any(part in CACHE_DIRECTORIES for part in relative.parts)


def project_files(repository_root: Path, patterns: Iterable[str]) -> list[Path]:
    """Return matching first-party files, excluding known local artifacts."""
    files: set[Path] = set()
    for pattern in patterns:
        files.update(
            path
            for path in repository_root.rglob(pattern)
            if path.is_file() and is_project_path(path, repository_root)
        )
    return sorted(files)


def load_yaml(path: Path) -> Any:
    """Load a YAML document without YAML 1.1 scalar coercion."""
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyBaseLoader)


def reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate member names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    """Load a JSON document and reject duplicate member names."""
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_json_pairs,
    )


def nonempty_string(value: Any) -> bool:
    """Return whether a value is a nonempty string."""
    return isinstance(value, str) and bool(value.strip())


def validate_serialized_files(repository_root: Path) -> list[str]:
    """Validate every first-party JSON and YAML document."""
    problems: list[str] = []
    for path in project_files(repository_root, ("*.json",)):
        try:
            load_json(path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            problems.append(
                f"{path.relative_to(repository_root)}: invalid JSON: {error}"
            )
    for path in project_files(repository_root, ("*.yml", "*.yaml")):
        try:
            load_yaml(path)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            problems.append(
                f"{path.relative_to(repository_root)}: invalid YAML: {error}"
            )
    return problems


def validate_plugin_manifest(repository_root: Path) -> list[str]:
    """Validate the installed plugin manifest and its referenced skill tree."""
    path = repository_root / ".codex-plugin" / "plugin.json"
    problems: list[str] = []
    try:
        document = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        return [f".codex-plugin/plugin.json: invalid JSON: {error}"]
    if not isinstance(document, dict):
        return [".codex-plugin/plugin.json: root must be an object"]

    for field in ("name", "version", "description", "license", "skills"):
        if not nonempty_string(document.get(field)):
            problems.append(f".codex-plugin/plugin.json: {field} must be nonempty")
    if document.get("name") != "repo-scaffold":
        problems.append(".codex-plugin/plugin.json: name must be repo-scaffold")
    if document.get("license") != "MIT":
        problems.append(".codex-plugin/plugin.json: license must match repository MIT")
    version = document.get("version")
    if isinstance(version, str) and not SEMVER.fullmatch(version):
        problems.append(".codex-plugin/plugin.json: version must be valid SemVer")

    skills_value = document.get("skills")
    if isinstance(skills_value, str) and skills_value.strip():
        skills_path = (repository_root / skills_value).resolve()
        try:
            skills_path.relative_to(repository_root.resolve())
        except ValueError:
            problems.append(
                ".codex-plugin/plugin.json: skills must stay inside the repository"
            )
        else:
            if not skills_path.is_dir():
                problems.append(
                    ".codex-plugin/plugin.json: skills must reference a directory"
                )
            elif not any(skills_path.rglob("SKILL.md")):
                problems.append(
                    ".codex-plugin/plugin.json: skills contains no SKILL.md"
                )
    return problems


def validate_release_please(repository_root: Path) -> list[str]:
    """Validate the repository's single automated release mode and version state."""
    workflow_root = repository_root / ".github" / "workflows"
    workflow_path = workflow_root / "release-please.yml"
    manual_dispatcher = workflow_root / "release-tag.yml"
    config_path = repository_root / "release-please-config.json"
    versions_path = repository_root / ".release-please-manifest.json"
    plugin_path = repository_root / ".codex-plugin" / "plugin.json"
    version_path = repository_root / "version.txt"
    problems: list[str] = []

    if not workflow_path.is_file():
        problems.append(".github/workflows/release-please.yml: missing")
    if manual_dispatcher.exists():
        problems.append(
            ".github/workflows/release-tag.yml: must not coexist with Release Please"
        )

    try:
        config = load_json(config_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        return [f"release-please-config.json: invalid JSON: {error}", *problems]
    if not isinstance(config, dict):
        return ["release-please-config.json: root must be an object", *problems]
    if config.get("release-type") != "simple":
        problems.append("release-please-config.json: release-type must be simple")
    if config.get("draft") is not True:
        problems.append("release-please-config.json: draft must be true")
    if config.get("force-tag-creation") is not True:
        problems.append("release-please-config.json: force-tag-creation must be true")
    packages = config.get("packages")
    root_package = packages.get(".") if isinstance(packages, dict) else None
    if not isinstance(root_package, dict):
        problems.append("release-please-config.json: packages must define root package")
    else:
        required_extra_file = {
            "type": "json",
            "path": ".codex-plugin/plugin.json",
            "jsonpath": "$.version",
        }
        extra_files = root_package.get("extra-files")
        if not isinstance(extra_files, list) or required_extra_file not in extra_files:
            problems.append(
                "release-please-config.json: root package must update plugin version"
            )

    versions: dict[str, Any] = {}
    try:
        manifest = load_json(versions_path)
        if isinstance(manifest, dict) and set(manifest) == {"."}:
            versions[".release-please-manifest.json"] = manifest["."]
        else:
            problems.append(
                ".release-please-manifest.json: must contain only the root package"
            )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        problems.append(f".release-please-manifest.json: invalid JSON: {error}")
    try:
        plugin = load_json(plugin_path)
        if isinstance(plugin, dict):
            versions[".codex-plugin/plugin.json"] = plugin.get("version")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        problems.append(f".codex-plugin/plugin.json: invalid JSON: {error}")
    try:
        versions["version.txt"] = version_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        problems.append(f"version.txt: unreadable: {error}")

    invalid_versions = [
        version_source
        for version_source, version in versions.items()
        if not isinstance(version, str) or not SEMVER.fullmatch(version)
    ]
    for version_source in invalid_versions:
        problems.append(f"{version_source}: release version must be valid SemVer")
    for version_source, version in versions.items():
        if version_source in invalid_versions:
            continue
        build_metadata = version.partition("+")[2]
        if build_metadata.startswith("codex."):
            problems.append(
                f"{version_source}: public release version must not use a local "
                "Codex cachebuster"
            )
    valid_versions = {
        version
        for version_source, version in versions.items()
        if version_source not in invalid_versions
    }
    if len(versions) == 3 and len(valid_versions) > 1:
        problems.append("release version files must contain the same version")

    for workflow_file in sorted(workflow_root.glob("*.yml")):
        try:
            document = load_yaml(workflow_file)
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if not isinstance(document, dict):
            continue
        triggers = document.get("on")
        if not isinstance(triggers, dict):
            continue
        push = triggers.get("push")
        if isinstance(push, dict) and "tags" in push:
            problems.append(
                f"{workflow_file.relative_to(repository_root).as_posix()}: tag push "
                "trigger conflicts "
                "with Release Please"
            )
    return problems


def read_front_matter(path: Path) -> tuple[Any, str]:
    """Return parsed YAML front matter and the remaining Markdown body."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing opening YAML front matter delimiter")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("missing closing YAML front matter delimiter") from error
    front_matter = "\n".join(lines[1:closing])
    return yaml.load(front_matter, Loader=UniqueKeyBaseLoader), "\n".join(
        lines[closing + 1 :]
    )


def validate_issue_form_body(relative: Path, form_body: list[Any]) -> list[str]:
    """Validate current GitHub issue-form body types and core constraints."""
    problems: list[str] = []
    seen_ids: set[str] = set()
    has_input = False
    for index, item in enumerate(form_body):
        prefix = f"{relative}: body[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{prefix} must be a mapping")
            continue
        item_type = item.get("type")
        if item_type not in ISSUE_FORM_INPUT_TYPES:
            problems.append(f"{prefix} has invalid type")
            continue
        if item_type != "markdown":
            has_input = True

        item_id = item.get("id")
        if item_id is not None:
            if not isinstance(item_id, str) or not ISSUE_FORM_ID.fullmatch(item_id):
                problems.append(
                    f"{prefix}.id may contain only letters, numbers, -, and _"
                )
            elif item_id in seen_ids:
                problems.append(f"{prefix}.id must be unique")
            else:
                seen_ids.add(item_id)

        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            problems.append(f"{prefix}.attributes must be a mapping")
            continue
        required_attribute = "value" if item_type == "markdown" else "label"
        if not nonempty_string(attributes.get(required_attribute)):
            problems.append(
                f"{prefix}.attributes.{required_attribute} must be nonempty"
            )

        if item_type == "dropdown":
            options = attributes.get("options")
            if (
                not isinstance(options, list)
                or not options
                or any(not nonempty_string(option) for option in options)
            ):
                problems.append(
                    f"{prefix}.attributes.options must be a nonempty string list"
                )
            elif len(set(options)) != len(options):
                problems.append(f"{prefix}.attributes.options must be unique")
        elif item_type == "checkboxes":
            options = attributes.get("options")
            if not isinstance(options, list) or not options:
                problems.append(f"{prefix}.attributes.options must be a nonempty list")
            else:
                labels: list[str] = []
                for option_index, option in enumerate(options):
                    if not isinstance(option, dict) or not nonempty_string(
                        option.get("label")
                    ):
                        problems.append(
                            f"{prefix}.attributes.options[{option_index}].label "
                            "must be nonempty"
                        )
                        continue
                    labels.append(option["label"])
                    required = option.get("required")
                    if required is not None and required not in {"true", "false"}:
                        problems.append(
                            f"{prefix}.attributes.options[{option_index}].required "
                            "must be a boolean"
                        )
                if len(set(labels)) != len(labels):
                    problems.append(
                        f"{prefix}.attributes.options labels must be unique"
                    )

        validations = item.get("validations")
        if validations is not None:
            if not isinstance(validations, dict):
                problems.append(f"{prefix}.validations must be a mapping")
            else:
                required = validations.get("required")
                if required is not None and required not in {"true", "false"}:
                    problems.append(f"{prefix}.validations.required must be a boolean")
                accept = validations.get("accept")
                if item_type == "upload" and accept is not None:
                    if not nonempty_string(accept):
                        problems.append(f"{prefix}.validations.accept must be nonempty")

    if not has_input:
        problems.append(f"{relative}: body must contain a non-markdown input")
    return problems


def validate_issue_templates(repository_root: Path) -> list[str]:
    """Validate issue template front matter, forms, and chooser configuration."""
    problems: list[str] = []
    template_roots = (
        repository_root / ".github" / "ISSUE_TEMPLATE",
        repository_root / "skills" / "repo-scaffold" / "assets" / "ISSUE_TEMPLATE",
    )
    for template_root in template_roots:
        for path in sorted(template_root.glob("*.md")):
            relative = path.relative_to(repository_root)
            try:
                front_matter, template_body = read_front_matter(path)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
                problems.append(f"{relative}: invalid front matter: {error}")
                continue
            if not isinstance(front_matter, dict):
                problems.append(f"{relative}: front matter must be a mapping")
                continue
            for field in ("name", "about"):
                if not nonempty_string(front_matter.get(field)):
                    problems.append(f"{relative}: {field} must be nonempty")
            name = front_matter.get("name")
            if isinstance(name, str) and len(name.strip()) <= 3:
                problems.append(f"{relative}: name must be more than 3 characters")
            if not template_body.strip():
                problems.append(f"{relative}: template body must be nonempty")

        for path in sorted(template_root.glob("*.yaml")):
            relative = path.relative_to(repository_root)
            problems.append(f"{relative}: issue forms must use the .yml extension")

        for path in sorted(template_root.glob("*.yml")):
            if path.name == "config.yml":
                continue
            relative = path.relative_to(repository_root)
            try:
                document = load_yaml(path)
            except (OSError, UnicodeError, yaml.YAMLError) as error:
                problems.append(f"{relative}: invalid issue form YAML: {error}")
                continue
            if not isinstance(document, dict):
                problems.append(f"{relative}: issue form root must be a mapping")
                continue
            for field in ("name", "description"):
                if not nonempty_string(document.get(field)):
                    problems.append(f"{relative}: {field} must be nonempty")
            name = document.get("name")
            if isinstance(name, str) and len(name.strip()) <= 3:
                problems.append(f"{relative}: name must be more than 3 characters")
            form_body = document.get("body")
            if not isinstance(form_body, list) or not form_body:
                problems.append(f"{relative}: body must be a nonempty list")
                continue
            problems.extend(validate_issue_form_body(relative, form_body))

        config_path = template_root / "config.yml"
        if not config_path.is_file():
            continue
        relative = config_path.relative_to(repository_root)
        try:
            document = load_yaml(config_path)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: invalid chooser YAML: {error}")
            continue
        if not isinstance(document, dict):
            problems.append(f"{relative}: root must be a mapping")
            continue
        if document.get("blank_issues_enabled") not in {"true", "false"}:
            problems.append(f"{relative}: blank_issues_enabled must be a boolean")
        contact_links = document.get("contact_links", [])
        if not isinstance(contact_links, list):
            problems.append(f"{relative}: contact_links must be a list")
            continue
        for index, contact in enumerate(contact_links):
            if not isinstance(contact, dict):
                problems.append(f"{relative}: contact_links[{index}] must be a mapping")
                continue
            for field in ("name", "url", "about"):
                if not nonempty_string(contact.get(field)):
                    problems.append(
                        f"{relative}: contact_links[{index}].{field} must be nonempty"
                    )
            url = contact.get("url")
            if (
                isinstance(url, str)
                and not TEMPLATE_TOKEN.search(url)
                and urlsplit(url).scheme != "https"
            ):
                problems.append(
                    f"{relative}: contact_links[{index}].url must use HTTPS"
                )
    return problems


def validate_dependabot(repository_root: Path) -> list[str]:
    """Validate installed and templated Dependabot configuration."""
    problems: list[str] = []
    paths = (
        repository_root / ".github" / "dependabot.yml",
        repository_root / "skills" / "repo-scaffold" / "assets" / "dependabot.yml",
    )
    allowed_intervals = {
        "daily",
        "weekly",
        "monthly",
        "quarterly",
        "semiannually",
        "yearly",
    }
    for path in paths:
        relative = path.relative_to(repository_root)
        try:
            document = load_yaml(path)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: invalid Dependabot YAML: {error}")
            continue
        if not isinstance(document, dict):
            problems.append(f"{relative}: root must be a mapping")
            continue
        if document.get("version") != "2":
            problems.append(f"{relative}: version must be 2")
        updates = document.get("updates")
        if not isinstance(updates, list) or not updates:
            problems.append(f"{relative}: updates must be a nonempty list")
            continue
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                problems.append(f"{relative}: updates[{index}] must be a mapping")
                continue
            for field in ("package-ecosystem", "directory"):
                if not nonempty_string(update.get(field)):
                    problems.append(f"{relative}: updates[{index}].{field} is required")
            schedule = update.get("schedule")
            if not isinstance(schedule, dict):
                problems.append(f"{relative}: updates[{index}].schedule is required")
            elif schedule.get("interval") not in allowed_intervals:
                problems.append(
                    f"{relative}: updates[{index}].schedule.interval is invalid"
                )
    return problems


def without_fenced_code(text: str) -> str:
    """Remove fenced and inline code before extracting Markdown links."""
    kept: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is None:
            kept.append(re.sub(r"`[^`\n]*`", "", line))
    return "\n".join(kept)


def link_destination(raw: str) -> str:
    """Extract a destination from a Markdown inline-link payload."""
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    return value.split(maxsplit=1)[0]


def validate_markdown_links(repository_root: Path) -> list[str]:
    """Validate that first-party relative Markdown links stay inside and exist."""
    problems: list[str] = []
    for path in project_files(repository_root, ("*.md",)):
        relative = path.relative_to(repository_root)
        try:
            text = without_fenced_code(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            problems.append(f"{relative}: could not read Markdown: {error}")
            continue
        destinations = [
            *(link_destination(match) for match in MARKDOWN_LINK.findall(text)),
            *(link_destination(match) for match in MARKDOWN_REFERENCE.findall(text)),
        ]
        for destination in destinations:
            if (
                not destination
                or destination.startswith(("#", "/", "//"))
                or TEMPLATE_TOKEN.search(destination)
                or urlsplit(destination).scheme
            ):
                continue
            parsed = urlsplit(destination)
            decoded_path = unquote(parsed.path)
            target = (path.parent / decoded_path).resolve()
            try:
                target.relative_to(repository_root.resolve())
            except ValueError:
                problems.append(f"{relative}: link escapes repository: {destination}")
                continue
            if not target.exists():
                problems.append(f"{relative}: relative link is missing: {destination}")
    return problems


def validate_release_archive(repository_root: Path) -> list[str]:
    """Build and inspect the exact archive shape used by the release workflow."""
    git = resolve_path_executable("git", forbidden_root=repository_root)
    if git is None:
        return ["release archive: git is unavailable outside the repository"]
    with tempfile.TemporaryDirectory(prefix="repo-scaffold-archive-") as directory:
        archive = Path(directory) / "plugin.zip"
        command = [
            git,
            "archive",
            "--format=zip",
            "--prefix=repo-scaffold/",
            "--output",
            str(archive),
            "HEAD",
            "--",
            ".codex-plugin",
            "skills",
            "README.md",
            "LICENSE",
        ]
        try:
            result = subprocess.run(  # noqa: S603 - executable is resolved safely
                command,
                cwd=repository_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return ["release archive: git archive timed out"]
        if result.returncode != 0:
            detail = result.stderr.strip() or "git archive failed"
            return [f"release archive: {detail}"]

        problems: list[str] = []
        expected = {
            "repo-scaffold/.codex-plugin/plugin.json",
            "repo-scaffold/README.md",
            "repo-scaffold/LICENSE",
            "repo-scaffold/skills/repo-scaffold/SKILL.md",
        }
        try:
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                for missing in sorted(expected - names):
                    problems.append(f"release archive: missing {missing}")
                for item in bundle.infolist():
                    path = PurePosixPath(item.filename)
                    if (
                        not path.parts
                        or path.parts[0] != "repo-scaffold"
                        or path.is_absolute()
                        or ".." in path.parts
                    ):
                        problems.append(
                            f"release archive: unsafe member {item.filename!r}"
                        )
                    mode = item.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        problems.append(
                            f"release archive: symbolic link {item.filename!r}"
                        )
        except (OSError, zipfile.BadZipFile) as error:
            return [f"release archive: invalid ZIP: {error}"]
        return problems


def validate_repository(repository_root: Path) -> list[str]:
    """Run every deterministic repository validation."""
    validators = (
        validate_serialized_files,
        validate_plugin_manifest,
        validate_release_please,
        validate_issue_templates,
        validate_dependabot,
        validate_markdown_links,
        validate_release_archive,
    )
    problems: list[str] = []
    for validator in validators:
        problems.extend(validator(repository_root))
    return problems


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    problems = validate_repository(repository_root)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print("Repository metadata, links, templates, and release archive are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
