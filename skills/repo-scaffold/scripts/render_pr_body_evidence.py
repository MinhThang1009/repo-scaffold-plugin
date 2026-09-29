#!/usr/bin/env python3
"""Render a deterministic pull-request body from immutable GitHub evidence."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any


MAX_BODY_BYTES = 1 * 1024 * 1024
MAX_JSON_BYTES = 10 * 1024 * 1024
MAX_COMMITS = 250
MAX_FILES = 3000
MAX_TEXT = 2048
MAX_COMMIT_TEXT = 16_384
MAX_KEY_CHANGES = 5
SUMMARY_BASE_PATTERN = re.compile(
    r"(?m)^PR-summary-base:[ \t]*([0-9a-fA-F]{40})[ \t]*$"
)
HEAD_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TEMPLATE_MARKER_PATTERN = re.compile(
    r"^\ufeff?<!-- repo-scaffold:pr-template=([a-z][a-z0-9-]*) -->[ \t]*$",
    re.MULTILINE,
)
MANAGED_BODY_MARKER = "<!-- repo-scaffold:pr-body-managed -->"
HEAD_START_MARKER = "<!-- repo-scaffold:pr-head:start -->"
HEAD_END_MARKER = "<!-- repo-scaffold:pr-head:end -->"
HEADING_PATTERN = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
REQUIRED_START = "<!-- repo-scaffold:required-checklist:start -->"
OPTIONAL_START = "<!-- repo-scaffold:optional-checklist:start -->"
ISSUE_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:#[0-9]+|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[0-9]+)"
)
COMMIT_PREFIX_PATTERN = re.compile(
    r"^(?:feat|fix|docs|test|chore|perf|refactor|style|build|ci)"
    r"(?:\([^()\r\n]+\))?!?:\s*",
    re.IGNORECASE,
)
STRUCTURED_COMMIT_FIELD_PATTERN = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+)?(Why|Purpose|Root cause|Cause|Changes|Key changes|"
    r"Verification|Tests?)(?:[ \t]*:[ \t]*(.*)|[ \t]*)$",
    re.IGNORECASE,
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON members before interpreting untrusted API data."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _read_bytes(path: Path, label: str, limit: int) -> bytes:
    try:
        with path.open("rb") as source:
            payload = source.read(limit + 1)
    except OSError as error:
        raise ValueError(f"could not read {label}: {error}") from error
    if len(payload) > limit:
        raise ValueError(f"{label} exceeds the {limit}-byte safety cap")
    return payload


def read_body(path: Path) -> str:
    """Read one bounded UTF-8 body."""
    payload = _read_bytes(path, "pull-request body", MAX_BODY_BYTES)
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("pull-request body is not valid UTF-8") from error


def _read_json(path: Path, label: str) -> Any:
    payload = _read_bytes(path, label, MAX_JSON_BYTES)
    try:
        return json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not valid JSON: {error}") from error


def _line_ending(body: str) -> str:
    """Return the single line ending used by a body."""
    has_crlf = "\r\n" in body
    without_crlf = body.replace("\r\n", "")
    if "\r" in without_crlf:
        raise ValueError("pull-request body contains an unsupported carriage return")
    if has_crlf and "\n" in without_crlf:
        raise ValueError("pull-request body mixes CRLF and LF line endings")
    return "\r\n" if has_crlf else "\n"


def _flatten_pages(document: Any, label: str) -> list[dict[str, Any]]:
    """Flatten either one API array or gh's paginated slurp array."""
    if not isinstance(document, list):
        raise ValueError(f"{label} must be a JSON array")
    if not document:
        return []
    if all(isinstance(item, dict) for item in document):
        return list(document)
    if not all(isinstance(page, list) for page in document):
        raise ValueError(f"{label} contains an invalid pagination shape")
    flattened: list[dict[str, Any]] = []
    for page in document:
        if not all(isinstance(item, dict) for item in page):
            raise ValueError(f"{label} contains a non-object item")
        flattened.extend(page)
    return flattened


