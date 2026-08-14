#!/usr/bin/env python3
"""Prepare and record a conservative incremental mutmut cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
MANIFEST_NAME = "mutation-cache-manifest.json"
REUSABLE_SOURCES_NAME = ".incremental-sources.json"
SOURCE_ROOTS = (PurePosixPath("scripts"), PurePosixPath("skills/repo-scaffold/scripts"))
KILLED_EXIT_CODES = {1, 3}
MAX_PROJECT_FILES = 10_000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_META_BYTES = 64 * 1024 * 1024
MAX_STATE_BYTES = 256 * 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "mutants",
    "venv",
}
IGNORED_FILE_NAMES = {".coverage"}
IGNORED_FILE_PREFIXES = (".coverage.",)


class DuplicateJsonMember(ValueError):
    """Raised when cached JSON contains an ambiguous duplicate member."""


@dataclass(frozen=True)
class ProjectSnapshot:
    """Content needed to decide whether cached killed mutants remain reusable."""

    source_hashes: dict[str, str]
    test_sources: dict[str, str]
    support_hashes: dict[str, str]
    state_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparationResult:
    """Summary of the mutation state selected for the next mutmut run."""

    full_reset: bool
    preserved_killed: int
    reset_results: int
    invalidated_files: int


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate members."""
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def _is_within(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def _validate_relative_path(value: str, *, kind: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or any(part in ("", ".") for part in path.parts)
    ):
        raise ValueError(f"invalid {kind} path {value!r}")
    return path


def _validate_digest_map(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_PROJECT_FILES:
        raise ValueError(f"manifest field {field!r} must be a bounded object")
    result: dict[str, str] = {}
    for raw_path, digest in value.items():
        if not isinstance(raw_path, str) or not isinstance(digest, str):
            raise ValueError(f"manifest field {field!r} has invalid members")
        _validate_relative_path(raw_path, kind=field)
        if SHA256_PATTERN.fullmatch(digest) is None:
            raise ValueError(f"manifest field {field!r} has an invalid digest")
        result[raw_path] = digest
    return result


def _validate_test_sources(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_PROJECT_FILES:
        raise ValueError("manifest test_sources must be a bounded object")
    result: dict[str, str] = {}
    total_bytes = 0
    for raw_path, source in value.items():
        if not isinstance(raw_path, str) or not isinstance(source, str):
            raise ValueError("manifest test_sources has invalid members")
        path = _validate_relative_path(raw_path, kind="test")
        if not _is_within(path, PurePosixPath("tests")) or path.suffix != ".py":
            raise ValueError(f"manifest has invalid test path {raw_path!r}")
        total_bytes += len(source.encode("utf-8"))
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("manifest test_sources exceeds the size limit")
        result[raw_path] = source
    return result


def _validate_source_paths(source_hashes: dict[str, str]) -> None:
    for raw_path in source_hashes:
        path = PurePosixPath(raw_path)
        if path.suffix != ".py" or not any(
            _is_within(path, root) for root in SOURCE_ROOTS
        ):
            raise ValueError(f"manifest has invalid source path {raw_path!r}")


def _expected_state_paths(source_hashes: dict[str, str]) -> set[str]:
    paths = {"mutmut-stats.json"}
    for relative in source_hashes:
        paths.add(relative)
        paths.add(f"{relative}.meta")
    return paths


def _validate_state_paths(
    state_hashes: dict[str, str], source_hashes: dict[str, str]
) -> None:
    if set(state_hashes) != _expected_state_paths(source_hashes):
        raise ValueError("manifest state paths differ from configured mutation sources")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether an existing path is a link-like filesystem boundary."""
    if path.is_symlink():
        return True
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _assert_safe_cache_path(mutation_root: Path, path: Path) -> None:
    """Reject a cache path with a symlink or reparse point in any component."""
    boundary = Path(os.path.abspath(mutation_root))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(boundary)
    except ValueError as error:
        raise ValueError(f"mutation cache path escapes mutants: {path}") from error
    current = boundary
    for part in (None, *relative.parts):
        if part is not None:
            current /= part
        if _is_link_or_reparse(current):
            raise ValueError(
                f"mutation cache path contains a link or reparse point: {current}"
            )
        if not current.exists():
            break


def _assert_safe_project_path(repository_root: Path, path: Path) -> None:
    """Reject project paths that cross a symlink or Windows reparse point."""
    boundary = Path(os.path.abspath(repository_root))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(boundary)
    except ValueError as error:
        raise ValueError(
            f"project inventory path escapes repository: {path}"
        ) from error
    current = boundary
    for part in (None, *relative.parts):
        if part is not None:
            current /= part
        if _is_link_or_reparse(current):
            raise ValueError(
                "project inventory contains a symlink or reparse point: "
                f"{current.relative_to(boundary).as_posix() or '.'!r}"
            )
        if not current.exists():
            break


def _project_files(repository_root: Path) -> list[tuple[str, Path]]:
    repository_root = Path(os.path.abspath(repository_root))
    _assert_safe_project_path(repository_root, repository_root)
    files: list[tuple[str, Path]] = []
    total_bytes = 0
    for directory, child_directories, filenames in os.walk(
        repository_root, topdown=True, followlinks=False
    ):
        current = Path(directory)
        _assert_safe_project_path(repository_root, current)
        safe_children: list[str] = []
        for name in sorted(child_directories):
            child = current / name
            relative = child.relative_to(repository_root)
            if set(relative.parts) & IGNORED_DIRECTORIES:
                continue
            _assert_safe_project_path(repository_root, child)
            safe_children.append(name)
        child_directories[:] = safe_children

        for name in sorted(filenames):
            path = current / name
            relative = path.relative_to(repository_root)
            if relative.name in IGNORED_FILE_NAMES or relative.name.startswith(
                IGNORED_FILE_PREFIXES
            ):
                continue
            _assert_safe_project_path(repository_root, path)
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                raise ValueError(
                    f"project file {relative.as_posix()!r} exceeds the size limit"
                )
            total_bytes += size
            if len(files) >= MAX_PROJECT_FILES or total_bytes > MAX_TOTAL_BYTES:
                raise ValueError(
                    "project inventory exceeds the cache preparation limits"
                )
            files.append((relative.as_posix(), path))
    return sorted(files)


def snapshot_project(repository_root: Path) -> ProjectSnapshot:
    """Hash production and support files while retaining test source for diffs."""
    source_hashes: dict[str, str] = {}
    test_sources: dict[str, str] = {}
    support_hashes: dict[str, str] = {}
    for relative, path in _project_files(repository_root):
        posix_path = PurePosixPath(relative)
        content = path.read_bytes()
        if posix_path.suffix == ".py" and any(
            _is_within(posix_path, root) for root in SOURCE_ROOTS
        ):
            source_hashes[relative] = _sha256(content)
        elif posix_path.suffix == ".py" and _is_within(
            posix_path, PurePosixPath("tests")
        ):
            try:
                test_sources[relative] = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(f"test source {relative!r} is not UTF-8") from error
        else:
            support_hashes[relative] = _sha256(content)
    return ProjectSnapshot(source_hashes, test_sources, support_hashes)


def manifest_document(snapshot: ProjectSnapshot) -> dict[str, Any]:
    """Return the stable JSON document saved with completed mutation state."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source_hashes": snapshot.source_hashes,
        "test_sources": snapshot.test_sources,
        "support_hashes": snapshot.support_hashes,
        "state_hashes": snapshot.state_hashes,
    }


def load_manifest(path: Path) -> ProjectSnapshot:
    """Load and strictly validate a cache manifest."""
    if path.stat().st_size > MAX_TOTAL_BYTES:
        raise ValueError("mutation cache manifest exceeds the size limit")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateJsonMember) as error:
        raise ValueError(f"could not read mutation cache manifest: {error}") from error
    expected_fields = {
        "schema_version",
        "source_hashes",
        "test_sources",
        "support_hashes",
        "state_hashes",
    }
    if not isinstance(document, dict) or set(document) != expected_fields:
        raise ValueError("mutation cache manifest fields differ from the schema")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ValueError("mutation cache manifest schema version is unsupported")
    source_hashes = _validate_digest_map(
        document["source_hashes"], field="source_hashes"
    )
    _validate_source_paths(source_hashes)
    state_hashes = _validate_digest_map(document["state_hashes"], field="state_hashes")
    _validate_state_paths(state_hashes, source_hashes)
    return ProjectSnapshot(
        source_hashes=source_hashes,
        test_sources=_validate_test_sources(document["test_sources"]),
        support_hashes=_validate_digest_map(
            document["support_hashes"], field="support_hashes"
        ),
        state_hashes=state_hashes,
    )


def _write_json(path: Path, document: dict[str, Any], *, mutation_root: Path) -> None:
    _assert_safe_cache_path(mutation_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_cache_path(mutation_root, path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _assert_safe_cache_path(mutation_root, temporary)
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _assert_safe_cache_path(mutation_root, path)
    _assert_safe_cache_path(mutation_root, temporary)
    os.replace(temporary, path)


def _mutation_root(repository_root: Path) -> Path:
    root = Path(os.path.abspath(repository_root))
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    _assert_safe_project_path(root, root)
    mutation_root = root / "mutants"
    _assert_safe_cache_path(root, mutation_root)
    return mutation_root


def _clear_mutation_state(mutation_root: Path) -> None:
    _assert_safe_cache_path(mutation_root, mutation_root)
    mutation_root.mkdir(parents=True, exist_ok=True)
    _assert_safe_cache_path(mutation_root, mutation_root)
    for child in mutation_root.iterdir():
        if _is_link_or_reparse(child) or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            raise ValueError(f"unsupported mutation cache entry {child.name!r}")


def _collect_state_hashes(
    mutation_root: Path, source_hashes: dict[str, str]
) -> dict[str, str]:
    state_hashes: dict[str, str] = {}
    total_bytes = 0
    for relative in sorted(_expected_state_paths(source_hashes)):
        path = mutation_root.joinpath(*PurePosixPath(relative).parts)
        _assert_safe_cache_path(mutation_root, path)
        if not path.is_file():
            raise ValueError(f"completed mutation state is missing {relative!r}")
        content = path.read_bytes()
        total_bytes += len(content)
        if len(content) > MAX_META_BYTES or total_bytes > MAX_STATE_BYTES:
            raise ValueError("completed mutation state exceeds the size limits")
        state_hashes[relative] = _sha256(content)
    return state_hashes


def _sanitize_restored_state(mutation_root: Path, state_hashes: dict[str, str]) -> None:
    allowed = set(state_hashes) | {MANIFEST_NAME}
    for relative, expected_digest in state_hashes.items():
        path = mutation_root.joinpath(*PurePosixPath(relative).parts)
        _assert_safe_cache_path(mutation_root, path)
        if not path.is_file():
            raise ValueError(f"restored mutation state is missing {relative!r}")
        content = path.read_bytes()
        if len(content) > MAX_META_BYTES or _sha256(content) != expected_digest:
            raise ValueError(
                f"restored mutation state failed integrity for {relative!r}"
            )

    for directory, child_directories, filenames in os.walk(
        mutation_root, topdown=False, followlinks=False
    ):
        current = Path(directory)
        for name in filenames:
            path = current / name
            _assert_safe_cache_path(mutation_root, path)
            relative = path.relative_to(mutation_root).as_posix()
            if relative not in allowed:
                path.unlink()
        for name in child_directories:
            path = current / name
            if _is_link_or_reparse(path):
                path.unlink()
            elif not any(path.iterdir()):
                path.rmdir()


def _source_state_paths(mutation_root: Path, relative: str) -> tuple[Path, Path]:
    path = _validate_relative_path(relative, kind="source")
    if not any(_is_within(path, root) for root in SOURCE_ROOTS):
        raise ValueError(f"source cache path is outside configured roots: {relative!r}")
    mutant = mutation_root.joinpath(*path.parts)
    meta = Path(f"{mutant}.meta")
    _assert_safe_cache_path(mutation_root, mutant)
    _assert_safe_cache_path(mutation_root, meta)
    return mutant, meta


def _remove_source_state(mutation_root: Path, relative: str) -> None:
    for path in _source_state_paths(mutation_root, relative):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            raise ValueError(f"mutation source state is not a file: {relative!r}")


def _tests_are_compatible(previous: dict[str, str], current: dict[str, str]) -> bool:
    """Reuse killed results only when the complete test suite is unchanged."""
    return previous == current


def _load_meta(path: Path) -> dict[str, Any]:
    if _is_link_or_reparse(path) or path.stat().st_size > MAX_META_BYTES:
        raise ValueError("mutation metadata is unsafe or oversized")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateJsonMember) as error:
        raise ValueError(f"could not read mutation metadata: {error}") from error
    required = {
        "exit_code_by_key",
        "durations_by_key",
        "estimated_durations_by_key",
    }
    allowed = required | {"type_check_error_by_key"}
    if (
        not isinstance(document, dict)
        or not required.issubset(document)
        or not set(document).issubset(allowed)
    ):
        raise ValueError("mutation metadata fields differ from the mutmut schema")
    exit_codes = document["exit_code_by_key"]
    if not isinstance(exit_codes, dict) or len(exit_codes) > 1_000_000:
        raise ValueError("mutation exit-code map is invalid")
    for key, value in exit_codes.items():
        if not isinstance(key, str) or not (value is None or type(value) is int):
            raise ValueError("mutation exit-code map has invalid members")
    for metadata_field in ("durations_by_key", "estimated_durations_by_key"):
        if not isinstance(document[metadata_field], dict):
            raise ValueError(
                f"mutation metadata field {metadata_field!r} must be an object"
            )
    type_errors = document.get("type_check_error_by_key", {})
    if not isinstance(type_errors, dict):
        raise ValueError("mutation type-check metadata must be an object")
    return document


def _prepare_source_state(mutation_root: Path, relative: str) -> tuple[int, int]:
    mutant_path, meta_path = _source_state_paths(mutation_root, relative)
    if not mutant_path.is_file() or mutant_path.is_symlink() or not meta_path.is_file():
        _remove_source_state(mutation_root, relative)
        raise ValueError("cached source state is incomplete")
    document = _load_meta(meta_path)
    preserved = 0
    reset = 0
    exit_codes = document["exit_code_by_key"]
    for key, value in exit_codes.items():
        if value in KILLED_EXIT_CODES:
            preserved += 1
        else:
            exit_codes[key] = None
            reset += 1
            document.get("type_check_error_by_key", {}).pop(key, None)
    _write_json(meta_path, document, mutation_root=mutation_root)
    return preserved, reset


def prepare_cache(repository_root: Path) -> PreparationResult:
    """Preserve proven kills and reset every result that needs another test run."""
    repository_root = Path(os.path.abspath(repository_root))
    mutation_root = _mutation_root(repository_root)
    manifest_path = mutation_root / MANIFEST_NAME
    _assert_safe_cache_path(mutation_root, manifest_path)
    current = snapshot_project(repository_root)
    try:
        previous = load_manifest(manifest_path)
    except (OSError, ValueError):
        _clear_mutation_state(mutation_root)
        return PreparationResult(True, 0, 0, 0)

    if (
        previous.support_hashes != current.support_hashes
        or previous.source_hashes != current.source_hashes
        or not _tests_are_compatible(previous.test_sources, current.test_sources)
    ):
        _clear_mutation_state(mutation_root)
        return PreparationResult(True, 0, 0, 0)
    try:
        _sanitize_restored_state(mutation_root, previous.state_hashes)
    except (OSError, ValueError):
        _clear_mutation_state(mutation_root)
        return PreparationResult(True, 0, 0, 0)

    preserved = 0
    reset = 0
    invalidated = 0
    reusable_sources: list[str] = []
    for relative in sorted(current.source_hashes):
        try:
            kept, pending = _prepare_source_state(mutation_root, relative)
        except (OSError, ValueError):
            _remove_source_state(mutation_root, relative)
            invalidated += 1
        else:
            preserved += kept
            reset += pending
            reusable_sources.append(relative)

    if invalidated:
        stats_path = mutation_root / "mutmut-stats.json"
        _assert_safe_cache_path(mutation_root, stats_path)
        stats_path.unlink()
    _write_json(
        mutation_root / REUSABLE_SOURCES_NAME,
        {"schema_version": SCHEMA_VERSION, "sources": reusable_sources},
        mutation_root=mutation_root,
    )
    _assert_safe_cache_path(mutation_root, manifest_path)
    manifest_path.unlink(missing_ok=True)
    return PreparationResult(False, preserved, reset, invalidated)


def record_cache(repository_root: Path) -> None:
    """Record the repository inputs for a completed mutation run."""
    repository_root = Path(os.path.abspath(repository_root))
    mutation_root = _mutation_root(repository_root)
    if not mutation_root.is_dir():
        raise ValueError("completed mutation state is required before recording")
    reusable_path = mutation_root / REUSABLE_SOURCES_NAME
    _assert_safe_cache_path(mutation_root, reusable_path)
    reusable_path.unlink(missing_ok=True)
    snapshot = snapshot_project(repository_root)
    snapshot = ProjectSnapshot(
        source_hashes=snapshot.source_hashes,
        test_sources=snapshot.test_sources,
        support_hashes=snapshot.support_hashes,
        state_hashes=_collect_state_hashes(mutation_root, snapshot.source_hashes),
    )
    _write_json(
        mutation_root / MANIFEST_NAME,
        manifest_document(snapshot),
        mutation_root=mutation_root,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the cache operation and repository root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "record"))
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Prepare or record mutation state with actionable diagnostics."""
    arguments = parse_args(argv)
    try:
        if arguments.operation == "record":
            record_cache(arguments.repository_root)
            print("Recorded mutation cache inputs.")
        else:
            result = prepare_cache(arguments.repository_root)
            print(
                "Prepared mutation cache: "
                f"full_reset={str(result.full_reset).lower()}, "
                f"preserved_killed={result.preserved_killed}, "
                f"reset_results={result.reset_results}, "
                f"invalidated_files={result.invalidated_files}."
            )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
