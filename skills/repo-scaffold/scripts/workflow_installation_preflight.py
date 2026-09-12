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
from zoneinfo import available_timezones

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
CODE_SCANNING_ALLOWLIST_KEYS = frozenset({"schema-version", "allowlist"})
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
FRESHNESS_REMINDER_REPOSITORY = "github.com/$GITHUB_REPOSITORY"
FRESHNESS_REMINDER_API_ENDPOINT = (
    "repos/$GITHUB_REPOSITORY/issues?state=open&per_page=100"
)
FRESHNESS_REMINDER_API_JQ = (
    ".[] | select(.pull_request == null) | "
    f'select((.body // "") | contains("<!-- {FRESHNESS_REMINDER_MARKER} -->")) | .number'
)
FRESHNESS_REMINDER_API_ALLOWED_ARGUMENTS = frozenset(
    {
        "--hostname",
        "github.com",
        "--hostname=github.com",
        "--paginate",
        FRESHNESS_REMINDER_API_ENDPOINT,
        "--jq",
        FRESHNESS_REMINDER_API_JQ,
        f"--jq={FRESHNESS_REMINDER_API_JQ}",
        "--method",
        "--method=GET",
        "-X",
        "-XGET",
        "-X=GET",
        "GET",
    }
)
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
        "source",
        "sh",
        ".",
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
FRESHNESS_AUDIT_REPOSITORY_ROOT = "."
FRESHNESS_AUDIT_JSON_OUTPUT = "$RUNNER_TEMP/freshness.json"
FRESHNESS_AUDIT_MARKDOWN_OUTPUT = "$RUNNER_TEMP/freshness.md"
FRESHNESS_SUMMARY_COMMAND = (
    "cat",
    FRESHNESS_AUDIT_MARKDOWN_OUTPUT,
    ">>",
    "$GITHUB_STEP_SUMMARY",
)
FRESHNESS_AUDIT_TRACKER_REGISTRY = ".github/freshness-trackers.json"
FRESHNESS_ALLOWED_ACTION_REPOSITORIES = frozenset(
    {"actions/checkout", "actions/setup-python"}
)
FRESHNESS_ACTION_REFERENCE_PATTERN = re.compile(
    r"(?:actions/checkout|actions/setup-python)@[0-9a-f]{40}\Z", re.IGNORECASE
)
FRESHNESS_REVIEWED_ACTION_REFERENCES = {
    "actions/checkout": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
}
FRESHNESS_ALLOWED_ACTION_INPUTS: dict[str, dict[str, object]] = {
    "actions/checkout": {"persist-credentials": "false"},
    "actions/setup-python": {"python-version": "3.x"},
}
CRON_STEP = r"(?:/[1-9][0-9]*)?"
CRON_MINUTE_VALUE = r"(?:[0-9]|[1-5][0-9])"
CRON_HOUR_VALUE = r"(?:[0-9]|1[0-9]|2[0-3])"
CRON_DAY_VALUE = r"(?:[1-9]|[12][0-9]|3[01])"
CRON_MONTH_VALUE = r"(?:[1-9]|1[0-2]|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
CRON_WEEKDAY_VALUE = r"(?:[0-6]|SUN|MON|TUE|WED|THU|FRI|SAT)"
FRESHNESS_WORKFLOW_DISPATCH_INPUT_KEYS = frozenset(
    {"default", "description", "options", "required", "type"}
)
FRESHNESS_WORKFLOW_DISPATCH_INPUT_TYPES = frozenset(
    {"boolean", "choice", "environment", "number", "string"}
)
FRESHNESS_WORKFLOW_DISPATCH_MAX_INPUTS = 25


def cron_field_pattern(value_pattern: str) -> str:
    """Build one POSIX cron field pattern with lists, ranges, and steps."""
    atom = rf"(?:\*|{value_pattern}(?:-{value_pattern})?){CRON_STEP}"
    return rf"{atom}(?:,{atom})*"


FRESHNESS_CRON_PATTERN = re.compile(
    rf"{cron_field_pattern(CRON_MINUTE_VALUE)} +"
    rf"{cron_field_pattern(CRON_HOUR_VALUE)} +"
    rf"{cron_field_pattern(CRON_DAY_VALUE)} +"
    rf"{cron_field_pattern(CRON_MONTH_VALUE)} +"
    rf"{cron_field_pattern(CRON_WEEKDAY_VALUE)}\Z",
    re.IGNORECASE,
)
FRESHNESS_SCHEDULE_ENTRY_KEYS = frozenset({"cron", "timezone"})
FRESHNESS_IANA_TIMEZONES = frozenset({"Etc/UTC"}) | frozenset(available_timezones())


def cron_value_number(value: str, named_values: dict[str, int]) -> int:
    """Normalize a numeric or named cron value for range validation."""
    normalized = value.upper()
    if normalized in named_values:
        return named_values[normalized]
    return int(value)


def cron_ranges_are_ordered(expression: str) -> bool:
    """Reject cron ranges whose start is beyond their end."""
    named_values_by_field: tuple[dict[str, int], ...] = (
        {},
        {},
        {},
        {
            "JAN": 1,
            "FEB": 2,
            "MAR": 3,
            "APR": 4,
            "MAY": 5,
            "JUN": 6,
            "JUL": 7,
            "AUG": 8,
            "SEP": 9,
            "OCT": 10,
            "NOV": 11,
            "DEC": 12,
        },
        {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6},
    )
    for field, named_values in zip(expression.split(), named_values_by_field):
        for atom in field.split(","):
            range_part = atom.split("/", 1)[0]
            if "-" not in range_part:
                continue
            start, end = range_part.split("-", 1)
            if cron_value_number(start, named_values) > cron_value_number(
                end, named_values
            ):
                return False
    return True