def _bounded_text(
    value: Any, label: str, *, allow_empty: bool = True, limit: int = MAX_TEXT
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if len(value) > limit:
        raise ValueError(f"{label} exceeds the {limit}-character safety cap")
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _validate_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEAD_SHA_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be exactly 40 hexadecimal characters")
    return value.lower()


def _validate_repository(value: Any, label: str) -> str:
    if not isinstance(value, str) or REPOSITORY_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be an OWNER/REPOSITORY identifier")
    return value


def _head_and_base(pr: dict[str, Any]) -> tuple[str, str, str, str]:
    head = pr.get("head")
    base = pr.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise ValueError("pull-request evidence has no valid head or base object")
    head_sha = _validate_sha(head.get("sha"), "pull-request head SHA")
    base_sha = _validate_sha(base.get("sha"), "pull-request base SHA")
    head_repo = head.get("repo")
    if not isinstance(head_repo, dict):
        raise ValueError("pull-request evidence has no head repository")
    repository = _validate_repository(
        head_repo.get("full_name"), "pull-request head repository"
    )
    title = _bounded_text(pr.get("title"), "pull-request title", allow_empty=False)
    if pr.get("state") != "open":
        raise ValueError("pull-request must remain open while its body is rendered")
    return head_sha, base_sha, repository, title


def _validate_commits(document: Any, head_sha: str) -> list[dict[str, str]]:
    rows = _flatten_pages(document, "pull-request commits")
    if not rows or len(rows) > MAX_COMMITS:
        raise ValueError("pull-request commits are missing or exceed the safety cap")
    commits: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        sha = _validate_sha(row.get("sha"), "commit SHA")
        if sha in seen:
            raise ValueError("pull-request commits contain a duplicate SHA")
        seen.add(sha)
        commit = row.get("commit")
        if not isinstance(commit, dict):
            raise ValueError("pull-request commit metadata is incomplete")
        message = _bounded_text(
            commit.get("message"),
            "commit message",
            allow_empty=False,
            limit=MAX_COMMIT_TEXT,
        )
        commits.append({"sha": sha, "message": message})
    if commits[-1]["sha"] != head_sha:
        raise ValueError("latest commit evidence is not bound to the pull-request head")
    return commits


def _validate_files(document: Any) -> list[dict[str, Any]]:
    rows = _flatten_pages(document, "pull-request files")
    if len(rows) > MAX_FILES:
        raise ValueError("pull-request files exceed the safety cap")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        filename = _bounded_text(
            row.get("filename"), "changed filename", allow_empty=False
        )
        if "\r" in filename or "\n" in filename or filename in seen:
            raise ValueError("pull-request files contain an invalid or duplicate path")
        seen.add(filename)
        status = row.get("status")
        if status not in {
            "added",
            "copied",
            "modified",
            "renamed",
            "deleted",
            "changed",
        }:
            raise ValueError("pull-request files contain an unknown status")
        numbers: dict[str, int] = {}
        for key in ("additions", "deletions", "changes"):
            value = row.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"changed file {key} must be a non-negative integer")
            numbers[key] = value
        files.append({"filename": filename, "status": status, **numbers})
    return sorted(files, key=lambda item: str(item["filename"]))


def _code(value: str) -> str:
    """Render untrusted text in an HTML code element."""
    compact = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return f"<code>{html.escape(compact, quote=True)}</code>"


def _plain(value: str) -> str:
    compact = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    escaped = html.escape(compact, quote=False)
    return re.sub(r"([\\`*_\[\]])", r"\\\1", escaped)


def _purpose_lines(commits: list[dict[str, str]], head_sha: str) -> list[str]:
    source_commits = _core_commits(commits)
    structured = _structured_evidence(source_commits, "purpose")
    if structured:
        return _author_reported_lines(structured, limit=3)
    subjects = [
        (commit["sha"], _clean_subject(commit["message"].splitlines()[0]))
        for commit in source_commits
    ]
    if not subjects:
        return [_summary_text([])]
    return _author_reported_lines(subjects, limit=3)


def _root_cause_lines(commits: list[dict[str, str]]) -> list[str]:
    source_commits = _core_commits(commits)
    structured = _structured_evidence(source_commits, "root")
    if structured:
        return _author_reported_lines(structured, limit=3)
    return [
        "The confirmed cause was not documented in commit metadata; review the diff before completing this section."
    ]


