from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "audit_freshness.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("scripts.audit_freshness", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load audit_freshness.py")
freshness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = freshness
SPEC.loader.exec_module(freshness)


class FakeResponse(BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def release(tag: str, sha: str) -> Any:
    return freshness.sync_action_pins.ActionRelease(tag, sha)


class FreshnessTests(unittest.TestCase):
    def write_repository(self, root: Path) -> None:
        for relative, content in {
            ".github/freshness-trackers.json": json.dumps(
                {
                    "schema-version": 1,
                    "workflow-directories": [
                        ".github/workflows",
                        "skills/repo-scaffold/assets/workflows",
                    ],
                    "release-please-configs": [
                        "release-please-config.json",
                        "skills/repo-scaffold/assets/release-please-config.json",
                        "skills/repo-scaffold/assets/release-please-config.vi.json",
                    ],
                    "requirement-sources": [
                        {
                            "path": "requirements-dev.in",
                            "locks": [
                                "requirements-dev.txt",
                                "requirements-mutation.txt",
                            ],
                        },
                        {
                            "path": "requirements-mutation.in",
                            "locks": ["requirements-mutation.txt"],
                        },
                        {
                            "path": "skills/repo-scaffold/assets/requirements-docs.txt",
                            "locks": [],
                        },
                    ],
                }
            ),
            ".github/workflows/ci.yml": (
                "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@"
                + "a" * 40
                + " # v1.0.0\n"
            ),
            "skills/repo-scaffold/assets/workflows/ci.yml": (
                "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@"
                + "a" * 40
                + " # v1.0.0\n"
            ),
            "release-please-config.json": json.dumps(
                {
                    "$schema": "https://raw.githubusercontent.com/googleapis/release-please/v17.6.0/schemas/config.json"
                }
            ),
            "skills/repo-scaffold/assets/release-please-config.json": json.dumps(
                {
                    "$schema": "https://raw.githubusercontent.com/googleapis/release-please/v17.6.0/schemas/config.json"
                }
            ),
            "skills/repo-scaffold/assets/release-please-config.vi.json": json.dumps(
                {
                    "$schema": "https://raw.githubusercontent.com/googleapis/release-please/v17.6.0/schemas/config.json"
                }
            ),
            "requirements-dev.in": "ruff==0.1.0\n",
            "requirements-mutation.in": "-r requirements-dev.in\nmutmut==1.0.0\n",
            "requirements-dev.txt": "ruff==0.1.0 \\\n    --hash=sha256:"
            + "a" * 64
            + "\n",
            "requirements-mutation.txt": "ruff==0.1.0 \\\n    --hash=sha256:"
            + "a" * 64
            + "\nmutmut==1.0.0\n",
            "skills/repo-scaffold/assets/requirements-docs.txt": "markdown-it-py==1.0.0\n",
        }.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_normalized_name_and_pypi_response_validation(self) -> None:
        self.assertEqual(freshness.normalized_name("Types_PyYAML"), "types-pyyaml")
        with self.assertRaisesRegex(freshness.AuditError, "unsafe"):
            freshness.latest_pypi_release("../unsafe")
        with mock.patch.object(
            freshness,
            "read_json",
            return_value={"info": {"version": "2.0.0"}},
        ):
            self.assertEqual(freshness.latest_pypi_release("example-package"), "2.0.0")
        with mock.patch.object(freshness, "read_json", return_value={"info": {}}):
            with self.assertRaisesRegex(freshness.AuditError, "no current version"):
                freshness.latest_pypi_release("example-package")

    def test_read_json_validates_network_size_shape_and_encoding(self) -> None:
        with mock.patch.object(
            freshness,
            "urlopen",
            return_value=FakeResponse(b'{"ok": true}'),
        ) as open_url:
            self.assertEqual(
                freshness.read_json("https://example.test/data"), {"ok": True}
            )
        self.assertEqual(open_url.call_args.kwargs["timeout"], 30)
        for payload in (b"[1]", b"{", b"x" * (freshness.MAX_RESPONSE_BYTES + 1)):
            with self.subTest(payload_size=len(payload)):
                with mock.patch.object(
                    freshness, "urlopen", return_value=FakeResponse(payload)
                ):
                    with self.assertRaises(freshness.AuditError):
                        freshness.read_json("https://example.test/data")
        with mock.patch.object(freshness, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(freshness.AuditError, "request failed"):
                freshness.read_json("https://example.test/data")

    def test_pinned_requirements_rejects_invalid_empty_and_conflicting_pins(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.in"
            path.write_text("ruff==1.0.0\n", encoding="utf-8")
            self.assertEqual(
                freshness.pinned_requirements(path), {"ruff": ("ruff", "1.0.0")}
            )
            for content, message in (
                ("", "no direct pins"),
                ("ruff>=1.0.0\n", "unsupported"),
                ("ruff==1.0.0\nruff==2.0.0\n", "conflicting"),
            ):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(freshness.AuditError, message):
                        freshness.pinned_requirements(path)
            with self.assertRaisesRegex(freshness.AuditError, "could not read"):
                freshness.pinned_requirements(path.with_name("missing.in"))

    def test_action_findings_are_semantic_and_cache_upstream_releases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            calls: list[str] = []

            def lookup(repository: str) -> Any:
                calls.append(repository)
                return release("v2.0.0", "b" * 40)

            findings = freshness.action_findings(
                root, trackers.workflow_directories, lookup
            )
            self.assertEqual(calls, ["actions/checkout"])
            self.assertEqual(len(findings), 2)
            self.assertTrue(all(item["latest"] == "v2.0.0" for item in findings))

            self.assertEqual(
                freshness.action_findings(
                    root,
                    trackers.workflow_directories,
                    lambda _repository: release("v1.0.0", "a" * 40),
                ),
                [],
            )

    def test_release_please_and_requirement_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            trackers = freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            schema = freshness.release_please_findings(
                root, trackers.release_please_configs, "v17.11.1"
            )
            self.assertEqual(len(schema), 3)
            self.assertEqual(
                freshness.release_please_findings(
                    root, trackers.release_please_configs, "v17.6.0"
                ),
                [],
            )
            config = root / trackers.release_please_configs[0]
            config.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(freshness.AuditError, "could not read"):
                freshness.release_please_findings(
                    root, trackers.release_please_configs, "v17.6.0"
                )
            config.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(freshness.AuditError, "unsupported"):
                freshness.release_please_findings(
                    root, trackers.release_please_configs, "v17.6.0"
                )
            config.write_text(
                json.dumps(
                    {
                        "$schema": "https://raw.githubusercontent.com/googleapis/release-please/v17.6.0/schemas/config.json"
                    }
                ),
                encoding="utf-8",
            )
            versions = {"ruff": "0.2.0", "mutmut": "1.0.0", "markdown-it-py": "1.0.0"}
            findings = freshness.requirement_findings(
                root, trackers.requirement_sources, versions.__getitem__
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["subject"], "ruff")

            (root / "requirements-dev.txt").write_text(
                "other==1.0.0\n", encoding="utf-8"
            )
            inconsistent = freshness.requirement_findings(
                root, trackers.requirement_sources, versions.__getitem__
            )
            self.assertEqual(inconsistent[-1]["kind"], "lock-consistency")

    def test_tracker_registry_rejects_invalid_and_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            registry = root / freshness.DEFAULT_TRACKER_REGISTRY
            valid = json.loads(registry.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(freshness.AuditError, "non-empty"):
                freshness.safe_relative_path("", field="test")
            with self.assertRaisesRegex(freshness.AuditError, "missing or unsafe"):
                freshness.tracked_path(root, Path("missing"), kind="test path")
            with mock.patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaisesRegex(freshness.AuditError, "missing or unsafe"):
                    freshness.tracked_path(
                        root, freshness.DEFAULT_TRACKER_REGISTRY, kind="test path"
                    )
            registry.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(freshness.AuditError, "could not read"):
                freshness.load_trackers(root, freshness.DEFAULT_TRACKER_REGISTRY)
            for document, message in (
                ({"schema-version": 2}, "schema-version"),
                (
                    {
                        "schema-version": 1,
                        "workflow-directories": ["../outside"],
                        "release-please-configs": [],
                        "requirement-sources": [],
                    },
                    "safe relative",
                ),
                (
                    {
                        "schema-version": 1,
                        "workflow-directories": [".github/workflows"],
                        "release-please-configs": [],
                        "requirement-sources": [{"path": "requirements.in"}],
                    },
                    "locks",
                ),
                (
                    {
                        **valid,
                        "workflow-directories": "not-a-list",
                    },
                    "workflow-directories must be a list",
                ),
                (
                    {
                        **valid,
                        "workflow-directories": [
                            ".github/workflows",
                            ".github/workflows",
                        ],
                    },
                    "must not repeat",
                ),
                ({**valid, "requirement-sources": "not-a-list"}, "sources"),
                ({**valid, "requirement-sources": ["not-an-object"]}, "object"),
                (
                    {
                        **valid,
                        "requirement-sources": [
                            {"path": "requirements.in", "locks": "not-a-list"}
                        ],
                    },
                    "locks",
                ),
                (
                    {
                        **valid,
                        "requirement-sources": [
                            {
                                "path": "requirements.in",
                                "locks": ["requirements.txt", "requirements.txt"],
                            }
                        ],
                    },
                    "unique",
                ),
            ):
                with self.subTest(document=document):
                    registry.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(freshness.AuditError, message):
                        freshness.load_trackers(
                            root, freshness.DEFAULT_TRACKER_REGISTRY
                        )

    def test_action_and_audit_registry_error_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            with self.assertRaisesRegex(freshness.AuditError, "workflow directory"):
                freshness.action_findings(
                    root,
                    (Path("requirements-dev.in"),),
                    lambda _repository: release("v1.0.0", "a" * 40),
                )
            (root / "empty-workflows").mkdir()
            with self.assertRaisesRegex(freshness.AuditError, "no workflow files"):
                freshness.action_findings(
                    root,
                    (Path("empty-workflows"),),
                    lambda _repository: release("v1.0.0", "a" * 40),
                )

            registry = root / freshness.DEFAULT_TRACKER_REGISTRY
            registry.write_text("{}", encoding="utf-8")
            report = freshness.audit(root, "")
            self.assertEqual(report["status"], "indeterminate")
            self.assertEqual(len(report["errors"]), 1)

            self.write_repository(root)
            document = json.loads(registry.read_text(encoding="utf-8"))
            document["release-please-configs"] = []
            registry.write_text(json.dumps(document), encoding="utf-8")
            client = mock.Mock()
            client.latest_release.return_value = release("v1.0.0", "a" * 40)
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(
                    freshness,
                    "latest_pypi_release",
                    side_effect={
                        "ruff": "0.1.0",
                        "mutmut": "1.0.0",
                        "markdown-it-py": "1.0.0",
                    }.__getitem__,
                ),
            ):
                report = freshness.audit(root, "token")
            self.assertEqual(report["status"], "current")
            self.assertNotIn(
                "googleapis/release-please", client.latest_release.call_args
            )

    def test_audit_markdown_and_main_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            client = mock.Mock()
            client.latest_release.side_effect = lambda repository: {
                "actions/checkout": release("v1.0.0", "a" * 40),
                "googleapis/release-please": release("v17.6.0", "c" * 40),
            }[repository]
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    return_value=client,
                ),
                mock.patch.object(
                    freshness,
                    "latest_pypi_release",
                    side_effect={
                        "ruff": "0.1.0",
                        "mutmut": "1.0.0",
                        "markdown-it-py": "1.0.0",
                    }.__getitem__,
                ),
            ):
                report = freshness.audit(root, "token")
            self.assertEqual(report["status"], "current")
            self.assertIn("No stale", freshness.markdown_report(report))

            report["findings"] = [
                {
                    "kind": "python-package",
                    "path": "requirements-dev.in",
                    "subject": "ruff",
                    "current": "0.1.0",
                    "latest": "0.2.0",
                    "details": "outdated",
                }
            ]
            self.assertIn("| Check |", freshness.markdown_report(report))
            report["errors"] = ["offline"]
            report["status"] = "indeterminate"
            self.assertIn("## Indeterminate", freshness.markdown_report(report))

            json_output = root / "report.json"
            markdown_output = root / "report.md"
            stdout = StringIO()
            with (
                mock.patch.object(freshness, "audit", return_value=report),
                redirect_stdout(stdout),
            ):
                self.assertEqual(
                    freshness.main(
                        [
                            "--repository-root",
                            str(root),
                            "--json-output",
                            str(json_output),
                            "--markdown-output",
                            str(markdown_output),
                        ]
                    ),
                    2,
                )
            self.assertIn("indeterminate", stdout.getvalue())
            self.assertEqual(
                json.loads(json_output.read_text(encoding="utf-8"))["status"],
                "indeterminate",
            )
            self.assertIn(
                "freshness-audit", markdown_output.read_text(encoding="utf-8")
            )

    def test_audit_records_independent_upstream_errors_and_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository(root)
            with (
                mock.patch.object(
                    freshness.sync_action_pins,
                    "GitHubReleaseClient",
                    side_effect=ValueError("missing token"),
                ),
                mock.patch.object(
                    freshness,
                    "latest_pypi_release",
                    side_effect=freshness.AuditError("PyPI offline"),
                ),
            ):
                report = freshness.audit(root, "")
            self.assertEqual(report["status"], "indeterminate")
            self.assertEqual(len(report["errors"]), 2)

        with (
            mock.patch.object(freshness, "main", return_value=0),
            self.assertRaises(SystemExit),
        ):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    def test_freshness_workflow_is_scheduled_and_non_required(self) -> None:
        workflows = (
            PLUGIN_ROOT / ".github" / "workflows" / "freshness.yml",
            PLUGIN_ROOT
            / "skills"
            / "repo-scaffold"
            / "assets"
            / "workflows"
            / "freshness.yml",
        )
        for path in workflows:
            workflow = path.read_text(encoding="utf-8")
            for fragment in (
                "schedule:",
                "workflow_dispatch:",
                "contents: read",
                "issues: write",
                "cancel-in-progress: false",
                "python scripts/audit_freshness.py",
                "repo-scaffold-freshness-audit",
                "--body-file",
            ):
                with self.subTest(path=path, fragment=fragment):
                    self.assertIn(fragment, workflow)
            self.assertNotIn("pull_request:", workflow)


if __name__ == "__main__":
    unittest.main()
