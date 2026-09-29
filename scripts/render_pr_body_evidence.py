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
MAX_COMMITS = 1000
MAX_FILES = 3000
MAX_CHECK_RUNS = 2000
MAX_TEXT = 2048
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
    r"^\s*(?:#+\s*)?(Why|Purpose|Root cause|Cause|Changes|Key changes|"
    r"Verification|Tests?)\s*:?\s*(.+?)\s*$",
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
        payload = path.read_bytes()
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


def _bounded_text(value: Any, label: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if len(value) > MAX_TEXT:
        value = value[:MAX_TEXT] + "…"
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
        message = _bounded_text(commit.get("message"), "commit message")
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


def _validate_checks(document: Any) -> list[dict[str, Any]]:
    pages = _flatten_pages(document, "check-runs")
    runs: list[dict[str, Any]] = []
    if pages and all("check_runs" in page for page in pages):
        for page in pages:
            check_runs = page.get("check_runs")
            if not isinstance(check_runs, list):
                raise ValueError("check-runs page has an invalid check_runs array")
            for run in check_runs:
                if not isinstance(run, dict):
                    raise ValueError("check-runs contains a non-object item")
                runs.append(run)
    else:
        runs = pages
    if len(runs) > MAX_CHECK_RUNS:
        raise ValueError("check-runs exceed the safety cap")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, int | str]] = set()
    for run in runs:
        name = _bounded_text(run.get("name"), "check-run name", allow_empty=False)
        status = run.get("status")
        if status not in {"queued", "in_progress", "completed"}:
            raise ValueError("check-runs contain an unknown status")
        conclusion = run.get("conclusion")
        if conclusion is not None:
            conclusion = _bounded_text(
                conclusion, "check-run conclusion", allow_empty=False
            )
        identifier = run.get("id", name)
        if not isinstance(identifier, (int, str)) or isinstance(identifier, bool):
            raise ValueError("check-run identifier is invalid")
        key = (name, identifier)
        if key in seen:
            raise ValueError("check-runs contain a duplicate run")
        seen.add(key)
        normalized.append(
            {
                "name": name,
                "status": status,
                "conclusion": conclusion,
                "id": identifier,
            }
        )
    return sorted(
        normalized, key=lambda item: (item["name"].casefold(), str(item["id"]))
    )


def _code(value: str) -> str:
    """Render untrusted text in an HTML code element."""
    compact = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return f"<code>{html.escape(compact, quote=True)}</code>"


def _plain(value: str) -> str:
    compact = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return html.escape(compact, quote=False)


def _purpose_lines(commits: list[dict[str, str]], head_sha: str) -> list[str]:
    source_commits = _core_commits(commits)
    subjects = [
        _clean_subject(commit["message"].splitlines()[0].strip())
        for commit in source_commits
        if not commit["message"].lstrip().casefold().startswith(("merge ", "revert "))
    ]
    structured = _structured_values(source_commits, "purpose")
    summary = _summary_text((subjects[:1] + structured), limit=2)
    return [_sentence(summary)]


def _commit_details(commits: list[dict[str, str]]) -> list[str]:
    details: list[str] = []
    for commit in commits:
        lines = [
            line.strip() for line in commit["message"].splitlines()[1:] if line.strip()
        ]
        if lines:
            details.append(f"- {_code(commit['sha'][:12])} {_plain(lines[0])}")
    return details or ["- No structured detail was supplied in the commit metadata."]


def _root_cause_lines(commits: list[dict[str, str]]) -> list[str]:
    source_commits = _core_commits(commits)
    structured = _structured_values(source_commits, "root")
    if structured:
        subjects = [
            _clean_subject(commit["message"].splitlines()[0].strip())
            for commit in source_commits
            if not commit["message"]
            .lstrip()
            .casefold()
            .startswith(("merge ", "revert "))
        ]
        return [_sentence(_summary_text(structured + subjects[:1], limit=2))]
    details = _commit_details(source_commits)
    if details == ["- No structured detail was supplied in the commit metadata."]:
        subjects = [
            _clean_subject(commit["message"].splitlines()[0].strip())
            for commit in source_commits
            if not commit["message"]
            .lstrip()
            .casefold()
            .startswith(("merge ", "revert "))
        ]
        primary = subjects[0] if subjects else "the available commit evidence"
        return [
            "No structured root-cause evidence was provided; the primary change was "
            + _sentence(primary)
        ]
    return [
        "The core rationale from the commit metadata is "
        + "; ".join(item[2:] for item in details)
        + "."
    ]


def _structured_values(commits: list[dict[str, str]], field: str) -> list[str]:
    aliases = {
        "purpose": {"why", "purpose"},
        "root": {"root cause", "cause"},
        "changes": {"changes", "key changes"},
        "verification": {"verification", "test", "tests"},
    }[field]
    values: list[str] = []
    seen: set[str] = set()
    for commit in commits:
        for line in commit["message"].splitlines()[1:]:
            match = STRUCTURED_COMMIT_FIELD_PATTERN.match(line)
            if match is None or match.group(1).casefold() not in aliases:
                continue
            value = _plain(match.group(2).strip())
            if value and value.casefold() not in seen:
                seen.add(value.casefold())
                values.append(value)
    return values


def _summary_text(values: list[str], limit: int = 3) -> str:
    compact = [_plain(value).rstrip(".") for value in values if value.strip()]
    if not compact:
        return "the available commit evidence"
    if len(compact) <= limit:
        if len(compact) == 1:
            return compact[0] + "."
        return "; ".join(compact[:-1]) + "; and " + compact[-1] + "."
    return (
        "; ".join(compact[:limit])
        + f"; plus {len(compact) - limit} additional context item(s)."
    )


