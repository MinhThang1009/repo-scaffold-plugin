from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import runpy
import sys
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts"

CODEQL_SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.codeql_preflight",
    SCRIPT_DIRECTORY / "codeql_preflight.py",
)
if CODEQL_SPEC is None or CODEQL_SPEC.loader is None:
    raise RuntimeError("Could not load codeql_preflight.py")
codeql_preflight = importlib.util.module_from_spec(CODEQL_SPEC)
sys.modules[CODEQL_SPEC.name] = codeql_preflight
sys.modules["codeql_preflight"] = codeql_preflight
CODEQL_SPEC.loader.exec_module(codeql_preflight)

SCRIPT_PATH = SCRIPT_DIRECTORY / "dependency_review_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.dependency_review_preflight", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load dependency_review_preflight.py")
dependency_review_preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dependency_review_preflight
SPEC.loader.exec_module(dependency_review_preflight)


def valid_sbom() -> dict[str, object]:
    return {
        "sbom": {
            "SPDXID": "SPDXRef-DOCUMENT",
            "spdxVersion": "SPDX-2.3",
            "creationInfo": {
                "created": "2026-10-04T00:00:00Z",
                "creators": ["Tool: Synthetic exporter"],
            },
            "name": "com.github.octo/example",
            "dataLicense": "CC0-1.0",
            "documentNamespace": "urn:uuid:00000000-0000-4000-8000-000000000042",
            "packages": [
                {
                    "SPDXID": "SPDXRef-Repository",
                    "name": "octo/example",
                    "versionInfo": "main",
                    "downloadLocation": "NOASSERTION",
                    "filesAnalyzed": False,
                }
            ],
        }
    }


class FakeClient:
    repository_response: object = {}
    sbom_response: object = valid_sbom()

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        self.request_count = 0

    def json(self, endpoint: str) -> object:
        self.request_count += 1
        if endpoint == "repos/octo/example":
            if isinstance(self.repository_response, Exception):
                raise self.repository_response
            return self.repository_response
        if endpoint == "repos/octo/example/dependency-graph/sbom":
            if isinstance(self.sbom_response, Exception):
                raise self.sbom_response
            return self.sbom_response
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    def json_at_status(self, endpoint: str, expected_status: int) -> object:
        assert type(expected_status) is int and expected_status == 200
        return self.json(endpoint)


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "hostname": "github.com",
        "repository": "octo/example",
        "expected_repository_id": 42,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def repository(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "full_name": "octo/example",
        "id": 42,
        "archived": False,
        "disabled": False,
        "visibility": "public",
        "owner": {"type": "Organization"},
        "security_and_analysis": {"advanced_security": {"status": "enabled"}},
    }
    value.update(overrides)
    return value


