#!/usr/bin/env python3
"""Fail-closed capability preflight for GitHub Actions workflow assets."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shlex
import stat
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from codeql_preflight import (
    GitHubClient,
    InspectionError,
    MAX_WORKFLOW_BYTES,
    UniqueKeyBaseLoader,
    split_repository,
    yaml,
)
import sync_action_pins


ALLOWED_ACTION_POLICIES = frozenset({"all", "local_only", "selected"})
CODE_SCANNING_ALLOWLIST_SCHEMA_VERSION = 3
MAX_CODE_SCANNING_ALLOWLIST_BYTES = 1024 * 1024
MAX_CODE_SCANNING_ALLOWLIST_ENTRIES = 256
MAX_CODE_SCANNING_ALLOWLIST_REVIEW_DAYS = 366
CODE_SCANNING_GATE_COMMAND = "scripts/check_code_scanning_alerts.py"
FRESHNESS_AUDIT_COMMAND = "python scripts/audit_freshness.py"
FRESHNESS_REMINDER_MARKER = "repo-scaffold-freshness-audit"
FRESHNESS_REMINDER_REPOSITORY_OPTION = "--repo"
FRESHNESS_REMINDER_BODY_FILE = "--body-file"
FRESHNESS_REMINDER_TITLE_OPTION = "--title"
FRESHNESS_REMINDER_MUTATION_SUBCOMMANDS = frozenset({"create", "edit", "close"})
FRESHNESS_REMINDER_BODY_SUBCOMMANDS = frozenset({"create", "edit"})
FRESHNESS_REMINDER_ALLOWED_MUTATION_SUBCOMMANDS = frozenset({"create", "edit", "close"})
FRESHNESS_REMINDER_READ_ONLY_SUBCOMMANDS = frozenset({"list", "ls", "status", "view"})
FRESHNESS_REMINDER_CONCURRENCY_GROUP = "${{ github.workflow }}-${{ github.repository }}"
FRESHNESS_REMINDER_JOB_NAME = "freshness-audit"
FRESHNESS_REMINDER_TIMEOUT_MINUTES = "15"
GITHUB_API_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
GITHUB_API_BODY_OPTIONS = frozenset({"--field", "--input", "--raw-field", "-F", "-f"})
GITHUB_API_METHOD_OPTIONS = frozenset({"--method", "-X"})
GITHUB_CLI_EXECUTABLE_NAMES = frozenset({"gh", "gh.exe"})
EMBEDDED_GITHUB_COMMAND_PATTERN = re.compile(
    r"(?i)(?:^|[\s`$(&|;/\\])(?:[^`$(&|;]*[/\\])?gh(?:\.exe)?\s+(?:api|issue)(?:\s|$)"
)
EMBEDDED_FRESHNESS_AUDIT_PATTERN = re.compile(
    r"(?<!\S)python\s+scripts/audit_freshness\.py(?:\s|$)"
)
DYNAMIC_SHELL_EXECUTORS = frozenset(
    {
        "bash",
        "cmd",
        "dash",
        "eval",
        "fish",
        "powershell",
        "pwsh",
        "sh",
        "xargs",
        "zsh",
    }
)
SHELL_COMMAND_WRAPPERS = frozenset({"command", "env", "nice", "nohup", "sudo", "time"})
UNSAFE_NETWORK_EXECUTORS = frozenset(
    {
        "curl",
        "curl.exe",
        "irm",
        "invoke-restmethod",
        "invoke-webrequest",
        "iwr",
        "wget",
        "wget.exe",
    }
)
NETWORK_METHOD_OPTIONS = frozenset({"--method", "--request", "-method", "-request"})
NETWORK_BODY_OPTIONS = frozenset(
    {
        "--body",
        "--data",
        "--data-binary",
        "--data-raw",
        "--data-urlencode",
        "--form",
        "--form-string",
        "--json",
        "--post-data",
        "--post-file",
        "--upload-file",
        "-body",
        "-d",
        "-form",
        "-infile",
        "-post-data",
        "-post-file",
    }
)
FRESHNESS_AUDIT_REQUIRED_OPTIONS = (
    "--repository-root",
    "--json-output",
    "--markdown-output",
)
CONTAINER_REFERENCE_PATTERN = re.compile(r"docker://[^\s@]+@sha256:[0-9a-f]{64}\Z")


class DuplicateJsonMember(ValueError):
    """Raised when a JSON companion file contains ambiguous duplicate keys."""


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting ambiguous duplicate member names."""
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def workflow_document(text: str, source: Path) -> dict[str, Any]:
    """Parse one workflow mapping without silently accepting duplicate keys."""
    try:
        loader = UniqueKeyBaseLoader(text)
        try:
            document = loader.get_single_data()
        finally:
            loader.dispose()
    except (yaml.YAMLError, InspectionError, RecursionError) as exc:
        raise InspectionError(f"Could not parse workflow {source}: {exc}") from exc
    if not isinstance(document, dict):
        raise InspectionError(f"Workflow {source} is not a YAML mapping.")
    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict) or any(
        not isinstance(job, dict) for job in jobs.values()
    ):
        raise InspectionError(f"Workflow {source} has an invalid jobs mapping.")
    return document


