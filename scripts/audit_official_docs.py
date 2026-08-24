#!/usr/bin/env python3
"""Remind maintainers to review claims backed by official documentation."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REGISTRY_BYTES = 512 * 1024
MAX_CLAIMS = 64
MAX_MARKERS_PER_CLAIM = 8
MAX_REVIEW_PERIOD_DAYS = 366
CLAIM_IDENTIFIER = re.compile(r"[a-z][a-z0-9-]*\Z")
HOSTNAME = re.compile(r"[a-z0-9][a-z0-9.-]*[a-z0-9]\Z")
DEFAULT_TRACKER_REGISTRY = Path(".github/official-docs-trackers.json")


class AuditError(RuntimeError):
    """Raised when a tracker or authoritative response cannot be trusted."""


class DuplicateJsonMember(ValueError):
    """Raised when JSON has ambiguous duplicate member names."""


class ApprovedRedirectHandler(HTTPRedirectHandler):
    """Follow only HTTPS redirects to an explicitly approved documentation host."""

    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        request: Any,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        """Reject a redirect before urllib opens a connection to its destination."""
        redirected_host = hostname(new_url, field="official documentation redirect")
        if redirected_host not in self.allowed_hosts:
            raise AuditError(
                "official documentation redirect leaves approved hosts: "
                f"{redirected_host}"
            )
        return super().redirect_request(
            request, response, code, message, headers, new_url
        )


@dataclass(frozen=True)
class DocumentationClaim:
    """One reviewed public claim and the authoritative page that supports it."""

    identifier: str
    label: str
    url: str
    allowed_hosts: tuple[str, ...]
    paths: tuple[Path, ...]
    markers: tuple[str, ...]
    reviewed_on: date
    review_period_days: int


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate keys."""
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonMember(f"duplicate JSON member {key!r}")
        document[key] = value
    return document


