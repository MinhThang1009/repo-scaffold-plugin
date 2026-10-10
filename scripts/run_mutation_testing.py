#!/usr/bin/env python3
"""Run mutmut while retaining cache state that was validated for reuse."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable


MUTMUT_VERSION = "3.8.0"
SCHEMA_VERSION = 1
REUSABLE_SOURCES_NAME = ".incremental-sources.json"
MAX_REUSABLE_SOURCES = 10_000
SHARD_PLAN_NAME = "mutation-shards.json"
SHARD_PLAN_SCHEMA_VERSION = 1
MAX_MUTATION_SHARDS = 128
MAX_MUTANTS_PER_SHARD = 100_000
MAX_REUSABLE_SOURCES_BYTES = 1024 * 1024
MAX_SHARD_PLAN_BYTES = 512 * 1024 * 1024

_MUTMUT_MAIN: Any = None
_ORIGINAL_CREATE_MUTANTS: Callable[[Path, Path], Any] | None = None
_REUSABLE_SOURCES: frozenset[str] = frozenset()
_SHARD_PLAN_WRITE_LOCK = threading.Lock()


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


def reject_json_constant(value: str) -> None:
    """Reject non-standard JSON constants such as NaN and Infinity."""
    raise ValueError(f"non-standard JSON constant {value!r}")


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
        or "\\" in value
        or any(ord(character) < 0x20 for character in value)
        or any(PureWindowsPath(part).drive for part in path.parts)
        or path.as_posix() != value
        or path.suffix != ".py"
        or not any(path == root or root in path.parents for root in source_roots)
    ):
        raise ValueError(f"invalid reusable mutation source {value!r}")
    return value


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


def _assert_safe_marker_path(repository_root: Path, marker: Path) -> None:
    """Reject a marker with a link or reparse point in any path component."""
    boundary = Path(os.path.abspath(repository_root))
    candidate = Path(os.path.abspath(marker))
    try:
        relative = candidate.relative_to(boundary)
    except ValueError as error:
        raise ValueError(
            f"incremental mutation marker escapes repository: {marker}"
        ) from error
    current = boundary
    for part in relative.parts:
        current /= part
        if _is_link_or_reparse(current):
            raise ValueError(
                "incremental mutation marker contains a link or reparse point: "
                f"{current}"
            )
        if not current.exists():
            break


def load_reusable_sources(repository_root: Path) -> frozenset[str]:
    """Load the short-lived source allowlist emitted by cache preparation."""
    marker = repository_root / "mutants" / REUSABLE_SOURCES_NAME
    _assert_safe_marker_path(repository_root, marker)
    if not marker.exists():
        return frozenset()
    if marker.stat().st_size > MAX_REUSABLE_SOURCES_BYTES:
        raise ValueError("incremental mutation source marker is unsafe or oversized")
    try:
        with marker.open("rb") as source:
            raw = source.read(MAX_REUSABLE_SOURCES_BYTES + 1)
        if len(raw) > MAX_REUSABLE_SOURCES_BYTES:
            raise ValueError(
                "incremental mutation source marker is unsafe or oversized"
            )
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_json_object,
            parse_constant=reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(
            f"could not read incremental mutation sources: {error}"
        ) from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "sources"}
        or type(document["schema_version"]) is not int
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
        mutation_data = _MUTMUT_MAIN.SourceFileMutationData(path=filename)
        mutation_data.load()
        current_hashes = {
            _MUTMUT_MAIN.get_mutant_name(filename, function): digest
            for function, digest in mutation_data.hash_by_function_name.items()
        }
        return _MUTMUT_MAIN.FileMutationResult(
            unmodified=True, current_hashes=current_hashes
        )
    if _ORIGINAL_CREATE_MUTANTS is None:
        raise RuntimeError("mutmut generation hook was not initialized")
    return _ORIGINAL_CREATE_MUTANTS(filename, output_path)


def load_mutmut() -> Any:
    """Load the reviewed mutmut implementation and reject version drift."""
    try:
        installed = importlib.metadata.version("mutmut")
    except importlib.metadata.PackageNotFoundError as error:
        raise ValueError(
            f"incremental runner requires mutmut {MUTMUT_VERSION}, but it is not installed"
        ) from error
    if installed != MUTMUT_VERSION:
        raise ValueError(
            f"incremental runner requires mutmut {MUTMUT_VERSION}, found {installed}"
        )
    try:
        return importlib.import_module("mutmut.__main__")
    except ModuleNotFoundError as error:
        raise ValueError(
            f"incremental runner could not import mutmut {MUTMUT_VERSION}: {error}"
        ) from error


def _validate_mutant_name(value: object) -> str:
    """Validate a bounded mutant key received through an internal artifact."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4_096
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        raise ValueError("mutation shard plan contains an invalid mutant name")
    return value