def workflow_run_commands(document: dict[str, Any]) -> list[str]:
    """Return shell command scalars from workflow steps."""
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict)
    commands: list[str] = []
    for job in jobs.values():
        assert isinstance(job, dict)
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                commands.append(step["run"])
    return commands


def executable_shell_lines(command: str) -> list[str]:
    """Return non-empty shell lines whose first token is not a comment."""
    return [
        line.lstrip()
        for line in command.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def executable_basename(token: str) -> str:
    """Return a case-insensitive executable basename from either path style."""
    return re.split(r"[/\\]", token)[-1].casefold()


def is_github_cli_executable(token: str) -> bool:
    """Return whether a token invokes gh, including path-qualified Windows forms."""
    return executable_basename(token) in GITHUB_CLI_EXECUTABLE_NAMES


def has_nonempty_option_value(tokens: list[str], option: str) -> bool:
    """Return whether parsed shell tokens contain a non-empty option value."""
    values = option_values(tokens, option)
    return values is not None and bool(values)


def option_values(tokens: list[str], option: str) -> tuple[str, ...] | None:
    """Return unambiguous values for one option, or ``None`` on malformed use."""
    option_prefix = f"{option}="
    values: list[str] = []
    for index, token in enumerate(tokens):
        if token.startswith(option_prefix):
            value = token.partition("=")[2].strip()
            if not value:
                return None
            values.append(value)
            continue
        if token == option and index + 1 < len(tokens):
            value = tokens[index + 1].strip()
            if not value or value.startswith("-"):
                return None
            values.append(value)
        elif token == option:
            return None
    return tuple(values)


SHELL_CONTROL_CHARACTERS = frozenset(";()|&")
SHELL_COMMAND_PREFIXES = frozenset({"!", "then", "do", "else"})
SHELL_TEST_OPERATORS = frozenset(
    {
        "=",
        "==",
        "!=",
        "-d",
        "-e",
        "-eq",
        "-f",
        "-ge",
        "-gt",
        "-le",
        "-lt",
        "-n",
        "-ne",
        "-r",
        "-s",
        "-v",
        "-w",
        "-x",
        "-z",
    }
)
INVALID_ISSUE_MUTATION = "__invalid__"


def has_embedded_command(tokens: list[str]) -> bool:
    """Reject quoted command text that the shell tokenizer cannot execute safely."""
    patterns = (EMBEDDED_GITHUB_COMMAND_PATTERN, EMBEDDED_FRESHNESS_AUDIT_PATTERN)
    for token in tokens:
        if not any(character.isspace() for character in token):
            continue
        if any(pattern.search(token) for pattern in patterns):
            return True
    direct_command = len(tokens) >= 2 and (
        tokens[0] == "gh"
        and tokens[1] in {"api", "issue"}
        or tuple(tokens[:2]) == ("python", "scripts/audit_freshness.py")
    )
    return not direct_command and any(
        pattern.search(" ".join(tokens)) for pattern in patterns
    )


def has_dynamic_shell_executor(tokens: list[str]) -> bool:
    """Reject command interpreters that could hide an unparseable mutation."""
    index = 0
    while index < len(tokens):
        if "=" not in tokens[index]:
            break
        assignment_name = tokens[index].partition("=")[0]
        if not assignment_name.isidentifier():
            break
        index += 1
    if index >= len(tokens):
        return False
    command = executable_basename(tokens[index])
    if command in SHELL_COMMAND_WRAPPERS or command in {
        "call",
        "iex",
        "invoke-expression",
        "start",
        "start-process",
    }:
        return True
    if command in DYNAMIC_SHELL_EXECUTORS:
        return True
    if not command.startswith(("$", "`")) or command in {"${", "$("}:
        return False
    if tokens[index].startswith("${{") or (
        len(tokens) > index + 1 and tokens[index + 1].casefold() in SHELL_TEST_OPERATORS
    ):
        return False
    if len(tokens) > 1:
        return True
    return re.fullmatch(r"\$[A-Z_][A-Z0-9_]*", tokens[index]) is None


def network_client_is_mutation(tokens: list[str]) -> bool:
    """Reject state-changing calls through known direct HTTP clients."""
    if not tokens or executable_basename(tokens[0]) not in UNSAFE_NETWORK_EXECUTORS:
        return False
    methods: list[str | None] = []
    has_body = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        normalized = token.casefold()
        if token == "-X" or normalized in NETWORK_METHOD_OPTIONS:
            if index + 1 >= len(tokens):
                methods.append(None)
            else:
                methods.append(tokens[index + 1])
                index += 1
        elif any(
            normalized.startswith(f"{option}=") for option in NETWORK_METHOD_OPTIONS
        ):
            methods.append(token.partition("=")[2])
        elif token.startswith("-X") and token != "-X":
            methods.append(token[2:].lstrip("="))
        elif token == "-G" or normalized == "--get":
            methods.append("GET")
        elif token == "-I" or normalized == "--head":
            methods.append("HEAD")
        elif (
            normalized in NETWORK_BODY_OPTIONS
            or any(
                normalized.startswith(option)
                for option in (
                    "--data=",
                    "--data-",
                    "--form=",
                    "--post-data=",
                    "--post-file=",
                    "--upload-file=",
                )
            )
            or token == "-d"
            or token == "-F"
            or token == "-T"
            or token.startswith(("-d", "-F", "-T"))
        ):
            has_body = True
        index += 1
    if any(
        method is None or method.strip().upper() not in GITHUB_API_READ_ONLY_METHODS
        for method in methods
    ):
        return True
    return has_body and not methods


def github_api_is_mutation(tokens: list[str], position: int) -> bool:
    """Return whether one ``gh api`` invocation can issue a state-changing request."""
    api_tokens = tokens[position:]
    methods: list[str | None] = []
    has_body = False
    index = 2
    while index < len(api_tokens):
        token = api_tokens[index]
        if token in GITHUB_API_METHOD_OPTIONS:
            if index + 1 >= len(api_tokens):
                methods.append(None)
            else:
                methods.append(api_tokens[index + 1])
                index += 1
        elif token.startswith("--method="):
            methods.append(token.partition("=")[2])
        elif token.startswith("-X") and token != "-X":
            methods.append(token[2:].lstrip("="))
        elif (
            token in GITHUB_API_BODY_OPTIONS
            or any(
                token.startswith(f"{option}=")
                for option in {"--field", "--input", "--raw-field"}
            )
            or (token.startswith("-f") and token != "-f")
            or (token.startswith("-F") and token != "-F")
        ):
            has_body = True
        index += 1
    if any(
        method is None or method.strip().upper() not in GITHUB_API_READ_ONLY_METHODS
        for method in methods
    ):
        return True
    return has_body and not methods


def issue_subcommand_positions(tokens: list[str]) -> tuple[int, ...] | None:
    """Return literal ``gh issue`` positions, including the global ``--repo`` form."""
    if not tokens:
        return ()
    if not is_github_cli_executable(tokens[0]):
        return (
            None
            if any(
                is_github_cli_executable(tokens[index]) and tokens[index + 1] == "issue"
                for index in range(1, len(tokens) - 1)
            )
            else ()
        )
    issue_position = 1
    if issue_position < len(tokens) and tokens[issue_position] in {"--repo", "-R"}:
        issue_position += 2
    elif issue_position < len(tokens) and (
        tokens[issue_position].startswith("--repo=")
        or (tokens[issue_position].startswith("-R") and tokens[issue_position] != "-R")
    ):
        issue_position += 1
    adjacent_positions = [
        index
        for index in range(len(tokens) - 1)
        if is_github_cli_executable(tokens[index]) and tokens[index + 1] == "issue"
    ]
    if any(index != 0 for index in adjacent_positions):
        return None
    if issue_position < len(tokens) and tokens[issue_position] == "issue":
        return (issue_position,)
    if "issue" in tokens[issue_position + 1 :]:
        return None
    return ()


def shell_logical_lines(command: str) -> Iterator[str]:
    """Join shell continuation lines without merging separate commands."""
    lines = executable_shell_lines(command)
    index = 0
    while index < len(lines):
        parts = [lines[index].rstrip()]
        cursor = index
        while parts[-1].endswith("\\") and cursor + 1 < len(lines):
            parts[-1] = parts[-1][:-1].rstrip()
            cursor += 1
            parts.append(lines[cursor])
        yield " ".join(parts)
        index = cursor + 1


def shell_command_segments(command: str) -> list[list[str]] | None:
    """Tokenize shell commands and split them at control operators."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = []
    current: list[str] = []
    for token in [*tokens, ";"]:
        if token and all(character in SHELL_CONTROL_CHARACTERS for character in token):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    return segments


def shell_command_prefix(segment: list[str]) -> list[str]:
    """Remove shell grammar words that can precede a command body."""
    normalized = list(segment)
    while normalized and normalized[0] in SHELL_COMMAND_PREFIXES:
        normalized.pop(0)
    return normalized


def issue_mutation_command_blocks(command: str) -> list[tuple[str, list[str]]]:
    """Return parsed ``gh issue`` mutation command blocks from shell text."""
    blocks: list[tuple[str, list[str]]] = []
    for logical_line in shell_logical_lines(command):
        segments = shell_command_segments(logical_line)
        if segments is None:
            return [(INVALID_ISSUE_MUTATION, [])]
        for segment in segments:
            tokens = shell_command_prefix(segment)
            if has_embedded_command(tokens) or has_dynamic_shell_executor(tokens):
                return [(INVALID_ISSUE_MUTATION, [])]
            if network_client_is_mutation(tokens):
                return [(INVALID_ISSUE_MUTATION, [])]
            api_positions = [
                index
                for index in range(len(tokens) - 1)
                if is_github_cli_executable(tokens[index])
                and tokens[index + 1] == "api"
            ]
            if any(
                github_api_is_mutation(tokens, position) for position in api_positions
            ):
                return [(INVALID_ISSUE_MUTATION, [])]
            issue_positions = issue_subcommand_positions(tokens)
            if issue_positions is None:
                return [(INVALID_ISSUE_MUTATION, [])]
            if not issue_positions:
                continue
            issue_position = issue_positions[0]
            if len(tokens) <= issue_position + 1:
                return [(INVALID_ISSUE_MUTATION, [])]
            subcommand = tokens[issue_position + 1]
            if subcommand in FRESHNESS_REMINDER_READ_ONLY_SUBCOMMANDS:
                continue
            if subcommand not in FRESHNESS_REMINDER_MUTATION_SUBCOMMANDS:
                return [(INVALID_ISSUE_MUTATION, [])]
            blocks.append((subcommand, tokens))
    return blocks


def has_issue_body_file_reconciliation(command: str) -> bool:
    """Require a body-backed issue reconciliation with bound mutations."""
    return has_issue_body_file_reconciliation_for_files(command)


def has_issue_body_file_reconciliation_for_files(
    command: str, expected_body_files: set[str] | None = None
) -> bool:
    """Require bound issue mutations to use one of the checker report files."""
    blocks = issue_mutation_command_blocks(command)
    return bool(
        any(
            subcommand in FRESHNESS_REMINDER_BODY_SUBCOMMANDS
            and option_has_one_value(
                tokens, FRESHNESS_REMINDER_BODY_FILE, expected_body_files
            )
            for subcommand, tokens in blocks
        )
        and all(
            subcommand in FRESHNESS_REMINDER_ALLOWED_MUTATION_SUBCOMMANDS
            and option_has_one_value(tokens, FRESHNESS_REMINDER_REPOSITORY_OPTION)
            and (
                subcommand == "close"
                or option_has_one_value(
                    tokens, FRESHNESS_REMINDER_BODY_FILE, expected_body_files
                )
            )
            and (
                subcommand != "create"
                or option_has_one_value(tokens, FRESHNESS_REMINDER_TITLE_OPTION)
            )
            for subcommand, tokens in blocks
        )
    )


def option_has_one_value(
    tokens: list[str], option: str, expected_values: set[str] | None = None
) -> bool:
    """Return whether one option has exactly one optionally expected value."""
    values = option_values(tokens, option)
    return bool(
        values
        and len(values) == 1
        and (expected_values is None or values[0] in expected_values)
    )


def freshness_audit_markdown_outputs(command: str) -> set[str] | None:
    """Return checker Markdown outputs, or ``None`` for an ambiguous invocation."""
    outputs: set[str] = set()
    saw_audit = False
    for logical_line in shell_logical_lines(command):
        segments = shell_command_segments(logical_line)
        if segments is None:
            return None
        for segment in segments:
            tokens = shell_command_prefix(segment)
            if has_embedded_command(tokens) or has_dynamic_shell_executor(tokens):
                return None
            audit_positions = [
                index
                for index in range(len(tokens) - 1)
                if tokens[index : index + 2] == ["python", "scripts/audit_freshness.py"]
            ]
            if not audit_positions:
                continue
            if audit_positions[0] != 0 or len(audit_positions) != 1:
                return None
            saw_audit = True
            values = {
                option: option_values(tokens, option)
                for option in FRESHNESS_AUDIT_REQUIRED_OPTIONS
            }
            if any(value is None or len(value) != 1 for value in values.values()):
                return None
            json_output = values["--json-output"]
            markdown_output = values["--markdown-output"]
            assert json_output is not None and markdown_output is not None
            if json_output[0] == markdown_output[0]:
                return None
            outputs.add(markdown_output[0])
    return outputs if saw_audit else set()


def has_freshness_audit_invocation(command: str) -> bool:
    """Require the freshness checker and all of its report output options."""
    outputs = freshness_audit_markdown_outputs(command)
    return outputs is not None and bool(outputs)


def has_repository_scoped_concurrency(document: dict[str, Any]) -> bool:
    """Require reminder runs to serialize shared repository Issue state."""
    concurrency = document.get("concurrency")
    return (
        isinstance(concurrency, dict)
        and concurrency.get("group") == FRESHNESS_REMINDER_CONCURRENCY_GROUP
        and concurrency.get("cancel-in-progress") == "false"
    )


def has_least_privileged_freshness_permissions(document: dict[str, Any]) -> bool:
    """Allow only read-only contents and Issue-write permissions for reminders."""
    if document.get("permissions") != {"contents": "read", "issues": "write"}:
        return False
    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict):
        return False
    for job in jobs.values():
        if not isinstance(job, dict):
            return False
        if "permissions" not in job:
            continue
        permissions = job["permissions"]
        if not isinstance(permissions, dict):
            return False
        if any(
            scope not in {"contents", "issues"}
            or value not in {"none", "read", "write"}
            or value == "write"
            and scope != "issues"
            for scope, value in permissions.items()
        ):
            return False
    return True


def workflow_uses_values(value: Any) -> Iterator[Any]:
    """Yield every parsed workflow ``uses`` value, including nested mappings."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "uses":
                yield child
            yield from workflow_uses_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from workflow_uses_values(child)


def validate_container_references(document: dict[str, Any], source: Path) -> None:
    """Reject external container tags before workflow assets are installed."""
    for reference in workflow_uses_values(document):
        if not isinstance(reference, str) or not reference.startswith("docker://"):
            continue
        if CONTAINER_REFERENCE_PATTERN.fullmatch(reference) is None:
            raise InspectionError(
                f"Workflow {source} external container reference must use a full "
                f"sha256 digest: {reference}"
            )


def local_reusable_workflow_names(document: dict[str, Any], source: Path) -> list[str]:
    """Return local reusable workflow inputs that must be preflighted together."""
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict)
    names: list[str] = []
    for job_name, job in jobs.items():
        assert isinstance(job, dict)
        call = job.get("uses")
        if call is None or not isinstance(call, str) or not call.startswith("./"):
            continue
        relative = call[2:]
        path = PurePosixPath(relative)
        if (
            not relative
            or "\\" in relative
            or "\x00" in relative
            or path.as_posix() != relative
            or path.parts[:2] != (".github", "workflows")
            or len(path.parts) != 3
            or path.suffix.casefold() not in {".yml", ".yaml"}
        ):
            raise InspectionError(
                f"Workflow {source} job {job_name!r} has an unsafe local reusable-workflow reference."
            )
        names.append(path.name)
    return names