def _clean_subject(subject: str) -> str:
    cleaned = COMMIT_PREFIX_PATTERN.sub("", subject).strip()
    return cleaned or "an unlabelled change"


def _core_commits(commits: list[dict[str, str]]) -> list[dict[str, str]]:
    metadata_markers = (
        "structured summary",
        "summary contract",
        "body layout",
        "body renderer",
    )
    core = [
        commit
        for commit in commits
        if not any(
            marker in commit["message"].splitlines()[0].casefold()
            for marker in metadata_markers
        )
    ]
    return core or commits


def _sentence(value: str) -> str:
    compact = " ".join(value.replace("\r", " ").replace("\n", " ").split()).strip()
    if not compact:
        return "The change is described by the available commit evidence."
    compact = compact[0].upper() + compact[1:]
    return compact if compact.endswith((".", "!", "?")) else compact + "."


def _changes_summary(
    commits: list[dict[str, str]], files: list[dict[str, Any]]
) -> list[str]:
    if not files:
        return ["No changed files were returned by the pull-request API."]
    structured = _structured_values(commits, "changes")
    subjects: list[str] = []
    seen: set[str] = set()
    for commit in commits:
        subject = _clean_subject(commit["message"].splitlines()[0].strip())
        if subject.casefold().startswith(("merge ", "revert ")):
            continue
        if subject.casefold() not in seen:
            seen.add(subject.casefold())
            subjects.append(subject)
    if not subjects:
        additions = sum(int(item["additions"]) for item in files)
        deletions = sum(int(item["deletions"]) for item in files)
        return [
            f"Update {len(files)} changed file(s), adding {additions} and removing {deletions} lines."
        ]
    selected: list[str] = []
    seen_selected: set[str] = set()
    for value in [*structured, *subjects]:
        key = value.casefold()
        if key not in seen_selected:
            seen_selected.add(key)
            selected.append(value)
        if len(selected) >= 3:
            break
    lines = [f"- {_sentence(subject)}" for subject in selected]
    theme = _diff_theme(files)
    if theme:
        lines.append(f"- {theme}")
    return lines


def _diff_theme(files: list[dict[str, Any]]) -> str:
    areas = {str(item["filename"]).split("/", 1)[0] for item in files}
    themes: list[str] = []
    if ".github" in areas:
        themes.append("workflow policy")
    if "scripts" in areas:
        themes.append("validation tooling")
    if "skills" in areas:
        themes.append("scaffold assets and contracts")
    if "tests" in areas:
        themes.append("regression coverage")
    if any(
        str(item["filename"]).lower().endswith((".md", ".markdown")) for item in files
    ):
        themes.append("documentation")
    if not themes:
        return ""
    if len(themes) == 1:
        return f"Update {themes[0]}."
    return "Synchronize " + ", ".join(themes[:-1]) + ", and " + themes[-1] + "."


def _verification_summary(commits: list[dict[str, str]]) -> list[str]:
    structured = _structured_values(commits, "verification")
    if structured:
        return [
            *[f"- {_sentence(value)}" for value in structured[:3]],
            "- Live status is tracked in the GitHub Checks tab for this pull-request head.",
        ]
    return [
        "- Verification is tracked in the GitHub Checks tab for this pull-request head."
    ]


def _issue_lines(commits: list[dict[str, str]]) -> list[str]:
    references: list[str] = []
    seen: set[str] = set()
    for commit in commits:
        for reference in ISSUE_REFERENCE_PATTERN.findall(commit["message"]):
            if reference not in seen:
                seen.add(reference)
                references.append(reference)
    if not references:
        return ["Not applicable."]
    return [
        "Referenced issues: "
        + ", ".join(_code(reference) for reference in references)
        + "."
    ]


def _section_kind(heading: str) -> str:
    normalized = heading.casefold()
    if any(
        token in normalized for token in ("purpose", "summary", "mục đích", "tóm tắt")
    ):
        return "purpose"
    if any(
        token in normalized
        for token in ("root cause", "cause", "scope", "risk", "nguyên nhân")
    ):
        return "root_cause"
    if any(
        token in normalized
        for token in ("verification", "monitor", "xác minh", "kiểm tra")
    ):
        return "verification"
    if any(token in normalized for token in ("related", "issue", "liên quan")):
        return "related"
    return "changes"


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
    head_sha, _base_sha, _repository, _title = _head_and_base(pr)
    current_body = pr.get("body") or ""
    if not isinstance(current_body, str) or current_body != body:
        raise ValueError("pull-request body changed before rendering")
    commits = _validate_commits(commits_document, head_sha)
    files = _validate_files(files_document)
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
        "purpose": _purpose_lines(commits, head_sha),
        "root_cause": _root_cause_lines(commits),
        "changes": _changes_summary(commits, files),
        "verification": _verification_summary(commits),
        "related": _issue_lines(commits),
    }
    for heading_index in range(len(headings) - 1, -1, -1):
        line_index, heading = headings[heading_index]
        section_end = (
            headings[heading_index + 1][0]
            if heading_index + 1 < len(headings)
            else len(lines)
        )
        section = lines[line_index + 1 : section_end]
        if REQUIRED_START in section or OPTIONAL_START in section:
            continue
        lines[line_index + 1 : section_end] = [
            "",
            *generated[_section_kind(heading)],
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