def _structured_evidence(
    commits: list[dict[str, str]], field: str
) -> list[tuple[str, str]]:
    aliases = {
        "purpose": {"why", "purpose"},
        "root": {"root cause", "cause"},
        "changes": {"changes", "key changes"},
        "verification": {"verification", "test", "tests"},
    }[field]
    values: list[tuple[str, str]] = []
    seen: set[str] = set()
    for commit in commits:
        active: str | None = None
        paragraph: list[str] = []

        def flush() -> None:
            value = " ".join(paragraph).strip()
            key = value.casefold().rstrip(".")
            if active in aliases and value and key not in seen:
                seen.add(key)
                values.append((commit["sha"], value))
            paragraph.clear()

        for line in commit["message"].splitlines()[1:]:
            match = STRUCTURED_COMMIT_FIELD_PATTERN.match(line)
            if match is not None:
                flush()
                active = match.group(1).casefold()
                if match.group(2):
                    paragraph.append(match.group(2).strip())
                continue
            if re.match(
                r"^(?:#{1,6}\s|[A-Za-z][A-Za-z -]*:|(?:Closes|Fixes|Resolves)\s+#)",
                line,
            ):
                flush()
                active = None
            elif not line.strip():
                flush()
            elif active is not None:
                bullet = re.match(r"^[ \t]*[-*+][ \t]+(.*)$", line)
                if bullet:
                    flush()
                    paragraph.append(bullet.group(1).strip())
                else:
                    paragraph.append(line.strip())
        flush()
    return values


def _structured_values(commits: list[dict[str, str]], field: str) -> list[str]:
    return [value for _, value in _structured_evidence(commits, field)]


def _author_reported_lines(evidence: list[tuple[str, str]], *, limit: int) -> list[str]:
    if len(evidence) > limit:
        raise ValueError(
            "Too many narrative topics; provide a reviewed PR-summary-base commit instead of dropping context"
        )
    return [
        f"- Author-reported at {_code(sha)}: {_sentence(value)}"
        for sha, value in evidence
    ]


def _summary_text(values: list[str], limit: int = 3) -> str:
    compact = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if not compact:
        return "Review the available commit and diff evidence to describe the purpose."
    if len(compact) > limit:
        raise ValueError(
            "Too many narrative topics; provide a reviewed PR-summary-base commit instead of dropping context"
        )
    return " ".join(_sentence(value) for value in compact)


def _clean_subject(subject: str) -> str:
    cleaned = COMMIT_PREFIX_PATTERN.sub("", subject).strip()
    return cleaned or "an unlabelled change"


def _core_commits(commits: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        commit
        for commit in commits
        if not commit["message"].lstrip().casefold().startswith("merge ")
    ]


def _summary_commits(
    commits: list[dict[str, str]], base_sha: str
) -> list[dict[str, str]]:
    """Use an explicit cumulative summary, including every later change."""
    for index in range(len(commits) - 1, -1, -1):
        commit = commits[index]
        message = commit["message"]
        if "PR-summary-base:" not in message:
            continue
        markers = SUMMARY_BASE_PATTERN.findall(message)
        if len(markers) != 1 or markers[0].lower() != base_sha:
            raise ValueError(
                "PR-summary-base does not match the current pull-request base"
            )
        if not all(
            _structured_values([commit], field)
            for field in ("purpose", "root", "changes", "verification")
        ):
            raise ValueError(
                "A cumulative PR summary must provide Why, Root cause, Changes, and Verification"
            )
        return _core_commits(commits[index:])
    return _core_commits(commits)


def _sentence(value: str) -> str:
    compact = " ".join(value.replace("\r", " ").replace("\n", " ").split()).strip()
    if not compact:
        return "The change is described by the available commit evidence."
    compact = compact[0].upper() + compact[1:]
    return _plain(compact if compact.endswith((".", "!", "?")) else compact + ".")