FRESHNESS_MARKER_ASSIGNMENTS = frozenset(
    {
        f"marker={FRESHNESS_REMINDER_MARKER}",
        f"marker=<!-- {FRESHNESS_REMINDER_MARKER} -->",
    }
)
FRESHNESS_TITLE_ASSIGNMENT = "title=Repository freshness update required"
FRESHNESS_ISSUE_NUMBERS_INITIALIZATION = "issue_numbers="
FRESHNESS_DUPLICATE_ISSUE_GUARD = (
    "if (( ${#issue_numbers[@]} > 1 )); then",
    "printf 'Found multiple open freshness reminder issues.\\n' >&2",
    "exit 1",
    "fi",
)
FRESHNESS_ALLOWED_SHELL_IF_LINES = frozenset(
    {
        f'if [[ ! -f "{FRESHNESS_AUDIT_MARKDOWN_OUTPUT}" ]]; then',
        'if [[ -n "$issue_numbers_output" ]]; then',
        FRESHNESS_DUPLICATE_ISSUE_GUARD[0],
        "if [[ \"$CHECKER_EXIT\" == '0' ]]; then",
        "if (( ${#issue_numbers[@]} == 1 )); then",
        "if [[ \"$CHECKER_EXIT\" != '0' ]]; then",
    }
)
FRESHNESS_ALLOWED_SHELL_COMMANDS = frozenset(
    {"${", "cat", "exit", "fi", "gh", "grep", "if", "mapfile", "printf", "set"}
)
FRESHNESS_ALLOWED_ISSUE_OPTIONS: dict[str, frozenset[str]] = {
    "close": frozenset({"--comment", "--repo"}),
    "edit": frozenset({"--body-file", "--repo", "--title"}),
    "create": frozenset({"--body-file", "--repo", "--title"}),
}
FRESHNESS_ALLOWED_SHELL_ASSIGNMENT_NAMES = frozenset(
    {"checker_exit", "issue_numbers", "issue_numbers_output", "marker", "title"}
)
SHELL_DIRECTORY_CHANGE_COMMANDS = frozenset(
    {"cd", "chdir", "popd", "pushd", "set-location", "sl", "sls"}
)
CONTAINER_REFERENCE_PATTERN = re.compile(
    r"(?:docker://)?[^\s@]+@sha256:[0-9a-f]{64}\Z", re.IGNORECASE
)


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
        if token == "--":
            break
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
SHELL_UNSAFE_PHASE_OPERATORS = frozenset({"&", "|", "|&", "&&", "||"})
SHELL_COMMAND_PREFIXES = frozenset({"then", "do", "else"})
FRESHNESS_PROTECTED_ENVIRONMENT_VARIABLES = frozenset(
    {
        "CHECKER_EXIT",
        "GITHUB_STEP_SUMMARY",
        "GITHUB_STATE",
        "PATH",
        "BASH_ENV",
        "ENV",
        "GITHUB_ENV",
        "GITHUB_PATH",
        "GITHUB_OUTPUT",
        "GIT_SSH_COMMAND",
        "GH_CONFIG_DIR",
        "HOME",
        "LD_PRELOAD",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "RUNNER_TEMP",
    }
)
FRESHNESS_SHELL_PROTECTED_ENVIRONMENT_VARIABLES = (
    FRESHNESS_PROTECTED_ENVIRONMENT_VARIABLES | {"GITHUB_TOKEN", "GH_TOKEN"}
)
FRESHNESS_SECRET_REFERENCE_PATTERN = re.compile(
    r"\$(?:\{(?:GITHUB_TOKEN|GH_TOKEN)(?:[^A-Za-z0-9_}]|})|"
    r"(?:GITHUB_TOKEN|GH_TOKEN)(?![A-Za-z0-9_]))"
)
FRESHNESS_INDIRECT_PARAMETER_PATTERN = re.compile(r"\$\{!")
FRESHNESS_ALLOWED_SHELL_EXPANSIONS = (
    "$RUNNER_TEMP",
    "$?",
    "$checker_exit",
    "$GITHUB_OUTPUT",
    "$GITHUB_STEP_SUMMARY",
    "$GITHUB_REPOSITORY",
    "$issue_numbers_output",
    "${#issue_numbers[@]}",
    "$CHECKER_EXIT",
    "${issue_numbers[0]}",
    "$marker",
    "$title",
    "${title}",
)
FRESHNESS_JOB_EXECUTION_CONTROLS = frozenset(
    {
        "cache-mode",
        "concurrency",
        "continue-on-error",
        "environment",
        "if",
        "needs",
        "snapshot",
        "strategy",
    }
)
FRESHNESS_STEP_EXECUTION_CONTROLS = frozenset(
    {
        "background",
        "cancel",
        "continue-on-error",
        "if",
        "parallel",
        "timeout-minutes",
        "wait",
        "wait-all",
    }
)
FRESHNESS_ALLOWED_ENVIRONMENT_VARIABLES = frozenset(
    {"GITHUB_TOKEN", "GH_TOKEN", "CHECKER_EXIT"}
)
FRESHNESS_TOKEN_EXPRESSION = "${{ github.token }}"
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


def freshness_issue_options_are_safe(
    tokens: list[str], issue_position: int, subcommand: str
) -> bool:
    """Reject unreviewed options on freshness Issue mutations."""
    allowed_options = FRESHNESS_ALLOWED_ISSUE_OPTIONS.get(subcommand)
    if allowed_options is None:
        return False
    arguments = tokens[issue_position + 2 :]
    if subcommand in {"close", "edit"}:
        if not arguments:
            return False
        arguments = arguments[1:]
    index = 0
    while index < len(arguments):
        token = arguments[index]
        option, separator, value = token.partition("=")
        if not token.startswith("-") or option not in allowed_options:
            return False
        if separator:
            if not value.strip():
                return False
            index += 1
            continue
        if index + 1 >= len(arguments) or arguments[index + 1].startswith("-"):
            return False
        index += 2
    title_values = option_values(tokens, "--title")
    if title_values is None or subcommand == "create" and len(title_values) != 1:
        return False
    if subcommand == "edit" and len(title_values) > 1:
        return False
    if any(
        value not in {"$title", "${title}"}
        and any(character in value for character in "$`")
        for value in title_values
    ):
        return False
    return True


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


