from __future__ import annotations

import importlib.util
import json
import os
import runpy
import sys
import tempfile
import unittest
from email.message import Message
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.request import Request


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "repo-scaffold" / "scripts" / "check_community_health.py"
)
SPEC = importlib.util.spec_from_file_location(
    "skills.repo-scaffold.scripts.check_community_health", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load check_community_health.py")
community_health = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = community_health
SPEC.loader.exec_module(community_health)


def registry_document(
    *,
    tracker: str = "contributor-covenant",
    kind: str = "file",
    allow_multiple: bool = False,
) -> dict[str, object]:
    return {
        "schema-version": 1,
        "files": [
            {
                "id": "code_of_conduct",
                "label": "Code of Conduct",
                "scope": "github-community-health",
                "kind": kind,
                "candidates": ["CODE_OF_CONDUCT.md"],
                "tracker": tracker,
                "allow_multiple": allow_multiple,
            }
        ],
    }


def covenant_text(version: str) -> str:
    return (
        "# Contributor Covenant Code of Conduct\n\n"
        f"This Code of Conduct is adapted from the Contributor Covenant, version {version}.\n"
    )


class FakeResponse(BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class FakeClient:
    def __init__(self, responses: dict[str, object | Exception]) -> None:
        self.responses = responses

    def get_json(self, endpoint: str) -> Any:
        response = self.responses[endpoint]
        if isinstance(response, Exception):
            raise response
        return response


def upstream_responses(
    repository: str = "owner/repository", health: int = 100
) -> dict[str, object | Exception]:
    commit = "a" * 40
    return {
        f"repos/{repository}/community/profile": {"health_percentage": health},
        "repos/EthicalSource/contributor_covenant/branches/release": {
            "commit": {"sha": commit}
        },
        f"repos/EthicalSource/contributor_covenant/git/trees/{commit}?recursive=1": {
            "truncated": False,
            "tree": [
                {"path": "content/version/2/1/code_of_conduct.md"},
                {"path": "content/version/3/0/code_of_conduct.md"},
                {"path": "README.md"},
                {"path": None},
                "invalid",
            ],
        },
    }


class RegistryTests(unittest.TestCase):
    def test_parse_and_load_registry(self) -> None:
        parsed = community_health.parse_registry(registry_document())
        self.assertEqual(parsed[0].identifier, "code_of_conduct")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(registry_document()), encoding="utf-8")
            self.assertEqual(community_health.load_registry(path), parsed)

    def test_registry_rejects_invalid_top_level_documents(self) -> None:
        invalid = [None, {}, {"schema-version": 1, "files": []}]
        for document in invalid:
            with (
                self.subTest(document=document),
                self.assertRaises(community_health.AuditError),
            ):
                community_health.parse_registry(document)

    def test_registry_rejects_invalid_entries(self) -> None:
        mutations = [
            ("entry", None),
            ("id", "Bad-ID"),
            ("label", ""),
            ("scope", "other"),
            ("kind", "link"),
            ("tracker", "unknown"),
            ("candidates", []),
            ("candidates", [None]),
            ("candidates", ["."]),
            ("candidates", ["../outside"]),
            ("candidates", ["docs/./file.md"]),
            ("candidates", [r"docs\file.md"]),
            ("candidates", ["C:/README.md"]),
            ("candidates", ["docs/C:README.md"]),
            ("candidates", ["README.md", "README.md"]),
            ("allow_multiple", "true"),
        ]
        for key, value in mutations:
            document = registry_document()
            if key == "entry":
                document["files"] = [value]
            else:
                document["files"][0][key] = value  # type: ignore[index]
            with (
                self.subTest(key=key, value=value),
                self.assertRaises(community_health.AuditError),
            ):
                community_health.parse_registry(document)

        duplicate = registry_document()
        duplicate["files"] = duplicate["files"] * 2  # type: ignore[operator]
        with self.assertRaisesRegex(community_health.AuditError, "unique"):
            community_health.parse_registry(duplicate)

        too_many = registry_document()
        too_many["files"] = [too_many["files"][0]] * (  # type: ignore[index]
            community_health.MAX_REGISTRY_ENTRIES + 1
        )
        with self.assertRaisesRegex(community_health.AuditError, "entry limit"):
            community_health.parse_registry(too_many)

    def test_load_registry_wraps_read_and_json_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaisesRegex(community_health.AuditError, "could not read"):
                community_health.load_registry(missing)
            invalid = Path(directory) / "invalid.json"
            invalid.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(community_health.AuditError, "could not read"):
                community_health.load_registry(invalid)
            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text(
                '{"schema-version": 1, "schema-version": 1, "files": []}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(community_health.AuditError, "could not read"):
                community_health.load_registry(duplicate)
            oversized = Path(directory) / "oversized.json"
            oversized.write_text(
                " " * (community_health.MAX_REGISTRY_BYTES + 1), encoding="utf-8"
            )
            with self.assertRaisesRegex(community_health.AuditError, "size limit"):
                community_health.load_registry(oversized)


class GitHubClientTests(unittest.TestCase):
    def test_get_json_uses_bounded_authenticated_request(self) -> None:
        response = FakeResponse(b'{"ok": true}')
        with mock.patch.object(
            community_health.GITHUB_API_OPENER, "open", return_value=response
        ) as open_url:
            result = community_health.GitHubClient("token", timeout=4).get_json(
                "repos/owner/repository"
            )
        self.assertEqual(result, {"ok": True})
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        self.assertEqual(open_url.call_args.kwargs["timeout"], 4)

    def test_get_json_rejects_unsafe_endpoints(self) -> None:
        client = community_health.GitHubClient()
        for endpoint in ("/repos/owner/repo", "http://example.test", "repos/../secret"):
            with (
                self.subTest(endpoint=endpoint),
                self.assertRaisesRegex(community_health.AuditError, "unsafe"),
            ):
                client.get_json(endpoint)

    def test_get_json_wraps_network_and_content_failures(self) -> None:
        failures = [
            HTTPError("url", 403, "forbidden", Message(), None),
            URLError("offline"),
            OSError("socket"),
        ]
        for failure in failures:
            with (
                self.subTest(failure=failure),
                mock.patch.object(
                    community_health.GITHUB_API_OPENER, "open", side_effect=failure
                ),
                self.assertRaises(community_health.AuditError),
            ):
                community_health.GitHubClient().get_json("repos/owner/repository")

        payloads = [
            b"x" * (community_health.MAX_RESPONSE_BYTES + 1),
            b"{",
            b'{"name":"first","name":"second"}',
        ]
        for payload in payloads:
            with (
                self.subTest(size=len(payload)),
                mock.patch.object(
                    community_health.GITHUB_API_OPENER,
                    "open",
                    return_value=FakeResponse(payload),
                ),
                self.assertRaises(community_health.AuditError),
            ):
                community_health.GitHubClient().get_json("repos/owner/repository")

    def test_get_json_rejects_redirects_before_following_them(self) -> None:
        handler = community_health.RejectRedirectHandler()

        with self.assertRaisesRegex(
            community_health.AuditError, "redirects are not allowed"
        ):
            handler.redirect_request(
                Request("https://api.github.com/repos/owner/repository"),
                None,
                302,
                "Found",
                Message(),
                "https://example.test/receive-token",
            )


class InventoryTests(unittest.TestCase):
    def test_inventory_handles_files_directories_and_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_entry = community_health.RegistryEntry(
                "readme",
                "README",
                "github-community-profile",
                "file",
                ("README.md",),
                "none",
            )
            self.assertEqual(
                community_health.inventory_entry(root, file_entry)["status"], "absent"
            )
            (root / "README.md").write_text("readme\n", encoding="utf-8")
            present = community_health.inventory_entry(root, file_entry)
            self.assertEqual(present["paths"], ["README.md"])

            templates = root / ".github" / "ISSUE_TEMPLATE"
            templates.mkdir(parents=True)
            (templates / "bug.md").write_text("bug\n", encoding="utf-8")
            (templates / "nested").mkdir()
            directory_entry = community_health.RegistryEntry(
                "issues",
                "Issues",
                "github-community-health",
                "directory",
                (".github/ISSUE_TEMPLATE",),
                "none",
            )
            self.assertEqual(
                community_health.inventory_entry(root, directory_entry)["paths"],
                [".github/ISSUE_TEMPLATE/bug.md"],
            )

    def test_inventory_detects_ambiguous_and_any_locations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first.md").write_text("one\n", encoding="utf-8")
            (root / "second.md").write_text("two\n", encoding="utf-8")
            entry = community_health.RegistryEntry(
                "template",
                "Template",
                "github-community-health",
                "any",
                ("first.md", "second.md"),
                "none",
            )
            result = community_health.inventory_entry(root, entry)
            self.assertEqual(result["status"], "ambiguous")
            self.assertIn("shadow", result["details"])

    def test_inventory_allows_explicit_multiple_locations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            default_template = root / ".github" / "PULL_REQUEST_TEMPLATE.md"
            default_template.parent.mkdir(parents=True)
            default_template.write_text("default\n", encoding="utf-8")
            specialized_templates = root / ".github" / "PULL_REQUEST_TEMPLATE"
            specialized_templates.mkdir()
            (specialized_templates / "feature.md").write_text(
                "feature\n", encoding="utf-8"
            )
            document = registry_document(kind="any", allow_multiple=True)
            document["files"][0].update(  # type: ignore[index]
                {
                    "id": "pull_request_template",
                    "label": "Pull request template",
                    "candidates": [
                        ".github/PULL_REQUEST_TEMPLATE.md",
                        ".github/PULL_REQUEST_TEMPLATE",
                    ],
                    "tracker": "none",
                }
            )
            entry = community_health.parse_registry(document)[0]

            result = community_health.inventory_entry(root, entry)

            self.assertEqual(result["status"], "present")
            self.assertEqual(
                result["paths"],
                [
                    ".github/PULL_REQUEST_TEMPLATE.md",
                    ".github/PULL_REQUEST_TEMPLATE/feature.md",
                ],
            )
            self.assertIn("explicitly allowed", result["details"])

    def test_path_checks_reject_links_and_inventory_kind_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "policy"
            path.mkdir()
            entry = community_health.RegistryEntry(
                "policy",
                "Policy",
                "github-community-health",
                "file",
                ("policy",),
                "none",
            )
            self.assertEqual(
                community_health.inventory_entry(root, entry)["status"], "absent"
            )
            with (
                mock.patch.object(
                    community_health.os.path, "lexists", return_value=True
                ),
                mock.patch.object(
                    community_health, "is_link_or_reparse", return_value=True
                ),
                self.assertRaisesRegex(community_health.AuditError, "linked"),
            ):
                community_health.checked_repository_path(root, "policy/file.md")

            with (
                mock.patch.object(Path, "rglob", return_value=[path / "linked"]),
                mock.patch.object(
                    community_health, "is_link_or_reparse", return_value=True
                ),
                self.assertRaisesRegex(community_health.AuditError, "linked"),
            ):
                community_health._directory_files(root, path)

    def test_reparse_attribute_detection(self) -> None:
        path = mock.Mock()
        metadata = mock.Mock(st_mode=0, st_file_attributes=stat_value())
        path.lstat.return_value = metadata
        self.assertTrue(community_health.is_link_or_reparse(path))


def stat_value() -> int:
    return getattr(community_health.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class CovenantTests(unittest.TestCase):
    def test_versions_and_local_policy_parsing(self) -> None:
        self.assertEqual(community_health.version_tuple("3.0"), (3, 0, 0))
        self.assertEqual(community_health.version_tuple("3.0.0"), (3, 0, 0))
        with self.assertRaisesRegex(community_health.AuditError, "invalid semantic"):
            community_health.version_tuple("v3")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CODE_OF_CONDUCT.md"
            path.write_text(covenant_text("3.0"), encoding="utf-8")
            self.assertEqual(
                community_health.local_contributor_covenant_version(path), "3.0"
            )
            path.write_text("custom policy\n", encoding="utf-8")
            self.assertIsNone(community_health.local_contributor_covenant_version(path))

    def test_local_policy_rejects_large_and_unreadable_files(self) -> None:
        path = mock.Mock()
        path.stat.return_value.st_size = community_health.MAX_POLICY_BYTES + 1
        with self.assertRaisesRegex(community_health.AuditError, "too large"):
            community_health.local_contributor_covenant_version(path)
        path.stat.side_effect = OSError("denied")
        with self.assertRaisesRegex(community_health.AuditError, "could not read"):
            community_health.local_contributor_covenant_version(path)

    def test_latest_contributor_covenant_selects_numeric_latest(self) -> None:
        upstream = community_health.latest_contributor_covenant(
            FakeClient(upstream_responses())
        )
        self.assertEqual(upstream["version"], "3.0")
        self.assertIn("a" * 40, upstream["url"])

    def test_latest_contributor_covenant_rejects_invalid_responses(self) -> None:
        branch_endpoint = "repos/EthicalSource/contributor_covenant/branches/release"
        cases: list[dict[str, object | Exception]] = []
        for branch in ({}, {"commit": {"sha": "invalid"}}):
            responses = upstream_responses()
            responses[branch_endpoint] = branch
            cases.append(responses)
        for tree in (
            {"truncated": True, "tree": []},
            {"truncated": False, "tree": []},
        ):
            responses = upstream_responses()
            responses[
                f"repos/EthicalSource/contributor_covenant/git/trees/{'a' * 40}?recursive=1"
            ] = tree
            cases.append(responses)
        for responses in cases:
            with (
                self.subTest(responses=responses),
                self.assertRaises(community_health.AuditError),
            ):
                community_health.latest_contributor_covenant(FakeClient(responses))

    def test_covenant_statuses(self) -> None:
        upstream = {
            "version": "3.0",
            "url": "https://example.test",
            "commit": "a",
            "path": "p",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "CODE_OF_CONDUCT.md"
            for version, expected in (
                ("2.1", "outdated"),
                ("3.0", "current"),
                ("3.0.0", "current"),
                ("4.0", "indeterminate"),
            ):
                path.write_text(covenant_text(version), encoding="utf-8")
                result: dict[str, Any] = {
                    "status": "present",
                    "paths": ["CODE_OF_CONDUCT.md"],
                }
                community_health._check_contributor_covenant(root, result, upstream)
                self.assertEqual(result["status"], expected)
            path.write_text("custom\n", encoding="utf-8")
            result = {"status": "present", "paths": ["CODE_OF_CONDUCT.md"]}
            community_health._check_contributor_covenant(root, result, upstream)
            self.assertEqual(result["status"], "unversioned")
            result = {"status": "absent", "paths": []}
            community_health._check_contributor_covenant(root, result, upstream)
            self.assertEqual(result["status"], "absent")


class AuditAndCliTests(unittest.TestCase):
    def test_audit_reports_current_attention_and_indeterminate(self) -> None:
        entries = community_health.parse_registry(registry_document())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "CODE_OF_CONDUCT.md"
            policy.write_text(covenant_text("3.0"), encoding="utf-8")
            current = community_health.audit(
                root,
                entries,
                "owner/repository",
                FakeClient(upstream_responses()),
                "now",
            )
            self.assertEqual(current["summary"]["status"], "current")

            policy.write_text(covenant_text("2.1"), encoding="utf-8")
            attention = community_health.audit(
                root,
                entries,
                "owner/repository",
                FakeClient(upstream_responses(health=90)),
                "now",
            )
            self.assertEqual(attention["summary"]["status"], "attention")

            broken = upstream_responses()
            broken["repos/owner/repository/community/profile"] = (
                community_health.AuditError("profile unavailable")
            )
            broken["repos/EthicalSource/contributor_covenant/branches/release"] = (
                community_health.AuditError("upstream unavailable")
            )
            indeterminate = community_health.audit(
                root, entries, "owner/repository", FakeClient(broken), "now"
            )
            self.assertEqual(indeterminate["summary"]["status"], "indeterminate")
            self.assertEqual(len(indeterminate["errors"]), 2)

            policy.unlink()
            absent = community_health.audit(
                root, entries, "owner/repository", FakeClient(broken), "now"
            )
            self.assertEqual(absent["files"][0]["status"], "absent")

    def test_audit_rejects_invalid_repository_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = community_health.parse_registry(registry_document(tracker="none"))
            with self.assertRaisesRegex(community_health.AuditError, "OWNER/REPO"):
                community_health.audit(root, entries, "invalid", FakeClient({}), "now")
            for health in (101, True, False):
                with self.subTest(health=health):
                    report = community_health.audit(
                        root,
                        entries,
                        "owner/repository",
                        FakeClient(
                            {
                                "repos/owner/repository/community/profile": {
                                    "health_percentage": health
                                }
                            }
                        ),
                        "now",
                    )
                    self.assertEqual(
                        report["community-profile"]["status"], "indeterminate"
                    )

    def test_markdown_report_covers_errors_and_absent_paths(self) -> None:
        report: dict[str, Any] = {
            "repository": "owner/repository",
            "checked-at": "now",
            "summary": {"status": "indeterminate"},
            "community-profile": {"status": "indeterminate"},
            "files": [
                {
                    "label": "Policy",
                    "paths": [],
                    "tracker": "none",
                    "status": "absent",
                    "details": "line|one\nline two",
                }
            ],
            "errors": ["offline\nretry"],
        }
        markdown = community_health.markdown_report(report)
        self.assertEqual(
            markdown,
            "<!-- repo-scaffold-community-health-drift -->\n"
            "# Community-health upstream report\n\n"
            "- Repository: `owner/repository`\n"
            "- Checked: `now`\n"
            "- Overall status: **indeterminate**\n"
            "- GitHub Community Profile: **indeterminate**\n\n"
            "| Surface | Local path(s) | Upstream tracking | Status | Details |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| Policy | _absent_ | not versioned | absent | line\\|one line two |\n\n"
            "## Indeterminate checks\n\n"
            "- offline retry\n\n"
            "Project-authored policies without a versioned canonical upstream are "
            "inventoried as `not versioned`; they are not falsely treated as "
            "outdated.\n",
        )

    def test_markdown_report_escapes_every_table_cell(self) -> None:
        report: dict[str, Any] = {
            "repository": "owner/repository",
            "checked-at": "now",
            "summary": {"status": "attention"},
            "community-profile": {"status": "attention"},
            "files": [
                {
                    "label": "Policy|extra\nrow",
                    "paths": ["docs/a|b\n.md"],
                    "tracker": "tracker|name\nnext",
                    "status": "stale|status\nnext",
                    "details": "detail|text\nnext",
                }
            ],
            "errors": [],
        }

        self.assertIn(
            "| Policy\\|extra row | `docs/a\\|b .md` | tracker\\|name next | "
            "stale\\|status next | detail\\|text next |",
            community_health.markdown_report(report),
        )

    def test_write_text_and_parse_args(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "report.md"
            community_health.write_text(path, "report\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "report\n")
            with (
                mock.patch.object(
                    community_health.os.path, "lexists", return_value=True
                ),
                mock.patch.object(
                    community_health, "is_link_or_reparse", return_value=True
                ),
                self.assertRaisesRegex(community_health.AuditError, "output"),
            ):
                community_health.write_text(path, "unsafe\n")
        args = community_health.parse_args(
            [
                "--repository",
                "owner/repository",
                "--json-output",
                "report.json",
                "--markdown-output",
                "report.md",
            ]
        )
        self.assertEqual(args.repository, "owner/repository")

    def test_main_returns_each_status_and_writes_reports(self) -> None:
        statuses = (("current", 0), ("attention", 1), ("indeterminate", 2))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            registry.write_text(json.dumps(registry_document()), encoding="utf-8")
            for status, expected in statuses:
                report = {
                    "summary": {"status": status},
                    "community-profile": {
                        "status": "current",
                        "health-percentage": 100,
                    },
                    "repository": "owner/repository",
                    "checked-at": "now",
                    "files": [],
                    "errors": [],
                }
                json_output = root / f"{status}.json"
                markdown_output = root / f"{status}.md"
                stdout = StringIO()
                with (
                    self.subTest(status=status),
                    mock.patch.object(
                        community_health, "audit", return_value=report
                    ) as audit,
                    mock.patch.dict(os.environ, {"GH_TOKEN": "token"}, clear=True),
                    mock.patch.object(community_health.sys, "stdout", stdout),
                ):
                    result = community_health.main(
                        [
                            "--repository-root",
                            str(root),
                            "--registry",
                            str(registry),
                            "--repository",
                            "owner/repository",
                            "--json-output",
                            str(json_output),
                            "--markdown-output",
                            str(markdown_output),
                        ]
                    )
                self.assertEqual(result, expected)
                self.assertEqual(
                    json_output.read_text(encoding="utf-8"),
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                )
                self.assertEqual(
                    markdown_output.read_text(encoding="utf-8"),
                    community_health.markdown_report(report),
                )
                self.assertEqual(
                    stdout.getvalue(), f"Community-health upstream status: {status}\n"
                )
                audit.assert_called_once()
                audit_root, audit_entries, audit_repository, audit_client, checked = (
                    audit.call_args.args
                )
                self.assertEqual(audit_root, root.resolve())
                self.assertEqual(
                    audit_entries, community_health.load_registry(registry)
                )
                self.assertEqual(audit_repository, "owner/repository")
                self.assertEqual(audit_client.token, "token")
                self.assertRegex(checked, r"^\d{4}-\d{2}-\d{2}T")

    def test_main_reports_audit_errors(self) -> None:
        stderr = StringIO()
        with (
            mock.patch.object(
                community_health,
                "load_registry",
                side_effect=community_health.AuditError("bad"),
            ),
            mock.patch.object(community_health.sys, "stderr", stderr),
        ):
            result = community_health.main(
                [
                    "--repository",
                    "owner/repository",
                    "--json-output",
                    "report.json",
                    "--markdown-output",
                    "report.md",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("error: bad", stderr.getvalue())

    def test_script_entry_point(self) -> None:
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT_PATH)]),
            mock.patch.object(community_health, "main", return_value=0),
            self.assertRaises(SystemExit),
        ):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")


if __name__ == "__main__":
    unittest.main()