def _changes_summary(
    commits: list[dict[str, str]], files: list[dict[str, Any]]
) -> list[str]:
    if not files:
        return ["No changed files were returned by the pull-request API."]
    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for commit in _core_commits(commits):
        facts = _structured_evidence([commit], "changes") or [
            (commit["sha"], _clean_subject(commit["message"].splitlines()[0]))
        ]
        for sha, value in facts:
            key = value.casefold().rstrip(".")
            if key not in seen:
                seen.add(key)
                selected.append((sha, value))
    if len(selected) > MAX_KEY_CHANGES:
        raise ValueError(
            "More than five key changes; provide a reviewed PR-summary-base commit instead of omitting changes"
        )
    return [
        f"- Author-reported at {_code(sha)}: {_sentence(value)}"
        for sha, value in selected
    ] or ["- No non-merge change description was provided."]


def _verification_summary(commits: list[dict[str, str]]) -> list[str]:
    evidence: dict[str, tuple[str, str]] = {}
    for commit in commits:
        for value in _structured_values([commit], "verification"):
            evidence[value.casefold().rstrip(".")] = (commit["sha"], value)
    if len(evidence) > 4:
        raise ValueError(
            "Too many verification notes; provide a reviewed cumulative PR summary"
        )
    return [
        *[
            f"- Author-reported at {_code(sha)}: {_sentence(value)}"
            for sha, value in evidence.values()
        ],
        "- Current CI and mutation results are available in the GitHub Checks tab; commit notes do not certify a later head.",
    ]


def _issue_lines(commits: list[dict[str, str]]) -> list[str]:
    references: dict[str, str] = {}
    for commit in commits:
        for reference in ISSUE_REFERENCE_PATTERN.findall(commit["message"]):
            references.setdefault(reference, commit["sha"])
    if not references:
        return ["Not applicable."]
    return [
        "Commit-referenced issues: "
        + ", ".join(
            f"{_code(reference)} ({_code(sha)})"
            for reference, sha in references.items()
        )
        + "."
    ]


def _section_kind(heading: str) -> str | None:
    normalized = heading.casefold()
    if any(
        token in normalized for token in ("purpose", "summary", "mục đích", "tóm tắt")
    ):
        return "purpose"
    if any(token in normalized for token in ("root cause", "nguyên nhân")):
        return "root_cause"
    if any(
        token in normalized
        for token in (
            "verification",
            "how to test",
            "monitor",
            "xác minh",
            "kiểm tra",
            "kiểm thử",
        )
    ):
        return "verification"
    if any(token in normalized for token in ("related", "issue", "liên quan")):
        return "related"
    if normalized in {
        "key changes",
        "dependency changes",
        "thay đổi chính",
        "thay đổi phụ thuộc",
    }:
        return "changes"
    return None


def _validate_inventory(pr: dict[str, Any], field: str, actual: int, cap: int) -> None:
    expected = pr.get(field)
    if (
        not isinstance(expected, int)
        or isinstance(expected, bool)
        or expected < 0
        or expected > cap
    ):
        raise ValueError(
            f"Pull-request {field} count is missing, invalid, or exceeds the supported API limit"
        )
    if actual != expected:
        raise ValueError(
            f"Pull-request {field} evidence is incomplete: expected {expected}, received {actual}"
        )


def _localize_system_text(lines: list[str], language: str) -> list[str]:
    """Localize fixed messages; authored commit prose stays in its source language."""
    if language != "vi":
        return lines
    translations = {
        "The confirmed cause was not documented in commit metadata; review the diff before completing this section.": "Commit metadata chưa ghi nguyên nhân đã xác nhận; hãy review diff trước khi hoàn tất mục này.",
        "Review the available commit and diff evidence to describe the purpose.": "Hãy review commit và diff để mô tả mục đích.",
        "No changed files were returned by the pull-request API.": "Pull-request API không trả về file đã thay đổi.",
        "- No non-merge change description was provided.": "- Chưa có mô tả thay đổi ngoài merge commit.",
        "- Current CI and mutation results are available in the GitHub Checks tab; commit notes do not certify a later head.": "- Xem kết quả CI và mutation hiện tại tại tab GitHub Checks; ghi chú của commit không chứng nhận head mới hơn.",
        "- Author-reported at ": "- Theo tác giả tại revision ",
        "Commit-referenced issues: ": "Issue được commit tham chiếu: ",
        "Not applicable.": "Không áp dụng.",
    }
    result: list[str] = []
    for line in lines:
        for source, translation in translations.items():
            if line.startswith(source):
                line = translation + line[len(source) :]
                break
        else:
            raise ValueError(
                "generated pull-request text has no Vietnamese translation"
            )
        result.append(line)
    return result


