#!/usr/bin/env python3
"""Fail closed unless every planned mutmut shard produced one result."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any


RESULT_FIELDS = (
    "exit_code_by_key",
    "type_check_error_by_key",
    "durations_by_key",
    "estimated_durations_by_key",
)
PRESERVED_KILLED_EXIT_CODES = frozenset({1, 3})
MAX_METADATA_BYTES = 512 * 1024 * 1024
SHARD_PLAN_SCHEMA_VERSION = 1
MAX_MUTATION_SHARDS = 128
MAX_MUTANTS_PER_SHARD = 100_000
MAX_MUTANT_NAME_LENGTH = 4_096
MAX_METADATA_SCAN_ENTRIES = 10_000


class DuplicateJsonMember(ValueError):
    """Raised when mutation metadata contains an ambiguous duplicate key."""


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object without accepting duplicate members."""
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def reject_json_constant(value: str) -> None:
    """Reject non-standard JSON constants such as NaN and Infinity."""
    raise ValueError(f"non-standard JSON constant {value!r}")


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether a path is a link-like filesystem boundary."""
    if path.is_symlink():
        return True
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _assert_safe_path(boundary: Path, path: Path) -> None:
    """Reject a path that crosses a link or reparse point below its boundary."""
    boundary = Path(os.path.abspath(boundary))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(boundary)
    except ValueError as error:
        raise ValueError(f"metadata path escapes its boundary: {path}") from error
    current = boundary
    for part in (None, *relative.parts):
        if part is not None:
            current /= part
        if _is_link_or_reparse(current):
            raise ValueError(
                f"metadata path contains a link or reparse point: {current}"
            )
        if not current.exists():
            break


def load_json(path: Path, *, boundary: Path | None = None) -> dict[str, Any]:
    """Read one metadata document without accepting an unexpected shape."""
    try:
        if boundary is not None:
            _assert_safe_path(boundary, path)
        elif _is_link_or_reparse(path):
            raise ValueError(f"metadata path is a link or reparse point: {path}")
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise ValueError(
                f"metadata exceeds the {MAX_METADATA_BYTES}-byte size limit"
            )
        with path.open("rb") as source:
            raw = source.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            raise ValueError(
                f"metadata exceeds the {MAX_METADATA_BYTES}-byte size limit"
            )
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_json_object,
            parse_constant=reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(f"could not read {path}: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"metadata is not an object: {path}")
    return document


def validate_shard_plan(plan: dict[str, Any]) -> list[list[str]]:
    """Validate the exact bounded plan shared by every mutation worker."""
    shards = plan.get("shards")
    if (
        set(plan) != {"schema_version", "shards"}
        or type(plan.get("schema_version")) is not int
        or plan.get("schema_version") != SHARD_PLAN_SCHEMA_VERSION
        or not isinstance(shards, list)
        or len(shards) not in range(1, MAX_MUTATION_SHARDS + 1)
    ):
        raise ValueError("mutation shard plan has an invalid schema")
    validated_shards: list[list[str]] = []
    for names in shards:
        if (
            not isinstance(names, list)
            or not names
            or len(names) > MAX_MUTANTS_PER_SHARD
        ):
            raise ValueError("mutation shard plan has an invalid shard")
        validated: list[str] = []
        for name in names:
            if (
                not isinstance(name, str)
                or not name
                or len(name) > MAX_MUTANT_NAME_LENGTH
                or any(character in name for character in ("\x00", "\r", "\n"))
            ):
                raise ValueError("mutation shard plan has an invalid mutant name")
            validated.append(name)
        validated_shards.append(validated)
    return validated_shards


def _write_json_atomically(
    path: Path, document: dict[str, Any], *, boundary: Path
) -> None:
    """Publish one validated mutation document without exposing a partial write."""
    _assert_safe_path(boundary, path)
    parent = path.parent
    _assert_safe_path(boundary, parent)
    temporary: Path | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        _assert_safe_path(boundary, parent)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
            _assert_safe_path(boundary, temporary)
            output.write(json.dumps(document, indent=4) + "\n")
            output.flush()
            os.fsync(output.fileno())
        _assert_safe_path(boundary, path)
        _assert_safe_path(boundary, temporary)
        os.replace(temporary, path)
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(
            f"could not publish mutation metadata {path}: {error}"
        ) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def metadata_paths(mutants: Path, expected_count: int) -> list[Path]:
    """Enumerate mutation metadata with bounded, link-safe traversal."""
    pending = [mutants]
    paths: list[Path] = []
    scanned = 0
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as directory:
                entries = sorted(directory, key=lambda entry: entry.name)
        except OSError as error:
            raise ValueError(
                f"could not enumerate mutation metadata: {current}"
            ) from error
        for entry in entries:
            scanned += 1
            if scanned > MAX_METADATA_SCAN_ENTRIES:
                raise ValueError(
                    "mutation metadata inventory exceeds the "
                    f"{MAX_METADATA_SCAN_ENTRIES}-entry safety cap"
                )
            path = Path(entry.path)
            try:
                if _is_link_or_reparse(path):
                    raise ValueError(
                        f"mutation metadata path is a link or reparse point: {path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False) and entry.name.endswith(
                    ".meta"
                ):
                    paths.append(path)
                    if len(paths) > expected_count:
                        raise ValueError(
                            "mutation shard metadata exceeds the shard-plan inventory"
                        )
            except OSError as error:
                raise ValueError(
                    f"could not inspect mutation metadata: {path}"
                ) from error
    return sorted(paths)


def merge(repository_root: Path, artifacts_root: Path) -> None:
    """Merge only results assigned to each shard and reject incomplete state."""
    mutants = repository_root / "mutants"
    plan = load_json(mutants / "mutation-shards.json", boundary=mutants)
    shards = validate_shard_plan(plan)
    assignments = {
        name: index
        for index, names in enumerate(shards)
        for name in names
        if isinstance(name, str) and name
    }
    if len(assignments) != sum(len(names) for names in shards):
        raise ValueError("mutation shard plan has duplicate or invalid names")
    expected_metadata_files = sum(len(names) for names in shards)
    base_paths = metadata_paths(mutants, expected_metadata_files)
    if not base_paths:
        raise ValueError("mutation shard metadata is missing")
    seen: set[str] = set()
    for base_path in base_paths:
        base = load_json(base_path, boundary=mutants)
        results = base.get("exit_code_by_key")
        if not isinstance(results, dict):
            raise ValueError(f"metadata lacks mutation results: {base_path}")
        unknown = set(results) - set(assignments)
        if unknown or seen & set(results):
            raise ValueError("mutation metadata does not match the shard plan")
        seen.update(results)
        if any(
            value is not None
            and (type(value) is not int or value not in PRESERVED_KILLED_EXIT_CODES)
            for value in results.values()
        ):
            raise ValueError("base mutation metadata contains an untrusted result")
        relative = base_path.relative_to(mutants)
        baseline_values_by_field: dict[str, dict[str, Any]] = {}
        for field in RESULT_FIELDS:
            values = base.get(field)
            if not isinstance(values, dict):
                raise ValueError(f"metadata field {field!r} is invalid")
            if not set(values).issubset(results):
                raise ValueError("mutation shard metadata keys differ")
            baseline_values_by_field[field] = dict(values)
        preserved = {
            name
            for name, value in results.items()
            if value in PRESERVED_KILLED_EXIT_CODES
        }
        missing = object()
        for index in range(len(shards)):
            overlay = load_json(
                artifacts_root / f"mutation-shard-{index}" / relative,
                boundary=artifacts_root,
            )
            for field in RESULT_FIELDS:
                base_values = base[field]
                overlay_values = overlay.get(field)
                if not isinstance(overlay_values, dict):
                    raise ValueError(f"metadata field {field!r} is invalid")
                baseline_values = baseline_values_by_field[field]
                if not set(overlay_values).issubset(results):
                    raise ValueError("mutation shard metadata keys differ")
                if field == "exit_code_by_key":
                    if set(overlay_values) != set(results):
                        raise ValueError("mutation shard metadata keys differ")
                    if any(
                        value is not None and type(value) is not int
                        for value in overlay_values.values()
                    ):
                        raise ValueError("mutation shard contains an invalid verdict")
                for name in sorted(baseline_values.keys() | overlay_values.keys()):
                    before = baseline_values.get(name, missing)
                    value = overlay_values.get(name, missing)
                    if assignments[name] == index:
                        if name in preserved and value != before:
                            raise ValueError(
                                "mutation shard changed a preserved result"
                            )
                        if value is missing:
                            base_values.pop(name, None)
                        else:
                            base_values[name] = value
                    elif value != before:
                        raise ValueError("mutation shard changed an unassigned mutant")
        if any(value is None for value in results.values()):
            raise ValueError("a mutation shard did not finish every assignment")
        _write_json_atomically(base_path, base, boundary=mutants)
    if seen != set(assignments):
        raise ValueError("mutation shard plan does not match generated metadata")


def main() -> int:
    try:
        merge(Path("."), Path("mutation-shards"))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
