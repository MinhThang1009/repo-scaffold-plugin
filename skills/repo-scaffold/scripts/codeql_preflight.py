#!/usr/bin/env python3
"""Fail-closed CodeQL default-setup preflight for repo-scaffold."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import quote

try:
    import yaml
except ImportError as exc:
    print(
        json.dumps(
            {
                "inspection_complete": False,
                "decision": "inconclusive",
                "error": "PyYAML is required for structural workflow inspection.",
            }
        )
    )
    raise SystemExit(2) from exc


MAX_LOCAL_WORKFLOWS = 500
MAX_REMOTE_WORKFLOWS = 500
MAX_LOCAL_DIRECTORY_ENTRIES = 10_000
MAX_WORKFLOW_BYTES = 5 * 1024 * 1024
MAX_TOTAL_WORKFLOW_BYTES = 64 * 1024 * 1024
MAX_GH_REQUESTS = 2_000
GH_REQUEST_TIMEOUT_SECONDS = 60
MAX_INSPECTION_SECONDS = 600
MAX_GH_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_GH_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_GH_JSON_NESTING = 100
MAX_REUSABLE_WORKFLOWS_PER_ROOT = 50
MAX_REUSABLE_REFERENCES_PER_ROOT = 500
MAX_WORKFLOW_LEVELS = 10
MAX_DYNAMIC_EXECUTION_DEPTH = 20
MAX_ENV_SPLIT_EXPANSIONS = 20
MAX_SHELL_RUN_BYTES = 256 * 1024
MAX_BASH_QUOTED_PREFIX_SCAN_CHARS = 1_000_000
MAX_CODEQL_COMMAND_SEGMENT_BYTES = 64 * 1024
FULL_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.IGNORECASE)
EXTERNAL_CALL = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/"
    r"(?P<path>\.github/workflows/[^@\\]+)@(?P<ref>[^\s]+)$"
)
LOCAL_CALL = re.compile(r"^\./(?P<path>\.github/workflows/[^\\]+)$")
CODEQL_ACTION = re.compile(
    r"^github/codeql-action/(?:init|autobuild|analyze)@[^\s]+$", re.IGNORECASE
)
# Keep token branches first-character-disjoint because workflow text is untrusted.
SHELL_ASSIGNMENT_VALUE_PATTERN = (
    r"""(?:[^\s;&|"'\\]|\\[^\r\n]|"(?:\\[^\r\n]|[^"\\\r\n])*"|'[^'\r\n]*')+"""
)
CODEQL_CLI = re.compile(
    r"(?:^|[\n;&|(){}]|\bthen\b|\bdo\b)\s*"
    r"(?:(?:sudo|env|command)\s+)*"
    rf"(?:[A-Za-z_][A-Za-z0-9_]*={SHELL_ASSIGNMENT_VALUE_PATTERN}\s+)*"
    r"(?:\"[^\"]*[/\\]codeql(?:\.exe)?\"|'[^']*[/\\]codeql(?:\.exe)?'|"
    r"(?:[^\s;&|\"']+[/\\])?codeql(?:\.exe)?|\"?\$\{?CODEQL\}?\"?)\s+"
    r"(?:database|github|pack|query|resolve|execute|bqrs|dataset)\b",
    re.IGNORECASE | re.MULTILINE,
)
CODEQL_SUBCOMMANDS = {
    "bqrs",
    "database",
    "dataset",
    "execute",
    "github",
    "pack",
    "query",
    "resolve",
}
POWERSHELL_START_PROCESS_CODEQL = re.compile(
    r"(?i)^\s*(?:Start-Process|saps|start)\s+"
    r"(?:-FilePath\s+)?"
    r"(?:\"(?:[^\"\r\n]*[/\\])?codeql(?:\.exe)?\"|"
    r"'(?:[^'\r\n]*[/\\])?codeql(?:\.exe)?'|"
    r"(?:[^\s;|\"']+[/\\])?codeql(?:\.exe)?)"
    r"(?=[^;|\r\n]*\b(?:database|github|pack|query|resolve|execute|bqrs|dataset)\b)",
)
# Exclude quote/backtick openers from fallback branches to keep matching linear.
POWERSHELL_ARGUMENT_PATTERN = (
    r"""(?:"(?:`[^\r\n]|[^"`\r\n])*"|'(?:''|[^'\r\n])*'|[^\s;|&"']+)"""
)


class InspectionError(RuntimeError):
    """Raised when the preflight cannot prove that mutation is safe."""


def resolve_path_executable(name: str, *, forbidden_root: Path) -> str | None:
    """Resolve a tool only from absolute PATH entries outside the target repository."""
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


def _require_json_nesting_within_limit(payload: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in payload:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_GH_JSON_NESTING:
                raise InspectionError(
                    "GitHub API JSON exceeds the nesting safety cap of "
                    f"{MAX_GH_JSON_NESTING}."
                )
        elif character in "]}":
            depth -= 1


class UniqueKeyBaseLoader(yaml.BaseLoader):
    """BaseLoader variant that rejects duplicate mapping keys."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise InspectionError(
                    "Workflow contains an unhashable mapping key."
                ) from exc
            if duplicate:
                raise InspectionError(f"Workflow contains duplicate key {key!r}.")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class WorkflowSignals:
    has_advanced_setup: bool
    reusable_calls: tuple[str, ...]


@dataclass
class WorkflowByteBudget:
    consumed_bytes: int = 0
    deadline: float = field(
        default_factory=lambda: time.monotonic() + MAX_INSPECTION_SECONDS
    )

    def check_deadline(self) -> None:
        if time.monotonic() > self.deadline:
            raise InspectionError(
                f"Workflow inspection exceeded the {MAX_INSPECTION_SECONDS}-second safety cap."
            )

    def parse(self, text: str, source: str) -> WorkflowSignals:
        self.check_deadline()
        size = len(text.encode("utf-8"))
        if self.consumed_bytes + size > MAX_TOTAL_WORKFLOW_BYTES:
            raise InspectionError(
                "Inspection exceeded the total workflow byte safety cap of "
                f"{MAX_TOTAL_WORKFLOW_BYTES} bytes."
            )
        self.consumed_bytes += size
        signals = parse_workflow(text, source, self.check_deadline)
        self.check_deadline()
        return signals


@dataclass(frozen=True)
class WorkflowContext:
    kind: str
    owner: str = ""
    repo: str = ""
    commit: str = ""

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (
            self.kind,
            self.owner.casefold(),
            self.repo.casefold(),
            self.commit.lower(),
        )


@dataclass(frozen=True)
class WorkflowNode:
    identity: tuple[str, ...]
    display_name: str
    context: WorkflowContext
    signals: WorkflowSignals


class GitHubClient:
    def __init__(self, hostname: str, *, forbidden_root: Path | None = None) -> None:
        self.hostname = hostname
        gh_executable = resolve_path_executable(
            "gh", forbidden_root=forbidden_root or Path.cwd()
        )
        if gh_executable is None:
            raise InspectionError(
                "GitHub CLI was not found on an absolute PATH entry outside the "
                "repository."
            )
        self.gh_executable = gh_executable
        self.request_count = 0
        self.response_bytes = 0
        self.deadline = time.monotonic() + MAX_INSPECTION_SECONDS

    @staticmethod
    def _read_output(stream: Any, limit: int, label: str) -> tuple[str, int]:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        if size > limit:
            raise InspectionError(
                f"GitHub API {label} exceeds the {limit}-byte safety cap."
            )
        stream.seek(0)
        try:
            return stream.read().decode("utf-8"), size
        except UnicodeDecodeError as exc:
            raise InspectionError(f"GitHub API {label} is not valid UTF-8.") from exc

    def _run(self, endpoint: str, *, raw: bool = False) -> str:
        if self.request_count >= MAX_GH_REQUESTS:
            raise InspectionError(
                f"GitHub API inspection exceeded the {MAX_GH_REQUESTS}-request safety cap."
            )
        self.request_count += 1
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise InspectionError(
                f"GitHub API inspection exceeded the {MAX_INSPECTION_SECONDS}-second safety cap."
            )
        timeout = min(GH_REQUEST_TIMEOUT_SECONDS, max(1, int(remaining)))
        command = [self.gh_executable, "api", "--hostname", self.hostname]
        if raw:
            command.extend(["-H", "Accept: application/vnd.github.raw+json"])
        command.append(endpoint)
        environment = os.environ.copy()
        environment.update({"GH_PAGER": "cat", "NO_COLOR": "1"})
        try:
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                result = subprocess.run(  # noqa: S603 - executable is resolved safely
                    command,
                    check=False,
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                    timeout=timeout,
                )
                stdout_text, stdout_size = self._read_output(
                    stdout, MAX_GH_RESPONSE_BYTES, "response byte count"
                )
                stderr_text, stderr_size = self._read_output(
                    stderr, MAX_GH_RESPONSE_BYTES, "error response byte count"
                )
        except FileNotFoundError as exc:
            raise InspectionError(
                "GitHub CLI is not installed or not on PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise InspectionError(
                f"GitHub API request timed out for {endpoint!r} after "
                f"{timeout} seconds."
            ) from exc
        self.response_bytes += stdout_size + stderr_size
        if self.response_bytes > MAX_TOTAL_GH_RESPONSE_BYTES:
            raise InspectionError(
                "GitHub API inspection exceeded the total response byte safety cap of "
                f"{MAX_TOTAL_GH_RESPONSE_BYTES} bytes."
            )
        if result.returncode != 0:
            detail = (stderr_text or stdout_text).strip()
            raise InspectionError(
                f"GitHub API request failed for {endpoint!r}: {detail}"
            )
        return stdout_text

    def json(self, endpoint: str) -> Any:
        payload = self._run(endpoint)
        _require_json_nesting_within_limit(payload)
        try:
            return json.loads(payload)
        except (ValueError, RecursionError) as exc:
            raise InspectionError(
                f"GitHub API returned invalid JSON for {endpoint!r}."
            ) from exc

    def raw(self, endpoint: str) -> str:
        return self._run(endpoint, raw=True)


def _mask(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] not in "\r\n":
            chars[index] = " "


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return bool(backslashes % 2)


def _is_powershell_escaped(text: str, index: int) -> bool:
    backticks = 0
    index -= 1
    while index >= 0 and text[index] == "`":
        backticks += 1
        index -= 1
    return bool(backticks % 2)


def _parse_heredoc_delimiter(line: str, start: int, end: int) -> tuple[str, int]:
    delimiter: list[str] = []
    index = start
    while index < end and line[index] not in " \t;&|()<>\r\n":
        character = line[index]
        if character == "\\":
            index += 1
            if index >= end:
                raise InspectionError(
                    "Shell heredoc has an incomplete escaped delimiter."
                )
            delimiter.append(line[index])
            index += 1
            continue
        if character in "'\"":
            quote_character = character
            index += 1
            while index < end and line[index] != quote_character:
                if quote_character == '"' and line[index] == "\\" and index + 1 < end:
                    index += 1
                delimiter.append(line[index])
                index += 1
            if index >= end:
                raise InspectionError(
                    "Shell heredoc has an unterminated quoted delimiter."
                )
            index += 1
            continue
        delimiter.append(character)
        index += 1
    if not delimiter:
        raise InspectionError("Shell heredoc has no static delimiter.")
    return "".join(delimiter), index


def _bash_heredoc_executes_body(line: str, operator_index: int) -> bool:
    segment = re.split(r"&&|\|\||[;|&()]", line[:operator_index])[-1].strip()
    try:
        words = shlex.split(segment, posix=True)
    except ValueError as exc:
        raise InspectionError("Shell command before heredoc is malformed.") from exc
    words = _bash_unwrap_command(words)
    if not words:
        return False
    command = _bash_command_name(words[0])
    return command in {"bash", "dash", "ksh", "sh", "zsh"} or words[0] in {
        ".",
        "source",
    }


def _bash_command_name(word: str) -> str:
    return PurePosixPath(word.replace("\\", "/")).name.casefold()


def _bash_pop_option(
    words: list[str], option_arguments: set[str], long_option_arguments: set[str]
) -> bool:
    if not words or not words[0].startswith("-") or words[0] == "-":
        return False
    option = words.pop(0)
    if option == "--":
        return True
    if option in option_arguments or option in long_option_arguments:
        if not words:
            return False
        words.pop(0)
    return True


def _bash_unwrap_command(words: list[str]) -> list[str]:
    """Return the command and arguments after supported execution wrappers."""
    words = list(words)
    while words and (
        words[0] in {"!", "if", "then", "until", "while", "do"}
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0])
    ):
        words.pop(0)

    env_split_expansions = 0
    while words:
        wrapper = _bash_command_name(words[0])
        if wrapper == "builtin":
            words.pop(0)
            continue
        if wrapper == "coproc":
            words.pop(0)
            continue
        if wrapper == "command":
            words.pop(0)
            while words and words[0] in {"-p", "--"}:
                words.pop(0)
            if words and words[0] in {"-v", "-V"}:
                return []
            continue
        if wrapper == "exec":
            words.pop(0)
            while words and words[0].startswith("-") and words[0] != "-":
                option = words.pop(0)
                if option == "--":
                    break
                if option == "-a":
                    if not words:
                        return []
                    words.pop(0)
                elif not re.fullmatch(r"-[cl]+", option):
                    return []
            continue
        if wrapper == "env":
            words.pop(0)
            while words:
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
                    words.pop(0)
                    continue
                split_string: str | None = None
                if words[0].startswith("--split-string="):
                    split_string = words.pop(0).split("=", 1)[1]
                elif words[0] in {"-S", "--split-string"}:
                    words.pop(0)
                    if not words:
                        return []
                    split_string = words.pop(0)
                elif words[0].startswith("-S") and len(words[0]) > 2:
                    split_string = words.pop(0)[2:]
                if split_string is not None:
                    env_split_expansions += 1
                    if env_split_expansions > MAX_ENV_SPLIT_EXPANSIONS:
                        raise InspectionError(
                            "Shell env split-string nesting exceeds the safety limit."
                        )
                    if "$" in split_string or "`" in split_string:
                        raise InspectionError(
                            "Shell env split-string has a non-literal payload."
                        )
                    try:
                        words[:0] = shlex.split(split_string, posix=True)
                    except ValueError as exc:
                        raise InspectionError(
                            "Shell env split-string is malformed."
                        ) from exc
                    continue
                if words[0].startswith(("--unset=", "--chdir=", "--argv0=")):
                    words.pop(0)
                    continue
                if words[0] in {
                    "-u",
                    "--unset",
                    "-C",
                    "--chdir",
                    "-S",
                    "--split-string",
                    "--argv0",
                }:
                    if len(words) < 2:
                        return []
                    del words[:2]
                    continue
                if _bash_pop_option(words, set(), set()):
                    continue
                break
            continue
        if wrapper == "sudo":
            words.pop(0)
            option_arguments = {
                "-C",
                "-D",
                "-g",
                "-h",
                "-p",
                "-R",
                "-r",
                "-T",
                "-t",
                "-U",
                "-u",
            }
            long_option_arguments = {
                "--chdir",
                "--close-from",
                "--group",
                "--host",
                "--prompt",
                "--role",
                "--type",
                "--user",
            }
            while words and words[0].startswith("-") and words[0] != "-":
                if words[0].startswith(
                    tuple(f"{option}=" for option in long_option_arguments)
                ):
                    words.pop(0)
                    continue
                if not _bash_pop_option(words, option_arguments, long_option_arguments):
                    return []
            continue
        if wrapper == "timeout":
            words.pop(0)
            while words and words[0].startswith("-") and words[0] != "-":
                if words[0].startswith(("--kill-after=", "--signal=")):
                    words.pop(0)
                    continue
                if not _bash_pop_option(
                    words, {"-k", "-s"}, {"--kill-after", "--signal"}
                ):
                    return []
            if not words:
                return []
            words.pop(0)  # duration
            continue
        if wrapper == "time":
            words.pop(0)
            while words and words[0].startswith("-") and words[0] != "-":
                if words[0].startswith(("--format=", "--output=")):
                    words.pop(0)
                    continue
                if not _bash_pop_option(words, {"-f", "-o"}, {"--format", "--output"}):
                    return []
            continue
        if wrapper == "nohup":
            words.pop(0)
            if words and words[0] == "--":
                words.pop(0)
            if words and words[0] in {"--help", "--version"}:
                return []
            continue
        if wrapper == "nice":
            words.pop(0)
            if words and words[0] in {"-n", "--adjustment"}:
                if len(words) < 2:
                    return []
                del words[:2]
            elif words and re.fullmatch(r"-\d+", words[0]):
                words.pop(0)
            continue
        if wrapper == "stdbuf":
            words.pop(0)
            while words and re.match(
                r"^(?:-[ioe]|--(?:input|output|error))(?:=|$)", words[0]
            ):
                option = words.pop(0)
                if "=" not in option and option in {
                    "-i",
                    "-o",
                    "-e",
                    "--input",
                    "--output",
                    "--error",
                }:
                    if not words:
                        return []
                    words.pop(0)
            continue
        break
    return words


def _bash_words_contain_codeql(words: list[str], dynamic_execution_depth: int) -> bool:
    if dynamic_execution_depth > MAX_DYNAMIC_EXECUTION_DEPTH:
        raise InspectionError("Dynamic command nesting exceeds the safety limit.")
    words = _bash_unwrap_command(words)
    if len(words) < 2:
        return False
    executable = words[0]
    executable_name = _bash_command_name(executable)
    is_codeql = executable_name in {"codeql", "codeql.exe"} or executable in {
        "$CODEQL",
        "${CODEQL}",
    }
    if is_codeql and words[1].casefold() in CODEQL_SUBCOMMANDS:
        return True

    nested_commands: list[list[str]] = []
    if executable_name == "xargs":
        nested_commands.append(_bash_xargs_command(words[1:]))
    elif executable_name == "find":
        nested_commands.extend(
            words[index + 1 :]
            for index, word in enumerate(words[:-1])
            if word in {"-exec", "-execdir"}
        )

    for nested in nested_commands:
        nested = _bash_unwrap_command(nested)
        if _bash_words_contain_codeql(nested, dynamic_execution_depth + 1):
            return True
        command_index = _bash_shell_command_string_index(nested)
        if command_index is None:
            continue
        if command_index >= len(nested):
            raise InspectionError("Shell dynamic command has no literal payload.")
        body = nested[command_index]
        if "$" in body or "`" in body:
            raise InspectionError("Shell dynamic command has a non-literal payload.")
        if contains_codeql_cli(body, "bash", dynamic_execution_depth + 1):
            return True
    return False


def _bash_contains_wrapped_codeql(text: str, dynamic_execution_depth: int = 0) -> bool:
    normalized = re.sub(r"\\\r?\n", " ", text).replace("\r\n", "\n")
    normalized = normalized.replace("\n", ";")
    lexer = shlex.shlex(normalized, posix=True, punctuation_chars=";&|(){}")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise InspectionError("Shell command is malformed.") from exc

    segment: list[str] = []
    for lexeme in [*tokens, ";"]:
        if lexeme and all(character in ";&|(){}" for character in lexeme):
            if _bash_words_contain_codeql(segment, dynamic_execution_depth):
                return True
            segment = []
        else:
            segment.append(lexeme)
    return False


def _bash_xargs_command(arguments: list[str]) -> list[str]:
    """Return the command after supported GNU/BSD xargs options."""
    options_with_argument = {
        "-a",
        "--arg-file",
        "-d",
        "--delimiter",
        "-E",
        "--eof",
        "-I",
        "--replace",
        "-J",
        "-L",
        "--max-lines",
        "-n",
        "--max-args",
        "-P",
        "--max-procs",
        "--process-slot-var",
        "-R",
        "-s",
        "-S",
        "--max-chars",
    }
    options_without_argument = {
        "-0",
        "--null",
        "-o",
        "--open-tty",
        "-p",
        "--interactive",
        "-r",
        "--no-run-if-empty",
        "-t",
        "--verbose",
        "-x",
        "--exit",
    }
    attached_short_options = tuple(
        option
        for option in options_with_argument
        if option.startswith("-") and len(option) == 2
    )
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option == "--":
            return arguments[index + 1 :]
        if not option.startswith("-") or option == "-":
            return arguments[index:]
        if option in options_without_argument:
            index += 1
            continue
        if option in options_with_argument:
            index += 2
            if index > len(arguments):
                return []
            continue
        if any(
            option.startswith(prefix) and len(option) > len(prefix)
            for prefix in attached_short_options
        ):
            index += 1
            continue
        if "=" in option and option.split("=", 1)[0] in options_with_argument:
            index += 1
            continue
        return []
    return []


def _bash_shell_command_string_index(words: list[str]) -> int | None:
    words = _bash_unwrap_command(words)
    if not words or _bash_command_name(words[0]) not in {
        "bash",
        "dash",
        "ksh",
        "sh",
        "zsh",
    }:
        return None

    index = 1
    has_command_option = False
    while index < len(words):
        option = words[index]
        if option in {"--", "-"}:
            return index + 1 if has_command_option else None
        if not option.startswith("-"):
            return index if has_command_option else None
        if option in {"-O", "-o", "--init-file", "--rcfile"}:
            index += 2
            continue
        if option.startswith(("-O", "-o", "--init-file=", "--rcfile=")):
            index += 1
            continue
        if re.fullmatch(r"-[A-Za-z]+", option) and "c" in option[1:]:
            has_command_option = True
        index += 1
    return len(words) if has_command_option else None


def _bash_shell_command_string(words: list[str]) -> bool:
    words = _bash_unwrap_command(words)
    command_index = _bash_shell_command_string_index(words)
    return command_index is not None and command_index == len(words)


def _bash_dynamic_execution_is_unresolved(text: str) -> bool:
    normalized = re.sub(r"\\\r?\n", " ", text).replace("\r\n", "\n")
    lexer = shlex.shlex(
        normalized.replace("\n", ";"), posix=True, punctuation_chars=";&|(){}"
    )
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise InspectionError("Shell dynamic command is malformed.") from exc

    segment: list[str] = []
    previous_separator = ""
    for lexeme in [*tokens, ";"]:
        if lexeme and all(character in ";&|(){}" for character in lexeme):
            words = _bash_unwrap_command(segment)
            if words:
                command = _bash_command_name(words[0])
                is_script_loader = command in {
                    "bash",
                    "dash",
                    "ksh",
                    "sh",
                    "zsh",
                } or words[0] in {".", "source"}
                if "$" in words[0] or "`" in words[0]:
                    return True
                if (
                    lexeme == "("
                    and is_script_loader
                    and any(word in {"<", ">"} for word in words[1:])
                ):
                    return True
                trap_handler = _bash_trap_handler(words)
                if trap_handler is not None and any(
                    marker in trap_handler for marker in ("$", "`")
                ):
                    return True
                if command == "eval":
                    if any(
                        "$" in argument or "`" in argument for argument in words[1:]
                    ):
                        return True
                else:
                    command_index = _bash_shell_command_string_index(words)
                    if command_index is not None:
                        if command_index >= len(words):
                            return True
                        command_text = words[command_index]
                        if "$" in command_text or "`" in command_text:
                            return True
                    elif is_script_loader:
                        if "|" in previous_separator:
                            return True
                        payload = _bash_here_string_payload(words)
                        if payload is not None and any(
                            marker in payload for marker in ("$", "`")
                        ):
                            return True
            segment = []
            previous_separator = lexeme
        else:
            segment.append(lexeme)
    return False


def _bash_here_string_payload(words: list[str]) -> str | None:
    for index, word in enumerate(words):
        if word == "<<<":
            return words[index + 1] if index + 1 < len(words) else ""
        if word.startswith("<<<"):
            return word[3:]
    return None


def _bash_trap_handler(words: list[str]) -> str | None:
    words = _bash_unwrap_command(words)
    if not words or _bash_command_name(words[0]) != "trap":
        return None
    arguments = words[1:]
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    if len(arguments) < 2 or arguments[0] in {"", "-", "-l", "-p"}:
        return None
    return arguments[0]


def _bash_dynamic_execution_bodies(text: str) -> list[str]:
    normalized = re.sub(r"\\\r?\n", " ", text).replace("\r\n", "\n")
    lexer = shlex.shlex(
        normalized.replace("\n", ";"), posix=True, punctuation_chars=";&|(){}"
    )
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise InspectionError("Shell dynamic command is malformed.") from exc

    bodies: list[str] = []
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token and all(character in ";&|(){}" for character in token):
            words = _bash_unwrap_command(segment)
            if words:
                command = _bash_command_name(words[0])
                trap_handler = _bash_trap_handler(words)
                if trap_handler is not None:
                    bodies.append(trap_handler)
                elif command == "eval" and len(words) > 1:
                    bodies.append(" ".join(words[1:]))
                else:
                    command_index = _bash_shell_command_string_index(words)
                    if command_index is not None and command_index < len(words):
                        bodies.append(words[command_index])
                    elif command in {
                        "bash",
                        "dash",
                        "ksh",
                        "sh",
                        "zsh",
                    } or words[0] in {".", "source"}:
                        payload = _bash_here_string_payload(words)
                        if payload is not None:
                            bodies.append(payload)
            segment = []
        else:
            segment.append(token)
    return bodies


def _bash_alias_expansions(
    text: str,
    aliases: dict[str, tuple[str, int]] | None = None,
    expand_aliases: bool = False,
    enabled_line: int = 0,
    fresh_parse: bool = False,
    depth: int = 0,
) -> list[str]:
    if depth > MAX_DYNAMIC_EXECUTION_DEPTH:
        raise InspectionError("Alias expansion nesting exceeds the safety limit.")
    normalized = re.sub(r"\\\r?\n", " ", text).replace("\r\n", "\n")
    lexer = shlex.shlex(normalized, posix=True, punctuation_chars=";&|(){}\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise InspectionError("Shell alias command is malformed.") from exc

    segments: list[tuple[list[str], int]] = []
    segment: list[str] = []
    line = 1
    segment_line = line
    for token in [*tokens, ";"]:
        if token and all(character in ";&|(){}\n" for character in token):
            if segment:
                segments.append((segment, segment_line))
            segment = []
            line += token.count("\n")
            segment_line = line
        else:
            if not segment:
                segment_line = line
            segment.append(token)

    active_aliases = dict(aliases or {})
    expansions: list[str] = []
    for raw_words, invocation_line in segments:
        words = _bash_unwrap_command(raw_words)
        if not words:
            continue
        command = _bash_command_name(words[0])
        if command == "shopt" and "expand_aliases" in words[1:]:
            if "-u" in words[1:]:
                expand_aliases = False
            elif "-s" in words[1:]:
                expand_aliases = True
                enabled_line = invocation_line
            continue
        if command == "alias":
            for declaration in words[1:]:
                if declaration in {"--", "-p"} or "=" not in declaration:
                    continue
                name, value = declaration.split("=", 1)
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
                    active_aliases[name] = (value, invocation_line)
            continue
        if command == "unalias":
            if "-a" in words[1:]:
                active_aliases.clear()
            else:
                for name in words[1:]:
                    if name != "--":
                        active_aliases.pop(name, None)
            continue
        if command == "eval" and len(words) > 1:
            body = " ".join(words[1:])
            if "$" not in body and "`" not in body:
                expansions.extend(
                    _bash_alias_expansions(
                        body,
                        active_aliases,
                        expand_aliases,
                        enabled_line,
                        True,
                        depth + 1,
                    )
                )
            continue

        alias = active_aliases.get(words[0])
        if alias is None or not expand_aliases:
            continue
        alias_body, declaration_line = alias
        if not fresh_parse and invocation_line <= max(declaration_line, enabled_line):
            continue
        expansion = " ".join((alias_body, *words[1:])).strip()
        expansions.append(expansion)
        expansions.extend(
            _bash_alias_expansions(
                expansion,
                active_aliases,
                expand_aliases,
                enabled_line,
                True,
                depth + 1,
            )
        )
    return expansions


def _bash_alias_state(
    text: str,
) -> tuple[dict[str, tuple[str, int]], bool, int]:
    """Return aliases that are active after parsing top-level Bash text."""
    normalized = re.sub(r"\\\r?\n", " ", text).replace("\r\n", "\n")
    lexer = shlex.shlex(normalized, posix=True, punctuation_chars=";&|(){}\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise InspectionError("Shell alias command is malformed.") from exc

    active_aliases: dict[str, tuple[str, int]] = {}
    expand_aliases = False
    enabled_line = 0
    segment: list[str] = []
    line = 1
    segment_line = line
    for token in [*tokens, ";"]:
        if token and all(character in ";&|(){}\n" for character in token):
            words = _bash_unwrap_command(segment)
            if words:
                command = _bash_command_name(words[0])
                if command == "shopt" and "expand_aliases" in words[1:]:
                    if "-u" in words[1:]:
                        expand_aliases = False
                    elif "-s" in words[1:]:
                        expand_aliases = True
                        enabled_line = segment_line
                elif command == "alias":
                    for declaration in words[1:]:
                        if declaration in {"--", "-p"} or "=" not in declaration:
                            continue
                        name, value = declaration.split("=", 1)
                        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
                            active_aliases[name] = (value, segment_line)
                elif command == "unalias":
                    if "-a" in words[1:]:
                        active_aliases.clear()
                    else:
                        for name in words[1:]:
                            if name != "--":
                                active_aliases.pop(name, None)
            segment = []
            line += token.count("\n")
            segment_line = line
        else:
            if not segment:
                segment_line = line
            segment.append(token)
    return active_aliases, expand_aliases, enabled_line


def _bash_quoted_string_role(line: str, quote_index: int) -> str:
    segment = re.split(r"&&|\|\||[;|&()]", line[:quote_index])[-1]
    if re.search(r"[A-Za-z_][A-Za-z0-9_]*=$", segment.rstrip()):
        return "literal"
    try:
        words = shlex.split(segment, posix=True)
    except ValueError as exc:
        raise InspectionError(
            "Shell command before quoted string is malformed."
        ) from exc
    words = _bash_unwrap_command(words)
    if not words:
        return "command"
    if _bash_shell_command_string(words):
        return "shell-command"
    command = _bash_command_name(words[0])
    nested_commands: list[list[str]] = []
    if command == "xargs":
        nested_commands.append(_bash_xargs_command(words[1:]))
    elif command == "find":
        nested_commands.extend(
            words[index + 1 :]
            for index, word in enumerate(words[:-1])
            if word in {"-exec", "-execdir"}
        )
    if any(_bash_shell_command_string(nested) for nested in nested_commands):
        return "shell-command"
    return "eval" if _bash_command_name(words[0]) == "eval" else "literal"


def _bash_opens_array_assignment(line: str, parenthesis_index: int) -> bool:
    if parenthesis_index < 2 or line[parenthesis_index - 1] != "=":
        return False
    cursor = parenthesis_index - 2
    if line[cursor] == "+":
        cursor -= 1
    name_end = cursor + 1
    while cursor >= 0 and (
        line[cursor].isascii() and (line[cursor].isalnum() or line[cursor] == "_")
    ):
        cursor -= 1
    name = line[cursor + 1 : name_end]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return False
    return cursor < 0 or line[cursor] in " \t;&|"


def _bash_executable_text(text: str) -> str:
    """Mask Bash heredoc bodies and multiline string literals."""
    output: list[str] = []
    pending: list[tuple[str, bool, bool]] = []
    quoted_prefix_scan_chars = 0
    quote_character: str | None = None
    quote_is_multiline = False
    quote_executes_content = False
    quote_is_command_string = False
    command_substitution_depth = 0
    command_substitution_quote: str | None = None
    backtick_substitution = False
    backtick_substitution_quote: str | None = None
    array_literal_depth = 0
    array_command_substitution_depth = 0
    arithmetic_parenthesis_depth = 0

    for line in text.splitlines(keepends=True):
        masked = list(line)
        content_end = len(line.rstrip("\r\n"))
        if pending:
            delimiter, strip_tabs, mask_body = pending[0]
            candidate = line[:content_end]
            if strip_tabs:
                candidate = candidate.lstrip("\t")
            if mask_body:
                _mask(masked, 0, content_end)
            if candidate == delimiter:
                pending.pop(0)
            output.append("".join(masked))
            continue

        index = 0
        quote_start: int | None = None
        if quote_character is not None:
            quote_start = 0
        while index < content_end:
            character = line[index]
            if backtick_substitution:
                if backtick_substitution_quote is not None:
                    if backtick_substitution_quote == "'":
                        if character == "'":
                            backtick_substitution_quote = None
                    elif character == "\\":
                        index += 2
                        continue
                    elif character == backtick_substitution_quote:
                        backtick_substitution_quote = None
                elif character in "'\"":
                    backtick_substitution_quote = character
                elif character == "\\":
                    index += 2
                    continue
                elif character == "`" and not _is_escaped(line, index):
                    masked[index] = "\n"
                    backtick_substitution = False
                index += 1
                continue
            if quote_character is not None:
                if quote_character == '"':
                    if command_substitution_depth:
                        if command_substitution_quote is not None:
                            if command_substitution_quote == "'":
                                if character == "'":
                                    command_substitution_quote = None
                            elif character == "\\":
                                index += 2
                                continue
                            elif character == command_substitution_quote:
                                command_substitution_quote = None
                        elif character in "'\"`":
                            command_substitution_quote = character
                        elif character == "\\":
                            index += 2
                            continue
                        elif character == "(":
                            command_substitution_depth += 1
                        elif character == ")":
                            command_substitution_depth -= 1
                            if not command_substitution_depth:
                                command_substitution_quote = None
                        index += 1
                        continue
                    if line.startswith("$(", index) and not _is_escaped(line, index):
                        command_substitution_depth = 1
                        index += 2
                        continue
                    if character == "`" and not _is_escaped(line, index):
                        masked[index] = "\n"
                        backtick_substitution = True
                        backtick_substitution_quote = None
                        index += 1
                        continue
                closes_quote = (
                    not command_substitution_depth
                    and not backtick_substitution
                    and character == quote_character
                    and (quote_character == "'" or not _is_escaped(line, index))
                )
                if closes_quote:
                    if (
                        quote_character == "'"
                        and not quote_executes_content
                        and quote_start is not None
                    ):
                        _mask(masked, quote_start, index + 1)
                    if quote_is_command_string:
                        masked[index] = "\n"
                    elif quote_is_multiline and not quote_executes_content:
                        _mask(masked, index, index + 1)
                    quote_character = None
                    quote_is_multiline = False
                    quote_executes_content = False
                    quote_is_command_string = False
                    quote_start = None
                elif (quote_character == '"' and not quote_executes_content) or (
                    quote_is_multiline and not quote_executes_content
                ):
                    _mask(masked, index, index + 1)
                index += 1
                continue

            if arithmetic_parenthesis_depth:
                if character == "\\":
                    index += 2
                    continue
                if character == "(":
                    arithmetic_parenthesis_depth += 1
                elif character == ")":
                    arithmetic_parenthesis_depth -= 1
                index += 1
                continue

            if (
                character == "#"
                and (index == 0 or line[index - 1] in " \t;|&()")
                and not _is_escaped(line, index)
            ):
                _mask(masked, index, content_end)
                break
            if (
                array_literal_depth
                and line[index : index + 2] in {"$(", "<(", ">("}
                and not _is_escaped(line, index)
            ):
                array_literal_depth += 1
                array_command_substitution_depth += 1
                index += 2
                continue
            if line.startswith("$((", index) and not _is_escaped(line, index):
                arithmetic_parenthesis_depth = 2
                index += 3
                continue
            if line.startswith("((", index) and not _is_escaped(line, index):
                arithmetic_parenthesis_depth = 2
                index += 2
                continue
            if character == "(" and not _is_escaped(line, index):
                opens_array = array_literal_depth or _bash_opens_array_assignment(
                    line, index
                )
                if opens_array:
                    array_literal_depth += 1
                    if array_command_substitution_depth:
                        array_command_substitution_depth += 1
                    index += 1
                    continue
            if (
                character == ")"
                and array_literal_depth
                and not _is_escaped(line, index)
            ):
                array_literal_depth -= 1
                if array_command_substitution_depth:
                    array_command_substitution_depth -= 1
                index += 1
                continue
            if character in "'\"" and not _is_escaped(line, index):
                quote_character = character
                quote_start = index
                if array_command_substitution_depth or backtick_substitution:
                    quote_role = "command"
                elif array_literal_depth:
                    quote_role = "literal"
                else:
                    quoted_prefix_scan_chars += index
                    if quoted_prefix_scan_chars > MAX_BASH_QUOTED_PREFIX_SCAN_CHARS:
                        raise InspectionError(
                            "Shell quoted-string analysis exceeds the safety limit."
                        )
                    quote_role = _bash_quoted_string_role(line, index)
                quote_executes_content = quote_role != "literal"
                quote_is_command_string = quote_role in {"eval", "shell-command"}
                if quote_is_command_string:
                    masked[index] = "\n"
                index += 1
                continue
            if character == "`" and not _is_escaped(line, index):
                masked[index] = "\n"
                backtick_substitution = True
                backtick_substitution_quote = None
                index += 1
                continue
            if line.startswith("<<<", index) and not _is_escaped(line, index):
                index += 3
                continue
            if line.startswith("<<", index) and not _is_escaped(line, index):
                operator_index = index
                delimiter_index = index + 2
                strip_tabs = False
                if delimiter_index < content_end and line[delimiter_index] == "-":
                    strip_tabs = True
                    delimiter_index += 1
                while delimiter_index < content_end and line[delimiter_index] in " \t":
                    delimiter_index += 1
                delimiter, index = _parse_heredoc_delimiter(
                    line, delimiter_index, content_end
                )
                pending.append(
                    (
                        delimiter,
                        strip_tabs,
                        not _bash_heredoc_executes_body(line, operator_index),
                    )
                )
                continue
            if array_literal_depth and not array_command_substitution_depth:
                _mask(masked, index, index + 1)
            index += 1

        if quote_character is not None:
            if (
                quote_start is not None
                and not quote_is_multiline
                and not quote_executes_content
            ):
                _mask(masked, quote_start, content_end)
            quote_is_multiline = True
        output.append("".join(masked))

    if pending:
        raise InspectionError("Shell heredoc is not terminated.")
    if quote_character is not None:
        raise InspectionError("Shell multiline string literal is not terminated.")
    if (
        command_substitution_depth
        or backtick_substitution
        or backtick_substitution_quote
    ):
        raise InspectionError("Shell command substitution is not terminated.")
    if array_literal_depth or array_command_substitution_depth:
        raise InspectionError("Shell array assignment is not terminated.")
    if arithmetic_parenthesis_depth:
        raise InspectionError("Shell arithmetic expression is not terminated.")
    return "".join(output)


def _powershell_here_string_start(
    line: str, content_end: int
) -> tuple[int, str] | None:
    quote_character: str | None = None
    index = 0
    while index < content_end:
        character = line[index]
        if quote_character is not None:
            if character == quote_character:
                if index + 1 < content_end and line[index + 1] == quote_character:
                    index += 2
                    continue
                if quote_character == '"' and _is_powershell_escaped(line, index):
                    index += 1
                    continue
                quote_character = None
            index += 1
            continue
        if character == "#":
            return None
        if character in "'\"":
            quote_character = character
            index += 1
            continue
        if character == "@" and index + 1 < content_end and line[index + 1] in "'\"":
            suffix = line[index + 2 : content_end].lstrip()
            if not suffix or suffix.startswith("#"):
                return index, line[index + 1]
        index += 1
    return None


def _powershell_subexpression_text(text: str) -> str:
    masked = list(text)
    _mask(masked, 0, len(masked))
    search_index = 0
    while True:
        start = text.find("$(", search_index)
        while start >= 0:
            backticks = 0
            escape_index = start - 1
            while escape_index >= 0 and text[escape_index] == "`":
                backticks += 1
                escape_index -= 1
            if not backticks % 2:
                break
            start = text.find("$(", start + 2)
        if start < 0:
            break
        depth = 1
        index = start + 2
        quote_character: str | None = None
        while index < len(text) and depth:
            character = text[index]
            if quote_character is not None:
                if character == "`":
                    index += 2
                    continue
                if character == quote_character:
                    quote_character = None
            elif character in "'\"":
                quote_character = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            index += 1
        if depth:
            raise InspectionError("PowerShell subexpression is not terminated.")
        masked[start:index] = text[start:index]
        search_index = index
    return "".join(masked)


def _powershell_executable_text(text: str) -> str:
    """Mask PowerShell here-string delimiters and bodies."""
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        masked = list(line)
        content_end = len(line.rstrip("\r\n"))
        start = _powershell_here_string_start(line, content_end)
        if start is None:
            output.append(line)
            line_index += 1
            continue

        start_index, here_quote = start
        terminator_index = line_index + 1
        terminator_match: re.Match[str] | None = None
        while terminator_index < len(lines):
            terminator_match = re.match(
                rf"^[ \t]*{re.escape(here_quote)}@(?=$|[ \t;|,)])",
                lines[terminator_index],
            )
            if terminator_match:
                break
            terminator_index += 1
        if terminator_match is None:
            raise InspectionError("PowerShell here-string is not terminated.")

        body = "".join(lines[line_index + 1 : terminator_index])
        terminator_line = lines[terminator_index]
        prefix = line[:start_index]
        suffix = terminator_line[terminator_match.end() :]
        executes_body = bool(
            re.search(
                r"(?i)(?:^|[;|])\s*(?:Invoke-Expression|iex)"
                r"(?:\s+-Command)?\s*$",
                prefix,
            )
            or re.match(r"(?i)^\s*\|\s*(?:Invoke-Expression|iex)(?:\s|$)", suffix)
        )
        _mask(masked, start_index, content_end)
        output.append("".join(masked))
        if executes_body:
            output.append(body)
        elif here_quote == '"':
            output.append(_powershell_subexpression_text(body))
        else:
            body_masked = list(body)
            _mask(body_masked, 0, len(body))
            output.append("".join(body_masked))
        terminator_masked = list(terminator_line)
        _mask(terminator_masked, 0, terminator_match.end())
        output.append("".join(terminator_masked))
        line_index = terminator_index + 1
    return "".join(output)


def _powershell_mask_comments_and_multiline_literals(text: str) -> str:
    """Mask comments and string data while retaining `$()` execution."""
    masked = list(text)
    index = 0
    while index < len(text):
        if text.startswith("<#", index):
            end = text.find("#>", index + 2)
            if end < 0:
                raise InspectionError("PowerShell block comment is not terminated.")
            _mask(masked, index, end + 2)
            index = end + 2
            continue
        if text[index] == "#":
            end = text.find("\n", index)
            if end < 0:
                end = len(text)
            _mask(masked, index, end)
            index = end
            continue
        if text[index] not in "'\"":
            index += 1
            continue

        quote_character = text[index]
        end = index + 1
        while end < len(text):
            if quote_character == '"' and text[end] == "`":
                end += 2
                continue
            if text[end] == quote_character:
                if end + 1 < len(text) and text[end + 1] == quote_character:
                    end += 2
                    continue
                break
            end += 1
        if end >= len(text):
            raise InspectionError("PowerShell string literal is not terminated.")
        body = text[index + 1 : end]
        _mask(masked, index, end + 1)
        if quote_character == '"':
            executable_body = _powershell_subexpression_text(body)
            masked[index + 1 : end] = executable_body
        index = end + 1
    return "".join(masked)


def _powershell_statement_parts(text: str) -> list[tuple[str, str]]:
    """Split statements and retain the separator that introduced each part."""
    parts: list[tuple[str, str]] = []
    current: list[str] = []
    separator = ""
    quote_character: str | None = None
    line_comment = False
    block_comment = False
    index = 0
    while index < len(text):
        character = text[index]
        following = text[index : index + 2]
        if line_comment:
            if character == "\n":
                parts.append((separator, "".join(current)))
                current = []
                separator = character
                line_comment = False
            index += 1
            continue
        if block_comment:
            if following == "#>":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote_character is not None:
            current.append(character)
            if character == "`" and quote_character == '"' and index + 1 < len(text):
                current.append(text[index + 1])
                index += 2
                continue
            if character == quote_character:
                if index + 1 < len(text) and text[index + 1] == quote_character:
                    current.append(text[index + 1])
                    index += 2
                    continue
                quote_character = None
            index += 1
            continue
        if following == "<#":
            block_comment = True
            index += 2
            continue
        if character == "#":
            line_comment = True
            index += 1
            continue
        if character in "'\"":
            quote_character = character
            current.append(character)
            index += 1
            continue
        if character in ";|&\n":
            parts.append((separator, "".join(current)))
            current = []
            separator = character
            index += 1
            continue
        current.append(character)
        index += 1
    parts.append((separator, "".join(current)))
    return parts


def _powershell_statement_segments(text: str) -> list[str]:
    """Split statements without treating quoted or commented separators as syntax."""
    return [segment for _, segment in _powershell_statement_parts(text)]


def _matching_shell_brace(text: str, open_index: int, shell_kind: str) -> int:
    depth = 0
    quote_character: str | None = None
    line_comment = False
    block_comment = False
    index = open_index
    while index < len(text):
        character = text[index]
        following = text[index : index + 2]
        if line_comment:
            if character == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if following == "#>":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote_character is not None:
            if shell_kind == "powershell":
                if quote_character == '"' and character == "`":
                    index += 2
                    continue
                if character == quote_character:
                    if index + 1 < len(text) and text[index + 1] == quote_character:
                        index += 2
                        continue
                    quote_character = None
            else:
                if quote_character == '"' and character == "\\":
                    index += 2
                    continue
                if character == quote_character:
                    quote_character = None
            index += 1
            continue
        if shell_kind == "powershell" and following == "<#":
            block_comment = True
            index += 2
            continue
        if character == "#" and (
            shell_kind == "powershell"
            or index == 0
            or text[index - 1] in " \t\r\n;|&(){}"
        ):
            line_comment = True
            index += 1
            continue
        if character in "'\"":
            quote_character = character
            index += 1
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise InspectionError(
        f"{shell_kind.capitalize()} function or scriptblock is not terminated."
    )


def _shell_function_definitions(
    text: str, shell_kind: str
) -> list[tuple[str, int, int]]:
    if shell_kind == "bash":
        pattern = re.compile(
            r"(?m)(?:^|[;\n])[ \t]*(?:"
            r"function[ \t]+(?P<function_name>[A-Za-z_][A-Za-z0-9_-]*)"
            r"(?:[ \t]*\([ \t]*\))?|"
            r"(?P<posix_name>[A-Za-z_][A-Za-z0-9_-]*)[ \t]*"
            r"\([ \t]*\))[ \t\r\n]*\{"
        )
    else:
        pattern = re.compile(
            r"(?im)(?:^|[;\n])[ \t]*function[ \t]+"
            r"(?P<function_name>[A-Za-z_][A-Za-z0-9_:-]*)[ \t\r\n]*\{"
        )

    definitions: list[tuple[str, int, int]] = []
    search_index = 0
    while match := pattern.search(text, search_index):
        name = cast(
            str,
            match.groupdict().get("function_name")
            or match.groupdict().get("posix_name"),
        )
        open_index = text.rfind("{", match.start(), match.end())
        close_index = _matching_shell_brace(text, open_index, shell_kind)
        definitions.append((name, match.start(), close_index + 1))
        search_index = match.end()
    return definitions


def _bash_function_is_invoked(
    text: str,
    name: str,
    depth: int = 0,
    aliases: dict[str, tuple[str, int]] | None = None,
    expand_aliases: bool = False,
    enabled_line: int = 0,
    fresh_parse: bool = False,
) -> bool:
    if depth > MAX_DYNAMIC_EXECUTION_DEPTH:
        raise InspectionError("Dynamic command nesting exceeds the safety limit.")
    normalized = re.sub(r"\\\r?\n", " ", text).replace("\r\n", "\n")
    lexer = shlex.shlex(
        normalized.replace("\n", ";"), posix=True, punctuation_chars=";&|(){}"
    )
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise InspectionError("Shell command is malformed.") from exc
    if any(
        _bash_function_is_invoked(expansion, name, depth + 1)
        for expansion in _bash_alias_expansions(
            text,
            aliases,
            expand_aliases,
            enabled_line,
            fresh_parse,
            depth,
        )
    ):
        return True
    segment: list[str] = []
    exported = False
    for token in [*tokens, ";"]:
        if token and all(character in ";&|(){}" for character in token):
            direct_words = list(segment)
            while direct_words and (
                direct_words[0] in {"!", "if", "then", "until", "while", "do"}
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", direct_words[0])
            ):
                direct_words.pop(0)
            while direct_words and direct_words[0] in {"coproc", "time"}:
                wrapper = direct_words.pop(0)
                if wrapper == "time":
                    while (
                        direct_words
                        and direct_words[0].startswith("-")
                        and direct_words[0] != "-"
                    ):
                        direct_words.pop(0)
            words = _bash_unwrap_command(segment)
            if direct_words and direct_words[0] == name:
                return True
            if words:
                command = _bash_command_name(words[0])
                if command in {"declare", "export", "typeset"}:
                    exports_functions = any(
                        argument.startswith("-")
                        and argument != "--"
                        and "f" in argument[1:]
                        for argument in words[1:]
                    )
                    if exports_functions and name in words[1:]:
                        exported = True
                trap_handler = _bash_trap_handler(words)
                if (
                    trap_handler is not None
                    and "$" not in trap_handler
                    and "`" not in trap_handler
                    and _bash_function_is_invoked(trap_handler, name, depth + 1)
                ):
                    return True
                if command == "eval" and len(words) > 1:
                    body = " ".join(words[1:])
                    if (
                        "$" not in body
                        and "`" not in body
                        and _bash_function_is_invoked(body, name, depth + 1)
                    ):
                        return True
                command_index = _bash_shell_command_string_index(words)
                if (
                    exported
                    and command_index is not None
                    and command_index < len(words)
                ):
                    body = words[command_index]
                    if (
                        "$" not in body
                        and "`" not in body
                        and _bash_function_is_invoked(body, name, depth + 1)
                    ):
                        return True
            segment = []
        else:
            segment.append(token)
    return False


def _powershell_function_is_invoked(
    text: str, name: str, aliases: dict[str, str | None] | None = None
) -> bool:
    executable_text = _powershell_mask_comments_and_multiline_literals(text)
    command = re.compile(
        rf"(?i)(?:^|[{{}}(=])\s*(?:return\s+)?(?:&\s*|\.\s*)?"
        rf"{re.escape(name)}(?=$|[\s;|)}}])(?!\s*=)"
    )
    if any(
        command.search(segment)
        for segment in _powershell_statement_segments(executable_text)
    ):
        return True
    for target, _ in _powershell_alias_invocations(executable_text, aliases):
        if target is None:
            raise InspectionError(
                "PowerShell alias target is not a static command name."
            )
        if target.casefold() == name.casefold():
            return True
    return False


def _powershell_static_alias_value(value: str) -> str | None:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    if not value or any(marker in value for marker in ("$", "`")):
        return None
    return value


def _powershell_alias_definition(
    segment: str,
) -> tuple[str | None, str | None] | None:
    match = re.match(
        r"(?i)^\s*(?:Set-Alias|sal|New-Alias|nal)(?:\s+|$)(?P<arguments>.*)$",
        segment,
    )
    if match is None:
        return None
    arguments = re.findall(POWERSHELL_ARGUMENT_PATTERN, match.group("arguments"))
    named: dict[str, str] = {}
    positional: list[str] = []
    value_parameters = {"-description", "-option", "-scope"}
    switch_parameters = {"-confirm", "-force", "-passthru", "-whatif"}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        parameter = argument.casefold()
        if parameter in {"-name", "-value"}:
            if index + 1 >= len(arguments):
                return None, None
            named[parameter] = arguments[index + 1]
            index += 2
            continue
        if parameter in value_parameters:
            index += 2
            continue
        if parameter in switch_parameters:
            index += 1
            continue
        if argument.startswith("-"):
            return None, None
        positional.append(argument)
        index += 1

    name_value = named.get("-name")
    target_value = named.get("-value")
    if name_value is None and positional:
        name_value = positional.pop(0)
    if target_value is None and positional:
        target_value = positional.pop(0)
    if name_value is None or target_value is None:
        return None, None
    return (
        _powershell_static_alias_value(name_value),
        _powershell_static_alias_value(target_value),
    )


def _powershell_alias_segments(
    text: str, aliases: dict[str, str | None] | None = None
) -> list[tuple[str, dict[str, str | None]]]:
    removal = re.compile(
        rf"(?i)^\s*(?:Remove-Alias)\s+"
        rf"(?:-Name\s+)?(?P<name>{POWERSHELL_ARGUMENT_PATTERN})"
        r"(?:\s|$)"
    )
    active_aliases = dict(aliases or {})
    executable: list[tuple[str, dict[str, str | None]]] = []
    for segment in _powershell_statement_segments(text):
        stripped = segment.strip()
        definition = _powershell_alias_definition(stripped)
        if definition is not None:
            name, target = definition
            if name is not None:
                active_aliases[name.casefold()] = target
            continue
        if match := removal.match(stripped):
            name = _powershell_static_alias_value(match.group("name"))
            if name is not None:
                active_aliases.pop(name.casefold(), None)
            continue
        executable.append((segment, dict(active_aliases)))
    return executable


def _powershell_resolve_alias(name: str, aliases: dict[str, str | None]) -> str | None:
    resolved = aliases[name]
    seen = {name}
    while resolved is not None and resolved.casefold() in aliases:
        alias_name = resolved.casefold()
        if alias_name in seen:
            raise InspectionError("PowerShell alias chain contains a cycle.")
        seen.add(alias_name)
        resolved = aliases[alias_name]
    return resolved


def _powershell_alias_invocations(
    text: str, aliases: dict[str, str | None] | None = None
) -> list[tuple[str | None, str]]:
    invocations: list[tuple[str | None, str]] = []
    for segment, active_aliases in _powershell_alias_segments(text, aliases):
        normalized = _powershell_normalize_command_positions(segment)
        for name in active_aliases:
            invocation = re.compile(
                rf"(?i)(?:^|[{{}}(])\s*(?:&\s*|\.\s*)?"
                rf"{re.escape(name)}(?=$|[\s;|}}])(?!\s*=)"
            )
            if not invocation.search(normalized):
                continue
            invocations.append(
                (_powershell_resolve_alias(name, active_aliases), normalized)
            )
            break
    return invocations


def _powershell_function_invocation_states(
    text: str, name: str, aliases: dict[str, str | None] | None = None
) -> list[dict[str, str | None]]:
    command = re.compile(
        rf"(?i)(?:^|[{{}}(=])\s*(?:return\s+)?(?:&\s*|\.\s*)?"
        rf"{re.escape(name)}(?=$|[\s;|}}])(?!\s*=)"
    )
    states: list[dict[str, str | None]] = []
    for segment, active_aliases in _powershell_alias_segments(text, aliases):
        normalized = _powershell_normalize_command_positions(segment)
        invoked = bool(command.search(normalized))
        if not invoked:
            for alias_name in active_aliases:
                invocation = re.compile(
                    rf"(?i)(?:^|[{{}}(])\s*(?:&\s*|\.\s*)?"
                    rf"{re.escape(alias_name)}(?=$|[\s;|}}])(?!\s*=)"
                )
                if invocation.search(normalized):
                    target = _powershell_resolve_alias(alias_name, active_aliases)
                    if target is None:
                        raise InspectionError(
                            "PowerShell alias target is not a static command name."
                        )
                    invoked = target.casefold() == name.casefold()
                    break
        if invoked:
            states.append(active_aliases)
    return states


def _powershell_contains_codeql_alias(text: str) -> bool:
    subcommand = re.compile(
        r"(?i)(?:^|[{}(])\s*(?:&\s*|\.\s*)?[^\s;|}]+\s+"
        r"(?:database|github|pack|query|resolve|execute|bqrs|dataset)\b"
    )

    def contains_alias_invocation(
        body: str, aliases: dict[str, str | None] | None = None
    ) -> bool:
        for target, invocation in _powershell_alias_invocations(body, aliases):
            if target is None:
                raise InspectionError(
                    "PowerShell alias target is not a static command name."
                )
            if _bash_command_name(target) in {
                "codeql",
                "codeql.exe",
            } and subcommand.search(invocation):
                return True
        return False

    definitions = _shell_function_definitions(text, "powershell")
    if not definitions:
        return contains_alias_invocation(text)
    top_level = list(text)
    for _, start, end in definitions:
        _mask(top_level, start, end)
    top_level_text = "".join(top_level)
    if contains_alias_invocation(top_level_text):
        return True
    for _, body, aliases in _powershell_reachable_function_calls(
        text, definitions, top_level_text
    ):
        if contains_alias_invocation(body, aliases):
            return True
    return False


def _powershell_normalize_command_positions(text: str) -> str:
    assignment_target = (
        r"(?:\[[A-Za-z_][A-Za-z0-9_.\[\], ]*\][ \t]*)?"
        r"\$(?:\{[^}\r\n]+\}|[A-Za-z_][A-Za-z0-9_:]*)"
        r"(?:(?:\.[A-Za-z_][A-Za-z0-9_]*)|(?:\[[^\]\r\n]+\]))*"
    )
    prefix = re.compile(
        r"(?im)(^|[;|&{}\n])([ \t]*)(?:"
        r"return\b[ \t]+|"
        rf"{assignment_target}[ \t]*=[ \t]*"
        r")",
    )
    return prefix.sub(r"\1\2", text)


def _mask_uninvoked_functions(text: str, shell_kind: str) -> str:
    definitions = _shell_function_definitions(text, shell_kind)
    if not definitions:
        return text
    top_level = list(text)
    for _, start, end in definitions:
        _mask(top_level, start, end)
    top_level_text = "".join(top_level)
    if shell_kind == "powershell":
        return _mask_uninvoked_powershell_functions(text, definitions, top_level_text)

    reachable = {
        name
        for name, _, _ in definitions
        if _bash_function_is_invoked(top_level_text, name)
    }
    while True:
        discovered = set(reachable)
        for source_name, start, end in definitions:
            if source_name not in reachable:
                continue
            open_index = text.find("{", start, end)
            body_text = list(text)
            for _, nested_start, nested_end in definitions:
                if start < nested_start and nested_end <= end:
                    _mask(body_text, nested_start, nested_end)
            body = "".join(body_text[open_index + 1 : end - 1])
            aliases, expand_aliases, enabled_line = _bash_alias_state(
                top_level_text[:start]
            )
            discovered.update(
                target_name
                for target_name, _, _ in definitions
                if _bash_function_is_invoked(
                    body,
                    target_name,
                    aliases=aliases,
                    expand_aliases=expand_aliases,
                    enabled_line=enabled_line,
                    fresh_parse=True,
                )
            )
        if discovered == reachable:
            break
        reachable = discovered

    masked = list(text)
    for name, start, end in definitions:
        if name not in reachable:
            _mask(masked, start, end)
    return "".join(masked)


def _mask_uninvoked_powershell_functions(
    text: str,
    definitions: list[tuple[str, int, int]],
    top_level_text: str,
) -> str:
    reachable = {
        name.casefold()
        for name, _, _ in _powershell_reachable_function_calls(
            text, definitions, top_level_text
        )
    }

    masked = list(text)
    for name, start, end in definitions:
        if name.casefold() not in reachable:
            _mask(masked, start, end)
    return "".join(masked)


def _powershell_reachable_function_calls(
    text: str,
    definitions: list[tuple[str, int, int]],
    top_level_text: str,
) -> list[tuple[str, str, dict[str, str | None]]]:
    definition_by_name = {
        name.casefold(): (name, start, end) for name, start, end in definitions
    }
    pending: list[tuple[str, dict[str, str | None]]] = []
    for name, _, _ in definitions:
        pending.extend(
            (name, aliases)
            for aliases in _powershell_function_invocation_states(top_level_text, name)
        )

    calls: list[tuple[str, str, dict[str, str | None]]] = []
    visited: set[tuple[str, tuple[tuple[str, str | None], ...]]] = set()
    while pending:
        source_name, aliases = pending.pop()
        state_key = (source_name.casefold(), tuple(sorted(aliases.items())))
        if state_key in visited:
            continue
        visited.add(state_key)
        _, start, end = definition_by_name[source_name.casefold()]
        open_index = text.find("{", start, end)
        body_text = list(text)
        for _, nested_start, nested_end in definitions:
            if start < nested_start and nested_end <= end:
                _mask(body_text, nested_start, nested_end)
        body = "".join(body_text[open_index + 1 : end - 1])
        calls.append((source_name, body, aliases))
        for target_name, _, _ in definitions:
            pending.extend(
                (target_name, target_aliases)
                for target_aliases in _powershell_function_invocation_states(
                    body, target_name, aliases
                )
            )

    return calls


def _powershell_contains_quoted_codeql_command(text: str) -> bool:
    command = re.compile(
        r"(?i)^\s*(?:"
        r'"[^"\r\n]*(?:[/\\])?codeql(?:\.exe)?"|'
        r"'[^'\r\n]*(?:[/\\])?codeql(?:\.exe)?'"
        r")\s+(?:database|github|pack|query|resolve|execute|bqrs|dataset)\b"
    )
    return any(
        separator == "&" and command.search(segment)
        for separator, segment in _powershell_statement_parts(text)
    )


def _powershell_quoted_body(text: str) -> str | None:
    text = text.lstrip()
    if not text or text[0] not in "'\"":
        return None
    quote_character = text[0]
    body: list[str] = []
    index = 1
    while index < len(text):
        character = text[index]
        if quote_character == '"' and character == "`" and index + 1 < len(text):
            body.extend((character, text[index + 1]))
            index += 2
            continue
        if character == quote_character:
            if index + 1 < len(text) and text[index + 1] == quote_character:
                body.extend((character, text[index + 1]))
                index += 2
                continue
            if text[index + 1 :].strip():
                return None
            return "".join(body)
        body.append(character)
        index += 1
    raise InspectionError("PowerShell dynamic command string is not terminated.")


def _powershell_dynamic_execution_bodies(text: str) -> list[str]:
    bodies: list[str] = []
    for separator, segment in _powershell_statement_parts(text):
        stripped = segment.strip()
        dot_sourced = stripped.startswith(". ")
        executable = stripped[2:].lstrip() if dot_sourced else stripped
        invoke_match = re.match(
            r"(?i)^(?:Invoke-Expression|iex)(?:\s+-Command)?(?:\s+|$)", stripped
        )
        if invoke_match:
            remainder = stripped[invoke_match.end() :]
            body = _powershell_quoted_body(remainder)
            if body is not None:
                bodies.append(body)
            elif remainder or separator == "|":
                raise InspectionError(
                    "PowerShell dynamic command has a non-literal payload."
                )
            continue

        shell_match = re.match(r"(?i)^(?:pwsh|powershell)(?:\.exe)?\b", stripped)
        if shell_match:
            command_match = re.search(r"(?i)(?:^|\s)-(?:c|command)\s+", stripped)
            if command_match:
                remainder = stripped[command_match.end() :]
                body = _powershell_quoted_body(remainder)
                if body is not None:
                    bodies.append(body)
                else:
                    raise InspectionError(
                        "PowerShell dynamic command has a non-literal payload."
                    )
            continue

        invoked_scriptblock_create = (
            re.fullmatch(
                r"(?is)\(\s*\[scriptblock\]::Create\s*\((.*)\)\s*\)",
                executable,
            )
            if dot_sourced or separator == "&"
            else None
        )
        if invoked_scriptblock_create:
            body = _powershell_quoted_body(invoked_scriptblock_create.group(1))
            if body is None:
                raise InspectionError(
                    "PowerShell dynamic command has a non-literal payload."
                )
            bodies.append(body)
            continue

        scriptblock_create = re.match(
            r"(?is)^\[scriptblock\]::Create\s*\((.*)\)\s*"
            + (r"$" if dot_sourced else r"\.\s*Invoke(?:ReturnAsIs)?\s*\("),
            executable,
        )
        if scriptblock_create:
            body = _powershell_quoted_body(scriptblock_create.group(1))
            if body is None:
                raise InspectionError(
                    "PowerShell dynamic command has a non-literal payload."
                )
            bodies.append(body)
            continue

        scriptblock_text = executable
        if (separator == "&" or dot_sourced) and scriptblock_text.startswith("{"):
            close_index = _matching_shell_brace(scriptblock_text, 0, "powershell")
            bodies.append(scriptblock_text[1:close_index])
    return bodies


def _powershell_contains_dynamic_codeql(
    text: str, dynamic_execution_depth: int
) -> bool:
    return any(
        contains_codeql_cli(body, "powershell", dynamic_execution_depth + 1)
        for body in _powershell_dynamic_execution_bodies(text)
    )


def _powershell_contains_start_process_codeql(text: str) -> bool:
    normalized = re.sub(r"`\r?\n", " ", text).replace("\r\n", "\n")
    return any(
        POWERSHELL_START_PROCESS_CODEQL.search(segment)
        for segment in _powershell_statement_segments(normalized)
    )


def _powershell_contains_cmd_codeql(text: str) -> bool:
    command = re.compile(
        r"(?i)^\s*\"?(?:"
        r'"[^"\r\n]*(?:[/\\])?codeql(?:\.exe)?"|'
        r"'[^'\r\n]*(?:[/\\])?codeql(?:\.exe)?'|"
        r"(?:[^\s\"']+[/\\])?codeql(?:\.exe)?"
        r")\"?\s+(?:database|github|pack|query|resolve|execute|bqrs|dataset)\b"
    )
    cmd_shell = re.compile(
        r"(?i)^\s*(?:cmd|cmd\.exe)\b[^;|\r\n]*?\s/(?:c|k)(?:\s+|$)(?P<body>.*)$"
    )
    return any(
        (match := cmd_shell.search(segment)) is not None
        and command.search(match.group("body")) is not None
        for segment in _powershell_statement_segments(text)
    )


def _powershell_dynamic_execution_is_unresolved(text: str) -> bool:
    normalized = re.sub(r"`\r?\n", " ", text).replace("\r\n", "\n")
    encoded_command = re.compile(
        r"(?i)^\s*(?:pwsh|powershell)(?:\.exe)?\b[^;|\r\n]*"
        r"\s-(?:e|ec|enc|enco|encod|encode|encoded|encodedc|encodedco|"
        r"encodedcom|encodedcomm|encodedcomma|encodedcomman|encodedcommand)\s+"
    )
    start_process = re.compile(r"(?i)^\s*(?:Start-Process|saps|start)\s+")
    cmd_shell = re.compile(
        r"(?i)^\s*(?:cmd|cmd\.exe)\b[^;|\r\n]*?\s/(?:c|k)(?:\s+|$)(?P<body>.*)$"
    )
    for separator, segment in _powershell_statement_parts(normalized):
        stripped = segment.strip()
        if encoded_command.search(stripped):
            return True
        process_match = start_process.search(stripped)
        if process_match and re.search(
            r'(?:^|\s|=)(?:"?\$|\()', stripped[process_match.end() :]
        ):
            return True
        cmd_match = cmd_shell.search(stripped)
        if cmd_match and re.search(
            r"(?i)(?:[%!$`]|\bcodeql(?:\.exe)?\b)", cmd_match.group("body")
        ):
            return True
        if separator == "&" and re.match(r'^(?:\$|"\$)', stripped):
            return True
        if separator == "&" and stripped.startswith("("):
            scriptblock_create = re.fullmatch(
                r"(?is)\(\s*\[scriptblock\]::Create\s*\((.*)\)\s*\)",
                stripped,
            )
            if (
                scriptblock_create is None
                or _powershell_quoted_body(scriptblock_create.group(1)) is None
            ):
                return True
        if stripped.startswith(". "):
            dot_target = stripped[2:].lstrip()
            if re.match(r"^(?:\$|\"\$)", dot_target):
                return True
            if dot_target.startswith("("):
                scriptblock_create = re.fullmatch(
                    r"(?is)\(\s*\[scriptblock\]::Create\s*\((.*)\)\s*\)",
                    dot_target,
                )
                if (
                    scriptblock_create is None
                    or _powershell_quoted_body(scriptblock_create.group(1)) is None
                ):
                    return True
    return False


def _configured_shell(defaults: object) -> str | None:
    if not isinstance(defaults, dict):
        return None
    run_defaults = defaults.get("run")
    if not isinstance(run_defaults, dict):
        return None
    shell = run_defaults.get("shell")
    return shell if isinstance(shell, str) else None


def _shell_kind(shell: str | None, runs_on: object) -> str:
    if shell is not None:
        command = shell.strip().split(maxsplit=1)[0].strip("'\"").replace("\\", "/")
        executable = command.rsplit("/", 1)[-1].casefold()
        if executable in {"bash", "bash.exe", "sh", "sh.exe", "zsh", "zsh.exe"}:
            return "bash"
        if executable in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
            return "powershell"
        return "unknown"

    labels = [runs_on] if isinstance(runs_on, str) else runs_on
    if not isinstance(labels, list) or not all(
        isinstance(label, str) for label in labels
    ):
        return "unknown"
    normalized = [label.casefold() for label in labels]
    if any(label == "windows" or label.startswith("windows-") for label in normalized):
        return "powershell"
    if any(
        label in {"linux", "macos"}
        or label.startswith("ubuntu-")
        or label.startswith("macos-")
        for label in normalized
    ):
        return "bash"
    return "unknown"


def shell_executable_text(text: str, shell_kind: str) -> str:
    if shell_kind == "bash":
        return _bash_executable_text(text)
    if shell_kind == "powershell":
        return _powershell_executable_text(text)
    return text


def _contains_direct_codeql(text: str) -> bool:
    for segment in text.splitlines():
        if "codeql" not in segment.casefold():
            continue
        if len(segment.encode("utf-8")) > MAX_CODEQL_COMMAND_SEGMENT_BYTES:
            raise InspectionError(
                "Shell command segment containing CodeQL exceeds the safety limit."
            )
        if CODEQL_CLI.search(segment):
            return True
    return False


def contains_codeql_cli(
    text: str, shell_kind: str, dynamic_execution_depth: int = 0
) -> bool:
    if dynamic_execution_depth > MAX_DYNAMIC_EXECUTION_DEPTH:
        raise InspectionError("Dynamic command nesting exceeds the safety limit.")
    size = len(text.encode("utf-8"))
    if size > MAX_SHELL_RUN_BYTES:
        raise InspectionError(
            f"Workflow run step exceeds the {MAX_SHELL_RUN_BYTES}-byte safety cap."
        )
    if shell_kind not in {"bash", "powershell"}:
        raise InspectionError(
            "Workflow run step uses an unsupported shell; direct CodeQL inspection "
            "cannot complete safely."
        )
    executable_text = shell_executable_text(text, shell_kind)
    executable_text = _mask_uninvoked_functions(executable_text, shell_kind)
    searchable_text = executable_text
    if shell_kind == "powershell":
        searchable_text = _powershell_mask_comments_and_multiline_literals(
            executable_text
        )
        searchable_text = _powershell_normalize_command_positions(searchable_text)
    if _contains_direct_codeql(searchable_text):
        return True
    if shell_kind == "bash":
        if _bash_contains_wrapped_codeql(executable_text, dynamic_execution_depth):
            return True
        dynamic_text = _mask_uninvoked_functions(text, "bash")
        if _bash_dynamic_execution_is_unresolved(dynamic_text):
            raise InspectionError("Shell dynamic command has a non-literal payload.")
        if any(
            contains_codeql_cli(expansion, "bash", dynamic_execution_depth + 1)
            for expansion in _bash_alias_expansions(dynamic_text)
        ):
            return True
        if any(
            contains_codeql_cli(body, "bash", dynamic_execution_depth + 1)
            for body in _bash_dynamic_execution_bodies(dynamic_text)
        ):
            return True
        return False
    dynamic_text = _mask_uninvoked_functions(text, "powershell")
    if _powershell_contains_cmd_codeql(executable_text):
        return True
    if _powershell_contains_codeql_alias(executable_text):
        return True
    if _powershell_dynamic_execution_is_unresolved(dynamic_text):
        raise InspectionError("PowerShell dynamic command has a non-literal payload.")
    return (
        _powershell_contains_start_process_codeql(executable_text)
        or _powershell_contains_dynamic_codeql(executable_text, dynamic_execution_depth)
        or _powershell_contains_quoted_codeql_command(executable_text)
    )


def is_direct_workflow_path(path: str) -> bool:
    workflow_path = PurePosixPath(path)
    return workflow_path.parent == PurePosixPath(
        ".github/workflows"
    ) and workflow_path.suffix.lower() in {".yml", ".yaml"}


def parse_workflow(
    text: str,
    source: str,
    check_deadline: Callable[[], None] | None = None,
) -> WorkflowSignals:
    if check_deadline is not None:
        check_deadline()
    size = len(text.encode("utf-8"))
    if size > MAX_WORKFLOW_BYTES:
        raise InspectionError(
            f"Workflow {source!r} exceeds the {MAX_WORKFLOW_BYTES}-byte safety cap."
        )
    try:
        loader = UniqueKeyBaseLoader(text)
        try:
            document = loader.get_single_data()
        finally:
            loader.dispose()
    except (yaml.YAMLError, InspectionError, RecursionError) as exc:
        raise InspectionError(f"Could not parse workflow {source!r}: {exc}") from exc
    if check_deadline is not None:
        check_deadline()
    if not isinstance(document, dict):
        raise InspectionError(f"Workflow {source!r} is not a YAML mapping.")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise InspectionError(f"Workflow {source!r} has no non-empty jobs mapping.")

    has_advanced_setup = False
    reusable_calls: list[str] = []
    workflow_shell = _configured_shell(document.get("defaults"))
    for job_name, job in jobs.items():
        if check_deadline is not None:
            check_deadline()
        if not isinstance(job, dict):
            raise InspectionError(
                f"Workflow {source!r} job {job_name!r} is not a mapping."
            )
        job_uses = job.get("uses")
        if job_uses is not None:
            if not isinstance(job_uses, str) or not (
                LOCAL_CALL.fullmatch(job_uses) or EXTERNAL_CALL.fullmatch(job_uses)
            ):
                raise InspectionError(
                    f"Workflow {source!r} has an unsupported reusable-workflow reference."
                )
            reusable_calls.append(job_uses)

        job_shell = _configured_shell(job.get("defaults")) or workflow_shell
        runs_on = job.get("runs-on")
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            raise InspectionError(
                f"Workflow {source!r} job {job_name!r} steps is not a list."
            )
        for index, step in enumerate(steps):
            if check_deadline is not None:
                check_deadline()
            if not isinstance(step, dict):
                raise InspectionError(
                    f"Workflow {source!r} job {job_name!r} step {index} is not a mapping."
                )
            step_uses = step.get("uses")
            if isinstance(step_uses, str) and CODEQL_ACTION.fullmatch(step_uses):
                has_advanced_setup = True
            run = step.get("run")
            if isinstance(run, str):
                step_shell = step.get("shell")
                if step_shell is not None and not isinstance(step_shell, str):
                    raise InspectionError(
                        f"Workflow {source!r} job {job_name!r} step {index} shell is not a string."
                    )
                shell_kind = _shell_kind(step_shell or job_shell, runs_on)
                if contains_codeql_cli(run, shell_kind):
                    has_advanced_setup = True
                if check_deadline is not None:
                    check_deadline()

    return WorkflowSignals(has_advanced_setup, tuple(reusable_calls))


def is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def require_safe_root(root: Path) -> None:
    if not root.is_absolute():
        raise InspectionError("Repository root must be an absolute path.")

    anchor = Path(root.anchor)
    if root == anchor:
        raise InspectionError("Refusing to inspect a filesystem root.")

    current = anchor
    for component in root.parts[len(anchor.parts) :]:
        current = current / component
        if not os.path.lexists(current):
            raise InspectionError(f"Repository root does not exist: {root}")
        if (
            current.is_symlink()
            or is_reparse_point(current)
            or os.path.ismount(current)
        ):
            raise InspectionError(
                f"Refusing to inspect linked, mounted, or reparse-point "
                f"repository root: {current}"
            )


def require_safe_path(path: Path, root: Path) -> None:
    require_safe_root(root)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise InspectionError(f"Path escapes repository root: {path}") from exc
    current = root
    for component in relative.parts:
        current = current / component
        if not os.path.lexists(current):
            return
        if (
            current.is_symlink()
            or is_reparse_point(current)
            or os.path.ismount(current)
        ):
            raise InspectionError(
                f"Refusing to inspect linked, mounted, or reparse-point path: {current}"
            )


def load_local_workflows(
    repo_root: Path, budget: WorkflowByteBudget | None = None
) -> dict[str, WorkflowSignals]:
    workflow_root = repo_root / ".github" / "workflows"
    require_safe_path(workflow_root, repo_root)
    if not workflow_root.exists():
        return {}
    if not workflow_root.is_dir():
        raise InspectionError(f"Workflow root is not a directory: {workflow_root}")

    budget = budget or WorkflowByteBudget()
    workflows: dict[str, WorkflowSignals] = {}
    directory_entries = 0
    for path in workflow_root.iterdir():
        directory_entries += 1
        if directory_entries > MAX_LOCAL_DIRECTORY_ENTRIES:
            raise InspectionError(
                "Local workflow directory entry count exceeded the "
                f"{MAX_LOCAL_DIRECTORY_ENTRIES}-entry safety cap."
            )
        require_safe_path(path, repo_root)
        if not path.is_file() or path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        key = path.relative_to(repo_root).as_posix()
        try:
            if path.stat().st_size > MAX_WORKFLOW_BYTES:
                raise InspectionError(
                    f"Workflow 'local:{key}' exceeds the "
                    f"{MAX_WORKFLOW_BYTES}-byte safety cap."
                )
            raw = path.read_bytes()
            if len(raw) > MAX_WORKFLOW_BYTES:
                raise InspectionError(
                    f"Workflow 'local:{key}' exceeds the "
                    f"{MAX_WORKFLOW_BYTES}-byte safety cap."
                )
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InspectionError(
                f"Workflow 'local:{key}' is not valid UTF-8."
            ) from exc
        except OSError as exc:
            raise InspectionError(f"Could not read workflow 'local:{key}'.") from exc
        workflows[key] = budget.parse(text, f"local:{key}")
        if len(workflows) > MAX_LOCAL_WORKFLOWS:
            raise InspectionError(
                f"Local workflow count exceeded the {MAX_LOCAL_WORKFLOWS}-file safety cap."
            )
    return workflows


class WorkflowResolver:
    def __init__(
        self,
        client: GitHubClient,
        local_workflows: dict[str, WorkflowSignals],
        budget: WorkflowByteBudget | None = None,
    ) -> None:
        self.client = client
        self.local_workflows = local_workflows
        self.budget = budget or WorkflowByteBudget()
        self.exact_workflows: dict[tuple[str, str, str, str], WorkflowSignals] = {}
        self.ref_cache: dict[tuple[str, str, str], str] = {}

    def seed_exact_workflow(
        self, owner: str, repo: str, commit: str, path: str, signals: WorkflowSignals
    ) -> None:
        self.exact_workflows[
            (owner.casefold(), repo.casefold(), commit.lower(), path)
        ] = signals

    def _load_exact_workflow(
        self, owner: str, repo: str, commit: str, path: str
    ) -> WorkflowSignals:
        cache_key = (owner.casefold(), repo.casefold(), commit.lower(), path)
        cached = self.exact_workflows.get(cache_key)
        if cached is not None:
            return cached
        encoded_path = "/".join(
            quote(component, safe="") for component in path.split("/")
        )
        endpoint = (
            f"repos/{owner}/{repo}/contents/{encoded_path}?ref={quote(commit, safe='')}"
        )
        signals = self.budget.parse(
            self.client.raw(endpoint), f"external:{owner}/{repo}/{path}@{commit}"
        )
        self.exact_workflows[cache_key] = signals
        return signals

    def resolve(self, call: str, context: WorkflowContext) -> WorkflowNode:
        local_match = LOCAL_CALL.fullmatch(call)
        if local_match:
            path = str(PurePosixPath(local_match.group("path")))
            if ".." in PurePosixPath(path).parts:
                raise InspectionError(
                    f"Reusable workflow path contains traversal: {call}"
                )
            if not is_direct_workflow_path(path):
                raise InspectionError(
                    f"Reusable workflow is not a direct workflow file: {call}"
                )
            if context.kind == "local":
                signals = self.local_workflows.get(path)
                if signals is None:
                    raise InspectionError(
                        f"Local reusable workflow does not exist: {call}"
                    )
                return WorkflowNode(("local", path), f"local:{path}", context, signals)
            signals = self._load_exact_workflow(
                context.owner, context.repo, context.commit, path
            )
            identity = (
                "exact",
                context.owner.casefold(),
                context.repo.casefold(),
                context.commit.lower(),
                path,
            )
            return WorkflowNode(
                identity,
                f"external:{context.owner}/{context.repo}/{path}@{context.commit}",
                context,
                signals,
            )

        external_match = EXTERNAL_CALL.fullmatch(call)
        if not external_match:
            raise InspectionError(f"Unsupported reusable-workflow reference: {call}")
        owner = external_match.group("owner")
        repo = external_match.group("repo")
        if owner in {".", ".."} or repo in {".", ".."}:
            raise InspectionError(
                f"Reusable workflow has an invalid repository identifier: {call}"
            )
        path = str(PurePosixPath(external_match.group("path")))
        reference = external_match.group("ref")
        if ".." in PurePosixPath(path).parts:
            raise InspectionError(f"Reusable workflow path contains traversal: {call}")
        if not is_direct_workflow_path(path):
            raise InspectionError(
                f"Reusable workflow is not a direct workflow file: {call}"
            )
        ref_key = (owner.casefold(), repo.casefold(), reference)
        commit = self.ref_cache.get(ref_key)
        if commit is None:
            endpoint = f"repos/{owner}/{repo}/commits/{quote(reference, safe='')}"
            response = self.client.json(endpoint)
            commit = response.get("sha") if isinstance(response, dict) else None
            if not isinstance(commit, str) or not FULL_OBJECT_ID.fullmatch(commit):
                raise InspectionError(
                    f"Reusable workflow did not resolve to a full object ID: {call}"
                )
            self.ref_cache[ref_key] = commit
        signals = self._load_exact_workflow(owner, repo, commit, path)
        exact_context = WorkflowContext("exact", owner, repo, commit)
        identity = ("exact", owner.casefold(), repo.casefold(), commit.lower(), path)
        return WorkflowNode(
            identity,
            f"external:{owner}/{repo}/{path}@{commit}",
            exact_context,
            signals,
        )


def load_remote_default_branch(
    client: GitHubClient,
    owner: str,
    repo: str,
    default_branch: str,
    budget: WorkflowByteBudget | None = None,
) -> tuple[str, dict[str, WorkflowSignals]]:
    budget = budget or WorkflowByteBudget()
    commit_response = client.json(
        f"repos/{owner}/{repo}/commits/{quote(default_branch, safe='')}"
    )
    commit = commit_response.get("sha") if isinstance(commit_response, dict) else None
    if not isinstance(commit, str) or not FULL_OBJECT_ID.fullmatch(commit):
        raise InspectionError("Default branch did not resolve to a full Git object ID.")
    tree = client.json(f"repos/{owner}/{repo}/git/trees/{commit}?recursive=1")
    if not isinstance(tree, dict) or tree.get("truncated") is not False:
        raise InspectionError("Default-branch tree is missing, invalid, or truncated.")
    items = tree.get("tree")
    if not isinstance(items, list):
        raise InspectionError("Default-branch tree has no tree array.")
    workflow_items = [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("type") == "blob"
        and isinstance(item.get("path"), str)
        and is_direct_workflow_path(item["path"])
    ]
    if len(workflow_items) > MAX_REMOTE_WORKFLOWS:
        raise InspectionError(
            f"Remote workflow count exceeded the {MAX_REMOTE_WORKFLOWS}-file safety cap."
        )
    workflows: dict[str, WorkflowSignals] = {}
    for item in workflow_items:
        blob = item.get("sha")
        if not isinstance(blob, str) or not FULL_OBJECT_ID.fullmatch(blob):
            raise InspectionError(
                f"Remote workflow has invalid blob ID: {item['path']}"
            )
        text = client.raw(f"repos/{owner}/{repo}/git/blobs/{blob}")
        workflows[item["path"]] = budget.parse(text, f"remote:{item['path']}@{commit}")
    return commit, workflows


def inspect_root(
    root_name: str,
    root_identity: tuple[str, ...],
    signals: WorkflowSignals,
    context: WorkflowContext,
    resolver: WorkflowResolver,
    check_deadline: Callable[[], None] | None = None,
) -> list[str]:
    advanced = [root_name] if signals.has_advanced_setup else []
    resolved: set[tuple[str, ...]] = set()
    maximum_level: dict[tuple[str, ...], int] = {root_identity: 1}
    reference_count = 0

    def visit(
        call: str,
        caller_context: WorkflowContext,
        level: int,
        active: frozenset[tuple[str, ...]],
    ) -> None:
        nonlocal reference_count
        if check_deadline is not None:
            check_deadline()
        reference_count += 1
        if reference_count > MAX_REUSABLE_REFERENCES_PER_ROOT:
            raise InspectionError(
                f"Reusable-workflow tree for {root_name!r} exceeds the per-root "
                f"{MAX_REUSABLE_REFERENCES_PER_ROOT}-edge traversal safety limit."
            )
        if level > MAX_WORKFLOW_LEVELS:
            raise InspectionError(
                f"Reusable-workflow tree for {root_name!r} exceeds {MAX_WORKFLOW_LEVELS} levels."
            )
        node = resolver.resolve(call, caller_context)
        if node.identity in active:
            raise InspectionError(
                f"Reusable-workflow tree for {root_name!r} contains a call cycle at "
                f"{node.display_name!r}."
            )
        if node.identity not in resolved:
            if len(resolved) >= MAX_REUSABLE_WORKFLOWS_PER_ROOT:
                raise InspectionError(
                    f"Reusable-workflow tree for {root_name!r} exceeds "
                    f"{MAX_REUSABLE_WORKFLOWS_PER_ROOT} unique called workflows."
                )
            resolved.add(node.identity)
        previous_level = maximum_level.get(node.identity)
        if previous_level is not None and level <= previous_level:
            return
        maximum_level[node.identity] = level
        if node.signals.has_advanced_setup:
            advanced.append(node.display_name)
        next_active = active | {node.identity}
        for nested_call in node.signals.reusable_calls:
            visit(nested_call, node.context, level + 1, next_active)

    root_active = frozenset({root_identity})
    for call in signals.reusable_calls:
        visit(call, context, 2, root_active)
    return advanced


def split_repository(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if (
        len(parts) != 2
        or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
        or any(part in {".", ".."} for part in parts)
    ):
        raise InspectionError("Repository must be an explicit OWNER/REPO identifier.")
    return parts[0], parts[1]


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("CodeQL preflight supports GitHub.com only.")
    owner, repo = split_repository(args.repository)
    repo_root = Path(os.path.abspath(os.fspath(args.repo_root)))
    require_safe_root(repo_root)
    if not repo_root.is_dir():
        raise InspectionError("Repository root is not a directory.")
    client = GitHubClient(args.hostname, forbidden_root=repo_root)

    current_setup = client.json(f"repos/{owner}/{repo}/code-scanning/default-setup")
    if not isinstance(current_setup, dict) or not isinstance(
        current_setup.get("state"), str
    ):
        raise InspectionError("Default-setup endpoint returned an invalid response.")
    if current_setup["state"] not in {"configured", "not-configured"}:
        raise InspectionError("Default-setup endpoint returned an unknown state.")
    if current_setup["state"] == "configured":
        return {
            "inspection_complete": True,
            "decision": "preserve-default-setup",
            "default_setup_state": current_setup["state"],
            "advanced_workflows": None,
            "has_codeql_analysis": None,
            "workflow_inspection_performed": False,
            "analysis_inspection_performed": False,
            "github_api_requests": client.request_count,
        }

    budget = WorkflowByteBudget(deadline=client.deadline)
    local_workflows = load_local_workflows(repo_root, budget)
    remote_commit, remote_workflows = load_remote_default_branch(
        client, owner, repo, args.default_branch, budget
    )
    resolver = WorkflowResolver(client, local_workflows, budget)
    for path, signals in remote_workflows.items():
        resolver.seed_exact_workflow(owner, repo, remote_commit, path, signals)

    advanced: list[str] = []
    local_context = WorkflowContext("local")
    for path, signals in local_workflows.items():
        advanced.extend(
            inspect_root(
                f"local:{path}",
                ("local", path),
                signals,
                local_context,
                resolver,
                budget.check_deadline,
            )
        )
    remote_context = WorkflowContext("exact", owner, repo, remote_commit)
    for path, signals in remote_workflows.items():
        advanced.extend(
            inspect_root(
                f"remote:{path}@{remote_commit}",
                (
                    "exact",
                    owner.casefold(),
                    repo.casefold(),
                    remote_commit.lower(),
                    path,
                ),
                signals,
                remote_context,
                resolver,
                budget.check_deadline,
            )
        )

    analyses = client.json(
        f"repos/{owner}/{repo}/code-scanning/analyses?tool_name=CodeQL&per_page=1"
    )
    if not isinstance(analyses, list):
        raise InspectionError("CodeQL analyses endpoint returned an invalid response.")
    has_analysis = bool(analyses)
    advanced = sorted(set(advanced))
    if not args.confirm_no_external_codeql:
        raise InspectionError(
            "Absence of external CI, indirect scripts, local actions, composite actions, "
            "and other CodeQL upload processes was not explicitly confirmed."
        )
    decision = (
        "require-explicit-switch-confirmation"
        if advanced or has_analysis
        else "may-offer-default-setup"
    )
    return {
        "inspection_complete": True,
        "decision": decision,
        "default_setup_state": current_setup["state"],
        "advanced_workflows": advanced,
        "has_codeql_analysis": has_analysis,
        "workflow_inspection_performed": True,
        "analysis_inspection_performed": True,
        "external_codeql_absence_confirmed": bool(args.confirm_no_external_codeql),
        "github_api_requests": client.request_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--confirm-no-external-codeql", action="store_true")
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