def shell_command_executable_index(tokens: list[str]) -> int | None:
    """Return the executable position after valid shell assignments."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if "=" not in token or not token.partition("=")[0].isidentifier():
            break
        index += 1
    return index if index < len(tokens) else None


def has_directory_change_command(tokens: list[str]) -> bool:
    """Reject commands that can move the checker away from the repository root."""
    return any(
        executable_basename(token) in SHELL_DIRECTORY_CHANGE_COMMANDS
        for token in tokens
    )


def has_repository_root_working_directory(document: dict[str, Any]) -> bool:
    """Require every freshness run step to inherit the repository root directory."""
    scopes: list[Any] = [document]
    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict) or not jobs:
        return False
    scopes.extend(jobs.values())
    for scope in scopes:
        if not isinstance(scope, dict):
            return False
        defaults = scope.get("defaults")
        if defaults is not None:
            if not isinstance(defaults, dict):
                return False
            run_defaults = defaults.get("run")
            if run_defaults is not None:
                if not isinstance(run_defaults, dict):
                    return False
                if (
                    "working-directory" in run_defaults
                    and run_defaults["working-directory"] != "."
                ):
                    return False
        steps = scope.get("steps", [])
        if not isinstance(steps, list):
            return False
        if any(
            isinstance(step, dict)
            and "working-directory" in step
            and step["working-directory"] != "."
            for step in steps
        ):
            return False
    return True


def freshness_job_execution_is_unconditional(job: object) -> bool:
    """Reject job or step controls that can silently skip reconciliation."""
    if not isinstance(job, dict) or any(
        control in job for control in FRESHNESS_JOB_EXECUTION_CONTROLS
    ):
        return False
    steps = job.get("steps")
    return isinstance(steps, list) and all(
        isinstance(step, dict)
        and not FRESHNESS_STEP_EXECUTION_CONTROLS.intersection(step)
        for step in steps
    )


def freshness_cron_syntax_is_valid(value: object) -> bool:
    """Require a five-field POSIX cron expression for scheduled reminders."""
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    return bool(
        FRESHNESS_CRON_PATTERN.fullmatch(normalized)
    ) and cron_ranges_are_ordered(normalized)


def freshness_schedule_entry_is_valid(value: object) -> bool:
    """Require a valid cron and optional IANA timezone without extra keys."""
    if not isinstance(value, dict) or set(value) - FRESHNESS_SCHEDULE_ENTRY_KEYS:
        return False
    if not freshness_cron_syntax_is_valid(value.get("cron")):
        return False
    if "timezone" not in value:
        return True
    timezone = value["timezone"]
    return isinstance(timezone, str) and timezone in FRESHNESS_IANA_TIMEZONES


def freshness_workflow_dispatch_is_valid(value: object) -> bool:
    """Require an empty or schema-valid manual trigger configuration."""
    if value in ("", None, "null"):
        return True
    if not isinstance(value, dict) or set(value) - {"inputs"}:
        return False
    inputs = value.get("inputs", {})
    if (
        not isinstance(inputs, dict)
        or len(inputs) > FRESHNESS_WORKFLOW_DISPATCH_MAX_INPUTS
    ):
        return False
    for name, definition in inputs.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or any(character in name for character in "\r\n\x00")
            or not isinstance(definition, dict)
            or any(
                key not in FRESHNESS_WORKFLOW_DISPATCH_INPUT_KEYS for key in definition
            )
        ):
            return False
        required = definition.get("required", "false")
        if required not in {"true", "false"}:
            return False
        input_type = definition.get("type", "string")
        if input_type not in FRESHNESS_WORKFLOW_DISPATCH_INPUT_TYPES:
            return False
        for key in ("description", "default"):
            if key in definition and not isinstance(definition[key], str):
                return False
        options = definition.get("options")
        if input_type == "choice":
            if (
                not isinstance(options, list)
                or not options
                or any(
                    not isinstance(option, str) or not option.strip()
                    for option in options
                )
            ):
                return False
        elif options is not None:
            return False
    return True


def freshness_execution_context_is_bash(workflow: object, job: object) -> bool:
    """Require the inspected freshness commands to execute as Bash on Ubuntu."""
    if not isinstance(workflow, dict) or not isinstance(job, dict):
        return False
    if job.get("runs-on") != "ubuntu-latest":
        return False
    for scope in (workflow, job):
        if "container" in scope or "services" in scope:
            return False
        defaults = scope.get("defaults")
        if defaults is None:
            continue
        if not isinstance(defaults, dict):
            return False
        run_defaults = defaults.get("run")
        if run_defaults is None:
            continue
        if not isinstance(run_defaults, dict):
            return False
        if run_defaults.get("shell") not in (None, "bash"):
            return False
    steps = job.get("steps")
    return (
        isinstance(steps, list)
        and freshness_action_steps_are_safe(steps)
        and all(
            isinstance(step, dict) and step.get("shell") in (None, "bash")
            for step in steps
        )
    )


def freshness_action_steps_are_safe(steps: object) -> bool:
    """Require each reviewed preparation action once before any run step."""
    if not isinstance(steps, list):
        return False
    prepared: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            return False
        uses = step.get("uses")
        if uses is None:
            if "uses" in step or prepared != FRESHNESS_ALLOWED_ACTION_REPOSITORIES:
                return False
            continue
        if "run" in step:
            return False
        if not isinstance(uses, str) or uses.count("@") != 1:
            return False
        repository = uses.partition("@")[0].casefold()
        if (
            repository not in FRESHNESS_ALLOWED_ACTION_REPOSITORIES
            or repository in prepared
            or FRESHNESS_ACTION_REFERENCE_PATTERN.fullmatch(uses) is None
            or uses.casefold()
            != FRESHNESS_REVIEWED_ACTION_REFERENCES[repository].casefold()
        ):
            return False
        if step.get("with") != FRESHNESS_ALLOWED_ACTION_INPUTS[repository]:
            return False
        prepared.add(repository)
    return prepared == FRESHNESS_ALLOWED_ACTION_REPOSITORIES


def freshness_authentication_bindings_are_safe(workflow: object, job: object) -> bool:
    """Require GitHub CLI authentication to use the workflow token in place."""
    if not isinstance(workflow, dict) or not isinstance(job, dict):
        return False
    for scope in (workflow, job):
        environment = scope.get("env")
        if environment is not None and (
            not isinstance(environment, dict)
            or any(
                variable not in FRESHNESS_ALLOWED_ENVIRONMENT_VARIABLES
                for variable in environment
            )
            or any(variable in environment for variable in ("GITHUB_TOKEN", "GH_TOKEN"))
        ):
            return False
    steps = job.get("steps")
    if not isinstance(steps, list):
        return False
    audit_steps = [
        step for step in steps if isinstance(step, dict) and step.get("id") == "audit"
    ]
    binding_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("env"), dict)
        and step["env"].get("CHECKER_EXIT") == "${{ steps.audit.outputs.checker_exit }}"
    ]
    if (
        len(audit_steps) != 1
        or len(binding_steps) != 1
        or binding_steps[0] is audit_steps[0]
    ):
        return False
    token_steps: dict[str, list[dict[str, Any]]] = {
        "GITHUB_TOKEN": [],
        "GH_TOKEN": [],
    }
    for step in steps:
        if not isinstance(step, dict):
            return False
        environment = step.get("env")
        if environment is not None and (
            not isinstance(environment, dict)
            or any(
                variable not in FRESHNESS_ALLOWED_ENVIRONMENT_VARIABLES
                for variable in environment
            )
        ):
            return False
        if not isinstance(environment, dict):
            continue
        for variable in token_steps:
            if variable not in environment:
                continue
            if environment[variable] != FRESHNESS_TOKEN_EXPRESSION:
                return False
            token_steps[variable].append(step)
    return (
        token_steps["GITHUB_TOKEN"] == audit_steps
        and token_steps["GH_TOKEN"] == binding_steps
    )


def freshness_shell_if_block_ranges(
    segments: list[list[str]],
) -> dict[int, int] | None:
    """Return matching shell ``if``/``fi`` segment ranges."""
    stack: list[int] = []
    ranges: dict[int, int] = {}
    for index, segment in enumerate(segments):
        command_tokens = shell_command_prefix(segment)
        if command_tokens and command_tokens[0] == "if":
            stack.append(index)
        elif command_tokens == ["fi"]:
            if not stack:
                return None
            ranges[stack.pop()] = index
    return None if stack else ranges


def freshness_checker_result_binding_is_safe(workflow: object, job: object) -> bool:
    """Require CHECKER_EXIT to come from the audit step output."""
    if not isinstance(workflow, dict) or not isinstance(job, dict):
        return False
    for scope in (workflow, job):
        environment = scope.get("env")
        if environment is not None and (
            not isinstance(environment, dict)
            or any(
                variable in environment
                for variable in FRESHNESS_PROTECTED_ENVIRONMENT_VARIABLES
            )
        ):
            return False
    steps = job.get("steps")
    if not isinstance(steps, list):
        return False
    audit_steps: list[dict[str, Any]] = []
    binding_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            return False
        if step.get("id") == "audit":
            audit_steps.append(step)
        environment = step.get("env")
        if environment is not None and not isinstance(environment, dict):
            return False
        if isinstance(environment, dict):
            protected_variables = {
                variable
                for variable in FRESHNESS_PROTECTED_ENVIRONMENT_VARIABLES
                if variable in environment
            }
            if not protected_variables:
                continue
            if protected_variables != {"CHECKER_EXIT"}:
                return False
            if environment["CHECKER_EXIT"] != "${{ steps.audit.outputs.checker_exit }}":
                return False
            binding_steps.append(step)
    if (
        len(audit_steps) != 1
        or len(binding_steps) != 1
        or binding_steps[0] is audit_steps[0]
    ):
        return False
    audit_run = audit_steps[0].get("run")
    binding_run = binding_steps[0].get("run")
    return (
        isinstance(audit_run, str)
        and freshness_checker_result_output_is_safe(audit_run)
        and isinstance(binding_run, str)
        and freshness_reconciliation_shell_options_are_safe(binding_run)
        and freshness_checker_result_controls_reconciliation(binding_run)
        and freshness_api_result_controls_issue_selection(binding_run)
    )


def freshness_checker_result_output_is_safe(command: str) -> bool:
    """Require the audit status output to derive from the checker process."""
    segments: list[list[str]] = []
    for logical_line in shell_logical_lines(command):
        line_segments = shell_command_segments(logical_line)
        if line_segments is None:
            return False
        segments.extend(line_segments)
    markdown_outputs = freshness_audit_markdown_outputs(command)
    if markdown_outputs is None or len(markdown_outputs) != 1:
        return False
    if markdown_outputs != {FRESHNESS_AUDIT_MARKDOWN_OUTPUT}:
        return False
    markdown_output = next(iter(markdown_outputs))
    checker_output_command = (
        "printf",
        "checker_exit=%s\\n",
        "$checker_exit",
        ">>",
        "$GITHUB_OUTPUT",
    )
    audit_indices: list[int] = []
    disable_errexit_indices: list[int] = []
    capture_indices: list[int] = []
    enable_errexit_indices: list[int] = []
    fallback_indices: list[int] = []
    output_indices: list[int] = []
    missing_report_indices: list[int] = []
    missing_report_paths: list[str] = []
    fallback_write_indices: list[int] = []
    for index, segment in enumerate(segments):
        command_tokens = shell_command_prefix(segment)
        if command_tokens and command_tokens[0] == "printf":
            is_fallback = (
                len(command_tokens) >= 5
                and command_tokens[:2] == ["printf", "%s\\n"]
                and command_tokens.count(">") == 1
                and command_tokens[-2:] == [">", markdown_output]
                and any(
                    FRESHNESS_REMINDER_MARKER in token for token in command_tokens[2:-2]
                )
                and all(
                    "$" not in token and "`" not in token and "%n" not in token
                    for token in command_tokens[2:-2]
                )
            )
            if tuple(command_tokens) != checker_output_command and not is_fallback:
                return False
        if segment[:2] == ["python", "scripts/audit_freshness.py"]:
            audit_indices.append(index)
            if option_values(segment, "--json-output") != (
                FRESHNESS_AUDIT_JSON_OUTPUT,
            ):
                return False
        if command_tokens == ["set", "+e"]:
            disable_errexit_indices.append(index)
        elif command_tokens == ["checker_exit=$?"]:
            capture_indices.append(index)
        elif command_tokens == ["set", "-e"]:
            enable_errexit_indices.append(index)
        elif command_tokens == ["checker_exit=2"]:
            fallback_indices.append(index)
        elif command_tokens == [
            "printf",
            "checker_exit=%s\\n",
            "$checker_exit",
            ">>",
            "$GITHUB_OUTPUT",
        ]:
            output_indices.append(index)
        elif "$GITHUB_OUTPUT" in command_tokens or any(
            "checker_exit=" in token for token in command_tokens
        ):
            return False
        elif freshness_variable_is_reassigned(command_tokens, "checker_exit"):
            return False
        elif command_tokens and command_tokens[0] == "set":
            return False
        if (
            len(command_tokens) == 6
            and command_tokens[:4] == ["if", "[[", "!", "-f"]
            and command_tokens[-1] == "]]"
        ):
            missing_report_indices.append(index)
            missing_report_paths.append(command_tokens[4])
        redirect_indices = [
            cursor for cursor, token in enumerate(command_tokens) if token == ">"
        ]
        if any(
            cursor + 1 < len(command_tokens)
            and command_tokens[cursor + 1] == markdown_output
            and command_tokens[0] == "printf"
            and any(FRESHNESS_REMINDER_MARKER in token for token in command_tokens)
            for cursor in redirect_indices
        ):
            fallback_write_indices.append(index)
    if (
        len(audit_indices) != 1
        or len(disable_errexit_indices) != 1
        or len(capture_indices) != 1
        or len(enable_errexit_indices) != 1
        or len(fallback_indices) != 1
        or len(output_indices) != 1
    ):
        return False
    audit_index = audit_indices[0]
    disable_errexit_index = disable_errexit_indices[0]
    capture_index = capture_indices[0]
    enable_errexit_index = enable_errexit_indices[0]
    output_index = output_indices[0]
    if (
        disable_errexit_index >= audit_index
        or capture_index != audit_index + 1
        or enable_errexit_index != capture_index + 1
        or capture_index >= output_index
    ):
        return False
    if len(missing_report_indices) != 1:
        return False
    if missing_report_paths != [markdown_output] or len(fallback_write_indices) != 1:
        return False
    fallback_index = fallback_indices[0]
    missing_report_index = missing_report_indices[0]
    fallback_write_index = fallback_write_indices[0]
    closing_indices = [
        index
        for index in range(missing_report_index + 1, len(segments))
        if shell_command_prefix(segments[index]) == ["fi"]
    ]
    return (
        missing_report_index < fallback_index < output_index
        and capture_index < missing_report_index
        and bool(closing_indices)
        and missing_report_index < fallback_write_index < fallback_index
        and fallback_index < closing_indices[0] < output_index
    )


def freshness_shell_expansions_are_safe(command: str) -> bool:
    """Allow only the reviewed shell expansions used by the reminder contract."""
    command_substitution = "issue_numbers_output=$("
    if (
        command.count("$(") != command.count(command_substitution)
        or command.count(command_substitution) > 1
    ):
        return False
    remaining = command.replace(command_substitution, "")
    for expansion in FRESHNESS_ALLOWED_SHELL_EXPANSIONS:
        boundary = "" if expansion.endswith(("}", "?")) else r"(?![A-Za-z0-9_])"
        remaining = re.sub(re.escape(expansion) + boundary, "", remaining)
    return "$" not in remaining


def freshness_shell_definitions_are_safe(command: str) -> bool:
    """Reject shell definitions that can shadow the checked executables."""
    if (
        "${{" in command
        or FRESHNESS_SECRET_REFERENCE_PATTERN.search(command)
        or FRESHNESS_INDIRECT_PARAMETER_PATTERN.search(command)
        or not freshness_shell_expansions_are_safe(command)
    ):
        return False
    segments: list[list[str]] = []
    for logical_line in shell_logical_lines(command):
        line_segments = shell_command_segments(logical_line)
        if line_segments is None:
            return False
        segments.extend(line_segments)
    if any(
        freshness_variable_is_reassigned(segment, variable)
        for segment in segments
        for variable in FRESHNESS_SHELL_PROTECTED_ENVIRONMENT_VARIABLES
    ):
        return False
    for segment in segments:
        command_tokens = shell_command_prefix(segment)
        if not command_tokens:
            continue
        executable = executable_basename(command_tokens[0])
        if executable in {
            "alias",
            "doskey",
            "new-alias",
            "set-alias",
            "unalias",
            "hash",
        }:
            return False
        if executable in {"declare", "export", "typeset"} and any(
            token == "-f" or token.startswith("-f") for token in command_tokens[1:]
        ):
            return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
    except ValueError:
        return False
    for index, token in enumerate(tokens):
        if token.casefold() == "function":
            return False
        if token.isidentifier() and (
            (
                index + 2 < len(tokens)
                and tokens[index + 1] == "()"
                and tokens[index + 2] == "{"
            )
            or (
                index + 3 < len(tokens)
                and tokens[index + 1 : index + 3] == ["(", ")"]
                and tokens[index + 3] == "{"
            )
        ):
            return False
    for segment in segments:
        command_tokens = shell_command_prefix(segment)
        if not command_tokens:
            continue
        executable_index = shell_command_executable_index(command_tokens)
        assignment_end = (
            executable_index if executable_index is not None else len(command_tokens)
        )
        if any(
            token.partition("=")[0] not in FRESHNESS_ALLOWED_SHELL_ASSIGNMENT_NAMES
            for token in command_tokens[:assignment_end]
        ):
            return False
        if executable_index is None:
            continue
        else:
            executable_token = command_tokens[executable_index]
            executable = executable_basename(executable_token)
            if (
                "/" in executable_token
                or "\\" in executable_token
                or executable not in FRESHNESS_ALLOWED_SHELL_COMMANDS
            ) and not (
                executable == "python"
                and command_tokens[executable_index : executable_index + 2]
                == FRESHNESS_AUDIT_COMMAND.split()
            ):
                return False
            command_body = command_tokens[executable_index:]
            if executable == "gh":
                if len(command_body) < 2 or command_body[1] not in {"api", "issue"}:
                    return False
                if command_body[1] == "issue" and (
                    len(command_body) < 3
                    or command_body[2] not in {"close", "edit", "create"}
                ):
                    return False
                if command_body[1] == "issue" and not freshness_issue_options_are_safe(
                    command_body, 1, command_body[2]
                ):
                    return False
            elif executable == "grep" and command_body != [
                "grep",
                "-Fq",
                "$marker",
                FRESHNESS_AUDIT_MARKDOWN_OUTPUT,
            ]:
                return False
            elif executable == "mapfile" and command_body != [
                "mapfile",
                "-t",
                "issue_numbers",
                "<<<",
                "$issue_numbers_output",
            ]:
                return False
            elif executable == "exit" and command_body not in (
                ["exit", "0"],
                ["exit", "1"],
            ):
                return False
            elif executable == "printf":
                format_index = (
                    2 if len(command_body) > 1 and command_body[1] == "--" else 1
                )
                if (
                    len(command_body) <= format_index
                    or "$" in command_body[format_index]
                    or "`" in command_body[format_index]
                    or "%n" in command_body[format_index]
                    or any(
                        token == "-v" or token.startswith("-v")
                        for token in command_body[1:]
                    )
                ):
                    return False
    return True


def freshness_shell_control_flow_is_safe(command: str) -> bool:
    """Require the canonical guards around freshness mutations."""
    lines = [
        line.strip()
        for line in command.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if (
        command.count("$(") != 1
        or "`" in command
        or command.count("issue_numbers_output=$(") != 1
    ):
        return False
    if any(
        re.match(r"^(?:for|while|until|case|select|coproc|trap|function)\b", line)
        or line in {"do", "done", "esac"}
        or (re.search(r"(?:^|;)\s*[{}()]", line) and line != ")")
        or re.search(r";\s*then\s*[{}()]", line)
        or re.search(r";\s*(?:then\s+)?if\b", line)
        or re.search(r";\s*(?:then\s+)?(?:fi|else)\b", line)
        or re.search(r"[<>]\s*\(", line)
        for line in lines
    ):
        return False
    if_lines = [line for line in lines if line.startswith("if ")]
    if any(line not in FRESHNESS_ALLOWED_SHELL_IF_LINES for line in if_lines):
        return False
    required_if_lines = (
        FRESHNESS_DUPLICATE_ISSUE_GUARD[0],
        "if [[ \"$CHECKER_EXIT\" == '0' ]]; then",
        "if [[ \"$CHECKER_EXIT\" != '0' ]]; then",
    )
    if any(if_lines.count(line) != 1 for line in required_if_lines):
        return False
    duplicate_guard_count = sum(
        tuple(lines[index : index + len(FRESHNESS_DUPLICATE_ISSUE_GUARD)])
        == FRESHNESS_DUPLICATE_ISSUE_GUARD
        for index in range(len(lines) - len(FRESHNESS_DUPLICATE_ISSUE_GUARD) + 1)
    )
    if duplicate_guard_count != 1:
        return False
    clean_index = if_lines.index("if [[ \"$CHECKER_EXIT\" == '0' ]]; then")
    stale_index = if_lines.index("if [[ \"$CHECKER_EXIT\" != '0' ]]; then")
    duplicate_index = if_lines.index(FRESHNESS_DUPLICATE_ISSUE_GUARD[0])
    if not duplicate_index < clean_index < stale_index:
        return False
    nonempty_indices = [
        index
        for index, line in enumerate(if_lines)
        if line == 'if [[ -n "$issue_numbers_output" ]]; then'
    ]
    if len(nonempty_indices) > 1 or any(
        index >= clean_index for index in nonempty_indices
    ):
        return False
    array_guard_positions = [
        index
        for index, line in enumerate(if_lines)
        if line == "if (( ${#issue_numbers[@]} == 1 )); then"
    ]
    if len(array_guard_positions) != 2:
        return False
    line_ranges: dict[int, int] = {}
    stack: list[int] = []
    for index, line in enumerate(lines):
        if line.startswith("if "):
            stack.append(index)
        elif line == "fi":
            if not stack:
                return False
            line_ranges[stack.pop()] = index
    if stack:
        return False
    array_line_positions = [
        index
        for index, line in enumerate(lines)
        if line == "if (( ${#issue_numbers[@]} == 1 )); then"
    ]
    close_line_positions = [
        index for index, line in enumerate(lines) if line.startswith("gh issue close ")
    ]
    edit_line_positions = [
        index for index, line in enumerate(lines) if line.startswith("gh issue edit ")
    ]
    create_line_positions = [
        index for index, line in enumerate(lines) if line.startswith("gh issue create ")
    ]
    else_line_positions = [index for index, line in enumerate(lines) if line == "else"]
    if not (
        len(array_line_positions) == 2
        and len(close_line_positions) == 1
        and len(edit_line_positions) == 1
        and len(create_line_positions) == 1
        and len(else_line_positions) == 1
    ):
        return False
    first_array_end = line_ranges[array_line_positions[0]]
    second_array_end = line_ranges[array_line_positions[1]]
    close_line = close_line_positions[0]
    edit_line = edit_line_positions[0]
    create_line = create_line_positions[0]
    else_line = else_line_positions[0]
    return (
        array_line_positions[0] < close_line < first_array_end
        and array_line_positions[1]
        < edit_line
        < else_line
        < create_line
        < second_array_end
    )


def freshness_marker_check_is_safe(command: str) -> bool:
    """Require the report marker before status branching and Issue mutations."""
    segments: list[list[str]] = []
    for logical_line in shell_logical_lines(command):
        line_segments = shell_command_segments(logical_line)
        if line_segments is None:
            return False
        segments.extend(line_segments)
    marker_assignment_indices = [
        index
        for index, segment in enumerate(segments)
        if freshness_variable_is_reassigned(segment, "marker")
    ]
    marker_indices = [
        index
        for index, segment in enumerate(segments)
        if len(segment) == 1 and segment[0] in FRESHNESS_MARKER_ASSIGNMENTS
    ]
    grep_indices = [
        index
        for index, segment in enumerate(segments)
        if segment == ["grep", "-Fq", "$marker", FRESHNESS_AUDIT_MARKDOWN_OUTPUT]
    ]
    clean_indices = [
        index
        for index, segment in enumerate(segments)
        if shell_command_prefix(segment)
        == ["if", "[[", "$CHECKER_EXIT", "==", "0", "]]"]
    ]
    return (
        len(marker_assignment_indices) == 1
        and len(marker_indices) == 1
        and len(grep_indices) == 1
        and len(clean_indices) == 1
        and marker_indices[0] < grep_indices[0] < clean_indices[0]
    )


def freshness_reconciliation_shell_options_are_safe(command: str) -> bool:
    """Require reconciliation to keep errexit, nounset, and pipefail enabled."""
    segments: list[list[str]] = []
    for logical_line in shell_logical_lines(command):
        line_segments = shell_command_segments(logical_line)
        if line_segments is None:
            return False
        segments.extend(line_segments)
    if any(
        token in {">", ">>", ">|", "&>", "&>>"}
        for segment in segments
        for token in segment
    ):
        return False
    commands = [shell_command_prefix(segment) for segment in segments]
    set_commands = [command for command in commands if command and command[0] == "set"]
    return (
        bool(commands)
        and commands[0] == ["set", "-euo", "pipefail"]
        and set_commands == [["set", "-euo", "pipefail"]]
    )


def freshness_summary_output_is_safe(command: str) -> bool:
    """Require the job summary to publish only the checked Markdown report."""
    segments: list[list[str]] = []
    for logical_line in shell_logical_lines(command):
        line_segments = shell_command_segments(logical_line)
        if line_segments is None:
            return False
        segments.extend(line_segments)
    cat_commands = [
        command_tokens
        for segment in segments
        if (command_tokens := shell_command_prefix(segment))
        and command_tokens[0] == "cat"
    ]
    return cat_commands == [list(FRESHNESS_SUMMARY_COMMAND)]


def freshness_checker_result_controls_reconciliation(command: str) -> bool:
    """Require checker status to select clean versus stale Issue mutations."""
    if not freshness_shell_control_flow_is_safe(command):
        return False
    if not freshness_marker_check_is_safe(command):
        return False
    segments: list[list[str]] = []
    for logical_line in shell_logical_lines(command):
        line_segments = shell_command_segments(logical_line)
        if line_segments is None:
            return False
        segments.extend(line_segments)
    if_ranges = freshness_shell_if_block_ranges(segments)
    if if_ranges is None:
        return False
    title_assignments = [
        shell_command_prefix(segment)
        for segment in segments
        if freshness_variable_is_reassigned(shell_command_prefix(segment), "title")
    ]
    title_assignment_indices = [
        index
        for index, segment in enumerate(segments)
        if freshness_variable_is_reassigned(shell_command_prefix(segment), "title")
    ]
    title_references = [
        token
        for segment in segments
        for token in shell_command_prefix(segment)
        if freshness_variable_reference(token, "title")
    ]
    if title_assignments and title_assignments != [[FRESHNESS_TITLE_ASSIGNMENT]]:
        return False
    if title_references and not title_assignments:
        return False
    issue_numbers_initializations = [
        shell_command_prefix(segment)
        for segment in segments
        if shell_command_prefix(segment) == [FRESHNESS_ISSUE_NUMBERS_INITIALIZATION]
    ]
    if issue_numbers_initializations != [[FRESHNESS_ISSUE_NUMBERS_INITIALIZATION]]:
        return False
    issue_numbers_reassignments = [
        shell_command_prefix(segment)
        for segment in segments
        if freshness_variable_is_reassigned(
            shell_command_prefix(segment), "issue_numbers"
        )
        and shell_command_prefix(segment)
        != ["mapfile", "-t", "issue_numbers", "<<<", "$issue_numbers_output"]
    ]
    if issue_numbers_reassignments != [[FRESHNESS_ISSUE_NUMBERS_INITIALIZATION]]:
        return False
    clean_tests: list[int] = []
    failure_tests: list[int] = []
    clean_exits: list[int] = []
    failure_exits: list[int] = []
    close_mutations: list[int] = []
    edit_mutations: list[int] = []
    create_mutations: list[int] = []
    for index, segment in enumerate(segments):
        command_tokens = shell_command_prefix(segment)
        if command_tokens == ["if", "[[", "$CHECKER_EXIT", "==", "0", "]]"]:
            clean_tests.append(index)
        if command_tokens == ["if", "[[", "$CHECKER_EXIT", "!=", "0", "]]"]:
            failure_tests.append(index)
        if command_tokens == ["exit", "0"]:
            clean_exits.append(index)
        if command_tokens == ["exit", "1"]:
            failure_exits.append(index)
        issue_positions = issue_subcommand_positions(command_tokens)
        if issue_positions is None:
            return False
        for position in issue_positions:
            if position + 1 >= len(command_tokens):
                return False
            subcommand = command_tokens[position + 1]
            if subcommand in {"close", "edit"} and position + 2 >= len(command_tokens):
                return False
            if subcommand == "close":
                close_mutations.append(index)
            elif subcommand == "edit":
                edit_mutations.append(index)
            elif subcommand == "create":
                create_mutations.append(index)
            else:
                return False
    if not (
        len(clean_tests) == 1
        and len(failure_tests) == 1
        and len(clean_exits) == 1
        and len(close_mutations) == 1
        and len(edit_mutations) == 1
        and len(create_mutations) == 1
        # One failure exit guards duplicate issues; one propagates stale status.
        and len(failure_exits) == 2
    ):
        return False
    issue_numbers_initialization_indices = [
        index
        for index, segment in enumerate(segments)
        if shell_command_prefix(segment) == [FRESHNESS_ISSUE_NUMBERS_INITIALIZATION]
    ]
    mapfile_indices = [
        index
        for index, segment in enumerate(segments)
        if shell_command_prefix(segment)
        == ["mapfile", "-t", "issue_numbers", "<<<", "$issue_numbers_output"]
    ]
    if (
        len(issue_numbers_initialization_indices) != 1
        or len(mapfile_indices) != 1
        or issue_numbers_initialization_indices[0] >= mapfile_indices[0]
    ):
        return False
    clean_test = clean_tests[0]
    clean_exit = clean_exits[0]
    failure_test = failure_tests[0]
    clean_block_end = if_ranges[clean_test]
    failure_block_end = if_ranges[failure_test]
    stale_mutations = [*edit_mutations, *create_mutations]
    if title_assignments and title_assignment_indices[0] >= min(
        [*close_mutations, *edit_mutations, *create_mutations]
    ):
        return False
    failure_exit_after = [
        exit_index for exit_index in failure_exits if exit_index > failure_test
    ]
    return (
        clean_test
        < close_mutations[0]
        < clean_exit
        < clean_block_end
        < min(stale_mutations)
        and max(stale_mutations) < failure_test
        and bool(failure_exit_after)
        and failure_test < min(failure_exit_after) < failure_block_end
    )


def has_direct_freshness_jobs(document: dict[str, Any]) -> bool:
    """Require freshness jobs to expose their run commands for inspection."""
    jobs = document.get("jobs", {})
    return (
        isinstance(jobs, dict)
        and bool(jobs)
        and all(isinstance(job, dict) and "uses" not in job for job in jobs.values())
    )


def has_freshness_repository_context(document: dict[str, Any]) -> bool:
    """Reject workflow overrides of the runner's current repository variable."""
    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict) or not jobs:
        return False
    for scope in [document, *jobs.values()]:
        if not isinstance(scope, dict):
            return False
        environment = scope.get("env")
        if environment is not None:
            if not isinstance(environment, dict):
                return False
            if "GITHUB_REPOSITORY" in environment:
                return False
        steps = scope.get("steps", [])
        if not isinstance(steps, list):
            return False
        for step in steps:
            if not isinstance(step, dict):
                return False
            step_environment = step.get("env")
            if step_environment is not None:
                if not isinstance(step_environment, dict):
                    return False
                if "GITHUB_REPOSITORY" in step_environment:
                    return False
    return True


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
            if not freshness_issue_options_are_safe(tokens, issue_position, subcommand):
                return [(INVALID_ISSUE_MUTATION, [])]
            blocks.append((subcommand, tokens))
    return blocks