def requires_issue_write(text: str, source: Path) -> bool:
    """Inspect YAML permission fields without treating comments or scripts as policy."""
    document = workflow_document(text, source)
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict)
    for scope in [document, *jobs.values()]:
        if permissions_grant_issue_write(scope):
            return True
    return False


def permissions_grant_issue_write(scope: dict[str, Any]) -> bool:
    """Return whether one workflow or job permission scope grants Issues write."""
    permissions = scope.get("permissions", {})
    return permissions == "write-all" or (
        isinstance(permissions, dict) and permissions.get("issues") == "write"
    )


def job_effective_issue_write(document: dict[str, Any], job: dict[str, Any]) -> bool:
    """Resolve the Issues permission inherited by one job from workflow scope."""
    if "permissions" in job:
        return permissions_grant_issue_write(job)
    return permissions_grant_issue_write(document)


def requires_pull_request_write_tokens(text: str, source: Path) -> bool:
    """Detect pull-request workflows whose write scopes need an explicit check."""
    document = workflow_document(text, source)
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict)
    triggers = document.get("on")
    if isinstance(triggers, str):
        pull_request_trigger = triggers == "pull_request"
    elif isinstance(triggers, list):
        pull_request_trigger = "pull_request" in triggers
    elif isinstance(triggers, dict):
        pull_request_trigger = "pull_request" in triggers
    else:
        pull_request_trigger = False
    if not pull_request_trigger:
        return False
    for scope in [document, *jobs.values()]:
        permissions = scope.get("permissions", {})
        if permissions == "write-all" or (
            isinstance(permissions, dict) and "write" in permissions.values()
        ):
            return True
    return False