class DependencyReviewPreflightTests(unittest.TestCase):
    def test_export_requires_completed_http_status_even_with_valid_spdx(self) -> None:
        for status in (200, 201, 202, 204, 206, 302):
            client = codeql_preflight.GitHubClient("github.com")
            receipt = f"HTTP/2.0 {status} Synthetic\n\n" + json.dumps(valid_sbom())
            with (
                self.subTest(status=status),
                mock.patch.object(
                    client,
                    "_run",
                    side_effect=[
                        json.dumps(repository()),
                        receipt,
                        json.dumps(repository()),
                    ],
                ) as request,
                mock.patch.object(
                    dependency_review_preflight, "GitHubClient", return_value=client
                ),
            ):
                if status == 200:
                    self.assertEqual(
                        dependency_review_preflight.run(arguments())["decision"],
                        "may-install-dependency-review-workflow",
                    )
                else:
                    with self.assertRaisesRegex(
                        dependency_review_preflight.InspectionError, f"HTTP {status}"
                    ):
                        dependency_review_preflight.run(arguments())
                self.assertEqual(
                    request.call_args_list[1],
                    mock.call("repos/octo/example/dependency-graph/sbom", include=True),
                )
                self.assertEqual(request.call_count, 3 if status == 200 else 2)

    def test_sbom_absence_partial_types_and_malformed_nested_evidence_cannot_approve(
        self,
    ) -> None:
        documents: tuple[dict[str, object], ...] = (
            {"sbom": {"SPDXID": "SPDXRef-DOCUMENT"}},
            {"sbom": {"SPDXID": "arbitrary"}},
            {
                "sbom": {
                    "SPDXID": "SPDXRef-DOCUMENT",
                    "creationInfo": False,
                    "packages": "invalid",
                }
            },
        )
        for document in documents:
            with (
                self.subTest(document=document),
                self.assertRaises(dependency_review_preflight.InspectionError),
            ):
                dependency_review_preflight.dependency_graph_is_available(document)
        for field in (
            "spdxVersion",
            "creationInfo",
            "name",
            "dataLicense",
            "documentNamespace",
            "packages",
        ):
            document = valid_sbom()
            body = document["sbom"]
            assert isinstance(body, dict)
            del body[field]
            with (
                self.subTest(missing=field),
                self.assertRaises(dependency_review_preflight.InspectionError),
            ):
                dependency_review_preflight.dependency_graph_is_available(document)

    def test_valid_sbom_metadata_and_empty_packages_are_not_repository_name_oracles(
        self,
    ) -> None:
        self.assertTrue(
            dependency_review_preflight.dependency_graph_is_available(valid_sbom())
        )
        document = valid_sbom()
        body = document["sbom"]
        assert isinstance(body, dict)
        body["packages"] = []
        body["name"] = "An independently named document"
        self.assertTrue(
            dependency_review_preflight.dependency_graph_is_available(document)
        )

    def test_sbom_schema_rejects_malformed_evidence_without_coercion(self) -> None:
        changes: tuple[tuple[tuple[str | int, ...], object], ...] = (
            (("SPDXID",), "SPDXRef-other"),
            (("dataLicense",), "MIT"),
            (("spdxVersion",), "SPDX-3.0"),
            (("spdxVersion",), None),
            (("name",), "two\nlines"),
            (("name",), " "),
            (("documentNamespace",), "relative/path"),
            (("documentNamespace",), "urn:"),
            (("documentNamespace",), "https:///missing-host"),
            (("documentNamespace",), "urn:uuid:test#fragment"),
            (("documentNamespace",), "urn:uuid:has space"),
            (("documentNamespace",), "https://[malformed"),
            (("documentNamespace",), "urn:uuid:invalid%ZZ"),
            (("documentNamespace",), "urn:uuid:control\x00"),
            (("documentNamespace",), "urn:uuid:unencoded-unicode-é"),
            (("creationInfo",), False),
            (("creationInfo", "created"), "2026-02-30T00:00:00Z"),
            (("creationInfo", "created"), "2026-10-04T00:00:00+00:00"),
            (("creationInfo", "created"), "2026-10-04T12:00:60Z"),
            (("creationInfo", "creators"), []),
            (("creationInfo", "creators"), "Tool: string"),
            (("creationInfo", "creators"), [False]),
            (("packages",), {}),
            (("packages",), [None]),
            (("packages", 0, "SPDXID"), "SPDXRef-DOCUMENT"),
            (("packages", 0, "SPDXID"), "SPDXRef-invalid_id"),
            (("packages", 0, "name"), False),
            (("packages", 0, "downloadLocation"), None),
            (("packages", 0, "versionInfo"), []),
            (("packages", 0, "filesAnalyzed"), "false"),
            (("packages", 0, "externalRefs"), [False]),
            (
                ("packages", 0, "externalRefs"),
                [{"referenceCategory": "PACKAGE-MANAGER"}],
            ),
            (("relationships",), "not-an-array"),
            (("relationships",), [None]),
            (("relationships",), [{}]),
            (("comment",), True),
        )
        for path, value in changes:
            document = valid_sbom()
            container = document["sbom"]
            for component in path[:-1]:
                assert isinstance(container, (dict, list))
                container = container[component]  # type: ignore[index]
            assert isinstance(container, (dict, list))
            container[path[-1]] = value  # type: ignore[index]
            with (
                self.subTest(path=path, value=value),
                self.assertRaises(dependency_review_preflight.InspectionError),
            ):
                dependency_review_preflight.dependency_graph_is_available(document)

    def test_sbom_optional_fields_backward_compatible_versions_and_relationships(
        self,
    ) -> None:
        document = valid_sbom()
        body = document["sbom"]
        assert isinstance(body, dict)
        packages = body["packages"]
        assert isinstance(packages, list)
        package = packages[0]
        del package["versionInfo"]
        del package["filesAnalyzed"]
        package.update(
            {
                "licenseDeclared": "NOASSERTION",
                "licenseConcluded": "MIT",
                "supplier": "NOASSERTION",
                "copyrightText": "multi\nline",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": "pkg:pypi/example@1.0",
                    }
                ],
            }
        )
        body["relationships"] = [
            {
                "relationshipType": "DESCRIBES",
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": "SPDXRef-Repository",
            }
        ]
        body["comment"] = ""
        for version in ("SPDX-2.2", "SPDX-2.3", "SPDX-2.4"):
            body["spdxVersion"] = version
            with self.subTest(version=version):
                self.assertTrue(
                    dependency_review_preflight.dependency_graph_is_available(document)
                )
        creation = body["creationInfo"]
        assert isinstance(creation, dict)
        creation["created"] = "2016-12-31T23:59:60Z"
        self.assertTrue(
            dependency_review_preflight.dependency_graph_is_available(document)
        )

    def test_sbom_aggregate_collection_and_text_limits_fail_closed(self) -> None:
        document = valid_sbom()
        body = document["sbom"]
        assert isinstance(body, dict)
        package = body["packages"][0]
        body["packages"].append(copy.deepcopy(package))
        with self.assertRaisesRegex(
            dependency_review_preflight.InspectionError, "duplicated"
        ):
            dependency_review_preflight.dependency_graph_is_available(document)
        body["packages"].pop()
        body["name"] = "x" * (dependency_review_preflight.MAX_SBOM_TEXT_CHARACTERS + 1)
        with self.assertRaisesRegex(
            dependency_review_preflight.InspectionError, "oversized"
        ):
            dependency_review_preflight.dependency_graph_is_available(document)
        body["name"] = "small"
        body["relationships"] = [
            {
                "relationshipType": "DESCRIBES",
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": "SPDXRef-Repository",
            }
        ]
        with (
            mock.patch.object(dependency_review_preflight, "MAX_SBOM_ITEMS", 2),
            self.assertRaisesRegex(
                dependency_review_preflight.InspectionError, "item safety cap"
            ),
        ):
            dependency_review_preflight.dependency_graph_is_available(document)
        del body["relationships"]
        with mock.patch.object(dependency_review_preflight, "MAX_SBOM_ITEMS", 2):
            self.assertTrue(
                dependency_review_preflight.dependency_graph_is_available(document)
            )

    def test_multi_request_verdict_revalidates_identity_and_applicability(self) -> None:
        original = repository(id=42)
        for final in (
            repository(id=43),
            repository(id=42, full_name="other/target"),
            repository(id=42, archived=True),
            repository(id=42, disabled=True),
            repository(id=42, visibility="private"),
            None,
        ):
            client = FakeClient("github.com")
            with (
                self.subTest(final=final),
                mock.patch.object(
                    client,
                    "json",
                    side_effect=[
                        original,
                        valid_sbom(),
                        final,
                    ],
                ),
                mock.patch.object(
                    dependency_review_preflight, "GitHubClient", return_value=client
                ),
                self.assertRaisesRegex(
                    dependency_review_preflight.InspectionError,
                    "repository identity|repository.*changed|revalidation",
                ),
            ):
                dependency_review_preflight.run(arguments(expected_repository_id=42))

    def test_repository_identity_cannot_change_between_discovery_and_verdict(
        self,
    ) -> None:
        FakeClient.repository_response = repository(id=42)
        with mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient):
            positive = dependency_review_preflight.run(
                arguments(expected_repository_id=42)
            )
        self.assertEqual(positive.get("repository_id"), 42)
        for identity in (43, None, True, "42", 0, -1, 42.5):
            FakeClient.repository_response = repository(id=identity)
            with (
                self.subTest(identity=identity),
                mock.patch.object(
                    dependency_review_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    dependency_review_preflight.InspectionError,
                    "repository ID|repository identity",
                ),
            ):
                dependency_review_preflight.run(arguments(expected_repository_id=42))

    def test_unbound_inspection_returns_discovery_only_not_installation_authorization(
        self,
    ) -> None:
        FakeClient.repository_response = repository(id=42)
        with mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient):
            result = dependency_review_preflight.run(
                arguments(expected_repository_id=None)
            )
        self.assertEqual(result["decision"], "bind-repository-identity-before-mutation")
        self.assertEqual(result["repository_id"], 42)

    def setUp(self) -> None:
        FakeClient.repository_response = repository()
        FakeClient.sbom_response = valid_sbom()

    def test_permits_public_repository_with_a_dependency_graph(self) -> None:
        with mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient):
            result = dependency_review_preflight.run(arguments())

        self.assertEqual(result["decision"], "may-install-dependency-review-workflow")
        self.assertTrue(result["dependency_graph_available"])
        self.assertEqual(result["github_code_security"], "not-required")
        self.assertEqual(result["github_api_requests"], 3)

    def test_requires_enabled_code_security_for_nonpublic_repository(self) -> None:
        FakeClient.repository_response = repository(
            visibility="private",
            security_and_analysis={"advanced_security": {"status": "disabled"}},
        )
        with mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient):
            result = dependency_review_preflight.run(arguments())
        self.assertEqual(
            result["decision"],
            "enable-github-code-security-before-installing-dependency-review",
        )
        self.assertFalse(result["dependency_graph_available"])
        self.assertEqual(result["github_api_requests"], 1)

        FakeClient.repository_response = repository(visibility="internal")
        with mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient):
            result = dependency_review_preflight.run(arguments())
        self.assertEqual(result["decision"], "may-install-dependency-review-workflow")
        self.assertEqual(result["github_code_security"], "enabled")

    def test_rejects_untrusted_repository_or_dependency_graph_evidence(self) -> None:
        cases: tuple[tuple[argparse.Namespace, object, str], ...] = (
            (arguments(hostname="github.example"), repository(), "GitHub.com only"),
            (arguments(), [], "response is invalid"),
            (arguments(), repository(full_name="octo/other"), "different repository"),
            (arguments(), repository(archived=True), "Archived"),
            (arguments(), repository(disabled=True), "Disabled"),
            (arguments(), repository(visibility="unknown"), "invalid visibility"),
            (arguments(), repository(visibility=[]), "invalid visibility"),
            (
                arguments(),
                repository(visibility="private", owner={"type": "User"}),
                "eligible organization",
            ),
            (
                arguments(),
                repository(
                    visibility="private",
                    security_and_analysis={"advanced_security": {"status": "unknown"}},
                ),
                "invalid advanced_security",
            ),
        )
        for args, response, message in cases:
            FakeClient.repository_response = response
            with self.subTest(message=message):
                with mock.patch.object(
                    dependency_review_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        dependency_review_preflight.InspectionError, message
                    ):
                        dependency_review_preflight.run(args)

        FakeClient.repository_response = repository()
        for response, message in (
            ([], "invalid"),
            ({}, "no SPDXID"),
            ({"sbom": {}}, "no SPDXID"),
            (dependency_review_preflight.InspectionError("not found"), "not found"),
        ):
            FakeClient.sbom_response = response
            with self.subTest(response=response):
                with mock.patch.object(
                    dependency_review_preflight, "GitHubClient", FakeClient
                ):
                    with self.assertRaisesRegex(
                        dependency_review_preflight.InspectionError, message
                    ):
                        dependency_review_preflight.run(arguments())

    def test_standalone_code_security_controls_nonpublic_eligibility(self) -> None:
        for visibility in ("private", "internal"):
            for analysis, allowed in (
                ({"code_security": {"status": "enabled"}}, True),
                (
                    {
                        "code_security": {"status": "enabled"},
                        "advanced_security": {"status": "disabled"},
                    },
                    True,
                ),
                (
                    {
                        "code_security": {"status": "disabled"},
                        "advanced_security": {"status": "enabled"},
                    },
                    False,
                ),
            ):
                FakeClient.repository_response = repository(
                    visibility=visibility, security_and_analysis=analysis
                )
                with (
                    self.subTest(visibility=visibility, analysis=analysis),
                    mock.patch.object(
                        dependency_review_preflight, "GitHubClient", FakeClient
                    ),
                ):
                    result = dependency_review_preflight.run(arguments())
                    self.assertEqual(
                        result["decision"],
                        "may-install-dependency-review-workflow"
                        if allowed
                        else "enable-github-code-security-before-installing-dependency-review",
                    )

    def test_malformed_code_security_cannot_fall_back_to_legacy_entitlement(
        self,
    ) -> None:
        value: object
        for value in (None, {}, [], {"status": []}, {"status": "unknown"}):
            FakeClient.repository_response = repository(
                visibility="private",
                security_and_analysis={
                    "code_security": value,
                    "advanced_security": {"status": "enabled"},
                },
            )
            with (
                self.subTest(value=value),
                mock.patch.object(
                    dependency_review_preflight, "GitHubClient", FakeClient
                ),
                self.assertRaisesRegex(
                    dependency_review_preflight.InspectionError, "code_security"
                ),
            ):
                dependency_review_preflight.run(arguments())

    def test_helpers_cli_and_documentation_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            dependency_review_preflight.InspectionError, "invalid 'archived'"
        ):
            dependency_review_preflight.require_boolean({}, "archived")
        with self.assertRaisesRegex(
            dependency_review_preflight.InspectionError, "invalid advanced_security"
        ):
            dependency_review_preflight.advanced_security_status({})

        with (
            mock.patch.object(
                dependency_review_preflight, "parse_args", return_value=arguments()
            ),
            mock.patch.object(dependency_review_preflight, "GitHubClient", FakeClient),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(dependency_review_preflight.main(), 0)
        self.assertIn("may-install", print_mock.call_args.args[0])
        with (
            mock.patch.object(
                dependency_review_preflight,
                "parse_args",
                side_effect=dependency_review_preflight.InspectionError("blocked"),
            ),
            mock.patch("builtins.print") as print_mock,
        ):
            self.assertEqual(dependency_review_preflight.main(), 2)
        self.assertIn("inconclusive", print_mock.call_args.args[0])

        with mock.patch.object(
            sys,
            "argv",
            ["dependency_review_preflight.py", "--repository", "octo/example"],
        ):
            self.assertEqual(
                dependency_review_preflight.parse_args().repository, "octo/example"
            )
        with self.assertRaises(SystemExit):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

        skill = (PLUGIN_ROOT / "skills" / "repo-scaffold" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        setup = (
            PLUGIN_ROOT / "skills" / "repo-scaffold" / "references" / "github-setup.md"
        ).read_text(encoding="utf-8")
        self.assertIn("dependency_review_preflight.py", skill)
        self.assertIn("dependency_review_preflight.py", setup)
        self.assertIn('dependencyReviewPreflightResult.repository, "OWNER/REPO"', setup)
        self.assertIn("dependency-graph/sbom", SCRIPT_PATH.read_text(encoding="utf-8"))
        for marker in (
            "reported HTTP `200`",
            "November 13, 2026",
            "generate-report",
            "not implement the async replacement",
            "each temporary download",
            "Do not forward GitHub credentials",
            "No async operation is performed",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, setup)
        self.assertIn(
            "Async report generation/fetch/download is not implemented", skill
        )


if __name__ == "__main__":
    unittest.main()