def has_issue_body_file_reconciliation(command: str) -> bool:
    """Require a body-backed issue reconciliation with bound mutations."""
    return has_issue_body_file_reconciliation_for_files(command)


def has_issue_body_file_reconciliation_for_files(
    command: str,
    expected_body_files: set[str] | None = None,
    expected_repository_values: set[str] | None = None,
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
            and option_has_one_value(
                tokens,
                FRESHNESS_REMINDER_REPOSITORY_OPTION,
                expected_repository_values,
            )
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


def has_freshness_repository_api_reads(
    command: str, *, require_lookup: bool = True
) -> bool:
    """Validate freshness API lookups and optionally require one lookup."""
    saw_api = False
    for logical_line in shell_logical_lines(command):
        segments = shell_command_segments(logical_line)
        if segments is None:
            return False
        for segment in segments:
            tokens = shell_command_prefix(segment)
            if any(
                token == "GITHUB_REPOSITORY"
                or token.partition("=")[0] == "GITHUB_REPOSITORY"
                for token in tokens
            ):
                return False
            github_positions = [
                index
                for index, token in enumerate(tokens)
                if is_github_cli_executable(token)
            ]
            if any(
                position + 1 < len(tokens)
                and tokens[position + 1].startswith("-")
                and "api" in tokens[position + 2 :]
                for position in github_positions
            ):
                return False
            api_positions = [
                index
                for index in range(len(tokens) - 1)
                if is_github_cli_executable(tokens[index])
                and tokens[index + 1] == "api"
            ]
            for position in api_positions:
                saw_api = True
                if has_embedded_command(tokens) or has_dynamic_shell_executor(tokens):
                    return False
                if github_api_is_mutation(tokens, position):
                    return False
                api_arguments = tokens[position + 2 :]
                if "--" in api_arguments:
                    return False
                if tuple(token for token in api_arguments if token == "--paginate") != (
                    "--paginate",
                ):
                    return False
                methods = [
                    api_arguments[index + 1]
                    for index, token in enumerate(api_arguments[:-1])
                    if token in GITHUB_API_METHOD_OPTIONS
                ]
                methods.extend(
                    token.partition("=")[2]
                    for token in api_arguments
                    if token.startswith("--method=")
                )
                methods.extend(
                    token[2:].lstrip("=")
                    for token in api_arguments
                    if token.startswith("-X") and token != "-X"
                )
                if len(methods) > 1 or any(
                    method.strip().upper() != "GET" for method in methods
                ):
                    return False
                hostname = option_values(api_arguments, "--hostname")
                if hostname != ("github.com",):
                    return False
                jq_values = option_values(api_arguments, "--jq")
                if jq_values is None or jq_values != (FRESHNESS_REMINDER_API_JQ,):
                    return False
                endpoints = [
                    token for token in api_arguments if token.startswith("repos/")
                ]
                if endpoints != [FRESHNESS_REMINDER_API_ENDPOINT]:
                    return False
                if any(
                    token not in FRESHNESS_REMINDER_API_ALLOWED_ARGUMENTS
                    for token in api_arguments
                ):
                    return False
                method_forms = sum(
                    token in {"--method", "-X"}
                    or token in {"--method=GET", "-XGET", "-X=GET"}
                    for token in api_arguments
                )
                expected_argument_count = (
                    1
                    + 1
                    + (2 if "--hostname" in api_arguments else 1)
                    + (2 if "--jq" in api_arguments else 1)
                    + (
                        0
                        if method_forms == 0
                        else 2
                        if "--method" in api_arguments or "-X" in api_arguments
                        else 1
                    )
                )
                if len(api_arguments) != expected_argument_count:
                    return False
    return saw_api or not require_lookup


def freshness_command_order_is_valid(command: str) -> bool:
    """Require the audit, lookup, and mutation phases to run in that order."""
    audit_positions: list[int] = []
    api_positions: list[int] = []
    mutation_positions: list[int] = []
    command_index = 0
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        if any(token in SHELL_UNSAFE_PHASE_OPERATORS for token in lexer):
            return False
    except ValueError:
        return False
    for logical_line in shell_logical_lines(command):
        segments = shell_command_segments(logical_line)
        if segments is None:
            return False
        for segment in segments:
            tokens = shell_command_prefix(segment)
            if tokens[:2] == FRESHNESS_AUDIT_COMMAND.split():
                audit_positions.append(command_index)
            api_commands = [
                index
                for index in range(len(tokens) - 1)
                if is_github_cli_executable(tokens[index])
                and tokens[index + 1] == "api"
            ]
            api_positions.extend(command_index for _ in api_commands)
            issue_positions = issue_subcommand_positions(tokens)
            if issue_positions is None:
                return False
            mutation_positions.extend(
                command_index
                for position in issue_positions
                if position + 1 < len(tokens)
                and tokens[position + 1] in FRESHNESS_REMINDER_MUTATION_SUBCOMMANDS
            )
            command_index += 1
    return bool(audit_positions and api_positions and mutation_positions) and (
        max(audit_positions) < min(api_positions) < min(mutation_positions)
        and max(api_positions) < min(mutation_positions)
    )


def freshness_api_result_assignments(
    command: str,
) -> tuple[list[str], list[tuple[str, int]]] | None:
    """Return shell tokens and assignments whose substitution invokes ``gh api``."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    assignments: list[tuple[str, int]] = []
    for index, token in enumerate(tokens[:-1]):
        if not token.endswith("=$"):
            continue
        variable = token[:-2]
        if not variable.isidentifier() or tokens[index + 1] != "(":
            continue
        depth = 1
        api_found = False
        closing_index: int | None = None
        for cursor in range(index + 2, len(tokens)):
            current = tokens[cursor]
            if current == "(":
                depth += 1
            elif current == ")":
                depth -= 1
                if depth == 0:
                    closing_index = cursor
                    break
            elif (
                current == "gh"
                and cursor + 1 < len(tokens)
                and tokens[cursor + 1] == "api"
            ):
                api_found = True
        if api_found and closing_index is not None:
            assignments.append((variable, closing_index))
    return tokens, assignments


def freshness_api_result_is_consumed(command: str) -> bool:
    """Require the freshness API result to be assigned and consumed later."""
    parsed = freshness_api_result_assignments(command)
    if parsed is None:
        return False
    tokens, assignments = parsed
    for variable, closing_index in assignments:
        plain_reference = f"${variable}"
        braced_reference = f"${{{variable}"
        for current in tokens[closing_index + 1 :]:
            if current.startswith(f"{variable}="):
                break
            if current == plain_reference:
                return True
            if current.startswith(braced_reference):
                suffix = current[len(braced_reference) :]
                if not suffix or suffix[0] in "}:#%/^,?+-=":
                    return True
            if current.startswith(plain_reference):
                suffix = current[len(plain_reference) :]
                if not suffix or not (suffix[0].isalnum() or suffix[0] == "_"):
                    return True
    return False


def freshness_variable_reference(token: str, variable: str) -> bool:
    """Return whether one shell token references the named variable."""
    plain_reference = f"${variable}"
    if token == plain_reference:
        return True
    braced_reference = f"${{{variable}"
    if token.startswith(braced_reference):
        suffix = token[len(braced_reference) :]
        return not suffix or suffix[0] in ":#%/^,?+-=[]}"
    if token.startswith(plain_reference):
        suffix = token[len(plain_reference) :]
        return not suffix or not (suffix[0].isalnum() or suffix[0] == "_")
    return False


def freshness_issue_argument_reference(token: str, variable: str) -> bool:
    """Allow only an unmodified lookup result as an Issue argument."""
    return token in {
        f"${variable}",
        f"${{{variable}}}",
        f"${{{variable}[0]}}",
    }


def freshness_variable_is_reassigned(tokens: list[str], variable: str) -> bool:
    """Return whether shell tokens overwrite or unset the named variable."""
    assignment_prefixes = (f"{variable}=", f"{variable}+=", f"{variable}[")
    if any(token.startswith(assignment_prefixes) for token in tokens):
        return True
    if any(
        re.search(rf"\$\{{{re.escape(variable)}(?:\[[^]]*\])?(?::)?=", token)
        for token in tokens
    ):
        return True
    if not tokens:
        return False
    executable = executable_basename(tokens[0])
    if executable in {
        "declare",
        "export",
        "local",
        "mapfile",
        "read",
        "readarray",
        "readonly",
        "typeset",
        "unset",
    }:
        return variable in tokens[1:]
    if executable != "printf":
        return False
    if any("%n" in token for token in tokens[1:]):
        return True
    return any(
        (token == "-v" and index + 1 < len(tokens) and tokens[index + 1] == variable)
        or (token.startswith("-v") and token[2:] == variable)
        for index, token in enumerate(tokens[1:], 1)
    )


def freshness_api_result_controls_issue_selection(command: str) -> bool:
    """Require lookup output to identify the Issue passed to a mutation."""
    parsed = freshness_api_result_assignments(command)
    if parsed is None:
        return False
    if not freshness_api_result_is_consumed(command):
        return False
    _, api_result_assignments = parsed
    segments: list[list[str]] = []
    for logical_line in shell_logical_lines(command):
        line_segments = shell_command_segments(logical_line)
        if line_segments is None:
            return False
        segments.extend(line_segments)
    result_variables = {variable for variable, _ in api_result_assignments}
    api_indices = [
        index
        for index, segment in enumerate(segments)
        if any(
            is_github_cli_executable(token)
            and index_token + 1 < len(segment)
            and segment[index_token + 1] == "api"
            for index_token, token in enumerate(segment)
        )
    ]
    collections: list[tuple[int, str]] = []
    for api_index in api_indices:
        for variable in result_variables:
            if not any(
                freshness_variable_is_reassigned(segment, variable)
                for segment in segments[api_index + 1 :]
            ):
                collections.append((api_index, variable))

    for collection_index, segment in enumerate(segments):
        command_tokens = shell_command_prefix(segment)
        if not command_tokens or command_tokens[0] not in {"mapfile", "readarray"}:
            continue
        redirect_indices = [
            index for index, token in enumerate(command_tokens) if token == "<<<"
        ]
        if len(redirect_indices) != 1:
            continue
        redirect_index = redirect_indices[0]
        if redirect_index < 2 or redirect_index + 1 >= len(command_tokens):
            continue
        target = command_tokens[redirect_index - 1]
        source = command_tokens[redirect_index + 1]
        if not target.isidentifier():
            continue
        source_variables = [
            variable
            for variable in result_variables
            if freshness_variable_reference(source, variable)
        ]
        if not source_variables:
            continue
        for api_index in api_indices:
            if api_index >= collection_index:
                continue
            if any(
                freshness_variable_is_reassigned(segment, variable)
                for variable in source_variables
                for segment in segments[api_index + 1 : collection_index]
            ):
                continue
            collections.append((collection_index, target))
            break

    if not collections:
        return False
    saw_bound_mutation = False
    for mutation_index, segment in enumerate(segments):
        command_tokens = shell_command_prefix(segment)
        issue_positions = issue_subcommand_positions(command_tokens)
        if issue_positions is None:
            return False
        for position in issue_positions:
            if position + 1 >= len(command_tokens):
                return False
            subcommand = command_tokens[position + 1]
            if subcommand == "create":
                continue
            if subcommand not in {"close", "edit"}:
                continue
            if position + 2 >= len(command_tokens):
                return False
            issue_argument = command_tokens[position + 2]
            mutation_bound = False
            for collection_index, variable in collections:
                if (
                    collection_index >= mutation_index
                    or not freshness_issue_argument_reference(issue_argument, variable)
                ):
                    continue
                if any(
                    freshness_variable_is_reassigned(segment, variable)
                    for segment in segments[collection_index + 1 : mutation_index]
                ):
                    continue
                mutation_bound = True
                break
            if not mutation_bound:
                return False
            saw_bound_mutation = True
    return saw_bound_mutation


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
            if (
                has_embedded_command(tokens)
                or has_dynamic_shell_executor(tokens)
                or has_directory_change_command(tokens)
            ):
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
            repository_root = values["--repository-root"]
            tracker_registry = option_values(tokens, "--tracker-registry")
            if (
                repository_root is None
                or repository_root[0] != FRESHNESS_AUDIT_REPOSITORY_ROOT
                or tracker_registry is None
                or len(tracker_registry) > 1
                or tracker_registry
                and tracker_registry[0] != FRESHNESS_AUDIT_TRACKER_REGISTRY
            ):
                return None
            expected_argument_count = 2
            for option in FRESHNESS_AUDIT_REQUIRED_OPTIONS:
                expected_argument_count += (
                    1 if any(token.startswith(f"{option}=") for token in tokens) else 2
                )
            if tracker_registry:
                expected_argument_count += (
                    1
                    if any(token.startswith("--tracker-registry=") for token in tokens)
                    else 2
                )
            if len(tokens) != expected_argument_count:
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


def workflow_uses_values(value: Any, _seen: set[int] | None = None) -> Iterator[Any]:
    """Yield each parsed workflow ``uses`` value once, including nested mappings."""
    seen = _seen if _seen is not None else set()
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "uses":
                yield child
            yield from workflow_uses_values(child, seen)
    elif isinstance(value, list):
        for child in value:
            yield from workflow_uses_values(child, seen)


def validate_container_references(document: dict[str, Any], source: Path) -> None:
    """Reject mutable action, job-container, and service-container images."""
    for reference in workflow_uses_values(document):
        if not isinstance(reference, str) or not reference.startswith("docker://"):
            continue
        if CONTAINER_REFERENCE_PATTERN.fullmatch(reference) is None:
            raise InspectionError(
                f"Workflow {source} external container reference must use a full "
                f"sha256 digest: {reference}"
            )
    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict):
        return
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        containers: list[tuple[str, object]] = []
        if "container" in job:
            containers.append((f"job {job_name!r} container", job["container"]))
        services = job.get("services")
        if services is not None:
            if not isinstance(services, dict):
                raise InspectionError(
                    f"Workflow {source} job {job_name!r} services must be a mapping."
                )
            containers.extend(
                (f"job {job_name!r} service {service_name!r}", service)
                for service_name, service in services.items()
            )
        for location, container in containers:
            image = container.get("image") if isinstance(container, dict) else container
            if (
                not isinstance(image, str)
                or CONTAINER_REFERENCE_PATTERN.fullmatch(image) is None
            ):
                raise InspectionError(
                    f"Workflow {source} {location} image must use a full "
                    f"sha256 digest: {image!r}"
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


def job_effective_contents_read(document: dict[str, Any], job: dict[str, Any]) -> bool:
    """Resolve whether one reminder job can read the repository contents."""
    scope = job if "permissions" in job else document
    permissions = scope.get("permissions")
    return isinstance(permissions, dict) and permissions.get("contents") == "read"


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
            not freshness_schedule_entry_is_valid(entry)
            for entry in triggers["schedule"]
        )
        or not freshness_workflow_dispatch_is_valid(triggers.get("workflow_dispatch"))
        or not has_repository_scoped_concurrency(document)
        or not has_least_privileged_freshness_permissions(document)
        or not has_repository_root_working_directory(document)
        or not has_direct_freshness_jobs(document)
        or not has_freshness_repository_context(document)
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
        if not isinstance(steps, list):
            return False
        job_commands = [
            step["run"]
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        ]
        job_text = "\n".join(job_commands)
        blocks = issue_mutation_command_blocks(job_text)
        if not blocks and steps:
            return False
        if not has_freshness_repository_api_reads(
            job_text, require_lookup=bool(blocks)
        ):
            return False
        if blocks:
            mutation_jobs += 1
            if (
                not freshness_job_execution_is_unconditional(job)
                or not freshness_execution_context_is_bash(document, job)
                or not freshness_authentication_bindings_are_safe(document, job)
                or not freshness_checker_result_binding_is_safe(document, job)
                or not freshness_shell_definitions_are_safe(job_text)
                or not freshness_checker_result_controls_reconciliation(job_text)
                or not freshness_api_result_controls_issue_selection(job_text)
                or not freshness_summary_output_is_safe(job_text)
            ):
                return False
            if not freshness_command_order_is_valid(job_text):
                return False
            if not (
                job_effective_issue_write(document, job)
                and job_effective_contents_read(document, job)
            ):
                return False
        markdown_outputs = freshness_audit_markdown_outputs(job_text)
        lines = [
            line for command in job_commands for line in executable_shell_lines(command)
        ]
        if (
            markdown_outputs
            and any(FRESHNESS_REMINDER_MARKER in line for line in lines)
            and has_issue_body_file_reconciliation_for_files(
                job_text,
                markdown_outputs,
                {FRESHNESS_REMINDER_REPOSITORY},
            )
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
        or set(document) != CODE_SCANNING_ALLOWLIST_KEYS
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