def is_code_scanning_gate(text: str, source: Path) -> bool:
    """Identify a gate that requires the shipped allowlist freshness reminder."""
    return any(
        CODE_SCANNING_GATE_COMMAND in command
        for command in workflow_run_commands(workflow_document(text, source))
    )


def is_freshness_reminder_workflow(text: str, source: Path) -> bool:
    """Identify the scheduled reminder that audits code-scanning exception dates."""
    if source.name.casefold() != "freshness.yml":
        return False
    document = workflow_document(text, source)
    triggers = document.get("on")
    if (
        not isinstance(triggers, dict)
        or set(triggers) != {"schedule", "workflow_dispatch"}
        or not isinstance(triggers.get("schedule"), list)
        or not triggers["schedule"]
        or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("cron"), str)
            or not entry["cron"].strip()
            for entry in triggers["schedule"]
        )
        or not has_repository_scoped_concurrency(document)
        or not has_least_privileged_freshness_permissions(document)
        or not requires_issue_write(text, source)
    ):
        return False
    jobs = document.get("jobs", {})
    assert isinstance(jobs, dict)
    reconciliation_jobs = 0
    mutation_jobs = 0
    for job in jobs.values():
        assert isinstance(job, dict)
        steps = job.get("steps", [])
        job_commands = (
            [
                step["run"]
                for step in steps
                if isinstance(step, dict) and isinstance(step.get("run"), str)
            ]
            if isinstance(steps, list)
            else []
        )
        job_text = "\n".join(job_commands)
        blocks = issue_mutation_command_blocks(job_text)
        if blocks:
            mutation_jobs += 1
            if not job_effective_issue_write(document, job):
                return False
        markdown_outputs = freshness_audit_markdown_outputs(job_text)
        lines = [
            line for command in job_commands for line in executable_shell_lines(command)
        ]
        if (
            markdown_outputs
            and any(FRESHNESS_REMINDER_MARKER in line for line in lines)
            and has_issue_body_file_reconciliation_for_files(job_text, markdown_outputs)
            and job.get("name") == FRESHNESS_REMINDER_JOB_NAME
            and job.get("timeout-minutes") == FRESHNESS_REMINDER_TIMEOUT_MINUTES
        ):
            reconciliation_jobs += 1
    return reconciliation_jobs == 1 and mutation_jobs == 1


