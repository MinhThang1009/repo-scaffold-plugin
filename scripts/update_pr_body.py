#!/usr/bin/env python3
"""Update the bounded, repository-owned head section in a pull-request body."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


MAX_BODY_BYTES = 1 * 1024 * 1024
HEAD_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}\Z")
TEMPLATE_MARKER_PATTERN = re.compile(
    r"(?m)^\ufeff?<!-- repo-scaffold:pr-template=[a-z][a-z0-9-]* -->[ \t]*(?=\r?$)"
)
HEAD_START_MARKER = "<!-- repo-scaffold:pr-head:start -->"
HEAD_END_MARKER = "<!-- repo-scaffold:pr-head:end -->"
HEAD_START_PATTERN = re.compile(rf"(?m)^{re.escape(HEAD_START_MARKER)}[ \t]*(?=\r?$)")
HEAD_END_PATTERN = re.compile(rf"(?m)^{re.escape(HEAD_END_MARKER)}[ \t]*(?=\r?$)")


def read_body(path: Path) -> str:
    """Read a bounded UTF-8 body without silently accepting malformed input."""
    try:
        payload = path.read_bytes()
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
        path.write_bytes(payload)
    except OSError as error:
        raise ValueError(
            f"could not write generated pull-request body: {error}"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args(argv)
    try:
        body = read_body(args.body_file)
        updated = update_body(body, args.head_sha)
        write_body(args.output, updated)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if updated == body:
        print("Pull-request body has no trusted head block to update.")
    else:
        print("Updated the trusted pull-request head block.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
