#!/usr/bin/env python3
"""Fail-closed preflight using /dependency-graph/sbom evidence."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
from typing import Any
from urllib.parse import urlsplit

from codeql_preflight import (
    request_metrics,
    GitHubClient,
    InspectionError,
    revalidate_repository_state,
    split_repository,
    verified_repository_id,
)


SUPPORTED_VISIBILITIES = frozenset({"public", "private", "internal"})
MAX_SBOM_ITEMS = 100_000
MAX_SBOM_TEXT_CHARACTERS = 256 * 1024


class SbomCollectionBudget:
    """Bound aggregate work across packages, creators, references and relationships."""

    def __init__(self) -> None:
        self.remaining = MAX_SBOM_ITEMS

    def consume(self, value: Any) -> list[Any]:
        if not isinstance(value, list):
            raise InspectionError("Dependency graph SBOM collection is invalid.")
        if len(value) > self.remaining:
            raise InspectionError("Dependency graph SBOM exceeds the item safety cap.")
        self.remaining -= len(value)
        return value


def sbom_text(value: Any, *, single_line: bool = False, required: bool = True) -> str:
    """Check text at the evidence boundary without coercion or echoing its contents."""
    if not isinstance(value, str) or len(value) > MAX_SBOM_TEXT_CHARACTERS:
        raise InspectionError("Dependency graph SBOM text is invalid or oversized.")
    if (required and not value.strip()) or (
        single_line and ("\n" in value or "\r" in value)
    ):
        raise InspectionError("Dependency graph SBOM text is empty or not single-line.")
    return value


def sbom_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InspectionError("Dependency graph SBOM record is invalid.")
    return value


def validate_sbom_package(
    value: Any, identifiers: set[str], budget: SbomCollectionBudget
) -> None:
    package = sbom_record(value)
    identifier = sbom_text(package.get("SPDXID"), single_line=True)
    if (
        not re.fullmatch(r"SPDXRef-[A-Za-z0-9.-]+", identifier)
        or identifier in identifiers
    ):
        raise InspectionError(
            "Dependency graph SBOM package identifier is invalid or duplicated."
        )
    identifiers.add(identifier)
    for field in ("name", "downloadLocation"):
        sbom_text(package.get(field), single_line=True)
    # Version and file-analysis fields are optional in SPDX. GitHub's OpenAPI
    # array-level `required` list does not make them required on each item.
    for field in (
        "versionInfo",
        "licenseConcluded",
        "licenseDeclared",
        "supplier",
        "copyrightText",
    ):
        if field in package:
            sbom_text(package[field], required=False)
    if "filesAnalyzed" in package and not isinstance(package["filesAnalyzed"], bool):
        raise InspectionError("Dependency graph SBOM filesAnalyzed must be a boolean.")
    if "externalRefs" in package:
        for reference in budget.consume(package["externalRefs"]):
            record = sbom_record(reference)
            for field in ("referenceCategory", "referenceType", "referenceLocator"):
                sbom_text(record.get(field), single_line=True)


def require_boolean(document: dict[str, Any], field: str) -> bool:
    """Read a repository boolean without accepting truthy substitute values."""
    value = document.get(field)
    if not isinstance(value, bool):
        raise InspectionError(f"Repository response has an invalid {field!r} value.")
    return value


def advanced_security_status(document: dict[str, Any]) -> str:
    """Require the published Code Security status for a non-public repository."""
    analysis = document.get("security_and_analysis")
    field = (
        "code_security"
        if isinstance(analysis, dict) and "code_security" in analysis
        else "advanced_security"
    )
    value = analysis.get(field) if isinstance(analysis, dict) else None
    status = value.get("status") if isinstance(value, dict) else None
    if not isinstance(status, str) or status not in {"enabled", "disabled"}:
        raise InspectionError(f"Repository response has an invalid {field} status.")
    return status


def dependency_graph_is_available(document: Any) -> bool:
    """Validate the bounded SPDX 2.x API envelope, not dependency or license completeness."""
    if not isinstance(document, dict):
        raise InspectionError("Dependency graph SBOM response is invalid.")
    sbom = document.get("sbom")
    spdx_id = sbom.get("SPDXID") if isinstance(sbom, dict) else None
    if not isinstance(spdx_id, str) or not spdx_id:
        raise InspectionError("Dependency graph SBOM response has no SPDXID.")
    sbom = sbom_record(sbom)
    if spdx_id != "SPDXRef-DOCUMENT" or sbom.get("dataLicense") != "CC0-1.0":
        raise InspectionError(
            "Dependency graph SBOM document identity or data license is invalid."
        )
    version = sbom_text(sbom.get("spdxVersion"), single_line=True)
    if not re.fullmatch(r"SPDX-2\.[0-9]+", version):
        raise InspectionError("Dependency graph SBOM has an unsupported SPDX format.")
    sbom_text(sbom.get("name"), single_line=True)
    namespace = sbom_text(sbom.get("documentNamespace"), single_line=True)
    try:
        parsed_namespace = urlsplit(namespace)
    except ValueError as error:
        raise InspectionError("Dependency graph SBOM namespace is invalid.") from error
    if (
        not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9+.-]*:(?:[-A-Za-z0-9._~:/?\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})+",
            namespace,
        )
        or not parsed_namespace.scheme
        or not namespace.partition(":")[2]
        or "#" in namespace
        or any(character.isspace() for character in namespace)
        or (
            parsed_namespace.scheme in {"http", "https"} and not parsed_namespace.netloc
        )
    ):
        raise InspectionError(
            "Dependency graph SBOM namespace must be an absolute fragment-free URI."
        )
    creation = sbom_record(sbom.get("creationInfo"))
    created = sbom_text(creation.get("created"), single_line=True)
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", created
    ):
        raise InspectionError("Dependency graph SBOM creation time must be UTC.")
    try:
        # ISO 8601 allows a leap-second spelling, which datetime cannot parse.
        if created.endswith(":60Z") and created[11:16] != "23:59":
            raise ValueError("UTC leap second is not at the end of the day")
        datetime.strptime(created.replace(":60Z", ":59Z"), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise InspectionError(
            "Dependency graph SBOM creation time is invalid."
        ) from error
    budget = SbomCollectionBudget()
    creators = budget.consume(creation.get("creators"))
    if not creators:
        raise InspectionError("Dependency graph SBOM has no creator evidence.")
    for creator in creators:
        sbom_text(creator, single_line=True)
    identifiers = {spdx_id}
    for package in budget.consume(sbom.get("packages")):
        validate_sbom_package(package, identifiers, budget)
    if "relationships" in sbom:
        for relationship in budget.consume(sbom["relationships"]):
            record = sbom_record(relationship)
            for field in ("relationshipType", "spdxElementId", "relatedSpdxElement"):
                sbom_text(record.get(field), single_line=True)
    if "comment" in sbom:
        sbom_text(sbom["comment"], required=False)
    return True


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Inspect one GitHub.com repository before dependency-review installation."""
    if not isinstance(args.hostname, str) or args.hostname.casefold() != "github.com":
        raise InspectionError("Dependency-review preflight supports GitHub.com only.")
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
    repository_id = verified_repository_id(
        repository, getattr(args, "expected_repository_id", None)
    )
    if require_boolean(repository, "archived"):
        raise InspectionError(
            "Archived repositories cannot install dependency-review workflows."
        )
    if require_boolean(repository, "disabled"):
        raise InspectionError(
            "Disabled repositories cannot install dependency-review workflows."
        )
    visibility = repository.get("visibility")
    if visibility not in tuple(SUPPORTED_VISIBILITIES):
        raise InspectionError("Repository response has an invalid visibility value.")

    github_code_security = "not-required"
    if visibility != "public":
        owner_document = repository.get("owner")
        owner_type = (
            owner_document.get("type") if isinstance(owner_document, dict) else None
        )
        if owner_type != "Organization":
            raise InspectionError(
                "Dependency review for private or internal repositories requires an "
                "eligible organization-owned repository."
            )
        github_code_security = advanced_security_status(repository)
        if github_code_security != "enabled":
            return {
                "inspection_complete": True,
                "decision": "enable-github-code-security-before-installing-dependency-review",
                "repository": args.repository,
                "repository_id": repository_id,
                "visibility": visibility,
                "dependency_graph_available": False,
                "github_code_security": github_code_security,
                **request_metrics(client.request_count),
            }

    dependency_graph_is_available(
        client.json_at_status(f"repos/{owner}/{repo}/dependency-graph/sbom", 200)
    )
    revalidate_repository_state(
        client,
        args.repository,
        repository,
        (
            "archived",
            "disabled",
            "visibility",
            "owner.type",
            "owner.id",
            "security_and_analysis.code_security.status",
            "security_and_analysis.advanced_security.status",
        ),
    )
    return {
        "inspection_complete": True,
        "decision": (
            "bind-repository-identity-before-mutation"
            if getattr(args, "expected_repository_id", None) is None
            else "may-install-dependency-review-workflow"
        ),
        "repository": args.repository,
        "repository_id": repository_id,
        "visibility": visibility,
        "dependency_graph_available": True,
        "github_code_security": github_code_security,
        **request_metrics(client.request_count),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--expected-repository-id", type=int)
    parser.add_argument("--hostname", default="github.com")
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