def validate_code_scanning_allowlist(path: Path) -> None:
    """Require a bounded schema-v3 allowlist companion before gate installation."""
    try:
        metadata = path.lstat()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
            or metadata.st_size > MAX_CODE_SCANNING_ALLOWLIST_BYTES
        ):
            raise OSError("not a regular allowlist file")
        with path.open("rb") as stream:
            raw = stream.read(MAX_CODE_SCANNING_ALLOWLIST_BYTES + 1)
        if len(raw) > MAX_CODE_SCANNING_ALLOWLIST_BYTES:
            raise OSError("allowlist exceeds the byte safety cap")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_json_object)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise InspectionError(
            f"Code-scanning allowlist input is missing or unsafe: {path}"
        ) from exc
    if (
        not isinstance(document, dict)
        or document.get("schema-version") != CODE_SCANNING_ALLOWLIST_SCHEMA_VERSION
        or not isinstance(document.get("allowlist"), list)
    ):
        raise InspectionError(
            "Code-scanning allowlist input must use schema-version 3 and an allowlist array."
        )
    entries = document["allowlist"]
    assert isinstance(entries, list)
    if len(entries) > MAX_CODE_SCANNING_ALLOWLIST_ENTRIES:
        raise InspectionError(
            "Code-scanning allowlist exceeds the "
            f"{MAX_CODE_SCANNING_ALLOWLIST_ENTRIES}-entry limit."
        )

    required_fields = {
        "number",
        "tool",
        "rule",
        "path",
        "reason",
        "reviewed-on",
        "review-period-days",
    }
    seen_numbers: set[int] = set()
    for index, entry in enumerate(entries, start=1):
        location = f"Code-scanning allowlist entry {index}"
        if not isinstance(entry, dict) or set(entry) != required_fields:
            raise InspectionError(
                f"{location} must use the exact selector and review-period fields."
            )

        number = entry["number"]
        if type(number) is not int or number < 1:
            raise InspectionError(f"{location} number must be a positive integer.")
        if number in seen_numbers:
            raise InspectionError(
                f"{location} repeats alert number {number}; numbers must be unique."
            )

        for field in ("tool", "rule", "reason"):
            value = entry[field]
            if not isinstance(value, str) or not value.strip():
                raise InspectionError(f"{location} {field} must be a non-empty string.")

        path_value = entry["path"]
        if path_value is not None:
            if not isinstance(path_value, str) or not path_value.strip():
                raise InspectionError(
                    f"{location} path must be null or a non-empty canonical POSIX path."
                )
            path_value_as_posix = PurePosixPath(path_value)
            if (
                not path_value_as_posix.parts
                or path_value_as_posix.is_absolute()
                or ".." in path_value_as_posix.parts
                or "\\" in path_value
                or any(
                    PureWindowsPath(part).drive for part in path_value_as_posix.parts
                )
                or path_value_as_posix.as_posix() != path_value
            ):
                raise InspectionError(f"{location} path must be canonical POSIX.")

        reviewed_on = entry["reviewed-on"]
        if not isinstance(reviewed_on, str) or not reviewed_on.strip():
            raise InspectionError(
                f"{location} reviewed-on must be a non-empty ISO date."
            )
        try:
            reviewed_date = date.fromisoformat(reviewed_on)
        except ValueError as error:
            raise InspectionError(
                f"{location} reviewed-on must use ISO date format."
            ) from error
        if reviewed_date > datetime.now(timezone.utc).date():
            raise InspectionError(f"{location} reviewed-on cannot be in the future.")

        review_period_days = entry["review-period-days"]
        if (
            not isinstance(review_period_days, int)
            or isinstance(review_period_days, bool)
            or not 1 <= review_period_days <= MAX_CODE_SCANNING_ALLOWLIST_REVIEW_DAYS
        ):
            raise InspectionError(
                f"{location} review period must be between 1 and "
                f"{MAX_CODE_SCANNING_ALLOWLIST_REVIEW_DAYS} days."
            )
        seen_numbers.add(number)