def safe_relative_path(value: object, *, field: str) -> Path:
    """Parse a repository-relative path without allowing an escape."""
    if not isinstance(value, str) or not value:
        raise AuditError(f"{field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise AuditError(f"{field} must be a safe relative path: {value!r}")
    return path


def hostname(url: str, *, field: str) -> str:
    """Validate one fixed HTTPS documentation URL and return its host."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise AuditError(f"{field} is not a valid URL") from error
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if (
        parsed.scheme != "https"
        or not host
        or HOSTNAME.fullmatch(host) is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise AuditError(f"{field} must be an HTTPS URL without credentials")
    return host


def require_string(document: dict[str, object], field: str, location: str) -> str:
    """Read a non-empty string from one tracker entry."""
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AuditError(f"{location}.{field} must be a non-empty string")
    return value


def load_trackers(
    root: Path, relative: Path = DEFAULT_TRACKER_REGISTRY
) -> tuple[DocumentationClaim, ...]:
    """Load a bounded, explicit official-documentation tracker registry."""
    registry_path = root / safe_relative_path(relative.as_posix(), field="registry")
    try:
        resolved = registry_path.resolve(strict=True)
        resolved.relative_to(root.resolve())
        if (
            registry_path.is_symlink()
            or registry_path.stat().st_size > MAX_REGISTRY_BYTES
        ):
            raise AuditError(
                f"official-docs tracker registry is missing or unsafe: {relative}"
            )
        document = json.loads(
            registry_path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_json_object,
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        DuplicateJsonMember,
        RecursionError,
    ) as error:
        raise AuditError(
            f"could not read official-docs tracker registry {relative}: {error}"
        ) from error
    if not isinstance(document, dict) or document.get("schema-version") != 1:
        raise AuditError("official-docs tracker registry must use schema-version 1")
    values = document.get("claims")
    if not isinstance(values, list) or not values or len(values) > MAX_CLAIMS:
        raise AuditError(
            "official-docs tracker registry claims must be a bounded non-empty list"
        )
    claims: list[DocumentationClaim] = []
    seen: set[str] = set()
    for index, raw_claim in enumerate(values):
        location = f"claims[{index}]"
        if not isinstance(raw_claim, dict):
            raise AuditError(f"{location} must be an object")
        identifier = require_string(raw_claim, "id", location)
        if CLAIM_IDENTIFIER.fullmatch(identifier) is None or identifier in seen:
            raise AuditError(f"{location}.id must be unique kebab-case")
        url = require_string(raw_claim, "url", location)
        source_host = hostname(url, field=f"{location}.url")
        raw_hosts = raw_claim.get("allowed-hosts")
        if not isinstance(raw_hosts, list) or not raw_hosts:
            raise AuditError(f"{location}.allowed-hosts must be a non-empty list")
        allowed_hosts = tuple(
            host.casefold() if isinstance(host, str) else "" for host in raw_hosts
        )
        if (
            len(allowed_hosts) != len(set(allowed_hosts))
            or source_host not in allowed_hosts
            or any(HOSTNAME.fullmatch(host) is None for host in allowed_hosts)
        ):
            raise AuditError(
                f"{location}.allowed-hosts must contain unique valid hosts including the source"
            )
        raw_paths = raw_claim.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise AuditError(f"{location}.paths must be a non-empty list")
        paths = tuple(
            safe_relative_path(value, field=f"{location}.paths[{path_index}]")
            for path_index, value in enumerate(raw_paths)
        )
        if len(paths) != len(set(paths)):
            raise AuditError(f"{location}.paths must not repeat paths")
        raw_markers = raw_claim.get("required-markers")
        if (
            not isinstance(raw_markers, list)
            or not raw_markers
            or len(raw_markers) > MAX_MARKERS_PER_CLAIM
            or not all(
                isinstance(marker, str) and 1 <= len(marker) <= 200
                for marker in raw_markers
            )
            or len(raw_markers) != len(set(raw_markers))
        ):
            raise AuditError(
                f"{location}.required-markers must be a bounded unique string list"
            )
        raw_reviewed_on = require_string(raw_claim, "reviewed-on", location)
        try:
            reviewed_on = date.fromisoformat(raw_reviewed_on)
        except ValueError as error:
            raise AuditError(
                f"{location}.reviewed-on must use ISO date format"
            ) from error
        review_period_days = raw_claim.get("review-period-days")
        if (
            not isinstance(review_period_days, int)
            or isinstance(review_period_days, bool)
            or not 1 <= review_period_days <= MAX_REVIEW_PERIOD_DAYS
        ):
            raise AuditError(
                f"{location}.review-period-days must be between 1 and {MAX_REVIEW_PERIOD_DAYS}"
            )
        seen.add(identifier)
        claims.append(
            DocumentationClaim(
                identifier=identifier,
                label=require_string(raw_claim, "label", location),
                url=url,
                allowed_hosts=allowed_hosts,
                paths=paths,
                markers=tuple(raw_markers),
                reviewed_on=reviewed_on,
                review_period_days=review_period_days,
            )
        )
    return tuple(claims)


def read_document(url: str, allowed_hosts: tuple[str, ...]) -> tuple[str, str]:
    """Fetch one bounded official page and return its resolved URL and UTF-8 text."""
    request = Request(
        url,
        headers={
            "Accept": "text/markdown,text/html;q=0.9",
            "User-Agent": "repo-scaffold-official-docs-audit",
        },
    )
    try:
        opener = build_opener(ApprovedRedirectHandler(allowed_hosts))
        with opener.open(request, timeout=30) as response:  # noqa: S310 - URL is registry-allowlisted
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            resolved_url = response.geturl()
    except (HTTPError, URLError, OSError) as error:
        raise AuditError(
            f"official documentation request failed for {url}: {error}"
        ) from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise AuditError(f"official documentation response is too large for {url}")
    try:
        return resolved_url, payload.decode("utf-8")
    except UnicodeError as error:
        raise AuditError(
            f"official documentation response is not UTF-8 for {url}"
        ) from error


def claim_findings(
    root: Path, claim: DocumentationClaim, today: date
) -> list[dict[str, str]]:
    """Return review findings for one source without interpreting its prose automatically."""
    for relative in claim.paths:
        path = root / relative
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root.resolve())
            if path.is_symlink():
                raise AuditError(f"claim source path is missing or unsafe: {relative}")
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as error:
            raise AuditError(
                f"claim source path is missing, unsafe, or unreadable: {relative}"
            ) from error
    resolved_url, content = read_document(claim.url, claim.allowed_hosts)
    resolved_host = hostname(resolved_url, field=f"resolved URL for {claim.identifier}")
    if resolved_host not in claim.allowed_hosts:
        raise AuditError(
            f"official documentation redirect leaves approved hosts for {claim.identifier}"
        )
    findings: list[dict[str, str]] = []
    missing = [marker for marker in claim.markers if marker not in content]
    paths = ", ".join(path.as_posix() for path in claim.paths)
    if missing:
        findings.append(
            {
                "kind": "official-docs-marker",
                "path": paths,
                "subject": claim.label,
                "current": "marker present at review",
                "latest": "marker missing",
                "details": f"Review {claim.url}; missing marker: {missing[0]!r}.",
            }
        )
    due_on = claim.reviewed_on + timedelta(days=claim.review_period_days)
    if claim.reviewed_on > today:
        raise AuditError(
            f"official-docs review date is in the future for {claim.identifier}"
        )
    if due_on <= today:
        findings.append(
            {
                "kind": "official-docs-review",
                "path": paths,
                "subject": claim.label,
                "current": claim.reviewed_on.isoformat(),
                "latest": due_on.isoformat(),
                "details": f"Review the claim against {claim.url} and update reviewed-on after approval.",
            }
        )
    return findings


def audit(
    root: Path,
    tracker_registry: Path = DEFAULT_TRACKER_REGISTRY,
    today: date | None = None,
) -> dict[str, Any]:
    """Audit each explicit official-docs claim and preserve indeterminate evidence."""
    checked_on = today or datetime.now(timezone.utc).date()
    findings: list[dict[str, str]] = []
    errors: list[str] = []
    try:
        claims = load_trackers(root, tracker_registry)
    except AuditError as error:
        claims = ()
        errors.append(str(error))
    for claim in claims:
        try:
            findings.extend(claim_findings(root, claim, checked_on))
        except AuditError as error:
            errors.append(str(error))
    status = "indeterminate" if errors else "attention" if findings else "current"
    return {
        "schema-version": 1,
        "checked-at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "findings": findings,
        "errors": errors,
    }


def markdown_report(report: dict[str, Any]) -> str:
    """Render an issue-safe report for a scheduled reminder workflow."""
    lines = [
        "<!-- repo-scaffold-official-docs-audit -->",
        "# Official documentation review report",
        "",
        f"- Checked: `{report['checked-at']}`",
        f"- Overall status: **{report['status']}**",
        "",
    ]
    findings = report["findings"]
    if findings:
        lines.extend(
            [
                "| Check | Path | Claim | Current | Review target |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        lines.extend(
            "| {kind} | `{path}` | {subject} | `{current}` | `{latest}` |".format(
                **finding
            )
            for finding in findings
        )
        lines.append("")
    else:
        lines.extend(
            ["All official-documentation claims are within their review period.", ""]
        )
    if report["errors"]:
        lines.extend(
            [
                "## Indeterminate checks",
                "",
                *[f"- {error}" for error in report["errors"]],
                "",
            ]
        )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse explicit report destinations for the trusted scheduled workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--tracker-registry", type=Path, default=DEFAULT_TRACKER_REGISTRY
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument(
        "--validate-registry",
        action="store_true",
        help="validate the local registry and paths without requesting external pages",
    )
    arguments = parser.parse_args(argv)
    if not arguments.validate_registry and (
        arguments.json_output is None or arguments.markdown_output is None
    ):
        parser.error(
            "--json-output and --markdown-output are required unless --validate-registry is used"
        )
    return arguments


def main(argv: list[str] | None = None) -> int:
    """Write reports and return current, attention, or indeterminate status."""
    arguments = parse_args(argv)
    repository_root = arguments.repository_root.resolve()
    if arguments.validate_registry:
        claims = load_trackers(repository_root, arguments.tracker_registry)
        print(f"Official documentation tracker registry is valid: {len(claims)} claims")
        return 0
    if arguments.json_output is None or arguments.markdown_output is None:
        raise AssertionError("argument parser must require report output paths")
    report = audit(repository_root, arguments.tracker_registry)
    arguments.json_output.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    arguments.markdown_output.write_text(markdown_report(report), encoding="utf-8")
    print(f"Official documentation review status: {report['status']}")
    return {"current": 0, "attention": 1, "indeterminate": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
