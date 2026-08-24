#!/usr/bin/env python3
"""Run actionlint against installed workflows and workflow assets."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


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


def run_actionlint(
    executable: str, workflow_files: list[Path], *, working_directory: Path
) -> int:
    command = [
        executable,
        "-no-color",
        # ShellCheck is run separately in binary mode. actionlint's Windows
        # integration can block on large run blocks or introduce CRLF on stdin.
        "-shellcheck=",
        *(str(path) for path in workflow_files),
    ]
    try:
        return subprocess.run(  # noqa: S603 - executable is resolved safely
            command, cwd=working_directory, check=False, timeout=60
        ).returncode
    except subprocess.TimeoutExpired:
        print("actionlint timed out.", file=sys.stderr)
        return 2


def discover_workflows(directory: Path) -> list[Path]:
    """Return direct GitHub workflow files using either supported YAML suffix."""
    return sorted(
        path
        for path in directory.glob("*")
        if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
    )


def workflow_shell_blocks(path: Path) -> list[tuple[str, str, bytes]]:
    """Extract statically identifiable Bash and POSIX shell run blocks."""
    document: Any = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(document, dict):
        raise ValueError("workflow root must be a mapping")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        raise ValueError("workflow jobs must be a mapping")

    blocks: list[tuple[str, str, bytes]] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict) or "runs-on" not in job:
            continue
        defaults = job.get("defaults")
        default_run = defaults.get("run") if isinstance(defaults, dict) else None
        default_shell = (
            default_run.get("shell") if isinstance(default_run, dict) else None
        )
        runner = job.get("runs-on")
        runner_has_ubuntu = (
            any("ubuntu" in str(value) for value in runner)
            if isinstance(runner, list)
            else "ubuntu" in str(runner)
        )
        steps = job.get("steps")
        if steps is None:
            continue
        if not isinstance(steps, list):
            raise ValueError(f"job {job_name!r} steps must be a list")
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or "run" not in step:
                continue
            script = step.get("run")
            if not isinstance(script, str):
                raise ValueError(f"job {job_name!r} step {index} run must be text")
            shell_value = step.get("shell", default_shell)
            if shell_value is None and runner_has_ubuntu:
                shell_value = "bash"
            shell_parts = str(shell_value).split() if shell_value is not None else []
            shell_name = shell_parts[0] if shell_parts else ""
            if shell_name not in {"bash", "sh"}:
                raise ValueError(
                    f"job {job_name!r} step {index} uses unsupported shell "
                    f"{shell_value!r}"
                )
            label = str(step.get("name", f"step {index}"))
            blocks.append((f"{job_name}: {label}", shell_name, script.encode()))
    return blocks


def run_shellcheck(executable: str, workflow_files: list[Path]) -> int:
    """Run ShellCheck with bounded binary stdin for every workflow run block."""
    found_block = False
    for path in workflow_files:
        try:
            blocks = workflow_shell_blocks(path)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
            print(f"{path}: could not extract shell blocks: {error}", file=sys.stderr)
            return 2
        for label, shell_name, script in blocks:
            found_block = True
            command = [
                executable,
                f"--shell={shell_name}",
                "--format=gcc",
                "-",
            ]
            try:
                result = subprocess.run(  # noqa: S603 - executable is resolved safely
                    command,
                    input=script,
                    check=False,
                    capture_output=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                print(f"{path} ({label}): ShellCheck timed out.", file=sys.stderr)
                return 2
            if result.returncode != 0:
                print(f"{path} ({label}):", file=sys.stderr)
                sys.stderr.buffer.write(result.stdout)
                sys.stderr.buffer.write(result.stderr)
                return result.returncode
    if not found_block:
        print("No shell run blocks were found.", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    actionlint = resolve_path_executable("actionlint", forbidden_root=repository_root)
    if actionlint is None:
        print(
            "actionlint is required on an absolute PATH entry outside the repository.",
            file=sys.stderr,
        )
        return 2
    shellcheck = resolve_path_executable("shellcheck", forbidden_root=repository_root)
    if shellcheck is None:
        print(
            "ShellCheck is required on an absolute PATH entry outside the repository.",
            file=sys.stderr,
        )
        return 2

    installed_workflows = discover_workflows(repository_root / ".github" / "workflows")
    asset_workflows = discover_workflows(
        repository_root / "skills" / "repo-scaffold" / "assets" / "workflows"
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
    shellcheck_result = run_shellcheck(
        shellcheck, [*installed_workflows, *asset_workflows]
    )
    if shellcheck_result != 0:
        return shellcheck_result

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