def require_boolean(document: dict[str, Any], field: str) -> bool:
    """Read a GitHub boolean without accepting truthy replacement values."""
    value = document.get(field)
    if not isinstance(value, bool):
        raise InspectionError(f"Repository response has an invalid {field!r} value.")
    return value


def actions_permissions(document: Any) -> tuple[bool, str]:
    """Validate the repository-scoped GitHub Actions policy response."""
    if not isinstance(document, dict):
        raise InspectionError("GitHub Actions permissions response is invalid.")
    enabled = document.get("enabled")
    allowed_actions = document.get("allowed_actions")
    if not isinstance(enabled, bool):
        raise InspectionError(
            "GitHub Actions permissions response has an invalid 'enabled' value."
        )
    if allowed_actions not in ALLOWED_ACTION_POLICIES:
        raise InspectionError(
            "GitHub Actions permissions response has an invalid 'allowed_actions' value."
        )
    return enabled, allowed_actions


def selected_actions_policy(document: Any) -> dict[str, bool | list[str]]:
    """Validate the effective selected-actions response from GitHub."""
    if not isinstance(document, dict):
        raise InspectionError("Selected Actions policy response is invalid.")
    github_owned = document.get("github_owned_allowed")
    verified = document.get("verified_allowed")
    patterns = document.get("patterns_allowed")
    if not isinstance(github_owned, bool) or not isinstance(verified, bool):
        raise InspectionError("Selected Actions policy has invalid boolean settings.")
    if not isinstance(patterns, list) or any(
        not isinstance(pattern, str)
        or not pattern
        or any(character in pattern for character in "\r\n\x00")
        for pattern in patterns
    ):
        raise InspectionError("Selected Actions policy has invalid allowed patterns.")
    return {
        "github_owned_allowed": github_owned,
        "verified_allowed": verified,
        "patterns_allowed": patterns,
    }