def _remove_protocol_lines(lines: list[str]) -> list[str]:
    filtered: list[str] = []
    in_head = False
    for line in lines:
        if line == MANAGED_BODY_MARKER:
            continue
        if line == HEAD_START_MARKER:
            in_head = True
            continue
        if in_head:
            if line == HEAD_END_MARKER:
                in_head = False
            continue
        filtered.append(line)
    if in_head:
        raise ValueError("pull-request body has an unterminated head block")
    return filtered


def render_dynamic_body(
    body: str,
    pr: dict[str, Any],
    commits_document: Any,
    files_document: Any,
    checks_document: Any,
) -> str:
    """Replace narrative sections with data from the exact pull-request revision."""
    newline = _line_ending(body)
    normalized = body.replace("\r\n", "\n")
    marker_matches = list(TEMPLATE_MARKER_PATTERN.finditer(normalized))
    if len(marker_matches) != 1 or marker_matches[0].start() != 0:
        raise ValueError(
            "pull-request body must begin with exactly one trusted template marker"
        )
    head_sha, base_sha, _repository, _title = _head_and_base(pr)
    current_body = pr.get("body") or ""
    if not isinstance(current_body, str) or current_body != body:
        raise ValueError("pull-request body changed before rendering")
    commits = _validate_commits(commits_document, head_sha)
    files = _validate_files(files_document)
    _validate_inventory(pr, "commits", len(commits), MAX_COMMITS)
    _validate_inventory(pr, "changed_files", len(files), MAX_FILES)
    summary_commits = _summary_commits(commits, base_sha)
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = _remove_protocol_lines(lines)
    marker_line = lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    headings = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := HEADING_PATTERN.match(line)) is not None
    ]
    generated = {
        "purpose": _purpose_lines(summary_commits, head_sha),
        "root_cause": _root_cause_lines(summary_commits),
        "changes": _changes_summary(summary_commits, files),
        "verification": _verification_summary(summary_commits),
        "related": _issue_lines(commits),
    }
    language = (
        "vi"
        if any(heading.casefold() == "mục đích" for _, heading in headings)
        else "en"
    )
    generated = {
        kind: _localize_system_text(content, language)
        for kind, content in generated.items()
    }
    for heading_index in range(len(headings) - 1, -1, -1):
        line_index, heading = headings[heading_index]
        section_end = (
            headings[heading_index + 1][0]
            if heading_index + 1 < len(headings)
            else len(lines)
        )
        section = lines[line_index + 1 : section_end]
        kind = _section_kind(heading)
        if REQUIRED_START in section or OPTIONAL_START in section or kind is None:
            continue
        lines[line_index + 1 : section_end] = [
            "",
            *generated[kind],
            "",
        ]
    while lines and not lines[-1].strip():
        lines.pop()
    output = [
        marker_line,
        "",
        *lines,
        "",
    ]
    rendered = newline.join(output)
    if len(rendered.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError("generated pull-request body exceeds the safety cap")
    return rendered


def write_body(path: Path, body: str) -> None:
    payload = body.encode("utf-8")
    if len(payload) > MAX_BODY_BYTES:
        raise ValueError("generated pull-request body exceeds the safety cap")
    try:
        path.write_bytes(payload)
    except OSError as error:
        raise ValueError(
            f"could not write generated pull-request body: {error}"
        ) from error


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--pr-file", type=Path, required=True)
    parser.add_argument("--commits-file", type=Path, required=True)
    parser.add_argument("--files-file", type=Path, required=True)
    parser.add_argument("--checks-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        body = read_body(arguments.body_file)
        pr = _read_json(arguments.pr_file, "pull-request evidence")
        if not isinstance(pr, dict):
            raise ValueError("pull-request evidence must be a JSON object")
        rendered = render_dynamic_body(
            body,
            pr,
            _read_json(arguments.commits_file, "pull-request commits"),
            _read_json(arguments.files_file, "pull-request files"),
            None,
        )
        write_body(arguments.output, rendered)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("Rendered the complete pull-request body from current PR evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
