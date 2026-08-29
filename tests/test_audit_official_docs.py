from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "audit_official_docs.py"
SPEC = importlib.util.spec_from_file_location(
    "scripts.audit_official_docs", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load audit_official_docs.py")
official_docs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = official_docs
SPEC.loader.exec_module(official_docs)


class FakeResponse(BytesIO):
    def __init__(self, payload: bytes, resolved_url: str) -> None:
        super().__init__(payload)
        self.resolved_url = resolved_url

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def geturl(self) -> str:
        return self.resolved_url


class FakeOpener:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response
        self.error: OSError | None = None
        self.request: object | None = None
        self.timeout: int | None = None

    def open(self, request: object, *, timeout: int) -> FakeResponse:
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("FakeOpener requires a response")
        return self.response


def registry_document(*, reviewed_on: str = "2026-08-24") -> dict[str, Any]:
    return {
        "schema-version": 1,
        "claims": [
            {
                "id": "official-docs",
                "label": "Official documentation",
                "url": "https://docs.example.test/guide",
                "allowed-hosts": ["docs.example.test"],
                "paths": ["README.md"],
                "required-markers": ["Supported contract"],
                "reviewed-on": reviewed_on,
                "review-period-days": 90,
            }
        ],
    }


class OfficialDocumentationAuditTests(unittest.TestCase):
    def write_repository(self, root: Path, *, reviewed_on: str = "2026-08-24") -> None:
        registry = root / official_docs.DEFAULT_TRACKER_REGISTRY
        registry.parent.mkdir(parents=True)
        registry.write_text(
            json.dumps(registry_document(reviewed_on=reviewed_on)), encoding="utf-8"
        )
        (root / "README.md").write_text("Repository documentation\n", encoding="utf-8")

    def test_load_trackers_and_url_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            claims = official_docs.load_trackers(root)
            self.assertEqual(claims[0].identifier, "official-docs")
            self.assertEqual(claims[0].paths, (Path("README.md"),))
            self.assertEqual(
                official_docs.hostname("https://docs.example.test/guide", field="url"),
                "docs.example.test",
            )
            for value in (
                "http://docs.example.test",
                "https://user@docs.example.test",
                "https://docs.example.test:444",
                "https://docs.example.test:not-a-port",
            ):
                with (
                    self.subTest(value=value),
                    self.assertRaises(official_docs.AuditError),
                ):
                    official_docs.hostname(value, field="url")

    def test_input_guard_helpers_reject_invalid_values(self) -> None:
        for value in (None, "", 3):
            with self.subTest(value=value), self.assertRaises(official_docs.AuditError):
                official_docs.safe_relative_path(value, field="path")
        for value in (
            r"docs\README.md",
            "docs/./README.md",
            "docs//README.md",
            "C:/README.md",
        ):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(official_docs.AuditError, "safe relative"),
            ):
                official_docs.safe_relative_path(value, field="path")
        with self.assertRaisesRegex(official_docs.AuditError, "non-empty string"):
            official_docs.require_string({}, "label", "claim")

    def test_load_trackers_rejects_unsafe_or_invalid_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            registry = root / official_docs.DEFAULT_TRACKER_REGISTRY
            valid = registry_document()
            self.assertTrue(official_docs.path_has_link_or_reparse(root.parent, root))
            with mock.patch.object(
                official_docs, "path_has_link_or_reparse", return_value=True
            ):
                with self.assertRaisesRegex(official_docs.AuditError, "unsafe"):
                    official_docs.load_trackers(root)
            for document, fragment in (
                ({"schema-version": 2, "claims": []}, "schema-version"),
                ({"schema-version": 1, "claims": []}, "non-empty"),
                ({"schema-version": 1, "claims": [None]}, "object"),
                (
                    {
                        **valid,
                        "claims": [
                            {**valid["claims"][0], "url": "http://example.test"}
                        ],
                    },
                    "HTTPS",
                ),  # type: ignore[index]
                (
                    {
                        **valid,
                        "claims": [{**valid["claims"][0], "paths": ["../outside"]}],
                    },
                    "safe relative",
                ),  # type: ignore[index]
                (
                    {
                        **valid,
                        "claims": [{**valid["claims"][0], "required-markers": []}],
                    },
                    "required-markers",
                ),  # type: ignore[index]
                (
                    {
                        **valid,
                        "claims": [{**valid["claims"][0], "reviewed-on": "invalid"}],
                    },
                    "ISO date",
                ),  # type: ignore[index]
                (
                    {
                        **valid,
                        "claims": [{**valid["claims"][0], "id": "Not Kebab"}],
                    },
                    "unique kebab-case",
                ),  # type: ignore[index]
                (
                    {
                        **valid,
                        "claims": [
                            {
                                **valid["claims"][0],
                                "allowed-hosts": "docs.example.test",
                            }
                        ],
                    },
                    "non-empty list",
                ),  # type: ignore[index]
                (
                    {
                        **valid,
                        "claims": [
                            {
                                **valid["claims"][0],
                                "allowed-hosts": ["invalid host"],
                            }
                        ],
                    },
                    "unique valid hosts",
                ),  # type: ignore[index]
                (
                    {
                        **valid,
                        "claims": [{**valid["claims"][0], "paths": "README.md"}],
                    },
                    "paths must be a non-empty list",
                ),  # type: ignore[index]
                (
                    {
                        **valid,
                        "claims": [
                            {**valid["claims"][0], "paths": ["README.md", "README.md"]}
                        ],
                    },
                    "must not repeat paths",
                ),  # type: ignore[index]
                (
                    {
                        **valid,
                        "claims": [{**valid["claims"][0], "review-period-days": True}],
                    },
                    "review-period-days",
                ),  # type: ignore[index]
            ):
                with self.subTest(fragment=fragment):
                    registry.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(official_docs.AuditError, fragment):
                        official_docs.load_trackers(root)
            registry.write_text(
                '{"schema-version": 1, "schema-version": 1}', encoding="utf-8"
            )
            with self.assertRaisesRegex(official_docs.AuditError, "could not read"):
                official_docs.load_trackers(root)
            registry.write_bytes(b" " * (official_docs.MAX_REGISTRY_BYTES + 1))
            with self.assertRaisesRegex(official_docs.AuditError, "unsafe"):
                official_docs.load_trackers(root)

    def test_read_document_validates_network_size_and_encoding(self) -> None:
        opener = FakeOpener(
            FakeResponse(b"Supported contract", "https://docs.example.test/guide")
        )
        with mock.patch.object(official_docs, "build_opener", return_value=opener):
            self.assertEqual(
                official_docs.read_document(
                    "https://docs.example.test/guide", ("docs.example.test",)
                ),
                ("https://docs.example.test/guide", "Supported contract"),
            )
        self.assertEqual(opener.timeout, 30)
        for payload in (b"\xff", b"x" * (official_docs.MAX_RESPONSE_BYTES + 1)):
            opener = FakeOpener(
                FakeResponse(payload, "https://docs.example.test/guide")
            )
            with (
                self.subTest(payload_size=len(payload)),
                mock.patch.object(
                    official_docs,
                    "build_opener",
                    return_value=opener,
                ),
                self.assertRaises(official_docs.AuditError),
            ):
                official_docs.read_document(
                    "https://docs.example.test/guide", ("docs.example.test",)
                )
        opener = FakeOpener()
        opener.error = OSError("offline")
        with (
            mock.patch.object(official_docs, "build_opener", return_value=opener),
            self.assertRaisesRegex(official_docs.AuditError, "request failed"),
        ):
            official_docs.read_document(
                "https://docs.example.test/guide", ("docs.example.test",)
            )

    def test_redirect_handler_rejects_unapproved_destination_before_fetching(
        self,
    ) -> None:
        handler = official_docs.ApprovedRedirectHandler(("docs.example.test",))
        request = official_docs.Request("https://docs.example.test/guide")
        with self.assertRaisesRegex(official_docs.AuditError, "leaves approved"):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://unapproved.example.test/guide",
            )
        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://docs.example.test/updated-guide",
        )
        self.assertIsNotNone(redirected)

    def test_claim_findings_cover_current_review_due_marker_and_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            claim = official_docs.load_trackers(root)[0]
            with mock.patch.object(
                official_docs,
                "read_document",
                return_value=("https://docs.example.test/guide", "Supported contract"),
            ):
                self.assertEqual(
                    official_docs.claim_findings(root, claim, date(2026, 8, 25)), []
                )
                due = official_docs.claim_findings(root, claim, date(2026, 11, 22))
            self.assertEqual(due[0]["kind"], "official-docs-review")
            with mock.patch.object(
                official_docs,
                "read_document",
                return_value=("https://docs.example.test/guide", "different text"),
            ):
                missing = official_docs.claim_findings(root, claim, date(2026, 8, 25))
            self.assertEqual(missing[0]["kind"], "official-docs-marker")
            with (
                mock.patch.object(
                    official_docs,
                    "read_document",
                    return_value=(
                        "https://unapproved.example.test/guide",
                        "Supported contract",
                    ),
                ),
                self.assertRaisesRegex(official_docs.AuditError, "approved hosts"),
            ):
                official_docs.claim_findings(root, claim, date(2026, 8, 25))
            (root / "README.md").unlink()
            with self.assertRaisesRegex(official_docs.AuditError, "source path"):
                official_docs.claim_findings(root, claim, date(2026, 8, 25))

    def test_claim_findings_rejects_unsafe_path_and_future_review_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root, reviewed_on="2026-08-26")
            claim = official_docs.load_trackers(root)[0]
            with (
                mock.patch.object(
                    official_docs,
                    "read_document",
                    return_value=(
                        "https://docs.example.test/guide",
                        "Supported contract",
                    ),
                ),
                self.assertRaisesRegex(official_docs.AuditError, "future"),
            ):
                official_docs.claim_findings(root, claim, date(2026, 8, 25))

            with (
                mock.patch.object(Path, "is_symlink", autospec=True, return_value=True),
                self.assertRaisesRegex(official_docs.AuditError, "unsafe"),
            ):
                official_docs.claim_findings(root, claim, date(2026, 8, 27))
            with (
                mock.patch.object(
                    official_docs, "path_has_link_or_reparse", return_value=True
                ),
                self.assertRaisesRegex(official_docs.AuditError, "unsafe"),
            ):
                official_docs.claim_findings(root, claim, date(2026, 8, 27))

    def test_audit_report_and_main_preserve_indeterminate_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root, reviewed_on="2026-05-01")
            with mock.patch.object(
                official_docs,
                "read_document",
                return_value=("https://docs.example.test/guide", "Supported contract"),
            ):
                report = official_docs.audit(root, today=date(2026, 8, 25))
            self.assertEqual(report["status"], "attention")
            self.assertIn("official-docs-review", official_docs.markdown_report(report))
            report["findings"] = [
                {
                    "kind": "official|docs\nnext",
                    "path": "README|guide.md\nnext",
                    "subject": "Claim|text\nnext",
                    "current": "old|value\nnext",
                    "latest": "new|value\nnext",
                    "details": "outdated",
                }
            ]
            self.assertIn(
                "| official\\|docs next | `README\\|guide.md next` | "
                "Claim\\|text next | `old\\|value next` | `new\\|value next` |",
                official_docs.markdown_report(report),
            )
            with mock.patch.object(
                official_docs,
                "read_document",
                side_effect=official_docs.AuditError("offline"),
            ):
                report = official_docs.audit(root, today=date(2026, 8, 25))
            self.assertEqual(report["status"], "indeterminate")
            self.assertIn("Indeterminate", official_docs.markdown_report(report))

            output_json = root / "report.json"
            output_markdown = root / "report.md"
            stdout = StringIO()
            with (
                mock.patch.object(official_docs, "audit", return_value=report),
                redirect_stdout(stdout),
            ):
                self.assertEqual(
                    official_docs.main(
                        [
                            "--repository-root",
                            str(root),
                            "--json-output",
                            str(output_json),
                            "--markdown-output",
                            str(output_markdown),
                        ]
                    ),
                    2,
                )
            self.assertIn("indeterminate", stdout.getvalue())
            self.assertEqual(
                json.loads(output_json.read_text(encoding="utf-8"))["status"],
                "indeterminate",
            )
            self.assertIn(
                "official-docs-audit", output_markdown.read_text(encoding="utf-8")
            )

            stdout = StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(
                    official_docs.main(
                        [
                            "--repository-root",
                            str(root),
                            "--validate-registry",
                        ]
                    ),
                    0,
                )
            self.assertIn("tracker registry is valid", stdout.getvalue())
            with self.assertRaises(SystemExit):
                official_docs.parse_args(["--repository-root", str(root)])
            malformed_arguments = official_docs.argparse.Namespace(
                repository_root=root,
                tracker_registry=official_docs.DEFAULT_TRACKER_REGISTRY,
                validate_registry=False,
                json_output=None,
                markdown_output=None,
            )
            with (
                mock.patch.object(
                    official_docs, "parse_args", return_value=malformed_arguments
                ),
                self.assertRaisesRegex(AssertionError, "argument parser"),
            ):
                official_docs.main([])

            (root / ".github/official-docs-trackers.json").write_text(
                '{"schema-version": 2, "claims": []}', encoding="utf-8"
            )
            report = official_docs.audit(root, today=date(2026, 8, 25))
            self.assertEqual(report["status"], "indeterminate")

        with (
            mock.patch.object(official_docs, "main", return_value=0),
            self.assertRaises(SystemExit),
        ):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")


if __name__ == "__main__":
    unittest.main()
