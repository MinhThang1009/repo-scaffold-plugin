#!/usr/bin/env python3
"""Fail closed unless every planned mutmut shard produced one result."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


RESULT_FIELDS = (
    "exit_code_by_key",
    "type_check_error_by_key",
    "durations_by_key",
    "estimated_durations_by_key",
)


def load_json(path: Path) -> dict[str, Any]:
    """Read one metadata document without accepting an unexpected shape."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"could not read {path}: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"metadata is not an object: {path}")
    return document


def merge(repository_root: Path, artifacts_root: Path) -> None:
    """Merge only results assigned to each shard and reject incomplete state."""
    mutants = repository_root / "mutants"
    plan = load_json(mutants / "mutation-shards.json")
    shards = plan.get("shards")
    if (
        not isinstance(shards, list)
        or not shards
        or any(not isinstance(names, list) or not names for names in shards)
    ):
        raise ValueError("mutation shard plan has an invalid schema")
    assignments = {
        name: index
        for index, names in enumerate(shards)
        for name in names
        if isinstance(name, str) and name
    }
    if len(assignments) != sum(len(names) for names in shards):
        raise ValueError("mutation shard plan has duplicate or invalid names")
    base_paths = sorted(mutants.rglob("*.meta"))
    if not base_paths:
        raise ValueError("mutation shard metadata is missing")
    seen: set[str] = set()
    for base_path in base_paths:
        base = load_json(base_path)
        results = base.get("exit_code_by_key")
        if not isinstance(results, dict):
            raise ValueError(f"metadata lacks mutation results: {base_path}")
        unknown = set(results) - set(assignments)
        if unknown or seen & set(results):
            raise ValueError("mutation metadata does not match the shard plan")
        seen.update(results)
        if any(value is not None for value in results.values()):
            raise ValueError("base mutation metadata already contains results")
        relative = base_path.relative_to(mutants)
        for index in range(len(shards)):
            overlay = load_json(artifacts_root / f"mutation-shard-{index}" / relative)
            for field in RESULT_FIELDS:
                base_values = base.get(field)
                overlay_values = overlay.get(field)
                if not isinstance(base_values, dict) or not isinstance(
                    overlay_values, dict
                ):
                    raise ValueError(f"metadata field {field!r} is invalid")
                if set(base_values) != set(overlay_values):
                    raise ValueError("mutation shard metadata keys differ")
                for name, value in overlay_values.items():
                    if assignments[name] == index:
                        base_values[name] = value
                    elif value is not None:
                        raise ValueError("mutation shard changed an unassigned mutant")
        if any(value is None for value in results.values()):
            raise ValueError("a mutation shard did not finish every assignment")
        base_path.write_text(json.dumps(base, indent=4) + "\n", encoding="utf-8")
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
