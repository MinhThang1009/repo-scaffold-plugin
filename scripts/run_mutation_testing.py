#!/usr/bin/env python3
"""Run mutmut while retaining cache state that was validated for reuse."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import multiprocessing
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Callable


MUTMUT_VERSION = "3.6.0"
SCHEMA_VERSION = 1
REUSABLE_SOURCES_NAME = ".incremental-sources.json"
MAX_REUSABLE_SOURCES = 10_000

_MUTMUT_MAIN: Any = None
_ORIGINAL_CREATE_MUTANTS: Callable[[Path, Path], Any] | None = None
_REUSABLE_SOURCES: frozenset[str] = frozenset()


class DuplicateJsonMember(ValueError):
    """Raised when runner state contains an ambiguous duplicate member."""


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate members."""
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def _validate_source_path(value: str) -> str:
    path = PurePosixPath(value)
    source_roots = (
        PurePosixPath("scripts"),
        PurePosixPath("skills/repo-scaffold/scripts"),
    )
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or path.suffix != ".py"
        or not any(path == root or root in path.parents for root in source_roots)
    ):
        raise ValueError(f"invalid reusable mutation source {value!r}")
    return value


def load_reusable_sources(repository_root: Path) -> frozenset[str]:
    """Load the short-lived source allowlist emitted by cache preparation."""
    marker = repository_root / "mutants" / REUSABLE_SOURCES_NAME
    if not marker.exists():
        return frozenset()
    if marker.is_symlink() or marker.stat().st_size > 1024 * 1024:
        raise ValueError("incremental mutation source marker is unsafe or oversized")
    try:
        document = json.loads(
            marker.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateJsonMember) as error:
        raise ValueError(
            f"could not read incremental mutation sources: {error}"
        ) from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "sources"}
        or document["schema_version"] != SCHEMA_VERSION
        or not isinstance(document["sources"], list)
        or len(document["sources"]) > MAX_REUSABLE_SOURCES
    ):
        raise ValueError("incremental mutation source marker has an invalid schema")
    sources = document["sources"]
    if any(not isinstance(source, str) for source in sources):
        raise ValueError("incremental mutation source marker has invalid members")
    validated = [_validate_source_path(source) for source in sources]
    if len(validated) != len(set(validated)):
        raise ValueError("incremental mutation source marker contains duplicates")
    return frozenset(validated)


def _create_or_reuse_mutants(filename: Path, output_path: Path) -> Any:
    """Keep prepared metadata intact instead of letting mutmut reset every result."""
    if filename.as_posix() in _REUSABLE_SOURCES:
        print(filename)
        return _MUTMUT_MAIN.FileMutationResult(unmodified=True)
    if _ORIGINAL_CREATE_MUTANTS is None:
        raise RuntimeError("mutmut generation hook was not initialized")
    return _ORIGINAL_CREATE_MUTANTS(filename, output_path)


def load_mutmut() -> Any:
    """Load the reviewed mutmut implementation and reject version drift."""
    installed = importlib.metadata.version("mutmut")
    if installed != MUTMUT_VERSION:
        raise ValueError(
            f"incremental runner requires mutmut {MUTMUT_VERSION}, found {installed}"
        )
    return importlib.import_module("mutmut.__main__")


def run_mutation_testing(
    repository_root: Path, *, max_children: int, mutmut_main: Any | None = None
) -> None:
    """Run mutmut with generation skipped only for validated cached sources."""
    global _MUTMUT_MAIN, _ORIGINAL_CREATE_MUTANTS, _REUSABLE_SOURCES

    root = repository_root.resolve()
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    marker = root / "mutants" / REUSABLE_SOURCES_NAME
    reusable_sources = load_reusable_sources(root)
    implementation = mutmut_main if mutmut_main is not None else load_mutmut()
    if reusable_sources and multiprocessing.get_start_method() != "fork":
        raise ValueError("incremental mutation reuse requires fork process semantics")
    original = implementation.create_mutants_for_file
    previous_cwd = Path.cwd()
    _MUTMUT_MAIN = implementation
    _ORIGINAL_CREATE_MUTANTS = original
    _REUSABLE_SOURCES = reusable_sources
    implementation.create_mutants_for_file = _create_or_reuse_mutants
    try:
        os.chdir(root)
        implementation._run([], max_children)
    finally:
        os.chdir(previous_cwd)
        implementation.create_mutants_for_file = original
        _MUTMUT_MAIN = None
        _ORIGINAL_CREATE_MUTANTS = None
        _REUSABLE_SOURCES = frozenset()
        marker.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the repository and bounded mutmut worker count."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--max-children", type=int, default=4, choices=range(1, 33))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run mutation testing with concise diagnostics for configuration failures."""
    arguments = parse_args(argv)
    try:
        run_mutation_testing(
            arguments.repository_root, max_children=arguments.max_children
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