def workflow_capabilities(
    workflows: list[Path],
) -> tuple[list[str], list[str], list[str], list[str], bool]:
    """Read external references and workflow capabilities that require confirmation."""
    if not workflows:
        raise InspectionError(
            "Selected Actions policy requires at least one --workflow input."
        )
    references: set[str] = set()
    issue_workflows: set[str] = set()
    pull_request_write_workflows: set[str] = set()
    code_scanning_gate_workflows: set[str] = set()
    freshness_reminder_supplied = False
    workflow_inputs = {workflow.name: workflow for workflow in workflows}
    if len(workflow_inputs) != len(workflows):
        raise InspectionError(
            "Workflow inputs must have unique filenames for local reusable-workflow resolution."
        )
    for workflow in workflows:
        try:
            metadata = workflow.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or workflow.is_symlink()
                or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
                or workflow.suffix.casefold() not in {".yml", ".yaml"}
                or metadata.st_size > MAX_WORKFLOW_BYTES
            ):
                raise OSError("not a regular workflow file")
            with workflow.open("rb") as stream:
                raw = stream.read(MAX_WORKFLOW_BYTES + 1)
            if len(raw) > MAX_WORKFLOW_BYTES:
                raise OSError("workflow exceeds the byte safety cap")
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise InspectionError(
                f"Workflow input is missing or unsafe: {workflow}"
            ) from exc
        document = workflow_document(text, workflow)
        validate_container_references(document, workflow)
        for local_name in local_reusable_workflow_names(document, workflow):
            if local_name not in workflow_inputs:
                raise InspectionError(
                    f"Workflow {workflow} calls local reusable workflow {local_name!r}; "
                    "pass it as another --workflow input."
                )
        try:
            # This rejects aliases, unpinned actions, and non-action `uses:` forms
            # before the exact references below are compared with GitHub's policy.
            sync_action_pins.auditable_action_repositories(workflow, text)
        except ValueError as exc:
            raise InspectionError(str(exc)) from exc
        if requires_issue_write(text, workflow):
            issue_workflows.add(workflow.name)
        if requires_pull_request_write_tokens(text, workflow):
            pull_request_write_workflows.add(workflow.name)
        if is_code_scanning_gate(text, workflow):
            code_scanning_gate_workflows.add(workflow.name)
        if is_freshness_reminder_workflow(text, workflow):
            freshness_reminder_supplied = True
        direct_references = {
            sync_action_pins.normalized_uses_reference(match)
            for match in sync_action_pins.workflow_uses_matches(text)
            if not (
                match.groupdict().get("quote") == ""
                and match.group("reference").startswith("*")
            )
        }
        direct_references.update(
            sync_action_pins.normalized_uses_reference(match)
            for match in sync_action_pins.anchored_action_reference_matches(text)
        )
        references.update(
            reference
            for reference in direct_references
            if not reference.startswith(("./", "docker://"))
        )
    return (
        sorted(references, key=str.casefold),
        sorted(issue_workflows, key=str.casefold),
        sorted(pull_request_write_workflows, key=str.casefold),
        sorted(code_scanning_gate_workflows, key=str.casefold),
        freshness_reminder_supplied,
    )


