#!/usr/bin/env python3
"""Run actionlint against installed workflows and workflow assets."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def resolve_path_executable(name: str, *, forbidden_root: Path) -> str | None:
    """Resolve a tool only from absolute PATH entries outside the repository."""
    forbidden = forbidden_root.resolve(strict=True)
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_directory:
            continue
        directory = Path(raw_directory.strip('"'))
        if not directory.is_absolute():
            continue
        candidate = shutil.which(str(directory / name))
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


def run_actionlint(
    executable: str, workflow_files: list[Path], *, working_directory: Path
) -> int:
    command = [
        executable,
        "-no-color",
        "-shellcheck=",
        *(str(path) for path in workflow_files),
    ]
    return subprocess.run(  # noqa: S603 - executable is resolved safely
        command, cwd=working_directory, check=False
    ).returncode


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    actionlint = resolve_path_executable("actionlint", forbidden_root=repository_root)
    if actionlint is None:
        print(
            "actionlint is required on an absolute PATH entry outside the repository.",
            file=sys.stderr,
        )
        return 2

    installed_workflows = sorted(
        (repository_root / ".github" / "workflows").glob("*.yml")
    )
    asset_workflows = sorted(
        (repository_root / "skills" / "repo-scaffold" / "assets" / "workflows").glob(
            "*.yml"
        )
    )
    if not installed_workflows or not asset_workflows:
        print("Expected installed workflows and workflow assets.", file=sys.stderr)
        return 2

    installed_result = run_actionlint(
        actionlint,
        installed_workflows,
        working_directory=repository_root,
    )
    if installed_result != 0:
        return installed_result

    with tempfile.TemporaryDirectory(prefix="repo-scaffold-actionlint-") as temp:
        temporary_root = Path(temp)
        temporary_workflows = temporary_root / ".github" / "workflows"
        temporary_workflows.mkdir(parents=True)
        copied_workflows: list[Path] = []
        for source in asset_workflows:
            destination = temporary_workflows / source.name
            shutil.copy2(source, destination)
            copied_workflows.append(destination)

        return run_actionlint(
            actionlint,
            copied_workflows,
            working_directory=temporary_root,
        )


if __name__ == "__main__":
    raise SystemExit(main())