def shard_mutants(mutant_names: list[str], shard_count: int) -> list[list[str]]:
    """Split unique mutant names evenly and deterministically across shards."""
    if shard_count not in range(1, MAX_MUTATION_SHARDS + 1):
        raise ValueError(
            f"mutation shard count must be between 1 and {MAX_MUTATION_SHARDS}"
        )
    names = sorted(_validate_mutant_name(name) for name in mutant_names)
    if not names:
        raise ValueError("mutation shard plan cannot be empty")
    if shard_count > len(names):
        raise ValueError("mutation shard count exceeds the mutant inventory")
    if len(names) != len(set(names)):
        raise ValueError("mutation shard plan contains duplicate mutant names")
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    for index, name in enumerate(names):
        shards[index % shard_count].append(name)
    if any(len(shard) > MAX_MUTANTS_PER_SHARD for shard in shards):
        raise ValueError("mutation shard exceeds the configured mutant limit")
    return shards


def write_shard_plan(
    repository_root: Path, mutant_names: list[str], shard_count: int
) -> Path:
    """Write one plan at a time so local callers cannot collide on publication."""
    with _SHARD_PLAN_WRITE_LOCK:
        return _write_shard_plan(repository_root, mutant_names, shard_count)


def _write_shard_plan(
    repository_root: Path, mutant_names: list[str], shard_count: int
) -> Path:
    """Write the exact, bounded mutation assignment used by every worker."""
    shards = shard_mutants(mutant_names, shard_count)
    path = repository_root / "mutants" / SHARD_PLAN_NAME
    _assert_safe_marker_path(repository_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoder = json.JSONEncoder(separators=(",", ":"), ensure_ascii=False)
    document = {"schema_version": SHARD_PLAN_SCHEMA_VERSION, "shards": shards}
    total_bytes = 0
    temporary_file = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(temporary_file.name)
    try:
        with temporary_file as output:
            _assert_safe_marker_path(repository_root, temporary)
            for chunk in encoder.iterencode(document):
                encoded = chunk.encode("utf-8")
                total_bytes += len(encoded)
                if total_bytes + 1 > MAX_SHARD_PLAN_BYTES:
                    raise ValueError("mutation shard plan exceeds the byte safety cap")
                output.write(encoded)
            output.write(b"\n")
        _assert_safe_marker_path(repository_root, path)
        _assert_safe_marker_path(repository_root, temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            _assert_safe_marker_path(repository_root, temporary)
            temporary.unlink()
    return path


def load_shard_names(repository_root: Path, shard_index: int) -> list[str]:
    """Load one exact shard and reject malformed or ambiguous assignments."""
    path = repository_root / "mutants" / SHARD_PLAN_NAME
    _assert_safe_marker_path(repository_root, path)
    try:
        if path.stat().st_size > MAX_SHARD_PLAN_BYTES:
            raise ValueError("mutation shard plan exceeds the byte safety cap")
        with path.open("rb") as source:
            raw = source.read(MAX_SHARD_PLAN_BYTES + 1)
        if len(raw) > MAX_SHARD_PLAN_BYTES:
            raise ValueError("mutation shard plan exceeds the byte safety cap")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_json_object,
            parse_constant=reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(f"could not read mutation shard plan: {error}") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "shards"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != SHARD_PLAN_SCHEMA_VERSION
        or not isinstance(document["shards"], list)
        or len(document["shards"]) not in range(1, MAX_MUTATION_SHARDS + 1)
    ):
        raise ValueError("mutation shard plan has an invalid schema")
    if shard_index not in range(len(document["shards"])):
        raise ValueError("mutation shard index is outside the planned range")
    flattened: list[str] = []
    shards: list[list[str]] = []
    for shard in document["shards"]:
        if (
            not isinstance(shard, list)
            or not shard
            or len(shard) > MAX_MUTANTS_PER_SHARD
        ):
            raise ValueError("mutation shard plan has an invalid shard")
        validated = [_validate_mutant_name(name) for name in shard]
        shards.append(validated)
        flattened.extend(validated)
    if len(flattened) != len(set(flattened)):
        raise ValueError("mutation shard plan assigns a mutant more than once")
    return shards[shard_index]


def prepare_mutation_shards(
    repository_root: Path,
    *,
    max_children: int,
    shard_count: int,
    mutmut_main: Any | None = None,
) -> Path:
    """Generate mutation metadata and an exact shard assignment only."""
    implementation = mutmut_main if mutmut_main is not None else load_mutmut()
    captured = _generate_mutation_sources(
        repository_root, max_children=max_children, mutmut_main=implementation
    )
    return write_shard_plan(repository_root, captured, shard_count)


def _ensure_mutmut_config(mutmut_main: Any) -> None:
    """Load either the current or legacy pinned mutmut configuration API."""
    config_loader = getattr(mutmut_main, "config", None)
    if callable(config_loader):
        config_loader()
    else:
        mutmut_main.Config.ensure_loaded()


def _pending_reusable_mutants(
    repository_root: Path, mutmut_main: Any, mutant_names: list[str]
) -> list[str]:
    """Return only shard assignments without a validated cached verdict."""
    previous_cwd = Path.cwd()
    try:
        os.chdir(repository_root)
        _ensure_mutmut_config(mutmut_main)
        # Shards contain exact names; upstream glob filtering scans every pattern
        # for every mutant before we even check which verdicts are reusable.
        mutants, _ = mutmut_main.collect_source_file_mutation_data(mutant_names=[])
    finally:
        os.chdir(previous_cwd)
    requested = set(mutant_names)
    verdicts = {name: result for _, name, result in mutants if name in requested}
    if verdicts.keys() != requested:
        raise ValueError("mutation metadata is missing a shard assignment")
    return [name for name in mutant_names if verdicts[name] is None]


def _generate_mutation_sources(
    repository_root: Path, *, max_children: int, mutmut_main: Any
) -> list[str]:
    """Generate mutation metadata and return names without running mutant tests."""
    global _MUTMUT_MAIN, _ORIGINAL_CREATE_MUTANTS, _REUSABLE_SOURCES

    root = Path(os.path.abspath(repository_root))
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    if _is_link_or_reparse(root):
        raise ValueError(f"repository root is a link or reparse point: {root}")
    marker = root / "mutants" / REUSABLE_SOURCES_NAME
    reusable_sources = load_reusable_sources(root)
    if reusable_sources and multiprocessing.get_start_method() != "fork":
        raise ValueError("incremental mutation reuse requires fork process semantics")

    original = mutmut_main.create_mutants_for_file
    previous_cwd = Path.cwd()
    previous_mutant_under_test = os.environ.get("MUTANT_UNDER_TEST")
    _MUTMUT_MAIN = mutmut_main
    _ORIGINAL_CREATE_MUTANTS = original
    _REUSABLE_SOURCES = reusable_sources
    mutmut_main.create_mutants_for_file = _create_or_reuse_mutants
    set_mutant_under_test = getattr(mutmut_main, "set_mutant_under_test", None)
    try:
        os.chdir(root)
        if callable(set_mutant_under_test):
            set_mutant_under_test("mutant_generation")
        else:
            os.environ["MUTANT_UNDER_TEST"] = "mutant_generation"
        _ensure_mutmut_config(mutmut_main)
        Path("mutants").mkdir(parents=True, exist_ok=True)
        mutmut_main.copy_src_dir()
        mutmut_main.copy_also_copy_files()
        mutmut_main.setup_source_paths()
        mutmut_main.store_lines_covered_by_tests()
        mutmut_main.create_mutants(max_children)
        mutants, _ = mutmut_main.collect_source_file_mutation_data(mutant_names=[])
        return [name for _, name, _ in mutants]
    finally:
        os.chdir(previous_cwd)
        if callable(set_mutant_under_test):
            set_mutant_under_test(None)
        if previous_mutant_under_test is None:
            os.environ.pop("MUTANT_UNDER_TEST", None)
        else:
            os.environ["MUTANT_UNDER_TEST"] = previous_mutant_under_test
        mutmut_main.create_mutants_for_file = original
        _MUTMUT_MAIN = None
        _ORIGINAL_CREATE_MUTANTS = None
        _REUSABLE_SOURCES = frozenset()
        _assert_safe_marker_path(root, marker)
        marker.unlink(missing_ok=True)


def run_mutation_testing(
    repository_root: Path,
    *,
    max_children: int,
    mutant_names: list[str] | None = None,
    mutmut_main: Any | None = None,
) -> None:
    """Run mutmut with generation skipped only for validated cached sources."""
    global _MUTMUT_MAIN, _ORIGINAL_CREATE_MUTANTS, _REUSABLE_SOURCES

    root = Path(os.path.abspath(repository_root))
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    if _is_link_or_reparse(root):
        raise ValueError(f"repository root is a link or reparse point: {root}")
    marker = root / "mutants" / REUSABLE_SOURCES_NAME
    reusable_sources = load_reusable_sources(root)
    implementation = mutmut_main if mutmut_main is not None else load_mutmut()
    if reusable_sources and multiprocessing.get_start_method() != "fork":
        raise ValueError("incremental mutation reuse requires fork process semantics")
    if reusable_sources and mutant_names:
        mutant_names = _pending_reusable_mutants(root, implementation, mutant_names)
        if not mutant_names:
            _assert_safe_marker_path(root, marker)
            marker.unlink(missing_ok=True)
            return
    original = implementation.create_mutants_for_file
    previous_cwd = Path.cwd()
    _MUTMUT_MAIN = implementation
    _ORIGINAL_CREATE_MUTANTS = original
    _REUSABLE_SOURCES = reusable_sources
    implementation.create_mutants_for_file = _create_or_reuse_mutants
    try:
        os.chdir(root)
        implementation._run(mutant_names or [], max_children)
    finally:
        os.chdir(previous_cwd)
        implementation.create_mutants_for_file = original
        _MUTMUT_MAIN = None
        _ORIGINAL_CREATE_MUTANTS = None
        _REUSABLE_SOURCES = frozenset()
        _assert_safe_marker_path(root, marker)
        marker.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the repository and bounded mutmut worker count."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--max-children", type=int, default=4, choices=range(1, 33))
    parser.add_argument("--shard-index", type=int)
    parser.add_argument(
        "--plan-shards", type=int, choices=range(1, MAX_MUTATION_SHARDS + 1)
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run mutation testing with concise diagnostics for configuration failures."""
    arguments = parse_args(argv)
    try:
        root = Path(os.path.abspath(arguments.repository_root))
        if arguments.plan_shards is not None:
            if arguments.shard_index is not None:
                raise ValueError("cannot plan mutation shards while running one shard")
            path = prepare_mutation_shards(
                root,
                max_children=arguments.max_children,
                shard_count=arguments.plan_shards,
            )
            print(f"Prepared mutation shard plan: {path.as_posix()}")
            return 0
        mutant_names = (
            load_shard_names(root, arguments.shard_index)
            if arguments.shard_index is not None
            else None
        )
        run_mutation_testing(
            root, max_children=arguments.max_children, mutant_names=mutant_names
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
