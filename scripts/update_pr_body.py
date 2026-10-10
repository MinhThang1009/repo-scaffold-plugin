#!/usr/bin/env python3
"""Render a complete pull-request body from a bounded source template."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path


MAX_BODY_BYTES = 1 * 1024 * 1024
HEAD_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}\Z")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}")
ALLOWED_PLACEHOLDERS = frozenset({"HEAD_SHA", "HEAD_REPOSITORY"})
TEMPLATE_MARKER_PATTERN = re.compile(
    r"(?m)^\ufeff?<!-- repo-scaffold:pr-template=[a-z][a-z0-9-]* -->[ \t]*(?=\r?$)"
)
MANAGED_BODY_MARKER = "<!-- repo-scaffold:pr-body-managed -->"
MANAGED_BODY_PATTERN = re.compile(
    rf"(?m)^{re.escape(MANAGED_BODY_MARKER)}[ \t]*(?=\r?$)"
)
HEAD_START_MARKER = "<!-- repo-scaffold:pr-head:start -->"
HEAD_END_MARKER = "<!-- repo-scaffold:pr-head:end -->"
HEAD_START_PATTERN = re.compile(rf"(?m)^{re.escape(HEAD_START_MARKER)}[ \t]*(?=\r?$)")
HEAD_END_PATTERN = re.compile(rf"(?m)^{re.escape(HEAD_END_MARKER)}[ \t]*(?=\r?$)")


def read_body(path: Path) -> str:
    """Read a bounded UTF-8 body without silently accepting malformed input."""
    try:
        with path.open("rb") as source:
            payload = source.read(MAX_BODY_BYTES + 1)
    except OSError as error:
        raise ValueError(f"could not read pull-request body: {error}") from error
    if len(payload) > MAX_BODY_BYTES:
        raise ValueError(
            f"pull-request body exceeds the {MAX_BODY_BYTES}-byte safety cap"
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("pull-request body is not valid UTF-8") from error


def _line_ending(body: str) -> str:
    """Return the sole line ending used by a body, rejecting mixed endings."""
    has_crlf = "\r\n" in body
    without_crlf = body.replace("\r\n", "")
    if "\r" in without_crlf:
        raise ValueError("pull-request body contains an unsupported carriage return")
    if has_crlf and "\n" in without_crlf:
        raise ValueError("pull-request body mixes CRLF and LF line endings")
    return "\r\n" if has_crlf else "\n"


def _head_block(head_sha: str, newline: str) -> str:
    return newline.join(
        (
            HEAD_START_MARKER,
            f"Latest head commit: `{head_sha.lower()}`.",
            HEAD_END_MARKER,
        )
    )


def _validate_head_inputs(head_sha: str, head_repository: str) -> None:
    """Validate immutable GitHub identifiers before rendering any body text."""
    if not HEAD_SHA_PATTERN.fullmatch(head_sha):
        raise ValueError("head SHA must be exactly 40 hexadecimal characters")
    if not REPOSITORY_PATTERN.fullmatch(head_repository):
        raise ValueError("head repository must be an OWNER/REPOSITORY identifier")


def is_managed_body(body: str) -> bool:
    """Return whether one complete body is explicitly repository-managed."""
    if not isinstance(body, str):
        return False
    try:
        newline = _line_ending(body)
    except ValueError:
        return False
    template_markers = list(TEMPLATE_MARKER_PATTERN.finditer(body))
    managed_markers = list(MANAGED_BODY_PATTERN.finditer(body))
    return (
        len(template_markers) == 1
        and template_markers[0].start() == 0
        and len(managed_markers) == 1
        and managed_markers[0].start() == template_markers[0].end() + len(newline)
    )


def render_body(template: str, head_sha: str, head_repository: str) -> str:
    """Render every PR section from ``template`` and the exact head identity."""
    if not isinstance(template, str):
        raise ValueError("pull-request template must be text")
    _validate_head_inputs(head_sha, head_repository)
    _line_ending(template)
    template_markers = list(TEMPLATE_MARKER_PATTERN.finditer(template))
    if len(template_markers) != 1 or template_markers[0].start() != 0:
        raise ValueError(
            "pull-request template must begin with exactly one trusted template marker"
        )
    if HEAD_START_MARKER in template or HEAD_END_MARKER in template:
        raise ValueError(
            "pull-request template must not contain the legacy partial head markers"
        )
    placeholders = PLACEHOLDER_PATTERN.findall(template)
    unknown = sorted(set(placeholders) - ALLOWED_PLACEHOLDERS)
    if unknown:
        raise ValueError(
            "pull-request template contains unsupported placeholders: "
            + ", ".join(unknown)
        )
    missing = sorted(ALLOWED_PLACEHOLDERS - set(placeholders))
    if missing:
        raise ValueError("pull-request template must bind " + ", ".join(missing))
    if not is_managed_body(template):
        raise ValueError(
            "pull-request body template must contain one managed-body marker immediately after the template marker"
        )
    rendered = template.replace("{{HEAD_SHA}}", head_sha.lower()).replace(
        "{{HEAD_REPOSITORY}}", head_repository
    )
    return rendered


def update_body(body: str, head_sha: str) -> str:
    """Insert or replace only the trusted head block in ``body``.

    Bodies without the repository's template marker are left untouched. This
    keeps an incomplete or third-party PR body from becoming writable through
    this helper; the normal template gate reports that body separately.
    """
    if not isinstance(body, str):
        raise ValueError("pull-request body must be text")
    if not HEAD_SHA_PATTERN.fullmatch(head_sha):
        raise ValueError("head SHA must be exactly 40 hexadecimal characters")

    newline = _line_ending(body)
    template_markers = list(TEMPLATE_MARKER_PATTERN.finditer(body))
    if not template_markers:
        return body
    if len(template_markers) != 1 or template_markers[0].start() != 0:
        raise ValueError(
            "pull-request body must begin with exactly one trusted template marker"
        )
    template_marker = template_markers[0]
    new_block = _head_block(head_sha, newline)

    starts = list(HEAD_START_PATTERN.finditer(body))
    ends = list(HEAD_END_PATTERN.finditer(body))
    if not starts and not ends:
        marker_end = template_marker.end()
        if body[marker_end : marker_end + len(newline)] == newline:
            marker_end += len(newline)
        return (
            body[: template_marker.end()]
            + newline
            + new_block
            + newline
            + body[marker_end:]
        )
    if len(starts) != 1 or len(ends) != 1 or starts[0].start() > ends[0].start():
        raise ValueError("pull-request head markers must form exactly one ordered pair")

    start = starts[0]
    end = ends[0]
    expected_start = template_marker.end() + len(newline)
    if start.start() != expected_start:
        raise ValueError(
            "pull-request head marker must immediately follow the template marker"
        )
    end_of_block = end.end()
    if body[end_of_block : end_of_block + len(newline)] == newline:
        end_of_block += len(newline)
    return body[: start.start()] + new_block + newline + body[end_of_block:]


def write_body(path: Path, body: str) -> None:
    """Write a bounded UTF-8 body, refusing an oversized generated result."""
    payload = body.encode("utf-8")
    if len(payload) > MAX_BODY_BYTES:
        raise ValueError(
            f"generated pull-request body exceeds the {MAX_BODY_BYTES}-byte safety cap"
        )
    try:
        destination = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        )
    except OSError as error:
        raise ValueError(
            f"could not write generated pull-request body: {error}"
        ) from error
    temporary = Path(destination.name)
    publication_error: OSError | None = None
    try:
        with destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    except OSError as error:
        publication_error = error
        raise ValueError(
            f"could not write generated pull-request body: {error}"
        ) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            publication_state = (
                f"publication failed: {publication_error}"
                if publication_error is not None
                else "publication state may have changed"
            )
            raise ValueError(
                f"body temporary cleanup failed: {error}; {publication_state}"
            ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--template-file", type=Path)
    source.add_argument("--body-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--head-repository")
    args = parser.parse_args(argv)
    try:
        if args.template_file is not None:
            if args.head_repository is None:
                raise ValueError("--head-repository is required with --template-file")
            template = read_body(args.template_file)
            updated = render_body(template, args.head_sha, args.head_repository)
            original = None
        else:
            if args.head_repository is not None:
                raise ValueError("--head-repository requires --template-file")
            original = read_body(args.body_file)
            updated = update_body(original, args.head_sha)
        write_body(args.output, updated)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if original is not None and updated == original:
        print("Pull-request body has no trusted head block to update.")
    elif args.template_file is not None:
        print("Rendered the complete pull-request body from the source template.")
    else:
        print("Updated the trusted pull-request head block.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