def selected_policy_allows(
    reference: str, policy: dict[str, bool | list[str]], *, public_repository: bool
) -> bool:
    """Apply GitHub's selected-actions allowlist to one exact action reference."""
    action = reference.rsplit("@", 1)[0]
    # GitHub documents this setting for actions in the `actions` organization.
    if policy["github_owned_allowed"] is True and action.casefold().startswith(
        "actions/"
    ):
        return True
    # GitHub does not expose a stable REST attribute proving a Marketplace creator
    # is verified. Require a literal configured pattern instead of guessing.
    # GitHub documents patterns as applicable to public repositories. Do not infer
    # Enterprise Cloud eligibility for a private or internal repository.
    if not public_repository:
        return False
    patterns = policy["patterns_allowed"]
    assert isinstance(patterns, list)
    return any(
        fnmatch.fnmatchcase(reference.casefold(), pattern.casefold())
        for pattern in patterns
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Inspect one repository before copying a dependent workflow asset."""
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError(
            "Workflow-installation preflight supports GitHub.com only."
        )
    owner, repo = split_repository(args.repository)
    client = GitHubClient(args.hostname)
    repository = client.json(f"repos/{owner}/{repo}")
    if not isinstance(repository, dict):
        raise InspectionError("Repository response is invalid.")
    full_name = repository.get("full_name")
    if (
        not isinstance(full_name, str)
        or full_name.casefold() != args.repository.casefold()
    ):
        raise InspectionError("GitHub returned a different repository than requested.")
    if require_boolean(repository, "archived"):
        raise InspectionError("Archived repositories cannot install workflow assets.")
    if require_boolean(repository, "disabled"):
        raise InspectionError("Disabled repositories cannot install workflow assets.")
    issues_enabled = require_boolean(repository, "has_issues")
    visibility = repository.get("visibility")

    (
        external_action_references,
        detected_issue_workflows,
        pull_request_write_workflows,
        code_scanning_gate_workflows,
        freshness_reminder_supplied,
    ) = (
        workflow_capabilities(args.workflow)
        if args.workflow
        else ([], [], [], [], False)
    )
    requires_external_actions = args.require_external_actions or bool(
        external_action_references
    )
    requires_issues = args.require_issues or bool(detected_issue_workflows)

    actions_enabled, allowed_actions = actions_permissions(
        client.json(f"repos/{owner}/{repo}/actions/permissions")
    )
    selected_policy: dict[str, bool | list[str]] | None = None
    unapproved_action_references: list[str] = []
    if actions_enabled and requires_external_actions and allowed_actions == "selected":
        selected_policy = selected_actions_policy(
            client.json(f"repos/{owner}/{repo}/actions/permissions/selected-actions")
        )
        if visibility not in {"public", "private", "internal"}:
            raise InspectionError(
                "Repository response has an invalid visibility value."
            )
        if not external_action_references:
            (
                external_action_references,
                detected_issue_workflows,
                pull_request_write_workflows,
                code_scanning_gate_workflows,
                freshness_reminder_supplied,
            ) = workflow_capabilities(args.workflow)
        unapproved_action_references = [
            reference
            for reference in external_action_references
            if not selected_policy_allows(
                reference,
                selected_policy,
                public_repository=visibility == "public",
            )
        ]
    external_actions_verified = actions_enabled and (
        not requires_external_actions
        or allowed_actions == "all"
        or (
            allowed_actions == "selected"
            and not unapproved_action_references
            and bool(external_action_references)
        )
    )
    issue_workflows_eligible = not requires_issues or issues_enabled
    pull_request_write_tokens_confirmed = (
        not pull_request_write_workflows or args.confirm_pull_request_write_tokens
    )
    code_scanning_allowlist_supplied = args.code_scanning_allowlist is not None
    if code_scanning_gate_workflows and args.code_scanning_allowlist is not None:
        validate_code_scanning_allowlist(args.code_scanning_allowlist)
    code_scanning_companions_verified = not code_scanning_gate_workflows or (
        freshness_reminder_supplied and code_scanning_allowlist_supplied
    )
    if not actions_enabled:
        decision = "enable-github-actions-before-installing-workflows"
    elif requires_external_actions and allowed_actions == "local_only":
        decision = "allow-external-actions-before-installing-workflows"
    elif (
        requires_external_actions
        and allowed_actions == "selected"
        and not external_actions_verified
    ):
        decision = "allow-selected-actions-before-installing-workflows"
    elif not issue_workflows_eligible:
        decision = "enable-issues-before-installing-issue-workflows"
    elif not pull_request_write_tokens_confirmed:
        decision = "confirm-pull-request-write-tokens-before-installing-workflows"
    elif not code_scanning_companions_verified:
        decision = "include-code-scanning-companions-before-installing-workflows"
    else:
        decision = "may-install-workflow-assets"
    return {
        "inspection_complete": True,
        "decision": decision,
        "repository": args.repository,
        "visibility": visibility,
        "github_actions_enabled": actions_enabled,
        "allowed_actions": allowed_actions,
        "requires_external_actions": requires_external_actions,
        "external_actions_verified": external_actions_verified,
        "external_action_references": external_action_references,
        "unapproved_action_references": unapproved_action_references,
        "selected_actions_policy": selected_policy,
        "issues_enabled": issues_enabled,
        "requires_issues": requires_issues,
        "detected_issue_workflows": detected_issue_workflows,
        "issue_workflows_eligible": issue_workflows_eligible,
        "requires_pull_request_write_tokens": bool(pull_request_write_workflows),
        "pull_request_write_workflows": pull_request_write_workflows,
        "pull_request_write_tokens_confirmed": pull_request_write_tokens_confirmed,
        "requires_code_scanning_companions": bool(code_scanning_gate_workflows),
        "code_scanning_gate_workflows": code_scanning_gate_workflows,
        "freshness_reminder_supplied": freshness_reminder_supplied,
        "code_scanning_allowlist_supplied": code_scanning_allowlist_supplied,
        "code_scanning_companions_verified": code_scanning_companions_verified,
        "github_api_requests": client.request_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--require-external-actions", action="store_true")
    parser.add_argument("--require-issues", action="store_true")
    parser.add_argument("--confirm-pull-request-write-tokens", action="store_true")
    parser.add_argument("--workflow", type=Path, action="append", default=[])
    parser.add_argument("--code-scanning-allowlist", type=Path)
    return parser.parse_args()


def main() -> int:
    try:
        result = run(parse_args())
    except (InspectionError, OSError, UnicodeError) as exc:
        print(
            json.dumps(
                {
                    "inspection_complete": False,
                    "decision": "inconclusive",
                    "error": str(exc),
                }
            )
        )
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
